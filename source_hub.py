# source_hub.py
"""Capture multi-source (maks. 9 slot), satu thread per source. Tanpa Tk."""
from __future__ import annotations

import logging
import threading
import time

import cv2

logger = logging.getLogger(__name__)
RETRY_S = 2.0
MAX_FAIL = 10


class SourceWorker:
    def __init__(self, slot, kind, device, mirror, req, hub):
        self.slot, self.kind, self.device = slot, kind, str(device)
        self.mirror, self.req, self._hub = bool(mirror), req, hub
        self.key = (kind, self.device, self.mirror, req if kind == "camera" else None)
        self._lock = threading.Lock()
        self._frame = None
        self.size = (0, 0)
        self.lost = False
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True, name=f"src-{slot}")
        self._t.start()

    def _open(self):
        if self.kind == "video":
            cap = cv2.VideoCapture(self.device)
        else:
            idx = int(self.device)
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(idx)
            if cap.isOpened() and self.req:
                w, h, fps = self.req
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                cap.set(cv2.CAP_PROP_FPS, fps)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _sleep(self, s):
        end = time.time() + s
        while self._run and time.time() < end:
            time.sleep(0.05)

    def _loop(self):
        while self._run:
            try:
                cap = self._open()
            except Exception as e:
                logger.error(f"[S{self.slot}] gagal buka {self.device}: {e}")
                cap = None
            if cap is None:
                self.lost = True
                self._sleep(RETRY_S)
                continue
            logger.info(f"[S{self.slot}] terbuka: {self.kind} {self.device}")
            iv = 0.0
            if self.kind == "video":
                fps = cap.get(cv2.CAP_PROP_FPS) or 0
                iv = 1.0 / (fps if 1.0 <= fps <= 240.0 else 25.0)
            nxt, fails = time.perf_counter(), 0
            while self._run:
                if iv:
                    wait = nxt - time.perf_counter()
                    if wait > 0:
                        time.sleep(wait)
                    nxt += iv
                    if time.perf_counter() > nxt + iv:
                        nxt = time.perf_counter()
                ok, f = cap.read()
                if not ok:
                    fails += 1
                    if fails >= MAX_FAIL:
                        break
                    if self.kind == "video":
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop
                        nxt = time.perf_counter()
                    else:
                        time.sleep(0.01)
                    continue
                fails = 0
                self.lost = False
                if self.mirror:
                    f = cv2.flip(f, 1)
                self.size = (f.shape[1], f.shape[0])
                if self._hub.paused:
                    continue
                with self._lock:
                    self._frame = f
                try:
                    self._hub.on_frame(self.slot, f, time.time())
                except Exception as e:
                    logger.error(f"[S{self.slot}] on_frame error: {e}", exc_info=True)
            cap.release()
            if self._run:
                logger.warning(f"[S{self.slot}] putus, coba lagi {RETRY_S:.0f}s")
                self.lost = True
                self._sleep(RETRY_S)

    def frame(self):
        with self._lock:
            return self._frame          # hanya-baca, jangan dimodifikasi

    def stop(self, wait=False):
        self._run = False
        if wait:
            self._t.join(timeout=0.5)


class SourceHub:
    def __init__(self, on_frame):
        self.on_frame = on_frame        # f(slot, frame_bgr, ts) — dipanggil dari thread worker
        self.paused = False
        self._w: dict = {}
        self._lock = threading.Lock()

    def sync(self, sources: dict, mirror: bool, req=None):
        """Samakan worker dengan {slot: SourceDef}. Yang tidak berubah dibiarkan jalan."""
        want = {s: (d.kind, str(d.device), bool(mirror), req if d.kind == "camera" else None)
                for s, d in sources.items()}
        with self._lock:
            for slot in list(self._w):
                if want.get(slot) != self._w[slot].key:
                    self._w.pop(slot).stop()
            for slot, d in sources.items():
                if slot not in self._w:
                    self._w[slot] = SourceWorker(slot, d.kind, d.device, mirror, req, self)

    def slots(self) -> list:
        with self._lock:
            return sorted(self._w)

    def frame(self, slot):
        w = self._w.get(slot)
        return w.frame() if w else None

    def size(self, slot) -> tuple:
        w = self._w.get(slot)
        return w.size if w else (0, 0)

    def sizes(self) -> dict:
        with self._lock:
            return {s: w.size for s, w in self._w.items()}

    def lost(self) -> set:
        with self._lock:
            return {s for s, w in self._w.items() if w.lost}

    def stop_all(self):
        with self._lock:
            ws, self._w = list(self._w.values()), {}
        for w in ws:
            w.stop()
        for w in ws:
            w._t.join(timeout=0.5)