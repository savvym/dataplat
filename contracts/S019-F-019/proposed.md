# S019-F-019 — Proposed Contract

**Status:** PROPOSED (reviewer iteration 1 applied — awaiting APPROVED)
**Date drafted:** 2026-05-25
**Author:** Plugin-implementer (Claude)
**Sprint-id:** S019-F-019
**Depends on:** F-018 (passes: true)

---

## §1 Goal + Scope Boundary

F-019 makes the `extract_mineru` Dagster asset do real work:

1. Read the real PDF bytes from `s3://sources/{source_id}/original.pdf` (MinIO).
2. Produce a **minimal but schema-valid** `DoclingDocument` JSON.  This is an honest stub extraction — it does NOT pretend to parse PDF content; it records the PDF byte length and best-effort page count, and emits a valid DoclingDocument skeleton.  Real MinerU/magic-pdf parsing is **explicitly out of scope**.
3. Write the document JSON to `s3://documents/{source_id}/extract_mineru/doc.docling.json` (MinIO).
4. Write a `document_variant` row to the `platform` Postgres database.
5. Inject the MinIO and platform-DB credentials the Dagster services currently lack.
6. **Prove** that a backfill run reaches per-partition status `SUCCESS` (not just `REQUESTED`).

**F-020 deferral (Criterion 1 proxy):** The feature spec's verification criterion 1 — `GET /api/sources/{source_id}/documents` returns 1 variant — requires F-020 (list document variants endpoint, `depends_on: F-019`, not yet built).  Following the honest-proxy pattern used in F-012/F-016, criterion 1 is verified here via a direct `psql` query asserting `document_variant` row existence with `extractor_name='mineru'`.  The literal GET-endpoint check is deferred to F-020.

**Invariant #6 (OpenAPI/TS sync) does NOT apply** — this sprint makes no API surface changes.

**DB migration NOT needed** — the `document_variant` table already exists (created by F-002 migration).

---

## §2 Files Changed

| Path | New / Modified | Why |
|---|---|---|
| `docker/dagster/Dockerfile` | **MODIFIED** | Add `boto3` (S3 client), `docling-core` (DoclingDocument type), and `pytest` (to run the `extract)` layer's helper unit tests inside the container) to the pip install list. No other changes. |
| `docker/docker-compose.dev.yml` | **MODIFIED** | Add `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `POSTGRES_DB` (platform DB), and `PLATFORM_DB_URL` to `dagster-webserver` and `dagster-daemon` service environments. Workers (`dagster-worker-cpu`, `dagster-worker-heavy`) run `sleep infinity` and do not execute assets; they do **not** need these env vars.  The `fastapi` service env is untouched. |
| `dagster/dagster_platform/definitions.py` | **MODIFIED** | Replace the stub body of `extract_mineru` with real logic (see §3). |
| `dagster/dagster_platform/extractor.py` | **NEW** | Helper module with pure functions: `build_s3_client()`, `read_pdf_bytes()`, `estimate_page_count()`, `build_docling_document()`, `write_document_json()`, `insert_document_variant()`.  Keeping this out of `definitions.py` makes unit-testing straightforward and keeps `definitions.py` as a thin orchestration file. |
| `dagster/tests/__init__.py` | **NEW** | Empty; makes `dagster/tests/` a package. |
| `dagster/tests/test_extractor.py` | **NEW** | Unit tests for the pure helpers in `extractor.py` — 3 cases: (a) `config_hash` constant, (b) `estimate_page_count` on the synthetic fixture and garbage blob, (c) `build_docling_document` shape check. See §2a for test-execution environment. |
| `verify/checks.sh` | **MODIFIED** | Add new `extract)` layer implementing V1-proxy through V4 assertions (see §7). Add `bash "$0" extract` to `all)` chain after `operators`. |

---

## §2a Test Execution Environment for `dagster/tests/test_extractor.py`

`extractor.py` imports `docling-core`, which is only installed in the Dagster image — not in `apps/api`'s venv.  The `backend` pytest layer (`bash verify/checks.sh backend`) runs inside `apps/api/` and would not find `docling_core`.

**Resolution:** The tests run **inside the `dagster-webserver` container** (which has `docling-core` installed after the image rebuild), invoked as a separate step at the top of the `extract)` layer before the E2E flow:

```bash
docker compose -f "$COMPOSE" exec -T dagster-webserver \
  python -m pytest /app/dagster/tests/test_extractor.py -q \
  || { echo "FAIL: extractor unit tests failed"; exit 1; }
