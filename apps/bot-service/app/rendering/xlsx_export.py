"""The report as the firm's own workbook: sheets Cover Page, Annex A,
Annex B and Annex C, laid out cell for cell like the CL-xxxxx spreadsheets
in example_case (labels in column C, a ":" in D, the value in E; Annex A's
"List Items" / "S/Nett Items" sub-headers; Annex B's Summary block; Annex
C's letter one line to a row).

Two readers: the firm, who keeps working in Excel, and
app.parts.history.parse_report, which imports past reports -- so a report
exported here can be re-imported into the price book unchanged. Missing
facts stay blank (the PDF says "Not recorded"); nothing is invented.
"""
import logging
import textwrap
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

from app.rendering.renderer import (
    _org_name,
    _owner_unverified,
    _signed_off_by_staff,
    money,
    report_view,
)

logger = logging.getLogger(__name__)

NUMBER_FORMAT = "#,##0.00"
PERCENT_FORMAT = "0%"
FONT_NAME = "Arial"
# The letter's lines, as the firm wraps them by hand in column B.
LETTER_WIDTH = 100
_THIN = Side(style="thin", color="000000")
_BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# Letter wording shared with the PDF template (security_incident.html);
# change both together.
_LETTER_OPENING = (
    "We have inspected thoroughly the actual damages found on the vehicle and our assessment is "
    "appended in the Annex(es) attached. The condition and age of the vehicle was considered before "
    "we reached our recommendation as to whether the parts need replacement or repair."
)
_LETTER_PREJUDICE = (
    "We have advised the repairer that the inspection was conducted strictly on a WITHOUT PREJUDICE "
    "basis and we have not given any instruction and authorisation to proceed with the repair of the vehicle."
)
_LETTER_CLOSING = "We are reverting the matter to you for your discretion and enclosed is our invoice for service rendered."
_COVER_REMARK = 'This inspection was conducted entirely on a "WITHOUT PREJUDICE" basis and we have not authorised any repair.'


def _font(bold: bool = False, size: float = 10, italic: bool = False) -> Font:
    return Font(name=FONT_NAME, size=size, bold=bold, italic=italic)


def _widths(ws, widths: dict[str, float]) -> None:
    for column, width in widths.items():
        ws.column_dimensions[column].width = width


class _Sheet:
    """Writes cells by (row, column) with the firm's styling; keeps the
    current row so the layout reads top to bottom."""

    def __init__(self, ws):
        self.ws = ws
        self.row = 1

    def put(self, row: int, col: int, value, *, bold=False, fmt=None, align=None, wrap=False, border=False, size=10):
        cell = self.ws.cell(row=row, column=col)
        if value is not None and value != "":
            cell.value = value
        cell.font = _font(bold=bold, size=size)
        if fmt:
            cell.number_format = fmt
        if align or wrap:
            cell.alignment = Alignment(horizontal=align, vertical="top", wrap_text=wrap)
        if border:
            cell.border = _BOX
        return cell

    def merge(self, row: int, first_col: int, last_col: int) -> None:
        self.ws.merge_cells(start_row=row, start_column=first_col, end_row=row, end_column=last_col)

    def skip(self, n: int = 1) -> None:
        self.row += n


