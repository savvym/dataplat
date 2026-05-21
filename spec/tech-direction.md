# Technical Direction — LLM Training Data Management Platform

> Source of truth: `docs/data_platform_design.md`. This document is intentionally free of function signatures, file names, and class layouts.

---

## Stack Choice (§1.4 — verbatim table)

| 决策 | 选择 | 替代方案 | 理由 |
|---|---|---|---|
| 数据分层抽象 | Dolma 风格(source/document/attribute/dataset) | Medallion(bronze/silver/gold) | Medallion 是 OLAP 抽象,不贴合 LLM 数据工程 |
| 编排骨架 | Dagster | Prefect / Airflow / 自研 | Asset 模型天然适配资产与血缘 |
| 中间格式 | DoclingDocument JSON | 纯 markdown / 纯 JSONL | 无损保留结构,可派生多种 view |
| 行级数据存储 | Lance | Parquet on object storage | 支持加列(attribute 累积)+ 向量索引 + 时间旅行 |
| 文档级解析 | MinerU(PDF 中文) + Docling(其他格式) | olmOCR / Marker | 中文支持好 + 多格式覆盖 |
| 算子库 | data-juicer(主) + Datatrove(补) | 全自研 | 200+ 现成 OP 减少 60%+ 工作量 |
| VLM 推理 | 第三方 API | 自部署 vllm | 小团队不维护 GPU 集群 |
| Pipeline 定义 | 代码定义的 Dagster Job | UI 拖拽 | 复杂度低,可 review,可版本控制 |
| Filter 语言 | SQL(Lance DataFusion) | jq / 自研 DSL | 标准、可优化、AI 友好 |
| 物化逻辑 | 注册的 schema_template | 用户写代码 | 复杂度封装,用户填 config 即可 |

### Additional stack decisions recorded in §10.2 and §3.2

**Backend:**
- FastAPI + async SQLAlchemy (Postgres 16) + asyncpg
- Redis 7 for caching and WebSocket pub/sub
- MinIO (S3-compatible) for file storage
- Lance for the global chunks table
- Dagster for orchestration (webserver + daemon + workers)

**Frontend:**
- React 18 + TypeScript + Vite
- shadcn/ui + Tailwind for components
- TanStack Query for server state
- TanStack Table for virtual-scroll tables (Chunks Explorer)
- Zustand for client state
- react-flow for lineage / pipeline visualization
- react-hook-form + zod for auto-generated forms
- monaco-editor for YAML/SQL editing
- recharts for histograms and distributions

---

## Monorepo Layout (§11.1, §3.2)

The repository is a monorepo. High-level structure (no file-level prescriptions):

```
dataplat/
  apps/
    api/           — FastAPI application (business API + WebSocket)
    web/           — React + Vite frontend
  dagster/         — Dagster code location (assets, IOManagers, jobs)
  plugins/         — Operator implementations (extractors, taggers, augmenters, materializers)
  packages/
    api-types/     — Auto-generated TypeScript types from OpenAPI spec (codegen output, committed)
  docker/          — docker-compose files for dev and prod
  docs/            — Design documents (read-only)
  spec/            — Planner artifacts (this directory)
  contracts/       — Sprint contracts
  verify/          — Smoke tests and layer checks
```

Service processes (§3.2):
1. `frontend` — nginx serving React static assets
2. `fastapi` — business API + WebSocket (uvicorn)
3. `dagster-webserver` — Dagster GraphQL API + UI
4. `dagster-daemon` — scheduler / sensor
5. `dagster-worker-cpu` — lightweight operators, scalable
6. `dagster-worker-heavy` — MinerU, LLM API calls, scalable
7. `postgres` — business schema + Dagster schema (separate schemas in same DB)
8. `redis` — cache + pub/sub
9. `minio` — S3-compatible object storage

---

## Six Hard Invariants (CLAUDE.md §Hard invariants, derived from §1.2 + §11.7)

These are non-negotiable architectural constraints. Any implementation that violates one FAILS review immediately.

**1. Lineage is mandatory.**
Every Dagster asset materialization must record the full upstream chain. Every chunk's `augmented_from`, `augmenter_id`, and `augmenter_config_hash` must be populated when applicable. No "we'll backfill later". Datasets carry a frozen `recipe_snapshot` at materialization time.

**2. Storage separation + CAS.**
Metadata (all relational records, references, status) lives in Postgres. Blob content lives in MinIO/S3 addressed by `sha256(content)`. Blob bytes must never be stored in Postgres columns.

**3. Schema frozen post-publish.**
Once a Silver/Gold equivalent (a materialized Dataset) is committed, its schema must not be edited in place. Schema changes require a new recipe version and a new materialization. The `dataset` table enforces this via `UNIQUE (recipe_id, version_tag)`.

**4. LLM calls go through the gateway.**
No code in processors, adapters, API routes, or workers may call Anthropic/OpenAI/etc. SDKs directly. All LLM calls go through the `LLMGateway` abstraction provided via `OperatorContext`. This centralizes rate-limiting, key management, and cost tracking.

**5. Async SQLAlchemy from day one.**
Every database session throughout `apps/api/` is async (asyncpg driver). The synchronous `session.query()` API is banned. This is a structural constraint that cannot be retrofitted cheaply.

**6. OpenAPI to TypeScript type sync.**
Any change to an API schema must be followed by `make codegen`, and the resulting `packages/api-types/` diff must be committed in the same git commit. CI rejects mismatches between the OpenAPI spec and the generated types.

---

## Phasing Plan (§12.2)

### Phase 0 — Infrastructure (target: weeks 1–2)
Stand up all services in docker-compose. Validate FastAPI can reach Dagster GraphQL. Validate Dagster can run a hello-world job. Frontend skeleton renders a login page that reaches the API. Postgres migrations baseline established.

### Phase 1 (MVP) — Source + Extract (weeks 3–4)
Source upload endpoint writes to MinIO and Postgres. Dagster external asset registered via GraphQL mutation. MinerU extractor operator implemented. `DoclingDocIOManager` writes document JSON and images to MinIO. `document_variant` records created. Documents preview page renders.

### Phase 1 (MVP) — Chunks + Taggers (weeks 5–6)
Fixed-size chunking asset produces rows in the global Lance table. `LanceChunksIOManager` implemented (row mode for chunker/augmenter, column mode for tagger). Three tagger assets run: quality_gpt4, lang_fasttext, minhash_dedup. Chunks Explorer page reads Lance directly and shows filter results.

### Phase 1 (MVP) — Recipe + Dataset (weeks 7–8)
Recipe CRUD API and editor UI. `sft_synthesis_qa` materializer implemented. Dataset materialization triggers Dagster backfill, LLM calls go through gateway, Parquet output written to MinIO. `HFDatasetIOManager` implemented. Dataset download endpoint. WebSocket pushes progress.

### Phase 1 (MVP) — Stabilization (week 9)
End-to-end smoke test: PDF upload to dataset download. Error handling. Deployment documentation. Auth (basic auth or simple JWT; no self-registration).

### Phase 2 — Post-MVP (no date committed)
Additional extractors (Docling, Firecrawl, md passthrough). VLM enrichment. Augmenter operators. Lineage UI. Recipe raw YAML mode. Complex sampling strategies. Operator marketplace. HuggingFace Hub export. Dataset diff. Argilla integration. Multi-user conflict resolution beyond last-write-wins.
