"""Contract 8 D1, offline: the light reader looking closer -- Gemini zooming
in itself (the code execution tool, per model, with its own timeout and the
zoom hint) and our own close-ups of the damage the sorter boxed.

    python scripts/test_detect_zoom.py

Covers: per-model tools (parsing, the start-up checks, which try carries
them); reading an answer that holds code-execution steps, through fakes and
through the real google-genai client on a mock transport, with the code
runs and the tools' tokens in the run log; the tools' timeout and a timeout
still falling through to the next model; the zoom hint on a try with tools
only; every request exactly as before while every setting is off (the sort
and the reading, compared part by part); the sort's schema, prompt and
version with and without the box; the crop's geometry (grow, minimum size,
kept inside the photo, odd boxes dropped); the close-up files (largest copy
first, EXIF turned, reused, never the photo itself); the "Photo P07:" label
left exactly so, each close-up after its own photo, and the cap; and the
benchmark's flags for all of it.

Offline: fakes and a mock transport answer; every outside connection is
refused. The key is made up; the photos are drawn here.
"""
import base64
import json
import os
import socket
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = Path(tempfile.mkdtemp(prefix="detect-zoom-test-"))
(_tmp / "storage").mkdir()
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyDetectZoomTestKeyxxxxxxxxxxxxxxxxx"
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "100000"
os.environ["SECOND_LOOK_ENABLED"] = "false"
for _name in ("GEMINI_THINKING_LEVEL_READ", "GEMINI_THINKING_LEVEL_SORT", "GEMINI_IMAGE_RESOLUTION", "GEMINI_READ_TOOLS",
              "GEMINI_READ_TOOLS_TIMEOUT", "GEMINI_READ_ZOOM_HINT", "INTAKE_SORT_DAMAGE_BOX", "INTAKE_READ_CROPS",
              "INTAKE_READ_CROPS_MAX"):
    os.environ.pop(_name, None)
# Test models only: the default chain's light models carry a thinking level.
os.environ["GEMINI_MODEL_CHAIN"] = "model-a,model-b"
os.environ["GEMINI_TRIAGE_MODEL_CHAIN"] = "sort-a"
os.environ["GEMINI_THINKING_LEVEL_READ"] = ""

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import google.genai as _genai  # noqa: E402

_REAL_GENAI_CLIENT = _genai.Client


class _NoGoogle:
    def __init__(self, *args, **kwargs):
        raise AssertionError("a real Gemini client was built outside the mock transport")


_genai.Client = _NoGoogle

import app.ai.structured as structured  # noqa: E402
import httpx  # noqa: E402
from google.genai import types as genai_types  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app import config  # noqa: E402
from app.ai import client as ai_client  # noqa: E402
from app.ai import extraction, runs, triage  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import crops, pipeline  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, IntakePhoto, IntakeSession, Report, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import case_id_for_file, generate_preview  # noqa: E402

# ------------------------------------------------------------ the fake SDK
SENT: list[dict] = []
# model -> what that model does: "timeout", "bad" (an answer that doesn't
# parse), an interaction to return, or None (answer by the request's kind).
BEHAVIOUR: dict = {}
SORT_BOXES: dict[str, object] = {}
DRAFT = {"description": "Test only.", "category": ["Vehicle Collision or Damage"], "damage_summary": [],
         "damaged_parts": []}


class _FakeInteractions:
    def create(self, **kwargs):
        SENT.append(kwargs)
        what = BEHAVIOUR.get(kwargs.get("model"))
        if what == "timeout":
            raise TimeoutError("test: no answer in time")
        if what == "bad":
            return SimpleNamespace(output_text="not json")
        if what is not None:
            return what
        schema = kwargs["response_format"]["schema"]
        if "photos" in schema.get("properties", {}):
            labels = [p["text"][6:-1] for p in kwargs["input"] if p.get("type") == "text" and p["text"].startswith("Photo ")]
            photos = [{"label": label, "kind": "damage", "angle": None, "area": "rear", "quality": "good",
                       "shows_damage": True, "part": "Rear Bumper", "note": "test", "damage_box": SORT_BOXES.get(label)}
                      for label in labels]
            return SimpleNamespace(output_text=json.dumps({"batch_sides": None, "photos": photos}))
        return SimpleNamespace(output_text=json.dumps(DRAFT))


_fake_client = SimpleNamespace(interactions=_FakeInteractions())
structured.get_client = lambda key: _fake_client
structured.acquire = lambda rate_limit_id: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
D1 = {"gemini_read_tools": None, "gemini_read_tools_timeout": None, "gemini_read_zoom_hint": False,
      "intake_sort_damage_box": False, "intake_read_crops": False, "intake_read_crops_max": 8}
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


def _reset() -> None:
    for field, value in D1.items():
        setattr(settings, field, value)
    SENT.clear()
    BEHAVIOUR.clear()
    SORT_BOXES.clear()


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    _reset()
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        _reset()


def drawn(name: str, size=(1200, 900), block=None, colour="grey") -> str:
    """A synthetic photo: a plain field, with a red block when given
    (left, top, right, bottom in pixels)."""
    path = _tmp / name
    image = Image.new("RGB", size, colour)
    if block:
        ImageDraw.Draw(image).rectangle(block, fill=(220, 0, 0))
    image.save(path, "JPEG", quality=90)
    return str(path)


