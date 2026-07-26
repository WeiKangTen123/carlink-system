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
