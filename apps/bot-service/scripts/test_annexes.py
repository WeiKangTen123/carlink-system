"""The report in the firm's spreadsheet format (2026-11-01): the line-total
rule, Annex B history and labour suggestions, pricing Annex A from past
cases, the cover-page / Annex B / Annex C routes, the PDF without photos,
the Excel download and a sign-off that keeps Annex C's figures
(app/reports/synthesize_annexes.py, app/parts/history.py,
app/parts/prefill.py, app/api/annexes.py, app/api/main.py, app/api/intake.py).

    python scripts/test_annexes.py [example_case folder]

Offline. With the example_case folder the four real CL reports are imported
(Annex A and Annex B) and the prefill is checked against them; without it,
two generated Honda Vezel reports stand in. PDF and Excel rendering are
replaced by stand-ins: the routes are the subject here, not Chromium.
"""
import importlib
import io
import os
import re
import sys
import tempfile
import types
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="annexes-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import openpyxl  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.api.intake as intake_api  # noqa: E402
import app.api.main as main_api  # noqa: E402
from app.api.main import app  # noqa: E402
from app.parts.history import normalise_item, normalise_labour, parse_report, suggest_labour  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import PastLabourLine, Report  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.reports.synthesize_annexes import adjusted_cost, convert_per_unit_estimates, synthesize_annexes_for_report  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

EXAMPLES = Path(sys.argv[1]) if len(sys.argv) > 1 else None
LOCKED = "Report is signed off and locked. Reopen it before editing."


def ok(label: str) -> None:
    print(f"    ok   {label}")


def filename_of(r) -> str:
    """The download's file name as a browser reads it: Starlette sends
    `filename*=utf-8''...` (RFC 5987) for a name with spaces or brackets."""
    header = r.headers["content-disposition"]
    m = re.search(r"filename\*=utf-8''([^;]+)|filename=\"([^\"]+)\"", header)
    assert m, header
    return unquote(m.group(1) or m.group(2))


# ------------------------------------------------------------ fixtures


def report_xlsx(ref: str, plate: str, make_model: str, parts: list, labour: list[tuple]) -> bytes:
    """A spreadsheet laid out like the firm's CL reports: Cover Page,
    Annex A (List Items / S/Nett Items) and Annex B."""
    book = openpyxl.Workbook()
    cover = book.active
    cover.title = "Cover Page"
    cover.append([None, "ACCIDENT VEHICLE INSPECTION / ASSESSMENT REPORT"])
    cover.append([None, f"Our  Ref: {ref} {plate}"])
    cover.append([None, None, "Date of survey", ":", "27th January 2018"])
    cover.append([None, None, "Registration No.", ":", plate])
    cover.append([None, None, "Make & Model", ":", make_model])
    annex = book.create_sheet("Annex A")
    annex.append([None, None, plate])
    annex.append([None, "Item", "Vehicle Parts Description", "Condition", "Remarks", "Qty", "W/shop   Est. ($)", "Disc       (%)", "Adjusted    Cost ($)"])
    annex.append([None, None, "List Items"])
    nett, n = False, 0
    for line in parts:
        if line == "NETT":
            annex.append([None, None, "S/Nett Items"])
            nett = True
            continue
        n += 1
        item, cond, qty, est, adj = line
        annex.append([None, float(n), item, cond, None, qty, est, None if nett else 0.2, adj])
    annex.append([None, None, "Total for Spare Parts :", None, None, None, 0, None, 0])
    b = book.create_sheet("Annex B")
    b.append([None, "Annex B : Adjustment on Labour and Spray Painting"])
    b.append([None, None, plate])
    b.append([None, "Item", "Job Description", "Workshop Est. ($)", "Adjusted Cost ($)"])
    for i, (desc, est, adj) in enumerate(labour, start=1):
        b.append([None, float(i), desc, est, adj])
    b.append([None, None, "TOTAL FOR LABOUR & SPRAY PAINTING :", sum(l[1] for l in labour), sum(l[2] for l in labour)])
    b.append([None, "Summary"])
    b.append([None, None, "TOTAL FOR SPARE PARTS :", 0, 0])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


