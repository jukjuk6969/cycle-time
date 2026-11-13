"""
fd_slave_api.py
===============
Flask slave API yang dijalankan di tiap Field Device.
Server (master) pull data dari sini — FD tidak pernah inisiasi ke server.

Endpoints:
    GET  /health             → status FD
    GET  /stream             → MJPEG live feed
    GET  /videos             → listing video (normal + anomaly)
    GET  /videos/<type>/<fn> → serve file video (support HTTP Range)
"""
import os
import socket
import json
import time
import logging
import threading
import urllib.request
import urllib.error
import queue
import sqlite3
from pathlib import Path
import select
import cv2
from flask import Flask, Response, jsonify, send_file, abort, request

from storage_manager import StorageManager

logger = logging.getLogger(__name__)

app = Flask(__name__)
_sm = StorageManager()

# Frame terbaru dari main.py — diupdate via push_frame()
_latest_frame = None
_frame_lock = threading.Lock()
_BASE = Path(__file__).parent
_VIDEOS_DIR = _BASE / "videos"
_BUFFER_DB = _BASE / "config" / "offline_buffer.db"
_SETUP_PATH = _BASE / "config" / "setup_state.json"
_setup_lock = threading.Lock()


def _read_saving_file() -> bool:
    try:
        return json.loads(_SETUP_PATH.read_text(encoding="utf-8")).get("saving") is True
    except Exception:
        return False                      # file hilang/rusak -> mode normal


def _write_saving_file(enabled: bool) -> bool:
    """Tulis atomik, field lain (model_path, dll) dipertahankan."""
    with _setup_lock:
        try:
            try:
                d = json.loads(_SETUP_PATH.read_text(encoding="utf-8"))
                if not isinstance(d, dict):
                    d = {}
            except Exception:
                d = {}
            d["saving"] = enabled
            _SETUP_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _SETUP_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
            os.replace(tmp, _SETUP_PATH)
            return True
        except Exception as e:
            logger.error(f"[SlaveAPI] Gagal simpan setup_state.json: {e}")
            return False


_saving = threading.Event()
if _read_saving_file():
    _saving.set()


def is_saving() -> bool:
    return _saving.is_set()



# Info registrasi
_station_config: dict = {}   # diisi dari response server setelah register berhasil
_server_url: str = ""
_slave_port: int = 5001
_HB_PORT = 5002          # port socket di server
_hb_port: int = _HB_PORT
_HB_INTERVAL = 10        # detik, heartbeat berkala (murah di socket)
_hb_queue: "queue.Queue[dict]" = queue.Queue(maxsize=200)

# --- Transport heartbeat/state: "tcp" (default) atau "udp" ---------------------------------
# Hanya heartbeat + state yang boleh lewat UDP (toleran kehilangan paket).
# Data siklus (HTTP) dan video (MJPEG/HTTP) tetap reliable dan TIDAK ikut berubah.
_KEEP = object()                 # penanda "jangan ubah" untuk set_transport()
_UDP_HB_INTERVAL = 5             # detik; lebih rapat dari TCP agar jendela 15s tahan 2 paket hilang
_UDP_REPEAT = 3                  # state penting dikirim berulang (seq sama -> server buang duplikat)
_UDP_CRITICAL = ("DISCARD", "CAMERA_OFFLINE", "COMPLETE")
_transport: str = "tcp"
_udp_hint = None                 # port UDP yang disarankan server (field udp_port saat register)
_udp_port: int = _HB_PORT        # default: nomor sama dengan HB (ruang port TCP dan UDP terpisah)
_udp_seq = 0
_udp_attempt = 0


def _read_setup_value(key, default=None):
    try:
        return json.loads(_SETUP_PATH.read_text(encoding="utf-8")).get(key, default)
    except Exception:
        return default


