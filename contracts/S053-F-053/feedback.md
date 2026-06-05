# S053-F-053 Mode A Feedback

VERDICT: CHANGES_REQUESTED

## Findings (numbered)

F1. **T-INV grep scope is under-broad — `dagster/dagster_platform/` not covered (blocking)**

   The T-INV test walks `apps/api/dataplat_api/` only. CLAUDE.md hard invariant #4
   reads: "Never call Anthropic/OpenAI/etc. SDKs directly from a processor, adapter,
   or random route." Processors live in `dagster/dagster_platform/` — e.g.
   `quality_tagger.py`, `hf_dataset_io_manager.py`. Those files are currently clean
   (they call via HTTP), but a future sprint could add `import anthropic` there and
   T-INV would not catch it. The F-053 spec V3 clause ("No direct … calls exist
   outside the gateway code path") is also unambiguous: its scope is the whole
   codebase, not just the FastAPI package.

   **Required fix:** The invariant test must walk at least two root directories:
   `apps/api/dataplat_api/` (excluding `llm/`) **and** `dagster/dagster_platform/`.
   A clean way is to parameterise the roots as a list:
   ```python
   SCAN_ROOTS = [
       repo_root / "apps" / "api" / "dataplat_api",
       repo_root / "dagster" / "dagster_platform",
   ]
   SKIP_DIRS = {"llm"}  # only relevant inside dataplat_api
   ```
   The test name and assertion logic stay the same; only the path list grows.
   `repo_root` can be located as `Path(__file__).parent.parent.parent.parent` from
   the tests directory.

---

F2. **V2 router-level HTTP assertion is absent from §9 test list (blocking)**

   The §6 verification matrix V2 row claims: "router maps `LLMAuthenticationError`
   → HTTP 502 with `detail`". But §9's description of T-L3 tests only the gateway
   layer: `LLMAuthenticationError` is raised from `complete()`. The matrix hedges
   with "router-level assertion inside T-L3 *or* a separate
   `test_router_invalid_key_returns_502`", but neither option appears as a named
   test in §9. An implementer who follows §9 literally will ship a gateway test
   only, leaving the HTTP 502 mapping untested.

   The F-053 spec clause ("fail with a clear error, not an unhandled exception")
   implicitly requires that the error surface correctly through the HTTP boundary.
   The router `try/except` block in §3.6 is new code and must be tested.

   **Required fix:** Add a concrete named test to §9 — either extend T-L3's §9
   description to include a `TestClient` call that asserts `HTTP 502` and checks
   `response.json()["detail"]` contains "invalid ANTHROPIC_API_KEY", **or** add
   an explicit **T-L5** `test_router_invalid_key_returns_502`. Pick one and commit
   to it; remove the "or a separate …" hedge from the V2 matrix row.

---

F3. **Mock mode will emit spurious `logger.warning` on every call (blocking)**

   §3.5 defines `_estimate_cost(model, ...)` which logs a WARNING when `model` is
   not in `_PRICE_TABLE`. In mock mode, `gateway.complete()` returns
   `model="mock"`. After this sprint, the mock code path will call
   `_estimate_cost("mock", 0, 0)`, which will emit:
   ```
   WARNING  llm.call unknown model 'mock' — estimated_cost_usd=0.0
   ```
   on every single mock LLM call. CI runs exclusively in mock mode, so the entire
   quality-tagger test suite will produce constant WARNING noise. This breaks the
   principle of zero spurious warnings, and will interfere with log-correctness
   tests added under F-094.

   **Required fix:** In the mock-mode branch of `complete()`, set
   `estimated_cost_usd = 0.0` directly without calling `_estimate_cost()`. The
   pattern already exists for the rate limiter ("bypassed entirely in mock mode").
   Applying the same bypass to cost estimation is consistent:
   ```python
   if self._mock:
       input_tokens = output_tokens = 0
       estimated_cost_usd = 0.0
       # ... emit log line ...
       return LLMResponse(content="0.5", model="mock", ...)
   ```
   The WARNING is only meaningful for real unknown production models; "mock" is not
   a model the price table needs to know about.

---

## Strengths

- **Logging format is fully specified in §3.4.** Both the human-readable format
  string (`"llm.call model=%s input_tokens=%d output_tokens=%d
  estimated_cost_usd=%.6f"`) and the `extra={}` dict are pinned. An implementer
  cannot misinterpret this — Mode B review will be a direct string match.

- **Exception ordering is correct and the MRO reasoning is explained (§3.2).**
  `anthropic.AuthenticationError` (more specific, subclass of `APIStatusError`) is
  caught first; `APIStatusError` second with a `status_code == 401` guard; generic
  `APIError` last. The note explaining *why* this order is required is load-bearing
  for implementers who might otherwise reverse the two.

- **Backwards compatibility is rigorously verified (§3.1, D-E).** The review of
  `test_recipes_preview.py` at line 171 (`LLMResponse(content=content, model="mock")`)
  and the confirmation that `_make_llm_stub()` uses `MagicMock(spec=LLMGateway)`
  (not `spec=LLMResponse`) is exactly the kind of due diligence that prevents
  silent test regressions. The dataclass default-value approach is the right
  technique.

- **D-D (mock-mode log emission) rationale is solid.** Emitting the log in mock
  mode makes V1 verifiable in CI without a real Anthropic key; `model="mock"` and
  `input_tokens=0` are semantically accurate (no real tokens consumed), so the V1
  clause "approximate token count" is satisfied.

- **OpenAPI codegen reasoning is explicit and correct (§7, R4).** The router is
  `include_in_schema=False` at the router level (per F-028 D-G). The implementer
  must run `make codegen` and verify an empty diff — this is explicitly stated.

- **D-A (logs vs Postgres) is well-justified.** "OR logs" language in design doc §6
  is cited; no migration required; singleton pattern preserved. This is the right
  MVP choice.

- **OQ-3 `_rate_limit_override` parameter is clean test ergonomics.** It avoids the
  `Settings()` re-instantiation problem without any production footprint.

- **Scope discipline is clean.** No Redis, no Celery, no streaming, no per-user
  buckets, no Postgres call-log table. Every out-of-scope item cites the authority
  that defers it.

---

## Suggestions (non-blocking)

- **Add D-G: upstream Anthropic `RateLimitError` mapping.** The Anthropic SDK raises
  `anthropic.RateLimitError` (a subclass of `APIStatusError`, `status_code=429`)
  when the upstream API throttles us. With the current exception handler, this maps
  to `LLMError` → HTTP 502 (not 429). This is a deliberate and defensible design
  choice (the distinction between "our in-process limiter rejected you" and "Anthropic
  rejected us" should be visible to callers), but it is not documented as a decision.
  Mode B reviewer may flag this as a missing case. Add a one-sentence D-G:
  > "Upstream `anthropic.RateLimitError` (HTTP 429 from Anthropic) is intentionally
  > mapped to generic `LLMError` → HTTP 502 rather than `LLMRateLimitError` → 429.
  > The 429 status is reserved for the in-process rate limiter only; upstream
  > throttling is an infrastructure error."

- **OQ-1 asyncio.Lock in Python 3.12 is a non-issue.** In Python ≥ 3.10 the
  deprecated `loop=` parameter was removed; `asyncio.Lock()` construction without a
  running event loop is unconditionally safe in Python 3.12. The caution in OQ-1 is
  based on pre-3.10 behaviour. The implementer can document "tested: no warning or
  error in Python 3.12" and close the question without a lazy-init workaround.
  Keeping OQ-1 is fine; its suggested fix (lazy init) is also harmless if applied.

- **T-L4 note consistency.** The §9 "Note on T-L4" correctly resolves that T-L4 must
  use real-mode (`ANTHROPIC_API_KEY="sk-test"`) + patched SDK. The brief mention of
  `LLMGateway(_rate_limit_override=2)` appearing in the same description could
  confuse an implementer into thinking mock mode is still acceptable. Consider
  making the final T-L4 spec in §9 a standalone block without the earlier
  conditional phrasing, since the resolution is already decided.

---
# S053-F-053 Mode A Round 2 Feedback

VERDICT: APPROVED

## F1 status: ADDRESSED — `dagster/dagster_platform/` now included in SCAN_ROOTS

Revision 2 adds the second root everywhere it matters: §3.7 defines
`SCAN_ROOTS = [repo_root / "apps" / "api" / "dataplat_api", repo_root / "dagster" / "dagster_platform"]`,
§9 T-INV repeats the same list, and the §6 verification matrix V3 row cites it explicitly.

Path resolution verified: `Path(__file__).parent.parent.parent.parent` from
`apps/api/tests/test_llm_gateway_invariant.py` resolves to the repo root, and
both `apps/api/dataplat_api` and `dagster/dagster_platform` exist under that root.

Skip logic verified: `"llm" in path.parts` correctly skips `gateway.py` (whose path
contains the `llm/` directory component) and is harmless in `dagster_platform/` which
has no `llm/` subdirectory.

Existing files in `dagster/dagster_platform/` verified clean: `quality_tagger.py` and
`hf_dataset_io_manager.py` contain only comment lines mentioning the invariant — no
actual `import anthropic` / `from anthropic` statements. T-INV would pass today and
catch any future violation.

## F2 status: ADDRESSED — Two named router-level tests added (T-L5 and T-L6)

T-L5 `test_router_invalid_key_returns_502` and T-L6 `test_router_rate_limit_returns_429`
are fully specified in §9 with concrete `TestClient` usage, dependency overrides, and
assertion strings. The "or a separate …" hedge in the V2 matrix row is gone; V2 now
cites both T-L3 (gateway layer) and T-L5 (HTTP boundary). Test names do not collide
with any of the five pre-existing tests in `test_llm_gateway.py`. Test count is
correctly updated to six new tests in §4.

## F3 status: ADDRESSED — Mock mode bypasses `_estimate_cost()` entirely

§3.4 explicitly states that `estimated_cost_usd = 0.0` is set directly in the mock
branch without calling `_estimate_cost()`, preventing the spurious
`WARNING … unknown model 'mock'` on every CI run. The rationale in D-D mirrors the
rate-limiter bypass pattern. T-L2 adds a sub-assertion that `logger.warning` is NOT
called in mock mode, making this a CI-enforced guarantee rather than a documentation
note.

## New findings (if any)

None blocking. A few observations for the implementer's awareness only:

- **T-INV on a missing scan root**: `Path.rglob("*.py")` raises `FileNotFoundError`
  if the root directory does not exist (rather than silently returning empty). This is
  actually desirable — it would catch a misconfigured repo layout immediately rather
  than passing silently. No change needed; just be aware that moving `dagster/` would
  break the test loudly, which is the correct behaviour.

- **`_rate_limit_override` parameter adds to `__init__` signature**: The existing
  `get_llm_gateway()` singleton factory calls `LLMGateway()` with no arguments;
  adding `_rate_limit_override: int | None = None` with a default value is fully
  backward-compatible. The singleton factory does not need to change. Verified correct.

- **Non-blocking suggestions from Round 1 are incorporated**: D-G (upstream
  `RateLimitError` → 502 rationale) added in §5; OQ-1 (`asyncio.Lock` in Python 3.12)
  closed with explanation; T-L4 is now a clean standalone spec block. All addressed.

This revision is ready to be promoted to `agreed.md`.
