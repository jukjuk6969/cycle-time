# ld_editor.py
"""
Editor Ladder Diagram (LD) visual untuk CT Monitor — seret-dan-lepas seperti software PLC.

Model     : rung = kondisi (kontak seri / paralel) -> keluaran (coil / Set / Reset / blok FB)
Hasil     : teks program plc_logic ("nama = ekspresi"), jadi langsung dipakai Pipeline /
            logic_editor (cfg.program). Susunan ladder disimpan sebagai komentar "#@ld {json}".
API sama dengan plc_editor.Editor:
    LDEditor(master, path=None, program=None, roi_names=None, on_apply=None, live=None)

Cara pakai
  - SERET item dari palet kiri ke kanvas. Titik tujuan muncul sebagai lingkaran hijau:
      kontak  -> sisipkan di jalur seri / di bawah kontak (= cabang paralel) / di bawah grup paralel
      keluaran-> ke kotak keluaran sebuah rung, atau ke area kosong di bawah (= rung baru)
  - Klik item palet (tanpa seret) = tambah di akhir rung terpilih.
  - Seret kontak di kanvas untuk memindahkan. Klik ganda = ubah nama / sifat.
  - Klik kanan = menu (NO/NC, cabang paralel, hapus, rung ...).  Del = hapus.  Ctrl+Z / Ctrl+Y.
  - Kontak "ROI" = bit kamera ($nama).  Kontak biasa = variabel (nama coil / blok).
  - Opsi "@" pada kontak = nilai variabel pada scan lalu.
  - Simulasi: klik kontak ROI untuk ON/OFF; jalur hijau = menghantar (Engine yang sama dengan produksi).

Jalankan:  python ld_editor.py [ladder.json | logic_config.xml]
"""
from __future__ import annotations

import copy
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from plc_engine import CONSTS, Engine
import plc_logic as L

# ═══════════════════════════════════════════════════════════════════════════
#  Model (tanpa Tk)
# ═══════════════════════════════════════════════════════════════════════════

NAME_OK = re.compile(r"[^\s&|^!(),#$=@]+$")
KW = {"AND", "OR", "XOR", "NOT"}
COIL_KINDS = ("coil", "set", "reset")
BLOCK_PARAM = {"TON": ("PT", "detik", 0.3), "TOF": ("PT", "detik", 0.2), "TP": ("PT", "detik", 0.5),
               "CTU": ("PV", "hitungan", 3), "R_TRIG": None, "F_TRIG": None}
BLOCK_PINS = {"TON": "IN", "TOF": "IN", "TP": "IN", "R_TRIG": "CLK", "F_TRIG": "CLK", "CTU": "CU"}


@dataclass(eq=False)
class El:
    """Kontak. roi=True -> bit kamera ($nama); prev=True -> nilai scan lalu (nama@)."""
    name: str = ""
    neg: bool = False
    prev: bool = False
    roi: bool = False


@dataclass(eq=False)
class Par:
    """Grup paralel: tiap cabang adalah series (list item)."""
    branches: list = field(default_factory=list)


@dataclass(eq=False)
class Out:
    kind: str = "coil"          # coil | set | reset | block
    name: str = ""
    btype: str = ""             # TON TOF TP R_TRIG F_TRIG CTU
    p: float = 0.0


@dataclass(eq=False)
class Rung:
    cond: list = field(default_factory=list)
    out: Out = field(default_factory=Out)
    extras: list = field(default_factory=list)   # pin tambahan blok (CTU: reset)
    title: str = ""


def name_err(s: str, allow_const: bool = False):
    if not s or not NAME_OK.match(s):
        return "Nama kosong atau memuat karakter terlarang (spasi & | ^ ! ( ) , # $ = @)."
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return "Nama tidak boleh berupa angka saja."
    if s in KW:
        return f"'{s}' adalah kata cadangan."
    if s in CONSTS and not allow_const:
        return f"'{s}' adalah konstanta."
    return None


# -- serialisasi --
def item_to_d(it):
    if isinstance(it, El):
        return {"t": "c", "n": it.name, "neg": it.neg, "prev": it.prev, "roi": it.roi}
    return {"t": "p", "b": [[item_to_d(x) for x in b] for b in it.branches]}


def item_from_d(d):
    if d["t"] == "c":
        return El(d["n"], bool(d.get("neg")), bool(d.get("prev")), bool(d.get("roi")))
    return Par([[item_from_d(x) for x in b] for b in d["b"]])


def rung_to_d(r: Rung):
    return {"title": r.title, "cond": [item_to_d(i) for i in r.cond],
            "out": {"kind": r.out.kind, "name": r.out.name, "bt": r.out.btype, "p": r.out.p},
            "extras": [[item_to_d(i) for i in e] for e in r.extras]}


def rung_from_d(d) -> Rung:
    o = d["out"]
    r = Rung([item_from_d(i) for i in d["cond"]], Out(o["kind"], o["name"], o.get("bt", ""), o.get("p", 0.0)),
             [[item_from_d(i) for i in e] for e in d.get("extras", [])], d.get("title", ""))
    fix_extras(r)
    return r


def dump(rungs) -> str:
    return json.dumps({"version": 1, "rungs": [rung_to_d(r) for r in rungs]}, ensure_ascii=True)

def load_rungs(s: str) -> list:
    raw = json.loads(s)

    if isinstance(raw, dict):
        items = raw["rungs"]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Format data ladder tidak valid.")

    return [rung_from_d(d) for d in items]

# -- struktur --
def fix_extras(r: Rung):
    need = 1 if (r.out.kind == "block" and r.out.btype == "CTU") else 0
    while len(r.extras) < need:
        r.extras.append([])
    del r.extras[need:]


def norm_series(items: list) -> list:
    out = []
    for it in items:
        if isinstance(it, Par):
            it.branches = [b for b in (norm_series(b) for b in it.branches) if b]
            if not it.branches:
                continue
            if len(it.branches) == 1:
                out.extend(it.branches[0])
                continue
        out.append(it)
    items[:] = out
    return items


def norm_rung(r: Rung):
    norm_series(r.cond)
    for e in r.extras:
        norm_series(e)


def all_series(r: Rung):
    return [r.cond] + list(r.extras)


def walk(items):
    for it in items:
        if isinstance(it, El):
            yield it
        else:
            for b in it.branches:
                yield from walk(b)


def find_in(items, target):
    """-> (list, index) tempat target berada, atau None."""
    for i, it in enumerate(items):
        if it is target:
            return items, i
        if isinstance(it, Par):
            for b in it.branches:
                f = find_in(b, target)
                if f:
                    return f
    return None


def size_item(it):
    if isinstance(it, El):
        return 1, 1
    ws, hs = [], 0
    for b in it.branches:
        w, h = size_series(b)
        ws.append(max(w, 1))
        hs += h
    return max(ws), hs


def size_series(items):
    if not items:
        return 0, 1
    ws, hs = zip(*(size_item(i) for i in items))
    return sum(ws), max(hs)

def _item_expr(it, memory_map=None) -> str:
    if isinstance(it, El):
        # ROI dan M = external input (diprefix $); variabel lain = internal
        is_ext = it.roi or bool(_MEM_RE.match(it.name))
        if is_ext:
            name = it.name
        else:
            aliases = (memory_map or {}).get("aliases", {})
            entry = aliases.get(it.name)
            name = entry.get("address", it.name) if isinstance(entry, dict) else it.name
        s = ("$" + name) if is_ext else name + ("@" if it.prev else "")
        return ("!" + s) if it.neg else s

    return "(" + " | ".join(
        _series_expr(b, memory_map=memory_map)
        for b in it.branches
    ) + ")"

def _series_expr(items, empty="TRUE", memory_map=None) -> str:
    return (
        " & ".join(
            _item_expr(i, memory_map=memory_map)
            for i in items
        )
        if items else empty
    )

def _fmt(v) -> str:
    return format(float(v), "g")


_MEM_RE = re.compile(r"^M(\d+)$")
_MEM_MAX = 65535  # batas atas wajar; sesuaikan dengan PLC target


def _check_mem_addr(name: str, where: str, errs: list):
    """[BARU] Validasi alamat memori bantu M0..M65535."""
    m = _MEM_RE.match(name)
    if not m:
        return
    n = int(m.group(1))
    if n > _MEM_MAX:
        errs.append(
            f"{where}: alamat memori '{name}' di luar rentang "
            f"(M0..M{_MEM_MAX})."
        )


