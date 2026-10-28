import threading
from typing import TYPE_CHECKING

from google import genai
from google.genai import types

if TYPE_CHECKING:
    from app.ai.keys import AiKey

# No retries inside the SDK: call_structured does its own (one wait of at most
# 30s on a 429, then the next model). The SDK's loop sleeps for whatever
# Retry-After the server sends. On 2026-10-22 a model whose daily quota was
# spent sent ~70,000s, and the call slept ~19 hours instead of failing over.
_HTTP_OPTIONS = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=0))

# One cached client per key, by its fingerprint (ai/keys.AiKey) -- never by
# the key itself. Building a client per call would be wasteful; with
# everyone's own keys there are more of them now, so the oldest is dropped
# past this many (a dropped one is simply built again when next used).
MAX_CLIENTS = 64
_clients: dict[str, genai.Client] = {}
_lock = threading.Lock()


def get_client(key: "AiKey") -> genai.Client:
    """The client for one key from ai/keys.resolve_keys.

    Never builds one without a key: genai.Client() with none quietly takes
    GEMINI_API_KEY from the environment, which would turn "this person has
    no key" into "the company pays"."""
    if key is None or not getattr(key, "value", None) or not getattr(key, "fingerprint", None):
        raise ValueError("get_client needs an AI key")
    with _lock:
        client = _clients.get(key.fingerprint)
        if client is None:
            while len(_clients) >= MAX_CLIENTS:
                _clients.pop(next(iter(_clients)))
            client = genai.Client(api_key=key.value, http_options=_HTTP_OPTIONS)
            _clients[key.fingerprint] = client
        return client


def reset_client_cache() -> None:
    """Called after company keys are added/removed so a deleted key's
    client doesn't linger and keep getting used."""
    with _lock:
        _clients.clear()
