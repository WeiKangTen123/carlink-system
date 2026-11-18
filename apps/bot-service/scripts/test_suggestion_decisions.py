"""Contract 9 D2 (plans/2026-11-15-evidence/contract9.md, sections 4.3-4.5):
what people decide about the AI's suggestions, and what becomes of them.

Covers:
  - suggestions.item_from_suggestion: the damage item a person adds, with
    the AI's words, reason, evidence and box on the best evidence's photo;
  - POST /intake/{id}/suggestions and POST /reports/{id}/suggestions: add /
    dismiss / undo stamped by the server; add idempotent; undo taking back
    the latest decision still standing; 409 while the AI reads (running)
    and when signed off (signed_off); 404 unknown_suggestion; 400
    bad_action; 403 for someone who may not edit;
  - the case page's add going the way POST /damage-items does (Annex A
    sync, _undismiss, history) and its undo never touching dismissed_parts;
  - second_look server-owned: an autosave or finalize with a stale copy,
    PUT /reports with one, change nothing;
  - filing: second_look kept, its photos, the list's evidence and the AI's
    zone suggestions turned into P-numbers, a deleted photo's dropped;
  - AI accuracy VERSION 2: the suggestions' outcomes (added / dismissed /
    left, by source / read / re-check), no reason, photo or box in a row,
    summed by GET /admin/ai-accuracy;
  - old cases: no second_look, items without keys, dismissed part names.

    python scripts/test_suggestion_decisions.py

Offline, no AI call. Parts are canonical names; the photos are coloured
squares; the reasons are made up (one quoting a made-up plate, which must
never reach an accuracy row).
"""
import copy
import io
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="c9-d2-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-c9-d2-key"
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"

# Replaces the API's PDF/Excel renderers (the routes are the subject here).
import test_annexes as annexes  # noqa: E402,F401

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.api.main as main_api  # noqa: E402
from app.api.main import app  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.parts.prefill import item_key  # noqa: E402
from app.reports import accuracy, suggestions  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiAccuracy, AuditEntry, IntakeSession, Report  # noqa: E402
from app.reports.schema import SecondLook  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": ""}

# A made-up plate in an AI reason: it must never reach an accuracy row.
FAKE_PLATE = "QZX9876K"


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


def jpeg(i: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (30 * i % 256, 90, (200 - 20 * i) % 256)).save(buf, "JPEG")
    return buf.getvalue()


def stored(rid: str) -> tuple[dict, dict | None]:
    db = SessionLocal()
    try:
        r = db.get(Report, rid)
        return copy.deepcopy(r.data), copy.deepcopy(r.ai_draft)
    finally:
        db.close()


def set_data(rid: str, **changes) -> None:
    db = SessionLocal()
    try:
        r = db.get(Report, rid)
        r.data = {**(r.data or {}), **changes}
        db.commit()
    finally:
        db.close()


def history(rid: str) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        rows = db.query(AuditEntry).filter(AuditEntry.report_id == rid).order_by(AuditEntry.id).all()
        return [(e.action, e.detail or "") for e in rows]
    finally:
        db.close()


def ev(photo: str, box=None, by="zone:rear", reason=None, confidence="Medium") -> dict:
    return {"photo": photo, "box": box, "box_kind": "ai" if box else None, "by": by, "reason": reason,
            "confidence": confidence, "img_w": 1600 if box else None, "img_h": 1200 if box else None}


def suggestion(part: str, *, source="zone", photo=None, evidence=(), found_by=("zone:rear",), seen_in=(), check=None,
               confidence="Medium", **extra) -> dict:
    item = {"key": part, "part": part, "name": part, "photo_reference": photo, "note": None, "condition_tokens": [],
            "condition": None, "damage_type": None, "confidence": confidence, "source": source, "covers": [part],
            "reason": None, "evidence": list(evidence), "found_by": list(found_by), "seen_in": list(seen_in),
            "check": check, "other_side_listed": None, "other_part_name": None, "severity": None, "remarks": None}
    item.update(extra)
    item["key"] = item_key(item) or part
    return item


class W:
    """What the sections share: the clients and the case they work on."""
    owner: TestClient
    other: TestClient
    admin: TestClient
    owner_id: str
    rid: str = ""
    photos: list[dict] = []


# ---------------------------------------------------------------- the item


