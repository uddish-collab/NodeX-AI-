"""Request/response bodies for the API."""
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings


class AnalyzeRequest(BaseModel):
    text: str = Field(..., max_length=settings.max_text_chars)
    # Optional label for traceability (e.g. the uploaded filename).
    source_name: str = Field("Pasted text", min_length=1, max_length=255)
    source_type: Literal["text", "pdf", "image"] = "text"


class UploadResponse(BaseModel):
    filename: str
    content_type: str
    size: int
    status: str  # "received"
    kind: str  # "pdf" | "docx" | "text" | "image"
    extraction_status: str  # "extracted" | "no_text_found" | "not_configured"
    extraction_method: str  # "direct" | "gemini_vision"
    text: str  # extracted text, ready to send to /analyze
    truncated: bool  # true if text was cut to the max length
    message: str | None = None
