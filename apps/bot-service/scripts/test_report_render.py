"""The report prints as the firm's four pages, and exports as its workbook.

    python scripts/test_report_render.py [example_case folder]

With the example_case folder, the report is built from the real
CL 12330 SMV 7530 X.xls (Cover Page, Annex A, Annex B and the letter's
figures) so the output can be held against the firm's own file; without it,
a small fixture stands in. Checks: the HTML carries every page's key text
and none of the dropped extras (GST, BOLA, forensic, seal, variance); the
PDF renders offline with Chromium; the workbook reads back through
app.parts.history.parse_report with the same reference, vehicle and Annex A
lines. Offline; no database.
"""
import copy
import html as html_lib
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="report-render-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import app.rendering.renderer as renderer  # noqa: E402
from app.parts.history import parse_report  # noqa: E402
from app.rendering.xlsx_export import render_xlsx  # noqa: E402

OUT = Path(_tmp)


def ok(label: str) -> None:
    print(f"    ok   {label}")


# ------------------------------------------------------------ the report


def fixture() -> dict:
    """A small signed-off case in the shape agent B's schema gives it."""
    return {
        "report_id": "CL 12999",
        "reporter_name": "Ong Ah Kee",
        "incident_datetime": "21st February 2023",
        "cover_page": {
            "date_of_request": "27th February 2023",
            "requested_by": "Loh Keng Yew",
            "requested_by_address": "C/o Blk 3023A, Ubi Road 1, #01-60, Singapore 408717",
            "workshop_address": "Blk 3023A, Ubi Road 1, #01-60,\nSingapore 408717",
            "date_of_survey": "27th February 2023 (10.15am)",
            "date_of_dismantle": "27th February 2023 (2.25pm)",
            "date_before_painting": "1st March 2023",
            "date_after_painting": "7th March 2023",
            "handbrake_condition": "see annex(es)",
            "modification": "Nil",
            "general_condition": "Good",
            "point_of_impact_note": "See Annex(es) and Motor Accident Reports",
        },
        "vehicle_info": {
            "plate_number": "SMV 7530 X", "make": "Honda", "model": "Vezel 1.5X CVT",
            "date_of_registration": "19th October 2020", "engine_number": "L15B5578795", "vin": "RU11328756",
            "odometer_reading": "058615 Km", "transmission": "Automatic", "color": "White",
            "paintwork_condition": "Good", "steering_condition": "see annex(es)", "brakes_condition": "see annex(es)",
            "tyres": {
                "front_nearside": {"tread_depth_mm": 7, "brand": "Dunlop", "size": "215/60 R16"},
                "front_offside": {"tread_depth_mm": 7, "brand": "Dunlop", "size": "215/60 R16"},
                "rear_nearside": {"tread_depth_mm": 6.5, "brand": "Dunlop", "size": "215/60 R16"},
                "rear_offside": {"tread_depth_mm": 6.5, "brand": "Dunlop"},
            },
        },
        "insurance_details": {"claim_type": "Third party", "workshop_assigned": "M/S People's Vehicle Recovery Service"},
        "annex_a": {"items": [
            {"item_no": "1.0", "part_name": "Rear bumper fascia", "condition": "Buckled/cut", "action": "Replace", "qty": 1,
             "workshop_est": 1020.0, "discount_pct": 0.2, "adjusted_cost": 816.0, "is_net_item": False},
            {"item_no": "2.0", "part_name": "Rear bumper clips", "condition": "Necessary", "action": "", "qty": 10,
             "workshop_est": 50.0, "discount_pct": 0.2, "adjusted_cost": 40.0, "is_net_item": False},
            {"item_no": "3.0", "part_name": "Tail gate hinges", "condition": "Serviceable", "action": "", "qty": 2,
             "workshop_est": 240.0, "discount_pct": 1.0, "adjusted_cost": 0.0, "is_net_item": False},
            {"item_no": "4.0", "part_name": "Reverse sensor", "condition": "Distorted/cut/faulty", "action": "Replace", "qty": 1,
             "qty_text": "1set", "workshop_est": 280.0, "discount_pct": 0.0, "adjusted_cost": 280.0, "is_net_item": True},
        ]},
        "annex_b": {"items": [
            {"item_no": "1.0", "description": "To remove, reinstall tail gate parts", "workshop_est": 120.0, "adjusted_cost": 80.0},
            {"item_no": "2.0", "description": "To respray painting on the change bodyparts, repair portion, and where consistent to the accident",
             "workshop_est": 1200.0, "adjusted_cost": 900.0},
        ]},
        "annex_c": {"agreed_lump_sum": 1900.0, "repair_days": 7},
        "sign_off": {"status": "Signed Off", "surveyor_name": "Patrick Ng", "qualifications": "MIMI, MIRTE, LCGI, I ENG, LAE, CGLI FTC.",
                     "signatory_title": None, "signature_data_url": None},
    }


