# tile_view.py
"""Susun frame beberapa source menjadi satu kanvas tile. Tanpa Tk."""
from __future__ import annotations

import zlib

import cv2
import numpy as np

CELL_1 = (640, 480)     # sel bila hanya 1 source
CELL_N = (480, 360)     # sel bila >= 2 source (tetap, supaya ukuran stream/rekaman stabil)
REC_CAP = (1280, 720)   # batas per tile untuk rekaman bila >= 2 source (naikkan jika CPU kuat)
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def grid_for(n: int) -> tuple:
    """(kolom, baris). 1 -> 1x1, 2..6 -> 2 kolom, 7..9 -> 3 kolom."""
    n = max(1, n)
    if n == 1:
        return 1, 1
    cols = 2 if n <= 6 else 3
    return cols, -(-n // cols)


def fixed_cell(n: int) -> tuple:
    return CELL_1 if n <= 1 else CELL_N


def rec_cell(sizes: dict, slots) -> tuple:
    """Sel rekaman mengikuti resolusi asli source terbesar (dibatasi REC_CAP bila >= 2 source).
    sizes: {slot: (w, h)} dari SourceHub.sizes(). Hasil selalu genap (yuv420p)."""
    slots = list(slots)
    dims = [sizes.get(s, (0, 0)) for s in slots]
    ws = [w for w, _ in dims if w]
    hs = [h for _, h in dims if h]
    if not ws or not hs:
        return fixed_cell(len(slots))
    w, h = max(ws), max(hs)
    if len(slots) > 1:
        k = min(1.0, REC_CAP[0] / w, REC_CAP[1] / h)
        w, h = int(w * k), int(h * k)
    return max(2, w - w % 2), max(2, h - h % 2)


def canvas_size(n: int, cell=None) -> tuple:
    """(lebar, tinggi) kanvas untuk n tile."""
    cw, ch = cell or fixed_cell(n)
    cols, rows = grid_for(n)
    return cols * cw, rows * ch


def _text(img, s, org, scale=0.6, color=(255, 255, 255)):
    cv2.putText(img, s, org, _FONT, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, s, org, _FONT, scale, color, 1, cv2.LINE_AA)


def compose(slots, get_frame, cell, decorate=None, label=None, cols=None):
    n = len(slots)
    if cols is None:
        cols, rows = grid_for(n)
    else:
        cols = max(1, min(cols, n))
        rows = -(-n // cols)
    cw, ch = cell
    label = (n > 1) if label is None else label
    canvas = np.zeros((rows * ch, cols * cw, 3), np.uint8)
    for i, slot in enumerate(slots):
        r, c = divmod(i, cols)
        x0, y0 = c * cw, r * ch
        f = get_frame(slot)
        if f is None:
            _text(canvas, f"S{slot}  NO SIGNAL", (x0 + 12, y0 + ch // 2), 0.7, (170, 170, 170))
        else:
            h, w = f.shape[:2]
            s = min(cw / w, ch / h)
            nw, nh = max(1, int(w * s)), max(1, int(h * s))
            small = (cv2.resize(f, (nw, nh), interpolation=cv2.INTER_AREA)
                     if (nw, nh) != (w, h) else f.copy())
            if decorate:
                decorate(slot, small, w, h)
            ox, oy = (cw - nw) // 2, (ch - nh) // 2
            canvas[y0 + oy:y0 + oy + nh, x0 + ox:x0 + ox + nw] = small
            if label:
                _text(canvas, f"S{slot}", (x0 + 8, y0 + 22))
        if n > 1:
            cv2.rectangle(canvas, (x0, y0), (x0 + cw - 1, y0 + ch - 1), (70, 70, 70), 1)
    return canvas

def layout_meta(slots, cell=None, cols=None, sizes=None) -> dict:
    """Metadata posisi tile untuk satu kanvas. Harus dipanggil dengan argumen yang
    sama seperti compose() agar cocok dengan gambar.

    Koordinat tile ternormalisasi 0..1 terhadap kanvas: [x, y, w, h].
    Klik di mana pun di dalam rect (termasuk area letterbox hitam) = slot tersebut.
    Bila `sizes` ({slot: (w, h)}) diberikan, tiap tile juga memuat "content": rect area gambar
    sebenarnya (tanpa letterbox) pada format yang sama, untuk crop di player.
    """
    slots = list(slots)
    n = len(slots)
    if n == 0:
        return {"layout_id": 0, "slots": [], "cols": 0, "rows": 0,
                "canvas": [0, 0], "tiles": []}
    cw, ch = cell or fixed_cell(n)
    if cols is None:
        cols, rows = grid_for(n)
    else:
        cols = max(1, min(cols, n))
        rows = -(-n // cols)
    tiles = []
    for i, slot in enumerate(slots):
        r, c = divmod(i, cols)
        t = {"slot": slot,
             "rect": [round(c / cols, 5), round(r / rows, 5),
                      round(1 / cols, 5), round(1 / rows, 5)]}
        w, h = (sizes or {}).get(slot, (0, 0))
        if w and h:                                  # sama dengan perhitungan di compose()
            sc = min(cw / w, ch / h)
            nw, nh = max(1, int(w * sc)), max(1, int(h * sc))
            ox, oy = (cw - nw) // 2, (ch - nh) // 2
            W, H = cols * cw, rows * ch
            t["content"] = [round((c * cw + ox) / W, 5), round((r * ch + oy) / H, 5),
                            round(nw / W, 5), round(nh / H, 5)]
        tiles.append(t)
    canvas = [cols * cw, rows * ch]
    # id stabil: berubah hanya jika urutan slot / grid / ukuran kanvas berubah
    lid = zlib.crc32(f"{slots}|{cols}x{rows}|{canvas}".encode()) & 0x7FFFFFFF
    return {"layout_id": lid, "slots": slots, "cols": cols, "rows": rows,
            "canvas": canvas, "tiles": tiles}