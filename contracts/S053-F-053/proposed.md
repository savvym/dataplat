# Sprint S053-F-053 — Proposed Contract
# LLM Gateway: call-metadata logging, structured error types, in-process rate limiting, and grep invariant test

**Sprint ID:** S053-F-053
**Feature:** F-053 (infra, Phase 1, P0)
**Author:** leader
**Date:** 2026-06-05
**Revision:** 2
**Depends on:** F-005 ✓ (passes: true)

---

## §1 Goal

Build out the LLM Gateway (established in F-028) to the level that satisfies the three F-053 verification clauses. Concretely: (1) every `LLMGateway.complete()` call — both real and mock — emits a structured `INFO` log line containing `model`, `input_tokens`, `output_tokens`, and `estimated_cost_usd`, and `LLMResponse` carries those fields so callers can inspect them programmatically; (2) when `ANTHROPIC_API_KEY` is set to a non-empty but invalid value, the call surfaces a typed `LLMAuthenticationError` (not a raw Anthropic exception), the router converts it to HTTP 502 with a clear `detail`, and all other Anthropic SDK exceptions surface as a typed `LLMError` (also HTTP 502); (3) a per-minute in-process rate limiter enforces `LLM_RATE_LIMIT_PER_MINUTE` (default 60), raising `LLMRateLimitError` on excess, which the router maps to HTTP 429; and (4) a static-analysis test asserts that no file in `apps/api/dataplat_api/` (excluding `llm/`) **or** `dagster/dagster_platform/` contains a direct `import anthropic` or `import openai` line, making the "no direct SDK imports" invariant a CI-runnable check rather than a documentation note.

---

## §2 Scope Boundaries

### IN SCOPE

- `LLMResponse` extended with `input_tokens: int = 0`, `output_tokens: int = 0`, `estimated_cost_usd: float = 0.0`.
- Three new exception classes: `LLMError`, `LLMAuthenticationError(LLMError)`, `LLMRateLimitError(LLMError)`.
- `_PRICE_TABLE: dict[str, tuple[float, float]]` — per-million-token prices for known models; MVP has one entry (`claude-3-haiku-20240307 → (0.25, 1.25)`). Unknown model → cost `0.0` + WARNING log.
- `logger = logging.getLogger("dataplat_api.llm")` — one `logger.info(...)` call at the end of every `LLMGateway.complete()`, real and mock, with `extra={"model": ..., "input_tokens": ..., "output_tokens": ..., "estimated_cost_usd": ...}`.
- `asyncio.Lock`-protected fixed-window rate limiter using `collections.deque[float]` of `time.monotonic()` timestamps; window = 60 seconds; capacity = `LLM_RATE_LIMIT_PER_MINUTE` (from `Settings`); bypassed entirely in mock mode.
- Anthropic exception wrapping at the `complete()` boundary: `anthropic.AuthenticationError` + `anthropic.APIStatusError(status_code=401)` → `LLMAuthenticationError`; all other `anthropic.APIError` subclasses → `LLMError`.
- Router error-to-HTTP mapping: `LLMAuthenticationError` → 502, `LLMRateLimitError` → 429, `LLMError` → 502.
- `LLM_RATE_LIMIT_PER_MINUTE: int = 60` added to `Settings` (pydantic-settings, not bare `os.environ`).
- New test file `apps/api/tests/test_llm_gateway_invariant.py` with the static grep check.
- Additions to `apps/api/tests/test_llm_gateway.py`: four new tests (log emission real mode, log emission mock mode, invalid-key path, rate-limit exceeded path).
- Compatibility update to `apps/api/tests/test_recipes_preview.py`'s `_make_llm_stub()` so `LLMResponse(content=..., model="mock")` instantiation gains the three new fields at their defaults — zero functional change, just signature alignment.

### EXPLICITLY OUT OF SCOPE

