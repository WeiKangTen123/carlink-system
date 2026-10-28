"""Integrity checks for dashboard intake (P1, 2026-10-21), offline with a
fake Gemini: re-reads keep the surveyor's work, nothing edits a draft while
the AI is reading it, finalize can't run twice and survives a failure, the
older /reports endpoints refuse drafts, uploads must be real images (HEIC
included), limits, the cleanup sweep, and the PDF's JPEG images.

    python3 scripts/test_intake_integrity.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="intake-integrity-")
os.environ.setdefault("STORAGE_DIR", _tmp)
# The start-up below makes the AI key secret (app/ai/secretbox.py): not in the source tree.
os.environ.setdefault("SECRETS_DIR", tempfile.mkdtemp(prefix="secrets-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import app.ai.structured as structured  # noqa: E402

gate = threading.Event()
gate.set()
draft_answers: list[dict] = []


class FakeInteractions:
    def create(self, *, model, system_instruction, input, response_format, timeout):
        gate.wait(10)
        if "photos" in response_format["schema"].get("properties", {}):
            import re

            labels = [m.group(1) for part in input if part.get("type") == "text"
                      for m in [re.match(r"Photo (P\d+):", part["text"])] if m]
            return SimpleNamespace(output_text=json.dumps({"photos": [
                {"label": l, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True, "note": None}
                for l in labels
            ]}))
        return SimpleNamespace(output_text=json.dumps(draft_answers.pop(0)))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.available_api_keys = lambda: ["fake"]
structured.acquire = lambda key=None: 0.0

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.api.intake as intake_api  # noqa: E402
from app.api.main import app  # noqa: E402
from auth_helpers import sign_in  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import cleanup  # noqa: E402
from app.intake.pipeline import merge_redraft  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession, Report  # noqa: E402
from app.storage.files import generate_pdf_image, tmp_dir  # noqa: E402


def ok(msg: str) -> None:
    print(f"    ok   {msg}")


def jpeg(color: tuple) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), color).save(buf, "JPEG")
    return buf.getvalue()


def ai_draft(**overrides) -> dict:
    d = {
        "description": "AI description", "category": ["Vehicle Collision or Damage"],
        "accident_type": "Rear-end collision", "severity_level": "Moderate",
        "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": "SNA1234B", "color": "Green"},
        "damage_summary": [{"part": "Rear Bumper", "severity": "Moderate"}, {"part": "Boot Lid", "severity": "Minor"}],
        "damaged_parts": ["Rear Bumper", "Boot Lid"],
    }
    d.update(overrides)
    return d


def wait_done(client, rid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/intake/{rid}").json()
        if s["stage"] in ("ready", "failed"):
            return s
        time.sleep(0.1)
    raise AssertionError("timed out")


def new_draft(client, n_photos=2) -> str:
    rid = client.post("/intake").json()["id"]
    for i in range(n_photos):
        r = client.post(f"/intake/{rid}/photos", files={"file": (f"p{i}.jpg", jpeg((40 * i, 80, 120)), "image/jpeg")}, data={"position": str(i)})
        assert r.status_code == 200, r.text
    return rid


def main() -> int:
    # merge_redraft rules, directly.
    current = ai_draft(
        description="Surveyor rewrote this",
        vehicle_info={"make": "Honda", "model": "Vezel", "plate_number": "SNA1234B", "color": "Black"},
        insurance_details={"insurer_name": "NTUC Income", "claim_number": "C-1"},
        people_involved=[{"name": "Lim", "role": "Driver"}],
        damage_summary=[{"part": "Rear Bumper", "severity": "Severe", "human_verified": True}, {"part": "Boot Lid"}],
    )
    previous = ai_draft()
    new = ai_draft(description="New AI description", accident_type="Side impact",
                   vehicle_info={"make": "Honda", "model": "Vezel 1.5X", "plate_number": "SNA1234B", "color": "Green", "vin": "RU1-1"},
                   damage_summary=[{"part": "Rear Bumper", "severity": "Minor"}, {"part": "Left Tail Lamp"}])
    merged = merge_redraft(current, previous, new)
    assert merged["description"] == "Surveyor rewrote this"           # surveyor edit kept
    assert merged["accident_type"] == "Side impact"                   # untouched -> new reading
    assert merged["vehicle_info"]["color"] == "Black"                 # surveyor correction kept
    assert merged["vehicle_info"]["model"] == "Vezel 1.5X"            # untouched -> new reading
    assert merged["vehicle_info"]["vin"] == "RU1-1"                   # new fact filled in
    assert merged["insurance_details"]["insurer_name"] == "NTUC Income"
    assert merged["people_involved"][0]["name"] == "Lim"
    parts = [(d["part"], d.get("severity")) for d in merged["damage_summary"]]
    assert parts == [("Rear Bumper", "Severe"), ("Left Tail Lamp", None)], parts
    ok("merge_redraft keeps surveyor edits/sections/checked damage, takes new readings elsewhere")

    with TestClient(app) as client:
        sign_in(client)
        # Re-read through the API keeps step-3/4 work.
        rid = new_draft(client)
        draft_answers.append(ai_draft())
        client.post(f"/intake/{rid}/process", json={"mode": "all"})
        state = wait_done(client, rid)
        assert state["stage"] == "ready"
        data = state["data"]
        data["insurance_details"]["insurer_name"] = "NTUC Income"
        data["people_involved"] = [{"name": "Lim Wei", "role": "Driver"}]
        data["vehicle_info"]["color"] = "Black"
        data["damage_summary"][0]["human_verified"] = True
        data["damage_summary"][0]["severity"] = "Severe"
        assert client.put(f"/intake/{rid}/data", json={"data": data}).status_code == 200
        draft_answers.append(ai_draft(damage_summary=[{"part": "Rear Bumper", "severity": "Minor"}, {"part": "Rear Windscreen"}]))
        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        after = wait_done(client, rid)["data"]
        assert after["insurance_details"]["insurer_name"] == "NTUC Income"
        assert after["people_involved"][0]["name"] == "Lim Wei"
        assert after["vehicle_info"]["color"] == "Black"
        assert [(d["part"], d["severity"]) for d in after["damage_summary"]] == [("Rear Bumper", "Severe"), ("Rear Windscreen", None)]
        ok("re-read through the API keeps insurer, people, colour fix and the checked damage item")

        # Edits refused while the AI is reading.
        gate.clear()
        draft_answers.append(ai_draft())
        client.post(f"/intake/{rid}/process", json={"mode": "read"})
        time.sleep(0.3)
        assert client.put(f"/intake/{rid}/data", json={"data": after}).status_code == 409
        pid = client.get(f"/intake/{rid}").json()["photos"][0]["id"]
        assert client.patch(f"/intake/{rid}/photos/{pid}", json={"excluded": True}).status_code == 409
        assert client.delete(f"/intake/{rid}/photos/{pid}").status_code == 409
        assert client.post(f"/intake/{rid}/finalize", json={"data": after}).status_code == 409
        gate.set()
        wait_done(client, rid)
        ok("autosave, regroup, delete and finalize refused while the AI is reading")

        # Older endpoints refuse a draft.
        for method, path in (("post", f"/reports/{rid}/sign-off"), ("post", f"/reports/{rid}/reopen"),
                             ("post", f"/reports/{rid}/render-pdf"), ("get", f"/reports/{rid}/download"),
                             ("post", f"/reports/{rid}/ai-enrich"), ("put", f"/reports/{rid}")):
            kwargs = {"json": {"draft": {"description": "x"}}} if method == "put" else {}
            r = getattr(client, method)(path, **kwargs)
            assert r.status_code == 409, (path, r.status_code, r.text)
        ok("sign-off, reopen, PDF, download, AI Auto-Map and edit refuse an unfinished intake")

        # Finalize: a failure after copying leaves the draft intact and retryable.
        final = client.get(f"/intake/{rid}").json()["data"]
        original_commit_step = intake_api._finalize_claimed

        def failing(db, report_id, body, user):
            intake_api.copy_into_case(report_id, [p for p in intake_api.ordered_photos(db, report_id)])
            raise RuntimeError("simulated crash after copying")

        intake_api._finalize_claimed = failing
        try:
            r = client.post(f"/intake/{rid}/finalize", json={"data": final})
            assert r.status_code == 500
        except RuntimeError:
            pass
        intake_api._finalize_claimed = original_commit_step
        state = client.get(f"/intake/{rid}").json()
        assert state["stage"] == "ready" and len(state["photos"]) == 2
        assert all(Path(settings.storage_dir, p["url"].removeprefix("/files/")).exists() for p in state["photos"])
        ok("a crash mid-finalize leaves the draft (stage, photos, files) as it was")

        # Claim: a second finalize while one holds the draft is refused.
        db = SessionLocal()
        db.get(IntakeSession, rid).stage = "finalizing"
        db.commit()
        db.close()
        assert client.post(f"/intake/{rid}/finalize", json={"data": final}).status_code == 409
        assert client.post(f"/intake/{rid}/photos", files={"file": ("x.jpg", jpeg((1, 2, 3)), "image/jpeg")}).status_code == 409
        db = SessionLocal()
        db.get(IntakeSession, rid).stage = "ready"
        db.commit()
        db.close()
        assert client.post(f"/intake/{rid}/finalize", json={"data": final}).status_code == 200
        case = client.get(f"/reports/{rid}").json()
        assert case["status"] == "confirmed" and len(case["photo_urls"]) == 2
        assert not (Path(settings.storage_dir) / "intake" / rid).exists()
        ok("finalize is claimed (second one and uploads refused) and the retry builds the full case")

        # Uploads must be real images; HEIC works.
        rid2 = client.post("/intake").json()["id"]
        r = client.post(f"/intake/{rid2}/photos", files={"file": ("fake.jpg", b"not really a jpeg", "image/jpeg")})
        assert r.status_code == 400, r.text
        import pillow_heif

        heif = pillow_heif.from_pillow(Image.new("RGB", (640, 480), (10, 120, 200)))
        buf = io.BytesIO()
        heif.save(buf, quality=80)
        r = client.post(f"/intake/{rid2}/photos", files={"file": ("IMG_0001.HEIC", buf.getvalue(), "image/heic")})
        assert r.status_code == 200, r.text
        photo = r.json()["photo"]
        assert photo["thumb_url"].endswith("_thumb.jpg") and photo["preview_url"].endswith("_preview.webp")
        assert client.get(photo["preview_url"]).status_code == 200
        ok("a non-image named .jpg is rejected; an iPhone HEIC gets a thumbnail and preview")

        # Limits. The shared cap counts and stops everyone but admins and
        # ticked staff (this client is an admin).
        plain = TestClient(app)
        sign_in(plain, role="user")
        saved = intake_api.MAX_OPEN_DRAFTS
        intake_api.MAX_OPEN_DRAFTS = 1
        try:
            first = plain.post("/intake")
            assert first.status_code == 200, first.text
            assert plain.post("/intake").status_code == 409
            r = client.post("/intake")
            assert r.status_code == 200, r.text
        finally:
            intake_api.MAX_OPEN_DRAFTS = saved
        for draft in (first.json()["id"], r.json()["id"]):
            assert client.delete(f"/reports/{draft}").status_code == 200
        ok("creating drafts beyond the shared open-draft cap is refused; admins are exempt")

        # Cleanup sweep: idle draft and old tmp file go, recent ones stay.
        old_rid = new_draft(client, 1)
        fresh_rid = new_draft(client, 1)
        week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
        db = SessionLocal()
        db.get(Report, old_rid).created_at = week_ago
        db.get(IntakeSession, old_rid).updated_at = week_ago
        for p in db.query(IntakePhoto).filter(IntakePhoto.report_id == old_rid):
            p.created_at = week_ago
        db.commit()
        db.close()
        old_tmp = tmp_dir() / "old.jpg"
        old_tmp.write_bytes(b"x")
        os.utime(old_tmp, (time.time() - 2 * 86400, time.time() - 2 * 86400))
        new_tmp = tmp_dir() / "new.jpg"
        new_tmp.write_bytes(b"x")
        result = cleanup.sweep()
        assert old_rid in result["drafts"] and fresh_rid not in result["drafts"], result
        assert not old_tmp.exists() and new_tmp.exists()
        assert client.get(f"/intake/{old_rid}").status_code == 404
        assert client.get(f"/intake/{fresh_rid}").status_code == 200
        ok("sweep removes the week-idle draft and day-old tmp file, keeps fresh ones")

    # PDF images are JPEG, downscaled.
    big = Path(_tmp, "big.jpg")
    Image.new("RGB", (4000, 3000), (90, 90, 90)).save(big, "JPEG", quality=95)
    out = Path(generate_pdf_image(str(big)))
    with Image.open(out) as im:
        assert im.format == "JPEG" and max(im.size) == 1600
    ok(f"PDF image is a 1600px JPEG ({out.stat().st_size // 1024} KB)")

    print("\nALL INTEGRITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
