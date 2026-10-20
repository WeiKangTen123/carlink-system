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

from sqlalchemy import Boolean, DateTime, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    # uploading -> sorting -> reading -> ready, or failed (retryable).
    stage: Mapped[str] = mapped_column(String, default="uploading")
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reporter_notes: Mapped[str] = mapped_column(String, default="")
    # Which photos the reading step actually sent to the model, so the
    # review screen can say what the facts were read from.
    read_photo_ids: Mapped[list] = mapped_column(JSON, default=list)
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
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    kind_source: Mapped[str] = mapped_column(String, default="ai")
    # Kept in the case file but never sent to the AI.
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
