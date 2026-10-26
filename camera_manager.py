"""
camera_manager.py
=================
Mengelola semua kamera unik yang terdaftar di file XML ROI config.
Setiap kamera berjalan di thread terpisah untuk pembacaan frame non-blocking.

Cara pakai:
    manager = CameraManager([
        "config/flag_cam1.xml",
        "config/filter_cam1.xml",
        "config/flag_cam2.xml",
    ])
    manager.start()

    frame = manager.get_frame("cam_1")
    frame = manager.get_frame("cam_2")

    manager.stop()

Mapping camera_id → source dibaca otomatis dari XML:
    camera_id="cam_1" camera_index="0" → cv2.VideoCapture(0)
    camera_id="cam_2" camera_index="1" → cv2.VideoCapture(1)
    camera_id="cam_3" camera_index="rtsp://..." → cv2.VideoCapture("rtsp://...")

Notes:
    - camera_index berupa string digit → dikonversi ke int
    - camera_index berupa string non-digit → langsung pakai sebagai URL
    - mirror dibaca dari XML mirror="true"/"false"
    - Jika dua XML punya camera_id sama dengan nilai konsisten → satu thread saja
    - Jika konflik (source atau mirror berbeda) → pakai yang pertama, log warning
    - get_frame() mengembalikan None jika kamera tidak tersedia / belum ada frame
"""

