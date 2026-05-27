# S029-F-029 Feedback (Mode A)

## Verdict: CHANGES_REQUESTED

---

## Findings

### [BLOCKER 1] V1/V2 specify wrong container — must use `fastapi`, not `dagster-webserver`

**Location:** §5 "Verification plan" — V1 (Non-null ISO 639-1 codes) and V2 (Confidence
floats in [0.0, 1.0]) both open with:

> "Inside the `dagster-webserver` container, run a Python snippet..."

**Problem:** Every Lance-reading check in `checks.sh` — without exception — runs inside
the **`fastapi`** container, not `dagster-webserver`:

```bash
# Established pattern throughout checks.sh (confirmed at lines 960–965, 1530–1537,
# 2445–2461, 2464–2484, 2486–2555, etc.):
docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${AL_SRC_ID}" \
  fastapi python -c "
import lancedb, os
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
..."
```

The `dagster-webserver` container works for **unit tests** (no MinIO connectivity needed)
and for Dagster GraphQL polling (already established in other layers), but it is NOT the
right container for post-asset Lance data assertions: the `fastapi` container has
`lancedb` installed and the MinIO service reachable on the internal `minio:9000` network.
The proposed.md also prescribes `os.environ.get('MINIO_ROOT_USER', 'lance')` inline in
the snippet instead of the established `-e S3_USER` / `-e S3_PASS` injection, which is a
second inconsistency in the same location.

**Note on unit tests:** The proposed "Run inside the `dagster-webserver` container:
`python -m pytest /app/dagster/tests/test_lang_tagger.py -q`" is correct — unit tests
do run in `dagster-webserver`. The wrong-container issue applies only to V1, V2, and V3.

**Required fix:** V1 and V2 (and V3, see BLOCKER 2) must use the `fastapi` container
with `-e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" -e SRC_ID="${AL_SRC_ID}"`
injection, and the Python snippet must read credentials from `os.environ['S3_USER']` /
`os.environ['S3_PASS']` and `int(os.environ['SRC_ID'])`.

---

### [BLOCKER 2] V3 is explicitly marked pseudocode — no runnable implementation provided

**Location:** §5 "Verification plan" — V3 (No new rows inserted):

```bash
# In checks.sh (pseudocode — actual implementation uses python -c inline):
COUNT_BEFORE=$(python3 -c "...table.count_rows(where_clause)...")
# trigger attr_lang and poll ...
COUNT_AFTER=$(python3 -c "...table.count_rows(where_clause)...")
[ "$COUNT_BEFORE" -eq "$COUNT_AFTER" ] || { echo "V3 FAIL: row count changed"; exit 1; }
```

**Problem:** The label `pseudocode` is self-disqualifying. Every other checks.sh layer
(including V1/V2 in this same contract, the `attr_quality` V4 idempotency check at
checks.sh lines 2486–2571, and every Lance check across chunks/extract/lance) provides
complete, runnable bash. The sprint workflow requires `verifier` to execute `checks.sh`
mechanically — pseudocode items cannot be verified by the verifier without becoming
manual steps, which defeats the CI gate.

V3 is listed as verification criterion #3 in `feature_list.json`: "no new rows inserted
(column-mode only)." Without a concrete implementation, F-029 cannot be declared
`passes: true` from `checks.sh` output alone.

**Required fix:** V3 must provide complete, concrete bash following the `attr_quality` V4
pattern. Concretely:

```bash
echo "--- attr_lang: V3 — no new rows: count before == count after ---"
WHERE_CLAUSE="source_id = {AL_SRC_ID} AND producer_asset = 'chunks'"

AL_RC_BEFORE=$(docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${AL_SRC_ID}" \
  fastapi python -c "
import lancedb, os
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
t = db.open_table('chunks')
src_id = int(os.environ['SRC_ID'])
print(t.count_rows(f\"source_id = {src_id} AND producer_asset = 'chunks'\"))
" | tr -d '[:space:]')

# ... re-trigger attr_lang backfill + poll to COMPLETED_SUCCESS (same pattern as V1) ...

AL_RC_AFTER=$(... same count_rows query ...)

test "$AL_RC_BEFORE" = "$AL_RC_AFTER" \
  || { echo "FAIL V3: row count changed $AL_RC_BEFORE → $AL_RC_AFTER (rows were inserted)"; exit 1; }
echo "  V3 OK: row count unchanged at $AL_RC_AFTER after second run"
```

The exact backfill re-trigger mechanism must be pinned — same approach used in the
initial V1 run (Dagster GraphQL `launchPartitionBackfill`).

---

### [HIGH 1] Version pin is ambiguous: `==1.0.6 (or latest stable at implementation time)` creates drift risk

**Location:** D14 ("Dockerfile: add `fasttext-langdetect` install + model bake"):

> "Add `fasttext-langdetect==1.0.6` (or latest stable at implementation time) to the
> `RUN pip install --no-cache-dir` block"

**Problem:** The parenthetical `(or latest stable at implementation time)` defeats the
purpose of an explicit version pin. Every other dependency in the Dagster Dockerfile
is pinned exactly (`dagster==1.11.16`, `lancedb==0.30.2`, `tiktoken==0.7.0`, etc.);
the intent is reproducible builds. If an implementer reads "or latest stable" and
installs `1.1.0`, a future `detect()` API change (e.g., return format, model path,
exception type) would produce a silent divergence. The bake-step test
(`detect('hello world')`) only checks that the code runs — it does not check the
exact behaviour of `{"lang": "__label__XX", "score": ...}` that D4 parses.

**Required fix:** Remove the parenthetical. The Dockerfile line must be exactly:
```dockerfile
fasttext-langdetect==1.0.6 \
```
If `1.0.6` is unavailable at implementation time, the implementer must update the
version number in D14 and the agreed.md before committing — never silently float to
"latest stable."

---

### [HIGH 2] V1/V2 Python snippets use bare `{SOURCE_ID}` without showing injection path

**Location:** §5 verification plan, V1 and V2 Python snippets:

```python
rows = (
    table.search()
    .where(f"source_id = {SOURCE_ID} AND producer_asset = 'chunks'")
    ...
)
```

**Problem:** `{SOURCE_ID}` appears as an unresolved Python f-string variable. The
snippet gives no indication of how this value enters the Python process from bash.
The established pattern in every existing checks.sh Lance assertion is:

```bash
docker compose -f "$COMPOSE" exec -T \
  -e SRC_ID="${AL_SRC_ID}" \                 # ← bash injects the value
  fastapi python -c "
import os
src_id = int(os.environ['SRC_ID'])           # ← Python reads it from env
rows = tbl.search().where(
    f\"source_id = {src_id} AND producer_asset = 'chunks'\").to_list()
"
```

An implementer who follows the proposed snippet literally will produce a
`NameError: name 'SOURCE_ID' is not defined` at runtime.

**Required fix:** V1 and V2 snippets must show the complete injection pattern:
`-e SRC_ID="${AL_SRC_ID}"` on the `docker compose exec` call, `os.environ['SRC_ID']`
inside the Python snippet, and the WHERE clause using `f\"source_id = {src_id}...\"`.

---

### [MEDIUM 1] R1 "verify at contract review time" is unresolvable from a review — make it an implementer gate

**Location:** §6 "Risks / open questions", R1:

> "Mitigation: verify at contract review time that a `fasttext-wheel` manylinux wheel
> exists for `python3.12` / `linux_x86_64` on PyPI."

**Problem:** Contract review is a static document analysis — it cannot run `pip install
--dry-run` to confirm wheel availability. The phrase "verify at contract review time"
implies this check will be done in the review, but it cannot be. If the wheel is
missing and no C toolchain is installed in `python:3.12-slim`, the `docker build` will
fail with a compile error that is only catchable during implementation.

