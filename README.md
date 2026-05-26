# Dataplat

> 面向 LLM 训练 / 评测的数据资产管理平台 —— 把上传的 PDF、URL、文档变成可直接用于训练的数据集，全程可视化、可追溯、可重跑。

业界做 LLM 训练数据的现状是「Dolma / Datatrove / NeMo Curator CLI + 自写脚本」。这套对大厂数据团队够用，但对小团队、独立开发者、非工程背景研究者门槛过高。本平台填补这个空白。

完整系统设计见 [`docs/data_platform_design.md`](docs/data_platform_design.md)（**唯一权威设计文档**，禁止修改）。

---

## 核心抽象

```
   Source           上传的原始文件 / URL（HF-style repo）
     ↓ extract
   Document         统一中间表征（DoclingDocument JSON）
     ↓ chunk
   Chunk            最小操作单位，写入全局 Lance 表
     ↓ tag / augment
   Attribute        作为 Lance 列累加（quality / lang / minhash …）
     ↓ recipe
   Dataset          按 filter + view + schema_template 物化产出
```

- **Source** 一份原料可被多个 extractor 各自产出一份 Document variant，其中一个标 canonical
- **Chunk** 跨所有来源共享一张 Lance 表，按 `producer_asset` 区分
- **Attribute** 物理上就是 Lance 表的列；**Augmenter** 产出新行并带 `augmented_from` 引用
- **Recipe** 声明式 DSL：filter（Lance DataFusion SQL）+ view + schema_template
- **Dataset** 内嵌 recipe snapshot，可基于同一原料重新物化

---

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 编排 | **Dagster** | Asset 模型天然适配资产与血缘 |
| 后端 | **FastAPI**（async SQLAlchemy）| REST + WebSocket，全异步 |
| 业务库 | **Postgres** | 元数据 / Dagster schema 双库共栖 |
| 对象存储 | **MinIO**（S3-compatible）| 原文件 / Document JSON / Lance 数据 |
| 行级数据 | **Lance** | 加列友好 + 向量索引 + 时间旅行 |
| 文档解析 | **MinerU**（PDF/中文）+ **Docling** | 中文支持 + 多格式覆盖 |
| 算子库 | **data-juicer** 主 + **Datatrove** 补 | 200+ 现成 OP |
| LLM/VLM | **第三方 API**（统一走 LLMGateway）| 小团队不维护 GPU 集群 |
| 前端 | **React + Vite**（待启动）| — |
| 任务队列 | **RQ** | MVP 简洁；Celery 留到扩规模 |

---

## 仓库结构

```
apps/api/                  FastAPI 后端（async SQLAlchemy + DagsterGateway）
  dataplat_api/            包：dagster/ · routers/ · schemas/ · db/ · llm/ …
  alembic/                 数据库迁移
  tests/                   pytest
dagster/                   Dagster code location（Definitions / jobs / assets）
docker/                    docker-compose.dev.yml + 各服务 Dockerfile + .env.example
docs/                      data_platform_design.md（设计文档，唯一权威）
spec/                      planner 输出：product-spec / tech-direction / feature_list
contracts/<sprint-id>/     每个 sprint 的契约：proposed / feedback / agreed / review-final
verify/                    checks.sh 分层验证 + reviewer 校准用例
skills/                    可复用流程文档（migration / fastapi-async / llm-gateway …）
plugins/                   adapter / processor 插件（计划中）
packages/api-types/        OpenAPI → TS 类型生成产物（计划中）
```

---

## 快速开始

依赖：`docker compose`（推荐 v2）、`make`、`python 3.12+`（仅本地开发用）。

```bash
# 1. 拉起开发栈（postgres / minio / dagster 全家桶 / fastapi）
docker compose -f docker/docker-compose.dev.yml up -d

# 2. 等到所有容器 healthy（约 30s）
docker compose -f docker/docker-compose.dev.yml ps

# 3. 跑迁移（FastAPI 容器内）
make migrate

# 4. 验证基线
bash verify/checks.sh all
```

默认端口（可在 `docker/.env.example` 改 `*_HOST_PORT`，默认偏移 +10000 避撞）：

| 服务 | 主机端口 | 容器端口 |
|---|---|---|
| FastAPI | 18000 | 8000 |
| Postgres | 15432 | 5432 |
| MinIO API | 19000 | 9000 |
| MinIO Console | 19001 | 9001 |
| Dagster UI | 13000 | 3000 |
| Frontend | 15173 | 5173 |

冒烟示例：

