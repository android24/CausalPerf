# Hidden correctness suite

This evaluator-only directory must never be mounted into the Agent view.
`suite.json` binds the task/version, source digest, protected overlay
destination, exact public correctness command and static anti-detection tokens.

The Android test currently verifies:

1. all 4096 lookup-table entries against an independent oracle implementation;
2. the complete startup table and independently computed digest;
3. final title, digest and sample values on the first screen;
4. absence of loading or placeholder output at the declared readiness point.

The suite validator additionally rejects application main source that refers to
instrumentation APIs, the private task identity, AndroidX Benchmark APIs or
`testOnly`, and rejects any hidden overlay that replaces a public test.

The private evaluator will materialize the overlay only inside its isolated
workspace after the Agent process has ended. The source and manifest are
SDK-free validated but have not yet compiled or run on Android. Protected-path
digest checks, reference-patch replay and trace mechanism checks remain separate
evaluator gates; they are not assertions inside this instrumentation suite.
