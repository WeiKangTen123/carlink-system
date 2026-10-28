"""Seals people's own AI keys (models.UserAiKey.key_enc) and fingerprints
them, both under one secret: the file ${SECRETS_DIR}/ai-keys.secret.

    seal(text) -> token            Fernet (AES + HMAC), so a changed token fails too
    unseal(token) -> text          raises Unreadable
    fingerprint(text) -> 64 hex    HMAC-SHA256 under the secret, not a plain hash:
                                   a stolen backup can't be used to test guessed keys
    state()                        "ok", or why own keys can't be read here
    startup(create=...)            once per process, at start-up

Why encrypt when the secret sits on the same server: the nightly backup
copies carlink.db off the server (infra/backup.sh), and so would any stray
copy of the database. The secret has its own volume, which neither the
backups nor /files (everything under storage/) ever see.

The secret:
- Comes from that file only. An environment variable as well would be a
  second source, and the api and the bot could end up disagreeing.
- Only the api process creates it (startup(create=True), from api/main.py's
  start-up); the Telegram bot mounts the folder read-only and reads it.
- Created exclusively: written to a temp file, then os.link'ed into place,
  which fails if the file is already there. Two processes racing both end
  up with the winner's, and a reader never sees a half-written file.
- A file that is there but can't be read, or isn't a valid secret, is never
  replaced: a new secret would make every saved key unreadable for good.
  It is logged, and every own key reads as "unreadable" until it's fixed.

The canary: app_settings "ai_keys_canary" holds a known text sealed with
the secret. Each process unseals it at start-up. If that fails, the secret
here isn't the one the keys were sealed with (a database restored from
another server, a swapped file), so own keys are all "unreadable" in this
process. Company keys don't depend on any of this.

A secret file that is gone while the canary says one existed (its volume
was lost, or the database moved to a new server without it): the api makes
a new one, seals a new canary, and marks every saved key "unreadable", so
their owners are asked to add them again.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets as _random
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

SECRET_FILE = "ai-keys.secret"
CANARY_KEY = "ai_keys_canary"
_CANARY_TEXT = "carlink ai-keys canary v1"
# Separates the fingerprint key from the sealing key, both made from the
# one secret.
_FINGERPRINT_LABEL = b"carlink ai-key fingerprint v1"
# A state other than "ok" is looked at again after this long, so the bot
# picks up a secret the api has just made, or a fixed file, without a
# restart; "ok" is kept for the life of the process.
_RECHECK_SECONDS = 10.0
LOST_SECRET_ERROR = "Saved before the server's key secret was lost. Add the key again."

# state() values.
OK = "ok"
MISSING = "missing"  # no secret yet (the api makes it), or it couldn't be made
BROKEN = "broken"  # the file is there but can't be read or isn't a secret
CANARY_FAILED = "canary_failed"  # a secret, but not the one keys were sealed with


class Unreadable(Exception):
    """Own keys can't be sealed or read in this process right now (state()
    says why), or one sealed value doesn't open."""


class _Box:
    def __init__(self, fernet: Fernet, mac_key: bytes):
        self.fernet = fernet
        self.mac_key = mac_key


_lock = threading.Lock()
_box: _Box | None = None
_state: str | None = None  # None: not checked yet in this process
_checked_at = 0.0
# Set by startup(create=True): only the api process makes the secret.
_may_create = False


def secret_path() -> Path:
    return Path(settings.secrets_dir) / SECRET_FILE


class _Bad(Exception):
    """The secret file is there but unusable."""


def _read_secret(path: Path) -> bytes | None:
    """The secret, None if there is no file, or _Bad."""
    try:
        raw = path.read_bytes().strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        # A directory, no permission, a disk error: there is something at
        # that path, so it is never replaced.
        raise _Bad(f"can't be read ({type(exc).__name__})")
    try:
        Fernet(raw)
    except Exception:
        raise _Bad("isn't a valid secret")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        logger.warning("AI key secret %s can be read by other users of the server; it should be mode 0600", path)
    return raw


