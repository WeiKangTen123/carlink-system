from google import genai
from google.genai import types

from app.config import settings

# No retries inside the SDK: call_structured does its own (one wait of at most
# 30s on a 429, then the next model). The SDK's loop sleeps for whatever
# Retry-After the server sends. On 2026-10-22 a model whose daily quota was
# spent sent ~70,000s, and the call slept ~19 hours instead of failing over.
_HTTP_OPTIONS = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=0))

# One cached client per API key. Keys change rarely (an operator adding or
# removing one in Settings), but building a client per request would be
# wasteful, so they're memoised by key rather than as a single global.
_clients: dict[str, genai.Client] = {}


def available_api_keys() -> list[str]:
    """Every Gemini key this deployment can use, in priority order.

    Operator-managed keys (added via Settings, stored in the api_keys
    table) come first, then the GEMINI_API_KEY environment variable as a
    fallback -- so an existing env-var-only deployment keeps working
    untouched, and adding keys through the UI doesn't require a redeploy.

    Each key is a completely separate quota allowance across every model
    in the chain, which is the entire point of supporting more than one.
    """
    keys: list[str] = []

    # Imported here rather than at module scope: app.reports.db imports
    # config, and this module is imported by the extraction path, so a
    # top-level import risks a circular import at startup.
    try:
        from app.reports.db import SessionLocal
        from app.reports.models import ApiKey

        db = SessionLocal()
        try:
            rows = db.query(ApiKey).filter(ApiKey.provider == "gemini").order_by(ApiKey.created_at).all()
            keys.extend(r.key for r in rows if r.key)
        finally:
            db.close()
    except Exception:
        # A missing/locked table must never take down drafting -- fall
        # through to the environment key.
        pass

    if settings.gemini_api_key and settings.gemini_api_key not in keys:
        keys.append(settings.gemini_api_key)

    return keys


def get_client(api_key: str | None = None) -> genai.Client:
    """Client for a specific key, or the first available one.

    Passing no key preserves the original behaviour for any caller that
    doesn't care about rotation.
    """
    if api_key is None:
        keys = available_api_keys()
        api_key = keys[0] if keys else None

    if not api_key:
        # No key anywhere -- let the SDK raise its own clear error rather
        # than inventing one here.
        return genai.Client(http_options=_HTTP_OPTIONS)

    client = _clients.get(api_key)
    if client is None:
        client = genai.Client(api_key=api_key, http_options=_HTTP_OPTIONS)
        _clients[api_key] = client
    return client


def reset_client_cache() -> None:
    """Called after keys are added/removed so a deleted key's client
    doesn't linger and keep getting used."""
    _clients.clear()
