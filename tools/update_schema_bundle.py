#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from causalperf_reference.schema_registry import BUNDLE_VERSION, build_bundle


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "shared" / "schema-bundle.lock.json"


def rendered_bundle() -> str:
    return json.dumps(
        build_bundle(ROOT, bundle_version=BUNDLE_VERSION),
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or regenerate the schema bundle lock")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = rendered_bundle()
    if args.write:
        descriptor, temporary = tempfile.mkstemp(prefix="schema-bundle-", dir=LOCK.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(expected)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).replace(LOCK)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        print(f"WROTE causalperf-contracts@{BUNDLE_VERSION}")
        return 0
    actual = LOCK.read_text(encoding="utf-8")
    if actual != expected:
        print("FAIL schema bundle lock is stale; run with --write")
        return 1
    print(f"PASS causalperf-contracts@{BUNDLE_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
