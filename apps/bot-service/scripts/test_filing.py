"""The filing step every channel shares (app/reports/filing.py, 2026-11-04):
a case filed through the Telegram bot gets Annex A rows like a wizard case
(priced for someone who may see the price book, one unpriced row per part
for anyone else), a photo_index, and the AI's draft kept on the row;
the wizard's filing writes the sorted photos into photo_index; GET
/reports/{id} answers ai_read / ai_read_photos from that draft.

    python scripts/test_filing.py [example_case folder]

Offline: the Telegram harness from scripts/test_telegram_bot.py (fake
updates, no AI call, a stand-in PDF render), the API through TestClient
with scripts/test_annexes.py's render stand-ins, and the past reports from
the example_case folder when given (two generated Honda Vezel reports
otherwise).
"""
import asyncio
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
# Sets the temporary storage, database and bot env at import: first.
import test_telegram_bot as bot  # noqa: E402
# Lends the past-report fixtures and replaces the API's PDF/Excel renderers.
import test_annexes as annexes  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.channels.telegram as tg  # noqa: E402
import app.conversation.flow as flow  # noqa: E402
from app.api.main import app  # noqa: E402
from app.parts.history import import_case, normalise_item, parse_report  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.filing import file_case_data, photo_index_for  # noqa: E402
from app.reports.models import IntakeSession, Report  # noqa: E402
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft, SignOffInfo, VehicleInfo  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

SUE_TG = 1009  # a "see all cases" account's Telegram


def ok(label: str) -> None:
    print(f"    ok   {label}")


def jpeg(color: tuple) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


def import_history() -> None:
    db = SessionLocal()
    try:
        folder = Path(tempfile.mkdtemp(prefix="filing-history-"))
        for _, (name, raw, _mime) in annexes.past_report_files():
            path = folder / name
            path.write_bytes(raw)
            import_case(db, parse_report(path), name)
        db.commit()
    finally:
        db.close()


# What the AI says about every draft here: a Vezel with two damaged parts,
# each seen in one photo, and an odometer reading.
VEZEL = SecurityIncidentDraft(
    description="Rear-ended at PIE exit 12", category=["Vehicle Collision or Damage"],
    accident_type="Rear-end collision", incident_datetime="2026-11-01 09:00",
    vehicle_info=VehicleInfo(plate_number="SMV7530X", make="Honda", model="Vezel", odometer_reading="15,287 km", color="White"),
    damage_summary=[DamageSummaryItem(part="Tail Gate", damage_type="Dent", severity="Moderate", photo_reference="P01"),
                    DamageSummaryItem(part="Rear Bumper", damage_type="Deformation", photo_reference="P02")],
    damaged_parts=["Tail Gate", "Rear Bumper"],
)
RENDERED: list[dict] = []  # the data each PDF render was given


def fake_draft_report(description, photo_paths, known_facts=None, **kw):
    """Stands in for the AI call; the real flow.build_draft merges the
    template's answers over it, as in production."""
    return VEZEL.model_copy(deep=True)


TEMPLATE_REPLY = (
    "Vehicle Plate: SKX9999Z\n"
    "Requested by (insurer / company): Tokio Marine\n"
    "Type of claim (Own damage / Third party): Own damage\n"
    "Workshop: Precise Auto Service\n"
    "Date/time of accident: 27 Feb 2023 10.15am\n"
    "Damaged Side (Left / Right / Front / Rear): Rear\n"
    "What happened: Rear-ended at a junction."
)


def fake_render_pdf(data, photo_paths, output_path, report_id=None, unverified=None, slot_wait=None):
    RENDERED.append(data)
    Path(output_path).write_bytes(b"%PDF-1.4 fake")


async def file_via_bot(chat_id: int, text: str = "Rear-ended at PIE exit 12"):
    out = bot.Outbox()
    bot.state.reset_session(str(chat_id))
    earlier = {r.id for r in bot.cases_for(chat_id)}
    for _ in range(2):
        await bot.send_photo(chat_id, out)
    await bot.send_text(chat_id, out, text)
    assert bot.state.get_session(str(chat_id)).draft is not None, out.texts[-3:]
    await tg.handle_callback(bot.tap(chat_id, out), None)
    new = [r for r in bot.cases_for(chat_id) if r.id not in earlier]
    assert len(new) == 1, (new, out.texts[-3:])
    return new[0], out


