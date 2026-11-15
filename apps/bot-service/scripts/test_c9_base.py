"""Contract 9's base (plans/2026-11-15-evidence/contract9.md, Step 1) --
offline, Gemini is a fake.

Covers: the evidence models (section 4.1) left out of the drafting schema,
which stays byte for byte as it was, and old drafts loading with them.

    python scripts/test_c9_base.py

Makes, parts and plates are made up; the photos are coloured squares.
"""
import copy
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="c9-base-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"

from app.ai.extraction import _response_schema  # noqa: E402
from app.reports.schema import (  # noqa: E402
    MAX_DECISIONS,
    DamageSummaryItem,
    SecondLook,
    SecurityIncidentDraft,
)

# Taken on feat/c9-base before contract 9's fields (d8dbabb, the four
# merges): json.dumps of the schema the drafting call (and the Telegram
# bot) sends, and of the whole draft model's.
GOLDEN_DRAFT_SCHEMA = "7ccb5ce425dc1fe68971672f41992753e1431b535edf941c45aa648e3d6d18d0"
GOLDEN_MODEL_SCHEMA = "b94a425945ec99d7a04605f0f48f57d920662de5ecb49491b7b38eb60dc8c1a7"
C9_ITEM_FIELDS = ("reason", "evidence", "check", "added_from")

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why != "" else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj).encode()).hexdigest()


# ---------------------------------------------------------------- 4.1 models


def schema_models() -> None:
    check(sha(_response_schema()) == GOLDEN_DRAFT_SCHEMA, "the drafting schema is byte for byte as before (golden)",
          sha(_response_schema()))
    check(sha(SecurityIncidentDraft.model_json_schema()) == GOLDEN_MODEL_SCHEMA,
          "the draft model's whole JSON schema too: nothing new in it")
    props = DamageSummaryItem.model_json_schema()["properties"]
    check(not set(C9_ITEM_FIELDS) & set(props), "reason / evidence / check / added_from: not in the item's schema")

    old = DamageSummaryItem.model_validate({"part": "Rear Bumper", "photo_reference": "P03", "bbox_2d": [1, 2, 3, 4]})
    check((old.reason, old.evidence, old.check, old.added_from) == (None, [], None, None),
          "an item saved before them loads, empty")
    item = {"part": "Boot Lid", "photo_reference": "@p1", "reason": "AI words",
            "evidence": [{"photo": "@p1", "box": [10, 20, 300, 400], "box_kind": "ai", "by": "zone:rear",
                          "reason": "dent at the edge", "confidence": "Medium", "img_w": 1600, "img_h": 1200},
                         {"photo": "@p2"}],
            "check": {"verdict": "yes", "p": 0.91, "by": "decision", "model": "gemini-decision-1"},
            "added_from": "suggestion"}
    draft = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [item]})
    got = draft.model_dump()["damage_summary"][0]
    check(got["evidence"][0]["box"] == [10, 20, 300, 400] and got["evidence"][1] == {
        "photo": "@p2", "box": None, "box_kind": None, "by": "overall", "reason": None, "confidence": None,
        "img_w": None, "img_h": None} and got["check"]["p"] == 0.91 and got["added_from"] == "suggestion"
          and got["reason"] == "AI words", "the wizard's autosave (through the draft model) keeps them", got)
    odd = DamageSummaryItem.model_validate({"part": "Boot Lid", "evidence": [{"photo": "@p1", "box": [900, 0, 100, 5]}]})
    check(odd.evidence[0].box == [900, 0, 100, 5], "lenient: an odd box loads as it is (cleaned where it is made)")

    look = SecondLook.model_validate({"status": "ok", "items": [{"part": "Rear Bumper", "source": "kit"}],
                                      "dismissed": ["Boot Lid Lock"]})
    dumped = look.model_dump()
    check(dumped["items"][0]["key"] == "" and dumped["items"][0]["evidence"] == [] and dumped["reads"] == []
          and dumped["decisions"] == [] and dumped["check"] is None and dumped["dismissed"] == ["Boot Lid Lock"],
          "an old second look (no key, reads, decisions, check) loads with them empty")
    many = SecondLook.model_validate({"decisions": [
        {"key": f"k{i}", "part": "Boot Lid", "action": "dismissed", "at": f"2026-11-15T00:00:{i % 60:02d}Z"}
        for i in range(MAX_DECISIONS + 5)]})
    check(len(many.decisions) == MAX_DECISIONS and many.decisions[0].key == "k5",
          f"decisions keep the newest {MAX_DECISIONS}, the oldest dropped, never refused")
    full = SecondLook.model_validate({"check": {"status": "ok", "by": "decision", "calls": 1}, "reads": ["overall"]})
    check(full.check.calls == 1 and full.reads == ["overall"], "check (CheckRun) and reads")


def main() -> int:
    for name, fn in (
        ("4.1: the evidence models, out of the drafting schema", schema_models),
    ):
        run_section(name, fn)
    print(f"\ntest_c9_base: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
