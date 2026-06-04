"""Async S3/MinIO client dependency — S011-F-011.

Provides:
  get_s3_client(): FastAPI async-generator dependency that yields an aioboto3
  S3 client configured from settings.  Tests override this dependency via
  app.dependency_overrides[get_s3_client] exactly as they override get_session.

Design decisions (agreed.md §3-D1, §3-D7):
  - Per-request client (not a module-level singleton) for simplicity and to
    avoid resource-leak edge-cases in async contexts.  A shared pool is a
    future optimisation.
  - endpoint_url is constructed as f"http://{settings.MINIO_ENDPOINT}" because
    MINIO_ENDPOINT is injected as host:port (no scheme) by docker-compose.
  - Hard invariant #2: raw source files are id-keyed (not CAS).  CAS applies
    only to processed artifacts (document_variant / commits).  This module
    stores bytes at sources/{source_id}/original.pdf per design doc line 252.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import aioboto3  # type: ignore[import-untyped]

from dataplat_api.config import settings


async def get_s3_client() -> AsyncGenerator[Any, None]:
    """FastAPI dependency — yields an aioboto3 S3 client.

    Used for both the sources bucket (F-011) and the datasets bucket (F-047).
    The caller selects the bucket by passing the appropriate settings value
    (settings.MINIO_SOURCES_BUCKET or settings.MINIO_DATASETS_BUCKET) to each
    put_object / get_object / generate_presigned_url call.

    Test override:
        app.dependency_overrides[get_s3_client] = _mock_s3_dep
    """
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=f"http://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ROOT_USER,
        aws_secret_access_key=settings.MINIO_ROOT_PASSWORD,
    ) as client:
        yield client
