"""Custom navigation and interactive live plotting widgets."""

from __future__ import annotations

from collections import deque
from itertools import islice
import math

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import QMenu, QStyle, QStyleOptionTab, QStylePainter, QTabBar, QWidget


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
    """Dependency-free oscilloscope plot with zooming, panning and channel visibility."""

    viewport_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._series: dict[str, deque[float]] = {}
        self._colors: dict[str, QColor] = {}
        self._hidden: set[str] = set()
        self._capacity = 5000
        self._base_window = 500
        self._x_zoom = 1.0
        self._y_zoom = 1.0
        self._history_offset = 0
        self._drag_origin: QPoint | None = None
        self._drag_offset = 0
        self._cursor_pos: QPoint | None = None
        self._legend_rows: list[tuple[QRectF, str]] = []
        # Store every sample, but cap visual repaint requests. At high polling rates
        # this keeps the UI responsive without reducing CSV/waveform data fidelity.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(33)
        self._repaint_timer.timeout.connect(self.update)

    def _schedule_repaint(self) -> None:
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def add_sample(self, name: str, value: float, color: str) -> None:
        self.register_series(name, color)
        number = float(value)
        if not math.isfinite(number):
            return
        self._series[name].append(number)
        self._schedule_repaint()

    def register_series(self, name: str, color: str) -> None:
        changed = False
        if name not in self._series:
            self._series[name] = deque(maxlen=self._capacity)
            changed = True
        new_color = QColor(color)
        if self._colors.get(name) != new_color:
            self._colors[name] = new_color
            changed = True
        if changed:
            self._schedule_repaint()

    def remove_series(self, name_prefix: str) -> None:
        for name in [key for key in self._series if key == name_prefix or key.startswith(f"{name_prefix}.") or key.startswith(f"{name_prefix}[")]:
            self._series.pop(name, None)
            self._colors.pop(name, None)
            self._hidden.discard(name)
        self._clamp_history_offset()
        self.update()

    def rename_series(self, old_name: str, new_name: str) -> None:
        """Rename a plotted channel without discarding its collected samples."""
        if old_name == new_name or old_name not in self._series:
            return
        self._series[new_name] = self._series.pop(old_name)
        if old_name in self._colors:
            self._colors[new_name] = self._colors.pop(old_name)
        if old_name in self._hidden:
            self._hidden.remove(old_name)
            self._hidden.add(new_name)
        self.update()

    def clear(self) -> None:
        for series in self._series.values():
            series.clear()
        self._history_offset = 0
        self.update()

    def clear_all_series(self) -> None:
        self._series.clear()
        self._colors.clear()
        self._hidden.clear()
        self._history_offset = 0
        self.update()

    def reset_view(self) -> None:
        self._x_zoom = 1.0
        self._y_zoom = 1.0
        self._history_offset = 0
        self._emit_view_state()
        self.update()

    def view_state(self) -> dict[str, float]:
        """Return the portable part of the current viewport state."""
        return {"x_zoom": self._x_zoom, "y_zoom": self._y_zoom}

    def restore_view_state(self, state: dict) -> None:
        """Restore zoom without restoring sample-dependent history position."""
        self._x_zoom = max(0.2, min(25.0, float(state.get("x_zoom", 1.0))))
        self._y_zoom = max(0.2, min(50.0, float(state.get("y_zoom", 1.0))))
        self._history_offset = 0
        self._emit_view_state()
        self.update()

    def show_all_channels(self) -> None:
        self._hidden.clear()
        self.update()

    def _window_size(self) -> int:
        return max(20, int(self._base_window / self._x_zoom))

    def _max_sample_count(self) -> int:
        return max((len(series) for series in self._series.values()), default=0)

    def _clamp_history_offset(self) -> None:
        maximum = max(0, self._max_sample_count() - self._window_size())
        self._history_offset = max(0, min(self._history_offset, maximum))

    def _emit_view_state(self) -> None:
        mode = "最新" if self._history_offset == 0 else f"回看 {self._history_offset} 点"
        self.viewport_changed.emit(f"横向 {self._x_zoom:.2f}×  ·  纵向 {self._y_zoom:.2f}×  ·  {mode}")

    def _plot_rect(self) -> QRectF:
        return QRectF(64, 18, max(20, self.width() - 82), max(20, self.height() - 58))

    def _visible_samples(self, series: deque[float]) -> list[float]:
        newest_first = list(
            islice(
                reversed(series),
                self._history_offset,
                self._history_offset + self._window_size(),
            )
        )
        newest_first.reverse()
        return newest_first

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._y_zoom = max(0.2, min(50.0, self._y_zoom * (1.18 ** steps)))
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._history_offset += int(-steps * max(1, self._window_size() / 12))
        else:
            old_window = self._window_size()
            self._x_zoom = max(0.2, min(25.0, self._x_zoom * (1.18 ** steps)))
            plot = self._plot_rect()
            ratio = max(0.0, min(1.0, (event.position().x() - plot.left()) / max(1.0, plot.width())))
            self._history_offset += int((old_window - self._window_size()) * (1.0 - ratio))
        self._clamp_history_offset()
        self._emit_view_state()
        self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, name in self._legend_rows:
                if rect.contains(event.position()):
                    if name in self._hidden:
                        self._hidden.remove(name)
                    else:
                        self._hidden.add(name)
                    self.update()
                    return
            if self._plot_rect().contains(event.position()):
                self._drag_origin = event.position().toPoint()
                self._drag_offset = self._history_offset
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._cursor_pos = event.position().toPoint()
        if self._drag_origin is not None:
            dx = event.position().x() - self._drag_origin.x()
            samples_per_pixel = self._window_size() / max(1.0, self._plot_rect().width())
            self._history_offset = self._drag_offset + int(dx * samples_per_pixel)
            self._clamp_history_offset()
            self._emit_view_state()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
            self.unsetCursor()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._cursor_pos = None
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_view()

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        reset = menu.addAction("自动适应 / 复位视图")
        latest = menu.addAction("回到最新数据")
        show_all = menu.addAction("显示全部通道")
        chosen = menu.exec(global_pos)
        if chosen == reset:
            self.reset_view()
        elif chosen == latest:
            self._history_offset = 0
            self._emit_view_state()
            self.update()
        elif chosen == show_all:
            self.show_all_channels()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = self.palette().color(self.backgroundRole())
        dark = background.lightness() < 100
        bg = QColor("#0a1019") if dark else QColor("#ffffff")
        grid = QColor("#233043") if dark else QColor("#e1e7ee")
        text = QColor("#8493a8") if dark else QColor("#738195")
        painter.fillRect(self.rect(), bg)

        plot = self._plot_rect()
        painter.setPen(QPen(grid, 1))
        for step in range(6):
            y = plot.top() + plot.height() * step / 5
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for step in range(11):
            x = plot.left() + plot.width() * step / 10
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        visible_data = {name: self._visible_samples(series) for name, series in self._series.items() if name not in self._hidden}
        values = [value for series in visible_data.values() for value in series]
        painter.setPen(text)
        if values:
            low, high = min(values), max(values)
            if low == high:
                padding = max(abs(low) * 0.1, 1.0)
                low -= padding
                high += padding
            else:
                padding = (high - low) * 0.08
                low -= padding
                high += padding
            center = (low + high) / 2
            half = (high - low) / (2 * self._y_zoom)
            low, high = center - half, center + half
            painter.setFont(QFont("Cascadia Mono", 8))
            painter.drawText(QRectF(2, plot.top() - 8, 57, 18), Qt.AlignmentFlag.AlignRight, f"{high:.4g}")
            painter.drawText(QRectF(2, plot.bottom() - 9, 57, 18), Qt.AlignmentFlag.AlignRight, f"{low:.4g}")
            for name, samples in visible_data.items():
                if not samples:
                    continue
                path = QPainterPath()
                divisor = max(1, len(samples) - 1)
                for index, value in enumerate(samples):
                    x = plot.left() + plot.width() * index / divisor
                    y = plot.bottom() - (value - low) / max(1e-15, high - low) * plot.height()
                    if index == 0:
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
                painter.setPen(QPen(self._colors.get(name, QColor("#08b9a1")), 1.7))
                painter.drawPath(path)
        else:
            message = "绑定解析成员后，实时波形会显示在这里" if self._series else "右键解析成员，选择“添加到波形调试”"
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, message)

        self._legend_rows.clear()
        if self._series:
            names = list(self._series)[:14]
            row_height = 21
            legend_width = min(220, max(145, max((len(name) for name in names), default=8) * 8 + 42))
            legend = QRectF(plot.right() - legend_width - 8, plot.top() + 8, legend_width, 12 + row_height * len(names))
            legend_bg = QColor(15, 24, 36, 220) if dark else QColor(255, 255, 255, 232)
            painter.setPen(QPen(grid, 1))
            painter.setBrush(legend_bg)
            painter.drawRoundedRect(legend, 6, 6)
            painter.setFont(QFont("Microsoft YaHei UI", 9))
            for index, name in enumerate(names):
                row = QRectF(legend.left() + 5, legend.top() + 6 + index * row_height, legend.width() - 10, row_height)
                self._legend_rows.append((row, name))
                color = self._colors.get(name, QColor("#08b9a1"))
                if name in self._hidden:
                    color.setAlpha(70)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(QRectF(row.left() + 5, row.top() + 5, 9, 9))
                painter.setPen(QColor(text.red(), text.green(), text.blue(), 100 if name in self._hidden else 255))
                label = name if len(name) <= 24 else name[:23] + "…"
                painter.drawText(QRectF(row.left() + 22, row.top() + 1, row.width() - 25, 18), label)

        if self._cursor_pos and plot.contains(QPointF(self._cursor_pos)):
            cursor_pen = QPen(QColor(80, 150, 190, 125), 1, Qt.PenStyle.DashLine)
            painter.setPen(cursor_pen)
            painter.drawLine(QPointF(self._cursor_pos.x(), plot.top()), QPointF(self._cursor_pos.x(), plot.bottom()))
            painter.drawLine(QPointF(plot.left(), self._cursor_pos.y()), QPointF(plot.right(), self._cursor_pos.y()))
