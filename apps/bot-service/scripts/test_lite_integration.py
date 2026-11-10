"""Contract 6's integration follow-ups: what agent T's named "Other / Not
Listed" parts and new parts (Window Regulator, Tyre, Wheel Arch Trim) need
from the files T could not edit.

    python scripts/test_lite_integration.py

Checked:
  - a re-read keeps a checked Other and still adds a NEW named Other
    (intake/pipeline.merge_redraft compares prefill.item_key, not the part);
  - the second look's zones hold the new parts, and its photo rule is the
    reading's (a damage-showing "other" photo counts without a part);
  - the Annex A scaffold (synthesize_annexes, no database) names and keys a
    named Other as prefill does, and a dismissed Other hides nothing else;
  - the damage-items API takes, cleans and keeps other_part_name, and
    removes / dismisses an Other by its name;
  - the start-up step brings imported past lines' columns up to date once;
  - the Telegram redraft keeps two Others' findings apart.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, every outside connection refused, no Gemini call. Part names
only, made up; no case data.
"""
import json
import os
import socket
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
_tmp = Path(tempfile.mkdtemp(prefix="lite-integration-test-"))
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = ""
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["MIN_FREE_DISK_GB"] = "0"

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

from fastapi.testclient import TestClient  # noqa: E402

import app.api.main as main  # noqa: E402
from app.ai import second_look as sl  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.conversation.flow import _carry_over_damage, _damage_lines  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.parts.history import PastCase, PastLine, import_case  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AppSetting, PastAnnexLine, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.reports.synthesize_annexes import synthesize_annexes_for_report  # noqa: E402
from app.reports.taxonomy import CANONICAL_PARTS, OTHER_PART  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"
NEW_PARTS = ("Window Regulator", "Tyre", "Wheel Arch Trim")


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


def other(name: str | None = None, **kw) -> dict:
    return {"part": OTHER_PART, "other_part_name": name, **kw}


def parts_of(items: list[dict]) -> list[tuple]:
    return [(it.get("part"), it.get("other_part_name")) for it in items]


# ---------------------------------------------------------------- sections


def redraft() -> None:
    current = {"damage_summary": [
        other("Rear air vent", human_verified=True),
        {"part": "Tail Lamp (side undetermined)", "human_verified": True},
    ]}
    new_ai = {"damage_summary": [
        other("Window regulator motor"),
        other("Rear air vent"),
        {"part": "Left Tail Lamp", "side_inferred": True},
        {"part": "Rear Bumper"},
    ]}
    merged = pipeline.merge_redraft(current, {}, new_ai)
    check(parts_of(merged["damage_summary"]) == [
        (OTHER_PART, "Rear air vent"), ("Tail Lamp (side undetermined)", None),
        (OTHER_PART, "Window regulator motor"), ("Rear Bumper", None)],
          "a new named Other is added beside a checked one; the same Other read again and a placed side of a "
          "checked undetermined part are not", json.dumps(parts_of(merged["damage_summary"])))
    merged = pipeline.merge_redraft({"damage_summary": [other(None, human_verified=True)]}, {},
                                    {"damage_summary": [other(None), other("Rear wiper arm")]})
    check(parts_of(merged["damage_summary"]) == [(OTHER_PART, None), (OTHER_PART, "Rear wiper arm")],
          "a checked unnamed Other hides another unnamed one (as before), not a named one",
          json.dumps(parts_of(merged["damage_summary"])))
    check(merged["damaged_parts"] == [OTHER_PART, OTHER_PART], "damaged_parts follows the merged list")