def _cover_sheet(ws, report: dict, view: dict, org: str, unverified: bool) -> None:
    s = _Sheet(ws)
    _widths(ws, {"A": 2, "B": 4, "C": 24, "D": 3, "E": 24, "F": 18, "G": 3, "H": 16})
    vehicle = report.get("vehicle_info") or {}
    insurance = report.get("insurance_details") or {}
    cover = report.get("cover_page") or {}

    # Letterhead: name, address, Tel on row 1 and Fax on row 3 as in the
    # firm's file. The numbers are text so a narrow column can't show them
    # as 6.29E+07.
    s.put(1, 2, org.upper(), bold=True, size=12)
    s.put(1, 6, "Tel : ")
    s.put(1, 8, "62856178")
    s.put(2, 2, "60 Hillside Drive")
    s.put(3, 2, "Singapore 549009")
    s.put(3, 6, " Fax : ")
    s.put(3, 8, "62874788")
    if unverified:
        # Same words as the PDF's banner: a stranger's export must not
        # pass as the firm's.
        s.put(5, 2, "Prepared by an unverified user, not issued by Carlink Consultancy", bold=True)
    s.put(7, 2, "ACCIDENT VEHICLE INSPECTION / ASSESSMENT REPORT", bold=True)
    s.put(8, 2, f"Our Ref: {view['our_ref']}" if view["our_ref"] else "Our Ref: (to be issued)", bold=True)
    s.row = 9

    def section(number: int, title: str) -> None:
        s.put(s.row, 2, number, bold=True)
        s.put(s.row, 3, title, bold=True)
        s.row += 1

    def line(label: str, value, *more_lines) -> None:
        """label | : | value, then any continuation lines (an address)
        under the value. Blank stays blank."""
        s.put(s.row, 3, label)
        s.put(s.row, 4, ":")
        s.put(s.row, 5, _text(value))
        s.merge(s.row, 5, 8)
        s.row += 1
        for extra in more_lines:
            if _text(extra):
                s.put(s.row, 5, _text(extra), wrap=False)
                s.merge(s.row, 5, 8)
                s.row += 1

    def pair(label: str, value, label2: str, value2) -> None:
        s.put(s.row, 3, label)
        s.put(s.row, 4, ":")
        s.put(s.row, 5, _text(value))
        s.put(s.row, 6, label2)
        s.put(s.row, 7, ":")
        s.put(s.row, 8, _text(value2))
        s.row += 1

    section(1, "REFERENCE")
    line("Date of request", cover.get("date_of_request"))
    line("Requested by", cover.get("requested_by"), cover.get("requested_by_address"))
    line("Type of claim", insurance.get("claim_type"))
    line("Workshop", insurance.get("workshop_assigned"), *_lines(cover.get("workshop_address")))
    line("Accident date", report.get("incident_datetime"))
    line("Date of survey", cover.get("date_of_survey"))
    line("Date of dismantle parts", cover.get("date_of_dismantle"))
    line("Date of before painting", cover.get("date_before_painting"))
    line("Date of after painting", cover.get("date_after_painting"))
    s.skip()

    section(2, "DETAILS OF VEHICLE")
    line("Registration No.", vehicle.get("plate_number"))
    line("Make & Model", view["make_model"])
    line("Date of registration", vehicle.get("date_of_registration"))
    line("Engine No.", vehicle.get("engine_number"))
    line("Chassis No.", vehicle.get("vin"))
    line("Speedometer reading", vehicle.get("odometer_reading"))
    line("Transmission", vehicle.get("transmission"))
    line("Colour", vehicle.get("color"))
    s.skip()

    section(3, "STATIC CHECK")
    pair("Steering", vehicle.get("steering_condition"), "Paintwork", vehicle.get("paintwork_condition"))
    pair("Footbrake", vehicle.get("brakes_condition"), "Modification", cover.get("modification"))
    pair("Handbrake", cover.get("handbrake_condition"), "General condition", cover.get("general_condition"))
    s.skip()

    section(4, "TYRE CONDITION")
    s.put(s.row, 5, "Nearside/Make", bold=True)
    s.put(s.row, 6, "Offside/Make", bold=True)
    s.put(s.row, 8, "Size", bold=True)
    s.row += 1
    for label, axle in (("Front tyre", view["tyres"]["front"]), ("Rear tyre", view["tyres"]["rear"])):
        s.put(s.row, 3, label)
        s.put(s.row, 4, ":")
        s.put(s.row, 5, axle["nearside"])
        s.put(s.row, 6, axle["offside"])
        s.put(s.row, 7, ":")
        s.put(s.row, 8, axle["size"])
        s.row += 1
    s.skip()

    section(5, "POINT OF IMPACT")
    s.skip()
    for text in (vehicle.get("point_of_impact"), cover.get("point_of_impact_note")):
        if _text(text):
            s.put(s.row, 3, _text(text))
            s.merge(s.row, 3, 8)
            s.row += 1
    s.skip(2)

    section(6, "REMARK")
    for text in textwrap.wrap(_COVER_REMARK, 90):
        s.put(s.row, 3, text)
        s.row += 1


