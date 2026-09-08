"""
SQLAlchemy engine/session wiring for PostgreSQL.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_settings

settings = get_settings()

# pool_pre_ping avoids "server closed the connection unexpectedly" errors
# after the DB has been idle (common on free-tier Postgres hosts).
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Called once on app startup."""
    from . import models  # noqa: F401  (ensures models are registered)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# create_all() only creates tables that don't exist yet - it never alters an
# existing table, so a new nullable column added to a model (e.g. Paper's
# source_type/pubmed_id/pubmed_url) needs to be added by hand on a database
# that already has the papers table. There's no migration framework in this
# project, so for simple additive nullable columns this just adds whichever
# ones are missing on startup - safe since every column added this way
# defaults to NULL and nothing before it referenced them.
_ADDITIVE_COLUMNS = {
    "papers": {
        "source_type": "VARCHAR(32)",
        "pubmed_id": "VARCHAR(32)",
        "pubmed_url": "VARCHAR(512)",
    },
}


def _add_missing_columns():
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    for table, columns in _ADDITIVE_COLUMNS.items():
        if table not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        with engine.begin() as conn:
            for name, coltype in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))
