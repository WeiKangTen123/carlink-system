from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import BOT_SERVICE_DIR, settings
from app.reports.models import Base


def _resolve_database_url(url: str) -> str:
    """Ensures relative SQLite database paths resolve consistently relative to bot-service root."""
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        path_str = url[len("sqlite:///"):]
        p = Path(path_str)
        if not p.is_absolute():
            resolved = (BOT_SERVICE_DIR / p).resolve()
            return f"sqlite:///{resolved.as_posix()}"
    return url


db_url = _resolve_database_url(settings.database_url)
_connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
engine = create_engine(db_url, connect_args=_connect_args)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if db_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
