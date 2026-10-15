import json

from celery import Celery
from redis import Redis
import structlog

from app.core.config import get_settings

settings = get_settings()
progress_redis = Redis.from_url(settings.celery_broker_url)
logger = structlog.get_logger(__name__)


def publish_ingestion_progress(job_id: str, payload: dict) -> None:
    try:
        progress_redis.publish(f"ingestion-progress:{job_id}", json.dumps(payload))
    except Exception:
        logger.exception("ingestion.progress_publish_failed", job_id=job_id)
celery_app = Celery("multimodal_rag", broker=settings.celery_broker_url)
celery_app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True,
                       worker_prefetch_multiplier=1, task_track_started=True)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def ingest_document(self, document_id: str) -> None:
    from app.core.container import build_services
    from app.db.session import session_scope
    from app.ingestion.service import IngestionService

    session = session_scope()
    try:
        logger.info("ingestion.started", document_id=document_id)
        IngestionService(
            build_services(settings), settings.max_pdf_pages, settings.pdf_render_dpi,
            progress_publisher=publish_ingestion_progress,
        ).ingest(session, document_id, self.request.id)
        logger.info("ingestion.completed", document_id=document_id)
    except Exception as exc:
        session.rollback()
        logger.exception("ingestion.failed", document_id=document_id, retry=self.request.retries)
        from sqlalchemy import select

        from app.db.models import DocumentRecord, IngestionJob

        job = session.scalar(select(IngestionJob).where(IngestionJob.document_id == document_id))
        document = session.get(DocumentRecord, document_id)
        if job:
            job.worker_task_id = None
            if self.request.retries >= self.max_retries:
                job.status = "failed"
                job.error = str(exc)[:2000]
                job.progress_stage = "failed"
                job.progress_message = "Ingestion failed"
            else:
                job.status = "queued"
                job.error = "Ingestion will be retried"
                job.progress_stage = "retrying"
                job.progress_message = f"Retrying after an ingestion error ({self.request.retries + 1}/{self.max_retries})"
            if document:
                document.status = job.status
            session.commit()
            publish_ingestion_progress(job.id, {
                "job_id": job.id, "document_id": document.id if document else document_id,
                "status": job.status, "error": job.error,
                "progress_stage": job.progress_stage,
                "progress_current": job.progress_current or 0,
                "progress_total": job.progress_total or 0,
                "progress_message": job.progress_message,
                "progress_percent": job.progress_percent or 0,
                "progress_details": job.progress_details or {},
            })
        raise
    finally:
        session.close()
