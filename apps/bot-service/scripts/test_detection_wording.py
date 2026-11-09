"""Detection speaks Annex A (contract4 A1, 2026-11-06): the firm's condition
words and remarks from the drafting call, the firm's part wording on every
Annex A row, "(side undetermined)" parts placed from the sorter's photo
areas, and the routes a person corrects them with
(app/reports/taxonomy.py, app/ai/extraction.py, app/intake/pipeline.py,
app/parts/history.py, app/parts/prefill.py, app/reports/synthesize_annexes.py,
app/conversation/flow.py, app/api/main.py).

    python scripts/test_detection_wording.py

Offline: no Gemini call. The past-case history is synthetic (made-up makes
and references, no real plates); the API runs through TestClient with
scripts/test_annexes.py's temporary database and render stand-ins.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
# Sets the temporary storage/database and replaces the renderers. No
# example_case folder: this file's history is its own.
sys.argv = sys.argv[:1]
import test_annexes as annexes  # noqa: E402,F401

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.extraction import _parse_draft, _response_schema  # noqa: E402
from app.api.main import app  # noqa: E402
from app.conversation.flow import _carry_over_damage, _damage_lines, edit_mentions_damage  # noqa: E402
from app.intake.pipeline import merge_redraft, resolve_sides, voting_areas  # noqa: E402
from app.parts.history import PastCase, PastLine, _without_side, firm_wording, import_case, side_of  # noqa: E402
from app.parts.prefill import row_name, sync_annex_a  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import Report  # noqa: E402
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft  # noqa: E402
from app.reports.synthesize_annexes import synthesize_annexes_for_report  # noqa: E402
from app.reports.taxonomy import (  # noqa: E402
    CANONICAL_DAMAGE_TYPES,
    CANONICAL_PARTS,
    DAMAGE_TYPE_TO_TOKEN,
    FIRM_CONDITION_COMMON,
    FIRM_CONDITION_TOKENS,
    FIRM_PART_NAMES,
    FIRM_PART_NAMES_HATCH,
    TOKEN_TO_DAMAGE_TYPE,
    annex_condition,
    condition_tokens_of,
    damage_type_from_tokens,
    firm_condition,
    join_condition,
    parse_condition,
    sided_part,
)
from auth_helpers import sign_in  # noqa: E402

UNDETERMINED = " (side undetermined)"


def ok(label: str) -> None:
    print(f"    ok   {label}")


def line(n, item, condition, qty, est, adjusted, section="list", discount=0.2) -> PastLine:
    return PastLine(line_no=n, section=section, item=item, condition=condition, remarks="", qty=qty, qty_raw=str(qty),
                    workshop_est=est, discount=discount, adjusted_cost=adjusted)


def import_history() -> None:
    """Two made-up past cases: a hatch (an SUV model name, so
    taxonomy.body_type_for calls it one) with a tail gate, tail lamp and
    bumper, and a sedan with a boot lid."""
    hatch = PastCase(ref="CL 90001", plate="TEST1A", make="Alphamotors", model="Vezel", make_model_raw="Alphamotors Vezel",
                     registered=None, surveyed="2025-01-01", lines=[
                         line(1, "Tail gate assy", "Buckled", 1, 1450, 1160),
                         line(2, "Tail gate stopper", "Necessary", 2, 50, 40),
                         line(3, "Tail lamp LH", "Grazed/cracked", 1, 400, 320),
                         line(4, "Tail lamp clips LH", "Necessary", 4, 20, 16),
                         line(5, "Rear bumper fascia", "Deformed/cut", 1, 1000, 800),
                         # The family's only line names a sub-part, as the
                         # real Civic's "Front bumper fog lamps cover" does.
                         line(6, "Front bumper fog lamps cover", "Slack", 1, 120, 96),
                     ])
    sedan = PastCase(ref="CL 90002", plate="TEST2B", make="Betamotors", model="Camry", make_model_raw="Betamotors Camry",
                     registered=None, surveyed="2025-02-01", lines=[
                         line(1, "Boot lid assy", "Buckled", 1, 1300, 1040),
                         line(2, "Boot lid hinges", "Bent", 2, 200, 160),
                     ])
    db = SessionLocal()
    try:
        import_case(db, hatch, "synthetic-1.xlsx")
        import_case(db, sedan, "synthetic-2.xlsx")
        db.commit()
    finally:
        db.close()


def rows(data: dict) -> list[dict]:
    return data["annex_a"]["items"]


# ------------------------------------------------------------- vocabulary


def check_vocabulary() -> None:
    print("the firm's condition words")
    assert len(FIRM_CONDITION_TOKENS) == 18 and len(set(FIRM_CONDITION_TOKENS)) == 18
    assert condition_tokens_of(["cut", "Grazed", "CUT", "Necessary", "a sentence"]) == ["Grazed", "Cut"]
    assert condition_tokens_of("rattle/Dented/bent") == ["Dented", "Bent", "Rattle"]
    assert join_condition(["Grazed", "Cut"]) == "Grazed/cut" and join_condition(["Distorted", "Cut", "Faulty"]) == "Distorted/cut/faulty"
    assert join_condition([]) is None
    ok("words are filtered to the 18, each once, in print order, joined 'Grazed/cut' (first capitalised)")

    assert firm_condition(["Rattle", "Grazed", "Cut", "Dented", "Bent"], None) == (["Grazed", "Dented", "Bent"], "Grazed/dented/bent"), \
        "review item 8: at most three words, the first three in the firm's order"
    assert firm_condition([], "Dent") == (["Dented"], "Dented")
    assert firm_condition([], "Deformation") == (["Deformed"], "Deformed")
    assert firm_condition(["Bent", "Dented"], "Crack") == (["Dented", "Bent"], "Dented/bent"), "the AI's words win over its type"
    for no_word in ("Shatter / Broken Glass", "Missing", "Water Damage", "Fire / Heat Damage", None):
        assert firm_condition([], no_word) == ([], None), no_word
    assert set(DAMAGE_TYPE_TO_TOKEN) <= set(CANONICAL_DAMAGE_TYPES) and set(DAMAGE_TYPE_TO_TOKEN.values()) <= set(FIRM_CONDITION_TOKENS)
    assert set(TOKEN_TO_DAMAGE_TYPE) <= set(FIRM_CONDITION_TOKENS) and set(TOKEN_TO_DAMAGE_TYPE.values()) <= set(CANONICAL_DAMAGE_TYPES)
    ok("no words: the one firm word for the damage type; Shatter, Missing, Water and Fire have none (kept as the type)")

    assert damage_type_from_tokens(["Grazed", "Cracked"]) == "Crack"
    assert damage_type_from_tokens(["Grazed"]) == "Scuff / Graze"
    assert damage_type_from_tokens(["Deformed", "Cut"]) == "Deformation"
    assert damage_type_from_tokens(["Jammed"]) is None and damage_type_from_tokens([]) is None
    ok("words imply a damage type: the first after a graze (the main damage), none for Jammed / Leaked / Rattle")

    assert parse_condition("necessary") == ([], "Necessary") and parse_condition(" Repair ") == ([], "Repair")
    assert parse_condition("bent/DENTED") == (["Bent", "Dented"], "Bent/dented"), "a person's order is kept"
    assert parse_condition("") == ([], None) and parse_condition(None) == ([], None)
    for bad, word in (("Bent/Broken", "Broken"), ("Necessary/cut", "Necessary"), ("Scratched", "Scratched"), ("Bent//cut", "")):
        try:
            parse_condition(bad)
        except ValueError as exc:
            assert str(exc) in (word, bad), (bad, str(exc))
        else:
            raise AssertionError(f"{bad!r} was accepted")
    for common in FIRM_CONDITION_COMMON:
        tokens, joined = parse_condition(common)
        assert joined == common and tokens == condition_tokens_of(tokens), common
    ok("a typed condition: Necessary / Serviceable / Repair whole, or firm words in any order; anything else names the bad word")

    assert annex_condition({"condition": "Bent/cut", "damage_type": "Dent", "condition_tokens": ["Dented"]}) == "Bent/cut"
    assert annex_condition({"condition_tokens": ["Cut", "Grazed"], "damage_type": "Dent"}) == "Grazed/cut"
    assert annex_condition({"damage_type": "Dent"}) == "Dented"
    assert annex_condition({"damage_type": "Water Damage"}) == "Water Damage" and annex_condition({}) == ""
    ok("Annex A's condition: the item's condition, else its words, else the firm word for its type, else the type")


def check_floor_names() -> None:
    print("part names in the firm's words (the floor)")
    for names in (FIRM_PART_NAMES, FIRM_PART_NAMES_HATCH):
        missing = [p for p in CANONICAL_PARTS if not names.get(p)]
        assert not missing, missing
        canonical = [p for p, n in names.items() if n in CANONICAL_PARTS]
        assert not canonical, canonical
        sided_wrong = [(p, n) for p, n in names.items() if p.endswith(UNDETERMINED) and side_of(n)]
        assert not sided_wrong, sided_wrong
        for p, n in names.items():
            if p.startswith("Left "):
                assert n.endswith(" LH"), (p, n)
            if p.startswith("Right "):
                assert n.endswith(" RH"), (p, n)
    assert FIRM_PART_NAMES["Boot Lid"] == "Boot lid assy" and FIRM_PART_NAMES_HATCH["Boot Lid"] == "Tail gate assy"
    assert FIRM_PART_NAMES["Left Tail Lamp"] == "Tail lamp LH" and FIRM_PART_NAMES["Tail Lamp (side undetermined)"] == "Tail lamp"
    assert FIRM_PART_NAMES_HATCH["Right Boot Lid Lamp"] == "Tail gate reflector lamp RH"
    ok("every canonical part has a wording for sedans and for hatches; none prints as a canonical name; LH / RH last, none when undetermined")


# ------------------------------------------------------------- schema / parse


def check_schema_and_parse() -> None:
    print("the drafting schema and parse")
    schema = _response_schema()
    item = schema["$defs"]["DamageSummaryItem"]["properties"]
    tokens = item["condition_tokens"]
    assert tokens["type"] == "array" and tokens["items"] == {"type": "string", "enum": FIRM_CONDITION_TOKENS}, tokens
    assert not {"minItems", "maxItems", "anyOf"} & set(tokens), tokens
    remarks = item["remarks"]
    assert {"type": "string", "enum": ["Replace", "Repair"]} in remarks["anyOf"] and {"type": "null"} in remarks["anyOf"], remarks
    assert "condition" not in item and "side_inferred" not in item, sorted(item)
    assert "condition" not in json.dumps(schema["$defs"]["DamageSummaryItem"].get("required") or [])
    print(f"         (draft schema: {len(json.dumps(schema))} characters)")
    ok("condition_tokens: an 18-word enum on the array's items, no min/maxItems or anyOf; remarks: Replace | Repair | null; condition and side_inferred never asked of the model")

    raw = json.dumps({
        "description": "Rear-ended.",
        "severity_level": "Moderate",
        "damage_summary": [
            {"part": "Rear Bumper", "damage_type": "Deformation", "condition_tokens": ["cut", "Deformed"], "remarks": "replace"},
            {"part": "Boot Lid", "condition_tokens": ["Dented"], "condition": "free text the model made up", "side_inferred": True},
            {"part": "Left Tail Lamp", "damage_type": "Crack", "severity": "pretty bad, let us think"},
            {"part": "Rear Windscreen", "damage_type": "Shatter / Broken Glass", "condition_tokens": ["Necessary"], "remarks": "Straighten"},
        ],
    })
    draft = _parse_draft(raw)
    got = [(i.condition_tokens, i.condition, i.damage_type, i.remarks, i.side_inferred) for i in draft.damage_summary]
    assert got == [
        (["Deformed", "Cut"], "Deformed/cut", "Deformation", "Replace", False),
        (["Dented"], "Dented", "Dent", None, False),
        (["Cracked"], "Cracked", "Crack", None, False),
        ([], None, "Shatter / Broken Glass", None, False),
    ], got
    assert draft.damage_summary[2].severity is None, "_sanitize_damage_summary runs"
    ok("parse: words filtered and ordered, condition built in code, the type filled from words, words from the type, odd remarks dropped, side_inferred never the model's")

    capped = _parse_draft(json.dumps({"description": "x", "damage_summary": [
        {"part": "Rear Bumper", "condition_tokens": ["Rattle", "Cut", "Grazed", "Deformed", "Torn"]}]}))
    assert (capped.damage_summary[0].condition_tokens, capped.damage_summary[0].condition) == (["Grazed", "Deformed", "Torn"], "Grazed/deformed/torn")
    ok("review item 8: a draft's words are capped at three (the first three in the firm's order)")

    backfilled = _parse_draft(json.dumps({"description": "x", "damaged_parts": ["Rear Bumper"], "severity_level": "Minor"}))
    assert [(i.part, i.condition, i.condition_tokens) for i in backfilled.damage_summary] == [("Rear Bumper", None, [])]
    ok("an item backfilled from damaged_parts has no condition (nothing was said about it)")


# ------------------------------------------------------------- Annex A


def case(parts: list[dict], make="Alphamotors", model="Vezel", body=None) -> dict:
    return {"vehicle_info": {"make": make, "model": model, "body_type": body}, "damage_summary": parts}


def check_annex_rows() -> None:
    print("Annex A rows from the damage list")
    db = SessionLocal()
    try:
        # Priced from the same make: the main row carries the item's
        # condition and remarks; fittings keep Necessary.
        out = rows(sync_annex_a(db, case([{"part": "Boot Lid", "damage_type": "Dent", "condition": "Buckled/cut", "condition_tokens": ["Buckled", "Cut"], "remarks": "Replace"}]), price_book=True))
        main = next(r for r in out if r["part_name"] == "Tail gate assy")
        assert (main["condition"], main["action"], main["discount_pct"], main["prefilled"]) == ("Buckled/cut", "Replace", 0.2, True), main
        stopper = next(r for r in out if r["part_name"] == "Tail gate stopper")
        assert (stopper["condition"], stopper["action"]) == ("Necessary", ""), stopper
        ok("the damaged part's own row prints its condition and remarks; a fitting keeps 'Necessary' and no remark")

        # Condition "Repair": priced, disallowed at 100%.
        out = rows(sync_annex_a(db, case([{"part": "Boot Lid", "damage_type": "Dent", "condition": "Repair", "remarks": "Replace"}]), price_book=True))
        main = next(r for r in out if r["part_name"] == "Tail gate assy")
        assert main["workshop_est"] > 0 and main["discount_pct"] == 1.0 and main["adjusted_cost"] == 0.0 and not main["is_net_item"], main
        plain = rows(sync_annex_a(db, case([{"part": "Bonnet", "condition": "Repair"}]), price_book=False))
        assert [(r["part_name"], r["condition"], r["discount_pct"], r["adjusted_cost"]) for r in plain] == [("Bonnet assy", "Repair", 1.0, 0.0)], plain
        scaffold = rows(synthesize_annexes_for_report(case([{"part": "Bonnet", "condition": "Repair"}])))
        assert [(r["part_name"], r["discount_pct"]) for r in scaffold] == [("Bonnet assy", 1.0)], scaffold
        ok("condition 'Repair': the row stays priced and is disallowed at 100% (prefill, the unpriced row and the scaffold alike)")

        # No canonical name ever prints, whatever the body and history.
        printed = []
        for body, make, model in (("Sedan", "Gammamotors", "City"), ("SUV", "Gammamotors", "Harrier"), (None, None, None),
                                  ("SUV", "Alphamotors", "Vezel")):
            for part in CANONICAL_PARTS:
                for price_book in (True, False):
                    data = case([{"part": part, "damage_type": "Dent"}], make, model, body)
                    printed += [(part, r["part_name"]) for r in rows(sync_annex_a(db, data, price_book=price_book))]
                printed += [(part, r["part_name"]) for r in rows(synthesize_annexes_for_report(case([{"part": part}], make, model, body)))]
        canonical = sorted({p for p in printed if p[1] in CANONICAL_PARTS})
        assert not canonical, canonical[:10]
        assert len({p for p, _ in printed}) == len(CANONICAL_PARTS)
        ok(f"no Annex A row is named by a canonical part name ({len(printed)} rows: every part x sedan/SUV/unknown x price book on/off, and the scaffold)")

        # Review item 2: the floor wins whenever it has a wording; history
        # wording only from the same make (model first), never any make's.
        assert firm_wording(db, "Boot Lid", "Alphamotors", "Vezel") == "Tail gate assy"
        assert firm_wording(db, "Left Tail Lamp", "Alphamotors", "Other") == "Tail lamp LH", "same make, another model"
        assert firm_wording(db, "Boot Lid", "Gammamotors", "Harrier") is None, "never another make's wording"
        assert firm_wording(db, "Bonnet", "Alphamotors", "Vezel") is None and firm_wording(db, "Left Rear Wheel", "Alphamotors", "Vezel") is None
        assert firm_wording(db, "Left Fog Lamp", "Alphamotors", "Vezel") == "Front bumper fog lamps cover LH", "the family's shortest line is a sub-part"
        assert row_name(db, "Left Fog Lamp", {"make": "Alphamotors", "model": "Vezel"}) == "Fog lamp LH", "the floor wins over it"
        fog = rows(sync_annex_a(db, case([{"part": "Left Fog Lamp", "damage_type": "Crack"}]), price_book=False))
        assert [r["part_name"] for r in fog] == ["Fog lamp LH"], fog
        unpriced = rows(sync_annex_a(db, case([{"part": "Boot Lid", "damage_type": "Dent"}], "Gammamotors", "Harrier", "SUV"), price_book=True))
        assert [(r["part_name"], r["workshop_est"], r["condition"]) for r in unpriced] == [("Tail gate assy", 0.0, "Dented")], unpriced
        sedan = rows(sync_annex_a(db, case([{"part": "Boot Lid", "damage_type": "Dent"}], "Gammamotors", "City", "Sedan"), price_book=True))
        assert [r["part_name"] for r in sedan] == ["Boot lid assy"], sedan
        # A pre-taxonomy case's free text is kept: its family's wording would be coarser.
        free = rows(sync_annex_a(db, case([{"part": "Tail gate lock striker", "damage_type": "Misalignment"}], "Gammamotors", "Harrier", "SUV"), price_book=False))
        assert [(r["part_name"], r["condition"]) for r in free] == [("Tail gate lock striker", "Shifted")], free
        ok("an unpriced row takes the floor wording (a fog lamp, not the history's 'fog lamps cover'); history wording only from the same make, never another's")
    finally:
        db.close()


def check_side_leak() -> None:
    print("a part whose side nobody determined never prints LH / RH")
    db = SessionLocal()
    try:
        undetermined = [{"part": "Tail Lamp" + UNDETERMINED, "damage_type": "Crack"}]
        priced = rows(sync_annex_a(db, case(undetermined), price_book=True))
        assert priced and any(r["prefilled"] and r["workshop_est"] > 0 for r in priced), priced
        assert all(side_of(r["part_name"]) is None for r in priced), [r["part_name"] for r in priced]
        assert {r["part_name"] for r in priced} == {"Tail lamp", "Tail lamp clips"}, priced
        unpriced = rows(sync_annex_a(db, case(undetermined), price_book=False))
        assert [r["part_name"] for r in unpriced] == ["Tail lamp"], unpriced
        assert firm_wording(db, "Tail Lamp" + UNDETERMINED, "Alphamotors", "Vezel") == "Tail lamp"
        for wording in ("Tail lamp LH", "Tail lamp (LH)", "Tail lamp L.H.", "Tail lamp R.H", "Tail lamp R/H", "Tail lamp rh x2"):
            assert _without_side(wording) in ("Tail lamp", "Tail lamp x2"), (wording, _without_side(wording))
        left = rows(sync_annex_a(db, case([{"part": "Left Tail Lamp", "damage_type": "Crack"}]), price_book=True))
        right = rows(sync_annex_a(db, case([{"part": "Right Tail Lamp", "damage_type": "Crack"}]), price_book=True))
        assert {r["part_name"] for r in left} == {"Tail lamp LH", "Tail lamp clips LH"}, left
        assert {r["part_name"] for r in right} == {"Tail lamp RH", "Tail lamp clips RH"}, right
        ok("the past case's 'Tail lamp LH' prints 'Tail lamp' for an undetermined lamp (priced or not), and LH / RH for a sided one")
    finally:
        db.close()


def check_inferred_side_rows() -> None:
    print("an inferred side prints no LH / RH until a person confirms it")
    db = SessionLocal()
    try:
        inferred = {"part": "Left Tail Lamp", "damage_type": "Crack", "ai_confidence": "Low", "side_inferred": True}
        # Priced from the same make's history: the family's lines, unsided.
        priced = rows(sync_annex_a(db, case([inferred]), price_book=True))
        assert {r["part_name"] for r in priced} == {"Tail lamp", "Tail lamp clips"}, priced
        assert all(side_of(r["part_name"]) is None and r["source_part"] == "Left Tail Lamp" and r["stale"] is False for r in priced), priced
        assert any(r["workshop_est"] > 0 for r in priced)
        # Unpriced (no price book; another make), and the scaffold.
        plain = sync_annex_a(db, case([inferred]), price_book=False)
        assert [(r["part_name"], r["source_part"], r["stale"]) for r in rows(plain)] == [("Tail lamp", "Left Tail Lamp", False)], rows(plain)
        other_make = rows(sync_annex_a(db, case([inferred], "Gammamotors", "Harrier", "SUV"), price_book=True))
        assert [r["part_name"] for r in other_make] == ["Tail lamp"], other_make
        scaffold = rows(synthesize_annexes_for_report(case([inferred])))
        assert [(r["part_name"], r["source_part"]) for r in scaffold] == [("Tail lamp", "Left Tail Lamp")], scaffold
        wheel = rows(sync_annex_a(db, case([{"part": "Left Rear Wheel", "side_inferred": True}], "Gammamotors", "Harrier", "SUV"), price_book=False))
        assert [r["part_name"] for r in wheel] == ["Rear wheel rim"], "the end stays, only the side goes"
        # A person's tick (or renaming the part, which clears side_inferred) confirms it.
        for confirmed in ({**inferred, "human_verified": True}, {**inferred, "side_inferred": False}):
            again = rows(sync_annex_a(db, {**plain, "damage_summary": [confirmed]}, price_book=False))
            assert [(r["part_name"], r["source_part"], r["stale"]) for r in again] == [("Tail lamp LH", "Left Tail Lamp", False)], again
        # Rows priced while unconfirmed are kept, prices and all, but take the
        # sided wording once confirmed (review item 6); a typed row doesn't.
        by_name = {r["part_name"]: r for r in priced}
        lamp_est, clips_est = by_name["Tail lamp"]["workshop_est"], by_name["Tail lamp clips"]["workshop_est"]
        confirmed_case = case([{**inferred, "human_verified": True}])
        renamed = rows(sync_annex_a(db, {**confirmed_case, "annex_a": {"items": priced}}, price_book=True))
        assert sorted((r["part_name"], r["workshop_est"], r["stale"]) for r in renamed) == \
            [("Tail lamp LH", lamp_est, False), ("Tail lamp clips LH", clips_est, False)], renamed
        typed = [{**by_name["Tail lamp"], "origin": "typed"}, dict(by_name["Tail lamp clips"])]
        kept = rows(sync_annex_a(db, {**confirmed_case, "annex_a": {"items": typed}}, price_book=True))
        assert sorted((r["part_name"], r["workshop_est"]) for r in kept) == [("Tail lamp", lamp_est), ("Tail lamp clips LH", clips_est)], kept
        ok("unconfirmed inferred side: rows worded and looked up as '(side undetermined)' (priced, unpriced, scaffold), source_part the sided name, not stale")
        ok("confirmed (ticked, or the part renamed): unpriced rows rebuilt as 'Tail lamp LH'; priced machine rows renamed with their prices kept; a typed row untouched")
    finally:
        db.close()


def check_review_annex_fixes() -> None:
    print("review fixes in Annex A (2026-11-08)")
    db = SessionLocal()
    try:
        # 1. A case filed before the firm's wording: its typed row says "Boot
        # Lid" / "Left Tail Lamp"; no second row in the firm's words.
        for part in ("Boot Lid", "Left Tail Lamp", "Left Rear Quarter Panel"):
            old = {**case([{"part": part, "damage_type": "Dent"}], "Gammamotors", "Harrier", "SUV"),
                   "annex_a": {"items": [{"part_name": part, "origin": "typed", "workshop_est": 500.0, "qty": 1, "discount_pct": 0.2,
                                          "source_part": part, "condition": "Dent", "action": "Replace"}]}}
            out = rows(sync_annex_a(db, old, price_book=False))
            assert [(r["part_name"], r["origin"]) for r in out] == [(part, "typed")], out
        ok("1: a kept row made for the part (source_part) stands for it -- no duplicate row for cases filed with canonical names")

        # 3. Each damaged part keeps its own rows, even when the wording is shared.
        both = [{"part": "Left Tail Lamp", "side_inferred": True, "damage_type": "Crack"},
                {"part": "Right Tail Lamp", "side_inferred": True, "damage_type": "Crack"}]
        plain = sync_annex_a(db, case(both, "Gammamotors", "Harrier", "SUV"), price_book=False)
        assert [(r["part_name"], r["source_part"]) for r in rows(plain)] == [("Tail lamp", "Left Tail Lamp"), ("Tail lamp", "Right Tail Lamp")], rows(plain)
        priced = sync_annex_a(db, case(both), price_book=True)
        got = sorted((r["source_part"], r["part_name"], r["workshop_est"]) for r in rows(priced))
        assert len(got) == 4 and {g[0] for g in got} == {"Left Tail Lamp", "Right Tail Lamp"} and all(g[2] > 0 for g in got), got
        again = sync_annex_a(db, priced, price_book=True)
        assert sorted((r["source_part"], r["part_name"], r["workshop_est"]) for r in rows(again)) == got, rows(again)
        mixed = [{"part": "Tail Lamp" + UNDETERMINED, "damage_type": "Crack"}, {"part": "Left Tail Lamp", "side_inferred": True}]
        assert [(r["part_name"], r["source_part"]) for r in rows(sync_annex_a(db, case(mixed), price_book=False))] == \
            [("Tail lamp", "Tail Lamp" + UNDETERMINED), ("Tail lamp", "Left Tail Lamp")]
        ok("3: two unconfirmed lamps (or an undetermined and an inferred one) keep a row each, priced each, stable on the next sync")
        # Two entries naming ONE part ("Boot Lid" and "Tail gate" are both
        # Boot Lid) share its rows: its past kit is priced once, not once
        # per entry (live check, 2026-11-09), on the first sync and the next.
        same = [{"part": "Boot Lid", "damage_type": "Dent"}, {"part": "Tail gate", "damage_type": "Dent"}]
        once = rows(sync_annex_a(db, case(same), price_book=True))
        assert sorted(r["part_name"] for r in once) == ["Tail gate assy", "Tail gate stopper"], once
        assert sorted(r["part_name"] for r in rows(sync_annex_a(db, {**case(same), "annex_a": {"items": once}}, price_book=True))) ==             ["Tail gate assy", "Tail gate stopper"]
        # Unpriced, a typed wording stays the person's ("Tail gate"), so
        # distinct wordings keep a row each; one wording prints once.
        assert [r["part_name"] for r in rows(sync_annex_a(db, case(same), price_book=False))] == ["Tail gate assy", "Tail gate"]
        bumper = [{"part": "Rear Bumper", "damage_type": "Dent"}, {"part": "Rear bumper fascia", "damage_type": "Dent"}]
        assert [r["part_name"] for r in rows(sync_annex_a(db, case(bumper), price_book=False))] == ["Rear bumper fascia"]
        assert [r["part_name"] for r in rows(sync_annex_a(db, case(bumper), price_book=True))] == ["Rear bumper fascia"]
        ok("3b: two entries naming one part share its rows -- its past lines are priced once, stable on the next sync")

        # 6. The part's own row follows tab-1 condition / remarks, price untouched.
        first = sync_annex_a(db, case([{"part": "Boot Lid", "condition": "Repair", "remarks": "Repair"}]), price_book=True)
        main = [r for r in rows(first) if r.get("main")]
        assert [(r["part_name"], r["condition"], r["action"], r["discount_pct"], r["adjusted_cost"]) for r in main] == \
            [("Tail gate assy", "Repair", "Repair", 1.0, 0.0)] and main[0]["workshop_est"] > 0, main
        assert not any(r.get("main") for r in rows(first) if r["part_name"] != "Tail gate assy"), "fittings aren't main"
        est = main[0]["workshop_est"]
        second = sync_annex_a(db, {**first, "damage_summary": [{"part": "Boot Lid", "condition": "Dented", "remarks": "Replace"}]}, price_book=True)
        main = next(r for r in rows(second) if r.get("main"))
        assert (main["condition"], main["action"], main["discount_pct"], main["workshop_est"], main["adjusted_cost"]) == \
            ("Dented", "Replace", 0.2, est, round(est * 0.8, 2)), main
        typed = {**second, "annex_a": {"items": [{**r, "origin": "typed", "condition": "Buckled"} if r.get("main") else r for r in rows(second)]}}
        third = sync_annex_a(db, {**typed, "damage_summary": [{"part": "Boot Lid", "condition": "Repair"}]}, price_book=True)
        main = next(r for r in rows(third) if r.get("main"))
        assert (main["condition"], main["discount_pct"], main["origin"]) == ("Buckled", 0.2, "typed"), main
        assert [r.get("main") for r in rows(synthesize_annexes_for_report(case([{"part": "Bonnet"}])))] == [True]
        assert [r.get("main") for r in rows(sync_annex_a(db, case([{"part": "Bonnet"}]), price_book=False))] == [True]
        ok("6: the main row (marked main) follows a tab-1 condition / remarks change with its price kept; Repair disallows, leaving Repair restores 20%; a typed row is the person's")
    finally:
        db.close()


# ------------------------------------------------------------- side resolver


def check_side_resolver() -> None:
    print("placing '(side undetermined)' parts from the photo areas")
    # Camera order. o and p have no sided neighbour (n: underbody, o: rear).
    photos = [("a", "rear-left"), ("doc", None), ("b", "rear"), ("c", "rear-left"), ("d", "rear"), ("e", "right"),
              ("f", "rear"), ("g", "front"), ("h", "rear-left"), ("i", "left"), ("j", "rear-right"), ("k", "rear"),
              ("m", "interior"), ("n", "underbody"), ("o", "rear"), ("p", "front")]

    def item(part, photo=None, **kw):
        return {"part": part, "photo_reference": f"@{photo}" if photo else None, "ai_confidence": "High", **kw}

    # Neighbours only: b's own area says no side; the nearest photo of the car
    # each way (a, skipping the document; c) are both rear-left.
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "b")], photos)
    assert out == [{**item("Left Tail Lamp", "b"), "ai_confidence": "Low", "side_inferred": True}], out
    ok("neighbours agree (the nearest car photo each way, past a document): placed, ai_confidence Low, side_inferred")

    # Split: d sits between rear-left (c) and right (e).
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "d")], photos)
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED and not out[0].get("side_inferred"), out
    ok("split evidence (one each way): left undetermined")

    # No side on the part's own photo (b: rear): the others decide on a clear
    # majority -- 2 to 1 places it, 3 to 2 does not.
    found_right = item("Right Rear Bumper Reflector", "k")
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "b"), found_right], photos)
    assert out[0]["part"] == "Left Tail Lamp" and out[0]["side_inferred"] is True, out
    close = [item("Tail Lamp" + UNDETERMINED, "b"), found_right, item("Right Boot Lid Lamp", "f"), item("Left Rear Bumper Reflector", "o")]
    out = resolve_sides(close, photos)
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED, out
    ok("no side on its own photo: a 2-to-1 majority of the others places it; 3-to-2 does not (a wrong side is worse than none)")

    # Review item 7: a side on the part's own photo is the answer when
    # nothing contradicts it, and nothing is placed when anything does --
    # the others can no longer outvote the photo the reader saw it in.
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "h")], photos)  # rear-left; neighbours front (no side) and left
    assert out[0]["part"] == "Left Tail Lamp", out
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "j"), found_right], photos)  # rear-right; neighbour i is left
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED, out
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "j"), found_right, item("Right Boot Lid Lamp", "f"), item("Right Fog Lamp", "k")], photos)
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED, "3 agreeing clues still can't outvote one contradiction of the photo"
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "a"), item("Right Rear Bumper Reflector", "k")], photos)
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED, "a right finding in the rear zone contradicts a rear-left photo"
    ok("7: the part's own photo side places it unless any other clue contradicts it (then nothing)")

    # Review item 12: NEIGHBOUR_REACH 2 skips one area-less photo, not two.
    reach = [("x", "rear-left"), ("doc1", None), ("doc2", None), ("y", "rear"), ("z", "rear")]
    assert resolve_sides([item("Tail Lamp" + UNDETERMINED, "y")], reach)[0]["part"] == "Tail Lamp" + UNDETERMINED
    assert resolve_sides([item("Tail Lamp" + UNDETERMINED, "y")], [reach[0], reach[1], *reach[3:]])[0]["part"] == "Left Tail Lamp"
    ok("12: a neighbour is found past one area-less photo, not past two (NEIGHBOUR_REACH 2)")

    # Review item 9: the other party's car, excluded and unusable photos don't vote.
    from types import SimpleNamespace as P
    shots = [P(id="t", area="left", kind="third_party", excluded=False, quality="good"),
             P(id="u", area="rear", kind="damage", excluded=False, quality="good"),
             P(id="v", area="rear-left", kind="overview", excluded=True, quality="good"),
             P(id="w", area="rear-left", kind="overview", excluded=False, quality="unusable"),
             P(id="q", area="rear-right", kind="damage", excluded=False, quality="blurry")]
    assert voting_areas(shots) == [("t", None), ("u", "rear"), ("v", None), ("w", None), ("q", "rear-right")]
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "u")], voting_areas(shots))
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED, "t (third party) is no clue; v is excluded, w unusable: no neighbour after u within reach"
    ok("9: photos of the other party's car, excluded and unusable photos give no clue")

    # Review item 4: a part a person checked as "(side undetermined)" stays so.
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "b")], photos, keep_undetermined={"Tail Lamp" + UNDETERMINED})
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED, out
    current = {"damage_summary": [{"part": "Tail Lamp" + UNDETERMINED, "human_verified": True}]}
    merged = merge_redraft(current, {}, {"damage_summary": [
        {"part": "Left Tail Lamp", "side_inferred": True}, {"part": "Right Tail Lamp"}, {"part": "Tail Lamp" + UNDETERMINED}]})
    assert [(i["part"], bool(i.get("human_verified"))) for i in merged["damage_summary"]] == \
        [("Tail Lamp" + UNDETERMINED, True), ("Right Tail Lamp", False)], merged["damage_summary"]
    ok("4: a re-read never places (or re-adds as 'Left ...') a part the surveyor checked as undetermined; a lamp the reader itself sided is still added")

    # Other sided findings in the same zone vote; one in another zone doesn't.
    out = resolve_sides([item("Boot Lid Lamp" + UNDETERMINED, "o"), item("Left Tail Lamp", "a")], photos)
    assert out[0]["part"] == "Left Boot Lid Lamp", out
    out = resolve_sides([item("Fog Lamp" + UNDETERMINED, "p"), item("Left Tail Lamp", "a")], photos)
    assert out[0]["part"] == "Fog Lamp" + UNDETERMINED, out
    # An inferred side is no evidence for another.
    out = resolve_sides([item("Boot Lid Lamp" + UNDETERMINED, "o"), item("Left Tail Lamp", "a", side_inferred=True)], photos)
    assert out[0]["part"] == "Boot Lid Lamp" + UNDETERMINED, out
    ok("findings the reader placed in the same zone vote (front ones don't count for a rear part; inferred ones never)")

    # Unify: the placed name is already listed -> dropped, the reader's kept.
    out = resolve_sides([item("Left Tail Lamp", "a"), item("Tail Lamp" + UNDETERMINED, "b")], photos)
    assert [i["part"] for i in out] == ["Left Tail Lamp"] and out[0]["ai_confidence"] == "High", out
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "b"), item("Tail Lamp" + UNDETERMINED, "b")], photos)
    assert [i["part"] for i in out] == ["Left Tail Lamp"], out
    ok("a placed part already listed is dropped (once per name)")

    # Doors aren't placed; a wheel needs its photo to say front or rear.
    out = resolve_sides([item("Door" + UNDETERMINED, "i"), item("Wheel" + UNDETERMINED, "h"), item("Wheel" + UNDETERMINED, "i"),
                         item("Quarter Panel" + UNDETERMINED, "h"), item("Headlamp" + UNDETERMINED)], photos)
    assert [i["part"] for i in out] == ["Door" + UNDETERMINED, "Left Rear Wheel", "Wheel" + UNDETERMINED,
                                        "Left Rear Quarter Panel", "Headlamp" + UNDETERMINED], out
    assert sided_part("Wheel Arch Liner" + UNDETERMINED, "Right", "Front") == "Right Front Wheel Arch Liner"
    assert sided_part("Left Tail Lamp", "Right") is None
    ok("doors stay undetermined; a wheel is placed only with its end (rear-left photo); no photo, no clue")


# ------------------------------------------------------------- never overwritten


def check_condition_kept() -> None:
    print("a condition once set is changed only by a person sending one")
    current = {"damage_summary": [{"part": "Rear Bumper", "damage_type": "Dent", "condition": "Buckled/cut",
                                   "condition_tokens": ["Buckled", "Cut"], "human_verified": True}]}
    new_ai = {"damage_summary": [{"part": "Rear Bumper", "damage_type": "Dent", "condition": "Dented", "condition_tokens": ["Dented"]},
                                 {"part": "Boot Lid", "damage_type": "Crack", "condition": "Cracked", "condition_tokens": ["Cracked"]}]}
    merged = merge_redraft(current, {}, new_ai)
    assert [(i["part"], i["condition"]) for i in merged["damage_summary"]] == [("Rear Bumper", "Buckled/cut"), ("Boot Lid", "Cracked")], merged
    ok("a re-read keeps the condition of a part the surveyor checked")

    prev = SecurityIncidentDraft(description="x", damage_summary=[
        DamageSummaryItem(part="Rear Bumper", damage_type="Deformation", condition="Grazed/cut", condition_tokens=["Grazed", "Cut"],
                          remarks="Replace", side_inferred=True),
        DamageSummaryItem(part="Boot Lid", damage_type="Dent", condition="Dented", condition_tokens=["Dented"]),
    ])
    redraft = SecurityIncidentDraft(description="y", damage_summary=[
        DamageSummaryItem(part="Rear Bumper"),
        DamageSummaryItem(part="Boot Lid", damage_type="Crack", condition="Cracked", condition_tokens=["Cracked"]),
    ])
    assert edit_mentions_damage("the boot lid is cracked, not dented") and edit_mentions_damage("bumper is cut")
    assert edit_mentions_damage("it's buckled and leaked") and edit_mentions_damage("the lamp is chipped")
    # Review item 5: everyday words that are also condition words don't count.
    for everyday in ("he cut into my lane at 5pm", "traffic was jammed", "the location shifted to Jurong", "the cable was slack",
                     "I bent down to look at 16:45", "the ticket was torn"):
        assert not edit_mentions_damage(everyday), everyday
    carried = _carry_over_damage(redraft, prev, "the boot lid is cracked, not dented")
    bumper, boot = carried.damage_summary
    assert (bumper.condition, bumper.condition_tokens, bumper.remarks, bumper.side_inferred, bumper.damage_type) == \
        ("Grazed/cut", ["Grazed", "Cut"], "Replace", True, "Deformation"), bumper
    assert (boot.condition, boot.condition_tokens) == ("Cracked", ["Cracked"]), "the model's revision of the same parse stands"
    whole = _carry_over_damage(SecurityIncidentDraft(description="z"), prev, "the time was 16:45")
    assert [(i.condition, i.remarks) for i in whole.damage_summary] == [("Grazed/cut", "Replace"), ("Dented", None)]
    lines = _damage_lines(prev)
    assert lines[0].endswith("Rear Bumper · Grazed/cut · Deformation") and lines[1].endswith("Boot Lid · Dented · Dent"), lines
    ok("Telegram re-draft: a blank condition, words, remarks and side_inferred come from the previous draft; the summary shows the condition before the type")

    db = SessionLocal()
    try:
        out = rows(sync_annex_a(db, case([{"part": "Rear Bumper", "damage_type": "Crack", "condition": "Buckled/cut", "condition_tokens": ["Buckled", "Cut"]}]), price_book=True))
        assert next(r for r in out if r["part_name"] == "Rear bumper fascia")["condition"] == "Buckled/cut", out
    finally:
        db.close()
    ok("sync prints the item's condition, not one rebuilt from its type or words")


# ------------------------------------------------------------- routes


def new_case(client: TestClient, items: list[dict]) -> str:
    r = client.post("/reports", json={"draft": {
        "category": ["Vehicle Collision or Damage"], "description": "Rear-ended",
        "vehicle_info": {"plate_number": "TEST3C", "make": "Alphamotors", "model": "Vezel"},
        "damage_summary": items,
    }})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def set_item(rid: str, index: int, **fields) -> None:
    db = SessionLocal()
    try:
        row = db.get(Report, rid)
        items = [dict(i) for i in row.data["damage_summary"]]
        items[index].update(fields)
        row.data = {**row.data, "damage_summary": items}
        db.commit()
    finally:
        db.close()


def check_routes() -> None:
    print("PATCH / POST /damage-items and GET /taxonomy")
    client = TestClient(app)
    with client:
        sign_in(client, role="user", name="Sue Seesall", sees_all_cases=True)
        rid = new_case(client, [{"part": "Boot Lid", "damage_type": "Dent"}, {"part": "Left Tail Lamp", "damage_type": "Crack"},
                                {"part": "Rear Bumper"}])
        patch = lambda i, body: client.patch(f"/reports/{rid}/damage-items/{i}", json=body)  # noqa: E731

        r = patch(0, {"condition": "cut/BUCKLED", "remarks": "replace"})
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert (item["condition"], item["condition_tokens"], item["remarks"], item["damage_type"]) == ("Cut/buckled", ["Cut", "Buckled"], "Replace", "Dent"), item
        main = next(x for x in r.json()["annex_a"]["items"] if x["part_name"] == "Tail gate assy")
        assert (main["condition"], main["action"]) == ("Cut/buckled", "Replace"), main
        r = patch(0, {"condition": "Necessary"})
        assert r.status_code == 200 and (r.json()["item"]["condition"], r.json()["item"]["condition_tokens"]) == ("Necessary", []), r.text
        ok("PATCH condition: firm words in the firm's casing, words follow; remarks Replace; Annex A's main row follows")

        for body, bad in (({"condition": "Bent/Broken"}, "'Broken'"), ({"condition": "Scratched"}, "'Scratched'"),
                          ({"condition": "Grazed, cut"}, "'Grazed, cut'"), ({"remarks": "Straighten"}, "'Straighten'")):
            r = patch(0, body)
            assert r.status_code == 400 and bad in r.json()["detail"], (body, r.text)
        r = patch(0, {"condition": "", "remarks": None})
        assert r.status_code == 200 and (r.json()["item"]["condition"], r.json()["item"]["condition_tokens"], r.json()["item"]["remarks"]) == (None, [], None), r.text
        ok("words off the firm's list and remarks other than Replace / Repair are refused (400, naming them); blank clears")

        fresh = new_case(client, [{"part": "Boot Lid", "damage_type": "Dent"}])
        r = client.patch(f"/reports/{fresh}/damage-items/0", json={"condition": "Repair"})
        main = next(x for x in r.json()["annex_a"]["items"] if x["part_name"] == "Tail gate assy")
        assert main["discount_pct"] == 1.0 and main["adjusted_cost"] == 0.0 and main["workshop_est"] > 0 and main["main"] is True, main
        ok("PATCH condition Repair: the part's row is disallowed at 100%")

        # Review item 6 through the routes: the priced main row follows tab 1
        # (price kept) -- also after an Annex A save from a client that sends
        # no `main` -- until a person edits that row in Annex A.
        main_row = lambda items: next(x for x in items if x["part_name"] == "Tail gate assy")  # noqa: E731
        before = main_row(patch(0, {"condition": "Bent"}).json()["annex_a"]["items"])
        assert (before["condition"], before["main"]) == ("Bent", True) and before["workshop_est"] > 0, before
        items = client.get(f"/reports/{rid}").json()["data"]["annex_a"]["items"]
        assert client.put(f"/reports/{rid}/annex-a", json={"items": [{k: v for k, v in x.items() if k != "main"} for x in items]}).status_code == 200
        after = main_row(patch(0, {"condition": "Cracked", "remarks": "Replace"}).json()["annex_a"]["items"])
        assert (after["condition"], after["action"], after["workshop_est"], after["main"]) == ("Cracked", "Replace", before["workshop_est"], True), after
        items = client.get(f"/reports/{rid}").json()["data"]["annex_a"]["items"]
        edited = [{**x, "condition": "Buckled/cut"} if x["part_name"] == "Tail gate assy" else x for x in items]
        assert client.put(f"/reports/{rid}/annex-a", json={"items": edited}).status_code == 200
        typed_row = main_row(patch(0, {"condition": "Dented"}).json()["annex_a"]["items"])
        assert (typed_row["condition"], typed_row["origin"]) == ("Buckled/cut", "typed"), typed_row
        ok("6: a tab-1 condition / remarks change reaches the priced main row (price kept; `main` survives an Annex A save without it); a row edited in Annex A is the person's")

        # D2 rule 2: changing the type never rewrites a condition that is set.
        patch(1, {"condition": "Grazed/cracked"})
        r = patch(1, {"damage_type": "Dent"})
        assert (r.json()["item"]["damage_type"], r.json()["item"]["condition"]) == ("Dent", "Grazed/cracked"), r.json()["item"]
        # Words on an item with no type: the type they imply.
        r = patch(2, {"condition": "Deformed/cut"})
        assert r.json()["item"]["damage_type"] == "Deformation", r.json()["item"]
        ok("a new damage type leaves a set condition alone; words on an item with no type fill it")

        # D2 rule 3, as changed by review item 10: only ticking Verified or
        # renaming the part confirms an inferred side.
        set_item(rid, 1, side_inferred=True, ai_confidence="Low", human_verified=False)
        for body in ({"oem_part_number": "33550-XXX"}, {"condition": "Cracked"}, {"remarks": "Replace"}, {"severity": "Minor"},
                     {"damage_type": "Crack"}, {"human_verified": False}, {"part": "Left Tail Lamp"}):
            r = patch(1, body)
            assert r.status_code == 200 and r.json()["item"]["side_inferred"] is True, (body, r.json()["item"])
        r = patch(1, {"human_verified": True})
        assert r.status_code == 200 and r.json()["item"]["side_inferred"] is False and r.json()["item"]["human_verified"] is True, r.json()["item"]
        set_item(rid, 1, side_inferred=True, human_verified=False)
        assert patch(1, {"part": "Right Headlamp"}).json()["item"]["side_inferred"] is False
        patch(1, {"part": "Left Tail Lamp"})
        ok("10: ticking Verified or renaming the part clears side_inferred; an OEM, condition, remarks, severity, type or untick edit keeps it")

        # Through the routes, unpriced (a self-signup): while the lamp's side
        # is inferred, an edit to ANOTHER part syncs Annex A with "Tail lamp";
        # ticking the lamp itself brings "Tail lamp LH".
        with TestClient(app) as plain:
            sign_in(plain, role="user", name="Pat Plain")
            pid = new_case(plain, [{"part": "Rear Bumper", "damage_type": "Deformation"}, {"part": "Left Tail Lamp", "damage_type": "Crack"}])
            set_item(pid, 1, side_inferred=True, ai_confidence="Low")
            out = plain.patch(f"/reports/{pid}/damage-items/0", json={"severity": "Minor"}).json()
            lamp = [x for x in out["annex_a"]["items"] if x["source_part"] == "Left Tail Lamp"]
            assert [(x["part_name"], x["stale"]) for x in lamp] == [("Tail lamp", False)] and out["damage_summary"][1]["side_inferred"] is True, lamp
            out = plain.patch(f"/reports/{pid}/damage-items/1", json={"human_verified": True}).json()
            lamp = [x for x in out["annex_a"]["items"] if x["source_part"] == "Left Tail Lamp"]
            assert [(x["part_name"], x["stale"]) for x in lamp] == [("Tail lamp LH", False)], lamp
        ok("through PATCH: an unconfirmed inferred lamp's row reads 'Tail lamp' (source_part 'Left Tail Lamp'); ticking it gives 'Tail lamp LH'")

        # Review item 11: the suggestions panel offers the unsided wording too.
        sid = new_case(client, [{"part": "Left Tail Lamp", "damage_type": "Crack"}])
        set_item(sid, 0, side_inferred=True, ai_confidence="Low")
        sugg = client.get(f"/reports/{sid}/annex-suggestions").json()
        names = [ln["name"] for g in sugg["groups"] if g["reason"] == "damaged" for ln in g["lines"]]
        assert names and all(side_of(n) is None for n in names) and sugg["damaged_parts"] == ["Tail Lamp" + UNDETERMINED], sugg
        set_item(sid, 0, human_verified=True)
        sugg = client.get(f"/reports/{sid}/annex-suggestions").json()
        names = [ln["name"] for g in sugg["groups"] if g["reason"] == "damaged" for ln in g["lines"]]
        assert "Tail lamp LH" in names and sugg["damaged_parts"] == ["Left Tail Lamp"], sugg
        ok("11: annex-suggestions offers 'Tail lamp' for an unconfirmed inferred side, 'Tail lamp LH' once it is ticked")

        # D2 rule 1: the add row sends words and remarks, no type.
        post = lambda body: client.post(f"/reports/{rid}/damage-items", json=body)  # noqa: E731
        r = post({"part": "Right Tail Lamp", "condition": "grazed/cracked", "remarks": "Replace"})
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert (item["damage_type"], item["condition"], item["condition_tokens"], item["remarks"], item["human_verified"], item["side_inferred"]) == \
            ("Crack", "Grazed/cracked", ["Grazed", "Cracked"], "Replace", True, False), item
        row = next(x for x in r.json()["annex_a"]["items"] if x["source_part"] == "Right Tail Lamp" and x["prefilled"] and "clips" not in x["part_name"])
        assert (row["part_name"], row["condition"], row["action"]) == ("Tail lamp RH", "Grazed/cracked", "Replace"), row
        r = post({"part": "Rear Windscreen", "condition": "Necessary"})
        assert (r.json()["item"]["damage_type"], r.json()["item"]["condition"]) == (None, "Necessary"), r.json()["item"]
        r = post({"part": "Bonnet", "damage_type": "Dent"})
        assert (r.json()["item"]["condition"], r.json()["item"]["condition_tokens"]) == ("Dented", ["Dented"]), r.json()["item"]
        assert post({"part": "Roof", "condition": "Broken"}).status_code == 400
        assert post({"part": "Roof", "remarks": "Renew"}).status_code == 400
        ok("POST: words with no type get the type they imply (none for Necessary); a type with no words gets its firm word; bad values 400")

        tax = client.get("/taxonomy").json()
        assert tax["condition_tokens"] == FIRM_CONDITION_TOKENS and tax["condition_common"] == FIRM_CONDITION_COMMON
        assert tax["condition_whole"] == ["Necessary", "Serviceable", "Repair"] and tax["remarks"] == ["Replace", "Repair"]
        assert tax["parts"] == CANONICAL_PARTS
        ok("GET /taxonomy lists the condition words, the common compounds, the whole-word conditions and the remarks")

        # Filing an intake keeps a condition typed at review (D2 rule 2).
        draft = client.post("/intake").json()["id"]
        body = {"category": ["Vehicle Collision or Damage"], "description": "Rear-ended",
                "vehicle_info": {"plate_number": "TEST4D", "make": "Alphamotors", "model": "Vezel"},
                "damage_summary": [{"part": "Boot Lid", "damage_type": "Dent", "condition": "Bent/cut", "condition_tokens": ["Bent", "Cut"],
                                    "remarks": "Replace", "side_inferred": True}]}
        assert client.put(f"/intake/{draft}/data", json={"data": body}).status_code == 200
        assert client.post(f"/intake/{draft}/finalize", json={"data": body}).status_code == 200
        filed = client.get(f"/reports/{draft}").json()["data"]
        assert (filed["damage_summary"][0]["condition"], filed["damage_summary"][0]["side_inferred"]) == ("Bent/cut", True), filed["damage_summary"]
        main = next(x for x in filed["annex_a"]["items"] if x["part_name"] == "Tail gate assy")
        assert (main["condition"], main["action"]) == ("Bent/cut", "Replace"), main
        ok("filing an intake keeps the condition typed at review (and the side flag until a person edits it); Annex A prints it")


def check_family_lines() -> None:
    print("a past line with no side prints once for both sides of a family")
    # A made-up past case with both tail lamps: one line for the pair
    # ("Taillamps assy" x2, as the real Honda case wrote it) and one per side.
    both_lamps = PastCase(ref="CL 90003", plate="TEST3C", make="Deltamotors", model="Pilot", make_model_raw="Deltamotors Pilot",
                          registered=None, surveyed="2025-03-01", lines=[
                              line(1, "Taillamps assy", "Cracked", 2, 800, 640),
                              line(2, "Tail lamp garnish LH", "Scratched", 1, 100, 80),
                          ])
    db = SessionLocal()
    try:
        import_case(db, both_lamps, "synthetic-3.xlsx")
        db.commit()
        sides = [{"part": "Left Tail Lamp", "damage_type": "Crack"}, {"part": "Right Tail Lamp", "damage_type": "Crack"}]
        first = sync_annex_a(db, case(sides, "Deltamotors", "Pilot"), price_book=True)
        got = sorted((r["source_part"], r["part_name"], r["workshop_est"]) for r in rows(first))
        assert got == [("Left Tail Lamp", "Tail lamp garnish LH", 100.0), ("Left Tail Lamp", "Taillamps assy", 800.0),
                       ("Right Tail Lamp", "Tail lamp garnish RH", 100.0)], got
        again = sync_annex_a(db, first, price_book=True)
        assert sorted((r["source_part"], r["part_name"], r["workshop_est"]) for r in rows(again)) == got, rows(again)
        ok("both lamps damaged: the pair's 'Taillamps assy' x2 priced once, each side keeps its own sided line; stable on the next sync")
        inferred = [dict(s, side_inferred=True) for s in sides]
        first = sync_annex_a(db, case(inferred, "Deltamotors", "Pilot"), price_book=True)
        names = sorted(r["part_name"] for r in rows(first))
        assert names == ["Tail lamp garnish", "Tail lamp garnish", "Taillamps assy"], names
        assert sorted(r["part_name"] for r in rows(sync_annex_a(db, first, price_book=True))) == names
        ok("sides not yet confirmed: the pair's line once, the per-side line once per lamp (no LH / RH), stable")
    finally:
        db.close()


def main() -> int:
    init_db()
    import_history()
    check_vocabulary()
    check_floor_names()
    check_schema_and_parse()
    check_annex_rows()
    check_side_leak()
    check_inferred_side_rows()
    check_review_annex_fixes()
    check_side_resolver()
    check_condition_kept()
    check_routes()
    check_family_lines()
    print("\nALL DETECTION WORDING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
