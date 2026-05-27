# S028-F-028 Feedback (Mode A)

## Verdict: CHANGES_REQUESTED

---

## Findings

### [BLOCKER 1] V-OPENAPI verification is a proxy, not a direct assertion

**Location:** §4 verification plan, row V-OPENAPI.

**Problem:** The criterion reads:

> `V-OPENAPI | Public openapi.json unchanged (internal route excluded) | bash verify/checks.sh runs V2/V5 still pass`

"runs V2/V5 still pass" means the existing API endpoint checks pass — this is not the same as
verifying that `GET /openapi.json` excludes `/api/internal/llm/completions`. If
`include_in_schema=False` is accidentally omitted, the existing `runs` checks would still
pass (they test different routes), the proxy criterion would show green, and the internal
endpoint would silently appear in the public spec — violating invariant #6 and defeating the
entire purpose of D4.

**Required fix:** Replace the proxy with a direct assertion in D11:

```bash
echo "--- attr_quality: V-OPENAPI — internal route not in public spec ---"
SPEC_BODY=$(curl -sf "http://localhost:${FASTAPI_HOST_PORT}/openapi.json")
if echo "$SPEC_BODY" | python3 -c "
import sys, json
spec = json.load(sys.stdin)
assert '/api/internal/llm/completions' not in spec.get('paths', {}), \
    'FAIL V-OPENAPI: /api/internal/llm/completions leaked into public openapi.json'
print('  V-OPENAPI OK: internal endpoint absent from public spec')
"; then
    true
else
    echo "FAIL: V-OPENAPI check failed"; exit 1
fi
```

