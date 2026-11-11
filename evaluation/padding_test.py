"""How far outside the gateway's boxes are the persons it did not fully cover?  (6 Discussion)

For each detector input, the frames in which evaluate_masking.py found a labeled person not fully
covered are decoded again, the gateway detector is run on them again, and for every such person:
  covered    - how many of its labeled joints are inside a box
  need       - the smallest padding (px on every side of each box) that would cover all its joints
  need_down  - the same with padding only at the bottom of each box
'none' when no box can cover it that way (e.g. the frame has no box). With no padding the result
must match the per-frame CSV of evaluate_masking.py, and the script stops if it does not.
The padded boxes are only an analysis: the gateway does not pad.

Uses keypoints_by_frame() and the constants of evaluate_masking.py, so the labels, the frame
sampling and the coverage test are the same as for Table 5. Run from the folder with the models.
usage: python padding_test.py <clips folder> <out folder> <WxH results folder> [<WxH results folder> ...]
  e.g. python padding_test.py aihub_515/clips aihub_515/padtest 192x320=aihub_515/results_ncnn192 384x640=aihub_515/results_ncnn384
       python padding_test.py --selftest
Writes <out>/uncovered_persons.csv (one row per person not fully covered) and prints a summary.
"""
import ast
import csv
import json  # noqa: F401 - used by keypoints_by_frame()
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
src = (HERE / "evaluate_masking.py").read_text(encoding="utf-8")
keep = [n for n in ast.parse(src).body
        if (isinstance(n, ast.FunctionDef) and n.name == "keypoints_by_frame")
        or (isinstance(n, ast.Assign) and n.targets[0].__class__ is ast.Tuple
            and {"FRAME_W", "LIGHT_MODEL"} & {t.id for t in n.targets[0].elts})
        or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "STRIDE")]
exec(compile(ast.Module(body=keep, type_ignores=[]), "evaluate_masking.py", "exec"))  # noqa: S102 - our own file

INF = math.inf


def clip_box(b):
    """Box as the gateway draws it: truncated to int, clipped; rows y1..y2-1, columns x1..x2-1."""
    x1, y1, x2, y2 = b
    return max(0, x1), max(0, y1), min(FRAME_W, x2), min(FRAME_H, y2)  # noqa: F821


def need_all_sides(pt, box):
    x, y = pt
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return INF
    return max(x1 - x, x - (x2 - 1), y1 - y, y - (y2 - 1), 0)


def need_bottom(pt, box):
    x, y = pt
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1 or not (x1 <= x < x2 and y >= y1):
        return INF
    return max(y - (y2 - 1), 0)


def person_needs(pts, boxes):
    """(joints covered, padding on every side to cover all, padding at the bottom to cover all)"""
    covered = sum(any(need_all_sides(p, b) == 0 for b in boxes) for p in pts)
    need = max(min((need_all_sides(p, b) for b in boxes), default=INF) for p in pts)
    down = max(min((need_bottom(p, b) for b in boxes), default=INF) for p in pts)
    return covered, need, down


def selftest():
    box = clip_box((10, 10, 20, 30))                       # rows 10..29, columns 10..19
    assert need_all_sides((15, 20), box) == 0
    assert need_all_sides((19, 29), box) == 0 and need_all_sides((20, 29), box) == 1
    assert need_all_sides((15, 33), box) == 4 and need_bottom((15, 33), box) == 4
    assert need_bottom((25, 33), box) == INF and need_bottom((15, 5), box) == INF
    assert person_needs([(15, 20), (15, 33)], [box]) == (1, 4, 4)
    assert person_needs([(15, 20)], []) == (0, INF, INF)
    assert person_needs([(15, 33)], [box, clip_box((14, 30, 16, 35))]) == (1, 0, 0)
    print("selftest ok")


if sys.argv[1:] == ["--selftest"]:
    selftest()
    sys.exit()

import cv2  # noqa: E402
from ultralytics import YOLO  # noqa: E402

clips_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
runs = {}  # "192x320" -> folder with frames/<clip>.csv
for arg in sys.argv[3:]:
    name, folder = arg.split("=")
    runs[name] = Path(folder)
