"""Tests for names and usernames people see (agent6 L6): every full name
and username is saved NFKC-normalised; invisible characters (format and
control characters, direction marks, zero-width spaces and joiners,
fillers, variation selectors) are refused with a plain 400; and the
"another account already uses that name" check also catches Cyrillic and
Greek look-alikes ("Wеi Kang" with a Cyrillic е is "Wei Kang"). Accented,
CJK and other real names still pass.

    python scripts/test_names.py

Covers PUT /auth/me, PATCH and POST /users (admin), POST /auth/signup,
invites (POST /account-links, POST /auth/links/accept) and sign-in by
username. Offline and self-contained: a temporary database and storage.
"""
import os
import socket
import sys
import tempfile
import traceback
import unicodedata
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="names-test-"))
(_tmp / "storage").mkdir()
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.auth import INVISIBLE_NAME, INVISIBLE_USERNAME  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth import core  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import AccountLink, User  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

BASE = "https://carlink.test"
PASSWORD = "purple tiger lamp bicycle"
DUPLICATE = "Another account already uses that name. If it really is yours, ask an admin to set it."

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


class Stop(Exception):
    pass


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
        return True
    FAILURES.append((_section["name"], label + (f"  [{why}]" if why else "")))
    print(f"    FAIL {label}" + (f"\n           got: {why}" if why else ""))
    return False


def need(cond, label: str, why: str = "") -> None:
    if not check(cond, label, why):
        raise Stop(label)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n[{name}]")
    try:
        fn()
    except Stop as exc:
        print(f"    ---  rest of this section skipped: {exc}")
    except Exception as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).resolve() == Path(__file__).resolve()]
        msg = f"section stopped by {type(exc).__name__}: {exc} (test line {frames[-1].lineno if frames else -1})"
        FAILURES.append((name, msg))
        print(f"    FAIL {msg}")


def browser() -> TestClient:
    return TestClient(app, base_url=BASE, raise_server_exceptions=False)


def jd(r) -> dict:
    try:
        body = r.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def brief(r) -> str:
    return f"{r.status_code} {(r.text or '').replace(chr(10), ' ')[:220]!a}"


_ips = iter(range(1, 1_000_000))


def fresh_ip() -> dict:
    n = next(_ips)
    return {"X-Real-IP": f"10.88.{(n // 256) % 256}.{n % 256}"}


def signup(username: str):
    c = browser()
    r = c.post("/auth/signup", json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
               headers=fresh_ip())
    need(r.status_code == 200, f"{username} signs up", brief(r))
    return SimpleNamespace(c=c, id=jd(r)["id"], username=jd(r)["username"])


def stored(user_id: str) -> User:
    db = SessionLocal(expire_on_commit=False)
    try:
        return db.get(User, user_id)
    finally:
        db.close()


def set_name(p, name: str):
    return p.c.put("/auth/me", json={"name": name})


W = SimpleNamespace()

# Each invisible character, by name, inside an otherwise ordinary name.
INVISIBLES = {
    "right-to-left override U+202E": "‮",
    "left-to-right mark U+200E": "‎",
    "right-to-left mark U+200F": "‏",
    "zero-width space U+200B": "​",
    "zero-width non-joiner U+200C": "‌",
    "zero-width joiner U+200D": "‍",
    "byte order mark U+FEFF": "﻿",
    "soft hyphen U+00AD": "­",
    "word joiner U+2060": "⁠",
    "first strong isolate U+2068": "⁨",
    "Arabic letter mark U+061C": "؜",
    "Hangul filler U+3164": "ㅤ",
    "halfwidth Hangul filler U+FFA0": "ﾠ",
    "Braille blank U+2800": "⠀",
    "variation selector-16 U+FE0F": "️",
    "combining grapheme joiner U+034F": "͏",
    "tag letter A U+E0041": "\U000e0041",
    "control character BEL": "\x07",
}

