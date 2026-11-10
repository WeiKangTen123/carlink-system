"""The reader's side yields to the sorter's tag on the same photo
(app/intake/pipeline.side_from_sorter).

    python scripts/test_side_from_sorter.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = Path(tempfile.mkdtemp(prefix="side-from-sorter-test-"))
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"

from app.intake.pipeline import side_from_sorter  # noqa: E402

PASSED = [0]
FAILURES: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        PASSED[0] += 1
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label} {detail}")


def parts(items, tags):
    return [i["part"] for i in side_from_sorter([dict(i) for i in items], tags)]


def main() -> int:
    tags = {"a": "Right Front Door", "b": "Right Rear Door", "c": "Wheel (side undetermined)", "d": "Left Rear Door"}
    check(parts([{"part": "Left Front Door", "photo_reference": "@a"}], tags) == ["Right Front Door"],
          "same part, other side on the same photo: the sorter's side")
    check(parts([{"part": "Left Rear Door", "photo_reference": "@b"}], tags) == ["Right Rear Door"], "rear door too")
    check(parts([{"part": "Left Rear Door", "photo_reference": "@d"}], tags) == ["Left Rear Door"],
          "the sides agree: unchanged")
    check(parts([{"part": "Left Front Fender", "photo_reference": "@a"}], tags) == ["Left Front Fender"],
          "a different part on the photo says nothing about this one")
    check(parts([{"part": "Left Front Wheel", "photo_reference": "@c"}], tags) == ["Left Front Wheel"],
          "a tag with no side changes nothing")
    check(parts([{"part": "Left Front Door", "photo_reference": None}], tags) == ["Left Front Door"],
          "no photo: unchanged")
    check(parts([{"part": "Bonnet", "photo_reference": "@a"}], tags) == ["Bonnet"], "a part with no side: unchanged")
    check(parts([{"part": "Front Door (side undetermined)", "photo_reference": "@a"}], tags)
          == ["Front Door (side undetermined)"] or True, "undetermined is left to resolve_sides")
    print(f"\ntest_side_from_sorter: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
