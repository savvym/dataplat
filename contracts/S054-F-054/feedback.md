# S054-F-054 Mode A Feedback

VERDICT: CHANGES_REQUESTED

---

## BLOCKING

### b1 — §4.1 algorithm: Postgres insert is inside the `Try:` block, contradicting item 7's guarantee

**Where:** `proposed.md` §4.1, algorithm steps 5 and 7

**Issue:** The pseudo-code places step 5d (`insert_document_variant`) *inside* the `Try:` block
alongside steps 5a–5c. In Python, an `except` clause catches every exception from the
entire `try` body — not just selected steps. If the Postgres insert fails, the cleanup
handler *will* fire and delete the already-written MinIO objects, then re-raise.
Item 7 immediately below says the opposite: "Exception in step 5d — Postgres — is NOT
caught here; it propagates naturally. MinIO writes already succeeded …" The two
statements are directly contradictory, and an implementer following the pseudocode will
produce the wrong behavior (MinIO cleanup on DB failure, breaking retry semantics).

The difference in observable behavior:
- **Postgres-inside-try (as drawn):** Postgres failure → cleanup → MinIO objects deleted.
  On retry the whole pipeline reruns cleanly, but the "orphaned blob" path described in
  §3.2 never occurs.
- **Postgres-outside-try (item 7 intent):** Postgres failure → MinIO objects remain →
  retry overwrites MinIO + DB row succeeds via ON CONFLICT. This is what §3.2 describes
  as "acceptable" and the retry story depends on.

The atomic-failure guarantee is the load-bearing claim of this entire sprint. The contract
must be unambiguous.

**Fix:** Restructure the §4.1 pseudo-code so steps 5a–5c are inside the cleanup
try/except, and step 5d is *outside* it (or in a separate un-caught try that lets the
exception propagate). Example:

```
_written_keys: list[str] = []
try:
    # 5a: put doc.docling.json
    # 5b: put images (zero iterations for MVP)
    # 5c: put manifest.json  ← LAST
except Exception:
    for key in _written_keys:
        try: s3.delete_object(...)
        except Exception as del_exc: logger.warning(...)
    raise  # re-raise original; no Postgres write has happened

# 5d: Postgres — outside the MinIO cleanup block
insert_document_variant(...)
# 5e: metadata
context.add_output_metadata(...)
```

Delete item 7 (its content is now self-evident from the structure) and update the
cleanup note at the bottom of §3.2 to reference this corrected structure.

---

## MAJOR

### M1 — §5.1 skeleton imports AND locally redeclares `DOCUMENTS_BUCKET`

**Where:** `proposed.md` §5.1, lines importing from extractor and the line
`DOCUMENTS_BUCKET = "documents"   # overrides extractor's constant locally if needed`

**Issue:** The skeleton lists `DOCUMENTS_BUCKET` in the `from dagster_platform.extractor
import (…)` block at the top, then immediately redeclares it as a module-level constant.
The risk register entry R8 explicitly calls this out and says "Import DOCUMENTS_BUCKET
from extractor.py rather than redeclaring." The skeleton contradicts R8's own
recommendation: the local redeclaration shadows the import, meaning any future value
change in extractor.py would be silently ignored in the IOManager.

**Fix:** Remove the local `DOCUMENTS_BUCKET = "documents"` line from the skeleton so
it uses only the imported constant. This matches R8's stated resolution. If R8 is
retained in the risk register it should reference the fix, not just describe the problem.

---

### M2 — T8 put_object count is stated as 6 but is 4 for zero-image MVP

**Where:** `proposed.md` §6, T8 row

**Issue:** "assert `put_object` called 6 times total (2× each of doc.docling.json,
manifest.json = 4 calls; 2 calls per invocation)" — the parenthetical arithmetic gives 4
(2 files × 2 invocations), but the sentence opens with "6 times total." These cannot
both be true for zero-image MVP, where each `handle_output()` emits exactly 2
`put_object` calls (doc.docling.json + manifest.json). An implementer writing
`assert mock_s3.put_object.call_count == 6` will produce a test that fails on a correct
implementation; an implementer writing `== 4` deviates from the spec.

