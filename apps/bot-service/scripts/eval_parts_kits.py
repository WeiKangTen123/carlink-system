"""How well do kits learned from past reports cover a new case's Annex A?

Leave-one-out over a folder of past report spreadsheets: each case is held
out in turn, kits are learned from the rest, and suggestions for the held-
out case's damaged parts (taken from its own Annex A) are compared with what
its surveyor actually wrote. Offline, no Gemini.

    python scripts/eval_parts_kits.py <folder with CL-xxxxx .xls/.xlsx> [--ai]

--ai also asks Gemini (as POST /reports/{id}/annex-suggestions/ai does) for
the parts no other case covers -- what the dashboard offers next -- and
needs a Gemini key.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="kits-eval-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'eval.db').as_posix()}")
os.environ.setdefault("STORAGE_DIR", _tmp)

from app.parts.history import FITTING_CONDITIONS, family_of, import_case, normalise_item, parse_report, side_of, suggest  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402


def _words(key: str) -> frozenset:
    return frozenset(w[:-1] if len(w) > 4 and w.endswith("s") else w for w in key.split())


def _matched(actual: set[str], suggested: set[str]) -> set[str]:
    """Annex A lines with a suggestion of the same meaning: same words, or
    one line's words (2+) all inside the other's. Each suggestion used once."""
    pairs = []
    for a in actual:
        for s in suggested:
            wa, ws = _words(a), _words(s)
            inter = len(wa & ws)
            small = min(len(wa), len(ws))
            score = max(inter / len(wa | ws), (inter / small) * 0.9 if inter >= 2 or small == 1 else 0) if wa and ws else 0
            if score >= 0.6:
                pairs.append((score, a, s))
    hit, used = set(), set()
    for _, a, s in sorted(pairs, reverse=True):
        if a not in hit and s not in used:
            hit.add(a)
            used.add(s)
    return hit


def damaged_parts(case) -> list[str]:
    """The case's damaged parts the way its damage list would name them:
    families of lines that weren't only refitted, with the side."""
    out = []
    for line in case.lines:
        fam = family_of(line.item)
        if fam and line.condition.strip().lower() not in FITTING_CONDITIONS:
            side = side_of(line.item)
            name = f"{side} {fam}" if side and fam not in ("Boot Lid", "Rear Bumper") else fam
            if name not in out:
                out.append(name)
    return out


def ai_lines(make: str, model: str, parts: list[str]) -> list[dict]:
    import json

    from app.ai.keys import AiActor
    from app.ai.structured import call_structured
    from app.api.parts import _AI_SCHEMA, _AI_SYSTEM

    out = call_structured(
        purpose="annex suggestions eval", system_instruction=_AI_SYSTEM,
        input_parts=[{"type": "text", "text": f"Vehicle: {make} {model}, Singapore.\nDamaged parts: {json.dumps(parts)}"}],
        schema=_AI_SCHEMA, parse=json.loads,
        models=["gemini-3-flash-preview", "gemini-3.6-flash", "gemini-3.5-flash-lite"], timeout=60.0,
        actor=AiActor.cli(),  # the company keys
    )
    return [{"name": ln["name"], "qty": ln.get("qty", 1), "usual_unit_price": None, "cases": 0} for p in out.get("parts") or [] for ln in p.get("lines") or []]


def main() -> int:
    use_ai = "--ai" in sys.argv
    files = sorted(p for p in Path(sys.argv[1]).rglob("*") if p.suffix.lower() in (".xls", ".xlsx") and not p.name.startswith("~$"))
    init_db()
    db = SessionLocal()
    cases = []
    for f in files:
        case = parse_report(f)
        import_case(db, case, f.name)
        cases.append(case)
    db.commit()
    print(f"imported {len(cases)} reports, {sum(len(c.lines) for c in cases)} lines")

    total_hit = total_lines = total_sugg = 0
    for case in cases:
        parts = damaged_parts(case)
        result = suggest(db, make=case.make, model=case.model, damaged_parts=parts, existing_items=[], exclude_ref=case.ref)
        suggested = [ln for g in result["groups"] for ln in g["lines"]]
        if use_ai and result["without_history"]:
            suggested += ai_lines(case.make, case.model, result["without_history"])
        sugg_keys = {normalise_item(s["name"]) for s in suggested}
        actual = {normalise_item(l.item) for l in case.lines}
        hit = _matched(actual, sugg_keys)
        total_hit += len(hit); total_lines += len(actual); total_sugg += len(sugg_keys)
        unwanted = sugg_keys - actual
        print(f"\n== {case.ref} {case.make} {case.model} ({result['scope']}, {result['cases_used']} cases)")
        print(f"   Annex A lines covered {len(hit)}/{len(actual)} = {len(hit) / max(1, len(actual)):.0%}; "
              f"suggested {len(sugg_keys)}, not in this Annex A {len(unwanted)}")
        priced = [s for s in suggested if s["usual_unit_price"] is not None]
        print(f"   with a usual price: {len(priced)}/{len(suggested)}; parts without history: {result['without_history']}")
        print(f"   e.g. {[(s['name'], s['qty'], s['usual_unit_price'], s['cases']) for s in suggested[:5]]}")
    if total_lines:
        print(f"\nTOTAL covered {total_hit}/{total_lines} = {total_hit / total_lines:.0%}; "
              f"suggested {total_sugg}, of which {total_sugg - total_hit} not in the held-out Annex A")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
