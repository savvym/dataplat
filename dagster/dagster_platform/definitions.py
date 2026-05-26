# dagster_platform/definitions.py
# Dagster code location for the dataplat platform.
# F-005: hello_world_job — smoke job to verify orchestration end-to-end.
# F-012: sources_partitions + source_asset — external asset for uploaded sources,
#        partitioned by DynamicPartitionsDefinition("sources"). FastAPI notifies
#        Dagster after each upload via addDynamicPartition + reportRunlessAssetEvents.
# F-019: extract_mineru — real asset body: reads PDF from MinIO, produces a minimal
#        valid DoclingDocument JSON, writes it back to MinIO, writes a document_variant
#        row to Postgres. Uses helpers from extractor.py.

from dagster import (
    AssetExecutionContext,
    AssetSpec,
    Definitions,
    DynamicPartitionsDefinition,
    MaterializeResult,
    MetadataValue,
    asset,
    job,
    op,
)

from dagster_platform.extractor import (
    build_docling_document,
    build_s3_client,
    estimate_page_count,
    insert_document_variant,
    read_pdf_bytes,
    write_document_json,
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
    description=(
        "MinerU extraction (F-019): reads PDF from MinIO, produces a minimal "
        "schema-valid DoclingDocument JSON, writes to s3://documents/{source_id}/"
        "extract_mineru/doc.docling.json, and inserts a document_variant row to Postgres."
    ),
)
def extract_mineru(context: AssetExecutionContext) -> MaterializeResult:
    """Real extraction asset (F-019).

    Steps (agreed.md §3):
      1. Parse source_id from partition key.
      2. Build boto3 S3 client from MINIO_* env.
      3. Read PDF bytes from s3://sources/{source_id}/original.pdf.
      4. Estimate page count (best-effort regex; 0 on failure).
      5. Build minimal valid DoclingDocument JSON (no DocumentOrigin/binary_hash).
      6. Write JSON to s3://documents/{source_id}/extract_mineru/doc.docling.json.
      7. Insert document_variant row (psycopg2, PLATFORM_DB_URL, ON CONFLICT DO NOTHING).
      8. Return MaterializeResult with metadata.
    """
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "extract_mineru: starting for partition_key=%s source_id=%d",
        partition_key,
        source_id,
    )

    s3 = build_s3_client()

    pdf_bytes = read_pdf_bytes(s3, source_id)
    context.log.info(
        "extract_mineru: read %d bytes from sources/%d/original.pdf",
        len(pdf_bytes),
        source_id,
    )

    page_count = estimate_page_count(pdf_bytes)
    context.log.info("extract_mineru: estimated page_count=%d", page_count)

    doc_json = build_docling_document(source_id, pdf_bytes, page_count)
    context.log.info(
        "extract_mineru: built DoclingDocument JSON (%d bytes)", len(doc_json)
    )

    write_document_json(s3, source_id, doc_json)
    context.log.info(
        "extract_mineru: wrote doc.docling.json to documents/%d/extract_mineru/",
        source_id,
    )

    insert_document_variant(source_id, page_count, context.run_id)
    context.log.info(
        "extract_mineru: inserted document_variant row (run_id=%s)", context.run_id
    )

    return MaterializeResult(
        metadata={
            "source_id": MetadataValue.int(source_id),
            "page_count": MetadataValue.int(page_count),
            "bytes": MetadataValue.int(len(pdf_bytes)),
            "storage_key": MetadataValue.text(
                f"documents/{source_id}/extract_mineru/doc.docling.json"
            ),
        }
    )


@asset(
    partitions_def=sources_partitions,
    description=(
        "Chunking (F-024 stub): body raises NotImplementedError. "
        "F-025 will implement the real chunking logic (read DoclingDocument from MinIO, "
        "split into chunks, write to Lance table)."
    ),
)
def chunks(context: AssetExecutionContext) -> MaterializeResult:
    """Stub chunking asset (F-024).

    The real body will be implemented in F-025. This stub exists so that:
      - The Dagster partition definition (`sources_partitions`) is shared.
      - POST /api/runs?asset=chunks can launch a backfill immediately (F-024).
      - The asset appears in the Dagster UI asset catalog.
    Raises NotImplementedError unconditionally so that any accidental execution
    fails loudly rather than silently producing incorrect output.
    """
    raise NotImplementedError(
        "chunks asset body not yet implemented — see F-025"
    )


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
    assets=[source_asset, extract_mineru, chunks],
)
