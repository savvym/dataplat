# dagster_platform/definitions.py
# Dagster code location for the dataplat platform.
# F-005: hello_world_job — smoke job to verify orchestration end-to-end.
# F-012: sources_partitions + source_asset — external asset for uploaded sources,
#        partitioned by DynamicPartitionsDefinition("sources"). FastAPI notifies
#        Dagster after each upload via addDynamicPartition + reportRunlessAssetEvents.

from dagster import (
    AssetExecutionContext,
    AssetSpec,
    Definitions,
    DynamicPartitionsDefinition,
    MaterializeResult,
    asset,
    job,
    op,
)

# F-012: Dynamic partition definition for source uploads.
# Partition key format: "src_{source_id}" (set by FastAPI after DB flush).
# Confirmed working in Dagster 1.11.16 (S012-F-012 agreed.md §3-D-asset).
sources_partitions = DynamicPartitionsDefinition(name="sources")

# F-012: External asset — Dagster does not materialise it; FastAPI reports
# materialization events via reportRunlessAssetEvents after each upload.
# AssetSpec is the correct API in Dagster 1.11.16 (external_asset_from_spec
# does NOT exist in this version — confirmed via introspection).
source_asset = AssetSpec(key="source", partitions_def=sources_partitions)


@asset(
    partitions_def=sources_partitions,
    description="MinerU extraction stub — F-018 trigger wiring. Real extraction logic: F-019.",
)
def extract_mineru(context: AssetExecutionContext) -> MaterializeResult:
    """Stub asset: logs the partition key and yields a trivial result.

    F-018 scope: wiring only. The real MinerU PDF→document extraction body
    is F-019. Do NOT add extraction logic here.
    """
    partition_key = context.partition_key
    context.log.info("extract_mineru stub: partition_key=%s", partition_key)
    return MaterializeResult()


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
    assets=[source_asset, extract_mineru],
)
