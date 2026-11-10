"""Reference examples inside the reading request (app/ai/read_examples.py,
extraction._build_input, intake/pipeline._read_into): a few labelled photos
from other cases, sent before the case's own on the intake reading, so the
light reader learns the firm's conventions from them.

Covers: unset, the request exactly as before (a blank setting is unset);
the manifest's checks, and a bad manifest reading on without examples (a
warning, never an error); the parts in order -- intro, "Example E1 (not
this case): <caption>" and its photo at Gemini's default resolution, the
end line, then the case as before -- and the system prompt's one more line;
the cost logged once a reading; no photo_reference pointing at an example
(the reading's own check, which leaves out an item seen on one; the
pipeline's label map; the second look's); the intake reading sends them,
the sorter, the second look and the bots' drafts don't; the benchmark's
--read-examples with leave-one-out, recorded per case.

    python scripts/test_read_examples.py

Offline: Gemini is a fake and every outside connection is refused. The
photos are tiny squares made here; cases and captions are made up.
"""
import base64
import contextlib
import io
import json
import logging
import os
import re
import socket
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = Path(tempfile.mkdtemp(prefix="read-examples-test-"))
(_tmp / "storage").mkdir()
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyReadExamplesTestKeyxxxxxxxxxxxxxx"
for name in ("GEMINI_READ_EXAMPLES", "GEMINI_IMAGE_RESOLUTION", "SECOND_LOOK_ENABLED", "BENCHMARK_SCRATCH"):
    os.environ.pop(name, None)

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import app.ai.structured as structured  # noqa: E402
from PIL import Image  # noqa: E402

from app import config  # noqa: E402
from app.ai import extraction, read_examples  # noqa: E402
from app.ai import second_look as sl  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.ai.read_examples import Examples, ManifestError  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, IntakePhoto, IntakeSession, Report, gen_id  # noqa: E402

# ------------------------------------------------------------ the fake SDK
# Every request's keyword arguments, in order; the reading's answer.
SENT: list[dict] = []
ANSWER = {"draft": json.dumps({"description": "Test only.", "damage_summary": []}), "fail_model": None}


def _labels(parts: list[dict]) -> list[str]:
    return [m.group(1) for p in parts if p.get("type") == "text" for m in [re.match(r"Photo (P\d+):$", p["text"])] if m]


class _FakeInteractions:
    def create(self, **kwargs):
        SENT.append(kwargs)
        props = kwargs["response_format"]["schema"].get("properties", {})
        if "damage_summary" in props:
            if kwargs["model"] == ANSWER["fail_model"]:
                raise RuntimeError("500 INTERNAL: test failure")
            return SimpleNamespace(output_text=ANSWER["draft"])
        if "items" in props:  # the second look
            return SimpleNamespace(output_text=json.dumps({"items": []}))
        if "photos" in props:  # the sorter
            return SimpleNamespace(output_text=json.dumps({"photos": [
                {"label": label, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True,
                 "part": "Rear Bumper", "note": None} for label in _labels(kwargs["input"])]}))
        raise AssertionError(f"unexpected call: {sorted(props)}")


_fake_client = SimpleNamespace(interactions=_FakeInteractions())
structured.get_client = lambda key: _fake_client
structured.acquire = lambda rate_limit_id: 0.0

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
# The keyword arguments call_structured sends a model with no generation
# setting (the chain below names none of the lite models).
BEFORE = {"model", "system_instruction", "input", "response_format", "timeout"}
CHAIN = "model-a,model-b"


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
    saved = (settings.gemini_read_examples, settings.gemini_image_resolution, settings.gemini_model_chain,
             settings.second_look_enabled)
    settings.gemini_read_examples = None
    settings.gemini_image_resolution = None
    settings.gemini_model_chain = CHAIN
    settings.second_look_enabled = False
    SENT.clear()
    ANSWER.update(draft=json.dumps({"description": "Test only.", "damage_summary": []}), fail_model=None)
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        (settings.gemini_read_examples, settings.gemini_image_resolution, settings.gemini_model_chain,
         settings.second_look_enabled) = saved
        read_examples.HELD_OUT = ()


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def logged(name: str):
    """The log records of one logger while the block runs."""
    handler, logger = _Records(), logging.getLogger(name)
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def square(path: Path, colour: str, fmt: str = "JPEG") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 16), colour).save(path, fmt)
    return path


