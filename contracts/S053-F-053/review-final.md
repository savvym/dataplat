# S053-F-053 Mode B Review (Final)

VERDICT: APPROVED

---

## Verification matrix (V1, V2, V3, V-R) — file:line evidence

- **V1** (log emitted with model/token/cost): `gateway.py:182–201` (mock path) and `gateway.py:251–263` (real path) both emit `logger.info("llm.call model=%s …", extra={"model":…,"input_tokens":…,"output_tokens":…,"estimated_cost_usd":…})`. `test_llm_gateway.py:140–176` (T-L1) and `test_llm_gateway.py:184–209` (T-L2) assert `mock_logger.info.assert_called_once()` and verify every `extra` key. — **PASS**

- **V2** (invalid key → clear error, not unhandled exception): `gateway.py:213–216` wraps `anthropic.AuthenticationError` → `LLMAuthenticationError`; `gateway.py:217–222` wraps `APIStatusError(401)` → same. `router.py:73–74` maps `LLMAuthenticationError` → `HTTPException(502)`. T-L3 (`test_llm_gateway.py:217–242`) asserts `LLMAuthenticationError` with "LLM authentication failed"; T-L5 (`test_llm_gateway.py:290–312`) asserts `response.status_code == 502` and `"LLM authentication failed"` in `detail`. — **PASS**

- **V3** (no direct SDK imports outside gateway): `test_llm_gateway_invariant.py:29–32` defines `SCAN_ROOTS = [repo_root/"apps/api/dataplat_api", repo_root/"dagster/dagster_platform"]`; `test_llm_gateway_invariant.py:70–71` skips files whose `py_file.parts` intersects `{"llm"}`; `test_llm_gateway_invariant.py:82–84` asserts no line `strip()`-starts with `"import anthropic"`, `"from anthropic"`, `"import openai"`, `"from openai"`. Manual execution confirmed: SCAN_ROOTS exist, dagster files reference anthropic only in prose comments (not as bare import prefix), zero violations. — **PASS**

- **V-R** (rate-limit path surfaces at HTTP boundary): `gateway.py:266–285` (`_check_rate_limit`): acquires `asyncio.Lock`, prunes `>= 60.0s` entries, raises `LLMRateLimitError` when `len(window) >= limit`. T-L4 (`test_llm_gateway.py:250–282`) uses `LLMGateway(_rate_limit_override=2)`, confirms first two calls succeed, third raises `LLMRateLimitError` with "LLM rate limit exceeded". T-L6 (`test_llm_gateway.py:320–340`) asserts `TestClient` returns `429` with "LLM rate limit exceeded" in `detail`. — **PASS**

---

## Contract conformance per file (§4 of agreed.md)

| File | Status | Notes |
|---|---|---|
| `apps/api/dataplat_api/llm/gateway.py` | **CONFORMS** | All §4 items present: `LLMError`/`LLMAuthenticationError`/`LLMRateLimitError` at L47–56; `LLMResponse` extended at L101–105; `_PRICE_TABLE` + `_estimate_cost()` at L63–80; `self._rate_lock`/`self._rate_window`/`self._rate_limit` at L146–153; rate limiter at L266–285; exception wrapping at L213–224; logger.info in both paths (L182–201, L251–263). |
| `apps/api/dataplat_api/llm/__init__.py` | **CONFORMS** | Imports and `__all__` include `LLMGateway`, `LLMResponse`, `LLMError`, `LLMAuthenticationError`, `LLMRateLimitError` (L7–21). |
| `apps/api/dataplat_api/llm/router.py` | **CONFORMS** | `LLMAuthenticationError`, `LLMError`, `LLMRateLimitError` imported at L25–31; `LLMCompletionResponse` gains `input_tokens`, `output_tokens`, `estimated_cost_usd` at L48–50; try/except block at L71–78 maps all three error classes to correct HTTP codes; `return` at L79–85 populates all fields. |
| `apps/api/dataplat_api/config.py` | **CONFORMS** | `LLM_RATE_LIMIT_PER_MINUTE: int = 60` at L71 with appropriate comment. pydantic-settings field, not bare `os.environ`. |
| `apps/api/tests/test_llm_gateway.py` | **CONFORMS** | 6 new tests (T-L1 through T-L6) all present at correct lines with correct names. 5 pre-existing tests updated with `mock_resp.usage.input_tokens` / `mock_resp.usage.output_tokens` to keep them passing. Total: 11 test functions. |
| `apps/api/tests/test_llm_gateway_invariant.py` | **CONFORMS** | Created; single T-INV test; `SCAN_ROOTS` includes both roots; `_SKIP_DIRS = {"llm"}`; uses `pathlib.rglob`; all four forbidden prefixes checked. |
| `apps/api/tests/test_recipes_preview.py` | **CONFORMS** | No edit required — confirmed by implementer (commit message explicit); `MagicMock(spec=LLMGateway)` only, no `spec=LLMResponse`; `LLMResponse(content=…, model="mock")` valid with new defaults. |

