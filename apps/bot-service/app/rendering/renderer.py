"""HTML/Jinja2 -> PDF rendering via Playwright (headless Chromium).

Chosen over docx-based generation and over WeasyPrint specifically because
it's the most reliable option to get running on Windows without extra
system dependencies -- `playwright install chromium` is the only setup
step. See docs/proposal.md section E for the fuller tradeoff.
"""
import base64
import logging
import mimetypes
import threading
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Chromium takes 200-500 MB per render on a small VM shared with another app,
# and nothing limited how many ran at once (about 40 could: one per API
# worker thread). Per process: the api and the Telegram bot each have their
# own. A render waits for a free slot and then gives up with RenderBusy.
#
# How long it waits is the caller's. The api's renders run on Starlette's
# pool of 40 threads, which also signs every request in, so 20 renders each
# waiting two minutes for a slot held the whole site up: they wait
# RENDER_SLOT_WAIT_SECONDS, then 429 (or leave the PDF to later). The
# Telegram bot renders rarely, on its own threads, and its PDF has to be
# made, so it waits BOT_RENDER_SLOT_WAIT_SECONDS -- two big reports ahead of
# it take about a minute. A parameter, not a setting per process: `python -m
# app.main` without flags runs both in one. The per-person limits are
# api/limits.py's.
MAX_CONCURRENT_RENDERS = 2
RENDER_SLOT_WAIT_SECONDS = 3
BOT_RENDER_SLOT_WAIT_SECONDS = 120
_render_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RENDERS)


class RenderBusy(RuntimeError):
    """Every Chromium slot stayed busy for as long as the caller would wait.
    A RuntimeError like every other render failure, so callers that already
    carry on without the PDF still do."""


TEMPLATES_DIR = Path(__file__).parent / "templates"
# Autoescape is a security requirement, not a style choice: report fields are
# typed by users and by anyone messaging the Telegram bot, so they must print
# as text. Unescaped, a name like <iframe src=http://internal-host/> made
# Chromium fetch that page into the PDF.
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

BLANK_IMAGE_DATA_URI = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

# The signer's title when sign-off recorded none (SignOffInfo.signatory_title).
DEFAULT_SIGNATORY_TITLE = "Automotive Engineer Assessor"
# What the letter says in place of a figure the surveyor hasn't agreed yet.
TO_BE_AGREED = "(to be agreed)"


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def money(value) -> str:
    """7475.04 -> "7,475.04": how the firm's tables print money. The letter
    (Annex C) puts "S$" in front."""
    return f"{_num(value):,.2f}"


_env.filters["money"] = money


def _qty_text(row: dict) -> str:
    """"1set" when the surveyor typed it, else the number without a
    pointless ".0"."""
    text = str(row.get("qty_text") or "").strip()
    if text:
        return text
    qty = _num(row.get("qty")) or 1.0
    return str(int(qty)) if qty == int(qty) else f"{qty:g}"


def _tyre_text(tyre: dict | None) -> str:
    """"7mm/Dunlop", as the firm writes one tyre; whatever part is recorded
    when only the depth or only the make is; "" when neither is."""
    tyre = tyre or {}
    depth = tyre.get("tread_depth_mm")
    parts = []
    if isinstance(depth, (int, float)) and not isinstance(depth, bool):
        parts.append(f"{depth:g}mm")
    if tyre.get("brand"):
        parts.append(str(tyre["brand"]).strip())
    return "/".join(parts)


