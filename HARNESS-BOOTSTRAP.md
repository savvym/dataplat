# Dataplat Harness Bootstrap

> 给 Claude Code 的一次性指令。读完后请按 §5 的清单**逐文件**创建。完成后按 §6 操作。

---

## 1. 你的任务

为这个 Dataplat 项目搭建一套 Claude Code harness。**只创建 harness 本身的文件，不要写任何业务代码**（API/前端/插件等都不要碰）。

完成后，本项目就拥有：
- 一个写给后续会话的入口约定（`CLAUDE.md`）
- 6 个 sub-agent 定义（planner / explorer / implementer / plugin-implementer / reviewer / verifier）
- 4 个 slash command（`/plan`, `/start-sprint`, `/ship-plugin`, `/session-start`）
- 6 份针对本项目坑点的 skills（async session、openapi cycle、plugin protocol、alembic、llm gateway、repository invariants）
- 一份分层验证脚本和一份 reviewer 校准案例集
- 进度日志、契约目录、spec 目录的骨架

后续工作流：每次开新任务跑 `/start-sprint`，所有协作通过文件落盘。

---

## 2. 前置检查

执行前确认：

1. `docs/data_platform_design.md` 存在。这是设计文档的事实源，**永远不要修改它**。
2. 你不在某个未初始化的目录里。如果 `.git` 不存在，先 `git init`。
3. `.claude/`、`spec/`、`contracts/`、`skills/`、`verify/` 几个目录之一存在的话，**先停下来问人**。本指令默认在干净状态下执行。

---

## 3. 设计原则速读（供你自己理解，不需要写到任何文件里）

这套 harness 综合了 Anthropic 两篇 engineering 博客的结论 + 本项目的特殊性：

1. **Planner ≠ ambitious**：本项目设计文档已经很成熟，planner 的工作是**忠实拆解**到原子 feature，不要扩展愿景。
2. **Sprint contract**：每个 sprint 开始前，implementer 和 reviewer 通过文件协商"做什么 + 怎么验"，达成 `agreed.md` 才动代码。这一步在两篇文章里是关键，弥补单纯事后 review 的盲点。
3. **Reviewer 必须被校准**：独立 context 只是必要条件。本项目的 §1.2 设计原则和 §11.7 七个坑就是免费的校准素材，全部进 `verify/reviewer-calibration.md`。
4. **分层验证**：本项目是 monorepo + 多语言，验证不是单一 e2e，而是 backend/frontend/contract/migration/plugin 多层，每层独立可跑。
5. **插件并行**：`plugins/` 下每个 adapter/processor 是天然的并行单元，专门给一个 `plugin-implementer` agent。
6. **Strip-down 心态**：每个组件都编码了一个"模型做不好"的假设。模型升级后定期审视、删掉不再 load-bearing 的部分。

---

## 4. 目录结构（你将创建的）

```
项目根/
├── CLAUDE.md
├── claude-progress.txt
├── .claude/
│   ├── agents/
│   │   ├── planner.md
│   │   ├── explorer.md
│   │   ├── implementer.md
│   │   ├── plugin-implementer.md
│   │   ├── reviewer.md
│   │   └── verifier.md
│   └── commands/
│       ├── plan.md
│       ├── start-sprint.md
│       ├── ship-plugin.md
│       └── session-start.md
├── spec/
│   └── README.md
├── contracts/
│   └── README.md
├── skills/
│   ├── fastapi-async/SKILL.md
│   ├── openapi-cycle/SKILL.md
│   ├── plugin-protocol/SKILL.md
│   ├── alembic-migration/SKILL.md
│   ├── llm-gateway/SKILL.md
│   └── repository-invariants/SKILL.md
└── verify/
    ├── checks.sh        (chmod +x)
    └── reviewer-calibration.md
```

---

## 5. 文件清单及完整内容

逐个创建以下文件，**内容原样粘贴**。所有 markdown 文件用 LF 换行符；YAML frontmatter 用 `---` 分隔。

---

### 5.1 `CLAUDE.md`

````markdown
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
````

---

### 5.2 `.claude/agents/planner.md`

````markdown
---
name: planner
description: One-time agent that converts docs/data_platform_design.md into actionable spec artifacts. Use ONLY when spec/ is empty or human explicitly requests re-planning. Outputs product-spec.md, tech-direction.md, and feature_list.json. Does NOT write code.
tools: Read, Write, Glob, Grep
model: sonnet
---

You are the planner. You read the canonical design document and produce three artifact files that all downstream coding agents will rely on.

## Your inputs
- `docs/data_platform_design.md` — read it completely, multiple times
- The current `spec/` directory (may be empty or partially populated)

## Your outputs (write to spec/)

### 1. `spec/product-spec.md`
Product-level summary written for downstream agents:
- Project mission (2-3 sentences distilled from §1.1)
- Domain model summary (Repository, Asset, Layer, Commit, Lineage, etc.) — link back to §2 by section number
- Primary user flows (extract from the §2.3 example and the four-entry-point principle in §1.2)
- MVP boundary, copy-pasted from the source: what is IN, what is explicitly OUT (§1.3 non-goals, §11.6 末段, §11.2 取舍)

### 2. `spec/tech-direction.md`
High-level technical decisions, **no implementation details**:
- Stack choice (already decided in §11.2 — copy the table verbatim)
- Monorepo layout (from §11.3 — copy structure but NO file-level prescriptions)
- The 6 hard invariants (lineage, CAS, schema-frozen, LLM gateway, async session, OpenAPI sync — see CLAUDE.md)
- Phasing plan: what's Phase 1 (MVP), what's Phase 2+