---

## Hard invariants

- **#4 LLM gateway sole import — PASS.** `import anthropic` appears only at `gateway.py:31`. `test_llm_gateway_invariant.py` (T-INV) enforces this as a CI check across both `apps/api/dataplat_api/` (excluding `llm/`) and `dagster/dagster_platform/`. Manual scan confirms zero violations in current codebase. `router.py` imports only from `dataplat_api.llm.gateway`, not from `anthropic` directly.

- **#6 OpenAPI codegen — PASS.** `router.py:33` sets `include_in_schema=False` at the APIRouter level (not per-route), per F-028 D-G. `LLMCompletionResponse` extension therefore does not appear in `openapi.json`. Implementer confirms `make codegen` produced empty diff; commit message states "codegen: openapi.json unchanged … confirmed via live diff; invariant #6 satisfied."

---

## Findings (numbered, blocking only)

*None.*

---

## Non-blocking observations

1. **T-INV missing-root silently skips (vs. agreed.md intent).** `test_llm_gateway_invariant.py:63–67` does `if not root.exists(): continue` rather than `FileNotFoundError`-raising. The Mode A Round 2 feedback noted that `rglob` on a missing path "raises `FileNotFoundError` … which is actually desirable." The implementation deviates slightly — it soft-skips instead. The agreed.md§3.7 spec says nothing prescriptive about the missing-root case; Mode A's comment was observational ("just be aware"). The soft-skip is conservative and harmless for CI (both roots exist in the dev container), and the `continue` approach avoids CI breakage if the dagster subtree is ever checked out separately. No change needed; flag for awareness in a future hardening sprint if stricter failure modes are desired.

2. **`_check_rate_limit` records the timestamp *before* the SDK call succeeds.** If `self._client.messages.create(...)` raises (e.g., `LLMError`), the `time.monotonic()` entry is already in the deque, consuming one slot even for failed calls. The agreed.md spec (§3.3) does not specify whether failed calls count against the window; this is therefore neither a bug nor a contract violation. The current behaviour (failed calls count) is arguably more conservative and correct from a rate-limiting standpoint. Document this in a future sprint if caller behaviour needs clarification.

3. **`test_model_from_env` uses `claude-3-opus-20240229`** — a model not in `_PRICE_TABLE`. This will cause `_estimate_cost()` to emit a `logger.warning` during that test run. The test does not assert on warning absence (unlike T-L2). This is benign (test still passes; warning is only noise in the test log) but reviewers of log-correctness tests (F-094) should be aware. Consider adding `claude-3-opus-20240229` to `_PRICE_TABLE` in a future sprint, or suppressing warnings in tests that intentionally exercise unknown-model paths.

4. **Exception wrapping does not cover `anthropic.APIConnectionError` / `anthropic.APITimeoutError` explicitly.** These are subclasses of `anthropic.APIError`, so they correctly fall through to the `except anthropic.APIError` → `LLMError` catch. Contract says "all other `anthropic.APIError` subclasses → `LLMError`" — satisfied. No change needed; this is an observation only.

---

## Summary

All six targeted verification items (V1, V2, V3, V-R, and the two sub-items for mock-mode and the rate-limiter boundary) are met exactly as specified in agreed.md Revision 2. The implementation is a line-for-line match to §3 architecture and §9 test specifications. All hard invariants pass. No blocking findings.

**This is the green light for verifier.**
