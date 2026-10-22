"""End-to-end test of the dashboard intake API (app/api/intake.py) with
Gemini replaced by a fake, so it runs offline and costs no quota.

Covers: batch upload + duplicate skip, AI sorting in batches, the reading
step's photo selection and "@<photo id>" references, surveyor regrouping and
exclusion, an AI outage and retry, a run interrupted by a restart, finalize
(photos moved, P-numbers fixed, Annex A rebuilt), and deleting a draft.

    python3 scripts/test_intake_flow.py                 # generated test images
    python3 scripts/test_intake_flow.py <photo folder>  # e.g. a real case folder

PDF rendering is attempted on finalize; without Playwright's Chromium
installed it fails and is logged, which the endpoint tolerates by design.
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="intake-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import app.ai.structured as structured  # noqa: E402

PHOTO_COUNT = 14  # > one sorting batch of 12


def photo_files() -> list[Path]:
    if len(sys.argv) > 1:
        return sorted(p for p in Path(sys.argv[1]).iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))[:PHOTO_COUNT]
    from PIL import Image

    out = Path(_tmp, "photos")
    out.mkdir()
    files = []
    for i in range(PHOTO_COUNT):
        path = out / f"IMG_{i:04d}.jpg"
        Image.new("RGB", (1200, 900), (20 * i % 255, 90, 255 - 15 * i)).save(path, "JPEG")
        files.append(path)
    return files


calls = {"sort": 0, "draft": 0, "fail": False}


class FakeInteractions:
    def create(self, *, model, system_instruction, input, response_format, timeout):
        if calls["fail"]:
            raise RuntimeError(calls["fail"])
        labels = [m.group(1) for part in input if part.get("type") == "text"
                  for m in [re.match(r"Photo (P\d+):", part["text"])] if m]
        assert len(labels) == len([p for p in input if p.get("type") == "image"])
        if "photos" in response_format["schema"].get("properties", {}):
            calls["sort"] += 1
            out = []
            for label in labels:
                n = int(label[1:])
                kind = "log_card" if n == 1 else "odometer" if n == 2 else "damage" if n % 3 else "overview"
                out.append({"label": label, "kind": kind, "area": "rear" if n % 2 else "left",
                            "quality": "blurry" if n == 5 else "good", "shows_damage": kind == "damage",
                            "note": f"photo {label}"})
            # A label that wasn't asked about must be ignored.
            out.append({"label": "P999", "kind": "damage", "area": None, "quality": "good", "shows_damage": True, "note": None})
            return SimpleNamespace(output_text=json.dumps({"photos": out}))
        calls["draft"] += 1
        draft = {
            "description": "Insured vehicle was rear-ended.",
            "category": ["Vehicle Collision or Damage"],
            "damaged_parts": ["Rear Bumper", "Boot Lid"],
            "damage_summary": [
                # Unpadded label, as models sometimes return it.
                {"part": "Rear Bumper", "severity": "Moderate", "photo_reference": labels[-1].replace("P0", "P"),
                 "bbox_2d": [100, 100, 500, 500], "ai_confidence": "High"},
                # A label that wasn't sent: reference and box must be dropped.
                {"part": "Boot Lid", "severity": "Minor", "photo_reference": "P77", "bbox_2d": [1, 2, 3, 4]},
            ],
            # Not SLK 3063 Z: that plate switches synthesize_annexes to its
            # hardcoded benchmark annex.
            "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": "SNA1234B"},
            "reporter_name": "AI Should Not Win",
        }
        return SimpleNamespace(output_text=json.dumps(draft))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.available_api_keys = lambda: ["fake"]
structured.acquire = lambda key=None: 0.0

from fastapi.testclient import TestClient  # noqa: E402

from auth_helpers import sign_in  # noqa: E402

from app.api.main import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake.pipeline import MAX_READ_PHOTOS, select_for_reading  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession  # noqa: E402


def wait_done(client: TestClient, rid: str, timeout: float = 60) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = client.get(f"/intake/{rid}").json()
        if state["stage"] in ("ready", "failed"):
            return state
        time.sleep(0.2)
    raise AssertionError("processing timed out")


def ok(msg: str) -> None:
    print(f"    ok   {msg}")


def main() -> int:
    files = photo_files()
    with TestClient(app) as client:
        sign_in(client)
        rid = client.post("/intake").json()["id"]

        for i, f in enumerate(files):
            with open(f, "rb") as fh:
                r = client.post(f"/intake/{rid}/photos", files={"file": (f.name, fh, "image/jpeg")}, data={"position": str(i)})
            assert r.status_code == 200 and "photo" in r.json(), r.text
        with open(files[3], "rb") as fh:
            r = client.post(f"/intake/{rid}/photos", files={"file": ("copy.jpg", fh, "image/jpeg")}, data={"position": "99"})
        assert "duplicate_of" in r.json(), r.text
        assert client.post(f"/intake/{rid}/photos", files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 400
        state = client.get(f"/intake/{rid}").json()
        assert [p["label"] for p in state["photos"]] == [f"P{i + 1:02d}" for i in range(len(files))]
        ok(f"{len(files)} photos uploaded in order, duplicate skipped, non-photo rejected")

        client.put(f"/intake/{rid}/data", json={"data": {"description": "", "reporter_name": "Ali Tan"}, "reporter_notes": "Rear-ended."})
        assert client.post(f"/intake/{rid}/process", json={"mode": "all"}).status_code == 200
        state = wait_done(client, rid)
        assert state["stage"] == "ready", state
        assert calls["sort"] == 2 and all(p["kind"] for p in state["photos"]), calls
        data = state["data"]
        assert data["reporter_name"] == "Ali Tan"
        assert data["damage_summary"][0]["photo_reference"].startswith("@")
        assert data["damage_summary"][1]["photo_reference"] is None and data["damage_summary"][1]["bbox_2d"] is None
        assert state["photos"][0]["id"] in state["read_photo_ids"]
        ok("sorted in 2 batches, read, references stored as photo ids, reporter fields kept")

        p2, p0 = state["photos"][2]["id"], state["photos"][0]["id"]
        assert client.patch(f"/intake/{rid}/photos/{p2}", json={"kind": "tyre"}).status_code == 200
        assert client.patch(f"/intake/{rid}/photos/{p2}", json={"kind": "nonsense"}).status_code == 400
        assert client.patch(f"/intake/{rid}/photos/{p0}", json={"excluded": True}).status_code == 200
        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        state = wait_done(client, rid)
        assert p0 not in state["read_photo_ids"] and calls["sort"] == 2
        assert state["photos"][2]["kind_source"] == "surveyor"
        ok("regroup and exclusion respected on re-read, no re-sorting")

        assert client.delete(f"/intake/{rid}/photos/{state['photos'][1]['id']}").status_code == 200
        listed = [x for x in client.get("/reports").json() if x["id"] == rid][0]
        assert listed["status"] == "intake" and listed["thumbnail_url"]
        assert client.get(f"/reports/{rid}").json()["pdf_url"] is None
        assert all(a["id"] != rid for a in client.get("/analytics/summary").json()["recent_activity"])
        ok("draft listed with thumbnail, detail bare, excluded from analytics")

        calls["fail"] = "503 service_unavailable: high demand"
        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        state = wait_done(client, rid)
        assert state["stage"] == "failed" and "busy" in state["error"], state["error"]
        calls["fail"] = "400 invalid_request: Request contains an invalid argument."
        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        state = wait_done(client, rid)
        assert state["stage"] == "failed" and "rejected" in state["error"], state["error"]
        calls["fail"] = False
        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        state = wait_done(client, rid)
        assert state["stage"] == "ready"
        ok("busy vs rejected AI errors are told apart; retry recovers")

        db = SessionLocal()
        db.get(IntakeSession, rid).stage = "sorting"
        db.commit()
        db.close()
        stale = client.get(f"/intake/{rid}").json()
        assert stale["stage"] == "failed" and "restarted" in stale["error"]
        ok("run interrupted by a restart surfaces as retryable, not stuck")

        data = state["data"]
        ref_id = data["damage_summary"][0]["photo_reference"][1:]
        expected = [p["label"] for p in state["photos"] if p["id"] == ref_id][0]
        data["damage_summary"].append({"part": "Left Tail Lamp", "severity": "Minor", "human_verified": True})
        assert client.post(f"/intake/{rid}/finalize", json={"data": data}).status_code == 200
        detail = client.get(f"/reports/{rid}").json()
        assert detail["status"] == "confirmed" and len(detail["photo_urls"]) == len(files) - 1
        assert detail["data"]["damage_summary"][0]["photo_reference"] == expected
        assert "Left Tail Lamp" in [it["part_name"] for it in detail["data"]["annex_a"]["items"]]
        assert not (Path(settings.storage_dir) / "intake" / rid).exists()
        ok(f"finalized: photos moved, reference resolved to {expected}, Annex A rebuilt")

        rid2 = client.post("/intake").json()["id"]
        with open(files[0], "rb") as fh:
            client.post(f"/intake/{rid2}/photos", files={"file": (files[0].name, fh, "image/jpeg")})
        assert client.delete(f"/reports/{rid2}").status_code == 200
        assert not (Path(settings.storage_dir) / "intake" / rid2).exists()
        ok("deleting a draft removes its photos")

    big = []
    for i in range(78):
        kind = "damage" if i < 50 else "overview" if i < 60 else "tyre" if i < 66 else "other"
        big.append(IntakePhoto(id=f"x{i}", report_id="r", position=i, path="", sha256="", kind=kind,
                               area=["rear", "left", "rear", "rear"][i % 4] if kind == "damage" else None,
                               quality="good", shows_damage=kind == "damage", excluded=False))
    big[60].kind = "log_card"
    big[61].quality = "unusable"
    chosen = select_for_reading(big)
    assert len(chosen) == MAX_READ_PHOTOS and big[60] in chosen and big[61] not in chosen
    assert {p.area for p in chosen if p.kind == "damage"} == {"rear", "left"}
    ok(f"78-photo case reads {MAX_READ_PHOTOS} photos: log card in, both damage areas covered")

    print("\nALL INTAKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
