"""In-memory conversation session store, keyed by channel-qualified chat id.

In-memory is a deliberate Phase 0 simplification: it's enough for a single
bot process handling a demo's worth of concurrent conversations, and avoids
standing up Redis before there's a real reason to. docs/proposal.md section D
already calls out Redis-backed session state as the Phase 1 upgrade, needed
once this runs as more than one worker.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.reports.schema import SecurityIncidentDraft


class Stage(str, Enum):
    AWAITING_PHOTOS = "awaiting_photos"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


@dataclass
class Session:
    stage: Stage = Stage.AWAITING_PHOTOS
    photo_paths: list[str] = field(default_factory=list)
    description: str = ""
    pending_edits: list[str] = field(default_factory=list)
    draft: Optional[SecurityIncidentDraft] = None
    # Fields the reporter fills in via the template (flow.build_template_prompt /
    # parse_template_reply) -- kept separate from the AI-derived fields
    # (category, damaged_parts, severity_level, ...) so user-stated ground
    # truth always wins over anything the AI infers.
    template_sent: bool = False
    location: Optional[str] = None
    incident_datetime: Optional[str] = None
    reported_to_authorities: bool = False


_sessions: dict[str, Session] = {}


def get_session(chat_id: str) -> Session:
    return _sessions.setdefault(chat_id, Session())


def reset_session(chat_id: str) -> Session:
    session = Session()
    _sessions[chat_id] = session
    return session
