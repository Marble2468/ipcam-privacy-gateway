# IP Camera Privacy Gateway

Code for the paper "Edge Computing-Based Privacy Preservation for IP Cameras Using Two-Track Stream Branching".

A Raspberry Pi 5 sits between a home IP camera and its viewers. The camera talks only to the gateway over the local network. Each viewing device gets one of two streams of the same camera:

```
IP camera ──(local RTSP)──> Raspberry Pi 5 gateway ──┬── registered device (valid ECDSA signature): raw stream
                                                      └── any other device: people blurred (YOLOv8n, NCNN)
```

## Repository layout

| Path | What it is |
|---|---|
| `gateway/app.py` | The gateway: Flask web app, camera capture, masking, device authentication, Telegram alerts and lockdown |
| `evaluation/export_models.sh` | Builds the model files used by the gateway and the evaluation scripts |
| `evaluation/benchmark_engines.py`, `benchmark_all.sh` | Table 4: speed and resource use of PyTorch / ONNX Runtime / NCNN on the Pi |
| `evaluation/sample_clips.py` | Draws the 100 evaluation clips at random with a fixed seed from AI Hub dataset 515 (writes `clips.txt`) |
| `evaluation/download_clips.sh`, `extract_clips.py` | Fetch only those clips (and their keypoint labels) from AI Hub |
| `evaluation/evaluate_masking.py`, `aggregate_results.py` | Table 5: how much of each labelled person the gateway's blur covers |
| `evaluation/results/` | The measurements behind Tables 4 and 5 |

## Requirements

- Raspberry Pi 5 (8 GB used in the paper), Raspberry Pi OS / Debian 13, Python 3.10
- A camera that serves RTSP (tested with a TP-Link Tapo C100)
- A Telegram bot and the chat ID of a private chat with it (for alerts, the fallback PIN and the remote lockdown)

```bash
pip install -r requirements.txt
```

The versions in `requirements.txt` are the ones used for every measurement in the paper.

## Running the gateway

1. Build the models in the folder you will run the gateway from (the model paths are relative):
   ```bash
   bash evaluation/export_models.sh
   ```
   The gateway uses `yolov8n_384x640_ncnn_model`. Where several guest viewers will watch at once, set `MASK_MODEL` and `MASK_IMGSZ` in `gateway/app.py` to the 192×320 model (Section 5.3).
2. Start it:
   ```bash
   python gateway/app.py
   ```
   It serves `https://<pi-address>:8443` with a self-signed certificate (your browser will warn once) and prints a 6-digit registration code on the console.
3. Open the page in the browser that will be the master device and fill in the setup form: the registration code, the camera's RTSP URL ending in `stream1` (the gateway reads the camera's sub-stream, `stream2`), the Telegram bot token and chat ID, and an administrator ID and password.
   The browser creates a P-256 key pair; the private key is stored non-extractable in that browser's IndexedDB and only the public key goes to the gateway. Always open the gateway at the same address from this browser, since the key belongs to that origin.

The setup form is accepted once. Afterwards the gateway keeps `gateway_config.json`, `allowed_public_keys.pem` and, while locked down, `lockdown.flag` in the working directory (all git-ignored). To register a new master device, stop the gateway, delete the first two files and run the setup again.

### Access levels

| Level | How it is reached | What it sees | Lifetime |
|---|---|---|---|
| guest | ID and password | masked stream | 2 h |
| master | ID and password, then the browser signs a one-time challenge (60 s) from the gateway with the registered key | raw stream, settings | 2 h |
| trusted | guest, then the one-time 6-digit PIN that the gateway sends to Telegram when a signature fails (valid 5 min, 5 tries) | raw stream, no settings | 30 min |

The Telegram alert has a lockdown button. Lockdown replaces the JWT signing key, so every session ends at once, and it survives a restart. It is lifted only by pressing "approve release" in Telegram and then signing with the master key within 5 minutes.

If the model cannot be loaded or inference fails, viewers of the masked stream get a black frame, never the raw one.

## Reproducing the results

### Table 4 (Raspberry Pi)
In a folder holding the models and one 1080p clip of AI Hub dataset 515 saved as `clip_1080p.mp4` (we used a training clip outside the Table 5 sample):
```bash
bash evaluation/benchmark_all.sh table4.csv            # 14 configurations x 3 runs, about 50 min
python evaluation/benchmark_engines.py --summary table4.csv
```
Each run is a fresh process; the script waits for the CPU to cool to 55 °C, records throttling, and stops if ultralytics loads a different engine or input shape than requested. Our runs are in `evaluation/results/table4_results.csv`.
The gateway's own input (384×640 at 360p) is measured with `--res 360 --model bbox --engine ncnn --imgsz 384 640`; our runs are in `evaluation/results/table4_384x640.csv`.

### Concurrent viewers (Section 5.3)
From a PC on the same network as a running gateway, with every dashboard closed:
```bash
python evaluation/benchmark_viewers.py --url https://<gateway address>:8443 --ssh <user>@<gateway address> --out viewers.csv
```
It asks for the gateway ID and password, holds 1 to 4 guest streams open at once for 60 s each and counts the new camera frames per viewer; with `--ssh` it also records the Pi's CPU use, temperature and throttling. Our runs are in `evaluation/results/viewers_results.csv` (384×640) and `viewers_results_192x320.csv`.

