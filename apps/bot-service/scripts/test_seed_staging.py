"""scripts/seed_staging.py: made-up accounts, cases and photos for staging.

    python scripts/test_seed_staging.py

Checked: it refuses without CARLINK_STACK=staging and with production's
PUBLIC_BASE_URL; it switches signups off; three accounts with the right
role and ticks, whose printed passwords sign in; four cases owned by
staging-staff (one filed, one signed off, one draft ready for review with a
made-up reading and sorted photos, one draft waiting for Process); every
photo is a generated placeholder (1600x1200 JPEG with the yellow "SYNTHETIC
TEST PHOTO" band); a second run adds nothing and changes no password;
--reset-passwords gives new ones; --close-signups-only closes signups again;
--images-out writes the placeholders; no AI client is ever made and nothing
leaves the machine.

Offline: temp storage and database, the PDF render a stub, outside
connections refused.
"""
import contextlib
import io
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

_tmp = Path(tempfile.mkdtemp(prefix="seed-staging-test-"))
ENV = {
    "STORAGE_DIR": str(_tmp / "storage"),
    "SECRETS_DIR": str(_tmp / "secrets"),
    "DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "RUN_BACKGROUND_CLEANUP": "false",
    "GEMINI_API_KEY": "",
    "TELEGRAM_BOT_TOKEN": "",
    "MIN_FREE_DISK_GB": "0",
    "PUBLIC_BASE_URL": "https://carlink-staging.example",
}
os.environ.update(ENV)
(_tmp / "storage").mkdir()

PASSED = [0]
FAILURES: list[str] = []


