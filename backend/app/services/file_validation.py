"""Checks that an uploaded file is one of the supported MVP formats."""
from pathlib import PurePath

ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}


def is_supported(filename: str, content_type: str | None) -> bool:
    kind = ALLOWED_EXTENSIONS.get(PurePath(filename).suffix.lower())
    if kind is None:
        return False
    ct = (content_type or "").lower()
    # Content type must agree with the extension's kind.
    if kind == "pdf":
        return ct == "application/pdf"
    if kind == "text":
        return ct.startswith("text/")
    return ct.startswith("image/")
