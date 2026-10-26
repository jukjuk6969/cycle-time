# CT Monitor (Cycle Time Monitor)

A machine-vision system that measures **operator cycle time** on manufacturing production lines. A camera at each workstation is analysed with a YOLO object-detection model; the order in which objects appear in user-defined regions of interest (ROIs) is translated into work cycles. Each cycle is timed, labelled, recorded to video, and reported to a central server, where it is stored and shown on a web dashboard.

> **Status:** active development. Items marked `TODO` could not be verified and should be confirmed by the maintainer.

---

## System overview

The repository contains three components that work together:

| Component | Location | Runs on | Instances | Purpose |
|---|---|---|---|---|
| **FD** (Field Device) | repository root (`main.py` and the Python modules next to it) | A mini PC at each workstation, next to the camera | **Many**: one per station, cloned for every station | Capture video, detect objects, measure cycles, record video, report to the BE |
| **BE** (Backend) | `backend/` | A central server | One | Receive and store cycle data, track FD status, serve the API and real-time updates, proxy FD video |
| **FE** (Frontend) | `frontend/` | Served to browsers (supervisors, engineers) | One | Dashboard: live station status, cycle statistics, anomaly videos, station management |

```
   Workstation 1                Workstation 2                 Workstation N
  ┌─────────────┐              ┌─────────────┐               ┌─────────────┐
  │ Camera      │              │ Camera      │               │ Camera      │
  │ FD (mini PC)│              │ FD (mini PC)│      ...      │ FD (mini PC)│
  └──────┬──────┘              └──────┬──────┘               └──────┬──────┘
         │  HTTP: cycle data, register, heartbeat                   │
         │  Socket.IO: parameter updates (takt time, limits)        │
         └───────────────────────┬──────────────────────────────────┘
                                 ▼
                        ┌──────────────────┐
                        │  BE (Node.js)    │──► Database
                        │  REST + Socket.IO│
                        │  video proxy     │──► pulls video from the owning FD on demand
                        └────────┬─────────┘
                                 │ REST + Socket.IO
                                 ▼
                        ┌──────────────────┐
                        │  FE (React)      │  browsers
                        └──────────────────┘
```

Because the FD is deployed once per station, it is designed to be **self-contained and resilient**: it keeps measuring and recording when the server is unreachable, and it stores its own videos locally. The server stores only the routing information (which FD holds which video), never the video file itself.

**A station is identified by the pair `(line, station_id)`.** `station_id` alone is not unique across lines.

## Table of contents