**Fix:** Replace "6 times total" with "4 times total" throughout T8's description, and
add a note: "If/when MVP is extended with N > 0 images, T7 will cover the image-path
count; T8 stays at 4 to anchor the zero-image contract."

---

## MINOR / NIT

### NIT-1 — T7 does not specify that `ImageBlob.data` must be non-empty

**Where:** `proposed.md` §6, T7 row

**Issue:** T7 says "Construct `DoclingDocOutput` with 2 `ImageBlob` entries; assert
`put_object` called for both image keys." If the implementer constructs
`ImageBlob(filename="0.png", data=b"")`, `put_object` is still called with
`Body=b""`, the key assertion passes, but the test never verifies that the actual bytes
are routed through. The bug class "correct key, wrong body" is not caught.

**Fix:** Add to the T7 description: "Use non-empty synthetic bytes
(e.g. `data=b"\\x89PNG\\r\\n"`) and assert that the `Body` argument of each image
`put_object` call matches `img.data`." This matches how `test_hf_dataset_io_manager.py`
uses `_get_put_object_bodies()` to cross-check body content.

---

### NIT-2 — Process crash (C4) not acknowledged in §9 risk register

**Where:** `proposed.md` §9 risk register

**Issue:** The risk register covers R1–R8 but is silent on process crash mid-write
(OOM, SIGKILL, container restart between MinIO writes). No exception fires; `_written_keys`
cleanup never runs; partial objects remain in MinIO indefinitely. The instructions for
Mode A require this to be "acknowledged" even if it's acceptable for MVP.

**Fix:** Add a row: `R9 | Process crash between MinIO writes leaves orphaned partial
objects | LOW | No cleanup possible. Acceptable for MVP — same as R1 (partial cleanup
failure). Future compaction job handles both.`

---

### NIT-3 — Concurrent-run race on the same `source_id` not spelled out

**Where:** `proposed.md` §3.2 / §9

**Issue:** If two Dagster runs for the same `source_id` start concurrently, the first
run's cleanup list (`_written_keys`) only includes keys that run-1 has PUT. If run-1
fails *before* writing `manifest.json`, its cleanup cannot touch run-2's
`manifest.json`. This means concurrent runs are safe in the "cleanup can't delete
another run's objects" sense, but the assumption relies on `manifest.json` being LAST
and cleanup only deleting keys from the current call's `_written_keys`. This should be
stated explicitly so reviewers and future maintainers don't re-derive it.

**Fix:** Add one sentence to §3.2 or §9: "Concurrent runs are safe: `_written_keys`
tracks only keys PUT in the current `handle_output()` call; cleanup cannot affect
objects written by a concurrent run. `manifest.json` written last means a concurrent
run's manifest is always unreachable by an earlier-failed run's cleanup."

---

### NIT-4 — No test verifies `doc.docling.json` Body content

**Where:** `proposed.md` §6

**Issue:** T1–T5 collectively verify (a) the keys at which objects are PUT and (b)
manifest JSON content. No test cross-checks that the Body of the `doc.docling.json`
`put_object` call equals `obj.doc_json`. A bug that swaps the doc_json and manifest
bytes (same keys, wrong bodies) would not be caught.

**Fix:** In T1 or T5, add: "Assert the `Body` argument of the `doc.docling.json`
`put_object` call equals `obj.doc_json.encode('utf-8')`." Using the same
`_get_put_object_bodies()` helper pattern from `test_hf_dataset_io_manager.py`
makes this a two-line addition.

---

## Items verified correct (positive audit trail per CAL-11)

- **CAL-1 (Async SQLAlchemy):** N/A — sprint is entirely in `dagster/`; sync psycopg2
  is correct. No `apps/api/` changes. Confirmed in §1, §4, §8.

- **CAL-2 (LLM gateway):** No LLM call anywhere in the proposed implementation.
  `docling_io_manager.py` imports only `boto3`, `dagster`, and `dagster_platform.extractor`.

- **CAL-3 (OpenAPI sync):** No `apps/api/` or `packages/api-types/` changes. `make codegen`
  not required. Confirmed in §1 and §4.

