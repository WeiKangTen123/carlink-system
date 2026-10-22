"""Password hashing.

scrypt from the standard library: deliberately slow and memory-hard, so a
copy of the database can't be brute-forced cheaply, without adding a native
dependency to the image. The parameters are stored inside each hash, so they
can be raised later without breaking existing passwords.
"""
import base64
import hashlib
import hmac
import secrets

# OWASP's 32 MiB scrypt setting (N=2^15, r=8, p=3): ~0.1-0.2s per check.
_N, _R, _P = 2**15, 8, 3
_MAXMEM = 64 * 1024 * 1024
_DKLEN = 32

MIN_PASSWORD_LENGTH = 10
# scrypt copes with any length; this only stops a request making the server
# hash megabytes.
MAX_PASSWORD_LENGTH = 256


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
            dklen=len(expected),
        )
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


def password_problem(password: str) -> str | None:
    """Why a new password is refused, or None if it's acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    if not password.strip():
        return "The password can't be only spaces."
    return None


def temporary_password() -> str:
    """For an admin to hand to a user, who must change it at first sign-in.
    No look-alike characters (0/O, 1/l/I), since it is read off a screen."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(14))