def call(purpose="draft", models=("model-a",), parse=json.loads, timeout=5.0, **kw):
    return structured.call_structured(
        purpose=purpose, system_instruction="test prompt", input_parts=[{"type": "text", "text": "hi"}],
        schema={"type": "object"}, parse=parse, models=list(models), actor=AiActor.cli(), timeout=timeout, **kw,
    )


def setup() -> None:
    init_db()
    s = config.Settings()
    check((s.gemini_read_tools, s.gemini_read_tools_timeout, s.gemini_read_zoom_hint, s.intake_sort_damage_box,
           s.intake_read_crops, s.intake_read_crops_max) == (None, None, False, False, False, 8),
          "every contract 8 D1 setting is off by default (close-ups capped at 8 when on)")


# ------------------------------------------------------------ (A) per-model tools


def tools_parsing() -> None:
    check(config.read_tools_by_model(None) == {} and config.read_tools_by_model(" ") == {}, "unset or blank: no tools")
    check(config.read_tools_by_model("code_execution") == {"*": ["code_execution"]}, "a bare tool is every model's")
    got = config.read_tools_by_model("gemini-3.1-flash-lite=code_execution, gemini-3-flash-preview = CODE_EXECUTION")
    check(got == {"gemini-3.1-flash-lite": ["code_execution"], "gemini-3-flash-preview": ["code_execution"]},
          "model=tool pairs, the thinking levels' syntax (case and spaces forgiven)", repr(got))
    got = config.read_tools_by_model("code_execution,gemini-x=code_execution,gemini-x=code_execution")
    check(got == {"*": ["code_execution"], "gemini-x": ["code_execution"]}, "a bare tool beside pairs; a repeat once", repr(got))
    check(config.read_tools("gemini-x=code_execution,code_execution") == ["code_execution"],
          "read_tools: every tool named, once")
    for bad in ("url_context", "gemini-x=", "gemini-x=google_search"):
        try:
            config.read_tools_by_model(bad)
            check(False, f"{bad!r} is refused")
        except ValueError:
            check(True, f"{bad!r} is refused")
    check(config.normal_read_tools(" gemini-x = code_execution ,code_execution") == "gemini-x=code_execution,code_execution"
          and config.normal_read_tools(" code_execution ") == "code_execution",
          "written one way: model=tool, the bare form unchanged")
    s = config.Settings(gemini_read_tools="gemini-3.1-flash-lite = code_execution", gemini_read_tools_timeout="240",
                        intake_read_crops_max=3)
    check((s.gemini_read_tools, s.gemini_read_tools_timeout, s.intake_read_crops_max)
          == ("gemini-3.1-flash-lite=code_execution", 240.0, 3), "Settings keeps them so", repr(s.gemini_read_tools))
    check(config.Settings(gemini_read_tools_timeout="").gemini_read_tools_timeout is None, "a blank timeout is unset")
    for field, bad in (("gemini_read_tools", "gemini-x=url_context"), ("gemini_read_tools_timeout", "0"),
                       ("gemini_read_tools_timeout", "-5"), ("intake_read_crops_max", -1)):
        try:
            config.Settings(**{field: bad})
            check(False, f"{field}={bad!r} stops the app at start-up")
        except ValidationError:
            check(True, f"{field}={bad!r} stops the app at start-up")


def tools_per_model() -> None:
    settings.gemini_read_tools = "model-b=code_execution"
    check("tools" not in structured.request_settings("draft", "model-a"), "a model not named: no tools")
    check(structured.request_settings("draft", "model-b").get("tools") == [{"type": "code_execution"}],
          "the model named: the tool")
    check(structured.request_settings("photo sort", "model-b") == {} and structured.request_settings("second look", "model-b") == {},
          "never on the sort or the second look")
    BEHAVIOUR["model-a"] = "bad"
    got = call(models=("model-a", "model-b"))
    check(got == DRAFT, "the chain moves on as ever")
    check([("tools" in s) for s in SENT] == [False, True], "only model-b's try carries the tool",
          repr([sorted(s) for s in SENT]))
    settings.gemini_read_tools = "code_execution"
    check(all(structured.request_settings("draft", m).get("tools") == [{"type": "code_execution"}] for m in ("model-a", "x")),
          "a bare value: every model, as before")


# ------------------------------------------------------------ (A) the answer


def _steps(*items):
    return [SimpleNamespace(**item) if isinstance(item, dict) else item for item in items]


def _text_step(*texts):
    return SimpleNamespace(type="model_output", content=[SimpleNamespace(type="text", text=t) for t in texts])


