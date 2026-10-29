"""Persistent application settings."""

from __future__ import annotations

from PySide6.QtCore import QSettings


class Settings:
    def __init__(self) -> None:
        self._store = QSettings("ModbusStudio", "ModbusStudio")

    def value(self, key: str, default=None):
        return self._store.value(key, default)

    def set(self, key: str, value) -> None:
        self._store.setValue(key, value)

    def serial_config(self) -> dict:
        return {
            "port": self.value("serial/port", ""),
            "baudrate": int(self.value("serial/baudrate", 9600)),
            "bytesize": int(self.value("serial/bytesize", 8)),
            "parity": self.value("serial/parity", "N"),
            "stopbits": float(self.value("serial/stopbits", 1)),
        }

    def save_serial_config(self, config: dict) -> None:
        for key, value in config.items():
            self.set(f"serial/{key}", value)
