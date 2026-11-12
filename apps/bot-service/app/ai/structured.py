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

from app.ai import allowance, runs
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
from app.config import ANY, image_resolutions, read_tools, settings, thinking_levels

logger = logging.getLogger(__name__)

T = TypeVar("T")

# refusal()'s verdicts that mean a model's allowance is gone for the day on
# a key: "quota" (used up) and "no_allowance" ("limit: 0", none at all).
DAY_VERDICTS = ("quota", "no_allowance")

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


def image_part(path: str, kind: str | None = None) -> dict:
    """An inline image input for client.interactions.create.

    kind: the photo's kind as the sorter filed it (ai/triage.PHOTO_KINDS),
    "unsorted" for a photo not sorted, on the reading call. The image then
    carries the resolution settings.gemini_image_resolution names for that
    kind (image_resolution). None -- sorting, the second look, the bot,
    which know no kinds -- sends the image as before, at Gemini's default.
    """
    media_type, _ = mimetypes.guess_type(path)
    part = {
        "type": "image",
        "data": base64.b64encode(Path(path).read_bytes()).decode("utf-8"),
        "mime_type": media_type or "image/jpeg",
    }
    resolution = image_resolution(kind)
    if resolution:
        part["resolution"] = resolution
    return part


def image_resolution(kind: str | None) -> str | None:
    """The "resolution" for a reading-call photo of this kind: its entry in
    settings.gemini_image_resolution, else the "*" entry, else None (no key
    sent: Gemini's default, "high" on Gemini 3). Per image because the
    Interactions API has no request-wide media resolution, and per kind
    because a close-up of a dent wants the detail an overview doesn't --
    ultra_high is 2240 tokens a photo against 1120, on a 32-photo call."""
    if kind is None:
        return None
    table = image_resolutions(settings.gemini_image_resolution)
    return table.get(kind) or table.get(ANY)


# call_structured's purpose -> the settings that tune that kind of call
# (config.gemini_thinking_level_read / _sort / _second_look and
# gemini_read_tools). Keyed by the purpose each caller already passes --
# extraction.draft_report, triage.triage_photos, second_look.look -- so
# none of them changes; the benchmark keys its accounting by them too. The
# Telegram bot drafts through draft_report, so "draft" is its call as well.
# A purpose not here (annex suggestions) is sent as it always was.
CALL_KINDS = {"draft": "read", "photo sort": "sort", "second look": "second_look"}
# Contract 8 D2: a zone call (ai/zone_read.py) is part of the reading and
# runs on its chain, so it takes the reading's thinking level and tools.
CALL_KINDS["draft_zone"] = "read"


def request_settings(purpose: str, model_id: str) -> dict:
    """The generation settings to add to one try's request: generation_config
    with a thinking_level, and tools on the reading call. {} when none is
    set -- the request is then byte for byte what it was before they
    existed. Field names as google-genai 2.22.0's interactions.create takes
    them (GenerationConfigParam.thinking_level, the code_execution
    ToolParam)."""
    kind = CALL_KINDS.get(purpose)
    if kind is None:
        return {}
    extra: dict = {}
    levels = thinking_levels(getattr(settings, f"gemini_thinking_level_{kind}", None))
    level = levels.get(model_id) or levels.get(ANY)
    if level:
        extra["generation_config"] = {"thinking_level": level}
    if kind == "read":
        tools = read_tools(settings.gemini_read_tools)
        if tools:
            extra["tools"] = [{"type": name} for name in tools]
    return extra


# Seconds to wait before asking a busy model (is_overloaded) once more, and
# before asking the whole chain again when every model was busy.
OVERLOADED_RETRY_SECONDS = 8.0
CHAIN_BUSY_RETRY_SECONDS = 20.0

_OVERLOADED = re.compile(r"\b503\b|UNAVAILABLE|overloaded|high demand", re.IGNORECASE)


