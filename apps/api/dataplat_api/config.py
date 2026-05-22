"""Application settings — S002-F-002.

Reads configuration from environment variables via pydantic-settings.
DATABASE_URL is set by docker-compose.dev.yml on the fastapi service.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str

    model_config = {"env_file": ".env", "extra": "ignore"}


settings: Settings = Settings()  # type: ignore[call-arg]
