"""Seeds a STAGING stack with made-up accounts, cases and photos, so the owner
and the lead can click through intake, review and sign-off without any real
customer's data. Refuses to run anywhere but staging.

    docker exec carlink_staging_api python scripts/seed_staging.py
    docker exec carlink_staging_api python scripts/seed_staging.py --close-signups-only
    docker exec carlink_staging_api python scripts/seed_staging.py --reset-passwords
    docker exec carlink_staging_api python scripts/seed_staging.py --images-out /app/storage/seed-photos
    docker exec carlink_staging_api cat /tmp/seed-staging-passwords.txt     # the new passwords, then:
    docker exec carlink_staging_api rm /tmp/seed-staging-passwords.txt

What it makes (again on a second run: only what is missing):
  - signups switched off (owner decision D3), and the company name
    "Carlink STAGING (test data)" when none is set;
  - three accounts: staging-admin (admin), staging-staff (a user ticked
    "sees all cases", "may use the company AI key", "may sign off") and
    staging-user (a plain self-signup user). New passwords are made at
    random and written to a 0600 file in the container, never printed
    (--passwords-file, default /tmp/seed-staging-passwords.txt -- gone when
    the container is recreated; read it, keep them in a password manager,
    delete it). --reset-passwords makes new ones;
  - placeholder photos drawn here (Pillow): a grey box of a car with
    "SYNTHETIC TEST PHOTO -- not a real vehicle" and a TEST plate on each.
    Never a customer's photo, never example_case;
  - four cases owned by staging-staff, all through the app's own API
    (in-process, no network):
      1. "filed"          a finished case (parts, photos, PDF on download);
      2. "signed off"     the same, signed off by staging-staff;
      3. "ready"          a draft at the Review step, as if the AI had read
                          it (a made-up reading: no AI call);
      4. "to process"     a draft with photos and no reading: Process runs
                          the real detection, in the staging lane of the
                          shared key (a few light-model calls).

No Gemini call is ever made by this script (the client is switched off
before anything runs). It refuses unless CARLINK_STACK=staging and the AI
lane is staging's (both set by infra/docker-compose.staging.yml), and
PUBLIC_BASE_URL is not production's.
"""
import argparse
import io
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROD_HOST = "carlink.34-16-92-98.sslip.io"
MARK = "[staging seed]"
COMPANY = "Carlink STAGING (test data)"
ACCOUNTS = [
    # username, name, role, ticks (sees_all_cases, ai_company_key_allowed, may_sign_off)
    ("staging-admin", "Staging Admin", "admin", (True, True, True)),
    ("staging-staff", "Staging Surveyor", "user", (True, True, True)),
    ("staging-user", "Staging Signup", "user", (False, False, False)),
]
CASES = [
    # key, title, photos, parts (part, damage type, severity, remarks)
    ("filed", "rear hit, filed", 4, [("Rear Bumper", "Dent", "Moderate", "Replace"),
                                     ("Left Tail Lamp", "Crack", "Severe", "Replace")]),
    ("signed off", "side scrape, signed off", 4, [("Left Front Door", "Scratch", "Minor", "Repair"),
                                                  ("Left Wing Mirror", "Broken / Detached", "Moderate", "Replace")]),
    ("ready", "front hit, ready for review", 6, [("Front Bumper", "Deformation", "Severe", "Replace"),
                                                 ("Bonnet", "Dent", "Moderate", "Repair"),
                                                 ("Right Headlamp", "Crack", "Moderate", "Replace")]),
    ("to process", "rear corner, to process", 6, []),
]


def refuse_unless_staging() -> None:
    stack = os.environ.get("CARLINK_STACK", "")
    if stack != "staging":
        sys.exit(f"seed_staging: refused -- CARLINK_STACK is {stack!r}, not 'staging'. "
                 "This only runs in the staging stack (infra/docker-compose.staging.yml).")
    from app.config import settings
    # Review L6: a second, independent sign -- not only production's host name.
    if settings.gemini_quota_lane != "staging":
        sys.exit("seed_staging: refused -- the AI lane is not staging's (GEMINI_QUOTA_LANE). Is this really staging?")
    if PROD_HOST in (settings.public_base_url or ""):
        sys.exit("seed_staging: refused -- PUBLIC_BASE_URL is production's address. Is this really staging?")