out_dir.mkdir(parents=True, exist_ok=True)
clip_no = {line.split("\t")[2][:-4]: i for i, line in enumerate(
    [l for l in (HERE / "clips.txt").read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")], 1)}

models = {}
for name in runs:
    h, w = map(int, name.split("x"))
    models[name] = (YOLO(f"yolov8n_{h}x{w}_ncnn_model", task="detect"), (h, w))

records, mismatches = [], 0
for clip in sorted(clip_no, key=clip_no.get):
    todo = {}  # input -> {frame: csv row}
    for name, folder in runs.items():
        rows = list(csv.DictReader(open(folder / "frames" / f"{clip}.csv")))
        todo[name] = {int(r["frame"]): r for r in rows if int(r["persons_kp"]) > int(r["persons_full_light"])}
    wanted = set().union(*todo.values())
    if not wanted:
        continue
    kps = keypoints_by_frame(clips_dir / f"{clip}.json")  # noqa: F821
    cap = cv2.VideoCapture(str(clips_dir / f"{clip}.mp4"))
    idx, last = -1, max(wanted)
    while idx < last and cap.grab():
        idx += 1
        if idx not in wanted:
            continue
        ok, frame = cap.retrieve()
        assert ok, f"clip {clip_no[clip]}: cannot decode frame {idx}"
        frame = cv2.resize(frame, (FRAME_W, FRAME_H))  # noqa: F821
        persons = [[(int(x), int(y)) for x, y in (pt for pt in p if pt is not None)] for p in kps.get(idx, [])]
        persons = [p for p in persons if p]
        for name, (model, imgsz) in models.items():
            row = todo[name].get(idx)
            if row is None:
                continue
            r = model(frame, imgsz=imgsz, conf=LIGHT_CONF, classes=[0], verbose=False)[0]  # noqa: F821
            raw = r.boxes.xyxy.cpu().numpy().astype(int)
            boxes = [clip_box(b) for b in raw]
            full = 0
            for p in persons:
                covered, need, down = person_needs(p, boxes)
                if covered == len(p):
                    full += 1
                    continue
                records.append([name, clip_no[clip], idx, len(p), covered, need, down])
            if full != int(row["persons_full_light"]) or len(raw) != int(row["boxes"]) \
                    or len(persons) != int(row["persons_kp"]):
                mismatches += 1
                print(f"  mismatch {name} clip {clip_no[clip]} frame {idx}: fully covered {full} vs "
                      f"{row['persons_full_light']}, boxes {len(raw)} vs {row['boxes']}", flush=True)
    cap.release()
    print(f"clip {clip_no[clip]:3d}: {len(wanted)} frames", flush=True)

with open(out_dir / "uncovered_persons.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["input", "clip_no", "frame", "joints", "joints_covered", "need_px", "need_down_px"])
    w.writerows([r[:5] + ["none" if v == INF else v for v in r[5:]] for r in records])
print(f"frames that differ from the evaluation CSVs: {mismatches}")
assert mismatches == 0, "re-running the detector did not reproduce the evaluation; results not valid"


def mean_coverage(name, pad, key):
    """Per-clip share of persons fully covered (Table 5 definition) if every box had this padding."""
    folder, ratios = runs[name], []
    recovered = {}
    for r in records:
        if r[0] == name and r[key] <= pad:
            recovered[r[1]] = recovered.get(r[1], 0) + 1
    for clip, no in clip_no.items():
        rows = list(csv.DictReader(open(folder / "frames" / f"{clip}.csv")))
        n = sum(int(r["persons_kp"]) for r in rows)
        if n:
            ratios.append((sum(int(r["persons_full_light"]) for r in rows) + recovered.get(no, 0)) / n)
    return 100 * sum(ratios) / len(ratios)


for name in runs:
    rs = [r for r in records if r[0] == name]
    n = len(rs)
    print(f"\n{name}: {n} persons not fully covered")
    print(f"  no joint inside any box   {sum(r[4] == 0 for r in rs):6d} ({100 * sum(r[4] == 0 for r in rs) / n:5.1f}%)")
    for lo, hi in ((1, 5), (6, 10), (11, 20), (21, 9999)):
        k = sum(lo <= r[5] <= hi for r in rs)
        print(f"  needs {lo:2d}-{hi if hi < 9999 else '':>4} px padding    {k:6d} ({100 * k / n:5.1f}%)")
    k = sum(r[5] == INF for r in rs)
    print(f"  no box to pad            {k:6d} ({100 * k / n:5.1f}%)")
    print("  mean per-clip full coverage: " + ", ".join(
        f"{label} {mean_coverage(name, pad, key):.2f}%" for label, pad, key in
        (("as is", 0, 5), ("5 px", 5, 5), ("10 px", 10, 5), ("bottom 5", 5, 6), ("bottom 10", 10, 6), ("bottom 20", 20, 6))))
