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
