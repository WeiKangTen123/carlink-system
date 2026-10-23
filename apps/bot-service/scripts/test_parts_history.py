"""Past-report history, Annex A suggestions and saving Annex A
(app/parts/history.py, app/api/parts.py).

    python scripts/test_parts_history.py [example_case folder]

Offline. With the example_case folder, the real CL 11900 report is parsed
too; without it, only the generated spreadsheet is.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="parts-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import openpyxl  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.api.parts as parts_api  # noqa: E402
from app.api.main import app  # noqa: E402
from app.parts.history import family_and_side, family_of, normalise_item, parse_report  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import Report  # noqa: E402
from auth_helpers import sign_in  # noqa: E402


def ok(label: str) -> None:
    print(f"    ok   {label}")


def report_xlsx(ref: str, plate: str, make_model: str, lines: list[tuple]) -> bytes:
    """A spreadsheet laid out like the firm's CL reports."""
    book = openpyxl.Workbook()
    cover = book.active
    cover.title = "Cover Page"
    cover.append([None, "ACCIDENT VEHICLE INSPECTION / ASSESSMENT REPORT"])
    cover.append([None, f"Our  Ref: {ref} {plate}"])
    cover.append([None, None, "Date of survey", ":", "27th January 2018"])
    cover.append([None, None, "Registration No.", ":", plate])
    cover.append([None, None, "Make & Model", ":", make_model])
    cover.append([None, None, "Date of registration", ":", "12th January 2017"])
    annex = book.create_sheet("Annex A")
    annex.append([None, None, plate])
    annex.append([None, "Item", "Vehicle Parts Description", "Condition", "Remarks", "Qty", "W/shop   Est. ($)", "Disc       (%)", "Adjusted    Cost ($)"])
    annex.append([None, None, "List Items"])
    nett = False
    for i, line in enumerate(lines, start=1):
        if line == "NETT":
            annex.append([None, None, "S/Nett Items"])
            nett = True
            continue
        item, cond, qty, est, adj = line
        annex.append([None, float(i), item, cond, "Replace", qty, est, None if nett else 0.2, adj])
    annex.append([None, None, "Total for Spare Parts :", None, None, None, 0, None, 0])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


VEZEL_A = [
    ("Tail gate", "Dented/bent", 1, 1450, 1160),
    ("Tail gate lock striker", "Distorted/slack", 1, 90, 72),
    ("Tail gate stopper", "Necessary", 2, 50, 40),
    ("Rear windscreen glass moulding", "Necessary", 1, 150, 120),
    ("Tail lamp LH", "Grazed/cracked", 1, 652, 521.6),
    "NETT",
    ("Reverse sensor", "Cut/faulty", "1set", 380, 380),
]
VEZEL_B = [
    ("Tail gate assy", "Buckled", 1, 1450, 1200),
    ("Tail gate lock striker", "Bent", 1, 100, 80),
    ("Tail gate stopper", "Necessary", 2, 50, 40),
    ("Rear windscreen glass moulding", "Necessary", 1, 90, 72),
    ("Rear windscreen glass clips", "Necessary", 14, 70, 56),
]


