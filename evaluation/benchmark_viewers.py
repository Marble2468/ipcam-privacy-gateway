"""Section 5.3: masked frame rate per viewer with 1 to 4 concurrent guest viewers.

Run on a PC on the same network as the gateway, with every dashboard closed: any other open
stream also loads the Pi. The script logs in once with the gateway ID and password, which gives
a guest session and so the masked stream, and then holds 1, 2, 3 and 4 /video_feed streams open
at the same time, one step after another. A first step with no viewer gives the load of the
camera capture alone.

For every viewer and step it counts the frames received and how many of them were new camera
frames. The stream loop in gateway/app.py sends each new camera frame once, but sends the latest
one again when no new frame has come for RESEND_INTERVAL (and did so all the time before
2026-11-07). The same camera frame gives the same JPEG bytes, so a frame counts as new when its
bytes differ from the previous frame on that connection.

With --ssh, the Pi's system-wide CPU use (all 4 cores = 100%), temperature and throttling are
read over SSH at the start and end of each step, and each step starts once the Pi has cooled
to 55 °C (at most 5 min). The run stops if any other client is connected to the gateway.

usage: python benchmark_viewers.py --url https://<gateway address>:8443 --ssh <user>@<gateway address> --out viewers.csv
"""
import argparse
import csv
import getpass
import hashlib
import http.client
import json
import os
import ssl
import subprocess
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit

VIEWERS = (0, 1, 2, 3, 4)
IDLE, WARMUP, SECONDS = 30, 10, 60
COOL_TO, COOL_MAX = 55.0, 300
PART = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"  # as written by generate_secure_stream
FIELDS = ["time", "viewers", "viewer", "seconds", "frames", "new_frames", "fps", "new_fps",
          "kb_per_frame", "cpu_pct", "temp_start", "temp_end", "throttled"]


def connect(url):
    u = urlsplit(url)
    if u.scheme == "http":
        return http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # the gateway makes a new self-signed certificate at every start
    return http.client.HTTPSConnection(u.hostname, u.port, context=ctx, timeout=10)