def code_answers() -> None:
    answer = json.dumps({"ok": True})
    code = [SimpleNamespace(type="code_execution_call", id="c1", arguments=SimpleNamespace(code="crop()")),
            SimpleNamespace(type="code_execution_result", call_id="c1", result="cropped")]
    cases = {
        "the answer after the code (what output_text normally is)":
            SimpleNamespace(output_text=answer, steps=[_text_step("Let me zoom in."), *code, _text_step(answer)],
                            usage=SimpleNamespace(total_input_tokens=100, total_output_tokens=20, total_tool_use_tokens=900)),
        "a closing remark after a last code step: the answer before it":
            SimpleNamespace(output_text="Done.", steps=[_text_step(answer), *code, _text_step("Done.")]),
        "two text items joined by the SDK ('Here it is:' + JSON)":
            SimpleNamespace(output_text="Here it is:" + answer, steps=[*code, _text_step("Here it is:", answer)]),
        "JSON in a fence inside prose":
            SimpleNamespace(output_text="Result:\n```json\n" + answer + "\n```", steps=[*code, _text_step("Result:\n```json\n" + answer + "\n```")]),
    }
    for label, interaction in cases.items():
        BEHAVIOUR["model-a"] = interaction
        attempts = runs.Attempts()
        with runs.collecting(attempts):
            try:
                got = call()
            except Exception as exc:
                got = f"{type(exc).__name__}: {exc}"
        items, _ = attempts.drain()
        check(got == {"ok": True} and [a["outcome"] for a in items] == ["ok"], label, repr((got, items)))
    BEHAVIOUR["model-a"] = cases["the answer after the code (what output_text normally is)"]
    with runs.collecting(attempts):
        call()
    items, _ = attempts.drain()
    check(items[0].get("tool_calls") == 1 and items[0].get("tool_tokens") == 900,
          "the run log's try: 1 code run and the tools' 900 tokens", repr(items[0]))

    # No code ran: parsed exactly as before -- a bad output_text fails the
    # try even when a step holds good JSON.
    BEHAVIOUR["model-a"] = SimpleNamespace(output_text="Done.", steps=[_text_step(answer), _text_step("Done.")])
    BEHAVIOUR["model-b"] = SimpleNamespace(output_text=json.dumps({"ok": "b"}))
    with runs.collecting(attempts):
        got = call(models=("model-a", "model-b"))
    items, _ = attempts.drain()
    check(got == {"ok": "b"} and [a["outcome"] for a in items] == ["schema", "ok"],
          "no code ran: output_text alone, as before (the try fails as 'schema', the next model answers)", repr(items))
    BEHAVIOUR["model-a"] = SimpleNamespace(output_text="still not json", steps=[*code, _text_step("nothing {here")])
    with runs.collecting(attempts):
        got = call(models=("model-a", "model-b"))
    items, _ = attempts.drain()
    check(got == {"ok": "b"} and [a["outcome"] for a in items] == ["schema", "ok"],
          "code ran and nothing parses: 'schema' and the next model, as before", repr(items))
    check("tool_calls" not in items[1] and "tool_tokens" not in items[1], "no code, no tool tokens: neither key")


# ---- the real SDK on a mock transport
BODIES: list[dict] = []


def _sdk_answer(model: str) -> httpx.Response:
    """A response shaped as the Interactions API returns a code-execution
    run (google-genai 2.22.0's step types): a thought, the model's words,
    the code and its result, an image the code made, then the JSON."""
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n not really").decode("ascii")
    return httpx.Response(200, json={
        "id": "int-zoom", "status": "completed", "model": model,
        "usage": {"total_input_tokens": 4000, "total_output_tokens": 300, "total_thought_tokens": 50,
                  "total_tool_use_tokens": 1200},
        "steps": [
            {"type": "thought", "signature": "c2ln"},
            {"type": "model_output", "content": [{"type": "text", "text": "I'll crop the rear bumper."}]},
            {"type": "code_execution_call", "id": "c1", "arguments": {"code": "img.crop((0, 0, 9, 9))", "language": "python"}},
            {"type": "code_execution_result", "call_id": "c1", "result": "saved crop.png"},
            {"type": "model_output", "content": [{"type": "image", "data": png, "mime_type": "image/png"}]},
            {"type": "code_execution_call", "id": "c2", "arguments": {"code": "print(2)", "language": "python"}},
            {"type": "code_execution_result", "call_id": "c2", "result": "2"},
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps({"ok": model})}]},
        ]})


def real_sdk() -> None:
    saved = (ai_client.genai.Client, ai_client._HTTP_OPTIONS)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        BODIES.append(body)
        return _sdk_answer(body.get("model", ""))

    ai_client.genai.Client = _REAL_GENAI_CLIENT
    ai_client._HTTP_OPTIONS = genai_types.HttpOptions(
        retry_options=saved[1].retry_options, httpx_client=httpx.Client(transport=httpx.MockTransport(handler)))
    ai_client.reset_client_cache()
    structured.get_client = ai_client.get_client
    try:
        settings.gemini_read_tools = "model-b=code_execution"
        interaction = ai_client.get_client(structured.resolve_keys(AiActor.cli())[0]).interactions.create(
            model="model-b", input=[{"type": "text", "text": "x"}], timeout=5.0)
        check(interaction.output_text == json.dumps({"ok": "model-b"}),
              "the SDK's output_text is the final JSON, past the thought, code, results and image",
              repr(interaction.output_text))
        BODIES.clear()
        attempts = runs.Attempts()
        with runs.collecting(attempts):
            got = call(models=("model-b",))
        items, _ = attempts.drain()
        check(got == {"ok": "model-b"}, "call_structured reads that answer", repr(got))
        check(BODIES and BODIES[0].get("tools") == [{"type": "code_execution"}], "the request carried the tool")
        check((items[0].get("tool_calls"), items[0].get("tool_tokens"), items[0].get("thought_tokens")) == (2, 1200, 50),
              "the run log: 2 code runs, 1200 tool tokens, 50 thinking", repr(items[0]))
        BODIES.clear()
        call(models=("model-a",))
        check(BODIES and "tools" not in BODIES[0], "a model not named: its request has no tools")
    finally:
        ai_client.genai.Client, ai_client._HTTP_OPTIONS = saved
        ai_client.reset_client_cache()
        structured.get_client = lambda key: _fake_client


