"""One structured-output Gemini call, walked through every key and model.

Originally the body of extraction.draft_report. Pulled out once dashboard
intake added a second kind of call (photo sorting, see ai/triage.py) that
needs exactly the same fallback, rate-limit and retry behaviour -- two
copies of this loop would drift the first time either was tuned.
"""
import base64
import logging
import mimetypes
import re
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from app.ai.client import available_api_keys, get_client
from app.ai.rate_limit import acquire, is_rate_limit_error, retry_after_seconds

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Extra time allowed on top of a call's own timeout before giving up on it.
# The SDK's per-request timeout doesn't always bound a call -- a test call
# to a preview model ran for minutes past timeout=60 -- so the limit is
# enforced here, on the wall clock.
DEADLINE_MARGIN = 15.0


def _create_within(deadline: float, create: Callable[..., T], **kwargs) -> T:
    """Runs one SDK call, giving up after `deadline` seconds.

    A thread can't be killed, so a call that overruns is left to finish on
    its own (daemon thread, result discarded) while the chain moves on to
    the next model. A fresh thread per call rather than a pool, so a few
    stuck calls can never queue up the ones behind them.
    """
    box: dict = {}
    done = threading.Event()

    def run() -> None:
        try:
            box["result"] = create(**kwargs)
        except BaseException as exc:  # handed to the caller below
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, name="gemini-call", daemon=True).start()
    if not done.wait(deadline):
        raise TimeoutError(f"no answer within {deadline:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["result"]

# Dashboard intake sends the 1600px WebP previews (storage/files.py) rather
# than full camera originals, and HEIC straight from iPhones. Neither is in
# every platform's mimetypes table, and the image/jpeg fallback below would
# mislabel them.
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/heic", ".heic")


def image_part(path: str) -> dict:
    """An inline image input for client.interactions.create."""
    media_type, _ = mimetypes.guess_type(path)
    return {
        "type": "image",
        "data": base64.b64encode(Path(path).read_bytes()).decode("utf-8"),
        "mime_type": media_type or "image/jpeg",
    }


def call_structured(
    *,
    purpose: str,
    system_instruction: str,
    input_parts: list[dict],
    schema: dict,
    parse: Callable[[str], T],
    models: list[str],
    timeout: float = 45.0,
) -> T:
    """Returns parse(raw JSON text) from the first key/model that produces
    output parse() accepts.

    parse runs inside the fallback: a response that fails validation moves
    on to the next model exactly like a transport error does, since a
    malformed answer is no more usable than none.
    """
    # Two nested fallbacks. Inner: each model in the chain, since they have
    # independent per-model quotas. Outer: each configured API key, since
    # every model shares one key's allowance -- once a key is exhausted
    # across the whole chain, only a different key helps. Ordered so a
    # single key's full chain is tried before moving on, rather than
    # burning every key on the first model.
    api_keys = available_api_keys() or [None]
    last_error: Exception | None = None

    for key_index, api_key in enumerate(api_keys):
        client = get_client(api_key)
        for model_id in models:
            # Every attempt takes a slot, not just the first. Previously the
            # gate ran once before these loops, so a draft that fell through
            # the chain fired up to five requests back-to-back -- which is
            # what tripped the quota during testing.
            acquire(api_key)
            # One retry per model on a 429, using the delay the server itself
            # supplies. Falling straight through to the next model is
            # pointless: it draws on the same per-key quota and 429s too.
            for attempt in (1, 2):
                try:
                    interaction = _create_within(
                        timeout + DEADLINE_MARGIN,
                        client.interactions.create,
                        model=model_id,
                        system_instruction=system_instruction,
                        input=input_parts,
                        response_format={
                            "type": "text",
                            "mime_type": "application/json",
                            "schema": schema,
                        },
                        # Without this, a stalled call hangs indefinitely
                        # (hit directly while testing model candidates --
                        # one call sat for 100+s with no response and no
                        # error), tying up a worker for no benefit since the
                        # point of the chain is to keep trying other models.
                        timeout=timeout,
                    )
                    raw_output = (interaction.output_text or "").strip()
                    if raw_output.startswith("```"):
                        raw_output = re.sub(r"^```(?:json)?\s*", "", raw_output)
                        raw_output = re.sub(r"\s*```$", "", raw_output)
                    result = parse(raw_output)
                    # Which model actually answered. The fallbacks return
                    # visibly thinner analyses (no photo reference, bounding
                    # box or confidence), and without this line a thin draft
                    # in production is indistinguishable from a bug.
                    logger.info("Gemini %s produced by %r on key #%d", purpose, model_id, key_index + 1)
                    return result
                except Exception as exc:
                    last_error = exc
                    delay = retry_after_seconds(exc) if is_rate_limit_error(exc) else None
                    if delay is not None and attempt == 1:
                        logger.warning(
                            "Gemini model %r rate-limited on key #%d; waiting %.1fs as instructed",
                            model_id, key_index + 1, delay,
                        )
                        time.sleep(delay)
                        continue
                    logger.warning(
                        "Gemini model %r failed on key #%d, trying next: %s",
                        model_id, key_index + 1, exc,
                    )
                    break

    raise RuntimeError(
        f"All Gemini fallback models ({', '.join(models)}) failed "
        f"across {len(api_keys)} configured API key(s)."
    ) from last_error
