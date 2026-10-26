"""
hand_detector.py
================
Vision Layer — YOLO-based hand/object detector.

Menggantikan implementasi MediaPipe sebelumnya.
Titik deteksi: center bounding box (cx, cy), menggantikan palm landmark index 9.

Tugas:
- Deteksi objek menggunakan YOLO (ultralytics)
- Menentukan ROI mana yang aktif berdasarkan center bbox
- Tracking urutan ROI (sequence) via ROITracker
- Menghasilkan HandState untuk dikonsumsi main.py

TIDAK bertugas untuk:
- validasi dwell
- workflow / cycle logic
- video recording
- pengiriman data ke backend

Output:
    HandState
        .hand_detected      : bool
        .hands              : list[PerHandState]
        .any_active_rois    : list[str]  — label ROI yang sedang aktif
        .active_roi         : str | None — ROI aktif pertama
"""

import queue
import threading
import time
import logging

import cv2

from dataclasses import dataclass, field
from typing import List, Optional

try:
    from ultralytics import YOLO as _YOLO

    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    _YOLO = None

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Warna per deteksi (index berputar)
_PALETTE = [
    (0, 200, 255),
    (0, 255, 100),
    (255, 80, 80),
    (255, 200, 0),
    (180, 0, 255),
    (0, 180, 180),
    (255, 140, 0),
    (100, 255, 0),
]

# Warna ROI
_COLOR_ROI_ACTIVE = (0, 220, 120)  # hijau — ROI sedang disentuh
_COLOR_ROI_INACTIVE = (100, 100, 100)  # abu   — ROI idle
_COLOR_ROI_NEXT = (0, 200, 255)  # biru  — ROI yang harus dituju berikutnya
_COLOR_TEXT = (255, 255, 255)

# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class PerHandState:
    """State satu objek terdeteksi (menggantikan per-hand MediaPipe)."""

    hand_id: int = 0
    palm_x: float = 0.0  # cx ternormalisasi 0–1 terhadap lebar frame
    palm_y: float = 0.0  # cy ternormalisasi 0–1 terhadap tinggi frame
    confidence: float = 0.0
    moving: bool = False
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    label: str = ""  # class name YOLO
    bbox: tuple = field(default_factory=tuple)  # (x1, y1, x2, y2) pixel


@dataclass
class HandState:
    """
    State agregat semua deteksi dalam satu frame.
    Kompatibel dengan main.py yang sebelumnya menggunakan MediaPipe.
    """

    hand_detected: bool = False
    hands: List[PerHandState] = field(default_factory=list)
    any_active_rois: List[str] = field(default_factory=list)
    active_roi: Optional[str] = None


# =============================================================================
# ROITracker — sequence logic
# =============================================================================


# AFTER


