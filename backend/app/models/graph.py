"""Graph data shapes shared by the API and (later) the AI extraction logic."""
from pydantic import BaseModel, Field


class Node(BaseModel):
    id: str
    label: str
    type: str = "concept"  # e.g. person, event, tool, concept


class Edge(BaseModel):
    source: str  # Node id
    target: str  # Node id
    relationship: str
    explanation: str = ""


class GraphResponse(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
