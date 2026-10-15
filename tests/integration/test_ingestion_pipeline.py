from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.container import Services
from app.db.models import Base, DocumentRecord, EvidenceRecord, IngestionJob
from app.ingestion.extractor import ExtractedEvidence
from app.ingestion.service import IngestionService
from app.query.service import QueryService
from tests.conftest import FakeEmbedder, FakeProvider, FakeStorage, FakeVectorIndex, make_pdf


def test_pdf_ingestion_indexes_text_and_original_visual_with_page_metadata():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    storage, vectors, provider = FakeStorage(), FakeVectorIndex(), FakeProvider()
    pdf = make_pdf()
    storage.put("source.pdf", pdf, "application/pdf")
    session.add(DocumentRecord(id="doc-1", filename="source.pdf", content_type="application/pdf",
                               checksum="hash", object_key="source.pdf", status="queued"))
    session.add(IngestionJob(id="job-1", document_id="doc-1", status="queued"))
    session.commit()
    class FakeExtractor:
        def extract(self, pdf_bytes, filename):
            return [
                ExtractedEvidence("text", 1, "Quarterly ARR growth was 18 percent."),
                ExtractedEvidence("image", 1, "Rendered chart", b"original-image", "image/png"),
            ]

    service = IngestionService(Services(provider, storage, FakeEmbedder(), vectors), 10, 96, FakeExtractor())
    service.ingest(session, "doc-1")
    records = session.scalars(select(EvidenceRecord).where(EvidenceRecord.document_id == "doc-1")).all()
    assert {"text", "image"}.issubset({record.kind for record in records})
    assert all(record.page_number == 1 for record in records)
    image = next(record for record in records if record.kind == "image")
    assert image.object_key and storage.get(image.object_key)
    assert image.content.startswith("Chart or document image")
    assert all(payload["page_number"] == 1 for payload in vectors.records.values())
    completed_job = session.get(IngestionJob, "job-1")
    assert completed_job.status == "completed"
    assert completed_job.progress_percent == 100
    assert completed_job.progress_details == {
        "pages": 1, "extracted_elements": 2, "text_chunks": 1,
        "table_chunks": 0, "image_records": 1, "total_records": 2,
    }
    response = QueryService(Services(provider, storage, FakeEmbedder(), vectors), 8).answer(
        session, "What does the page show?", []
    )
    assert response.citations
    assert provider.received_images
    assert provider.received_images[0][0] in [storage.get(row.object_key) for row in records if row.object_key]
    session.close()