def builder() -> None:
    sug = suggestion(
        "Left Tail Lamp", photo="@p2", seen_in=["@p1", "@p2"], found_by=["zone:rear", "zone:left"],
        condition_tokens=["Cracked"], damage_type=None, severity="Moderate", remarks="Replace",
        reason="lens cracked at the lower edge", confidence="Low",
        evidence=[ev("@p1", [100, 200, 300, 400], reason="lens cracked at the lower edge"), ev("@p2")],
        check={"verdict": "yes", "p": 0.91, "by": "decision", "model": "gemini-decision-1", "reason": None})
    item = suggestions.item_from_suggestion(sug)
    check(item["photo_reference"] == "@p1" and item["bbox_2d"] == [100, 200, 300, 400],
          "photo_reference = the best evidence's photo, bbox_2d its box", (item["photo_reference"], item["bbox_2d"]))
    check((item["part"], item["condition_tokens"], item["condition"], item["damage_type"], item["severity"], item["remarks"])
          == ("Left Tail Lamp", ["Cracked"], "Cracked", "Crack", "Moderate", "Replace"),
          "the AI's words copied; the condition built from them, the type they imply (as POST /damage-items)", item)
    check(item["reason"] == "lens cracked at the lower edge" and len(item["evidence"]) == 2
          and item["evidence"][0]["box"] == [100, 200, 300, 400] and item["seen_in"] == ["@p1", "@p2"]
          and item["found_by"] == ["zone:rear", "zone:left"] and item["check"]["verdict"] == "yes",
          "reason, evidence, seen_in, found_by and the re-check's answer copied")
    check(item["ai_confidence"] == "Low" and item["human_verified"] is True and item["added_from"] == "suggestion",
          "the AI's confidence; verified by the person adding it; added_from suggestion")
    bare = suggestions.item_from_suggestion(suggestion("Boot Lid Hinge", photo="@p3", damage_type="Dent"))
    check(bare["photo_reference"] == "@p3" and bare["bbox_2d"] is None and bare["evidence"] == []
          and bare["condition"] == "Dented",
          "no evidence: the suggestion's own photo, no box; a type with no words gets the firm word", bare)
    other = suggestions.item_from_suggestion(suggestion("Other / Not Listed", other_part_name="Rear air vent"))
    check(other["other_part_name"] == "Rear air vent" and item_key(other) == item_key({"part": "Other / Not Listed",
                                                                                     "other_part_name": "Rear air vent"}),
          "a named Other keeps its name (its own Annex A row)")

    decisions = [{"key": "A", "action": "added"}, {"key": "A", "action": "undone"}, {"key": "A", "action": "dismissed"},
                 {"key": "B", "action": "dismissed"}, {"key": "B", "action": "undone"}, {"key": "B", "action": "undone"}]
    still = suggestions.standing(decisions)
    check([d["action"] for d in still["A"]] == ["dismissed"] and still["B"] == [],
          "standing(): an undo takes back the latest decision still standing", still)


# ---------------------------------------------------------------- intake


def make_draft() -> None:
    W.rid = W.owner.post("/intake").json()["id"]
    for i in range(4):
        r = W.owner.post(f"/intake/{W.rid}/photos", files={"file": (f"p{i}.jpg", jpeg(i), "image/jpeg")},
                         data={"position": str(i)})
        assert r.status_code == 200, r.text
    W.photos = W.owner.get(f"/intake/{W.rid}").json()["photos"]
    p = [f"@{ph['id']}" for ph in W.photos]
    look = SecondLook(status="skipped", note="The second look is switched off.", reads=["overall", "zone:rear", "zone:left"], items=[
        suggestion("Left Tail Lamp", photo=p[1], seen_in=[p[1], p[2]], found_by=["zone:rear", "zone:left"],
                   condition_tokens=["Cracked"], reason=f"lens cracked, plate {FAKE_PLATE} visible",
                   evidence=[ev(p[1], [100, 200, 300, 400], reason="lens cracked"), ev(p[2])],
                   check={"verdict": "yes", "p": 0.9, "by": "decision", "model": "gemini-decision-1", "reason": None}),
        suggestion("Boot Lid Hinge", photo=p[3], seen_in=[p[3]], evidence=[ev(p[3], [10, 10, 200, 200])],
                   check={"verdict": "no", "p": 0.1, "by": "decision", "model": "gemini-decision-1", "reason": None}),
        suggestion("Rear Windscreen", source="kit", photo=p[0], found_by=[], evidence=[]),
        suggestion("Rear Bumper Beam", source="nearby", photo=p[2], found_by=[], evidence=[ev(p[2])],
                   check={"verdict": "unsure", "p": 0.5, "by": "decision", "model": "gemini-decision-1", "reason": None}),
    ]).model_dump()
    data, _ = stored(W.rid)
    data.update(damage_summary=[{"part": "Rear Bumper", "photo_reference": p[0], "ai_confidence": "High",
                                 "evidence": [ev(p[0], [5, 5, 500, 500], by="overall")]}],
                damaged_parts=["Rear Bumper"], second_look=look)
    ai_draft = {"damage_summary": copy.deepcopy(data["damage_summary"]),
                "zone_suggestions": [it for it in copy.deepcopy(look["items"]) if it["source"] == "zone"],
                "zone_reads": ["overall", "zone:rear", "zone:left"]}
    db = SessionLocal()
    try:
        db.get(Report, W.rid).data = data
        session = db.get(IntakeSession, W.rid)
        session.ai_draft = ai_draft
        session.stage = "ready"
        db.commit()
    finally:
        db.close()


