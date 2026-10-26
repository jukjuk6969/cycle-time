# main.py
import ipaddress
import re
from urllib.parse import urlparse
import cv2
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from datetime import datetime
import threading
import time
import os
import queue
import shutil
import subprocess
import xml.etree.ElementTree as ET
import json
import logging
import argparse
import sys
import urllib.request as _urllib
from pathlib import Path
from typing import Optional
import socketio as _sio
import numpy as np
import fd_slave_api
import ctypes
import size_sync
import csv
import fd_auth
from logic_editor import load_logic_xml, validate as validate_logic, LogicEditorPanel

try:

    from ultralytics import YOLO as _YOLO

    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

from video_recorder import VideoRecorder
from camera_manager import CameraManager
from api_sender import ApiSender
from storage_manager import StorageManager, VIDEOS_DIR
from logic_editor import load_logic_xml, validate as validate_logic
from logic_cycle_engine import LogicCycleEngine

# ═══════════════════════════════════════════════════════════════════════════════
#  Logging
# ═══════════════════════════════════════════════════════════════════════════════


def setup_logging(level_str: str):
    level = getattr(logging, level_str.upper(), logging.INFO)
    Path(__file__).parent.joinpath("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(__file__).parent / "logs" / "ct_monitor.log",
                encoding="utf-8",
            ),
        ],
    )


logger = logging.getLogger(__name__)
LOGIC_CONFIG_PATH = Path(__file__).parent / "config" / "logic_config.xml"


# ═══════════════════════════════════════════════════════════════════════════════
#  Settings
# ═══════════════════════════════════════════════════════════════════════════════