### Table 5
The videos cannot be redistributed (see Data below). With an AI Hub account and API key:
```bash
# keypoint labels first: download VL1.zip ... VL20.zip (validation split) from AI Hub, then
python evaluation/sample_clips.py <folder with VL zips>   # writes evaluation/clips.txt (we ran Python 3.12.10)
bash evaluation/download_clips.sh <work folder>         # the 100 clips in clips.txt
python evaluation/extract_clips.py <work folder>/clips <VL zips>
python evaluation/evaluate_masking.py <work folder>/clips <results folder>   # on the Pi, about 5 h
python evaluation/evaluate_masking.py <work folder>/clips <results folder 2> 192x320   # the 192x320 comparison rows
python evaluation/aggregate_results.py <results folder>
```
`evaluation/results/table5_per_clip.csv` holds our per-clip results (clips numbered by their line in `clips.txt`) and `table5_run_config.json` the exact settings and package versions; the 192×320 comparison is in `table5_per_clip_192x320.csv` and `table5_run_config_192x320.json`.

### Missed people and box padding (Section 6)
After both `evaluate_masking.py` runs, on the Pi, from the folder with the models:
```bash
python evaluation/padding_test.py <work folder>/clips <padding folder> 192x320=<results folder 2> 384x640=<results folder>   # about 30 min
```
It decodes again only the frames in which a labeled person was not fully covered, runs the gateway detector on them and, for each such person, counts the joints inside a box and the box padding that would have covered all of them. It stops if the result without padding differs from the per-frame results of `evaluate_masking.py`. The padding is an analysis only; the gateway does not pad its boxes. Our results are in `evaluation/results/padding_uncovered_persons.csv` (one row per person, clips numbered as in `clips.txt`) and `padding_test_summary.txt`.

### Table 6
Run the gateway with a camera and use two browsers: the registered one (raw stream) and a second one without the key, e.g. a private window logged in with the same ID and password (masked stream). For the replay case, copy the JSON body of a successful `POST /api/verify_signature` from the registered browser's developer tools and send it again from the second browser; the gateway answers 401 because each challenge can be used only once.

## Paper ↔ code

| Paper | Code |
|---|---|
| Device authentication (challenge–response, non-extractable key) | `gateway/app.py`: `api_get_challenge`, `api_verify_signature`, `signChallenge()` in the dashboard page |
| Two-track stream branching, fail-closed masking | `gateway/app.py`: `generate_secure_stream`, `StreamInferencePipeline` |
| Fallback PIN, trusted level | `gateway/app.py`: `issue_bypass_pin`, `api_verify_pin` |
| Telegram kill switch and lockdown release | `gateway/app.py`: `telegram_long_polling_worker`, `SystemSecurityStateMachine` |
| Table 4 | `evaluation/benchmark_engines.py`, `benchmark_all.sh`, `results/table4_results.csv` |
| Section 5.3, concurrent viewers | `evaluation/benchmark_viewers.py`, `results/viewers_results.csv` |
| Table 5 | `evaluation/evaluate_masking.py`, `aggregate_results.py`, `results/table5_*` |
| Section 6, missed people and box padding | `evaluation/padding_test.py`, `results/padding_*` |

## Known limitations

- Flask's development server with a self-signed certificate; put a proper TLS front end in front of it for real use.
- One registered master key. If that device is lost, delete `allowed_public_keys.pem` on the Pi and restart the gateway; the setup page opens again with a new registration code.
- The Telegram bot token, chat ID and camera RTSP URL are stored in plain text in `gateway_config.json` (the administrator password is stored as a scrypt hash). The gateway accepts only a private chat with the bot (a positive chat ID), so alerts, the PIN and the buttons are limited to the owner's account.
- The gateway runs inference separately for each guest stream, one frame at a time. With the 384×640 input one guest viewer received all 15 FPS of the camera and two received about 9 each (Section 5.3); the 192×320 input serves three at the full rate but covers fewer persons.

## Data

The evaluation uses "Abnormal behavior video data in residential and shared spaces" (National Information Society Agency, AI Hub dataset 515). This research used the dataset "Abnormal behavior video data in residential and shared spaces", which was built with funding from the Ministry of Science and ICT of Korea and support from the National Information Society Agency (NIA) and is available from AI-Hub (www.aihub.or.kr). The data may not be redistributed; applications are open to Korean residents, and use outside Korea needs a separate agreement with the NIA. Only derived numbers are in this repository; following AI Hub's guidance, it contains no file names or labels from the dataset.

## License

AGPL-3.0 (see `LICENSE`), as required by Ultralytics YOLO.

## Authors

- Seokyong Moon — msy96322@sunmoon.ac.kr
- Jooyoung Lee — alth4512@sunmoon.ac.kr
- Hyoseong Kim — hyokim1025@sunmoon.ac.kr
- Taeyoon Lee — yoon5998@sunmoon.ac.kr
- Taek Lee (corresponding) — comtaek76@sunmoon.ac.kr

Division of Computer Science and Engineering, Sun Moon University, 70 Sunmoon-ro 221beon-gil, Tangjeong-myeon, Asan 31460, Republic of Korea.
Supported by the MSIT, Korea, under the National Program for Excellence in SW, supervised by the IITP (2024-0-00023).
