"""Stored photo originals re-saved smaller (owner, 23 Nov 2026:
app/config.py photo_store_compress, storage/files.compress_original) --
offline, no AI call, only synthetic photos made here with Pillow.

  0. the setting: "off" by default, takes off / on, refuses a typo; passed
     through docker-compose.prod.yml once, in the shared block, at "off";
  1. off: compress_original does nothing (None, the file byte for byte);
     a website upload, a filed case's photo, a public Telegram report and a
     staff Telegram draft each store exactly the bytes sent;
  2. on, a large noisy camera-like JPEG at quality 98 with EXIF rotation
     (Orientation 6) and a GPS position: smaller, opens, no EXIF or GPS
     left, the rotation baked into the pixels (width and height swapped,
     the picture the right way round), its colour profile kept, the
     thumbnail and preview made from it the right way round;
  3. on, left alone (None, byte for byte, no temp file left): a .png, a PNG
     under a .jpg name, a corrupt .jpg, a cut-off JPEG, a small quality-60
     JPEG with no EXIF; a small JPEG WITH GPS is re-saved all the same;
  4. on, the website upload (api/intake.upload_intake_photo): no GPS
     stored, size_bytes is the stored size, sha256 the uploaded bytes', and
     the same bytes again answer duplicate_of;
  5. on, the other ways in: save_photo (filed cases: POST /reports, case
     edits, WhatsApp, the staff bot's filing), public Telegram reports
     (intake/public._store_photos) and staff Telegram full detection
     (intake/telegram_bridge.store_photos);
  6. when each intake photo was taken (owner decision D7: the date, no
     location; storage/files.photo_taken_at, IntakePhoto.taken_at_local /
     taken_at): the columns added to an old database; DateTimeOriginal,
     else DateTimeDigitized, else DateTime, UTC only with the camera's own
     offset; garbage, unset, pre-2000 and future dates, no EXIF, PNG,
     corrupt: None; HEIC when this Pillow can write one; the website upload
     with the switch on (date kept on the row, no EXIF in the file) and off;
     a staff Telegram draft's rows.

    python scripts/test_photo_compress.py
"""
import contextlib
import hashlib
import io
import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # apps/bot-service
REPO = ROOT.parent.parent
sys.path.insert(0, str(ROOT))
_tmp = Path(tempfile.mkdtemp(prefix="photo-compress-test-"))
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["MIN_FREE_DISK_GB"] = "0"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
# No AI anywhere here: no key at all.
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("PHOTO_STORE_COMPRESS", None)

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageChops, ImageOps, ImageStat  # noqa: E402

from app import config  # noqa: E402
from app.api.main import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import public, telegram_bridge  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import IntakePhoto, User, gen_id  # noqa: E402
from app.storage import files  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

ORIENTATION = 0x0112
GPS_IFD = 0x8825
EXIF_IFD = 0x8769
DATE_ORIGINAL, DATE_DIGITIZED, DATE_TIME = 0x9003, 0x9004, 0x0132
OFFSET_ORIGINAL, OFFSET_TIME = 0x9011, 0x9010
SRC = _tmp / "sources"
SRC.mkdir(parents=True, exist_ok=True)

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {str(why)[:1500]}" if why != "" else ""))
    return bool(cond)


def need(cond, label: str, why="") -> None:
    """A check the rest of the section stands on."""
    if not check(cond, label, why):
        raise AssertionError(label)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


@contextlib.contextmanager
def compress(value: str):
    old = settings.photo_store_compress
    settings.photo_store_compress = value
    try:
        yield
    finally:
        settings.photo_store_compress = old


# ------------------------------------------------------------- synthetic photos


def _srgb_profile() -> bytes | None:
    try:
        from PIL import ImageCms

        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:  # Pillow built without littlecms: the ICC check is skipped
        return None


SRGB = _srgb_profile()