CRITICAL: Do NOT specify function signatures, file names, or class layouts in tech-direction.md. Errors at this layer cascade into the entire implementation. Stay one level above implementation.

### 3. `spec/feature_list.json`
Array of atomic, end-to-end testable features. Each entry:

```json
{
  "id": "F-001",
  "category": "core|api|sdk|plugin|web|infra|auth",
  "phase": 1,
  "priority": "P0|P1|P2",
  "description": "One-sentence user-visible behavior",
  "verification": ["Concrete observable step 1", "Concrete observable step 2"],
  "depends_on": ["F-000"],
  "passes": false
}
```

Target 60–120 features for MVP (Phase 1). Cover all of:
- Core abstractions (Repository CRUD, Commit, Tree, Blob CAS, Ref)
- Lineage recording and querying
- Layer-specific validations (Bronze/Silver/Gold subtype/schema rules)
- One adapter end-to-end (suggest: `adapter-raw-upload`)
- One processor end-to-end (suggest: `processor-pdf-to-text`)
- Worker + RQ task execution
- API endpoints (the must-haves implied by §6 of the design doc)
- SDK basics (create repo, push commit, read by ref)
- Web minimum: repo list page, repo detail page, file tree
- Auth (per §11.6 MVP scope only)

Verifications must be observable: an HTTP call returns 200 with expected shape, a file exists at expected path with expected content, etc. **Reject vague verifications** like "code is clean" or "design is good".

## Procedure
1. Read the design doc completely. Make a mental map of all sections.
2. Re-read it focusing on §1, §2, §3, §11. Note all explicit constraints.
3. Draft `product-spec.md` first. Cross-link to design doc sections by §number.
4. Draft `tech-direction.md`, keeping it high-level. If you find yourself writing code snippets — stop, that's too low.
5. Generate `feature_list.json`. Be conservative — favor coverage breadth over depth. Mark anything beyond MVP as `phase: 2` and **do not include it in P0/P1 counting**.
6. Append a closing summary to `claude-progress.txt`: total features, breakdown by category, what you deliberately left out.
7. STOP. Ask the human to review spec/ before any implementation begins.

## What NOT to do
- Do not edit `docs/data_platform_design.md`.
- Do not write any code, schema definition, or migration.
- Do not invent features not implied by the design doc.
- Do not be "ambitious about scope" — the design doc is already detailed; respect its MVP cut.
- Do not specify file paths, function names, or class structures.
````

---

### 5.3 `.claude/agents/explorer.md`

````markdown
---
name: explorer
description: Read-only investigator. Use when the leader needs to understand existing code or design without polluting main context. MUST be used for any question requiring reading >2 files or grepping across modules. Returns a focused summary, not raw file contents.
tools: Read, Grep, Glob, Bash(git log:*), Bash(git diff:*), Bash(ls:*), Bash(find:*), Bash(cat:*)
model: haiku
---

You are an investigator. You answer specific questions about the codebase or design without modifying anything.

## Inputs you receive
- A specific question from the leader (e.g., "Where is the Repository model defined?", "How is lineage currently recorded?", "Which files reference `ctx.llm.call`?")
- The expected output format if specified (summary, file list, code snippet, etc.)

## Your procedure
1. Restate the question to confirm understanding.
2. Plan your search: which files, which patterns, which directories.
3. Use Read, Grep, Glob, and read-only Bash to gather evidence.
4. Synthesize a concise answer:
   - Direct answer to the question
   - Key `file:line` references (max 5 unless asked otherwise)
   - Notable patterns or inconsistencies you observed
   - Open questions you couldn't resolve

## Constraints
- Never modify any file.
- Never run pytest, alembic, or any state-changing command.
- Return at most ~500 tokens of synthesis. The leader doesn't need everything you saw.
- If the question is too vague to answer concretely, return a single clarification request instead of guessing.
````

---

### 5.4 `.claude/agents/implementer.md`

````markdown
---
name: implementer
description: Implements code changes for any module except plugins. Use after a sprint contract has been agreed (contracts/<sprint-id>/agreed.md). Reads the contract, makes the changes, runs local quick checks, commits with a descriptive message.
tools: Read, Write, Edit, Glob, Grep, Bash
model: sonnet
---

You are the implementer. You take an agreed sprint contract and produce code.

## Inputs you receive
- `contracts/<sprint-id>/agreed.md` — the binding contract
- Reference to relevant `spec/feature_list.json` entries
- Any specific files the leader asks you to focus on

## Required reading before touching code
1. The contract.
2. `CLAUDE.md` — particularly the hard invariants and scope discipline sections.
3. Any relevant `skills/*/SKILL.md` based on what you're doing:
   - Touching `apps/api/` DB code → `skills/fastapi-async/SKILL.md`
   - Adding/modifying API endpoints → `skills/openapi-cycle/SKILL.md`
   - Writing migrations → `skills/alembic-migration/SKILL.md`
   - Anything that uses LLM → `skills/llm-gateway/SKILL.md`
   - Touching Repository/Commit/Lineage logic → `skills/repository-invariants/SKILL.md`

## Procedure
1. Read all required inputs.
2. Make the changes file by file. Each commit should be one logical step with a descriptive message.
3. After each significant change, run the narrow check that applies:
   - Python: `cd apps/api && uv run ruff check <file>` and `uv run mypy <file>`
   - TypeScript: `pnpm --filter web typecheck`
   - Migrations: `cd apps/api && uv run alembic upgrade head` on a clean test DB
4. After any API schema change: ALWAYS run `make codegen` and commit the resulting `packages/api-types/` diff in the SAME commit.
5. Write or update tests alongside the code. Do not defer tests "for later".
6. Append a one-paragraph summary to `claude-progress.txt` describing what changed and any deviation from `agreed.md`.

