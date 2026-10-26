# plc_editor.py
"""
Editor PLC visual (FBD, gaya TIA Portal) di atas plc_logic.py / plc_engine.py.

Tanpa mengetik program: seret blok, tarik kabel, klik kanan port.

Cara pakai
  - Klik tombol blok di panel kiri -> blok muncul di kanvas.
  - Tarik dari port KELUARAN (titik kanan blok) ke port MASUKAN (titik kiri blok) = kabel.
  - Tarik ujung kabel dari port masukan = angkat / pindahkan kabel.
  - Klik kanan port masukan : Negasi (!), Nilai scan lalu (@), Konstanta TRUE/FALSE, Putuskan.
  - Klik ganda blok         : ubah nama + parameter (PT detik / PV hitungan).
  - Klik kanan blok         : tambah/kurangi input gerbang, hapus.  Tombol Del = hapus blok terpilih.
  - Blok IN = bit ROI (sama dengan $nama di bahasa teks). Nama ROI harus sama dengan nama HitRule.
  - Konvensi nama keluaran (dibaca ProgramRunner): done_<roi>, skip_<roi>, start, run, done_<output>.
  - Simulasi: klik IN untuk ON/OFF, kabel hijau = TRUE (memakai Engine yang sama dengan produksi).

Berkas: membuka/menyimpan config JSON plc_logic (mode "program"); posisi blok disimpan di
cfg["layout"] (diabaikan oleh Pipeline). Config mode template otomatis dikonversi
(lalu "Simpan" meminta nama berkas baru supaya template asli tidak tertimpa).

Jalankan:  python plc_editor.py [config.json]
"""
from __future__ import annotations
import tkinter.font as tkfont
import json
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import time
from plc_engine import CONSTS, Engine, Node
import plc_logic as L

# ── Konstanta tampilan ─────────────────────────────────────────────────────
W, HEAD, ROW = 150, 26, 22
GATES = ("AND", "OR", "XOR", "NAND", "NOR")
MULTI = set(GATES)
FIXED_IN = {"IN": 0, "TON": 1, "TOF": 1, "TP": 1, "R_TRIG": 1, "F_TRIG": 1,
            "SR": 2, "RS": 2, "CTU": 2}
PORT_LABELS = {"TON": ["IN"], "TOF": ["IN"], "TP": ["IN"], "R_TRIG": ["CLK"],
               "F_TRIG": ["CLK"], "SR": ["S1", "R"], "RS": ["S", "R1"], "CTU": ["CU", "R"]}
PARAM = {"TON": ("pt", "PT", "s", 0.3), "TOF": ("pt", "PT", "s", 0.2),
         "TP": ("pt", "PT", "s", 0.5), "CTU": ("pv", "PV", "", 3)}
HEAD_COL = {"IN": "#bbf7d0", "AND": "#bfdbfe", "OR": "#bfdbfe", "XOR": "#bfdbfe",
            "NAND": "#bfdbfe", "NOR": "#bfdbfe", "TON": "#fde68a", "TOF": "#fde68a",
            "TP": "#fde68a", "R_TRIG": "#ddd6fe", "F_TRIG": "#ddd6fe",
            "SR": "#fecaca", "RS": "#fecaca", "CTU": "#a5f3fc"}
PALETTE = [
    ("Input", ["IN"]),
    ("Gerbang", list(GATES)),
    ("Timer", ["TON", "TOF", "TP"]),
    ("Pemicu tepi", ["R_TRIG", "F_TRIG"]),
    ("Latch", ["SR", "RS"]),
    ("Counter", ["CTU"]),
]
ID_OK = re.compile(r"[^\s&|^!(),#$=@]+$")
FONT = ("TkDefaultFont", 9)
FONT_B = ("TkDefaultFont", 9, "bold")
BLOCK_HELP = {
    "IN":  "Bit ROI dari kamera. Nama harus sama dengan nama ROI (HitRule).\nNama '__abort' = sinyal abort dari main.py.",
    "AND":    "TRUE bila SEMUA input TRUE.\nInput kosong diabaikan.",
    "OR":     "TRUE bila SALAH SATU input TRUE.",
    "XOR":    "TRUE bila jumlah input TRUE ganjil.",
    "NAND":   "Kebalikan AND.",
    "NOR":    "Kebalikan OR.",
    "TON":    "Timer tunda-ON: keluaran TRUE setelah IN\nTRUE terus selama PT detik.",
    "TOF": "Tunda-OFF: TRUE selama IN TRUE, lalu tetap TRUE\nPT detik sejak scan terakhir IN TRUE.",
    "TP":  "Pulsa: tepi naik IN memicu TRUE selama PT detik.\nPemicu baru diabaikan selama pulsa aktif. PT = 0 tidak menghasilkan pulsa.",
    "R_TRIG": "Tepi naik: TRUE hanya 1 scan saat CLK naik.",
    "F_TRIG": "Tepi turun: TRUE hanya 1 scan saat CLK turun.",
    "SR":     "Latch, SET dominan.\nS1 = set, R = reset.",
    "RS":     "Latch, RESET dominan.\nS = set, R1 = reset.",
    "CTU": "Menghitung tepi naik CU. Q TRUE saat hitungan >= PV.\nHitungan terus naik melewati PV. R (prioritas) mereset ke 0.",}

