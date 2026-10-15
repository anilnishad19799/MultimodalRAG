"""Rebuild the configured Qdrant collection from stored PostgreSQL evidence."""

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import DocumentRecord, EvidenceRecord
from app.db.session import get_session_factory
from app.vector.factory import create_vector_dependencies


def main() -> None:
    settings = get_settings()
    embedder, vector_index = create_vector_dependencies(settings)
    session = get_session_factory()()
    indexed = 0
    try:
        documents = session.scalars(
            select(DocumentRecord).where(DocumentRecord.status == "completed")
        ).all()
        for document in documents:
            evidence_rows = session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.document_id == document.id)
            ).all()
            for row in evidence_rows:
                payload = {
                    "evidence_id": row.id,
                    "document_id": row.document_id,
                    "filename": row.filename,
                    "page_number": row.page_number,
                    "kind": row.kind,
                    "content": row.content,
                    "object_key": row.object_key,
                    "mime_type": row.mime_type,
                    "ready": False,
                }
                vector_index.upsert(row.id, embedder.embed(row.content), payload)
                indexed += 1
            if evidence_rows:
                vector_index.activate_document(document.id)
            print(f"Indexed {len(evidence_rows)} evidence items for {document.filename}")
        print(f"Finished: indexed {indexed} evidence items into {settings.qdrant_collection}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
