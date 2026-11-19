# Official Spark plugins

These plugins are intentionally command-only: they add prompts, not hooks or
executable tools, so enabling one does not grant code execution.

Install the whole set (each member can still be disabled independently):

```bash
spark plugins install-official --registry plugins/registry.json
spark plugins enable review-checklist
spark plugins disable ship-checklist
```

The registry URL points at this repository and each entry uses `subdir`, so
the installer clones once per member and validates the normal
`spark-plugin.json` manifest.
