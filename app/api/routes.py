import uuid
import json
import queue
import threading

import structlog
from redis import Redis
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from starlette.concurrency import run_in_threadpool

from app.core.errors import InvalidUpload
from app.core.schemas import (
    ChatRequest, ChatResponse, IngestionStatus, MemoryForgetResult, MemoryItem, ThreadCreate,
    ThreadDetail, ThreadMessage, ThreadSummary,
)
from app.db.models import (
    ConversationMessage, ConversationThread, DocumentRecord, EvidenceRecord, IngestionJob,
    LongTermMemory,
)
from app.ingestion.extractor import validate_upload
from app.ingestion.service import register_document
from app.query.memory import ConversationMemory, LOCAL_OWNER
from app.worker import celery_app, ingest_document, publish_ingestion_progress

router = APIRouter(prefix="/api/v1")
logger = structlog.get_logger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/threads", response_model=ThreadSummary, status_code=201)
def create_thread(request: Request, body: ThreadCreate) -> ThreadSummary:
    session = request.app.state.session_factory()
    try:
        thread = ConversationThread(id=str(uuid.uuid4()), owner_id=LOCAL_OWNER,
                                    title=(body.title or "New conversation")[:200])
        session.add(thread)
        session.commit()
        session.refresh(thread)
        return ThreadSummary(id=thread.id, title=thread.title, created_at=thread.created_at,
                             updated_at=thread.updated_at)
    finally:
        session.close()


@router.get("/threads", response_model=list[ThreadSummary])
def list_threads(request: Request, limit: int = 30) -> list[ThreadSummary]:
    session = request.app.state.session_factory()
    try:
        threads = session.scalars(
            select(ConversationThread).where(ConversationThread.owner_id == LOCAL_OWNER)
            .order_by(ConversationThread.updated_at.desc()).limit(max(1, min(limit, 100)))
        ).all()
        return [ThreadSummary(id=item.id, title=item.title, created_at=item.created_at,
                              updated_at=item.updated_at) for item in threads]
    finally:
        session.close()


