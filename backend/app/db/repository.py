"""All SQL lives here. Routes and services never write SQL themselves."""
import sqlite3

from app.models.analysis import AnalysisResult


def save_analysis(
    conn: sqlite3.Connection, name: str, source_type: str, content: str, result: AnalysisResult
) -> tuple[int, dict[str, int], list[int]]:
    """Store a source with its entities and relationships in one transaction.
    Returns (source_id, {ai_entity_id: database_entity_id}, [relationship_ids])."""
    with conn:  # commits on success, rolls back on error
        source_id = conn.execute(
            "INSERT INTO sources (name, source_type, content) VALUES (?, ?, ?)",
            (name, source_type, content),
        ).lastrowid
        id_map: dict[str, int] = {}
        for e in result.entities:
            id_map[e.id] = conn.execute(
                "INSERT INTO entities (source_id, name, type) VALUES (?, ?, ?)",
                (source_id, e.name, e.type),
            ).lastrowid
        rel_ids: list[int] = []
        for r in result.relationships:
            rel_ids.append(
                conn.execute(
                    "INSERT INTO relationships (source_id, source_entity_id, target_entity_id,"
                    " relationship_type, explanation, confidence) VALUES (?, ?, ?, ?, ?, ?)",
                    (source_id, id_map[r.source], id_map[r.target],
                     r.relationship, r.explanation, r.confidence),
                ).lastrowid
            )
    return source_id, id_map, rel_ids


def get_entities(
    conn: sqlite3.Connection, source_id: int | None = None, entity_type: str | None = None
) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM entities WHERE 1=1", []
    if source_id is not None:
        sql += " AND source_id = ?"
        args.append(source_id)
    if entity_type:
        sql += " AND type = ?"
        args.append(entity_type)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def get_relationships(
    conn: sqlite3.Connection, source_id: int | None = None
) -> list[sqlite3.Row]:
    if source_id is None:
        return conn.execute("SELECT * FROM relationships ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM relationships WHERE source_id = ? ORDER BY id", (source_id,)
    ).fetchall()


def get_entity(conn: sqlite3.Connection, entity_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()


def get_entity_relationships(conn: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM relationships WHERE source_entity_id = ? OR target_entity_id = ?"
        " ORDER BY id",
        (entity_id, entity_id),
    ).fetchall()


def get_entities_by_ids(conn: sqlite3.Connection, ids: set[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM entities WHERE id IN ({marks}) ORDER BY id", list(ids)
    ).fetchall()


def get_source(conn: sqlite3.Connection, source_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