def _update_setup(values: dict) -> bool:
    """Gabungkan key ke setup_state.json secara atomik (key lain dipertahankan)."""
    with _setup_lock:
        try:
            try:
                d = json.loads(_SETUP_PATH.read_text(encoding="utf-8"))
                if not isinstance(d, dict):
                    d = {}
            except Exception:
                d = {}
            d.update(values)
            _SETUP_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _SETUP_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
            os.replace(tmp, _SETUP_PATH)
            return True
        except Exception as e:
            logger.error(f"[SlaveAPI] Gagal simpan setup_state.json: {e}")
            return False


def _resolve_udp_port() -> int:
    """Prioritas: override lokal (setup_state.json) > saran server > port HB."""
    for cand in (_read_setup_value("hb_udp_port"), _udp_hint):
        try:
            p = int(cand)
            if 1 <= p <= 65535:
                return p
        except (TypeError, ValueError):
            pass
    return _hb_port


def get_transport() -> str:
    return _transport


def get_udp_port() -> int:
    return _udp_port


def get_udp_port_override() -> str:
    v = _read_setup_value("hb_udp_port")
    return "" if v in (None, "") else str(v)


def set_transport(mode, udp_port=_KEEP) -> bool:
    """mode: 'tcp' | 'udp'. udp_port: int = override, None = otomatis, (kosong) = tidak diubah."""
    global _transport, _udp_port
    mode = str(mode or "").strip().lower()
    if mode not in ("tcp", "udp"):
        return False
    values = {"hb_transport": mode}
    if udp_port is not _KEEP:
        if udp_port is not None:
            try:
                udp_port = int(udp_port)
            except (TypeError, ValueError):
                return False
            if not (1 <= udp_port <= 65535):
                return False
        values["hb_udp_port"] = udp_port
    if not _update_setup(values):
        return False
    _transport = mode
    _udp_port = _resolve_udp_port()
    logger.info(f"[SlaveAPI] Transport heartbeat: {mode.upper()}"
                + (f" (port UDP {_udp_port})" if mode == "udp" else ""))
    return True


_t0 = _read_setup_value("hb_transport", "tcp")
_transport = _t0 if _t0 in ("tcp", "udp") else "tcp"
_udp_port = _resolve_udp_port()

def get_config() -> dict:
    if _station_config:
        return _station_config.copy()
    try:
        return json.loads(
            (_BASE / "config" / "station_identity.json").read_text(encoding="utf-8")
        )
    except Exception:
        return {}
# ═══════════════════════════════════════════════════════
#  Interface ke main.py
# ═══════════════════════════════════════════════════════
_frame_id = 0
_latest_layout = None

def push_frame(frame, layout=None):
    """Non-blocking: simpan referensi frame terbaru + metadata layout-nya."""
    global _latest_frame, _frame_id, _latest_layout
    with _frame_lock:
        _latest_frame = frame
        _latest_layout = layout
        _frame_id += 1


_register_thread = None
_flask_started = False

def start(server_url: str, port: int = 5001):
    global _server_url, _slave_port, _flask_started
    _server_url = server_url.rstrip("/")
    _slave_port = port
    if not _flask_started:
        _flask_started = True
        threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False),
            daemon=True, name="fd-slave-api",
        ).start()
        threading.Thread(target=_encoder_loop, daemon=True,
                         name="fd-mjpeg-enc").start()
        logger.info(f"[SlaveAPI] Listening di port {port}")
    _ensure_registration()

def _ensure_registration():
    global _register_thread
    if _register_thread and _register_thread.is_alive():
        return
    _register_thread = threading.Thread(
        target=_register_with_retry, daemon=True, name="fd-register")
    _register_thread.start()


# ═══════════════════════════════════════════════════════
#  Registrasi & Heartbeat ke Server
# ═══════════════════════════════════════════════════════

def _get_local_ip() -> str:
    """Ambil IP lokal yang dipakai untuk reach server."""
    try:
        host = _server_url.split("//")[-1].split(":")[0]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

