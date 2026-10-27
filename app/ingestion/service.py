import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.container import Services
from app.core.errors import InvalidUpload
from app.db.models import DocumentRecord, EvidenceRecord, IngestionJob
from app.ingestion.extractor import ExtractedEvidence, PdfExtractor, validate_upload


TEXT_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1800, chunk_overlap=180)


def chunk_text(text: str, max_chars: int = 1800, overlap: int = 180) -> list[str]:
    splitter = TEXT_SPLITTER
    if max_chars != 1800 or overlap != 180:
        splitter = RecursiveCharacterTextSplitter(chunk_size=max_chars, chunk_overlap=overlap)
    source = Document(page_content=text)
    return [chunk.page_content for chunk in splitter.split_documents([source]) if chunk.page_content.strip()]


def chunk_table(markdown: str, max_chars: int = 1800) -> list[str]:
    lines = [line for line in markdown.splitlines() if line.strip()]
    if len(markdown) <= max_chars or len(lines) <= 2:
        return [markdown]
    header = lines[:2]
    chunks: list[str] = []
    current = list(header)
    for row in lines[2:]:
        candidate = "\n".join([*current, row])
        if len(candidate) <= max_chars:
            current.append(row)
            continue
        if len(current) > len(header):
            chunks.append("\n".join(current))
            current = list(header)
        if len("\n".join([*current, row])) <= max_chars:
            current.append(row)
        else:
            available = max(100, max_chars - len("\n".join(header)) - 1)
            chunks.extend("\n".join([*header, part]) for part in chunk_text(row, available, 0))
            current = list(header)
    if len(current) > len(header):
        chunks.append("\n".join(current))
    return chunks or [markdown]


