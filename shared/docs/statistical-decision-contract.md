# Statistical decision contract

## Scope

Startup v0.1 supports one confirmatory design: `A1 -> B -> A2`. The design is
declared in a sealed `StatisticalPolicy` before the first treatment sample.
Randomized-interleaved and blocked A/B are future document versions; the v0.1
Schemas reject them instead of silently applying the A1/B/A2 method.

The reference implementation is deterministic and model-independent. Numerical
limits remain provisional until CPU-001 calibration, but their meaning and
decision precedence are frozen here.

## Preregistered inputs

The Prediction owns:

- primary metric and expected direction;
- absolute and relative minimum practical effects;
- `both`/`maximum` or `either` threshold combination;
- protected secondary metrics, their regression direction and maximum tolerated
  regression.

The Statistical Policy owns:

- design and policy version;
- minimum included samples and maximum invalid percentage per arm;
- maximum A1/A2 baseline drift;
- bootstrap resamples, confidence level and seed;
- Bonferroni family definition for protected secondary metrics.

Every MeasurementSet repeats the sample/exclusion limits and must exactly match
the sealed policy. This prevents one arm from receiving a more permissive
post-hoc policy.

## Primary metric

For each arm, retain every sample and use only those marked included by a
preregistered, machine-detectable exclusion code. Report included/excluded
counts, median, P90, median absolute deviation, minimum and maximum.

The estimator is the median of combined A1/A2 minus the B median for an expected
decrease, with sign reversed for an expected increase. A seeded percentile
bootstrap estimates its two-sided confidence interval.

Primary `PASS` requires:

1. minimum included count in every arm;
2. invalid percentage within policy in every arm;
3. A1/A2 drift within policy;
4. the preregistered practical threshold combination;
5. a confidence interval excluding zero in the predicted direction.

Insufficient samples, excessive invalid samples, excessive drift or an interval
crossing zero are `INCONCLUSIVE`. A stable effect that is too small is `FAIL`.

## Protected secondary metrics

Protected metrics are non-inferiority vetoes; they can never rescue a failed
primary metric. For each metric, compute treatment regression relative to the
combined A1/A2 baseline in its declared adverse direction.

For a family of `m` protected metrics, each bootstrap interval uses confidence
`1 - (1 - primary_confidence) / m`. This Bonferroni rule is conservative and
prevents adding secondary metrics from inflating false acceptance.

- lower interval bound above the regression margin: `FAIL`;
- upper bound above the margin but lower bound not above it: `INCONCLUSIVE`;
- upper bound at or below the margin: `PASS`.

Any protected `FAIL` vetoes the experiment. Any protected `INCONCLUSIVE` makes
the overall statistical result inconclusive.

## Invalid samples and exclusions

Invalid samples remain in MeasurementSet and the ledger. Exceeding the invalid
limit is a computed inconclusive result, not a structural parse error. Unknown
or post-hoc exclusion reasons remain contract violations. The runner never
retries or deletes a sample because its value weakens the expected result.

## Interpretation limits

The bootstrap interval quantifies sampling uncertainty within this experiment;
it does not establish population or cross-device validity. CPU-001 Phase 1B
must calibrate iteration count, drift, margins and environment bounds. Any
calibration change creates a new policy version, and Phase 1C qualification must
use fresh measurements.
