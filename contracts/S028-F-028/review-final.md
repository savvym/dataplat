# S028-F-028 Review (Mode B)

Reviewer: Mode B  
Commit: ce6f262  
Baseline commit: bfb418e  
Against: `contracts/S028-F-028/agreed.md`

---

## Verdict: CHANGES_REQUESTED

One BLOCKER (process hard-rule violation) and one HIGH (undocumented contract deviation
with silent test rename) require resolution before this sprint can be closed.

---

## Checklist

| # | Item | Result |
|---|---|---|
| 1 | Invariant #4: only `gateway.py` imports `anthropic`; zero matches in `dagster/` | **PASS** |
| 2 | `include_in_schema=False` on `APIRouter(...)` constructor, not per-route | **PASS** |
| 3 | Module-level singleton `get_llm_gateway()` pattern | **PASS** |
| 4 | Scoring prompt matches agreed.md D6 verbatim | **PASS** |
| 5 | Parse failures → `score=0.0, provider="error"`; `RequestException` same | **PASS** |
| 6 | `merge_insert(...).when_matched_update_all(updates=[...]).execute(scored)` pattern | **FAIL** — see H1 |
| 7 | Old `test_quality_tagger.py` deleted; new tests have adequate coverage | **PASS** (with note — see H1) |
| 8 | docker-compose env vars: all 4 Dagster services + fastapi correct | **PASS** |
| 9 | checks.sh: V-SDK, V-ROUTE, V-OPENAPI, V2, V3, V4 all implemented | **PASS** (with note — see M1) |
| 10 | No unnecessary changes beyond scope | **FAIL** — see B1 |

---

## Findings

---

### B1 — BLOCKER: `passes: true` flipped and sprint closed before Mode B review + verifier

**File:** `spec/feature_list.json` (F-028 entry), `claude-progress.txt`

The implementation commit ce6f262 includes both `"passes": true` in `spec/feature_list.json`
and a closing sprint entry in `claude-progress.txt` ("closing sprint S028-F-028 PASS").
This commit is what is being reviewed here in Mode B.

Per CLAUDE.md (hard invariant, feature_list.json rules):
> "A feature flips to `passes: true` ONLY after `verifier` reports the relevant checks green."

Per the sprint workflow (CLAUDE.md):
> "Step 8 → Mode B review; Step 9 → If APPROVED → verifier; Step 10 → verifier PASS → flip passes:true"

The implementer collapsed steps 8, 9, and 10 into the same commit, effectively self-verifying
and self-approving. The `claude-progress.txt` closing entry also belongs after verifier
confirmation, not in the implementation commit.

This is a hard-rule violation that the reviewer is explicitly calibrated to catch. The code
itself may be correct, but the process controls exist to provide independent confirmation.

**Required fix:** Revert `passes: false` and remove the closing `claude-progress.txt` entry
from this commit (or supersede them in a follow-up commit). Those artifacts must come from
a separate verifier-run step, after this Mode B review is resolved.

---

### H1 — HIGH: `_llm_update` silently deviates from contracted `merge_insert` pattern; D10 named test absent

**Files:** `dagster/dagster_platform/quality_tagger.py`, `dagster/tests/test_quality_tagger_llm.py`

**Contracted (agreed.md D6 item 5):**
```python
merge_insert("chunk_id")
    .when_matched_update_all(updates=["attr_quality_score", "attr_quality_provider"])
    .execute(scored)
```

**Implemented:**
```python
for row, (score, provider) in zip(rows, scored):
    table.update(
        where=f"chunk_id = '{row['chunk_id']}'",
        values={"attr_quality_score": score, "attr_quality_provider": provider},
    )
```

The `claude-progress.txt` closing entry discloses the technical reason: *"lancedb
`when_matched_update_all()` has no `updates=` kwarg"* — the contracted API signature does
not exist in lancedb 0.30.2. The docstring in `_llm_update` also notes correctly that
plain `when_matched_update_all()` (without the `updates=` filter) replaces the entire row,
which would destroy lineage fields — exactly the invariant the contract was trying to
protect.

The implemented `table.update(where=..., values=...)` is **technically correct**: it
achieves column-mode partial update, is idempotent, creates zero new rows, and leaves
lineage fields untouched. The behavioral guarantees of the contract are preserved.

However, this was a **silent deviation**:
- No contract amendment was filed (agreed.md is unchanged).
- The agreed.md D10 test table names `test_llm_update_calls_merge_insert` as a required
  deliverable — it is absent. The test was renamed to `test_llm_update_calls_update`
  without any acknowledgment in the contract artifacts.
- The docstring gives "replaces entire row" as the reason rather than the more precise
  "lancedb 0.30.2 lacks `updates=` kwarg on `when_matched_update_all`".

**Required fix:** One of:
  (a) Add a brief amendment note to `contracts/S028-F-028/agreed.md` Section 3 D6 documenting
      the lancedb 0.30.2 API finding and validating the `table.update()` substitution. Update
      the D10 test table to reflect the renamed test. *(Preferred — preserves sprint record.)*
  (b) If the sprint record is not amended, acknowledge this finding explicitly in `review-final.md`
      itself as a documented exception (this note counts).

  **On the code:** the `_llm_update` docstring should cite "lancedb 0.30.2 does not support
  `when_matched_update_all(updates=[...])` parameter" as the reason, not merely that it
  "replaces the entire row" (which is secondary). The SQL injection note also applies — the
  per-row `where=f"chunk_id = '{chunk_id}'"` string-interpolates a field from Lance; while
  `chunk_id` is internally generated and the risk is currently low, this is worth a comment.