class IngestionService:
    def __init__(self, services: Services, max_pages: int, render_dpi: int, extractor=None, progress_publisher=None):
        self.services = services
        self.extractor = extractor or PdfExtractor(max_pages, render_dpi)
        self.progress_publisher = progress_publisher

    def ingest(self, session: Session, document_id: str, task_id: str | None = None) -> None:
        document = session.get(DocumentRecord, document_id)
        if not document:
            raise InvalidUpload("Document record does not exist")
        job = session.scalar(select(IngestionJob).where(IngestionJob.document_id == document_id))
        if not job:
            raise InvalidUpload("Ingestion job does not exist")
        task_id = task_id or str(uuid.uuid4())
        if job.status == "completed":
            return
        if job.status == "processing":
            if job.worker_task_id not in {None, task_id}:
                return
        else:
            claimed = session.execute(
                update(IngestionJob)
                .where(IngestionJob.id == job.id, IngestionJob.status == "queued")
                .values(status="processing", error=None, worker_task_id=task_id)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                session.rollback()
                return
            session.execute(
                update(DocumentRecord)
                .where(DocumentRecord.id == document_id)
                .values(status="processing")
                .execution_options(synchronize_session=False)
            )
            session.commit()
            session.refresh(job)
        self._progress(session, job, "extracting", 0, 1, "Reading PDF pages and extracting content")
        self.services.vector_index.delete_document(document_id)
        session.execute(delete(EvidenceRecord).where(EvidenceRecord.document_id == document_id))
        session.commit()
        raw = self.services.storage.get(document.object_key)
        if document.content_type == "application/pdf":
            if isinstance(self.extractor, PdfExtractor):
                extracted = self.extractor.extract(
                    raw, document.filename,
                    progress_callback=lambda current, total, message: self._progress(
                        session, job, "extracting", current, total, message,
                        {"pages": total},
                    ),
                )
            else:
                extracted = self.extractor.extract(raw, document.filename)
        else:
            extracted = [ExtractedEvidence("image", 1, "Standalone uploaded image", raw, document.content_type)]
        pending: list[dict] = []
        image_tasks = {}
        for item in extracted:
            records = self._to_records(item)
            for ordinal, (kind, content, image, mime) in enumerate(records):
                evidence_id = hashlib.sha256(
                    f"{document_id}:{item.page_number}:{item.kind}:{item.ordinal}:{ordinal}:{content}".encode()
                ).hexdigest()
                object_key = None
                if image is not None:
                    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime or "", "bin")
                    object_key = f"assets/{document_id}/{item.page_number}/{evidence_id}.{extension}"
                    self.services.storage.put(object_key, image, mime or "application/octet-stream")
                    image_tasks[evidence_id] = (image, mime or "image/png", item.page_number)
                payload = {
                    "evidence_id": evidence_id,
                    "document_id": document_id,
                    "owner_id": document.owner_id,
                    "filename": document.filename,
                    "page_number": item.page_number,
                    "kind": kind,
                    "content": content,
                    "object_key": object_key,
                    "mime_type": mime,
                    "ready": False,
                }
                pending.append({
                    "id": evidence_id, "kind": kind, "page": item.page_number,
                    "content": content, "object_key": object_key, "mime": mime, "payload": payload,
                })

        progress_details = {
            "pages": max((item.page_number for item in extracted), default=0),
            "extracted_elements": len(extracted),
            "text_chunks": sum(row["kind"] == "text" for row in pending),
            "table_chunks": sum(row["kind"] == "table" for row in pending),
            "image_records": sum(row["kind"] == "image" for row in pending),
            "total_records": len(pending),
        }
        total_images = len(image_tasks)
        pending_by_id = {row["id"]: row for row in pending}
        self._progress(session, job, "describing_images", 0, total_images,
                       f"Creating descriptions for {total_images} visual items", progress_details)
        if image_tasks:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(
                        self.services.provider.summarize_image, image, mime,
                        f"{document.filename}, page {page}",
                    ): evidence_id
                    for evidence_id, (image, mime, page) in image_tasks.items()
                }
                for completed, future in enumerate(as_completed(futures), start=1):
                    evidence_id = futures[future]
                    summary = future.result()
                    item = pending_by_id[evidence_id]
                    item["content"] = summary
                    item["payload"]["content"] = summary
                    self._progress(session, job, "describing_images", completed, total_images,
                                   f"Described visual {completed} of {total_images}", progress_details)

        total_records = len(pending)
        self._progress(session, job, "embedding", 0, total_records,
                       f"Embedding {total_records} text, table, and image records", progress_details)
        batch_size = 64
        for start in range(0, total_records, batch_size):
            batch = pending[start:start + batch_size]
            texts = [row["content"] for row in batch]
            embed_many = getattr(self.services.embedder, "embed_many", None)
            vectors = embed_many(texts) if embed_many else [self.services.embedder.embed(text) for text in texts]
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding provider returned an unexpected number of vectors")
            points = []
            for row, vector in zip(batch, vectors):
                session.add(EvidenceRecord(
                    id=row["id"], document_id=document_id, kind=row["kind"],
                    page_number=row["page"], filename=document.filename,
                    content=row["content"], object_key=row["object_key"], mime_type=row["mime"],
                ))
                points.append((row["id"], vector, row["payload"]))
            upsert_many = getattr(self.services.vector_index, "upsert_many", None)
            if upsert_many:
                upsert_many(points)
            else:
                for record_id, vector, payload in points:
                    self.services.vector_index.upsert(record_id, vector, payload)
            session.commit()
            indexed = min(start + len(batch), total_records)
            self._progress(session, job, "indexing", indexed, total_records,
                           f"Indexed {indexed} of {total_records} records", progress_details)
        document.status = job.status = "completed"
        job.worker_task_id = None
        job.progress_stage = "completed"
        job.progress_current = job.progress_total = total_records
        job.progress_message = f"Finished indexing {total_records} records"
        job.progress_percent = 100
        job.progress_details = progress_details
        self.services.vector_index.activate_document(document_id)
        session.commit()
        self._publish_progress(job)

    def _progress(
        self, session: Session, job: IngestionJob, stage: str, current: int, total: int,
        message: str, details: dict[str, int] | None = None,
    ) -> None:
        job.progress_stage = stage
        job.progress_current = current
        job.progress_total = total
        job.progress_message = message[:255]
        if details is not None:
            job.progress_details = details
        if stage == "extracting":
            job.progress_percent = 3 + round(17 * current / total) if total else 3
        elif stage == "describing_images":
            job.progress_percent = 20 + round(30 * current / total) if total else 50
        elif stage == "embedding":
            job.progress_percent = 50
        elif stage == "indexing":
            job.progress_percent = 50 + round(49 * current / total) if total else 99
        session.commit()
        self._publish_progress(job)

    def _publish_progress(self, job: IngestionJob) -> None:
        if self.progress_publisher:
            self.progress_publisher(job.id, {
                "job_id": job.id, "document_id": job.document_id, "status": job.status,
                "error": job.error, "progress_stage": job.progress_stage,
                "progress_current": job.progress_current or 0,
                "progress_total": job.progress_total or 0,
                "progress_message": job.progress_message,
                "progress_percent": job.progress_percent or 0,
                "progress_details": job.progress_details or {},
            })

    @staticmethod
    def _to_records(item: ExtractedEvidence) -> list[tuple[str, str, bytes | None, str | None]]:
        if item.kind == "text":
            return [("text", chunk, None, None) for chunk in chunk_text(item.content)]
        if item.kind == "table":
            return [("table", chunk, None, None) for chunk in chunk_table(item.content)]
        return [("image", item.content, item.image_bytes, item.mime_type)]


def register_document(
    session: Session,
    storage,
    filename: str,
    content_type: str,
    data: bytes,
    max_bytes: int,
    max_image_bytes: int,
    owner_id: str = "unassigned",
) -> tuple[DocumentRecord, IngestionJob]:
    mime = validate_upload(filename, content_type, data, max_bytes, max_image_bytes)
    checksum = hashlib.sha256(data).hexdigest()
    existing = session.scalar(select(DocumentRecord).where(
        DocumentRecord.checksum == checksum,
        DocumentRecord.owner_id == owner_id,
    ).order_by(DocumentRecord.owner_id == owner_id))
    if existing:
        job = session.scalar(select(IngestionJob).where(IngestionJob.document_id == existing.id))
        if job:
            return existing, job
    document_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    suffix = filename.rsplit(".", 1)[-1].lower()
    object_key = f"documents/{document_id}/source.{suffix}"
    storage.put(object_key, data, mime)
    document = DocumentRecord(
        id=document_id, filename=filename, content_type=mime, checksum=checksum, object_key=object_key,
        owner_id=owner_id,
    )
    job = IngestionJob(id=job_id, document_id=document_id)
    session.add_all([document, job])
    session.commit()
    return document, job