def check_rungs(rungs, memory_map=None) -> list:
    aliases = (memory_map or {}).get("aliases", {})

    def _canon(n):
        """Resolve nama alias -> alamat PLC agar tabrakan (alias vs alamat) terdeteksi."""
        entry = aliases.get(n)
        return entry.get("address", n) if isinstance(entry, dict) else n

    errs = []
    names = {}

    for i, r in enumerate(rungs, 1):
        o = r.out
        e = name_err(o.name)
        if e:
            errs.append(f"Rung {i}: nama keluaran — {e}")
        else:
            _check_mem_addr(o.name, f"Rung {i}: keluaran", errs)
            # M = input eksternal ($M), tidak boleh jadi target coil
            if _MEM_RE.match(o.name):
                errs.append(
                    f"Rung {i}: alamat M '{o.name}' hanya untuk input, "
                    "bukan keluaran. Ganti nama coil."
                )
        for s in all_series(r):
            for el in walk(s):
                e = name_err(el.name, allow_const=not el.roi)
                if e:
                    errs.append(f"Rung {i}: kontak '{el.name}' — {e}")
                else:
                    # M hanya boleh pada kontak variabel, bukan ROI
                    if el.roi and _MEM_RE.match(el.name):
                        errs.append(
                            f"Rung {i}: kontak ROI '{el.name}' tidak boleh "
                            "memakai alamat memori M (ROI = bit kamera)."
                        )
                    elif not el.roi:
                        _check_mem_addr(el.name, f"Rung {i}: kontak", errs)

                if el.roi and el.prev:
                    errs.append(
                        f"Rung {i}: kontak ROI '{el.name}' tidak bisa "
                        "memakai '@'."
                    )

        if o.kind == "block" and o.btype not in BLOCK_PARAM:
            errs.append(f"Rung {i}: tipe blok '{o.btype}' tidak dikenal.")

        # [FIX] canonical: pakai alamat hasil alias, bukan nama mentah
        canon = _canon(o.name)
        kinds = names.setdefault(canon, [])
        kinds.append((i, "sr" if o.kind in ("set", "reset") else "plain"))

    for n, lst in names.items():
        plain = [i for i, k in lst if k == "plain"]
        if len(plain) > 1 or (plain and len(lst) > len(plain)):
            errs.append(
                f"Nama '{n}' dipakai lebih dari satu keluaran "
                f"(rung {', '.join(str(i) for i, _ in lst)})."
            )
    return errs
def ladder_to_text(rungs, memory_map=None) -> str:
    memory_map = memory_map or {"aliases": {}}

    aliases = memory_map.get("aliases", {})

    def resolve_alias(name):
        entry = aliases.get(name)
        if isinstance(entry, dict):
            return entry.get("address", name)
        return name

    errs = check_rungs(rungs, memory_map)   # ← perbaiki
    if errs:
        raise ValueError("\n".join(errs))

    last = {}
    for i, r in enumerate(rungs):
        if r.out.kind in ("set", "reset"):
            last[r.out.name] = (i, r.out.kind)

    sets, resets, lines = {}, {}, []

    for i, r in enumerate(rungs):
        o = r.out

        # ── Hitung kondisi SEKALI dengan memory_map ─────────────────────
        # (sebelumnya hanya cabang "block" yang me-resolve alias,
        #  sehingga coil / set / reset memakai nama mentah → gagal di Engine)
        cond = _series_expr(r.cond, memory_map=memory_map)

        if o.kind == "coil":
            lines.append(
                f"{resolve_alias(o.name)} = {cond}"
            )

        elif o.kind == "block":
            t = o.btype
            if t in ("TON", "TOF", "TP"):
                lines.append(
                    f"{resolve_alias(o.name)} = "
                    f"{t}({cond}, {_fmt(o.p)})"
                )
            elif t == "CTU":
                rs = (
                    _series_expr(r.extras[0], "FALSE", memory_map=memory_map)
                    if r.extras else "FALSE"
                )
                lines.append(
                    f"{resolve_alias(o.name)} = "
                    f"CTU({cond}, {rs}, "
                    f"{int(round(float(o.p)))})"
                )
            else:
                lines.append(
                    f"{resolve_alias(o.name)} = {t}({cond})"
                )

        else:                                            # set / reset
            name = resolve_alias(o.name)

            (sets if o.kind == "set" else resets).setdefault(
                name, []
            ).append(cond)

            if last[o.name][0] == i:
                s = " | ".join(sets.get(name, [])) or "FALSE"
                rr = " | ".join(resets.get(name, [])) or "FALSE"

                fn = (
                    "SR"
                    if last[o.name][1] == "set"
                    else "RS"
                )

                lines.append(
                    f"{name} = {fn}({s}, {rr})"
                )

    return "\n".join(lines) + "\n"

def compile_ladder(rungs, memory_map=None):
    text = ladder_to_text(rungs, memory_map=memory_map)
    nodes = L.compile_program(text)
    Engine(nodes)
    return text, nodes


# -- teks program (Node) -> LD --
def _parse_ref(r: str):
    neg = r.startswith("!")
    b = r[1:] if neg else r
    prev = b.endswith("@")
    return (b[:-1] if prev else b), neg, prev


def _el_ref(r):
    b, neg, prev = _parse_ref(r)
    if b in CONSTS:
        return El("TRUE" if CONSTS[b] else "FALSE", neg)
    return El(b, neg, prev)


def _flip(e):
    return El(e.name, not e.neg, e.prev, e.roi)


def _cp(e):
    return El(e.name, e.neg, e.prev, e.roi)


def _gate_series(t, refs, owner, pre):
    es = [_el_ref(r) for r in refs]
    if len(es) == 1:
        return [_flip(es[0])] if t in ("NAND", "NOR") else [es[0]]
    if t == "AND":
        return es
    if t == "OR":
        return [Par([[e] for e in es])]
    if t == "NAND":
        return [Par([[_flip(e)] for e in es])]
    if t == "NOR":
        return [_flip(e) for e in es]
    cur = es[0]                                           # XOR (paritas)
    for k, c in enumerate(es[1:], 1):
        s = [Par([[_cp(cur), _flip(c)], [_flip(cur), _cp(c)]])]
        if k == len(es) - 1:
            return s
        tmp = f"{owner}_x{k}"
        pre.append(Rung(s, Out("coil", tmp)))
        cur = El(tmp)


def nodes_to_ladder(nodes) -> list:
    rungs = []
    for n in nodes:
        t, a = n.type, n.inputs
        if t == "IN":
            rungs.append(Rung([El(n.params.get("name", n.id), roi=True)], Out("coil", n.id)))
        elif t == "NOT":
            rungs.append(Rung([_flip(_el_ref(a[0]))], Out("coil", n.id)))
        elif t in ("AND", "OR", "XOR", "NAND", "NOR"):
            pre = []
            s = _gate_series(t, a, n.id, pre)
            rungs += pre
            rungs.append(Rung(s, Out("coil", n.id)))
        elif t in ("TON", "TOF", "TP"):
            rungs.append(Rung([_el_ref(a[0])], Out("block", n.id, t, float(n.params.get("pt", 0)))))
        elif t in ("R_TRIG", "F_TRIG"):
            rungs.append(Rung([_el_ref(a[0])], Out("block", n.id, t)))
        elif t == "CTU":
            rungs.append(Rung([_el_ref(a[0])], Out("block", n.id, "CTU", float(n.params.get("pv", 1))),
                              [[_el_ref(a[1])]]))
        elif t in ("SR", "RS"):
            s, r = _el_ref(a[0]), _el_ref(a[1])
            order = [(r, "reset"), (s, "set")] if t == "SR" else [(s, "set"), (r, "reset")]
            for e, k in order:
                rungs.append(Rung([e], Out(k, n.id)))
        else:
            raise ValueError(f"tipe node tidak didukung: {t}")
    for r in rungs:
        fix_extras(r)
    return rungs


def _norm_text(text: str) -> str:
    out = []
    for ln in text.splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            out.append(ln)
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════════════
#  Tampilan
# ═══════════════════════════════════════════════════════════════════════════

CW, RH, WY, HEAD = 112, 66, 42, 26      # lebar sel, tinggi baris, posisi kawat, tinggi judul rung
LEFT, OUTW, GAP = 64, 200, 24
C_OFF, C_ON, C_WIRE = "#475569", "#16a34a", "#334155"
FONT = ("TkDefaultFont", 9)
FONT_B = ("TkDefaultFont", 9, "bold")

PALETTE = [
    ("Kontak", [("┤ ├   NO", {"kind": "contact"}),
                ("┤/├   NC", {"kind": "contact", "neg": True}),
                ("ROI   NO", {"kind": "contact", "roi": True}),
                ("ROI   NC", {"kind": "contact", "roi": True, "neg": True})]),
    ("Keluaran", [("( )   Coil", {"kind": "out", "o": "coil"}),
                  ("(S)   Set", {"kind": "out", "o": "set"}),
                  ("(R)   Reset", {"kind": "out", "o": "reset"})]),
    ("Timer", [(t, {"kind": "out", "o": "block", "bt": t}) for t in ("TON", "TOF", "TP")]),
    ("Pemicu tepi", [(t, {"kind": "out", "o": "block", "bt": t}) for t in ("R_TRIG", "F_TRIG")]),
    ("Counter", [("CTU", {"kind": "out", "o": "block", "bt": "CTU"})]),
]