def decide(client: TestClient, surface: str, key: str, action: str, looked=False):
    return client.post(f"/{surface}/{W.rid}/suggestions", json={"key": key, "action": action, "looked": looked})


def intake_decisions() -> None:
    make_draft()
    rid = W.rid
    r = decide(W.owner, "intake", "Left Tail Lamp", "add", looked=True)
    body = r.json()
    check(r.status_code == 200 and set(body) == {"second_look", "item", "item_index"}, "add: 200 {second_look, item, item_index}", r.text)
    item = body["item"]
    check(item and item["part"] == "Left Tail Lamp" and item["added_from"] == "suggestion" and item["human_verified"]
          and item["photo_reference"] == f"@{W.photos[1]['id']}" and item["bbox_2d"] == [100, 200, 300, 400]
          and body["item_index"] == 1, "the item built by the server, at the end of the stored list", item)
    data, _ = stored(rid)
    check([it["part"] for it in data["damage_summary"]] == ["Rear Bumper", "Left Tail Lamp"]
          and data["damaged_parts"] == ["Rear Bumper", "Left Tail Lamp"], "appended to the stored list; damaged_parts follows")
    d = data["second_look"]["decisions"]
    check(len(d) == 1 and d[0]["key"] == "Left Tail Lamp" and d[0]["action"] == "added" and d[0]["by_id"] == W.owner_id
          and d[0]["by_name"].startswith("Sue Surveyor") and d[0]["looked"] is True and d[0]["where"] == "intake" and d[0]["at"],
          "the decision stamped by the server: who, when, looked, where", d)
    check(any(it["key"] == "Left Tail Lamp" for it in data["second_look"]["items"]),
          "the suggestion stays in second_look.items (the panel hides a listed one)")
    check(("suggestion_added", "Left Tail Lamp") in history(rid), "history suggestion_added, the part's name only", history(rid))

    again = decide(W.owner, "intake", "Left Tail Lamp", "add").json()
    data, _ = stored(rid)
    check(again["item"] is None and again["item_index"] is None and len(data["damage_summary"]) == 2
          and len(data["second_look"]["decisions"]) == 1, "add again: nothing added twice, no second decision", again)

    r = decide(W.owner, "intake", "Boot Lid Hinge", "dismiss")
    data, _ = stored(rid)
    check(r.status_code == 200 and r.json()["item"] is None and data["second_look"]["dismissed"] == ["Boot Lid Hinge"]
          and data["second_look"]["decisions"][-1]["action"] == "dismissed", "dismiss: the key into dismissed, recorded")
    decide(W.owner, "intake", "Boot Lid Hinge", "dismiss")
    data, _ = stored(rid)
    check(data["second_look"]["dismissed"] == ["Boot Lid Hinge"] and len(data["second_look"]["decisions"]) == 2,
          "dismiss again changes nothing")
    r = decide(W.owner, "intake", "Boot Lid Hinge", "undo")
    data, _ = stored(rid)
    check(r.status_code == 200 and data["second_look"]["dismissed"] == [] and data["second_look"]["decisions"][-1]["action"] == "undone",
          "undo a dismissal: the key leaves dismissed, recorded undone")
    check([a for a, _ in history(rid) if a.startswith("suggestion_")] ==
          ["suggestion_added", "suggestion_dismissed", "suggestion_undone"], "history: added, dismissed, undone")

    # Undo of an add: the item leaves the list; dismissed_parts untouched.
    set_data(rid, dismissed_parts=["Tail Gate"])
    r = decide(W.owner, "intake", "Left Tail Lamp", "undo")
    body = r.json()
    data, _ = stored(rid)
    check(r.status_code == 200 and body["item"]["part"] == "Left Tail Lamp" and body["item_index"] == 1
          and [it["part"] for it in data["damage_summary"]] == ["Rear Bumper"] and data["damaged_parts"] == ["Rear Bumper"],
          "undo an add: that item leaves the stored list and is returned (for the wizard to take off)", body)
    check(data["dismissed_parts"] == ["Tail Gate"] and data["second_look"]["dismissed"] == [],
          "undo an add never touches dismissed_parts nor dismissed")
    n = len(data["second_look"]["decisions"])
    again = decide(W.owner, "intake", "Left Tail Lamp", "undo").json()
    data, _ = stored(rid)
    check(again["item"] is None and len(data["second_look"]["decisions"]) == n, "undo with nothing standing changes nothing")

    for key, action, status, code, what in (
        ("Bonnet", "add", 404, "unknown_suggestion", "an unknown key"),
        ("Bonnet", "undo", 404, "unknown_suggestion", "an undo of an unknown key"),
        ("Left Tail Lamp", "tick", 400, "bad_action", "an action that isn't add, dismiss or undo"),
    ):
        r = decide(W.owner, "intake", key, action)
        check(r.status_code == status and r.json().get("code") == code, f"{status} {code}: {what}", r.text)
    r = decide(W.other, "intake", "Left Tail Lamp", "add")
    check(r.status_code in (403, 404), "someone who may not edit the draft: refused", r.status_code)

    pipeline.reserve(rid)
    db = SessionLocal()
    try:
        db.get(IntakeSession, rid).stage = "reading"
        db.commit()
    finally:
        db.close()
    try:
        r = decide(W.owner, "intake", "Rear Windscreen", "dismiss")
        check(r.status_code == 409 and r.json().get("code") == "running", "409 running while the AI reads the draft", r.text)
    finally:
        pipeline.release(rid)
        db = SessionLocal()
        try:
            db.get(IntakeSession, rid).stage = "ready"
            db.commit()
        finally:
            db.close()

    # Decide again for the filing below: Left Tail Lamp added, Rear
    # Windscreen dismissed, the other two left.
    assert decide(W.owner, "intake", "Left Tail Lamp", "add", looked=True).status_code == 200
    assert decide(W.owner, "intake", "Rear Windscreen", "dismiss").status_code == 200