def _xls_rows(path: Path, sheet: str) -> list[list]:
    import xlrd

    book = xlrd.open_workbook(str(path))
    ws = next(s for s in book.sheets() if s.name.strip().lower() == sheet)
    return [[ws.cell_value(r, c) for c in range(ws.ncols)] for r in range(ws.nrows)]


def _tyre(text: str, size: str) -> dict:
    """"7mm/Dunlop" + "215/60 R16" -> the schema's TyreItem."""
    depth, _, brand = text.partition("/")
    m = re.search(r"\d+(?:\.\d+)?", depth)
    return {"tread_depth_mm": float(m.group(0)) if m else None, "brand": brand.strip() or None, "size": size.strip() or None}


def report_from_xls(path: Path) -> dict:
    """The firm's CL 12330 SMV 7530 X.xls as a report dict: Annex A through
    history.parse_report, the rest read off the Cover Page / Annex B / Annex
    C sheets."""
    case = parse_report(path)
    fields: dict[str, str] = {}
    pairs: dict[str, tuple[str, str]] = {}
    our_ref = None
    for row in _xls_rows(path, "cover page"):
        cells = [str(c).strip() if not (isinstance(c, float) and c.is_integer()) else str(int(c)) for c in row]
        values = [c for c in cells if c]
        if values and (m := re.match(r"Our\s+Ref:\s*(.+)", values[0])):
            our_ref = m.group(1).strip()
        # Every "label : value" on the row, two to a row on the static check.
        i = 0
        while i + 2 < len(values):
            if values[i + 1] == ":":
                fields.setdefault(values[i].lower(), values[i + 2])
                i += 3
            else:
                i += 1
        if values and values[0].lower().endswith("tyre") and len(values) >= 6:
            pairs[values[0].lower().split()[0]] = (values[2], values[3], values[5])
    front, rear = pairs.get("front", ("", "", "")), pairs.get("rear", ("", "", ""))

    labour, in_table = [], False
    for row in _xls_rows(path, "annex b"):
        text = [str(c).strip() for c in row]
        if any("job description" in t.lower() for t in text):
            in_table = True
            continue
        if not in_table:
            continue
        if any(t.upper().startswith("TOTAL FOR LABOUR") for t in text):
            break
        number = row[1] if len(row) > 1 else ""
        if isinstance(number, (int, float)) and number:
            labour.append({"item_no": f"{int(number)}.0", "description": re.sub(r"\s+", " ", text[2]),
                           "workshop_est": float(row[3] or 0), "adjusted_cost": float(row[4] or 0)})

    letter = " ".join(str(c) for row in _xls_rows(path, "annex c") for c in row if c)
    lump = re.search(r"lump sum of\s*S\$([\d,]+\.\d{2})", letter)
    days = re.search(r"period of (\d+)\s+working days", letter)
    signer = re.search(r"Yours faithfully,.*?CARLINK CONSULTANCY\s+([A-Z][A-Z ]+?)\s+Automotive", letter)

    make = case.make_model_raw.split()[0]
    return {
        "report_id": our_ref,
        "incident_datetime": fields.get("accident date"),
        "cover_page": {
            "date_of_request": fields.get("date of request"),
            "requested_by": fields.get("requested by"),
            "date_of_survey": fields.get("date of survey"),
            "date_of_dismantle": fields.get("date of dismantle parts"),
            "date_before_painting": fields.get("date of before painting"),
            "date_after_painting": fields.get("date of after painting"),
            "handbrake_condition": fields.get("handbrake"),
            "modification": fields.get("modification"),
            "general_condition": fields.get("general condition"),
            "point_of_impact_note": "See Annex(es) and Motor Accident Reports",
        },
        "vehicle_info": {
            "plate_number": fields.get("registration no."),
            "make": make,
            "model": case.make_model_raw[len(make):].strip(),
            "date_of_registration": fields.get("date of registration"),
            "engine_number": fields.get("engine no."),
            "vin": fields.get("chassis no."),
            "odometer_reading": fields.get("speedometer reading"),
            "transmission": fields.get("transmission"),
            "color": fields.get("colour"),
            "paintwork_condition": fields.get("paintwork"),
            "steering_condition": fields.get("steering"),
            "brakes_condition": fields.get("footbrake"),
            "tyres": {
                "front_nearside": _tyre(front[0], front[2]), "front_offside": _tyre(front[1], front[2]),
                "rear_nearside": _tyre(rear[0], rear[2]), "rear_offside": _tyre(rear[1], rear[2]),
            },
        },
        "insurance_details": {"claim_type": fields.get("type of claim"), "workshop_assigned": fields.get("workshop")},
        "annex_a": {"items": [
            {
                "item_no": f"{line.line_no}.0", "part_name": line.item, "condition": line.condition, "action": line.remarks,
                "qty": line.qty, "qty_text": line.qty_raw if not re.fullmatch(r"[\d.]+", line.qty_raw) else None,
                "workshop_est": line.workshop_est, "discount_pct": line.discount if line.discount is not None else 0.0,
                "adjusted_cost": line.adjusted_cost, "is_net_item": line.section == "nett",
            }
            for line in case.lines
        ]},
        "annex_b": {"items": labour},
        "annex_c": {"agreed_lump_sum": float(lump.group(1).replace(",", "")) if lump else None,
                    "repair_days": int(days.group(1)) if days else None},
        "sign_off": {"status": "Signed Off", "surveyor_name": (signer.group(1).strip().title() if signer else "Patrick Ng"),
                     "qualifications": "MIMI, MIRTE, LCGI, I ENG, LAE, CGLI FTC."},
    }