def second_look() -> None:
    zoned = {p for parts in sl.ZONES.values() for p in parts}
    new = [p for p in CANONICAL_PARTS if any(p.endswith(n) for n in NEW_PARTS)]
    check(len(new) == 12 and set(new) <= zoned, "every sided new part is in a zone", str(sorted(set(new) - zoned)))
    check(set(sl.ZONES) == {"front", "rear", "left", "right"} and all(p in CANONICAL_PARTS for p in zoned),
          "every zone part is a canonical part")
    front = {p for p in sl.ZONES["front"] if any(p.endswith(n) for n in NEW_PARTS)}
    rear = {p for p in sl.ZONES["rear"] if any(p.endswith(n) for n in NEW_PARTS)}
    check(front == {"Left Front Wheel Arch Trim", "Right Front Wheel Arch Trim"}
          and rear == {"Left Rear Wheel Arch Trim", "Right Rear Wheel Arch Trim"},
          "the ends hold their arch trims", f"{sorted(front)} {sorted(rear)}")
    for side in ("Left", "Right"):
        mine = {p for p in sl.ZONES[side.lower()] if any(p.endswith(n) for n in NEW_PARTS)}
        check(mine == {f"{side} {end} {n}" for end in ("Front", "Rear") for n in NEW_PARTS},
              f"the {side.lower()} side holds its regulators, tyres and arch trims", str(sorted(mine)))
    found, _ = sl.candidates(None, make=None, model=None, listed={"Left Front Door"}, dismissed=set(), sorter_parts=[])
    names = [c.part for c in found]
    check({"Left Front Window Regulator", "Left Front Tyre", "Left Front Wheel Arch Trim"} <= set(names)
          and "Right Front Window Regulator" not in names,
          "a damaged door makes its side's regulator, tyre and arch trim candidates", json.dumps(names))
    check(sl._zones_of("Wheel Arch Trim (side undetermined)") == {"front", "rear"}
          and sl._zones_of("Window Regulator (side undetermined)") == {"left", "right"},
          "an undetermined new part takes its sided names' zones")

    def photo(i, kind, part=None, damage=True, area=None):
        return SimpleNamespace(id=f"x{i}", kind=kind, area=area, part=part, shows_damage=damage, quality="good",
                               excluded=False)

    grid = [photo(0, kind, part, damage) for kind in ("damage", "other", "interior", "tyre", "overview", "scene")
            for part in (None, "Left Front Door") for damage in (True, False)]
    differ = [(p.kind, p.part, p.shows_damage) for p in grid
              if sl._is_evidence(p) != pipeline._is_damage_evidence(p)]
    check(not differ, "the second look's evidence rule is the reading's (pipeline._is_damage_evidence)", str(differ))
    photos = [photo(1, "other", damage=True, area="left"), photo(2, "other", damage=False, area="left"),
              photo(3, "interior", damage=True, area="left"), photo(4, "damage", part="Left Front Door", area="left")]
    picked = [p.id for p in sl.pick_photos(photos, zones={"left"}, read_ids=set(), candidate_parts=set(), item_photo_ids=set())]
    check(picked == ["x1", "x4"],
          "a damage-showing 'other' photo with no part is looked at again (a removed regulator); an undamaged one "
          "and a part-less interior one are not", json.dumps(picked))


def scaffold() -> None:
    vehicle = {"make": "Honda", "model": "Vezel"}
    damage = [
        {"part": "Rear Bumper"},
        other("left rear air vent"),
        other("Number plate lamp"),
        other(None),
        {"part": "Left Tail Lamp", "side_inferred": True},
        {"part": "Left Front Tyre"},
    ]
    rows = synthesize_annexes_for_report({"vehicle_info": vehicle, "damage_summary": damage})["annex_a"]["items"]
    got = [(r["part_name"], r["source_part"]) for r in rows]
    check(got[1:4] == [("Rear air vent LH", "Other / Not Listed: Left rear air vent"),
                       ("Number plate lamp", "Other / Not Listed: Number plate lamp"),
                       ("Other part (to be named)", OTHER_PART)],
          "a named Other is its own row by its name (side last) and source (prefill.item_source); an unnamed one "
          "is the 'to be named' row", json.dumps(got))
    check(not got[4][0].endswith(" LH") and got[4][1] == "Left Tail Lamp" and got[5][0] == "Front tyre LH",
          "an unconfirmed side prints without LH / RH; the new parts take the firm's wording", json.dumps(got))
    rows = synthesize_annexes_for_report({"vehicle_info": vehicle, "damage_summary": damage,
                                          "dismissed_parts": ["Other / Not Listed: left rear air vent"]})["annex_a"]["items"]
    sources = [r["source_part"] for r in rows]
    check("Rear Bumper" in sources and not any("air vent" in s for s in sources) and len(rows) == len(damage) - 1,
          "a dismissed Other hides only its own row -- read as a part name it would be the Rear Bumper", json.dumps(sources))


