# Sprint S028-F-028 — Agreed Contract

> Feature: `quality_gpt4` — Replace stub length-heuristic scorer in `attr_quality` with a real
> LLM-backed scorer routed through an internal FastAPI LLM gateway.
>
> **Reviewer:** Mode A APPROVED (iter 2) — all 3 BLOCKERs + 3 HIGHs + 2 MEDIUMs + 2 NITs addressed.

---

## Goal

Build a two-layer LLM Gateway architecture — a new internal FastAPI endpoint
(`POST /api/internal/llm/completions`) that wraps the Anthropic SDK, plus a refactored
`quality_tagger.py` that calls it via plain `requests` — so that the `attr_quality` Dagster
asset scores chunk quality with an LLM while satisfying hard invariant #4 (no direct SDK calls
outside `apps/api/dataplat_api/llm/`). A mock mode (no `ANTHROPIC_API_KEY` → `score=0.5,
model="mock"`) enables CI without burning API credits.

---

## 2. Files changed / created

### New files

| File | Purpose |
|---|---|
| `apps/api/dataplat_api/llm/__init__.py` | Package init; exports `LLMGateway` class. |
| `apps/api/dataplat_api/llm/gateway.py` | `LLMGateway` — wraps Anthropic SDK; mock mode when `ANTHROPIC_API_KEY` absent. Module-level singleton. |
| `apps/api/dataplat_api/llm/router.py` | `POST /api/internal/llm/completions` — no auth; bridges HTTP → `LLMGateway`. `include_in_schema=False`. |
| `apps/api/tests/test_llm_gateway.py` | Unit tests for `LLMGateway` (mock mode, happy path, error handling). |
| `dagster/tests/test_quality_tagger_llm.py` | Unit tests for refactored `quality_tagger.py` with mocked `requests.post`. |

### Modified files

| File | Change |
|---|---|
| `apps/api/dataplat_api/main.py` | Register `llm_router` (`/api/internal/llm`). |
| `apps/api/pyproject.toml` | Add `anthropic>=0.40.0` to `[project.dependencies]`. |
| `dagster/dagster_platform/quality_tagger.py` | Replace stub with `score_chunks_via_gateway()`; dynamic `attr_quality_provider` from response `model` field; drop Option A SQL path entirely. |
| `dagster/dagster_platform/definitions.py` | Update `attr_quality` asset description (remove F-028 forward-reference). |
| `docker/docker-compose.dev.yml` | Add `ANTHROPIC_API_KEY`, `LLM_GATEWAY_URL`, `LLM_MODEL` to all four Dagster services; add `ANTHROPIC_API_KEY`, `LLM_MODEL` to `fastapi` service. |
| `verify/checks.sh` | Update `attr_quality)` layer: add V-ROUTE, V-OPENAPI, V-SDK, V-CODEGEN, score-range, provider-not-stub, idempotency checks. |

### Deleted files

| File | Reason | Deliverable |
|---|---|---|
| `dagster/tests/test_quality_tagger.py` | Tests symbols removed in D6 (`compute_quality_score`, `QUALITY_PROVIDER`); would cause `ImportError`. Replaced by D10. | D6 / D10 |

---

## 3. Deliverables

### D1 — NEW `apps/api/dataplat_api/llm/__init__.py`

**What:** Package init. Single exported symbol: `LLMGateway`.

```python
from dataplat_api.llm.gateway import LLMGateway

__all__ = ["LLMGateway"]
```

**Acceptance criteria:**
- `from dataplat_api.llm import LLMGateway` works with no side-effects.
- No other symbols imported at module level.

---

### D2 — NEW `apps/api/dataplat_api/llm/gateway.py`

**What:** `LLMGateway` class. Reads `ANTHROPIC_API_KEY` and `LLM_MODEL` from `os.environ`.
If `ANTHROPIC_API_KEY` is absent or empty, operates in **mock mode** — returns
`content="0.5"` and `model="mock"` without touching any Anthropic SDK code paths.

**Interface:**

```python
@dataclass
class LLMResponse:
    content: str     # raw text returned by the model (e.g. "0.85")
    model: str       # model name (e.g. "claude-3-haiku-20240307") or "mock"

class LLMGateway:
    def __init__(self) -> None: ...
    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 16,
    ) -> LLMResponse: ...
```

`messages` follows the Anthropic Messages API shape: `[{"role": "user", "content": "..."}]`.

