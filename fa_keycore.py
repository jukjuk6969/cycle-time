"""
fa_keycore.py (v2)
==================
Dipakai BERSAMA oleh FA Key Generator dan FD. Tidak memuat MASTER maupun private key.

    seed phrase 12 kata --(admin, fa_seedgen.py)--> MASTER
    station_secret = HMAC(MASTER, "station|line|station_id")      -> file .fakey (per FD)
    window         = floor(epoch_UTC / 300)                       -> ganti tiap 5 menit
    kode           = 6 karakter angka+huruf dari
                     HMAC(station_secret, "line|station|window")

File .fakey ditandatangani Ed25519 (private key dari seed yang sama); FD memeriksa
tanda tangan dengan public key (fa_public_key.txt) sebelum mempercayai isinya.

Alfabet 32 karakter tanpa yang mirip (0/O, 1/I/L): 32^6 = 2^30 ~ 1,07 miliar kombinasi.
"""
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

WINDOW_SEC = 300
CODE_LEN = 6
TOLERANCE = 1                                  # terima window sebelum/sesudah
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # tepat 32 karakter


def _now(now):
    return time.time() if now is None else float(now)


def window_index(now=None) -> int:
    return int(_now(now) // WINDOW_SEC)


def seconds_left(now=None) -> int:
    return int(WINDOW_SEC - (_now(now) % WINDOW_SEC))


def make_code(secret: bytes, line, station, window: int) -> str:
    msg = f"{int(line)}|{int(station)}|{window}".encode()
    num = int.from_bytes(hmac.new(secret, msg, hashlib.sha256).digest()[:4], "big") >> 2
    return "".join(ALPHABET[(num >> (5 * i)) & 31] for i in reversed(range(CODE_LEN)))


def current_code(secret: bytes, line, station, now=None) -> str:
    return make_code(secret, line, station, window_index(now))


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def verify_code(code, secret: bytes, line, station, now=None,
                tolerance: int = TOLERANCE) -> bool:
    code = normalize(code)
    if len(code) != CODE_LEN:
        return False
    w, ok = window_index(now), False
    for dw in range(-tolerance, tolerance + 1):          # tanpa early-exit
        if hmac.compare_digest(code, make_code(secret, line, station, w + dw)):
            ok = True
    return ok


def fmt(code: str) -> str:
    return f"{code[:3]} {code[3:]}"


# ── File provisioning (.fakey) ────────────────────────────────────────────

def load_provisioning(fakey_path, public_key_hex: str) -> dict:
    """Kembalikan {'line','station','secret'(bytes)}; ValueError (dengan sebab jelas) jika gagal."""
    try:
        raw = Path(fakey_path).read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ValueError(f"file tidak bisa dibaca ({e.__class__.__name__}): {fakey_path}")
    try:
        d = json.loads(raw)
        payload, sig = d["payload"], base64.b64decode(d["sig"])
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    except (ValueError, KeyError, TypeError):
        raise ValueError("isi .fakey rusak atau formatnya salah")
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex.strip()))
    except ValueError:
        raise ValueError("fa_public_key.txt tidak valid (harus 64 karakter hex)")
    try:
        pub.verify(sig, blob)
    except InvalidSignature:
        raise ValueError("tanda tangan TIDAK cocok: .fakey dan fa_public_key.txt berasal dari "
                         "sesi 'new' (seed) yang berbeda, atau file .fakey diubah")
    try:
        return {"line": int(payload["line"]), "station": int(payload["station"]),
                "secret": bytes.fromhex(payload["secret"])}
    except (KeyError, ValueError, TypeError):
        raise ValueError("payload .fakey tidak lengkap")