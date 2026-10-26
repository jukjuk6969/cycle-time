"""
size_sync.py
============
Sinkronkan ukuran (MB) semua video di videos/normal dan videos/anomaly ke server
lewat POST {server}/api/video/update-size  {"filename": ..., "size_mb": ...}

Pakai dari main.py:
    import size_sync
    size_sync.start_background(base_url)          # otomatis tiap 5 menit
    size_sync.sync_sizes(base_url, force=True)    # manual (tombol)

Atau CLI:
    python size_sync.py --server http://192.168.137.2:3000 [--force]
"""
import argparse
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import os

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent
VIDEOS_DIR = BASE / "videos"
CACHE_PATH = BASE / "config" / "size_synced.json"
MIN_AGE_SEC = 10          # lewati file yang baru ditulis (masih di-finalize)
NOT_FOUND_RETRY_SEC = 1800  # file yang belum ada barisnya: coba lagi tiap 30 menit

_run_lock = threading.Lock()

SERVER_CFG_PATH = BASE / "config" / "server_config.json"
BUFFER_MAX_AGE_SEC = 86400 * 2   # batas buffer: 48 jam
def default_server() -> str:
    """Baca IP:port master terakhir yang dipakai main.py."""
    try:
        c = json.loads(SERVER_CFG_PATH.read_text(encoding="utf-8"))
        return f"http://{c['host']}:{int(c['port'])}"
    except Exception:
        return "http://192.168.137.2:3000"
def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, CACHE_PATH)
    except Exception as e:
        logger.warning(f"[SizeSync] gagal simpan cache: {e}")


def post_size(base_url: str, filename: str, size_mb: float, timeout: float = 5.0):
    """Return (status, detail). status: 'ok' | 'not_found' | 'failed'."""
    url = f"{base_url.rstrip('/')}/api/video/update-size"
    data = json.dumps({"filename": filename, "size_mb": size_mb}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
            try:
                j = json.loads(body)
                if isinstance(j, dict) and j.get("ok") is False:
                    return "not_found", body[:120]
            except Exception:
                pass
            return "ok", f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return ("not_found" if e.code == 404 else "failed"), f"HTTP {e.code}"
    except Exception as e:
        return "failed", str(e)


def sync_sizes(base_url: str, force: bool = False, on_progress=None) -> dict:
    """Kirim ukuran video <= 48 jam. Entri cache yang lebih tua dihapus permanen.
    force=True: abaikan cache & jeda retry (batas 48 jam untuk cache tetap berlaku)."""
    res = {"total": 0, "ok": 0, "skipped": 0, "not_found": 0, "failed": 0}
    if not _run_lock.acquire(blocking=False):
        return res
    try:
        full = _load_cache()
        before = dict(full)
        now = time.time()

        entries = []                                   # satu kali stat per file
        for f in VIDEOS_DIR.rglob("*.mp4"):
            if f.name.startswith("_tmp_"):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, f))
        entries.sort(key=lambda e: e[0])
        res["total"] = len(entries)

        # Eliminasi: hanya file <= 48 jam yang boleh punya entri
        alive = {f.name for mtime, _, f in entries
                 if now - mtime <= BUFFER_MAX_AGE_SEC}
        full = {k: v for k, v in full.items()
                if (k[3:] if k.startswith("nf:") else k) in alive}

        for i, (mtime, size, f) in enumerate(entries, 1):
            age = now - mtime
            if age < MIN_AGE_SEC:
                res["skipped"] += 1
                continue
            if age > BUFFER_MAX_AGE_SEC and not force:
                res["skipped"] += 1                    # di luar jendela 48 jam
                continue
            size_mb = round(size / 1024 / 1024, 2)
            nf_key = "nf:" + f.name

            if not force:
                if full.get(f.name) == size_mb:
                    res["skipped"] += 1
                    continue
                nf_ts = full.get(nf_key)
                if nf_ts and now - nf_ts < NOT_FOUND_RETRY_SEC:
                    res["skipped"] += 1
                    continue

            status, detail = post_size(base_url, f.name, size_mb)
            in_window = age <= BUFFER_MAX_AGE_SEC
            if status == "ok":
                if in_window:
                    full[f.name] = size_mb
                full.pop(nf_key, None)
                res["ok"] += 1
            elif status == "not_found":
                res["not_found"] += 1
                if in_window:
                    full[nf_key] = now
                logger.info(f"[SizeSync] {f.name}: belum ada di server ({detail})")
            else:
                res["failed"] += 1
                logger.warning(f"[SizeSync] {f.name}: gagal ({detail})")
                if res["failed"] >= 5 and res["ok"] == 0:
                    logger.warning("[SizeSync] server tampaknya tidak terjangkau, berhenti")
                    break
            if on_progress:
                on_progress(i, len(entries))

        if full != before:                             # tulis hanya jika berubah
            _save_cache(full)
        logger.info(f"[SizeSync] selesai: {res} | cache={len(full)} entri")
        return res
    finally:
        _run_lock.release()
_bg_started = False


def start_background(base_url: str, interval: float = 300.0):
    """Sinkron saat start (setelah 15 dtk), lalu berkala."""
    global _bg_started
    if _bg_started:
        return
    _bg_started = True

    def loop():
        time.sleep(15)
        while True:
            try:
                sync_sizes(base_url)
            except Exception as e:
                logger.warning(f"[SizeSync] error: {e}")
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="size-sync").start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    print(sync_sizes(a.server or default_server(), force=a.force))
