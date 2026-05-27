# S026-F-026 — Mode B Review (Final)

**Reviewer:** reviewer (Mode B — post-implementation diff review)
**Diff:** `git diff 3c8e5f3..b9918a2`
**Contract:** `contracts/S026-F-026/agreed.md`
**Date:** 2026-05-27

---

## Verdict

**APPROVED**

No blocking issues. All contract requirements are met. All 6 hard invariants hold. All
7 checklist items from the review brief are satisfied. Codebase is clean for verifier.

---

## Per-requirement trace

### §2 — Files changed / created

| File | Action required | Outcome |
|------|-----------------|---------|
| `dagster/dagster_platform/lance_io_manager.py` | CREATE | Created, 107 lines ✓ |
| `dagster/dagster_platform/definitions.py` | MODIFY (4 sub-items) | All 4 sub-items satisfied ✓ |
| `dagster/dagster_platform/chunker.py` | comment-only change | 4-line superseded comment added ✓ |
| `verify/checks.sh` | Append V5 + V6 | Both appended inside `chunks)` case ✓ |

No migrations, no API schema changes, no `apps/api/` changes. Confirmed by diff stat.

---

### Checklist item 1 — C1 fix: `context.has_partition_key` guard

**Contract:** D5 requires the guard to appear BEFORE accessing `context.partition_key`
because `context.partition_key` raises `DagsterInvariantViolationError` (not falsy) on
non-partitioned assets.

**Finding:** SATISFIED
- `lance_io_manager.py:55` — `if not context.has_partition_key:` raises `ValueError` with
  a descriptive message.
- `lance_io_manager.py:60` — `source_id = int(context.partition_key.removeprefix("src_"))`
  is only reached after the guard passes.
- The D11 empty-list early-return (line 47–50) precedes the D5 guard. This is consistent
  with agreed.md D11: "if obj is empty, return immediately without touching Lance" — the
  guard is still correctly placed relative to the only access of `partition_key`.
- Comment at line 52–54 accurately explains the invariant.

---

### Checklist item 2 — `chunks` asset returns `list[dict]`, not `MaterializeResult`

**Contract:** D9 — switch return type to `list[dict[str, Any]]`; move materialization
metadata to `context.add_output_metadata()`.

**Finding:** SATISFIED
- `definitions.py:140` — signature is `def chunks(...) -> list[dict[str, Any]]:`
- `definitions.py:177–183` — asset-level metadata (`source_id`, `chunk_count`,
  `text_length`) moves to `context.add_output_metadata()`.
- `definitions.py:184` — returns `rows` directly (no `MaterializeResult` constructor).
- `MaterializeResult` import at line 24 is retained — `extract_mineru` (line 118) still
  uses it.  No spurious dead import.
- `from typing import Any` correctly added (line 17) for the new return type annotation.

---

### Checklist item 3 — `io_manager_key` wired on asset and Definitions

**Contract:** §2(b) — asset annotated with `io_manager_key="lance_chunks_io"`;
§2(d) — resource added to `Definitions`.

**Finding:** SATISFIED
- `definitions.py:132` — `io_manager_key="lance_chunks_io"` in `@asset(...)` decorator.
- `definitions.py:208` — `resources={"lance_chunks_io": LanceChunksIOManager()}` in
  `defs = Definitions(...)`.
- Key string `"lance_chunks_io"` matches on both sides. ✓

---

### Checklist item 4 — V5 uses dedicated count-only snippet (C2 fix)

**Contract:** C2 required a dedicated snippet that prints only an integer for V5 — NOT
the human-readable V1 output string.

**Finding:** SATISFIED
- V1 (`checks.sh:1969`) prints: `print(f'  V1 OK: {n} chunk rows written ...')` — a
  human-readable f-string, not capturable as a bare integer.
- V5 pre-run snippet (`checks.sh:2079–2080`) prints: `print(n)` — bare integer only,
  captured via `$(...) 2>/dev/null` into `CH_COUNT1`.
- V5 post-run snippet (`checks.sh:2150–2151`) is an identical dedicated snippet, also
  `print(n)` captured into `CH_COUNT2`.
- Both snippets include `2>/dev/null` which redirects Docker/lancedb startup noise;
  genuine snippet failure (exception) still propagates as empty string, causing
  `[ "$CH_COUNT2" -eq "$CH_COUNT1" ]` to exit non-zero — fail-safe. ✓
- The C2 comment at `checks.sh:2062` explicitly records the rationale.

---

### Checklist item 5 — V6 duplicate chunk_id check

**Contract:** V6 must query `chunk_id` for the test source and assert
`len(ids) == len(set(ids))`.