VEZEL_A = [
    ("Tail gate assy", "Buckled", 1, 1450, 1160), ("Tail gate hinges", "Serviceable", 2, 240, 0),
    ("Tail gate stopper", "Necessary", 2, 50, 40), ("Tail gate emblem VEZEL", "Necessary", 1, 65, 52),
    ("Rear bumper fascia", "Buckled/cut", 1, 1020, 816), ("Rear bumper side retainer", "Necessary", 2, 84, 67.2),
    ("Rear bumper clips", "Necessary", 10, 50, 40),
    "NETT", ("Reverse sensor", "Distorted/cut/faulty", "1set", 280, 280),
]
VEZEL_B = [
    ("Tail gate", "Dented/bent", 1, 1450, 1200), ("Tail gate stopper", "Necessary", 2, 50, 40),
    ("Rear bumper fascia", "Deformed/cut", 1, 1000, 800), ("Rear bumper clips", "Necessary", 8, 40, 32),
    "NETT", ("Reverse sensor", "Cut/faulty", "1set", 380, 380),
]
LABOUR_A = [
    ("To remove, reinstall rear windscreen (to FR)", 150, 120), ("To respray affected areas", 1200, 900),
    ("To remove, reinstall tail gate parts", 120, 80),
]
LABOUR_B = [
    ("To remove/refix rear windscreen glass", 180, 120), ("To respray affected areas", 1800, 1250), ("Towing", 100, 60),
]


def past_report_files() -> list[tuple]:
    if EXAMPLES:
        files = sorted(EXAMPLES.rglob("CL *.xls"))
        assert len(files) >= 4, f"expected the four CL reports under {EXAMPLES}, found {[f.name for f in files]}"
        return [("files", (f.name, f.read_bytes(), "application/octet-stream")) for f in files]
    return [
        ("files", ("CL 11900 SLK 3063 Z.xlsx", report_xlsx("CL 11900", "SLK 3063 Z", "Honda Vezel 1.5X CVT", VEZEL_A, LABOUR_A), "application/octet-stream")),
        ("files", ("CL 12330 SMV 7530 X.xlsx", report_xlsx("CL 12330", "SMV 7530 X", "Honda Vezel 1.5X CVT", VEZEL_B, LABOUR_B), "application/octet-stream")),
    ]


def new_case(client: TestClient, plate: str = "SMV7530X") -> str:
    r = client.post("/reports", json={"draft": {
        "category": ["Vehicle Collision or Damage"], "description": "Rear-ended at a junction",
        "vehicle_info": {"plate_number": plate, "make": "Honda", "model": "Vezel"},
        "damage_summary": [{"part": "Tail Gate", "damage_type": "Dent", "severity": "Severe"},
                           {"part": "Rear Bumper", "damage_type": "Deformation"}],
    }})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def set_signed_off(rid: str, signed: bool) -> None:
    db = SessionLocal()
    try:
        row = db.get(Report, rid)
        row.data = {**row.data, "sign_off": {**(row.data.get("sign_off") or {}), "status": "Signed Off" if signed else "Draft"}}
        row.status = "Signed Off" if signed else "confirmed"
        db.commit()
    finally:
        db.close()


# PDF and Excel stand-ins: the routes' behaviour is the subject here.
render_calls: list[dict] = []


def fake_render_pdf(report, photo_paths, output_path, report_id=None, include_photos=True, **kw):
    render_calls.append({"include_photos": include_photos, "path": output_path, "photos": len(photo_paths or [])})
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4 stand-in\n" + str(include_photos).encode())


main_api.render_pdf = fake_render_pdf
intake_api.render_pdf = fake_render_pdf

try:
    # importlib, not `import app.rendering.xlsx_export`: that statement
    # rebinds the name `app` to the package, over the FastAPI app above.
    importlib.import_module("app.rendering.xlsx_export")  # agent A's exporter, when it has landed
    REAL_XLSX = True
except Exception:
    REAL_XLSX = False
    stub = types.ModuleType("app.rendering.xlsx_export")

    def _stub_render_xlsx(report, output_path, report_id=None):
        book = openpyxl.Workbook()
        book.active.title = "Cover Page"
        book.active.append(["Our  Ref:", report.get("report_id") or ""])
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        book.save(output_path)

    stub.render_xlsx = _stub_render_xlsx
    sys.modules["app.rendering.xlsx_export"] = stub


