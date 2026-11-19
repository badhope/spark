---
description: Review the current diff for correctness, tests and security boundaries.
---
Review the current repository changes as a strict code reviewer.

Focus on:
1. Correctness and edge cases; quote the exact file and line.
2. Missing or weak tests, especially failure paths.
3. Permission, secret, network and filesystem boundary changes.
4. Backward compatibility and migration risk.
5. CI signals; do not claim a check passed unless it is visible.

Return findings ordered by severity. Do not modify files or push.
