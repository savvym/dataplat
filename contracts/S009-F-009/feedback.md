# S009-F-009 — Mode A Review (Iter 1)

**Verdict:** CHANGES_REQUESTED

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Contract version reviewed:** proposed.md (Iter 1)

---

## Summary

The proposed contract is thorough and well-structured, following the S008 style closely.
The auth gate, async SQLAlchemy, and CAL-3 obligations are all addressed. The main gap is
a concrete HIGH finding: the `IntegrityError` inspection strategy (`str(exc.orig)`) is
specified but its portability across both asyncpg and psycopg backends is not verified,
and the fallback re-raise is insufficiently specified for the case where the `orig` message
does not contain "unique". Two MEDIUM items need resolution before implementation: the V2
check in `checks.sh collections)` as written does not actually verify the correct row was
inserted (it is missing the ownership linkage), and the `all)` ordering of the new
`collections` layer is not stated explicitly in §3 (it is left ambiguous). There are also
several lower-severity clarifications. Overall the contract is very close to approvable and
should reach APPROVED on iter 2 with minor edits.

---

## Findings

### BLOCKER (must fix before APPROVED)

None.

---

### HIGH (must fix before APPROVED)

**H-1: `IntegrityError.orig` string-scan is not portable across asyncpg vs psycopg**

§4 D4, `proposed.md:121-136`.

The handler inspects `str(exc.orig).lower()` for the substring `"unique"`. The content of
`exc.orig` depends on the DBAPI driver:
- With **asyncpg** (the most likely driver given the async stack), `exc.orig` is an
  `asyncpg.exceptions.UniqueViolationError` instance. `str()` of that exception typically
  produces something like `'duplicate key value violates unique constraint
  "source_collection_name_key"'`, which does contain `"unique"`, so this would pass.
- With **psycopg3**, `exc.orig` is a `psycopg.errors.UniqueViolation` and `str()` also
  typically contains "unique".
- However, there is a third case: if a *foreign-key* violation fires (e.g., `owner_id`
  references a deleted user), `str(exc.orig)` will NOT contain "unique" and the handler
  correctly re-raises. Good.
- The risk is the opposite direction: a future UNIQUE constraint added to a different
  column on `source_collection` (e.g., a composite) would also match the `"unique"`
  scan and incorrectly return 409 with `"Collection name already exists"` instead of a
  500 or a different 409 message.

The contract must resolve at least one of:
1. **State explicitly** which DBAPI driver is in use and confirm `str(exc.orig)` contains
   `"unique"` on that driver. Show a representative string.
2. OR **tighten the match** to the specific constraint name
   `"source_collection_name_key"` (the auto-generated name from `unique=True` on a
   `mapped_column`), noting that this is asyncpg/psycopg-specific. Document the
   constraint name.
3. OR **accept the current scan as "good enough" for MVP** and document that any future
   unique constraint on `source_collection` may inadvertently produce a 409 name-conflict
   error. State this risk explicitly in §8 or §9 so the reviewer does not have to infer it.

None of these require a design change — only documentation clarity. But without it, the
implementer may ship a subtly wrong 409 surface for non-name uniqueness violations.

---

### MEDIUM (should fix; reviewer may APPROVE with explicit acknowledgement)

**M-1: `checks.sh collections)` V2 verifies row existence but not `owner_id` linkage**

§6 V2, `proposed.md:220`.

The V2 check runs:

```
SELECT id, name FROM source_collection WHERE name='test-coll-checks'
```

This confirms a row was inserted, but does NOT confirm `owner_id` is set correctly
(the D5 design decision: `owner_id = current_user.id`). A handler that accidentally
inserts `owner_id = NULL` would still pass V2. The V2 psql command should be:

```sql
SELECT id, name, owner_id FROM source_collection
  WHERE name='test-coll-checks' AND owner_id IS NOT NULL
```

Or, if the checks.sh layer has access to the seeded admin user's id (which is 1 from auth
V1), it can assert `owner_id = 1` directly. At minimum, `owner_id IS NOT NULL` must be
checked in the psql V2 assertion.

This is MEDIUM (not HIGH) because unit Test 2 does assert `owner_id == 1` on the ORM
object passed to `session.add()`, so the unit coverage is adequate. The V2 live-DB check
just needs tightening.

**M-2: `all)` chain position for new `collections` layer is not stated**

