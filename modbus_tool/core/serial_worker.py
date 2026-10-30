"""Serial reader living on a dedicated Qt thread."""

from __future__ import annotations

import threading
import time
import queue

import serial
from PySide6.QtCore import QThread, Signal


class SerialWorker(QThread):
    data_received = Signal(int)
    frame_received = Signal(bytes)
    status_changed = Signal(bool, str)
    error_occurred = Signal(str)
    MAX_RECEIVE_BUFFER = 65_536
    MAX_WRITES_PER_CYCLE = 16

    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.config = dict(config)
        self._serial: serial.Serial | None = None
        self._running = threading.Event()
        self._opened = threading.Event()
        self._write_queue: queue.Queue[bytes] = queue.Queue(maxsize=256)

    def run(self) -> None:
        try:
            serial_config = dict(self.config)
            port = serial_config.pop("port")
            self._serial = serial.serial_for_url(
                port, timeout=0.02, write_timeout=1, **serial_config
            )
        except (serial.SerialException, ValueError) as exc:
            self.error_occurred.emit(str(exc))
            self.status_changed.emit(False, "打开失败")
            return

        self._running.set()
        self._opened.set()
        self.status_changed.emit(True, self.config["port"])
        buffer = bytearray()
        pending_byte_count = 0
        last_count_emit = time.monotonic()
        last_byte_at = 0.0
        baud = max(300, int(self.config["baudrate"]))
        # Modbus RTU requires >= 3.5 character times; keep a practical floor for USB adapters.
        frame_gap = max(0.004, 3.5 * 11 / baud)
        try:
            while self._running.is_set():
                # Do not let a burst of queued writes starve receive processing.
                for _ in range(self.MAX_WRITES_PER_CYCLE):
                    try:
                        outgoing = self._write_queue.get_nowait()
                    except queue.Empty:
                        break
                    self._serial.write(outgoing)
                waiting = self._serial.in_waiting
                chunk = self._serial.read(waiting or 1)
                now = time.monotonic()
                if chunk:
                    buffer.extend(chunk)
                    pending_byte_count += len(chunk)
                    last_byte_at = now
                    # A disconnected/noisy line without RTU silence must not grow
                    # memory indefinitely. Preserve the bytes as a bounded raw frame.
                    if len(buffer) >= self.MAX_RECEIVE_BUFFER:
                        self.frame_received.emit(bytes(buffer))
                        buffer.clear()
                        last_byte_at = 0.0
                if pending_byte_count and now - last_count_emit >= 0.05:
                    self.data_received.emit(pending_byte_count)
                    pending_byte_count = 0
                    last_count_emit = now
                if not chunk and buffer and now - last_byte_at >= frame_gap:
                    if pending_byte_count:
                        self.data_received.emit(pending_byte_count)
                        pending_byte_count = 0
                    self.frame_received.emit(bytes(buffer))
                    buffer.clear()
        except (serial.SerialException, OSError) as exc:
            self.error_occurred.emit(str(exc))
        finally:
            self._opened.clear()
            if pending_byte_count:
                self.data_received.emit(pending_byte_count)
            if buffer:
                self.frame_received.emit(bytes(buffer))
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = None
            self.status_changed.emit(False, "已断开")

    def send(self, data: bytes) -> None:
        if not self._opened.is_set() or not self._running.is_set():
            raise serial.SerialException("串口尚未连接")
        try:
            self._write_queue.put_nowait(bytes(data))
        except queue.Full as exc:
            raise serial.SerialException("发送队列已满，请降低发送频率") from exc

    def stop(self) -> bool:
        self._running.clear()
        port = self._serial
        if port and port.is_open and hasattr(port, "cancel_read"):
            try:
                port.cancel_read()
            except (serial.SerialException, OSError):
                pass
        stopped = self.wait(2000)
        while True:
            try:
                self._write_queue.get_nowait()
            except queue.Empty:
                break
        return stopped
