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
# F-030: attr_minhash — column-mode update asset: computes MinHash signatures and
#        clusters near-duplicate chunks via MinHashLSH(threshold=0.85), updates
#        attr_minhash_signature, attr_minhash_cluster_id, attr_minhash_is_head in-place.
#        Zero new rows. Uses helpers from minhash_tagger.py.
# F-031: LanceChunksIOManager column mode — tagger assets (attr_quality, attr_lang,
#        attr_minhash) now return list[dict] and route through LanceChunksIOManager
#        column mode (D3a partial merge_insert). compute_*_scores() functions handle
#        read-only scoring; IOManager owns the Lance write.
# F-043: dataset — real asset body: reads Lance chunks filtered by recipe_snapshot,
#        calls LLM gateway to synthesise Q+A pairs (sft_synthesis_qa), writes train/
#        val Parquet splits + recipe.json + README.md to MinIO via HFDatasetIOManager.
#        Replaces the F-042 no-op stub. io_manager_key="hf_dataset_io" added.
# F-050: fastapi_run_status_sensor — run-status sensor that fires on every Dagster
#        run status change (STARTED/SUCCESS/FAILURE/CANCELED) and POSTs the event to
#        POST /api/dagster/events on the FastAPI service. Uses the dagster/backfill
#        tag (OQ-1 fix) to resolve the backfill ID stored in Run.dagster_run_id.

from typing import Any
import os
from datetime import datetime, timezone

import requests

from dagster import (
    AssetExecutionContext,
    AssetSpec,
    Definitions,
    DagsterRunStatus,
    DynamicPartitionsDefinition,
    MaterializeResult,
    MetadataValue,
    RunStatusSensorContext,
    asset,
    job,
    op,
    run_status_sensor,
)

from dagster_platform.lance_io_manager import LanceChunksIOManager
from dagster_platform.hf_dataset_io_manager import HFDatasetIOManager
from dagster_platform.sft_synthesis_qa import (
    DatasetOutput,
    call_llm_gateway,
    deterministic_split,
    fetch_dataset_row,
    parse_dataset_partition_key,
    read_chunks_from_lance,
)

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
    compute_quality_scores,
)

from dagster_platform.lang_tagger import (
    compute_lang_scores,
)

from dagster_platform.minhash_tagger import (
    compute_minhash_scores,
)

# F-012: Dynamic partition definition for source uploads.
# Partition key format: "src_{source_id}" (set by FastAPI after DB flush).
# Confirmed working in Dagster 1.11.16 (S012-F-012 agreed.md §3-D-asset).
sources_partitions = DynamicPartitionsDefinition(name="sources")

# F-042: DynamicPartitionsDefinition for dataset versions.
# Partition key format: "ds_{recipe_id}_v{n}" (design doc §5.3, line 532).
# F-043 will replace the stub dataset asset body but must NOT change this
# definition name or the asset key "dataset" — they are frozen per agreed.md §6.
dataset_versions = DynamicPartitionsDefinition(name="dataset_versions")

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
    io_manager_key="lance_chunks_io",
    description=(
        "Quality tagger (F-028): scores each chunk via the internal LLM gateway "
        "(POST /api/internal/llm/completions), returns partial dicts routed through "
        "LanceChunksIOManager column mode (F-031) which updates attr_quality_score "
        "and attr_quality_provider columns on existing producer_asset='chunks' rows. "
        "Zero new rows created. attr_quality_provider is the model name returned by "
        "the gateway (e.g. 'claude-3-haiku-20240307' or 'mock' in CI)."
    ),
)
def attr_quality(context: AssetExecutionContext) -> list[dict[str, Any]]:
    """Column-mode quality tagger asset (F-027 / F-031 IOManager refactor).

    Steps:
      1. Parse source_id from partition key.
      2. Call compute_quality_scores(source_id) — reads chunk texts from Lance,
         scores via LLM gateway, returns partial dicts (no Lance write).
      3. Add asset-level metadata via context.add_output_metadata().
      4. Return rows — LanceChunksIOManager column mode handles the Lance write.

    Does NOT call update_quality_scores_in_lance() (deprecated in F-031).
    """
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "attr_quality: starting for partition_key=%s source_id=%d",
        partition_key,
        source_id,
    )

    rows = compute_quality_scores(source_id)
    context.log.info(
        "attr_quality: scored %d chunk(s) for source_id=%d",
        len(rows),
        source_id,
    )

    if len(rows) == 0:
        context.log.warning(
            "attr_quality: zero rows scored for source_id=%d — "
            "chunks may not yet exist for this source",
            source_id,
        )

    context.add_output_metadata(
        {
            "source_id": MetadataValue.int(source_id),
            "chunk_count": MetadataValue.int(len(rows)),
        }
    )
    return rows