§3 file list, `proposed.md:49-54`. The `checks.sh collections)` layer is described as a
new layer, and §8 states it is added to the `all)` chain after `auth`. However, the
proposed.md does not specify the exact position in the `all)` chain (e.g., between `auth`
and `buckets`, or after `runs`). This matters because:
- `collections)` V1/V3 need a seeded admin user (auth V1) and a minted token.
- `collections)` V2 needs the test row to persist between V1 and V2 within the same case.
- If `collections)` is placed before `auth`, it will always fail standalone.

The contract must specify the exact position in the `all)` ordering. The implied intent
(after `auth`) should be made explicit in §3 or §5.

**M-3: `checks.sh collections)` V1/V3 do not specify token minting within the case**

`proposed.md:219-221`.

The V1 check (`curl POST /api/sources/collections`) hits a protected endpoint (auth gate
enforced). The proposed V1 description says "curl + psql" but does not show the curl
command with an `Authorization: Bearer` header. Every prior curl call to a protected route
in checks.sh mints a token first (see `auth)` V5 and `runs)` pattern). The `collections)`
layer needs its own token-minting block at the top, following the same `COLL_TOKEN` /
`mktemp` pattern as `RUNS_TOKEN`. The contract must show this pattern explicitly (even as
pseudocode) so the implementer knows to include it.

---

### LOW (nice-to-have)

**L-1: Unit Test 3 (409 duplicate) IntegrityError constructor shape needs a note**

`proposed.md:263-265`.

The contract specifies:
```python
IntegrityError("", {}, Exception("UNIQUE constraint failed: source_collection.name"))
```

SQLAlchemy 2.x `IntegrityError(statement, params, orig)` — `orig` here is a plain
`Exception`. The handler checks `str(exc.orig).lower()` for `"unique"`. The test's `orig`
message is `"UNIQUE constraint failed: source_collection.name"`, which does contain
`"unique"` (case-insensitive). This is fine. However, the note in §9 R2 already covers
this. No action required — just confirming the note is correct.

**L-2: `SourceCollectionOut.owner_id` is `int | None` but in practice always set**

`proposed.md:83-90`.

The contract correctly notes that `owner_id` is nullable at the DB/ORM level but always set
by this endpoint. Consider adding a note that the nullable type in `SourceCollectionOut`
is a truthful reflection of the ORM constraint, not an invitation to omit it. F-010 and
F-011 implementers will read this schema; if they see `int | None`, they may assume it is
sometimes absent. This is LOW because the explanation is already in D2 — it just could be
repeated as a comment on the schema field.

**L-3: `make codegen` / OpenAPI export procedure unresolved (R3)**

`proposed.md:337-341`.

The open question in R3 is still open. The S008 review-final (CAL-3 PASS note) confirmed
that `packages/api-types/openapi.json` was manually regenerated for S008 because no
Makefile exists. The implementer must use the same manual procedure for F-009. The
procedure should be documented inline in the contract (not just flagged as a risk) so the
implementer does not ship without regenerating. Suggested resolution: add a note to §3 or
§6 (OpenAPI sync row) stating: "Until `make codegen` is wired, regenerate via
`cd apps/api && uv run python -c 'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' > ../../packages/api-types/openapi.json`
and commit the result." (Or whatever the established manual procedure is.) This is LOW
because the S008 precedent exists and the implementer can look it up, but stating it
inline reduces friction.

---

### NIT (style only)

**N-1: §6 V1/V2/V3 rows use inconsistent column widths**

Minor formatting nit in the verification table. No action required.

**N-2: Test 10 (`test_create_collection_extra_fields_ignored`) is a useful regression test
but not listed in the V1-V3 verification table**

`proposed.md:298-301`. This is fine — not every unit test needs a corresponding checks.sh
item. Just noting for awareness.

---

## Calibration sweep (CAL-1..CAL-11)

- **CAL-1 (Async session enforcement):** PASS. D4 specifies `session.add()` (sync) +
  `await session.commit()` + `await session.refresh()`. No `session.query()` mentioned
  anywhere. Unit test mocks are correctly specified as `MagicMock()` for `.add()` and
  `AsyncMock` for `.commit()` / `.refresh()`. Compliant with CAL-1 as specified.

- **CAL-2 (LLM gateway enforcement):** N/A. No LLM SDK imports. No processor/adapter code.
  New router only touches Postgres via SQLAlchemy.

- **CAL-3 (OpenAPI ↔ TS sync):** PASS (plan level). §3 explicitly lists
  `packages/api-types/openapi.json` as MODIFIED in the same commit. §6 has an
  "OpenAPI sync" verification row. L-3 above flags that the exact regeneration command
  is undocumented, but the obligation is correctly stated. CAL-3 is satisfied at the
  contract level; the implementer must not forget to actually run the export.