HELP_PAGES = [
    ("Dasar", """
MEMBUAT DIAGRAM
- Klik tombol blok di panel kiri, atau klik kanan di area kosong > Tambah blok.
- Seret blok untuk memindahkan (posisi snap ke grid 10 px).
- Tarik dari titik KANAN blok (keluaran) ke titik KIRI blok lain (masukan) untuk membuat kabel.
- Tarik ujung kabel dari port masukan untuk memindahkan atau melepasnya.

MENGEDIT
- Klik ganda blok: ubah nama dan parameter (PT detik / PV hitungan).
- Klik kanan port masukan: Negasi (!), Nilai scan lalu (@), Konstanta TRUE/FALSE, Putuskan.
- Klik kanan gerbang: tambah / kurangi jumlah input.
- Del / Backspace: hapus blok terpilih.

SIMBOL
- Lingkaran merah di port = negasi (!).
- Tanda @ oranye = nilai scan sebelumnya.
- Kabel putus-putus = kabel memakai @.
- Teks ungu di port = konstanta TRUE / FALSE.
"""),
    ("Blok", "\n\n".join(f"{k}\n{v}" for k, v in BLOCK_HELP.items())),
    ("Penamaan", """
- Blok IN: nama = nama ROI di HitRule. Nama yang tidak punya ROI menghasilkan peringatan saat Pipeline dibuat.
- Nama node tidak boleh memuat spasi atau & | ^ ! ( ) , # $ = @ dan tidak boleh TRUE, FALSE, AND, OR, XOR, NOT.

Konvensi nama keluaran (dibaca ProgramRunner):
- done_<roi>  : ROI selesai (muncul di panel status + waktu)
- skip_<roi>  : ROI dilewati
- start       : memulai siklus (waktu 0)
- run         : siklus sedang berjalan
- done_<output> : siklus selesai (isi cycle.finish di config)
"""),
    ("Simulasi", """
- Tombol Simulasi memakai Engine yang sama dengan produksi, scan tiap 0,1 detik.
- Klik blok IN untuk ON/OFF.
- Kabel hijau = TRUE, abu-abu = FALSE.
- Mengubah diagram saat simulasi berjalan membuat Engine dibangun ulang (state timer dan counter kembali ke awal).
- Simulasi berhenti sendiri bila diagram tidak valid.
"""),
    ("Urutan scan", """
- Urutan evaluasi ditentukan otomatis dari kabel: blok yang jadi sumber dievaluasi lebih dulu.
- Kabel umpan balik (melingkar) atau kabel @ dibaca dari scan sebelumnya, tertunda 1 scan.
- Gunakan latch (SR/RS) atau kabel @ untuk membuat memori antar scan.
"""),
]


class Tip:
    def __init__(self, w, text):
        self.w, self.text, self.tw = w, text, None
        w.bind("<Enter>", self.show)
        w.bind("<Leave>", self.hide)

    def show(self, e=None):
        if self.tw:
            return
        self.tw = tk.Toplevel(self.w)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{self.w.winfo_rootx() + self.w.winfo_width() + 6}+{self.w.winfo_rooty()}")
        tk.Label(self.tw, text=self.text, justify="left", bg="#fffbe6", relief="solid",
                 borderwidth=1, padx=6, pady=3, font=FONT).pack()

    def hide(self, e=None):
        if self.tw:
            self.tw.destroy()
            self.tw = None

