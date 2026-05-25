# S015-F-015 — Reviewer Feedback (Mode A)

**Reviewer:** Claude (independent)
**Date:** 2026-05-25
**Proposal:** `contracts/S015-F-015/proposed.md`

---

## Calibration checks (CAL-* against the proposal)

- **CAL-1 (async session):** PASS — §3.3 uses `async with SessionLocal()`, `await session.execute(select(...))`, `result.scalars().first()`, `await session.commit()`. No `session.query()`. Confirmed against actual `cli.py` pattern.
- **CAL-2 (LLM gateway):** N/A — no LLM call anywhere in this proposal.
- **CAL-3 (OpenAPI sync):** N/A — no router or Pydantic schema touched. §6 correctly marks codegen as NOT REQUIRED. CLAUDE.md invariant #6 satisfied.
- **CAL-4 (lineage):** N/A — no `Commit` created. Correctly noted in §6.
- **CAL-5 (CAS path):** N/A — no blob written, no MinIO interaction.
- **CAL-6 (schema freeze):** N/A — no Silver/Gold commit.
- **CAL-7 (Bronze faithfulness):** N/A — no Bronze adapter.
- **CAL-8 (MVP scope):** PASS — the proposal is narrowly scoped to inserting one DB row via an idempotent CLI command. No out-of-scope items.
- **CAL-9 (plugin isolation):** N/A — not a plugin.
- **CAL-10 (test coverage):** Flagged below as LOW — see Finding 1.
- **CAL-11 (bias check):** Applied; findings below are concrete.

---

## Hard invariant compliance

| Invariant | Assessment |
|---|---|
| #1 Lineage | N/A — confirmed correct. |
| #2 Storage / CAS | N/A — confirmed correct. |
| #3 Schema frozen | N/A — no published commit. |
| #4 LLM gateway | N/A — no LLM call. |
| #5 Async SQLAlchemy | SATISFIED — pattern confirmed against actual `cli.py` at lines 38-54. |
| #6 OpenAPI sync | N/A — confirmed correct. No new route, no codegen needed. |

---

## OQ-2 Resolution (directive)

`image="dataplat/mineru:0.1.0"` is the correct approach. The column is `TEXT NOT NULL` (confirmed at `apps/api/dataplat_api/db/models.py:190`). A non-empty, conventionally-named Docker image reference satisfies the NOT NULL constraint without ambiguity. An empty string would be semantically wrong; `"PLACEHOLDER"` is misleading to tooling. The versioned name `dataplat/mineru:0.1.0` is a valid image reference that will fail to pull until F-019 builds it, which is the intended behavior. **OQ-2 is resolved: use `"dataplat/mineru:0.1.0"` as proposed.**

---

## Scrutiny findings

### Finding 1 — LOW: No unit test for skip-path; precedent is weak but defensible

The proposal declines a pytest citing the F-007 precedent. However, F-007's seed (`seed_admin`) also has no dedicated test. The integration check (operators V1/V2/V3) exercises the real Postgres constraint and JSONB storage, which is the material failure surface for this function. The skip-if-exists path (the only non-trivial branch) is exercised by V3. This is defensible.

The one uncovered scenario is `asyncpg.UndefinedTableError` (migration not run), but §9 correctly documents this as operator error with no crash-recovery obligation for a seed command.

**Decision: no test required. Rationale accepted.**

### Finding 2 — NIT: `all)` insertion position described correctly but verify the closing `;;`

The proposal shows `operators` appended before the `;;` closing `all)`. Confirmed: `verify/checks.sh:929` shows the current `all)` block ends with `runs` then `;;`. The new insertion point is correct and will not break the `case` statement. The `*)` wildcard branch at line 930 remains unaffected.

**No action needed; noting explicitly to confirm the reviewer checked this.**

### Finding 3 — NIT: V1a grep pattern uses unescaped pipe

In §7.1, the criterion 1a grep:

```bash
grep -q '^extractor|source|document$'
```

In unquoted shell regex (grep BRE), `|` is a literal character unless using `grep -E`. The proposal uses `grep -q` without `-E`, so `|` is interpreted as a literal pipe character, NOT alternation. However, reading the query carefully: the SQL concatenates with `||` literal `'|'` as a separator character, so the output is literally `extractor|source|document`. The grep pattern `'^extractor|source|document$'` in BRE will match the string `extractor|source|document` because `|` in BRE is literal. This is actually correct, if accidental — the pipe in the grep is not alternation but is matching the literal `|` separator injected by the SQL. The check works, but the intent is easy to misread.

**Recommendation (NIT only):** The implementer should add a comment noting that `|` here is a literal separator from the SQL concatenation, not a regex alternation operator. No behavioral change required.

### Finding 4 — PASS (explicit): UNIQUE(name, version) idempotency guard is correct

The select guard keys on both `Operator.name == "mineru"` AND `Operator.version == "0.1.0"` (§3.3). This matches the actual constraint `uq_operator_name_version` defined at `models.py:161`. A guard on name alone would be subtly wrong if a future sprint seeds `mineru@0.2.0`. The proposal handles this correctly (§5 explicitly addresses the different-version case).

### Finding 5 — PASS (explicit): config_schema JSONB validity check is non-vacuous

V2 uses the Postgres JSONB `->>'type'` operator against the stored value and greps for the literal string `object`. This is non-vacuous: the `->>` operator requires Postgres to have parsed the value as JSONB at INSERT time. If the column contained a NULL or the JSONB failed to parse, the psql query would return an empty string (NULL rendered as empty in `-tA` mode), which would not match `'^object$'`. The check legitimately proves criterion 2.

### Finding 6 — PASS (explicit): image NOT NULL satisfied

`image="dataplat/mineru:0.1.0"` is a non-empty string. `models.py:190` confirms `image: Mapped[str] = mapped_column(sa.Text, nullable=False)`. No constraint failure possible. OQ-2 resolved above.

### Finding 7 — PASS (explicit): exact required column values present

Cross-checked §4 against criterion 1:
- `name='mineru'` — present
- `category='extractor'` — present
- `input_kind='source'` — present
- `output_kind='document'` — present

All four required values match the F-015 feature_list.json verification criterion.

### Finding 8 — PASS (explicit): scope is contained

Files touched: `apps/api/dataplat_api/cli.py` (modified) + `verify/checks.sh` (modified). No router, no schema, no migration, no model change. Confirmed against the actual models.py (Operator model at lines 157-208 needs no modification). Scope is correctly bounded.

---

## Summary

One LOW finding (no unit test — accepted per precedent) and two NITs (grep comment clarity; OQ-2 resolved). All hard invariants are N/A or satisfied. The idempotency guard, image placeholder, config_schema validity check, and checks.sh wiring are all correctly designed.

---

APPROVED