def report_view(report: dict) -> dict:
    """The rows, totals and letter figures that both the PDF template and
    the workbook (xlsx_export.py) print, worked out once so the two can't
    disagree.

    Totals are summed from the rows printed, never read from
    annex_a.total_* / annex_c.*: a table whose total doesn't add up is worse
    than a stale stored figure, and the backend works those out the same
    way. Annex A prints List Items first, then S/Nett Items, numbered in
    that order (the firm's layout); W/shop Est is the line total as quoted.
    Nothing here invents a value: a figure nobody recorded comes out as 0 /
    "" and the template decides how to say so.
    """
    report = report or {}
    vehicle = report.get("vehicle_info") or {}
    insurance = report.get("insurance_details") or {}
    annex_c = report.get("annex_c") or {}
    sign = report.get("sign_off") or {}

    rows = []
    for item in (report.get("annex_a") or {}).get("items") or []:
        item = item or {}
        nett = bool(item.get("is_net_item"))
        discount = item.get("discount_pct")
        rows.append({
            "name": str(item.get("part_name") or "").strip(),
            "condition": str(item.get("condition") or "").strip(),
            "remarks": str(item.get("action") or "").strip(),
            "qty": _num(item.get("qty")) or 1.0,
            "qty_text": _qty_text(item),
            "est": round(_num(item.get("workshop_est")), 2),
            # Blank for nett items (no discount applies) and for a row that
            # never had one recorded; 0.2 prints as 20%.
            "disc": None if nett or discount is None else _num(discount),
            "adjusted": round(_num(item.get("adjusted_cost")), 2),
            "nett": nett,
        })
    list_rows = [r for r in rows if not r["nett"]]
    nett_rows = [r for r in rows if r["nett"]]
    for number, row in enumerate(list_rows + nett_rows, start=1):
        row["no"] = number

    labour = []
    for item in (report.get("annex_b") or {}).get("items") or []:
        item = item or {}
        labour.append({
            "no": len(labour) + 1,
            "description": str(item.get("description") or "").strip(),
            "est": round(_num(item.get("workshop_est")), 2),
            "adjusted": round(_num(item.get("adjusted_cost")), 2),
        })

    a_est = round(sum(r["est"] for r in rows), 2)
    a_adjusted = round(sum(r["adjusted"] for r in rows), 2)
    b_est = round(sum(r["est"] for r in labour), 2)
    b_adjusted = round(sum(r["adjusted"] for r in labour), 2)

    tyres = vehicle.get("tyres") or {}

    def axle(prefix: str) -> dict:
        nearside = tyres.get(f"{prefix}_nearside") or {}
        offside = tyres.get(f"{prefix}_offside") or {}
        return {
            "nearside": _tyre_text(nearside),
            "offside": _tyre_text(offside),
            "size": str(nearside.get("size") or offside.get("size") or "").strip(),
        }

    lump_sum = annex_c.get("agreed_lump_sum")
    repair_days = annex_c.get("repair_days")
    signed = sign.get("status") == "Signed Off"
    return {
        "our_ref": str(report.get("report_id") or "").strip() or None,
        "plate": str(vehicle.get("plate_number") or "").strip(),
        "make_model": " ".join(str(x).strip() for x in (vehicle.get("make"), vehicle.get("model")) if x and str(x).strip()),
        "workshop": str(insurance.get("workshop_assigned") or "").strip(),
        "list_rows": list_rows,
        "nett_rows": nett_rows,
        "labour": labour,
        "a_est": a_est,
        "a_adjusted": a_adjusted,
        "b_est": b_est,
        "b_adjusted": b_adjusted,
        "c_est": round(a_est + b_est, 2),
        "c_adjusted": round(a_adjusted + b_adjusted, 2),
        # The letter's figures. Nothing priced yet: said so, not "S$0.00".
        "c_est_text": f"S${money(a_est + b_est)}" if a_est + b_est else "(not yet assessed)",
        "c_adjusted_text": f"S${money(a_adjusted + b_adjusted)}" if a_est + b_est else "(not yet assessed)",
        "lump_sum_text": f"S${money(lump_sum)}" if _num(lump_sum) > 0 else TO_BE_AGREED,
        "repair_days_text": str(int(_num(repair_days))) if _num(repair_days) > 0 else TO_BE_AGREED,
        "tyres": {"front": axle("front"), "rear": axle("rear")},
        "signed": signed,
        "signer": str(sign.get("surveyor_name") or sign.get("approved_by") or sign.get("reviewed_by") or "").strip(),
        "signatory_title": str(sign.get("signatory_title") or "").strip() or DEFAULT_SIGNATORY_TITLE,
        "qualifications": str(sign.get("qualifications") or "").strip(),
        "signature_data_url": sign.get("signature_data_url") if signed else None,
    }