```bash
# 触发 hello-world Dagster 任务并轮询到 success
RUN_ID=$(curl -sS -X POST http://localhost:18000/api/admin/runs/hello-world | jq -r .dagster_run_id)
for i in $(seq 1 60); do
  s=$(curl -sS http://localhost:18000/api/runs/$RUN_ID | jq -r .status)
  echo "iter=$i status=$s"; [ "$s" = success ] && break; sleep 1
done
```

---

## 开发协作约定

本仓使用一种**契约式 sprint 工作流**驱动 LLM 协作开发，所有约束写在 [`CLAUDE.md`](CLAUDE.md)：

- 每个 sprint 对应一个 feature（`spec/feature_list.json`），从 `proposed.md` → 评审 → `agreed.md` → 实现 → Mode B 复审 → verifier 一气呵成
- 任何触及 `apps/api/` 或 `plugins/` 的改动必须经过 `reviewer` 子代理
- `passes:true` 只能由 verifier 验证通过后由 leader 翻转
- 6 条硬不变量（lineage / CAS 分离 / schema 冻结 / LLM Gateway / async SQLAlchemy / OpenAPI↔TS 同步）违反任何一条直接 fail review

---

## 当前进度（Phase 0–1）

21/105 features 通过：

- **F-001** docker-compose 开发栈
- **F-002** Postgres 基线迁移（8 张 §4.1 业务表）
- **F-003** MinIO 桶初始化（sources / documents / documents-vlm / lance / datasets）
- **F-004** DagsterGateway 抽象 + `GET /api/admin/dagster-status`
- **F-005** hello-world Dagster 任务 + `GET /api/runs/{run_id}`
- **F-006** `verify/checks.sh smoke` 真正检查 API health / DB / MinIO / Dagster 四件套（lifespan 内 `SELECT 1` 探针使 `/healthz` 真正依赖 Postgres）
- **F-007** 一次性 `seed-admin` CLI 写入 admin 用户 + `POST /api/auth/token` 颁发 JWT（bcrypt + PyJWT HS256，常量时间防枚举）
- **F-008** 所有非公开路由（admin / runs / sources）强制 `Bearer` JWT；`get_current_user` 依赖 + `OAuth2PasswordBearer(auto_error=True)` + 常量 `Could not validate credentials` 文案；新增 stub `GET /api/sources/collections`（body 留给 F-010）
- **F-009** `POST /api/sources/collections` 创建 source collection（`SourceCollectionCreate`/`SourceCollectionOut` 模型；`owner_id = current_user.id`；UNIQUE 违例按精确约束名 `source_collection_name_key` 捕获 → 409；async session.add + commit + refresh；checks.sh 新增 `collections)` 层 V1/V2/V3 + `all)` 链插入到 `auth` 与 `buckets` 之间）
- **F-010** `GET /api/sources/collections` 分页列出当前用户的 source collections（`limit`/`offset` Query 参数，默认 20，`ge=1,le=200` / `ge=0`；两条 async 查询:owner 过滤的分页 SELECT + 独立 COUNT，`total` 为全量计数而非页大小；`ORDER BY id ASC`；`CollectionListResponse.items` 由 `list[Any]` 收窄为 `list[SourceCollectionOut]`；checks.sh `collections)` 层新增 LIST-V1/LIST-V2）
- **F-011** `POST /api/sources/upload` 上传 PDF：存入 MinIO `s3://sources/{id}/original.pdf`，写 source 行（`sha256` / `storage_uri` / `kind='file'` / `mime_type='application/pdf'`），返回 `{id, storage_uri}`；首个 app 内 S3 客户端 `storage/s3.py`（`get_s3_client` aioboto3 依赖，可在测试中 override）；flush-then-set 顺序（flush 取 id → 设 `storage_uri`/`dagster_partition_key=src_{id}`，临时键用 `uuid4().hex` → S3 上传 → commit；上传失败则事务隐式回滚不留孤儿行）；非 PDF → 415；checks.sh 新增 `sources)` 层 UPLOAD-V1..V4，`all)` 链插入到 `collections` 与 `buckets` 之间
- **F-012** 上传成功后 FastAPI 通知 Dagster（best-effort，commit 之后）：`add_source_partition` 向 `"sources"` `DynamicPartitionsDefinition` 注册 `src_{source_id}`，`report_source_materialization` 为外部资产 `source` 上报一次 `reportRunlessAssetEvents` 物化事件；两次调用各自 try/except，失败仅记 WARNING 仍返回 201（上传已落库不受 Dagster 可用性影响）。新增 `dagster/dagster_platform/definitions.py` 的 `DynamicPartitionsDefinition("sources")` + `AssetSpec(key="source")`；gateway 新增两个 mutation 方法（沿用 launch_hello_world 错误处理，复用 repositorySelector 常量；`DuplicateDynamicPartitionError` 幂等忽略）；`docker-compose.dev.yml` 为全部 4 个 dagster 服务加 `../dagster:/app/dagster` bind mount（改代码后 restart 即生效，无需 rebuild），并新增 `dagster/.gitignore` 屏蔽运行时目录；checks.sh `dagster)` 层新增 F012-V1（partition 出现）+ F012-V2（物化事件）。注：Dagster 1.11.16 实际 mutation 为 `addDynamicPartition`（单数，需 repositorySelector）+ `reportRunlessAssetEvents`（非设计稿的 reportRuntimeAssetMaterialization）。