def autosave_ignores_second_look() -> None:
    rid = W.rid
    before, _ = stored(rid)
    sent = copy.deepcopy(before)
    sent["second_look"] = {"status": "ok", "items": [], "dismissed": ["Bonnet"], "decisions": []}
    sent["description"] = "Rear-ended at a junction"
    r = W.owner.put(f"/intake/{rid}/data", json={"data": sent})
    after, _ = stored(rid)
    check(r.status_code == 200 and after["second_look"] == before["second_look"] and after["description"] == "Rear-ended at a junction",
          "an autosave with a stale second_look changes nothing of it (the rest is saved)", r.text)
    sent["second_look"] = {"items": "nonsense", "decisions": 5}
    r = W.owner.put(f"/intake/{rid}/data", json={"data": sent})
    after, _ = stored(rid)
    check(r.status_code == 200 and after["second_look"] == before["second_look"],
          "a malformed copy from an old tab fails nothing: it is never read", r.text)
    sent.pop("second_look")
    r = W.owner.put(f"/intake/{rid}/data", json={"data": sent})
    after, _ = stored(rid)
    check(r.status_code == 200 and after["second_look"] == before["second_look"], "a save without one keeps the stored one")


# ---------------------------------------------------------------- filing


def filing_relabel() -> None:
    rid = W.rid
    # The surveyor deletes P01 before filing: the rest move up one.
    assert W.owner.delete(f"/intake/{rid}/photos/{W.photos[0]['id']}").status_code == 200
    data, _ = stored(rid)
    body = copy.deepcopy(data)
    body["second_look"] = {"items": [], "dismissed": [], "decisions": []}  # the browser's copy: ignored
    body["report_id"] = None
    r = W.owner.post(f"/intake/{rid}/finalize", json={"data": body})
    check(r.status_code == 200, "finalize", r.text)
    data, ai_draft = stored(rid)
    look = data.get("second_look") or {}
    check(look.get("decisions") and len(look["decisions"]) == len(stored_decisions[0]) and look["dismissed"] == ["Rear Windscreen"],
          "second_look kept at filing, as stored (the browser's copy ignored): decisions, dismissed", look.get("dismissed"))
    by_key = {it["key"]: it for it in look.get("items") or []}
    tail = by_key.get("Left Tail Lamp") or {}
    check(tail.get("photo_reference") == "P01" and tail.get("seen_in") == ["P01", "P02"]
          and [e["photo"] for e in tail.get("evidence") or []] == ["P01", "P02"],
          "a suggestion's photo_reference, seen_in and evidence as P-numbers", tail)
    windscreen = by_key.get("Rear Windscreen") or {}
    check("Rear Windscreen" in by_key and windscreen.get("photo_reference") is None and windscreen.get("evidence") == [],
          "a suggestion on the deleted photo is kept, with no photo", windscreen)
    listed = data["damage_summary"]
    bumper = next(it for it in listed if it["part"] == "Rear Bumper")
    lamp = next(it for it in listed if it["part"] == "Left Tail Lamp")
    check(bumper["photo_reference"] is None and bumper["bbox_2d"] is None and bumper["evidence"] == [],
          "a list item on the deleted photo: no photo, no box, its evidence entry dropped", bumper)
    check(lamp["photo_reference"] == "P01" and [e["photo"] for e in lamp["evidence"]] == ["P01", "P02"]
          and lamp["seen_in"] == ["P01", "P02"] and lamp["added_from"] == "suggestion",
          "the added item's photos, seen_in and evidence as P-numbers", lamp)
    zs = {it["key"]: it for it in (ai_draft or {}).get("zone_suggestions") or []}
    check(zs.get("Left Tail Lamp", {}).get("seen_in") == ["P01", "P02"]
          and [e["photo"] for e in zs["Left Tail Lamp"]["evidence"]] == ["P01", "P02"]
          and zs.get("Boot Lid Hinge", {}).get("photo_reference") == "P03",
          "Report.ai_draft keeps the AI's zone suggestions, relabelled", zs)
    check(not any(str(v).startswith("@") for v in json.dumps({"d": data, "a": ai_draft}).split('"')),
          "no \"@<photo id>\" left anywhere in the filed case")
    check(ai_draft["damage_summary"][0]["photo_reference"] is None and ai_draft["damage_summary"][0]["evidence"] == [],
          "the AI's own list relabelled the same way (deleted photo dropped)")


