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


# create_all only creates missing TABLES; it never adds a column to a table
# that already exists, and this project has no migration tool. Columns added
# to an existing model after it first shipped are listed here as
# (table, column, SQL type) and added on startup if absent. Nullable only.
_ADDED_COLUMNS = [
    ("intake_sessions", "ai_draft", "JSON"),
]

# Indexes added after their table first shipped. A failure (e.g. existing
# duplicate rows blocking a UNIQUE index) is logged, not fatal.
_ADDED_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_intake_photos_report_sha ON intake_photos (report_id, sha256)",
]


def _upgrade_schema() -> None:
    import logging

    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            if table in inspector.get_table_names():
                existing = {c["name"] for c in inspector.get_columns(table)}
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                    log.info("Added column %s.%s", table, column)
    for statement in _ADDED_INDEXES:
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except Exception as exc:
            log.warning("Could not create index (%s): %s", statement, exc)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _upgrade_schema()