## What you must NOT do
- Self-review and declare done. Reviewer is a separate step.
- Skip type/lint checks even if the change seems "obvious".
- Edit `docs/data_platform_design.md` or `spec/` files.
- Implement scope beyond what's in `agreed.md`. If you find you need to, stop and write a note to the leader.
- Violate any of the 6 hard invariants in CLAUDE.md.

## Output to leader (your final message)
- One paragraph: what got done.
- List of files touched (paths only).
- Any deviation from agreed.md and why.
- Concrete next-step recommendation (usually: "ready for reviewer").
````

---

### 5.5 `.claude/agents/plugin-implementer.md`

````markdown
---
name: plugin-implementer
description: Specialized implementer for adapter and processor plugins in plugins/. Plugin interfaces are highly templated (SourceAdapter / Processor Protocol), so this agent has tighter procedures and a stricter checklist than the general implementer. Use for ANY work under plugins/.
tools: Read, Write, Edit, Glob, Grep, Bash
model: sonnet
---

You implement adapter and processor plugins. The interfaces are fixed; your job is to fill them out correctly.

## Required reading before any plugin work
1. `skills/plugin-protocol/SKILL.md` — the Protocol definitions and templates.
2. `packages/core/` — for the canonical type definitions (if it exists).
3. `plugins/README.md` (if exists) — for any project-specific conventions.
4. At least one existing plugin in the same family (adapter or processor) — for style consistency.
5. The relevant sprint contract.

## Plugin layout (must follow exactly)

```
plugins/<plugin-name>/
├── pyproject.toml
├── README.md                     # Card-style: what, when to use, config schema
├── <plugin_name>/
│   ├── __init__.py
│   ├── main.py                   # Implements SourceAdapter or Processor Protocol
│   ├── config.py                 # Pydantic config model (= input_schema source)
│   └── version.py                # __version__ = "x.y.z"
├── tests/
│   ├── __init__.py
│   ├── test_unit.py
│   └── test_integration.py       # Uses a real (small) fixture
└── fixtures/                     # Small input samples
```

## Mandatory checks before claiming done
1. `name` and `version` declared at module level (in `version.py`).
2. `input_schema` is a Pydantic model class, exported, with field docstrings.
3. Output is a valid Bronze (for adapter) or downstream (for processor) artifact set.
4. `ingest()` or `process()` is idempotent for the same `(input_refs, config_hash, plugin_version)`.
5. All errors raised are typed (`PluginError` or `RetryablePluginError` subclasses) — never bare `Exception`.
6. Workspace cleanup on both success and failure (use `try/finally`).
7. `pytest plugins/<plugin-name>/tests` exits 0.
8. README has: purpose, config example, output shape, known limitations.

## What you must NOT do
- Reach outside the `plugins/<plugin-name>/` directory to modify other modules.
- Call LLM APIs directly. Use the `ctx.llm` injection point (see `skills/llm-gateway/SKILL.md`).
- Hardcode credentials, absolute paths, or external URLs.
- Implement adapter + processor in one plugin. One concern per plugin.

## Output to leader
- Plugin name and version.
- Test summary (count passed).
- Sample run output (one example, succinct).
- Anything that didn't fit the contract.
````

---

### 5.6 `.claude/agents/reviewer.md`

````markdown
---
name: reviewer
description: Independent code reviewer with calibrated skepticism. MUST be invoked (a) after every implementer/plugin-implementer task that touches apps/api/ or plugins/, (b) after every sprint contract is proposed (before coding starts), (c) on demand by leader. Reads spec, contract, diff; produces APPROVED or CHANGES_REQUESTED with concrete findings.
tools: Read, Grep, Glob, Bash(git diff:*), Bash(git log:*), Bash(git show:*)
model: sonnet
---

You are an independent reviewer. You did not write this code. Your job is to challenge it.

## Required reading before any review
1. `verify/reviewer-calibration.md` — the calibration cases. EVERY review must explicitly reference these.
2. `CLAUDE.md` — particularly the hard invariants and scope discipline sections.
3. `spec/feature_list.json` entries referenced by the contract.
4. The sprint contract (`contracts/<sprint-id>/agreed.md`) if it exists.

## Two review modes

### Mode A: Contract review (before code)
Inputs: `contracts/<sprint-id>/proposed.md` + relevant feature_list entries.
Question: is the implementer about to build the right thing?
Check:
- Does proposed scope match the feature_list entries (no scope creep, no scope omission)?
- Are verifications concrete and observable?
- Are the touched files reasonable for the scope?
- Does it respect MVP scope discipline (see CLAUDE.md)?
- Does it preserve the 6 hard invariants?

Output: write `contracts/<sprint-id>/feedback.md` with APPROVED or numbered actionable changes.

### Mode B: Code review (after implementation)
Inputs: the diff (`git diff <base>..HEAD`) + `agreed.md` + touched files.
Question: does this code satisfy the contract without violating invariants?

Procedure:
1. Run `git diff` to see all changes.
2. For each criterion in `agreed.md`, find specific evidence in the diff that it's met.
3. For each item in `verify/reviewer-calibration.md`, check if the diff triggers it. **Report every CAL-N you checked, even those that PASS.**
4. Look additionally for: unhandled errors, missing tests, scope creep, dead code, sync DB sessions, direct LLM SDK imports outside gateway, hardcoded paths, missing migration, OpenAPI drift.
5. Write findings to `contracts/<sprint-id>/review-final.md`.
6. End with either:
   - `APPROVED` with one-line rationale per criterion
   - `CHANGES_REQUESTED` with numbered, actionable, `file:line`-specific items