- **CAL-4 (Lineage completeness):** N/A. No `Commit` objects created. This sprint creates
  `source_collection` rows, which are catalogue metadata, not tracked Commits in the
  lineage graph.

- **CAL-5 (CAS path discipline):** N/A. No blob storage. No MinIO writes.

- **CAL-6 (Schema freeze post-publish):** N/A. No Silver/Gold dataset schemas touched.
  `SourceCollectionOut` is a new API response schema (Pydantic BaseModel), not a
  Silver/Gold schema.

- **CAL-7 (Bronze faithfulness):** N/A. No adapter or Bronze processor code.

- **CAL-8 (MVP scope discipline):** PASS. §2 explicitly excludes pagination, granular ACL,
  soft-delete, and other deferred features. No Celery, no Docker-in-Docker, no MFA, no
  self-registration, no Kafka, no training framework code in scope.

- **CAL-9 (Plugin isolation):** N/A. No plugin code touched.

- **CAL-10 (Test coverage — happy path + at least one failure):** PASS. 10 unit tests
  planned: happy path (Tests 1, 9, 10), failure modes (Tests 3/IntegrityError, 4/401,
  5/422, 6/422, 7/422, 8/422). Far exceeds CAL-10 minimum. Test 3 concretely exercises
  the IntegrityError path via `AsyncMock(side_effect=...)`. CAL-10 satisfied.

- **CAL-11 (Bias check):** Checked. Each finding above references a specific section or
  line in proposed.md. No vague sign-off. One HIGH (H-1), two MEDIUM (M-2, M-3), one
  MEDIUM (M-1), three LOW, two NIT. No approval without concrete evidence.

---

## Hard invariants check

- **#1 Lineage mandatory:** N/A. No `Commit`, no Dagster materialization, no lineage graph
  entity in scope.

- **#2 Storage separation + CAS:** N/A. No blob writes. No MinIO interaction.

- **#3 Schema frozen post-publish:** N/A. No Silver/Gold dataset schema modified.
  `SourceCollectionOut` is a new API response type, not a versioned data schema.

- **#4 LLM gateway:** N/A. No LLM calls.

- **#5 Async SQLAlchemy:** PASS (plan level). D4 implementation sketch uses
  `session.add()` (correctly synchronous per SQLAlchemy 2.x AsyncSession) +
  `await session.commit()` + `await session.refresh()`. No `session.query()` anywhere in
  the contract. The unit test spec correctly distinguishes `MagicMock()` for `.add()` and
  `AsyncMock` for `.commit()` / `.refresh()`.

- **#6 OpenAPI ↔ TS sync:** PASS (plan level). `packages/api-types/openapi.json`
  regeneration is called out in §3 and §6. The `collections)` layer includes a
  "OpenAPI sync" verification row. The `[[ -f Makefile ]] || exit 0` guard in
  `contract)` means CI does not fail if Makefile is absent (same treatment as S008).
  Implementation must commit openapi.json in the same commit as the router change.

---

## Scope discipline check

- **§11.6 deferrals (auth, ACL, self-registration, etc.):** No scope creep. §2 non-goals
  explicitly excludes granular ACL, soft-delete, and registration flows. Auth gate uses
  `get_current_user` from F-008 (already implemented). No new auth mechanism introduced.

- **§1.3 deferrals (distributed processing, Kafka, training, etc.):** No scope creep.
  Only Postgres, FastAPI, and the existing ORM are touched.

- **`visibility = private|internal` MVP-only ACL:** Not touched. `owner_id` is recorded
  for the resource (correct, per design doc §4.1) but no per-resource access enforcement
  beyond `get_current_user` is implemented or implied.

- **Celery / Dagster / Docker-in-Docker / plugin sandbox:** Not touched.

---

## Decision rationale

The proposed contract is structurally sound, correctly addresses all three F-009
verification criteria (V1/V2/V3), correctly identifies the auth gate requirement, respects
hard invariant #5 (async SQLAlchemy), and has explicit CAL-3 coverage. The BLOCKER count
is zero. The two HIGH items are: H-1 (IntegrityError string scan portability — resolvable
by adding a one-paragraph clarification to D4 or §9) and H-1 is re-classified to HIGH
because an unresolved ambiguity here means the implementer will either ship an over-broad
409 surface or have to make a judgement call at implementation time. The two MEDIUM items
(M-1: V2 psql check missing `owner_id` assertion; M-2: `all)` position unstated; M-3:
`collections)` token-minting pattern not shown) are each fixable with a sentence or two
in the contract. No design rethink is required. Iter 2 should resolve H-1, M-1, M-2, M-3
and can be APPROVED immediately on iter 2 unless new issues arise.