def write_manifest(folder: Path, data) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "manifest.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def texts(parts: list[dict]) -> list[str]:
    return [p["text"] for p in parts if p.get("type") == "text"]


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


# Two examples next to their manifest: one JPEG with a case tag, one PNG.
EXAMPLES_DIR = _tmp / "examples"
E1 = square(EXAMPLES_DIR / "photos" / "e1.jpg", "orange")
E2 = square(EXAMPLES_DIR / "photos" / "e2.png", "purple", "PNG")
CAPTION_1 = "Car's front faces the camera: its RIGHT side is on the photo's LEFT. Damage here is Right."
CAPTION_2 = "A scraped rim is the Wheel (Grazed); tyre marks are the Tyre."
GOOD = write_manifest(EXAMPLES_DIR, {"examples": [
    {"id": "e1", "image": "photos/e1.jpg", "case": "Test Case One", "caption": f"  {CAPTION_1}  "},
    {"id": "e2", "image": "photos/e2.png", "caption": CAPTION_2},
]})
# The case's own photos.
A = square(_tmp / "case" / "a.jpg", "red")
B = square(_tmp / "case" / "b.jpg", "blue")
SELECTION = ("Only a selection of this case's photos is shown below. Each is labelled with its case photo number -- "
             "use exactly that label for 'photo_reference'.")


def new_case(colours: tuple[str, ...], sorted_: bool = True) -> tuple[str, list[str]]:
    """An intake draft with one photo per colour, sorted as rear damage
    (or not sorted at all)."""
    rid = gen_id()
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=rid, reporter_notes="Rear hit at a carpark."))
        ids = []
        for i, colour in enumerate(colours):
            path = square(_tmp / "cases" / rid / f"{i}.jpg", colour)
            photo_id = gen_id()
            fields = dict(kind="damage", area="rear", part="Rear Bumper", shows_damage=True, quality="good") if sorted_ else {}
            db.add(IntakePhoto(id=photo_id, report_id=rid, position=i, original_name=f"{i}.jpg", path=str(path),
                               sha256=f"{rid}{i}", **fields))
            ids.append(photo_id)
        db.commit()
    finally:
        db.close()
    return rid, ids


def stored(rid: str) -> tuple[dict, list[str], list]:
    """The draft's data, the photos the session read, and its read runs'
    photos_sent."""
    db = SessionLocal()
    try:
        report, session = db.get(Report, rid), db.get(IntakeSession, rid)
        sent = [r.photos_sent for r in db.query(AiRun).filter_by(report_id=rid, kind="read").all()]
        return report.data or {}, list(session.read_photo_ids or []), sent
    finally:
        db.close()


def drafts() -> list[dict]:
    return [s for s in SENT if "damage_summary" in s["response_format"]["schema"].get("properties", {})]


def example_images(parts: list[dict]) -> int:
    data = {b64(E1), b64(E2)}
    return sum(1 for p in parts if p.get("type") == "image" and p.get("data") in data)


# ---------------------------------------------------------------- sections


def setup() -> None:
    init_db()
    check(config.Settings().gemini_read_examples is None, "gemini_read_examples is unset by default")


