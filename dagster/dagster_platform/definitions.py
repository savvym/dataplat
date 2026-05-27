# dagster_platform/definitions.py
# Dagster code location for the dataplat platform.
# F-005: hello_world_job — smoke job to verify orchestration end-to-end.
# F-012: sources_partitions + source_asset — external asset for uploaded sources,
#        partitioned by DynamicPartitionsDefinition("sources"). FastAPI notifies
#        Dagster after each upload via addDynamicPartition + reportRunlessAssetEvents.
# F-019: extract_mineru — real asset body: reads PDF from MinIO, produces a minimal
#        valid DoclingDocument JSON, writes it back to MinIO, writes a document_variant
#        row to Postgres. Uses helpers from extractor.py.
# F-025: chunks — real asset body: reads DoclingDocument, splits into ≤512-token
#        fixed-size chunks, writes rows to Lance 'chunks' table. Uses helpers
#        from chunker.py.
# F-026: LanceChunksIOManager — delegates all Lance writes for the chunks asset
#        to the IO manager (delete-before-insert idempotency). chunks asset now
#        returns list[dict] instead of MaterializeResult.
# F-027: attr_quality — column-mode update asset: reads existing chunks rows from
#        Lance, updates attr_quality_score and attr_quality_provider in-place using
#        a stub length-heuristic scorer. Zero new rows. Uses helpers from quality_tagger.py.
# F-029: attr_lang — column-mode update asset: detects language of each chunk using
#        fasttext lid.176.ftz, updates attr_lang_code and attr_lang_confidence in-place.
#        Zero new rows. Uses helpers from lang_tagger.py.

from typing import Any

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

from dagster_platform.lance_io_manager import LanceChunksIOManager

from dagster_platform.extractor import (
    build_docling_document,
    build_s3_client,
    estimate_page_count,
    insert_document_variant,
    read_pdf_bytes,
    write_document_json,
)

from dagster_platform.chunker import (
    extract_text_from_document,
    fixed_size_chunk,
    lookup_source_collection_id,
    read_docling_document,
)

from dagster_platform.quality_tagger import (
    update_quality_scores_in_lance,
)

from dagster_platform.lang_tagger import (
    update_lang_in_lance,
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
    io_manager_key="lance_chunks_io",
    description=(
        "Chunking (F-025/F-026): reads DoclingDocument from MinIO, splits text into "
        "≤512-token fixed-size chunks using tiktoken cl100k_base, writes rows "
        "to the Lance 'chunks' table at s3://{MINIO_LANCE_BUCKET}/chunks via "
        "LanceChunksIOManager (delete-before-insert idempotency)."
    ),
)
def chunks(context: AssetExecutionContext) -> list[dict[str, Any]]:
    """Real chunking asset (F-025 / F-026 IO manager refactor).

    Steps (agreed.md §3 / F-026 agreed.md D9):
      1. Parse source_id from partition key.
      2. Look up source_collection_id from Postgres.
      3. Build boto3 S3 client from MINIO_* env.
      4. Read DoclingDocument JSON from s3://documents/{source_id}/extract_mineru/doc.docling.json.
      5. Extract plain text (fallback chain: markdown → doc.name → f"source_{source_id}").
      6. Split into ≤512-token fixed-size chunks (tiktoken cl100k_base).
      7. Record asset-level metadata via context.add_output_metadata().
      8. Return rows — LanceChunksIOManager handles the Lance write (idempotent).
    """
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "chunks: starting for partition_key=%s source_id=%d",
        partition_key,
        source_id,
    )

    collection_id = lookup_source_collection_id(source_id)
    context.log.info("chunks: resolved collection_id=%d", collection_id)

    s3 = build_s3_client()

    doc = read_docling_document(s3, source_id)
    context.log.info("chunks: loaded DoclingDocument for source_id=%d", source_id)

    text = extract_text_from_document(doc, source_id)
    context.log.info("chunks: extracted text length=%d chars", len(text))

    rows = fixed_size_chunk(text, source_id=source_id, collection_id=collection_id)
    context.log.info("chunks: produced %d chunk(s)", len(rows))

    # Asset-level metadata (D9: moves from MaterializeResult to add_output_metadata).
    # IO-level metadata (row_count, mode) is added by LanceChunksIOManager.handle_output().
    context.add_output_metadata(
        {
            "source_id": MetadataValue.int(source_id),
            "chunk_count": MetadataValue.int(len(rows)),
            "text_length": MetadataValue.int(len(text)),
        }
    )
    return rows