class ROITracker:
    def __init__(self, rois: list, classes: list):
        self._rois = rois
        self._classes = classes
        self._current_idx = 0
        self._in_roi = False
        self._cycle_count = 0
        self._visited: list = []
        self._cycle_start_time: Optional[float] = None
        self._pending_start = False  # ← tambah
        self.on_cycle_start: Optional[callable] = None
        self.on_cycle_complete: Optional[callable] = None

    @property
    def current_roi_idx(self) -> int:
        return self._current_idx

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    def reset(self):
        self._cycle_count = 0
        self._current_idx = 0
        self._in_roi = False
        self._visited = []
        self._cycle_start_time = None
        self._pending_start = False  # ← tambah

    def update(self, cx: int, cy: int, label: str) -> tuple[bool, int, Optional[float]]:
        if not self._rois:
            return False, 0, None

        # ── Fire pending start dari cycle sebelumnya ──────────────────────── ← tambah
        if self._pending_start:  # ← tambah
            self._pending_start = False  # ← tambah
            if self.on_cycle_start:  # ← tambah
                self.on_cycle_start()  # ← tambah

        target = self._rois[self._current_idx]
        expected_cls = self._classes[self._current_idx] if self._classes else set()
        inside = self._point_in_roi(cx, cy, target)
        class_match = (not expected_cls) or (label in expected_cls)

        cycle_done = False
        cycle_time = None

        if inside and class_match and not self._in_roi:
            self._in_roi = True
            now = time.time()

            if self._current_idx == 0:
                if self._cycle_start_time is not None and len(self._visited) > 0:
                    cycle_time = now - self._cycle_start_time
                    self._cycle_count += 1
                    self._visited = []
                    cycle_done = True

                    if self.on_cycle_complete:
                        end_ts = now
                        start_ts = end_ts - cycle_time
                        self.on_cycle_complete(
                            self._cycle_count, start_ts, end_ts, cycle_time
                        )

                    self._pending_start = True  # ← ganti: defer on_cycle_start

                else:
                    if self.on_cycle_start:  # ← pindah: hanya cycle pertama
                        self.on_cycle_start()

                self._cycle_start_time = now

            else:
                self._visited.append(self._current_idx)

            self._current_idx = (self._current_idx + 1) % len(self._rois)

        elif not (inside and class_match):
            self._in_roi = False

        return cycle_done, self._current_idx, cycle_time

    def get_active_roi_label(self, roi_labels: list) -> Optional[str]:
        """
        Kembalikan label ROI yang sedang 'next' (dituju berikutnya).
        roi_labels : list label string paralel dengan self._rois.
        """
        if not roi_labels or self._current_idx >= len(roi_labels):
            return None
        return roi_labels[self._current_idx]

    @staticmethod
    def _point_in_roi(cx: int, cy: int, roi: tuple) -> bool:
        x1, y1, x2, y2 = roi
        return x1 <= cx <= x2 and y1 <= cy <= y2


# =============================================================================
# HandDetector — YOLO-based (menggantikan MediaPipe)
# =============================================================================


