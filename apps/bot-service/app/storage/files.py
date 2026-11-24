import logging
import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple, Optional

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

# The stored original's JPEG quality with settings.photo_store_compress "on"
# (compress_original). Owner, 23 Nov 2026: camera originals at ~q95+ came
# out ~77% smaller at 85, same pixel size, with no visible difference.
PHOTO_STORE_QUALITY = 85
# Above this many pixels (read from the header, before anything is
# decoded) the original is stored as sent: a 108-200 MP phone photo, or a
# small file claiming huge dimensions, would take over a gigabyte to
# re-encode (review M1, 24 Nov 2026). Today's phones shoot 12-50 MP.
PHOTO_COMPRESS_MAX_PIXELS = 60_000_000
# At most this many originals decoded and re-encoded at once in a process
# (the api's thread pool, the bot's threads), so a burst of uploads can't
# multiply the memory peak. A photo that waits longer than
# PHOTO_COMPRESS_WAIT_SECONDS for a turn is stored as sent -- never a failed
# upload, never a request held forever.
PHOTO_COMPRESS_CONCURRENCY = 2
PHOTO_COMPRESS_WAIT_SECONDS = 30.0
_COMPRESS_SLOTS = threading.BoundedSemaphore(PHOTO_COMPRESS_CONCURRENCY)
_COMPRESS_SUFFIXES = (".jpg", ".jpeg")
# MPO: a phone JPEG carrying more than one picture (depth map, a second
# lens) -- Pillow names it apart. Only its main picture is kept.
_COMPRESS_FORMATS = ("JPEG", "MPO")

# When a photo was taken, from its EXIF (photo_taken_at): each date tag with
# the offset tag that belongs to it (EXIF 2.31) and the IFD it is in -- None
# the main one, else the Exif sub-IFD. The first that reads as a date wins.
_EXIF_IFD = 0x8769
_DATE_ORIGINAL, _OFFSET_ORIGINAL, _DATE_DIGITIZED = 0x9003, 0x9011, 0x9004
_TAKEN_TAGS = (
    (0x9003, 0x9011, _EXIF_IFD),  # DateTimeOriginal, OffsetTimeOriginal
    (0x9004, 0x9012, _EXIF_IFD),  # DateTimeDigitized, OffsetTimeDigitized
    (0x0132, 0x9010, None),       # DateTime, OffsetTime
)
_EXIF_DATE = re.compile(r"(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_EXIF_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})")
# A date before this is a camera whose clock was never set.
EARLIEST_TAKEN = datetime(2000, 1, 1)


class UnsafePath(ValueError):
    """A path built from a request value that would leave its folder."""


def confined_path(base, *parts: str) -> Path:
    """base/parts..., refused (UnsafePath) unless it ends up strictly inside
    base once "." and ".." are worked out. For folders named after a value
    from a request (a case id, a chat id): case ids are made by the server
    (models.gen_id) and every route checks one against the database before
    it gets here, so this only makes that rule explicit where the path is
    built. For a plain id the result reads exactly as base / id."""
    root = os.path.normpath(os.fspath(base))
    full = os.path.normpath(os.path.join(root, *parts))
    if not full.startswith(root.rstrip(os.sep) + os.sep):
        raise UnsafePath(f"{parts!r} leaves {root}")
    return Path(full)


def incident_dir(incident_id: str) -> Path:
    """incidents/{year}/{month}/{incident_id}/ -- matches docs/proposal.md section G."""
    now = datetime.now(timezone.utc)
    month = Path(settings.storage_dir) / "incidents" / f"{now.year:04d}" / f"{now.month:02d}"
    d = confined_path(month, incident_id)
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


class TakenAt(NamedTuple):
    """When a photo was taken, by its EXIF (photo_taken_at).

    local: the camera's own clock as it wrote it -- naive, in whatever zone
    the phone was set to (a phone in Malaysia: UTC+8), NOT UTC. The date a
    surveyor would call the day of the photo.
    utc: the same moment in UTC (naive, like every DateTime column here),
    only when the camera wrote its offset (OffsetTimeOriginal and kin);
    None otherwise -- no zone is guessed."""
    local: datetime
    utc: Optional[datetime]


def _exif_text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    return str(value or "").strip().strip("\x00").strip()


def _exif_datetime(value) -> datetime | None:
    """"2026:11:23 14:05:09" (EXIF's form; "-" in the date tolerated), or
    None: blank, "0000:00:00 00:00:00" (unset) or not a date."""
    match = _EXIF_DATE.match(_exif_text(value))
    if not match:
        return None
    try:
        return datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _exif_offset(value) -> timedelta | None:
    """"+08:00" as a timedelta, or None: absent or not an offset on Earth."""
    match = _EXIF_OFFSET.fullmatch(_exif_text(value))
    if not match:
        return None
    sign, hours, minutes = match.group(1), int(match.group(2)), int(match.group(3))
    if hours > 14 or minutes > 59:
        return None
    offset = timedelta(hours=hours, minutes=minutes)
    return -offset if sign == "-" else offset


