from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .config import get_settings
from .routers import papers

settings = get_settings()

app = FastAPI(
    title="BioLink API",
    description="Reads biomedical papers, extracts biological entities, "
                 "and links them to authoritative biological databases.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(papers.router)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Serve the static HTML/CSS/JS frontend -------------------------------
# Mounted last (and at "/") so it acts as a catch-all that serves
# index.html at "/" and index.html-relative assets (style.css, app.js)
# correctly, while the /api/* routes above still take priority.
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