def _to_data_uri(path: str) -> str:
    if not path or not Path(path).exists():
        return BLANK_IMAGE_DATA_URI
    try:
        from app.storage.files import generate_pdf_image
        # A 1600px JPEG made for the PDF (or generated now), falling back to
        # the original. Downscaled because 15MB+ camera images made the
        # Playwright HTML payload ~300MB; JPEG rather than the WebP preview
        # because Chromium re-encodes WebP losslessly into the PDF, which
        # made a 65-photo report 117MB.
        pdf_image = generate_pdf_image(path)
        target_path = Path(pdf_image) if pdf_image else Path(path)
        media_type, _ = mimetypes.guess_type(str(target_path))
        media_type = media_type or "image/jpeg"
        data = base64.standard_b64encode(target_path.read_bytes()).decode("utf-8")
        return f"data:{media_type};base64,{data}"
    except Exception as exc:
        logger.warning("Could not convert %s to data URI: %s", path, exc)
        return BLANK_IMAGE_DATA_URI


def _org_name() -> str:
    """The operator's company name from Settings, used on the PDF header.

    Looked up here rather than passed in by callers because render_pdf is
    invoked from six places (both channel adapters plus create/update/
    sign-off/reopen), and threading an extra argument through all of them
    would be easy to miss on the next one added.

    Imported lazily and wrapped: a rendering failure must never be caused
    by a settings lookup, so any DB problem falls back to the default
    rather than propagating.
    """
    try:
        from app.reports.db import SessionLocal
        from app.reports.models import AppSetting

        db = SessionLocal()
        try:
            row = db.get(AppSetting, "company_name")
            if row and row.value.strip():
                return row.value.strip()
        finally:
            db.close()
    except Exception:
        pass
    return "Carlink Consultancy"


def _owner_unverified(case_id) -> bool:
    """Whether the case belongs to an account outside the firm: not an admin
    and not ticked "May sign off" (permissions.is_staff). Its PDF then says
    so on every page, since the template prints the firm's letterhead on
    everyone's reports. Unassigned cases (the Telegram bot's) and cases not
    saved yet have no owner, so no banner.

    Looked up here for the same reason as _org_name. Unlike that, a failed
    lookup prints the banner: it is what keeps a stranger's PDF from passing
    as the firm's."""
    if not case_id:
        return False
    try:
        from app.auth.permissions import is_staff
        from app.reports.db import SessionLocal
        from app.reports.models import Report, User

        db = SessionLocal()
        try:
            owner_id = db.query(Report.owner_id).filter(Report.id == case_id).scalar()
            if owner_id is None:
                return False
            owner = db.query(User.role, User.may_sign_off).filter(User.id == owner_id).first()
            return owner is None or not is_staff(owner)
        finally:
            db.close()
    except Exception:
        logger.warning("Could not read the owner of case %s; printing the unverified banner", case_id, exc_info=True)
        return True


def _signed_off_by_staff(report: dict) -> bool:
    """Whether staff (permissions.is_staff, read now) signed the case off:
    then the firm has vetted it, and its PDF needs no unverified banner
    whoever owns it. Read from the data being rendered, not the stored row,
    since sign-off renders before it commits. Only /sign-off writes
    signed_by_user_id -- every other save keeps the stored sign_off -- and
    reopening sets the status back to Draft.

    Fails closed: a signer who can't be looked up keeps the banner."""
    sign_off = (report or {}).get("sign_off") or {}
    signer_id = sign_off.get("signed_by_user_id")
    if sign_off.get("status") != "Signed Off" or not signer_id:
        return False
    try:
        from app.auth.permissions import is_staff
        from app.reports.db import SessionLocal
        from app.reports.models import User

        db = SessionLocal()
        try:
            signer = db.query(User.role, User.may_sign_off).filter(User.id == signer_id).first()
            return signer is not None and is_staff(signer)
        finally:
            db.close()
    except Exception:
        logger.warning("Could not read signer %s; keeping the unverified banner", signer_id, exc_info=True)
        return False


