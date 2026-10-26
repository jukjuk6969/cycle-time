# logic_cycle_engine.py
"""
LogicCycleEngine — penyambung logic_config.xml (dibuat logic_editor) ke siklus produksi main.py.

Tugasnya hanya MEMBACA konfigurasi dan menjalankan logikanya (lewat SequenceTester di
logic_bridge.py). Tidak ada kode menggambar / mengedit ROI di sini.

Antarmuka sengaja meniru ROITracker lama supaya penggantian di main.py minim:
    eng = LogicCycleEngine()
    eng.load("config/logic_config.xml")
    eng.on_cycle_start      = f(start_ts)
    e.on_cycle_complete = lambda n, s, en, ct, g, sk=None: ev.append(("done", n, round(s, 3), round(ct, 3), g))
    eng.on_cycle_rejected   = f(elapsed, min_ct)      # siklus < min cycle time
    eng.on_cycle_aborted    = f(reason)               # konfigurasi berubah saat siklus berjalan
    eng.min_cycle_getter    = callable -> float
    eng.update(dets)        # dets = [(x1, y1, x2, y2, class, conf)]; PANGGIL TIAP HASIL INFERENSI,
                            # termasuk saat dets kosong (supaya dwell/grace/timer bisa berakhir)
    eng.is_cycle_active() / cycle_elapsed() / seconds_since_roi() / abort_cycle() / reset_count()

Waktu: update() memakai time.time() (jam dinding) bila t tidak diberikan, sehingga
start_ts/end_ts langsung cocok dengan VideoRecorder dan server.

Catatan thread: update() dipanggil dari thread YOLO, getter dari thread UI. Callback
dipanggil SETELAH lock dilepas.

Uji mandiri:  python logic_cycle_engine.py
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from logic_bridge import SequenceTester
from logic_editor import adapt_to_frame, load_logic_xml

logger = logging.getLogger(__name__)


class LogicCycleEngine:
    def __init__(self, cfg=None):
        self._lock = threading.RLock()
        self._tester = SequenceTester()
        self.cfg = None

        self.on_cycle_start: Optional[Callable] = None
        self.on_cycle_complete: Optional[Callable] = None
        self.on_cycle_rejected: Optional[Callable] = None
        self.on_cycle_aborted: Optional[Callable] = None
        self.min_cycle_getter: Optional[Callable] = None

        self._count = 0
        self._reset_runtime()
        if cfg is not None:
            self.set_config(cfg)

    # ── internal ──────────────────────────────────────────────────────────
    def _reset_runtime(self):
        self._active = False
        self._start_ts: Optional[float] = None
        self._last_hit: Optional[float] = None
        self._genesis = True               # siklus pertama setelah start / abort / reset
        self._seen = self._tester.cycles
        self._prev_status = {}

    def _fire(self, events: list):
        for ev in events:
            kind, args = ev[0], ev[1:]
            cb = {
                "start": self.on_cycle_start,
                "complete": self.on_cycle_complete,
                "rejected": self.on_cycle_rejected,
                "aborted": self.on_cycle_aborted,
            }[kind]
            if cb:
                try:
                    cb(*args)
                except Exception as e:
                    logger.error(f"[LogicEngine] callback {kind} error: {e}", exc_info=True)

    # ── konfigurasi ───────────────────────────────────────────────────────
    def load(self, path) -> bool:
        """Baca logic_config.xml. Return True bila konfigurasi berubah."""
        return self.set_config(load_logic_xml(path))

    def set_config(self, cfg) -> bool:
        events = []
        with self._lock:
            self.cfg = cfg
            changed = self._tester.sync(cfg)
            if changed:
                if self._active:
                    events.append(("aborted", "konfigurasi ROI berubah"))
                self._active = False
                self._start_ts = self._last_hit = None
                self._genesis = True
                self._seen = self._tester.cycles
        self._fire(events)
        return changed

    def adapt(self, frame_w: int, frame_h: int, mirror: bool) -> list:
        """Sesuaikan koordinat ROI ke resolusi/mirror kamera aktif. Return catatan perubahan."""
        with self._lock:
            if self.cfg is None:
                return []
            notes = adapt_to_frame(self.cfg, frame_w, frame_h, bool(mirror))
            if notes:
                self.set_config(self.cfg)
            return notes

    def ready(self) -> bool:
        with self._lock:
            return self._tester.ready() and self._tester.pipe is not None

    @property
    def error(self) -> str:
        return self._tester.error

    # ── inti ──────────────────────────────────────────────────────────────
    def update(self, dets: list, t: Optional[float] = None):
        t = time.time() if t is None else t
        events = []
        with self._lock:
            tester = self._tester
            if not self.ready():
                return
            tester.update(dets, t)

            if any(names for _, names in tester.assign):
                self._last_hit = t

            cycles, state = tester.cycles, tester.state
            runner = getattr(tester.pipe, "runner", None)
            t0 = getattr(runner, "t0", None)

            # 1) siklus selesai
            if cycles > self._seen:
                self._seen = cycles
                if self._active:
                    events.append(self._finish(t, tester.last_cycle, dict(tester.status)))
            elif self._active:
                self._prev_status = dict(tester.status)     # snapshot frame terakhir sebelum selesai

            # 2) siklus mulai / mulai ulang
            if state == "RUNNING":
                if not self._active:
                    events.append(self._begin(t0 if t0 is not None else t))
                elif t0 is not None and t0 != self._start_ts:      # restart_on_input
                    events.append(("aborted", "siklus dimulai ulang dari Input"))
                    events.append(self._begin(t0))
        self._fire(events)

    def _begin(self, ts: float):
        self._active = True
        self._start_ts = ts
        self._last_hit = ts
        return ("start", ts)

    def _skipped_expected(self, status: dict) -> list:
        """ROI tag 'expected' yang tidak 'done' pada siklus ini → [(id, keterangan)]."""
        if self.cfg is None:
            return []
        prev = self._prev_status

        def done(rid):
            for s in (status, prev):
                st = s.get(rid) if s else None
                if st and st[0] == "done":
                    return True
            return False

        return [(r.id, getattr(r, "description", "") or r.id)
                for r in self.cfg.sequence
                if getattr(r, "tag", "") == "expected" and not done(r.id)]

    def _finish(self, end_ts: float, ct: Optional[float], status: Optional[dict] = None):
        start = self._start_ts
        self._active = False
        self._start_ts = self._last_hit = None
        if ct is None:
            ct = end_ts - start
        min_ct = float(self.min_cycle_getter()) if self.min_cycle_getter else 0.0
        if ct < min_ct:
            self._prev_status = {}
            return ("rejected", ct, min_ct)
        skipped = self._skipped_expected(status or {})
        self._prev_status = {}
        self._count += 1
        genesis, self._genesis = self._genesis, False
        return ("complete", self._count, start, end_ts, ct, genesis, skipped)

    # ── antarmuka ala ROITracker ──────────────────────────────────────────
    @property
    def cycle_count(self) -> int:
        return self._count

    def is_cycle_active(self) -> bool:
        return self._active

    def cycle_elapsed(self) -> float:
        """Detik sejak Input terkena (0 bila tidak ada siklus)."""
        s = self._start_ts
        return time.time() - s if (self._active and s is not None) else 0.0

    def seconds_since_roi(self) -> float:
        """Detik sejak terakhir ada deteksi yang mengenai ROI mana pun (untuk timer discard)."""
        h = self._last_hit
        return time.time() - h if (self._active and h is not None) else 0.0

    def abort_cycle(self):
        """Batalkan siklus berjalan TANPA menghapus cycle_count. Siklus berikutnya = genesis."""
        with self._lock:
            self._tester.reset()
            self._reset_runtime()

    def reset_count(self):
        with self._lock:
            self.abort_cycle()
            self._count = 0

    # ── untuk UI / monitor ────────────────────────────────────────────────
    def status(self) -> dict:
        with self._lock:
            return dict(self._tester.status)

    def message(self) -> str:
        with self._lock:
            return self._tester.message

    def state(self) -> str:
        with self._lock:
            return self._tester.state

    def render(self) -> str:
        with self._lock:
            return self._tester.render()

    def roi_ids(self) -> list:
        return [r.id for r in self.cfg.all_rois()] if self.cfg else []
    def monitor_snapshot(self):
        with self._lock:
            pipe = self._tester.pipe
            if pipe is None:
                return None
            return {
                "dbg": pipe.debug(),
                "state": self._tester.state,
                "cycles": self._tester.cycles,
                "cycle_time": getattr(pipe, "cycle_time", None),
                "last_cycle": self._tester.last_cycle,
                "msg": self._tester.render(),
                "wall": time.time(),
            }


# ── Uji mandiri ────────────────────────────────────────────────────────────
def _selftest():
    from types import SimpleNamespace as NS

    def roi(id, role, x, tag="", desc="", **kw):
        return NS(id=id, role=role, rect=(x, 0, x + 50, 50), classes=set(), conf=0.25,
                  tag=tag, description=desc, **kw)

    class Cfg(NS):
        def all_rois(self):
            return [self.input, *self.sequence, self.output]

    def make(**kw):
        return Cfg(input=roi("in", "input", 0),
                   sequence=[roi("s1", "sequence", 60, "expected", "scan"),
                             roi("s2", "sequence", 120, "mandatory", "pasang", dwell=0.2),
                             roi("s3", "sequence", 180, "expected", "mask")],
                   output=roi("out", "output", 240), **kw)

    def det(name):
        r = {"in": 0, "s1": 60, "s2": 120, "s3": 180, "out": 240}[name]
        return [(r + 20, 20, r + 30, 30, "hand", 0.9)]

    seq = ["", "in", "", "s1", "", "s2", "s2", "s2", "", "s3", "", "out", ""]

    def drive(e, frames, i0=0):
        for i, n in enumerate(frames, start=i0):
            e.update(det(n) if n else [], round(i * 0.1, 6))

    def hook(e, ev):
        e.on_cycle_start = lambda ts: ev.append(("start", round(ts, 3)))
        e.on_cycle_complete = lambda n, s, en, ct, g, sk=None: ev.append(("done", n, round(s, 3), round(ct, 3), g))
        e.on_cycle_rejected = lambda el, mn: ev.append(("rej", round(el, 3)))
        e.on_cycle_aborted = lambda why: ev.append(("abort", why))

    # 1) dua siklus penuh: genesis hanya yang pertama
    ev = []
    e = LogicCycleEngine(make())
    hook(e, ev)
    e.min_cycle_getter = lambda: 0.5
    drive(e, seq)
    assert ev == [("start", 0.1), ("done", 1, 0.1, 1.0, True)], ev
    assert e.cycle_count == 1 and not e.is_cycle_active()
    drive(e, seq, i0=13)
    assert ev[2:] == [("start", 1.4), ("done", 2, 1.4, 1.0, False)], ev

    # 2) terlalu cepat -> rejected, hitungan tidak naik
    ev = []
    e = LogicCycleEngine(make())
    hook(e, ev)
    e.min_cycle_getter = lambda: 2.0
    drive(e, seq)
    assert ev == [("start", 0.1), ("rej", 1.0)] and e.cycle_count == 0, ev

    # 3) abort_cycle menjaga hitungan, siklus berikutnya genesis lagi
    ev = []
    e = LogicCycleEngine(make())
    hook(e, ev)
    drive(e, seq)
    drive(e, ["", "in", "", "s1"], i0=13)
    assert e.is_cycle_active()
    e.abort_cycle()
    assert not e.is_cycle_active() and e.cycle_count == 1 and e.cycle_elapsed() == 0.0
    ev.clear()
    drive(e, seq, i0=40)
    assert ev[-1][0] == "done" and ev[-1][1] == 2 and ev[-1][4] is True, ev

    # 4) konfigurasi berubah saat siklus berjalan -> on_cycle_aborted
    ev = []
    e = LogicCycleEngine(make())
    hook(e, ev)
    drive(e, ["", "in", "", "s1"])
    assert e.set_config(make(skip_mode="final")) is True
    assert ev[-1][0] == "abort" and not e.is_cycle_active(), ev
    assert e.set_config(make(skip_mode="final")) is False

    # 5) reset_count
    e.reset_count()
    assert e.cycle_count == 0
    print("logic_cycle_engine: semua uji lolos")


if __name__ == "__main__":
    _selftest()