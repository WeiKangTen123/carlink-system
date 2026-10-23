"""PUT /reports/{id} must change only what the client sends.

The dashboard's Edit form sends a partial draft -- no annexes, no liability
assessment, no vehicle year. Replacing the stored case with it (defaults
filling every gap) used to erase all of those on every save.

    python scripts/test_report_update_merge.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="update-merge-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import Report  # noqa: E402
from auth_helpers import sign_in  # noqa: E402


def main() -> int:
    client = TestClient(app, base_url="https://carlink.test")
    sign_in(client, role="admin")
    r = client.post("/reports", json={"draft": {
        "category": ["Vehicle Collision or Damage"],
        "description": "Rear-ended at a junction",
        "vehicle_info": {"plate_number": "SNA1234B", "make": "Honda", "model": "Vezel", "year": "2017"},
    }})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    # What the surveyor recorded elsewhere: an Annex A line and a liability view.
    db = SessionLocal()
    row = db.get(Report, rid)
    data = dict(row.data)
    data["annex_a"] = {"items": [{"item_no": "1.0", "part_name": "Tail gate", "qty": 1, "workshop_est": 1450.0,
                                  "discount_pct": 0.2, "adjusted_cost": 1160.0}],
                       "total_workshop_est": 1450.0, "total_adjusted_cost": 1160.0}
    data["bola_assessment"] = {**(data.get("bola_assessment") or {}), "dispute_status": "Agreed 100/0"}
    row.data = data
    db.commit()
    db.close()

    # What the Edit form sends: the fields it shows, nothing else.
    r = client.put(f"/reports/{rid}", json={"draft": {
        "category": ["Vehicle Collision or Damage"],
        "description": "Rear-ended at a junction; driver shaken",
        "vehicle_info": {"plate_number": "SNA1234B", "make": "Honda", "model": "Vezel"},
        "damage_summary": [{"part": "Boot Lid", "severity": "Severe"}],
    }})
    assert r.status_code == 200, r.text
    saved = client.get(f"/reports/{rid}").json()["data"]
    assert saved["description"] == "Rear-ended at a junction; driver shaken"
    assert [d["part"] for d in saved["damage_summary"]] == ["Boot Lid"]
    assert saved["annex_a"]["items"][0]["part_name"] == "Tail gate", saved["annex_a"]
    assert saved["annex_a"]["total_adjusted_cost"] == 1160.0
    assert saved["bola_assessment"]["dispute_status"] == "Agreed 100/0"
    assert saved["vehicle_info"]["year"] == "2017", saved["vehicle_info"]
    print("    ok   a partial edit keeps the annexes, the liability assessment and the vehicle year")

    # Clearing a field on purpose still clears it.
    r = client.put(f"/reports/{rid}", json={"draft": {"description": "Rear-ended at a junction", "vehicle_info": {"year": None}}})
    assert r.status_code == 200, r.text
    assert client.get(f"/reports/{rid}").json()["data"]["vehicle_info"]["year"] is None
    print("    ok   a field sent as empty is still cleared")

    print("\nALL UPDATE-MERGE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
