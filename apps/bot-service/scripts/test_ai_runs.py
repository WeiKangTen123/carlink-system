"""The AI run log (round 3, B3: app/ai/runs.py, models.AiRun), offline with
a fake Gemini: one row per intake stage (sort, read, re-read as redraft),
every model try recorded (a 429 then ok, with the wait), tokens when the
response reports them, a failure row with its code and a scrubbed error,
rows kept by the case at filing with photo_index read flags, GET
/reports/{id}'s ai_runs and ai_last_read, a run left "running" shown as
aborted, Annex A suggestions, start_run / finish_run for Phase 2, GET
/ai-usage's sums and its admin gate, and a public report's run holding
nothing of what its sender sent. (The Telegram bot's rows:
scripts/test_telegram_bot.py.)

    python scripts/test_ai_runs.py
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="ai-runs-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
# Key-shaped, so "the key is never stored" means something (its last 4: 1234).
COMPANY_KEY = "AIzaTestCompanyKeyForTheRunLog000001234"
os.environ["GEMINI_API_KEY"] = COMPANY_KEY
os.environ["MIN_FREE_DISK_GB"] = "0"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import app.ai.structured as structured  # noqa: E402

# What the fake does on each drafting call: a dict answers, an exception is
# raised, a str is returned as the raw answer. Empty: MODE decides.
DRAFTS: list = []
MODE = {"fail": None, "raw": None}
ANNEX = {"parts": [{"part": "Rear Bumper", "lines": [{"name": "Rear bumper", "qty": 1}, {"name": "Bumper clips", "qty": 6}]}]}


class FakeInteractions:
    def create(self, *, model, system_instruction, input, response_format, timeout):
        import re

        props = response_format["schema"].get("properties", {})
        if "photos" in props:  # sorting (ai/triage.py): no usage reported
            labels = [m.group(1) for part in input if part.get("type") == "text"
                      for m in [re.match(r"Photo (P\d+):", part["text"])] if m]
            return SimpleNamespace(output_text=json.dumps({"photos": [
                {"label": label, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True, "note": None}
                for label in labels
            ]}))
        usage = SimpleNamespace(total_input_tokens=1200, total_output_tokens=300)
        if set(props) == {"parts"}:  # Annex A suggestions
            return SimpleNamespace(output_text=json.dumps(ANNEX), usage=SimpleNamespace(total_input_tokens=50, total_output_tokens=20))
        if DRAFTS:
            step = DRAFTS.pop(0)
        elif MODE["fail"]:
            step = RuntimeError(MODE["fail"])
        elif MODE["raw"]:
            step = MODE["raw"]
        else:
            step = draft()
        if isinstance(step, BaseException):
            raise step
        return SimpleNamespace(output_text=step if isinstance(step, str) else json.dumps(step), usage=usage)


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.ai import runs  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.api.main import app  # noqa: E402
from app.intake import public  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import AiRun, AppSetting, Report  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

RUN_KEYS = {"id", "kind", "channel", "started_at", "finished_at", "status", "error_code", "error_text", "model_used",
            "photos_sent", "photos_count", "parts_found", "actor_name", "key_source", "key_last4"}
USAGE_KEYS = {"days", "runs", "failed", "failed_by_code", "waits_429", "waited_s", "tokens_in", "tokens_out",
              "by_key", "by_kind", "by_user", "failures"}


def ok(msg: str) -> None:
    print(f"    ok   {msg}")


def draft(**overrides) -> dict:
    d = {
        "description": "Rear-ended at a junction.", "category": ["Vehicle Collision or Damage"],
        "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": "SKA1234Z"},
        "damage_summary": [{"part": "Rear Bumper", "severity": "Moderate"}, {"part": "Boot Lid", "severity": "Minor"}],
        "damaged_parts": ["Rear Bumper", "Boot Lid"],
    }
    d.update(overrides)
    return d


def jpeg(color: tuple) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def wait_done(client, rid, timeout=30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/intake/{rid}").json()
        if s["stage"] in ("ready", "failed"):
            return s
        time.sleep(0.1)
    raise AssertionError("timed out")


def new_draft(client, n_photos=2) -> str:
    rid = client.post("/intake").json()["id"]
    for i in range(n_photos):
        r = client.post(f"/intake/{rid}/photos", files={"file": (f"p{i}.jpg", jpeg((40 * i + 10, 80, 120)), "image/jpeg")},
                        data={"position": str(i)})
        assert r.status_code == 200, r.text
    return rid


def rows_for(report_id: str) -> list[AiRun]:
    db = SessionLocal()
    try:
        return db.query(AiRun).filter(AiRun.report_id == report_id).order_by(AiRun.started_at).all()
    finally:
        db.close()


def row_text(row: AiRun) -> str:
    """Every column of a row as one string, to look for what mustn't be there."""
    return json.dumps({c.name: getattr(row, c.name) for c in AiRun.__table__.columns}, default=str)