stored_decisions: list = [None]


def remember_decisions() -> None:
    data, _ = stored(W.rid)
    stored_decisions[0] = list(data["second_look"]["decisions"])


# ---------------------------------------------------------------- case page


def case_decisions() -> None:
    rid = W.rid
    r = W.owner.post(f"/intake/{rid}/suggestions", json={"key": "Boot Lid Hinge", "action": "add"})
    check(r.status_code == 409, "the draft endpoint on a filed case: 409", r.status_code)
    # A part the surveyor had removed from the list earlier.
    set_data(rid, dismissed_parts=["Boot Lid Hinge", "Tail Gate"])
    r = decide(W.owner, "reports", "Boot Lid Hinge", "add", looked=False)
    body = r.json()
    check(r.status_code == 200 and {"second_look", "item", "item_index", "id", "damage_summary", "damaged_parts", "annex_a", "annex_c"} <= set(body),
          "add on the case: second_look/item/item_index and what POST /damage-items answers", r.text)
    item = body.get("item") or {}
    check(item.get("photo_reference") == "P03" and item.get("bbox_2d") == [10, 10, 200, 200] and item.get("added_from") == "suggestion",
          "the item on the case's P-number with its box", item)
    rows = body["annex_a"]["items"]
    check(any(row.get("source_part") == "Boot Lid Hinge" for row in rows), "Annex A follows the list (sync)", [r_.get("source_part") for r_ in rows])
    data, _ = stored(rid)
    check(data["dismissed_parts"] == ["Tail Gate"], "_undismiss: the added part leaves dismissed_parts", data["dismissed_parts"])
    check(data["second_look"]["decisions"][-1]["where"] == "case" and data["second_look"]["decisions"][-1]["looked"] is False,
          "the decision says where (case) and that the person didn't look first")
    acts = [a for a, _ in history(rid)]
    check(acts[-2:] == ["suggestion_added", "damage_item_added"], "history: suggestion_added and damage_item_added", acts[-4:])

    r = decide(W.owner, "reports", "Boot Lid Hinge", "undo")
    body = r.json()
    data, _ = stored(rid)
    check(r.status_code == 200 and body["item"]["part"] == "Boot Lid Hinge"
          and "Boot Lid Hinge" not in [it["part"] for it in data["damage_summary"]]
          and not any(row.get("source_part") == "Boot Lid Hinge" for row in data["annex_a"]["items"]),
          "undo the add on the case: the item and its unpriced Annex A rows go", body.get("item"))
    check(data["dismissed_parts"] == ["Tail Gate"], "undo-add never puts the part into dismissed_parts", data["dismissed_parts"])
    check([a for a, _ in history(rid)][-2:] == ["suggestion_undone", "damage_item_removed"], "history: suggestion_undone, damage_item_removed")

    r = decide(W.owner, "reports", "Rear Bumper Beam", "dismiss")
    data, _ = stored(rid)
    check(r.status_code == 200 and "Rear Bumper Beam" in data["second_look"]["dismissed"] and r.json()["annex_a"] == data["annex_a"],
          "dismiss on the case: recorded, the list and Annex A answered as they are")
    r = decide(W.owner, "reports", "Rear Bumper Beam", "undo")
    data, _ = stored(rid)
    check(r.status_code == 200 and "Rear Bumper Beam" not in data["second_look"]["dismissed"], "undo the dismissal on the case")
    r = decide(W.owner, "reports", "Bonnet", "dismiss")
    check(r.status_code == 404 and r.json().get("code") == "unknown_suggestion", "404 unknown_suggestion on the case")
    r = decide(W.owner, "reports", "Bonnet", "maybe")
    check(r.status_code == 400 and r.json().get("code") == "bad_action", "400 bad_action on the case")
    check(decide(W.other, "reports", "Boot Lid Hinge", "add").status_code in (403, 404), "someone who may not edit: refused")

    # PUT /reports never changes second_look.
    before, _ = stored(rid)
    r = W.owner.put(f"/reports/{rid}", json={"draft": {"description": "Edited", "category": ["Vehicle Collision or Damage"],
                                                        "second_look": {"items": [], "dismissed": ["Bonnet"]}}})
    after, _ = stored(rid)
    check(r.status_code == 200 and after["second_look"] == before["second_look"] and after["description"] == "Edited",
          "PUT /reports ignores second_look (the rest saved)", r.text)
    # A decision committed while the edit's PDF renders is kept.
    real_render = main_api._render_after_change

    def render_meanwhile(user, report, what):
        look = stored(rid)[0]["second_look"]
        set_data(rid, second_look={**look, "dismissed": look["dismissed"] + ["Rear Bumper Beam"]})
        return real_render(user, report, what)

    main_api._render_after_change = render_meanwhile
    try:
        r = W.owner.put(f"/reports/{rid}", json={"draft": {"description": "Edited again", "category": ["Vehicle Collision or Damage"]}})
    finally:
        main_api._render_after_change = real_render
    after, _ = stored(rid)
    check(r.status_code == 200 and "Rear Bumper Beam" in after["second_look"]["dismissed"] and after["description"] == "Edited again",
          "a decision made while PUT /reports rendered its PDF survives the save", after["second_look"]["dismissed"])
    set_data(rid, second_look={**after["second_look"], "dismissed": [k for k in after["second_look"]["dismissed"] if k != "Rear Bumper Beam"]})

    # Add Boot Lid Hinge for good (for the accuracy record), then sign off.
    assert decide(W.owner, "reports", "Boot Lid Hinge", "add", looked=True).status_code == 200
    annexes.set_signed_off(rid, True)
    r = decide(W.owner, "reports", "Rear Bumper Beam", "dismiss")
    check(r.status_code == 409 and r.json().get("code") == "signed_off", "409 signed_off once signed off", r.text)


