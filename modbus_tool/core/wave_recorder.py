"""Bounded waveform recording and non-blocking CSV export."""

from __future__ import annotations

import csv
import os
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class WaveRecorder:
    """Thread-safe, bounded collection of parsed waveform rows."""

    def __init__(self, max_rows: int = 200_000) -> None:
        self._rows: deque[dict[str, object]] = deque(maxlen=max_rows)
        self._channels: list[str] = []
        self._lock = threading.Lock()

    def append(self, timestamp: datetime, values: dict[str, float | int]) -> None:
        if not values:
            return
        with self._lock:
            for channel in values:
                if channel not in self._channels:
                    self._channels.append(channel)
            row: dict[str, object] = {
                "timestamp": timestamp.isoformat(sep=" ", timespec="milliseconds")
            }
            row.update(values)
            self._rows.append(row)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self._channels.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)

    def snapshot(self) -> tuple[list[str], list[dict[str, object]]]:
        with self._lock:
            channels = list(self._channels)
            rows = list(self._rows)
        return channels, [dict(row) for row in rows]


class CsvExportWorker(QThread):
    export_finished = Signal(str, int)
    export_failed = Signal(str)

    def __init__(
        self,
        target: str,
        recorder: WaveRecorder,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.target = target
        self.recorder = recorder

    def run(self) -> None:
        target = Path(self.target)
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            channels, rows = self.recorder.snapshot()
            target.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                fields = ["timestamp", *channels]
                writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            self.export_finished.emit(str(target), len(rows))
        except (OSError, csv.Error) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            self.export_failed.emit(str(exc))
