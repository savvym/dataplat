# 数据资产管理平台 — 系统设计文档

> **版本**:v1.0(初始设计)
> **状态**:架构已收敛,待 MVP 验证
> **目标读者**:接手开发的工程师、AI agent
> **前置阅读**:Dolma 论文(arXiv: 2402.00159)、Dagster asset 文档、Docling README

---

## 0. 文档使用说明

本文档分三部分:

- **第 1–3 章**:背景、目标、整体架构。**必读**
- **第 4–8 章**:核心子系统的设计与接口。开发对应模块前必读
- **第 9–12 章**:落地相关(API、UI、部署、实施路径)。负责对应模块时读

代码片段是**接口示意**,不是可运行的完整实现。

---

## 1. 背景与目标

### 1.1 问题域

为 LLM 训练/评测准备数据集这件事,目前业界做法是用 CLI 工具(Dolma、Datatrove、NeMo Curator)+ 自己写脚本拼接,**没有带产品化 UI 的端到端平台**。这套 CLI 流程对大厂数据团队够用,但对小团队、单人开发者或非工程背景的研究者门槛过高。

本平台填补这个空白:**让用户上传文件/URL,经过可视化的处理流程,产出可直接用于训练的标准数据集**。

### 1.2 核心需求

| # | 需求 | 说明 |
|---|---|---|
| 1 | 多格式输入 | PDF、docx、pptx、md、URL 五种主要来源 |
| 2 | 统一中间表征 | 异构来源转为统一格式,下游算法只面对一种数据结构 |
| 3 | 可视化处理 | 不写代码完成从原文件到数据集的整个流程 |
| 4 | 增量与可重跑 | 任何环节可单独重跑,影响范围明确 |
| 5 | 完整血缘 | 任意 chunk/数据集行可回溯到原文件的某页某段 |
| 6 | 多种产出 | 同一份原始数据产出 CPT、SFT、Benchmark 多种数据集 |
| 7 | 数据增强 | 翻译、改写、合成等增强操作作为一等公民 |
| 8 | 可复现 | 任何 dataset 都带完整 recipe,可基于同一原料重新物化 |

### 1.3 范围限定

**在范围内**:

- 文档解析、内容抽取、chunking、属性标注、数据增强、数据集物化
- 文本为主,图像作为附属资产保留(支持多模态 SFT)
- 单人 / 小团队规模,百万级 chunks
- 单租户(无权限隔离需求)

**不在范围内**:

- 千万+ chunks 的分布式处理(架构兼容但非首要目标)
- 多租户、企业级权限管理、计费
- 模型训练本身(本平台只产出数据集,训练用 HF transformers/llama-factory 等)
- 实时数据流(本平台是批处理为主)
- 通用 RAG 应用(产出的数据集可被 RAG 用,但 RAG 不是核心目标)

### 1.4 关键设计决策

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

---

## 2. 核心抽象

### 2.1 五个核心概念

```
        ┌──────────┐
        │  Source  │ ◀── 用户上传的原始文件/URL,HF-style repo
        └────┬─────┘
             ▼
        ┌──────────┐
        │ Document │ ◀── 抽取后的统一表征(DoclingDocument JSON)
        └────┬─────┘
             ▼
        ┌──────────┐
        │  Chunk   │ ◀── 切分后的最小操作单位,全局 Lance 表
        └────┬─────┘
             ▼ (一对多)
   ┌─────────┼─────────┐
   ▼         ▼         ▼
┌──────┐ ┌──────┐ ┌──────┐
│ Attr │ │ Attr │ │ Attr │ ◀── 标注属性,作为 Lance 列存在
│ qual │ │ lang │ │minhash│
└──────┘ └──────┘ └──────┘
             │
             ▼
        ┌──────────┐
        │  Recipe  │ ◀── 声明式 dataset 定义(filter + view + schema)
        └────┬─────┘
             ▼
        ┌──────────┐
        │ Dataset  │ ◀── 物化产物,HF-style repo
        └──────────┘
```

### 2.2 概念间关系

- **一个 Source** 上传后,可被**多个 Extractor**(MinerU、Docling 等)各自处理出一份 **Document variant**。其中一个被标记为 canonical。
- **一份 Document** 可被多种 chunking 策略切分,产出多组 **Chunk**。同一份 chunks 进入同一张全局 Lance 表,通过 `producer_asset` 列区分。
- **一份 Chunk** 通过 **Tagger** 算子获得 **Attribute**(物理上是 Lance 表的列),通过 **Augmenter** 算子产出**派生 Chunk**(物理上是 Lance 表的新行,带 `augmented_from` 引用)。
- **一份 Recipe** 引用一个或多个 Source 集合,声明 filter / view / schema,物化成一个 **Dataset**。Dataset 自带 recipe snapshot,可重新物化。

### 2.3 数据可见性

- **Source** 和 **Dataset** 是 HF-style repo,**用户直接面对**
- **Document** 和 **Chunk** 是系统中间产物,**UI 可查可探索,但不强调"管理"**
- **Attribute** 是 Chunk 的附属属性,通过 Chunks Explorer 看到
- **Recipe** 是用户编辑的"配方文件",对应 UI 的 Recipe Editor

---

## 3. 架构总览

### 3.1 四层架构

```
┌─────────────────────────────────────────────────────┐
│ 用户面                                                │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │  Frontend   │  │  FastAPI     │  │ Dagster UI │  │
│  │  React+Vite │  │  REST + WS   │  │  (运维侧)  │  │
│  └─────────────┘  └──────────────┘  └────────────┘  │
├─────────────────────────────────────────────────────┤
│ 控制面                                                │
│  ┌──────────────────┐  ┌──────────────────────┐    │
│  │ Dagster webserver│  │  Dagster daemon      │    │
│  │ (GraphQL API)    │  │  (scheduler/sensor)  │    │
│  └──────────────────┘  └──────────────────────┘    │
├─────────────────────────────────────────────────────┤
│ 执行面                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────┐  │
│  │  CPU Workers │  │ Heavy Workers│  │   外部   │  │
│  │  ×N(轻算子) │  │  ×M(MinerU、 │  │ LLM/VLM  │  │
│  │              │  │  大模型调用) │  │   API    │  │
│  └──────────────┘  └──────────────┘  └──────────┘  │
├─────────────────────────────────────────────────────┤
│ 数据面                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ Postgres │ │  Redis   │ │  MinIO   │ │ Lance  │ │
│  │业务+Dagster│ │ 缓存+WS  │ │  文件    │ │ chunks │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
└─────────────────────────────────────────────────────┘
```

