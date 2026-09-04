"""SQLAlchemy models.

Phase 0 keeps this deliberately thin: one Report row with the validated
draft stored as JSON, rather than the fully normalized Reporter /
PersonInvolved / Witness / AuditLogEntry tables from the target data model
in docs/proposal.md section F. Normalize into those tables in Phase 1, once
there's a dashboard and multi-report querying that actually needs it.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