import cv2
import logging
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class _CameraThread(threading.Thread):
    """Thread pembaca frame untuk satu kamera."""

    def __init__(self, camera_id: str, source: str | int, mirror: bool = False):
        super().__init__(name=f"cam-{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.source = source
        self.mirror = mirror

        self._frame: Optional[object] = None  # np.ndarray | None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cap: Optional[cv2.VideoCapture] = None
        self.is_open = False

    # ── public API ────────────────────────────────────────────────────────

    def get_frame(self):
        """Kembalikan salinan frame terbaru (thread-safe). None jika belum ada."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        self._stop_event.set()

    # ── thread loop ───────────────────────────────────────────────────────

    def run(self):
        src = int(self.source) if str(self.source).isdigit() else self.source
        cap = cv2.VideoCapture(src)

        if not cap.isOpened():
            logger.error(
                f"[CameraManager] Gagal membuka kamera '{self.camera_id}' "
                f"(source={self.source!r})"
            )
            return

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

        self._cap = cap
        self.is_open = True
        logger.info(
            f"[CameraManager] '{self.camera_id}' terbuka — "
            f"{w}x{h} @ {fps:.1f}fps (source={self.source!r}, mirror={self.mirror})"
        )

        fail_streak = 0
        MAX_FAILS = 30  # ≈ 1 detik @ 30fps sebelum reconnect

        while not self._stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                fail_streak += 1
                if fail_streak >= MAX_FAILS:
                    logger.warning(
                        f"[CameraManager] '{self.camera_id}' — "
                        f"{fail_streak} frame gagal berturut-turut, "
                        f"coba reconnect..."
                    )
                    cap.release()
                    time.sleep(1.0)
                    cap = cv2.VideoCapture(src)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if not cap.isOpened():
                        logger.error(
                            f"[CameraManager] Reconnect '{self.camera_id}' gagal"
                        )
                        self.is_open = False
                        break
                    self._cap = cap
                    self.is_open = True
                    fail_streak = 0
                    logger.info(
                        f"[CameraManager] '{self.camera_id}' reconnect berhasil"
                    )
                continue

            fail_streak = 0
            if self.mirror:
                frame = cv2.flip(frame, 1)

            with self._lock:
                self._frame = frame

        cap.release()
        self.is_open = False
        logger.info(f"[CameraManager] '{self.camera_id}' thread berhenti")


# ═══════════════════════════════════════════════════════════════════════════════
#  CameraManager — public class
# ═══════════════════════════════════════════════════════════════════════════════


class CameraManager:
    """
    Membuka semua kamera unik dari satu atau lebih file XML ROI config
    dan menyediakan akses frame per camera_id secara thread-safe.

    Parameters
    ----------
    xml_paths : list[str]
        Daftar path ke file XML ROI config (flag maupun filter).
        Duplikat camera_id yang konsisten diabaikan — satu thread saja dibuat.
    """

    def __init__(self, xml_paths: list[str]):
        self._threads: dict[str, _CameraThread] = {}
        self._camera_sources: dict[str, tuple[str, bool]] = {}
        # camera_id → (source, mirror)

        seen: dict[str, tuple[str, bool]] = {}
        for path in xml_paths:
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError as e:
                logger.warning(f"[CameraManager] Skip '{path}' — XML tidak valid: {e}")
                continue

            camera_id = root.get("camera_id")
            source = root.get("camera_index")
            mirror = root.get("mirror", "false") == "true"

            if not camera_id or source is None:
                logger.warning(
                    f"[CameraManager] Skip '{path}' — "
                    f"tidak ada camera_id atau camera_index"
                )
                continue

            if camera_id in seen:
                if seen[camera_id] != (source, mirror):
                    logger.warning(
                        f"[CameraManager] '{camera_id}' konflik antar XML "
                        f"(source/mirror berbeda) — pakai yang pertama ditemukan"
                    )
                continue  # konsisten maupun konflik: skip, satu thread saja

            seen[camera_id] = (source, mirror)
            logger.info(
                f"[CameraManager] Ditemukan '{camera_id}' — "
                f"source={source!r}, mirror={mirror} (dari '{Path(path).name}')"
            )

        if not seen:
            raise ValueError(
                "[CameraManager] Tidak ada camera_id/camera_index valid "
                "dari XML yang diberikan"
            )

        self._camera_sources = seen

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        """Mulai semua thread kamera."""
        for camera_id, (source, mirror) in self._camera_sources.items():
            t = _CameraThread(camera_id, source, mirror=mirror)
            t.start()
            self._threads[camera_id] = t

        # Beri waktu kamera inisialisasi (maks 3 detik)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            ready = [t for t in self._threads.values() if t.is_open or not t.is_alive()]
            if len(ready) == len(self._threads):
                break
            time.sleep(0.05)

        opened = [cid for cid, t in self._threads.items() if t.is_open]
        failed = [cid for cid, t in self._threads.items() if not t.is_open]

        if opened:
            logger.info(f"[CameraManager] Kamera berhasil dibuka: {opened}")
        if failed:
            logger.warning(f"[CameraManager] Kamera GAGAL dibuka: {failed}")

    def stop(self):
        """Hentikan semua thread kamera."""
        for t in self._threads.values():
            t.stop()
        for t in self._threads.values():
            t.join(timeout=3.0)
        logger.info("[CameraManager] Semua kamera dihentikan")

    # ── frame access ──────────────────────────────────────────────────────

    def get_frame(self, camera_id: str):
        """
        Kembalikan frame terbaru untuk camera_id tertentu.
        Return None jika kamera tidak terdaftar atau belum ada frame.
        """
        t = self._threads.get(camera_id)
        if t is None:
            logger.debug(
                f"[CameraManager] get_frame: camera_id '{camera_id}' tidak terdaftar"
            )
            return None
        return t.get_frame()

    # ── introspection ─────────────────────────────────────────────────────

    @property
    def camera_ids(self) -> list[str]:
        """Daftar semua camera_id yang terdaftar."""
        return list(self._camera_sources.keys())

    def is_open(self, camera_id: str) -> bool:
        """Apakah kamera camera_id berhasil dibuka dan masih berjalan?"""
        t = self._threads.get(camera_id)
        return bool(t and t.is_open)

    def status(self) -> dict[str, dict]:
        """Ringkasan status semua kamera. Berguna untuk logging / HUD."""
        result = {}
        for cid, (source, mirror) in self._camera_sources.items():
            t = self._threads.get(cid)
            result[cid] = {
                "source": source,
                "mirror": mirror,
                "open": t.is_open if t else False,
                "alive": t.is_alive() if t else False,
                "has_frame": t.get_frame() is not None if t else False,
            }
        return result

    def __repr__(self) -> str:
        parts = [
            f"{cid}({'open' if t.is_open else 'closed'})"
            for cid, t in self._threads.items()
        ]
        return f"CameraManager([{', '.join(parts)}])"
