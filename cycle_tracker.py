"""
cycle_tracker.py
================

Production cycle tracker.

Tugas:
- hitung cycle time
- anomaly detection
- takt time monitoring
- completion tracking
- logging

Tracker ini membaca status dari:
    SequenceEngine

Tracker TIDAK membaca:
- kamera
- MediaPipe
- ROI

Flow:
------
Sequence START
    ↓
RUNNING
    ↓
Sequence COMPLETE
    ↓
Hitung cycle time
    ↓
Anomaly check
    ↓
Logging
    ↓
Reset
"""

import csv
import os
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────────────────
# ENUM
# ─────────────────────────────────────────────────────────


class CycleState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


# ─────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────


@dataclass
class CycleResult:
    cycle_id: int
    station_id: int  # ← tambah
    start_timestamp: float  # ← ganti dari started_at
    end_timestamp: float  # ← ganti dari completed_at
    cycle_time_sec: float  # ← ganti dari cycle_time_s
    takt_time_s: float
    is_complete: bool  # ← ganti dari status: str
    anomaly: bool
    completion_count: int
    operator_id: str = None  # ← tambah
    anomaly_type: str = "OK"
    label: str = ""
    is_genesis: bool = False


# ─────────────────────────────────────────────────────────
# TRACKER
# ─────────────────────────────────────────────────────────


class CycleTracker:
    def __init__(
        self,
        station_id: int = 0,
        takt_time_s: float = 20.0,
        anomaly_min_s: float = 0.0,
        anomaly_max_s: float = 20.0,
        log_path: str = "logs/cycle_log.csv",
        operator_id: str = None,
    ):

        self.takt_time_s = (
            float(takt_time_s) if isinstance(takt_time_s, (int, float)) else 10.0
        )
        self.anomaly_min_s = (
            float(anomaly_min_s) if isinstance(anomaly_min_s, (int, float)) else 3.0
        )
        self.anomaly_max_s = (
            float(anomaly_max_s) if isinstance(anomaly_max_s, (int, float)) else 20.0
        )
        self.log_path = log_path
        self.last_cycle_result = None
        self.state = CycleState.IDLE
        self.cycle_started_at: Optional[float] = None
        self.cycle_completed_at: Optional[float] = None
        self.current_cycle_time = 0.0
        self.completion_count = 0
        self.failed_count = 0
        self.anomaly_count = 0
        self.total_cycle_time = 0.0
        self.last_cycle_time = 0.0
        self.cycle_id = 0
        self._ensure_log_file()
        self.operator_id = operator_id
        self.station_id = station_id

    # ─────────────────────────────────────────────────────

    def update(self, sequence_engine, timestamp=None):

        if timestamp is None:
            timestamp = time.time()

        engine_state = sequence_engine.engine_state.value

        # =================================================
        # START cycle
        # =================================================

        if self.state == CycleState.IDLE and engine_state in ["dwelling"]:
            self.start_cycle(timestamp)

        # =================================================
        # COMPLETE cycle
        # =================================================

        if self.state == CycleState.RUNNING and engine_state == "complete":
            self.complete_cycle(timestamp)

        # =================================================
        # FAILED cycle
        # =================================================

        if engine_state == "failed":
            self.fail_cycle()

    # ─────────────────────────────────────────────────────

    def start_cycle(self, timestamp):

        self.state = CycleState.RUNNING

        self.cycle_started_at = timestamp

        print(f"[cycle_tracker] Cycle START (ID={self.cycle_id + 1})")

    # ─────────────────────────────────────────────────────

    def complete_cycle(self, timestamp):
        self.state = CycleState.COMPLETE
        self.cycle_completed_at = timestamp
        self.current_cycle_time = self.cycle_completed_at - self.cycle_started_at
        self.last_cycle_time = self.current_cycle_time
        self.total_cycle_time += self.current_cycle_time
        self.completion_count += 1
        self.cycle_id += 1
        anomaly = self.is_anomaly(self.current_cycle_time)
        if anomaly:
            self.anomaly_count += 1
        result = CycleResult(
            cycle_id=self.cycle_id,
            station_id=self.station_id,  # ← tambah
            start_timestamp=self.cycle_started_at,  # ← ganti
            end_timestamp=self.cycle_completed_at,  # ← ganti
            cycle_time_sec=self.current_cycle_time,  # ← ganti
            takt_time_s=self.takt_time_s,
            is_complete=True,  # ← ganti
            anomaly=anomaly,
            completion_count=self.completion_count,
            operator_id=self.operator_id,  # ← tambah
        )
        self.last_cycle_result = result
        self.log_cycle(result)
        takt_status = self.get_takt_status(self.current_cycle_time)
        print(
            f"[cycle_tracker] "
            f"Cycle COMPLETE "
            f"{self.current_cycle_time:.2f}s "
            f"| TAKT={takt_status}"
        )

        if anomaly:
            print("[cycle_tracker] ANOMALY DETECTED")

        self.state = CycleState.IDLE

    # ─────────────────────────────────────────────────────

    def fail_cycle(self):

        self.failed_count += 1

        self.state = CycleState.FAILED

        print("[cycle_tracker] Cycle FAILED")

        self.state = CycleState.IDLE

    # ─────────────────────────────────────────────────────

    def is_anomaly(self, cycle_time):
        return cycle_time > self.anomaly_max_s

    # ─────────────────────────────────────────────────────

    def get_takt_status(self, cycle_time):

        if cycle_time < self.takt_time_s:
            return "AHEAD"

        if cycle_time > self.takt_time_s:
            return "BEHIND"

        return "ON TARGET"

    # ─────────────────────────────────────────────────────

    @property
    def average_cycle_time(self):

        if self.completion_count == 0:
            return 0.0

        return self.total_cycle_time / self.completion_count

    # ─────────────────────────────────────────────────────

    def log_cycle(self, result: CycleResult):

        with open(self.log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=result.__dict__.keys())

            writer.writerow(asdict(result))

    # ─────────────────────────────────────────────────────

    def _ensure_log_file(self):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if os.path.exists(self.log_path):
            return
        with open(self.log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "cycle_id",
                    "station_id",
                    "start_timestamp",
                    "end_timestamp",
                    "cycle_time_sec",
                    "takt_time_s",
                    "is_complete",
                    "anomaly",
                    "completion_count",
                    "operator_id",
                ]
            )

    # ─────────────────────────────────────────────────────

    def debug(self):

        print("====================================")
        print(f"STATE              : {self.state.value}")
        print(f"CYCLE ID           : {self.cycle_id}")
        print(f"COMPLETION COUNT   : {self.completion_count}")
        print(f"FAILED COUNT       : {self.failed_count}")
        print(f"ANOMALY COUNT      : {self.anomaly_count}")
        print(f"LAST CYCLE         : {self.last_cycle_time:.2f}s")
        print(f"AVG CYCLE          : {self.average_cycle_time:.2f}s")
        print(f"TAKT TIME          : {self.takt_time_s:.2f}s")
        print("====================================")