HELP = """SERET & LEPAS
- Seret item dari palet kiri ke kanvas. Lingkaran hijau = titik tujuan; yang terdekat berwarna oranye.
- Kontak: sisipkan di jalur seri, atau lepas di BAWAH kontak lain untuk membuat cabang paralel,
  atau di bawah grup paralel untuk menambah cabang.
- Keluaran (Coil/Set/Reset/Timer/Counter): lepas di kotak keluaran sebuah rung, atau di area kosong paling
  bawah untuk membuat rung baru.
- Klik item palet tanpa menyeret = tambah di akhir rung terpilih.
- Seret kontak yang sudah ada untuk memindahkannya.

MENGEDIT
- Klik ganda kontak/keluaran: ubah nama dan sifat.   Klik kanan: menu.   Del: hapus.   Ctrl+Z / Ctrl+Y.
SUMBER INPUT
- ROI   : bit hasil deteksi kamera   → $nama
- M     : memori bantu PLC (BOOL)    → M0..M31 (default) + alamat hasil mapping
- Var   : variabel internal / output rung
- Opsi '@' = nilai variabel pada scan lalu.

KELUARAN
- Coil ( ): nama = kondisi.    Set (S) / Reset (R) dengan nama sama = latch (rung terakhir menang).
- Timer TON/TOF/TP: kondisi jadi pin IN, nama blok = sinyal keluaran Q.
- CTU: kondisi = CU; kotak kedua di bawahnya = pin R (reset). Kosong = tidak pernah reset.
- Konvensi nama (dibaca ProgramRunner): done_<roi>, skip_<roi>, start, run, done_<output>.

SIMULASI
- Klik kontak ROI untuk ON/OFF. Hijau = menghantar. Timer menampilkan ET.
"""


