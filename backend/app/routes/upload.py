from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.models.requests import UploadResponse
from app.services import content_extractor
from app.services.file_validation import detect_kind, unsupported_message

router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
def upload(file: UploadFile = File(...)) -> UploadResponse:
    # Plain `def`: extraction can call Gemini (slow), so FastAPI runs it in a worker thread.
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="No file was provided.")

    kind = detect_kind(filename, file.content_type)
    if kind is None:
        raise HTTPException(status_code=415, detail=unsupported_message(filename))

    # Read at most limit+1 bytes so oversized files are rejected without loading everything.
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413, detail=f"File too large. Max size is {settings.max_upload_mb} MB."
        )
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Extraction happens in memory; nothing is written to disk.
    result = content_extractor.extract(kind, data)

    return UploadResponse(
        filename=filename,
        content_type=file.content_type or "",
        size=len(data),
        status="received",
        kind=kind,
        extraction_status=result.status,
        extraction_method=result.method,
        text=result.text[: settings.max_text_chars],
        truncated=len(result.text) > settings.max_text_chars,
        message=result.message,
    )
