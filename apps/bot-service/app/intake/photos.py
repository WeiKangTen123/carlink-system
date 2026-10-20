"""Where intake photos live before and after the draft becomes a case."""
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.reports.models import IntakePhoto
from app.storage.files import (
    incident_dir,
    preview_path_for,
    thumbnail_path_for,
    to_preview_url,
    to_public_url,
    to_thumbnail_url,
)


def intake_dir(report_id: str) -> Path:
    """storage/intake/{report_id}/ -- one folder per draft, so deleting or
    finalizing a draft is a single rmtree. Deliberately not incident_dir(),
    which is keyed on the current month and would split a draft whose
    uploads straddle a month boundary across two folders."""
    d = Path(settings.storage_dir) / "intake" / report_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def remove_intake_dir(report_id: str) -> None:
    d = Path(settings.storage_dir) / "intake" / report_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def ordered_photos(db: Session, report_id: str) -> list[IntakePhoto]:
    """Case photo order: the browser's position (files sorted by name, which
    is camera order), upload time as the tie-break. P01, P02... and the final
    photo_00, photo_01... filenames both follow this order."""
    return (
        db.query(IntakePhoto)
        .filter(IntakePhoto.report_id == report_id)
        .order_by(IntakePhoto.position, IntakePhoto.created_at)
        .all()
    )


def photo_label(index: int) -> str:
    return f"P{index + 1:02d}"


def ai_input_path(photo_path: str) -> str:
    """The 1600px WebP preview made at upload when there is one -- typically
    ~200KB against a 2-8MB original, and still sharp enough to read a log
    card. Falls back to the original (e.g. HEIC, which Pillow can't open)."""
    preview = preview_path_for(photo_path)
    return str(preview) if preview.exists() else photo_path


def serialize_photo(photo: IntakePhoto, label: str) -> dict:
    return {
        "id": photo.id,
        "label": label,
        "position": photo.position,
        "name": photo.original_name,
        "size_bytes": photo.size_bytes,
        "url": to_public_url(photo.path),
        "thumb_url": to_thumbnail_url(photo.path),
        "preview_url": to_preview_url(photo.path),
        "kind": photo.kind,
        "area": photo.area,
        "quality": photo.quality,
        "shows_damage": photo.shows_damage,
        "note": photo.note,
        "kind_source": photo.kind_source,
        "excluded": photo.excluded,
    }


def move_into_case(report_id: str, photos: list[IntakePhoto]) -> list[str]:
    """Moves the draft's photos to the report's permanent photos/ folder as
    photo_00, photo_01... (the same naming save_photo uses), carrying their
    thumbnail/preview along instead of regenerating them. Returns the new
    paths, in order, for Report.photo_paths -- one per photo passed, so the
    caller must pass only photos whose file exists, or the P-numbers it
    assigned would point at the wrong photos."""
    dest_dir = incident_dir(report_id) / "photos"
    paths: list[str] = []
    for index, photo in enumerate(photos):
        src = Path(photo.path)
        dest = dest_dir / f"photo_{index:02d}{src.suffix or '.jpg'}"
        shutil.move(str(src), str(dest))
        for derived in (thumbnail_path_for, preview_path_for):
            old = derived(str(src))
            if old.exists():
                shutil.move(str(old), str(derived(str(dest))))
        paths.append(str(dest))
    return paths
