# Product Specification — LLM Training Data Management Platform

> Source of truth: `docs/data_platform_design.md`. All section references below point to that document.

---

## Project Mission (§1.1)

This platform fills the gap between raw files and ready-to-train LLM datasets. Users upload PDF/docx/URL sources, run a visual processing pipeline, and produce HuggingFace-compatible datasets without writing pipeline code. The target audience is small teams and solo researchers for whom existing CLI tools (Dolma, Datatrove, NeMo Curator) are too high-friction.

---

## Domain Model (§2)

Six first-class concepts form the data model. See §2.1 for the full hierarchy diagram and §2.2 for relationships.

### Source (§2.1, §4.1)
A user-uploaded file (PDF, docx, pptx, md) or URL. Stored in MinIO at `s3://sources/`. Represented in Postgres as `source` rows grouped into `source_collection` containers (HF-style repo analogy). Each source gets a `dagster_partition_key` used throughout the pipeline.

### Document / Document Variant (§2.1, §4.1, §4.3)
The unified intermediate representation produced by an extractor from a source. Physically: a `DoclingDocument` JSON file plus extracted images, stored in MinIO at `s3://documents/`. One source can have multiple variants (one per extractor); exactly one is marked `is_canonical`.

### Chunk (§2.1, §4.2)
The smallest unit of data processing. Produced by running a chunking strategy against a canonical document. All chunks from all sources share a single global Lance table at `s3://lance/chunks/`. Identified by `chunk_id`; carries `source_id` and `producer_asset` for filtering.

### Attribute (§2.1, §4.2)
An annotation on a chunk, stored physically as a column in the Lance table. Added by Tagger operators (e.g., `attr_quality_score`, `attr_lang_code`, `attr_minhash_signature`). New attribute types = new columns; Lance schema evolution handles this.

### Recipe (§2.1, §7)
A declarative dataset-generation specification (JSON/YAML). Composed of six stages: source selection, chunk filtering (SQL on Lance), view rendering, schema template selection, sampling, and output format. Stored in Postgres `recipe` table. Editable until materialization.

### Dataset (§2.1, §4.1, §4.3)
The materialized output: HuggingFace-compatible Parquet files plus a frozen `recipe_snapshot`. Stored at `s3://datasets/`. Each materialization creates a new versioned partition; old versions are immutable. Carries `status` (pending/running/done/failed) and statistics.

### Supporting concepts
- **Operator**: any data-processing unit — Extractor, Tagger, Augmenter, or Materializer (§6.1). Registered in Postgres `operator` table. Self-describes its `config_schema` for UI form generation.
- **Run**: a Dagster execution mapped 1:1 to a business `run` row in Postgres (§4.1).

---

## Primary User Flows

Four entry points were identified in §1.2 and the three interaction patterns in §3.3:

### Flow 1: Upload and Extract (§3.3, §12.2 Phase 1)
User uploads a PDF. The platform stores it in MinIO, registers it in Postgres, and notifies Dagster via GraphQL that a new `source` partition exists. User then triggers extraction; Dagster runs the MinerU extractor worker, which produces a `DoclingDocument` JSON and writes a `document_variant` record.

### Flow 2: Browse Chunks (§3.3, §10.3)
User opens the Chunks Explorer. Enters a SQL-like filter. FastAPI queries Lance directly (no Dagster involved). Results are returned with counts and attribute distributions. User can inspect a single chunk's source references and lineage.

### Flow 3: Create and Preview a Recipe (§7, §10.4)
User opens Recipe Editor. Selects source collections, adds SQL filter on chunk attributes, picks a schema template (e.g., `sft_synthesis_qa`), sets sampling and output options. Right panel shows a live 3-sample preview. User saves the recipe.

### Flow 4: Materialize a Dataset (§3.3, §9.1)
User triggers materialization of a saved recipe. FastAPI creates a `dataset` row (status=pending), launches a Dagster backfill. Worker reads matching chunks from Lance, calls LLM via gateway if needed, writes Parquet files to MinIO, updates dataset status to `done`. WebSocket pushes progress to the frontend.

---

## MVP Boundary

### IN — from §12.1

- Source upload (PDF only)
- One extractor: MinerU
- One chunking strategy (fixed-size)
- Three taggers: quality_gpt4, lang_fasttext, minhash_dedup
- One schema template: sft_synthesis_qa
- Four UI pages: Sources, Chunks Explorer, Recipes, Datasets

### OUT — explicitly deferred (§12.1 non-goals, §1.3, §12.4)

The following are out of scope for MVP and must not be implemented without explicit human approval:

**From §12.1:**
- Augmenter operators
- Multiple extractor switching or comparison (docling, firecrawl, md passthrough)
- VLM enrichment (document_vlm_enriched asset)
- Lineage UI page
- Recipe raw YAML editing mode
- Complex sampling strategies (stratified, per_source_caps beyond uniform)

**From §1.3:**
- 10M+ chunk distributed processing
- Multi-tenant access control, billing
- Model training itself
- Real-time data streams
- General RAG application use

**From §12.4 (deferred/undecided):**
- Self-registration, password reset email, MFA, OAuth, social login
- Repository-level granular ACL (MVP uses `visibility = private|internal` only)
- Operator marketplace ("pull from GitHub")
- HuggingFace Hub one-click export
- Dataset diff UI
- Argilla integration

**From CLAUDE.md §Hard invariants / Scope discipline:**
- Celery or Dagster executor beyond multiprocess — MVP uses RQ patterns with Dagster multiprocess
- Docker-in-Docker plugin sandbox — MVP uses subprocess
- Experiment tracking (MLflow, W&B)
- Kafka / event streaming