### 3.2 进程清单(docker-compose 视角)

| # | 服务 | 说明 |
|---|---|---|
| 1 | `frontend` | nginx 服务 React 静态资源 |
| 2 | `fastapi` | 业务 API + WebSocket,uvicorn |
| 3 | `dagster-webserver` | Dagster GraphQL API + UI |
| 4 | `dagster-daemon` | 后台调度/sensor |
| 5 | `dagster-worker-cpu` | 跑轻量算子,可 scale |
| 6 | `dagster-worker-heavy` | 跑 MinerU、大模型 API 调用,可 scale |
| 7 | `postgres` | 业务 + Dagster 双 schema |
| 8 | `redis` | FastAPI 缓存 + WebSocket pub/sub |
| 9 | `minio` | S3 兼容,存原文件、Document、Lance 表、Dataset |

无 GPU 服务(VLM 走外部 API)。

### 3.3 三个关键交互模式

**(1) 用户上传文件 → 触发处理**

```
Frontend → FastAPI: POST /api/sources/upload
FastAPI → MinIO: 写原文件
FastAPI → Postgres: 写 source 元数据
FastAPI → Dagster GraphQL: addDynamicPartitions + reportRuntimeAssetMaterialization
FastAPI → Frontend: 返回 source_id

(用户点"处理")
Frontend → FastAPI: POST /api/runs (asset=extract_mineru, partition=...)
FastAPI → Dagster GraphQL: launchPartitionBackfill
Dagster daemon → Worker: 调度任务
Worker → MinIO: 读原文件
Worker → External API: 必要时调 OCR/VLM
Worker → MinIO: 写 DoclingDocument JSON + images
Worker → Postgres: 更新 document_variant
Dagster → FastAPI (event webhook)
FastAPI → Redis pub/sub → WebSocket → Frontend: 进度推送
```

**(2) 用户在 Chunks Explorer 上探索**

```
Frontend → FastAPI: POST /api/chunks/query (filter, pagination)
FastAPI → Lance: scanner with filter
FastAPI → Frontend: 返回 chunks 列表 + 计数 + 分布
```

**完全不经过 Dagster**。这是数据面/控制面分离的好处。

**(3) 用户物化数据集**

```
Frontend → FastAPI: POST /api/datasets/materialize (recipe_id)
FastAPI → Postgres: 创建 dataset 记录(status=pending)
FastAPI → Dagster GraphQL: launchPartitionBackfill(dataset asset, new partition)
Worker → Lance: 按 filter 读 chunks
Worker → External API: 必要时调 LLM 合成
Worker → MinIO: 写 parquet + README + dataset_infos.json
Worker → Postgres: 更新 dataset(status=done, stats)
WebSocket 推送完成事件
```

---

## 4. 数据模型

### 4.1 Postgres Schema(业务表)

```sql
-- ===== 用户(最小化) =====
CREATE TABLE users (
  id          BIGSERIAL PRIMARY KEY,
  email       TEXT UNIQUE NOT NULL,
  name        TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ===== Source 层 =====
CREATE TABLE source_collection (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT UNIQUE NOT NULL,
  owner_id        BIGINT REFERENCES users(id),
  dataset_card_md TEXT,           -- HF-style 描述
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE source (
  id                    BIGSERIAL PRIMARY KEY,
  collection_id         BIGINT REFERENCES source_collection(id) ON DELETE CASCADE,
  kind                  TEXT NOT NULL,  -- 'file' | 'url'
  original_name         TEXT NOT NULL,
  storage_uri           TEXT NOT NULL,  -- s3://sources/{id}
  sha256                TEXT NOT NULL,
  size                  BIGINT,
  mime_type             TEXT,
  license               TEXT,
  source_metadata       JSONB DEFAULT '{}',
  dagster_partition_key TEXT NOT NULL UNIQUE,  -- 跟 Dagster 对接的 key
  preferred_extractor   TEXT,           -- 用户指定默认用哪个 extractor
  uploaded_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_source_collection ON source(collection_id);
CREATE INDEX idx_source_sha256 ON source(sha256);

-- ===== Document variant(一个 source 可有多个 extractor 产物)=====
CREATE TABLE document_variant (
  id                  BIGSERIAL PRIMARY KEY,
  source_id           BIGINT REFERENCES source(id) ON DELETE CASCADE,
  extractor_name      TEXT NOT NULL,
  extractor_version   TEXT NOT NULL,
  config_hash         TEXT NOT NULL,
  storage_prefix      TEXT NOT NULL,    -- s3://documents/{source_id}/{extractor}/
  page_count          INT,
  image_count         INT,
  is_canonical        BOOLEAN DEFAULT FALSE,
  materialized_at     TIMESTAMPTZ DEFAULT NOW(),
  dagster_run_id      TEXT,
  UNIQUE (source_id, extractor_name, config_hash)
);

CREATE INDEX idx_doc_variant_source ON document_variant(source_id);
CREATE UNIQUE INDEX idx_doc_canonical ON document_variant(source_id) WHERE is_canonical;

-- ===== 算子注册表(extractor / tagger / augmenter / materializer 共用)=====
CREATE TABLE operator (
  id                  BIGSERIAL PRIMARY KEY,
  name                TEXT NOT NULL,
  version             TEXT NOT NULL,
  category            TEXT NOT NULL,  -- 'extractor'|'tagger'|'augmenter'|'materializer'
  input_kind          TEXT NOT NULL,  -- 'source'|'document'|'chunk'|'chunks'
  output_kind         TEXT NOT NULL,  -- 'document'|'chunk'|'attribute'|'dataset'
  output_schema       JSONB,          -- Arrow schema for output
  config_schema       JSONB,          -- JSON Schema for config (UI 渲染表单用)
  default_config      JSONB DEFAULT '{}',
  
  -- 元信息
  description         TEXT,
  reference_url       TEXT,
  example_input       JSONB,
  example_output      JSONB,
  
  -- 实现
  image               TEXT NOT NULL,  -- worker 拉的镜像
  entrypoint          TEXT,
  
  -- 成本/限速
  estimated_cost_per_unit JSONB,      -- {"per_chunk": 0.0001, "currency": "USD"}
  rate_limit_per_minute   INT,
  
  -- 状态
  is_active           BOOLEAN DEFAULT TRUE,
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  
  UNIQUE (name, version)
);

CREATE INDEX idx_operator_category ON operator(category, is_active);

-- ===== Recipe =====
CREATE TABLE recipe (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT UNIQUE NOT NULL,
  description     TEXT,
  owner_id        BIGINT REFERENCES users(id),
  definition      JSONB NOT NULL,         -- recipe DSL 的 JSON 形式
  schema_template_operator_id BIGINT REFERENCES operator(id),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ===== Dataset(物化产物)=====
CREATE TABLE dataset (
  id                BIGSERIAL PRIMARY KEY,
  recipe_id         BIGINT REFERENCES recipe(id),
  recipe_snapshot   JSONB NOT NULL,        -- 物化时冻结的 recipe(可复现关键)
  version_tag       TEXT NOT NULL,
  hf_repo_uri       TEXT NOT NULL,
  dataset_card_md   TEXT,
  sample_count      BIGINT,
  size_bytes        BIGINT,
  stats             JSONB,                 -- split sizes, attribute distributions 等
  status            TEXT NOT NULL,         -- 'pending'|'running'|'done'|'failed'
  materialized_by   BIGINT REFERENCES users(id),
  materialized_at   TIMESTAMPTZ,
  dagster_run_id    TEXT,
  UNIQUE (recipe_id, version_tag)
);

CREATE INDEX idx_dataset_recipe ON dataset(recipe_id);

-- ===== Run(业务运行,跟 Dagster run 1:1)=====
CREATE TABLE run (
  id                   BIGSERIAL PRIMARY KEY,
  dagster_run_id       TEXT UNIQUE NOT NULL,
  kind                 TEXT NOT NULL,     -- 'extract'|'enrich'|'chunk'|'tag'|'augment'|'materialize'
  asset_keys           TEXT[] NOT NULL,
  partition_keys       TEXT[] DEFAULT '{}',
  source_collection_id BIGINT REFERENCES source_collection(id),
  dataset_id           BIGINT REFERENCES dataset(id),
  recipe_id            BIGINT REFERENCES recipe(id),
  config               JSONB,
  status               TEXT NOT NULL,
  started_at           TIMESTAMPTZ,
  ended_at             TIMESTAMPTZ,
  triggered_by         BIGINT REFERENCES users(id),
  trigger_context      JSONB              -- UI 上下文,如"用户点了哪个按钮"
);

CREATE INDEX idx_run_status ON run(status, started_at DESC);
CREATE INDEX idx_run_triggered ON run(triggered_by, started_at DESC);
```