PASSWORDS_FILE = "/tmp/seed-staging-passwords.txt"


def write_passwords(path: Path, passwords: list[tuple[str, str]]) -> None:
    """The new passwords into a file only its owner can read (0600), never
    through a symlink -- they are never printed (CodeQL: clear-text
    logging of sensitive data)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (AttributeError, OSError):
        pass  # Windows (tests)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# Carlink STAGING seed accounts (made-up). Keep them in a password manager, then delete this file.\n")
        for username, password in passwords:
            f.write(f"{username} {password}\n")


# ------------------------------------------------------------------ photos

def placeholder_photo(n: int, case_no: int, damage: bool) -> bytes:
    """A 1600x1200 JPEG that is plainly not a photo of a real car."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1600, 1200
    hue = (n * 47 + case_no * 91) % 255
    img = Image.new("RGB", (w, h), (40 + hue // 4, 60 + hue // 5, 80 + hue // 6))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 8):  # a gradient sky, so every photo differs
        d.line([(0, y), (w, y)], fill=(60 + (y * hue) % 120, 90 + y // 12, 140 - y // 12))
    d.rectangle([0, 820, w, h], fill=(70, 70, 75))  # road
    body = (180 - hue // 3, 180 - hue // 4, 190 - hue // 5)
    d.rounded_rectangle([260, 520, 1340, 860], radius=60, fill=body, outline=(30, 30, 30), width=6)
    d.rounded_rectangle([480, 380, 1120, 560], radius=50, fill=body, outline=(30, 30, 30), width=6)
    d.rectangle([540, 410, 790, 530], fill=(150, 190, 220))
    d.rectangle([820, 410, 1070, 530], fill=(150, 190, 220))
    for cx in (480, 1120):
        d.ellipse([cx - 110, 760, cx + 110, 980], fill=(20, 20, 20))
        d.ellipse([cx - 50, 820, cx + 50, 920], fill=(160, 160, 160))
    d.rectangle([690, 760, 910, 820], fill=(250, 250, 250), outline=(0, 0, 0), width=3)
    font = _font(40)
    d.text((705, 768), f"TEST {case_no:02d}{n:02d}", fill=(0, 0, 0), font=font)
    if damage:  # a made-up "damage" scribble
        x0, y0 = 300 + (n * 137) % 800, 580 + (n * 53) % 180
        for k in range(9):
            d.line([(x0 + k * 14, y0), (x0 + 60 + k * 9, y0 + 90)], fill=(90, 20, 20), width=7)
    big = _font(64)
    d.rectangle([0, 0, w, 150], fill=(255, 230, 0))
    d.text((40, 20), "SYNTHETIC TEST PHOTO -- not a real vehicle", fill=(0, 0, 0), font=big)
    d.text((40, 95), f"Carlink staging seed, case {case_no}, photo {n}", fill=(0, 0, 0), font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _font(size: int):
    from PIL import ImageFont

    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ------------------------------------------------------------------ data

def draft_data(case_no: int, title: str, parts: list) -> dict:
    from app.reports.schema import SecurityIncidentDraft

    return SecurityIncidentDraft(
        description=f"{MARK} {title}. A made-up case for testing: no real vehicle, person or claim.",
        category=["Vehicle Collision or Damage"],
        location="1 Test Street, Test Town",
        incident_datetime="2026-11-01T09:30:00",
        reporter_name="Staging Tester",
        vehicle_info={"plate_number": f"TEST {case_no:04d}", "make": "Testmake", "model": "Testmodel",
                      "year": "2020", "color": "Grey", "body_type": "Sedan", "transmission": "Automatic",
                      "odometer_reading": f"0{case_no}2345 Km"},
        insurance_details={"insurer_name": "Test Insurer", "claim_type": "Own damage"},
        damage_summary=[{"part": p, "damage_type": t, "severity": s, "remarks": r} for p, t, s, r in parts],
        damaged_parts=[p for p, _, _, _ in parts],
    ).model_dump()


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seed a STAGING stack with made-up data.")
    ap.add_argument("--close-signups-only", action="store_true", help="only switch new signups off")
    ap.add_argument("--reset-passwords", action="store_true", help="new passwords for the three seed accounts")
    ap.add_argument("--images-out", help="also write the placeholder photos to this folder (for upload trials)")
    ap.add_argument("--passwords-file", default=PASSWORDS_FILE,
                    help=f"where new passwords go, 0600 (default {PASSWORDS_FILE}); never printed")
    args = ap.parse_args(argv)
    refuse_unless_staging()

    # Nothing here may reach Gemini: the client is switched off first.
    import app.ai.structured as structured

    def _no_ai(*_a, **_k):
        raise RuntimeError("seed_staging makes no AI calls")

    structured.get_client = _no_ai

    from app.api.signup import SIGNUPS_OPEN_KEY
    from app.reports.db import SessionLocal, init_db
    from app.reports.models import AppSetting

    init_db()
    db = SessionLocal()
    try:
        row = db.get(AppSetting, SIGNUPS_OPEN_KEY)
        if row:
            row.value = "false"
        else:
            db.add(AppSetting(key=SIGNUPS_OPEN_KEY, value="false"))
        db.commit()
    finally:
        db.close()
    print("seed_staging: signups are off")
    if args.close_signups_only:
        return 0

    passwords = ensure_accounts(args.reset_passwords)
    ensure_company_name()
    photos_out = Path(args.images_out) if args.images_out else None
    made = ensure_cases(photos_out)
    print("\nseed_staging: done.")
    if passwords:
        target = Path(args.passwords_file)
        write_passwords(target, passwords)
        print(f"New passwords for {', '.join(u for u, _ in passwords)}: in {target} (0600), not shown here.")
        print(f"  docker exec carlink_staging_api cat {target}   # then: docker exec carlink_staging_api rm {target}")
    else:
        print("Accounts were there already (passwords unchanged; --reset-passwords for new ones).")
    print(f"Cases made now: {', '.join(made) or 'none (all four were there)'}")
    return 0


def ensure_accounts(reset: bool) -> list[tuple[str, str]]:
    from app.auth.core import audit
    from app.auth.passwords import hash_password, password_problem
    from app.reports.db import SessionLocal
    from app.reports.models import User

    out = []
    db = SessionLocal()
    try:
        for username, name, role, (sees_all, ai_key, sign_off) in ACCOUNTS:
            email = f"{username}@example.com"
            user = db.query(User).filter(User.email == email).first()
            if user is not None and not reset:
                continue
            password = secrets.token_urlsafe(15)
            problem = password_problem(password, (name, email))
            if problem:  # never with 20 random characters; but never silently
                sys.exit(f"seed_staging: the generated password was refused: {problem}")
            if user is None:
                user = User(email=email, username=username, name=name, role=role, password_hash=hash_password(password),
                            sees_all_cases=sees_all, ai_company_key_allowed=ai_key, may_sign_off=sign_off,
                            qualifications="Test qualification", license_number="TEST-0000",
                            firm_name="Staging Test Firm")
                db.add(user)
                audit(db, None, "user_created", detail=f"{email} ({role}) by scripts/seed_staging.py")
            else:
                user.password_hash = hash_password(password)
                user.must_change_password = False
                user.failed_logins = 0
                user.locked_until = None
                audit(db, None, "password_reset", detail=f"{email} by scripts/seed_staging.py")
            out.append((username, password))
        db.commit()
    finally:
        db.close()
    return out


def ensure_company_name() -> None:
    from app.reports.db import SessionLocal
    from app.reports.models import AppSetting

    db = SessionLocal()
    try:
        row = db.get(AppSetting, "company_name")
        if row is None:
            db.add(AppSetting(key="company_name", value=COMPANY))
        elif not (row.value or "").strip():
            row.value = COMPANY
        db.commit()
    finally:
        db.close()


def _seeded_titles() -> set[str]:
    from app.reports.db import SessionLocal
    from app.reports.models import Report

    db = SessionLocal()
    try:
        titles = set()
        for (data,) in db.query(Report.data).all():
            text = (data or {}).get("description") or ""
            if text.startswith(MARK):
                titles.add(text[len(MARK):].split(".", 1)[0].strip())
        return titles
    finally:
        db.close()


def _client_for(username: str):
    from fastapi.testclient import TestClient

    import app.api.main as main
    from app.auth.core import SESSION_COOKIE, create_session
    from app.reports.db import SessionLocal
    from app.reports.models import User

    # The app's own routes, in this process: no network, no lifespan (the
    # api container already runs its background jobs).
    client = TestClient(main.app, base_url="https://carlink-staging.seed")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == f"{username}@example.com").first()
        token = create_session(db, user)
        db.commit()
    finally:
        db.close()
    client.cookies.set(SESSION_COOKIE, token)
    return client


def _ok(response, what: str):
    if response.status_code >= 300:
        sys.exit(f"seed_staging: {what} failed: {response.status_code} {response.text[:300]}")
    return response.json() if response.content else {}


def ensure_cases(photos_out: Path | None) -> list[str]:
    from app.reports.db import SessionLocal
    from app.reports.models import IntakePhoto, IntakeSession

    done = _seeded_titles()
    staff = _client_for("staging-staff")
    made = []
    for case_no, (key, title, n_photos, parts) in enumerate(CASES, 1):
        photos = [placeholder_photo(i, case_no, damage=i % 2 == 0) for i in range(1, n_photos + 1)]
        if photos_out is not None:
            photos_out.mkdir(parents=True, exist_ok=True)
            for i, data in enumerate(photos, 1):
                (photos_out / f"seed-case{case_no}-photo{i}.jpg").write_bytes(data)
        if title in done:
            continue
        report_id = _ok(staff.post("/intake"), f"{key}: new intake")["id"]
        for i, data in enumerate(photos, 1):
            _ok(staff.post(f"/intake/{report_id}/photos", files={"file": (f"seed-{case_no}-{i}.jpg", data, "image/jpeg")},
                           data={"position": str(i)}), f"{key}: photo {i}")
        draft = draft_data(case_no, title, parts)
        _ok(staff.put(f"/intake/{report_id}/data", json={"data": draft, "reporter_notes": f"{MARK} made-up notes"}),
            f"{key}: the form")
        if key in ("filed", "signed off"):
            _ok(staff.post(f"/intake/{report_id}/finalize", json={"data": draft}), f"{key}: filing")
            if key == "signed off":
                _ok(staff.post(f"/reports/{report_id}/sign-off",
                               json={"terms_accepted": True, "open_items_ack": True}), f"{key}: sign-off")
        elif key == "ready":
            # As if the AI had read it: the photos sorted and a reading kept
            # (made up here; no AI call). The Review step opens on it.
            db = SessionLocal()
            try:
                rows = db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).order_by(IntakePhoto.position).all()
                areas = ["front", "front-left", "front-right", "front", "front", "rear"]
                for i, photo in enumerate(rows):
                    photo.kind = "damage" if i % 2 == 0 else "overview"
                    photo.area = areas[i % len(areas)]
                    photo.quality = "good"
                    photo.shows_damage = i % 2 == 0
                session = db.get(IntakeSession, report_id)
                session.stage = "ready"
                session.progress_done = session.progress_total = len(rows)
                session.ai_draft = draft
                session.read_photo_ids = [p.id for p in rows]
                session.stage_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
            finally:
                db.close()
        made.append(key)
        print(f"seed_staging: case {case_no} ({key}): {report_id}")
    return made


if __name__ == "__main__":
    sys.exit(main())
