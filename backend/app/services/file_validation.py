"""Checks that an uploaded file is one of the supported formats."""
from pathlib import PurePath

ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "text",
    ".md": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Declared content types accepted per extension. Browsers/tools sometimes send
# generic types for .docx, so the real check for it is the ZIP/XML structure.
_MIME_RULES = {
    ".pdf": {"application/pdf"},
    ".docx": {DOCX_MIME, "application/zip", "application/octet-stream"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg", "image/jpg", "image/pjpeg"},
    ".jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"},
    ".webp": {"image/webp"},
}

SUPPORTED_LABEL = "PDF, DOCX, PNG, JPG/JPEG, WEBP, TXT or MD"


def unsupported_message(filename: str) -> str:
    msg = f"Unsupported file type. Allowed: {SUPPORTED_LABEL}."
    if PurePath(filename).suffix.lower() == ".doc":
        msg += " Legacy .doc files are not supported; please save the file as .docx."
    return msg


def detect_kind(filename: str, content_type: str | None) -> str | None:
    """Return "pdf", "docx", "text" or "image", or None if unsupported."""
    ext = PurePath(filename).suffix.lower()
    kind = ALLOWED_EXTENSIONS.get(ext)
    if kind is None:
        return None
    ct = (content_type or "").split(";")[0].strip().lower()
    if kind == "text":
        return kind if ct.startswith("text/") else None
    return kind if ct in _MIME_RULES[ext] else None


def image_mime_from_bytes(data: bytes) -> str | None:
    """Identify an image by its first bytes (don't trust the filename)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
