"""SQLAlchemy models.

Phase 0 keeps this deliberately thin: one Report row with the validated
draft stored as JSON, rather than the fully normalized Reporter /
PersonInvolved / Witness / AuditLogEntry tables from the target data model
in docs/proposal.md section F. Normalize into those tables in Phase 1, once
there's a dashboard and multi-report querying that actually needs it.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    """ISO 8601 with an explicit UTC offset.

    SQLite hands DateTime columns back naive (no tzinfo) even though they're
    written in UTC, and a naive ISO string is read as LOCAL time by
    JavaScript: Node on the server (UTC) and a browser in Singapore (UTC+8)
    then disagreed by 8 hours -- every case time and SLA countdown shown in
    the browser was 8 hours off, and React reported hydration error #418.
    """
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


class AppSetting(Base):
    """Key/value store for the handful of settings a user can actually
    change at runtime.

    Deliberately separate from Settings in config.py: those are env-var,
    deploy-time configuration (API keys, model chain, storage paths) that
    a dashboard form has no business rewriting -- the Settings page shows
    them read-only and says so. This table is only for things that are
    genuinely user-owned, like the company name printed on PDFs.
    """
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class ApiKey(Base):
    """Operator-supplied provider API keys (currently Gemini).

    Multiple keys are supported on purpose: every model in the fallback
    chain shares one key's quota, so once that key is exhausted across all
    models, drafting stops entirely. A second key is a whole fresh set of
    quota buckets -- this project hit free-tier limits repeatedly during
    development, so this is a real operational need, not a nicety.

    The key itself is never returned by the API -- only `last4` and
    whether one exists (see the /setup/llm-keys endpoints). It's stored
    as-is rather than encrypted because any decryption key would have to
    live on the same box, so encryption here would be theatre rather than
    protection; the real control is not exposing it over HTTP.
    """
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    provider: Mapped[str] = mapped_column(String, default="gemini")
    label: Mapped[str] = mapped_column(String, default="")
    key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    type: Mapped[str] = mapped_column(String, default="security_incident")
    status: Mapped[str] = mapped_column(String, default="confirmed")
    channel: Mapped[str] = mapped_column(String, default="telegram")
    reporter_chat_id: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
    photo_paths: Mapped[list] = mapped_column(JSON, default=list)
    pdf_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class IntakeSession(Base):
    """Progress of a dashboard intake that hasn't been turned into a case yet.

    The draft itself is an ordinary Report row with status "intake" -- so it
    shows in the Cases Repository's New Intake / Draft lane like any other
    case -- and this holds only what the intake wizard needs on top: where
    background AI processing has got to, and the reporter's own words, which
    the AI rewrites into data["description"] and would otherwise be lost.
    Deleted when the intake is finalized.
    """
    __tablename__ = "intake_sessions"

    report_id: Mapped[str] = mapped_column(String, primary_key=True)
    # uploading -> sorting -> reading -> ready, or failed (retryable);
    # "finalizing" while POST /finalize turns it into a case.
    stage: Mapped[str] = mapped_column(String, default="uploading")
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reporter_notes: Mapped[str] = mapped_column(String, default="")
    # Which photos the reading step actually sent to the model, so the
    # review screen can say what the facts were read from.
    read_photo_ids: Mapped[list] = mapped_column(JSON, default=list)
    # The AI's last draft exactly as it came back, before merging into
    # Report.data. A re-read compares the form against it to tell which
    # fields the surveyor changed -- those are kept, not overwritten.
    # (Added after the table shipped: see db._ADDED_COLUMNS.)
    ai_draft: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # When the current stage began, so the wizard can show elapsed time and
    # an estimate of what's left. (Added later: see db._ADDED_COLUMNS.)
    stage_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class IntakePhoto(Base):
    """One uploaded photo of an intake draft.

    Its own row rather than an entry in Report.photo_paths because uploads
    arrive several at a time in parallel: appending to one shared JSON list
    from concurrent requests loses entries, while inserting rows doesn't.
    Moved into Report.photo_paths, in `position` order, on finalize.
    """
    __tablename__ = "intake_photos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    report_id: Mapped[str] = mapped_column(String, index=True)
    # Order chosen by the browser (files sorted by name, i.e. camera order).
    position: Mapped[int] = mapped_column(Integer, default=0)
    original_name: Mapped[str] = mapped_column(String, default="")
    path: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Set by AI sorting (ai/triage.py) or overridden by the surveyor.
    kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    quality: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    shows_damage: Mapped[bool] = mapped_column(Boolean, default=False)
    # The one damaged part the photo shows best (a taxonomy.CANONICAL_PARTS
    # name), so the reading step can spread its photo budget across parts
    # instead of across the coarse areas -- on a rear-end case every damage
    # photo is "rear". (Added after the table shipped: see db._ADDED_COLUMNS.)
    part: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    kind_source: Mapped[str] = mapped_column(String, default="ai")
    # Kept in the case file but never sent to the AI.
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class User(Base):
    """A person who can sign in to the dashboard (app/auth).

    The first admin is created on the server with
    `python -m app.auth.manage create-user`. Everyone else signs up on the
    public /signup page (username, email, password) and is in at once as a
    "user", or joins through a one-time invite link an admin creates
    (app/api/signup.py). Only an admin makes someone an admin. Users are
    disabled rather than deleted, so the case history keeps pointing at real
    accounts.

    The surveyor profile (qualifications, licence, firm) is what sign-off
    prints -- it used to be typed in, or defaulted to one fixed surveyor, so
    anyone could sign a case as anyone.
    """
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    # Always stored lower-cased (app/auth/core.normalize_email).
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    # Lower-cased, 3-30 characters (app/auth/core.username_problem); signs
    # in like the email. Unique through ux_users_username (reports/db.py),
    # which also covers databases from before this column existed.
    # (Added after the table shipped: see db._ADDED_COLUMNS.)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The full name printed on sign-offs; starts out as the username.
    name: Mapped[str] = mapped_column(String)
    # "admin": users, API keys, settings, deleting cases. "user": cases.
    # (Called "surveyor" until 2026-10-25; db._DATA_FIXES renames it.)
    role: Mapped[str] = mapped_column(String, default="user")
    password_hash: Mapped[str] = mapped_column(String)
    # Set when an admin hands out a temporary password; the API refuses
    # everything but changing it until it is changed.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    qualifications: Mapped[str] = mapped_column(String, default="")
    license_number: Mapped[str] = mapped_column(String, default="")
    firm_name: Mapped[str] = mapped_column(String, default="")
    # Wrong passwords in a row; reaching the limit sets locked_until.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class AuthSession(Base):
    """One signed-in browser. The cookie holds a random token; only its
    SHA-256 is stored, so a copy of the database (or a backup) can't be used
    to sign in as anyone."""
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    # Hard limit regardless of activity; the idle limit is checked against
    # last_seen_at (see app/auth/core.py).
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ip: Mapped[str] = mapped_column(String, default="")
    user_agent: Mapped[str] = mapped_column(String, default="")


class AuditEntry(Base):
    """Who did what to a case (or to a user account), and when.

    user_name is copied at the time, so the history still reads right after
    a user is renamed or disabled. report_id is kept after a case is deleted
    -- the record that it was deleted, and by whom, is the point.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_name: Mapped[str] = mapped_column(String, default="")
    action: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class AccountLink(Base):
    """A one-time link an admin sends someone (by WhatsApp -- there is no
    email service): "invite" creates an account, "reset" sets a new
    password on an existing one.

    Only the SHA-256 of the token is stored, like sessions. A link works
    once, expires, can be revoked, and is tied to one email; the role comes
    from this row, never from the person using the link.
    """
    __tablename__ = "account_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    purpose: Mapped[str] = mapped_column(String, default="invite")  # invite | reset
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String, default="user")
    # The name the admin typed, for the greeting on the invite page.
    name: Mapped[str] = mapped_column(String, default="")
    # The account a reset link is for.
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(String)
    created_by_name: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    used_by_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PastAnnexLine(Base):
    """One Annex A line from a past report spreadsheet (app/parts/history.py)
    -- the firm's own record of what was replaced with what, and what it
    was adjusted to. Re-importing a report replaces its lines."""
    __tablename__ = "past_annex_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_ref: Mapped[str] = mapped_column(String, index=True)  # "CL 11900"
    source_file: Mapped[str] = mapped_column(String, default="")
    plate: Mapped[str] = mapped_column(String, default="")
    make: Mapped[str] = mapped_column(String, default="", index=True)
    model: Mapped[str] = mapped_column(String, default="", index=True)
    make_model_raw: Mapped[str] = mapped_column(String, default="")
    registered: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # ISO date
    surveyed: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # ISO date
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String, default="list")  # list | nett
    item: Mapped[str] = mapped_column(String)
    # Wording-level key ("tail lamp" for "Tail lamp LH") and the canonical
    # part family the line belongs to ("Tail Lamp"), or None.
    item_key: Mapped[str] = mapped_column(String, index=True)
    family: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    condition: Mapped[str] = mapped_column(String, default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    workshop_est: Mapped[float] = mapped_column(Float, default=0.0)
    discount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adjusted_cost: Mapped[float] = mapped_column(Float, default=0.0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