@asset(
    partitions_def=sources_partitions,
    description=(
        "Quality tagger (F-028): updates attr_quality_score and attr_quality_provider "
        "columns on existing producer_asset='chunks' rows in Lance. Zero new rows created. "
        "Scores each chunk by calling the internal LLM gateway (POST /api/internal/llm/completions). "
        "attr_quality_provider is set to the model name returned by the gateway (e.g. "
        "'claude-3-haiku-20240307' or 'mock' in CI)."
    ),
)
def attr_quality(context: AssetExecutionContext) -> MaterializeResult:
    """Column-mode quality tagger asset (F-027).

    Steps (agreed.md §3 D2–D4):
      1. Parse source_id from partition key.
      2. Call update_quality_scores_in_lance() to update attr_quality_score and
         attr_quality_provider on existing producer_asset='chunks' rows.
         Returns row count (zero if no chunks exist for this source — no-op).
      3. Return MaterializeResult with source_id and rows_updated metadata.

    Does NOT use io_manager_key — this is a column-mode update, not a row insert.
    """
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "attr_quality: starting for partition_key=%s source_id=%d",
        partition_key,
        source_id,
    )

    row_count = update_quality_scores_in_lance(source_id)
    context.log.info(
        "attr_quality: updated %d row(s) for source_id=%d",
        row_count,
        source_id,
    )

    if row_count == 0:
        context.log.warning(
            "attr_quality: zero rows updated for source_id=%d — "
            "chunks may not yet exist for this source",
            source_id,
        )

    return MaterializeResult(
        metadata={
            "source_id": MetadataValue.int(source_id),
            "rows_updated": MetadataValue.int(row_count),
        }
    )


@asset(
    partitions_def=sources_partitions,
    description=(
        "Lang tagger (F-029): updates attr_lang_code and attr_lang_confidence "
        "columns on existing producer_asset='chunks' rows in Lance using fasttext "
        "lid.176.ftz. Zero new rows created."
    ),
)
def attr_lang(context: AssetExecutionContext) -> MaterializeResult:
    """Column-mode language tagger asset (F-029).

    Steps (agreed.md §3 D12):
      1. Parse source_id from partition key.
      2. Call update_lang_in_lance() to update attr_lang_code and
         attr_lang_confidence on existing producer_asset='chunks' rows.
         Returns row count (zero if no chunks exist for this source — no-op).
      3. Return MaterializeResult with source_id and rows_updated metadata.

    Does NOT use io_manager_key — this is a column-mode update, not a row insert.
    """
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "attr_lang: starting for partition_key=%s source_id=%d",
        partition_key,
        source_id,
    )

    row_count = update_lang_in_lance(source_id)
    context.log.info(
        "attr_lang: updated %d row(s) for source_id=%d",
        row_count,
        source_id,
    )

    if row_count == 0:
        context.log.warning(
            "attr_lang: zero rows updated for source_id=%d — "
            "chunks may not yet exist",
            source_id,
        )

    return MaterializeResult(
        metadata={
            "source_id": MetadataValue.int(source_id),
            "rows_updated": MetadataValue.int(row_count),
        }
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
    assets=[source_asset, extract_mineru, chunks, attr_quality, attr_lang],
    resources={"lance_chunks_io": LanceChunksIOManager()},
)
