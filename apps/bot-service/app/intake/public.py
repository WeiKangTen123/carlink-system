"""Public reports through the Telegram bot (P6): the one module that changes
them. Both processes use it on the one SQLite database -- the bot
(channels/telegram_public.py) checks, saves and withdraws; the api
(api/inbox.py, api/intake.py, intake/cleanup.py) takes, assigns,
releases, discards and expires -- so each rule lives here once.

A public report is an ordinary intake draft: a Report (channel
PUBLIC_CHANNEL, status "intake", no owner, reporter_chat_id "public") with
its IntakeSession, IntakePhoto rows and files in intake/{id}/, plus one
PublicSubmission row: who sent it and what they gave. Its state is never
stored; it follows from the report:

    new      status "intake", no owner     the inbox: nobody may change it
    taken    status "intake", an owner     that person's draft, like any other
    filed    any other status              a case; telegram_* cleared

Every change of state is ONE conditional statement on reports, settled by
SQLite alone, so two processes, two tabs or the sweep can't both win:

    take, assign   UPDATE reports SET owner_id=:x WHERE ... AND owner_id IS NULL
    release        UPDATE reports SET owner_id=NULL WHERE ... AND owner_id=:seen
    discard, withdraw, expiry
                   claim the stage (UPDATE intake_sessions SET stage='deleting'
                   WHERE stage NOT IN BUSY_STAGES), then DELETE FROM reports
                   WHERE ... AND owner_id IS :seen; if either changes no row,
                   both are rolled back

The first write of a transaction takes SQLite's write lock (even one that
matches no row), so whatever is checked after it in the same transaction
-- the caps, the draft limit, the account still active -- can't change
before the commit.

Nothing here commits unless its docstring says so: the caller commits, and
after a delete removes the folder (photos.remove_intake_dir). A refusal
rolls back what the call did, then raises (InboxError: an ApiError the API
sends as {detail, code}; PublicRefused for the bot) or returns False.

Personal data: the sender's name, phone and words are kept only in
PublicSubmission and the draft's Report.data.reporter_* -- never in a
history entry or a log line, which say the reference (TG-...) and the
report id. The AI only ever sees IntakeSession.reporter_notes
(reporter_notes_for: no name, phone, plate or reference) and the photos.

Never imports the pipeline, the AI, rendering or telegram.
"""
import hashlib
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.api.limits import MAX_OPEN_DRAFTS_PER_PERSON
from app.auth.chat_links import CHANNEL as TELEGRAM_LINKS, CODE_ALPHABET, clean_name
from app.auth.core import as_utc, audit, utc_now
from app.auth.deps import ApiError
from app.auth.permissions import PUBLIC_CHANNEL
from app.intake.photos import intake_dir, is_readable_image, remove_intake_dir
from app.reports.db import SessionLocal
from app.reports.models import (
    AppSetting,
    ChatLink,
    IntakePhoto,
    IntakeSession,
    PublicBlock,
    PublicSubmission,
    Report,
    User,
    gen_id,
    iso_utc,
)
from app.reports.owners import _vetted, assigned_detail, owner_dict, owners_by_id
from app.reports.schema import SecurityIncidentDraft, VehicleInfo
from app.storage.files import (
    disk_space_low,
    generate_preview,
    generate_thumbnail,
    preview_path_for,
    thumbnail_path_for,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------- the rules

# Untaken (and released) reports are deleted this long after they were
# sent: the date promised at consent (PublicSubmission.expires_at).
UNTAKEN_MAX_AGE = timedelta(days=30)
# Untaken reports in the inbox, from everyone.
MAX_OPEN = 50
# Reports sent in any hour, from everyone.
MAX_PER_HOUR = 20
# Per Telegram account and per phone: reports not yet filed, and reports
# sent in a rolling 24 hours.
MAX_OPEN_PER_SENDER = 3
MAX_PER_SENDER_DAY = 3
# Telegram photos (never files): per report, each, all together, pixels.
MAX_PHOTOS = 20
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_REPORT_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 40_000_000
# Public reports stop this many GB before staff work does
# (storage/files.disk_space_low(extra_gb)).
DISK_HEADROOM_GB = 2
# A sender whose report was discarded as spam (PublicBlock).
BLOCK_FOR = timedelta(days=90)
# The privacy wording the person agreed to (the bot's consent text).
CONSENT_VERSION = "2026-10-30"

# What the person picks; the label goes into the draft's reporter_role.
ROLES = {"driver": "Driver", "owner": "Car owner", "workshop": "Workshop", "other": "Someone else"}
_ROLE_WORDS = {"driver": "the car's driver", "owner": "the car's owner", "workshop": "a workshop", "other": "someone else"}
# Discard's reasons, as the history shows them.
DISCARD_REASONS = {
    "spam": "Spam",
    "duplicate": "Duplicate",
    "test": "Test",
    "not_vehicle": "Not a vehicle report",
    "other": "Other",
}
MAX_NAME = 80
MAX_WHEN_WHERE = 300
MAX_STATEMENT = 2000
MAX_DISCARD_NOTE = 120

# Stages in which the AI or finalize holds a draft: nothing deletes it then.
# RUNNING_STAGES is pipeline.RUNNING_STAGES (not imported: see the top).
RUNNING_STAGES = ("sorting", "reading")
BUSY_STAGES = ("finalizing", *RUNNING_STAGES)

SG_TIME = timezone(timedelta(hours=8))
_HOUR = timedelta(hours=1)
_DAY = timedelta(hours=24)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# History wording ("created" and the rest say the reference after " · ").
CREATED_DETAIL = "Sent by the public through Telegram"
TAKEN_DETAIL = "Taken from the public inbox"
EXPIRED_DETAIL = f"Not taken within {UNTAKEN_MAX_AGE.days} days"
WITHDRAWN_DETAIL = "Withdrawn by the sender"
WITHDRAW_ASKED_DETAIL = "The sender asked to withdraw it"
# Left in a released draft whose AI run a restart stopped (release()).
STALE_RUN = "Processing stopped before finishing. Press Retry to continue."

# ------------------------------------------------------- admin settings
# app_settings rows (models.AppSetting) admins edit on the Company tab (PUT
# /settings, admin only); GET /settings shows them only to admins. The bot
# reads them from the database.
PUBLIC_CONTACT_PHONE = "public_contact_phone"  # "6123 4567"; empty: no "call us" line
DPO_CONTACT = "dpo_contact"  # "dpo@firm.sg"; empty: the Company tab warns
COMPANY_KEY_BILLED = "company_key_billed"  # "true" | "false" (or unset: false)
SETTING_KEYS = (PUBLIC_CONTACT_PHONE, DPO_CONTACT, COMPANY_KEY_BILLED)
MAX_SETTING_TEXT = 200


# ------------------------------------------------------------- refusals


class PublicRefused(Exception):
    """Why the bot can't take a report from this person now (check_can_accept,
    save_submission). code, never anything personal:

        inbox_full    MAX_OPEN untaken reports already
        hourly_full   MAX_PER_HOUR sent in the last hour, from everyone
        sender_limit  this Telegram account: MAX_OPEN_PER_SENDER open, or
                      MAX_PER_SENDER_DAY in 24 hours
        phone_limit   the same for this phone number
        linked        this Telegram is connected to a Carlink account (staff
                      use the staff flow)
        blocked       discarded as spam lately (PublicBlock)
        disk_low      under min_free_disk_gb + DISK_HEADROOM_GB free
    """

    CODES = ("inbox_full", "hourly_full", "sender_limit", "phone_limit", "linked", "blocked", "disk_low")

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class InboxError(ApiError):
    """A refusal of take, assign, release or discard: the API's 409 (404 for
    inbox_gone) with a code the dashboard explains, sent as is by main.py's
    ApiError handler."""


GONE = "This public report isn't here any more: it was withdrawn, discarded or deleted after 30 days."
FILED = "This public report has already been filed as a case."
CHANGED = "This report changed a moment ago. Reload it and try again."
FINALIZING = "This report is being filed as a case right now."
AI_RUNNING = "Wait for the AI to finish reading this report first."
TAKEN = "Taken by {name} just now."
TAKEN_UNKNOWN = "Someone else has just taken this report."
OWNER_INACTIVE = "That account is disabled. Choose someone else."
OWNER_UNVETTED = "Public reports can only go to an account an admin has checked: an admin, or one with a staff tick."
DRAFT_LIMIT = f"You already have {MAX_OPEN_DRAFTS_PER_PERSON} unfinished drafts. Finish or delete one first."
DRAFT_LIMIT_OTHER = "{name} already has " + f"{MAX_OPEN_DRAFTS_PER_PERSON} unfinished drafts."


def refusal(db: Session, report_id: str, *, taking: bool = False) -> InboxError:
    """Why a conditional change found nothing to change, worked out after
    it (reads only): 404 inbox_gone (deleted, or not a public report), 409
    inbox_filed, inbox_finalizing, inbox_taken (taking: someone has it, "Taken
    by S. Lee just now"), ai_running, else inbox_changed. For the API after
    delete_submission returns False, too."""
    row = db.query(Report.channel, Report.status, Report.owner_id).filter(Report.id == report_id).first()
    if row is None or row.channel != PUBLIC_CHANNEL:
        return InboxError(404, GONE, "inbox_gone")
    if row.status != "intake":
        return InboxError(409, FILED, "inbox_filed")
    stage = db.query(IntakeSession.stage).filter(IntakeSession.report_id == report_id).scalar()
    if stage == "finalizing":
        return InboxError(409, FINALIZING, "inbox_finalizing")
    if taking and row.owner_id is not None:
        owner = db.query(User.name, User.username).filter(User.id == row.owner_id).first()
        who = (owner.name or owner.username) if owner else None
        return InboxError(409, TAKEN.format(name=who) if who else TAKEN_UNKNOWN, "inbox_taken")
    if stage in RUNNING_STAGES:
        return InboxError(409, AI_RUNNING, "ai_running")
    return InboxError(409, CHANGED, "inbox_changed")


# --------------------------------------------------------------- clauses


def untaken_clause():
    """SQL for a report in the inbox (permissions.in_inbox). A NULL channel
    or status never equals, so a legacy row is never taken for one."""
    return and_(Report.channel == PUBLIC_CHANNEL, Report.status == "intake", Report.owner_id.is_(None))


def not_untaken_clause():
    """SQL for every report except those in the inbox: the Cases lists,
    analytics and the shared draft cap. Not NOT(untaken_clause()): with a
    NULL channel or status that is NULL, and the row would drop out of
    every list, so each part says so itself."""
    return or_(
        Report.channel.is_(None),
        Report.channel != PUBLIC_CHANNEL,
        Report.status.is_(None),
        Report.status != "intake",
        Report.owner_id.isnot(None),
    )


def state_of(report) -> str:
    """new | taken | filed (the top of this file)."""
    if report.status != "intake":
        return "filed"
    return "new" if report.owner_id is None else "taken"


# -------------------------------------------------------------- reference

_REF = re.compile(rf"TG-\d{{6}}-[{CODE_ALPHABET}]{{4}}")


def _sg(now: datetime) -> datetime:
    return as_utc(now).astimezone(SG_TIME)


def is_reference(text) -> bool:
    return isinstance(text, str) and _REF.fullmatch(text) is not None


def new_reference(db: Session, now: datetime | None = None) -> str:
    """TG-YYMMDD-XXXX: the Singapore date, then 4 characters from
    chat_links.CODE_ALPHABET (no 0/O, 1/I or L). Never mistaken for a
    link code (XXXX-XXXX from the alphabet): any 8 characters of it hold the
    month, which always has a 0 or a 1, and the alphabet has neither.
    Drawn again on a clash with a report still here; the unique rule backs
    that up."""
    day = _sg(now or utc_now()).strftime("%y%m%d")
    for _ in range(100):
        ref = f"TG-{day}-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        if db.query(PublicSubmission.report_id).filter(PublicSubmission.reference == ref).first() is None:
            return ref
    raise RuntimeError("Couldn't find a free public report reference")


# ------------------------------------------------------ phones and plates

_E164 = re.compile(r"\+[1-9]\d{6,14}")
_PHONE_JUNK = re.compile(r"[\s\-().]")
_PLATE = re.compile(r"[A-Z0-9]{2,12}")
_PLATE_SG = re.compile(r"[A-Z]{1,3}\d{1,4}[A-Z]?")


def normalize_phone(raw) -> str | None:
    """A Telegram contact's phone_number as E.164 ("+6591234567"), or None.
    Telegram sends the digits with or without "+"."""
    if not isinstance(raw, str):
        return None
    text = _PHONE_JUNK.sub("", raw.strip())
    if not text.startswith("+"):
        text = "+" + text
    return text if _E164.fullmatch(text) else None


def is_anonymous_number(e164: str) -> bool:
    """A +888 Fragment number: no one can call it back."""
    return (e164 or "").startswith("+888")


def _sg_number(e164: str) -> bool:
    return (e164 or "").startswith("+65") and len(e164) == 11


def phone_kind(e164: str) -> str:
    """sg_mobile (+65 8/9...), sg_other (+65, a landline or other), foreign."""
    if _sg_number(e164):
        return "sg_mobile" if e164[3] in "89" else "sg_other"
    return "sg_other" if (e164 or "").startswith("+65") else "foreign"


def phone_display(e164: str) -> str:
    """+65 9123 4567 for a Singapore number, else E.164 as it is."""
    if _sg_number(e164):
        return f"+65 {e164[3:7]} {e164[7:]}"
    return e164 or ""


def mask_phone(e164: str) -> str:
    """The inbox list's: +65 •••• 4567; another country's keeps its first
    two digits, +44 •••• 1180."""
    if not e164:
        return ""
    head = "+65" if e164.startswith("+65") else e164[:3]
    return f"{head} •••• {e164[-4:]}"


def phone_links(e164: str) -> dict:
    """{"call": "tel:+6591234567", "whatsapp": "https://wa.me/6591234567"}."""
    digits = re.sub(r"\D", "", e164 or "")
    return {"call": f"tel:+{digits}", "whatsapp": f"https://wa.me/{digits}"}


def clean_plate(text) -> str | None:
    """A typed plate as stored: upper case, spaces and dashes out, 2-12
    letters and digits. None if it isn't one."""
    if not isinstance(text, str):
        return None
    plate = re.sub(r"[\s\-]", "", unicodedata.normalize("NFKC", text)).upper()
    return plate if _PLATE.fullmatch(plate) else None


def plate_looks_sg(plate: str | None) -> bool:
    """Shaped like a Singapore plate (SBA1234A); kept as a flag only."""
    return bool(plate) and _PLATE_SG.fullmatch(plate) is not None


def _clip(text, limit: int) -> str:
    """Their words as given, minus control characters (newlines and tabs
    kept), at most limit characters."""
    if not isinstance(text, str):
        return ""
    kept = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    return kept.strip()[:limit].strip()


# ------------------------------------------------------- settings (admin)


def _setting(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return (row.value or "").strip() if row else ""


def public_contact_phone(db: Session) -> str:
    """The public line the bot gives ("call us on ..."); "" when not set,
    and then the bot leaves every such clause out."""
    return _setting(db, PUBLIC_CONTACT_PHONE)


def dpo_contact(db: Session) -> str:
    """The data-protection contact /privacy shows; "" when not set."""
    return _setting(db, DPO_CONTACT)


def company_key_billed(db: Session) -> bool:
    """Whether an admin has confirmed the company AI key's Google Cloud
    project has billing on. Until then a public report's AI uses only the
    presser's own billed key (ai/keys.resolve_keys)."""
    return _setting(db, COMPANY_KEY_BILLED) == "true"


def settings_dict(db: Session) -> dict:
    """{public_contact_phone, dpo_contact, company_key_billed (bool)}, for
    GET/PUT /settings (admins only)."""
    return {
        PUBLIC_CONTACT_PHONE: public_contact_phone(db),
        DPO_CONTACT: dpo_contact(db),
        COMPANY_KEY_BILLED: company_key_billed(db),
    }


def save_setting(db: Session, key: str, value) -> None:
    """Stores one of SETTING_KEYS (the caller commits): company_key_billed
    as "true"/"false", the others as one line of at most MAX_SETTING_TEXT
    characters. ValueError for any other key."""
    if key not in SETTING_KEYS:
        raise ValueError(f"not a public report setting: {key}")
    if key == COMPANY_KEY_BILLED:
        stored = "true" if value is True or str(value).strip().lower() == "true" else "false"
    else:
        stored = " ".join(_clip(value if isinstance(value, str) else "", MAX_SETTING_TEXT).split())
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=stored))
    else:
        row.value = stored


