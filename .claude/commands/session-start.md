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
