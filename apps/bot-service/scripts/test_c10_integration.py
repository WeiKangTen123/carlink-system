"""Contract 10 end to end (plans/2026-11-17-linked-parts/contract10.md, agent
I) -- offline: Gemini is the fake create(**settings), as in
scripts/test_intake_flow.py, answering the sort, the main read, the zone
reads, the linked check and the re-check; a scratch database and storage
folder; no PDF render.

  1. on: a zone-mode read with the linked parts "on", the linked check
     "lite", the re-check "lite" and the catalogue's diagram neighbours ->
     the linked parts stored (nothing ticked, a zone twin joined, the
     catalogue's neighbour); the linked check fills verdicts and evidence;
     the re-check skips the hidden linked parts; a hidden part added on the
     wizard and on the case page needs strip-down, its chip ends when a
     person edits its condition; a part added by hand re-links (wizard
     autosave, case page POST /damage-items); filing relabels the AI
     draft's zone suggestions and the linked parts' photos; sign-off writes
     an AI-accuracy VERSION 3 row with the link groups;
  2. shadow: nothing shown, the linked parts (and the linked check's
     answer) on the AI draft only, relabelled at filing, counted at
     sign-off (suggestions.shadow);
  3. Telegram on, the heartbeat across two processes: this file started
     again as the bot's process (--bot-child) runs the website's pipeline
     on a draft the bridge made; while it reads, the api (this process,
     which has no run of it) shows the draft reading -- no Retry --, and
     refuses Retry, autosave, a decision and File case, and the sweep
     leaves it; it ends ready with the linked parts. A bot killed mid-run:
     once its heartbeat is stale the draft reads "stopped", and Retry runs
     it in the api;
  4. everything off: the requests and the stored data (the read, the AI
     draft, decisions, autosave, filing, the case page's damage items, the
     run log, the AI-accuracy metrics) byte for byte as master a89ede1's,
     in single mode and in contract 9's live zone mode -- goldens taken
     there. Two changes are contract 10's on purpose and are taken out
     before comparing (and checked on their own): ai_draft["zone_intact"]
     (Z, zone mode) and the accuracy version, row and metrics (2 -> 3).

    python scripts/test_c10_integration.py
    python scripts/test_c10_integration.py --golden   (prints section 4's goldens;
        C10_TEST_ROOT=<a bot-service checkout> takes them there, e.g. a
        git archive of master a89ede1)

Makes, plates and parts are made up; the photos are coloured rectangles.
"""
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