# ---------------------------------------------------------------- accuracy


def accuracy_outcomes() -> None:
    # Contract 10 bumped it to 3 (the linked parts); the suggestions section is VERSION 2's.
    check(accuracy.VERSION >= 2, "accuracy VERSION 2 or later")
    look = {
        "items": [
            suggestion("Left Tail Lamp", found_by=["zone:rear", "zone:left"], reason=f"cracked, plate {FAKE_PLATE}",
                       evidence=[ev("P07", [1, 2, 300, 400], reason=f"plate {FAKE_PLATE}")],
                       check={"verdict": "yes", "p": 0.9, "by": "decision"}),
            suggestion("Boot Lid Hinge", found_by=["zone:rear"], check={"verdict": "no", "p": 0.1, "by": "decision"}),
            suggestion("Rear Windscreen", source="kit", found_by=[]),
            suggestion("Rear Bumper Beam", source="nearby", found_by=[], check={"verdict": "unsure", "by": "lite"}),
        ],
        "dismissed": ["Boot Lid Hinge", "Tail Gate"],
        "decisions": [
            {"key": "Left Tail Lamp", "part": "Left Tail Lamp", "action": "added", "looked": True, "at": "x"},
            {"key": "Boot Lid Hinge", "part": "Boot Lid Hinge", "action": "dismissed", "looked": False, "at": "x"},
            {"key": "Rear Windscreen", "part": "Rear Windscreen", "action": "added", "looked": False, "at": "x"},
            {"key": "Rear Windscreen", "part": "Rear Windscreen", "action": "undone", "looked": False, "at": "x"},
            # Re-read away from the items (listed): its facts from the AI's frozen zone suggestions.
            {"key": "Right Tail Lamp", "part": "Right Tail Lamp", "action": "added", "looked": False, "at": "x"},
        ],
    }
    ai = {"damage_summary": [{"part": "Rear Bumper"}],
          "zone_suggestions": [suggestion("Right Tail Lamp", found_by=["zone:right"])]}
    final = {"damage_summary": [{"part": "Rear Bumper"}, {"part": "Left Tail Lamp", "added_from": "suggestion"},
                                {"part": "Right Tail Lamp", "added_from": "suggestion"}],
             "second_look": look}
    out = accuracy.compare(ai, final)
    s = out["suggestions"]
    check((s["shown"], s["added"], s["dismissed"], s["left"]) == (6, 2, 2, 2),
          "shown = items + decided + dismissed keys; added (on the final list) / dismissed / left", s)
    check(s["looked_first"] == 1, "looked_first: added after opening the viewer", s["looked_first"])
    check(s["by_source"] == {"zone": {"shown": 3, "added": 2, "dismissed": 1, "left": 0},
                             "kit": {"shown": 1, "added": 0, "dismissed": 0, "left": 1},
                             "nearby": {"shown": 1, "added": 0, "dismissed": 0, "left": 1}},
          "by source (a key re-read away: its source from the AI's zone suggestions)", s["by_source"])
    check(s["by_read"] == {"zone:rear": {"shown": 2, "added": 1, "dismissed": 1, "left": 0},
                           "zone:left": {"shown": 1, "added": 1, "dismissed": 0, "left": 0},
                           "zone:right": {"shown": 1, "added": 1, "dismissed": 0, "left": 0}},
          "by the read that found it", s["by_read"])
    check(s["by_check"] == {"yes": {"shown": 1, "added": 1, "dismissed": 0, "left": 0},
                            "no": {"shown": 1, "added": 0, "dismissed": 1, "left": 0},
                            "none": {"shown": 3, "added": 1, "dismissed": 1, "left": 1},
                            "unsure": {"shown": 1, "added": 0, "dismissed": 0, "left": 1}},
          "by the re-check's verdict (none: not re-checked)", s["by_check"])
    check(out["damage"]["added"] == 2 and out["damage"]["kept"] == 1,
          "the list counts unchanged: a part added from a suggestion is still added by people", out["damage"])
    text = json.dumps(out)
    check(FAKE_PLATE not in text and "cracked" not in text and "P07" not in text and "300" not in text,
          "no reason, photo or box in the row: counts only")

    empty = accuracy.compare({"damage_summary": [{"part": "Rear Bumper"}]}, {"damage_summary": [{"part": "Rear Bumper"}]})
    check(empty["suggestions"] == {"shown": 0, "added": 0, "dismissed": 0, "left": 0, "looked_first": 0,
                                   "by_source": {}, "by_read": {}, "by_check": {}},
          "a case with no second_look: all zero", empty["suggestions"])
    broken = accuracy.compare({"zone_suggestions": "x"}, {"second_look": {"items": [None, 3, {"part": ""}], "decisions": "x", "dismissed": [None]}})
    check(broken["suggestions"]["shown"] == 0, "broken data counts as empty", broken["suggestions"])

    summed = accuracy.sum_suggestions([out, {"damage": {}}, {"suggestions": "junk"}, out])
    check((summed["shown"], summed["added"], summed["looked_first"]) == (12, 4, 2)
          and summed["by_source"]["zone"]["added"] == 4 and summed["by_check"]["unsure"]["left"] == 2,
          "sum_suggestions adds cases up; a row from before VERSION 2 adds 0", summed)

    # The signed-off case of the sections above, through record() and the admin page.
    db = SessionLocal()
    try:
        metrics = accuracy.record(db, W.rid)
    finally:
        db.close()
    s = (metrics or {}).get("suggestions") or {}
    # Left Tail Lamp + Boot Lid Hinge added (on the final list), Rear
    # Windscreen dismissed, Rear Bumper Beam left.
    check((s.get("shown"), s.get("added"), s.get("dismissed"), s.get("left"), s.get("looked_first")) == (4, 2, 1, 1, 2),
          "the signed-off case's record: its suggestions' outcomes", s)
    db = SessionLocal()
    try:
        row = db.get(AiAccuracy, W.rid)
        check(row is not None and row.version == accuracy.VERSION and FAKE_PLATE not in json.dumps(row.metrics),
              "stored with the current version; the plate in a reason never reaches the row")
    finally:
        db.close()
    r = W.admin.get("/admin/ai-accuracy")
    totals = r.json().get("totals", {}).get("suggestions") or {}
    check(r.status_code == 200 and totals.get("shown") == 4 and totals.get("by_source", {}).get("kit", {}).get("dismissed") == 1
          and r.json()["months"][0].get("suggestions", {}).get("added") == 2,
          "GET /admin/ai-accuracy sums them, in all and per month", totals)


