"""Staff Telegram reports through the website's intake pipeline (contract
10, agent T: settings.telegram_full_detection; app/intake/telegram_bridge.py
and channels/telegram._detect_and_reply).

    python scripts/test_telegram_full.py

Off (the default): the bot drafts and files exactly as before -- the same
replies, one drafting call, Confirm files the case -- and nothing of the
bridge runs. On: a staff report's photos and words become an intake draft
(the wizard's rows, owned by the linked person) and the website's pipeline
reads it (sort, main read, second look with the linked parts) on that
person's AI; the chat gets "Reading N photos... (about 2 min)", then the
parts, the suggestions to decide on the website and the link. A failure
never loses the photos: before the draft is saved they stay in the chat,
after it they are the draft's (the website's Retry). Public reports keep
their own path. The bot's token is never logged.

Offline: fake Telegram updates and downloads; Gemini is the fake
interactions client (as scripts/test_intake_flow.py's create(**settings));
a temporary database and storage folder; no PDF render.
"""
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="telegram-full-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-company-key"
TOKEN = "123456:fake-token-for-full-detection-tests"
os.environ["TELEGRAM_BOT_TOKEN"] = TOKEN
os.environ["PUBLIC_BASE_URL"] = "https://carlink.test/"
os.environ["MIN_FREE_DISK_GB"] = "0"

from PIL import Image  # noqa: E402

import app.ai.structured as structured  # noqa: E402

# --- the fake Gemini (create(**settings), as test_intake_flow.py) -------------

CALLS = {"sort": 0, "draft": 0, "fail": None, "gate": None, "keys": [], "draft_text": []}


class FakeInteractions:
    def __init__(self, key):
        self.key = key

    def create(self, *, model, system_instruction, input, response_format, timeout, **settings):
        CALLS["keys"].append(getattr(self.key, "source", None))
        gate = CALLS["gate"]
        if gate is not None:
            assert gate.wait(30), "the test never let the reading go on"
        if CALLS["fail"]:
            raise RuntimeError(CALLS["fail"])
        labels = [m.group(1) for part in input if part.get("type") == "text"
                  for m in [re.match(r"Photo (P\d+):", part["text"])] if m]
        if "photos" in response_format["schema"].get("properties", {}):
            CALLS["sort"] += 1
            out = [{"label": label, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True,
                    "part": "Rear Bumper", "note": f"photo {label}"} for label in labels]
            return SimpleNamespace(output_text=json.dumps({"photos": out}))
        CALLS["draft"] += 1
        CALLS["draft_text"].append("\n".join(p["text"] for p in input if p.get("type") == "text"))
        draft = {
            "description": "The insured car was hit at the rear.",
            "category": ["Vehicle Collision or Damage"],
            "damaged_parts": ["Rear Bumper", "Boot Lid"],
            "damage_summary": [
                {"part": "Rear Bumper", "severity": "Severe", "damage_type": "Dent", "condition": "Crumpled",
                 "photo_reference": labels[0] if labels else None, "ai_confidence": "High"},
                {"part": "Boot Lid", "severity": "Moderate", "damage_type": "Dent",
                 "photo_reference": labels[-1] if labels else None},
            ],
            # The AI reads a plate off the photo; the one typed in the chat wins.
            "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": "SNA1234B"},
        }
        return SimpleNamespace(output_text=json.dumps(draft))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions(key))
structured.acquire = lambda key=None: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

import app.channels.telegram as tg  # noqa: E402
import app.channels.telegram_public as tg_public  # noqa: E402
from app.api import limits  # noqa: E402
from app.auth.core import utc_now  # noqa: E402
from app.config import settings  # noqa: E402
from app.conversation import state  # noqa: E402
from app.conversation.flow import TEMPLATE_PROMPT, DraftResult  # noqa: E402
from app.conversation.state import Stage  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.intake import telegram_bridge as bridge  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, AuditEntry, ChatLink, IntakePhoto, IntakeSession, Report, User  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import preview_path_for, tmp_dir  # noqa: E402

