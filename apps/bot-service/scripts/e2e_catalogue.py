"""End-to-end run of the parts-catalogue look-up on this PC: a real API
(uvicorn) on 127.0.0.1 against a throw-away database, the real catalogue worker
(tools/parts_mcp, `--once`) reading the parts site, and the surveyor's side
through the API -- start a look-up, see the rows, hide one and undo it, put
one on an existing Annex A line and one on a new line (catalogue_ref), see
them accepted, the estimates untouched, nothing of it in the Excel workbook
or the PDF's HTML, then take the new line off again.

    cd apps/bot-service
    python scripts/e2e_catalogue.py                 # with the live worker
    python scripts/e2e_catalogue.py --skip-live     # a canned result instead (offline)
    python scripts/e2e_catalogue.py --hold          # keep the API up afterwards (screenshots)

Not part of the normal test suite: it starts processes and (without
--skip-live) lets the worker fetch pages the cache doesn't have yet -- at
most --max-live (20) requests to the parts site: a watchdog stops the worker
at that count. Never touches a real database or the live server: the API's
database, storage and secrets live in a temporary folder, and the worker
talks to http://127.0.0.1 only. The worker token is a random value made
here and never printed. Every plate, VIN and name is made up.

Options:
    --skip-live            post a canned result as the worker instead of running it
    --worker-python PATH   the parts_mcp venv's python (default: $E2E_WORKER_PYTHON, or
                           this python)
    --data-dir PATH        the worker's data folder (default: $PARTS_MCP_DATA_DIR, or
                           ~/carlink-parts-data) -- its page cache is what keeps it polite
    --sources LIST         the worker's sources (default: microfiches, the only one in this repo)
    --max-live N           stop the worker at N live requests to one host (default 20)
    --port N               the API's port (default: a free one)
    --hold                 after the run, seed a second case looked up with the same rows,
                           write <tmp>/hold.json (API base, case ids, the test login) and
                           keep the API running until Ctrl+C or <tmp>/stop exists
    --keep                 keep the temporary folder
"""
import argparse
import io
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent
TOOLS = REPO / "tools"
DEFAULT_WORKER_PYTHON = sys.executable  # set $E2E_WORKER_PYTHON to the parts_mcp venv

# The made-up case. Nothing here may reach the worker but the car and the
# part names.
PLATE, VIN, ENGINE, OUR_REF = "TEST 0001", "TESTVIN0000E2E001", "TESTENG0001", "CL E2E 0001"
DRIVER = "Test Driver Person"
DAMAGE = ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Rear Bumper Beam"]
BUMPER_EST = 900.0

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--skip-live", action="store_true")
    p.add_argument("--worker-python", default=os.environ.get("E2E_WORKER_PYTHON") or DEFAULT_WORKER_PYTHON)
    p.add_argument("--data-dir", default=os.environ.get("PARTS_MCP_DATA_DIR") or str(Path.home() / "carlink-parts-data"))
    p.add_argument("--sources", default="microfiches")
    p.add_argument("--max-live", type=int, default=20)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--hold", action="store_true")
    p.add_argument("--keep", action="store_true")
    return p.parse_args(argv)


# ------------------------------------------------------------------ output

FAILED: list[str] = []
SECRETS: list[str] = []


def say(text: str = "") -> None:
    for s in SECRETS:
        text = text.replace(s, "[token]")
    print(text, flush=True)


def step(text: str) -> None:
    say(f"\n== {text}")


