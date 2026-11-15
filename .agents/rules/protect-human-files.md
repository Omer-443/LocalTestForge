
---
trigger: glob
globs: "fixtures/**, tests/acceptance/**"
description: Protect human-owned fixtures and acceptance tests from AI modifications.
---

# Human-Owned Files Protection

These files are human-owned ground truth.

1. Never edit, rename, delete, move, overwrite, or regenerate files matching these patterns.
2. Never modify test assertions, expected outputs, snapshots, or fixtures.
3. You may read these files to understand expected behaviour.
4. If a test or fixture appears incorrect, stop and explain the issue to the user.
5. Never make tests pass by weakening or changing acceptance tests.
6. Only the human owner can authorize changes to these files.