class Editor(tk.Toplevel):
    POS_TAG = "#@pos"

    def __init__(self, master=None, path=None, program=None, roi_names=None, on_apply=None, live=None):
        super().__init__(master)
        self.title("Editor PLC Visual (FBD)")
        self.geometry("1280x760")
        self.blocks: dict = {}      # uid -> {type,name,x,y,ins:[ref|None],p}
        self.n = 0
        self.sel = None
        self.drag = None
        self.cfg = None
        self.path = None
        self.sim, self.engine, self.t, self.q = False, None, 0.0, {}

        # Mapping alias dan alamat PLC
        self.memory = {
            "aliases": {},
            "values": {}
        }
        self.inputs_on: set = set()
        self.nid2uid: dict = {}
        self._job = None
        self.warns = []
        self.roi_names = list(roi_names or [])
        self.on_apply = on_apply
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.zoom = 1.0
        self.zoom_var = tk.StringVar(value="100%")
        base = tkfont.nametofont("TkDefaultFont")
        self.cf = base.copy()
        self.cf.configure(size=9)
        self.cf_b = base.copy()
        self.cf_b.configure(size=9, weight="bold")
        self.live = live                    # callable -> snapshot dict | None
        self.live_on, self.info = False, {}
        self._live_job, self._live_key = None, None
        self.live_var = tk.StringVar(value="")
        self._build_ui()
        if program is not None:
            try:
                self.load_text(program)
            except Exception:
                self.destroy()
                raise
        elif path:
            self.open_cfg(path)
        if on_apply:                        # mode dialog (dipanggil dari logic_editor)
            self.transient(master)
            self.wait_visibility()
            self.grab_set()

    def close(self):
        if self.live_on:
            self.stop_live()
        self.stop_sim()
        self.destroy()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        top = tk.Frame(self, bg="#e2e8f0")
        top.pack(side="top", fill="x")
        for text, cmd in [("Baru", self.new), ("Buka config…", self.open_cfg),
                          ("Simpan", self.save_cfg),
                          ("Simpan sebagai…", lambda: self.save_cfg(True)),
                          ("Lihat teks", self.show_text),
                          ("Bantuan (F1)", self.show_help)]:
            if self.on_apply and text.startswith(("Buka", "Simpan")):
                continue
            tk.Button(top, text=text, command=cmd).pack(side="left", padx=2, pady=3)
        self.btn_sim = tk.Button(
            top,
            text="▶ Simulasi",
            command=self.toggle_sim
        )
        self.btn_sim.pack(side="left", padx=12)

        if self.live:
            self.btn_live = tk.Button(
                top,
                text="● Monitoring",
                command=self.toggle_live
            )
            self.btn_live.pack(side="left", padx=2)

            tk.Label(
                top,
                textvariable=self.live_var,
                bg="#e2e8f0",
                fg="#b91c1c",
                font=FONT_B
            ).pack(side="left", padx=8)
        tk.Label(top, text="Tampilan:", bg="#e2e8f0").pack(side="left", padx=(16, 2))
        tk.Button(top, text="−", width=2, command=lambda: self.zoom_at(1 / 1.25)).pack(side="left")
        tk.Label(top, textvariable=self.zoom_var, width=5, bg="#e2e8f0").pack(side="left")
        tk.Button(top, text="+", width=2, command=lambda: self.zoom_at(1.25)).pack(side="left")
        tk.Button(top, text="Reset", command=self.reset_view).pack(side="left", padx=(6, 2))
        tk.Button(top, text="Muat semua", command=self.fit_view).pack(side="left")
        if self.on_apply:
            tk.Button(top, text="Batal", command=self.close).pack(side="right", padx=4, pady=3)
            tk.Button(top, text="✔ Terapkan ke program", bg="#bbf7d0",
                      command=self.apply_and_close).pack(side="right", padx=2, pady=3)

        body = tk.Frame(self)
        body.pack(fill="both", expand=True)
        pal = tk.Frame(body, width=130, bg="#f1f5f9")
        pal.pack(side="left", fill="y")
        for title, types in PALETTE:
            tk.Label(pal, text=title, bg="#f1f5f9", font=FONT_B, anchor="w").pack(fill="x", padx=6, pady=(8, 0))
            for t in types:
                btn = tk.Button(pal, text=t, width=12, command=lambda t=t: self.add_block(t))
                btn.pack(padx=6, pady=1)
                Tip(btn, BLOCK_HELP[t])

        wrap = tk.Frame(body)
        wrap.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg="#f8fafc", scrollregion=(0, 0, 4000, 3000),
                                highlightthickness=0)
        sx = tk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        sy = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        sx.pack(side="bottom", fill="x")
        sy.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.status = tk.StringVar(value="Tekan F1 untuk bantuan  •  Tarik dari port kanan ke port kiri = kabel  •  Klik kanan port = negasi / @ / konstanta")
        tk.Label(self, textvariable=self.status, anchor="w", bg="#e2e8f0").pack(side="bottom", fill="x")

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_down)
        c.bind("<B1-Motion>", self.on_move)
        c.bind("<ButtonRelease-1>", self.on_up)
        c.bind("<Double-Button-1>", self.on_double)
        right = ("<Button-3>", "<Control-Button-1>") + (("<Button-2>",) if sys.platform == "darwin" else ())
        for ev in right:
            c.bind(ev, self.on_right)
        if sys.platform != "darwin":            # tombol tengah = geser bebas
            c.bind("<ButtonPress-2>", lambda e: c.scan_mark(e.x, e.y))
            c.bind("<B2-Motion>", lambda e: c.scan_dragto(e.x, e.y, gain=1))
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            c.bind(ev, self.on_wheel)
        c.bind("<Delete>", lambda e: self.delete_block(self.sel) if self.sel else None)
        c.bind("<BackSpace>", lambda e: self.delete_block(self.sel) if self.sel else None)
        self.bind("<F1>", lambda e: self.show_help())
        self.bind("<Control-0>", lambda e: self.reset_view())
        self.bind("<Control-equal>", lambda e: self.zoom_at(1.25))
        self.bind("<Control-plus>", lambda e: self.zoom_at(1.25))
        self.bind("<Control-minus>", lambda e: self.zoom_at(1 / 1.25))
        self.redraw()

    # ── Model ─────────────────────────────────────────────────────────────
    def unique(self, base: str, is_in: bool) -> str:
        names = {b["name"] for b in self.blocks.values() if (b["type"] == "IN") == is_in}
        k = 1
        while f"{base}{k}" in names:
            k += 1
        return f"{base}{k}"

    def add_block(self, t, x=None, y=None, name=None, p=None, nin=None):
        if x is None:
            k = len(self.blocks) % 8
            x = round(self.canvas.canvasx(60 + k * 24) / self.zoom / 10) * 10
            y = round(self.canvas.canvasy(60 + k * 24) / self.zoom / 10) * 10
        self.n += 1
        uid = f"b{self.n}"
        if nin is None:
            nin = 2 if t in MULTI else FIXED_IN[t]
        if p is None and t in PARAM:
            p = PARAM[t][3]
        if name is None and t == "IN" and self.roi_names:
            used = {b["name"] for b in self.blocks.values() if b["type"] == "IN"}
            name = next((r for r in self.roi_names if r not in used), None)
        name = name or self.unique("in" if t == "IN" else t.lower(), t == "IN")
        self.blocks[uid] = {"type": t, "name": name, "x": x, "y": y, "ins": [None] * nin, "p": p}
        self.sel = uid
        self.changed()
        self.redraw()
        return uid

    def delete_block(self, uid):
        if uid not in self.blocks:
            return
        del self.blocks[uid]
        for b in self.blocks.values():
            b["ins"] = [None if (r and r.get("src") == uid) else r for r in b["ins"]]
        self.sel = None
        self.changed()
        self.redraw()

    def changed(self):
        self.engine = None          # simulasi dibangun ulang pada tick berikutnya

    def new(self):
        if self.blocks and not messagebox.askyesno("Baru", "Hapus semua blok?"):
            return
        self.blocks, self.n, self.sel, self.cfg, self.path = {}, 0, None, None, None
        self.changed()
        self.redraw()

    def node_id(self, uid):
        b = self.blocks[uid]
        return "in_" + b["name"] if b["type"] == "IN" else b["name"]

    def check_name(self, uid, t, s):
        if not s or not ID_OK.match(s):
            return "Nama kosong atau memuat karakter terlarang (spasi & | ^ ! ( ) , # $ = @)."
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return "Nama tidak boleh berupa angka saja."       
        if s in CONSTS or s in L._KW:
            return f"'{s}' adalah kata cadangan."
        is_in = t == "IN"
        for u, b in self.blocks.items():
            if u != uid and (b["type"] == "IN") == is_in and b["name"] == s:
                return f"Nama '{s}' sudah dipakai."
        in_ids = {"in_" + b["name"] for u, b in self.blocks.items() if b["type"] == "IN" and u != uid}
        if not is_in and s in in_ids:
            return f"Nama '{s}' bentrok dengan input."
        if is_in and any(b["type"] != "IN" and b["name"] == "in_" + s for b in self.blocks.values()):
            return f"Input '{s}' bentrok dengan nama node."
        return None
    

    # ── Geometri ──────────────────────────────────────────────────────────
    def size(self, b):
        rows = max(len(b["ins"]), 1)
        return W, HEAD + rows * ROW + (ROW if b["type"] in PARAM else 0) + 4

    def in_pos(self, b, i):
        return b["x"], b["y"] + HEAD + i * ROW + ROW / 2

    def out_pos(self, b):
        return b["x"] + W, b["y"] + HEAD + ROW / 2

    def hit(self, x, y):
        for uid in reversed(list(self.blocks)):
            b = self.blocks[uid]
            ox, oy = self.out_pos(b)
            if (x - ox) ** 2 + (y - oy) ** 2 <= (10 / self.zoom) ** 2:
                return uid, "out", 0
            for i in range(len(b["ins"])):
                ix, iy = self.in_pos(b, i)
                if (x - ix) ** 2 + (y - iy) ** 2 <= (10 / self.zoom) ** 2:
                    return uid, "in", i
            w, h = self.size(b)
            if b["x"] <= x <= b["x"] + w and b["y"] <= y <= b["y"] + h:
                return uid, "body", 0
        return None, None, 0

    # ── Menggambar ────────────────────────────────────────────────────────
    def val(self, uid):
        return self.q.get(uid) if (self.sim or self.live_on) else None

    def redraw(self):
        c = self.canvas
        c.delete("all")
        for gx in range(0, 4001, 100):
            c.create_line(gx, 0, gx, 3000, fill="#eef2f7")
        for gy in range(0, 3001, 100):
            c.create_line(0, gy, 4000, gy, fill="#eef2f7")
        for uid, b in self.blocks.items():
            for i, r in enumerate(b["ins"]):
                if r and "src" in r and r["src"] in self.blocks:
                    self.draw_wire(r, b, i)
        for uid, b in self.blocks.items():
            self.draw_block(uid, b)
        if self.drag and self.drag[0] == "wire":
            s = self.blocks.get(self.drag[1])
            if s:
                ox, oy = self.out_pos(s)
                c.create_line(ox, oy, self.drag[2], self.drag[3], fill="#f59e0b", width=2, dash=(4, 3))
        if self.zoom != 1.0:
            c.scale("all", 0, 0, self.zoom, self.zoom)

    def draw_wire(self, r, b, i):
        s = self.blocks[r["src"]]
        x1, y1 = self.out_pos(s)
        x2, y2 = self.in_pos(b, i)
        v = self.val(r["src"])
        col = "#16a34a" if v else ("#94a3b8" if v is False else "#334155")
        if x2 > x1 + 30:
            mx = (x1 + x2) / 2
            pts = [x1, y1, mx, y1, mx, y2, x2, y2]
        else:                                   # kabel balik / umpan balik
            ym = (y1 + y2) / 2 + 30
            pts = [x1, y1, x1 + 14, y1, x1 + 14, ym, x2 - 14, ym, x2 - 14, y2, x2, y2]
        self.canvas.create_line(*pts, fill=col, width=2, dash=(5, 3) if r.get("prev") else ())

    def draw_block(self, uid, b):
        c, t = self.canvas, b["type"]
        w, h = self.size(b)
        x, y = b["x"], b["y"]
        sel = uid == self.sel
        edge = "#f59e0b" if sel else "#475569"
        c.create_rectangle(x, y, x + w, y + h, fill="#ffffff", outline=edge, width=3 if sel else 1.5)
        c.create_rectangle(x, y, x + w, y + HEAD, fill=HEAD_COL[t], outline=edge, width=1.5)
        c.create_text(x + w / 2, y + HEAD / 2, text="INPUT (ROI)" if t == "IN" else t, font=FONT_B)
        c.create_text(x, y - 3, anchor="sw", font=FONT_B, fill="#0f172a",
                      text=("$" if t == "IN" else "") + b["name"])
        labels = PORT_LABELS.get(t, [])
        for i, r in enumerate(b["ins"]):
            px, py = self.in_pos(b, i)
            c.create_oval(px - 3, py - 3, px + 3, py + 3, fill="#334155", outline="")
            lx = px + 6
            if r and r.get("neg"):
                c.create_oval(px + 4, py - 4, px + 12, py + 4, fill="white", outline="#dc2626", width=2)
                lx = px + 16
            if i < len(labels):
                c.create_text(lx, py, text=labels[i], anchor="w", font=FONT)
            if r and r.get("prev"):
                c.create_text(px - 7, py - 9, text="@", fill="#ea580c", font=FONT_B)
            if r and "const" in r:
                c.create_text(px - 8, py, text=r["const"], anchor="e", fill="#7c3aed", font=self.cf_b)
        ox, oy = self.out_pos(b)
        v = self.val(uid)
        c.create_oval(ox - 4, oy - 4, ox + 4, oy + 4, outline="",
                      fill="#16a34a" if v else ("#94a3b8" if v is False else "#334155"))
        if t in ("NAND", "NOR"):
            c.create_oval(ox - 13, oy - 4, ox - 5, oy + 4, fill="white", outline="#dc2626", width=2)
        if t != "IN":
            c.create_text(ox - (18 if t in ("NAND", "NOR") else 8), oy, text="Q", anchor="e", font=self.cf)
        else:
            on = bool(self.q.get(uid)) if self.live_on else b["name"] in self.inputs_on
            show = self.sim or self.live_on
            txt = ("● ON" if on else "○ OFF") if show else "bit ROI"
            col = ("#16a34a" if on else "#64748b") if show else "#64748b"
            c.create_text(x + w / 2, y + HEAD + ROW / 2, text=txt, fill=col, font=FONT_B)
        if t in PARAM:
            key, lab, unit, _ = PARAM[t]
            txt = f"{lab} = {format(float(b['p']), 'g')}{(' ' + unit) if unit else ''}"
            inf = self.info.get(uid) if self.live_on else None
            if inf is not None and t == "TON":
                txt += f"   ET = {inf:.2f}"
            elif inf is not None and t == "CTU":
                txt += f"   CV = {int(inf)}"
            c.create_text(x + w / 2, y + h - ROW / 2 - 2, font=self.cf, text=txt)

    # ── Mouse ─────────────────────────────────────────────────────────────
    def _xy(self, e):
        return self.canvas.canvasx(e.x) / self.zoom, self.canvas.canvasy(e.y) / self.zoom

    def on_down(self, e):
        self.canvas.focus_set()
        x, y = self._xy(e)
        uid, part, i = self.hit(x, y)
        self.drag = None
        if part == "out":
            self.drag, self.sel = ("wire", uid, x, y), uid
        elif part == "in":
            r = self.blocks[uid]["ins"][i]
            if r and "src" in r:                # angkat kabel
                self.blocks[uid]["ins"][i] = None
                self.changed()
                self.drag = ("wire", r["src"], x, y)
        elif part == "body":
            self.sel = uid
            b = self.blocks[uid]
            self.blocks[uid] = self.blocks.pop(uid)     # naik ke atas
            self.drag = ("move", uid, x - b["x"], y - b["y"], False)
        else:
            self.sel = None
        self.redraw()

    def on_move(self, e):
        if not self.drag:
            return
        x, y = self._xy(e)
        if self.drag[0] == "move":
            _, uid, dx, dy, _m = self.drag
            b = self.blocks[uid]
            nx, ny = round((x - dx) / 10) * 10, round((y - dy) / 10) * 10
            if (nx, ny) != (b["x"], b["y"]):
                b["x"], b["y"] = max(0, nx), max(20, ny)
                self.drag = ("move", uid, dx, dy, True)
        else:
            self.drag = ("wire", self.drag[1], x, y)
        self.redraw()

    def on_up(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        x, y = self._xy(e)
        if d[0] == "wire":
            uid, part, i = self.hit(x, y)
            if part == "in":
                self.blocks[uid]["ins"][i] = {"src": d[1], "neg": False, "prev": False}
                self.changed()
        elif d[0] == "move" and not d[4] and self.sim and self.blocks[d[1]]["type"] == "IN":
            nm = self.blocks[d[1]]["name"]
            self.inputs_on.symmetric_difference_update({nm})
        self.redraw()

    def on_double(self, e):
        x, y = self._xy(e)
        uid, part, _ = self.hit(x, y)
        if part == "body" and not (self.sim and self.blocks[uid]["type"] == "IN"):
            self.edit_block(uid)

    def on_right(self, e):
        x, y = self._xy(e)
        uid, part, i = self.hit(x, y)
        m = tk.Menu(self, tearoff=0)
        if part == "in":
            m.add_command(label="Negasi  (!)", command=lambda: self.flip(uid, i, "neg"))
            m.add_command(label="Nilai scan lalu  (@)", command=lambda: self.flip(uid, i, "prev"))
            m.add_separator()
            m.add_command(label="Konstanta TRUE", command=lambda: self.setref(uid, i, {"const": "TRUE"}))
            m.add_command(label="Konstanta FALSE", command=lambda: self.setref(uid, i, {"const": "FALSE"}))
            m.add_command(label="Putuskan", command=lambda: self.setref(uid, i, None))
        elif part in ("body", "out"):
            self.sel = uid
            b = self.blocks[uid]
            m.add_command(label="Ubah nama / parameter…", command=lambda: self.edit_block(uid))
            if b["type"] in MULTI:
                m.add_command(label="Tambah input", command=lambda: self.add_input(uid))
                if len(b["ins"]) > 1:
                    m.add_command(label="Kurangi input", command=lambda: self.del_input(uid))
            m.add_separator()
            m.add_command(label="Hapus blok", command=lambda: self.delete_block(uid))
        else:
            sub = tk.Menu(m, tearoff=0)
            for _, types in PALETTE:
                for t in types:
                    sub.add_command(label=t, command=lambda t=t: self.add_block(
                        t, round(x / 10) * 10, round(y / 10) * 10))
            m.add_cascade(label="Tambah blok", menu=sub)
            if self.roi_names:
                rs = tk.Menu(m, tearoff=0)
                used = {b["name"] for b in self.blocks.values() if b["type"] == "IN"}
                for r in self.roi_names:
                    rs.add_command(label=r, state="disabled" if r in used else "normal",
                                   command=lambda r=r: self.add_block(
                                       "IN", round(x / 10) * 10, round(y / 10) * 10, name=r))
                m.add_cascade(label="Input ROI", menu=rs)
        self.redraw()
        m.tk_popup(e.x_root, e.y_root)

    def flip(self, uid, i, key):
        r = self.blocks[uid]["ins"][i]
        if not r:
            return
        if "const" in r:
            if key == "neg":
                r["const"] = "FALSE" if r["const"] == "TRUE" else "TRUE"
        else:
            r[key] = not r[key]
        self.changed()
        self.redraw()

    def setref(self, uid, i, ref):
        self.blocks[uid]["ins"][i] = ref
        self.changed()
        self.redraw()

    def add_input(self, uid):
        self.blocks[uid]["ins"].append(None)
        self.changed()
        self.redraw()

    def del_input(self, uid):
        self.blocks[uid]["ins"].pop()
        self.changed()
        self.redraw()

    def edit_block(self, uid):
        b = self.blocks[uid]
        s = self.ask_name("Input ROI (pilih / ketik):" if b["type"] == "IN"
                          else "Nama node (mis. done_s1, run, start):",
                          b["name"], self.roi_names if b["type"] == "IN" else None)
        if s is None:
            return
        s = s.strip()
        err = self.check_name(uid, b["type"], s)
        if err:
            messagebox.showerror("Nama tidak valid", err)
            return
        b["name"] = s
        if b["type"] in PARAM:
            _, lab, unit, _ = PARAM[b["type"]]
            v = simpledialog.askfloat(lab, f"{lab} ({unit or 'hitungan'}):", initialvalue=b["p"],
                                      parent=self, minvalue=0)
            if v is not None:
                b["p"] = v
        self.changed()
        self.redraw()

    # ── Konversi graf <-> Node ────────────────────────────────────────────
    def sorted_uids(self):
        seen, out = set(), []

        def visit(u):
            if u in seen:
                return
            seen.add(u)
            for r in self.blocks[u]["ins"]:
                if r and r.get("src") in self.blocks and not r.get("prev"):
                    visit(r["src"])
            out.append(u)

        for u in sorted(self.blocks, key=lambda k: (self.blocks[k]["type"] != "IN",
                                                    self.blocks[k]["x"], self.blocks[k]["y"])):
            visit(u)
        return out

    def ref_str(self, r):
        if not r:
            return None
        if "const" in r:
            return r["const"]
        s = self.node_id(r["src"]) + ("@" if r.get("prev") else "")
        return ("!" + s) if r.get("neg") else s

    def to_nodes(self):
        ids = [self.node_id(u) for u in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("ada nama node yang dobel")
        warns, nodes = [], []
        for uid in self.sorted_uids():
            b, t, nid = self.blocks[uid], self.blocks[uid]["type"], self.node_id(uid)
            if t == "IN":
                nodes.append(Node(nid, "IN", params={"name": b["name"]}))
                continue
            refs = [self.ref_str(r) for r in b["ins"]]
            if t in MULTI:
                refs = [r for r in refs if r is not None] or ["FALSE"]
                if len(refs) < len(b["ins"]):
                    warns.append(f"{b['name']}: input kosong diabaikan")
            else:
                if any(r is None for r in refs):
                    warns.append(f"{b['name']}: input belum tersambung (dianggap FALSE)")
                refs = [r or "FALSE" for r in refs]
            params = {}
            if t in ("TON", "TOF", "TP"):
                params["pt"] = float(b["p"])
            elif t == "CTU":
                params["pv"] = float(b["p"])
            nodes.append(Node(nid, t, refs, params) if params else Node(nid, t, refs))
        self.warns = warns
        return nodes

    def load_nodes(self, nodes, layout=None):
        self.blocks, self.n, self.sel = {}, 0, None
        layout = layout or {}
        uid_of = {}
        seen_in = {}
        for nd in nodes:
            if nd.type == "IN":
                nm = nd.params.get("name", nd.id)
                if nm in seen_in:
                    uid_of[nd.id] = uid_of[seen_in[nm]]
                    continue
                seen_in[nm] = nd.id
            self.n += 1
            uid_of[nd.id] = f"b{self.n}"
        for nd in nodes:
            t = nd.type
            if t == "NOT":
                bt, nin = "AND", 1
            elif t in FIXED_IN or t in MULTI:
                bt, nin = t, (len(nd.inputs) if t in MULTI else FIXED_IN[t])
            else:
                raise ValueError(f"tipe node tidak didukung: {t}")
            name = nd.params.get("name", nd.id) if bt == "IN" else nd.id
            p = nd.params.get("pt", nd.params.get("pv")) if bt in PARAM else None
            self.blocks[uid_of[nd.id]] = {"type": bt, "name": name, "x": None, "y": None,
                                          "ins": [None] * max(nin, 0), "p": p if p is not None
                                          else (PARAM[bt][3] if bt in PARAM else None)}
        order = {nd.id: k for k, nd in enumerate(nodes)}
        id_of = {u: nid for nid, u in uid_of.items()}
        for nd in nodes:
            b = self.blocks[uid_of[nd.id]]
            for i, s in enumerate(nd.inputs if b["type"] != "IN" else []):
                r = self.parse_ref(s, uid_of)
                if r and "src" in r and order[id_of[r["src"]]] >= order[nd.id]:
                    r["prev"] = True    # rujukan ke bawah / diri sendiri = scan lalu
                if nd.type == "NOT" and r:
                    if "const" in r:
                        r["const"] = "FALSE" if r["const"] == "TRUE" else "TRUE"
                    else:
                        r["neg"] = not r["neg"]
                b["ins"][i] = r
        # tata letak otomatis untuk blok tanpa posisi tersimpan
        level = {}

        def lv(u, stack=()):
            if u in level:
                return level[u]
            if u in stack:
                return 0
            ins = [r["src"] for r in self.blocks[u]["ins"] if r and "src" in r and not r.get("prev")]
            level[u] = 0 if not ins else 1 + max(lv(s, stack + (u,)) for s in ins)
            return level[u]

        ycol = {}
        for nd in nodes:
            u = uid_of[nd.id]
            b = self.blocks[u]
            if nd.id in layout:
                b["x"], b["y"] = layout[nd.id]
            else:
                k = lv(u)
                b["x"], b["y"] = 40 + k * 240, 50 + ycol.get(k, 0)
                ycol[k] = ycol.get(k, 0) + self.size(b)[1] + 40
        self.changed()
        self.redraw()
        self.after(60, self.fit_view)
    @staticmethod
    def parse_ref(s, uid_of):
        neg, prev = False, False
        while s.startswith("!"):
            neg, s = not neg, s[1:]
        if s.endswith("@"):
            prev, s = True, s[:-1]
        if s in CONSTS:
            return {"const": "TRUE" if (bool(CONSTS[s]) ^ neg) else "FALSE"}
        if s not in uid_of:
            return None
        return {"src": uid_of[s], "neg": neg, "prev": prev}

    # ── Berkas ────────────────────────────────────────────────────────────
    def open_cfg(self, path=None):
        path = path or filedialog.askopenfilename(filetypes=[("Konfigurasi JSON", "*.json")])
        if not path:
            return
        try:
            cfg = L.load_config(path)
            converted = cfg.get("mode", "template") == "template"
            if converted:
                cfg = L.template_to_program_config(cfg)
            nodes = L.compile_program(cfg["program"])
            self.load_nodes(nodes, cfg.get("layout"))
        except Exception as e:
            messagebox.showerror("Gagal membuka", str(e))
            return
        self.cfg = cfg
        self.path = None if converted else path
        self.status.set(("Config template dikonversi ke program — Simpan akan meminta nama berkas baru. "
                         if converted else "Dibuka: ") + path)

    def save_cfg(self, as_new=False):
        try:
            nodes = self.to_nodes()
            Engine(nodes)                       # validasi referensi
            text = L.program_to_text(nodes)
            L.compile_program(text)             # pastikan teks bisa dikompilasi balik
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e))
            return
        cfg = self.cfg or L.make_config([], program=text)
        cfg["mode"], cfg["program"] = "program", text
        cfg.pop("spec", None)

        # Pertahankan mapping memory yang sudah ada.
        cfg.setdefault("memory", {
            "aliases": {},
            "values": {}
        })
        self.memory = cfg["memory"]
        cfg.setdefault("cycle", {})
        cfg.setdefault("messages", {})
        cfg["layout"] = {self.node_id(u): [b["x"], b["y"]] for u, b in self.blocks.items()}
        path = self.path
        if as_new or not path:
            path = filedialog.asksaveasfilename(defaultextension=".json",
                                                filetypes=[("Konfigurasi JSON", "*.json")])
            if not path:
                return
        L.save_config(path, cfg)
        self.cfg, self.path = cfg, path
        self.status.set("Tersimpan: " + path + ("   ⚠ " + "; ".join(self.warns) if self.warns else ""))
    def load_text(self, text):
        nodes = L.compile_program(text)
        layout = {}
        for ln in text.splitlines():
            p = ln.split()
            if len(p) == 4 and p[0] == self.POS_TAG:
                try:
                    layout[p[1]] = [int(p[2]), int(p[3])]
                except ValueError:
                    pass
        self.load_nodes(nodes, layout)

    def to_text(self):
        if not self.blocks:
            return ""
        nodes = self.to_nodes()
        Engine(nodes)
        text = L.program_to_text(nodes)
        L.compile_program(text)             # pastikan bisa dikompilasi balik
        pos = "".join(f"{self.POS_TAG} {self.node_id(u)} {int(b['x'])} {int(b['y'])}\n"
                      for u, b in self.blocks.items())
        return text + pos

    def apply_and_close(self):
        try:
            text = self.to_text()
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e), parent=self)
            return
        if self.roi_names:
            used = {b["name"] for b in self.blocks.values() if b["type"] == "IN"}
            unknown = sorted(used - set(self.roi_names) - {"__abort"})
            if unknown and not messagebox.askyesno(
                    "Peringatan", "Input tanpa ROI: " + ", ".join(unknown) + "\n\nTetap terapkan?",
                    parent=self):
                return
        self.on_apply(text)
        self.close()

    def ask_name(self, prompt, initial, choices=None):
        if not choices:
            return simpledialog.askstring("Nama", prompt, initialvalue=initial, parent=self)
        from tkinter import ttk
        res = {"v": None}
        top = tk.Toplevel(self)
        top.title("Input ROI")
        top.transient(self)
        top.grab_set()
        tk.Label(top, text=prompt).pack(padx=10, pady=(10, 2))
        var = tk.StringVar(value=initial)
        cb = ttk.Combobox(top, textvariable=var, values=choices, width=28)
        cb.pack(padx=10)

        def ok(_=None):
            res["v"] = var.get()
            top.destroy()

        tk.Button(top, text="OK", width=8, command=ok).pack(pady=10)
        top.bind("<Return>", ok)
        top.bind("<Escape>", lambda e: top.destroy())
        cb.focus_set()
        top.wait_window()
        return res["v"]
    def _set_zoom(self, z):
        self.zoom = z
        size = max(1, round(9 * z))
        self.cf.configure(size=size)
        self.cf_b.configure(size=size)
        self.canvas.configure(scrollregion=(0, 0, 4000 * z, 3000 * z))
        self.zoom_var.set(f"{z * 100:.0f}%")
        self.redraw()

    def zoom_at(self, f, px=None, py=None):
        c = self.canvas
        if px is None:
            px, py = c.winfo_width() / 2, c.winfo_height() / 2
        z0 = self.zoom
        z1 = max(0.25, min(3.0, z0 * f))
        if abs(z1 - z0) < 1e-6:
            return
        mx, my = c.canvasx(px) / z0, c.canvasy(py) / z0     # titik di bawah kursor
        self._set_zoom(z1)
        c.xview_moveto(max(0, (mx * z1 - px) / (4000 * z1)))
        c.yview_moveto(max(0, (my * z1 - py) / (3000 * z1)))

    def reset_view(self):
        self._set_zoom(1.0)
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def fit_view(self):
        if not self.blocks:
            self.reset_view()
            return
        self.update_idletasks()
        c = self.canvas
        cw, ch = max(c.winfo_width(), 400), max(c.winfo_height(), 300)
        bl = list(self.blocks.values())
        x1 = min(b["x"] for b in bl) - 30
        y1 = min(b["y"] for b in bl) - 40
        x2 = max(b["x"] + W for b in bl) + 30
        y2 = max(b["y"] + self.size(b)[1] for b in bl) + 30
        z = max(0.25, min(1.0, cw / (x2 - x1), ch / (y2 - y1)))
        self._set_zoom(z)
        c.xview_moveto(max(0, x1 * z) / (4000 * z))
        c.yview_moveto(max(0, y1 * z) / (3000 * z))

    def on_wheel(self, e):
        up = e.num == 4 or e.delta > 0
        if e.state & 0x4:                       # Ctrl = zoom ke arah kursor
            self.zoom_at(1.15 if up else 1 / 1.15, e.x, e.y)
        elif e.state & 0x1:                     # Shift = geser horizontal
            self.canvas.xview_scroll(-3 if up else 3, "units")
        else:
            self.canvas.yview_scroll(-3 if up else 3, "units")
    def toggle_live(self):
        if self.live_on:
            self.stop_live()
            return
        snap = self.live()
        if not snap:
            messagebox.showinfo("Monitor", "main.py belum menghasilkan data.\n"
                                "Pipeline belum berjalan atau belum ada scan.", parent=self)
            return
        mine = {self.node_id(u) for u in self.blocks}
        run = set(snap["dbg"])
        if mine != run:
            msg = "Diagram berbeda dengan program yang sedang berjalan di main.py."
            a, b = sorted(mine - run)[:8], sorted(run - mine)[:8]
            if a:
                msg += "\n\nHanya di diagram: " + ", ".join(a)
            if b:
                msg += "\n\nHanya di program berjalan: " + ", ".join(b)
            if not messagebox.askyesno("Monitor", msg + "\n\nTetap monitor? "
                                       "(hanya node bernama sama yang tampil)", parent=self):
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
        self.q, self.info, self._live_key = {}, {}, None
        self.live_var.set("")
        self.btn_live.config(text="● Monitor", bg=self.btn_sim.cget("bg"))
        self.redraw()

    def pull_live(self):
        snap = self.live()
        if not snap or not snap.get("program"):
            messagebox.showinfo("Ambil program", "Belum ada data dari main.py.", parent=self)
            return
        if self.blocks and not messagebox.askyesno(
                "Ambil program", "Ganti diagram dengan program yang sedang berjalan di main.py?",
                parent=self):
            return
        self.load_text(snap["program"])

    def live_tick(self):
        if not self.live_on:
            return
        snap = self.live()
        if snap:
            n2u = {self.node_id(u): u for u in self.blocks}
            dbg = snap["dbg"]
            q = {n2u[k]: bool(v[0]) for k, v in dbg.items() if k in n2u}
            info = {n2u[k]: v[1] for k, v in dbg.items() if k in n2u and v[1] is not None}
            stale = time.time() - snap.get("wall", 0) > 2.0
            ct = snap.get("cycle_time") if snap.get("cycle_time") is not None else snap.get("last_cycle")
            label = f"{snap['state']}" + (f"  siklus {ct:.2f}s" if ct is not None else "") \
                    + f"  #{snap['cycles']}  {snap['msg']}"
            self.live_var.set(("⚠ TIDAK ADA UPDATE  " if stale else "") + label)
            key = (tuple(sorted(q.items())), tuple((k, round(v, 1)) for k, v in sorted(info.items())), stale)
            if key != self._live_key:
                self.q, self.info, self._live_key = q, info, key
                self.redraw()
        else:
            self.live_var.set("menunggu data…")
        self._live_job = self.after(100, self.live_tick)
    def show_help(self):
        from tkinter import ttk
        if getattr(self, "_help", None) and self._help.winfo_exists():
            self._help.lift()
            return
        w = self._help = tk.Toplevel(self)
        w.title("Bantuan")
        w.geometry("660x540")
        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True)
        for title, body in HELP_PAGES:
            f = tk.Frame(nb)
            nb.add(f, text=title)
            tx = tk.Text(f, wrap="word", font=("TkDefaultFont", 10), padx=10, pady=8)
            tx.insert("1.0", body.strip())
            tx.config(state="disabled")
            tx.pack(fill="both", expand=True)

    def show_text(self):
        try:
            text = L.program_to_text(self.to_nodes())
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e))
            return
        w = tk.Toplevel(self)
        w.title("Program teks (hasil dari diagram, hanya baca)")
        tx = tk.Text(w, width=80, height=30, font=("Courier", 10))
        tx.insert("1.0", text)
        tx.config(state="disabled")
        tx.pack(fill="both", expand=True)

    # ── Simulasi ──────────────────────────────────────────────────────────
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
        self.tick()

    def stop_sim(self):
        self.sim, self.engine, self.q = False, None, {}
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.btn_sim.config(text="▶ Simulasi")
        self.redraw()

    def start_engine(self):
        try:
            self.engine = Engine(self.to_nodes())
        except Exception as e:
            messagebox.showerror("Program tidak valid", str(e))
            self.engine = None
            return False
        self.t = 0.0
        self.nid2uid = {self.node_id(u): u for u in self.blocks}
        return True

    def tick(self):
        if not self.sim:
            return
        if self.engine is None and not self.start_engine():
            self.stop_sim()
            return
        ext = {b["name"]: (b["name"] in self.inputs_on) for b in self.blocks.values() if b["type"] == "IN"}
        try:
            q = self.engine.scan(ext, round(self.t, 6))
        except Exception as e:
            messagebox.showerror("Galat simulasi", str(e))
            self.stop_sim()
            return
        self.t += 0.1
        self.q = {self.nid2uid[k]: bool(v) for k, v in q.items() if k in self.nid2uid}
        self.redraw()
        self._job = self.after(100, self.tick)

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    ed = Editor(root, path=sys.argv[1] if len(sys.argv) > 1 else None)
    ed.protocol("WM_DELETE_WINDOW", lambda: (ed.stop_sim(), root.destroy()))
    root.mainloop()