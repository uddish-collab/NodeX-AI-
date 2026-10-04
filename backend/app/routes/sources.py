import sqlite3

from fastapi import APIRouter, Depends

from app.db import repository
from app.db.database import get_db
from app.models.graph import SourceSummary
from app.services.graph_mapper import source_ref

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
