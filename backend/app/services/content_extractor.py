"""Turns uploaded file bytes into clean text. Nothing is written to disk."""
from dataclasses import dataclass

import pymupdf

from app.services.errors import UnreadableFileError
from app.services.text_utils import normalize_text


def extract_from_pdf(data: bytes) -> str:
    """Extract text from PDF bytes. Returns "" for a PDF with no text layer
    (e.g. a scanned document); raises UnreadableFileError for corrupt files."""
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise UnreadableFileError("This PDF is password-protected.")
            pages = [page.get_text() for page in doc]
    except UnreadableFileError:
        raise
    except Exception:
        raise UnreadableFileError("Could not read this PDF. The file may be corrupted.")
    return normalize_text("\n\n".join(pages))


def extract_from_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")  # never fails; good enough for an MVP
    return normalize_text(text)


@dataclass
class ImageExtraction:
    configured: bool
    text: str
    message: str


def extract_from_image(data: bytes, content_type: str) -> ImageExtraction:
    """Image/screenshot extraction hook.

    Not implemented yet. Later, send `data` to a multimodal model (OpenAI or
    Gemini vision) and return the text/description it finds. Until then we
    report honestly instead of inventing content.
    """
    return ImageExtraction(
        configured=False,
        text="",
        message="Image processing is not configured yet. No text was extracted.",
    )