def load_settings(path: str = "config/ct_settings.json") -> dict:
    defaults = {
        "RECORDER_START_DELAY": 0.5,
        "RECORDER_IDLE_TIMEOUT": 5.0,
        "SEQUENCE_COMPLETE_DELAY": 1.0,
        "ANOMALY_TIMER": 20.0,     # detik, diset user. Batas cycle time dianggap anomaly
        "DISCARD_TIMEOUT": 300.0,
        "MIN_CYCLE_TIME": 3.0,
        "SHORT_ANOMALY_LIMIT": 0.0,   # detik; 0 = nonaktif
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        for key in defaults:
            if key in loaded:
                defaults[key] = loaded[key]
        logger.info(f"[Settings] Loaded dari {path}")
    except FileNotFoundError:
        logger.warning(f"[Settings] {path} tidak ditemukan, pakai default")
    return defaults


def save_settings(cfg: dict, path: str):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        logger.info(f"[Settings] Disimpan ke {path}")
    except Exception as e:
        logger.error(f"[Settings] Gagal simpan: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Server / TaktTime
# ═══════════════════════════════════════════════════════════════════════════════
IDENTITY_PATH = Path(__file__).parent / "config" / "station_identity.json"
SETUP_PATH = Path(__file__).parent / "config" / "setup_state.json"
SERVER_CFG_PATH = Path(__file__).parent / "config" / "server_config.json"
DEFAULT_MASTER = ("192.168.137.2", 3000)


def parse_master_input(host_text: str, port_text: str):
    """Validasi input user. Return (host, port) atau raise ValueError."""
    h = re.sub(r"^https?://", "", (host_text or "").strip(), flags=re.I).split("/")[0]
    p_txt = (port_text or "").strip()
    if ":" in h:
        h, p_txt = h.rsplit(":", 1)
    h = h.strip()
    if not h:
        raise ValueError("IP / hostname master wajib diisi.")
    try:
        ipaddress.ip_address(h)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", h):
            raise ValueError(f"IP / hostname tidak valid: {h}")
    try:
        port = int(p_txt) if p_txt else DEFAULT_MASTER[1]
    except ValueError:
        raise ValueError("Port harus berupa angka.")
    if not (1 <= port <= 65535):
        raise ValueError("Port harus 1–65535.")
    return h, port


def build_endpoint(host: str, port: int) -> str:
    return f"http://{host}:{port}/api/cycle"


def load_server_cfg() -> dict:
    try:
        return json.loads(SERVER_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_server_cfg(cfg: dict):
    try:
        SERVER_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SERVER_CFG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(tmp, SERVER_CFG_PATH)
    except Exception as e:
        logger.warning(f"[ServerCfg] Gagal simpan: {e}")


def remember_master(host: str, port: int):
    """Dipanggil setelah koneksi BERHASIL: jadikan IP terakhir."""
    cfg = load_server_cfg()
    cfg.pop("pending", None)
    cfg.update({"host": host, "port": port,
                "last_ok_at": datetime.now().isoformat(timespec="seconds")})
    save_server_cfg(cfg)


def resolve_endpoint(cli_endpoint):
    """CLI > pending (setelah restart) > terakhir terkoneksi > default."""
    if cli_endpoint:
        return cli_endpoint, "CLI"
    cfg = load_server_cfg()
    pend = cfg.pop("pending", None)
    if pend:
        save_server_cfg(cfg)                      # pending dipakai sekali
        try:
            h, p = parse_master_input(str(pend.get("host", "")), str(pend.get("port", "")))
            return build_endpoint(h, p), "pending"
        except ValueError:
            pass
    if cfg.get("host"):
        try:
            h, p = parse_master_input(str(cfg["host"]), str(cfg.get("port", "")))
            return build_endpoint(h, p), "terakhir terkoneksi"
        except ValueError:
            pass
    return build_endpoint(*DEFAULT_MASTER), "default"
def save_identity(cfg: dict):
    """Simpan identitas stasiun dari server agar FD bisa merekam saat server offline."""
    keys = ("station_id", "line", "name", "takt_time", "roi_timeout_sec",
            "min_cycle_sec", "ip", "port", "hostname")
    try:
        IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        IDENTITY_PATH.write_text(
            json.dumps({k: cfg.get(k) for k in keys}, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[Identity] Gagal simpan: {e}")


def load_identity() -> dict:
    try:
        return json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def verify_server_connection(
    base_url: str,
    station_id: int,
    max_attempts: int = 2,
    connect_timeout: float = 4.0,
    pong_timeout: float = 3.0,
    http_timeout: float = 3.0,
    retry_delay: float = 2.0,
):
    for attempt in range(1, max_attempts + 1):
        logger.info(
            f"[Startup] Verifikasi server {base_url} (attempt {attempt}/{max_attempts})..."
        )
        sio_client = _sio.Client(reconnection=True, logger=False)
        pong_event = threading.Event()

        @sio_client.on("pong")
        def _on_pong(data):
            pong_event.set()

        try:
            sio_client.connect(
                base_url,
                transports=["websocket"],
                wait_timeout=connect_timeout,
            )
        except Exception as e:
            logger.warning(f"[Startup] Socket.io gagal: {e}")
            if attempt < max_attempts:
                time.sleep(retry_delay)
                continue
            logger.error("[Startup] Server tidak dapat dijangkau.")
            sys.exit(1)

        sio_client.emit("ping", {"sent_at": time.time(), "station_id": station_id})
        if not pong_event.wait(timeout=pong_timeout):
            sio_client.disconnect()
            if attempt < max_attempts:
                time.sleep(retry_delay)
                continue
            logger.error("[Startup] Heartbeat gagal.")
            sys.exit(1)

        logger.info("[Startup] Heartbeat OK ✓")
        try:
            with _urllib.urlopen(f"{base_url}/api/summary", timeout=http_timeout) as r:
                r.read()
            logger.info("[Startup] HTTP /api/summary OK ✓")
        except Exception as e:
            sio_client.disconnect()
            if attempt < max_attempts:
                time.sleep(retry_delay)
                continue
            logger.error(f"[Startup] HTTP gagal: {e}")
            sys.exit(1)

        logger.info("[Startup] Server terverifikasi ✓")
        return sio_client

    sys.exit(1)

class TaktTimeSource:
    def __init__(self, base_url, station_id, line, initial_fallback, existing_sio=None):
        self._base_url = base_url
        self._station_id = station_id
        self._line = line
        self._value = initial_fallback
        self._anomaly = float(initial_fallback)
        self._discard = 300.0
        self._server_connected = True
        self._last_disconnect_logged = False
        self._min = 3.0
        self._short = None

        if existing_sio is not None and existing_sio.connected:
            self._sio = existing_sio
            self._register_takt_listener()
            self._register_connection_watchers()
        else:
            self._sio = _sio.Client(reconnection=True, logger=False)
            self._setup_socket()
    def _is_me(self, d):
        return (str(d.get("station_id")) == str(self._station_id)
                and str(d.get("line")) == str(self._line))

    def _register_takt_listener(self):
        @self._sio.on("takt_time_update")
        def on_takt_update(data):
            if str(data.get("station_id")) == str(self._station_id):
                prev = self._value
                self._value = float(data["takt_time"])
                self._anomaly = self._value      # target_cycle_time = takt
                logger.info(f"[TaktTime] PUSH: {prev}s → {self._value}s")

        @self._sio.on("roi_timeout_update")
        def on_roi_timeout(data):
            if str(data.get("station_id")) == str(self._station_id):
                self._discard = float(data["roi_timeout_sec"])
                logger.info(f"[Timer] PUSH discard={self._discard}s")
        @self._sio.on("min_cycle_update")                      # ← TAMBAH
        def on_min_cycle(data):                                # ← TAMBAH
            if str(data.get("station_id")) == str(self._station_id):
                self._min = float(data["min_cycle_sec"])
                logger.info(f"[Timer] PUSH min_cycle={self._min}s")
        @self._sio.on("short_anomaly_update")
        def on_short(data):
            if str(data.get("station_id")) == str(self._station_id):
                self._short = float(data["short_anomaly_sec"])
                logger.info(f"[Timer] PUSH short_limit={self._short}s")

    def _register_connection_watchers(self):
        @self._sio.event
        def connect():
            self._server_connected = True
            if self._last_disconnect_logged:
                logger.info("[Server] Koneksi PULIH ✓")
                self._last_disconnect_logged = False

        @self._sio.event
        def disconnect():
            self._server_connected = False
            logger.error("[Server] *** SERVER TERPUTUS ***")
            self._last_disconnect_logged = True

    def _setup_socket(self):
        self._register_takt_listener()
        self._register_connection_watchers()
        try:
            self._sio.connect(
                self._base_url,
                transports=["websocket"],
                wait_timeout=5,
            )
        except Exception as e:
            logger.warning(f"[TaktTime] Socket.io gagal: {e}")
            self._server_connected = False

    @property
    def is_connected(self) -> bool:
        try:
            return bool(self._sio.connected)
        except Exception:
            return False

    @property
    def value(self) -> float:
        return self._value
    @property                                  # ← tempel mulai di sini
    def anomaly_timer(self) -> float:
        return self._anomaly

    @property
    def discard_timeout(self) -> float:
        return self._discard                   # ← sampai sini
    @property                                                  # ← TAMBAH
    def min_cycle(self) -> float:                              # ← TAMBAH
        return self._min      
    @property
    def short_limit(self):
        return self._short
    def disconnect(self):
        try:
            self._sio.disconnect()
        except Exception:
            pass

    def refresh(self) -> float:
        try:
            with _urllib.urlopen(f"{self._base_url}/api/stations", timeout=3) as r:
                rows = json.loads(r.read())
            for s in rows:
                if not self._is_me(s):
                    continue
                a = s.get("target_cycle_time") or s.get("takt_time")
                if isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0:
                    self._anomaly = float(a)
                t = s.get("takt_time")
                if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
                    prev, self._value = self._value, float(t)
                    if self._value != prev:
                        logger.info(f"[TaktTime] {prev}s → {self._value}s (backend)")
                d = s.get("roi_timeout_sec")
                if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
                    self._discard = float(d)
                m = s.get("min_cycle_sec")                     # ← TAMBAH
                if isinstance(m, (int, float)) and not isinstance(m, bool) and m >= 0:   # ← TAMBAH
                    self._min = float(m)   
                sh = s.get("short_anomaly_sec")
                if isinstance(sh, (int, float)) and not isinstance(sh, bool) and sh >= 0:
                    self._short = float(sh)
                break
        except Exception as e:
            logger.warning(f"[TaktTime] Gagal fetch: {e}")
        return self._value
# ═══════════════════════════════════════════════════════════════════════════════
#  FrameLogger
# ═══════════════════════════════════════════════════════════════════════════════


class FrameLogger:
    def __init__(self, log_path: str):
        self._lock = threading.Lock()
        self._file = open(log_path, "w", encoding="utf-8", buffering=1)
        self._start = time.time()
        self._file.write(
            f"# CT Monitor Log\n"
            f"# Mulai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{'=' * 70}\n"
        )

    def log(self, tag: str, msg: str, console: bool = True):
        ms = (time.time() - self._start) * 1000
        line = f"{ms:10.1f}ms | {tag:<8} | {msg}"
        with self._lock:
            self._file.write(line + "\n")
        if console:
            logger.info(f"[{tag}] {msg}")

    def close(self):
        with self._lock:
            self._file.write(
                f"{'=' * 70}\n"
                f"# Selesai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            self._file.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  CycleReporter (CSV lokal)
# ═══════════════════════════════════════════════════════════════════════════════


class CycleReporter:
    
    def __init__(self, output_dir: str):
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._path = os.path.join(output_dir, f"cycle_report_{ts}.csv")
        self._lock = threading.Lock()
        import csv

        with open(self._path, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(
                [
                    "cycle_number",
                    "start_time",
                    "end_time",
                    "cycle_time_seconds",
                    "anomaly",
                    "genesis",
                    "anomaly_label",
                ]
            )
    def record(self, cycle_number, start_ts, end_ts, cycle_time, anomaly_type: str,
               is_genesis: bool, anomaly_label: str = ""):
        start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        label = {"OK": "NO", "LONG": "YES_LONG", "SHORT": "YES_SHORT"}.get(anomaly_type, "NO")
        with self._lock:
            with open(self._path, "a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(
                    [cycle_number, start_str, end_str, f"{cycle_time:.3f}", label,
                     "YES" if is_genesis else "NO", anomaly_label])

    def path(self) -> str:
        return self._path

# ═══════════════════════════════════════════════════════════════════════════════
#  YOLOInference
# ═══════════════════════════════════════════════════════════════════════════════
class YOLOInference:
    def __init__(self):
        self._model = None
        self._conf = 0.25
        self._imgsz = 640
        self._input_q = queue.Queue(maxsize=2)
        self._out_lock = threading.Lock()
        self._drawn = None
        self._raw = None
        self._dets = []
        self._running = False
        self._thread = None
        self.enabled = False
        self._tracked_classes: set = set()
        self._engine: "LogicCycleEngine | None" = None
        self.on_cycle_aborted = None   # callback(reason: str)

    # ── engine logika ──
    def set_engine(self, engine):
        self._engine = engine
        self.engine_enabled = True     # False saat ROI sedang diedit
        engine.on_cycle_aborted = self._relay_abort

    def _relay_abort(self, reason: str):
        if self.on_cycle_aborted:
            try:
                self.on_cycle_aborted(reason)
            except Exception as e:
                logger.error(f"[YOLO] on_cycle_aborted error: {e}", exc_info=True)

    @property
    def engine(self):
        return self._engine

    @property
    def _tracker(self):          # alias sementara: kode lama masih memakai yolo._tracker
        return self._engine

    def reset_counts(self):
        if self._engine:
            self._engine.reset_count()

    def get_cycle_count(self) -> int:
        return self._engine.cycle_count if self._engine else 0

    # ── model ──
    def load(self, path: str) -> bool:
        if not YOLO_AVAILABLE:
            return False
        try:
            self._model = _YOLO(path)
            return True
        except Exception as e:
            logger.error(f"[YOLO] load error: {e}")
            return False

    def set_conf(self, v: float):
        self._conf = max(0.01, min(1.0, v))

    def set_tracked_classes(self, classes: set):
        self._tracked_classes = set(classes)

    def get_class_names(self) -> dict:
        return dict(self._model.names) if self._model else {}

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        try:
            self._input_q.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=2)

    def push(self, frame):
        if not self.enabled or self._model is None:
            return
        try:
            self._input_q.put_nowait(frame)
        except queue.Full:
            pass

    def get_result(self):
        with self._out_lock:
            return self._drawn, self._raw, list(self._dets)

    def _loop(self):
        while self._running:
            item = self._input_q.get()
            if item is None:
                break
            raw = item
            try:
                results = self._model.predict(
                    source=raw, conf=self._conf, imgsz=self._imgsz, verbose=False,
                )
                drawn, dets = self._draw_and_track(raw.copy(), results)
            except Exception as e:
                logger.error(f"[YOLO] infer error: {e}", exc_info=True)
                drawn, dets = raw.copy(), []
            with self._out_lock:
                self._drawn = drawn
                self._raw = raw
                self._dets = dets

    def _draw_and_track(self, frame, results):
        palette = [
            (0, 200, 255), (0, 255, 100), (255, 80, 80), (255, 200, 0),
            (180, 0, 255), (0, 180, 180), (255, 140, 0), (100, 255, 0),
        ]
        dets, boxes = [], []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                label = result.names.get(cls_id, str(cls_id))
                if self._tracked_classes and label not in self._tracked_classes:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                color = palette[cls_id % len(palette)]
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                text = f"{label} {conf:.2f}"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
                cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
                cv2.putText(frame, text, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.circle(frame, (cx, cy), 4, (255, 255, 255), -1)
                cv2.circle(frame, (cx, cy), 4, color, 1)
                dets.append({"label": label, "conf": conf,
                             "bbox": (x1, y1, x2, y2), "center": (cx, cy)})
                boxes.append((x1, y1, x2, y2, label, conf))
        # Selalu dipanggil tiap inferensi (termasuk boxes kosong) agar dwell/grace bisa berakhir
        if self._engine and self.engine_enabled:
            self._engine.update(boxes)
        return frame, dets

class ROIViewer:
    """Menggambar ROI dari logic_config (hanya-baca) di atas preview RGB."""
    COLORS = {"input": (60, 200, 80), "output": (30, 130, 240),
              "mandatory": (255, 120, 30), "expected": (240, 200, 40)}

    def __init__(self, engine_getter):
        self._get = engine_getter
        self._sx = self._sy = 1.0

    def set_scale(self, preview_w, preview_h, frame_w, frame_h):
        if preview_w and preview_h and frame_w and frame_h:
            self._sx = preview_w / frame_w
            self._sy = preview_h / frame_h

    def draw_on_frame(self, frame):
        eng = self._get()
        cfg = eng.cfg if eng else None
        if cfg is None:
            return frame
        status = eng.status()
        seq_no = {r.id: i for i, r in enumerate(cfg.sequence, 1)}
        for r in cfg.all_rois():
            x1, y1, x2, y2 = r.rect
            p1 = (int(x1 * self._sx), int(y1 * self._sy))
            p2 = (int(x2 * self._sx), int(y2 * self._sy))
            color = self.COLORS.get(r.role if r.role != "sequence" else r.tag, (255, 255, 255))
            st = status.get(r.id)
            done = bool(st and st[0] == "done")
            if r.role == "input":
                label = "IN"
            elif r.role == "output":
                label = "OUT"
            else:
                label = f"{seq_no[r.id]}:{'M' if r.tag == 'mandatory' else 'E'}"
            if done:
                label += " OK"
            elif st and st[0] == "skip":
                label += " SKIP"
            cv2.rectangle(frame, p1, p2, color, 3 if done else 2)
            ty = p1[1] - 5 if p1[1] > 18 else p1[1] + 16
            cv2.putText(frame, label, (p1[0] + 3, ty), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, label, (p1[0] + 3, ty), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, color, 1, cv2.LINE_AA)
        return frame
# ═══════════════════════════════════════════════════════════════════════════════
#  CTMonitorApp
# ═══════════════════════════════════════════════════════════════════════════════


class CTMonitorApp:
    OUTPUT_FPS = 24

    def __init__(self, root: tk.Tk, args):
        self.root = root
        self.args = args
        self._logic_path: Path = LOGIC_CONFIG_PATH      # path aktif (bisa diganti lewat file picker)
        try:
            self._logic_config = load_logic_xml(self._logic_path)
            logger.info(
                "[Logic] Config dimuat dari %s (%d ROI sequence)",
                self._logic_path,
                len(self._logic_config.sequence),
            )
        except (OSError, ET.ParseError, ValueError, TypeError) as e:
            self._logic_config = None
            logger.error("[Logic] Gagal membaca %s: %s", self._logic_path, e)
        self.root.title(f"CT Monitor — Station {args.station}")
        self.root.geometry("1280x760")
        self.root.minsize(1050, 650)

        # ── Kamera state ───────────────────────────────────────────────────
        self.cap = None
        self.running = False
        self.recording = False
        self.record_start_time = None
        self._ffmpeg_raw = None
        self._ffmpeg_ann = None
        self._video_raw = None
        self._video_ann = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._frame_w = 640
        self._frame_h = 480
        self._capture_thread = None
        self._writer_thread = None
        self._write_queue = queue.Queue(maxsize=120)
        self._measured_fps = None
        self._fps_stable = False
        self._actual_fps = 24.0
        self._paused = False
        self._last_frame_ts = time.time()     # ← TAMBAH BARIS INI

        # ── YOLO ───────────────────────────────────────────────────────────
        self.yolo = YOLOInference()
        self.yolo.set_engine(LogicCycleEngine())
        self._attach_cycle_callbacks()
        self.yolo.on_cycle_aborted = self._on_cycle_aborted
        self.yolo.start()
        # ── ROI state ──────────────────────────────────────────────────────


        # ── Server / cycle state ───────────────────────────────────────────
        self._server_lock = threading.Lock()   # cegah init/reconnect berjalan dobel
        self._slave_started = False
        self._hb_started = False
        self._hold_state_until = 0.0
        self._live_state = "IDLE"
        self._state_evt = threading.Event()
        self._takt: Optional[TaktTimeSource] = None
        self._sender: Optional[ApiSender] = None
        self._ct_recorder: Optional[VideoRecorder] = None  # cycle-time recorder
        self._storage = None
        self._server_connected = True
        self._server_disconnected_since: float | None = None
        self._last_heartbeat = time.time()
        self._cycle_count = 0

        # ── Settings ───────────────────────────────────────────────────────
        self._settings_path = Path(__file__).parent / "config" / "ct_settings.json"
        self._cfg = load_settings(str(self._settings_path))
        self._storage = StorageManager()
        self._storage.start()

        # ── Output dir ─────────────────────────────────────────────────────
        self.output_dir = os.path.join(os.getcwd(), "captures")
        os.makedirs(self.output_dir, exist_ok=True)
        log_name = f"framelog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        self._logger = FrameLogger(os.path.join(self.output_dir, log_name))
        self._reporter: Optional[CycleReporter] = None

        # ── Tk vars ────────────────────────────────────────────────────────
        self._var_station  = tk.StringVar(value="—")   # ← nilai dari server, bukan args
        self._var_line     = tk.StringVar(value="—")   # ← BARU
        self._var_name     = tk.StringVar(value="—")   # ← BARU
        self._var_ip       = tk.StringVar(value="—")   # ← BARU
        self._var_port     = tk.StringVar(value="—")   # ← BARU
        self._var_host     = tk.StringVar(value="—")   # ← BARU
        self._var_operator = tk.StringVar(value=args.operator or "—")
        self._var_state    = tk.StringVar(value="IDLE")
        self._var_cycle    = tk.StringVar(value="0")
        self._var_elapsed  = tk.StringVar(value="—")
        self._var_last_ct  = tk.StringVar(value="—")
        self._var_avg_ct   = tk.StringVar(value="—")
        self._var_best_ct  = tk.StringVar(value="—")
        self._ct_history: list[float] = []
        self._var_takt     = tk.StringVar(value=f"{args.takt_time:.1f}s")
        self._var_anom_lim = tk.StringVar(value="—")
        self._var_disc_lim = tk.StringVar(value="—")
        self._var_min_lim  = tk.StringVar(value="—")
        self._var_ok_count = tk.StringVar(value="0")
        self._var_anom_count = tk.StringVar(value="0")
        self._var_server   = tk.StringVar(value="Menghubungkan...")
        _u = urlparse(args.endpoint)
        self._var_master_ip = tk.StringVar(value=_u.hostname or "")
        self._var_master_port = tk.StringVar(value=str(_u.port or DEFAULT_MASTER[1]))
        self._slave_base = None

        self.camera_name_var = tk.StringVar(value="HD USB CAMERA")
        self._camera_map = {
            "HD USB CAMERA": 0,
            "USB Camera 2": 1,
            "OBS Virtual Camera": 2,
        }
        self.source_var = tk.StringVar(value="Kamera")
        self.video_path_var = tk.StringVar(value="")
        self.width = tk.IntVar(value=640)
        self.height = tk.IntVar(value=480)
        self.fps = tk.IntVar(value=24)
        self.format_var = tk.StringVar(value="mp4 (H.264)")
        self.mirror_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Kamera belum dibuka")
        self.record_status_var = tk.StringVar(value="READY")
        self.timer_var = tk.StringVar(value="00:00:00")
        self.folder_var = tk.StringVar(value=self.output_dir)
        self.photo_count_var = tk.StringVar(value="Foto: 0")
        self._photo_count = 0
        self.model_path_var = tk.StringVar(value="")
        self.conf_var = tk.DoubleVar(value=0.25)
        self.det_count_var = tk.StringVar(value="Deteksi: 0")
        self._class_vars: dict[str, tk.BooleanVar] = {}
        self._saved_disabled_classes: set[str] = set()
        self._tracked_classes: set[str] = set()
        self.cycle_var = tk.StringVar(value="—")
        self._var_logic_file = tk.StringVar(value=self._logic_path.name)
        self._var_logic = tk.StringVar(value="—")
        self._var_short_lim = tk.StringVar(value="—")
        self._roi_editor = None
        self._mode = "setting"      # "setting" | "production"
        self._build_ui()
        self._bind_shortcuts()
        self._restore_setup()
        self._init_logic_engine()        
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

        # Mulai koneksi server di background
        # Antrean UI untuk thread background (aman dipakai sebelum mainloop)
        self._ui_q: queue.Queue = queue.Queue()
        self._drain_ui_q()

        self.authenticated = False
        if not self._require_login():
            self._logger.close()
            sys.exit(0)
        self.authenticated = True

        # Server baru dihubungi setelah login berhasil
        threading.Thread(target=self._init_server, daemon=True).start()

    # ──────────────────────────────────────────────────────────────────────
    #  Server init (background)
    # ──────────────────────────────────────────────────────────────────────
    def _ui(self, fn):
        """Panggil dari thread mana pun: fn dieksekusi di main thread."""
        self._ui_q.put(fn)

    def _drain_ui_q(self):
        try:
            while True:
                fn = self._ui_q.get_nowait()
                try:
                    fn()
                except Exception as e:
                    logger.error(f"[UI] callback error: {e}", exc_info=True)
        except queue.Empty:
            pass
        self.root.after(50, self._drain_ui_q)
    def _reconnect_server(self):
        if self._server_lock.locked():
            return
        self._reconnect_btn.config(state="disabled")
        self.root_status.config(text="Reconnect ke server...")
        logger.info("[Server] Reconnect manual diminta")
        threading.Thread(target=self._init_server, daemon=True).start()
    def _apply_master_target(self):
        if self._server_lock.locked():
            self.root_status.config(text="Koneksi sedang berjalan, tunggu sebentar...")
            return
        try:
            host, port = parse_master_input(self._var_master_ip.get(),
                                            self._var_master_port.get())
        except ValueError as e:
            messagebox.showerror("Server Master", str(e))
            return
        self._var_master_ip.set(host)
        self._var_master_port.set(str(port))
        new_base = f"http://{host}:{port}"

        # fd_slave_api sudah berjalan ke server lama -> perlu restart
        if self._slave_started and self._slave_base and new_base != self._slave_base:
            if not messagebox.askyesno(
                "Server Master",
                f"Target diganti ke {host}:{port}.\n"
                "Aplikasi perlu dimulai ulang agar berlaku. Restart sekarang?"):
                return
            cfg = load_server_cfg()
            cfg["pending"] = {"host": host, "port": port}
            save_server_cfg(cfg)
            self._restart_app()
            return

        self.args.endpoint = build_endpoint(host, port)
        logger.info(f"[Server] Target master diubah ke {new_base}")
        self._reconnect_server()

    def _restart_app(self):
        self.close_app()
        try:
            if self.root.winfo_exists():      # user membatalkan penutupan
                return
        except tk.TclError:
            pass
        argv, skip = [], False
        for a in sys.argv[1:]:                # buang --endpoint agar pending berlaku
            if skip:
                skip = False
                continue
            if a == "--endpoint":
                skip = True
                continue
            if a.startswith("--endpoint="):
                continue
            argv.append(a)
        subprocess.Popen([sys.executable, sys.argv[0], *argv])
        os._exit(0)
    def _sync_sizes_now(self):
        base_url = self.args.endpoint.replace("/api/cycle", "")
        self.root_status.config(text="Sync ukuran video...")

        def work():
            r = size_sync.sync_sizes(base_url, force=True)
            self.root.after(0, lambda: self.root_status.config(
                text=f"Sync ukuran: {r['ok']} OK | {r['not_found']} belum ada di server | {r['failed']} gagal"))

        threading.Thread(target=work, daemon=True).start()
    def _init_server(self):
        # Hanya satu proses init/reconnect pada satu waktu
        if not self._server_lock.acquire(blocking=False):
            return
        try:
            self._do_init_server()
        finally:
            self._server_lock.release()
            self._ui( lambda: self._reconnect_btn.config(state="normal"))

    def _register_station_update(self):
        @self._takt._sio.on("station_update")
        def _on_station_update(data):
            if (str(data.get("station_id")) != str(self.args.station)
                    or str(data.get("line")) != str(self.args.line)):
                return
            new_name = data.get("name")
            if not new_name:
                return
            fd_slave_api._station_config["name"] = new_name
            self._ui( lambda n=new_name: self._var_name.set(n))
            self._ui( lambda n=new_name: self.root.title(
                f"CT Monitor — {n} ({self.args.line})"))
            logger.info(f"[FD] Nama station diperbarui: {new_name}")

    def _apply_cached_identity(self):
        cfg = load_identity()
        if not cfg.get("station_id") or not cfg.get("line"):
            ln, st = fd_auth.identity()
            if not (ln and st):
                logger.error("[FD] Server offline dan tidak ada identitas — rekaman lokal tidak bisa jalan")
                return
            cfg = {"station_id": st, "line": ln}
        self.args.station = self.args.station_no = cfg["station_id"]
        self.args.line = cfg["line"]
        if cfg.get("takt_time"):
            self.args.takt_time = cfg["takt_time"]
        self._ui( lambda: self._var_station.set(str(cfg["station_id"])))
        self._ui( lambda: self._var_line.set(str(cfg["line"])))
        self._ui( lambda: self._var_name.set(cfg.get("name") or "—"))
        self._ui( lambda: self.root_status.config(
            text="Server offline — pakai identitas tersimpan, rekaman lokal tetap jalan"))
        logger.warning(f"[FD] Server offline — identitas dari cache: "
                       f"ST{cfg['station_id']} L{cfg['line']}")

    def _do_init_server(self):
        base_url = self.args.endpoint.replace("/api/cycle", "")
        verified_sio = None
        try:
            self._ui(lambda: self._var_server.set("Menghubungkan..."))
            self._ui(lambda: self._server_indicator.config(fg="#888"))

            # Bersihkan koneksi lama (jika ada)
            old = self._takt
            if old is not None:
                self._takt = None          # supaya UI loop tidak menimpa status
                old.disconnect()

            verified_sio = verify_server_connection(base_url=base_url, station_id=0)

            if not self._slave_started:
                fd_slave_api.start(server_url=base_url, port=5001)
                self._slave_started = True
                self._slave_base = base_url
            else:
                fd_slave_api._ensure_registration()      # Reconnect setelah 403 / registrasi mati

            cfg = {}
            for _ in range(30):
                if fd_slave_api._station_config:          # hanya data live dari server
                    cfg = fd_slave_api._station_config.copy()
                    break
                time.sleep(1)

            if not cfg:
                logger.error("[FD] Gagal dapat config — IP belum terdaftar di server")
                try:
                    verified_sio.disconnect()
                except Exception:
                    pass
                self._ui(lambda: self._var_server.set("IP TIDAK TERDAFTAR"))
                self._ui(lambda: self._server_indicator.config(fg="#e53935"))
                return

            self.args.station    = cfg["station_id"]
            self.args.station_no = cfg["station_id"]
            self.args.line       = cfg["line"]
            self.args.takt_time  = cfg["takt_time"]
            save_identity(cfg)

            self._ui(lambda: self._var_station.set(str(cfg["station_id"])))
            self._ui(lambda: self._var_line.set(cfg.get("line", "—")))
            self._ui(lambda: self._var_name.set(cfg.get("name", "—")))
            self._ui(lambda: self._var_takt.set(f"{cfg['takt_time']:.1f}s"))
            self._ui(lambda: self._var_ip.set(cfg.get("ip", "—")))
            self._ui(lambda: self._var_port.set(str(cfg.get("port", "—"))))
            self._ui(lambda: self._var_host.set(cfg.get("hostname", "—")))
            self._ui(lambda: self.root.title(
                f"CT Monitor — {cfg.get('name','?')} ({cfg.get('line','?')})"
            ))
            logger.info(
                f"[FD] Identity: ST{cfg['station_id']} | {cfg['line']} | "
                f"{cfg['name']} | takt={cfg['takt_time']}s | "
                f"ip={cfg.get('ip')}:{cfg.get('port')} | host={cfg.get('hostname')}"
            )

            takt = TaktTimeSource(
                base_url=base_url,
                station_id=self.args.station,
                line=self.args.line,
                initial_fallback=self.args.takt_time,
                existing_sio=verified_sio,
            )
            self._takt = takt
            self._register_station_update()
            takt.refresh()
            if cfg.get("min_cycle_sec") is not None:
                takt._min = float(cfg["min_cycle_sec"])

            if self._sender is None:        # satu instance saja, agar thread flush buffer tidak dobel
                self._sender = ApiSender(base_url=f"{base_url}/api")

            if not self._hb_started:
                self._hb_started = True
                threading.Thread(target=self._state_heartbeat_loop, daemon=True,
                                 name="state-heartbeat").start()

            if self._storage is None:
                self._storage = StorageManager()
                self._storage.start()
            size_sync.start_background(base_url)   # cek tiap 5 menit
            self._server_connected = True
            _u = urlparse(base_url)
            remember_master(_u.hostname, _u.port or DEFAULT_MASTER[1])
            self._server_disconnected_since = None
            self._ui(lambda: self._var_server.set("Terhubung ✓"))
            self._ui(lambda: self._server_indicator.config(fg="#4caf50"))
            self._ui(lambda: self.root_status.config(text="Server terhubung ✓"))
            logger.info("[Server] Inisialisasi selesai ✓")

        except SystemExit:
            self._apply_cached_identity()
            self._ui(lambda: self._var_server.set("GAGAL KONEK"))
            self._ui(lambda: self._server_indicator.config(fg="#e53935"))
            self._ui(lambda: self.root_status.config(
                text="Gagal konek server — klik Reconnect untuk coba lagi"))
            logger.error("[Server] Koneksi gagal — fitur server dinonaktifkan")

    def _auth_identity(self):
        """(line, station_id) milik FD ini: live dari server bila sudah ada, kalau tidak dari cache."""
        st, ln = self.args.station_no, self.args.line
        if st and ln:
            return ln, st
        cfg = load_identity()
        return cfg.get("line"), cfg.get("station_id")
    def _lag_watchdog(self):
        last = time.perf_counter()
        while True:
            time.sleep(1.0)
            now = time.perf_counter()
            lag = now - last - 1.0
            if lag > 0.5:
                logger.warning(f"[Lag] thread tertunda {lag:.2f}s "
                            f"(HB={fd_slave_api.is_hb_connected()})")
            last = now
    def _require_login(self, startup: bool = True) -> bool:
        """Dialog login modal. Evaluasi kode LOKAL (fd_auth + fa_keycore), tanpa server.
        startup=True : jendela utama disembunyikan selama login (peluncuran aplikasi).
        startup=False: relogin untuk masuk Setting Mode, jendela utama tetap tampil."""
        if startup:
            self.root.withdraw()
        result = {"ok": False}
        n_digits = fd_auth.CODE_DIGITS

        win = tk.Toplevel(self.root)
        win.title("CT Monitor — Login" if startup else "CT Monitor — Masuk Setting Mode")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        if not startup:
            win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Kode akses diperlukan",
                  font=("Arial", 12, "bold")).pack(anchor="w")
        ident_lbl = ttk.Label(frm, text="")
        ident_lbl.pack(anchor="w", pady=(4, 0))
        ttk.Label(frm, text=f"Masukkan kode {n_digits} karakter dari FA Key Generator:").pack(
            anchor="w", pady=(8, 4))
        code_var = tk.StringVar()
        entry = ttk.Entry(frm, textvariable=code_var, width=10, justify="center",
                          font=("Consolas", 20, "bold"))
        entry.pack()
        msg = ttk.Label(frm, text="", foreground="#c62828", wraplength=300)
        msg.pack(anchor="w", pady=(6, 0))
        info = ttk.Label(frm, text="", foreground="#666666")
        info.pack(anchor="w", pady=(6, 0))
        btn = ttk.Button(frm, text="Login")
        btn.pack(fill="x", pady=(10, 0), ipady=3)

        def _digits() -> str:
            return "".join(ch for ch in code_var.get() if ch.isalnum()).upper()

        def _submit(event=None):
            digits = _digits()
            if not digits or fd_auth.lock_left() > 0:
                return
            if len(digits) != n_digits:
                msg.config(text=f"Kode harus {n_digits} karakter.")
                return
            status = fd_auth.attempt(digits)
            code_var.set("")
            if status == "ok":
                result["ok"] = True
                logger.info("[Auth] Login berhasil")
                win.destroy()
            elif status == "no_key":
                msg.config(text="File kunci stasiun (fa_station.fakey) tidak ditemukan "
                                "atau tidak valid. Hubungi admin.")
                logger.error("[Auth] .fakey tidak ada/tidak valid")
            elif status == "invalid":
                logger.warning("[Auth] Kode salah")
                if fd_auth.lock_left() == 0:
                    msg.config(text=f"Kode salah. Sisa percobaan: {fd_auth.fails_left()}.")

        def _on_change(*_):
            cur = code_var.get()
            if cur != cur.upper():
                code_var.set(cur.upper())      # tampil huruf besar; trace jalan lagi
                return
            if len(_digits()) == n_digits:
                win.after(50, _submit)

        def _tick():
            if not win.winfo_exists():
                return
            line, station = self._auth_identity()
            ident_lbl.config(
                text=f"Stasiun: Line {line} / Station {station}"
                if (line and station) else "Stasiun: belum diketahui")
            info.config(text=f"Server: {self._var_server.get()}   |   "
                             f"Jam FD: {time.strftime('%H:%M:%S', time.gmtime())} UTC")
            left = fd_auth.lock_left()
            if left > 0:
                msg.config(text=f"Terlalu banyak percobaan. Coba lagi dalam {left} detik.")
                btn.config(state="disabled")
                entry.config(state="disabled")
            elif str(btn.cget("state")) == "disabled":
                btn.config(state="normal")
                entry.config(state="normal")
                msg.config(text="")
                entry.focus_set()
            win.after(500, _tick)

        btn.config(command=_submit)
        entry.bind("<Return>", _submit)
        code_var.trace_add("write", _on_change)

        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2
        y = (win.winfo_screenheight() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        win.lift()
        win.focus_force()
        entry.focus_set()
        win.grab_set()
        _tick()
        win.wait_window()

        if result["ok"] and startup:
            self.root.deiconify()
        return result["ok"]
    # ──────────────────────────────────────────────────────────────────────
    #  Setup persistence (auto save / restore)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _var_int(var, default):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    def _save_setup(self):
        try:
            old = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
            if not isinstance(old, dict):
                old = {}
        except Exception:
            old = {}

        # Kalau model belum di-load, pertahankan daftar class yang sebelumnya dimatikan
        if self._class_vars:
            disabled = sorted(l for l, v in self._class_vars.items() if not v.get())
        else:
            disabled = sorted(self._saved_disabled_classes)
        self._saved_disabled_classes = set(disabled)

        state = {
            **old,
            "version": 3,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "model_path": self.model_path_var.get().strip(),
            "logic_path": str(self._logic_path),
            "source": self.source_var.get(),
            "camera_name": self.camera_name_var.get(),
            "video_path": self.video_path_var.get().strip(),
            "width": self._var_int(self.width, 640),
            "height": self._var_int(self.height, 480),
            "fps": self._var_int(self.fps, 24),
            "format": self.format_var.get(),
            "mirror": bool(self.mirror_var.get()),
            "conf": round(float(self.conf_var.get()), 2),
            "disabled_classes": disabled,
            "output_dir": self.output_dir,
        }
        try:
            SETUP_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = SETUP_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(tmp, SETUP_PATH)
            self.root_status.config(text="Setup disimpan (model, logic, kamera, YOLO, folder)")
            logger.info(f"[Setup] Disimpan ke {SETUP_PATH}")
        except Exception as e:
            logger.error(f"[Setup] Gagal simpan: {e}")
            messagebox.showerror("Save Setup", str(e))

    def _restore_setup(self):
        try:
            d = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                return
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f"[Setup] File setup rusak, diabaikan: {e}")
            return

        # Model (load tetap manual: butuh identitas stasiun dari server)
        mp = d.get("model_path", "")
        if mp:
            self.model_path_var.set(mp)
            if not os.path.exists(mp):
                logger.warning(f"[Setup] Model tidak ditemukan: {mp}")

        # File XML logic (dimuat sebelum _init_logic_engine dipanggil)
        lp = d.get("logic_path")
        if lp and Path(lp) != self._logic_path:
            p = Path(lp)
            if p.exists():
                try:
                    self._logic_config = load_logic_xml(p)
                    self._logic_path = p
                    self._var_logic_file.set(p.name)
                except (OSError, ET.ParseError, ValueError, TypeError) as e:
                    logger.warning(f"[Setup] Logic XML {p} gagal dimuat: {e}")
            else:
                logger.warning(f"[Setup] Logic XML tidak ditemukan: {p}")

        # Kamera / video
        if d.get("source") in ("Kamera", "Video File"):
            self.source_var.set(d["source"])
        if d.get("camera_name") in self._camera_map:
            self.camera_name_var.set(d["camera_name"])
        if d.get("video_path"):
            self.video_path_var.set(d["video_path"])
        for key, var in (("width", self.width), ("height", self.height), ("fps", self.fps)):
            v = d.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                var.set(v)
        if d.get("format") in ("mp4 (H.264)", "mp4 (H.265)"):
            self.format_var.set(d["format"])
        if isinstance(d.get("mirror"), bool):
            self.mirror_var.set(d["mirror"])

        # YOLO
        c = d.get("conf")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            self.conf_var.set(max(0.01, min(1.0, float(c))))
            self._on_conf_change()
        dc = d.get("disabled_classes")
        if isinstance(dc, list):
            self._saved_disabled_classes = {str(x) for x in dc}

        # Folder output
        od = d.get("output_dir")
        if od and os.path.isdir(od) and os.access(od, os.W_OK):
            self.output_dir = od
            self.folder_var.set(od)
        logger.info("[Setup] Dipulihkan dari setup_state.json")

    def _build_panel_logic(self, parent):
        box = ttk.LabelFrame(parent, text="Logic ROI", padding=8)
        box.pack(fill="x", pady=(0, 6))
        ttk.Label(box, text="File:", foreground="#777777").pack(anchor="w")
        ttk.Label(box, textvariable=self._var_logic_file, wraplength=260,
                  foreground="#777777").pack(anchor="w")
        ttk.Button(box, text="📂  Pilih file XML...",
                   command=self._browse_logic).pack(fill="x", pady=(4, 6))
        ttk.Label(box, textvariable=self._var_logic, wraplength=260).pack(
            anchor="w", pady=(2, 6))
        ttk.Button(box, text="✏  Edit ROI (Logic Editor)",
                   command=self._enter_roi_editor).pack(fill="x", pady=(0, 6), ipady=3)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Siklus:").pack(side="left")
        ttk.Label(row, textvariable=self.cycle_var,
                  font=("Consolas", 14, "bold")).pack(side="left", padx=(8, 0))
        ttk.Button(box, text="🔄  Muat ulang XML", command=self._reload_logic).pack(
            fill="x", pady=(0, 4))
        ttk.Button(box, text="Reset Count", command=self._reset_count).pack(
            fill="x", pady=(0, 4))
        ttk.Button(box, text="💾  Save Setup", command=self._save_setup).pack(fill="x")
    def _browse_logic(self):
        if self._roi_editor is not None:
            messagebox.showinfo("Logic", "Tutup Logic Editor dulu sebelum mengganti file XML.")
            return
        path = filedialog.askopenfilename(
            title="Pilih file XML ROI (logic config)",
            initialdir=str(self._logic_path.parent),
            filetypes=[("XML", "*.xml"), ("All", "*.*")],
        )
        if not path:
            return
        new_path = Path(path)
        try:
            cfg = load_logic_xml(new_path)
        except (OSError, ET.ParseError, ValueError, TypeError) as e:
            messagebox.showerror("Logic", f"Gagal membaca {new_path.name}:\n{e}")
            return
        errors, _warnings = validate_logic(cfg)
        if errors:
            messagebox.showerror("Logic", f"{new_path.name} tidak valid:\n" + "\n".join(errors))
            return

        # Siklus yang sedang berjalan tidak valid lagi untuk config baru
        self.yolo.engine.abort_cycle()
        if self._ct_recorder:
            self._ct_recorder.discard()
        self._var_elapsed.set("—")
        self._set_cycle_state("IDLE")

        self._logic_path = new_path
        self._var_logic_file.set(new_path.name)
        self._logic_config = cfg
        self._init_logic_engine()
        if self.running:
            self.yolo.engine.adapt(self._frame_w, self._frame_h, self.mirror_var.get())
        logger.info(f"[Logic] File XML diganti ke {new_path}")
    def _reload_logic(self):
        try:
            cfg = load_logic_xml(self._logic_path)
        except (OSError, ET.ParseError, ValueError, TypeError) as e:
            messagebox.showerror("Logic", f"Gagal membaca {self._logic_path.name}:\n{e}")
            return
        self._logic_config = cfg
        self._init_logic_engine()
        if self.running:
            self.yolo.engine.adapt(self._frame_w, self._frame_h, self.mirror_var.get())
    def _enter_roi_editor(self):
        if self._roi_editor is not None:
            return
        if not self.running:
            messagebox.showinfo("Edit ROI", "Buka kamera / video dulu, lalu klik Edit ROI.")
            return
        # Hentikan logika produksi selama ROI diedit
        self.yolo.engine_enabled = False
        self.yolo.engine.abort_cycle()
        if self._ct_recorder:
            self._ct_recorder.discard()
        self._var_elapsed.set("—")
        self._set_cycle_state("IDLE")

        self._ctrl_canvas.pack_forget()
        self._ctrl_scrollbar.pack_forget()
        panel = LogicEditorPanel(
            self._ctrl_outer, self.preview,
            get_frame_size=lambda: (self._frame_w, self._frame_h) if self.running else (0, 0),
            get_class_names=self.yolo.get_class_names,
            get_mirror=lambda: self.mirror_var.get(),
            get_default_conf=lambda: self.conf_var.get(),
            xml_path=self._logic_path,
            on_saved=lambda _p: self._reload_logic(),
            on_close=lambda: self.root.after(0, self._exit_roi_editor),
            set_status=lambda t: self.root_status.config(text=t),
            get_live=self.yolo.engine.monitor_snapshot,
        )
        panel.pack(fill="both", expand=True)
        panel.attach()
        self._roi_editor = panel
        self.root_status.config(
            text="Edit ROI — tekan P untuk membekukan preview sebelum menggambar")

    def _exit_roi_editor(self):
        panel = self._roi_editor
        if panel is None:
            return
        self._roi_editor = None
        try:
            panel.detach()
        finally:
            panel.destroy()
        self.preview.config(cursor="")
        self._ctrl_scrollbar.pack(side="right", fill="y")
        self._ctrl_canvas.pack(side="left", fill="both", expand=True)
        self._reload_logic()
        self.yolo.engine_enabled = True
        self.root_status.config(text="Edit ROI ditutup — logika produksi aktif lagi")
    def _reset_count(self):
        self.yolo.reset_counts()
        if self._ct_recorder:
            self._ct_recorder.discard()
        self.cycle_var.set("0")
        self._var_cycle.set("0")

    def _init_logic_engine(self):
        cfg = self._logic_config
        if cfg is None:
            self._var_logic.set("TIDAK ADA / RUSAK")
            self.root_status.config(
                text=f"{self._logic_path.name} tidak ada/rusak — buat dulu dengan logic_editor")
            return
        errors, warnings = validate_logic(cfg)
        for w in warnings:
            logger.warning(f"[Logic] {w}")
        if errors:
            for e in errors:
                logger.error(f"[Logic] {e}")
            self._var_logic.set("TIDAK VALID — lihat log")
            self.root_status.config(text="logic_config.xml tidak valid — lihat log")
            return
        self.yolo.engine.set_config(cfg)
        lowest = max(0.01, int(min(r.conf for r in cfg.all_rois()) * 100) / 100)
        if lowest < self.conf_var.get():
            self.conf_var.set(lowest)
            self._on_conf_change()
        n = len(cfg.sequence)
        self._var_logic.set(f"{n} sequence ROI | dimuat {datetime.now():%H:%M:%S}")
        logger.info(f"[Logic] Engine aktif: {n} sequence ROI")
        self.root_status.config(text=f"Logic dimuat: {n} sequence ROI")
    # ──────────────────────────────────────────────────────────────────────
    #  Cycle callbacks
    # ──────────────────────────────────────────────────────────────────────

    def _anomaly_limit(self) -> float:
        if self._takt:
            return self._takt.anomaly_timer
        return float(self._cfg.get("ANOMALY_TIMER", 20.0))

    def _discard_limit(self) -> float:
        if self._takt:
            return self._takt.discard_timeout
        return float(self._cfg.get("DISCARD_TIMEOUT", 300.0))
    def _min_cycle_limit(self) -> float:
        if self._takt:
            return self._takt.min_cycle
        return float(self._cfg.get("MIN_CYCLE_TIME", 3.0))
    def _short_limit(self) -> float:
        if self._takt and self._takt.short_limit is not None:
            return self._takt.short_limit
        return float(self._cfg.get("SHORT_ANOMALY_LIMIT", 0.0))

    def _classify_cycle(self, ct: float) -> str:
        """Return 'SHORT', 'LONG', atau 'OK'. (< min CT sudah dibuang di tracker)"""
        short = self._short_limit()
        if short > 0 and ct < short:
            return "SHORT"
        if ct > self._anomaly_limit():
            return "LONG"
        return "OK"
    @staticmethod
    def _anomaly_label(anom_type: str, skipped: list) -> str:
        """OK  |  Anomaly+long  |  Anomaly+expected+LCD  |  Anomaly+long+expected+LCD"""
        parts = []
        if anom_type == "LONG":
            parts.append("long")
        elif anom_type == "SHORT":
            parts.append("short")
        if skipped:
            parts += ["expected", ", ".join(d for _, d in skipped)]
        return "Anomaly+" + "+".join(parts) if parts else "OK"
    def _on_cycle_rejected(self, elapsed: float, min_ct: float):
        """Dipanggil dari thread YOLO — semua yang menyentuh Tk lewat root.after."""
        logger.warning(f"[Filter] Siklus {elapsed:.2f}s < min {min_ct:.1f}s — dibuang")
        self._logger.log("DISCARD", f"cycle terlalu cepat {elapsed:.2f}s < {min_ct:.1f}s")
        if self._ct_recorder:
            self._ct_recorder.discard()
        self.root.after(
            0,
            lambda: self._enter_discard(
                f"siklus {elapsed:.1f}s < minimum {min_ct:.1f}s", hold=2.0
            ),
        )
    def _on_cycle_start(self, ts=None):
        if self._ct_recorder and not self.args.no_record:
            self._ct_recorder.start_cycle(ts or time.time())
        logger.info("[Cycle] START")

    def _on_cycle_complete(self, cycle_num, start_ts, end_ts, cycle_time,
                           is_genesis=False, skipped=None):
        skipped = skipped or []
        if skipped:
            logger.warning("[Cycle] #%d expected terlewat: %s", cycle_num,
                           ", ".join(f"{i} ({d})" for i, d in skipped))
        anom_type = self._classify_cycle(cycle_time)
        label = "GENESIS" if is_genesis else ""
        video_info = None
        if self._ct_recorder and not self.args.no_record:
            base_url = self.args.endpoint.replace("/api/cycle", "")
            video_info = self._ct_recorder.stop_cycle(
                cycle_time_sec=cycle_time,
                takt_time=self._takt.value if self._takt else None,
                anomaly_type=anom_type,
                update_url=f"{base_url}/api/video/update-size",
                end_ts=end_ts,
                label=label,
                skipped=skipped,
                anomaly_label=self._anomaly_label(anom_type, skipped),
            )
        threading.Thread(
            target=self._handle_cycle_complete,
            args=(cycle_num, start_ts, end_ts, cycle_time, anom_type,
                  video_info, is_genesis, skipped),
            daemon=True,
        ).start()

    def _handle_cycle_complete(self, cycle_num, start_ts, end_ts, cycle_time,
                            anom_type, video_info, is_genesis=False, skipped=None):
        skipped = skipped or []
        label = self._anomaly_label(anom_type, skipped)
        logger.info(f"[Cycle] Complete #{cycle_num} | ct={cycle_time:.2f}s | {label}")
        try:
            is_anom = anom_type != "OK" or bool(skipped)
            try:
                self._storage.append_summary({
                    "line": self.args.line,
                    "station_id": self.args.station,
                    "operator_id": self.args.operator or "",
                    "cycle_time_sec": round(cycle_time, 3),
                    "start_timestamp": start_ts,
                    "end_timestamp": end_ts,
                    "is_complete": 1,
                    "is_anomaly": int(is_anom),
                    "cycle_count": cycle_num,
                    "video_filename": (video_info or {}).get("filename", ""),
                    "anomaly_type": anom_type,
                    "is_genesis": int(is_genesis),
                    "anomaly_label": label,
                    "skipped_expected": "; ".join(d for _, d in skipped),
                })
            except Exception as e:
                logger.error(f"[Summary] gagal tulis CSV: {e}")
            if self._takt:
                self._takt.refresh()

            if self._reporter:
                self._reporter.record(
                    cycle_num, start_ts, end_ts, cycle_time,
                    anom_type, is_genesis, label,
                )

            # Kirim ke server (video_info sudah dibuat di _on_cycle_complete)
            if self._sender:
                from cycle_tracker import CycleResult

                result = CycleResult(
                    cycle_id=cycle_num,
                    station_id=self.args.station,
                    start_timestamp=start_ts,
                    end_timestamp=end_ts,
                    cycle_time_sec=cycle_time,
                    takt_time_s=self._takt.value if self._takt else 0,
                    is_complete=True,
                    anomaly=is_anom,
                    completion_count=cycle_num,
                    operator_id=self.args.operator,
                    anomaly_type=anom_type,
                    label="GENESIS" if is_genesis else "",
                    is_genesis=is_genesis,
                )
                if video_info:
                    result.anomaly = video_info.get("is_anomaly", is_anom)
                self._sender.send(
                    result,
                    video_info=video_info,
                    extra={
                        "is_anomaly": is_anom,
                        "anomaly_label": label,
                        "skipped_expected": [
                            {"id": i, "description": d} for i, d in skipped
                        ],
                    },
                )

            self._cycle_count = cycle_num
            self.root.after(
                0, lambda: self._on_cycle_ui_update(
                    cycle_num, cycle_time, anom_type, is_genesis, skipped, label)
            )
            self.root.after(0, self._show_complete)
            self.root.after(
                0, lambda: self.cycle_var.set(f"{cycle_num}  ({cycle_time:.1f}s)")
            )

        except Exception as e:
            logger.error(f"[Cycle] _handle_cycle_complete ERROR: {e}", exc_info=True)

    def _attach_cycle_callbacks(self):
        t = self.yolo._tracker
        if t:
            t.on_cycle_start = self._on_cycle_start
            t.on_cycle_complete = self._on_cycle_complete
            t.on_cycle_rejected = self._on_cycle_rejected
            t.min_cycle_getter = self._min_cycle_limit
    def _check_timers(self):
        tracker = self.yolo._tracker
        if not tracker or not tracker.is_cycle_active():
            self._elapsed_label.config(foreground="")
            return

        anomaly_lim = self._anomaly_limit()
        discard_lim = self._discard_limit()
        grace = float(self._cfg.get("TRANSIT_GRACE", 3.0))

        # Timer discard: last ROI time + setpoint
        idle = tracker.seconds_since_roi()
        if idle >= discard_lim:
            self._on_discard_timeout(idle)
            return

        # Timer anomaly: elapsed sejak ROI 1, merah jika lewat batas
        elapsed = tracker.cycle_elapsed()

        # Pastikan nilai timer UI selalu diperbarui selama cycle aktif
        if elapsed >= 0:
            self._var_elapsed.set(f"{elapsed:.1f}s")
        self._elapsed_label.config(
            foreground="#c62828" if elapsed > anomaly_lim else ""
        )
        # State: tahan COMPLETE beberapa detik
        if time.time() < self._hold_state_until:
            return

        want = "TRANSIT" if idle >= grace else "RUNNING"
        if self._var_state.get() != want:
            self._set_cycle_state(want)

        if want == "TRANSIT":
            self.root_status.config(
                text=f"TRANSIT {idle:.0f}s — timeout dalam {discard_lim - idle:.0f}s"
            )
    def _enter_discard(self, reason: str, hold: float = 0.0):
        """Set state DISCARD (dikirim ke server), lalu kembali IDLE setelah 3 detik."""
        self._hold_state_until = time.time() + hold if hold > 0 else 0.0
        self._set_cycle_state("DISCARD")
        self._var_elapsed.set("—")
        self.root_status.config(text=f"Siklus dibuang — {reason}")
        self.root.after(
            3000,
            lambda: self._set_cycle_state("IDLE")
            if self._var_state.get() == "DISCARD" else None,
        )
    def _on_discard_timeout(self, idle: float):
        logger.warning(f"[Discard] {idle:.0f}s tanpa ROI — video dibuang")
        self._logger.log("DISCARD", f"timeout idle {idle:.0f}s")
        if self.yolo._tracker:
            self.yolo._tracker.abort_cycle()
        if self._ct_recorder:
            self._ct_recorder.discard()
        self._enter_discard(f"timeout {idle:.0f}s, operator meninggalkan station")
    def _on_cycle_aborted(self, reason: str):
        logger.warning(f"[Discard] ABORT: {reason} — video dibuang")
        self._logger.log("DISCARD", reason)
        if self._ct_recorder:
            self._ct_recorder.discard()
        self._ui(lambda: self._enter_discard(reason))
    def _show_complete(self):
        self._hold_state_until = time.time() + 2.0
        self._set_cycle_state("COMPLETE")
    # ──────────────────────────────────────────────────────────────────────
    #  UI Builder
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        # Preview
        preview_outer = ttk.LabelFrame(
            main, text="Live Preview", padding=5
        )
        preview_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview_outer.rowconfigure(0, weight=1)
        preview_outer.columnconfigure(0, weight=1)

        self._preview_container = tk.Frame(preview_outer, bg="black")
        self._preview_container.grid(row=0, column=0, sticky="nsew")

        self.preview = tk.Label(
            self._preview_container,
            text="Camera Preview",
            bg="black",
            fg="white",
            font=("Arial", 18),
        )
        self.preview.pack(fill="both", expand=True)
        self.roi_view = ROIViewer(lambda: self.yolo.engine)

        # Control panel (scrollable)
        ctrl_outer = tk.Frame(main)
        ctrl_outer.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(1, minsize=300, weight=0)

        ctrl_canvas = tk.Canvas(ctrl_outer, highlightthickness=0)
        ctrl_scrollbar = ttk.Scrollbar(
            ctrl_outer, orient="vertical", command=ctrl_canvas.yview
        )
        ctrl_canvas.configure(yscrollcommand=ctrl_scrollbar.set)
        ctrl_scrollbar.pack(side="right", fill="y")
        ctrl_canvas.pack(side="left", fill="both", expand=True)
        self._ctrl_outer = ctrl_outer
        self._ctrl_scrollbar = ctrl_scrollbar
        control = ttk.Frame(ctrl_canvas)
        _win = ctrl_canvas.create_window((0, 0), window=control, anchor="nw")
        self._ctrl_canvas = ctrl_canvas
        self._ctrl_frame = control

        def _on_ctrl_resize(e):
            ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all"))
            ctrl_canvas.itemconfig(_win, width=ctrl_canvas.winfo_width())

        def _on_canvas_resize(e):
            ctrl_canvas.itemconfig(_win, width=e.width)

        def _on_mousewheel(e):
            ctrl_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        control.bind("<Configure>", _on_ctrl_resize)
        ctrl_canvas.bind("<Configure>", _on_canvas_resize)
        ctrl_canvas.bind("<MouseWheel>", _on_mousewheel)
        control.bind("<MouseWheel>", _on_mousewheel)

        # Tombol mode (paling atas)
        self._mode_btn = ttk.Button(
            control, text="▶  Production Mode", command=self._on_mode_button
        )
        self._mode_btn.pack(fill="x", pady=(0, 6), ipady=3)

        # Panel yang selalu tampil (production + setting)
        self._build_panel_stasiun(control)
        self._build_panel_produksi(control)
        self._build_panel_cycle_time(control)

        # Panel khusus setting mode
        self._setting_frame = ttk.Frame(control)
        self._setting_frame.pack(fill="x")
        sf = self._setting_frame
        self._build_panel_camera(sf)
        self._build_panel_yolo(sf)
        self._build_panel_logic(sf)
        self._build_panel_capture(sf)
        self._build_panel_server(sf)
        self._build_panel_storage(sf)
        self._build_panel_status(sf)

        self.root_status = ttk.Label(
            self.root, text="Ready", relief="sunken", anchor="w", padding=4
        )
        self.root_status.grid(row=1, column=0, sticky="ew")

        self._bind_mouse_scroll()

    # ── Panel: STASIUN ────────────────────────────────────────────────────

    def _build_panel_stasiun(self, parent):
        box = ttk.LabelFrame(parent, text="STASIUN", padding=8)
        box.pack(fill="x", pady=(0, 6))

        def _row(lbl, var):
            r = ttk.Frame(box)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=lbl, width=12, anchor="w").pack(side="left")
            ttk.Label(r, textvariable=var, font=("Consolas", 10, "bold")).pack(
                side="left", fill="x", expand=True, anchor="w"
            )

        # Semua nilai di bawah ini datang dari response server
        _row("Station ID", self._var_station)
        _row("Nama",       self._var_name)
        _row("Line",       self._var_line)
        _row("Hostname",   self._var_host)
        _row("IP",         self._var_ip)
        _row("Port",       self._var_port)

        op_row = ttk.Frame(box)
        op_row.pack(fill="x", pady=(6, 0))
        ttk.Label(op_row, text="Operator", width=12, anchor="w").pack(side="left")
        self._op_entry = ttk.Entry(op_row, font=("Arial", 10), width=10)
        self._op_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(
            op_row, text="Set", width=5,
            command=lambda: self._set_operator(self._op_entry.get()),
        ).pack(side="left")

    # ── Panel: PRODUKSI ───────────────────────────────────────────────────

    def _build_panel_produksi(self, parent):
        box = ttk.LabelFrame(parent, text="PRODUKSI", padding=8)
        box.pack(fill="x", pady=(0, 6))

        def _row(lbl, var, bold=False):
            f = ttk.Frame(box)
            f.pack(fill="x", pady=2)
            ttk.Label(f, text=lbl, width=12, anchor="w").pack(side="left")
            font = ("Consolas", 11, "bold") if bold else ("Consolas", 10)
            lb = ttk.Label(f, textvariable=var, font=font)
            lb.pack(side="left")
            return lb

        state_row = ttk.Frame(box)
        state_row.pack(fill="x", pady=2)
        ttk.Label(state_row, text="State", width=12, anchor="w").pack(side="left")
        self._state_label = ttk.Label(
            state_row, textvariable=self._var_state, font=("Consolas", 12, "bold")
        )
        self._state_label.pack(side="left")
        _row("Anomaly (s)", self._var_anom_lim)
        _row("Discard (s)", self._var_disc_lim)
        _row("Min CT (s)", self._var_min_lim)
        _row("Short (s)", self._var_short_lim) 
        _row("Cycle Count", self._var_cycle, bold=True)
        _row("Takt Time", self._var_takt)

        count_row = ttk.Frame(box)
        count_row.pack(fill="x", pady=(6, 2))
        ttk.Label(count_row, text="OK", width=4, anchor="w").pack(side="left")
        ttk.Label(
            count_row,
            textvariable=self._var_ok_count,
            font=("Consolas", 11, "bold"),
            foreground="#2e7d32",
        ).pack(side="left")
        ttk.Label(count_row, text="  ANOM").pack(side="left")
        ttk.Label(
            count_row,
            textvariable=self._var_anom_count,
            font=("Consolas", 11, "bold"),
            foreground="#c62828",
        ).pack(side="left")
    def _build_panel_cycle_time(self, parent):
        box = ttk.LabelFrame(parent, text="Cycle Time", padding=8)
        box.pack(fill="x", pady=(0, 6))

        # 1. Current Cycle (pindahan dari Elapsed)
        cur = ttk.Frame(box)
        cur.pack(fill="x", pady=2)
        ttk.Label(cur, text="Current Cycle", width=14, anchor="w").pack(side="left")
        self._elapsed_label = ttk.Label(
            cur, textvariable=self._var_elapsed, font=("Consolas", 14, "bold")
        )
        self._elapsed_label.pack(side="left")

        ttk.Separator(box, orient="horizontal").pack(fill="x", pady=6)

        # 2. Last Cycle (besar)
        ttk.Label(box, text="Last Cycle", anchor="w").pack(fill="x")
        self._last_ct_label = ttk.Label(
            box, textvariable=self._var_last_ct,
            font=("Consolas", 32, "bold"), anchor="center",
        )
        self._last_ct_label.pack(fill="x", pady=(0, 4))

        ttk.Separator(box, orient="horizontal").pack(fill="x", pady=6)

        # 3. Statistik tambahan
        def _row(lbl, var):
            r = ttk.Frame(box)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=lbl, width=14, anchor="w").pack(side="left")
            ttk.Label(r, textvariable=var, font=("Consolas", 11, "bold")).pack(side="left")

        _row("Average", self._var_avg_ct)
    # ── Panel: Camera ─────────────────────────────────────────────────────

    def _build_panel_camera(self, parent):
        cam_box = ttk.LabelFrame(parent, text="Camera", padding=8)
        cam_box.pack(fill="x", pady=(0, 6))

        ttk.Label(cam_box, text="Sumber").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(
            cam_box,
            textvariable=self.source_var,
            values=["Kamera", "Video File"],
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="ew", padx=4)
        self.source_var.trace_add("write", self._on_source_change)

        self.cam_label = ttk.Label(cam_box, text="Pilih Kamera")
        self.cam_label.grid(row=1, column=0, sticky="w", pady=2)
        self.cam_combo = ttk.Combobox(
            cam_box,
            textvariable=self.camera_name_var,
            values=list(self._camera_map.keys()),
            state="readonly",
            width=18,
        )
        self.cam_combo.grid(row=1, column=1, sticky="ew", padx=4)

        self.vid_label = ttk.Label(cam_box, text="File Video")
        self.vid_row = ttk.Frame(cam_box)
        ttk.Entry(self.vid_row, textvariable=self.video_path_var, width=14).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(
            self.vid_row, text="Browse", command=self._browse_video, width=7
        ).pack(side="left", padx=(4, 0))
        self.vid_label.grid(row=2, column=0, sticky="w", pady=2)
        self.vid_row.grid(row=2, column=1, sticky="ew", padx=4)
        self.vid_label.grid_remove()
        self.vid_row.grid_remove()

        for row, (lbl, var) in enumerate(
            [("Width", self.width), ("Height", self.height), ("FPS", self.fps)], start=3
        ):
            ttk.Label(cam_box, text=lbl).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(cam_box, textvariable=var, width=10).grid(
                row=row, column=1, sticky="ew", padx=4
            )

        ttk.Label(cam_box, text="Format").grid(row=6, column=0, sticky="w", pady=2)
        ttk.Combobox(
            cam_box,
            textvariable=self.format_var,
            values=["mp4 (H.264)", "mp4 (H.265)"],
            state="readonly",
            width=12,
        ).grid(row=6, column=1, sticky="ew", padx=4)

        ttk.Checkbutton(cam_box, text="Mirror", variable=self.mirror_var).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.open_btn = ttk.Button(
            cam_box, text="Buka Kamera", command=self.toggle_camera
        )
        self.open_btn.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    # ── Panel: YOLO ───────────────────────────────────────────────────────

    def _build_panel_yolo(self, parent):
        yolo_box = ttk.LabelFrame(parent, text="YOLO Detector", padding=8)
        yolo_box.pack(fill="x", pady=(0, 6))

        model_row = ttk.Frame(yolo_box)
        model_row.pack(fill="x", pady=(0, 4))
        ttk.Entry(model_row, textvariable=self.model_path_var, width=16).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(model_row, text="Browse", command=self._browse_model, width=7).pack(
            side="left", padx=(4, 0)
        )

        ttk.Button(yolo_box, text="Load Model", command=self._load_model).pack(
            fill="x", pady=(0, 4)
        )

        conf_hdr = ttk.Frame(yolo_box)
        conf_hdr.pack(fill="x")
        ttk.Label(conf_hdr, text="Confidence:").pack(side="left")
        self.conf_label = ttk.Label(conf_hdr, text="0.25", width=4)
        self.conf_label.pack(side="right")

        ttk.Scale(
            yolo_box,
            from_=0.01,
            to=1.0,
            orient="horizontal",
            variable=self.conf_var,
            command=self._on_conf_change,
        ).pack(fill="x", pady=(2, 6))

        self.yolo_btn = ttk.Button(
            yolo_box,
            text="▶  Aktifkan YOLO",
            command=self._toggle_yolo,
            state="disabled",
        )
        self.yolo_btn.pack(fill="x", ipady=3)
        ttk.Label(yolo_box, textvariable=self.det_count_var, font=("Arial", 9)).pack(
            anchor="center", pady=(3, 0)
        )

        ttk.Label(yolo_box, text="Track Class:", font=("Arial", 9, "bold")).pack(
            anchor="w", pady=(8, 2)
        )
        self._class_list_frame = ttk.Frame(yolo_box)
        self._class_list_frame.pack(fill="x", padx=4)

    # ── Panel: Capture ────────────────────────────────────────────────────

    def _build_panel_capture(self, parent):
        cap_box = ttk.LabelFrame(parent, text="Capture", padding=8)
        cap_box.pack(fill="x", pady=(0, 6))

        self.photo_btn = ttk.Button(
            cap_box, text="📷  FOTO  [F]", command=self.take_photo, state="disabled"
        )
        self.photo_btn.pack(fill="x", ipady=5, pady=2)
        self.pause_btn = ttk.Button(
            cap_box,
            text="⏸  PAUSE (TEST)  [P]",
            command=self.toggle_pause,
            state="disabled",
        )
        self.pause_btn.pack(fill="x", ipady=5, pady=2)
        self.record_btn = ttk.Button(
            cap_box,
            text="🎥  MULAI REKAM  [R]",
            command=self.toggle_recording,
            state="disabled",
        )
        self.record_btn.pack(fill="x", ipady=5, pady=2)

        ttk.Label(cap_box, text="Durasi").pack(anchor="w", pady=(6, 0))
        ttk.Label(
            cap_box, textvariable=self.timer_var, font=("Consolas", 16, "bold")
        ).pack(anchor="center")
        ttk.Label(cap_box, textvariable=self.photo_count_var, font=("Arial", 9)).pack(
            anchor="center", pady=(2, 0)
        )

    # ── Panel: SERVER ─────────────────────────────────────────────────────

    def _build_panel_server(self, parent):
        box = ttk.LabelFrame(parent, text="SERVER", padding=8)
        box.pack(fill="x", pady=(0, 6))
        ip_row = ttk.Frame(box)
        ip_row.pack(fill="x", pady=(0, 4))
        ttk.Label(ip_row, text="IP Master", width=12, anchor="w").pack(side="left")
        ttk.Entry(ip_row, textvariable=self._var_master_ip, width=15).pack(
            side="left", fill="x", expand=True)
        ttk.Label(ip_row, text=" :").pack(side="left")
        ttk.Entry(ip_row, textvariable=self._var_master_port, width=6,
                  justify="center").pack(side="left")
        ttk.Button(box, text="💾  Simpan & Hubungkan",
                   command=self._apply_master_target).pack(fill="x", pady=(0, 6), ipady=2)
        def _row(lbl, var):
            r = ttk.Frame(box)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=lbl, width=12, anchor="w").pack(side="left")
            ttk.Label(r, textvariable=var, font=("Consolas", 10)).pack(
                side="left", fill="x", expand=True, anchor="w"
            )

        _row("Status",    self._var_server)
        _row("Takt Time", self._var_takt)
        _row("Timeout",   self._var_disc_lim)

        self._server_indicator = tk.Label(box, text="●", fg="#888", font=("Arial", 16))
        self._server_indicator.pack(pady=(4, 0))
        self._reconnect_btn = ttk.Button(
            box, text="🔄  Reconnect Server", command=self._reconnect_server
        )
        self._reconnect_btn.pack(fill="x", pady=(6, 0), ipady=3)

    # ── Panel: Storage ────────────────────────────────────────────────────

    def _build_panel_storage(self, parent):
        box = ttk.LabelFrame(parent, text="Penyimpanan", padding=8)
        box.pack(fill="x", pady=(0, 6))
        ttk.Label(box, textvariable=self.folder_var, wraplength=260).pack(
            anchor="w", pady=(0, 4)
        )
        ttk.Button(box, text="Pilih Folder", command=self.select_folder).pack(fill="x")
        ttk.Button(box, text="Sync Ukuran Video ke Server",
                   command=self._sync_sizes_now).pack(fill="x", pady=(4, 0))

    # ── Panel: Status ─────────────────────────────────────────────────────

    def _build_panel_status(self, parent):
        box = ttk.LabelFrame(parent, text="Status", padding=8)
        box.pack(fill="x")
        ttk.Label(box, text="Camera:").pack(anchor="w")
        ttk.Label(box, textvariable=self.status_var, font=("Arial", 10, "bold")).pack(
            anchor="w", pady=(0, 4)
        )
        ttk.Label(box, text="Recording:").pack(anchor="w")
        ttk.Label(
            box, textvariable=self.record_status_var, font=("Arial", 10, "bold")
        ).pack(anchor="w")

    # ──────────────────────────────────────────────────────────────────────
    #  UI helpers
    # ──────────────────────────────────────────────────────────────────────
    def _set_mode(self, mode: str):
        self._mode = mode
        if mode == "production":
            self._setting_frame.pack_forget()
            self._mode_btn.config(text="🔒  Setting Mode (perlu login)")
            self.root_status.config(text="Production Mode")
        else:
            self._setting_frame.pack(fill="x")
            self._mode_btn.config(text="▶  Production Mode")
            self.root_status.config(text="Setting Mode")
        self._ctrl_canvas.yview_moveto(0)

    def _on_mode_button(self):
        if self._mode == "setting":
            self._set_mode("production")          # keluar dari setting: tanpa login
        elif self._require_login(startup=False):  # masuk setting: wajib relogin
            self._set_mode("setting")
        else:
            self.root_status.config(text="Login dibatalkan — tetap di Production Mode")

    def _set_operator(self, value: str):
        value = value.strip()
        if value:
            self._var_operator.set(value)
            self.args.operator = value
            self.root_status.config(text=f"Operator: {value}")
    def _set_timer(self, key: str, entry):        # ← tempel di sini
        try:
            v = float(entry.get().replace(",", "."))
            if v <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Timer", "Isi angka detik > 0")
            return
        self._cfg[key] = v
        save_settings(self._cfg, str(self._settings_path))
        self.root_status.config(text=f"{key} = {v:.0f}s")
    def toggle_pause(self):
        """TEST MODE: bekukan preview, tapi timer cycle/discard tetap jalan."""
        self._paused = not self._paused
        if self._paused:
            self.pause_btn.config(text="▶  RESUME  [P]")
            self.root_status.config(
                text="⏸ PAUSE — preview dibekukan, timer tetap jalan (mode test)"
            )
            logger.info("[Pause] Preview dibekukan, timer tetap jalan")
        else:
            self.pause_btn.config(text="⏸  PAUSE (TEST)  [P]")
            self.root_status.config(text="▶ RESUME — stream kembali normal")
            logger.info("[Pause] Resume")
    def _set_cycle_state(self, state: str):
        self._var_state.set(state)
        self._live_state = state
        self._state_evt.set()
        colors = {
            "IDLE": None, "RUNNING": "#2e7d32", "TRANSIT": "#ef6c00",
            "COMPLETE": "#f57f17", "TIMEOUT": "#c62828", "FAILED": "#c62828",
            "CAMERA_OFFLINE": "#6a1b9a",
            "DISCARD": "#c62828",
        }
        fg = colors.get(state)
        if fg:
            self._state_label.config(foreground=fg)
        else:
            self._state_label.config(foreground="")

    def _on_cycle_ui_update(self, cycle_count: int, cycle_time: float, anom_type: str,
                            is_genesis: bool = False, skipped=None, label: str = ""):
        skipped = skipped or []
        self._var_cycle.set(str(cycle_count))
        self._var_last_ct.set(f"{cycle_time:.1f}s")
        color = {"OK": "#2e7d32", "LONG": "#c62828", "SHORT": "#ef6c00"}[anom_type]
        if skipped and anom_type == "OK":
            color = "#ef6c00"
        self._last_ct_label.config(foreground=color)
        self._ct_history.append(cycle_time)
        self._ct_history = self._ct_history[-50:]
        self._var_avg_ct.set(f"{sum(self._ct_history) / len(self._ct_history):.1f}s")
        tag = {"OK": "OK", "LONG": "ANOMALY", "SHORT": "SHORT ANOMALY"}[anom_type]
        if is_genesis:
            tag += " | GENESIS"
        self.root_status.config(
            text=f"Cycle {cycle_count} selesai | CT={cycle_time:.2f}s | {tag}"
        )
        # Update OK/ANOM count dari folder
        ok_n = sum(1 for f in VIDEOS_DIR.glob("*/*/normal/*.mp4")
                   if not f.name.startswith("_tmp_"))
        anom_n = sum(1 for _ in VIDEOS_DIR.glob("*/*/anomali/*.mp4"))
        self._var_ok_count.set(str(ok_n))
        self._var_anom_count.set(str(anom_n))

    def _bind_shortcuts(self):
        self.root.bind(
            "<p>", lambda e: self.toggle_pause() if self.running else None
        )
        self.root.bind(
            "<f>",
            lambda e: self.take_photo() if self._latest_frame is not None else None,
        )
        self.root.bind(
            "<r>", lambda e: self.toggle_recording() if self.running else None
        )

    def _bind_mouse_scroll(self):
        def _scroll2(e):
            self._ctrl_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _bind_children(widget):
            widget.bind("<MouseWheel>", _scroll2)
            for child in widget.winfo_children():
                _bind_children(child)

        _bind_children(self._ctrl_frame)

    # ──────────────────────────────────────────────────────────────────────
    #  YOLO controls
    # ──────────────────────────────────────────────────────────────────────

    def _browse_model(self):
        path = filedialog.askopenfilename(
            title="Pilih model YOLO (.pt)",
            filetypes=[("PyTorch model", "*.pt"), ("All", "*.*")],
        )
        if path:
            self.model_path_var.set(path)

    def _load_model(self):
        if not YOLO_AVAILABLE:
            messagebox.showerror("Error", "pip install ultralytics")
            return
        path = self.model_path_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Model", "File tidak ditemukan.")
            return
        self.root_status.config(text="Loading model...")
        self.root.update_idletasks()
        if self.yolo.load(path):
            self.yolo_btn.config(state="normal")
            self.root_status.config(text=f"Model: {os.path.basename(path)}")
            self._logger.log("YOLO", f"loaded | {path}")
            self._build_class_checklist(self.yolo.get_class_names())
            # Init cycle recorder setelah model siap
            if not self.args.no_record and (not self.args.station_no or not self.args.line):
                messagebox.showwarning("Model", "Identitas stasiun belum diterima dari server. "
                                                "Tunggu status 'Terhubung ✓', lalu Load Model lagi.")
                return
            self._sync_recorder()
            # Init reporter
            if self._reporter is None:
                self._reporter = CycleReporter(self.output_dir)
                self._logger.log("REPORT", f"CSV: {self._reporter.path()}")
        else:
            messagebox.showerror("Model", "Gagal load model.")
            self.root_status.config(text="Gagal load model.")

    def _on_conf_change(self, _=None):
        v = round(self.conf_var.get(), 2)
        self.conf_label.config(text=f"{v:.2f}")
        self.yolo.set_conf(v)

    def _toggle_yolo(self):
        self.yolo.enabled = not self.yolo.enabled
        if self.yolo.enabled:
            self.yolo_btn.config(text="⏹  Nonaktifkan YOLO")
        else:
            self.yolo_btn.config(text="▶  Aktifkan YOLO")
            self.det_count_var.set("Deteksi: 0")

    def _build_class_checklist(self, names: dict):
        # Pakai state centang saat ini kalau model di-load ulang; kalau belum ada, pakai yang tersimpan
        if self._class_vars:
            disabled = {l for l, v in self._class_vars.items() if not v.get()}
        else:
            disabled = set(self._saved_disabled_classes)
        for w in self._class_list_frame.winfo_children():
            w.destroy()
        self._class_vars.clear()
        for cls_id in sorted(names.keys()):
            label = names[cls_id]
            var = tk.BooleanVar(value=label not in disabled)
            self._class_vars[label] = var
            ttk.Checkbutton(
                self._class_list_frame,
                text=label,
                variable=var,
                command=self._on_class_selection_change,
            ).pack(anchor="w")
        self._on_class_selection_change()
        self._bind_mouse_scroll()

    def _on_class_selection_change(self):
        self._tracked_classes = {
            lbl for lbl, var in self._class_vars.items() if var.get()
        }
        self.yolo.set_tracked_classes(self._tracked_classes)
    # ──────────────────────────────────────────────────────────────────────
    #  Camera
    # ──────────────────────────────────────────────────────────────────────

    def _codec(self):
        return "libx265" if "H.265" in self.format_var.get() else "libx264"

    def _sync_recorder(self):
        fps = self.OUTPUT_FPS
        if self._ct_recorder is None:
            if self.args.no_record or not self.args.station_no or not self.args.line:
                return
            self._ct_recorder = VideoRecorder(
                station_id=self.args.station_no, line=self.args.line,
                frame_width=self._frame_w, frame_height=self._frame_h,
                fps=fps, codec=self._codec(),
            )
        else:
            self._ct_recorder.configure(self._frame_w, self._frame_h, fps, self._codec())
    # ──────────────────────────────────────────────────────────────────────
    #  Camera
    # ──────────────────────────────────────────────────────────────────────

    def toggle_camera(self):
        if self.running:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        try:
            width = int(self.width.get())
            height = int(self.height.get())
            fps = int(self.fps.get())

            if self.source_var.get() == "Video File":
                path = self.video_path_var.get().strip()
                if not path or not os.path.exists(path):
                    messagebox.showerror("Video", "File tidak ditemukan.")
                    return
                cap = cv2.VideoCapture(path)
                if not cap.isOpened():
                    raise RuntimeError(f"Tidak bisa buka video: {path}")
                source_label = os.path.basename(path)
            else:
                name = self.camera_name_var.get()
                index = self._camera_map.get(name, 0)
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    cap = cv2.VideoCapture(index)
                if not cap.isOpened():
                    raise RuntimeError(f"Tidak bisa buka kamera: {name}")
                source_label = name

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)

            self.cap = cap
            self.running = True
            self._last_frame_ts = time.time()
            self._set_cycle_state("IDLE")     # keluar dari CAMERA_OFFLINE
            self._frame_w = actual_w
            self._frame_h = actual_h
            notes = self.yolo.engine.adapt(actual_w, actual_h, self.mirror_var.get())
            if notes:
                logger.info(f"[Logic] ROI disesuaikan: {', '.join(notes)}")
            self._actual_fps = actual_fps if 0 < actual_fps <= 240 else float(fps)
            self._measured_fps = None
            self._fps_stable = False
            self._sync_recorder()

            self.status_var.set(
                f"ON - {actual_w}x{actual_h} @ {self._actual_fps:.1f}fps  [{source_label}]"
            )
            self.open_btn.config(text="Tutup")
            self.photo_btn.config(state="normal")
            self.record_btn.config(state="disabled")
            self.pause_btn.config(state="normal")
            self._paused = False
            self.pause_btn.config(text="⏸  PAUSE (TEST)  [P]")
            self.root_status.config(text="Menunggu FPS stabil...")
            self._logger.log("CAM", f"buka | {source_label} | {actual_w}x{actual_h}")

            self._capture_thread = threading.Thread(
                target=self._capture_loop, daemon=True
            )
            self._capture_thread.start()
            self._schedule_ui_update()

        except Exception as e:
            if self.cap:
                self.cap.release()
                self.cap = None
            self._logger.log("CAM", f"ERROR: {e}")
            messagebox.showerror("Error", str(e))

    def _capture_loop(self):
        try:
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 2
            )
        except Exception:
            pass

        is_video_file = self.source_var.get() == "Video File"
        for _ in range(5 if not is_video_file else 0):
            self.cap.read()

        consecutive_fail = 0
        frame_count = 0
        fps_timer = time.perf_counter()
        measure_n = 0
        target_iv = 1.0 / self._actual_fps
        next_t = time.perf_counter()

        while self.running:
            now = time.perf_counter()
            wait = next_t - now
            if wait > 0.002:
                time.sleep(wait - 0.001)
            try:
                ret, frame = self.cap.read()
            except Exception as e:
                logger.error(f"[Camera] read exception: {e}")
                ret, frame = False, None
            now = time.perf_counter()
            next_t += target_iv
            if now > next_t + target_iv:
                next_t = now + target_iv

            if not ret:
                if is_video_file:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    next_t = time.perf_counter()
                    continue
                else:
                    consecutive_fail += 1
                    if consecutive_fail >= 10:
                        self.root.after(0, self._on_camera_error)
                        break
                    continue

            consecutive_fail = 0
            self._last_frame_ts = time.time()
            frame_count += 1

            elapsed = now - fps_timer
            if elapsed >= 3.0:
                self._measured_fps = frame_count / elapsed
                measure_n += 1
                frame_count = 0
                fps_timer = now
                if (measure_n >= 2 or is_video_file) and not self._fps_stable:
                    self._fps_stable = True
                    self.root.after(0, self._on_fps_stable)
            if self._paused:
                continue
            if self.mirror_var.get():
                frame = cv2.flip(frame, 1)

            with self._frame_lock:
                self._latest_frame = frame

            fd_slave_api.push_frame(frame)

            self.yolo.push(frame)

            # Feed ke cycle recorder jika sedang recording cycle
            if self._ct_recorder:
                self._ct_recorder.write_frame(frame, self._last_frame_ts)
            if self.recording:
                try:
                    self._write_queue.put_nowait((frame.copy(), now))
                except queue.Full:
                    pass

    def _on_fps_stable(self):
        if self.running:
            self._sync_recorder()
            self.record_btn.config(state="normal")
            self.root_status.config(
                text=f"Kamera aktif — {self._measured_fps:.1f}fps → output {self.OUTPUT_FPS}fps"
            )

    def _on_camera_error(self):
        if not self.running:          # cegah dipanggil dobel (read gagal + frame beku)
            return
        logger.error("[Camera] CRASH — frame tidak diterima")
        self._logger.log("CAMERA", "CRASH / OFFLINE")
        self.stop_camera(crashed=True)

    def stop_camera(self, crashed: bool = False):
        if self.recording:
            self.stop_recording()
        self.running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=2)
            self._capture_thread = None
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        with self._frame_lock:
            self._latest_frame = None

        # Siklus yang sedang berjalan tidak valid lagi
        tracker = self.yolo._tracker
        if tracker and tracker.is_cycle_active():
            tracker.abort_cycle()
            if self._ct_recorder:
                self._ct_recorder.discard()
            self._logger.log(
                "DISCARD", "camera crash — siklus dibatalkan" if crashed else "kamera ditutup"
            )

        self.preview.config(image="", text="Camera Preview")
        self.status_var.set("KAMERA OFFLINE" if crashed else "Kamera belum dibuka")
        src = self.source_var.get()
        self.open_btn.config(text="Buka Video" if src == "Video File" else "Buka Kamera")
        self.photo_btn.config(state="disabled")
        self.record_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")
        self._paused = False
        self.pause_btn.config(text="⏸  PAUSE (TEST)  [P]")

        self._hold_state_until = 0.0
        self._var_elapsed.set("—")
        if crashed:
            self._set_cycle_state("CAMERA_OFFLINE")   # → dikirim ke server via heartbeat
            self.root_status.config(text="⚠ Kamera crash / disconnect — klik Buka Kamera untuk menyambung ulang")
        else:
            self._set_cycle_state("IDLE")
            self.root_status.config(text="Kamera ditutup")
    def _on_source_change(self, *args):
        if self.source_var.get() == "Video File":
            self.cam_label.grid_remove()
            self.cam_combo.grid_remove()
            self.vid_label.grid()
            self.vid_row.grid()
            self.open_btn.config(text="Buka Video")
        else:
            self.vid_label.grid_remove()
            self.vid_row.grid_remove()
            self.cam_label.grid()
            self.cam_combo.grid()
            self.open_btn.config(text="Buka Kamera")

    def _browse_video(self):
        path = filedialog.askopenfilename(
            title="Pilih file video",
            filetypes=[
                ("Video files", "*.mp4 *.avi *.mov *.mkv *.wmv"),
                ("All", "*.*"),
            ],
        )
        if path:
            self.video_path_var.set(path)

    # ──────────────────────────────────────────────────────────────────────
    #  UI update loop
    # ──────────────────────────────────────────────────────────────────────

    def _schedule_ui_update(self):
        if not self.running:
            return

        try:
            if self.yolo.enabled:
                drawn, raw, dets = self.yolo.get_result()
                display = drawn if drawn is not None else raw
                self.det_count_var.set(f"Deteksi: {len(dets)}")
            else:
                with self._frame_lock:
                    display = (
                        self._latest_frame.copy()
                        if self._latest_frame is not None
                        else None
                    )

            if display is not None:
                prev_w = self._preview_container.winfo_width()
                prev_h = self._preview_container.winfo_height()
                rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
                resized = self._resize_for_preview(rgb, prev_w or 800, prev_h or 580)
                rh, rw = resized.shape[:2]
                if self._roi_editor is not None:
                    ox = max(0, (self.preview.winfo_width() - rw) // 2)
                    oy = max(0, (self.preview.winfo_height() - rh) // 2)
                    self._roi_editor.set_view(rw, rh, self._frame_w, self._frame_h, ox, oy)
                    resized = self._roi_editor.draw_on_frame(resized)
                else:
                    self.roi_view.set_scale(rw, rh, self._frame_w, self._frame_h)
                    resized = self.roi_view.draw_on_frame(resized)
                img = Image.fromarray(resized)
                photo = ImageTk.PhotoImage(image=img)
                self.preview.config(image=photo, text="")
                self.preview.image = photo

                # Update server status
                if self._takt:
                    sio_ok = self._takt.is_connected
                    saving = fd_slave_api.is_saving()
                    hb_ok = fd_slave_api.is_hb_connected() or saving
                    server_now = sio_ok and hb_ok
                    if self._server_connected and not server_now:
                        self._server_disconnected_since = time.time()
                    elif not self._server_connected and server_now:
                        self._server_disconnected_since = None
                    self._server_connected = server_now
                    if saving and sio_ok:
                        color, text = "#1e88e5", "SAVING MODE"
                    elif server_now:
                        color, text = "#4caf50", "Terhubung ✓"
                    elif sio_ok:
                        color, text = "#ff9800", "HB PUTUS (reconnect...)"
                    else:
                        color, text = "#e53935", "TERPUTUS ✗"
                    self._server_indicator.config(fg=color)
                    self._var_server.set(text)
                    self._var_takt.set(f"{self._takt.value:.1f}s")
                    self._var_anom_lim.set(f"{self._takt.anomaly_timer:.0f}")
                    self._var_disc_lim.set(f"{self._takt.discard_timeout:.0f}")
                    self._var_min_lim.set(f"{self._takt.min_cycle:.1f}")
                    sl = self._short_limit()
                    self._var_short_lim.set(f"{sl:.1f}" if sl > 0 else "off")
            # Timer anomaly + discard
            if (self.running and not self._paused
                    and self.source_var.get() != "Video File"
                    and time.time() - self._last_frame_ts > 3.0):
                self._on_camera_error()
                return
            self._check_timers()
            if not (self.yolo._tracker and self.yolo._tracker.is_cycle_active()):
                self._var_elapsed.set("—")
            if self.recording:
                self._update_timer()

        except Exception as e:
            logger.error(f"[UI] _schedule_ui_update error: {e}", exc_info=True)

        self.root.after(33, self._schedule_ui_update)

    @staticmethod
    def _resize_for_preview(image, max_w, max_h):
        h, w = image.shape[:2]
        scale = min(max_w / w, max_h / h)
        if scale >= 1:
            return image
        return cv2.resize(
            image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
        )

    # ──────────────────────────────────────────────────────────────────────
    #  Photo
    # ──────────────────────────────────────────────────────────────────────

    def take_photo(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return
            raw = self._latest_frame.copy()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        cv2.imwrite(os.path.join(self.output_dir, f"photo_{ts}_raw.jpg"), raw)
        if self.yolo.enabled:
            drawn, _, _ = self.yolo.get_result()
            if drawn is not None:
                cv2.imwrite(os.path.join(self.output_dir, f"photo_{ts}_ann.jpg"), drawn)
        self._photo_count += 1
        self.photo_count_var.set(f"Foto: {self._photo_count}")
        self.root_status.config(text=f"Foto tersimpan: photo_{ts}_raw.jpg")

    # ──────────────────────────────────────────────────────────────────────
    #  Recording (ffmpeg manual — untuk rekam keseluruhan sesi)
    # ──────────────────────────────────────────────────────────────────────

    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def _make_pipe(self, filename, width, height, codec, crf):
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "bgr24",
            "-r",
            str(self.OUTPUT_FPS),
            "-i",
            "-",
            "-c:v",
            codec,
            "-crf",
            crf,
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            filename,
        ]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def start_recording(self):
        with self._frame_lock:
            if self._latest_frame is None:
                messagebox.showwarning("Rekam", "Kamera belum siap.")
                return
        if not shutil.which("ffmpeg"):
            messagebox.showerror("Error", "FFmpeg tidak ditemukan.")
            return
        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        codec = "libx265" if "H.265" in self.format_var.get() else "libx264"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._video_raw = os.path.join(self.output_dir, f"video_{ts}_raw.mp4")
        self._video_ann = os.path.join(self.output_dir, f"video_{ts}_ann.mp4")
        self._ffmpeg_raw = self._make_pipe(self._video_raw, width, height, codec, "28")
        self._ffmpeg_ann = self._make_pipe(self._video_ann, width, height, codec, "28")
        self.recording = True
        self.record_start_time = time.time()
        self._write_queue = queue.Queue(maxsize=120)
        self._writer_thread = threading.Thread(target=self._writer_worker, daemon=True)
        self._writer_thread.start()
        self.record_btn.config(text="⏹  STOP REKAM  [R]")
        self.record_status_var.set("● RECORDING")
        self.root_status.config(text=f"Merekam → {os.path.basename(self._video_raw)}")

    def _writer_worker(self):
        total = 0
        frame_dur = 1.0 / self.OUTPUT_FPS
        last_ts = None
        debt = 0.0
        while True:
            item = self._write_queue.get()
            if item is None:
                break
            raw_frame, capture_ts = item
            if last_ts is None:
                repeat = 1
                debt = 0.0
            else:
                gap = min(capture_ts - last_ts, 2.0)
                total_due = gap + debt
                repeat = int(total_due / frame_dur)
                debt = total_due - (repeat * frame_dur)
                repeat = max(1, repeat)
            last_ts = capture_ts
            ann_frame = raw_frame
            if self.yolo.enabled:
                drawn, _, _ = self.yolo.get_result()
                if drawn is not None:
                    ann_frame = drawn
            raw_bytes = raw_frame.tobytes()
            ann_bytes = ann_frame.tobytes()
            for _ in range(repeat):
                try:
                    self._ffmpeg_raw.stdin.write(raw_bytes)
                except BrokenPipeError:
                    pass
                try:
                    self._ffmpeg_ann.stdin.write(ann_bytes)
                except BrokenPipeError:
                    pass
                total += 1
            self._write_queue.task_done()

    def stop_recording(self):
        self.recording = False
        dur = time.time() - self.record_start_time if self.record_start_time else 0
        if self._writer_thread and self._writer_thread.is_alive():
            self._write_queue.put(None)
            self._writer_thread.join()
        self._writer_thread = None
        for pipe in [self._ffmpeg_raw, self._ffmpeg_ann]:
            if pipe:
                try:
                    pipe.stdin.close()
                    pipe.wait()
                except Exception:
                    pass
        self._ffmpeg_raw = self._ffmpeg_ann = None
        self.record_start_time = None
        self.timer_var.set("00:00:00")
        self.record_btn.config(text="🎥  MULAI REKAM  [R]")
        self.record_status_var.set("READY")
        self.root_status.config(
            text=f"Tersimpan: {os.path.basename(self._video_raw or '')} + _ann.mp4"
        )

    def _update_timer(self):
        if not self.recording or self.record_start_time is None:
            return
        e = int(time.time() - self.record_start_time)
        h, r = divmod(e, 3600)
        m, s = divmod(r, 60)
        self.timer_var.set(f"{h:02d}:{m:02d}:{s:02d}")

    # ──────────────────────────────────────────────────────────────────────
    #  Storage / lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def select_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir)
        if folder and os.access(folder, os.W_OK):
            self.output_dir = folder
            self.folder_var.set(folder)

    def close_app(self):
        if self._roi_editor is not None and not self._roi_editor.confirm_close():
            return
        if self.recording:
            if not messagebox.askyesno("Rekam Aktif", "Sedang merekam. Tutup?"):
                return
        self.yolo.stop()
        self.stop_camera()
        if self._takt:
            self._takt.disconnect()
        if self._ct_recorder:
            try:
                self._ct_recorder.discard()
            except Exception:
                pass
        if self._storage:
            try:
                self._storage.stop()
            except Exception:
                pass
        self._logger.close()
        logger.info("=== CT Monitor STOP ===")
        self.root.destroy()

    def _state_heartbeat_loop(self):
        pending = None
        last_state = None
        while True:
            # tidur 1 detik, atau bangun lebih cepat saat state berubah
            self._state_evt.wait(timeout=1.0)
            self._state_evt.clear()

            tracker = self.yolo._tracker
            active = tracker and tracker.is_cycle_active()
            elapsed  = tracker.cycle_elapsed()     if active else None
            roi_idle = tracker.seconds_since_roi() if active else None

            state = self._live_state
            if state != last_state:
                if state in ("DISCARD", "CAMERA_OFFLINE"):
                    pending = state
                last_state = state

            try:
                if pending and pending != state:
                    if fd_slave_api.send_state(pending, None, None):
                        pending = None
                ok = fd_slave_api.send_state(state, elapsed, roi_idle)   # selalu bawa roi_idle
                if ok and pending == state:
                    pending = None
            except Exception as e:
                logger.warning(f"[Heartbeat] gagal: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CT Monitor")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--operator", default=None) 
    parser.add_argument("--takt-time", type=float, default=20.0)
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])

    args = parser.parse_args()
    args.station_no = 0
    args.station = 0
    args.line    = ""

    setup_logging(args.log_level)
    args.endpoint, _src = resolve_endpoint(args.endpoint)
    logger.info(f"[Server] Target master: {args.endpoint} (sumber: {_src})")

    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    style = ttk.Style()
    style.configure("Selected.TFrame", background="#0055aa")

    app = CTMonitorApp(root, args)
    root.mainloop()