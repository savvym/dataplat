# S019-F-019 — Reviewer Final (Mode B, post-implementation)

**Reviewed:** `git diff a07301a..07e4d64` (commit 07e4d64) against contracts/S019-F-019/agreed.md, CLAUDE.md invariants, verify/reviewer-calibration.md. Ground-truthed against committed files.

## Findings

1. **MEDIUM (acknowledged in contract — no action for MVP)** [extractor.py:169-199] The canonical pre-count + INSERT runs in one psycopg2 transaction under READ COMMITTED (default). Two concurrent runs for the same source with DIFFERENT extractors could both see canonical-count=0 and both insert is_canonical=TRUE → the 2nd hits the `idx_doc_canonical` partial-unique violation, which is NOT covered by the `ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING` clause. NOT reachable in the single-extractor MVP (the `uq_document_variant_source_extractor_config` constraint fires first for duplicate mineru). agreed.md §6 explicitly accepts this as "race-safe enough for MVP." Must be addressed before multi-extractor production.
2. **NIT** [extractor.py:122] `pdf_bytes` param in build_docling_document is unused (page_count computed separately) — annotated `# noqa: ARG001`, intentional/documented.
3. **NIT** [checks.sh:1370,1403,1413] `EX_SRC_ID` interpolated into psql strings unquoted — safe (it's a python-printed int), pattern caution only.

No BLOCKER or HIGH findings.

## S3-key deviation ruling

agreed.md §3 intent: read `s3://sources/{id}/original.pdf`. Implementer changed the boto3 `Key` to `sources/{id}/original.pdf` after live-confirming F-011 (sources.py:286) wrote with `Bucket="sources", Key="sources/{id}/original.pdf"` (bucket name prefixed into the key). LEGITIMATE ground-truth correction — the contract intent (read the PDF F-011 stored) is fully satisfied; the `s3://` URI is logical, the boto3 Key is the literal object key. The sources/documents asymmetry (sources key has `sources/` prefix; documents key `{id}/extract_mineru/...` does not) is a pre-existing F-011 wart, not introduced by F-019; documents key matches spec `s3://documents/{id}/...` cleanly. Acceptable (NIT-level note for future contributors).

## Contract-item checklist (agreed.md → met)

- Goal #1 read PDF: MET — extractor.py:65-81 (key sources/{id}/original.pdf).
- Goal #2 minimal DoclingDocument: MET — extractor.py:120-138, DoclingDocument(name=...), model_dump_json.
- Goal #3 write to s3://documents/...: MET — extractor.py:84-93, Bucket=documents Key={id}/extract_mineru/doc.docling.json.
- Goal #4 document_variant row: MET — extractor.py:146-201, all NOT NULL cols.
- Goal #5 inject MinIO+DB creds: MET — compose webserver+daemon (NOT workers).
- Goal #6 backfill→SUCCESS: MET — checks.sh extract) polls COMPLETED_SUCCESS + per-partition SUCCESS, 120s timeout.
- §2 files (7) + test-env: MET — pytest in dagster-webserver container.
- §3 asset body order: MET — definitions.py:65-110, no swallowing except.
- §4 no binary_hash/origin: MET — no DocumentOrigin import; origin null (test asserts).
- §5 psycopg2 + conn closed in finally: MET — extractor.py:167,201.
- §5a PLATFORM_DB_URL sync postgresql:// → platform DB: MET — compose:136,171.
- §5a Dockerfile deps boto3/docling-core/pytest: MET — Dockerfile:28-34.
- §6 INSERT all cols + config_hash 44136fa3 + storage_prefix s3://documents/{id}/extract_mineru/ + ON CONFLICT DO NOTHING + is_canonical pre-count-in-txn + dagster_run_id=context.run_id: MET — extractor.py:40-42,165,180-198; definitions.py:96.
- §7 checks.sh: MET — MINIO_USER/PASS at top, unit tests, poll, V1-V4 non-vacuous, all) chains extract, terminal-failure detection.
- §9 invariants #1/#2/#5: MET — variant provenance present; doc bytes only in MinIO; sync psycopg2 outside apps/api/.

## Calibration (verify/reviewer-calibration.md)

- CAL-1 (async): PASS — no apps/api touched; psycopg2 sync is outside the invariant scope.
- CAL-2 (LLM gateway): PASS/N-A — no LLM SDK.
- CAL-3 (OpenAPI sync): N/A — no API surface.
- CAL-4 (lineage): N/A — no Commit; variant lineage fields present.
- CAL-5 (CAS path): N/A — extraction result path, PDF at F-011 path.
- CAL-6 (schema freeze): N/A.
- CAL-7 (Bronze faithfulness): N/A — not an adapter.
- CAL-8 (MVP scope): PASS — no Celery/DinD/OAuth.
- CAL-9 (plugin isolation): PASS — extractor.py imports only stdlib/boto3/psycopg2/docling-core; no apps/api or plugins reach-in.
- CAL-10 (test coverage): PASS — 9 unit tests (success + 2 failure modes) + E2E V1-V4.
- CAL-11 (bias check): applied — file:line evidence throughout.

APPROVED