# ---------------------------------------------------------------- blocks


def is_blocked(db: Session, tg_id, now: datetime | None = None) -> bool:
    now = now or utc_now()
    return (
        db.query(PublicBlock.telegram_user_id)
        .filter(PublicBlock.telegram_user_id == str(tg_id), PublicBlock.until > now)
        .first()
        is not None
    )


def block_sender(db: Session, tg_id, *, actor=None, reference: str | None = None, now: datetime | None = None) -> None:
    """No reports from this Telegram account for BLOCK_FOR from now (again,
    if it was blocked already). The caller commits; delete_submission(...,
    block=True) calls it for a spam discard."""
    now = now or utc_now()
    values = {
        "blocked_at": now,
        "until": now + BLOCK_FOR,
        "blocked_by": getattr(actor, "id", None),
        "reference": reference,
    }
    db.execute(
        sqlite_insert(PublicBlock)
        .values(telegram_user_id=str(tg_id), **values)
        .on_conflict_do_update(index_elements=[PublicBlock.telegram_user_id], set_=values)
    )


def sweep_blocks(db: Session, now: datetime | None = None) -> int:
    """Deletes blocks that have run out (intake/cleanup.py; the caller
    commits). Returns how many."""
    now = now or utc_now()
    result = db.execute(delete(PublicBlock).where(PublicBlock.until <= now).execution_options(synchronize_session=False))
    return result.rowcount or 0