def unset() -> None:
    check(read_examples.for_reading() is None, "unset: for_reading gives no examples")
    for blank in ("", "   "):
        check(config.Settings(gemini_read_examples=blank).gemini_read_examples is None,
              f"a blank setting ({blank!r}) is unset")

    # Today's request for a selection of a case, written out: nothing added.
    parts = extraction._build_input("Rear hit", [str(A), str(B)], None, ["P03", "P07"])
    want = [text("Reporter's description:\nRear hit"), text(SELECTION), text("Photo P03:"), structured.image_part(str(A)),
            text("Photo P07:"), structured.image_part(str(B))]
    check(parts == want, "_build_input with no examples: the request as before, part for part",
          json.dumps(texts(parts)))
    check(extraction._build_input("Rear hit", [str(A)], {"location": "x"}, None, None, None)
          == extraction._build_input("Rear hit", [str(A)], {"location": "x"}),
          "examples=None is the same as not passing it")
    check(extraction._build_input("d", [str(A)], examples=Examples("intro", ())) == extraction._build_input("d", [str(A)]),
          "no examples left (every one held out): nothing of them either")

    # The pipeline's reading, setting unset: the call's arguments are exactly
    # the old ones.
    rid, _ = new_case(("red", "green"))
    pipeline._read(rid, AiActor.cli())
    sent = drafts()
    check(len(sent) == 1 and set(sent[0]) == BEFORE, "the intake reading sends the same keyword arguments as before",
          repr(sorted(sent[0]) if sent else None))
    check(sent and sent[0]["system_instruction"] == extraction.SYSTEM_PROMPT, "...the system prompt unchanged")
    inputs = sent[0]["input"] if sent else []
    check(texts(inputs)[0] == "Reporter's description:\nRear hit at a carpark." and _labels(inputs) == ["P01", "P02"]
          and sum(1 for p in inputs if p.get("type") == "image") == 2 and not any("Example" in t for t in texts(inputs)),
          "...and its input the description, the selection note and the case's two photos, nothing else",
          json.dumps(texts(inputs)))


def manifest() -> None:
    ex = read_examples.load(GOOD)
    check(ex.ids == ["e1", "e2"] and len(ex) == 2, "a good manifest: its examples, in order")
    check(ex.items[0].image == str(E1.resolve()) and Path(ex.items[1].image).is_file(),
          "...each image found relative to the manifest, as an absolute path")
    check(ex.items[0].caption == CAPTION_1 and ex.items[0].case == "Test Case One" and ex.items[1].case is None,
          "...captions trimmed; the case tag optional")
    check(ex.intro == read_examples.DEFAULT_INTRO, "...no intro: the default one")
    custom = write_manifest(_tmp / "custom", {"intro": "  Our firm's habits.  ", "examples": [
        {"id": "x", "image": str(E1.resolve()), "caption": "Lesson."}]})
    check(read_examples.load(custom).intro == "Our firm's habits." and read_examples.load(custom).items[0].image == str(E1.resolve()),
          "an intro of its own is used; an absolute image path stays as it is")
    six = write_manifest(_tmp / "six", {"examples": [
        {"id": f"s{i}", "image": str(E1.resolve()), "caption": "Lesson."} for i in range(6)]})
    check(len(read_examples.load(six)) == 6, f"{read_examples.MAX_EXAMPLES} examples are allowed")

    note = _tmp / "bad" / "notes.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("not a photo", encoding="utf-8")
    one = {"id": "b1", "image": str(E1.resolve()), "caption": "Lesson."}
    bad = [
        ("not JSON", "{", "is not JSON"),
        ("no examples list", {"intro": "x"}, 'needs {"examples"'),
        ("an empty list", {"examples": []}, "no examples"),
        ("seven examples", {"examples": [dict(one, id=f"b{i}") for i in range(7)]}, "at most 6"),
        ("an example that isn't an object", {"examples": ["e1"]}, "must be an object"),
        ("an example without an id", {"examples": [{"image": one["image"], "caption": "x"}]}, "no id"),
        ("an id used twice", {"examples": [one, one]}, "id used twice"),
        ("a blank caption", {"examples": [dict(one, caption="   ")]}, "no caption"),
        ("no caption at all", {"examples": [{"id": "b1", "image": one["image"]}]}, "no caption"),
        ("no image named", {"examples": [{"id": "b1", "caption": "x"}]}, "no image"),
        ("an image that isn't there", {"examples": [dict(one, image="photos/missing.jpg")]}, "no image at"),
        ("a file that isn't a photo", {"examples": [dict(one, image=str(note))]}, "is not a photo"),
        ("a case that isn't text", {"examples": [dict(one, case=5)]}, "case must be text"),
        ("an intro that isn't text", {"intro": 5, "examples": [one]}, "intro: must be text"),
    ]
    for n, (label, data, says) in enumerate(bad):
        path = write_manifest(_tmp / "bad" / str(n), data)
        try:
            read_examples.load(path)
            check(False, f"{label}: refused")
            continue
        except ManifestError as exc:
            check(says in str(exc), f"{label}: refused ({exc})")
        settings.gemini_read_examples = str(path)
        with logged("app.ai.read_examples") as records:
            got = read_examples.for_reading()
        check(got is None and any(r.levelno == logging.WARNING for r in records),
              "...and a reading with it set goes on without examples, a warning in the log")
    try:
        read_examples.load(_tmp / "nowhere" / "manifest.json")
        check(False, "a manifest that isn't there: refused")
    except ManifestError as exc:
        check("can't read" in str(exc), "a manifest that isn't there: refused")

    # Through the whole intake reading: a bad manifest never breaks it.
    settings.gemini_read_examples = str(_tmp / "bad" / "0" / "manifest.json")
    ANSWER["draft"] = json.dumps({"description": "Rear hit.", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "P01"}]})
    rid, ids = new_case(("red",))
    SENT.clear()
    with logged("app.ai.read_examples") as records:
        pipeline._read(rid, AiActor.cli())
    data, _, _ = stored(rid)
    sent = drafts()
    check([it.get("part") for it in data.get("damage_summary") or []] == ["Rear Bumper"]
          and data["damage_summary"][0]["photo_reference"] == f"@{ids[0]}",
          "an intake reading with a bad manifest is read and saved as usual")
    check(sent and sent[0]["system_instruction"] == extraction.SYSTEM_PROMPT and not example_images(sent[0]["input"])
          and records, "...with no examples, the system prompt as before, and a warning")


