#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from isolation import IsolationHarness, IsolationPolicy, IsolationRunSpec, PrivateCanarySet


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Agent and evaluator through a fail-closed isolation backend")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--canaries", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    policy = IsolationPolicy(load(args.policy))
    canaries = PrivateCanarySet(load(args.canaries))
    spec = IsolationRunSpec.from_document(load(args.run), policy, canaries)
    report = IsolationHarness(policy, canaries).run(spec)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{report['status']} {report['id']} {','.join(report['reason_codes']) or 'OK'}")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
