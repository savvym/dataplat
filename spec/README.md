# spec/

This directory is populated by the `planner` sub-agent via the `/plan` command.

After `/plan` runs, you'll find:

- `product-spec.md` — product mission, domain model summary, MVP boundary
- `tech-direction.md` — stack, monorepo layout, hard invariants, phasing
- `feature_list.json` — atomic, end-to-end testable features with `passes` flags

**Do not hand-edit `feature_list.json`** except to flip a `passes` field after a sprint closes. The leader does this automatically at the end of a sprint.

Source of truth for everything in here: `docs/data_platform_design.md`. If `spec/` and the design doc disagree, the design doc wins — re-run `/plan` after archiving the current `spec/` to `spec/.archive/<timestamp>/`.
