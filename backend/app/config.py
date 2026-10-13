"""
Central configuration for BioLink.

All settings are read from environment variables (or a .env file at the
project root) so the same code runs in dev, CI, and production without
edits. See .env.example for the full list of variables.
"""
import os
from functools import lru_cache

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional; env vars can be set directly instead.
    pass


class Settings:
    # --- Database -----------------------------------------------------
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://biolink:biolink@localhost:5432/biolink",
    )

    # --- NER -------------------------------------------------------------
    # Preferred model: a scispacy biomedical model (e.g. en_core_sci_sm).
    # If it isn't installed, ner.py automatically falls back to a
    # dictionary + regex based extractor so the app still runs.
    SCISPACY_MODEL: str = os.getenv("SCISPACY_MODEL", "en_core_sci_sm")
    SPACY_FALLBACK_MODEL: str = os.getenv("SPACY_FALLBACK_MODEL", "en_core_web_sm")

    # Gene symbols that are NOT on the curated list are guessed from their shape
    # (2-8 capitals/digits, not a known acronym) at confidence 0.5. This is
    # always on in the dictionary-only tier. For the spaCy/scispaCy tiers it is
    # OFF by default: on methods/statistics text full of acronyms (BMI, COPD,
    # GATK, ...) it produced 17 false genes in 12 sentences (eval/dataset_noise.json),
    # far more than the genes it recovered. Set INCLUDE_GUESSED_GENES=true to
    # trade precision for recall on genes outside the curated list.
    INCLUDE_GUESSED_GENES: bool = os.getenv("INCLUDE_GUESSED_GENES", "false").lower() == "true"

    # --- External biological database APIs --------------------------------
    NCBI_EUTILS_BASE: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    NCBI_API_KEY: str = os.getenv("NCBI_API_KEY", "")  # optional, raises rate limit
    UNIPROT_BASE: str = "https://rest.uniprot.org/uniprotkb"
    RCSB_SEARCH_BASE: str = "https://search.rcsb.org/rcsbsearch/v2/query"
    KEGG_BASE: str = "https://rest.kegg.jp"
    QUICKGO_BASE: str = "https://www.ebi.ac.uk/QuickGO/services"
    STRING_BASE: str = "https://string-db.org/api/json"

    REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "8"))

    # If a live external call fails (offline, rate-limited, blocked network),
    # BioLink falls back to small cached example records for a handful of
    # well-known genes/diseases so the UI still has something to show.
    # Set to False in production if you'd rather surface the raw error.
    USE_OFFLINE_FALLBACK: bool = os.getenv("USE_OFFLINE_FALLBACK", "true").lower() == "true"

    # --- Misc -----------------------------------------------------------
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "20"))
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")


@lru_cache
def get_settings() -> Settings:
    return Settings()