# ------------------------------------------------------------ the caps


def open_count(db: Session) -> int:
    """Untaken reports in the inbox (MAX_OPEN)."""
    return (
        db.query(func.count(PublicSubmission.report_id))
        .join(Report, Report.id == PublicSubmission.report_id)
        .filter(untaken_clause())
        .scalar()
    ) or 0


def _linked(db: Session, tg: str) -> bool:
    """chat_links.linked_user's answer (an active account), without its
    last_used_at write."""
    return (
        db.query(ChatLink.id)
        .join(User, User.id == ChatLink.user_id)
        .filter(ChatLink.channel == TELEGRAM_LINKS, ChatLink.external_id == tg, User.active.is_(True))
        .first()
        is not None
    )


def _over(db: Session, who, open_cap: int, day_cap: int, now: datetime) -> bool:
    """One sender's (or phone's) reports: not yet filed, or sent in 24 hours."""
    open_now = (
        db.query(func.count(PublicSubmission.report_id))
        .join(Report, Report.id == PublicSubmission.report_id)
        .filter(who, Report.status == "intake")
        .scalar()
    )
    if open_now >= open_cap:
        return True
    today = db.query(func.count(PublicSubmission.report_id)).filter(who, PublicSubmission.submitted_at > now - _DAY).scalar()
    return today >= day_cap


