"""Password hashing.

scrypt from the standard library: deliberately slow and memory-hard, so a
copy of the database can't be brute-forced cheaply, without adding a native
dependency to the image. The parameters are stored inside each hash, so they
can be raised later without breaking existing passwords.
"""
import base64
import hashlib
import hmac
import re
import secrets
import threading
from functools import lru_cache
from pathlib import Path
from typing import Iterable

# OWASP's 32 MiB scrypt setting (N=2^15, r=8, p=3): ~0.1-0.2s per check.
_N, _R, _P = 2**15, 8, 3
_MAXMEM = 64 * 1024 * 1024
_DKLEN = 32

# At most this many hashes at once: each takes 32 MiB and a worker thread
# from the pool every request (photos included) shares, so a flood of
# sign-in attempts must not be able to take them all.
_SLOTS = threading.BoundedSemaphore(4)
_SLOT_WAIT_SECONDS = 10


class HashingBusy(Exception):
    """Too many password checks already running; ask the caller to retry."""


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    if not _SLOTS.acquire(timeout=_SLOT_WAIT_SECONDS):
        raise HashingBusy()
    try:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=dklen)
    finally:
        _SLOTS.release()


# NIST SP 800-63B-4: 15 characters when the password is the only factor, no
# composition rules, and a check against common and predictable passwords.
# Applies to new passwords only; existing ones keep working.
MIN_PASSWORD_LENGTH = 15
# scrypt copes with any length; this only stops a request making the server
# hash megabytes.
MAX_PASSWORD_LENGTH = 256


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, _N, _R, _P, _DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_dummy_hash: str | None = None


def burn_time(password: str) -> None:
    """Spends one hash's worth of time when the email isn't known, so the
    response time doesn't reveal which emails have accounts."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_hex(16))
    verify_password(password, _dummy_hash)


TOO_COMMON = "That password is too common or easy to guess. Try 3 or 4 random words."
TOO_PERSONAL = "Don't build the password from your name, your email or \"carlink\". Try 3 or 4 random words."

# The 10,000 most common passwords from SecLists (MIT licence):
# https://github.com/danielmiessler/SecLists/blob/master/Passwords/Common-Credentials/10k-most-common.txt
_COMMON_FILE = Path(__file__).with_name("common_passwords.txt")
# Runs that are easy to type without thinking: alphabet, digits, keyboard rows.
_SEQUENCES = (
    "abcdefghijklmnopqrstuvwxyz",
    "01234567890123456789",
    "qwertyuiopasdfghjklzxcvbnm",
    "1qaz2wsx3edc4rfv5tgb6yhn7ujm8ik9ol0p",
)
_SEPARATORS = re.compile(r"[\s._\-!@#$%^&*()+=,;:'\"/\\|?~`]+")


@lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    words = {line.strip().lower() for line in _COMMON_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}
    # Famous examples people copy, and words tied to this system.
    words |= {"correcthorsebatterystaple", "carlink", "carlinkstudio", "letmein"}
    return frozenset(words)


def _is_predictable(password: str) -> bool:
    low = password.lower()
    squashed = _SEPARATORS.sub("", low)
    if low in _common() or squashed in _common():
        return True
    # One short piece repeated: "passwordpassword", "abcabcabcabcabc".
    if re.fullmatch(r"(.{1,8}?)\1+", squashed):
        return True
    # A common word padded with digits or symbols: "Password12345678!".
    core = re.sub(r"^[\d\W_]+|[\d\W_]+$", "", low)
    if core and (core in _common() or len(core) < 4):
        return True
    for run in _SEQUENCES:
        if squashed in run or squashed in run[::-1]:
            return True
    return False


def _personal_words(personal: Iterable[str]) -> list[str]:
    """Name parts and the email's local part, at least 3 letters each."""
    words = ["carlink"]
    for value in personal:
        value = (value or "").lower()
        if "@" in value:
            value = value.split("@", 1)[0]
        words += [w for w in re.split(r"[^a-z0-9]+", value) if len(w) >= 3]
    return words


def password_problem(password: str, personal: Iterable[str] = ()) -> str | None:
    """Why a new password is refused, or None if it's acceptable.

    `personal`: the account's name and email, which a password mustn't be
    built from. The one place the rules live -- the API, the server command
    and the dashboard's hints all follow it.
    """
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    if not password.strip():
        return "The password can't be only spaces."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if _is_predictable(password):
        return TOO_COMMON
    # What's left once the personal words are taken out must still be long
    # enough on its own: "weikangten-2026-carlink" is mostly guessable.
    rest = password.lower()
    for word in _personal_words(personal):
        rest = rest.replace(word, "")
    if len(_SEPARATORS.sub("", rest)) < 10:
        return TOO_PERSONAL
    return None


def temporary_password() -> str:
    """For an admin to hand to a user, who must change it at first sign-in.
    No look-alike characters (0/O, 1/l/I), since it is read off a screen."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))
