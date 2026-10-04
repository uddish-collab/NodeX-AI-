from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.models.requests import UploadResponse
from app.services import content_extractor
from app.services.file_validation import ALLOWED_EXTENSIONS, detect_kind

router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="No file was provided.")

    kind = detect_kind(filename, file.content_type)
    if kind is None:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Allowed: PDF, image, or text files ({allowed}).",
        )

    # Read at most limit+1 bytes so oversized files are rejected without loading everything.
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413, detail=f"File too large. Max size is {settings.max_upload_mb} MB."
        )
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Extraction happens in memory; nothing is written to disk.
    message = None
    if kind == "pdf":
        text = content_extractor.extract_from_pdf(data)
    elif kind == "text":
        text = content_extractor.extract_from_text(data)
    else:
        image = content_extractor.extract_from_image(data, file.content_type or "")
        text, message = image.text, image.message

    if text:
        extraction_status = "extracted"
    elif kind == "image":
        extraction_status = "not_configured"
    else:
        extraction_status = "no_text_found"
        message = "No readable text found (a scanned PDF or a blank file?)."

    return UploadResponse(
        filename=filename,
        content_type=file.content_type or "",
        size=len(data),
        status="received",
        kind=kind,
        extraction_status=extraction_status,
        text=text[: settings.max_text_chars],
        truncated=len(text) > settings.max_text_chars,
        message=message,
    )