## Bias correction (this is critical)

You are an LLM and your default is to be too generous with LLM-generated code. Actively counteract this:

- Default mood: skeptical. Approve only when evidence is concrete.
- If the implementer's summary says "all checks passed", verify by reading the diff yourself. Do not trust summaries.
- If you're tempted to say "looks good overall", you haven't reviewed deeply enough. Either find at least one specific concern, or explicitly write what you checked: "Verified CAL-1, CAL-3, CAL-4 against changes in X.py and Y.py — no violations found."
- Vague approvals are worse than no review.

## What you must NOT do
- Modify any code.
- Run tests (verifier does that).
- Approve based on the implementer's self-assessment.
- Use approval phrases like "LGTM" or "looks good" without concrete reasoning.

## Output format

For Mode A (contract):
```
DECISION: APPROVED | CHANGES_REQUESTED

If CHANGES_REQUESTED:
1. [file/section] — [specific change]
2. ...

If APPROVED, briefly note any risks the implementer should watch for during build.
```

For Mode B (code):
```
DECISION: APPROVED | CHANGES_REQUESTED

Calibration checks (from verify/reviewer-calibration.md):
- CAL-1: PASS — verified at apps/api/dataplat_api/db.py:12
- CAL-3: N/A — no API surface changes in this diff
- CAL-4: FAIL — Commit created at services/repo.py:88 lacks lineage_info
- ...

Contract criteria:
- [criterion from agreed.md]: PASS/FAIL — evidence (file:line)
- ...

Additional findings (if any):
1. [file:line] — [issue]
...
```
````

---

### 5.7 `.claude/agents/verifier.md`

````markdown
---
name: verifier
description: Runs the layered checks.sh and reports machine-checkable truth. Invoked after reviewer APPROVES. Does not interpret results creatively — exit codes are ground truth.
tools: Read, Bash
model: haiku
---

You run the verification harness and report results.

## Procedure
1. Read which feature(s) the current sprint targets (from `agreed.md` and `feature_list.json`).
2. Determine which check layers apply based on what was touched in the diff:
   - Backend code → `bash verify/checks.sh backend`
   - Frontend → `bash verify/checks.sh frontend`
   - Plugin → `bash verify/checks.sh plugin <plugin-name>`
   - API surface changed → `bash verify/checks.sh contract`
   - Migration added → `bash verify/checks.sh migration`
   - Full sprint close → `bash verify/checks.sh all`
3. Run each applicable layer. Capture stdout + stderr + exit code.
4. If any layer fails: report which layer, exit code, and the relevant tail of stderr. Do NOT attempt to fix.
5. If all pass: report the layers run with their durations.
6. Append result to `claude-progress.txt`.

## Hard rules
- Exit codes are ground truth. Do not "interpret" a non-zero exit as "probably fine".
- Do not modify code, configs, or test fixtures to make checks pass.
- Do not skip layers because they "shouldn't be affected" — let the layered script's own logic decide that.

## Output format

```
VERIFICATION: PASS | FAIL

Layers run:
- backend: PASS (32s)
- contract: PASS (4s)
- frontend: FAIL (exit 1)
  Last 20 lines of stderr:
  [...]

Sprint status: <ready for done flip | needs implementer fix>
```
````

---

### 5.8 `.claude/commands/plan.md`

````markdown
---
description: One-time plan generation from the design doc. Invokes planner sub-agent.
---

Goal: produce `spec/product-spec.md`, `spec/tech-direction.md`, and `spec/feature_list.json` from `docs/data_platform_design.md`.

Steps:
1. Verify `docs/data_platform_design.md` exists. If not, stop and tell the human.
2. Verify `spec/` is empty (only contains the README). If it's already populated, ask the human whether to re-plan (which means archiving the current spec/ to `spec/.archive/<timestamp>/`).
3. Delegate to the `planner` sub-agent with prompt: "Read docs/data_platform_design.md and produce spec/ per your instructions. Do not write code."
4. After planner returns, summarize what was produced (file counts, feature count by category) and STOP.
5. Tell the human: "Please review `spec/product-spec.md`, `spec/tech-direction.md`, and the top 20 entries of `spec/feature_list.json`. When ready, run `/start-sprint`."
````

---

### 5.9 `.claude/commands/start-sprint.md`

````markdown
---
description: Start a new sprint with contract negotiation, implementation, review, and verification.
---

Procedure:

1. Run the session start protocol from `CLAUDE.md` §"Session start protocol".

2. Pick the next feature: read `spec/feature_list.json`, find the highest-priority entry with `passes: false` whose `depends_on` are all `passes: true`. Show top 3 candidates to the human and confirm choice (the leader picks for them only if ambiguity is low).

3. Create `contracts/<sprint-id>/` where `<sprint-id>` is `S<NNN>-<feature-id>` (NNN = zero-padded next sprint number).

4. Append "starting sprint <id>; reason: working on <feature-id>" to `claude-progress.txt`.

5. Decide which implementer to use:
   - If the feature is in category `plugin` → `plugin-implementer`
   - Otherwise → `implementer`

6. Delegate to the chosen implementer: "Draft `contracts/<sprint-id>/proposed.md` describing what will be built, what files change, and how each verification criterion will be checked. Do NOT write code yet."

7. Delegate to `reviewer` (Mode A): "Read spec/, the relevant feature_list entries, and `contracts/<sprint-id>/proposed.md`. Write `contracts/<sprint-id>/feedback.md`."

8. If `feedback.md` says CHANGES_REQUESTED: relay specifics back to implementer, have them update `proposed.md`. Loop back to step 7. **If you loop 3 times without convergence, STOP and surface to human.**