def is_overloaded(error: Exception) -> bool:
    """Google answered "busy, try again later" (503 UNAVAILABLE, "currently
    experiencing high demand") rather than refusing this request."""
    if isinstance(error, ValueError):
        return False  # an answer that didn't parse, whatever it quotes
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int):
        return status == 503
    return bool(_OVERLOADED.search(str(error)))


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
    judge_key: bool = True,
    _busy_round: int = 0,
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

    A model whose free allowance for the day is used up on a key -- any key,
    the company's too -- is remembered until Google's reset (ai/allowance.py)
    and skipped on that key until then, with nothing sent.

    judge_key=False: what this call's chain says about an own key is not
    stored on it (the call still ends in AiUnavailable when that key was all
    there was). The second look passes it: its own short chain being used
    up says nothing about the reading's models, and storing "quota" (or
    "invalid" for "limit: 0") would switch the person's key off for every
    AI call (review, 2026-11-09).
    """
    # Two nested fallbacks. Inner: each model in the chain, since they have
    # independent per-model quotas. Outer: each key, since every model
    # shares one key's allowance -- once a key is exhausted across the
    # whole chain, only a different key helps. Ordered so a single key's
    # full chain is tried before moving on, rather than burning every key
    # on the first model.
    keys = resolve_keys(actor)
    last_error: Exception | None = None
    # Whether every try so far failed for the day's allowance (a skip, or a
    # 429 per day / "limit: 0"): then the call ends in allowance.DayUsedUp.
    all_day = True
    # Whether every model that was asked answered "busy" (is_overloaded):
    # then the whole chain is asked once more after CHAIN_BUSY_RETRY_SECONDS.
    # None until a model answers; a model skipped for its spent day leaves it
    # as it is -- the strong model is spent most of the day, which is when
    # the light ones being busy matters (review, 2026-11-10).
    all_busy = None
    # The AiUnavailable code of each own key that failed for its own reasons.
    refused: list[str] = []
    # The run log (ai/runs.py) gets the request's size once, and each try
    # below as it ends, through the calling thread's collector.
    if not _busy_round:
        runs.note_input(_input_size(system_instruction, input_parts))

    for key in keys:
        client = get_client(key)
        own = key.source == "own"
        # refusal() of each model's last error, for an own key.
        verdicts: list[str | None] = []
        # The last error Google itself sent for this key in this call: the
        # words its row keeps (record_result). None when every model was
        # skipped below.
        key_error: Exception | None = None
        for model_id in models:
            # This model's free day is used up on this key: skipped with no
            # request and no rate-limit slot. Asking anyway would only get
            # the same 429 (whose "retry in 33s" we used to wait out), every
            # call until midnight Pacific. To the key's outcome below it is
            # the 429 it would have been.
            spent_until = allowance.spent_until(key.fingerprint, model_id)
            if spent_until is not None:
                logger.info(
                    "Gemini model %r skipped on %s: its free daily allowance is used up until %s",
                    model_id, key.label, allowance.iso(spent_until),
                )
                _note_attempt(key, model_id, "day_spent", 0.0, time.monotonic(), None, spent_until=spent_until)
                verdicts.append("no_allowance" if allowance.limit(key.fingerprint, model_id) == 0 else "quota")
                last_error = allowance.DaySpent(model_id)
                continue
            # Every attempt takes a slot, not just the first. Previously the
            # gate ran once before these loops, so a draft that fell through
            # the chain fired up to five requests back-to-back -- which is
            # what tripped the quota during testing.
            waited = acquire(key.rate_limit_id)
            verdict = None
            # Per model: a thinking level may be set for one model of the
            # chain only.
            extra = request_settings(purpose, model_id)
            # One retry per model on a per-minute 429, using the delay the
            # server itself supplies. Falling straight through to the next
            # model is pointless: it draws on the same per-key quota and
            # 429s too. (A day's 429 is not retried: see below.)
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
                        # A model that refuses a setting (a thinking level
                        # it hasn't got) answers 400: that try fails like
                        # any other error and the chain moves on.
                        **extra,
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
                    last_error = key_error = exc
                    said = refusal(exc)
                    all_busy = is_overloaded(exc) and all_busy is not False
                    if said not in DAY_VERDICTS:
                        all_day = False
                    # The day's allowance gone on this model: remembered
                    # until the reset, whoever's key it is -- the company
                    # key's free day runs out just the same.
                    spent_until = None
                    if said in DAY_VERDICTS:
                        spent_until = allowance.mark_spent(key.fingerprint, model_id, allowance.limit_from(exc))
                    _note_attempt(
                        key, model_id, _outcome(exc, interaction), waited, started, interaction, spent_until=spent_until
                    )
                    # What it says about the key itself is kept for an own
                    # key only; a company key's row is left as before P4.
                    verdict = said if own else None
                    # A refused key or a spent day's allowance won't come
                    # back in 30 seconds: no wait, straight to the next.
                    # Only a per-minute 429 (no verdict) is waited out.
                    delay = retry_after_seconds(exc) if said is None and is_rate_limit_error(exc) else None
                    # Google "busy" (503, "high demand"): usually gone in
                    # seconds, and the light models -- what most reads run
                    # on -- see it most. One short wait on the same model
                    # beats ending a run whose last models are both busy.
                    if delay is None and said is None and is_overloaded(exc):
                        delay = OVERLOADED_RETRY_SECONDS
                    if delay is not None and attempt == 1:
                        logger.warning(
                            "Gemini model %r rate-limited or busy on %s; waiting %.1fs before asking again",
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
            # Every model skipped and the key already marked quota (being
            # tried again after QUOTA_RETRY_AFTER): nothing new was learned,
            # and recording it would wipe the row's words from Google.
            if judge_key and (key_error is not None or not key.retrying):
                record_result(actor, key, outcome, key_error)
            refused.append(AI_QUOTA_USED if outcome == "quota" else AI_KEY_INVALID)

    if refused and len(refused) == len(keys):
        # Their own key was all they had: say what's wrong with it rather
        # than "the AI failed". (Not chained: the SDK's error stays in the
        # log lines above, scrubbed.)
        raise AiUnavailable(refused[-1]) from None
    if all_busy and not _busy_round:
        # Every model of the chain said "busy" (each already asked twice):
        # a short pause and one more round, rather than failing a run when
        # the light models -- what most reads run on -- are all busy at once.
        logger.warning("Every Gemini model was busy; asking the chain again in %.0fs", CHAIN_BUSY_RETRY_SECONDS)
        time.sleep(CHAIN_BUSY_RETRY_SECONDS)
        return call_structured(
            purpose=purpose, system_instruction=system_instruction, input_parts=input_parts, schema=schema,
            parse=parse, models=models, actor=actor, timeout=timeout, judge_key=judge_key, _busy_round=1,
        )
    raise (allowance.DayUsedUp if all_day and last_error is not None else RuntimeError)(
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
    refused it), 429, timeout (ours or the SDK's), or error. (A try never
    sent, its model's day used up, is "day_spent".)"""
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


