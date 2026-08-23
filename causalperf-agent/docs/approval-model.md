# Approval model

## Risk classes

| Class | Examples | Default |
|---|---|---|
| R0 read-only | inspect source, parse trace, query device state | automatic within task |
| R1 bounded execution | build, test, benchmark, install declared APK | one task-level approval |
| R2 source mutation | apply reversible diff inside writable paths | per intervention approval |
| R3 expanded capability | add dependency, enable network, change build config | explicit per action |
| R4 external publication | commit, push, PR, upload artifact, deploy | explicit and outside initial autonomous loop |
| Prohibited | private Ground Truth access, protected-test changes, destructive device/system actions | never |

## Approval record

Every approval contains:

```text
run_id
intervention_id or action scope
risk class
exact file/command/device scope
reason
approver identity reference
timestamp and expiration
plan/diff digest
```

Changing the plan or diff invalidates the approval.

At runtime, an R2+ request must include an approval ID. If the ID is known but
no decision has yet been recorded, the request becomes `APPROVAL_PENDING` and
is not dispatched. An absent ID, invalid/tampered record, mismatched request
digest, wrong run/risk, denial, expiration, or approval recorded after the
Runner's authorization time is a hard denial. Authorization time comes from a
Runner-owned clock, not the Agent request.

## Human review package

Before R2 approval, present:

- hypothesis and supporting/contradicting evidence;
- preregistered prediction and falsification condition;
- exact diff or bounded transformation;
- affected files and anticipated behavior;
- benchmark and correctness commands;
- risk assessment and rollback procedure;
- remaining experiment budget.

## Non-bypassable gates

User approval authorizes an action but does not convert failed integrity,
correctness, environment, or statistical checks into PASS. External publication
always remains a separate decision after experiment completion.

The executable semantics are defined in the
[Runtime Policy Engine](runtime-policy-engine.md). Approval records authorize an
exact request; they do not grant a reusable risk-class capability.
