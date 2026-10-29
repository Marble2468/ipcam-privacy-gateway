"""Table 4: speed and resource use of one masking configuration on the Raspberry Pi.

Each call measures one combination in its own process and appends one row to the CSV,
so memory from earlier runs cannot carry over. benchmark_all.sh runs every combination.

Per frame: resize to the target resolution, run the model, blur (GaussianBlur 19).
  bbox - blur each person box, as the gateway does
  seg  - blur the person mask dilated like the segmentation baseline
Every engine gets the same input shape (192x320 for 360p, 640x1088 for 1080p).

  latency = resize + inference + blur per frame (decoding excluded: the gateway
            reads the camera in a separate thread). FPS = 1000 / mean latency.
  cpu     = system-wide CPU use (all 4 cores = 100%) averaged over the measured frames
  ram     = peak resident memory of the process

Threads: torch runs with 4 threads for every engine (it also does NMS and mask
post-processing for ONNX and NCNN). OpenCV runs single-threaded in every combination,
as it does in the gateway once ultralytics is imported.

usage: python benchmark_engines.py --res 360 --model bbox --engine ncnn --run 1 --out results.csv
       python benchmark_engines.py --summary results.csv
Run on the Pi in the folder holding the clip and the models from export_models.sh.
"""
import argparse
import csv
import resource
import statistics
import subprocess
import time
from datetime import datetime
from importlib import metadata
from pathlib import Path

import cv2
import numpy as np
import psutil

CLIP = "AIHUB_CLIP.mp4"  # AI Hub 515 training clip, not in the Table 5 sample
WARMUP, MAX_FRAMES, MAX_SECONDS = 30, 900, 60
CONF, BLUR = 0.3, (19, 19)
TORCH_THREADS = 4  # all 4 cores; ultralytics would set 3 (cpu_count - 1)
RES = {360: {"size": (640, 360), "imgsz": (192, 320), "dilate": 11},
       1080: {"size": (1920, 1080), "imgsz": (640, 1088), "dilate": 31}}
BACKEND_FORMAT = {"pytorch": "pt", "onnx": "onnx", "ncnn": "ncnn"}  # AutoBackend.format
BUSY = ("evaluate_masking", "app_final", "benchmark_engines")


def model_file(model, engine, imgsz):
    base = "yolov8n" if model == "bbox" else "yolov8n-seg"
    tag = f"{imgsz[0]}x{imgsz[1]}"
    return {"pytorch": f"{base}.pt",
            "onnx": f"{base}_{tag}.onnx",
            "ncnn": f"{base}_{tag}_ncnn_model"}[engine]


def vcgencmd(arg):
    return subprocess.run(["vcgencmd", arg], capture_output=True, text=True, check=True).stdout.strip()


def temperature():
    return float(vcgencmd("measure_temp").split("=")[1].rstrip("'C"))


def throttled_now():
    # Only the low 4 bits are the current state; the upper bits stay set once anything happened since boot.
    return int(vcgencmd("get_throttled").split("=")[1], 16) & 0xF


def version(pkg):
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def check_idle():
    me = psutil.Process().pid
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        cmd = " ".join(p.info["cmdline"] or [])
        # match python processes only: an ssh "bash -c" wrapper also has the script name in its command line
        if p.info["pid"] != me and "python" in (p.info["name"] or "") and any(b in cmd for b in BUSY):
            raise SystemExit(f"another job is running, stop it first: {cmd}")


def make_step(args, res):
    """Return a function that masks one frame in place."""
    w, h = res["size"]
    if args.model == "none":
        cv2.setNumThreads(0)  # importing ultralytics does this in the other combinations
        return (lambda frame: cv2.resize(frame, (w, h))), None

    from ultralytics import YOLO
    path = model_file(args.model, args.engine, res["imgsz"])
    yolo = YOLO(path, task="detect" if args.model == "bbox" else "segment")
    kernel = np.ones((res["dilate"], res["dilate"]), np.uint8)

    def step(frame):
        frame = cv2.resize(frame, (w, h))
        if args.model == "bbox":
            r = yolo(frame, imgsz=res["imgsz"], conf=CONF, classes=[0], verbose=False)[0]
            for x1, y1, x2, y2 in r.boxes.xyxy.cpu().numpy().astype(int):
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
                if x2 > x1 and y2 > y1:
                    frame[y1:y2, x1:x2] = cv2.GaussianBlur(frame[y1:y2, x1:x2], BLUR, 0)
        else:
            r = yolo(frame, imgsz=res["imgsz"], conf=CONF, classes=[0], retina_masks=True, verbose=False)[0]
            if r.masks is not None:
                mask = cv2.dilate((r.masks.data.cpu().numpy().max(0) > 0.5).astype(np.uint8), kernel) > 0
                frame[mask] = cv2.GaussianBlur(frame, BLUR, 0)[mask]
        return frame

    return step, yolo


def check_backend(yolo, engine, imgsz):
    """Stop if ultralytics ran a different engine or input shape than asked for."""
    backend = yolo.predictor.model
    assert backend.format == BACKEND_FORMAT[engine], f"expected {engine}, ultralytics loaded {backend.format}"
    if engine != "pytorch":
        assert list(backend.metadata["imgsz"]) == list(imgsz), f"model input {backend.metadata['imgsz']} != {imgsz}"