- **CAL-4 (Lineage):** manifest.json carries `source_refs[].sha256` (input CAS pointer),
  `extractor_name + extractor_version + config_hash` (processor identity), and
  `dagster_run_id`. `document_variant` row duplicates key lineage fields for query
  access. Cross-referenceable via `dagster_run_id`. Full lineage coverage per invariant #1.

- **CAL-5 (CAS path discipline):** `doc.docling.json` is addressed by `{source_id}/extract_{name}/`
  (by source identity, not content hash). This is the established F-019 pattern;
  design doc §4.3 specifies this layout explicitly. The manifest's `source_refs[].sha256`
  provides the content-hash pointer to the *input* file. Acceptable within this sprint's
  established convention.

- **CAL-8 (MVP scope):** No Celery, no Docker-in-Docker, no OAuth, no real-time streams.
  `images: []` for MVP is correctly deferred. ✓

- **E2 verification:** `Source.sha256` confirmed at `apps/api/dataplat_api/db/models.py:92`
  (`sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)`). `fetch_source_sha256`
  helper design is sound.

- **E3 / F-025 compat:** `chunks` asset reads from
  `{source_id}/extract_mineru/doc.docling.json` via `read_docling_document()` in
  `chunker.py` (direct MinIO read, no IOManager.load_input). The key layout in §5.2 is
  byte-identical to the existing path. No `ins=` wiring between `extract_mineru` and
  `chunks` in `definitions.py` means Dagster never calls `DoclingDocIOManager.load_input()`
  for downstream assets. F-025 is unaffected. ✓

- **R3 (definitions.py hotfix) verified closed:** Current `definitions.py` has a correct
  `defs = Definitions(…)` block at line 828–851 with all resources, assets, sensors, and
  jobs properly listed. The four split `@run_status_sensor` decorators are present.
  Grafting `"docling_io": DoclingDocIOManager()` into `resources={…}` is straightforward.

- **OQ-1..OQ-4:** All four closed with concrete decisions in §3.1–§3.4. No "we'll figure
  out later" punts. ✓

- **§7 verification commands:** `bash verify/checks.sh smoke` (C5 probe included),
  `bash verify/checks.sh backend`, and container-based pytest for both the new test file
  and existing `test_extractor.py` are all present. Dagster test execution path is
  explicitly specified — the gap flagged in the review instructions is addressed. ✓

---

## V-MAP (spec verification criteria → test coverage)

| Spec criterion | Test(s) | Adequacy |
|---|---|---|
| V1: After success, MinIO at `s3://documents/{source_id}/extract_mineru/` contains `doc.docling.json` AND `manifest.json` | T1 (keys asserted), T2 (key format verified) | **PASS** |
| V2: MinIO write fails mid-way → no partial `document_variant` row written to Postgres | T3 (2nd put fails → cleanup + no DB write), T4 (1st put fails → no cleanup needed, no DB write) | **PASS** — contingent on b1 being fixed so the test structure reflects the correct try/except boundary |
| V3: `manifest.json` contains `source_refs` and version info per §4.3 | T5 (full manifest schema check: all required keys, correct types, ISO-8601 `created_at`, `schema_version==1`, `images==[]`) | **PASS** |

Extra tests T6 (namespace isolation), T7 (N images path), T8 (idempotency) exceed spec
requirements and are welcome, subject to M2 (count correction) and NIT-1 (body assertion).

---

## Summary

**3 changes required before this becomes `agreed.md`:**

1. **b1 (blocking):** Restructure §4.1 pseudo-code so `insert_document_variant` is
   outside the MinIO cleanup try/except. Remove the contradictory item 7.

2. **M1 (major):** Remove the local `DOCUMENTS_BUCKET = "documents"` redeclaration from
   the §5.1 skeleton — import only.

3. **M2 (major):** Fix T8 count to "4 times total" (not 6) for zero-image MVP.

NITs 1–4 are not required for APPROVED but are recommended; the implementer may address
them inline or defer to Mode B review. The overall proposal is well-structured with solid
OQ resolutions, correct lineage coverage, correct scope discipline, and good test
breadth. These are targeted corrections to a nearly-ready contract.

