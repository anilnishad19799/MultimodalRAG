import base64
from types import SimpleNamespace

import pytest
from unittest.mock import patch

from app.core.errors import InvalidUpload
from app.ingestion.extractor import PdfExtractor, validate_upload
from app.ingestion.service import chunk_table, chunk_text
from tests.conftest import make_pdf


def test_chunk_text_preserves_content_with_overlap():
    text = "alpha " * 1000
    chunks = chunk_text(text, max_chars=500, overlap=50)
    assert len(chunks) > 2
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert chunks[0][:5] == "alpha"


def test_validate_pdf_checks_extension_mime_and_signature():
    assert validate_upload("sample.pdf", "application/pdf", b"%PDF-1.7 data", 100) == "application/pdf"
    with pytest.raises(InvalidUpload):
        validate_upload("sample.pdf", "image/png", b"%PDF-1.7 data", 100)


def test_validate_rejects_empty_and_oversize_files():
    with pytest.raises(InvalidUpload):
        validate_upload("x.pdf", "application/pdf", b"", 100)
    with pytest.raises(InvalidUpload):
        validate_upload("x.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"x" * 20, 10)


def test_unstructured_pdf_elements_keep_table_image_and_page_metadata():
    table = SimpleNamespace(
        category="Table",
        metadata=SimpleNamespace(
            page_number=2,
            text_as_html="<table><tr><th>ARR</th></tr><tr><td>18%</td></tr></table>",
            image_base64=base64.b64encode(b"table-image").decode("ascii"),
            image_mime_type="image/png",
        ),
        __str__=lambda self: "ARR 18%",
    )
    with patch("app.ingestion.extractor.partition_pdf", return_value=[table]):
        evidence = PdfExtractor(max_pages=5, render_dpi=72).extract(make_pdf(), "sample.pdf")

    assert any(item.kind == "table" and "<th>ARR</th>" in item.content for item in evidence)
    assert any(item.kind == "image" and item.image_bytes == b"table-image" and item.page_number == 2 for item in evidence)
    assert any(item.kind == "image" and item.content == "Rendered PDF page 1" for item in evidence)


def test_large_table_chunks_repeat_column_header_and_keep_rows():
    table = "| Name | Value |\n| --- | --- |\n" + "".join(f"| metric-{i} | {i}% |\n" for i in range(200))
    chunks = chunk_table(table, max_chars=400)
    assert len(chunks) > 1
    assert all("| Name | Value |" in chunk for chunk in chunks)
    assert all(len(chunk) <= 400 for chunk in chunks)
