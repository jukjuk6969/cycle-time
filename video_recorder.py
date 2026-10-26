"""
video_recorder.py
=================
Merekam video per cycle time.

Kategori hasil rekaman (dua dimensi independen):
  label        : ""  (normal)  | "GENESIS"
  anomaly_type : "OK" | "LONG" | "SHORT"

Penamaan file:
  Normal            : l{line}_st{station}_{ts}_{ct}s.mp4
  Normal + Genesis  : l{line}_st{station}_{ts}_{ct}s_GENESIS.mp4
  Long              : l{line}_st{station}_{ts}_{ct}s_LONG_ANOMALY.mp4
  Short             : l{line}_st{station}_{ts}_{ct}s_SHORT_ANOMALY.mp4
  Long + Genesis    : ..._GENESIS_LONG_ANOMALY.mp4
  Short + Genesis   : ..._GENESIS_SHORT_ANOMALY.mp4

Folder:
  videos/normal/   -> anomaly_type == OK
  videos/anomaly/  -> LONG / SHORT (tidak pernah dihapus otomatis)

Alur cycle diatur ROITracker di main.py:
  menunggu ROI 1 -> GENESIS -> (ROI terakhir) -> cycle normal -> (ROI terakhir) -> ...
  discard / timeout / kamera ditutup -> kembali menunggu ROI 1
  Di ROI terakhir, stop_cycle() dan start_cycle() dipanggil dengan timestamp yang sama.
"""
import json as _json
import logging
import queue
import shutil
import subprocess
import threading
import time
import urllib.request as _ur
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional
from storage_manager import StorageManager, TMP_DIR
import re
logger = logging.getLogger(__name__)


# ── ffmpeg / ffprobe ──────────────────────────────────────────────────────────

def _find_ffmpeg() -> str:
    here = Path(__file__).parent
    for p in (here / "ffmpeg.exe", here / "bin" / "ffmpeg.exe",
              Path(r"C:\ProgramData\chocolatey\bin\ffmpeg.exe")):
        if p.exists():
            return str(p.resolve())
    found = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError("ffmpeg.exe tidak ditemukan.")


def _find_ffprobe(ffmpeg: str) -> str:
    p = Path(ffmpeg).with_name("ffprobe.exe")
    if p.exists():
        return str(p)
    return shutil.which("ffprobe.exe") or shutil.which("ffprobe") or ""


_FFMPEG = _find_ffmpeg()
_FFPROBE = _find_ffprobe(_FFMPEG)


