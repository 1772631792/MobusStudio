"""Serial reader living on a dedicated Qt thread."""

from __future__ import annotations

import threading
import time

import serial
from PySide6.QtCore import QThread, Signal


class SerialWorker(QThread):
    data_received = Signal(bytes)
    frame_received = Signal(bytes)
    status_changed = Signal(bool, str)
    error_occurred = Signal(str)

    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._serial: serial.Serial | None = None
        self._running = threading.Event()
        self._write_lock = threading.Lock()

    def run(self) -> None:
        try:
            self._serial = serial.Serial(timeout=0.02, write_timeout=1, **self.config)
        except (serial.SerialException, ValueError) as exc:
            self.error_occurred.emit(str(exc))
            self.status_changed.emit(False, "打开失败")
            return

        self._running.set()
        self.status_changed.emit(True, self.config["port"])
        buffer = bytearray()
        last_byte_at = 0.0
        baud = max(300, int(self.config["baudrate"]))
        # Modbus RTU requires >= 3.5 character times; keep a practical floor for USB adapters.
        frame_gap = max(0.004, 3.5 * 11 / baud)
        try:
            while self._running.is_set():
                waiting = self._serial.in_waiting
                chunk = self._serial.read(waiting or 1)
                now = time.monotonic()
                if chunk:
                    buffer.extend(chunk)
                    last_byte_at = now
                    self.data_received.emit(bytes(chunk))
                elif buffer and now - last_byte_at >= frame_gap:
                    self.frame_received.emit(bytes(buffer))
                    buffer.clear()
        except (serial.SerialException, OSError) as exc:
            self.error_occurred.emit(str(exc))
        finally:
            if buffer:
                self.frame_received.emit(bytes(buffer))
            if self._serial and self._serial.is_open:
                self._serial.close()
            self.status_changed.emit(False, "已断开")

    def send(self, data: bytes) -> None:
        if not self._serial or not self._serial.is_open:
            raise serial.SerialException("串口尚未连接")
        with self._write_lock:
            self._serial.write(data)

    def stop(self) -> None:
        self._running.clear()
        self.wait(1500)
