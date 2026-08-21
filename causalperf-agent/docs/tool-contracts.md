# Agent tool contracts

The Agent proposes typed requests; it never submits a shell program. The
external Policy Engine validates a request against
`../schemas/tool-contract.schema.json`, the capability manifest, budgets and an
approval record before dispatch.

| Tool | Risk | Request binds | Response evidence |
|---|---:|---|---|
| `inspect_source` | R0 | relative root/path allowlist and byte limit | source snapshot or excerpt artifacts |
| `query_trace` | R0 | immutable trace, versioned collector/query and limits | Evidence artifacts |
| `inspect_device` | R0 | resolved hashed device and requested checks | EnvironmentSnapshot artifact |
| `build_variant` | R1 | executable/argv, cwd, env, timeout and output limit | BuildResult/APK/log artifacts |
| `install_apk` | R1 | hashed device, package, APK identity and timeout | installation result/log |
| `run_benchmark` | R1 | command envelope, device/package, data partition and frozen sequence | MeasurementSet/trace/result artifacts |
| `apply_patch` | R2 | intervention, exact patch/baseline digests, paths and approval | new source snapshot and patch result |
| `rollback_patch` | R2 | intervention, strategy, expected baseline and inherited approval | RollbackResult |
| `publish_patch` | R4 | accepted result, patch, destination and separate approval | external publication receipt |

## Non-bypassable rules

- Paths are relative and cannot contain parent traversal.
- Commands are executable plus argument arrays. Shell strings, control
  operators, glob expansion and command substitution are not an interface.
- Device operations bind one hashed serial and one declared package.
- Benchmark calls bind a partition and preregistered sequence digest.
- Patch requests bind the exact approval ID and baseline/patch digests.
- A structurally valid request may still be denied by capability, budget,
  lifecycle, protected-path, environment or approval policy.
