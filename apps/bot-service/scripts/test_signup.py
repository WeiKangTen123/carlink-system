"""Tests for open signup, usernames, the user/admin roles and one-time
account links (app/api/signup.py, app/api/auth.py, app/auth/links.py,
app/auth/passwords.py, the upgrade steps in app/reports/db.py).

    python scripts/test_signup.py

Offline, against a temporary database.
"""
import os
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
from sqlalchemy import inspect, text  # noqa: E402

import app.api.signup as signup_api  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, utc_now  # noqa: E402
from app.auth.links import claim, sweep_links  # noqa: E402
from app.auth.passwords import TOO_COMMON, TOO_PERSONAL, password_problem  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import AccountLink, AuditEntry, User  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

BASE = "https://carlink.test"
GOOD_PASSWORD = "purple tiger lamp bicycle"
_ip_counter = iter(range(1, 100_000))


def ok(label: str) -> None:
    print(f"    ok   {label}")


def browser() -> TestClient:
    return TestClient(app, base_url=BASE)


def fresh_ip() -> dict:
    n = next(_ip_counter)
    return {"X-Real-IP": f"10.{n // 65536}.{(n // 256) % 256}.{n % 256}"}


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


def signup(client: TestClient, username: str, email: str, password: str = GOOD_PASSWORD, headers: dict | None = None, **extra):
    body = {"username": username, "email": email, "password": password, **extra}
    return client.post("/auth/signup", json=body, headers=headers or fresh_ip())


def check_upgrade() -> None:
    """Databases from before usernames and the "user" role: the start-up
    steps fill in usernames, rename the role and drop the old requests."""
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS access_requests (id VARCHAR PRIMARY KEY, email VARCHAR)"))
    db = SessionLocal()
    db.add_all([
        User(email="weikangten@gmail.com", name="Wei Kang", role="admin", password_hash="!"),
        User(email="weikangten@other.example", name="Another Wei Kang", role="surveyor", password_hash="!"),
        User(email="a@b.example", name="Short", role="surveyor", password_hash="!"),
        User(email="admin@corp.example", name="Reserved", role="surveyor", password_hash="!"),
    ])
    db.commit()
    db.close()
    init_db()
    init_db()  # a second start-up on an upgraded database is harmless
    db = SessionLocal()
    rows = {u.email: u for u in db.query(User).all()}
    db.close()
    assert rows["weikangten@gmail.com"].username == "weikangten" and rows["weikangten@gmail.com"].role == "admin"
    assert rows["weikangten@other.example"].username == "weikangten2"
    assert rows["a@b.example"].username == "usera" and rows["admin@corp.example"].username == "useradmin"
    assert all(u.role in ("admin", "user") for u in rows.values())
    assert "access_requests" not in inspect(engine).get_table_names()
    ok("upgrade: usernames made from emails (unique, never reserved), surveyor -> user, old requests table dropped")


