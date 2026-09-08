"""
Pydantic schemas used for API request/response bodies.
"""
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, ConfigDict


class DatabaseRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source: str
    status: str
    payload: Optional[Dict[str, Any]] = None


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    text: str
    normalized_id: Optional[str] = None
    entity_type: str
    confidence: float
    sentence: Optional[str] = None
    records: List[DatabaseRecordOut] = []


class RelationshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    sentence: Optional[str] = None
    confidence: float


class PaperListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    status: str


class PaperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    status: str
    error_message: Optional[str] = None
    summary: Optional[str] = None
    entities: List[EntityOut] = []
    relationships: List[RelationshipOut] = []


class GraphNode(BaseModel):
    data: Dict[str, Any]


class GraphEdge(BaseModel):
    data: Dict[str, Any]


class GraphOut(BaseModel):
    elements: Dict[str, List[Dict[str, Any]]]  # {"nodes": [...], "edges": [...]}


class AskRequest(BaseModel):
    """
    "Ask Bio-Link" takes the paper's already-extracted entities/
    relationships directly in the request (the shape the frontend already
    holds in memory after upload or paper selection) rather than a
    paper_id, so the same endpoint works for both a persisted paper and
    the client-only demo dataset without a DB round trip.
    """
    filename: str
    summary: Optional[str] = None
    entities: List[EntityOut] = []
    relationships: List[RelationshipOut] = []
    question: str


class AskResponse(BaseModel):
    answer: str
    grounded: bool  # False if the LLM wasn't reachable/configured
