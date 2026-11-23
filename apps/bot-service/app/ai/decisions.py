"""The Decisions API (contract 9, sections 2 and 4.7): closed yes/no
questions about photos, each answered as a calibrated yes-probability with
no text -- what the re-check (ai/check.py) asks about every suggestion.

Why this and not one more Gemini call: its model, gemini-decision-1, has a
free bucket of its own (200 a minute, 10,000 a day per project), answered
the lead's live try in 0.77 s and writes no output tokens, so the sort,
the reading and the zone reads keep their allowances. It is not in the
pinned google-genai (2.22.0), so this is a plain httpx POST, in the shape
that answered on 15 Nov:

    POST https://generativelanguage.googleapis.com/v1beta/decisions
    x-goog-api-key: <the key>
    {"model": "gemini-decision-1",
     "input": [{"type": "text", "text": "Photo P07:"},
               {"type": "image", "data": <base64>, "mime_type": "image/webp", "resolution": "medium"}, ...],
     "questions": {"F1": {"type": "noul", "instructions": "In photo P07, is ..."}}}
    200 {"answers": {"F1": {"noul": 0.83}}, ...}     noul: the yes-probability, 0.01-0.99

A question is text only -- no region, no system prompt -- so it names its
photo by label.

The same keys, rate limiter and allowance as structured.call_structured:
the person's keys in their order (keys.resolve_keys, a public report's
rule included), one slot of the shared 12-a-minute window per request
(rate_limit.acquire), the model skipped on a key whose free day is used up
(allowance), and every try on the run log (runs.note_attempt: model, the
key's last 4, outcome, time, tokens). What a try says about an own key is
never stored on it (call_structured's judge_key=False): this one model
running out says nothing about the reading's.

A failure is never waited out: a 400 (Google's "safety" refusal of a photo
among them), a 429, a 5xx, a timeout or an answer without the
probabilities raises DecisionFailed, and the re-check falls back to the
lite call or leaves the suggestions as they are. A 429 or a refused key
moves on to the next key first (its own allowance); the others end the
call at once -- another key won't unblock a photo Google refused or wake a
busy model.

The key goes in the header and nowhere else: never in the URL, a log line,
the run log or an error, whose words are scrubbed of it.
"""
import json
import logging
import re
import time

import httpx

from app.ai import allowance, runs
from app.ai.keys import AiActor, last4, record_result, resolve_keys
from app.ai import quota_guard
from app.ai.quota_guard import LaneRefused
from app.ai.rate_limit import acquire
from app.ai.structured import _create_within, _input_size
from app.config import settings

logger = logging.getLogger(__name__)

URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
# The benchmark counts these calls under this purpose (call_structured's
# purposes are the kinds of call; this is one more).
PURPOSE = "decision"
# Seconds of Google's time a whole re-check may spend here, over both of
# its requests and every key; the wait for a rate-limit slot isn't counted
# (the limiter is ours). Answers come in about a second.
TIME_BOX = 20.0
# On top of the request's own timeout, before the wall clock gives up on it
# (httpx's timeout is per read, not for the whole request).
DEADLINE_MARGIN = 2.0
# Tests put an httpx.MockTransport here: nothing leaves the machine.
TRANSPORT: httpx.BaseTransport | None = None

# Google's words for a used-up day, or no allowance at all, in a 429 --
# allowance.py's rule.
_DAY_WORDS = re.compile(r"PerDay|per[ _-]day|\blimit: 0\b", re.IGNORECASE)
_SAFETY = re.compile(r"safety", re.IGNORECASE)
_KEY_REFUSED = re.compile(r"API_KEY_INVALID|API key not valid|API key expired", re.IGNORECASE)
# Error words kept in a log line or an exception, scrubbed.
MAX_DETAIL = 160


class DecisionFailed(RuntimeError):
    """The Decisions API gave no usable answer. why:

        "safety"        Google refused the input (400, error code "safety")
        "rate_limited"  a 429 on every key
        "day_spent"     the model's free day used up on every key
        "refused"       every key refused (401, 403, a key-invalid 400)
        "busy"          503
        "server"        another 5xx
        "timeout"       no answer within the time box
        "bad_answer"    an answer without the probabilities
        "error"         anything else (another 400, the network)

    Its words are scrubbed of the key and of anything plate-like. status:
    the last request's HTTP status, when it got one."""

    def __init__(self, why: str, detail: str | None = None, status: int | None = None):
        self.why = why
        self.status = status
        super().__init__(f"Decisions API: {why}" + (f" ({detail})" if detail else ""))


