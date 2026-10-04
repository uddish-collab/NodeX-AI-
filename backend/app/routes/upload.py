from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.models.requests import UploadResponse
from app.services.file_validation import ALLOWED_EXTENSIONS, is_supported

router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="No file was provided.")

    if not is_supported(filename, file.content_type):
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

    # The file is only held in memory for now; nothing is written to disk.
    return UploadResponse(
        filename=filename,
        content_type=file.content_type or "",
        size=len(data),
        status="received",
    )
