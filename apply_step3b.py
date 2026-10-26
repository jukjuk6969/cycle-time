# apply_step3b.py
"""
Menambal logic_editor.py: SequenceTester lama -> Pipeline (via logic_bridge.py),
field ROI baru (mode/min_overlap/priority/group/dwell/grace), XML versi 3,
dialog "Logika / Program", dan update deteksi memakai waktu frame.

Pakai:  python apply_step3b.py [logic_editor.py]
Aman:   semua edit dikerjakan di memori; bila satu anchor tidak cocok, file TIDAK diubah.
        File asli dicadangkan ke logic_editor.py.bak_step3b.
"""
import py_compile
import shutil
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "logic_editor.py")
src = path.read_text(encoding="utf-8")
text = src
errors = []


def edit(old, new, label):
    """Ganti teks persis; harus muncul tepat 1 kali."""
    global text
    n = text.count(old)
    if n != 1:
        errors.append(f"{label}: anchor ditemukan {n}x (harus 1x)")
        return
    text = text.replace(old, new, 1)


def find_line(prefix, label):
    """Index awal & akhir (setelah \\n) dari satu-satunya baris yang diawali prefix persis."""
    lines = text.split("\n")
    hits = [i for i, l in enumerate(lines) if l.startswith(prefix)]
    if len(hits) != 1:
        errors.append(f"{label}: baris '{prefix.strip()[:40]}' ditemukan {len(hits)}x (harus 1x)")
        return None
    start = sum(len(l) + 1 for l in lines[:hits[0]])
    return start, start + len(lines[hits[0]]) + 1


def insert_after_line(prefix, add, label):
    global text
    r = find_line(prefix, label)
    if r:
        text = text[:r[1]] + add + text[r[1]:]


def insert_before_line(prefix, add, label):
    global text
    r = find_line(prefix, label)
    if r:
        text = text[:r[0]] + add + text[r[0]:]


def replace_line(prefix, new, label):
    global text
    r = find_line(prefix, label)
    if r:
        text = text[:r[0]] + new + text[r[1]:]


# ── 1. import + versi XML ─────────────────────────────────────────────────
edit('XML_VERSION = "2"',
     'from logic_bridge import SequenceTester, check_config, template_text\n\nXML_VERSION = "3"',
     "import/versi")

# ── 2. model data ─────────────────────────────────────────────────────────
insert_after_line('    description: str = ""', '''    mode: str = "center"                        # center | fraction | iou
    min_overlap: float = 0.4                    # untuk mode fraction / iou
    priority: int = 0                           # menang bila satu deteksi kena banyak ROI dalam 1 grup
    group: str = ""                             # "" = tidak eksklusif
    dwell: float = 0.0                          # detik: harus bertahan di ROI sebelum dihitung
    grace: float = 0.0                          # detik: tetap dianggap di ROI setelah deteksi hilang
''', "RoiDef")

insert_after_line('    mirror: bool = False', '''    skip_mode: str = "immediate"                # immediate | final
    restart_on_input: bool = False
    single: bool = False                        # satu deteksi hanya boleh kena satu ROI
    max_det: int = 0                            # 0 = tanpa batas; K = K deteksi ber-conf tertinggi
    program: str = ""                           # kosong = template; isi = program PLC teks
''', "LogicConfig")

edit('    return errors, warnings\n',
     '    e2, w2 = check_config(cfg)\n    errors += e2\n    warnings += w2\n    return errors, warnings\n',
     "validate")

# ── 3. XML tulis / baca ───────────────────────────────────────────────────
insert_after_line('    el.set("conf", f"{r.conf:.3f}")', '''    el.set("mode", r.mode)
    el.set("min_overlap", f"{r.min_overlap:.3f}")
    el.set("priority", str(int(r.priority)))
    el.set("group", r.group)
    el.set("dwell", f"{r.dwell:g}")
    el.set("grace", f"{r.grace:g}")
''', "_write_geom")

insert_after_line('    root.set("mirror", "true" if cfg.mirror else "false")', '''    root.set("skip_mode", cfg.skip_mode)
    root.set("restart_on_input", "true" if cfg.restart_on_input else "false")
    root.set("single", "true" if cfg.single else "false")
    root.set("max_det", str(int(cfg.max_det)))
''', "save root attrs")

