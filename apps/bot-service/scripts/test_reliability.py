"""Test suite for Backend & Storage Reliability improvements.
Tests:
1. _to_data_uri safe handling of missing files, None, empty string.
2. render_pdf exception handling and browser cleanup.
3. to_public_url and to_thumbnail_url error safety on non-relative paths and URLs.
4. SQLite WAL mode and busy timeout pragmas.
5. Consistent database_url path resolution.
6. API endpoint fault tolerance (GET report, download, create, update) when render_pdf fails.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure app is in python path
bot_service_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(bot_service_dir))

from app.config import settings, BOT_SERVICE_DIR
from app.rendering.renderer import _to_data_uri, BLANK_IMAGE_DATA_URI, render_pdf
from app.storage.files import to_public_url, to_thumbnail_url
from app.reports.db import engine, SessionLocal, init_db
from app.reports.models import Report


def test_renderer_to_data_uri():
    print("[TEST] Running test_renderer_to_data_uri...")
    # Missing file
    res = _to_data_uri("non_existent_image_12345.jpg")
    assert res == BLANK_IMAGE_DATA_URI, f"Expected blank data URI, got {res[:30]}"
    
    # Empty string and None
    assert _to_data_uri("") == BLANK_IMAGE_DATA_URI
    assert _to_data_uri(None) == BLANK_IMAGE_DATA_URI

    # Existing file
    sample_file = bot_service_dir / "app" / "rendering" / "templates" / "security_incident.html"
    res_existing = _to_data_uri(str(sample_file))
    assert res_existing.startswith("data:"), "Expected valid data URI for existing file"
    print("  -> PASSED: _to_data_uri is safe against missing files.")


def test_renderer_render_pdf_failure():
    print("[TEST] Running test_renderer_render_pdf_failure...")
    # Passing invalid template or failing playwright should raise clean RuntimeError
    with patch("app.rendering.renderer._env.get_template", side_effect=Exception("Template disk read error")):
        try:
            render_pdf({}, [], "out.pdf")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Template rendering failed" in str(e)

    # Playwright launch failure
    from scripts.render_sample import SAMPLE_CAR_REPORT
    with patch("app.rendering.renderer.sync_playwright") as mock_pw:
        mock_pw.side_effect = Exception("Browser binary not found")
        try:
            render_pdf(SAMPLE_CAR_REPORT, [], "out.pdf")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Playwright PDF generation failed" in str(e)
    print("  -> PASSED: render_pdf safely handles failures and raises RuntimeError without crashing process.")


def test_storage_files_urls():
    print("[TEST] Running test_storage_files_urls...")
    # 1. Existing URL
    assert to_public_url("http://example.com/img.jpg") == "http://example.com/img.jpg"
    assert to_public_url("https://example.com/img.jpg") == "https://example.com/img.jpg"
    assert to_public_url("/files/incidents/2026/01/p1.jpg") == "/files/incidents/2026/01/p1.jpg"
    assert to_thumbnail_url("http://example.com/img.jpg") == "http://example.com/img.jpg"
    assert to_thumbnail_url("/files/some/photo.jpg") == "/files/some/photo.jpg"

    # 2. Path outside storage_dir (would previously throw ValueError)
    outside_path = r"C:\Windows\System32\drivers\etc\hosts" if os.name == "nt" else "/etc/hosts"
    public_outside = to_public_url(outside_path)
    assert public_outside == "/files/hosts", f"Expected /files/hosts, got {public_outside}"
    
    thumb_outside = to_thumbnail_url(outside_path)
    assert thumb_outside == "/files/hosts" or thumb_outside.startswith("/files/"), f"Got {thumb_outside}"

    # 3. Path inside storage_dir
    inside_path = Path(settings.storage_dir).resolve() / "incidents" / "2026" / "test.jpg"
    public_inside = to_public_url(str(inside_path))
    assert public_inside == "/files/incidents/2026/test.jpg"
    print("  -> PASSED: to_public_url and to_thumbnail_url handle non-relative paths safely.")


def test_db_wal_and_resolution():
    print("[TEST] Running test_db_wal_and_resolution...")
    init_db()
    with engine.connect() as conn:
        journal_mode = conn.exec_driver_sql("PRAGMA journal_mode;").scalar()
        assert str(journal_mode).lower() == "wal", f"Expected journal_mode=wal, got {journal_mode}"
        busy_timeout = conn.exec_driver_sql("PRAGMA busy_timeout;").scalar()
        assert busy_timeout == 5000, f"Expected busy_timeout=5000, got {busy_timeout}"
    
    # Path resolution check
    from app.reports.db import _resolve_database_url
    resolved = _resolve_database_url("sqlite:///./relative.db")
    expected_part = (BOT_SERVICE_DIR / "relative.db").as_posix()
    assert expected_part in resolved, f"Expected {expected_part} in {resolved}"
    print("  -> PASSED: DB WAL mode and busy timeout verified, consistent URL resolution verified.")


def test_api_endpoints_pdf_fault_tolerance():
    print("[TEST] Running test_api_endpoints_pdf_fault_tolerance...")
    from fastapi.testclient import TestClient
    from app.api.main import app

    client = TestClient(app)

    # Insert a dummy report into db
    db = SessionLocal()
    rep_id = "test_rel_01"
    existing = db.get(Report, rep_id)
    if not existing:
        rep = Report(
            id=rep_id,
            channel="telegram",
            reporter_chat_id="123",
            data={"category": ["Vehicle Collision or Damage"], "description": "Crash test"},
            status="confirmed",
            photo_paths=[],
            pdf_path=None,
        )
        db.add(rep)
        db.commit()
    db.close()

    # Test 1: GET /reports/{report_id} when render_pdf raises exception
    with patch("app.api.main.render_pdf", side_effect=RuntimeError("Playwright crashed")):
        resp = client.get(f"/reports/{rep_id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["id"] == rep_id
        assert data["pdf_url"] is None, f"Expected pdf_url=None, got {data['pdf_url']}"
    print("  -> PASSED: GET /reports/{id} returns 200 with pdf_url: None when PDF generation fails.")

    # Test 2: GET /reports/{report_id}/download when render_pdf raises exception
    with patch("app.api.main.render_pdf", side_effect=RuntimeError("Playwright crashed")):
        resp = client.get(f"/reports/{rep_id}/download")
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        assert "PDF generation currently unavailable" in resp.json()["detail"]
    print("  -> PASSED: GET /reports/{id}/download returns 503 when PDF generation fails.")

    # Test 3: POST /reports when render_pdf raises exception
    with patch("app.api.main.render_pdf", side_effect=RuntimeError("Playwright crashed")):
        payload = {
            "draft": {
                "category": ["Vehicle Collision or Damage"],
                "description": "Manual draft",
                "datetime": "2026-10-09T12:00:00Z",
                "location": "Jurong West",
                "persons_involved": [],
                "action_taken": "Assessed",
            },
            "temp_photo_paths": [],
        }
        resp = client.post("/reports", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        created_id = resp.json()["id"]
        # Verify in DB
        db = SessionLocal()
        created_report = db.get(Report, created_id)
        assert created_report is not None
        assert created_report.status == "confirmed"
        assert created_report.pdf_path is None
        db.delete(created_report)
        db.commit()
        db.close()
    print("  -> PASSED: POST /reports succeeds and commits record even when PDF generation fails.")

    # Test 4: PUT /reports/{report_id} when render_pdf raises exception
    with patch("app.api.main.render_pdf", side_effect=RuntimeError("Playwright crashed")):
        update_payload = {
            "draft": {
                "category": ["Vehicle Collision or Damage"],
                "description": "Updated crash test",
                "datetime": "2026-10-09T12:00:00Z",
                "location": "Jurong West",
                "persons_involved": [],
                "action_taken": "Assessed again",
            },
            "temp_photo_paths": [],
        }
        resp = client.put(f"/reports/{rep_id}", json=update_payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["id"] == rep_id
        # Verify in DB
        db = SessionLocal()
        updated_report = db.get(Report, rep_id)
        assert updated_report.data["description"] == "Updated crash test"
        db.close()
    print("  -> PASSED: PUT /reports/{id} updates and commits record even when PDF generation fails.")

    # Clean up test report
    db = SessionLocal()
    r = db.get(Report, rep_id)
    if r:
        db.delete(r)
        db.commit()
    db.close()


if __name__ == "__main__":
    test_renderer_to_data_uri()
    test_renderer_render_pdf_failure()
    test_storage_files_urls()
    test_db_wal_and_resolution()
    test_api_endpoints_pdf_fault_tolerance()
    print("\nALL RELIABILITY TESTS PASSED SUCCESSFULLY! :)")
