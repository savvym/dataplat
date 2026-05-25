# S018-F-018 — Reviewer Final (Mode B, post-implementation)

**Reviewed:** `git diff 94ec1d0..5336567` (commit 5336567) against contracts/S018-F-018/agreed.md, CLAUDE.md invariants, verify/reviewer-calibration.md. Ground-truthed against actual files. NOTE: the Mode A Dagster-backfill-vs-RQ BLOCKER was leader-overruled with design-doc evidence (lines 65/189/214/1008) and is NOT re-raised.

## Findings

1. **LOW** [test_runs_trigger.py:152-183] `test_trigger_extract_run_row_added` is named/docstringed as verifying `Run(kind='extract', status='pending')` on `session.add`, but does not inspect `session.add.call_args` (the session mock isn't captured post-override-pop) — it effectively re-checks the response body, duplicating the happy-path test. Test-quality gap only; the handler hardcodes kind/status at runs.py:148-151 so the contract is enforced in production. Remedy (future): capture the session mock and assert `session.add.call_args[0][0].kind == "extract"`.
2. **LOW** [checks.sh:483-579] The F018 `runs)` block has no `dagster-webserver restart + wait` step; it assumes `dagster)` (which restarts + waits 60s) ran first. `all)` chains `dagster` before `runs` (confirmed line 1228), so the full chain is safe; only standalone `bash checks.sh runs` on a stale webserver is fragile. Remedy: add a prerequisite comment.
3. **NIT** [runs.py:130-136] The defensive `add_source_partition` loop swallows ALL `DagsterGatewayError` (bare `except: pass`). Duplicates are already no-ops inside the method; the only escapees are Unauthorized/PythonError, which a misconfigured Dagster would hide at trigger time. In-contract (§7 "best-effort"); noted for F-019.

No BLOCKER, HIGH, or MEDIUM findings.

## Contract-item checklist (agreed.md → met)

- §4 extract_mineru @asset stub: MET — `@asset(partitions_def=sources_partitions)` returns MaterializeResult(); imports asset/AssetExecutionContext/MaterializeResult; wired into Definitions(assets=[source_asset, extract_mineru]); materializable (not AssetSpec); honest stub.
- §5.2/5.3 RunCreate: MET — `asset: Literal["extract_mineru"]` + `source_ids: Annotated[list[int], Field(min_length=1)]` (schemas/runs.py:73-75).
- §5.4 RunCreateResponse: MET — dagster_run_id:str + run_id:int (schemas/runs.py:85-89).
- §6 run-row NOT NULL fields: MET — dagster_run_id=backfill_id, kind='extract', asset_keys=['extract_mineru'], status='pending', partition_keys, triggered_by=current_user.id (runs.py:148-162); nullable cols explicit None.
- §7 ordering: MET — source-existence check→404 (runs.py:115) → src_{id} convert → defensive add_source_partition → launch_extract_backfill→503 → add+commit+refresh→202.
- §9 gateway.launch_extract_backfill: MET — all failure modes raise DagsterGatewayError (timeout/connect/HTTPError/non-2xx/non-JSON/errors-key/wrong-typename/empty-backfillId); mutation const defined :152 used :700; variables {"backfillParams":{assetSelection:[{path:[extract_mineru]}], partitionNames, title}}; no raw httpx in runs.py.
- tests: MET (with LOW #1) — 6 cases, dependency overrides, no live deps; happy asserts 202+body+launch called with ['src_42']; 503 mocks DagsterGatewayError; 422×2; 404.
- checks.sh runs): MET — F018-V1 (202+dagster_run_id str+run_id int), V2 (`grep -q '^extract|pending$'` on psql), V3 (partitionBackfillOrError isAssetBackfill+extract_mineru); reuses $RUNS_TOKEN; appended in existing runs) case; `;;` closes after V3; all) chains runs. Non-vacuous.
- §10 invariant #5 (async): MET — await execute/commit/refresh, no session.query.
- §10 invariant #6 (OpenAPI sync): MET — openapi.json in 5336567; POST /api/runs (202) + RunCreate + RunCreateResponse present; existing not clobbered.
- Scope: MET — no migration, no Run-model change, main.py untouched, spec/feature_list.json zero-line diff, no real extraction.

## Calibration (verify/reviewer-calibration.md)

- CAL-1 (async): PASS — runs.py await execute/commit/refresh.
- CAL-2 (LLM gateway): PASS/N-A — no LLM SDK; httpx confined to gateway.py.
- CAL-3 (OpenAPI sync): PASS — openapi.json same commit.
- CAL-4 (lineage): N/A — no Commit object; Run provenance via triggered_by/asset_keys/partition_keys.
- CAL-5 (CAS): N/A — no blob.
- CAL-6 (schema freeze): N/A.
- CAL-7 (Bronze faithfulness): N/A — no adapter.
- CAL-8 (MVP scope): PASS — Dagster backfill design-sanctioned (overruled); no Celery/DinD/OAuth/ACL.
- CAL-9 (plugin isolation): N/A — definitions.py imports only dagster.
- CAL-10 (test coverage): PASS — 6 tests (202/503/422/422/404 +1).
- CAL-11 (bias check): applied — file:line cites throughout.

APPROVED
