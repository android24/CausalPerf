# Repository tools

Run the complete local verification suite from the repository root:

```bash
sh tools/test_all.sh
```

`validate_repository.py` checks repository layout, tracked generated files,
ownership boundaries, public-task isolation, local Markdown links, and all JSON
Schema definitions. It complements—not replaces—the Bench task validator and
runtime cross-object validators.
