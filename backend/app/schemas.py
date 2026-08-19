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