# ------------------------------------------------------------ (A) timeout and hint


def timeout_and_hint() -> None:
    deadlines: list[float] = []
    real_within = structured._create_within

    def within(deadline, create, **kwargs):
        deadlines.append(deadline)
        return real_within(deadline, create, **kwargs)

    structured._create_within = within
    try:
        settings.gemini_read_tools = "model-b=code_execution"
        settings.gemini_read_tools_timeout = 300.0
        settings.gemini_read_zoom_hint = True
        BEHAVIOUR["model-a"] = "timeout"
        attempts = runs.Attempts()
        with runs.collecting(attempts):
            extraction.draft_report("test", [], timeout=120.0, actor=AiActor.cli())
        items, _ = attempts.drain()
        check([a["outcome"] for a in items] == ["timeout", "ok"], "a try that times out falls through to the next model",
              repr([a["outcome"] for a in items]))
        a, b = SENT
        check(a["timeout"] == 120.0 and deadlines[0] == 120.0 + structured.DEADLINE_MARGIN,
              "no tools: the caller's 120 s (and its deadline)", repr((a["timeout"], deadlines)))
        check(b["timeout"] == 300.0 and deadlines[1] == 300.0 + structured.DEADLINE_MARGIN,
              "with tools: gemini_read_tools_timeout's 300 s (and its deadline)", repr((b["timeout"], deadlines)))
        check(a["system_instruction"] == extraction.SYSTEM_PROMPT, "no tools: the prompt as it is, hint or not")
        check(b["system_instruction"] == extraction.SYSTEM_PROMPT + "\n" + extraction.ZOOM_NOTE,
              "with tools: ZOOM_NOTE on its own line after the prompt")
        check(extraction.ZOOM_NOTE.count(". ") <= 2 and "zoom" in extraction.ZOOM_NOTE and "side" in extraction.ZOOM_NOTE,
              "the hint: two sentences or so, zoom before naming the part and its side")

        SENT.clear()
        deadlines.clear()
        settings.gemini_read_tools_timeout = None
        settings.gemini_read_zoom_hint = False
        BEHAVIOUR.clear()
        extraction.draft_report("test", [], timeout=120.0, actor=AiActor.cli(), photo_labels=None)
        check(SENT[0]["timeout"] == 120.0 and "tools" not in SENT[0], "model-a first, no tools")
        BEHAVIOUR["model-a"] = "bad"
        SENT.clear()
        extraction.draft_report("test", [], timeout=120.0, actor=AiActor.cli())
        check(SENT[1]["timeout"] == 120.0 and SENT[1]["system_instruction"] == extraction.SYSTEM_PROMPT,
              "tools but no timeout and no hint set: the caller's timeout and the prompt as it is")

        seen: list[dict] = []
        real_call = extraction.call_structured
        extraction.call_structured = lambda **kw: seen.append(kw) or real_call(**kw)
        try:
            extraction.draft_report("test", [], actor=AiActor.cli())
            settings.gemini_read_zoom_hint = True
            extraction.draft_report("test", [], actor=AiActor.cli())
        finally:
            extraction.call_structured = real_call
        check("tools_note" not in seen[0] and seen[1].get("tools_note") == extraction.ZOOM_NOTE,
              "draft_report passes tools_note only while the hint is on")
    finally:
        structured._create_within = real_within


# ------------------------------------------------------------ (C) the sort


