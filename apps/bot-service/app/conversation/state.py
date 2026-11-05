"""In-memory conversation session store, keyed by channel-qualified chat id.

In-memory is a deliberate Phase 0 simplification: it's enough for a single
bot process handling a demo's worth of concurrent conversations, and avoids
standing up Redis before there's a real reason to. docs/proposal.md section D
already calls out Redis-backed session state as the Phase 1 upgrade, needed
once this runs as more than one worker.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from app.reports.schema import SecurityIncidentDraft


class Stage(str, Enum):
    AWAITING_PHOTOS = "awaiting_photos"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


class PublicStep(str, Enum):
    """Where a public report form is (channels/telegram_public.py): steps
    1-6, then the review and its edit menu."""
    NAME = "name"
    ROLE = "role"
    PHONE = "phone"
    PLATE = "plate"
    WHEN_WHERE = "when_where"
    WHAT = "what"
    PHOTOS = "photos"
    REVIEW = "review"
    EDIT_MENU = "edit_menu"


@dataclass
class PublicForm:
    """A report from someone with no Carlink account, being filled in. Made
    at consent (nothing is kept before it), in memory only: its photos are
    the session's photo_paths (tg-<uuid> temp files), and nothing goes into
    the database until Submit (intake/public.save_submission).

    nonce ties a button tap to this form (pub:<nonce>:<action>[:rev]); rev
    goes up with every change, so a Submit on an older review shows the
    review again instead of sending."""
    nonce: str
    consent_at: datetime
    step: PublicStep = PublicStep.NAME
    rev: int = 0
    name: Optional[str] = None
    role: Optional[str] = None  # a key of intake/public.ROLES
    phone: Optional[str] = None  # E.164, from a contact shared from this Telegram
    plate: Optional[str] = None
    plate_unknown: bool = False
    when_where: Optional[str] = None
    statement: str = ""
    # Telegram file_unique_ids of the kept photos (a repeat isn't fetched
    # again), and their bytes together (MAX_REPORT_BYTES, and the bot's cap
    # on all public temp files).
    photo_ids: list[str] = field(default_factory=list)
    photo_bytes: int = 0
    photo_count_sent: int = 0  # photos sent after consent, kept or not
    forwarded_count: int = 0
    editing: bool = False  # back to the review once this step is answered
    submitting: bool = False  # claimed by the first Submit tap
    # The album's "Got N photos" reply, sent 2 s after the last photo, and
    # what to add to it (a photo refused for its size, the cap...).
    ack_task: Any = None
    ack_notes: list[str] = field(default_factory=list)


@dataclass
class Session:
    stage: Stage = Stage.AWAITING_PHOTOS
    photo_paths: list[str] = field(default_factory=list)
    description: str = ""
    pending_edits: list[str] = field(default_factory=list)
    draft: Optional[SecurityIncidentDraft] = None
    # The AI's own reading behind `draft` (flow.DraftResult.ai_draft):
    # filed as Report.ai_draft, never the merged draft.
    ai_draft: Optional[dict] = None
    # Fields the reporter fills in via the template (flow.build_template_prompt /
    # parse_template_reply) -- kept separate from the AI-derived fields
    # (category, damaged_parts, severity_level, ...) so user-stated ground
    # truth always wins over anything the AI infers.
    template_sent: bool = False
    location: Optional[str] = None
    incident_datetime: Optional[str] = None
    vehicle_plate: Optional[str] = None
    damaged_side: Optional[str] = None
    insurer_name: Optional[str] = None
    workshop_name: Optional[str] = None
    vehicle_body_type: Optional[str] = None
    # The cover page's facts the template asks (2026-11-04): who asked for
    # the report and the type of claim. The reporter's own name, role and
    # contact and "reported to authorities" are no longer asked: the
    # report never printed them.
    claim_type: Optional[str] = None
    requested_by: Optional[str] = None
    clarifying_field: Optional[str] = None
    # Telegram (channels/telegram.py): set while a Confirm is being saved, so
    # a second tap saves nothing; and when the chat was last active, so a
    # draft left for over a day expires instead of being confirmed after the
    # api's cleanup has deleted its photos.
    finalizing: bool = False
    last_active: float = field(default_factory=time.monotonic)
    # Telegram: the Carlink account the draft is being filed as. A draft
    # started for one account is thrown away, not filed, once the Telegram
    # is connected to another.
    carlink_user_id: Optional[str] = None
    # Telegram, someone with no account (P6): their public report form,
    # from consent until Submit, cancel or 24 hours idle. Its photos are
    # photo_paths above; it never has a staff draft beside it.
    public: Optional[PublicForm] = None


_sessions: dict[str, Session] = {}


def get_session(chat_id: str) -> Session:
    return _sessions.setdefault(chat_id, Session())


def find_session(chat_id: str) -> Optional[Session]:
    """The chat's session if it has one, without starting one."""
    return _sessions.get(chat_id)


def reset_session(chat_id: str) -> Session:
    session = Session()
    _sessions[chat_id] = session
    return session


def drop_session(chat_id: str) -> Optional[Session]:
    """Forgets the chat's session, leaving none behind (Telegram, for a chat
    that isn't connected to an account). Returns it, for its temp files."""
    return _sessions.pop(chat_id, None)


def public_sessions() -> list[tuple[str, Session]]:
    """(chat id, session) of every chat with a public report form open
    (Telegram: the bot's caps on open forms and their temp files)."""
    return [(chat_id, s) for chat_id, s in list(_sessions.items()) if s.public is not None]