class _TryFailed(Exception):
    """One request's failure, for the key loop. outcome: the run log's
    (call_structured's) word for it; why: DecisionFailed's."""

    def __init__(self, outcome: str, why: str, status: int | None = None, detail: str | None = None,
                 limit: int | None = None):
        super().__init__(why)
        self.outcome, self.why, self.status, self.detail, self.limit = outcome, why, status, detail, limit


# Which failures another key may get past (its own allowance, its own
# project); the rest end the call.
_NEXT_KEY = ("rate_limited", "day_spent", "refused")


def decide(*, purpose: str = PURPOSE, input_parts: list[dict], questions: dict[str, dict], actor: AiActor,
           model: str | None = None, budget: float = TIME_BOX) -> dict:
    """One request: {"answers": {question key: yes-probability 0..1},
    "model": the model, "seconds": Google's time, tries included}. A
    question without a probability in the answer is left out of answers;
    none at all is a bad answer.

    budget: the seconds of Google's time this request may take, over every
    key it tries. Raises DecisionFailed, or keys.AiUnavailable when the
    person has no key to try (as call_structured does)."""
    model = model or settings.gemini_decision_model
    keys = resolve_keys(actor)
    payload = json.dumps({"model": model, "input": input_parts, "questions": questions}).encode("utf-8")
    runs.note_input(_input_size(" ".join(str(q.get("instructions") or "") for q in questions.values()), input_parts))
    left = float(budget)
    spent = 0.0
    last: _TryFailed | None = None
    for key in keys:
        until = allowance.spent_until(key.fingerprint, model)
        if until is not None:
            # Asking would only get the day's 429 again: skipped, as
            # call_structured skips it, with nothing sent.
            logger.info("Decisions API skipped on %s: %s's free daily allowance is used up until %s",
                        key.label, model, allowance.iso(until))
            _note(key, model, "day_spent", 0.0, time.monotonic(), spent_until=until)
            last = _TryFailed("day_spent", "day_spent")
            continue
        if left <= 0:
            raise DecisionFailed("timeout", f"no answer within {budget:.0f}s")
        try:
            # As call_structured: the old call without the shared folder.
            waited = (acquire(key.rate_limit_id) if quota_guard.mode() == "local"
                      else acquire(key.rate_limit_id, model=model, fingerprint=key.fingerprint))
        except LaneRefused as refused:
            # The staging lane's share of the shared key is used up
            # (ai/quota_guard.py): nothing sent, as a spent day.
            logger.info("Decisions API not asked on %s: %s", key.label, refused.why)
            _note(key, model, "day_spent", 0.0, time.monotonic(), spent_until=allowance.next_reset())
            last = _TryFailed("day_spent", "day_spent")
            continue
        started = time.monotonic()
        try:
            answers, tokens = _post(key, payload, list(questions), left)
        except _TryFailed as failed:
            took = time.monotonic() - started
            left -= took
            spent += took
            until = None
            if failed.why == "day_spent":
                until = allowance.mark_spent(key.fingerprint, model, failed.limit)
            _note(key, model, failed.outcome, waited, started, status=failed.status, spent_until=until)
            logger.warning("Decisions API %s on %s: %s", failed.why, key.label, failed.detail or "-")
            last = failed
            if failed.why in _NEXT_KEY:
                continue
            raise DecisionFailed(failed.why, failed.detail, failed.status) from None
        took = time.monotonic() - started
        _note(key, model, "ok", waited, started, status=200, tokens=tokens)
        record_result(actor, key, "ok")
        logger.info("Decisions API answered %d of %d question(s) on %s in %.1fs",
                    len(answers), len(questions), key.label, took)
        return {"answers": answers, "model": model, "seconds": round(spent + took, 2)}
    if last is None:
        raise DecisionFailed("refused", "no key to try")
    raise DecisionFailed(last.why, last.detail, last.status)


