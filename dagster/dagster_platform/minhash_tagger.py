"""minhash_tagger.py — Pure helpers for the attr_minhash Dagster asset (F-030).

All functions are side-effect-free where possible, or have clearly bounded I/O.
Keeping these out of definitions.py makes unit-testing straightforward.

Design notes (agreed.md §3):
- No Dagster imports — same no-Dagster guarantee as lang_tagger.py, quality_tagger.py,
  chunker.py, and extractor.py. Allows fast unit tests without a Dagster runtime.
- MinHash is purely algorithmic — CLAUDE.md invariant #4 (LLM gateway) does not apply.
- Column-mode update: ZERO new rows. Updates attr_minhash_signature,
  attr_minhash_cluster_id, and attr_minhash_is_head columns on existing
  producer_asset='chunks' rows only. Does NOT modify lineage fields
  (augmented_from, augmenter_id, etc.) — taggers are NOT augmenters.
- DB access via raw lancedb (already in the Dagster image).
- Two-phase processing (agreed.md D2): all rows for a source are fetched once,
  sorted by chunk_id ascending, clustering computed in-memory, then per-row
  table.update() calls issued. Batch operation — cannot assign cluster IDs row-by-row.
- Sentinel behaviour (agreed.md D6): empty/whitespace text receives the MinHash of
  the empty shingle set — treated as a full first-class row. Multiple empty-text
  chunks cluster together (Jaccard=1.0 on empty sets); lex-lowest chunk_id is head.
- 128 permutations, Jaccard threshold 0.85, word-level 3-grams (agreed.md D3):
    * 128 permutations matches pa.list_(pa.uint64()) in CHUNKS_SCHEMA.
    * Threshold 0.85 — aggressive enough to catch near-duplicates, conservative
      enough to avoid false positives on merely similar content.
    * Word 3-grams are robust to minor punctuation and casing variation.
- Head election: lexicographically-smallest chunk_id in each cluster (agreed.md D4).
- No cross-source clustering (agreed.md D5): each source partition is processed
  independently.
- Signature stored as list(minhash.hashvalues.tolist()) — .tolist() converts NumPy
  uint64 scalars to plain Python int before building the list, which PyArrow coerces
  safely to pa.list_(pa.uint64()) on write (agreed.md D8).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import lancedb  # type: ignore[import-untyped]
from datasketch import MinHash, MinHashLSH  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lance storage helpers (same pattern as lang_tagger.py)
# ---------------------------------------------------------------------------


def _build_lance_storage_options() -> dict[str, str]:
    """Build S3-compatible storage_options dict for lancedb.connect().

    Reads MINIO_* from os.environ (same pattern as lang_tagger.py).
    """
    return {
        "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
        "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
        "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
        "aws_region":            "us-east-1",
        "allow_http":            "true",
    }


# ---------------------------------------------------------------------------
# MinHash helper (internal — shared by _compute_signature and _cluster_rows)
# ---------------------------------------------------------------------------


def _build_minhash_from_text(text: str) -> Any:
    """Build a MinHash(num_perm=128) from word-level 3-gram shingles.

    Lowercases text and splits on whitespace. For empty/whitespace input,
    no shingles are added and the MinHash of the empty set is returned —
    deterministic and well-defined (agreed.md D6).

    Args:
        text: The chunk text. May be None, empty, or whitespace-only.

    Returns:
        A datasketch.MinHash object with 128 permutations.
    """
    m = MinHash(num_perm=128)
    words = text.lower().split() if text else []
    # Sliding window of 3 words → word-level 3-gram shingles.
    for i in range(len(words) - 2):
        shingle = " ".join(words[i : i + 3])
        m.update(shingle.encode("utf-8"))
    return m


# ---------------------------------------------------------------------------
# MinHash signature computation (agreed.md D3, D8)
# ---------------------------------------------------------------------------


def _compute_signature(text: str) -> list[int]:
    """Compute a 128-permutation MinHash signature for the given text.

    Shingles are word-level 3-grams (whitespace-split trigrams on lowercased text).
    For empty/whitespace text, returns the MinHash of an empty shingle set —
    deterministic and well-defined (agreed.md D6).

    Returns list(minhash.hashvalues.tolist()) — plain Python list[int].
    The .tolist() call converts NumPy uint64 scalars to plain Python int,
    which PyArrow coerces safely to pa.list_(pa.uint64()) on write (agreed.md D8).

    Args:
        text: The chunk text. May be empty or whitespace-only.

    Returns:
        A list of exactly 128 int values (the MinHash signature).
    """
    return list(_build_minhash_from_text(text).hashvalues.tolist())


# ---------------------------------------------------------------------------
# Union-Find for connected-component (transitive) clustering
# ---------------------------------------------------------------------------


def _make_union_find(
    keys: list[str],
) -> tuple[dict[str, str], dict[str, int]]:
    """Initialise a union-find structure keyed by strings.

    Returns (parent, rank) dicts — both keyed by the same strings.
    """
    parent = {k: k for k in keys}
    rank: dict[str, int] = {k: 0 for k in keys}
    return parent, rank


def _find(parent: dict[str, str], x: str) -> str:
    """Iterative find with path compression (no recursion depth risk)."""
    # Walk to root.
    root = x
    while parent[root] != root:
        root = parent[root]
    # Path compression: make every node on the path point directly to root.
    current = x
    while current != root:
        nxt = parent[current]
        parent[current] = root
        current = nxt
    return root


def _union(
    parent: dict[str, str], rank: dict[str, int], a: str, b: str
) -> None:
    """Union by rank."""
    ra, rb = _find(parent, a), _find(parent, b)
    if ra == rb:
        return
    if rank[ra] < rank[rb]:
        ra, rb = rb, ra
    parent[rb] = ra
    if rank[ra] == rank[rb]:
        rank[ra] += 1


# ---------------------------------------------------------------------------
# Clustering (agreed.md D2, D3, D4)
# ---------------------------------------------------------------------------


def _cluster_rows(rows: list[dict]) -> list[dict]:
    """Cluster rows by MinHash LSH near-duplicate detection.

    Expects rows **already sorted by chunk_id ascending** (agreed.md F3 fix).
    Computes a 128-permutation MinHash signature for each row, inserts into
    MinHashLSH(threshold=0.85), then uses union-find to compute the transitive
    connected-component closure (LSH gives pairwise neighbours; union-find
    resolves transitivity).

    Cluster label assignment:
    - Each unique root in the union-find gets a 0-based integer label, assigned
      in the order its root is first encountered while walking chunk_ids in sorted
      order — guarantees deterministic, stable integer labels across re-runs.

    Head election:
    - Within each cluster, the lexicographically-smallest chunk_id is the head
      (attr_minhash_is_head=True). All others are False.

    Args:
        rows: List of dicts with at minimum {"chunk_id": str, "text": str|None}.
              Must be sorted by chunk_id ascending before this call.

    Returns:
        List of input rows augmented with three new keys:
            "attr_minhash_signature"    — list[int] of length 128
            "attr_minhash_cluster_id"   — int (0-based cluster label)
            "attr_minhash_is_head"      — bool
        Empty input → empty list (no crash).
    """
    if not rows:
        return []

    chunk_ids = [row["chunk_id"] for row in rows]

    # Phase 1: Build MinHash objects and capture list[int] signatures in one pass.
    minhashes: dict[str, Any] = {}
    sig_values: dict[str, list[int]] = {}
    for row in rows:
        cid = row["chunk_id"]
        text = row.get("text") or ""
        m = _build_minhash_from_text(text)
        minhashes[cid] = m
        sig_values[cid] = list(m.hashvalues.tolist())

    # Phase 2: Build LSH index.
    lsh = MinHashLSH(threshold=0.85, num_perm=128)
    for cid in chunk_ids:
        lsh.insert(cid, minhashes[cid])

    # Phase 3: Query LSH for pairwise neighbours, union-find for transitive closure.
    parent, rank = _make_union_find(chunk_ids)
    for cid in chunk_ids:
        for neighbour in lsh.query(minhashes[cid]):
            if neighbour != cid:
                _union(parent, rank, cid, neighbour)

    # Phase 4: Assign 0-based integer cluster labels in sorted chunk_id order.
    root_to_label: dict[str, int] = {}
    next_label = 0
    chunk_to_label: dict[str, int] = {}
    for cid in chunk_ids:
        root = _find(parent, cid)
        if root not in root_to_label:
            root_to_label[root] = next_label
            next_label += 1
        chunk_to_label[cid] = root_to_label[root]

    # Phase 5: Head election — lex-smallest chunk_id in each cluster.
    cluster_to_head: dict[int, str] = {}
    for cid in chunk_ids:
        label = chunk_to_label[cid]
        if label not in cluster_to_head or cid < cluster_to_head[label]:
            cluster_to_head[label] = cid

    # Phase 6: Augment each row with the three new columns.
    result = []
    for row in rows:
        cid = row["chunk_id"]
        label = chunk_to_label[cid]
        result.append(
            {
                **row,
                "attr_minhash_signature": sig_values[cid],
                "attr_minhash_cluster_id": label,
                "attr_minhash_is_head": (cid == cluster_to_head[label]),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Lance column-mode update
# ---------------------------------------------------------------------------


def compute_minhash_scores(source_id: int) -> list[dict]:
    """Read chunk_id + text from Lance, compute MinHash signatures and clusters.

    Fetches ALL rows for the source (required by batch clustering), sorts by
    chunk_id ascending for deterministic cluster label assignment, runs
    _cluster_rows(), and returns partial dicts.

    Returns partial dicts suitable for LanceChunksIOManager column mode (F-031).
    Does NOT write to Lance — the IOManager handles the write.

    Args:
        source_id: The source to process.

    Returns:
        List of dicts: [{"chunk_id": str, "attr_minhash_signature": list[int],
        "attr_minhash_cluster_id": int, "attr_minhash_is_head": bool}, ...].
        Returns an empty list if no chunks exist for this source.
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    table = db.open_table("chunks")

    where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
    rows = (
        table.search()
        .where(where_clause)
        .select(["chunk_id", "text"])
        .to_list()
    )
    if not rows:
        logger.info(
            "compute_minhash_scores: no rows found for source_id=%d", source_id
        )
        return []

    # Sort by chunk_id ascending for canonical, deterministic cluster labels.
    rows_sorted = sorted(rows, key=lambda r: r["chunk_id"])
    clustered = _cluster_rows(rows_sorted)

    return [
        {
            "chunk_id": r["chunk_id"],
            "attr_minhash_signature": r["attr_minhash_signature"],
            "attr_minhash_cluster_id": r["attr_minhash_cluster_id"],
            "attr_minhash_is_head": r["attr_minhash_is_head"],
        }
        for r in clustered
    ]


