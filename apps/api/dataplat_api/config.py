"""Application settings — S002-F-002, extended S004-F-004, extended S007-F-007.

Reads configuration from environment variables via pydantic-settings.
DATABASE_URL is set by docker-compose.dev.yml on the fastapi service.
DAGSTER_GRAPHQL_URL is set by docker-compose.dev.yml; renamed from
DAGSTER_GRAPHQL in S004-F-004 (update local .env if you have the old name).
SECRET_KEY is set by docker-compose.dev.yml (S007-F-007); no default — fast
fail at startup if absent.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    # default matches compose-internal DNS; can be overridden via env var.
    # Renamed from DAGSTER_GRAPHQL in sprint S004-F-004 — update your local
    # .env if you have the old name set.
    DAGSTER_GRAPHQL_URL: str = "http://dagster-webserver:3000/graphql"

    # Added S007-F-007: JWT auth settings.
    # SECRET_KEY has NO default — pydantic-settings raises ValidationError at
    # startup if absent, giving a fast fail rather than silent insecure operation.
    # docker-compose.dev.yml injects SECRET_KEY with a dev placeholder so the
    # container always starts; set a strong random value in production.
    SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_SECONDS: int = 3600  # 1 hour; override via env var for tests

    model_config = {"env_file": ".env", "extra": "ignore"}


settings: Settings = Settings()  # type: ignore[call-arg]
