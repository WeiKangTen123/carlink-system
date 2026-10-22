"""Tests for dashboard sign-in (app/auth, app/api/auth.py): the check on
every request, lockout, roles, signing off as yourself, who may reopen and
delete, the case history, session expiry and the server command.

    python scripts/test_auth.py

Offline: PDF rendering is attempted and fails without Playwright's Chromium,
which every endpoint here tolerates by design.
"""
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="auth-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, sweep_sessions, token_hash, utc_now  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import AuditEntry, AuthSession, User  # noqa: E402

BASE = "https://carlink.test"
ADMIN_EMAIL = "owner@example.com"
ADMIN_PASSWORD = "first-admin-pw"


def ok(label: str) -> None:
    print(f"    ok   {label}")


def browser() -> TestClient:
    # https so the Secure session cookie is kept and sent back.
    return TestClient(app, base_url=BASE)


def login(client: TestClient, email: str, password: str, **kw):
    return client.post("/auth/login", json={"email": email, "password": password}, **kw)


def run_manage(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.auth.manage", *args],
        cwd=ROOT, input=stdin, capture_output=True, text=True, env=os.environ.copy(),
    )


def new_case(client: TestClient, plate: str = "SKA1234A") -> str:
    r = client.post("/reports", json={"draft": {
        "category": ["Vehicle Collision or Damage"],
        "description": "Rear-ended at a junction",
        "vehicle_info": {"plate_number": plate},
    }})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def main() -> int:
    with TestClient(app, base_url=BASE) as anon:
        print("signed out")
        assert anon.get("/health").status_code == 200
        for path in ("/reports", "/auth/me", "/settings", "/files/incidents/2026/10/x/photo_00.jpg", "/reports/x/download"):
            r = anon.get(path)
            assert r.status_code == 401, (path, r.status_code)
        assert anon.post("/intake").status_code == 401
        ok("every route but /health refuses a request with no session (photos and PDFs too)")
        anon.cookies.set(SESSION_COOKIE, "made-up-token")
        assert anon.get("/reports").status_code == 401
        ok("a made-up session token is refused")
        anon.cookies.clear()

        print("first admin from the server command")
        r = run_manage("create-user", "--email", " Owner@Example.com ", "--name", "Owner", "--role", "admin", "--password-stdin", stdin=ADMIN_PASSWORD + "\n")
        assert r.returncode == 0, r.stderr
        r = run_manage("create-user", "--email", ADMIN_EMAIL, "--name", "Again", "--password-stdin", stdin=ADMIN_PASSWORD + "\n")
        assert r.returncode != 0 and "already" in (r.stderr + r.stdout)
        r = run_manage("create-user", "--email", "short@example.com", "--name", "S", "--password-stdin", stdin="short\n")
        assert r.returncode != 0
        assert ADMIN_EMAIL in run_manage("list-users").stdout
        ok("create-user (email lower-cased, duplicates and short passwords refused), list-users")

        print("signing in")
        admin = browser()
        assert login(admin, ADMIN_EMAIL, "wrong-password").status_code == 401
        assert login(admin, "nobody@example.com", ADMIN_PASSWORD).json()["detail"] == "Email or password is wrong."
        r = login(admin, "  OWNER@example.com", ADMIN_PASSWORD)
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "admin" and r.json()["must_change_password"] is False
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie and "path=/" in cookie
        assert admin.get("/auth/me").json()["email"] == ADMIN_EMAIL
        ok("wrong password / unknown email get the same message; cookie is HttpOnly, Secure, SameSite=Lax")
        assert admin.get("/system/info").json()["auth"]["configured"] is True

        print("requests from another site")
        r = admin.post("/intake", headers={"Origin": "https://evil.34-45-253-162.sslip.io"})
        assert r.status_code == 403, r.status_code
        r = admin.post("/intake", headers={"Origin": BASE})
        assert r.status_code == 200, r.text
        draft_by_admin = r.json()["id"]
        assert admin.post("/intake", headers={"Origin": "http://localhost:3000"}).status_code == 200
        r = login(browser(), ADMIN_EMAIL, ADMIN_PASSWORD, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403 and "set-cookie" not in r.headers
        assert admin.post("/intake", headers={"Origin": "http://carlink.test:8080"}).status_code == 200
        ok("a POST carrying another site's Origin is refused (sign-in too); same host on another port, and local dev, pass")

        print("adding a surveyor")
        r = admin.post("/users", json={"email": "Pat@Example.com", "name": "Pat Surveyor", "role": "surveyor",
                                       "qualifications": "MIMI", "license_number": "LIC-123", "firm_name": "Pat & Co."})
        assert r.status_code == 200, r.text
        pat_id, temp = r.json()["user"]["id"], r.json()["temporary_password"]
        assert r.json()["user"]["email"] == "pat@example.com" and "password_hash" not in r.json()["user"]
        assert admin.post("/users", json={"email": "pat@example.com", "name": "Dup"}).status_code == 409
        assert admin.post("/users", json={"email": "not-an-email", "name": "X"}).status_code == 400
        ok("admin adds a user with a temporary password; duplicate and invalid emails refused")

        pat = browser()
        r = login(pat, "pat@example.com", temp)
        assert r.status_code == 200 and r.json()["must_change_password"] is True
        r = pat.get("/reports")
        assert r.status_code == 403 and r.json()["code"] == "password_change_required"
        assert pat.get("/auth/me").status_code == 200
        assert pat.put("/auth/me", json={"name": "Someone Else"}).status_code == 403
        assert pat.post("/auth/password", json={"current_password": "nope", "new_password": "pat-new-password"}).status_code == 400
        assert pat.post("/auth/password", json={"current_password": temp, "new_password": "short"}).status_code == 400
        assert pat.post("/auth/password", json={"current_password": temp, "new_password": "pat-new-password"}).status_code == 200
        assert pat.get("/reports").status_code == 200
        ok("temporary password: nothing but changing it works until it is changed")

        print("roles")
        for method, path in (("get", "/users"), ("get", "/system/info"), ("get", "/setup/llm-keys"), ("post", "/reports/ai-enrich-all")):
            assert getattr(pat, method)(path).status_code == 403, path
        assert pat.put("/settings", json={"company_name": "X"}).status_code == 403
        assert pat.get("/settings").status_code == 200
        assert admin.put("/settings", json={"company_name": "Carlink"}).status_code == 200
        ok("surveyors can't reach users, keys, system info, settings changes or batch enrich")

        print("signing off as yourself")
        case_id = new_case(pat)
        assert pat.put(f"/reports/{case_id}", json={"draft": {"category": ["Other"], "description": "Edit 1"}}).status_code == 200
        assert pat.put(f"/reports/{case_id}", json={"draft": {"category": ["Other"], "description": "Edit 2"}}).status_code == 200
        r = pat.post(f"/reports/{case_id}/sign-off", json={
            "surveyor_name": "Patrick Ng", "license_number": "SURV-SG-0492", "signature_hash": "FORGED", "terms_accepted": True,
            "signature_data_url": "http://169.254.169.254/latest/meta-data/",
        })
        assert r.status_code == 200, r.text
        sign = r.json()["sign_off"]
        assert sign["surveyor_name"] == "Pat Surveyor" and sign["license_number"] == "LIC-123"
        assert sign["qualifications"] == "MIMI" and sign["firm_name"] == "Pat & Co."
        assert sign["signed_by_user_id"] == pat_id and sign["signature_hash"] != "FORGED"
        assert sign["signature_data_url"] is None
        assert pat.post(f"/reports/{case_id}/sign-off", json={}).status_code == 409
        ok("sign-off uses the signer's own profile; typed names, hashes and non-image signatures are ignored; no double sign-off")

        forged = {"status": "Signed Off", "surveyor_name": "Real Admin", "license_number": "LIC-ADMIN", "signature_hash": "DEADBEEF"}
        r = pat.post("/reports", json={"draft": {"category": ["Other"], "description": "forged", "sign_off": forged}})
        forged_id = r.json()["id"]
        assert pat.get(f"/reports/{forged_id}").json()["data"]["sign_off"]["status"] == "Draft"
        r = pat.put(f"/reports/{forged_id}", json={"draft": {"category": ["Other"], "description": "forged 2", "sign_off": forged}})
        assert r.status_code == 200
        stored = pat.get(f"/reports/{forged_id}").json()["data"]["sign_off"]
        assert stored["status"] == "Draft" and stored["surveyor_name"] is None and stored["license_number"] is None
        forged_draft = pat.post("/intake").json()["id"]
        assert pat.put(f"/intake/{forged_draft}/data", json={"data": {"description": "", "sign_off": forged}}).status_code == 200
        assert pat.get(f"/intake/{forged_draft}").json()["data"]["sign_off"]["status"] == "Draft"
        r = pat.post(f"/intake/{forged_draft}/finalize", json={"data": {"category": ["Other"], "description": "x", "sign_off": forged}})
        assert r.status_code == 200, r.text
        assert pat.get(f"/reports/{forged_draft}").json()["data"]["sign_off"]["status"] == "Draft"
        ok("a sign-off sent inside a case save, autosave or finalize is ignored -- only /sign-off signs")

        print("reopening and deleting")
        r = admin.post("/users", json={"email": "sam@example.com", "name": "Sam Surveyor"})
        sam = browser()
        login(sam, "sam@example.com", r.json()["temporary_password"])
        sam.post("/auth/password", json={"current_password": r.json()["temporary_password"], "new_password": "sam-new-password"})
        assert sam.post(f"/reports/{case_id}/reopen").status_code == 403
        assert pat.post(f"/reports/{case_id}/reopen").status_code == 200
        ok("only the signer or an admin may reopen")
        assert pat.delete(f"/reports/{case_id}").status_code == 403
        draft_by_pat = pat.post("/intake").json()["id"]
        assert pat.delete(f"/reports/{draft_by_pat}").status_code == 200
        ok("surveyors can throw away an unfinished intake but not delete a case")

        history = pat.get(f"/reports/{case_id}").json()["history"]
        actions = [(h["action"], h["user_name"]) for h in history]
        assert actions == [("created", "Pat Surveyor"), ("edited", "Pat Surveyor"), ("signed_off", "Pat Surveyor"), ("reopened", "Pat Surveyor")], actions
        assert history[2]["detail"] == "pat@example.com"
        ok("history: created, edited (two saves = one entry), signed off (with the account email), reopened")

        for rid in (forged_id, forged_draft):
            assert admin.delete(f"/reports/{rid}").status_code == 200
        assert admin.delete(f"/reports/{case_id}").status_code == 200
        assert admin.delete(f"/reports/{draft_by_admin}").status_code == 200
        ok("admin deletes a case")

        print("lockout")
        for _ in range(4):
            assert login(browser(), "sam@example.com", "wrong-password").status_code == 401
        assert login(browser(), "sam@example.com", "wrong-password").status_code == 429
        assert login(browser(), "sam@example.com", "sam-new-password").status_code == 429
        ok("5 wrong passwords lock the account; even the right one is refused while locked")
        db = SessionLocal()
        db.query(User).filter(User.email == "sam@example.com").update({"locked_until": utc_now() - timedelta(seconds=1)})
        db.commit()
        db.close()
        assert login(browser(), "sam@example.com", "sam-new-password").status_code == 200
        ok("the lock lifts after its time")

        other = {"X-Real-IP": "198.51.100.7"}
        codes = [login(browser(), "ghost@example.com", "wrong-password", headers=other).status_code for _ in range(6)]
        assert codes == [401, 401, 401, 401, 429, 429], codes
        ok("an email with no account locks the same way, so the lockout doesn't reveal which emails exist")

        guess = browser()
        login(guess, "sam@example.com", "sam-new-password")
        codes = [guess.post("/auth/password", json={"current_password": f"guess-{i}", "new_password": "whatever-1234"}).status_code for i in range(5)]
        assert codes == [400, 400, 400, 400, 429], codes
        assert guess.get("/auth/me").status_code == 401 and sam.get("/auth/me").status_code == 401
        db = SessionLocal()
        db.query(User).filter(User.email == "sam@example.com").update({"locked_until": None})
        db.commit()
        db.close()
        sam = browser()
        assert login(sam, "sam@example.com", "sam-new-password").status_code == 200
        ok("guessing the current password on a signed-in session locks the account and signs it out")

        print("admin account controls")
        users = admin.get("/users").json()
        assert any(u["email"] == "sam@example.com" for u in users)
        admin_id = [u for u in users if u["email"] == ADMIN_EMAIL][0]["id"]
        assert admin.patch(f"/users/{admin_id}", json={"active": False}).status_code == 409
        assert admin.patch(f"/users/{admin_id}", json={"role": "surveyor"}).status_code == 409
        ok("an admin can't disable themselves or demote the last admin")
        assert admin.patch(f"/users/{pat_id}", json={"active": False}).status_code == 200
        assert pat.get("/reports").status_code == 401
        assert login(browser(), "pat@example.com", "pat-new-password").status_code == 401
        assert admin.patch(f"/users/{pat_id}", json={"active": True}).status_code == 200
        ok("disabling a user signs them out at once and stops them signing in (same answer as a wrong password)")
        assert admin.patch(f"/users/{pat_id}", json={"role": "admin"}).status_code == 200
        assert admin.patch(f"/users/{pat_id}", json={"role": "surveyor"}).status_code == 200
        r = admin.post(f"/users/{pat_id}/reset-password")
        assert r.status_code == 200
        assert login(browser(), "pat@example.com", "pat-new-password").status_code == 401
        assert login(browser(), "pat@example.com", r.json()["temporary_password"]).json()["must_change_password"] is True
        assert admin.post(f"/users/{admin_id}/reset-password").status_code == 409
        ok("password reset: old password stops working, the temporary one must be changed")

        print("profile")
        r = sam.put("/auth/me", json={"name": "Sam S.", "license_number": "  LIC-9 "})
        assert r.status_code == 200 and r.json()["name"] == "Sam S." and r.json()["license_number"] == "LIC-9"
        assert sam.put("/auth/me", json={"name": "  "}).status_code == 400
        assert sam.put("/auth/me", json={"role": "admin"}).json()["role"] == "surveyor"
        db = SessionLocal()
        details = [e.detail for e in db.query(AuditEntry).filter(AuditEntry.action.in_(["profile_updated", "user_updated"])).all()]
        db.close()
        assert any("Sam S." in d and "LIC-9" in d for d in details), details
        assert any("role: 'surveyor' -> 'admin'" in d for d in details), details
        ok("users edit their own name and profile, never their role; profile and role changes are in the history")

        print("session end")
        token = sam.cookies.get(SESSION_COOKIE)
        r = sam.post("/auth/logout")
        assert r.status_code == 200 and SESSION_COOKIE in r.headers.get("set-cookie", "")
        stale = browser()
        stale.cookies.set(SESSION_COOKIE, token)
        assert stale.get("/reports").status_code == 401
        r = stale.post("/auth/logout")
        assert r.status_code == 200 and SESSION_COOKIE in r.headers.get("set-cookie", "")
        ok("signing out ends the session server-side; a dead cookie can still be cleared")

        sam2 = browser()
        login(sam2, "sam@example.com", "sam-new-password")
        db = SessionLocal()
        row = db.get(AuthSession, token_hash(sam2.cookies.get(SESSION_COOKIE)))
        row.last_seen_at = utc_now() - timedelta(hours=13)
        db.commit()
        assert sam2.get("/reports").status_code == 401
        sam3 = browser()
        login(sam3, "sam@example.com", "sam-new-password")
        row = db.get(AuthSession, token_hash(sam3.cookies.get(SESSION_COOKIE)))
        row.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
        assert sam3.get("/reports").status_code == 401
        ok("a session ends after 12 idle hours and at its hard limit")

        login(browser(), "sam@example.com", "sam-new-password")
        db.query(AuthSession).update({"last_seen_at": utc_now() - timedelta(days=2)})
        db.commit()
        assert sweep_sessions(db) >= 1 and db.query(AuthSession).count() == 0
        db.close()
        ok("the cleanup sweep removes dead sessions")

        print("per-network limit")
        far = {"X-Real-IP": "203.0.113.9"}
        for i in range(20):
            login(browser(), f"nobody{i}@example.com", "whatever-pw", headers=far)
        assert login(browser(), ADMIN_EMAIL, ADMIN_PASSWORD, headers=far).status_code == 429
        assert login(browser(), ADMIN_EMAIL, ADMIN_PASSWORD).status_code == 200
        ok("20 failures from one address block it, not everyone")

        print("server reset-password")
        r = run_manage("reset-password", "--email", "sam@example.com", "--password-stdin", stdin="sam-third-password\n")
        assert r.returncode == 0, r.stderr
        assert login(browser(), "sam@example.com", "sam-third-password").status_code == 200
        ok("reset-password from the command line")

    print("\nALL AUTH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
