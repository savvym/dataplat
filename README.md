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

13/105 features 通过：

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

下一批候选：F-014（列出 collection 内 sources）/ F-015（MinerU operator 注册种子）/ F-016（列出 operators）。

---

## 范围与目标

**在范围**：文档解析 / chunking / 属性标注 / 数据增强 / 数据集物化；文本为主，图像作附属资产；单人–小团队规模，百万级 chunks；单租户。

**不在范围**：分布式千万级 chunks、多租户/企业权限/计费、模型训练本身（用 HF transformers / llama-factory）、实时数据流、通用 RAG 应用。

详见设计文档 §1.3。

---

## License

待定。
