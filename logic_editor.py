# logic_editor.py
"""
Logic Editor — editor ROI untuk CT Monitor.

Menghasilkan config/logic_config.xml yang dibaca main.py.

Kategori ROI
  main_roi      : Input (ambil barang) dan Output (taruh barang). Wajib, dibuat lebih dulu.
  sequence_roi  : berurutan, di antara Input dan Output.
      tag "mandatory": wajib selesai; kalau belum, ROI berikutnya terkunci.
      tag "expected" : boleh dilewati; terlewat -> label "skip process".
      Setiap sequence_roi wajib punya keterangan ("ROI ini untuk apa").

Timeout TIDAK disimpan di XML (nilainya dari server; hanya untuk main_roi + mandatory).

Cara pakai dari main.py (hybrid):
    panel = LogicEditorPanel(parent, preview_label,
                             get_frame_size=lambda: (w, h),     # (0, 0) bila belum ada sumber
                             get_class_names=lambda: {id: "nama"},
                             get_mirror=lambda: bool,
                             on_close=..., on_saved=..., set_status=...)
    panel.attach()    # ambil alih mouse di preview; panel.pack(...) oleh main.py
    panel.detach()    # kembalikan mouse ke ROIOverlay lama
    di loop UI:  panel.set_view(preview_w, preview_h, frame_w, frame_h, off_x, off_y)
                 frame_rgb = panel.draw_on_frame(frame_rgb)
    main.py membaca hasilnya lewat load_logic_xml(path).

Khusus sumber VIDEO (jeda / mundur / maju per frame, slider, kecepatan):
    kirim transport=VideoTransport(cap) ke panel; main.py cukup memanggil
    transport.next_frame(dt) di loop capture alih-alih cap.read(). Untuk kamera: transport=None.
    Pintasan: Spasi = jeda, <-/-> = 1 frame, Shift+<-/-> = 1 detik.

Uji mandiri:  python logic_editor.py --video file.mp4 --classes hand
              python logic_editor.py --image foto.jpg
              python logic_editor.py --camera 0
"""
from __future__ import annotations
import gc
import argparse
import os
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
import cv2
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import queue
from logic_bridge import SequenceTester, check_config, template_text
from plc_logic import compile_program
from plc_engine import Engine
XML_VERSION = "3"
DEFAULT_XML = Path(__file__).parent / "config" / "logic_config.xml"

ROLE_INPUT = "input"
ROLE_OUTPUT = "output"
ROLE_SEQ = "sequence"
TAG_MANDATORY = "mandatory"
TAG_EXPECTED = "expected"
TAGS = (TAG_MANDATORY, TAG_EXPECTED)
MIN_ROI_PX = 10


# ═══════════════════════════════════════════════════════════════════════════════
#  Model data (tanpa Tk — bisa dipakai main.py)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class RoiDef:
    id: str
    role: str                                   # input | output | sequence
    rect: tuple                                 # (x1, y1, x2, y2) koordinat frame
    classes: set = field(default_factory=set)   # kosong = semua class
    conf: float = 0.25
    tag: str = ""                               # hanya sequence
    description: str = ""                       # hanya sequence
    mode: str = "center"                        # center | fraction | iou
    min_overlap: float = 0.4                    # untuk mode fraction / iou
    priority: int = 0                           # menang bila satu deteksi kena banyak ROI dalam 1 grup
    group: str = ""                             # "" = tidak eksklusif
    dwell: float = 0.0                          # detik: harus bertahan di ROI sebelum dihitung
    grace: float = 0.0                          # detik: tetap dianggap di ROI setelah deteksi hilang


@dataclass
class LogicConfig:
    input: Optional[RoiDef] = None
    output: Optional[RoiDef] = None
    memory: dict = field(default_factory=lambda: {"aliases": {}})
    sequence: list = field(default_factory=list)
    frame_w: int = 0
    frame_h: int = 0
    mirror: bool = False
    skip_mode: str = "immediate"                # immediate | final
    restart_on_input: bool = False
    single: bool = False                        # satu deteksi hanya boleh kena satu ROI
    max_det: int = 0                            # 0 = tanpa batas; K = K deteksi ber-conf tertinggi
    program: str = ""                           # kosong = template; isi = program PLC teks

    def all_rois(self) -> list:
        out = []
        if self.input:
            out.append(self.input)
        out.extend(self.sequence)
        if self.output:
            out.append(self.output)
        return out

    def find(self, rid: Optional[str]) -> Optional[RoiDef]:
        for r in self.all_rois():
            if r.id == rid:
                return r
        return None

    def next_seq_id(self) -> str:
        nums = [int(r.id[1:]) for r in self.sequence if r.id[1:].isdigit()]
        return f"s{max(nums, default=0) + 1}"


def validate(cfg: LogicConfig, frame_size: tuple = (0, 0)):
    """Return (errors, warnings). Error = tidak boleh disimpan."""
    errors, warnings = [], []
    if cfg.input is None:
        errors.append("ROI Input belum dibuat.")
    if cfg.output is None:
        errors.append("ROI Output belum dibuat.")
    if not any(r.tag == TAG_MANDATORY for r in cfg.sequence):
        errors.append("Minimal 1 sequence ROI harus bertag 'mandatory'.")

    seen = set()
    for i, r in enumerate(cfg.sequence, 1):
        if r.tag not in TAGS:
            errors.append(f"Sequence {i}: tag harus 'mandatory' atau 'expected'.")
        if not r.description.strip():
            errors.append(f"Sequence {i}: keterangan wajib diisi.")
    for r in cfg.all_rois():
        if r.id in seen:
            errors.append(f"ID ROI dobel: {r.id}")
        seen.add(r.id)
        x1, y1, x2, y2 = r.rect
        if (x2 - x1) < MIN_ROI_PX or (y2 - y1) < MIN_ROI_PX:
            errors.append(f"ROI '{r.id}' terlalu kecil (< {MIN_ROI_PX}px).")
        fw, fh = frame_size
        if fw and fh and (x1 < 0 or y1 < 0 or x2 > fw or y2 > fh):
            warnings.append(f"ROI '{r.id}' melewati batas frame {fw}x{fh}.")
    e2, w2 = check_config(cfg)
    errors += e2
    warnings += w2
    return errors, warnings


def adapt_to_frame(cfg: LogicConfig, fw: int, fh: int, mirror: bool) -> list:
    """Sesuaikan koordinat ke resolusi/mirror aktif. Return catatan perubahan."""
    notes = []
    if not (fw and fh):
        return notes
    if cfg.mirror != mirror and cfg.frame_w:
        W = cfg.frame_w
        for r in cfg.all_rois():
            x1, y1, x2, y2 = r.rect
            r.rect = (W - x2, y1, W - x1, y2)
        notes.append("mirror dibalik")
    if cfg.frame_w and cfg.frame_h and (cfg.frame_w != fw or cfg.frame_h != fh):
        sx, sy = fw / cfg.frame_w, fh / cfg.frame_h
        for r in cfg.all_rois():
            x1, y1, x2, y2 = r.rect
            r.rect = (int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))
        notes.append(f"di-rescale {cfg.frame_w}x{cfg.frame_h} -> {fw}x{fh}")
    cfg.frame_w, cfg.frame_h, cfg.mirror = fw, fh, mirror
    return notes


# ═══════════════════════════════════════════════════════════════════════════════
#  XML
# ═══════════════════════════════════════════════════════════════════════════════


def _write_geom(el: ET.Element, r: RoiDef):
    el.set("id", r.id)
    for k, v in zip(("x1", "y1", "x2", "y2"), r.rect):
        el.set(k, str(int(v)))
    el.set("classes", ",".join(sorted(r.classes)))
    el.set("conf", f"{r.conf:.3f}")
    el.set("mode", r.mode)
    el.set("min_overlap", f"{r.min_overlap:.3f}")
    el.set("priority", str(int(r.priority)))
    el.set("group", r.group)
    el.set("dwell", f"{r.dwell:g}")
    el.set("grace", f"{r.grace:g}")


def save_logic_xml(path, cfg: LogicConfig):
    errors, _ = validate(cfg)
    if errors:
        raise ValueError("\n".join(errors))
    root = ET.Element("logic_config")
    root.set("version", XML_VERSION)
    root.set("saved_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    root.set("frame_width", str(cfg.frame_w))
    root.set("frame_height", str(cfg.frame_h))
    root.set("mirror", "true" if cfg.mirror else "false")
    root.set("skip_mode", cfg.skip_mode)
    root.set("restart_on_input", "true" if cfg.restart_on_input else "false")
    root.set("single", "true" if cfg.single else "false")
    root.set("max_det", str(int(cfg.max_det)))

    for r, role in ((cfg.input, ROLE_INPUT), (cfg.output, ROLE_OUTPUT)):
        el = ET.SubElement(root, "main_roi")
        el.set("role", role)
        _write_geom(el, r)

    seq = ET.SubElement(root, "sequence")
    for i, r in enumerate(cfg.sequence, 1):
        el = ET.SubElement(seq, "roi")
        el.set("order", str(i))
        el.set("tag", r.tag)
        el.set("description", r.description.strip())
        _write_geom(el, r)

    if cfg.program.strip():
        ET.SubElement(root, "program").text = cfg.program.strip("\n")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    mem = ET.SubElement(root, "memory")
    for name, item in cfg.memory.get("aliases", {}).items():
        m = ET.SubElement(mem, "alias")
        m.set("name", name)
        m.set("address", item.get("address", ""))
        m.set("type", item.get("type", "BOOL"))
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, path)


def _read_roi(el: ET.Element, role: str) -> RoiDef:
    cls = el.get("classes", "")
    return RoiDef(
        id=el.get("id", ""),
        role=role,
        rect=tuple(int(el.get(k)) for k in ("x1", "y1", "x2", "y2")),
        classes={c for c in cls.split(",") if c},
        conf=float(el.get("conf", "0.25") or 0.25),
        tag=el.get("tag", ""),
        description=el.get("description", ""),
        mode=el.get("mode", "center") or "center",
        min_overlap=float(el.get("min_overlap", "0.4") or 0.4),
        priority=int(float(el.get("priority", "0") or 0)),
        group=el.get("group", ""),
        dwell=float(el.get("dwell", "0") or 0),
        grace=float(el.get("grace", "0") or 0),
    )


