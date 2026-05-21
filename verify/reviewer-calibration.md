# Reviewer Calibration Cases

These are the specific things reviewer MUST check on every Mode B (code) review. Each one comes from design doc §1.2 / §11.7 or known LLM agent failure modes.

**Reviewer must report each CAL-N as PASS / FAIL / N/A with evidence.** Approval without working through these is invalid.

When adding a new case: include source section (if from design doc), concrete FAIL and PASS examples, and a "Why" noting what real problem this guards against.

---

## CAL-1: Async session enforcement (§11.7 #1)

Watch for in diffs touching `apps/api/`:

```python
# FAIL — sync API
db.query(Repo).filter(Repo.id == repo_id).first()
session.commit()

# PASS — async API
result = await session.execute(select(Repo).where(Repo.id == repo_id))
await session.commit()
```

If you see `db.query`, `session.query`, or `.commit()` without `await`, FAIL.

---

## CAL-2: LLM gateway enforcement (§11.7 #2)

Watch for outside `apps/api/dataplat_api/llm/`:

```python
# FAIL
import anthropic
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# FAIL
httpx.post("https://api.anthropic.com/v1/messages", ...)

# PASS
ctx.llm.call(model="...", messages=[...])
```

Any direct LLM SDK import outside the gateway dir is FAIL.

---

## CAL-3: OpenAPI sync (§11.7 #3)

If the diff touches `apps/api/dataplat_api/routers/` or any Pydantic model in `apps/api/dataplat_api/schemas/`, then `packages/api-types/openapi.json` MUST also appear in the diff. If not, FAIL — implementer forgot `make codegen`.

---

## CAL-4: Lineage completeness (§1.2 #4, §2.2)

Any code creating a Commit must populate `lineage_info` with parents + processor + config_hash + inputs. If a commit is created with `lineage_info=None` or `lineage_info={}`, FAIL.

Particularly watch seed/test data — `parents=[]` is OK for a true initial commit, but the field itself must exist.

---

## CAL-5: CAS path discipline (§1.2 #5)

Blob storage paths should be derived purely from `sha256(content)`. Look for:

```python
# FAIL
blob_path = f"{repo_id}/{filename}"
blob_path = f"uploads/{uuid.uuid4()}.bin"

# PASS
blob_path = f"blobs/{sha[:2]}/{sha[2:4]}/{sha}"
```

Filenames belong in Tree entries, not in blob storage paths.

---

## CAL-6: Schema freeze post-publish (§1.2 #4, §3.2)

Look for in-place migrations of Silver/Gold schemas. Schema changes should mint a new commit (and usually a new version). If the diff modifies an existing schema file in place rather than creating a new schema version, FAIL.

---

## CAL-7: Bronze faithfulness (§3.1)

Bronze adapters should not do "semantic cleaning" (dedup beyond exact hash, content rewriting, language filtering). If you see this logic in `plugins/adapter-*`, FAIL — it belongs in Silver processors.

Allowed in Bronze: format normalization (PDF→md, HTML→md, character encoding fixes).

---

## CAL-8: MVP scope discipline (§1.3, §11.6 末段)

These should NOT appear in MVP work. If they do without explicit human approval logged in `claude-progress.txt`, FAIL:

- User self-registration flow
- Password reset email flow
- MFA / OAuth / social login
- Repository-level granular ACL (only `visibility = private|internal` allowed)
- Celery (use RQ)
- Docker-in-Docker for plugin sandboxing (use subprocess)
- Training framework integration code
- Real-time / streaming data (Kafka etc.)

---

## CAL-9: Plugin isolation

Plugin code should not reach into other modules. If `plugins/adapter-foo/` imports from `plugins/processor-bar/` or from `apps/api/`, FAIL. Plugins depend ONLY on `packages/core/`.

---

## CAL-10: Test coverage on happy path + one failure

Any new feature/endpoint/plugin needs at least:
- One test for the success case
- One test for at least one failure mode (invalid input, missing resource, etc.)

If the diff adds production code but no corresponding tests, FAIL.

---

## CAL-11: Bias check — "looks good overall"

If you (the reviewer) are about to write "looks good", "LGTM", "no major issues", or any approval without concrete `file:line` evidence — STOP. That's the bias talking.

Either find at least one specific concern, or explicitly note what was actually checked: "Verified CAL-1, CAL-3, CAL-4 against changes in `services/repo.py` and `routers/repos.py` — no violations found."

Vague approval = no approval.

---

## How to add a new case

When you (the human or reviewer) catch a class of issue not on this list:
1. Add a new CAL-N entry with: name, source section (if from design doc), concrete FAIL and PASS examples, the "Why" (what real problem did this cause).
2. Reviewer reads this file at the start of every Mode B review.
3. Over time, this file becomes the project's institutional memory.