---

## Required changes for iter 2

1. **D4 / §9 (H-1):** Clarify the `str(exc.orig)` portability. Either (a) state the DBAPI
   driver and confirm the string format, (b) tighten the match to the constraint name
   `"source_collection_name_key"`, or (c) explicitly document the risk that future unique
   constraints on `source_collection` would produce a false-positive 409. Pick one and
   write it.

2. **§6 V2 (M-1):** Change the psql V2 assertion to also check `owner_id IS NOT NULL`
   (or `owner_id = 1` if the seeded admin id is reliably 1).

3. **§3 or §5 (M-2):** State the explicit position of `collections` in the `all)` chain
   (e.g., "after `auth`, before `buckets`").

4. **§6 V1/V3 or §3 (M-3):** Add a token-minting note for the `collections)` layer.
   Show the `COLL_TOKEN` / `mktemp` pattern (or at minimum note "follows the same
   token-mint pattern as `runs)` and `dagster)`").

---

# S009-F-009 — Mode A Review (Iter 2)

**Verdict:** APPROVED

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Contract version reviewed:** proposed.md (Iter 2)

---

## Iter-1 fix verification

- **H-1:** RESOLVED. §4 D4 (proposed.md lines 131-168) chooses path (b): match is
  `"source_collection_name_key" in str(exc.orig)`. Driver confirmed as asyncpg 0.30.0
  (proposed.md line 134; cross-checked against `apps/api/pyproject.toml:16`).
  Representative `str(exc.orig)` shown at lines 135-138:
  `'duplicate key value violates unique constraint "source_collection_name_key"'`.
  Constraint name confirmed via `\d source_collection` (proposed.md lines 141-144).
  Cross-verified against `apps/api/alembic/versions/0001_baseline_schema.py:54`
  (`sa.Column("name", sa.Text, nullable=False, unique=True)` — no explicit constraint
  name, so Postgres auto-generates `source_collection_name_key` per
  `{table}_{column}_key` convention). Cross-verified against
  `apps/api/dataplat_api/db/models.py:53` (same `unique=True` on `name`).
  Fully concrete and correct.

- **M-1:** RESOLVED. §6 V2 psql assertion (proposed.md line 301) is:
  `WHERE name='test-coll-checks' AND owner_id IS NOT NULL`. Directly matches the
  required tightening.

- **M-2:** RESOLVED. §6 `all)` block (proposed.md lines 325-338) shows the full chain
  with `collections` explicitly after `auth` and before `buckets`, with an inline
  comment: "seeds admin@example.com — required by collections) token mint".

- **M-3:** RESOLVED. §6 `collections)` layer (proposed.md lines 261-316) shows the
  full token-minting preamble: `COLL_TOKEN_BODY=$(mktemp)`, curl to
  `/api/auth/token`, status check with "run `bash $0 auth` first" guard, python3
  JSON parse into `COLL_TOKEN`, `rm -f "$COLL_TOKEN_BODY"`. Pattern matches the
  existing `dagster)` (checks.sh lines 209-218) and `runs)` (checks.sh lines 282-290)
  layers exactly. Variable name `COLL_TOKEN` (not `TOKEN`) avoids namespace collision
  as noted at proposed.md line 319.

- **L-3:** RESOLVED. §3 `packages/api-types/openapi.json` row (proposed.md line 65)
  and §6 OpenAPI sync section (lines 345-353) both show the exact manual regen command
  from S008 precedent (commit `91a2651`):
  `cd apps/api && uv run python -c 'import json; from dataplat_api.main import app;
  print(json.dumps(app.openapi(), indent=2))' > ../../packages/api-types/openapi.json`.
  Path is correct: `apps/api/` is two levels below repo root, so `../../` resolves to
  repo root where `packages/api-types/openapi.json` lives.

- **L-2:** RESOLVED. `SourceCollectionOut.owner_id` field (proposed.md lines 97-101)
  carries the comment: "Nullable at the ORM/DB level (source_collection.owner_id is a
  nullable FK), but always populated by this POST handler via current_user.id.
  F-010 / F-011 implementers: a null owner_id is a data-integrity anomaly, not a
  normal case produced by this endpoint."