# --------------------------------------------------------------- checks


def render_html(report: dict, **kwargs) -> str:
    """The HTML handed to Chromium, without launching it."""
    captured: list[str] = []
    real = renderer.html_to_pdf
    renderer.html_to_pdf = lambda html, path, slot_wait=None: captured.append(html)
    try:
        renderer.render_pdf(report, kwargs.pop("photo_paths", []), str(OUT / "captured.pdf"), report_id="unsaved",
                            unverified=kwargs.pop("unverified", False), **kwargs)
    finally:
        renderer.html_to_pdf = real
    return captured[0]


def text_of(html: str) -> str:
    """Visible text, whitespace squeezed, so a check can read across tags."""
    body = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", body)).replace("“", '"').replace("”", '"').split())


REMOVED = ("GST", "BOLA", "forensic", "surveyor seal", "variance", "savings", "Generated", "dossier", "statutory", "SHA-256",
           "Consolidated", "Loss Adjuster", "Annex D", "reconciliation", "Grand Total", "System Ref", "Pending surveyor")


def check_html(report: dict, m) -> None:
    view = renderer.report_view(report)
    html = render_html(copy.deepcopy(report))
    text = text_of(html)

    # Cover page
    for needle in ("ACCIDENT VEHICLE INSPECTION / ASSESSMENT REPORT", f"Our Ref: {report['report_id']}", "60 Hillside Drive",
                   "Singapore 549009", "Tel : 62856178", "Fax : 62874788", "1 REFERENCE", "Date of request :",
                   f"Requested by : {report['cover_page']['requested_by']}", "Type of claim : Third party",
                   f"Workshop : {report['insurance_details']['workshop_assigned']}", "Date of dismantle parts :",
                   "2 DETAILS OF VEHICLE", "Registration No. : SMV 7530 X", "Make & Model : Honda Vezel 1.5X CVT",
                   "Chassis No. : RU11328756", "Speedometer reading : 058615 Km", "3 STATIC CHECK", "Paintwork : Good",
                   "Modification : Nil", "General condition : Good", "4 TYRE CONDITION", "Nearside/Make Offside/Make Size",
                   "Front tyre : 7mm/Dunlop 7mm/Dunlop : 215/60 R16", "5 POINT OF IMPACT", "See Annex(es) and Motor Accident Reports",
                   "6 REMARK", 'conducted entirely on a "WITHOUT PREJUDICE" basis and we have not authorised any repair.'):
        assert needle in text, needle
    ok("cover page: letterhead, Our Ref, sections 1-6 with the firm's labels, tyres per axle")

    # Annex A
    for needle in ("Annex A : Assessment / Adjustment on Spare Parts", "Item Vehicle Parts Description Condition Remarks Qty W/shop Est. ($) Disc (%) Adjusted Cost ($)",
                   "List Items", "S/Nett Items", "Total for Spare Parts :", m["a_est"], m["a_adjusted"]):
        assert needle in text, needle
    first = view["list_rows"][0]
    assert f"1 {first['name']} {first['condition']}" in text
    assert re.search(r"Rear bumper clips Necessary\s+10 50\.00 20% 40\.00", text), "clips: est is the line total, 20%"
    assert re.search(r"Tail gate hinges Serviceable\s+2 240\.00 100% 0\.00", text), "serviceable: 100%, adjusted 0.00"
    assert re.search(r"Reverse sensor Distorted/cut/faulty\s+(Replace\s+)?1set 280\.00 280\.00", text), "nett: qty text, no discount"
    nett_no = view["nett_rows"][0]["no"]
    assert f"S/Nett Items {nett_no} " in text, "numbering runs on into the nett section"
    ok("Annex A: List Items then S/Nett Items, Remarks = action, qty text, discount %, line totals")

    # Annex B + Summary
    for needle in ("Annex B : Adjustment on Labour and Spray Painting", "Item Job Description Workshop Est. ($) Adjusted Cost ($)",
                   "TOTAL FOR LABOUR & SPRAY PAINTING :", m["b_est"], m["b_adjusted"], "Summary", "TOTAL FOR SPARE PARTS :",
                   f"TOTAL REPAIR COSTS : {m['c_est']} {m['c_adjusted']}"):
        assert needle in text, needle
    ok("Annex B: labour table, total row and the Summary block (parts, labour, total repair costs)")

    # Annex C
    for needle in ("Annex C: Recommendation", "We have inspected thoroughly the actual damages found on the vehicle",
                   f"{report['insurance_details']['workshop_assigned']} estimated repair cost is S${m['c_est']} and we have adjusted it to S${m['c_adjusted']} on a part by part basis",
                   f"contract lump sum of {m['lump']}, according to acceptable quality and standard",
                   f"reasonable period of {report['annex_c']['repair_days']} working days", "strictly on a WITHOUT PREJUDICE basis",
                   "enclosed is our invoice for service rendered", "Yours faithfully, CARLINK CONSULTANCY",
                   report["sign_off"]["surveyor_name"].upper(), "Automotive Engineer Assessor", "MIMI, MIRTE, LCGI, I ENG, LAE, CGLI FTC."):
        assert needle in text, needle
    assert "DRAFT" not in text
    ok("Annex C: the firm's letter with workshop, totals, lump sum, days, signer, default title and qualifications")

    for word in REMOVED:
        assert not re.search(rf"{re.escape(word)}", text, re.I), f"{word!r} is still printed"
    # "seal" only off the letter page: "Rear windscreen glass inner seal" is a part.
    assert not re.search(r"seal", text[text.index("Annex C: Recommendation"):], re.I), "a seal is still printed"
    assert "Not recorded" not in text, "every fact of this case is recorded"
    assert "S$0.00" not in text and " 0.00 0.00" not in text.replace("100% 0.00", "")
    ok("nothing the firm's report doesn't carry: no GST, BOLA, forensic, seal, variance, generation stamp")

    # Unsigned: placeholders, the draft notice, the watermark, no signature block.
    draft = copy.deepcopy(report)
    draft["sign_off"] = {"status": "Draft", "signature_data_url": "data:image/png;base64,AAAA"}
    draft["annex_c"] = {}
    draft["report_id"] = None
    dtext = text_of(render_html(draft))
    assert "Our Ref: (to be issued)" in dtext and "CL 12" not in dtext
    assert "contract lump sum of (to be agreed)," in dtext and "period of (to be agreed) working days" in dtext
    assert "DRAFT" in dtext and "not signed off" in dtext and "Automotive Engineer Assessor" not in dtext
    assert "data:image/png" not in render_html(draft), "a draft never shows a signature"
    ok("unsigned: Our Ref (to be issued), lump sum and days (to be agreed), DRAFT notice, no signature")

    # Signed with a drawn signature and a typed title.
    signed = copy.deepcopy(report)
    signed["sign_off"].update({"signature_data_url": "data:image/png;base64,AAAA", "signatory_title": "Senior Motor Assessor"})
    shtml = render_html(signed)
    assert '<img src="data:image/png;base64,AAAA"' in shtml and "Senior Motor Assessor" in shtml
    assert "Automotive Engineer Assessor" not in shtml
    ok("signed: the drawn signature and the typed title print")

    # Missing facts are said to be missing, never filled in.
    bare = {"vehicle_info": {"plate_number": "SBA 1234 Z"}, "annex_a": {"items": [{"part_name": "Front bumper", "qty": 1}]}}
    btext = text_of(render_html(bare))
    assert btext.count("Not recorded") >= 20 and "Front bumper" in btext
    assert "(not yet assessed)" in btext and "S$0.00" not in btext
    ok("a bare case prints Not recorded for every missing fact, an unpriced row stays blank, no S$0.00 anywhere")

    # The unverified-owner banner still works.
    assert "Prepared by an unverified user, not issued by" in render_html(copy.deepcopy(report), unverified=True)
    assert "unverified user" not in html
    ok("the unverified-owner banner is kept")

    # Photographs appendix on and off.
    from PIL import Image

    photos = []
    for i in range(5):
        p = OUT / f"photo{i}.jpg"
        Image.new("RGB", (640, 480), (30 * i, 90, 160)).save(p, "JPEG")
        photos.append(str(p))
    with_photos = render_html(copy.deepcopy(report), photo_paths=photos)
    assert text_of(with_photos).count("Photographs") == 2 and "P01" in with_photos and "P05" in with_photos
    assert with_photos.count('class="page"') == 4 + 2, "five photos: two appendix pages of four"
    without = render_html(copy.deepcopy(report), photo_paths=photos, include_photos=False)
    assert "Photographs" not in text_of(without) and "data:image/jpeg" not in without
    assert without.count('class="page"') == 4
    ok("Photographs appendix: four to a page with P01.. captions, left out with include_photos=False")