BASE = "https://carlink.test"
ANN_TG, BEN_TG, CAL_TG, DOT_TG, URI_TG = 2001, 2002, 2003, 2004, 2005
STRANGER = 6666
P = SimpleNamespace()

TEMPLATE_REPLY = (
    "Vehicle Plate: SKA1234X\n"
    "Requested by (insurer / company): Acme Insurance\n"
    "Type of claim (Own damage / Third party): Own damage\n"
    "Workshop: XYZ Auto\n"
    "Date/time of accident: 3 Nov 2026 16:45\n"
    "Damaged Side (Left / Right / Front / Rear): Rear\n"
    "What happened: Rear-ended at a junction"
)


def ok(label: str) -> None:
    print(f"    ok   {label}")


# --- people ---------------------------------------------------------------------

def make_user(name: str, username: str, **ticks) -> str:
    db = SessionLocal()
    try:
        user = User(email=f"{username}@example.com", username=username, name=name, role="user", password_hash="!", **ticks)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def link_tg(user_id: str, tg_id: int) -> None:
    db = SessionLocal()
    try:
        db.add(ChatLink(channel="telegram", external_id=str(tg_id), user_id=user_id, linked_at=utc_now()))
        db.commit()
    finally:
        db.close()


def set_user(user_id: str, **values) -> None:
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == user_id).update(values)
        db.commit()
    finally:
        db.close()


def make_people() -> None:
    # Ticked for the company key (GEMINI_API_KEY above), so AI is on.
    P.ann = make_user("Ann Lim", "ann", ai_company_key_allowed=True)
    P.ben = make_user("Ben Tan", "ben", ai_company_key_allowed=True, may_sign_off=True)
    P.cal = make_user("Cal Ong", "cal", ai_company_key_allowed=True)
    P.dot = make_user("Dot Lee", "dot", ai_company_key_allowed=True)
    P.uri = make_user("Uri Goh", "uri", ai_company_key_allowed=True)
    for user_id, tg_id in ((P.ann, ANN_TG), (P.ben, BEN_TG), (P.cal, CAL_TG), (P.dot, DOT_TG), (P.uri, URI_TG)):
        link_tg(user_id, tg_id)


# --- fake Telegram (as scripts/test_telegram_bot.py) ---------------------------

class Outbox:
    def __init__(self):
        self.texts: list[str] = []
        self.kw: list[dict] = []
        self.downloads: list[str] = []


_shade = [0]


class FakeFile:
    def __init__(self, outbox: Outbox):
        self.outbox = outbox

    async def download_to_drive(self, path):
        # A different picture each time: the draft keeps the same photo once.
        _shade[0] += 1
        Image.new("RGB", (64, 48), ((_shade[0] * 37) % 256, (_shade[0] * 91) % 256, 120)).save(path, "JPEG")
        self.outbox.downloads.append(path)


class FakePhoto:
    def __init__(self, outbox: Outbox):
        self.outbox = outbox

    async def get_file(self):
        return FakeFile(self.outbox)


class FakeMessage:
    def __init__(self, outbox: Outbox, text=None, caption=None, photo=False):
        self.outbox = outbox
        self.text = text
        self.caption = caption
        self.photo = [FakePhoto(outbox)] if photo else []
        self.document = None
        self.forward_origin = None

    async def reply_text(self, text, **kw):
        await asyncio.sleep(0)
        self.outbox.texts.append(text)
        self.outbox.kw.append(kw)


def message_update(chat_id: int, outbox: Outbox, **message_kw):
    msg = FakeMessage(outbox, **message_kw)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=chat_id, username=f"tg{chat_id}", full_name=f"Phone {chat_id}", first_name="P"),
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        effective_message=msg,
        message=msg,
        callback_query=None,
    )


async def send_photo(chat_id: int, outbox: Outbox) -> None:
    await tg.handle_photo(message_update(chat_id, outbox, photo=True), None)


async def send_text(chat_id: int, outbox: Outbox, text: str) -> None:
    await tg.handle_text(message_update(chat_id, outbox, text=text), None)