def _refusal_code(db: Session, tg: str, phone: str | None, now: datetime) -> str | None:
    if is_blocked(db, tg, now):
        return "blocked"
    if _linked(db, tg):
        return "linked"
    if open_count(db) >= MAX_OPEN:
        return "inbox_full"
    sent = db.query(func.count(PublicSubmission.report_id)).filter(PublicSubmission.submitted_at > now - _HOUR).scalar()
    if sent >= MAX_PER_HOUR:
        return "hourly_full"
    if _over(db, PublicSubmission.telegram_user_id == tg, MAX_OPEN_PER_SENDER, MAX_PER_SENDER_DAY, now):
        return "sender_limit"
    if phone and _over(db, PublicSubmission.submitter_phone == phone, MAX_OPEN_PER_SENDER, MAX_PER_SENDER_DAY, now):
        return "phone_limit"
    return None


def check_can_accept(db: Session, tg_id, phone: str | None = None, now: datetime | None = None) -> None:
    """Raises PublicRefused if this Telegram account (and, once shared, this
    phone, E.164) can't send a report now: at consent, after the contact is
    shared, and again inside save_submission's transaction, which is the
    check that counts. Reads only. The bot adds its own in-memory limits
    (which also remember withdrawn reports)."""
    now = now or utc_now()
    if disk_space_low(DISK_HEADROOM_GB):
        raise PublicRefused("disk_low")
    code = _refusal_code(db, str(tg_id), phone, now)
    if code:
        raise PublicRefused(code)


# ------------------------------------------------------------ submitting


@dataclass
class Form:
    """What save_submission needs from the bot's finished form (any object
    with these attributes will do). Cleaned again on saving.

    phone: E.164 from a contact the person shared from this Telegram
    account. plate: None for "Not sure". when_where: their text, or
    "Location pin: lat, lon". photo_count_sent: photos they sent, kept or
    not; forwarded_count: forwarded messages among what they sent."""
    telegram_user_id: str
    name: str
    role: str
    phone: str
    statement: str
    consent_at: datetime
    plate: str | None = None
    when_where: str | None = None
    telegram_username: str | None = None
    telegram_name: str | None = None
    consent_version: str = CONSENT_VERSION
    photo_count_sent: int = 0
    forwarded_count: int = 0


def _when_text(now: datetime) -> str:
    """30 Oct 2026, 3:12 pm (Singapore time)."""
    t = _sg(now)
    return f"{t.day} {_MONTHS[t.month - 1]} {t.year}, {t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def reporter_notes_for(form, now: datetime | None = None) -> str:
    """The draft's IntakeSession.reporter_notes: what the AI reads. A header,
    who they are, when and where, and what happened -- never the name,
    phone, plate or reference (those go in Report.data, where the AI's
    redraft keeps them)."""
    when_where = _clip(getattr(form, "when_where", None), MAX_WHEN_WHERE) or "not given"
    return "\n".join((
        f"Sent by a member of the public through Telegram on {_when_text(now or utc_now())} (Singapore time). "
        "Their own words, not checked.",
        f"Sent by: {_ROLE_WORDS.get(getattr(form, 'role', None), 'someone else')}",
        f"When and where: {when_where}",
        f"What happened: {_clip(getattr(form, 'statement', None), MAX_STATEMENT)}",
    ))


def _cleaned(form) -> dict:
    """The form's values as stored. ValueError if one that must be there
    isn't (the bot's steps make sure of them)."""
    tg = str(getattr(form, "telegram_user_id", "") or "").strip()
    name = clean_name(getattr(form, "name", None), MAX_NAME)
    role = getattr(form, "role", None)
    phone = normalize_phone(getattr(form, "phone", None))
    statement = _clip(getattr(form, "statement", None), MAX_STATEMENT)
    if not (tg and name and role in ROLES and phone and statement):
        raise ValueError("A public report needs the Telegram id, a name, a role, a phone and what happened.")
    plate = clean_plate(getattr(form, "plate", None))
    return {
        "tg": tg,
        "name": name,
        "role": role,
        "phone": phone,
        "statement": statement,
        "plate": plate,
        "plate_looks_sg": plate_looks_sg(plate),
        "when_where": _clip(getattr(form, "when_where", None), MAX_WHEN_WHERE) or None,
        "telegram_username": clean_name(getattr(form, "telegram_username", None), 64),
        "telegram_name": clean_name(getattr(form, "telegram_name", None), 128),
        "consent_at": as_utc(getattr(form, "consent_at", None)),
        "consent_version": getattr(form, "consent_version", None) or CONSENT_VERSION,
        "photo_count_sent": max(int(getattr(form, "photo_count_sent", 0) or 0), 0),
        "forwarded_count": max(int(getattr(form, "forwarded_count", 0) or 0), 0),
    }