def load_logic_xml(path) -> LogicConfig:
    root = ET.parse(path).getroot()
    if root.tag != "logic_config":
        raise ValueError("Bukan file logic_config.")
    cfg = LogicConfig(
        frame_w=int(root.get("frame_width", 0) or 0),
        frame_h=int(root.get("frame_height", 0) or 0),
        mirror=root.get("mirror", "false") == "true",
    )
    cfg.skip_mode = root.get("skip_mode", "immediate") or "immediate"
    cfg.restart_on_input = root.get("restart_on_input", "false") == "true"
    cfg.single = root.get("single", "false") == "true"
    cfg.max_det = int(root.get("max_det", "0") or 0)
    prog = root.find("program")
    cfg.program = (prog.text or "") if prog is not None else ""
    for el in root.findall("main_roi"):
        role = el.get("role")
        if role == ROLE_INPUT:
            cfg.input = _read_roi(el, ROLE_INPUT)
        elif role == ROLE_OUTPUT:
            cfg.output = _read_roi(el, ROLE_OUTPUT)
    seq = root.find("sequence")
    if seq is not None:
        rows = sorted(seq.findall("roi"), key=lambda e: int(e.get("order", 0) or 0))
        cfg.sequence = [_read_roi(e, ROLE_SEQ) for e in rows]
    mem = root.find("memory")
    if mem is not None:
        for m in mem.findall("alias"):
            cfg.memory["aliases"][m.get("name", "")] = {
                "address": m.get("address", ""),
                "type": m.get("type", "BOOL"),
            }
    return cfg


def open_program_in_ld(parent, cfg: LogicConfig, on_applied=None):
    """Buka program aktif (cfg.program atau template) di LDEditor, simpan balik hasil edit."""
    from ld_editor import LDEditor

    prog = (cfg.program or "").strip()
    if not prog:
        # Butuh Input + Output minimal untuk membuat template
        if not (cfg.input and cfg.output):
            messagebox.showinfo(
                "Belum bisa membuka LD",
                "Gambar ROI Input dan Output dulu sebelum membuka program di LD.\n"
                "Template program tidak bisa dibuat tanpa keduanya.",
                parent=parent,
            )
            return
        prog = template_text(cfg)

    roi_names = [r.id for r in cfg.all_rois()] + ["__abort"]
    def _apply(text: str):
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.startswith("#@ld ")
        ).strip()
        try:
            Engine(compile_program(body))
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e), parent=parent)
            return
        cfg.program = body + "\n"      # hanya commit kalau valid
        if on_applied:
            on_applied(cfg)

    # Buka LDEditor. live=None; tambahkan callable monitor bila perlu.
    ed = LDEditor(
        parent,
        program=prog,
        roi_names=roi_names,
        on_apply=_apply,
        memory_map={"aliases": dict(cfg.memory.get("aliases", {}))},
    )
    ed.wait_window()

# ═══════════════════════════════════════════════════════════════════════════════
#  Dialog properti ROI
# ═══════════════════════════════════════════════════════════════════════════════


def ask_roi_properties(parent, title, class_names, is_sequence, init=None, default_conf=0.25):
    """Dialog modal. Return dict(classes, conf, tag, description) atau None."""
    names = list(class_names)
    if init:
        names += [c for c in sorted(init.classes) if c not in names]

    top = tk.Toplevel(parent)
    top.title(title)
    top.resizable(False, False)
    top.transient(parent)
    top.grab_set()
    result = {"value": None}
    body = ttk.Frame(top, padding=12)
    body.pack(fill="both", expand=True)

    ttk.Label(body, text="Class yang diterima:", font=("Arial", 10, "bold")).pack(anchor="w")
    class_vars: dict[str, tk.BooleanVar] = {}
    if names:
        checked = set(init.classes) if init and init.classes else set(names)
        for n in names:
            class_vars[n] = tk.BooleanVar(value=n in checked)
            ttk.Checkbutton(body, text=n, variable=class_vars[n]).pack(anchor="w", padx=8)
    else:
        ttk.Label(body, text="Model belum dimuat — semua class diterima.",
                  foreground="#666666").pack(anchor="w", padx=8)

    ttk.Label(body, text="Confidence minimum:", font=("Arial", 10, "bold")).pack(
        anchor="w", pady=(10, 0))
    conf_var = tk.DoubleVar(value=init.conf if init else default_conf)
    conf_row = ttk.Frame(body)
    conf_row.pack(fill="x")
    conf_lbl = ttk.Label(conf_row, text=f"{conf_var.get():.2f}", width=5)
    ttk.Scale(conf_row, from_=0.01, to=1.0, variable=conf_var, length=220,
              command=lambda _=None: conf_lbl.config(text=f"{conf_var.get():.2f}")
              ).pack(side="left")
    conf_lbl.pack(side="left", padx=(6, 0))

    tag_var = tk.StringVar(value=(init.tag if init and init.tag else TAG_MANDATORY))
    desc_var = tk.StringVar(value=init.description if init else "")
    desc_entry = None
    if is_sequence:
        ttk.Label(body, text="Tag:", font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 0))
        ttk.Radiobutton(body, text="mandatory — wajib; ROI berikutnya terkunci sampai selesai",
                        variable=tag_var, value=TAG_MANDATORY).pack(anchor="w", padx=8)
        ttk.Radiobutton(body, text="expected — boleh dilewati; terlewat = 'skip process'",
                        variable=tag_var, value=TAG_EXPECTED).pack(anchor="w", padx=8)
        ttk.Label(body, text="Keterangan (ROI ini untuk apa):",
                  font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 0))
        desc_entry = ttk.Entry(body, textvariable=desc_var, width=44)
        desc_entry.pack(fill="x")

    adv = {
        "mode": tk.StringVar(value=getattr(init, "mode", "center") if init else "center"),
        "min_overlap": tk.StringVar(value=f"{getattr(init, 'min_overlap', 0.4):g}" if init else "0.4"),
        "priority": tk.StringVar(value=str(getattr(init, "priority", 0)) if init else "0"),
        "group": tk.StringVar(value=getattr(init, "group", "") if init else ""),
        "dwell": tk.StringVar(value=f"{getattr(init, 'dwell', 0.0):g}" if init else "0"),
        "grace": tk.StringVar(value=f"{getattr(init, 'grace', 0.0):g}" if init else "0"),
    }
    ttk.Label(body, text="Lanjutan (logika PLC):", font=("Arial", 10, "bold")).pack(
        anchor="w", pady=(10, 0))
    adv_grid = ttk.Frame(body)
    adv_grid.pack(fill="x", padx=8)
    for i, (lbl, key) in enumerate((
            ("Mode kena", "mode"), ("Min overlap (fraction/iou)", "min_overlap"),
            ("Prioritas", "priority"), ("Grup eksklusif", "group"),
            ("Dwell (detik)", "dwell"), ("Grace (detik)", "grace"))):
        ttk.Label(adv_grid, text=lbl).grid(row=i, column=0, sticky="w", pady=1)
        if key == "mode":
            w = ttk.Combobox(adv_grid, textvariable=adv[key], values=("center", "fraction", "iou"),
                             state="readonly", width=10)
        else:
            w = ttk.Entry(adv_grid, textvariable=adv[key], width=12)
        w.grid(row=i, column=1, sticky="w", padx=(8, 0), pady=1)

    err = ttk.Label(body, text="", foreground="#c62828")
    err.pack(anchor="w", pady=(6, 0))

    def _ok(_=None):
        chosen = {n for n, v in class_vars.items() if v.get()}
        if class_vars and not chosen:
            err.config(text="Pilih minimal 1 class.")
            return
        desc = desc_var.get().strip()
        if is_sequence and not desc:
            err.config(text="Keterangan wajib diisi.")
            return
        try:
            mo = float(adv["min_overlap"].get().replace(",", "."))
            pr = int(float(adv["priority"].get() or 0))
            dw = float(adv["dwell"].get().replace(",", ".") or 0)
            gr = float(adv["grace"].get().replace(",", ".") or 0)
            if not (0 < mo <= 1) or dw < 0 or gr < 0:
                raise ValueError
        except ValueError:
            err.config(text="Lanjutan: overlap 0-1, dwell/grace >= 0, prioritas bilangan bulat.")
            return
        result["value"] = {
            "classes": chosen,
            "conf": round(float(conf_var.get()), 3),
            "tag": tag_var.get() if is_sequence else "",
            "description": desc if is_sequence else "",
            "mode": adv["mode"].get(),
            "min_overlap": mo,
            "priority": pr,
            "group": adv["group"].get().strip().replace(" ", "_"),
            "dwell": dw,
            "grace": gr,
        }
        top.destroy()

    btns = ttk.Frame(body)
    btns.pack(fill="x", pady=(8, 0))
    ttk.Button(btns, text="OK", width=8, command=_ok).pack(side="left")
    ttk.Button(btns, text="Batal", width=8, command=top.destroy).pack(side="left", padx=(6, 0))
    top.bind("<Return>", _ok)
    top.bind("<Escape>", lambda e: top.destroy())

    top.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - top.winfo_reqwidth()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - top.winfo_reqheight()) // 3)
    top.geometry(f"+{x}+{y}")
    (desc_entry or top).focus_set()
    top.wait_window()
    return result["value"]


# ═══════════════════════════════════════════════════════════════════════════════
#  Transport video (jeda / mundur / maju) — tanpa Tk, aman lintas thread
# ═══════════════════════════════════════════════════════════════════════════════


