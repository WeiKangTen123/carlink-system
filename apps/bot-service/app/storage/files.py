import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


def incident_dir(incident_id: str) -> Path:
    """incidents/{year}/{month}/{incident_id}/ -- matches docs/proposal.md section G."""
    now = datetime.now(timezone.utc)
    d = Path(settings.storage_dir) / "incidents" / f"{now.year:04d}" / f"{now.month:02d}" / incident_id
    (d / "photos").mkdir(parents=True, exist_ok=True)
    return d


def save_photo(incident_id: str, source_path: str, index: int) -> str:
    d = incident_dir(incident_id)
    ext = Path(source_path).suffix or ".jpg"
    dest = d / "photos" / f"photo_{index:02d}{ext}"
    shutil.copy(source_path, dest)
    return str(dest)


def report_pdf_path(incident_id: str, version: int = 1) -> str:
    d = incident_dir(incident_id)
    return str(d / f"report_v{version}.pdf")


def tmp_dir() -> Path:
    d = Path(settings.storage_dir) / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def to_public_url(abs_path: str) -> str:
    """Converts an absolute on-disk path into a URL servable via the /files
    static mount in app/api/main.py -- what the dashboard links/images use.
    """
    rel = Path(abs_path).resolve().relative_to(Path(settings.storage_dir).resolve())
    return "/files/" + str(rel).replace("\\", "/")
