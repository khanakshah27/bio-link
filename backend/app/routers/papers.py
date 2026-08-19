from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..config import get_settings
from ..services import pdf_extract, pipeline
from ..services.graph_builder import build_graph

router = APIRouter(prefix="/api/papers", tags=["papers"])
settings = get_settings()


@router.post("/upload", response_model=schemas.PaperOut)
def upload_paper(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")

    raw_bytes = file.file.read()
    size_mb = len(raw_bytes) / (1024 * 1024)
    if size_mb > settings.MAX_UPLOAD_MB:
        raise HTTPException(400, f"File exceeds the {settings.MAX_UPLOAD_MB}MB limit.")

    try:
        text = pdf_extract.extract_text(raw_bytes)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    paper = models.Paper(filename=file.filename, raw_text=text, status="processing")
    db.add(paper)
    db.commit()
    db.refresh(paper)

    try:
        paper = pipeline.run_pipeline(db, paper)
    except Exception as exc:
        raise HTTPException(500, f"Processing failed: {exc}")

    return _load_full_paper(db, paper.id)


@router.get("", response_model=list[schemas.PaperListItem])
def list_papers(db: Session = Depends(get_db)):
    return db.query(models.Paper).order_by(models.Paper.uploaded_at.desc()).all()


@router.get("/{paper_id}", response_model=schemas.PaperOut)
def get_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = _load_full_paper(db, paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")
    return paper


@router.get("/{paper_id}/graph", response_model=schemas.GraphOut)
def get_graph(paper_id: str, db: Session = Depends(get_db)):
    paper = (
        db.query(models.Paper)
        .options(joinedload(models.Paper.entities).joinedload(models.Entity.records))
        .filter(models.Paper.id == paper_id)
        .first()
    )
    if not paper:
        raise HTTPException(404, "Paper not found.")

    relationships = (
        db.query(models.EntityRelationship)
        .filter(models.EntityRelationship.paper_id == paper_id)
        .all()
    )
    db_records_by_entity = {e.id: e.records for e in paper.entities}
    return build_graph(paper.entities, relationships, db_records_by_entity)


@router.delete("/{paper_id}")
def delete_paper(paper_id: str, db: Session = Depends(get_db)):
    paper = db.query(models.Paper).filter(models.Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(404, "Paper not found.")
    db.delete(paper)
    db.commit()
    return {"deleted": paper_id}


def _load_full_paper(db: Session, paper_id: str):
    return (
        db.query(models.Paper)
        .options(
            joinedload(models.Paper.entities).joinedload(models.Entity.records),
            joinedload(models.Paper.relationships),
        )
        .filter(models.Paper.id == paper_id)
        .first()
    )
