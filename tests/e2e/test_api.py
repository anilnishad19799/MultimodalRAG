from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.container import Services
from app.db.models import Base, DocumentRecord, EvidenceRecord
from app.main import create_app
from tests.conftest import FakeEmbedder, FakeProvider, FakeStorage, FakeVectorIndex


def test_text_and_multimodal_chat_return_validated_citations(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage, vector, provider = FakeStorage(), FakeVectorIndex(), FakeProvider()
    services = Services(provider, storage, FakeEmbedder(), vector)
    with factory() as session:
        session.add(DocumentRecord(id="doc-1", filename="report.pdf", content_type="application/pdf",
                                   checksum="seed-hash", object_key="report.pdf", status="completed"))
        row = EvidenceRecord(id="evidence-1", document_id="doc-1", kind="text", page_number=2,
                             filename="report.pdf", content="ARR growth reached 18 percent.")
        session.add(row)
        session.commit()
        vector.upsert("evidence-1", [1.0], {"evidence_id": "evidence-1", "document_id": "doc-1",
                     "filename": "report.pdf", "page_number": 2, "kind": "text",
                     "content": row.content, "object_key": None})
    settings = Settings(model_provider="openai", database_url="sqlite://")
    app = create_app(settings, services, factory)
    monkeypatch.setattr("app.api.routes.ingest_document.delay", lambda document_id: None)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"question": "What is the ARR growth?"})
        assert response.status_code == 200
        data = response.json()
        assert data["citations"][0]["page_number"] == 2
        png = make_one_pixel_png()
        response = client.post("/api/v1/chat/multimodal", data={"question": "Describe this image"},
                              files=[("images", ("input.png", png, "image/png"))])
        assert response.status_code == 200
        assert provider.received_images[0][0] == png
        upload = client.post("/api/v1/documents", files={"file": ("standalone.png", png, "image/png")})
        assert upload.status_code == 202
        job = upload.json()
        status = client.get(f"/api/v1/ingestions/{job['job_id']}")
        assert status.status_code == 200
        assert status.json()["status"] == "queued"


def make_one_pixel_png():
    from PIL import Image
    import io

    output = io.BytesIO()
    Image.new("RGB", (1, 1), color="red").save(output, format="PNG")
    return output.getvalue()