def check_pdf(report: dict) -> None:
    pdf = OUT / "report.pdf"
    try:
        renderer.render_pdf(copy.deepcopy(report), [], str(pdf), report_id="unsaved", unverified=False, include_photos=False)
    except RuntimeError as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            print("    skip Chromium isn't installed (playwright install chromium)")
            return
        raise
    assert pdf.exists() and pdf.stat().st_size > 8000, pdf.stat().st_size
    ok(f"the PDF renders offline ({pdf.stat().st_size // 1024} KB)")
    try:
        import pymupdf
    except ImportError:
        print("    skip PyMuPDF isn't installed, so the pages weren't read back")
        return
    doc = pymupdf.open(pdf)
    pages = [" ".join(p.get_text().split()) for p in doc]
    assert len(pages) == 4, f"{len(pages)} pages, not the firm's four"
    assert "ASSESSMENT REPORT" in pages[0] and "TYRE CONDITION" in pages[0].upper()
    assert "Annex A" in pages[1] and "Total for Spare Parts" in pages[1]
    assert "Annex B" in pages[2] and "TOTAL REPAIR COSTS" in pages[2]
    assert "Annex C" in pages[3] and "Yours faithfully" in pages[3]
    ok("four pages: Cover Page, Annex A, Annex B with the Summary, Annex C")
    draft = copy.deepcopy(report)
    draft["sign_off"] = {"status": "Draft"}
    renderer.render_pdf(draft, [], str(OUT / "draft.pdf"), report_id="unsaved", unverified=False, include_photos=False)
    dpages = [" ".join(p.get_text().split()) for p in pymupdf.open(OUT / "draft.pdf")]
    assert all("DRAFT" in p for p in dpages), "the DRAFT watermark is on every page"
    ok("an unsigned report carries DRAFT on every page")