echo "  extractor unit tests: OK"
```

The bind-mount (`../dagster:/app/dagster`) makes `dagster/tests/test_extractor.py` available inside the container at `/app/dagster/tests/test_extractor.py` without a rebuild.  The `all)` chain's `extract` invocation picks these up automatically.

These tests do **not** run in the `backend` layer and are **not** added to `apps/api/tests/`.  They are scoped to the `extract` layer only.  No `pytest.ini` or `pyproject.toml` is needed inside `dagster/` — `python -m pytest` with an explicit path is sufficient.

**Test cases in `test_extractor.py`:**

```python
# (a) config_hash constant
def test_config_hash_constant():
    import json, hashlib
    from dagster_platform.extractor import CONFIG_HASH
    expected = hashlib.sha256(
        json.dumps({}, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()
    assert CONFIG_HASH == expected == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"

# (b) estimate_page_count
def test_estimate_page_count_synthetic_pdf():
    from dagster_platform.extractor import estimate_page_count
    pdf = (
        b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
        b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
        b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
        b'xref\n0 4\n0000000000 65535 f \n...\ntrailer...\n%%EOF\n'
    )
    assert estimate_page_count(pdf) == 1

def test_estimate_page_count_garbage():
    from dagster_platform.extractor import estimate_page_count
    assert estimate_page_count(b"not a pdf at all") == 0

# (c) build_docling_document shape
def test_build_docling_document_shape():
    import json
    from dagster_platform.extractor import build_docling_document
    pdf_bytes = b"%PDF-1.4 minimal"
    result = build_docling_document(source_id=42, pdf_bytes=pdf_bytes, page_count=0)
    data = json.loads(result)
    assert data["schema_name"] == "DoclingDocument"
    assert data["name"] == "source_42"
    assert "origin" not in data or data.get("origin") is None  # no binary_hash field
    assert data["pages"] == {}   # 0 pages → empty dict
```

---

## §3 Asset Body Design — Step by Step

The revised `extract_mineru` asset body (in `definitions.py`) calls helpers from `extractor.py`:

```
1.  source_id  = int(context.partition_key.removeprefix("src_"))
2.  s3         = build_s3_client()          # boto3 from MINIO_* env vars
3.  pdf_bytes  = read_pdf_bytes(s3, source_id)
                 # s3.get_object(Bucket="sources", Key=f"{source_id}/original.pdf")
                 # raises RuntimeError (logged) if object not found
4.  page_count = estimate_page_count(pdf_bytes)
                 # regex scan for /Count N in PDF xref table; falls back to 0 on failure
5.  doc_json   = build_docling_document(source_id, pdf_bytes, page_count)
                 # see §4 for exact shape
6.  write_document_json(s3, source_id, doc_json)
                 # s3.put_object(Bucket="documents",
                 #               Key=f"{source_id}/extract_mineru/doc.docling.json",
                 #               Body=doc_json.encode(), ContentType="application/json")
7.  insert_document_variant(source_id, page_count, context.run_id)
                 # raw psycopg2 INSERT; see §5 and §6
8.  return MaterializeResult(metadata={
        "source_id": MetadataValue.int(source_id),
        "page_count": MetadataValue.int(page_count),
        "bytes": MetadataValue.int(len(pdf_bytes)),
        "storage_key": MetadataValue.text(f"documents/{source_id}/extract_mineru/doc.docling.json"),
    })
```

**Idempotency:** `insert_document_variant` uses `INSERT ... ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING`.  Re-running the asset for the same source does not crash and does not violate the unique constraint.  The S3 `put_object` is naturally idempotent (overwrites the same key).  The partial-unique canonical index (`idx_doc_canonical`) is respected: `is_canonical = TRUE` only when no canonical row for that source exists yet (see §6).

---

## §4 DoclingDocument Minimal Shape

**Decision: use `docling-core`.**
`docling-core 2.77.0` pulls only: `pydantic` (already present), `pillow`, `pandas`, `numpy`, `jsonschema`, `typer`, `latex2mathml`, `pyyaml` (already present), `tabulate` (already present).  No torch, no heavy ML, no GPU deps.  The added image size is material (~80–100 MB for pillow + pandas + numpy wheels) but acceptable for an extraction service.  Using the actual `DoclingDocument` model from `docling_core.types.doc.document` gives us guaranteed schema validity and forward-compatibility as the docling schema evolves — a hand-rolled JSON dict would need maintenance every time the schema changes.

Live introspection of `DoclingDocument.model_json_schema()` (run against the live dagster-webserver container) showed:

- **Only required field:** `name` (string).
- All other fields (`origin`, `furniture`, `body`, `groups`, `texts`, `pictures`, `tables`, `key_value_items`, `form_items`, `pages`) are optional with defaults.

**`binary_hash` dropped entirely.**  Live investigation found that `DocumentOrigin.binary_hash` truncates the hex sha256 to a 64-bit integer (`& 0xFFFFFFFFFFFFFFFF`) on serialization, making it useless as an integrity check and misleading to downstream readers.  The authoritative sha256 of the PDF is already stored on the `source.sha256` column (F-011) and does not need a truncated copy in the DoclingDocument.  `DocumentOrigin` is dropped completely — `name` alone satisfies the schema.

The asset constructs:

```python
from docling_core.types.doc.document import DoclingDocument, PageItem
from docling_core.types.doc.base import Size

doc = DoclingDocument(name=f"source_{source_id}")
# Add a page entry for each estimated page (best-effort; 0 pages → empty dict, still valid)
for page_no in range(1, page_count + 1):
    doc.pages[page_no] = PageItem(page_no=page_no, size=Size(width=612.0, height=792.0))
doc_json = doc.model_dump_json()   # guaranteed schema-valid
```

Resulting JSON shape (example, 1-page PDF):

```json
{
  "schema_name": "DoclingDocument",
  "version": "1.10.0",
  "name": "source_7",
  "origin": null,
  "furniture": {"self_ref": "#/furniture", "children": [], ...},
  "body": {"self_ref": "#/body", "children": [], ...},
  "groups": [], "texts": [], "pictures": [], "tables": [],
  "key_value_items": [], "form_items": [],
  "pages": {
    "1": {"size": {"width": 612.0, "height": 792.0}, "image": null, "page_no": 1}
  }
}
```

The asset handles the synthetic minimal PDF fixture used in `checks.sh` (which has 1 page per `/Count` field) without crashing.  When `estimate_page_count` returns 0 (e.g. malformed PDF or regex miss), `pages` is `{}` — still valid per the schema.

**Schema source:** `docling_core.types.doc.document.DoclingDocument` (version 1.10.0 as shipped with docling-core 2.77.0), confirmed via live `model_json_schema()` introspection on the running dagster-webserver container.

---

## §5 DB Access from the Asset

**Chosen mechanism: raw `psycopg2` with a parameterized `INSERT`.**

Rationale:
- `psycopg2-binary==2.9.10` is already installed in the Dagster image (required by `dagster-postgres`).  Adding it again is a no-op; no new dep is needed for DB access.
- The asset runs synchronously (Dagster assets are sync by default in this stack).  Using `asyncpg` / async SQLAlchemy from a sync context would require an event-loop bridge — unnecessary complexity.
- Importing `dataplat_api` into the Dagster image would couple the two codebases and require installing all FastAPI deps in Dagster — not acceptable.
- Raw `psycopg2` with parameterized queries satisfies idempotency, avoids injection, and keeps the Dagster image dependency footprint minimal.

**Hard invariant #5 compliance:** Invariant #5 ("async SQLAlchemy from day one") is scoped to `apps/api/dataplat_api/`.  The Dagster asset lives in `dagster/dagster_platform/` — outside `apps/api/` — and runs synchronously.  A sync psycopg2 insert there is **not** a violation.

**Platform DB connection string:** The asset reads:
```
PLATFORM_DB_URL=postgresql://app:devpassword@postgres:5432/platform
```
This env var is added to `dagster-webserver` and `dagster-daemon` services in `docker-compose.dev.yml` (see §5a).  The asset builds the connection as:
```python
import os, psycopg2
conn = psycopg2.connect(os.environ["PLATFORM_DB_URL"])
```
The connection is opened, used, and closed within `insert_document_variant()` — no connection pool, no persistent state.  This is safe for the MVP throughput.

---

## §5a Infra Changes

### Env vars added to `dagster-webserver` and `dagster-daemon` in `docker-compose.dev.yml`

```yaml
MINIO_ENDPOINT: ${MINIO_ENDPOINT:-minio:9000}
MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-devpassword}
POSTGRES_DB: ${POSTGRES_DB:-platform}
PLATFORM_DB_URL: postgresql://${POSTGRES_USER:-app}:${POSTGRES_PASSWORD:-devpassword}@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-platform}
```

`MINIO_ENDPOINT` is not currently in `.env.example` (it is set inline in the `fastapi` service). Adding it directly to the compose service `environment:` block (with default `minio:9000`) avoids adding it to `.env.example` and maintains consistency with how the `fastapi` service already defines it.

Workers (`dagster-worker-cpu`, `dagster-worker-heavy`) run `sleep infinity` and **do not** execute asset logic — they do not get these env vars.

### Deps added to `docker/dagster/Dockerfile`

```dockerfile
RUN pip install --no-cache-dir \
    dagster==1.11.16 \
    dagster-webserver==1.11.16 \
    dagster-postgres==0.27.16 \
    psycopg2-binary==2.9.10 \
    boto3==1.37.38 \
    docling-core==2.77.0 \
    pytest==8.3.4
```

`boto3==1.37.38` is pinned to a specific version for reproducibility. `docling-core==2.77.0` is pinned to the version confirmed in live dry-run. `pytest==8.3.4` is REQUIRED so the `extract)` layer's helper unit tests (`python -m pytest /app/dagster/tests/...` inside the dagster-webserver container) can run — confirmed live that pytest is NOT otherwise present in the image. No extra operational cost: the image rebuild is already required for boto3/docling-core. (If `pytest==8.3.4` is unavailable on the local mirror, use `pytest>=8,<9`.)

### Operational steps required

Because deps are added to the Dockerfile, an **image rebuild is required**:

```bash
# From repo root:
docker compose -f docker/docker-compose.dev.yml build dagster-webserver dagster-daemon
docker compose -f docker/docker-compose.dev.yml up -d --force-recreate dagster-webserver dagster-daemon
```

The bind-mount (`../dagster:/app/dagster`) means code changes to `definitions.py` and new `extractor.py` are picked up without rebuild; only the dep changes require a rebuild.

---

## §6 `document_variant` INSERT Logic

Exact column values:

| Column | Value | Notes |
|---|---|---|
| `source_id` | `source_id` (int from partition_key) | FK to `source.id` |
| `extractor_name` | `'mineru'` | Matches operator table row from F-015 |
| `extractor_version` | `'0.1.0'` | Matches operator.version seed from F-015 |
| `config_hash` | `sha256(json.dumps({}, sort_keys=True, separators=(',', ':')))` = `'44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'` | SHA-256 of canonical JSON of the operator config `{}` (the default_config). Constant for this operator version. |
| `storage_prefix` | `f's3://documents/{source_id}/extract_mineru/'` | Full `s3://` URI prefix, consistent with `source.storage_uri` format (`s3://sources/{id}/original.pdf`). The doc JSON lives at `{storage_prefix}doc.docling.json`. |
| `page_count` | `page_count` (int, 0 if estimation failed) | Best-effort from regex |
| `image_count` | `0` | Honest: no image extraction in minimal stub |
| `is_canonical` | `TRUE` if no canonical row exists yet for this source_id; `FALSE` otherwise | Determined by a pre-INSERT query: `SELECT COUNT(*) FROM document_variant WHERE source_id=%s AND is_canonical=TRUE`. If 0 rows → set `TRUE`; else `FALSE`. This check + INSERT is done in a single transaction to avoid race conditions. |
| `dagster_run_id` | `context.run_id` | The per-partition **run** ID (UUID string). This is the Dagster run that executed the asset, NOT the backfill ID. Confirmed: `context.run_id` in a per-partition run = `'0a0ee6b7-1212-42bb-a0c7-37c70a7610d1'` (format: UUID), while the backfill ID (stored in `run.dagster_run_id` by F-018) = `'bjaipijg'` (short alphanumeric). |

