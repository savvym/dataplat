# Mode A Review — S004-F-004 DagsterGateway Abstraction + Admin Status Route

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Input:** `contracts/S004-F-004/proposed.md`, `spec/feature_list.json`, `docs/data_platform_design.md`, `docker/docker-compose.dev.yml`, `apps/api/dataplat_api/main.py`, `apps/api/dataplat_api/config.py`, `apps/api/pyproject.toml`, `apps/api/uv.lock`, `verify/checks.sh`, `verify/reviewer-calibration.md`

---

## DECISION: CHANGES_REQUESTED

Five items must be addressed before this contract can be approved. Two are blockers; three are majors that would cause verifier failures or silent bugs in production.

---

## Findings

### Finding 1 — BLOCKER: Shell injection risk in `checks.sh` `dagster)` layer via `${RESPONSE}` interpolation

**Location:** proposed.md §7, `dagster)` block, both V1 and V2 python3 inline scripts.

The contract embeds the HTTP response body directly into a Python `json.loads()` call via `'''${RESPONSE}'''`:

```bash
python3 -c "
import json, sys
body = json.loads('''${RESPONSE}''')
...
"
```

This is a **shell injection / Python syntax breakage** vector. If the Dagster response body contains any single-quote character (e.g. an error message like `{"errors": [{"message": "can't reach..."}]}`), the triple-quoted string literal will break. It would also break on backslashes, dollar signs, and any content that the shell expands inside double-quoted `"` that wraps the `-c` argument. The existing `checks.sh` `buckets)` layer passes the response body safely using `docker compose exec ... python -c` with environment-injected values. The safe pattern here is to pipe the curl output into stdin:

```bash
curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" | python3 - << 'PYEOF'
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('V1 OK:', body)
PYEOF
```

Or more simply: pipe into `python3 -c "import json,sys; ..."` reading from `sys.stdin`. The contract MUST use stdin piping, not `${RESPONSE}` interpolation, in all verification scripts.

**Fix:** Replace `json.loads('''${RESPONSE}''')` in both V1 and V2 blocks with `json.load(sys.stdin)` and pipe curl's stdout into the python3 process. The V1/V2 curl invocation already outputs to stdout — just remove the `RESPONSE=$(...)` capture and pipe directly.

---

### Finding 2 — BLOCKER: `dagster)` layer never added to `all)` block in `checks.sh`

**Location:** proposed.md §7, `dagster)` block description vs. current `verify/checks.sh` lines 171-178.

The contract states: "The `all)` block is updated to call `bash "$0" dagster` after `bash "$0" buckets`." But the current `all)` block in `checks.sh` (lines 171-178) is:

```bash
all)
    bash "$0" infra
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    bash "$0" buckets
    ;;
```

There is no `dagster` entry. The contract says the implementer must add it, but the `agreed.md` verification criteria for F-006 says "`bash verify/checks.sh smoke` exits 0" — and `smoke` does not call `dagster`. F-004's verifier will run `bash verify/checks.sh dagster` (or `bash verify/checks.sh all`). The contract must be explicit that `bash "$0" dagster` is inserted between `buckets` and the `;;` terminator. This is stated in prose but the actual `checks.sh` modification must be made part of the implementer's mandatory file list. The file list already includes it (section 11), so this is a contract completeness gap: the proposed block definition at §7 must show the full updated `all)` case with `dagster` inserted, not just describe it.

**Fix:** In the contract's §7, include the full updated `all)` block with `bash "$0" dagster` inserted. Clarify that the `dagster)` case must be followed by `bash "$0" dagster` inside `all)` in the same commit.

---

### Finding 3 — MAJOR: `DAGSTER_GRAPHQL` env var rename is a breaking change for any existing `.env.example` values; rename semantics are unclear

**Location:** proposed.md §5, §6, OQ-3.

The contract resolves OQ-3 by renaming the compose env var from `DAGSTER_GRAPHQL` to `DAGSTER_GRAPHQL_URL`. The existing `docker-compose.dev.yml` line 220 currently reads:

```yaml
DAGSTER_GRAPHQL: ${DAGSTER_GRAPHQL:-http://dagster-webserver:3000/graphql}
```