def scrub_checks() -> None:
    print("scrubbing")
    text = runs.scrub_error(
        "500 internal: call +65 9123 4567 or tan.ah.kow@example.com about SNA 1234 B and JQB 1234, key "
        f"{COMPANY_KEY}, NRIC S1234567D, see P03; Error code: 429, retry in 4.5s"
    )
    for leaked in ("9123", "4567", "tan.ah.kow", "SNA 1234 B", "JQB 1234", COMPANY_KEY, "S1234567D"):
        assert leaked not in text, (leaked, text)
    assert "AIza..." in text and "P03" in text and "429" in text and "[plate]" in text and "[email]" in text, text
    validation = runs.scrub_error(
        "ValidationError: 1 validation error for SecurityIncidentDraft category Input should be a valid list "
        "[type=list_type, input_value='Tan Ah Kow, SKX 1234 A, my car', input_type=str]"
    )
    assert "Tan Ah Kow" not in validation and "input_value=..." in validation and "list_type" in validation, validation
    # pydantic prints a dict or list value as head...tail, with "]" inside it:
    # the value must go whole, not up to its first "]".
    for value_shape in (
        "input_value={'damage_summary': [], 'n...ter_name': 'Tan Ah Kow'}, input_type=dict]",
        "input_value=['Tan Ah Kow said'], input_type=list]",
        "input_value='{\"x\": [1], \"name\": \"Tan Ah Kow\"', input_type=str]",
        "input_value='{\"x\": [1], \"name\": \"Tan Ah Ko",  # cut off
    ):
        shaped = runs.scrub_error("ValidationError: description Field required [type=missing, " + value_shape)
        assert "Tan Ah Ko" not in shaped and "input_value=..." in shaped, shaped
    # A database error quotes the row it tried to write (case data JSON).
    sql = runs.scrub_error(
        "(sqlite3.OperationalError) database is locked [SQL: UPDATE reports SET data=? WHERE reports.id = ?] "
        "[parameters: ('{\"reporter_name\": \"Tan Ah Kow\"}', 'abc123')] (Background on this error at: https://sqlalche.me/e/20/e3q8)"
    )
    assert "Tan Ah Kow" not in sql and "database is locked" in sql, sql
    assert len(runs.scrub_error("x " * 500)) <= 200 and runs.scrub_error("") is None
    ok("scrub_error: phones, e-mails, plates, NRIC, keys and the model output quoted in a validation error go; "
       "codes, photo labels and Google's words stay; at most 200 characters")


