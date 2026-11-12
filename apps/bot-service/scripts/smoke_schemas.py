"""LIVE check that Gemini accepts the response schemas, on every model that
may be asked to answer them. Run by the integrator before a schema change
is merged or deployed -- never by the tests, never on a laptop.

Why: the drafting schema is shared by the dashboard wizard and the Telegram
bot, and Gemini refuses a schema it finds too complex with 400
INVALID_ARGUMENT on EVERY call -- the repo lost every draft that way once
(2ae2183; see extraction._NOT_MODEL_OUTPUT). Whether a schema is "too
complex" is decided per model on Google's side, so nothing offline can
answer it. One call per schema per model, with a tiny synthetic image (a
grey square: no vehicle, no person, nothing real), says pass or 400 for
each.

    docker exec carlink_api python scripts/smoke_schemas.py --live
        [--models a,b] [--triage-models c,d] [--second-look-models e,f]
        [--thinking-level-read L] [--thinking-level-sort L] [--thinking-level-second-look L]
        [--read-tools code_execution] [--resolution R]
        [--read-tools-timeout S] [--zoom-hint] [--sort-damage-box]

The model settings (app/config.py) are sent as the app would send them
(structured.request_settings): the configured ones, or these flags in their
place. A schema can pass on its own and be refused with them -- a JSON
schema together with code execution is a preview feature on Gemini 3, and
gemini-3.8-flash has no "minimal" thinking -- so smoke a setting before it
goes live. --resolution gives every image of every schema's request that
"resolution" (low, medium, high, ultra_high): whether each model takes it.

Contract 8 D1: --read-tools takes the per-model form too
("gemini-3.1-flash-lite=code_execution"); --read-tools-timeout and
--zoom-hint send a try with tools as the reading would (its timeout, and
extraction.ZOOM_NOTE after the prompt); --sort-damage-box smokes the sort
schema and prompt with the damage box (triage.response_schema). A pass
says how many code steps the model ran ("code runs"), and its answer is
read as call_structured reads it (structured._parse_answer).

Schemas: "draft" (ai/extraction._response_schema, the wizard's reading and
the bot's drafting), "sort" (ai/triage._RESPONSE_SCHEMA) and "second_look"
(ai/second_look.response_schema at its largest: MAX_CANDIDATES parts in the
enum). Each is tried on every distinct model of the three chains
(settings.gemini_model_chain, gemini_triage_model_chain and
gemini_second_look_chain, or the overrides), since a fallback can put a
schema on any of them. Uses the first company key (the env key inside
carlink_api), and takes a slot from the shared rate-limit window per call
(ai/rate_limit), so it queues behind live traffic rather than tripping the
12-per-minute quota. ~3 x (models) calls. The key is never printed: errors
are scrubbed (ai/keys.scrub).

Exit status: 0 when no model answered 400; 1 when any did; 2 when it could
not run (no --live, no key).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import extraction, second_look, triage  # noqa: E402
from app.ai.client import get_client  # noqa: E402
from app.ai.keys import company_keys, http_status, scrub  # noqa: E402
from app.ai.rate_limit import acquire  # noqa: E402
from app.ai.structured import (  # noqa: E402
    DEADLINE_MARGIN,
    _create_within,
    _parse_answer,
    _tool_calls,
    image_part,
    request_settings,
    tools_try,
)
from app.config import IMAGE_RESOLUTIONS, normal_read_tools, normal_thinking_levels, settings  # noqa: E402
from app.reports.taxonomy import CANONICAL_PARTS  # noqa: E402

# Each schema's call_structured purpose: the one its model settings follow.
PURPOSES = {"draft": "draft", "sort": "photo sort", "second_look": "second look"}

TIMEOUT = 60.0


def _chain(text: str) -> list[str]:
    return [m.strip() for m in (text or "").split(",") if m.strip()]


def _tiny_image(folder: Path) -> str:
    """A 96x64 grey JPEG with a darker block: enough for the model to have
    an image to read, nothing it could mistake for a case."""
    from PIL import Image, ImageDraw

    path = folder / "smoke.jpg"
    image = Image.new("RGB", (96, 64), (128, 128, 128))
    ImageDraw.Draw(image).rectangle((20, 16, 60, 44), fill=(70, 70, 70))
    image.save(path, "JPEG", quality=80)
    return str(path)


def _schemas(image: str) -> dict[str, dict]:
    """name -> what call_structured would send for it, and the parse that
    would read the answer."""
    def parse_sort(raw: str):
        data = json.loads(raw)
        if not isinstance(data.get("photos"), list):
            raise ValueError("no photos list")
        return data

    # The second look's enum is built per case from its candidates; the
    # longest it can be is MAX_CANDIDATES names.
    parts = [p for p in CANONICAL_PARTS if p != "Other / Not Listed"][: second_look.MAX_CANDIDATES]
    cands = [second_look.Candidate(p, "nearby", "schema smoke test") for p in parts]
    sent = [second_look.SentPhoto("P01", "smoke", image)]

    return {
        "draft": {
            "system": extraction.SYSTEM_PROMPT,
            "input": extraction._build_input("Schema smoke test: one grey square, no vehicle and no incident.", [image]),
            "schema": extraction._response_schema(),
            "parse": extraction._parse_draft,
        },
        "sort": {
            # As sent now: with the damage box under --sort-damage-box.
            "system": triage.system_prompt(),
            "input": [{"type": "text", "text": "1 photos follow."}, {"type": "text", "text": "Photo P01:"}, image_part(image)],
            "schema": triage.response_schema(),
            "parse": parse_sort,
        },
        "second_look": {
            "system": second_look.SYSTEM_PROMPT,
            "input": second_look.build_input({}, {"Roof"}, cands, sent),
            "schema": second_look.response_schema(parts),
            "parse": lambda raw: second_look.parse_answer(raw, set(parts), {"P01": "smoke"}),
        },
    }


def request(name: str, spec: dict, model: str, resolution: str | None = None) -> dict:
    """What call_structured would send for one schema on one model: its
    model settings (request_settings) included, a try with tools as
    tools_try makes it, and --resolution on every image."""
    inputs = spec["input"]
    if resolution:
        inputs = [dict(part, resolution=resolution) if part.get("type") == "image" else part for part in inputs]
    extra = request_settings(PURPOSES[name], model)
    note = extraction.ZOOM_NOTE if name == "draft" and settings.gemini_read_zoom_hint else None
    timeout, system = tools_try(extra, TIMEOUT, spec["system"], note)
    return dict(
        model=model,
        system_instruction=system,
        input=inputs,
        response_format={"type": "text", "mime_type": "application/json", "schema": spec["schema"]},
        timeout=timeout,
        **extra,
    )


def _verdict(exc: Exception, key_value: str) -> tuple[str, str]:
    status = http_status(exc)
    detail = scrub(str(exc), key_value)
    if status == 400:
        return "400", detail
    if status == 429:
        return "429 (quota, no verdict)", detail
    if status == 404:
        return "404 (model not found)", detail
    if isinstance(exc, TimeoutError):
        return "timeout (no verdict)", detail
    return f"error{f' {status}' if status else ''}", detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="required: this makes real Gemini calls on the company key")
    parser.add_argument("--models", help="drafting chain to try instead of settings.gemini_model_chain")
    parser.add_argument("--triage-models", help="sorting chain to try instead of settings.gemini_triage_model_chain")
    parser.add_argument("--second-look-models", help="second-look chain to try instead of settings.gemini_second_look_chain")
    parser.add_argument("--thinking-level-read", help="instead of settings.gemini_thinking_level_read (e.g. low, or "
                                                      "gemini-3.5-flash-lite=medium)")
    parser.add_argument("--thinking-level-sort", help="instead of settings.gemini_thinking_level_sort")
    parser.add_argument("--thinking-level-second-look", help="instead of settings.gemini_thinking_level_second_look")
    parser.add_argument("--read-tools", help="instead of settings.gemini_read_tools (code_execution)")
    parser.add_argument("--resolution", choices=IMAGE_RESOLUTIONS, help="every image sent with this resolution")
    parser.add_argument("--read-tools-timeout", type=float, metavar="S",
                        help="instead of settings.gemini_read_tools_timeout: seconds of a try with tools")
    parser.add_argument("--zoom-hint", action="store_true",
                        help="settings.gemini_read_zoom_hint on: ZOOM_NOTE after the prompt on a try with tools")
    parser.add_argument("--sort-damage-box", action="store_true",
                        help="settings.intake_sort_damage_box on: the sort schema and prompt with the damage box")
    args = parser.parse_args()
    if not args.live:
        print("Refusing to run without --live: every line is a real Gemini call on the company key.")
        return 2
    try:
        for flag, field, normal in (("thinking_level_read", "gemini_thinking_level_read", normal_thinking_levels),
                                    ("thinking_level_sort", "gemini_thinking_level_sort", normal_thinking_levels),
                                    ("thinking_level_second_look", "gemini_thinking_level_second_look", normal_thinking_levels),
                                    ("read_tools", "gemini_read_tools", normal_read_tools)):
            if getattr(args, flag) is not None:
                setattr(settings, field, normal(getattr(args, flag)))
    except ValueError as exc:
        print(f"Bad setting: {exc}")
        return 2
    if args.read_tools_timeout is not None:
        if args.read_tools_timeout <= 0:
            print("Bad setting: --read-tools-timeout must be more than 0")
            return 2
        settings.gemini_read_tools_timeout = args.read_tools_timeout
    if args.zoom_hint:
        settings.gemini_read_zoom_hint = True
    if args.sort_damage_box:
        settings.intake_sort_damage_box = True

    models = list(dict.fromkeys(
        (_chain(args.models) if args.models else extraction._model_chain())
        + (_chain(args.triage_models) if args.triage_models else triage._triage_chain())
        + (_chain(args.second_look_models) if args.second_look_models else second_look._chain())
    ))
    keys = company_keys()
    if not keys:
        print("No company key configured (GEMINI_API_KEY or Settings > Company): nothing to call with.")
        return 2
    key = keys[0]
    client = get_client(key)
    print(f"Key: {key.label}. Models: {', '.join(models)}.")
    tried = {f: getattr(settings, f) for f in ("gemini_thinking_level_read", "gemini_thinking_level_sort",
                                                 "gemini_thinking_level_second_look", "gemini_read_tools",
                                                 "gemini_read_tools_timeout", "gemini_read_zoom_hint",
                                                 "intake_sort_damage_box")
             if getattr(settings, f)}
    if tried or args.resolution:
        print(f"Model settings: {tried}" + (f"; every image at {args.resolution}" if args.resolution else ""))

    results: list[tuple[str, str, str, str]] = []
    with tempfile.TemporaryDirectory(prefix="smoke-schemas-") as folder:
        schemas = _schemas(_tiny_image(Path(folder)))
        for name, spec in schemas.items():
            print(f"\n{name} schema: {len(json.dumps(spec['schema']))} characters")
            for model in models:
                acquire(key.rate_limit_id)
                sent = request(name, spec, model, args.resolution)
                try:
                    interaction = _create_within(
                        sent["timeout"] + DEADLINE_MARGIN,
                        client.interactions.create,
                        **sent,
                    )
                    runs = _tool_calls(interaction)
                    try:
                        _parse_answer(spec["parse"], interaction)
                        verdict, detail = "pass" + (f" (code runs {runs})" if runs else ""), ""
                    except Exception as exc:
                        # Accepted the schema; the answer itself didn't parse.
                        # Not a 400, but worth seeing.
                        verdict, detail = "pass (answer didn't parse)", scrub(str(exc), key.value)
                except Exception as exc:
                    verdict, detail = _verdict(exc, key.value)
                results.append((name, model, verdict, detail))
                print(f"  {model:<28} {verdict}{f'  -- {detail}' if detail else ''}")

    rejected = [(n, m) for n, m, v, _ in results if v == "400"]
    no_verdict = [(n, m) for n, m, v, _ in results if not v.startswith("pass") and v != "400"]
    print()
    if rejected:
        print("SCHEMA REJECTED (400) by: " + ", ".join(f"{n} on {m}" for n, m in rejected))
    else:
        print("No model rejected a schema with 400.")
    if no_verdict:
        print("No verdict (quota, timeout, unknown model -- run again later): " + ", ".join(f"{n} on {m}" for n, m in no_verdict))
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