# Real names that must keep working: accents (composed and decomposed),
# CJK, other scripts, apostrophes and hyphens.
REAL_NAMES = [
    "José Núñez", "Zoë Tan", "Nguyễn Văn An", "François Dupont", "Søren Ølgaard", "Łukasz Wójcik",
    "陳偉康", "山田 太郎", "김민준", "முருகன் செல்வம்", "Siti Nurhaliza binti Ahmad", "محمد علي",
    "Николай Петров", "Νίκος Παπαδόπουλος", "สมชาย ใจดี", "राज कुमार", "O'Brien Lee", "Mary-Jane Lim",
]


def setup() -> None:
    init_db()
    W.admin_c = browser()
    W.admin_id = sign_in(W.admin_c, role="admin", name="Ada Names", username="adanames")
    W.weikang = signup("weikang.n")
    r = set_name(W.weikang, "Wei Kang")
    need(r.status_code == 200, "Wei Kang names himself", brief(r))
    W.other = signup("other.n")


def unit_rules() -> None:
    for label, ch in INVISIBLES.items():
        check(core.is_invisible(ch), f"is_invisible: {label}")
    for ch in "aZé陳김ம ،'-" + "́":
        check(not core.is_invisible(ch), f"is_invisible({ch!a}) is False")
    key = core._clash_key
    check(key("Wеi Kang") == key("Wei Kang"), "clash key: Cyrillic е in Wеi Kang == Wei Kang")
    check(key("Wei Κang") == key("Wei Kang"), "clash key: Greek Κ in Wei Κang == Wei Kang")
    check(key("ΗΑΝΚ") == key("HANK"), "clash key: Greek capitals ΗΑΝΚ == HANK")
    check(key("Ｗｅｉ　Ｋａｎｇ") == key("wei kang"), "clash key: full-width letters and space == wei kang")
    check(key("Wei​Kang") == key("WeiKang") and key("Wei ‮Kang") == key("Wei Kang"), "clash key: invisible characters dropped")
    check(key("José") != key("Jose"), "clash key: José and Jose stay different (the accent shows)")
    check(key("Николай") != key("Nikolai") and key("Νίκος") != key("Nikos"), "clash key: real Cyrillic and Greek names aren't folded into Latin ones")
    check(key("José") == key("José"), "clash key: decomposed and composed accents are the same name")


def own_name() -> None:
    p = W.other
    for name in REAL_NAMES:
        r = set_name(p, name)
        got = stored(p.id).name
        check(r.status_code == 200 and got == unicodedata.normalize("NFKC", name), f"PUT /auth/me name {name!a} -> 200, kept", brief(r))
    r = set_name(p, "José Martí")
    check(r.status_code == 200 and stored(p.id).name == "José Martí", "a decomposed accent is saved composed (NFKC)", repr(stored(p.id).name))
    r = set_name(p, "Ｍａｒｙ　Ｔａｎ")
    check(r.status_code == 200 and stored(p.id).name == "Mary Tan", "full-width letters and space are saved plain (NFKC)", ascii(stored(p.id).name))
    r = set_name(p, "  Mary \t Ong  ")
    check(r.status_code == 200 and stored(p.id).name == "Mary Ong", "other spaces collapse to one", ascii(stored(p.id).name))
    before = stored(p.id).name
    for label, ch in INVISIBLES.items():
        r = set_name(p, f"Mary{ch} Tan")
        check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_NAME, f"PUT /auth/me name with {label} -> 400", brief(r))
    check(stored(p.id).name == before, "...and the name didn't change")
    r = set_name(p, "​")
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_NAME, "a name of only a zero-width space -> 400", brief(r))
    r = set_name(p, "   ")
    check(r.status_code == 400 and jd(r).get("detail") == "Name can't be empty.", "a name of only spaces -> 400 empty", brief(r))


