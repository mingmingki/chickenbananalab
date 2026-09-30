#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")/../.." && pwd)"
python_bin="${PYTHON:-python3}"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH="$repo_dir/infra/autotrader:$repo_dir/autotrader"
cd "$repo_dir"
"$python_bin" -m unittest discover -s infra/autotrader/tests -v
"$python_bin" infra/autotrader/check_sources.py
cd "$repo_dir/autotrader"
failed=0
for suite in tests gemini_checks negative_guard_checks rollback_checks rollback_full_checks; do
  # Separate processes preserve each suite's same-named fixture imports.
  if ! "$python_bin" -m pytest -p offline "$suite" -q; then
    failed=1
  fi
done
exit "$failed"
