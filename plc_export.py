# plc_export.py
"""
Ekspor program PLC (list[Node] dari plc_engine / plc_logic) ke:
  - Structured Text (ST, IEC 61131-3)
  - Ladder Diagram (LD) berbentuk teks ASCII, dengan monitoring live (kontak menyala hijau)

Pemakaian:
    from plc_export import nodes_from_text, to_st, to_ld, ExportViewer
    nodes = nodes_from_text(program_text)        # atau build_sequence_program(spec)
    st_text = to_st(nodes)
    ld_text, spans = to_ld(nodes)
    ExportViewer(parent, program_text, live=get_live)   # jendela ST/LD

Semantik yang dipertahankan dari Engine:
  * urutan node = urutan evaluasi per scan
  * "x@" = nilai x pada scan lalu  -> variabel x_prev (disalin di awal scan)
  * rujukan ke node di bawah tanpa "@" otomatis membaca nilai scan lalu (sifat alami PLC)
  * SR set-dominan, RS reset-dominan

Uji mandiri:  python plc_export.py [config.xml | program.txt] [st|ld|both]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from plc_engine import CONSTS, Node

try:
    import tkinter as tk
    from tkinter import ttk, filedialog
except ImportError:          # server tanpa Tk: fungsi ST/LD tetap bisa dipakai
    tk = None

TIMED = ("TON", "TOF", "TP")
GATES = ("AND", "OR", "XOR", "NAND", "NOR")


# ═══════════════════════════════════════════════════════════════════════════
#  Util
# ═══════════════════════════════════════════════════════════════════════════

def nodes_from_text(text: str) -> list:
    from plc_logic import compile_program
    return compile_program(text)


def parse_ref(r: str):
    """'!x@' -> ('x', True, True)  (base, negasi, scan_lalu)"""
    neg = r.startswith("!")
    b = r[1:] if neg else r
    prev = b.endswith("@")
    return (b[:-1] if prev else b), neg, prev


def _vn(s) -> str:
    """Nama valid untuk IEC (huruf/angka/_ tanpa '__')."""
    v = re.sub(r"_+", "_", re.sub(r"\W", "_", str(s))).strip("_") or "x"
    return "n_" + v if v[0].isdigit() else v


def _fmt(v) -> str:
    return format(float(v), "g")


def _t(pt) -> str:
    return f"T#{int(round(float(pt) * 1000))}ms"


class _Names:
    def __init__(self, nodes):
        self.m, used = {}, set()
        for n in nodes:
            base = v = _vn(n.id)
            k = 2
            while v.lower() in used:
                v, k = f"{base}_{k}", k + 1
            used.add(v.lower())
            self.m[n.id] = v

    def __getitem__(self, k):
        if k not in self.m:
            raise ValueError(f"referensi '{k}' tidak ditemukan")
        return self.m[k]


def _prev_bases(nodes) -> list:
    ids = {n.id for n in nodes}
    seen = []
    for n in nodes:
        for r in n.inputs:
            b, _, p = parse_ref(r)
            if p and b in ids and b not in seen:
                seen.append(b)
    return seen


def _input_var(n) -> str:
    return "I_" + _vn(n.params.get("name", n.id))


# ═══════════════════════════════════════════════════════════════════════════
#  Structured Text
# ═══════════════════════════════════════════════════════════════════════════

def _st_ref(r, nm, flip=False, paren=False) -> str:
    b, neg, prev = parse_ref(r)
    neg = neg ^ flip
    if b in CONSTS:
        return "TRUE" if (CONSTS[b] ^ neg) else "FALSE"
    name = nm[b] + ("_prev" if prev else "")
    if not neg:
        return name
    return f"(NOT {name})" if paren else f"NOT {name}"


def _st_gate(t, refs, nm) -> str:
    if t in ("NAND", "NOR"):
        if len(refs) == 1:
            return _st_ref(refs[0], nm, flip=True)
        op = "AND" if t == "NAND" else "OR"
        return "NOT (" + f" {op} ".join(_st_ref(r, nm, paren=True) for r in refs) + ")"
    return f" {t} ".join(_st_ref(r, nm, paren=len(refs) > 1) for r in refs)


def to_st(nodes: list, program: str = "CT_Monitor") -> str:
    nm = _Names(nodes)
    prevs = _prev_bases(nodes)
    inputs = []
    for n in nodes:
        if n.type == "IN" and _input_var(n) not in inputs:
            inputs.append(_input_var(n))

    L = [f"PROGRAM {program}", "VAR_INPUT"]
    L += [f"    {v} : BOOL;   // bit ROI (hasil deteksi)" for v in inputs]
    L += ["END_VAR", "", "VAR"]
    L += [f"    {nm[n.id]} : BOOL;" for n in nodes]
    L += [f"    {nm[b]}_prev : BOOL;   // nilai {nm[b]} pada scan lalu" for b in prevs]
    for n in nodes:
        if n.type in TIMED or n.type in ("R_TRIG", "F_TRIG", "CTU", "SR", "RS"):
            L.append(f"    fb_{nm[n.id]} : {n.type};")
    L += ["END_VAR", ""]

    if prevs:
        L.append("// --- nilai scan lalu (untuk referensi x@) ---")
        L += [f"{nm[b]}_prev := {nm[b]};" for b in prevs]
        L.append("")
    L.append("// --- logika (urutan = urutan evaluasi) ---")

    for n in nodes:
        o, t, a = nm[n.id], n.type, n.inputs
        fb = f"fb_{o}"
        if t == "IN":
            L.append(f"{o} := {_input_var(n)};")
        elif t == "NOT":
            L.append(f"{o} := {_st_ref(a[0], nm, flip=True)};")
        elif t in GATES:
            L.append(f"{o} := {_st_gate(t, a, nm)};")
        elif t in TIMED:
            L.append(f"{fb}(IN := {_st_ref(a[0], nm)}, PT := {_t(n.params.get('pt', 0))});")
            L.append(f"{o} := {fb}.Q;")
        elif t in ("R_TRIG", "F_TRIG"):
            L.append(f"{fb}(CLK := {_st_ref(a[0], nm)});")
            L.append(f"{o} := {fb}.Q;")
        elif t == "CTU":
            pv = int(round(float(n.params.get("pv", 1))))
            L.append(f"{fb}(CU := {_st_ref(a[0], nm)}, RESET := {_st_ref(a[1], nm)}, PV := {pv});")
            L.append(f"{o} := {fb}.Q;")
        elif t == "SR":
            L.append(f"{fb}(SET1 := {_st_ref(a[0], nm)}, RESET := {_st_ref(a[1], nm)});")
            L.append(f"{o} := {fb}.Q1;")
        elif t == "RS":
            L.append(f"{fb}(SET := {_st_ref(a[0], nm)}, RESET1 := {_st_ref(a[1], nm)});")
            L.append(f"{o} := {fb}.Q1;")
        else:
            raise ValueError(f"tipe node tidak didukung: {t}")
    L.append("END_PROGRAM")
    return "\n".join(L) + "\n"


# ═══════════════════════════════════════════════════════════════════════════
#  Ladder: model rung
# ═══════════════════════════════════════════════════════════════════════════
# Ekspresi: ("c", label, neg, key) kontak | ("s", [e..]) seri | ("p", [e..]) paralel
# key = id node asli (untuk warna live) atau None

@dataclass
class Rung:
    title: str
    expr: object = None          # ekspresi syarat (rung biasa)
    out: str = ""                # variabel keluaran
    coil: str = "N"              # N = ( ), S = (S), R = (R)
    block: Optional[tuple] = None   # (tipe, teks_param, [(pin, ekspresi)])
    key: Optional[str] = None    # id node untuk warna coil


def _ct(r, nm):
    b, neg, prev = parse_ref(r)
    if b in CONSTS:
        return ("c", "TRUE" if (CONSTS[b] ^ neg) else "FALSE", False, None)
    return ("c", nm[b] + ("@" if prev else ""), neg, None if prev else b)


def _flip(c):
    return ("c", c[1], not c[2], c[3])


def _xor_expr(cs, owner, pre):
    cur = cs[0]
    for k, c in enumerate(cs[1:], 1):
        e = ("p", [("s", [cur, _flip(c)]), ("s", [_flip(cur), c])])
        if k == len(cs) - 1:
            return e
        tmp = f"{owner}_x{k}"
        pre.append(Rung(f"{owner} (antara XOR {k})", e, tmp))
        cur = ("c", tmp, False, None)
    return cur


def _gate_expr(t, refs, nm, owner, pre):
    cs = [_ct(r, nm) for r in refs]
    if len(cs) == 1:
        return _flip(cs[0]) if t in ("NAND", "NOR") else cs[0]
    if t == "AND":
        return ("s", cs)
    if t == "OR":
        return ("p", cs)
    if t == "NAND":
        return ("p", [_flip(c) for c in cs])
    if t == "NOR":
        return ("s", [_flip(c) for c in cs])
    return _xor_expr(cs, owner, pre)


def build_rungs(nodes: list) -> list:
    nm = _Names(nodes)
    rungs = []
    for b in _prev_bases(nodes):
        rungs.append(Rung(f"salin {b} -> {b}@ (scan lalu)", ("c", nm[b], False, b), nm[b] + "@"))
    for n in nodes:
        t, o, a = n.type, nm[n.id], n.inputs
        if t == "IN":
            rungs.append(Rung(n.id, ("c", _input_var(n), False, n.id), o, key=n.id))
        elif t == "NOT":
            rungs.append(Rung(n.id, _flip(_ct(a[0], nm)), o, key=n.id))
        elif t in GATES:
            pre = []
            e = _gate_expr(t, a, nm, o, pre)
            rungs += pre
            rungs.append(Rung(f"{n.id}  [{t}]", e, o, key=n.id))
        elif t in TIMED:
            pt = _t(n.params.get("pt", 0))
            rungs.append(Rung(f"{n.id}  [{t} {pt}]", None, o, key=n.id,
                              block=(t, pt, [("IN", _ct(a[0], nm))])))
        elif t in ("R_TRIG", "F_TRIG"):
            rungs.append(Rung(f"{n.id}  [{t}]", None, o, key=n.id,
                              block=(t, "", [("CLK", _ct(a[0], nm))])))
        elif t == "CTU":
            pv = f"PV={int(round(float(n.params.get('pv', 1))))}"
            rungs.append(Rung(f"{n.id}  [CTU {pv}]", None, o, key=n.id,
                              block=("CTU", pv, [("CU", _ct(a[0], nm)), ("R", _ct(a[1], nm))])))
        elif t in ("SR", "RS"):
            s, r = _ct(a[0], nm), _ct(a[1], nm)
            # urutan rung menentukan dominasi: yang terakhir menang
            order = [(r, "R"), (s, "S")] if t == "SR" else [(s, "S"), (r, "R")]
            for e, c in order:
                rungs.append(Rung(f"{n.id}  [{t}: {'SET' if c == 'S' else 'RESET'}]", e, o, coil=c))
        else:
            raise ValueError(f"tipe node tidak didukung: {t}")
    return rungs


# ═══════════════════════════════════════════════════════════════════════════
#  Ladder: layout & gambar ASCII
# ═══════════════════════════════════════════════════════════════════════════

def _size(e):
    if e[0] == "c":
        return 1, 1
    ws, hs = zip(*[_size(x) for x in e[1]])
    return (sum(ws), max(hs)) if e[0] == "s" else (max(ws), sum(hs))


def _labels(e):
    return [e[1]] if e[0] == "c" else [s for x in e[1] for s in _labels(x)]


def _fit(s, w):
    return s if len(s) <= w else s[:max(1, w - 1)] + "~"


class _Grid:
    def __init__(self, nlines, width, cw):
        self.g = [[" "] * width for _ in range(nlines)]
        self.cw, self.ox, self.spans = cw, 1, []

    def put(self, ln, x, s):
        row = self.g[ln]
        for i, ch in enumerate(s):
            if 0 <= x + i < len(row):
                row[x + i] = ch

    def hline(self, ln, x0, x1):
        row = self.g[ln]
        for x in range(max(0, x0), min(len(row), x1)):
            if row[x] == " ":
                row[x] = "-"

    def lines(self):
        return ["".join(r).rstrip() for r in self.g]

    def draw(self, e, col, row, alloc):
        cw, wl = self.cw, 2 * row + 1
        x0 = self.ox + col * cw
        k = e[0]
        if k == "c":
            _, label, neg, key = e
            self.hline(wl, x0, x0 + alloc * cw)
            sx = x0 + (cw - 3) // 2
            self.put(wl, sx, "]/[" if neg else "] [")
            lab = _fit(label, cw - 2)
            lx = x0 + (cw - len(lab)) // 2
            self.put(2 * row, lx, lab)
            if key is not None:
                self.spans.append((wl, sx, sx + 3, key, neg))
                self.spans.append((2 * row, lx, lx + len(lab), key, neg))
        elif k == "s":
            x = col
            for sub in e[1]:
                w = _size(sub)[0]
                self.draw(sub, x, row, w)
                x += w
            self.hline(wl, self.ox + x * cw, x0 + alloc * cw)
        else:
            r, wls = row, []
            for sub in e[1]:
                self.draw(sub, col, r, alloc)
                wls.append(2 * r + 1)
                r += _size(sub)[1]
            xl, xr = x0, x0 + alloc * cw - 1
            for x in (xl, xr):
                for ln in range(wls[0], wls[-1] + 1):
                    if ln in wls:
                        self.g[ln][x] = "+"
                    elif self.g[ln][x] == " ":
                        self.g[ln][x] = "|"


def _rung_grid(rg: Rung, cw: int, body: int):
    if rg.block:
        typ, par, pins = rg.block
        H = sum(_size(e)[1] for _, e in pins)
        inw = max(_size(e)[0] for _, e in pins)
        nlines = 2 * H + 1
    else:
        ew, H = _size(rg.expr)
        nlines = 2 * H
    width = 1 + (body + 1) * cw + 1
    rail = width - 1
    g = _Grid(nlines, width, cw)

    if rg.block:
        r, wls = 0, []
        for _, e in pins:
            g.draw(e, 0, r, inw)
            wls.append(2 * r + 1)
            r += _size(e)[1]
        bx, bw = 1 + inw * cw, 2 * cw
        edge = "+" + "-" * (bw - 2) + "+"
        g.put(0, bx, edge)
        g.put(nlines - 1, bx, edge)
        for ln in range(1, nlines - 1):
            g.g[ln][bx] = g.g[ln][bx + bw - 1] = "|"
        g.put(0, bx + 2, " " + _fit(f"{typ} {par}".strip(), bw - 6) + " ")
        for wl, (pin, _) in zip(wls, pins):
            g.g[wl][bx] = "+"
            g.put(wl, bx + 2, pin)
        g.g[1][bx + bw - 1] = "+"
        g.put(1, bx + bw - 3, "Q")
        g.hline(1, bx + bw, rail)
    else:
        g.draw(rg.expr, 0, 0, ew)
        g.hline(1, 1 + ew * cw, rail)

    cx0 = 1 + body * cw
    sym = {"N": "( )", "S": "(S)", "R": "(R)"}[rg.coil]
    sx = cx0 + (cw - 3) // 2
    g.put(1, sx, sym)
    lab = _fit(rg.out, cw - 2)
    lx = cx0 + (cw - len(lab)) // 2
    g.put(0, lx, lab)
    if rg.coil == "N" and rg.key:
        g.spans.append((1, sx, sx + 3, rg.key, False))
        g.spans.append((0, lx, lx + len(lab), rg.key, False))
    for ln in range(nlines):
        g.g[ln][0] = "|"
        g.g[ln][rail] = "|"
    return g


LD_LEGEND = [
    "(* LADDER — dibuat otomatis dari program PLC *)",
    "(*  ] [  kontak NO    ]/[  kontak NC    ( )  coil    (S) set    (R) reset  *)",
    "(*  x@   = nilai x pada scan lalu (disalin di rung paling atas)            *)",
    "(*  SR/RS = pasangan coil S/R; rung terakhir menang (SR: set, RS: reset)   *)",
    "(*  Kotak = function block IEC; Q = keluaran                               *)",
    "",
]


def to_ld(nodes: list):
    """Return (teks_ladder, spans). spans = [(baris0, kol_awal, kol_akhir, id_node, negasi)]."""
    rungs = build_rungs(nodes)
    labs = []
    for rg in rungs:
        labs.append(rg.out)
        if rg.expr:
            labs += _labels(rg.expr)
        if rg.block:
            for _, e in rg.block[2]:
                labs += _labels(e)
    cw = min(max(max((len(s) for s in labs), default=6) + 4, 11), 22)

    def body(rg):
        if rg.block:
            return max(_size(e)[0] for _, e in rg.block[2]) + 2
        return _size(rg.expr)[0]

    maxb = max((body(r) for r in rungs), default=1)
    out, spans = list(LD_LEGEND), []
    for i, rg in enumerate(rungs, 1):
        out.append(f"Rung {i}: {rg.title}")
        g = _rung_grid(rg, cw, maxb)
        base = len(out)
        out += g.lines()
        spans += [(base + ln, c0, c1, k, neg) for ln, c0, c1, k, neg in g.spans]
        out.append("")
    out.append("(* END *)")
    return "\n".join(out) + "\n", spans


# ═══════════════════════════════════════════════════════════════════════════
#  Jendela viewer (Tk)
# ═══════════════════════════════════════════════════════════════════════════

if tk is not None:
    class ExportViewer(tk.Toplevel):
        """Tampilan ST / LD. live = callable -> snapshot {'dbg': {id: (nilai, info)}} atau None."""

        def __init__(self, master, program_text: str, live=None, mode: str = "LD"):
            nodes = nodes_from_text(program_text)      # galat -> ke pemanggil
            self.ST = to_st(nodes)
            self.LD, self.spans = to_ld(nodes)
            super().__init__(master)
            self.title("Tampilan Ladder (LD) / Structured Text (ST)")
            self.geometry("1100x700")
            self.live, self._job, self._key = live, None, None
            self.mode = tk.StringVar(value=mode)
            self.mon = tk.BooleanVar(value=False)

            bar = ttk.Frame(self, padding=4)
            bar.pack(side="top", fill="x")
            ttk.Radiobutton(bar, text="Ladder (LD)", variable=self.mode, value="LD",
                            command=self._show).pack(side="left")
            ttk.Radiobutton(bar, text="Structured Text (ST)", variable=self.mode, value="ST",
                            command=self._show).pack(side="left", padx=(8, 0))
            if live:
                ttk.Checkbutton(bar, text="Monitor live (LD)", variable=self.mon,
                                command=self._toggle_mon).pack(side="left", padx=(16, 0))
            ttk.Button(bar, text="Simpan…", command=self._save).pack(side="right")
            ttk.Button(bar, text="Salin", command=self._copy).pack(side="right", padx=(0, 6))

            frm = ttk.Frame(self)
            frm.pack(fill="both", expand=True)
            self.txt = tk.Text(frm, font=("Consolas", 10), wrap="none", undo=False)
            sy = ttk.Scrollbar(frm, orient="vertical", command=self.txt.yview)
            sx = ttk.Scrollbar(frm, orient="horizontal", command=self.txt.xview)
            self.txt.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
            sy.pack(side="right", fill="y")
            sx.pack(side="bottom", fill="x")
            self.txt.pack(side="left", fill="both", expand=True)
            self.txt.tag_configure("on", foreground="#15803d", background="#dcfce7")
            self.protocol("WM_DELETE_WINDOW", self._close)
            self._show()

        def _current(self) -> str:
            return self.LD if self.mode.get() == "LD" else self.ST

        def _show(self):
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", self._current())
            self.txt.config(state="disabled")
            self._key = None
            self._apply_live()

        def _copy(self):
            self.clipboard_clear()
            self.clipboard_append(self._current())

        def _save(self):
            ext = ".ld.txt" if self.mode.get() == "LD" else ".st"
            path = filedialog.asksaveasfilename(parent=self, defaultextension=ext,
                                                filetypes=[("Teks", "*.txt *.st"), ("Semua", "*.*")])
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._current())

        def _toggle_mon(self):
            if self.mon.get():
                self._tick()
            elif self._job:
                self.after_cancel(self._job)
                self._job = None
                self._key = None
                self.txt.tag_remove("on", "1.0", "end")

        def _apply_live(self):
            self.txt.tag_remove("on", "1.0", "end")
            if not (self.live and self.mon.get() and self.mode.get() == "LD"):
                return
            snap = self.live()
            if not snap:
                return
            dbg = snap["dbg"]
            state = tuple(sorted((k, bool(v[0])) for k, v in dbg.items()))
            for ln, c0, c1, key, neg in self.spans:
                if key in dbg and (bool(dbg[key][0]) != neg):
                    self.txt.tag_add("on", f"{ln + 1}.{c0}", f"{ln + 1}.{c1}")
            self._key = state

        def _tick(self):
            if not self.mon.get():
                return
            snap = self.live() if self.live else None
            if snap:
                state = tuple(sorted((k, bool(v[0])) for k, v in snap["dbg"].items()))
                if state != self._key:
                    self._apply_live()
            self._job = self.after(150, self._tick)

        def _close(self):
            if self._job:
                self.after_cancel(self._job)
            self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
#  Uji mandiri / CLI
# ═══════════════════════════════════════════════════════════════════════════

def _demo_text() -> str:
    from plc_engine import SeqSpec, StepSpec, build_sequence_program
    from plc_logic import program_to_text
    spec = SeqSpec(StepSpec("in"), StepSpec("out"),
                   [StepSpec("s1", "expected", "scan"),
                    StepSpec("s2", "mandatory", "pasang", dwell=0.2)])
    return program_to_text(build_sequence_program(spec))


if __name__ == "__main__":
    import sys
    src = None
    if len(sys.argv) > 1:
        p = sys.argv[1]
        if p.lower().endswith(".xml"):
            from logic_editor import load_logic_xml
            from logic_bridge import template_text
            cfg = load_logic_xml(p)
            src = cfg.program.strip() or template_text(cfg)
        else:
            with open(p, encoding="utf-8") as f:
                src = f.read()
    else:
        src = _demo_text()
    what = sys.argv[2].lower() if len(sys.argv) > 2 else "both"
    nodes = nodes_from_text(src)
    if what in ("st", "both"):
        print(to_st(nodes))
    if what in ("ld", "both"):
        print(to_ld(nodes)[0])