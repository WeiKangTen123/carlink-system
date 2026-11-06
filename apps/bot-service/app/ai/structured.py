"""One structured-output Gemini call, walked through every key and model.

Originally the body of extraction.draft_report. Pulled out once dashboard
intake added a second kind of call (photo sorting, see ai/triage.py) that
needs exactly the same fallback, rate-limit and retry behaviour -- two
copies of this loop would drift the first time either was tuned.

Every call is for an AiActor (ai/keys.py), a required keyword: forgetting it
is a crash in the tests, never a call quietly made on the company's key.
"""
import base64
import logging
import mimetypes
import re
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from app.ai import runs
from app.ai.client import get_client
from app.ai.keys import (
    AI_KEY_INVALID,
    AI_QUOTA_USED,
    AiActor,
    AiUnavailable,
    last4,
    record_result,
    refusal,
    resolve_keys,
    scrub,
)
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
    actor: AiActor,
    timeout: float = 45.0,
) -> T:
    """Returns parse(raw JSON text) from the first key/model that produces
    output parse() accepts.

    parse runs inside the fallback: a response that fails validation moves
    on to the next model exactly like a transport error does, since a
    malformed answer is no more usable than none.

    actor: who the call is for. Their keys are looked up now, for this
    call (keys.resolve_keys). Google refusing their own key, or its
    allowance running out on every model (or never there: no billing), is
    kept on its row (keys.record_result) and the next key is tried: the
    company's, if they may use it. Raises AiUnavailable when there is no key
    to try, or when their own key was the only one and failed that way.
    Errors are told apart by keys.refusal, which knows both of the SDK's
    error families.
    """
    # Two nested fallbacks. Inner: each model in the chain, since they have
    # independent per-model quotas. Outer: each key, since every model
    # shares one key's allowance -- once a key is exhausted across the
    # whole chain, only a different key helps. Ordered so a single key's
    # full chain is tried before moving on, rather than burning every key
    # on the first model.
    keys = resolve_keys(actor)
    last_error: Exception | None = None
    # The AiUnavailable code of each own key that failed for its own reasons.
    refused: list[str] = []
    # The run log (ai/runs.py) gets the request's size once, and each try
    # below as it ends, through the calling thread's collector.
    runs.note_input(_input_size(system_instruction, input_parts))

    for key in keys:
        client = get_client(key)
        own = key.source == "own"
        # refusal() of each model's last error, for an own key.
        verdicts: list[str | None] = []
        for model_id in models:
            # Every attempt takes a slot, not just the first. Previously the
            # gate ran once before these loops, so a draft that fell through
            # the chain fired up to five requests back-to-back -- which is
            # what tripped the quota during testing.
            waited = acquire(key.rate_limit_id)
            verdict = None
            # One retry per model on a 429, using the delay the server itself
            # supplies. Falling straight through to the next model is
            # pointless: it draws on the same per-key quota and 429s too.
            for attempt in (1, 2):
                started = time.monotonic()
                interaction = None
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
                    logger.info("Gemini %s produced by %r on %s", purpose, model_id, key.label)
                    record_result(actor, key, "ok")
                    _note_attempt(key, model_id, "ok", waited, started, interaction)
                    return result
                except Exception as exc:
                    last_error = exc
                    _note_attempt(key, model_id, _outcome(exc, interaction), waited, started, interaction)
                    # A company key's failures go on exactly as before P4.
                    verdict = refusal(exc) if own else None
                    # A refused key or a spent day's allowance won't come
                    # back in 30 seconds: no wait, straight to the next.
                    delay = retry_after_seconds(exc) if verdict is None and is_rate_limit_error(exc) else None
                    if delay is not None and attempt == 1:
                        logger.warning(
                            "Gemini model %r rate-limited on %s; waiting %.1fs as instructed",
                            model_id, key.label, delay,
                        )
                        time.sleep(delay)
                        # The retry's wait is the one the server asked for.
                        waited = delay
                        continue
                    # runs.scrub_error, not only the key scrub: a validation
                    # error quotes the model's answer, which on a public
                    # report can repeat the sender's own words.
                    logger.warning(
                        "Gemini model %r failed on %s, trying next: %s",
                        model_id, key.label, runs.scrub_error(str(exc), (key.value,)),
                    )
                    break
            verdicts.append(verdict)
            if verdict == "invalid":
                break  # Google refused the key itself: no other model will take it
        outcome = _key_outcome(verdicts) if own else None
        if outcome:
            record_result(actor, key, outcome, last_error)
            refused.append(AI_QUOTA_USED if outcome == "quota" else AI_KEY_INVALID)

    if refused and len(refused) == len(keys):
        # Their own key was all they had: say what's wrong with it rather
        # than "the AI failed". (Not chained: the SDK's error stays in the
        # log lines above, scrubbed.)
        raise AiUnavailable(refused[-1]) from None
    raise RuntimeError(
        f"All Gemini fallback models ({', '.join(models)}) failed "
        f"across {len(keys)} configured API key(s)."
    ) from _scrubbed(last_error, keys)


