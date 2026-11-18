"""Contract 10 fresh-eyes review, the backend fixes
(plans/2026-11-17-linked-parts/review-c10.md) -- offline: Gemini is the fake
create(**settings), as in scripts/test_link_check.py; a scratch database and
storage folder.

  H1  an "impact" linked part never takes its side from
      vehicle_info.point_of_impact (the AI's own draft): with no firm sided
      part at that end it gets its "(side undetermined)" name -- the
      review's probe: a "Rear RH corner" point of impact, the only sided
      part on the left (and inferred, Low) -- in links.for_parent and in
      second_look._store; a firm sided part at that end still settles it;
  M2  a link table load_table can't survive never breaks contract 9:
      bad types anywhere (a list or an object as the id, junk trigger
      words, a list as the type...) -> None and a log line, never an
      exception; with the links module failing to import, _store still
      stores the zone suggestions -- off (the module not even imported),
      on and shadow;
  M3  the linked check: at most two requests a call (a 503: one short wait
      and one retry), each through the rate limiter; nothing sent after its
      30 s box; every other call's busy handling unchanged; Telegram's
      "about N min" a minute more while the linked check runs;
  L1  an added hidden part needs a strip-down only when the table flags
      one or it is in that bucket (a boot lid's lock: no);
  L2  an added linked part never takes its listed part's photo as its own
      (photo_reference, seen_in, bbox_2d); that photo stays as evidence by
      "link"; a sorter's tag or the linked check's box is its own photo;
  L4  the summed AI-accuracy figures carry links_table (a list), the key
      the dashboard reads; old rows still add up;
  L7  the wizard's autosave answers "linked" -- the new linked parts only,
      no decision -- beside second_look (kept for an older wizard), as the
      case page's POST /damage-items does: added to the newer decision's
      look it changes nothing, where its second_look would roll the
      decision back.

    python scripts/test_c10_review.py
    C10_TEST_ROOT=<a bot-service checkout> python scripts/test_c10_review.py
        (the same checks against another checkout, e.g. feat/c10-integration
        e0e0ce7, where they fail)

Makes, parts and plates are made up; the photos are coloured squares.
"""
import copy
import io
import json
import logging
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get("C10_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(Path(__file__).resolve().parent))  # auth_helpers
_tmp = tempfile.mkdtemp(prefix="c10-review-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaFAKEc10reviewKEY0000000000000000001"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
os.environ["SECOND_LOOK_ENABLED"] = "false"
for _name in ("INTAKE_LINKS", "INTAKE_LINK_CHECK", "GEMINI_LINK_CHAIN", "INTAKE_LINK_CHECK_MAX_CALLS",
              "INTAKE_LINKS_MAX", "INTAKE_LINKS_TYPES", "INTAKE_LINKS_SIDE", "INTAKE_LINKS_INTACT",
              "INTAKE_LINKS_UNKNOWN_HIT", "INTAKE_LINKS_CATALOGUE", "GEMINI_THINKING_LEVEL_LINK",
              "GEMINI_STORE_OFF", "INTAKE_CHECK_MODE", "CATALOGUE_ENABLED"):
    os.environ.pop(_name, None)

from PIL import Image  # noqa: E402

import app.ai.structured as structured  # noqa: E402

REQUESTS: list[dict] = []
ACQUIRED: list[float] = []
_lock = threading.Lock()
HANDLER = {"fn": None}


class Busy(Exception):
    status_code = 503

    def __init__(self):
        super().__init__("Error code: 503 - the model is currently experiencing high demand")


class FakeInteractions:
    def create(self, **kwargs):
        with _lock:
            REQUESTS.append(kwargs)
        out = HANDLER["fn"](kwargs)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(output_text=out if isinstance(out, str) else json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=7000, total_output_tokens=300))


def _acquire(key=None) -> float:
    with _lock:
        ACQUIRED.append(time.monotonic())
    return 0.0


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = _acquire
structured.OVERLOADED_RETRY_SECONDS = 0.2
structured.CHAIN_BUSY_RETRY_SECONDS = 0.2

from fastapi.testclient import TestClient  # noqa: E402

import app.intake as intake_pkg  # noqa: E402
from app.ai import allowance, link_check as lc, runs, second_look  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.api import main as api_main  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import links  # noqa: E402
from app.reports import accuracy, suggestions  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakeSession, Report  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
TABLE = links.TABLE
ON = links.Options(mode="on")


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
    REQUESTS.clear()
    ACQUIRED.clear()
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        allowance.clear()


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


class Logged(logging.Handler):
    """The records a logger wrote at WARNING or above, while installed."""

    def __init__(self, name: str):
        super().__init__(logging.WARNING)
        self.logger = logging.getLogger(name)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self.logger.addHandler(self)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self)