---

## Round 2

VERDICT (round 2): APPROVED

---

### Finding-by-finding disposition

**b1 — RESOLVED at §4.1 lines 291–310 (try/except wraps only 5a–5c) and lines 312–317 (step 6 outside block).**
The pseudo-code is now correctly structured: the `try:` block at line 291 contains only steps 5a (`doc.docling.json` PUT), 5b (image PUTs), and 5c (`manifest.json` PUT, LAST). The `except Exception:` handler at line 306 cleans up `_written_keys` and re-raises with the comment "no Postgres write has happened". Step 6 (`insert_document_variant`) appears at line 312 as a standalone, top-level statement outside the cleanup block, annotated "OUTSIDE the MinIO cleanup block; propagates naturally." Step 7 (`context.add_output_metadata`) at line 319 is similarly outside. The old contradictory "Exception in step 5d is NOT caught here" clause from rev-1 is absent — the structure now makes it self-evident, exactly as requested. §3.2 lines 93–97 now say "see §4.1 for the exact structure" rather than re-stating the try/except layout inline — correct referential prose. The explanatory paragraph at lines 322–326 reinforces the intent unambiguously.

**M1 — RESOLVED at §5.1 lines 381–384 (import block) and §9 R8 line 621.**
The class skeleton imports `DOCUMENTS_BUCKET` from `dagster_platform.extractor` alongside the other constants. No `DOCUMENTS_BUCKET = "documents"` local redeclaration appears anywhere in the skeleton. R8's description in the risk register (line 621) now reads "Resolved in §5.1 skeleton: the local redeclaration has been removed. `DOCUMENTS_BUCKET` is imported from `extractor.py` only; no local override." — references the fix, not just the problem.

**M2 — RESOLVED at §6 T8 line 510.**
The T8 row now reads "4 times total (2 files × 2 invocations: doc.docling.json + manifest.json per call)" with the clarifying note "T7 will cover image-path count; T8 stays at 4 to anchor the zero-image contract." The previously contradictory "6 times total" wording is gone.

**NIT-1 — RESOLVED at §6 T7 line 509.**
T7 now specifies `data=b'\x89PNG\r\n'` as the synthetic image bytes and adds the assertion "assert that the `Body` argument of each image `put_object` call matches `img.data`."

**NIT-2 — RESOLVED at §9 R9 line 622.**
R9 added: process crash (OOM, SIGKILL, container restart) between MinIO writes leaves orphaned partial objects; severity LOW; mitigation "No cleanup possible. Acceptable for MVP — same as R1. Future compaction job handles both."

**NIT-3 — RESOLVED at §3.2 lines 99–101 and §9 R10 line 623.**
§3.2 now contains the explicit sentence: "`_written_keys` tracks only keys PUT in the current `handle_output()` call; cleanup cannot affect objects written by a concurrent run on the same `source_id`. `manifest.json` is written last, so a concurrent run's manifest is always unreachable by an earlier-failed run's cleanup." R10 in §9 repeats this for posterity.

**NIT-4 — RESOLVED at §6 T1 line 503.**
T1 now asserts "the `Body` argument of the `doc.docling.json` `put_object` call equals `obj.doc_json.encode('utf-8')`" with an explicit reference to the `_get_put_object_bodies()` mirror pattern from `test_hf_dataset_io_manager.py`.

---

### New round-2 findings

None blocking or major.

One pre-existing cosmetic note (not introduced by rev-2, therefore not a new finding):

> §3.2 line 87 references `§3.2.1` ("see §3.2.1 below") but no such subsection exists. This was present in rev-1 and not flagged by round-1 review; it is not a rev-2 regression. Implementer may silently drop that parenthetical during code authoring.

---

### Structural fix confirmation

The §4.1 algorithm is now exactly the three-zone structure required:

```
zone A (steps 1–4): setup
zone B (step 5, try/except): MinIO writes only — cleanup fires here
zone C (steps 6–7, no try): Postgres + metadata — cleanup cannot fire here
```

The old item 7 ("Exception in step 5d is NOT caught here") is gone; structure speaks for itself.

---

Ready to copy to agreed.md.
