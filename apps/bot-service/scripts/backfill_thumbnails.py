"""One-off: generate thumbnails for photos saved before thumbnailing existed.

Non-destructive. Originals are never modified, moved, or deleted -- this
only writes a new <name>_thumb.jpg beside each one, and skips any that
already has one. Safe to re-run.

    docker exec carlink_api python3 scripts/backfill_thumbnails.py [--dry-run]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.storage.files import generate_thumbnail, thumbnail_path_for

DRY_RUN = "--dry-run" in sys.argv


def main() -> int:
    root = Path(settings.storage_dir) / "incidents"
    if not root.exists():
        print(f"No storage at {root}")
        return 1

    photos = [
        p
        for p in sorted(root.glob("*/*/*/photos/*"))
        # Don't thumbnail the thumbnails on a re-run.
        if p.is_file() and not p.stem.endswith("_thumb")
    ]

    made = skipped = failed = 0
    before_bytes = after_bytes = 0

    for src in photos:
        dest = thumbnail_path_for(str(src))
        if dest.exists():
            skipped += 1
            continue
        if DRY_RUN:
            print(f"  would create {dest.name}  (from {src.stat().st_size / 1048576:.2f}MB)")
            made += 1
            continue
        result = generate_thumbnail(str(src))
        if result and Path(result).exists():
            made += 1
            before_bytes += src.stat().st_size
            after_bytes += Path(result).stat().st_size
            print(f"  {src.stat().st_size / 1048576:>6.2f}MB -> {Path(result).stat().st_size / 1024:>6.0f}KB  {src.name}")
        else:
            failed += 1
            print(f"  FAILED {src}")

    print(f"\n{len(photos)} photos: {made} created, {skipped} already had one, {failed} failed")
    if made and not DRY_RUN and before_bytes:
        print(f"{before_bytes / 1048576:.1f}MB of originals -> {after_bytes / 1048576:.2f}MB of thumbnails "
              f"({100 - after_bytes / before_bytes * 100:.1f}% smaller)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