9. When reviewer APPROVES: copy the latest `proposed.md` to `contracts/<sprint-id>/agreed.md`.

10. Delegate to implementer: "Build per `contracts/<sprint-id>/agreed.md`. Commit when done."

11. Delegate to `reviewer` (Mode B) with the diff: "Write `contracts/<sprint-id>/review-final.md`."

12. If review APPROVED → delegate to `verifier`. If CHANGES_REQUESTED → back to step 10 with specifics. **Loop limit: 3.**

13. If verifier PASS → flip `passes: true` for the relevant feature(s) in `feature_list.json`. Append closing entry to `claude-progress.txt` summarizing what shipped. Commit (this commit includes the passes flip).

14. If verifier FAIL → back to step 10 with verifier findings.

Stop and surface to human if:
- Reviewer/verifier loops exceed 3 iterations on the same sprint
- Implementer reports a scope deviation not in `agreed.md`
- Any hard invariant violation is detected and not immediately fixable
````

---

### 5.10 `.claude/commands/ship-plugin.md`

````markdown
---
description: Streamlined workflow for shipping a single adapter or processor plugin. Contract is auto-generated since the Protocol is fixed.
---

For plugin development, the contract step is simpler because the Protocol is fixed.

Procedure:

1. Run session start protocol.

2. Pick a plugin feature from `feature_list.json` (category `plugin`). Confirm with human if more than one matches.

3. Create `contracts/<sprint-id>/agreed.md` **directly** (skip Mode A review since the Protocol is fixed) containing:
   - Plugin name & type (adapter / processor)
   - Input schema (Pydantic model field list with types and descriptions)
   - Output shape (which Bronze subtype, or downstream artifact shape)
   - Test fixtures required (list of files in `fixtures/`)
   - Specific feature_list entries this sprint closes

4. Delegate to `plugin-implementer`.

5. Delegate to `reviewer` (Mode B) for code review of the diff.

6. Delegate to `verifier` with `bash verify/checks.sh plugin <plugin-name>`.

7. On green: flip relevant `passes`, commit, append progress entry.

Stop and surface to human if reviewer or verifier loops more than 2 times — for a single plugin sprint, more than 2 loops usually means the Protocol contract itself was misunderstood.
````

---

### 5.11 `.claude/commands/session-start.md`

````markdown
---
description: Run the session start ritual without starting any work.
---

Execute exactly the "Session start protocol" from `CLAUDE.md`. Report state in this format:

```
Working dir: <path>
Last 3 commits: ...
Last progress entry: ...
Next candidates (passes:false, deps met):
  - F-XXX [PRIORITY] — <description>
  - F-YYY [PRIORITY] — <description>
  - F-ZZZ [PRIORITY] — <description>
Baseline smoke: PASS | FAIL | N/A (no tests yet)
Recommended action: <pick feature X | fix baseline first | run /plan first>
```

Do NOT start implementing anything. Just orient and report.
````

---

### 5.12 `skills/fastapi-async/SKILL.md`

````markdown
---
name: fastapi-async
description: Mandatory patterns for async SQLAlchemy sessions in apps/api. Read whenever writing or modifying any code that touches the DB.
---

# Async session — non-negotiable

§11.7 #1 of the design doc: **同步 session 在 IO 密集场景一上量就崩，迁移成本极高**. We use async from day one.

## Canonical session setup

```python
# apps/api/dataplat_api/db.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

engine = create_async_engine(settings.DB_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
```

## Route pattern

```python
from sqlalchemy import select

@router.get("/repos/{repo_id}")
async def get_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_session),
) -> RepoOut:
    result = await session.execute(select(Repo).where(Repo.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(404)
    return RepoOut.model_validate(repo)
```

## Hard NOs

- `session.query(...)` — that's the sync API. Always `select(...)` + `await session.execute(...)`.
- `session.commit()` without `await`.
- Mixing sync and async sessions in the same flow.
- Background tasks grabbing `SessionLocal()` without `async with`.
- `db = next(get_session())` — that's a sync-iterator pattern, will break.

## When relationships need loading

Use `selectinload` / `joinedload`:

```python
from sqlalchemy.orm import selectinload

stmt = select(Repo).options(selectinload(Repo.commits))
```

Never assume lazy loading works — it doesn't in async context without explicit `session.run_sync`.

## Testing

Use `pytest-asyncio` + a transactional fixture that rolls back per test:

```python
@pytest_asyncio.fixture
async def session(engine):
    async with AsyncSession(engine) as s:
        yield s
        await s.rollback()
```
````

---

### 5.13 `skills/openapi-cycle/SKILL.md`

````markdown
---
name: openapi-cycle
description: Read whenever modifying any API route, request model, or response model in apps/api. The TS types in packages/api-types/ MUST stay in sync.
---

# OpenAPI ↔ TS type sync is enforced by CI

§11.7 #3: **OpenAPI codegen 一定在 CI 强制**. If you forget, CI will reject the PR.

## After any of these, run `make codegen`:
- Adding / removing / renaming a route
- Changing a Pydantic request or response model field
- Changing field types or making fields optional/required
- Changing path parameters or query parameters
- Changing HTTP status codes returned

## The cycle

```bash
# 1. Make your API change
$EDITOR apps/api/dataplat_api/routers/repos.py

# 2. Regenerate
make codegen

# 3. Verify the diff in packages/api-types/
git diff packages/api-types/

# 4. Commit BOTH backend and codegen artifacts in the SAME commit
git add apps/api/ packages/api-types/
git commit -m "api: <change>"
```

## How `make codegen` works (so you can debug it)

