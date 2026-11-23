"""Contract 9's review fixes (plans/2026-11-15-evidence/review-c9.md, the
BACKEND findings):

  M1  no decision on a suggestion -- nor the item an add put on the list
      -- is lost to another save overlapping it. Each path the review's
      race probe found, with a decision landing from another person
      between the save's read and its write:
        PUT /intake/{id}/checked, PATCH / POST / DELETE
        /reports/{id}/damage-items, PUT /reports/{id}/checked, POST
        /reports/{id}/ai-enrich (the write lock taken before the read: the
        decision lands after the save, on top of it);
        PUT /reports/{id} (renders with no lock: what the decision changed
        is kept, or 409 changed_meanwhile when the save sent the list);
        sign-off (never signs over a decision made during its render: 409
        changed_meanwhile, nothing signed, the case's PDF rendered again);
  L1  an added suggestion's bbox_2d is the AI's box only, never the
      sorter's;
  L2  POST /reports keeps no browser-sent second_look;
  L3  second_look._store keeps the decided suggestions after the open
      ones (outside the 16): "Show decided" and undo work after a re-read;
      a part listed by hand stays out; each key once; AI accuracy's shown
      count unchanged; the re-check neither asks about them nor reorders
      them;
  L4  the re-check's run-log photos are renumbered at filing; a reason
      quoting a photo label leaves it out;
  L5  a close-up or grid copy used again is touched, so the clean-up's
      age sweep doesn't take it before the read that sends it.

    python scripts/test_c9_review.py

Offline, no AI call. Builds on test_suggestion_decisions (its temporary
storage and database, its stand-in PDF renderer, its draft with four
suggestions); the photos are coloured squares.
"""
import copy
import io
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# First: it sets the environment (temporary storage and database) the app
# is imported with, and replaces the PDF renderer.
import test_suggestion_decisions as T  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.api.annexes as annexes_api  # noqa: E402
import app.api.intake as intake_api  # noqa: E402
import app.api.main as main_api  # noqa: E402
from app.ai import check as recheck  # noqa: E402
from app.ai import grid, runs, second_look  # noqa: E402
from app.intake import cleanup, crops  # noqa: E402
from app.reports import accuracy, suggestions  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, Report  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

W, stored, set_data, check = T.W, T.stored, T.set_data, T.check

# How long a racing request is given to land while the save it races is
# between its read and its write. A save holding the write lock makes it
# wait for the save's commit (then it lands on top); one that doesn't lets
# it land in between, as in the review's probe.
RACE_WAIT = 1.0
# A save that renders with no lock: the racing request lands during the
# render, however long it takes.
RENDER_WAIT = 15.0


class Racing:
    """A request on another thread -- another person -- started from inside
    the save it races."""

    def __init__(self, send):
        self.out: dict = {}
        self.thread = threading.Thread(target=self._run, args=(send,), daemon=True)

    def _run(self, send) -> None:
        try:
            self.out["response"] = send()
        except Exception as exc:  # re-raised by result()
            self.out["error"] = exc

    def start(self, wait: float) -> "Racing":
        self.thread.start()
        self.thread.join(wait)
        return self

    def result(self):
        self.thread.join(30)
        if "error" in self.out:
            raise self.out["error"]
        return self.out.get("response")


@contextmanager
def meanwhile(module, name: str, send, wait: float = RACE_WAIT):
    """While the block runs, the first call of module.name starts `send` on
    another thread and waits up to `wait` for it before going on. Yields a
    dict whose "race" is that Racing."""
    real = getattr(module, name)
    race: dict = {}

    def hooked(*args, **kwargs):
        if "race" not in race:
            # Kept before it starts: the racing request may call this too
            # (an add syncs Annex A), and must not start another.
            race["race"] = Racing(send)
            race["race"].start(wait)
        return real(*args, **kwargs)

    setattr(module, name, hooked)
    try:
        yield race
    finally:
        setattr(module, name, real)


def decide_as(client: TestClient, surface: str, rid: str, key: str, action: str):
    return lambda: client.post(f"/{surface}/{rid}/suggestions", json={"key": key, "action": action})


