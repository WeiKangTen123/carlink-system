"""Seals people's own AI keys (models.UserAiKey.key_enc) and fingerprints
them, both under one secret: the file ${SECRETS_DIR}/ai-keys.secret.

    seal(text) -> token            Fernet (AES + HMAC), so a changed token fails too
    unseal(token) -> text          raises Unreadable
    fingerprint(text) -> 64 hex    HMAC-SHA256 under the secret, not a plain hash:
                                   a stolen backup can't be used to test guessed keys
    state(), describe()            "ok", or why own keys can't be read here
    startup(create=...)            once per process, at start-up

    python -m app.ai.secretbox status
    python -m app.ai.secretbox reset [--confirm]    start over (in the api container)

Why encrypt when the secret sits on the same server: the nightly backup
copies carlink.db off the server (infra/backup.sh), and so would any stray
copy of the database. The secret has its own volume, which neither the
backups nor /files (everything under storage/) ever see.

The secret:
- Comes from that file only. An environment variable as well would be a
  second source, and the api and the bot could end up disagreeing.
- Only the api process creates it (startup(create=True), from api/main.py's
  start-up), and only for a database that has never had one: no canary and
  no saved own keys. The Telegram bot mounts the folder read-only.
- Created exclusively: written to a temp file, then os.link'ed into place,
  which fails if the file is already there. Two processes racing both end
  up with the winner's, and a reader never sees a half-written file.
- Never replaced by the app. A file that is damaged ("broken"), the wrong
  one ("canary_failed"), or gone while the database says keys were sealed
  with one ("lost": a volume not mounted, a server move) leaves own keys
  off -- unreadable, and none can be saved -- with an error at every start
  naming the two ways out: put the right file back, or `reset --confirm`.
  A new secret made instead would lose every saved key for good, and on a
  mount that is only missing for one start, lose them for nothing.

The canary: app_settings "ai_keys_canary" holds a known text sealed with
the secret. Each process unseals it at start-up; if that fails, the secret
here isn't the one the keys were sealed with (a database restored from
another server, a swapped file). Company keys don't depend on any of this.

"Unreadable" is never written into the key rows: it is worked out from
state() when they are read, so putting the right secret back mends every
key at once. While "ok", the file and the canary are looked at again every
few seconds of use, so a reset or a restored file is noticed without a
restart.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets as _random
import sys
import threading
import time
from dataclasses import dataclass
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
# How often the state is looked at again while in use: a problem fixed, or
# (while "ok") the file or the canary changed by a reset or a restore.
_RECHECK_SECONDS = 10.0

# state() values.
OK = "ok"
OFF = "off"  # no secret yet (the api makes it), or it couldn't be made
LOST = "lost"  # no secret file, but the database has keys sealed with one
BROKEN = "broken"  # the file is there but can't be read or isn't a secret
CANARY_FAILED = "canary_failed"  # a secret, but not the one keys were sealed with

_RESET = "run `python -m app.ai.secretbox reset --confirm` in the api container to start over (everyone re-adds their key)"
FIX_LOST = f"Copy the old ai-keys.secret into the ai_secrets volume, or {_RESET}."
FIX_WRONG = f"Copy the right ai-keys.secret (the one these keys were saved with) into the ai_secrets volume, or {_RESET}."
_DETAILS = {
    OK: "Saved own AI keys can be opened. Their secret is ai-keys.secret in the ai_secrets volume.",
    OFF: (
        "The AI key secret hasn't been made yet: the api makes it when it starts. If this stays, check "
        "the api's log, and that the ai_secrets volume is mounted (read-write for the api)."
    ),
    LOST: (
        "The AI key secret (ai-keys.secret) is missing, but keys were saved with one, so saved own keys "
        f"can't be opened and no new ones can be saved. {FIX_LOST}"
    ),
    BROKEN: (
        "The AI key secret file is damaged or can't be read, so saved own keys can't be opened and no new "
        f"ones can be saved. {FIX_WRONG}"
    ),
    CANARY_FAILED: (
        "This server's AI key secret isn't the one the saved keys were sealed with (a database from "
        "another server, or a replaced file), so they can't be opened and no new ones can be saved. "
        f"{FIX_WRONG}"
    ),
}


class Unreadable(Exception):
    """Own keys can't be sealed or read in this process right now (state()
    says why), or one sealed value doesn't open."""