def main() -> int:
    init_db()
    check_upgrade()

    print("password rules")
    from app.config import settings
    assert settings.strict_password_rules is False
    assert password_problem("weikang12") == "Use at least 10 characters."
    assert password_problem("weikang123") is None and password_problem("passwordpassword") is None
    ok("by default: at least 10 characters, no other checks (the owner's choice for now)")
    settings.strict_password_rules = True
    assert password_problem("short-password") == "Use at least 15 characters."
    for weak in ("passwordpassword", "passwordpassword1", "!!iloveyouiloveyou2026", "Password12345678!", "123456789012345678", "qwertyuiopasdfgh", "correct horse battery staple"):
        assert password_problem(weak) == TOO_COMMON, weak
    assert password_problem("tan-wei-ming-2026!", ("Tan Wei Ming", "weiming@example.com")) == TOO_PERSONAL
    assert password_problem(GOOD_PASSWORD, ("Tan Wei Ming", "weiming@example.com")) is None
    ok("strict rules (STRICT_PASSWORD_RULES=true): 15+, not common or predictable, not personal")
    settings.strict_password_rules = False  # the rest runs with the default rules

    admin = browser()
    admin_id = sign_in(admin, role="admin", name="Kelvin Lim")
    anon = browser()

    print("signing up")
    assert anon.get("/auth/signup").json() == {"open": True}
    newcomer = browser()
    r = signup(newcomer, "  Ali.Tan ", "Ali@Example.com", role="admin")
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["username"] == "ali.tan" and me["email"] == "ali@example.com" and me["name"] == "ali.tan"
    assert me["role"] == "user" and not me["must_change_password"]
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("__host-carlink_session=") and "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert newcomer.get("/auth/me").json()["username"] == "ali.tan"
    assert newcomer.get("/reports").status_code == 200
    ok("username + email + password makes a 'user' account and signs it in (a role in the body is ignored)")
    assert newcomer.get("/users").status_code == 403 and newcomer.get("/system/info").status_code == 403
    assert newcomer.put("/signup-settings", json={"open": False}).status_code == 403
    ok("a new user can work on cases but not reach users, system info or the signup switch")
    assert count(AuditEntry, action="signed_up") == 1

    for login in ("ali.tan", "ALI.TAN", "ali@example.com"):
        r = browser().post("/auth/login", json={"email": login, "password": GOOD_PASSWORD})
        assert r.status_code == 200, (login, r.text)
    r = browser().post("/auth/login", json={"email": "ali.tan", "password": "wrong-password"})
    assert r.status_code == 401 and r.json()["detail"] == "Email, username or password is wrong."
    ghost_ip = {"X-Real-IP": "198.51.100.40"}
    codes = [browser().post("/auth/login", json={"email": "ghost.user", "password": "wrong-password"}, headers=ghost_ip).status_code for _ in range(6)]
    assert codes == [401, 401, 401, 401, 429, 429], codes
    ok("sign in with the username (any case) or the email; unknown usernames lock out like unknown emails")

    for i, (username, status) in enumerate([("ab", 400), ("_ali", 400), ("ali tan", 400), ("x" * 31, 400), ("Admin", 400), ("ALI.TAN", 409)]):
        r = signup(anon, username, f"new{i}@example.com")
        assert r.status_code == status and r.json()["field"] == "username", (username, r.text)
    r = signup(anon, "fresh.one", "not-an-email")
    assert r.status_code == 400 and r.json()["field"] == "email"
    r = signup(anon, "fresh.one", "ALI@example.com")
    assert r.status_code == 409 and r.json()["field"] == "email" and "Sign in" in r.json()["detail"]
    r = signup(anon, "fresh.one", "fresh@example.com", password="short")
    assert r.status_code == 400 and r.json()["field"] == "password" and "10" in r.json()["detail"]
    assert count(User, email="fresh@example.com") == 0
    ok("bad, reserved or taken usernames, bad or taken emails and short passwords each name the field to fix")

    before = count(User)
    r = signup(anon, "bot.user", "bot@example.com", website="http://spam")
    assert r.status_code == 400 and count(User) == before
    ok("a filled-in honeypot makes no account")

    r = anon.post("/auth/signup", json={"username": "evil.one", "email": "evil@example.com", "password": GOOD_PASSWORD},
                  headers={"Origin": "https://evil.34-45-253-162.sslip.io", **fresh_ip()})
    assert r.status_code == 403 and count(User, email="evil@example.com") == 0
    ok("a signup posted from another site's page is refused")

    ip = fresh_ip()
    codes = [signup(anon, f"flood{i}", f"flood{i}@example.com", headers=ip).status_code for i in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429, codes
    assert signup(anon, "other.network", "other@example.com").status_code == 200
    ok("a 21st signup try from one network in an hour gets 429; other networks are unaffected")

    old_cap = signup_api.MAX_SIGNUPS_PER_DAY
    signup_api.MAX_SIGNUPS_PER_DAY = count(User, active=True)
    r = signup(anon, "one.too.many", "toomany@example.com")
    assert r.status_code == 503 and count(User, email="toomany@example.com") == 0
    signup_api.MAX_SIGNUPS_PER_DAY = old_cap
    ok("beyond the daily total, signup pauses (503) instead of filling the users table")

    switcher = browser()
    sign_in(switcher, role="user", name="Someone Else")
    old_cookie = switcher.cookies.get(SESSION_COOKIE)
    assert signup(switcher, "switch.person", "switch@example.com").status_code == 200
    stale = browser()
    stale.cookies.set(SESSION_COOKIE, old_cookie)
    assert stale.get("/auth/me").status_code == 401
    ok("signing up while signed in as someone else ends that other session")

    results = []
    barrier = threading.Barrier(2)

    def racer(i: int) -> None:
        barrier.wait()
        results.append(signup(browser(), "same.name", f"racer{i}@example.com").status_code)

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409], results
    assert count(User, username="same.name") == 1
    ok("two signups racing for one username: exactly one account")

    print("the signup switch")
    assert browser().put("/signup-settings", json={"open": False}).status_code == 401
    assert admin.put("/signup-settings", json={"open": False}).json() == {"open": False}
    assert anon.get("/auth/signup").json() == {"open": False}
    r = signup(anon, "late.comer", "late@example.com")
    assert r.status_code == 403 and "closed" in r.json()["detail"]
    invite = admin.post("/account-links", json={"email": "invited@example.com", "role": "user"}).json()
    r = browser().post("/auth/links/accept", json={"token": invite["token"], "username": "invited.one", "password": GOOD_PASSWORD}, headers=fresh_ip())
    assert r.status_code == 200 and r.json()["username"] == "invited.one", r.text
    assert admin.put("/signup-settings", json={"open": True}).json() == {"open": True}
    assert signup(anon, "late.comer", "late@example.com").status_code == 200
    assert count(AuditEntry, action="signups_closed") == 1 and count(AuditEntry, action="signups_opened") == 1
    ok("an admin can close signups (invites still work) and open them again; both are in the history")

    print("roles and usernames")
    db = SessionLocal()
    ali_id = db.query(User).filter_by(username="ali.tan").one().id
    db.close()
    assert newcomer.patch(f"/users/{ali_id}", json={"role": "admin"}).status_code == 403
    assert newcomer.put("/auth/me", json={"role": "admin"}).json()["role"] == "user"
    assert admin.patch(f"/users/{ali_id}", json={"role": "surveyor"}).status_code == 422
    r = admin.patch(f"/users/{ali_id}", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert newcomer.get("/users").status_code == 200
    assert admin.patch(f"/users/{ali_id}", json={"role": "user"}).json()["role"] == "user"
    assert newcomer.get("/users").status_code == 403
    ok("only an admin changes roles (user <-> admin); it takes effect on the next request")

    r = newcomer.put("/auth/me", json={"username": "Ali_Tan", "name": "Ali Tan"})
    assert r.status_code == 200 and r.json()["username"] == "ali_tan" and r.json()["name"] == "Ali Tan", r.text
    assert newcomer.put("/auth/me", json={"username": "switch.person"}).status_code == 409
    assert newcomer.put("/auth/me", json={"username": "a"}).status_code == 400
    assert browser().post("/auth/login", json={"email": "ali_tan", "password": GOOD_PASSWORD}).status_code == 200
    assert admin.patch(f"/users/{ali_id}", json={"username": "ali.t"}).json()["username"] == "ali.t"
    db = SessionLocal()
    details = [e.detail for e in db.query(AuditEntry).filter(AuditEntry.action.in_(["profile_updated", "user_updated"])).all()]
    db.close()
    assert any("username: 'ali.tan' -> 'ali_tan'" in d for d in details), details
    ok("users change their own username and full name (unique, recorded in the history); admins can too")

    users = admin.get("/users").json()
    assert all("username" in u and "password_hash" not in u for u in users)
    ok("the user list carries usernames, never password hashes")

    print("passing for someone else")
    for name in ("Kelvin Lim", "  kelvin   LIM ", "Ｋｅｌｖｉｎ Ｌｉｍ", "Carlink Admin", "Sys Admin", "Support Team"):
        r = newcomer.put("/auth/me", json={"name": name})
        assert r.status_code == 400, (name, r.text)
    assert admin.patch(f"/users/{ali_id}", json={"name": "Carlink Support"}).status_code == 200
    assert newcomer.put("/auth/me", json={"name": "Ali Tan"}).status_code == 200
    ok("people can't name themselves like another account or like the system/admins (an admin still can)")

    case_id = newcomer.post("/reports", json={"draft": {"category": ["Other"], "description": "history name"}}).json()["id"]
    history = admin.get(f"/reports/{case_id}").json()["history"]
    assert history[0]["action"] == "created" and history[0]["user_name"] == "Ali Tan (@ali.t)", history
    ok("the history names an account by full name and username, so two 'Ali Tan's can't be confused")

    for bad in ("weikangten@gmail.com\u200b", "weikangten@gmail.com\u00ad", "\uff57eikangten@gmail.com", "we\u0456kang@gmail.com", "a@b"):
        r = signup(anon, "lookalike.user", bad)
        assert r.status_code == 400 and r.json()["field"] == "email", (repr(bad), r.text)
    ok("emails with invisible or look-alike characters are refused (plain ASCII only)")

    for name in ("sysadmin", "carlink.support", "the-owner", "root.user"):
        r = signup(anon, name, f"{name.replace('.', '').replace('-', '')}@example.com")
        assert r.status_code == 400 and r.json()["field"] == "username", (name, r.text)
    assert newcomer.put("/auth/me", json={"username": "ali.admin"}).status_code == 400
    assert admin.patch(f"/users/{ali_id}", json={"username": "carlink.ali"}).json()["username"] == "carlink.ali"
    assert admin.patch(f"/users/{ali_id}", json={"username": "ali.t"}).status_code == 200
    ok("usernames people choose can't contain admin/carlink/support/owner/root...; an admin can still give one")

    prober = browser()
    sign_in(prober, role="user", name="Prober")
    codes = [prober.put("/auth/me", json={"username": f"probe.{i}"}).status_code for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429, codes
    ok("an account can change its username at most 10 times an hour (so it can't probe which names exist)")

    invite_for = admin.post("/account-links", json={"email": "expected@example.com", "role": "admin"}).json()
    r = signup(anon, "squatter", "expected@example.com")
    assert r.status_code == 409 and r.json()["field"] == "email" and "invited" in r.json()["detail"], r.text
    r = browser().post("/auth/links/accept", json={"token": invite_for["token"], "username": "expected.one", "password": GOOD_PASSWORD}, headers=fresh_ip())
    assert r.status_code == 200 and r.json()["role"] == "admin", r.text
    ok("while someone has an open invite, nobody else can sign up with their email")

    racers = []
    for i in range(2):
        c = browser()
        sign_in(c, role="user", name=f"Renamer {i}")
        racers.append(c)
    results = []
    barrier = threading.Barrier(2)

    def rename(c) -> None:
        barrier.wait()
        results.append(c.put("/auth/me", json={"username": "contested"}).status_code)

    threads = [threading.Thread(target=rename, args=(c,)) for c in racers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409], results
    ok("two accounts renaming to the same username at once: one gets it, the other a 409 (not a crash)")

    print("invite links")
    anon_ip = fresh_ip()
    token = admin.post("/account-links", json={"email": "WeiMing@Example.com", "role": "user", "name": "Tan Wei Ming"}).json()["token"]
    for _ in range(2):
        r = anon.post("/auth/links/lookup", json={"token": token}, headers=anon_ip)
        assert r.status_code == 200, r.text
    info = r.json()
    assert info == {"purpose": "invite", "email": "weiming@example.com", "role": "user",
                    "name": "Tan Wei Ming", "invited_by": "Kelvin Lim", "expires_at": info["expires_at"]}
    ok("lookup shows email, role and inviter, and doesn't use the link up")

    for body, status in [({"username": "ali.t"}, 409), ({"username": "x"}, 400), ({"username": "wei.ming", "password": "short"}, 400)]:
        r = anon.post("/auth/links/accept", json={"token": token, "password": GOOD_PASSWORD, **body}, headers=anon_ip)
        assert r.status_code == status, (body, r.text)
    ok("a taken or bad username, or a short password, is refused and leaves the link usable")

    invited = browser()
    r = invited.post("/auth/links/accept", json={"token": token, "username": "Wei.Ming", "password": GOOD_PASSWORD, "role": "admin"}, headers=anon_ip)
    assert r.status_code == 200, r.text
    me = invited.get("/auth/me").json()
    assert me["username"] == "wei.ming" and me["role"] == "user" and me["email"] == "weiming@example.com"
    assert me["name"] == "Tan Wei Ming"  # the name the admin typed, for sign-offs
    for path in ("/auth/links/accept", "/auth/links/lookup"):
        r = anon.post(path, json={"token": token, "username": "again.one", "password": GOOD_PASSWORD}, headers=anon_ip)
        assert r.status_code == 410 and r.json()["code"] == "used", (path, r.text)
    ok("accepting makes the account with the link's role and the chosen username; the link then answers 410 used")

    links = admin.get("/account-links").json()
    assert all("token" not in l and "token_hash" not in l for l in links)
    ok("tokens are never listed")

    race = admin.post("/account-links", json={"email": "race@example.com", "role": "user"}).json()["token"]
    results = []
    barrier = threading.Barrier(2)

    def accept(i: int) -> None:
        barrier.wait()
        results.append(browser().post("/auth/links/accept", json={"token": race, "username": f"racer.{i}", "password": GOOD_PASSWORD}, headers=fresh_ip()).status_code)

    threads = [threading.Thread(target=accept, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 410], results
    assert count(User, email="race@example.com") == 1
    ok("two browsers using one link at once: exactly one account")

    assert admin.post("/account-links", json={"email": "ali@example.com", "role": "user"}).status_code == 409
    first = admin.post("/account-links", json={"email": "later@example.com", "role": "user"}).json()
    second = admin.post("/account-links", json={"email": "later@example.com", "role": "user"}).json()
    assert anon.post("/auth/links/lookup", json={"token": first["token"]}, headers=fresh_ip()).json()["code"] == "invalid"
    set_link(second["link"]["id"], expires_at=utc_now() - timedelta(minutes=1))
    db = SessionLocal()
    assert not claim(db, db.get(AccountLink, second["link"]["id"]))
    db.rollback()
    db.close()
    assert anon.post("/auth/links/lookup", json={"token": second["token"]}, headers=fresh_ip()).json()["code"] == "expired"
    third = admin.post("/account-links", json={"email": "later@example.com", "role": "user"}).json()
    assert admin.delete(f"/account-links/{third['link']['id']}").json()["status"] == "revoked"
    assert anon.post("/auth/links/lookup", json={"token": third["token"]}, headers=fresh_ip()).json()["code"] == "invalid"
    ok("a new link cancels the old one; expired and revoked links refuse with 410")

    second_admin = browser()
    second_admin_id = sign_in(second_admin, role="admin", name="Leaving Admin")
    orphan = second_admin.post("/account-links", json={"email": "orphan@example.com", "role": "admin"}).json()
    assert admin.patch(f"/users/{second_admin_id}", json={"active": False}).status_code == 200
    assert anon.post("/auth/links/lookup", json={"token": orphan["token"]}, headers=fresh_ip()).status_code == 410
    ok("a disabled admin's outstanding invites stop working")

    bad_ip = fresh_ip()
    codes = [anon.post("/auth/links/lookup", json={"token": f"guess-{i}"}, headers=bad_ip).status_code for i in range(21)]
    assert codes[:20] == [410] * 20 and codes[20] == 429, codes
    ok("guessing tokens from one network is cut off after 20 tries")

    print("password reset links")
    db = SessionLocal()
    wei = db.query(User).filter_by(email="weiming@example.com").one()
    db.close()
    assert admin.post(f"/users/{admin_id}/reset-link").status_code == 409
    reset = admin.post(f"/users/{wei.id}/reset-link").json()
    new_password = "orange river kite morning"
    r = browser().post("/auth/links/accept", json={"token": reset["token"], "password": new_password}, headers=fresh_ip())
    assert r.status_code == 200 and r.json()["username"] == "wei.ming", r.text
    assert invited.get("/auth/me").status_code == 401
    assert browser().post("/auth/login", json={"email": "wei.ming", "password": GOOD_PASSWORD}).status_code == 401
    assert browser().post("/auth/login", json={"email": "wei.ming", "password": new_password}).status_code == 200
    ok("a reset link sets a new password (username unchanged) and signs out old sessions; the old password stops working")

    print("cleanup")
    removed = sweep_links(SessionLocal(), utc_now() + timedelta(days=61))
    assert removed >= 5 and count(AccountLink) == 0, removed
    ok(f"old links are deleted by the sweep ({removed})")

    print("\nALL SIGNUP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
