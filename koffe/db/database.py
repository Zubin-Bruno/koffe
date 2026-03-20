import os
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from koffe.db.models import Base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/koffe.db")

# Ensure the data directory exists
db_path = DATABASE_URL.replace("sqlite:///", "")
Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # needed for SQLite + FastAPI
)


def _strip_accents(text: str | None) -> str | None:
    """Remove accent marks from text (e.g. 'Azúcar' → 'Azucar').

    Uses Unicode decomposition: 'ú' becomes 'u' + combining-acute-accent,
    then we drop the combining mark, leaving plain 'u'.
    """
    if text is None:
        return None
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")


@event.listens_for(engine, "connect")
def _register_sqlite_functions(dbapi_conn, connection_record):
    """Register custom SQL functions on every new SQLite connection."""
    dbapi_conn.create_function("strip_accents", 1, _strip_accents)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def create_tables() -> None:
    Base.metadata.create_all(engine)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
