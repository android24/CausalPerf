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
- preregistration plus computed integrity, correctness, environment, isolation,
  mechanism, statistical and replication decision table;
- canonical artifact digests and cross-object invariant validation;
- sealed source manifests, raw correctness results and frozen environment
  policy inputs with adversarial no-self-assert tests;
- environment/mechanism/replication gate computation;
- append-only hash-chained ledger;
- pure A1/B/A2 reference evaluator;
- runtime and benchmark Schema validation tests.

Not yet implemented:

- invalid-sample records rather than already-filtered numeric arrays;
- secondary metrics and multiple-comparison handling;
- randomized-interleaved or blocked estimators (rejected by Startup v0.1 and
  require a future versioned contract);
- real Android EnvironmentSnapshot collection;
- authenticated producer identity and protected-view isolation;
- production command/device runner and crash recovery;
- production-grade statistical library review.
