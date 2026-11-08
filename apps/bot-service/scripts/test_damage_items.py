"""The damage-item routes of the case page's tab 1 (app/api/main.py,
2026-11-04): PATCH widened to the part, damage type, severity and photo,
POST adds a part, DELETE removes one and remembers it as dismissed; each
answers the damage list and the Annex A/C that follow it
(app/parts/prefill.sync_annex_a).

    python scripts/test_damage_items.py [example_case folder]

Offline: the API through TestClient with scripts/test_annexes.py's render
stand-ins and past-report fixtures (the four real CL reports when the
example_case folder is given).
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
# Sets the temporary storage/database, replaces the renderers, lends the
# past-report fixtures.
import test_annexes as annexes  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.api.main import app  # noqa: E402
from app.parts.history import normalise_item  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import AuditEntry, Report  # noqa: E402
from app.storage.files import tmp_dir  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

LOCKED = "Report is signed off and locked. Reopen it before editing."


def ok(label: str) -> None:
    print(f"    ok   {label}")


def jpeg(name: str, color: tuple) -> str:
    path = Path(tmp_dir(), name)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), color).save(path, "JPEG")
    return str(path)


def new_case(client: TestClient, plate: str, photos: int = 2) -> str:
    r = client.post("/reports", json={"draft": {
        "category": ["Vehicle Collision or Damage"], "description": "Rear-ended at a junction",
        "vehicle_info": {"plate_number": plate, "make": "Honda", "model": "Vezel"},
        "damage_summary": [{"part": "Tail Gate", "damage_type": "Dent", "severity": "Severe", "photo_reference": "P01", "bbox_2d": [100, 100, 500, 600]},
                           {"part": "Rear Bumper", "damage_type": "Deformation"}],
    }})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    db = SessionLocal()
    try:
        # Photos straight onto the row: the routes only count them.
        db.get(Report, rid).photo_paths = [jpeg(f"{plate}-{i}.jpg", (40 * i, 90, 160)) for i in range(photos)]
        db.commit()
    finally:
        db.close()
    return rid


def rows_of(res: dict) -> list[dict]:
    return res["annex_a"]["items"]


def set_signed_off(rid: str, signed: bool) -> None:
    annexes.set_signed_off(rid, signed)


def main() -> int:
    admin, surveyor, plain, other = TestClient(app), TestClient(app), TestClient(app), TestClient(app)
    with admin, surveyor, plain, other:
        sign_in(admin, role="admin", name="Ada Admin")
        sign_in(surveyor, role="user", name="Sue Seesall", sees_all_cases=True)
        sign_in(plain, role="user", name="Pat Plain")
        sign_in(other, role="user", name="Olu Other")
        r = admin.post("/parts-history/import", files=annexes.past_report_files())
        assert r.status_code == 200 and all("lines" in x for x in r.json()["results"]), r.text

        print("PATCH /damage-items/{i}")
        rid = new_case(surveyor, "SMV7530X")
        # Price Annex A from past cases first, then type one price by hand.
        first = surveyor.post(f"/reports/{rid}/annex-a/prefill").json()
        assert any(i["prefilled"] and i["workshop_est"] > 0 for i in rows_of(first))
        typed = [{**rows_of(first)[0], "workshop_est": 777.0, "origin": "typed"}] + rows_of(first)[1:]
        assert surveyor.put(f"/reports/{rid}/annex-a", json={"items": typed}).status_code == 200
        typed_name = rows_of(first)[0]["part_name"]

        r = surveyor.patch(f"/reports/{rid}/damage-items/0", json={"damage_type": "Crack", "severity": "Minor", "human_verified": True, "oem_part_number": " 74800-T7A "})
        assert r.status_code == 200, r.text
        out = r.json()
        item = out["item"]
        assert (item["part"], item["damage_type"], item["severity"], item["human_verified"], item["oem_part_number"], item["photo_reference"], item["bbox_2d"]) == \
            ("Tail Gate", "Crack", "Minor", True, "74800-T7A", "P01", [100, 100, 500, 600]), item
        assert out["item_index"] == 0 and out["damage_summary"][0] == item and len(out["damage_summary"]) == 2
        assert set(out) >= {"id", "item_index", "item", "damage_summary", "damaged_parts", "annex_a", "annex_c"}
        assert out["damaged_parts"] == [d["part"] for d in out["damage_summary"]] == ["Tail Gate", "Rear Bumper"]
        kept = next(i for i in rows_of(out) if i["part_name"] == typed_name)
        assert kept["workshop_est"] == 777.0 and kept["origin"] == "typed", kept
        tailgate = next(i for i in rows_of(out) if normalise_item(i["part_name"]) == "tailgate")
        # The item has no condition (filed before the firm's words): the row
        # prints the firm word for its new damage type.
        assert tailgate["condition"] == "Cracked" if tailgate["workshop_est"] == 0 else True
        assert out["annex_c"]["adjusted_total"] == out["annex_a"]["total_adjusted_cost"]
        ok("PATCH changes damage type, severity, the tick and the OEM number; answers the list and Annex A/C; the typed row is kept")

        r = surveyor.patch(f"/reports/{rid}/damage-items/0", json={"photo_reference": "p02"})
        assert r.status_code == 200 and r.json()["item"]["photo_reference"] == "P02" and r.json()["item"]["bbox_2d"] is None, r.text
        r = surveyor.patch(f"/reports/{rid}/damage-items/0", json={"photo_reference": None})
        assert r.status_code == 200 and r.json()["item"]["photo_reference"] is None, r.text
        ok("a new photo drops the AI's box (drawn on the old one); null clears the photo")

        r = surveyor.patch(f"/reports/{rid}/damage-items/1", json={"part": "Rear Windscreen"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["damage_summary"][1]["part"] == "Rear Windscreen" and [d["part"] for d in out["damage_summary"]] == ["Tail Gate", "Rear Windscreen"]
        rows = rows_of(out)
        assert any(i["source_part"] == "Rear Windscreen" for i in rows), rows
        bumper_rows = [i for i in rows if i["source_part"] == "Rear Bumper"]
        assert bumper_rows and all(i["stale"] and i["workshop_est"] > 0 for i in bumper_rows), bumper_rows
        assert all(not i["stale"] for i in rows if i["source_part"] != "Rear Bumper")
        detail = surveyor.get(f"/reports/{rid}").json()["data"]
        assert detail["damaged_parts"] == ["Tail Gate", "Rear Windscreen"] and detail["annex_a"] == out["annex_a"]
        ok("renaming a part: its unpriced rows go, its priced rows stay flagged stale, the new part's rows arrive; damaged_parts follows")

        for body, what in (({"part": "Flux Capacitor"}, "an unknown part"), ({"damage_type": "Bent"}, "an unknown damage type"),
                           ({"severity": "Huge"}, "an unknown severity"), ({"photo_reference": "P09"}, "a photo the case doesn't have"),
                           ({"photo_reference": "photo 1"}, "a photo reference that isn't P-numbered")):
            r = surveyor.patch(f"/reports/{rid}/damage-items/0", json=body)
            assert r.status_code == 400 and "'" in r.json()["detail"], (what, r.text)
        assert surveyor.patch(f"/reports/{rid}/damage-items/7", json={"human_verified": True}).status_code == 404
        assert surveyor.patch(f"/reports/{rid}/damage-items/0", json={"severity": None}).status_code == 200
        ok("values off the taxonomy and photos the case doesn't have are refused (400); a missing item is 404")

        print("POST /damage-items")
        r = surveyor.post(f"/reports/{rid}/damage-items", json={"part": "Left Tail Lamp", "damage_type": "Crack", "photo_reference": "P02"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["item_index"] == 2 and out["item"]["part"] == "Left Tail Lamp" and out["item"]["human_verified"] is True
        assert out["item"]["damage_type"] == "Crack" and out["item"]["photo_reference"] == "P02" and out["item"]["severity"] is None
        lamp = [i for i in rows_of(out) if i["source_part"] == "Left Tail Lamp"]
        assert lamp and any(i["workshop_est"] > 0 and i["origin"] == "history" for i in lamp), lamp
        assert next(i for i in rows_of(out) if i["part_name"] == typed_name)["workshop_est"] == 777.0
        ok("POST appends the part, verified by the person; Annex A gains its rows from past cases; typed rows untouched")
        assert surveyor.post(f"/reports/{rid}/damage-items", json={"part": "Flux Capacitor"}).status_code == 400
        assert surveyor.post(f"/reports/{rid}/damage-items", json={}).status_code == 422
        assert surveyor.post(f"/reports/{rid}/damage-items", json={"part": "Bonnet", "damage_type": "Bent"}).status_code == 400
        ok("POST refuses an unknown part or damage type (400) and no part at all (422)")

        print("DELETE /damage-items/{i}")
        r = surveyor.delete(f"/reports/{rid}/damage-items/2")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["item"]["part"] == "Left Tail Lamp" and [d["part"] for d in out["damage_summary"]] == ["Tail Gate", "Rear Windscreen"]
        lamp = [i for i in rows_of(out) if i["source_part"] == "Left Tail Lamp"]
        assert lamp and all(i["stale"] and i["workshop_est"] > 0 for i in lamp), lamp
        detail = surveyor.get(f"/reports/{rid}").json()["data"]
        assert detail["dismissed_parts"] == ["Left Tail Lamp"], detail["dismissed_parts"]
        assert surveyor.delete(f"/reports/{rid}/damage-items/5").status_code == 404
        ok("DELETE removes the item, records the part as dismissed, flags its priced rows stale (never removes them)")

        # Someone else's case: not found, as every case-edit route.
        for method, path, kw in (("patch", f"/reports/{rid}/damage-items/0", {"json": {"human_verified": True}}),
                                 ("post", f"/reports/{rid}/damage-items", {"json": {"part": "Bonnet"}}),
                                 ("delete", f"/reports/{rid}/damage-items/0", {})):
            assert other.request(method, path, **kw).status_code == 404, (method, path)
        ok("another person's case is 'not found' on all three")

        print("dismissed parts stay dismissed; Annex A without the price book")
        pid = new_case(plain, "SNA5678C")
        before = plain.get(f"/reports/{pid}").json()["data"]
        assert "annex_a" not in before or not (before.get("annex_a") or {}).get("items")
        r = plain.delete(f"/reports/{pid}/damage-items/1")
        assert r.status_code == 200, r.text
        out = r.json()
        # Unpriced for a self-signup. "Tail Gate" is pre-taxonomy free text,
        # kept as written; a canonical name gets the firm's wording (below).
        assert [(normalise_item(i["part_name"]), i["origin"], i["workshop_est"]) for i in rows_of(out)] == [("tailgate", "detection", 0.0)], rows_of(out)
        tailgate_name = rows_of(out)[0]["part_name"]
        assert [d["part"] for d in out["damage_summary"]] == ["Tail Gate"]
        # As a re-detection would: the part is back in the list, but it was dismissed.
        db = SessionLocal()
        try:
            row = db.get(Report, pid)
            row.data = {**row.data, "damage_summary": row.data["damage_summary"] + [{"part": "Rear Bumper", "damage_type": "Dent"}]}
            db.commit()
        finally:
            db.close()
        out = plain.patch(f"/reports/{pid}/damage-items/0", json={"human_verified": True}).json()
        assert [i["part_name"] for i in rows_of(out)] == [tailgate_name], rows_of(out)
        assert all(i["workshop_est"] == 0 and not i["prefilled"] for i in rows_of(out))
        ok("a later sync adds no rows for a dismissed part; a self-signup's rows stay unpriced")
        out = plain.post(f"/reports/{pid}/damage-items", json={"part": "Rear Bumper"}).json()
        assert [i["part_name"] for i in rows_of(out)] == [tailgate_name, "Rear bumper fascia"], rows_of(out)
        assert plain.get(f"/reports/{pid}").json()["data"]["dismissed_parts"] == []
        ok("adding the part back by hand clears the dismissal and its row returns")
        # Every item removed (three: the tail gate, the re-detected bumper and
        # the one added by hand): the table is empty and STAYS empty (no
        # scaffold row for a dismissed part); a rename onto a dismissed part
        # brings it back.
        assert [d["part"] for d in out["damage_summary"]] == ["Tail Gate", "Rear Bumper", "Rear Bumper"]
        out = plain.delete(f"/reports/{pid}/damage-items/2").json()
        assert plain.get(f"/reports/{pid}").json()["data"]["dismissed_parts"] == [], "a part still listed once more isn't dismissed"
        assert plain.delete(f"/reports/{pid}/damage-items/1").status_code == 200
        out = plain.delete(f"/reports/{pid}/damage-items/0").json()
        assert out["damage_summary"] == [] and out["damaged_parts"] == [] and rows_of(out) == [] and out["annex_c"]["adjusted_total"] == 0.0, out["annex_a"]
        # Recorded as canonical names: "Tail Gate" belongs to the Boot Lid family.
        assert plain.get(f"/reports/{pid}").json()["data"]["dismissed_parts"] == ["Rear Bumper", "Boot Lid"], plain.get(f"/reports/{pid}").json()["data"]["dismissed_parts"]
        out = plain.post(f"/reports/{pid}/damage-items", json={"part": "Bonnet"}).json()
        # No past case has a bonnet: the floor wording (taxonomy.FIRM_PART_NAMES).
        assert [i["part_name"] for i in rows_of(out)] == ["Bonnet assy"]
        out = plain.patch(f"/reports/{pid}/damage-items/0", json={"part": "Rear Bumper"}).json()
        assert [i["part_name"] for i in rows_of(out)] == ["Rear bumper fascia"] and plain.get(f"/reports/{pid}").json()["data"]["dismissed_parts"] == ["Boot Lid"]
        ok("with every item removed Annex A is empty, not re-scaffolded; renaming an item onto a dismissed part un-dismisses it as POST does")
        db = SessionLocal()
        try:
            actions = [(e.action, e.detail) for e in db.query(AuditEntry).filter(AuditEntry.report_id == pid).order_by(AuditEntry.id)]
        finally:
            db.close()
        assert ("damage_item_removed", "Rear Bumper") in actions and ("damage_item_added", "Rear Bumper") in actions
        assert ("damage_item_edited", "item 1: Rear Bumper") in actions and sum(1 for a, _ in actions if a.startswith("damage_item_")) >= 6, actions
        ok("each damage-item change is its own history entry (not folded into 'edited')")

        print("the price book follows the case's owner")
        # An admin edits a self-signup's case: no firm prices land in it.
        theirs = new_case(plain, "SNA9999D")
        out = admin.patch(f"/reports/{theirs}/damage-items/0", json={"human_verified": True}).json()
        assert rows_of(out) and all(i["workshop_est"] == 0 and not i["prefilled"] for i in rows_of(out)), rows_of(out)
        mine = new_case(admin, "SNA8888E")
        out = admin.patch(f"/reports/{mine}/damage-items/0", json={"human_verified": True}).json()
        assert any(i["prefilled"] and i["workshop_est"] > 0 for i in rows_of(out)), rows_of(out)
        ok("an admin's edit on a self-signup's case keeps it unpriced; on an admin's own case the past-case prices apply")

        print("locks")
        draft = surveyor.post("/intake").json()["id"]
        for method, path, kw in (("patch", "/damage-items/0", {"json": {"human_verified": True}}), ("post", "/damage-items", {"json": {"part": "Bonnet"}}),
                                 ("delete", "/damage-items/0", {})):
            r = surveyor.request(method, f"/reports/{draft}{path}", **kw)
            assert r.status_code == 409 and "intake" in r.json()["detail"], (method, r.text)
        set_signed_off(rid, True)
        for method, path, kw in (("patch", "/damage-items/0", {"json": {"human_verified": True}}), ("post", "/damage-items", {"json": {"part": "Bonnet"}}),
                                 ("delete", "/damage-items/0", {})):
            r = surveyor.request(method, f"/reports/{rid}{path}", **kw)
            assert r.status_code == 409 and r.json()["detail"] == LOCKED, (method, r.text)
        set_signed_off(rid, False)
        assert surveyor.patch(f"/reports/{rid}/damage-items/0", json={"human_verified": False}).status_code == 200
        db = SessionLocal()
        try:
            assert db.get(Report, rid).pdf_path is None, "rendered again on the next download"
        finally:
            db.close()
        ok("an unfinished intake and a signed-off case are refused (409) on all three; a reopened case edits again; the PDF is dropped for re-render")

    print("\nALL DAMAGE-ITEM CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