# ------------------------------------------------------------ the API


def _admin_client() -> tuple[TestClient, str]:
    db = SessionLocal()
    try:
        user = User(email=f"admin-{gen_id()}@example.com", username=f"admin{gen_id()[:6]}", name="Admin", role="admin",
                    password_hash="!")
        db.add(user)
        db.commit()
        token = create_session(db, user)
        db.commit()
        uid = user.id
    finally:
        db.close()
    c = TestClient(main.app, base_url=BASE)
    c.cookies.set(SESSION_COOKIE, token)
    return c, uid


def _new_case(owner_id: str, damage: list[dict]) -> str:
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status="confirmed", owner_id=owner_id,
                   data={"vehicle_info": {"make": "Honda", "model": "Vezel"}, "damage_summary": damage})
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def _stored(rid: str) -> dict:
    db = SessionLocal()
    try:
        return dict(db.get(Report, rid).data or {})
    finally:
        db.close()


def api() -> None:
    c, uid = _admin_client()
    rid = _new_case(uid, [{"part": "Rear Bumper", "human_verified": True}, other("Rear air vent", human_verified=True),
                          other("Number plate lamp")])
    base = f"/reports/{rid}/damage-items"

    r = c.patch(f"{base}/2", json={"other_part_name": "  boot   lamp "})
    body = r.json()
    check(r.status_code == 200 and body["item"]["other_part_name"] == "Boot lamp",
          "PATCH names an Other, cleaned as the reader's (spaces, capital)", f"{r.status_code} {r.text[:200]}")
    sources = [row["source_part"] for row in body["annex_a"]["items"]]
    check("Other / Not Listed: Boot lamp" in sources and "Other / Not Listed: Number plate lamp" not in sources,
          "Annex A follows the new name", json.dumps(sources))

    r = c.patch(f"{base}/1", json={"condition": "Cracked"})
    check(r.status_code == 200 and r.json()["item"]["other_part_name"] == "Rear air vent" and r.json()["item"]["condition"] == "Cracked",
          "another edit keeps the name", r.text[:200])
    r = c.patch(f"{base}/2", json={"part": "Bonnet"})
    check(r.status_code == 200 and r.json()["item"]["part"] == "Bonnet" and r.json()["item"].get("other_part_name") is None,
          "a part renamed off Other drops the name", r.text[:200])
    r = c.patch(f"{base}/0", json={"other_part_name": "Anything"})
    check(r.status_code == 200 and r.json()["item"].get("other_part_name") is None, "a listed part takes no name", r.text[:200])

    r = c.post(base, json={"part": OTHER_PART, "other_part_name": "rear wiper arm"})
    check(r.status_code == 200 and r.json()["item"]["other_part_name"] == "Rear wiper arm"
          and any(row["part_name"] == "Rear wiper arm" for row in r.json()["annex_a"]["items"]),
          "POST adds a named Other with its own Annex A row", r.text[:200])
    r = c.post(base, json={"part": "Boot Lid", "other_part_name": "x"})
    check(r.status_code == 200 and r.json()["item"].get("other_part_name") is None, "POST of a listed part drops a name", r.text[:200])

    items = _stored(rid)["damage_summary"]
    vent = next(i for i, it in enumerate(items) if it.get("other_part_name") == "Rear air vent")
    r = c.delete(f"{base}/{vent}")
    data = _stored(rid)
    sources = [row["source_part"] for row in data["annex_a"]["items"] if not row.get("stale")]
    check(r.status_code == 200 and data["dismissed_parts"] == ["Other / Not Listed: rear air vent"],
          "removing a named Other while another Other is listed dismisses it by its name", json.dumps(data.get("dismissed_parts")))
    check("Other / Not Listed: Rear wiper arm" in sources and "Rear Bumper" in sources
          and not any("air vent" in s for s in sources),
          "only its own rows go: the other Other's and the Rear Bumper's stay", json.dumps(sources))

    r = c.post(base, json={"part": OTHER_PART, "other_part_name": "Rear  air vent"})
    data = _stored(rid)
    check(r.status_code == 200 and data["dismissed_parts"] == []
          and any(row["source_part"] == "Other / Not Listed: Rear air vent" for row in data["annex_a"]["items"]),
          "adding it again takes it out of dismissed_parts (any case or spacing) and its row comes back",
          json.dumps(data.get("dismissed_parts")))

    rid = _new_case(uid, [other(None), other("Rear wiper arm")])
    r = c.delete(f"/reports/{rid}/damage-items/0")
    data = _stored(rid)
    check(r.status_code == 200 and data["dismissed_parts"] == [OTHER_PART]
          and [row["source_part"] for row in data["annex_a"]["items"]] == ["Other / Not Listed: Rear wiper arm"],
          "an unnamed Other is dismissed as before, beside a named one that stays", json.dumps(data.get("dismissed_parts")))
    rid = _new_case(uid, [other("Rear wiper arm"), other("Rear wiper arm")])
    c.delete(f"/reports/{rid}/damage-items/0")
    check(_stored(rid).get("dismissed_parts") == [], "an Other still listed under the same name is not dismissed")


