from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BOT_SERVICE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = (BOT_SERVICE_DIR / "carlink.db").resolve()
DEFAULT_STORAGE_DIR = (BOT_SERVICE_DIR / "storage").resolve()
# /app/secrets in the container (BOT_SERVICE_DIR is /app there).
DEFAULT_SECRETS_DIR = (BOT_SERVICE_DIR / "secrets").resolve()

# The values Gemini's Interactions API takes (google-genai 2.22.0:
# GenerationConfig.thinking_level, ImageContent.resolution, the
# code_execution tool), for the model settings below.
THINKING_LEVELS = ("minimal", "low", "medium", "high")
IMAGE_RESOLUTIONS = ("low", "medium", "high", "ultra_high")
READ_TOOLS = ("code_execution",)
# The entry of a model setting that applies to every model (or every photo
# kind) not named in it.
ANY = "*"


def _entries(text: str | None) -> list[str]:
    return [e.strip() for e in (text or "").split(",") if e.strip()]


def thinking_levels(text: str | None) -> dict[str, str]:
    """A gemini_thinking_level_* setting as {model: level}. "medium" is
    {"*": "medium"}, every model; "gemini-3.5-flash-lite=medium" names one
    model, and a model not named gets no level at all (its own default);
    both together, "low,gemini-3-flash-preview=high", give the bare level to
    the rest. Per model because one chain mixes the strong model (default
    high) with the lite ones (default minimal): raising the lite reader's
    level must not lower the strong one's. {} when unset. Raises ValueError
    on a level Gemini doesn't have."""
    out: dict[str, str] = {}
    for entry in _entries(text):
        model, _, level = entry.rpartition("=")
        model, level = model.strip() or ANY, level.strip().lower()
        if level not in THINKING_LEVELS:
            raise ValueError(f"thinking level {level!r}: use one of {', '.join(THINKING_LEVELS)}")
        out[model] = level
    return out


def image_resolutions(text: str | None) -> dict[str, str]:
    """gemini_image_resolution as {photo kind: resolution}: "damage=ultra_high,
    tyre=ultra_high,overview=medium"; "*=medium" for every kind not named.
    {} when unset. Raises ValueError on a malformed entry or a resolution
    Gemini doesn't have. The kinds are the sorter's (ai/triage.PHOTO_KINDS)
    plus "unsorted"; not checked here (config can't import the sorter), the
    benchmark checks them."""
    out: dict[str, str] = {}
    for entry in _entries(text):
        kind, sep, resolution = entry.partition("=")
        kind, resolution = kind.strip().lower(), resolution.strip().lower()
        if not sep or not kind:
            raise ValueError(f"image resolution {entry!r}: write it as kind=resolution, e.g. damage=ultra_high")
        if resolution not in IMAGE_RESOLUTIONS:
            raise ValueError(f"image resolution {resolution!r}: use one of {', '.join(IMAGE_RESOLUTIONS)}")
        out[kind] = resolution
    return out