### 4.2 Lance Schema(chunks 全局表)

```python
# chunks 表的 Arrow schema(伪代码)
chunks_schema = pa.schema([
    # === 标识 ===
    ("chunk_id",           pa.string()),       # uuid 或 source+offset 派生
    ("source_id",          pa.int64()),        # 反规范化方便过滤
    ("source_collection_id", pa.int64()),
    ("producer_asset",     pa.string()),       # "chunks" | "augment_translate_en" 等
    ("producer_version",   pa.string()),
    
    # === 内容 ===
    ("text",               pa.large_string()), # chunk 的可用文本(线性化后)
    ("token_count",        pa.int32()),
    ("docling_refs",       pa.string()),       # 在 DoclingDocument 中的 NodeItem 路径
    ("source_refs",        pa.string()),       # JSON: {page, bbox, char_range}
    
    # === 派生关系 ===
    ("augmented_from",     pa.string()),       # 父 chunk_id(NULL = 原始)
    ("augmenter_id",       pa.string()),       # 增强算子 id
    ("augmenter_config_hash", pa.string()),
    
    # === Attribute 列(动态加,以下是初始集)===
    ("attr_quality_score",      pa.float32()),
    ("attr_quality_provider",   pa.string()),  # 'gpt-4o-mini' | 'qwen-judge' 等
    ("attr_lang_code",          pa.string()),
    ("attr_lang_confidence",    pa.float32()),
    ("attr_minhash_signature",  pa.list_(pa.uint64())),
    ("attr_minhash_cluster_id", pa.int64()),
    ("attr_minhash_is_head",    pa.bool_()),
    ("attr_pii_has_pii",        pa.bool_()),
    ("attr_pii_categories",     pa.list_(pa.string())),
    ("attr_embed_vector",       pa.list_(pa.float32(), 1024)),  # Lance 原生向量索引
    
    # === 时间戳 ===
    ("created_at",         pa.timestamp("ms")),
    ("updated_at",         pa.timestamp("ms")),
])
```

**重要约定**:

- **新增 attribute = 加新列**(Lance 支持 schema evolution)。Tagger 的 output_schema 决定加哪些列
- **新增 chunk 行**时,所有 `attr_*` 列必须为 NULL,由下游 tagger 重新计算
- **`chunk_id` 命名规范**:原始 chunk 用 `{source_id}_{seq}`,augmented 用 `{parent}_{augmenter_short}`
- **不存原始 markdown**(那个在 Document 里),只存 chunk 的线性化文本

### 4.3 MinIO 存储布局

```
s3://sources/                                  # 原始文件
  {source_id}/
    original.{ext}                              # 原文件
    metadata.json                               # 上传时的元信息

s3://documents/                                # Document 产物
  {source_id}/
    extract_mineru/                             # 一个 extractor 一个目录
      doc.docling.json                          # DoclingDocument JSON
      images/                                   # 引用的图片资产
        0.png
        1.jpg
      manifest.json                             # source_refs + 版本信息
    extract_docling/
      doc.docling.json
      ...
    _canonical -> extract_mineru/               # 软链/指针:当前 canonical

s3://documents_vlm/                            # VLM 增强后的 Document
  {source_id}/
    doc.docling.json                            # 图片描述替换后
    manifest.json

s3://lance/chunks/                             # 全局 chunks 表
  (Lance 自管理目录结构,含 _versions/、data/ 等)

s3://datasets/                                 # 物化产物
  {dataset_id}_v{version}/
    README.md                                   # dataset card
    dataset_infos.json                          # HF datasets 元信息
    recipe.json                                 # 冻结的 recipe snapshot
    data/
      train-00000.parquet
      train-00001.parquet
      validation-00000.parquet
```

