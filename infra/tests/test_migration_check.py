#!/usr/bin/env python3
"""infra/migration_check.sh, end to end without Docker.

    python infra/tests/test_migration_check.py

A synthetic "old" database (made by today's init_db, then a column dropped
and a one-time step's mark removed -- what a database from before a release
looks like) goes through the script. `docker` is a small Python stand-in:
volumes are temp folders, `docker run` runs the command here with the
container paths (-v) mapped to those folders and the code mount put on
PYTHONPATH, so the app's real init_db runs on the copy. Checked: PASS, the
added column and the one-time step are named (with the rollback note), row
counts printed, the copy given is never written, the throwaway volume is
gone afterwards, every run has --network none; and the refusals (not root,
no copy, a damaged copy).
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_deploy import BASH, FAILURES, PASSED, check  # noqa: E402

DOCKER_SHIM = r'''
import os, re, shutil, subprocess, sys
from pathlib import Path


def native(p):
    # Git Bash's /c/Users/... is C:/Users/... to Windows Python.
    m = re.match(r"^/([a-zA-Z])/(.*)$", p) if os.name == "nt" else None
    return f"{m.group(1).upper()}:/{m.group(2)}" if m else p


vols = Path(os.environ["MOCK_VOLS"])
log = open(os.environ["MOCK_LOG"], "a", encoding="utf-8")
args = sys.argv[1:]
log.write("docker " + " ".join(args) + "\n")
log.flush()
if args[:1] == ["inspect"]:
    print("carlink-staging-api:test")
    sys.exit(0)
if args[:2] == ["volume", "create"]:
    (vols / args[-1]).mkdir(parents=True)
    print(args[-1])
    sys.exit(0)
if args[:2] == ["volume", "rm"]:
    shutil.rmtree(vols / args[-1], ignore_errors=True)
    sys.exit(0)
if args[:1] != ["run"]:
    sys.exit(0)
i, mounts, env, entry, network = 1, [], {}, None, None
while i < len(args) and args[i].startswith("-"):
    a = args[i]
    if a in ("--rm", "-i"):
        i += 1
    elif a == "--network":
        network = args[i + 1]
        i += 2
    elif a == "-v":
        spec, drive = args[i + 1], ""
        if len(spec) > 2 and spec[1] == ":" and spec[2] in "/\\":
            drive, spec = spec[:2], spec[2:]
        parts = spec.split(":")
        src, dst = native(drive + parts[0]), parts[1]
        mounts.append((dst, Path(src) if ("/" in src or "\\" in src) else vols / src))
        i += 2
    elif a == "-e":
        k, _, v = args[i + 1].partition("=")
        env[k] = v
        i += 2
    elif a == "--entrypoint":
        entry = args[i + 1]
        i += 2
    else:
        sys.exit(f"shim: unknown docker run option {a}")
if network != "none":
    sys.exit("shim: every run must have --network none")
rest = args[i + 1:]
mounts.sort(key=lambda m: -len(m[0]))


def host(p):
    for dst, h in mounts:
        if p == dst or p.startswith(dst + "/"):
            return str(h) + p[len(dst):]
    return p


rest = [host(a) for a in rest]
child_env = dict(os.environ)
for k, v in env.items():
    if k == "DATABASE_URL":
        v = "sqlite:///" + Path(host(v[len("sqlite:///"):])).as_posix()
    elif v.startswith("/app/"):
        v = host(v)
    child_env[k] = v
cwd = None
for dst, h in mounts:
    if dst == "/app/app":
        cwd = str(h.parent)
        child_env["PYTHONPATH"] = cwd
if entry == "cp":
    shutil.copyfile(rest[0], rest[1])
    sys.exit(0)
if entry == "python":
    sys.exit(subprocess.run([sys.executable, *rest], env=child_env, cwd=cwd).returncode)
sys.exit(f"shim: entrypoint {entry}")
'''

PYTHON3_SHIM = r'''#!/bin/bash
# Windows Python needs C:/ paths for Git Bash's /tmp/... and /c/... ones.
args=()
for a in "$@"; do
    if [[ "$a" == /* ]] && command -v cygpath >/dev/null 2>&1; then a=$(cygpath -m "$a"); fi
    args+=("$a")
done
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"
exec "__PY__" "${args[@]}"
'''


DOCKER_WRAPPER = r'''#!/bin/bash
# Git Bash: a host path (/tmp/..., /c/...) before a "-v" colon becomes C:/...
# for Windows Python; container paths (/app, /check, /backup) stay as they are.
args=()
for a in "$@"; do
    if [[ "$a" == /* ]] && command -v cygpath >/dev/null 2>&1; then
        head=${a%%:*}
        rest=${a#"$head"}
        case "$head" in /app | /app/* | /check | /check/* | /backup/* | /tmp/secrets) ;; *) a="$(cygpath -m "$head")$rest" ;; esac
    fi
    args+=("$a")
done
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"  # pass them on as they are now
exec "__PY__" "__SHIM__" "${args[@]}"
'''


def old_database(path: Path) -> None:
    """Today's schema via the app's init_db, then made 'older'."""
    tmp = Path(tempfile.mkdtemp(prefix="mig-db-"))
    env = {**os.environ, "STORAGE_DIR": str(tmp), "SECRETS_DIR": str(tmp / "s"),
           "DATABASE_URL": f"sqlite:///{path.as_posix()}", "RUN_BACKGROUND_CLEANUP": "false", "GEMINI_API_KEY": ""}
    subprocess.run([sys.executable, "-c", "from app.reports.db import init_db; init_db()"], check=True, env=env,
                   cwd=REPO / "apps" / "bot-service", capture_output=True)
    db = sqlite3.connect(path)
    db.execute("delete from app_settings where key = 'upgrade:past_lines_refresh_v1'")
    db.execute("alter table intake_sessions drop column run_heartbeat_at")
    db.commit()
    db.execute("pragma wal_checkpoint(truncate)")
    db.close()
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="migration-check-test-"))
    repo = tmp / "repo"
    (repo / "infra").mkdir(parents=True)
    shutil.copytree(REPO / "apps" / "bot-service" / "app", repo / "apps" / "bot-service" / "app",
                    ignore=shutil.ignore_patterns("__pycache__"))
    script = repo / "infra" / "migration_check.sh"
    script.write_text((REPO / "infra" / "migration_check.sh").read_text(encoding="utf-8").replace("\r\n", "\n"),
                      encoding="utf-8", newline="\n")
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker_shim.py").write_text(DOCKER_SHIM, encoding="utf-8")
    py = Path(sys.executable).as_posix()
    shims = {
        "docker": DOCKER_WRAPPER.replace("__PY__", py).replace("__SHIM__", (bin_dir / "docker_shim.py").as_posix()),
        "python3": PYTHON3_SHIM.replace("__PY__", py),
        "id": '#!/bin/bash\nif [ "${1:-}" = -u ]; then echo "${MOCK_UID:-0}"; else echo root; fi\n',
    }
    for name, body in shims.items():
        (bin_dir / name).write_text(body, encoding="utf-8", newline="\n")
        (bin_dir / name).chmod(0o755)
    vols = tmp / "vols"
    vols.mkdir()
    log = tmp / "calls.log"
    copy = tmp / "backup" / "carlink-20261122-010000.db"
    copy.parent.mkdir()
    old_database(copy)
    before_bytes = copy.read_bytes()

    def run(*args, **extra):
        log.write_text("")
        env = {**os.environ, "MOCK_VOLS": str(vols), "MOCK_LOG": str(log), "SHIMS": str(bin_dir), **extra}
        snippet = ('s="$SHIMS"; command -v cygpath >/dev/null 2>&1 && s=$(cygpath -u "$s"); '
                   'export PATH="$s:$PATH"; exec bash "$0" "$@"')
        r = subprocess.run([BASH, "-c", snippet, script.as_posix(), *args], env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        return r.returncode, r.stdout + r.stderr

    print("\n1. an older copy through the staged code's init_db")
    code, out = run(copy.as_posix())
    check(code == 0 and "migration-check: PASS" in out, "PASS", out)
    check("columns added:  intake_sessions.run_heartbeat_at" in out, "names the column the release adds", out)
    check("one-time steps run: past_lines_refresh_v1" in out and "needs a database restore" in out,
          "names the one-time step it ran, with the rollback note", out)
    check("app_settings" in out and "init_db run 2 log" in out, "row counts and both runs' logs printed")
    check(copy.read_bytes() == before_bytes, "the copy given was never written")
    check(list(vols.iterdir()) == [], "the throwaway volume is gone afterwards")
    calls = log.read_text()
    runs = [line for line in calls.splitlines() if line.startswith("docker run")]
    check(len(runs) >= 6 and all("--network none" in line for line in runs), "every container ran with --network none",
          calls)
    check(":/app/app:ro" in calls, "the code is mounted read-only")
    check("carlink_api" not in calls and "infra_bot_data" not in calls, "production's container and volume never named")

    print("\n2. --ref: the release's code from git, nothing checked out")
    git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "release"], ["tag", "rel-1"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_env)
    shutil.rmtree(repo / "apps" / "bot-service" / "app" / "ai")  # the checkout is broken; the tag isn't
    code, out = run("--ref", "rel-1", copy.as_posix())
    check(code == 0 and "migration-check: PASS" in out and "code:  rel-1 = commit" in out,
          "--ref rel-1: PASS on the tagged code (the clone's own files aren't used)", out)
    check(not (repo / "apps" / "bot-service" / "app" / "ai").exists(), "nothing was checked out")
    code, out = run("--ref", "no-such-tag", copy.as_posix())
    check(code == 1 and "no commit 'no-such-tag'" in out, "an unknown ref: refused", out)
    code, out = run("--ref", "a..b")
    check(code == 1 and "is not a commit" in out, "a bad ref name: refused", out)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "."], check=True, capture_output=True)

    print("\n3. refusals")
    code, out = run(copy.as_posix(), MOCK_UID="1000")
    check(code == 1 and "run as root" in out, "not root: refused", out)
    code, out = run((tmp / "nope.db").as_posix())
    check(code == 1 and "no such file" in out, "a copy that isn't there: refused", out)
    code, out = run(CARLINK_BACKUP_DIR=(tmp / "empty").as_posix())
    check(code == 1 and "no local copy" in out, "no local copy at all: refused, with how to make one", out)
    bad = tmp / "backup" / "carlink-broken.db"
    bad.write_bytes(b"not a database" * 100)
    code, out = run(bad.as_posix())
    check(code != 0 and "PASS" not in out, "a damaged copy: not a pass", out[-400:])
    check(list(vols.iterdir()) == [], "... and its volume is removed too")

    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAIL {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