def check_xlsx(report: dict, source: Path | None) -> None:
    import openpyxl

    path = OUT / "export.xlsx"
    render_xlsx(copy.deepcopy(report), str(path), report_id="unsaved", unverified=False)
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["Cover Page", "Annex A", "Annex B", "Annex C"], book.sheetnames
    cover = book["Cover Page"]
    assert cover["B1"].value == "CARLINK CONSULTANCY" and cover["F1"].value.strip() == "Tel :" and cover["B2"].value == "60 Hillside Drive"
    assert cover["B8"].value == f"Our Ref: {report['report_id']}"
    labels = {(cover.cell(r, 3).value, cover.cell(r, 4).value): cover.cell(r, 5).value for r in range(1, cover.max_row + 1)}
    assert labels[("Requested by", ":")] == report["cover_page"]["requested_by"]
    assert labels[("Make & Model", ":")] == "Honda Vezel 1.5X CVT" and labels[("Front tyre", ":")] == "7mm/Dunlop"
    static = {cover.cell(r, 6).value: cover.cell(r, 8).value for r in range(1, cover.max_row + 1)
              if cover.cell(r, 3).value in ("Steering", "Footbrake", "Handbrake")}
    assert static == {"Paintwork": "Good", "Modification": "Nil", "General condition": "Good"}, static
    ok("Cover Page sheet: letterhead, label : value rows, static check pairs, tyre row")

    a = book["Annex A"]
    header = [a.cell(4, c).value for c in range(2, 10)]
    assert header == ["Item", "Vehicle Parts Description", "Condition", "Remarks", "Qty", "W/shop Est. ($)", "Disc (%)", "Adjusted Cost ($)"], header
    assert a["C5"].value == "List Items" and a["C5"].font.bold and a["B4"].font.bold
    assert a["G6"].number_format == NUMBER_FORMAT and a["H6"].number_format == "0%" and a["H6"].value == 0.2
    assert any(a.cell(r, 3).value == "S/Nett Items" for r in range(5, a.max_row + 1))
    total_row = next(r for r in range(5, a.max_row + 1) if a.cell(r, 4).value == "Total for Spare Parts :")
    assert a.cell(total_row, 7).value == renderer.report_view(report)["a_est"]
    nett = next(r for r in range(5, a.max_row + 1) if a.cell(r, 3).value == "Reverse sensor")
    assert a.cell(nett, 6).value == "1set" and a.cell(nett, 8).value is None and a.cell(nett, 9).value == 280.0
    ok("Annex A sheet: header row, List Items / S/Nett Items, 0% discount format, 1set, total row")

    b = book["Annex B"]
    assert [b.cell(4, c).value for c in range(2, 6)] == ["Item", "Job Description", "Workshop Est. ($)", "Adjusted Cost ($)"]
    col_c = [b.cell(r, 3).value for r in range(1, b.max_row + 1)]
    assert "TOTAL FOR LABOUR & SPRAY PAINTING :" in col_c and "TOTAL FOR SPARE PARTS :" in col_c and "TOTAL REPAIR COSTS :" in col_c
    assert "Summary" in [b.cell(r, 2).value for r in range(1, b.max_row + 1)]
    c = book["Annex C"]
    col_b = [str(c.cell(r, 2).value or "") for r in range(1, c.max_row + 1)]
    letter = " ".join(col_b)
    assert col_b[0] == "CARLINK CONSULTANCY" and col_b[3] == "Annex C: Recommendation"
    assert "Yours faithfully," in col_b and report["sign_off"]["surveyor_name"].upper() in col_b and "Automotive Engineer Assessor" in col_b
    assert "contract lump sum of S$" in letter and "GST" not in letter
    ok("Annex B sheet with the Summary block; Annex C letter lines and signer")

    # The acceptance: it reads back as a past report.
    back = parse_report(path)
    view = renderer.report_view(report)
    assert back.ref == re.sub(r"\s+", " ", re.search(r"CL\s*\d+", report["report_id"]).group(0)), back.ref
    assert (back.make, back.model) == ("Honda", "Vezel"), (back.make, back.model)
    assert back.plate == "SMV7530X" and back.registered == "2020-10-19"
    want = [(r["no"], r["name"], r["qty"], r["est"], r["disc"], r["adjusted"], "nett" if r["nett"] else "list")
            for r in view["list_rows"] + view["nett_rows"]]
    got = [(l.line_no, l.item, l.qty, l.workshop_est, l.discount, l.adjusted_cost, l.section) for l in back.lines]
    assert got == want, (got[:3], want[:3])
    assert back.lines[-1].qty_raw == "1set"
    ok(f"the workbook reads back through history.parse_report: ref, make/model and all {len(got)} Annex A lines")
    if source is not None:
        orig = parse_report(source)
        assert [(l.item, round(l.workshop_est, 2), l.discount, round(l.adjusted_cost, 2), l.section) for l in orig.lines] == \
               [(l.item, round(l.workshop_est, 2), l.discount, round(l.adjusted_cost, 2), l.section) for l in back.lines]
        ok(f"and line for line it matches the firm's own {source.name}")

    # A draft: blanks, not "Not recorded"; no CL number made up.
    draft = copy.deepcopy(report)
    draft.update({"report_id": None, "sign_off": {"status": "Draft"}, "annex_c": {}, "cover_page": {}})
    render_xlsx(draft, str(OUT / "draft.xlsx"), report_id="unsaved", unverified=True)
    dbook = openpyxl.load_workbook(OUT / "draft.xlsx")
    values = " | ".join(str(c.value) for ws in dbook.worksheets for row in ws.iter_rows() for c in row if c.value is not None)
    assert "Our Ref: (to be issued)" in values and "Not recorded" not in values and "CL 12" not in values
    assert "(to be agreed)" in values and "DRAFT - not signed off" in values and "Prepared by an unverified user" in values
    assert "Patrick" not in values.title()
    ok("a draft workbook: Our Ref (to be issued), blanks for missing facts, (to be agreed), DRAFT, unverified line")


