# Startup benchmark tasks

Startup v0.1 begins with five draft single-cause tasks. A draft becomes frozen
only after variance calibration, reference-fix verification, schema validation,
private Ground Truth packaging, and evaluator anti-cheat tests.

| Task | Primary mechanism | Pilot risk |
|---|---|---|
| [cpu-001](cpu-001/SPEC.md) | main-thread computation | implemented draft; pilot pending |
| [io-001](io-001/SPEC.md) | synchronous main-thread file I/O | medium: cache control |
| [binder-001](binder-001/SPEC.md) | synchronous cross-process Binder wait | low-medium |
| [scheduling-001](scheduling-001/SPEC.md) | runnable main thread starved by contention | high: device dependence |
| [gc-001](gc-001/SPEC.md) | startup allocation pressure and GC | high: runtime nondeterminism |

Scheduling and GC remain candidate tasks. If pilots cannot separate their
injected effect from environmental variance, they must be redesigned or moved
out of v0.1 rather than weakened after observing Agent results.

## Shared task requirements

- Cold start with TTID as the default primary metric.
- Synthetic data and no external network dependency.
- One injected primary defect per v0.1 task.
- Functional output identical before and after valid intervention.
- Benchmark and correctness suites outside Agent writable scope.
- Public task package physically separated from private Ground Truth.
- Reference patch validated in at least three independent sessions.

Each task directory contains `reproduction.json`. The manifest is structurally
valid even while a task is a draft, but it must report missing artifacts and
their reasons honestly. `validate_reproduction.py` raises the required evidence
bar as lifecycle advances from `DRAFT` to `IMPLEMENTED`, `CALIBRATED`,
`QUALIFIED`, and `FROZEN`; a filename or lifecycle claim alone is insufficient.
