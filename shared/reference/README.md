# Reference decision loop

This package is a deterministic reference, not the production Agent. It proves
that statistical and causal verdicts can be produced without an LLM.

```bash
PYTHONPATH=shared/reference \
python3 shared/reference/run_synthetic.py

PYTHONPATH=shared/reference \
python3 -m unittest discover -s shared/reference/tests -v
```

Current scope:

- A1/B/A2 median effect and deterministic bootstrap interval;
- baseline-drift, sample-size, practical-effect, and uncertainty gates;
- preregistration, integrity, correctness, environment, mechanism,
  statistical, and replication decision table;
- canonical artifact digests and cross-object invariant validation;
- environment/mechanism/replication gate computation;
- append-only hash-chained ledger;
- pure A1/B/A2 reference evaluator;
- runtime and benchmark Schema validation tests.

Not yet implemented:

- invalid-sample records rather than already-filtered numeric arrays;
- secondary metrics and multiple-comparison handling;
- randomized interleaved analysis;
- real Android EnvironmentSnapshot collection;
- production command/device runner and crash recovery;
- production-grade statistical library review.
