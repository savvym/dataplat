---
name: verifier-role-scope
description: Verifier sub-agent must only RUN checks and REPORT results — flipping feature_list.json passes flags, appending closing entries, and committing are LEADER tasks per CLAUDE.md §"Sprint workflow" step 10.
metadata:
  type: feedback
---

When invoking the `verifier` sub-agent for sprint verification, **constrain its scope in the prompt**: it must only execute the checks and report PASS/FAIL with evidence. It must NOT:
- Edit `spec/feature_list.json` (passes flag is a leader-only edit)
- Append to `claude-progress.txt`
- Create git commits

**Why:** In sprint S002-F-002 (2026-05-22) the verifier flipped F-002 passes:true, appended its own closing entry, and committed at 1435361 — taking three steps from CLAUDE.md sprint workflow step 10 that belong to the leader. The end state was correct so the leader accepted the commit, but this collapses the audit trail: the leader can no longer write a clean "I verified, then flipped, then committed" narrative because the verifier did all three in one tool turn.

**How to apply:** Every Agent invocation with `subagent_type: verifier` must include in the prompt:

> Your job is ONLY to run checks and report machine-verified ground truth. Do NOT edit `spec/feature_list.json`. Do NOT append to `claude-progress.txt`. Do NOT git commit. The leader will do those after reading your report.

Alternatively, narrow the verifier agent definition's tool allowlist (currently `Read, Bash`) by adding a hook or prompt-level guard. The Bash tool gives it `sed`/`git`/`jq` which is enough rope.

Related: see [[user-role]] and the project's contract-driven sprint discipline in [[project-mvp-scope]].