class _Box:
    def __init__(self, fernet: Fernet, mac_key: bytes):
        self.fernet = fernet
        self.mac_key = mac_key


@dataclass(frozen=True)
class _Loaded:
    box: _Box
    digest: str  # SHA-256 of the file as it was read
    canary: str  # the canary as it was when it opened


_lock = threading.Lock()
_loaded: _Loaded | None = None
_state: str | None = None  # None: not checked yet in this process
_checked_at = 0.0
# Set by startup(create=True): only the api process makes the secret.
_may_create = False


def secret_path() -> Path:
    return Path(settings.secrets_dir) / SECRET_FILE


class _Bad(Exception):
    """The secret file is there but unusable."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_secret(path: Path) -> bytes | None:
    """The secret, None if there is no file, or _Bad."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            raw = f.read().strip()
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
    if os.name == "posix" and st.st_mode & 0o077:
        logger.warning("AI key secret %s can be read by other users of the server; it should be mode 0600", path)
    return raw


def _write_temp(path: Path, raw: bytes) -> Path:
    """A new secret in a temp file beside path (mode 0600, on disk)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{_random.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw + b"\n")
        f.flush()
        os.fsync(f.fileno())
    return tmp


def _create_secret(path: Path) -> bool:
    """Puts a new secret at path unless one is there already. True if this
    process made it; False if another got there first."""
    tmp = _write_temp(path, Fernet.generate_key())
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


def _opens(box: _Box, token: str | None, expected: str | None = None) -> bool:
    try:
        text = box.fernet.decrypt((token or "").encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return False
    return expected is None or text == expected


def _no_table(exc) -> bool:
    return "no such table" in str(exc).lower()


def _keys_were_sealed() -> bool | None:
    """Whether this database has had a secret: a canary, or saved own keys.
    None if the database couldn't be asked."""
    from sqlalchemy.exc import OperationalError

    from app.reports.db import SessionLocal
    from app.reports.models import AppSetting, UserAiKey

    db = SessionLocal()
    try:
        if db.query(AppSetting.key).filter(AppSetting.key == CANARY_KEY).first() is not None:
            return True
        return db.query(UserAiKey.id).first() is not None
    except OperationalError as exc:
        return False if _no_table(exc) else None
    finally:
        db.close()


def _check_canary(box: _Box, *, create: bool) -> tuple[str, str, str | None]:
    """(state, what to log if the state is new, the canary if it opened)."""
    from sqlalchemy.exc import IntegrityError, OperationalError

    from app.reports.db import SessionLocal
    from app.reports.models import AppSetting, UserAiKey

    wrong = (
        "doesn't open the stored canary: it isn't the secret the saved keys were sealed with (a database "
        f"from another server, or a replaced file). Own AI keys are off here until it's fixed. {FIX_WRONG}"
    )
    db = SessionLocal()
    try:
        row = db.get(AppSetting, CANARY_KEY)
        if row is None:
            if not create:
                # The api seals it before any key can be saved; until then
                # there is nothing here to read anyway.
                return OFF, "is there, but the api hasn't sealed the canary yet", None
            # Only a secret that opens the keys already saved may be sealed
            # into a new canary (one lost its row, say).
            sample = db.query(UserAiKey.key_enc).first()
            if sample is not None and not _opens(box, sample[0]):
                return CANARY_FAILED, wrong, None
            value = box.fernet.encrypt(_CANARY_TEXT.encode()).decode("ascii")
            try:
                db.add(AppSetting(key=CANARY_KEY, value=value))
                db.commit()
                return OK, "", value
            except IntegrityError:
                db.rollback()  # another process sealed one a moment ago
                row = db.get(AppSetting, CANARY_KEY)
                if row is None:
                    return OFF, "lost a race sealing the canary; trying again shortly", None
        if _opens(box, row.value, _CANARY_TEXT):
            return OK, "", row.value
        return CANARY_FAILED, wrong, None
    except OperationalError as exc:
        db.rollback()
        return OFF, f"couldn't be checked against the canary ({exc}); trying again shortly", None
    finally:
        db.close()