def look_alikes() -> None:
    p = W.other
    for name, what in (("Wеi Kang", "Cyrillic е"), ("Wei Кang", "Cyrillic К"), ("Wei Κang", "Greek Κ"), ("WEI KANG", "upper case"),
                       ("Ｗｅｉ Ｋａｎｇ", "full-width"), ("Wei  Kang", "two spaces"), ("Wеі Каng", "three Cyrillic letters")):
        r = set_name(p, name)
        check(r.status_code == 400 and jd(r).get("detail") == DUPLICATE, f"{what} ({name!a}) clashes with Wei Kang -> 400", brief(r))
    r = set_name(p, "Wei Kan")
    check(r.status_code == 200, "a different name (Wei Kan) is fine", brief(r))
    r = set_name(p, "Cаrlink Support")
    check(r.status_code == 400 and jd(r).get("detail") == core.RESERVED_NAME, "Cаrlink with a Cyrillic а is still reserved", brief(r))
    r = set_name(p, "Аdmin Tan")
    check(r.status_code == 400 and jd(r).get("detail") == core.RESERVED_NAME, "Аdmin with a Cyrillic А is still reserved", brief(r))
    # A look-alike already saved (before this check, or by an admin) blocks the plain one too.
    q = signup("third.n")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET name = :n WHERE id = :u"), {"n": "Аlice Tаn", "u": q.id})
    r = set_name(p, "Alice Tan")
    check(r.status_code == 400 and jd(r).get("detail") == DUPLICATE, "a saved Cyrillic Аlice Tаn blocks a plain Alice Tan", brief(r))
    r = set_name(W.weikang, "Wei Kang")
    check(r.status_code == 200, "Wei Kang saving his own name again is fine", brief(r))


def admin_names() -> None:
    c = W.admin_c
    p = W.other
    r = c.patch(f"/users/{p.id}", json={"name": "Wеi Kang"})
    check(r.status_code == 200, "an admin may still set a look-alike name (admins set any name)", brief(r))
    r = c.patch(f"/users/{p.id}", json={"name": "Ｍａｒｙ Ｌｉｍ"})
    check(r.status_code == 200 and stored(p.id).name == "Mary Lim", "admin PATCH: the name is saved NFKC", ascii(stored(p.id).name))
    r = c.patch(f"/users/{p.id}", json={"name": "Mary‮ Lim"})
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_NAME, "admin PATCH: an invisible character -> 400", brief(r))
    r = c.patch(f"/users/{p.id}", json={"username": "ｍａｒｙ.ｌｉｍ"})
    check(r.status_code == 200 and stored(p.id).username == "mary.lim", "admin PATCH: a full-width username is saved plain", brief(r))
    r = c.patch(f"/users/{p.id}", json={"username": "mary​lim"})
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_USERNAME, "admin PATCH: an invisible character in a username -> 400", brief(r))
    r = c.post("/users", json={"email": "newbie.n@example.com", "name": "New‍ Person", "role": "user"})
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_NAME, "admin POST /users: an invisible character -> 400", brief(r))
    r = c.post("/users", json={"email": "newbie.n@example.com", "name": "陳 小明", "role": "user"})
    check(r.status_code == 200 and (jd(r).get("user") or {}).get("name") == "陳 小明", "admin POST /users: a CJK name -> 200", brief(r))