| Deferred item | Authority |
|---|---|
| Distributed / Redis-backed rate limiting | CLAUDE.md §Scope discipline — Redis deferred in MVP |
| Persistent call-log rows in Postgres | D-A decision below — logs are sufficient for MVP |
| OpenAI SDK support | No `import openai` anywhere; not an MVP requirement |
| Self-registration / OAuth / MFA | CLAUDE.md §Scope discipline |
| Streaming completions (`stream=True`) | F-028 never introduced streaming; out of F-053 scope |
| Per-caller / per-user rate-limit buckets | MVP uses a single global bucket |

---

## §3 Architecture

### 3.1 LLMResponse — Extended Dataclass

Three new fields added to the existing `@dataclass`:

```python
@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
```

**Backward compatibility:** `LLMResponse(content="0.5", model="mock")` still works — the three new fields default to `0`. Every existing call site (`preview.py`, `router.py`, tests) remains valid without modification. `test_recipes_preview.py`'s `_make_llm_stub()` constructs `LLMResponse(content=content, model="mock")` — valid with defaults. No functional change needed there; the file is listed in §4 because mypy will recheck the construction and we want the pass noted explicitly.

### 3.2 Exception Hierarchy

```python
class LLMError(Exception):
    """Base for all gateway-raised LLM errors."""

class LLMAuthenticationError(LLMError):
    """Raised when the API key is rejected by the upstream provider."""

class LLMRateLimitError(LLMError):
    """Raised when the in-process rate limiter rejects the call."""
```

All three live in `gateway.py` and are exported from `__init__.py`.

**Anthropic exception mapping** (in `complete()`, real-mode path only):

```python
try:
    response = await self._client.messages.create(...)
except anthropic.AuthenticationError as exc:
    raise LLMAuthenticationError(
        f"LLM authentication failed: invalid ANTHROPIC_API_KEY"
    ) from exc
except anthropic.APIStatusError as exc:
    if exc.status_code == 401:
        raise LLMAuthenticationError(
            f"LLM authentication failed: invalid ANTHROPIC_API_KEY"
        ) from exc
    raise LLMError(f"LLM upstream error: {exc}") from exc
except anthropic.APIError as exc:
    raise LLMError(f"LLM upstream error: {exc}") from exc
```

