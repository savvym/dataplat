"""Application settings — S002-F-002, extended S004-F-004, extended S007-F-007,
extended S011-F-011 (MinIO / S3 settings), extended S022-F-022 (documents bucket),
extended S023-F-023 (Lance global chunks table bucket).

Reads configuration from environment variables via pydantic-settings.
DATABASE_URL is set by docker-compose.dev.yml on the fastapi service.
DAGSTER_GRAPHQL_URL is set by docker-compose.dev.yml; renamed from
DAGSTER_GRAPHQL in S004-F-004 (update local .env if you have the old name).
SECRET_KEY is set by docker-compose.dev.yml (S007-F-007); no default — fast
fail at startup if absent.
MINIO_ENDPOINT / MINIO_ROOT_USER / MINIO_ROOT_PASSWORD are injected by
docker-compose.dev.yml lines 223-225 (environment block on the fastapi
service); defaults match the compose dev values so the service starts without
explicit env-var changes in non-compose contexts.
MINIO_SOURCES_BUCKET / MINIO_DOCUMENTS_BUCKET / MINIO_LANCE_BUCKET are NOT
injected by docker-compose; Python defaults "sources", "documents", and "lance"
match the buckets created by minio-init.
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

    # Added S011-F-011: MinIO / S3 settings for raw source file storage.
    # Soft defaults match docker-compose.dev.yml dev values (agreed.md §3-D2).
    # Raw source files are id-keyed (not CAS) per design doc line 252 / agreed §3-D4.
    MINIO_ENDPOINT: str = "minio:9000"  # host:port, no scheme
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "devpassword"
    MINIO_SOURCES_BUCKET: str = "sources"  # bucket created by minio-init

    # Added S022-F-022: documents bucket for extracted DoclingDocument JSON + images.
    # Per design doc §4.3, documents are stored at s3://documents/{source_id}/{extractor}/
    MINIO_DOCUMENTS_BUCKET: str = "documents"

    # Added S023-F-023: Lance bucket for the global chunks table.
    # Matches the bucket created by minio-init (F-003). Default "lance" so
    # no docker-compose.dev.yml change is needed.
    MINIO_LANCE_BUCKET: str = "lance"

    # Added S047-F-047: datasets bucket for Parquet + metadata artifacts written by F-044.
    # Default "datasets" matches MINIO_DATASETS_BUCKET env default in hf_dataset_io_manager.py.
    # Deferred from F-043 (contracts/S043-F-043/agreed.md §Out of Scope item 4).
    MINIO_DATASETS_BUCKET: str = "datasets"

    # Added S050-F-050: shared secret for the Dagster run-status webhook.
    # Default "" allows CI unit tests to run without the env var; production MUST
    # set a non-empty value.  The POST /api/dagster/events handler checks for an
    # empty value and returns HTTP 500 (fail-closed) rather than accepting any caller
    # that sends an empty X-Dagster-Webhook-Secret header (agreed.md §7 OQ-2 fix).
    DAGSTER_WEBHOOK_SECRET: str = ""

    # Added S053-F-053: in-process per-minute rate limit for LLM gateway calls.
    # Default 60 allows up to 60 LLM calls per 60-second sliding window.
    # Override via env var LLM_RATE_LIMIT_PER_MINUTE for tighter/looser limits.
    LLM_RATE_LIMIT_PER_MINUTE: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}


settings: Settings = Settings()  # type: ignore[call-arg]