---

## 5. Dagster Asset 拓扑

### 5.1 完整 asset graph

```mermaid
graph TD
    source["source<br/>(external asset)"] --> extract_mineru["extract_mineru<br/>PDF"]
    source --> extract_docling["extract_docling<br/>docx/pptx/html"]
    source --> extract_firecrawl["extract_firecrawl<br/>URL"]
    source --> extract_md["extract_md_passthrough"]
    
    extract_mineru --> document["document<br/>(canonical variant)"]
    extract_docling --> document
    extract_firecrawl --> document
    extract_md --> document
    
    document --> doc_vlm["document_vlm_enriched<br/>(可选)"]
    
    doc_vlm --> chunks["chunks"]
    document -.-> chunks
    
    chunks --> attr_quality["attr_quality"]
    chunks --> attr_lang["attr_lang"]
    chunks --> attr_minhash["attr_minhash"]
    chunks --> attr_pii["attr_pii"]
    chunks --> attr_embed["attr_embed"]
    
    chunks --> aug_translate["augment_translate_en"]
    chunks --> aug_paraphrase["augment_paraphrase"]
    
    aug_translate -.-> chunks
    aug_paraphrase -.-> chunks
    
    chunks --> dataset["dataset"]
    attr_quality --> dataset
    attr_lang --> dataset
    attr_minhash --> dataset
    attr_pii --> dataset
```

**说明**:
- 实线:必经路径
- 虚线:写回路径(augmenter 写新行进 chunks 表,触发下游 tagger 重新计算)

### 5.2 Asset 清单

| Asset | 类别 | 上游 | Partition Def | 物理输出 |
|---|---|---|---|---|
| `source` | external | (none) | DynamicPartitions("sources") | MinIO `sources/` |
| `extract_mineru` | extractor | source | 继承 source | MinIO `documents/.../extract_mineru/` |
| `extract_docling` | extractor | source | 继承 source | MinIO `documents/.../extract_docling/` |
| `extract_firecrawl` | extractor | source | 继承 source(仅 URL 类型) | MinIO `documents/.../extract_firecrawl/` |
| `extract_md_passthrough` | extractor | source | 继承 source(仅 md) | MinIO `documents/.../extract_md/` |
| `document` | selector | 上述所有 extractor | 继承 source | Postgres 指针 + 软链 |
| `document_vlm_enriched` | enricher | document | 继承 source | MinIO `documents_vlm/` |
| `chunks` | chunker | document 或 document_vlm_enriched | 多维:(source, chunking_strategy) | Lance 新行 |
| `attr_*`(N 个) | tagger | chunks | 继承 chunks | Lance 加列 |
| `augment_*`(N 个) | augmenter | chunks | 继承 chunks | Lance 新行 |
| `dataset` | materializer | chunks + 选定 attrs | DynamicPartitions("dataset_versions") | MinIO `datasets/` |

### 5.3 Partition 策略

**Source 类**:`DynamicPartitionsDefinition(name="sources")`,partition_key = `src_<sha256[:12]>`。FastAPI 上传文件时通过 GraphQL 加 partition。

**Document / chunks / attrs / augmenters**:继承 source partition。同一 source 的整条加工链路是一组 partition,backfill 单个文件 = 触发一组 asset 的同名 partition。

**Chunks 多维 partition**:`(source_partition, chunking_strategy_name)`。同一 source 用不同策略切的 chunks 是不同 partition,互不影响。

**Dataset**:`DynamicPartitionsDefinition(name="dataset_versions")`,partition_key = `ds_<recipe_id>_v<n>`。每次物化产生新 partition,老的不动。

### 5.4 External asset 模式

`source` 是 external asset —— Dagster 不 materialize 它,只承认它存在。FastAPI 上传文件后:

```python
# FastAPI 侧
async def upload_source(file, collection_id, user_id):
    # 1. 业务侧入库
    source_id = await write_to_minio_and_postgres(file, collection_id)
    partition_key = f"src_{source_id}"
    
    # 2. 通知 Dagster 这个 partition 存在了
    await dagster_client.mutate("""
        mutation($keys: [String!]!) {
            addDynamicPartitions(
                partitionsDefName: "sources", 
                partitionKeys: $keys
            )
        }
    """, keys=[partition_key])
    
    # 3. 上报 materialization(让血缘图有这个节点)
    await dagster_client.mutate("""
        mutation($key: String!, $partition: String!, $meta: JSONString!) {
            reportRuntimeAssetMaterialization(
                assetKey: $key, partitionKey: $partition, metadata: $meta
            )
        }
    """, key="source", partition=partition_key, meta=json.dumps({
        "uri": f"s3://sources/{source_id}",
        "size": file.size,
    }))
```

---

## 6. 算子系统

### 6.1 四个算子类别

| 类别 | 输入 → 输出 | 物理作用 | 例子 |
|---|---|---|---|
| **Extractor** | source → DoclingDocument | 写新 document variant | MinerU、Docling、Firecrawl |
| **Tagger** | chunk → attribute | chunks 表加列 | quality_score、lang_detect、minhash |
| **Augmenter** | chunk → N chunks | chunks 表加行 | translate、paraphrase、persona_dialog |
| **Materializer** | chunks + filter → dataset | 写 HF parquet | cpt_plain、sft_synthesis_qa、benchmark_mcq |

### 6.2 统一接口

所有算子实现统一接口:

```python
class Operator(Protocol):
    name: str
    version: str
    category: Literal["extractor", "tagger", "augmenter", "materializer"]
    config_schema: dict       # JSON Schema, 用于 UI 自动生成表单
    output_schema: dict       # 输出的数据 schema
    
    def health_check(self) -> bool:
        """worker 启动时调,验证依赖可用"""
    
    def process(
        self, 
        input_data: Any,      # 类别决定类型
        config: dict,
        context: OperatorContext
    ) -> Any:
        """实际处理逻辑"""
```

`OperatorContext` 提供:
- 输入/输出 storage URI
- LLM/VLM client(已配好 API key + rate limit)
- 当前 Dagster run_id / partition_key(便于打 metric)
- Postgres 只读连接

### 6.3 注册流程

新增一个算子 = 三步:

1. **在 Postgres `operator` 表插一行**:name、version、category、config_schema、image 等
2. **构建 worker 镜像**(Python 实现 + 依赖打包)
3. **在 Dagster 代码里通过 asset_factory 生成对应的 asset definition**:

