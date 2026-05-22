"""Application settings — S002-F-002, extended S004-F-004.

Reads configuration from environment variables via pydantic-settings.
DATABASE_URL is set by docker-compose.dev.yml on the fastapi service.
DAGSTER_GRAPHQL_URL is set by docker-compose.dev.yml; renamed from
DAGSTER_GRAPHQL in S004-F-004 (update local .env if you have the old name).
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    # default matches compose-internal DNS; can be overridden via env var.
    # Renamed from DAGSTER_GRAPHQL in sprint S004-F-004 — update your local
    # .env if you have the old name set.
    DAGSTER_GRAPHQL_URL: str = "http://dagster-webserver:3000/graphql"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings: Settings = Settings()  # type: ignore[call-arg]
