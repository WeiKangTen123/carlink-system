"""The model settings for each kind of reading call (app/config.py,
app/ai/structured.py, contract 6): thinking level per call kind, image
resolution per photo kind, tools on the reading call -- and that, left
unset, every request is exactly what it was before they existed.

    python scripts/test_model_settings.py

Covers: the settings' parsing and start-up checks; the request with
nothing set (no generation_config, no tools, no "resolution" on an image);
each call kind getting its own level (by call_structured's purpose,
through the real draft_report / triage_photos / second look); a level for
one model of the chain only; tools on the reading call only; resolution by
kind; thinking tokens and code runs in the run log; and the same through
the real google-genai client on a mock transport: the JSON it sends, byte
for byte with nothing set, and a model that answers 400 to a level falling
through to the next.

Offline: the fake and the mock transport answer; every outside connection
is refused. The key is made up.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = Path(tempfile.mkdtemp(prefix="model-settings-test-"))
(_tmp / "storage").mkdir()
COMPANY_KEY = "AIzaSyModelSettingsTestKeyxxxxxxxxxxxx"
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = COMPANY_KEY
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "100000"
os.environ["SECOND_LOOK_ENABLED"] = "true"
for name in ("GEMINI_THINKING_LEVEL_READ", "GEMINI_THINKING_LEVEL_SORT", "GEMINI_THINKING_LEVEL_SECOND_LOOK",
             "GEMINI_IMAGE_RESOLUTION", "GEMINI_READ_TOOLS"):
    os.environ.pop(name, None)

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import google.genai as _genai  # noqa: E402


class _NoGoogle:
    def __init__(self, *args, **kwargs):
        raise AssertionError("a real Gemini client was built outside the mock transport")


# The real one, for "the real SDK" below: it runs on a mock transport.
_REAL_GENAI_CLIENT = _genai.Client
_genai.Client = _NoGoogle

import app.ai.structured as structured  # noqa: E402
import httpx  # noqa: E402
from google.genai import types as genai_types  # noqa: E402
from PIL import Image  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app import config  # noqa: E402
from app.ai import client as ai_client  # noqa: E402
from app.ai import extraction, keys, runs, triage  # noqa: E402
from app.ai import second_look as sl  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.reports.db import init_db  # noqa: E402

# ------------------------------------------------------------ the fake SDK
# Every request's keyword arguments, in order.
SENT: list[dict] = []
ANSWER = {"text": json.dumps({"ok": True}), "interaction": None}


class _FakeInteractions:
    def create(self, **kwargs):
        SENT.append(kwargs)
        if ANSWER["interaction"] is not None:
            return ANSWER["interaction"]
        return SimpleNamespace(output_text=ANSWER["text"])


_fake_client = SimpleNamespace(interactions=_FakeInteractions())
_real_get_client = structured.get_client
structured.get_client = lambda key: _fake_client
structured.acquire = lambda rate_limit_id: 0.0

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
SETTINGS = ("gemini_thinking_level_read", "gemini_thinking_level_sort", "gemini_thinking_level_second_look",
            "gemini_image_resolution", "gemini_read_tools")
# The keyword arguments call_structured sent before contract 6.
BEFORE = {"model", "system_instruction", "input", "response_format", "timeout"}


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    for field in SETTINGS:
        setattr(settings, field, None)
    SENT.clear()
    ANSWER.update(text=json.dumps({"ok": True}), interaction=None)
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        for field in SETTINGS:
            setattr(settings, field, None)


def photo(name: str, colour: str = "grey") -> str:
    path = _tmp / name
    Image.new("RGB", (40, 30), colour).save(path, "JPEG")
    return str(path)


def call(purpose: str, models=("model-a",), inputs=None):
    return structured.call_structured(
        purpose=purpose, system_instruction="test", input_parts=inputs or [{"type": "text", "text": "hi"}],
        schema={"type": "object"}, parse=json.loads, models=list(models), actor=AiActor.cli(), timeout=5.0,
    )


def setup() -> None:
    init_db()
    check(all(getattr(settings, f) is None for f in SETTINGS), "every model setting is unset by default")


# ---------------------------------------------------------------- settings


def parsing() -> None:
    check(config.thinking_levels(None) == {} and config.thinking_levels(" ") == {}, "unset or blank: no level")
    check(config.thinking_levels("Medium") == {"*": "medium"}, "a bare level is every model's")
    check(config.thinking_levels("gemini-x=high, gemini-y = LOW") == {"gemini-x": "high", "gemini-y": "low"},
          "model=level pairs, case and spaces forgiven")
    check(config.thinking_levels("low,gemini-x=high") == {"*": "low", "gemini-x": "high"}, "a bare level with pairs")
    for bad in ("extreme", "gemini-x=", "gemini-x=dynamic"):
        try:
            config.thinking_levels(bad)
            check(False, f"{bad!r} is refused")
        except ValueError:
            check(True, f"{bad!r} is refused")
    check(config.image_resolutions("damage=ultra_high, Tyre=ULTRA_HIGH,*=medium")
          == {"damage": "ultra_high", "tyre": "ultra_high", "*": "medium"}, "kind=resolution pairs, '*' for the rest")
    for bad in ("damage", "damage=huge", "=low"):
        try:
            config.image_resolutions(bad)
            check(False, f"resolution {bad!r} is refused")
        except ValueError:
            check(True, f"resolution {bad!r} is refused")
    check(config.read_tools("code_execution, code_execution") == ["code_execution"], "tools: code_execution, once")
    try:
        config.read_tools("google_search")
        check(False, "an unknown tool is refused")
    except ValueError:
        check(True, "an unknown tool is refused")

    s = config.Settings(gemini_thinking_level_read=" LOW , gemini-x = HIGH ", gemini_thinking_level_sort="",
                        gemini_image_resolution="Damage=Ultra_High", gemini_read_tools=" code_execution ")
    check((s.gemini_thinking_level_read, s.gemini_thinking_level_sort, s.gemini_image_resolution, s.gemini_read_tools)
          == ("low,gemini-x=high", None, "damage=ultra_high", "code_execution"),
          "Settings keeps each written one way; blank is unset",
          repr((s.gemini_thinking_level_read, s.gemini_thinking_level_sort, s.gemini_image_resolution)))
    for field, bad in (("gemini_thinking_level_second_look", "extreme"), ("gemini_image_resolution", "damage=4k"),
                       ("gemini_read_tools", "url_context")):
        try:
            config.Settings(**{field: bad})
            check(False, f"{field}={bad!r} stops the app at start-up")
        except ValidationError:
            check(True, f"{field}={bad!r} stops the app at start-up")


# ---------------------------------------------------------------- the request


def unset() -> None:
    image = photo("unset.jpg")
    for purpose in ("draft", "photo sort", "second look", "annex suggestions"):
        SENT.clear()
        call(purpose, inputs=[{"type": "text", "text": "Photo P01:"}, structured.image_part(image)])
        check(len(SENT) == 1 and set(SENT[0]) == BEFORE, f"{purpose}: the same keyword arguments as before",
              repr(sorted(SENT[0]) if SENT else None))
    check(set(structured.image_part(image)) == {"type", "data", "mime_type"}, "an image: type, data and mime_type only")
    check(set(structured.image_part(image, kind="damage")) == {"type", "data", "mime_type"},
          "...with a kind too, while no resolution is set")
    check(structured.request_settings("draft", "model-a") == {}, "request_settings is {}")


def by_kind() -> None:
    settings.gemini_thinking_level_read = "medium"
    settings.gemini_thinking_level_sort = "low"
    settings.gemini_thinking_level_second_look = "high"
    want = {"draft": "medium", "photo sort": "low", "second look": "high"}
    for purpose, level in want.items():
        SENT.clear()
        call(purpose)
        check(SENT and SENT[0].get("generation_config") == {"thinking_level": level} and "tools" not in SENT[0],
              f"{purpose}: generation_config thinking_level {level}", repr(SENT[0] if SENT else None))
    SENT.clear()
    call("annex suggestions")
    check(SENT and set(SENT[0]) == BEFORE, "annex suggestions: no setting of any kind")

    settings.gemini_thinking_level_read = "gemini-strong=high"
    SENT.clear()
    ANSWER["text"] = "not json"  # every model fails: both are tried
    try:
        call("draft", models=("gemini-strong", "gemini-lite"))
    except RuntimeError:
        pass
    check([s.get("generation_config") for s in SENT] == [{"thinking_level": "high"}, None],
          "a level for one model: only that model's request carries it", repr([s.get("generation_config") for s in SENT]))
    settings.gemini_thinking_level_read = "medium,gemini-strong=high"
    SENT.clear()
    try:
        call("draft", models=("gemini-strong", "gemini-lite"))
    except RuntimeError:
        pass
    check([s.get("generation_config") for s in SENT] == [{"thinking_level": "high"}, {"thinking_level": "medium"}],
          "...and the bare level for the rest of the chain")

    ANSWER["text"] = json.dumps({"ok": True})
    settings.gemini_read_tools = "code_execution"
    for purpose in ("draft", "photo sort", "second look"):
        SENT.clear()
        call(purpose)
        has = SENT[0].get("tools") if SENT else None
        expected = [{"type": "code_execution"}] if purpose == "draft" else None
        check(has == expected, f"{purpose}: tools {expected}", repr(has))


def real_callers() -> None:
    """The purposes the settings key on are the ones the real callers pass."""
    settings.gemini_thinking_level_read = "medium"
    settings.gemini_thinking_level_sort = "low"
    settings.gemini_thinking_level_second_look = "high"
    settings.gemini_read_tools = "code_execution"
    image = photo("caller.jpg", "red")

    ANSWER["text"] = json.dumps({"description": "Test only.", "damage_summary": []})
    extraction.draft_report("test", [image], actor=AiActor.cli())
    check(len(SENT) == 1 and SENT[0].get("generation_config") == {"thinking_level": "medium"}
          and SENT[0].get("tools") == [{"type": "code_execution"}], "extraction.draft_report: the reading's level and tools",
          repr({k: v for k, v in SENT[0].items() if k in ("generation_config", "tools")} if SENT else None))
    check(all("resolution" not in p for p in SENT[0]["input"] if p.get("type") == "image"),
          "...and its photos no resolution (the reading's builder passes no kind yet)")

    SENT.clear()
    ANSWER["text"] = json.dumps({"photos": [{"label": "P01", "kind": "damage", "area": "rear", "quality": "good",
                                             "shows_damage": True, "part": None, "note": None}]})
    triage.triage_photos([("P01", image)], actor=AiActor.cli())
    check(len(SENT) == 1 and SENT[0].get("generation_config") == {"thinking_level": "low"} and "tools" not in SENT[0],
          "triage.triage_photos: the sorting level, no tools")

    SENT.clear()
    ANSWER["text"] = json.dumps({"items": []})
    sl.look(actor=AiActor.cli(), vehicle={}, listed={"Rear Bumper"},
            cands=[sl.Candidate("Boot Lid", "nearby", "test")], sent=[sl.SentPhoto("P01", "photo-1", image)])
    check(len(SENT) == 1 and SENT[0].get("generation_config") == {"thinking_level": "high"} and "tools" not in SENT[0],
          "second_look.look: the second look's level, no tools")


def resolution() -> None:
    image = photo("res.jpg")
    settings.gemini_image_resolution = "damage=ultra_high,tyre=ultra_high,*=medium"
    got = {kind: structured.image_part(image, kind=kind).get("resolution")
           for kind in ("damage", "tyre", "overview", "unsorted", None)}
    check(got == {"damage": "ultra_high", "tyre": "ultra_high", "overview": "medium", "unsorted": "medium", None: None},
          "by kind; '*' for every other kind; no kind (sorting, second look, bot): none", repr(got))
    settings.gemini_image_resolution = "damage=low"
    check(structured.image_part(image, kind="overview").get("resolution") is None
          and structured.image_resolution("damage") == "low", "a kind not named, with no '*': no resolution")
    settings.gemini_image_resolution = "damage=ultra_high"
    SENT.clear()
    ANSWER["text"] = json.dumps({"photos": []})
    try:
        triage.triage_photos([("P01", image)], actor=AiActor.cli())
    except RuntimeError:
        pass
    check(SENT and all("resolution" not in p for s in SENT for p in s["input"] if p.get("type") == "image"),
          "sorting sends its photos without one, whatever is set")


def tokens() -> None:
    attempts = runs.Attempts()
    ANSWER["interaction"] = SimpleNamespace(
        output_text=json.dumps({"ok": True}),
        usage=SimpleNamespace(total_input_tokens=1200, total_output_tokens=300, total_thought_tokens=450),
        steps=[SimpleNamespace(type="thought"), SimpleNamespace(type="code_execution_call"),
               SimpleNamespace(type="code_execution_result"), SimpleNamespace(type="code_execution_call"),
               SimpleNamespace(type="model_output")],
    )
    with runs.collecting(attempts):
        call("draft")
    items, _ = attempts.drain()
    a = items[0] if items else {}
    check((a.get("prompt_tokens"), a.get("output_tokens"), a.get("thought_tokens"), a.get("tool_calls")) == (1200, 300, 450, 2),
          "the run log's try: prompt, output and thinking tokens, and the code runs", repr(a))
    ANSWER["interaction"] = SimpleNamespace(output_text=json.dumps({"ok": True}),
                                            usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=2))
    with runs.collecting(attempts):
        call("draft")
    items, _ = attempts.drain()
    check(items and "thought_tokens" not in items[0] and "tool_calls" not in items[0],
          "no thinking tokens reported, no code run: neither key (never a made-up 0)", repr(items))


# ---------------------------------------------------------------- the real SDK
BODIES: list[dict] = []
RAW: list[bytes] = []
_sdk_lock = threading.Lock()
REFUSE_LEVEL = {"model": None, "level": None}


def _ok(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "int-1", "status": "completed", "model": model,
        "usage": {"total_input_tokens": 100, "total_output_tokens": 20, "total_thought_tokens": 7},
        "steps": [
            {"type": "code_execution_call", "id": "c1", "arguments": {"code": "print(1)", "language": "python"}},
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps({"ok": True})}]},
        ]})


def _handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    with _sdk_lock:
        BODIES.append(body)
        RAW.append(bytes(request.content))
    model = body.get("model", "")
    if model == REFUSE_LEVEL["model"] and (body.get("generation_config") or {}).get("thinking_level") == REFUSE_LEVEL["level"]:
        return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message":
                                                   "Thinking level MINIMAL is not supported by this model."}})
    return _ok(model)


def images_of(body: dict) -> list[dict]:
    """The image items of a request's JSON: the SDK sends a list of inputs as
    one user_input step holding them as its content."""
    out = []
    for item in body.get("input") or []:
        for part in item.get("content") or [item]:
            if part.get("type") == "image":
                out.append(part)
    return out


def real_sdk() -> None:
    saved = (ai_client.genai.Client, ai_client._HTTP_OPTIONS)
    ai_client.genai.Client = _REAL_GENAI_CLIENT
    ai_client._HTTP_OPTIONS = genai_types.HttpOptions(
        retry_options=saved[1].retry_options, httpx_client=httpx.Client(transport=httpx.MockTransport(_handler)))
    ai_client.reset_client_cache()
    structured.get_client = ai_client.get_client
    image = photo("sdk.jpg", "blue")
    try:
        BODIES.clear()
        RAW.clear()
        inputs = [{"type": "text", "text": "Photo P01:"}, structured.image_part(image, kind="damage")]
        got = call("draft", inputs=inputs)
        check(got == {"ok": True} and len(BODIES) == 1, "nothing set: one request, answered")
        body = BODIES[0]
        check("generation_config" not in body and "tools" not in body and len(images_of(body)) == 1
              and "resolution" not in images_of(body)[0],
              "...its JSON has no generation_config, no tools, no image resolution", repr(sorted(body)))
        # The same call made directly with the keyword arguments of before.
        direct = ai_client.get_client(keys.company_keys()[0])
        direct.interactions.create(model="model-a", system_instruction="test", input=inputs,
                                   response_format={"type": "text", "mime_type": "application/json", "schema": {"type": "object"}},
                                   timeout=5.0)
        check(len(RAW) == 2 and RAW[0] == RAW[1], "...byte for byte the request made with the old arguments")

        settings.gemini_thinking_level_read = "low"
        settings.gemini_read_tools = "code_execution"
        settings.gemini_image_resolution = "damage=ultra_high"
        BODIES.clear()
        attempts = runs.Attempts()
        with runs.collecting(attempts):
            got = call("draft", inputs=[{"type": "text", "text": "Photo P01:"}, structured.image_part(image, kind="damage")])
        body = BODIES[0] if BODIES else {}
        image_items = images_of(body)
        check(body.get("generation_config") == {"thinking_level": "low"}, "the SDK sends generation_config.thinking_level",
              repr(body.get("generation_config")))
        check(body.get("tools") == [{"type": "code_execution"}], "...tools [{type: code_execution}]", repr(body.get("tools")))
        check(image_items and image_items[0].get("resolution") == "ultra_high", "...and the image's resolution",
              repr([{k: v for k, v in p.items() if k != "data"} for p in image_items]))
        check(body.get("response_format", {}).get("schema") == {"type": "object"}, "...beside the JSON schema")
        items, _ = attempts.drain()
        a = items[0] if items else {}
        check(got == {"ok": True} and (a.get("thought_tokens"), a.get("tool_calls")) == (7, 1),
              "the real response's thinking tokens and code run reach the run log", repr(a))

        settings.gemini_thinking_level_read = "minimal"
        settings.gemini_read_tools = None
        REFUSE_LEVEL.update(model="model-strong", level="minimal")
        BODIES.clear()
        got = call("draft", models=("model-strong", "model-lite"))
        check(got == {"ok": True} and [b["model"] for b in BODIES] == ["model-strong", "model-lite"],
              "a model answering 400 to the level: the chain moves on and the next model answers",
              repr([b["model"] for b in BODIES]))
        try:
            ai_client.get_client(keys.company_keys()[0]).interactions.create(
                model="model-strong", input=[{"type": "text", "text": "x"}], generation_config={"thinking_level": "minimal"},
                timeout=5.0)
            check(False, "the 400 is an error")
        except Exception as exc:
            check(keys.refusal(exc) is None, "...a 400 that says nothing about the key (refusal: None)", repr(keys.refusal(exc)))
    finally:
        REFUSE_LEVEL.update(model=None, level=None)
        ai_client.genai.Client, ai_client._HTTP_OPTIONS = saved
        ai_client.reset_client_cache()
        structured.get_client = lambda key: _fake_client


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("the settings", parsing),
        ("unset: the request as before", unset),
        ("thinking level and tools by call kind", by_kind),
        ("the real callers' purposes", real_callers),
        ("image resolution by photo kind", resolution),
        ("tokens and code runs in the run log", tokens),
        ("the real SDK on a mock transport", real_sdk),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
