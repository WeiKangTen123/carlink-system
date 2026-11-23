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

Review fixes (22 Nov): `docker compose` is infra/tests/fake_compose.py,
which renders the real compose files from the environment deploy.sh hands
it -- so a token, port, name or project exported in the shell is shown NOT
to reach either stack (H1, M3), and the rendered-config check refuses
production's token in staging's .env before anything starts; a failed build
puts the previous checkout back (M4); compose.sh restarts nginx after up /
restart / start (H2) and refuses production's destructive verbs (M7); a
failed prune still smokes and records (L2); restart counts from before the
deploy don't fail it (L3); the flags list hides secrets (L4); .env parsed
as compose does (L5); the backup record must be newer than the run, read
from the volume (L8); staging's smoke fails when https doesn't ask for the
password (L9) or production no longer mounts the shared folder (L14).
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
BACKUP_RAN = None
PASSED = [0]

GEMINI = "AIzaSyTESTKEYvalue-never-printed-000000"  # AIza + 35, a real key's shape
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
    # infra/tests/fake_compose.py: renders the real compose files from the
    # environment it is given (so what deploy.sh scrubbed is really gone).
    shift
    export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"
    exec "$MOCK_PY" "$MOCK_FAKE_COMPOSE" "$@" ;;
  exec)
    shift; [ "$1" = -i ] && shift; name=$1; shift
    case "$*" in
      "printenv GEMINI_API_KEY") [ "$name" = carlink_api ] && [ -n "${MOCK_PROD_GEMINI:-}" ] && { echo "$MOCK_PROD_GEMINI"; exit 0; }; exit 1 ;;
      "printenv TELEGRAM_BOT_TOKEN") [ -n "${MOCK_PROD_BOT:-}" ] && { echo "$MOCK_PROD_BOT"; exit 0; }; exit 1 ;;
      "printenv CATALOGUE_WORKER_TOKEN") exit 1 ;;
      *list-users*) echo "1 owner@example.com admin"; exit 0 ;;
      *GEMINI_SHARED_QUOTA_DIR*) exit "${MOCK_CONTAINER_SHARED:-0}" ;;
      *) exit 0 ;;
    esac ;;
  inspect)
    if [[ "$*" == *Mounts* ]]; then echo "${MOCK_PROD_MOUNTS-/app/storage /app/app /app/secrets /app/ai-shared}"; exit 0; fi
    if [[ "$*" == *State.Running* ]]; then echo "${MOCK_STATE:-true 0}"; exit 0; fi
    if [[ "$*" == *RestartCount* ]]; then echo "${MOCK_RESTARTS:-0}"; exit 0; fi
    exit 0 ;;
  volume) [ "${2:-}" = inspect ] && echo "$MOCK_VOL_DIR"; exit 0 ;;
  builder | image) exit "${MOCK_PRUNE_EXIT:-0}" ;;
  logs) [ -n "${MOCK_LOGS:-}" ] && printf '%s\n' "$MOCK_LOGS"; exit 0 ;;
  run) exit "${MOCK_NGINX_T_EXIT:-0}" ;;
  ps) echo "${MOCK_PS:-carlink_staging_api}"; exit 0 ;;
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
  https://*/login) code=${MOCK_PUBLIC_CODE:-200}; body='<html>' ;;
  https://*/robots.txt) code=200; body=${MOCK_ROBOTS-$'User-agent: *\nDisallow: /'} ;;
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
  bash) [ "${MOCK_BACKUP_EXIT:-0}" = 0 ] || exit "$MOCK_BACKUP_EXIT"; bash "$2"; exit $? ;;
  test) exit 0 ;;
  *) exec "$@" ;;
esac
''',
    "flock": '#!/bin/bash\nexit 0\n',
    "id": '#!/bin/bash\ncase "${1:-}" in -u) echo "${MOCK_UID:-1000}" ;; -un) echo tester ;; *) echo "uid=1000(tester)" ;; esac\n',
    "stat": '#!/bin/bash\necho "${MOCK_PERMS:-600}"\n',
    "df": r'''#!/bin/bash
