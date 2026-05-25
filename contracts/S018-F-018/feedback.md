# S018-F-018 — Reviewer Feedback

**Reviewer:** Independent reviewer (Claude)
**Mode:** A (contract review, pre-implementation)
**Iteration:** 2 (convergence check — all iter-1 findings applied)

---

## DECISION: APPROVED

---

## Iter-1 → Iter-2 convergence audit

**Finding 1 (BLOCKER — Dagster-backfill-vs-RQ):** OVERRULED by leader with documented
design-doc evidence (§5.4 line 189, line 214, line 1008 all specify `launchPartitionBackfill`).
F-004/F-005/F-012 precedent confirmed. §1 now documents the rationale. Not re-raised.

**Finding 2 (HIGH — duplicate token-mint):** RESOLVED. §8 setup block opens with
`COMPOSE_F018="docker/docker-compose.dev.yml"` and the PDF-upload step — no `RUNS_TOKEN`
mint. The comment "Do NOT add a second token mint" and the explanatory header are explicit.
`$RUNS_TOKEN` and `$FASTAPI_HOST_PORT` are reused from the F-005 block above.

**Finding 3 (HIGH — dead DAGSTER_HOST_PORT):** RESOLVED. No `DAGSTER_HOST_PORT` declaration
in the F018 additions. V3 uses container-internal `localhost:3000` via `docker compose exec
dagster-webserver`. Correct.

**Finding 4 (HIGH — 404-for-body-source-ids RFC 9110 note):** RESOLVED. §5.3 now contains
an explicit paragraph acknowledging the deviation from RFC 9110 guidance and documenting it as
intentional ("missing source id is a reference to a non-existent resource"). Future reviewers
directed not to re-flag.

**Finding 5 (HIGH — CAL-10, no unit tests):** RESOLVED. `apps/api/tests/test_runs_trigger.py`
listed as NEW in §2 and §12 with five cases: 202 happy-path, DagsterGatewayError→503,
wrong-asset→422, empty-source_ids→422, missing-source-id→404. Mirrors established test
pattern (`test_dagster_notify.py` / `test_sources_upload.py`).

**Finding 6 (MEDIUM — Definitions assets list):** Was a confirmation note; no change needed.
Confirmed: mixing `AssetSpec` and `@asset` in `Definitions(assets=[...])` is valid in
Dagster 1.11.16.

**Finding 7 (MEDIUM — timeout note):** RESOLVED. §9 now contains an explicit paragraph
explaining that `launchPartitionBackfill` is a synchronous enqueue (returns `backfillId`
before per-partition execution begins), making 10s sufficient for MVP.

**Finding 8 (LOW — V2 grep tightened):** RESOLVED. V2 now uses `grep -q '^extract|pending$'`
(BRE, no `-E` flag). The literal `|` in BRE matches the psql output format
`kind || '|' || status` = `extract|pending` exactly. No 'running' branch. OQ-4 marked
DECIDED in §11.

**Finding 9 (NIT — RunCreate schema inconsistency):** RESOLVED. §5.2 schema code block
now shows `asset: Literal["extract_mineru"]` consistently with §5.3.

**Finding 10 (NIT — §8 messiness):** RESOLVED. §8 is rewritten as one unambiguous linear
sequence with a clear placement description. No self-correction text remains.

---

## Calibration checks (final pass)

- CAL-1: PASS — §10 invariant #5 and §7 steps 2/7 mandate full async pattern. No
  `session.query()` or sync `.commit()` in the plan.
- CAL-2: N/A — No LLM call in this sprint.
- CAL-3: PASS — `packages/api-types/openapi.json` listed as MODIFIED in §2 and §12,
  same-commit requirement stated in §10 invariant #6.
- CAL-4: N/A — No `Commit` object created. `Run` row provenance (triggered_by,
  asset_keys, partition_keys) is appropriate for a run record.
- CAL-5: N/A — No blob storage.
- CAL-6: N/A — No Silver/Gold schema touched.
- CAL-7: N/A — No Bronze adapter.
- CAL-8: PASS — No Celery, no DinD, no OAuth, no granular ACL. Dagster backfill
  confirmed design-doc-sanctioned (overruled Finding 1).
- CAL-9: N/A — No plugin code.
- CAL-10: PASS (iter 2) — `apps/api/tests/test_runs_trigger.py` NEW with 5 cases.
- CAL-11: Applied — each finding above cites specific section and line.

---

## OQ rulings (final)

- **OQ-1 (pre-registration):** APPROVED. Keep defensive `add_source_partition` per key
  before launch (idempotent, protects against fresh-Dagster environments).
- **OQ-2 (Literal field):** APPROVED. `Literal["extract_mineru"]` is correct for MVP;
  update to a union or str+validator when a second asset is added.
- **OQ-3 (shared sources_partitions):** APPROVED. Both assets sharing
  `DynamicPartitionsDefinition(name="sources")` is the correct and intended design.
- **OQ-4 (status strictness):** DECIDED. Assert `status='pending'` exactly via BRE
  literal-pipe grep. Confirmed in §8 and §11.

---

## Implementer watch-points during build

1. The `runs)` case `;;` in `checks.sh` must close after the V3 block — verify the
   appended code ends with `;;` and does not fall through to `*)`.
2. `session.refresh(run)` after commit is required to populate `run.id` — do not
   attempt to read `run.id` before refresh.
3. `Source.id.in_(source_ids)` uses SQLAlchemy `in_()` — confirm the import of
   `select` and `Source` model in `routers/runs.py`.
4. Dagster webserver must be restarted after `definitions.py` change before any
   `checks.sh runs` invocation.
