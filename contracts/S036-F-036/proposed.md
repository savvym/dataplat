# S036-F-036 — Chunk Lineage Endpoint: proposed.md

Sprint: S036-F-036  
Feature: F-036 (Phase 1, P1)  
Author: Leader (inline)  
Date: 2026-06-02

---

## 1. Goal

Expose a read-only lineage endpoint for any chunk in the Lance table.

Given a `chunk_id`, the endpoint:

1. Fetches the chunk itself from Lance.
2. Walks the `augmented_from` chain in Lance (iteratively, tip → root) to build
   an ordered lineage list. For a non-augmented (original) chunk, the chain
   has exactly 1 entry (the chunk itself). For an augmented chunk, the chain
   has ≥ 2 entries, ordered from the requested chunk down to the root original.
3. Fetches the `source` record and the canonical `document_variant` record for
   that source from Postgres (async SQLAlchemy), using the `source_id` from the
   **root** chunk (the final entry in the lineage chain).
4. Returns the composed lineage response.

**Verification criteria (from feature spec):**

- **V1:** `GET /api/chunks/{id}/lineage` returns `200` with response body
  `{"chunk": {...}, "source": {"id": ...}, "document_variant": {"id": ...}, "lineage_chain": [...]}`.
- **V2:** For a non-augmented chunk, `lineage_chain` has exactly 1 entry; for an
  augmented chunk, `lineage_chain` traces from the requested chunk to the root
  original chunk (tip → root order).

---

## 2. API Contract

### 2.1 Request

```
GET /api/chunks/{chunk_id}/lineage
```

| Parameter  | Location | Type   | Constraints            | Notes                                 |
|------------|----------|--------|------------------------|---------------------------------------|
| `chunk_id` | path     | string | `max_length=256`       | `FPath(max_length=256)` — mirrors F-035 |

**Auth:** `Depends(get_current_user)` — Bearer token required, HTTP 401 if absent.

No query parameters.

---

### 2.2 Response shape

**HTTP 200 — success:**

```json
{
  "chunk": { <ChunkRead — all 24 fields> },
  "source": { <SourceRead — 10 fields> },
  "document_variant": { <DocumentVariantRead — 10 fields> | null },
  "lineage_chain": [
    { <ChunkLineageEntry for the requested chunk> },
    { <ChunkLineageEntry for its augmented_from parent> },
    ...
    { <ChunkLineageEntry for the root original chunk> }
  ]
}
```

#### 2.2.1 `chunk`

Reuse the existing `ChunkRead` schema (all 24 CHUNKS_SCHEMA fields) —
the chunk row for the requested `chunk_id` exactly as returned by
`GET /api/chunks/{id}`.

#### 2.2.2 `source`

Reuse `SourceRead` from `apps/api/dataplat_api/schemas/sources.py` (10 fields:
`id`, `collection_id`, `kind`, `original_name`, `storage_uri`, `sha256`,
`size`, `mime_type`, `dagster_partition_key`, `uploaded_at`).

**Rationale:** The spec says "returns the source record"; `SourceRead` already
captures the full record shape and is the established contract for source
entities. Using it avoids a redundant ad-hoc schema and keeps client
de-serialization consistent with `GET /api/sources/{id}`.

#### 2.2.3 `document_variant`

Type: `DocumentVariantRead | None`.

Reuse `DocumentVariantRead` from `apps/api/dataplat_api/schemas/sources.py`
(10 fields: `id`, `extractor_name`, `extractor_version`, `config_hash`,
`storage_prefix`, `page_count`, `image_count`, `is_canonical`,
`materialized_at`, `dagster_run_id`).

**Resolution rule:** Query `document_variant` table filtered by
`source_id = <root chunk's source_id>` and `is_canonical = true`.
If a canonical variant exists, return it. If no canonical variant exists,
return `null` (not an error). See OQ-1 for discussion.

**Rationale:** The Lance `chunks` table stores `docling_refs` (a freeform
string path into the DoclingDocument, not a foreign key to `document_variant.id`).
There is no direct FK from a chunk row to a `document_variant` row. The
clearest deterministic rule available is: find the canonical variant for the
source. This maps 1-to-1 for well-formed data (the `idx_doc_canonical` partial
unique index enforces at most one canonical per source).

