#!/usr/bin/env python3
"""Production's compose comes out the same; the staging stack cannot touch it.

    python infra/tests/test_compose.py      (needs PyYAML)

1. PRODUCTION UNCHANGED. infra/docker-compose.prod.yml rendered with the
   production env defaults (compose_render.py: GEMINI_API_KEY and
   TELEGRAM_BOT_TOKEN set, every other variable at its default) is compared
   with prod-compose.render.json, the render of master's file (5ee5e03) taken
   before the staging work. Only the differences in EXPECTED_PROD_CHANGES may
   appear, each with its reason, plus the release's new switch lines
   (EXPECTED_PROD_NEW_ENV), each added at config.py's default. With
   PUBLIC_BASE_URL set (as a server's infra/.env may set it), only those
   added switch lines. A deliberate change to
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

# Release 1 (int/release-1): the switches of PR #38 (detection fixes F1-F7,
# batching B2-B5/B9) and Wave 2 (E7, E9a, E11, E12) are passed through the
# shared x-bot-service block, so api and telegram-bot each get these lines,
# added at config.py's defaults = today's behaviour (test_enh_fixes,
# test_enh_batching and test_enh_wave2 section 0 check each default against
# config.py). They may appear only as ADDED, with exactly these values; a
# server's infra/.env that sets one gets its own value, as for any variable.
EXPECTED_PROD_NEW_ENV = {
    "INTAKE_MAIN_READ_FALLBACK": "off",
    "INTAKE_KEEP_CUT": "off",
    "INTAKE_KEEP_OTHER_SIDE": "off",
    "INTAKE_LINK_HIDDEN_NO": "keep",
    "INTAKE_SORT_RETRY_SKIPPED": "off",
    "INTAKE_PARALLEL_READS": "off",
    "INTAKE_LINK_CHECK_PARALLEL": "off",
    "INTAKE_SORT_WORKERS": "3",
    "INTAKE_KEEP_SORT_LABEL": "off",
    "INTAKE_SORT_RESCUE": "off",
    "INTAKE_MAX_READ_PHOTOS": "",
    "GEMINI_IMAGE_RESOLUTION_MAIN_READ": "",
    "INTAKE_SIDE_GEOMETRY": "off",
    "INTAKE_ZONE_WRONG_END": "drop",
    "INTAKE_LINK_CHECK_MAX_CALLS": "2",
    # Photo storage (owner, 23 Nov 2026): the stored JPEG originals re-saved
    # smaller (app/config.py photo_store_compress), added the same way --
    # only as ADDED, only at config.py's default "off" (scripts/
    # test_photo_compress.py checks that default), on api and telegram-bot.
    "PHOTO_STORE_COMPRESS": "off",
    # Phase 1 (report checks): the AI's words in the firm's words
    # (app/reports/firm_words.py), off = every reading as before
    # (test_p1_report_checks checks the default against config.py).
    "INTAKE_FIRM_WORDS": "off",
    # The workshop and the odometer left to the user (owner, 25 Nov 2026;
    # app/reports/user_fields.py), off = every read as before
    # (scripts/test_user_fields.py checks the default against config.py).
    "INTAKE_USER_FIELDS": "off",
    # Detection Round 1 (owner, 25 Nov 2026): A1 the sorter's finds as
    # suggestions, A2 the zone reads' "intact" rule -- each at config.py's
    # default = today's behaviour (scripts/test_detect_r1.py section 0
    # checks them against config.py).
    "INTAKE_SORTER_HINTS": "off",
    "INTAKE_INTACT_RULE": "old",
}
NEW_ENV_SERVICES = ("api", "telegram-bot")


def _new_switch(difference) -> bool:
    """One of the release's new switch lines, added at its default."""
    path, before, after = difference
    for svc in NEW_ENV_SERVICES:
        prefix = f"services.{svc}.environment."
        if path.startswith(prefix):
            name = path[len(prefix):]
            return (name in EXPECTED_PROD_NEW_ENV and before == "<absent>"
                    and after == EXPECTED_PROD_NEW_ENV[name])
    return False


def _check_new_switches(found, label: str) -> None:
    added = {d[0] for d in found if _new_switch(d)}
    want = {f"services.{svc}.environment.{name}" for svc in NEW_ENV_SERVICES for name in EXPECTED_PROD_NEW_ENV}
    check(added == want, label, f"missing {sorted(want - added)}")


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
    unexpected = [d for d in found if d[0] not in EXPECTED_PROD_CHANGES and not _new_switch(d)]
    check(not unexpected, "with the prod env defaults, nothing differs but the expected PUBLIC_BASE_URL default"
          " (and the release's new switch lines, added at their defaults)",
          "; ".join(f"{p}: {b!r} -> {a!r}" for p, b, a in unexpected))
    _check_new_switches(found, f"api and telegram-bot each get the {len(EXPECTED_PROD_NEW_ENV)} new switch lines,"
                               " at their defaults (off / keep / drop / 3 / 2 / blank)")
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
        others = [d for d in found if not _new_switch(d)]
        check(not others, "with a full infra/.env (PUBLIC_BASE_URL set), production renders exactly as before"
              " (but the release's new switch lines at their defaults)", repr(others[:3]))
        _check_new_switches(found, "with a full infra/.env: the same new switch lines, nothing else")
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


