"""Modbus Studio application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from modbus_tool.ui.main_window import MainWindow
from modbus_tool.ui.i18n import EnglishUiFilter, translate_ui
from modbus_tool.ui.styles import LIGHT_STYLE
from modbus_tool.core.settings import Settings


def resource_path(relative: str) -> Path:
    """Resolve a resource both from source and from a PyInstaller one-file bundle."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / relative


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Modbus Studio")
    app.setOrganizationName("ModbusStudio")
    icon_path = resource_path("resources/modbus_studio.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    language = Settings().value("ui/language", "zh_CN")
    app.setFont(QFont("Segoe UI" if language == "en_US" else "Microsoft YaHei UI", 13))
    app.setStyle("Fusion")
    app.setStyleSheet(LIGHT_STYLE)

    window = MainWindow()
    if language == "en_US":
        app._english_ui_filter = EnglishUiFilter(app)
        app.installEventFilter(app._english_ui_filter)
        translate_ui(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