```python
# dagster_assets/factory.py
def make_tagger_asset(operator_row: OperatorRow) -> AssetsDefinition:
    @asset(
        name=f"attr_{operator_row.name}",
        deps=[chunks],
        partitions_def=source_partitions,
        io_manager_key="lance_chunks_io",
        op_tags={"category": "tagger", "operator_id": operator_row.id},
    )
    def _asset(context, chunks):
        operator = load_operator_from_image(operator_row)
        config = context.op_config or operator_row.default_config
        return operator.process(chunks, config, context)
    
    return _asset

# 启动时加载所有 active operators 并生成 assets
all_assets = []
for op in pg.query("SELECT * FROM operator WHERE is_active"):
    if op.category == "tagger":
        all_assets.append(make_tagger_asset(op))
    elif op.category == "augmenter":
        all_assets.append(make_augmenter_asset(op))
    # ...
```

这样新增算子**不需要改 Dagster 代码**,只需要 Postgres + image 部署。Dagster code location 通过 `reload_code_location` API 重新加载 asset 定义即可看到。

### 6.4 算子实现来源

- **Extractor**:直接包装 MinerU、Docling、Firecrawl 的 Python API,适配输出到 DoclingDocument
- **Tagger**:包装 **data-juicer** 的 Mapper/Filter(quality classifier、lang detect、Gopher rules)和 **Datatrove** 的 minhash dedup
- **Augmenter**:包装 data-juicer 的 nlpaug、chinese_convert、generate_qa 等 Mapper
- **Materializer**:自己实现(逻辑高度业务相关,封装在 schema_template 里)

初始注册算子目标:**20-30 个**,覆盖最常见场景。可以在系统跑起来后通过"算子市场"持续添加。

---

## 7. Recipe DSL

### 7.1 完整 schema

```yaml
# Recipe v1 schema

name: <string, required, unique>
description: <string>
version_tag: <string, required>   # 用户给这个 recipe 起的版本名

# === Stage 1: Source 选择 ===
sources:
  - collection_id: <int, required>
    weight: <float, default 1.0>           # 混合时的权重
    filter:                                # 可选,在 source 元数据上的过滤
      where: <SQL expression>              # 比如 "license = 'CC-BY' AND size < 10485760"

# === Stage 2: Filter(在 chunks 上)===
filter:
  where: <SQL expression>                  # Lance 上跑的 WHERE 子句
  # 例: "attr_quality_score > 0.7 AND attr_lang_code IN ('zh', 'en')"

# === Stage 3: View(怎么渲染 chunk 内容)===
view:
  kind: <enum: linearized_text|md_with_images|docling_json>
  options:
    image_strategy: <enum: vlm_description|placeholder|drop, default placeholder>
    table_format: <enum: markdown|html|json, default markdown>
    keep_section_headers: <bool, default true>

# === Stage 4: Schema Template ===
schema:
  template: <string, required>             # 引用 operator.name(category=materializer)
  template_version: <string, default latest>
  config: <object>                         # 按 template 的 config_schema 填

# === Stage 5: Sampling ===
sampling:
  max_total_samples: <int, optional>
  per_source_caps:                         # 每个 source collection 的上限
    <collection_id>: <int>
  strategy: <enum: stratified|uniform|none, default uniform>
  shuffle_seed: <int, default 42>

# === Stage 6: Output ===
output:
  format: <enum: parquet|jsonl, default parquet>
  hf_compatible: <bool, default true>
  splits:
    train: <float>
    validation: <float>
    test: <float, optional>
  shard_size_mb: <int, default 256>
```

### 7.2 内置 schema_template 清单(MVP)

| Name | 用途 | 关键 config |
|---|---|---|
| `cpt_plain` | 纯文本 CPT | text_field_name |
| `cpt_concat` | 拼接成长文档 | join_separator, max_doc_tokens |
| `sft_synthesis_qa` | LLM 合成 Q+A | synthesizer_model, prompt_template, fallback_on_failure |
| `sft_from_chunks` | 直接以 chunk 作为 assistant 回复(用一个 user prompt 包装) | system_prompt, user_prompt_template |
| `dpo_two_model` | 用两个模型生成 chosen/rejected | model_a, model_b, judge_model |
| `benchmark_mcq` | 合成选择题 | distractor_count, distractor_strategy |
| `benchmark_open` | 合成开放问答 + 评分标准 | grading_rubric_prompt |

### 7.3 Filter 语言

直接使用 SQL,基于 Lance 的 DataFusion 实现。支持:
- 数值比较、字符串匹配、IN/NOT IN、BETWEEN
- AND/OR/NOT
- JSON 字段访问(`source_refs->>'page'`)
- 数组操作(`array_contains(attr_pii_categories, 'email')`)
- 标量函数(length, lower, upper, etc.)

**不支持**(架构允许但 UI 不暴露):
- JOIN(单表查询足够)
- Subquery
- 窗口函数

UI 上用 visual filter builder 拼 SQL,高级用户可切换 raw SQL 模式。

---

## 8. IOManager 设计

### 8.1 三个 IOManager 职责

| IOManager | 服务的 asset | 后端 | 主要逻辑 |
|---|---|---|---|
| `DoclingDocIOManager` | extract_*、document、document_vlm_enriched | MinIO | 写 JSON + images + manifest |
| `LanceChunksIOManager` | chunks、attr_*、augment_* | Lance | 写新行 / 更新列 |
| `HFDatasetIOManager` | dataset | MinIO | 写 parquet + README + recipe.json |

### 8.2 LanceChunksIOManager 的 row/column 模式

这是三个里最复杂的。同一个 Lance 表被多种 asset 写,但写的语义不同:

```python
class LanceChunksIOManager(IOManager):
    def handle_output(self, context, obj):
        asset_name = context.asset_key.path[-1]
        category = lookup_operator_category(asset_name)  # 从 Postgres 查
        ds = lance.dataset(self.table_uri)
        partition = context.partition_key  # source_id
        
        if category in ("chunker", "augmenter"):
            # Row mode: 删除旧的,插入新的
            ds.delete(
                f"source_id={partition} AND producer_asset='{asset_name}'"
            )
            arrow_table = obj.with_columns({
                "producer_asset": pa.array([asset_name] * len(obj)),
                "producer_version": pa.array([context.metadata["version"]] * len(obj)),
            })
            ds.insert(arrow_table)
            
        elif category == "tagger":
            # Column mode: 只更新这个 tagger 对应的列
            column_name = f"attr_{asset_name.removeprefix('attr_')}"
            ds.merge_insert(
                on="chunk_id",
                when_matched_update_all=[column_name]
            ).execute(obj)
        
        context.add_output_metadata({
            "row_count": len(obj),
            "mode": "row" if category != "tagger" else "column",
        })
```