Additionally, add to D11 acceptance criteria: `make codegen && git diff --exit-code
packages/api-types/` exits 0 (direct enforcement of invariant #6). This step must also
appear in the §4 verification plan.

---

### [BLOCKER 2] V4 idempotency check has no executable implementation

**Location:** D11, item 7 (V4 idempotency).

**Problem:** D11 item 7 describes V4 behaviorally:

> - Re-trigger the backfill.
> - Poll to `COMPLETED_SUCCESS`.
> - Query row count again; assert it equals the first run's count.

No bash or Python code is provided. Every other check in `checks.sh` has a complete
executable implementation. The sprint workflow requires `verifier` to run `checks.sh`
mechanically — "description only" items cannot be verified by the verifier without
becoming manual steps, which defeats the CI gate.

V4 is listed as verification criterion #4 in `feature_list.json`: "Running the tagger a
second time does not fail; it overwrites the column values." Leaving it without code means
F-028 cannot be declared `passes: true` from `checks.sh` output alone.

**Required fix:** D11 must provide a full V4 implementation. Concretely:

```bash
echo "--- attr_quality: V4 — idempotency: second run does not change row count ---"
# Capture row count before second run
AQ_RC_BEFORE=$(docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" -e SRC_ID="${AQ_SRC_ID}" \
  fastapi python -c "
import lancedb, os
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
t = db.open_table('chunks')
print(t.count_rows(f\"source_id = {int(os.environ['SRC_ID'])} AND producer_asset = 'chunks'\"))
" | tr -d '[:space:]')

# Re-trigger attr_quality backfill (same pattern as V1)
# ... [same backfill POST + poll loop as first run] ...

# Assert row count unchanged
AQ_RC_AFTER=$(... same query ...)
test "$AQ_RC_BEFORE" = "$AQ_RC_AFTER" \
  || { echo "FAIL V4: row count changed $AQ_RC_BEFORE → $AQ_RC_AFTER (rows were inserted)"; exit 1; }
echo "  V4 OK: row count unchanged at $AQ_RC_AFTER after second run"
```

The exact backfill re-trigger mechanism (Dagster REST `reportRunlessAssetEvents` or
GraphQL `launchPartitionBackfill`) must be pinned — same approach used in the first run.

---

### [BLOCKER 3] `test_quality_tagger.py` deletion is contradictory and missing from §2 files table

**Location:** D10 acceptance criteria (lines that say "left unchanged"), D-H design note,
§2 "Modified files" table.

**Problem — two sub-issues:**

**3a. Self-contradictory language.** D10 acceptance criteria states:

> "The old `test_quality_tagger.py` (F-027 stub tests) is **left unchanged** — those tests
> should now FAIL. The old file is **deleted** as part of D6; D10 is its replacement."

"Left unchanged" and "deleted" are mutually exclusive. An implementer reading this could
reasonably leave the file in place (following "left unchanged"). The intent (deletion) is
clear from D-H and R4, but the note in D10 contradicts them.

**3b. Deletion absent from §2 files table.** The §2 "Modified files" table lists
`dagster/dagster_platform/quality_tagger.py` but does NOT include
`dagster/tests/test_quality_tagger.py` as a deletion. The §2 table is the implementer's
authoritative change checklist. If a file change is not in the table, it gets missed.

**Consequence:** If the old test file remains, any test runner broader than the specific
`test_quality_tagger_llm.py` command (e.g., `pytest dagster/tests/`) will hit
`compute_quality_score` and `QUALITY_PROVIDER` — symbols removed in D6 — and fail with
`ImportError` or `AttributeError`. CI will be broken.

**Required fix:**

1. Remove the "left unchanged" sentence from D10. Replace with:
   > "The old `test_quality_tagger.py` must be **deleted** (it tests symbols removed in D6).
   > D10 is its replacement."

2. Add to §2 "Modified files" (or a new "Deleted files") table:

   | File | Change | Deliverable |
   |---|---|---|
   | `dagster/tests/test_quality_tagger.py` | **DELETED** | D6 / D10 |
   | `dagster/tests/test_quality_tagger_llm.py` | new | D10 |

---

### [HIGH 1] `include_in_schema=False` placement is ambiguous; `make codegen` not in verification plan

**Location:** D3 acceptance criteria, D4 acceptance criteria, D-G design note, §4
verification plan.

**Problem — two sub-issues:**

**1a. "Or individual routes if needed" leaves a gap.** D3 says "tag with
`include_in_schema=False` on the router or individual routes if needed." D4 repeats "tag
with `include_in_schema=False` on the router or individual routes if needed." D-G repeats
the same. The "or individual routes" escape hatch means an implementer could set
`include_in_schema=False` only on the POST handler but forget the router, or set it on
neither and argue "I'll add it if needed." The correct and unambiguous placement is:

```python
# router.py
router = APIRouter(prefix="/api/internal/llm", include_in_schema=False)
```

This must be required, not optional.

**1b. `make codegen` empty-diff is only in R2 prose.** Invariant #6 requires `make codegen`
after any API schema change, with the resulting diff committed in the same commit. R2
(Risk section) says "Implementer must run `make codegen` and verify the diff is empty
before committing" — but this appears only in the Risk prose. It is NOT listed as a step
in the §4 verification plan and NOT mapped to any check in `checks.sh`. It is therefore
advisory, not enforced.

**Required fix:**
- D3 / D4 / D-G: change to "**must** use `APIRouter(prefix=..., include_in_schema=False)`
  at the router level — not per-route, not optional."
- §4 verification plan: add:

  | ID | Criterion | How verified |
  |---|---|---|
  | V-CODEGEN | `make codegen` produces empty diff in `packages/api-types/` | `make codegen && git diff --exit-code packages/api-types/` |

- D11 acceptance criteria: "V-OPENAPI direct assertion (BLOCKER 1 fix) + `make codegen`
  empty diff both pass."

---

### [HIGH 2] `LLMGateway` creates a new `AsyncAnthropic()` client per HTTP request

**Location:** D3, `get_llm_gateway()` dependency definition.

**Problem:** D3 specifies:

```python
def get_llm_gateway() -> LLMGateway:
    return LLMGateway()
```

and D2 shows `LLMGateway.__init__` creates `anthropic.AsyncAnthropic()`. FastAPI's
`Depends` calls this factory **once per HTTP request**. Under the D6 per-chunk calling
pattern (~200 HTTP calls per source), this creates 200 new `AsyncAnthropic()` client
instances per source materialization — each with its own `httpx.AsyncClient`, connection
pool, and TLS handshake state. This is a resource leak: file descriptors and memory
accumulate until GC runs, and `httpx` connection pools that never cleanly close can leave
sockets in `TIME_WAIT`. This is not acceptable for a production code path.

**Required fix:** Use a module-level singleton with lazy initialisation:

```python
# gateway.py (or router.py)
_gateway: LLMGateway | None = None

def get_llm_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
```

Or, preferably, use FastAPI `lifespan` to control client lifecycle:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm_gateway = LLMGateway()
    yield
    # optionally: await app.state.llm_gateway._client.aclose()
```

D3 must specify exactly one of these patterns — not "prefer a simple factory."

---

### [HIGH 3] D10 test table omits `_llm_update` and `update_quality_scores_in_lance` despite R4 requiring them

**Location:** D10 test table (6 listed tests), R4 mitigation claim.

**Problem:** R4 (Risk section) explicitly states:

> "D10 `test_quality_tagger_llm.py` must test the full Lance update path (including
> `_llm_update`) via mocked `requests.post`, not just the scoring function. The reviewer
> should check that D10 achieves equivalent or better coverage."

D10's test table lists 6 tests — all test `score_chunks_via_gateway()` (the HTTP scoring
function). **None** test `_llm_update()` or `update_quality_scores_in_lance()`. The
Lance merge_insert path is the only code that writes to storage; it is the most critical
path for column-mode correctness and idempotency. Without a test for `_llm_update()`,
the `merge_insert("chunk_id").when_matched_update_all(...)` call is untested at the unit
level. A bug there (e.g., wrong column list, merge on wrong key) would only surface
in integration tests.

**Required fix:** Add at minimum two tests to D10:

| Test | What it checks |
|---|---|
| `test_llm_update_calls_merge_insert` | Mock `table.merge_insert(...).when_matched_update_all(...).execute(data)` chain; assert it's called with correct `chunk_id` key and `["attr_quality_score", "attr_quality_provider"]` columns |
| `test_update_quality_scores_no_new_rows` | Mock Lance `open_table` + `search().where().select().to_list()` returning 2 rows; mock `requests.post`; call `update_quality_scores_in_lance(src_id=42)`; assert `count_rows()` is called (no insert path triggered) |

---

### [MEDIUM 1] D11 V2/V3 proposed code uses shell variable interpolation instead of `-e` env injection

**Location:** D11, items 5 (V2) and 6 (V3) proposed Python snippets.

**Problem:** The proposed V2/V3 snippets use:

```python
rows = tbl.search().where('source_id = $SRC_ID').to_list()
```

This interpolates the shell variable `$SRC_ID` directly into the Python string before it
reaches the Python interpreter. The **established pattern** in the existing `attr_quality)`
block (confirmed in `checks.sh` lines 2409–2429) is:

```bash
docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${AQ_SRC_ID}" \
  fastapi python -c "
