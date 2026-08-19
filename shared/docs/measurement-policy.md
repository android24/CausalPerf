# Measurement policy

## Status

This is the normative pilot policy for Startup v0.1. Numerical thresholds are
provisional until variance pilots are completed and the benchmark is frozen.

## Primary design

- Metric: TTID in milliseconds unless the task overrides it.
- Startup mode: cold.
- Physical device preferred; emulator results form a separate stratum.
- Release/profileable build with a task-pinned compilation mode.
- One resolved device per run; cross-device aggregation is not performed.

## Pilot defaults

| Parameter | Default |
|---|---:|
| Environment stabilization runs | 3 |
| Measured iterations per arm | 30 |
| Bootstrap resamples | 10,000 |
| Confidence level | 95% |
| Default practical improvement | max(50 ms, 10%) |
| Maximum invalid samples | 10% |
| Maximum one experiment wall time | task-defined, required |

P90 is reported descriptively at 30 iterations but is not a sole acceptance
criterion. Stable tail claims require a larger task-defined sample.

## Sequence

Use a task-pinned sequence. Preferred local default is randomized blocks with a
recorded seed. If rebuilding makes interleaving impractical, use `A1-B-A2` and
require the two baseline blocks to agree within the task stability bound.

## Reported statistics

- included and excluded sample counts;
- median, P90, median absolute deviation, minimum, and maximum;
- absolute and relative median effect;
- bootstrap confidence interval for the median effect;
- baseline drift between A1 and A2 when reversal is used;
- protected secondary metric changes.

Statistical uncertainty and practical significance are separate gates. A narrow
confidence interval around a trivial effect does not pass; a large but unstable
effect is inconclusive.

## Exclusion policy

Samples are excluded only for preregistered machine-detectable reasons:

- benchmark or application failure;
- device disconnect;
- invalid startup mode;
- thermal status outside task policy;
- compilation state mismatch;
- declared background-load violation;
- corrupted or missing required artifact.

Every exclusion remains in the ledger. Post-hoc trimming based on metric value
is forbidden.

## Environment validity

Capture before each block and after abnormal runs:

- device/OS/build identity;
- battery and charging state;
- thermal status;
- CPU topology and online cores;
- free storage and memory-pressure indicators;
- package compilation state;
- relevant background-load indicators.

If more than 10% of measured samples are invalid, or the baseline drift exceeds
the task limit, return `FLAKY` or `INCONCLUSIVE` rather than adding retries until
a desired result appears.

## Calibration before freeze

For each Startup v0.1 task:

1. run at least three independent sessions on the reference device;
2. estimate within-session and between-session variance;
3. verify the injected defect exceeds natural variance;
4. verify the reference intervention meets the practical threshold;
5. choose final iteration count and drift limit from pilot evidence;
6. freeze the policy with benchmark version and toolchain digests.

Calibration sessions are not qualification evidence. After any change to
sample count, exclusion rules, thresholds, analysis method, mechanism query, or
environment bounds, qualification measurements must be collected from fresh
sessions. Qualification data must never be used to continue tuning the same
task version; a required change starts a new calibration version.
