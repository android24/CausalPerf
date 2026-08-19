# Causal validation protocol

## Objective

Determine whether changing a proposed mechanism causes a practically meaningful
performance improvement while preserving required behavior.

## Required sequence

1. **Validate task and environment.** Resolve one device, source revision,
   benchmark, correctness suite, compilation mode, and execution budget.
2. **Reproduce.** Establish that the target regression or injected defect is
   measurable under the declared environment.
3. **Observe.** Collect baseline measurements and trace evidence.
4. **Generate competing hypotheses.** Record at least one plausible alternative
   when evidence permits.
5. **Preregister prediction.** Before treatment, record expected target effect,
   mechanism evidence change, protected metrics, and falsification conditions.
6. **Apply one minimal intervention.** Record diff and rollback procedure.
7. **Verify integrity and correctness.** Stop and roll back on failure.
8. **Measure treatment.** Follow the task sequence and exclusion policy.
9. **Verify mechanism.** Confirm the predicted evidence changed in the expected
   direction; target speedup alone is insufficient.
10. **Replicate or reverse.** Repeat on a clean build or return to baseline when
    the task requires strong causal support.
11. **Decide.** Emit `CAUSALLY_SUPPORTED`, `REJECTED`, or `INCONCLUSIVE` with a
    complete ledger.

## Experimental designs

Preferred order:

1. Randomized interleaved A/B measurements when reinstall/build cost permits.
2. `A1 -> B -> A2` reversal design for stateful or expensive Android builds.
3. Blocked A/B design with matched environment snapshots.

Simple `A then B` is exploratory only and cannot produce the strongest verdict
unless the task documents why order effects are negligible and replication is
performed.

## Causal-support gate

```text
CAUSALLY_SUPPORTED only if
  task integrity == PASS
  AND correctness == PASS
  AND environment validity == PASS
  AND prediction was preregistered
  AND intervention targets the proposed mechanism
  AND mechanism evidence changes as predicted
  AND practical threshold is met
  AND statistical policy passes
  AND required replication or reversal passes
```

## Confound checklist

- build/source revision;
- APK signing and build variant;
- compilation and profile state;
- cold/warm/hot startup state;
- cache and application data state;
- device, OS, ABI, and firmware;
- thermal, battery, charging, and power mode;
- background load and network conditions;
- measurement ordering and elapsed experiment time;
- multiple simultaneous code changes;
- removed or deferred required user-visible work.

Uncontrolled major confounds force `INCONCLUSIVE`.

## Strength levels

| Level | Evidence | Permitted wording |
|---|---|---|
| O1 | Trace/benchmark observation only | correlated, observed contributor |
| E1 | One controlled treatment, gates pass | experimentally supported |
| C1 | Treatment plus mechanism check and replication/reversal | causally supported in declared environment |
| C2 | C1 replicated across declared device strata | causally supported across tested strata |

No result may generalize beyond its tested environment without an explicit
external-validity qualification.
