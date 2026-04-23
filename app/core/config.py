from pathlib import Path
from pydantic_settings import BaseSettings

# Points to the repo-root .env for local dev; silently ignored inside Docker
# (Docker passes all settings as environment variables via docker-compose).
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@learnexus.lk"
    aae_inactivity_days: int = 7
    aae_cron_hour: int = 9
    phone_encryption_key: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    parent_summary_day: str = "mon"
    parent_summary_hour: int = 8

    model_config = {
        "env_file": str(_ENV_FILE) if _ENV_FILE.exists() else None,
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
