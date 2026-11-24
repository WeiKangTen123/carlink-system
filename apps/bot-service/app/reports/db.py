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
    # "1set" wording of a past Annex A line (models.PastAnnexLine.qty_text);
    # the table shipped with PR #9, before this column.
    ("past_annex_lines", "qty_text", "VARCHAR"),
    # What the AI last said about a case (models.Report.ai_draft, 2026-11-04).
    ("reports", "ai_draft", "JSON"),
    # Where the sorter saw a photo's damage (models.IntakePhoto.damage_box,
    # contract 8 D1).
    ("intake_photos", "damage_box", "JSON"),
    # The heartbeat of an AI run in the Telegram bot's process
    # (models.IntakeSession.run_heartbeat_at, contract 10).
    ("intake_sessions", "run_heartbeat_at", "DATETIME"),
    # The sorter's own kind of a photo a person regrouped
    # (models.IntakePhoto.ai_kind, R-batching B5).
    ("intake_photos", "ai_kind", "VARCHAR"),
    # The sort's geometry of a photo, for E7's side worked out in code
    # (models.IntakePhoto.geometry, plans/2026-11-22-enhance-staging 3c).
    ("intake_photos", "geometry", "JSON"),
    # Stage 1 (owner, 24 Nov 2026): where a past line came from and whether
    # its price was confirmed (models.PastAnnexLine.origin / confirmed);
    # filled for the rows already there by the start-up step
    # price_book_origin_v1 (init_db).
    ("past_annex_lines", "origin", "VARCHAR"),
    ("past_annex_lines", "confirmed", "BOOLEAN"),
    # When a photo was taken, by its EXIF: the camera's clock and, when the
    # camera wrote its offset, UTC (models.IntakePhoto.taken_at_local /
    # taken_at; owner decision D7, 23 Nov 2026).
    ("intake_photos", "taken_at_local", "DATETIME"),
    ("intake_photos", "taken_at", "DATETIME"),
    # The knowledge stamp of an AI run (models.AiRun.knowledge; step 2B,
    # L-library D.3): None on the rows from before.
    ("ai_runs", "knowledge", "JSON"),
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
    # Public reports (P6): the same names as models.PublicSubmission's and
    # PublicBlock's __table_args__. The reference's unique rule is inside
    # the table.
    "CREATE INDEX IF NOT EXISTS ix_public_submissions_expires_at ON public_submissions (expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_public_submissions_telegram_user_id_submitted_at"
    " ON public_submissions (telegram_user_id, submitted_at)",
    "CREATE INDEX IF NOT EXISTS ix_public_submissions_submitter_phone_submitted_at"
    " ON public_submissions (submitter_phone, submitted_at)",
    "CREATE INDEX IF NOT EXISTS ix_public_blocks_until ON public_blocks (until)",
    # The AI run log (round 3, models.AiRun): the same names create_all
    # gives report_id's index=True and the table's started_at Index.
    "CREATE INDEX IF NOT EXISTS ix_ai_runs_report_id ON ai_runs (report_id)",
    "CREATE INDEX IF NOT EXISTS ix_ai_runs_started_at ON ai_runs (started_at)",
    # The parts-catalogue look-up (models.CatalogueJob / CatalogueSuggestion):
    # the same names create_all gives them. The first is the one-open-job-
    # per-case rule.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_catalogue_jobs_open ON catalogue_jobs (report_id)"
    " WHERE status IN ('pending', 'running')",
    "CREATE INDEX IF NOT EXISTS ix_catalogue_jobs_status_created_at ON catalogue_jobs (status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_catalogue_jobs_report_id ON catalogue_jobs (report_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalogue_suggestions_report_id ON catalogue_suggestions (report_id)",
    "CREATE INDEX IF NOT EXISTS ix_catalogue_suggestions_job_id ON catalogue_suggestions (job_id)",
    # The AI accuracy record (contract 8 L, models.AiAccuracy): the same name
    # create_all gives signed_off_at's index=True.
    "CREATE INDEX IF NOT EXISTS ix_ai_accuracy_signed_off_at ON ai_accuracy (signed_off_at)",
    # "My prices" (Stage 1, models.UserPrice): one price per person, make,
    # model and line, a side-less line included -- a plain UNIQUE would let
    # NULL sides repeat. The other two are the models' own __table_args__.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_prices_key"
    " ON user_prices (user_id, make, model_key, item_key, coalesce(side, ''))",
    "CREATE INDEX IF NOT EXISTS ix_user_prices_user_id_make_model_key ON user_prices (user_id, make, model_key)",
    "CREATE INDEX IF NOT EXISTS ix_book_prices_key ON book_prices (make, model_key, item_key)",
    # The part decisions of signed-off cases (step 2B, models.PartObservation):
    # the same names as its __table_args__.
    "CREATE INDEX IF NOT EXISTS ix_part_observations_make_model_key ON part_observations (make, model_key)",
    "CREATE INDEX IF NOT EXISTS ix_part_observations_part ON part_observations (part)",
    "CREATE INDEX IF NOT EXISTS ix_part_observations_report_id ON part_observations (report_id)",
    "CREATE INDEX IF NOT EXISTS ix_part_observations_signed_off_at ON part_observations (signed_off_at)",
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
    from app.parts import history, prices
    from app.reports import owners, synthesize_annexes

    _create_tables()
    _upgrade_schema()
    # Cases from before owners get the account that filed them.
    _once("case_owners_v1", owners.backfill_case_owners)
    # The people already working here keep seeing every case, using the
    # company AI key and signing off; self-signups don't.
    _once("staff_ticks_v1", owners.grandfather_staff_ticks)
    # Annex A estimates typed per unit become the line totals the firm
    # writes (synthesize_annexes.convert_per_unit_estimates).
    _once("annex_a_line_totals_v1", synthesize_annexes.convert_per_unit_estimates)
    # Imported past lines take the families, sides and models of the rules
    # as of 2026-11-10 (the regulator, tyre and arch garnish apart; "HR-V"
    # kept whole): the price book list shows the stored columns, which an
    # import wrote under the old rules (history.refresh_lines).
    _once("past_lines_refresh_v1", history.refresh_lines)
    # Stage 1 (owner, 24 Nov 2026): each past line's origin ("import" /
    # "carlink"), imported ones confirmed; lines learned from cases before
    # this stay unconfirmed, so they never price a case (DB4).
    _once("price_book_origin_v1", prices.mark_past_line_origins)
    # And no plate in the price book (K0): blanked once, with one history
    # entry giving the count -- never a plate. Irreversible; the deploy's
    # backup has them.
    _once("price_book_plates_v1", prices.drop_price_book_plates)