The guard for `anthropic.APIStatusError` with `status_code == 401` is needed because some Anthropic SDK versions raise `APIStatusError` rather than the narrower `AuthenticationError` for 401 responses. Catching `APIStatusError` before the generic `APIError` fallback requires that it appear second (Python's MRO means the more specific `AuthenticationError` catch comes first).

### 3.3 Rate Limiter

Simple fixed-window sliding approach: a `collections.deque[float]` stores `time.monotonic()` timestamps of recent calls. Before each real-mode call, the limiter:

1. Acquires `asyncio.Lock` (non-blocking from the event loop).
2. Prunes entries older than 60 seconds from the left of the deque.
3. If `len(deque) >= limit`: releases lock, raises `LLMRateLimitError("LLM rate limit exceeded: {limit} calls/minute")`.
4. Otherwise: appends `time.monotonic()`, releases lock, proceeds with the SDK call.

**Mock mode bypasses the limiter entirely** — mock calls are free, instant, and intended for CI where a rate-limit bucket would produce flaky tests.

**`asyncio.Lock`** — correct for async code; the limiter lives in `LLMGateway.__init__` as `self._rate_lock = asyncio.Lock()` and `self._rate_window: collections.deque[float] = collections.deque()`. The lock must be acquired inside the running event loop, which means `__init__` must NOT acquire it (only `complete()` does). This is safe because `LLMGateway` is always used inside an async context.

**Window size** is always 60 seconds regardless of DST because `time.monotonic()` is a monotonic wall-clock counter unaffected by DST or leap seconds.

### 3.4 Call Metadata Logging

After every successful `complete()` call (before returning), the gateway emits:

```python
logger.info(
    "llm.call model=%s input_tokens=%d output_tokens=%d estimated_cost_usd=%.6f",
    model, input_tokens, output_tokens, estimated_cost_usd,
    extra={
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
    },
)
```

Using both a human-readable format string and `extra={}` ensures the fields appear in both plain-text logs and structured JSON log sinks (e.g., loguru, structlog) without requiring callers to change.

**Mock mode values:** `model="mock"`, `input_tokens=0`, `output_tokens=0`, `estimated_cost_usd=0.0`. The log line is still emitted so V1 is satisfied by the CI code path (where `ANTHROPIC_API_KEY` is absent). **In mock mode, `estimated_cost_usd` is set to `0.0` directly — `_estimate_cost()` is NOT called.** This prevents `_estimate_cost()` from emitting a spurious `WARNING … unknown model 'mock'` on every CI call. The pattern mirrors the rate-limiter bypass: mock calls are free and instant, so cost estimation is equally meaningless. `_estimate_cost()` is only invoked on the real-API success path, after `response.usage` is extracted. The unknown-model `WARNING` is preserved exclusively for genuinely unknown real production models (e.g., a future `claude-4-opus` model not yet in `_PRICE_TABLE`).

**Real mode token extraction:** `response.usage.input_tokens` and `response.usage.output_tokens` from the Anthropic SDK response object (these fields are present on every non-streaming `Message` response since Anthropic SDK ≥ 0.20). `LLMResponse` carries them as `input_tokens` and `output_tokens`.

### 3.5 Price Table

```python
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    # model_id: (price_per_million_input_tokens_usd, price_per_million_output_tokens_usd)
    "claude-3-haiku-20240307": (0.25, 1.25),
}

def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in _PRICE_TABLE:
        logger.warning("llm.call unknown model %r — estimated_cost_usd=0.0", model)
        return 0.0
    inp_price, out_price = _PRICE_TABLE[model]
    return (input_tokens * inp_price + output_tokens * out_price) / 1_000_000
```

A module-level pure function, no state. Easy to extend by adding rows to `_PRICE_TABLE`.

### 3.6 Router Error Mapping

```python
@router.post("/completions", response_model=LLMCompletionResponse)
async def completions(...) -> LLMCompletionResponse:
    try:
        result = await gateway.complete(body.messages, body.max_tokens)
    except LLMAuthenticationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM upstream error: {exc}")
    return LLMCompletionResponse(
        content=result.content,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
    )
```

`LLMCompletionResponse` (Pydantic model in `router.py`) gains three new fields mirroring `LLMResponse`. Since the router is `include_in_schema=False`, this does NOT produce an OpenAPI diff and does not require `make codegen`. (Hard invariant #6 is NOT triggered — this endpoint is excluded from the public schema.)

### 3.7 Grep Invariant Test

`test_llm_gateway_invariant.py` walks two root directories:

```python
repo_root = Path(__file__).parent.parent.parent.parent  # repo root from tests/
SCAN_ROOTS = [
    repo_root / "apps" / "api" / "dataplat_api",
    repo_root / "dagster" / "dagster_platform",
]
SKIP_DIRS = {"llm"}  # only relevant inside dataplat_api; has no effect on dagster_platform
```

For every `.py` file under either root that is NOT inside a `llm/` directory subtree, the test asserts that no line starts with `import anthropic`, `from anthropic`, `import openai`, or `from openai`. Uses `pathlib.Path.rglob("*.py")` — no subprocess, no shell grep — so the test runs identically on all platforms and inside the container.

**Rationale for both roots:** CLAUDE.md hard invariant #4 applies to processors and adapters as well as API routes. Processors (`quality_tagger.py`, `hf_dataset_io_manager.py`) live in `dagster/dagster_platform/`. Without scanning that directory, a future sprint could inadvertently add `import anthropic` there and T-INV would not catch it. The V3 spec clause ("No direct … calls exist outside the gateway code path") is codebase-wide, not FastAPI-package-only.

---

## §4 Files Changed

| File | Action | Description |
|---|---|---|
| `apps/api/dataplat_api/llm/gateway.py` | **MODIFY** | Add `LLMError`, `LLMAuthenticationError`, `LLMRateLimitError` exception classes; extend `LLMResponse` with `input_tokens: int = 0`, `output_tokens: int = 0`, `estimated_cost_usd: float = 0.0`; add `_PRICE_TABLE` + `_estimate_cost()` helper; add `self._rate_lock` / `self._rate_window` in `__init__`; add `LLM_RATE_LIMIT_PER_MINUTE` read from `Settings`; implement rate-limiter check and Anthropic exception wrapping in `complete()`; emit `logger.info("llm.call", extra=...)` after every call (real and mock). |
| `apps/api/dataplat_api/llm/__init__.py` | **MODIFY** | Add `LLMError`, `LLMAuthenticationError`, `LLMRateLimitError` to imports and `__all__`. |
| `apps/api/dataplat_api/llm/router.py` | **MODIFY** | Import `LLMAuthenticationError`, `LLMRateLimitError`, `LLMError`; wrap `gateway.complete()` call in try/except and map to `HTTPException(502)` / `HTTPException(429)`; extend `LLMCompletionResponse` with `input_tokens`, `output_tokens`, `estimated_cost_usd` fields; populate those fields from `result` in the return statement. |
| `apps/api/dataplat_api/config.py` | **MODIFY** | Add `LLM_RATE_LIMIT_PER_MINUTE: int = 60` to `Settings`. |
| `apps/api/dataplat_api/recipes/preview.py` | **NO LOGIC CHANGE — type compatibility only** | `preview.py` never constructs `LLMResponse` directly and never reads `.input_tokens` / `.output_tokens` / `.estimated_cost_usd`. The only change is that mypy will recheck the file because `LLMGateway` is imported from the modified module. No edits required; listed here so the implementer confirms this explicitly before moving on. |
| `apps/api/tests/test_llm_gateway.py` | **MODIFY** | Add six new tests: (T-L1) `test_llm_log_emitted_real_mode`; (T-L2) `test_llm_log_emitted_mock_mode`; (T-L3) `test_invalid_api_key_raises_LLMAuthenticationError`; (T-L4) `test_rate_limit_exceeded_raises_LLMRateLimitError`; (T-L5) `test_router_invalid_key_returns_502`; (T-L6) `test_router_rate_limit_returns_429`. Full descriptions in §9. |
| `apps/api/tests/test_llm_gateway_invariant.py` | **CREATE** | Single test `test_no_direct_llm_sdk_imports_outside_gateway` — defines `SCAN_ROOTS = [repo_root / "apps/api/dataplat_api", repo_root / "dagster/dagster_platform"]`; skips files inside any `llm/` subtree; uses `pathlib.Path.rglob("*.py")` per root; asserts no line starts with `import anthropic`, `from anthropic`, `import openai`, or `from openai`. Failure message includes offending file path and line number. |
| `apps/api/tests/test_recipes_preview.py` | **MODIFY (minor)** | `_make_llm_stub()` constructs `LLMResponse(content=content, model="mock")`. With the new dataclass fields having default values, this construction is still valid and requires no change. However, confirm by reading the file: if `LLMResponse` is imported with `spec=LLMResponse` in any `MagicMock(spec=LLMResponse)` call, verify the mock still exposes the new fields correctly. No behavioral changes — update construction call if needed to remain explicit for readability, otherwise leave as-is and note "no edit required". |

---

## §5 Design Decisions

### D-A — Logs vs. Postgres

**Decision: logs.**

The design doc (§6) says "log call metadata (model, tokens, estimated cost) to Postgres OR logs". Logs are simpler, cheaper, and sufficient for MVP: (a) no new migration required, (b) no async DB session passed into the gateway (which would break the singleton pattern and force the gateway to become a per-request dependency), (c) structured logs are searchable in any log aggregator. A future sprint can add a DB-backed call log table if billing or audit requirements emerge. This choice is explicitly endorsed by the design doc's "OR logs" language.

### D-B — Rate-Limit Window Implementation

**Decision: fixed sliding-window via `collections.deque[float]` of `time.monotonic()` timestamps, with `asyncio.Lock`.**

- `time.monotonic()` — unaffected by DST, leap seconds, or NTP adjustments. Correct for measuring wall-clock intervals within a process.
- `collections.deque` — O(1) left-pop for pruning old entries; O(1) right-append for recording new calls.
- `asyncio.Lock` — correct for async coroutines sharing a single event loop. Not `threading.Lock` because `LLMGateway` is async-only.
- Window = 60 seconds (hard-coded), capacity = `settings.LLM_RATE_LIMIT_PER_MINUTE` (default 60 from env).
- No Redis, no Celery, no external state — CLAUDE.md scope discipline.

### D-C — Cost Table

**Decision: single entry for `claude-3-haiku-20240307 → (0.25, 1.25)` per million tokens.**

- Prices sourced from Anthropic pricing page (as of 2026-06): $0.25/M input, $1.25/M output.
- Unknown models log `estimated_cost_usd=0.0` with a `logger.warning(...)` rather than raising — a missing entry should not break a call that otherwise succeeded.
- The table is a module-level dict constant, easy to extend in a later sprint without touching any other logic.
- `LLM_MODEL` defaults to `claude-3-haiku-20240307` in `LLMGateway.__init__`, so MVP's normal code path always hits the known entry.

### D-D — Mock-Mode Logging

**Decision: log line is emitted in mock mode with `model="mock"`, `input_tokens=0`, `output_tokens=0`, `estimated_cost_usd=0.0`.**

This is load-bearing for V1: CI never sets `ANTHROPIC_API_KEY`, so the only code path that runs in CI is mock mode. If mock mode did not emit the log line, the V1 verification test (`test_llm_log_emitted_mock_mode`) would have to contort itself to test real mode (requiring a full mock of the Anthropic SDK response including `usage` fields). Emitting in mock mode makes V1 testable with minimal mocking in both CI and local environments. The `estimated_cost_usd=0.0` in mock mode is semantically accurate (no real API call → no real cost).

### D-E — Backwards Compatibility of LLMResponse Extension

**Decision: use Python dataclass default values for all three new fields.**

`LLMResponse(content="0.5", model="mock")` remains valid — the new fields `input_tokens=0`, `output_tokens=0`, `estimated_cost_usd=0.0` are provided as keyword defaults. All existing call sites (router, `preview.py`, every test) are unaffected without any code changes. `test_recipes_preview.py`'s `_make_llm_stub()` uses `MagicMock(spec=LLMGateway)`, not `MagicMock(spec=LLMResponse)`, so the spec mock is unaffected. The `LLMResponse` constructor call `LLMResponse(content=content, model="mock")` remains valid.

### D-F — `LLM_RATE_LIMIT_PER_MINUTE` in Settings vs. `os.environ` Direct Read

**Decision: add to `Settings` (pydantic-settings).**

All other gateway configuration (`LLM_MODEL`, `ANTHROPIC_API_KEY`) is read via `os.environ` in `LLMGateway.__init__`, but `LLM_RATE_LIMIT_PER_MINUTE` is a new field added in this sprint and should follow the project pattern of centralizing configuration in `Settings`. The gateway reads `from dataplat_api.config import settings` and accesses `settings.LLM_RATE_LIMIT_PER_MINUTE`. This also makes the field overridable in tests via `monkeypatch.setenv` (pydantic-settings re-reads env vars when `Settings()` is reconstructed, and tests that construct `LLMGateway` fresh will pick up the monkeypatched value because `LLMGateway.__init__` reads `settings.LLM_RATE_LIMIT_PER_MINUTE` at construction time). **Note:** `ANTHROPIC_API_KEY` and `LLM_MODEL` are intentionally NOT moved to `Settings` in this sprint — that is a separate refactor outside F-053 scope; touching them risks breaking the existing `monkeypatch.setenv` test pattern in `test_llm_gateway.py`.

### D-G — Upstream `anthropic.RateLimitError` Maps to HTTP 502, Not 429

**Decision: upstream Anthropic `RateLimitError` → generic `LLMError` → HTTP 502.**

The Anthropic SDK raises `anthropic.RateLimitError` (a subclass of `APIStatusError`, `status_code=429`) when the upstream API throttles us. With the exception handler in §3.2, this maps to `LLMError` → HTTP 502 rather than `LLMRateLimitError` → HTTP 429. This is deliberate: the HTTP 429 status code is reserved exclusively for the **in-process rate limiter** (`LLMRateLimitError`). Upstream throttling — Anthropic rejecting our service — is an infrastructure error, not a per-caller quota signal. Callers should not back-off or retry in the same way they would for our rate limit. Mapping both to HTTP 429 would conflate the two very different situations. If upstream throttling becomes a concern, a dedicated exception class (e.g., `LLMUpstreamRateLimitError`) can be introduced in a future sprint with its own HTTP mapping.

---

## §6 Verification Matrix

| Verification clause | Concrete check | Test name(s) |
|---|---|---|
| V1 "Triggering quality tagger generates log entries showing model name and approximate token count" | `logger.info` called with `extra` dict containing `model`, `input_tokens`, `output_tokens`, `estimated_cost_usd` in both real-mode (SDK mocked) and mock-mode (no API key) paths | `test_llm_log_emitted_real_mode` (T-L1), `test_llm_log_emitted_mock_mode` (T-L2) |
| V2 "Setting an invalid API key in env causes LLM calls to fail with a clear error (not an unhandled exception)" | `anthropic.AuthenticationError` raised by mocked SDK → `LLMAuthenticationError` raised by gateway; `TestClient` POST to `/api/internal/llm/completions` (with gateway dep mocked to raise `LLMAuthenticationError`) → HTTP 502 with `detail` containing "LLM authentication failed" | `test_invalid_api_key_raises_LLMAuthenticationError` (T-L3), `test_router_invalid_key_returns_502` (T-L5) |
| V3 "No direct openai.ChatCompletion or anthropic.Anthropic() calls exist outside the gateway code path (grep check)" | `pathlib.Path.rglob` over `SCAN_ROOTS = [apps/api/dataplat_api/, dagster/dagster_platform/]` excluding `llm/` subtrees asserts no line starts with `import anthropic`, `from anthropic`, `import openai`, `from openai` | `test_no_direct_llm_sdk_imports_outside_gateway` (T-INV) in `test_llm_gateway_invariant.py` |
| V-R "In-process rate-limit path surfaces correctly at the HTTP boundary" | Gateway raises `LLMRateLimitError` on third call (with `_rate_limit_override=2`); `TestClient` POST returns HTTP 429 with `detail` containing "LLM rate limit exceeded" | `test_rate_limit_exceeded_raises_LLMRateLimitError` (T-L4), `test_router_rate_limit_returns_429` (T-L6) |

---

## §7 Hard Invariants

| Invariant | Status | Notes |
|---|---|---|
| **#1 Lineage mandatory** | N/A | No Commit row involved. |
| **#2 Storage separation + CAS** | N/A | No blob bytes written. Log lines go to stdout/logging framework only. |
| **#3 Schema frozen post-publish** | N/A | No Silver/Gold publish. |
| **#4 LLM calls via gateway** | ✓ | This sprint strengthens invariant #4 by adding the grep test (V3). `gateway.py` remains the sole file in `apps/api/` that imports `anthropic`. |
| **#5 Async SQLAlchemy** | ✓ | No DB session usage in this sprint. Rate limiter uses `asyncio.Lock` (in-process, no sync blocking). |
| **#6 OpenAPI ↔ TS sync** | ✓ — `make codegen` NOT required | `router.py` is registered with `include_in_schema=False` at the router level (F-028 agreed.md §D-G). `LLMCompletionResponse` extension does not appear in `openapi.json`. No diff expected. Implementer MUST verify: after changes, run `make codegen` and confirm `git diff -- packages/api-types/openapi.json` is empty. |

---

## §8 Open Questions / Notes for Implementer

### OQ-1 — `asyncio.Lock` initialization timing *(CLOSED)*

In Python ≥ 3.10, the deprecated `loop=` parameter was removed from `asyncio.Lock`. In Python 3.12 (used by this project), `asyncio.Lock()` construction outside a running event loop is unconditionally safe — no `DeprecationWarning`, no `RuntimeError`. The concern in earlier OQ-1 phrasing was based on pre-3.10 behaviour. **Resolution:** `self._rate_lock = asyncio.Lock()` in `LLMGateway.__init__` is correct. The implementer should add a one-line comment: `# asyncio.Lock() construction is safe outside event loop in Python 3.12+`. No lazy-init workaround is needed.

### OQ-2 — `response.usage` availability

`anthropic.types.Message.usage` is typed as `Usage` (always present for non-streaming calls) since Anthropic SDK ≥ 0.20. The dev Docker image should have a version ≥ 0.20; the implementer should confirm with `uv pip show anthropic` inside the container and note the version in the commit message. If `usage` is absent on an older SDK version, fall back to `input_tokens=0, output_tokens=0` with a WARNING.

### OQ-3 — Rate limiter in tests with monkeypatching *(CLOSED)*

**Resolution: `_rate_limit_override: int | None = None` constructor parameter.**

`LLMGateway` reads `settings.LLM_RATE_LIMIT_PER_MINUTE` at construction time. Since `settings` is a module-level singleton, `monkeypatch.setenv` alone is insufficient (the cached `Settings()` instance is not rebuilt). The chosen approach is to add an accepted `_rate_limit_override: int | None = None` parameter to `LLMGateway.__init__`: when set, it takes precedence over `settings.LLM_RATE_LIMIT_PER_MINUTE`. This is test-only ergonomics — in all production code paths `_rate_limit_override` is never passed (always `None`). It avoids `Settings` re-instantiation complexity and imposes zero production footprint. The `monkeypatch.setenv` approach (alternative: reload `Settings()` inside the test) was rejected because it mutates global state across tests, risking ordering-dependent failures. The constructor parameter is explicit, scoped to the test's `LLMGateway` instance, and does not affect other tests running concurrently.

---

## §9 Test List

All new/modified tests are in `apps/api/tests/`.

**`test_llm_gateway.py`** (MODIFY — add T-L1 through T-L6):

- **T-L1** `test_llm_log_emitted_real_mode` — patches `anthropic.AsyncAnthropic` (SDK mocked); `mock_resp.usage.input_tokens = 10`, `mock_resp.usage.output_tokens = 5`, `mock_resp.model = "claude-3-haiku-20240307"`; patches `logging.getLogger("dataplat_api.llm").info`; calls `await gw.complete(...)`; asserts `logger.info` called once with `extra["model"] == "claude-3-haiku-20240307"`, `extra["input_tokens"] == 10`, `extra["output_tokens"] == 5`, `extra["estimated_cost_usd"] > 0.0`.
- **T-L2** `test_llm_log_emitted_mock_mode` — no `ANTHROPIC_API_KEY`; patches `logging.getLogger("dataplat_api.llm").info`; calls `await gw.complete(...)`; asserts `logger.info` called once with `extra["model"] == "mock"`, `extra["input_tokens"] == 0`, `extra["output_tokens"] == 0`, `extra["estimated_cost_usd"] == 0.0`. **Sub-assertion:** also patches `logging.getLogger("dataplat_api.llm").warning` and asserts it is **not called** — no spurious "unknown model 'mock'" WARNING is emitted in mock mode.
- **T-L3** `test_invalid_api_key_raises_LLMAuthenticationError` — patches `anthropic.AsyncAnthropic` so `messages.create` raises `anthropic.AuthenticationError`; calls `await gw.complete(...)`; asserts `LLMAuthenticationError` is raised; asserts `str(exc)` contains `"LLM authentication failed"`.
- **T-L4** `test_rate_limit_exceeded_raises_LLMRateLimitError` — uses real-mode path: `monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")`; patches `AsyncAnthropic.messages.create` to return a valid mock response; constructs `LLMGateway(_rate_limit_override=2)`; calls `await gw.complete(...)` three times; asserts the first two return `LLMResponse` successfully; asserts the third raises `LLMRateLimitError` with message containing `"LLM rate limit exceeded"`.
- **T-L5** `test_router_invalid_key_returns_502` — uses FastAPI `TestClient` against the existing app; overrides the `get_llm_gateway` dependency to return a mock gateway whose `complete()` raises `LLMAuthenticationError("LLM authentication failed: invalid ANTHROPIC_API_KEY")`; POSTs to `/api/internal/llm/completions`; asserts `response.status_code == 502`; asserts `"LLM authentication failed"` appears in `response.json()["detail"]`.
- **T-L6** `test_router_rate_limit_returns_429` — uses FastAPI `TestClient` against the existing app; overrides the `get_llm_gateway` dependency to return a mock gateway whose `complete()` raises `LLMRateLimitError("LLM rate limit exceeded: 60 calls/minute")`; POSTs to `/api/internal/llm/completions`; asserts `response.status_code == 429`; asserts `"LLM rate limit exceeded"` appears in `response.json()["detail"]`.

**`test_llm_gateway_invariant.py`** (CREATE):

- **T-INV** `test_no_direct_llm_sdk_imports_outside_gateway` — locates the repo root via `repo_root = Path(__file__).parent.parent.parent.parent`; defines:
  ```python
  SCAN_ROOTS = [
      repo_root / "apps" / "api" / "dataplat_api",
      repo_root / "dagster" / "dagster_platform",
  ]
  ```
  For each root, iterates `rglob("*.py")`; skips files whose path includes any `llm/` directory component (i.e., `"llm" in path.parts`); for each remaining file, reads lines and asserts no line stripped of leading whitespace starts with `"import anthropic"`, `"from anthropic"`, `"import openai"`, or `"from openai"`. Failure message includes the offending root, file path, line number, and offending line text.

**`test_recipes_preview.py`** (MODIFY — minimal or none):

- Confirm `_make_llm_stub()` at line 168–172 constructs `LLMResponse(content=content, model="mock")`. With default-valued new fields this remains valid. No edit needed. **If** `spec=LLMResponse` is used anywhere in this file for a MagicMock, verify the mock spec is regenerated (it will be, since Python re-evaluates spec at `MagicMock(spec=LLMResponse)` call time). No such usage was found in the file read above — only `MagicMock(spec=LLMGateway)` is used. Confirmed: **no edit required**.

---

## §10 Risk Register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | `asyncio.Lock` construction outside event loop in Python 3.12 | LOW | OQ-1 CLOSED: safe in Python 3.12+; no workaround needed. |
| R2 | `anthropic.APIStatusError` vs `anthropic.AuthenticationError` — SDK version variance in which class is raised for 401 | MEDIUM | Catch both in explicit order (AuthenticationError first, then APIStatusError with status_code check). Test both paths. |
| R3 | `response.usage` absent on older SDK version | LOW | See OQ-2; fall back to 0 with WARNING rather than crashing. |
| R4 | `make codegen` produces a non-empty diff because `LLMCompletionResponse` is unexpectedly in the OpenAPI schema | LOW | Router is `include_in_schema=False` at router level (F-028 invariant). Verify after implementation with `git diff -- packages/api-types/openapi.json`. |
| R5 | T-INV breaks if an existing file was sneaked in with a direct SDK import | LOW | That would be a real invariant violation; the test should fail and the implementer should fix the offending file. |