if [[ "$*" == *-Pk* ]]; then
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/x 100000000 1000 90000000 1%% /\n'
else
    printf 'Use%%\n 20%%\n'
fi
''',
    "sleep": '#!/bin/bash\nexit 0\n',
    "python3": '#!/bin/bash\nexport MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"\nexec "$MOCK_PY" "$@"\n',
}

# The test repo's backup.sh: says which one ran, and writes the record in the
# (mock) production volume, as infra/backup.sh does.
BACKUP_SH = r'''echo __WHICH__ > "$MOCK_BACKUP_RAN"
at=${MOCK_BACKUP_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}
printf '{"at": "%s", "reports": 3, "local_path": "/root/.carlink-backup/local/carlink-x.db", "uploaded": false}' "$at" > "$MOCK_VOL_DIR/.last_backup"
'''


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
        for name in ("deploy.sh", "compose.sh", "../apps/bot-service/scripts/bench/run_bench.sh"):
            text = (REPO / "infra" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
            name = Path(name).name
            (self.infra / name).write_text(text, encoding="utf-8", newline="\n")
        self.log = self.tmp / "calls.log"
        global BACKUP_RAN
        BACKUP_RAN = self.tmp / "backup-ran"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.shared = self.tmp / "ai-shared"
        self.shared.mkdir()
        self.vol = self.tmp / "infra_bot_data"
        self.vol.mkdir()
        self.render_out = self.tmp / "rendered.json"
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
        # A's backup.sh is broken, like production's before D1 (it aimed at the
        # deleted bucket), unless MOCK_OLD_BACKUP_BROKEN=0.
        (work / "infra" / "backup.sh").write_text(
            'if [ "${MOCK_OLD_BACKUP_BROKEN:-1}" = 1 ]; then echo "old backup: NoSuchBucket" >&2; exit 1; fi\n'
            + BACKUP_SH.replace("__WHICH__", "old-backup"))
        for name in ("docker-compose.prod.yml", "docker-compose.staging.yml", "docker-compose.ai-shared.yml"):
            text = (REPO / "infra" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
            (work / "infra" / name).write_text(text, encoding="utf-8", newline="\n")
        (work / ".gitignore").write_text("infra/.env\n.deployed\n")
        self.git("add", "-A", cwd=work)
        self.git("commit", "-q", "-m", "A", cwd=work)
        self.A = self.git("rev-parse", "HEAD", cwd=work)
        self.git("push", "-q", "origin", "master", cwd=work)
        self.git("checkout", "-q", "-b", "feat/x", cwd=work)
        (work / "README").write_text("B\n")
        (work / "infra" / "backup.sh").write_text(BACKUP_SH.replace("__WHICH__", "release-backup"))
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
                    "PUBLIC_BASE_URL=https://carlink-staging.34-16-92-98.sslip.io\n"
                    f"GEMINI_API_KEY={GEMINI}\nTELEGRAM_BOT_TOKEN=\nCOMPOSE_PROFILES=\nINTAKE_LINKS=on\n"
                    "PHOTO_STORE_COMPRESS=on\n")
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
            "MOCK_BACKUP_RAN": str(BACKUP_RAN),
            "MOCK_PROD_GEMINI": GEMINI, "MOCK_PROD_BOT": PROD_BOT,
            "MOCK_PY": Path(sys.executable).as_posix(),
            "MOCK_FAKE_COMPOSE": (REPO / "infra" / "tests" / "fake_compose.py").as_posix(),
            "MOCK_VOL_DIR": str(self.vol), "MOCK_RENDER_OUT": str(self.render_out),
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

    def rendered(self) -> dict:
        """The last config the fake compose rendered (what compose would run)."""
        import json
        return json.loads(self.render_out.read_text(encoding="utf-8")) if self.render_out.exists() else {}

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
    check("> PHOTO_STORE_COMPRESS=on" in out,
          "... including the photo storage switch (PHOTO_*, owner 23 Nov)", out)
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
    check(code == 1 and "production's can't be read to compare" in out,
          "refused: a staging token when production's can't be read to compare", out)
    w.write_env("staging", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n" f"GEMINI_API_KEY={GEMINI}\n"
                "PUBLIC_BASE_URL=https://carlink-staging.34-16-92-98.sslip.io\n"
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

    # Review H1/M3: what the SHELL exported never reaches a stack -- the
    # rendered config is built from staging's infra/.env alone.
    shell = {"TELEGRAM_BOT_TOKEN": PROD_BOT, "COMPOSE_PROJECT_NAME": "infra", "CARLINK_NAME": "carlink",
             "NGINX_PORT": "8080", "PUBLIC_BASE_URL": "https://carlink.34-16-92-98.sslip.io",
             "COMPOSE_PROFILES": "telegram", "CATALOGUE_WORKER_TOKEN": "prod-worker"}
    code, out = w.run("deploy.sh", *dry, **shell)
    r = w.rendered()
    api = (r.get("services") or {}).get("api") or {}
    env = api.get("environment") or {}
    check(code == 0 and "exported in this shell are ignored" in out and no_secret(out),
          "a shell that exported production's token, project, names, port, profiles: ignored (dry run passes)", out)
    check(r.get("name") == "carlink-staging" and api.get("container_name") == "carlink_staging_api"
          and env.get("TELEGRAM_BOT_TOKEN") == "" and env.get("CATALOGUE_WORKER_TOKEN") == ""
          and env.get("PUBLIC_BASE_URL") != "https://carlink.34-16-92-98.sslip.io"
          and "telegram-bot" not in (r.get("services") or {})
          and (r["services"]["nginx"].get("ports") or [{}])[0].get("published") == "8081",
          "... the rendered config has staging's .env values: no token, carlink_staging_*, 8081, no bot", repr(r)[:400])

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
            "-f infra/docker-compose.ai-shared.yml up -d --no-build")
    check(want in calls, "compose: -p carlink-staging, the three files, up -d --no-build", calls)
    check("config --format json" in calls and " build" in calls
          and calls.rindex("config --format json") < calls.index(" build") < calls.index("up -d --no-build"),
          "the rendered config is checked, then the images built, then started", calls)
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
    check(code == 1 and "older than this backup run" in out and w.head("prod") == w.A,
          "a backup record older than this run (backup.sh wrote none): refused (review L8)", out)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", MOCK_BUILD_EXIT="1", MOCK_OLD_BACKUP_BROKEN="0")
    calls = w.calls()
    check(code == 1 and "build failed" in out and w.head("prod") == w.A and " up " not in calls,
          "a failed build: refused, the previous checkout put back, nothing started (review M4)", out)
    check(BACKUP_RAN.read_text().strip() == "old-backup" and "trying the release's" not in out,
          "a working backup.sh in the checkout is the one used")
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master")
    calls = w.calls()
    check(code == 0 and "smoke: PASS" in out and w.head("prod") == w.M, "deployed the merge commit, smoke PASS", out)
    check("sudo bash" in calls and "/backup.sh" in calls
          and calls.index("/backup.sh") < calls.index("up -d --no-build"), "backup first", calls)
    check("sudo cat" in calls and "docker exec carlink_api cat" not in calls,
          "the backup record is read from the volume, not through carlink_api (review L8)", calls)
    check(BACKUP_RAN.exists() and BACKUP_RAN.read_text().strip() == "release-backup"
          and "trying the release's" in out,
          "the checkout's backup.sh failing (pre-D1), the RELEASE's ran (taken from the commit)", out)
    check("docker compose -p infra -f infra/docker-compose.prod.yml -f infra/docker-compose.ai-shared.yml up -d --no-build" in calls,
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
    # Review M3: a shell set up for staging can't re-render production.
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--no-backup",
                      NGINX_PORT="8081", CARLINK_NAME="carlink_staging", COMPOSE_PROJECT_NAME="carlink-staging",
                      TELEGRAM_BOT_TOKEN=STAGING_BOT, GEMINI_QUOTA_LANE="staging")
    r = w.rendered()
    svcs = r.get("services") or {}
    check(code == 0 and r.get("name") == "infra"
          and sorted(b.get("container_name") for b in svcs.values()) ==
          ["carlink_api", "carlink_dashboard", "carlink_nginx", "carlink_parts_worker", "carlink_telegram_bot"]
          and svcs["nginx"]["ports"][0]["published"] == "8080"
          and svcs["api"]["environment"].get("TELEGRAM_BOT_TOKEN") == PROD_BOT
          and "GEMINI_QUOTA_LANE" not in svcs["api"]["environment"] and no_secret(out),
          "production with a staging-flavoured shell: still infra, carlink_*, 8080, its own token, prod lane", repr(r)[:300])
    w.write_env("prod", "NGINX_PORT=8081\n")
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--dry-run")
    check(code == 1 and "production is 127.0.0.1:8080" in out, "production's .env with another port: refused", out)
    w.write_env("prod", "")
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--no-backup", MOCK_PRUNE_EXIT="1")
    check(code == 0 and "prune failed (ignored)" in out and w.record("prod").get("smoke") == "pass",
          "a failed prune: warned; the smoke and the record still happen (review L2)", out)
    # A rollback to a commit from before this tooling (no shared-AI override
    # in it): production deploys it with its own window.
    work = w.tmp / "work"
    w.git("checkout", "-q", "-b", "pre-tooling", w.A, cwd=work)
    w.git("rm", "-q", "infra/docker-compose.ai-shared.yml", "infra/docker-compose.staging.yml", cwd=work)
    w.git("commit", "-q", "-m", "old", cwd=work)
    w.git("push", "-q", "origin", "pre-tooling", cwd=work)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "pre-tooling", "--fast-lane", "rollback", "--no-backup")
    calls = w.calls()
    check(code == 0 and "docker compose -p infra -f infra/docker-compose.prod.yml up -d --no-build" in calls,
          "a rollback to a commit without the shared-AI override: deployed without it", out + calls)
    code, out = w.run("deploy.sh", "--env", "prod", "--ref", "master", "--fast-lane", "back again", "--no-backup")
    check(code == 0 and w.head("prod") == w.M, "... and forward again", out)


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
    check(code == 1 and "restarts 3 (was 0 after the deploy)" in out, "a container restarted since the deploy fails the smoke", out)
    # Review L3: a container up's left alone that once restarted is fine...
    code, out = w.run("deploy.sh", "--env", "staging", "--ref", "feat/x", MOCK_RESTARTS="3", MOCK_STATE="true 3")
    check(code == 0 and w.record("staging").get("restarts", "").count("=3") >= 3,
          "restart counts from before the deploy don't fail it (the baseline is kept in .deployed)", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only", MOCK_STATE="true 4")
    check(code == 1 and "restarts 4 (was 3 after the deploy)" in out, "... a restart after it does", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only")
    check(code == 0 and "/login 200 over https; its robots.txt keeps search engines out" in out,
          "the public address reaching staging's sign-in page, no pop-up (owner, 23 Nov): passes", out)
    calls = w.calls()
    check("curl https://carlink-staging.34-16-92-98.sslip.io/login" in calls
          and "curl https://carlink-staging.34-16-92-98.sslip.io/robots.txt" in calls,
          "... checked on the public https address: /login and robots.txt", calls)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only", MOCK_PUBLIC_CODE="502")
    check(code == 1 and "/login answered '502'" in out and "don't use staging until it is" in out,
          "the public address not reaching the sign-in page fails the staging smoke (review L9)", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only", MOCK_ROBOTS="")
    check(code == 1 and "don't use staging until it is" in out,
          "no robots.txt that keeps search engines out fails it too (not through the staging host site)", out)
    code, out = w.run("deploy.sh", "--env", "staging", "--smoke-only", MOCK_PROD_MOUNTS="/app/storage /app/app")
    check(code == 1 and "doesn't mount the shared AI folder" in out,
          "production no longer mounting the shared folder fails the staging smoke (review L14)", out)
    w.run("deploy.sh", "--env", "staging", "--ref", "feat/x")


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
    # Review H2: up / restart / start restart nginx afterwards (new addresses).
    for env_name, args in (("prod", ["up", "-d"]), ("staging", ["restart", "api"]), ("prod", ["start"])):
        code, out = w.run("compose.sh", env_name, *args)
        calls = w.calls()
        nginx = "carlink_nginx" if env_name == "prod" else "carlink_staging_nginx"
        check(code == 0 and "config --format json" in calls and f"docker restart {nginx}" in calls
              and calls.index(f"{' '.join(args)}") < calls.index(f"docker restart {nginx}"),
              f"{env_name} {' '.join(args)}: rendered config checked, then nginx restarted after it", calls)
    code, out = w.run("compose.sh", "prod", "logs", "api")
    check(code == 0 and "docker restart" not in w.calls(), "prod logs: nothing restarted")
    # Review M7: production's live data is one `down -v` away.
    for args in (["down", "-v"], ["down"], ["rm", "-f"], ["kill"], ["stop"], ["volume", "ls"], ["build"],
                 ["up", "-d", "--build"], ["up", "-V"], ["logs", "-v"]):
        code, out = w.run("compose.sh", "prod", *args)
        check(code == 1 and "not allowed on production" in out and "docker compose -p infra" not in w.calls(),
              f"prod {' '.join(args)}: refused, compose not run", out)
    code, out = w.run("compose.sh", "prod", "exec", "api", "ls")
    check(code == 0 and "exec api ls" in w.calls(), "prod exec: allowed")
    code, out = w.run("compose.sh", "staging", "down", "-v")
    check(code == 0 and "down -v" in w.calls(), "staging down -v: allowed (staging's own volumes)")
    w.write_env("staging", "CARLINK_NAME=carlink_staging\nNGINX_PORT=8081\n" f"GEMINI_API_KEY={GEMINI}\n"
                f"TELEGRAM_BOT_TOKEN={PROD_BOT}\nCOMPOSE_PROFILES=telegram\n")
    code, out = w.run("compose.sh", "staging", "up", "-d")
    check(code == 1 and "is production's" in out and " up -d" not in w.calls() and no_secret(out),
          "staging up with production's token in its .env: refused before anything starts (review H1)", out)
    w.write_env("staging")


def env_parsing(w: World) -> None:
    print("\n9. .env read as compose reads it; the flags list hides secrets (review L4, L5)")
    w.write_env("staging", "COMPOSE_PROJECT_NAME=carlink-staging\nCARLINK_NAME=carlink_staging\n"
                "PUBLIC_BASE_URL='https://carlink-staging.34-16-92-98.sslip.io'\n"
                "export NGINX_PORT=8081   # staging's port\n"
                f"GEMINI_API_KEY=\"{GEMINI}\"\nTELEGRAM_BOT_TOKEN=\nINTAKE_LINKS=on\n"
                f"GEMINI_SPARE_KEY={GEMINI}\nINTAKE_NOTE=uses {GEMINI} here\n")
    code, out = w.run("deploy.sh", "--env", "staging", "--ref", "feat/x", "--dry-run")
    check(code == 0 and "127.0.0.1:8081" in out, "export, spaces, quotes and a # comment parse as compose does", out)
    check("GEMINI_SPARE_KEY" not in out and GEMINI not in out and "INTAKE_NOTE=uses <key> here" in out,
          "the flags list leaves out *KEY* names and masks a key-shaped value", out)
    w.write_env("staging")


def bench_lane(w: World) -> None:
    print("\n8. run_bench.sh runs in the staging lane")
    # A benchmark harness in the throwaway repo, so run_bench gets to the run.
    work = w.tmp / "work"
    w.git("checkout", "-q", "feat/x", cwd=work)
    target = work / "apps" / "bot-service" / "scripts"
    target.mkdir(parents=True, exist_ok=True)
    (target / "benchmark_intake.py").write_text("# --sort-cache\n")
    w.git("add", "-A", cwd=work)
    w.git("commit", "-q", "-m", "bench harness", cwd=work)
    w.git("push", "-q", "origin", "feat/x", cwd=work)
    bench = {"BENCH_REPO": str(w.staging)}  # the container: the default (review M5: staging's)
    code, out = w.run("run_bench.sh", "lane-test", "feat/x", BENCH_LANE="fast", **bench)
    check(code == 1 and "BENCH_LANE: staging (the default) or prod" in out, "an unknown lane: refused", out)
    code, out = w.run("run_bench.sh", "lane-test", "feat/x", MOCK_CONTAINER_SHARED="1", **bench)
    check(code == 1 and "no shared AI quota folder" in out, "the staging lane without the shared folder: refused", out)
    code, out = w.run("run_bench.sh", "lane-test", "feat/x", **bench)
    calls = w.calls()
    check("AI lane staging" in out and "-e GEMINI_QUOTA_LANE=staging" in calls and "carlink_staging_api" in calls,
          "by default the run is in the staging lane, in the named container", out + calls)
    check("BENCHMARK_SCRATCH=1" in calls and "run.db" in calls and "/tmp/bench/lane-test" in calls,
          "on its scratch database under /tmp/bench (sandbox work), never the stack's own", calls)
    check("docker exec" in calls and "carlink_api" not in calls, "the default container is staging's, never production's")
    # Review M5: production's lane where the guard is on, and production's
    # container, only with --allow-prod-bench "<why>".
    code, out = w.run("run_bench.sh", "lane-test", "feat/x", BENCH_LANE="prod", **bench)
    check(code == 1 and "takes production's share" in out and "GEMINI_QUOTA_LANE" not in w.calls(),
          "BENCH_LANE=prod where the shared guard is on: refused", out)
    code, out = w.run("run_bench.sh", "--allow-prod-bench", "compare with the old numbers", "lane-test", "feat/x",
                      BENCH_LANE="prod", **bench)
    check("-e GEMINI_QUOTA_LANE=prod" in w.calls() and "--allow-prod-bench: compare with the old numbers" in out,
          "... allowed with --allow-prod-bench and a reason (said in the output)", out)
    code, out = w.run("run_bench.sh", "lane-test", "feat/x", BENCH_CONTAINER="carlink_api", MOCK_PS="carlink_api", **bench)
    check(code == 1 and "PRODUCTION's container" in out, "production's container: refused", out)
    code, out = w.run("run_bench.sh", "--allow-prod-bench", "  ", "lane-test", "feat/x", **bench)
    check(code == 1 and "needs the reason" in out, "--allow-prod-bench without a reason: refused", out)
    code, out = w.run("run_bench.sh", "lane-test", "feat/x", BENCH_LANE="prod", MOCK_CONTAINER_SHARED="1", **bench)
    check("-e GEMINI_QUOTA_LANE=prod" in w.calls(), "BENCH_LANE=prod in a container without the guard: as before",
          out + w.calls())


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
    for section in (options, staging_refusals, staging_deploy, prod_checks, prod_deploy, smoke_failures, compose_wrapper,
                    env_parsing, bench_lane):
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
