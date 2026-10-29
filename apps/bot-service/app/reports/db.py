import logging
import sqlite3
import time
from pathlib import Path
from sqlalchemy import create_engine, event, insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import BOT_SERVICE_DIR, settings
from app.reports.models import AppSetting, Base

log = logging.getLogger(__name__)


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
        cursor.execute("PRAGMA busy_timeout=5000")
        for attempt in range(20):
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                # A brand-new database file that another process (the api,
                # the bot, deploy.sh's admin check) is switching to WAL this
                # moment: that fails at once rather than waiting. The mode is
                # kept in the file, so this connection gets it anyway.
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.1)
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
    # Case owners and the staff ticks (app/auth/permissions.py).
    ("reports", "owner_id", "VARCHAR"),
    ("users", "sees_all_cases", "BOOLEAN"),
    ("users", "ai_company_key_allowed", "BOOLEAN"),
    ("users", "may_sign_off", "BOOLEAN"),
    # Why an intake run stopped, when the person's AI wasn't available (P4).
    ("intake_sessions", "error_code", "VARCHAR"),
    ("intake_sessions", "error_by", "VARCHAR"),
]

# Indexes added after their table first shipped. A failure (e.g. existing
# duplicate rows blocking a UNIQUE index) is logged, not fatal. Also checked
# for tables create_all made: it adds each index in a statement of its own,
# so a process stopped between the two would leave one out.
_ADDED_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_intake_photos_report_sha ON intake_photos (report_id, sha256)",
    # NULLs don't collide, so accounts from before usernames are fine until
    # _backfill_usernames gives them one.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users (username)",
    # Same name create_all gives Report.owner_id's index=True.
    "CREATE INDEX IF NOT EXISTS ix_reports_owner_id ON reports (owner_id)",
    # Telegram link codes (P5): create_all adds this one separately. The
    # unique rules of both chat tables are inside the tables themselves.
    "CREATE INDEX IF NOT EXISTS ix_chat_link_codes_user_id ON chat_link_codes (user_id)",
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
    from sqlalchemy import inspect, text

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


def _create_tables() -> None:
    """create_all, which checks which tables are missing and then creates
    them -- so when the api, the bot and deploy.sh's admin check start at
    once, two can both find a new table missing and the slower one fails
    with "table ... already exists". Another try then finds it there. Each
    such failure means another process made a table, so on a brand-new
    database there can be one per table (3 tries wasn't enough in tests)."""
    attempts = len(Base.metadata.tables) + 1
    for attempt in range(attempts):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError as exc:
            if "already exists" not in str(exc).lower() or attempt == attempts - 1:
                raise
            log.info("Another process created a table at the same moment; checking again")


def _once(name: str, step) -> None:
    """Runs step(db) a single time over the life of the database, for
    start-up changes that must not be repeated -- the case-owner backfill
    run again would undo an admin who has since made a case Unassigned.

    The done mark (app_settings "upgrade:{name}") is written FIRST, as the
    first statement of the transaction: that takes SQLite's write lock, so a
    second process starting at the same moment waits, then finds the mark
    (IntegrityError) and skips. Nothing may be read before it in this
    session, or SQLite (WAL) can refuse to turn the read into a write. The
    step runs in the same transaction, so if it fails the mark goes too and
    the next start-up tries again. "database is locked" (another process
    held the lock past busy_timeout) means skip and try at the next start.
    """
    key = f"upgrade:{name}"
    db = SessionLocal()
    try:
        try:
            db.execute(insert(AppSetting).values(key=key, value="done"))
        except IntegrityError:
            db.rollback()
            return  # done already, or just now by another process
        except OperationalError as exc:
            db.rollback()
            if "locked" not in str(exc).lower():
                raise
            log.warning("Skipped start-up step %s (database busy); it runs at the next start", name)
            return
        try:
            result = step(db)
            db.commit()
            log.info("Start-up step %s done (%s)", name, result)
        except Exception:
            db.rollback()
            log.exception("Start-up step %s failed; it runs again at the next start", name)
    finally:
        db.close()


def init_db() -> None:
    from app.reports import owners

    _create_tables()
    _upgrade_schema()
    # Cases from before owners get the account that filed them.
    _once("case_owners_v1", owners.backfill_case_owners)
    # The people already working here keep seeing every case, using the
    # company AI key and signing off; self-signups don't.
    _once("staff_ticks_v1", owners.grandfather_staff_ticks)
