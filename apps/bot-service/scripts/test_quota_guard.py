"""The shared quota guard (app/ai/quota_guard.py): production and staging on
ONE Gemini key.

    python scripts/test_quota_guard.py

Checked:
  1. defaults (no shared folder, production lane): exactly the old window --
     the same file, the same format, no slot for a retry, nothing counted,
     nothing written anywhere else;
  2. production lane in the shared folder: 12 a minute of its own, spaced,
     never waiting for staging; every request counted per model and lane;
  3. staging lane: a slot only while the window (with it) holds <= 6, at most
     4 a minute; stops at 300 staging requests, at 400 production requests,
     on a model with < 300 left, on a model with no known limit; Google's own
     stated limit wins; nothing at all without the folder;
  4. the day rolls over at midnight Pacific (08:00 UTC in November);
  5. a set folder that isn't there: production falls back to its own files,
     staging is refused; a broken folder never stops production;
  6. concurrent writers: threads, and separate processes (flock on Linux, a
     byte lock on Windows): no request lost from the window or the count;
  7. call_structured: a refused model is skipped with nothing sent (noted
     "day_spent" with the reason), every model refused ends in DayUsedUp,
     and a busy model's retry takes a slot (and a count) in shared mode only;
  8. the day memory (ai/allowance.py) moves into the folder, seeded once
     from the stack's own;
  9. production's extras tiers (RICH / NORMAL / LEAN) and the admin summary
     (GET /system/info ai_quota);
 10. the settings refuse a typo (a lane, a day limit).

Offline: temporary folders, a fake Gemini client, a fake clock. No real key.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

_tmp = Path(tempfile.mkdtemp(prefix="quota-guard-test-"))
STORAGE = _tmp / "storage"
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSy" + "Q" * 33
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
for _name in ("GEMINI_SHARED_QUOTA_DIR", "GEMINI_QUOTA_LANE"):
    os.environ.pop(_name, None)

from app import config  # noqa: E402
from app.ai import allowance, quota_guard, rate_limit, structured  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import Settings, settings  # noqa: E402
from app.reports.db import init_db  # noqa: E402

PASSED = [0]
FAILURES: list[str] = []
LITE5, LITE1 = "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"
KEY = "test-key-value"


def check(cond, label: str, detail: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append(label)
        print(f"    FAIL {label}" + (f"\n         {detail}" if detail else ""))
    return bool(cond)


# ---------------------------------------------------------------- harness

DEFAULTS = {name: getattr(settings, name) for name in (
    "gemini_shared_quota_dir", "gemini_quota_lane", "gemini_max_requests_per_minute",
    "gemini_min_call_interval_seconds", "gemini_staging_max_per_minute", "gemini_staging_window_share",
    "gemini_staging_day_max", "gemini_staging_prod_busy", "gemini_staging_model_reserve",
    "gemini_quota_day_limits", "gemini_quota_tier_model")}


def configure(**values) -> None:
    for name, value in DEFAULTS.items():
        setattr(settings, name, value)
    for name, value in values.items():
        setattr(settings, name, value)


class Clock:
    """quota_guard's wall clock and sleep, and allowance's day clock."""

    def __init__(self, at: datetime):
        self.t = at.timestamp()
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s

    def utc(self) -> datetime:
        return datetime.fromtimestamp(self.t, tz=timezone.utc)


CLOCK = Clock(datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc))  # 20:00 on 21 Nov in California


def use_clock(at: datetime | None = None) -> Clock:
    global CLOCK
    CLOCK = Clock(at or datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc))
    quota_guard._now = CLOCK.now
    quota_guard._sleep = CLOCK.sleep
    allowance._clock = CLOCK.utc
    return CLOCK


def new_shared() -> Path:
    d = Path(tempfile.mkdtemp(prefix="shared-", dir=_tmp))
    return d


def tag() -> str:
    return quota_guard.key_tag(KEY)


def day_file(root: Path) -> dict:
    return json.loads((root / "day" / f"{tag()}.json").read_text(encoding="utf-8"))


def window_file(root: Path) -> list:
    return json.loads((root / "window" / f"{tag()}.json").read_text(encoding="utf-8"))["stamps"]


def put_window(root: Path, stamps: list) -> None:
    (root / "window").mkdir(parents=True, exist_ok=True)
    (root / "window" / f"{tag()}.json").write_text(json.dumps({"stamps": stamps}), encoding="utf-8")


def put_day(root: Path, counts: dict) -> None:
    (root / "day").mkdir(parents=True, exist_ok=True)
    (root / "day" / f"{tag()}.json").write_text(json.dumps({"day": quota_guard.today(), "counts": counts}),
                                                encoding="utf-8")


def refused(fn) -> str | None:
    try:
        fn()
    except quota_guard.LaneRefused as exc:
        return exc.why
    return None


def files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------- sections

def defaults_unchanged() -> None:
    print("\n1. defaults: no shared folder, production lane -- the old window, exactly")
    configure(gemini_min_call_interval_seconds=0.0)
    use_clock()
    check(settings.gemini_shared_quota_dir == "" and settings.gemini_quota_lane == "prod",
          "the defaults: no shared folder, production lane")
    check(quota_guard.mode() == "local", "mode local")
    before = files_under(STORAGE)
    waited = rate_limit.acquire(KEY, model=LITE5, fingerprint="fp")
    window = STORAGE / ".ratelimit" / f"{quota_guard.key_tag(KEY)}.json"
    data = json.loads(window.read_text())
    check(waited == 0.0 and isinstance(data, list) and len(data) == 1 and isinstance(data[0], float),
          "acquire takes a slot in STORAGE_DIR/.ratelimit/<tag>.json, a plain list of stamps (the old format)",
          repr(data))
    check(rate_limit.acquire_retry(KEY, model=LITE5, fingerprint="fp") == 0.0
          and len(json.loads(window.read_text())) == 1, "a retry takes no slot and writes nothing (as before)")
    added = sorted(set(files_under(STORAGE)) - set(before))
    check(all(p.startswith(".ratelimit/") for p in added), "nothing written outside .ratelimit/", repr(added))
    check(quota_guard.day_counts(KEY) == {}, "nothing counted per day without the folder")
    check(rate_limit.estimate_wait(KEY) == 0.0, "estimate_wait as before (window not full)")
    state = quota_guard.summary()
    check(state["mode"] == "local" and state["keys"] == [], "the admin summary says local, no counts")


def prod_lane() -> None:
    print("\n2. production lane in the shared folder")
    root = new_shared()
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=4.0)
    clock = use_clock()
    own_window = STORAGE / ".ratelimit" / f"{tag()}.json"
    own_before = own_window.read_text()
    check(quota_guard.mode() == "shared", "mode shared once the folder is there")
    for _ in range(3):
        rate_limit.acquire(KEY, model=LITE5, fingerprint="fp")
    check(clock.slept == [4.0, 4.0], "spaced 4 s apart (its own requests)", repr(clock.slept))
    check(day_file(root)["counts"] == {LITE5: {"prod": 3}}, "3 requests counted for the model, prod lane",
          repr(day_file(root)))
    check(own_window.read_text() == own_before, "the stack's own window is not used")

    # Staging holds 4 of the window, all just now: production doesn't wait.
    root = new_shared()
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=4.0)
    clock = use_clock()
    put_window(root, [[clock.t - 1, "staging"]] * 4)
    rate_limit.acquire(KEY, model=LITE5)
    check(clock.slept == [], "production never waits for staging's requests (spacing or count)", repr(clock.slept))

    root = new_shared()
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    clock = use_clock()
    for _ in range(12):
        rate_limit.acquire(KEY, model=LITE1)
    check(clock.slept == [] and len(window_file(root)) == 12, "12 in a minute without waiting, 12 in the window")
    rate_limit.acquire(KEY, model=LITE1)
    check(len(clock.slept) == 1 and 59.0 <= clock.slept[0] <= 60.0, "the 13th waits for the oldest to age out",
          repr(clock.slept))
    check(sum(day_file(root)["counts"][LITE1].values()) == 13, "all 13 counted")
    check(len(window_file(root)) == 1, "the window keeps only the last minute (the first 12 aged out)")
    rate_limit.acquire_retry(KEY, model=LITE1)
    check(day_file(root)["counts"][LITE1]["prod"] == 14, "a retry is a request: slot and count in shared mode")
    rate_limit.acquire(KEY)
    check(day_file(root)["counts"].get(quota_guard.UNKNOWN_MODEL) == {"prod": 1}, "a request with no model is counted too")


def staging_lane() -> None:
    print("\n3. staging lane")
    configure(gemini_quota_lane="staging", gemini_min_call_interval_seconds=0.0,
              gemini_shared_quota_dir=str(_tmp / "not-there"))
    use_clock()
    why = refused(lambda: rate_limit.acquire(KEY, model=LITE5))
    check(why is not None and "no shared quota folder" in why, "without the folder: refused, nothing sent", repr(why))
    check(refused(lambda: rate_limit.acquire_retry(KEY, model=LITE5)) is not None,
          "a retry without the folder is refused too")

    root = new_shared()
    configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    clock = use_clock()
    put_window(root, [[clock.t - 10, "prod"]] * 5)
    rate_limit.acquire(KEY, model=LITE5)
    check(clock.slept == [], "a slot while the window, with it, holds 6 (5 production + 1)")
    rate_limit.acquire(KEY, model=LITE5)
    check(len(clock.slept) == 1 and 49.0 <= clock.slept[0] <= 51.0,
          "the next waits: 6 in the window, production keeps 6 free", repr(clock.slept))

    root = new_shared()
    configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    clock = use_clock()
    for _ in range(4):
        rate_limit.acquire(KEY, model=LITE5)
    check(clock.slept == [], "4 staging requests in a minute without waiting")
    rate_limit.acquire(KEY, model=LITE5)
    check(len(clock.slept) == 1 and clock.slept[0] >= 59.0, "the 5th waits: at most 4 a minute", repr(clock.slept))
    check(day_file(root)["counts"] == {LITE5: {"staging": 5}}, "counted in the staging lane", repr(day_file(root)))

    configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=6.0)
    clock = use_clock()
    put_window(root, [[clock.t - 2, "prod"]])
    put_day(root, {})
    rate_limit.acquire(KEY, model=LITE5)
    check(clock.slept == [4.0], "staging keeps its spacing after production's last request too", repr(clock.slept))

    cases = [
        ({LITE5: {"staging": 150}, LITE1: {"staging": 150}}, LITE1, "staging has sent 300"),
        ({LITE5: {"prod": 400}}, LITE1, "production has sent 400"),
        ({LITE5: {"prod": 150, "staging": 51}}, LITE5, "has 299 of 500 left"),
        ({}, "gemini-3-flash-preview", "no known daily limit"),
    ]
    for counts, model, words in cases:
        root = new_shared()
        configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
        use_clock()
        put_day(root, counts)
        why = refused(lambda: rate_limit.acquire(KEY, model=model))
        check(why is not None and words in why, f"refused when {words}", repr(why))
        check(day_file(root)["counts"] == counts and not (root / "window" / f"{tag()}.json").exists(),
              "  ... and nothing counted or put in the window")
    root = new_shared()
    configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    use_clock()
    put_day(root, {LITE5: {"prod": 150, "staging": 50}, LITE1: {"prod": 249}})
    check(refused(lambda: rate_limit.acquire(KEY, model=LITE5)) is None,
          "allowed with exactly 300 left, under 300 staging and 400 production")
    allowance.clear()
    allowance.mark_spent("fp-free", LITE5, 250)
    allowance.forget("fp-free")  # spent cleared, the stated limit kept
    put_day(root, {})
    why = refused(lambda: rate_limit.acquire(KEY, model=LITE5, fingerprint="fp-free"))
    check(why is not None and "of 250 left" in why, "Google's own stated limit (250) wins over the table", repr(why))
    allowance.clear()


def day_rollover() -> None:
    print("\n4. the day rolls over at midnight Pacific")
    root = new_shared()
    configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    clock = use_clock(datetime(2026, 11, 22, 7, 59, 0, tzinfo=timezone.utc))  # 23:59 on 21 Nov in California
    put_day(root, {LITE5: {"staging": 300}})
    check(quota_guard.today() == "2026-11-21", "07:59 UTC is still 21 Nov in California", quota_guard.today())
    check(refused(lambda: rate_limit.acquire(KEY, model=LITE5)) is not None, "staging stopped before the reset")
    clock.t = datetime(2026, 11, 22, 8, 0, 1, tzinfo=timezone.utc).timestamp()
    check(quota_guard.today() == "2026-11-22", "08:00 UTC is a new day (PST)", quota_guard.today())
    check(refused(lambda: rate_limit.acquire(KEY, model=LITE5)) is None, "staging may send again after the reset")
    check(day_file(root) == {"day": "2026-11-22", "counts": {LITE5: {"staging": 1}}},
          "yesterday's counts are gone, today's start at 1", repr(day_file(root)))
    use_clock(datetime(2026, 7, 1, 6, 59, tzinfo=timezone.utc))
    check(quota_guard.today() == "2026-06-30", "in summer (PDT) the day ends at 07:00 UTC", quota_guard.today())
    use_clock(datetime(2026, 7, 1, 7, 0, 1, tzinfo=timezone.utc))
    check(quota_guard.today() == "2026-07-01", "... and starts again then")


def missing_folder() -> None:
    print("\n5. a folder that isn't there, or breaks")
    configure(gemini_shared_quota_dir=str(_tmp / "never-mounted"), gemini_min_call_interval_seconds=0.0)
    use_clock()
    before = len(json.loads((STORAGE / ".ratelimit" / f"{tag()}.json").read_text()))
    check(quota_guard.mode() == "local", "production with a set but missing folder: its own files")
    rate_limit.acquire(KEY, model=LITE5)
    after = len(json.loads((STORAGE / ".ratelimit" / f"{tag()}.json").read_text()))
    check(after == before + 1, "the slot is taken in the stack's own window, as before the guard")
    check(not (_tmp / "never-mounted").exists(), "nothing creates the missing folder")

    root = new_shared()
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    use_clock()
    real = quota_guard._read_day
    quota_guard._read_day = lambda path: (_ for _ in ()).throw(OSError("disk full"))
    try:
        before = len(json.loads((STORAGE / ".ratelimit" / f"{tag()}.json").read_text()))
        rate_limit.acquire(KEY, model=LITE5)
        after = len(json.loads((STORAGE / ".ratelimit" / f"{tag()}.json").read_text()))
        check(after == before + 1, "a failing shared folder: production falls back to its own window, no error")
        configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
        why = refused(lambda: rate_limit.acquire(KEY, model=LITE5))
        check(why is not None and "failed" in why, "... staging is refused", repr(why))
    finally:
        quota_guard._read_day = real
    (root / "day").mkdir(exist_ok=True)
    (root / "day" / f"{tag()}.json").write_text("{not json", encoding="utf-8")
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    rate_limit.acquire(KEY, model=LITE5)
    check(day_file(root)["counts"] == {LITE5: {"prod": 1}}, "a corrupt day file starts afresh, no error")


CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ["GEMINI_SHARED_QUOTA_DIR"] = sys.argv[2]
os.environ["GEMINI_QUOTA_LANE"] = sys.argv[4]
os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "100000"
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["GEMINI_STAGING_MAX_PER_MINUTE"] = "100000"
os.environ["GEMINI_STAGING_WINDOW_SHARE"] = "100000"
os.environ["GEMINI_STAGING_DAY_MAX"] = "100000"
os.environ["GEMINI_STAGING_PROD_BUSY"] = "100000"
os.environ["GEMINI_STAGING_MODEL_RESERVE"] = "0"
from app.ai import rate_limit
for _ in range(int(sys.argv[3])):
    rate_limit.acquire("test-key-value", model="gemini-3.5-flash-lite")
print("done")
"""


