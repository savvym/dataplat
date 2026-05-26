# S023-F-023 Mode A Review — Iteration 2

**Verdict**: APPROVED

---

## Previous findings resolution

### [BLOCKER] #1 — Field count 22 → 24
**RESOLVED.**  
Every occurrence of `22` has been replaced with `24`:
- Summary (line 14): "canonical 24-field Arrow schema"
- `CHUNKS_SCHEMA` description (line 37): "all 24 fields from design doc §4.2"
- `test_chunks_schema_field_count` (line 58): `assert len(CHUNKS_SCHEMA) == 24`
- `empty_table()` narrative (line 166): "all 24 typed columns"
- checks.sh V1 (line 271): `assert len(schema_names) == 24, f'expected 24 fields, …'`
- All-24 unit-test coverage note (line 238) is present.

Confirmed by grep: no remaining `22` field-count assertions anywhere in the document.

---

### [HIGH] #1 — `except Exception` narrowed to targeted catch
**RESOLVED.**  
D4 code block (lines 150–158) now reads:

```python
except (FileNotFoundError, OSError) as exc:
    # Only create a new empty table when the path genuinely does not exist.
    # Re-raise anything else (permission denied, network error, corrupted manifest).
    if "does not exist" not in str(exc).lower() and "not found" not in str(exc).lower():
        raise
```

The broad catch is gone. The double-guard (type narrowing + message check + re-raise) is exactly the pattern requested. The requirement for the implementer to confirm the exact `lance` exception type and record it in the module docstring is also retained.

---

### [MEDIUM] #1 — `pylance` → `lance`
**RESOLVED.**  
`grep pylance proposed.md` returns zero hits. All three prior occurrences corrected:
- D1 (line 127): "the `lance` dep bundled inside `lancedb`"
- D2 / pyproject.toml comment (lines 93, 97): "pyarrow + lance come as transitive deps"

---

### [MEDIUM] #2 — Concrete fallback for `pa.Schema.empty_table()`
**RESOLVED.**  
D4 now provides the full two-path pattern (lines 170–176):

```python
# Preferred (PyArrow >= 14): CHUNKS_SCHEMA.empty_table()
# Fallback if AttributeError:
arrays = [pa.array([], type=field.type) for field in CHUNKS_SCHEMA]
empty_tbl = pa.table(dict(zip(CHUNKS_SCHEMA.names, arrays)))
# Note: pa.list_(pa.float32(), 1024) and pa.list_(pa.uint64()) support pa.array([], type=...)
```

The requirement for the implementer to record in the module docstring which form was used (with the resolved pyarrow version) is explicit.

---

### [MEDIUM] #3 — Ordered trial list for `storage_options["endpoint"]`
**RESOLVED.**  
D5 (lines 201–207) contains the three-entry ranked list:

```
1. "endpoint"          (lancedb >= 0.6 / object_store 0.9)
2. "aws_endpoint"      (lancedb <= 0.5 / object_store 0.7)
3. "aws_endpoint_url"  (lancedb 0.10+ or rust object_store 0.10+)
```

And the implementer is required to leave a comment in `make_lance_storage_options()` noting the exact key + lancedb version it was verified against.

---

### [NIT] #1 — `storage/__init__.py` not mentioned
**RESOLVED.**  
The file is now an explicit "Files to create" entry (lines 49–52) with a "CREATE IF ABSENT" instruction and an explanation of why it matters for the `dataplat_api.storage.lance` import.

---

### [NIT] #2 — `updated_at` absent from type spot-check
**RESOLVED.**  
`test_chunks_schema_key_field_types` (line 69) now asserts:  
`updated_at` → `pa.timestamp("ms")`  
The timestamp pair is complete.

---

## New findings (iteration 2)

### [NIT] D8 parenthetical wording imprecision

**Location**: D8 (line 228):
> "The URI … is computed inside `get_or_create_chunks_table()` (and `make_lance_storage_options()`) at call time …"

`make_lance_storage_options()` does not compute the URI; it computes the storage-options dict. The intent of the parenthetical is clearly to say "both functions resolve settings at call time", but the wording implies `make_lance_storage_options()` also produces the URI. This is a clarifying note only — D5 unambiguously shows what the function returns, and the D4 pseudocode correctly shows the URI being built exclusively inside `get_or_create_chunks_table()`. **Not blocking.**

---

## Criterion coverage assessment

| Criterion | Contract layer | Status |
|---|---|---|
| Schema has all 7 required fields | `backend` unit test | ✅ |
| Schema has exactly 24 §4.2 fields | `backend` unit test | ✅ |
| Schema visible via `lance.dataset(…).schema` | `lance` V1 check | ✅ |
| Table path accessible via MinIO SDK | `lance` V2 check | ✅ |

---

## What remains sound (unchanged from iteration 1 assessment)

- **D1** — `lance.write_dataset()` over `lancedb.create_table()` to avoid `.lance` suffix. Correct.
- **D2** — Pin only `lancedb`; `lance` + `pyarrow` come in transitively. Correct.
- **D3** — Synchronous implementation appropriate for Dagster context. Justified.
- **D6** — `MINIO_LANCE_BUCKET = "lance"` default matches F-003 bucket; no docker-compose changes.
- **D7** — Lazy init (no lifespan hook) avoids blocking FastAPI startup on a non-critical I/O path.
- **D8** — `CHUNKS_SCHEMA` as module-level constant is correct; URI computed at call time respects env-var overrides.
- **checks.sh structure** — `lance)` layer, boto3-based V2 check, `all)` chain placement after `documents`.
- **Out-of-scope list** — Correctly excludes row writes (F-025), IOManagers, Dagster assets, FastAPI routes, Alembic migrations, and `make codegen`.
- **CLAUDE.md hard-invariant compliance** — No new async DB sessions required, no Postgres blob storage, no direct LLM calls, no OpenAPI changes, no Celery/Dagster scope creep.

---

## Decision

All six previously-required changes (one BLOCKER, one HIGH, three MEDIUMs, two NITs) are addressed.
No new issues rise above NIT level, and the single NIT identified is a wording clarification in a
descriptive note that does not affect implementation correctness.

**This contract is approved for implementation.**
