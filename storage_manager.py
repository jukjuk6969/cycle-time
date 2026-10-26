"""
storage_manager.py
==================
Manajemen storage video — Quarterly Retention Strategy.

Struktur folder:

    videos/
    ├── _tmp/                        (rekaman yang sedang berjalan)
    └── <tahun>/
        ├── <tahun>_summary.csv      (tidak pernah disentuh modul ini)
        └── <bulan>/
            ├── normal/              (retensi quarterly)
            └── anomali/             (permanen, hanya hapus manual)

Aturan:
  - Video normal : record terus berjalan setiap bulan.
  - Setiap awal quarter (April, July, October, January), 3 bulan
    sebelumnya masuk periode BACKUP selama 1 bulan.
  - Setelah 1 bulan backup selesai, pada bulan berikutnya video normal
    dari quarter tersebut mulai dihapus otomatis.
  - Contoh: Jan-Mar -> backup April -> delete mulai May.
  - Video anomali: TIDAK pernah dihapus otomatis. Hapus manual lewat
                   delete_anomaly_video() (path relatif dari VIDEOS_DIR) atau
                   delete_anomaly_by_name() (nama file saja, dipakai API FD).
  - File "_tmp_*" : rekaman yang sedang berjalan ada di TMP_DIR. Sisa rekaman
                    yang terputus (mtime > ORPHAN_TMP_SECONDS) dihapus.
  - File non-mp4 (mis. 2026_summary.csv) tidak pernah disentuh.
  - Video lama (sebelum migrasi) di videos/normal dan videos/anomaly tetap bisa
    diputar dan dihapus lewat find_video(), tetapi tidak ikut retensi otomatis.
    Pindahkan dengan migrate_videos.py.

Konvensi nama file:
    [l<line>_]st<station>_<timestamp>_<ct>[_GENESIS][_LONG|_SHORT_ANOMALY].mp4
"""

import re
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
import csv
import os

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
VIDEOS_DIR = BASE_DIR / "videos"
TMP_DIR = VIDEOS_DIR / "_tmp"        # rekaman yang sedang berjalan

NORMAL_DIRNAME = "normal"
ANOMALY_DIRNAME = "anomali"
LEGACY_ANOMALY_DIRNAME = "anomaly"   # nama folder lama (datar) sebelum migrasi

# Nama bulan tetap Inggris dan tidak bergantung locale sistem.
# Ubah ke "01_January", dst. kalau ingin urutan folder kronologis.
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

RECORD_MONTHS = 3          # satu periode record terdiri dari 3 bulan
BACKUP_WINDOW_MONTHS = 1    # 1 bulan penuh untuk backup sebelum delete
CLEANUP_INTERVAL = 3600     # cek setiap 1 jam
ORPHAN_TMP_SECONDS = 7200   # _tmp_ yang tidak berubah > 2 jam dianggap sisa crash

TMP_PREFIX = "_tmp_"
_GB = 1024 ** 3
_MB = 1024 ** 2

_NAME_RE = re.compile(r"^(?:l(?P<line>\d+)_)?st(?P<station>\d+)_")
_TS_RE = re.compile(r"_(\d{4})(\d{2})\d{2}_\d{6}")     # tahun & bulan dari nama file

# ── Summary CSV ───────────────────────────────────────────────────────────────
SUMMARY_FILENAME_FMT = "{year}_summary.csv"   # ganti ke "{year}-summary.csv" jika perlu

SUMMARY_COLUMNS = [
    "line",
    "station_id",
    "operator_id",
    "cycle_time_sec",
    "start_timestamp",
    "end_timestamp",
    "is_complete",
    "is_anomaly",
    "created_at",
    "cycle_count",
    "video_filename",
    "anomaly_type",
    "is_genesis",
    "anomaly_type",
    "is_genesis",
    "anomaly_label",
    "skipped_expected",
]