#### 2.2.4 `ChunkLineageEntry` (new Pydantic model)

A minimal per-entry model capturing lineage identity fields only. Full content
fields (text, attributes, etc.) are intentionally excluded to keep the chain
payload lean; callers can fetch individual entries via `GET /api/chunks/{id}`
if needed.

```python
class ChunkLineageEntry(BaseModel):
    """One entry in the augmented_from chain, tip-to-root order.

    Fields are the identity + provenance columns from CHUNKS_SCHEMA that
    describe *how* this chunk was produced and who its parent is.
    source_id is included so callers can detect if multiple entries share
    a source (all should, unless data is corrupt).
    """
    chunk_id: str
    source_id: int | None
    producer_asset: str | None
    producer_version: str | None
    augmented_from: str | None   # null on the root (original) entry
    augmenter_id: str | None
    augmenter_config_hash: str | None
```

**Chain order:** tip → root. Index 0 is the requested chunk; the final entry
is the root original (whose `augmented_from` is `null`). This order is
natural for the iterative traversal and lets clients identify the root as
`lineage_chain[-1]`.

---

### 2.3 Response schema (Pydantic)

New model in `apps/api/dataplat_api/schemas/chunks.py`:

```python
class ChunkLineageEntry(BaseModel):
    chunk_id: str
    source_id: int | None
    producer_asset: str | None
    producer_version: str | None
    augmented_from: str | None
    augmenter_id: str | None
    augmenter_config_hash: str | None

class ChunkLineageResponse(BaseModel):
    chunk: ChunkRead
    source: SourceRead
    document_variant: DocumentVariantRead | None
    lineage_chain: list[ChunkLineageEntry]
```

---

## 3. Files Changed