def request_parts() -> None:
    examples = read_examples.load(GOOD)
    settings.gemini_image_resolution = "*=medium"
    hints = [{"kind": "damage"}, {"kind": "overview"}]
    parts = extraction._build_input("Rear hit", [str(A), str(B)], None, ["P03", "P07"], hints, examples)
    head = parts[:6]
    check(head[0] == text(read_examples.DEFAULT_INTRO), "first the intro")
    check(head[1] == text(f"Example E1 (not this case): {CAPTION_1}") and head[3] == text(f"Example E2 (not this case): {CAPTION_2}"),
          "then each example: 'Example E1 (not this case): <caption>'", json.dumps(texts(head)))
    check(head[2] == {"type": "image", "data": b64(E1), "mime_type": "image/jpeg"}
          and head[4] == {"type": "image", "data": b64(E2), "mime_type": "image/png"},
          "...each followed by its photo, with no resolution (Gemini's default) while the case's photos have one")
    check(head[5] == text(extraction.EXAMPLES_END) and "THIS case" in head[5]["text"], "then the line that ends them")
    check(parts[6:] == extraction._build_input("Rear hit", [str(A), str(B)], None, ["P03", "P07"], hints),
          "then the case, exactly as it is sent without examples")
    check(all(p.get("resolution") == "medium" for p in parts[6:] if p.get("type") == "image"),
          "...its photos still at their kind's resolution")

    labels = _labels(parts)
    check(labels == ["P03", "P07"] and not any(t.startswith("Photo") for t in texts(head)),
          "the only 'Photo Pxx:' labels are the case's; no example text starts with 'Photo'")
    check(pipeline._LABEL_RE.match("E1") is None and sl._LABEL_RE.match("E1") is None
          and extraction._case_label("E1") is None and extraction.example_label(1) == "E1",
          "an example's label E1 is no photo label to the pipeline or the second look")
    with_facts = extraction._build_input("Rear hit", [str(A)], {"location": "Carpark"}, examples=examples)
    t = texts(with_facts)
    check(t[0] == read_examples.DEFAULT_INTRO and t.index(extraction.EXAMPLES_END) < next(
        i for i, x in enumerate(t) if x.startswith("Already established")), "known facts too come after the examples")

    # draft_report: the system prompt's one line more, and the cost logged
    # once a reading although two models are tried.
    ANSWER["fail_model"] = "model-a"
    with logged("app.ai.extraction") as records:
        extraction.draft_report("Rear hit", [str(A)], photo_labels=["P03"], actor=AiActor.cli(), examples=examples)
    sent = drafts()
    check(len(sent) == 2 and all(s["system_instruction"] == extraction.SYSTEM_PROMPT + "\n" + extraction.EXAMPLES_PROMPT_LINE
                                 for s in sent), "with examples: the system prompt plus one line, on every model tried")
    check(extraction.EXAMPLES_PROMPT_LINE.count("\n") == 0 and "not part of this case" in extraction.EXAMPLES_PROMPT_LINE,
          "...a single line saying they are not part of the case")
    cost = [r.getMessage() for r in records if "reference example" in r.getMessage()]
    check(cost == ["Reading with 2 reference example(s) (e1, e2): about 2240 more input tokens"],
          "the cost logged once a reading: how many examples, about 1,120 tokens each", repr(cost))
    check(all(s["input"][:6] == head[:2] + [head[2]] + head[3:6] for s in sent), "...both tries carry the examples first")

    SENT.clear()
    with logged("app.ai.extraction") as records:
        extraction.draft_report("Rear hit", [str(A)], photo_labels=["P03"], actor=AiActor.cli())
    sent = drafts()
    check(sent and all(s["system_instruction"] == extraction.SYSTEM_PROMPT for s in sent)
          and not any("reference example" in r.getMessage() for r in records),
          "without examples: the system prompt unchanged and nothing logged")


