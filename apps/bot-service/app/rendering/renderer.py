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
# own. A render waits up to RENDER_SLOT_WAIT_SECONDS for a free slot -- two
# big reports ahead of it take about a minute -- and then gives up with
# RenderBusy. The per-person limit on renders is api/limits.py's.
MAX_CONCURRENT_RENDERS = 2
RENDER_SLOT_WAIT_SECONDS = 120
_render_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RENDERS)


class RenderBusy(RuntimeError):
    """Every Chromium slot stayed busy for RENDER_SLOT_WAIT_SECONDS. A
    RuntimeError like every other render failure, so callers that already
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


def render_pdf(report: dict, photo_paths: list[str], output_path: str, report_id=None, unverified: bool | None = None) -> None:
    """unverified: whether to print the unverified-owner banner. Left None,
    it comes from the case's owner (_owner_unverified); pass it only when
    the case isn't saved yet but has an owner (POST /reports)."""
    if unverified is None:
        unverified = _owner_unverified(report_id)
    try:
        template = _env.get_template("security_incident.html")
        html = template.render(
            report=report,
            report_id=report_id or report.get("report_id") or "—",
            # A report's own company_name still wins when set (it belongs to
            # that specific inspection); the Settings value is the fallback
            # for the usual case where the report doesn't carry one -- which
            # is every real report currently on file.
            org_name=_org_name(),
            photo_data_uris=[_to_data_uri(p) for p in (photo_paths or [])],
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

    html_to_pdf(html, output_path)


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


def html_to_pdf(html: str, output_path: str) -> None:
    """Print a fully self-contained HTML page to an A4 PDF, offline.

    Scripts are off and every request except data: URIs is refused, so the
    page can't fetch anything even if a field slipped markup through. At
    most MAX_CONCURRENT_RENDERS run at once; RenderBusy if no slot frees up.
    """
    if not _render_slots.acquire(timeout=RENDER_SLOT_WAIT_SECONDS):
        logger.warning("No PDF render slot free after %ss for %s", RENDER_SLOT_WAIT_SECONDS, output_path)
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