import lancedb, os
src_id = int(os.environ['SRC_ID'])
...
rows = t.search().where(f'source_id = {src_id}').select([...]).to_list()
```

The `-e VAR=val` + `os.environ` pattern is the correct, idiomatic approach throughout the
file. D11 must use `-e SRC_ID="${AQ_SRC_ID}"` + `int(os.environ['SRC_ID'])` for all new
inline Python, consistent with the existing V2/V3 implementation.

---

### [MEDIUM 2] Collection name `"test-attr-quality-f027"` not updated in D11

**Location:** D11 description and the implied test fixture setup.

**Problem:** The existing `attr_quality)` block creates collection `"test-attr-quality-f027"`
(checks.sh line 2217). D11 updates this block for F-028 but does not explicitly state that
the collection name must change to `"test-attr-quality-f028"`. If the same collection name
is reused, the F-028 check block operates on F-027 data (if a prior run created it), which
can produce false positives: F-028 V3 would pass against F-027 rows that happen to have a
non-`"length_heuristic"` provider from a prior experimental run.

Additionally, keeping the collection named `"test-attr-quality-f027"` creates confusion
when both F-027 and F-028 checks run in the same CI session — the 409 idempotency branch
(line 2219) would reuse the old collection, making it unclear which feature's data is
under test.

**Required fix:** D11 must include a bullet:
- "Rename the test collection from `test-attr-quality-f027` to `test-attr-quality-f028`
  in the collection create call and the Postgres fallback lookup."

---

### [NIT 1] V-ROUTE verified by "manual curl or gateway unit test" — not automated

**Location:** §4 verification plan, row V-ROUTE.

**Problem:** `V-ROUTE | POST /api/internal/llm/completions returns 200 | Manual curl or
gateway unit test` — "manual curl" is not runnable by the verifier via `checks.sh`.

**Required fix:** Specify an automated curl command in D11:

```bash
echo "--- attr_quality: V-ROUTE — POST /api/internal/llm/completions ---"
ROUTE_RESP=$(curl -sf -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/internal/llm/completions" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Rate: hi"}],"max_tokens":4}')
echo "$ROUTE_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'content' in d and 'model' in d, f'missing fields: {d}'
print(f'  V-ROUTE OK: content={d[\"content\"]!r} model={d[\"model\"]!r}')
" || { echo "FAIL: V-ROUTE check failed"; exit 1; }
```

---

### [NIT 2] D7 acceptance criteria lacks an import-guard verification bullet

**Location:** D7 (definitions.py description update) acceptance criteria.

**Problem:** D7 only verifies that the description string is updated and the import block
is unchanged. D6 acceptance criteria include the correct `grep -r "import anthropic..."
dagster/` check for invariant #4. D7 (definitions.py edit) does not independently include
this verification, even though definitions.py imports from quality_tagger and any
accidental re-introduction of a direct SDK import would be in the same file tree.

**Required fix:** Add to D7 acceptance criteria:
- "The static `grep` from D6 (`grep -r 'import anthropic|from anthropic|import openai|from
  openai' dagster/`) still returns no matches after D7 changes are applied."

