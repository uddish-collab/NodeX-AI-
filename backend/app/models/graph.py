"""The graph shape the frontend consumes: nodes + edges.
(The AI's internal entities/relationships are converted into these.)"""
from pydantic import BaseModel, Field


class Node(BaseModel):
    id: str  # globally unique, e.g. "e12"
    label: str
    type: str
    source_id: str  # where this node came from, e.g. "s3"


class Edge(BaseModel):
    id: str  # e.g. "r7"
    source: str  # Node id
    target: str  # Node id
    relationship: str
    explanation: str = ""
    confidence: float
    source_id: str  # originating source, e.g. "s3"


class GraphResponse(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class AnalyzeResponse(GraphResponse):
    source_summary: str
    source_id: str  # the source record created for this analysis


class SourceInfo(BaseModel):
    id: str
    name: str
    source_type: str
    created_at: str
    excerpt: str  # first part of the original text


class NodeDetails(BaseModel):
    node: Node
    connected_nodes: list[Node]
    relationships: list[Edge]
    source: SourceInfo | None
