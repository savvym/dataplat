# docker/dagster/Dockerfile
# Build context: REPO ROOT  (compose uses context: . dockerfile: docker/dagster/Dockerfile)
# All COPY paths are relative to the repo root.
#
# Installs Dagster 1.9.10 + dependencies inline (no requirements.txt).
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
RUN pip install --no-cache-dir \
    dagster==1.9.10 \
    dagster-webserver==1.9.10 \
    dagster-postgres==1.9.10 \
    psycopg2-binary==2.9.10

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
