from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
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
    database_url: str = "sqlite:///./carlink.db"
    storage_dir: str = "./storage"
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
    gemini_min_call_interval_seconds: float = 10.0


settings = Settings()
