"""Unit tests for dagster_platform.minhash_tagger (F-030).

Tests the pure helper functions: _compute_signature and _cluster_rows.
No Docker, no LanceDB, no network required — all tests run in-process.

All 9 required test cases per agreed.md §2.8 / §4:
  1. test_compute_signature_deterministic
  2. test_compute_signature_empty_text
  3. test_compute_signature_length
  4. test_cluster_rows_all_unique
  5. test_cluster_rows_near_duplicates
  6. test_cluster_rows_head_election
  7. test_cluster_rows_idempotent
  8. test_cluster_rows_order_invariant   (F5 fix)
  9. test_cluster_rows_empty_list
"""

from __future__ import annotations

import pytest

# Import from the module under test.
from dagster_platform.minhash_tagger import _cluster_rows, _compute_signature


# ---------------------------------------------------------------------------
# _compute_signature tests
# ---------------------------------------------------------------------------


def test_compute_signature_deterministic() -> None:
    """Same text produces identical signature on two separate calls.

    MinHash with fixed num_perm=128 uses a seeded hash function, so the
    signature is deterministic across calls within the same process.
    """
    text = "the quick brown fox jumps over the lazy dog near the river bank"
    sig1 = _compute_signature(text)
    sig2 = _compute_signature(text)
    assert sig1 == sig2, "Signature is not deterministic for the same text"


def test_compute_signature_empty_text() -> None:
    """Empty string and whitespace-only string do not raise; return 128-element lists.

    Agreed.md D6: empty/whitespace text → MinHash of empty shingle set.
    Deterministic and well-defined — no special-case branch.
    """
    sig_empty = _compute_signature("")
    sig_ws = _compute_signature("   \t\n  ")
    assert isinstance(sig_empty, list)
    assert isinstance(sig_ws, list)
    assert len(sig_empty) == 128
    assert len(sig_ws) == 128
    # Both are MinHash of an empty shingle set — they should be identical.
    assert sig_empty == sig_ws


def test_compute_signature_length() -> None:
    """Signature is always exactly 128 elements regardless of text content."""
    for text in [
        "",
        "one",
        "one two",
        "one two three",
        "a b c d e f g h i j k l m n o p q r s t u v w x y z",
        "x" * 10000,
    ]:
        sig = _compute_signature(text)
        assert isinstance(sig, list), f"Expected list, got {type(sig)}"
        assert len(sig) == 128, f"Expected 128 elements, got {len(sig)} for text={text!r}"
        # All values should be plain Python ints (not numpy scalars).
        assert all(isinstance(v, int) for v in sig), (
            "Signature contains non-int values (numpy scalars must be converted)"
        )


# ---------------------------------------------------------------------------
# _cluster_rows tests
# ---------------------------------------------------------------------------

# Shared helper for building test row dicts.
def _row(chunk_id: str, text: str) -> dict:
    return {"chunk_id": chunk_id, "text": text}


def test_cluster_rows_all_unique() -> None:
    """N rows with completely distinct text → N distinct cluster_ids, each is_head=True.

    With threshold=0.85 and word 3-gram shingles, texts with entirely disjoint
    vocabularies have Jaccard ≈ 0.0 and are never placed in the same cluster.
    """
    rows = [
        _row("c1", "alpha beta gamma delta epsilon zeta eta theta"),
        _row("c2", "apple orange banana mango kiwi melon grape cherry"),
        _row("c3", "table chair desk lamp sofa window curtain mirror"),
        _row("c4", "python java ruby swift kotlin scala haskell erlang"),
    ]
    sorted_rows = sorted(rows, key=lambda r: r["chunk_id"])
    result = _cluster_rows(sorted_rows)

    cluster_ids = [r["attr_minhash_cluster_id"] for r in result]
    is_heads = [r["attr_minhash_is_head"] for r in result]

    assert len(set(cluster_ids)) == 4, (
        f"Expected 4 distinct cluster IDs, got {set(cluster_ids)}"
    )
    assert all(is_heads), (
        "All rows should be heads when every row is its own singleton cluster"
    )


def test_cluster_rows_near_duplicates() -> None:
    """Two rows with identical text → same cluster_id, exactly one is_head=True.

    Identical text → Jaccard = 1.0 > threshold(0.85) → LSH buckets them together
    → union-find merges them → one cluster. Lex-lowest chunk_id is head.
    """
    text = "the quick brown fox jumps over the lazy dog near the river and the bridge"
    rows = [
        _row("chunk_b", text),
        _row("chunk_a", text),
    ]
    sorted_rows = sorted(rows, key=lambda r: r["chunk_id"])
    result = _cluster_rows(sorted_rows)

    by_id = {r["chunk_id"]: r for r in result}
    assert by_id["chunk_a"]["attr_minhash_cluster_id"] == by_id["chunk_b"]["attr_minhash_cluster_id"], (
        "Identical texts must share the same cluster_id"
    )
    heads = [r for r in result if r["attr_minhash_is_head"]]
    assert len(heads) == 1, f"Expected exactly 1 head, got {len(heads)}: {heads}"
    # Lex-lowest chunk_id ("chunk_a" < "chunk_b") must be the head.
    assert heads[0]["chunk_id"] == "chunk_a", (
        f"Expected 'chunk_a' as head (lex-lowest), got {heads[0]['chunk_id']!r}"
    )