完整代码见附录 A。

### 8.3 关键约束

- **augmenter 写入的新行,所有 `attr_*` 列必须为 NULL** —— 强制下游 tagger 重新计算,不能继承父 chunk 的属性
- **tagger 写入失败,IOManager 应保留之前的列值** —— 用 Lance 的事务保证
- **IOManager 上报到 Postgres 的写入**(document_variant、dataset 等)和 Lance/MinIO 的写入要尽量原子。先写存储后写 Postgres,失败时孤儿数据有清理 job 兜底

---

## 9. API 层

### 9.1 路由分组

```
/api/auth/             — 登录、token 刷新(单租户先简化)
/api/sources/          — Source collection 和 source 的 CRUD
  POST   /sources/upload
  GET    /sources/{id}
  GET    /sources/{id}/preview                    — 渲染预览
  POST   /collections                              — 新建 collection
  GET    /collections/{id}/sources                 — 列出 sources

/api/documents/        — Document variant 管理
  GET    /sources/{source_id}/documents            — 列出该 source 的所有 document variants
  POST   /sources/{source_id}/documents/{name}/set-canonical
  GET    /documents/{variant_id}/render             — 渲染 markdown 预览

/api/chunks/           — Chunks 查询(直接打 Lance)
  POST   /chunks/query                              — { filter, columns, limit, offset }
  POST   /chunks/aggregate                          — { filter, group_by, metrics }
  POST   /chunks/distribution                       — { filter, column }  返回 histogram
  GET    /chunks/{id}                               — 单 chunk 详情(含 source_refs)
  GET    /chunks/{id}/lineage                       — 完整血缘

/api/operators/        — 算子注册表浏览
  GET    /operators?category=tagger
  GET    /operators/{id}
  POST   /operators                                 — 注册新算子(管理员)

/api/recipes/          — Recipe CRUD
  GET    /recipes
  GET    /recipes/{id}
  POST   /recipes
  PUT    /recipes/{id}
  POST   /recipes/{id}/preview                      — 实时生成 3-5 条样本

/api/datasets/         — Dataset 管理
  GET    /datasets
  GET    /datasets/{id}
  POST   /datasets/{id}/materialize                 — 物化(可指定 recipe version)
  GET    /datasets/{id}/download                    — 流式下载 HF parquet

/api/runs/             — 运行查询(代理 Dagster GraphQL)
  POST   /runs                                      — 触发 backfill
  GET    /runs?status=running
  GET    /runs/{id}
  GET    /runs/{id}/logs                            — 跳 Dagster UI 或代理

/api/ws/               — WebSocket
  /ws/runs                                          — 订阅 run 状态变化
  /ws/notifications                                 — 用户通知
```

### 9.2 FastAPI ↔ Dagster 封装层

设计原则:**永远不让前端直接接触 Dagster GraphQL,所有 Dagster 调用走 FastAPI 包装**。

```python
class DagsterGateway:
    def __init__(self, graphql_endpoint, http_client):
        self.client = http_client
        self.endpoint = graphql_endpoint
    
    async def launch_extraction(
        self, 
        source_ids: list[int], 
        extractor: str,    # "mineru" | "docling" | ...
        config: dict,
        triggered_by: int
    ) -> str:
        """返回 dagster_run_id"""
        partitions = [f"src_{sid}" for sid in source_ids]
        asset_key = f"extract_{extractor}"
        
        result = await self.client.execute(
            LAUNCH_PARTITION_BACKFILL_MUTATION,
            variables={
                "selector": {"assetKey": [asset_key]},
                "partitionKeys": partitions,
                "config": {"ops": {asset_key: {"config": config}}},
                "tags": [
                    {"key": "triggered_by", "value": str(triggered_by)},
                    {"key": "business_kind", "value": "extract"},
                ]
            }
        )
        run_id = result["launchPartitionBackfill"]["backfillId"]
        
        # 记录到业务 run 表
        await pg.insert_run({
            "dagster_run_id": run_id,
            "kind": "extract",
            "asset_keys": [asset_key],
            "partition_keys": partitions,
            "config": config,
            "triggered_by": triggered_by,
            "status": "pending",
        })
        return run_id
    
    async def launch_materialization(...): ...
    async def launch_taggers(...): ...
    # 类似的方法,每个业务操作一个
```

业务前端永远只看到 `launch_extraction` 这样的方法,不见 GraphQL 细节。

### 9.3 WebSocket 事件

```python
# WebSocket 推送的事件类型
{
    "type": "run.status_changed",
    "run_id": "...",
    "kind": "extract",
    "from": "running",
    "to": "success",
    "metadata": {...}
}

{
    "type": "asset.materialized",
    "asset_key": "extract_mineru",
    "partition_key": "src_abc",
    "metadata": {
        "uri": "s3://...",
        "preview": "..."
    }
}

{
    "type": "chunks.added",
    "source_id": 123,
    "count": 45,
    "producer_asset": "chunks"
}
```

来源:FastAPI 监听 Dagster 的 webhook(或轮询 GraphQL),转换成业务事件,通过 Redis pub/sub 广播给所有 WebSocket 连接。

---

## 10. 前端

### 10.1 页面清单

| # | 页面 | 主要功能 |
|---|---|---|
| 1 | **Home / Dashboard** | 最近活动、运行中的任务、关键数据集统计 |
| 2 | **Sources** | Source collection 列表 + 详情,上传,管理 |
| 3 | **Documents** | 查看 source 的 document variants,切换 canonical |
| 4 | **Chunks Explorer** | 核心:浏览/过滤/打标/增强 chunks |
| 5 | **Operators (Taggers/Augmenters)** | 算子注册表浏览 |
| 6 | **Recipes** | Recipe 列表 + 编辑器 |
| 7 | **Datasets** | Dataset 列表 + 详情 + 下载 |
| 8 | **Activity / Runs** | 运行历史,跳 Dagster UI 看详情 |
| 9 | **Lineage** | 选中任意资产看血缘图 |
| 10 | **Settings** | 用户、外部 API key、系统配置 |

