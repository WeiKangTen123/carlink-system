"""Tests for the CodeQL findings fixed on 2026-11-22 (plans/2026-11-22-
enhance-staging/S-security.md): each section would have caught its finding.

    python scripts/test_security_fixes.py

  - folders named after a request value stay inside their base
    (storage/files.confined_path: incident_dir, intake_dir,
    remove_intake_dir; py/path-injection);
  - photos sent back with a case edit must be the caller's own uploads,
    directly in storage/tmp (api/main._checked_temp_paths;
    py/path-injection);
  - WhatsApp media: only Twilio's media URL, rebuilt from its ids; a
    redirect only to a public https host, never with the Twilio
    credentials; a chat id is one plain file name (py/full-ssrf,
    py/path-injection);
  - the password check's trim is linear, and says the same as the regex it
    replaced; the sign-up email check accepts and refuses the same
    addresses (py/polynomial-redos).

Offline and synthetic: temporary STORAGE_DIR, SECRETS_DIR and database,
made-up ids and credentials, downloads on an httpx MockTransport, every
outside connection refused. No Gemini, no real files.
"""
import asyncio
import os
import random
import re
import socket
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="security-fixes-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
# Forced, not setdefault: never a real folder or key from the environment.
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ.pop("GEMINI_API_KEY", None)
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = "AC" + "0" * 32
os.environ["TWILIO_AUTH_TOKEN"] = "made-up-twilio-token"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import httpx  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import app.channels.whatsapp as whatsapp  # noqa: E402
from app.api import auth as auth_api  # noqa: E402
from app.api.main import _checked_temp_paths  # noqa: E402
from app.auth import passwords  # noqa: E402
from app.auth.core import CurrentUser  # noqa: E402
from app.intake.photos import intake_dir, remove_intake_dir  # noqa: E402
from app.storage.files import UnsafePath, confined_path, incident_dir, tmp_dir  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def refused(fn, *args) -> bool:
    try:
        fn(*args)
    except UnsafePath:
        return True
    return False


def everything_under(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)) for p in folder.rglob("*")}


def person(user_id: str) -> CurrentUser:
    return CurrentUser(id=user_id, email="w@example.com", username="w", name="W", role="user",
                       must_change_password=False, qualifications="", license_number="", firm_name="", session_hash="")


# ------------------------------------------------------------ folders by id
def case_folders() -> None:
    base = STORAGE / "intake"
    check(str(confined_path(base, "2790f292c0e3")) == str(base / "2790f292c0e3"),
          "a real case id: the same path as base / id")
    check(str(confined_path(base, "a1", "photos")) == str(base / "a1" / "photos"), "...and below it")
    for bad in ("", ".", "..", "../x", "../../carlink.db", "a/../../x", "a/../..", str(_tmp / "elsewhere"),
                "/etc/passwd", "C:\\Windows", "..\\x"):
        if bad in ("C:\\Windows", "..\\x") and os.sep == "/":
            continue  # plain file names on Linux, not paths
        check(refused(confined_path, base, bad), f"{bad!r}: refused")
    check(refused(confined_path, base, "../intake2"), "a sibling folder sharing the prefix (intake2): refused")

    top_before = set(_tmp.iterdir())
    for bad in ("../../outside", "../../../outside", "..", ""):
        check(refused(incident_dir, bad), f"incident_dir({bad!r}): refused")
        check(refused(intake_dir, bad), f"intake_dir({bad!r}): refused")
    made = [p for p in everything_under(STORAGE) if "outside" in p or p.endswith("photos")]
    check(set(_tmp.iterdir()) == top_before and not made, "...and no folder made anywhere for them", repr(made))

    d = incident_dir("abc123def456")
    check(d.parent.parent.parent == STORAGE / "incidents" and (d / "photos").is_dir(),
          "incident_dir(a real id): storage/incidents/YYYY/MM/<id>/photos", str(d))
    i = intake_dir("abc123def456")
    check(i == STORAGE / "intake" / "abc123def456" and i.is_dir(), "intake_dir(a real id): storage/intake/<id>", str(i))

    keep = STORAGE / "intake" / "someone-elses"
    keep.mkdir(parents=True, exist_ok=True)
    (keep / "p.jpg").write_bytes(b"x")
    (STORAGE / "carlink.db").write_bytes(b"not a database")
    for bad in ("", ".", "..", "../", "../intake", "someone-elses/.."):
        remove_intake_dir(bad)
    check((keep / "p.jpg").exists() and (STORAGE / "carlink.db").exists() and (STORAGE / "intake").is_dir(),
          "remove_intake_dir('' / '..' / ...): the intake folder and storage untouched")
    remove_intake_dir("abc123def456")
    check(not i.exists() and (keep / "p.jpg").exists(), "remove_intake_dir(a real id): just that draft's folder goes")