def read_tools(text: str | None) -> list[str]:
    """gemini_read_tools as tool names. [] when unset. Raises ValueError on
    a tool this code doesn't know how to send."""
    names = list(dict.fromkeys(e.lower() for e in _entries(text)))
    unknown = [n for n in names if n not in READ_TOOLS]
    if unknown:
        raise ValueError(f"read tools {unknown}: only {', '.join(READ_TOOLS)}")
    return names


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BOT_SERVICE_DIR / ".env"), ".env"),
        extra="ignore",
    )

    telegram_bot_token: str = ""
    # Where people open Carlink in a browser, for links sent outside the
    # dashboard: the Telegram bot's "open your case" and "connect first"
    # messages. No trailing slash needed. Set in infra/docker-compose.prod.yml.
    public_base_url: str = "https://carlink.34-45-253-162.sslip.io"
    gemini_api_key: str = ""
    # Ordered by real capability, not just cost -- confirmed live:
    # gemini-3-flash-preview is the only one of these that has actually
    # produced a real damage bounding box in testing (see extraction.py's
    # bbox_* prompt); 3.6/3.7-flash are untested-but-plausible mid-tier
    # candidates; the two -lite models never localize but reliably draft
    # everything else, so they're the last-resort fallback that keeps a
    # report drafting even once the stronger models' (much lower) daily
    # quota is exhausted. "antigravity" was in this list before but
    # doesn't accept image input at all (confirmed: HTTP 400 "Image input
    # modality is not enabled") -- every report has photos, so it could
    # never have actually served a request here.
    gemini_model_chain: str = "gemini-3-flash-preview,gemini-3.6-flash,gemini-3.7-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    # Dashboard intake sorts every photo of a case (often 40-80) before the
    # real drafting call. Sorting needs no bounding boxes -- the one thing
    # only the stronger models do -- so it runs lite-first, keeping the
    # stronger models' much smaller daily quota for the drafting call.
    gemini_triage_model_chain: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3-flash-preview"
    # The second look after an intake reading (ai/second_look.py): ONE call
    # per read on its own chain, so it never draws on the reading model's
    # small free daily allowance. Provisional: chosen after measuring models
    # with scripts/benchmark_intake.py --second-look-models. NB on the free
    # tier gemini-3.5-flash and gemini-3.6-flash share ONE allowance of 20 a
    # day (Google's 429 for 3.5 names 3.6), the backup reader's.
    gemini_second_look_chain: str = "gemini-3.5-flash,gemini-3.6-flash"
    # Off until the benchmark shows it finds more parts without many wrong
    # ones (owner's gate, 2026-11-09): no second-look call at all and the
    # Review step shows no suggestions. The benchmark turns it on for a run
    # given --second-look-models.
    second_look_enabled: bool = False
    # Generation settings for each kind of reading call, all unset by
    # default: an unset one sends nothing, so the request is exactly what
    # it was before they existed (contract 6, 2026-11-10). The lite models
    # read at their default thinking level, which is "minimal" on
    # gemini-3.5-flash-lite. Set them to measure (scripts/benchmark_intake.py
    # --thinking-level-read ...), then here once a level has earned it.
    #
    # Thinking level per call kind: "minimal", "low", "medium" or "high" for
    # every model of the chain, or per model ("gemini-3.5-flash-lite=medium",
    # see thinking_levels). A model that refuses a level (gemini-3.8-flash
    # has no "minimal") fails that try like any other error and the chain
    # moves on to the next model.
    gemini_thinking_level_read: str | None = None
    gemini_thinking_level_sort: str | None = None
    gemini_thinking_level_second_look: str | None = None
    # Per photo kind on the reading call, e.g. "damage=ultra_high,
    # tyre=ultra_high,overview=medium" (image_resolutions). Each image item
    # gets that "resolution" (low/medium/high/ultra_high = 280/560/1120/2240
    # tokens on Gemini 3; Gemini's default is high); a kind not named is sent
    # without one. Sorting and the second look are never given one.
    gemini_image_resolution: str | None = None
    # Tools for the reading call only: "code_execution" lets the model crop
    # and zoom into a photo itself (Agentic Vision). With a JSON schema this
    # is a preview feature on Gemini 3 (Google's structured-output docs),
    # untested on the lite models: a trial setting.
    gemini_read_tools: str | None = None
    # Sweeps idle intake drafts and stale tmp uploads (intake/cleanup.py).
    # Off in tests and scripts that import the app; set in production.
    run_background_cleanup: bool = False
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    storage_dir: str = str(DEFAULT_STORAGE_DIR)
    # Holds ai-keys.secret, which seals people's own AI keys (ai/secretbox.py).
    # Its own volume in production (/app/secrets: the api writes, the bot
    # only reads), never under storage_dir, which /files serves and the
    # backups copy.
    secrets_dir: str = str(DEFAULT_SECRETS_DIR)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # Minimum spacing enforced between Gemini calls, per process (see
    # extraction.py's _wait_for_rate_limit) -- a conservative placeholder
    # until real RPM numbers from the account's actual quota tier are
    # available to tune this precisely. Only paces calls made *within* one
    # process (telegram-bot and api are separate processes sharing the same
    # GEMINI_API_KEY, so this doesn't coordinate across them) -- a real
    # cross-process limit would need a shared store (Redis, or the sqlite
    # db both containers already mount), not worth the added infra until
    # this is actually the bottleneck in practice.
    # Minimum gap between consecutive calls, so a burst under the per-minute
    # cap still arrives spaced rather than all at once.
    gemini_min_call_interval_seconds: float = 4.0
    # Hard ceiling per API key per rolling 60s, enforced across BOTH the API
    # and bot processes (see ai/rate_limit.py). Deliberately under the free
    # tier's 20/min for gemini-3-flash, because one draft can spend several
    # requests walking the fallback chain.
    gemini_max_requests_per_minute: int = 12
    # Dashboard sign-in (app/auth). A session ends after this long without
    # use, and in any case this many days after signing in.
    auth_session_idle_hours: float = 12
    auth_session_max_days: float = 7
    # The session cookie is only sent over HTTPS. Production is HTTPS (host
    # nginx, infra/host-nginx/); browsers also accept it on http://localhost.
    # Set false only for plain-HTTP access from another machine.
    auth_cookie_secure: bool = True
    # New passwords: off = at least 10 characters (the owner's choice for
    # now); on = at least 15 plus the common/predictable/personal checks in
    # app/auth/passwords.py. The dashboard's hints say 10 -- update
    # MIN_PASSWORD_LENGTH in apps/dashboard/lib/signup.ts when switching.
    strict_password_rules: bool = False
    # Uploads and new cases are refused (507 disk_full, and a reply from the
    # Telegram bot) once the storage disk has less than this many GB free: a
    # full disk breaks SQLite writes for both processes, and the VM's ~10GB
    # disk is shared with another app. See storage/files.disk_space_low.
    min_free_disk_gb: float = 3.0
    # /docs, /redoc and /openapi.json. Off: they list every route to anyone
    # who signs up.
    enable_api_docs: bool = False

    # A typo in one of the model settings stops the app at start-up rather
    # than being sent to Gemini (a 400 on every call) or silently ignored.
    @field_validator("gemini_thinking_level_read", "gemini_thinking_level_sort", "gemini_thinking_level_second_look")
    @classmethod
    def _thinking_level(cls, value):
        return normal_thinking_levels(value)

    @field_validator("gemini_image_resolution")
    @classmethod
    def _image_resolution(cls, value):
        return normal_image_resolutions(value)

    @field_validator("gemini_read_tools")
    @classmethod
    def _read_tools(cls, value):
        return normal_read_tools(value)


# Each model setting written the one way, None when blank (unset). Raise
# ValueError as the parsers above do. The benchmark checks its flags with
# them before it sets them on `settings` (an assignment isn't validated).
def normal_thinking_levels(value) -> str | None:
    if value is None:
        return None
    levels = thinking_levels(str(value))
    return ",".join(level if model == ANY else f"{model}={level}" for model, level in levels.items()) or None


def normal_image_resolutions(value) -> str | None:
    if value is None:
        return None
    return ",".join(f"{kind}={r}" for kind, r in image_resolutions(str(value)).items()) or None


def normal_read_tools(value) -> str | None:
    if value is None:
        return None
    return ",".join(read_tools(str(value))) or None


settings = Settings()