def references() -> None:
    examples = read_examples.load(GOOD)
    box = [100, 100, 200, 200]
    ANSWER["draft"] = json.dumps({"description": "Rear hit.", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "P03", "bbox_2d": box},
        {"part": "Boot Lid", "photo_reference": "E1", "bbox_2d": box},
        {"part": "Bonnet", "photo_reference": "Example E2", "bbox_2d": box},
        {"part": "Front Bumper", "photo_reference": "p7", "bbox_2d": box},
        {"part": "Left Tail Lamp", "photo_reference": "P05", "bbox_2d": box},
        {"part": "Boot Lid", "photo_reference": "P07", "bbox_2d": box},
    ], "damaged_parts": ["Rear Bumper", "Boot Lid", "Bonnet", "Front Bumper", "Left Tail Lamp"]})
    with logged("app.ai.extraction") as records:
        draft = extraction.draft_report("Rear hit", [str(A), str(B)], photo_labels=["P03", "P07"], actor=AiActor.cli(),
                                        examples=examples)
    got = [(it.part, it.photo_reference, it.bbox_2d) for it in draft.damage_summary]
    check(got == [("Rear Bumper", "P03", box), ("Front Bumper", "p7", box), ("Left Tail Lamp", None, None),
                  ("Boot Lid", "P07", box)],
          "with examples: an item seen on an example ('E1', 'Example E2') is left out -- another case's damage; "
          "a reference to a photo not sent is dropped with its box, the item kept", repr(got))
    check(draft.damaged_parts == ["Rear Bumper", "Boot Lid", "Front Bumper", "Left Tail Lamp"],
          "...damaged_parts loses the part only seen on an example, keeps one a case photo shows too",
          repr(draft.damaged_parts))
    check(sum("seen on reference example" in r.getMessage() for r in records) == 2, "...each one left out is logged")
    plain = ANSWER["draft"]
    ANSWER["draft"] = json.dumps({"description": "Rear hit.", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "P02"}, {"part": "Boot Lid", "photo_reference": "e01"},
        {"part": "Bonnet", "photo_reference": "P03"}]})
    draft = extraction.draft_report("Rear hit", [str(A), str(B)], actor=AiActor.cli(), examples=examples)
    check([(it.part, it.photo_reference) for it in draft.damage_summary] == [("Rear Bumper", "P02"), ("Bonnet", None)],
          "...with the default numbering too (photos P01 and P02: 'e01' left out, P03 dropped)")
    ANSWER["draft"] = plain
    draft = extraction.draft_report("Rear hit", [str(A), str(B)], photo_labels=["P03", "P07"], actor=AiActor.cli())
    check([it.photo_reference for it in draft.damage_summary] == ["P03", "E1", "Example E2", "p7", "P05", "P07"],
          "without examples nothing changes (the bot keeps what it always kept)")

    # The intake reading: what it stores points at case photos only.
    settings.gemini_read_examples = str(GOOD)
    ANSWER["draft"] = json.dumps({"description": "Rear hit.", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "P02", "bbox_2d": box},
        {"part": "Boot Lid", "photo_reference": "E1", "bbox_2d": box},
    ]})
    rid, ids = new_case(("red", "green"))
    SENT.clear()
    pipeline._read(rid, AiActor.cli())
    data, read_ids, runs_sent = stored(rid)
    items = {it["part"]: it for it in data.get("damage_summary") or []}
    check(items.get("Rear Bumper", {}).get("photo_reference") == f"@{ids[1]}" and items["Rear Bumper"].get("bbox_2d") == box,
          "intake: a reference to a case photo is kept, as its photo id")
    check("Boot Lid" not in items and "Boot Lid" not in (data.get("damaged_parts") or []),
          "intake: an item seen on an example never reaches the draft", repr(sorted(items)))
    check(read_ids == ids and runs_sent == [["P01", "P02"]],
          "the photos read and the run log's photos sent are the case's only", repr((read_ids, runs_sent)))

    # The second look reads labels back the same way: an example's is no photo.
    found = sl.parse_answer(json.dumps({"items": [{"part": "Boot Lid", "photo": "E1", "condition_tokens": ["Dented"]},
                                                  {"part": "Boot Lid", "photo": "P01", "condition_tokens": ["Dented"]}]}),
                            {"Boot Lid"}, {"P01": "photo-1"})
    check([it["photo_reference"] for it in found] == ["@photo-1"], "the second look drops a suggestion on 'E1'")


