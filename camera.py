import cv2
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from datetime import datetime
import threading
import time
import os
import sys
import queue
import shutil
import subprocess
# ==========================================================================
# Logger terpusat — semua log FPS/frame timing lewat sini
# ==========================================================================


class FrameLogger:
    TAGS = {
        "CAPTURE",
        "QUEUE",
        "WRITER",
        "BLEND",
        "DROP",
        "TIMING",
        "CAMERA",
        "RECORD",
    }

    def __init__(self, log_path: str):
        self._lock = threading.Lock()
        self._log_path = log_path
        self._file = open(log_path, "w", encoding="utf-8", buffering=1)
        self._start_time = time.time()
        self._write_header()
        print(f"[Logger] Log aktif → {log_path}")

    def _write_header(self):
        self._file.write(
            f"# Camera Recorder Frame Log\n"
            f"# Mulai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# Kolom: elapsed_ms | tag | pesan\n"
            f"{'=' * 70}\n"
        )

    def log(self, tag: str, msg: str, print_console: bool = True):
        elapsed_ms = (time.time() - self._start_time) * 1000
        line = f"{elapsed_ms:10.1f}ms | {tag:<8} | {msg}"
        with self._lock:
            self._file.write(line + "\n")
        if print_console:
            print(f"[{tag}] {msg}")

    def close(self):
        with self._lock:
            self._file.write(
                f"{'=' * 70}\n# Selesai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            self._file.close()


# ==========================================================================
# App
# ==========================================================================


class CameraRecorderApp:
    # Target output — VideoWriter & writer_worker selalu pakai ini
    OUTPUT_FPS = 30

    def __init__(self, root):
        self.root = root
        self.root.title("Camera Capture & Recorder")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)

        self.cap = None
        self.running = False
        self.recording = False
        self.video_writer = None
        self._ffmpeg_proc = None  # ← proses FFmpeg pipe
        self.format_var = tk.StringVar(value="mp4")  # ← pilihan format
        self.record_start_time = None

        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._capture_thread = None
        self._writer_thread = None
        self._write_queue = queue.Queue(maxsize=120)

        self._measured_fps = None
        self._fps_stable = False
        self._actual_fps = 30.0

        self.output_dir = os.path.join(os.getcwd(), "captures")
        os.makedirs(self.output_dir, exist_ok=True)

        log_filename = f"framelog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        self._logger = FrameLogger(os.path.join(self.output_dir, log_filename))

        self.camera_index = tk.IntVar(value=0)
        self.width = tk.IntVar(value=1280)
        self.height = tk.IntVar(value=720)
        self.fps = tk.IntVar(value=30)

        self.status_var = tk.StringVar(value="Kamera belum dibuka")
        self.record_status_var = tk.StringVar(value="READY")
        self.timer_var = tk.StringVar(value="00:00:00")
        self.folder_var = tk.StringVar(value=self.output_dir)
        self.photo_count_var = tk.StringVar(value="Foto: 0")
        self._photo_count = 0

        self._build_ui()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        preview_frame = ttk.LabelFrame(main, text="Live Preview", padding=5)
        preview_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        self.preview = tk.Label(
            preview_frame,
            text="Camera Preview",
            bg="black",
            fg="white",
            font=("Arial", 20),
        )
        self.preview.grid(row=0, column=0, sticky="nsew")

        control = ttk.Frame(main, width=300)
        control.grid(row=0, column=1, sticky="ns")

        camera_box = ttk.LabelFrame(control, text="Camera", padding=10)
        camera_box.pack(fill="x", pady=(0, 10))

        ttk.Label(camera_box, text="Camera Index").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            camera_box, from_=0, to=20, textvariable=self.camera_index, width=8
        ).grid(row=0, column=1, sticky="ew", padx=5)

        for row, (label, var) in enumerate(
            [("Width", self.width), ("Height", self.height), ("FPS", self.fps)], start=1
        ):
            ttk.Label(camera_box, text=label).grid(
                row=row, column=0, sticky="w", pady=4
            )
            ttk.Entry(camera_box, textvariable=var, width=10).grid(
                row=row, column=1, sticky="ew", padx=5
            )

        ttk.Label(camera_box, text="Format").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(
            camera_box,
            textvariable=self.format_var,
            values=["mp4 (H.264)", "mp4 (H.265)"],
            state="readonly",
            width=12,
        ).grid(row=4, column=1, sticky="ew", padx=5)

        self.open_btn = ttk.Button(
            camera_box, text="Buka Kamera", command=self.toggle_camera
        )
        self.open_btn.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        capture_box = ttk.LabelFrame(control, text="Capture", padding=10)
        capture_box.pack(fill="x", pady=(0, 10))

        self.photo_btn = ttk.Button(
            capture_box, text="📷  FOTO  [F]", command=self.take_photo, state="disabled"
        )
        self.photo_btn.pack(fill="x", ipady=8, pady=3)

        self.record_btn = ttk.Button(
            capture_box,
            text="🎥  MULAI REKAM  [R]",
            command=self.toggle_recording,
            state="disabled",
        )
        self.record_btn.pack(fill="x", ipady=8, pady=3)

        ttk.Label(capture_box, text="Durasi Rekaman").pack(anchor="w", pady=(10, 0))
        ttk.Label(
            capture_box, textvariable=self.timer_var, font=("Consolas", 20, "bold")
        ).pack(anchor="center")
        ttk.Label(
            capture_box, textvariable=self.photo_count_var, font=("Arial", 9)
        ).pack(anchor="center", pady=(4, 0))

        storage_box = ttk.LabelFrame(control, text="Penyimpanan", padding=10)
        storage_box.pack(fill="x", pady=(0, 10))
        ttk.Label(storage_box, textvariable=self.folder_var, wraplength=270).pack(
            anchor="w", pady=(0, 8)
        )
        ttk.Button(storage_box, text="Pilih Folder", command=self.select_folder).pack(
            fill="x"
        )

        status_box = ttk.LabelFrame(control, text="Status", padding=10)
        status_box.pack(fill="x")
        ttk.Label(status_box, text="Camera:").pack(anchor="w")
        ttk.Label(
            status_box, textvariable=self.status_var, font=("Arial", 10, "bold")
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(status_box, text="Recording:").pack(anchor="w")
        ttk.Label(
            status_box, textvariable=self.record_status_var, font=("Arial", 10, "bold")
        ).pack(anchor="w")

        self.root_status = ttk.Label(
            self.root, text="Ready", relief="sunken", anchor="w", padding=5
        )
        self.root_status.grid(row=1, column=0, sticky="ew")

    def _bind_shortcuts(self):
        self.root.bind(
            "<f>",
            lambda e: self.take_photo() if self._latest_frame is not None else None,
        )
        self.root.bind(
            "<r>", lambda e: self.toggle_recording() if self.running else None
        )

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    def toggle_camera(self):
        if self.running:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        try:
            index = int(self.camera_index.get())
            width = int(self.width.get())
            height = int(self.height.get())
            fps = int(self.fps.get())

            if sys.platform == "win32":
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(index)

            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(index)

            if not cap.isOpened():
                raise RuntimeError(f"Tidak dapat membuka kamera index {index}")

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            self.cap = cap
            self.running = True
            self._measured_fps = None
            self._fps_stable = False

            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            self._actual_fps = actual_fps if 0 < actual_fps <= 240 else float(fps)

            self._logger.log(
                "CAMERA",
                f"index={index} | resolusi={actual_w}x{actual_h} "
                f"| fps_report={self._actual_fps:.1f} | fps_set={fps} "
                f"| output_target={self.OUTPUT_FPS}fps",
            )

            self.status_var.set(
                f"ON - {actual_w}x{actual_h} @ {self._actual_fps:.1f} FPS"
            )
            self.open_btn.config(text="Tutup Kamera")
            self.photo_btn.config(state="normal")
            self.record_btn.config(state="disabled")
            self.root_status.config(text="Menunggu FPS stabil...")

            # Satu capture thread saja
            self._capture_thread = threading.Thread(
                target=self._capture_loop, daemon=True
            )
            self._capture_thread.start()
            self._schedule_ui_update()

        except Exception as e:
            if self.cap:
                self.cap.release()
                self.cap = None
            messagebox.showerror("Camera Error", str(e))

    # ------------------------------------------------------------------
    # Capture loop — busy-wait + high priority thread
    # ------------------------------------------------------------------

    def _capture_loop(self):
        # Naikkan prioritas thread di Windows
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(),
                2,  # THREAD_PRIORITY_HIGHEST
            )
        except Exception:
            pass

        # Flush buffer kamera — buang frame lama sebelum mulai
        for _ in range(5):
            self.cap.read()

        consecutive_failures = 0
        frame_count = 0
        fps_timer = time.perf_counter()
        last_frame_time = None
        measurement_count = 0

        # target_interval dari fps yang di-set user (misal 30fps → 33.3ms)
        target_interval = 1.0 / self._actual_fps
        next_capture_time = time.perf_counter()

        while self.running:
            # ---- busy-wait presisi tinggi ----
            now = time.perf_counter()
            wait = next_capture_time - now
            if wait > 0.002:
                time.sleep(wait - 0.001)  # sleep kasar
            while time.perf_counter() < next_capture_time:
                pass  # busy-wait sisa

            ret, frame = self.cap.read()
            now = time.perf_counter()

            # Jadwalkan frame berikutnya — pakai increment, bukan now+interval
            # agar tidak drift saat ada delay sesekali
            next_capture_time += target_interval

            # Kalau sangat terlambat (lebih dari 2 frame), reset jadwal
            if now > next_capture_time + target_interval:
                next_capture_time = now + target_interval

            if not ret:
                consecutive_failures += 1
                self._logger.log(
                    "CAPTURE", f"frame gagal | consecutive={consecutive_failures}"
                )
                if consecutive_failures >= 10:
                    self.root.after(0, self._on_camera_error)
                    break
                continue

            consecutive_failures = 0
            frame_count += 1

            # Log gap anomali
            if last_frame_time is not None:
                gap_ms = (now - last_frame_time) * 1000
                expected_ms = target_interval * 1000
                if gap_ms > expected_ms * 1.5:
                    self._logger.log(
                        "CAPTURE",
                        f"gap anomali: {gap_ms:.1f}ms (normal={expected_ms:.1f}ms) | frame #{frame_count}",
                    )
            last_frame_time = now

            # Ukur FPS aktual tiap 3 detik
            elapsed = now - fps_timer
            if elapsed >= 3.0:
                self._measured_fps = frame_count / elapsed
                measurement_count += 1
                self._logger.log(
                    "TIMING",
                    f"capture fps={self._measured_fps:.2f} | frames={frame_count} "
                    f"| elapsed={elapsed:.2f}s | measurement #{measurement_count}",
                )
                frame_count = 0
                fps_timer = now

                if measurement_count >= 2 and not self._fps_stable:
                    self._fps_stable = True
                    self.root.after(0, self._on_fps_stable)

            with self._frame_lock:
                self._latest_frame = frame

            if self.recording:
                try:
                    self._write_queue.put_nowait((frame.copy(), now))
                except queue.Full:
                    self._logger.log(
                        "DROP",
                        f"queue penuh — frame dibuang | qsize={self._write_queue.qsize()}",
                    )

    def _on_fps_stable(self):
        if self.running:
            self.record_btn.config(state="normal")
            self.root_status.config(
                text=f"Kamera aktif — capture {self._measured_fps:.1f}fps → output {self.OUTPUT_FPS}fps"
            )
            self._logger.log(
                "CAMERA",
                f"FPS stabil | measured={self._measured_fps:.2f} "
                f"| output_target={self.OUTPUT_FPS} — rekam diizinkan",
            )

    def _on_camera_error(self):
        self._logger.log("CAMERA", "disconnect — capture berhenti")
        self.root_status.config(text="⚠ Kamera disconnect — capture berhenti")
        self.stop_camera()

    def stop_camera(self):
        if self.recording:
            self.stop_recording()

        self.running = False

        if self._capture_thread:
            self._capture_thread.join(timeout=2)
            self._capture_thread = None

        if self.cap:
            self.cap.release()
            self.cap = None

        with self._frame_lock:
            self._latest_frame = None

        self._logger.log("CAMERA", "kamera ditutup")
        self.preview.config(image="", text="Camera Preview")
        self.status_var.set("Kamera belum dibuka")
        self.root_status.config(text="Kamera ditutup")
        self.open_btn.config(text="Buka Kamera")
        self.photo_btn.config(state="disabled")
        self.record_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _schedule_ui_update(self):
        if not self.running:
            return

        with self._frame_lock:
            frame = (
                self._latest_frame.copy() if self._latest_frame is not None else None
            )

        if frame is not None:
            display = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            display = self._resize_for_preview(display, 760, 560)
            image = Image.fromarray(display)
            photo = ImageTk.PhotoImage(image=image)
            self.preview.config(image=photo, text="")
            self.preview.image = photo

        if self.recording:
            self._update_timer()

        self.root.after(33, self._schedule_ui_update)

    @staticmethod
    def _resize_for_preview(image, max_width, max_height):
        h, w = image.shape[:2]
        scale = min(max_width / w, max_height / h)
        if scale >= 1:
            return image
        return cv2.resize(
            image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
        )

    # ------------------------------------------------------------------
    # Photo
    # ------------------------------------------------------------------

    def take_photo(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return
            snapshot = self._latest_frame.copy()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = os.path.join(self.output_dir, f"photo_{timestamp}.jpg")

        if cv2.imwrite(filename, snapshot):
            self._photo_count += 1
            self.photo_count_var.set(f"Foto: {self._photo_count}")
            self.root_status.config(text=f"Foto tersimpan: {filename}")
        else:
            messagebox.showerror("Error", "Gagal menyimpan foto.")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        with self._frame_lock:
            if self._latest_frame is None or self.cap is None:
                messagebox.showwarning(
                    "Rekam", "Kamera belum siap, tunggu beberapa detik."
                )
                return

        try:
            if not os.access(self.output_dir, os.W_OK):
                raise RuntimeError(f"Folder tidak bisa ditulis: {self.output_dir}")

            width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            # VideoWriter SELALU pakai OUTPUT_FPS (30)
            out_fps = self.OUTPUT_FPS

            # Gunakan fallback aman jika _measured_fps belum terisi
            capture_fps = (
                self._measured_fps
                if self._measured_fps is not None
                else self._actual_fps
            )

            if not shutil.which("ffmpeg"):
                raise RuntimeError(
                    "FFmpeg tidak ditemukan. Install FFmpeg terlebih dahulu."
                )

            fmt = self.format_var.get()
            codec = "libx265" if "H.265" in fmt else "libx264"
            crf = "28"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(self.output_dir, f"video_{timestamp}.mp4")

            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-s",
                f"{width}x{height}",
                "-pix_fmt",
                "bgr24",
                "-r",
                str(out_fps),
                "-i",
                "-",
                "-c:v",
                codec,
                "-crf",
                crf,
                "-preset",
                "fast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",  # ← MOOV di depan, langsung bisa diputar
                filename,
            ]
            self._ffmpeg_proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            self.video_writer = None  # ← tidak pakai VideoWriter sama sekali

            self.recording = True
            self.record_start_time = time.time()
            self.current_video = filename

            self._logger.log(
                "RECORD",
                f"START | file={os.path.basename(filename)} "
                f"| resolusi={width}x{height} "
                f"| fps_writer={out_fps} "
                f"| fps_capture={capture_fps:.2f} "
                f"| fps_report={self._actual_fps:.1f}",
            )

            self._write_queue = queue.Queue(maxsize=120)
            self._writer_thread = threading.Thread(
                target=self._writer_worker, daemon=True
            )
            self._writer_thread.start()

            self.record_btn.config(text="⏹  STOP REKAM  [R]")
            self.record_status_var.set("● RECORDING")
            self.root_status.config(text=f"Merekam: {filename}")

        except Exception as e:
            messagebox.showerror("Recording Error", str(e))

    # ------------------------------------------------------------------
    # Writer worker — frame duplication berbasis akumulasi waktu
    # ------------------------------------------------------------------

    def _writer_worker(self):
        """
        FFmpeg pipe — tidak perlu accumulator/duplikasi frame.
        FFmpeg yang handle timestamp & frame rate secara internal.
        """
        total_written = 0
        stat_timer = time.time()

        self._logger.log("WRITER", "worker start | pipe → FFmpeg")

        while True:
            item = self._write_queue.get()
            if item is None:
                break

            frame, _ = item  # ← timestamp tidak dipakai, FFmpeg yang generate PTS

            try:
                self._ffmpeg_proc.stdin.write(frame.tobytes())
                total_written += 1
            except BrokenPipeError:
                self._logger.log("WRITER", "pipe putus — FFmpeg berhenti lebih awal")
                break

            now = time.time()
            if now - stat_timer >= 5.0:
                self._logger.log(
                    "TIMING",
                    f"writer | total={total_written} | queue={self._write_queue.qsize()}",
                )
                stat_timer = now

            self._write_queue.task_done()

        self._logger.log("WRITER", f"SELESAI | total_ditulis={total_written}")

    def stop_recording(self):
        self.recording = False
        duration = time.time() - self.record_start_time if self.record_start_time else 0

        if self._writer_thread and self._writer_thread.is_alive():
            self._write_queue.put(None)
            self._writer_thread.join()  # tunggu queue habis dulu
        self._writer_thread = None

        if self._ffmpeg_proc:
            self._ffmpeg_proc.stdin.close()  # ← sinyal EOF ke FFmpeg
            self._ffmpeg_proc.wait()  # ← tunggu FFmpeg finalize MOOV
            self._ffmpeg_proc = None  # ← baru null-kan

        self._logger.log(
            "RECORD",
            f"STOP | durasi_nyata={duration:.1f}s | file={getattr(self, 'current_video', '-')}",
        )

        self.record_start_time = None
        self.timer_var.set("00:00:00")
        self.record_btn.config(text="🎥  MULAI REKAM  [R]")
        self.record_status_var.set("READY")

        if hasattr(self, "current_video"):
            self.root_status.config(text=f"Video tersimpan: {self.current_video}")

    def _update_timer(self):
        if not self.recording or self.record_start_time is None:
            return
        elapsed = int(time.time() - self.record_start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.timer_var.set(f"{h:02d}:{m:02d}:{s:02d}")

    # ------------------------------------------------------------------
    # Storage / lifecycle
    # ------------------------------------------------------------------

    def select_folder(self):
        folder = filedialog.askdirectory(
            initialdir=self.output_dir, title="Pilih Folder Penyimpanan"
        )
        if folder:
            if not os.access(folder, os.W_OK):
                messagebox.showerror("Error", "Folder tidak bisa ditulis.")
                return
            self.output_dir = folder
            self.folder_var.set(folder)

    def close_app(self):
        if self.recording:
            if not messagebox.askyesno(
                "Rekam Aktif",
                "Sedang merekam. Yakin ingin menutup? Video akan disimpan.",
            ):
                return
        try:
            self.stop_camera()  # ← stop_camera() sudah handle: stop_recording(),
            #   join capture thread, lalu cap.release() — urutan aman
        finally:
            self._logger.close()
            self.root.destroy()


def main():
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    CameraRecorderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
