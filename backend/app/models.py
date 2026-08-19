"""
ORM models: Paper -> Entity -> DatabaseRecord, and Relationship (edges
between two entities within the same paper).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Integer, ForeignKey, DateTime, JSON, Float
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Paper(Base):
    __tablename__ = "papers"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    filename = Column(String(512), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    raw_text = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    status = Column(String(32), default="processing")  # processing|done|error
    error_message = Column(Text, nullable=True)

    entities = relationship("Entity", back_populates="paper", cascade="all, delete-orphan")
    relationships = relationship("EntityRelationship", back_populates="paper", cascade="all, delete-orphan")


class Entity(Base):
    __tablename__ = "entities"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    paper_id = Column(UUID(as_uuid=False), ForeignKey("papers.id"), nullable=False)
    text = Column(String(512), nullable=False)          # surface form, e.g. "TP53"
    normalized_id = Column(String(512), nullable=True)  # canonical form/id
    entity_type = Column(String(64), nullable=False)    # gene|protein|disease|snp|pathway|organism|chemical
    confidence = Column(Float, default=1.0)
    start_char = Column(Integer, nullable=True)
    end_char = Column(Integer, nullable=True)
    sentence = Column(Text, nullable=True)               # evidence sentence

    paper = relationship("Paper", back_populates="entities")
    records = relationship("DatabaseRecord", back_populates="entity", cascade="all, delete-orphan")


class DatabaseRecord(Base):
    """One result from an external biological database for one entity."""
    __tablename__ = "database_records"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    entity_id = Column(UUID(as_uuid=False), ForeignKey("entities.id"), nullable=False)
    source = Column(String(64), nullable=False)   # ncbi_gene|uniprot|pdb|kegg|go|clinvar|string
    status = Column(String(32), default="ok")      # ok|unavailable|error|offline_fallback
    payload = Column(JSON, nullable=True)          # structured response
    fetched_at = Column(DateTime, default=datetime.utcnow)

    entity = relationship("Entity", back_populates="records")


class EntityRelationship(Base):
    """A directed relation extracted between two entities, e.g. TP53 -> inhibits -> MDM2."""
    __tablename__ = "entity_relationships"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    paper_id = Column(UUID(as_uuid=False), ForeignKey("papers.id"), nullable=False)
    source_entity_id = Column(UUID(as_uuid=False), ForeignKey("entities.id"), nullable=False)
    target_entity_id = Column(UUID(as_uuid=False), ForeignKey("entities.id"), nullable=False)
    relation_type = Column(String(64), nullable=False)  # inhibits|activates|interacts_with|regulates|associated_with
    sentence = Column(Text, nullable=True)
    confidence = Column(Float, default=0.7)

    paper = relationship("Paper", back_populates="relationships")