@router.get("/threads/{thread_id}", response_model=ThreadDetail)
def get_thread(request: Request, thread_id: str) -> ThreadDetail:
    session = request.app.state.session_factory()
    try:
        thread = session.get(ConversationThread, thread_id)
        if not thread or thread.owner_id != LOCAL_OWNER:
            raise HTTPException(status_code=404, detail="Conversation thread not found")
        messages = session.scalars(
            select(ConversationMessage).where(ConversationMessage.thread_id == thread.id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        ).all()
        return ThreadDetail(
            id=thread.id, title=thread.title, created_at=thread.created_at, updated_at=thread.updated_at,
            messages=[ThreadMessage(id=item.id, role=item.role, content=item.content,
                                    citations=item.citations or [], created_at=item.created_at) for item in messages],
        )
    finally:
        session.close()


@router.get("/memories", response_model=list[MemoryItem])
def list_memories(request: Request) -> list[MemoryItem]:
    session = request.app.state.session_factory()
    try:
        rows = session.scalars(
            select(LongTermMemory).where(LongTermMemory.owner_id == LOCAL_OWNER)
            .order_by(LongTermMemory.created_at.desc()).limit(500)
        ).all()
        return [MemoryItem(id=row.id, memory_type=row.memory_type, content=row.content,
                           created_at=row.created_at) for row in rows]
    finally:
        session.close()


@router.delete("/memories/{memory_id}", status_code=204)
def forget_memory(request: Request, memory_id: str) -> Response:
    session = request.app.state.session_factory()
    try:
        memory = session.scalar(select(LongTermMemory).where(
            LongTermMemory.id == memory_id, LongTermMemory.owner_id == LOCAL_OWNER,
        ))
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")
        if memory.vector_synced:
            try:
                request.app.state.services.vector_index.delete_memory(memory.id)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Could not remove memory from vector search") from exc
        session.delete(memory)
        session.commit()
        return Response(status_code=204)
    finally:
        session.close()


@router.delete("/memory", response_model=MemoryForgetResult)
def forget_all_memory(request: Request) -> MemoryForgetResult:
    session = request.app.state.session_factory()
    try:
        # Remove vectors first; if Qdrant is unavailable, keep the database intact for retry.
        try:
            request.app.state.services.vector_index.delete_memories(LOCAL_OWNER)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Could not remove memories from vector search") from exc
        memory_count = session.scalar(select(func.count()).select_from(LongTermMemory).where(
            LongTermMemory.owner_id == LOCAL_OWNER,
        )) or 0
        owned_thread_ids = select(ConversationThread.id).where(ConversationThread.owner_id == LOCAL_OWNER)
        thread_count = session.scalar(select(func.count()).select_from(ConversationThread).where(
            ConversationThread.owner_id == LOCAL_OWNER,
        )) or 0
        session.execute(delete(LongTermMemory).where(LongTermMemory.owner_id == LOCAL_OWNER))
        session.execute(delete(ConversationMessage).where(ConversationMessage.thread_id.in_(owned_thread_ids)))
        session.execute(delete(ConversationThread).where(ConversationThread.owner_id == LOCAL_OWNER))
        session.commit()
        return MemoryForgetResult(deleted_memories=memory_count, deleted_threads=thread_count)
    finally:
        session.close()


@router.post("/documents", response_model=IngestionStatus, status_code=202)
async def upload_document(request: Request, file: UploadFile = File(...)) -> IngestionStatus:
    settings = request.app.state.settings
    data = await file.read(settings.max_upload_bytes + 1)
    return await run_in_threadpool(
        _register_upload, request, file.filename or "upload",
        file.content_type or "application/octet-stream", data,
    )


def _register_upload(request: Request, filename: str, content_type: str, data: bytes) -> IngestionStatus:
    settings = request.app.state.settings
    session = request.app.state.session_factory()
    try:
        document, job = register_document(
            session, request.app.state.services.storage, filename,
            content_type, data, settings.max_upload_bytes, settings.max_image_bytes,
        )
        if job.status in {"queued", "failed"}:
            job.status, job.error = "queued", None
            document.status = "queued"
            session.commit()
            try:
                ingest_document.delay(document.id)
            except Exception as exc:
                job.status = document.status = "failed"
                job.error = "Unable to enqueue ingestion"
                session.commit()
                raise HTTPException(status_code=503, detail=job.error) from exc
        return IngestionStatus(job_id=job.id, document_id=document.id, status=job.status, error=job.error)
    except InvalidUpload as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="Unable to queue ingestion; retry the upload") from exc
    finally:
        session.close()


