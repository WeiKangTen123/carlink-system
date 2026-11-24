"""Phase 1 F (plans/2026-11-23-report-and-parts R-report gap 8): the Review
step's "prints as" under each damaged part (dashboard lib/reviewChecks.ts
printsAs) is what filing prints.

    python scripts/test_prints_as.py

1. GET /taxonomy sends each part's floor wording (taxonomy.FIRM_PART_NAMES
   and FIRM_PART_NAMES_HATCH) as firm_part_names / firm_part_names_hatch,
   and keeps every field it had.
2. The dashboard's fixture (apps/dashboard/scripts/fixtures/
   prints-as-sample.json) quotes that wording, the make/model catalogue and
   the body types exactly -- scripts/check-review-checks.mjs runs printsAs
   on it.
3. Filing each of the fixture's vehicles through the intake (as staff
   without the price book: one unpriced line per part) prints exactly the
   fixture's `prints`, one line per row that has a part: tail gate or boot
   lid by body type (the catalogue before a stated body), no LH / RH on a
   side nobody confirmed, a named Other by its name with the side last, an
   unnamed Other as "Other part (to be named)", a row with no part left out.

Offline: no Gemini call. The API runs through TestClient with
scripts/test_annexes.py's temporary database and render stand-ins. Made-up
makes and plates only.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.argv = sys.argv[:1]
import test_annexes as annexes  # noqa: E402,F401  (temporary database, render stand-ins)

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.reports.db import init_db  # noqa: E402
from app.reports.taxonomy import BODY_TYPES, FIRM_PART_NAMES, FIRM_PART_NAMES_HATCH, MODEL_BODY_TYPES  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

FIXTURE = ROOT.parent / "dashboard" / "scripts" / "fixtures" / "prints-as-sample.json"


def ok(label: str) -> None:
    print(f"    ok   {label}")


def check_taxonomy(client: TestClient) -> None:
    print("GET /taxonomy sends the floor wording")
    tax = client.get("/taxonomy").json()
    assert tax["firm_part_names"] == FIRM_PART_NAMES, "firm_part_names"
    assert tax["firm_part_names_hatch"] == FIRM_PART_NAMES_HATCH, "firm_part_names_hatch"
    for key in ("parts", "car_map_parts", "car_map_names", "damage_types", "severities", "condition_tokens", "condition_common",
                "condition_whole", "remarks", "body_types", "model_body_types", "usage"):
        assert key in tax, key
    ok("firm_part_names and firm_part_names_hatch are taxonomy's; every earlier field is still there")


def check_fixture(fixture: dict) -> None:
    print("the dashboard's fixture quotes the taxonomy")
    t = fixture["taxonomy"]
    for part, name in t["firm_part_names"].items():
        assert FIRM_PART_NAMES[part] == name, (part, name, FIRM_PART_NAMES[part])
    for part, name in t["firm_part_names_hatch"].items():
        assert FIRM_PART_NAMES_HATCH[part] == name, (part, name, FIRM_PART_NAMES_HATCH[part])
    for fragment, body in t["model_body_types"].items():
        assert MODEL_BODY_TYPES[fragment] == body, (fragment, body)
    assert t["body_types"] == BODY_TYPES, t["body_types"]
    rows = [row for group in fixture["vehicles"] for row in group["items"]]
    for row in rows:
        part = row["item"]["part"]
        assert not part or part in t["firm_part_names"], f"{part!r} is quoted in the fixture's wording"
    ok(f"{len(t['firm_part_names'])} wordings, {len(t['model_body_types'])} catalogue entries and the body types match; "
       f"{len(rows)} rows")


def check_filing(client: TestClient, fixture: dict) -> None:
    print("filing prints what the Review step says")
    for group in fixture["vehicles"]:
        vehicle = group["vehicle"]
        items = [dict(row["item"]) for row in group["items"]]
        body = {"category": ["Vehicle Collision or Damage"], "description": "Made-up test case",
                "vehicle_info": dict(vehicle), "damage_summary": items}
        draft = client.post("/intake").json()["id"]
        r = client.put(f"/intake/{draft}/data", json={"data": body})
        assert r.status_code == 200, r.text
        r = client.post(f"/intake/{draft}/finalize", json={"data": body})
        assert r.status_code == 200, r.text
        filed = client.get(f"/reports/{draft}").json()["data"]
        printed = sorted(row["part_name"] for row in filed["annex_a"]["items"])
        expected = sorted(row["prints"] for row in group["items"] if row["prints"] is not None)
        label = f"{vehicle['make']} {vehicle['model']}{' (' + vehicle['body_type'] + ')' if vehicle.get('body_type') else ''}"
        assert printed == expected, (label, printed, expected)
        ok(f"{label}: {', '.join(expected)}")


def main() -> int:
    init_db()
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    client = TestClient(app)
    with client:
        # Staff without the price book: one unpriced line per part, in the
        # floor wording -- what the Review step shows.
        sign_in(client, role="user", name="Pat Printsas")
        check_taxonomy(client)
        check_fixture(fixture)
        check_filing(client, fixture)
    print("\nALL PRINTS-AS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
