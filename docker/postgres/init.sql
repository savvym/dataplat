-- docker/postgres/init.sql
-- Mounted at /docker-entrypoint-initdb.d/01-init.sql:ro
-- Executed by postgres:16 as the superuser on FIRST BOOT only
-- (only runs when pg_data volume is empty).
-- The 'platform' database is already created by POSTGRES_DB env var
-- before this script runs.  We only need to create the Dagster database.

CREATE DATABASE platform_dagster;
