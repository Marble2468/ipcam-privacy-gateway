"""Table 5: how well the gateway's box blur hides people in the sampled AI Hub clips.

Every STRIDE-th frame of each clip is resized to 640x360 as the gateway does, then:
  light  - the gateway detector (YOLOv8n NCNN 384x640 unless another input is given, conf 0.3,
           person only); its boxes are exactly the area the gateway blurs
  seg    - YOLOv8n-seg (NCNN 384x640, conf 0.25, person only); its raw mask is the reference
           silhouette, and the same mask dilated like the segmentation baseline is the
           area that baseline blurs
  labels - AI Hub per-frame person keypoints, independent of both models

One CSV per clip (one row per evaluated frame) is written to <output>/frames/.
Finished clips are skipped, so an interrupted run can simply be restarted.
Summarise with aggregate_results.py.

usage: python evaluate_masking.py <folder with <clip>.mp4 and <clip>.json> <output folder> [detector input, e.g. 192x320]
Model paths are relative to the working directory.
"""
import csv
import json
import os
import platform
import sys
import time
from importlib import metadata
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

STRIDE = 3
FRAME_W, FRAME_H = 640, 360  # gateway resizes every frame to this (gateway/app.py)
LIGHT_MODEL, LIGHT_IMGSZ, LIGHT_CONF = "yolov8n_384x640_ncnn_model", (384, 640), 0.3  # as in gateway/app.py
SEG_MODEL, SEG_IMGSZ, SEG_CONF = "yolov8n-seg_384x640_ncnn_model", (384, 640), 0.25
SEG_DILATE = 11  # baseline dilates 31x31 at 1920x1080, i.e. about 11x11 at 640x360
NOSE = 0  # K0 in the AI Hub keypoint list

COLUMNS = ["frame", "persons", "nose_n", "nose_light", "nose_seg",
           "kp_n", "kp_light", "kp_seg",
           "persons_kp", "persons_full_light", "persons_full_seg",  # persons with >=1 labeled joint / all inside
           "ref_px", "ref_covered_px", "boxes"]


def keypoints_by_frame(label_path):
    """frame index -> persons, each a list of 15 (x, y) points in 640x360.

    None where the point is not labeled (0,0) or falls outside the frame.
    """
    data = json.loads(Path(label_path).read_bytes())
    sx = FRAME_W / int(data["metadata"]["width"])
    sy = FRAME_H / int(data["metadata"]["height"])
    frames = {}
    for fr in data["file"][0]["frames"]:
        persons = []
        for person in fr.get("persons", []):
            pts = []
            for kp in person["keypoints"]:
                x, y = map(float, kp.split(","))
                x, y = x * sx, y * sy
                labeled = (x, y) != (0, 0) and 0 <= x < FRAME_W and 0 <= y < FRAME_H
                pts.append((x, y) if labeled else None)
            persons.append(pts)
        frames[int(fr["frame_index"])] = persons
    return frames


def inside(mask, pt):
    return bool(mask[int(pt[1]), int(pt[0])])


def run_models(frame, light, seg):
    boxes = np.zeros((FRAME_H, FRAME_W), np.uint8)
    r = light(frame, imgsz=LIGHT_IMGSZ, conf=LIGHT_CONF, classes=[0], verbose=False)[0]
    xyxy = r.boxes.xyxy.cpu().numpy().astype(int)
    for x1, y1, x2, y2 in xyxy:
        # same truncation and clipping as the gateway
        boxes[max(0, y1):min(FRAME_H, y2), max(0, x1):min(FRAME_W, x2)] = 1

    # retina_masks returns masks at frame size. Without it masks.data is letterboxed
    # (384x640 here) and resizing it straight to the frame shifts the silhouette.
    r = seg(frame, imgsz=SEG_IMGSZ, conf=SEG_CONF, classes=[0], retina_masks=True, verbose=False)[0]
    if r.masks is None:
        ref = np.zeros((FRAME_H, FRAME_W), np.uint8)
    else:
        ref = (r.masks.data.cpu().numpy().max(0) > 0.5).astype(np.uint8)
    seg_area = cv2.dilate(ref, np.ones((SEG_DILATE, SEG_DILATE), np.uint8))
    return boxes, len(xyxy), ref, seg_area