# ------------------------------------------------------- photos sent back
def temp_paths() -> None:
    tmp = Path(tmp_dir())
    alice, bob = person("alice1"), person("bob2")
    mine = tmp / "ualice1-abc.jpg"
    mine.write_bytes(b"x")
    bobs = tmp / "ubob2-def.jpg"
    bobs.write_bytes(b"x")
    (tmp / "sub").mkdir(exist_ok=True)
    nested = tmp / "sub" / "ualice1-ghi.jpg"
    nested.write_bytes(b"x")
    secret = STORAGE / "carlink.db"
    secret.write_bytes(b"not a database")

    got = _checked_temp_paths([str(mine)], alice)
    check(got == [os.path.realpath(mine)], "Alice's own upload: accepted, as its real path", repr(got))

    def bad_request(raw) -> bool:
        try:
            _checked_temp_paths([raw], alice)
        except HTTPException as exc:
            return exc.status_code == 400
        return False

    for label, raw in (
        ("the database, by its own path", str(secret)),
        ("the database, through tmp/..", str(tmp / ".." / "carlink.db")),
        ("tmp/../tmp/<Alice's>: fine path, but spelled via ..", None),
        ("Bob's upload", str(bobs)),
        ("a file in a folder below tmp", str(nested)),
        ("tmp itself", str(tmp)),
        ("a NUL byte", str(mine) + "\x00.jpg"),
        ("a relative path", "ualice1-abc.jpg"),
        ("a prefix sibling folder", str(tmp) + "x" + os.sep + "ualice1-abc.jpg"),
    ):
        if raw is None:
            ok = _checked_temp_paths([str(tmp / ".." / "tmp" / mine.name)], alice) == [os.path.realpath(mine)]
            check(ok, label + ": accepted, as the real path")
            continue
        check(bad_request(raw), f"{label}: 400")

    link = tmp / "ualice1-link.jpg"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        print("    (symlinks not allowed here: the symlink case is skipped)")
    else:
        check(bad_request(str(link)), "a symlink in tmp to a file outside it: 400")


# ------------------------------------------------------------- WhatsApp
AC, MM, ME = "AC" + "a1" * 16, "MM" + "b2" * 16, "ME" + "c3" * 16
TWILIO_MEDIA = f"https://api.twilio.com/2010-04-01/Accounts/{AC}/Messages/{MM}/Media/{ME}"
CDN_IP = "93.184.216.34"  # a public address, as a literal: no DNS needed