def camera_jpeg(name: str, *, size=(1500, 1000), quality=98, orientation=6, gps=True, icc=True,
                taken: dict | None = None, ifd0: dict | None = None) -> Path:
    """A camera-like JPEG: a left-to-right gradient (so a wrong turn shows)
    over noise (so it compresses like a photo, not a flat colour), saved at
    `quality`, with EXIF Orientation and a made-up GPS position. taken: tags
    of the Exif sub-IFD (DateTimeOriginal...); ifd0: more main-IFD tags."""
    w, h = size
    grad = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))  # dark left, light right
    noise = Image.effect_noise((w, h), 40)
    top_bar = Image.new("L", (w, h), 0)
    top_bar.paste(255, (0, 0, w, h // 6))  # a bright band along the top edge
    image = Image.merge("RGB", (grad, noise, top_bar))
    exif = Image.Exif()
    exif[0x010F] = "SyntheticCam"  # Make
    if orientation:
        exif[ORIENTATION] = orientation
    if gps:
        exif[GPS_IFD] = {1: "N", 2: (3.0, 8.0, 30.0), 3: "E", 4: (101.0, 41.0, 10.0)}
    if taken:
        exif[EXIF_IFD] = dict(taken)
    for tag, value in (ifd0 or {}).items():
        exif[tag] = value
    options = {"quality": quality, "exif": exif.tobytes()}
    if icc and SRGB:
        options["icc_profile"] = SRGB
    path = SRC / name
    image.save(path, "JPEG", **options)
    return path


def plain_jpeg(name: str, *, size=(640, 480), quality=60) -> Path:
    w, h = size
    image = Image.merge("RGB", (Image.linear_gradient("L").resize((w, h)), Image.effect_noise((w, h), 12),
                                Image.new("L", (w, h), 90)))
    path = SRC / name
    image.save(path, "JPEG", quality=quality)
    return path


def gps_of(path) -> dict:
    with Image.open(path) as im:
        return dict(im.getexif().get_ifd(GPS_IFD))


def exif_bytes(path) -> bytes | None:
    with Image.open(path) as im:
        return im.info.get("exif")


def no_metadata(path) -> bool:
    data = Path(path).read_bytes()
    with Image.open(path) as im:
        return (not im.info.get("exif") and len(im.getexif()) == 0 and not gps_of(path)
                and b"Exif\x00\x00" not in data and not im.info.get("xmp"))


def opens(path) -> bool:
    try:
        with Image.open(path) as im:
            im.load()
        return True
    except Exception:
        return False


def temp_files(folder) -> list[str]:
    return sorted(p.name for p in Path(folder).iterdir() if p.suffix == ".tmp")


def upright_diff(stored, original) -> float:
    """Mean difference between the stored picture and the original turned
    upright by its EXIF, both shrunk (the noise averages out): small when
    the rotation went the right way, large when not."""
    with Image.open(original) as o, Image.open(stored) as s:
        want = ImageOps.exif_transpose(o).convert("RGB")
        got = s.convert("RGB")
        if want.size != got.size:
            return 255.0
        small = (max(1, want.width // 10), max(1, want.height // 10))
        diff = ImageChops.difference(want.resize(small), got.resize(small))
        return sum(ImageStat.Stat(diff).mean) / 3


def sha(path_or_bytes) -> str:
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    return hashlib.sha256(data).hexdigest()


def stored_row(photo_id: str) -> IntakePhoto:
    db = SessionLocal()
    try:
        return db.get(IntakePhoto, photo_id)
    finally:
        db.close()


# ------------------------------------------------------------- 0. the setting


def settings_section() -> None:
    fresh = config.Settings()
    check(fresh.photo_store_compress == "off" and settings.photo_store_compress == "off",
          "photo_store_compress defaults to off, and this process runs at it")
    bad = []
    for given, want in (("on", "on"), ("ON", "on"), (" off ", "off"), ("", "off")):
        got = config.Settings(photo_store_compress=given).photo_store_compress
        if got != want:
            bad.append((given, got))
    os.environ["PHOTO_STORE_COMPRESS"] = "on"
    try:
        if config.Settings().photo_store_compress != "on":
            bad.append(("PHOTO_STORE_COMPRESS=on in the environment", "not read"))
    finally:
        os.environ.pop("PHOTO_STORE_COMPRESS", None)
    for typo in ("yes", "true", "1", "maybe"):
        try:
            config.Settings(photo_store_compress=typo)
            bad.append((typo, "accepted"))
        except Exception:
            pass
    check(not bad, "takes off / on (any case, blank is off); a typo stops the app", bad)
    compose = (REPO / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    anchor, services = compose.index("x-bot-service: &bot-service"), compose.index("\nservices:")
    want = "    - PHOTO_STORE_COMPRESS=${PHOTO_STORE_COMPRESS:-off}"
    lines = [ln for ln in compose.splitlines() if "PHOTO_STORE_COMPRESS" in ln and not ln.strip().startswith("#")]
    check(lines == [want] and anchor < compose.find(want) < services,
          "docker-compose.prod.yml: once, in the shared block (api and telegram-bot), at config's default", lines)
    check(files.PHOTO_STORE_QUALITY == 85, "the stored quality is 85")


# ------------------------------------------------------------- 1. off: as before


def off_section() -> None:
    src = camera_jpeg("off.jpg")
    before = src.read_bytes()
    with compress("off"):
        check(files.compress_original(str(src)) is None and src.read_bytes() == before,
              "compress_original: None, the file byte for byte")

        with TestClient(app) as client:
            sign_in(client)
            rid = client.post("/intake").json()["id"]
            r = client.post(f"/intake/{rid}/photos", files={"file": ("off.jpg", before, "image/jpeg")})
            need(r.status_code == 200, "website upload accepted", r.text)
            row = stored_row(r.json()["photo"]["id"])
            check(Path(row.path).read_bytes() == before and row.size_bytes == len(before) and row.sha256 == sha(before),
                  "website upload: exactly the bytes sent are stored (size and sha256 the upload's)")
            check(gps_of(row.path) != {}, "the GPS position is still in it (as before)")

        stored = files.save_photo(gen_id(), str(src), 1)
        check(Path(stored).read_bytes() == before, "save_photo: the bytes sent, as before")

        kept = public._store_photos(gen_id(), [str(src)])
        need(len(kept) == 1, "public report: the photo is kept")
        check(Path(kept[0]["path"]).read_bytes() == before and kept[0]["size"] == len(before),
              "public report: the bytes sent, as before")

        kept, skipped = telegram_bridge.store_photos(gen_id(), [str(src)])
        need(len(kept) == 1 and skipped == 0, "staff Telegram draft: the photo is kept")
        check(Path(kept[0]["path"]).read_bytes() == before and kept[0]["size"] == len(before),
              "staff Telegram draft: the bytes sent, as before")


# ------------------------------------------------------------- 2. on: a camera photo


def camera_section() -> None:
    original = camera_jpeg("camera.jpg")
    need(gps_of(original) and exif_bytes(original), "the synthetic photo carries EXIF and a GPS position")
    with Image.open(original) as im:
        need(im.size == (1500, 1000) and im.getexif().get(ORIENTATION) == 6, "1500x1000, EXIF Orientation 6")
    folder = _tmp / "camera"
    folder.mkdir()
    work = folder / "photo.jpg"
    work.write_bytes(original.read_bytes())
    before = work.stat().st_size

    with compress("on"):
        got = files.compress_original(str(work))
    after = work.stat().st_size
    check(got == after and after < before, f"smaller: {before} -> {after} bytes, the new size returned", got)
    check(after < before * 0.7, f"much smaller at q85 ({after / before:.0%} of the q98 original)")
    check(opens(work), "opens and decodes whole")
    with Image.open(work) as im:
        check(im.format == "JPEG" and im.size == (1000, 1500),
              "a JPEG, width and height swapped: the rotation is in the pixels", (im.format, im.size))
        if SRGB:
            check(im.info.get("icc_profile") == SRGB, "its colour profile (ICC) is kept")
    check(no_metadata(work), "no EXIF, no GPS, no XMP left")
    check(upright_diff(work, original) < 6, "the picture is the right way round (as the EXIF said to turn it)",
          upright_diff(work, original))
    check(temp_files(folder) == [], "no temp file left", temp_files(folder))

    thumb, preview = files.generate_thumbnail(str(work)), files.generate_preview(str(work))
    check(thumb and preview and Path(thumb).exists() and Path(preview).exists(), "thumbnail and preview made")
    with Image.open(thumb) as t, Image.open(preview) as p:
        check(t.size == (267, 400) and p.size == (1000, 1500),
              "both upright (portrait, 267x400 and 1000x1500), not turned twice", (t.size, p.size))

    with compress("on"):
        again = work.read_bytes()
        second = files.compress_original(str(work))
    check(opens(work) and no_metadata(work), "a second call is harmless (the photo still opens, no metadata)",
          (second, len(again)))


# ------------------------------------------------------------- 3. on: left alone


def left_alone_section() -> None:
    folder = _tmp / "alone"
    folder.mkdir()
    real_png = folder / "photo.png"
    Image.effect_noise((300, 200), 30).convert("RGB").save(real_png, "PNG")
    png_as_jpg = folder / "png-inside.jpg"
    png_as_jpg.write_bytes(real_png.read_bytes())
    corrupt = folder / "corrupt.jpg"
    corrupt.write_bytes(b"\xff\xd8\xff\xe0 not really a photo " * 200)
    cut_off = folder / "cut-off.jpg"
    whole = camera_jpeg("whole.jpg", size=(800, 600)).read_bytes()
    cut_off.write_bytes(whole[: len(whole) // 2])
    small = folder / "small-q60.jpg"
    small.write_bytes(plain_jpeg("small.jpg").read_bytes())
    with Image.open(small) as im:
        need(not im.info.get("exif"), "the small q60 JPEG has no EXIF")
    webp_as_jpeg = folder / "webp-inside.jpeg"
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), (10, 120, 200)).save(buf, "WEBP")
    webp_as_jpeg.write_bytes(buf.getvalue())

    cases = (
        (real_png, "a .png"),
        (png_as_jpg, "a PNG under a .jpg name"),
        (webp_as_jpeg, "a WebP under a .jpeg name"),
        (corrupt, "a corrupt .jpg"),
        (cut_off, "a cut-off JPEG (half its bytes)"),
        (small, "a small quality-60 JPEG with no EXIF (re-saving would not make it smaller)"),
    )
    with compress("on"):
        for path, what in cases:
            before = path.read_bytes()
            got = files.compress_original(str(path))
            check(got is None and path.read_bytes() == before, f"{what}: None, byte for byte", got)
        check(files.compress_original(str(folder / "missing.jpg")) is None, "a missing file: None (no exception)")
    check(temp_files(folder) == [], "no temp file left in the folder", temp_files(folder))

    # Re-saving makes this one larger, but it has a GPS position to drop.
    small_gps = folder / "small-gps.jpg"
    small_gps.write_bytes(camera_jpeg("small-gps.jpg", size=(320, 240), quality=60, orientation=0,
                                      icc=False).read_bytes())
    with compress("on"):
        got = files.compress_original(str(small_gps))
    check(got == small_gps.stat().st_size and no_metadata(small_gps) and opens(small_gps),
          "a small JPEG WITH a GPS position is re-saved all the same: the EXIF is dropped", got)
    check(temp_files(folder) == [], "still no temp file left", temp_files(folder))


# ------------------------------------------------------------- 4. on: the website upload


def upload_section() -> None:
    sent = camera_jpeg("upload.jpg").read_bytes()
    with compress("on"), TestClient(app) as client:
        sign_in(client)
        rid = client.post("/intake").json()["id"]
        r = client.post(f"/intake/{rid}/photos", files={"file": ("IMG_0001.jpg", sent, "image/jpeg")},
                        data={"position": "0"})
        need(r.status_code == 200 and "photo" in r.json(), "upload accepted", r.text)
        answer = r.json()["photo"]
        row = stored_row(answer["id"])
        stored = Path(row.path)
        check(no_metadata(stored) and opens(stored), "the stored original has no EXIF or GPS, and opens")
        check(row.size_bytes == stored.stat().st_size == answer["size_bytes"] and row.size_bytes < len(sent),
              f"size_bytes is the stored size ({row.size_bytes}), smaller than the {len(sent)} sent",
              (row.size_bytes, stored.stat().st_size, answer["size_bytes"]))
        check(row.sha256 == sha(sent), "sha256 is the uploaded bytes' hash")
        check(files.thumbnail_path_for(row.path).exists() and files.preview_path_for(row.path).exists(),
              "thumbnail and preview made")
        with Image.open(files.preview_path_for(row.path)) as p:
            check(p.height > p.width, "the preview is upright", p.size)
        again = client.post(f"/intake/{rid}/photos", files={"file": ("IMG_0001.jpg", sent, "image/jpeg")})
        check(again.status_code == 200 and again.json() == {"duplicate_of": answer["id"]},
              "the same bytes uploaded again: duplicate_of the first", again.text)
        check(temp_files(stored.parent) == [] and len(list(stored.parent.glob("*.jpg"))) == 2,
              "the draft folder holds the one original and its thumbnail, no temp file",
              sorted(p.name for p in stored.parent.iterdir()))
        png = io.BytesIO()
        Image.effect_noise((300, 200), 30).convert("RGB").save(png, "PNG")
        r = client.post(f"/intake/{rid}/photos", files={"file": ("shot.png", png.getvalue(), "image/png")})
        need(r.status_code == 200 and "photo" in r.json(), "a PNG upload accepted", r.text)
        row = stored_row(r.json()["photo"]["id"])
        check(Path(row.path).read_bytes() == png.getvalue() and row.size_bytes == len(png.getvalue()),
              "a PNG is stored as sent")


# ------------------------------------------------------------- 5. on: the other ways in


def other_entries_section() -> None:
    src = camera_jpeg("entry.jpg")
    sent = src.read_bytes()
    with compress("on"):
        stored = Path(files.save_photo(gen_id(), str(src), 1))
        check(src.read_bytes() == sent, "save_photo: the source (the temp upload) is left as it was")
        check(stored.stat().st_size < len(sent) and no_metadata(stored) and opens(stored),
              "save_photo (filed cases, WhatsApp, the staff bot's filing): stored smaller, no EXIF or GPS")
        check(files.thumbnail_path_for(str(stored)).exists() and files.preview_path_for(str(stored)).exists()
              and temp_files(stored.parent) == [], "save_photo: thumbnail and preview made, no temp file")

        kept = public._store_photos(gen_id(), [str(src)])
        need(len(kept) == 1, "public report: the photo is kept")
        path = Path(kept[0]["path"])
        check(no_metadata(path) and opens(path) and kept[0]["size"] == path.stat().st_size < len(sent),
              "public Telegram report: stored smaller, no EXIF or GPS, size is the stored size")
        check(kept[0]["sha256"] == sha(sent) and src.read_bytes() == sent,
              "public report: sha256 the sent bytes', the bot's temp file left as it was")
        check(files.thumbnail_path_for(str(path)).exists() and files.preview_path_for(str(path)).exists()
              and temp_files(path.parent) == [], "public report: thumbnail and preview made, no temp file")
        twice = public._store_photos(gen_id(), [str(src), str(src)])
        check(len(twice) == 1, "public report: the same photo twice is still kept once")

        kept, skipped = telegram_bridge.store_photos(gen_id(), [str(src), str(src)])
        need(len(kept) == 1 and skipped == 0, "staff Telegram draft: the photo kept once, the repeat not lost")
        path = Path(kept[0]["path"])
        check(no_metadata(path) and opens(path) and kept[0]["size"] == path.stat().st_size < len(sent),
              "staff Telegram full detection: stored smaller, no EXIF or GPS, size is the stored size")
        check(kept[0]["sha256"] == sha(sent) and files.preview_path_for(str(path)).exists()
              and temp_files(path.parent) == [],
              "staff Telegram draft: sha256 the sent bytes', preview made, no temp file")


# ------------------------------------------------------------- 7. the AI sees the same images


def ai_same_section() -> None:
    """Owner's accuracy gate (24 Nov 2026): with the switch on, the 4 real
    cases scored 46/78 against a 47 floor set before the run. The AI reads
    the 1600px preview (intake/photos.ai_input_path), so the preview and the
    thumbnail are now made from the file AS SENT, before it is compressed:
    switch on or off, they must be byte for byte the same, at every way in."""
    src = camera_jpeg("same.jpg", size=(2400, 1600))
    sent = src.read_bytes()

    def pair(store):
        out = {}
        for mode in ("off", "on"):
            with compress(mode):
                path = Path(store())
                out[mode] = (sha(files.preview_path_for(str(path)).read_bytes()),
                             sha(files.thumbnail_path_for(str(path)).read_bytes()),
                             path.stat().st_size)
        return out

    with TestClient(app) as client:
        sign_in(client)

        def website():
            rid = client.post("/intake").json()["id"]
            r = client.post(f"/intake/{rid}/photos", files={"file": ("IMG_7.jpg", sent, "image/jpeg")},
                            data={"position": "0"})
            need(r.status_code == 200 and "photo" in r.json(), "upload accepted", r.text)
            return stored_row(r.json()["photo"]["id"]).path

        ways = {
            "website upload": website,
            "save_photo (filed cases, WhatsApp, staff bot filing)": lambda: files.save_photo(gen_id(), str(src), 1),
            "public Telegram report": lambda: public._store_photos(gen_id(), [str(src)])[0]["path"],
            "staff Telegram full detection": lambda: telegram_bridge.store_photos(gen_id(), [str(src)])[0][0]["path"],
        }
        for name, store in ways.items():
            got = pair(store)
            check(got["off"][0] == got["on"][0],
                  f"{name}: the AI's preview is byte for byte the same with the switch on and off")
            check(got["off"][1] == got["on"][1], f"{name}: the thumbnail is the same too")
            check(got["on"][2] < got["off"][2],
                  f"{name}: and the stored original is still smaller with it on ({got['on'][2]} < {got['off'][2]})")


# ------------------------------------------------------------- 6. when it was taken (D7)

LOCAL = datetime(2026, 11, 20, 9, 15, 30)
LOCAL_TEXT = "2026:11:20 09:15:30"


def taken_section() -> None:
    from sqlalchemy import inspect, text

    from app.reports import db as db_module

    # The columns: listed for the start-up upgrade, and added to an old table.
    wanted = [("intake_photos", "taken_at_local", "DATETIME"), ("intake_photos", "taken_at", "DATETIME")]
    check(all(w in db_module._ADDED_COLUMNS for w in wanted)
          and hasattr(IntakePhoto, "taken_at_local") and hasattr(IntakePhoto, "taken_at"),
          "IntakePhoto.taken_at_local and .taken_at, both in db._ADDED_COLUMNS")
    db_module.init_db()
    with db_module.engine.begin() as conn:
        conn.execute(text("ALTER TABLE intake_photos DROP COLUMN taken_at_local"))
        conn.execute(text("ALTER TABLE intake_photos DROP COLUMN taken_at"))
    columns = {c["name"] for c in inspect(db_module.engine).get_columns("intake_photos")}
    gone = not ({"taken_at_local", "taken_at"} & columns)
    db_module.init_db()
    columns = {c["name"] for c in inspect(db_module.engine).get_columns("intake_photos")}
    check(gone and {"taken_at_local", "taken_at"} <= columns,
          "a database without them: init_db adds both (old rows stay valid, NULL)")

    # photo_taken_at on its own.
    def read(name, **kw):
        return files.photo_taken_at(camera_jpeg(name, size=(160, 120), **kw))

    got = read("t-original.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT})
    check(got == (LOCAL, None), "DateTimeOriginal, no offset: the camera's clock, no UTC (no zone guessed)", got)
    got = read("t-offset.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT, OFFSET_ORIGINAL: "+08:00"})
    check(got == (LOCAL, LOCAL - timedelta(hours=8)), "with OffsetTimeOriginal +08:00: UTC is 8 hours earlier", got)
    got = read("t-west.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT, OFFSET_ORIGINAL: "-05:30"})
    check(got == (LOCAL, LOCAL + timedelta(hours=5, minutes=30)), "a -05:30 offset: UTC later", got)
    got = read("t-digitized.jpg", taken={DATE_DIGITIZED: LOCAL_TEXT})
    check(got == (LOCAL, None), "only DateTimeDigitized: used", got)
    got = read("t-datetime.jpg", ifd0={DATE_TIME: LOCAL_TEXT}, taken={OFFSET_TIME: "+08:00"})
    check(got == (LOCAL, LOCAL - timedelta(hours=8)), "only DateTime, with its OffsetTime: used", got)
    got = read("t-both.jpg", ifd0={DATE_TIME: "2026:11:22 18:00:00"},
               taken={DATE_ORIGINAL: LOCAL_TEXT, OFFSET_ORIGINAL: "+08:00", OFFSET_TIME: "+00:00"})
    check(got == (LOCAL, LOCAL - timedelta(hours=8)),
          "DateTimeOriginal wins over DateTime (an editor's save time), with its own offset", got)
    got = read("t-bad-offset.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT, OFFSET_ORIGINAL: "+8"})
    check(got == (LOCAL, None), "an offset that isn't one: the local time, no UTC", got)
    got = read("t-far-offset.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT, OFFSET_ORIGINAL: "+15:00"})
    check(got == (LOCAL, None), "an offset no place on Earth has: no UTC", got)
    future = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y:%m:%d %H:%M:%S")
    for name, tags, what in (
        ("t-garbage.jpg", {DATE_ORIGINAL: "not a date at all"}, "a garbage date"),
        ("t-zero.jpg", {DATE_ORIGINAL: "0000:00:00 00:00:00"}, "an unset date (all zeros)"),
        ("t-month13.jpg", {DATE_ORIGINAL: "2026:13:40 25:61:61"}, "a date that doesn't exist"),
        ("t-1999.jpg", {DATE_ORIGINAL: "1999:12:31 23:59:59"}, "a date before 2000 (a clock never set)"),
        ("t-future.jpg", {DATE_ORIGINAL: future}, "a date 3 days ahead"),
    ):
        got = read(name, taken=tags)
        check(got is None, f"{what}: None", got)
    got = read("t-bad-first.jpg", taken={DATE_ORIGINAL: "1980:01:01 00:00:00"}, ifd0={DATE_TIME: LOCAL_TEXT})
    check(got is None, "a wrong DateTimeOriginal is not replaced by DateTime (the first date decides)", got)
    check(files.photo_taken_at(plain_jpeg("t-none.jpg")) is None, "no EXIF: None")
    png = SRC / "t.png"
    Image.new("RGB", (40, 30), "red").save(png, "PNG")
    corrupt = SRC / "t-corrupt.jpg"
    corrupt.write_bytes(b"\xff\xd8\xff\xe0 nothing here " * 50)
    check(files.photo_taken_at(png) is None and files.photo_taken_at(corrupt) is None
          and files.photo_taken_at(SRC / "missing.jpg") is None,
          "a PNG, a corrupt file, a missing file: None, no exception")
    heic = SRC / "t.heic"
    try:
        exif = Image.Exif()
        exif[EXIF_IFD] = {DATE_ORIGINAL: LOCAL_TEXT, OFFSET_ORIGINAL: "+08:00"}
        Image.new("RGB", (64, 48), (200, 10, 10)).save(heic, "HEIF", exif=exif.tobytes())
    except Exception as exc:
        print(f"    skip HEIC: this Pillow can't write one here ({type(exc).__name__})")
    else:
        got = files.photo_taken_at(heic)
        check(got == (LOCAL, LOCAL - timedelta(hours=8)), "an iPhone HEIC: read the same way (pillow-heif)", got)

    # The website upload, the switch on and off.
    def upload(client, rid, path):
        r = client.post(f"/intake/{rid}/photos", files={"file": (path.name, path.read_bytes(), "image/jpeg")})
        need(r.status_code == 200 and "photo" in r.json(), f"{path.name} uploaded", r.text)
        return stored_row(r.json()["photo"]["id"])

    with TestClient(app) as client:
        sign_in(client)
        rid = client.post("/intake").json()["id"]
        with compress("on"):
            row = upload(client, rid, camera_jpeg("u-taken.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT}))
            check(row.taken_at_local == LOCAL and row.taken_at is None,
                  "switch on, DateTimeOriginal + GPS: the row has the camera's time (no offset: no UTC)",
                  (row.taken_at_local, row.taken_at))
            check(no_metadata(row.path), "... and the stored file has no EXIF, no GPS, no date")
            row = upload(client, rid, camera_jpeg("u-offset.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT,
                                                                         OFFSET_ORIGINAL: "+08:00"}))
            check(row.taken_at_local == LOCAL and row.taken_at == LOCAL - timedelta(hours=8) and no_metadata(row.path),
                  "with OffsetTimeOriginal: UTC too; the stored file has no EXIF", (row.taken_at_local, row.taken_at))
            row = upload(client, rid, plain_jpeg("u-none.jpg"))
            check(row.taken_at_local is None and row.taken_at is None, "no EXIF: both None")
            row = upload(client, rid, camera_jpeg("u-garbage.jpg", taken={DATE_ORIGINAL: "yesterday-ish"}))
            check(row.taken_at_local is None and row.taken_at is None, "a garbage date: both None")
        with compress("off"):
            row = upload(client, rid, camera_jpeg("u-off.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT,
                                                                      OFFSET_ORIGINAL: "+08:00"}))
            check(row.taken_at_local == LOCAL and row.taken_at == LOCAL - timedelta(hours=8),
                  "switch off: recorded all the same", (row.taken_at_local, row.taken_at))
            check(gps_of(row.path) != {}, "... and the file is stored as sent (EXIF kept, as before)")
        state = client.get(f"/intake/{rid}")
        check(state.status_code == 200 and len(state.json().get("photos") or []) == 5,
              "the draft still loads with its 5 photos", state.status_code)

    # Staff Telegram full detection: the same, onto the draft's rows.
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.role == "admin").first()
        admin_id = admin.id
    finally:
        db.close()
    with compress("on"):
        src = camera_jpeg("tg-taken.jpg", taken={DATE_ORIGINAL: LOCAL_TEXT, OFFSET_ORIGINAL: "+08:00"})
        sent = [str(src), str(plain_jpeg("tg-none.jpg"))]
        created = telegram_bridge.create_draft(external_id="1001", photo_paths=sent, facts={}, notes="",
                                               who=lambda s: (s.get(User, admin_id), None))
    need(created.report_id and created.photos == 2, "staff Telegram draft made", created)
    db = SessionLocal()
    try:
        rows = (db.query(IntakePhoto).filter(IntakePhoto.report_id == created.report_id)
                .order_by(IntakePhoto.position).all())
        taken = [(r.taken_at_local, r.taken_at) for r in rows]
        paths = [r.path for r in rows]
    finally:
        db.close()
    check(taken == [(LOCAL, LOCAL - timedelta(hours=8)), (None, None)],
          "staff Telegram draft: the photo sent as a file keeps its date on the row; one with no EXIF has none", taken)
    check(all(no_metadata(p) for p in paths), "... and its stored files have no EXIF")


def main() -> int:
    for name, fn in (
        ("0. the setting: default, choices, compose", settings_section),
        ("1. off: stored as sent, byte for byte", off_section),
        ("2. on: a camera photo with EXIF rotation and GPS", camera_section),
        ("3. on: files left alone", left_alone_section),
        ("4. on: the website upload", upload_section),
        ("5. on: save_photo, public Telegram reports, staff Telegram drafts", other_entries_section),
        ("6. when each intake photo was taken (D7)", taken_section),
        ("7. the AI's preview is the same, switch on or off (accuracy gate)", ai_same_section),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