**Full SQL (parameterized):**

```sql
BEGIN;
SELECT COUNT(*) FROM document_variant WHERE source_id = %s AND is_canonical = TRUE;
-- if count == 0: is_canonical_val = TRUE else FALSE
INSERT INTO document_variant
    (source_id, extractor_name, extractor_version, config_hash,
     storage_prefix, page_count, image_count, is_canonical, dagster_run_id)
VALUES (%s, 'mineru', '0.1.0', %s, %s, %s, 0, %s, %s)
ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING;
COMMIT;
```

`ON CONFLICT DO NOTHING` handles re-materializations gracefully: the S3 write already overwrote the doc JSON, and the DB row stays as-is (not updated). This is acceptable for the MVP; a future enhancement could `DO UPDATE SET dagster_run_id = EXCLUDED.dagster_run_id`.

---

## §7 Verification Mapping — `extract` layer in `checks.sh`

The new `extract)` layer in `verify/checks.sh` implements the full E2E flow:

### Setup

```bash
COMPOSE="docker/docker-compose.dev.yml"
[[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
# Mirror the buckets) block exactly — these vars are block-local under set -euo pipefail
MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

# Run extractor unit tests inside the dagster-webserver container
# (docling-core is only installed in the Dagster image, not in apps/api venv)
docker compose -f "$COMPOSE" exec -T dagster-webserver \
  python -m pytest /app/dagster/tests/test_extractor.py -q \
  || { echo "FAIL: extractor unit tests failed"; exit 1; }
echo "  extractor unit tests: OK"

# 1. Mint Bearer token (admin@example.com / testpassword123)
# 2. Generate minimal valid PDF (same synthetic blob as F-018)
# 3. POST /api/sources/upload → capture SRC_ID
# 4. POST /api/runs {"asset": "extract_mineru", "source_ids": [SRC_ID]} → capture BACKFILL_ID
```

