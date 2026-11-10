"""call_structured asks a busy model once more (app/ai/structured.py).

    python scripts/test_busy_retry.py

Google sometimes answers 503 "currently experiencing high demand", mostly on
the light models that most reads now run on. One short wait and a second
try on the same model; then the chain moves on as before. Other errors are
not waited for.

Offline: get_client, the rate-limit slot and time.sleep are fakes.
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="busy-retry-test-"))
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSy" + "B" * 33
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"

from app.ai import structured  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.reports.db import init_db  # noqa: E402

PASSED = [0]
FAILURES: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        PASSED[0] += 1
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label} {detail}")


class Busy(Exception):
    status_code = 503

    def __init__(self):
        super().__init__("Error code: 503 - {'error': {'message': 'gemini-3.5-flash-lite is currently experiencing "
                         "high demand, spikes in demand are usually temporary.', 'code': 'service_unavailable'}}")


class BadRequest(Exception):
    status_code = 400

    def __init__(self):
        super().__init__("Error code: 400 - invalid argument")


CALLS: list[str] = []
SLEPT: list[float] = []
PLAN: dict[str, list] = {}


def _create(**kwargs):
    model = kwargs["model"]
    CALLS.append(model)
    step = PLAN[model].pop(0)
    if isinstance(step, Exception):
        raise step
    return SimpleNamespace(output_text=step, usage=None, outputs=[], id="fake")


structured.get_client = lambda key: SimpleNamespace(interactions=SimpleNamespace(create=_create))
structured.acquire = lambda *a, **k: 0.0
structured.time.sleep = lambda s: SLEPT.append(s)


def call(models):
    CALLS.clear()
    SLEPT.clear()
    return structured.call_structured(
        purpose="draft", system_instruction="s", input_parts=[{"type": "text", "text": "x"}],
        schema={"type": "object"}, parse=lambda raw: raw, models=models, actor=AiActor.cli(), timeout=5,
    )


def main() -> int:
    init_db()

    print("busy once, then an answer")
    PLAN.clear()
    PLAN["lite-a"] = [Busy(), '{"ok": 1}']
    PLAN["lite-b"] = ['{"ok": 2}']
    out = call(["lite-a", "lite-b"])
    check(out == '{"ok": 1}', "the same model answers on its second try", repr(out))
    check(CALLS == ["lite-a", "lite-a"], "asked lite-a twice, never lite-b", repr(CALLS))
    check(SLEPT == [structured.OVERLOADED_RETRY_SECONDS], "one short wait before asking again", repr(SLEPT))

    print("busy twice: on to the next model")
    PLAN.clear()
    PLAN["lite-a"] = [Busy(), Busy()]
    PLAN["lite-b"] = ['{"ok": 2}']
    out = call(["lite-a", "lite-b"])
    check(out == '{"ok": 2}', "the next model answers", repr(out))
    check(CALLS == ["lite-a", "lite-a", "lite-b"], "two tries on lite-a, then lite-b", repr(CALLS))
    check(len(SLEPT) == 1, "waited once only", repr(SLEPT))

    print("every model busy twice: the whole chain once more")
    PLAN.clear()
    PLAN["lite-a"] = [Busy(), Busy(), '{"ok": 3}']
    PLAN["lite-b"] = [Busy(), Busy()]
    out = call(["lite-a", "lite-b"])
    check(out == '{"ok": 3}', "the second round's first model answers", repr(out))
    check(CALLS == ["lite-a", "lite-a", "lite-b", "lite-b", "lite-a"], "a, a, b, b, then a again", repr(CALLS))
    check(structured.CHAIN_BUSY_RETRY_SECONDS in SLEPT, "a longer pause before the second round", repr(SLEPT))

    print("busy everywhere twice over: it gives up")
    PLAN.clear()
    PLAN["lite-a"] = [Busy()] * 4
    PLAN["lite-b"] = [Busy()] * 4
    try:
        call(["lite-a", "lite-b"])
        check(False, "raises after the second round")
    except RuntimeError:
        check(len(CALLS) == 8, "two rounds of two tries each, no third", repr(CALLS))

    print("other errors are not waited for")
    PLAN.clear()
    PLAN["lite-a"] = [BadRequest()]
    PLAN["lite-b"] = ['{"ok": 2}']
    out = call(["lite-a", "lite-b"])
    check(out == '{"ok": 2}' and CALLS == ["lite-a", "lite-b"] and SLEPT == [], "a 400 goes straight to the next model",
          repr((CALLS, SLEPT)))

    print("is_overloaded")
    check(structured.is_overloaded(Busy()), "503 high demand is busy")
    check(not structured.is_overloaded(BadRequest()), "400 is not busy")
    check(not structured.is_overloaded(Exception("429 RESOURCE_EXHAUSTED")), "429 is not busy (it has its own wait)")

    print(f"\ntest_busy_retry: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