def item(part: str, *tokens, **more) -> dict:
    return {"part": part, "condition_tokens": list(tokens), "ai_confidence": "High", **more}


def sided(found: list[dict]) -> list[tuple[str, str | None]]:
    return [(it["part"], it.get("side_from")) for it in found if it["part"].startswith(("Left ", "Right "))]


def make_draft(rid: str, data: dict, ai_draft: dict | None = None, owner: str | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test", data=data,
                      owner_id=owner))
        db.add(IntakeSession(report_id=rid, stage="review", reporter_notes="",
                             ai_draft=ai_draft if ai_draft is not None else {"zone_suggestions": [],
                                                                             "zone_reads": ["overall"]}))
        db.commit()
    finally:
        db.close()


def stored(rid: str) -> tuple[dict, dict]:
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, rid)
        return copy.deepcopy(db.get(Report, rid).data), copy.deepcopy(session.ai_draft if session else None)
    finally:
        db.close()


def zone_item(part: str, confidence: str = "Medium") -> dict:
    return {"part": part, "source": "zone", "confidence": confidence, "condition_tokens": ["Cracked"],
            "found_by": ["zone:rear"]}


SKIPPED = {"status": "skipped", "note": "The second look is switched off.", "items": [], "seconds": 0.1}


# ---------------------------------------------------------------- H1


def impact_sides() -> None:
    bumper = item("Rear Bumper", "Crumpled", severity="Severe")
    # The only sided part at the rear: its side was inferred, and Low.
    lamp = item("Left Tail Lamp", "Cracked", side_inferred=True, ai_confidence="Low")
    for said in (None, "Rear RH corner", "Rear LH corner", "rear right"):
        case = {"damage_summary": [bumper, lamp], "vehicle_info": {"point_of_impact": said},
                "second_look": {"items": [], "dismissed": [], "decisions": []}}
        found = links.for_parent(bumper, case, [], TABLE, ON)
        names = [it["part"] for it in found]
        check(sided(found) == [] and "Rear Bumper Reflector (side undetermined)" in names,
              f"for_parent, point of impact {said!r}, the only sided part inferred: no side taken, "
              "the reflector undetermined", sided(found))

    rid = "h1-store"
    make_draft(rid, {"damage_summary": [bumper, lamp], "damaged_parts": ["Rear Bumper", "Left Tail Lamp"],
                     "vehicle_info": {"make": "Testmake", "model": "Testmodel", "point_of_impact": "Rear RH corner"}})
    with Settings(intake_links="on"):
        second_look._store(rid, dict(SKIPPED))
    items = [it for it in stored(rid)[0]["second_look"]["items"] if it.get("source") == "link"]
    check(items and sided(items) == [] and not any(it.get("side_from") == "point of impact" for it in items)
          and "Rear Bumper Reflector (side undetermined)" in [it["part"] for it in items],
          "_store, the review's probe (a \"Rear RH corner\" point of impact, a left lamp inferred): "
          "no Right part, none sided by the point of impact", sided(items))

    # A firm sided part at that end still settles an impact link (2 to 1),
    # whatever the point of impact says.
    reflector = item("Right Rear Bumper Reflector", "Cracked", ai_confidence="Medium")
    case = {"damage_summary": [bumper, reflector], "vehicle_info": {"point_of_impact": "Rear LH corner"}}
    found = links.for_parent(bumper, case, [], TABLE, ON)
    check(("Right Tail Lamp", "Right Rear Bumper Reflector") in sided(found)
          and not any(part.startswith("Left ") for part, _ in sided(found)),
          "a firm Right part listed at the rear: Right, side_from it -- never the point of impact's Left",
          sided(found))