def expect(cond, label: str, detail: str = "") -> bool:
    say(f"   {'ok  ' if cond else 'FAIL'} {label}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILED.append(label)
    return bool(cond)


def note(label: str) -> None:
    say(f"   ..   {label}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ the case


def seed(tmp: Path) -> dict:
    """The test login and a filed Honda Vezel 2016 case with four damaged
    parts and an Annex A whose rear bumper is priced from past cases."""
    from app.auth.passwords import hash_password
    from app.reports.db import SessionLocal, init_db
    from app.reports.models import Report, User
    from app.reports.schema import SecurityIncidentDraft
    from app.reports.synthesize_annexes import synthesize_annexes_for_report

    init_db()
    password = "e2e-" + secrets.token_urlsafe(12)
    db = SessionLocal()
    try:
        user = User(email="e2e.surveyor@example.com", username="e2e_surveyor", name="E2E Surveyor", role="user",
                    password_hash=hash_password(password))
        db.add(user)
        db.flush()
        ids = [_case(db, user.id, SecurityIncidentDraft, synthesize_annexes_for_report, Report)]
        db.commit()
        return {"user_id": user.id, "username": "e2e_surveyor", "name": "E2E Surveyor", "password": password, "case": ids[0]}
    finally:
        db.close()


def _row(name, **kw):
    out = {"item_no": "1.0", "part_name": name, "condition": "", "action": "", "qty": 1.0, "qty_text": None,
           "workshop_est": 0.0, "discount_pct": 0.2, "adjusted_cost": 0.0, "is_net_item": False, "oem_part_number": None,
           "suggested_from": None, "usual_unit_price": None, "prefilled": False, "origin": "detection",
           "source_part": None, "stale": False, "main": True}
    out.update(kw)
    return out


def _case(db, owner_id, Draft, synthesize, Report) -> str:
    data = Draft(description="Made-up end-to-end test case: rear-end collision.",
                 category=["Vehicle Collision or Damage"]).model_dump()
    data["report_id"] = OUR_REF
    data["vehicle_info"].update({"plate_number": PLATE, "make": "Honda", "model": "Vezel", "year": "2016", "vin": VIN,
                                 "engine_number": ENGINE, "driver_name": DRIVER, "date_of_registration": "1 Mar 2016"})
    data["damage_summary"] = [{"part": p, "damage_type": "Dent", "severity": "Moderate", "human_verified": True} for p in DAMAGE]
    data["damaged_parts"] = list(DAMAGE)
    data["annex_a"] = {"items": [
        _row("Rear bumper", workshop_est=BUMPER_EST, prefilled=True, origin="history", usual_unit_price=720.0,
             suggested_from="2 past cases (Honda Vezel)", source_part="Rear Bumper", condition="Deformed/cut"),
        _row("Tail gate assy", source_part="Boot Lid"),
        _row("Tail lamp LH", source_part="Left Tail Lamp"),
        _row("Rear bumper reinforcement", source_part="Rear Bumper Beam"),
    ]}
    r = Report(channel="manual", reporter_chat_id="dashboard", status="confirmed", owner_id=owner_id,
               data=synthesize(data), photo_paths=[])
    db.add(r)
    db.flush()
    return r.id


# ------------------------------------------------------------------ the API


class Api:
    def __init__(self, base: str):
        import httpx

        self.base = base
        self.http = httpx.Client(base_url=base, timeout=120.0, headers={"Origin": base})

    def login(self, username: str, password: str) -> None:
        from app.auth.core import SESSION_COOKIE

        r = self.http.post("/auth/login", json={"email": username, "password": password})
        assert r.status_code == 200, f"login: {r.status_code} {r.text[:200]}"
        token = r.cookies.get(SESSION_COOKIE)
        assert token, "login set no session cookie"
        self.http.cookies.set(SESSION_COOKIE, token)

    def get(self, path, **kw):
        return self.http.get(path, **kw)

    def post(self, path, **kw):
        return self.http.post(path, **kw)

    def put(self, path, **kw):
        return self.http.put(path, **kw)


def start_api(tmp: Path, port: int, env: dict) -> subprocess.Popen:
    log = open(tmp / "api.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    import httpx

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the API stopped (exit {proc.returncode}); see {tmp / 'api.log'}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("the API did not come up in 90 s")


# ------------------------------------------------------------------ the worker


def live_counts(data_dir: Path) -> dict[str, int]:
    """Today's (UTC) live requests per host, from the worker's page cache
    (the same table the worker's own count reads)."""
    path = Path(data_dir) / "pages.db"
    if not path.exists():
        return {}
    day = datetime.now(timezone.utc).date().isoformat()
    for _ in range(5):
        try:
            con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
            try:
                return {h: n for h, n in con.execute("SELECT host, count FROM request_counts WHERE day = ?", (day,))}
            finally:
                con.close()
        except sqlite3.Error:
            time.sleep(0.2)
    return {}


def run_worker(args, api_base: str, token: str) -> dict:
    """The worker, once, against the local API. A watchdog stops it once
    it has made --max-live live requests to any one host."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CATALOGUE_", "DATABASE_", "STORAGE_", "SECRETS_"))}
    env.update({"CARLINK_API_BASE": api_base, "CATALOGUE_WORKER_TOKEN": token, "PARTS_MCP_DATA_DIR": args.data_dir,
                "WORKER_NAME": "e2e-local-pc", "PYTHONUTF8": "1"})
    before = live_counts(Path(args.data_dir))
    cmd = [args.worker_python, "-m", "parts_mcp.worker", "--once", "--sources", args.sources]
    note(f"running: {' '.join(Path(c).name if i == 0 else c for i, c in enumerate(cmd))} (cwd tools/)")
    proc = subprocess.Popen(cmd, cwd=str(TOOLS), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    stopped = {"by_watchdog": False}

    def watchdog():
        while proc.poll() is None:
            now = live_counts(Path(args.data_dir))
            if any(n - before.get(h, 0) >= args.max_live for h, n in now.items()):
                stopped["by_watchdog"] = True
                proc.terminate()
                return
            time.sleep(0.5)

    threading.Thread(target=watchdog, daemon=True).start()
    lines = []
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        say("   worker| " + line)
    proc.wait(timeout=60)
    after = live_counts(Path(args.data_dir))
    live = {h: n - before.get(h, 0) for h, n in after.items() if n - before.get(h, 0) > 0}
    return {"exit": proc.returncode, "live": live, "watchdog": stopped["by_watchdog"], "log": lines}


def canned_result(job: dict) -> dict:
    """--skip-live: rows shaped like the real parts-honda.uk answer for an
    HR-V (made up), for the parts the job asked."""
    def row(callout, number, name, amount, qty="1", side=None):
        return {"callout": callout, "part_number": number, "superseded_from": None, "name": name, "name_original": None,
                "qty": qty, "side": side, "notes": None, "ref_amount": amount, "ref_currency": "EUR",
                "ref_tax_note": "Unit Price Excl Tax", "source": "microfiches",
                "url": f"https://www.parts-honda.uk/part/{number}", "fetched_at": "2026-11-12T10:42:00+00:00", "stale": False}

    def diagram(key, name, rows):
        return {"diagram_key": f"microfiches:30585:{key}", "diagram_name": name,
                "diagram_url": f"https://www.parts-honda.uk/honda-cars/HRV/2016/ELEGANCE/{key}/30585",
                "image_url": f"https://www.parts-honda.uk/img/{key}.png", "match_score": 1.0, "rows": rows}

    found = {
        # The face in three colours (one group on the page).
        "Rear Bumper": [diagram("REAR-BUMPER", "REAR BUMPER", [
            row("1", "04715T7WA90ZZ", "FACE, RR. BUMPER", 309.41), row("1", "04715T7WA90ZA", "FACE, RR. BUMPER", 309.41),
            row("1", "04715T7WA90ZB", "FACE, RR. BUMPER", 309.41), row("7", "91503TG7003", "CLIP, BUMPER", 1.87, "4")])],
        "Boot Lid": [diagram("TAILGATE-PANEL", "TAILGATE PANEL", [row("1", "68100T7WA00ZZ", "TAILGATE (DOOR)", 533.77)])],
        "Left Tail Lamp": [diagram("TAILLIGHT", "TAILLIGHT", [row("1", "33550T7WA01", "LIGHT ASSY., L. TAIL", 187.23, side="left"),
                                                          row("26", "90650TX6A01", "CLIP, RR. COMBINATION LIGHT", 4.96)])],
        # Its own diagram, and the bumper's again (shown once, under the bumper).
        "Rear Bumper Beam": [diagram("REAR-BUMPER-BEAM", "REAR BUMPER BEAM", [row("2", "71530T7WA00", "BEAM, RR. BUMPER", 141.59)]),
                             diagram("REAR-BUMPER", "REAR BUMPER", [row("7", "91503TG7003", "CLIP, BUMPER", 1.87, "4")])],
    }
    vehicle = {"key": "microfiches:30585", "source": "microfiches", "brand": "Honda", "model": "HRV",
               "name": "HRV 2016 ELEGANCE", "market": "Europe", "year_from": None, "year_to": None,
               "url": "https://www.parts-honda.uk/honda-cars/HRV/2016/ELEGANCE/30585", "twin_of": "Vezel"}
    return {"status": "done", "error_code": None, "error_text": None, "vehicle": vehicle, "candidates": [vehicle],
            "failures": [], "parts": [{**p, "diagrams": found.get(p["source_part"], []), "failures": []} for p in job["parts"]]}


# ------------------------------------------------------------------ helpers


def rows_of(view: dict):
    for part in view.get("parts") or []:
        for d in part["diagrams"]:
            for r in d["rows"]:
                yield part, d, r


def ref_of(row: dict, d: dict) -> dict:
    """As the dashboard's lib/catalogue.catalogueRefOf."""
    return {"suggestion_id": row["id"], "source": row["source"], "part_number": row["part_number"], "name": row["name"],
            "diagram_name": d["diagram_name"], "diagram_url": d["diagram_url"], "ref_amount": row["ref_amount"],
            "ref_currency": row["ref_currency"], "ref_tax_note": row["ref_tax_note"], "fetched_at": row["fetched_at"]}


def new_line(part: dict, d: dict, row: dict) -> dict:
    """As the dashboard's lib/catalogue.catalogueLine: named by tidy_name."""
    qty = (row.get("qty") or "").strip()
    return {"item_no": "", "part_name": row.get("tidy_name") or row["name"] or row["part_number"], "condition": "", "action": "",
            "qty": float(qty) if qty.replace(".", "", 1).isdigit() and float(qty) > 0 else 1.0, "qty_text": None,
            "workshop_est": 0, "discount_pct": 0.2, "adjusted_cost": 0, "is_net_item": False,
            "oem_part_number": row["part_number"], "suggested_from": "catalogue (parts-honda.uk)", "usual_unit_price": None,
            "prefilled": False, "origin": "lookup", "source_part": part["source_part"], "stale": False,
            "catalogue_ref": ref_of(row, d)}


def status_by_id(view: dict) -> dict:
    return {r["id"]: r["status"] for _, _, r in rows_of(view)}


def money_texts(amount) -> list[str]:
    if amount is None:
        return []
    return sorted({f"{amount:.2f}", f"{amount:,.2f}"})


def pdf_html(data: dict, report_id: str) -> str:
    """The HTML the PDF is printed from, made by the renderer itself (its
    Chromium step swapped for capturing the page)."""
    import app.rendering.renderer as renderer

    captured = []
    saved = renderer.html_to_pdf
    renderer.html_to_pdf = lambda html, out, slot_wait=None: captured.append(html)
    try:
        renderer.render_pdf(data, [], str(Path(tempfile.gettempdir()) / "e2e-catalogue-unused.pdf"), report_id=report_id,
                            unverified=False)
    finally:
        renderer.html_to_pdf = saved
    return captured[0]


# ------------------------------------------------------------------ the run


def run(args) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="e2e-catalogue-"))
    token = "e2e-" + secrets.token_urlsafe(32)
    SECRETS.append(token)
    env = {
        "STORAGE_DIR": str((tmp / "storage").resolve()),
        "SECRETS_DIR": str((tmp / "secrets").resolve()),
        "DATABASE_URL": f"sqlite:///{(tmp / 'e2e.db').as_posix()}",
        "CATALOGUE_ENABLED": "true",
        "CATALOGUE_WORKER_TOKEN": token,
        "AUTH_COOKIE_SECURE": "false",
        "RUN_BACKGROUND_CLEANUP": "false",
        "TELEGRAM_BOT_TOKEN": "",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
        "GEMINI_API_KEY": "",
        "MIN_FREE_DISK_GB": "0",
        "PYTHONUTF8": "1",
    }
    (tmp / "storage").mkdir()
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))
    port = args.port or free_port()
    base = f"http://127.0.0.1:{port}"
    api_proc = None
    try:
        step(f"1. A throw-away API on {base} (database, storage and secrets in a temporary folder)")
        login = seed(tmp)
        case = login["case"]
        api_proc = start_api(tmp, port, {**os.environ, **env})
        api = Api(base)
        api.login(login["username"], login["password"])
        expect(True, f"signed in as {login['username']} (a test login); case {case}: Honda Vezel 2016, plate {PLATE}, "
               f"damaged: {', '.join(DAMAGE)}")

        step("2. The panel before anything (GET /reports/{id}/catalogue)")
        view = api.get(f"/reports/{case}/catalogue").json()
        expect(view.get("enabled") is True and view.get("can_request") is True and view.get("blocked_reason") is None,
               "enabled, can_request, no blocked reason")
        expect(view.get("worker", {}).get("online") is False, "catalogue worker not seen yet")

        step("3. [Look up in catalogue]")
        r = api.post(f"/reports/{case}/catalogue/lookup", json={"vehicle_key": None})
        expect(r.status_code == 202, "POST lookup: 202", f"{r.status_code} {r.text[:200]}")
        job_id = r.json()["job"]["id"]
        from app.reports.db import SessionLocal
        from app.reports.models import CatalogueJob

        db = SessionLocal()
        try:
            payload = dict(db.get(CatalogueJob, job_id).payload or {})
        finally:
            db.close()
        claimed = {"job_id": job_id, **payload}  # catalogue.job_for_worker: what the claim hands the worker
        shape = {k: (type(v).__name__ if k != "parts" else [sorted(p) for p in v][:1]) for k, v in claimed.items()}
        note(f"claimed job shape: {json.dumps(shape)}")
        note(f"car: {claimed['brand']} {claimed['model']} (model_key {claimed['model_key']}), year {claimed['year']}; "
             f"parts: {[(p['source_part'], p['query'], p['side']) for p in claimed['parts']]}")
        text = json.dumps(claimed).lower()
        leaks = [s for s in (PLATE, VIN, ENGINE, OUR_REF, DRIVER, case, login["name"], login["username"],
                             PLATE.replace(" ", ""), VIN[-6:]) if s.lower() in text]
        expect(not leaks, "the job carries no plate, VIN, engine no., Our Ref, case id or names", str(leaks))
        expect(set(claimed) == {"job_id", "kind", "brand", "model", "model_key", "year", "vehicle_key", "parts"},
               "the job has the contract's keys only", str(sorted(claimed)))

        step("4. The catalogue worker claims it" + (" (skipped: --skip-live posts a canned result)" if args.skip_live else ""))
        if args.skip_live:
            import httpx

            w = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60)
            r = w.post("/catalogue-worker/claim", json={"worker": "e2e-local-pc", "version": "0.1"})
            job = r.json()["job"]
            expect(job and job["job_id"] == job_id, "claimed by the stand-in worker")
            r = w.post(f"/catalogue-worker/jobs/{job_id}/result", json=canned_result(job))
            expect(r.status_code == 200, "canned result accepted", r.text[:200])
            live = {}
        else:
            out = run_worker(args, base, token)
            live = out["live"]
            expect(out["exit"] == 0 and not out["watchdog"], "the worker ran once and stopped by itself",
                   f"exit {out['exit']}, watchdog {out['watchdog']}")
            expect(all(n <= args.max_live for n in live.values()),
                   f"live site requests: {live or 'none (all from the cache)'} (at most {args.max_live} per host)")
            from app.reports.schema import CATALOGUE_HOSTS

            expect(all(h.removeprefix("www.") in CATALOGUE_HOSTS for h in live),
                   "requests to the Microfiches sites only", str(sorted(live)))
            expect(not any(token in line for line in out["log"]), "the token is not in the worker's output")

        step("5. The panel: rows grouped by damaged part")
        view = api.get(f"/reports/{case}/catalogue").json()
        job = view.get("job") or {}
        if not expect(job.get("status") == "done", "the look-up is done", f"{job.get('status')} {job.get('error_code')} {job.get('error_text')}"):
            return finish(tmp, args, api_proc, keep=True)
        vehicle = view.get("vehicle") or {}
        note(f"catalogue car: {vehicle.get('brand')} {vehicle.get('model')} '{vehicle.get('name')}' {vehicle.get('market')} "
             f"({vehicle.get('key')}), twin_of={vehicle.get('twin_of')!r}; {len(view.get('candidates') or [])} candidate(s)")
        expect(vehicle.get("twin_of") == "Vezel", "the car is marked as the Vezel's market twin (twin_of 'Vezel')")
        expect(view["worker"]["online"] is True, "catalogue worker online (ready)")
        # Phase 3: the name a new line takes, colour versions, a diagram shown once.
        rows_now = list(rows_of(view))
        expect(rows_now and all("tidy_name" in r and "variant_key" in r for _, _, r in rows_now)
               and all("same_as_part" in d for p in view["parts"] for d in p["diagrams"]),
               "every row has tidy_name and variant_key, every diagram same_as_part")
        for _, _, r in rows_now[:6]:
            note(f"tidy_name: {r['name']!r} -> {r['tidy_name']!r}")
        groups = {}
        for _, d, r in rows_now:
            if r["variant_key"]:
                groups.setdefault((d["diagram_key"], r["variant_key"]), []).append(r["part_number"])
        shared = [(p["source_part"], d["diagram_name"], d["same_as_part"]) for p in view["parts"] for d in p["diagrams"] if d["same_as_part"]]
        note(f"colour groups: {[(k[1], len(v)) for k, v in groups.items()] or 'none'}; diagrams shown once: {shared or 'none'}")
        if args.skip_live:
            expect([len(v) for v in groups.values()] == [3], "the canned bumper face in 3 colours: one group of 3", str(groups))
            expect(("Rear Bumper Beam", "REAR BUMPER", "Rear Bumper") in shared,
                   "the beam's REAR BUMPER diagram: 'same as Rear Bumper'", str(shared))
        total = 0
        for part in view["parts"]:
            n = sum(len(d["rows"]) for d in part["diagrams"])
            total += n
            note(f"{part['source_part']}: " + "; ".join(
                f"{d['diagram_name']} ({d['match_score']}) {len(d['rows'])} row(s)" for d in part["diagrams"]))
        if view.get("not_found"):
            note(f"not in catalogues: {view['not_found']}")
        if view.get("failures"):
            note(f"failures: {view['failures']}")
        expect(total > 0, f"{total} suggestion(s) on the case, all 'suggested'",
               str({s for s in status_by_id(view).values()}))
        expect(set(status_by_id(view).values()) == {"suggested"}, "every row starts as a suggestion")

        # The rows used below, as a surveyor would pick them: the row whose
        # match hint is the "Rear bumper" line (the bumper skin) onto that
        # line; a tail-lamp fitting with no line of its own (a clip, else a
        # gasket, else any row without a hint) as a new line.
        everything = list(rows_of(view))
        bumper_line = "Rear bumper"
        hint = lambda r: (r.get("match_hint") or {}).get("line_name")  # noqa: E731
        attach = next(((p, d, r) for p, d, r in everything if p["source_part"] == "Rear Bumper" and hint(r) == bumper_line), None) \
            or next(((p, d, r) for p, d, r in everything if p["source_part"] == "Rear Bumper" and "BUMPER" in (r["name"] or "").upper()), None) \
            or next(((p, d, r) for p, d, r in everything if p["source_part"] == "Rear Bumper"), None)
        expect(attach is not None and hint(attach[2]) == bumper_line,
               "a 'Rear Bumper' row carries the match hint 'Rear bumper'", str(attach and attach[2]["name"]))
        lamp = [(p, d, r) for p, d, r in everything if p["source_part"] == "Left Tail Lamp" and not hint(r)]
        added = next((x for word in ("CLIP", "GASKET", "") for x in lamp if (x[2]["name"] or "").upper().startswith(word)), None) \
            or next(((p, d, r) for p, d, r in everything if attach and r["id"] != attach[2]["id"]), None)
        other = next(((p, d, r) for p, d, r in everything if r["id"] not in {x[2]["id"] for x in (attach, added) if x}), None)
        if not expect(attach and added and other, "rows to attach, add and hide were found"):
            return finish(tmp, args, api_proc, keep=True)
        note(f"attach to line 'Rear bumper': {attach[2]['part_number']} {attach[2]['name']!r} "
             f"(match hint: {(attach[2].get('match_hint') or {}).get('line_name')!r})")
        note(f"add as a new line: {added[2]['part_number']} {added[2]['name']!r} ({added[0]['source_part']})")

        step("6. Hide a row, then undo")
        sid = other[2]["id"]
        r = api.post(f"/reports/{case}/catalogue/suggestions/{sid}", json={"action": "reject"})
        expect(r.status_code == 200 and r.json()["suggestion"]["status"] == "rejected", f"row {sid} hidden (rejected)")
        expect(status_by_id(api.get(f"/reports/{case}/catalogue").json())[sid] == "rejected", "GET shows it rejected")
        r = api.post(f"/reports/{case}/catalogue/suggestions/{sid}", json={"action": "undo"})
        expect(r.status_code == 200 and r.json()["suggestion"]["status"] == "suggested", "undo: suggested again")

        step("7. Put one on the 'Rear bumper' line and one on a new line; Save Annex A (PUT /annex-a)")
        report = api.get(f"/reports/{case}").json()
        items = [dict(it) for it in report["data"]["annex_a"]["items"]]
        line = next(it for it in items if it["part_name"] == bumper_line)
        line["catalogue_ref"] = ref_of(attach[2], attach[1])
        line["oem_part_number"] = line.get("oem_part_number") or attach[2]["part_number"]
        items.append(new_line(*added))
        r = api.put(f"/reports/{case}/annex-a", json={"items": items})
        expect(r.status_code == 200, "PUT /annex-a: 200", f"{r.status_code} {r.text[:300]}")
        view = api.get(f"/reports/{case}/catalogue").json()
        statuses = status_by_id(view)
        expect(statuses[attach[2]["id"]] == "accepted" and statuses[added[2]["id"]] == "accepted",
               "GET .../catalogue: both rows accepted")
        decided = {r["id"]: r for _, _, r in rows_of(view)}[attach[2]["id"]]
        expect(bool(decided["decided_by"]) and bool(decided["decided_at"]), f"decided by {decided['decided_by']} at {decided['decided_at']}")
        expect(sum(1 for s in statuses.values() if s == "accepted") == 2, "no other row accepted")
        report = api.get(f"/reports/{case}").json()
        saved = report["data"]["annex_a"]["items"]
        b = next(it for it in saved if it["part_name"] == bumper_line)
        n = next(it for it in saved if it.get("origin") == "lookup")
        expect(b["workshop_est"] == BUMPER_EST, f"'Rear bumper' W/shop Est. unchanged: {b['workshop_est']}")
        expect(b["prefilled"] is True and b["origin"] == "history", "...still the past cases' price (prefilled, history)")
        expect(b["catalogue_ref"]["part_number"] == attach[2]["part_number"] and b["oem_part_number"] == attach[2]["part_number"],
               f"...carrying {attach[2]['part_number']} (ref {b['catalogue_ref']['ref_amount']} {b['catalogue_ref']['ref_currency']})")
        expect(n["workshop_est"] == 0 and n["adjusted_cost"] == 0, f"new line '{n['part_name']}': W/shop Est. 0 (unpriced)")
        review = (report.get("review") or {}).get("annex_a") or []
        names = [it["part_name"] for it in saved]
        expect(review and review[names.index(bumper_line)] == "check",
               "review: the attached past-case price still shows 'check'", str(review))

        step("8. The report the firm sends: no part number, no reference price")
        numbers = [attach[2]["part_number"], added[2]["part_number"]]
        prices = money_texts(attach[2]["ref_amount"]) + money_texts(added[2]["ref_amount"])
        r = api.get(f"/reports/{case}/download.xlsx")
        expect(r.status_code == 200, "Excel workbook downloaded")
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(r.content))
        cells = " | ".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None)
        expect(bumper_line in cells and n["part_name"] in cells, "the workbook lists the Annex A lines (the new one too)")
        expect(not any(x in cells for x in numbers), f"no catalogue part number in the workbook ({', '.join(numbers)})")
        expect(not any(x in cells for x in prices), f"no reference price in the workbook ({', '.join(prices)})")
        html = pdf_html(report["data"], case)
        expect(bumper_line in html, "the PDF's HTML (the renderer's own page) lists the lines")
        expect(not any(x in html for x in numbers), "no catalogue part number in the PDF's HTML")
        expect(not any(x in html for x in prices), "no reference price in the PDF's HTML")
        r = api.get(f"/reports/{case}/download")
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            expect(True, f"the PDF itself renders ({len(r.content)} bytes, via Chromium)")
        else:
            note(f"PDF download answered {r.status_code} (Chromium may be missing here); the HTML check above stands")

        step("9. Take the new line off again; Save Annex A")
        items = [dict(it) for it in saved if it.get("origin") != "lookup"]
        r = api.put(f"/reports/{case}/annex-a", json={"items": items})
        statuses = status_by_id(api.get(f"/reports/{case}/catalogue").json())
        expect(r.status_code == 200 and statuses[added[2]["id"]] == "suggested", "its row is a suggestion again")
        expect(statuses[attach[2]["id"]] == "accepted", "the row on 'Rear bumper' stays accepted")

        if args.hold:
            hold(tmp, args, api, base, login, view)
    finally:
        if api_proc is not None and api_proc.poll() is None:
            api_proc.terminate()
            try:
                api_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                api_proc.kill()
    return finish(tmp, args, None)


