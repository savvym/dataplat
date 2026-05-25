# docker/dagster/Dockerfile
# Build context: REPO ROOT  (compose uses context: . dockerfile: docker/dagster/Dockerfile)
# All COPY paths are relative to the repo root.
#
# Installs Dagster 1.11.16 + dependencies inline (no requirements.txt).
# Note: agreed.md §8a originally specified dagster==1.9.10, but that version
# is not present on the local PyPI mirror (versions 1.7.x–1.10.x are missing).
# 1.11.16 is the closest available compatible release and ships the same
# /dagster_version endpoint. dagster-postgres uses 0.x versioning where 0.27.x
# corresponds to dagster 1.11.x. See contracts/S001-F-001/review-final.md addendum.
# psycopg2-binary is required by dagster-postgres / SQLAlchemy to connect to Postgres;
# without it the webserver crashes at startup before the healthcheck can pass.
#
# ENV PYTHONPATH=/app/dagster  — makes dagster_platform importable (it lives at
#   /app/dagster/dagster_platform/ after COPY dagster/ /app/dagster/)
# ENV DAGSTER_HOME=/app/dagster — tells Dagster where to find dagster.yaml and
#   workspace.yaml, wiring the Postgres storage config and code-location together.

FROM python:3.12-slim

WORKDIR /app

# Install Dagster and its Postgres storage backend (pinned per agreed.md §8a)
# F-019: boto3 (S3/MinIO client), docling-core (DoclingDocument type for minimal-valid
# extraction output), pytest (needed to run dagster/tests/ unit tests inside the container
# via `python -m pytest` in the checks.sh extract) layer — not otherwise in this image).
RUN pip install --no-cache-dir \
    dagster==1.11.16 \
    dagster-webserver==1.11.16 \
    dagster-postgres==0.27.16 \
    psycopg2-binary==2.9.10 \
    boto3==1.37.38 \
    docling-core==2.77.0 \
    pytest==8.3.4

# Copy the entire dagster/ directory from the repo root into the image.
# After this COPY:
#   /app/dagster/workspace.yaml       — code-location declaration
#   /app/dagster/dagster.yaml         — Postgres storage config
#   /app/dagster/dagster_platform/    — minimal Python package (empty Definitions)
COPY dagster/ /app/dagster/

# PYTHONPATH: lets Python find dagster_platform.definitions
ENV PYTHONPATH=/app/dagster

# DAGSTER_HOME: Dagster reads dagster.yaml and workspace.yaml from this directory
ENV DAGSTER_HOME=/app/dagster