def _sensible(when: datetime) -> bool:
    """Not before EARLIEST_TAKEN (a clock never set), at most a day ahead."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return EARLIEST_TAKEN <= when <= now + timedelta(days=1)


def _taken_from(exif) -> tuple[TakenAt, timedelta | None] | None:
    """photo_taken_at's reading of an Image.Exif, with the offset it used."""
    sub = exif.get_ifd(_EXIF_IFD)
    for date_tag, offset_tag, ifd in _TAKEN_TAGS:
        local = _exif_datetime((sub if ifd else exif).get(date_tag))
        if local is None:
            continue
        # The first date found decides: one that makes no sense means the
        # camera's clock is wrong, and the next tag (DateTime is often when
        # an editor last saved it) would not be better.
        if not _sensible(local):
            return None
        offset = _exif_offset(sub.get(offset_tag))
        return TakenAt(local=local, utc=local - offset if offset is not None else None), offset
    return None


def _exif_stamp(when: datetime) -> str:
    return when.strftime("%Y:%m:%d %H:%M:%S")


def _offset_stamp(offset: timedelta) -> str:
    minutes = int(offset.total_seconds()) // 60
    sign = "-" if minutes < 0 else "+"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _date_only_exif(exif) -> bytes | None:
    """The EXIF a compressed original keeps (owner decision D7: the date
    taken, no location): only DateTimeOriginal -- the date photo_taken_at
    reads, wherever it found it -- its OffsetTimeOriginal when the camera
    wrote one, and DateTimeDigitized when it makes sense. No GPS, no
    make/model/serial, no thumbnail, no orientation (the pixels are turned
    already), nothing else. None: no sensible date, so no EXIF at all.
    photo_taken_at reads the same date and offset back from it."""
    found = _taken_from(exif)
    if found is None:
        return None
    taken, offset = found
    from PIL import Image

    tags = {_DATE_ORIGINAL: _exif_stamp(taken.local)}
    if offset is not None:
        tags[_OFFSET_ORIGINAL] = _offset_stamp(offset)
    digitized = _exif_datetime(exif.get_ifd(_EXIF_IFD).get(_DATE_DIGITIZED))
    if digitized is not None and _sensible(digitized):
        tags[_DATE_DIGITIZED] = _exif_stamp(digitized)
    kept = Image.Exif()
    kept[_EXIF_IFD] = tags
    return kept.tobytes()


def photo_taken_at(photo_path) -> TakenAt | None:
    """When the photo was taken (TakenAt), from its EXIF: DateTimeOriginal,
    else DateTimeDigitized, else DateTime -- the first that reads as a
    date, with its own offset tag when the camera wrote one. Only the date
    is read; nothing else of the EXIF (GPS included) goes anywhere.

    Read from the original as uploaded; a compressed original keeps the same
    date and offset (compress_original, _date_only_exif), so reading it
    again gives the same answer. None: no EXIF date (a screenshot, a photo
    sent through an app that strips it, a PNG), a date before
    EARLIEST_TAKEN (a clock never set) or more than a day ahead of now, or
    a file Pillow can't read. HEIC too, through pillow-heif, when it
    carries EXIF. Never raises."""
    try:
        from PIL import Image

        with Image.open(photo_path) as im:
            found = _taken_from(im.getexif())
        return found[0] if found else None
    except Exception as exc:
        logger.warning("Could not read when %s was taken: %s", Path(str(photo_path)).name, exc)
        return None