### 10.2 技术栈

- **React 18 + TypeScript + Vite**
- **shadcn/ui** + **Tailwind**:组件库
- **TanStack Query**:服务端状态
- **TanStack Table**:虚拟滚动表格(Chunks Explorer 用)
- **Zustand**:客户端状态
- **react-flow**:血缘图、pipeline 可视化
- **react-hook-form + zod**:表单(根据 operator config_schema 自动生成)
- **monaco-editor**:YAML/SQL 编辑器
- **recharts**:histogram、bar chart

### 10.3 Chunks Explorer 详细设计

布局:

```
┌────────────────────────────────────────────────────────────┐
│ 顶栏: [collection 多选] [当前 filter pills] [视图切换]      │
├──────────────────┬─────────────────────────────────────────┤
│ 左侧 Filter 侧栏  │ 主区(三种 tab)                          │
│                  │                                          │
│ 属性区(滚动)    │ 表格 / 分布 / 对比                       │
│ ▾ quality       │                                          │
│   滑块 [.7—1.0] │ 虚拟滚动表格,可显示十万行不卡           │
│ ▾ lang           │                                          │
│   ☑ zh ☑ en    │ "23,847 chunks match"                    │
│ ▾ minhash        │                                          │
│ ...              │                                          │
│                  │ [⊕ 应用 Tagger ▾] [⤴ 应用 Augmenter ▾]│
│ 来源             │ [⊟ 保存视图] [⤵ 用作 Recipe Filter]     │
│ ☑ tech_papers   │                                          │
│ ☑ arxiv         │                                          │
│                  │                                          │
│ 派生数据         │                                          │
│ ◉ 只要原始       │                                          │
│ ○ 含 augmented   │                                          │
└──────────────────┴─────────────────────────────────────────┘
```

**关键交互**:

1. 每改 filter,中间区域 1 秒内更新(FastAPI 直接打 Lance count)
2. 切换到"分布"tab,每个属性是一个 histogram,**点击 histogram 某段自动加 filter**
3. 切换到"对比"tab,可同时显示两份 filter 的结果,看差异
4. 点击一行,右侧滑出详情面板,含血缘、源文件链接、衍生 chunk 列表
5. "应用 Tagger / Augmenter":弹窗选算子,渲染 config 表单(从 config_schema 生成),提交 = 触发 Dagster backfill 对**当前 filter 命中的 chunks 子集**

### 10.4 Recipe Editor 详细设计

布局:三栏 —— 左侧步骤导航 / 中间当前步骤编辑 / 右侧实时 preview。

六个步骤每个有专用编辑器:
- **Sources**:多选 source collection + 权重 slider
- **Filter**:visual filter builder(同 Chunks Explorer 风格)
- **View**:三选一卡片 + 子选项表单
- **Schema**:template 卡片选择 + config 表单(根据 config_schema 自动渲染)
- **Sampling**:数字输入 + strategy 选择
- **Output**:格式 + split + 命名

**实时 preview**(右侧常驻):
- 显示 3 条按当前 recipe 生成的样本
- 改参数 → 节流 3 秒后重新生成(避免合成类 template 频繁调 LLM 费钱)
- 显示成本估算

**可切换 visual / YAML 模式**。

### 10.5 自动生成表单的关键模式

由于算子的 config 五花八门,UI 必须能基于 `operator.config_schema`(JSON Schema)自动生成表单。推荐用 **uiform-form** 或 **react-jsonschema-form**。

示例:

```json
// operator.config_schema(存 Postgres)
{
  "type": "object",
  "properties": {
    "synthesizer_model": {
      "type": "string",
      "enum": ["gpt-4o-mini", "qwen-max", "claude-haiku"],
      "default": "gpt-4o-mini"
    },
    "prompt_template": {
      "type": "string",
      "format": "textarea",
      "minLength": 10
    },
    "max_retries": {
      "type": "integer",
      "minimum": 0,
      "maximum": 10,
      "default": 3
    }
  },
  "required": ["synthesizer_model", "prompt_template"]
}
```

前端把这段 schema 喂给 form 库 = 一个可用的表单,无需为每个算子写定制 UI。

---

## 11. 部署架构

### 11.1 起步:docker-compose 单机

```yaml
# docker-compose.yml(关键部分)
version: "3.9"

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: platform
    volumes:
      - pg_data:/var/lib/postgresql/data
  
  redis:
    image: redis:7-alpine
  
  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
    volumes:
      - minio_data:/data
  
  fastapi:
    build: ./backend
    depends_on: [postgres, redis, minio, dagster-webserver]
    environment:
      DATABASE_URL: postgresql://app:${POSTGRES_PASSWORD}@postgres/platform
      REDIS_URL: redis://redis:6379
      MINIO_ENDPOINT: minio:9000
      DAGSTER_GRAPHQL: http://dagster-webserver:3000/graphql
      OPENAI_API_KEY: ${OPENAI_API_KEY}     # for taggers/materializers
  
  frontend:
    build: ./frontend
    depends_on: [fastapi]
  
  dagster-webserver:
    build: ./dagster
    command: dagster-webserver -h 0.0.0.0 -p 3000
    depends_on: [postgres]
    environment:
      DAGSTER_PG_DB: platform_dagster
      DAGSTER_PG_USER: app
      ...
  
  dagster-daemon:
    build: ./dagster
    command: dagster-daemon run
    depends_on: [postgres, dagster-webserver]
  
  dagster-worker-cpu:
    build: ./dagster
    command: dagster api grpc -h 0.0.0.0 -p 4000
    deploy:
      replicas: 2
    environment:
      WORKER_PROFILE: cpu
  
  dagster-worker-heavy:
    build: ./dagster
    command: dagster api grpc -h 0.0.0.0 -p 4001
    deploy:
      replicas: 1
    environment:
      WORKER_PROFILE: heavy

volumes:
  pg_data:
  minio_data:
```

### 11.2 资源需求(MVP)