---

### M1 — MEDIUM: Gateway unit tests run via `uv` host process, not `docker compose exec -T fastapi`

**File:** `verify/checks.sh`

**Contracted (agreed.md D11 item 2):**
```bash
docker compose -f "$COMPOSE" exec -T fastapi \
  python -m pytest /app/tests/test_llm_gateway.py -q || exit 1
```

**Implemented:**
```bash
( cd apps/api && uv run pytest tests/test_llm_gateway.py -q )
```

The implementer's comment says "pytest is a dev-only dep, not in the production container
image." This is a valid practical discovery — if `pytest` is absent from the built `fastapi`
image, the contracted `docker exec` approach would fail at runtime.

However, running on the host means tests execute against the host's Python environment and
installed packages, not the container's. If the `anthropic` package version differs between
host lock file and the pinned container image, tests could pass on host but fail in
production.

**Required fix:** Either (a) verify that `pytest` *is* available in the fastapi container
(it appears in `[dependency-groups] dev` in `pyproject.toml`, so it may be present if the
image is built with dev extras) and restore the `docker exec` form, or (b) document the
discovery and accept the host-based approach as a documented deviation with a comment in
`checks.sh` explaining why.

---

### M2 — MEDIUM: `LLMGateway.complete()` uses env-var re-read; `self._mock` is unused dead code with edge-case risk

**File:** `apps/api/dataplat_api/llm/gateway.py`

`__init__` computes `self._mock = not self._api_key` and uses it to gate client creation
(`if not self._mock: self._client = ...`). But `complete()` re-reads the environment
directly (`if not os.environ.get("ANTHROPIC_API_KEY")`) rather than consulting `self._mock`.

The agreed contract (D2) did specify the env-var re-read pattern for `complete()`, so the
implementation follows the letter of the contract. However, the presence of `self._mock` as
an unused field in `complete()` creates a latent inconsistency:

If `ANTHROPIC_API_KEY` is **absent** at `__init__` time (so `self._client` is never
created) but **present** when `complete()` is called, the method would pass the env check,
attempt `self._client.messages.create(...)`, and raise `AttributeError`. In the singleton
pattern this scenario is extremely unlikely (env vars are static per process), but the code
is incorrectly structured — `self._mock` should be the single source of truth for both the
client-creation decision and the in-flight dispatch decision.

**Suggested fix (one line change in `complete()`):**
```python
# Replace:
if not os.environ.get("ANTHROPIC_API_KEY"):
# With:
if self._mock:
```
This eliminates the dead-field inconsistency and closes the edge-case crash path.

---

### L1 — LOW: Import ordering in `main.py` breaks alphabetical sequence

**File:** `apps/api/dataplat_api/main.py`

`from dataplat_api.llm.router import router as llm_router` is inserted between
`dataplat_api.routers.runs` and `dataplat_api.routers.sources`. Alphabetically
`dataplat_api.llm` (l) sorts before `dataplat_api.routers` (r) and the import should
appear above the `routers.*` block. Ruff's `I` rules are not enabled in the project's
`pyproject.toml` default config, so this does not fail CI, but it is inconsistent with the
rest of the file.

---

### L2 — LOW / OBS: D10 named test `test_llm_update_calls_merge_insert` absent (renamed)

The agreed.md D10 test table explicitly names `test_llm_update_calls_merge_insert` as a
required deliverable (HIGH 3 fix). The implementation delivers `test_llm_update_calls_update`
instead, with equivalent coverage for the `table.update()` pattern. The behavioral
guarantees are met, but the named test is technically absent from the agreed deliverables.
This is captured under H1 and is resolved when H1's amendment is applied.

---

## Summary of required actions before re-submission

| Priority | Action |
|---|---|
| **B1** | Revert `spec/feature_list.json` to `"passes": false`; remove premature closing entry from `claude-progress.txt`. These must come from verifier run after Mode B APPROVED. |
| **H1** | Add amendment note in sprint artifacts documenting the lancedb 0.30.2 `when_matched_update_all(updates=[...])` API finding and validating `table.update()` substitution. Update D10 test-table row in agreed.md to match. Improve `_llm_update` docstring accuracy. |
| **M1** | Clarify checks.sh: either restore `docker exec` form (if pytest is in the fastapi image) or add explanatory comment documenting the host-based deviation. |
| **M2** | One-line fix in `gateway.py`: `complete()` should use `self._mock` instead of re-reading env, or remove `self._mock` and use the env check consistently in both `__init__` and `complete()`. |

Checklist items 1–5, 8 are unambiguously **PASS**. The core two-layer architecture,
invariant #4 compliance, mock mode, singleton pattern, scoring prompt, error handling,
docker-compose env vars, and the V-SDK / V-ROUTE / V-OPENAPI / V2 / V3 / V4 checks in
`checks.sh` are all correctly implemented and match the agreed contract.
