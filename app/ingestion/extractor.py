import base64
import io
from dataclasses import dataclass
from typing import Callable

import fitz
from PIL import Image
from unstructured.partition.pdf import partition_pdf

from app.core.errors import InvalidUpload


@dataclass
class ExtractedEvidence:
    kind: str
    page_number: int
    content: str
    image_bytes: bytes | None = None
    mime_type: str | None = None
    ordinal: int = 0


class PdfExtractor:
    def __init__(self, max_pages: int, render_dpi: int):
        self.max_pages, self.render_dpi = max_pages, render_dpi

    def extract(
        self, pdf_bytes: bytes, filename: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[ExtractedEvidence]:
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise InvalidUpload("The uploaded PDF is invalid or unreadable") from exc
        try:
            if document.page_count > self.max_pages:
                raise InvalidUpload(f"PDF exceeds the {self.max_pages}-page limit")
            if progress_callback:
                progress_callback(0, document.page_count, "Analyzing PDF layout and extracting text/tables")

            # Unstructured detects semantic elements, tables, and embedded visual
            # blocks. Its high-resolution strategy also performs OCR on scanned PDFs.
            elements = partition_pdf(
                file=io.BytesIO(pdf_bytes),
                strategy="hi_res",
                infer_table_structure=True,
                extract_image_block_types=["Image", "Table"],
                extract_image_block_to_payload=True,
                languages=["eng"],
                pdf_image_dpi=self.render_dpi,
            )
            evidence: list[ExtractedEvidence] = []
            for ordinal, element in enumerate(elements):
                metadata = element.metadata
                page_no = int(getattr(metadata, "page_number", None) or 1)
                category = str(getattr(element, "category", type(element).__name__)).lower()
                content = str(element).strip()
                if category == "table":
                    # HTML retains row and header relationships for table retrieval.
                    table_html = getattr(metadata, "text_as_html", None)
                    evidence.append(ExtractedEvidence("table", page_no, str(table_html or content), ordinal=ordinal))
                    self._append_image_element(evidence, metadata, page_no, ordinal, "Table visual")
                elif category == "image":
                    self._append_image_element(evidence, metadata, page_no, ordinal, content or "Embedded image")
                elif content and category != "pagebreak":
                    evidence.append(ExtractedEvidence("text", page_no, content, ordinal=ordinal))
                    # Some Unstructured element types contain both text and an
                    # associated image block (for example, a detected table).
                    if getattr(metadata, "image_base64", None):
                        self._append_image_element(evidence, metadata, page_no, ordinal, "Embedded visual element")

            # Full page renders preserve vector charts and diagrams that are not
            # separate raster image elements in the PDF structure.
            if progress_callback:
                progress_callback(0, document.page_count, "Rendering original page images")
            for page_index, page in enumerate(document):
                pix = page.get_pixmap(dpi=self.render_dpi, alpha=False)
                evidence.append(
                    ExtractedEvidence(
                        "image", page_index + 1, f"Rendered PDF page {page_index + 1}",
                        pix.tobytes("png"), "image/png", 1_000_000 + page_index,
                    )
                )
                if progress_callback:
                    progress_callback(page_index + 1, document.page_count,
                                      f"Rendered page {page_index + 1} of {document.page_count}")
        except InvalidUpload:
            raise
        except Exception as exc:
            raise InvalidUpload("The PDF could not be partitioned; check that it is readable and unencrypted") from exc
        finally:
            document.close()
        if not evidence:
            raise InvalidUpload(f"No text, tables, or images could be extracted from {filename}")
        return evidence

    @staticmethod
    def _append_image_element(
        evidence: list[ExtractedEvidence], metadata, page_no: int, ordinal: int, description: str
    ) -> None:
        image_base64 = getattr(metadata, "image_base64", None)
        if not image_base64:
            return
        try:
            image_bytes = base64.b64decode(image_base64, validate=True)
        except (ValueError, TypeError):
            return
        mime_type = getattr(metadata, "image_mime_type", None) or "image/png"
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            return
        evidence.append(ExtractedEvidence("image", page_no, description, image_bytes, mime_type, ordinal))


def validate_upload(
    filename: str,
    content_type: str,
    data: bytes,
    max_bytes: int,
    max_image_bytes: int = 20 * 1024 * 1024,
) -> str:
    if not data:
        raise InvalidUpload("Uploaded file is empty")
    if len(data) > max_bytes:
        raise InvalidUpload(f"Upload exceeds the {max_bytes}-byte limit")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == "pdf" and content_type in {"application/pdf", "application/octet-stream"} and data.startswith(b"%PDF-"):
        return "application/pdf"
    image_types = {"png": ("image/png", b"\x89PNG\r\n\x1a\n"), "jpg": ("image/jpeg", b"\xff\xd8\xff"),
                   "jpeg": ("image/jpeg", b"\xff\xd8\xff"), "webp": ("image/webp", b"RIFF")}
    if extension == "webp":
        mime, signature = image_types[extension]
        if not (data.startswith(signature) and data[8:12] == b"WEBP"):
            raise InvalidUpload("Image content does not match its file extension")
    if extension in image_types:
        if len(data) > max_image_bytes:
            raise InvalidUpload(f"Image exceeds the {max_image_bytes}-byte limit")
        mime, signature = image_types[extension]
        if content_type in {mime, "application/octet-stream"} and data.startswith(signature):
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if image.width * image.height > 40_000_000:
                        raise InvalidUpload("Image exceeds the 40-megapixel limit")
                    image.verify()
            except InvalidUpload:
                raise
            except Exception as exc:
                raise InvalidUpload("Image file is invalid or unreadable") from exc
            return mime
    raise InvalidUpload("Only valid PDF, PNG, JPEG, and WebP files are supported")
