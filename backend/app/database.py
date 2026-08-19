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