def parts(data: dict) -> list[str]:
    return [it["part"] for it in data.get("damage_summary") or []]


def decisions(data: dict) -> list[tuple[str, str]]:
    return [(d["key"], d["action"]) for d in (data.get("second_look") or {}).get("decisions") or []]


def pdf_path(rid: str) -> str | None:
    db = SessionLocal()
    try:
        return db.get(Report, rid).pdf_path
    finally:
        db.close()


def brief(r) -> str:
    return f"{r.status_code} {r.text[:200]}"


# ---------------------------------------------------------------- M1: a draft


def intake_checked_race() -> None:
    T.make_draft()
    rid = W.rid
    with meanwhile(intake_api, "checked_change", decide_as(W.admin, "intake", rid, "Boot Lid Hinge", "dismiss")) as race:
        r = W.owner.put(f"/intake/{rid}/checked", json={"set": {"vehicle_info.make": {"na": False}}})
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200, "the field tick and the dismissal: both 200",
          (brief(r), brief(inner)))
    check("vehicle_info.make" in (data.get("checked") or {}) and data["second_look"]["dismissed"] == ["Boot Lid Hinge"]
          and decisions(data) == [("Boot Lid Hinge", "dismissed")],
          "PUT /intake/{id}/checked: a dismissal made while it ran is kept, and the tick too",
          (data.get("checked"), data["second_look"]["dismissed"], decisions(data)))
    # Filed for the case-page races below: four suggestions, Boot Lid
    # Hinge dismissed, the list Rear Bumper.
    body = copy.deepcopy(data)
    body["report_id"] = None
    r = W.owner.post(f"/intake/{rid}/finalize", json={"data": body})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- M1: a case


def damage_item_races() -> None:
    rid = W.rid
    # PATCH: "Verified" ticked on one row while someone adds a suggestion.
    with meanwhile(main_api, "price_book_for_case", decide_as(W.admin, "reports", rid, "Rear Bumper Beam", "add")) as race:
        r = W.owner.patch(f"/reports/{rid}/damage-items/0", json={"human_verified": True})
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200 and inner.json()["item"], "the tick and the add: both 200",
          (brief(r), brief(inner)))
    check(parts(data) == ["Rear Bumper", "Rear Bumper Beam"] and data["damage_summary"][0].get("human_verified") is True
          and ("Rear Bumper Beam", "added") in decisions(data) and data["damaged_parts"] == ["Rear Bumper", "Rear Bumper Beam"],
          "PATCH damage-items: the added item and its decision kept, the tick too", (parts(data), decisions(data)))
    check(data["annex_a"] == inner.json()["annex_a"], "...and Annex A as the add left it (synced to both)")

    # POST: a part added by hand while someone dismisses a suggestion.
    with meanwhile(main_api, "price_book_for_case", decide_as(W.admin, "reports", rid, "Rear Windscreen", "dismiss")) as race:
        r = W.owner.post(f"/reports/{rid}/damage-items", json={"part": "Bonnet"})
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200, "the hand add and the dismissal: both 200", (brief(r), brief(inner)))
    check("Bonnet" in parts(data) and "Rear Windscreen" in data["second_look"]["dismissed"]
          and decisions(data)[-1:] == [("Rear Windscreen", "dismissed")],
          "POST damage-items: the dismissal kept, the hand-added part too", (parts(data), data["second_look"]["dismissed"]))

    # DELETE: a part removed while someone undoes that dismissal.
    at = parts(data).index("Bonnet")
    with meanwhile(main_api, "price_book_for_case", decide_as(W.admin, "reports", rid, "Rear Windscreen", "undo")) as race:
        r = W.owner.delete(f"/reports/{rid}/damage-items/{at}")
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200, "the removal and the undo: both 200", (brief(r), brief(inner)))
    check("Bonnet" not in parts(data) and "Bonnet" in (data.get("dismissed_parts") or [])
          and "Rear Windscreen" not in data["second_look"]["dismissed"] and decisions(data)[-1:] == [("Rear Windscreen", "undone")],
          "DELETE damage-items: the undo kept, the removal too", (parts(data), data["second_look"]["dismissed"], decisions(data)))


