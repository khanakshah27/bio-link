# BioLink

**An Intelligent Research Paper → Biological Database Mapper**

BioLink reads a biomedical research paper (PDF), extracts the genes, proteins,
diseases, SNPs, pathways, organisms and chemicals it mentions, cross-references
each one against seven authoritative biological databases in real time, and
renders the result as an interactive dashboard and knowledge graph.

```
Upload PDF → Extract text → Biomedical NER → Normalize entities
    → Query NCBI / UniProt / PDB / KEGG / GO / ClinVar / STRING
    → Extract relationships → Build knowledge graph → Dashboard + AI summary
```

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI |
| NER | scispacy / spaCy (BioBERT-compatible pipeline), with a dictionary+regex fallback |
| Database | PostgreSQL (SQLAlchemy ORM) |
| Frontend | Plain HTML + CSS + vanilla JS (no framework) |
| Graph visualization | Cytoscape.js |

## Project layout

```
BioLink/
  backend/
    app/
      main.py                 FastAPI app + static frontend mount
      config.py                Settings (env vars)
      database.py               SQLAlchemy engine/session
      models.py                 ORM models (Paper, Entity, DatabaseRecord, EntityRelationship)
      schemas.py                 Pydantic response models
      routers/papers.py          /api/papers/* endpoints
      services/
        pdf_extract.py            PyMuPDF/pdfplumber text extraction
        ner.py                     scispacy/spaCy NER + dictionary fallback
        normalize.py                Entity alias/canonicalization
        relationship_extraction.py   Pattern-based relation extraction
        db_integrations.py           NCBI/UniProt/PDB/KEGG/GO/ClinVar/STRING clients
        summarizer.py                 Extractive (or Claude-powered) summary
        graph_builder.py               Cytoscape.js elements builder
        pipeline.py                    Orchestrates the full pipeline
    requirements.txt
    .env.example
  frontend/
    index.html / style.css / app.js    (served directly by FastAPI, no build step)
  docker-compose.yml            Local PostgreSQL
  scripts/download_models.sh    NER model installer
```

## Setup

### 1. Start PostgreSQL

```bash
docker compose up -d
```

(Or point `DATABASE_URL` in `.env` at any Postgres instance you already have.)

### 2. Install backend dependencies

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 3. (Recommended) Install a real biomedical NER model

```bash
../scripts/download_models.sh
```

BioLink works without this step — `ner.py` automatically falls back to a
curated dictionary + regex extractor — but a real scispacy/BioBERT-class
model recognizes far more entities in real papers than the fallback's
curated lexicon.

### 4. Run

```bash
uvicorn app.main:app --reload
```

Open **http://localhost:8000** — the FastAPI app serves the frontend directly,
so there's no separate frontend server or build step.

## How the pieces fit together

**PDF → text.** `pdf_extract.py` uses PyMuPDF, falling back to pdfplumber for
PDFs it struggles with.

**Text → entities.** `ner.py` tries to load a scispacy biomedical model, then
a plain spaCy model, and finally a zero-dependency dictionary + regex
extractor, in that order — whichever is available. Every entity is classified
into one of BioLink's seven types (gene, protein, disease, SNP, pathway,
organism, chemical) and tagged with the sentence it appeared in, which powers
the "evidence" view in the dashboard.

**Entities → canonical IDs.** `normalize.py` collapses aliases (e.g. `p53` →
`TP53`) so the same biological entity doesn't appear as multiple graph nodes.

**Entities → database records.** `db_integrations.py` makes live HTTP calls
to NCBI Gene, UniProt, RCSB PDB, KEGG, QuickGO, ClinVar and STRING, matching
each database to the entity types it's actually useful for (e.g. genes →
NCBI/UniProt/PDB/STRING/GO; diseases → ClinVar/KEGG). If a call fails —
no network, rate limiting, an unreachable host — BioLink falls back to a
small cached example record for a few well-known genes/diseases so the UI
still has something meaningful to show; these are always labeled
`offline_fallback` in the API and shown as a "cached" pill in the UI, never
silently mixed in with live data. Set `USE_OFFLINE_FALLBACK=false` in `.env`
to disable this and surface raw errors instead.

**Sentences → relationships.** `relationship_extraction.py` looks for
recognized relational verbs ("inhibits", "interacts with", "increases ...
risk", etc.) co-occurring with two or more entities in the same sentence and
emits labeled graph edges — this is what turns the knowledge graph from a
flat entity list into an actual graph.

**Everything → the knowledge graph.** `graph_builder.py` assembles entities,
relationships, and one node per external database source that returned data,
into a Cytoscape.js `{nodes, edges}` structure, matching the "disease → gene →
UniProt → PDB structure" branching graph described in the original project
brief.

**Everything → a summary.** `summarizer.py` defaults to a dependency-free
extractive summary (scores sentences by entity density + position). If
`ANTHROPIC_API_KEY` is set, it instead asks Claude for a short abstractive
summary grounded in the extracted entities/relationships.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/papers/upload` | Upload a PDF, run the full pipeline, return the paper with entities |
| `GET` | `/api/papers` | List previously analyzed papers |
| `GET` | `/api/papers/{id}` | Full paper detail (entities, relationships, summary) |
| `GET` | `/api/papers/{id}/graph` | Cytoscape.js `{elements: {nodes, edges}}` |
| `DELETE` | `/api/papers/{id}` | Delete a paper and its data |

## Notes on external network access

BioLink's database clients call the real public REST APIs for NCBI, UniProt,
RCSB PDB, KEGG, QuickGO, ClinVar and STRING. If you're running this behind a
restricted network/proxy, make sure outbound HTTPS is allowed to:

```
eutils.ncbi.nlm.nih.gov   rest.uniprot.org   search.rcsb.org
rest.kegg.jp   www.ebi.ac.uk   string-db.org
```

Without that access, BioLink still runs end-to-end using the offline
fallback described above — useful for demos, but not a substitute for the
live data in normal use.

## Frontend design

Cream base (`#FBF7F1`) with a pastel-purple system (`#9B87C4` primary, deep
plum `#5B3E8C` for headings) and a distinct soft pastel per entity type
(pink for proteins, coral for diseases, gold for pathways, mint for
organisms, sky blue for chemicals) — used consistently across the stat
cards, entity chips, evidence highlights, database table, and graph nodes,
so a color always means the same thing everywhere in the app. Fraunces
(serif) carries headings and the AI summary; Karla (sans) is the UI/body
face; IBM Plex Mono is used for gene symbols, IDs, and status badges.

## Roadmap (see also section 10–11 of the original proof-of-concept)

- Multi-paper comparison view
- Confidence-score display in the UI (already tracked in the data model)
- Swap the relationship extractor for a trained BioBERT relation-classification head
- Exportable PDF report per paper
