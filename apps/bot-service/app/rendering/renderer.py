"""HTML/Jinja2 -> PDF rendering via Playwright (headless Chromium).

Chosen over docx-based generation and over WeasyPrint specifically because
it's the most reliable option to get running on Windows without extra
system dependencies -- `playwright install chromium` is the only setup
step. See docs/proposal.md section E for the fuller tradeoff.
"""
import base64
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _to_data_uri(path: str) -> str:
    media_type, _ = mimetypes.guess_type(path)
    media_type = media_type or "image/jpeg"
    data = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")
    return f"data:{media_type};base64,{data}"


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


def render_pdf(report: dict, photo_paths: list[str], output_path: str, report_id=None) -> None:
    template = _env.get_template("security_incident.html")
    html = template.render(
        report=report,
        report_id=report_id or report.get("report_id") or "—",
        # A report's own company_name still wins when set (it belongs to
        # that specific inspection); the Settings value is the fallback
        # for the usual case where the report doesn't carry one -- which
        # is every real report currently on file.
        org_name=_org_name(),
        photo_data_uris=[_to_data_uri(p) for p in photo_paths],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html)
            page.pdf(
                path=output_path,
                format="A4",
                print_background=True,
                margin={"top": "0mm", "bottom": "12mm", "left": "0mm", "right": "0mm"},
            )
        finally:
            browser.close()
