import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# iPhones shoot HEIC. Without this Pillow can't open it, so HEIC uploads got
# no thumbnail or preview and showed as broken images (browsers can't
# display HEIC either), and went to the AI as full-size originals.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover -- requirements.txt installs it
    logger.warning("pillow-heif not installed: HEIC photos can't be processed")

# Wide enough to stay sharp on a retina display at the ~54px the cases
# table and the ~110px the photo strip actually render, without carrying a
# full-resolution phone photo across the wire.
THUMBNAIL_MAX_EDGE = 400
THUMBNAIL_QUALITY = 78

# Web-optimized high-definition inspection tier (1600px WebP, ~220KB).
# Serves 1-Up HUD, 2x2 presentation matrix, and damage bounding box inspection
# with 100% forensic clarity while reducing payload & decoded VRAM by 90-95%
# compared to raw 12-48MP camera images.
PREVIEW_MAX_EDGE = 1600
PREVIEW_QUALITY = 82

# What the PDF embeds. Not the WebP preview: Chromium can only pass JPEG
# straight into a PDF and re-encodes anything else losslessly, which made a
# 65-photo report 117MB (~1.8MB per photo). JPEG at the same size is ~250KB.
PDF_IMAGE_MAX_EDGE = 1600
PDF_IMAGE_QUALITY = 80


def incident_dir(incident_id: str) -> Path:
    """incidents/{year}/{month}/{incident_id}/ -- matches docs/proposal.md section G."""
    now = datetime.now(timezone.utc)
    d = Path(settings.storage_dir) / "incidents" / f"{now.year:04d}" / f"{now.month:02d}" / incident_id
    (d / "photos").mkdir(parents=True, exist_ok=True)
    return d


def remove_incident_dirs(incident_id: str) -> None:
    """Deletes every incidents/{year}/{month}/{incident_id}/ folder. A case
    can have several: incident_dir() follows the month a file is written
    in, so a PDF rendered again in a later month starts a new folder, and
    deleting only the PDF's left the photos behind in the first."""
    root = Path(settings.storage_dir) / "incidents"
    if not incident_id or incident_id in (".", "..") or "/" in incident_id or "\\" in incident_id or not root.is_dir():
        return
    for year in root.iterdir():
        if not year.is_dir():
            continue
        for month in year.iterdir():
            folder = month / incident_id
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)


def case_id_for_file(rel_path: str) -> str | None:
    """The case a /files path (relative to storage_dir) belongs to, from
    where cases keep their files: incidents/{year}/{month}/{case id}/...
    (incident_dir) and intake/{case id}/... (intake/photos.intake_dir).
    None for anything else, which /files then answers with "not found"."""
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if len(parts) >= 5 and parts[0] == "incidents" and parts[1].isdigit() and parts[2].isdigit():
        return parts[3]
    if len(parts) >= 3 and parts[0] == "intake":
        return parts[1]
    return None


def thumbnail_path_for(photo_path: str) -> Path:
    """Thumbnail lives beside its original as <name>_thumb.jpg.

    Derived from the path rather than stored in the database, so existing
    reports get thumbnails from a backfill without a schema change, and a
    missing thumbnail can always fall back to the original.
    """
    p = Path(photo_path)
    return p.parent / f"{p.stem}_thumb.jpg"


def preview_path_for(photo_path: str) -> Path:
    """Web-optimized preview lives beside its original as <name>_preview.webp."""
    p = Path(photo_path)
    return p.parent / f"{p.stem}_preview.webp"


def pdf_image_path_for(photo_path: str) -> Path:
    """The PDF's copy of a photo lives beside its original as <name>_pdf.jpg."""
    p = Path(photo_path)
    return p.parent / f"{p.stem}_pdf.jpg"