def main() -> int:
    scrub_checks()
    with TestClient(app) as client:
        admin_id = sign_in(client, name="Ada Admin")
        plain = TestClient(app)
        sign_in(plain, role="user", name="Plain Person")

        print("intake stages")
        rid = new_draft(client)
        # The reading's first try is a 429 that asks for a short wait; the
        # retry on the same model answers.
        DRAFTS[:] = [RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded. Please retry in 0.01s."), draft()]
        assert client.post(f"/intake/{rid}/process", json={"mode": "all"}).status_code == 200
        state = wait_done(client, rid)
        assert state["stage"] == "ready", state["error"]
        sort_row, read_row = rows_for(rid)
        assert (sort_row.kind, sort_row.status, sort_row.channel) == ("sort", "ok", "dashboard"), row_text(sort_row)
        assert sort_row.photos_sent == {"count": 2} and sort_row.actor_user_id == admin_id
        assert [a["outcome"] for a in sort_row.attempts] == ["ok"] and sort_row.prompt_tokens is None
        assert sort_row.key_source == "company" and sort_row.key_last4 == "1234" and sort_row.input_bytes > 0
        ok("sorting: one row for the stage, {count: 2}, its one try, no tokens when the response reports none")
        assert (read_row.kind, read_row.status) == ("read", "ok")
        outcomes = [(a["outcome"], a["model"]) for a in read_row.attempts]
        first_model = outcomes[0][1]
        assert outcomes == [("429", first_model), ("ok", first_model)], outcomes
        assert read_row.attempts[0]["waited_s"] == 0.0 and read_row.attempts[1]["waited_s"] >= 0.2, read_row.attempts
        assert all(isinstance(a["ms"], int) and a["key_last4"] == "1234" for a in read_row.attempts)
        assert read_row.model_used == first_model and read_row.photos_sent == ["P01", "P02"] and read_row.parts_found == 2
        assert (read_row.prompt_tokens, read_row.output_tokens) == (1200, 300), (read_row.prompt_tokens, read_row.output_tokens)
        assert read_row.finished_at and read_row.error_code is None and read_row.error_text is None
        ok("reading: a 429 then ok on the same model (the wait recorded), model used, P-labels, parts found, tokens")

        # A re-read every model refuses: a failed "redraft" row, the code
        # from the last try, Google's words scrubbed (the key it quoted too).
        MODE["fail"] = f"500 internal: upstream said Tan's number is +65 9123 4567, key {COMPANY_KEY}"
        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        state = wait_done(client, rid)
        MODE["fail"] = None
        assert state["stage"] == "failed"
        failed = rows_for(rid)[-1]
        assert (failed.kind, failed.status, failed.error_code) == ("redraft", "failed", "ai_error"), row_text(failed)
        assert failed.model_used is None and all(a["outcome"] == "error" for a in failed.attempts) and len(failed.attempts) == 5
        assert "500 internal" in failed.error_text and "9123" not in failed.error_text and COMPANY_KEY not in failed.error_text
        assert len(failed.error_text) <= 200
        ok("a re-read failing on all 5 models: redraft / failed / ai_error, 5 tries, the error scrubbed (no number, no key)")

        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        assert wait_done(client, rid)["stage"] == "ready"
        assert [(r.kind, r.status) for r in rows_for(rid)] == [("sort", "ok"), ("read", "ok"), ("redraft", "failed"), ("redraft", "ok")]
        assert COMPANY_KEY not in "".join(row_text(r) for r in rows_for(rid))
        ok("Retry reads again (redraft ok); no row holds the key, only its last 4")

        print("filing")
        final = client.get(f"/intake/{rid}").json()["data"]
        assert client.post(f"/intake/{rid}/finalize", json={"data": final}).status_code == 200
        assert len(rows_for(rid)) == 4
        detail = client.get(f"/reports/{rid}").json()
        assert detail["status"] == "confirmed"
        index = detail["data"]["photo_index"]
        assert [e.get("read") for e in index] == [True, True], index
        rows = detail["ai_runs"]
        assert all(set(r) == RUN_KEYS for r in rows), [sorted(set(r) ^ RUN_KEYS) for r in rows]
        assert [(r["kind"], r["status"]) for r in rows] == [("redraft", "ok"), ("redraft", "failed"), ("read", "ok"), ("sort", "ok")]
        assert rows[1]["error_code"] == "ai_error" and rows[1]["error_text"] == failed.error_text
        assert rows[3]["photos_sent"] == {"count": 2} and rows[3]["photos_count"] == 2 and rows[0]["photos_count"] == 2
        # As the history names people: "Ada Admin (@admin-...)" once start-up gave her a username.
        assert rows[0]["actor_name"].startswith("Ada Admin") and rows[0]["key_source"] == "company", rows[0]
        assert rows[0]["key_last4"] == "1234", rows[0]
        last = detail["ai_last_read"]
        assert set(last) == {"at", "photos_read", "photos_total", "seconds", "model"}, last
        assert (last["photos_read"], last["photos_total"], last["model"]) == (2, 2, rows[0]["model_used"]) and last["at"] == rows[0]["finished_at"]
        ok("the rows stay with the case (same id); photo_index read: true; GET /reports/{id} ai_runs (newest first, "
           "error_text and key_last4 included) and ai_last_read")

        # A deleted photo before filing: the reading's labels follow the
        # case's numbering (runs.relabel_last_read), not the draft's.
        rid2 = new_draft(client, 3)
        client.post(f"/intake/{rid2}/process", json={"mode": "all"})
        state = wait_done(client, rid2)
        assert rows_for(rid2)[-1].photos_sent == ["P01", "P02", "P03"]
        assert client.delete(f"/intake/{rid2}/photos/{state['photos'][0]['id']}").status_code == 200
        assert client.post(f"/intake/{rid2}/finalize", json={"data": state["data"]}).status_code == 200
        detail2 = client.get(f"/reports/{rid2}").json()
        assert detail2["ai_runs"][0]["photos_sent"] == ["P01", "P02"], detail2["ai_runs"][0]
        assert [e.get("read") for e in detail2["data"]["photo_index"]] == [True, True]
        assert (detail2["ai_last_read"]["photos_read"], detail2["ai_last_read"]["photos_total"]) == (2, 2)
        ok("a photo deleted after the reading: the run names the case's own P-numbers at filing")

        never = client.post("/intake").json()["id"]
        assert client.post(f"/intake/{never}/finalize", json={"data": final}).status_code == 200
        bare = client.get(f"/reports/{never}").json()
        assert bare["ai_runs"] == [] and bare["ai_last_read"] is None
        ok("a case filed with no AI run: ai_runs [], ai_last_read null")

        print("Annex A suggestions")
        r = client.post(f"/reports/{rid}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
        assert r.status_code == 200, r.text
        annex = client.get(f"/reports/{rid}").json()["ai_runs"][0]
        assert (annex["kind"], annex["status"], annex["parts_found"], annex["photos_sent"], annex["photos_count"]) == (
            "annex_suggest", "ok", 1, None, None), annex
        ok("POST /annex-suggestions/ai: an annex_suggest row (no photos, 1 part with lines)")

        print("Phase 2's start_run / finish_run, and a run that lost its process")
        db = SessionLocal()
        try:
            row = runs.start_run(db, report_id=rid, kind="redetect", channel="dashboard",
                                 actor=AiActor.for_user(admin_id, "dashboard"))
            assert db.get(AiRun, row.id).status == "running"
            runs.finish_run(db, row, "failed", error=TimeoutError("no answer within 75s"), photos_sent=["P01"], attempts=[
                {"model": "gemini-x", "key_source": "own", "key_last4": "9z9z", "outcome": "timeout", "waited_s": 0.0, "ms": 75000}])
            done = db.get(AiRun, row.id)
            assert (done.status, done.error_code, done.key_source, done.key_last4, done.model_used) == (
                "failed", "timeout", "own", "9z9z", None), row_text(done)
            assert done.error_text == "TimeoutError: no answer within 75s", done.error_text
            # Left "running" by a restart two hours ago; and one from 40 days ago.
            db.add(AiRun(report_id=rid, kind="redetect", channel="dashboard", actor_user_id=admin_id, status="running",
                         started_at=datetime.now(timezone.utc) - timedelta(hours=2)))
            db.add(AiRun(report_id=rid, kind="lookup", channel="system", status="ok",
                         started_at=datetime.now(timezone.utc) - timedelta(days=40),
                         finished_at=datetime.now(timezone.utc) - timedelta(days=40)))
            db.commit()
        finally:
            db.close()
        listed = client.get(f"/reports/{rid}").json()["ai_runs"]
        stale = next(r for r in listed if r["kind"] == "redetect" and r["finished_at"] is None)
        assert (stale["status"], stale["error_code"]) == ("aborted", "interrupted"), stale
        ok("start_run / finish_run (timeout code, own key last 4); a run 'running' for 2 hours shows aborted / interrupted")

        print("a report from the public")
        db = SessionLocal()
        try:
            db.merge(AppSetting(key=public.COMPANY_KEY_BILLED, value="true"))
            db.commit()
        finally:
            db.close()
        photo = Path(_tmp, "public.jpg")
        photo.write_bytes(jpeg((200, 30, 30)))
        sender = {"name": "Tan Ah Kow", "phone": "+6591234567", "plate": "SKX1234A",
                  "statement": "A white lorry reversed into my door outside Blk 123 Jurong West", "username": "tanahkow77"}
        form = public.Form(telegram_user_id="424242", name=sender["name"], role="driver", phone=sender["phone"],
                           statement=sender["statement"], consent_at=datetime.now(timezone.utc), plate=sender["plate"],
                           telegram_username=sender["username"], telegram_name="Ah Kow T")
        pub, reference = public.save_submission(form, [str(photo)])
        assert client.post(f"/inbox/{pub}/take").status_code == 200
        # Every model answers with something that isn't a draft, quoting the
        # sender's name and phone: the validation error must not keep them.
        MODE["raw"] = json.dumps({"description": "x", "category": f"{sender['name']} {sender['phone']} {sender['plate']}"})
        assert client.post(f"/intake/{pub}/process", json={"mode": "all"}).status_code == 200
        assert wait_done(client, pub)["stage"] == "failed"
        MODE["raw"] = None
        pub_rows = rows_for(pub)
        assert [(r.kind, r.status) for r in pub_rows] == [("sort", "ok"), ("read", "failed")], [(r.kind, r.status) for r in pub_rows]
        bad = pub_rows[-1]
        assert bad.error_code == "bad_answer" and all(a["outcome"] == "schema" for a in bad.attempts), row_text(bad)
        everything = "".join(row_text(r) for r in pub_rows)
        for leaked in (sender["name"], "Ah Kow", "91234567", "9123 4567", sender["plate"], "Jurong", "lorry",
                       sender["username"], "424242", reference):
            assert leaked not in everything, (leaked, everything)
        assert all(r.channel == "dashboard" and r.actor_user_id == admin_id for r in pub_rows)
        ok("a public report's runs: report id and the staff member only -- no name, phone, plate, statement, "
           "Telegram id or reference, even when the model's answer quoted them")

        print("GET /ai-usage")
        r = plain.get("/ai-usage")
        assert r.status_code == 403, r.text
        assert client.get("/ai-usage?days=14").status_code == 400
        usage = client.get("/ai-usage").json()
        assert set(usage) == USAGE_KEYS and usage["days"] == 30, sorted(usage)
        db = SessionLocal()
        try:
            recent = [r for r in db.query(AiRun).all()
                      if runs._as_utc(r.started_at) >= datetime.now(timezone.utc) - timedelta(days=30)]
        finally:
            db.close()
        assert usage["runs"] == len(recent), (usage["runs"], len(recent))
        # Failed: the re-read (ai_error), Phase 2's timeout, the stale run
        # (interrupted) and the public reading (bad_answer).
        assert usage["failed"] == 4 and usage["failed_by_code"] == {
            "ai_error": 1, "timeout": 1, "interrupted": 1, "bad_answer": 1}, usage["failed_by_code"]
        assert usage["waits_429"] == 1 and usage["waited_s"] >= 0.2
        reads_ok = [r for r in recent if r.prompt_tokens]
        assert usage["tokens_in"] == sum(r.prompt_tokens for r in reads_ok) and usage["tokens_out"] == sum(
            r.output_tokens for r in reads_ok if r.output_tokens), (usage["tokens_in"], usage["tokens_out"])
        assert usage["by_kind"]["sort"] == 3 and usage["by_kind"]["annex_suggest"] == 1 and "lookup" not in usage["by_kind"]
        company = next(k for k in usage["by_key"] if k["key_source"] == "company")
        assert company["key_last4"] == "1234" and company["failed"] == 2, usage["by_key"]
        assert any(k["key_source"] == "own" and k["key_last4"] == "9z9z" for k in usage["by_key"])
        ada = next(u for u in usage["by_user"] if (u["name"] or "").startswith("Ada Admin"))
        assert ada["runs"] == usage["runs"] and ada["failed"] == 4, usage["by_user"]
        fails = usage["failures"]
        assert [f["error_code"] for f in fails][0] == "bad_answer" and len(fails) == 4, fails
        assert all(set(f) == {"at", "report_id", "plate_or_ref", "kind", "status", "error_code", "actor_name"} for f in fails)
        by_report = {f["report_id"]: f for f in fails}
        assert by_report[pub]["plate_or_ref"] is None, by_report[pub]
        assert next(f for f in fails if f["error_code"] == "ai_error")["plate_or_ref"] == "SKA1234Z"
        assert sender["plate"] not in json.dumps(usage) and sender["name"] not in json.dumps(usage)
        assert client.get("/ai-usage?days=90").json()["by_kind"].get("lookup") == 1
        assert client.get("/ai-usage?days=7").json()["days"] == 7
        ok("admins only (a user gets 403; days other than 7/30/90: 400); runs, failures by code, 429 waits and wait time, "
           "tokens, by key / kind / user, the newest failures with the case's plate -- a public report's never")

    print("\nALL AI RUN LOG CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
