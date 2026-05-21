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
