"""Runs the bot-service test suite the way CI does (.github/workflows/ci.yml).

    python scripts/run_ci_tests.py              # every scripts/test_*.py and check_*.py
    python scripts/run_ci_tests.py test_auth    # just these (names with or without .py)

Each script runs alone in its own process, one after another (several share
cross-process files such as the AI rate-limit window, so running them side by
side makes them flaky). Scripts named in scripts/ci_skip.txt are skipped; each
line there must give a reason.

No real secrets, ever: GEMINI_API_KEY, TELEGRAM_BOT_TOKEN and the other key
variables are removed from the environment before any test starts. A test
that needs a key sets its own fake one (test_intake_flow does). Outside
traffic is pointed at a dead local proxy (--offline, on by default), so a test
that tries to reach Gemini, Telegram or any other host fails fast instead of
calling out; TestClient and localhost servers are not affected.

Exit code 0 when every script that ran passed; 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # apps/bot-service
SCRIPTS = ROOT / "scripts"
SKIP_FILE = SCRIPTS / "ci_skip.txt"

# Never handed to a test, whatever the calling shell has.
SECRET_VARS = (
    "GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    "TELEGRAM_BOT_TOKEN", "CATALOGUE_WORKER_TOKEN", "WHATSAPP_TOKEN", "WHATSAPP_ACCESS_TOKEN",
)

# Known to fail now and then when run straight after the others (shared
# rate-limit / flood-control state between processes); each passes alone.
# Retried once; a pass on the retry is reported as "flaky", not hidden.
RETRY_ONCE = {
    "test_intake_flow": "sort-count check depends on the shared AI rate-limit window",
    "test_telegram_public": "flood-control ('Please slow down') check is timing-sensitive",
}

# Port 9 (discard) on localhost: nothing listens, so a proxied request fails
# at once with a connection error.
DEAD_PROXY = "http://127.0.0.1:9"


def read_skips() -> dict[str, str]:
    """ci_skip.txt: `name  # reason` per line; blank lines and lines starting
    with # are ignored. A line without a reason is an error, as is a name
    that no longer exists (a stale entry would hide a test by accident)."""
    skips: dict[str, str] = {}
    if not SKIP_FILE.exists():
        return skips
    problems = []
    for n, line in enumerate(SKIP_FILE.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        name, _, reason = text.partition("#")
        name, reason = name.strip().removesuffix(".py"), reason.strip()
        if not reason:
            problems.append(f"ci_skip.txt line {n}: {name} has no reason (write `{name}  # why`)")
        elif not (SCRIPTS / f"{name}.py").exists():
            problems.append(f"ci_skip.txt line {n}: scripts/{name}.py does not exist (remove the line)")
        else:
            skips[name] = reason
    if problems:
        sys.exit("\n".join(problems))
    return skips


def test_env(offline: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SECRET_VARS}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if offline:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[var] = DEAD_PROXY
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1,testserver"
    return env


def run_one(name: str, env: dict[str, str], timeout: int) -> tuple[bool, float, str]:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / f"{name}.py")],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        out = proc.stdout.decode("utf-8", "replace")
        ok = proc.returncode == 0
        if not ok:
            out += f"\n[exit code {proc.returncode}]"
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace") + f"\n[timed out after {timeout} s]"
        ok = False
    return ok, time.monotonic() - start, out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="only these scripts (default: all test_*.py and check_*.py)")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per script (default 600)")
    parser.add_argument("--allow-network", action="store_true", help="do not point outside traffic at a dead proxy")
    parser.add_argument("--log-dir", type=Path, help="also write each script's output to <dir>/<name>.log")
    args = parser.parse_args()
    # A test's output can hold any character; a Windows console's code page
    # can't print them all, and the runner must not die reporting a failure.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    skips = read_skips()
    if args.names:
        names = [n.removesuffix(".py") for n in args.names]
        missing = [n for n in names if not (SCRIPTS / f"{n}.py").exists()]
        if missing:
            sys.exit(f"no such script: {', '.join(missing)}")
    else:
        names = sorted(p.stem for p in SCRIPTS.glob("test_*.py")) + sorted(p.stem for p in SCRIPTS.glob("check_*.py"))

    env = test_env(offline=not args.allow_network)
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    if args.log_dir:
        args.log_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, str, float]] = []  # name, status, seconds
    for name in names:
        if name in skips:
            print(f"SKIP  {name}  ({skips[name]})", flush=True)
            results.append((name, "skip", 0.0))
            continue
        ok, secs, out = run_one(name, env, args.timeout)
        status = "pass" if ok else "FAIL"
        if not ok and name in RETRY_ONCE:
            print(f"RETRY {name}  ({RETRY_ONCE[name]})", flush=True)
            ok, secs2, out2 = run_one(name, env, args.timeout)
            out = out + "\n----- retry -----\n" + out2
            secs += secs2
            status = "flaky" if ok else "FAIL"
        if args.log_dir:
            (args.log_dir / f"{name}.log").write_text(out, encoding="utf-8")
        print(f"{status.upper():5} {name}  {secs:.1f}s", flush=True)
        if status == "FAIL":
            if github:
                print(f"::group::{name} output")
            print(out if github else out[-6000:], flush=True)
            if github:
                print("::endgroup::")
                print(f"::error title={name} failed::scripts/{name}.py failed (output above)")
        results.append((name, status, secs))

    failed = [n for n, s, _ in results if s == "FAIL"]
    flaky = [n for n, s, _ in results if s == "flaky"]
    ran = [n for n, s, _ in results if s != "skip"]
    print()
    print(f"{len(ran)} ran, {len(ran) - len(failed)} passed ({len(flaky)} on retry), "
          f"{len(failed)} failed, {len(results) - len(ran)} skipped, "
          f"{sum(s for _, _, s in results):.0f}s")
    if flaky:
        print("passed on retry: " + ", ".join(flaky))
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