**Finding:** SATISFIED
- `checks.sh:2175–2176` — `.search().where(...).select(['chunk_id']).to_list()`
- `checks.sh:2177–2178` — `ids = [r['chunk_id'] for r in rows]` then
  `assert len(ids) == len(set(ids)), ...`
- `checks.sh:2179` — `print(f'  V6 OK: ...')` and `sys.exit(0)`.
- `checks.sh:2181` — `|| { echo "FAIL V6: duplicate chunk_ids detected"; exit 1; }`
- V6 runs after V5 `COMPLETED_SUCCESS` guard, sharing `CH_SRC_ID` from outer scope. ✓

---

### Checklist item 6 — D10: superseded comment on `write_chunks_to_lance()`

**Contract:** One-line comment noting the function is superseded by `LanceChunksIOManager`.

**Finding:** SATISFIED (and exceeds minimum)
- `chunker.py:217–219` — three-line comment block:
  ```
  Superseded by LanceChunksIOManager (F-026, sprint S026-F-026).
  Retained here because it is unit-tested in tests/test_chunker.py.
  The chunks asset no longer calls this function directly.
  ```
- Function body is unchanged; no logic edits in `chunker.py`. ✓
- `write_chunks_to_lance` import removed from `definitions.py:44` (was `-    write_chunks_to_lance,`). ✓

---

### Checklist item 7 — D8: `load_input()` raises `NotImplementedError`

**Contract:** D8 — `load_input()` must raise `NotImplementedError` with descriptive message.

**Finding:** SATISFIED
- `lance_io_manager.py:97–107` — `load_input()` immediately raises:
  ```python
  raise NotImplementedError(
      "LanceChunksIOManager.load_input() is not implemented. "
      "Downstream processors connect to Lance directly."
  )
  ```
- Type signature is `(self, context: InputContext) -> None` — imports `InputContext`
  from dagster at line 22. ✓

---

## Additional quality observations

### Imports in `lance_io_manager.py`

`from dagster_platform.chunker import CHUNKS_SCHEMA, build_lance_storage_options`
(line 24) — imports exactly the two symbols named in agreed.md §2 table. No circular
import risk: `chunker.py` has no `from dagster_platform.lance_io_manager import ...`. ✓

### D3 — env-var config, no Pydantic dependency

`lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")` uses safe `.get()` with
default. `build_lance_storage_options()` (from chunker.py) reads `MINIO_ROOT_USER`,
`MINIO_ROOT_PASSWORD`, `MINIO_ENDPOINT` — same pattern as rest of codebase. ✓

### D6 — `producer_asset` derivation

`producer_asset = context.asset_key.path[-1]` (line 64). This correctly yields
`"chunks"` for the current asset and is reusable for future assets without modification,
as the design doc §8.2 intended. ✓

### D7 — F-028 TODO comment

`lance_io_manager.py:77` — `# TODO F-028: dispatch column mode vs. row mode based on
operator category.` present at the correct location (just above `table.delete()`). ✓

### D11 — empty-list guard

`lance_io_manager.py:47–50` — skips both the delete and the add, records
`{"row_count": 0, "mode": "row_skipped"}` metadata. Consistent with D11. ✓

### V5 polling loop

40 × 3 s = 120 s max, matching the agreed.md V5 spec ("≤120 s, 40×3 s sleep"). ✓
Terminal-state fast-fail on `COMPLETED_FAILED|CANCELED|*FAIL*`. ✓

### `checks.sh` structural integrity

V5 and V6 are placed before the `;;` that closes the `chunks)` case (line 2182). The
`chunks)` case still has a single `;;` at the end. The `  *)` fallback at line 2183 is
unchanged. ✓

---

## Hard invariant check

| # | Invariant | Status |
|---|-----------|--------|
| 1 | **Lineage mandatory** | Not applicable — Lance chunks table is not a lineage-tracked commit. Postgres `run` table (F-024) records backfill ID unchanged. ✓ |
| 2 | **Storage separation + CAS** | All chunk bytes go to `s3://lance/chunks`. Nothing written to Postgres in this sprint. ✓ |
| 3 | **Schema frozen post-publish** | `CHUNKS_SCHEMA` reused verbatim from `chunker.py`. No schema change. ✓ |
| 4 | **LLM calls via gateway** | No LLM calls introduced anywhere in diff. ✓ |
| 5 | **Async SQLAlchemy** | IO manager lives in `dagster/dagster_platform/`. No SQLAlchemy anywhere in diff. ✓ |
| 6 | **OpenAPI ↔ TS type sync** | No API schema changes. `make codegen` not required. ✓ |

---

## No items for verifier follow-up

The implementation is complete, clean, and faithful to the contract. Pass to verifier
with `bash verify/checks.sh chunks` as the gating command.
