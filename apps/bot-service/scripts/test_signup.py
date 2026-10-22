"""Tests for requesting access and one-time account links
(app/api/signup.py, app/auth/links.py, app/auth/passwords.py).

    python scripts/test_signup.py

Offline, against a temporary database.
"""
import os
import re
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="signup-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.auth.core import utc_now  # noqa: E402
from app.auth.links import sweep_signups  # noqa: E402
from app.auth.passwords import TOO_COMMON, TOO_PERSONAL, password_problem  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AccessRequest, AccountLink, AuditEntry, User  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

BASE = "https://carlink.test"
GOOD_PASSWORD = "purple tiger lamp bicycle"
_ip_counter = iter(range(1, 10_000))


def ok(label: str) -> None:
    print(f"    ok   {label}")


def browser() -> TestClient:
    return TestClient(app, base_url=BASE)


def fresh_ip() -> dict:
    return {"X-Real-IP": f"10.0.{next(_ip_counter) // 250}.{next(_ip_counter) % 250}"}


def request_body(**over) -> dict:
    body = {
        "name": "Tan Wei Ming", "email": "WeiMing@Example.com", "phone": "+65 9123 4567",
        "organisation": "Carlink", "requested_role": "admin", "note": "Starting 1 Nov",
        "consent": True, "website": "",
    }
    body.update(over)
    return body


def count(model, **filters) -> int:
    db = SessionLocal()
    try:
        return db.query(model).filter_by(**filters).count()
    finally:
        db.close()


def set_link(link_id: str, **values) -> None:
    db = SessionLocal()
    try:
        link = db.get(AccountLink, link_id)
        for k, v in values.items():
            setattr(link, k, v)
        db.commit()
    finally:
        db.close()