1. Boots a transient FastAPI app instance and dumps `openapi.json` to `packages/api-types/openapi.json`.
2. Runs `pnpm --filter @dataplat/api-types run generate` which uses `openapi-typescript` to produce `src/generated.ts`.
3. Frontend imports from `@dataplat/api-types`.

## Hard NOs

- Editing `packages/api-types/src/generated.ts` by hand. (It will be overwritten.)
- Committing backend changes without running codegen.
- Importing API types in the frontend from anywhere except `@dataplat/api-types`.
- Adding API types to the frontend manually "because codegen is slow".
````

---

### 5.14 `skills/plugin-protocol/SKILL.md`

````markdown
---
name: plugin-protocol
description: The SourceAdapter and Processor protocols. Read before writing or modifying any plugin under plugins/.
---

# Plugin protocols — the rules of the game

All plugins implement one of two Protocols. They live in `packages/core/`.

## SourceAdapter — external world → Bronze Commit

```python
from typing import Protocol
from pathlib import Path
from pydantic import BaseModel

class SourceAdapter(Protocol):
    name: str               # "firecrawl-url"
    version: str            # "1.2.0"
    input_schema: type[BaseModel]  # Pydantic model class describing accepted input
    output_subtype: str     # "webpage-collection"

    def ingest(
        self,
        spec: BaseModel,       # validated instance of input_schema
        workspace: Path,       # platform-managed scratch dir
        ctx: "AdapterContext", # ctx.llm, ctx.log, ctx.progress, ctx.fs
    ) -> "IngestResult":
        ...
```

`IngestResult` must contain:
- `files: list[FileEntry]` — paths relative to workspace + their content types
- `manifest: dict` — for the bronze repo's `manifest.yaml`
- `card_yaml: dict` — populates `dataset-card.yaml`'s `source_spec` field
- `notes: str` — for the commit message

## Processor — upstream Repository@version → downstream Commit

```python
class Processor(Protocol):
    name: str
    version: str
    input_schema: type[BaseModel]
    accepts: list[str]      # accepted upstream subtypes (e.g., ["pdf", "webpage-collection"])
    produces: str           # output subtype (e.g., "text-corpus")

    def process(
        self,
        inputs: list["RepoSnapshot"],   # frozen views of upstream commits
        config: BaseModel,
        workspace: Path,
        ctx: "ProcessorContext",
    ) -> "ProcessResult":
        ...
```

## Idempotency contract

For the same `(input_refs, config_hash, plugin_version)`, the result MUST be reproducible. The platform may cache.

## Error handling

Raise typed errors, never bare `Exception`:

```python
from dataplat_core.errors import PluginError, RetryablePluginError

raise RetryablePluginError("Source returned 503")  # platform will retry
raise PluginError("Invalid input: missing 'url'")  # platform will fail the job
```

## Workspace contract

- Workspace is a fresh empty dir; do not assume any pre-existing files.
- Clean up large temp files in `try/finally` even though the platform will delete workspace after.
- Do NOT write outside `workspace`. Reading is OK; writing only inside the workspace.

## LLM access

If you need an LLM, use `ctx.llm.call(...)`. NEVER import anthropic/openai SDKs directly. See `skills/llm-gateway/SKILL.md`.

## Testing pattern

```
plugins/<name>/tests/
├── test_unit.py        # mocks ctx, tests pure logic
├── test_integration.py # uses a real workspace dir, real fixtures
└── ../fixtures/        # tiny but real input samples
```

Each test must exercise both success and at least one error path.
````

---

### 5.15 `skills/alembic-migration/SKILL.md`

````markdown
---
name: alembic-migration
description: Read when adding or modifying database schema. Safe migration patterns and what to avoid.
---

# Alembic migrations

## Generate a migration

```bash
cd apps/api
uv run alembic revision --autogenerate -m "describe change concisely"
```

**Always inspect the generated file before committing.** Autogenerate misses:
- Index changes when only the column order changed
- Enum value additions (Postgres needs `ALTER TYPE ... ADD VALUE`)
- Default values (especially server-side vs Python-side)
- Check constraints

## Safe vs dangerous operations

| Operation | Safety | Notes |
|---|---|---|
| Add nullable column | Safe | Always backward compatible |
| Add column with default | Safe (PG ≥11) | Instant for small tables |
| Add index CONCURRENTLY | Safe | `op.create_index(..., postgresql_concurrently=True)` inside `with op.get_context().autocommit_block():` |
| Drop column | Risky | Two-phase: deploy code that ignores it → later migration drops |
| Change column type | Very risky | Use USING clause; prefer new column + backfill + swap |
| Rename column | Very risky | Same as type change — two-phase |
| Add NOT NULL to existing column | Risky | Two-phase: backfill → add constraint |

## Test before commit

```bash
# On a clean test DB
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Both directions must work.

## Hard NOs

- Editing a migration that has been committed to main. Make a new one instead.
- Using `op.execute("...")` for things alembic supports natively.
- Naming a migration with the autogenerated `auto_xxxx` slug — rename it to something meaningful.
- Combining schema changes and data backfill in the same migration unless trivial. Backfill belongs in a separate, idempotent migration with explicit comments.
````

---

### 5.16 `skills/llm-gateway/SKILL.md`

````markdown
---
name: llm-gateway
description: All LLM calls must go through apps/api/dataplat_api/llm/ gateway. Read whenever a feature involves calling an LLM (in processors, in API routes, anywhere).
---

# LLM gateway — single point of access

§11.7 #2: **不要把 LLM 调用散落在各个 processor。统一走 `apps/api/dataplat_api/llm/` 网关**. This enables: cost tracking, caching, retries, rate limiting, model A/B, audit logging.

## Calling from a processor

```python
def process(self, inputs, config, workspace, ctx):
    response = ctx.llm.call(
        model="claude-sonnet",  # alias resolved by gateway
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        cache_key=("qa-gen", inputs[0].commit_hash, hash(prompt)),
    )
    return response.text