| File | Status | Changes |
|------|--------|---------|
| `apps/api/dataplat_api/schemas/chunks.py` | **MODIFIED** | Add `ChunkLineageEntry` and `ChunkLineageResponse` models. Import `SourceRead` and `DocumentVariantRead` from `schemas/sources.py`. |
| `apps/api/dataplat_api/routers/chunks.py` | **MODIFIED** | Add `GET /{chunk_id}/lineage` handler (`get_chunk_lineage`). Add `AsyncSession` and `get_session` imports. Add `Source`, `DocumentVariant` model imports. Add `SourceRead`, `DocumentVariantRead` schema imports. Add `ChunkLineageEntry`, `ChunkLineageResponse` schema imports. |
| `apps/api/tests/test_chunks_lineage.py` | **NEW** | Full unit test suite (≥ 15 tests) for the new endpoint. |
| `packages/api-types/openapi.json` | **MODIFIED** | Regenerated via `make codegen` — adds `ChunkLineageEntry`, `ChunkLineageResponse`, `DocumentVariantRead` (if not already present), and the new path. Committed in the same commit (invariant #6). |

No migrations required — no Postgres schema changes.

---

## 4. Implementation Sketch

```python
_MAX_LINEAGE_DEPTH = 32  # cycle / runaway guard

@router.get("/{chunk_id}/lineage", response_model=ChunkLineageResponse)
async def get_chunk_lineage(
    chunk_id: str = FPath(..., max_length=256),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChunkLineageResponse:

    # ── Step 1: Fetch the requested chunk from Lance ───────────────────────────
    #   Re-use the same pattern as F-035 get_chunk_by_id.
    #   Single-quote escape chunk_id for DataFusion predicate.

    def _fetch_chunk(cid: str) -> dict | None:
        """Synchronous Lance fetch for one chunk_id. Run via asyncio.to_thread()."""
        try:
            table = get_or_create_chunks_table()
            safe_cid = cid.replace("'", "''")
            arrow_tbl = (
                table.search()
                .where(f"chunk_id = '{safe_cid}'")
                .select(["chunk_id", "source_id", "source_collection_id",
                         "producer_asset", "producer_version",
                         "text", "token_count", "docling_refs", "source_refs",
                         "augmented_from", "augmenter_id", "augmenter_config_hash",
                         "attr_quality_score", "attr_quality_provider",
                         "attr_lang_code", "attr_lang_confidence",
                         "attr_minhash_signature", "attr_minhash_cluster_id",
                         "attr_minhash_is_head", "attr_pii_has_pii",
                         "attr_pii_categories", "attr_embed_vector",
                         "created_at", "updated_at"])
                .limit(1)
                .to_arrow()
            )
            rows = arrow_tbl.to_pylist()
            return rows[0] if rows else None
        except Exception as exc:
            raise LanceQueryError(str(exc)) from exc

    try:
        root_row = await asyncio.to_thread(_fetch_chunk, chunk_id)
    except LanceQueryError as exc:
        raise HTTPException(status_code=400, detail=f"Lance query error: {exc}")

    if root_row is None:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id!r} not found")

    chunk_read = ChunkRead(**root_row)

    # ── Step 2: Walk augmented_from chain (Lance, tip → root) ─────────────────
    #   Build lineage_chain iteratively.
    #   Depth cap: _MAX_LINEAGE_DEPTH (32). Stop when augmented_from is None
    #   or already seen (cycle guard). Raise HTTP 500 on cycle detection.

    lineage_chain: list[ChunkLineageEntry] = []
    seen_ids: set[str] = set()
    current_row = root_row

    for _depth in range(_MAX_LINEAGE_DEPTH):
        cid = current_row["chunk_id"]

        # Cycle detection — must check BEFORE appending to catch re-entry.
        if cid in seen_ids:
            raise HTTPException(
                status_code=500,
                detail=f"Cycle detected in augmented_from chain at chunk_id={cid!r}",
            )

        seen_ids.add(cid)
        lineage_chain.append(ChunkLineageEntry(
            chunk_id=cid,
            source_id=current_row.get("source_id"),
            producer_asset=current_row.get("producer_asset"),
            producer_version=current_row.get("producer_version"),
            augmented_from=current_row.get("augmented_from"),
            augmenter_id=current_row.get("augmenter_id"),
            augmenter_config_hash=current_row.get("augmenter_config_hash"),
        ))

        # ── Root check FIRST (before depth cap). ─────────────────────────────
        # A legitimate chain whose root is at exactly depth 32 (i.e. the 32nd
        # entry has augmented_from=None) must succeed with HTTP 200.
        parent_id: str | None = current_row.get("augmented_from")
        if parent_id is None:
            break  # root reached — always succeeds even at depth == _MAX_LINEAGE_DEPTH

        # ── Depth cap: only fires when root has NOT yet been reached. ─────────
        # The for-loop exhausts here only if we have appended _MAX_LINEAGE_DEPTH
        # entries and still have a non-null augmented_from. The `else` clause
        # below handles that case.

        # Fetch parent.
        try:
            parent_row = await asyncio.to_thread(_fetch_chunk, parent_id)
        except LanceQueryError as exc:
            raise HTTPException(status_code=400, detail=f"Lance query error: {exc}")

        if parent_row is None:
            # Parent referenced by augmented_from doesn't exist in Lance.
            # Treat as a broken chain — raise 500 (data integrity issue).
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Broken augmented_from chain: chunk {cid!r} references "
                    f"parent {parent_id!r} which does not exist in Lance."
                ),
            )

        current_row = parent_row
    else:
        # for-loop exhausted _MAX_LINEAGE_DEPTH iterations without a break —
        # root was not reached; the chain is too deep (or non-terminating).
        raise HTTPException(
            status_code=500,
            detail=(
                f"Lineage chain depth cap ({_MAX_LINEAGE_DEPTH}) exceeded "
                f"starting from chunk_id={chunk_id!r}; possible runaway "
                f"augmentation chain."
            ),
        )

    # root chunk = last entry in lineage_chain
    root_source_id: int | None = lineage_chain[-1].source_id

    # ── Step 3: Fetch source from Postgres (async SQLAlchemy) ─────────────────
    if root_source_id is None:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Root chunk {lineage_chain[-1].chunk_id!r} has null source_id; "
                f"cannot resolve source record."
            ),
        )

    source_result = await session.execute(
        select(Source).where(Source.id == root_source_id)
    )
    source_orm = source_result.scalar_one_or_none()

    if source_orm is None:
        raise HTTPException(
            status_code=404,
            detail=f"Source {root_source_id!r} not found in Postgres.",
        )

    source_read = SourceRead.model_validate(source_orm)

    # ── Step 4: Fetch canonical document_variant from Postgres ────────────────
    dv_result = await session.execute(
        select(DocumentVariant)
        .where(DocumentVariant.source_id == root_source_id)
        .where(DocumentVariant.is_canonical.is_(True))
        .limit(1)
    )
    dv_orm = dv_result.scalar_one_or_none()
    dv_read = DocumentVariantRead.model_validate(dv_orm) if dv_orm is not None else None

    # ── Step 5: Compose and return ────────────────────────────────────────────
    return ChunkLineageResponse(
        chunk=chunk_read,
        source=source_read,
        document_variant=dv_read,
        lineage_chain=lineage_chain,
    )
```

**Notes on the sketch:**

- `_fetch_chunk` uses `asyncio.to_thread()` wrapping synchronous Lance I/O
  (invariant: sync Lance I/O must never block the event loop).
- In the current sketch `_fetch_chunk` fetches all 24 columns (via `.select()`)
  to re-use the same function for the requested chunk and each chain step.
  The implementer may optimise to a smaller column projection for chain steps
  (only the 7 `ChunkLineageEntry` fields), provided the initial chunk fetch
  keeps all 24 columns for `ChunkRead`.
- The sketch issues one Lance round-trip per chain step. For MVP this is
  acceptable; a batch query (multiple `chunk_id IN (...)`) is a post-MVP
  optimisation once DataFusion `IN` predicate support is confirmed.
- Single-quote escaping is applied inside `_fetch_chunk` before every call.
- Both Postgres queries use `await session.execute(select(...))` — no
  `session.query()`, no sync sessions (invariant #5).

---

## 5. Edge Cases

### 5.1 Non-augmented chunk

`augmented_from` is `null` on the initial row. The loop appends 1 entry and
immediately breaks. `lineage_chain` length == 1. Satisfies V2.

### 5.2 Augmented chunk

Each iteration fetches the parent by `augmented_from`. Chain length ≥ 2.
The root is the first entry whose `augmented_from` is `null`. Satisfies V2.

### 5.3 Cycle in `augmented_from`

The `seen_ids` set detects if a `chunk_id` is revisited before reaching a
`null` augmented_from. Resolution: **HTTP 500** with a message identifying the
cycle entry point. Rationale: a cycle is a data-integrity bug, not a client
input error; 500 signals "something is wrong with the stored data."

### 5.4 Depth cap

`_MAX_LINEAGE_DEPTH = 32`. The traversal loop is `for _depth in range(32)`, so
at most 32 iterations run. Within each iteration the **root check
(`augmented_from is None`) executes before the loop can exhaust**, meaning a
legitimate chain whose 32nd (final) entry is the root will `break` successfully
and return **HTTP 200**. The depth cap only fires when all 32 iterations
complete without the `break` — i.e. the chain has at least 33 entries and no
root has been found — and is implemented via the `for…else` clause which raises
**HTTP 500** with a cap-exceeded message.

Key boundary invariant:
- Chain of exactly 32 entries, last entry is root → **HTTP 200** ✓  
- Chain of 33+ entries (or non-terminating, no null `augmented_from`) → **HTTP 500** ✓

> The cycle guard will fire first in true cycle cases (it checks the current
> entry's chunk_id before counting), so in practice the depth cap is a
> belt-and-suspenders guard for very long-but-not-cyclic chains.

### 5.5 `source_id` null on root chunk

If `lineage_chain[-1].source_id` is `null`, the source cannot be resolved.
Resolution: **HTTP 500** (data integrity — every production chunk must have a
`source_id`). 404 would imply the source row is absent; a null foreign key
field is structurally broken data.

### 5.6 `source_id` present but source row absent in Postgres

Resolution: **HTTP 404** with `detail=f"Source {root_source_id!r} not found"`.
Rationale: from the client's perspective the source "doesn't exist", matching
conventional 404 semantics for resource-not-found. A missing source row could
also indicate the source was deleted; 404 is the appropriate signal.

### 5.7 `document_variant` absent or no canonical variant

Resolution: return `"document_variant": null` in the response body. This is
not an error — a source may not yet have had a canonical variant assigned
(extraction in progress, or extraction skipped). Clients must null-check this
field.

### 5.8 `docling_refs` is a string path, not a FK

The `docling_refs` column is a string (e.g. `'{"ref": "page-1"}'`), not a
foreign key to `document_variant.id`. It cannot be used to resolve which
document variant produced this chunk. Resolution: use the `is_canonical` rule
(§ 2.2.3). See OQ-1.

### 5.9 Broken parent reference in `augmented_from`

If a non-null `augmented_from` value references a `chunk_id` that does not
exist in Lance, the handler raises **HTTP 500** with a message identifying the
dangling reference. This is a data-integrity issue (augmentation pipeline
should never write a child without the parent being present).

---

## 6. Tests Planned

File: `apps/api/tests/test_chunks_lineage.py` (new file).

Mock pattern: `patch("dataplat_api.routers.chunks.get_or_create_chunks_table")`
for Lance; `patch("dataplat_api.routers.chunks.get_session")` (or AsyncMock of
the session) for Postgres. Follow the TestClient + `app.dependency_overrides`
pattern established in `test_chunks_get_by_id.py`.

| # | Test name | Intent |
|---|-----------|--------|
| 1 | `test_lineage_200_non_augmented_chain_length_1` | Non-augmented chunk → 200, `lineage_chain` length == 1, `chunk_id` in chain, `augmented_from` is null; `document_variant` may be null. **(satisfies V1 + V2-non-augmented)** |
| 2 | `test_lineage_200_augmented_chain_length_3` | Chunk C augmented from B augmented from A (A is root) → 200, `lineage_chain == [C, B, A]` (tip-to-root order), `lineage_chain[-1].augmented_from` is null. **(satisfies V2-augmented)** |
| 3 | `test_lineage_200_response_has_required_top_level_keys` | Response body contains keys `chunk`, `source`, `document_variant`, `lineage_chain`. **(satisfies V1 shape)** |
| 4 | `test_lineage_200_source_and_dv_fields_present` | `source.id` and `document_variant.id` present in body when source + canonical variant exist. |
| 5 | `test_lineage_404_chunk_not_found` | Lance returns empty list for requested `chunk_id` → 404. |
| 6 | `test_lineage_401_no_token` | No `Authorization` header → 401 (no `get_current_user` override). |
| 7 | `test_lineage_400_lance_error` | `get_or_create_chunks_table` raises `Exception` → HTTP 400 with `"Lance query error"` in detail. |
| 8 | `test_lineage_500_cycle_detected` | Lance returns chunk A with `augmented_from='B'`, chunk B with `augmented_from='A'` → 500 with `"Cycle detected"` in detail. |
| 9a | `test_lineage_200_depth_boundary_32_chain_succeeds` | Mock returns a chain of exactly 32 distinct chunks (entries 0–30 have non-null `augmented_from`; entry 31 is the root with `augmented_from=None`) → **HTTP 200**, `lineage_chain` length == 32. Verifies that a max-depth valid chain is not falsely rejected. |
| 9b | `test_lineage_500_depth_cap_exceeded` | Mock returns a chain of 33+ distinct chunks (no cycle, no null `augmented_from` within the first 32 iterations) → **HTTP 500** with cap-exceeded message in detail. Verifies the `for…else` branch fires. |
| 10 | `test_lineage_404_source_not_found_in_postgres` | Lance returns valid chain; Postgres `Source` query returns `None` → 404 with `"not found"` in source detail. |
| 11 | `test_lineage_200_null_document_variant_when_no_canonical` | Source exists in Postgres but no canonical variant → 200 with `"document_variant": null`. |
| 12 | `test_lineage_escapes_single_quote_in_chunk_id` | `chunk_id = "it's"` → `.where()` called with `"chunk_id = 'it''s'"`. |
| 13 | `test_lineage_500_broken_augmented_from_chain` | Chunk A has `augmented_from='B'`; second `_fetch_chunk` call (for B) returns `None` → **HTTP 500** with `"Broken augmented_from chain"` in detail. Covers §5.9 mid-traversal missing parent branch. |
| 14 | `test_lineage_500_null_source_id_on_root_chunk` | Root chunk has `source_id=None` (`augmented_from=None`) → **HTTP 500** with `"null source_id"` in detail. Covers §5.5 data-integrity sentinel. |

---

## 7. OpenAPI / codegen

After implementation, the implementer **MUST** run:

```bash
make codegen
```

This regenerates `packages/api-types/openapi.json`. The diff from `make codegen`
(adding the `/api/chunks/{chunk_id}/lineage` path, `ChunkLineageEntry`,
`ChunkLineageResponse`, and any newly exported schemas) **MUST** be committed
in the **same Git commit** as the implementation changes. CI will reject any
mismatch between the OpenAPI spec and the TypeScript types (invariant #6).

---

## 8. Verification Mapping

| Spec verification criterion | Covered by test(s) |
|-----------------------------|--------------------|
| **V1:** `GET /api/chunks/{id}/lineage` returns `200` with `{"chunk": {...}, "source": {"id": ...}, "document_variant": {"id": ...}, "lineage_chain": [...]}` | `test_lineage_200_response_has_required_top_level_keys` + `test_lineage_200_source_and_dv_fields_present` + `test_lineage_200_non_augmented_chain_length_1` |
| **V2:** Non-augmented → chain length 1; augmented → chain traces to root original | `test_lineage_200_non_augmented_chain_length_1` + `test_lineage_200_augmented_chain_length_3` |

---

## 9. Open Questions

**OQ-1 — Document variant resolution rule.**
The Lance `chunks` schema stores `docling_refs` as a freeform string (NodeItem
path inside the DoclingDocument JSON), not a FK to `document_variant.id`. There
is no direct join path from a chunk row to a specific variant row. This proposal
uses "the canonical variant for the root chunk's `source_id`" as the resolution
rule (leveraging the `idx_doc_canonical` partial unique index). If the intent is
to resolve the *specific* variant that was used during chunking (e.g. the
`storage_prefix` that was read), we would need to either (a) add a
`document_variant_id` column to `CHUNKS_SCHEMA` in a future sprint, or (b) look
up by extractor name embedded in `storage_prefix` heuristics. For MVP, canonical
variant is the pragmatic choice. **Reviewer: please confirm this rule is
acceptable, or suggest an alternative.**

**OQ-2 — `_fetch_chunk` column projection for chain steps.**
The sketch fetches all 24 columns for every chain step so a single helper
function can serve both the initial `ChunkRead` construction and the
`ChunkLineageEntry` construction. The implementer could alternatively use a
7-column projection for chain steps (after the first). This is a performance
vs. simplicity tradeoff; for MVP, simplicity (one function) is preferred unless
the reviewer or verifier flags it.

**OQ-3 — Batch vs. iterative Lance traversal.**
The sketch issues one Lance query per chain step. For chains ≤ 32 deep this is
acceptable at MVP scale. A single `chunk_id IN (...)` batch query would require
fetching all `augmented_from` values first and then resolving in one shot, but
DataFusion `IN` predicate support on string columns in LanceDB should be
confirmed before relying on it. Iterative traversal is safer. **Reviewer:
confirm iterative is acceptable for MVP.**

**OQ-4 — Route ordering: `/{chunk_id}/lineage` vs `/{chunk_id}`.**
`GET /{chunk_id}/lineage` (two path segments after the router prefix) and
`GET /{chunk_id}` (one path segment) differ in segment count. Starlette/FastAPI
path-matching is structural: a two-segment path is never confused with a
one-segment path regardless of handler registration order. The routing concern
raised here is a **non-issue** for this specific pair of routes.

For clarity and readability, the `get_chunk_lineage` handler should still be
defined before `get_chunk_by_id` in `routers/chunks.py` source order, but this
is a style preference only — NOT a correctness requirement. The implementer does
**not** need to treat registration order as safety-critical here.

**OQ-5 — HTTP status for broken `augmented_from` reference.**
§5.9 proposes HTTP 500 for a non-null `augmented_from` that resolves to a
missing Lance row. An alternative is HTTP 422 (unprocessable data) or a
specialised 409. 500 is chosen here because the caller provided a valid
`chunk_id`; the data corruption is server-side. **Reviewer: confirm 500 is
preferred over an alternative status code.**