insert_before_line('    tree = ET.ElementTree(root)', '''    if cfg.program.strip():
        ET.SubElement(root, "program").text = cfg.program.strip("\\n")
''', "save program")

edit('        description=el.get("description", ""),\n    )',
     '''        description=el.get("description", ""),
        mode=el.get("mode", "center") or "center",
        min_overlap=float(el.get("min_overlap", "0.4") or 0.4),
        priority=int(float(el.get("priority", "0") or 0)),
        group=el.get("group", ""),
        dwell=float(el.get("dwell", "0") or 0),
        grace=float(el.get("grace", "0") or 0),
    )''', "_read_roi")

edit('        mirror=root.get("mirror", "false") == "true",\n    )\n',
     '''        mirror=root.get("mirror", "false") == "true",
    )
    cfg.skip_mode = root.get("skip_mode", "immediate") or "immediate"
    cfg.restart_on_input = root.get("restart_on_input", "false") == "true"
    cfg.single = root.get("single", "false") == "true"
    cfg.max_det = int(root.get("max_det", "0") or 0)
    prog = root.find("program")
    cfg.program = (prog.text or "") if prog is not None else ""
''', "load cfg")

# ── 4. Dialog properti ROI: bagian lanjutan ───────────────────────────────
insert_before_line('    err = ttk.Label(body, text="", foreground="#c62828")', '''    adv = {
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

''', "dialog lanjutan UI")

edit('''        desc = desc_var.get().strip()
        if is_sequence and not desc:
            err.config(text="Keterangan wajib diisi.")
            return
''', '''        desc = desc_var.get().strip()
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
''', "dialog lanjutan parse")

edit('            "description": desc if is_sequence else "",\n        }',
     '''            "description": desc if is_sequence else "",
            "mode": adv["mode"].get(),
            "min_overlap": mo,
            "priority": pr,
            "group": adv["group"].get().strip().replace(" ", "_"),
            "dwell": dw,
            "grace": gr,
        }''', "dialog hasil")

# ── 5. SequenceTester lama dibuang (pindah ke logic_bridge.py) ─────────────
a = text.find("class SequenceTester:")
b = text.find("#  Panel editor")
if a < 0 or b < 0:
    errors.append("SequenceTester/Panel editor: marker tidak ditemukan")
else:
    b = text.rfind("\n# ═", 0, b) + 1
    if b <= a:
        errors.append("SequenceTester: urutan marker salah")
    else:
        text = (text[:a] + "# SequenceTester kini ada di logic_bridge.py (di-import di atas;\n"
                "# memakai plc_engine/plc_logic).\n\n\n" + text[b:])

# ── 6. Panel: tombol, helper, dialog logika & program ─────────────────────
insert_before_line('        self._hint_var = tk.StringVar(value="")', '''        ttk.Button(self, text="⚙ Logika / Program…",
                   command=self._open_logic_settings).pack(fill="x", pady=(0, 6), ipady=2)

''', "panel tombol")

edit('        self.cfg.mirror = bool(self.get_mirror())\n        self._sel_id = roi.id\n',
     '        self._apply_adv(roi, res)\n        self.cfg.mirror = bool(self.get_mirror())\n        self._sel_id = roi.id\n',
     "_finish_draw")
insert_after_line('        r.classes, r.conf = res["classes"], res["conf"]',
                  '        self._apply_adv(r, res)\n', "_edit_selected")

