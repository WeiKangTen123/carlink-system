#!/usr/bin/env python3
"""A stand-in for `docker compose` in infra/tests/test_deploy.py (no Docker).

    fake_compose.py -p PROJECT -f FILE [-f FILE ...] <command> [args]

`config --format json` renders the real compose files the way compose would
(compose_render.py) from the environment this process got -- so what
deploy.sh scrubbed is really gone -- plus <project dir>/.env (the shell wins,
as in compose), drops services whose profiles aren't active
(COMPOSE_PROFILES), and prints docker compose's JSON shape (container_name,
long-form ports and volumes, named top-level volumes and networks). `build`
and `up` exit with MOCK_BUILD_EXIT / MOCK_UP_EXIT; anything else exits 0.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compose_render as cr  # noqa: E402


def main(argv: list[str]) -> int:
    project, files, rest = None, [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-p":
            project = argv[i + 1]
            i += 2
        elif a == "-f":
            files.append(argv[i + 1])
            i += 2
        else:
            rest = argv[i:]
            break
    cmd = rest[0] if rest else ""
    if cmd == "version":
        print("Docker Compose version v2.30.3")
        return 0
    if cmd == "build":
        return int(os.environ.get("MOCK_BUILD_EXIT", "0"))
    if cmd == "up":
        return int(os.environ.get("MOCK_UP_EXIT", "0"))
    if cmd != "config":
        return 0
    first_dir = (Path.cwd() / files[0]).parent
    env = {}
    dotenv = first_dir / ".env"
    if dotenv.exists():
        env.update(cr.read_env_file(dotenv))
    env.update({k: v for k, v in os.environ.items()})
    texts = [(str(Path.cwd() / f), (Path.cwd() / f).read_text(encoding="utf-8")) for f in files]
    try:
        r = cr.render(texts, env, project)
    except cr.RenderError as exc:
        print(f"error while interpolating: {exc}", file=sys.stderr)
        return 1
    active = {p.strip() for p in (env.get("COMPOSE_PROFILES") or "").split(",") if p.strip()}
    out = {"name": r["name"], "services": {}, "volumes": {}, "networks": {}}
    prefix = r["name"] + "_"
    for full in r["volumes"]:
        out["volumes"][full[len(prefix):]] = {"name": full}
    for full in r["networks"]:
        out["networks"][full[len(prefix):]] = {"name": full}
    for svc, body in r["services"].items():
        profiles = body.get("profiles") or []
        if profiles and not (set(profiles) & active):
            continue
        s = {"container_name": body.get("container_name"),
             "environment": {k: (v if v is not None else "") for k, v in (body.get("environment") or {}).items()}}
        ports = []
        for p in body.get("ports") or []:
            parts = p.split(":")
            host_ip, published, target = (parts if len(parts) == 3 else ["", parts[0], parts[1]])
            ports.append({"mode": "ingress", "host_ip": host_ip, "target": int(target), "published": published,
                          "protocol": "tcp"})
        if ports:
            s["ports"] = ports
        vols = []
        for v in body.get("volumes") or []:
            kind, rest_ = v.split(":", 1)
            if kind == "volume":
                src, target = rest_.split(":")[:2]
                vols.append({"type": "volume", "source": src[len(prefix):], "target": target})
            else:
                bits = rest_.rsplit(":", 2) if rest_.endswith(":ro") else rest_.rsplit(":", 1)
                vols.append({"type": "bind", "source": bits[0], "target": bits[1]})
        if vols:
            s["volumes"] = vols
        out["services"][svc] = s
    if os.environ.get("MOCK_RENDER_OUT"):
        Path(os.environ["MOCK_RENDER_OUT"]).write_text(json.dumps(out), encoding="utf-8")
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