def evaluate_clip(video, label, light, seg):
    kps = keypoints_by_frame(label)
    cap = cv2.VideoCapture(str(video))
    rows, idx = [], -1
    while cap.grab():
        idx += 1
        if idx % STRIDE:
            continue
        ok, frame = cap.retrieve()
        assert ok, f"{video.name}: cannot decode frame {idx}"
        frame = cv2.resize(frame, (FRAME_W, FRAME_H))
        boxes, n_boxes, ref, seg_area = run_models(frame, light, seg)

        persons = kps.get(idx, [])
        noses = [p[NOSE] for p in persons if p[NOSE] is not None]
        pts = [pt for p in persons for pt in p if pt is not None]
        labeled = [[pt for pt in p if pt is not None] for p in persons]
        labeled = [p for p in labeled if p]
        rows.append([idx, len(persons), len(noses),
                     sum(inside(boxes, pt) for pt in noses), sum(inside(seg_area, pt) for pt in noses),
                     len(pts), sum(inside(boxes, pt) for pt in pts), sum(inside(seg_area, pt) for pt in pts),
                     len(labeled),
                     sum(all(inside(boxes, pt) for pt in p) for p in labeled),
                     sum(all(inside(seg_area, pt) for pt in p) for p in labeled),
                     int(ref.sum()), int((ref & boxes).sum()), n_boxes])
    cap.release()
    assert rows, f"{video.name}: no frames read"
    if abs(idx + 1 - len(kps)) > 1:
        print(f"  warning: {video.name} has {idx + 1} frames but {len(kps)} labeled frames")
    return rows


def version(pkg):
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


clips_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
if len(sys.argv) > 3:  # another gateway input, e.g. 192x320 (Table 5 comparison); NCNN model from export_models.sh
    h, w = map(int, sys.argv[3].split("x"))
    LIGHT_MODEL, LIGHT_IMGSZ = f"yolov8n_{h}x{w}_ncnn_model", (h, w)
(out_dir / "frames").mkdir(parents=True, exist_ok=True)

config = {
    "stride": STRIDE, "frame_size": [FRAME_W, FRAME_H], "classes": [0],
    "light": {"model": LIGHT_MODEL, "imgsz": LIGHT_IMGSZ, "conf": LIGHT_CONF},
    "seg": {"model": SEG_MODEL, "imgsz": SEG_IMGSZ, "conf": SEG_CONF, "retina_masks": True,
            "dilate": SEG_DILATE},
    "python": platform.python_version(), "platform": platform.platform(),
    "packages": {p: version(p) for p in
                 ["ultralytics", "torch", "onnxruntime", "ncnn", "opencv-python", "numpy"]},
}
config = json.loads(json.dumps(config))  # tuples -> lists, so it compares equal to the saved file
cfg_path = out_dir / "run_config.json"
if cfg_path.exists():
    assert json.loads(cfg_path.read_text()) == config, \
        "settings or package versions changed since the last run; use a new output folder"
else:
    cfg_path.write_text(json.dumps(config, indent=2))

light = YOLO(LIGHT_MODEL, task="detect")
seg = YOLO(SEG_MODEL, task="segment")
videos = sorted(clips_dir.glob("*.mp4"))
for i, video in enumerate(videos, 1):
    out = out_dir / "frames" / (video.stem + ".csv")
    if out.exists():
        continue
    start = time.time()
    rows = evaluate_clip(video, video.with_suffix(".json"), light, seg)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    os.replace(tmp, out)  # a clip only counts as done once its CSV is complete
    print(f"[{i}/{len(videos)}] {video.stem}: {len(rows)} frames, {time.time() - start:.0f}s", flush=True)
