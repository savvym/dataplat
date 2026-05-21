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
