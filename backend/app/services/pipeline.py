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
        entity_rows: dict[str, models.Entity] = {}
        for e in entities_extracted:
            normalized_id = norm.normalize(e.text, e.entity_type)
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
            entity_rows[f"{e.text.upper()}::{e.start_char}"] = row

        # --- Query external databases per entity (dedup by normalized id) --
        queried_normalized_ids: set[str] = set()
        db_records_by_entity: dict[str, list[models.DatabaseRecord]] = {}
        for e in entities_extracted:
            row = entity_rows[f"{e.text.upper()}::{e.start_char}"]
            dedup_key = f"{row.entity_type}::{row.normalized_id}"
            records_payload = (
                dbint.query_all_for_entity(row.text, row.entity_type)
                if dedup_key not in queried_normalized_ids else []
            )
            queried_normalized_ids.add(dedup_key)

            db_records_by_entity.setdefault(row.id, [])
            for rec in records_payload:
                dr = models.DatabaseRecord(
                    entity_id=row.id,
                    source=rec["source"],
                    status=rec["status"],
                    payload=rec["payload"],
                )
                db.add(dr)
                db_records_by_entity[row.id].append(dr)

        # --- Relationship extraction ---------------------------------------
        relations = relext.extract_relationships(entities_extracted)
        for r in relations:
            src_row = _find_entity_row(entity_rows, r.source_text)
            tgt_row = _find_entity_row(entity_rows, r.target_text)
            if not src_row or not tgt_row:
                continue
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
    for key, row in entity_rows.items():
        if key.startswith(surface_text.upper() + "::"):
            return row
    return None