@router.get("/ingestions/{job_id}", response_model=IngestionStatus)
def ingestion_status(request: Request, job_id: str) -> IngestionStatus:
    session = request.app.state.session_factory()
    try:
        job = session.get(IngestionJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Ingestion job not found")
        return _ingestion_status(job)
    finally:
        session.close()


def _ingestion_status(job: IngestionJob) -> IngestionStatus:
    return IngestionStatus(
        job_id=job.id, document_id=job.document_id, status=job.status, error=job.error,
        progress_stage=job.progress_stage, progress_current=job.progress_current or 0,
        progress_total=job.progress_total or 0, progress_message=job.progress_message,
        progress_percent=job.progress_percent or 0, progress_details=job.progress_details or {},
    )


@router.get("/ingestions/{job_id}/events")
def ingestion_events(request: Request, job_id: str):
    session = request.app.state.session_factory()
    try:
        job = session.get(IngestionJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Ingestion job not found")
    finally:
        session.close()

    # A worker can disappear after claiming a task (for example, a container
    # restart). In that case the database remains "processing" forever. Check
    # Celery's active/reserved/scheduled lists before opening the stream and
    # requeue only jobs whose recorded task is no longer known to any worker.
    _recover_orphaned_ingestion(request, job_id)

    def event_stream():
        client = Redis.from_url(request.app.state.settings.celery_broker_url)
        pubsub = client.pubsub()
        pubsub.subscribe(f"ingestion-progress:{job_id}")
        try:
            session = request.app.state.session_factory()
            try:
                current = session.get(IngestionJob, job_id)
                if current is None:
                    return
                payload = _ingestion_status(current).model_dump(mode="json")
            finally:
                session.close()
            last_payload = payload
            yield f"retry: 1500\nevent: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if payload["status"] in {"completed", "failed"}:
                return
            while True:
                message = pubsub.get_message(timeout=15.0)
                if message and message.get("type") == "message":
                    payload = json.loads(message["data"])
                    if payload != last_payload:
                        yield f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        last_payload = payload
                    if payload.get("status") in {"completed", "failed"}:
                        return
                else:
                    yield ": keep-alive\n\n"
        finally:
            pubsub.close()
            client.close()

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _recover_orphaned_ingestion(request: Request, job_id: str) -> None:
    session = request.app.state.session_factory()
    try:
        job = session.get(IngestionJob, job_id)
        if not job or job.status != "processing" or not job.worker_task_id:
            return
        task_id = job.worker_task_id
        inspect = celery_app.control.inspect(timeout=1.0)
        known_tasks = set()
        for task_group in (inspect.active(), inspect.reserved(), inspect.scheduled()):
            for entries in (task_group or {}).values():
                for entry in entries:
                    request_data = entry.get("request", entry)
                    if request_data.get("id"):
                        known_tasks.add(request_data["id"])
        if task_id in known_tasks:
            return

        document = session.get(DocumentRecord, job.document_id)
        if not document:
            return
        job.status = document.status = "queued"
        job.error = None
        job.worker_task_id = None
        job.progress_stage = "queued"
        job.progress_current = job.progress_total = job.progress_percent = 0
        job.progress_message = "Worker task was lost; putting this document back in the queue"
        session.commit()
        queued_payload = _ingestion_status(job).model_dump(mode="json")
        publish_ingestion_progress(job.id, queued_payload)
        result = ingest_document.delay(document.id)
        # Do not overwrite task metadata if a fast worker has already claimed it.
        session.refresh(job)
        if job.status == "queued":
            job.worker_task_id = result.id
            session.commit()
        logger.warning("ingestion.orphan_requeued", job_id=job.id, previous_task_id=task_id,
                       replacement_task_id=result.id)
    except Exception:
        session.rollback()
        logger.exception("ingestion.orphan_recovery_failed", job_id=job_id)
    finally:
        session.close()


@router.get("/evidence/{evidence_id}/image")
def evidence_image(request: Request, evidence_id: str) -> Response:
    session = request.app.state.session_factory()
    try:
        evidence = session.scalar(
            select(EvidenceRecord)
            .join(DocumentRecord, EvidenceRecord.document_id == DocumentRecord.id)
            .where(
                EvidenceRecord.id == evidence_id,
                EvidenceRecord.kind == "image",
                DocumentRecord.status == "completed",
            )
        )
        if not evidence or not evidence.object_key:
            raise HTTPException(status_code=404, detail="Image evidence not found")
        image = request.app.state.services.storage.get(evidence.object_key)
        mime_type = evidence.mime_type or "image/png"
        extension = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type, "png")
        filename = f"page-{evidence.page_number}-{evidence.id[:8]}.{extension}"
        return Response(
            content=image,
            media_type=mime_type,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    finally:
        session.close()


@router.post("/chat", response_model=ChatResponse)
def chat(request: Request, body: ChatRequest) -> ChatResponse:
    question = body.question
    if not question.strip():
        raise HTTPException(status_code=422, detail="question must be a non-empty string")
    return _query(request, question, [], body.thread_id)


@router.post("/chat/stream")
def chat_stream(request: Request, body: ChatRequest):
    if not body.question.strip():
        raise HTTPException(status_code=422, detail="question must be a non-empty string")
    return StreamingResponse(
        _stream_query_events(request, body.question, [], body.thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/multimodal", response_model=ChatResponse)
async def multimodal_chat(
    request: Request,
    question: str = Form(..., max_length=20_000),
    thread_id: str | None = Form(default=None, max_length=36),
    images: list[UploadFile] = File(default=[]),
) -> ChatResponse:
    settings = request.app.state.settings
    if not question.strip():
        raise HTTPException(status_code=422, detail="question must be a non-empty string")
    if len(images) > settings.max_user_images:
        raise HTTPException(status_code=400, detail=f"At most {settings.max_user_images} user images can be attached for the selected provider")
    prepared: list[tuple[str, str, bytes]] = []
    for image in images:
        data = await image.read(settings.max_upload_bytes + 1)
        try:
            mime = validate_upload(
                image.filename or "image", image.content_type or "", data,
                settings.max_upload_bytes, settings.max_image_bytes,
            )
        except InvalidUpload as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not mime.startswith("image/"):
            raise HTTPException(status_code=400, detail="Multimodal chat attachments must be images")
        prepared.append((image.filename or f"image-{uuid.uuid4()}", mime, data))
    return await run_in_threadpool(_query, request, question, prepared, thread_id)


@router.post("/chat/multimodal/stream")
async def multimodal_chat_stream(
    request: Request,
    question: str = Form(..., max_length=20_000),
    thread_id: str | None = Form(default=None, max_length=36),
    images: list[UploadFile] = File(default=[]),
):
    settings = request.app.state.settings
    if not question.strip():
        raise HTTPException(status_code=422, detail="question must be a non-empty string")
    if len(images) > settings.max_user_images:
        raise HTTPException(status_code=400, detail=f"At most {settings.max_user_images} user images can be attached for the selected provider")
    prepared: list[tuple[str, str, bytes]] = []
    for image in images:
        data = await image.read(settings.max_upload_bytes + 1)
        try:
            mime = validate_upload(
                image.filename or "image", image.content_type or "", data,
                settings.max_upload_bytes, settings.max_image_bytes,
            )
        except InvalidUpload as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not mime.startswith("image/"):
            raise HTTPException(status_code=400, detail="Multimodal chat attachments must be images")
        prepared.append((image.filename or f"image-{uuid.uuid4()}", mime, data))
    return StreamingResponse(
        _stream_query_events(request, question, prepared, thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream_query_events(request: Request, question: str, images: list[tuple[str, str, bytes]], thread_id: str | None):
    events: queue.Queue = queue.Queue()

    def produce() -> None:
        session = request.app.state.session_factory()
        try:
            events.put(("stage", {"stage": "context", "message": "Loading conversation context"}))
            memory_service = ConversationMemory(session, request.app.state.services)
            try:
                memory_state = memory_service.begin_turn(thread_id, question)
            except ValueError as exc:
                events.put(("error", {"message": str(exc)}))
                return
            events.put(("stage", {"stage": "routing", "message": "Choosing the right assistant"}))
            answer = request.app.state.chat_graph.invoke(
                session, question, images, memory_state,
                emit=lambda name, payload: events.put((name, payload)),
            )
            answer.thread_id = memory_state["thread_id"]
            events.put(("result", answer.model_dump(mode="json")))
            events.put(("stage", {"stage": "saving", "message": "Saving this answer and updating memory"}))
            memory_service.finish_turn(
                memory_state, answer.answer, [item.model_dump() for item in answer.citations]
            )
            events.put(("done", {"thread_id": memory_state["thread_id"]}))
        except Exception as exc:
            logger.exception("chat.stream_failed")
            events.put(("error", {"message": str(exc)[:500] or "The request failed."}))
        finally:
            session.close()
            events.put((None, None))

    threading.Thread(target=produce, daemon=True).start()
    while True:
        name, payload = events.get()
        if name is None:
            return
        yield f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _query(request: Request, question: str, images: list[tuple[str, str, bytes]], thread_id: str | None = None) -> ChatResponse:
    session = request.app.state.session_factory()
    try:
        memory_service = ConversationMemory(session, request.app.state.services)
        try:
            memory_state = memory_service.begin_turn(thread_id, question)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        answer = request.app.state.chat_graph.invoke(session, question, images, memory_state)
        memory_service.finish_turn(memory_state, answer.answer, [item.model_dump() for item in answer.citations])
        answer.thread_id = memory_state["thread_id"]
        return answer
    finally:
        session.close()