This is redundant with D6 but makes D7 self-contained as a verification checklist item.

---

## Summary

The **two-layer architecture** (Dagster `requests.post` → FastAPI `/api/internal/llm/completions`
→ `LLMGateway` → Anthropic SDK) is the correct design for invariant #4. The mock mode
strategy (empty `ANTHROPIC_API_KEY` → `score=0.5, model="mock"`) is sound for CI.
The column-mode update via `merge_insert("chunk_id").when_matched_update_all(...)` is
correct per §8.2 of the design doc. The per-chunk batching deferral to F-029 is
acceptable. The Option A (SQL `values_sql`) removal is correct — HTTP cannot be called
from SQL. These core architectural decisions are **approved**.

The three BLOCKERs are **process and verifiability gaps**, not design flaws:
- V-OPENAPI cannot catch a missing `include_in_schema=False` (BLOCKER 1).
- V4 has no runnable code (BLOCKER 2).
- The test file deletion is undocumented in the change table and contradicted in D10 (BLOCKER 3).

The three HIGHs are **correctness and completeness gaps**:
- `include_in_schema=False` ambiguity risks the internal endpoint leaking (HIGH 1).
- Per-request `AsyncAnthropic()` instantiation is a resource leak under production load (HIGH 2).
- D10 omits the most critical code path (`_llm_update`) from unit tests despite R4 promising coverage (HIGH 3).

Fix all BLOCKERs and HIGHs before implementation begins. MEDIUMs and NITs may be
addressed inline during implementation but must be reflected in the final `agreed.md`.