class HandDetector:
    """
    Vision layer berbasis YOLO untuk production CT Monitor.

    Menggantikan HandDetector MediaPipe sebelumnya.
    Interface publik dipertahankan agar main.py tidak banyak berubah.

    Args:
        roi_pool    : dict { label: { "pixel": (x1,y1,x2,y2) } }
                      Sama dengan format yang dipakai main.py.
        model_path  : path ke file .pt YOLO.
        conf        : confidence threshold (default 0.25).
        max_hands   : maksimum objek yang diproses per frame.
                      Nama dipertahankan agar kompatibel dengan main.py.
        tracked_classes : set class YOLO yang diterima. Kosong = semua.
        on_cycle_start    : callback saat cycle mulai.
        on_cycle_complete : callback saat cycle selesai.
                            Signature: (cycle_count, start_ts, end_ts, cycle_time)
    """

    _DEFAULT_CONF = 0.7  # fallback jika ROI tidak punya conf

    def __init__(
        self,
        roi_pool: dict,
        model_path: str = "",
        conf: float | dict = 0.25,
        max_hands: int = 2,
        tracked_classes: set = None,
        on_cycle_start: Optional[callable] = None,
        on_cycle_complete: Optional[callable] = None,
    ):
        if not YOLO_AVAILABLE:
            raise ImportError(
                "ultralytics tidak terinstall. Jalankan: pip install ultralytics"
            )

        # Normalisasi conf: float → semua ROI pakai nilai sama
        # dict → per-ROI conf, key = label ROI
        if isinstance(conf, dict):
            self._roi_confs: dict[str, float] = conf
            # Conf global paling rendah dari semua ROI → dipakai sebagai threshold
            # awal YOLO agar tidak ada deteksi yang gugur sebelum di-filter per-ROI
            self._conf = min(conf.values()) if conf else self._DEFAULT_CONF
        else:
            self._roi_confs = {}  # kosong = mode flat, pakai self._conf
            self._conf = float(conf)

        self._max_detections = max_hands
        self._tracked_classes = set(tracked_classes) if tracked_classes else set()
        self._model = None
        self._imgsz = 640

        # Inference thread
        self._input_q = queue.Queue(maxsize=2)
        self._out_lock = threading.Lock()
        self._drawn: dict = {}
        self._last_dets = []
        self._running = False
        self._thread = None

        # Velocity tracking (menggantikan MediaPipe motion)
        self._prev_positions: dict = {}

        # ROI pool dari main.py → konversi ke list untuk ROITracker
        self._roi_pool = roi_pool
        self._roi_labels = list(roi_pool.keys())
        self._roi_coords = [tuple(roi_pool[lbl]["pixel"]) for lbl in self._roi_labels]
        # Semua class diterima untuk semua ROI (production — tidak pakai filter per-ROI)
        self._roi_classes = [set() for _ in self._roi_labels]

        self._tracker = ROITracker(self._roi_coords, self._roi_classes)
        self._tracker.on_cycle_start = on_cycle_start
        self._tracker.on_cycle_complete = on_cycle_complete

        # Load model
        if model_path:
            self._load_model(model_path)

        # Start inference thread
        self._start()

        logger.info(
            f"[HandDetector] YOLO mode | "
            f"ROIs: {self._roi_labels} | "
            f"conf={conf} | "
            f"model={'loaded' if self._model else 'NOT LOADED'}"
        )

    # ── Model ─────────────────────────────────────────────────────────────────

    def _load_model(self, path: str):
        try:
            self._model = _YOLO(path)
            logger.info(f"[HandDetector] Model loaded: {path}")
        except Exception as e:
            logger.error(f"[HandDetector] Gagal load model: {e}")
            self._model = None

    # ── Conf helper ───────────────────────────────────────────────────────────

    def _get_roi_conf(self, roi_label: str) -> float:
        """
        Kembalikan confidence threshold untuk ROI tertentu.
        Jika conf di-pass sebagai float (bukan dict), semua ROI pakai self._conf.
        """
        if self._roi_confs:
            return self._roi_confs.get(roi_label, self._DEFAULT_CONF)
        return self._conf

    def load_model(self, path: str) -> bool:
        """Load atau ganti model saat runtime."""
        self._load_model(path)
        return self._model is not None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._inference_loop, daemon=True, name="yolo-detector"
        )
        self._thread.start()

    def close(self):
        """Hentikan inference thread. Kompatibel dengan main.py yang memanggil detector.close()."""
        self._running = False
        try:
            self._input_q.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("[HandDetector] Stopped.")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Public API ────────────────────────────────────────────────────────────
    def process(
        self,
        frames_by_camera: dict,
        roi_enabled: bool = True,
    ) -> HandState:
        if not frames_by_camera:
            return HandState()

        for camera_id, frame_bgr in frames_by_camera.items():
            if frame_bgr is None:
                continue
            if self._model is not None:
                try:
                    self._input_q.put_nowait((camera_id, frame_bgr))
                except queue.Full:
                    pass

        with self._out_lock:
            all_dets = list(self._last_dets)
            drawn_map = self._drawn

        if drawn_map:
            for camera_id, frame_bgr in frames_by_camera.items():
                if frame_bgr is not None and camera_id in drawn_map:
                    drawn = drawn_map[camera_id]
                    frame_bgr[:] = drawn[: frame_bgr.shape[0], : frame_bgr.shape[1]]
        else:
            # Model belum loaded atau belum ada hasil inference —
            # gambar ROI langsung ke frame agar tetap terlihat
            if roi_enabled:
                self.draw_rois_only(frames_by_camera)

        primary_frame = next(
            (f for f in frames_by_camera.values() if f is not None), None
        )
        return self._build_hand_state(all_dets, primary_frame, roi_enabled)

    # process_multi dihapus — process() sudah handle dict langsung

    def draw_rois_only(self, frames_by_camera: dict):
        for camera_id, frame_bgr in frames_by_camera.items():
            if frame_bgr is None:
                continue
            h, w = frame_bgr.shape[:2]
            for i, lbl in enumerate(self._roi_labels):
                if self._roi_pool[lbl].get("camera_id") != camera_id:
                    continue  # hanya gambar ROI milik kamera ini
                x1, y1, x2, y2 = self._roi_coords[i]
                is_next = i == self._tracker.current_roi_idx
                color = _COLOR_ROI_NEXT if is_next else _COLOR_ROI_INACTIVE
                cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame_bgr,
                    lbl,
                    (x1 + 4, y1 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                    cv2.LINE_AA,
                )

    def reset_tracker(self):
        """Reset ROITracker — cycle count dan index kembali ke awal."""
        self._tracker.reset()

    @property
    def cycle_count(self) -> int:
        return self._tracker.cycle_count

    def update_roi_pool(self, new_pool: dict):
        """
        Ganti ROI pool saat runtime (reload XML atau apply adjustment mirror).
        Thread-safe — pakai lock inference.
        """
        with self._out_lock:
            self._roi_pool = new_pool
            self._roi_labels = list(new_pool.keys())
            self._roi_coords = [
                tuple(new_pool[lbl]["pixel"]) for lbl in self._roi_labels
            ]
            self._roi_classes = [set() for _ in self._roi_labels]
            self._tracker._rois = self._roi_coords
            self._tracker._classes = self._roi_classes
            self._drawn.clear()
        logger.info(f"[HandDetector] ROI pool updated | {len(new_pool)} ROI")

    def update_confs(self, roi_confs: dict[str, float]):
        """
        Update confidence threshold per ROI saat runtime.
        Tidak menyentuh nilai XML asli.
        Thread-safe — nilai float assignment bersifat atomic di CPython.
        """
        self._roi_confs = roi_confs
        self._conf = min(roi_confs.values()) if roi_confs else self._DEFAULT_CONF
        logger.info(
            f"[HandDetector] Confs updated | "
            f"global_min={self._conf:.3f} | {len(roi_confs)} ROI"
        )

    # ── Inference thread ──────────────────────────────────────────────────────
    def _inference_loop(self):
        while self._running:
            item = self._input_q.get()
            if item is None:
                break
            if self._model is None:
                continue

            camera_id, raw = item  # ← unpack tuple
            try:
                results = self._model.predict(
                    source=raw,
                    conf=self._conf,
                    imgsz=self._imgsz,
                    verbose=False,
                )
                drawn, dets = self._process_results(camera_id, raw.copy(), results)
            except Exception as e:
                logger.warning(f"[HandDetector] Inference error: {e}")
                drawn, dets = raw.copy(), []

            with self._out_lock:
                self._drawn[camera_id] = drawn
                # Reset dulu baru extend — agar tidak akumulasi tak terbatas
                self._last_dets = [
                    d for d in self._last_dets if d.get("camera_id") != camera_id
                ]
                self._last_dets.extend({**d, "camera_id": camera_id} for d in dets)

    def _process_results(self, camera_id: str, frame, results) -> tuple[object, list]:
        """
        Proses output YOLO:
        - Filter class yang tidak dipilih
        - Hitung center bbox (cx, cy) — menggantikan palm landmark index 9
        - Update ROITracker
        - Gambar bbox + ROI ke frame
        """
        dets = []
        h, w = frame.shape[:2]

        for result in results:
            if result.boxes is None:
                continue

            for i, box in enumerate(result.boxes):
                if len(dets) >= self._max_detections:
                    break

                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                label = result.names.get(cls_id, str(cls_id))

                # Filter class
                if self._tracked_classes and label not in self._tracked_classes:
                    continue

                x1, y1, x2, y2 = map(int, box.xyxy[0])

                # Center bbox — menggantikan palm landmark index 9
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                color = _PALETTE[cls_id % len(_PALETTE)]

                # Update ROITracker hanya jika ROI yang dituju milik camera_id ini
                roi_idx = self._tracker._current_idx
                if roi_idx < len(self._roi_labels):
                    roi_label = self._roi_labels[roi_idx]
                    roi_cam = self._roi_pool[roi_label].get("camera_id", "")
                    roi_conf_threshold = self._get_roi_conf(roi_label)
                    if roi_cam == camera_id and conf >= roi_conf_threshold:
                        self._tracker.update(cx, cy, label)
                    elif roi_cam == camera_id and conf < roi_conf_threshold:
                        logger.debug(
                            f"[HandDetector] Det '{label}' conf={conf:.3f} "
                            f"< ROI '{roi_label}' threshold={roi_conf_threshold:.3f} — skip"
                        )
                # Hitung velocity (menggantikan MediaPipe motion compute)
                vx, vy, moving = self._compute_velocity(i, cx / w, cy / h)

                dets.append(
                    {
                        "label": label,
                        "conf": conf,
                        "bbox": (x1, y1, x2, y2),
                        "center": (cx, cy),
                        "palm_x": cx / w,
                        "palm_y": cy / h,
                        "moving": moving,
                        "velocity": (vx, vy),
                        "color": color,
                    }
                )

                # Gambar bbox
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                text = f"{label} {conf:.2f}"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
                cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
                cv2.putText(
                    frame,
                    text,
                    (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    _COLOR_TEXT,
                    1,
                    cv2.LINE_AA,
                )

                # Center dot — menggantikan palm dot MediaPipe
                cv2.circle(frame, (cx, cy), 10, color, -1)
                cv2.circle(frame, (cx, cy), 13, (255, 255, 255), 2)
                cv2.putText(
                    frame,
                    f"D{i + 1}",
                    (cx - 10, cy - 15),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    _COLOR_TEXT,
                    1,
                )
                cv2.putText(
                    frame,
                    f"{conf:.2f}",
                    (cx - 15, cy + 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.50,
                    _COLOR_TEXT,
                    1,
                )

        # Gambar semua ROI
        self._draw_rois(frame, dets, camera_id=camera_id)

        return frame, dets

    # ── ROI drawing ───────────────────────────────────────────────────────────

    def _draw_rois(self, frame, dets: list, camera_id: str = ""):
        active_centers = {(d["center"][0], d["center"][1]) for d in dets}
        for i, (lbl, (x1, y1, x2, y2)) in enumerate(
            zip(self._roi_labels, self._roi_coords)
        ):
            # Cek apakah ada deteksi di dalam ROI ini
            occupied = any(
                x1 <= cx <= x2 and y1 <= cy <= y2 for cx, cy in active_centers
            )
            is_next = i == self._tracker.current_roi_idx

            if occupied:
                color = _COLOR_ROI_ACTIVE
            elif is_next:
                color = _COLOR_ROI_NEXT
            else:
                color = _COLOR_ROI_INACTIVE

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame,
                lbl,
                (x1 + 4, y1 - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )

    # ── HandState builder ─────────────────────────────────────────────────────

    def _build_hand_state(self, dets: list, frame_bgr, roi_enabled: bool) -> HandState:
        """
        Konversi list deteksi YOLO ke HandState.
        palm_x / palm_y = cx/w dan cy/h (menggantikan landmark index 9).
        """
        state = HandState()

        if not dets:
            return state

        state.hand_detected = True
        h, w = frame_bgr.shape[:2] if frame_bgr is not None else (480, 640)

        for i, det in enumerate(dets):
            per = PerHandState(
                hand_id=i,
                palm_x=det["palm_x"],
                palm_y=det["palm_y"],
                confidence=det["conf"],
                moving=det["moving"],
                velocity_x=det["velocity"][0],
                velocity_y=det["velocity"][1],
                label=det["label"],
                bbox=det["bbox"],
            )
            state.hands.append(per)

        # Isi any_active_rois dan active_roi berdasarkan posisi deteksi vs ROI
        if roi_enabled:
            active_rois = []
            for det in dets:
                cx, cy = det["center"]
                for lbl, (x1, y1, x2, y2) in zip(self._roi_labels, self._roi_coords):
                    if x1 <= cx <= x2 and y1 <= cy <= y2:
                        if lbl not in active_rois:
                            active_rois.append(lbl)
            state.any_active_rois = active_rois
            state.active_roi = active_rois[0] if active_rois else None

        return state

    # ── Velocity (menggantikan MediaPipe _compute_velocity) ───────────────────

    def _compute_velocity(
        self, obj_id: int, nx: float, ny: float
    ) -> tuple[float, float, bool]:
        """
        Hitung kecepatan gerak objek antar frame.
        Koordinat input sudah ternormalisasi (0–1).
        """
        if obj_id not in self._prev_positions:
            self._prev_positions[obj_id] = (nx, ny)
            return 0.0, 0.0, False

        px, py = self._prev_positions[obj_id]
        vx = nx - px
        vy = ny - py
        self._prev_positions[obj_id] = (nx, ny)

        speed = abs(vx) + abs(vy)
        moving = speed > 0.003  # threshold sama dengan implementasi MediaPipe

        return vx, vy, moving
