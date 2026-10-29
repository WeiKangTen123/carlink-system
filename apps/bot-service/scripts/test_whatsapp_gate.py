"""Tests for the WhatsApp webhook's gate (app/channels/whatsapp.py).

    python scripts/test_whatsapp_gate.py

The webhook has no AI for anyone (a WhatsApp number is linked to no
account), and since P4 that was also its only gate: any signed-in account
could make the api fetch any MediaUrl, with the Twilio SID and token sent
as Basic auth. Now every message gets the polite reply before the form's
media is looked at, and -- for the day drafting is switched back on --
media is only fetched from Twilio's host, at most 25MB, without the
credentials following a redirect elsewhere.

Offline: the router on a small app with a stand-in for the sign-in
middleware, downloads on an httpx MockTransport, every outside connection
refused.
"""
import asyncio
import os
import socket
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="whatsapp-gate-test-"))
(_tmp / "storage").mkdir()
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForWhatsAppGateTst1"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = "ACtest00000000000000000000000000"
os.environ["TWILIO_AUTH_TOKEN"] = "test-twilio-token"

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.ai.structured as structured  # noqa: E402
import app.channels.whatsapp as whatsapp  # noqa: E402
from app.auth.core import CurrentUser  # noqa: E402
from app.conversation import state as chat_state  # noqa: E402
from app.reports.db import init_db  # noqa: E402

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


def person(role: str = "user", ai: bool = False) -> CurrentUser:
    return CurrentUser(id=f"u-{role}-{ai}", email="w@example.com", username="w", name="W", role=role,
                       must_change_password=False, qualifications="", license_number="", firm_name="", session_hash="",
                       ai_company_key_allowed=ai)


def gate() -> None:
    init_db()
    mini = FastAPI()
    mini.include_router(whatsapp.router)
    as_user = {"user": None}

    @mini.middleware("http")
    async def _signed_in(request, call_next):
        request.state.user = as_user["user"]
        return await call_next(request)

    fetched: list[str] = []

    async def no_download(chat_id, url, index):
        fetched.append(url)
        raise AssertionError("media was fetched")

    def no_gemini(key=None):
        raise AssertionError("Gemini was called")

    real = (whatsapp._download_media, structured.get_client)
    whatsapp._download_media, structured.get_client = no_download, no_gemini
    try:
        wa = TestClient(mini)
        forms = (
            {"From": "whatsapp:+6511111111", "Body": ""},
            {"From": "whatsapp:+6511111111", "Body": "Rear-ended at the carpark."},
            {"From": "whatsapp:+6511111111", "Body": "confirm"},
            {"From": "whatsapp:+6511111111", "NumMedia": "2", "MediaUrl0": "http://169.254.169.254/latest/meta-data/",
             "MediaUrl1": "https://api.twilio.com/2010-04-01/Accounts/AC/Messages/MM/Media/ME"},
        )
        for who, user in (("a plain user", person()), ("a ticked user", person(ai=True)), ("an admin", person("admin"))):
            as_user["user"] = user
            replies = [wa.post("/whatsapp/webhook", data=form) for form in forms]
            check(all(r.status_code == 200 and "<Message>" in r.text and "isn't available over WhatsApp" in r.text for r in replies),
                  f"{who}: 200 TwiML, the polite reply, whatever was sent", replies[-1].text[:200])
        check(not fetched, "no media was fetched -- not even Twilio's, let alone 169.254.169.254", repr(fetched))
        check(not chat_state.get_session("whatsapp:whatsapp:+6511111111").photo_paths, "...and nothing was kept for the chat")
    finally:
        whatsapp._download_media, structured.get_client = real


def media_urls() -> None:
    for url, ok in (
        ("https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1", True),
        ("https://api.twilio.com:443/2010-04-01/Accounts/AC1/Media/ME1", True),
        ("http://api.twilio.com/2010-04-01/Accounts/AC1/Media/ME1", False),
        ("https://api.twilio.com:8443/x", False),
        ("https://api.twilio.com.evil.example/x", False),
        ("https://evil.example/?u=https://api.twilio.com/x", False),
        ("https://user@evil.example/x", False),
        ("http://169.254.169.254/latest/meta-data/", False),
        ("https://localhost/x", False),
        ("file:///etc/passwd", False),
        ("not a url", False),
    ):
        check(whatsapp._media_url_ok(url) is ok, f"{url}: {'fetched' if ok else 'refused'}")


def downloads() -> None:
    seen: list[tuple[str, str | None]] = []
    answers = {"big": False}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "api.twilio.com":
            return httpx.Response(307, headers={"location": "https://media.twiliocdn.example/abc.jpg"})
        size = whatsapp.MAX_MEDIA_BYTES + 1024 if answers["big"] else 2048
        return httpx.Response(200, content=b"\xff\xd8" + b"0" * size)

    real_async = httpx.AsyncClient

    class MockAsync(real_async):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    whatsapp.httpx.AsyncClient = MockAsync
    try:
        url = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"
        path = asyncio.run(whatsapp._download_media("whatsapp:+6511111111", url, 0))
        check(Path(path).exists() and Path(path).stat().st_size == 2050, "a Twilio photo is fetched, through Twilio's redirect")
        check(seen[0][0] == "api.twilio.com" and (seen[0][1] or "").startswith("Basic "), "...the credentials go to api.twilio.com")
        check(seen[1][0] == "media.twiliocdn.example" and seen[1][1] is None, "...and not on to the host it redirects to", repr(seen))

        seen.clear()
        answers["big"] = True
        try:
            asyncio.run(whatsapp._download_media("whatsapp:+6511111111", url, 1))
            raised = None
        except ValueError as exc:
            raised = exc
        dest = Path(os.environ["STORAGE_DIR"]) / "tmp" / "whatsapp_+6511111111_1.jpg"
        check(raised is not None and not dest.exists(), "over 25MB: refused, and the partial file removed")

        seen.clear()
        for bad in ("http://169.254.169.254/latest/meta-data/", "https://evil.example/x.jpg"):
            try:
                asyncio.run(whatsapp._download_media("whatsapp:+6511111111", bad, 2))
                raised = None
            except ValueError as exc:
                raised = exc
            check(raised is not None, f"{bad}: refused")
        check(not seen, "...without a single request", repr(seen))
    finally:
        whatsapp.httpx.AsyncClient = real_async


def main() -> int:
    for name, fn in (
        ("no AI, and nothing fetched", gate),
        ("media only from Twilio", media_urls),
        ("downloads: size cap, credentials stay with Twilio", downloads),
    ):
        run_section(name, fn)
    print(f"\ntest_whatsapp_gate: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL WHATSAPP GATE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
