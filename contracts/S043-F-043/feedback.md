# S043-F-043 — Reviewer Mode A Feedback

**Verdict**: CHANGES_REQUESTED  
**Reviewed**: 2026-06-04

---

## Summary

The proposal is structurally sound, faithfully follows the established helper-module + IOManager pattern, and correctly threads through hard invariants #1, #4, and #5. Two changes are required before agreed.md: (1) the val split filename must be corrected from `val-00000.parquet` to `validation-00000.parquet` to match the canonical design doc §4.3 layout that downstream features depend on; and (2) operator row insertion must be removed from this sprint's scope and explicitly deferred to F-092, which already exists in the feature list for exactly that purpose. All 8 open questions are resolved below. Once the two MEDIUM findings are folded into the proposal the contract can move to agreed.md.

---

## Findings

### Blocker (HIGH) — must fix before agreed.md

*None.*

---

### MEDIUM — should fix; iteration required before agreed.md

**M1 — Val split filename conflicts with design doc §4.3 canonical layout**

The proposal writes `data/val-00000.parquet`. Design doc §4.3 (the canonical source of truth, §0 usage note) explicitly names the file `validation-00000.parquet`:

```
s3://datasets/
  {dataset_id}_v{version}/
    data/
      train-00000.parquet
      validation-00000.parquet    ← canonical name
```

The recipe DSL §7.1 uses `output.splits.validation` (not `val`) as the key. The HuggingFace datasets convention also uses `validation`. F-043's first verification criterion ("Parquet files exist in MinIO at the **expected path**") will fail if the verifier reads the expected path from the design doc. F-047 (download endpoint) and F-069 (Datasets page) will hard-code or derive this filename from the recipe's split keys; `val` will produce a mismatch.

**Fix required**: change `val-00000.parquet` → `validation-00000.parquet` everywhere in the proposal (algorithm step 9, IOManager step 10, V1 test assertion, V2 test, and the Out of Scope sharding note). The unit test assertion must also change: `Key="7_v1/data/validation-00000.parquet"`.

---

**M2 — Operator row insertion is F-092; must not be claimed by F-043**

The proposal states: *"This sprint inserts the row via a Dagster startup script or a manual SQL seed."* F-092 already exists in `spec/feature_list.json` with `passes: false` for exactly this deliverable:

```json
{
  "id": "F-092",
  "description": "sft_synthesis_qa materializer seed: a seed script inserts the
    sft_synthesis_qa materializer operator row into the operator table …"
}
```

CLAUDE.md §Hard rules: *"Never invent a feature not in spec/feature_list.json without updating it."* Implementing F-092's deliverable inside F-043 without authorization inflates scope, would require flipping F-092's `passes` flag (not sanctioned here), and creates double-accounting.

The materializer's execution path does NOT query the `operator` table at runtime — it reads config from `recipe_snapshot["schema"]["config"]` directly — so no operator row is needed for F-043 to function.

**Fix required**: remove the operator row insertion claim from the Operator Registration section; add "Operator row seed deferred to F-092 (separate sprint)" to the Out of Scope table.

---

### LOW — nice to have

**L1 — Include `chunk_id` in Parquet output for row-level lineage**

Design doc §1.2 requirement 5 states: *"任意 chunk/数据集行可回溯到原文件的某页某段"* (any dataset **row** can be traced back to its source). Stripping `chunk_id` entirely from the Parquet output makes it impossible to trace a single `(instruction, output)` pair back to its originating Lance chunk. The proposal already carries `chunk_id` through `qa_rows` — the cost to include it in the output is zero. Resolution for question 6 below mandates including it; the Out of Scope note should be changed accordingly.

**L2 — `dataset_infos.json` deferral should be stated explicitly**

F-044's verification criterion explicitly checks `s3://datasets/{dataset_id}_v{version}/dataset_infos.json exists and is valid JSON`. The proposal's Out of Scope section does not mention `dataset_infos.json`. A reader of agreed.md for F-043 could incorrectly assume it should be written here. Add one line: *"`dataset_infos.json` — deferred to F-044 (its verification criterion owns this file)."*