@asset(
    partitions_def=sources_partitions,
    io_manager_key="lance_chunks_io",
    description=(
        "Lang tagger (F-029): detects language of each chunk using fasttext "
        "lid.176.ftz, returns partial dicts routed through LanceChunksIOManager "
        "column mode (F-031) which updates attr_lang_code and attr_lang_confidence "
        "columns on existing producer_asset='chunks' rows. Zero new rows created."
    ),
)
def attr_lang(context: AssetExecutionContext) -> list[dict[str, Any]]:
    """Column-mode language tagger asset (F-029 / F-031 IOManager refactor).

    Steps:
      1. Parse source_id from partition key.
      2. Call compute_lang_scores(source_id) — reads chunk texts from Lance,
         detects language via fasttext, returns partial dicts (no Lance write).
      3. Add asset-level metadata via context.add_output_metadata().
      4. Return rows — LanceChunksIOManager column mode handles the Lance write.

    Does NOT call update_lang_in_lance() (deprecated in F-031).
    """
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "attr_lang: starting for partition_key=%s source_id=%d",
        partition_key,
        source_id,
    )

    rows = compute_lang_scores(source_id)
    context.log.info(
        "attr_lang: detected language for %d chunk(s) for source_id=%d",
        len(rows),
        source_id,
    )

    if len(rows) == 0:
        context.log.warning(
            "attr_lang: zero rows detected for source_id=%d — chunks may not yet exist",
            source_id,
        )

    context.add_output_metadata(
        {
            "source_id": MetadataValue.int(source_id),
            "chunk_count": MetadataValue.int(len(rows)),
        }
    )
    return rows


@asset(
    partitions_def=sources_partitions,
    io_manager_key="lance_chunks_io",
    description=(
        "MinHash dedup tagger (F-030): computes MinHash signatures and clusters "
        "near-duplicate chunks, returns partial dicts routed through "
        "LanceChunksIOManager column mode (F-031) which updates "
        "attr_minhash_signature, attr_minhash_cluster_id, attr_minhash_is_head "
        "in chunks table. Zero new rows created. Column-mode update only."
    ),
)
def attr_minhash(context: AssetExecutionContext) -> list[dict[str, Any]]:
    """Column-mode MinHash near-duplicate tagger asset (F-030 / F-031 IOManager refactor).

    Steps:
      1. Parse source_id from partition key.
      2. Call compute_minhash_scores(source_id) — reads chunk texts from Lance,
         computes MinHash signatures and LSH-based cluster assignments (sorted by
         chunk_id ascending for deterministic labels), returns partial dicts
         (no Lance write).
      3. Add asset-level metadata via context.add_output_metadata().
      4. Return rows — LanceChunksIOManager column mode handles the Lance write.

    Does NOT call update_minhash_in_lance() (deprecated in F-031).
    """
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "attr_minhash: starting for partition_key=%s source_id=%d",
        partition_key,
        source_id,
    )

    rows = compute_minhash_scores(source_id)
    context.log.info(
        "attr_minhash: computed minhash for %d chunk(s) for source_id=%d",
        len(rows),
        source_id,
    )

    if len(rows) == 0:
        context.log.warning(
            "attr_minhash: zero rows computed for source_id=%d — "
            "chunks may not yet exist",
            source_id,
        )

    context.add_output_metadata(
        {
            "source_id": MetadataValue.int(source_id),
            "chunk_count": MetadataValue.int(len(rows)),
        }
    )
    return rows