def test_cluster_rows_head_election() -> None:
    """Three rows in the same cluster → head is the lexicographically smallest chunk_id.

    Uses identical text so all three rows cluster together (Jaccard=1.0).
    chunk_ids are "z_chunk", "a_chunk", "m_chunk" — lex order is a < m < z,
    so "a_chunk" must be elected head regardless of insertion order.
    """
    text = (
        "duplicated content here and there and everywhere in this test case "
        "for head election verification purposes and correctness"
    )
    rows = [
        _row("z_chunk", text),
        _row("a_chunk", text),
        _row("m_chunk", text),
    ]
    sorted_rows = sorted(rows, key=lambda r: r["chunk_id"])
    result = _cluster_rows(sorted_rows)

    by_id = {r["chunk_id"]: r for r in result}

    # All in the same cluster.
    labels = {r["attr_minhash_cluster_id"] for r in result}
    assert len(labels) == 1, f"Expected 1 cluster, got {len(labels)}"

    # Exactly one head.
    heads = [r for r in result if r["attr_minhash_is_head"]]
    assert len(heads) == 1, f"Expected exactly 1 head, got {len(heads)}"

    # Head is lex-smallest.
    assert heads[0]["chunk_id"] == "a_chunk", (
        f"Expected 'a_chunk' as head (lex-smallest), got {heads[0]['chunk_id']!r}"
    )

    # Others are not heads.
    assert not by_id["m_chunk"]["attr_minhash_is_head"]
    assert not by_id["z_chunk"]["attr_minhash_is_head"]


def test_cluster_rows_idempotent() -> None:
    """Running _cluster_rows twice on the same (sorted) input gives identical output.

    Agreed.md: stable labels guaranteed by sort-then-cluster approach.
    """
    rows = [
        _row("c1", "identical text content here for idempotency test case checking"),
        _row("c2", "identical text content here for idempotency test case checking"),
        _row("c3", "completely separate and unrelated text for this particular chunk"),
    ]
    sorted_rows = sorted(rows, key=lambda r: r["chunk_id"])

    result1 = _cluster_rows(sorted_rows)
    result2 = _cluster_rows(sorted_rows)

    assignments1 = {
        r["chunk_id"]: (r["attr_minhash_cluster_id"], r["attr_minhash_is_head"])
        for r in result1
    }
    assignments2 = {
        r["chunk_id"]: (r["attr_minhash_cluster_id"], r["attr_minhash_is_head"])
        for r in result2
    }
    assert assignments1 == assignments2, (
        "_cluster_rows is not idempotent — second call produced different assignments"
    )


def test_cluster_rows_order_invariant() -> None:
    """Sort-then-cluster is order-independent: different input orderings → same output.

    F5 fix: creates two lists with the same rows in different order, sorts each by
    chunk_id (simulating update_minhash_in_lance), and verifies that the resulting
    cluster_id and is_head assignments are identical keyed by chunk_id.
    """
    text = "shared text content for order invariance test across multiple different rows"
    rows_fwd = [
        _row("z_chunk_003", text),
        _row("a_chunk_001", text),
        _row("m_chunk_002", text),
    ]
    rows_bwd = list(reversed(rows_fwd))

    # Simulate update_minhash_in_lance: sort by chunk_id before clustering.
    sorted_fwd = sorted(rows_fwd, key=lambda r: r["chunk_id"])
    sorted_bwd = sorted(rows_bwd, key=lambda r: r["chunk_id"])

    result_fwd = {
        r["chunk_id"]: (r["attr_minhash_cluster_id"], r["attr_minhash_is_head"])
        for r in _cluster_rows(sorted_fwd)
    }
    result_bwd = {
        r["chunk_id"]: (r["attr_minhash_cluster_id"], r["attr_minhash_is_head"])
        for r in _cluster_rows(sorted_bwd)
    }

    assert result_fwd == result_bwd, (
        "Cluster assignments differ between forward and backward input orderings "
        f"after sort-then-cluster. fwd={result_fwd}, bwd={result_bwd}"
    )


def test_cluster_rows_empty_list() -> None:
    """Empty input list returns empty list — no crash, no exception."""
    result = _cluster_rows([])
    assert result == [], f"Expected empty list for empty input, got {result!r}"