async def watchers_done(timeout: float = 30) -> None:
    if tg._watchers:
        await asyncio.wait_for(asyncio.gather(*list(tg._watchers)), timeout)


async def until(check, timeout: float = 20) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out waiting"
        await asyncio.sleep(0.01)


def drafts_of(user_id: str) -> list[Report]:
    db = SessionLocal()
    try:
        return db.query(Report).filter(Report.owner_id == user_id, Report.status == "intake").all()
    finally:
        db.close()


def intake_folders() -> set[str]:
    root = Path(_tmp, "intake")
    return {p.name for p in root.iterdir()} if root.is_dir() else set()


def temp_photos() -> set[str]:
    return {p.name for p in tmp_dir().glob("tg-*")}


DRAFT_CALLS: list = []


def fake_build_draft(description, photo_paths, session=None, **kw):
    DRAFT_CALLS.append((description, list(photo_paths), kw.get("actor")))
    draft = SecurityIncidentDraft(description=description)
    return DraftResult(draft=draft, summary_text="Draft summary", ai_draft=draft.model_dump())


def fake_render_pdf(data, photo_paths, output_path, report_id=None, unverified=None, slot_wait=None):
    Path(output_path).write_bytes(b"%PDF-1.4 fake")


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def text(self) -> str:
        out = []
        for r in self.records:
            out.append(r.getMessage())
            if r.exc_info:
                out.append(logging.Formatter().formatException(r.exc_info))
        return "\n".join(out)


def must_not_run(*_a, **_kw):
    raise AssertionError("the full-detection bridge ran with the setting off")


# --- checks ---------------------------------------------------------------------

async def check_off_is_today() -> None:
    print("setting off: today's path")
    assert settings.telegram_full_detection is False
    out = Outbox()
    before_runs = CALLS["sort"] + CALLS["draft"]
    with mock.patch.object(bridge, "create_draft", must_not_run), mock.patch.object(bridge, "start", must_not_run), \
            mock.patch.object(tg, "_detect_and_reply", must_not_run):
        await send_photo(ANN_TG, out)
        await send_text(ANN_TG, out, "Rear-ended at PIE exit 12")
    assert out.texts == [
        "Got it -- 1 photo(s) received. Send more or describe what happened.",
        TEMPLATE_PROMPT,
        "Drafting your report...",
        "👤 Filing as @ann (Ann Lim)\n\nDraft summary",
    ], out.texts
    session = state.get_session(str(ANN_TG))
    assert session.stage == Stage.AWAITING_CONFIRMATION and session.draft is not None
    assert len(DRAFT_CALLS) == 1 and DRAFT_CALLS[0][0] == "Rear-ended at PIE exit 12"
    assert DRAFT_CALLS[0][2].user_id == P.ann and DRAFT_CALLS[0][2].channel == "telegram"
    assert drafts_of(P.ann) == [] and CALLS["sort"] + CALLS["draft"] == before_runs
    assert intake_folders() == set()
    ok("off: the same four replies as before, one drafting call on the person's AI, no intake draft, no pipeline")

    # Review M2 (photo compression, 24 Nov 2026): Confirm saves the photos
    # (thumbnail, preview, compression) in a thread, not on the bot's loop.
    saved: list[tuple[int, bool]] = []
    real_save = tg.save_photo

    def recording_save(report_id, path, index):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        saved.append((index, on_loop))
        return real_save(report_id, path, index)

    sent_photos = list(session.photo_paths)
    with mock.patch.object(tg, "save_photo", recording_save):
        await tg.handle_callback(SimpleNamespace(
            effective_user=SimpleNamespace(id=ANN_TG, username="tgann", full_name="Phone", first_name="P"),
            effective_chat=SimpleNamespace(id=ANN_TG, type="private"),
            effective_message=None, message=None,
            callback_query=SimpleNamespace(data="confirm", message=FakeMessage(out), answer=mock.AsyncMock()),
        ), None)
    db = SessionLocal()
    try:
        filed = db.query(Report).filter(Report.owner_id == P.ann, Report.status == "confirmed").all()
    finally:
        db.close()
    assert len(filed) == 1 and filed[0].channel == "telegram", filed
    assert out.texts[-1].startswith("✅ Report saved to your cases in Carlink."), out.texts[-1]
    assert str(ANN_TG) not in tg._detecting and not tg._watchers
    ok("off: Confirm files the case in the chat as before")
    assert saved == [(i, False) for i in range(1, len(sent_photos) + 1)], saved
    assert [Path(p).name for p in filed[0].photo_paths] == [f"photo_{i:02d}.jpg" for i in range(1, len(sent_photos) + 1)]
    ok("Confirm saves the photos off the bot's event loop (a thread), in the same order, as photo_01...")