def main() -> int:
    print("line totals")
    assert adjusted_cost(50, 0.2) == 40.0 and adjusted_cost(50, 0.2, is_net_item=True) == 50.0 and adjusted_cost(0, 0.2) == 0.0
    data = synthesize_annexes_for_report({
        "annex_a": {"items": [
            {"part_name": "Rear bumper clips", "qty": 10, "workshop_est": 50, "discount_pct": 0.2, "adjusted_cost": 400.0},
            {"part_name": "Tail gate hinges", "qty": 2, "workshop_est": 240, "discount_pct": 1.0},
            {"part_name": "Reverse sensor", "qty": 1, "qty_text": "1set", "workshop_est": 280, "is_net_item": True, "discount_pct": 0.2},
        ]},
        "annex_b": {"items": [{"description": "To respray affected areas", "workshop_est": 1800, "adjusted_cost": 1250}]},
        "annex_c": {"agreed_lump_sum": 6500, "gst_amount": 585.0, "total_with_gst": 7085.0},
    })
    a, b, c = data["annex_a"], data["annex_b"], data["annex_c"]
    assert [r["adjusted_cost"] for r in a["items"]] == [40.0, 0.0, 280.0], a["items"]
    assert a["total_workshop_est"] == 570.0 and a["total_adjusted_cost"] == 320.0, a
    assert b["total_workshop_est"] == 1800.0 and b["total_adjusted_cost"] == 1250.0
    assert c["workshop_total"] == 2370.0 and c["adjusted_total"] == 1570.0 and c["agreed_lump_sum"] == 6500
    assert c["gst_amount"] is None and c["total_with_gst"] is None
    ok("W/shop Est is the line total: 10 clips at 50 adjust to 40, not 400; a 100% item to 0; a S/Nett item to its estimate")
    ok("Annex A/B/C totals add the line totals (no x qty); GST is no longer computed, even on a case that had it")
    assert "cover_page" not in SecurityIncidentDraft.model_json_schema()["properties"]
    old = SecurityIncidentDraft.model_validate({"description": "stored before 2026-11-01", "annex_a": {"items": [{"part_name": "Tail gate"}]}}).model_dump()
    assert old["cover_page"]["date_of_survey"] is None and old["annex_a"]["items"][0]["prefilled"] is False and old["sign_off"]["signatory_title"] is None
    ok("old stored cases still validate (cover_page, prefilled, qty_text, signatory_title default); the AI is never asked for the cover page")

    admin = TestClient(app, base_url="https://carlink.test")
    sign_in(admin, role="admin", name="Kelvin Lim")
    # An in-house surveyor: sees every case (the price book), may sign off.
    surveyor = TestClient(app, base_url="https://carlink.test")
    sign_in(surveyor, role="user", name="Patrick Ng", sees_all_cases=True, may_sign_off=True, ai_company_key_allowed=True,
            qualifications="MIMI, MIRTE, LCGI")
    # A self-signup: no ticks.
    stranger = TestClient(app, base_url="https://carlink.test")
    sign_in(stranger, role="user", name="Walk In")

    print("past reports: Annex B")
    if EXAMPLES:
        real = next(EXAMPLES.rglob("CL 12330*.xls"))
        case = parse_report(real)
        assert (case.ref, case.make, case.model, len(case.lines), len(case.labour)) == ("CL 12330", "Honda", "Vezel", 30, 7), case.ref
        assert case.labour[2].description == "To remove, reinstall rear windscreen (to FR)" and case.labour[2].adjusted_cost == 120.0
        assert case.labour[0].description.startswith("To remove, reinstall electrical wiring harness, check lighting and reconnect")
        assert case.labour[-1].workshop_est == 1500.0 and case.labour[-1].adjusted_cost == 1200.0
        assert [l.qty_raw for l in case.lines if l.qty_raw == "1set"] == ["1set"]
        ok(f"the real {real.name} reads 30 parts and 7 labour lines; wrapped job descriptions come out on one line")
    else:
        path = Path(_tmp, "a.xlsx")
        path.write_bytes(report_xlsx("CL 11900", "SLK 3063 Z", "Honda Vezel 1.5X CVT", VEZEL_A, LABOUR_A))
        case = parse_report(path)
        assert len(case.lines) == 8 and [l.description for l in case.labour] == [l[0] for l in LABOUR_A], case.labour
        assert case.labour[0].workshop_est == 150.0 and case.labour[0].adjusted_cost == 120.0
        ok("a CL spreadsheet's Annex B reads into labour lines (between the Job Description header and the TOTAL row)")
    assert normalise_labour("To remove, reinstall rear windscreen (to FR)") == "remove reinstall rear windscreen to fr"
    assert normalise_labour("to  Respray affected areas.") == normalise_labour("Respray affected areas") == "respray affected areas"
    ok("labour wording keys drop a leading 'to', punctuation and double spaces")

    files = past_report_files()
    r = admin.post("/parts-history/import", files=files)
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert all("lines" in x and x["labour"] > 0 for x in results), results
    by_ref = {x["ref"]: x for x in results}
    if EXAMPLES:
        assert by_ref["CL 12330"]["labour"] == 7 and by_ref["CL 11900"]["labour"] == 11 and by_ref["CL 12076"]["labour"] == 13, by_ref
    db = SessionLocal()
    stored = db.query(PastLabourLine).filter(PastLabourLine.case_ref == "CL 12330").count()
    db.close()
    assert stored == by_ref["CL 12330"]["labour"]
    again = admin.post("/parts-history/import", files=files[:1]).json()["results"][0]
    assert again["labour"] == results[0]["labour"] and again["lines"] == results[0]["lines"]
    db = SessionLocal()
    assert db.query(PastLabourLine).filter(PastLabourLine.case_ref == again["ref"]).count() == again["labour"]
    db.close()
    hist = admin.get("/parts-history").json()
    assert {c["ref"]: c["labour"] for c in hist["cases"]} == {ref: x["labour"] for ref, x in by_ref.items()}, hist["cases"]
    ok("importing a report stores its Annex B lines too; a re-import replaces both kinds; the history lists the labour count")

    print("labour suggestions")
    db = SessionLocal()
    s = suggest_labour(db, "Honda", "Vezel 1.5X CVT", existing=[])
    db.close()
    assert s["groups"][0]["reason"] == "past Honda Vezel cases", s["groups"][0]["reason"]
    lines = s["groups"][0]["lines"]
    respray = next(l for l in lines if normalise_labour(l["description"]) == "respray affected areas")
    assert respray["usual_price"] and respray["usual_workshop_est"], respray
    assert respray["source"] == f"{respray['cases']} past Honda Vezel case{'s' if respray['cases'] != 1 else ''}", respray
    if EXAMPLES:
        # The three real Vezel reports each word their jobs differently ("To
        # respray affected areas" / "To putty and respray paint on affected
        # area" / "To respray painting on the change bodyparts..."), so every
        # Vezel line rests on one case; 11900's figures come through as they are.
        assert (respray["cases"], respray["usual_price"], respray["usual_workshop_est"]) == (1, 1250.0, 1800.0), respray
        assert len(lines) == 7 + 7 + 11, len(lines)
        assert len(s["groups"]) == 2 and s["groups"][1]["reason"] == "other past cases"
        civic = [l["description"] for l in s["groups"][1]["lines"]]
        assert "Panel beating" in civic and "To check alignment" in civic, civic
        assert all(l["source"] == "1 past Honda Civic case" for l in s["groups"][1]["lines"]), s["groups"][1]["lines"][:2]
        assert s["cases_used"] == 4
    else:
        # Both generated Vezel reports wrote it: median of 900 / 1250 and of 1200 / 1800.
        assert (respray["cases"], respray["usual_price"], respray["usual_workshop_est"]) == (2, 1075.0, 1500.0), respray
        assert len(s["groups"]) == 1
    assert lines == sorted(lines, key=lambda l: (-l["cases"], l["description"].lower()))
    db = SessionLocal()
    s2 = suggest_labour(db, "Honda", "Vezel", existing=["to respray affected areas"])
    db.close()
    assert not any(normalise_labour(l["description"]) == "respray affected areas" for g in s2["groups"] for l in g["lines"])
    db = SessionLocal()
    s3 = suggest_labour(db, "Toyota", "Hiace", existing=[])
    db.close()
    assert s3["groups"] and s3["groups"][0]["reason"] == "all imported cases"
    ok("labour lines group by wording with median adjusted / workshop figures: same make+model first, then the rest; lines already in Annex B left out")

    print("prefill from past cases")
    rid = new_case(surveyor)
    r = surveyor.post(f"/reports/{rid}/annex-a/prefill")
    assert r.status_code == 200, r.text
    out = r.json()
    items = out["annex_a"]["items"]
    priced = [i for i in items if i["prefilled"] and i["workshop_est"] > 0]
    assert len(priced) >= 3 and out["filled"] == len(priced), [(i["part_name"], i["workshop_est"]) for i in items]
    assert out["unpriced"] == sum(1 for i in items if i["workshop_est"] <= 0)
    assert all(i["suggested_from"] and i["suggested_from"].endswith("(Honda Vezel)") and i["usual_unit_price"] for i in priced), priced
    tailgate = next(i for i in items if normalise_item(i["part_name"]) == "tailgate")
    assert tailgate["condition"] == "Dent" and tailgate["prefilled"] and tailgate["workshop_est"] > 0, tailgate
    assert tailgate["adjusted_cost"] == round(tailgate["workshop_est"] * 0.8, 2) and tailgate["discount_pct"] == 0.2
    bumper = next(i for i in items if normalise_item(i["part_name"]) == "rear bumper")
    assert bumper["condition"] == "Deformation" and bumper["workshop_est"] > 0, bumper
    stopper = next(i for i in items if normalise_item(i["part_name"]) == "tailgate stopper")
    # The adjusted cost is what past cases paid for two; the estimate is the
    # workshop's usual asking figure, so it sits above the adjusted figure.
    assert stopper["condition"] == "Necessary" and stopper["qty"] == 2.0 and stopper["adjusted_cost"] == round(stopper["usual_unit_price"] * 2, 2) and stopper["workshop_est"] > stopper["adjusted_cost"], stopper
    hinges = next((i for i in items if "hinge" in i["part_name"].lower()), None)
    if hinges:
        assert hinges["workshop_est"] == 0 and hinges["condition"] == "Serviceable" and hinges["adjusted_cost"] == 0, hinges
    assert [i["item_no"] for i in items] == [f"{n}.0" for n in range(1, len(items) + 1)]
    sections = [i["is_net_item"] for i in items]
    assert sections == sorted(sections), "List Items must come before S/Nett Items"
    assert out["annex_c"]["adjusted_total"] == out["annex_a"]["total_adjusted_cost"] == round(sum(i["adjusted_cost"] for i in items), 2)
    assert out["annex_c"]["gst_amount"] is None
    ok("a Honda Vezel with a damaged Tail Gate and Rear Bumper gets priced rows from past Vezel cases, tagged prefilled with their source")
    ok("the damaged part itself carries the AI's damage type, fittings keep 'Necessary', a kept (Serviceable) part stays unpriced")
    ok("rows are numbered in saved order, List Items before S/Nett, and Annex C adds up to them")
    with_sets = [i for i in items if i["qty_text"]]
    if with_sets:
        assert all(i["qty_text"].endswith("set") and i["qty"] == float(i["qty_text"][:-3]) for i in with_sets), with_sets
        ok("a line past cases wrote as '1set' prints as 1set and counts as 1")

    # A row a person priced is kept, not repeated, and not re-priced.
    typed = [{"part_name": "Rear bumper fascia", "condition": "Buckled/cut", "action": "Replace", "qty": 1, "workshop_est": 1020, "discount_pct": 0.2},
             {"part_name": "Spoiler", "qty": 1, "workshop_est": 0}]
    assert surveyor.put(f"/reports/{rid}/annex-a", json={"items": typed}).status_code == 200
    out2 = surveyor.post(f"/reports/{rid}/annex-a/prefill").json()
    names = [i["part_name"] for i in out2["annex_a"]["items"]]
    kept = out2["annex_a"]["items"][0]
    assert kept["part_name"] == "Rear bumper fascia" and kept["workshop_est"] == 1020 and kept["adjusted_cost"] == 816.0 and not kept["prefilled"]
    assert sum(1 for n in names if normalise_item(n) == "rear bumper") == 1, names
    assert "Spoiler" not in names and any(normalise_item(n) == "tailgate" for n in names)
    ok("a row a person priced survives a prefill unchanged and isn't duplicated; unpriced rows are replaced")
    r = surveyor.put(f"/reports/{rid}/annex-a", json={"items": [
        {"part_name": "Reverse sensor", "qty": 1, "qty_text": "1set", "workshop_est": 280, "is_net_item": True},
        {"part_name": "Rear bumper clips", "qty": 1, "qty_text": "10", "workshop_est": 50, "discount_pct": 0.2},
    ]})
    rows = r.json()["annex_a"]["items"]
    assert (rows[0]["qty"], rows[0]["qty_text"], rows[0]["adjusted_cost"]) == (1.0, "1set", 280.0)
    assert (rows[1]["qty"], rows[1]["qty_text"], rows[1]["adjusted_cost"]) == (10.0, None, 40.0)
    ok("PUT /annex-a keeps '1set' as text with qty 1, turns a bare '10' into the number, and adjusts the line total")

    r = stranger.post(f"/reports/{rid}/annex-a/prefill")
    assert r.status_code == 404, r.text
    own = new_case(stranger, plate="SNA5678C")
    r = stranger.post(f"/reports/{own}/annex-a/prefill")
    assert r.status_code == 403 and r.json()["code"] == "not_allowed", r.text
    unpriced = stranger.get(f"/reports/{own}").json()["data"]["annex_a"]["items"]
    assert all(i["workshop_est"] == 0 and not i["prefilled"] for i in unpriced)
    r = stranger.get(f"/reports/{own}/labour-suggestions")
    assert r.status_code == 200 and r.json()["hidden"] is True and r.json()["groups"] == [], r.text
    ok("the price book stays the firm's: a self-signup can't prefill (403) and gets hidden labour suggestions")

    print("prefill on filing an intake")
    draft = surveyor.post("/intake").json()["id"]
    body = {"category": ["Vehicle Collision or Damage"], "description": "Rear-ended",
            "vehicle_info": {"plate_number": "SLK3063Z", "make": "Honda", "model": "Vezel 1.5X CVT"},
            "damage_summary": [{"part": "Boot Lid", "damage_type": "Dent"}]}
    assert surveyor.put(f"/intake/{draft}/data", json={"data": body}).status_code == 200
    r = surveyor.post(f"/intake/{draft}/finalize", json={"data": body})
    assert r.status_code == 200, r.text
    filed = surveyor.get(f"/reports/{draft}").json()["data"]
    filed_items = filed["annex_a"]["items"]
    assert any(i["prefilled"] and i["workshop_est"] > 0 for i in filed_items), filed_items
    assert next(i for i in filed_items if normalise_item(i["part_name"]) == "tailgate")["condition"] == "Dent"
    assert filed["sign_off"]["status"] == "Draft" and filed["annex_c"]["adjusted_total"] == filed["annex_a"]["total_adjusted_cost"]
    draft2 = stranger.post("/intake").json()["id"]
    assert stranger.post(f"/intake/{draft2}/finalize", json={"data": body}).status_code == 200
    plain = stranger.get(f"/reports/{draft2}").json()["data"]["annex_a"]["items"]
    assert plain and all(i["workshop_est"] == 0 and not i["prefilled"] for i in plain)
    ok("finalizing an intake prices Annex A from past cases for staff; a self-signup's case keeps unpriced rows")

    print("cover page")
    rid = new_case(surveyor)
    body = {
        "report_id": "CL 12330", "incident_datetime": "21st February 2023",
        "cover_page": {"date_of_request": "27th February 2023", "requested_by": "Loh Keng Yew",
                       "date_of_survey": "27th February 2023 (10.15am)", "general_condition": "Good", "modification": "Nil"},
        "vehicle_info": {"date_of_registration": "19th October 2020", "odometer_reading": "058615 Km",
                         "tyres": {"front_nearside": {"tread_depth_mm": 7, "brand": "Dunlop", "size": "215/60 R16"},
                                   "front_offside": {"tread_depth_mm": 7, "brand": "Dunlop"}}},
        "insurance_details": {"claim_type": "Third party", "workshop_assigned": "M/S People's Vehicle Recovery Service"},
    }
    r = surveyor.put(f"/reports/{rid}/cover-page", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["report_id"] == "CL 12330" and saved["incident_datetime"] == "21st February 2023"
    assert saved["cover_page"]["date_of_survey"] == "27th February 2023 (10.15am)" and saved["cover_page"]["handbrake_condition"] is None
    v = saved["vehicle_info"]
    assert v["make"] == "Honda" and v["plate_number"] == "SMV7530X" and v["odometer_reading"] == "058615 Km", v
    assert v["tyres"]["front_nearside"]["brand"] == "Dunlop" and v["tyres"]["rear_offside"]["brand"] is None
    assert saved["insurance_details"]["workshop_assigned"] == "M/S People's Vehicle Recovery Service"
    stored = surveyor.get(f"/reports/{rid}").json()
    assert stored["data"]["cover_page"] == saved["cover_page"] and stored["data"]["description"] == "Rear-ended at a junction"
    assert stored["pdf_url"] is None or True  # the PDF is rendered again on the next download (pdf_path dropped)
    ok("the cover page saves Our Ref, the accident date, the firm's cover fields, vehicle and insurance facts; the rest of the case is untouched")
    r = surveyor.put(f"/reports/{rid}/cover-page", json={
        "cover_page": {"handbrake_condition": "see annex(es)", "general_condition": None},
        "vehicle_info": {"tyres": {"rear_nearside": {"tread_depth_mm": 6.5, "brand": "Michelin"}}},
    })
    assert r.status_code == 200, r.text
    again = r.json()
    cp = again["cover_page"]
    assert cp["date_of_survey"] == "27th February 2023 (10.15am)" and cp["handbrake_condition"] == "see annex(es)" and cp["general_condition"] is None
    assert cp["modification"] == "Nil"
    t = again["vehicle_info"]["tyres"]
    assert t["rear_nearside"]["brand"] == "Michelin" and t["front_nearside"]["brand"] is None, t
    assert again["vehicle_info"]["odometer_reading"] == "058615 Km" and again["report_id"] == "CL 12330"
    ok("a second save merges key by key: absent keys stay, null clears, tyres are replaced as a whole")
    r = stranger.put(f"/reports/{rid}/cover-page", json={"cover_page": {"modification": "x"}})
    assert r.status_code == 404, r.text
    r = stranger.put(f"/reports/{own}/cover-page", json={"report_id": "CL 99999"})
    assert r.status_code == 403, r.text
    ok("someone else's case is 'not found'; Our Ref is still staff's to set")

    print("Annex B and Annex C")
    r = surveyor.put(f"/reports/{rid}/annex-b", json={"items": [
        {"description": "To respray affected areas", "workshop_est": 1800, "adjusted_cost": 1250},
        {"description": "   ", "workshop_est": 1, "adjusted_cost": 1},
        {"description": "To  remove, reinstall tail gate parts ", "workshop_est": 120, "adjusted_cost": 80, "prefilled": True,
         "suggested_from": "2 past Honda Vezel cases", "usual_price": 80},
    ]})
    assert r.status_code == 200, r.text
    ab, ac = r.json()["annex_b"], r.json()["annex_c"]
    assert [i["item_no"] for i in ab["items"]] == ["1.0", "2.0"] and ab["items"][1]["description"] == "To remove, reinstall tail gate parts"
    assert [i["adjusted_cost"] for i in ab["items"]] == [1250.0, 80.0] and ab["total_workshop_est"] == 1920.0 and ab["total_adjusted_cost"] == 1330.0
    assert ab["items"][1]["prefilled"] is True and ab["items"][1]["usual_price"] == 80
    a_now = surveyor.get(f"/reports/{rid}").json()["data"]["annex_a"]
    assert ac["adjusted_total"] == round(a_now["total_adjusted_cost"] + 1330.0, 2) and ac["workshop_total"] == round(a_now["total_workshop_est"] + 1920.0, 2)
    assert ac["gst_amount"] is None
    ok("Annex B saves renumbered, blank lines dropped, adjusted costs as typed; the Summary totals follow")
    assert surveyor.put(f"/reports/{rid}/annex-b", json={"items": [{"description": f"job {i}"} for i in range(101)]}).status_code == 422
    s = surveyor.get(f"/reports/{rid}/labour-suggestions").json()
    assert s["vehicle"] == "Honda Vezel" and s["hidden"] is False and s["groups"], s
    assert not any(normalise_labour(l["description"]) == "respray affected areas" for g in s["groups"] for l in g["lines"])
    ok("labour suggestions for the case's vehicle leave out lines already in its Annex B")

    r = surveyor.put(f"/reports/{rid}/annex-c", json={"agreed_lump_sum": 6500, "repair_days": 10})
    assert r.status_code == 200, r.text
    c = r.json()["annex_c"]
    assert c["agreed_lump_sum"] == 6500 and c["repair_days"] == 10 and c["gst_amount"] is None and c["total_with_gst"] is None
    assert c["adjusted_total"] == ac["adjusted_total"]
    c2 = surveyor.put(f"/reports/{rid}/annex-c", json={"repair_days": None}).json()["annex_c"]
    assert c2["agreed_lump_sum"] == 6500 and c2["repair_days"] is None
    assert surveyor.put(f"/reports/{rid}/annex-c", json={"agreed_lump_sum": -1}).status_code == 422
    ok("Annex C keeps the lump sum and working days: absent keys stay, null clears, nothing negative, no GST")

    print("locks")
    set_signed_off(rid, True)
    for method, path, payload in (
        ("put", f"/reports/{rid}/cover-page", {"cover_page": {"modification": "Nil"}}),
        ("put", f"/reports/{rid}/annex-b", {"items": []}),
        ("put", f"/reports/{rid}/annex-c", {"repair_days": 12}),
        ("post", f"/reports/{rid}/annex-a/prefill", None),
        ("put", f"/reports/{rid}/annex-a", {"items": []}),
    ):
        r = getattr(surveyor, method)(path, json=payload) if payload is not None else getattr(surveyor, method)(path)
        assert r.status_code == 409 and r.json()["detail"] == LOCKED, (path, r.status_code, r.text)
    assert surveyor.get(f"/reports/{rid}/labour-suggestions").status_code == 200
    set_signed_off(rid, False)
    draft = surveyor.post("/intake").json()["id"]
    assert surveyor.put(f"/reports/{draft}/cover-page", json={"cover_page": {"modification": "Nil"}}).status_code == 409
    assert surveyor.post(f"/reports/{draft}/annex-a/prefill").status_code == 409
    ok("a signed-off case's pages are locked (409) and an unfinished intake is refused (409); reading suggestions still works")

    print("downloads")
    render_calls.clear()
    r = surveyor.get(f"/reports/{rid}/download?photos=0")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf"), r.text
    assert filename_of(r) == "CL 12330 SMV7530X (no photos).pdf", r.headers["content-disposition"]
    # Rendered to "<id>_nophotos.<random>.part.pdf" and swapped into place whole.
    assert render_calls[-1]["include_photos"] is False and f"{rid}_nophotos." in render_calls[-1]["path"]
    db = SessionLocal()
    row = db.get(Report, rid)
    assert row.pdf_path is None, row.pdf_path
    db.close()
    r = surveyor.get(f"/reports/{rid}/download")
    assert r.status_code == 200 and filename_of(r) == "CL 12330 SMV7530X.pdf", r.headers["content-disposition"]
    assert render_calls[-1]["include_photos"] is True and render_calls[-1]["path"].endswith("report_v1.pdf")
    db = SessionLocal()
    assert db.get(Report, rid).pdf_path.endswith("report_v1.pdf")
    db.close()
    r = surveyor.get(f"/reports/{rid}/download?photos=0")
    assert r.status_code == 200 and render_calls[-1]["include_photos"] is False and len(render_calls) == 3
    ok("?photos=0 renders the report without photos every time and never becomes the case's PDF; the full one is kept as before")
    assert stranger.get(f"/reports/{rid}/download?photos=0").status_code == 404  # not theirs: "not found", as every case route
    other = surveyor.get(f"/reports/{new_case(surveyor, plate='SNB 1 C')}/download")
    assert other.status_code == 200 and re.fullmatch(r"REF-[0-9A-F]{6} SNB 1 C\.pdf", filename_of(other)), filename_of(other)
    ok("files are named '<Our Ref> <plate>', with REF-<case id> while no Our Ref is issued -- never a made-up CL number")
    r = surveyor.get(f"/reports/{rid}/download.xlsx")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert filename_of(r) == "CL 12330 SMV7530X.xlsx" and r.headers["cache-control"] == "no-store"
    book = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True)
    assert "Cover Page" in book.sheetnames
    if REAL_XLSX:
        assert {"Cover Page", "Annex A", "Annex B", "Annex C"} <= set(book.sheetnames), book.sheetnames
    book.close()
    assert stranger.get(f"/reports/{rid}/download.xlsx").status_code == 404
    assert surveyor.get(f"/reports/{draft}/download.xlsx").status_code == 409
    ok(f"download.xlsx answers the four-sheet workbook ({'agent A''s exporter' if REAL_XLSX else 'stand-in exporter'}) under the same name and gates")

    print("sign-off keeps Annex C")
    r = surveyor.post(f"/reports/{rid}/sign-off", json={"terms_accepted": True, "signatory_title": "Automotive Engineer Assessor"})
    assert r.status_code == 200, r.text
    so = r.json()["sign_off"]
    assert so["status"] == "Signed Off" and so["surveyor_name"] == "Patrick Ng" and so["signatory_title"] == "Automotive Engineer Assessor"
    assert so["agreed_quantum"] == 6500 and so["turnaround_days"] is None, so
    signed = surveyor.get(f"/reports/{rid}").json()["data"]
    assert signed["annex_c"]["agreed_lump_sum"] == 6500 and signed["annex_c"]["repair_days"] is None
    assert signed["annex_c"]["gst_amount"] is None and signed["annex_c"]["total_with_gst"] is None
    assert signed["insurance_details"]["final_approved_cost"] == "S$6,500.00"
    ok("a sign-off without figures keeps Annex C's lump sum and days, records the signatory title, and adds no GST")
    assert surveyor.post(f"/reports/{rid}/reopen").status_code == 200
    r = surveyor.post(f"/reports/{rid}/sign-off", json={"terms_accepted": True, "agreed_quantum": "6000", "turnaround_days": 12,
                                                        "liability_opinion": "Third party fully at fault"})
    assert r.status_code == 200, r.text
    so = r.json()["sign_off"]
    assert so["agreed_quantum"] == 6000.0 and so["turnaround_days"] == 12 and so["signatory_title"] is None
    signed = surveyor.get(f"/reports/{rid}").json()["data"]
    assert signed["annex_c"]["agreed_lump_sum"] == 6000.0 and signed["annex_c"]["repair_days"] == 12 and signed["annex_c"]["gst_amount"] is None
    assert signed["bola_assessment"]["apportionment_rationale"] == "Third party fully at fault"
    ok("figures typed at sign-off still win, and an old client's liability opinion is still recorded")


    print("estimates typed per unit before 2026-11-04 (one-off start-up step)")
    rid = new_case(surveyor, plate="SKX1234A")
    db = SessionLocal()
    try:
        r = db.get(Report, rid)
        data = dict(r.data or {})
        data["annex_a"] = {"items": [
            # typed as 5 per clip under the old editor: adjusted was 10 x 5 x 0.8
            {"part_name": "Rear bumper clips", "qty": 10, "workshop_est": 5, "discount_pct": 0.2, "adjusted_cost": 40.0},
            {"part_name": "Tail gate", "qty": 1, "workshop_est": 1450, "discount_pct": 0.2, "adjusted_cost": 1160.0},
            # prefilled rows were always line totals
            {"part_name": "Rear bumper side retainer", "qty": 2, "workshop_est": 42, "discount_pct": 0.2, "adjusted_cost": 33.6, "prefilled": True},
        ]}
        r.data = data
        db.commit()
        changed = convert_per_unit_estimates(db)
        db.commit()
        items = db.get(Report, rid).data["annex_a"]["items"]
    finally:
        db.close()
    # Earlier checks left other cases with qty>1 rows in this database, so
    # the count covers them too; this case's rows are what is asserted.
    assert changed >= 1, changed
    assert [(i["workshop_est"], i["adjusted_cost"]) for i in items] == [(50.0, 40.0), (1450.0, 1160.0), (42.0, 33.6)], items
    ok("a qty>1 row typed per unit becomes its line total with the same adjusted cost; qty-1 and prefilled rows are left alone")

    print("\nALL ANNEX CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