async def telegram_checks() -> None:
    print("filed through the Telegram bot")
    with mock.patch.object(flow, "draft_report", fake_draft_report), \
            mock.patch.object(tg, "render_pdf", fake_render_pdf), \
            mock.patch.object(tg, "estimate_wait", lambda _key=None: 0.0), \
            mock.patch.object(tg, "BURST", 10**6), mock.patch.object(tg, "BURST_LINKED", 10**6):
        # Ann: no ticks, so the price book is hidden from her.
        case, out = await file_via_bot(bot.ANN_TG)
        rows = case.data["annex_a"]["items"]
        assert [(r["part_name"], r["condition"], r["origin"], r["source_part"], r["workshop_est"], r["prefilled"]) for r in rows] == [
            ("Tail Gate", "Dent", "detection", "Tail Gate", 0.0, False), ("Rear Bumper", "Deformation", "detection", "Rear Bumper", 0.0, False)], rows
        assert case.data["annex_c"]["adjusted_total"] == 0.0 and case.data["sign_off"] == SignOffInfo().model_dump()
        ok("a self-signup's bot case has one unpriced detection row per damaged part, signed off by nobody")
        assert case.data["photo_index"] == [
            {"label": "P01", "kind": None, "area": None, "part": "Tail Gate", "shows_damage": True, "quality": None},
            {"label": "P02", "kind": None, "area": None, "part": "Rear Bumper", "shows_damage": True, "quality": None},
        ], case.data["photo_index"]
        ok("photo_index: one entry per photo, kind None (never sorted), the part from the AI's own photo reference")
        assert case.ai_draft and case.ai_draft["vehicle_info"]["plate_number"] == "SMV7530X" and case.ai_draft["vehicle_info"]["odometer_reading"] == "15,287 km"
        assert [d["part"] for d in case.ai_draft["damage_summary"]] == ["Tail Gate", "Rear Bumper"]
        ok("the AI's draft is kept on the row (Report.ai_draft)")
        assert len(RENDERED[-1]["annex_a"]["items"]) == 2 and RENDERED[-1]["photo_index"], "filed before the PDF rendered"
        assert "CIR-" not in out.texts[-1] and f"/reports/{case.id}" in out.texts[-1] and "saved to your cases" in out.texts[-1], out.texts[-1]
        ok("the filing runs before the render (the PDF shows the rows); the reply gives the case link and no CIR- code")

        # Sue: "see all cases", so Annex A is priced from the past Vezel reports.
        sue = bot.make_user("Sue Staff", "sue", ai_company_key_allowed=True, sees_all_cases=True)
        bot.link_tg(sue, SUE_TG)
        case, out = await file_via_bot(SUE_TG)
        rows = case.data["annex_a"]["items"]
        priced = [r for r in rows if r["prefilled"] and r["workshop_est"] > 0]
        assert len(priced) >= 3 and all(r["origin"] == "history" and r["source_part"] in ("Tail Gate", "Rear Bumper") for r in priced), rows
        tailgate = next(r for r in rows if normalise_item(r["part_name"]) == "tailgate")
        assert tailgate["condition"] == "Dent" and tailgate["workshop_est"] > 0, tailgate
        assert case.data["annex_c"]["adjusted_total"] == case.data["annex_a"]["total_adjusted_cost"] > 0
        assert case.owner_id == sue and case.ai_draft["damage_summary"][0]["part"] == "Tail Gate" and len(case.data["photo_index"]) == 2
        ok("a price-book account's bot case has Annex A priced from past cases (origin history), the AI draft and photo_index too")

        # The template's typed facts go into the case, never into the AI's draft.
        case, out = await file_via_bot(SUE_TG, TEMPLATE_REPLY)
        assert case.data["vehicle_info"]["plate_number"] == "SKX9999Z" and case.data["cover_page"]["requested_by"] == "Tokio Marine"
        assert case.data["insurance_details"]["claim_type"] == "Own damage" and case.data["incident_datetime"] == "27 Feb 2023 10.15am"
        assert case.ai_draft["vehicle_info"]["plate_number"] == "SMV7530X" and case.ai_draft["incident_datetime"] == "2026-11-01 09:00"
        assert not (case.ai_draft.get("cover_page") or {}).get("requested_by") and not (case.ai_draft.get("insurance_details") or {}).get("claim_type")
        with TestClient(app) as admin:
            sign_in(admin, role="admin", name="Ada Admin")
            read = admin.get(f"/reports/{case.id}").json()
            assert read["ai_read"]["vehicle_info"]["plate_number"] == "SMV7530X" and read["ai_read"]["incident_datetime"] == "2026-11-01 09:00", read["ai_read"]
            assert "SKX9999Z" not in str(read["ai_read"]) and read["data"]["vehicle_info"]["plate_number"] == "SKX9999Z"
        ok("a typed plate, date, requested-by and claim type are the case's facts, not AI-read: ai_draft and ai_read keep what the AI said")