def _probe_duration(path: Path) -> float:
    if not _FFPROBE:
        return 0.0
    try:
        r = subprocess.run(
            [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=15)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _fit_duration(src: Path, target: float) -> float:
    """Skala timestamp tanpa re-encode agar durasi file = target.
    Return durasi file hasil ukur sebelum koreksi (0.0 jika ffprobe tidak ada)."""
    actual = _probe_duration(src)
    if actual <= 0 or abs(actual - target) <= 0.04:
        return actual
    tmp = src.with_name(src.stem + "_fix.mp4")
    r = subprocess.run(
        [_FFMPEG, "-y", "-itsscale", f"{target / actual:.6f}", "-i", str(src),
         "-c", "copy", "-movflags", "+faststart", str(tmp)],
        capture_output=True, timeout=60)
    if r.returncode == 0 and tmp.exists():
        tmp.replace(src)
        logger.info(f"[VideoRecorder] durasi dikoreksi {actual:.2f}s -> {target:.2f}s")
    else:
        tmp.unlink(missing_ok=True)
    return actual


# ── Folder & konstanta ────────────────────────────────────────────────────────
TMP_DIR.mkdir(parents=True, exist_ok=True)

_FPS = 24
_CODEC = "libx265"
_PRESET = "fast"
_CRF = "27"
_MAXRATE = "1M"
_BUFSIZE = "2M"
_END = object()

ANOMALY_TYPES = ("OK", "LONG", "SHORT")
LABEL_GENESIS = "GENESIS"
def _slug(text: str, maxlen: int = 40) -> str:
    """Aman untuk nama file Windows: hanya huruf/angka, pemisah '-'."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return s[:maxlen].strip("-") or "x"

# ── Session: satu rekaman cycle ───────────────────────────────────────────────

class _Session:
    def __init__(self, cmd, temp_path, start_ts, fps):
        self.cmd = cmd
        self.pipe = None                 # diisi oleh thread sesi
        self.temp_path = temp_path
        self.start_ts = start_ts
        self.fps = fps
        self.queue = queue.Queue(maxsize=600)
        self.aborted = False
        self.failed = False              # ffmpeg gagal start
        self.frames = 0
        self.dropped = 0
        self.padded = 0
        self.skipped = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="vr-writer")
        self.thread.start()

    def _spawn(self) -> bool:
        t0 = time.perf_counter()
        try:
            self.pipe = subprocess.Popen(
                self.cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.failed = True
            logger.error(f"[VideoRecorder] ffmpeg gagal start: {e}")
            return False
        logger.info(
            f"[VideoRecorder] ffmpeg siap {(time.perf_counter() - t0) * 1000:.0f}ms "
            f"| antre={self.queue.qsize()} frame")
        return True

    def _drain(self):
        """ffmpeg gagal: kosongkan queue agar stop_cycle tidak memblokir."""
        while not self.aborted:
            item = self.queue.get()
            if item is None or item[0] is _END:
                break

    def _write(self, frame) -> bool:
        if self.pipe is None:
            return False
        try:
            self.pipe.stdin.write(frame.tobytes())
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def _run(self):
        if not self._spawn():
            self._drain()
            return
        fd = 1.0 / self.fps
        n, last = 0, None
        while not self.aborted:
            item = self.queue.get()
            if item is None:
                break
            if item[0] is _END:
                target = int(round((item[1] - self.start_ts) / fd))
                while last is not None and n < target and self._write(last):
                    n += 1
                    self.padded += 1
                break
            frame, ts = item
            idx = max(0, int(round((ts - self.start_ts) / fd)))
            if last is None:
                last = frame
            while n < idx:
                if not self._write(last):
                    break
                n += 1
                self.padded += 1
            if n == idx:
                if self._write(frame):
                    n += 1
            else:
                self.skipped += 1
            last = frame
        self.frames = n

    def close(self):
        self.thread.join(timeout=60)
        if self.pipe is None:
            return
        try:
            self.pipe.stdin.close()
        except Exception:
            pass
        try:
            self.pipe.wait(timeout=60)
        except Exception:
            self.pipe.kill()

# ── VideoRecorder ─────────────────────────────────────────────────────────────

class VideoRecorder:
    def __init__(self, station_id, line, frame_width, frame_height,
                 fps=_FPS, codec=_CODEC):
        self.station_id = station_id
        self.line = line
        self._prefix = f"l{line}_st{station_id}"
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.fps = fps
        self.codec = codec
        self._cur: Optional[_Session] = None
        self._preroll = deque(maxlen=30)
        self._pre_lock = threading.Lock()
        self._lock = threading.Lock()

    def configure(self, frame_width=None, frame_height=None, fps=None, codec=None):
        with self._lock:
            if self._cur is not None:
                logger.warning("[VideoRecorder] configure diabaikan: sedang merekam")
                return False
            if frame_width:
                self.frame_width = frame_width
            if frame_height:
                self.frame_height = frame_height
            if fps:
                self.fps = fps
            if codec:
                self.codec = codec
            with self._pre_lock:
                self._preroll.clear()
            return True

    @property
    def recording(self) -> bool:
        return self._cur is not None

    def start_cycle(self, start_ts: Optional[float] = None):
        start_ts = start_ts or time.time()
        with self._lock:
            if self._cur is not None:                  # pengaman
                old, self._cur = self._cur, None
                threading.Thread(target=self._abort, args=(old,), daemon=True).start()
            tag = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            temp = TMP_DIR / f"_tmp_{self._prefix}_{tag}.mp4"
            extra = ["-tag:v", "hvc1"] if self.codec == "libx265" else []
            cmd = [_FFMPEG, "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
                   "-s", f"{self.frame_width}x{self.frame_height}",
                   "-pix_fmt", "bgr24", "-r", str(self.fps), "-i", "-",
                   "-c:v", self.codec, "-crf", _CRF, "-preset", _PRESET,
                   "-maxrate", _MAXRATE, "-bufsize", _BUFSIZE,
                   "-pix_fmt", "yuv420p", *extra, "-movflags", "+faststart",
                   str(temp)]
            s = _Session(cmd, temp, start_ts, self.fps)
            with self._pre_lock:
                for f, ts in self._preroll:
                    if ts >= start_ts:
                        s.queue.put_nowait((f, ts))
                self._preroll.clear()
                self._cur = s
        logger.info(f"[VideoRecorder] START | {self._prefix} | {self.fps}fps {self.codec}")

    def write_frame(self, frame, ts: Optional[float] = None):
        ts = ts or time.time()
        with self._pre_lock:
            s = self._cur
            if s is None:
                self._preroll.append((frame, ts))
                return
        try:
            s.queue.put_nowait((frame, ts))
        except queue.Full:
            s.dropped += 1

    def stop_cycle(self, cycle_time_sec, takt_time=None, is_anomaly=False,
                   update_url="", anomaly_type="", end_ts: Optional[float] = None,
                   label="", skipped=None, anomaly_label=""):
        """
        anomaly_type : "OK" | "LONG" | "SHORT"  (sumber kebenaran status anomali)
        label        : "" | "GENESIS"
        is_anomaly   : dipertahankan untuk kompatibilitas; hanya dipakai jika
                       anomaly_type kosong (True -> LONG).
        skipped      : [(roi_id, keterangan)] ROI 'expected' yang tidak terjadi → anomali
        anomaly_label: teks lengkap, mis. "Anomaly+expected+LCD" (hanya dicatat di info)
        """
        end_ts = end_ts or time.time()
        with self._lock:
            s, self._cur = self._cur, None
        if s is None:
            logger.warning("[VideoRecorder] stop_cycle() tanpa rekaman aktif")
            return None
        try:
            s.queue.put((_END, end_ts), timeout=2)
        except queue.Full:
            s.aborted = True

        atype = (anomaly_type or ("LONG" if is_anomaly else "OK")).upper()
        if atype not in ANOMALY_TYPES:
            logger.warning(f"[VideoRecorder] anomaly_type tidak dikenal: {atype} -> LONG")
            atype = "LONG"
        skipped = list(skipped or [])
        anomaly = atype != "OK" or bool(skipped)
        label = (label or "").upper()

        now = datetime.now()
        ts_name = now.strftime("%Y%m%d_%H%M%S")
        # milidetik di nama file agar dua cycle di detik yang sama tidak bentrok
        ts_file = f"{ts_name}{now.microsecond // 1000:03d}"
        ct_str = f"{cycle_time_sec:.1f}s".replace(".", "_")

        parts = [self._prefix, ts_file, ct_str]
        if label:
            parts.append(label)
        if atype != "OK":
            parts.append(atype)
        if skipped:
            parts += ["EXPECTED", _slug("-".join(d for _, d in skipped))]
        if anomaly:
            parts.append("ANOMALY")
        filename = "_".join(parts) + ".mp4"
        final_path = StorageManager.get_save_dir(anomaly, now) / filename

        info = {
            "filename": filename,
            "path": str(final_path),
            "file_path": str(final_path),
            "relative_path": StorageManager.to_relative(final_path),
            "station_id": self.station_id,
            "line": self.line,
            "cycle_time_sec": cycle_time_sec,
            "takt_time": takt_time,
            "label": label,
            "is_genesis": label == LABEL_GENESIS,
            "is_anomaly": anomaly,
            "anomaly_type": atype,
            "skipped_expected": [{"id": i, "description": d} for i, d in skipped],
            "anomaly_label": anomaly_label,
            "recorded_at": ts_name,
            "recorded_ts": end_ts,          # epoch FD, bebas masalah zona waktu
            "size_mb": None,
            "size_ready": False,
            "update_url": update_url,
        }
        threading.Thread(
            target=self._finalize,
            args=(s, final_path, info),
            daemon=True,
            name="vr-finalize"
        ).start()

        return info

    def discard(self):
        with self._lock:
            s, self._cur = self._cur, None
        if s:
            threading.Thread(target=self._abort, args=(s,), daemon=True).start()

    # ── Background ────────────────────────────────────────────────────────────
    def _finalize(self, s: _Session, dst: Path, info: dict):
        try:
            # 1. Tutup session / tunggu ffmpeg selesai
            s.close()

            if not s.temp_path.exists():
                logger.error("[VideoRecorder] File temp tidak ditemukan | %s", s.temp_path)
                return

            # 2. Koreksi durasi tanpa re-encode
            raw_dur = _fit_duration(s.temp_path, info["cycle_time_sec"])

            # 3. Pindah temp -> final (move aman lintas folder/drive)
            try:
                shutil.move(str(s.temp_path), str(dst))
            except Exception as e:
                logger.error(
                    "[VideoRecorder] Move error | src=%s | dst=%s | error=%s",
                    s.temp_path, dst, e)
                return

            # 4. Ukuran FINAL
            size_bytes = dst.stat().st_size
            size_mb = round(size_bytes / 1024 / 1024, 2)
            info["size_bytes"] = size_bytes
            info["size_mb"] = size_mb
            info["size_ready"] = True

            logger.info(
                "[VideoRecorder] SIZE READY | filename=%s | bytes=%d | mb=%.2f",
                dst.name, size_bytes, size_mb)

            logger.info(
                "[VideoRecorder] %s | label=%s | type=%s | "
                "file(sebelum koreksi)=%.2fs | CT=%.2fs | frames=%d | "
                "dropped=%d | padded=%d | skipped=%d | size=%.2fMB",
                dst.name, info.get("label") or "-", info["anomaly_type"],
                raw_dur, info["cycle_time_sec"], s.frames,
                s.dropped, s.padded, s.skipped, size_mb)

            # 5. Update ukuran file ke server
            url = info.get("update_url", "")
            if not url:
                logger.warning(
                    "[VideoRecorder] SIZE UPDATE dilewati: update_url kosong | filename=%s",
                    dst.name)
                return

            payload = {
                "filename": dst.name,
                "size_mb": size_mb,
                "size_bytes": size_bytes,
            }
            logger.info(
                "[VideoRecorder] SIZE UPDATE -> %s | filename=%s | size_mb=%.2f | size_bytes=%d",
                url, dst.name, size_mb, size_bytes)

            req = _ur.Request(
                url,
                data=_json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with _ur.urlopen(req, timeout=3) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    logger.info(
                        "[VideoRecorder] SIZE UPDATE RESPONSE | filename=%s | status=%s | body=%s",
                        dst.name, response.status, body)
            except _ur.HTTPError as he:
                if he.code == 404:
                    logger.info(
                        "[VideoRecorder] SIZE UPDATE: baris video belum ada di server, "
                        "size_sync akan menyusul | filename=%s", dst.name)
                else:
                    raise
            except (_ur.URLError, TimeoutError, OSError) as ne:
                logger.warning(
                    "[VideoRecorder] SIZE UPDATE ditunda, server tidak terjangkau "
                    "(size_sync akan menyusul) | filename=%s | %s", dst.name, ne)

        except Exception as ex:
            logger.exception(
                "[VideoRecorder] Finalize error | filename=%s | error=%s",
                dst.name if dst else "?", ex)

    def _abort(self, s: _Session):
        s.aborted = True
        try:
            s.queue.put(None, timeout=2)
        except queue.Full:
            pass
        s.close()
        try:
            s.temp_path.unlink()
        except Exception:
            pass