class StorageManager:
    _summary_lock = threading.Lock()
    """
    Background thread cleanup video.
    Hanya menghapus otomatis file di folder <tahun>/<bulan>/normal/.
    Folder anomali dan file CSV tidak pernah disentuh oleh cleanup.
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="storage-manager"
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        self._thread.start()
        logger.info(
            f"[StorageManager] Berjalan | "
            f"Record: {RECORD_MONTHS} bulan | "
            f"Backup window: {BACKUP_WINDOW_MONTHS} bulan | "
            f"Delete: mulai bulan setelah backup | "
            f"Anomali: permanen (hapus manual)"
        )

    def stop(self):
        self._stop_event.set()

    # ── Helper path untuk modul perekam ───────────────────────────────────────

    @staticmethod
    def get_save_dir(is_anomaly: bool, when: datetime = None) -> Path:
        """
        Kembalikan folder tujuan simpan video dan buat jika belum ada.
        Contoh: videos/2026/April/normal  atau  videos/2026/April/anomali
        `when` harus waktu yang sama dengan timestamp di nama file, karena
        find_video() membaca tahun/bulan dari nama file.
        """
        when = when or datetime.now()
        kind = ANOMALY_DIRNAME if is_anomaly else NORMAL_DIRNAME
        folder = VIDEOS_DIR / str(when.year) / MONTH_NAMES[when.month - 1] / kind
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @staticmethod
    def to_relative(path: Path) -> str:
        """Path relatif dari VIDEOS_DIR (pakai '/'), cocok untuk disimpan di DB/CSV."""
        return Path(path).resolve().relative_to(VIDEOS_DIR.resolve()).as_posix()

    # ── Summary CSV tahunan ───────────────────────────────────────────────────

    @staticmethod
    def get_summary_path(year: int) -> Path:
        """
        Path summary CSV tahunan, sejajar dengan folder bulan.
        Contoh: videos/2026/2026_summary.csv
        """
        folder = VIDEOS_DIR / str(year)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / SUMMARY_FILENAME_FMT.format(year=year)

    @staticmethod
    def _to_datetime(value):
        """Terima datetime / epoch (detik) / string ISO / None -> datetime atau None."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return datetime.fromtimestamp(value)          # epoch dari FD
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _csv_value(value):
        """Normalisasi nilai untuk CSV (datetime -> ISO, None -> kosong)."""
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        return value
    _header_checked: set = set()

    @staticmethod
    def _migrate_summary_header(path: Path):
        """Header file lama lebih pendek -> tulis ulang dengan kolom baru (nilai kosong)."""
        with open(path, "r", newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        if not rows or rows[0] == SUMMARY_COLUMNS:
            return
        old = rows[0]
        if old != SUMMARY_COLUMNS[:len(old)]:
            logger.warning(f"[StorageManager] Header {path.name} tidak dikenal, tidak diubah. "
                           f"Ganti nama file itu agar summary baru dibuat.")
            return
        pad = [""] * (len(SUMMARY_COLUMNS) - len(old))
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(SUMMARY_COLUMNS)
            for r in rows[1:]:
                w.writerow(r + pad)
        os.replace(tmp, path)
        logger.info(f"[StorageManager] Header {path.name} dimigrasi ({len(pad)} kolom baru)")
    def append_summary(self, record: dict, when: datetime = None):
        """
        Tambahkan satu baris siklus ke summary CSV tahunan.
        File dan header dibuat otomatis jika belum ada.

        record: dict dengan key sesuai SUMMARY_COLUMNS, yaitu
                line, station_id, operator_id, cycle_time_sec, start_timestamp,
                end_timestamp, is_complete, is_anomaly, created_at, cycle_count,
                video_filename, anomaly_type, is_genesis.
                Timestamp boleh datetime, epoch (detik), atau string ISO.
                Key yang hilang diisi kosong, key tambahan diabaikan.
                created_at yang kosong diisi end_timestamp.
        when:   menentukan tahun file. Default: end_timestamp, lalu
                start_timestamp, lalu waktu sekarang.

        Catatan: file summary lama dengan header berbeda harus diganti namanya.
        Return Path file summary, atau None jika gagal.
        """
        try:
            when = (
                when
                or self._to_datetime(record.get("end_timestamp"))
                or self._to_datetime(record.get("start_timestamp"))
                or datetime.now()
            )

            row = {col: self._csv_value(record.get(col)) for col in SUMMARY_COLUMNS}
            for col in ("start_timestamp", "end_timestamp", "created_at"):
                dt = self._to_datetime(record.get(col))
                if dt:
                    row[col] = self._csv_value(dt)
            if not row["created_at"]:
                row["created_at"] = row["end_timestamp"] or self._csv_value(datetime.now())

            path = self.get_summary_path(when.year)

            with self._summary_lock:
                if path.exists() and path.stat().st_size > 0 and path not in self._header_checked:
                    self._migrate_summary_header(path)
                    self._header_checked.add(path)
                need_header = not path.exists() or path.stat().st_size == 0
                with open(path, "a", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(
                        fh, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore"
                    )
                    if need_header:
                        writer.writeheader()
                    writer.writerow(row)

            return path
        except Exception as e:
            logger.error(f"[StorageManager] Gagal tulis summary: {e}")
            return None

    # ── Scan file ─────────────────────────────────────────────────────────────

    @staticmethod
    def _scan(kind: str) -> list:
        """
        Scan semua mp4 di <tahun>/<bulan>/<kind>/ dan kembalikan list
        (path, mtime, size). stat() hanya dipanggil sekali per file.
        File _tmp_ dilewati.
        """
        result = []
        for f in VIDEOS_DIR.glob(f"*/*/{kind}/*.mp4"):
            if f.name.startswith(TMP_PREFIX):
                continue
            try:
                st = f.stat()
            except OSError:
                continue  # file hilang di tengah scan
            result.append((f, st.st_mtime, st.st_size))
        return result

    # ── Quarterly retention ───────────────────────────────────────────────────

    @staticmethod
    def _delete_period_for(now: datetime = None):
        """
        Tentukan quarter yang sudah melewati 1 bulan backup dan boleh dihapus.

        Pola:
            Jan-Mar  -> Backup April   -> Delete mulai May
            Apr-Jun  -> Backup July    -> Delete mulai August
            Jul-Sep  -> Backup October -> Delete mulai November
            Oct-Dec  -> Backup January -> Delete mulai February

        Return:
            (year, start_month, end_month) jika saat ini bulan delete,
            atau None jika saat ini masih bulan record/backup.
        """
        now = now or datetime.now()

        # Bulan 1, 4, 7, 10 adalah bulan BACKUP. Belum boleh delete.
        # Delete hanya mulai bulan ke-2 setelah quarter selesai:
        # Feb/Mar, May/Jun, Aug/Sep, Nov/Dec.
        if now.month % 3 == 1:
            return None

        # Ambil quarter SEBELUM quarter berjalan.
        # Contoh:
        #   May/June -> Q1 (Jan-Mar)
        #   Aug/Sep  -> Q2 (Apr-Jun)
        #   Nov/Dec  -> Q3 (Jul-Sep)
        #   Feb/Mar  -> Q4 tahun sebelumnya (Oct-Dec)
        current_quarter_start = (
            ((now.month - 1) // RECORD_MONTHS) * RECORD_MONTHS + 1
        )
        quarter_start = current_quarter_start - RECORD_MONTHS
        target_year = now.year

        if quarter_start <= 0:
            quarter_start += 12
            target_year -= 1

        quarter_end = quarter_start + RECORD_MONTHS - 1
        return target_year, quarter_start, quarter_end

    @staticmethod
    def _period_months(year: int, start_month: int, end_month: int) -> set:
        """Buat set (year, month) untuk satu quarter kalender."""
        return {(year, month) for month in range(start_month, end_month + 1)}

    def run_cleanup(self, now: datetime = None) -> dict:
        """
        Cleanup normal berdasarkan quarter kalender.

        Recording tidak dihentikan. Cleanup hanya menyentuh video normal
        yang berada pada quarter yang sudah mendapat 1 bulan backup.
        Tidak ada penghapusan berdasarkan umur 90 hari dan tidak ada
        penghapusan berdasarkan size limit.
        """
        with self._cleanup_lock:
            now = now or datetime.now()
            files = self._scan(NORMAL_DIRNAME)
            delete_period = self._delete_period_for(now)

            deleted_count = 0
            deleted_bytes = 0
            deleted_period = None

            if delete_period:
                delete_year, start_month, end_month = delete_period
                delete_period = self._period_months(
                    delete_year, start_month, end_month
                )
                deleted_period = {
                    "year": delete_year,
                    "start_month": start_month,
                    "end_month": end_month,
                }

                for f, mtime, size in files:
                    # Struktur: videos/<year>/<month>/normal/file.mp4
                    try:
                        file_year = int(f.parents[2].name)
                        file_month = MONTH_NAMES.index(f.parents[1].name) + 1
                    except (ValueError, IndexError):
                        continue

                    if (file_year, file_month) not in delete_period:
                        continue

                    if self._safe_unlink(
                        f,
                        size,
                        f"quarter retention {delete_year}-"
                        f"{start_month:02d}..{end_month:02d}",
                    ):
                        deleted_count += 1
                        deleted_bytes += size

            # _tmp_ tetap dibersihkan terpisah. Rekaman aktif aman karena
            # mtime-nya terus berubah.
            orphan_count = self._cleanup_orphan_tmp()

            usage = self.get_usage()
            report = {
                "deleted_count": deleted_count,
                "deleted_mb": round(deleted_bytes / _MB, 2),
                "orphan_tmp_deleted": orphan_count,
                "normal_usage_gb": usage["normal_gb"],
                "anomaly_count": usage["anomaly_count"],
                "anomaly_usage_gb": usage["anomaly_gb"],
                "record_months": RECORD_MONTHS,
                "backup_window_months": BACKUP_WINDOW_MONTHS,
                "delete_period": deleted_period,
            }

            if deleted_count > 0:
                logger.info(
                    f"[StorageManager] Quarterly cleanup: {deleted_count} file "
                    f"dihapus ({deleted_bytes / _MB:.1f}MB) | "
                    f"periode {delete_year}-{start_month:02d} "
                    f"s/d {delete_year}-{end_month:02d}"
                )
            elif deleted_period:
                logger.debug(
                    f"[StorageManager] Tidak ada file untuk dihapus pada "
                    f"periode {delete_year}-{start_month:02d} "
                    f"s/d {delete_year}-{end_month:02d}"
                )

            return report

    def _cleanup_orphan_tmp(self) -> int:
        """Hapus _tmp_*.mp4 yang tidak berubah > ORPHAN_TMP_SECONDS (sisa rekaman crash)."""
        deleted = 0
        now = time.time()
        candidates = list(TMP_DIR.glob(f"{TMP_PREFIX}*.mp4"))
        candidates += list(VIDEOS_DIR.glob(f"*/*/{NORMAL_DIRNAME}/{TMP_PREFIX}*.mp4"))
        for f in candidates:
            try:
                if now - f.stat().st_mtime > ORPHAN_TMP_SECONDS:
                    f.unlink()
                    deleted += 1
                    logger.warning(f"[StorageManager] Hapus sisa rekaman: {f.name}")
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.error(f"[StorageManager] Gagal hapus {f.name}: {e}")
        return deleted

    @staticmethod
    def _safe_unlink(path: Path, size: int, reason: str) -> bool:
        try:
            path.unlink()
            logger.debug(
                f"[StorageManager] Hapus normal ({reason}): "
                f"{path.name} ({size / _MB:.1f}MB)"
            )
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.error(f"[StorageManager] Gagal hapus {path.name}: {e}")
            return False

    # ── Cari file lewat nama (dipakai API FD) ─────────────────────────────────

    def find_video(self, vtype: str, filename: str):
        """
        Cari file video lewat nama. vtype: 'normal' | 'anomaly' (dari URL server).
        Urutan: folder tahun/bulan dari timestamp di nama file, lalu semua bulan,
        lalu folder lama (datar) untuk video sebelum migrasi.
        Return Path atau None.
        """
        if (not filename.endswith(".mp4") or "/" in filename
                or "\\" in filename or ".." in filename):
            return None
        dirname = ANOMALY_DIRNAME if vtype == "anomaly" else NORMAL_DIRNAME

        m = _TS_RE.search(filename)
        if m and 1 <= int(m.group(2)) <= 12:
            p = (VIDEOS_DIR / m.group(1) / MONTH_NAMES[int(m.group(2)) - 1]
                 / dirname / filename)
            if p.is_file():
                return p

        for p in VIDEOS_DIR.glob(f"*/*/{dirname}/{filename}"):   # nama tak berpola
            if p.is_file():
                return p

        legacy_dir = LEGACY_ANOMALY_DIRNAME if vtype == "anomaly" else NORMAL_DIRNAME
        legacy = VIDEOS_DIR / legacy_dir / filename               # sebelum migrasi
        return legacy if legacy.is_file() else None

    # ── Hapus manual anomali ──────────────────────────────────────────────────

    def delete_anomaly_by_name(self, filename: str):
        """
        Hapus video anomali lewat nama file.
        Return True = terhapus, False = gagal/ditolak, None = tidak ditemukan.
        """
        p = self.find_video("anomaly", filename)
        if p is None:
            return None
        if p.parent.name not in (ANOMALY_DIRNAME, LEGACY_ANOMALY_DIRNAME):
            return False
        try:
            size_mb = p.stat().st_size / _MB
            p.unlink()
            logger.info(f"[StorageManager] Manual delete anomali: {p.name} ({size_mb:.1f}MB)")
            return True
        except Exception as e:
            logger.error(f"[StorageManager] Gagal hapus anomali {p.name}: {e}")
            return False

    def delete_anomaly_video(self, relative_path: str) -> bool:
        """
        Hapus satu video anomali secara manual.

        relative_path: path relatif dari VIDEOS_DIR,
                       contoh "2026/April/anomali/l1_st1_xxx_ANOMALY.mp4".
        Return True jika berhasil, False jika tidak ditemukan, ditolak, atau gagal.
        """
        try:
            base = VIDEOS_DIR.resolve()
            target = (VIDEOS_DIR / relative_path).resolve()

            # Cegah path traversal & pastikan benar-benar file anomali
            if base not in target.parents:
                logger.warning(f"[StorageManager] Ditolak (di luar videos/): {relative_path}")
                return False
            if target.parent.name != ANOMALY_DIRNAME or target.suffix.lower() != ".mp4":
                logger.warning(f"[StorageManager] Ditolak (bukan video anomali): {relative_path}")
                return False
            if not target.is_file():
                logger.warning(f"[StorageManager] File tidak ditemukan: {relative_path}")
                return False

            size_mb = target.stat().st_size / _MB
            target.unlink()
            logger.info(
                f"[StorageManager] Manual delete anomali: {relative_path} ({size_mb:.1f}MB)"
            )
            return True
        except Exception as e:
            logger.error(f"[StorageManager] Gagal hapus anomali {relative_path}: {e}")
            return False

    def delete_anomaly_videos_bulk(self, relative_paths: list) -> dict:
        """Hapus beberapa video anomali sekaligus. Return hasil per path."""
        return {p: self.delete_anomaly_video(p) for p in relative_paths}

    # ── Info & listing ────────────────────────────────────────────────────────

    def get_usage(self) -> dict:
        normal = self._scan(NORMAL_DIRNAME)
        anomaly = self._scan(ANOMALY_DIRNAME)

        normal_bytes = sum(s for _, _, s in normal)
        anomaly_bytes = sum(s for _, _, s in anomaly)

        oldest_normal = None
        if normal:
            oldest_ts = min(m for _, m, _ in normal)
            oldest_normal = datetime.fromtimestamp(oldest_ts).isoformat()

        return {
            "normal_count": len(normal),
            "normal_gb": round(normal_bytes / _GB, 3),
            "record_months": RECORD_MONTHS,
            "backup_window_months": BACKUP_WINDOW_MONTHS,
            "anomaly_count": len(anomaly),
            "anomaly_gb": round(anomaly_bytes / _GB, 3),
            "total_gb": round((normal_bytes + anomaly_bytes) / _GB, 3),
            "oldest_normal_file": oldest_normal,
        }

    def list_videos(
        self,
        anomaly_only: bool = False,
        station_id: int = None,
        line: int = None,
        year: int = None,
        month: str = None,
    ) -> list:
        """
        Daftar video terurut dari yang terbaru.
        Filter opsional: station_id, line, year (2026), month ("April").
        'path' berisi path relatif dari VIDEOS_DIR (dipakai untuk delete/akses).
        """
        kind = ANOMALY_DIRNAME if anomaly_only else NORMAL_DIRNAME
        y = str(year) if year else "*"
        m = month if month else "*"

        entries = []
        for f in VIDEOS_DIR.glob(f"{y}/{m}/{kind}/*.mp4"):
            if f.name.startswith(TMP_PREFIX):
                continue
            if station_id and self._parse_station_id(f.name) != station_id:
                continue
            if line and self._parse_line(f.name) != line:
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            entries.append((f, st))

        entries.sort(key=lambda x: x[1].st_mtime, reverse=True)

        return [
            {
                "filename": f.name,
                "path": f.relative_to(VIDEOS_DIR).as_posix(),
                "year": f.parents[2].name,
                "month": f.parents[1].name,
                "size_mb": round(st.st_size / _MB, 2),
                "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "is_anomaly": anomaly_only,
                "anomaly_type": self._parse_anomaly_type(f.name),
                "is_genesis": "_GENESIS" in f.name,
                "is_expected": "_EXPECTED_" in f.name,
                "line": self._parse_line(f.name),
                "station_id": self._parse_station_id(f.name),
                "can_delete": anomaly_only,  # hanya anomali yang bisa dihapus manual
            }
            for f, st in entries
        ]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_station_id(filename: str) -> int:
        m = _NAME_RE.match(filename)
        return int(m.group("station")) if m else 0

    @staticmethod
    def _parse_line(filename: str) -> int:
        m = _NAME_RE.match(filename)
        return int(m.group("line")) if m and m.group("line") else 0

    @staticmethod
    def _parse_anomaly_type(filename: str) -> str:
        stem = filename[:-4] if filename.endswith(".mp4") else filename
        if not stem.endswith("_ANOMALY"):
            return "OK"
        tokens = stem.split("_")
        if "SHORT" in tokens:
            return "SHORT"
        if "LONG" in tokens:
            return "LONG"
        # hanya EXPECTED terlewat: anomaly_type tetap OK (sama dengan yang dikirim ke server)
        return "OK" if "EXPECTED" in tokens else "LONG"   # nama lama tanpa token = LONG

    def _loop(self):
        # Cleanup langsung saat start, lalu ulangi tiap CLEANUP_INTERVAL
        while not self._stop_event.is_set():
            try:
                self.run_cleanup()
            except Exception as e:
                logger.error(f"[StorageManager] Loop error: {e}")
            self._stop_event.wait(CLEANUP_INTERVAL)