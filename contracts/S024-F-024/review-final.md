# Review Final: S024-F-024 — Trigger chunking via POST /api/runs

**Reviewer:** reviewer (Mode B — code review after implementation)  
**Commit:** `19b7e0df8254fe5ae64f5e9de6cd132dc173389e`  
**Date:** 2026-05-26  
**Verdict:** **APPROVED**

---

## Scope of review

Compared the implementation diff against `contracts/S024-F-024/agreed.md` and checked all
six hard invariants from `CLAUDE.md`.

Files changed: 13 (gateway.py, routers/runs.py, schemas/runs.py, definitions.py,
test_gateway_chunks_backfill.py, test_runs_trigger.py, verify/checks.sh,
packages/api-types/openapi.json, spec/feature_list.json, claude-progress.txt,
contracts/S024-F-024/{agreed,feedback,proposed}.md).

---

## Contract item checklist

| Item | Status | Notes |
|---|---|---|
| `RunCreate.asset` Literal widened to `("extract_mineru", "chunks")` | ✅ PASS | schemas/runs.py exactly as contracted; docstring updated |
| `_LAUNCH_CHUNKS_BACKFILL_MUTATION` constant added after extract constant | ✅ PASS | Separate constant, correct operation name `LaunchChunksBackfill`, all 7 GraphQL union arms |
| `launch_chunks_backfill()` method: 5 `DagsterGatewayError` paths | ✅ PASS | TimeoutException, ConnectError, HTTPError, non-2xx, JSON parse, GraphQL `errors`, non-Success `__typename`, absent/empty backfillId — all paths present |
| `assetSelection: [{"path": ["chunks"]}]`, `title: "F-024 chunks"` | ✅ PASS | Payload correct |
| gateway.py module docstring lists both methods | ✅ PASS | Lines +9/+10 in diff |
| `trigger_extract_run` docstring updated (step 4 dispatch) | ✅ PASS | |
| Module docstring updated (`F-018/F-024`) | ✅ PASS | |
| Route summary/description updated | ✅ PASS | Minor wording difference noted below (NIT-1) |
| `if body.asset == "extract_mineru": ... else:` dispatch | ✅ PASS | `kind="extract"/"chunk"`, `asset_keys` correct per branch |
| `else` comment: "guaranteed by RunCreate.asset Literal validation" | ✅ PASS | |
| Function name `trigger_extract_run` kept as-is | ✅ PASS | Per D4 |
| `chunks` stub asset: `@asset(partitions_def=sources_partitions, ...)` | ✅ PASS | |
| Stub body raises `NotImplementedError` | ✅ PASS | Message matches contracted text |
| No `deps=` on stub | ✅ PASS | Per D3 |
| `defs = Definitions(assets=[source_asset, extract_mineru, chunks])` | ✅ PASS | |
| 5 gateway unit tests | ✅ PASS | All 5 from contract table present in `test_gateway_chunks_backfill.py` |
| Router tests (202, kind=chunk, dispatch check, regression) | ✅ PASS | See note on `test_trigger_run_extract_still_works` (NIT-2) |
| Schema tests (accepts chunks, rejects unknown) | ✅ PASS | Present as `test_schema_accepts_chunks_asset` / `test_schema_still_rejects_unknown_asset` |
| checks.sh: F024-setup + F024-V1/V2/V3 before closing `;;` | ✅ PASS | |
| openapi.json committed in same commit (hard invariant #6) | ✅ PASS | `"const"` → `"enum": ["extract_mineru","chunks"]`, description updated |
| No migration required | ✅ PASS | `kind: str` column pre-existing; "chunk" is a new value not a new column |

---

## Hard invariant check

| Invariant | Status | Notes |
|---|---|---|
| #1 Lineage mandatory | N/A | No new Commit lineage record introduced |
| #2 Storage separation + CAS | N/A | No new blob storage |
| #3 Schema frozen post-publish | N/A | No Silver/Gold repo commits |
| #4 LLM calls via gateway | N/A | No LLM calls |
| #5 Async SQLAlchemy | ✅ PASS | `await gateway.launch_chunks_backfill(partition_keys)` in `else` branch; no sync sessions |
| #6 OpenAPI ↔ TS type sync | ✅ PASS | `packages/api-types/openapi.json` committed in the same commit (19b7e0d); `RunCreate.asset` reflects the enum change |

---

## Findings

### NIT-1 — Route description wording differs from agreed.md

**agreed.md** specified:
```
"Launch a Dagster asset backfill for the given asset over the supplied source IDs. "
"Supported assets: 'extract_mineru' (F-018), 'chunks' (F-024). "
```

**Actual implementation:**
```
"Launch a Dagster asset backfill for extract_mineru or chunks over the given source IDs. "
```

The "Supported assets: … (F-018) … (F-024)" sentence is absent. The information is still
communicated (both asset names appear inline), and the OpenAPI JSON correctly reflects this
wording. **No functional impact; no action required.** Flag for cleanup if desired.

---

### NIT-2 — `test_trigger_run_extract_still_works` not added as a named test

The agreed.md contract table lists this test explicitly. The implementation does not add it
as a new test function. However, the pre-existing F-018 test `test_trigger_extract_happy_path`
(which POSTs `asset="extract_mineru"` and asserts 202 + correct dispatch) provides equivalent
regression coverage. The test file also retains `test_trigger_extract_wrong_asset_returns_422`
and `test_trigger_extract_run_row_added`.

**No functional coverage gap.** The contract named test should have been added for strict
contract fidelity. No action required; pre-existing tests are sufficient for verification.

---

### NIT-3 — checks.sh F024-setup imports `struct, zlib, io` (unused)

The agreed.md F024-setup block (reviewed and approved in Mode A) uses a simple byte-literal
PDF without imports. The Mode A reviewer explicitly confirmed "No `import struct, zlib` line
is present anywhere in the document. ✓" (feedback.md NIT-3 verification section).

The actual checks.sh implementation uses a different PDF generation function that imports
`struct, zlib, io` but only uses `sys` (called at the bottom via
`sys.stdout.buffer.write(pdf.encode())`). `struct`, `zlib`, and `io` are never referenced.
Python will silently ignore unused imports, so this does not break the check. The primary
generation path falls back to `|| printf '...'` if the Python script fails, adding robustness.

**No runtime impact; the check will pass.** Minor deviation from the reviewed contract.
No action required.

---

### Observation — openapi.json picks up pre-existing F-022 drift

The F-024 `make codegen` run also regenerated the `/api/documents/{variant_id}/render`
endpoint entry (from F-022) which was missing from the previous `openapi.json` state. This
means the F-022 commit (`184ee67`) had violated hard invariant #6 — it shipped a new API
endpoint without committing the updated `openapi.json`. The F-024 commit has silently corrected
that drift as a side effect of its own `make codegen`.

**Not a F-024 defect; the current openapi.json is now correct.** The pre-existing F-022
invariant violation is noted for the record; it cannot be retroactively addressed without
amending that commit, which is out of scope.

---

### Process deviation — `passes: true` flipped before Mode B + verifier

The implementer set `spec/feature_list.json` F-024 `passes: true` in the implementation
commit (19b7e0d) before this Mode B review was completed and before the `verifier` has run.
Per the sprint workflow (CLAUDE.md step 10), `passes: true` should only be flipped after
`verifier` reports green.

**This review does not block on this** (per the task brief), but the leader should ensure
verifier runs and, if it fails, flip `passes` back to `false` and re-run the sprint.

---

## Quality observations (no action required)

- **Happy-path assetSelection assertion in gateway test:** `test_launch_chunks_backfill_success`
  verifies `assetSelection == [{"path": ["chunks"]}]`, `partitionNames`, and `title`. This is
  the most important safety net against a copy-paste error silently targeting the wrong asset.
  Well done.

- **`test_trigger_chunks_happy_path_202` asserts `launch_extract_backfill.assert_not_called()`:**
  Confirms dispatch is exclusive, not additive. Good.

- **Fallback in checks.sh setup:** The `|| printf '%%PDF-1.4\n...'` fallback is a robustness
  improvement over the contract. No downside.

- **`kind="chunk"` vs. `asset="chunks"` mapping** is correctly documented in the handler
  docstring and is the only asymmetry in the implementation. The agreed.md explicitly sanctioned
  this naming (same as F-018 uses `kind="extract"` for `asset="extract_mineru"`).

---

## APPROVED

All contract items satisfied. All hard invariants pass. Three NITs are cosmetic or harmless
deviations that do not affect correctness, coverage, or observability. The implementation is
safe to proceed to the verifier stage.

**Next step:** run `bash verify/checks.sh runs` (after confirming dagster-webserver has been
restarted to pick up the `chunks` stub asset) and `bash verify/checks.sh backend`.