def _input_size(system_instruction: str, input_parts: list[dict]) -> int:
    """What a call sends, in bytes: the system prompt, the texts and the
    images as sent (base64)."""
    size = len((system_instruction or "").encode("utf-8"))
    for part in input_parts:
        if part.get("type") == "image":
            size += len(part.get("data") or "")
        elif part.get("type") == "text":
            size += len((part.get("text") or "").encode("utf-8"))
    return size


def _outcome(exc: Exception, interaction) -> str:
    """One failed try, for the run log: schema (the model answered, parse()
    refused it), 429, timeout (ours or the SDK's), or error."""
    if interaction is not None:
        return "schema"
    if is_rate_limit_error(exc):
        return "429"
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "timeout"
    return "error"


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _tokens(interaction) -> tuple[int | None, int | None]:
    """(prompt, output) tokens when the response says: the interactions
    API's usage, or generate_content's usage_metadata. (None, None) when it
    doesn't -- never a made-up 0."""
    usage = getattr(interaction, "usage", None)
    if usage is not None:
        return _count(getattr(usage, "total_input_tokens", None)), _count(getattr(usage, "total_output_tokens", None))
    meta = getattr(interaction, "usage_metadata", None)
    if meta is not None:
        return _count(getattr(meta, "prompt_token_count", None)), _count(getattr(meta, "candidates_token_count", None))
    return None, None


def _note_attempt(key, model_id: str, outcome: str, waited, started: float, interaction) -> None:
    """One try for the run log (ai/runs.py). Never raises: a log that can't
    be kept must not turn an answer into a failure. waited: seconds spent
    before the try was sent (a rate-limit slot, or the delay a 429 asked
    for)."""
    try:
        attempt = {
            "model": model_id,
            "key_source": key.source,
            "key_last4": last4(key.value) if key.value else None,
            "outcome": outcome,
            "waited_s": round(float(waited), 1) if isinstance(waited, (int, float)) and not isinstance(waited, bool) else 0.0,
            "ms": int((time.monotonic() - started) * 1000),
        }
        prompt_tokens, output_tokens = _tokens(interaction) if interaction is not None else (None, None)
        if prompt_tokens is not None:
            attempt["prompt_tokens"] = prompt_tokens
        if output_tokens is not None:
            attempt["output_tokens"] = output_tokens
        runs.note_attempt(attempt)
    except Exception:
        logger.warning("AI run log: couldn't note a Gemini try", exc_info=True)


def _key_outcome(verdicts: list[str | None]) -> str | None:
    """What an own key's models, all failed, say about the key itself
    (keys.record_result's outcomes), or None when it's no fault of the key:
    refused ("invalid", which stops the chain); out of allowance on every
    model -- "no_allowance" when none ever had any (billing off), else
    "quota"."""
    if not verdicts:
        return None
    if verdicts[-1] == "invalid":
        return "invalid"
    if all(v == "no_allowance" for v in verdicts):
        return "no_allowance"
    if all(v in ("quota", "no_allowance") for v in verdicts):
        return "quota"
    return None


def _scrubbed(error: Exception | None, keys) -> Exception | None:
    """A copy of the last error, holding only its scrubbed text, to chain
    the final RuntimeError from: that cause is shown (intake's message) and
    logged (its traceback), and the SDK's own error can quote the key it was
    sent with -- a company key's too. Built-in types (TimeoutError from
    _create_within, ValueError from parse) keep their type."""
    if error is None:
        return None
    text = str(error)
    for key in keys:
        text = scrub(text, key.value)
    text = scrub(text)
    if type(error).__module__ == "builtins":
        try:
            return type(error)(text)
        except Exception:
            pass
    return RuntimeError(f"{type(error).__name__}: {text}")