- **单台 32G + 8 core 服务器**可 hold 住百万级 chunks 的开发/小规模运营
- **存储**:百万 chunks 在 Lance 约 5-10 GB(取决于 chunk 大小);Document 文件平均每个 1-5 MB;原文件按你的数据量算
- **外部 API 成本**:
  - VLM 描述图片:按 0.001 USD/张算
  - LLM 合成 Q+A:按 0.0001 USD/chunk(用 gpt-4o-mini)
  - 处理 10000 个 chunks 的合成 SFT dataset ≈ 1 USD

### 11.3 扩展路径

| 规模 | 关键变化 |
|---|---|
| <100K chunks | docker-compose 单机,Dagster multiprocess executor |
| 100K - 1M | docker-compose 多机,Dagster celery executor,Postgres 主从 |
| 1M - 10M | k8s,Dagster k8s executor,Lance 分片,S3 而非自托管 MinIO |
| >10M | 重新评估架构;可能需要 Ray Data 替代部分 Dagster 任务 |

---

## 12. 实施路径

### 12.1 MVP 范围(目标:8 周)

**只打通一条最小路径**:PDF → DoclingDocument → chunks → quality+lang+minhash → SFT dataset

**包含**:
- Source 上传(只支持 PDF)
- 一个 extractor(MinerU)
- 一种 chunking 策略
- 三个 tagger(quality_gpt4、lang_fasttext、minhash_dedup)
- 一个 schema_template(sft_synthesis_qa)
- Sources / Chunks Explorer / Recipes / Datasets 四个页面

**不包含**(留给 v2):
- Augmenter
- 多种 extractor 切换 / 对比
- VLM 增强
- 血缘 UI
- Recipe 的 raw YAML 模式
- 复杂的 sampling 策略

### 12.2 阶段性目标

**Phase 0:基础设施**(1-2 周)
- docker-compose 跑通
- Dagster 能跑一个 hello world job
- FastAPI ↔ Dagster GraphQL 通了
- 前端框架搭好,能跑通登录 → 首页

**Phase 1:Source + Extract**(2 周)
- Source 上传 + Postgres 入库 + Dagster external asset
- MinerU adapter 完成,extract_mineru asset 跑通
- DoclingDocIOManager 实现
- Documents 预览页面

**Phase 2:Chunks + Taggers**(2 周)
- Chunks asset(简单的 fixed-size chunking)
- LanceChunksIOManager 实现
- 三个 tagger asset 跑通
- Chunks Explorer 页面(只读)

**Phase 3:Recipe + Dataset**(2 周)
- sft_synthesis_qa materializer
- Recipe Editor UI
- Dataset 物化 + 下载
- HFDatasetIOManager 实现

**Phase 4:打磨 + 上线**(1 周)
- 端到端跑通完整流程
- 错误处理、日志
- 部署文档

### 12.3 关键风险

| 风险 | 缓解 |
|---|---|
| MinerU 处理失败率高(扫描件、复杂版面)| 准备 Docling 作为 fallback;允许用户手动重传或换 extractor |
| Lance 在小团队不熟悉,踩坑 | MVP 阶段评估 Lance vs Parquet 实际差异,必要时降级到 Parquet |
| Dagster 学习曲线 | 一个人主攻 Dagster,其他人通过抽象层访问 |
| LLM API 成本失控 | UI 上必须显示成本估算 + 设硬上限(per-recipe budget cap) |
| Recipe DSL 不够表达 | 留 raw YAML 逃生通道;复杂逻辑通过新增 schema_template 解决 |
| 算子注册的"image 部署"流程繁琐 | MVP 阶段先把所有算子打到一个大 image 里,后面再拆 |

### 12.4 待决问题

1. **认证方案**:MVP 是否要做用户登录?如果单租户内部用,可以先 basic auth 顶住
2. **算子市场**:是否做"从 GitHub 拉算子"这种功能?MVP 先不做
3. **数据集分享**:是否要做"导出到 HuggingFace Hub"的一键功能?v2 考虑
4. **数据集 diff**:同一 recipe 的两个版本的 dataset 怎么对比?需要专门 UI 设计
5. **Argilla 集成**:人工 review 流程是直接嵌入 UI 还是跳 Argilla?待用户反馈
6. **多用户冲突**:两个用户同时改同一个 Recipe 怎么办?MVP 先用 last-write-wins + Postgres 行锁

---

## 附录 A:完整 IOManager 代码骨架

(略,见独立代码仓库的 `dagster/io_managers/`)

## 附录 B:关键参考资料

- **Dolma 论文**:Soldaini et al., "Dolma: an Open Corpus of Three Trillion Tokens for Language Model Pretraining Research", arXiv:2402.00159
- **Dolma toolkit**:https://github.com/allenai/dolma
- **olmOCR**:https://github.com/allenai/olmocr
- **Docling**:https://github.com/docling-project/docling
- **MinerU**:https://github.com/opendatalab/MinerU
- **Datatrove**:https://github.com/huggingface/datatrove
- **data-juicer**:https://github.com/modelscope/data-juicer
- **Dagster Assets**:https://docs.dagster.io/concepts/assets/software-defined-assets
- **Lance**:https://lancedb.github.io/lance/

## 附录 C:术语表

| 术语 | 含义 |
|---|---|
| Source | 用户上传的原始文件或 URL |
| Source collection | 一组 source 的 HF-style 容器 |
| Document | source 抽取后的统一表征(DoclingDocument JSON + 引用的图片) |
| Document variant | 同一 source 用不同 extractor 产生的不同 document 版本 |
| Chunk | document 切分后的最小可处理单位,行存于全局 Lance 表 |
| Attribute | chunk 的标注属性,物理上是 Lance 表的列 |
| Augmented chunk | 由 augmenter 算子产生的派生 chunk,带 augmented_from 引用 |
| Recipe | 声明式的 dataset 生成配方,YAML/JSON |
| Dataset | 物化产物,HF-style parquet repo |
| Operator | 系统中所有数据处理单元的统称(extractor/tagger/augmenter/materializer 四类)|
| Asset | Dagster 的资产抽象,对应一个数据产物 |
| Partition | Dagster asset 的一个分区实例,本系统中通常 = 一个 source |
| Materialization | 一次 asset 物化运行,产生一个版本 |
| Schema template | Materializer 的同义词,Recipe 中引用的"如何产出 dataset 行"的逻辑封装 |

---

**文档结束。**

后续修订:在 git 中跟踪,大版本号(v2.0)表示架构性变更,小版本号(v1.1)表示细节补充。每次修订在文档顶部记录变更摘要。
