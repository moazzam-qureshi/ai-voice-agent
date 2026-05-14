"""Page image extraction for PDF documents.

Normalizes PDFs to page images for VLM processing via PyMuPDF.
Images are also accepted as single-page documents.
"""

import base64
import tempfile
from collections.abc import Iterator
from pathlib import Path

import fitz  # PyMuPDF
import structlog

logger = structlog.get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}


class PageImage:
    """Represents a single page rendered as an image."""

    def __init__(
        self,
        page_number: int,
        image_bytes: bytes,
        base64_string: str,
        width: int,
        height: int,
    ):
        self.page_number = page_number
        self.image_bytes = image_bytes
        self.base64_string = base64_string
        self.width = width
        self.height = height


def extract_pages_as_images(
    file_path: Path | None = None,
    file_content: bytes | None = None,
    filename: str | None = None,
    dpi: int = 200,
    image_format: str = "png",
) -> Iterator[PageImage]:
    """Extract pages from a document as images."""
    if file_path is None and file_content is None:
        raise ValueError("Either file_path or file_content must be provided")

    if file_content is not None and filename is None:
        raise ValueError("filename is required when using file_content")

    if file_path is not None:
        suffix = file_path.suffix.lower()
        working_path = file_path
        temp_file = None
    else:
        suffix = Path(filename).suffix.lower()
        temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        temp_file.write(file_content)
        temp_file.close()
        working_path = Path(temp_file.name)

    try:
        if suffix in IMAGE_EXTENSIONS:
            yield from _extract_from_image(working_path, image_format)
            return

        if suffix == ".pdf":
            yield from _extract_from_pdf(working_path, dpi, image_format)
            return

        raise ValueError(f"Unsupported file type: {suffix}")

    finally:
        if temp_file is not None:
            Path(temp_file.name).unlink(missing_ok=True)


def _extract_from_pdf(
    pdf_path: Path,
    dpi: int,
    image_format: str,
) -> Iterator[PageImage]:
    """Extract pages from PDF as images."""
    doc = fitz.open(pdf_path)
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    logger.info("extracting_pdf_pages", path=str(pdf_path), page_count=len(doc), dpi=dpi)

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=matrix)

            if image_format == "png":
                img_bytes = pix.tobytes("png")
            else:
                img_bytes = pix.tobytes("jpeg")

            b64_string = base64.standard_b64encode(img_bytes).decode("utf-8")

            yield PageImage(
                page_number=page_num + 1,
                image_bytes=img_bytes,
                base64_string=b64_string,
                width=pix.width,
                height=pix.height,
            )
    finally:
        doc.close()


def _extract_from_image(
    image_path: Path,
    image_format: str,
) -> Iterator[PageImage]:
    """Treat an image file as a single-page document."""
    logger.info("processing_image_as_page", path=str(image_path))

    doc = fitz.open(image_path)

    try:
        if len(doc) > 0:
            page = doc[0]
            pix = page.get_pixmap()

            if image_format == "png":
                img_bytes = pix.tobytes("png")
            else:
                img_bytes = pix.tobytes("jpeg")

            b64_string = base64.standard_b64encode(img_bytes).decode("utf-8")

            yield PageImage(
                page_number=1,
                image_bytes=img_bytes,
                base64_string=b64_string,
                width=pix.width,
                height=pix.height,
            )
    finally:
        doc.close()


def get_page_count(
    file_path: Path | None = None,
    file_content: bytes | None = None,
    filename: str | None = None,
) -> int:
    """Return page count without rendering images."""
    if file_path is None and file_content is None:
        raise ValueError("Either file_path or file_content must be provided")

    if file_content is not None and filename is None:
        raise ValueError("filename is required when using file_content")

    if file_path is not None:
        suffix = file_path.suffix.lower()
        working_path = file_path
        temp_file = None
    else:
        suffix = Path(filename).suffix.lower()
        temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        temp_file.write(file_content)
        temp_file.close()
        working_path = Path(temp_file.name)

    try:
        if suffix in IMAGE_EXTENSIONS:
            return 1

        if suffix == ".pdf":
            doc = fitz.open(working_path)
            count = len(doc)
            doc.close()
            return count

        return 0

    finally:
        if temp_file is not None:
            Path(temp_file.name).unlink(missing_ok=True)