**L3 — `MINIO_DATASETS_BUCKET` registration deferred; this should be documented**

The IOManager reads `os.environ.get("MINIO_DATASETS_BUCKET", "datasets")` directly. This is acceptable for the Dagster layer (Dagster does not use FastAPI `Settings`). However the open question (#4) implies the reviewer should decide whether to add it to `apps/api/dataplat_api/config.py` now. The correct answer is: defer to F-047 (download endpoint owns this setting in FastAPI). The agreed.md should contain an explicit note: *"F-047 must add `MINIO_DATASETS_BUCKET` to FastAPI `Settings` before the download path is computed."*

**L4 — Integration test should use pure-function wrapper, not `dagster.materialize()`**

The proposal's TBD note asks whether `dagster.materialize()` is acceptable. Existing tests in `dagster/tests/` (confirmed: `test_quality_tagger_llm.py`, `test_extractor.py`, etc.) all use the pure-function wrapper pattern with no Dagster runtime. `dagster.materialize()` introduces a live Dagster code location requirement and runs against the registered `defs` object, which would make tests dependent on having `HFDatasetIOManager` available as a resource at test time. The pure-function wrapper pattern (`_run_dataset_asset(partition_key)`) is the correct choice and must be the agreed-upon approach.

---

### NIT — purely cosmetic

**NIT-1** — In the algorithm sketch step 1, `recipe_id, n = parse_dataset_partition_key(...)` implicitly derives `version_tag` from `n`. Add `version_tag = f"v{n}"` as an explicit assignment on the next line for readability, since it is referenced as a standalone variable in later steps.

**NIT-2** — `DatasetOutput` field comment says `"chunk_id"` is optional, but the dataclass schema doesn't show it as `Optional`. If the resolution for Q6 mandates including `chunk_id`, remove the "optionally" hedge from the comment.

**NIT-3** — The test for `test_call_llm_gateway_parse_failure_fallback_false` should clarify whether it raises on *any* non-JSON content or only when `fallback_on_failure` is set at the call-site (i.e., is it a per-call parameter or derived from recipe config?). The algorithm sketch passes it from `template_config`, not from `call_llm_gateway()` directly; spell this out in the test description.

---

## Resolutions for the 8 open questions

**1. Is `requests.post(LLM_GATEWAY_URL/...)` an acceptable substitute for `ctx.llm.call()`?**

**Resolution: YES — accept `requests.post` to the internal gateway.**  
**Reasoning**: The internal endpoint `POST /api/internal/llm/completions` is confirmed to exist in `apps/api/dataplat_api/llm/router.py`. The `quality_tagger.py` precedent (`score_chunks_via_gateway`) uses exactly this pattern. The enforceable criterion is "no direct SDK imports" (checked by V5's AST walk) — that is fully satisfied. The phrase "ctx.llm.call()" in the design doc and feature list is a conceptual description of the gateway abstraction, not a mandatory Python call signature.

---

**2. Where does `val_ratio` come from?**

**Resolution: From `recipe_snapshot["output"]["splits"]["validation"]` with fallback to 0.1.**  
**Reasoning**: Design doc §7.1 places `splits` under the `output` stage of the recipe DSL. The fallback to 0.1 when the key is absent is appropriate for recipes that do not specify splits. Do NOT read it from the operator `config_schema` — that would conflate the materializer's internal parameter with the recipe-level output specification. The proposal's step 3 `val_ratio` derivation is correct as written.

---

**3. Must the `operator` row be inserted in this sprint, or deferred?**

**Resolution: DEFER to F-092. Do not insert the row in this sprint.**  
**Reasoning**: F-092 (`passes: false`) is already defined in `feature_list.json` for exactly this seed. The materializer's execution path reads config from `recipe_snapshot["schema"]["config"]` — it does not query the `operator` table at runtime. No functional requirement of F-043 depends on the operator row existing. CLAUDE.md §Hard rules forbids implementing untracked features; implementing F-092's deliverable here without authorization violates scope discipline.

---

**4. Should `MINIO_DATASETS_BUCKET` be added to `Settings` now or deferred?**

**Resolution: DEFER to F-047.**  
**Reasoning**: The Dagster layer reads environment variables directly (see `extractor.py`, `quality_tagger.py`); it does not use FastAPI `Settings`. The `os.environ.get("MINIO_DATASETS_BUCKET", "datasets")` pattern in the IOManager is acceptable. The FastAPI `Settings` entry belongs in F-047 (download endpoint) which is the first FastAPI route that must know the datasets bucket path. The agreed.md should note this obligation for F-047.

---

**5. Is zero-row materialization acceptable (or should it tombstone)?**

**Resolution: ACCEPTABLE. Allow it; log a warning.**  
**Reasoning**: A recipe filter that matches zero chunks is a valid user-authored edge case (e.g., overly restrictive quality threshold). Two zero-row Parquet files are structurally valid HuggingFace artifacts. The F-040 freeze guard correctly locks the recipe after status=`done`, even if the dataset has zero rows — that is the user's problem to fix by creating a new recipe version. Tombstone semantics (raising) would prevent the asset from completing successfully, leaving the dataset row at `status='pending'` indefinitely, which is worse.

---

**6. Should `chunk_id` be in the output Parquet?**

**Resolution: YES — include `chunk_id` as a third column in the output Parquet.**  
**Reasoning**: Design doc §1.2 requirement 5 mandates row-level traceability ("任意 chunk/数据集行可回溯到原文件的某页某段"). Without `chunk_id` in the output rows, it is impossible to trace a specific `(instruction, output)` pair back to its Lance chunk (and hence back to the source document page). The IOManager schema should be `pa.schema([("instruction", pa.string()), ("output", pa.string()), ("chunk_id", pa.string())])`. V2's column assertion test must add `"chunk_id"` to its expected field list. The Out of Scope note about lineage should be updated accordingly.

---

**7. `max_tokens=512` — accept? Configurable via recipe?**

**Resolution: Make it configurable via `recipe_snapshot["schema"]["config"]["max_tokens"]` with fallback to 512.**  
**Reasoning**: The recipe DSL §7.1 `schema.config` section is explicitly designed for per-template configuration. `prompt_template` and `fallback_on_failure` are already read from this path in the algorithm sketch — `max_tokens` should follow the same pattern: `template_config.get("max_tokens", 512)`. The 512 default is reasonable for a JSON-structured QA response. The operator's `config_schema` (F-092) should declare `max_tokens` as an optional integer field — that's a F-092 concern, not F-043's. F-043 just reads it.

---

**8. `@dataclass` vs `TypedDict` for `DatasetOutput`?**

**Resolution: Use `@dataclass`.**  
**Reasoning**: `DatasetOutput` is a structured object passed from the Dagster asset to the IOManager — it carries both primitive scalars and nested collections. `@dataclass` provides instantiation-time type validation, is inspectable at runtime (useful for Dagster metadata logging), supports default values naturally, and signals "this is a domain object, not a plain dict." `TypedDict` is appropriate for type-annotating dict-shaped data that must remain a plain dict (e.g., for `json.dumps`). The IOManager boundary is not a dict-pass-through; it is an explicit handoff between two system components. Keep `@dataclass`.

---

## Recommended additions to agreed.md

The following clarifications must be incorporated into `contracts/S043-F-043/agreed.md` when the implementer addresses M1 and M2 above:

- **Val file naming**: Change all references from `val-00000.parquet` to `validation-00000.parquet` throughout (algorithm sketch, IOManager pseudocode, V1/V2 test assertions, shard naming note in Out of Scope).
- **Operator row deferral**: Add to Out of Scope table: `"sft_synthesis_qa operator row — deferred to F-092 (feature_list.json, passes: false)"`. Remove the paragraph in "Operator registration" that claims to insert the row.
- **chunk_id in Parquet**: The `pa.schema` in step 9 of the IOManager must include `("chunk_id", pa.string())`. The V2 column assertion must add `"chunk_id"` to the expected column set. Remove the note "chunk_id is not written to the Parquet output (lineage info only)".
- **max_tokens configurable**: Step 3 of the algorithm sketch must add `max_tokens = template_config.get("max_tokens", 512)` and `call_llm_gateway(prompt, max_tokens=max_tokens)` in the loop.
- **dataset_infos.json deferral**: Add one line to Out of Scope: `"dataset_infos.json — deferred to F-044 (F-044 verification criterion owns this file)"`.
- **MINIO_DATASETS_BUCKET obligation**: Add one line to Out of Scope: `"MINIO_DATASETS_BUCKET FastAPI Settings entry — deferred to F-047 (download endpoint sprint)"`.
- **Integration test pattern**: Lock in the pure-function wrapper approach (`_run_dataset_asset(partition_key)`) — do not use `dagster.materialize()`.
- **F-047 bucket obligation**: Add a note in the HFDatasetIOManager section: *"F-047 must add MINIO_DATASETS_BUCKET to FastAPI Settings (apps/api/dataplat_api/config.py) before the download path is computed."*

---

## Round 2 Re-review (after revision)

**Verdict**: APPROVED

Revision 2 faithfully addresses every finding from Round 1 and accurately reflects all 8 reviewer decisions. Specific confirmations:

**M1 (val filename)** — `val-00000.parquet` is gone everywhere. The outputs layout (line 129), IOManager `put_object` calls (line 222), V1 test key assertions (lines 263–264), V2 column test (lines 272–274), and the Out of Scope sharding note (line 347) all consistently use `validation-00000.parquet`.

**M2 (operator row scope)** — The Operator Registration section (lines 65–71) now states clearly that the row is deferred to F-092 and that no operator table query is needed at runtime. The Out of Scope table (line 352) adds the explicit deferral bullet, removing any double-accounting risk.

**L1 (chunk_id in Parquet)** — `chunk_id` is threaded through the full pipeline: the `DatasetOutput` comment (lines 82–83) names all three keys without any "optionally" qualifier; algorithm step 5 (lines 183–186) appends `chunk_id` from the Lance chunk; the `pa.schema` in IOManager step 9 (lines 212–215) declares it as a third `pa.string()` column; and the V2 test (lines 272–274) asserts all three columns. The invariant #1 compliance row (lines 244–245) explicitly cites row-level lineage traceability.

**L2 (dataset_infos.json deferral)** — Line 353 (Out of Scope) adds the exact sentence requested, preventing any reader confusion with F-044's verification criterion.

**L3 (MINIO_DATASETS_BUCKET obligation documented)** — Both the inline NOTE in the IOManager pseudocode (lines 235–236) and the Out of Scope entry (line 354) state that F-047 must add `MINIO_DATASETS_BUCKET` to FastAPI `Settings` before the download path is computed.

**L4 (pure-function wrapper)** — Lines 338–339 lock in `_run_dataset_asset(partition_key)` as the integration test pattern and explicitly state "`dagster.materialize()` is NOT used," consistent with the `quality_tagger.py` precedent.

**NIT-1** — Lines 144–145 add `version_tag = f"v{n}"` as an explicit assignment immediately after `parse_dataset_partition_key`, improving readability in later steps.

**NIT-2** — The `DatasetOutput` comment (lines 82–83) now reads `# dicts with "instruction", "output", "chunk_id"` with no optional qualifier, consistent with the mandated inclusion.

**NIT-3** — The test table entry for `test_call_llm_gateway_parse_failure_fallback_false` (lines 330–331) now clarifies that `fallback_on_failure=False` is sourced from `template_config` in the recipe, not a per-call parameter.

**8 decisions** — Lines 360–375 (Decisions section) reproduce all eight resolutions accurately and verbatim, matching the Q1–Q8 resolutions in this feedback document.

The contract is ready to be promoted to `agreed.md`.
