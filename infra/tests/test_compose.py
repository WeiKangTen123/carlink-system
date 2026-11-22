#!/usr/bin/env python3
"""Production's compose comes out the same; the staging stack cannot touch it.

    python infra/tests/test_compose.py      (needs PyYAML)

1. PRODUCTION UNCHANGED. infra/docker-compose.prod.yml rendered with the
   production env defaults (compose_render.py: GEMINI_API_KEY and
   TELEGRAM_BOT_TOKEN set, every other variable at its default) is compared
   with prod-compose.render.json, the render of master's file (5ee5e03) taken
   before the staging work. Only the differences in EXPECTED_PROD_CHANGES may
   appear, each with its reason. With PUBLIC_BASE_URL set (as a server's
   infra/.env may set it), there must be none at all. A deliberate change to
   production's compose updates the snapshot (or this list) in the same pull
   request, so the reviewer sees exactly what production will get.
2. THE SHARED AI FOLDER OVERRIDE (docker-compose.ai-shared.yml) on top of
   production adds the folder and one variable to api and telegram-bot,
   and nothing else.
3. THE STAGING STACK (prod file + docker-compose.staging.yml +
   docker-compose.ai-shared.yml, staging.env.example) shares no project,
   container name, volume, network or host port with production; every
   service is capped and killed first; the bot and the parts worker are off
   unless asked; the AI runs in the staging lane; and it refuses to render
   at all without CARLINK_NAME.

Offline, no Docker, no real .env: only the repo's files and placeholders.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compose_render as cr  # noqa: E402

REPO = cr.REPO
FAILURES: list[str] = []
PASSED = [0]

# Every difference production's render may show against the 5ee5e03 snapshot,
# and why. Nothing else may differ.
EXPECTED_PROD_CHANGES = {
    # The default was the deleted old server (34.45.253.162, gone 2026-11-04).
    # A server whose infra/.env sets PUBLIC_BASE_URL sees no change at all.
    "services.api.environment.PUBLIC_BASE_URL": "default moved to the live address",
    "services.telegram-bot.environment.PUBLIC_BASE_URL": "default moved to the live address",
}
LIVE_URL = "https://carlink.34-16-92-98.sslip.io"


def check(cond, label: str, detail: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append(label)
        print(f"    FAIL {label}" + (f"\n         {detail}" if detail else ""))
    return bool(cond)


def text(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def render(files: list[str], env: dict, project=None) -> dict:
    return cr.render([(f, text(f)) for f in files], env, project)


def prod_unchanged() -> None:
    print("\n1. production's compose, before vs after")
    snapshot = json.loads((HERE / "prod-compose.render.json").read_text(encoding="utf-8"))
    env = dict(cr.PROD_ENV_DEFAULTS)
    after = render([cr.PROD_FILE], env)
    found = cr.diff(snapshot, after)
    unexpected = [d for d in found if d[0] not in EXPECTED_PROD_CHANGES]
    check(not unexpected, "with the prod env defaults, nothing differs but the expected PUBLIC_BASE_URL default",
          "; ".join(f"{p}: {b!r} -> {a!r}" for p, b, a in unexpected))
    for path in EXPECTED_PROD_CHANGES:
        got = dict((p, a) for p, _, a in found).get(path)
        check(got == LIVE_URL, f"{path} defaults to the live address", repr(got))

    names = {svc: body["container_name"] for svc, body in after["services"].items()}
    check(names == {"api": "carlink_api", "dashboard": "carlink_dashboard", "nginx": "carlink_nginx",
                    "parts-worker": "carlink_parts_worker", "telegram-bot": "carlink_telegram_bot"},
          "the five production container names are unchanged", repr(names))
    check(after["name"] == "infra", "the project is still 'infra' (the live infra_* volumes)", after["name"])
    check(sorted(after["volumes"]) == ["infra_ai_secrets", "infra_bot_data", "infra_parts_data"],
          "the same three volumes", repr(sorted(after["volumes"])))
    check(after["services"]["nginx"].get("ports") == ["127.0.0.1:8080:80"], "nginx still on 127.0.0.1:8080",
          repr(after["services"]["nginx"].get("ports")))

    # A server's infra/.env with every variable this file reads (placeholders).
    full = dict(env, PUBLIC_BASE_URL="https://example.invalid", NGINX_PORT="8080", NGINX_BIND="127.0.0.1",
                GEMINI_MODEL_CHAIN="m1,m2", CATALOGUE_ENABLED="true", CATALOGUE_WORKER_TOKEN="placeholder",
                INTAKE_READ_MODE="zones", INTAKE_LINKS="on", INTAKE_LINK_CHECK="lite")
    before_full = cr.render([(cr.PROD_FILE, _snapshot_source())], full) if _snapshot_source() else None
    if before_full is not None:
        found = cr.diff(before_full, render([cr.PROD_FILE], full))
        check(not found, "with a full infra/.env (PUBLIC_BASE_URL set), production renders exactly as before",
              repr(found[:3]))
    else:
        print("    skip the full-.env comparison: no git history here (the snapshot check above still ran)")

    try:
        render([cr.PROD_FILE], {"TELEGRAM_BOT_TOKEN": "x"})
        check(False, "production still refuses to render without GEMINI_API_KEY")
    except cr.RenderError:
        check(True, "production still refuses to render without GEMINI_API_KEY")
    no_bot = render([cr.PROD_FILE], {"GEMINI_API_KEY": "x"})
    check(no_bot["services"]["api"]["environment"]["TELEGRAM_BOT_TOKEN"] == "",
          "without a Telegram token it renders (empty): a staging stack runs without a bot (deploy.sh still demands it for production)")


def _snapshot_source() -> str | None:
    """master's prod compose file from git (5ee5e03), when this checkout has
    the history; None in a shallow CI checkout."""
    import subprocess
    for ref in ("5ee5e0398cd07b0e90f1f6655bb399b39e8a5006", "origin/master"):
        try:
            return cr.git_show(ref, cr.PROD_FILE)
        except (subprocess.CalledProcessError, OSError):
            continue
    return None


def main() -> int:
    if cr.yaml is None:
        print("FAIL: PyYAML is not installed (pip install pyyaml)")
        return 1
    for section in SECTIONS:
        try:
            section()
        except Exception as exc:  # a crash is a failure, not a pass
            import traceback
            traceback.print_exc()
            FAILURES.append(f"{section.__name__} crashed: {type(exc).__name__}: {exc}")
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAIL {f}")
    return 1 if FAILURES else 0


SECTIONS = [prod_unchanged]

if __name__ == "__main__":
    sys.exit(main())