def _annex_a_sheet(ws, view: dict) -> None:
    s = _Sheet(ws)
    _widths(ws, {"A": 2, "B": 6, "C": 44, "D": 20, "E": 12, "F": 7, "G": 13, "H": 9, "I": 15})
    s.put(1, 2, "Annex A : Assessment / Adjustment on Spare Parts", bold=True, size=11)
    s.put(3, 3, view["plate"], bold=True)
    headers = ["Item", "Vehicle Parts Description", "Condition", "Remarks", "Qty", "W/shop Est. ($)", "Disc (%)", "Adjusted Cost ($)"]
    for offset, label in enumerate(headers):
        s.put(4, 2 + offset, label, bold=True, align="center", wrap=True, border=True)
    ws.row_dimensions[4].height = 30
    s.row = 5

    def rows(rows_: list[dict]) -> None:
        for p in rows_:
            s.put(s.row, 2, p["no"], align="center")
            s.put(s.row, 3, p["name"])
            s.put(s.row, 4, p["condition"])
            s.put(s.row, 5, p["remarks"])
            qty = p["qty_text"]
            s.put(s.row, 6, _qty_cell(qty), align="center")
            # An unpriced row stays blank: 0.00 would read as a quote of
            # nothing. A priced row disallowed in full prints adjusted 0.
            if p["est"]:
                s.put(s.row, 7, p["est"], fmt=NUMBER_FORMAT)
                if p["disc"] is not None:
                    s.put(s.row, 8, p["disc"], fmt=PERCENT_FORMAT, align="center")
                s.put(s.row, 9, p["adjusted"], fmt=NUMBER_FORMAT)
            s.row += 1

    s.put(s.row, 3, "List Items", bold=True)
    s.row += 1
    rows(view["list_rows"])
    if view["nett_rows"]:
        s.put(s.row, 3, "S/Nett Items", bold=True)
        s.row += 1
        rows(view["nett_rows"])
    s.skip(2)
    s.put(s.row, 4, "Total for Spare Parts :", bold=True, align="right")
    s.merge(s.row, 4, 6)
    if view["a_est"]:
        s.put(s.row, 7, view["a_est"], bold=True, fmt=NUMBER_FORMAT)
        s.put(s.row, 9, view["a_adjusted"], bold=True, fmt=NUMBER_FORMAT)


def _annex_b_sheet(ws, view: dict) -> None:
    s = _Sheet(ws)
    _widths(ws, {"A": 2, "B": 6, "C": 72, "D": 18, "E": 18})
    s.put(1, 2, "Annex B : Adjustment on Labour and Spray Painting", bold=True, size=11)
    s.put(3, 3, view["plate"], bold=True)
    for offset, label in enumerate(["Item", "Job Description", "Workshop Est. ($)", "Adjusted Cost ($)"]):
        s.put(4, 2 + offset, label, bold=True, align="center", wrap=True, border=True)
    s.row = 6
    # A blank row between jobs, as the firm leaves (descriptions run long).
    for job in view["labour"]:
        s.put(s.row, 2, job["no"], align="center")
        s.put(s.row, 3, job["description"], wrap=True)
        if job["est"]:
            s.put(s.row, 4, job["est"], fmt=NUMBER_FORMAT)
            s.put(s.row, 5, job["adjusted"], fmt=NUMBER_FORMAT)
        s.row += 2
    s.put(s.row, 3, "TOTAL FOR LABOUR & SPRAY PAINTING :", bold=True)
    if view["b_est"]:
        s.put(s.row, 4, view["b_est"], bold=True, fmt=NUMBER_FORMAT)
        s.put(s.row, 5, view["b_adjusted"], bold=True, fmt=NUMBER_FORMAT)
    s.skip(2)
    s.put(s.row, 2, "Summary", bold=True, size=11)
    s.skip(2)
    s.put(s.row, 4, "Workshop Est. ($)", bold=True, align="center")
    s.put(s.row, 5, "Adjusted Cost ($)", bold=True, align="center")
    s.skip(2)
    summary = [
        ("TOTAL FOR SPARE PARTS :", view["a_est"], view["a_adjusted"], False),
        ("TOTAL FOR LABOUR & SPRAY PAINTING :", view["b_est"], view["b_adjusted"], False),
        None,
        ("TOTAL REPAIR COSTS :", view["c_est"], view["c_adjusted"], True),
    ]
    for entry in summary:
        if entry is None:
            s.row += 1
            continue
        label, est, adjusted, bold = entry
        s.put(s.row, 3, label, bold=True)
        if est:
            s.put(s.row, 4, est, bold=bold, fmt=NUMBER_FORMAT)
            s.put(s.row, 5, adjusted, bold=bold, fmt=NUMBER_FORMAT)
        s.row += 1


