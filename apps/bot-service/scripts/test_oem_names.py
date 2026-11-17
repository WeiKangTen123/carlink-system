"""An OEM catalogue row's name -> our canonical part (app/parts/oem_names.py).

    python scripts/test_oem_names.py

Checked: the mapper against the gold file (scripts/fixtures/oem_names_gold.json:
300 distinct row names from the microfiches cache, labelled by hand, a third
of them held back from tuning) at >= 85% exact match overall and on the held-
out third, and no wrong part among the trap rows; each trap the module is
written around, also in wordings the gold does not hold; sides from the name
("R.", "L.", the site's cut "R," of "PANEL R, FR FENDER", "(RH)") and from
the caller, "RR." is rear and never right; the "(side undetermined)" name
when no side is known; front or rear from the row, not the diagram; None for
fasteners, clips, labels and bulbs; end_of().

Pure: no database, no network.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.parts.oem_names import end_of, to_family, to_part  # noqa: E402
from app.reports.taxonomy import CANONICAL_PARTS  # noqa: E402

GOLD = ROOT / "scripts" / "fixtures" / "oem_names_gold.json"
MIN_ACCURACY = 0.85

PASSED = [0]
FAILURES: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        PASSED[0] += 1
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label} {detail}")


def accuracy(entries: list[dict]) -> tuple[float, list[str]]:
    wrong = [
        f"{e['name']!r} on {e['diagram']!r}: wanted {e['label']!r}, got {to_part(e['name'], e['diagram'])!r}"
        for e in entries if to_part(e["name"], e["diagram"]) != e["label"]
    ]
    return 1 - len(wrong) / max(1, len(entries)), wrong


def gold() -> None:
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    entries = data["entries"]
    check(len(entries) >= 300, f"the gold holds {len(entries)} rows (>= 300)")
    check(all(e["label"] is None or e["label"] in CANONICAL_PARTS for e in entries),
          "every gold label is a canonical part (or None)")
    held = [e for e in entries if e["split"] == "held"]
    check(abs(len(held) - len(entries) / 3) <= 2, f"a third is held out ({len(held)} of {len(entries)})")
    kinds = {e["kind"] for e in entries}
    check({"part", "none", "trap", "fastener"} <= kinds, "the gold has parts, Nones, traps and fasteners")
    for name, rows in (("overall", entries), ("held-out", held), ("tuning", [e for e in entries if e["split"] != "held"])):
        score, wrong = accuracy(rows)
        check(score >= MIN_ACCURACY, f"{name}: {score:.1%} exact over {len(rows)} rows (>= {MIN_ACCURACY:.0%})",
              "\n    " + "\n    ".join(wrong[:10]))
    # A part where None was wanted is the costly mistake, so it is counted on its own.
    invented = [e for e in entries if e["label"] is None and to_part(e["name"], e["diagram"]) is not None]
    check(not invented, "no part invented where the gold says None", str([e["name"] for e in invented][:8]))
    traps = [e for e in entries if e["kind"] == "trap"]
    check(len(traps) >= 20 and all(to_part(e["name"], e["diagram"]) == e["label"] for e in traps),
          f"all {len(traps)} trap rows map as labelled")


def traps() -> None:
    # (name, diagram, what it must NOT become)
    for name, diagram in (
        ("SILENCER", "TRUNK LID"), ("SILENCER", "FRONT DOOR PANELS"), ("SILENCER COMP.", "TRUNK LID"),
        ("INSULATOR, SILENCER", "EXHAUST PIPE / SILENCER"), ("GASKET, SILENCER", "EXHAUST PIPE / SILENCER"),
        ("PLATE, SILENCER BAFFLE", "EXHAUST PIPE / SILENCER"),
    ):
        check(to_part(name, diagram) != "Exhaust System", f"{name!r} on {diagram!r} is not the Exhaust System")
    check(to_part("SILENCER COMP., EX.", "EXHAUST PIPE / SILENCER") == "Exhaust System", "the exhaust silencer is")
    check(to_part("MUFFLER COMP., EXH.", "EXHAUST PIPE") == "Exhaust System", "a muffler is the Exhaust System")
    check(to_part("BRACKET, MUFFLER", "EXHAUST PIPE") is None, "a muffler bracket is no part of ours")
    for name in ("GRILLE, R. SPEAKER", "GRILLE, L. SPEAKER", "GRILLE, FR. SPEAKER", "GRILLE, SPEAKER"):
        check(to_part(name, "REAR TRAY / TRUNK LINING") is None, f"{name!r} (speaker grille) is no Front Grille")
        check(to_part(name, "FRONT GRILLE") is None, f"{name!r} is none even on a grille diagram")
    check(to_part("BASE, FR. GRILLE", "FRONT GRILLE ('08)") == "Front Grille", "the front grille base is the Front Grille")
    for name in ("WHEELHOUSE COMP., R. RR.", "WHEELHOUSE COMP., L. FR.", "WHEELHOUSE", "Wheelhouse cover"):
        check(to_part(name, "FLOOR / INNER PANELS") is None, f"{name!r} is a body panel, no wheel")
    for name in ("PLATE, TIRE PRESSURE CAUTION", "PLATE, TIRE PRESSURE CAUT", "PLATE, SPARE TIRE CAUTION (T-TYPE)",
                 "LABEL, TIRE PRESSURE", "Tyre pressure label", "TIRE CAUTION PLATE", "STICKER, TYRE"):
        check(to_part(name, "EMBLEMS / CAUTION LABELS") is None, f"{name!r} is a label, no Tyre")
    check(to_part("TIRE (195/65R15 91H) (DUNLOP)", "TIRE / WHEEL DISKS") == "Tyre (side undetermined)", "a real tyre row is")
    check(to_part("GUARD, FR. SPLASH (14\")", "FRONT BRAKE (1)") is None, "the brake's splash guard is no arch liner")
    check(to_part("GUARD, R. FR. SPLASH", "FRONT FENDERS") == "Right Front Wheel Arch Liner", "the fender's splash guard is")
    check(to_part("LIGHT ASSY., L. RR. FOG &", "TAILLIGHT") is None, "a rear fog lamp is not the (front) Fog Lamp")
    check(to_part("LIGHT ASSY., LICENSE", "LIGHT ASSY., LICENSE") is None, "the licence light is no part of ours")
    check(to_part("PROTECTOR COMP., R. FR. DOOR *NH578*", "MOLDING") is None, "a door protector moulding is none")
    check(to_part("DRIVESHAFT ASSY., R.", "FRONT DRIVESHAFT (AT)") is None, "no canonical part: None")


def ends_not_from_the_diagram() -> None:
    civic = "BUMPERS ('08)"
    check(to_part("FACE, FR. BUMPER", civic) == "Front Bumper", "FR. on the mixed diagram: front")
    check(to_part("FACE, RR. BUMPER", civic) == "Rear Bumper", "RR. on the mixed diagram: rear")
    check(to_part("FACE, FR. BUMPER", "REAR BUMPER") == "Front Bumper", "FR. on a rear diagram is still front")
    check(to_part("FACE, RR. BUMPER", "FRONT GRILLE") == "Rear Bumper", "RR. on a front diagram is still rear")
    check(to_part("BEAM COMP., FR. BUMPER", civic) == "Front Bumper Beam", "front beam")
    check(to_part("BEAM COMP., RR. BUMPER UPPER", civic) == "Rear Bumper Beam", "rear beam")
    check(to_part("BASE, FR. LICENSE PLATE", civic) == "Front Number Plate", "the front plate base")
    check(to_part("FACE, BUMPER", civic) is None, "a bumper face with no end in the name: None, not a guess")
    check(to_part("COVER, RR. BUMPER LOWER", civic) is None, "a lower cover is not the bumper")
    check(to_part("SPOILER, FR. BUMPER AIR", civic) is None, "an air spoiler is not the bumper")
    check(to_part("Trim panel bumper centre rear", "Trim panel, rear") == "Rear Bumper", "the BMW bumper cover")


def sides() -> None:
    check(to_part("LAMP UNIT, R. TAIL", "TAILLIGHT") == "Right Tail Lamp", "R. -> Right")
    check(to_part("LAMP UNIT, L. TAIL", "TAILLIGHT") == "Left Tail Lamp", "L. -> Left")
    check(to_part("LAMP UNIT, L. LID", "TAILLIGHT") == "Left Boot Lid Lamp", "a lid lamp is the boot lid's")
    check(to_part("HEADLIGHT UNIT, R.", "HEADLIGHT") == "Right Headlamp", "headlight unit R.")
    check(to_part("HEADLIGHT UNIT", "HEADLIGHT") == "Headlamp (side undetermined)", "no side: undetermined")
    check(to_part("HEADLIGHT UNIT", "HEADLIGHT", side="left") == "Left Headlamp", "the caller's side fills a gap")
    check(to_part("HEADLIGHT UNIT, R.", "HEADLIGHT", side="left") == "Right Headlamp", "the name's side wins")
    check(to_part("HEADLIGHT UNIT (LH)", "HEADLIGHT") == "Left Headlamp", "(LH)")
    check(to_part("PANEL R, FR FENDER", "FRONT FENDERS") == "Right Front Fender", "cut name: PANEL R, FR FENDER")
    check(to_part("PANEL L, FR FENDER", "FRONT FENDERS") == "Left Front Fender", "cut name: PANEL L, FR FENDER")
    check(to_part("PANEL SET R, RR OU", "OUTER PANELS / REAR PANEL") == "Right Rear Quarter Panel", "cut: PANEL SET R, RR OU")
    check(to_part("PANEL SET L, RR OU", "OUTER PANELS / REAR PANEL") == "Left Rear Quarter Panel", "cut: PANEL SET L, RR OU")
    check(to_part("PANEL R, SIDE SILL", "OUTER PANELS / REAR PANEL") == "Right Sill / Rocker Panel", "cut: sill R,")
    check(to_part("PANEL COMP., RR.", "OUTER PANELS / REAR PANEL") == "Rear End Panel", "RR. alone is rear, not right")
    check(to_part("PANEL COMP., RR.", "REAR DOOR PANELS") is None, "PANEL COMP., RR. off a rear-panel diagram: None")
    check(to_part("PANEL COMP., R. FR. DOOR", "FRONT DOOR PANELS (2D)") == "Right Front Door", "R. FR. DOOR")
    check(to_part("PANEL COMP., L. RR. DOOR", "REAR DOOR PANELS") == "Left Rear Door", "L. RR. DOOR")
    check(to_part("PANEL COMP., FR. DOOR", "FRONT DOOR PANELS (2D)") == "Door (side undetermined)", "no side: undetermined")
    check(to_part("REGULATOR ASSY., R. FR. D", "FRONT DOOR GLASS / DOOR REGULATOR") == "Right Front Window Regulator",
          "a cut name's end")
    check(to_part("DISK, ALUMINIUM WHEEL (15X6J) (ENKEI)", "TIRE / WHEEL DISKS") == "Wheel (side undetermined)",
          "a wheel with no end: undetermined, even when a side is given")
    check(to_part("DISK, ALUMINIUM WHEEL (15X6J)", "TIRE / WHEEL DISKS", side="right") == "Wheel (side undetermined)",
          "a wheel's side alone can't place it")
    check(to_family("PANEL COMP., R. RR. DOOR", "REAR DOOR PANELS") == ("Rear Door", "Right"), "to_family: family and side")
    check(to_family("BOLT, FLANGE, 6X12") is None, "to_family: a bolt is none")
    check(to_part("LAMP UNIT, R. TAIL", side="nonsense") == "Right Tail Lamp", "an unknown side word is ignored")
    for part in ("Right Tail Lamp", "Left Rear Door Glass", "Rear Bumper", "Tail Lamp (side undetermined)"):
        check(part in CANONICAL_PARTS, f"{part!r} is canonical")


def not_a_part() -> None:
    for name in ("BOLT, FLANGE, 6X12", "NUT, WHEEL (FUSE RASHI)", "CLIP, FR. BUMPER", "CLIP, UNDER COVER", "SCREW, TAPPING, 5X12",
                 "WASHER, SPRING, 10MM", "GROMMET, RR. COMBINATION LIGHT", "RIVET, 4.8MM", "PIN, SPLIT, 3.0X22",
                 "BULB, W5W", "SOCKET COMP. (T10)", "GASKET, BASE", "LABEL, FUEL FILLER CAUTION", "EMBLEM, RR.(HONDA)",
                 "SEAL, R. FR. DOOR LOWER", "SPACER, R. FR. BUMPER SIDE", "STAY, R. FR. FENDER", "TAPE, R. RR. DOOR SASH",
                 "BOLT, WHEEL (THAI MEIRA)", "CAP, ALUMINIUM WHEEL CENTER", "WEIGHT, BALANCE (10G) (TOHO)",
                 "Hex bolt", "C clip nut", "Fillister-head screw", "SNAP", "COVER", "", None, 42):
        check(to_part(name, "BUMPERS ('08)") is None, f"{name!r} -> None")
    check(to_part("BRACKET, R. RR. BUMPER BEAM", "FLOOR / INNER PANELS") is None, "a bumper beam's bracket is no beam")
    check(to_part("STIFFENER, RR. BUMPER FACE", "OUTER PANELS / REAR PANEL") is None, "a bumper face's stiffener is no bumper")
    check(to_part("PLATE, R. SIDE SILL RR. E", "FLOOR / INNER PANELS") is None, "a sill's plate is no sill")
    check(to_part("STOPPER, TRUNK LID", "TRUNK LID") is None, "a trunk lid stopper is no lid")
    check(to_part("CUSHION, TRUNK HINGE", "TRUNK LID") is None, "a hinge cushion is no hinge")


def ends() -> None:
    check(end_of("Rear Bumper") == "rear" and end_of("Front Bumper Beam") == "front", "end_of: named ends")
    check(end_of("Left Tail Lamp") == "rear" and end_of("Right Headlamp") == "front", "end_of: lamps")
    check(end_of("Boot Lid Lock") == "rear" and end_of("Bonnet") == "front", "end_of: boot, bonnet")
    check(end_of("Suspension - Front") == "front" and end_of("Suspension - Rear") == "rear", "end_of: suspension")
    check(end_of("Roof") is None and end_of("Left Front Door") == "front", "end_of: none for a roof; a door has its end")
    check(end_of("Tail Lamp (side undetermined)") == "rear" and end_of("Wheel (side undetermined)") is None,
          "end_of: undetermined forms")
    check(end_of(None) is None and end_of("") is None, "end_of: nothing")


def main() -> int:
    for name, fn in (
        ("gold file", gold), ("traps", traps), ("front/rear from the row", ends_not_from_the_diagram),
        ("sides", sides), ("not a part", not_a_part), ("ends", ends),
    ):
        print(f"\n{name}")
        try:
            fn()
        except Exception as exc:  # a crash is a failure, not a stop
            FAILURES.append(f"{name} crashed: {type(exc).__name__}: {exc}")
            print(f"  FAIL crashed: {type(exc).__name__}: {exc}")
    print(f"\ntest_oem_names: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for label in FAILURES:
        print(f"  FAIL {label}")
    if not FAILURES:
        print("\nALL OEM NAME CHECKS PASSED")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