- **F-013** `GET /api/sources/{id}` 返回 source 完整记录（`SourceRead` 模型，10 字段含 `storage_uri`/`sha256`/`size`/`mime_type`/`collection_id`，`from_attributes=True`）；async `SELECT ... LEFT JOIN source_collection ... WHERE id=:id AND (collection.owner_id=:uid OR collection_id IS NULL)` + `scalar_one_or_none()`，缺失或越权一律 404（不泄露存在性，沿用 F-010 owner-scoping）；新路由追加在固定路径之后，不遮蔽 `/collections`；source 表无 `owner_id`，未归集 source 对所有已认证用户可见（严格归属需迁移，推迟）；checks.sh `sources)` 层新增 F013-V1（200 + 全字段）/ F013-V2（99999 → 404）。
- **F-014** `GET /api/sources/collections/{id}/sources` 分页列出某 collection 内的 sources（新增 `SourceListResponse {items: list[SourceRead], total: int}`，复用 F-013 的 `SourceRead`；3 条 async 查询:先做归属校验 SELECT（`id == :id AND owner_id == :uid`）`scalar_one_or_none()`，缺失或越权一律 404 `Collection not found`，再做分页 SELECT + collection 维度全量 COUNT；**不**用 JOIN 折叠归属——否则越权 collection 会返回 200 空列表而泄露语义；`limit` 默认 20 `ge=1,le=200` / `offset` `ge=0`，`ORDER BY Source.id ASC`；路由插在 `POST /collections` 与 `POST /upload` 之间，保持 `GET /{id}` 仍为最后；checks.sh `sources)` 层新增 F014-V1（建 collection + 传 3 PDF → 200 / total>=3 / 每项含 5 必需字段）/ F014-V2（不存在 collection → 404）。
- **F-015** 算子注册表种子:`python -m dataplat_api.cli seed-operators` 幂等 async 子命令,向 `operator` 表插入 MinerU extractor 行(`name=mineru` / `version=0.1.0` / `category=extractor` / `input_kind=source` / `output_kind=document` / `image=dataplat/mineru:0.1.0` 占位待 F-019 / 3 属性合法 JSON Schema `config_schema`);沿用 F-007 `seed-admin` 结构(`SessionLocal()` async session + `await session.execute(select(...))` 幂等守卫 + `session.add` + commit),幂等键为 `(name, version)` 匹配 `uq_operator_name_version`;不入 Alembic 迁移(种子数据不应进 schema 迁移);无 API 面变更故 `make codegen` 不适用(invariant #6 N/A);checks.sh 新增 `operators)` 层 V1(行字段值 + count=1)/ V2(`config_schema->>'type'='object'` 证 JSONB 合法)/ V3(重跑仍 count=1 幂等),并接入 `all)` 链(`runs` 之后)。
- **F-016** `GET /api/operators` 列出激活算子（可选 `?category=` 过滤）：新增 `routers/operators.py` + `OperatorRead` 模型（10 字段含 id/name/version/category/config_schema + input_kind/output_kind/image/description/is_active，`from_attributes=True`），返回**纯 JSON 数组**（非分页——算子注册表为小型有界目录）；`Depends(get_current_user)` 强制鉴权（无 token → 401）；async `select(Operator).where(Operator.is_active.isnot(False)).order_by(Operator.id.asc())`，`category` 非空时追加 `WHERE category=:c`，未知 category 返回 200 + `[]`（非 404）；`is_active IS NOT FALSE`（非 `=true`）以兼容种子行依赖 server_default 而 ORM 侧可能为 NULL 的情况；checks.sh `operators)` 层在 F-015 之后新增 F016-AUTH（401）/ F016-V1（category=extractor 含 mineru）/ F016-V2（每项 5 必需字段 + mineru v0.1.0 config_schema 为 object）/ F016-V3（category=tagger → 200 + 空数组，无 tagger 种子故空属预期）；openapi.json 同 commit 重生成（invariant #6）。
- **F-017** `GET /api/operators/{id}` 返回算子完整记录（新增 `OperatorDetail` 模型，覆盖全部 19 个 ORM 列含 `config_schema`/`output_schema`/`default_config`，类型与可空性逐列匹配 ORM；`OperatorRead` 保持精简不变——list 用精简、detail 用全量,各自显式契约）；算子为全局注册表无 `owner_id`,故 detail 不做归属过滤——`select(Operator).where(id==:id)` + `scalar_one_or_none()`,缺失即 404 `Operator not found`(与 F-013 source detail 不同:无防枚举语义,因算子对所有已认证用户无条件可见);路由 `/{operator_id}` 与 list 的 `""` 路径不冲突,main.py 无需改动(F-016 已挂载 router);checks.sh `operators)` 层新增 F017-V1(从 list 动态解析 mineru id → GET detail → 200 + `config_schema.type=='object'` + 严格 `isinstance(default_config, dict)`(detail 走 SELECT 读真实库值 `{}`,非 insert 缓冲) + `output_schema` 键存在(种子未设故为 null))/ F017-V2(99999 → 404);openapi.json 同 commit 重生成,新增 `/api/operators/{operator_id}` 路径 + `OperatorDetail` 组件,未覆盖 F-016 的 `OperatorRead`。
- **F-018** `POST /api/runs {asset:'extract_mineru', source_ids:[...]}` 触发 MinerU 抽取——首个「发起运行」端点(202 Accepted)。在 `definitions.py` 新增可物化 `@asset extract_mineru`(由既有 `sources_partitions` 分区,stub body 仅 log + 返回 `MaterializeResult()`,真实抽取逻辑=F-019;AssetSpec/外部资产不可 backfill 故必须是真 `@asset`);gateway 新增 `launch_extract_backfill(partition_keys)`(实时内省确认的 `launchPartitionBackfill(backfillParams:LaunchBackfillParams!)`,`assetSelection:[{path:[extract_mineru]}]`+`partitionNames`,成功取 `LaunchBackfillSuccess.backfillId`,全失败模式→`DagsterGatewayError`;设计稿 §5.4 伪码已二次过时不采用);`RunCreate(asset:Literal[extract_mineru], source_ids:min_length=1)`+`RunCreateResponse`;handler 顺序(agreed §7):源存在性校验(任一缺失→404)→`src_{id}` 分区键→防御性幂等 `add_source_partition`→launch backfill(`DagsterGatewayError`→503)→写 run 行(`dagster_run_id=backfillId` 因 NOT NULL UNIQUE 故必须 launch-first;`kind=extract`/`status=pending`/`asset_keys=[extract_mineru]`/`partition_keys`/`triggered_by=user.id`)→commit+refresh→202。backfill 粒度追踪(一个 backfillId 派生 N 个分区 run,`launchedRunIds` 暂不用);commit-后-失败=孤儿 backfill(MVP 可接受漏,同 F-011/F-012)。`launchPartitionBackfill` 是设计稿 L189/L214/L1008 钦定机制(Dagster=编排骨架),CLAUDE.md「MVP 用 RQ」指插件执行沙箱层非编排层。新增 `test_runs_trigger.py`(6 例:202/503/422 错 asset/422 空/404 缺源 + 全 body);checks.sh `runs)` 层扩展 F018-V1(202+dagster_run_id+run_id)/V2(`^extract|pending$`)/V3(`partitionBackfillOrError` isAssetBackfill+extract_mineru);openapi.json 同 commit。改 `definitions.py` 后需 `docker compose restart dagster-webserver`(bind-mount,不 rebuild)。
- **F-019** `extract_mineru` 算子做真实工作(替换 F-018 stub):Dagster asset 从 MinIO 读 `s3://sources/{id}/original.pdf` → 产出**最小但 schema-valid 的 DoclingDocument** JSON(`docling_core` 的 `DoclingDocument(name=...)`,不构造 `DocumentOrigin`/binary_hash——docling-core 会把 sha256 截断成 64bit 故无意义,源 sha256 已在 source 行权威保存) → 写 `s3://documents/{id}/extract_mineru/doc.docling.json` → 写 `document_variant` 行(raw psycopg2 同步,因 asset 在 dagster/ 非 apps/api/ 故不受 invariant#5 async 约束;`extractor_name=mineru`/`version=0.1.0`/`config_hash=sha256({})=44136fa3...`/`storage_prefix=s3://documents/{id}/extract_mineru/`/`dagster_run_id=context.run_id` 即每分区 RUN id,区别于 run 表的 backfillId;`is_canonical`=无 canonical 时 TRUE(事务内预查)+ `ON CONFLICT(source_id,extractor_name,config_hash) DO NOTHING` 幂等)。**人定范围**:最小真 DoclingDocument 非完整 MinerU(设计稿「只打通最小路径」);crit-1 GET /api/sources/{id}/documents=F-020 未建,用 psql 查 variant 行代理验证 + 注记延后 F-020。**基础设施**:dagster 镜像加 boto3/docling-core/pytest(rebuild),daemon+webserver 注入 MINIO_*+PLATFORM_DB_URL;实测证明 backfill 经 managed-gRPC code location + DefaultRunLauncher 真正跑到 COMPLETED_SUCCESS(workers sleep-infinity 不参与)。S3 读 key 修正为 `sources/{id}/original.pdf`(F-011 把 bucket 名也前缀进 key 的既有 quirk)。新增 `dagster/dagster_platform/extractor.py`(纯函数 helper) + `dagster/tests/test_extractor.py`(9 测,容器内 pytest 跑) + checks.sh `extract)` 层(容器内单测 + E2E 轮询 backfill 到 COMPLETED_SUCCESS 120s + V1-proxy/V2/V3/V4)。invariant #6 N/A(无 API 面),无迁移。已知 MEDIUM(多算子并发 canonical 竞态,单算子 MVP 不可达,留待生产)。
- **F-020** `GET /api/sources/{source_id}/documents` 列出 document variants：返回指定 source 的所有 `document_variant` 行（纯 JSON 数组,不分页——每 source 通常 1–3 个 variant）；新增 `DocumentVariantRead` 模型（10 字段：id / extractor_name / extractor_version / config_hash / storage_prefix / page_count / image_count / is_canonical / materialized_at / dagster_run_id，`from_attributes=True`）；**两步 owner-scoping**（同 F-013 `GET /{id}`：`LEFT JOIN source_collection`+ `OR(owner_id=caller, collection_id IS NULL)`，缺失或越权统一 404 防枚举）；路由插在 `POST /upload` 之后、`GET /{id}` catch-all 之前；accessible source 无 variant → 200+`[]`，source 不存在/越权 → 404；checks.sh 新增 `documents)` 层（自包含：上传+触发抽取+轮询到 COMPLETED_SUCCESS+V1 200/array[1]/5 必需字段+V2 99999→404），接入 `all)` 链；openapi.json 同 commit 重生成（invariant #6）。
- **F-021** `POST /api/sources/{source_id}/documents/{extractor_name}/set-canonical` 设置 canonical variant：原子性(单事务内) CLEAR `is_canonical=FALSE` 旧行 + SET `is_canonical=TRUE` 目标行(按 extractor_name 最高 id);CLEAR-first 避免暂态违反 `idx_doc_canonical` 部分唯一索引;两条 UPDATE 均设 `synchronize_session=False`(refresh 提供权威状态);`await session.refresh(target)` 必须在 commit 后调用(expire_on_commit=True 否则 MissingGreenlet);owner-scoping 同 F-020;源不存在→404 "Source not found" / variant 不存在→404 "Variant not found";返回 `DocumentVariantRead`(HTTP 200);checks.sh `documents)` 层扩展 V1(200+字段)/V2(psql COUNT=1 + extractor_name=mineru)/V3a(pg_indexes 索引存在)/V3b(INSERT probe 行+UPDATE→ERROR 唯一约束拒绝+cleanup);openapi.json 同 commit。

下一批候选：F-022（render document preview）/ F-023（Lance chunks 表初始化）/ F-024（触发分块）。

---

## 范围与目标

**在范围**：文档解析 / chunking / 属性标注 / 数据增强 / 数据集物化；文本为主，图像作附属资产；单人–小团队规模，百万级 chunks；单租户。

**不在范围**：分布式千万级 chunks、多租户/企业权限/计费、模型训练本身（用 HF transformers / llama-factory）、实时数据流、通用 RAG 应用。

详见设计文档 §1.3。

---

## License

待定。
