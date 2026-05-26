# Review Feedback: S024-F-024 — Trigger chunking via POST /api/runs

**Reviewer:** reviewer (Mode A — contract review before code is written, iteration 3 — FINAL)  
**Date:** 2026-05-26  
**Verdict:** APPROVED

---

## Summary

All findings from iterations 1 and 2 are resolved. No new issues found. The contract is
approved as written; the implementer may proceed directly to coding.

---

## Iteration findings — final status

| Finding | Iteration | Status |
|---|---|---|
| B-1 — missing F024 setup bash block | 1 | **RESOLVED** |
| NIT-1 — `launch_extract_backfill` absent from gateway.py docstring | 1 | **RESOLVED** |
| NIT-2 — runs.py module docstring not updated | 1 | **RESOLVED** |
| B-2 — setup curl command used `\$VAR` (backslash-dollar) | 2 | **RESOLVED** |
| NIT-3 — unused `import struct, zlib` in setup Python snippet | 2 | **RESOLVED** |

---

## B-2 verification

The four curl argument lines in the setup block (proposed.md lines 306–310) now read:

```bash
F024_UP_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
  -H "Authorization: Bearer $RUNS_TOKEN" \
  -F "file=@${F024_PDF};type=application/pdf" \
  -w '%{http_code}' -o "$F024_UP_BODY")
```

All four variables use plain `$` / `${…}` — no backslash escaping. Shell expansion will
occur correctly at runtime. ✓

---

## NIT-3 verification

The setup `python3 -c` block (proposed.md lines 292–303) opens with:

```python
# Minimal valid single-page PDF (same approach as F018 setup).
pdf = b'%PDF-1.4\n'
```

No `import struct, zlib` line is present anywhere in the document. ✓

---

## Clarification on `\$id` in F024-V3 (not a bug)

Lines 373–374 retain `\$id` inside the GraphQL triple-quoted Python string:

```python
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
```

This is **intentional and correct**. The Python script is passed via `python3 -c "…"` inside
a double-quoted shell string. Without the backslash, bash would expand `$id` to an empty
string before Python ever sees it. The `\$id` escaping causes bash to pass the literal
two-character sequence `$id` to Python, which is valid GraphQL variable syntax. The shell
variable `$F024_BACKFILL_ID` on line 386 (outside the triple-quoted string, in a Python
dict value) correctly uses no backslash — bash expands it to the actual backfill ID before
Python receives the string. No action required.

---

## Confirmed sound (cumulative — all iterations)

| Area | Finding |
|---|---|
| Schema extension (`RunCreate.asset` Literal) | Widens from `"extract_mineru"` to `("extract_mineru", "chunks")`; Pydantic v2 validation rejects any other value with 422. ✓ |
| `_LAUNCH_CHUNKS_BACKFILL_MUTATION` constant | Separate from `_LAUNCH_EXTRACT_BACKFILL_MUTATION`; different operation name `LaunchChunksBackfill`; all same GraphQL error union arms. ✓ |
| `launch_chunks_backfill` error paths | Five `DagsterGatewayError` paths (network, HTTP non-2xx, GraphQL `errors`, non-Success `__typename`, empty `backfillId`) mirror `launch_extract_backfill`. ✓ |
| gateway.py module docstring | §2 lines 125–126: both `launch_extract_backfill` and `launch_chunks_backfill` listed. ✓ |
| `if/else` dispatch in `trigger_extract_run` | Clean two-branch dispatch; `kind`/`asset_keys` correctly set per branch; Literal validation guarantees the `else` arm is only `"chunks"`. ✓ |
| runs.py module docstring | §3 updated line-11 text: `trigger an asset backfill (extract_mineru or chunks, HTTP 202 Accepted, F-018/F-024)`. ✓ |
| `chunks` stub asset | `@asset(partitions_def=sources_partitions)`, body `raise NotImplementedError`; no `deps=`; added to `defs` assets list. ✓ |
| F024 setup scoping | `F024_SRC_ID` assigned before V1; `F024_BACKFILL_ID`/`F024_RUN_ID` derived from V1 body; used in V3 and V2 respectively. ✓ |
| Temp-file cleanup | `rm -f "$F024_PDF"` after write; `rm -f "$F024_UP_BODY"` after both success and failure paths; `rm -f "$F024_V1_BODY"` after all reads. ✓ |
| `$COMPOSE_F018`, `$RUNS_TOKEN`, `$FASTAPI_HOST_PORT` scope | All defined earlier in the `runs)` case block; in scope for all F024 checks. ✓ |
| F024-V2 `grep -q '^chunk|pending$'` | Basic-regex `|` is a literal pipe; SQL `kind \|\| '\|' \|\| status` produces `chunk\|pending`; pattern matches correctly. ✓ |
| `all)` chain ordering | `dagster)` before `runs)` — dagster-webserver restart precedes F024 checks. ✓ |
| All three F-024 verification criteria | V1 (HTTP 202 + response shape), V2 (`kind=chunk, status=pending`), V3 (Dagster `assetSelection` contains `"chunks"`) each have a corresponding bash check. ✓ |
| Hard invariant #6 (OpenAPI sync) | `make codegen` + `packages/api-types/` committed in same commit — explicitly required in §2 and verification plan step 5/6. ✓ |
| Hard invariant #5 (async SQLAlchemy) | No new sync DB calls introduced. ✓ |
| Hard invariant #4 (LLM gateway) | Not applicable — no LLM calls. ✓ |
| Unit test table (§5) | Nine tests: 5 gateway + 4 router + 2 schema = 11 total specified; coverage appropriate. ✓ |
| D1–D6 design decisions | All sound and well-justified. ✓ |
| Out-of-scope table | Real chunking body, `deps=`, plugin registration, migration, `GET /api/runs` list all correctly deferred. ✓ |
| MVP scope | No Celery/Dagster scheduling, no Docker-in-Docker, no OAuth. ✓ |

---

## APPROVED

The implementer may proceed. Implement exactly as specified in `proposed.md`. At completion,
submit the diff for Mode B review.