def case_checked_and_enrich_races() -> None:
    rid = W.rid
    # A field not confirmed yet (the make was, on the draft): a save that
    # changes nothing writes nothing, so it couldn't lose anything.
    with meanwhile(annexes_api, "checked_change", decide_as(W.admin, "reports", rid, "Left Tail Lamp", "add")) as race:
        r = W.owner.put(f"/reports/{rid}/checked", json={"set": {"vehicle_info.model": {"na": False}}})
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200, "the field tick and the add: both 200", (brief(r), brief(inner)))
    check("vehicle_info.model" in (data.get("checked") or {}) and "Left Tail Lamp" in parts(data)
          and decisions(data)[-1:] == [("Left Tail Lamp", "added")],
          "PUT /reports/{id}/checked: the added item and its decision kept, the tick too", (parts(data), decisions(data)))

    # Make and model with no vehicle summary yet: "fill derived fields" has
    # something to write.
    set_data(rid, vehicle_info={**(data.get("vehicle_info") or {}), "make": "Honda", "model": "Vezel"}, vehicle_details=None)
    with meanwhile(main_api, "_enrich_single_report_data", decide_as(W.admin, "reports", rid, "Left Tail Lamp", "undo")) as race:
        r = W.owner.post(f"/reports/{rid}/ai-enrich")
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200, "fill derived fields and the undo: both 200", (brief(r), brief(inner)))
    check("Left Tail Lamp" not in parts(data) and decisions(data)[-1:] == [("Left Tail Lamp", "undone")]
          and "Vezel" in (data.get("vehicle_details") or ""),
          "ai-enrich: the undo kept (the item off the list again)", (parts(data), decisions(data)))


def put_report_races() -> None:
    rid = W.rid
    # An add while the edit's PDF renders (no lock: seconds).
    with meanwhile(main_api, "_render_after_change", decide_as(W.admin, "reports", rid, "Left Tail Lamp", "add"),
                   wait=RENDER_WAIT) as race:
        r = W.owner.put(f"/reports/{rid}", json={"draft": {"description": "Edited while a part was added"}})
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200 and inner.json()["item"], "the edit and the add: both 200",
          (brief(r), brief(inner)))
    check(data["description"] == "Edited while a part was added" and "Left Tail Lamp" in parts(data)
          and "Left Tail Lamp" in data["damaged_parts"] and decisions(data)[-1:] == [("Left Tail Lamp", "added")],
          "PUT /reports: an add made during its render keeps its item on the list, and the edit is saved",
          (parts(data), decisions(data)))
    check(data["annex_a"] == inner.json()["annex_a"] and data["annex_c"] == inner.json()["annex_c"],
          "...Annex A and C as the add synced them")
    check(r.json()["pdf_url"] is None and pdf_path(rid) is None,
          "...and the PDF rendered without the part is not kept: the next download renders it again", pdf_path(rid))

    # A dismissal only (the PDF prints no suggestions): kept, the PDF too.
    with meanwhile(main_api, "_render_after_change", decide_as(W.admin, "reports", rid, "Rear Windscreen", "dismiss"),
                   wait=RENDER_WAIT) as race:
        r = W.owner.put(f"/reports/{rid}", json={"draft": {"description": "Edited while one was dismissed"}})
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 200 and inner.status_code == 200 and "Rear Windscreen" in data["second_look"]["dismissed"]
          and data["description"] == "Edited while one was dismissed" and r.json()["pdf_url"] and pdf_path(rid),
          "a dismissal during the render: kept, and the rendered PDF kept (nothing it prints changed)", brief(r))

    # The save sent the list itself while a decision changed it: neither
    # side can be kept whole -- 409, the decision stands.
    sent_list = copy.deepcopy(data["damage_summary"])
    with meanwhile(main_api, "_render_after_change", decide_as(W.admin, "reports", rid, "Left Tail Lamp", "undo"),
                   wait=RENDER_WAIT) as race:
        r = W.owner.put(f"/reports/{rid}", json={"draft": {"description": "Not saved", "damage_summary": sent_list}})
    inner = race["race"].result()
    data, _ = stored(rid)
    check(r.status_code == 409 and r.json().get("code") == "changed_meanwhile" and inner.status_code == 200,
          "PUT /reports that sent the list while an undo changed it: 409 changed_meanwhile", brief(r))
    check(data["description"] == "Edited while one was dismissed" and "Left Tail Lamp" not in parts(data)
          and decisions(data)[-1:] == [("Left Tail Lamp", "undone")] and pdf_path(rid) is None,
          "...nothing of the save kept, the undo stands, the PDF rendered with the save's edit not kept",
          (data["description"], parts(data), pdf_path(rid)))


