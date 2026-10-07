"""
Orchestrates the full BioLink pipeline for one uploaded paper:

    PDF bytes -> text -> NER -> normalize -> relationship extraction
    -> external DB queries -> persist to Postgres -> summary

This is the function main.py's upload endpoint calls; it owns the DB
session commits so partial failures don't leave a half-written paper row.
"""
from sqlalchemy.orm import Session

from .. import models
from . import pdf_extract, ner, normalize as norm, relationship_extraction as relext
from . import db_integrations as dbint
from . import summarizer


def run_pipeline(db: Session, paper: models.Paper) -> models.Paper:
    try:
        entities_extracted, ner_backend = ner.extract_entities(paper.raw_text)

        # --- Persist entities --------------------------------------------
        # One row per distinct (type, normalized id) per paper. Persisting
        # every mention produced hundreds of duplicate, disconnected graph
        # nodes; the first mention supplies the evidence sentence.
        entity_rows: dict[tuple[str, str], models.Entity] = {}
        for e in entities_extracted:
            normalized_id = norm.normalize(e.text, e.entity_type)
            key = (e.entity_type, normalized_id)
            if key in entity_rows:
                continue
            row = models.Entity(
                paper_id=paper.id,
                text=e.text,
                normalized_id=normalized_id,
                entity_type=e.entity_type,
                confidence=e.confidence,
                start_char=e.start_char,
                end_char=e.end_char,
                sentence=e.sentence,
            )
            db.add(row)
            db.flush()  # assigns row.id without committing
            entity_rows[key] = row

        # --- Query external databases once per entity -----------------------
        for row in entity_rows.values():
            for rec in dbint.query_all_for_entity(row.text, row.entity_type):
                db.add(models.DatabaseRecord(
                    entity_id=row.id,
                    source=rec["source"],
                    status=rec["status"],
                    payload=rec["payload"],
                ))

        # --- Relationship extraction ---------------------------------------
        relations = relext.extract_relationships(entities_extracted)
        seen_relations: set[tuple[str, str, str]] = set()
        for r in relations:
            src_row = _find_entity_row(entity_rows, r.source_text)
            tgt_row = _find_entity_row(entity_rows, r.target_text)
            if not src_row or not tgt_row or src_row.id == tgt_row.id:
                continue
            rel_key = (src_row.id, tgt_row.id, r.relation_type)
            if rel_key in seen_relations:
                continue
            seen_relations.add(rel_key)
            db.add(models.EntityRelationship(
                paper_id=paper.id,
                source_entity_id=src_row.id,
                target_entity_id=tgt_row.id,
                relation_type=r.relation_type,
                sentence=r.sentence,
                confidence=r.confidence,
            ))

        # --- Summary ---------------------------------------------------
        paper.summary = summarizer.summarize(paper.raw_text, entities_extracted, relations)
        paper.status = "done"
        paper.error_message = None

        db.commit()
        db.refresh(paper)
        return paper

    except Exception as exc:  # keep the paper row, but mark it failed
        db.rollback()
        paper.status = "error"
        paper.error_message = str(exc)
        db.add(paper)
        db.commit()
        db.refresh(paper)
        raise


def _find_entity_row(entity_rows: dict, surface_text: str):
    """Match a relation endpoint's surface text to a persisted entity row,
    by surface text first and then by normalized id (so "p53" finds TP53)."""
    upper = surface_text.strip().upper()
    for row in entity_rows.values():
        if row.text.upper() == upper or (row.normalized_id or "").upper() == upper:
            return row
    for (etype, _), row in entity_rows.items():
        if norm.normalize(surface_text, etype) == row.normalized_id:
            return row
    return None
