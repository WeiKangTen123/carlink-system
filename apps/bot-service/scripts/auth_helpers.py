"""Signs a TestClient in, for the API tests: every route needs a signed-in
user (app/auth/middleware.py). Import after the test has pointed
DATABASE_URL / STORAGE_DIR at its temp folder."""
from app.auth.core import SESSION_COOKIE, create_session
from app.reports.db import SessionLocal, init_db
from app.reports.models import User, gen_id


def sign_in(client, *, role: str = "admin", name: str = "Test Surveyor", **profile) -> str:
    """Creates a user with a session and puts the cookie on `client`.
    The user has no usable password (sessions only). Returns the user id."""
    init_db()
    db = SessionLocal()
    try:
        user = User(email=f"{role}-{gen_id()}@example.com", name=name, role=role, password_hash="!", **profile)
        db.add(user)
        db.flush()
        token = create_session(db, user)
        db.commit()
        user_id = user.id
    finally:
        db.close()
    client.cookies.set(SESSION_COOKIE, token)
    return user_id


def mark_must_haves_na(client, report_id: str) -> list[str]:
    """Before a test signs a case off: marks N/A each fact the sign-off's
    must-have list still misses (GET /reports/{id} review.before_sign_off;
    app/reports/review.MUST_HAVE, owner 23 Nov 2026), as a surveyor does
    when a fact doesn't apply -- the API refuses the sign-off otherwise.
    `client` must be staff (Our Ref is theirs). Returns the keys marked;
    none on a tree from before the list (no before_sign_off)."""
    got = client.get(f"/reports/{report_id}")
    review = (got.json().get("review") or {}) if got.status_code == 200 else {}
    keys = [key for item in review.get("before_sign_off") or [] for key in item.get("keys") or []]
    if keys:
        r = client.put(f"/reports/{report_id}/checked", json={"set": {key: {"na": True} for key in keys}})
        assert r.status_code == 200, r.text
    return keys