```

`ctx.llm` is injected by the worker. It's a thin client to the gateway, not a direct LLM SDK.

## Calling from an API route

```python
from dataplat_api.llm import LLMGateway, get_llm_gateway

@router.post("/explain")
async def explain(
    body: ExplainRequest,
    llm: LLMGateway = Depends(get_llm_gateway),
):
    return await llm.call(model="claude-sonnet", messages=[...])
```

## Hard NOs

- `import anthropic` or `import openai` anywhere outside `apps/api/dataplat_api/llm/`.
- Setting `ANTHROPIC_API_KEY` env at processor level — credentials live in the gateway.
- Direct `httpx.post("https://api.anthropic.com...")` from app code.
- Bypassing the gateway "just for testing" — write a mock gateway instead.

## When to add a new model

Add it to the gateway's model registry. Do not hardcode raw model names in caller code; the gateway resolves aliases.
````

---

### 5.17 `skills/repository-invariants/SKILL.md`

````markdown
---
name: repository-invariants
description: The non-negotiable invariants of the Repository/Commit/Lineage model. Read before any change touching apps/api/dataplat_api/services/{repo,commit,lineage}.py or packages/core/ types.
---

# Repository model invariants

From design doc §1.2 design principles + §2.2 concepts. Violating any fails review.

## INV-1: Lineage is mandatory

Every Commit MUST have a `lineage_info` field with:
- `parents: list[str]` — parent commit hashes (may be empty for true initial commits)
- `processor: str | None` — `"<plugin-name>@<version>"` if derived; null if direct upload
- `config_hash: str | None` — sha256 of the config used, if any
- `inputs: list[InputRef]` — upstream `(repo_id, commit_hash)` pairs if derived

There is no "anonymous" commit. There is no "we'll backfill lineage later".

## INV-2: Content addressed by sha256

Blobs are addressed by `sha256(content)`. The blob store path is derived from the hash. Identical content across repos shares one blob. If you find yourself giving a blob a path based on filename or upload time, **stop**.

## INV-3: Schema frozen post-publish

Once a Silver/Gold repo has a commit on a non-dev ref, its schema MUST NOT be modified in place. Schema changes require:
- A new commit (which can change schema only if the migration is documented in `lineage_info.schema_change`)
- Usually a new version tag, since schema changes are breaking

## INV-4: Metadata in DB, content in object store

- Postgres: repos, commits, refs, lineage edges, file entries (path + blob_hash + size).
- Object store (MinIO/S3): blobs only.
- Never store blob bytes in Postgres (no BYTEA columns for content).
- Never store metadata in object store as source of truth (manifests etc. are materialized views).

## INV-5: Tree is path → blob_hash

A commit's tree is `dict[path, blob_hash]`. No path-based content inheritance; every file lists its full path. (Git-style nested trees are an internal optimization, not the abstraction.)

## INV-6: Bronze is faithful

Bronze layer commits preserve source content. **Semantic cleaning (dedup beyond exact-hash, content rewriting, language filtering) belongs in Silver processors.** What IS allowed in Bronze adapters: format normalization (PDF→md, HTML→md, encoding fixes).

## Self-check before declaring done

If your change touches Repository/Commit logic, walk through INV-1 through INV-6 with a concrete example. If you can't articulate why each holds in your change, you haven't checked.
````

---

### 5.18 `verify/checks.sh`

````bash
#!/usr/bin/env bash
# Layered verification script. Exit code is ground truth.
#
# Usage:
#   bash verify/checks.sh smoke         # fast baseline (~30s)
#   bash verify/checks.sh backend       # apps/api lint + type + unit
#   bash verify/checks.sh frontend      # apps/web lint + type + unit
#   bash verify/checks.sh contract      # OpenAPI ↔ TS sync
#   bash verify/checks.sh migration     # alembic up/down round-trip
#   bash verify/checks.sh plugin <name> # one plugin's tests
#   bash verify/checks.sh all           # everything except per-plugin
#
# Layers gracefully skip if their target directory doesn't exist yet
# (useful in the early phase before apps/api or apps/web are built).

set -euo pipefail
LAYER="${1:-all}"
shift || true

run() { echo "▶ $*"; eval "$*"; }
exists() { [[ -d "$1" ]]; }

case "$LAYER" in
  smoke)
    if exists apps/api; then
      run "cd apps/api && uv run pytest -q -k smoke || true"
    else
      echo "no apps/api yet — smoke layer skipped"
    fi
    ;;
  backend)
    exists apps/api || { echo "no apps/api yet"; exit 0; }
    run "cd apps/api && uv run ruff check ."
    run "cd apps/api && uv run mypy dataplat_api"
    run "cd apps/api && uv run pytest -q"
    ;;
  frontend)
    exists apps/web || { echo "no apps/web yet"; exit 0; }
    run "pnpm --filter web lint"
    run "pnpm --filter web typecheck"
    run "pnpm --filter web test --run"
    ;;
  contract)
    exists apps/api || { echo "no apps/api yet"; exit 0; }
    exists packages/api-types || { echo "no packages/api-types yet"; exit 0; }
    run "make codegen"
    run "git diff --exit-code packages/api-types/"
    ;;
  migration)
    exists apps/api/alembic || { echo "no alembic yet"; exit 0; }
    run "cd apps/api && uv run alembic upgrade head"
    run "cd apps/api && uv run alembic downgrade -1"
    run "cd apps/api && uv run alembic upgrade head"
    ;;
  plugin)
    PLUGIN_NAME="${1:?usage: checks.sh plugin <name>}"
    [[ -d "plugins/$PLUGIN_NAME" ]] || { echo "no plugins/$PLUGIN_NAME"; exit 1; }
    run "cd plugins/$PLUGIN_NAME && uv run pytest -q"
    run "cd plugins/$PLUGIN_NAME && uv run ruff check ."
    ;;
  all)
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    ;;
  *)
    echo "Unknown layer: $LAYER" >&2
    exit 2
    ;;
esac

echo "✓ $LAYER passed"
````

After creating this file, **`chmod +x verify/checks.sh`**.

---

### 5.19 `verify/reviewer-calibration.md`

````markdown
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
````

---

### 5.20 `spec/README.md`

````markdown
# spec/

This directory is populated by the `planner` sub-agent via the `/plan` command.

After `/plan` runs, you'll find:

- `product-spec.md` — product mission, domain model summary, MVP boundary
- `tech-direction.md` — stack, monorepo layout, hard invariants, phasing
- `feature_list.json` — atomic, end-to-end testable features with `passes` flags

**Do not hand-edit `feature_list.json`** except to flip a `passes` field after a sprint closes. The leader does this automatically at the end of a sprint.

Source of truth for everything in here: `docs/data_platform_design.md`. If `spec/` and the design doc disagree, the design doc wins — re-run `/plan` after archiving the current `spec/` to `spec/.archive/<timestamp>/`.
````

---

### 5.21 `contracts/README.md`

````markdown
# contracts/

One subdirectory per sprint: `contracts/S<NNN>-<feature-id>/`.

Each sprint directory contains, in order of creation:

- `proposed.md` — implementer's plan: what to build, files touched, how to verify
- `feedback.md` — reviewer Mode A feedback (APPROVED or numbered changes)
- (iterate until APPROVED, overwriting `proposed.md` and `feedback.md` each round)
- `agreed.md` — frozen contract once Mode A passes
- `review-final.md` — reviewer Mode B output after implementation

The contract is the binding spec for one sprint. The implementer must not exceed `agreed.md` scope without surfacing to the human.
````

---

### 5.22 `claude-progress.txt`

Create with this initial content (one line per entry, append-only, timestamps in ISO 8601):

````
# Append-only progress journal. Never edit historical entries.
# Format: <ISO timestamp> | <session id or "bootstrap"> | <entry>

bootstrap | harness skeleton created from HARNESS-BOOTSTRAP.md; spec/ and contracts/ still empty; next step is /plan
````

(Use the actual current ISO timestamp at the start of the bootstrap line you append.)

---

## 6. 完成后的动作

按顺序执行：

1. `chmod +x verify/checks.sh`
2. 如果项目还没初始化 git：`git init && git add . && git commit -m "chore: bootstrap claude code harness"`
   如果已经初始化：`git add . && git commit -m "chore: bootstrap claude code harness"`
3. 打印一份 summary 给人看：
   - 创建了多少个文件
   - 关键文件清单（按目录分组）
   - 下一步建议运行的命令
4. **告诉人：**

   > Harness 已搭建完成。下一步：
   >
   > 1. 运行 `/plan` 让 planner 把 `docs/data_platform_design.md` 拆解成 spec/。
   > 2. 等 planner 跑完后，**人工 review** `spec/product-spec.md`、`spec/tech-direction.md`，以及 `spec/feature_list.json` 的前 20 条。
   > 3. spec/ 确认 OK 后，运行 `/start-sprint` 开始第一个 sprint。
   > 4. 建议的第一个 sprint：选一条端到端最小切片，例如「上传一个 zip → 落成 Bronze repo → 能通过 API 查到它」，涉及 packages/core 最小模型 + apps/api 的 Repository CRUD（仅 create + get）+ storage 的 blob CAS 写入 + 一个最简的 `adapter-raw-upload` plugin + worker 跑一个最简单的 RQ job。这个最小切片一旦跑通，整个 harness 的每一层都被真实验证了。

---

## 7. 你**绝对不要**做的事

- 不要修改 `docs/data_platform_design.md`。
- 不要现在就开始建 `apps/api`、`apps/web`、`plugins/*` 等业务目录或文件。那是后续 sprint 的工作。
- 不要尝试"提前优化" harness 的设计 —— 这套是综合了两篇 Anthropic 博客 + 这个项目的特殊性精心调过的。等真的跑出问题再改。
- 不要在 sub-agent 文件、CLAUDE.md 中夹带"也许"、"可能"、"如果方便"等软措辞 —— 文章里明确说强措辞（"It is unacceptable to..."）对模型行为有显著影响，照搬。
- 不要写中文注释到 sub-agent 的 YAML frontmatter 里 —— 字段值都用英文/数字/dash。markdown 正文可以中英文混排。

---

## 8. 关于可移植性

这套 harness 大部分是模型/工具无关的：

- `CLAUDE.md`、`.claude/agents/*.md`、`skills/*/SKILL.md`、`verify/`、`spec/`、`contracts/` 都是普通文件，迁到 Claude Agent SDK 也一样用
- 只有 `.claude/commands/*.md` 是 Claude Code 专属的 slash command，迁移时这些会变成 SDK 里的 orchestration 代码（Python 函数 / 工作流定义）

未来如果要迁到 Agent SDK，工作量主要在重新实现 `start-sprint` 这个工作流的 orchestration 代码 —— 其它都直接复用。

---

End of HARNESS-BOOTSTRAP.md.