def compress_original(photo_path: str) -> int | None:
    """With settings.photo_store_compress "on", re-saves a JPEG original in
    place at PHOTO_STORE_QUALITY, the same pixel size: its EXIF rotation
    baked into the pixels (ImageOps.exif_transpose, as the thumbnail and
    preview do), its colour profile (ICC) kept, and of its EXIF only the
    date it was taken (_date_only_exif, owner decision D7) -- no GPS, no
    camera details, no XMP or comment. Returns the new size in bytes.

    Call it once per original, AFTER its thumbnail and preview have been
    made from the file as sent, and only when both were made: the preview
    is what the AI reads (intake/photos.ai_input_path), and it must be the
    same whatever this switch says (owner's accuracy gate, 24 Nov 2026).
    What still reads the compressed original: close-ups for the reading
    (intake/crops.py, settings.intake_read_crops -- off in production; an
    accuracy run for it must have this switch on), the PDF's images, and a
    preview made later because one went missing.

    JPEG only: a HEIC, PNG or WebP original is stored as sent, its EXIF --
    a HEIC's GPS position included -- kept.

    None, and the file left byte for byte as it was: the setting is off; not
    a .jpg/.jpeg name; not a JPEG inside (Pillow's format); over
    PHOTO_COMPRESS_MAX_PIXELS; no free slot within
    PHOTO_COMPRESS_WAIT_SECONDS; a file that can't be opened or decoded;
    or the re-save is no smaller and there was no metadata to drop. Never
    raises (a stored photo matters more than a smaller one): written beside
    the original and moved into place (os.replace), so a failure leaves the
    original whole and no temp file."""
    if getattr(settings, "photo_store_compress", "off") != "on":
        return None
    path = Path(photo_path)
    if path.suffix.lower() not in _COMPRESS_SUFFIXES:
        return None
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        from PIL import Image, ImageOps

        before = path.stat().st_size
        with Image.open(path) as im:
            # Both from the header: nothing is decoded yet.
            if im.format not in _COMPRESS_FORMATS:
                return None
            if im.width * im.height > PHOTO_COMPRESS_MAX_PIXELS:
                logger.info("Stored %s as sent: %dx%d is over the %d-pixel limit for compressing",
                            path.name, im.width, im.height, PHOTO_COMPRESS_MAX_PIXELS)
                return None
            if not _COMPRESS_SLOTS.acquire(timeout=PHOTO_COMPRESS_WAIT_SECONDS):
                logger.warning("Stored %s as sent: no free compression slot within %ss",
                               path.name, PHOTO_COMPRESS_WAIT_SECONDS)
                return None
            try:
                raw_exif = im.info.get("exif") or b""
                kept_exif = _date_only_exif(im.getexif()) if raw_exif else None
                # Anything of the original's EXIF beyond the date we keep.
                meta_to_drop = bool(raw_exif) and raw_exif != kept_exif
                icc_profile = im.info.get("icc_profile")
                # In place: no second full-size copy when there is nothing
                # to turn (a turn still needs the turned image once).
                ImageOps.exif_transpose(im, in_place=True)
                out = im
                if out.mode not in ("RGB", "L"):
                    out = out.convert("RGB")
                    # The profile described the old colours (CMYK, say).
                    icc_profile = None
                # Nothing of the original's metadata rides along but what
                # is passed below: Pillow writes a JPEG comment from info,
                # and future versions may write more.
                out.info = {}
                options = {"quality": PHOTO_STORE_QUALITY, "optimize": True, "progressive": True}
                if icc_profile:
                    options["icc_profile"] = icc_profile
                if kept_exif:
                    options["exif"] = kept_exif
                out.save(tmp, "JPEG", **options)
            finally:
                _COMPRESS_SLOTS.release()
        after = tmp.stat().st_size
        if after >= before and not meta_to_drop:
            return None
        os.replace(tmp, path)
        return after
    except Exception as exc:
        logger.warning("Could not compress %s: %s", photo_path, exc)
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", tmp, exc)


def save_photo(incident_id: str, source_path: str, index: int) -> str:
    """Copies a photo into the case's photos/ folder as photo_NN, with its
    thumbnail and preview. Blocking work (full-size decodes and, with
    settings.photo_store_compress, a full-size re-encode): never call it
    on an event loop -- asyncio.to_thread, as channels/telegram does."""
    d = incident_dir(incident_id)
    ext = Path(source_path).suffix or ".jpg"
    dest = d / "photos" / f"photo_{index:02d}{ext}"
    shutil.copy(source_path, dest)
    # The thumbnail and preview from the file as sent, then the original
    # compressed (settings.photo_store_compress): the preview -- what the AI
    # reads -- is the same either way. Not compressed when either failed:
    # one made later would come from the compressed file.
    thumb = generate_thumbnail(str(dest))
    preview = generate_preview(str(dest))
    if thumb is not None and preview is not None:
        compress_original(str(dest))
    return str(dest)


def report_pdf_path(incident_id: str, version: int = 1) -> str:
    d = incident_dir(incident_id)
    return str(d / f"report_v{version}.pdf")


def tmp_dir() -> Path:
    d = Path(settings.storage_dir) / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def disk_space_low(extra_gb: float = 0.0) -> bool:
    """Whether the storage disk has under settings.min_free_disk_gb free, so
    uploads and new cases should be refused (api/limits.py, the Telegram
    bot). Not being able to read it counts as enough room: this guard must
    never be what stops all work.

    extra_gb raises the bar: public Telegram reports stop extra_gb earlier
    (intake/public.DISK_HEADROOM_GB), so strangers can never be what fills
    the disk staff need."""
    try:
        free = shutil.disk_usage(settings.storage_dir).free
    except OSError as exc:
        logger.warning("Could not read free disk space for %s: %s", settings.storage_dir, exc)
        return False
    return free < (settings.min_free_disk_gb + extra_gb) * 1024**3


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