# Telegram photos are JPEG; nothing else Pillow could open is kept (no HEIC:
# libheif stays out of the bot process, review H4).
_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def _image_ext(path) -> str | None:
    """The file's extension by what it is (Pillow, the header only), or None
    if it isn't one of _FORMATS or is over MAX_PIXELS."""
    try:
        from PIL import Image

        with Image.open(path, formats=list(_FORMATS)) as im:
            width, height = im.size
            found = im.format
    except Exception:
        return None
    if found not in _FORMATS or width * height > MAX_PIXELS:
        return None
    return _FORMATS[found]


def _copy_hashing(src: Path, dest: Path) -> str:
    digest = hashlib.sha256()
    with open(src, "rb") as inp, open(dest, "wb") as out:
        while chunk := inp.read(1024 * 1024):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _store_photos(report_id: str, photo_paths) -> list[dict]:
    """Copies the photos into intake/{report_id}/ (the temp files stay the
    bot's to delete), keeping at most MAX_PHOTOS, each at most
    MAX_PHOTO_BYTES and MAX_PIXELS, MAX_REPORT_BYTES together, that Pillow
    reads as a JPEG, PNG or WebP image and can decode whole (its thumbnail
    and preview are made) -- the same photo twice once (the unique
    (report_id, sha256) index would refuse the second). The rest are
    skipped."""
    folder = intake_dir(report_id)
    kept: list[dict] = []
    seen: set[str] = set()
    total = 0
    for raw in photo_paths or ():
        if len(kept) >= MAX_PHOTOS:
            break
        src = Path(raw)
        try:
            size = src.stat().st_size
        except OSError:
            continue
        if size <= 0 or size > MAX_PHOTO_BYTES or total + size > MAX_REPORT_BYTES:
            continue
        ext = _image_ext(src)
        if ext is None:
            continue
        photo_id = gen_id()
        dest = folder / f"{photo_id}{ext}"
        sha = _copy_hashing(src, dest)
        # Checked again on the copy: what is kept is what was checked.
        if sha in seen or _image_ext(dest) != ext or not is_readable_image(dest):
            dest.unlink(missing_ok=True)
            continue
        # verify() passes a cut-off JPEG; making these decodes all of it.
        if generate_thumbnail(str(dest)) is None or generate_preview(str(dest)) is None:
            for path in (dest, thumbnail_path_for(str(dest)), preview_path_for(str(dest))):
                path.unlink(missing_ok=True)
            continue
        seen.add(sha)
        total += size
        kept.append({"id": photo_id, "path": str(dest), "sha256": sha, "size": size, "ext": ext})
    return kept


def save_submission(form, photo_paths, now: datetime | None = None) -> tuple[str, str]:
    """Saves a finished public form as an intake draft; returns (report id,
    reference). Commits (its own session); run it off the event loop.

    1. The files first, with no lock held: copied (not moved) into
       intake/{id}/, checked and deduplicated, thumbnails and previews made.
    2. ONE transaction: the Report row goes in first, which takes SQLite's
       write lock; then the caps are checked again (check_can_accept's,
       linked and blocked included) -- nothing can commit between that and
       this commit; then the session, photo and submission rows, the
       history entry, and the commit.
    Any failure rolls back and removes the folder; a cap reached meanwhile
    raises PublicRefused. ValueError for a form missing what it needs."""
    now = as_utc(now) or utc_now()
    fields = _cleaned(form)
    if disk_space_low(DISK_HEADROOM_GB):
        raise PublicRefused("disk_low")
    report_id = gen_id()
    try:
        photos = _store_photos(report_id, photo_paths)
    except BaseException:
        remove_intake_dir(report_id)
        raise

    db = SessionLocal()
    try:
        data = SecurityIncidentDraft(
            description="",
            category=["Vehicle Collision or Damage"],
            reporter_name=fields["name"],
            reporter_role=ROLES[fields["role"]],
            reporter_contact=phone_display(fields["phone"]),
            vehicle_info=VehicleInfo(plate_number=fields["plate"]),
        ).model_dump()
        db.add(Report(
            id=report_id,
            channel=PUBLIC_CHANNEL,
            status="intake",
            owner_id=None,
            # Never the Telegram id (review M5): that stays in
            # PublicSubmission, which forgets it at filing.
            reporter_chat_id="public",
            data=data,
            photo_paths=[],
            created_at=now,
        ))
        db.flush()  # the write lock, until the commit
        code = _refusal_code(db, fields["tg"], fields["phone"], now)
        if code:
            raise PublicRefused(code)
        reference = new_reference(db, now)
        db.add(IntakeSession(report_id=report_id, stage="uploading", reporter_notes=reporter_notes_for(form, now), updated_at=now))
        for position, photo in enumerate(photos):
            db.add(IntakePhoto(
                id=photo["id"],
                report_id=report_id,
                position=position,
                original_name=f"telegram-{position + 1}{photo['ext']}",
                path=photo["path"],
                sha256=photo["sha256"],
                size_bytes=photo["size"],
                created_at=now,
            ))
        db.add(PublicSubmission(
            report_id=report_id,
            reference=reference,
            submitted_at=now,
            expires_at=now + UNTAKEN_MAX_AGE,
            consent_at=fields["consent_at"] or now,
            consent_version=fields["consent_version"],
            submitter_name=fields["name"],
            submitter_role=fields["role"],
            submitter_phone=fields["phone"],
            phone_kind=phone_kind(fields["phone"]),
            plate=fields["plate"],
            plate_looks_sg=fields["plate_looks_sg"],
            when_where=fields["when_where"],
            statement=fields["statement"],
            photo_count_sent=max(fields["photo_count_sent"], len(photos)),
            forwarded_count=fields["forwarded_count"],
            telegram_user_id=fields["tg"],
            telegram_username=fields["telegram_username"],
            telegram_name=fields["telegram_name"],
        ))
        audit(db, None, "created", report_id, detail=f"{CREATED_DETAIL} · {reference}")
        db.commit()
    except BaseException:
        db.rollback()
        remove_intake_dir(report_id)
        raise
    finally:
        db.close()
    logger.info("Public report %s saved as draft %s with %d photo(s)", reference, report_id, len(photos))
    return report_id, reference