def _check(create: bool) -> tuple[str, _Loaded | None, str]:
    """(state, the secret if ok, what to log if the state is new)."""
    path = secret_path()
    try:
        found = _read_secret(path)
        if found is None:
            sealed_before = _keys_were_sealed()
            if sealed_before is None:
                return OFF, None, "couldn't be checked (the database is busy); trying again shortly"
            if sealed_before:
                # The api links the file in before it seals the canary, so
                # with a canary there the file is too -- unless it is gone.
                # (Another api process may have just made both.)
                found = _read_secret(path)
                if found is None:
                    return LOST, None, (
                        "is missing, but this database has keys sealed with one, so a new one is NOT made "
                        f"(that would lose them for good). Own AI keys are off until it's fixed. {FIX_LOST}"
                    )
            elif not create:
                return OFF, None, "doesn't exist yet (the api makes it); own AI keys are off here for now"
            else:
                if _create_secret(path):
                    logger.info("Made a new AI key secret at %s", path)
                found = _read_secret(path)
                if found is None:
                    return OFF, None, "couldn't be made"
    except _Bad as exc:
        return BROKEN, None, (
            f"{exc}. It is NOT replaced (that would lose every saved key): own AI keys are off until "
            f"it's fixed. {FIX_WRONG}"
        )
    except OSError as exc:
        return OFF, None, f"couldn't be made: {exc}"
    raw = found
    box = _make_box(raw)
    state, problem, canary = _check_canary(box, create=create)
    if state != OK:
        return state, None, problem
    return OK, _Loaded(box, _digest(raw), canary), ""


def _canary_now(db=None) -> str | None:
    from app.reports.db import SessionLocal
    from app.reports.models import AppSetting

    own = db is None
    if own:
        db = SessionLocal()
    try:
        return db.query(AppSetting.value).filter(AppSetting.key == CANARY_KEY).scalar()
    finally:
        if own:
            db.close()


def _still_current(loaded: _Loaded) -> bool:
    """The file and the canary are the ones this process opened. The file
    is read, not just looked at: 45 bytes, at most every few seconds, and a
    copy written over it in place can keep its size and even its time."""
    try:
        return _digest(secret_path().read_bytes().strip()) == loaded.digest and _canary_now() == loaded.canary
    except Exception:
        return False


def _ensure() -> str:
    global _loaded, _state, _checked_at
    with _lock:
        now = time.monotonic()
        if _state is not None and now - _checked_at < _RECHECK_SECONDS:
            return _state
        if _state == OK and _loaded is not None and _still_current(_loaded):
            _checked_at = now
            return OK
        state, loaded, problem = _check(_may_create)
        # Logged once per change, and at every start (startup() clears the
        # state), not at every look while it lasts.
        if state != _state:
            if problem:
                level = logging.INFO if state == OFF and not _may_create else logging.ERROR
                logger.log(level, "AI key secret %s %s", secret_path(), problem)
            elif _state is not None:
                logger.info("AI key secret %s works again", secret_path())
        _state, _loaded, _checked_at = state, loaded, now
        return state


def _expire() -> None:
    """Look again at the next use."""
    global _checked_at
    with _lock:
        _checked_at = float("-inf")


def startup(*, create: bool) -> str:
    """At process start-up, after init_db: reads the secret and checks the
    canary. create=True only in the api, which may make the secret (for a
    database that has never had one) and seal the canary; the bot reads.
    Returns state()."""
    global _may_create, _state
    with _lock:
        _may_create = _may_create or create
        _state = None
    state = _ensure()
    if state == OK:
        logger.info("AI key secret ready (%s)", secret_path())
    return state


def state() -> str:
    """"ok", or why this process can't seal or read own keys: "off",
    "lost", "broken" or "canary_failed"."""
    return _ensure()


def describe() -> dict:
    """{state, detail} for an admin (GET /system/info): the detail is plain
    English and names the fix."""
    current = state()
    return {"state": current, "detail": _DETAILS[current]}


def forget() -> None:
    """Drops what this process has loaded; the next use reads the file
    again (tests, after changing the secret)."""
    global _loaded, _state, _may_create
    with _lock:
        _loaded, _state, _may_create = None, None, False