def sort_box() -> None:
    images = [("P01", drawn("s1.jpg")), ("P02", drawn("s2.jpg")), ("P03", drawn("s3.jpg"))]
    SORT_BOXES.update({"P01": [100, 200, 300, 400], "P02": [500, 400, 300, 600], "P03": [0, 0, 1000, 1000]})

    check(triage.sort_prompt_version() == triage.SORT_PROMPT_VERSION and triage.system_prompt() is triage.SYSTEM_PROMPT
          and triage.response_schema() is triage._RESPONSE_SCHEMA, "off: version, prompt and schema are today's, unchanged")
    got = triage.triage_photos(images, actor=AiActor.cli())
    sent = SENT[-1]
    check(sent["system_instruction"] is triage.SYSTEM_PROMPT and sent["response_format"]["schema"] is triage._RESPONSE_SCHEMA
          and set(sent) == BEFORE, "off: the sort is sent today's prompt and schema, nothing else")
    check(all(set(r) == {"kind", "area", "quality", "shows_damage", "part", "note"} for r in got.values()),
          "off: a box the model sends anyway is not kept (the result's keys as before)")

    settings.intake_sort_damage_box = True
    check(triage.sort_prompt_version() != triage.SORT_PROMPT_VERSION, "on: another prompt version",
          triage.sort_prompt_version())
    schema = triage.response_schema()
    item = schema["properties"]["photos"]["items"]
    names = list(item["properties"])
    check(names[names.index("part") + 1] == "damage_box" and "damage_box" in item["required"],
          "on: damage_box right after part, and required", repr(names))
    check("damage_box" not in triage._RESPONSE_SCHEMA["properties"]["photos"]["items"]["properties"],
          "...on a copy: today's schema is untouched")
    prompt = triage.system_prompt()
    check(prompt.replace(triage._BOX_RULE, "") == triage.SYSTEM_PROMPT and prompt.count("damage_box") == 1,
          "on: the prompt is today's plus the one damage_box rule")
    got = triage.triage_photos(images, actor=AiActor.cli())
    check(SENT[-1]["response_format"]["schema"] == schema and SENT[-1]["system_instruction"] == prompt,
          "on: the sort is sent the boxed schema and prompt")
    check((got["P01"]["damage_box"], got["P02"]["damage_box"], got["P03"]["damage_box"])
          == ([100, 200, 300, 400], None, [0, 0, 1000, 1000]),
          "on: boxes kept as whole numbers, an inverted one dropped", repr(got))
    check(triage._clean({"kind": "damage", "shows_damage": False, "damage_box": [1, 2, 3, 4]}, True)["damage_box"] is None,
          "a box on a photo showing no damage is not kept")
    for bad in ([1, 2, 3], [-1, 0, 5, 5], [0, 0, 1001, 5], [5, 5, 5, 9], [0, 0, "9", 9], [True, 0, 5, 5],
                [0, 0, float("nan"), 5], None, {"ymin": 0}):
        check(triage.clean_box(bad) is None, f"clean_box drops {bad!r}")
    check(triage.clean_box([10.4, 20.6, 300, 400]) == [10, 21, 300, 400], "clean_box rounds to whole numbers")

    # The benchmark's sort cache keys on the sort as it is asked now.
    import benchmark_intake as bi

    root = _tmp / "sortcache"
    settings.intake_sort_damage_box = False
    off_dir, off_meta = bi.sort_cache_dir(root)
    check(bi._sort_fields() == bi.SORT_FIELDS, "off: the cache keeps today's fields")
    settings.intake_sort_damage_box = True
    on_dir, on_meta = bi.sort_cache_dir(root)
    check(on_dir != off_dir and on_meta["sort_prompt_version"] == triage.sort_prompt_version()
          and on_meta["prompt_fingerprint"] != off_meta["prompt_fingerprint"],
          "on: a cache folder of its own (version and fingerprint)", repr((on_meta, off_meta)))
    check(bi._sort_fields() == bi.SORT_FIELDS + ("damage_box",), "on: the cache keeps the box too")


# ------------------------------------------------------------ (C) geometry


def geometry() -> None:
    check(crops.crop_window([400, 400, 600, 600], 1000, 1000) == (340, 340, 660, 660),
          "grown 30% of the box each way")
    check(crops.crop_window([500, 500, 510, 510], 2000, 1000) == (885, 380, 1135, 630),
          "a tiny box: at least 25% of the shorter side each way, about its middle")
    check(crops.crop_window([0, 0, 100, 100], 1000, 1000) == (0, 0, 250, 250), "at the top-left edge: moved inside, size kept")
    check(crops.crop_window([900, 900, 1000, 1000], 1000, 1000) == (750, 750, 1000, 1000), "at the bottom-right edge too")
    check(crops.crop_window([0, 100, 1000, 300], 1000, 1000) == (40, 0, 360, 1000),
          "a box the photo's full height: cut to the photo, never past it")
    check(crops.crop_window([100, 100, 900, 900], 1000, 1000) is None,
          "a close-up over half the photo is not worth one: none")
    for bad in ([600, 400, 500, 600], [-5, 0, 100, 100], [0, 0, 1001, 10], [100, 100, 100, 200], ["a", 1, 2, 3],
                None, [1, 2, 3]):
        check(crops.crop_window(bad, 1000, 1000) is None, f"odd box {bad!r}: none")
    check(crops.crop_window([400, 400, 600, 600], 0, 100) is None, "an empty image: none")
    for size in ((3, 3), (1, 1), (5000, 10)):
        try:
            crops.crop_window([0, 0, 1000, 10], *size)
            check(True, f"never raises ({size})")
        except Exception as exc:
            check(False, f"never raises ({size})", repr(exc))


# ------------------------------------------------------------ (C) the files