def hold(tmp, args, api, base, login, view) -> None:
    """A second case, looked up with the same rows and nothing attached
    yet (for the dashboard's before/after screenshots); the API stays up."""
    import httpx

    from app.reports.db import SessionLocal
    from app.reports.models import Report
    from app.reports.schema import SecurityIncidentDraft
    from app.reports.synthesize_annexes import synthesize_annexes_for_report

    db = SessionLocal()
    try:
        screens = _case(db, login["user_id"], SecurityIncidentDraft, synthesize_annexes_for_report, Report)
        db.commit()
    finally:
        db.close()
    r = api.post(f"/reports/{screens}/catalogue/lookup", json={"vehicle_key": None})
    job_id = r.json()["job"]["id"]
    w = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {SECRETS[0]}"}, timeout=60)
    job = w.post("/catalogue-worker/claim", json={"worker": "e2e-local-pc", "version": "0.1"}).json()["job"]
    keep = ("callout", "part_number", "superseded_from", "name", "name_original", "qty", "side", "notes", "ref_amount",
            "ref_currency", "ref_tax_note", "source", "url", "fetched_at", "stale")
    result = {"status": "done", "error_code": None, "error_text": None, "vehicle": view.get("vehicle"),
              "candidates": view.get("candidates") or [], "failures": view.get("failures") or [],
              "parts": [{"source_part": p["source_part"], "query": p["query"], "side": p["side"], "failures": p.get("failures") or [],
                         "diagrams": [{**{k: d[k] for k in ("diagram_key", "diagram_name", "diagram_url", "image_url", "match_score")},
                                       "rows": [{k: r[k] for k in keep} for r in d["rows"]]} for d in p["diagrams"]]}
                        for p in view["parts"] if p["source_part"] in {x["source_part"] for x in job["parts"]}]}
    r = w.post(f"/catalogue-worker/jobs/{job_id}/result", json=result)
    expect(r.status_code == 200, f"--hold: case {screens} looked up with the same rows")
    info = {"api_base": base, "case": login["case"], "screens_case": screens, "username": login["username"],
            "password": login["password"]}
    (tmp / "hold.json").write_text(json.dumps(info), encoding="utf-8")
    say(f"\n--hold: API up at {base}; login and case ids in {tmp / 'hold.json'}; create {tmp / 'stop'} or Ctrl+C to stop")
    try:
        while not (tmp / "stop").exists():
            # Keep the catalogue worker "ready" for the panel while it is looked at.
            w.post("/catalogue-worker/claim", json={"worker": "e2e-local-pc", "version": "0.1"})
            time.sleep(20)
    except KeyboardInterrupt:
        pass


def finish(tmp: Path, args, api_proc, keep: bool = False) -> int:
    if api_proc is not None and api_proc.poll() is None:
        api_proc.terminate()
    say("")
    if FAILED:
        say(f"E2E: {len(FAILED)} check(s) FAILED: " + "; ".join(FAILED))
    else:
        say("E2E: all checks passed")
    if args.keep or keep or FAILED:
        say(f"temporary folder kept: {tmp}")
    else:
        # This process's own connections to the throw-away database (the
        # seeding and the checks) hold the file open on Windows.
        try:
            from app.reports.db import engine

            engine.dispose()
        except Exception:
            pass
        if api_proc is not None:
            try:
                api_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                api_proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        if tmp.exists():
            say(f"could not remove the temporary folder: {tmp}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run(parse_args()))