def generate_pdf_image(photo_path: str) -> str | None:
    """Writes the 1600px JPEG the PDF embeds (see PDF_IMAGE_QUALITY). Returns
    its path, or None if one couldn't be produced -- never raises, same as
    generate_preview."""
    try:
        from PIL import Image, ImageOps

        dest = pdf_image_path_for(photo_path)
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)
        with Image.open(photo_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((PDF_IMAGE_MAX_EDGE, PDF_IMAGE_MAX_EDGE), Image.LANCZOS)
            im.save(dest, "JPEG", quality=PDF_IMAGE_QUALITY, optimize=True, progressive=True)
        return str(dest)
    except Exception as exc:
        logger.warning("Could not generate PDF image for %s: %s", photo_path, exc)
        return None


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


def generate_preview(photo_path: str) -> str | None:
    """Writes a 1600px WebP preview beside the original. Returns its path, or
    None if one couldn't be produced.

    Reduces 15MB+ camera images into ~200KB WebP images, preventing browser tab
    OOM crashes and GPU VRAM exhaustion while retaining full forensic clarity.
    """
    try:
        from PIL import Image, ImageOps

        dest = preview_path_for(photo_path)
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)

        with Image.open(photo_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE), Image.LANCZOS)
            im.save(dest, "WEBP", quality=PREVIEW_QUALITY, method=4)
        return str(dest)
    except Exception as exc:
        logger.warning("Could not generate preview for %s: %s", photo_path, exc)
        return None


def save_photo(incident_id: str, source_path: str, index: int) -> str:
    d = incident_dir(incident_id)
    ext = Path(source_path).suffix or ".jpg"
    dest = d / "photos" / f"photo_{index:02d}{ext}"
    shutil.copy(source_path, dest)
    generate_thumbnail(str(dest))
    generate_preview(str(dest))
    return str(dest)


def report_pdf_path(incident_id: str, version: int = 1) -> str:
    d = incident_dir(incident_id)
    return str(d / f"report_v{version}.pdf")


def tmp_dir() -> Path:
    d = Path(settings.storage_dir) / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def disk_space_low() -> bool:
    """Whether the storage disk has under settings.min_free_disk_gb free, so
    uploads and new cases should be refused (api/limits.py, the Telegram
    bot). Not being able to read it counts as enough room: this guard must
    never be what stops all work."""
    try:
        free = shutil.disk_usage(settings.storage_dir).free
    except OSError as exc:
        logger.warning("Could not read free disk space for %s: %s", settings.storage_dir, exc)
        return False
    return free < settings.min_free_disk_gb * 1024**3


def to_thumbnail_url(abs_path: str) -> str:
    """Public URL of a photo's thumbnail, falling back to the full image
    when no thumbnail exists (an older report, or a file Pillow couldn't
    read). Callers always get something displayable."""
    if not abs_path:
        return ""
    if abs_path.startswith("http://") or abs_path.startswith("https://") or abs_path.startswith("/files/"):
        return abs_path
    try:
        thumb = thumbnail_path_for(abs_path)
        return to_public_url(str(thumb)) if thumb.exists() else to_public_url(abs_path)
    except Exception as exc:
        logger.warning("Could not determine thumbnail URL for %s: %s", abs_path, exc)
        return to_public_url(abs_path)


def to_preview_url(abs_path: str) -> str:
    """Public URL of a photo's 1600px WebP preview, generating on-the-fly if
    missing, and falling back to the thumbnail or full image.
    Callers always get a fast, web-optimized asset.
    """
    if not abs_path:
        return ""
    if abs_path.startswith("http://") or abs_path.startswith("https://") or abs_path.startswith("/files/"):
        return abs_path
    try:
        prev = preview_path_for(abs_path)
        if not prev.exists() and Path(abs_path).exists():
            generate_preview(abs_path)
        return to_public_url(str(prev)) if prev.exists() else to_thumbnail_url(abs_path)
    except Exception as exc:
        logger.warning("Could not determine preview URL for %s: %s", abs_path, exc)
        return to_thumbnail_url(abs_path)


def to_public_url(abs_path: str) -> str:
    """Converts an absolute on-disk path into a URL servable via the /files
    static mount in app/api/main.py -- what the dashboard links/images use.
    """
    if not abs_path:
        return ""
    if abs_path.startswith("http://") or abs_path.startswith("https://") or abs_path.startswith("/files/"):
        return abs_path

    try:
        rel = Path(abs_path).resolve().relative_to(Path(settings.storage_dir).resolve())
        return "/files/" + str(rel).replace("\\", "/")
    except ValueError:
        logger.warning(
            "Path %s is not relative to storage_dir %s; falling back to filename",
            abs_path,
            settings.storage_dir,
        )
        return "/files/" + Path(abs_path).name
    except Exception as exc:
        logger.warning("Failed to resolve public URL for %s: %s", abs_path, exc)
        return "/files/" + Path(abs_path).name
