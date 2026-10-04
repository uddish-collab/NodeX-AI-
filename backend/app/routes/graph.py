import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.db import repository
from app.db.database import get_db
from app.models.graph import GraphResponse, NodeDetails
from app.services import graph_mapper as gm

router = APIRouter()


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    source_id: str | None = None,
    type: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> GraphResponse:
    """The whole saved graph. Optional filters: ?source_id=s1 and ?type=project."""
    sid = None
    if source_id is not None:
        sid = gm.parse_ref(source_id, "s")
        if sid is None:
            raise HTTPException(status_code=400, detail="source_id must look like 's1'.")

    nodes = [gm.row_to_node(r) for r in repository.get_entities(conn, sid, type)]
    kept = {n.id for n in nodes}
    # Only keep edges whose both ends are still in the (possibly filtered) graph.
    edges = [
        e for e in map(gm.row_to_edge, repository.get_relationships(conn, sid))
        if e.source in kept and e.target in kept
    ]
    return GraphResponse(nodes=nodes, edges=edges)


@router.get("/graph/node/{node_id}", response_model=NodeDetails)
def get_node(node_id: str, conn: sqlite3.Connection = Depends(get_db)) -> NodeDetails:
    eid = gm.parse_ref(node_id, "e")
    row = repository.get_entity(conn, eid) if eid is not None else None
    if row is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")

    rels = repository.get_entity_relationships(conn, eid)
    neighbour_ids = {
        i for r in rels for i in (r["source_entity_id"], r["target_entity_id"])
    } - {eid}
    source = repository.get_source(conn, row["source_id"])
    neighbours = repository.get_entities_by_ids(conn, neighbour_ids)
    return NodeDetails(
        node=gm.row_to_node(row),
        connected_nodes=[gm.row_to_node(r) for r in neighbours],
        relationships=[gm.row_to_edge(r) for r in rels],
        source=gm.row_to_source(source) if source else None,
    )