def update_minhash_in_lance(source_id: int) -> int:
    """Update attr_minhash_signature, attr_minhash_cluster_id, attr_minhash_is_head.

    .. deprecated::
        F-031: Use ``compute_minhash_scores(source_id)`` and route the result
        through ``LanceChunksIOManager`` (column mode) instead.  This function
        is kept for backward compatibility but is no longer called from Dagster
        tagger assets.

    Performs a **column-mode update** on existing rows where:
        source_id = <source_id> AND producer_asset = 'chunks'

    Zero new rows are created. Lineage fields (augmented_from, augmenter_id,
    augmenter_config_hash, producer_asset, producer_version) are left untouched.

    Processing steps (agreed.md D2):
    1. Fetch chunk_id and text for all matching rows.
    2. Sort fetched rows by chunk_id ascending (canonical order for stable labels).
    3. Call _cluster_rows() to compute signatures and cluster assignments.
    4. Issue per-row table.update() to write the three columns.
    5. Return table.count_rows(where_clause) — same predicate, same pattern as
       lang_tagger.py line 142.

    Idempotency: re-running overwrites the same three columns — no row count change.
    Stable labels: the sort-before-cluster guarantee ensures cluster_id assignments
    are identical on every re-run for the same set of chunks.

    Uses the same per-row table.update(where=..., values=...) pattern as
    quality_tagger.py and lang_tagger.py. merge_insert is NOT used — lancedb
    0.30.2 when_matched_update_all() without updates= kwarg replaces the entire
    row, destroying lineage fields.

    Args:
        source_id: The source to process.

    Returns:
        Number of rows matched by the WHERE clause (zero if no chunks exist —
        caller logs a warning). Row count is checked AFTER the update.
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    # Open existing table — do NOT create; chunks must already exist.
    table = db.open_table("chunks")

    where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
    _minhash_update(table, source_id, where_clause)

    row_count: int = table.count_rows(where_clause)
    return row_count


def _minhash_update(
    table: Any,
    source_id: int,
    where_clause: str,
) -> None:
    """MinHash clustering column update: fetch → sort → cluster → update.

    .. deprecated::
        F-031: Internal helper for the deprecated ``update_minhash_in_lance()``.
        New code should use ``compute_minhash_scores()`` and route through
        ``LanceChunksIOManager`` column mode.

    Fetches chunk_id and text for matching rows, sorts by chunk_id ascending,
    computes MinHash signatures and cluster assignments via _cluster_rows(), then
    calls table.update() once per row to overwrite attr_minhash_signature,
    attr_minhash_cluster_id, and attr_minhash_is_head (keyed on chunk_id).
    Zero new rows; all other columns are untouched.

    Args:
        table:        An open lancedb Table object.
        source_id:    The source being processed (used only in log messages).
        where_clause: SQL WHERE clause identifying rows to update.
    """
    rows = (
        table.search()
        .where(where_clause)
        .select(["chunk_id", "text"])
        .to_list()
    )
    if not rows:
        logger.info(
            "_minhash_update: no rows found for source_id=%d — skipping",
            source_id,
        )
        return

    # Sort by chunk_id ascending for canonical, deterministic cluster labels (F3 fix).
    rows_sorted = sorted(rows, key=lambda r: r["chunk_id"])

    clustered = _cluster_rows(rows_sorted)

    for row in clustered:
        chunk_id: str = row["chunk_id"]
        table.update(
            where=f"chunk_id = '{chunk_id}'",
            values={
                "attr_minhash_signature":  row["attr_minhash_signature"],
                "attr_minhash_cluster_id": row["attr_minhash_cluster_id"],
                "attr_minhash_is_head":    row["attr_minhash_is_head"],
            },
        )