SHARED_FILE = "infra/docker-compose.ai-shared.yml"
STAGING_FILE = "infra/docker-compose.staging.yml"
SHARED_MOUNT = "bind:/opt/carlink-ai-shared:/app/ai-shared"
LANE_VARS = {"GEMINI_SHARED_QUOTA_DIR", "GEMINI_QUOTA_DAY_LIMITS", "GEMINI_STAGING_MAX_PER_MINUTE",
             "GEMINI_QUOTA_MINUTE_BUDGET", "GEMINI_QUOTA_PROD_PER_MINUTE",
             "GEMINI_STAGING_WINDOW_SHARE", "GEMINI_STAGING_DAY_MAX", "GEMINI_STAGING_MODEL_RESERVE",
             "GEMINI_STAGING_PROD_BUSY"}


def shared_override() -> None:
    print("\n2. production + the shared AI folder override")
    env = dict(cr.PROD_ENV_DEFAULTS)
    prod = render([cr.PROD_FILE], env)
    both = render([cr.PROD_FILE, SHARED_FILE], env)
    allowed = {f"services.{svc}.environment.{v}" for svc in ("api", "telegram-bot") for v in LANE_VARS}
    allowed |= {"services.api.volumes", "services.telegram-bot.volumes"}
    found = cr.diff(prod, both)
    check(found and all(p in allowed for p, _, _ in found),
          "it changes only api's and telegram-bot's lane settings and volumes",
          repr([p for p, _, _ in found if p not in allowed]))
    for svc in ("api", "telegram-bot"):
        before, after = prod["services"][svc]["volumes"], both["services"][svc]["volumes"]
        check(after == before + [SHARED_MOUNT], f"{svc}: the host folder is added at /app/ai-shared, the rest kept",
              repr(after))
        e = both["services"][svc]["environment"]
        check(e.get("GEMINI_SHARED_QUOTA_DIR") == "/app/ai-shared" and "GEMINI_QUOTA_LANE" not in e,
              f"{svc}: GEMINI_SHARED_QUOTA_DIR set, lane left at the app's default (prod)")
    check(both["services"]["parts-worker"] == prod["services"]["parts-worker"]
          and both["services"]["dashboard"] == prod["services"]["dashboard"]
          and both["services"]["nginx"] == prod["services"]["nginx"],
          "dashboard, nginx and parts-worker are untouched")
    moved = render([cr.PROD_FILE, SHARED_FILE], dict(env, CARLINK_AI_SHARED_DIR="/srv/ai"))
    check("bind:/srv/ai:/app/ai-shared" in moved["services"]["api"]["volumes"],
          "CARLINK_AI_SHARED_DIR moves the host folder")


def staging_env() -> dict:
    env = cr.read_env_file(REPO / "infra" / "staging.env.example")
    check(env.get("GEMINI_API_KEY") == "" and env.get("TELEGRAM_BOT_TOKEN") == ""
          and env.get("CATALOGUE_WORKER_TOKEN") == "",
          "staging.env.example holds no secret values")
    env["GEMINI_API_KEY"] = "placeholder-gemini-key"
    return env