def threads(yolo, engine):
    """Thread count the engine runs with, for the record only."""
    if yolo is None:
        return None
    backend = yolo.predictor.model
    try:
        if engine == "pytorch":
            import torch
            return torch.get_num_threads()
        if engine == "onnx":
            return backend.session.get_session_options().intra_op_num_threads  # 0 = onnxruntime default
        return backend.net.opt.num_threads
    except AttributeError:
        return None


def measure(args):
    check_idle()
    res = RES[args.res]
    temp_start, throttle_start = temperature(), throttled_now()
    step, yolo = make_step(args, res)

    cap = cv2.VideoCapture(CLIP)
    assert cap.isOpened(), f"cannot open {CLIP}"
    latencies, decodes = [], []
    i, started = 0, None
    while True:
        t = time.perf_counter()
        ok, frame = cap.read()
        decode = time.perf_counter() - t
        assert ok, f"clip ended after {i} frames"
        t = time.perf_counter()
        step(frame)
        latency = time.perf_counter() - t
        if i == 0 and yolo is not None:
            check_backend(yolo, args.engine, res["imgsz"])
            # only now: the predictor resets torch threads when it starts on the first frame
            import torch
            torch.set_num_threads(TORCH_THREADS)
        i += 1
        if i <= WARMUP:
            continue
        if started is None:
            started = time.monotonic()
            psutil.cpu_percent(None)  # start of the CPU window
        latencies.append(latency * 1000)
        decodes.append(decode * 1000)
        if len(latencies) >= MAX_FRAMES or time.monotonic() - started >= MAX_SECONDS:
            break
    cpu = psutil.cpu_percent(None)  # average since the start of the window
    cap.release()

    row = {
        "time": datetime.now().isoformat(timespec="seconds"), "run": args.run,
        "res": args.res, "model": args.model, "engine": args.engine,
        "model_file": model_file(args.model, args.engine, res["imgsz"]) if yolo else "",
        "imgsz": "x".join(map(str, res["imgsz"])) if yolo else "",
        "frames": len(latencies),
        "fps": round(1000 / statistics.mean(latencies), 2),
        "latency_ms": round(statistics.mean(latencies), 2),
        "latency_p95_ms": round(float(np.percentile(latencies, 95)), 2),
        "decode_ms": round(statistics.mean(decodes), 2),
        "cpu_pct": round(cpu, 1),
        "ram_peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),  # KiB on Linux
        "temp_start": temp_start, "temp_end": temperature(),
        "throttled": throttle_start | throttled_now(),
        "threads": threads(yolo, args.engine),
        **{p: version(p) for p in ["ultralytics", "torch", "onnxruntime", "ncnn", "opencv-python"]},
    }
    out = Path(args.out)
    new = not out.exists()
    with open(out, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)
    print(f"{args.res}p {args.model} {args.engine} run {args.run}: {row['fps']} FPS, "
          f"{row['latency_ms']} ms, CPU {row['cpu_pct']}%, RAM {row['ram_peak_mb']} MB, "
          f"{row['temp_start']}->{row['temp_end']}C, throttled={row['throttled']}")


def summarize(path):
    """Mean and standard deviation over runs, per combination. Throttled runs are left out."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    groups = {}
    for r in rows:
        if int(r["throttled"]):
            print(f"skipping throttled run: {r['res']}p {r['model']} {r['engine']} run {r['run']}")
            continue
        groups.setdefault((int(r["res"]), r["model"], r["engine"]), []).append(r)
    print(f"{'res':>5} {'model':5} {'engine':8} {'n':>2} {'FPS':>14} {'latency ms':>15} {'CPU %':>12} {'RAM MB':>14}")
    for (res, model, engine), rs in sorted(groups.items()):
        cells = []
        for key in ["fps", "latency_ms", "cpu_pct", "ram_peak_mb"]:
            values = [float(r[key]) for r in rs]
            sd = statistics.stdev(values) if len(values) > 1 else 0.0
            cells.append(f"{statistics.mean(values):.1f} ± {sd:.1f}")
        print(f"{res:>4}p {model:5} {engine:8} {len(rs):>2} {cells[0]:>14} {cells[1]:>15} {cells[2]:>12} {cells[3]:>14}")


parser = argparse.ArgumentParser()
parser.add_argument("--res", type=int, choices=RES)
parser.add_argument("--model", choices=["none", "bbox", "seg"])
parser.add_argument("--engine", choices=["none", "pytorch", "onnx", "ncnn"])
parser.add_argument("--run", type=int)
parser.add_argument("--out")
parser.add_argument("--summary", metavar="CSV")
args = parser.parse_args()
if args.summary:
    summarize(args.summary)
else:
    assert None not in (args.res, args.model, args.engine, args.run, args.out), "need --res --model --engine --run --out"
    assert (args.model == "none") == (args.engine == "none"), "use --engine none only with --model none"
    measure(args)
