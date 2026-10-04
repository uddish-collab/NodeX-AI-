"""Adapter between the AI's entities/relationships, the database rows,
and the nodes/edges the frontend consumes. Public ids are prefixed:
s = source, e = entity (node), r = relationship (edge)."""
import sqlite3

from app.models.analysis import AnalysisResult
from app.models.graph import AnalyzeResponse, Edge, Node, SourceInfo


def node_id(entity_id: int) -> str:
    return f"e{entity_id}"


def source_ref(source_id: int) -> str:
    return f"s{source_id}"


def parse_ref(value: str, prefix: str) -> int | None:
    """"e12" -> 12. Returns None if the value is not a valid id."""
    if value.startswith(prefix) and value[len(prefix):].isdigit():
        return int(value[len(prefix):])
    return None


def row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=node_id(row["id"]), label=row["name"], type=row["type"],
        source_id=source_ref(row["source_id"]),
    )


def row_to_edge(row: sqlite3.Row) -> Edge:
    return Edge(
        id=f"r{row['id']}",
        source=node_id(row["source_entity_id"]),
        target=node_id(row["target_entity_id"]),
        relationship=row["relationship_type"],
        explanation=row["explanation"],
        confidence=row["confidence"],
        source_id=source_ref(row["source_id"]),
    )


def row_to_source(row: sqlite3.Row) -> SourceInfo:
    return SourceInfo(
        id=source_ref(row["id"]), name=row["name"], source_type=row["source_type"],
        created_at=row["created_at"], excerpt=row["content"][:300],
    )


def analysis_to_graph(
    result: AnalysisResult, source_id: int, id_map: dict[str, int], rel_ids: list[int]
) -> AnalyzeResponse:
    """AI entities + relationships -> API nodes + edges, using the saved database ids
    so every edge points at a real node id."""
    sref = source_ref(source_id)
    nodes = [
        Node(id=node_id(id_map[e.id]), label=e.name, type=e.type, source_id=sref)
        for e in result.entities
    ]
    edges = [
        Edge(
            id=f"r{rid}",
            source=node_id(id_map[r.source]),
            target=node_id(id_map[r.target]),
            relationship=r.relationship,
            explanation=r.explanation,
            confidence=r.confidence,
            source_id=sref,
        )
        for r, rid in zip(result.relationships, rel_ids)
    ]
    return AnalyzeResponse(
        nodes=nodes, edges=edges, source_summary=result.source_summary, source_id=sref
    )