def usernames() -> None:
    c = browser()
    r = c.post("/auth/signup", json={"username": "ｊｏｈｎｎｙ", "email": "johnny.n@example.com", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 200 and jd(r).get("username") == "johnny", "signup: a full-width username is saved plain (NFKC)", brief(r))
    r = c.post("/auth/signup", json={"username": "jo​hn", "email": "john2.n@example.com", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 400 and jd(r).get("field") == "username" and jd(r).get("detail") == INVISIBLE_USERNAME,
          "signup: an invisible character in the username -> 400 field username", brief(r))
    r = c.post("/auth/signup", json={"username": "Kim.n", "email": "kim.n@example.com", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 200 and jd(r).get("username") == "kim.n", "signup: the Kelvin sign K reads as k", brief(r))
    r = c.post("/auth/signup", json={"username": "ａｄｍｉｎ", "email": "adm.n@example.com", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 400 and jd(r).get("field") == "username", "signup: full-width admin is the reserved admin", brief(r))
    r = c.post("/auth/signup", json={"username": "ma‮ry", "email": "mary.n@example.com", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_USERNAME, "signup: a right-to-left override in a username -> 400", brief(r))
    d = browser()
    r = d.post("/auth/login", json={"email": "ｊｏｈｎｎｙ", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 200 and jd(r).get("username") == "johnny", "signing in with the full-width username works", brief(r))
    p = W.weikang
    r = p.c.put("/auth/me", json={"username": "ｗｅｉｋａｎｇ.ｎ２"})
    check(r.status_code == 200 and stored(p.id).username == "weikang.n2", "PUT /auth/me: a full-width username is saved plain", brief(r))
    r = p.c.put("/auth/me", json={"username": "weikang‍.n"})
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_USERNAME, "PUT /auth/me: an invisible character in a username -> 400", brief(r))


def invites() -> None:
    c = W.admin_c
    r = c.post("/account-links", json={"email": "invitee1.n@example.com", "role": "user", "name": "Mary‮ Wong"})
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_NAME, "an invite with an invisible character in the name -> 400", brief(r))
    r = c.post("/account-links", json={"email": "invitee1.n@example.com", "role": "user", "name": "Zoë Wong"})
    token = jd(r).get("token")
    need(r.status_code == 200 and token, "an invite for Zoë Wong (decomposed ë)", brief(r))
    check((jd(r).get("link") or {}).get("name") == "Zoë Wong", "...its name is saved composed", ascii((jd(r).get("link") or {}).get("name")))
    r = c.post("/account-links", json={"email": "invitee0.n@example.com", "role": "user", "name": "   "})
    check(r.status_code == 200 and (jd(r).get("link") or {}).get("name") == "", "an invite with no name is still fine", brief(r))
    r = browser().post("/auth/links/accept", json={"token": token, "username": "zo​e", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 400 and jd(r).get("detail") == INVISIBLE_USERNAME, "accepting with an invisible character in the username -> 400", brief(r))
    r = browser().post("/auth/links/accept", json={"token": token, "username": "ｚｏｅ.ｗｏｎｇ", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 200 and jd(r).get("username") == "zoe.wong" and jd(r).get("name") == "Zoë Wong",
          "accepting: the username saved plain, the name as the admin typed it", brief(r))
    # An invite made before this check, with an invisible character in its name.
    r = c.post("/account-links", json={"email": "invitee2.n@example.com", "role": "user", "name": "Old Invite"})
    token = jd(r).get("token")
    need(r.status_code == 200 and token, "an invite for Old Invite", brief(r))
    db = SessionLocal()
    try:
        link = db.query(AccountLink).filter(AccountLink.email == "invitee2.n@example.com").first()
        link.name = "Old‮ Invite​"
        db.commit()
    finally:
        db.close()
    r = browser().post("/auth/links/accept", json={"token": token, "username": "old.invite", "password": PASSWORD}, headers=fresh_ip())
    check(r.status_code == 200 and jd(r).get("name") == "Old Invite", "an older invite's invisible characters are dropped on accept", brief(r))


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("is_invisible and the look-alike key", unit_rules),
        ("PUT /auth/me name: real names pass, NFKC, invisible characters refused", own_name),
        ("look-alike names clash; reserved words with look-alikes", look_alikes),
        ("admin PATCH / POST /users", admin_names),
        ("usernames: signup, PUT /auth/me, sign-in", usernames),
        ("invites: the admin's name, the accepted username", invites),
    ):
        run_section(name, fn)
        if name == "setup" and FAILURES:
            break
    print(f"\ntest_names: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if FAILURES:
        return 1
    print("\nALL NAME CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
