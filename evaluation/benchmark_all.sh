#!/bin/bash
# Table 4: all 14 combinations x 3 runs, each in a fresh process.
# Every run goes through the combinations in its own shuffled (but fixed) order,
# and waits for the CPU to cool to 55 C (at most 3 minutes) before each one.
#
# usage (on the Pi, in the folder with the clip and the models):
#   bash benchmark_all.sh <results.csv>
# Set PYTHON to the interpreter to use (default: python).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:?usage: bash benchmark_all.sh <results.csv>}"
PY="${PYTHON:-python}"

COMBOS="360,none,none 1080,none,none"
for res in 360 1080; do
    for model in bbox seg; do
        for engine in pytorch onnx ncnn; do
            COMBOS="$COMBOS $res,$model,$engine"
        done
    done
done

for run in 1 2 3; do
    for combo in $(printf '%s\n' $COMBOS | shuf --random-source=<(yes "$run")); do
        IFS=, read -r res model engine <<< "$combo"
        for _ in $(seq 36); do
            temp=$(vcgencmd measure_temp | grep -o '[0-9.]*')
            awk "BEGIN { exit !($temp <= 55) }" && break
            sleep 5
        done
        "$PY" "$HERE/benchmark_engines.py" --res "$res" --model "$model" --engine "$engine" --run "$run" --out "$OUT"
    done
done

"$PY" "$HERE/benchmark_engines.py" --summary "$OUT"
