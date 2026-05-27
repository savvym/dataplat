# Sprint S028-F-028 — Proposed Contract

> Feature: `quality_gpt4` — Replace stub length-heuristic scorer in `attr_quality` with a real
> LLM-backed scorer routed through an internal FastAPI LLM gateway.

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
| `apps/api/dataplat_api/llm/gateway.py` | `LLMGateway` — wraps Anthropic SDK; mock mode when `ANTHROPIC_API_KEY` absent. |
| `apps/api/dataplat_api/llm/router.py` | `POST /api/internal/llm/completions` — no auth; bridges HTTP → `LLMGateway`. |
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
| `verify/checks.sh` | Update `attr_quality)` layer: drop "length_heuristic" check; add score-range, provider-not-stub, idempotency, and no-direct-SDK-import checks. |

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

**Acceptance criteria:**
- Zero `import anthropic` statements outside this file (checked by D11 grep).
- `LLMGateway` is async (uses `anthropic.AsyncAnthropic`).
- Mock mode returns exactly `LLMResponse(content="0.5", model="mock")`.
- `LLM_MODEL` env var is read with default `"claude-3-haiku-20240307"`.
- No hardcoded API keys anywhere.
- No `session.query()` / sync SQLAlchemy (invariant #5; gateway has no DB access).

---

### D3 — NEW `apps/api/dataplat_api/llm/router.py`

**What:** FastAPI router. Exposes `POST /api/internal/llm/completions`.

**No authentication required** — this endpoint is only reachable within the Docker network
(Dagster → FastAPI inter-service). Do NOT add `Depends(current_user)` or any JWT check.

**Request schema** (inline Pydantic model, no openapi.json update needed — internal endpoint):

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
async def completions(body: LLMCompletionRequest, gateway: LLMGateway = Depends(...)):
    result = await gateway.complete(body.messages, body.max_tokens)
    return LLMCompletionResponse(content=result.content, model=result.model)
```

`LLMGateway` is provided via `Depends`. Prefer a simple factory dependency rather than a
global singleton, e.g. `def get_llm_gateway() -> LLMGateway: return LLMGateway()`.

**Prefix:** The router is mounted at `/api/internal/llm` in `main.py` (D4), so the full
path is `/api/internal/llm/completions`.

**Acceptance criteria:**
- `POST /api/internal/llm/completions` returns HTTP 200 with `{"content": "...", "model": "..."}`.
- No `Depends(current_user)` or Bearer JWT dependency.
- Router uses async handler (invariant #5).
- Schema does NOT appear in `packages/api-types/openapi.json` — internal endpoint is excluded
  from the public OpenAPI spec (see invariant #6 scope note in Risk section).

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

**Router prefix:** The `llm_router` should be defined with `prefix="/api/internal/llm"` in
`router.py` itself (or passed here), so the full path is `/api/internal/llm/completions`.

**Acceptance criteria:**
- `GET /openapi.json` does NOT expose `/api/internal/llm/completions` in the public spec
  (tag with `include_in_schema=False` on the router or individual routes if needed; see R2).
- App continues to pass `bash verify/checks.sh smoke`.

---

### D5 — EDIT `apps/api/pyproject.toml`

**What:** Add Anthropic SDK dependency.

In `[project.dependencies]`, add:

```
"anthropic>=0.40.0",
```

Place it after existing `httpx` entry (alphabetical or by function — either is acceptable).

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

4. **Scoring prompt template** (exact text agreed upon here; implementer must use this verbatim):

```
Rate the quality of the following text chunk on a scale from 0.0 to 1.0,
where 1.0 is high-quality, coherent, informative text and 0.0 is garbled,
empty, or meaningless content.

Text:
{chunk_text}

Respond with ONLY a single decimal number between 0.0 and 1.0. No explanation.
```

5. **Update `_option_b_update()`:**
   - Drop Option A entirely (SQL `values_sql` path is removed — cannot call HTTP from SQL).
   - Change `select(["chunk_id", "token_count"])` → `select(["chunk_id", "text"])`.
   - Call `score_chunks_via_gateway([r["text"] for r in rows])` to get `(score, provider)` per row.
   - Resulting `scored` list: `[{"chunk_id": r["chunk_id"], "attr_quality_score": score, "attr_quality_provider": provider}]`.
   - `merge_insert("chunk_id").when_matched_update_all(updates=["attr_quality_score", "attr_quality_provider"]).execute(scored)`.

6. **Rename** `_option_b_update` → `_llm_update` (the "fallback" framing no longer applies;
   LLM read-back is the only path). Update call site in `update_quality_scores_in_lance()`.

7. **`update_quality_scores_in_lance()`** becomes simpler — no try/except for Option A:
   ```python
   table = db.open_table("chunks")
   where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
   _llm_update(table, source_id, where_clause)
   row_count = table.count_rows(where_clause)
   return row_count
   ```

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

Also update the imports from `quality_tagger.py` if `compute_quality_score` or
`QUALITY_PROVIDER` were imported (they were not — `definitions.py` only imports
`update_quality_scores_in_lance`). Verify the import block is still correct after D6 renames.

**Acceptance criteria:**
- No references to "length_heuristic" or "F-028 will replace" remain in `definitions.py`.
- `update_quality_scores_in_lance` is still the only import from `quality_tagger`.

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

All tests use `unittest.mock.patch("requests.post", ...)`.

**Acceptance criteria:**
- `docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver python -m pytest /app/dagster/tests/test_quality_tagger_llm.py -q` exits 0.
- All tests pass without `ANTHROPIC_API_KEY` in the test environment.
- The old `test_quality_tagger.py` (F-027 stub tests) is left unchanged — those tests should
  now FAIL (since `compute_quality_score` no longer exists). The old file is **deleted** as
  part of D6; D10 is its replacement. See R4.

---

### D11 — EDIT `verify/checks.sh`

**What:** Update the `attr_quality)` layer to reflect F-028 checks. The F-027 V3 check
(`attr_quality_provider == 'length_heuristic'`) becomes a **failure condition** in F-028.

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

3. **No-direct-SDK-import check** (static, V-SDK):
   ```bash
   # V-SDK: quality_tagger.py must not import anthropic/openai directly
   if grep -qE "^(import anthropic|from anthropic|import openai|from openai)" \
       dagster/dagster_platform/quality_tagger.py; then
     echo "FAIL V-SDK: direct LLM SDK import in quality_tagger.py" && exit 1
   fi
   echo "PASS V-SDK: no direct SDK import"
   ```

4. **V1 — POST /api/runs** (unchanged from F-027): HTTP 202, has `dagster_run_id`.

5. **V2 — scores in [0.0, 1.0]** (replaces old V2 non-null check):
   ```bash
   docker compose -f "$COMPOSE" exec -T fastapi python -c "
   import lancedb, os
   db = lancedb.connect('s3://' + os.environ['MINIO_LANCE_BUCKET'] + '/chunks', storage_options={...})
   tbl = db.open_table('chunks')
   rows = tbl.search().where('source_id = $SRC_ID').to_list()
   bad = [r for r in rows if r['attr_quality_score'] is None or not (0.0 <= r['attr_quality_score'] <= 1.0)]
   assert not bad, f'FAIL V2: {len(bad)} rows have out-of-range score'
   print('PASS V2: all scores in [0.0, 1.0]')
   " || exit 1
   ```

6. **V3 — provider is NOT "length_heuristic"** (F-028 invariant):
   ```bash
   docker compose -f "$COMPOSE" exec -T fastapi python -c "
   ...
   stub_rows = [r for r in rows if r.get('attr_quality_provider') == 'length_heuristic']
   assert not stub_rows, f'FAIL V3: {len(stub_rows)} rows still have stub provider'
   providers = {r['attr_quality_provider'] for r in rows}
   print(f'PASS V3: providers = {providers}')
   " || exit 1
   ```

7. **V4 — idempotency check** (second run does not change row count):
   - Re-trigger the backfill.
   - Poll to `COMPLETED_SUCCESS`.
   - Query row count again; assert it equals the first run's count.

8. **Comment** explaining mock mode: provider will be `"mock"` in CI when
   `ANTHROPIC_API_KEY` is absent. This is expected and V3 will accept it (it only rejects
   `"length_heuristic"`).

**Acceptance criteria:**
- `bash verify/checks.sh attr_quality` exits 0 in CI (with no `ANTHROPIC_API_KEY` → mock mode).
- V3 explicitly rejects `"length_heuristic"` as the provider.
- V-SDK grep check is static (no container exec needed).

---

## 4. Verification Plan

End-to-end verification sequence (order matters):

```
Step 1: docker compose -f docker/docker-compose.dev.yml build fastapi dagster-webserver
Step 2: docker compose -f docker/docker-compose.dev.yml up -d
Step 3: bash verify/checks.sh smoke          # baseline must still pass
Step 4: bash verify/checks.sh extract_mineru  # ensure prereqs work
Step 5: bash verify/checks.sh chunks         # ensure Lance has chunk rows with text field
Step 6: bash verify/checks.sh attr_quality   # F-028 acceptance gate
```

**Criterion mapping:**

| ID | Criterion | How verified |
|---|---|---|
| V-SDK | No direct SDK import in tagger | Static `grep` in checks.sh D11, step 3 |
| V-UNIT-TAGGER | Tagger unit tests pass (mock requests) | `test_quality_tagger_llm.py` in checks.sh |
| V-UNIT-GW | Gateway unit tests pass | `test_llm_gateway.py` in checks.sh |
| V-MOCK | CI with no API key returns score=0.5, provider="mock" | checks.sh V2+V3 pass with empty `ANTHROPIC_API_KEY` |
| V-ROUTE | `POST /api/internal/llm/completions` returns 200 | Manual curl or gateway unit test |
| V-202 | `POST /api/runs {"asset":"attr_quality"}` returns 202 | checks.sh V1 |
| V-SCORE | All `attr_quality_score` in [0.0, 1.0] | checks.sh V2 |
| V-PROV | `attr_quality_provider` != "length_heuristic" | checks.sh V3 |
| V-IDEM | Second run: same row count, no duplication | checks.sh V4 |
| V-OPENAPI | Public openapi.json unchanged (internal route excluded) | `bash verify/checks.sh runs` V2/V5 still pass |

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
large sources (e.g., 200 chunks = 200 HTTP round-trips). Batching is deferred to F-029.
For MVP the per-chunk approach is acceptable given typical source sizes.

### D-F — `attr_quality_provider` from gateway response `model` field

F-027 used a hardcoded `"length_heuristic"` constant. F-028 sets `attr_quality_provider`
to the `model` field returned by the gateway. In mock mode this is `"mock"`. In
production this is the actual Anthropic model name (e.g., `"claude-3-haiku-20240307"`).
This provides auditability — each chunk row records which model scored it.

### D-G — Internal endpoint excluded from public OpenAPI spec

`/api/internal/llm/completions` is an inter-service endpoint not intended for external
clients. It must be excluded from `packages/api-types/openapi.json` (use
`include_in_schema=False` on the router or route). This avoids triggering invariant #6
(`make codegen` requirement) since no public schema changes.

### D-H — Old `test_quality_tagger.py` deleted

The F-027 test file (`dagster/tests/test_quality_tagger.py`) tests `compute_quality_score`
and `QUALITY_PROVIDER`, both of which are removed in D6. The file must be deleted to
prevent misleading test failures. D10 (`test_quality_tagger_llm.py`) is its replacement.

---

## 6. Risk / Open Questions

### R1 — `requests` availability in Dagster image (LOW)
**Risk:** `requests` may not be in the Dagster image's virtualenv.
**Mitigation:** `requests` is a transitive dep of `dagster` itself and is confirmed present.
Implementer should verify with `docker compose exec dagster-webserver python -c "import requests; print(requests.__version__)"` before writing code.

### R2 — Internal endpoint in public OpenAPI spec (MEDIUM)
**Risk:** If `include_in_schema=False` is not set on the llm router/route, `make codegen`
will regenerate `packages/api-types/openapi.json` with the new internal endpoint, which
may break invariant #6 (openapi.json diff must be committed in the same commit as schema
change, but we don't want this endpoint in the public spec at all).
**Mitigation:** D3 explicitly requires `include_in_schema=False`. D11 checks.sh V-OPENAPI
confirms the runs checks still pass (proxy for "no new public routes broke anything").
Implementer must run `make codegen` and verify the diff is empty before committing.

### R3 — LLM response parsing failures in production (LOW)
**Risk:** The LLM occasionally returns non-numeric text (e.g., "I cannot rate this.").
**Mitigation:** D6 requires graceful handling: parse errors → `score=0.0, provider="error"`.
The asset will complete successfully; the row will have a valid (if inaccurate) score.
F-029 will add retry logic if needed.

### R4 — Old test file deletion (LOW)
**Risk:** Deleting `test_quality_tagger.py` removes F-027 test coverage of the Lance update
logic path shared with F-028.
**Mitigation:** D10 `test_quality_tagger_llm.py` must test the full Lance update path
(including `_llm_update`) via mocked `requests.post`, not just the scoring function.
The reviewer should check that D10 achieves equivalent or better coverage.

### R5 — Per-chunk latency (ACCEPTED)
**Risk:** 200 chunks × ~500ms per Anthropic call = ~100s for a single source. Backfill
will time out in Dagster if the default op timeout is shorter than this.
**Mitigation:** In mock mode (CI), calls are instant. In production, the Dagster op timeout
should be set to 600s for `attr_quality`. This is a known limitation deferred to F-029
(batching). Implementer should note the timeout in a code comment.

### R6 — Anthropic SDK version compatibility (LOW)
**Risk:** `anthropic>=0.40.0` may introduce breaking changes in future minor versions.
**Mitigation:** The `LLMGateway` wraps only the `messages.create()` call with a narrow
interface. If the SDK changes, only `gateway.py` needs updating.

### Open Question OQ-1
Should the internal LLM endpoint support provider-agnostic routing (Anthropic/OpenAI/
local) from the start, or is Anthropic-only sufficient for F-028?
**Proposed answer:** Anthropic-only for F-028. Provider abstraction is tracked separately.

### Open Question OQ-2
Should `attr_quality_provider` include the full model version string (e.g.,
`"claude-3-haiku-20240307"`) or a normalized alias (e.g., `"claude-3-haiku"`)?
**Proposed answer:** Use the raw `model` string from the Anthropic response. This preserves
full auditability. Normalization can be added in F-029 if needed.
