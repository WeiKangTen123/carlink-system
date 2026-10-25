from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
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
    ("intake_photos", "part", "VARCHAR"),
    ("intake_sessions", "stage_started_at", "DATETIME"),
    ("users", "username", "VARCHAR"),
]

# Indexes added after their table first shipped. A failure (e.g. existing
# duplicate rows blocking a UNIQUE index) is logged, not fatal.
_ADDED_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_intake_photos_report_sha ON intake_photos (report_id, sha256)",
    # NULLs don't collide, so accounts from before usernames are fine until
    # _backfill_usernames gives them one.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users (username)",
]

# One-off data changes, each safe to run on every start-up.
_DATA_FIXES = [
    # The non-admin role was called "surveyor" until 2026-10-25.
    "UPDATE users SET role = 'user' WHERE role = 'surveyor'",
    "UPDATE account_links SET role = 'user' WHERE role = 'surveyor'",
    # The "request access" flow it held was replaced by open signup the
    # same day; requests are personal data with no further use (PDPA).
    "DROP TABLE IF EXISTS access_requests",
]


def _upgrade_schema() -> None:
    import logging

    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)
    inspector = inspect(engine)
    # The api and telegram-bot containers (and deploy.sh's admin check)
    # start at the same moment and all run this; whichever loses a race
    # finds the work already done, so each step tolerates that.
    for table, column, sql_type in _ADDED_COLUMNS:
        if table in inspector.get_table_names():
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                    log.info("Added column %s.%s", table, column)
                except OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
    for statement in _ADDED_INDEXES:
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except Exception as exc:
            log.warning("Could not create index (%s): %s", statement, exc)
    names = set(inspect(engine).get_table_names())
    for statement in _DATA_FIXES:
        table = statement.split()[1] if statement.startswith("UPDATE") else None
        if table and table not in names:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except OperationalError as exc:
            log.warning("Could not apply data fix (%s): %s", statement, exc)
    _backfill_usernames()


def _backfill_usernames() -> None:
    """Accounts from before usernames get one made from their email
    ("weikangten@gmail.com" -> "weikangten"); they can change it on My
    account."""
    from app.auth.core import username_from_email
    from app.reports.models import User

    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        # Oldest first, so the first account with a given email name gets it
        # without a number.
        ids = [u.id for u in db.query(User).filter(User.username.is_(None)).order_by(User.created_at, User.id).all()]
        for user_id in ids:
            for _ in range(3):
                user = db.get(User, user_id)
                if user is None or user.username:
                    break  # another process just did it
                user.username = username_from_email(db, user.email)
                try:
                    db.commit()
                    break
                except IntegrityError:
                    # Another process took that name a moment ago; pick again.
                    db.rollback()
                    db.expire_all()
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _upgrade_schema()