# ---------------------------------------------------------------- M2


def _raw_table() -> dict:
    return json.loads(links.TABLE_PATH.read_text(encoding="utf-8"))


def _first(field, value):
    def change(t):
        t["links"][0][field] = value
        return t
    return change


BAD_TABLES = {
    "a list as an id": _first("id", ["not", "a", "string"]),
    "an object as an id": _first("id", {"a": 1}),
    "a number as an id": _first("id", 7),
    "a list among the trigger words": _first("trigger_tokens", [["Crumpled"]]),
    "an object among the trigger words": _first("trigger_tokens", [{"x": 1}]),
    "trigger words as an object": _first("trigger_tokens", {"x": 1}),
    "a list as the type": _first("type", ["attached"]),
    "an object as the area": _first("area", {"a": 1}),
    "a number as the parent": _first("parent", 7),
    "a list as the child": _first("child", ["Rear Bumper Beam"]),
    "a number as hidden": _first("hidden", 1),
    "a list as the reason": _first("reason", ["x"]),
    "an object as the sources": _first("sources", {"a": 1}),
    "a link that is a list": lambda t: {**t, "links": [[1, 2]] + t["links"][1:]},
    "a link that is null": lambda t: {**t, "links": [None] + t["links"][1:]},
    "a number as the version": lambda t: {**t, "version": 3},
    "the links as an object": lambda t: {**t, "links": {"a": 1}},
    "a list as the table": lambda t: [t],
    "null as the table": lambda t: None,
}


def bad_tables() -> None:
    for what, change in BAD_TABLES.items():
        table = change(_raw_table())
        path = Path(_tmp, "bad_table.json")
        path.write_text(json.dumps(table), encoding="utf-8")
        with Logged("app.intake.links") as log:
            try:
                got, raised = links.load_table(path), None
            except Exception as exc:  # the bug: it raised at import
                got, raised = "raised", f"{type(exc).__name__}: {exc}"
        try:
            errors = links.validate_table(table)
        except Exception as exc:
            errors = f"raised {type(exc).__name__}: {exc}"
        check(got is None and raised is None and log.records and isinstance(errors, list) and errors,
              f"{what}: no table, a log line, never an exception", raised or errors)
    check(links.load_table() is not None and links.validate_table(_raw_table()) == [],
          "the shipped table still loads and validates")


def _break_links_import():
    """The links module failing to import (as a table load_table raises on
    did at import): undo() puts it back."""
    held, attr = sys.modules.get("app.intake.links"), getattr(intake_pkg, "links", None)
    sys.modules["app.intake.links"] = None  # import -> ImportError
    if hasattr(intake_pkg, "links"):
        delattr(intake_pkg, "links")

    def undo():
        sys.modules["app.intake.links"] = held
        if attr is not None:
            intake_pkg.links = attr
    return undo