def _register_with_retry():
    global _station_config, _hb_port, _udp_hint, _udp_port
    payload = {
        "ip":       _get_local_ip(),
        "port":     _slave_port,
        "hostname": socket.gethostname(),
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            payload["saving"] = _saving.is_set()
            data    = json.dumps(payload).encode()
            req     = urllib.request.Request(
                f"{_server_url}/api/fd/register",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                body = json.loads(r.read())
            _station_config = {
                "station_id":      body["station_id"],
                "line":            body["line"],
                "name":            body["name"],
                "takt_time":       body["takt_time"],
                "roi_timeout_sec": body["roi_timeout_sec"],
                "min_cycle_sec":   body.get("min_cycle_sec"),
                "ip":              body.get("ip"),
                "port":            body.get("port"),
                "hostname":        body.get("hostname"),
            }
            try:
                _hb_port = int(body.get("hb_port") or _HB_PORT)
            except (TypeError, ValueError):
                _hb_port = _HB_PORT
            _udp_hint = body.get("udp_port")          # opsional: server boleh menyarankan port UDP
            _udp_port = _resolve_udp_port()
            logger.info(
                f"[SlaveAPI] Registered — L{body['line']} ST{body['station_id']} "
                f"| {body['name']} | takt={body['takt_time']}s"
            )
            _socket_heartbeat_loop()

        except urllib.error.HTTPError as e:
            if e.code == 403:
                # IP tidak terdaftar — admin belum daftarkan FD ini
                body = {}
                try:
                    body = json.loads(e.read())
                except Exception:
                    pass
                logger.error(   
                    f"[SlaveAPI] DITOLAK SERVER (403): "
                    f"{body.get('error', 'IP tidak terdaftar')} — klik Reconnect setelah IP didaftarkan."
                )
                return

            # Error lain (500, 503, dll) — retry
            wait = min(attempt * 5, 60)
            logger.warning(
                f"[SlaveAPI] Register gagal HTTP {e.code} "
                f"(attempt {attempt}) — retry dalam {wait}s"
            )
            time.sleep(wait)

        except Exception as e:
            wait = min(attempt * 5, 60)
            logger.warning(
                f"[SlaveAPI] Register gagal: {e} "
                f"(attempt {attempt}) — retry dalam {wait}s"
            )
            time.sleep(wait)


def _hb_host() -> str:
    return _server_url.split("//")[-1].split(":")[0]


_hb_connected = threading.Event()

def is_hb_connected() -> bool:
    return _hb_connected.is_set()

def _socket_heartbeat_loop():
    """
    Koneksi TCP persisten ke server. Mengirim:
      - hello  : sekali saat connect (identifikasi FD)
      - hb     : tiap _HB_INTERVAL detik
      - state  : dari antrean (push_state / send_state)
    Reconnect otomatis dengan backoff jika putus.
    """
    attempt = 0
    while True:
        if _saving.is_set():
            time.sleep(0.5)
            continue
        if _transport != "tcp":
            _udp_session()
            continue
        sock = None
        stop = None
        try:
            sock = socket.create_connection((_hb_host(), _hb_port), timeout=5)
            sock.settimeout(5)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "SIO_KEEPALIVE_VALS"):      # Windows
                sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10_000, 5_000))
            elif hasattr(socket, "TCP_KEEPIDLE"):          # Linux
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)

            cfg = _station_config
            _hb_send(sock, {
                "type": "hello",
                "station_id": cfg["station_id"],
                "line": cfg["line"],
                "ip": _get_local_ip(),
                "port": _slave_port,
                "hostname": socket.gethostname(),
            })
            stop = threading.Event()
            last_rx = [time.monotonic()]
            _hb_connected.clear()
            threading.Thread(target=_hb_reader, args=(sock, stop, last_rx), daemon=True).start()

            # tunggu ack dari reader (JANGAN recv di sini)
            deadline = time.monotonic() + 5
            while not _hb_connected.is_set():
                if stop.is_set():
                    raise ConnectionError("server menutup/menolak saat hello")
                if time.monotonic() > deadline:
                    raise ConnectionError("ack tidak diterima 5s")
                time.sleep(0.05)

            logger.info(f"[SlaveAPI] Heartbeat socket terhubung ke {_hb_host()}:{_hb_port}")
            attempt = 0

            while not _hb_queue.empty():          # buang state basi
                try: _hb_queue.get_nowait()
                except queue.Empty: break

            last_hb = time.monotonic() - _HB_INTERVAL      # kirim hb pertama segera
            while not stop.is_set():
                if _saving.is_set() or _transport != "tcp":
                    break
                try:
                    _hb_send(sock, _hb_queue.get(timeout=1.0))
                except queue.Empty:
                    pass

                if time.monotonic() - last_rx[0] > 15:
                    raise ConnectionError("server tidak menjawab > 15s")

                mono = time.monotonic()
                if mono - last_hb >= _HB_INTERVAL:
                    _hb_send(sock, {"type": "hb", "station_id": cfg["station_id"],
                                    "line": cfg["line"], "ip": _get_local_ip(),
                                    "port": _slave_port, "ts": time.time()})   # ts tetap jam dinding
                    last_hb = mono
            if _transport != "tcp" and not _saving.is_set():
                logger.info("[SlaveAPI] Transport diganti ke UDP: socket TCP ditutup")
                continue
            if _saving.is_set():              # BARU: keluar tanpa log "putus"
                logger.info("[SlaveAPI] Saving mode: heartbeat socket ditutup")
                continue                      # blok finally menutup socket
            raise ConnectionError("koneksi ditutup server")

        except Exception as e:
            _hb_connected.clear()
            if stop: stop.set()                 # hentikan reader dulu
            if sock:
                try:
                    sock.close()                # tutup SEBELUM tidur
                except Exception:
                    pass
            attempt += 1
            wait = min(attempt * 1, 30)
            logger.warning(f"[SlaveAPI] Heartbeat socket putus: {e} — reconnect dalam {wait}s")
            time.sleep(wait)
        finally:
            _hb_connected.clear()
            if stop: stop.set()
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

