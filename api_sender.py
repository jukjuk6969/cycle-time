"""
api_sender.py
=============
Mengirim data cycle time ke Node.js backend (server.js).
Heartbeat/state live dikirim lewat TCP socket di fd_slave_api.py (bukan lewat modul ini).

Kontrak server (lihat server.js):
    POST /api/cycle      → 201 { ok, cycle_id, cycle_count }
        body: {
            station_id       : int   (LOGICAL id, bukan PK)
            cycle_time_sec   : float
            operator_id?     : str
            end_timestamp?   : float
            video?           : {
                filename        : str,
                file_path?      : str,   # server: v.file_path || v.path || ""
                size_mb?        : float,
                cycle_time_sec? : float, # server fallback ke data.cycle_time_sec
                is_anomaly?     : bool,
            }
        }

Fitur:
    - Retry otomatis dengan exponential backoff (HANYA untuk 5xx / timeout / network)
    - 4xx tidak di-retry dan tidak di-buffer (fatal — biasanya station_id salah)
    - Local buffer (SQLite) untuk payload yang gagal karena server down
    - Flush buffer periodik di background thread
    - Non-blocking: pengiriman di thread terpisah
"""

import json
import time
import logging
import sqlite3
import threading
import urllib.request
import urllib.error
from pathlib import Path

from cycle_tracker import CycleResult

logger = logging.getLogger(__name__)

BUFFER_DB_PATH = Path(__file__).parent / "config" / "offline_buffer.db"

# Base URL backend. Semua endpoint diturunkan dari sini.
# Cocokkan dengan `server.listen(PORT)` di server.js.
DEFAULT_BASE_URL = "http://192.168.137.2:3000/api"

MAX_RETRY = 3
RETRY_DELAY_BASE = 2.0      # exponential: 2s, 4s, 8s
FLUSH_INTERVAL = 30.0       # detik antar flush buffer
FLUSH_BATCH = 20            # maksimum record per flush

# Batas ukuran tabel SQLite agar tidak membengkak tanpa batas.
PENDING_MAX_ROWS = 5000                 # data siklus belum terkirim; lewat batas → yang terlama dibuang
DEAD_MAX_ROWS = 1000                    # payload yang ditolak server
DEAD_MAX_AGE_SEC = 30 * 24 * 3600       # simpan 30 hari

# Status yang dianggap "sementara" → retry & buffer.
# Selain ini (khususnya 4xx) → fatal, drop payload.
RETRYABLE_STATUS = {None, 408, 429, 500, 502, 503, 504}