def store_survives() -> None:
    def draft(rid):
        make_draft(rid, {"damage_summary": [item("Rear Bumper", "Dented")], "damaged_parts": ["Rear Bumper"],
                         "vehicle_info": {}},
                   {"zone_suggestions": [zone_item("Left Tail Lamp")], "zone_reads": ["overall", "zone:rear"]})

    undo = _break_links_import()
    try:
        try:
            mode, raised = second_look._links_mode(), None
        except Exception as exc:
            mode, raised = None, type(exc).__name__
        check(mode == "off" and raised is None, "off: the links module is not even imported", raised)
        for mode in ("off", "on", "shadow"):
            rid = f"m2-{mode}"
            draft(rid)
            with Settings(intake_links=mode):
                try:
                    second_look._store(rid, dict(SKIPPED))
                    raised = None
                except Exception as exc:
                    raised = f"{type(exc).__name__}: {exc}"
            data, ai_draft = stored(rid)
            look = data.get("second_look") or {}
            check(raised is None and [it["part"] for it in look.get("items") or []] == ["Left Tail Lamp"]
                  and "link_suggestions" not in (ai_draft or {}),
                  f"{mode}: links failing to import, the zone suggestions are stored all the same", raised or look)
    finally:
        undo()

    # build failing (on, shadow): the same; shadow records it found none.
    real = second_look._with_links

    def broken(*args, **kwargs):
        raise TypeError("a link table build can't use")

    second_look._with_links = broken
    try:
        for mode in ("on", "shadow"):
            rid = f"m2-build-{mode}"
            draft(rid)
            with Settings(intake_links=mode):
                second_look._store(rid, dict(SKIPPED))
            data, ai_draft = stored(rid)
            check([it["part"] for it in (data.get("second_look") or {}).get("items") or []] == ["Left Tail Lamp"]
                  and (mode == "on" or ai_draft.get("link_suggestions") == []),
                  f"{mode}: the linked parts failing to build, the zone suggestions are stored", data.get("second_look"))
    finally:
        second_look._with_links = real


# ---------------------------------------------------------------- M3


def _group() -> lc.Group:
    path = Path(_tmp, "rear.jpg")
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 80, 40)).save(buf, "JPEG", quality=80)
    path.write_bytes(buf.getvalue())
    return lc.Group(area="rear", skill="rear",
                    candidates=[lc.Candidate(ref="C1", key="Rear Bumper Beam", line="- C1: Rear Bumper Beam")],
                    parent_lines=["- Rear Bumper: crush"],
                    photos=[lc.Photo(photo_id="ph1", label="P01", path=str(path), raw=False)])


ANSWER = {"answers": [{"id": "C1", "cue": "crush", "verdict": "likely", "photo": "P01", "box_2d": None,
                       "reason": "the cover is pushed in deep"}]}


def _ask() -> tuple[object, float]:
    started = time.monotonic()
    try:
        with Settings(gemini_link_chain="lite-x,lite-y"):
            got = lc.ask(_group(), AiActor.cli(), runs.Attempts())
    except Exception as exc:
        got = exc
    return got, time.monotonic() - started


