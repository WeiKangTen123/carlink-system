"""A Gemini call that never answers must not hang the caller.

Offline: a fake client stands in for the SDK. The first model sleeps far
past its timeout (as a real call did, for ~19 hours, when the SDK slept out
a Retry-After), the second fails outright, the third answers. call_structured
has to give up on the first within its deadline and return the third's
answer.

    python scripts/test_gemini_deadline.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.ai.structured as structured  # noqa: E402


class _Interactions:
    def create(self, *, model, timeout, **_):
        if model == "hangs":
            time.sleep(30)
        if model == "fails":
            raise RuntimeError("500 INTERNAL")
        return type("Interaction", (), {"output_text": json.dumps({"model": model})})()


class _Client:
    interactions = _Interactions()


def main() -> int:
    structured.get_client = lambda api_key=None: _Client()
    structured.available_api_keys = lambda: ["test-key"]
    structured.acquire = lambda api_key=None: None
    structured.DEADLINE_MARGIN = 0.5

    started = time.monotonic()
    result = structured.call_structured(
        purpose="deadline test", system_instruction="", input_parts=[], schema={},
        parse=json.loads, models=["hangs", "fails", "answers"], timeout=0.5,
    )
    elapsed = time.monotonic() - started
    assert result == {"model": "answers"}, result
    assert elapsed < 5, f"took {elapsed:.1f}s -- the hung call wasn't abandoned"
    print(f"  ok   hung model abandoned after its deadline; next model answered ({elapsed:.1f}s)")

    try:
        structured.call_structured(
            purpose="deadline test", system_instruction="", input_parts=[], schema={},
            parse=json.loads, models=["hangs"], timeout=0.5,
        )
    except RuntimeError as exc:
        assert isinstance(exc.__cause__, TimeoutError), repr(exc.__cause__)
        print("  ok   a chain of only hung models fails with a timeout instead of hanging")
    else:
        raise AssertionError("expected the chain to fail")

    print("\nALL DEADLINE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