def login(url):
    body = json.dumps({"id": input("gateway ID: "), "pw": getpass.getpass("gateway password: ")})
    conn = connect(url)
    conn.request("POST", "/api/login_idpw", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    resp.read()
    cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    if resp.status != 200 or not cookie.startswith("auth_token="):
        raise SystemExit(f"login failed: HTTP {resp.status}")
    value, _, attrs = cookie.partition(";")
    print("cookie attributes:", attrs.strip())
    return value


class Viewer(threading.Thread):
    def __init__(self, url, cookie):
        super().__init__(daemon=True)
        self.url, self.cookie = url, cookie
        self.counting = self.stop = False
        self.frames = self.new = self.bytes = 0
        self.error = None

    def run(self):
        conn = connect(self.url)
        try:
            conn.request("GET", "/video_feed", headers={"Cookie": self.cookie})
            resp = conn.getresponse()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            buf, last = b"", None
            while not self.stop:
                chunk = resp.read1(65536)
                if not chunk:
                    raise RuntimeError("stream closed by the gateway")
                buf += chunk
                while True:
                    start = buf.find(PART)
                    end = buf.find(PART, start + len(PART)) if start >= 0 else -1
                    if end < 0:
                        break
                    jpeg = buf[start + len(PART):end - 2]  # each part ends with \r\n
                    buf = buf[end:]
                    digest = hashlib.blake2b(jpeg, digest_size=16).digest()
                    if self.counting:
                        self.frames += 1
                        self.new += digest != last
                        self.bytes += len(jpeg)
                    last = digest
        except Exception as e:
            if not self.stop:
                self.error = e
        finally:
            conn.close()


def pi_stats(ssh, port):
    # stderr is not captured, so an SSH prompt or a Tailscale sign-in link shows in the terminal
    cmd = ("head -1 /proc/stat; vcgencmd measure_temp; vcgencmd get_throttled; "
           f"ss -Htn state established '( sport = :{port} )' | wc -l")
    out = subprocess.run(["ssh", ssh, cmd], stdout=subprocess.PIPE, text=True, timeout=120, check=True).stdout.split("\n")
    ticks = [int(x) for x in out[0].split()[1:9]]  # user nice system idle iowait irq softirq steal
    return {"busy": sum(ticks) - ticks[3] - ticks[4], "total": sum(ticks),
            "temp": float(out[1].split("=")[1].rstrip("'C")), "throttled": int(out[2].split("=")[1], 16),
            "clients": int(out[3])}


def cool(ssh, port):
    # wait for the Pi to cool down and for other clients (a dashboard left open) to leave
    t0 = time.monotonic()
    while ((s := pi_stats(ssh, port))["temp"] > COOL_TO or s["clients"]) and time.monotonic() - t0 < COOL_MAX:
        print(f"  waiting: {s['temp']} C, {s['clients']} other connection(s) to the gateway", flush=True)
        time.sleep(10)
    if s["clients"]:
        raise SystemExit(f"{s['clients']} other connection(s) to the gateway: close every dashboard")
    return s["temp"]


def step(n, url, cookie, ssh):
    port = urlsplit(url).port or 443
    temp0 = cool(ssh, port) if ssh else ""
    viewers = [Viewer(url, cookie) for _ in range(n)]
    for v in viewers:
        v.start()
    time.sleep(WARMUP if n else 0)
    started = datetime.now().isoformat(timespec="seconds")
    a = pi_stats(ssh, port) if ssh else None
    for v in viewers:
        v.counting = True
    t0 = time.monotonic()
    time.sleep(SECONDS if n else IDLE)
    for v in viewers:
        v.counting = False
    secs = time.monotonic() - t0
    b = pi_stats(ssh, port) if ssh else None
    for v in viewers:
        v.stop = True
    for v in viewers:
        v.join(15)
    for v in viewers:
        if v.error:
            raise SystemExit(f"{n} viewer(s): {v.error}")
        if v.new == 0:
            raise SystemExit(f"{n} viewer(s): no new frame (camera not connected or masking unavailable?)")
    for s in (a, b) if ssh else ():
        if s["clients"] != n:
            raise SystemExit(f"{n} viewer(s): {s['clients']} connections to the gateway, so another client was connected")

    pi = {"cpu_pct": "", "temp_start": temp0, "temp_end": "", "throttled": ""}
    if ssh:
        # current throttling bits at the end, or "has occurred" bits that appeared during the step
        newly = ((b["throttled"] & ~a["throttled"]) >> 16) & 0xF
        pi.update(cpu_pct=round(100 * (b["busy"] - a["busy"]) / (b["total"] - a["total"]), 1),
                  temp_end=b["temp"], throttled=(b["throttled"] & 0xF) | newly)
    base = {"time": started, "viewers": n, "seconds": round(secs, 1), **pi}
    if not n:
        return [{**dict.fromkeys(FIELDS, ""), **base}]
    return [{**base, "viewer": i + 1, "frames": v.frames, "new_frames": v.new,
             "fps": round(v.frames / secs, 2), "new_fps": round(v.new / secs, 2),
             "kb_per_frame": round(v.bytes / v.frames / 1024, 1)}
            for i, v in enumerate(viewers)]


def summary(rows):
    print(f"{'viewers':>7} {'new FPS per viewer':>19} {'sent FPS per viewer':>20} {'CPU %':>6} {'temp C':>11} {'throttled':>9}")
    for n in VIEWERS:
        rs = [r for r in rows if r["viewers"] == n]
        if not rs:
            continue
        r = rs[0]
        temp = f"{r['temp_start']}-{r['temp_end']}" if r["temp_end"] != "" else ""
        if n:
            new, sent = [x["new_fps"] for x in rs], [x["fps"] for x in rs]
            new_s = f"{sum(new) / n:.1f} ({min(new):.1f}-{max(new):.1f})"
            sent_s = f"{sum(sent) / n:.1f}"
        else:
            new_s = sent_s = "-"
        print(f"{n:>7} {new_s:>19} {sent_s:>20} {r['cpu_pct']:>6} {temp:>11} {r['throttled']:>9}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="gateway address, e.g. https://192.168.1.10:8443")
    ap.add_argument("--ssh", help="SSH target of the Pi, for CPU use, temperature and throttling")
    ap.add_argument("--out", required=True, help="CSV file; rows are appended")
    args = ap.parse_args(argv)

    cookie = login(args.url)
    rows = []
    for n in VIEWERS:
        print(f"{n} viewer(s): {WARMUP if n else 0} s warm-up, {SECONDS if n else IDLE} s measured", flush=True)
        rows += step(n, args.url, cookie, args.ssh)

    new_file = not os.path.exists(args.out)
    with open(args.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    summary(rows)


if __name__ == "__main__":
    main()