def link_busy() -> None:
    check(lc.TIME_BOX == 30.0 and getattr(lc, "MAX_TRIES", None) == 2, "a 30 s box and two tries a call",
          (lc.TIME_BOX, getattr(lc, "MAX_TRIES", None)))

    REQUESTS.clear()
    ACQUIRED.clear()
    HANDLER["fn"] = lambda kwargs: Busy()
    got, took = _ask()
    check(isinstance(got, Exception) and [r["model"] for r in REQUESTS] == ["lite-x", "lite-x"],
          "busy throughout: one try, one retry -- two requests in all, then it gives up",
          [r["model"] for r in REQUESTS])
    # As every call's: a model's first try takes a slot of the shared rate
    # limiter; its retry on the same model waits the short busy pause.
    check(len(ACQUIRED) == 1, "through the shared rate limiter", len(ACQUIRED))
    check(0.2 <= took < 1.5, "one short wait between them, no second round", round(took, 2))
    check(all(r["timeout"] <= lc.TIME_BOX for r in REQUESTS), "no try's timeout runs past the box",
          [r["timeout"] for r in REQUESTS])

    REQUESTS.clear()
    tries = iter([Busy()])
    HANDLER["fn"] = lambda kwargs: next(tries, None) or ANSWER
    got, _ = _ask()
    check(isinstance(got, dict) and got.get("Rear Bumper Beam", {}).get("link_check", {}).get("verdict") == "likely"
          and len(REQUESTS) == 2, "busy once: the retry answers", got)

    REQUESTS.clear()
    ACQUIRED.clear()
    old = structured.OVERLOADED_RETRY_SECONDS
    structured.OVERLOADED_RETRY_SECONDS = 1.0
    answers = iter(["{not json", Busy()])
    HANDLER["fn"] = lambda kwargs: next(answers, Busy())
    try:
        got, took = _ask()
    finally:
        structured.OVERLOADED_RETRY_SECONDS = old
    check(isinstance(got, Exception) and [r["model"] for r in REQUESTS] == ["lite-x", "lite-y"] and took < 0.9
          and len(ACQUIRED) == 2,
          "bad JSON, then the next model busy: two requests, each model's through the limiter, no wait for a third",
          ([r["model"] for r in REQUESTS], took, len(ACQUIRED)))

    # The box: a slow busy answer, given up on -- its retry is never sent.
    REQUESTS.clear()
    old_box = lc.TIME_BOX
    lc.TIME_BOX = 0.5

    def slow_busy(kwargs):
        time.sleep(0.7)
        return Busy()

    HANDLER["fn"] = slow_busy
    try:
        got, took = _ask()
        time.sleep(1.2)  # the given-up thread's 503 lands at 0.7 s; a retry would go at 0.9 s
    finally:
        lc.TIME_BOX = old_box
    check(isinstance(got, TimeoutError) and took < 0.7 and len(REQUESTS) == 1,
          "given up after its box: nothing more sent in the background", (type(got).__name__, len(REQUESTS)))

    # Every other call: as before -- each model twice, then the chain once more.
    REQUESTS.clear()
    HANDLER["fn"] = lambda kwargs: Busy()
    try:
        structured.call_structured(purpose="draft", system_instruction="x", input_parts=[{"type": "text", "text": "x"}],
                                   schema={"type": "object"}, parse=json.loads, models=["m1", "m2"],
                                   actor=AiActor.cli())
    except Exception:
        pass
    check([r["model"] for r in REQUESTS] == ["m1", "m1", "m2", "m2"] * 2 and all(r["timeout"] == 45.0 for r in REQUESTS),
          "another call's busy handling unchanged (8 requests, its own timeout)", [r["model"] for r in REQUESTS])

    # Telegram's "about N min" counts the linked check when it runs.
    from app.channels import telegram

    plain = telegram._detection_minutes(3)
    with Settings(intake_links="on", intake_link_check="lite"):
        checking = telegram._detection_minutes(3)
    with Settings(intake_links="off", intake_link_check="lite"):
        links_off = telegram._detection_minutes(3)
    check(checking == plain + 1 and links_off == plain, "Telegram's estimate: a minute more while the linked check runs",
          (plain, checking, links_off))


# ---------------------------------------------------------------- L1, L2