def _annex_c_sheet(ws, view: dict, org: str) -> None:
    s = _Sheet(ws)
    _widths(ws, {"A": 2, "B": 110})
    s.put(1, 2, org.upper(), bold=True, size=11)
    s.put(3, 2, view["plate"], bold=True)
    s.put(4, 2, "Annex C: Recommendation", bold=True)
    s.row = 7

    def paragraph(text: str) -> None:
        for line in textwrap.wrap(text, LETTER_WIDTH):
            s.put(s.row, 2, line)
            s.row += 1
        s.row += 2

    paragraph(_LETTER_OPENING)
    paragraph(
        f"{view['workshop'] or 'The repairer'} estimated repair cost is {view['c_est_text']} and we have adjusted "
        f"it to {view['c_adjusted_text']} on a part by part basis. However, taking into consideration the age of the vehicle "
        "and the availability of used/recondition components. We, therefore recommend a contract lump sum "
        "repair cost."
    )
    paragraph(
        f"The repairer have agreed to undertake the repairs on a contract lump sum of {view['lump_sum_text']}, according "
        "to acceptable quality and standard. (Repairer discretion to repair parts, to replace with old parts, or to replace "
        "with OEM or genuine parts)."
    )
    paragraph(
        f"The entire repair of the damaged vehicle should be completed within a reasonable period of {view['repair_days_text']} "
        "working days."
    )
    paragraph(_LETTER_PREJUDICE)
    paragraph(_LETTER_CLOSING)
    s.row += 1
    s.put(s.row, 2, "Yours faithfully,")
    s.row += 1
    s.put(s.row, 2, org.upper(), bold=True)
    s.row += 4
    if view["signed"]:
        # The drawn signature isn't embedded; the name, title and
        # qualifications are what the firm's own sheets carry.
        s.put(s.row, 2, (view["signer"] or "").upper(), bold=True)
        s.put(s.row + 1, 2, view["signatory_title"])
        if view["qualifications"]:
            s.put(s.row + 2, 2, view["qualifications"])
    else:
        s.put(s.row, 2, "DRAFT - not signed off", bold=True)


def _text(value) -> str:
    return str(value).strip() if value is not None and str(value).strip() else ""


def _lines(value) -> list[str]:
    """An address typed over several lines: one row each."""
    return [part.strip() for part in _text(value).splitlines() if part.strip()]


def _qty_cell(qty_text: str):
    """"1set" stays text; a plain number is written as one so Excel sums it."""
    try:
        number = float(qty_text)
    except (TypeError, ValueError):
        return qty_text
    return int(number) if number == int(number) else number


def render_xlsx(report: dict, output_path: str, report_id=None, unverified: bool | None = None) -> None:
    """Writes the four-sheet workbook for a report.

    report_id is the case's id, as render_pdf takes it: it only decides
    whether the unverified-owner line is printed (renderer._owner_unverified,
    unless staff signed the case off). The Our Ref comes from
    report["report_id"]."""
    if unverified is None:
        unverified = _owner_unverified(report_id) and not _signed_off_by_staff(report)
    view = report_view(report or {})
    org = _org_name()
    book = Workbook()
    cover = book.active
    cover.title = "Cover Page"
    _cover_sheet(cover, report or {}, view, org, unverified)
    _annex_a_sheet(book.create_sheet("Annex A"), view)
    _annex_b_sheet(book.create_sheet("Annex B"), view)
    _annex_c_sheet(book.create_sheet("Annex C"), view, org)
    for ws in book.worksheets:
        ws.sheet_view.showGridLines = False
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToHeight = 0
    book.properties.title = f"{view['our_ref'] or 'Report'} {view['plate']}".strip()
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        book.save(output_path)
    except Exception as exc:
        logger.error("Workbook export failed for %s: %s", output_path, exc, exc_info=True)
        raise RuntimeError(f"Workbook export failed: {exc}") from exc