class LDEditor(tk.Toplevel):
    LD_TAG = "#@ld "

    def __init__(self, master=None, path=None, program=None, roi_names=None, on_apply=None, live=None, memory_map=None):
        super().__init__(master)
        self.title("Editor Ladder (LD)")
        self.geometry("1320x780")
        self.rungs: list = []
        # Mapping alias ke alamat memory PLC
        self.memory_map = {
            "aliases": {}
        }
        if memory_map:
            self.memory_map = memory_map
        self.sel = None
        self.roi_names = list(roi_names or [])
        self.on_apply, self.live = on_apply, live
        self.path = None
        self.undo_s, self.redo_s = [], []
        self.dirty = False
        self.drag = None
        self.hits, self.slots = [], []
        # simulasi / monitor
        self.sim = self.live_on = False
        self.engine, self.t, self._job, self._live_job = None, 0.0, None, None
        self.vals, self.prev, self.info = {}, {}, {}
        self.inputs_on: set = set()
        self.lit, self.lit_in = {}, {}
        self.live_var = tk.StringVar(value="")
        self._live_key = None
        self.maxc, self.xo, self.xr = 4, 0, 0
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        if program is not None:
            try:
                self.load_text(program)
            except Exception:
                self.destroy()
                raise
        elif path:
            self.open_file(path)
        self.redraw()
        if on_apply:
            self.transient(master)
            self.wait_visibility()
            self.grab_set()

    # ── UI ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        bar = tk.Frame(self, bg="#e2e8f0")
        bar.pack(side="top", fill="x")

        def btn(text, cmd, **kw):
            b = tk.Button(bar, text=text, command=cmd, **kw)
            b.pack(side="left", padx=2, pady=3)
            return b

        if not self.on_apply:
            btn("Baru", self.new)
            btn("Buka…", self.open_file)
            btn("Simpan…", self.save_file)
        btn("＋ Rung", self.add_rung)
        btn("▲", lambda: self.move_rung(-1), width=2)
        btn("▼", lambda: self.move_rung(1), width=2)
        btn("↶", self.undo, width=2)
        btn("↷", self.redo, width=2)
        btn("Validasi", self.validate_ui)
        btn("Memory Mapping", self.open_memory_mapping)
        btn("Lihat ST / teks", self.show_export)
        self.btn_sim = btn("▶ Simulasi", self.toggle_sim)
        if self.live:
            self.btn_live = btn("● Monitor", self.toggle_live)
            tk.Label(bar, textvariable=self.live_var, bg="#e2e8f0", fg="#b91c1c",
                     font=FONT_B).pack(side="left", padx=8)
        btn("Bantuan (F1)", self.show_help)
        if self.on_apply:
            tk.Button(bar, text="Batal", command=self.close).pack(side="right", padx=4, pady=3)
            tk.Button(bar, text="✔ Terapkan ke program", bg="#bbf7d0",
                      command=self.apply_and_close).pack(side="right", padx=2, pady=3)

        body = tk.Frame(self)
        body.pack(fill="both", expand=True)
        pal = tk.Frame(body, width=150, bg="#f1f5f9")
        pal.pack(side="left", fill="y")
        for title, items in PALETTE:
            tk.Label(pal, text=title, bg="#f1f5f9", font=FONT_B, anchor="w").pack(fill="x", padx=6, pady=(8, 1))
            for text, spec in items:
                lb = tk.Label(pal, text=text, width=14, bg="#e2e8f0", relief="raised", bd=2,
                              cursor="hand2", font=("Consolas", 10))
                lb.pack(padx=6, pady=1)
                lb.bind("<ButtonPress-1>", lambda e, s=spec, t=text: self._pal_press(e, s, t))
                lb.bind("<B1-Motion>", self._pal_motion)
                lb.bind("<ButtonRelease-1>", self._pal_release)

        wrap = tk.Frame(body)
        wrap.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg="#ffffff", highlightthickness=0, scrollregion=(0, 0, 1600, 1200))
        sx = tk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        sy = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        sx.pack(side="bottom", fill="x")
        sy.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.status = tk.StringVar(value="Seret item dari palet ke kanvas  •  klik ganda = ubah nama  •  F1 = bantuan")
        tk.Label(self, textvariable=self.status, anchor="w", bg="#e2e8f0").pack(side="bottom", fill="x")

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_down)
        c.bind("<B1-Motion>", self.on_move)
        c.bind("<ButtonRelease-1>", self.on_up)
        c.bind("<Double-Button-1>", self.on_double)
        for ev in ("<Button-3>", "<Control-Button-1>"):
            c.bind(ev, self.on_right)
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            c.bind(ev, self.on_wheel)
        self.bind("<Delete>", lambda e: self.delete_sel())
        self.bind("<BackSpace>", lambda e: self.delete_sel())
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-y>", lambda e: self.redo())
        self.bind("<F1>", lambda e: self.show_help())

    # ── util ───────────────────────────────────────────────────────────────
    def say(self, text):
        self.status.set(text)

    def var_names(self):
        names = {r.out.name for r in self.rungs if r.out.name}
        return sorted(names)

    def m_all(self):
        """Alamat M yang dipakai sebagai kontak (external input)."""
        s = set()
        for r in self.rungs:
            for ser in all_series(r):
                for e in walk(ser):
                    if not e.roi and _MEM_RE.match(e.name):
                        s.add(e.name)
        return sorted(s)

    def roi_all(self):
        s = set(self.roi_names)
        for r in self.rungs:
            for ser in all_series(r):
                s |= {e.name for e in walk(ser) if e.roi}
        return sorted(s)

    def uniq(self, base):
        used = {r.out.name for r in self.rungs}
        k = 1
        while f"{base}{k}" in used:
            k += 1
        return f"{base}{k}"
    def next_m(self):
        """Alamat M0, M1, M2, … pertama yang belum dipakai (out maupun alias)."""
        used = {r.out.name for r in self.rungs}
        for item in self.memory_map.get("aliases", {}).values():
            if isinstance(item, dict) and item.get("type") == "BOOL":
                used.add(item["address"])
        k = 0
        while f"M{k}" in used:
            k += 1
        return f"M{k}"
    def push(self):
        self.undo_s.append(dump(self.rungs))
        del self.undo_s[:-100]
        self.redo_s.clear()
        self.dirty = True
        self.changed()

    def changed(self):
        self.engine = None

    def undo(self):
        if self.undo_s:
            self.redo_s.append(dump(self.rungs))
            self.rungs = load_rungs(self.undo_s.pop())
            self.sel = None
            self.changed()
            self.redraw()

    def redo(self):
        if self.redo_s:
            self.undo_s.append(dump(self.rungs))
            self.rungs = load_rungs(self.redo_s.pop())
            self.sel = None
            self.changed()
            self.redraw()

    def _modal_back(self):
        if self.on_apply:
            try:
                self.grab_set()
            except tk.TclError:
                pass

    def new(self):
        if self.rungs and not messagebox.askyesno("Baru", "Hapus semua rung?", parent=self):
            return
        self.push()
        self.rungs, self.sel, self.path = [], None, None
        self.redraw()

    # ── tata letak & gambar ────────────────────────────────────────────────
    def _col(self, p):
        if p is None:
            return C_WIRE
        return C_ON if p else "#94a3b8"

    def _compute_flow(self):
        self.lit, self.lit_in = {}, {}
        if not (self.sim or self.live_on):
            return
        for r in self.rungs:
            self._flow_series(r.cond, True)
            for e in r.extras:
                self._flow_series(e, True, empty_false=True)

    def _cval(self, el):
        if el.name in ("TRUE", "FALSE") and not el.roi:
            return el.name == "TRUE"
        if el.roi or _MEM_RE.match(el.name):
            return self.vals.get("$" + el.name, False)
        return (self.prev if el.prev else self.vals).get(el.name, False)
    
    def _add_alias_vals(self, vals):
        """Duplikasi key alias → alamat agar tampilan cocok baik pakai alias maupun alamat."""
        for alias, item in self.memory_map.get("aliases", {}).items():
            if isinstance(item, dict):
                addr = item.get("address")
                if addr in vals and alias not in vals:
                    vals[alias] = vals[addr]

    def _flow_series(self, items, pin, empty_false=False):
        p = pin
        for it in items:
            p = self._flow_item(it, p)
        if empty_false and not items:
            p = False
        self.lit[id(items)] = p
        return p

    def _flow_item(self, it, pin):
        self.lit_in[id(it)] = pin
        if isinstance(it, El):
            out = bool(pin and (self._cval(it) != it.neg))
        else:
            outs = [self._flow_series(b, pin) for b in it.branches]
            out = any(outs)
        self.lit[id(it)] = out
        return out

    def redraw(self):
        c = self.canvas
        c.delete("all")
        self.hits, self.slots = [], []
        self._compute_flow()
        cols = [4]
        for r in self.rungs:
            cols += [size_series(s)[0] for s in all_series(r)]
        self.maxc = max(cols)
        self.xo = LEFT + self.maxc * CW
        self.xr = self.xo + OUTW
        y = 10
        for i, r in enumerate(self.rungs):
            y += self._draw_rung(r, i, y) + GAP
        self.y_end = y
        self.slots.append({"k": "newrung", "x": LEFT + 200, "y": y + 24, "d": None})
        if not self.rungs:
            c.create_text(LEFT + 20, 50, anchor="w", fill="#94a3b8", font=("TkDefaultFont", 12),
                          text="Kanvas kosong — seret 'Coil' dari palet ke sini, atau klik ＋ Rung")
        if self.drag and self.drag.get("moved"):
            self._draw_drag()
        c.configure(scrollregion=(0, 0, self.xr + 80, max(y + 140, 400)))

    def _draw_rung(self, rg, i, y):
        c = self.canvas
        sel = self.sel is rg
        c.create_text(8, y + 13, anchor="w", text=f"Rung {i + 1}", font=FONT_B,
                      fill="#ea580c" if sel else "#0f172a")
        c.create_text(78, y + 13, anchor="w", text=rg.title, font=FONT, fill="#64748b")
        self.hits.append(((0, y, self.xr, y + HEAD), "rung", rg))
        Y0 = y + HEAD
        ch = size_series(rg.cond)[1]
        rows = ch + sum(size_series(e)[1] for e in rg.extras)
        self._draw_series(rg.cond, 0, 0, self.maxc, Y0)
        r = ch
        for e in rg.extras:
            self._draw_series(e, 0, r, self.maxc, Y0)
            r += size_series(e)[1]
        c.create_line(LEFT, Y0 + 4, LEFT, Y0 + rows * RH - 4, width=4, fill="#1e293b")
        c.create_line(self.xr, Y0 + 4, self.xr, Y0 + rows * RH - 4, width=4, fill="#1e293b")
        self._draw_out(rg, Y0, rows, ch)
        return HEAD + rows * RH

    def _slot(self, k, x, y, d):
        self.slots.append({"k": k, "x": x, "y": y, "d": d})

    def _draw_series(self, items, col, row, alloc, Y0):
        wy = Y0 + row * RH + WY
        x = col
        if not items:
            self._slot("series", LEFT + col * CW + 34, wy, (items, 0))
        else:
            self._slot("series", LEFT + x * CW, wy, (items, 0))
        for i, it in enumerate(items):
            w = size_item(it)[0]
            if isinstance(it, El):
                self._draw_contact(it, x, row, w, Y0, items, i)
            else:
                self._draw_par(it, x, row, w, Y0)
            x += w
            self._slot("series", LEFT + x * CW, wy, (items, i + 1))
        p = self.lit.get(id(items))
        self.canvas.create_line(LEFT + x * CW, wy, LEFT + (col + alloc) * CW, wy, width=2, fill=self._col(p))

    def _draw_contact(self, el, col, row, alloc, Y0, items, idx):
        c = self.canvas
        x0 = LEFT + col * CW
        x1 = x0 + alloc * CW
        wy = Y0 + row * RH + WY
        mid = x0 + CW / 2
        pin, pout = self.lit_in.get(id(el)), self.lit.get(id(el))
        c.create_line(x0, wy, mid - 14, wy, width=2, fill=self._col(pin))
        c.create_line(mid + 14, wy, x1, wy, width=2, fill=self._col(pout))
        flow = self.sim or self.live_on
        v = self._cval(el) if flow else None
        conduct = v is not None and (v != el.neg)
        sc = (C_ON if conduct else C_OFF) if flow else C_WIRE
        c.create_line(mid - 14, wy - 14, mid - 14, wy + 14, width=3, fill=sc)
        c.create_line(mid + 14, wy - 14, mid + 14, wy + 14, width=3, fill=sc)
        if el.neg:
            c.create_line(mid - 9, wy + 11, mid + 9, wy - 11, width=2, fill=sc)
        if el.roi:
            c.create_text(mid, wy + 24, text="ROI", font=("TkDefaultFont", 7), fill="#15803d")
        elif _MEM_RE.match(el.name):
            c.create_text(mid, wy + 24, text="M",
                        font=("TkDefaultFont", 7, "bold"), fill="#7c3aed")

        c.create_text(mid, wy - 26, text=el.name or "?", font=FONT_B,
                    fill=("#15803d" if el.roi
                            else "#7c3aed" if _MEM_RE.match(el.name)
                            else "#b91c1c" if not el.name
                            else "#0f172a"))
        if el.prev:
            c.create_text(mid + 22, wy - 22, text="@", font=FONT_B, fill="#ea580c")
        if flow and (el.roi or _MEM_RE.match(el.name)):
            on = self.vals.get("$" + el.name, False)
            c.create_text(mid, wy + 34, text="● ON" if on else "○ OFF",
                          font=("TkDefaultFont", 7, "bold"),
                          fill=C_ON if on else "#64748b")
        if self.sel is el:
            c.create_rectangle(mid - 44, wy - 40, mid + 44, wy + 18, outline="#f59e0b", width=2)
        self.hits.append(((mid - 44, wy - 40, mid + 44, wy + 18), "el", el))
        self._slot("wrap", mid, wy + 17, (items, idx))

    def _draw_par(self, par, col, row, alloc, Y0):
        c = self.canvas
        r, ys = row, []
        for b in par.branches:
            self._draw_series(b, col, r, alloc, Y0)
            ys.append(Y0 + r * RH + WY)
            r += size_series(b)[1]
        xl, xr = LEFT + col * CW, LEFT + (col + alloc) * CW
        pin, pout = self.lit_in.get(id(par)), self.lit.get(id(par))
        c.create_line(xl, ys[0], xl, ys[-1], width=2, fill=self._col(pin))
        c.create_line(xr, ys[0], xr, ys[-1], width=2, fill=self._col(pout))
        sel = self.sel is par
        if sel:
            c.create_rectangle(xl - 4, ys[0] - 40, xr + 4, ys[-1] + 24, outline="#f59e0b", width=2, dash=(4, 3))
        self.hits.append(((xl - 7, ys[0], xl + 7, ys[-1]), "par", par))
        self.hits.append(((xr - 7, ys[0], xr + 7, ys[-1]), "par", par))
        self._slot("parAdd", (xl + xr) / 2, Y0 + r * RH + 10, par)

    def _draw_out(self, rg, Y0, rows, ch):
        c = self.canvas
        o = rg.out
        wy = Y0 + WY
        xo = self.xo
        pc = self.lit.get(id(rg.cond))
        flow = self.sim or self.live_on
        v = self.vals.get(o.name, False) if flow else None
        sel = self.sel is o
        self._slot("out", xo + 50, wy, rg)
        if o.kind in COIL_KINDS:
            cx = xo + 52
            c.create_line(xo, wy, cx - 17, wy, width=2, fill=self._col(pc))
            lit = bool(v) if flow else False
            col = C_ON if lit else (C_OFF if flow else C_WIRE)
            c.create_oval(cx - 17, wy - 17, cx + 17, wy + 17, width=3, outline=col,
                          fill="#dcfce7" if lit else "#ffffff")
            c.create_text(cx, wy, text={"coil": "", "set": "S", "reset": "R"}[o.kind], font=FONT_B, fill=col)
            c.create_text(cx, wy - 28, text=o.name or "?", font=FONT_B,
                          fill="#b91c1c" if not o.name else "#0f172a")
            c.create_line(cx + 17, wy, self.xr, wy, width=2, fill=self._col(v if flow else None))
            rect = (cx - 30, wy - 40, cx + 30, wy + 24)
        else:
            bx0, bx1 = xo + 12, xo + OUTW - 44
            by0, by1 = wy - 30, max(wy + 30, Y0 + rows * RH - 12)
            lit = bool(v) if flow else False
            col = C_ON if lit else C_WIRE
            c.create_rectangle(bx0, by0, bx1, by1, width=2, outline=col, fill="#f0fdf4" if lit else "#f8fafc")
            c.create_text((bx0 + bx1) / 2, by0 + 10, text=o.btype, font=FONT_B)
            pin = BLOCK_PINS[o.btype]
            c.create_line(xo, wy, bx0, wy, width=2, fill=self._col(pc))
            c.create_text(bx0 + 4, wy, text=pin, anchor="w", font=FONT)
            if o.btype == "CTU" and rg.extras:
                ry = Y0 + ch * RH + WY
                c.create_line(xo, ry, bx0, ry, width=2, fill=self._col(self.lit.get(id(rg.extras[0]))))
                c.create_text(bx0 + 4, ry, text="R", anchor="w", font=FONT)
            c.create_text(bx1 - 4, wy, text="Q", anchor="e", font=FONT)
            c.create_line(bx1, wy, self.xr, wy, width=2, fill=self._col(v if flow else None))
            par = BLOCK_PARAM[o.btype]
            txt = ""
            if par:
                txt = f"{par[0]}={_fmt(o.p)}" + ("s" if par[0] == "PT" else "")
                inf = self.info.get(o.name) if flow else None
                if inf is not None and o.btype == "TON":
                    txt += f"  ET={inf:.2f}"
                elif inf is not None and o.btype == "CTU":
                    txt += f"  CV={int(inf)}"
            c.create_text((bx0 + bx1) / 2, by1 - 10, text=txt, font=("TkDefaultFont", 8))
            c.create_text((bx0 + bx1) / 2, by0 - 9, text=o.name or "?", font=FONT_B,
                          fill="#b91c1c" if not o.name else "#0f172a")
            rect = (bx0, by0, bx1, by1)
        if sel:
            x0, y0, x1, y1 = rect
            c.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline="#f59e0b", width=2)
        self.hits.append((rect, "out", o))
        

    # ── hit / seret ────────────────────────────────────────────────────────
    def _xy(self, e):
        return self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)

    def _hit(self, x, y):
        for rect, kind, obj in self.hits:
            if rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                return kind, obj
        return None

    def _nearest(self, x, y, kind):
        want = ("series", "wrap", "parAdd") if kind == "contact" else ("out", "newrung")
        best = None
        orig = self.drag.get("orig") if self.drag else None
        for s in self.slots:
            if s["k"] not in want:
                continue
            if s["k"] == "wrap" and s["d"][0][s["d"][1]] is orig:
                continue
            d = math.hypot(s["x"] - x, s["y"] - y)
            lim = 90 if s["k"] in ("out", "newrung") else 52
            if d <= lim and (best is None or d < best[0]):
                best = (d, s)
        return best[1] if best else None

    def _draw_drag(self):
        c, d = self.canvas, self.drag
        kind = d["spec"]["kind"]
        want = ("series", "wrap", "parAdd") if kind == "contact" else ("out", "newrung")
        near = self._nearest(d["cx"], d["cy"], kind)
        for s in self.slots:
            if s["k"] not in want:
                continue
            on = s is near
            r = 9 if on else 5
            c.create_oval(s["x"] - r, s["y"] - r, s["x"] + r, s["y"] + r,
                          fill="#f59e0b" if on else "#bbf7d0", outline="#16a34a", width=2)
        if near and near["k"] == "newrung":
            c.create_rectangle(LEFT, self.y_end, self.xr, self.y_end + 60, outline="#f59e0b", dash=(5, 3), width=2)
        if d.get("over", True):
            c.create_rectangle(d["cx"] - 40, d["cy"] - 14, d["cx"] + 40, d["cy"] + 14, fill="#fef3c7",
                               outline="#f59e0b", width=2)
            c.create_text(d["cx"], d["cy"], text=d.get("label", ""), font=FONT_B)

    def _pal_press(self, e, spec, label):
        self.drag = {"spec": spec, "orig": None, "moved": False, "label": label, "src": "pal",
                     "x0": e.x_root, "y0": e.y_root, "cx": 0, "cy": 0, "over": False}

    def _pal_xy(self, e):
        c = self.canvas
        px, py = e.x_root - c.winfo_rootx(), e.y_root - c.winfo_rooty()
        inside = 0 <= px < c.winfo_width() and 0 <= py < c.winfo_height()
        return c.canvasx(px), c.canvasy(py), inside

    def _pal_motion(self, e):
        d = self.drag
        if not d or d.get("src") != "pal":
            return
        if not d["moved"] and math.hypot(e.x_root - d["x0"], e.y_root - d["y0"]) > 5:
            d["moved"] = True
        if d["moved"]:
            d["cx"], d["cy"], d["over"] = self._pal_xy(e)
            self.redraw()

    def _pal_release(self, e):
        d, self.drag = self.drag, None
        if not d or d.get("src") != "pal":
            return
        if not d["moved"]:                       # klik = tambah di akhir rung terpilih
            self.redraw()
            self.click_add(d["spec"])
            return
        x, y, inside = self._pal_xy(e)
        if inside:
            self.drag = d
            d["cx"], d["cy"] = x, y
            s = self._nearest(x, y, d["spec"]["kind"])
            self.drag = None
            if s:
                self.apply_drop(d["spec"], s, None)
                return
            self.say("Lepas di dekat lingkaran hijau (titik tujuan).")
        self.redraw()

    def on_down(self, e):
        self.canvas.focus_set()
        x, y = self._xy(e)
        h = self._hit(x, y)
        self.drag = None
        if h:
            kind, obj = h
            self.sel = obj
            self._press = (kind, obj)
            if kind == "el" and not (self.sim or self.live_on):
                self.drag = {"spec": {"kind": "contact"}, "orig": obj, "moved": False, "src": "cv",
                             "x0": x, "y0": y, "cx": x, "cy": y, "label": obj.name}
        else:
            self.sel, self._press = None, None
        self.redraw()

    def on_move(self, e):
        d = self.drag
        if not d or d.get("src") != "cv":
            return
        x, y = self._xy(e)
        if not d["moved"] and math.hypot(x - d["x0"], y - d["y0"]) > 6:
            d["moved"] = True
        if d["moved"]:
            d["cx"], d["cy"] = x, y
            self.redraw()

    def on_up(self, e):
        d, self.drag = self.drag, None
        if d and d.get("src") == "cv" and d["moved"]:
            x, y = self._xy(e)
            self.drag = d
            d["cx"], d["cy"] = x, y
            s = self._nearest(x, y, "contact")
            self.drag = None
            if s:
                self.apply_drop(d["spec"], s, d["orig"])
                return
            self.redraw()
            return
        pr = getattr(self, "_press", None)
        if (pr and pr[0] == "el" and self.sim and not (d and d["moved"])
                and (pr[1].roi or _MEM_RE.match(pr[1].name))):
            self.inputs_on.symmetric_difference_update({pr[1].name})
        self.redraw()

    def on_wheel(self, e):
        up = e.num == 4 or e.delta > 0
        if e.state & 0x1:
            self.canvas.xview_scroll(-3 if up else 3, "units")
        else:
            self.canvas.yview_scroll(-3 if up else 3, "units")

    # ── aksi edit ──────────────────────────────────────────────────────────
    def apply_drop(self, spec, slot, orig):
        k, d = slot["k"], slot["d"]
        if spec["kind"] == "contact":
            if orig is None:
                info = self.dlg_contact(None, spec)
                if info is None:
                    self.redraw()
                    return
                new = El(info["name"], info["neg"], info["prev"], info["roi"])
            else:
                new = _cp(orig)
            self.push()
            if k == "series":
                lst, idx = d
                lst.insert(idx, new)
            elif k == "wrap":
                lst, idx = d
                lst[idx] = Par([[lst[idx]], [new]])
            else:
                d.branches.append([new])
            if orig is not None:
                for r in self.rungs:
                    for s in all_series(r):
                        f = find_in(s, orig)
                        if f:
                            del f[0][f[1]]
                            break
            self.normalize()
            self.sel = new
        else:
            o = self.dlg_out(None, spec)
            if o is None:                           # [FIX 1]
                self.redraw()
                return
            self.push()                             # [FIX 2]
            if k == "out":
                d.out = o
                fix_extras(d)
                self.sel = o
            else:
                r = Rung([], o)
                fix_extras(r)
                self.rungs.append(r)
                self.sel = o
        self.redraw()

    def normalize(self):
        for r in self.rungs:
            norm_rung(r)

    def click_add(self, spec):
        rg = self.sel_rung() or (self.rungs[-1] if self.rungs else None)
        if spec["kind"] == "contact":
            if rg is None:
                self.say("Buat rung dulu (＋ Rung) atau seret keluaran ke kanvas.")
                return
            self.apply_drop(spec, {"k": "series", "d": (rg.cond, len(rg.cond))}, None)
        else:
            if rg is None:
                self.apply_drop(spec, {"k": "newrung", "d": None}, None)
            else:
                self.apply_drop(spec, {"k": "out", "d": rg}, None)

    def sel_rung(self):
        s = self.sel
        if isinstance(s, Rung):
            return s
        for r in self.rungs:
            if s is r.out or any(find_in(x, s) for x in all_series(r)):
                return r
        return None

    def add_rung(self, at=None):
        self.push()
        r = Rung([], Out("coil", self.uniq("m")))         # was next_m()
        cur = self.sel_rung()
        if at is None:
            at = (self.rungs.index(cur) + 1) if cur in self.rungs else len(self.rungs)
        self.rungs.insert(at, r)
        self.sel = r
        self.redraw()

    def delete_rung(self, r):
        if r in self.rungs:
            self.push()
            self.rungs.remove(r)
            self.sel = None
            self.redraw()

    def move_rung(self, delta):
        r = self.sel_rung()
        if r is None:
            return
        i = self.rungs.index(r)
        j = i + delta
        if 0 <= j < len(self.rungs):
            self.push()
            self.rungs[i], self.rungs[j] = self.rungs[j], self.rungs[i]
            self.redraw()

    def delete_sel(self):
        s = self.sel
        if s is None or self.focus_get() is not None and isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            return
        if isinstance(s, Rung):
            self.delete_rung(s)
        elif isinstance(s, Out):
            r = self.sel_rung()
            if r:
                self.delete_rung(r)
        else:
            self.push()
            for r in self.rungs:
                for ser in all_series(r):
                    f = find_in(ser, s)
                    if f:
                        del f[0][f[1]]
                        break
            self.normalize()
            self.sel = None
            self.redraw()

    def on_double(self, e):
        x, y = self._xy(e)
        h = self._hit(x, y)
        if not h or self.sim or self.live_on:
            return
        kind, obj = h
        if kind == "el":
            self.edit_el(obj)
        elif kind == "out":
            self.edit_out(obj)
        elif kind == "rung":
            self.edit_title(obj)

    def edit_el(self, el):
        info = self.dlg_contact(el, {})
        if info:
            self.push()
            el.name, el.neg, el.prev, el.roi = info["name"], info["neg"], info["prev"], info["roi"]
            self.redraw()

    def edit_out(self, o):
        new = self.dlg_out(o, {})
        if new:
            self.push()
            o.kind, o.name, o.btype, o.p = new.kind, new.name, new.btype, new.p
            for r in self.rungs:
                fix_extras(r)
            self.redraw()

    def edit_title(self, r):
        s = simpledialog.askstring("Rung", "Judul / keterangan rung:", initialvalue=r.title, parent=self)
        self._modal_back()
        if s is not None:
            self.push()
            r.title = s.strip()
            self.redraw()

    def on_right(self, e):
        x, y = self._xy(e)
        h = self._hit(x, y)
        m = tk.Menu(self, tearoff=0)
        if h:
            kind, obj = h
            self.sel = obj
            if kind == "el":
                m.add_command(label="Ubah nama / sifat…", command=lambda: self.edit_el(obj))
                m.add_command(label="Balik NO ⇄ NC", command=lambda: self._toggle_neg(obj))
                m.add_command(label="Tambah cabang paralel di bawah…", command=lambda: self._menu_wrap(obj))
                m.add_separator()
                m.add_command(label="Hapus kontak", command=self.delete_sel)
            elif kind == "par":
                m.add_command(label="Hapus grup paralel", command=self.delete_sel)
            elif kind == "out":
                m.add_command(label="Ubah keluaran…", command=lambda: self.edit_out(obj))
                m.add_command(label="Hapus rung", command=self.delete_sel)
            else:
                self._rung_menu(m, obj)
        else:
            m.add_command(label="＋ Tambah rung", command=self.add_rung)
        self.redraw()
        m.tk_popup(e.x_root, e.y_root)

    def _rung_menu(self, m, r):
        m.add_command(label="Judul rung…", command=lambda: self.edit_title(r))
        m.add_command(label="Sisipkan rung di atas", command=lambda: self.add_rung(self.rungs.index(r)))
        m.add_command(label="Sisipkan rung di bawah", command=lambda: self.add_rung(self.rungs.index(r) + 1))
        m.add_command(label="Naikkan", command=lambda: self.move_rung(-1))
        m.add_command(label="Turunkan", command=lambda: self.move_rung(1))
        m.add_separator()
        m.add_command(label="Hapus rung", command=lambda: self.delete_rung(r))

    def _toggle_neg(self, el):
        self.push()
        el.neg = not el.neg
        self.redraw()

    def _menu_wrap(self, el):
        for r in self.rungs:
            for ser in all_series(r):
                f = find_in(ser, el)
                if f:
                    self.apply_drop({"kind": "contact"}, {"k": "wrap", "d": f}, None)
                    return

    # ── dialog ─────────────────────────────────────────────────────────────
    def _dialog(self, title):
        top = tk.Toplevel(self)
        top.title(title)
        top.resizable(False, False)
        top.transient(self)
        top.grab_set()
        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)
        return top, body

    def _finish_dialog(self, top):
        top.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - top.winfo_reqwidth()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - top.winfo_reqheight()) // 3)
        top.geometry(f"+{x}+{y}")
        top.wait_window()
        self._modal_back()
    def mem_bit_names(self):
        """Alamat M yang sudah dipetakan (BOOL) + slot M0..M31 yang belum dipakai."""
        used = set()
        for item in self.memory_map.get("aliases", {}).values():
            if isinstance(item, dict) and item.get("type") == "BOOL":
                used.add(item["address"])
        for k in range(32):
            used.add(f"M{k}")
        return sorted(used, key=lambda a: int(re.sub(r"\D", "", a) or 0))

    def signal_names(self):
        aliases = self.memory_map.get("aliases", {})
        bool_aliases = {n for n, it in aliases.items()
                        if isinstance(it, dict) and it.get("type") == "BOOL"}
        return sorted(set(self.var_names()) | set(self.mem_bit_names()) | bool_aliases)

    def dlg_contact(self, el, spec):
        res = {"v": None}
        top, body = self._dialog("Kontak")
        roi = tk.BooleanVar(value=el.roi if el else bool(spec.get("roi")))
        neg = tk.BooleanVar(value=el.neg if el else bool(spec.get("neg")))
        prev = tk.BooleanVar(value=el.prev if el else False)
        name = tk.StringVar(value=el.name if el else "")
        ttk.Label(body, text="Sumber:", font=("Arial", 10, "bold")).pack(anchor="w")
        ttk.Radiobutton(
            body, text="ROI — bit kamera ($nama)",
            variable=roi, value=True,
        ).pack(anchor="w", padx=8)
        ttk.Radiobutton(
            body, text="Variabel / M — nama coil, blok, atau M0..M31",
            variable=roi, value=False,
        ).pack(anchor="w", padx=8)
        ttk.Label(body, text="Nama:", font=("Arial", 10, "bold")).pack(anchor="w", pady=(8, 0))
        cb = ttk.Combobox(body, textvariable=name, width=30)
        cb.pack(fill="x")
        ttk.Label(body, text="Jenis kontak:", font=("Arial", 10, "bold")).pack(anchor="w", pady=(8, 0))
        ttk.Radiobutton(body, text="┤ ├  NO — menghantar bila TRUE", variable=neg, value=False).pack(anchor="w", padx=8)
        ttk.Radiobutton(body, text="┤/├  NC — menghantar bila FALSE", variable=neg, value=True).pack(anchor="w", padx=8)
        pv = ttk.Checkbutton(body, text="Nilai scan lalu (@)", variable=prev)
        pv.pack(anchor="w", pady=(8, 0))
        err = ttk.Label(body, text="", foreground="#c62828")
        err.pack(anchor="w", pady=(6, 0))

        def refresh(*_):
            cb.config(values=self.roi_all() if roi.get()
          else self.signal_names() + ["TRUE", "FALSE"])
            if roi.get():
                prev.set(False)
                pv.state(["disabled"])
            else:
                pv.state(["!disabled"])

        roi.trace_add("write", refresh)
        refresh()

        def ok(_=None):
            n = name.get().strip()
            e = name_err(n, allow_const=not roi.get())
            if e:
                err.config(text=e)
                return
            res["v"] = {"name": n, "neg": neg.get(), "prev": prev.get() and not roi.get(), "roi": roi.get()}
            top.destroy()

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="OK", width=8, command=ok).pack(side="left")
        ttk.Button(row, text="Batal", width=8, command=top.destroy).pack(side="left", padx=(6, 0))

        top.bind("<Return>", ok)
        top.bind("<Escape>", lambda e: top.destroy())
        cb.focus_set()
        self._finish_dialog(top)
        return res["v"]

    def dlg_out(self, o, spec):
        res = {"v": None}
        top, body = self._dialog("Keluaran")
        is_block = (o.kind == "block") if o else spec.get("o") == "block"
        btype = (o.btype if o else spec.get("bt", ""))
        kind = tk.StringVar(value=(o.kind if o else spec.get("o", "coil")))
        default = self.next_m() if not is_block else self.uniq(btype.lower() + "_")
        name = tk.StringVar(value=o.name if o else default)
        par = BLOCK_PARAM.get(btype) if is_block else None
        pval = tk.StringVar(value=_fmt(o.p) if (o and par) else (_fmt(par[2]) if par else ""))
        if not is_block:
            ttk.Label(body, text="Jenis:", font=("Arial", 10, "bold")).pack(anchor="w")
            for txt, v in (("( )  Coil — nama = kondisi", "coil"), ("(S)  Set — latch ON", "set"),
                           ("(R)  Reset — latch OFF", "reset")):
                ttk.Radiobutton(body, text=txt, variable=kind, value=v).pack(anchor="w", padx=8)
        else:
            ttk.Label(body, text=f"Blok {btype}", font=("Arial", 10, "bold")).pack(anchor="w")
        ttk.Label(body, text="Nama keluaran (variabel / M0..M31):",
                font=("Arial", 10, "bold")).pack(anchor="w", pady=(8, 0))
        cb = ttk.Combobox(body, textvariable=name, width=30, values=self.signal_names())
        cb.pack(fill="x")
        if par:
            ttk.Label(body, text=f"{par[0]} ({par[1]}):", font=("Arial", 10, "bold")).pack(anchor="w", pady=(8, 0))
            ttk.Entry(body, textvariable=pval, width=12).pack(anchor="w")
        err = ttk.Label(body, text="", foreground="#c62828")
        err.pack(anchor="w", pady=(6, 0))

        def ok(_=None):
            n = name.get().strip()
            e = name_err(n)
            if e:
                err.config(text=e)
                return
            if _MEM_RE.match(n):
                err.config(text="Alamat M hanya untuk input, bukan keluaran. Gunakan nama lain.")
                return
            p = 0.0
            if par:
                try:
                    p = float(pval.get().replace(",", "."))
                    if p < 0:
                        raise ValueError
                except ValueError:
                    err.config(text=f"{par[0]} harus angka >= 0.")
                    return
            res["v"] = Out("block", n, btype, p) if is_block else Out(kind.get(), n)
            top.destroy()

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="OK", width=8, command=ok).pack(side="left")
        ttk.Button(row, text="Batal", width=8, command=top.destroy).pack(side="left", padx=(6, 0))
        top.bind("<Return>", ok)
        top.bind("<Escape>", lambda e: top.destroy())
        cb.focus_set()
        self._finish_dialog(top)
        return res["v"]

    # ── program teks / berkas ──────────────────────────────────────────────
    def compile(self):
        return compile_ladder(self.rungs, memory_map=self.memory_map)

    def to_text(self, layout=True) -> str:
        if not self.rungs:
            return ""
        text, _ = self.compile()
        if layout:
            text += self.LD_TAG + dump(self.rungs) + "\n"
        return text

    def load_text(self, text):
        ld, body = None, []
        for ln in text.splitlines():
            if ln.startswith(self.LD_TAG):
                ld = ln[len(self.LD_TAG):]
            else:
                body.append(ln)
        plain = "\n".join(body)
        nodes = L.compile_program(plain)
        rungs = None
        if ld:
            try:
                cand = load_rungs(ld)
                if _norm_text(ladder_to_text(cand, memory_map=self.memory_map)) == _norm_text(plain):
                    rungs = cand
            except Exception:
                rungs = None
        self.rungs = rungs if rungs is not None else nodes_to_ladder(nodes)
        self.sel = None
        self.dirty = False
        self.changed()
        self.say("Program dimuat" + ("" if rungs is not None else " (struktur dibangun ulang dari teks)"))
        self.redraw()

    def open_file(self, path=None):
        path = path or filedialog.askopenfilename(
            parent=self, filetypes=[("Ladder JSON", "*.json"), ("Config XML", "*.xml"), ("Teks program", "*.txt")])
        if not path:
            return
        try:
            if path.lower().endswith(".xml"):
                import xml.etree.ElementTree as ET
                root = ET.parse(path).getroot()
                prog = root.find("program")
                text = (prog.text or "") if prog is not None else ""
                if not text.strip():
                    raise ValueError("XML ini belum berisi <program>. Buka lewat Logic Editor (tombol LD).")
                self.load_text(text)
            elif path.lower().endswith(".json"):
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)

                if isinstance(raw, dict) and "rungs" in raw:
                    # Format JSON baru: rung + memory mapping
                    self.rungs = load_rungs(
                        json.dumps(raw["rungs"], ensure_ascii=False)
                    )

                    memory = raw.get("memory", {})
                    aliases = memory.get("aliases", {}) if isinstance(memory, dict) else {}

                    if not isinstance(aliases, dict):
                        raise ValueError("Format memory.aliases tidak valid.")

                    self.memory_map = {"aliases": aliases}

                else:
                    # Kompatibilitas dengan JSON lama yang hanya berisi rung
                    self.rungs = load_rungs(json.dumps(raw))
                    self.memory_map = {"aliases": {}}

                self.sel = None
                self.changed()
                self.redraw()
            else:
                with open(path, encoding="utf-8") as f:
                    self.load_text(f.read())
        except Exception as e:
            messagebox.showerror("Gagal membuka", str(e), parent=self)
            return
        self.path = path
        self.say("Dibuka: " + path)

    def save_file(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                            filetypes=[("Ladder JSON", "*.json"), ("Teks program", "*.txt")])
        if not path:
            return
        try:
            if path.lower().endswith(".txt"):
                data = self.to_text()
            else:
                old_payload = json.loads(dump(self.rungs))

                payload = {
                    "version": 1,
                    "rungs": old_payload["rungs"],
                    "memory": {
                        "aliases": self.memory_map.get("aliases", {})
                    }
                }
                data = json.dumps(payload, indent=2, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e), parent=self)
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        self.path, self.dirty = path, False
        self.say("Tersimpan: " + path)

    def open_memory_mapping(self):
        """Mengatur alias ke alamat memory PLC."""
        top = tk.Toplevel(self)
        top.title("Memory Mapping")
        top.geometry("560x420")
        top.transient(self)

        aliases = self.memory_map.setdefault("aliases", {})

        # Daftar mapping
        ttk.Label(
            top, text="Daftar Memory Mapping",
            font=("Arial", 11, "bold")
        ).pack(anchor="w", padx=10, pady=(10, 4))

        listbox = tk.Listbox(top, height=10)
        listbox.pack(fill="both", expand=True, padx=10)

        # Form input
        form = ttk.Frame(top)
        form.pack(fill="x", padx=10, pady=8)

        ttk.Label(form, text="Alias").grid(row=0, column=0, sticky="w", pady=3)
        alias_var = tk.StringVar()
        ttk.Entry(form, textvariable=alias_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3
        )

        ttk.Label(form, text="Alamat PLC").grid(
            row=1, column=0, sticky="w", pady=3
        )
        address_var = tk.StringVar()
        ttk.Entry(form, textvariable=address_var).grid(
            row=1, column=1, sticky="ew", padx=6, pady=3
        )

        ttk.Label(form, text="Tipe Data").grid(
            row=2, column=0, sticky="w", pady=3
        )
        type_var = tk.StringVar(value="BOOL")
        type_box = ttk.Combobox(
            form,
            textvariable=type_var,
            values=("BOOL", "INT"),
            state="readonly",
            width=12
        )
        type_box.grid(row=2, column=1, sticky="w", padx=6, pady=3)

        form.columnconfigure(1, weight=1)

        def refresh():
            listbox.delete(0, tk.END)
            for name, item in sorted(aliases.items()):
                listbox.insert(
                    tk.END,
                    f"{name}  ->  {item['address']}  ({item['type']})"
                )

        def select_item(_event=None):
            selected = listbox.curselection()
            if not selected:
                return

            name = sorted(aliases)[selected[0]]
            item = aliases[name]

            alias_var.set(name)
            address_var.set(item["address"])
            type_var.set(item["type"])

        listbox.bind("<<ListboxSelect>>", select_item)

        def save_mapping():
            name = alias_var.get().strip()
            address = address_var.get().strip().upper()
            data_type = type_var.get().strip().upper()

            if not name:
                messagebox.showwarning(
                    "Validasi", "Alias tidak boleh kosong.", parent=top
                )
                return

            if not (name[0].isalpha() or name[0] == "_") or not all(
                ch.isalnum() or ch == "_" for ch in name
            ):
                messagebox.showwarning(
                    "Validasi",
                    "Alias hanya boleh berisi huruf, angka, dan underscore; "
                    "karakter pertama harus huruf atau underscore.",
                    parent=top
                )
                return

            # Alamat yang didukung pada tahap ini: M0, M1, D0, D1, dan seterusnya.
            prefix = address[:1]
            suffix = address[1:]

            if prefix not in ("M", "D") or not suffix.isdigit():
                messagebox.showwarning(
                    "Validasi",
                    "Alamat harus berupa M diikuti angka atau D diikuti angka, "
                    "misalnya M0 atau D0.",
                    parent=top
                )
                return
            if prefix == "M" and int(suffix) > _MEM_MAX:
                messagebox.showwarning(
                    "Validasi", f"Alamat M melebihi M{_MEM_MAX}.", parent=top
                )
                return
            expected_type = "BOOL" if prefix == "M" else "INT"
            if data_type != expected_type:
                messagebox.showwarning(
                    "Validasi",
                    f"Alamat {prefix} harus menggunakan tipe {expected_type}.",
                    parent=top
                )
                return

            # Satu alamat tidak boleh dipakai oleh dua alias berbeda.
            for other_name, item in aliases.items():
                if other_name != name and item["address"] == address:
                    messagebox.showwarning(
                        "Validasi",
                        f"Alamat {address} sudah digunakan oleh alias "
                        f"'{other_name}'.",
                        parent=top
                    )
                    return

            aliases[name] = {
                "address": address,
                "type": data_type
            }

            refresh()
            self.dirty = True
            self.say("Memory mapping diperbarui")
            messagebox.showinfo(
                "Memory Mapping",
                f"{name} -> {address} ({data_type})\n"
                "Mapping tersimpan sementara di editor. "
                "Simpan program untuk menyimpannya ke file.",
                parent=top
            )

        def delete_mapping():
            selected = listbox.curselection()
            if not selected:
                messagebox.showwarning(
                    "Hapus Mapping",
                    "Pilih mapping yang ingin dihapus.",
                    parent=top
                )
                return

            name = sorted(aliases)[selected[0]]
            if messagebox.askyesno(
                "Konfirmasi",
                f"Hapus mapping '{name}'?",
                parent=top
            ):
                del aliases[name]
                alias_var.set("")
                address_var.set("")
                type_var.set("BOOL")
                refresh()
                self.dirty = True
                self.say("Memory mapping dihapus")

        buttons = ttk.Frame(top)
        buttons.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(
            buttons, text="Tambah / Perbarui", command=save_mapping
        ).pack(side="left")

        ttk.Button(
            buttons, text="Hapus", command=delete_mapping
        ).pack(side="left", padx=6)

        ttk.Button(
            buttons, text="Tutup", command=top.destroy
        ).pack(side="right")

        refresh()
        self._finish_dialog(top)

    def validate_ui(self):
        try:
            text, nodes = self.compile()
        except Exception as e:
            messagebox.showerror("Tidak valid", str(e), parent=self)
            return
        warns = list(Engine(nodes).warnings)
        unknown = sorted(set(self.roi_all()) - set(self.roi_names) - {"__abort"}) if self.roi_names else []
        if unknown:
            warns.append("ROI tanpa definisi: " + ", ".join(unknown))
        messagebox.showinfo("Validasi", f"OK — {len(self.rungs)} rung, {len(nodes)} node."
                            + ("".join("\n• " + w for w in warns)), parent=self)

    def show_export(self):
        try:
            text = self.to_text(layout=False)
            if not text:
                raise ValueError("Belum ada rung.")
            from plc_export import ExportViewer
            v = ExportViewer(self, text, live=self.live)
        except Exception as e:
            messagebox.showerror("ST / teks", str(e), parent=self)
            return
        if self.on_apply:
            v.transient(self)
            v.grab_set()
            v.wait_window()
            self._modal_back()

    def apply_and_close(self):
        try:
            text = self.to_text()
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e), parent=self)
            return
        if not text:
            messagebox.showerror("Program kosong", "Belum ada rung.", parent=self)
            return
        if self.roi_names:
            unknown = sorted(set(self.roi_all()) - set(self.roi_names) - {"__abort"})
            if unknown and not messagebox.askyesno(
                    "Peringatan", "Kontak ROI tanpa definisi: " + ", ".join(unknown) + "\n\nTetap terapkan?",
                    parent=self):
                return
        self.on_apply(text)
        self.close(force=True)

    def show_help(self):
        w = tk.Toplevel(self)
        w.title("Bantuan — Editor Ladder")
        w.geometry("700x560")
        tx = tk.Text(w, wrap="word", font=("TkDefaultFont", 10), padx=10, pady=8)
        tx.insert("1.0", HELP)
        tx.config(state="disabled")
        tx.pack(fill="both", expand=True)

    def close(self, force=False):
        if not force and not self.on_apply and self.dirty and self.rungs:
            if not messagebox.askyesno("Tutup", "Ada perubahan yang belum disimpan. Tutup saja?", parent=self):
                return
        if self.live_on:
            self.stop_live()
        self.stop_sim()
        self.destroy()

    # ── simulasi & monitor ─────────────────────────────────────────────────
    def toggle_sim(self):
        if self.live_on:
            self.stop_live()
        if self.sim:
            self.stop_sim()
            return
        if not self.start_engine():
            return
        self.sim = True
        self.btn_sim.config(text="■ Stop")
        self.sim_tick()

    def stop_sim(self):
        self.sim, self.engine = False, None
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.vals, self.prev, self.info = {}, {}, {}
        try:
            self.btn_sim.config(text="▶ Simulasi")
            self.redraw()
        except tk.TclError:
            pass

    def start_engine(self):
        try:
            self.engine = Engine(self.compile()[1])
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e), parent=self)
            self.engine = None
            return False
        self.t = 0.0
        return True

    def sim_tick(self):
        if not self.sim:
            return
        if self.engine is None and not self.start_engine():
            self.stop_sim()
            return
        try:
            q = self.engine.scan({n: True for n in self.inputs_on}, round(self.t, 6))
        except Exception as e:
            messagebox.showerror("Galat simulasi", str(e), parent=self)
            self.stop_sim()
            return
        self.t += 0.1
        self.vals = dict(q)
        self._add_alias_vals(self.vals)
        for n in self.roi_all() + self.m_all() + ["__abort"]:
            self.vals["$" + n] = n in self.inputs_on
        self.prev = dict(self.engine._prev)
        self._add_alias_vals(self.prev)   
        self.info = {k: v[1] for k, v in self.engine.debug().items() if v[1] is not None}
        self.redraw()
        self._job = self.after(100, self.sim_tick)

    def toggle_live(self):
        if self.live_on:
            self.stop_live()
            return
        if not self.live():
            messagebox.showinfo("Monitor", "main.py belum menghasilkan data.", parent=self)
            return
        if self.sim:
            self.stop_sim()
        self.live_on = True
        self.btn_live.config(text="■ Stop monitor", bg="#fecaca")
        self.live_tick()

    def stop_live(self):
        self.live_on = False
        if self._live_job:
            self.after_cancel(self._live_job)
            self._live_job = None
        self.vals, self.prev, self.info, self._live_key = {}, {}, {}, None
        self.live_var.set("")
        try:
            self.btn_live.config(text="● Monitor", bg=self.btn_sim.cget("bg"))
            self.redraw()
        except tk.TclError:
            pass

    def live_tick(self):
        if not self.live_on:
            return
        snap = self.live()
        if snap:
            dbg = snap["dbg"]
            vals = {k: bool(v[0]) for k, v in dbg.items()}
            self._add_alias_vals(vals)
            for r in self.rungs:
                for ser in all_series(r):
                    for el in walk(ser):
                        if (el.roi or _MEM_RE.match(el.name)) and ("in_" + el.name) in vals:
                            vals["$" + el.name] = vals["in_" + el.name]
                if (len(r.cond) == 1 and isinstance(r.cond[0], El) and r.cond[0].roi
                        and not r.cond[0].neg and r.out.name in vals):
                    vals["$" + r.cond[0].name] = vals[r.out.name]
            info = {k: v[1] for k, v in dbg.items() if v[1] is not None}
            stale = time.time() - snap.get("wall", 0) > 2.0
            ct = snap.get("cycle_time") if snap.get("cycle_time") is not None else snap.get("last_cycle")
            self.live_var.set(("⚠ TIDAK ADA UPDATE  " if stale else "") + f"{snap['state']}"
                              + (f"  siklus {ct:.2f}s" if ct is not None else "")
                              + f"  #{snap['cycles']}  {snap['msg']}")
            key = (tuple(sorted(vals.items())), tuple((k, round(v, 1)) for k, v in sorted(info.items())), stale)
            if key != self._live_key:
                self.prev, self.vals, self.info, self._live_key = self.vals, vals, info, key
                self.redraw()
        else:
            self.live_var.set("menunggu data…")
        self._live_job = self.after(100, self.live_tick)


# ═══════════════════════════════════════════════════════════════════════════
#  Mandiri
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    ed = LDEditor(root, path=arg)
    ed.protocol("WM_DELETE_WINDOW", lambda: (ed.stop_sim(), root.destroy()))
    root.mainloop()