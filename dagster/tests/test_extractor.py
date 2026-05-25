"""Unit tests for dagster_platform/extractor.py pure helpers (F-019).

These tests run INSIDE the dagster-webserver container (which has docling-core
installed) via:
    docker compose exec -T dagster-webserver python -m pytest /app/dagster/tests/test_extractor.py -q

They do NOT run in the backend (apps/api) pytest layer — docling-core is not
in that venv. See agreed.md §2a for the test-execution environment rationale.
"""

from __future__ import annotations

import hashlib
import json


# ---------------------------------------------------------------------------
# (a) config_hash constant
# ---------------------------------------------------------------------------


def test_config_hash_constant() -> None:
    """CONFIG_HASH must equal sha256 of canonical JSON of empty dict."""
    from dagster_platform.extractor import CONFIG_HASH

    expected = hashlib.sha256(
        json.dumps({}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert CONFIG_HASH == expected
    assert CONFIG_HASH == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


# ---------------------------------------------------------------------------
# (b) estimate_page_count
# ---------------------------------------------------------------------------

# Synthetic 1-page PDF matching the fixture used in checks.sh
_SYNTHETIC_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n"
    b"3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\n"
)


def test_estimate_page_count_synthetic_pdf() -> None:
    """Synthetic 1-page PDF should return 1."""
    from dagster_platform.extractor import estimate_page_count

    assert estimate_page_count(_SYNTHETIC_PDF) == 1


def test_estimate_page_count_garbage() -> None:
    """Garbage bytes (no /Count field) should return 0, not raise."""
    from dagster_platform.extractor import estimate_page_count

    assert estimate_page_count(b"not a pdf at all") == 0


def test_estimate_page_count_empty() -> None:
    """Empty bytes should return 0, not raise."""
    from dagster_platform.extractor import estimate_page_count

    assert estimate_page_count(b"") == 0


# ---------------------------------------------------------------------------
# (c) build_docling_document shape
# ---------------------------------------------------------------------------


def test_build_docling_document_schema_name() -> None:
    """Serialized JSON must have schema_name == 'DoclingDocument'."""
    from dagster_platform.extractor import build_docling_document

    result = build_docling_document(source_id=42, pdf_bytes=b"%PDF-1.4 minimal", page_count=0)
    data = json.loads(result)
    assert data["schema_name"] == "DoclingDocument"


def test_build_docling_document_name() -> None:
    """Document name must be 'source_{source_id}'."""
    from dagster_platform.extractor import build_docling_document

    result = build_docling_document(source_id=42, pdf_bytes=b"%PDF-1.4 minimal", page_count=0)
    data = json.loads(result)
    assert data["name"] == "source_42"


def test_build_docling_document_no_binary_hash() -> None:
    """origin must be null — DocumentOrigin/binary_hash dropped per §4."""
    from dagster_platform.extractor import build_docling_document

    result = build_docling_document(source_id=42, pdf_bytes=b"%PDF-1.4 minimal", page_count=0)
    data = json.loads(result)
    # origin is serialised as null when no DocumentOrigin is supplied
    assert data.get("origin") is None


def test_build_docling_document_empty_pages_when_zero() -> None:
    """page_count=0 must produce an empty pages dict (still schema-valid)."""
    from dagster_platform.extractor import build_docling_document

    result = build_docling_document(source_id=1, pdf_bytes=b"x", page_count=0)
    data = json.loads(result)
    assert data["pages"] == {}


def test_build_docling_document_pages_populated() -> None:
    """page_count=2 must produce pages with keys '1' and '2'."""
    from dagster_platform.extractor import build_docling_document

    result = build_docling_document(source_id=5, pdf_bytes=_SYNTHETIC_PDF, page_count=2)
    data = json.loads(result)
    assert set(data["pages"].keys()) == {"1", "2"}
    assert data["pages"]["1"]["page_no"] == 1
    assert data["pages"]["2"]["page_no"] == 2