def _ready() -> _Box:
    current = _ensure()
    loaded = _loaded
    if current != OK or loaded is None:
        raise Unreadable(current)
    return loaded.box


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


def sealed_with_current(db) -> bool:
    """For a save, once it holds SQLite's write lock (it has flushed): the
    canary is still the one this process's secret opened, so what it sealed
    can be read later. False after a reset or a restore meanwhile; the next
    use then looks again."""
    loaded = _loaded
    ok = loaded is not None and _canary_now(db) == loaded.canary
    if not ok:
        _expire()
    return ok


# ------------------------------------------------------------- starting over


def saved_key_count() -> int:
    from sqlalchemy.exc import OperationalError

    from app.reports.db import SessionLocal
    from app.reports.models import UserAiKey

    db = SessionLocal()
    try:
        return db.query(UserAiKey).count()
    except OperationalError as exc:
        if _no_table(exc):
            return 0
        raise
    finally:
        db.close()


def reset() -> int:
    """Starts own keys over: a new secret, a new canary, and every saved own
    key deleted -- sealed with a secret that is gone, they can't be opened
    again. Returns how many were deleted. For the server command line only
    (`reset --confirm`); the running api and bot notice at their next use.

    The database changes and the new file go in together: the rows and the
    canary are written first, the file is moved into place (os.replace)
    while the write lock is held, then the commit."""
    from app.auth.core import audit
    from app.reports.db import SessionLocal
    from app.reports.models import AppSetting, UserAiKey

    path = secret_path()
    raw = Fernet.generate_key()
    tmp = _write_temp(path, raw)
    db = SessionLocal()
    try:
        deleted = db.query(UserAiKey).delete(synchronize_session=False)
        value = _make_box(raw).fernet.encrypt(_CANARY_TEXT.encode()).decode("ascii")
        row = db.get(AppSetting, CANARY_KEY)
        if row is None:
            db.add(AppSetting(key=CANARY_KEY, value=value))
        else:
            row.value = value
        audit(db, None, "ai_keys_reset",
              detail=f"New AI key secret made on the server; {deleted} saved own key(s) deleted")
        db.flush()
        os.replace(tmp, path)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
        try:
            os.unlink(tmp)
        except OSError:
            pass
    _expire()
    logger.warning("AI key secret reset at %s: %d saved own key(s) deleted", path, deleted)
    return deleted


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.ai.secretbox",
        description="The secret that seals people's own AI keys (ai-keys.secret).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="say whether saved own keys can be opened, and if not, why")
    reset_cmd = commands.add_parser("reset", help="start over: a new secret, and every saved own key deleted")
    reset_cmd.add_argument("--confirm", action="store_true", help="really do it (without it, only says what it would do)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    # Everything it would log is printed below anyway.
    logger.setLevel(logging.CRITICAL)

    from app.reports.db import init_db

    init_db()
    # As a reader: looking never makes a secret.
    current = describe()
    print(f"AI key secret ({secret_path()}): {current['state']}")
    print(f"  {current['detail']}")
    if args.command == "status":
        return 0 if current["state"] == OK else 1

    count = saved_key_count()
    if not args.confirm:
        print("\n`reset --confirm` would:")
        print(f"  - make a new secret at {secret_path()}, replacing whatever is there;")
        print(f"  - delete the {count} saved own AI key(s): without the old secret they can't be opened;")
        print("  - seal a new canary, so the api and the bot accept the new secret.")
        print("People then add their keys again on Settings > My AI key. Company keys aren't touched.")
        if current["state"] == OK:
            print(f"\nThe current secret WORKS: resetting would throw away {count} working key(s).")
        else:
            print("\nIf the old ai-keys.secret still exists somewhere, copying it back is better: nothing is lost.")
        print("Nothing was changed. Run again with --confirm to do it.")
        return 1
    try:
        deleted = reset()
    except OSError as exc:
        print(f"\nCouldn't reset: {exc}. Run this in the api container (the bot's copy of the folder is "
              "read-only), and if something other than a file is at that path, remove it first.")
        return 1
    print(f"\nDone: a new secret at {secret_path()}, and {deleted} saved own key(s) deleted.")
    print("The api and the bot pick it up at their next use; people can add their keys again now.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