def _udp_open():
    """Socket UDP. Family (IPv4/IPv6) mengikuti hasil resolve host server."""
    infos = socket.getaddrinfo(_hb_host(), _udp_port, type=socket.SOCK_DGRAM)
    fam, kind, proto, _canon, addr = infos[0]
    s = socket.socket(fam, kind, proto)
    s.settimeout(1.0)
    s.connect(addr)          # UDP "connected": hanya menerima dari server, send() tanpa alamat
    return s


def _udp_send(sock, msg, repeat=1):
    global _udp_seq
    with _send_lock:
        _udp_seq = (_udp_seq + 1) & 0x7FFFFFFF
        out = dict(msg)
        out["seq"] = _udp_seq
        out["transport"] = "udp"
        data = json.dumps(out, separators=(",", ":")).encode()
        if len(data) > 1200:
            logger.warning("[SlaveAPI] Paket UDP terlalu besar, dibuang")
            return
        for _ in range(repeat):
            sock.send(data)


def _udp_reader(sock, stop, last_rx):
    while not stop.is_set():
        try:
            d = sock.recv(2048)
        except socket.timeout:
            continue
        except ConnectionResetError:         # Windows: ICMP port-unreachable; biarkan timeout ack yang memutus
            time.sleep(0.05)
            continue
        except OSError:
            break
        try:
            msg = json.loads(d)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        last_rx[0] = time.monotonic()
        t = msg.get("type")
        if t == "ping":
            try:
                _udp_send(sock, {"type": "pong", "t": msg.get("t")})
            except OSError:
                pass
        elif t == "ack":
            _hb_connected.set()
        elif t == "error":
            logger.error(f"[HB-UDP] server menolak: {msg.get('error')}")
            break
    stop.set()