### Poll to SUCCESS

```bash
# Poll partitionBackfillOrError until COMPLETED_SUCCESS or timeout (120s)
# Check interval: 3s, max attempts: 40
#
# GraphQL query (from inside dagster-webserver container):
#   partitionBackfillOrError(backfillId: $BACKFILL_ID) {
#     ... on PartitionBackfill { status }
#   }
#
# Terminal states: COMPLETED_SUCCESS, COMPLETED_FAILED, CANCELED
# On timeout: FAIL loudly with "timeout waiting for backfill completion (last status=...)"
#
# After backfill COMPLETED_SUCCESS, also query the per-partition run status:
#   runsOrError(filter: {tags: [{key: "dagster/backfill", value: $BACKFILL_ID}]}) {
#     ... on Runs { results { runId status } }
#   }
# Assert: at least one run with status=SUCCESS
```

**Live evidence:** In the investigation, backfill `bjaipijg` reached `COMPLETED_SUCCESS` in ~28s, and the per-partition run `0a0ee6b7-...` reached `SUCCESS`.  The 120s timeout provides 4x headroom.

### V1-proxy (Criterion 1 via DB query — F-020 deferred)

```bash
docker compose -f "$COMPOSE" exec -T postgres \
  psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
    "SELECT extractor_name || '|' || extractor_version
     FROM document_variant WHERE source_id=${SRC_ID} AND extractor_name='mineru'" \
  | grep -q '^mineru|0\.1\.0$' \
  || { echo "FAIL: V1-proxy — document_variant row missing or extractor_name/version wrong"; exit 1; }
echo "  V1-proxy OK: document_variant row exists (extractor_name=mineru, version=0.1.0)"
echo "  NOTE: literal GET /api/sources/${SRC_ID}/documents check deferred to F-020"
```