However, `docs/data_platform_design.md` line 1102 uses `DAGSTER_GRAPHQL` (without `_URL`). Renaming to `DAGSTER_GRAPHQL_URL` diverges from the design doc's sample compose snippet. This is a cosmetic inconsistency (design doc samples are illustrative, not normative), but the more important issue is: if any developer has `DAGSTER_GRAPHQL=...` set in their shell environment or `.env` file, the rename silently breaks their configuration with no error (pydantic-settings will fall back to the default `http://dagster-webserver:3000/graphql` without warning because `extra = "ignore"` is set in `config.py`). The proposed contract does not require updating `.env.example` to rename/add the `DAGSTER_GRAPHQL_URL` variable.

**Fix:** The implementer must: (a) update `.env.example` to replace `DAGSTER_GRAPHQL` with `DAGSTER_GRAPHQL_URL`, (b) add `.env.example` to the files list in §11. This is a new file that the contract §11 currently omits. Alternatively, if `.env.example` does not contain `DAGSTER_GRAPHQL` at all today, verify that explicitly and note it in §9 OQ-3. The contract must confirm one or the other rather than leaving it ambiguous.

---

### Finding 4 — MAJOR: Missing explicit prohibition on raw `httpx` calls to Dagster outside `dataplat_api/dagster/`

**Location:** proposed.md §3.2, Design decisions.

Hard Invariant #4 enforces that "LLM calls go through the gateway" and explicitly names the enforcement mechanism. The Dagster gateway is intended to parallel this pattern. However, the contract contains **no statement** forbidding future code from calling `httpx.post("http://dagster-webserver:3000/graphql", ...)` directly from a route handler or service module. The LLM gateway invariant works because CLAUDE.md says "Never call Anthropic/OpenAI/etc. SDKs directly from a processor, adapter, or random route." There is no equivalent statement for Dagster in the contract or in CLAUDE.md.

The contract should include — as a named invariant or a doc comment in `gateway.py`'s module docstring — the equivalent rule: **"All FastAPI → Dagster GraphQL calls must go through `DagsterGateway`. Direct `httpx` calls to `dagster-webserver` outside `apps/api/dataplat_api/dagster/` are forbidden."** This is particularly important because F-005, F-012, F-018 etc. will all add more Dagster methods; without explicit documentation, the next implementer may reach for `httpx` directly.

**Fix:** Add a sentence to §3.2 (or as a new §3.3 "Enforcement boundary") stating this rule. Also add a grep-based check in `checks.sh backend` or a separate CI step that verifies no `httpx` calls targeting `dagster` exist outside the gateway module. At minimum, the rule must be stated in the contract so it propagates to `agreed.md` and the `CLAUDE.md` can be updated to include it as an extension to Hard Invariant #4.

---

### Finding 5 — MAJOR: `get_dagster_version()` error handling for GraphQL-level errors is underspecified

**Location:** proposed.md §3.1, `get_dagster_version()` docstring.

The contract specifies `DagsterGatewayError` is raised when "the HTTP call fails, returns non-200, or the response shape is missing data.version." However, Dagster's GraphQL endpoint can return **HTTP 200 with a `{"errors": [...]}` body** when the query is malformed or when the server encounters an internal error. This is standard GraphQL behavior. The contract's docstring bullet "response shape is missing data.version" partially covers this case, but the implementer may write code like:

```python
data = response.json()
return data["data"]["version"]  # KeyError if errors present
```

This would raise a `KeyError`, not a `DagsterGatewayError`, causing a 500 instead of 503. The contract must specify explicitly that **both** of these cases must raise `DagsterGatewayError`:
- `"errors"` key present in the response JSON (regardless of HTTP status code)
- `"data"` key absent or `"data"["version"]` absent, None, or empty string

**Fix:** Update §3.1's `get_dagster_version()` docstring to add two explicit raise conditions: (1) response JSON contains `"errors"` key, (2) `data.version` is missing, None, or empty string. Also add a note that `KeyError`/`ValueError` from response parsing must be caught and re-raised as `DagsterGatewayError`.

---

## Calibration Table (Mode A — pre-code; checking the contract's design, not a diff)

