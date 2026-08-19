# Hidden checks

Planned evaluator-only checks:

1. verify public protected-path digests are unchanged;
2. run lookup-table correctness with additional sampled indices;
3. verify application and benchmark do not detect instrumentation or task IDs;
4. inspect required startup work and first-screen readiness semantics;
5. compare trace mechanism evidence before and after intervention;
6. run reference patch and candidate patch through the same A1-B-A2 policy.

Executable hidden tests will be added after the Android project builds on the
pinned toolchain.

