"""Tests for POST /reports/{id}/upload-photos: adding photos to a case on
the edit form with no AI (app/api/main.upload_report_photos).

    python scripts/test_upload_photos.py

Since P4, /reports/analyze-photos needs AI, and it was the only way a new
photo reached the edit form -- so someone with AI off couldn't add one to
their own case. This route saves them as analyze-photos does (storage/tmp,
named u{user id}-...) and PUT /reports/{id} attaches them, as before.

Checked: the case gate (404 / 403) and the 409s PUT would give, 507 when
the disk is low, the file type and 25MB rules, the u{id}- names, no AI at
all, 60 photos an hour for unvetted accounts (429 rate_limited), and that
the paths it returns attach through PUT /reports/{id}.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, the PDF render replaced by a stub, Gemini by a fake that fails the
test if anything calls it, and every outside connection refused.
"""
import io
import os
import socket
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="upload-photos-test-"))
(_tmp / "storage").mkdir()
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForUploadPhotosTest1"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import google.genai as _genai  # noqa: E402

GEMINI: list[str] = []


class _NoGoogle:
    def __init__(self, *args, **kwargs):
        GEMINI.append("client built")
        raise AssertionError("a Gemini client was built")


_genai.Client = _NoGoogle

import app.ai.structured as structured  # noqa: E402


def _no_gemini(key=None):
    GEMINI.append("get_client")
    raise AssertionError("Gemini was called")


structured.get_client = _no_gemini

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.api.main as main  # noqa: E402
from app.api import limits  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import IntakeSession, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import tmp_dir  # noqa: E402


def _fake_render(data, photo_paths, output_path, report_id=None, **_kw):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


main.render_pdf = _fake_render
app = main.app

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def brief(r) -> str:
    return f"{r.status_code} {r.text[:200]}"


