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
        [--models a,b] [--triage-models c,d]

Schemas: "draft" (ai/extraction._response_schema, the wizard's reading and
the bot's drafting) and "sort" (ai/triage._RESPONSE_SCHEMA). Each is tried on
every distinct model of both chains (settings.gemini_model_chain and
settings.gemini_triage_model_chain, or the overrides), since a fallback can
put either schema on any of them. Uses the first company key (the env key
inside carlink_api), and takes a slot from the shared rate-limit window per
call (ai/rate_limit), so it queues behind live traffic rather than tripping
the 12-per-minute quota. ~2 x (models) calls. The key is never printed:
errors are scrubbed (ai/keys.scrub).

Exit status: 0 when no model answered 400; 1 when any did; 2 when it could
not run (no --live, no key).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import extraction, triage  # noqa: E402
from app.ai.client import get_client  # noqa: E402
from app.ai.keys import company_keys, http_status, scrub  # noqa: E402
from app.ai.rate_limit import acquire  # noqa: E402
from app.ai.structured import DEADLINE_MARGIN, _create_within, image_part  # noqa: E402

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

    return {
        "draft": {
            "system": extraction.SYSTEM_PROMPT,
            "input": extraction._build_input("Schema smoke test: one grey square, no vehicle and no incident.", [image]),
            "schema": extraction._response_schema(),
            "parse": extraction._parse_draft,
        },
        "sort": {
            "system": triage.SYSTEM_PROMPT,
            "input": [{"type": "text", "text": "1 photos follow."}, {"type": "text", "text": "Photo P01:"}, image_part(image)],
            "schema": triage._RESPONSE_SCHEMA,
            "parse": parse_sort,
        },
    }


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
    args = parser.parse_args()
    if not args.live:
        print("Refusing to run without --live: every line is a real Gemini call on the company key.")
        return 2

    models = list(dict.fromkeys(
        (_chain(args.models) if args.models else extraction._model_chain())
        + (_chain(args.triage_models) if args.triage_models else triage._triage_chain())
    ))
    keys = company_keys()
    if not keys:
        print("No company key configured (GEMINI_API_KEY or Settings > Company): nothing to call with.")
        return 2
    key = keys[0]
    client = get_client(key)
    print(f"Key: {key.label}. Models: {', '.join(models)}.")

    results: list[tuple[str, str, str, str]] = []
    with tempfile.TemporaryDirectory(prefix="smoke-schemas-") as folder:
        schemas = _schemas(_tiny_image(Path(folder)))
        for name, spec in schemas.items():
            print(f"\n{name} schema: {len(json.dumps(spec['schema']))} characters")
            for model in models:
                acquire(key.rate_limit_id)
                try:
                    interaction = _create_within(
                        TIMEOUT + DEADLINE_MARGIN,
                        client.interactions.create,
                        model=model,
                        system_instruction=spec["system"],
                        input=spec["input"],
                        response_format={"type": "text", "mime_type": "application/json", "schema": spec["schema"]},
                        timeout=TIMEOUT,
                    )
                    raw = (interaction.output_text or "").strip()
                    try:
                        spec["parse"](raw)
                        verdict, detail = "pass", ""
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
