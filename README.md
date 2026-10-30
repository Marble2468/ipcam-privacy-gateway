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
| `evaluation/sample_clips.py`, `clips.txt` | The 100 evaluation clips, drawn at random with a fixed seed from AI Hub dataset 515 |
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
   The gateway uses `yolov8n_192x320_ncnn_model`.
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
In a folder holding the models and the 1080p clip `AIHUB_CLIP.mp4` (AI Hub dataset 515):
```bash
bash evaluation/benchmark_all.sh table4.csv            # 14 configurations x 3 runs, about 50 min
python evaluation/benchmark_engines.py --summary table4.csv
```
Each run is a fresh process; the script waits for the CPU to cool to 55 °C, records throttling, and stops if ultralytics loads a different engine or input shape than requested. Our runs are in `evaluation/results/table4_results.csv`.

### Table 5
The videos cannot be redistributed (see Data below). With an AI Hub account and API key:
```bash
bash evaluation/download_clips.sh <work folder>         # the 100 clips in clips.txt
# keypoint labels: download the matching VL*.zip files from AI Hub, then
python evaluation/extract_clips.py <work folder>/clips <VL zips>
python evaluation/evaluate_masking.py <work folder>/clips <results folder>   # on the Pi, about 4 h
python evaluation/aggregate_results.py <results folder>
```
`evaluation/results/table5_clip_summary.csv` holds our per-clip results and `table5_run_config.json` the exact settings and package versions.

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
| Table 5 | `evaluation/evaluate_masking.py`, `aggregate_results.py`, `results/table5_*` |

## Known limitations

- Flask's development server with a self-signed certificate; put a proper TLS front end in front of it for real use.
- One registered master key. If that device is lost, the owner must reinstall the gateway on the Pi.
- The Telegram bot token, chat ID and camera RTSP URL are stored in plain text in `gateway_config.json` (the administrator password is stored as a scrypt hash). Use a private chat with the bot: button presses are checked by chat ID only.
- Each viewer runs its own inference on the Pi.

## Data

The evaluation uses "Abnormal behavior video data in residential and shared spaces" (National Information Society Agency, AI Hub dataset 515). This research used datasets from 'The Open AI Dataset Project (AI-Hub, S. Korea)'. All data information can be accessed through 'AI-Hub (www.aihub.or.kr)'. The data may not be redistributed; applications are open to Korean residents, and use outside Korea needs a separate agreement with the NIA. Only clip names and derived numbers are in this repository.

## License

AGPL-3.0 (see `LICENSE`), as required by Ultralytics YOLO.

## Authors

- Seokyong Moon — msy96322@sunmoon.ac.kr
- Jooyoung Lee — alth4512@sunmoon.ac.kr
- Hyoseong Kim — hyokim1025@sunmoon.ac.kr
- Taeyoon Lee — yoon5998@sunmoon.ac.kr
- Taek Lee (corresponding) — comtaek76@sunmoon.ac.kr

Division of Computer Science and Engineering, Sun Moon University, Republic of Korea.
Supported by the MSIT, Korea, under the National Program for Excellence in SW, supervised by the IITP (2024-0-00023).
