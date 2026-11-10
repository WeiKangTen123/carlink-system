"""The fresh-eyes review of feat/lite-detection (2026-11-10), each finding
as a check.

    python scripts/test_lite_review_fixes.py

- A rim line with no front/rear ("Sport rim") is priced once, on every
  sync, not again on the next one (prefill._held_family).
- Naming an unnamed "Other / Not Listed" keeps its priced row (not stale)
  and adds no second row.
- split_make_model: punctuation words are no model; "MAKE-MODEL" splits.
- A chain whose strong model is spent for the day and whose light models
  are busy is still asked a second round; a parse error is never "busy".
"""
import copy
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = Path(tempfile.mkdtemp(prefix="lite-review-test-"))
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSy" + "R" * 33
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"

from app.ai import allowance, structured  # noqa: E402
from app.ai.keys import AiActor, resolve_keys  # noqa: E402
from app.parts import history  # noqa: E402
from app.parts.prefill import sync_annex_a  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import PastAnnexLine  # noqa: E402

PASSED = [0]
FAILURES: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        PASSED[0] += 1
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label} {detail}")


def _add(db, ref, item, qty, est, adj, cond="Grazed"):
    db.add(PastAnnexLine(case_ref=ref, make="Honda", model="Civic", make_model_raw="Honda Civic", item=item,
                         item_key=history.normalise_item(item), family=history.family_of(item),
                         side=history.side_of(item), condition=cond, qty=qty, workshop_est=est,
                         adjusted_cost=adj, section="list"))


def rows(d):
    return [(r["part_name"], r["workshop_est"], r.get("stale"), r["source_part"]) for r in d["annex_a"]["items"]]


def rim_once() -> None:
    print("an end-less rim line is priced once, on every sync")
    db = SessionLocal()
    try:
        _add(db, "CL 1", "Sport rim", 2, 1600.0, 1280.0)
        _add(db, "CL 2", "Sport rim", 1, 800.0, 640.0)
        db.commit()
        data = {"vehicle_info": {"make": "Honda", "model": "Civic"},
                "damage_summary": [{"part": "Left Rear Wheel", "condition": "Grazed", "human_verified": True},
                                   {"part": "Right Rear Wheel", "condition": "Grazed", "human_verified": True}]}
        s1 = sync_annex_a(db, copy.deepcopy(data), price_book=True)
        s2 = sync_annex_a(db, copy.deepcopy(s1), price_book=True)
        s3 = sync_annex_a(db, copy.deepcopy(s2), price_book=True)
        rims = lambda d: [r for r in rows(d) if r[0] == "Sport rim"]
        check(len(rims(s1)) == 1, "first sync: one Sport rim row", repr(rows(s1)))
        check(rims(s2) == rims(s1) and rims(s3) == rims(s1), "the next syncs add no second row", repr(rows(s2)))
    finally:
        db.close()


def other_named() -> None:
    print("naming an unnamed Other keeps its priced row")
    db = SessionLocal()
    try:
        vehicle = {"make": "Honda", "model": "Civic"}
        row = {"part_name": "Rear air vent", "qty": 1.0, "workshop_est": 120.0, "discount_pct": 20.0,
               "adjusted_cost": 96.0, "is_net_item": False, "origin": "typed", "source_part": "Other / Not Listed",
               "condition": "Cracked", "action": "Replace"}
        before = {"vehicle_info": vehicle, "damage_summary": [{"part": "Other / Not Listed"}],
                  "annex_a": {"items": [row]}}
        s1 = sync_annex_a(db, copy.deepcopy(before), price_book=True)
        named = copy.deepcopy(s1)
        named["damage_summary"] = [{"part": "Other / Not Listed", "other_part_name": "Rear air vent"}]
        s2 = sync_annex_a(db, named, price_book=True)
        priced = [r for r in rows(s2) if r[1] == 120.0]
        check(len(priced) == 1 and priced[0][2] is False, "the priced row stays, not stale", repr(rows(s2)))
        check(len(rows(s2)) == 1, "and no second, unpriced row for the named part", repr(rows(s2)))
    finally:
        db.close()


def make_model() -> None:
    print("split_make_model")
    f = history.split_make_model
    check(f("Honda - Vezel 1.5X") == ("Honda", "Vezel"), "a lone dash is no model", repr(f("Honda - Vezel 1.5X")))
    check(f("HONDA-VEZEL")[0] == "Honda" and f("HONDA-VEZEL")[1].lower() == "vezel", "MAKE-MODEL splits",
          repr(f("HONDA-VEZEL")))
    check(f("Honda HR-V 1.5") == ("Honda", "HR-V") and f("Mazda CX-5") == ("Mazda", "CX-5"), "a model's hyphen stays")
    check(f("Mercedes-Benz C200") == ("Mercedes Benz", "C200"), "a two-word make's hyphen splits")
    check(f("Honda Vezel 1.5X CVT") == ("Honda", "Vezel"), "the live price book's wording is unchanged")


class Busy(Exception):
    status_code = 503

    def __init__(self):
        super().__init__("503 currently experiencing high demand")


def busy_after_spent() -> None:
    print("spent strong model + busy light models: a second round")
    calls, slept = [], []
    plan = {"lite-a": [Busy(), Busy(), '{"ok": 1}'], "lite-b": [Busy(), Busy()]}

    def create(**kw):
        calls.append(kw["model"])
        step = plan[kw["model"]].pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(output_text=step, usage=None, outputs=[], id="x")

    structured.get_client = lambda key: SimpleNamespace(interactions=SimpleNamespace(create=create))
    structured.acquire = lambda *a, **k: 0.0
    structured.time.sleep = lambda s: slept.append(s)
    key = resolve_keys(AiActor.cli())[0]
    allowance.mark_spent(key.fingerprint, "strong", None)
    out = structured.call_structured(
        purpose="draft", system_instruction="s", input_parts=[{"type": "text", "text": "x"}],
        schema={"type": "object"}, parse=lambda raw: raw, models=["strong", "lite-a", "lite-b"],
        actor=AiActor.cli(), timeout=5,
    )
    check(out == '{"ok": 1}' and "strong" not in calls, "answered on the second round; the spent model never asked",
          repr(calls))
    check(structured.CHAIN_BUSY_RETRY_SECONDS in slept, "with the chain's pause", repr(slept))
    check(not structured.is_overloaded(ValueError("bad JSON quoting 503 high demand")), "a parse error is never busy")


def main() -> int:
    init_db()
    for fn in (rim_once, other_named, make_model, busy_after_spent):
        fn()
    print(f"\ntest_lite_review_fixes: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