| CAL-N | Verdict | Evidence |
|---|---|---|
| CAL-1: Async session enforcement | PASS | No DB session code in scope. Gateway uses `httpx.AsyncClient`. Contract explicitly states `async def` for all methods and prohibits sync sessions. |
| CAL-2: LLM gateway enforcement | PASS | No LLM calls in scope. Contract does not import or reference any LLM SDK. |
| CAL-3: OpenAPI sync | PASS (deferred correctly) | `packages/` directory does not exist (`ls packages/ 2>&1` returns "No such file or directory"). `verify/checks.sh` contract layer already exits 0 early when `packages/api-types/` is absent (line 82). No Makefile exists. CAL-3 deferral is correct for this sprint. NOTE: the first sprint that ships a TS consumer MUST establish `make codegen` before adding any routes. This is a follow-up obligation, not a blocker here. |
| CAL-4: Lineage completeness | N/A | No Commit objects created or modified in this sprint. |
| CAL-5: CAS path discipline | N/A | No blob storage in scope. |
| CAL-6: Schema freeze post-publish | N/A | No Silver/Gold schema changes. |
| CAL-7: Bronze faithfulness | N/A | No adapter/processor changes. |
| CAL-8: MVP scope discipline | PASS | No deferred features (Celery, DinD, auth, ACL, training frameworks) are introduced. The Dagster gateway is explicitly in-scope infrastructure. |
| CAL-9: Plugin isolation | N/A | No plugin work. |
| CAL-10: Test coverage (happy path + failure) | PASS (conditionally) | Contract §8 OQ-7 explicitly calls for pytest tests: (1) happy path — mock `get_dagster_version()` returning a string, assert 200; (2) failure path — mock raising `DagsterGatewayError`, assert 503. Dependencies (`pytest-asyncio`, `respx` or `unittest.mock`) must be added to `[dependency-groups] dev`. The contract names this requirement clearly. This is sufficient for a Mode A approval — the implementer is bound to deliver it. |
| CAL-11: Bias check | N/A (self-referential; applied throughout) | Checked each finding for concrete evidence before writing. Not using vague approval language. |

---

## Non-blocking Notes (for the implementer, not blockers)

**N-1: Lifespan pattern is correct.** The `asynccontextmanager` lifespan with `app.state.dagster_gateway` is the right pattern for FastAPI 0.115.x. The `@app.on_event("startup")` deprecated pattern is not used. Confirmed consistent with §3.2 and design doc §9.2.

**N-2: `httpx` not a transitive dep — confirmed.** `grep -c "httpx" apps/api/uv.lock` returns 0. The implementer's claim is correct. `httpx==0.28.1` must be explicitly added to `[project.dependencies]`.

**N-3: Dagster healthcheck exists — `depends_on` will not block.** `dagster-webserver` in `docker-compose.dev.yml` line 133 has a healthcheck (`python urllib.request.urlopen` against `/dagster_version`) with `start_period: 30s`. The `condition: service_healthy` dependency is safe.

**N-4: `DagsterStatusResponse` as Pydantic `BaseModel` is correct.** This feeds OpenAPI schema generation automatically. When `make codegen` is eventually wired, this model will produce the correct TS type without changes.

**N-5: `-> DagsterStatusResponse` with `JSONResponse` return on error path.** The contract's note on §4 is correct — FastAPI passes `Response` subclasses through without serialization. This is the standard pattern.

**N-6: Restart loop is bounded.** V2's `for i in $(seq 1 30); do ... sleep 1; done` with `[[ "$READY" == "1" ]] || exit 1` correctly bounds the wait to 30 seconds and fails clearly. No infinite loop risk.

**N-7: Future-sprint obligation to extend CLAUDE.md.** Once the Dagster gateway enforcement rule (Finding 4) is documented in `agreed.md`, the leader should add it to CLAUDE.md Hard Invariant #4's description or as a new invariant #7. This keeps the institutional memory current.


---

## Iteration 2

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Input:** `contracts/S004-F-004/proposed.md` (iteration 2 revision)

---

### DECISION: APPROVED

All five iter-1 findings are resolved. The dagster) verification block was walked by hand as requested; no new issues were introduced.

---

### Iter-1 finding closure