def main() -> int:
    init_db()
    init_db()  # a second start-up on an existing database is harmless

    print("password rules")
    assert password_problem("short-password") == "Use at least 15 characters."
    for weak in ("passwordpassword", "Password12345678!", "123456789012345678", "qwertyuiopasdfgh", "correct horse battery staple"):
        assert password_problem(weak) == TOO_COMMON, weak
    assert password_problem("tan-wei-ming-2026!", ("Tan Wei Ming", "weiming@example.com")) == TOO_PERSONAL
    assert password_problem(GOOD_PASSWORD, ("Tan Wei Ming", "weiming@example.com")) is None
    ok("15+ characters, not common or predictable, not built from the person's own name or email")

    admin = browser()
    admin_id = sign_in(admin, role="admin", name="Kelvin Lim")
    surveyor = browser()
    sign_in(surveyor, role="surveyor")
    anon = browser()

    print("requesting access")
    r = anon.post("/auth/access-requests", json=request_body(), headers=fresh_ip())
    assert r.status_code == 202 and re.fullmatch(r"REQ-[A-Z2-9]{4}", r.json()["reference"]), r.text
    db = SessionLocal()
    row = db.query(AccessRequest).filter_by(reference=r.json()["reference"]).one()
    assert row.email == "weiming@example.com" and row.status == "pending" and row.phone == "+65 9123 4567"
    db.close()
    ok("a valid request is accepted with a reference and stored lower-cased, as pending")

    for bad, message in [
        ({"email": "not-an-email"}, "email"),
        ({"name": " "}, "name"),
        ({"phone": "12ab"}, "mobile"),
        ({"consent": False}, "Tick the box"),
    ]:
        r = anon.post("/auth/access-requests", json=request_body(**bad), headers=fresh_ip())
        assert r.status_code == 400 and message in r.json()["detail"], (bad, r.text)
    ok("bad email, name or mobile and missing consent are refused with a message to show")

    existing = User(email="taken@example.com", name="Existing Person", role="surveyor", password_hash="!")
    db = SessionLocal()
    db.add(existing)
    db.commit()
    db.close()
    r_new = anon.post("/auth/access-requests", json=request_body(email="nobody-yet@example.com"), headers=fresh_ip())
    r_taken = anon.post("/auth/access-requests", json=request_body(email="taken@example.com"), headers=fresh_ip())
    assert r_new.status_code == r_taken.status_code == 202
    assert set(r_new.json()) == set(r_taken.json()) == {"reference"}
    ok("an email that already has an account gets exactly the same answer")

    before = count(AccessRequest)
    r = anon.post("/auth/access-requests", json=request_body(email="bot@example.com", website="http://spam"), headers=fresh_ip())
    assert r.status_code == 202 and count(AccessRequest) == before
    ok("a filled-in honeypot gets the normal answer but nothing is stored")

    ip = fresh_ip()
    codes = [anon.post("/auth/access-requests", json=request_body(email=f"flood{i}@example.com"), headers=ip).status_code for i in range(6)]
    assert codes == [202] * 5 + [429], codes
    assert anon.post("/auth/access-requests", json=request_body(email="other@example.com"), headers=fresh_ip()).status_code == 202
    ok("a sixth request from one network in an hour gets 429; other networks are unaffected")

    for _ in range(4):
        anon.post("/auth/access-requests", json=request_body(email="same@example.com"), headers=fresh_ip())
    assert count(AccessRequest, email="same@example.com", status="pending") == 3
    ok("at most 3 pending requests per email are kept")

    foreign = {"Origin": "https://evil.sslip.io", **fresh_ip()}
    for path, body in [("/auth/access-requests", request_body()), ("/auth/links/lookup", {"token": "x"}),
                       ("/auth/links/accept", {"token": "x", "name": "A", "password": GOOD_PASSWORD})]:
        assert anon.post(path, json=body, headers=foreign).status_code == 403, path
    ok("all three public POSTs refuse a request from another site's page")

    print("admin decisions")
    for method, path in [("get", "/access-requests"), ("get", "/account-links"), ("post", "/account-links"),
                         ("post", "/access-requests/x/approve"), ("post", f"/users/{admin_id}/reset-link")]:
        assert getattr(anon, method)(path).status_code == 401, path
        assert getattr(surveyor, method)(path, **({"json": {}} if method == "post" else {})).status_code == 403, path
    ok("request and link management needs an admin: 401 signed out, 403 for a surveyor")

    pending = admin.get("/access-requests").json()
    wei = next(p for p in pending if p["email"] == "weiming@example.com")
    taken = next(p for p in pending if p["email"] == "taken@example.com")
    same = [p for p in pending if p["email"] == "same@example.com"]
    assert taken["has_account"] and not wei["has_account"] and same[0]["same_email_pending"] == 2
    assert admin.get("/auth/me").json()["pending_access_requests"] == len(pending)
    ok("pending list flags existing accounts and repeat emails; /auth/me carries the count for the badge")

    assert admin.post(f"/access-requests/{wei['id']}/approve", json={}).status_code == 422
    assert admin.post(f"/access-requests/{taken['id']}/approve", json={"role": "surveyor"}).status_code == 409
    r = admin.post(f"/access-requests/{wei['id']}/approve", json={"role": "surveyor", "expires_hours": 72})
    assert r.status_code == 200, r.text
    approved = r.json()
    token = approved["token"]
    assert approved["request"]["status"] == "approved" and approved["link"]["role"] == "surveyor"
    assert admin.post(f"/access-requests/{wei['id']}/approve", json={"role": "admin"}).status_code == 409
    assert count(AuditEntry, action="access_request_approved") == 1
    ok("approving needs an explicit role (the request asked for admin; surveyor was chosen), once, and is audited")

    links = admin.get("/account-links").json()
    assert all("token" not in l and "token_hash" not in l for l in links)
    db = SessionLocal()
    stored = db.query(AccountLink).filter_by(id=approved["link"]["id"]).one()
    assert stored.token_hash != token and token not in stored.token_hash
    db.close()
    ok("tokens are never listed, and the database holds only their SHA-256")

    rej = same[0]
    r = admin.post(f"/access-requests/{rej['id']}/reject", json={"note": "Not with the firm"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert admin.delete(f"/access-requests/{same[1]['id']}").json() == {"ok": True}
    ok("a request can be rejected with a note, or erased")

    print("using an invite")
    user_ip = fresh_ip()
    for _ in range(2):
        r = anon.post("/auth/links/lookup", json={"token": token}, headers=user_ip)
        assert r.status_code == 200, r.text
    info = r.json()
    assert info == {"purpose": "invite", "email": "weiming@example.com", "role": "surveyor",
                    "name": "Tan Wei Ming", "invited_by": "Kelvin Lim", "expires_at": info["expires_at"]}
    ok("lookup shows email, role and inviter, and doesn't use the link up")

    r = anon.post("/auth/links/accept", json={"token": token, "name": "Tan Wei Ming", "password": "too-short"}, headers=user_ip)
    assert r.status_code == 400 and "15" in r.json()["detail"]
    r = anon.post("/auth/links/accept", json={"token": token, "name": "  ", "password": GOOD_PASSWORD}, headers=user_ip)
    assert r.status_code == 400
    r = anon.post("/auth/links/accept", json={"token": token, "name": "Tan Wei Ming", "password": "tan wei ming 2026 x"}, headers=user_ip)
    assert r.status_code == 400 and r.json()["detail"] == TOO_PERSONAL
    ok("a short or personal password, or a blank name, is refused and leaves the link usable")

    newcomer = browser()
    r = newcomer.post("/auth/links/accept", json={"token": token, "name": "Tan Wei Ming", "password": GOOD_PASSWORD, "role": "admin"}, headers=user_ip)
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    me = newcomer.get("/auth/me").json()
    assert me["email"] == "weiming@example.com" and me["role"] == "surveyor" and not me["must_change_password"]
    assert "pending_access_requests" not in me
    ok("accepting creates the account with the link's role (a role in the body is ignored) and signs in")

    for path in ("/auth/links/accept", "/auth/links/lookup"):
        r = anon.post(path, json={"token": token, "name": "X", "password": GOOD_PASSWORD}, headers=user_ip)
        assert r.status_code == 410 and r.json()["code"] == "used", (path, r.text)
    assert count(User, email="weiming@example.com") == 1
    assert browser().post("/auth/login", json={"email": "weiming@example.com", "password": GOOD_PASSWORD}).status_code == 200
    ok("the link then answers 410 used, and the new password signs in normally")

    race = admin.post("/account-links", json={"email": "race@example.com", "role": "surveyor"}).json()["token"]
    results = []
    barrier = threading.Barrier(2)

    def accept() -> None:
        barrier.wait()
        results.append(browser().post("/auth/links/accept", json={"token": race, "name": "Racer", "password": GOOD_PASSWORD}, headers=fresh_ip()).status_code)

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 410], results
    assert count(User, email="race@example.com") == 1
    ok("two browsers using one link at once: exactly one account")

    print("expiry, revoking and replacing")
    assert admin.post("/account-links", json={"email": "taken@example.com", "role": "surveyor"}).status_code == 409
    first = admin.post("/account-links", json={"email": "late@example.com", "role": "surveyor", "name": "Late Comer"}).json()
    second = admin.post("/account-links", json={"email": "late@example.com", "role": "surveyor"}).json()
    r = anon.post("/auth/links/lookup", json={"token": first["token"]}, headers=fresh_ip())
    assert r.status_code == 410 and r.json()["code"] == "invalid"
    ok("a new link for the same email cancels the old one")

    set_link(second["link"]["id"], expires_at=utc_now() - timedelta(minutes=1))
    r = anon.post("/auth/links/lookup", json={"token": second["token"]}, headers=fresh_ip())
    assert r.status_code == 410 and r.json()["code"] == "expired"
    third = admin.post("/account-links", json={"email": "late@example.com", "role": "surveyor"}).json()
    assert admin.delete(f"/account-links/{third['link']['id']}").json()["status"] == "revoked"
    r = anon.post("/auth/links/accept", json={"token": third["token"], "name": "Late", "password": GOOD_PASSWORD}, headers=fresh_ip())
    assert r.status_code == 410 and r.json()["code"] == "invalid"
    ok("expired and revoked links refuse with 410")

    second_admin = browser()
    second_admin_id = sign_in(second_admin, role="admin", name="Leaving Admin")
    orphan = second_admin.post("/account-links", json={"email": "orphan@example.com", "role": "admin"}).json()
    assert admin.patch(f"/users/{second_admin_id}", json={"active": False}).status_code == 200
    r = anon.post("/auth/links/lookup", json={"token": orphan["token"]}, headers=fresh_ip())
    assert r.status_code == 410
    ok("a disabled admin's outstanding invites stop working")

    bad_ip = fresh_ip()
    codes = [anon.post("/auth/links/lookup", json={"token": f"guess-{i}"}, headers=bad_ip).status_code for i in range(21)]
    assert codes[:20] == [410] * 20 and codes[20] == 429, codes
    ok("guessing tokens from one network is cut off after 20 tries")

    print("password reset links")
    db = SessionLocal()
    wei_user = db.query(User).filter_by(email="weiming@example.com").one()
    db.close()
    assert admin.post(f"/users/{admin_id}/reset-link").status_code == 409
    reset = admin.post(f"/users/{wei_user.id}/reset-link").json()
    assert reset["link"]["purpose"] == "reset" and reset["link"]["status"] == "open"
    r = anon.post("/auth/links/lookup", json={"token": reset["token"]}, headers=fresh_ip())
    assert r.json()["purpose"] == "reset" and r.json()["email"] == "weiming@example.com"
    new_password = "orange river kite morning"
    r = browser().post("/auth/links/accept", json={"token": reset["token"], "name": "", "password": new_password}, headers=fresh_ip())
    assert r.status_code == 200, r.text
    assert newcomer.get("/auth/me").status_code == 401
    assert browser().post("/auth/login", json={"email": "weiming@example.com", "password": GOOD_PASSWORD}).status_code == 401
    assert browser().post("/auth/login", json={"email": "weiming@example.com", "password": new_password}).status_code == 200
    assert count(AuditEntry, action="password_reset_by_link") == 1
    ok("a reset link sets a new password, signs out old sessions, and the old password stops working")

    print("cleanup")
    removed = sweep_signups(SessionLocal(), utc_now() + timedelta(days=61))
    assert removed["links"] >= 5 and removed["requests"] >= 5, removed
    assert count(AccessRequest) == 0 and count(AccountLink) == 0
    ok(f"old links and requests are deleted by the sweep ({removed})")

    print("\nALL SIGNUP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
