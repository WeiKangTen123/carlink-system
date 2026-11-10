"""The parts list, names and price book for the light reader (contract 6, T):
the new parts (Window Regulator, Tyre, Wheel Arch Trim) and the firm's
wording for them, other_part_name on an "Other / Not Listed" item end to
end, and the price-book fixes -- quantities a past case wrote (no 1.5
tyres), HR-V finding the Vezel's history, wording ties decided the same way
every time, and the rim, tyre and spare-wheel nut apart
(app/reports/taxonomy.py, app/reports/schema.py, app/parts/history.py,
app/parts/prefill.py).

    python scripts/test_lite_parts.py

Offline: no Gemini call, a temporary database. The past cases are made up
(their part names are the firm's, from the four CL reports; no plates,
VINs or names).
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="lite-parts-test-"))
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

from app.ai.extraction import _response_schema  # noqa: E402
from app.parts.history import (  # noqa: E402
    PastCase,
    PastLine,
    _same_model,
    canonical_part,
    family_and_side,
    family_of,
    import_case,
    split_make_model,
    suggest,
)
from app.parts.prefill import item_key, item_row_name, item_source, sync_annex_a  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import PastAnnexLine, PastLabourLine  # noqa: E402
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft  # noqa: E402
from app.reports.taxonomy import (  # noqa: E402
    CANONICAL_PARTS,
    FIRM_PART_NAMES,
    FIRM_PART_NAMES_HATCH,
    OTHER_PART,
    sided_part,
    unsided_part,
)

UNDETERMINED = " (side undetermined)"
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why else ""))
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


def line(n, item, condition, qty, est, adjusted, section="list") -> PastLine:
    return PastLine(line_no=n, section=section, item=item, condition=condition, remarks="", qty=qty, qty_raw=str(qty),
                    workshop_est=est, discount=0.0 if section == "nett" else 0.2, adjusted_cost=adjusted)


def case(ref, model, lines, make="Honda") -> PastCase:
    return PastCase(ref=ref, plate=f"TEST{ref[-2:]}", make=make, model=model, make_model_raw=f"{make} {model}",
                    registered=None, surveyed="2025-01-01", lines=lines)


SEDAN = case("CL 91001", "Civic", [
    line(1, "Rear bumper assy", "Buckled/cut", 1, 689.2, 551.36),
    line(2, "Rear bumper clips", "Necessary", 1, 50, 40),
    line(3, "Rear air vent", "Bent/cut", 2, 85.2, 68.16),
    line(4, "Rear bumper lower arch garnish", "Distorted/cut", 1, 69.1, 55.28),
    line(5, "Rear bumper mudflaps", "Grazed/cut", 2, 184, 147.2),
    line(6, "Spare tyre panel", "Crumpled", 1, 952.6, 762.08),
    line(7, "Spare tyre panel top board", "Bent/cut", 1, 321.8, 257.44),
    line(8, "Spare tyre wheel nut", "Bent/jammed", 1, 42, 33.6),
    line(9, "Sport rim", "Grazed/warped", 2, 1100, 1100, "nett"),
    line(10, "Front tyre", "Grazed", 2, 760, 760, "nett"),
])
HATCH = case("CL 91002", "Vezel", [
    line(1, "Front door assy RH", "Grazed/dented/warped", 1, 960, 768),
    line(2, "Front door stopper", "Necessary", 2, 20.5, 16.4),
    line(3, "Front door glass regulator mechanism", "Bent", 1, 210, 168),
    line(4, "Front door glass regulator mechanism motor", "Distorted/faulty", 1, 381, 304.8),
    line(5, "Front fender splash shield", "Distorted/cut", 1, 130.2, 104.16),
    line(6, "Front fender wheel arch protector ganrish RH", "Grazed", 1, 180, 144),
    line(7, "Front tyre RH", "Serviceable", 1, 300, 0, "nett"),
    line(8, "Front wheel rim RH", "Grazed/chipped", 1, 650, 650, "nett"),
    line(9, "Rear fender wheel arch garnish LH", "Grazed/slack/cut", 1, 150, 120),
    line(10, "Rear bumper wheel arch protector LH", "Distorted/cut", 1, 150, 120),
])


def reimport(*cases) -> None:
    db = SessionLocal()
    try:
        db.query(PastAnnexLine).delete()
        db.query(PastLabourLine).delete()
        for c in cases:
            import_case(db, c, f"{c.ref}.xlsx")
        db.commit()
    finally:
        db.close()


def rows(parts: list[dict], model="Vezel", make="Honda", price_book=True, data_extra=None) -> list[dict]:
    db = SessionLocal()
    try:
        data = {"vehicle_info": {"make": make, "model": model}, "damage_summary": parts, **(data_extra or {})}
        return sync_annex_a(db, data, price_book=price_book)["annex_a"]["items"]
    finally:
        db.close()


def names(out: list[dict]) -> list[str]:
    return [r["part_name"] for r in out]


# ---------------------------------------------------------------- sections


def vocabulary() -> None:
    new = [f"{s} {e} {p}" for p in ("Window Regulator", "Tyre", "Wheel Arch Trim") for e in ("Front", "Rear") for s in ("Left", "Right")]
    new += [f"{p}{UNDETERMINED}" for p in ("Window Regulator", "Tyre", "Wheel Arch Trim")]
    check(all(p in CANONICAL_PARTS for p in new) and len(set(CANONICAL_PARTS)) == len(CANONICAL_PARTS),
          f"the {len(new)} new parts are on the list, each once", str([p for p in new if p not in CANONICAL_PARTS]))
    want = {
        "Left Front Window Regulator": "Front door glass regulator mechanism LH",
        "Right Rear Window Regulator": "Rear door glass regulator mechanism RH",
        "Window Regulator" + UNDETERMINED: "Door glass regulator mechanism",
        "Right Front Tyre": "Front tyre RH",
        "Left Rear Tyre": "Rear tyre LH",
        "Tyre" + UNDETERMINED: "Tyre",
        "Right Front Wheel Arch Trim": "Front fender wheel arch garnish RH",
        "Left Rear Wheel Arch Trim": "Rear fender wheel arch garnish LH",
        "Wheel Arch Trim" + UNDETERMINED: "Wheel arch garnish",
        "Right Front Wheel Arch Liner": "Front fender splash shield RH",
        "Left Front Wheel": "Front wheel rim LH",
    }
    got = {p: FIRM_PART_NAMES.get(p) for p in want}
    check(got == want, "the firm's wording: '... regulator mechanism', 'Front tyre', '... wheel arch garnish' apart from the splash shield",
          json.dumps({p: g for p, g in got.items() if g != want[p]}))
    check(all(FIRM_PART_NAMES_HATCH.get(p) == FIRM_PART_NAMES.get(p) for p in new), "the same on a hatch")
    check(unsided_part("Right Front Window Regulator") == "Window Regulator" + UNDETERMINED
          and unsided_part("Left Rear Tyre") == "Tyre" + UNDETERMINED
          and unsided_part("Left Rear Wheel Arch Trim") == "Wheel Arch Trim" + UNDETERMINED,
          "each new sided part has its '(side undetermined)' name")
    check(sided_part("Tyre" + UNDETERMINED, "Left", "Rear") == "Left Rear Tyre"
          and sided_part("Tyre" + UNDETERMINED, "Left", None) is None
          and sided_part("Wheel Arch Trim" + UNDETERMINED, "Right", "Front") == "Right Front Wheel Arch Trim",
          "a tyre or an arch trim is placed on a side only with its end, like a wheel")
    check(sided_part("Window Regulator" + UNDETERMINED, "Left", "Front") is None,
          "a regulator is never placed (a side doesn't say which door), like a door")

    import check_taxonomy_sync

    check(check_taxonomy_sync.main() == 0, "the dashboard's zone map (vehicleZones.ts) has every part")


def families() -> None:
    want = {
        "Front door glass regulator mechanism": "Front Window Regulator",
        "Front door glass regulator mechanism motor": "Front Window Regulator",
        "Rear door window regulator": "Rear Window Regulator",
        "Front door assy RH": "Front Door",
        "Front fender wheel arch protector ganrish RH": "Front Wheel Arch Trim",
        "Front fender splash shield": "Front Wheel Arch Liner",
        "Rear fender wheel arch garnish LH": "Rear Wheel Arch Trim",
        "Rear bumper lower arch garnish": "Rear Wheel Arch Trim",
        "Rear bumper mudflaps": "Rear Wheel Arch Trim",
        "Rear bumper wheel arch protector LH": "Rear Wheel Arch Liner",
        "Sport rim": "Wheel",
        "Front wheel rim RH": "Front Wheel",
        "Rear wheel rim": "Rear Wheel",
        "Front tyre": "Front Tyre",
        "Rear tyres": "Rear Tyre",
        "Spare tyre wheel nut": "Boot Floor Board",
        "Spare tyre panel": "Rear Floor Panel",
        "Wheel nuts": "Wheel Nut",
        "Rear air vent": "Rear Bumper",
        "Side sill panel LH": "Sill / Rocker Panel",
        "Rear cross member": "Rear Subframe",
        "Front cross member": "Front Subframe",
    }
    got = {item: family_of(item) for item in want}
    check(got == want, "the firm's sub-part lines read as their own families, not folded into a parent",
          json.dumps({i: g for i, g in got.items() if g != want[i]}))
    check(canonical_part("Front wheel rim RH") == "Right Front Wheel" and canonical_part("Front tyre RH") == "Right Front Tyre"
          and canonical_part("Rear fender wheel arch garnish LH") == "Left Rear Wheel Arch Trim"
          and canonical_part("Side sill panel LH") == "Left Sill / Rocker Panel",
          "a sided line reads as its canonical part ('Front wheel rim RH' was 'Wheel')")
    check(family_and_side("Left Rear Wheel") == ("Rear Wheel", "Left") and family_and_side("Tyre" + UNDETERMINED) == ("Tyre", None)
          and family_and_side("Quarter Panel" + UNDETERMINED) == ("Rear Quarter Panel", None),
          "families keep the wheel's end; the undetermined quarter panel finds the rear fender's lines")


def models() -> None:
    check(split_make_model("Honda HR-V 1.5 CVT") == ("Honda", "HR-V") and split_make_model("Mazda CX-5 2.0") == ("Mazda", "CX-5")
          and split_make_model("Mercedes-Benz C200 AMG") == ("Mercedes Benz", "C200")
          and split_make_model("Honda Vezel 1.5X CVT") == ("Honda", "Vezel"),
          "an import keeps a hyphenated model (HR-V, CX-5), and still splits Mercedes-Benz")
    check(_same_model("HR-V", "Vezel") and _same_model("Vezel 1.5X", "hr-v") and _same_model("Jazz", "Fit")
          and _same_model("Mazda3", "Axela") and not _same_model("CX-3", "CX-5") and not _same_model("", ""),
          "market twins are one model (HR-V = Vezel, Jazz = Fit, Mazda3 = Axela); CX-3 is not CX-5")
    reimport(SEDAN, HATCH)
    db = SessionLocal()
    try:
        s = suggest(db, make="Honda", model="HR-V", damaged_parts=["Left Rear Wheel Arch Trim"], existing_items=[])
    finally:
        db.close()
    check(s["scope"] == "past Honda Vezel cases", "a case read as 'HR-V' is priced from the Vezel's history, not every Honda", s["scope"])
    out = rows([{"part": "Left Rear Wheel Arch Trim", "condition_tokens": ["Grazed"]}], model="HR-V")
    check(names(out) == ["Rear fender wheel arch garnish LH"] and out[0]["workshop_est"] == 150 and out[0]["main"],
          "the arch garnish is its own priced row, not the splash shield's", json.dumps(names(out)))


def quantities_and_ties() -> None:
    reimport(SEDAN, HATCH)
    db = SessionLocal()
    try:
        s = suggest(db, make="Honda", model="Jazz", damaged_parts=["Left Front Tyre"], existing_items=[])
    finally:
        db.close()
    tyre = [ln for g in s["groups"] for ln in g["lines"]]
    check(len(tyre) == 1 and tyre[0]["qty"] == 1 and tyre[0]["name"] == "Front tyre LH",
          "a both-tyres line (2) and a one-tyre line (1): qty 1, a count a past case wrote -- never 1.5",
          json.dumps([(ln["name"], ln["qty"]) for ln in tyre]))
    out = rows([{"part": "Left Front Tyre", "damage_type": "Puncture / Tear"}], model="Jazz")
    check([(r["part_name"], r["qty"], r["workshop_est"]) for r in out] == [("Front tyre LH", 1.0, 380.0)],
          "the row prices one tyre at the usual unit price (760 for 2 -> 380), not 1.5 x",
          json.dumps([(r["part_name"], r["qty"], r["workshop_est"]) for r in out]))

    one = case("CL 91003", "Civic", [line(1, "Tail lamp LH", "Cracked", 1, 400, 320)])
    two = case("CL 91004", "Civic", [line(1, "Taillamp LH", "Cracked", 1, 400, 320)])
    picked = []
    for order in ((one, two), (two, one)):
        reimport(*order)
        db = SessionLocal()
        try:
            s = suggest(db, make="Honda", model="Civic", damaged_parts=["Left Tail Lamp"], existing_items=[])
        finally:
            db.close()
        picked.append([ln["name"] for g in s["groups"] for ln in g["lines"]])
    check(picked[0] == picked[1] == ["Tail lamp LH"],
          "a tie between two wordings goes to the first in sorted order, whatever the import order "
          "(a set's order changes between processes)", str(picked))


def wheels() -> None:
    reimport(SEDAN, HATCH)
    out = rows([{"part": "Left Front Wheel", "damage_type": "Scratch"}], model="Jazz")
    check(names(out) == ["Front wheel rim LH"] and out[0]["main"] and out[0]["workshop_est"] == 650,
          "a front wheel on another Honda: the same make's front rim, its main row -- no 'Sport rim', tyre or spare-wheel nut",
          json.dumps(names(out)))
    out = rows([{"part": "Left Rear Wheel", "damage_type": "Scratch"}], model="Jazz")
    check([(r["part_name"], r["workshop_est"]) for r in out] == [("Rear wheel rim LH", 0.0)],
          "a rear wheel no past line names: unpriced, never the front rim or another model's 'Sport rim' x2",
          json.dumps([(r["part_name"], r["workshop_est"]) for r in out]))
    out = rows([{"part": "Left Front Wheel", "damage_type": "Scratch"}], model="Civic")
    check([(r["part_name"], r["main"]) for r in out] == [("Sport rim", True)],
          "the Civic's own rim line names no end: it stands in for the front wheel there", json.dumps(names(out)))
    out = rows([{"part": "Boot Floor Board", "damage_type": "Deformation"}], model="Civic")
    check("Spare tyre wheel nut" in names(out) and "Sport rim" not in names(out),
          "the spare wheel's nut comes with the boot floor, not with a wheel", json.dumps(names(out)))
    out = rows([{"part": "Left Front Wheel", "side_inferred": True}], model="Vezel")
    check([(r["part_name"], r["source_part"]) for r in out] == [("Front wheel rim", "Left Front Wheel")] and out[0]["workshop_est"] == 650,
          "an inferred side is priced without LH / RH but keeps the end (front rims only)", json.dumps(names(out)))


def other_parts() -> None:
    reimport(SEDAN, HATCH)
    item = DamageSummaryItem(part=OTHER_PART, other_part_name="  window   regulator motor ")
    check(item.other_part_name == "Window regulator motor", "the name is cleaned: spaces collapsed, first letter capitalised")
    check(DamageSummaryItem(part="Bonnet", other_part_name="anything").other_part_name is None
          and DamageSummaryItem(part=OTHER_PART, other_part_name=" ").other_part_name is None
          and len(DamageSummaryItem(part=OTHER_PART, other_part_name="x" * 300).other_part_name) == 80,
          "only an 'Other / Not Listed' item keeps a name; blank is none; at most 80 characters")
    draft = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [
        {"part": OTHER_PART, "other_part_name": "Rear air vent"}]})
    check(draft.model_dump()["damage_summary"][0]["other_part_name"] == "Rear air vent", "it survives the draft (autosave, finalize)")
    schema = _response_schema()
    field = schema["$defs"]["DamageSummaryItem"]["properties"].get("other_part_name") or {}
    props = list(schema["$defs"]["DamageSummaryItem"]["properties"])
    check(field and "enum" not in json.dumps(field) and len(field.get("description", "")) <= 80 and props.index("other_part_name") == 1,
          f"the reader is asked for it right after part, with no enum and a short description (draft schema {len(json.dumps(schema))} characters)",
          json.dumps(field))

    out = rows([{"part": "Right Front Door", "damage_type": "Dent"},
                {"part": OTHER_PART, "other_part_name": "front window regulator motor", "condition_tokens": ["Faulty"]},
                {"part": OTHER_PART, "other_part_name": "Left door weatherstrip", "damage_type": "Puncture / Tear"}])
    others = [r for r in out if (r["source_part"] or "").startswith(OTHER_PART)]
    check([(r["part_name"], r["workshop_est"], r["condition"], r["main"]) for r in others]
          == [("Front door glass regulator mechanism motor", 381.0, "Faulty", True), ("Door weatherstrip LH", 0.0, "Torn", True)],
          "two unlisted parts are two rows: one the past line it is (priced, its condition), one by its own name (side last)",
          json.dumps([(r["part_name"], r["workshop_est"], r["condition"]) for r in others]))
    check(len({r["source_part"] for r in others}) == 2 and "Front door glass regulator mechanism" not in names(out),
          "each row knows which unlisted part it is for; the regulator itself is not added (only the motor was named)")

    out = rows([{"part": OTHER_PART, "other_part_name": "Rear bumper bracket"}], model="Civic")
    check([(r["part_name"], r["workshop_est"]) for r in out] == [("Rear bumper bracket", 0.0)],
          "a name its family's lines don't hold stays unpriced: a bracket is not priced as the bumper fascia",
          json.dumps([(r["part_name"], r["workshop_est"]) for r in out]))
    out = rows([{"part": "Rear Bumper", "damage_type": "Crack"}, {"part": OTHER_PART, "other_part_name": "Rear air vent"}], model="Civic")
    check(names(out).count("Rear air vent") == 1, "a line the listed part's kit already holds is printed once", json.dumps(names(out)))
    out = rows([{"part": OTHER_PART, "other_part_name": "front window regulator motor"}], price_book=False)
    check(names(out) == ["Front window regulator motor"] and out[0]["workshop_est"] == 0,
          "without the price book: one unpriced row by its name")
    out = rows([{"part": OTHER_PART}, {"part": OTHER_PART}])
    check(names(out) == ["Other part (to be named)"], "unnamed unlisted parts: still the one 'to be named' row")

    # A person prices the first; a second unlisted part comes along.
    first = {"part": OTHER_PART, "other_part_name": "Rear air vent LH"}
    out = rows([first], model="Jazz")
    priced = [{**out[0], "workshop_est": 90.0, "origin": "typed"}]
    second = {"part": OTHER_PART, "other_part_name": "Number plate lamp"}
    out = rows([first, second], model="Jazz", data_extra={"annex_a": {"items": priced}})
    check([(r["part_name"], r["workshop_est"], r["stale"]) for r in out] == [("Rear air vent LH", 90.0, False), ("Number plate lamp", 0.0, False)],
          "a priced unlisted part's row is kept, and a second unlisted part still gets its own row",
          json.dumps([(r["part_name"], r["workshop_est"], r["stale"]) for r in out]))
    out = rows([second], model="Jazz", data_extra={"annex_a": {"items": out}})
    check([(r["part_name"], r["stale"]) for r in out] == [("Rear air vent LH", True), ("Number plate lamp", False)],
          "removing the first flags only its own row stale")
    out = rows([second], model="Jazz", data_extra={"dismissed_parts": [OTHER_PART]})
    check(names(out) == ["Number plate lamp"], "dismissing an unnamed 'Other' doesn't hide a named one")

    check(item_key({"part": OTHER_PART, "other_part_name": "Rear air vent"}) == item_key({"part": OTHER_PART, "other_part_name": "rear  AIR vent"})
          != item_key({"part": OTHER_PART, "other_part_name": "Number plate lamp"}) and item_key({"part": "Left Tail Lamp"}) == "Left Tail Lamp",
          "item_key: one key per unlisted part's name (any case), the canonical part otherwise")
    check(item_row_name({"part": OTHER_PART, "other_part_name": "left rear air vent"}, {}) == "Rear air vent LH"
          and item_row_name({"part": "Left Rear Tyre"}, {}) == "Rear tyre LH",
          "item_row_name (for the scaffold, no database): the name, side last; else the floor")
    check(item_source({"part": OTHER_PART, "other_part_name": "Rear air vent"}) == "Other / Not Listed: Rear air vent"
          and item_source({"part": OTHER_PART}) == OTHER_PART and item_source({"part": "Bonnet"}) == "Bonnet",
          "item_source: the source_part an item's rows carry")


def stored_columns() -> None:
    reimport(HATCH, case("CL 91005", "HR", [line(1, "Rear bumper fascia", "Deformed/cut", 1, 1000, 800)]))
    db = SessionLocal()
    try:
        # As imported before this change: the regulator was the door's, the
        # model "HR" (the hyphen split it).
        for ln in db.query(PastAnnexLine).all():
            if "regulator" in ln.item:
                ln.family = "Front Door"
            if ln.case_ref == "CL 91005":
                ln.make_model_raw = "Honda HR-V 1.5"
        db.commit()
        s = suggest(db, make="Honda", model="Vezel", damaged_parts=["Right Front Window Regulator"], existing_items=[])
        found = [ln["name"] for g in s["groups"] for ln in g["lines"]]
        check(found == ["Front door glass regulator mechanism", "Front door glass regulator mechanism motor"],
              "a past import's stale family column doesn't hide its lines: families are read from the wording", str(found))
        from app.parts.history import refresh_lines

        changed = refresh_lines(db)
        db.commit()
        regulator = {ln.family for ln in db.query(PastAnnexLine).all() if "regulator" in ln.item}
        hrv = {ln.model for ln in db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 91005")}
        check(changed >= 3 and regulator == {"Front Window Regulator"} and hrv == {"HR-V"},
              "refresh_lines brings the stored family and model up to date (a start-up step after a rule change)",
              f"{changed} {regulator} {hrv}")
    finally:
        db.close()


def main_() -> int:
    init_db()
    for name, fn in (
        ("new parts and their wording", vocabulary),
        ("past lines into families", families),
        ("models and twins", models),
        ("quantities and wording ties", quantities_and_ties),
        ("rim, tyre and nut apart", wheels),
        ("an 'Other / Not Listed' part with a name", other_parts),
        ("past imports' stored columns", stored_columns),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main_())