def concurrent_writers() -> None:
    print("\n6. concurrent writers")
    root = new_shared()
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0,
              gemini_max_requests_per_minute=100000)
    quota_guard._now = time.time
    quota_guard._sleep = time.sleep
    allowance._clock = lambda: datetime.now(timezone.utc)
    errors: list[str] = []

    def worker():
        try:
            for _ in range(25):
                rate_limit.acquire(KEY, model=LITE5)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    counts = day_file(root)["counts"][LITE5]
    check(not errors and counts == {"prod": 200} and len(window_file(root)) == 200,
          "8 threads x 25: 200 counted, 200 in the window", f"{counts} {len(window_file(root))} {errors[:2]}")

    root = new_shared()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEMINI_STAGING") and k != "GEMINI_QUOTA_LANE"}
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), str(root), "30", lane],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
             for lane in ("prod", "prod", "staging", "staging", "prod", "staging")]
    outs = [p.communicate(timeout=180) for p in procs]
    bad = [err[-300:] for (out, err), p in zip(outs, procs) if p.returncode != 0 or "done" not in out]
    counts = day_file(root)["counts"].get(LITE5, {})
    check(not bad and counts == {"prod": 90, "staging": 90},
          "6 processes (3 per lane) x 30, one shared folder: 180 counted, none lost",
          f"{counts} {bad[:1]}")
    check(len(window_file(root)) == 180, "... and 180 in the window", str(len(window_file(root))))
    lock_kind = "flock" if sys.platform != "win32" else "a Windows byte lock (the server uses flock)"
    print(f"         (locking here: {lock_kind})")


