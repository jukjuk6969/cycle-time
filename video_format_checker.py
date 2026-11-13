"""
video_format_checker.py
=======================
Cek format video (container, codec, resolusi, fps, durasi, bitrate, dll).
Pakai ffprobe. Ada file picker (banyak file / satu folder).

Jalankan:  python video_format_checker.py
"""
import csv
import json
import re
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

CREATE_NO_WINDOW = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0
VIDEO_EXT = ("*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm", "*.wmv", "*.m4v")
CT_RE = re.compile(r"_(\d+)_(\d)s")  # contoh: ..._12_5s_ANOMALY.mp4 -> 12.5

# codec -> (nama tampil, aman diputar di browser?)
CODEC_INFO = {
    "h264": ("H.264", True),
    "hevc": ("H.265", False),
    "vp9": ("VP9", True),
    "vp8": ("VP8", True),
    "av1": ("AV1", True),
    "mpeg4": ("MPEG-4", False),
    "mjpeg": ("MJPEG", False),
}


def find_ffprobe() -> str:
    here = Path(__file__).parent
    for p in (
        here / "ffprobe.exe",
        here / "bin" / "ffprobe.exe",
        Path(r"C:\ProgramData\chocolatey\bin\ffprobe.exe"),
    ):
        if p.exists():
            return str(p)
    return shutil.which("ffprobe") or shutil.which("ffprobe.exe") or ""


FFPROBE = find_ffprobe()


def probe(path: str) -> dict:
    """Return dict hasil ffprobe (raw) atau raise."""
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffprobe gagal")
    return json.loads(r.stdout)


def parse_fps(s: str) -> float:
    try:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    except Exception:
        return 0.0


def summarize(path: str, data: dict) -> dict:
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    dur = float(fmt.get("duration") or v.get("duration") or 0)
    size = int(fmt.get("size") or Path(path).stat().st_size)
    codec = v.get("codec_name", "?")
    name, web_ok = CODEC_INFO.get(codec, (codec, False))

    fps = parse_fps(v.get("avg_frame_rate", "0/0")) or parse_fps(v.get("r_frame_rate", "0/0"))
    nb = v.get("nb_frames")
    frames = int(nb) if nb and nb.isdigit() else (round(fps * dur) if fps and dur else 0)

    m = CT_RE.findall(Path(path).name)
    ct = float(f"{m[-1][0]}.{m[-1][1]}") if m else None
    diff = f"{dur - ct:+.2f}s" if ct is not None else "-"

    return {
        "file": Path(path).name,
        "path": path,
        "container": fmt.get("format_name", "?").split(",")[0],
        "codec": f"{name} ({codec})",
        "profile": v.get("profile", "-"),
        "pix_fmt": v.get("pix_fmt", "-"),
        "res": f"{v.get('width', '?')}x{v.get('height', '?')}",
        "fps": f"{fps:.2f}",
        "frames": frames,
        "dur": f"{dur:.2f}s",
        "ct_name": f"{ct:.1f}s" if ct is not None else "-",
        "diff": diff,
        "bitrate": f"{int(fmt.get('bit_rate') or 0) / 1000:.0f} kbps",
        "size": f"{size / 1024 / 1024:.2f} MB",
        "audio": a.get("codec_name", "ada") if a else "tidak ada",
        "browser": "OK" if web_ok else "Mungkin tidak bisa",
    }