---

## New findings (introduced by iter 2)

None. The iter-2 additions (D4 tightening, V2 psql assertion, all) chain block,
token-mint preamble, OpenAPI regen command, owner_id comment) are all correct,
internally consistent, and do not introduce new failure modes.

Spot-checks performed:
- `str(None)` is `"None"`, which does not contain `"source_collection_name_key"`,
  so a `None` `exc.orig` falls through to `raise` correctly.
- No other column on `source_collection` uses `unique=True` (models.py lines 49-67),
  so there is no risk of a future constraint silently matching this check within the
  current schema.
- The `all)` chain position (after `auth`, before `buckets`) is consistent with
  `collections)` needing a seeded admin user and `buckets)` having no such dependency.

---

## Calibration sweep (CAL-1..CAL-11)

- **CAL-1 (Async session):** PASS. D4 code sketch (proposed.md lines 154-163) uses
  `session.add(collection)` (sync — correct for AsyncSession), `await session.commit()`,
  `await session.refresh(collection)`. No `session.query()` anywhere. Test specs
  (§7) use `MagicMock()` for `.add()` and `AsyncMock` for `.commit()`/`.refresh()`.

- **CAL-2 (LLM gateway):** N/A. No LLM SDK imports or calls. Router touches only
  Postgres via SQLAlchemy.

- **CAL-3 (OpenAPI sync):** PASS. `packages/api-types/openapi.json` listed as MODIFIED
  in §3 (proposed.md line 65); same-commit obligation stated twice (§3 and §6);
  manual regen command provided. `contract)` layer's `[[ -f Makefile ]] || exit 0`
  guard prevents CI failure while Makefile is absent.

- **CAL-4 (Lineage completeness):** N/A. No `Commit` (lineage graph entity) created.
  `source_collection` is catalogue metadata, not a tracked Commit.

- **CAL-5 (CAS path discipline):** N/A. No blob storage writes or MinIO interaction.

- **CAL-6 (Schema freeze post-publish):** N/A. No Silver/Gold dataset schema modified.
  `SourceCollectionOut` is a new Pydantic response type, not a versioned data schema.

- **CAL-7 (Bronze faithfulness):** N/A. No adapter or Bronze processor code in scope.

- **CAL-8 (MVP scope discipline):** PASS. §2 non-goals explicitly exclude pagination,
  granular ACL, soft-delete, self-registration, Celery, Docker-in-Docker, and
  experiment tracking. No deferred feature is touched.

- **CAL-9 (Plugin isolation):** N/A. No plugin code touched.

- **CAL-10 (Test coverage):** PASS. 10 unit tests (§7): success cases (Tests 1, 9, 10)
  + failure cases (Test 3/409 IntegrityError, Test 4/401 no-token, Tests 5-8/422
  validation). `checks.sh collections)` V1/V2/V3 add integration-level coverage.
  Far exceeds the CAL-10 minimum of one success + one failure test.

- **CAL-11 (Bias check):** Each item above cites specific proposed.md line numbers or
  cross-referenced source files. No vague sign-off.

---

## Hard invariants (brief)

- **#1 Lineage mandatory:** N/A. No Commit entity created.
- **#2 Storage separation + CAS:** N/A. No blob storage writes.
- **#3 Schema frozen post-publish:** N/A. No Silver/Gold schema modified.
- **#4 LLM calls through gateway:** N/A. No LLM calls.
- **#5 Async SQLAlchemy:** PASS. D4 code sketch and all test mock specs use correct
  async pattern throughout; no sync sessions or `session.query()` anywhere in the
  contract.
- **#6 OpenAPI ↔ TS sync:** PASS. `packages/api-types/openapi.json` committed in
  the same commit as router change per §3 and §6; manual regen command is specific
  and runnable.

---

## Decision rationale

Every iter-1 finding that was required for approval (H-1, M-1, M-2, M-3) is concretely
resolved with specific section text and line numbers. L-2 and L-3 are also folded in.
The constraint-name match is cross-verified against the actual ORM model
(`models.py:53`), the baseline migration (`0001_baseline_schema.py:54`), and the
asyncpg version in `pyproject.toml:16` — all consistent with the constraint name
`source_collection_name_key`. The `collections)` token-mint preamble matches house
style exactly (same pattern as `dagster)` and `runs)` layers). The `all)` chain position
is explicit and dependency-safe. No new issues were introduced by iter-2 edits. All
hard invariants are N/A or PASS. The contract is ready for implementation.
