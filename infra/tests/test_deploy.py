#!/usr/bin/env python3
"""infra/deploy.sh: its argument checks, refusals and a whole mocked deploy
of each stack -- without Docker, sudo or the network.

    python infra/tests/test_deploy.py

Real git (a throwaway "origin" with three commits, and a production clone
and a staging clone of it); docker, sudo, curl, flock, id, stat and df are
small shims (in a temp bin folder) that log their calls and answer as the
case needs. Checked: every option refusal; staging can't run as project
"infra", with production's names, port or clone, without its .env (0600),
key, the shared AI folder or (unless --allow-unshared-prod) production
mounting it, or with production's Telegram / worker token -- and no token
or key ever appears in the output; the ref is resolved and checked out
detached; the production tree check (staged files and smoke) and the fast
lane; production's backup (must work, must be fresh) and secrets; the
compose commands of each stack; smoke failures (a Traceback, a token in a
log) give exit 2 and a record that blocks production until --smoke-only
passes; --compose (infra/compose.sh). Also bash -n, and shellcheck -S error
when it is installed (CI has it).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FAILURES: list[str] = []
PASSED = [0]

GEMINI = "AIzaSyTESTKEYvalue-never-printed-0000000"
PROD_BOT = "1234567890:AA" + "p" * 33
STAGING_BOT = "9876543210:AA" + "s" * 33
assert re.fullmatch(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}", PROD_BOT) and re.fullmatch(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}", STAGING_BOT)


def check(cond, label: str, detail: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append(label)
        print(f"    FAIL {label}" + (f"\n         {detail[-1500:]}" if detail else ""))
    return bool(cond)


SHIMS = {
    "docker": r'''#!/bin/bash
echo "docker $*" >> "$MOCK_LOG"
case "$1" in
  compose)
    if [ "${2:-}" = version ]; then echo "Docker Compose version v2.30.3"; exit 0; fi
    for a in "$@"; do [ "$a" = up ] && exit "${MOCK_UP_EXIT:-0}"; done
    exit 0 ;;
  exec)
    shift; [ "$1" = -i ] && shift; name=$1; shift
    case "$*" in
      "printenv GEMINI_API_KEY") [ "$name" = carlink_api ] && [ -n "${MOCK_PROD_GEMINI:-}" ] && { echo "$MOCK_PROD_GEMINI"; exit 0; }; exit 1 ;;
      "printenv TELEGRAM_BOT_TOKEN") [ -n "${MOCK_PROD_BOT:-}" ] && { echo "$MOCK_PROD_BOT"; exit 0; }; exit 1 ;;
      "printenv CATALOGUE_WORKER_TOKEN") exit 1 ;;
      "cat /app/storage/.last_backup")
        printf '{"at": "%s", "reports": 3, "local_path": "/root/.carlink-backup/local/carlink-x.db", "uploaded": false, "bucket": null}' \
          "${MOCK_BACKUP_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"; exit 0 ;;
      *list-users*) echo "1 owner@example.com admin"; exit 0 ;;
      *) exit 0 ;;
    esac ;;
  inspect)
    if [[ "$*" == *Mounts* ]]; then echo "${MOCK_PROD_MOUNTS-/app/storage /app/app /app/secrets /app/ai-shared}"; exit 0; fi
    if [[ "$*" == *RestartCount* ]]; then echo "${MOCK_STATE:-true 0}"; exit 0; fi
    exit 0 ;;
  logs) [ -n "${MOCK_LOGS:-}" ] && printf '%s\n' "$MOCK_LOGS"; exit 0 ;;
  run) exit "${MOCK_NGINX_T_EXIT:-0}" ;;
  *) exit 0 ;;
esac
''',
    "curl": r'''#!/bin/bash
url=; code_only=0
for a in "$@"; do case "$a" in http*) url=$a ;; '%{http_code}') code_only=1 ;; esac; done
echo "curl $url" >> "$MOCK_LOG"
case "$url" in
  */api/health) code=200; body='{"status":"ok"}' ;;
  */api/auth/me | */api/reports | */api/system/info) code=401; body='{"detail":"Not signed in."}' ;;
  */api/catalogue-worker/claim) code=404; body= ;;
  */login) code=200; body='<html>' ;;
  */api/auth/signup) code=200; body=${MOCK_SIGNUP:-'{"open":false}'} ;;
  https://*) code=${MOCK_PUBLIC_CODE:-401}; body= ;;
  *) code=000; body= ;;
esac
if [ "$code_only" = 1 ]; then printf '%s' "$code"; else printf '%s' "$body"; fi
''',
    "sudo": r'''#!/bin/bash
echo "sudo $*" >> "$MOCK_LOG"
case "$1" in
  bash) exit "${MOCK_BACKUP_EXIT:-0}" ;;
  test) exit 0 ;;
  *) exec "$@" ;;
esac
''',
    "flock": '#!/bin/bash\nexit 0\n',
    "id": '#!/bin/bash\ncase "${1:-}" in -u) echo "${MOCK_UID:-1000}" ;; -un) echo tester ;; *) echo "uid=1000(tester)" ;; esac\n',
    "stat": '#!/bin/bash\necho "${MOCK_PERMS:-600}"\n',
    "df": '#!/bin/bash\nprintf "Use%%\\n 20%%\\n"\n',
    "sleep": '#!/bin/bash\nexit 0\n',
}


class World:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="deploy-test-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in SHIMS.items():
            p = self.bin / name
            p.write_text(body, encoding="utf-8", newline="\n")
            p.chmod(0o755)
        self.infra = self.tmp / "script"
        self.infra.mkdir()
        for name in ("deploy.sh", "compose.sh"):
            text = (REPO / "infra" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
            (self.infra / name).write_text(text, encoding="utf-8", newline="\n")
        self.log = self.tmp / "calls.log"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.shared = self.tmp / "ai-shared"
        self.shared.mkdir()
        self._make_repo()

    def git(self, *args, cwd=None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.tmp, check=True, capture_output=True, text=True,
                              env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}).stdout.strip()

    def _make_repo(self):
        origin = self.tmp / "carlink-system.git"
        self.git("init", "-q", "--bare", str(origin))
        work = self.tmp / "work"
        self.git("clone", "-q", str(origin), str(work))
        self.git("checkout", "-q", "-b", "master", cwd=work)
        (work / "infra").mkdir()
        (work / "infra" / "nginx.conf").write_text("server {}\n")
        (work / ".gitignore").write_text("infra/.env\n.deployed\n")
        self.git("add", "-A", cwd=work)
        self.git("commit", "-q", "-m", "A", cwd=work)
        self.A = self.git("rev-parse", "HEAD", cwd=work)
        self.git("push", "-q", "origin", "master", cwd=work)
        self.git("checkout", "-q", "-b", "feat/x", cwd=work)
        (work / "README").write_text("B\n")
        self.git("add", "-A", cwd=work)
        self.git("commit", "-q", "-m", "B", cwd=work)
        self.B = self.git("rev-parse", "HEAD", cwd=work)
        self.git("push", "-q", "origin", "feat/x", cwd=work)
        # The merge commit M: another commit, exactly B's files.
        self.M = self.git("commit-tree", f"{self.B}^{{tree}}", "-p", self.A, "-p", self.B, "-m", "merge", cwd=work)
        self.git("push", "-q", "origin", f"{self.M}:refs/heads/master", cwd=work)
        self.tree_B = self.git("rev-parse", f"{self.B}^{{tree}}", cwd=work)
        self.prod = self.tmp / "carlink-system"
        self.staging = self.tmp / "carlink-staging"
        self.git("clone", "-q", str(origin), str(self.prod))
        self.git("-C", str(self.prod), "checkout", "-q", "--detach", self.A)
        self.git("clone", "-q", str(origin), str(self.staging))
        self.git("-C", str(self.staging), "checkout", "-q", "--detach", self.A)
        self.write_env("staging")
        self.write_env("prod", "")

    def write_env(self, which: str, text: str | None = None) -> None:
        if text is None:
            text = ("COMPOSE_PROJECT_NAME=carlink-staging\nCARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n"
                    f"GEMINI_API_KEY={GEMINI}\nTELEGRAM_BOT_TOKEN=\nCOMPOSE_PROFILES=\nINTAKE_LINKS=on\n")
        (self.staging if which == "staging" else self.prod).joinpath("infra", ".env").write_text(text, encoding="utf-8")

    def run(self, script: str, *args, **mock) -> tuple[int, str]:
        self.log.write_text("")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CARLINK_", "MOCK_", "COMPOSE_"))
               and k not in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN")}
        env.update({
            "HOME": str(self.home), "MOCK_LOG": str(self.log), "SHIMS": str(self.bin),
            "CARLINK_PROD_DIR": str(self.prod), "CARLINK_STAGING_DIR": str(self.staging),
            "CARLINK_AI_SHARED_DIR": str(self.shared), "CARLINK_DEPLOY_LOG": str(self.home / "carlink-deploy.log"),
            "CARLINK_SMOKE_WAIT": "0", "CARLINK_SMOKE_SETTLE": "0", "CARLINK_REPO_SLUG": "carlink-system.git",
            "MOCK_PROD_GEMINI": GEMINI, "MOCK_PROD_BOT": PROD_BOT,
        })
        for k, v in mock.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        snippet = ('s="$SHIMS"; command -v cygpath >/dev/null 2>&1 && s=$(cygpath -u "$s"); '
                   'export PATH="$s:$PATH"; exec bash "$0" "$@"')
        r = subprocess.run([BASH, "-c", snippet, str(self.infra / script), *args], env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        return r.returncode, r.stdout + r.stderr

    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8")

    def head(self, which: str) -> str:
        return self.git("-C", str(self.prod if which == "prod" else self.staging), "rev-parse", "HEAD")

    def record(self, which: str) -> dict:
        p = (self.prod if which == "prod" else self.staging) / ".deployed"
        if not p.exists():
            return {}
        return dict(line.split("=", 1) for line in p.read_text().splitlines() if "=" in line and not line.startswith("#"))


def no_secret(out: str) -> bool:
    return GEMINI not in out and PROD_BOT not in out and STAGING_BOT not in out


def options(w: World) -> None:
    print("\n1. options")
    code, out = w.run("deploy.sh")
    check(code == 2 and "--env staging --ref" in out, "no arguments: the usage, exit 2", out)
    for args, words in (
        (["--env", "bogus", "--ref", "x"], "must be prod or staging"),
        (["--env", "staging"], "--ref <commit|branch|tag> is required"),
        (["--ref", "feat/x"], "--env prod|staging is required"),
        (["--env", "staging", "--ref", "feat/x", "--yes"], "unknown option: --yes"),
        (["--env", "staging", "--ref", "feat/x", "--fast-lane", "why"], "--fast-lane is for production"),
        (["--env", "prod", "--ref", "x", "--fast-lane", "  "], "needs the reason"),
        (["--env", "prod", "--ref", "x", "--fast-lane"], "needs the reason"),
        (["--env", "staging", "--ref", "feat/x", "--no-backup"], "--no-backup is for production"),
        (["--env", "prod", "--ref", "x", "--allow-unshared-prod"], "--allow-unshared-prod is for staging"),
        (["--env", "staging", "--ref", "a..b"], "is not a commit, branch or tag name"),
        (["--env", "staging", "--ref", "x;rm"], "is not a commit, branch or tag name"),
        (["--env", "staging", "--smoke-only", "--ref", "x"], "--ref is for a deploy"),
        (["--env", "staging", "--smoke-only", "--compose", "ps"], "don't go together"),
    ):
        code, out = w.run("deploy.sh", *args)
        check(code == 1 and words in out, f"refused: {' '.join(args)}", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--ref", "feat/x", MOCK_UID="0")
    check(code == 1 and "not root" in out, "refused as root", out)
    check("checkout" not in w.calls() and w.head("staging") == w.A, "  ... nothing ran, nothing checked out")


def staging_refusals(w: World) -> None:
    print("\n2. staging refusals (before any change)")
    dry = ["--env", "staging", "--ref", "feat/x", "--dry-run"]
    code, out = w.run("deploy.sh", *dry)
    check(code == 0 and "dry run: every check passed" in out and w.head("staging") == w.A,
          "a good staging .env passes the dry run; nothing checked out", out)
    check("flags that differ from production" in out and "> INTAKE_LINKS=on" in out,
          "the dry run lists the flags that differ from production's (gate D)", out)
    check("-p carlink-staging -f infra/docker-compose.prod.yml -f infra/docker-compose.staging.yml "
          "-f infra/docker-compose.ai-shared.yml" in out, "... and names staging's project and three files", out)
    cases = [
        ("no .env", None, {}, "no "),
        ("COMPOSE_PROJECT_NAME=infra", "COMPOSE_PROJECT_NAME=infra\nCARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n"
         f"GEMINI_API_KEY={GEMINI}\n", {}, "would be 'infra'"),
        ("CARLINK_NAME=carlink", "CARLINK_NAME=carlink\nNGINX_PORT=8081\n" f"GEMINI_API_KEY={GEMINI}\n", {},
         "CARLINK_NAME must be carlink_staging"),
        ("no CARLINK_NAME", "NGINX_PORT=8081\n" f"GEMINI_API_KEY={GEMINI}\n", {}, "CARLINK_NAME must be"),
        ("NGINX_PORT=8080", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8080\n" f"GEMINI_API_KEY={GEMINI}\n", {},
         "NGINX_PORT must be set, and not 8080"),
        ("no key", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8081\nGEMINI_API_KEY=\n", {}, "GEMINI_API_KEY is empty"),
        ("mode 644", None, {"MOCK_PERMS": "644"}, "chmod 600"),
        ("the shell says COMPOSE_PROJECT_NAME=infra", None, {"COMPOSE_PROJECT_NAME": "infra"}, "would be 'infra'"),
        ("telegram profile, no token", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n"
         f"GEMINI_API_KEY={GEMINI}\nCOMPOSE_PROFILES=telegram\n", {}, "TELEGRAM_BOT_TOKEN is empty"),
    ]
    for label, text, mock, words in cases:
        env_file = w.staging / "infra" / ".env"
        if label == "no .env":
            env_file.unlink()
        elif text is not None:
            w.write_env("staging", text)
        code, out = w.run("deploy.sh", *dry, **mock)
        check(code == 1 and words in out, f"refused: {label}", out)
        w.write_env("staging")

    w.write_env("staging", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n" f"GEMINI_API_KEY={GEMINI}\n"
                f"TELEGRAM_BOT_TOKEN={PROD_BOT}\nCOMPOSE_PROFILES=telegram\n")
    code, out = w.run("deploy.sh", *dry)
    check(code == 1 and "is production's" in out and no_secret(out),
          "refused: staging with production's Telegram token (compared by hash; neither shown)", out)
    code, out = w.run("deploy.sh", *dry, MOCK_PROD_BOT=None)
    check(code == 1 and "can't read production's TELEGRAM_BOT_TOKEN" in out,
          "refused: a staging token when production's can't be read to compare", out)
    w.write_env("staging", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n" f"GEMINI_API_KEY={GEMINI}\n"
                f"TELEGRAM_BOT_TOKEN={STAGING_BOT}\nCOMPOSE_PROFILES=telegram\n")
    code, out = w.run("deploy.sh", *dry)
    check(code == 0 and no_secret(out), "a separate test bot's token passes (not shown)", out)
    w.write_env("staging", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n" f"GEMINI_API_KEY={GEMINI}\n"
                "CATALOGUE_WORKER_TOKEN=same-worker-token\n")
    w.write_env("prod", "CATALOGUE_WORKER_TOKEN=same-worker-token\n")
    code, out = w.run("deploy.sh", *dry)
    check(code == 1 and "CATALOGUE_WORKER_TOKEN is production's" in out and "same-worker-token" not in out,
          "refused: production's parts-worker token", out)
    w.write_env("prod", "")
    w.write_env("staging")

    shutil.rmtree(w.shared)
    code, out = w.run("deploy.sh", *dry)
    check(code == 1 and "no shared AI quota folder" in out, "refused: no shared AI quota folder", out)
    w.shared.mkdir()
    code, out = w.run("deploy.sh", *dry, MOCK_PROD_MOUNTS="/app/storage /app/app")
    check(code == 1 and "doesn't mount the shared AI folder" in out, "refused: production doesn't mount it", out)
    code, out = w.run("deploy.sh", *dry, "--allow-unshared-prod", MOCK_PROD_MOUNTS="/app/storage /app/app")
    check(code == 0 and "--allow-unshared-prod" in out, "--allow-unshared-prod: allowed, with a warning", out)

    code, out = w.run("deploy.sh", *dry, CARLINK_STAGING_DIR=str(w.prod))
    check(code == 1 and "its own clone" in out, "refused: staging in production's clone", out)
    (w.staging / "infra" / "nginx.conf").write_text("server { edited }\n")
    code, out = w.run("deploy.sh", *dry)
    check(code == 1 and "local edits" in out, "refused: a clone with local edits", out)
    w.git("-C", str(w.staging), "checkout", "-q", "--", "infra/nginx.conf")
    code, out = w.run("deploy.sh", "--env", "staging", "--ref", "no-such-branch", "--dry-run")
    check(code == 1 and "no commit 'no-such-branch'" in out, "refused: a ref that isn't there", out)
    code, out = w.run("deploy.sh", *dry, CARLINK_REPO_SLUG="someone/else")
    check(code == 1 and "origin is" in out, "refused: a clone of another repository", out)


def staging_deploy(w: World) -> None:
    print("\n3. a staging deploy (mocked docker)")
    code, out = w.run("deploy.sh", "--env", "staging", "--ref", "feat/x")
    calls = w.calls()
    check(code == 0 and "smoke: PASS" in out, "deployed, smoke PASS", out)
    check(w.head("staging") == w.B, "feat/x checked out in the staging clone")
    detached = subprocess.run(["git", "-C", str(w.staging), "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode
    check(detached != 0, "HEAD is detached (no branch to pull)")
    want = ("docker compose -p carlink-staging -f infra/docker-compose.prod.yml -f infra/docker-compose.staging.yml "
            "-f infra/docker-compose.ai-shared.yml up -d --build")
    check(want in calls, "compose: -p carlink-staging, the three files, up -d --build", calls)
    check("config -q" in calls and calls.index("config -q") < calls.index("up -d --build"),
          "the files are rendered (config -q) before anything starts")
    check("sudo" not in calls, "no backup, no sudo for staging", calls)
    check("--network carlink-staging_default" in calls and "docker restart carlink_staging_nginx" in calls,
          "nginx -t on staging's network, then staging's nginx restarted")
    check("seed_staging.py --close-signups-only" in calls, "signups closed on every staging deploy")
    check("127.0.0.1:8081/api/health" in calls and "127.0.0.1:8080" not in calls, "smoke checks on 8081 only", calls)
    check("infra" not in re.findall(r"-p (\S+)", calls), "never -p infra")
    rec = w.record("staging")
    check(rec.get("commit") == w.B and rec.get("tree") == w.tree_B and rec.get("smoke") == "pass"
          and rec.get("previous") == w.A, ".deployed: commit, tree, smoke pass, previous", repr(rec))
    log = (w.home / "carlink-deploy.log").read_text()
    check(f"env=staging commit={w.B}" in log and "smoke=pass" in log, "a line in the deploy log", log)
    check(no_secret(out), "no key or token in the output")


def prod_checks(w: World) -> None:
    print("\n4. production: tree check, fast lane, secrets")
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 0 and "tree check: the same files as staging's" in out,
          "the merge commit (master) has staging's files: passes", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", w.A, "--dry-run")
    check(code == 1 and "tree check" in out, "a commit with other files: refused", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", w.A, "--dry-run", "--fast-lane", "urgent typo fix")
    check(code == 0 and "FAST LANE" in out and "urgent typo fix" in out, "--fast-lane skips it, loudly", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run", MOCK_PROD_GEMINI=None)
    check(code == 1 and "GEMINI_API_KEY is not set" in out, "no Gemini key anywhere: refused", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run", MOCK_PROD_BOT=None)
    check(code == 1 and "TELEGRAM_BOT_TOKEN is not set" in out, "no Telegram token anywhere: refused (prod demands it)", out)
    w.write_env("prod", "CARLINK_NAME=carlink_staging\n")
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 1 and "production's containers are carlink_*" in out, "production's .env naming other containers: refused", out)
    w.write_env("prod", "COMPOSE_PROJECT_NAME=carlink-staging\n")
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 1 and "production is 'infra'" in out, "production's .env naming another project: refused", out)
    w.write_env("prod", "")
    staged = w.staging / ".deployed"
    saved = staged.read_text()
    staged.unlink()
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 1 and "staging has no deploy record" in out, "nothing staged: refused", out)
    staged.write_text(saved)


def prod_deploy(w: World) -> None:
    print("\n5. a production deploy (mocked docker, sudo)")
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", MOCK_BACKUP_EXIT="1")
    check(code == 1 and "backup failed -- not deploying" in out and w.head("prod") == w.A,
          "backup fails: refused, nothing checked out", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", MOCK_BACKUP_AT="2026-01-01T00:00:00Z")
    check(code == 1 and "not fresh" in out and w.head("prod") == w.A, "a stale backup record: refused", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master")
    calls = w.calls()
    check(code == 0 and "smoke: PASS" in out and w.head("prod") == w.M, "deployed the merge commit, smoke PASS", out)
    check("sudo bash" in calls and "infra/backup.sh" in calls
          and calls.index("infra/backup.sh") < calls.index("up -d --build"), "backup first", calls)
    check("docker compose -p infra -f infra/docker-compose.prod.yml -f infra/docker-compose.ai-shared.yml up -d --build" in calls,
          "compose: -p infra, production's file + the shared AI folder (it exists)", calls)
    check("docker restart carlink_nginx" in calls and "127.0.0.1:8080/api/health" in calls, "carlink_nginx, port 8080")
    check(no_secret(out), "no key or token in the output (they came from the running container)")
    rec = w.record("prod")
    check(rec.get("commit") == w.M and rec.get("backup", "").endswith("carlink-x.db"), "production's .deployed", repr(rec))
    shutil.rmtree(w.shared)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 0 and "-f infra/docker-compose.ai-shared.yml" not in out and "keeps its own AI window" in out,
          "without the folder production's compose has no shared AI override (as before)", out)
    w.shared.mkdir()
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--no-backup")
    check(code == 0 and "WITHOUT A BACKUP" in out and "sudo bash" not in w.calls(), "--no-backup: loud, no backup run", out)


def smoke_failures(w: World) -> None:
    print("\n6. smoke failures block production")
    code, out = w.run("deploy.sh", "--env", "staging", "--ref", "feat/x",
                      MOCK_LOGS="Traceback (most recent call last):\nERROR something broke")
    check(code == 2 and "smoke: FAIL" in out and w.record("staging").get("smoke") == "fail",
          "a Traceback in the logs: exit 2, recorded smoke=fail", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 1 and "smoke checks say 'fail'" in out, "production refuses what failed its smoke", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only")
    check(code == 0 and w.record("staging").get("smoke") == "pass", "--smoke-only passing clears it", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 0, "... and production may go", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only", MOCK_LOGS=f"sending to bot{STAGING_BOT} ok")
    check(code == 1 and "a Telegram token is in its log" in out and STAGING_BOT not in out,
          "a token in a log fails the smoke, never shown", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only", MOCK_SIGNUP='{"open":true}')
    check(code == 1 and "signups are not closed" in out, "staging with signups open fails the smoke", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only", MOCK_STATE="true 3")
    check(code == 1 and "want running, 0 restarts" in out, "a restarting container fails the smoke", out)
    w.run("deploy.sh", "--env", "staging", "--smoke-only")


def compose_wrapper(w: World) -> None:
    print("\n7. infra/compose.sh")
    code, out = w.run("compose.sh", "staging", "logs", "--tail", "5", "api")
    calls = w.calls()
    check(code == 0 and "docker compose -p carlink-staging -f infra/docker-compose.prod.yml "
          "-f infra/docker-compose.staging.yml -f infra/docker-compose.ai-shared.yml logs --tail 5 api" in calls,
          "staging: the project and files, then the arguments as given", calls)
    code, out = w.run("compose.sh", "prod", "ps")
    check(code == 0 and "docker compose -p infra -f infra/docker-compose.prod.yml" in w.calls() and no_secret(out),
          "prod: -p infra (secrets from the running api, not shown)", w.calls())
    code, out = w.run("compose.sh")
    check(code == 2, "no arguments: the usage")


def static_checks() -> None:
    print("\n0. bash -n, shellcheck")
    for name in ("deploy.sh", "compose.sh", "migration_check.sh"):
        path = REPO / "infra" / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        r = subprocess.run([BASH, "-n"], input=text, capture_output=True, text=True)
        check(r.returncode == 0, f"bash -n {name}", r.stderr)
        if shutil.which("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "error", "-s", "bash", "-"], input=text, capture_output=True, text=True)
            check(r.returncode == 0, f"shellcheck -S error {name}", r.stdout)
    if not shutil.which("shellcheck"):
        print("    skip shellcheck (not installed here; CI's ubuntu runner has it)")


def find_bash() -> str:
    override = os.environ.get("CARLINK_TEST_BASH")
    if override:
        return override
    if sys.platform == "win32":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash") or "bash"


BASH = find_bash()


def main() -> int:
    static_checks()
    w = World()
    for section in (options, staging_refusals, staging_deploy, prod_checks, prod_deploy, smoke_failures, compose_wrapper):
        try:
            section(w)
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