def check(cond, label: str, detail: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append(label)
        print(f"    FAIL {label}" + (f"\n         {detail[-800:]}" if detail else ""))
    return bool(cond)


def refusals() -> None:
    print("\n1. refuses anywhere but staging")
    script = str(ROOT / "scripts" / "seed_staging.py")
    for extra, words in (({}, "CARLINK_STACK is ''"),
                         ({"CARLINK_STACK": "prod"}, "CARLINK_STACK is 'prod'"),
                         ({"CARLINK_STACK": "staging", "PUBLIC_BASE_URL": "https://carlink.34-16-92-98.sslip.io"},
                          "production's address")):
        env = {k: v for k, v in os.environ.items() if k != "CARLINK_STACK"}
        env.update(extra)
        r = subprocess.run([sys.executable, script, "--close-signups-only"], env=env, capture_output=True, text=True,
                           cwd=ROOT, timeout=120)
        check(r.returncode != 0 and words in (r.stdout + r.stderr), f"refused: {extra or 'no CARLINK_STACK'}",
              r.stdout + r.stderr)
    check(not (_tmp / "test.db").exists() or os.path.getsize(_tmp / "test.db") == 0,
          "  ... before touching the database")


_real_connect = socket.socket.connect
NET: list[str] = []


def _offline(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        NET.append(str(address))
        raise ConnectionRefusedError(f"test is offline: {address!r}")
    return _real_connect(sock, address)


def run_seed(*args) -> tuple[int, str]:
    import seed_staging

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = seed_staging.main(list(args))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            print(exc.code)
    return code, out.getvalue()


def seeding() -> None:
    print("\n2. a first seed")
    os.environ["CARLINK_STACK"] = "staging"
    socket.socket.connect = _offline
    import app.api.intake as intake_api
    import app.api.main as main
    import app.ai.client as ai_client
    from app.reports.models import IntakePhoto, IntakeSession, Report, User
    from app.reports.db import SessionLocal

    def fake_render(data, photo_paths, output_path, report_id=None, **_kw):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"%PDF-1.4\n% seed test\n%%EOF\n")

    main.render_pdf = fake_render
    intake_api.render_pdf = fake_render
    clients: list[str] = []
    real_get_client = ai_client.get_client
    ai_client.get_client = lambda *a, **k: clients.append("made") or real_get_client(*a, **k)

    code, out = run_seed()
    check(code == 0 and "seed_staging: done." in out, "the seed runs", out)
    passwords = dict(re.findall(r"^\s+(staging-\w+)\s+(\S+)$", out, re.M))
    check(sorted(passwords) == ["staging-admin", "staging-staff", "staging-user"], "three passwords printed once",
          repr(sorted(passwords)))
    db = SessionLocal()
    try:
        users = {u.username: u for u in db.query(User).all()}
        check(users["staging-admin"].role == "admin" and users["staging-staff"].role == "user"
              and users["staging-staff"].may_sign_off and users["staging-staff"].sees_all_cases
              and not users["staging-user"].may_sign_off and not users["staging-user"].ai_company_key_allowed,
              "admin, a ticked staff user, a plain user")
        staff_id = users["staging-staff"].id
        reports = db.query(Report).all()
        check(len(reports) == 4 and all(r.owner_id == staff_id for r in reports), "four cases, all staging-staff's")
        filed = [r for r in reports if r.status != "intake"]
        drafts = [r for r in reports if r.status == "intake"]
        check(len(filed) == 2 and len(drafts) == 2, "two filed, two drafts", repr([r.status for r in reports]))
        check(any(main._is_signed_off(r) for r in filed) and not all(main._is_signed_off(r) for r in filed),
              "one of the filed cases is signed off")
        check(all(len(r.photo_paths or []) == 4 for r in filed), "the filed cases have their 4 photos")
        sessions = {s.report_id: s for s in db.query(IntakeSession).all()}
        ready = [r for r in drafts if sessions[r.id].stage == "ready"]
        waiting = [r for r in drafts if sessions[r.id].stage == "uploading"]
        check(len(ready) == 1 and len(waiting) == 1, "one draft ready for review, one waiting for Process",
              repr([sessions[r.id].stage for r in drafts]))
        if ready:
            s = sessions[ready[0].id]
            photos = db.query(IntakePhoto).filter(IntakePhoto.report_id == ready[0].id).all()
            check(len(photos) == 6 and all(p.kind for p in photos) and len(s.read_photo_ids or []) == 6
                  and len((s.ai_draft or {}).get("damage_summary") or []) == 3,
                  "the ready draft: 6 sorted photos, a made-up reading of 3 parts")
        check(all((r.data or {}).get("description", "").startswith("[staging seed]") for r in reports),
              "every case is marked as seed data")
        files = [p for p in (_tmp / "storage").rglob("*.jpg")] + [p for p in (_tmp / "storage").rglob("*.jpeg")]
        from PIL import Image
        photo_rows = db.query(IntakePhoto).all()
        originals = [Path(p) for r in filed for p in (r.photo_paths or [])]
        sizes = set()
        banners = 0
        for path in originals:
            full = path if path.is_absolute() else Path(ENV["STORAGE_DIR"]) / path
            if full.exists():
                with Image.open(full) as im:
                    sizes.add(im.size)
                    banners += im.convert("RGB").getpixel((5, 5)) == (255, 230, 0) or \
                        abs(im.convert("RGB").getpixel((5, 5))[0] - 255) < 12
        check(sizes and banners == len(originals), "every photo is a generated placeholder with the yellow band",
              f"{sizes} {banners}/{len(originals)} {files[:2]}")
        del photo_rows
    finally:
        db.close()

    from fastapi.testclient import TestClient
    c = TestClient(main.app, base_url="https://carlink-staging.example")
    check(c.get("/auth/signup").json() == {"open": False}, "signups are off")
    r = c.post("/auth/login", json={"email": "staging-staff", "password": passwords.get("staging-staff", "")})
    check(r.status_code == 200, "the printed password signs staging-staff in", r.text[:200])
    check(clients == [] and NET == [], "no AI client made, nothing left the machine", repr(clients + NET))

    print("\n3. again")
    code, out = run_seed()
    check(code == 0 and "Cases made now: none" in out and "Passwords" not in out,
          "a second run adds nothing and keeps the passwords", out)
    db = SessionLocal()
    try:
        check(db.query(Report).count() == 4, "still four cases")
    finally:
        db.close()
    code, out = run_seed("--reset-passwords")
    new = dict(re.findall(r"^\s+(staging-\w+)\s+(\S+)$", out, re.M))
    check(code == 0 and len(new) == 3 and new.get("staging-staff") != passwords.get("staging-staff"),
          "--reset-passwords: three new ones")
    r = TestClient(main.app, base_url="https://carlink-staging.example").post(
        "/auth/login", json={"email": "staging-staff", "password": passwords.get("staging-staff", "")})
    check(r.status_code != 200, "the old password no longer works")

    from app.reports.models import AppSetting
    db = SessionLocal()
    try:
        db.get(AppSetting, "signups_open").value = "true"
        db.commit()
    finally:
        db.close()
    code, out = run_seed("--close-signups-only")
    check(code == 0 and c.get("/auth/signup").json() == {"open": False} and "Cases made" not in out,
          "--close-signups-only closes them again and does nothing else")
    target = _tmp / "seed-photos"
    code, out = run_seed("--images-out", str(target))
    written = sorted(target.glob("*.jpg"))
    check(code == 0 and len(written) == 20, "--images-out writes the 20 placeholders", str(len(written)))
    socket.socket.connect = _real_connect


def main() -> int:
    for section in (refusals, seeding):
        try:
            section()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            FAILURES.append(f"{section.__name__} crashed: {type(exc).__name__}: {exc}")
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAIL {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
