"""People's own AI (Gemini) keys, added on Settings > My AI key
(models.UserAiKey, the routes in api/ai_keys.py).

Release P3 only stores them and says how they stand. The AI still runs on
the company key for everyone allowed it (auth/permissions.may_use_company_key),
exactly as before; P4 makes it use the presser's own key first.

One key per person and one person per key: a key someone else has saved,
or one of the company's, is refused. The key is sealed the moment it
arrives (ai/secretbox.py) and never leaves the server again. Everyone,
admins included, sees only its last 4 characters and a status:

    untested    saved, but Google couldn't be reached to check it
    ok          Google accepted it at the last check
    invalid     Google refused it (wrong, deleted, or the API is off for it)
    quota       Google accepted it but it has no allowance left for now
    unreadable  this server can't open saved keys right now (secretbox.state()
                isn't "ok"). Worked out when read, never stored: the rows
                keep Google's last verdict, so the right secret put back
                mends them all
"""
import hmac
import logging
import re

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.ai import secretbox
from app.auth.core import audit, utc_now
from app.auth.permissions import may_use_company_key
from app.config import settings
from app.reports.models import ApiKey, User, UserAiKey, iso_utc

logger = logging.getLogger(__name__)

PROVIDER = "gemini"
STATUSES = ("untested", "ok", "invalid", "quota", "unreadable")
# What a row can hold: Google's verdicts. "unreadable" is only ever shown.
STORED_STATUSES = ("untested", "ok", "invalid", "quota")
# The one answer for a key someone else has (an own key, or a company
# key): it mustn't confirm which, or that the key exists at all.
IN_USE = "This key can't be used here."
# What the AI will use (P4): a key Google hasn't refused or run dry.
WORKING = ("ok", "untested")

# Gemini keys are 39 characters starting "AIza"; looser than that, so a
# newer key format isn't turned away by this check rather than by Google.
_FORMAT = re.compile(r"[A-Za-z0-9_-]{20,60}")
FORMAT_PROBLEM = "That doesn't look like a Gemini API key. It is usually 39 letters and numbers, starting with AIza."
# Anything key-like in a message from Google, before it is stored.
_KEYLIKE = re.compile(r"AIza[0-9A-Za-z_-]*")
MAX_ERROR = 200
# models.list answers in well under a second; a hung connection mustn't
# hold a worker thread for long.
PROBE_TIMEOUT_MS = 10_000
UNREACHABLE = "Couldn't reach Google to check the key."
STORAGE_OFF_ERROR = "This server can't read saved AI keys right now. Please tell your admin."
ROW_UNREADABLE_ERROR = "This key can't be opened on this server. Add it again."


def format_problem(key: str) -> str | None:
    """Why this (trimmed) text can't be a key, or None."""
    return None if _FORMAT.fullmatch(key) else FORMAT_PROBLEM


def last4(key: str) -> str:
    return key[-4:]


def scrub(message: str, key: str | None = None) -> str:
    """A message from Google made safe to keep: no key in it, one line, at
    most 200 characters."""
    text = message or ""
    if key:
        text = text.replace(key, "...")
    text = _KEYLIKE.sub("AIza...", text)
    return " ".join(text.split())[:MAX_ERROR]