# F-043: Dataset materializer (replaces F-042 stub).
# Partition key format: "ds_{recipe_id}_v{n}" (design doc §5.3, line 532).
# io_manager_key="hf_dataset_io" added per agreed.md §6 "ADDABLE" carve-out.
# The partitions_def and asset key "dataset" are FROZEN (per F-042 agreed.md §6).
@asset(
    partitions_def=dataset_versions,
    io_manager_key="hf_dataset_io",
    description=(
        "Dataset materializer (F-043 sft_synthesis_qa): reads matching chunks "
        "from Lance using the recipe's filter predicate, calls the internal LLM "
        "gateway to synthesise Q+A pairs, performs a deterministic train/val split, "
        "and returns a DatasetOutput for HFDatasetIOManager to write train/val Parquet "
        "+ recipe.json + README.md to s3://datasets/{dataset_id}_{version_tag}/."
    ),
)
def dataset(context: AssetExecutionContext) -> DatasetOutput:
    """Real dataset materializer asset (F-043 sft_synthesis_qa).

    Steps (agreed.md §"Algorithm sketch"):
      1. Parse recipe_id + version_tag from partition key.
      2. Query Postgres for the dataset row (recipe_snapshot, dataset_id).
      3. Extract config from recipe_snapshot (filter, prompt_template, val_ratio,
         fallback_on_failure, max_tokens).
      4. Read chunks from Lance (optionally filtered by filter_sql).
      5. For each chunk: call LLM gateway to synthesise Q+A pair.
      6. Deterministic split of qa_rows into train/val buckets.
      7. Record asset-level metadata.
      8. Return DatasetOutput — HFDatasetIOManager handles MinIO writes.
    """
    partition_key = context.partition_key
    recipe_id, version_tag = parse_dataset_partition_key(partition_key)
    context.log.info(
        "dataset: starting for partition_key=%s recipe_id=%d version_tag=%s",
        partition_key,
        recipe_id,
        version_tag,
    )

    db_row = fetch_dataset_row(recipe_id, version_tag)
    dataset_id: int = db_row["id"]
    recipe_snapshot: dict[str, Any] = db_row["recipe_snapshot"]
    dataset_card_md: str | None = db_row["dataset_card_md"]
    context.log.info(
        "dataset: resolved dataset_id=%d hf_repo_uri=%s",
        dataset_id,
        db_row["hf_repo_uri"],
    )

    # Extract config from frozen recipe_snapshot.
    filter_sql: str | None = recipe_snapshot.get("filter", {}).get("where")
    template_config: dict[str, Any] = recipe_snapshot.get("schema", {}).get(
        "config", {}
    )
    prompt_template: str = template_config.get(
        "prompt_template",
        (
            "Generate a question and answer for the following text:\n\n"
            "{chunk_text}\n\n"
            'Respond with JSON: {{"instruction": "...", "output": "..."}}'
        ),
    )
    val_ratio: float = (
        recipe_snapshot.get("output", {}).get("splits", {}).get("validation", 0.1)
    )
    fallback_on_failure: bool = template_config.get("fallback_on_failure", True)
    max_tokens: int = template_config.get("max_tokens", 512)

    context.log.info(
        "dataset: config — filter_sql=%r val_ratio=%.2f max_tokens=%d "
        "fallback_on_failure=%s",
        filter_sql,
        val_ratio,
        max_tokens,
        fallback_on_failure,
    )

    chunks = read_chunks_from_lance(filter_sql)
    context.log.info("dataset: read %d chunk(s) from Lance", len(chunks))

    if not chunks:
        context.log.warning(
            "dataset: zero chunks found (recipe_id=%d version_tag=%s filter_sql=%r) "
            "— materializing empty dataset",
            recipe_id,
            version_tag,
            filter_sql,
        )

    qa_rows: list[dict[str, Any]] = []
    for chunk in chunks:
        prompt = prompt_template.format(chunk_text=chunk["text"])
        raw = call_llm_gateway(
            prompt,
            max_tokens=max_tokens,
            fallback_on_failure=fallback_on_failure,
        )
        if raw is not None:
            qa_rows.append(
                {
                    "instruction": raw["instruction"],
                    "output": raw["output"],
                    "chunk_id": chunk["chunk_id"],
                }
            )

    skipped = len(chunks) - len(qa_rows)
    context.log.info(
        "dataset: synthesised %d Q+A pair(s), skipped %d chunk(s)",
        len(qa_rows),
        skipped,
    )

    train_rows, val_rows = deterministic_split(qa_rows, val_ratio)
    context.log.info("dataset: split — train=%d val=%d", len(train_rows), len(val_rows))

    context.add_output_metadata(
        {
            "dataset_id": MetadataValue.int(dataset_id),
            "train_count": MetadataValue.int(len(train_rows)),
            "val_count": MetadataValue.int(len(val_rows)),
            "chunks_processed": MetadataValue.int(len(chunks)),
            "chunks_skipped": MetadataValue.int(skipped),
        }
    )

    return DatasetOutput(
        train_rows=train_rows,
        val_rows=val_rows,
        recipe_snapshot=recipe_snapshot,
        dataset_id=dataset_id,
        version_tag=version_tag,
        dataset_card_md=dataset_card_md,
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


# ── F-050: Run-status sensor ──────────────────────────────────────────────────
# Environment variables (injected by docker-compose.dev.yml):
#   FASTAPI_WEBHOOK_URL   — defaults to the compose-internal FastAPI address.
#   DAGSTER_WEBHOOK_SECRET — shared secret validated by POST /api/dagster/events.
_FASTAPI_WEBHOOK_URL: str = os.getenv(
    "FASTAPI_WEBHOOK_URL", "http://fastapi:8000/api/dagster/events"
)
_DAGSTER_WEBHOOK_SECRET: str = os.getenv("DAGSTER_WEBHOOK_SECRET", "")

_EVENT_TYPE_MAP: dict[DagsterRunStatus, str] = {
    DagsterRunStatus.STARTED: "RUN_START",
    DagsterRunStatus.SUCCESS: "RUN_SUCCESS",
    DagsterRunStatus.FAILURE: "RUN_FAILURE",
    DagsterRunStatus.CANCELED: "RUN_CANCELED",
}


@run_status_sensor(
    run_status_list=[
        DagsterRunStatus.STARTED,
        DagsterRunStatus.SUCCESS,
        DagsterRunStatus.FAILURE,
        DagsterRunStatus.CANCELED,
    ],
    name="fastapi_run_status_sensor",
    minimum_interval_seconds=5,
)
def fastapi_run_status_sensor(context: RunStatusSensorContext) -> None:
    """Post run status events to the FastAPI webhook (F-050).

    Fires for every Dagster run (all jobs + backfills). Unknown dagster_run_ids
    are silently ignored by FastAPI (HTTP 200 processed=False).

    Backfill-ID extraction (OQ-1 fix):
      Run.dagster_run_id stores the Dagster backfill ID (from launchPartitionBackfill
      → backfillId). A @run_status_sensor fires once per individual partition run,
      each with its own UUID (context.dagster_run.run_id). The sensor reads
      context.dagster_run.tags.get("dagster/backfill") — automatically stamped on
      every partition run inside a backfill by Dagster 1.x — and uses that as the
      dagster_run_id payload. For non-backfill runs (e.g. hello_world_job) the tag
      is absent; run_id is used as the fallback and those runs correctly return
      processed=False since they have no matching Run row.

    Delivery semantics: best-effort, NOT at-least-once. The try/except swallows all
    HTTP exceptions and returns normally so the Dagster daemon marks this tick SUCCESS
    and advances its cursor. A failed HTTP call means the event is permanently dropped
    — it will NOT be retried on the next tick. For at-least-once semantics, remove
    the try/except and let exceptions propagate; the daemon will re-attempt the same
    tick on the next poll interval. For MVP, best-effort delivery is acceptable
    (agreed.md §11 out-of-scope).
    """
    event_type = _EVENT_TYPE_MAP[context.dagster_run.status]
    # M1 fix: use the backfill tag when available so the lookup matches
    # Run.dagster_run_id (which stores the backfill ID, not the partition-run UUID).
    dagster_run_id: str = (
        context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id
    )
    payload = {
        "event_type": event_type,
        "dagster_run_id": dagster_run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(
            _FASTAPI_WEBHOOK_URL,
            json=payload,
            headers={"X-Dagster-Webhook-Secret": _DAGSTER_WEBHOOK_SECRET},
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as exc:
        context.log.warning(
            "fastapi_run_status_sensor: HTTP call failed (event dropped): %s", exc
        )
        # Do not raise — event is permanently dropped (best-effort; see docstring).
        # Sensor failure must not fail the run itself.


defs = Definitions(
    jobs=[hello_world_job],
    assets=[
        source_asset,
        extract_mineru,
        chunks,
        attr_quality,
        attr_lang,
        attr_minhash,
        dataset,
    ],
    sensors=[fastapi_run_status_sensor],
    resources={
        "lance_chunks_io": LanceChunksIOManager(),
        "hf_dataset_io": HFDatasetIOManager(),
    },
)
