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


def detect_kind(filename: str, content_type: str | None) -> str | None:
    """Return "pdf", "text" or "image", or None if unsupported."""
    kind = ALLOWED_EXTENSIONS.get(PurePath(filename).suffix.lower())
    if kind is None:
        return None
    ct = (content_type or "").lower()
    # Content type must agree with the extension's kind.
    ok = {
        "pdf": ct == "application/pdf",
        "text": ct.startswith("text/"),
        "image": ct.startswith("image/"),
    }[kind]
    return kind if ok else None