def probe_key(key: str) -> tuple[str, str | None]:
    """Asks Google whether it takes this key: models.list, which spends none
    of the key's allowance. ("ok", None), ("invalid", why), ("quota", why),
    or ("untested", why) when Google couldn't give an answer.

    A client of its own, thrown away after: app/ai/client.get_client keeps
    every key it sees for the life of the process."""
    from google import genai
    from google.genai import errors, types

    client = None
    try:
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=PROBE_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=0)),
        )
        next(iter(client.models.list(config={"page_size": 1})), None)
        return "ok", None
    except errors.APIError as exc:
        why = scrub(f"Google said: {exc.code} {exc.message or exc.status or ''}", key)
        if exc.code in (400, 401, 403):
            return "invalid", why
        if exc.code == 429:
            return "quota", why
        # Google's own trouble (5xx): no verdict on the key.
        logger.info("AI key check got no answer from Google (HTTP %s)", exc.code)
        return "untested", why
    except Exception as exc:
        # Never the exception itself: some carry the request, key included.
        logger.info("AI key check couldn't reach Google (%s)", type(exc).__name__)
        return "untested", UNREACHABLE
    finally:
        close = getattr(client, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass


def company_key_matches(db: Session, key: str) -> bool:
    """Whether this is one of the company's keys (Settings > Company, or
    GEMINI_API_KEY). Those are already shared by everyone allowed them."""
    candidates = [k for (k,) in db.query(ApiKey.key).filter(ApiKey.provider == PROVIDER).all() if k]
    if settings.gemini_api_key:
        candidates.append(settings.gemini_api_key)
    sent = key.encode("utf-8")
    return any(hmac.compare_digest(c.strip().encode("utf-8"), sent) for c in candidates)


def key_in_use(db: Session, key: str, fingerprint: str, user_id: str) -> str | None:
    """Why someone can't save this key, for the history (they are only
    told "This key can't be used here."), or None."""
    other = (
        db.query(UserAiKey.user_id)
        .filter(UserAiKey.fingerprint == fingerprint, UserAiKey.user_id != user_id)
        .first()
    )
    if other:
        owner = db.get(User, other[0])
        return f"another account's key ({'@' + owner.username if owner and owner.username else other[0]})"
    if company_key_matches(db, key):
        return "a company key"
    return None


def fingerprint_if_readable(key: str) -> str | None:
    """The key's fingerprint, or None when own keys can't be opened here
    (their fingerprints couldn't be compared then anyway)."""
    try:
        return secretbox.fingerprint(key)
    except secretbox.Unreadable:
        return None


def own_key_owner(db: Session, fingerprint: str | None) -> str | None:
    """Whose own key this is ("@username"), for refusing it as a company
    key, or None. fingerprint is fingerprint_if_readable's: worked out
    before the caller takes the write lock, as it may read the secret
    afresh. None (own keys can't be opened here) checks nothing: adding a
    company key, the backup, mustn't wait on that."""
    if fingerprint is None:
        logger.warning("A company key was added without checking it against own keys (secret %s)", secretbox.state())
        return None
    other = db.query(UserAiKey.user_id).filter(UserAiKey.fingerprint == fingerprint).first()
    if other is None:
        return None
    owner = db.get(User, other[0])
    return "@" + owner.username if owner and owner.username else other[0]


def get_row(db: Session, user_id: str) -> UserAiKey | None:
    return db.query(UserAiKey).filter(UserAiKey.user_id == user_id).first()


def save_key(
    db: Session, user_id: str, key: str, fingerprint: str, status: str, error: str | None, *, billing_confirmed: bool
) -> UserAiKey:
    """Seals and stores the key as this person's, replacing any they had
    (the caller commits). status is probe_key's verdict."""
    now = utc_now()
    row = get_row(db, user_id)
    if row is None:
        row = UserAiKey(user_id=user_id, provider=PROVIDER)
        db.add(row)
    row.key_enc = secretbox.seal(key)
    row.last4 = last4(key)
    row.fingerprint = fingerprint
    row.status = status
    row.last_error = error
    row.billing_confirmed = billing_confirmed
    row.last_checked_at = None if status == "untested" else now
    row.last_used_at = None
    # A new key: "added" now, even when it replaces one.
    row.created_at = now
    row.updated_at = now
    return row


def record_check(db: Session, user_id: str, fingerprint: str, status: str, error: str | None) -> bool:
    """Stores a re-check's verdict (the caller commits) -- only if the key
    tested is still this person's key: one saved meanwhile mustn't get the
    old key's verdict. False when it was thrown away."""
    result = db.execute(
        update(UserAiKey)
        .where(UserAiKey.user_id == user_id, UserAiKey.fingerprint == fingerprint)
        .values(status=status, last_error=error, last_checked_at=utc_now())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount > 0


def unseal_key(row: UserAiKey) -> str:
    """The key itself, for asking Google about it. Raises
    secretbox.Unreadable. Never put it in a response or a log."""
    return secretbox.unseal(row.key_enc)


def delete_user_key(db: Session, user_id: str, actor, *, action: str = "ai_key_removed", reason: str = "") -> bool:
    """Removes this person's own key, if they have one, with a history
    entry; False if there was none. The caller commits, so this can be part
    of a bigger change -- disabling an account deletes its key in the same
    transaction (PATCH /users/{id} {active: false})."""
    # One DELETE, not a read and then a delete: it takes SQLite's write
    # lock as it runs, so a key saved a moment before goes too, and one
    # being saved meanwhile waits for this commit (then finds the account
    # off, api/ai_keys.py).
    gone = db.execute(
        delete(UserAiKey)
        .where(UserAiKey.user_id == user_id)
        .returning(UserAiKey.last4)
        .execution_options(synchronize_session=False)
    ).all()
    if not gone:
        return False
    owner = db.get(User, user_id)
    detail = f"{owner.email if owner else user_id}: key ...{gone[0][0]}"
    audit(db, actor, action, detail=f"{detail} ({reason})" if reason else detail)
    return True


def _status_of(stored: str, readable: bool) -> str:
    return stored if readable else "unreadable"


def keys_by_user(db: Session, user_ids) -> dict[str, dict]:
    """{user id: {has_key, last4, status, added_at}} for the people who have
    a key, for the admin's Users screen -- one query for all of them, and
    never the sealed key."""
    ids = list(user_ids)
    if not ids:
        return {}
    rows = (
        db.query(UserAiKey.user_id, UserAiKey.last4, UserAiKey.status, UserAiKey.created_at)
        .filter(UserAiKey.user_id.in_(ids))
        .all()
    )
    readable = secretbox.state() == secretbox.OK if rows else True
    return {
        r.user_id: {
            "has_key": True,
            "last4": r.last4,
            "status": _status_of(r.status, readable),
            "added_at": iso_utc(r.created_at),
        }
        for r in rows
    }


def ai_key_status(db: Session, user, *, unreadable: bool = False) -> dict:
    """GET /auth/me/ai-key: the person's own key and what the AI will use
    for them. source is what P4 will use; in P3 the AI still runs on the
    company key, for the people allowed it, as before. unreadable: this
    row wouldn't open, though the secret is fine (shown, not stored)."""
    row = get_row(db, user.id)
    company = may_use_company_key(user)
    if row is None:
        return {
            "has_key": False,
            "last4": None,
            "status": None,
            "billing_confirmed": False,
            "added_at": None,
            "last_checked_at": None,
            "last_error": None,
            "company_allowed": company,
            "ai_available": company,
            "source": "company" if company else "none",
        }
    storage_ok = secretbox.state() == secretbox.OK
    readable = storage_ok and not unreadable
    status = _status_of(row.status, readable)
    working = status in WORKING
    error = row.last_error if readable else STORAGE_OFF_ERROR if not storage_ok else ROW_UNREADABLE_ERROR
    return {
        "has_key": True,
        "last4": row.last4,
        "status": status,
        "billing_confirmed": bool(row.billing_confirmed),
        "added_at": iso_utc(row.created_at),
        "last_checked_at": iso_utc(row.last_checked_at) if row.last_checked_at else None,
        "last_error": error,
        "company_allowed": company,
        "ai_available": working or company,
        "source": "own" if working else "company" if company else "none",
    }
