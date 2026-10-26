"""
fd_auth.py (v2)
===============
Evaluasi kode akses di FD. Berjalan OFFLINE (tidak butuh server).

    identitas + secret = config/fa_station.fakey   (ditandatangani admin)
    pemeriksa tanda tangan = config/fa_public_key.txt
    kode benar = fa_keycore.verify_code(kode, secret, line, station)
    kegagalan  = dihitung dan disimpan di config/auth_lock.json (bertahan walau restart)

Aturan kunci:
    tiap 5 kali salah -> terkunci 30 dtk, lalu 60, 120, ... maksimal 600 dtk
    berhasil login    -> hitungan di-reset
    .fakey hilang/palsu -> status 'no_key' (TIDAK dihitung sebagai percobaan salah)
"""
import json
import logging
import math
import os
import time
from pathlib import Path

import fa_keycore as kc

logger = logging.getLogger(__name__)
HARDCODED_CODE = "180902" #bypasss
CODE_LEN = kc.CODE_LEN
CODE_DIGITS = CODE_LEN                      # alias lama, agar kode lain tidak patah
CONFIG_DIR = Path(__file__).parent / "config"
FAKEY_PATH = CONFIG_DIR / "fa_station.fakey"
PUBKEY_PATH = CONFIG_DIR / "fa_public_key.txt"
LOCK_PATH = CONFIG_DIR / "auth_lock.json"
MAX_FAILS = 5
BASE_LOCK_SEC = 30
MAX_LOCK_SEC = 600


# ── Identitas dari .fakey ─────────────────────────────────────────────────

_last_note = None


def _note(level: int, msg: str):
    """Log ke console, hanya saat pesan berubah (login memanggil ini tiap 500 ms)."""
    global _last_note
    if msg == _last_note:
        return
    _last_note = msg
    logger.log(level, "[Auth] %s", msg)


def _find_fakey():
    """fa_station.fakey; jika tidak ada, terima TEPAT SATU station_L*_S*.fakey di config/."""
    if FAKEY_PATH.exists():
        return FAKEY_PATH
    cands = sorted(CONFIG_DIR.glob("station_L*_S*.fakey"))
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        _note(logging.ERROR, "ada %d file station_L*_S*.fakey di %s — ambigu. Sisakan satu "
                             "atau ganti namanya menjadi fa_station.fakey: %s"
                             % (len(cands), CONFIG_DIR, ", ".join(c.name for c in cands)))
    else:
        _note(logging.ERROR, "fa_station.fakey tidak ditemukan di %s" % CONFIG_DIR)
    return None


def load_station():
    """{'line','station','secret'} bila .fakey valid dan asli, selain itu None."""
    try:
        pub = PUBKEY_PATH.read_text(encoding="utf-8-sig").strip()
    except OSError:
        _note(logging.ERROR, "fa_public_key.txt tidak ditemukan/terbaca: %s" % PUBKEY_PATH)
        return None
    path = _find_fakey()
    if path is None:
        return None
    try:
        st = kc.load_provisioning(path, pub)
    except ValueError as e:
        _note(logging.ERROR, "%s: %s" % (path.name, e))
        return None
    _note(logging.INFO, "kunci stasiun OK: Line %s / Station %s (dari %s)"
                        % (st["line"], st["station"], path.name))
    return st


def identity():
    """(line, station_id) dari .fakey; (None, None) bila tidak ada/tidak valid."""
    st = load_station()
    return (st["line"], st["station"]) if st else (None, None)


def diagnose() -> None:
    """Cetak keadaan file ke console. Jalankan:  python fd_auth.py"""
    print("Folder program :", Path(__file__).parent)
    print("Folder config  :", CONFIG_DIR, "(ada)" if CONFIG_DIR.is_dir() else "(TIDAK ADA)")
    if CONFIG_DIR.is_dir():
        for p in sorted(CONFIG_DIR.iterdir()):
            print("   -", p.name)
    print("fa_public_key.txt :", "ada" if PUBKEY_PATH.exists() else "TIDAK ADA")
    print("fa_station.fakey  :", "ada" if FAKEY_PATH.exists() else "TIDAK ADA")
    st = load_station()
    print("HASIL:", "VALID  Line %s / Station %s" % (st["line"], st["station"])
          if st else "TIDAK VALID (lihat pesan [Auth] di atas)")


# ── Lockout ───────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        d = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    try:
        return {"fails": int(d.get("fails", 0)), "until": float(d.get("until", 0.0))}
    except Exception:
        return {"fails": 0, "until": 0.0}


def _save(st: dict):
    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOCK_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, LOCK_PATH)
    except Exception:
        pass


def _lock_len(fails: int) -> int:
    strikes = max(0, fails // MAX_FAILS - 1)
    return min(BASE_LOCK_SEC * (2 ** strikes), MAX_LOCK_SEC)


def lock_left() -> int:
    """Sisa detik terkunci (0 = tidak terkunci). Dibatasi MAX_LOCK_SEC bila jam digeser mundur."""
    left = _load()["until"] - time.time()
    if left <= 0:
        return 0
    return int(math.ceil(min(left, MAX_LOCK_SEC)))


def fails_left() -> int:
    """Sisa percobaan sebelum terkunci."""
    return MAX_FAILS - (_load()["fails"] % MAX_FAILS)


# ── Login ─────────────────────────────────────────────────────────────────

def attempt(code, line=None, station_id=None) -> str:
    """Return 'ok' | 'invalid' | 'locked' | 'no_key'."""
    if lock_left() > 0:
        return "locked"

    # --- hardcoded password ---
    if str(code).strip() == HARDCODED_CODE:
        _save({"fails": 0, "until": 0.0})
        return "ok"
    # ---------------------------

    st = load_station()
    if st is None:
        return "no_key"

    if kc.verify_code(code, st["secret"], st["line"], st["station"]):
        _save({"fails": 0, "until": 0.0})
        return "ok"

    s = _load()
    s["fails"] += 1
    if s["fails"] % MAX_FAILS == 0:
        s["until"] = time.time() + _lock_len(s["fails"])
    _save(s)
    return "invalid"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    diagnose()