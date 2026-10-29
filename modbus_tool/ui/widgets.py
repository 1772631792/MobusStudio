"""Custom navigation and lightweight live plotting widgets."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QStyleOptionTab, QStylePainter, QTabBar, QWidget


class HorizontalTextSideTabBar(QTabBar):
    """East-side vertical tab stack whose labels remain horizontal."""

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802
        return QSize(82, 46)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QStylePainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            text = option.text
            option.text = ""
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option)
            painter.setPen(QColor("#0877bb") if index == self.currentIndex() else QColor("#68778a"))
            font = painter.font()
            font.setBold(index == self.currentIndex())
            painter.setFont(font)
            painter.drawText(self.tabRect(index), Qt.AlignmentFlag.AlignCenter, text)


class LivePlotWidget(QWidget):
    """Fast dependency-free multi-channel rolling waveform."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(260)
        self._series: dict[str, deque[float]] = {}
        self._colors: dict[str, QColor] = {}
        self._capacity = 500

    def add_sample(self, name: str, value: float, color: str) -> None:
        if name not in self._series:
            self._series[name] = deque(maxlen=self._capacity)
        self._colors[name] = QColor(color)
        self._series[name].append(float(value))
        self.update()

    def remove_series(self, name_prefix: str) -> None:
        for name in [key for key in self._series if key == name_prefix or key.startswith(f"{name_prefix}[")]:
            self._series.pop(name, None)
            self._colors.pop(name, None)
        self.update()

    def clear(self) -> None:
        self._series.clear()
        self._colors.clear()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = self.palette().color(self.backgroundRole())
        dark = background.lightness() < 100
        bg = QColor("#0a1019") if dark else QColor("#ffffff")
        grid = QColor("#233043") if dark else QColor("#e1e7ee")
        text = QColor("#8493a8") if dark else QColor("#738195")
        painter.fillRect(self.rect(), bg)

        plot = QRectF(58, 18, max(20, self.width() - 76), max(20, self.height() - 55))
        painter.setPen(QPen(grid, 1))
        for step in range(6):
            y = plot.top() + plot.height() * step / 5
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for step in range(11):
            x = plot.left() + plot.width() * step / 10
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        values = [value for series in self._series.values() for value in series]
        painter.setPen(text)
        if not values:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "绑定解析通道后，实时波形会显示在这里")
            return
        low, high = min(values), max(values)
        if low == high:
            padding = max(abs(low) * 0.1, 1.0)
            low -= padding
            high += padding
        else:
            padding = (high - low) * 0.08
            low -= padding
            high += padding

        painter.setFont(QFont("Cascadia Mono", 8))
        painter.drawText(QRectF(2, plot.top() - 8, 52, 18), Qt.AlignmentFlag.AlignRight, f"{high:.4g}")
        painter.drawText(QRectF(2, plot.bottom() - 9, 52, 18), Qt.AlignmentFlag.AlignRight, f"{low:.4g}")

        for name, series in self._series.items():
            if not series:
                continue
            samples = list(series)
            path = QPainterPath()
            divisor = max(1, len(samples) - 1)
            for index, value in enumerate(samples):
                x = plot.left() + plot.width() * index / divisor
                y = plot.bottom() - (value - low) / (high - low) * plot.height()
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(self._colors.get(name, QColor("#08b9a1")), 1.8))
            painter.drawPath(path)

        legend_x = plot.left()
        legend_y = self.height() - 21
        for name in self._series:
            color = self._colors.get(name, QColor("#08b9a1"))
            painter.fillRect(QRectF(legend_x, legend_y, 12, 3), color)
            painter.setPen(text)
            painter.drawText(QRectF(legend_x + 17, legend_y - 8, 105, 18), name)
            legend_x += 125
