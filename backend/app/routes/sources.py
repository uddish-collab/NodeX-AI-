import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.db import repository
from app.db.database import get_db
from app.models.graph import SourceSummary
from app.services.graph_mapper import parse_ref, source_ref

router = APIRouter()


@router.get("/sources", response_model=list[SourceSummary])
def list_sources(conn: sqlite3.Connection = Depends(get_db)) -> list[SourceSummary]:
    """Every saved source (without its full text), oldest first."""
    return [
        SourceSummary(
            id=source_ref(row["id"]),
            name=row["name"],
            source_type=row["source_type"],
            created_at=row["created_at"],
        )
        for row in repository.list_sources(conn)
    ]


@router.delete("/sources/{source_id}")
def delete_source(source_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, str]:
    """Delete one saved source together with its entities and relationships."""
    sid = parse_ref(source_id, "s")
    if sid is None or not repository.delete_source(conn, sid):
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found.")
    return {"status": "deleted", "source_id": source_id}