PANEL_METHODS = '''    def _apply_adv(self, roi, res):
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
        help_txt = ("nama = ekspresi   (satu baris satu node, urutan baris = urutan evaluasi)\\n"
                    "$roi = bit ROI | nama@ = nilai scan lalu | & | ^ ! atau AND OR XOR NOT, ( )\\n"
                    "TON/TOF/TP(sinyal, detik)  R_TRIG/F_TRIG(sinyal)  SR/RS(set, reset)  "
                    "CTU(naik, reset, n)  NAND/NOR(...)\\n"
                    "Konvensi: done_<roi> / skip_<roi> tampil di tabel; start, run, done_<output> = waktu siklus.\\n"
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
            return txt.get("1.0", "end").strip("\\n")

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
                msg.config(text="\\n".join(errs), foreground="#c62828")
                return False
            msg.config(text="OK" + ("".join("\\n• " + w for w in warns)), foreground="#2e7d32")
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

        btns = ttk.Frame(top)
        btns.pack(fill="x", padx=10, pady=10)
        ttk.Button(btns, text="Isi dari template", command=_from_template).pack(side="left")
        ttk.Button(btns, text="Validasi", command=_check).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Kosongkan (pakai template)", command=_clear).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Simpan", command=_save).pack(side="right")
        ttk.Button(btns, text="Batal", command=top.destroy).pack(side="right", padx=(0, 6))
        top.wait_window()

'''
edit("    def save(self) -> bool:\n", PANEL_METHODS + "    def save(self) -> bool:\n", "panel metode")

# ── 7. Uji mandiri: update memakai hasil inferensi baru + waktu frame ─────
edit('    det = {"model": None, "busy": False, "result": [], "key": None, "frame_no": 0, "t_last": 0.0}',
     '    det = {"model": None, "busy": False, "result": [], "key": None, "frame_no": 0, "t_last": 0.0,\n'
     '           "seq": 0, "t": 0.0, "seen": -1}', "det state")
edit("    def _infer(frame_bgr, conf, key):",
     "    def _infer(frame_bgr, conf, key, t_frame=0.0):", "_infer sig")
edit('            det["result"] = out\n            det["key"] = key\n',
     '            det["result"] = out\n            det["key"] = key\n            det["t"] = t_frame\n            det["seq"] += 1\n',
     "_infer hasil")
edit("        threading.Thread(target=_infer, args=(frame_bgr.copy(), float(det_conf.get()), key),",
     "        tfr = tp.position() / tp.fps() if tp is not None else time.perf_counter()\n"
     "        threading.Thread(target=_infer, args=(frame_bgr.copy(), float(det_conf.get()), key, tfr),",
     "maybe_detect")
edit('        rois = panel.cfg.all_rois()\n        for (x1, y1, x2, y2, name, cf) in det["result"]:\n',
     '        rois = panel.cfg.all_rois()\n        hit_map = {d: n for d, n in tester.assign}\n'
     '        for (x1, y1, x2, y2, name, cf) in det["result"]:\n', "draw_detections")
insert_before_line('            color = (0, 255, 120) if hit else (255, 60, 60)', '''            nm = hit_map.get((x1, y1, x2, y2, name, cf))
            if nm is not None:                       # keputusan resmi dari logika (mode/grup/prioritas)
                hit = panel.cfg.find(nm[0]) if nm else None
''', "draw hit")
replace_line('            tester.sync(panel.cfg)',
             '            if tester.sync(panel.cfg):\n                det["seen"] = -1                 # ROI berubah: nilai ulang hasil terakhir\n',
             "tick sync")
replace_line('            tester.update(compute_hits(panel.cfg, live), now_t)', '''            if det_on.get() and det["model"] is not None:
                if det["seq"] != det["seen"]:        # hanya saat ada hasil inferensi baru
                    det["seen"] = det["seq"]
                    tester.update(live, det["t"])    # waktu = waktu frame yang dideteksi
            else:
                tester.update([], now_t)
''', "tick update")

# ── tulis ─────────────────────────────────────────────────────────────────
if errors:
    print("GAGAL — file TIDAK diubah:")
    for e in errors:
        print("  -", e)
    sys.exit(1)

bak = path.with_name(path.name + ".bak_step3b")
shutil.copyfile(path, bak)
path.write_text(text, encoding="utf-8")
try:
    py_compile.compile(str(path), doraise=True)
except py_compile.PyCompileError as e:
    shutil.copyfile(bak, path)
    print("GAGAL kompilasi, file dikembalikan dari cadangan:\n", e)
    sys.exit(1)
print(f"OK: {path} ditambal ({len(src.splitlines())} -> {len(text.splitlines())} baris). Cadangan: {bak.name}")