def staging_stack() -> None:
    print("\n3. the staging stack beside production")
    env = staging_env()
    files = [cr.PROD_FILE, STAGING_FILE, SHARED_FILE]
    st = render(files, env)
    prod = render([cr.PROD_FILE], dict(cr.PROD_ENV_DEFAULTS))
    check(st["name"] == "carlink-staging", "project carlink-staging", st["name"])
    no_name = {k: v for k, v in env.items() if k != "COMPOSE_PROJECT_NAME"}
    check(render(files, no_name)["name"] == "carlink-staging",
          "still carlink-staging without COMPOSE_PROJECT_NAME (the override's name:), never 'infra'")
    names = {s: b["container_name"] for s, b in st["services"].items()}
    prod_names = {b["container_name"] for b in prod["services"].values()}
    check(all(n.startswith("carlink_staging_") for n in names.values()), "every container is carlink_staging_*",
          repr(names))
    check(not (set(names.values()) & prod_names), "no container name is production's")
    check(set(st["volumes"]) == {"carlink-staging_ai_secrets", "carlink-staging_bot_data", "carlink-staging_parts_data"}
          and not (set(st["volumes"]) & set(prod["volumes"])), "its own volumes, none of production's",
          repr(sorted(st["volumes"])))
    check(list(st["networks"]) == ["carlink-staging_default"], "its own network")
    check(st["services"]["nginx"].get("ports") == ["127.0.0.1:8081:80"], "nginx on 127.0.0.1:8081 only",
          repr(st["services"]["nginx"].get("ports")))
    used_vols = [v for b in st["services"].values() for v in b.get("volumes") or [] if v.startswith("volume:")]
    check(used_vols and all(v.startswith("volume:carlink-staging_") for v in used_vols),
          "every named volume a service mounts is staging's", repr(used_vols))

    total = 0
    for svc, body in st["services"].items():
        limits = ((body.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        check(body.get("restart") == "unless-stopped" and body.get("oom_score_adj") == 500
              and limits.get("memory") and limits.get("cpus"),
              f"{svc}: unless-stopped, oom_score_adj 500, memory {limits.get('memory')} and cpus {limits.get('cpus')} capped")
        mem = str(limits.get("memory", "0"))
        total += int(mem[:-1]) if mem.endswith("M") else int(float(mem[:-1]) * 1024) if mem.endswith("G") else 0
    check(2800 <= total <= 3200, f"worst case about 3 GB of memory ({total} MB, everything on)")
    check(st["services"]["telegram-bot"].get("profiles") == ["telegram"]
          and st["services"]["parts-worker"].get("profiles") == ["catalogue"]
          and not any(st["services"][s].get("profiles") for s in ("api", "dashboard", "nginx")),
          "the bot and the parts worker only on request (profiles telegram / catalogue)")
    check(not any(b.get("profiles") for b in prod["services"].values()), "production has no profiles (all five run)")

    for svc in ("api", "telegram-bot"):
        e = st["services"][svc]["environment"]
        want = {"GEMINI_QUOTA_LANE": "staging", "GEMINI_SHARED_QUOTA_DIR": "/app/ai-shared",
                "GEMINI_MAX_REQUESTS_PER_MINUTE": "4", "CARLINK_STACK": "staging",
                "PUBLIC_BASE_URL": "https://carlink-staging.34-16-92-98.sslip.io", "TELEGRAM_BOT_TOKEN": ""}
        got = {k: e.get(k) for k in want}
        check(got == want, f"{svc}: staging lane, shared folder, pace 4/min, staging URL, no bot token", repr(got))
        check(SHARED_MOUNT in st["services"][svc]["volumes"], f"{svc}: the shared AI folder is mounted")

    # Parity: apart from what staging changes on purpose, each service is
    # production's (same build, command, image source, the same flags).
    staged_keys = {"container_name", "restart", "oom_score_adj", "deploy", "profiles", "labels", "volumes", "image"}
    staged_env = LANE_VARS | {"GEMINI_QUOTA_LANE", "GEMINI_MAX_REQUESTS_PER_MINUTE", "GEMINI_MIN_CALL_INTERVAL_SECONDS",
                              "CARLINK_STACK", "PUBLIC_BASE_URL", "TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY",
                              "CATALOGUE_ENABLED", "CATALOGUE_WORKER_TOKEN"}
    flags_env = dict(cr.PROD_ENV_DEFAULTS, **{k: v for k, v in env.items() if k not in staged_env})
    prod_same_flags = render([cr.PROD_FILE], flags_env)
    for svc, body in st["services"].items():
        p = prod_same_flags["services"][svc]
        a = {k: v for k, v in body.items() if k not in staged_keys and k != "environment"}
        b = {k: v for k, v in p.items() if k not in staged_keys and k != "environment"}
        ea = {k: v for k, v in (body.get("environment") or {}).items() if k not in staged_env}
        eb = {k: v for k, v in (p.get("environment") or {}).items() if k not in staged_env}
        check(a == b and ea == eb, f"{svc}: otherwise the same as production (build, command, flags)",
              repr(cr.diff(b, a) + cr.diff(eb, ea))[:300])
    check(st["services"]["parts-worker"]["deploy"]["resources"]["limits"]["memory"] == "512M",
          "parts-worker keeps production's 512M cap")

    for var, why in (("CARLINK_NAME", "production's container names"), ("NGINX_PORT", "production's port 8080")):
        try:
            render(files, {k: v for k, v in env.items() if k != var})
            check(False, f"without {var} the staging files refuse to render (no fallback to {why})")
        except cr.RenderError:
            check(True, f"without {var} the staging files refuse to render (no fallback to {why})")


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


SECTIONS = [prod_unchanged, shared_override, staging_stack]

if __name__ == "__main__":
    sys.exit(main())