def added_links() -> None:
    lid = item("Boot Lid", "Bent", severity="Severe", photo_reference="@ph1",
               evidence=[{"photo": "@ph1", "box": [100, 100, 400, 400], "box_kind": "ai", "by": "overall"}])
    found = links.build([lid], [], [], {}, TABLE, ON)
    by_part = {it["part"]: it for it in found}
    lock = by_part.get("Boot Lid Lock")
    check(lock is not None and lock["hidden"] and not lock["needs_strip_down"] and lock["bucket"] == "likely",
          "the table: Boot Lid Lock hidden, no strip-down, likely", lock and {k: lock[k] for k in ("hidden", "bucket")})
    added = {it["part"]: suggestions.item_from_suggestion(it) for it in found}
    wrong = [p for p, a in added.items()
             if bool(a.get("needs_strip_down")) != (found[[f["part"] for f in found].index(p)]["needs_strip_down"]
                                                    or by_part[p]["bucket"] == "strip_down")]
    check(not wrong and "needs_strip_down" not in added["Boot Lid Lock"] and added["Boot Lid Lock"].get("hidden"),
          "added: \"Confirm at strip-down\" only where the table flags it or its bucket is strip-down (L1)", wrong)
    check(all(a.get("photo_reference") is None and a.get("bbox_2d") is None and not a.get("seen_in")
              for a in added.values()),
          "added: none takes the Boot Lid's photo as its own (L2)",
          {p: a.get("photo_reference") for p, a in added.items() if a.get("photo_reference")})
    check(all(("@ph1", "link") in [(e["photo"], e["by"]) for e in a["evidence"]] for a in added.values()),
          "the parent's photo stays, as evidence by link")

    seen_photo = SimpleNamespace(id="ph2", part="Boot Lid Lock", area="rear", kind="damage", shows_damage=True,
                                 excluded=False, quality="good", damage_box=[10, 10, 200, 200])
    seen = {it["part"]: it for it in links.build([lid], [], [seen_photo], {}, TABLE, ON)}["Boot Lid Lock"]
    got = suggestions.item_from_suggestion(seen)
    check(seen["bucket"] == "seen" and got["photo_reference"] == "@ph2" and got["seen_in"] == ["@ph2"]
          and got.get("bbox_2d") is None, "a sorter's tag of it is its own photo (no AI box)", got.get("photo_reference"))
    checked = {**lock, "evidence": [{"photo": "@ph1", "by": "link", "box": None, "box_kind": None},
                                    {"photo": "@ph3", "by": "link_check", "box": [10, 20, 300, 400], "box_kind": "ai"}]}
    got = suggestions.item_from_suggestion(checked)
    check(got["photo_reference"] == "@ph3" and got["bbox_2d"] == [10, 20, 300, 400],
          "the linked check's photo and box are its own", (got.get("photo_reference"), got.get("bbox_2d")))
    zone = suggestions.item_from_suggestion({
        "key": "Left Tail Lamp", "part": "Left Tail Lamp", "source": "zone", "photo_reference": "@ph4",
        "evidence": [{"photo": "@ph4", "box": [1, 2, 3, 4], "box_kind": "ai", "by": "zone:rear"}]})
    check(zone["photo_reference"] == "@ph4" and zone["bbox_2d"] == [1, 2, 3, 4] and "needs_strip_down" not in zone,
          "a zone suggestion added as before")

    user = SimpleNamespace(id="u1", name="Tester", username="tester", email=None, role="admin")
    data = {"damage_summary": [lid], "damaged_parts": ["Boot Lid"], "vehicle_info": {},
            "second_look": {"status": "ok", "items": found, "dismissed": [], "decisions": []}}
    done = suggestions.decide(data, "Boot Lid Lock", "add", True, user, where="intake")
    check(done.item and done.item.get("photo_reference") is None and "needs_strip_down" not in done.item,
          "decide's add: the same item on the list", done.item)


# ---------------------------------------------------------------- L4


def accuracy_sums() -> None:
    v2 = {"suggestions": {"shown": 1, "added": 1, "dismissed": 0, "left": 0, "looked_first": 0,
                          "by_source": {"zone": {"shown": 1, "added": 1, "dismissed": 0, "left": 0}}}}

    def v3(table):
        return {"suggestions": {"shown": 1, "added": 1, "dismissed": 0, "left": 0, "looked_first": 0,
                                "by_source": {"link": {"shown": 1, "added": 1, "dismissed": 0, "left": 0}},
                                "by_rule": {"L1:rear-bumper>beam": {"shown": 1, "added": 1, "dismissed": 0, "left": 0}},
                                "links_table": table}}

    summed = accuracy.sum_suggestions([v2, v3("L1"), v3("L2"), v3("L1"), v3(None), v3(["L3", 5]), {"damage": {}}])
    check(summed.get("links_table") == ["L1", "L2", "L3"] and "links_tables" not in summed,
          "summed: links_table, the versions behind the counts", {k: summed.get(k) for k in ("links_table", "links_tables")})
    check(summed["shown"] == 6 and summed["by_source"]["link"]["shown"] == 5 and summed["by_source"]["zone"]["shown"] == 1,
          "old rows and new still add up", summed["by_source"])
    check(accuracy.sum_suggestions([v2])["links_table"] == [], "VERSION 2 rows only: no table")


# ---------------------------------------------------------------- L7