async def hand_over(chat_id: int, out: Outbox, photos: int = 3, text: str = TEMPLATE_REPLY) -> None:
    for _ in range(photos):
        await send_photo(chat_id, out)
    await send_text(chat_id, out, text)


async def check_on_reads_like_the_website(log: LogCapture) -> None:
    print("setting on: the website's detection")
    out = Outbox()
    sorts, reads = CALLS["sort"], CALLS["draft"]
    CALLS["keys"].clear()
    await hand_over(ANN_TG, out)
    assert out.texts[:2] == ["Got it -- 1 photo(s) received. Send more or describe what happened.", TEMPLATE_PROMPT]
    assert out.texts[4] == (
        "📸 Reading 3 photos… (about 2 min)\n👤 Draft for @ann (Ann Lim)\nI'll message you here when it's done."
    ), out.texts[4]
    # The chat starts afresh at once: the photos are the draft's.
    session = state.get_session(str(ANN_TG))
    assert session.photo_paths == [] and session.draft is None and temp_photos() == set()
    [draft] = drafts_of(P.ann)
    assert tg._detecting.get(str(ANN_TG)) == draft.id or not pipeline.is_running(draft.id)
    ok("the reply: 'Reading 3 photos… (about 2 min)', for the linked account; the chat's temp photos handed over")

    await watchers_done()
    summary = out.texts[-1]
    link = f"{BASE}/reports/{draft.id}"
    assert summary.startswith("✅ Finished reading your 3 photos.\n👤 Draft for @ann (Ann Lim)"), summary
    assert "🔧 2 damaged parts found" in summary and "🔴 Rear Bumper · Dented · Dent" in summary, summary
    assert "🟠 Boot Lid · Dented · Dent" in summary and "🚗 SKA1234X · Honda Vezel" in summary, summary
    assert summary.endswith(f"Open the draft to check it, decide the suggestions and file the case:\n{link}"), summary
    assert "suggestion" not in summary.split("\n\n")[1], summary  # links off: none to decide
    assert out.kw[-1].get("link_preview_options") is not None and "parse_mode" not in out.kw[-1]
    ok("the summary: parts found with severity and condition, the typed plate, the link to the draft (plain text)")

    db = SessionLocal()
    try:
        report = db.get(Report, draft.id)
        session_row = db.get(IntakeSession, draft.id)
        photos = db.query(IntakePhoto).filter(IntakePhoto.report_id == draft.id).order_by(IntakePhoto.position).all()
        runs_ = db.query(AiRun).filter(AiRun.report_id == draft.id).all()
        created = db.query(AuditEntry).filter(AuditEntry.report_id == draft.id, AuditEntry.action == "created").all()
        data = report.data
        assert report.channel == "telegram" and report.reporter_chat_id == str(ANN_TG) and report.status == "intake"
        assert report.owner_id == P.ann and session_row.stage == "ready", (report.owner_id, session_row.stage)
        assert len(photos) == 3 and [p.position for p in photos] == [0, 1, 2]
        assert all(Path(p.path).is_file() and preview_path_for(p.path).is_file() for p in photos)
        assert {p.kind for p in photos} == {"damage"} and all(p.part == "Rear Bumper" for p in photos)
        assert session_row.read_photo_ids == [p.id for p in photos]
        # The template's facts are the form's, over what the AI read.
        assert data["vehicle_info"]["plate_number"] == "SKA1234X", data["vehicle_info"]
        assert data["cover_page"]["requested_by"] == "Acme Insurance"
        assert data["insurance_details"]["claim_type"] == "Own damage"
        assert data["insurance_details"]["workshop_assigned"] == "XYZ Auto"
        assert data["incident_datetime"] == "3 Nov 2026 16:45"
        assert [it["part"] for it in data["damage_summary"]] == ["Rear Bumper", "Boot Lid"]
        assert data["damage_summary"][0]["photo_reference"] == f"@{photos[0].id}"
        assert "template_fields" in data  # the static check's usual wording, as a wizard draft starts
        assert session_row.reporter_notes == (
            "Rear-ended at a junction\nDamaged side (stated by the reporter at the car): Rear"
        ), session_row.reporter_notes
        assert {r.kind for r in runs_} >= {"sort", "read"}, {r.kind for r in runs_}
        assert all(r.channel == "telegram" and r.actor_user_id == P.ann and r.key_source == "company" for r in runs_), [(r.channel, r.actor_user_id, r.key_source) for r in runs_]
        assert len(created) == 1 and created[0].user_id == P.ann and created[0].detail == bridge.CREATED_DETAIL
    finally:
        db.close()
    assert CALLS["sort"] == sorts + 1 and CALLS["draft"] == reads + 1
    assert set(CALLS["keys"]) == {"company"}, CALLS["keys"]
    assert "Damaged side (stated by the reporter at the car): Rear" in CALLS["draft_text"][-1]
    assert str(ANN_TG) not in tg._detecting and limits.ai_running.claim(P.ann)
    limits.ai_running.release(P.ann)
    assert TOKEN not in log.text() and TOKEN.split(":")[1] not in log.text()
    ok("an intake draft like the wizard's: the person's, sorted and read on their AI (company key, run log "
       "'telegram'), the typed facts kept, the stated side in the notes; one reading at a time released; no token logged")


