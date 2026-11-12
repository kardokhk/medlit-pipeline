#!/usr/bin/env bash
# Collect both proof-of-concept journal-years and build the coverage report.
# usage: bash run_all.sh [OUT_DIR]     (default: runs/)
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-runs}"
PY="${PYTHON:-python3}"
"$PY" -m pytest -q tests
for c in bmc_medicine_2024 diabetes_care_2024; do
  "$PY" -m medlit run "configs/$c.yaml" --out "$OUT"
done
"$PY" -m medlit report "$OUT/bmc_medicine_2024" "$OUT/diabetes_care_2024" --out "$OUT/report"