def sign_off_race() -> None:
    from auth_helpers import mark_must_haves_na

    rid = W.rid
    body = {"terms_accepted": True, "open_items_ack": True}
    # Sign-off needs the must-have facts filled in or N/A (review.MUST_HAVE).
    mark_must_haves_na(W.admin, rid)
    with meanwhile(main_api, "_render_after_change", decide_as(W.owner, "reports", rid, "Left Tail Lamp", "add"),
                   wait=RENDER_WAIT) as race:
        r = W.admin.post(f"/reports/{rid}/sign-off", json=body)
    inner = race["race"].result()
    data, _ = stored(rid)
    db = SessionLocal()
    try:
        status = db.get(Report, rid).status
    finally:
        db.close()
    check(inner.status_code == 200 and r.status_code == 409 and r.json().get("code") == "changed_meanwhile",
          "an add during the sign-off's render: the sign-off refused, 409 changed_meanwhile", (brief(r), brief(inner)))
    check(status != "Signed Off" and (data.get("sign_off") or {}).get("status") != "Signed Off"
          and "Left Tail Lamp" in parts(data) and decisions(data)[-1:] == [("Left Tail Lamp", "added")]
          and pdf_path(rid) is None,
          "...nothing signed, the add stands, and the PDF rendered with the signature is not kept", (status, parts(data)))
    r = W.admin.post(f"/reports/{rid}/sign-off", json=body)
    data, _ = stored(rid)
    check(r.status_code == 200 and data["sign_off"]["status"] == "Signed Off" and "Left Tail Lamp" in parts(data),
          "signed again, now with the part on the list", brief(r))


# ---------------------------------------------------------------- L1, L2


def builder_box_kind() -> None:
    sort_first = T.suggestion("Left Tail Lamp", photo="@a", evidence=[
        {"photo": "@a", "box": [100, 100, 300, 300], "box_kind": "sort", "by": "sort", "reason": None,
         "confidence": None, "img_w": None, "img_h": None},
        T.ev("@b", [10, 20, 300, 400])])
    item = suggestions.item_from_suggestion(sort_first)
    check(item["photo_reference"] == "@a" and item["bbox_2d"] is None and item["evidence"][0]["box"] == [100, 100, 300, 300]
          and item["evidence"][0]["box_kind"] == "sort",
          "the best evidence boxed by the sorter: no bbox_2d (no \"AI box\"); the sorter's box stays on its entry", item)
    ai_first = suggestions.item_from_suggestion(T.suggestion("Left Tail Lamp", photo="@b", evidence=[T.ev("@b", [10, 20, 300, 400])]))
    check(ai_first["bbox_2d"] == [10, 20, 300, 400], "boxed by the AI: its box is bbox_2d, as before")


def create_ignores_second_look() -> None:
    made_up = {"status": "ok", "items": [T.suggestion("Bonnet")], "dismissed": ["Tail Gate"],
               "decisions": [{"key": "Bonnet", "part": "Bonnet", "action": "added", "by_id": "someone", "by_name": "Someone Else",
                              "at": "2026-11-16T00:00:00+00:00", "looked": True, "where": "case"}]}
    r = W.owner.post("/reports", json={"draft": {"description": "Typed in", "category": ["Vehicle Collision or Damage"],
                                                 "second_look": made_up}})
    data, _ = stored(r.json()["id"]) if r.status_code == 200 else ({}, None)
    check(r.status_code == 200 and data.get("second_look") is None,
          "POST /reports: a second_look in the body is not kept (no made-up suggestions or decisions)", brief(r))


# ---------------------------------------------------------------- L3