**F-1 (BLOCKER — shell injection):** RESOLVED. All five occurrences in §7 now use `json.load(sys.stdin)` via direct pipe. Confirmed at proposed.md lines 282, 309, 333, 368 (standalone V1/V2 narrative examples) and lines 330–337 / 365–372 (the `checks.sh` dagster block). The safety rationale note at line 320 is correct and informative. `json.loads('''${...}''')` is absent from the document.

**F-2 (BLOCKER — `all)` block):** RESOLVED. §7 now contains an explicit "Updated `all)` block" subsection at lines 376–390 showing the full seven-line block with `bash "$0" dagster` inserted after `bash "$0" buckets`. The implementer has a concrete, unambiguous target to match.

**F-3 (MAJOR — .env.example):** RESOLVED. `docker/.env.example` is added to the §2 files table at line 31. §6 item 3 (line 252) describes the rename plus a migration comment for developers with the old name. OQ-3 (line 426) confirms `DAGSTER_GRAPHQL=http://dagster-webserver:3000/graphql` is present on line 25 of `.env.example` — independently verified against the live file (grep confirmed). The three-location rename checklist in OQ-3 (compose, .env.example, config.py) is explicit and complete.

**F-4 (MAJOR — enforcement boundary):** RESOLVED. §3.3 (lines 146–171) states the rule clearly: "All FastAPI → Dagster GraphQL calls MUST go through `apps/api/dataplat_api/dagster/gateway.py`. No other module in `apps/api/` — and no plugin — may import `httpx` to call Dagster directly." The grep guard is wired into the `dagster)` block between V1 and V2 (lines 339–350). The guard correctly uses `|| true` so `set -euo pipefail` does not abort on an empty-match (zero-result) grep, then checks `[[ -n "$BAD_CALLS" ]]` explicitly. The pattern `httpx\.(get|post|AsyncClient)` with grep -i `dagster` covers the most common single-line bypass patterns; the contract acknowledges it is a tripwire, not a comprehensive static analyzer (§3.3 line 169). The known gap (multi-line patterns, e.g. URL on one line, `httpx.post(url)` on the next) is acceptable for MVP scope. The `|| true` and `[[ -n ... ]]` handling is correct for a `pipefail` shell.

**F-5 (MAJOR — GraphQL error handling):** RESOLVED. §3.1 `get_dagster_version()` docstring (lines 90–103) now enumerates all six failure modes: httpx network errors with `raise ... from exc` chaining, non-2xx HTTP status, non-JSON body (JSONDecodeError), `"errors"` key present with non-empty array, `"data"` key absent, and `"data"["version"]` absent/None/empty string. Line 99–100 explicitly instructs the implementer: "Any KeyError / ValueError from response parsing — catch and re-raise as DagsterGatewayError so callers always see one exception type." Line 102–103 reinforces: "The implementer MUST NOT let any of these bubble as KeyError, ValueError, or httpx exceptions."

---

### Hand-walk of the dagster) block in checks.sh

Walking the proposed block (lines 322–374) as if executing it:

1. `[[ -f "$COMPOSE" ]] || exit 0` — correct guard; skips gracefully if no compose file.
2. `FASTAPI_HOST_PORT` — defaults to 18000, matches compose port mapping.
3. **V1** (lines 330–337): `curl -fsS` pipes to `python3 -c "... json.load(sys.stdin) ..."`. The `-f` flag makes curl exit non-zero on HTTP error; `pipefail` propagates that to the `||` handler which exits 1 with a clear message. AssertionError in python3 also exits 1, triggering the same handler. The assertion checks `'dagster_version' in body` (presence) AND `len(body['dagster_version']) > 0` (non-empty). Both checks are required by the F-004 verifier spec. PASS.
4. **Boundary grep** (lines 339–350): runs after V1, before V2. Correct ordering — catches a bypass violation before we spend 30s on a restart. The grep runs from repo root against `apps/api/dataplat_api/`, which is the correct relative path. The `-v 'apps/api/dataplat_api/dagster/'` exclusion correctly removes gateway module hits from output (since grep -rn output lines include the path as supplied to grep, and the gateway file is at `apps/api/dataplat_api/dagster/gateway.py`). The `|| true` + `[[ -n ... ]]` pattern is safe under `set -e`. PASS.
5. **V2** (lines 352–372): `docker compose restart fastapi`, then a bounded 30-iteration wait loop hitting `http://localhost:8000/healthz` (container-internal port, via `exec -T`) with a 2-second timeout per attempt. `READY=0` is set before the loop; `READY=1` is set on success before `break`; the `sleep 1` is inside the loop after the exec, so it is only reached on failure (a `break` bypasses it). After the loop, `[[ "$READY" == "1" ]]` correctly fails the script with a clear message if Dagster never became healthy. The final curl mirrors V1 exactly, using stdin piping. PASS.
6. Block ends with `;;` at line 373. No fall-through to the `*)` unknown-layer case. PASS.