# ------------------------------------------------------------ start-up step


def startup_step() -> None:
    key = "upgrade:past_lines_refresh_v1"
    db = SessionLocal()
    try:
        check(db.get(AppSetting, key) is not None, "init_db ran the past-lines refresh (its done mark is there)")
        line = PastLine(line_no=1, section="list", item="Front door glass regulator mechanism", condition="Bent",
                        remarks="", qty=1, qty_raw="1", workshop_est=210, discount=0.2, adjusted_cost=168)
        import_case(db, PastCase(ref="CL 92001", plate="TEST01", make="Honda", model="HR-V", make_model_raw="Honda HR-V 1.5",
                                 registered=None, surveyed="2025-01-01", lines=[line]), "CL 92001.xlsx")
        # As imported before 10 Nov: the regulator was the door's, the model "HR".
        for ln in db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 92001"):
            ln.family, ln.model = "Front Door", "HR"
        db.delete(db.get(AppSetting, key))
        db.commit()
    finally:
        db.close()

    def columns():
        db = SessionLocal()
        try:
            return [(ln.family, ln.model) for ln in db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 92001")]
        finally:
            db.close()

    init_db()
    check(columns() == [("Front Window Regulator", "HR-V")],
          "on a database that hasn't had it, start-up re-reads the stored family and model", str(columns()))
    db = SessionLocal()
    try:
        for ln in db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 92001"):
            ln.family = "Front Door"
        db.commit()
    finally:
        db.close()
    init_db()
    check(columns() == [("Front Door", "HR-V")], "and only once: the next start leaves the lines alone", str(columns()))


# ------------------------------------------------------------ the Telegram redraft


def telegram_redraft() -> None:
    previous = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [
        other("Rear air vent", photo_reference="P01", severity="Minor"),
        other("Rear wiper arm", photo_reference="P02", severity="Severe"),
    ]})
    draft = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [
        other("Rear wiper arm"), other("Rear air vent")]})
    out = _carry_over_damage(draft, previous, "the dent on the bumper is bigger")
    got = [(it.other_part_name, it.photo_reference, it.severity) for it in out.damage_summary]
    check(got == [("Rear wiper arm", "P02", "Severe"), ("Rear air vent", "P01", "Minor")],
          "a damage correction fills each Other's blanks from the same Other, not the other one", json.dumps(got))
    lines = _damage_lines(out)
    check(any(line.endswith("Other / Not Listed: Rear wiper arm") for line in lines),
          "the chat summary names the Other", json.dumps(lines))


def main_() -> int:
    init_db()
    for name, fn in (
        ("a re-read and the named Others", redraft),
        ("the second look's zones and photos", second_look),
        ("the Annex A scaffold", scaffold),
        ("the damage-items API", api),
        ("the start-up refresh of past lines", startup_step),
        ("the Telegram redraft", telegram_redraft),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main_())