async def check_linked_parts_summary() -> None:
    out = Outbox()
    with mock.patch.object(settings, "intake_links", "on"):
        await hand_over(BEN_TG, out, photos=2)
        await watchers_done()
    summary = out.texts[-1]
    [draft] = drafts_of(P.ben)
    db = SessionLocal()
    try:
        look = db.get(Report, draft.id).data["second_look"]
    finally:
        db.close()
    linked = [it for it in look["items"] if it.get("source") == "link"]
    assert linked, look
    assert f"💡 {len(look['items'])} suggestions to decide on the website ({len(linked)} linked to parts on the list)" \
        in summary, summary
    lines = summary.split("\n")
    names = lines[lines.index(next(line for line in lines if line.startswith("💡"))) + 1]
    assert names.startswith(str(look["items"][0].get("name") or look["items"][0]["part"])), (names, summary)
    shown = min(len(look["items"]), 5)
    assert names.count(", ") == shown - 1 and (f"+{len(look['items']) - shown} more" in names) == (len(look["items"]) > 5)
    assert not any(it["part"] in [d["part"] for d in db_parts(draft.id)] for it in linked)
    ok(f"linked parts on: the summary counts {len(look['items'])} suggestions to decide on the website, "
       f"{len(linked)} linked, none ticked onto the list")