class ApiSender:
    """
    Args:
        base_url  : base URL backend tanpa trailing slash, termasuk prefix /api
                    (mis. "http://192.168.137.1:3000/api")
        timeout   : HTTP timeout dalam detik (untuk JSON; tidak untuk file besar)
        buffer_db : path SQLite untuk offline buffer
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 5.0,
        buffer_db: Path = BUFFER_DB_PATH,
    ):
        self.base_url = base_url.rstrip("/")
        self.cycle_url = f"{self.base_url}/cycle"
        self.timeout = timeout

        self._buffer_db = buffer_db
        self._init_buffer_db()
        self._lock = threading.Lock()

        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="api-flush"
        )
        self._flush_thread.start()

        logger.info(
            f"[ApiSender] base_url={self.base_url} | cycle={self.cycle_url}"
        )

    # ── Public API ────────────────────────────────────────────────────────────
    def send(self, result: CycleResult, video_info: dict = None, extra: dict = None):
        """
        Kirim CycleResult ke /api/cycle (non-blocking, di thread baru).
        """
        if video_info:
            logger.info(
                "[ApiSender] VIDEO_INFO DARI MAIN.PY:\n"
                f"    filename       = {video_info.get('filename')}\n"
                f"    file_path      = {video_info.get('file_path')}\n"
                f"    size_mb        = {video_info.get('size_mb')}\n"
                f"    is_anomaly     = {video_info.get('is_anomaly')}\n"
                f"    anomaly_type   = {video_info.get('anomaly_type')}\n"
            )
            file_path = video_info.get("file_path") or video_info.get("path")
            if file_path:
                try:
                    p = Path(file_path)
                    if p.exists():
                        logger.info(
                            f"[ApiSender] UKURAN FILE AKTUAL: {p.name} = "
                            f"{round(p.stat().st_size / (1024 ** 2), 2)} MB"
                        )
                    else:
                        logger.info(f"[ApiSender] File tidak ada: {p}")
                except Exception as e:
                    logger.error(f"[ApiSender] GAGAL CEK UKURAN FILE: {e}")
        else:
            logger.info("[ApiSender] VIDEO_INFO = None")

        t = threading.Thread(
            target=self._send_with_retry,
            args=(result, video_info, extra),
            daemon=True,
            name=f"send-{result.station_id}-{int(result.end_timestamp)}",
        )
        t.start()

    @staticmethod
    def get_file_size(file_path: Path) -> dict:
        """
        Helper untuk dipakai pemanggil saat menyusun `video_info`.
        Mengembalikan dict dengan size_mb yang siap dipakai di payload cycle.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"[ApiSender] File tidak ditemukan: {file_path}")

        size_bytes = file_path.stat().st_size
        return {
            "path": str(file_path.resolve()),
            "filename": file_path.name,
            "size_bytes": size_bytes,
            "size_kb": round(size_bytes / 1024, 2),
            "size_mb": round(size_bytes / (1024 ** 2), 2),
            "exists": True,
        }
    def set_base_url(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.cycle_url = f"{self.base_url}/cycle"

    @staticmethod
    def _line():
        from fd_slave_api import get_config
        line = get_config().get("line")
        if line:
            return line
        try:
            p = Path(__file__).parent / "config" / "station_identity.json"
            return json.loads(p.read_text(encoding="utf-8")).get("line")
        except Exception:
            return None
        
    # ── Internal send ─────────────────────────────────────────────────────────

    def _send_with_retry(self, result: CycleResult, video_info: dict = None, extra: dict = None):
        for _ in range(30):
            if self._line() is not None:
                break
            time.sleep(2)
        else:
            payload = self._build_payload(result, video_info, extra)
            payload["buffered"] = True
            payload["buffered_at"] = time.time()
            self._buffer_insert(payload)
            return

        payload = self._build_payload(result, video_info, extra)

        # ==========================================================
        # DEBUG: TAMPILKAN PAYLOAD PERSIS YANG AKAN DIKIRIM
        # ==========================================================
        logger.info(
            "[ApiSender] PAYLOAD /api/cycle:\n"
            + json.dumps(payload, indent=2, ensure_ascii=False)
        )

        if "video" in payload:
            logger.info(
                "[ApiSender] VIDEO PAYLOAD:\n"
                f"    filename       = {payload['video'].get('filename')}\n"
                f"    file_path      = {payload['video'].get('file_path')}\n"
                f"    size_mb        = {payload['video'].get('size_mb')}\n"
                f"    cycle_time_sec = {payload['video'].get('cycle_time_sec')}\n"
                f"    is_anomaly     = {payload['video'].get('is_anomaly')}\n"
                f"    anomaly_type   = {payload['video'].get('anomaly_type')}\n"
            )
        else:
            logger.warning(
                "[ApiSender] PAYLOAD TIDAK MEMILIKI FIELD 'video'"
            )

        for attempt in range(1, MAX_RETRY + 1):
            ok, status, body = self._post_json(self.cycle_url, payload)

            if ok:
                logger.info(
                    f"[ApiSender] OK station={result.station_id} "
                    f"ct={result.cycle_time_sec}s | attempt {attempt}"
                )
                return

            if status is not None and 400 <= status < 500 and status not in RETRYABLE_STATUS:
                if status == 400 and body.get("code") == "FUTURE_TS":
                    payload["buffered"] = True
                    payload["buffered_at"] = time.time()
                    self._buffer_insert(payload)          # tunggu jam dikoreksi
                    return
                logger.error(f"[ApiSender] HTTP {status} -> dead")
                with self._lock:
                    with sqlite3.connect(self._buffer_db) as conn:
                        conn.execute(
                            "INSERT INTO dead (payload, reason, created_at) VALUES (?, ?, ?)",
                            (json.dumps(payload), f"HTTP {status}", time.time()),
                        )
                return
            # Retryable (5xx / 408 / 429 / network error)
            delay = RETRY_DELAY_BASE ** attempt
            logger.warning(
                f"[ApiSender] Gagal kirim (attempt {attempt}/{MAX_RETRY}) "
                f"status={status} — retry dalam {delay:.0f}s"
            )
            if attempt < MAX_RETRY:
                time.sleep(delay)

        logger.error("[ApiSender] Semua retry gagal — menyimpan ke buffer lokal.")
        payload["buffered"] = True
        payload["buffered_at"] = time.time()      # jam FD saat masuk buffer
        self._buffer_insert(payload)

    def _post_json(self, url, payload):
        """Return (success, status|None, body_dict)."""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json",
                        "Content-Length": str(len(data))},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status in (200, 201, 204), resp.status, {}
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8", "ignore"))
            except Exception:
                body = {}
            return False, e.code, body
        except Exception as e:
            logger.debug(f"[ApiSender] POST {url} error: {e}")
            return False, None, {}

    @staticmethod
    def _build_payload(result, video_info, extra=None):
        payload = {
            "line": ApiSender._line(),
            "station_id": result.station_id,
            "cycle_time_sec": result.cycle_time_sec,
            "operator_id": result.operator_id,
            "start_timestamp": result.start_timestamp,
            "end_timestamp": result.end_timestamp,
            "is_complete": True,
            "anomaly_type": getattr(result, "anomaly_type", "OK"),
            "label": getattr(result, "label", ""),
            "is_genesis": getattr(result, "is_genesis", False),
        }
        if extra:
            payload.update(extra)
        if video_info and video_info.get("filename"):
            payload["video"] = {
                "filename": video_info["filename"],
                "file_path": video_info.get("file_path", ""),
                "size_mb": video_info.get("size_mb"),
                "cycle_time_sec": video_info.get("cycle_time_sec", result.cycle_time_sec),
                "is_anomaly": bool(video_info.get("is_anomaly", result.anomaly)),
                "anomaly_type": video_info.get("anomaly_type", getattr(result, "anomaly_type", "OK")),
                "label": video_info.get("label", ""),
                "is_genesis": video_info.get("is_genesis", False),
                "anomaly_label": video_info.get("anomaly_label", ""),
                "skipped_expected": video_info.get("skipped_expected", []),
                "recorded_at": video_info.get("recorded_at"),
                "recorded_ts": video_info.get("recorded_ts"),
            }
            logger.info(
                f"[ApiSender] Video routing: {video_info['filename']} "
                f"| station={result.station_id} "
                f"| anomaly={payload['video']['is_anomaly']}"
            )
        else:
            logger.debug("[ApiSender] No video attached to this cycle") 

        return payload

    # ── Offline buffer (SQLite) ───────────────────────────────────────────────

    def _init_buffer_db(self):
        self._buffer_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._buffer_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload    TEXT    NOT NULL,
                    created_at REAL    NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dead (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload    TEXT    NOT NULL,
                    reason     TEXT,
                    created_at REAL    NOT NULL
                )
            """)
    def _buffer_insert(self, payload: dict):
        with self._lock:
            with sqlite3.connect(self._buffer_db) as conn:
                conn.execute(
                    "INSERT INTO pending (payload, created_at) VALUES (?, ?)",
                    (json.dumps(payload), time.time()),
                )

    def _buffer_delete(self, row_id: int):
        with self._lock:
            with sqlite3.connect(self._buffer_db) as conn:
                conn.execute("DELETE FROM pending WHERE id = ?", (row_id,))

    def _flush_loop(self):
        while True:
            time.sleep(FLUSH_INTERVAL)
            self._flush_buffer()
            self._prune_buffer()

    def _prune_buffer(self):
        """Batasi ukuran tabel pending & dead."""
        try:
            with self._lock:
                with sqlite3.connect(self._buffer_db) as conn:
                    n = conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
                    if n > PENDING_MAX_ROWS:
                        drop = n - PENDING_MAX_ROWS
                        conn.execute(
                            "DELETE FROM pending WHERE id IN "
                            "(SELECT id FROM pending ORDER BY id LIMIT ?)", (drop,))
                        logger.error(
                            f"[ApiSender] Buffer pending melewati {PENDING_MAX_ROWS}: "
                            f"{drop} record TERLAMA dibuang (server down terlalu lama)")
                    conn.execute("DELETE FROM dead WHERE created_at < ?",
                                 (time.time() - DEAD_MAX_AGE_SEC,))
                    conn.execute(
                        "DELETE FROM dead WHERE id NOT IN "
                        "(SELECT id FROM dead ORDER BY id DESC LIMIT ?)", (DEAD_MAX_ROWS,))
        except Exception as e:
            logger.error(f"[ApiSender] Prune buffer gagal: {e}")

    def _buffer_kill(self, row_id: int, payload_str: str, reason: str):
        """Pindahkan ke tabel dead (bisa dipulihkan), bukan hapus."""
        with self._lock:
            with sqlite3.connect(self._buffer_db) as conn:
                conn.execute(
                    "INSERT INTO dead (payload, reason, created_at) VALUES (?, ?, ?)",
                    (payload_str, reason, time.time()),
                )
                conn.execute("DELETE FROM pending WHERE id = ?", (row_id,))

    def _flush_buffer(self):
        last_id = 0
        while True:
            with self._lock:
                try:
                    with sqlite3.connect(self._buffer_db) as conn:
                        rows = conn.execute(
                            "SELECT id, payload FROM pending WHERE id > ? ORDER BY id LIMIT ?",
                            (last_id, FLUSH_BATCH),
                        ).fetchall()
                except Exception as e:
                    logger.error(f"[ApiSender] Buffer read error: {e}")
                    return
            if not rows:
                return

            for row_id, payload_str in rows:
                last_id = row_id
                try:
                    payload = json.loads(payload_str)
                except Exception as e:
                    logger.error(f"[ApiSender] Payload rusak id={row_id}: {e}")
                    self._buffer_kill(row_id, payload_str, f"json rusak: {e}")
                    continue

                # isi identitas yang kosong saat payload dibuffer
                if payload.get("line") is None:
                    line = self._line()
                    if line is None:
                        logger.warning("[ApiSender] Identitas line belum ada, flush ditunda")
                        return
                    payload["line"] = line
                    if isinstance(payload.get("video"), dict):
                        pass  # video tidak membawa line, tidak perlu diubah

                payload["synced_at"] = time.time()
                ok, status, body = self._post_json(self.cycle_url, payload)

                if ok:
                    self._buffer_delete(row_id)
                    logger.info(f"[ApiSender] Buffer flush OK: id={row_id}")
                    continue

                if status is not None and 400 <= status < 500 and status not in RETRYABLE_STATUS:
                    # 400 FUTURE_TS: jam FD maju, bisa berhasil nanti -> tahan, jangan buang
                    if status == 400 and body.get("code") == "FUTURE_TS":
                        logger.warning(f"[ApiSender] id={row_id} ditahan: end_timestamp > jam server")
                        continue
                    logger.error(f"[ApiSender] Buffer id={row_id} ditolak HTTP {status} -> dead")
                    self._buffer_kill(row_id, payload_str, f"HTTP {status}")
                    continue

                logger.warning(
                    f"[ApiSender] Server belum pulih (status={status}), "
                    f"id={row_id} tetap di buffer"
                )
                return                                # server masih down: berhenti, coba 30 dtk lagi