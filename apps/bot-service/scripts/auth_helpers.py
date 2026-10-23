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