def _post(key, payload: bytes, wanted: list[str], timeout: float) -> tuple[dict[str, float], dict]:
    """One POST on one key: ({question key: p}, tokens). Raises _TryFailed."""
    def send() -> httpx.Response:
        with httpx.Client(transport=TRANSPORT, timeout=httpx.Timeout(timeout)) as client:
            return client.post(URL, content=payload,
                               headers={"x-goog-api-key": key.value, "Content-Type": "application/json"})

    try:
        response = _create_within(timeout + DEADLINE_MARGIN, send)
    except (TimeoutError, httpx.TimeoutException):
        raise _TryFailed("timeout", "timeout", detail=f"no answer within {timeout:.0f}s") from None
    except httpx.HTTPError as exc:
        raise _TryFailed("error", "error", detail=type(exc).__name__) from None
    status = response.status_code
    raw = response.text or ""
    detail = _detail(raw, key.value, status)
    if status == 200:
        try:
            body = response.json()
        except ValueError:
            raise _TryFailed("schema", "bad_answer", status, "not JSON") from None
        answers = probabilities(body, wanted)
        if not answers:
            raise _TryFailed("schema", "bad_answer", status, "no probabilities in the answer")
        return answers, _tokens(body)
    if status == 429:
        if _DAY_WORDS.search(raw):
            raise _TryFailed("429", "day_spent", status, detail, allowance.limit_from(raw))
        raise _TryFailed("429", "rate_limited", status, detail)
    if status in (401, 403) or (status == 400 and _KEY_REFUSED.search(raw)):
        raise _TryFailed("error", "refused", status, detail)
    if status == 400 and _SAFETY.search(raw):
        raise _TryFailed("error", "safety", status, detail)
    if status == 503:
        raise _TryFailed("error", "busy", status, detail)
    if status >= 500:
        raise _TryFailed("error", "server", status, detail)
    raise _TryFailed("error", "error", status, detail)


def probabilities(body, wanted: list[str]) -> dict[str, float]:
    """{question key: yes-probability} from an answer: answers[key].noul, a
    number from 0 to 1. A question missing, or given anything else, is
    left out."""
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        return {}
    out: dict[str, float] = {}
    for name in wanted:
        value = answers.get(name)
        if isinstance(value, dict):
            value = value.get("noul")
        if isinstance(value, dict):
            value = value.get("probability")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            p = float(value)
        except ValueError:
            continue
        if 0.0 <= p <= 1.0:
            out[name] = p
    return out


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _tokens(body) -> dict:
    """The tokens the answer reports, under the run log's names; {} when it
    says nothing (never a made-up 0)."""
    usage = body.get("usage") or body.get("usageMetadata") or body.get("usage_metadata") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return {}
    out = {}
    for name, fields in (("prompt_tokens", ("total_input_tokens", "input_tokens", "promptTokenCount", "prompt_token_count")),
                         ("output_tokens", ("total_output_tokens", "output_tokens", "candidatesTokenCount",
                                            "candidates_token_count"))):
        value = next((_count(usage.get(f)) for f in fields if _count(usage.get(f)) is not None), None)
        if value is not None:
            out[name] = value
    return out


def _detail(raw: str, key_value: str, status: int) -> str | None:
    """Google's words on a failed request, made safe to log: its error
    message when the body has one, scrubbed (no key, no quoted input,
    nothing plate-like), short."""
    if status == 200:
        return None
    words = raw
    try:
        body = json.loads(raw)
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            words = " ".join(str(error.get(k)) for k in ("code", "status", "message") if error.get(k) is not None)
    except ValueError:
        pass
    text = runs.scrub_error(f"{status} {words}", (key_value,))
    return text[:MAX_DETAIL] if text else str(status)


def _note(key, model: str, outcome: str, waited, started: float, *, status: int | None = None,
          tokens: dict | None = None, spent_until=None) -> None:
    """One try for the run log, as structured._note_attempt writes them (the
    benchmark counts them the same way). Never raises."""
    try:
        attempt = {
            "model": model,
            "key_source": key.source,
            "key_last4": last4(key.value) if key.value else None,
            "outcome": outcome,
            "waited_s": round(float(waited), 1) if isinstance(waited, (int, float)) and not isinstance(waited, bool) else 0.0,
            "ms": 0 if outcome == "day_spent" else int((time.monotonic() - started) * 1000),
        }
        if status is not None:
            attempt["http_status"] = status
        if status == 503:
            attempt["busy"] = True
        if spent_until is not None:
            attempt["spent_until"] = allowance.iso(spent_until)
        attempt.update(tokens or {})
        runs.note_attempt(attempt)
    except Exception:
        logger.warning("AI run log: couldn't note a Decisions API try", exc_info=True)
