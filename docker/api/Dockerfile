# docker/api/Dockerfile
# Build context: REPO ROOT  (compose uses context: . dockerfile: docker/api/Dockerfile)
# All COPY paths are relative to the repo root.
#
# Installs FastAPI + uvicorn + SQLAlchemy async + asyncpg (pinned per agreed.md §8a).
# SQLAlchemy and asyncpg are declared now (not used until F-002) so pyproject.toml
# does not need editing when DB code is added.

FROM python:3.12-slim

WORKDIR /app

# Copy and install the FastAPI package
COPY apps/api/ /app/

RUN pip install --no-cache-dir \
    fastapi==0.115.12 \
    "uvicorn[standard]==0.34.2" \
    "sqlalchemy[asyncio]==2.0.41" \
    asyncpg==0.30.0 \
    alembic==1.14.1 \
    "pydantic-settings==2.7.0" \
    && pip install --no-cache-dir -e .

# Default command (compose overrides this to add --reload + bind mount)
CMD ["uvicorn", "dataplat_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
