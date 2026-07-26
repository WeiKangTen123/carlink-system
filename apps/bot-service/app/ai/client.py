from google import genai

from app.config import settings

_client: genai.Client | None = None


def get_client() -> genai.Client:
    """Lazily construct the Gemini client.

    Passes the configured key if set; otherwise lets the SDK fall back to
    the GEMINI_API_KEY environment variable.
    """
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else genai.Client()
    return _client
