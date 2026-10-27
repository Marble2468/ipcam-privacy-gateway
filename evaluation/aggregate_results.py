"""Summarise evaluate_masking.py output into the Table 5 numbers.

usage: python aggregate_results.py <output folder of evaluate_masking.py>
Writes <output>/clip_summary.csv (one row per clip) and prints mean, min and max over clips.
A clip with no labeled noses (or no detected silhouette) has no value for that metric
and is left out of its mean; the printed n says how many clips each number covers.
"""
import csv
import math
import sys
from pathlib import Path

CLIPS = Path(__file__).parent / "clips.txt"


def ratio(a, b):
    return a / b if b else math.nan


def summarise(path):
    with open(path, newline="") as f:
        rows = [{k: int(v) for k, v in r.items()} for r in csv.DictReader(f)]
    total = {k: sum(r[k] for r in rows) for k in rows[0]}
    face_frames = [r for r in rows if r["nose_n"] > 0]
    silhouette_frames = [r for r in rows if r["ref_px"] > 0]
    return {
        "clip": path.stem,
        "frames": len(rows),
        "frames_with_person": sum(r["persons"] > 0 for r in rows),
        # against AI Hub keypoints (independent of both models)
        "face_exposed_light": 1 - ratio(total["nose_light"], total["nose_n"]),
        "face_exposed_seg": 1 - ratio(total["nose_seg"], total["nose_n"]),
        "frames_face_exposed_light": ratio(sum(r["nose_light"] < r["nose_n"] for r in face_frames), len(face_frames)),
        "frames_face_exposed_seg": ratio(sum(r["nose_seg"] < r["nose_n"] for r in face_frames), len(face_frames)),
        "keypoints_covered_light": ratio(total["kp_light"], total["kp_n"]),
        "keypoints_covered_seg": ratio(total["kp_seg"], total["kp_n"]),
        # against the segmentation model's silhouette (the original paper's metric)
        "silhouette_covered_light": ratio(total["ref_covered_px"], total["ref_px"]),
        "frames_silhouette_uncovered_light":
            ratio(sum(r["ref_covered_px"] == 0 for r in silhouette_frames), len(silhouette_frames)),
    }


out_dir = Path(sys.argv[1])
sampled = [line.split("\t")[2][:-4] for line in CLIPS.read_text(encoding="utf-8").splitlines()
           if line and not line.startswith("#")]
missing = [c for c in sampled if not (out_dir / "frames" / f"{c}.csv").exists()]
if missing:
    print(f"warning: {len(missing)}/{len(sampled)} sampled clips have no results yet")

clips = [summarise(p) for p in sorted((out_dir / "frames").glob("*.csv"))]
with open(out_dir / "clip_summary.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(clips[0]))
    writer.writeheader()
    writer.writerows(clips)

print(f"{len(clips)} clips, {sum(c['frames'] for c in clips)} evaluated frames")
for key in list(clips[0])[3:]:
    values = [c[key] for c in clips if not math.isnan(c[key])]
    if values:
        print(f"{key:36s} n={len(values):3d}  mean {100 * sum(values) / len(values):6.2f}%  "
              f"min {100 * min(values):6.2f}%  max {100 * max(values):6.2f}%")