def main() -> int:
    print("parsing and part families")
    path = Path(_tmp, "a.xlsx")
    path.write_bytes(report_xlsx("CL 11900", "SLK 3063 Z", "Honda Vezel 1.5X CVT", VEZEL_A))
    case = parse_report(path)
    assert (case.ref, case.plate, case.make, case.model, case.registered) == ("CL 11900", "SLK3063Z", "Honda", "Vezel", "2017-01-12"), case
    assert [l.item for l in case.lines][-1] == "Reverse sensor" and case.lines[-1].section == "nett" and case.lines[-1].qty == 1.0
    assert case.lines[0].adjusted_cost == 1160 and case.lines[0].discount == 0.2
    assert family_of("Tail gate lock striker") == "Boot Lid Lock" and family_of("Tail lamp LH") == "Tail Lamp"
    assert family_of("Rear windscreen glass moulding") == "Rear Windscreen" and family_of("Rear bumper beam (inner)") == "Rear Bumper Beam"
    assert family_and_side("Left Tail Lamp") == ("Tail Lamp", "Left") and family_and_side("Boot Lid") == ("Boot Lid", None)
    assert normalise_item("Tail gate assy") == normalise_item("Tail Gate") == "tailgate"
    ok("a CL report spreadsheet reads into reference, vehicle and Annex A lines (list and S/Nett)")
    if len(sys.argv) > 1:
        real = next(Path(sys.argv[1]).rglob("CL 11900*.xls"))
        rc = parse_report(real)
        assert rc.ref == "CL 11900" and (rc.make, rc.model) == ("Honda", "Vezel") and len(rc.lines) == 37, (rc.ref, rc.make, rc.model, len(rc.lines))
        ok(f"the real {real.name} reads as 37 lines of a Honda Vezel")

    admin = TestClient(app, base_url="https://carlink.test")
    sign_in(admin, role="admin", name="Kelvin Lim")
    surveyor = TestClient(app, base_url="https://carlink.test")
    sign_in(surveyor, role="surveyor")

    print("importing")
    files = [
        ("files", ("CL 11900 SLK 3063 Z.xlsx", report_xlsx("CL 11900", "SLK 3063 Z", "Honda Vezel 1.5X CVT", VEZEL_A), "application/octet-stream")),
        ("files", ("CL 12330 SMV 7530 X.xlsx", report_xlsx("CL 12330", "SMV 7530 X", "Honda Vezel 1.5X CVT", VEZEL_B), "application/octet-stream")),
        ("files", ("notes.txt", b"hello", "text/plain")),
    ]
    assert surveyor.post("/parts-history/import", files=files).status_code == 403
    r = admin.post("/parts-history/import", files=files)
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert [x.get("lines") for x in res[:2]] == [6, 5] and "error" in res[2], res
    again = admin.post("/parts-history/import", files=files[:1]).json()["results"][0]
    assert again["lines"] == 6
    hist = surveyor.get("/parts-history").json()
    assert len(hist["cases"]) == 2
    striker = next(l for l in hist["lines"] if "striker" in l["item"].lower())
    assert striker["cases"] == 2 and striker["usual_unit_price"] == 76.0 and striker["min_price"] == 72.0
    ok("admins import reports (re-import replaces), other files are refused by name; history shows usual prices")

    print("suggestions")
    r = admin.post("/reports", json={"draft": {
        "category": ["Vehicle Collision or Damage"], "description": "Rear-ended",
        "vehicle_info": {"plate_number": "SNA1234B", "make": "Honda", "model": "Vezel"},
        "damage_summary": [{"part": "Boot Lid", "severity": "Severe"}, {"part": "Right Tail Lamp"}],
    }})
    rid = r.json()["id"]
    s = surveyor.get(f"/reports/{rid}/annex-suggestions").json()
    assert s["scope"] == "past Honda Vezel cases" and s["cases_used"] == 2, s
    boot = next(g for g in s["groups"] if g["part"] == "Boot Lid")
    names = [l["name"] for l in boot["lines"]]
    assert any(normalise_item(n) == "tailgate" for n in names), names
    tail = next(g for g in s["groups"] if g["part"] == "Right Tail Lamp")
    assert tail["lines"][0]["name"] == "Tail lamp RH" and tail["lines"][0]["usual_unit_price"] == 521.6, tail
    companions = [g for g in s["groups"] if g["reason"].startswith("often replaced with")]
    assert any(g["part"] == "Rear Windscreen" for g in companions), [g["part"] for g in s["groups"]]
    lock = next(g for g in s["groups"] if g["part"] == "Boot Lid Lock")
    assert lock["lines"][0]["usual_unit_price"] == 76.0 and lock["lines"][0]["cases"] == 2
    ok("suggestions per damaged part, same make and model, mirrored to the case's side, with usual prices")
    ok("parts often replaced together (rear windscreen trims with a tailgate) come as companions")
    from app.parts.history import suggest
    from app.reports.db import SessionLocal as _S
    db = _S()
    long_model = suggest(db, make="honda", model="Vezel 1.5X CVT", damaged_parts=["Boot Lid"], existing_items=[])
    db.close()
    assert long_model["scope"] == "past honda Vezel cases" and long_model["cases_used"] == 2, long_model["scope"]
    ok("a case's longer model name (\"Vezel 1.5X CVT\") still matches the history's \"Vezel\"")

    print("saving Annex A")
    items = [
        {"part_name": "Tail gate assy", "condition": "Buckled", "qty": 1, "workshop_est": 1450, "discount_pct": 0.2,
         "suggested_from": "2 past Honda Vezel cases", "usual_unit_price": 1180.0},
        {"part_name": "Tail gate stopper", "condition": "Necessary", "qty": 2, "workshop_est": 25, "discount_pct": 0.2},
        {"part_name": "Reverse sensor", "qty": 1, "workshop_est": 380, "is_net_item": True},
        {"part_name": "   ", "qty": 1},
    ]
    r = surveyor.put(f"/reports/{rid}/annex-a", json={"items": items})
    assert r.status_code == 200, r.text
    a = r.json()["annex_a"]
    assert [i["item_no"] for i in a["items"]] == ["1.0", "2.0", "3.0"]
    assert [i["adjusted_cost"] for i in a["items"]] == [1160.0, 40.0, 380.0]
    assert a["total_adjusted_cost"] == 1580.0 and a["total_workshop_est"] == 1880.0
    saved = surveyor.get(f"/reports/{rid}").json()["data"]
    assert saved["annex_a"]["items"][0]["suggested_from"] == "2 past Honda Vezel cases"
    assert saved["annex_c"]["adjusted_total"] == 1580.0
    s2 = surveyor.get(f"/reports/{rid}/annex-suggestions").json()
    boot2 = next((g for g in s2["groups"] if g["part"] == "Boot Lid"), {"lines": []})
    assert not any(normalise_item(l["name"]) == "tailgate" for l in boot2["lines"])
    ok("Annex A saves with adjusted costs and totals worked out on the server; blank rows dropped")
    ok("lines already in Annex A aren't suggested again")

    db = SessionLocal()
    row = db.get(Report, rid)
    row.data = {**row.data, "sign_off": {**(row.data.get("sign_off") or {}), "status": "Signed Off"}}
    db.commit()
    db.close()
    assert surveyor.put(f"/reports/{rid}/annex-a", json={"items": items}).status_code == 409
    ok("a signed-off case's Annex A is locked")

    print("AI fill-in")
    parts_api.call_structured = lambda **kw: {"parts": [{"part": "Front Grille", "lines": [{"name": "Front grille", "qty": 1}, {"name": "Front grille clips", "qty": 6}]}]}
    r = surveyor.post(f"/reports/{rid}/annex-suggestions/ai", json={"parts": ["Front Grille"]})
    g = r.json()["groups"][0]
    assert g["reason"].startswith("AI suggestion") and g["lines"][1]["qty"] == 6 and g["lines"][0]["usual_unit_price"] is None
    def boom(**kw):
        raise RuntimeError("quota")
    parts_api.call_structured = boom
    assert surveyor.post(f"/reports/{rid}/annex-suggestions/ai", json={"parts": ["Front Grille"]}).status_code == 503
    ok("parts with no history can get AI lines: names and quantities only, labelled, no price")

    assert surveyor.delete("/parts-history/CL 12330").status_code == 403
    assert admin.delete("/parts-history/CL 12330").json()["lines"] == 5
    assert len(admin.get("/parts-history").json()["cases"]) == 1
    ok("an admin can remove an imported report")

    print("\nALL PARTS-HISTORY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
