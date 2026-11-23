"""Tests the sliding-window limiter, including across processes.

Cross-process coordination is the whole reason this exists -- the previous
module-level gate let carlink_api and carlink_telegram_bot each run a
private counter against one shared API key, doubling the real request rate.
That property can't be checked by reasoning, so it's exercised with actual
subprocesses here.

    python3 scripts/test_rate_limit.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"    {'ok  ' if ok else 'FAIL'} {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(label)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="ratelimit-test-")
    os.environ["STORAGE_DIR"] = tmp
    os.environ.setdefault("GEMINI_API_KEY", "x")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
    os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "4"
    os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"

    from app.ai import rate_limit

    print("  window of 4/min, no spacing")
    t0 = time.monotonic()
    for _ in range(4):
        rate_limit.acquire("k1")
    check("4 calls fit the window immediately", time.monotonic() - t0 < 1.0,
          f"({time.monotonic() - t0:.2f}s)")

    check("a 5th call is told to wait", rate_limit.estimate_wait("k1") > 50,
          f"(estimate {rate_limit.estimate_wait('k1'):.0f}s)")

    print("\n  separate keys get separate windows")
    t0 = time.monotonic()
    rate_limit.acquire("k2")
    check("a different key is not blocked", time.monotonic() - t0 < 1.0)

    print("\n  spacing between consecutive calls")
    os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "1"
    from app.config import Settings
    rate_limit.settings = Settings()
    t0 = time.monotonic()
    rate_limit.acquire("k3")
    rate_limit.acquire("k3")
    gap = time.monotonic() - t0
    check("second call is spaced by the interval", 0.9 <= gap < 2.5, f"({gap:.2f}s)")

    print("\n  cross-process: a second process shares the window")
    os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
    child = (
        "import os,sys,json,time;"
        f"sys.path.insert(0,{str(ROOT)!r});"
        "from app.ai import rate_limit;"
        "print(json.dumps(rate_limit.estimate_wait('k1')))"
    )
    out = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True,
                         env={**os.environ}, timeout=60)
    seen = float(out.stdout.strip() or -1) if out.returncode == 0 else -1
    check("separate process sees the SAME full window", seen > 50,
          f"(child estimate {seen:.0f}s)" if seen >= 0 else f"(child failed: {out.stderr[-120:]})")

    print("\n  429 retry delay parsing")
    err = Exception("Error code: 429 - {'error': {'message': 'You exceeded your current quota. "
                    "Please retry in 4.588220893s.', 'code': 'too_many_requests'}}")
    d = rate_limit.retry_after_seconds(err)
    check("reads the server's stated delay", d is not None and 4.5 < d < 5.5, f"({d}s)")
    check("recognises it as a rate limit", rate_limit.is_rate_limit_error(err))
    check("ignores unrelated errors", rate_limit.retry_after_seconds(Exception("boom")) is None)
    check("429 without a stated delay still pauses",
          rate_limit.retry_after_seconds(Exception("429 too_many_requests")) == 5.0)

    print("\n  corrupt window file doesn't break anything")
    bad = Path(tmp) / ".ratelimit"
    for f in bad.glob("*.json"):
        f.write_text("{{{not json")
    try:
        rate_limit.acquire("k1")
        check("recovers from a corrupt window", True)
    except Exception as e:
        check("recovers from a corrupt window", False, str(e))

    print("\n  a window file from before a reboot (review H3)")
    # Before H3 the stamps were time.monotonic(), which starts again at every
    # boot: one stamp from a box up 10 days made the next call sleep ~10 days.
    os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "4"
    os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "12"
    rate_limit.settings = Settings()
    path = rate_limit._window_file("reboot-key")
    now = time.time()
    ten_days = 10 * 86400
    # Stamps the old clock wrote ahead of today's wall time (a clock that
    # went back), and old monotonic numbers (seconds since an earlier boot).
    path.write_text(json.dumps([now + ten_days] * 12 + [864000.5, 3.2]))
    slept: list[float] = []
    real_sleep = rate_limit.time.sleep
    rate_limit.time.sleep = slept.append
    try:
        waited = rate_limit.acquire("reboot-key")
    finally:
        rate_limit.time.sleep = real_sleep
    check("the next call doesn't wait for stamps from before a reboot", waited == 0 and slept == [],
          f"(waited {waited}, slept {slept})")
    kept = json.loads(path.read_text())
    check("they are dropped from the file; the new stamp is wall-clock time",
          len(kept) == 1 and abs(kept[0] - time.time()) < 5, repr(kept))
    est = rate_limit.estimate_wait("reboot-key")
    check("estimate_wait agrees with acquire (the 4 s spacing after that call)", 0 < est <= 4.0, f"({est})")
    # A full window of stamps just inside the 1 s slack: the wait is capped at
    # the window (a fake clock that the fake sleep moves on).
    fake = [time.time()]
    path.write_text(json.dumps([fake[0] + 0.5] * 12))
    slept.clear()
    real_clock = rate_limit._clock
    rate_limit._clock = lambda: fake[0]

    def fake_sleep(s):
        slept.append(s)
        fake[0] += s

    rate_limit.time.sleep = fake_sleep
    try:
        rate_limit.acquire("reboot-key")
    finally:
        rate_limit.time.sleep = real_sleep
        rate_limit._clock = real_clock
    check("no single wait is longer than the window (60 s)", slept and max(slept) <= 60.0 and len(slept) <= 3,
          repr(slept))
    old_name = hashlib.sha256(b"reboot-key").hexdigest()[:16] + ".json"
    check("the file name is no longer the plain sha256 of the key", path.name != old_name, path.name)

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("  all rate-limit assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
