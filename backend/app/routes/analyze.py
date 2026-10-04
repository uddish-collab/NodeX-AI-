from fastapi import APIRouter, HTTPException

from app.models.requests import AnalyzeRequest, AnalyzeResponse
from app.services.analyzer import analyze_text

router = APIRouter()


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="'text' must not be empty.")
    return analyze_text(body.text)