### V2 — MinIO object exists + valid JSON

```bash
docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${SRC_ID}" \
  fastapi python -c "
import boto3, os, json, sys
s3 = boto3.client('s3', endpoint_url='http://minio:9000',
    aws_access_key_id=os.environ['S3_USER'],
    aws_secret_access_key=os.environ['S3_PASS'])
src_id = os.environ['SRC_ID']
key = f'{src_id}/extract_mineru/doc.docling.json'
try:
    resp = s3.get_object(Bucket='documents', Key=key)
    body = resp['Body'].read()
    data = json.loads(body)   # must be valid JSON
    assert data.get('schema_name') == 'DoclingDocument', f'schema_name wrong: {data.get(\"schema_name\")}'
    print(f'  V2 OK: doc.docling.json exists at {key}, {len(body)} bytes, schema_name=DoclingDocument')
except Exception as e:
    print(f'FAIL: V2 — {e}', file=sys.stderr)
    sys.exit(1)
" || { echo "FAIL: V2 check failed"; exit 1; }
```

### V3 — `is_canonical = TRUE`

```bash
docker compose -f "$COMPOSE" exec -T postgres \
  psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
    "SELECT is_canonical FROM document_variant
     WHERE source_id=${SRC_ID} AND extractor_name='mineru'" \
  | grep -q '^t$' \
  || { echo "FAIL: V3 — is_canonical is not TRUE"; exit 1; }
echo "  V3 OK: is_canonical=TRUE"
```