def direct_checks() -> None:
    print("file_case_data")
    db = SessionLocal()
    try:
        plain = SimpleNamespace(role="user", sees_all_cases=False)
        staff = SimpleNamespace(role="user", sees_all_cases=True)
        draft = VEZEL.model_dump()
        before = {**draft, "report_id": "CL 12330", "sign_off": {**SignOffInfo().model_dump(), "status": "Signed Off", "approved_by": "Mallory"},
                  "annex_a": {"items": [{"part_name": "Old row", "workshop_est": 9}]}, "annex_b": {"items": [{"description": "Old labour", "workshop_est": 9}]}}
        sorted_kinds = [{"kind": "damage", "area": "rear", "part": "Tail Gate", "shows_damage": True, "quality": "good"}, {}]
        data = file_case_data(db, before, plain, ai_draft=draft, photo_kinds=sorted_kinds)
        assert data["sign_off"] == SignOffInfo().model_dump() and data["report_id"] == "CL 12330"
        assert [r["part_name"] for r in data["annex_a"]["items"]] == ["Tail Gate", "Rear Bumper"] and data["annex_b"]["items"] == []
        assert data["photo_index"] == [
            {"label": "P01", "kind": "damage", "area": "rear", "part": "Tail Gate", "shows_damage": True, "quality": "good"},
            {"label": "P02", "kind": None, "area": None, "part": "Rear Bumper", "shows_damage": True, "quality": None},
        ], data["photo_index"]
        assert before["sign_off"]["status"] == "Signed Off", "the caller's dict is left alone"
        ok("sign-off blank, Our Ref kept, annexes rebuilt from the damage list, photo_index from the sort step (the AI's reference fills an unsorted photo)")
        assert file_case_data(db, draft, plain, ai_draft=None, photo_kinds=None)["photo_index"] == []
        assert photo_index_for([{}, {}], {"damage_summary": [{"part": "Tail Gate", "photo_reference": "P09"}]}) == [
            {"label": "P01", "kind": None, "area": None, "part": None, "shows_damage": None, "quality": None},
            {"label": "P02", "kind": None, "area": None, "part": None, "shows_damage": None, "quality": None}]
        priced = file_case_data(db, draft, staff, ai_draft=draft, photo_kinds=None)["annex_a"]["items"]
        assert any(r["prefilled"] and r["workshop_est"] > 0 and r["origin"] == "history" for r in priced), priced
        ok("no photos -> an empty index; a reference to no photo is ignored; a price-book user's rows are priced")
    finally:
        db.close()


