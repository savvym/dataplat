#!/usr/bin/env sh
# docker/minio/init-buckets.sh
# One-shot bucket initialisation for the Dataplat dev stack.
# Run by the minio-init service (minio/mc image) after minio is healthy.
#
# Credentials are passed via the MC_HOST_init env var (set by docker-compose);
# format: http://<user>:<password>@<host>:<port>
# NOTE: if you change MINIO_ROOT_PASSWORD to include URL-special chars
# (@, :, /, ?, #), switch to `mc alias set` with --api S3v4 and pass
# creds via STDIN rather than the MC_HOST URL authority.
#
# The alias name "init" is local to this container only and does not
# need to match the alias used in checks.sh V1 verification.

set -eu

ALIAS=init

echo "Waiting for mc to confirm MinIO is reachable..."
mc ready "${ALIAS}"

echo "Creating buckets (--ignore-existing for idempotency)..."
mc mb --ignore-existing "${ALIAS}/sources"
mc mb --ignore-existing "${ALIAS}/documents"
# S3/MinIO bucket names prohibit underscores; 'documents-vlm' maps to the
# design doc's 'documents_vlm' concept (s3://documents_vlm/ in §4.3).
# Storage URIs in application code must use 'documents-vlm' as the bucket name.
mc mb --ignore-existing "${ALIAS}/documents-vlm"
mc mb --ignore-existing "${ALIAS}/lance"
mc mb --ignore-existing "${ALIAS}/datasets"

echo "Bucket initialisation complete."
exit 0
