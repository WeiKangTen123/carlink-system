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
from app.intake.pipeline import merge_redraft, resolve_sides  # noqa: E402
from app.parts.history import PastCase, PastLine, firm_wording, import_case, side_of  # noqa: E402
from app.parts.prefill import sync_annex_a  # noqa: E402
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

        # Firm wording from history, by body class, when nothing is priced.
        assert firm_wording(db, "Boot Lid", "Gammamotors", "Harrier", "SUV") == "Tail gate assy"
        assert firm_wording(db, "Boot Lid", "Gammamotors", "City", "Sedan") == "Boot lid assy"
        assert firm_wording(db, "Left Tail Lamp", "Gammamotors", "Harrier", "SUV") == "Tail lamp LH"
        assert firm_wording(db, "Right Tail Lamp", "Gammamotors", "City", "Sedan") == "Tail lamp RH"
        assert firm_wording(db, "Bonnet", "Alphamotors", "Vezel", "SUV") is None, "no past case had one: the caller takes the floor"
        assert firm_wording(db, "Left Rear Wheel", "Alphamotors", "Vezel", "SUV") is None, "Wheel history lumps front and rear"
        unpriced = rows(sync_annex_a(db, case([{"part": "Boot Lid", "damage_type": "Dent"}], "Gammamotors", "Harrier", "SUV"), price_book=True))
        assert [(r["part_name"], r["workshop_est"], r["condition"]) for r in unpriced] == [("Tail gate assy", 0.0, "Dented")], unpriced
        # A pre-taxonomy case's free text is kept: its family's wording would be coarser.
        free = rows(sync_annex_a(db, case([{"part": "Tail gate lock striker", "damage_type": "Misalignment"}], "Gammamotors", "Harrier", "SUV"), price_book=False))
        assert [(r["part_name"], r["condition"]) for r in free] == [("Tail gate lock striker", "Shifted")], free
        ok("a part no same-make case prices takes the firm's past wording (same model, else same body class, else any) -- a tailgate for an SUV, a boot lid for a sedan")
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
        assert firm_wording(db, "Tail Lamp" + UNDETERMINED, "Gammamotors", "Harrier", "SUV") == "Tail lamp"
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
        # A person's tick (or any edit, which clears side_inferred) confirms it.
        for confirmed in ({**inferred, "human_verified": True}, {**inferred, "side_inferred": False}):
            again = rows(sync_annex_a(db, {**plain, "damage_summary": [confirmed]}, price_book=False))
            assert [(r["part_name"], r["source_part"], r["stale"]) for r in again] == [("Tail lamp LH", "Left Tail Lamp", False)], again
        # A row already priced while unconfirmed is a kept row: untouched, and
        # it still stands for the lamp (no second, sided row).
        kept = rows(sync_annex_a(db, {**case([{**inferred, "human_verified": True}]), "annex_a": {"items": priced}}, price_book=True))
        assert {r["part_name"] for r in kept} == {"Tail lamp", "Tail lamp clips"} and not any(r["stale"] for r in kept), kept
        ok("unconfirmed inferred side: rows worded and looked up as '(side undetermined)' (priced, unpriced, scaffold), source_part the sided name, not stale")
        ok("confirmed (ticked, or side_inferred cleared): the next sync rebuilds the unpriced row as 'Tail lamp LH'; a row already priced stays as it is")
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

    # Majority 2 to 1: own area rear-right (j) and a reader-placed right
    # finding in the rear zone, against the left neighbour (i).
    found_right = item("Right Rear Bumper Reflector", "k")
    out = resolve_sides([item("Tail Lamp" + UNDETERMINED, "j"), found_right], photos)
    assert out[0]["part"] == "Right Tail Lamp" and out[0]["side_inferred"] is True, out
    # ...but 3 to 2 is too close: add two left findings in the rear zone.
    close = [item("Tail Lamp" + UNDETERMINED, "j"), found_right, item("Left Rear Bumper Reflector", "h"), item("Left Boot Lid Lamp", "c")]
    out = resolve_sides(close, photos)
    assert out[0]["part"] == "Tail Lamp" + UNDETERMINED, out
    ok("a 2-to-1 majority places it; 3-to-2 does not (a wrong side is worse than none)")

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

        # The rows above are priced now, and a priced row is kept as it is
        # by every sync (contract2's handover rule): Repair is checked on a
        # case whose Annex A this edit builds.
        fresh = new_case(client, [{"part": "Boot Lid", "damage_type": "Dent"}])
        r = client.patch(f"/reports/{fresh}/damage-items/0", json={"condition": "Repair"})
        main = next(x for x in r.json()["annex_a"]["items"] if x["part_name"] == "Tail gate assy")
        assert main["discount_pct"] == 1.0 and main["adjusted_cost"] == 0.0 and main["workshop_est"] > 0, main
        kept = patch(0, {"condition": "Bent"}).json()["annex_a"]["items"]
        assert next(x for x in kept if x["part_name"] == "Tail gate assy")["condition"] == "Cut/buckled", "a priced row is not rewritten"
        ok("PATCH condition Repair: the part's row is disallowed at 100%; a row already priced keeps what it says")

        # D2 rule 2: changing the type never rewrites a condition that is set.
        patch(1, {"condition": "Grazed/cracked"})
        r = patch(1, {"damage_type": "Dent"})
        assert (r.json()["item"]["damage_type"], r.json()["item"]["condition"]) == ("Dent", "Grazed/cracked"), r.json()["item"]
        # Words on an item with no type: the type they imply.
        r = patch(2, {"condition": "Deformed/cut"})
        assert r.json()["item"]["damage_type"] == "Deformation", r.json()["item"]
        ok("a new damage type leaves a set condition alone; words on an item with no type fill it")

        # D2 rule 3: ticking Verified alone clears side_inferred.
        set_item(rid, 1, side_inferred=True, ai_confidence="Low")
        r = patch(1, {"human_verified": True})
        assert r.status_code == 200 and r.json()["item"]["side_inferred"] is False and r.json()["item"]["human_verified"] is True, r.json()["item"]
        set_item(rid, 1, side_inferred=True)
        assert patch(1, {"oem_part_number": "33550-XXX"}).json()["item"]["side_inferred"] is False
        ok("any person's edit -- ticking Verified alone included -- clears side_inferred")

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


def main() -> int:
    init_db()
    import_history()
    check_vocabulary()
    check_floor_names()
    check_schema_and_parse()
    check_annex_rows()
    check_side_leak()
    check_inferred_side_rows()
    check_side_resolver()
    check_condition_kept()
    check_routes()
    print("\nALL DETECTION WORDING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