1. [FD: Field Device (mini PC)](#part-a--fd-field-device-mini-pc)
2. [BE: Backend](#part-b--be-backend)
3. [FE: Frontend](#part-c--fe-frontend)
4. [Cycle labels (shared vocabulary)](#cycle-labels)
5. [Deployment notes](#deployment-notes)
6. [In progress / roadmap](#in-progress--roadmap)
7. [License](#license)

---

# Part A – FD: Field Device (mini PC)

## Features

**Detection and cycle logic**
- Real-time object detection with YOLO (Ultralytics), configurable confidence threshold (default `0.25`) and input size (`640`); the tracked classes are chosen in the GUI
- ROI-based cycle logic, defined in an XML file (`logic_config.xml`) produced by the built-in logic editor (see [ROI logic](#roi-logic))
- Cycle labelling: Normal / Anomaly (Long, Short), plus Skipped, Genesis and Late tags (see [Cycle labels](#cycle-labels))
- Cycles shorter than a minimum time are not counted; unfinished cycles are discarded after a timeout

**Operation**
- Tkinter GUI with two modes:
  - **Production mode**: preview plus the station, production and cycle-time panels only
  - **Setting mode**: full toolbar (camera, model, ROI/logic editing). Entering it requires logging in again
- **Save Setup** button stores the setup (including the YOLO model path) in `config/setup_state.json`, restored automatically at the next start. The model itself is still loaded manually
- The ROI logic XML is chosen with a file picker, not a fixed path
- Input from a USB camera or from a video file (useful for tuning)
- Per-cycle video recording with automatic storage management
- Local CSV report per session and a frame-level text log
- Local authentication: the login check (hash of the entered address plus an embedded key against a list of hashes) runs **on the FD itself**, so it works even when the server is down

**Connectivity and resilience**
- Self-registers with the BE on startup (`POST /api/fd/register`, retry with backoff) and sends a heartbeat every 30 s (`POST /api/fd/heartbeat`)
- Startup health check of the master server (WebSocket connect, `ping`/`pong`, `GET /api/summary`)
- Station identity is cached locally, so recording continues while offline; the FD reconnects automatically
- The master IP/port can be changed from the GUI (restart applies it when needed)
- Receives live parameter updates over Socket.IO (takt time, discard timeout, minimum cycle, short-anomaly limit)
- Exposes a small local API for live stream and video access (see [FD local API](#fd-local-api))

**Industrial logic**
- Built-in editors for the ROI logic (`logic_editor.py`) and for PLC-style FBD/ladder logic (`plc_editor.py`, `ld_editor.py`, `plc_*.py`). The PLC-style logic is camera logic that behaves like a PLC; nothing is sent to a real PLC yet

## How it works

1. **Capture**: `camera_manager` reads frames from the selected camera or video file (default 640×480, 24 FPS).
2. **Detect**: frames go into a small bounded queue (size 2; stale frames are dropped, not queued) and are processed by a background YOLO thread.
3. **Evaluate**: detections are passed to `LogicCycleEngine` on every inference, including frames with no detections, so dwell and timeout timers can expire.
4. **Track the cycle**: the engine marks ROIs as done or skipped in order. When the output ROI is reached the cycle is complete; on timeout or abort the cycle is discarded.
5. **Label**: the cycle time is compared with the limits (see [Cycle timing parameters](#cycle-timing-parameters)) and tagged (see [Cycle labels](#cycle-labels)).
6. **Record and report**: the cycle is appended to the CSV report, the video clip is saved locally, and the result (with the video's file name and station) is sent to the BE.

### ROI logic

The logic is defined in `config/logic_config.xml`:

- **`main_roi`**: the **input** ROI (where the item is picked up) and the **output** ROI (where it is placed). They must be declared before any other ROI. The cycle goes Input → sequence → Output → Input.
- **`sequence_roi`**: an ordered list of ROIs, each with an `order`, a **tag** and a **description**:
  - `mandatory`: must be completed. An unfinished mandatory ROI **locks** the ROIs after it. If it is not touched within the timeout, the cycle video is discarded.
  - `expected`: non-blocking. If it is not completed, the cycle is **not** blocked but is labelled with the skipped ROI's description.
- The **timeout** value comes from the server and applies to `main_roi` and mandatory `sequence_roi` only. It is not stored in the XML.
- ROIs are edited in the built-in logic editor (button **Mode Edit ROI** in setting mode, which swaps the right-hand toolbar). The editor supports video sources with pause and frame stepping, and a live sequence tester.

`TODO`: add a minimal `logic_config.xml` example.

#### ROI colours on the live preview

| Role | Colour | Label |
|---|---|---|
| Input | green | `IN` |
| Output | blue | `OUT` |
| Sequence – mandatory | orange | `<n>:M` |
| Sequence – expected | yellow | `<n>:E` |

A completed ROI gets an `OK` suffix and a thicker border; a skipped one gets `SKIP`.

## FD architecture

```
 Camera / video file
        │
        ▼
 camera_manager ──► YOLOInference (thread) ──► LogicCycleEngine
                          │                          │
                          │ push_frame()             ├──► CycleReporter (CSV)
                          ▼                          ├──► VideoRecorder / StorageManager
                    fd_slave_api (MJPEG)             └──► ApiSender ──► BE (HTTP)
                                                                         ▲
                                  TaktTimeSource (Socket.IO push + HTTP) ┘
```

Main classes in `main.py`:

| Class | Responsibility |
|---|---|
| `CTMonitorApp` | Tkinter GUI, application state, wiring of all components |
| `YOLOInference` | Background inference thread, drawing, feeding the cycle engine |
| `ROIViewer` | Read-only overlay of the ROIs on the preview |
| `TaktTimeSource` | Receives takt time, timeout and limits from the server (push and pull) |
| `CycleReporter` | Writes the per-session CSV report |
| `FrameLogger` | Timestamped frame-level log |

## FD files (repository root)

| File | Description |
|---|---|
| `main.py` | FD application: GUI, server connection, YOLO inference |
| `camera_manager.py` | Camera discovery and capture |
| `video_recorder.py` | Per-cycle video recording |
| `storage_manager.py` | Video storage management |
| `logic_cycle_engine.py` | ROI-based cycle logic engine |
| `logic_editor.py` | Editor/validator for `logic_config.xml` |
| `cycle_tracker.py`, `logic_bridge.py` | `TODO`: describe role |
| `plc_engine.py`, `plc_logic.py`, `plc_editor.py`, `plc_export.py`, `ld_editor.py` | PLC / FBD / ladder logic and tooling |
| `hand_detector.py` | Hand detection (`TODO`: confirm backend and status) |
| `api_sender.py` | Sends cycle results (with retry) to the BE |
| `size_sync.py` | Syncs recorded video sizes to the server |
| `fd_auth.py`, `fa_keycore.py` | Local authentication |
| `apply_step3b.py` | One-off patch script (consider moving or removing) |
| `config/` | Runtime configuration |
| `model/` | Detection models (`TODO`: list the model file(s) used) |
| `flag_roi/` | `TODO` |

## Requirements

- Python 3 (`TODO`: tested version)
- A desktop environment with Tkinter
- Python packages (from the imports in `main.py`): `opencv-python`, `numpy`, `Pillow`, `python-socketio` with WebSocket support (`websocket-client`), `ultralytics` (required for detection), `Flask` (for the FD local API)
- FFmpeg on `PATH` (`TODO`: confirm whether mandatory)
- A USB camera or a sample video file
- Network access to the BE (the FD still works offline once its identity is cached)

## Installation

```bash
git clone https://github.com/jukjuk6969/cycle-time.git
cd cycle-time

python -m venv venv
# Windows:       venv\Scripts\activate
# Linux / macOS: source venv/bin/activate

pip install opencv-python numpy Pillow "python-socketio[client]" websocket-client ultralytics flask
```

`TODO`: add a `requirements.txt` (`pip freeze > requirements.txt`).

Because the same FD software is cloned to many mini PCs, keep a per-station checklist: install, copy the YOLO model and `logic_config.xml`, set the master address, set the station identity, log in, press **Save Setup**.

## Running the FD

```bash
python main.py --help
python main.py --endpoint http://<BE_IP>:3000/api/cycle
```

Known option: `--endpoint`. `TODO`: paste the other flags from `--help` (station, line, takt time, operator).

**Master server selection order**
1. `--endpoint` on the command line
2. A target saved from the GUI that takes effect after restart (used once)
3. The last master that connected successfully
4. Built-in default: `192.168.137.2:3000`

**Startup sequence**
1. The GUI opens and loads the saved setup and the ROI logic.
2. The operator logs in (verified locally).
3. In the background the FD verifies the BE (WebSocket, heartbeat, `GET /api/summary`; two attempts) and registers itself.
4. Station configuration (station ID, line, name, takt time, limits) is fetched and cached to `config/station_identity.json`.
5. If the BE is unreachable, the cached identity is used and local recording continues.

## FD configuration

| File | Purpose |
|---|---|
| `config/ct_settings.json` | Cycle timing and recorder settings |
| `config/logic_config.xml` | ROI logic (chosen with the file picker) |
| `config/server_config.json` | Last connected master (`host`, `port`, `last_ok_at`) and an optional one-time `pending` target |
| `config/station_identity.json` | Cached station identity from the server |
| `config/setup_state.json` | Saved setup (**Save Setup**), including the YOLO model path |

### Cycle timing parameters

Defaults used when `config/ct_settings.json` lacks a key:

| Key | Default | Meaning |
|---|---|---|
| `RECORDER_START_DELAY` | `0.5` s | Delay before recording starts |
| `RECORDER_IDLE_TIMEOUT` | `5.0` s | Idle time before the recorder stops |
| `SEQUENCE_COMPLETE_DELAY` | `1.0` s | Delay after the sequence completes |
| `ANOMALY_TIMER` | `20.0` s | A cycle longer than this is flagged Long |
| `DISCARD_TIMEOUT` | `300.0` s | A cycle exceeding this is discarded |
| `MIN_CYCLE_TIME` | `3.0` s | Minimum duration for a valid cycle |
| `SHORT_ANOMALY_LIMIT` | `0.0` s | A cycle shorter than this is flagged Short; `0` disables |

When connected, the server can override takt time, anomaly limit, discard timeout, minimum cycle and short-anomaly limit at runtime.

## FD output files

| Path | Content |
|---|---|
| `captures/cycle_report_<timestamp>.csv` | One row per cycle |
| `captures/framelog_<timestamp>.txt` | Frame-level log |
| `logs/ct_monitor.log` | Application log |
| `videos/<year>/<month>/normal` and `.../anomali` | Per-cycle recordings, organised by year, month and type |

Video files are named `l<line>_st<station>_<date>_<time>_<seconds>s_<LABEL>.mp4`, for example `l6_st10_20260930_154336_18_7s_ANOMALY.mp4`.

**CSV columns**

| Column | Description |
|---|---|
| `cycle_number` | Sequential cycle number in the session |
| `start_time`, `end_time` | Timestamps with millisecond precision |
| `cycle_time_seconds` | Measured cycle time |
| `anomaly` | `NO` (Normal), `YES_LONG` or `YES_SHORT` |
| `genesis` | `YES` / `NO` (see [Cycle labels](#cycle-labels)) |
| `anomaly_label` | Reason or tag text attached to the cycle, empty if none (for example `Skipped: mask`) |

## FD local API

Each FD runs a small Flask service (`fd_slave_api.py`, port `5001`):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/stream` | Live MJPEG stream |
| `GET` | `/videos` | List the videos stored on this FD |
| `GET` | `/videos/<type>/<filename>` | Download or play one video |

The BE proxies these endpoints so that browsers never talk to an FD directly.

## FD troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Startup stops with "server cannot be reached" | Check the master IP/port and that `/api/summary` responds. The target can be changed in the GUI. |
| "Failed to get config – IP not registered" | This FD's IP is not registered on the BE. |
| No detections | Confirm `ultralytics` is installed, a model is loaded, and the tracked classes are ticked. |
| Wrong camera opens | The camera list is hard-coded in `main.py` (`HD USB CAMERA`, `USB Camera 2`, `OBS Virtual Camera`). Adjust it for your hardware. |
| Changed master is not applied | Once registered with a master, switching to another requires an app restart (the GUI offers it). |
| ROI setup lost after restart | Press **Save Setup** in setting mode. |

---

# Part B – BE: Backend

Central server in `backend/`. It is the single point that all FDs report to and that the dashboard reads from.

## Responsibilities

- Receive cycle data from every FD and store it
- Keep track of which FDs are registered and online (register + heartbeat, with an in-memory session registry and a grace period before a station is shown offline)
- Push parameter changes (takt time, limits, timeout) to FDs and live status to the dashboard over Socket.IO
- Proxy live streams and video files from the FD that owns them, so the server stores only routing information
- Compute dashboard statistics: cycle time per station and shift, anomaly counts, and inter-cycle metrics (S2S start-to-start, E2E end-to-end)

## Stack

- Node.js with Express, Socket.IO
- Relational database via `db.js`: SQL Server (`TODO`: confirm; a migration to PostgreSQL that touches only `db.js` and configuration is in progress)

## Setup

`TODO`: confirm the exact commands, typically:

```bash
cd backend
npm install
cp .env.example .env     # TODO: provide .env.example
node server.js
```

Configuration is read from `.env`, for example `CORS_ORIGINS` (allowed dashboard origins; there are no hard-coded IPs in `server.js`). `TODO`: list all variables (port, database connection).

## API

Endpoints referenced by the FD and FE:

| Method | Path | Used by | Purpose |
|---|---|---|---|
| `POST` | `/api/cycle` | FD | Report a finished cycle (includes the video's FD station and file name) |
| `GET` | `/api/summary` | FD | Startup health probe |
| `GET` | `/api/stations` | FD / FE | Station list and parameters |
| `POST` | `/api/fd/register` | FD | FD registers itself (IP, port, hostname) |
| `POST` | `/api/fd/heartbeat` | FD | Keep-alive, every 30 s |
| `GET` | `/api/fd/:id/stream` | FE | Proxy of the FD's live stream |
| `GET` | `/api/fd/:id/videos` | FE | Proxy of the FD's video list |
| `GET` | `/api/fd/:id/video/:type/:filename` | FE | Proxy of one video file |

`TODO`: document the JSON body of `/api/cycle` and the remaining endpoints (history, anomaly list, S2S/E2E, station management).

**Socket.IO events sent to FDs**: `takt_time_update`, `roi_timeout_update`, `min_cycle_update`, `short_anomaly_update`, `station_update`. The server also answers the FD's `ping` with `pong`.

## Data model

| Table | Key | Notes |
|---|---|---|
| `stations` | `(line, station_id)` | Also stores `ip`, `port`, `hostname`, `last_seen`, `is_online` |
| `cycle_times` | `(line, station_id, end_timestamp)` | No surrogate `id`. Holds the cycle result, label columns (`anomaly_label`, `skipped_expected`), video columns (`video_filename`, `video_size_mb`, `video_recorded_at`, …), and computed columns `prod_day` and `shift` |
| S2S / E2E tables | `TODO` | Inter-cycle metrics, each in its own table with in-memory caches |

Notes:
- Shifts are derived in the database from `end_timestamp` in **WIB (UTC+7)**.
- The cycle counter resets **per shift**.
- Video and cycle are one record: video metadata lives in `cycle_times`, and the file stays on the FD.
- The database has a unique filtered index on `video_filename`.

---

# Part C – FE: Frontend

Dashboard in `frontend/`, for supervisors and engineers.

## Stack

React 18, `react-scripts` 5, Chakra UI 3, Recharts.

## Main components

| Component | Purpose |
|---|---|
| `App` | Layout and routing |
| `StationCard` | Overview card per station: live state, progress bar based on elapsed time |
| `StationDetail` | Per-station detail: **CT total** (statistics by time range and shift) and **CT terbaru** (latest 100 cycles for that line, station and shift) |
| `AnomalyList` | **Anomaly Videos** table: filters by station, anomaly type (Long / Short), date range, Normal, Skipped, Genesis, Late; Play / Download / Delete actions |
| `VideoPlayer` | Plays a cycle video through the BE proxy |
| `StationRegister` | Station management (create, edit, delete stations) |

## Setup

`TODO`: confirm the exact commands, typically:

```bash
cd frontend
npm install
npm start          # development server
npm run build      # production build
```

`TODO`: document how the FE finds the BE (environment variable or config file).

---

# Cycle labels

Labels are **not mutually exclusive**. A single cycle can be, for example, *Anomaly* + *Short* + *Skipped: mask*, as shown in the Anomaly Videos table.

| Dashboard label | Meaning | CSV / internal value |
|---|---|---|
| **Normal** | Cycle time is within the configured limits | `anomaly = NO` (internal `OK`) |
| **Anomaly** | Parent category for any out-of-limit cycle | `YES_LONG` or `YES_SHORT` |
| ↳ **Long** | Cycle time above the anomaly limit | `YES_LONG` (internal `LONG`) |
| ↳ **Short** | Cycle time below the short-anomaly limit. The dashboard shows the limit next to the cycle time (for example `11.65 s  < 14 s`) | `YES_SHORT` (internal `SHORT`) |
| **Skipped** | One or more *expected* sequence ROIs were not completed. The ROI description is shown as `Skipped: <description>` (for example `Skipped: mask`) | `anomaly_label`, `skipped_expected` |
| **Genesis** | `TODO`: define. The CSV has a `genesis` column. The old reset-threshold logic was removed on the BE, so all cycle pairs are now recorded | `genesis = YES` |
| **Late** | `TODO`: define | `TODO` |

`TODO`: confirm the exact trigger of each label against `logic_cycle_engine.py` and the BE code.

---

# Deployment notes

- **One BE and one FE, many FDs.** Plan the FD rollout as the repeatable part: same software, different station identity `(line, station_id)`, model and ROI logic per station.
- The BE needs to be reachable from every FD (default port `3000`). FDs expose port `5001` to the BE for stream and video proxying.
- FD videos remain on the FD. If an FD is offline, its videos are not playable from the dashboard.
- Set `CORS_ORIGINS` in the BE `.env` to the dashboard's origin.
- Keep FD clocks correct (NTP). Shift assignment is based on the cycle's end timestamp in WIB.

# In progress / roadmap

`TODO`: confirm each item against the current code before publishing.

- **Saving mode** (per station): a bandwidth-saving mode in which the FD sends only cycle data (no heartbeat, no live stream) and the dashboard shows "Saving Mode" in place of the live state
- Migration of the BE database to PostgreSQL
- Modbus input providers and a Modbus TCP slave mode for the logic engine
- Live monitoring of the FBD/PLC logic from the editor

# License

Licensed under the [Apache License 2.0](LICENSE). Replace the `[yyyy] [name of copyright owner]` placeholder in the license notice with the real year and owner.