**Required fix:** Replace the R1 mitigation language with a concrete implementer
gate in agreed.md:

> "Before submitting the Dockerfile change, the implementer MUST run:
> `pip download fasttext-langdetect==1.0.6 --only-binary :all: -d /tmp/ftl-check
>   --python-version 312 --platform manylinux2014_x86_64 --abi cp312`
> and verify that the download succeeds with a `.whl` file. If it fails (source-only),
> add `RUN apt-get install -y gcc g++ libstdc++-dev` before the pip install step and
> document this addition in agreed.md."

---

### [MEDIUM 2] Unit test table omits an exception-handling test for `detect()`

**Location:** §5 verification plan, "Unit tests" table (9 required test cases).

**Problem:** The test table covers happy path, label stripping, confidence clamping,
empty/whitespace sentinel, `_lang_update` column correctness, and the full path. It
does not include a test for `detect()` raising an exception. `fasttext_langdetect.detect`
can raise `ValueError` or `RuntimeError` on inputs that pass the empty-string guard
(e.g., extremely long strings, strings with null bytes, or a corrupt model file).

Without an exception-handling test, an implementer may leave `detect()` calls unguarded,
causing the entire `update_lang_in_lance()` call to abort mid-batch when a single
problematic chunk is encountered — rather than logging and continuing with a sentinel.

**Required fix:** Add one test to the unit test table:

| Test | What it verifies |
|---|---|
| `test_detect_language_detect_raises` | When `detect()` raises `ValueError`, `detect_language()` either re-raises with a clear message OR returns `("und", 0.0)` sentinel; the contract must specify which behaviour is expected |

The agreed.md must commit to one of the two behaviours (re-raise vs. sentinel) for
implementer clarity.

---

## Confirmations (no change required)

### [CONFIRMED] D4 — `fasttext-langdetect detect()` return format is correct

The proposed D4 parsing code:
```python
result = detect(text)          # {"lang": "__label__en", "score": 0.9999}
code = result["lang"].replace("__label__", "")  # "en"
conf = float(result["score"])  # 0.9999
```
is correct for `fasttext-langdetect==1.0.x`. The `detect()` function returns
`{"lang": "__label__XX", "score": <float>}` where the language code IS prefixed with
`__label__`. The `.replace("__label__", "")` strip is the correct approach, matching
the library's public API. No correction needed.

### [CONFIRMED] D13 / all) ordering — `bash "$0" attr_lang` placed after `attr_quality` is correct

The proposed `all)` addition placing `bash "$0" attr_lang` after
`bash "$0" attr_quality  # F-027` is correct. `attr_lang` depends on `chunks`
(F-025), which is already in the pipeline chain before `attr_quality`. The sequential
ordering in `all)` ensures `chunks` rows exist before `attr_lang` runs. No correction
needed.

---

## Summary

The **core architecture** (fasttext direct import, column-mode `table.update()` per-row,
zero new rows, `_build_lance_storage_options()` + `update_lang_in_lance()` following the
`quality_tagger.py` pattern exactly, D2 confirming CLAUDE.md invariant #4 does not apply
to classical ML classifiers, Dockerfile bake step, `make codegen` gate) is **approved**.
The structural identity with F-028 is appropriate and the design decisions are sound.

The two BLOCKERs are **verifiability gaps**: V1/V2 point to the wrong container
(dagster-webserver instead of fastapi), and V3 provides no runnable code. Both would
cause `bash verify/checks.sh attr_lang` to fail mechanically or produce a meaningless
result until fixed. The two HIGHs are **correctness gaps**: version pin ambiguity
opens a reproducibility hole, and the SOURCE_ID injection gap produces a runtime
`NameError` in the verification script. Fix all BLOCKERs and HIGHs before
implementation begins. MEDIUMs may be addressed inline during implementation but
must be reflected in the final `agreed.md`.