# --------------------------------------------------- take, assign, release


def _reference(db: Session, report_id: str) -> str | None:
    return db.query(PublicSubmission.reference).filter(PublicSubmission.report_id == report_id).scalar()


def _claim_untaken(db: Session, report_id: str, owner_id: str) -> None:
    """The one conditional UPDATE that takes a report out of the inbox."""
    result = db.execute(
        update(Report)
        .where(Report.id == report_id, untaken_clause())
        .values(owner_id=owner_id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise refusal(db, report_id, taking=True)


def _check_drafts(db: Session, user_id: str, name: str | None = None) -> None:
    """Counted after the UPDATE, under its lock, so this report counts and
    nothing else can slip in: at most MAX_OPEN_DRAFTS_PER_PERSON."""
    drafts = db.query(func.count(Report.id)).filter(Report.owner_id == user_id, Report.status == "intake").scalar()
    if drafts > MAX_OPEN_DRAFTS_PER_PERSON:
        db.rollback()
        raise InboxError(409, DRAFT_LIMIT_OTHER.format(name=name) if name else DRAFT_LIMIT, "draft_limit")


def _mark_taken(db: Session, report_id: str, owner_id: str, now: datetime) -> None:
    # Touching the session restarts the 7-day idle clock (cleanup.py):
    # otherwise a report taken on day 25 went at the next sweep (H3).
    db.execute(
        update(IntakeSession)
        .where(IntakeSession.report_id == report_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(PublicSubmission)
        .where(PublicSubmission.report_id == report_id)
        .values(taken_at=now, taken_by=owner_id)
        .execution_options(synchronize_session=False)
    )


def take(db: Session, report_id: str, actor, now: datetime | None = None) -> str:
    """actor takes an untaken report as their own draft. The caller checks
    can_take and commits. Returns the reference. InboxError: 404
    inbox_gone; 409 inbox_taken ("Taken by S. Lee just now"), inbox_filed,
    draft_limit (counted after the take)."""
    now = now or utc_now()
    _claim_untaken(db, report_id, actor.id)
    _check_drafts(db, actor.id)
    _mark_taken(db, report_id, actor.id, now)
    reference = _reference(db, report_id)
    audit(db, actor, "taken", report_id, detail=f"{TAKEN_DETAIL} · {reference}")
    return reference


def assign(db: Session, report_id: str, target, actor, now: datetime | None = None) -> str:
    """An admin gives an untaken report to target (a User row) as their
    draft. The caller checks the admin and commits. Returns the reference.
    InboxError as take's, with draft_limit counted for target, plus 409
    owner_inactive and owner_unvetted (owners._vetted: an admin or a ticked
    account) -- checked first, and again under the write lock
    (api/auth.recipient_still_active's rule)."""
    now = now or utc_now()
    if not target.active:
        raise InboxError(409, OWNER_INACTIVE, "owner_inactive")
    if not _vetted(target):
        raise InboxError(409, OWNER_UNVETTED, "owner_unvetted")
    _claim_untaken(db, report_id, target.id)
    _check_drafts(db, target.id, target.name or target.username)
    # Columns, not the row: db.get would answer from what was loaded earlier.
    fresh = (
        db.query(User.active, User.role, User.sees_all_cases, User.ai_company_key_allowed, User.may_sign_off)
        .filter(User.id == target.id)
        .first()
    )
    if fresh is None or not fresh.active:
        db.rollback()
        raise InboxError(409, OWNER_INACTIVE, "owner_inactive")
    if not _vetted(fresh):
        db.rollback()
        raise InboxError(409, OWNER_UNVETTED, "owner_unvetted")
    _mark_taken(db, report_id, target.id, now)
    reference = _reference(db, report_id)
    audit(db, actor, "assigned", report_id, detail=f"{assigned_detail(target)} from the public inbox · {reference}")
    return reference


def release(db: Session, report_id: str, actor, seen_owner: str | None, *, is_running=None) -> str:
    """Gives a taken public draft back to the inbox -- only while its owner
    is still seen_owner (what the person looked at). The caller checks
    can_release and commits. Returns the reference. The 30-day clock goes
    on from when it was sent: one released after that goes at the next
    sweep (L8).

    Refused, rolled back, under the write lock: 409 inbox_finalizing; 409
    ai_running while the stage says the AI is reading it. is_running
    (pipeline.is_running, from the api) tells a stage a restart left behind
    from a real run: that one is set to "failed" and the release goes
    ahead. Without it, such a stage counts as running. Else as refusal()."""
    if seen_owner is None:
        raise refusal(db, report_id)
    result = db.execute(
        update(Report)
        .where(
            Report.id == report_id,
            Report.channel == PUBLIC_CHANNEL,
            Report.status == "intake",
            Report.owner_id == seen_owner,
        )
        .values(owner_id=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise refusal(db, report_id)
    stage = db.query(IntakeSession.stage).filter(IntakeSession.report_id == report_id).scalar()
    if stage == "finalizing":
        db.rollback()
        raise InboxError(409, FINALIZING, "inbox_finalizing")
    if stage in RUNNING_STAGES:
        if is_running is None or is_running(report_id):
            db.rollback()
            raise InboxError(409, AI_RUNNING, "ai_running")
        db.execute(
            update(IntakeSession)
            .where(IntakeSession.report_id == report_id, IntakeSession.stage == stage)
            .values(stage="failed", error=STALE_RUN, error_code=None, error_by=None)
            .execution_options(synchronize_session=False)
        )
    db.execute(
        update(PublicSubmission)
        .where(PublicSubmission.report_id == report_id)
        .values(taken_at=None, taken_by=None)
        .execution_options(synchronize_session=False)
    )
    reference = _reference(db, report_id)
    audit(db, actor, "returned_to_inbox", report_id, detail=reference or "")
    return reference


# --------------------------------------------- discard, withdraw, expiry


def _owner_is(seen_owner: str | None):
    return Report.owner_id.is_(None) if seen_owner is None else Report.owner_id == seen_owner


def _delete(
    db: Session,
    report_id: str,
    *,
    seen_owner: str | None,
    actor,
    action: str,
    detail: str,
    block: bool = False,
    sender: str | None = None,
    now: datetime | None = None,
) -> bool:
    # 1. Claim the stage: refused while the AI or finalize holds the draft.
    #    Also the transaction's first write, so its lock is held from here.
    claimed = db.execute(
        update(IntakeSession)
        .where(IntakeSession.report_id == report_id, IntakeSession.stage.notin_(BUSY_STAGES))
        .values(stage="deleting")
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed and db.query(IntakeSession.report_id).filter(IntakeSession.report_id == report_id).first():
        db.rollback()
        return False
    # 2. The report, only if it is still as the caller saw it.
    where = [Report.id == report_id, Report.channel == PUBLIC_CHANNEL, Report.status == "intake", _owner_is(seen_owner)]
    if sender is not None:
        where.append(exists().where(
            PublicSubmission.report_id == Report.id, PublicSubmission.telegram_user_id == sender,
        ))
    if db.execute(delete(Report).where(*where).execution_options(synchronize_session=False)).rowcount != 1:
        db.rollback()
        return False
    # 3. The rest of it.
    db.execute(delete(IntakePhoto).where(IntakePhoto.report_id == report_id).execution_options(synchronize_session=False))
    db.execute(delete(IntakeSession).where(IntakeSession.report_id == report_id).execution_options(synchronize_session=False))
    gone = db.execute(
        delete(PublicSubmission)
        .where(PublicSubmission.report_id == report_id)
        .returning(PublicSubmission.reference, PublicSubmission.telegram_user_id)
        .execution_options(synchronize_session=False)
    ).first()
    reference = gone.reference if gone else None
    if block and gone is not None and gone.telegram_user_id:
        block_sender(db, gone.telegram_user_id, actor=actor, reference=reference, now=now)
    audit(db, actor, action, report_id, detail=" · ".join(p for p in (reference, detail) if p))
    return True


def delete_submission(
    db: Session,
    report_id: str,
    *,
    seen_owner: str | None,
    actor,
    action: str,
    detail: str = "",
    block: bool = False,
    now: datetime | None = None,
) -> bool:
    """Deletes a public draft -- report, session, photo and submission rows
    -- if it is still as the caller saw it: owner seen_owner (None: in the
    inbox), not filed, its stage not held by the AI or finalize. Writes
    one history entry, action with "TG-... · detail" (discarded "· Spam",
    expired, withdrawn): never a name or phone. block: also block the
    sender (a spam discard).

    True: the caller commits, then removes the folder
    (photos.remove_intake_dir). False: nothing changed and everything this
    call did is rolled back; refusal() says why (inbox_changed, ai_running,
    inbox_filed, inbox_gone)."""
    return _delete(db, report_id, seen_owner=seen_owner, actor=actor, action=action, detail=detail, block=block, now=now)


def discard_detail(reason: str, duplicate_of: str | None = None, note: str | None = None) -> str:
    """The history's words for a discard: "Spam", "Duplicate of TG-...",
    "Other · <note>" (the note, one line, at most MAX_DISCARD_NOTE
    characters; staff are asked to keep personal details out of it).
    ValueError for a reason not in DISCARD_REASONS."""
    if reason not in DISCARD_REASONS:
        raise ValueError(f"unknown discard reason: {reason}")
    words = DISCARD_REASONS[reason]
    if reason == "duplicate" and duplicate_of:
        words = f"Duplicate of {' '.join(_clip(duplicate_of, 40).split())}"
    note = " ".join(_clip(note, MAX_DISCARD_NOTE).split()) if note else ""
    return f"{words} · {note}" if note else words


def withdraw(db: Session, report_id: str, tg_id, now: datetime | None = None) -> str:
    """/withdraw: the sender takes back one of their own reports.

        "deleted"  it was still in the inbox: deleted with its photos (the
                   caller commits, then removes the folder)
        "taken"    staff have it: withdraw_requested_at is set for them (and
                   noted in its history) once
        "none"     not this sender's, not here any more, or filed

    Every statement matches telegram_user_id, so a forged button (another
    report's id) changes nothing. The caller commits."""
    now = now or utc_now()
    tg = str(tg_id)
    if _delete(db, report_id, seen_owner=None, actor=None, action="withdrawn", detail=WITHDRAWN_DETAIL, sender=tg, now=now):
        return "deleted"
    mine = (
        PublicSubmission.report_id == report_id,
        PublicSubmission.telegram_user_id == tg,
        PublicSubmission.filed_at.is_(None),
        exists().where(Report.id == PublicSubmission.report_id, Report.status == "intake"),
    )
    noted = db.execute(
        update(PublicSubmission)
        .where(*mine, PublicSubmission.withdraw_requested_at.is_(None))
        .values(withdraw_requested_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if noted:
        audit(db, None, "withdraw_requested", report_id, detail=f"{_reference(db, report_id)} · {WITHDRAW_ASKED_DETAIL}")
        return "taken"
    return "taken" if db.query(PublicSubmission.report_id).filter(*mine).first() else "none"


def open_for_sender(db: Session, tg_id) -> list[dict]:
    """This Telegram account's reports not yet filed, oldest first, for
    /withdraw's buttons: [{id, reference, submitted_at (UTC), taken,
    withdraw_requested}]."""
    rows = (
        db.query(
            PublicSubmission.report_id,
            PublicSubmission.reference,
            PublicSubmission.submitted_at,
            PublicSubmission.withdraw_requested_at,
            Report.owner_id,
        )
        .join(Report, Report.id == PublicSubmission.report_id)
        .filter(
            PublicSubmission.telegram_user_id == str(tg_id),
            PublicSubmission.filed_at.is_(None),
            Report.status == "intake",
        )
        .order_by(PublicSubmission.submitted_at)
        .all()
    )
    return [
        {
            "id": r.report_id,
            "reference": r.reference,
            "submitted_at": as_utc(r.submitted_at),
            "taken": r.owner_id is not None,
            "withdraw_requested": r.withdraw_requested_at is not None,
        }
        for r in rows
    ]


def sweep_untaken(db: Session, now: datetime | None = None) -> list[str]:
    """Deletes untaken reports whose expires_at has come (30 days after they
    were sent; the sweep runs every 6 hours), each through delete_submission,
    so one taken a moment before stays. COMMITS each one, then removes its
    folder. Returns their ids (intake/cleanup.py)."""
    now = now or utc_now()
    due = [
        rid
        for (rid,) in db.query(PublicSubmission.report_id)
        .join(Report, Report.id == PublicSubmission.report_id)
        .filter(PublicSubmission.expires_at <= now, untaken_clause())
        .order_by(PublicSubmission.expires_at)
        .all()
    ]
    removed: list[str] = []
    for rid in due:
        if _delete(db, rid, seen_owner=None, actor=None, action="expired", detail=EXPIRED_DETAIL, now=now):
            db.commit()
            remove_intake_dir(rid)
            removed.append(rid)
    return removed


def sweep_orphans(db: Session) -> int:
    """Deletes submission rows whose report is gone (intake/cleanup.py; the
    caller commits). Returns how many."""
    result = db.execute(
        delete(PublicSubmission)
        .where(~exists().where(Report.id == PublicSubmission.report_id))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def delete_row(db: Session, report_id: str) -> str | None:
    """Deletes the report's submission row, if any, returning its reference
    (DELETE /reports/{id} of a taken draft, whose history entry carries it).
    The caller commits."""
    gone = db.execute(
        delete(PublicSubmission)
        .where(PublicSubmission.report_id == report_id)
        .returning(PublicSubmission.reference)
        .execution_options(synchronize_session=False)
    ).first()
    return gone.reference if gone else None


# ----------------------------------------------------------- filing


def on_finalize(db: Session, report_id: str, now: datetime | None = None) -> str | None:
    """Finalize's part (api/intake.py, before its history entry): the
    submission is filed (filed_at), the sender's Telegram id, @username and
    name are forgotten, and the case's reporter_chat_id is "public", never
    a Telegram id (M5). The name, phone, reference and words stay with the
    case. Returns the reference, or None for a report that isn't public.
    The caller commits."""
    now = now or utc_now()
    result = db.execute(
        update(PublicSubmission)
        .where(PublicSubmission.report_id == report_id)
        .values(filed_at=now, telegram_user_id=None, telegram_username=None, telegram_name=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    db.execute(
        update(Report)
        .where(Report.id == report_id, Report.channel == PUBLIC_CHANNEL)
        .values(reporter_chat_id="public")
        .execution_options(synchronize_session=False)
    )
    return _reference(db, report_id)


# ----------------------------------------------------------- showing


def submission_info(db: Session, report) -> dict | None:
    """The public_submission block of GET /intake/{id}, GET /reports/{id}
    and GET /inbox/{id}, for people who may see the case: who sent it and
    what they gave. None for any other report. Never the Telegram user id.

        {reference, state, submitted_at, expires_at, consent_at, consent_version,
         sender: {name, role, role_label, phone, phone_display, phone_masked,
                  phone_kind, phone_verified, links: {call, whatsapp},
                  telegram_username, telegram_name},
         plate, plate_looks_sg, when_where, statement, photo_count_sent,
         forwarded_count, taken_at, taken_by: {id, name, username, active} | null,
         filed_at, withdraw_requested_at}
    """
    if getattr(report, "channel", None) != PUBLIC_CHANNEL:
        return None
    row = db.get(PublicSubmission, report.id)
    if row is None:
        return None
    taken_by = owners_by_id(db, [row.taken_by]).get(row.taken_by) if row.taken_by else None

    def when(value):
        return iso_utc(value) if value else None

    return {
        "reference": row.reference,
        "state": state_of(report),
        "submitted_at": when(row.submitted_at),
        "expires_at": when(row.expires_at),
        "consent_at": when(row.consent_at),
        "consent_version": row.consent_version,
        "sender": {
            "name": row.submitter_name,
            "role": row.submitter_role,
            "role_label": ROLES.get(row.submitter_role, ROLES["other"]),
            "phone": row.submitter_phone,
            "phone_display": phone_display(row.submitter_phone),
            "phone_masked": mask_phone(row.submitter_phone),
            "phone_kind": row.phone_kind,
            # Typed numbers are refused: every phone came from a contact
            # shared from the sender's own Telegram.
            "phone_verified": True,
            "links": phone_links(row.submitter_phone),
            "telegram_username": row.telegram_username,
            "telegram_name": row.telegram_name,
        },
        "plate": row.plate,
        "plate_looks_sg": bool(row.plate_looks_sg),
        "when_where": row.when_where,
        "statement": row.statement,
        "photo_count_sent": row.photo_count_sent,
        "forwarded_count": row.forwarded_count,
        "taken_at": when(row.taken_at),
        "taken_by": owner_dict(taken_by),
        "filed_at": when(row.filed_at),
        "withdraw_requested_at": when(row.withdraw_requested_at),
    }


def references_for(db: Session, report_ids) -> dict[str, str]:
    """{report id: reference} for those of these reports that came from the
    public, in one query (GET /reports' public_reference)."""
    ids = [i for i in report_ids if i]
    if not ids:
        return {}
    rows = db.query(PublicSubmission.report_id, PublicSubmission.reference).filter(PublicSubmission.report_id.in_(ids)).all()
    return {r.report_id: r.reference for r in rows}


def same_sender(db: Session, report_id: str) -> list[dict]:
    """The sender's other reports still here -- the same Telegram account
    or the same phone -- oldest first: [{id, reference, state}]."""
    row = db.get(PublicSubmission, report_id)
    if row is None:
        return []
    who = [PublicSubmission.submitter_phone == row.submitter_phone]
    if row.telegram_user_id:
        who.append(PublicSubmission.telegram_user_id == row.telegram_user_id)
    rows = (
        db.query(PublicSubmission.report_id, PublicSubmission.reference, Report.status, Report.owner_id)
        .join(Report, Report.id == PublicSubmission.report_id)
        .filter(PublicSubmission.report_id != report_id, or_(*who))
        .order_by(PublicSubmission.submitted_at)
        .all()
    )
    return [{"id": r.report_id, "reference": r.reference, "state": state_of(r)} for r in rows]