def db_parts(report_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        return db.get(Report, report_id).data["damage_summary"]
    finally:
        db.close()


async def check_run_failure_keeps_photos() -> None:
    print("failures keep the photos")
    out = Outbox()
    CALLS["fail"] = "503 UNAVAILABLE high demand"
    try:
        await hand_over(CAL_TG, out, photos=2)
        await watchers_done()
    finally:
        CALLS["fail"] = None
    [draft] = drafts_of(P.cal)
    text = out.texts[-1]
    assert text.startswith("⚠️ I couldn't finish reading the photos.\nThe AI is busy or today's quota is used up."), text
    assert text.endswith(f"\n\nYour 2 photos are in the draft, with Retry on its page:\n{BASE}/reports/{draft.id}"), text
    db = SessionLocal()
    try:
        session_row = db.get(IntakeSession, draft.id)
        photos = db.query(IntakePhoto).filter(IntakePhoto.report_id == draft.id).all()
        assert session_row.stage == "failed" and session_row.error, session_row.stage
        assert len(photos) == 2 and all(Path(p.path).is_file() for p in photos)
    finally:
        db.close()
    assert limits.ai_running.claim(P.cal)
    limits.ai_running.release(P.cal)
    ok("the AI fails mid-run: the chat says why, the 2 photos stay in the draft for the website's Retry")

    # The person's AI goes off mid-run (an admin's untick): the next call's
    # keys are looked up again, and the run stops with the reason's code.
    out = Outbox()
    gate = CALLS["gate"] = threading.Event()
    try:
        await hand_over(CAL_TG, out, photos=1, text="Scraped at the carpark")
        set_user(P.cal, ai_company_key_allowed=False)
        gate.set()
        await watchers_done()
    finally:
        CALLS["gate"] = None
        gate.set()
        set_user(P.cal, ai_company_key_allowed=True)
    text = out.texts[-1]
    assert text.startswith("⚠️ I couldn't finish reading the photos.\n"), text
    assert text.endswith(f"\n\nYour AI settings: {BASE}/settings#my-ai-key"), text
    db = SessionLocal()
    try:
        failed = [db.get(IntakeSession, d.id) for d in drafts_of(P.cal)]
        assert any(s.stage == "failed" and s.error_code and s.error_by == P.cal for s in failed)
    finally:
        db.close()
    ok("the person's AI goes off mid-run: the reason, and where to turn it back on")


async def check_refusals_keep_the_chat() -> None:
    # AI switched off between the photos and the words.
    out = Outbox()
    await send_photo(DOT_TG, out)
    set_user(P.dot, ai_company_key_allowed=False)
    await send_text(DOT_TG, out, "Rear-ended at a junction")
    set_user(P.dot, ai_company_key_allowed=True)
    assert "AI is off for your account" in out.texts[-1], out.texts[-1]
    session = state.get_session(str(DOT_TG))
    assert len(session.photo_paths) == 1 and Path(session.photo_paths[0]).is_file()
    assert drafts_of(P.dot) == [] and limits.ai_running.claim(P.dot)
    limits.ai_running.release(P.dot)
    ok("AI off at hand-over: told how to turn it on; the photo stays in the chat, no draft")

    # The draft can't be saved (a disk error while copying).
    folders = intake_folders()
    with mock.patch.object(bridge, "store_photos", side_effect=OSError("disk error")):
        await send_text(DOT_TG, out, "Rear-ended at a junction")
    assert out.texts[-1] == tg.DRAFT_NOT_SAVED, out.texts[-1]
    assert len(session.photo_paths) == 1 and Path(session.photo_paths[0]).is_file()
    assert drafts_of(P.dot) == [] and intake_folders() == folders and limits.ai_running.claim(P.dot)
    limits.ai_running.release(P.dot)
    ok("the draft can't be saved: 'send the description again'; the photo stays, no folder left behind")

    # The website's cap on open drafts.
    db = SessionLocal()
    try:
        for _ in range(limits.MAX_OPEN_DRAFTS_PER_PERSON):
            db.add(Report(channel="manual", reporter_chat_id="dashboard", data={}, status="intake", owner_id=P.dot))
        db.commit()
    finally:
        db.close()
    await send_text(DOT_TG, out, "Rear-ended at a junction")
    assert out.texts[-1].startswith(f"You already have {limits.MAX_OPEN_DRAFTS_PER_PERSON} unfinished drafts"), out.texts[-1]
    assert len(session.photo_paths) == 1 and Path(session.photo_paths[0]).is_file()
    assert len(drafts_of(P.dot)) == limits.MAX_OPEN_DRAFTS_PER_PERSON and intake_folders() == folders
    db = SessionLocal()
    try:
        db.query(Report).filter(Report.owner_id == P.dot, Report.status == "intake").delete()
        db.commit()
    finally:
        db.close()
    await send_text(DOT_TG, out, "Rear-ended at a junction")
    await watchers_done()
    assert out.texts[-1].startswith("✅ Finished reading your 1 photo."), out.texts[-1]
    ok("five open drafts: the website's words, the photo kept; with room again, the same chat reads it")

    # Saved, but the run can't start (no thread): the draft says Retry.
    await send_photo(DOT_TG, out)
    with mock.patch.object(pipeline, "start_reserved", side_effect=RuntimeError("can't start a thread")):
        await send_text(DOT_TG, out, "Rear-ended at a junction")
    newest = max(drafts_of(P.dot), key=lambda r: r.created_at)
    assert out.texts[-1] == ("Your 1 photo is saved in Carlink as a draft, but I couldn't start reading it. "
                             f"Open the draft and press Retry:\n{BASE}/reports/{newest.id}"), out.texts[-1]
    db = SessionLocal()
    try:
        row = db.get(IntakeSession, newest.id)
        assert row.stage == "failed" and row.error == bridge.NOT_STARTED, (row.stage, row.error)
    finally:
        db.close()
    assert not pipeline.is_running(newest.id) and str(DOT_TG) not in tg._detecting and limits.ai_running.claim(P.dot)
    limits.ai_running.release(P.dot)
    ok("the run can't start: the draft (with its photo) is marked for the website's Retry, and the chat says so")

    # The link ends between the gate and the save (step 8 under the write lock).
    await send_photo(DOT_TG, out)
    temp = list(state.get_session(str(DOT_TG)).photo_paths)
    with mock.patch.object(tg, "_may_file", lambda db, external_id, user_id: (None, tg.NO_LONGER_CONNECTED)):
        await send_text(DOT_TG, out, "Rear-ended at a junction")
    assert out.texts[-1] == tg.NO_LONGER_CONNECTED
    assert str(DOT_TG) not in state._sessions and not any(Path(p).exists() for p in temp)
    assert len(drafts_of(P.dot)) == 2  # the one read, the one that never started
    ok("the link ended meanwhile: nothing saved, the chat's draft dropped as at Confirm")


async def check_while_reading() -> None:
    print("while a reading runs")
    out = Outbox()
    gate = CALLS["gate"] = threading.Event()
    try:
        with mock.patch.object(tg, "DETECTION_POLL_SECONDS", 0.01), \
                mock.patch.object(tg, "DETECTION_HEARTBEAT_SECONDS", 0.0):
            await hand_over(URI_TG, out, photos=2)
            [draft] = drafts_of(P.uri)
            first_beat = _updated_at(draft.id)
            await asyncio.sleep(0.05)
            await until(lambda: _updated_at(draft.id) > first_beat)
            await send_text(URI_TG, out, "the plate is SKA1234X")
            assert out.texts[-1] == tg.STILL_READING, out.texts[-1]
            # The next report: its photos come in; its words wait their turn.
            await send_photo(URI_TG, out)
            assert out.texts[-2] == "Got it -- 1 photo(s) received. Send more or describe what happened."
            await send_text(URI_TG, out, "Scraped a pillar at the carpark")
            assert out.texts[-1] == tg.EARLIER_STILL_READING, out.texts[-1]
            session = state.get_session(str(URI_TG))
            assert len(session.photo_paths) == 1 and Path(session.photo_paths[0]).is_file()
            assert len(drafts_of(P.uri)) == 1
            gate.set()
            await watchers_done()
    finally:
        CALLS["gate"] = None
        gate.set()
    assert out.texts[-1].startswith("✅ Finished reading your 2 photos."), out.texts[-1]
    ok("text during the reading: 'still reading'; a second report waits (photos kept); the heartbeat runs")

    await send_text(URI_TG, out, "Scraped a pillar at the carpark")
    await watchers_done()
    assert out.texts[-1].startswith("✅ Finished reading your 1 photo."), out.texts[-1]
    assert len(drafts_of(P.uri)) == 2
    ok("once the first is read, the second report's words start its own reading")


def _updated_at(report_id: str):
    db = SessionLocal()
    try:
        return db.get(IntakeSession, report_id).updated_at
    finally:
        db.close()


async def check_slow_and_deleted() -> None:
    out = Outbox()
    gate = CALLS["gate"] = threading.Event()
    try:
        with mock.patch.object(tg, "DETECTION_POLL_SECONDS", 0.01), mock.patch.object(tg, "DETECTION_NUDGE_SECONDS", 0.05), \
                mock.patch.object(tg, "DETECTION_WAIT_SECONDS", 0.2):
            await hand_over(ANN_TG, out, photos=1, text="Scraped at the carpark")
            await watchers_done()
        [draft] = [d for d in drafts_of(P.ann) if pipeline.is_running(d.id)]
        assert out.texts[-2] == tg.STILL_READING_NUDGE, out.texts[-2:]
        assert out.texts[-1] == ("⏳ The reading is taking longer than usual. It will be on the website when it's done:\n"
                                 f"{BASE}/reports/{draft.id}"), out.texts[-1]
        assert str(ANN_TG) not in tg._detecting
    finally:
        gate.set()
        CALLS["gate"] = None
    await until(lambda: not pipeline.is_running(draft.id))
    ok("a slow reading: one 'still reading' note, then the link; the reading itself carries on")

    # Deleted on the website mid-run.
    gate = CALLS["gate"] = threading.Event()
    try:
        with mock.patch.object(tg, "DETECTION_POLL_SECONDS", 0.01):
            await hand_over(ANN_TG, out, photos=1, text="Scraped at the carpark")
            [draft] = [d for d in drafts_of(P.ann) if pipeline.is_running(d.id)]
            from app.api.intake import delete_intake_files

            db = SessionLocal()
            try:
                delete_intake_files(db, draft.id)
                db.query(Report).filter(Report.id == draft.id).delete()
                db.commit()
            finally:
                db.close()
            gate.set()
            await watchers_done()
    finally:
        CALLS["gate"] = None
        gate.set()
    assert out.texts[-1] == "The draft for this report was deleted in Carlink before the reading finished.", out.texts[-1]
    ok("deleted on the website mid-run: the chat is told, nothing else")


async def check_public_keeps_its_path() -> None:
    print("public reports")
    out = Outbox()
    with mock.patch.object(bridge, "create_draft", must_not_run), mock.patch.object(tg, "_detect_and_reply", must_not_run):
        await send_photo(STRANGER, out)
        await send_text(STRANGER, out, "my car was hit")
    assert out.downloads == [] and str(STRANGER) not in state._sessions
    assert out.kw and all(isinstance(kw.get("reply_markup"), object) for kw in out.kw)
    assert any("privacy" in t for t in out.texts), out.texts
    db = SessionLocal()
    try:
        assert db.query(Report).filter(Report.reporter_chat_id == str(STRANGER)).count() == 0
    finally:
        db.close()
    ok("someone not connected still gets the public form's welcome: no download, no draft, no AI")


async def run_checks() -> None:
    log = LogCapture()
    root = logging.getLogger()
    root.addHandler(log)
    root.setLevel(logging.DEBUG)
    with mock.patch.object(tg, "build_draft", fake_build_draft), mock.patch.object(tg, "render_pdf", fake_render_pdf), \
            mock.patch.object(tg, "estimate_wait", lambda _key=None: 0.0), \
            mock.patch.object(tg, "BURST", 10**6), mock.patch.object(tg, "BURST_LINKED", 10**6):
        await check_off_is_today()
        with mock.patch.object(settings, "telegram_full_detection", True), \
                mock.patch.object(tg, "DETECTION_POLL_SECONDS", 0.01), \
                mock.patch.object(tg, "build_draft", side_effect=AssertionError("drafted in the chat with the setting on")):
            await check_on_reads_like_the_website(log)
            await check_linked_parts_summary()
            await check_run_failure_keeps_photos()
            await check_refusals_keep_the_chat()
            await check_while_reading()
            await check_slow_and_deleted()
            await check_public_keeps_its_path()
    assert TOKEN not in log.text()
    ok("the bot's token is in no log line")


def main() -> int:
    init_db()
    make_people()
    asyncio.run(run_checks())
    print("\nALL TELEGRAM FULL-DETECTION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