def crop_files() -> None:
    folder = _tmp / "case"
    folder.mkdir()
    original = drawn("case/orig.jpg", size=(3200, 2400), block=(1500, 1100, 1700, 1300))
    generate_preview(original)
    preview = str(Path(original).with_name("orig_preview.webp"))
    before = (Path(original).read_bytes(), Path(preview).read_bytes())
    box = [450, 450, 550, 550]
    path = crops.make_crop([original, preview], box)
    check(path is not None and Path(path).parent == Path(settings.storage_dir) / "ai_derived" / "crops"
          and len(Path(path).stem) == 64, "a close-up: STORAGE_DIR/ai_derived/crops/<sha256>.jpg", repr(path))
    with Image.open(path) as im:
        size, centre = im.size, im.convert("RGB").getpixel((im.width // 2, im.height // 2))
    check(size == (600, 600), "cut from the original (3200 px), not the 1600 px preview: 600 px", repr(size))
    check(centre[0] > 180 and centre[1] < 60, "the box's damage is in the middle of it", repr(centre))
    check((Path(original).read_bytes(), Path(preview).read_bytes()) == before, "the photo and its preview are untouched")
    check(case_id_for_file("ai_derived/crops/" + Path(path).name) is None, "the folder is never served by /files")

    real_cut = crops._cut
    crops._cut = lambda *a, **k: (_ for _ in ()).throw(AssertionError("cut again"))
    try:
        again = crops.make_crop([original, preview], box)
    finally:
        crops._cut = real_cut
    check(again == path, "the same box again: the file already made, nothing cut")
    other = crops.make_crop([original, preview], [440, 450, 550, 550])
    check(other and other != path, "another box: another file")

    broken = _tmp / "case" / "broken.jpg"
    broken.write_bytes(b"\xff\xd8 not a jpeg at all")
    fallback = crops.make_crop([str(broken), preview], box)
    with Image.open(fallback) as im:
        check(im.size == (300, 300), "an original Pillow can't open: cut from the preview instead", repr(im.size))
    check(crops.make_crop([str(_tmp / "missing.jpg")], box) is None and crops.make_crop([original], [9, 9, 1, 1]) is None
          and crops.make_crop([], box) is None, "a missing file, an odd box, no file: none, never an error")

    # An original stored on its side with EXIF orientation 6, as phones do:
    # the sorter saw the upright preview, so the box is in its frame.
    upright = Image.new("RGB", (300, 400), "grey")
    ImageDraw.Draw(upright).rectangle((0, 0, 100, 100), fill=(220, 0, 0))
    raw = upright.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[0x0112] = 6
    turned = _tmp / "case" / "turned.jpg"
    raw.save(turned, "JPEG", quality=90, exif=exif.tobytes())
    generate_preview(str(turned))
    path = crops.make_crop([str(turned)], [0, 0, 250, 333])
    with Image.open(path) as im:
        pixel = im.convert("RGB").getpixel((im.width // 4, im.height // 4))
    check(pixel[0] > 180 and pixel[1] < 60, "an EXIF-rotated original is turned upright before it is cut", repr(pixel))


# ------------------------------------------------------------ (C) the reading


def make_case(specs: list[tuple]) -> str:
    """A draft whose photos are already sorted: (kind, shows_damage, box)
    each, drawn with a red block where the box is."""
    db = SessionLocal()
    try:
        report = Report(channel="manual", reporter_chat_id="test", status="intake",
                        data=SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump())
        db.add(report)
        db.flush()
        db.add(IntakeSession(report_id=report.id, reporter_notes="Rear-ended at a junction."))
        for position, (kind, damage, box) in enumerate(specs):
            photo_id = gen_id()
            path = drawn(f"{photo_id}.jpg", size=(1600, 1200), block=(700, 500, 800, 600), colour=(90, 90, 90 + position))
            generate_preview(path)
            db.add(IntakePhoto(id=photo_id, report_id=report.id, position=position, original_name=f"IMG_{position:04d}.jpg",
                               path=path, sha256=f"{position:064d}", size_bytes=1, kind=kind, area="rear", quality="good",
                               shows_damage=damage, part="Rear Bumper" if damage else None, note=None, damage_box=box))
        db.commit()
        return report.id
    finally:
        db.close()


def _draft_request() -> dict:
    drafts = [s for s in SENT if "photos" not in s["response_format"]["schema"].get("properties", {})]
    return drafts[-1]


def _read(rid: str) -> dict:
    SENT.clear()
    pipeline._read(rid, AiActor.cli())
    return _draft_request()


BOX = [420, 440, 500, 500]
SPECS = [("log_card", False, None), ("overview", True, BOX), ("damage", True, BOX), ("damage", True, [600, 500, 400, 700]),
         ("damage", True, BOX), ("third_party", True, BOX), ("damage", False, BOX), ("damage", True, BOX),
         ("damage", True, None), ("damage", True, BOX), ("tyre", True, BOX)]


def off_identical() -> None:
    """Every setting off: the sort and the reading as before -- also with
    boxes already stored on the photos, and with the settings that only
    act with tools (timeout, hint) set."""
    rid = make_case(SPECS)
    first = _read(rid)
    check(set(first) == BEFORE and first["system_instruction"] is extraction.SYSTEM_PROMPT and first["timeout"] == 120.0,
          "the reading: today's keyword arguments, prompt and 120 s", repr(sorted(first)))
    db = SessionLocal()
    try:
        photos = pipeline.ordered_photos(db, rid)
        labels = {p.id: pipeline.photo_label(i) for i, p in enumerate(photos)}
        chosen = pipeline.select_for_reading(photos)
        hints = [{"kind": p.kind, "area": p.area, "part": p.part, "note": p.note} for p in chosen]
        by_hand = extraction._build_input("Rear-ended at a junction.", [pipeline.ai_input_path(p.path) for p in chosen],
                                          None, [labels[p.id] for p in chosen], hints)
    finally:
        db.close()
    check(first["input"] == by_hand, "...its input part for part what _build_input made before (no close-up)")
    check(first["response_format"] == {"type": "text", "mime_type": "application/json",
                                       "schema": extraction._response_schema()}, "...and its response format")

    settings.gemini_read_tools_timeout = 300.0
    settings.gemini_read_zoom_hint = True
    settings.intake_read_crops_max = 2
    second = _read(rid)
    check(second == first, "the tools' timeout and hint, with no tools: the very same request")
    settings.intake_read_crops = True
    settings.intake_read_crops_max = 0
    third = _read(rid)
    check(third == first, "close-ups on with a cap of 0: the very same request")

    # The sort, every setting off: today's request, and a box already on a
    # photo is left as it is.
    settings.intake_read_crops = False
    db = SessionLocal()
    try:
        for p in pipeline.ordered_photos(db, rid):
            p.kind = None
        db.commit()
    finally:
        db.close()
    SENT.clear()
    pipeline._sort(rid, AiActor.cli())
    sorts = [s for s in SENT if "photos" in s["response_format"]["schema"].get("properties", {})]
    check(sorts and all(s["system_instruction"] is triage.SYSTEM_PROMPT and s["response_format"]["schema"] is triage._RESPONSE_SCHEMA
                        and set(s) == BEFORE for s in sorts), "the sort: today's prompt, schema and arguments")
    db = SessionLocal()
    try:
        kept = [p.damage_box for p in pipeline.ordered_photos(db, rid)]
    finally:
        db.close()
    check(kept == [s[2] for s in SPECS], "...and the boxes stored before are left as they were")

    settings.intake_sort_damage_box = True
    SORT_BOXES.update({"P01": [10, 10, 50, 50], "P02": [900, 10, 100, 50]})
    db = SessionLocal()
    try:
        for p in pipeline.ordered_photos(db, rid):
            p.kind = None
        db.commit()
    finally:
        db.close()
    pipeline._sort(rid, AiActor.cli())
    db = SessionLocal()
    try:
        boxes = [p.damage_box for p in pipeline.ordered_photos(db, rid)]
    finally:
        db.close()
    check(boxes[0] == [10, 10, 50, 50] and boxes[1] is None and boxes[2] is None,
          "with the box on, sorting stores the sorter's (sensible) boxes on the photos", repr(boxes[:3]))


def reading_crops() -> None:
    rid = make_case(SPECS)
    settings.intake_read_crops = True
    settings.intake_read_crops_max = 3
    sent = _read(rid)
    parts = sent["input"]
    texts = [p.get("text") for p in parts]
    labels = [t for t in texts if t and t.startswith("Photo ")]
    closeups = [i for i, t in enumerate(texts) if t and t.startswith("Close-up of photo ")]
    check(all(t == f"Photo {t[6:-1]}:" and len(t) == len("Photo P07:") for t in labels),
          "every photo's label is exactly 'Photo P07:'", repr(labels))
    check(all(parts[texts.index(t) + 1]["type"] == "image" for t in labels), "...and is followed by its photo")
    check(len(closeups) == 3, "the cap: 3 close-ups", repr([texts[i] for i in closeups]))
    for i in closeups:
        label = texts[i][len("Close-up of photo "):-1]
        before = parts[i - 1]
        check(texts[i - 2] == f"Photo {label}:" and before["type"] == "image" and parts[i + 1]["type"] == "image"
              and parts[i + 1]["data"] != before["data"],
              f"'Close-up of photo {label}:' comes right after photo {label}'s image, with an image of its own")
    check(texts.count(extraction.CLOSE_UP_NOTE) == 1 and texts.index(extraction.CLOSE_UP_NOTE) < texts.index(labels[0]),
          "the close-up note once, before the photos")
    db = SessionLocal()
    try:
        photos = pipeline.ordered_photos(db, rid)
        kind_of = {pipeline.photo_label(i): p.kind for i, p in enumerate(photos)}
        run = db.query(AiRun).filter(AiRun.report_id == rid, AiRun.kind == "read").order_by(AiRun.started_at.desc()).first()
    finally:
        db.close()
    cut = [texts[i][len("Close-up of photo "):-1] for i in closeups]
    check(all(kind_of[label] == "damage" for label in cut), "damage close-ups first", repr(cut))
    check(run is not None and run.photos_sent == [t[6:-1] for t in labels],
          "the run log's photos are the photos' labels only (filing's relabel needs them so)")

    settings.intake_read_crops_max = 8
    parts = _read(rid)["input"]
    texts = [p.get("text") for p in parts]
    cut = {t[len("Close-up of photo "):-1] for t in texts if t and t.startswith("Close-up of photo ")}
    expected = {label for label, kind in kind_of.items()} - {"P01", "P04", "P06", "P07", "P09"}
    check(cut == expected, "with room for all: every photo of the insured car showing damage with a sensible box, "
                           "never the other party's car, an inverted box, no box or no damage", repr(sorted(cut)))
    every = [t for t in texts if t and t.startswith("Close-up of photo ")]
    check(len(every) == len(cut), "one close-up per photo")

    # A close-up that can't be cut gives its place to the next one.
    settings.intake_read_crops_max = 2
    real = crops.make_crop
    crops.make_crop = lambda sources, box: None if sources[0].endswith(photos[2].path[-20:]) else real(sources, box)
    try:
        texts = [p.get("text") for p in _read(rid)["input"]]
    finally:
        crops.make_crop = real
    check(sum(1 for t in texts if t and t.startswith("Close-up of photo ")) == 2 and "Close-up of photo P03:" not in texts,
          "a close-up that can't be made: the next photo gets the place")


# ------------------------------------------------------------ the benchmark


def benchmark_flags() -> None:
    import benchmark_intake as bi

    bi._app()  # its accounting wraps call_structured and the run log, as in a benchmark run
    args = bi.parse_args(["--read-tools", "gemini-3.1-flash-lite=code_execution", "--read-tools-timeout", "240",
                          "--zoom-hint", "--sort-damage-box", "--read-crops"])
    got = bi.model_settings(args)
    check(got == {"gemini_read_tools": "gemini-3.1-flash-lite=code_execution", "gemini_read_tools_timeout": 240.0,
                  "gemini_read_zoom_hint": True, "intake_sort_damage_box": True, "intake_read_crops": True},
          "every flag to its setting (--read-crops alone keeps the setting's cap)", repr(got))
    got = bi.model_settings(bi.parse_args(["--sort-damage-box", "--read-crops", "5"]))
    check(got.get("intake_read_crops_max") == 5, "--read-crops N sets the cap")
    for bad, says in ((["--zoom-hint"], "--zoom-hint"), (["--read-tools-timeout", "200"], "--read-tools-timeout"),
                      (["--read-crops"], "--sort-damage-box"), (["--read-tools", "x=url_context"], "--read-tools")):
        try:
            bi.model_settings(bi.parse_args(bad))
            check(False, f"{bad} is refused")
        except SystemExit as exc:
            check(says in str(exc.code), f"{bad} is refused ({exc.code})")
    for bad in (["--read-tools-timeout", "0"], ["--read-crops", "0"]):
        try:
            bi.parse_args(bad)
            check(False, f"{bad} is refused")
        except SystemExit:
            check(True, f"{bad} is refused")

    check(bi.ai_settings(settings) == {"thinking_level_read": settings.gemini_thinking_level_read,
                                       "thinking_level_sort": settings.gemini_thinking_level_sort,
                                       "thinking_level_second_look": settings.gemini_thinking_level_second_look,
                                       "image_resolution": settings.gemini_image_resolution, "read_tools": None},
          "all off: ai_settings as before (none of these keys)", repr(bi.ai_settings(settings)))
    settings.gemini_read_tools = "code_execution"
    settings.gemini_read_tools_timeout = 240.0
    settings.gemini_read_zoom_hint = True
    settings.intake_sort_damage_box = True
    settings.intake_read_crops = True
    got = bi.ai_settings(settings)
    check({k: got.get(k) for k in ("read_tools", "read_tools_timeout", "read_zoom_hint", "intake_sort_damage_box",
                                   "intake_read_crops", "intake_read_crops_max")}
          == {"read_tools": "code_execution", "read_tools_timeout": 240.0, "read_zoom_hint": True,
              "intake_sort_damage_box": True, "intake_read_crops": True, "intake_read_crops_max": 8},
          "on: each in ai_settings", repr(got))

    parts = [{"type": "text", "text": "Photo P01:"}, {"type": "image", "data": "x"},
             {"type": "text", "text": "Close-up of photo P01:"}, {"type": "image", "data": "y"}]
    bi.ACCOUNTING.clear()
    counted = bi._counting(lambda **kw: runs.note_attempt({"model": "m", "outcome": "ok", "ms": 10, "tool_calls": 3,
                                                          "tool_tokens": 700}))
    counted(purpose="draft", input_parts=parts)
    call = bi.ACCOUNTING.call_list()[0]
    check((call.get("crops"), call.get("images"), call.get("tool_calls"), call.get("tool_tokens")) == (1, {"default": 2}, 3, 700),
          "a call notes its close-ups, images, code runs and the tools' tokens", repr(call))
    m = bi.run_metrics({"parts": bi.score_parts([], []), "vehicle": {}, "accounting": bi.ACCOUNTING.summary(),
                        "ai_calls": bi.ACCOUNTING.call_list()})
    check((m.get("read_crops"), m.get("read_code_runs"), m.get("read_tok_tool")) == (1, 3, 700),
          "run metrics: read_crops, read_code_runs, read_tok_tool", repr(m))


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("(A) per-model tools: the setting", tools_parsing),
        ("(A) per-model tools: which try carries them", tools_per_model),
        ("(A) an answer with code-execution steps (fakes)", code_answers),
        ("(A) the same through the real SDK on a mock transport", real_sdk),
        ("(A) the tools' timeout and the zoom hint", timeout_and_hint),
        ("(C) the sort's damage box", sort_box),
        ("(C) the close-up's geometry", geometry),
        ("(C) the close-up files", crop_files),
        ("every setting off: the requests as before", off_identical),
        ("(C) the reading's close-ups and their cap", reading_crops),
        ("the benchmark's flags", benchmark_flags),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
