# Private evaluator package

This directory must never be included in the Agent task package, prompt,
workspace, or artifact search path. It is mounted only for post-run evaluation
under a separate evaluator security boundary.

Contents:

- `ground-truth.json`: causal mechanism and accepted intervention classes;
- `expert-patch.diff`: one validated reference intervention;
- `hidden-tests/`: evaluator-only integrity checks.

The reference patch is illustrative until it has passed the required physical-
device pilot. Its digest is pinned in `ground-truth.json`.

