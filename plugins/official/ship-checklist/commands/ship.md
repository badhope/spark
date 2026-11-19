---
description: Run the pre-ship checklist before committing or tagging.
---
Run a pre-ship audit for this repository.

Check:
1. Targeted tests and the full suite status; list any skipped/blocked tests.
2. Lint, type check, import contracts and packaging build.
3. Docs/config examples match the code; no stale flags.
4. Version policy: patch-only changes must not consume a minor version.
5. Rollback path and any data/config migration.

Return a checklist with PASS/FAIL and the command used for each item. Do not push.