def intake_only() -> None:
    """The intake reading sends the examples; the sorter, the second look and
    the bots' drafts never do."""
    settings.gemini_read_examples = str(GOOD)
    settings.second_look_enabled = True
    ANSWER["draft"] = json.dumps({"description": "Rear hit.", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "P01"}]})
    rid, _ = new_case(("red", "green", "blue"), sorted_=False)
    pipeline._sort(rid, AiActor.cli())
    pipeline._read(rid, AiActor.cli())
    by_kind: dict[str, list[dict]] = {}
    for s in SENT:
        props = s["response_format"]["schema"].get("properties", {})
        kind = "draft" if "damage_summary" in props else "look" if "items" in props else "sort"
        by_kind.setdefault(kind, []).append(s)
    draft = (by_kind.get("draft") or [{}])[0]
    check(draft and example_images(draft["input"]) == 2 and texts(draft["input"])[0] == read_examples.DEFAULT_INTRO,
          "the intake reading: both examples, first")
    check(by_kind.get("sort") and all(not example_images(s["input"]) and "Example" not in " ".join(texts(s["input"]))
                                      and s["system_instruction"] != extraction.SYSTEM_PROMPT for s in by_kind["sort"]),
          "the sorter: none")
    check(by_kind.get("look") and all(not example_images(s["input"]) and "Example E" not in " ".join(texts(s["input"]))
                                      for s in by_kind["look"]),
          "the second look: none (it was asked)", repr(sorted(by_kind)))

    # The Telegram/WhatsApp drafts and analyze-photos call draft_report as
    # before, without examples, whatever the setting.
    SENT.clear()
    extraction.draft_report("Rear hit", [str(A)], {"location": "Carpark"}, actor=AiActor.cli())
    sent = drafts()
    check(sent and sent[0]["system_instruction"] == extraction.SYSTEM_PROMPT and not example_images(sent[0]["input"])
          and sent[0]["input"] == extraction._build_input("Rear hit", [str(A)], {"location": "Carpark"}),
          "the bots' drafting call: no examples with the setting on")


