import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Wide enough to stay sharp on a retina display at the ~54px the cases
# table and the ~110px the photo strip actually render, without carrying a
# full-resolution phone photo across the wire.
THUMBNAIL_MAX_EDGE = 400
THUMBNAIL_QUALITY = 78


def incident_dir(incident_id: str) -> Path:
    """incidents/{year}/{month}/{incident_id}/ -- matches docs/proposal.md section G."""
    now = datetime.now(timezone.utc)
    d = Path(settings.storage_dir) / "incidents" / f"{now.year:04d}" / f"{now.month:02d}" / incident_id
    (d / "photos").mkdir(parents=True, exist_ok=True)
    return d


def thumbnail_path_for(photo_path: str) -> Path:
    """Thumbnail lives beside its original as <name>_thumb.jpg.

    Derived from the path rather than stored in the database, so existing
    reports get thumbnails from a backfill without a schema change, and a
    missing thumbnail can always fall back to the original.
    """
    p = Path(photo_path)
    return p.parent / f"{p.stem}_thumb.jpg"


def generate_thumbnail(photo_path: str) -> str | None:
    """Writes a downscaled JPEG beside the original. Returns its path, or
    None if one couldn't be produced.

    Never raises: a thumbnail is an optimisation, and failing to make one
    must not stop a report being filed. Callers fall back to the original
    image, which is exactly the behaviour before thumbnails existed.
    """
    try:
        from PIL import Image, ImageOps

        dest = thumbnail_path_for(photo_path)
        with Image.open(photo_path) as im:
            # Phone photos carry EXIF rotation; without this the thumbnail
            # can come out sideways relative to the original.
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE), Image.LANCZOS)
            im.save(dest, "JPEG", quality=THUMBNAIL_QUALITY, optimize=True, progressive=True)
        return str(dest)
    except Exception as exc:
        logger.warning("Could not generate thumbnail for %s: %s", photo_path, exc)
        return None


def save_photo(incident_id: str, source_path: str, index: int) -> str:
    d = incident_dir(incident_id)
    ext = Path(source_path).suffix or ".jpg"
    dest = d / "photos" / f"photo_{index:02d}{ext}"
    shutil.copy(source_path, dest)
    generate_thumbnail(str(dest))
    return str(dest)


def report_pdf_path(incident_id: str, version: int = 1) -> str:
    d = incident_dir(incident_id)
    return str(d / f"report_v{version}.pdf")


def tmp_dir() -> Path:
    d = Path(settings.storage_dir) / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def to_thumbnail_url(abs_path: str) -> str:
    """Public URL of a photo's thumbnail, falling back to the full image
    when no thumbnail exists (an older report, or a file Pillow couldn't
    read). Callers always get something displayable."""
    thumb = thumbnail_path_for(abs_path)
    return to_public_url(str(thumb)) if thumb.exists() else to_public_url(abs_path)


def to_public_url(abs_path: str) -> str:
    """Converts an absolute on-disk path into a URL servable via the /files
    static mount in app/api/main.py -- what the dashboard links/images use.
    """
    rel = Path(abs_path).resolve().relative_to(Path(settings.storage_dir).resolve())
    return "/files/" + str(rel).replace("\\", "/")