**Mock mode logic:**

```python
if not os.environ.get("ANTHROPIC_API_KEY"):
    return LLMResponse(content="0.5", model="mock")
```

**Real mode logic:** Calls `anthropic.AsyncAnthropic().messages.create(...)` with the
configured model (`LLM_MODEL`, default `"claude-3-haiku-20240307"`), extracts
`response.content[0].text` as `content`, and the response `model` field as `model`.

**Singleton pattern (HIGH 2 fix):** Provide a module-level singleton factory:

```python
_gateway: LLMGateway | None = None

def get_llm_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
```

This ensures exactly one `AsyncAnthropic()` client instance is created for the lifetime of
the process — no resource leak under production load.

**Acceptance criteria:**
- Zero `import anthropic` statements outside this file (checked by D11 grep).
- `LLMGateway` is async (uses `anthropic.AsyncAnthropic`).
- Mock mode returns exactly `LLMResponse(content="0.5", model="mock")`.
- `LLM_MODEL` env var is read with default `"claude-3-haiku-20240307"`.
- No hardcoded API keys anywhere.
- No `session.query()` / sync SQLAlchemy (invariant #5; gateway has no DB access).
- `get_llm_gateway()` returns a module-level singleton (not per-request instantiation).

---

### D3 — NEW `apps/api/dataplat_api/llm/router.py`

**What:** FastAPI router. Exposes `POST /api/internal/llm/completions`.

**No authentication required** — this endpoint is only reachable within the Docker network
(Dagster → FastAPI inter-service). Do NOT add `Depends(current_user)` or any JWT check.

**Router MUST use `include_in_schema=False` at the router level (HIGH 1 fix):**

```python
router = APIRouter(prefix="/api/internal/llm", include_in_schema=False)
```

This is **required**, not optional. Do NOT use per-route `include_in_schema=False` — set it
on the `APIRouter` constructor itself.

**Request schema** (inline Pydantic model):

```python
class LLMCompletionRequest(BaseModel):
    messages: list[dict[str, str]]
    max_tokens: int = 16
```

**Response schema:**

```python
class LLMCompletionResponse(BaseModel):
    content: str
    model: str
```

**Route implementation:**

```python
@router.post("/completions", response_model=LLMCompletionResponse)
async def completions(
    body: LLMCompletionRequest,
    gateway: LLMGateway = Depends(get_llm_gateway),
):
    result = await gateway.complete(body.messages, body.max_tokens)
    return LLMCompletionResponse(content=result.content, model=result.model)
```

`get_llm_gateway` is the singleton factory from D2.

**Acceptance criteria:**
- `POST /api/internal/llm/completions` returns HTTP 200 with `{"content": "...", "model": "..."}`.
- No `Depends(current_user)` or Bearer JWT dependency.
- Router uses async handler (invariant #5).
- `APIRouter(prefix="/api/internal/llm", include_in_schema=False)` — mandatory.
- Schema does NOT appear in `packages/api-types/openapi.json`.
- `make codegen && git diff --exit-code packages/api-types/` produces empty diff.

---

### D4 — EDIT `apps/api/dataplat_api/main.py`

**What:** Register the llm router.

Add to imports:

```python
from dataplat_api.llm.router import router as llm_router
```

Add to router registration block (after existing routers):

```python
app.include_router(llm_router)
```

The prefix is already set on the router in D3 (`/api/internal/llm`).

**Acceptance criteria:**
- `GET /openapi.json` does NOT expose `/api/internal/llm/completions` in the public spec.
- App continues to pass `bash verify/checks.sh smoke`.
- `make codegen && git diff --exit-code packages/api-types/` exits 0.

---

### D5 — EDIT `apps/api/pyproject.toml`

**What:** Add Anthropic SDK dependency.

In `[project.dependencies]`, add:

```
"anthropic>=0.40.0",
```

Place it after existing `httpx` entry.

**Acceptance criteria:**
- `docker compose -f docker/docker-compose.dev.yml exec -T fastapi python -c "import anthropic; print('ok')"` exits 0.
- No upper bound on version (`>=0.40.0`, not `==`).
- No changes to any other packages in `pyproject.toml`.

---

### D6 — EDIT `dagster/dagster_platform/quality_tagger.py`

**What:** Replace the stub scorer with a real LLM-backed scorer that calls the internal
gateway. This is the primary logic change of F-028.

**Key changes:**

1. **Remove** `QUALITY_PROVIDER = "length_heuristic"` constant (provider is now dynamic).
2. **Remove** `compute_quality_score(token_count: int) -> float` stub function entirely.
3. **Add** `score_chunks_via_gateway(texts: list[str]) -> list[tuple[float, str]]`:
   - For each chunk text: POST `{"messages": [{"role": "user", "content": <scoring_prompt>}], "max_tokens": 16}` to `LLM_GATEWAY_URL/api/internal/llm/completions`.
   - Parse response JSON: `{"content": "0.85", "model": "claude-3-haiku-20240307"}`.
   - Parse `content` as `float`; clamp to `[0.0, 1.0]`.
   - Returns `list[tuple[float, str]]` — `(score, model_name)` per chunk.
   - Uses `requests.post(...)` — NOT `httpx` (Dagster image has `requests`, not `httpx`).
   - Reads `LLM_GATEWAY_URL` from `os.environ` with default `"http://fastapi:8000"`.
   - Must handle `requests.RequestException` and float parse errors gracefully (log and use
     `score=0.0, model="error"` for the failing chunk rather than aborting the entire batch).

4. **Scoring prompt template** (exact text; implementer must use this verbatim):

```
Rate the quality of the following text chunk on a scale from 0.0 to 1.0,
where 1.0 is high-quality, coherent, informative text and 0.0 is garbled,
empty, or meaningless content.

Text:
{chunk_text}

Respond with ONLY a single decimal number between 0.0 and 1.0. No explanation.
```

5. **Rename** `_option_b_update` → `_llm_update`:
   - Drop Option A entirely (SQL `values_sql` path removed — cannot call HTTP from SQL).
   - Change `select(["chunk_id", "token_count"])` → `select(["chunk_id", "text"])`.
   - Call `score_chunks_via_gateway([r["text"] for r in rows])` to get `(score, provider)` per row.
   - Resulting `scored` list: `[{"chunk_id": r["chunk_id"], "attr_quality_score": score, "attr_quality_provider": provider}]`.
   - **AMENDMENT (Mode B H1):** lancedb 0.30.2 does not support `when_matched_update_all(updates=[...])`
     — the `updates=` kwarg does not exist, and bare `when_matched_update_all()` replaces the
     entire row (destroying lineage fields). Use `table.update(where=f"chunk_id = '{id}'", values={...})`
     per row instead. This achieves the same column-mode partial update correctly.

6. **`update_quality_scores_in_lance()`** becomes simpler — no try/except for Option A:
   ```python
   table = db.open_table("chunks")
   where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
   _llm_update(table, source_id, where_clause)
   row_count = table.count_rows(where_clause)
   return row_count
   ```

7. **Delete** `dagster/tests/test_quality_tagger.py` — tests symbols removed above. D10 replaces it.

**No Dagster imports** — same guarantee as F-027. The `requests` import is the only new
third-party import.

**Acceptance criteria:**
- `grep -r "import anthropic\|import openai\|from anthropic\|from openai" dagster/` → no matches.
- `requests` is used for the HTTP call (not `httpx`, not `urllib`).
- `LLM_GATEWAY_URL` is read from env with fallback `"http://fastapi:8000"`.
- Mock mode (no `ANTHROPIC_API_KEY`) returns score 0.5, provider "mock" — verified by gateway
  not by the tagger (the tagger just parses whatever the gateway returns).
- Idempotent: re-running overwrites `attr_quality_score` and `attr_quality_provider`;
  row count unchanged; no new rows.
- `dagster/tests/test_quality_tagger.py` is **deleted** (not left in place).

---

### D7 — EDIT `dagster/dagster_platform/definitions.py`

**What:** Update the `attr_quality` asset description to reflect F-028 completion.

Change the `description=` string on `@asset` from:

```
"Quality tagger (F-027): … Uses a stub length-heuristic scorer: score = min(1.0, token_count / 512.0). "
"F-028 will replace the stub with a real LLM scorer once the gateway exists."
```

To:

```
"Quality tagger (F-028): updates attr_quality_score and attr_quality_provider "
"columns on existing producer_asset='chunks' rows in Lance. Zero new rows created. "
"Scores each chunk by calling the internal LLM gateway (POST /api/internal/llm/completions). "
"attr_quality_provider is set to the model name returned by the gateway (e.g. "
"'claude-3-haiku-20240307' or 'mock' in CI)."
```

Also verify `update_quality_scores_in_lance` is still the only import from `quality_tagger`.

**Acceptance criteria:**
- No references to "length_heuristic" or "F-028 will replace" remain in `definitions.py`.
- `update_quality_scores_in_lance` is still the only import from `quality_tagger`.
- `grep -rE "^(import anthropic|from anthropic|import openai|from openai)" dagster/` → no matches (NIT 2 fix).

---

### D8 — EDIT `docker/docker-compose.dev.yml`

**What:** Add three new env vars to all Dagster services (webserver, daemon, worker-cpu,
worker-heavy) and two to the fastapi service.

**For all four Dagster services** (`dagster-webserver`, `dagster-daemon`,
`dagster-worker-cpu`, `dagster-worker-heavy`), add to `environment:`:

```yaml
# F-028: LLM gateway URL (Dagster calls FastAPI internal endpoint).
LLM_GATEWAY_URL: ${LLM_GATEWAY_URL:-http://fastapi:8000}
# F-028: Anthropic API key — absent in CI → mock mode (score=0.5).
ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
# F-028: LLM model name forwarded to gateway. Gateway default is claude-3-haiku-20240307.
LLM_MODEL: ${LLM_MODEL:-claude-3-haiku-20240307}
```

**For the `fastapi` service**, add to `environment:`:

```yaml
# F-028: Anthropic SDK credentials for LLM gateway.
ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
LLM_MODEL: ${LLM_MODEL:-claude-3-haiku-20240307}
```

Note: `fastapi` does NOT need `LLM_GATEWAY_URL` — it IS the gateway.

**Acceptance criteria:**
- All four Dagster services have `LLM_GATEWAY_URL`, `ANTHROPIC_API_KEY`, `LLM_MODEL`.
- `fastapi` service has `ANTHROPIC_API_KEY`, `LLM_MODEL` (no `LLM_GATEWAY_URL`).
- Defaults for `ANTHROPIC_API_KEY` is empty string `""` — NOT a placeholder value — so that
  mock mode activates automatically in CI when the key is absent from the environment.
- `docker compose config` parses without errors.

---

### D9 — NEW `apps/api/tests/test_llm_gateway.py`

**What:** Unit tests for `LLMGateway`. Must NOT make real Anthropic API calls.

**Tests required:**

| Test | What it checks |
|---|---|
| `test_mock_mode_no_api_key` | `ANTHROPIC_API_KEY` unset → `content="0.5"`, `model="mock"` |
| `test_mock_mode_empty_api_key` | `ANTHROPIC_API_KEY=""` → same mock response |
| `test_real_mode_parses_response` | Anthropic client mocked via `unittest.mock.patch`; verifies `complete()` calls `messages.create()` and returns correct `LLMResponse` |
| `test_max_tokens_default` | Default `max_tokens=16` passed to Anthropic SDK |
| `test_model_from_env` | `LLM_MODEL="claude-3-opus-20240229"` env var respected |

All tests use `pytest` and `unittest.mock` / `pytest-mock`. No real network calls.

**Acceptance criteria:**
- `docker compose -f docker/docker-compose.dev.yml exec -T fastapi python -m pytest /app/tests/test_llm_gateway.py -q` exits 0.
- No real Anthropic API calls made (all mocked).

---

### D10 — NEW `dagster/tests/test_quality_tagger_llm.py`

**What:** Unit tests for the refactored `quality_tagger.py`. Must NOT make real HTTP calls.

**Tests required:**

| Test | What it checks |
|---|---|
| `test_score_via_gateway_mock_response` | Mock `requests.post` returns `{"content": "0.75", "model": "mock"}` → score=0.75, provider="mock" |
| `test_score_via_gateway_clamping_above_1` | Gateway returns `"1.5"` → clamped to 1.0 |
| `test_score_via_gateway_clamping_below_0` | Gateway returns `"-0.1"` → clamped to 0.0 |
| `test_score_via_gateway_parse_error` | Gateway returns `"error: ..."` → score=0.0, provider="error" (no exception raised) |
| `test_score_via_gateway_request_exception` | `requests.post` raises `requests.RequestException` → score=0.0, provider="error" per chunk |
| `test_gateway_url_from_env` | `LLM_GATEWAY_URL="http://custom:8000"` is used in POST URL |
| `test_llm_update_calls_update` | Mock `table.update(where=..., values=...)` call chain; assert called with correct chunk_id where clause and `{"attr_quality_score": ..., "attr_quality_provider": ...}` values dict (HIGH 3 fix; AMENDED: renamed from `test_llm_update_calls_merge_insert` due to lancedb 0.30.2 API limitation) |
| `test_update_quality_scores_no_new_rows` | Mock Lance `open_table` + `search().where().select().to_list()` returning 2 rows; mock `requests.post`; call `update_quality_scores_in_lance(src_id=42)`; assert `count_rows()` is called and no insert path triggered (HIGH 3 fix) |

All tests use `unittest.mock.patch("requests.post", ...)` and mock Lance table objects.

**Acceptance criteria:**
- `docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver python -m pytest /app/dagster/tests/test_quality_tagger_llm.py -q` exits 0.
- All tests pass without `ANTHROPIC_API_KEY` in the test environment.
- The old `test_quality_tagger.py` must be **deleted** (it tests symbols removed in D6). D10 is its replacement.

---

### D11 — EDIT `verify/checks.sh`

**What:** Update the `attr_quality)` layer to reflect F-028 checks.

**Rename test collection** from `test-attr-quality-f027` to `test-attr-quality-f028` (MEDIUM 2 fix).

**Changes to `attr_quality)` layer:**

1. **Unit tests** — update the pytest command from `test_quality_tagger.py` to
   `test_quality_tagger_llm.py`:
   ```bash
   docker compose -f "$COMPOSE" exec -T dagster-webserver \
     python -m pytest /app/dagster/tests/test_quality_tagger_llm.py -q || exit 1
   ```

2. **Gateway unit tests** — add FastAPI gateway test run:
   ```bash
   docker compose -f "$COMPOSE" exec -T fastapi \
     python -m pytest /app/tests/test_llm_gateway.py -q || exit 1
   ```

3. **V-SDK — No-direct-SDK-import check** (static, no container exec):
   ```bash
   echo "--- attr_quality: V-SDK — no direct LLM SDK import in quality_tagger.py ---"
   if grep -qE "^(import anthropic|from anthropic|import openai|from openai)" \
       dagster/dagster_platform/quality_tagger.py; then
     echo "FAIL V-SDK: direct LLM SDK import in quality_tagger.py" && exit 1
   fi
   echo "  V-SDK OK: no direct SDK import"
   ```

4. **V-ROUTE — automated curl** (NIT 1 fix):
   ```bash
   echo "--- attr_quality: V-ROUTE — POST /api/internal/llm/completions ---"
   ROUTE_RESP=$(curl -sf -X POST \
     "http://localhost:${FASTAPI_HOST_PORT}/api/internal/llm/completions" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"Rate: hello world"}],"max_tokens":4}')
   echo "$ROUTE_RESP" | python3 -c "
   import sys, json
   d = json.load(sys.stdin)
   assert 'content' in d and 'model' in d, f'missing fields: {d}'
   print(f'  V-ROUTE OK: content={d[\"content\"]!r} model={d[\"model\"]!r}')
   " || { echo "FAIL: V-ROUTE check failed"; exit 1; }
   ```

5. **V-OPENAPI — direct assertion** (BLOCKER 1 fix):
   ```bash
   echo "--- attr_quality: V-OPENAPI — internal route not in public spec ---"
   curl -sf "http://localhost:${FASTAPI_HOST_PORT}/openapi.json" | python3 -c "
   import sys, json
   spec = json.load(sys.stdin)
   assert '/api/internal/llm/completions' not in spec.get('paths', {}), \
       'FAIL V-OPENAPI: /api/internal/llm/completions leaked into public openapi.json'
   print('  V-OPENAPI OK: internal endpoint absent from public spec')
   " || { echo "FAIL: V-OPENAPI check failed"; exit 1; }
   ```

6. **V1 — POST /api/runs** (trigger attr_quality backfill, same pattern as before): HTTP 202.

7. **V2 — scores in [0.0, 1.0]** (uses `-e` env pattern per MEDIUM 1 fix):
   ```bash
   docker compose -f "$COMPOSE" exec -T \
     -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
     -e SRC_ID="${AQ_SRC_ID}" \
     fastapi python -c "
   import lancedb, os
   db = lancedb.connect('s3://lance/chunks', storage_options={
       'aws_access_key_id': os.environ['S3_USER'],
       'aws_secret_access_key': os.environ['S3_PASS'],
       'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
   tbl = db.open_table('chunks')
   src_id = int(os.environ['SRC_ID'])
   rows = tbl.search().where(f'source_id = {src_id} AND producer_asset = \'chunks\'').select(['attr_quality_score']).to_list()
   bad = [r for r in rows if r['attr_quality_score'] is None or not (0.0 <= r['attr_quality_score'] <= 1.0)]
   assert not bad, f'FAIL V2: {len(bad)} rows have out-of-range score'
   print(f'  V2 OK: all {len(rows)} scores in [0.0, 1.0]')
   " || exit 1
   ```

8. **V3 — provider is NOT "length_heuristic"**:
   ```bash
   docker compose -f "$COMPOSE" exec -T \
     -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
     -e SRC_ID="${AQ_SRC_ID}" \
     fastapi python -c "
   import lancedb, os
   db = lancedb.connect('s3://lance/chunks', storage_options={
       'aws_access_key_id': os.environ['S3_USER'],
       'aws_secret_access_key': os.environ['S3_PASS'],
       'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
   tbl = db.open_table('chunks')
   src_id = int(os.environ['SRC_ID'])
   rows = tbl.search().where(f'source_id = {src_id} AND producer_asset = \'chunks\'').select(['attr_quality_provider']).to_list()
   stub_rows = [r for r in rows if r.get('attr_quality_provider') == 'length_heuristic']
   assert not stub_rows, f'FAIL V3: {len(stub_rows)} rows still have stub provider'
   providers = {r['attr_quality_provider'] for r in rows}
   print(f'  V3 OK: providers = {providers}')
   " || exit 1
   ```

9. **V4 — idempotency: second run does not change row count** (BLOCKER 2 fix — full implementation):
   ```bash
   echo "--- attr_quality: V4 — idempotency: second run does not change row count ---"
   # Capture row count before second run
   AQ_RC_BEFORE=$(docker compose -f "$COMPOSE" exec -T \
     -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
     -e SRC_ID="${AQ_SRC_ID}" \
     fastapi python -c "
   import lancedb, os
   db = lancedb.connect('s3://lance/chunks', storage_options={
       'aws_access_key_id': os.environ['S3_USER'],
       'aws_secret_access_key': os.environ['S3_PASS'],
       'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
   t = db.open_table('chunks')
   print(t.count_rows(f\"source_id = {int(os.environ['SRC_ID'])} AND producer_asset = 'chunks'\"))
   " | tr -d '[:space:]')

   # Re-trigger attr_quality backfill (same POST + poll pattern as V1)
   AQ_RESP2=$(curl -sf -X POST "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
     -H "Content-Type: application/json" -H "Authorization: Bearer ${TOKEN}" \
     -d "{\"source_ids\":[${AQ_SRC_ID}],\"asset\":\"attr_quality\"}")
   AQ_RUN_ID2=$(echo "$AQ_RESP2" | python3 -c "import sys,json; print(json.load(sys.stdin)['dagster_run_id'])")
   # Poll until COMPLETED_SUCCESS (same helper as V1)
   for i in $(seq 1 30); do
     AQ_STATUS2=$(curl -sf "http://localhost:${DAGSTER_HOST_PORT}/graphql" \
       -H "Content-Type: application/json" \
       -d "{\"query\":\"{ runOrError(runId: \\\"${AQ_RUN_ID2}\\\") { ... on Run { status } } }\"}" \
       | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['runOrError']['status'])")
     [ "$AQ_STATUS2" = "SUCCESS" ] && break
     sleep 2
   done
   [ "$AQ_STATUS2" = "SUCCESS" ] || { echo "FAIL V4: second run status=$AQ_STATUS2"; exit 1; }

   # Assert row count unchanged
   AQ_RC_AFTER=$(docker compose -f "$COMPOSE" exec -T \
     -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
     -e SRC_ID="${AQ_SRC_ID}" \
     fastapi python -c "
   import lancedb, os
   db = lancedb.connect('s3://lance/chunks', storage_options={
       'aws_access_key_id': os.environ['S3_USER'],
       'aws_secret_access_key': os.environ['S3_PASS'],
       'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
   t = db.open_table('chunks')
   print(t.count_rows(f\"source_id = {int(os.environ['SRC_ID'])} AND producer_asset = 'chunks'\"))
   " | tr -d '[:space:]')

   test "$AQ_RC_BEFORE" = "$AQ_RC_AFTER" \
     || { echo "FAIL V4: row count changed $AQ_RC_BEFORE → $AQ_RC_AFTER (rows were inserted)"; exit 1; }
   echo "  V4 OK: row count unchanged at $AQ_RC_AFTER after second run"
   ```

10. **Comment** explaining mock mode: provider will be `"mock"` in CI when
    `ANTHROPIC_API_KEY` is absent. This is expected and V3 accepts it (only rejects
    `"length_heuristic"`).

**Acceptance criteria:**
- `bash verify/checks.sh attr_quality` exits 0 in CI (with no `ANTHROPIC_API_KEY` → mock mode).
- V3 explicitly rejects `"length_heuristic"` as the provider.
- V-SDK grep check is static (no container exec needed).
- V-OPENAPI directly verifies internal endpoint absence from openapi.json (not a proxy).
- V4 is fully executable (not a description-only step).
- Collection named `test-attr-quality-f028` (not reusing F-027 name).

---

## 4. Verification Plan

End-to-end verification sequence (order matters):

```
Step 1: docker compose -f docker/docker-compose.dev.yml build fastapi dagster-webserver
Step 2: docker compose -f docker/docker-compose.dev.yml up -d
Step 3: bash verify/checks.sh smoke          # baseline must still pass
Step 4: make codegen && git diff --exit-code packages/api-types/   # invariant #6
Step 5: bash verify/checks.sh backend        # ruff/mypy/pytest all pass
Step 6: bash verify/checks.sh attr_quality   # F-028 acceptance gate
```

**Criterion mapping:**

| ID | Criterion | How verified |
|---|---|---|
| V-SDK | No direct SDK import in tagger | Static `grep` in checks.sh (D11 item 3) |
| V-UNIT-TAGGER | Tagger unit tests pass (mock requests) | `test_quality_tagger_llm.py` in checks.sh (D11 item 1) |
| V-UNIT-GW | Gateway unit tests pass | `test_llm_gateway.py` in checks.sh (D11 item 2) |
| V-ROUTE | `POST /api/internal/llm/completions` returns 200 | Automated curl in checks.sh (D11 item 4) |
| V-OPENAPI | Internal endpoint not in public openapi.json | Direct assertion in checks.sh (D11 item 5) |
| V-CODEGEN | `make codegen` produces empty diff | Step 4 above; `make codegen && git diff --exit-code packages/api-types/` |
| V-202 | `POST /api/runs {"asset":"attr_quality"}` returns 202 | checks.sh V1 (D11 item 6) |
| V-SCORE | All `attr_quality_score` in [0.0, 1.0] | checks.sh V2 (D11 item 7) |
| V-PROV | `attr_quality_provider` != "length_heuristic" | checks.sh V3 (D11 item 8) |
| V-IDEM | Second run: same row count, no duplication | checks.sh V4 (D11 item 9, full implementation) |

---

## 5. Design Decisions

### D-A — Two-layer architecture (invariant #4 compliance)

Hard invariant #4 prohibits calling Anthropic/OpenAI SDKs directly from a processor,
adapter, or random route. The `quality_tagger.py` module lives in the Dagster layer and
is therefore a processor helper. All SDK calls must stay in `apps/api/dataplat_api/llm/`.

The Dagster tagger calls `POST /api/internal/llm/completions` via `requests`. The FastAPI
gateway calls `anthropic.AsyncAnthropic().messages.create(...)`. This two-layer design
satisfies invariant #4 with a clear enforcement boundary.

### D-B — Mock mode via empty `ANTHROPIC_API_KEY`

CI environments do not have Anthropic API keys. Rather than skipping the check or using
a separate test mode flag, the gateway auto-detects mock mode by checking if `ANTHROPIC_API_KEY`
is absent or empty. This avoids adding a separate `LLM_MOCK_MODE=true` env var and keeps
the mock path fully exercised by the standard `attr_quality)` checks.sh layer.

### D-C — `requests` not `httpx` in the Dagster layer

The Dagster Docker image has `requests` installed (as a transitive dependency of many
packages) but does NOT have `httpx`. Since Dagster code does not share the FastAPI
virtualenv, only `requests` is reliably available in `quality_tagger.py`.

### D-D — Drop Option A SQL path entirely

The F-027 `update_quality_scores_in_lance()` tried Option A (SQL `values_sql`) first and
fell back to Option B. In F-028, LLM scoring requires reading each chunk's `text` field
from Lance into Python before calling the gateway — SQL cannot invoke an HTTP endpoint.
Option A is therefore impossible for LLM-based scoring and is removed. The function
becomes a direct call to `_llm_update()` (renamed from `_option_b_update`).

### D-E — Per-chunk HTTP calls (batching deferred)

F-028 calls the gateway once per chunk. This is simple and correct but may be slow for
large sources (e.g., 200 chunks = 200 HTTP round-trips). Batching is deferred to a
future feature. For MVP the per-chunk approach is acceptable given typical source sizes.

### D-F — `attr_quality_provider` from gateway response `model` field

F-027 used a hardcoded `"length_heuristic"` constant. F-028 sets `attr_quality_provider`
to the `model` field returned by the gateway. In mock mode this is `"mock"`. In
production this is the actual Anthropic model name (e.g., `"claude-3-haiku-20240307"`).
This provides auditability — each chunk row records which model scored it.

### D-G — Internal endpoint excluded from public OpenAPI spec

`/api/internal/llm/completions` is an inter-service endpoint not intended for external
clients. It **must** use `APIRouter(prefix=..., include_in_schema=False)` at the router
level — not per-route, not optional. This avoids triggering invariant #6 (`make codegen`
requirement) since no public schema changes. V-OPENAPI check enforces this directly.

### D-H — Old `test_quality_tagger.py` deleted

The F-027 test file (`dagster/tests/test_quality_tagger.py`) tests `compute_quality_score`
and `QUALITY_PROVIDER`, both of which are removed in D6. The file must be deleted to
prevent `ImportError`/`AttributeError` failures. D10 (`test_quality_tagger_llm.py`) is
its replacement with equivalent-or-better coverage of the Lance update path.

### D-I — Module-level singleton for LLMGateway (HIGH 2 fix)

`get_llm_gateway()` returns a module-level singleton — NOT a per-request factory. This
ensures exactly one `AsyncAnthropic()` client instance is created for the lifetime of the
FastAPI process. Under the D6 per-chunk calling pattern (~200 HTTP calls per source),
per-request instantiation would create 200 `AsyncAnthropic()` clients per source
materialization — a resource leak (file descriptors, connection pools, TLS state).

---

## 6. Risk / Open Questions

### R1 — `requests` availability in Dagster image (LOW)
**Risk:** `requests` may not be in the Dagster image's virtualenv.
**Mitigation:** `requests` is a transitive dep of `dagster` itself and is confirmed present.
Implementer should verify with `docker compose exec dagster-webserver python -c "import requests; print(requests.__version__)"` before writing code.

### R2 — Internal endpoint in public OpenAPI spec (MEDIUM → MITIGATED)
**Risk:** If `include_in_schema=False` is not set, the internal endpoint leaks.
**Mitigation:** D3 requires `APIRouter(include_in_schema=False)` at router level (mandatory).
D11 V-OPENAPI directly asserts absence. V-CODEGEN verifies `make codegen` empty diff.

### R3 — LLM response parsing failures in production (LOW)
**Risk:** The LLM occasionally returns non-numeric text.
**Mitigation:** D6 requires graceful handling: parse errors → `score=0.0, provider="error"`.

### R4 — Old test file deletion (LOW → MITIGATED)
**Risk:** Deleting `test_quality_tagger.py` removes F-027 test coverage.
**Mitigation:** D10 tests the full Lance update path (`_llm_update` + `update_quality_scores_in_lance`) via mocked `requests.post` (HIGH 3 fix). 8 tests provide equivalent-or-better coverage.

### R5 — Per-chunk latency (ACCEPTED)
**Risk:** 200 chunks × ~500ms per Anthropic call = ~100s for a single source.
**Mitigation:** In mock mode (CI), calls are instant. Dagster op timeout should be noted
in a code comment. This is a known limitation; batching deferred.

### R6 — Anthropic SDK version compatibility (LOW)
**Risk:** `anthropic>=0.40.0` may introduce breaking changes.
**Mitigation:** `LLMGateway` wraps only `messages.create()` — narrow interface.
