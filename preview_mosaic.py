# preview_mosaic.py
"""
Pratinjau mosaik multi-kamera. Sumber bisa file video atau indeks kamera.

Contoh (2 video berdampingan, tiap tile 640x480):
    python preview_mosaic.py a.mp4 b.mp4
Contoh (video + kamera 0, tile 480x360, tumpuk vertikal, kamera ke-2 di-mirror):
    python preview_mosaic.py a.mp4 0 --tile 480x360 --stack v --mirror 2
Tekan Q / Esc untuk keluar.
"""
import argparse
import time

import cv2

from camera_layout import Layout, Tile, validate_layout
from multi_capture import MultiCapture


def build_layout(sources, tw, th, stack, mirror_slots):
    n = len(sources)
    if stack == "h":
        lay = Layout(tw * n, th)
        pos = [(i * tw, 0) for i in range(n)]
    else:
        lay = Layout(tw, th * n)
        pos = [(0, i * th) for i in range(n)]
    for i, (dev, (x, y)) in enumerate(zip(sources, pos), 1):
        lay.tiles.append(Tile(i, dev, x, y, tw, th, mirror=(i in mirror_slots)))
    return lay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+", help="file video atau indeks kamera, urut = kamera ke-1, ke-2, ...")
    ap.add_argument("--tile", default="640x480", help="ukuran tiap tile, WxH")
    ap.add_argument("--stack", choices=("h", "v"), default="h", help="h = berdampingan, v = atas-bawah")
    ap.add_argument("--mirror", type=int, nargs="*", default=[], help="nomor kamera yang di-mirror")
    a = ap.parse_args()

    tw, th = (int(v) for v in a.tile.lower().split("x"))
    lay = build_layout(a.sources, tw, th, a.stack, set(a.mirror))
    errs = validate_layout(lay)
    if errs:
        raise SystemExit("\n".join(errs))

    cap = MultiCapture(lay)
    if not cap.isOpened():
        cap.release()
        raise SystemExit("Salah satu sumber gagal dibuka. Periksa path / indeks kamera.")

    print(f"Canvas {lay.width}x{lay.height}, {len(lay.tiles)} kamera. Q / Esc untuk keluar.")
    n, t0, shown_fps = 0, time.perf_counter(), 0.0
    while True:
        ok, frame = cap.read()
        if ok:
            for t in lay.tiles:
                x1, y1, x2, y2 = t.rect
                cv2.rectangle(frame, (x1, y1), (x2 - 1, y2 - 1), (0, 255, 255), 2)
                cv2.putText(frame, f"C{t.slot}", (x1 + 8, y1 + 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)
            health = cap.health()
            bad = [s for s, v in health.items() if not v]
            cv2.putText(frame, f"{shown_fps:.0f} fps" + (f"  OFFLINE: {bad}" if bad else ""),
                        (8, lay.height - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 0, 255) if bad else (0, 255, 0), 2, cv2.LINE_AA)
            cv2.imshow("Mosaik", frame)
        n += 1
        if time.perf_counter() - t0 >= 1.0:
            shown_fps, n, t0 = n / (time.perf_counter() - t0), 0, time.perf_counter()
        if cv2.waitKey(30) & 0xFF in (ord("q"), 27):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
