#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

python3.12 tools/validate_repository.py
PYTHONPATH=shared/reference python3.12 -m unittest discover -s shared/reference/tests -v
python3.12 -m unittest discover -s causalperf-bench/tests -v
python3.12 -m unittest discover -s causalperf-agent/tests -v
python3.12 -m unittest discover -s tests -v
python3.12 causalperf-bench/tools/validate_task.py \
  causalperf-bench/tasks/startup/cpu-001/public-task \
  --private-evaluator causalperf-bench/tasks/startup/cpu-001/private-evaluator
python3.12 causalperf-bench/tools/validate_reproduction.py \
  causalperf-bench/tasks/startup/cpu-001 \
  causalperf-bench/tasks/startup/io-001 \
  causalperf-bench/tasks/startup/binder-001 \
  causalperf-bench/tasks/startup/scheduling-001 \
  causalperf-bench/tasks/startup/gc-001
