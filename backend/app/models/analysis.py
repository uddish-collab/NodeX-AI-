"""Structured output the AI must produce. Anything that doesn't match is rejected."""
from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal[
    "person", "project", "organization", "event", "topic",
    "technology", "document", "deadline", "resource", "concept",
]
RelationshipType = Literal[
    "related_to", "mentions", "uses", "belongs_to",
    "deadline_for", "references", "same_topic", "depends_on",
]


class Entity(BaseModel):
    id: str  # e.g. "e1"; relationships point at these ids
    name: str
    type: EntityType


class Relationship(BaseModel):
    source: str  # Entity id
    target: str  # Entity id
    relationship: RelationshipType
    explanation: str  # short justification taken from the source text
    confidence: float = Field(ge=0.0, le=1.0)


class AnalysisResult(BaseModel):
    entities: list[Entity]
    relationships: list[Relationship]
    source_summary: str
