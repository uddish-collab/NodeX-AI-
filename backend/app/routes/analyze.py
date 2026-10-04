import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.db import repository
from app.db.database import get_db
from app.models.graph import AnalyzeResponse
from app.models.requests import AnalyzeRequest
from app.services import ai_service, graph_mapper
from app.services.text_utils import normalize_text

router = APIRouter()


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(body: AnalyzeRequest, conn: sqlite3.Connection = Depends(get_db)) -> AnalyzeResponse:
    text = normalize_text(body.text)
    if not text:
        raise HTTPException(status_code=400, detail="'text' must not be empty.")

    # If the AI step fails this raises, and nothing is saved.
    result = ai_service.analyze_text(text)

    source_id, id_map, rel_ids = repository.save_analysis(
        conn, body.source_name, body.source_type, text, result
    )
    return graph_mapper.analysis_to_graph(result, source_id, id_map, rel_ids)