### V4 — `dagster_run_id` NOT NULL

```bash
docker compose -f "$COMPOSE" exec -T postgres \
  psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
    "SELECT dagster_run_id FROM document_variant
     WHERE source_id=${SRC_ID} AND extractor_name='mineru'" \
  | grep -qE '^[0-9a-f-]{30,}$' \
  || { echo "FAIL: V4 — dagster_run_id is NULL or empty"; exit 1; }
echo "  V4 OK: dagster_run_id is non-null"
```

(`dagster_run_id` is a UUID like `0a0ee6b7-1212-42bb-a0c7-37c70a7610d1`; the regex `^[0-9a-f-]{30,}$` matches any UUID without hardcoding the exact value.)

---

## §8 Execution Reality — Run-to-SUCCESS Finding

**Investigation performed against live stack (2026-05-25).**

**Finding: backfill runs DO execute to SUCCESS without any additional configuration.**

Details:
- `dagster.yaml` does not declare `run_launcher:` or `run_coordinator:` — Dagster's defaults apply.
- `DagsterInstance.get()` in the live daemon reports `run_launcher: DefaultRunLauncher` and `run_coordinator: QueuedRunCoordinator` (dequeue_interval_seconds=5).
- `workspace.yaml` uses `load_from: python_module:` which creates a `ManagedGrpcPythonEnvCodeLocationOrigin`. This means the **webserver/daemon manages its own gRPC subprocess** (`dagster api grpc`) for the code location. The `DefaultRunLauncher` can talk to this managed gRPC server and execute runs.
- The workers (`dagster-worker-cpu`, `dagster-worker-heavy`) running `sleep infinity` are **NOT involved** in asset execution. Asset runs are executed in-process by the managed gRPC subprocess managed by the webserver/daemon.
- `BACKFILL` and `QUEUED_RUN_COORDINATOR` daemons are both healthy (confirmed via `instance.daemonHealth` query).