def autosave_answer() -> None:
    client = TestClient(api_main.app)
    owner = sign_in(client)
    bumper = item("Rear Bumper", "Dented", photo_reference=None)
    rid = "l7-draft"
    base = {"description": "", "category": [], "damage_summary": [bumper], "damaged_parts": ["Rear Bumper"],
            "vehicle_info": {"make": "Testmake", "model": "Testmodel"},
            "second_look": {"status": "skipped", "items": [], "dismissed": [], "decisions": [], "reads": ["overall"]}}
    make_draft(rid, copy.deepcopy(base), owner=owner)
    with Settings(intake_links="on"):
        second_look._store(rid, dict(SKIPPED))
        data = stored(rid)[0]
        had = {it["key"] for it in data["second_look"]["items"]}
        draft = copy.deepcopy(data)
        draft["damage_summary"].append({"part": "Boot Lid", "condition": "Bent", "condition_tokens": ["Bent"],
                                        "human_verified": True})
        r = client.put(f"/intake/{rid}/data", json={"data": draft})
        saved = r.json()
        after = stored(rid)[0]
        linked = saved.get("linked")
        check(r.status_code == 200 and isinstance(linked, list) and linked
              and all("Boot Lid" in it.get("linked_from", []) and it["key"] not in had for it in linked)
              and {it["key"] for it in linked} == {it["key"] for it in after["second_look"]["items"]} - had,
              "autosave with a part added by hand: \"linked\" is its new linked parts, and only those",
              {k: v for k, v in saved.items() if k != "second_look"})
        check(saved.get("second_look") == after["second_look"] and not any("decisions" in it for it in linked),
              "second_look as stored, kept for an older wizard; linked carries no decision")

        # A decision right after it; its answer lands first, the autosave's after.
        key = linked[0]["key"]
        r = client.post(f"/intake/{rid}/suggestions", json={"key": key, "action": "dismiss", "looked": False})
        decided = r.json()["second_look"]
        merged = copy.deepcopy(decided)
        keys = {it["key"] for it in merged["items"]}
        merged["items"] += [it for it in linked if it["key"] not in keys]
        check(r.status_code == 200 and merged == decided and key in decided["dismissed"]
              and stored(rid)[0]["second_look"]["dismissed"] == [key],
              "the late autosave answer's linked parts added to the decision's look: nothing rolled back",
              decided["dismissed"])
        check(saved["second_look"]["dismissed"] == [], "(whereas its second_look would have put the dismissal back)")

        r = client.put(f"/intake/{rid}/data", json={"data": draft})
        check(r.json() == {"ok": True}, "the same save again: nothing new, nothing answered", r.json())

    rid = "l7-off"
    make_draft(rid, copy.deepcopy(base), owner=owner)
    draft = copy.deepcopy(base)
    draft["damage_summary"].append({"part": "Boot Lid", "condition": "Bent", "condition_tokens": ["Bent"]})
    r = client.put(f"/intake/{rid}/data", json={"data": draft})
    check(r.json() == {"ok": True}, "off: the autosave answers as before", r.json())

    # The case page's POST /damage-items answers the same way.
    rid = "l7-case"
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="confirmed", channel="dashboard", reporter_chat_id="test", owner_id=owner,
                      data=copy.deepcopy(base)))
        db.commit()
    finally:
        db.close()
    with Settings(intake_links="on"):
        r = client.post(f"/reports/{rid}/damage-items", json={"part": "Boot Lid", "condition": "Bent"})
    answer = r.json()
    check(r.status_code == 200 and answer.get("linked")
          and [it["key"] for it in answer["linked"]] == [it["key"] for it in answer["second_look"]["items"]],
          "the case page: \"linked\" beside second_look", {k: v for k, v in answer.items() if k == "linked"} or answer)


def main() -> int:
    init_db()
    check(TABLE is not None, "the link table loads")
    for name, fn in (
        ("H1: an impact link never takes the point of impact's side", impact_sides),
        ("M2: a bad link table never raises", bad_tables),
        ("M2: _store stores the zone suggestions whatever the links do", store_survives),
        ("M3: the linked check's busy handling: one retry, then unchanged", link_busy),
        ("L1 / L2: a linked part added", added_links),
        ("L4: the summed accuracy figures' links_table", accuracy_sums),
        ("L7: the autosave answers the new linked parts on their own", autosave_answer),
    ):
        run_section(name, fn)
    print(f"\ntest_c10_review: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