def render_pdf(
    report: dict,
    photo_paths: list[str],
    output_path: str,
    report_id=None,
    unverified: bool | None = None,
    slot_wait: float | None = None,
    include_photos: bool = True,
) -> None:
    """unverified: whether to print the unverified-owner banner. Left None,
    it comes from the case's owner (_owner_unverified), unless staff signed
    it off (_signed_off_by_staff); pass it only when the case isn't saved
    yet but has an owner (POST /reports).

    slot_wait: how long to wait for a Chromium slot (html_to_pdf).

    include_photos: False leaves out the Photographs appendix (the firm's
    report proper is the four pages) and skips reading the photos at all --
    they are what makes a render slow and the PDF big."""
    if unverified is None:
        unverified = _owner_unverified(report_id) and not _signed_off_by_staff(report)
    try:
        template = _env.get_template("security_incident.html")
        html = template.render(
            report=report,
            report_id=report_id or report.get("report_id") or "—",
            view=report_view(report),
            # The company name from Settings; the template prints it in
            # the letterhead and signs the letter with it.
            org_name=_org_name(),
            photo_data_uris=[_to_data_uri(p) for p in (photo_paths or [])] if include_photos else [],
            include_photos=include_photos,
            # No longer printed (the firm's report carries no generation
            # stamp); kept for templates that still look for it.
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            unverified_owner=unverified,
        )
    except Exception as exc:
        logger.error("Template rendering failed for %s: %s", output_path, exc, exc_info=True)
        raise RuntimeError(f"Template rendering failed: {exc}") from exc

    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.error("Failed to create directory for %s: %s", output_path, exc, exc_info=True)
        raise RuntimeError(f"Failed to create output directory: {exc}") from exc

    html_to_pdf(html, output_path, slot_wait=slot_wait)


def _offline_only(route) -> None:
    # Photos arrive inlined as data: URIs and the template links nothing
    # external, so a real report never needs the network. Anything else is
    # markup that got past escaping trying to reach a host from inside the
    # container -- refuse it.
    if route.request.url.startswith("data:"):
        route.continue_()
    else:
        logger.warning("Blocked a network request while rendering a PDF: %.200s", route.request.url)
        route.abort()


def html_to_pdf(html: str, output_path: str, slot_wait: float | None = None) -> None:
    """Print a fully self-contained HTML page to an A4 PDF, offline.

    Scripts are off and every request except data: URIs is refused, so the
    page can't fetch anything even if a field slipped markup through. At
    most MAX_CONCURRENT_RENDERS run at once; RenderBusy if no slot frees up
    within slot_wait seconds (None: RENDER_SLOT_WAIT_SECONDS; 0: no wait).
    """
    wait = RENDER_SLOT_WAIT_SECONDS if slot_wait is None else slot_wait
    if not (_render_slots.acquire(timeout=wait) if wait > 0 else _render_slots.acquire(blocking=False)):
        logger.warning("No PDF render slot free after %ss for %s", wait, output_path)
        raise RenderBusy("Every PDF render slot stayed busy.")
    try:
        _chromium_pdf(html, output_path)
    finally:
        _render_slots.release()


def _chromium_pdf(html: str, output_path: str) -> None:
    try:
        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch()
                context = browser.new_context(java_script_enabled=False)
                context.route("**/*", _offline_only)
                page = context.new_page()
                page.emulate_media(media="print")
                page.set_content(html, wait_until="load")
                page.pdf(
                    path=output_path,
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "10mm", "bottom": "12mm", "left": "12mm", "right": "12mm"},
                )
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception as close_err:
                        logger.warning("Error closing Playwright browser: %s", close_err)
    except Exception as exc:
        logger.error("Playwright PDF generation failed for %s: %s", output_path, exc, exc_info=True)
        raise RuntimeError(f"Playwright PDF generation failed: {exc}") from exc