def intake_checks() -> None:
    print("filed through the intake wizard")
    with TestClient(app) as client:
        sign_in(client, role="user", name="Plain Person")
        rid = client.post("/intake").json()["id"]
        for i, color in enumerate(((200, 30, 30), (30, 30, 200))):
            r = client.post(f"/intake/{rid}/photos", files={"file": (f"p{i}.jpg", jpeg(color), "image/jpeg")}, data={"position": str(i)})
            assert r.status_code == 200, r.text
        photos = client.get(f"/intake/{rid}").json()["photos"]
        assert client.patch(f"/intake/{rid}/photos/{photos[1]['id']}", json={"kind": "odometer"}).status_code == 200
        # As if the AI had read it (the real run is scripts/test_intake_integrity.py),
        # in the pipeline's own shape: photo references are "@<intake photo id>"
        # until the order is fixed at filing.
        ai_said = {**VEZEL.model_dump(), "vehicle_info": {**VEZEL.model_dump()["vehicle_info"], "color": "Red"}}
        ai_said["damage_summary"] = [{**ai_said["damage_summary"][0], "photo_reference": f"@{photos[0]['id']}"},
                                     {**ai_said["damage_summary"][1], "photo_reference": "@gone", "bbox_2d": [1, 2, 3, 4]}]
        db = SessionLocal()
        try:
            db.get(IntakeSession, rid).ai_draft = ai_said
            db.commit()
        finally:
            db.close()
        state = client.get(f"/intake/{rid}").json()
        assert state["ai_read"]["vehicle_info"]["color"] == "Red" and state["ai_read"]["incident_datetime"] == "2026-11-01 09:00"
        assert state["ai_read_photos"] == {"odometer_reading": "P02"}, state["ai_read_photos"]
        assert "ai_read" not in state["data"]
        ok("GET /intake/{id}: ai_read / ai_read_photos from the session's AI draft and the sorted photos, for the wizard's cover tags")
        body = {**VEZEL.model_dump(), "vehicle_info": {**VEZEL.model_dump()["vehicle_info"], "color": "White"}}
        body["damage_summary"] = [{"part": "Tail Gate", "damage_type": "Dent", "photo_reference": "P01"}]
        assert client.post(f"/intake/{rid}/finalize", json={"data": body}).status_code == 200
        detail = client.get(f"/reports/{rid}").json()
        # P01 was never sorted, so the AI's own reference (P01 shows the
        # tail gate) labels it; P02 keeps the surveyor's sort.
        assert detail["data"]["photo_index"] == [
            {"label": "P01", "kind": None, "area": None, "part": "Tail Gate", "shows_damage": True, "quality": None},
            {"label": "P02", "kind": "odometer", "area": None, "part": None, "shows_damage": False, "quality": None},
        ], detail["data"]["photo_index"]
        rows = detail["data"]["annex_a"]["items"]
        assert [(r["part_name"], r["origin"], r["workshop_est"]) for r in rows] == [("Tail Gate", "detection", 0.0)], rows
        db = SessionLocal()
        try:
            stored = db.get(Report, rid)
            assert stored.data["vehicle_info"]["color"] == "White" and stored.ai_draft["vehicle_info"]["color"] == "Red"
            refs = [(d["photo_reference"], d.get("bbox_2d")) for d in stored.ai_draft["damage_summary"]]
            assert refs == [("P01", None), (None, None)], refs  # "@id" -> P-number; a reference to a deleted photo -> none
        finally:
            db.close()
        ok("a wizard case's photo_index comes from the sorted photos; the AI's draft is kept apart from the surveyor's version, its photo references turned into P-numbers")
        assert detail["ai_read"] == {"vehicle_info": {"plate_number": "SMV7530X", "make": "Honda", "model": "Vezel", "odometer_reading": "15,287 km",
                                                      "color": "Red"}, "incident_datetime": "2026-11-01 09:00"}, detail["ai_read"]
        assert detail["ai_read_photos"] == {"odometer_reading": "P02"}, detail["ai_read_photos"]
        assert "ai_read" not in detail["data"] and "Red" not in str(detail["data"])
        ok("GET /reports/{id}: ai_read is the AI's non-empty cover values (the surveyor's White stays in data), ai_read_photos names the odometer photo")

        # Photos added later (PUT /reports/{id}) extend photo_index positionally, kind None.
        r = client.post(f"/reports/{rid}/upload-photos", files=[("photos", ("later.jpg", jpeg((90, 160, 40)), "image/jpeg"))])
        assert r.status_code == 200, r.text
        r = client.put(f"/reports/{rid}", json={"draft": {"description": "Rear-ended", "category": ["Vehicle Collision or Damage"]},
                                                "temp_photo_paths": r.json()["temp_photo_paths"]})
        assert r.status_code == 200, r.text
        index = client.get(f"/reports/{rid}").json()["data"]["photo_index"]
        assert [p["label"] for p in index] == ["P01", "P02", "P03"] and index[2] == {"label": "P03", "kind": None, "area": None, "part": None, "shows_damage": None, "quality": None}, index
        ok("a photo added on the edit form gets a photo_index entry (kind None) so the labels stay positional")

        no_draft = client.post("/intake").json()["id"]
        assert client.post(f"/intake/{no_draft}/finalize", json={"data": body}).status_code == 200
        bare = client.get(f"/reports/{no_draft}").json()
        assert bare["ai_read"] is None and bare["ai_read_photos"] == {} and bare["data"]["photo_index"] == []
        ok("a case filed with no AI run: ai_read null, ai_read_photos {}")


def main() -> int:
    init_db()
    bot.make_people()
    import_history()
    asyncio.run(telegram_checks())
    direct_checks()
    intake_checks()
    print("\nALL FILING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
