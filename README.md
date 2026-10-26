# CT Monitor (Cycle Time Monitor)

A machine-vision system that measures **operator cycle time** on manufacturing production lines. A camera at each workstation is analysed with a YOLO object-detection model; the order in which objects appear in user-defined regions of interest (ROIs) is translated into work cycles. Each completed cycle is timed, classified (OK / too long / too short), recorded to video and CSV, and reported to a central master server.

> **Status:** active development. Sections marked `TODO` could not be verified from the code and should be completed by the maintainer.

---

## Table of contents

1. [Features](#features)
2. [How it works](#how-it-works)
3. [Architecture](#architecture)
4. [Repository layout](#repository-layout)
5. [Requirements](#requirements)
6. [Installation](#installation)
7. [Running the application](#running-the-application)
8. [Configuration](#configuration)
9. [Master server integration](#master-server-integration)
10. [Output files](#output-files)
11. [Troubleshooting](#troubleshooting)
12. [Development notes](#development-notes)
13. [License](#license)

---

## Features

**Detection and cycle logic**
- Real-time object detection with YOLO (Ultralytics), configurable confidence threshold (default `0.25`) and input size (`640`)
- Choose which detected classes are tracked, directly from the GUI
- ROI-based cycle engine: a cycle is built from an `input` ROI, an ordered list of `sequence` ROIs, and an `output` ROI
- Sequence ROIs are either **mandatory** or **expected**; each is shown on the live preview as completed (`OK`) or skipped (`SKIP`)
- Cycle labelling: every cycle is tagged as **Normal** or **Anomaly** (sub-types **Long** and **Short**), and may additionally carry **Skipped**, **Genesis** or **Late** tags (see [Cycle labels](#cycle-labels))
- Cycles shorter than a minimum time are not counted, and unfinished cycles are discarded after a timeout

**Operation**
- Tkinter desktop GUI with a *setting* mode (configure ROIs, camera, model) and a *production* mode
- Input from a USB camera or from a video file (useful for testing and tuning)
- Per-cycle video recording and automatic storage management
- Local CSV report per session and a frame-level text log
- Operator login before the application can be used

**Connectivity and resilience**
- Reports each cycle to the master server over HTTP and receives live parameter updates over Socket.IO
- Startup health check of the master server (WebSocket connection, heartbeat, HTTP probe)
- Station identity is cached locally, so recording keeps working when the server is offline
- Manual reconnect and the ability to change the master IP/port from the GUI (with automatic restart when required)
- Synchronisation of recorded video sizes to the server

**Industrial logic**
- Built-in editors for the logic configuration and for PLC-style (ladder) logic, with export support (`plc_*.py`, `ld_editor.py`)

---

## How it works

1. **Capture** – `camera_manager` reads frames from the selected camera or video file (default 640×480, 24 FPS).
2. **Detect** – frames are pushed into a small bounded queue (size 2; stale frames are dropped rather than queued) and processed by a background YOLO thread.
3. **Evaluate** – detections (bounding boxes with labels) are passed to `LogicCycleEngine` on every inference, including frames with no detections, so dwell and grace timers can expire correctly.
4. **Track the cycle** – the engine marks ROIs as done or skipped in order. When the output ROI is reached the cycle is complete; if the process is aborted or times out, the cycle is discarded.
5. **Classify** – the measured cycle time is compared against the limits (see [Cycle timing parameters](#cycle-timing-parameters)) and labelled Normal, or Anomaly (Long / Short). Skipped sequence ROIs and other conditions add extra tags (see [Cycle labels](#cycle-labels)).
6. **Record and report** – the cycle is appended to the CSV report, the video clip is saved, and the result is sent to the master server.

### ROI colours on the live preview

| Role | Colour | Label |
|---|---|---|
| Input | green | `IN` |
| Output | blue | `OUT` |
| Sequence – mandatory | orange | `<n>:M` |
| Sequence – expected | yellow | `<n>:E` |

A completed ROI gets an `OK` suffix and a thicker border; a skipped one gets `SKIP`.

### Cycle labels

Labels are **not mutually exclusive**. A single cycle can be, for example, *Anomaly* + *Short* + *Skipped: mask*, as in the "Anomaly Videos" dashboard view.

| Dashboard label | Meaning | CSV / internal value |
|---|---|---|
| **Normal** | Cycle time is within the configured limits | `anomaly = NO` (internal type `OK`) |
| **Anomaly** | Parent category for any out-of-limit cycle | `anomaly = YES_LONG` or `YES_SHORT` |
| ↳ **Long** | Cycle time is above the anomaly limit | `YES_LONG` (internal `LONG`) |
| ↳ **Short** | Cycle time is below the short-anomaly limit. The dashboard shows the limit next to the cycle time (for example `11.65 s  < 14 s`) | `YES_SHORT` (internal `SHORT`) |
| **Skipped** | One or more sequence ROIs were not completed. The reason is shown as `Skipped: <name>` (for example `Skipped: mask`) | stored in `anomaly_label` |
| **Genesis** | `TODO`: define (the CSV has a `genesis` column) | `genesis = YES` |
| **Late** | `TODO`: define | `TODO` |

`TODO`: confirm the exact trigger of each label against `logic_cycle_engine.py` and the dashboard code. In particular, confirm whether a skipped mandatory ROI is what makes a cycle Short.

---

## Architecture

```
 Camera / video file
        │
        ▼
 camera_manager ──► YOLOInference (thread) ──► LogicCycleEngine
                                                    │
                  ┌─────────────────────────────────┼───────────────────────┐
                  ▼                                 ▼                       ▼
           CycleReporter (CSV)          VideoRecorder / StorageManager   ApiSender
                                                                            │  HTTP POST /api/cycle
                                                                            ▼
                                                                    Master server
                                                                            ▲
                         TaktTimeSource (Socket.IO push + HTTP refresh) ────┘
```

Main classes in `main.py`:

| Class | Responsibility |
|---|---|
| `CTMonitorApp` | Tkinter GUI, application state, wiring of all components |
| `YOLOInference` | Background inference thread, drawing of detections, feeding the cycle engine |
| `ROIViewer` | Read-only overlay of the ROIs from the logic config on the preview |
| `TaktTimeSource` | Receives takt time / timeout / limits from the server (push and pull) |
| `CycleReporter` | Writes the per-session CSV report |
| `FrameLogger` | Timestamped frame-level log |

---

## Repository layout

| Path | Description |
|---|---|
| `main.py` | Application entry point: GUI, server connection, YOLO inference |
| `camera_manager.py` | Camera discovery and capture |
| `video_recorder.py` | Per-cycle video recording |
| `storage_manager.py` | Storage management for recorded videos |
| `logic_cycle_engine.py` | ROI-based cycle logic engine |
| `logic_editor.py` | Editor/validator for `logic_config.xml` |
| `cycle_tracker.py`, `logic_bridge.py` | `TODO`: describe role |
| `plc_engine.py`, `plc_logic.py`, `plc_editor.py`, `plc_export.py`, `ld_editor.py` | PLC / ladder-diagram logic and tooling |
| `hand_detector.py` | `TODO`: describe role |
| `api_sender.py` | Sends cycle results to the master server |
| `size_sync.py` | Syncs recorded video sizes to the server |
| `fd_auth.py`, `fa_keycore.py` | Authentication / key handling (`TODO`: describe) |
| `apply_step3b.py` | One-off patch script (consider moving or removing) |
| `config/` | Runtime configuration (see [Configuration](#configuration)) |
| `model/` | Detection models (`TODO`: list the model file(s) used) |
| `flag_roi/` | `TODO` |
| `backend/` | Master server (`TODO`: stack and how to run) |
| `frontend/` | Dashboard (`TODO`: stack and how to run) |
| `LICENSE` | Apache License 2.0 |

---

## Requirements

- Python 3 (`TODO`: tested version)
- A desktop environment with Tkinter (included with most Python installers)
- Python packages, derived from the imports in `main.py`:
  - `opencv-python`
  - `numpy`
  - `Pillow`
  - `python-socketio` with WebSocket support (`websocket-client`)
  - `ultralytics` (optional at import time, but required for detection)
- FFmpeg available on `PATH` (`TODO`: confirm whether it is mandatory)
- A USB camera or a sample video file
- A reachable master server (the app can start without one only if a cached station identity exists)

---

## Installation

```bash
git clone https://github.com/jukjuk6969/cycle-time.git
cd cycle-time

python -m venv venv
# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install opencv-python numpy Pillow "python-socketio[client]" websocket-client ultralytics
```

`TODO`: add a `requirements.txt` (for example via `pip freeze > requirements.txt`) so installation becomes `pip install -r requirements.txt`.

Place your trained YOLO weights in `model/` (or load them from the GUI).

---

## Running the application

```bash
# list all command-line options
python main.py --help

# start and point the app at a specific master server
python main.py --endpoint http://<MASTER_IP>:3000/api/cycle
```

Options known from the code: `--endpoint`, plus station, line, takt time and operator arguments (`TODO`: paste the exact flags from `--help`).

**Master server selection order**

1. `--endpoint` on the command line
2. A target saved from the GUI that takes effect after restart (used once)
3. The last master that connected successfully
4. Built-in default: `192.168.137.2:3000`

**Startup sequence**

1. The GUI opens in *setting* mode and loads `config/logic_config.xml`.
2. The operator logs in.
3. In the background the app verifies the master server: WebSocket connect → heartbeat (`ping`/`pong`) → `GET /api/summary`. It makes two attempts.
4. The station configuration (station ID, line, name, takt time, limits) is fetched from the server and cached to `config/station_identity.json`.
5. If the server is unreachable, the cached identity is used and local recording continues.

---

## Configuration

All runtime configuration lives in `config/`.

| File | Purpose |
|---|---|
| `ct_settings.json` | Cycle timing and recorder settings (below) |
| `logic_config.xml` | ROI definitions and cycle sequence (`TODO`: add an example) |
| `server_config.json` | Last connected master (`host`, `port`, `last_ok_at`) and an optional one-time `pending` target |
| `station_identity.json` | Cached station identity from the server |
| `setup_state.json` | Saved setup state restored at startup (`TODO`: describe fields) |

### Cycle timing parameters

Defaults used when `config/ct_settings.json` is missing a key:

| Key | Default | Meaning |
|---|---|---|
| `RECORDER_START_DELAY` | `0.5` s | Delay before recording starts |
| `RECORDER_IDLE_TIMEOUT` | `5.0` s | Idle time before the recorder stops |
| `SEQUENCE_COMPLETE_DELAY` | `1.0` s | Delay after the sequence completes |
| `ANOMALY_TIMER` | `20.0` s | A cycle longer than this is flagged LONG |
| `DISCARD_TIMEOUT` | `300.0` s | A cycle exceeding this is discarded |
| `MIN_CYCLE_TIME` | `3.0` s | Minimum duration for a valid cycle |
| `SHORT_ANOMALY_LIMIT` | `0.0` s | A cycle shorter than this is flagged SHORT; `0` disables |

When connected, the server can override takt time, anomaly limit, discard timeout, minimum cycle and short-anomaly limit at runtime.

---

## Master server integration

The station acts as a client of a master server (default port `3000`) and also runs a small local service on port `5001` (`fd_slave_api`) that registers the station with the master.

**HTTP endpoints used**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/cycle` | Report a finished cycle |
| `GET` | `/api/summary` | Startup health probe |
| `GET` | `/api/stations` | Pull station parameters (takt time, limits) |

**Socket.IO events received**

| Event | Effect |
|---|---|
| `pong` | Reply to the startup `ping` heartbeat |
| `takt_time_update` | Updates takt time (also used as the target cycle time) |
| `roi_timeout_update` | Updates the discard timeout |
| `min_cycle_update` | Updates the minimum cycle time |
| `short_anomaly_update` | Updates the short-anomaly limit |
| `station_update` | Updates the station name shown in the GUI |

`TODO`: document the JSON body sent to `/api/cycle`.

---

## Output files

| Path | Content |
|---|---|
| `captures/cycle_report_<timestamp>.csv` | One row per cycle |
| `captures/framelog_<timestamp>.txt` | Timestamped frame-level log |
| `logs/ct_monitor.log` | Application log |
| Videos directory | Per-cycle recordings, managed by `StorageManager` (`TODO`: state the default path) |

**CSV columns**

| Column | Description |
|---|---|
| `cycle_number` | Sequential cycle number in the session |
| `start_time`, `end_time` | Timestamps with millisecond precision |
| `cycle_time_seconds` | Measured cycle time |
| `anomaly` | `NO` (Normal), `YES_LONG` or `YES_SHORT` |
| `genesis` | `YES` / `NO`. Corresponds to the **Genesis** label (see [Cycle labels](#cycle-labels)) |
| `anomaly_label` | Reason or tag text attached to the cycle, empty if none (for example `Skipped: mask`) |

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Startup stops with "server cannot be reached" | Check the master IP/port and that `/api/summary` responds. The target can be changed in the GUI. |
| "Failed to get config – IP not registered" | This machine's IP is not registered on the master server. |
| Reconnect button appears to do nothing | Check `logs/ct_monitor.log` for the underlying error. |
| No detections | Confirm `ultralytics` is installed, a model is loaded, and the tracked classes are ticked. |
| Wrong camera opens | The camera list is hard-coded in `main.py` (`HD USB CAMERA`, `USB Camera 2`, `OBS Virtual Camera`). Adjust it for your hardware. |
| Changed master is not applied | Once the station has registered with a master, switching to another one requires an app restart. The GUI offers it. |

---

## Development notes

- `main.py` is large (~2,900 lines). Splitting it into modules (server client, inference, GUI) would make testing easier.
- GUI updates from background threads should go through the `_ui` queue rather than calling Tkinter directly.
- Add `.gitignore` entries for `captures/`, `logs/`, `venv/`, and any local model or video files.
- Contributions: open an issue first for larger changes (`TODO`: add contribution guidelines).

---

## License

Licensed under the [Apache License 2.0](LICENSE). Remember to replace the `[yyyy] [name of copyright owner]` placeholder in the license notice with the real year and owner.
