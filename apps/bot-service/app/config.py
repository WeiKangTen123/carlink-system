from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BOT_SERVICE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = (BOT_SERVICE_DIR / "carlink.db").resolve()
DEFAULT_STORAGE_DIR = (BOT_SERVICE_DIR / "storage").resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BOT_SERVICE_DIR / ".env"), ".env"),
        extra="ignore",
    )

    telegram_bot_token: str = ""
    # Comma-separated Telegram user or chat ids allowed to use the bot, on
    # top of every chat that has already filed a case through it (see the
    # allow-list in channels/telegram.py). Stands in until account linking.
    telegram_allowed_ids: str = ""
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
    # Sweeps idle intake drafts and stale tmp uploads (intake/cleanup.py).
    # Off in tests and scripts that import the app; set in production.
    run_background_cleanup: bool = False
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    storage_dir: str = str(DEFAULT_STORAGE_DIR)
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


settings = Settings()