def _thought_tokens(interaction) -> int | None:
    """Thinking tokens, when the response says: billed as output but
    reported apart from it -- what a thinking level costs."""
    usage = getattr(interaction, "usage", None)
    if usage is not None:
        return _count(getattr(usage, "total_thought_tokens", None))
    meta = getattr(interaction, "usage_metadata", None)
    if meta is not None:
        return _count(getattr(meta, "thoughts_token_count", None))
    return None


def _tool_calls(interaction) -> int:
    """Code the model ran on this try (its code_execution_call steps):
    whether a tools trial used the tool at all."""
    steps = getattr(interaction, "steps", None)
    if not isinstance(steps, (list, tuple)):
        return 0
    return sum(1 for step in steps if getattr(step, "type", None) == "code_execution_call")


def _note_attempt(key, model_id: str, outcome: str, waited, started: float, interaction, *, spent_until=None) -> None:
    """One try for the run log (ai/runs.py). Never raises: a log that can't
    be kept must not turn an answer into a failure. waited: seconds spent
    before the try was sent (a rate-limit slot, or the delay a 429 asked
    for). spent_until: when the model's free day on this key comes back,
    on a try skipped for it (day_spent) or a 429 that said it was used up
    -- what the review step's "Read by the backup model" notice is told."""
    try:
        attempt = {
            "model": model_id,
            "key_source": key.source,
            "key_last4": last4(key.value) if key.value else None,
            "outcome": outcome,
            "waited_s": round(float(waited), 1) if isinstance(waited, (int, float)) and not isinstance(waited, bool) else 0.0,
            "ms": 0 if outcome == "day_spent" else int((time.monotonic() - started) * 1000),
        }
        if spent_until is not None:
            attempt["spent_until"] = allowance.iso(spent_until)
        prompt_tokens, output_tokens = _tokens(interaction) if interaction is not None else (None, None)
        if prompt_tokens is not None:
            attempt["prompt_tokens"] = prompt_tokens
        if output_tokens is not None:
            attempt["output_tokens"] = output_tokens
        thought_tokens = _thought_tokens(interaction) if interaction is not None else None
        if thought_tokens is not None:
            attempt["thought_tokens"] = thought_tokens
        tool_calls = _tool_calls(interaction) if interaction is not None else 0
        if tool_calls:
            attempt["tool_calls"] = tool_calls
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
    if isinstance(error, allowance.DaySpent):
        return error  # our own words (a model name), never Google's: nothing to scrub
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