COLUMNS = [
    ("file", "File", 260),
    ("container", "Container", 70),
    ("codec", "Codec", 110),
    ("res", "Resolusi", 80),
    ("fps", "FPS", 55),
    ("frames", "Frame", 60),
    ("dur", "Durasi", 70),
    ("ct_name", "CT (nama)", 70),
    ("diff", "Selisih", 65),
    ("bitrate", "Bitrate", 85),
    ("size", "Ukuran", 75),
    ("browser", "Browser", 110),
]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Video Format Checker")
        root.geometry("1250x600")
        self.raw = {}      # iid -> raw ffprobe json
        self.rows = {}     # iid -> summary dict

        bar = ttk.Frame(root, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Pilih File...", command=self.pick_files).pack(side="left")
        ttk.Button(bar, text="Pilih Folder...", command=self.pick_folder).pack(
            side="left", padx=6
        )
        ttk.Button(bar, text="Hapus Semua", command=self.clear).pack(side="left")
        ttk.Button(bar, text="Export CSV", command=self.export_csv).pack(side="right")
        self.status = tk.StringVar(
            value="ffprobe: " + (FFPROBE or "TIDAK DITEMUKAN (taruh ffprobe.exe di folder ini)")
        )

        paned = ttk.PanedWindow(root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8)

        top = ttk.Frame(paned)
        self.tree = ttk.Treeview(
            top, columns=[c[0] for c in COLUMNS], show="headings", selectmode="browse"
        )
        for key, title, w in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=w, anchor="w")
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        sx = ttk.Scrollbar(top, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=sx.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)
        paned.add(top, weight=3)

        bottom = ttk.LabelFrame(paned, text="Detail (ffprobe, file terpilih)")
        self.detail = tk.Text(bottom, height=10, font=("Consolas", 9), wrap="none")
        dsb = ttk.Scrollbar(bottom, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=dsb.set)
        self.detail.pack(side="left", fill="both", expand=True)
        dsb.pack(side="right", fill="y")
        paned.add(bottom, weight=1)

        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w").pack(
            fill="x", side="bottom"
        )
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.tag_configure("bad", background="#ffe0e0")
        self.tree.tag_configure("warn", background="#fff4d6")

        if not FFPROBE:
            messagebox.showwarning(
                "ffprobe", "ffprobe tidak ditemukan.\nTaruh ffprobe.exe di folder program ini."
            )

    # ── pilih file ────────────────────────────────────────────────────────
    def pick_files(self):
        paths = filedialog.askopenfilenames(
            title="Pilih video",
            filetypes=[("Video", " ".join(VIDEO_EXT)), ("Semua file", "*.*")],
        )
        if paths:
            self.analyze(list(paths))

    def pick_folder(self):
        folder = filedialog.askdirectory(title="Pilih folder video")
        if not folder:
            return
        paths = []
        for ext in VIDEO_EXT:
            paths += [str(p) for p in Path(folder).glob(ext)]
        if not paths:
            messagebox.showinfo("Folder", "Tidak ada file video di folder ini.")
            return
        self.analyze(sorted(paths))

    # ── analisis (thread supaya UI tidak macet) ───────────────────────────
    def analyze(self, paths):
        if not FFPROBE:
            messagebox.showerror("ffprobe", "ffprobe tidak ditemukan.")
            return
        self.status.set(f"Menganalisis {len(paths)} file...")
        threading.Thread(target=self._worker, args=(paths,), daemon=True).start()

    def _worker(self, paths):
        for i, p in enumerate(paths, 1):
            try:
                data = probe(p)
                row = summarize(p, data)
                self.root.after(0, self._add_row, row, data)
            except Exception as e:
                self.root.after(0, self._add_error, p, str(e))
            self.root.after(0, self.status.set, f"Selesai {i}/{len(paths)}")

    def _add_row(self, row, data):
        tags = ()
        if row["browser"] != "OK":
            tags = ("warn",)
        if row["diff"] != "-" and abs(float(row["diff"][:-1])) > 0.5:
            tags = ("bad",)
        iid = self.tree.insert("", "end", values=[row[c[0]] for c in COLUMNS], tags=tags)
        self.raw[iid] = data
        self.rows[iid] = row

    def _add_error(self, path, msg):
        iid = self.tree.insert(
            "", "end", values=[Path(path).name] + ["ERROR"] + [""] * (len(COLUMNS) - 2),
            tags=("bad",),
        )
        self.raw[iid] = {"error": msg, "path": path}

    # ── detail, clear, export ─────────────────────────────────────────────
    def show_detail(self, _=None):
        sel = self.tree.selection()
        self.detail.delete("1.0", "end")
        if sel:
            self.detail.insert("1.0", json.dumps(self.raw.get(sel[0], {}), indent=2))

    def clear(self):
        self.tree.delete(*self.tree.get_children())
        self.raw.clear()
        self.rows.clear()
        self.detail.delete("1.0", "end")

    def export_csv(self):
        if not self.rows:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")]
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([c[1] for c in COLUMNS] + ["Path"])
            for r in self.rows.values():
                w.writerow([r[c[0]] for c in COLUMNS] + [r["path"]])
        self.status.set(f"CSV disimpan: {path}")


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