# ---------------------------------------------------------------- old cases


def old_cases() -> None:
    # A case filed before contract 9: no second_look at all.
    r = W.owner.post("/reports", json={"draft": {"description": "Old case", "category": ["Vehicle Collision or Damage"],
                                                 "damage_summary": [{"part": "Tail Gate"}]}})
    old = r.json()["id"]
    r = W.owner.post(f"/reports/{old}/suggestions", json={"key": "Tail Gate", "action": "dismiss"})
    check(r.status_code == 404 and r.json().get("code") == "unknown_suggestion", "a case with no suggestions: 404 for any key")
    r = W.owner.put(f"/reports/{old}", json={"draft": {"description": "Old case, edited", "category": ["Vehicle Collision or Damage"]}})
    data, _ = stored(old)
    check(r.status_code == 200 and data.get("second_look") is None, "PUT /reports on it adds no second_look", data.get("second_look"))

    # A draft from before keys and decisions: items by part, a dismissed part name.
    rid = W.owner.post("/intake").json()["id"]
    set_data(rid, damage_summary=[{"part": "Rear Bumper"}], second_look={
        "status": "ok", "items": [{"part": "Boot Lid Lock", "photo_reference": None, "confidence": "High", "source": "kit"}],
        "dismissed": ["Rear Bumper Beam"]})
    r = W.owner.post(f"/intake/{rid}/suggestions", json={"key": "Boot Lid Lock", "action": "add"})
    data, _ = stored(rid)
    check(r.status_code == 200 and r.json()["item"]["part"] == "Boot Lid Lock" and len(data["damage_summary"]) == 2,
          "an old item without a key goes by its part")
    r = W.owner.post(f"/intake/{rid}/suggestions", json={"key": "Rear Bumper Beam", "action": "undo"})
    data, _ = stored(rid)
    check(r.status_code == 200 and data["second_look"]["dismissed"] == [] and data["second_look"]["decisions"][-1]["action"] == "undone",
          "undo of an old dismissal with no decision recorded: it leaves dismissed")
    # Filed with no second_look: none on the case.
    bare = W.owner.post("/intake").json()["id"]
    body = {"description": "x", "category": ["Vehicle Collision or Damage"], "damage_summary": [{"part": "Tail Gate"}]}
    r = W.owner.post(f"/intake/{bare}/finalize", json={"data": body})
    data, _ = stored(bare)
    check(r.status_code == 200 and data.get("second_look") is None, "a draft with no suggestions files with none")
    old_draft_saved = W.owner.put(f"/intake/{rid}/data", json={"data": {"description": "y", "category": [], "damage_summary": [{"part": "Rear Bumper"}]}})
    check(old_draft_saved.status_code == 200 and stored(rid)[0]["second_look"]["items"][0]["part"] == "Boot Lid Lock",
          "an autosave of an old draft keeps its second_look")


def main() -> int:
    init_db()
    W.owner, W.other, W.admin = TestClient(app), TestClient(app), TestClient(app)
    with W.owner, W.other, W.admin:
        W.owner_id = sign_in(W.owner, role="user", name="Sue Surveyor", sees_all_cases=True)
        sign_in(W.other, role="user", name="Olu Other")
        sign_in(W.admin, role="admin", name="Ada Admin")
        for name, fn in (
            ("item_from_suggestion and standing()", builder),
            ("POST /intake/{id}/suggestions: add, dismiss, undo", intake_decisions),
            ("the autosave never changes second_look", autosave_ignores_second_look),
            ("filing keeps and relabels the suggestions and evidence", lambda: (remember_decisions(), filing_relabel())),
            ("POST /reports/{id}/suggestions: the case page", case_decisions),
            ("AI accuracy VERSION 2: the suggestions' outcomes", accuracy_outcomes),
            ("old cases", old_cases),
        ):
            run_section(name, fn)
    print(f"\ntest_suggestion_decisions: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
