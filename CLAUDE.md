# Dataplat — LLM Training Data Management Platform

This file is the entry point. Read it at the start of every session.

## Where things live
- Canonical design: `docs/data_platform_design.md` (do NOT edit; it is the source of truth)
- Planner output: `spec/{product-spec.md, tech-direction.md, feature_list.json}`
- Per-sprint contracts: `contracts/<sprint-id>/{proposed.md, feedback.md, agreed.md, review-final.md}`
- Progress journal (append-only): `claude-progress.txt`
- Reusable procedures: `skills/<name>/SKILL.md` — read the relevant one before any task
- Reviewer calibration cases: `verify/reviewer-calibration.md`

## Hard invariants — violating any of these FAILS review

(Distilled from design doc §1.2 + §11.7)

1. **Lineage is mandatory.** Any Commit MUST record `parents[]` + processor identity + config hash + input refs. No exceptions, no "we'll backfill later".
2. **Storage separation + CAS.** Metadata lives in Postgres; content lives in MinIO/S3 addressed by `sha256(content)`. Never store blob bytes in Postgres.
3. **Schema frozen post-publish.** Once a Silver/Gold repo publishes a commit, its schema MUST NOT be edited in place. Schema changes require a new commit (and typically a new version).
4. **LLM calls go through the gateway.** Never call Anthropic/OpenAI/etc. SDKs directly from a processor, adapter, or random route. Use `apps/api/dataplat_api/llm/` via `ctx.llm.call()` or `LLMGateway` dependency.
5. **Async SQLAlchemy from day one.** Every DB session is async. No `session.query()`, no sync sessions, anywhere in `apps/api/`.
6. **OpenAPI ↔ TS type sync.** Any API schema change MUST be followed by `make codegen`, and the resulting `packages/api-types/` diff committed in the SAME commit. CI will reject mismatches.

## Scope discipline (MVP boundaries)

The design doc explicitly defers these. Do NOT implement them in MVP without human approval:

- Self-registration / password reset email / MFA / OAuth / social login (§11.6)
- Repository-level granular ACL — MVP uses `visibility = private|internal` only (§11.6)
- Celery / Dagster — MVP uses RQ (§11.2)
- Docker-in-Docker plugin sandbox — MVP uses subprocess (§11.2)
- Training frameworks, experiment tracking, Kafka streams (§1.3)

## Session start protocol — RUN BEFORE TOUCHING ANY CODE

1. `pwd && git log --oneline -20`
2. `tail -50 claude-progress.txt`
3. If `spec/feature_list.json` exists: `jq '[.[] | select(.passes==false)] | .[0:5]' spec/feature_list.json`
4. If `docker/docker-compose.dev.yml` exists: `docker compose -f docker/docker-compose.dev.yml up -d`
5. If `apps/api/alembic/` exists: `make migrate` (or `cd apps/api && uv run alembic upgrade head`)
6. Run baseline smoke: `bash verify/checks.sh smoke`
7. If baseline fails: fix THAT first. Do NOT start a new feature on broken main.
8. Pick exactly ONE feature. Proceed to sprint workflow.

## Sprint workflow (standard path for any non-trivial task)

1. Leader picks one feature from `spec/feature_list.json` (lowest `passes:false` with highest priority whose `depends_on` are all `passes:true`).
2. Create `contracts/<sprint-id>/` where sprint-id is `S<NNN>-<feature-id>`.
3. Append "starting sprint <id>" to `claude-progress.txt` with a one-line WHY.
4. Delegate to `implementer` (or `plugin-implementer` for plugin work) — draft `contracts/<sprint-id>/proposed.md`: what to build, what files change, how to verify.
5. Delegate to `reviewer` (Mode A) — read `spec/` + `proposed.md`, write `contracts/<sprint-id>/feedback.md`: APPROVED or numbered changes.
6. Iterate steps 4-5 until reviewer APPROVES. Save final as `agreed.md`.
7. Delegate to implementer to build per `agreed.md`. They commit.
8. Delegate to `reviewer` (Mode B) with the diff and `agreed.md` → `contracts/<sprint-id>/review-final.md`.
9. If APPROVED → delegate to `verifier`. If CHANGES_REQUESTED → back to step 7.
10. If verifier PASS → flip the relevant `passes:true` in `feature_list.json`. Append closing entry to `claude-progress.txt`. Commit.
11. If verifier FAIL → back to step 7 with verifier findings.

## Delegation rules — when to spawn a sub-agent vs. do it inline

| Situation | Action |
|---|---|
| Need to read >2 files / cross-module grep | → `explorer` |
| Plugin (adapter/processor) work | → `plugin-implementer` |
| Cross-module change touching ≥3 of: api / web / sdk / plugin / worker | leader decomposes into sequential tasks |
| Single-file change <50 lines, conversational, or planning/strategy | leader does inline |
| ANY change touching `apps/api/dataplat_api/` | → `reviewer` after implementation |
| After reviewer APPROVES | → `verifier` |
| Writing PLAN / SPEC / contract content | leader does inline (project memory; don't lose to sub-agent context) |

**Before any sub-agent invocation:** append a one-line entry to `claude-progress.txt` stating WHY.

## When to invoke reviewer (calibrated rule, not "always")

The reviewer earns its cost when the task is near the model's edge of reliability. Apply this rule:

- **Always invoke** for: anything touching `apps/api/`, any plugin, any migration, any change crossing module boundaries.
- **Skip** for: typo fixes, single-function pure refactors with existing tests, README updates, comment-only changes.
- **When unsure:** read the diff. If you'd be embarrassed to ship without a second pair of eyes, invoke reviewer.

## feature_list.json rules

- The ONLY editable field is `passes`.
- A feature flips to `passes: true` ONLY after `verifier` reports the relevant checks green.
- It is unacceptable to remove, restructure, or rewrite feature entries to make progress look better. This is a destructive action; reviewer is calibrated to catch it.

## Definition of done (sprint level)

A sprint is `done` iff:
- `contracts/<sprint-id>/agreed.md` exists and every item in it is addressed.
- Relevant `verify/checks.sh <layer>` exits 0.
- `contracts/<sprint-id>/review-final.md` ends with `APPROVED`.
- Affected `passes` flags in `feature_list.json` flipped to `true`.
- `claude-progress.txt` has the closing entry.
- Git commit(s) pushed with descriptive messages.

## Hard rules

- Never edit `docs/data_platform_design.md`.
- Never skip reviewer for changes in `apps/api/` or `plugins/`.
- Never let implementer self-review.
- Never bypass `make codegen` after API changes.
- Never invent a feature not in `spec/feature_list.json` without updating it (with human approval) first.
- Before any sub-agent call, write a one-line WHY entry in `claude-progress.txt`.
- **Always `git push` after finishing work** (after every sprint close, after every commit that concludes a task).