class VideoTransport:
    """Membungkus cv2.VideoCapture untuk file video.

    next_frame(dt) dipanggil terus-menerus oleh loop tampilan/capture (dt = detik sejak
    panggilan sebelumnya). Saat dijeda frame yang sama dikembalikan, jadi ROI bisa
    digambar dengan tenang. Seek (mundur/maju) diterapkan pada panggilan berikutnya.
    """

    SPEEDS = (0.25, 0.5, 1.0, 2.0)

    def __init__(self, cap, loop: bool = True):
        self.cap = cap
        self.loop = loop
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        self._fps = fps if 1.0 <= fps <= 240.0 else 25.0
        self._count = max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
        self._paused = False
        self._speed = 1.0
        self._pos = 0
        self._frame = None
        self._acc = 0.0
        self._pending: Optional[int] = None
        self._lock = threading.RLock()

    # -- status --
    def is_video(self) -> bool:
        return True

    def fps(self) -> float:
        return self._fps

    def frame_count(self) -> int:
        return self._count

    def position(self) -> int:
        """Index frame yang sedang tampil (0-based)."""
        with self._lock:
            return self._pending if self._pending is not None else self._pos

    def is_paused(self) -> bool:
        return self._paused

    def get_speed(self) -> float:
        return self._speed

    # -- kontrol --
    def set_paused(self, flag: bool):
        with self._lock:
            self._paused = bool(flag)
            self._acc = 0.0

    def set_speed(self, speed: float):
        with self._lock:
            self._speed = max(0.05, float(speed))

    def seek(self, idx: int):
        with self._lock:
            hi = self._count - 1 if self._count > 0 else None
            idx = max(0, idx if hi is None else min(idx, hi))
            self._pending = idx
            self._acc = 0.0

    def step(self, n: int):
        """Maju (n>0) atau mundur (n<0) n frame; otomatis menjeda."""
        with self._lock:
            base = self._pending if self._pending is not None else self._pos
            self._paused = True
            self.seek(base + n)

    # -- dipanggil loop tampilan --
    def next_frame(self, dt: float):
        with self._lock:
            if self._pending is not None:
                idx, self._pending = self._pending, None
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, f = self.cap.read()
                if ok:
                    self._frame, self._pos = f, idx
                return self._frame
            if self._paused and self._frame is not None:
                return self._frame
            n = 1
            if self._frame is not None:
                self._acc += max(0.0, dt) * self._fps * self._speed
                n = int(self._acc)
                if n <= 0:
                    return self._frame
                self._acc -= n
            for _ in range(n - 1):
                if not self.cap.grab():
                    break
            ok, f = self.cap.read()
            if not ok:
                if not self.loop:
                    return self._frame
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, f = self.cap.read()
                if not ok:
                    return self._frame
            self._frame = f
            self._pos = max(0, int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
            return self._frame

# ═══════════════════════════════════════════════════════════════════════════════
#  Uji urutan sequence (live) — tanpa Tk
# ═══════════════════════════════════════════════════════════════════════════════

ST_DONE, ST_SKIP = "done", "skip"


def compute_hits(cfg: LogicConfig, dets) -> set:
    """dets: [(x1, y1, x2, y2, nama_class, conf)] koordinat frame. Return set id ROI yang terkena."""
    hits = set()
    rois = cfg.all_rois()
    for (x1, y1, x2, y2, name, cf) in dets:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        for r in rois:
            rx1, ry1, rx2, ry2 = r.rect
            if (rx1 <= cx <= rx2 and ry1 <= cy <= ry2
                    and (not r.classes or name in r.classes) and cf >= r.conf):
                hits.add(r.id)
    return hits


# SequenceTester kini ada di logic_bridge.py (di-import di atas;
# memakai plc_engine/plc_logic).


# ═══════════════════════════════════════════════════════════════════════════════
#  Panel editor
# ═══════════════════════════════════════════════════════════════════════════════


class LogicEditorPanel(ttk.Frame):
    # Warna RGB (frame preview main.py sudah dikonversi ke RGB sebelum digambar)
    COLORS = {
        ROLE_INPUT: (60, 200, 80),
        ROLE_OUTPUT: (30, 130, 240),
        TAG_MANDATORY: (255, 120, 30),
        TAG_EXPECTED: (240, 200, 40),
    }
    HANDLE_R = 6
    HIT_MARGIN = 8

    def __init__(self, parent, preview_widget,
                 get_frame_size: Callable[[], tuple],
                 get_class_names: Callable[[], dict] = lambda: {},
                 get_mirror: Callable[[], bool] = lambda: False,
                 get_default_conf: Callable[[], float] = lambda: 0.25,
                 xml_path=DEFAULT_XML,
                 on_saved: Optional[Callable] = None,
                 on_close: Optional[Callable] = None,
                 set_status: Optional[Callable] = None,
                 get_live: Optional[Callable] = None,
                 transport: Optional["VideoTransport"] = None):
        super().__init__(parent)
        self._preview = preview_widget
        self.get_frame_size = get_frame_size
        self.get_class_names = get_class_names
        self.get_mirror = get_mirror
        self.get_default_conf = get_default_conf
        self.xml_path = Path(xml_path)
        self.on_saved = on_saved
        self.on_close = on_close
        self.set_status = set_status
        self.transport = transport
        self._tp_visible = False
        self._tp_syncing = False
        self._tp_n = -1
        self._scrubbing = False
        self._last_scrub = 0.0
        self._key_ids: list = []
        self.get_live = get_live
        self.cfg = LogicConfig()
        self._dirty = False
        self._armed: Optional[str] = None
        self._sel_id: Optional[str] = None
        self._syncing = False

        self._sx = self._sy = 1.0
        self._ox = self._oy = 0
        self._action: Optional[str] = None
        self._corner = -1
        self._drag_origin = (0, 0)
        self._orig_rect = (0, 0, 0, 0)
        self._drag_rect = None
        self._moved = False

        self._attached = False
        self._bindtag = f"LogicEditor{id(self)}"
        self._old_cursor = "crosshair"
        self._progress: dict = {}      # id ROI -> "done" | "skip"
        self._build_ui()
        self.load(silent=True)
        self._refresh()

    # ── UI ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Area aksi dipasang PERTAMA (side=bottom) agar selalu di bawah area scroll
        self._bottom = ttk.Frame(self)
        self._bottom.pack(side="bottom", fill="x")

        # Container scroll
        self._scroll_canvas = tk.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            width=300,
        )
        self._scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self._scroll_canvas.yview
        )

        self._scroll_canvas.configure(
            yscrollcommand=self._scrollbar.set
        )

        self._scrollbar.pack(side="right", fill="y")
        self._scroll_canvas.pack(side="left", fill="both", expand=True)

        # Semua widget Build UI ditempatkan di dalam frame ini
        self._ui_content = ttk.Frame(self._scroll_canvas)
        self._ui_window = self._scroll_canvas.create_window(
            (0, 0),
            window=self._ui_content,
            anchor="nw"
        )

        def _update_scroll_region(_event=None):
            self._scroll_canvas.configure(
                scrollregion=self._scroll_canvas.bbox("all")
            )

        def _fit_content_width(event):
            self._scroll_canvas.itemconfigure(
                self._ui_window,
                width=event.width
            )

        self._ui_content.bind("<Configure>", _update_scroll_region)
        self._scroll_canvas.bind("<Configure>", _fit_content_width)

        # Scroll dengan roda mouse
        def _mousewheel(event):
            if getattr(event, "delta", 0):
                self._scroll_canvas.yview_scroll(
                    int(-event.delta / 120), "units"
                )
            elif getattr(event, "num", None) == 4:
                self._scroll_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                self._scroll_canvas.yview_scroll(1, "units")
            return "break"

        self._scroll_canvas.bind("<MouseWheel>", _mousewheel)
        self._ui_content.bind("<MouseWheel>", _mousewheel)
        self._scroll_canvas.bind("<Button-4>", _mousewheel)
        self._scroll_canvas.bind("<Button-5>", _mousewheel)

        # Semua widget UI selanjutnya harus menggunakan parent ini
        ui = self._ui_content

        ttk.Label(ui, text="Logic Editor",
                font=("Arial", 11, "bold")).pack(anchor="w")

        self._dirty_var = tk.StringVar(value="")
        ttk.Label(
            ui,
            textvariable=self._dirty_var,
            foreground="#ef6c00"
        ).pack(anchor="w")

        self._build_transport_box()

        main_box = ttk.LabelFrame(
            ui, text="1. ROI Utama (wajib)", padding=6
        )
        main_box.pack(fill="x", pady=(4, 6))
        self._main_box = main_box
        self._btn_in = ttk.Button(main_box, command=lambda: self._arm(ROLE_INPUT))
        self._btn_in.pack(fill="x", pady=(0, 3), ipady=2)
        self._btn_out = ttk.Button(main_box, command=lambda: self._arm(ROLE_OUTPUT))
        self._btn_out.pack(fill="x", ipady=2)

        seq_box = ttk.LabelFrame(ui, text="2. Sequence ROI (berurutan)", padding=6)
        seq_box.pack(fill="x", pady=(0, 6))
        self._btn_seq = ttk.Button(seq_box, text="＋ Tambah Sequence ROI",
                                   command=lambda: self._arm(ROLE_SEQ))
        self._btn_seq.pack(fill="x", pady=(0, 4), ipady=2)

        tree_row = ttk.Frame(seq_box)
        tree_row.pack(fill="x")
        self.tree = ttk.Treeview(tree_row, columns=("no", "tag", "desc", "conf", "chk"),
                                 show="headings", height=9, selectmode="browse")
        for col, text, w, anchor in (("no", "#", 34, "center"), ("tag", "Tag", 70, "w"),
                                     ("desc", "Keterangan", 120, "w"), ("conf", "Conf", 38, "center"),
                                     ("chk", "✓", 30, "center")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor=anchor, stretch=(col == "desc"))
        self.tree.tag_configure("input", foreground="#2e7d32")
        self.tree.tag_configure("output", foreground="#1565c0")
        self.tree.tag_configure(TAG_MANDATORY, foreground="#e65100")
        self.tree.tag_configure(TAG_EXPECTED, foreground="#9e8600")
        sb = ttk.Scrollbar(tree_row, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Delete>", lambda e: self._delete_selected())

        row = ttk.Frame(seq_box)
        row.pack(fill="x", pady=(4, 0))
        self._btn_up = ttk.Button(row, text="▲", width=3, command=lambda: self._move(-1))
        self._btn_down = ttk.Button(row, text="▼", width=3, command=lambda: self._move(1))
        self._btn_edit = ttk.Button(row, text="Edit", command=self._edit_selected)
        self._btn_del = ttk.Button(row, text="Hapus", command=self._delete_selected)
        for b in (self._btn_up, self._btn_down):
            b.pack(side="left", padx=(0, 2))
        self._btn_edit.pack(side="left", fill="x", expand=True, padx=(4, 2))
        self._btn_del.pack(side="left", fill="x", expand=True)

        ttk.Button(self._bottom, text="⚙ Logika / Program…",
                   command=self._open_logic_settings).pack(fill="x", pady=(0, 6), ipady=2)
        self._hint_var = tk.StringVar(value="")
        ttk.Label(self._bottom, textvariable=self._hint_var, wraplength=270,
                  foreground="#555555").pack(anchor="w", pady=(0, 6))
        ttk.Button(self._bottom, text="⇄ Edit program di LD…",
                command=self._open_program_in_ld
                ).pack(fill="x", pady=(0, 6), ipady=2)
        act = ttk.Frame(self._bottom)
        act.pack(fill="x")
        # (tiga tombol Simpan / Muat / Tutup di dalam `act` tidak berubah)
        ttk.Label(self._bottom, text=f"File: {self.xml_path.name}", foreground="#777777").pack(
            anchor="w", pady=(4, 0))
        ttk.Button(act, text="💾 Simpan", command=self.save).pack(
            side="left", fill="x", expand=True, ipady=3, padx=(0, 2))
        ttk.Button(act, text="📂 Muat", command=self._load_dialog).pack(
            side="left", fill="x", expand=True, ipady=3, padx=(2, 2))
        ttk.Button(act, text="Tutup", command=self._close).pack(
            side="left", fill="x", expand=True, ipady=3, padx=(2, 0))
        ttk.Label(self, text=f"File: {self.xml_path.name}", foreground="#777777").pack(
            anchor="w", pady=(4, 0))

    # ── Kontrol video (hanya tampil bila sumber = video) ───────────────────

    _TEXT_CLASSES = ("Entry", "TEntry", "Text", "TCombobox", "TSpinbox", "TButton",
                     "TCheckbutton", "TRadiobutton", "TScale")

    def _build_transport_box(self):
        box = ttk.LabelFrame(
            self._ui_content,
            text="Transport Video",
            padding=6
        )
        self._tp_box = box
        row = ttk.Frame(box)
        row.pack(fill="x")
        self._tp_btn_b1s = ttk.Button(row, text="⏪", width=3,
                                      command=lambda: self._tp_step(-self._tp_sec()))
        self._tp_btn_bf = ttk.Button(row, text="◀", width=3, command=lambda: self._tp_step(-1))
        self._tp_btn_play = ttk.Button(row, text="⏸ Jeda", command=self._tp_toggle)
        self._tp_btn_ff = ttk.Button(row, text="▶", width=3, command=lambda: self._tp_step(1))
        self._tp_btn_f1s = ttk.Button(row, text="⏩", width=3,
                                      command=lambda: self._tp_step(self._tp_sec()))
        self._tp_btn_b1s.pack(side="left", padx=(0, 2))
        self._tp_btn_bf.pack(side="left", padx=(0, 2))
        self._tp_btn_play.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._tp_btn_ff.pack(side="left", padx=(0, 2))
        self._tp_btn_f1s.pack(side="left")

        self._tp_slider = ttk.Scale(box, from_=0, to=1, orient="horizontal",
                                    command=self._tp_on_slider)
        self._tp_slider.pack(fill="x", pady=(6, 0))
        self._tp_slider.bind("<ButtonPress-1>", lambda e: setattr(self, "_scrubbing", True), add="+")
        self._tp_slider.bind("<ButtonRelease-1>", self._tp_on_slider_release, add="+")

        self._tp_info = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._tp_info, font=("Consolas", 9)).pack(anchor="w")

        sp = ttk.Frame(box)
        sp.pack(fill="x", pady=(2, 0))
        ttk.Label(sp, text="Kecepatan:").pack(side="left")
        self._tp_speed = ttk.Combobox(sp, values=[f"{v:g}x" for v in VideoTransport.SPEEDS],
                                      state="readonly", width=6)
        self._tp_speed.set("1x")
        self._tp_speed.pack(side="left", padx=(6, 0))
        self._tp_speed.bind("<<ComboboxSelected>>", self._tp_on_speed)

        ttk.Label(box, text="Spasi: jeda · ←/→: 1 frame · Shift+←/→: 1 detik",
                  foreground="#777777", wraplength=270).pack(anchor="w", pady=(4, 0))

    def _video_active(self) -> bool:
        t = self.transport
        return bool(t is not None and self._attached and t.is_video())

    def _tp_sec(self) -> int:
        return max(1, int(round(self.transport.fps())))

    def _tp_step(self, n):
        if self._video_active():
            self.transport.step(n)

    def _tp_toggle(self):
        if self._video_active():
            self.transport.set_paused(not self.transport.is_paused())

    def _tp_on_slider(self, value):
        if self._tp_syncing or not self._scrubbing or not self._video_active():
            return
        now = time.perf_counter()
        if now - self._last_scrub < 0.08:          # batasi seek saat digeser
            return
        self._last_scrub = now
        self.transport.set_paused(True)
        self.transport.seek(int(float(value)))

    def _tp_on_slider_release(self, _=None):
        self._scrubbing = False
        if not self._video_active():
            return
        target = int(float(self._tp_slider.get()))
        if target != self.transport.position():
            self.transport.set_paused(True)
            self.transport.seek(target)

    def _tp_on_speed(self, _=None):
        if self._video_active():
            self.transport.set_speed(float(self._tp_speed.get().rstrip("x")))

    @staticmethod
    def _fmt_time(sec: float) -> str:
        sec = max(0.0, sec)
        return f"{int(sec // 60):02d}:{sec % 60:05.2f}"

    def _update_transport_ui(self):
        active = self._video_active()
        if active != self._tp_visible:
            if active:
                self._tp_box.pack(fill="x", pady=(4, 0), before=self._main_box)
            else:
                self._tp_box.pack_forget()
            self._tp_visible = active
        if not active:
            return
        t = self.transport
        text = "▶ Putar" if t.is_paused() else "⏸ Jeda"
        if self._tp_btn_play.cget("text") != text:
            self._tp_btn_play.config(text=text)
        n, pos, fps = t.frame_count(), t.position(), t.fps()
        self._tp_syncing = True
        try:
            if n != self._tp_n:
                self._tp_n = n
                self._tp_slider.config(to=max(1, n - 1), state="normal" if n > 1 else "disabled")
            if n > 1 and not self._scrubbing:
                self._tp_slider.set(pos)
        finally:
            self._tp_syncing = False
        total = f"{n}" if n > 0 else "?"
        self._tp_info.set(f"Frame {pos + 1}/{total}   {self._fmt_time(pos / fps)}"
                          + (f" / {self._fmt_time(n / fps)}" if n > 0 else ""))
        want = f"{t.get_speed():g}x"
        if self._tp_speed.get() != want:
            self._tp_speed.set(want)

    def _key_handler(self, action):
        def handler(event):
            if not self._video_active():
                return None
            cls = getattr(event.widget, "winfo_class", lambda: "")()
            if cls in self._TEXT_CLASSES:
                return None
            action()
            return "break"
        return handler

    def _bind_keys(self):
        top = self.winfo_toplevel()
        specs = (("<space>", self._tp_toggle),
                 ("<Left>", lambda: self._tp_step(-1)),
                 ("<Right>", lambda: self._tp_step(1)),
                 ("<Shift-Left>", lambda: self._tp_step(-self._tp_sec() if self._video_active() else 0)),
                 ("<Shift-Right>", lambda: self._tp_step(self._tp_sec() if self._video_active() else 0)))
        for seq, act in specs:
            self._key_ids.append((seq, top.bind(seq, self._key_handler(act), add="+")))

    def _unbind_keys(self):
        top = self.winfo_toplevel()
        for seq, fid in self._key_ids:
            try:
                top.unbind(seq, fid)
            except tk.TclError:
                pass
        self._key_ids = []

    # ── API untuk main.py ──────────────────────────────────────────────────

    def attach(self):
        """Ambil alih mouse di preview. Handler lama (ROIOverlay) dilewati lewat 'break'."""
        if self._attached:
            return
        self._attached = True
        w = self._preview
        self._old_cursor = str(w.cget("cursor")) or "crosshair"
        w.bindtags((self._bindtag,) + tuple(w.bindtags()))
        w.bind_class(self._bindtag, "<ButtonPress-1>", self._on_press)
        w.bind_class(self._bindtag, "<B1-Motion>", self._on_drag)
        w.bind_class(self._bindtag, "<ButtonRelease-1>", self._on_release)
        w.config(cursor="arrow")
        self._bind_keys()

    def detach(self):
        if not self._attached:
            return
        self._attached = False
        self._armed = None
        self._action = None
        self._drag_rect = None
        w = self._preview
        w.bindtags(tuple(t for t in w.bindtags() if t != self._bindtag))
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            w.unbind_class(self._bindtag, seq)
        w.config(cursor=self._old_cursor)
        self._unbind_keys()
        self._update_transport_ui()
        self._refresh()

    def set_view(self, preview_w, preview_h, frame_w, frame_h, offset_x, offset_y):
        if preview_w and preview_h:
            self._sx = frame_w / preview_w
            self._sy = frame_h / preview_h
        self._ox, self._oy = offset_x, offset_y
        self._sync_to_frame()
        self._update_transport_ui()

    def is_dirty(self) -> bool:
        return self._dirty
    def _chk_text(self, rid) -> str:
        st = self._progress.get(rid)
        return "✓" if st == "done" else ("–" if st == "skip" else "")

    def set_progress(self, status: dict):
        """status: {id_roi: (ST_DONE|ST_SKIP, detik)} dari SequenceTester.status.
        Hanya mengubah sel kolom ✓ (tanpa membangun ulang tabel, jadi seleksi tetap)."""
        new = {rid: v[0] for rid, v in status.items()}
        if new == self._progress:
            return
        self._progress = new
        for rid in self.tree.get_children():
            self.tree.set(rid, "chk", self._chk_text(rid))
    def confirm_close(self) -> bool:
        """True bila boleh ditutup. Menanyakan simpan bila ada perubahan."""
        if not self._dirty:
            return True
        ans = messagebox.askyesnocancel(
            "Logic Editor", "Ada perubahan yang belum disimpan. Simpan dulu?")
        if ans is None:
            return False
        return self.save() if ans else True

    # ── Koordinat ──────────────────────────────────────────────────────────

    def _pos(self, event):
        return event.x - self._ox, event.y - self._oy

    def _to_prev(self, rect):
        x1, y1, x2, y2 = rect
        return (int(x1 / self._sx), int(y1 / self._sy),
                int(x2 / self._sx), int(y2 / self._sy))

    def _to_frame(self, rect):
        x1, y1, x2, y2 = rect
        return (x1 * self._sx, y1 * self._sy, x2 * self._sx, y2 * self._sy)

    def _clamp(self, rect):
        fw, fh = self.get_frame_size()
        x1, y1, x2, y2 = (int(v) for v in rect)
        if fw and fh:
            x1, x2 = max(0, min(fw, x1)), max(0, min(fw, x2))
            y1, y2 = max(0, min(fh, y1)), max(0, min(fh, y2))
        return (x1, y1, x2, y2)

    def _sync_to_frame(self):
        fw, fh = self.get_frame_size()
        if not (fw and fh):
            return
        mirror = bool(self.get_mirror())
        cfg = self.cfg
        if not cfg.all_rois():
            cfg.frame_w, cfg.frame_h, cfg.mirror = fw, fh, mirror
            return
        if (cfg.frame_w, cfg.frame_h, cfg.mirror) == (fw, fh, mirror):
            return
        notes = adapt_to_frame(cfg, fw, fh, mirror)
        if notes:
            self._mark_dirty()
            self._say("ROI disesuaikan: " + ", ".join(notes))

    # ── Mouse ──────────────────────────────────────────────────────────────

    def _hit(self, mx, my):
        rois = self.cfg.all_rois()
        order = sorted(reversed(rois), key=lambda r: r.id != self._sel_id)  # terpilih dulu
        rr = self.HANDLE_R + self.HIT_MARGIN
        for r in order:
            px1, py1, px2, py2 = self._to_prev(r.rect)
            if r.id == self._sel_id:
                for i, (cx, cy) in enumerate(((px1, py1), (px2, py1), (px2, py2), (px1, py2))):
                    if abs(mx - cx) <= rr and abs(my - cy) <= rr:
                        return r, "resize", i
            if px1 <= mx <= px2 and py1 <= my <= py2:
                return r, "move", -1
        return None, None, -1

    def _on_press(self, event):
        mx, my = self._pos(event)
        self._moved = False
        if self._armed:
            self._action = "draw"
            self._drag_origin = (mx, my)
            self._drag_rect = None
            return "break"
        roi, action, corner = self._hit(mx, my)
        self._select(roi.id if roi else None)
        if roi is None:
            self._action = None
            return "break"
        self._action, self._corner = action, corner
        self._drag_origin = (mx, my)
        self._orig_rect = tuple(roi.rect)
        return "break"

    def _on_drag(self, event):
        mx, my = self._pos(event)
        if self._action == "draw":
            x0, y0 = self._drag_origin
            self._drag_rect = (min(x0, mx), min(y0, my), max(x0, mx), max(y0, my))
        elif self._action in ("move", "resize"):
            roi = self.cfg.find(self._sel_id)
            if roi is None:
                return "break"
            dx = (mx - self._drag_origin[0]) * self._sx
            dy = (my - self._drag_origin[1]) * self._sy
            x1, y1, x2, y2 = self._orig_rect
            if self._action == "move":
                fw, fh = self.get_frame_size()
                w, h = x2 - x1, y2 - y1
                nx = x1 + dx
                ny = y1 + dy
                if fw and fh:
                    nx = max(0, min(nx, fw - w))
                    ny = max(0, min(ny, fh - h))
                new = (nx, ny, nx + w, ny + h)
            else:
                nx1, ny1, nx2, ny2 = x1, y1, x2, y2
                c = self._corner
                if c == 0:
                    nx1, ny1 = x1 + dx, y1 + dy
                elif c == 1:
                    nx2, ny1 = x2 + dx, y1 + dy
                elif c == 2:
                    nx2, ny2 = x2 + dx, y2 + dy
                else:
                    nx1, ny2 = x1 + dx, y2 + dy
                new = (min(nx1, nx2), min(ny1, ny2), max(nx1, nx2), max(ny1, ny2))
            roi.rect = self._clamp(new)
            self._moved = True
        return "break"

    def _on_release(self, event):
        if self._action == "draw":
            rect, self._drag_rect, self._action = self._drag_rect, None, None
            if rect and (rect[2] - rect[0]) >= MIN_ROI_PX and (rect[3] - rect[1]) >= MIN_ROI_PX:
                self._finish_draw(self._clamp(self._to_frame(rect)))
        elif self._action in ("move", "resize"):
            self._action = None
            if self._moved:
                self._mark_dirty()
        return "break"

    # ── Aksi ───────────────────────────────────────────────────────────────

    def _arm(self, role):
        fw, fh = self.get_frame_size()
        if not (fw and fh):
            messagebox.showinfo("Logic Editor", "Buka kamera / video dulu sebelum menggambar ROI.")
            return
        if role == ROLE_SEQ and not (self.cfg.input and self.cfg.output):
            return
        self._armed = None if self._armed == role else role
        if self._armed and self._video_active() and not self.transport.is_paused():
            self.transport.set_paused(True)        # frame diam saat menggambar
        self._preview.config(cursor="crosshair" if self._armed else "arrow")
        label = {ROLE_INPUT: "Input", ROLE_OUTPUT: "Output", ROLE_SEQ: "Sequence ROI"}
        self._say(f"Drag di preview untuk menggambar {label[role]}. "
                  "Klik tombol yang sama untuk batal." if self._armed else "")
        self._refresh()

    def _disarm(self):
        self._armed = None
        self._preview.config(cursor="arrow")
        self._say("")

    def _class_names(self) -> list:
        return [n for _, n in sorted(self.get_class_names().items())]

    def _finish_draw(self, rect):
        role = self._armed
        self._disarm()
        existing = {ROLE_INPUT: self.cfg.input, ROLE_OUTPUT: self.cfg.output}.get(role)
        title = {ROLE_INPUT: "ROI Input", ROLE_OUTPUT: "ROI Output",
                 ROLE_SEQ: f"Sequence ROI {len(self.cfg.sequence) + 1}"}[role]
        res = ask_roi_properties(self.winfo_toplevel(), title, self._class_names(),
                                 role == ROLE_SEQ, init=existing,
                                 default_conf=self.get_default_conf())
        if res is None:
            self._refresh()
            return
        if role == ROLE_INPUT:
            roi = RoiDef("in", ROLE_INPUT, rect, res["classes"], res["conf"])
            self.cfg.input = roi
        elif role == ROLE_OUTPUT:
            roi = RoiDef("out", ROLE_OUTPUT, rect, res["classes"], res["conf"])
            self.cfg.output = roi
        else:
            roi = RoiDef(self.cfg.next_seq_id(), ROLE_SEQ, rect, res["classes"], res["conf"],
                         res["tag"], res["description"])
            self.cfg.sequence.append(roi)
        fw, fh = self.get_frame_size()
        self.cfg.frame_w, self.cfg.frame_h = fw, fh
        self._apply_adv(roi, res)
        self.cfg.mirror = bool(self.get_mirror())
        self._sel_id = roi.id
        self._mark_dirty()
        self._refresh()

    def _edit_selected(self):
        r = self.cfg.find(self._sel_id)
        if r is None:
            return
        res = ask_roi_properties(self.winfo_toplevel(), f"Edit {r.id}", self._class_names(),
                                 r.role == ROLE_SEQ, init=r, default_conf=r.conf)
        if res is None:
            return
        r.classes, r.conf = res["classes"], res["conf"]
        self._apply_adv(r, res)
        if r.role == ROLE_SEQ:
            r.tag, r.description = res["tag"], res["description"]
        self._mark_dirty()
        self._refresh()

    def _delete_selected(self):
        r = self.cfg.find(self._sel_id)
        if r is None or r.role != ROLE_SEQ:
            return
        if not messagebox.askyesno("Hapus ROI", f"Hapus sequence '{r.description}'?"):
            return
        self.cfg.sequence.remove(r)
        self._sel_id = None
        self._mark_dirty()
        self._refresh()

    def _move(self, delta):
        r = self.cfg.find(self._sel_id)
        if r is None or r.role != ROLE_SEQ:
            return
        seq = self.cfg.sequence
        i = seq.index(r)
        j = i + delta
        if 0 <= j < len(seq):
            seq[i], seq[j] = seq[j], seq[i]
            self._mark_dirty()
            self._refresh()

    # ── Seleksi & refresh ──────────────────────────────────────────────────

    def _select(self, rid):
        self._sel_id = rid
        self._refresh()

    def _on_tree_select(self, _=None):
        if self._syncing:
            return
        sel = self.tree.selection()
        self._sel_id = sel[0] if sel else None
        self._update_buttons()

    def _refresh(self):
        self._refresh_tree()
        self._update_buttons()
        self._dirty_var.set("● belum disimpan" if self._dirty else "")

    def _refresh_tree(self):
        self._syncing = True
        try:
            self.tree.delete(*self.tree.get_children())
            cfg = self.cfg
            if cfg.input:
                self.tree.insert("", "end", iid=cfg.input.id, tags=("input",),
                                 values=("IN", "input", "Ambil barang", f"{cfg.input.conf:.2f}",
                                         self._chk_text(cfg.input.id)))
            for i, r in enumerate(cfg.sequence, 1):
                self.tree.insert("", "end", iid=r.id, tags=(r.tag,),
                                 values=(i, r.tag, r.description, f"{r.conf:.2f}",
                                         self._chk_text(r.id)))
            if cfg.output:
                self.tree.insert("", "end", iid=cfg.output.id, tags=("output",),
                                 values=("OUT", "output", "Taruh barang", f"{cfg.output.conf:.2f}",
                                         self._chk_text(cfg.output.id)))
            if self._sel_id and self.tree.exists(self._sel_id):
                self.tree.selection_set(self._sel_id)
            else:
                self._sel_id = None
        finally:
            self._syncing = False

    def _update_buttons(self):
        cfg = self.cfg
        self._btn_in.config(text=("↻ Ganti ROI Input ✓" if cfg.input else "▢ Gambar ROI Input")
                            + ("  [aktif]" if self._armed == ROLE_INPUT else ""))
        self._btn_out.config(text=("↻ Ganti ROI Output ✓" if cfg.output else "▢ Gambar ROI Output")
                             + ("  [aktif]" if self._armed == ROLE_OUTPUT else ""))
        both = bool(cfg.input and cfg.output)
        self._btn_seq.config(state="normal" if both else "disabled",
                             text="＋ Tambah Sequence ROI" + ("  [aktif]" if self._armed == ROLE_SEQ else ""))
        if not both:
            self._say_once("Buat ROI Input dan Output dulu sebelum menambah sequence.")
        r = cfg.find(self._sel_id)
        is_seq = bool(r and r.role == ROLE_SEQ)
        idx = cfg.sequence.index(r) if is_seq else -1
        self._btn_up.config(state="normal" if is_seq and idx > 0 else "disabled")
        self._btn_down.config(state="normal" if is_seq and idx < len(cfg.sequence) - 1 else "disabled")
        self._btn_edit.config(state="normal" if r else "disabled")
        self._btn_del.config(state="normal" if is_seq else "disabled")

    def _say_once(self, text):
        if not self._hint_var.get():
            self._hint_var.set(text)

    def _say(self, text):
        self._hint_var.set(text)
        if text and self.set_status:
            self.set_status(text)

    def _mark_dirty(self):
        self._dirty = True
        self._dirty_var.set("● belum disimpan")

    # ── Simpan / muat ──────────────────────────────────────────────────────

    def _apply_adv(self, roi, res):
        roi.mode = res.get("mode", "center")
        roi.min_overlap = res.get("min_overlap", 0.4)
        roi.priority = res.get("priority", 0)
        roi.group = res.get("group", "")
        roi.dwell = res.get("dwell", 0.0)
        roi.grace = res.get("grace", 0.0)

    def _open_logic_settings(self):
        cfg = self.cfg
        top = tk.Toplevel(self.winfo_toplevel())
        top.title("Logika / Program")
        top.resizable(False, False)
        top.transient(self.winfo_toplevel())
        top.grab_set()
        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)

        skip = tk.StringVar(value=cfg.skip_mode)
        ttk.Label(body, text="Expected yang terlewat:", font=("Arial", 10, "bold")).pack(anchor="w")
        ttk.Radiobutton(body, text="immediate — skip begitu step setelahnya selesai (urutan ketat)",
                        variable=skip, value="immediate").pack(anchor="w", padx=8)
        ttk.Radiobutton(body, text="final — masih boleh diselesaikan, skip baru dihitung saat Output",
                        variable=skip, value="final").pack(anchor="w", padx=8)

        restart = tk.BooleanVar(value=cfg.restart_on_input)
        single = tk.BooleanVar(value=cfg.single)
        ttk.Checkbutton(body, text="Masuk ROI Input lagi saat berjalan = mulai siklus baru",
                        variable=restart).pack(anchor="w", pady=(10, 0))
        ttk.Checkbutton(body, text="Satu deteksi hanya boleh mengenai satu ROI (terbaik)",
                        variable=single).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Maks. deteksi dipakai (0 = semua):").pack(side="left")
        maxdet = tk.StringVar(value=str(cfg.max_det))
        ttk.Entry(row, textvariable=maxdet, width=5).pack(side="left", padx=(6, 0))

        prog_lbl = ttk.Label(body, text="")
        prog_lbl.pack(anchor="w", pady=(10, 0))

        def _upd():
            prog_lbl.config(text="Program kustom: AKTIF (template diabaikan)" if cfg.program.strip()
                            else "Program kustom: tidak dipakai (pakai template)")
        _upd()

        def _edit_prog():
            self._open_program_editor(top)
            _upd()

        ttk.Button(body, text="Edit program teks…", command=_edit_prog).pack(fill="x", pady=(4, 0))
        err = ttk.Label(body, text="", foreground="#c62828")
        err.pack(anchor="w", pady=(6, 0))

        def _ok():
            try:
                md = int(maxdet.get() or 0)
                if md < 0:
                    raise ValueError
            except ValueError:
                err.config(text="Maks. deteksi harus bilangan bulat >= 0.")
                return
            new = (skip.get(), restart.get(), single.get(), md)
            if new != (cfg.skip_mode, cfg.restart_on_input, cfg.single, cfg.max_det):
                cfg.skip_mode, cfg.restart_on_input, cfg.single, cfg.max_det = new
                self._mark_dirty()
            top.destroy()

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="OK", width=8, command=_ok).pack(side="left")
        ttk.Button(btns, text="Batal", width=8, command=top.destroy).pack(side="left", padx=(6, 0))
        top.wait_window()

    def _open_program_editor(self, parent):
        import dataclasses
        cfg = self.cfg
        top = tk.Toplevel(parent)
        top.title("Program PLC (teks)")
        top.transient(parent)
        top.grab_set()
        help_txt = ("nama = ekspresi   (satu baris satu node, urutan baris = urutan evaluasi)\n"
                    "$roi = bit ROI | nama@ = nilai scan lalu | & | ^ ! atau AND OR XOR NOT, ( )\n"
                    "TON/TOF/TP(sinyal, detik)  R_TRIG/F_TRIG(sinyal)  SR/RS(set, reset)  "
                    "CTU(naik, reset, n)  NAND/NOR(...)\n"
                    "Konvensi: done_<roi> / skip_<roi> tampil di tabel; start, run, done_<output> = waktu siklus.\n"
                    "Komentar dengan #.")
        ttk.Label(top, text=help_txt, foreground="#555555", justify="left").pack(
            anchor="w", padx=10, pady=(10, 4))
        frame = ttk.Frame(top)
        frame.pack(fill="both", expand=True, padx=10)
        txt = tk.Text(frame, width=92, height=26, font=("Consolas", 10), undo=True, wrap="none")
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", cfg.program)
        msg = ttk.Label(top, text="", justify="left", wraplength=640)
        msg.pack(anchor="w", padx=10, pady=(6, 0))

        def _body() -> str:
            return txt.get("1.0", "end").strip("\n")

        def _from_template():
            if not (cfg.input and cfg.output):
                msg.config(text="Buat ROI Input dan Output dulu.", foreground="#c62828")
                return
            if _body().strip() and not messagebox.askyesno(
                    "Program", "Ganti isi editor dengan hasil template?", parent=top):
                return
            txt.delete("1.0", "end")
            txt.insert("1.0", template_text(cfg))

        def _check() -> bool:
            probe = dataclasses.replace(cfg, program=_body())
            errs, warns = check_config(probe)
            if errs:
                msg.config(text="\n".join(errs), foreground="#c62828")
                return False
            msg.config(text="OK" + ("".join("\n• " + w for w in warns)), foreground="#2e7d32")
            return True

        def _save():
            body = _body()
            if body.strip() and not _check():
                return
            new = body if body.strip() else ""
            if new != cfg.program:
                cfg.program = new
                self._mark_dirty()
            top.destroy()

        def _clear():
            txt.delete("1.0", "end")
        def _open_fbd():
            try:
                from plc_editor import Editor
            except ImportError as e:
                messagebox.showerror("FBD", f"plc_editor.py tidak ditemukan:\n{e}", parent=top)
                return
            src = _body()
            if not src.strip() and cfg.input and cfg.output:
                src = template_text(cfg)            # teks kosong = template yang sedang aktif

            def apply(text):
                txt.delete("1.0", "end")
                txt.insert("1.0", text)
                _check()

            try:
                ed = Editor(top, program=src, roi_names=[r.id for r in cfg.all_rois()],
                            on_apply=apply, live=self.get_live)
            except Exception as e:
                messagebox.showerror("FBD", f"Program tidak bisa divisualkan:\n{e}", parent=top)
                return
            ed.wait_window()
            top.grab_set()
        def _open_export():
            src = _body()
            if not src.strip():
                if not (cfg.input and cfg.output):
                    msg.config(text="Buat ROI Input dan Output dulu.", foreground="#c62828")
                    return
                src = template_text(cfg)            # program kosong = template aktif
            try:
                from plc_export import ExportViewer
                v = ExportViewer(top, src, live=self.get_live)
            except Exception as e:
                messagebox.showerror("ST / LD", str(e), parent=top)
                return
            v.transient(top); v.grab_set(); v.wait_window(); top.grab_set()
        btns = ttk.Frame(top)
        btns.pack(fill="x", padx=10, pady=10)
        ttk.Button(btns, text="Isi dari template", command=_from_template).pack(side="left")
        ttk.Button(btns, text="FBD", command=_open_fbd).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="ST / LD", command=_open_export).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Validasi", command=_check).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Kosongkan (pakai template)", command=_clear).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Simpan", command=_save).pack(side="right")
        ttk.Button(btns, text="Batal", command=top.destroy).pack(side="right", padx=(0, 6))
        top.wait_window()

    def save(self) -> bool:
        fw, fh = self.get_frame_size()
        if fw and fh:
            self.cfg.frame_w, self.cfg.frame_h = fw, fh
            self.cfg.mirror = bool(self.get_mirror())
        errors, warnings = validate(self.cfg, (fw, fh))
        if errors:
            messagebox.showerror("Simpan gagal", "\n".join(f"• {e}" for e in errors))
            return False
        if warnings and not messagebox.askyesno(
                "Peringatan", "\n".join(f"• {w}" for w in warnings) + "\n\nTetap simpan?"):
            return False
        try:
            save_logic_xml(self.xml_path, self.cfg)
        except Exception as e:
            messagebox.showerror("Simpan gagal", str(e))
            return False
        self._dirty = False
        self._refresh()
        self._say(f"Tersimpan: {self.xml_path.name} "
                  f"({len(self.cfg.sequence)} sequence ROI)")
        if self.on_saved:
            self.on_saved(self.xml_path)
        return True

    def load(self, path=None, silent=False) -> bool:
        path = Path(path) if path else self.xml_path
        if not path.exists():
            if not silent:
                messagebox.showinfo("Muat", f"File tidak ditemukan:\n{path}")
            return False
        try:
            cfg = load_logic_xml(path)
        except Exception as e:
            if not silent:
                messagebox.showerror("Muat gagal", str(e))
            return False
        self.cfg = cfg
        self._sel_id = None
        self._dirty = False
        self._sync_to_frame()
        self._refresh()
        self._say(f"Dimuat: {path.name}")
        return True

    def _load_dialog(self):
        if not self.confirm_close():
            return
        path = filedialog.askopenfilename(
            title="Muat logic_config", initialdir=str(self.xml_path.parent),
            filetypes=[("XML", "*.xml"), ("All", "*.*")])
        if path:
            self.load(path)

    def _close(self):
        if self.confirm_close() and self.on_close:
            self.on_close()

    # ── Gambar di preview ──────────────────────────────────────────────────

    def draw_on_frame(self, frame):
        """Gambar semua ROI di atas frame preview (RGB, sudah di-resize)."""
        if not self._attached:
            return frame
        seq_no = {r.id: i for i, r in enumerate(self.cfg.sequence, 1)}
        for r in self.cfg.all_rois():
            x1, y1, x2, y2 = self._to_prev(r.rect)
            color = self.COLORS[r.role if r.role != ROLE_SEQ else r.tag]
            sel = r.id == self._sel_id
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3 if sel else 2)
            if r.role == ROLE_INPUT:
                label = "IN"
            elif r.role == ROLE_OUTPUT:
                label = "OUT"
            else:
                label = f"{seq_no[r.id]}:{'M' if r.tag == TAG_MANDATORY else 'E'}"
            ty = y1 - 5 if y1 > 18 else y1 + 16
            cv2.putText(frame, label, (x1 + 3, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, label, (x1 + 3, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        color, 1, cv2.LINE_AA)
            if sel:
                for cx, cy in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
                    cv2.circle(frame, (cx, cy), self.HANDLE_R, (255, 255, 255), -1)
                    cv2.circle(frame, (cx, cy), self.HANDLE_R, color, 2)
        if self._drag_rect:
            x1, y1, x2, y2 = self._drag_rect
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 2)
        return frame
    def _open_program_in_ld(self):
        from tkinter import messagebox
        # Konfirmasi dulu
        if not messagebox.askyesno(
                "Edit di LD",
                "Program LD akan menggantikan program teks saat ini.\n"
                "Setelah diedit, cfg.program berubah dan template tidak lagi dipakai.\n\n"
                "Lanjutkan?", parent=self):
            return
        open_program_in_ld(self.winfo_toplevel(), self.cfg,
                        on_applied=lambda c: self._mark_dirty())
        self._refresh()
# ═══════════════════════════════════════════════════════════════════════════════
#  Uji mandiri (tanpa main.py)
# ═══════════════════════════════════════════════════════════════════════════════


def _run_standalone(args):
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title("Logic Editor (mandiri)")
    root.geometry("1280x720")

    # ── State sumber, model, deteksi ──
    src = {"cap": None, "still": None, "transport": None}
    size = {"w": 0, "h": 0}
    names = {i: c.strip() for i, c in enumerate(args.classes.split(",")) if c.strip()}
    det = {"model": None, "busy": False, "result": [], "key": None, "frame_no": 0, "t_last": 0.0,
           "seq": 0, "t": 0.0, "seen": -1}
    infer_queue = queue.Queue(maxsize=1)
    status = tk.StringVar(value="Siap")
    video_var = tk.StringVar(value="Video: (belum dimuat)")
    model_var = tk.StringVar(value="Model: (belum dimuat)")
    det_on = tk.BooleanVar(value=True)
    det_conf = tk.DoubleVar(value=0.25)
    det_rate = tk.DoubleVar(value=7)
    seq_on = tk.BooleanVar(value=True)
    seq_var = tk.StringVar(value="")
    tester = SequenceTester()
    # ── Toolbar atas ──
    toolbar = ttk.Frame(root)
    toolbar.pack(side="top", fill="x", padx=6, pady=(6, 0))
    ttk.Button(toolbar, text="🎬 Load Video", command=lambda: ask_video()).pack(side="left", ipady=2)
    ttk.Label(toolbar, textvariable=video_var, foreground="#555555").pack(side="left", padx=(8, 20))
    ttk.Button(toolbar, text="🧠 Load Model", command=lambda: ask_model()).pack(side="left", ipady=2)
    ttk.Label(toolbar, textvariable=model_var, foreground="#555555").pack(side="left", padx=(8, 20))
    ttk.Checkbutton(toolbar, text="Deteksi YOLO", variable=det_on,
                    command=lambda: det.update(result=[], key=None)).pack(side="left")
    ttk.Label(toolbar, text="Conf:").pack(side="left", padx=(12, 2))
    conf_lbl = ttk.Label(toolbar, text="0.25", width=5)
    ttk.Scale(toolbar, from_=0.05, to=0.95, variable=det_conf, length=140,
              command=lambda _=None: (conf_lbl.config(text=f"{det_conf.get():.2f}"),
                                      det.update(key=None))).pack(side="left")
    conf_lbl.pack(side="left", padx=(4, 0))
    ttk.Label(toolbar, text="Deteksi/dtk:").pack(side="left", padx=(12, 2))
    rate_lbl = ttk.Label(toolbar, text="7", width=4)
    ttk.Scale(toolbar, from_=1, to=30, variable=det_rate, length=120,
              command=lambda _=None: rate_lbl.config(
                  text="maks" if det_rate.get() >= 29.5 else f"{det_rate.get():.0f}")
              ).pack(side="left")
    rate_lbl.pack(side="left", padx=(4, 0))

    ttk.Label(root, textvariable=status, relief="sunken", anchor="w").pack(side="bottom", fill="x")
    seq_box = ttk.LabelFrame(root, text="Uji urutan (live)", padding=6)
    seq_box.pack(side="bottom", fill="x", padx=6)
    ttk.Label(seq_box, textvariable=seq_var, font=("Consolas", 10),
              justify="left").pack(anchor="w")
    container = tk.Frame(root, bg="black")
    container.pack(side="left", fill="both", expand=True, padx=6, pady=6)
    container.pack_propagate(False)
    preview = tk.Label(container, bg="black", fg="white",
                       text="Klik “Load Video” untuk mulai")
    preview.pack(fill="both", expand=True)
    def get_local_monitor():
        pipe = tester.pipe

        if pipe is None:
            return None

        return {
            "dbg": pipe.debug(),
            "state": tester.state,
            "cycles": tester.cycles,
            "cycle_time": getattr(pipe, "cycle_time", None),
            "last_cycle": tester.last_cycle,
            "msg": tester.render(),
            "wall": time.time(),
        }
    panel = LogicEditorPanel(
        root, preview,
        get_frame_size=lambda: (size["w"], size["h"]),
        get_class_names=lambda: dict(names),
        get_mirror=lambda: args.mirror,
        xml_path=args.xml,
        on_close=root.destroy,
        set_status=status.set,
        get_live=get_local_monitor,
        transport=None,
    )
    panel.pack(side="right", fill="y", padx=6, pady=6)
    panel.attach()
    root.protocol("WM_DELETE_WINDOW", lambda: root.destroy() if panel.confirm_close() else None)

    # ── Sumber ──
    def release_source():
        if src["cap"] is not None:
            src["cap"].release()
        src.update(cap=None, still=None, transport=None)
        panel.transport = None
        panel._tp_n = -1
        size["w"] = size["h"] = 0
        tester.reset()
        det.update(result=[], key=None)

    def set_video(path) -> bool:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            messagebox.showerror("Load Video", f"Gagal membuka video:\n{path}")
            return False
        release_source()
        src["cap"] = cap
        src["transport"] = VideoTransport(cap)
        panel.transport = src["transport"]
        video_var.set(f"Video: {Path(path).name}")
        status.set(f"Video dimuat: {Path(path).name}")
        return True

    def set_image(path) -> bool:
        img = cv2.imread(str(path))
        if img is None:
            messagebox.showerror("Load Gambar", f"Gagal membaca gambar:\n{path}")
            return False
        release_source()
        src["still"] = img
        video_var.set(f"Gambar: {Path(path).name}")
        return True

    def set_camera(index) -> bool:
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            messagebox.showerror("Kamera", f"Gagal membuka kamera {index}.")
            return False
        release_source()
        src["cap"] = cap
        video_var.set(f"Kamera: {index}")
        return True

    def ask_video():
        path = filedialog.askopenfilename(
            title="Pilih video",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm"),
                       ("Semua file", "*.*")])
        if path:
            set_video(path)
        root.focus_set()

    # ── Model ──
    def load_model(path) -> bool:
        
        try:
            from ultralytics import YOLO
        except ImportError:
            messagebox.showerror("Load Model",
                                 "Paket 'ultralytics' belum terpasang.\n\npip install ultralytics")
            return False
        root.config(cursor="watch")
        status.set(f"Memuat model {Path(path).name} ...")
        root.update_idletasks()
        try:
            import torch
            torch.set_num_threads(2)                       # sisakan core untuk UI & decoder video
            model = YOLO(str(path))
            model_names = model.names
        except Exception as e:
            messagebox.showerror("Load Model", f"Gagal memuat model:\n{e}")
            status.set("Gagal memuat model")
            return False
        finally:
            root.config(cursor="")
        if isinstance(model_names, (list, tuple)):
            model_names = dict(enumerate(model_names))
        names.clear()
        names.update({int(k): str(v) for k, v in dict(model_names).items()})
        det.update(model=model, result=[], key=None)
        model_var.set(f"Model: {Path(path).name} ({len(names)} class)")
        status.set(f"Model aktif: {', '.join(names.values())}")
        return True

    def ask_model():
        path = filedialog.askopenfilename(
            title="Pilih model",
            filetypes=[("Model YOLO", "*.pt *.onnx *.engine *.torchscript"),
                       ("Semua file", "*.*")])
        if path:
            load_model(path)
        root.focus_set()

    # ── Inferensi di thread terpisah (UI tidak macet) ──
    def _infer(frame_bgr, conf, key, t_frame=0.0):
        try:
            res = det["model"].predict(frame_bgr, conf=conf, verbose=False)[0]
            out = []
            for b in res.boxes:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                cid = int(b.cls[0])
                out.append((x1, y1, x2, y2, names.get(cid, str(cid)), float(b.conf[0])))
            det["result"] = out
            det["key"] = key
            det["t"] = t_frame
            det["seq"] += 1
        except Exception as e:
            det["result"] = []
            det["key"] = key
            msg = f"Error deteksi: {e}"
            root.after(0, lambda m=msg: status.set(m))
        finally:
            det["busy"] = False


    def maybe_detect(frame_bgr):
        if det["model"] is None or not det_on.get() or det["busy"]:
            return

        tp = src["transport"]

        if tp is not None and tp.is_paused():
            key = ("v", tp.position(), round(det_conf.get(), 2))
            if det["key"] == key:
                return
        elif src["still"] is not None:
            key = ("i", round(det_conf.get(), 2))
            if det["key"] == key:
                return
        else:
            key = None
            rate = det_rate.get()
            if (
                rate < 29.5
                and time.perf_counter() - det["t_last"]
                < 1.0 / max(1.0, rate)
            ):
                return

        tfr = (
            tp.position() / tp.fps()
            if tp is not None
            else time.perf_counter()
        )

        task = (
            frame_bgr.copy(),
            float(det_conf.get()),
            key,
            tfr,
        )

        # Jangan antrekan frame jika worker masih memiliki tugas.
        try:
            infer_queue.put_nowait(task)
        except queue.Full:
            del task
            return

        det["busy"] = True
        det["t_last"] = time.perf_counter()

    def inference_worker():
        while True:
            task = infer_queue.get()

            try:
                if task is None:
                    return

                frame_bgr, conf, key, t_frame = task
                _infer(frame_bgr, conf, key, t_frame)

            except Exception as e:
                print(f"Worker YOLO error: {e}")
                det["busy"] = False

            finally:
                infer_queue.task_done()

    worker_thread = threading.Thread(
        target=inference_worker,
        name="YOLO-Inference",
        daemon=True,
    )
    worker_thread.start()

    def draw_detections(rgb, scale):
        rois = panel.cfg.all_rois()
        hit_map = {d: n for d, n in tester.assign}
        for (x1, y1, x2, y2, name, cf) in det["result"]:
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            hit = None
            for r in rois:
                rx1, ry1, rx2, ry2 = r.rect
                if (rx1 <= cx <= rx2 and ry1 <= cy <= ry2
                        and (not r.classes or name in r.classes) and cf >= r.conf):
                    hit = r
                    break
            nm = hit_map.get((x1, y1, x2, y2, name, cf))
            if nm is not None:                       # keputusan resmi dari logika (mode/grup/prioritas)
                hit = panel.cfg.find(nm[0]) if nm else None
            color = (0, 255, 120) if hit else (255, 60, 60)    # hijau = masuk ROI
            p1 = (int(x1 * scale), int(y1 * scale))
            p2 = (int(x2 * scale), int(y2 * scale))
            cv2.rectangle(rgb, p1, p2, color, 2)
            label = f"{name} {cf:.2f}" + (f" @{hit.id}" if hit else "")
            ty = p1[1] - 4 if p1[1] > 16 else p1[1] + 14
            cv2.putText(rgb, label, (p1[0], ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(rgb, label, (p1[0], ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        color, 1, cv2.LINE_AA)

    # ── Sumber dari argumen CLI (opsional) ──
    if args.model:
        load_model(args.model)
    if args.image:
        set_image(args.image)
    elif args.video:
        set_video(args.video)
    elif args.camera is not None:
        set_camera(args.camera)

    # ── Loop tampilan ──
    last = {"t": time.perf_counter()}
    cache = {"key": None, "frame": None, "rgb": None}   # frame diam: simpan hasil resize
    photo_ref = {"img": None, "size": None}             # satu PhotoImage dipakai ulang
    def tick():
        now = time.perf_counter()
        dt, last["t"] = now - last["t"], now

        tp = src["transport"]
        frame, key = None, None
        if src["still"] is not None:
            frame, key = src["still"], ("i",)
        elif tp is not None:
            frame = tp.next_frame(dt)
            if tp.is_paused():
                key = ("v", tp.position())
        elif src["cap"] is not None:
            ok, f = src["cap"].read()
            frame = f if ok else None

        if frame is None:
            root.after(33, tick)
            return
        h, w = frame.shape[:2]
        size["w"], size["h"] = w, h
        pw, ph = container.winfo_width(), container.winfo_height()
        if pw < 50 or ph < 50:
            root.after(33, tick)
            return

        # Frame diam (jeda / gambar): pakai ulang hasil flip+resize+convert
        ckey = (key, pw, ph, args.mirror) if key is not None else None
        if ckey is not None and cache["key"] == ckey:
            frame = cache["frame"]
            rgb = cache["rgb"].copy()                 # kecil (ukuran preview)
        else:
            if args.mirror:
                frame = cv2.flip(frame, 1)
            scale = min(pw / w, ph / h)
            small = frame
            if scale < 1:                             # resize dulu, baru convert (lebih kecil)
                small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                                   interpolation=cv2.INTER_AREA)
            base = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            if ckey is not None:
                cache.update(key=ckey, frame=frame, rgb=base)
                rgb = base.copy()
            else:
                cache.update(key=None, frame=None, rgb=None)
                rgb = base

        maybe_detect(frame)                           # kirim ke thread YOLO
        if seq_on.get():
            now_t = tp.position() / tp.fps() if tp is not None else time.perf_counter()
            if tester.sync(panel.cfg):
                det["seen"] = -1                 # ROI berubah: nilai ulang hasil terakhir
            live = det["result"] if (det_on.get() and det["model"] is not None) else []
            if det_on.get() and det["model"] is not None:
                if det["seq"] != det["seen"]:        # hanya saat ada hasil inferensi baru
                    det["seen"] = det["seq"]
                    tester.update(live, det["t"])    # waktu = waktu frame yang dideteksi
            else:
                tester.update([], now_t)
            seq_var.set(tester.render())
            panel.set_progress(tester.status)
        else:
            seq_var.set("")
            panel.set_progress({})

        rh, rw = rgb.shape[:2]
        off_x = max(0, (preview.winfo_width() - rw) // 2)
        off_y = max(0, (preview.winfo_height() - rh) // 2)
        panel.set_view(rw, rh, w, h, off_x, off_y)
        if det_on.get() and det["model"] is not None:
            draw_detections(rgb, rw / w)
        rgb = panel.draw_on_frame(rgb)

        img = Image.fromarray(rgb)

        try:
            if (
                photo_ref["img"] is not None
                and photo_ref["size"] == img.size
            ):
                try:
                    photo_ref["img"].paste(img)
                except MemoryError:
                    # Lepaskan referensi gambar lama, lalu buat ulang.
                    old_img = photo_ref["img"]
                    photo_ref["img"] = None
                    preview.config(image="")
                    preview.image = None
                    del old_img
                    gc.collect()

                    photo_ref["img"] = ImageTk.PhotoImage(img)
                    photo_ref["size"] = img.size
                    preview.config(image=photo_ref["img"], text="")
                    preview.image = photo_ref["img"]
            else:
                new_photo = ImageTk.PhotoImage(img)
                photo_ref["img"] = new_photo
                photo_ref["size"] = img.size
                preview.config(image=new_photo, text="")
                preview.image = new_photo

        except (MemoryError, tk.TclError) as e:
            # Jangan biarkan exception menghentikan loop Tkinter.
            status.set(f"Memori preview terbatas: {e}")

        finally:
            img.close()
        root.after(33, tick)
    tick()
    root.mainloop()
    release_source()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Logic Editor — uji mandiri")
    ap.add_argument("--image", help="(opsional) langsung buka gambar")
    ap.add_argument("--video", help="(opsional) langsung buka video")
    ap.add_argument("--camera", type=int, default=None, help="(opsional) index kamera")
    ap.add_argument("--model", help="(opsional) langsung muat model .pt")
    ap.add_argument("--classes", default="", help="(opsional) nama class dipisah koma, mis. hand,part")
    ap.add_argument("--xml", default=str(DEFAULT_XML))
    ap.add_argument("--mirror", action="store_true")
    _run_standalone(ap.parse_args())