def benchmark() -> None:
    """--read-examples: leave-one-out, and which examples went with each case
    in the result file."""
    import benchmark_intake as bi

    A_ = bi._app()
    base = _tmp / "bench"
    cases_dir, key_path, out = base / "cases", base / "key.json", base / "out" / "run.json"
    for folder, colour in (("case_one", "red"), ("case_two", "blue")):
        square(cases_dir / folder / "p1.jpg", colour)
    vehicle = {"plate_number": "TEST-1", "make": "Testmake", "model": "Testmodel", "color": "White", "vin": "TESTVIN1",
               "engine_number": "TESTENG1", "date_of_registration": "2017-01-12"}
    key = {"cases": [
        {"name": "Test Case One", "folder": "case_one", "vehicle": vehicle,
         "parts": [{"part": "Rear Bumper", "surveyor": ["Rear bumper"]}]},
        {"name": "Test Case Two", "folder": "case_two", "vehicle": vehicle,
         "parts": [{"part": "Boot Lid", "surveyor": ["Tail gate"]}]},
    ]}
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(key), encoding="utf-8")
    ex_dir = base / "examples"
    for name, colour in (("one", "orange"), ("two", "purple"), ("any", "yellow")):
        square(ex_dir / f"{name}.jpg", colour)
    manifest_path = write_manifest(ex_dir, {"examples": [
        # Tagged by the case's name written another way, by its folder, and not at all.
        {"id": "one", "image": "one.jpg", "case": "test-case-ONE", "caption": "Lesson from case one."},
        {"id": "two", "image": "two.jpg", "case": "CASE_TWO", "caption": "Lesson from case two."},
        {"id": "any", "image": "any.jpg", "caption": "Lesson from no case."},
    ]})
    bad = write_manifest(base / "bad", {"examples": [{"id": "z", "image": "missing.jpg", "caption": "x"}]})

    seen: list[tuple] = []
    real_read = A_.pipeline._read

    def reading(rid, actor):
        seen.append((read_examples.HELD_OUT, (read_examples.for_reading() or Examples("", ())).ids))
        real_read(rid, actor)

    saved = (A_.pipeline._sort, A_.pipeline._read)
    A_.pipeline._sort = lambda rid, actor: None  # the photos stay unsorted: read as such
    A_.pipeline._read = reading
    os.environ["BENCHMARK_SCRATCH"] = "1"
    args = ["--cases", str(cases_dir), "--key", str(key_path), "--out", str(out), "--no-second-look", "--models", CHAIN]
    buf = io.StringIO()
    try:
        try:
            bi.main(args + ["--read-examples", str(bad)])
            check(False, "a bad manifest stops the run")
        except SystemExit as exc:
            check("--read-examples" in str(exc.code) and "no image at" in str(exc.code) and not out.exists()
                  and settings.gemini_read_examples is None,
                  "a bad manifest stops the run before anything, the settings untouched", repr(exc.code))
        SENT.clear()
        with contextlib.redirect_stdout(buf):
            code = bi.main(args + ["--read-examples", str(manifest_path)])
    finally:
        A_.pipeline._sort, A_.pipeline._read = saved
        os.environ.pop("BENCHMARK_SCRATCH", None)
    console = buf.getvalue()
    check(code == 0, "the run completes", console[-500:])
    doc = json.loads(out.read_text(encoding="utf-8"))
    check(doc["ai_settings"]["read_examples"] == str(manifest_path.resolve()), "ai_settings names the manifest, absolute")
    check([e["id"] for e in doc["read_examples"]["examples"]] == ["one", "two", "any"]
          and doc["read_examples"]["examples"][0]["caption"] == "Lesson from case one."
          and "image" not in doc["read_examples"]["examples"][0],
          "the result file lists the manifest's examples: id, case, caption")
    got = [(r["case"], r.get("read_examples")) for r in doc["runs"]]
    check(got == [("Test Case One", {"sent": ["two", "any"], "held_out": ["one"]}),
                  ("Test Case Two", {"sent": ["one", "any"], "held_out": ["two"]})],
          "leave-one-out: each case is read without its own example (by name or folder), and the run records "
          "which were sent", repr(got))
    check([ids for _, ids in seen] == [["two", "any"], ["one", "any"]] and read_examples.HELD_OUT == (),
          "...what the pipeline itself got while reading; nothing held out after the run", repr(seen))
    reads = drafts()
    captions = [[t for t in texts(r["input"]) if t.startswith("Example E")] for r in reads]
    check(captions == [["Example E1 (not this case): Lesson from case two.", "Example E2 (not this case): Lesson from no case."],
                       ["Example E1 (not this case): Lesson from case one.", "Example E2 (not this case): Lesson from no case."]],
          "...and in the requests themselves", repr(captions))
    check("read examples: 3 from" in console and "read examples: sent two, any; held out (this case's own) one" in console,
          "the console says so", console[:600])


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("unset: the request as before", unset),
        ("the manifest", manifest),
        ("the request's parts, in order", request_parts),
        ("photo_reference never points at an example", references),
        ("only the intake reading sends them", intake_only),
        ("the benchmark: --read-examples, leave-one-out", benchmark),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