class Busy(Exception):
    status_code = 503

    def __init__(self):
        super().__init__("Error code: 503 - currently experiencing high demand")


SENT: list[str] = []
PLAN: dict[str, list] = {}


def _create(**kwargs):
    SENT.append(kwargs["model"])
    step = PLAN[kwargs["model"]].pop(0)
    if isinstance(step, Exception):
        raise step
    return SimpleNamespace(output_text=step, usage=None, outputs=[], id="fake")


def call(models):
    SENT.clear()
    notes: list[dict] = []
    real_note = structured.runs.note_attempt
    structured.runs.note_attempt = notes.append
    try:
        return structured.call_structured(
            purpose="draft", system_instruction="s", input_parts=[{"type": "text", "text": "x"}],
            schema={"type": "object"}, parse=lambda raw: raw, models=models, actor=AiActor.cli(), timeout=5,
        ), notes
    finally:
        structured.runs.note_attempt = real_note


def call_structured_lanes() -> None:
    print("\n7. call_structured with the guard")
    init_db()
    structured.get_client = lambda key: SimpleNamespace(interactions=SimpleNamespace(create=_create))
    env_key = os.environ["GEMINI_API_KEY"]
    root = new_shared()
    configure(gemini_quota_lane="staging", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    use_clock()
    real_sleep = structured.time.sleep
    structured.time.sleep = lambda s: None
    try:
        _lanes_in_call_structured(root, env_key)
    finally:
        structured.time.sleep = real_sleep


def _lanes_in_call_structured(root: Path, env_key: str) -> None:
    PLAN.clear()
    PLAN[LITE5] = ['{"ok": 1}']
    out, notes = call(["gemini-3-flash-preview", LITE5])
    check(out == '{"ok": 1}' and SENT == [LITE5], "the strong model (no known limit) is skipped, nothing sent; the light one answers",
          repr(SENT))
    first = notes[0] if notes else {}
    check(first.get("outcome") == "day_spent" and "no known daily limit" in first.get("lane_stop", "")
          and first.get("spent_until"), "the skip is in the run log: day_spent, the reason, until the reset", repr(first))
    day = json.loads((root / "day" / f"{quota_guard.key_tag(env_key)}.json").read_text())
    check(day["counts"] == {LITE5: {"staging": 1}}, "the request sent is counted (staging lane)", repr(day))

    put_day_key = root / "day" / f"{quota_guard.key_tag(env_key)}.json"
    put_day_key.write_text(json.dumps({"day": quota_guard.today(), "counts": {LITE5: {"staging": 300}}}))
    PLAN.clear()
    try:
        call([LITE5, LITE1])
        check(False, "every model refused: the call fails")
    except allowance.DayUsedUp as exc:
        check(SENT == [], "every model refused: nothing sent")
        check(allowance.day_spent_error(exc), "it ends in DayUsedUp (\"used up until the reset\")", repr(exc))

    root = new_shared()
    configure(gemini_quota_lane="prod", gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    PLAN.clear()
    PLAN[LITE5] = [Busy(), '{"ok": 2}']
    out, _ = call([LITE5])
    day = json.loads((root / "day" / f"{quota_guard.key_tag(env_key)}.json").read_text())
    check(out == '{"ok": 2}' and SENT == [LITE5, LITE5] and day["counts"] == {LITE5: {"prod": 2}},
          "shared mode: a busy model's retry is counted (2 requests sent, 2 counted)", repr(day))

    configure(gemini_min_call_interval_seconds=0.0)
    slots: list[str] = []
    real_local = rate_limit._acquire_local
    rate_limit._acquire_local = lambda api_key, deadline=None: slots.append("slot") or 0.0
    try:
        PLAN.clear()
        PLAN[LITE5] = [Busy(), '{"ok": 3}']
        out, _ = call([LITE5])
    finally:
        rate_limit._acquire_local = real_local
    check(out == '{"ok": 3}' and slots == ["slot"], "no shared folder: one slot for the model, none for its retry (as before)",
          repr(slots))


def allowance_moves() -> None:
    print("\n8. the day memory moves into the shared folder")
    configure()
    use_clock()
    allowance.clear()
    allowance.mark_spent("fp-local", LITE5, 500)
    local = STORAGE / ".allowance" / "days.json"
    check(local.exists(), "without the folder: STORAGE_DIR/.allowance/days.json, as before")
    root = new_shared()
    configure(gemini_shared_quota_dir=str(root))
    check(allowance.spent_until("fp-local", LITE5) is not None,
          "with the folder: the stack's own memory is copied in on first use (still spent)")
    shared = root / "allowance" / "days.json"
    check(shared.exists(), "the store is <folder>/allowance/days.json")
    allowance.mark_spent("fp-shared", LITE1, 500)
    local_data = json.loads(local.read_text())
    check(not any("fp-shared" in k for k in local_data.get("spent", {})) and
          len(json.loads(shared.read_text())["spent"]) == 2, "new entries go to the shared store only")
    configure()
    check(allowance.spent_until("fp-shared", LITE1) is None, "back without the folder: the stack's own store again")
    allowance.clear()


def tiers_and_summary() -> None:
    print("\n9. production's extras tiers and the admin summary")
    root = new_shared()
    configure(gemini_shared_quota_dir=str(root))
    use_clock()
    env_key = os.environ["GEMINI_API_KEY"]
    path = root / "day" / f"{quota_guard.key_tag(env_key)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    for used, want in ((0, "RICH"), (249, "RICH"), (250, "NORMAL"), (375, "NORMAL"), (376, "LEAN"), (600, "LEAN")):
        path.write_text(json.dumps({"day": quota_guard.today(), "counts": {LITE5: {"prod": used}}}))
        check(quota_guard.extras_tier() == want, f"{used} of 500 used: {want}", quota_guard.extras_tier())
    configure()
    check(quota_guard.extras_state() == {"tier": "RICH", "model": LITE5, "limit": 500, "used": None, "left": None,
                                         "known": False}, "no folder: RICH, nothing known")
    configure(gemini_shared_quota_dir=str(root))
    path.write_text(json.dumps({"day": quota_guard.today(), "counts": {LITE5: {"prod": 10, "staging": 300}}}))
    import app.api.main as main
    info = main._ai_quota()
    key = (info or {}).get("keys", [{}])[0]
    check(info and info["mode"] == "shared" and key.get("key", "").startswith("company"),
          "system info ai_quota: shared, the company key by its label", repr(info)[:300])
    check(key.get("models", {}).get(LITE5) == {"prod": 10, "staging": 300, "limit": 500, "left": 190}
          and "staging has sent 300" in (key.get("staging_stopped") or ""),
          "per model per lane, the limit, what's left, and staging stopped", repr(key)[:300])
    check(env_key not in json.dumps(info), "no key in it")
    check(info["extras"]["tier"] == "NORMAL" and info["extras"]["left"] == 190,
          "with production's extras tier (190 of 500 left: NORMAL)", repr(info.get("extras")))


def settings_refuse_typos() -> None:
    print("\n10. the settings refuse a typo")
    for env, label in (({"GEMINI_QUOTA_LANE": "stagng"}, "a lane that doesn't exist"),
                       ({"GEMINI_QUOTA_DAY_LIMITS": "gemini-3.5-flash-lite:500"}, "a day limit without ="),
                       ({"GEMINI_STAGING_DAY_MAX": "-1"}, "a negative cap")):
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            Settings()
            check(False, f"refused: {label}")
        except Exception:
            check(True, f"refused: {label}")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    os.environ["GEMINI_QUOTA_LANE"] = " Staging "
    try:
        check(Settings().gemini_quota_lane == "staging", "the lane written the one way")
    finally:
        os.environ.pop("GEMINI_QUOTA_LANE", None)
    check(config.day_limits("a=1, b = 2") == {"a": 1, "b": 2}, "day limits parse")


def slot_deadlines() -> None:
    print("\n11. a caller's deadline for its slot (review L4)")
    configure(gemini_min_call_interval_seconds=0.0)
    use_clock()
    key = "deadline-key"
    window = STORAGE / ".ratelimit" / f"{quota_guard.key_tag(key)}.json"
    window.parent.mkdir(parents=True, exist_ok=True)
    full = [time.time()] * settings.gemini_max_requests_per_minute
    window.write_text(json.dumps(full))
    try:
        rate_limit.acquire(key, deadline=time.monotonic() + 5)
        check(False, "own window full, 5 s left: SlotDeadline")
    except rate_limit.SlotDeadline:
        check(json.loads(window.read_text()) == full, "own window full, 5 s left: SlotDeadline, no slot taken")
    try:
        with rate_limit.slot_deadline(time.monotonic() + 5):
            rate_limit.acquire(key)
        check(False, "the same through slot_deadline(): acquire(key) unchanged for its callers")
    except rate_limit.SlotDeadline:
        check(True, "the same through slot_deadline(): acquire(key) unchanged for its callers")
    window.write_text("[]")
    check(rate_limit.acquire(key, deadline=time.monotonic() + 5) == 0.0 and len(json.loads(window.read_text())) == 1,
          "a free slot before the deadline is taken")
    try:
        rate_limit.acquire(key, deadline=time.monotonic() - 1)
        check(False, "a deadline already past: SlotDeadline")
    except rate_limit.SlotDeadline:
        check(len(json.loads(window.read_text())) == 1, "a deadline already past: SlotDeadline, nothing taken")
    with rate_limit.slot_deadline(None):
        check(rate_limit.acquire(key) == 0.0, "slot_deadline(None): no limit, as always")

    root = new_shared()
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0)
    clock = use_clock()
    for _ in range(12):
        rate_limit.acquire(KEY, model=LITE5)
    try:
        rate_limit.acquire(KEY, model=LITE5, deadline=time.monotonic() + 5)
        check(False, "shared window full: SlotDeadline")
    except rate_limit.SlotDeadline:
        check(day_file(root)["counts"] == {LITE5: {"prod": 12}} and len(window_file(root)) == 12 and clock.slept == [],
              "shared window full, 5 s left: SlotDeadline, nothing counted, no wait")

    # call_structured with a deadline and a full window: nothing sent.
    configure(gemini_min_call_interval_seconds=0.0)
    init_db()
    env_key = os.environ["GEMINI_API_KEY"]
    env_window = STORAGE / ".ratelimit" / f"{quota_guard.key_tag(env_key)}.json"
    env_window.parent.mkdir(parents=True, exist_ok=True)
    env_window.write_text(json.dumps([time.time()] * settings.gemini_max_requests_per_minute))
    structured.get_client = lambda k: SimpleNamespace(interactions=SimpleNamespace(create=_create))
    SENT.clear()
    PLAN.clear()
    PLAN[LITE5] = ['{"ok": 9}']
    started = time.monotonic()
    try:
        structured.call_structured(
            purpose="link", system_instruction="s", input_parts=[{"type": "text", "text": "x"}],
            schema={"type": "object"}, parse=lambda raw: raw, models=[LITE5], actor=AiActor.cli(), timeout=5,
            deadline=time.monotonic() + 3,
        )
        check(False, "call_structured past its time box: fails")
    except Exception:
        check(SENT == [] and time.monotonic() - started < 2.5,
              "call_structured whose slot would come after its deadline: no request, no 60 s wait", repr(SENT))
    env_window.write_text("[]")

    # Shared mode: a busy model's retry whose slot would come past the
    # deadline ends the call -- no retry, and no next model either.
    root = new_shared()
    configure(gemini_shared_quota_dir=str(root), gemini_min_call_interval_seconds=0.0,
              gemini_max_requests_per_minute=1)
    use_clock()
    real_sleep = structured.time.sleep
    structured.time.sleep = lambda s: None
    SENT.clear()
    PLAN.clear()
    PLAN[LITE5] = [Busy(), '{"ok": 1}']
    PLAN[LITE1] = ['{"ok": 2}']
    try:
        structured.call_structured(
            purpose="link", system_instruction="s", input_parts=[{"type": "text", "text": "x"}],
            schema={"type": "object"}, parse=lambda raw: raw, models=[LITE5, LITE1], actor=AiActor.cli(), timeout=5,
            deadline=time.monotonic() + 20,
        )
        check(False, "a retry past the deadline ends the call")
    except Exception:
        day = json.loads((root / "day" / f"{quota_guard.key_tag(env_key)}.json").read_text())
        check(SENT == [LITE5] and day["counts"] == {LITE5: {"prod": 1}},
              "shared mode: a retry whose slot comes past the deadline -- not sent, not counted, no next model",
              repr(SENT) + repr(day))
    finally:
        structured.time.sleep = real_sleep


SECTIONS = [defaults_unchanged, prod_lane, staging_lane, day_rollover, missing_folder, concurrent_writers,
            call_structured_lanes, allowance_moves, tiers_and_summary, settings_refuse_typos, slot_deadlines]


def main() -> int:
    for section in SECTIONS:
        try:
            section()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            FAILURES.append(f"{section.__name__} crashed: {type(exc).__name__}: {exc}")
        finally:
            configure()
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAIL {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
