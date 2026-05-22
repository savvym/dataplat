# dagster_platform/definitions.py
# Dagster code location for the dataplat platform.
# F-005: hello_world_job — smoke job to verify orchestration end-to-end.
# Assets, schedules, and sensors for the full asset graph are added in later
# sprints (F-010+).

from dagster import Definitions, job, op


@op
def hello_op(context) -> None:  # type: ignore[no-untyped-def]
    """Minimal op that logs a greeting. Used by hello_world_job (F-005)."""
    context.log.info("hello world")


@job
def hello_world_job() -> None:
    """Smoke job: runs hello_op to verify the Dagster orchestration layer works.

    Registered in Definitions so the webserver can discover and launch it.
    This job is intentionally trivial — it exists only to prove that
    FastAPI → DagsterGateway → Dagster GraphQL → executor → worker pipeline
    functions end-to-end before any real asset processing is wired up.
    """
    hello_op()


defs = Definitions(
    jobs=[hello_world_job],
)
