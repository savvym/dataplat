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
# F-025: lancedb (Lance vector table), pyarrow (Arrow schema + record batches),
# tiktoken (cl100k_base BPE tokeniser for fixed-size chunking).
# F-029: fasttext-langdetect (language detection, fasttext lid.176.ftz model via
# fasttext-predict C extension; original pin 1.0.6 unavailable — using 1.1.1,
# module import changed from fasttext_langdetect to ftlangdetect in 1.1.1).
# F-030: datasketch>=1.6 (MinHash + MinHashLSH for near-duplicate clustering);
# no version pin unless the local PyPI mirror restricts availability.
RUN pip install --no-cache-dir \
    dagster==1.11.16 \
    dagster-webserver==1.11.16 \
    dagster-postgres==0.27.16 \
    psycopg2-binary==2.9.10 \
    boto3==1.37.38 \
    docling-core==2.77.0 \
    lancedb==0.30.2 \
    pyarrow==24.0.0 \
    tiktoken==0.7.0 \
    pytest==8.3.4 \
    fasttext-langdetect==1.1.1 \
    datasketch

# Copy the entire dagster/ directory from the repo root into the image.
# After this COPY:
#   /app/dagster/workspace.yaml       — code-location declaration
#   /app/dagster/dagster.yaml         — Postgres storage config
#   /app/dagster/dagster_platform/    — minimal Python package (empty Definitions)
COPY dagster/ /app/dagster/

# F-025: bake the tiktoken cl100k_base BPE vocabulary into the image so that
# the encoder loads instantly at runtime without fetching from the internet
# (R4 mitigation in agreed.md — air-gapped / slow-network environments).
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# F-029: bake fasttext lid.176.ftz model into the image (avoids runtime download).
# Uses low_memory=True to select the compressed lid.176.ftz model (~900KB).
# FTLANG_CACHE set to /app/fasttext-models so the cached model persists in the
# image layer and is available at runtime without a network call.
ENV FTLANG_CACHE=/app/fasttext-models
RUN python -c "from ftlangdetect import detect; detect('hello world', low_memory=True)"

# PYTHONPATH: lets Python find dagster_platform.definitions
ENV PYTHONPATH=/app/dagster

# DAGSTER_HOME: Dagster reads dagster.yaml and workspace.yaml from this directory
ENV DAGSTER_HOME=/app/dagster
