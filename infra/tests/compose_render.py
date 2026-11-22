#!/usr/bin/env python3
"""Renders Carlink's compose files the way `docker compose config` would,
without Docker, so a change to infra/docker-compose.prod.yml can be checked
for what it does to PRODUCTION before it reaches the server.

    # what production's compose turns into (prod env defaults), as JSON
    python infra/tests/compose_render.py render -f infra/docker-compose.prod.yml

    # production before (a git ref's file) vs after (this checkout): only the
    # differences, and exit 1 when there are any not listed with --expect
    python infra/tests/compose_render.py diff --before-ref origin/master \
        [--expect services.api.environment.PUBLIC_BASE_URL ...]

    # the staging stack (prod file + staging override + shared AI folder)
    python infra/tests/compose_render.py render -p carlink-staging \
        -f infra/docker-compose.prod.yml -f infra/docker-compose.staging.yml \
        -f infra/docker-compose.ai-shared.yml --env-file infra/staging.env.example

What it does (the parts of the Compose spec these files use): YAML anchors
and `<<` merges (PyYAML), ${VAR} / ${VAR:-default} / ${VAR-default} /
${VAR:?error} / ${VAR?error} / ${VAR:+alt} / $$ interpolation from the given
environment, the merge of override files (environment and labels by name,
volumes by target, ports and profiles appended without duplicates, command
and entrypoint replaced, mappings merged), the project name (-p, then
COMPOSE_PROJECT_NAME, then the files' top-level `name:`, then the first
file's folder) and project-qualified volume names. Top-level `version` and
`x-*` keys are left out, as `docker compose config` leaves them out.

Not a full Compose: no profiles filtering, no `extends`, no nested ${...}
inside a default (refused). It is a reviewer's tool and the basis of
infra/tests/test_compose.py; the server's `docker compose config` is the
final word.

The "prod env defaults": GEMINI_API_KEY and TELEGRAM_BOT_TOKEN set to
placeholders (deploy.sh demands both for production) and nothing else, so
every other ${VAR:-default} renders its default. No real value is ever read:
--env-file takes a file you name (an example file), never infra/.env by
default.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - reported to the caller
    yaml = None

REPO = Path(__file__).resolve().parents[2]
PROD_FILE = "infra/docker-compose.prod.yml"
# Placeholders, never real values: production's deploy demands both.
PROD_ENV_DEFAULTS = {"GEMINI_API_KEY": "placeholder-gemini-key", "TELEGRAM_BOT_TOKEN": "placeholder-bot-token"}


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- interpolation

_TOKEN = re.compile(r"\$(?:(\$)|\{([^}]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_BRACED = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])(.*))?$", re.DOTALL)


def interpolate(text: str, env: dict[str, str], where: str = "") -> str:
    def one(m: re.Match) -> str:
        if m.group(1):
            return "$"
        if m.group(3):
            return env.get(m.group(3), "")
        inner = m.group(2)
        if "${" in inner or "$" in inner.split(":", 1)[0]:
            raise RenderError(f"{where}: nested interpolation is not supported: ${{{inner}}}")
        b = _BRACED.match(inner)
        if not b:
            raise RenderError(f"{where}: bad interpolation ${{{inner}}}")
        name, op, arg = b.group(1), b.group(2), b.group(3) or ""
        value = env.get(name)
        present = value is not None
        non_empty = present and value != ""
        if op is None:
            return value or ""
        if op == ":-":
            return value if non_empty else arg
        if op == "-":
            return value if present else arg
        if op == ":?":
            if not non_empty:
                raise RenderError(f"{where}: required variable {name} is missing a value: {arg}")
            return value
        if op == "?":
            if not present:
                raise RenderError(f"{where}: required variable {name} is missing a value: {arg}")
            return value
        if op == ":+":
            return arg if non_empty else ""
        if op == "+":
            return arg if present else ""
        raise RenderError(f"{where}: bad interpolation ${{{inner}}}")

    return _TOKEN.sub(one, text)


def interpolate_tree(node, env: dict[str, str], where: str = ""):
    if isinstance(node, dict):
        return {k: interpolate_tree(v, env, f"{where}.{k}" if where else str(k)) for k, v in node.items()}
    if isinstance(node, list):
        return [interpolate_tree(v, env, f"{where}[{i}]") for i, v in enumerate(node)]
    if isinstance(node, str):
        return interpolate(node, env, where)
    return node


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines (compose's .env format: no export, # comments, optional
    quotes). Never sourced."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


# ---------------------------------------------------------------- merging

def _env_map(env) -> dict[str, str | None]:
    if env is None:
        return {}
    if isinstance(env, dict):
        return {str(k): (None if v is None else str(v)) for k, v in env.items()}
    out: dict[str, str | None] = {}
    for item in env:
        key, sep, value = str(item).partition("=")
        out[key] = value if sep else None
    return out


def _volume_target(v) -> str:
    if isinstance(v, dict):
        return str(v.get("target"))
    parts = str(v).split(":")
    return parts[1] if len(parts) >= 2 else parts[0]


def _merge_service(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if key not in out:
            out[key] = value
        elif key in ("environment", "labels"):
            merged = _env_map(out[key])
            merged.update(_env_map(value))
            out[key] = merged
        elif key == "volumes":
            by_target = {_volume_target(v): v for v in out[key] or []}
            for v in value or []:
                by_target[_volume_target(v)] = v
            out[key] = list(by_target.values())
        elif key in ("ports", "profiles", "expose"):
            seen = list(out[key] or [])
            for v in value or []:
                if v not in seen:
                    seen.append(v)
            out[key] = seen
        elif key in ("command", "entrypoint"):
            out[key] = value
        elif isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        elif isinstance(out[key], list) and isinstance(value, list):
            out[key] = out[key] + [v for v in value if v not in out[key]]
        else:
            out[key] = value
    return out


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def merge_files(docs: list[dict]) -> dict:
    out: dict = {}
    for doc in docs:
        for key, value in doc.items():
            if key == "services":
                services = out.setdefault("services", {})
                for name, svc in (value or {}).items():
                    services[name] = _merge_service(services.get(name, {}), svc or {})
            elif isinstance(out.get(key), dict) and isinstance(value, dict):
                out[key] = _deep_merge(out[key], value)
            else:
                out[key] = value
    return out


# ---------------------------------------------------------------- rendering

def load_yaml(text: str, where: str) -> dict:
    if yaml is None:
        raise RenderError("PyYAML is not installed (pip install pyyaml)")
    doc = yaml.safe_load(text) or {}
    if not isinstance(doc, dict):
        raise RenderError(f"{where}: not a mapping")
    return doc


def _repo_relative(path: Path) -> str:
    """'<repo>/apps/bot-service' for a path inside the checkout (so the render
    doesn't depend on where the checkout is), else the path as given."""
    resolved, repo = path.resolve(), REPO.resolve()
    try:
        return "<repo>/" + resolved.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def _normalize_service(name: str, svc: dict, project: str, base_dir: Path) -> dict:
    out: dict = {}
    for key, value in sorted(svc.items()):
        if key == "environment":
            value = dict(sorted(_env_map(value).items()))
        elif key == "labels":
            value = dict(sorted(_env_map(value).items()))
        elif key == "depends_on":
            value = sorted(value) if isinstance(value, list) else dict(sorted(value.items()))
        elif key == "build":
            if isinstance(value, str):
                value = {"context": value}
            value = dict(value)
            if "context" in value:
                value["context"] = _repo_relative(base_dir / value["context"])
            if isinstance(value.get("args"), list):
                value["args"] = dict(sorted(_env_map(value["args"]).items()))
        elif key == "volumes":
            value = [_normalize_volume(v, project, base_dir) for v in value or []]
        elif key == "ports":
            value = [str(p) for p in value or []]
        elif key in ("oom_score_adj",):
            value = int(value)
        out[key] = value
    if "container_name" not in out:
        out["container_name"] = f"{project}-{name}-1"
    if "image" not in out and "build" in out:
        out["image"] = f"{project}-{name}"
    return out


def _normalize_volume(v, project: str, base_dir: Path) -> str:
    if isinstance(v, dict):
        src, target, ro = v.get("source", ""), v.get("target", ""), v.get("read_only", False)
        mode = "ro" if ro else ""
    else:
        parts = str(v).split(":")
        src, target = (parts[0], parts[1]) if len(parts) >= 2 else ("", parts[0])
        mode = parts[2] if len(parts) >= 3 else ""
    if src.startswith(".") or src.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", src):
        if src.startswith("."):
            src = _repo_relative(base_dir / src)
        kind = "bind"
    else:
        src = f"{project}_{src}" if src else ""
        kind = "volume"
    return f"{kind}:{src}:{target}" + (f":{mode}" if mode else "")


def render(files: list[tuple[str, str]], env: dict[str, str], project: str | None = None) -> dict:
    """files: [(path for messages and folder, YAML text)]. Returns the
    normalized config: name, services (sorted), volumes (project-qualified),
    networks."""
    docs = []
    for path, text in files:
        raw = load_yaml(text, path)
        raw = {k: v for k, v in raw.items() if k != "version" and not str(k).startswith("x-")}
        docs.append(interpolate_tree(raw, env, path))
    merged = merge_files(docs)
    first_dir = (REPO / files[0][0]).parent if not Path(files[0][0]).is_absolute() else Path(files[0][0]).parent
    name = project or env.get("COMPOSE_PROJECT_NAME") or merged.get("name") or first_dir.name
    name = str(name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise RenderError(f"project name {name!r} is not a valid compose project name")
    services = {svc: _normalize_service(svc, body or {}, name, first_dir)
                for svc, body in sorted((merged.get("services") or {}).items())}
    volumes = {f"{name}_{vol}": (body or {}) for vol, body in sorted((merged.get("volumes") or {}).items())}
    networks = {f"{name}_{net}": (body or {}) for net, body in sorted((merged.get("networks") or {"default": None}).items())}
    return {"name": name, "services": services, "volumes": volumes, "networks": networks}


def diff(before, after, path: str = "") -> list[tuple[str, object, object]]:
    """Leaf differences as (dotted path, before, after); a missing side is
    the string "<absent>"."""
    out = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            p = f"{path}.{key}" if path else str(key)
            if key not in before:
                out.append((p, "<absent>", after[key]))
            elif key not in after:
                out.append((p, before[key], "<absent>"))
            else:
                out.extend(diff(before[key], after[key], p))
    elif before != after:
        out.append((path, before, after))
    return out


def git_show(ref: str, path: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{path}"], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def _env_from_args(args) -> dict[str, str]:
    env: dict[str, str] = {}
    if not args.no_prod_defaults:
        env.update(PROD_ENV_DEFAULTS)
    for f in args.env_file or []:
        env.update(read_env_file(Path(f)))
    for item in args.env or []:
        key, _, value = item.partition("=")
        env[key] = value
    return env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("render", "diff"):
        p = sub.add_parser(name)
        p.add_argument("--env", action="append", metavar="KEY=VALUE", help="a variable (repeatable)")
        p.add_argument("--env-file", action="append", help="a KEY=value file (repeatable; later wins)")
        p.add_argument("--no-prod-defaults", action="store_true",
                       help="don't set the GEMINI_API_KEY / TELEGRAM_BOT_TOKEN placeholders")
        p.add_argument("-p", "--project", help="project name (as docker compose -p)")
    sub.choices["render"].add_argument("-f", "--file", action="append", required=True)
    d = sub.choices["diff"]
    d.add_argument("--before-ref", default="origin/master", help="git ref of the 'before' file (default origin/master)")
    d.add_argument("--file", default=PROD_FILE, help=f"the compose file to compare (default {PROD_FILE})")
    d.add_argument("--expect", action="append", default=[],
                   help="a dotted path allowed to differ (repeatable), e.g. services.api.environment.PUBLIC_BASE_URL")
    args = ap.parse_args(argv)
    env = _env_from_args(args)
    try:
        if args.cmd == "render":
            files = [(f, (REPO / f if not Path(f).is_absolute() else Path(f)).read_text(encoding="utf-8")) for f in args.file]
            print(json.dumps(render(files, env, args.project), indent=2, sort_keys=True))
            return 0
        before = render([(args.file, git_show(args.before_ref, args.file))], env, args.project)
        after = render([(args.file, (REPO / args.file).read_text(encoding="utf-8"))], env, args.project)
    except (RenderError, subprocess.CalledProcessError, OSError) as exc:
        print(f"compose_render: {exc}", file=sys.stderr)
        return 2
    found = diff(before, after)
    unexpected = [d for d in found if d[0] not in args.expect]
    print(f"{args.file}: {args.before_ref} vs this checkout, env: "
          f"{', '.join(sorted(k for k in env)) or '(none)'}")
    for p, b, a in found:
        tag = "expected  " if p in args.expect else "DIFFERENT "
        print(f"  {tag}{p}\n      before: {json.dumps(b)}\n      after:  {json.dumps(a)}")
    print(f"{len(found)} difference(s), {len(unexpected)} not expected")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