**Live proof:** Triggered backfill `bjaipijg` for source id=7, partition key `src_7`:
- `partitionBackfillOrError.status` progressed `REQUESTED → COMPLETED_SUCCESS` in ~28s.
- `runsOrError(filter: tags dagster/backfill=bjaipijg)` returned run `0a0ee6b7-...` with `status=SUCCESS`.

**Conclusion: no dagster.yaml / executor changes are needed to make runs execute.** The only infra changes required for F-019 are (a) adding deps to the Dockerfile and (b) adding env vars to daemon + webserver compose services.

---

## §9 Invariant Compliance

| Invariant | Status | Notes |
|---|---|---|
| **#1 Lineage mandatory** | PARTIAL — MVP acceptable | `document_variant` records `source_id` (input ref), `extractor_name` + `extractor_version` + `config_hash` (processor identity + config hash), and `dagster_run_id` (execution provenance). This IS the variant-level provenance. Full Commit lineage (`parents[]` + processor identity in a Commit object) is a later abstraction (F-054 DoclingDocIOManager). |
| **#2 Storage separation + CAS** | SATISFIED | DoclingDocument JSON bytes live in MinIO (`s3://documents/...`); only metadata lives in Postgres. We do NOT store doc bytes in Postgres. `storage_prefix` is a pointer, not content. |
| **#3 Schema frozen post-publish** | N/A | No schema publish in this sprint. |
| **#4 LLM calls via gateway** | N/A | No LLM calls. |
| **#5 Async SQLAlchemy in apps/api** | SATISFIED | Invariant is scoped to `apps/api/dataplat_api/`. The Dagster asset is in `dagster/dagster_platform/`, runs synchronously, and uses raw psycopg2 — no violation. |
| **#6 OpenAPI ↔ TS sync** | N/A | No API surface change. |

**Plugin boundary note:** The `extract_mineru` asset lives in `dagster/dagster_platform/` — not under `plugins/`. It is not a `SourceAdapter` or `Processor` in the `plugins/` sense. The operator registry row (from F-015) is the bridge: `operator.name='mineru'` describes the operator; F-019 is the Dagster-side asset implementation that executes when the operator is triggered. The `plugins/` layout (with `SourceAdapter`/`Processor` protocols) is the future abstraction for pluggable operators — F-019 implements the operator logic directly in the Dagster asset as the MVP pattern.

---

## §10 Open Questions

1. **`boto3` version pin:** `boto3==1.37.38` is chosen as a recent stable release available on PyPI. If the local mirror does not have this exact version, the Dockerfile should use `boto3>=1.35.0,<2.0` instead. The implementer should verify on first `docker compose build`.

2. **Idempotency on re-materialization:** `ON CONFLICT DO NOTHING` means the `dagster_run_id` column is NOT updated on re-run. If idempotency requirements change (e.g. "always update run_id on re-extract"), the conflict clause must change to `DO UPDATE SET dagster_run_id = EXCLUDED.dagster_run_id`. This is a known limitation of the MVP implementation — acceptable for now.

3. **`estimate_page_count` reliability:** The regex `/Count N` approach is best-effort and works on the synthetic PDF fixture.  Real-world PDFs with incremental updates, compressed streams, or unusual cross-reference tables may produce incorrect counts.  The count defaults to 0 on failure — the DoclingDocument and DB row are still written correctly.  Real page counting would require `pypdf` (not in scope).

4. **Image size increase:** Adding `docling-core` + `boto3` to the Dagster image will increase its size by approximately 100–120 MB (pillow ~50 MB, pandas ~30 MB, numpy ~30 MB, boto3 ~5 MB). This is acceptable for the dev stack. The CI pipeline (if it builds and caches images) will have a one-time cost.
