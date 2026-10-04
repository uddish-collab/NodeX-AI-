"""Request/response bodies for the non-graph endpoints."""
from pydantic import BaseModel, Field

from app.core.config import settings
from app.models.graph import GraphResponse


class AnalyzeRequest(BaseModel):
    text: str = Field(..., max_length=settings.max_text_chars)


class AnalyzeResponse(GraphResponse):
    status: str  # "placeholder" until real AI analysis exists
    message: str
    input_length: int


class UploadResponse(BaseModel):
    filename: str
    content_type: str
    size: int
    status: str