def _udp_wait(seconds):
    """Tidur yang terputus bila transport diganti / saving aktif."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and _transport == "udp" and not _saving.is_set():
        time.sleep(0.25)


def _udp_session():
    """
    Satu sesi heartbeat via UDP: hello berulang sampai di-ack, lalu hb berkala + state dari antrean.
    Kembali saat transport diganti / saving aktif / server diam (pemanggil mengulang).
    Server WAJIB membalas {"type":"ack"} untuk hello DAN hb (itu satu-satunya tanda hidup di UDP).
    """
    global _udp_attempt
    cfg = _station_config
    sock = None
    stop = threading.Event()
    try:
        sock = _udp_open()
        last_rx = [time.monotonic()]
        _hb_connected.clear()
        threading.Thread(target=_udp_reader, args=(sock, stop, last_rx),
                         daemon=True, name="fd-udp-rx").start()
        hello = {"type": "hello", "station_id": cfg["station_id"], "line": cfg["line"],
                 "ip": _get_local_ip(), "port": _slave_port, "hostname": socket.gethostname()}
        deadline = time.monotonic() + 5
        while not _hb_connected.is_set():
            if stop.is_set():
                raise ConnectionError("server menolak/menutup saat hello")
            if time.monotonic() > deadline:
                raise ConnectionError(f"ack UDP tidak diterima 5s (port {_udp_port})")
            _udp_send(sock, hello)
            _hb_connected.wait(1.0)

        logger.info(f"[SlaveAPI] Heartbeat UDP terhubung ke {_hb_host()}:{_udp_port} "
                    f"({sock.family.name})")
        _udp_attempt = 0
        while not _hb_queue.empty():              # buang state basi
            try:
                _hb_queue.get_nowait()
            except queue.Empty:
                break

        last_hb = time.monotonic() - _UDP_HB_INTERVAL
        while not stop.is_set():
            if _saving.is_set() or _transport != "udp":
                logger.info("[SlaveAPI] Heartbeat UDP dihentikan (saving / ganti transport)")
                return
            try:
                msg = _hb_queue.get(timeout=1.0)
                crit = msg.get("state") in _UDP_CRITICAL
                _udp_send(sock, msg, _UDP_REPEAT if crit else 1)
            except queue.Empty:
                pass
            if time.monotonic() - last_rx[0] > 15:
                raise ConnectionError("server tidak menjawab > 15s")
            if time.monotonic() - last_hb >= _UDP_HB_INTERVAL:
                _udp_send(sock, {"type": "hb", "station_id": cfg["station_id"],
                                 "line": cfg["line"], "ip": _get_local_ip(),
                                 "port": _slave_port, "ts": time.time()})
                last_hb = time.monotonic()
        raise ConnectionError("socket UDP ditutup")
    except Exception as e:
        _udp_attempt += 1
        wait = min(_udp_attempt, 30)
        logger.warning(f"[SlaveAPI] Heartbeat UDP putus: {e} - reconnect dalam {wait}s")
        _udp_wait(wait)
    finally:
        _hb_connected.clear()
        stop.set()
        if sock:
            try:
                sock.close()
            except Exception:
                pass


_send_lock = threading.Lock()

def _hb_send(sock, msg):
    with _send_lock:
        sock.sendall((json.dumps(msg) + "\n").encode())

def _hb_reader(sock, stop, last_rx):
    buf = b""
    while not stop.is_set():
        try:
            d = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        if not d:
            break
        buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            last_rx[0] = time.monotonic()
            t = msg.get("type")
            if t == "ping":
                _hb_send(sock, {"type": "pong", "t": msg["t"]})
            elif t == "ack":
                _hb_connected.set()
            elif t == "error":
                logger.error(f"[HB] server menolak: {msg.get('error')}")
                break
    stop.set()
# ═══════════════════════════════════════════════════════
#  Flask Endpoints
# ═══════════════════════════════════════════════════════

@app.route("/health")
def health():
    usage = _sm.get_usage()
    return jsonify({
        "online": True,
        "station_id": _station_config.get("station_id"),
        "line": _station_config.get("line"),
        "hostname": socket.gethostname(),
        "stream_ready": _latest_frame is not None,
        **usage,
    })
@app.route("/transport", methods=["GET", "POST"])
def transport():
    if request.method == "GET":
        return jsonify({"transport": _transport, "udp_port": _udp_port,
                        "connected": _hb_connected.is_set()})
    data = request.get_json(silent=True) or {}
    mode = data.get("transport")
    if mode not in ("tcp", "udp"):
        return jsonify({"error": "transport harus 'tcp' atau 'udp'"}), 400
    port = data["udp_port"] if "udp_port" in data else _KEEP
    if port is not _KEEP and port is not None:
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            return jsonify({"error": "udp_port harus integer 1-65535 atau null"}), 400
    if not set_transport(mode, port):
        return jsonify({"error": "gagal menyimpan setup_state.json"}), 500
    return jsonify({"ok": True, "transport": _transport, "udp_port": _udp_port})


@app.route("/saving-mode", methods=["GET", "POST"])
def saving_mode():
    if request.method == "GET":
        return jsonify({"saving": _saving.is_set()})
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled (boolean) wajib ada"}), 400
    if not _write_saving_file(enabled):          # file dulu; gagal = tidak ada yang berubah
        return jsonify({"error": "gagal menyimpan setup_state.json"}), 500
    if enabled:
        _saving.set()                            # loop heartbeat menutup socket setelah balasan ini
    else:
        _saving.clear()                          # loop heartbeat langsung konek + hello
    logger.info(f"[SlaveAPI] Saving mode {'AKTIF' if enabled else 'NONAKTIF'}")
    return jsonify({"ok": True, "saving": enabled})
_MJPEG_FPS = 30          # dari 20
_STREAM_MAX_W = 480      # lebar maksimum stream
_STREAM_QUALITY = 50     # kualitas JPEG

_jpeg = None
_jpeg_id = 0
_jpeg_lid = 0          # layout_id milik JPEG di atas
_jpeg_cond = threading.Condition()
_viewers = 0
_viewers_lock = threading.Lock()


def _encoder_loop():
    """Satu encoder untuk semua penonton; hanya aktif kalau ada penonton."""
    global _jpeg, _jpeg_id, _jpeg_lid
    last_id = -1
    interval = 1.0 / _MJPEG_FPS
    while True:
        if _viewers == 0:
            time.sleep(0.2)
            continue
        t0 = time.perf_counter()
        with _frame_lock:
            frame, fid, layout = _latest_frame, _frame_id, _latest_layout
        if frame is None or fid == last_id:
            time.sleep(0.01)
            continue
        last_id = fid
        h, w = frame.shape[:2]
        if w > _STREAM_MAX_W:
            frame = cv2.resize(
                frame, (_STREAM_MAX_W, int(h * _STREAM_MAX_W / w)),
                interpolation=cv2.INTER_AREA,
            )
        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _STREAM_QUALITY]
        )
        if ok:
            data = buf.tobytes()

            now = time.perf_counter()

            if "_last_stream_encode" not in globals():
                _last_stream_encode = now
            else:
                dt = now - _last_stream_encode

                if dt > 0.15:
                    print(
                        f"[STREAM JITTER] "
                        f"interval={dt * 1000:.0f} ms "
                        f"JPEG={len(data) / 1024:.1f} KB"
                    )

                _last_stream_encode = now

            with _jpeg_cond:
                _jpeg = data
                _jpeg_lid = layout["layout_id"] if layout else 0            # baru
                _jpeg_id += 1
                _jpeg_cond.notify_all()
        rest = interval - (time.perf_counter() - t0)
        if rest > 0:
            time.sleep(rest)


def _generate_mjpeg():
    global _viewers

    with _viewers_lock:
        _viewers += 1

    last = -1

    try:
        while True:
            with _jpeg_cond:
                if not _jpeg_cond.wait_for(
                    lambda: _jpeg_id != last and _jpeg is not None,
                    timeout=2.0
                ):
                    continue

                # Ambil snapshot ketika masih memegang lock
                data = _jpeg
                current_id = _jpeg_id
                lid = _jpeg_lid 

            # Safety check
            if data is None:
                continue

            last = current_id

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"X-Layout-Id: " + str(lid).encode() + b"\r\n"     # baru
                b"Content-Length: " + str(len(data)).encode() +
                b"\r\n\r\n" +
                data +
                b"\r\n"
            )

    except GeneratorExit:
        pass

    finally:
        with _viewers_lock:
            _viewers -= 1


@app.route("/stream")
def stream():
    resp = Response(
        _generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp

# ═══════════════════════════════════════════════════════
#  Stream per source (resolusi asli) — tempel di fd_slave_api.py, setelah route /stream
# ═══════════════════════════════════════════════════════
_SLOT_FPS = 25
_SLOT_QUALITY = 75
_slot_getter = None          # callable(slot) -> frame BGR | None  (diisi main.py: hub.frame)
_slot_lock = threading.Lock()
_slot_streams: dict = {}     # slot -> _SlotStream


def set_slot_source(getter):
    """main.py: fd_slave_api.set_slot_source(self.hub.frame)"""
    global _slot_getter
    _slot_getter = getter


class _SlotStream:
    """Satu encoder per slot, dipakai bersama semua penonton slot itu; berhenti saat tidak ada penonton."""

    def __init__(self, slot):
        self.slot = slot
        self.cond = threading.Condition()
        self.jpeg = None
        self.jid = 0
        self.viewers = 0                       # hanya diubah di bawah _slot_lock
        threading.Thread(target=self._run, daemon=True, name=f"fd-mjpeg-s{slot}").start()

    def _run(self):
        last = None
        interval = 1.0 / _SLOT_FPS
        while True:
            with _slot_lock:
                if self.viewers == 0:
                    _slot_streams.pop(self.slot, None)
                    return
            t0 = time.perf_counter()
            getter = _slot_getter
            frame = getter(self.slot) if getter else None
            if frame is None or frame is last:     # frame baru = objek baru dari worker
                time.sleep(0.01)
                continue
            last = frame
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _SLOT_QUALITY])
            if ok:
                with self.cond:
                    self.jpeg = buf.tobytes()
                    self.jid += 1
                    self.cond.notify_all()
            rest = interval - (time.perf_counter() - t0)
            if rest > 0:
                time.sleep(rest)


def _slot_acquire(slot):
    with _slot_lock:
        st = _slot_streams.get(slot)
        if st is None:
            st = _slot_streams[slot] = _SlotStream(slot)
        st.viewers += 1
        return st


def _slot_release(st):
    with _slot_lock:
        st.viewers -= 1


def _generate_slot_mjpeg(slot):
    st = _slot_acquire(slot)
    last = -1
    try:
        while True:
            with st.cond:
                if not st.cond.wait_for(lambda: st.jid != last and st.jpeg is not None, timeout=2.0):
                    continue
                data, last = st.jpeg, st.jid
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(data)).encode() +
                b"\r\n\r\n" + data + b"\r\n"
            )
    except GeneratorExit:
        pass
    finally:
        _slot_release(st)


@app.route("/stream/<int:slot>")
def stream_slot(slot):
    getter = _slot_getter
    if getter is None or not (0 <= slot <= 99) or getter(slot) is None:
        abort(404)
    resp = Response(
        _generate_slot_mjpeg(slot),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp

@app.route("/layout")
def layout():
    """Layout tile terbaru (untuk hit-test klik di server)."""
    with _frame_lock:
        lay = _latest_layout
    if lay is None:
        return jsonify({"error": "belum ada frame"}), 503
    resp = jsonify(lay)
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/videos")
def list_videos():
    return jsonify({
        "station_id": _station_config.get("station_id"),
        "normal":  _sm.list_videos(anomaly_only=False),
        "anomaly": _sm.list_videos(anomaly_only=True),
    })
def _pending_filenames() -> set:
    """Nama video yang cycle-nya masih antre di offline_buffer.db (menunggu dikirim)."""
    names = set()
    try:
        with sqlite3.connect(f"file:{_BUFFER_DB}?mode=ro", uri=True, timeout=2) as conn:
            for (payload,) in conn.execute("SELECT payload FROM pending"):
                try:
                    fn = (json.loads(payload).get("video") or {}).get("filename")
                    if fn:
                        names.add(fn)
                except Exception:
                    pass
    except Exception:
        pass            # DB belum ada / terkunci → anggap kosong
    return names


@app.route("/videos/list")
def videos_list():
    """Daftar file video fisik di FD, dengan penanda 'pending' (masih antre kirim)."""
    now = time.time()
    pending = _pending_filenames()
    out = []
    _KINDS = {
        "normal":  ["*/*/normal",  "normal"],
        "anomaly": ["*/*/anomali", "anomaly"],
    }

    for vtype, patterns in _KINDS.items():
        for pat in patterns:
            for f in _VIDEOS_DIR.glob(f"{pat}/*.mp4"):
                if f.name.startswith("_tmp_"):
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                if now - st.st_mtime < 10:
                    continue
                out.append({
                    "filename": f.name,
                    "type": vtype,
                    "size_mb": round(st.st_size / 1024 / 1024, 2),
                    "mtime": st.st_mtime,
                    "pending": f.name in pending,
                })
    return jsonify(out)

@app.route("/videos/<vtype>/<filename>", methods=["GET", "DELETE"])
def video_file(vtype: str, filename: str):
    if ".." in filename or "/" in filename or "\\" in filename:
        abort(400)
    if vtype not in ("normal", "anomaly"):
        abort(404)

    if request.method == "DELETE":
        if vtype != "anomaly":
            return jsonify({"error": "Hanya video anomaly yang boleh dihapus"}), 403
        result = _sm.delete_anomaly_by_name(filename)
        if result is None:
            return jsonify({"error": "File tidak ditemukan"}), 404
        if result is False:
            return jsonify({"error": "Gagal menghapus file"}), 500
        logger.info(f"[SlaveAPI] DELETE anomaly: {filename}")
        return jsonify({"ok": True, "deleted": filename})

    path = _sm.find_video(vtype, filename)
    if path is None:
        abort(404)
    return send_file(str(path), mimetype="video/mp4", conditional=True)
# ── tempel di fd_slave_api.py, dekat route /videos/<vtype>/<filename> ──
@app.route("/videos/<vtype>/<filename>/layout")
def video_layout(vtype: str, filename: str):
    """Posisi tiap source (S1..Sn) di dalam video gabungan. 404 bila video lama / tanpa metadata."""
    if ".." in filename or "/" in filename or "\\" in filename:
        abort(400)
    if vtype not in ("normal", "anomaly"):
        abort(404)
    path = _sm.find_video(vtype, filename)
    if path is None:
        abort(404)
    from video_recorder import read_layout          # lazy: ffprobe hanya dipakai saat diminta
    lay = read_layout(path)
    if lay is None:
        return jsonify({"error": "video tanpa metadata layout"}), 404
    resp = jsonify(lay)
    resp.headers["Cache-Control"] = "no-store"
    return resp
def _enqueue(msg: dict) -> bool:
    try:
        _hb_queue.put_nowait(msg)
        return True
    except queue.Full:
        try:
            _hb_queue.get_nowait()          # buang yang terlama
        except queue.Empty:
            pass
        try:
            _hb_queue.put_nowait(msg)
            return True
        except queue.Full:
            return False

def send_state(state: str, elapsed_sec=None, roi_idle_sec=None) -> bool:
    cfg = _station_config
    if not cfg or not _hb_connected.is_set():
        return False
    return _enqueue({
        "type": "state",
        "line": cfg["line"],
        "station_id": cfg["station_id"],
        "state": state,
        "elapsed_sec": elapsed_sec,
        "roi_idle_sec": roi_idle_sec,
    })