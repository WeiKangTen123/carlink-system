"""A burst of PDF renders must not take the API's threads (release P1
review). Sync routes, and AuthMiddleware's session lookup on every request,
share Starlette's pool of 40 worker threads; renders that each waited two
minutes for one of Chromium's two slots used to fill it, and then nothing
else answered -- 20 render-pdf calls from one account held 18 threads, and
two or three accounts took the site down.

    python scripts/test_render_threads.py

Runs the app under a real uvicorn server on a free local port: TestClient
gives every request its own event loop and thread pool, so it can't show a
shared pool running out. Offline: Chromium is a fake that holds its slot
until the test lets it go, with a temporary database and storage folder.
"""
import os
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="render-threads-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""

import httpx  # noqa: E402
import uvicorn  # noqa: E402

import app.rendering.renderer as renderer  # noqa: E402
from app.api import limits  # noqa: E402
from app.api.main import app  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import Report, gen_id  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

# Three self-signups, 20 render-pdf calls each, all at once: more requests
# than the pool has threads.
ACCOUNTS = 3
CALLS_EACH = 20
# What "answers quickly" means here, for a request that does almost nothing.
QUICK_SECONDS = 1.0

# Chromium: holds its slot until RELEASE is set, then writes a stub PDF.
RELEASE = threading.Event()
IN_CHROMIUM: list[str] = []


def _slow_chromium_pdf(html: str, output_path: str) -> None:
    IN_CHROMIUM.append(output_path)
    RELEASE.wait(60)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


renderer._chromium_pdf = _slow_chromium_pdf


def ok(label: str) -> None:
    print(f"    ok   {label}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def case_for(owner_id: str) -> str:
    """A filed case with no PDF yet, written straight to the database."""
    db = SessionLocal()
    try:
        rid = gen_id()
        db.add(Report(id=rid, channel="manual", reporter_chat_id="dashboard", status="confirmed", owner_id=owner_id,
                      data={"category": ["Other"], "description": "Rear-ended"}))
        db.commit()
        return rid
    finally:
        db.close()


def timed_get(client: httpx.Client, path: str) -> tuple[float, object]:
    """(seconds, response or the error) -- a pool that has run out shows as
    a timeout rather than an answer."""
    started = time.monotonic()
    try:
        answer = client.get(path, timeout=10)
    except httpx.HTTPError as exc:
        answer = exc
    return time.monotonic() - started, answer


def post(client: httpx.Client, path: str):
    try:
        return client.post(path)
    except httpx.HTTPError as exc:
        return exc


def code(answer) -> tuple:
    if isinstance(answer, httpx.Response):
        try:
            return answer.status_code, answer.json().get("code")
        except ValueError:
            return answer.status_code, None
    return "error", type(answer).__name__


def main() -> int:
    init_db()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = threading.Thread(target=server.run, daemon=True)
    serving.start()
    assert wait_until(lambda: server.started, 30), "uvicorn didn't start"
    base = f"http://127.0.0.1:{port}"
    pool = ThreadPoolExecutor(ACCOUNTS * CALLS_EACH)
    burst = []
    try:
        people = []
        for i in range(ACCOUNTS):
            client = httpx.Client(base_url=base, timeout=150, limits=httpx.Limits(max_connections=CALLS_EACH + 5))
            user_id = sign_in(client, role="user", name=f"Rae Render{i}")
            people.append((client, user_id, case_for(user_id)))
        watcher = people[0][0]
        _, answer = timed_get(watcher, "/health")
        assert code(answer) == (200, None), answer

        print(f"{ACCOUNTS} accounts x {CALLS_EACH} render-pdf calls at once, Chromium held")
        burst = [pool.submit(post, client, f"/reports/{case_id}/render-pdf")
                 for client, _, case_id in people for _ in range(CALLS_EACH)]
        assert wait_until(lambda: len(IN_CHROMIUM) == renderer.MAX_CONCURRENT_RENDERS, 15), "renders never reached Chromium"
        time.sleep(0.5)  # the rest of the burst arrives and queues for whatever it waits on

        for path in ("/health", "/auth/me"):
            seconds, answer = timed_get(watcher, path)
            assert code(answer)[0] == 200 and seconds < QUICK_SECONDS, f"{path}: {code(answer)} after {seconds:.1f}s"
        ok(f"GET /health and a signed-in GET /auth/me answer in under {QUICK_SECONDS}s "
           f"with {len(burst)} renders asked for and both Chromium slots held")

        # Everyone but the two in Chromium is answered without waiting for
        # them: refused at once (their own render is in flight), or after the
        # api's short wait for a slot.
        settled = wait_until(lambda: sum(f.done() for f in burst) == len(burst) - renderer.MAX_CONCURRENT_RENDERS,
                             renderer.RENDER_SLOT_WAIT_SECONDS + 5)
        done = [f.result() for f in burst if f.done()]
        assert settled, f"{len(done)} of {len(burst)} answered while Chromium was held"
        assert {code(a) for a in done} == {(429, "render_busy")}, sorted({code(a) for a in done}, key=str)
        ok(f"the other {len(done)} get 429 render_busy, at once or after the api's {renderer.RENDER_SLOT_WAIT_SECONDS}s "
           "wait for a slot, while Chromium still holds both")

        RELEASE.set()
        wait(burst, timeout=30)
        answers = [f.result() for f in burst]
        rendered = [a for a in answers if code(a) == (200, None)]
        assert len(rendered) == renderer.MAX_CONCURRENT_RENDERS, sorted({code(a) for a in answers}, key=str)
        assert all(code(a) in ((200, None), (429, "render_busy")) for a in answers)
        assert sum(len(limits.renders._times.get(uid, ())) for _, uid, _ in people) == len(rendered)
        ok("then the two held renders finish (200); only they count toward anyone's render budget")

        seconds, answer = timed_get(watcher, "/health")
        assert code(answer)[0] == 200 and seconds < QUICK_SECONDS
        client, _, case_id = people[0]
        assert client.post(f"/reports/{case_id}/render-pdf").status_code == 200
        ok("afterwards the same people render again")
    finally:
        RELEASE.set()
        server.should_exit = True
        serving.join(10)
        pool.shutdown(wait=False, cancel_futures=True)

    print("\nALL RENDER-THREAD CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
