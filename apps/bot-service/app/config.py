from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    gemini_api_key: str = ""
    gemini_model_chain: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,antigravity"
    database_url: str = "sqlite:///./carlink.db"
    storage_dir: str = "./storage"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""


settings = Settings()
