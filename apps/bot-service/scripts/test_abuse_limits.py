"""Release P1's abuse limits (app/api/limits.py, the renderer's slot limit
and unverified-owner banner), beyond what test_permissions.py's B2 sections
check over HTTP: the AI never writing Our Ref, sign-off order, who gets the
banner (none once staff sign off) and where it lands on a real PDF, Our Ref
edge cases, renders deferred rather than refused on saves, the render
budget (unvetted accounts only, refunded when nothing rendered), one render
at a time per person, the Chromium slot limit and the bot's longer wait for
it, the daily case cap, the disk guard on edits, finalize and in the
Telegram bot, ticks on new accounts, and the PDF download's Cache-Control.
test_render_threads.py checks the API's threads under a burst of renders.

    python scripts/test_abuse_limits.py

Offline: Gemini and Chromium are fakes, with a temporary database and
storage folder -- except one real PDF at the end, which needs Playwright's
Chromium; checking its banner page by page needs PyMuPDF (skipped without).
"""
import asyncio
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="abuse-limits-test-")
os.environ["STORAGE_DIR"] = _tmp
# The start-up below makes the AI key secret (app/ai/secretbox.py): not in the source tree.
os.environ["SECRETS_DIR"] = tempfile.mkdtemp(prefix="secrets-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ.pop("ENABLE_API_DOCS", None)

import app.ai.structured as structured  # noqa: E402

# The model "reads" an Our Ref off a photo; it must never reach a case.
AI_DRAFT = {"report_id": "CL 55555", "description": "Fake AI draft.", "category": ["Vehicle Collision or Damage"]}


class FakeInteractions:
    def create(self, **_kw):
        return SimpleNamespace(output_text=json.dumps(AI_DRAFT))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.api.intake as intake_api  # noqa: E402
import app.channels.telegram as tg  # noqa: E402
import app.rendering.renderer as renderer  # noqa: E402
from app.ai.extraction import _parse_draft, _response_schema  # noqa: E402
from app.api import limits  # noqa: E402
from app.api.main import _api_error, app  # noqa: E402
from app.auth.deps import ApiError  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake.pipeline import merge_redraft  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import AuditEntry, ChatLink, Report, gen_id  # noqa: E402
from app.reports.owners import TICKS  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage import files as storage_files  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

BASE = "https://carlink.test"
BANNER = "Prepared by an unverified user, not issued by Carlink Consultancy"

# Chromium: records each page's HTML and writes a stub PDF. The slot limit
# around it (html_to_pdf) is the real one. CHROMIUM["fail"] makes it crash;
# with CHROMIUM["gate"] set it holds its slot until the gate opens.
REAL_CHROMIUM_PDF = renderer._chromium_pdf
RENDERED: list[str] = []
CHROMIUM = {"fail": False, "gate": None, "inside": 0}
_inside_lock = threading.Lock()


def _fake_chromium_pdf(html: str, output_path: str) -> None:
    RENDERED.append(html)
    if CHROMIUM["fail"]:
        raise RuntimeError("Playwright PDF generation failed: Chromium crashed")
    gate = CHROMIUM["gate"]
    if gate is not None:
        with _inside_lock:
            CHROMIUM["inside"] += 1
        try:
            gate.wait(30)
        finally:
            with _inside_lock:
                CHROMIUM["inside"] -= 1
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


renderer._chromium_pdf = _fake_chromium_pdf


def ok(label: str) -> None:
    print(f"    ok   {label}")


def person(role: str = "user", name: str = "Test Person", **ticks):
    c = TestClient(app, base_url=BASE)
    user_id = sign_in(c, role=role, name=name, **ticks)
    return SimpleNamespace(c=c, id=user_id)


def jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


def draft(plate: str = "SKA1111A", **extra) -> dict:
    return {"category": ["Vehicle Collision or Damage"], "description": "Rear-ended",
            "vehicle_info": {"plate_number": plate}, **extra}


def new_case(p, **extra) -> str:
    r = p.c.post("/reports", json={"draft": draft(**extra)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def stored(case_id: str):
    db = SessionLocal()
    try:
        r = db.get(Report, case_id)
        return SimpleNamespace(data=r.data, pdf_path=r.pdf_path)
    finally:
        db.close()


def low_disk():
    """shutil.disk_usage reporting 1 GB free."""
    usage = namedtuple("usage", "total used free")
    return mock.patch.object(shutil, "disk_usage", lambda path: usage(100 * 2**30, 99 * 2**30, 2**30))


def drop_pdf(case_id: str) -> None:
    """Deletes the case's PDF file, so the next download or opening renders."""
    path = stored(case_id).pdf_path
    if path:
        Path(path).unlink(missing_ok=True)


def counted(user_id: str) -> int:
    """Renders on this account's budget right now."""
    return len(limits.renders._times.get(user_id, ()))


def wait_until(cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def main() -> int:
    with TestClient(app, base_url=BASE):
        admin = person("admin", "Ada Admin")
        plain = person(name="Pia Plain")
        signer = person(name="Sid Signer", may_sign_off=True)
        seer = person(name="Sue Seesall", sees_all_cases=True)
        ai_only = person(name="Al Aionly", ai_company_key_allowed=True)

        print("AI and Our Ref")
        assert "report_id" not in _response_schema()["properties"]
        assert _parse_draft(json.dumps(AI_DRAFT)).report_id is None
        r = ai_only.c.post("/reports/analyze-photos", data={"description": "x"}, files=[("photos", ("a.jpg", jpeg(), "image/jpeg"))])
        assert r.status_code == 200 and r.json()["draft"]["report_id"] is None, r.text
        for p in r.json()["temp_photo_paths"]:
            Path(p).unlink(missing_ok=True)
        merged = merge_redraft({"report_id": "CL 12345"}, {}, {"report_id": None, "description": "new"})
        assert merged["report_id"] == "CL 12345" and merged["description"] == "new"
        ok("the AI never writes Our Ref, and a re-read keeps the one staff typed")

        mini = FastAPI()
        from app.channels.whatsapp import router as whatsapp_router

        mini.include_router(whatsapp_router)
        mini.add_exception_handler(ApiError, _api_error)
        as_user = {"user": None}

        @mini.middleware("http")
        async def _signed_in(request, call_next):
            request.state.user = as_user["user"]
            return await call_next(request)

        from app.auth.core import CurrentUser

        def current(ai: bool) -> CurrentUser:
            return CurrentUser(id="u1", email="w@example.com", username="w", name="W", role="user", must_change_password=False,
                               qualifications="", license_number="", firm_name="", session_hash="", ai_company_key_allowed=ai)

        wa = TestClient(mini)
        # P4: whoever presses the button pays, and a WhatsApp number has no
        # account behind it -- so no AI for anyone there, ticked or not.
        from app.conversation import state as chat_state

        def no_gemini(key=None):
            raise AssertionError("Gemini was called for WhatsApp")

        import app.channels.whatsapp as whatsapp

        fetched: list[str] = []

        async def no_download(chat_id, url, index):
            fetched.append(url)
            raise AssertionError("WhatsApp media was fetched")

        real_get_client, structured.get_client = structured.get_client, no_gemini
        real_download, whatsapp._download_media = whatsapp._download_media, no_download
        try:
            for ticked in (False, True):
                as_user["user"] = current(ticked)
                for form in ({"From": "whatsapp:+6500000000", "Body": ""},
                             {"From": "whatsapp:+6500000000", "Body": "it was scraped"},
                             # Not Twilio's: before P4 any signed-in account could make the
                             # api fetch this, with the Twilio SID and token attached.
                             {"From": "whatsapp:+6500000000", "NumMedia": "1",
                              "MediaUrl0": "http://169.254.169.254/latest/meta-data/"}):
                    r = wa.post("/whatsapp/webhook", data=form)
                    assert r.status_code == 200 and "isn't available over WhatsApp" in r.text, r.text
            assert not fetched, fetched
            assert not chat_state.get_session("whatsapp:whatsapp:+6500000000").photo_paths
        finally:
            structured.get_client = real_get_client
            whatsapp._download_media = real_download
        ok("the WhatsApp webhook (mounted only with Twilio credentials) has no AI for anyone: a polite reply, "
           "before any media is fetched")

        print("sign-off")
        d = signer.c.post("/intake").json()["id"]
        r = signer.c.post(f"/reports/{d}/sign-off", json={"terms_accepted": True})
        assert r.status_code == 409, r.text
        case_id = new_case(plain)
        r = plain.c.post(f"/reports/{case_id}/sign-off", json={"terms_accepted": True})
        assert r.status_code == 403 and r.json()["code"] == "not_allowed" and "sign off" in r.json()["detail"], r.text
        assert stored(case_id).data["sign_off"]["status"] == "Draft"
        signer.c.delete(f"/reports/{d}")
        ok("a draft is still 409 (finish it first), a non-staff sign-off 403 not_allowed, and nothing is signed")

        print("who gets the unverified banner")
        def owned_by(owner_id):
            db = SessionLocal()
            try:
                rid = gen_id()
                db.add(Report(id=rid, channel="manual", reporter_chat_id="dashboard", data=draft(), status="confirmed", owner_id=owner_id))
                db.commit()
                return rid
            finally:
                db.close()

        cases = {
            "not saved yet (Telegram renders first)": ("nosuchcase1", False),
            "Unassigned": (owned_by(None), False),
            "an admin's": (owned_by(admin.id), False),
            "a May-sign-off account's": (owned_by(signer.id), False),
            "a see-all-cases (no sign-off) account's": (owned_by(seer.id), True),
            "an AI-tick-only account's": (owned_by(ai_only.id), True),
            "a self-signup's": (owned_by(plain.id), True),
            "an owner whose account row is gone": (owned_by("gone-user-id"), True),
        }
        for label, (rid, want) in cases.items():
            assert renderer._owner_unverified(rid) is want, label
        with mock.patch("app.reports.db.SessionLocal", side_effect=RuntimeError("database is locked")):
            assert renderer._owner_unverified(cases["an admin's"][0]) is True
        ok("banner for owners who aren't admin or May-sign-off, or can't be read; none for staff, Unassigned or unsaved")

        RENDERED.clear()
        new_case(plain)
        assert RENDERED and BANNER in RENDERED[-1] and "@bottom-center" in RENDERED[-1]
        RENDERED.clear()
        new_case(signer)
        assert RENDERED and "unverified" not in RENDERED[-1].lower()
        ok("POST /reports renders before the case is saved: the caller's status decides the banner")

        staff = person(name="Stu Staff", may_sign_off=True, sees_all_cases=True)
        outsider = person(name="Otto Outside")
        case_id = new_case(outsider)
        RENDERED.clear()
        r = staff.c.post(f"/reports/{case_id}/sign-off", json={"terms_accepted": True})
        assert r.status_code == 200 and RENDERED and BANNER not in RENDERED[-1], r.text
        RENDERED.clear()
        assert admin.c.post(f"/reports/{case_id}/render-pdf").status_code == 200
        assert RENDERED and BANNER not in RENDERED[-1]
        RENDERED.clear()
        assert admin.c.post(f"/reports/{case_id}/reopen").status_code == 200
        assert RENDERED and BANNER in RENDERED[-1]
        ok("signed off by staff, an unvetted owner's case PDF has no banner (re-renders too); reopened, it's back")

        def signed(signer_id, status="Signed Off"):
            return {"sign_off": {"status": status, "signed_by_user_id": signer_id}}

        assert renderer._signed_off_by_staff(signed(admin.id)) and renderer._signed_off_by_staff(signed(signer.id))
        for data in (signed(seer.id), signed(ai_only.id), signed(plain.id), signed("gone-user-id"),
                     signed(admin.id, status="Draft"), signed(None), {"sign_off": None}, {}):
            assert not renderer._signed_off_by_staff(data), data
        with mock.patch("app.reports.db.SessionLocal", side_effect=RuntimeError("database is locked")):
            assert not renderer._signed_off_by_staff(signed(admin.id))
        ok("only a signer who is admin or May-sign-off counts; other signers, a gone account, a reopened case "
           "and a failed lookup keep the banner")

        print("Our Ref")
        rid = new_case(admin, report_id="CL 70001")
        db = SessionLocal()
        db.get(Report, rid).owner_id = plain.id
        db.commit()
        db.close()
        r = plain.c.put(f"/reports/{rid}", json={"draft": draft(report_id="  CL 70001 ", description="tidied")})
        assert r.status_code == 200 and stored(rid).data["report_id"] == "CL 70001" and stored(rid).data["description"] == "tidied", r.text
        r = plain.c.put(f"/reports/{rid}", json={"draft": draft(report_id=None)})
        assert r.status_code == 403 and r.json() == {"detail": "Only staff can set Our Ref.", "code": "not_allowed"}, r.text
        r = plain.c.put(f"/reports/{rid}", json={"draft": {"category": ["Other"], "description": "no ref sent"}})
        assert r.status_code == 200 and stored(rid).data["report_id"] == "CL 70001"
        r = admin.c.put(f"/reports/{rid}", json={"draft": draft(report_id=None)})
        assert r.status_code == 200 and stored(rid).data["report_id"] is None
        ok("non-staff: the same ref (spaces aside) is kept as stored, clearing it is refused; staff can clear it")

        d = plain.c.post("/intake").json()["id"]
        r = admin.c.put(f"/intake/{d}/data", json={"data": {"description": "", "category": ["Other"], "report_id": "CL 70002"}})
        assert r.status_code == 200, r.text
        r = plain.c.put(f"/intake/{d}/data", json={"data": {"description": "mine", "category": ["Other"], "report_id": "CL 70002"}})
        assert r.status_code == 200, r.text
        r = plain.c.post(f"/intake/{d}/finalize", json={"data": draft(report_id="CL 70002")})
        assert r.status_code == 200 and stored(d).data["report_id"] == "CL 70002", r.text
        ok("an Our Ref an admin put on someone's draft survives their autosave and finalize")

        print("renders: per person, and two at once")
        budget = limits._RenderBudget(2, 600)
        now = [1000.0]
        with mock.patch.object(limits.time, "monotonic", lambda: now[0]):
            assert budget.take("a") and budget.take("a") and not budget.take("a") and budget.take("b")
            budget.refund("a")
            assert budget.take("a") and not budget.take("a")
            now[0] += 601
            assert budget.take("a")
        ok("the budget is per account, takes a refund and rolls over")

        rid = new_case(plain)
        limits.renders.reset()
        for _ in range(limits.RENDERS_PER_WINDOW):
            assert limits.renders.take(plain.id)
        r = plain.c.put(f"/reports/{rid}", json={"draft": {"category": ["Other"], "description": "over budget"}})
        assert r.status_code == 200 and r.json()["pdf_url"] is None and stored(rid).pdf_path is None, r.text
        r = plain.c.get(f"/reports/{rid}")
        assert r.status_code == 200 and r.json()["pdf_url"] is None and r.json()["data"]["description"] == "over budget"
        r = plain.c.get(f"/reports/{rid}/download")
        assert r.status_code == 429 and r.json() == {
            "detail": "You've made a lot of PDFs in the last few minutes. Wait a few minutes and try again.",
            "code": "render_limit",
        }, r.text
        r = plain.c.post(f"/reports/{rid}/render-pdf")
        assert r.status_code == 429 and r.json()["code"] == "render_limit"
        made = plain.c.post("/reports", json={"draft": draft()})
        assert made.status_code == 200 and stored(made.json()["id"]).pdf_path is None
        assert counted(plain.id) == limits.RENDERS_PER_WINDOW
        ok("an unvetted account over its share: a save still saves, without a PDF; asking for the PDF is 429 render_limit")

        for who in (admin, signer, seer, ai_only):
            case = new_case(who)
            codes = {who.c.post(f"/reports/{case}/render-pdf").status_code for _ in range(limits.RENDERS_PER_WINDOW + 5)}
            assert codes == {200} and counted(who.id) == 0, (who.id, codes)
        ok(f"admins and accounts with any one tick have no render budget: {limits.RENDERS_PER_WINDOW + 5} in a row all render")

        limits.renders.reset()
        r = plain.c.get(f"/reports/{rid}/download")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and r.content.startswith(b"%PDF")
        files_url = signer.c.post(f"/reports/{new_case(signer)}/render-pdf").json()["pdf_url"]
        assert "immutable" in signer.c.get(files_url).headers["cache-control"]
        ok("later the download renders it, sent with Cache-Control: no-store (/files keeps its caching)")

        assert renderer.RENDER_SLOT_WAIT_SECONDS <= 5 and renderer.BOT_RENDER_SLOT_WAIT_SECONDS >= 60
        held = [renderer._render_slots.acquire(timeout=1) for _ in range(renderer.MAX_CONCURRENT_RENDERS)]
        assert all(held)
        before = counted(plain.id)
        try:
            with mock.patch.object(renderer, "RENDER_SLOT_WAIT_SECONDS", 0.2):
                started = time.monotonic()
                try:
                    renderer.html_to_pdf("<html></html>", str(Path(_tmp, "x.pdf")))
                    raise AssertionError("rendered with every slot taken")
                except renderer.RenderBusy:
                    assert time.monotonic() - started >= 0.2
                r = plain.c.post(f"/reports/{rid}/render-pdf")
                assert r.status_code == 429 and r.json() == {"detail": limits.RENDER_BUSY, "code": "render_busy"}, r.text
                r = plain.c.put(f"/reports/{rid}", json={"draft": {"category": ["Other"], "description": "slots busy"}})
                assert r.status_code == 200 and stored(rid).pdf_path is None, r.text
            # Not patched: opening a case doesn't wait the api's few seconds either.
            started = time.monotonic()
            r = plain.c.get(f"/reports/{rid}")
            assert r.status_code == 200 and r.json()["pdf_url"] is None and time.monotonic() - started < 1.5, r.text
        finally:
            for _ in held:
                renderer._render_slots.release()
        assert counted(plain.id) == before
        renderer.html_to_pdf("<html></html>", str(Path(_tmp, "x.pdf")))
        ok("with both Chromium slots busy a render waits briefly, then gives up: 429 render_busy for render-pdf, "
           "PDF deferred on a save, none on opening a case (which never waits); nothing counted")

        limits.renders.reset()
        for _ in range(limits.RENDERS_PER_WINDOW - 1):
            assert limits.renders.take(plain.id)
        drop_pdf(rid)
        CHROMIUM["fail"] = True
        try:
            assert plain.c.post(f"/reports/{rid}/render-pdf").status_code == 500
            assert plain.c.get(f"/reports/{rid}/download").status_code == 503
            assert plain.c.get(f"/reports/{rid}").json()["pdf_url"] is None
        finally:
            CHROMIUM["fail"] = False
        assert counted(plain.id) == limits.RENDERS_PER_WINDOW - 1
        assert plain.c.post(f"/reports/{rid}/render-pdf").status_code == 200
        r = plain.c.post(f"/reports/{rid}/render-pdf")
        assert r.status_code == 429 and r.json()["code"] == "render_limit", r.text
        ok("a render that fails gives its count back: after 19 and three failures the 20th still renders, the 21st doesn't")

        print("renders: one at a time per person")
        quick = person(name="Quinn Quick")
        first, other = new_case(quick), new_case(quick)
        drop_pdf(first)
        drop_pdf(other)
        signer_case = new_case(signer)
        limits.renders.reset()
        gate = threading.Event()
        CHROMIUM.update(gate=gate, inside=0)
        answers: dict = {}
        threads = [
            threading.Thread(target=lambda: answers.setdefault("first", quick.c.post(f"/reports/{first}/render-pdf"))),
            threading.Thread(target=lambda: answers.setdefault("signer", signer.c.post(f"/reports/{signer_case}/render-pdf"))),
        ]
        try:
            threads[0].start()
            assert wait_until(lambda: CHROMIUM["inside"] == 1), "the first render never reached Chromium"
            started = time.monotonic()
            r = quick.c.post(f"/reports/{first}/render-pdf")
            assert r.status_code == 429 and r.json() == {"detail": limits.RENDER_BUSY, "code": "render_busy"}, r.text
            r = quick.c.get(f"/reports/{other}/download")
            assert r.status_code == 429 and r.json()["code"] == "render_busy", r.text
            r = quick.c.get(f"/reports/{other}")
            assert r.status_code == 200 and r.json()["pdf_url"] is None, r.text
            r = quick.c.put(f"/reports/{other}", json={"draft": {"category": ["Other"], "description": "while rendering"}})
            assert r.status_code == 200 and r.json()["pdf_url"] is None, r.text
            assert time.monotonic() - started < 2, "a refused render waited"
            assert counted(quick.id) == 1  # a refusal takes nothing
            threads[1].start()
            assert wait_until(lambda: CHROMIUM["inside"] == 2), "someone else's render was held up"
        finally:
            gate.set()
            CHROMIUM["gate"] = None
            for t in threads:
                t.join(10)
        assert answers["first"].status_code == 200 and answers["signer"].status_code == 200
        assert quick.c.post(f"/reports/{first}/render-pdf").status_code == 200
        ok("while one of a person's PDFs renders, their next is 429 render_busy at once (download too), opening "
           "and saving skip it; other people render meanwhile; once it's done they can render again")

        print("the Telegram bot still waits for Chromium")
        # Filed by a Telegram connected to an account (P5).
        filer = person(name="Bo Bot", ai_company_key_allowed=True)
        db = SessionLocal()
        try:
            db.add(ChatLink(channel="telegram", external_id="4343", user_id=filer.id))
            db.commit()
        finally:
            db.close()
        linked = tg.Linked(id=filer.id, label="Bo Bot", staff=False)
        photo = Path(_tmp, "tg-photo.jpg")
        photo.write_bytes(jpeg())
        session = tg.get_session("4343")
        session.draft = SecurityIncidentDraft(description="Filed from the bot")
        session.photo_paths = [str(photo)]
        held = [renderer._render_slots.acquire(timeout=1) for _ in range(renderer.MAX_CONCURRENT_RENDERS)]
        assert all(held)
        threading.Timer(1.0, renderer._render_slots.release).start()  # one slot frees up in a second
        try:
            with mock.patch.object(renderer, "RENDER_SLOT_WAIT_SECONDS", 0.3):  # the api would give up first
                started = time.monotonic()
                update = SimpleNamespace(message=None, callback_query=None, effective_user=SimpleNamespace(id=4343))
                asyncio.run(tg._finalize_report(update, session, "4343", linked))
                waited = time.monotonic() - started
        finally:
            renderer._render_slots.release()
        db = SessionLocal()
        try:
            bot_case = db.query(Report).filter(Report.reporter_chat_id == "4343").one()
        finally:
            db.close()
        assert bot_case.pdf_path and Path(bot_case.pdf_path).exists() and waited >= 0.9, (bot_case.pdf_path, waited)
        ok("with both slots busy the bot's render waits past the api's limit for one, and its case gets the PDF")

        print("drafts and new cases")
        def filed(owner, n, age=timedelta(0)):
            db = SessionLocal()
            try:
                when = (datetime.now(timezone.utc) - age).replace(tzinfo=None)
                ids = []
                for _ in range(n):
                    ids.append(gen_id())
                    db.add(Report(id=ids[-1], channel="manual", reporter_chat_id="dashboard", data=draft(),
                                  status="confirmed", owner_id=owner.id, created_at=when))
                db.commit()
                return ids
            finally:
                db.close()

        capped = person(name="Cal Capped")
        early_draft = capped.c.post("/intake").json()["id"]
        filed(capped, 9, age=timedelta(hours=25))
        filed(capped, 9)
        # The 10th in 24 hours (the 9 older ones don't count) -- and the last.
        assert capped.c.post("/reports", json={"draft": draft()}).status_code == 200
        for r in (capped.c.post("/reports", json={"draft": draft()}), capped.c.post("/intake"),
                  capped.c.post(f"/intake/{early_draft}/finalize", json={"data": draft()})):
            assert r.status_code == 409 and r.json()["code"] == "case_limit" and "24 hours" in r.json()["detail"], r.text
        assert capped.c.get(f"/intake/{early_draft}").json()["stage"] == "uploading"  # the draft is as it was
        ok("10 filed cases in 24 hours: the next case, draft or finalize is 409 case_limit; older cases don't count")

        filed(ai_only, 12)
        assert ai_only.c.post("/reports", json={"draft": draft()}).status_code == 200
        ok("any tick lifts the daily cap")

        busy_admin = person("admin", "Bea Busy")
        codes = [busy_admin.c.post("/intake").status_code for _ in range(6)]
        assert codes == [200] * 5 + [409], codes
        db = SessionLocal()
        try:
            before = intake_api._shared_open_drafts(db)
        finally:
            db.close()
        seer.c.post("/intake")
        signer.c.post("/intake")
        ai_only.c.post("/intake")
        db = SessionLocal()
        try:
            after = intake_api._shared_open_drafts(db)
        finally:
            db.close()
        assert after == before + 1, (before, after)
        ok("5 open drafts per person, admins too; only the AI-tick-only draft counts toward the shared 30")

        print("disk guard")
        with mock.patch.object(settings, "min_free_disk_gb", 0.5), low_disk():
            assert not storage_files.disk_space_low()
        with mock.patch.object(shutil, "disk_usage", side_effect=OSError("gone")):
            assert not storage_files.disk_space_low()
        r = ai_only.c.post("/reports/analyze-photos", data={"description": "x"}, files=[("photos", ("a.jpg", jpeg(), "image/jpeg"))])
        temp = r.json()["temp_photo_paths"]
        rid = new_case(ai_only)
        with low_disk():
            r = ai_only.c.put(f"/reports/{rid}", json={"draft": draft(), "temp_photo_paths": temp})
            assert r.status_code == 507 and r.json() == {"detail": "The server is low on storage. Please tell your admin.", "code": "disk_full"}
            assert all(Path(p).exists() for p in temp)
            r = ai_only.c.put(f"/reports/{rid}", json={"draft": draft(description="no photos")})
            assert r.status_code == 200, r.text
        ok("an edit adding photos is refused under the threshold (setting-driven); an edit without photos isn't")

        finisher = person(name="Fay Finisher", ai_company_key_allowed=True)
        d = finisher.c.post("/intake").json()["id"]
        r = finisher.c.post(f"/intake/{d}/photos", files={"file": ("IMG_1.jpg", jpeg(), "image/jpeg")}, data={"position": "1"})
        assert r.status_code == 200, r.text
        before = finisher.c.get(f"/intake/{d}").json()
        with low_disk():
            r = finisher.c.post(f"/intake/{d}/finalize", json={"data": draft()})
            assert r.status_code == 507 and r.json()["code"] == "disk_full", r.text
        after = finisher.c.get(f"/intake/{d}").json()
        assert after["stage"] == before["stage"] and len(after["photos"]) == 1 and after["status"] == "intake", after
        r = finisher.c.post(f"/intake/{d}/finalize", json={"data": draft()})
        assert r.status_code == 200 and stored(d).pdf_path, r.text
        ok("finalize under the threshold is 507 disk_full before the draft is claimed: it stays as it was, and finishes later")

        async def telegram_checks():
            # A Telegram connected to an account with AI (P5): the disk
            # guard is what stops it.
            db = SessionLocal()
            try:
                db.add(ChatLink(channel="telegram", external_id="4242", user_id=finisher.id))
                db.commit()
            finally:
                db.close()
            texts, downloads = [], []

            async def reply(text, **_kw):
                texts.append(text)

            async def get_file():
                downloads.append(1)
                raise AssertionError("downloaded with the disk nearly full")

            def update(**msg):
                message = SimpleNamespace(**{"reply_text": reply, "caption": None, "photo": [], "document": None, **msg})
                return SimpleNamespace(effective_user=SimpleNamespace(id=4242), effective_chat=SimpleNamespace(id=4242, type="private"),
                                       effective_message=message, message=message, callback_query=None)

            with low_disk():
                await tg.handle_photo(update(photo=[SimpleNamespace(get_file=get_file)]), None)
                doc = SimpleNamespace(mime_type="image/jpeg", file_name="a.jpg", get_file=get_file)
                await tg.handle_document(update(document=doc), None)
            assert not downloads and len(texts) == 2 and all("low on storage" in t for t in texts), texts
            assert not tg.get_session("4242").photo_paths

        asyncio.run(telegram_checks())
        ok("the Telegram bot answers politely and downloads nothing")

        print("ticks on a new account")
        def created(**body) -> dict:
            r = admin.c.post("/users", json=body)
            assert r.status_code == 200, r.text
            return r.json()["user"]

        kim = created(email="kim@example.com", name="Kim Staff", sees_all_cases=True, may_sign_off=True)
        lee = created(email="lee@example.com", name="Lee Plain")
        nul = created(email="nul@example.com", name="Nu Null", sees_all_cases=None, ai_company_key_allowed=False)
        boss = created(email="boss@example.com", name="Bo Boss", role="admin", **{t: True for t in TICKS})
        assert [kim[t] for t in TICKS] == [True, False, True], kim
        assert not any(u[t] for u in (lee, nul, boss) for t in TICKS), (lee, nul, boss)
        listed = {u["email"]: u for u in admin.c.get("/users").json()}
        assert [listed["kim@example.com"][t] for t in TICKS] == [True, False, True]
        db = SessionLocal()
        try:
            details = [e.detail for e in db.query(AuditEntry).filter(AuditEntry.action == "user_created").order_by(AuditEntry.id)]
        finally:
            db.close()
        assert details[-4:] == [
            "kim@example.com (user); sees_all_cases: False -> True; may_sign_off: False -> True",
            "lee@example.com (user)",
            "nul@example.com (user)",
            "boss@example.com (admin)",
        ], details[-4:]
        ok("POST /users takes the three ticks (off when left out or null), records them as PATCH does, "
           "and ignores them for an admin")

    print("a real PDF")
    renderer._chromium_pdf = REAL_CHROMIUM_PDF
    from render_sample import SAMPLE_CAR_REPORT

    out = {flag: Path(_tmp, f"real-{flag}.pdf") for flag in (True, False)}
    try:
        for flag, path in out.items():
            renderer.render_pdf(copy.deepcopy(SAMPLE_CAR_REPORT), [], str(path), report_id="unsaved", unverified=flag)
    except RuntimeError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            print("    skip Chromium isn't installed (playwright install chromium)")
            print("\nALL ABUSE-LIMIT CHECKS PASSED (real PDF skipped)")
            return 0
        raise
    try:
        import pymupdf
    except ImportError:
        print("    skip PyMuPDF isn't installed, so the banner wasn't checked page by page")
        print("\nALL ABUSE-LIMIT CHECKS PASSED (page-by-page banner check skipped)")
        return 0
    doc = pymupdf.open(out[True])
    assert doc.page_count > 1
    for number, page in enumerate(doc, start=1):
        hits = page.search_for("Prepared by an unverified user")
        assert hits, f"no banner on page {number}"
        margin_banner = max(hits, key=lambda h: h.y0)
        content = [b for b in page.get_text("blocks") if "unverified user" not in b[4]]
        assert all(b[3] <= margin_banner.y0 for b in content), f"the banner overlaps content on page {number}"
        assert BANNER in " ".join(page.get_text().split())
    assert all(not page.search_for("unverified") for page in pymupdf.open(out[False]))
    ok(f"unverified: the banner is on all {doc.page_count} pages, below everything else; verified: nowhere")

    print("\nALL ABUSE-LIMIT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