CHILD = "--bot-child" in sys.argv
GOLDEN_MODE = "--golden" in sys.argv
ROOT = Path(os.environ.get("C10_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
# The bot's process (section 3) shares the parent's database and storage.
_tmp = os.environ.get("C10_CHILD_TMP") or tempfile.mkdtemp(prefix="c10-integration-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaFAKEc10integrationKEY00000000000001"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
os.environ["PUBLIC_BASE_URL"] = "https://carlink.test/"
if not CHILD:
    for _name in ("SECOND_LOOK_ENABLED", "INTAKE_READ_MODE", "INTAKE_ZONE_ONLY", "INTAKE_ZONE_EVIDENCE",
                  "INTAKE_CHECK_MODE", "INTAKE_SORT_DAMAGE_BOX", "CATALOGUE_ENABLED", "TELEGRAM_FULL_DETECTION",
                  "INTAKE_LINKS", "INTAKE_LINKS_MAX", "INTAKE_LINKS_TYPES", "INTAKE_LINKS_SIDE", "INTAKE_LINKS_INTACT",
                  "INTAKE_LINKS_UNKNOWN_HIT", "INTAKE_LINKS_CATALOGUE", "INTAKE_LINK_CHECK", "GEMINI_LINK_CHAIN",
                  "INTAKE_LINK_CHECK_MAX_CALLS", "GEMINI_THINKING_LEVEL_LINK"):
        os.environ.pop(_name, None)
os.environ["SECOND_LOOK_ENABLED"] = "false"

from PIL import Image  # noqa: E402

import app.ai.structured as structured  # noqa: E402

# ------------------------------------------------------------- the fake Gemini

REQUESTS: list[dict] = []
_lock = threading.Lock()
# The bot's process (section 3) holds every call until this file exists.
GATE = {"file": os.environ.get("C10_GATE") or None}

# (kind, area, part, shows damage) per photo, P01 first.
CASE = [
    ("log_card", None, None, False), ("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
    ("damage", "rear", "Left Tail Lamp", True), ("damage", "rear", "Boot Lid", True), ("overview", "left", None, False),
    ("damage", "left", "Left Rear Door", True), ("damage", "rear", "Rear Bumper Reflector (side undetermined)", True),
    ("damage", "rear", None, True), ("third_party", None, None, True),
]


def kind_of(kwargs) -> str:
    props = kwargs["response_format"]["schema"].get("properties", {})
    if "photos" in props:
        return "sort"
    if "zone_side_seen" in props:
        return "zone"
    if "answers" in props:
        return "link"
    if "findings" in props:
        return "check"
    return "draft"


def _labels(parts) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


def _sort_answer(labels):
    out = []
    for label in labels:
        kind, area, part, damage = CASE[int(label[1:]) - 1]
        out.append({"label": label, "kind": kind, "angle": None, "area": area, "quality": "good",
                    "shows_damage": damage, "part": part, "note": f"photo {label}"})
    return {"batch_sides": None, "photos": out}


def _draft_answer(labels):
    def ref(label):
        return label if label in labels else (labels[0] if labels else None)
    return {
        "description": "The insured car was hit at the rear while parked.",
        "category": ["Vehicle Collision or Damage"],
        "damaged_parts": ["Rear Bumper", "Boot Lid", "Left Rear Door"],
        "damage_summary": [
            {"part": "Rear Bumper", "photo_reference": ref("P03"), "ai_confidence": "High",
             "condition_tokens": ["Cracked", "Crumpled"], "severity": "Severe", "remarks": "Replace"},
            {"part": "Boot Lid", "photo_reference": ref("P05"), "ai_confidence": "High", "condition_tokens": ["Dented"],
             "severity": "Moderate"},
            {"part": "Left Rear Door", "photo_reference": ref("P07"), "ai_confidence": "Medium",
             "condition_tokens": ["Dented"]},
        ],
        "vehicle_info": {"make": "Gammamotors", "model": "Vezel", "year": "2019", "plate_number": "SZZ1234Z"},
    }


def _zone_answer(kwargs, labels):
    if "the car's rear" not in kwargs["system_instruction"]:
        return {"zone_side_seen": "unclear", "damage": [], "checked_intact": []}
    schema = kwargs["response_format"]["schema"]["properties"]
    props = schema["damage"]["items"]["properties"]
    intact_names = schema["checked_intact"]["items"]["enum"]

    def item(label, part, confidence, tokens):
        out = {"photo_reference": label, "part": part, "ai_confidence": confidence, "condition_tokens": tokens}
        if "reason" in props:
            out["reason"] = f"The {part.lower()} looks cracked."
        if "box_2d" in props:
            out["box_2d"] = [120, 140, 520, 600]
        if "grid_cells" in props:
            out["grid_cells"] = []
        return out
    damage = []
    if "P03" in labels:
        damage.append(item("P03", "Rear Bumper", "High", ["Cracked"]))
    if "P04" in labels:
        damage.append(item("P04", "Left Tail Lamp", "Medium", ["Cracked"]))
    return {"zone_side_seen": "rear", "damage": damage,
            "checked_intact": [n for n in ("Parking Sensor", "Rear Windscreen") if n in intact_names]}


def _link_answer(kwargs, labels):
    text = kwargs["input"][0]["text"]
    answers = []
    for ref, part in re.findall(r"^- (C\d+): (.+?)(?: -- .*)?$", text, re.M):
        if part == "Rear Bumper Reflector (side undetermined)":
            photo = "P08" if "P08" in labels else (labels[0] if labels else None)
            answers.append({"id": ref, "cue": "cracked", "verdict": "seen", "photo": photo,
                            "box_2d": [200, 220, 480, 560], "reason": "The reflector is cracked at its corner."})
        elif part == "Rear Bumper Beam":
            answers.append({"id": ref, "cue": "crush", "verdict": "strip_down", "photo": None, "box_2d": None,
                            "reason": "The bumper is pushed in; the beam behind it may be bent."})
        elif part == "Rear Number Plate":
            answers.append({"id": ref, "cue": "none_seen", "verdict": "no", "photo": None, "box_2d": None,
                            "reason": "The plate sits straight."})
        else:
            answers.append({"id": ref, "cue": "cannot_see", "verdict": "cannot_tell", "photo": None, "box_2d": None,
                            "reason": "Not visible in these photos."})
    return {"answers": answers}


def _check_answer(kwargs, labels):
    refs = kwargs["response_format"]["schema"]["properties"]["findings"]["items"]["properties"]["ref"]["enum"]
    return {"findings": [{"ref": ref, "damaged": "unsure", "reason": "Hard to tell from these photos."} for ref in refs]}


ANSWERS = {"sort": lambda kw, labels: _sort_answer(labels), "draft": lambda kw, labels: _draft_answer(labels),
           "zone": _zone_answer, "link": _link_answer, "check": _check_answer}


class FakeInteractions:
    def create(self, **kwargs):
        gate = GATE["file"]
        while gate and not Path(gate).exists():
            time.sleep(0.05)
        with _lock:
            REQUESTS.append(kwargs)
        labels = _labels(kwargs["input"])
        out = ANSWERS[kind_of(kwargs)](kwargs, labels)
        return SimpleNamespace(output_text=json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=1000, total_output_tokens=100))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

from app.config import settings  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiAccuracy, AiRun, IntakeSession, Report, User  # noqa: E402

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
    REQUESTS.clear()
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


def jpeg(i: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (25 * i % 256, 110, (230 - 20 * i) % 256)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _fake_render(*_a, output_path=None, **_kw):
    path = output_path or (_a[2] if len(_a) > 2 else None)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


def _app():
    import app.api.intake as intake_api
    import app.api.main as main_api

    main_api.render_pdf = _fake_render
    intake_api.render_pdf = _fake_render
    return main_api.app


def new_draft(client, n: int = len(CASE)) -> tuple[str, list[str]]:
    rid = client.post("/intake").json()["id"]
    ids = []
    for i in range(n):
        r = client.post(f"/intake/{rid}/photos", files={"file": (f"IMG_{i:04d}.jpg", jpeg(i), "image/jpeg")},
                        data={"position": str(i)})
        assert r.status_code == 200 and "photo" in r.json(), r.text
        ids.append(r.json()["photo"]["id"])
    return rid, ids


def wait_done(client, rid: str, timeout: float = 90) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = client.get(f"/intake/{rid}").json()
        if state["stage"] in ("ready", "failed"):
            return state
        time.sleep(0.1)
    raise AssertionError("the reading timed out")


def read(client, rid: str, notes: str = "Hit at the rear while parked.") -> dict:
    state = client.get(f"/intake/{rid}").json()
    data = state["data"]
    data["vehicle_info"] = {**(data.get("vehicle_info") or {}), "plate_number": "SZZ1234Z"}
    r = client.put(f"/intake/{rid}/data", json={"data": data, "reporter_notes": notes})
    assert r.status_code == 200, r.text
    r = client.post(f"/intake/{rid}/process", json={"mode": "all"})
    assert r.status_code == 200, r.text
    return wait_done(client, rid)


def db_row(model, key):
    db = SessionLocal()
    try:
        row = db.get(model, key)
        if row is not None:
            db.expunge(row)
        return row
    finally:
        db.close()


def runs_of(rid: str) -> list[AiRun]:
    db = SessionLocal()
    try:
        rows = db.query(AiRun).filter(AiRun.report_id == rid).all()
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def sign_off(client, rid: str):
    return client.post(f"/reports/{rid}/sign-off", json={"terms_accepted": True, "open_items_ack": True})


def refs_in(obj) -> list[str]:
    """Every "@<photo id>" left anywhere in obj."""
    return re.findall(r'"@[^"]*"', json.dumps(obj))


def links_of(look: dict) -> list[dict]:
    return [it for it in (look or {}).get("items") or [] if it.get("source") == "link"]


def by_part(items: list[dict], part: str) -> dict | None:
    return next((it for it in items if it.get("part") == part), None)


# ------------------------------------------------------------ 1. links on


ON = dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason",
          intake_check_mode="lite", intake_links="on", intake_link_check="lite")


def links_on() -> None:
    from fastapi.testclient import TestClient

    from app.intake import links
    from app.parts import catalogue
    from auth_helpers import sign_in

    neighbours_calls = []

    def fake_neighbours(make, model, year, parts):
        neighbours_calls.append((make, model, year, list(parts)))
        return {"Rear Bumper": [
            {"part": "Rear Bumper Beam", "oem_name": "BEAM COMP., RR. BUMPER", "diagram": "REAR BUMPER", "source": "test"},
            {"part": "Rear Windscreen", "oem_name": "GLASS, RR. WINDSHIELD", "diagram": "REAR BUMPER", "source": "test"},
        ]}

    real_neighbours = catalogue.diagram_neighbours
    catalogue.diagram_neighbours = fake_neighbours
    try:
        with Settings(**ON, intake_links_catalogue=True), TestClient(_app()) as client:
            sign_in(client, role="admin", may_sign_off=True)
            rid, ids = new_draft(client)
            state = read(client, rid)
            check(state["stage"] == "ready", "the read finished", state.get("error"))
            data = state["data"]
            look = data["second_look"]
            linked = links_of(look)
            listed = {it["part"] for it in data["damage_summary"]}
            kinds = [kind_of(r) for r in REQUESTS]

            # -- the read with the linked parts on (1)
            check(listed == {"Rear Bumper", "Boot Lid", "Left Rear Door"}, "the damage list is the read's alone",
                  sorted(listed))
            check(1 <= len(linked) <= settings.intake_links_max, f"{len(linked)} linked parts stored, within the cap")
            check(not {it["part"] for it in linked} & listed, "none of them is on the damage list (nothing ticked)")
            check(all(it.get("linked_from") and it.get("link_rule", "").startswith(f"{links.TABLE_VERSION}:")
                      for it in linked), "each names the listed part it hangs off and its L1 rule")
            zone_twin = by_part(look["items"], "Left Tail Lamp")
            check(zone_twin is not None and zone_twin.get("source") == "zone"
                  and zone_twin.get("linked_from") == ["Rear Bumper"],
                  "the zone read's Left Tail Lamp is one suggestion, the Rear Bumper joined to it", zone_twin)
            check(neighbours_calls == [("Gammamotors", "Vezel", 2019, ["Rear Bumper", "Boot Lid", "Left Rear Door"])],
                  "catalogue neighbours asked once, with the read's make, model and year and the listed parts",
                  neighbours_calls)
            beam = by_part(linked, "Rear Bumper Beam")
            check(beam is not None and beam.get("oem") == ["REAR BUMPER"], "a table link the catalogue backs: oem",
                  beam and beam.get("oem"))
            oem_only = by_part(linked, "Rear Windscreen")
            check(oem_only is not None and oem_only.get("link_rule") == f"{links.TABLE_VERSION}:oem>rear-windscreen",
                  "the catalogue's own neighbour is a candidate", oem_only and oem_only.get("link_rule"))
            check(kinds.count("zone") >= 1 and kinds.count("draft") == 1, "zone mode: one main read, the zone reads")

            # -- the linked check fills verdicts (2)
            link_reqs = [r for r in REQUESTS if kind_of(r) == "link"]
            run = look.get("link_run") or {}
            check(1 <= len(link_reqs) <= settings.intake_link_check_max_calls and run.get("status") == "ok"
                  and run.get("calls") == len(link_reqs), f"the linked check: {len(link_reqs)} call(s), run ok", run)
            chain = [m.strip() for m in settings.gemini_link_chain.split(",")]
            check(all(r["model"] in chain for r in link_reqs), "on the light link chain only, never the strong model")
            asked = {p for r in link_reqs for _, p in re.findall(r"^- (C\d+): (.+?)(?: -- .*)?$", r["input"][0]["text"], re.M)}
            check(asked <= {it["part"] for it in linked}, "the closed list: only stored linked parts are asked about")
            reflector = by_part(linked, "Rear Bumper Reflector (side undetermined)")
            ev = [e for e in (reflector or {}).get("evidence") or [] if e.get("by") == "link_check"]
            check(reflector is not None and (reflector.get("link_check") or {}).get("verdict") == "seen"
                  and reflector.get("bucket") == "seen" and ev and ev[0].get("box") and ev[0]["photo"] == f"@{ids[7]}",
                  "seen: verdict, bucket, and the check's photo with its box as evidence", reflector)
            check(beam is not None and (beam.get("link_check") or {}).get("verdict") == "strip_down"
                  and beam.get("bucket") == "strip_down" and beam.get("hidden"), "the hidden beam: strip_down")
            plate = by_part(linked, "Rear Number Plate")
            check(plate is not None and (plate.get("link_check") or {}).get("verdict") == "no",
                  "'no' recorded on the plate", plate and plate.get("link_check"))
            answered = [it for it in linked if it["part"] in asked]
            check(answered and all(it.get("link_check") for it in answered), "every candidate asked has a verdict")
            check(any(r.kind == "link_check" and r.status == "ok" for r in runs_of(rid)), "a link_check run-log row")

            # -- the contract 9 re-check skips the hidden linked parts (3)
            check_reqs = [r for r in REQUESTS if kind_of(r) == "check"]
            hidden = [it for it in linked if it.get("hidden")]
            shown = [it for it in look["items"] if not it.get("hidden")]
            check(len(check_reqs) == 1 and (look.get("check") or {}).get("status") == "ok", "the re-check ran once",
                  look.get("check"))
            check(hidden and all(it.get("check") is None for it in hidden), "no hidden linked part was re-checked",
                  [it["part"] for it in hidden if it.get("check")])
            check(any(it.get("check") for it in shown), "the other suggestions were")
            text = check_reqs[0]["input"][0]["text"] if check_reqs else ""
            check(not any(it["part"].lower() in text.lower() for it in hidden), "the re-check's request names none of them")

            # -- a part added by hand on the wizard re-links (5)
            draft = copy.deepcopy(data)
            draft["damage_summary"].append({"part": "Left Rear Quarter Panel", "condition": "Dented",
                                            "condition_tokens": ["Dented"], "human_verified": True})
            r = client.put(f"/intake/{rid}/data", json={"data": draft})
            answer = r.json()
            new = [it for it in links_of(answer.get("second_look")) if "Left Rear Quarter Panel" in it.get("linked_from", [])]
            stored = db_row(Report, rid).data
            check(r.status_code == 200 and new and stored["second_look"] == answer["second_look"],
                  f"wizard autosave with a part added by hand: {len(new)} linked part(s) of it, answered and stored",
                  answer)
            r = client.put(f"/intake/{rid}/data", json={"data": draft})
            check("second_look" not in r.json() and db_row(Report, rid).data["second_look"] == stored["second_look"],
                  "the same save again links nothing more")

            # -- a hidden part added on the wizard: strip-down until its condition is edited (4, wizard)
            floor = by_part(linked, "Boot Floor Board")
            r = client.post(f"/intake/{rid}/suggestions", json={"key": floor["key"], "action": "add"})
            item = r.json()["item"]
            check(item and item.get("needs_strip_down") is True and item.get("hidden") is True
                  and item.get("linked_from") == ["Rear Bumper"] and not item.get("condition"),
                  "a hidden linked part added: needs_strip_down, where it hangs off, no condition made up", item)
            draft["damage_summary"].append(item)
            client.put(f"/intake/{rid}/data", json={"data": draft})
            kept = by_part(db_row(Report, rid).data["damage_summary"], "Boot Floor Board")
            check(kept.get("needs_strip_down") is True, "an autosave that leaves it alone keeps the flag")
            draft["damage_summary"][-1] = {**item, "condition": "Dented", "condition_tokens": ["Dented"]}
            client.put(f"/intake/{rid}/data", json={"data": draft})
            edited = by_part(db_row(Report, rid).data["damage_summary"], "Boot Floor Board")
            check("needs_strip_down" not in edited, "a person's condition ends it (left out, not false)", edited)
            draft["damage_summary"][-1] = {**item, "condition": "Dented", "condition_tokens": ["Dented"]}
            client.put(f"/intake/{rid}/data", json={"data": draft})
            check("needs_strip_down" not in by_part(db_row(Report, rid).data["damage_summary"], "Boot Floor Board"),
                  "an old tab still sending the flag doesn't bring it back")

            # -- filing relabels the zone and linked suggestions (6)
            state = client.get(f"/intake/{rid}").json()
            r = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
            check(r.status_code == 200, "File case", r.text)
            filed = db_row(Report, rid)
            index_of = {pid: i for i, pid in enumerate(ids)}
            label = lambda pid: f"P{index_of[pid] + 1:02d}"  # noqa: E731
            zone_sugs = (filed.ai_draft or {}).get("zone_suggestions") or []
            check(zone_sugs and not refs_in(filed.ai_draft) and zone_sugs[0].get("photo_reference") == label(ids[3]),
                  "the AI draft's zone suggestions as P-numbers (no '@' left)", zone_sugs[:1])
            filed_links = links_of(filed.data["second_look"])
            fr = by_part(filed_links, "Rear Bumper Reflector (side undetermined)")
            check(not refs_in(filed.data["second_look"]) and any(
                e.get("by") == "link_check" and e.get("photo") == label(ids[7]) for e in fr.get("evidence") or []),
                "the linked parts' photos (the linked check's too) as P-numbers")
            lc_rows = [r for r in runs_of(rid) if r.kind == "link_check"]
            check(lc_rows and not refs_in(lc_rows[0].photos_sent), "the link_check run row's photos relabelled too",
                  lc_rows and lc_rows[0].photos_sent)

            # -- a hidden part added on the case page: its chip, ended by a condition edit (4, case page)
            r = client.post(f"/reports/{rid}/suggestions", json={"key": beam["key"], "action": "add"})
            body = r.json()
            check(r.status_code == 200 and body["item"].get("needs_strip_down") is True, "case page: the beam added, "
                  "needs strip-down", body.get("item"))
            at = body["item_index"]
            detail = client.get(f"/reports/{rid}").json()
            check(detail["data"]["damage_summary"][at].get("needs_strip_down") is True, "the case shows its chip")
            r = client.patch(f"/reports/{rid}/damage-items/{at}", json={"severity": "Severe"})
            check(r.json()["item"].get("needs_strip_down") is True, "an edit of something else keeps it")
            r = client.patch(f"/reports/{rid}/damage-items/{at}", json={"condition": "Dented"})
            check(r.status_code == 200 and "needs_strip_down" not in r.json()["item"]
                  and "needs_strip_down" not in db_row(Report, rid).data["damage_summary"][at],
                  "a condition edit ends it", r.json().get("item"))

            # -- a part added by hand on the case page re-links (5)
            r = client.post(f"/reports/{rid}/damage-items", json={"part": "Left Front Fender", "condition": "Dented"})
            body = r.json()
            new = [it for it in links_of(body.get("second_look")) if "Left Front Fender" in it.get("linked_from", [])]
            check(r.status_code == 200 and new and db_row(Report, rid).data["second_look"] == body["second_look"],
                  f"case page POST /damage-items: {len(new)} linked part(s) of it, answered and stored")

            # -- sign-off: AI accuracy VERSION 3 (7)
            from app.reports import accuracy

            r = sign_off(client, rid)
            check(r.status_code == 200, "signed off", r.text[:300])
            row = db_row(AiAccuracy, rid)
            sug = (row.metrics or {}).get("suggestions") or {} if row else {}
            check(row is not None and row.version == accuracy.VERSION == 3, "an AI-accuracy row, VERSION 3")
            check("link" in (sug.get("by_source") or {}) and sug.get("by_link_type") and sug.get("by_bucket")
                  and sug.get("link_check") and sug.get("links_table") == links.TABLE_VERSION,
                  "with the link groups: by source link, type, bucket, the linked check's verdicts, table L1",
                  {k: sug.get(k) for k in ("by_source", "links_table")})
            check(not re.search(r"SZZ1234Z", json.dumps(row.metrics)), "no plate in the row")
    finally:
        catalogue.diagram_neighbours = real_neighbours


def catalogue_off_and_failing() -> None:
    """intake_links_catalogue off: diagram_neighbours never called; on but
    raising: the read is fine and the table links alone."""
    from fastapi.testclient import TestClient

    from app.parts import catalogue
    from auth_helpers import sign_in

    calls = []

    def boom(*a):
        calls.append(a)
        raise RuntimeError("catalogue store unreadable")

    real = catalogue.diagram_neighbours
    catalogue.diagram_neighbours = boom
    try:
        with Settings(intake_links="on"), TestClient(_app()) as client:
            sign_in(client, role="admin")
            rid, _ = new_draft(client, 6)
            state = read(client, rid)
            check(state["stage"] == "ready" and links_of(state["data"]["second_look"]) and calls == [],
                  "catalogue off: no call, the table's linked parts stored")
            with Settings(intake_links_catalogue=True):
                client.post(f"/intake/{rid}/process", json={"mode": "read"})
                state = wait_done(client, rid)
            linked = links_of(state["data"]["second_look"])
            check(state["stage"] == "ready" and linked and len(calls) == 1
                  and not any((it.get("link_rule") or "").split(":")[-1].startswith("oem>") for it in linked),
                  "catalogue on but failing: the reading is fine, the table alone links")
    finally:
        catalogue.diagram_neighbours = real


# ------------------------------------------------------------ 2. shadow


def shadow() -> None:
    from fastapi.testclient import TestClient

    from auth_helpers import sign_in

    with Settings(intake_read_mode="zones", intake_zone_only="suggest", intake_links="shadow",
                  intake_link_check="lite"), TestClient(_app()) as client:
        sign_in(client, role="admin", may_sign_off=True)
        rid, ids = new_draft(client)
        state = read(client, rid)
        look = state["data"]["second_look"]
        session = db_row(IntakeSession, rid)
        predicted = (session.ai_draft or {}).get("link_suggestions") or []
        check(state["stage"] == "ready" and not links_of(look)
              and not any(it.get("linked_from") for it in look["items"]),
              "shadow: no linked part shown, none joined to a suggestion")
        check(predicted and all(it.get("source") == "link" for it in predicted),
              f"{len(predicted)} linked parts kept on the AI draft")
        lc = [r for r in REQUESTS if kind_of(r) == "link"]
        check(lc and (session.ai_draft.get("link_run") or {}).get("status") == "ok" and "link_run" not in look,
              "the linked check ran on them, its run on the AI draft only")
        check(any(it.get("link_check") for it in predicted), "its verdicts on the AI draft's linked parts")
        # One predicted part ends on the list by hand: in shadow nothing re-links.
        hit = predicted[0]["part"]
        draft = copy.deepcopy(state["data"])
        draft["damage_summary"].append({"part": hit, "condition": "Dented", "condition_tokens": ["Dented"],
                                        "human_verified": True})
        r = client.put(f"/intake/{rid}/data", json={"data": draft})
        check("second_look" not in r.json(), "a part added by hand links nothing in shadow")
        state = client.get(f"/intake/{rid}").json()
        client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
        filed = db_row(Report, rid)
        kept = (filed.ai_draft or {}).get("link_suggestions") or []
        check(filed.status != "intake" and kept and not refs_in(kept)
              and any(e.get("photo", "").startswith("P") for it in kept for e in it.get("evidence") or []),
              "filing: the AI draft's linked parts kept, relabelled as P-numbers")
        r = sign_off(client, rid)
        row = db_row(AiAccuracy, rid)
        shadow_counts = ((row.metrics or {}).get("suggestions") or {}).get("shadow") if row else None
        check(r.status_code == 200 and shadow_counts and shadow_counts.get("predicted") == len(kept)
              and shadow_counts.get("on_final_list") == 1,
              "counted at sign-off: predicted, and the one on the signed-off list", shadow_counts)


# ------------------------------------------------------------ 3. Telegram + heartbeat


def _child_env(rid_gate: Path) -> dict:
    env = dict(os.environ, C10_CHILD_TMP=_tmp, C10_GATE=str(rid_gate), PYTHONIOENCODING="utf-8",
               INTAKE_READ_MODE="zones", INTAKE_ZONE_ONLY="suggest", INTAKE_LINKS="on", INTAKE_LINK_CHECK="lite",
               TELEGRAM_FULL_DETECTION="true")
    if os.environ.get("C10_TEST_ROOT"):
        env["C10_TEST_ROOT"] = os.environ["C10_TEST_ROOT"]
    return env


def bot_child(rid: str, uid: str) -> int:
    """The Telegram bot's process: start the website's run on the draft the
    bridge made (telegram_bridge.start, as _detect_and_reply does) and stay
    up while it runs."""
    from app.ai.keys import AiActor
    from app.intake import telegram_bridge as bridge

    pipeline.RUN_HEARTBEAT_SECONDS = 0.2
    print("child: starting", flush=True)
    if not bridge.start(rid, AiActor.for_user(uid, "telegram")):
        print("child: could not start", flush=True)
        return 2
    while pipeline.is_running(rid):
        time.sleep(0.05)
    print("child: done", flush=True)
    return 0


def _spawn(rid: str, uid: str, gate: Path):
    """This file again, as the bot's process; its output to a file (a pipe
    nobody reads while the test waits could fill and stall it)."""
    log = Path(_tmp, f"child-{rid}.log")
    with open(log, "w", encoding="utf-8") as out:
        child = subprocess.Popen([sys.executable, __file__, "--bot-child", rid, uid], env=_child_env(gate),
                                 stdout=out, stderr=subprocess.STDOUT)
    return child, log


def _bridge_draft(uid: str, n: int = 6) -> str:
    from app.intake import telegram_bridge as bridge

    folder = Path(_tmp, f"tg-{time.time_ns()}")
    folder.mkdir()
    paths = []
    for i in range(n):
        path = folder / f"tg-{i}.jpg"
        path.write_bytes(jpeg(i))
        paths.append(str(path))
    created = bridge.create_draft(external_id="2001", photo_paths=paths, facts={"vehicle_plate": "SZZ1234Z"},
                                  notes="Hit at the rear.", who=lambda db: (db.get(User, uid), None))
    assert created.report_id, created.problem
    return created.report_id


def _beat_of(rid: str):
    session = db_row(IntakeSession, rid)
    return session.run_heartbeat_at if session else None


def _wait(cond, timeout=30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def telegram_heartbeat() -> None:
    from fastapi.testclient import TestClient

    from app.intake import cleanup
    from auth_helpers import sign_in

    with Settings(**ON, telegram_full_detection=True), TestClient(_app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)

        # -- a live run in the bot's process
        rid = _bridge_draft(uid)
        created_beat = _beat_of(rid)
        check(created_beat is not None, "the bridge's draft is alive from the start (run_heartbeat_at set)")
        gate = Path(_tmp, f"gate-{rid}")
        child, log = _spawn(rid, uid, gate)
        try:
            check(_wait(lambda: (_beat_of(rid) or created_beat) > created_beat), "the bot's run beats its heartbeat")
            check(not pipeline.is_running(rid), "this process (the api) has no run of it")
            state = client.get(f"/intake/{rid}").json()
            check(state["stage"] == "sorting" and not state.get("error"),
                  "the wizard sees it reading, not 'stopped': no Retry offered", (state["stage"], state.get("error")))
            r = client.post(f"/intake/{rid}/process", json={"mode": "all"})
            check(r.status_code == 409, "Retry refused: a second run would race the bot's", r.status_code)
            r = client.put(f"/intake/{rid}/data", json={"data": state["data"]})
            check(r.status_code == 409, "autosave refused while it reads", r.status_code)
            r = client.post(f"/intake/{rid}/suggestions", json={"key": "Rear Bumper Beam", "action": "add"})
            check(r.status_code == 409 and r.json().get("code") == "running", "a decision refused: running", r.text)
            r = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
            check(r.status_code == 409, "File case refused", r.status_code)
            session, report = db_row(IntakeSession, rid), db_row(Report, rid)
            far = datetime.now(timezone.utc) + timedelta(days=30)
            check(cleanup.draft_deletes_at(session, report, None, datetime.now(timezone.utc)) is None
                  and pipeline.is_live(session) and not pipeline.is_live(session, far),
                  "the sweep leaves it (busy); a beat 30 days old would not count")
            gate.write_text("go")
            state = wait_done(client, rid)
            child.wait(timeout=60)
            look = state["data"]["second_look"]
            check(state["stage"] == "ready" and child.returncode == 0, "the bot's run ended ready",
                  log.read_text(encoding="utf-8", errors="replace")[-800:])
            check(links_of(look) and look.get("link_run", {}).get("status") == "ok",
                  "the same full detection: the linked parts and their linked check")
            rows = runs_of(rid)
            check(rows and all(r.channel == "telegram" and r.actor_user_id == uid for r in rows)
                  and {"sort", "read", "link_check"} <= {r.kind for r in rows},
                  "its run-log rows: channel telegram, on the person's AI", sorted({r.kind for r in rows}))
            r = client.post(f"/intake/{rid}/suggestions",
                            json={"key": links_of(look)[0]["key"], "action": "dismiss"})
            check(r.status_code == 200, "decided on the website once it is done")
        finally:
            if child.poll() is None:
                child.kill()

        # -- the bot killed mid-run: stale heartbeat -> "stopped", Retry in the api
        rid = _bridge_draft(uid)
        start_beat = _beat_of(rid)
        gate = Path(_tmp, f"gate-{rid}")
        child, _log = _spawn(rid, uid, gate)
        try:
            _wait(lambda: (_beat_of(rid) or start_beat) > start_beat)
            check(client.get(f"/intake/{rid}").json()["stage"] == "sorting", "reading while the bot lives")
        finally:
            child.kill()
            child.wait(timeout=30)
        stale = pipeline.RUN_HEARTBEAT_STALE
        pipeline.RUN_HEARTBEAT_STALE = timedelta(seconds=1)
        try:
            check(_wait(lambda: client.get(f"/intake/{rid}").json()["stage"] == "failed", 10),
                  "the bot gone: once its beat is stale the draft reads stopped")
            state = client.get(f"/intake/{rid}").json()
            check(state["error"] == pipeline.RUN_INTERRUPTED_MESSAGE, "with Retry's words", state["error"])
            r = client.post(f"/intake/{rid}/process", json={"mode": "all"})
            check(r.status_code == 200 and db_row(IntakeSession, rid).run_heartbeat_at is None,
                  "Retry runs it in the api (the old beat cleared)", r.text[:200])
            state = wait_done(client, rid)
            check(state["stage"] == "ready" and links_of(state["data"]["second_look"]), "and it reads ready")
        finally:
            pipeline.RUN_HEARTBEAT_STALE = stale


# ------------------------------------------------------------ 4. everything off


# Section 4's goldens, taken on master a89ede1 (contract 9 live) with this
# file: C10_TEST_ROOT=<git archive of a89ede1>/apps/bot-service
# python scripts/test_c10_integration.py --golden. sha256 of the normalised
# requests and stored data (normalised(): ids, times and durations).
GOLDEN_OFF = {
    "single": "62387060f1bdf0d683d60ae60445a81bd4151da1029f44036421ec50a11bb0f7",
    "zones": "6efedc32835f47c768c5ac82932b318b6f56d36a7469e598d24a6b341ab0756a",
}
OFF_MODES = {
    "single": {},
    # The live server's contract 9 reading (infra/.env), with the lite re-check.
    "zones": dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason",
                  intake_check_mode="lite"),
}
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
# Durations, and the sign-off's hash of who signed and when.
_DURATIONS = {"seconds", "ms", "waited_s", "signature_hash"}


def normalised(obj, ids: dict[str, str]):
    """obj as JSON with the run's ids as placeholders, times as "<t>", the
    durations as null and image bytes as their sha256: what is the same on
    every run of the same case."""
    def walk(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k in _DURATIONS:
                    out[k] = None
                elif k == "data" and isinstance(v, str) and len(v) > 200:
                    out[k] = hashlib.sha256(v.encode()).hexdigest()
                else:
                    out[k] = walk(v)
            return out
        if isinstance(o, (list, tuple)):
            return [walk(v) for v in o]
        return o
    text = json.dumps(walk(obj), sort_keys=True, default=str)
    for real, alias in sorted(ids.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(real, alias)
    text = text.replace(Path(_tmp).as_posix(), "<tmp>").replace(str(Path(_tmp)).replace("\\", "\\\\"), "<tmp>")
    return json.loads(_TIME.sub("<t>", text))


def _request(kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if k != "timeout"}


def _run_rows(rid: str) -> list[dict]:
    rows = [{c: getattr(r, c) for c in ("kind", "channel", "actor_user_id", "key_source", "key_last4", "status",
                                         "error_code", "error_text", "attempts", "model_used", "photos_sent",
                                         "input_bytes", "prompt_tokens", "output_tokens")} for r in runs_of(rid)]
    for r in rows:
        # A run's tries in the order they ended: the zone reads run at once.
        r["attempts"] = sorted(r["attempts"] or [], key=lambda a: json.dumps(a, sort_keys=True, default=str))
    return sorted(rows, key=lambda r: (r["kind"], json.dumps(r, sort_keys=True, default=str)))


def _without_c10(ai_draft):
    """ai_draft without contract 10's deliberate addition in zone mode
    (Z: "zone_intact"); returns (the rest, whether it was there)."""
    if not isinstance(ai_draft, dict):
        return ai_draft, False
    rest = dict(ai_draft)
    return rest, rest.pop("zone_intact", None) is not None


def off_scenario(mode: str) -> tuple[dict, dict]:
    """One case through every route with contract 10 at its defaults.
    (artefacts normalised, facts checked apart)."""
    from fastapi.testclient import TestClient

    from auth_helpers import sign_in

    REQUESTS.clear()
    facts: dict = {}
    out: dict = {}
    with Settings(**OFF_MODES[mode]), TestClient(_app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        rid, pids = new_draft(client)
        ids = {rid: "<rid>", uid: "<uid>", db_row(User, uid).email: "<email>",
               **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        state = read(client, rid)
        facts["stage"] = state["stage"]
        session = db_row(IntakeSession, rid)
        ai_draft, facts["zone_intact_read"] = _without_c10(session.ai_draft)
        out["read"] = {"data": db_row(Report, rid).data, "ai_draft": ai_draft,
                       "read_photo_ids": session.read_photo_ids, "runs": _run_rows(rid)}
        out["requests"] = sorted((normalised(_request(r), ids) for r in REQUESTS),
                                 key=lambda r: json.dumps(r, sort_keys=True))
        data = state["data"]
        open_items = [it for it in (data.get("second_look") or {}).get("items") or []]
        if open_items:
            r = client.post(f"/intake/{rid}/suggestions", json={"key": open_items[0]["key"], "action": "add"})
            out["add"] = r.json()
            if r.json().get("item"):
                data["damage_summary"].append(r.json()["item"])
        if len(open_items) > 1:
            out["dismiss"] = client.post(f"/intake/{rid}/suggestions",
                                         json={"key": open_items[1]["key"], "action": "dismiss"}).json()
        data["damage_summary"].append({"part": "Left Rear Quarter Panel", "condition": "Dented",
                                       "condition_tokens": ["Dented"], "human_verified": True})
        data["damage_summary"][0] = {**data["damage_summary"][0], "condition": "Cracked", "condition_tokens": ["Cracked"]}
        out["autosave"] = client.put(f"/intake/{rid}/data", json={"data": data}).json()
        out["autosaved"] = db_row(Report, rid).data
        state = client.get(f"/intake/{rid}").json()
        out["finalize"] = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]}).json()
        filed = db_row(Report, rid)
        filed_ai, facts["zone_intact_filed"] = _without_c10(filed.ai_draft)
        out["filed"] = {"data": filed.data, "ai_draft": filed_ai, "status": filed.status, "runs": _run_rows(rid)}
        out["case_add"] = client.post(f"/reports/{rid}/damage-items",
                                      json={"part": "Left Front Fender", "condition": "Dented"}).json()
        out["case_patch"] = client.patch(f"/reports/{rid}/damage-items/0", json={"condition": "Dented"}).json()
        facts["sign_off"] = sign_off(client, rid).status_code
        row = db_row(AiAccuracy, rid)
        # The version (row and metrics) is contract 10's bump, 2 -> 3 (4.7):
        # checked on its own, not hashed.
        metrics = dict(row.metrics or {}) if row else None
        facts["version"] = (row.version, metrics.pop("version", None)) if row else None
        out["accuracy"] = {"metrics": metrics, "models": row.models if row else None}
        out["case"] = db_row(Report, rid).data
        return normalised(out, ids), facts


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def everything_off() -> None:
    from app.reports import accuracy

    for mode in OFF_MODES:
        artefacts, facts = off_scenario(mode)
        sha = digest(artefacts)
        check(facts["stage"] == "ready" and facts["sign_off"] == 200, f"{mode}: read, filed and signed off")
        check(sha == GOLDEN_OFF[mode], f"{mode}: requests and stored data byte for byte as master a89ede1's (golden)",
              sha)
        check(not any(it.get("source") == "link" for it in (artefacts["case"].get("second_look") or {}).get("items") or [])
              and "link_suggestions" not in json.dumps(artefacts), f"{mode}: no linked part anywhere")
        want = mode == "zones"
        check(facts["zone_intact_read"] is want and facts["zone_intact_filed"] is want,
              f"{mode}: ai_draft['zone_intact'] {'kept (Z, contract 10 4.1)' if want else 'absent'}")
        check(facts["version"] == (3, 3) and accuracy.VERSION == 3,
              f"{mode}: the accuracy row and its metrics are VERSION 3 (contract 10 4.7)", facts["version"])
        check(not [r for r in REQUESTS if kind_of(r) == "link"], f"{mode}: no linked-check request")


def golden() -> int:
    """Prints section 4's goldens; --dump DIR also writes what they hash
    (to tell two trees' runs apart: diff the files)."""
    dump = Path(sys.argv[sys.argv.index("--dump") + 1]) if "--dump" in sys.argv else None
    for mode in OFF_MODES:
        artefacts, facts = off_scenario(mode)
        print(f"{mode}: {digest(artefacts)}  {facts}")
        if dump:
            dump.mkdir(parents=True, exist_ok=True)
            (dump / f"{mode}.json").write_text(json.dumps(artefacts, sort_keys=True, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    init_db()
    for name, fn in (
        ("1. links on: read, linked check, re-check, strip-down, re-link, filing, sign-off", links_on),
        ("1b. the catalogue's neighbours: off never called, failing never fails the read", catalogue_off_and_failing),
        ("2. shadow: nothing shown, counted at sign-off", shadow),
        ("3. Telegram on: the run in the bot's process, the heartbeat across processes", telegram_heartbeat),
        ("4. everything off: byte for byte as master a89ede1", everything_off),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    if CHILD:
        at = sys.argv.index("--bot-child")
        sys.exit(bot_child(sys.argv[at + 1], sys.argv[at + 2]))
    if GOLDEN_MODE:
        init_db()
        sys.exit(golden())
    sys.exit(main())