NUMBER_FORMAT = "#,##0.00"


def main() -> int:
    source = None
    if len(sys.argv) > 1:
        source = next(Path(sys.argv[1]).rglob("CL 12330*.xls"))
        report = report_from_xls(source)
        print(f"report built from {source.name}: {len(report['annex_a']['items'])} parts, {len(report['annex_b']['items'])} labour lines")
        assert report["report_id"] == "CL 12330 SMV 7530 X" and report["annex_c"] == {"agreed_lump_sum": 6500.0, "repair_days": 10}
        assert report["cover_page"]["date_of_survey"] == "27th February 2023 (10.15am)" and report["sign_off"]["surveyor_name"] == "Patrick Ng"
        view = renderer.report_view(report)
        assert (view["a_est"], view["a_adjusted"], view["b_est"], view["b_adjusted"]) == (6843.8, 4935.04, 3270.0, 2540.0), view
        assert (view["c_est"], view["c_adjusted"]) == (10113.8, 7475.04)
        ok("the totals come out as the firm's: 6,843.80 / 4,935.04 parts, 3,270.00 / 2,540.00 labour, 10,113.80 / 7,475.04")
        money = {"a_est": "6,843.80", "a_adjusted": "4,935.04", "b_est": "3,270.00", "b_adjusted": "2,540.00",
                 "c_est": "10,113.80", "c_adjusted": "7,475.04", "lump": "S$6,500.00"}
    else:
        report = fixture()
        print("report from the fixture (pass the example_case folder to use the real CL 12330 file)")
        money = {"a_est": "1,590.00", "a_adjusted": "1,136.00", "b_est": "1,320.00", "b_adjusted": "980.00",
                 "c_est": "2,910.00", "c_adjusted": "2,116.00", "lump": "S$1,900.00"}

    print("HTML")
    check_html(report, money)
    print("PDF")
    check_pdf(report)
    print("workbook")
    check_xlsx(report, source)
    print("\nALL REPORT RENDER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
