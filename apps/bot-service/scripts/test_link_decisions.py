"""The linked parts beyond the engine (contract 10, plans/2026-11-17-linked-
parts/contract10.md) -- offline, no Gemini, a scratch database.

Covers:
  - second_look._store: intake_links "off" stores the second look and the
    AI draft byte for byte as feat/c10-base did (a golden taken there);
    "shadow" the same second look, the linked parts on the AI draft only;
    "on" the linked parts after the open suggestions, outside
    MAX_SUGGESTIONS, a zone suggestion's twin joined to it, listed and
    dismissed ones left out, the damage list untouched; a re-read keeps a
    decided linked part; no table -> as off;
  - the re-check (check.plan_asks): hidden linked parts neither asked nor
    counted, the other suggestions asked first;
  - add / dismiss / undo (suggestions.decide): the link fields copied, a
    hidden part needs_strip_down, the report's condition left empty; the
    re-link on add (an added Rear End Panel hit hard unlocks its links, a
    grazed one doesn't; none while links are off or in shadow); an undo
    takes back the links its add brought, not the decided ones;
  - filing: the AI draft's linked parts and intact_in relabelled;
  - AI accuracy VERSION 3: by source "link", link type, bucket, hit, rule,
    the linked check's verdict, the table version, shadow mode's
    predictions; a case without linked parts keeps contract 9's shape; a
    VERSION 2 row still sums; names only.

    python scripts/test_link_decisions.py
    python scripts/test_link_decisions.py --golden   (prints the "off" golden;
        LINKS_TEST_ROOT=<a bot-service checkout> takes it there)

Parts and photos are made up.
"""
import copy
import hashlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get("LINKS_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="link-decisions-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-link-decisions-key"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
for name in ("INTAKE_LINKS", "INTAKE_LINKS_MAX", "INTAKE_LINKS_TYPES", "INTAKE_LINKS_SIDE", "INTAKE_LINKS_INTACT",
             "INTAKE_LINKS_UNKNOWN_HIT", "INTAKE_LINKS_CATALOGUE", "SECOND_LOOK_ENABLED"):
    os.environ.pop(name, None)

import app.ai.structured as structured  # noqa: E402

CLIENTS: list = []


def _no_client(key=None):
    CLIENTS.append(key)
    raise AssertionError("no Gemini call here")


structured.get_client = _no_client

from app.ai import second_look  # noqa: E402
from app.config import settings  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession, Report  # noqa: E402

# second_look._store with intake_links "off" on the case below, taken on
# feat/c10-base (56007d3, before the linked parts): sha256 of the stored
# second look and of the AI draft (python scripts/test_link_decisions.py
# --golden with LINKS_TEST_ROOT at that checkout).
GOLDEN_OFF = {
    "second_look": "237125af11eb22fc619dedcd225e38331fce2c9aef6f93dd2716274183f885fd",
    "ai_draft": "ffc8f4d29193f6e39677dac2cb126a90950e2b42c196b575a17b7617de4ab886",
}
USER = SimpleNamespace(id="u-sam", name="Sam Surveyor", username="sam")
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


class Settings:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: getattr(settings, k) for k in self.values}
        for k, v in self.values.items():
            setattr(settings, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(settings, k, v)


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- the case


def zone(rid: str, part: str, photo: int, confidence="Medium") -> dict:
    ref = f"@{rid}-p{photo}"
    return {"part": part, "condition_tokens": ["Cracked"], "photo_reference": ref, "ai_confidence": confidence,
            "confidence": confidence, "source": "zone", "found_by": ["zone:rear"], "seen_in": [ref],
            "evidence": [{"photo": ref, "box": [100, 100, 400, 400], "box_kind": "ai", "by": "zone:rear",
                          "reason": "lens split at the edge", "confidence": confidence}]}


def make_case(rid: str, *, extra_zone: int = 0) -> None:
    """A rear-end draft: a deformed rear bumper (Replace) and a buckled boot
    lid on the list; zone suggestions of a reflector, a tail lamp and the
    beam; a previous second look with two dismissals; sorted photos."""
    bumper_ref, lid_ref = f"@{rid}-p1", f"@{rid}-p2"
    listed = [
        {"part": "Rear Bumper", "condition_tokens": ["Deformed"], "condition": "Deformed", "remarks": "Replace",
         "severity": "Severe", "photo_reference": bumper_ref, "ai_confidence": "High",
         "evidence": [{"photo": bumper_ref, "box": [50, 50, 500, 900], "box_kind": "ai", "by": "overall"}]},
        {"part": "Boot Lid", "condition_tokens": ["Buckled"], "condition": "Buckled", "photo_reference": lid_ref,
         "ai_confidence": "High"},
    ]
    zones = [zone(rid, "Left Rear Bumper Reflector", 3, "High"), zone(rid, "Left Tail Lamp", 4),
             zone(rid, "Rear Bumper Beam", 5, "Low")]
    fillers = ["Left Rear Quarter Panel", "Right Rear Quarter Panel", "Rear Windscreen", "Left Boot Lid Lamp",
               "Right Boot Lid Lamp", "Rear Number Plate", "Left Boot Side Trim", "Right Boot Side Trim",
               "Left Rear Wheel Arch Trim", "Right Rear Wheel Arch Trim", "Left Rear Wheel Arch Liner",
               "Right Rear Wheel Arch Liner", "Rear Floor Panel", "Exhaust System", "Fuel Tank", "Roof"]
    zones += [zone(rid, part, 6) for part in fillers[:extra_zone]]
    data = {
        "description": "", "category": [], "damage_summary": listed, "damaged_parts": [it["part"] for it in listed],
        "vehicle_info": {"make": "Testmake", "model": "Testmodel", "point_of_impact": "Rear"},
        "second_look": {"status": "skipped", "note": "The second look is switched off.", "items": [],
                        "dismissed": ["Parking Sensor", "Boot Lid Hinge"], "reads": ["overall", "zone:rear"],
                        "decisions": [{"key": "Parking Sensor", "part": "Parking Sensor", "action": "dismissed",
                                       "at": "2026-11-17T01:00:00+00:00"},
                                      {"key": "Boot Lid Hinge", "part": "Boot Lid Hinge", "action": "dismissed",
                                       "at": "2026-11-17T01:01:00+00:00"}]},
    }
    ai_draft = {"damage_summary": copy.deepcopy(listed), "zone_suggestions": zones, "zone_reads": ["overall", "zone:rear"],
                "zone_intact": {"zone:rear": ["Rear Number Plate", "Right Tail Lamp"]}}
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test", data=data))
        db.add(IntakeSession(report_id=rid, stage="review", reporter_notes="", ai_draft=ai_draft))
        sorted_as = [("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
                     ("damage", "rear", "Boot Lid", True), ("damage", "rear", "Left Rear Bumper Reflector", True),
                     ("damage", "rear", "Left Tail Lamp", True), ("damage", "rear", "Reversing Camera", True),
                     ("damage", "rear", None, True)]
        for i, (kind, area, part, damage) in enumerate(sorted_as):
            db.add(IntakePhoto(id=f"{rid}-p{i}", report_id=rid, position=i, original_name=f"IMG_{i:02d}.jpg",
                               path=str(Path(_tmp, rid, f"IMG_{i:02d}.jpg")), sha256=f"{rid}{i}", kind=kind, area=area,
                               part=part, shows_damage=damage, quality="good"))
        db.commit()
    finally:
        db.close()


def stored(rid: str) -> tuple[dict, dict]:
    db = SessionLocal()
    try:
        return copy.deepcopy(db.get(Report, rid).data), copy.deepcopy(db.get(IntakeSession, rid).ai_draft)
    finally:
        db.close()


def store(rid: str, mode: str, **kw) -> tuple[dict, dict]:
    make_case(rid, **kw)
    with Settings(intake_links=mode):
        second_look._store(rid, {"status": "skipped", "note": "The second look is switched off.", "items": [],
                                 "seconds": 0.1})
    return stored(rid)


def golden() -> int:
    init_db()
    data, ai_draft = store("store-off", "off")
    print(json.dumps({"second_look": sha(data["second_look"]), "ai_draft": sha(ai_draft)}, indent=4))
    return 0


# ---------------------------------------------------------------- _store


def store_modes() -> None:
    from app.intake import links

    data, ai_draft = store("store-off", "off")
    check(sha(data["second_look"]) == GOLDEN_OFF["second_look"] and sha(ai_draft) == GOLDEN_OFF["ai_draft"],
          "off: the second look and the AI draft byte for byte as on feat/c10-base (golden)",
          (sha(data["second_look"]), sha(ai_draft)))
    check(not any(it.get("source") == "link" or "linked_from" in it for it in data["second_look"]["items"])
          and "link_suggestions" not in ai_draft, "off: no linked part anywhere")

    shadow, shadow_draft = store("store-shadow", "shadow")
    off_look = json.loads(json.dumps(data["second_look"]).replace("store-off", "store-shadow"))
    check(shadow["second_look"] == off_look, "shadow: the second look exactly as off")
    found = shadow_draft.get("link_suggestions")
    check(isinstance(found, list) and found and all(it["source"] == "link" for it in found)
          and "Rear Bumper Beam" not in [it["part"] for it in found],
          "shadow: the linked parts on the AI draft (the zone read's beam is not one of them)",
          [it.get("part") for it in found or []])
    check({k: v for k, v in shadow_draft.items() if k != "link_suggestions"}
          == json.loads(json.dumps(stored("store-off")[1]).replace("store-off", "store-shadow")),
          "shadow: the rest of the AI draft untouched")

    on, on_draft = store("store-on", "on")
    items = on["second_look"]["items"]
    kinds = [it.get("source") for it in items]
    first_link = kinds.index("link") if "link" in kinds else len(kinds)
    check("link" in kinds and all(k != "link" for k in kinds[:first_link]) and "link_suggestions" not in on_draft,
          "on: the linked parts after the zone suggestions, in second_look only", kinds)
    by_part = {it["part"]: it for it in items}
    beam = by_part.get("Rear Bumper Beam", {})
    check(beam.get("source") == "zone" and beam.get("linked_from") == ["Rear Bumper"]
          and beam.get("link_rule") == "L1:rear-bumper>beam",
          "a zone suggestion that is also linked: one item, the zone's, with the bumper joined", beam)
    reflector = by_part.get("Left Rear Bumper Reflector", {})
    check(reflector.get("source") == "zone" and reflector.get("linked_from") == ["Rear Bumper"]
          and "Rear Bumper Reflector (side undetermined)" not in by_part,
          "its undetermined twin from the bumper joins the zone's sided reflector")
    linked = [it for it in items if it.get("source") == "link"]
    check(not any(it["part"] in ("Parking Sensor", "Boot Lid Hinge", "Rear Bumper", "Boot Lid") for it in linked),
          "listed and dismissed parts are not suggested", [it["part"] for it in linked])
    plate = by_part.get("Rear Number Plate", {})
    check(plate.get("intact_in") == ["zone:rear"] and linked[-1]["part"] == "Rear Number Plate",
          "one a zone read called intact: demoted, with the read", [it["part"] for it in linked])
    check(all(it.get("name") and it.get("covers") and it.get("key") == it["part"] for it in linked),
          "named in the firm's words, with covers and key, as the other suggestions")
    camera = by_part.get("Reversing Camera", {})
    check(camera.get("bucket") == "seen" and camera.get("seen_in") == ["@store-on-p5"],
          "a part the sorter tagged on a damage photo: seen", camera)
    check(json.dumps(on["damage_summary"]) == json.dumps(data["damage_summary"]).replace("store-off", "store-on")
          and on["damaged_parts"] == data["damaged_parts"], "on: the damage list byte for byte as off")
    check(on["second_look"]["dismissed"] == data["second_look"]["dismissed"]
          and on["second_look"]["decisions"] == data["second_look"]["decisions"], "what people decided: untouched")

    many, _ = store("store-many", "on", extra_zone=16)
    look = many["second_look"]["items"]
    open_zone = [it for it in look if it.get("source") == "zone"]
    open_link = [it for it in look if it.get("source") == "link"]
    check(len(open_zone) == second_look.MAX_SUGGESTIONS and 0 < len(open_link) <= settings.intake_links_max,
          "16 zone suggestions and the linked parts beside them: their own cap, outside MAX_SUGGESTIONS",
          (len(open_zone), len(open_link)))

    saved_table = links.TABLE
    links.TABLE = None
    try:
        none, _ = store("store-notable", "on")
    finally:
        links.TABLE = saved_table
    check(not any(it.get("source") == "link" for it in none["second_look"]["items"]), "no table: as off")

    # A re-read after a person dismissed one linked part and added another.
    from app.reports import suggestions

    data, _ = stored("store-on")
    done = suggestions.decide(data, "Reversing Camera", "dismiss", False, USER, "intake")
    done = suggestions.decide(done.data, "Boot Floor Board", "add", True, USER, "intake")
    db = SessionLocal()
    try:
        db.get(Report, "store-on").data = done.data
        db.commit()
    finally:
        db.close()
    with Settings(intake_links="on"):
        second_look._store("store-on", {"status": "skipped", "note": None, "items": []})
    again = stored("store-on")[0]["second_look"]
    keys = [it["key"] for it in again["items"]]
    check(keys.count("Reversing Camera") == 1 and keys.count("Boot Floor Board") == 1
          and keys.index("Reversing Camera") > keys.index(next(k for k in keys if k not in ("Reversing Camera", "Boot Floor Board"))),
          "a re-read keeps the decided linked parts (after the open ones), once each", keys)


# ---------------------------------------------------------------- the re-check


def recheck() -> None:
    from PIL import Image

    from app.ai import check as recheck_mod

    folder = Path(_tmp, "recheck")
    folder.mkdir()
    photos = []
    for i in range(3):
        path = folder / f"IMG_{i}.jpg"
        Image.new("RGB", (32, 24), (40 * i, 90, 120)).save(path, "JPEG")
        photos.append(SimpleNamespace(id=f"r{i}", path=str(path), kind="damage", area="rear", part=None,
                                      shows_damage=True, excluded=False, quality="good"))
    link = {"key": "Parking Sensor", "part": "Parking Sensor", "source": "link", "evidence": [{"photo": "@r0", "by": "link"}]}
    hidden = {"key": "Rear Bumper Beam", "part": "Rear Bumper Beam", "source": "link", "hidden": True,
              "evidence": [{"photo": "@r0", "by": "link"}]}
    zone_item = {"key": "Left Tail Lamp", "part": "Left Tail Lamp", "source": "zone", "photo_reference": "@r1"}
    plan = recheck_mod.plan_asks([link, hidden, zone_item], photos)
    check([a.key for a in plan.asks] == ["Left Tail Lamp", "Parking Sensor"],
          "the zone suggestion asked first, the visible linked part next, the hidden one not at all",
          [a.key for a in plan.asks])
    check(plan.total == 2 and "Rear Bumper Beam" not in plan.unasked, "a hidden linked part isn't counted either",
          (plan.total, plan.unasked))
    check(recheck_mod.is_hidden_link(hidden) and not recheck_mod.is_hidden_link({**zone_item, "hidden": True}),
          "only a linked part's hidden flag counts")
    many = [{"key": f"z{i}", "part": "Left Tail Lamp", "source": "zone", "photo_reference": "@r1"} for i in range(16)]
    full = recheck_mod.plan_asks([link] + many, photos)
    check(len(full.asks) == recheck_mod.MAX_QUESTIONS and "Parking Sensor" in full.unasked,
          "16 zone suggestions take the 16 questions before a linked part", full.unasked)


# ---------------------------------------------------------------- add / dismiss / undo


def decisions() -> None:
    from app.intake import links
    from app.reports import suggestions
    from app.reports.schema import SecondLook

    bumper = {"part": "Rear Bumper", "condition_tokens": ["Deformed"], "photo_reference": "@d1",
              "evidence": [{"photo": "@d1", "by": "overall"}]}
    found = [it for it in links.build([bumper], [], [], {}, links.TABLE, links.Options(mode="on"))
             if it["part"] != "Rear End Panel"]  # a zone read's suggestion below
    look = SecondLook.model_validate({"items": found + [
        {"key": "Rear End Panel", "part": "Rear End Panel", "source": "zone", "condition_tokens": ["Buckled"],
         "photo_reference": "@d4", "confidence": "Medium"},
        {"key": "Boot Lid Lock", "part": "Boot Lid Lock", "source": "zone", "condition_tokens": ["Jammed"],
         "confidence": "Low"}]}).model_dump()
    data = {"damage_summary": [bumper], "damaged_parts": ["Rear Bumper"], "second_look": look,
            "vehicle_info": {"make": "Testmake", "model": "Testmodel"}}

    with Settings(intake_links="on"):
        done = suggestions.decide(data, "Rear Bumper Beam", "add", True, USER, "intake")
    beam = done.item
    check(beam["part"] == "Rear Bumper Beam" and beam["linked_from"] == ["Rear Bumper"] and beam["link_type"] == "behind"
          and beam["hidden"] and beam["needs_strip_down"] and beam["added_from"] == "suggestion",
          "add a hidden linked part: linked_from, link_type, hidden, needs_strip_down copied", beam)
    check(beam.get("condition") is None and beam.get("remarks") is None and not beam.get("condition_tokens")
          and beam.get("bbox_2d") is None, "no condition, remarks or AI box made up (the report is unchanged)", beam)
    check(beam.get("photo_reference") is None and not beam.get("seen_in")
          and [(e["photo"], e["by"]) for e in beam["evidence"]] == [("@d1", "link")],
          "no photo of its own (review L2): the parent's stays as evidence by link only", beam)
    with Settings(intake_links="on"):
        sensor = suggestions.decide(done.data, "Parking Sensor", "add", False, USER, "intake").item
    check(sensor["link_type"] == "attached" and "hidden" not in sensor and "needs_strip_down" not in sensor,
          "a visible linked part: no strip-down (defaults left out of the dump)", sensor)
    lid = {"key": "Boot Lid Lock", "part": "Boot Lid Lock", "source": "link", "hidden": True, "linked_from": ["Boot Lid"],
           "link_type": "opens_with"}
    check("needs_strip_down" not in suggestions.item_from_suggestion(lid)
          and suggestions.item_from_suggestion(lid)["hidden"],
          "hidden alone is no strip-down (review L1): a boot lid's lock is seen once the lid opens")
    check(suggestions.item_from_suggestion({**lid, "bucket": "strip_down"})["needs_strip_down"]
          and suggestions.item_from_suggestion({**lid, "needs_strip_down": True})["needs_strip_down"],
          "the strip-down bucket, or the table's flag: needs_strip_down")
    plain = suggestions.item_from_suggestion({"key": "Left Tail Lamp", "part": "Left Tail Lamp", "source": "zone"})
    check(not any(k in plain for k in ("linked_from", "link_type", "hidden", "needs_strip_down")),
          "a zone suggestion added: no link field (dumps as before)")

    before = len(data["second_look"]["items"])
    with Settings(intake_links="on"):
        added = suggestions.decide(data, "Rear End Panel", "add", False, USER, "intake")
    new = [it for it in added.second_look["items"][before:]]
    check([it["part"] for it in new] == ["Boot Lid Weatherstrip", "Rear Floor Panel"]
          and all(it["linked_from"] == ["Rear End Panel"] and it["name"] and it["source"] == "link" for it in new),
          "an added Rear End Panel (buckled: heavy) brings its links -- the weatherstrip and floor panel; the lock "
          "is a suggestion already", [it["part"] for it in new])
    for mode in ("off", "shadow"):
        with Settings(intake_links=mode):
            quiet = suggestions.decide(data, "Rear End Panel", "add", False, USER, "intake")
        check(len(quiet.second_look["items"]) == before, f"links {mode}: no re-link on add")
    light = copy.deepcopy(data)
    light["second_look"]["items"][-2]["condition_tokens"] = ["Grazed"]
    with Settings(intake_links="on"):
        grazed = suggestions.decide(light, "Rear End Panel", "add", False, USER, "intake")
    check(len(grazed.second_look["items"]) == before, "a grazed Rear End Panel (light): no new links")

    with Settings(intake_links="on"):
        kept = suggestions.decide(added.data, "Rear Floor Panel", "dismiss", False, USER, "intake")
        undone = suggestions.decide(kept.data, "Rear End Panel", "undo", False, USER, "intake")
    parts_after = [it["part"] for it in undone.second_look["items"]]
    check(undone.listed == "removed" and "Rear Floor Panel" in parts_after,
          "undo of the add: a link a person decided on stays", parts_after)
    with Settings(intake_links="on"):
        undone2 = suggestions.decide(added.data, "Rear End Panel", "undo", False, USER, "intake")
    check(len(undone2.second_look["items"]) == before and "Rear Floor Panel" not in
          [it["part"] for it in undone2.second_look["items"]], "undo of the add: the links it brought go again")
    check(all(it.get("source") != "link" or it.get("linked_from") == ["Rear Bumper"]
              for it in undone2.second_look["items"]), "the read's own linked parts stay")
    with Settings(intake_links="on"):
        gone = suggestions.decide(data, "Reversing Camera", "dismiss", False, USER, "case")
        back = suggestions.decide(gone.data, "Reversing Camera", "undo", False, USER, "case")
    check("Reversing Camera" in gone.second_look["dismissed"] and "Reversing Camera" not in back.second_look["dismissed"]
          and len(back.second_look["items"]) == before, "dismiss and undo a linked part on the case page")


# ---------------------------------------------------------------- filing


def filing_relabel() -> None:
    from app.reports import filing, suggestions

    ai_draft = {"damage_summary": [{"part": "Rear Bumper", "photo_reference": "@a"}],
                "zone_suggestions": [{"part": "Left Tail Lamp", "seen_in": ["@b"]}],
                "link_suggestions": [{"part": "Rear Bumper Beam", "source": "link", "photo_reference": "@b",
                                      "seen_in": ["@b", "@gone"], "intact_in": ["zone:rear", "@a"],
                                      "evidence": [{"photo": "@a", "by": "link"}, {"photo": "@gone", "by": "sort"}]}]}
    index_of = {"a": 0, "b": 1}
    out = filing.relabel_ai_draft(ai_draft, index_of)
    link = out["link_suggestions"][0]
    check(link["photo_reference"] == "P02" and link["seen_in"] == ["P02"] and link["intact_in"] == ["zone:rear", "P01"]
          and [e["photo"] for e in link["evidence"]] == ["P01"], "the AI draft's linked parts as P-numbers", link)
    check(out["damage_summary"][0]["photo_reference"] == "P01" and out["zone_suggestions"][0]["seen_in"] == ["P02"],
          "the damage list and the zone suggestions as before")
    check(ai_draft["link_suggestions"][0]["photo_reference"] == "@b" and filing.relabel_ai_draft(None, index_of) is None,
          "a copy; None stays None")
    check(suggestions.relabel_item({"part": "x", "intact_in": ["zone:front"]}, {})["intact_in"] == ["zone:front"],
          "relabel_item keeps a zone read's name in intact_in")


# ---------------------------------------------------------------- AI accuracy v3


def accuracy_v3() -> None:
    from app.intake import links
    from app.reports import accuracy

    check(accuracy.VERSION == 3 and "link" in accuracy.SUGGESTION_SOURCES, "VERSION 3, source \"link\"")
    bumper = {"part": "Rear Bumper", "condition_tokens": ["Deformed"]}
    found = links.build([bumper], [], [], {}, links.TABLE, links.Options(mode="on"))
    for it in found:
        it["reason"] = "Sits behind the cover XYZ12345"
    found[0]["link_check"] = {"verdict": "seen", "cue": "cracked", "model": "m", "reason": "plate ABC1234 cracked"}
    beam = next(it for it in found if it["part"] == "Rear Bumper Beam")
    final = {"damage_summary": [bumper, {"part": "Rear Bumper Beam", "added_from": "suggestion",
                                         "linked_from": ["Rear Bumper"], "evidence": [{"photo": "P07", "by": "link"}]}],
             "second_look": {"items": found + [{"key": "Left Tail Lamp", "part": "Left Tail Lamp", "source": "zone"}],
                             "dismissed": ["Parking Sensor"],
                             "decisions": [{"key": "Rear Bumper Beam", "part": "Rear Bumper Beam", "action": "added"},
                                           {"key": "Parking Sensor", "part": "Parking Sensor", "action": "dismissed"}]}}
    s = accuracy.compare({"damage_summary": [bumper]}, final)["suggestions"]
    n = len(found)
    check(s["by_source"]["link"] == {"shown": n, "added": 1, "dismissed": 1, "left": n - 2}
          and s["by_source"]["zone"]["shown"] == 1, "by source: link", s["by_source"])
    check(s["by_link_type"]["behind"]["added"] == 1 and s["by_link_type"]["attached"]["dismissed"] == 1
          and sum(c["shown"] for c in s["by_link_type"].values()) == n, "by link type", s["by_link_type"])
    check(s["by_bucket"]["strip_down"]["added"] == 1 and s["by_bucket"]["likely"]["dismissed"] == 1, "by bucket")
    check(s["by_hit"] == {"heavy": {"shown": n, "added": 1, "dismissed": 1, "left": n - 2}}, "by hit", s["by_hit"])
    check(s["by_rule"][beam["link_rule"]]["added"] == 1 and len(s["by_rule"]) == n, "by rule")
    check(s["link_check"] == {"seen": {"shown": 1, "added": 0, "dismissed": 0, "left": 1}}
          or s["link_check"]["seen"]["shown"] == 1, "by the linked check's verdict", s["link_check"])
    check(s["links_table"] == "L1" and "shadow" not in s, "the table version; no shadow section without shadow mode")
    text = json.dumps(s)
    check("XYZ12345" not in text and "ABC1234" not in text and "P07" not in text and "Sits behind" not in text,
          "names only: no reason, photo or box in the row")

    plain = accuracy.compare({"damage_summary": [bumper]}, {"damage_summary": [bumper]})["suggestions"]
    check(set(plain) == {"shown", "added", "dismissed", "left", "looked_first", "by_source", "by_read", "by_check"},
          "a case without linked parts: contract 9's shape", sorted(plain))

    shadow_draft = {"damage_summary": [bumper], "link_suggestions": found + [found[0]]}
    final_shadow = {"damage_summary": [bumper, {"part": "Rear Bumper Beam"}, {"part": "Left Rear Bumper Reflector"}]}
    sh = accuracy.compare(shadow_draft, final_shadow)["suggestions"]
    check(sh["shadow"]["predicted"] == n and sh["shadow"]["on_final_list"] == 2
          and sh["shadow"]["by_rule"][beam["link_rule"]] == {"predicted": 1, "on_final_list": 1}
          and sh["links_table"] == "L1",
          "shadow: predicted against the signed-off list (an undetermined reflector by its sided name)", sh["shadow"])

    v2_row = {"suggestions": {"shown": 3, "added": 1, "dismissed": 1, "left": 1, "looked_first": 1,
                              "by_source": {"zone": {"shown": 3, "added": 1, "dismissed": 1, "left": 1}},
                              "by_read": {}, "by_check": {}}}
    summed = accuracy.sum_suggestions([v2_row, {"suggestions": s}, {"suggestions": sh}, {"damage": {}}])
    check(summed["shown"] == 3 + s["shown"] and summed["by_source"]["zone"]["shown"] == 4
          and summed["by_source"]["link"]["shown"] == n and summed["by_link_type"]["behind"]["added"] == 1
          and summed["links_table"] == ["L1"] and "links_tables" not in summed and summed["shadow"]["predicted"] == n
          and summed["link_check"]["seen"]["shown"] == 1,
          "sum: a VERSION 2 row adds its counts and nothing else; v3 rows their link groups", summed)
    empty = accuracy.sum_suggestions([v2_row])
    check(empty["by_link_type"] == {} and empty["shadow"] == {"predicted": 0, "on_final_list": 0, "by_rule": {}}
          and empty["links_table"] == [], "only VERSION 2 rows: the link groups empty, zero, no table")


def main() -> int:
    init_db()
    for name, fn in (
        ("second_look._store: off (golden), shadow, on", store_modes),
        ("the re-check skips hidden linked parts", recheck),
        ("add / dismiss / undo, the re-link on add", decisions),
        ("filing: the AI draft's linked parts relabelled", filing_relabel),
        ("AI accuracy VERSION 3", accuracy_v3),
    ):
        run_section(name, fn)
    check(not CLIENTS, "no Gemini client asked for")
    print(f"\ntest_link_decisions: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(golden() if "--golden" in sys.argv else main())