def _create_secret(path: Path) -> bool:
    """Puts a new secret at path unless one is there already. True if this
    process made it; False if another got there first."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{_random.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key() + b"\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            # Fails if the name is taken: exactly one process wins, and the
            # file appears whole.
            os.link(tmp, path)
            return True
        except FileExistsError:
            return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _make_box(raw: bytes) -> _Box:
    mac_key = hmac.new(base64.urlsafe_b64decode(raw), _FINGERPRINT_LABEL, hashlib.sha256).digest()
    return _Box(Fernet(raw), mac_key)


def _mark_all_unreadable(db) -> int:
    from sqlalchemy.exc import OperationalError

    from app.reports.models import UserAiKey

    try:
        return db.query(UserAiKey).update(
            {UserAiKey.status: "unreadable", UserAiKey.last_error: LOST_SECRET_ERROR}, synchronize_session=False
        )
    except OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return 0


def _check_canary(box: _Box, *, create: bool, made_secret: bool) -> tuple[str, str]:
    """(state, what to log if the state is new)."""
    from sqlalchemy.exc import IntegrityError, OperationalError

    from app.reports.db import SessionLocal
    from app.reports.models import AppSetting

    db = SessionLocal()
    try:
        row = db.get(AppSetting, CANARY_KEY)
        if row is None:
            if not create:
                # The api seals it before any key can be saved; until then
                # there is nothing here to read anyway.
                return MISSING, "the api hasn't sealed the canary yet"
            try:
                db.add(AppSetting(key=CANARY_KEY, value=box.fernet.encrypt(_CANARY_TEXT.encode()).decode("ascii")))
                db.commit()
                return OK, ""
            except IntegrityError:
                db.rollback()  # another process sealed one a moment ago
                row = db.get(AppSetting, CANARY_KEY)
                if row is None:
                    return MISSING, "lost a race sealing the canary; trying again shortly"
        try:
            if box.fernet.decrypt((row.value or "").encode()).decode() == _CANARY_TEXT:
                return OK, ""
        except (InvalidToken, ValueError, UnicodeDecodeError):
            pass
        if not made_secret:
            return CANARY_FAILED, (
                "doesn't open the stored canary: it isn't the secret the saved keys were sealed with. "
                "Own AI keys are unreadable here until the right file is back"
            )
        # This process has just made the secret, yet keys were sealed
        # before: the old secret is gone, and so are those keys.
        row.value = box.fernet.encrypt(_CANARY_TEXT.encode()).decode("ascii")
        lost = _mark_all_unreadable(db)
        db.commit()
        logger.error(
            "The AI key secret was missing, so a new one was made. The %d saved own AI key(s) can't be "
            "read any more and show as unreadable; their owners must add them again.", lost,
        )
        return OK, ""
    except OperationalError as exc:
        db.rollback()
        return MISSING, f"couldn't be checked against the canary ({exc}); trying again shortly"
    finally:
        db.close()


# The secrets this process made (their SHA-256), so a canary check that has
# to be retried (a busy database) still knows the old secret is gone. The
# secret itself, not its path: a different file put there later is not one
# this process made.
_made_here: set[str] = set()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check(create: bool) -> tuple[str, _Box | None, str]:
    """(state, box if ok, what to log if the state is new)."""
    path = secret_path()
    try:
        raw = _read_secret(path)
        if raw is None:
            if not create:
                return MISSING, None, "doesn't exist yet (the api makes it); own AI keys are off here for now"
            made = _create_secret(path)
            raw = _read_secret(path)
            if raw is None:
                return MISSING, None, "couldn't be made"
            if made:
                _made_here.add(_digest(raw))
                logger.info("Made a new AI key secret at %s", path)
    except _Bad as exc:
        return BROKEN, None, (
            f"{exc}. It is NOT replaced (that would lose every saved key): own AI keys are unreadable "
            "until it is fixed"
        )
    except OSError as exc:
        return MISSING, None, f"couldn't be made: {exc}"
    box = _make_box(raw)
    state, problem = _check_canary(box, create=create, made_secret=_digest(raw) in _made_here)
    return state, (box if state == OK else None), problem


def _ensure() -> str:
    global _box, _state, _checked_at
    with _lock:
        if _state == OK:
            return OK
        now = time.monotonic()
        if _state is not None and now - _checked_at < _RECHECK_SECONDS:
            return _state
        state, box, problem = _check(_may_create)
        # Logged once per change, not at every look while it lasts.
        if state != _state and problem:
            level = logging.INFO if state == MISSING and not _may_create else logging.ERROR
            logger.log(level, "AI key secret %s %s", secret_path(), problem)
        _state, _box, _checked_at = state, box, now
        return state


def startup(*, create: bool) -> str:
    """At process start-up, after init_db: reads the secret and checks the
    canary. create=True only in the api, which may make the secret and seal
    the canary; the bot reads. Returns state()."""
    global _may_create, _state
    with _lock:
        _may_create = _may_create or create
        _state = None
    state = _ensure()
    if state == OK:
        logger.info("AI key secret ready (%s)", secret_path())
    return state


def state() -> str:
    """"ok", or why this process can't seal or read own keys: "missing",
    "broken" or "canary_failed"."""
    return _ensure()


def forget() -> None:
    """Drops what this process has loaded; the next use reads the file
    again (tests, after changing the secret)."""
    global _box, _state, _may_create
    with _lock:
        _box, _state, _may_create = None, None, False
        _made_here.clear()


def _ready() -> _Box:
    current = _ensure()
    box = _box
    if current != OK or box is None:
        raise Unreadable(current)
    return box


def seal(text: str) -> str:
    return _ready().fernet.encrypt(text.encode("utf-8")).decode("ascii")


def unseal(token: str) -> str:
    box = _ready()
    try:
        return box.fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        raise Unreadable("this value wasn't sealed with this secret")


def fingerprint(text: str) -> str:
    return hmac.new(_ready().mac_key, text.encode("utf-8"), hashlib.sha256).hexdigest()