def whatsapp_media() -> None:
    for host, public in (("127.0.0.1", False), ("10.1.2.3", False), ("172.18.0.4", False), ("192.168.1.9", False),
                         ("169.254.169.254", False), ("::1", False), ("fd00::1", False), ("0.0.0.0", False),
                         (CDN_IP, True)):
        check(whatsapp._public_host(host) is public, f"{host}: {'public' if public else 'not public'}")

    for chat in ("whatsapp:+6591234567", "whatsapp:../../../etc/x", "whatsapp:a/b\\c", "whatsapp:\x00"):
        name = whatsapp._media_file_name(chat, 0)
        check(re.fullmatch(r"[A-Za-z0-9+_-]+_0\.jpg", name) is not None, f"chat {chat!r} -> one plain file name", name)
    check(whatsapp._media_file_name("whatsapp:+6591234567", 2) == "whatsapp_+6591234567_2.jpg",
          "a real chat id: the same name as before")

    seen: list[tuple[str, str | None]] = []
    redirect = {"to": f"https://{CDN_IP}/media/abc.jpg"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.host == "api.twilio.com":
            return httpx.Response(307, headers={"location": redirect["to"]})
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": f"https://{CDN_IP}/loop"})
        return httpx.Response(200, content=b"\xff\xd8" + b"0" * 64)

    real_async = httpx.AsyncClient

    class MockAsync(real_async):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    def fetch(url: str, chat: str = "whatsapp:+6591234567"):
        try:
            return asyncio.run(whatsapp._download_media(chat, url, 0)), None
        except ValueError as exc:
            return None, exc

    whatsapp.httpx.AsyncClient = MockAsync
    try:
        path, err = fetch(TWILIO_MEDIA)
        check(path is not None and Path(path).stat().st_size == 66, "Twilio -> a public CDN address: fetched", repr(err))
        check(seen[0][0] == TWILIO_MEDIA and (seen[0][1] or "").startswith("Basic "), "...the credentials go to api.twilio.com")
        check(seen[1][0].startswith(f"https://{CDN_IP}/") and seen[1][1] is None, "...and not to the CDN", repr(seen))
        check(Path(path).parent == Path(tmp_dir()), "...kept directly in storage/tmp")

        for where in ("http://169.254.169.254/latest/meta-data/", "https://169.254.169.254/latest/meta-data/",
                      "https://127.0.0.1/admin", "https://10.0.0.5/", "https://[::1]/x", "https://localhost/x",
                      f"https://{CDN_IP}:8443/x", f"http://{CDN_IP}/x", "file:///etc/passwd"):
            seen.clear()
            redirect["to"] = where
            path, err = fetch(TWILIO_MEDIA)
            check(path is None and err is not None and len(seen) == 1,
                  f"Twilio redirecting to {where}: refused, nothing fetched from there", repr(seen))

        seen.clear()
        redirect["to"] = f"https://{CDN_IP}/loop"
        path, err = fetch(TWILIO_MEDIA)
        check(path is None and err is not None and len(seen) == whatsapp.MAX_REDIRECTS + 1,
              f"redirects without end: refused after {whatsapp.MAX_REDIRECTS}", f"{len(seen)} requests")

        seen.clear()
        redirect["to"] = f"https://{CDN_IP}/media/abc.jpg"
        path, err = fetch(TWILIO_MEDIA, chat="whatsapp:../../escape")
        check(path is not None and Path(path).parent == Path(tmp_dir()) and not (STORAGE.parent / "escape_0.jpg").exists(),
              "a From with ../ in it: still one file directly in storage/tmp", repr(path))

        seen.clear()
        for bad in ("https://api.twilio.com/x", f"https://api.twilio.com.evil.example/2010-04-01/Accounts/{AC}/Messages/{MM}/Media/{ME}",
                    "http://169.254.169.254/latest/meta-data/", TWILIO_MEDIA + "?x=1"):
            path, err = fetch(bad)
            check(path is None and err is not None, f"{bad}: refused")
        check(not seen, "...without a single request", repr(seen))
    finally:
        whatsapp.httpx.AsyncClient = real_async


# --------------------------------------------------------------- ReDoS
_OLD_TRIM = re.compile(r"^[\d\W_]+|[\d\W_]+$")
_OLD_EMAIL = re.compile(r"[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}")


def redos() -> None:
    rng = random.Random(20261122)
    alphabet = "aZ09_ -!.%\n\té€٣²"  # letters, digits (Arabic-Indic too), symbols, unicode, newline
    same = all(
        passwords._trim_pads(s) == _OLD_TRIM.sub("", s)
        for s in ("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24))) for _ in range(20000))
    )
    check(same, "_trim_pads == the old regex on 20,000 random strings")
    for s, want in (("Password12345678!", "Password"), ("123!", ""), ("1a1", "a"), ("__x__", "x"), ("", "")):
        check(passwords._trim_pads(s) == want, f"_trim_pads({s!r}) == {want!r}")

    t = time.perf_counter()
    passwords._trim_pads("a" + "0" * 200_000 + "a")
    took = time.perf_counter() - t
    check(took < 1.0, f"200,000 digits between two letters: {took:.3f}s (the old regex: minutes)")
    for pw, predictable in (("Password12345678!", True), ("passwordpassword", True), ("12345678", True),
                            ("tangerine-orbit-kettle-moss", False)):
        check(passwords._is_predictable(pw) is predictable, f"{pw!r}: {'refused' if predictable else 'fine'} as before")

    emails = ["a@b.co", "first.last+tag@example.com", "o'neil@mail.example.sg", "x_y%z@a-b.c-d.museum",
              "bad", "a@b", "a@b.c", "@b.co", "a@.co", "a@b..co", "a b@c.co", "a@b.co ", "A@B.CO", "a@b.co\n",
              "a@b_c.co", "a@b.c0m", "a@@b.co", "a@b.co.", "%" * 50, "a@" + "b." * 40 + "co"]
    for e in emails:
        check(bool(auth_api._EMAIL.fullmatch(e)) == bool(_OLD_EMAIL.fullmatch(e)), f"email {e[:30]!r}: same answer as before")
    t = time.perf_counter()
    for s in ("%" * 100_000, "a@" + "a." * 50_000 + "!", "a@" + "a" * 100_000):
        auth_api._EMAIL.fullmatch(s)
    took = time.perf_counter() - t
    check(took < 1.0, f"100,000-character emails: {took:.3f}s")


def main() -> int:
    for name, fn in (
        ("folders named after a case id stay in storage", case_folders),
        ("photos sent back with a case: own uploads, directly in tmp", temp_paths),
        ("WhatsApp media: Twilio only, public redirects only, plain file names", whatsapp_media),
        ("regexes on what people type: linear", redos),
    ):
        run_section(name, fn)
    print(f"\ntest_security_fixes: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL SECURITY FIX CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
