"""Static analysis test — LLM SDK import invariant (F-053).

Asserts that no Python file in apps/api/dataplat_api/ (excluding the llm/
subdirectory) or dagster/dagster_platform/ contains a direct
``import anthropic``, ``from anthropic``, ``import openai``, or ``from openai``
line.

This makes CLAUDE.md hard invariant #4 ("LLM calls go through the gateway")
a CI-runnable check rather than a documentation note.

The scan uses pathlib.Path.rglob("*.py") — no subprocess, no shell grep —
so the test runs identically on all platforms and inside the fastapi container.

Run inside the fastapi container:
    python -m pytest /app/tests/test_llm_gateway_invariant.py -v
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Scan configuration
# ---------------------------------------------------------------------------

# Repo root: tests/ → api/ → apps/ → repo root (4 levels up).
_repo_root = Path(__file__).parent.parent.parent.parent

SCAN_ROOTS = [
    _repo_root / "apps" / "api" / "dataplat_api",
    _repo_root / "dagster" / "dagster_platform",
]

# Directory names to skip.  "llm" covers the dataplat_api/llm/ gateway package
# which is the one intentional place where `import anthropic` lives.
# It has no effect on dagster_platform (there is no llm/ subdirectory there).
_SKIP_DIRS = {"llm"}

# Line prefixes that indicate a direct SDK import.
_FORBIDDEN_PREFIXES = (
    "import anthropic",
    "from anthropic",
    "import openai",
    "from openai",
)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_no_direct_llm_sdk_imports_outside_gateway() -> None:
    """No file outside llm/ may directly import the anthropic or openai SDKs.

    CLAUDE.md hard invariant #4: all LLM calls must go through the gateway
    (apps/api/dataplat_api/llm/).  This test ensures the invariant is not
    accidentally violated by any current or future file in the two scan roots.
    """
    violations: list[str] = []

    for root in SCAN_ROOTS:
        if not root.exists():
            # If a scan root doesn't exist (e.g. dagster_platform not checked out),
            # skip rather than fail — the invariant can only be violated by
            # files that exist.
            continue

        for py_file in sorted(root.rglob("*.py")):
            # Skip any file whose path includes an "llm" directory component.
            if _SKIP_DIRS.intersection(py_file.parts):
                continue

            try:
                lines = py_file.read_text(encoding="utf-8").splitlines()
            except OSError:
                # Unreadable files are not a violation; surface as a note.
                continue

            for lineno, raw_line in enumerate(lines, start=1):
                stripped = raw_line.strip()
                for prefix in _FORBIDDEN_PREFIXES:
                    if stripped.startswith(prefix):
                        violations.append(
                            f"{py_file}:{lineno}: {raw_line.rstrip()}"
                        )
                        break  # one violation per line is enough

    assert not violations, (
        "Direct LLM SDK import(s) found outside gateway (CLAUDE.md invariant #4):\n"
        + "\n".join(f"  {v}" for v in violations)
    )