def decided_kept_over_a_reread() -> None:
    T.make_draft()
    rid = W.rid
    for key, action in (("Left Tail Lamp", "add"), ("Boot Lid Hinge", "dismiss"), ("Rear Windscreen", "dismiss")):
        r = T.decide(W.owner, "intake", key, action)
        assert r.status_code == 200, r.text
    data, _ = stored(rid)
    # Rear Bumper Beam put on the list by hand (the wizard's own row): no decision on it.
    set_data(rid, damage_summary=data["damage_summary"] + [{"part": "Rear Bumper Beam"}])
    before = {it["key"]: it for it in stored(rid)[0]["second_look"]["items"]}
    # A re-read with the second look off: the zone reads found Left Tail
    # Lamp and Boot Lid Hinge again (ai_draft["zone_suggestions"]).
    second_look._store(rid, {"status": "skipped", "note": second_look.NOTE_OFF})
    data, _ = stored(rid)
    look = data["second_look"]
    keys_ = [it["key"] for it in look["items"]]
    open_items, settled = second_look.split_settled(look["items"], data)
    check(open_items == [] and [it["key"] for it in settled] == ["Left Tail Lamp", "Boot Lid Hinge", "Rear Windscreen"]
          and len(keys_) == len(set(keys_)),
          "after a re-read the decided ones stay in items, each once: the added, the dismissed, one the read didn't find again",
          keys_)
    check("Rear Bumper Beam" not in keys_, "a part listed by hand (no decision) stays out, as before")
    lamp = next(it for it in look["items"] if it["key"] == "Left Tail Lamp")
    check(lamp["evidence"] == before["Left Tail Lamp"]["evidence"] and lamp["check"] == before["Left Tail Lamp"]["check"],
          "kept as the person decided on it: its evidence and re-check answer")

    # Shown counts a key once, whether it is in items or only in decisions.
    full = accuracy._suggestions({}, data)
    without = accuracy._suggestions({}, {**data, "second_look": {**look, "items": open_items}})
    check(full["shown"] == without["shown"] == 3 and full["dismissed"] == 2 and full["added"] == 1,
          "AI accuracy: shown not counted twice", (full["shown"], without["shown"]))

    # Undo a dismissal after the re-read: the card comes back.
    r = T.decide(W.owner, "intake", "Boot Lid Hinge", "undo")
    data, _ = stored(rid)
    open_items, settled = second_look.split_settled(data["second_look"]["items"], data)
    check(r.status_code == 200 and [it["key"] for it in open_items] == ["Boot Lid Hinge"]
          and [it["key"] for it in settled] == ["Left Tail Lamp", "Rear Windscreen"],
          "undo after a re-read: the suggestion is open again (its card back), with its evidence",
          [it["key"] for it in data["second_look"]["items"]])
    second_look._store(rid, {"status": "skipped"})
    data, _ = stored(rid)
    keys_ = [it["key"] for it in data["second_look"]["items"]]
    check(keys_ == ["Boot Lid Hinge", "Left Tail Lamp", "Rear Windscreen"],
          "another re-read: the open one first, the decided after it", keys_)

    # The re-check asks about the open one only and leaves the decided ones
    # where and as they are.
    plan = recheck._plan(rid)
    check(plan is not None and plan.total == 1, "the re-check plans for the open suggestions only",
          plan and plan.total)
    before_items = copy.deepcopy(data["second_look"]["items"])
    answer = {"verdict": "no", "p": 0.1, "by": "decision", "model": "gemini-decision-1", "reason": None}
    recheck._store(rid, recheck._check_run("ok", by="decision", model="gemini-decision-1", calls=1),
                   {"Boot Lid Hinge": answer}, {})
    items = stored(rid)[0]["second_look"]["items"]
    check([it["key"] for it in items] == keys_ and items[0]["check"] == answer and items[1:] == before_items[1:],
          "...and its answer goes on the open one; the decided ones after it, untouched", [it["key"] for it in items])


# ---------------------------------------------------------------- L4