No issues found in the dagster) block.

---

### New-issue scan (did iter-2 introduce anything?)

- **`lifespan` shape (§3.2 lines 130–137):** Unchanged from iter-1. `asynccontextmanager`, `app.state.dagster_gateway = gateway`, `yield`, `await gateway.aclose()`. Correct.
- **`httpx` dep claim (§3.2 line 116, §9 OQ-2 lines 420–422):** Unchanged. Confirmed `grep -c "httpx" apps/api/uv.lock` returns 0. Explicit dep required. Correct.
- **`config.py` shape (§5 lines 231–234):** `DAGSTER_GRAPHQL_URL: str = "http://dagster-webserver:3000/graphql"` with `extra = "ignore"` inherited from existing `model_config`. The silent-fallback behavior for developers with old `DAGSTER_GRAPHQL` in their environment is correct and documented in OQ-3.
- **No new scope creep:** No MVP-deferred features (Celery, DinD, auth, ACLs) introduced. CAL-8 holds.
- **CAL-3 deferral:** §10 line 471 correctly notes the iter-1 confirmation. No regression.
- **Test coverage obligation (OQ-7 lines 452–458):** Unchanged and explicit. Happy path + 503 path required; implementer bound to add `pytest-asyncio` and test deps to `[dependency-groups] dev`.

---

### Calibration table (iteration 2)

| CAL-N | Verdict | Evidence |
|---|---|---|
| CAL-1: Async session | PASS | No DB session code. `httpx.AsyncClient` only. All gateway methods `async def`. |
| CAL-2: LLM gateway | PASS | No LLM SDK imports or calls anywhere in contract scope. |
| CAL-3: OpenAPI sync | PASS — deferral confirmed | `packages/` does not exist on disk. `checks.sh` contract layer exits 0 early (line 82). Deferral obligation for first TS-consumer sprint noted in §10. |
| CAL-4: Lineage | N/A | No Commit objects. |
| CAL-5: CAS paths | N/A | No blob storage. |
| CAL-6: Schema freeze | N/A | No Silver/Gold schema. |
| CAL-7: Bronze faithfulness | N/A | No adapter code. |
| CAL-8: MVP scope | PASS | No Celery, DinD, auth, ACL, training framework. |
| CAL-9: Plugin isolation | N/A | No plugin work. |
| CAL-10: Test coverage | PASS (implementer bound) | OQ-7 explicitly requires happy-path test (mock version string → 200) and failure-path test (mock `DagsterGatewayError` → 503). Test deps must be added to `pyproject.toml`. |
| CAL-11: Bias check | Applied | Each criterion above backed by specific proposed.md line numbers or live filesystem verification. No vague approval language used. |

---

### Notes for implementation

**N-1:** The boundary grep in `dagster)` runs relative to the repo root. The implementer must not add a `cd` before it. Consistent with all other layers in `checks.sh`.

**N-2:** After implementing `get_dagster_version()`, the implementer should write a brief inline comment documenting which exception types each `except` clause catches and why (mirrors the contract's docstring exactly). This makes future code review faster.

**N-3:** The `gateway.py` module docstring must include the enforcement boundary statement from §3.3 verbatim. The contract requires it (§3.3 last sentence: "The `gateway.py` module docstring MUST also state this rule so it is visible at point-of-reading"). The Mode B reviewer will check for it.

**N-4:** Leader should update `CLAUDE.md` Hard Invariant #4 (or add #7) to document the Dagster gateway enforcement rule after this sprint merges, per §3.3 line 171.