def jpeg(colour=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(buf, "JPEG")
    return buf.getvalue()


def files(*names, data=None) -> list:
    return [("photos", (n, data if data is not None else jpeg((i * 40 % 255, 90, 120)), "image/jpeg"))
            for i, n in enumerate(names)]


def tmp_files(prefix: str = "") -> list[Path]:
    return sorted(p for p in tmp_dir().iterdir() if p.name.startswith(prefix))


def new_user(name: str, *, role: str = "user", **profile) -> str:
    db = SessionLocal()
    try:
        user = User(email=f"{name.lower()}-{gen_id()}@example.com", username=f"{name.lower()}{gen_id()[:6]}",
                    name=name, role=role, password_hash="!", **profile)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def signed_in(user_id: str) -> TestClient:
    c = TestClient(app, base_url=BASE)
    db = SessionLocal()
    try:
        token = create_session(db, db.get(User, user_id))
        db.commit()
    finally:
        db.close()
    c.cookies.set(SESSION_COOKIE, token)
    return c


def new_case(owner_id: str, *, status: str = "confirmed", signed_off: bool = False) -> str:
    db = SessionLocal()
    try:
        data = SecurityIncidentDraft(description="Rear-ended.", category=["Vehicle Collision or Damage"]).model_dump()
        if signed_off:
            data["sign_off"]["status"] = "Signed Off"
        r = Report(channel="manual", reporter_chat_id="dashboard", status="Signed Off" if signed_off else status,
                   owner_id=owner_id, data=data, photo_paths=[])
        db.add(r)
        db.flush()
        if status == "intake":
            db.add(IntakeSession(report_id=r.id))
        db.commit()
        return r.id
    finally:
        db.close()


def photo_paths_of(report_id: str) -> list[str]:
    db = SessionLocal()
    try:
        return list(db.get(Report, report_id).photo_paths or [])
    finally:
        db.close()


def upload(c: TestClient, report_id: str, *names, data=None):
    return c.post(f"/reports/{report_id}/upload-photos", files=files(*names, data=data))


W = SimpleNamespace()


# ================================================================= sections
def setup() -> None:
    W.boot = TestClient(app, base_url=BASE)
    W.boot.__enter__()  # the api's start-up
    W.P = new_user("Pia")  # plain: no ticks, no key -- AI off
    W.Q = new_user("Quin")  # plain, another account
    W.S = new_user("Sam", sees_all_cases=True)
    W.V = new_user("Vic", ai_company_key_allowed=True)
    W.admin = new_user("Ada", role="admin")
    W.p, W.q, W.s, W.v, W.a = (signed_in(u) for u in (W.P, W.Q, W.S, W.V, W.admin))
    W.case = new_case(W.P)
    W.draft = new_case(W.P, status="intake")
    W.signed = new_case(W.P, signed_off=True)
    W.v_case = new_case(W.V)
    limits.photo_uploads.reset()
    db = SessionLocal()
    try:
        pia = db.get(User, W.P)
        check(limits._unvetted(pia) and W.p.get("/auth/me").json().get("ai_allowed") is False, "Pia is unvetted and has AI off")
    finally:
        db.close()


def gate() -> None:
    before = tmp_files()
    r = upload(W.q, W.case, "a.jpg")
    check(r.status_code == 404, "another plain user on Pia's case -> 404", brief(r))
    r = upload(W.p, "nosuchcase000", "a.jpg")
    check(r.status_code == 404, "a made-up case -> 404", brief(r))
    r = upload(W.s, W.draft, "a.jpg")
    check(r.status_code == 403 and r.json().get("code") == "not_allowed",
          "'see all' staff on Pia's unfinished draft (may look, not change) -> 403 not_allowed", brief(r))
    r = upload(W.p, W.draft, "a.jpg")
    check(r.status_code == 409 and "intake" in r.json().get("detail", ""), "her own draft -> 409: photos go in through New Intake", brief(r))
    r = upload(W.p, W.signed, "a.jpg")
    check(r.status_code == 409 and "signed off" in r.json().get("detail", ""), "a signed-off case -> 409 (PUT would refuse it)", brief(r))
    signed_out = TestClient(app, base_url=BASE)
    r = upload(signed_out, W.case, "a.jpg")
    check(r.status_code == 401, "signed out -> 401", brief(r))
    check(tmp_files() == before, "none of these saved a file")
    r = W.p.post(f"/reports/{W.case}/upload-photos")
    check(r.status_code == 400, "no photos -> 400", brief(r))


def disk() -> None:
    before = tmp_files()
    real = limits.disk_space_low
    limits.disk_space_low = lambda: True
    try:
        r = upload(W.p, W.case, "a.jpg")
    finally:
        limits.disk_space_low = real
    check(r.status_code == 507 and r.json().get("code") == "disk_full", "disk nearly full -> 507 disk_full", brief(r))
    check(tmp_files() == before, "...and nothing was saved")


def type_and_size() -> None:
    before = tmp_files()
    r = W.p.post(f"/reports/{W.case}/upload-photos", files=files("ok.jpg") + [("photos", ("notes.pdf", b"%PDF-1.4", "application/pdf"))])
    check(r.status_code == 400 and "Unsupported file format '.pdf'" in r.json().get("detail", ""),
          "a .pdf among them -> 400, analyze-photos' message", brief(r))
    check(tmp_files() == before, "...checked before any file is saved, so the good one isn't left behind either")
    big = b"\xff\xd8" + b"0" * (main.MAX_PHOTO_SIZE_BYTES + 10)
    r = W.p.post(f"/reports/{W.case}/upload-photos", files=files("first.jpg") + [("photos", ("huge.jpg", big, "image/jpeg"))])
    check(r.status_code == 413 and "25MB" in r.json().get("detail", ""), "a photo over 25MB -> 413", brief(r))
    check(tmp_files() == before, "...and the one saved before it is removed: all or nothing")
    check(len(limits.photo_uploads._times.get(W.P, ())) == 0, "...and neither counted towards her hourly limit")


def saved_like_analyze() -> None:
    before = set(tmp_files())
    gemini_before = len(GEMINI)
    r = W.p.post(f"/reports/{W.case}/upload-photos",
                 files=files("IMG_1.JPG", "scan.png") + [("photos", ("noext", jpeg(), "image/jpeg"))])
    body = r.json() if r.status_code == 200 else {}
    paths = body.get("temp_photo_paths") or []
    check(r.status_code == 200 and set(body) == {"temp_photo_paths"} and len(paths) == 3,
          "Pia (AI off) uploads 3 photos -> 200 {temp_photo_paths: [3 paths]}", brief(r))
    check(len(GEMINI) == gemini_before, "...with no AI at all")
    names = [Path(p).name for p in paths]
    check(all(Path(p).parent.resolve() == tmp_dir().resolve() for p in paths), "they are in storage/tmp")
    check(all(n.startswith(f"u{W.P}-") for n in names), "named u{her id}-..., like analyze-photos' uploads", repr(names))
    check([Path(n).suffix for n in names] == [".jpg", ".png", ".jpg"], "extensions kept (lower case), none -> .jpg", repr(names))
    check(set(tmp_files()) - before == {Path(p) for p in paths}, "exactly those files were written")
    W.pia_paths = paths

    print("  attached by PUT /reports/{id}, as before")
    r = W.p.put(f"/reports/{W.case}", json={"draft": {"description": "Rear-ended, photos added."}, "temp_photo_paths": paths})
    check(r.status_code == 200, "PUT /reports/{id} with them -> 200", brief(r))
    attached = photo_paths_of(W.case)
    check(len(attached) == 3 and all(Path(p).exists() for p in attached), "the case now has the 3 photos", repr(attached))
    check(not any(Path(p).exists() for p in paths), "...and the temp files are gone")
    r = W.p.post(f"/reports/{W.case}/upload-photos", files=files("more.jpg"))
    more = r.json().get("temp_photo_paths") or []
    r = W.p.put(f"/reports/{W.case}", json={"draft": {"description": "One more."}, "temp_photo_paths": more})
    check(r.status_code == 200 and len(photo_paths_of(W.case)) == 4, "a second batch is added after them, not over them",
          repr(photo_paths_of(W.case)))

    print("  only the uploader's own")
    r = W.v.post(f"/reports/{W.v_case}/upload-photos", files=files("vic.jpg"))
    vic = r.json().get("temp_photo_paths") or []
    check(r.status_code == 200 and Path(vic[0]).name.startswith(f"u{W.V}-"), "Vic uploads to his case", brief(r))
    r = W.p.put(f"/reports/{W.case}", json={"draft": {"description": "Steal."}, "temp_photo_paths": vic})
    check(r.status_code == 400 and Path(vic[0]).exists() and len(photo_paths_of(W.case)) == 4,
          "Pia can't attach Vic's upload to her case (400), and it stays his", brief(r))


def hourly_cap() -> None:
    limits.photo_uploads.reset()
    batch = [f"p{i:02d}.jpg" for i in range(20)]
    codes = [upload(W.p, W.case, *batch).status_code for _ in range(3)]
    check(codes == [200, 200, 200], "Pia uploads 60 photos in an hour (3 x 20)", repr(codes))
    before = tmp_files()
    r = upload(W.p, W.case, "61.jpg")
    check(r.status_code == 429 and r.json().get("code") == "rate_limited" and r.json().get("detail") == limits.PHOTO_LIMIT,
          "the 61st -> 429 rate_limited, plain detail", brief(r))
    check(tmp_files() == before, "...saved nothing")
    limits.photo_uploads.reset()
    limits.photo_uploads.take(W.P, 55)
    r = upload(W.p, W.case, *[f"x{i}.jpg" for i in range(10)])
    check(r.status_code == 429 and tmp_files() == before, "55 used + 10 more -> 429, none of the 10 saved", brief(r))
    check(len(limits.photo_uploads._times.get(W.P, ())) == 55, "...and none counted")
    r = upload(W.p, W.case, *[f"y{i}.jpg" for i in range(5)])
    check(r.status_code == 200, "...while 5 more fit", brief(r))

    print("  staff and ticked accounts aren't counted")
    for who, c, case in (("an admin", W.a, W.case), ("a ticked user (company AI key)", W.v, W.v_case),
                         ("'see all' staff", W.s, W.case)):
        r = upload(c, case, *batch, *[f"z{i}.jpg" for i in range(45)])
        check(r.status_code == 200, f"{who}: 65 photos at once -> 200", brief(r))
    check(set(limits.photo_uploads._times) == {W.P}, "only Pia's photos were counted", repr(set(limits.photo_uploads._times)))
    limits.photo_uploads.reset()


def ai_unchanged() -> None:
    r = W.p.post("/reports/analyze-photos", data={"description": "x"}, files=files("a.jpg"))
    check(r.status_code == 409 and r.json().get("code") == "ai_key_missing", "analyze-photos still needs AI (409 ai_key_missing)",
          brief(r))
    check(not GEMINI, "no Gemini call or client anywhere in this test", repr(GEMINI))


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("the case gate", gate),
        ("disk", disk),
        ("file type and size", type_and_size),
        ("saved like analyze-photos, attached by PUT", saved_like_analyze),
        ("60 photos an hour for unvetted accounts", hourly_cap),
        ("AI routes unchanged", ai_unchanged),
    ):
        run_section(name, fn)
    print(f"\ntest_upload_photos: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL UPLOAD-PHOTOS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