def check_run_relabelled() -> None:
    T.make_draft()
    rid = W.rid
    ids = [p["id"] for p in W.photos]
    db = SessionLocal()
    try:
        db.add(AiRun(report_id=rid, kind=runs.CHECK_KIND, status="ok",
                     photos_sent=[f"@{ids[0]}", f"@{ids[1]}", f"@{ids[3]}"]))
        db.add(AiRun(report_id=rid, kind=runs.CHECK_KIND, status="ok", photos_sent=["P02"]))
        db.commit()
    finally:
        db.close()
    r = W.owner.delete(f"/intake/{rid}/photos/{ids[1]}")
    assert r.status_code == 200, r.text
    body = copy.deepcopy(stored(rid)[0])
    body["report_id"] = None
    r = W.owner.post(f"/intake/{rid}/finalize", json={"data": body})
    db = SessionLocal()
    try:
        sent = sorted((row.photos_sent for row in db.query(AiRun).filter(AiRun.report_id == rid, AiRun.kind == runs.CHECK_KIND)),
                      key=len)
    finally:
        db.close()
    check(r.status_code == 200 and sent == [["P02"], ["P01", "P03"]],
          "filing: the re-check's photos as the case's P-numbers, a deleted photo's dropped; an old row's labels as they were",
          sent)
    lite = recheck.parse_lite(json.dumps({"findings": [
        {"ref": "F1", "damaged": "yes", "photo_reference": "P03", "reason": "lens cracked, see P03"}]}), ["F1"], ["P03"])
    check(lite["F1"]["reason"] == "lens cracked, see", "a lite re-check reason quoting a photo label: the label left out",
          lite["F1"]["reason"])


# ---------------------------------------------------------------- L5


def derived_copies_touched() -> None:
    folder = Path(T._tmp, "l5")
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / "src.jpg"
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (120, 60, 30)).save(buf, "JPEG")
    src.write_bytes(buf.getvalue())
    old = time.time() - 30 * 24 * 3600

    crop = crops.make_crop([str(src)], [100, 100, 400, 400])
    copy_ = grid.grid_path(str(src))
    stale = Path(crops.crop_dir()) / "left-over.jpg"
    stale.write_bytes(b"x")
    for path in (crop, copy_, stale):
        os.utime(path, (old, old))
    check(crop and crops.make_crop([str(src)], [100, 100, 400, 400]) == crop and grid.grid_path(str(src)) == copy_,
          "a close-up and a grid copy made before are used again")
    check(Path(crop).stat().st_mtime > time.time() - 60 and Path(copy_).stat().st_mtime > time.time() - 60,
          "...and touched when used")
    removed = cleanup._sweep_ai_derived(datetime.now(timezone.utc))
    check(Path(crop).exists() and Path(copy_).exists() and not stale.exists() and removed == 1,
          "the age sweep keeps the ones just used (and still removes an old one nobody used)", removed)


def main() -> int:
    init_db()
    W.owner, W.other, W.admin = TestClient(T.app), TestClient(T.app), TestClient(T.app)
    with W.owner, W.other, W.admin:
        W.owner_id = sign_in(W.owner, role="user", name="Sue Surveyor", sees_all_cases=True)
        sign_in(W.other, role="user", name="Olu Other")
        sign_in(W.admin, role="admin", name="Ada Admin")
        for name, fn in (
            ("M1: PUT /intake/{id}/checked during a decision", intake_checked_race),
            ("M1: PATCH / POST / DELETE damage-items during a decision", damage_item_races),
            ("M1: PUT /reports/{id}/checked and ai-enrich during a decision", case_checked_and_enrich_races),
            ("M1: PUT /reports with a decision during its render", put_report_races),
            ("M1: sign-off with a decision during its render", sign_off_race),
            ("L1: an added suggestion's box is the AI's only", builder_box_kind),
            ("L2: POST /reports keeps no second_look", create_ignores_second_look),
            ("L3: decided suggestions kept over a re-read", decided_kept_over_a_reread),
            ("L4: the re-check's photos renumbered at filing; labels out of reasons", check_run_relabelled),
            ("L5: derived copies touched when used again", derived_copies_touched),
        ):
            T.run_section(name, fn)
    print(f"\ntest_c9_review: {T.PASSED[0]} checks passed, {len(T.FAILURES)} failed")
    for section, label in T.FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if T.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
