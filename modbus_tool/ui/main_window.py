"""Main application window."""

from __future__ import annotations

from collections import deque
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import time

import serial
from serial.tools import list_ports
from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QColor, QCloseEvent, QFont, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTabBar,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modbus_tool.core.alarm import AlarmEvent, AlarmRule
from modbus_tool.core.modbus import (
    FUNCTION_NAMES,
    RtuFrame,
    append_crc,
    build_request,
    hex_text,
    parse_hex,
    write_response_status,
)
from modbus_tool.core.data_parser import encode_typed_values, modbus_data_area, parse_typed_values, type_size
from modbus_tool.core.project_config import PROJECT_SCHEMA, PROJECT_VERSION, validate_project
from modbus_tool.core.serial_worker import SerialWorker
from modbus_tool.core.settings import Settings
from modbus_tool.core.wave_recorder import CsvExportWorker, WaveRecorder
from modbus_tool.ui.styles import DARK_STYLE, LIGHT_STYLE
from modbus_tool.ui.alarm_dialog import AlarmRuleDialog
from modbus_tool.ui.widgets import GlassMenu, HorizontalTextSideTabBar, IntegerEdit, LivePlotWidget


FUNCTION_OPTIONS = [
    ("01 · 读线圈", 1),
    ("02 · 读离散输入", 2),
    ("03 · 读保持寄存器", 3),
    ("04 · 读输入寄存器", 4),
    ("05 · 写单个线圈", 5),
    ("06 · 写单个寄存器", 6),
    ("0F · 写多个线圈", 15),
    ("10 · 写多个寄存器", 16),
]


class FrameFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.address = -1
        self.function = -1
        self.crc_mode = 0
        self.keyword = ""

    def filterAcceptsRow(self, source_row, source_parent) -> bool:  # noqa: N802
        model = self.sourceModel()
        if model is None:
            return True
        addr = model.index(source_row, 3, source_parent).data(Qt.ItemDataRole.UserRole)
        func = model.index(source_row, 4, source_parent).data(Qt.ItemDataRole.UserRole)
        crc_ok = model.index(source_row, 6, source_parent).data(Qt.ItemDataRole.UserRole)
        row_text = " ".join(
            str(model.index(source_row, col, source_parent).data() or "")
            for col in range(model.columnCount())
        ).lower()
        return (
            (self.address < 0 or addr == self.address)
            and (self.function < 0 or func == self.function)
            and (self.crc_mode == 0 or (self.crc_mode == 1) == bool(crc_ok))
            and (not self.keyword or self.keyword in row_text)
        )


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.worker: SerialWorker | None = None
        self.frames: deque[RtuFrame] = deque()
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.good_frames = 0
        self.bad_frames = 0
        self._last_preview = b""
        self._last_write_frame = b""
        self._queued_write_frame: bytes | None = None
        self._active_write_frame = b""
        self.write_in_progress = False
        self._known_ports: tuple[tuple[str, str, str], ...] = ()
        self.parsers: list[dict] = []
        self._parser_sequence = 0
        self.parser_colors = ["#00bfa5", "#0879be", "#ef476f", "#f2a541", "#8b5cf6", "#35a853"]
        self.poll_active = False
        self.poll_waiting = False
        self.poll_success = 0
        self.poll_failures = 0
        self.poll_sent_at = 0.0
        self.poll_request = b""
        self.poll_sent_quantity = 0
        self.poll_tasks: list[dict] = []
        self.poll_task_sequence = 0
        self.poll_current_task: dict | None = None
        self.poll_manual_once = False
        self._poll_form_loading = False
        self.wave_recorder = WaveRecorder(max_rows=200_000)
        self.alarm_rules: list[AlarmRule] = []
        self.alarm_events: deque[AlarmEvent] = deque(maxlen=50_000)
        self._alarm_rule_rows: dict[str, int] = {}
        self.export_worker: CsvExportWorker | None = None
        self._raw_pending: deque[str] = deque(maxlen=5000)
        self._overview_dirty = False
        self._wave_count_dirty = False
        self._alarm_view_dirty = False

        self.setWindowTitle("Modbus Studio · RTU")
        self.resize(1360, 840)
        self.setMinimumSize(1050, 680)
        self._build_ui()
        self._restore_settings()
        self.refresh_ports(manual=True)
        self._update_builder()
        self.raw_flush_timer = QTimer(self)
        self.raw_flush_timer.setInterval(50)
        self.raw_flush_timer.timeout.connect(self._flush_raw_output)
        self.raw_flush_timer.start()
        self.overview_refresh_timer = QTimer(self)
        self.overview_refresh_timer.setInterval(250)
        self.overview_refresh_timer.timeout.connect(self._flush_overview)
        self.overview_refresh_timer.start()
        self.port_scan_timer = QTimer(self)
        self.port_scan_timer.setInterval(1000)
        self.port_scan_timer.timeout.connect(self.refresh_ports)
        self.port_scan_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 10, 12, 8)
        root_layout.setSpacing(9)
        root_layout.addWidget(self._build_top_bar())

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.addWidget(self._build_workspace())
        self.body_splitter.addWidget(self._build_right_tabs())
        self.body_splitter.setStretchFactor(0, 1)
        self.body_splitter.setSizes([820, 520])
        root_layout.addWidget(self.body_splitter, 1)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.status_label = QLabel("就绪 · 等待连接串口")
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(QLabel("RTU  |  v0.2.0"))
        self.setStatusBar(status)

    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="topBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 12, 10)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(40, 40)
        logo.setPixmap(QApplication.windowIcon().pixmap(40, 40))
        logo.setToolTip("Modbus Studio")
        layout.addWidget(logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(QLabel("MODBUS STUDIO", objectName="brand"))
        title_box.addWidget(QLabel("清爽、可靠的 RTU 调试台", objectName="muted"))
        layout.addLayout(title_box)
        layout.addStretch()

        import_project = QPushButton("导入 JSON")
        import_project.setToolTip("导入工程配置，不会自动连接串口或启动轮询")
        import_project.clicked.connect(self._import_project_json)
        layout.addWidget(import_project)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(150)
        self.port_combo.setToolTip("可用串口")
        layout.addWidget(self.port_combo)
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"])
        self.baud_combo.setCurrentText("9600")
        self.baud_combo.setFixedWidth(110)
        layout.addWidget(self.baud_combo)
        refresh = QPushButton("↻")
        refresh.setToolTip("刷新串口")
        refresh.setFixedWidth(38)
        refresh.clicked.connect(lambda _checked=False: self.refresh_ports(manual=True))
        layout.addWidget(refresh)
        self.connect_button = QPushButton("连接串口", objectName="primary")
        self.connect_button.setMinimumWidth(100)
        self.connect_button.clicked.connect(self.toggle_connection)
        layout.addWidget(self.connect_button)
        self.connection_dot = QLabel("●  未连接")
        self.connection_dot.setStyleSheet("color:#738096;padding:0 6px")
        layout.addWidget(self.connection_dot)
        return bar

    def _build_workspace(self) -> QWidget:
        panel = QFrame(objectName="panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QWidget()
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(14, 9, 10, 9)
        tools.setSpacing(6)
        self.view_tabs = QTabBar(objectName="mainViewTabs")
        self.view_tabs.setDrawBase(False)
        self.view_tabs.setExpanding(False)
        self.view_tabs.setUsesScrollButtons(False)
        for label in ("帧列表", "原始数据", "数据概览", "实时波形", "参数报警"):
            self.view_tabs.addTab(label)
        self.view_tabs.currentChanged.connect(self._switch_view)
        tools.addWidget(self.view_tabs)
        tools.addStretch()
        pause = QPushButton("暂停")
        pause.setCheckable(True)
        pause.toggled.connect(self._toggle_display_pause)
        self.pause_button = pause
        tools.addWidget(pause)
        clear = QPushButton("清空", objectName="danger")
        clear.clicked.connect(self.clear_data)
        tools.addWidget(clear)
        layout.addWidget(toolbar)

        self.views = QStackedWidget()
        self.views.addWidget(self._build_frame_view())
        self.views.addWidget(self._build_raw_view())
        self.views.addWidget(self._build_overview())
        self.views.addWidget(self._build_waveform_view())
        self.views.addWidget(self._build_alarm_view())
        layout.addWidget(self.views, 1)
        return panel

    def _build_frame_view(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 10)
        page_layout.setSpacing(0)

        self.frame_splitter = QSplitter(Qt.Orientation.Vertical)
        splitter = self.frame_splitter
        splitter.setChildrenCollapsible(False)
        self.frame_model = QStandardItemModel(0, 8, self)
        self.frame_model.setHorizontalHeaderLabels(
            ["ID", "时间", "方向", "站号", "功能", "摘要", "CRC", "原始帧"]
        )
        self.frame_filter = FrameFilter(self)
        self.frame_filter.setSourceModel(self.frame_model)
        self.frame_table = QTableView()
        self.frame_table.setModel(self.frame_filter)
        self.frame_table.setAlternatingRowColors(True)
        self.frame_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.frame_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.frame_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.frame_table.verticalHeader().hide()
        frame_font = QFont(self.frame_table.font())
        frame_font.setPointSize(max(14, frame_font.pointSize() + 1))
        self.frame_table.setFont(frame_font)
        self.frame_table.verticalHeader().setDefaultSectionSize(38)
        header_font = QFont(self.frame_table.horizontalHeader().font())
        header_font.setPointSize(max(13, header_font.pointSize() + 1))
        self.frame_table.horizontalHeader().setFont(header_font)
        self.frame_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.frame_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.frame_table.setColumnWidth(0, 58)
        self.frame_table.setColumnWidth(1, 105)
        self.frame_table.setColumnWidth(2, 58)
        self.frame_table.setColumnWidth(3, 62)
        self.frame_table.setColumnWidth(4, 135)
        self.frame_table.setColumnWidth(6, 70)
        self.frame_table.selectionModel().selectionChanged.connect(self._show_selected_frame)
        splitter.addWidget(self.frame_table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(14, 8, 14, 10)
        detail_layout.addWidget(QLabel("帧详情", objectName="sectionTitle"))
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumBlockCount(300)
        self.detail_text.setMinimumHeight(210)
        detail_font = QFont("Cascadia Mono", 14)
        detail_font.setStyleHint(QFont.StyleHint.Monospace)
        self.detail_text.setFont(detail_font)
        self.detail_text.setPlaceholderText("选择一条帧，查看地址、功能、负载及 CRC 信息")
        detail_layout.addWidget(self.detail_text)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([430, 260])
        page_layout.addWidget(splitter, 1)
        return page

    def _build_raw_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 12, 14, 14)
        hint = QLabel("时间戳 + 方向 + HEX · 接收数据按 RTU 静默间隔自动分帧", objectName="muted")
        layout.addWidget(hint)
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setMaximumBlockCount(5000)
        self.raw_text.setPlaceholderText("连接串口后，接收的 RTU 数据将显示在这里…")
        layout.addWidget(self.raw_text)
        return widget

    def _metric_card(self, title: str, color: str) -> tuple[QFrame, QLabel]:
        card = QFrame(objectName="statusCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.addWidget(QLabel(title, objectName="muted"))
        number = QLabel("0", objectName="metric")
        number.setStyleSheet(f"color:{color}")
        box.addWidget(number)
        return card, number

    def _build_overview(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(18, 18, 18, 18)
        cards = QGridLayout()
        entries = [
            ("接收字节", "#39d9c1", "rx_metric"),
            ("有效帧", "#77e29a", "good_metric"),
            ("CRC 异常", "#ff7185", "bad_metric"),
            ("发送字节", "#67a9ff", "tx_metric"),
        ]
        for index, (title, color, attr) in enumerate(entries):
            card, label = self._metric_card(title, color)
            setattr(self, attr, label)
            cards.addWidget(card, index // 2, index % 2)
        layout.addLayout(cards)
        layout.addSpacing(6)
        layout.addWidget(QLabel("字节分布", objectName="sectionTitle"))
        self.byte_overview = QPlainTextEdit()
        self.byte_overview.setReadOnly(True)
        self.byte_overview.setPlaceholderText("收到数据后，这里会显示常见字节及占比。")
        layout.addWidget(self.byte_overview, 1)
        return widget

    def _build_waveform_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 12, 14, 14)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(QLabel("实时数据波形", objectName="sectionTitle"))
        title_box.addWidget(QLabel("单窗口 / 上下分屏 · 在绘图区右键选择解析成员", objectName="muted"))
        header.addLayout(title_box)
        header.addStretch()
        self.wave_record_count = QLabel("已记录 0 行", objectName="muted")
        header.addWidget(self.wave_record_count)
        self.wave_tabs = QTabWidget()
        self.live_plots = [LivePlotWidget(), LivePlotWidget()]
        self.active_wave_plot_index = 0
        for index, plot in enumerate(self.live_plots):
            plot.viewport_changed.connect(
                lambda text, view=index: self._wave_viewport_changed(view, text)
            )
            plot.binding_menu_requested.connect(
                lambda menu, view=index: self._populate_wave_binding_menu(menu, view)
            )
            plot.activated.connect(lambda view=index: self._activate_wave_plot(view))
        self.single_wave_page = QWidget()
        self.single_wave_layout = QVBoxLayout(self.single_wave_page)
        self.single_wave_layout.setContentsMargins(0, 0, 0, 0)
        self.single_wave_layout.addWidget(self.live_plots[0])
        self.split_wave_view = QSplitter(Qt.Orientation.Vertical)
        self.split_wave_view.setChildrenCollapsible(False)
        self.split_wave_view.addWidget(self.live_plots[1])
        self.wave_tabs.addTab(self.single_wave_page, "单窗口")
        self.wave_tabs.addTab(self.split_wave_view, "上下分屏")
        # Keep the original attribute for old project files and third-party code.
        self.live_plot = self.live_plots[0]
        self.wave_tabs.currentChanged.connect(self._wave_layout_changed)
        reset_plot = QPushButton("适应视图")
        reset_plot.setToolTip("恢复自动量程并回到最新数据；也可在绘图区双击")
        reset_plot.clicked.connect(lambda: self._active_live_plot().reset_view())
        self.export_csv_button = QPushButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._export_wave_csv)
        header.addWidget(self.export_csv_button)
        clear_plot = QPushButton("清空波形")
        clear_plot.clicked.connect(self._clear_wave_data)
        header.addWidget(clear_plot)
        header.insertWidget(header.count() - 2, reset_plot)
        layout.addLayout(header)
        layout.addWidget(self.wave_tabs, 1)
        interaction = QHBoxLayout()
        self.wave_view_state = QLabel("横向 1.00×  ·  纵向 1.00×  ·  最新", objectName="muted")
        interaction.addWidget(self.wave_view_state)
        interaction.addStretch()
        interaction.addWidget(QLabel("右键：绑定成员  ·  暂停后悬停：查看点值  ·  滚轮：缩放  ·  拖拽：回看", objectName="muted"))
        layout.addLayout(interaction)
        return widget

    def _active_live_plot(self) -> LivePlotWidget:
        return self.live_plots[self.active_wave_plot_index]

    def _wave_viewport_changed(self, view: int, text: str) -> None:
        if self.active_wave_plot_index == view:
            self.wave_view_state.setText(text)

    def _activate_wave_plot(self, view: int) -> None:
        self.active_wave_plot_index = view
        self.wave_view_state.setText(
            f"波形 {'AB'[view]} · {self.live_plots[view]._view_state_text()}"
        )

    def _wave_layout_changed(self, mode: int) -> None:
        """Move plot A between a full-size page and the upper split pane."""
        if mode == 0:
            self.single_wave_layout.addWidget(self.live_plots[0])
            self._activate_wave_plot(0)
            return
        self.split_wave_view.insertWidget(0, self.live_plots[0])
        self.split_wave_view.setSizes([1, 1])
        self._activate_wave_plot(0)

    def _populate_wave_binding_menu(self, menu: QMenu, view: int) -> None:
        """Build a channel chooser directly inside a waveform context menu."""
        self._activate_wave_plot(view)
        for config in self.parsers:
            indices = sorted(config["enabled"])
            if not indices:
                continue
            target_menu = menu
            if config["count"] > 1:
                target_menu = menu.addMenu(config["name"])
            for index in indices:
                label = self._series_name(config, index)
                if config["count"] > 1:
                    label = config["members"][index]
                action = target_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(index in config["plot_views"][view])
                action.toggled.connect(
                    lambda checked, cfg=config, member=index, target=view: self._set_wave_binding(
                        cfg, member, target, checked
                    )
                )

    def _clear_wave_data(self) -> None:
        for plot in self.live_plots:
            plot.clear()
        self.wave_recorder.clear()
        self._wave_count_dirty = False
        self.wave_record_count.setText("已记录 0 行")
        self.status_label.setText("波形数据已清空，通道绑定保持不变")

    def _export_wave_csv(self) -> None:
        if len(self.wave_recorder) == 0:
            QMessageBox.information(self, "没有波形数据", "请先绑定解析通道并接收数据。")
            return
        default_name = datetime.now().strftime("modbus_wave_%Y%m%d_%H%M%S.csv")
        target, _selected_filter = QFileDialog.getSaveFileName(
            self, "导出波形 CSV", default_name, "CSV 文件 (*.csv)"
        )
        if not target:
            return
        if not target.lower().endswith(".csv"):
            target += ".csv"
        self.export_csv_button.setEnabled(False)
        self.export_csv_button.setText("正在导出…")
        self.export_worker = CsvExportWorker(target, self.wave_recorder, self)
        self.export_worker.export_finished.connect(self._csv_export_finished)
        self.export_worker.export_failed.connect(self._csv_export_failed)
        self.export_worker.start()

    def _csv_export_finished(self, path: str, row_count: int) -> None:
        self.export_csv_button.setEnabled(True)
        self.export_csv_button.setText("导出 CSV")
        self.status_label.setText(f"已导出 {row_count:,} 行波形数据：{path}")
        if self.export_worker:
            self.export_worker.deleteLater()
            self.export_worker = None

    def _csv_export_failed(self, message: str) -> None:
        self.export_csv_button.setEnabled(True)
        self.export_csv_button.setText("导出 CSV")
        QMessageBox.warning(self, "CSV 导出失败", message)
        if self.export_worker:
            self.export_worker.deleteLater()
            self.export_worker = None

    def _build_alarm_view(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(QLabel("参数报警", objectName="sectionTitle"))
        title_box.addWidget(QLabel("右键添加检测通道 · 只记录报警与恢复状态变化", objectName="muted"))
        header.addLayout(title_box)
        header.addStretch()
        self.alarm_summary = QLabel("0 条规则 · 0 条活动报警 · 0 条事件", objectName="muted")
        header.addWidget(self.alarm_summary)
        self.alarm_add_button = QPushButton("添加报警")
        self.alarm_add_button.clicked.connect(self._open_alarm_add_menu)
        header.addWidget(self.alarm_add_button)
        edit_button = QPushButton("编辑规则")
        edit_button.clicked.connect(self._edit_selected_alarm_rule)
        header.addWidget(edit_button)
        delete_button = QPushButton("删除规则", objectName="danger")
        delete_button.clicked.connect(self._delete_selected_alarm_rule)
        header.addWidget(delete_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Vertical)
        rules_panel = QWidget()
        rules_layout = QVBoxLayout(rules_panel)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        rules_layout.addWidget(QLabel("检测规则", objectName="sectionTitle"))
        self.alarm_rule_model = QStandardItemModel(0, 5, self)
        self.alarm_rule_model.setHorizontalHeaderLabels(
            ["检测通道", "报警条件", "当前值", "状态", "启用"]
        )
        self.alarm_rule_table = QTableView()
        self.alarm_rule_table.setModel(self.alarm_rule_model)
        self.alarm_rule_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alarm_rule_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.alarm_rule_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alarm_rule_table.setAlternatingRowColors(True)
        self.alarm_rule_table.verticalHeader().hide()
        self.alarm_rule_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.alarm_rule_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((2, 120), (3, 90), (4, 70)):
            self.alarm_rule_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.alarm_rule_table.setColumnWidth(column, width)
        self.alarm_rule_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.alarm_rule_table.customContextMenuRequested.connect(self._show_alarm_context_menu)
        self.alarm_rule_table.doubleClicked.connect(self._edit_selected_alarm_rule)
        rules_layout.addWidget(self.alarm_rule_table)
        splitter.addWidget(rules_panel)

        events_panel = QWidget()
        events_layout = QVBoxLayout(events_panel)
        events_layout.setContentsMargins(0, 0, 0, 0)
        events_header = QHBoxLayout()
        events_header.addWidget(QLabel("报警事件", objectName="sectionTitle"))
        events_header.addStretch()
        export_events = QPushButton("导出事件")
        export_events.clicked.connect(self._export_alarm_events)
        events_header.addWidget(export_events)
        clear_events = QPushButton("清空事件", objectName="danger")
        clear_events.clicked.connect(self._clear_alarm_events)
        events_header.addWidget(clear_events)
        events_layout.addLayout(events_header)
        self.alarm_event_model = QStandardItemModel(0, 5, self)
        self.alarm_event_model.setHorizontalHeaderLabels(
            ["发生时间", "状态", "检测通道", "检测值", "报警条件"]
        )
        self.alarm_event_table = QTableView()
        self.alarm_event_table.setModel(self.alarm_event_model)
        self.alarm_event_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alarm_event_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.alarm_event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alarm_event_table.setAlternatingRowColors(True)
        self.alarm_event_table.verticalHeader().hide()
        self.alarm_event_table.setColumnWidth(0, 175)
        self.alarm_event_table.setColumnWidth(1, 80)
        self.alarm_event_table.setColumnWidth(3, 120)
        self.alarm_event_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.alarm_event_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.alarm_event_table.selectionModel().selectionChanged.connect(self._show_alarm_event_detail)
        events_layout.addWidget(self.alarm_event_table, 1)
        self.alarm_event_detail = QPlainTextEdit()
        self.alarm_event_detail.setReadOnly(True)
        self.alarm_event_detail.setMaximumHeight(118)
        self.alarm_event_detail.setPlaceholderText("选择一条报警事件，查看发生时间、检测值和原始 RTU 帧")
        events_layout.addWidget(self.alarm_event_detail)
        splitter.addWidget(events_panel)
        splitter.setSizes([250, 430])
        layout.addWidget(splitter, 1)
        return page

    def _alarm_channels(self) -> list[tuple[dict, int, str]]:
        channels = []
        for config in self.parsers:
            for index in sorted(config["enabled"]):
                channels.append((config, index, self._series_name(config, index)))
        return channels

    def _populate_alarm_channel_menu(self, menu: QMenu) -> None:
        channels = self._alarm_channels()
        if not channels:
            action = menu.addAction("请先添加解析通道")
            action.setEnabled(False)
            return
        grouped: dict[int, QMenu] = {}
        for config, index, channel in channels:
            target = menu
            if config["count"] > 1:
                key = id(config)
                if key not in grouped:
                    grouped[key] = menu.addMenu(config["name"])
                target = grouped[key]
            label = config["members"][index] if config["count"] > 1 else channel
            action = target.addAction(label)
            action.triggered.connect(
                lambda _checked=False, selected=channel: self._edit_alarm_rule(selected)
            )

    def _open_alarm_add_menu(self) -> None:
        menu = GlassMenu(self)
        self._populate_alarm_channel_menu(menu)
        menu.exec(self.alarm_add_button.mapToGlobal(self.alarm_add_button.rect().bottomLeft()))

    def _show_alarm_context_menu(self, position) -> None:
        menu = GlassMenu(self)
        add_menu = menu.addMenu("添加检测通道")
        self._populate_alarm_channel_menu(add_menu)
        rule = self._selected_alarm_rule()
        menu.addSeparator()
        edit_action = menu.addAction("编辑所选规则")
        toggle_action = menu.addAction("停用规则" if rule and rule.enabled else "启用规则")
        delete_action = menu.addAction("删除所选规则")
        for action in (edit_action, toggle_action, delete_action):
            action.setEnabled(rule is not None)
        chosen = menu.exec(self.alarm_rule_table.viewport().mapToGlobal(position))
        if chosen == edit_action:
            self._edit_selected_alarm_rule()
        elif chosen == toggle_action and rule is not None:
            rule.enabled = not rule.enabled
            rule.active = False
            rule.active_since = None
            self._rebuild_alarm_rule_table()
        elif chosen == delete_action:
            self._delete_selected_alarm_rule()

    def _selected_alarm_rule(self) -> AlarmRule | None:
        rows = self.alarm_rule_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.alarm_rule_model.item(rows[0].row(), 0)
        rule_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((rule for rule in self.alarm_rules if rule.rule_id == rule_id), None)

    def _edit_alarm_rule(self, channel: str, rule: AlarmRule | None = None) -> None:
        dialog = AlarmRuleDialog(channel, rule, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_rule is None:
            return
        if rule is None:
            self.alarm_rules.append(dialog.result_rule)
        else:
            index = self.alarm_rules.index(rule)
            self.alarm_rules[index] = dialog.result_rule
        self._rebuild_alarm_rule_table()

    def _edit_selected_alarm_rule(self, *_args) -> None:
        rule = self._selected_alarm_rule()
        if rule is not None:
            self._edit_alarm_rule(rule.channel, rule)

    def _delete_selected_alarm_rule(self) -> None:
        rule = self._selected_alarm_rule()
        if rule is None:
            return
        self.alarm_rules.remove(rule)
        self._rebuild_alarm_rule_table()

    def _rebuild_alarm_rule_table(self) -> None:
        self.alarm_rule_model.removeRows(0, self.alarm_rule_model.rowCount())
        self._alarm_rule_rows.clear()
        for row, rule in enumerate(self.alarm_rules):
            current = "—" if rule.current_value is None else f"{rule.current_value:.10g}"
            state = "已停用" if not rule.enabled else (
                "报警" if rule.active else ("正常" if rule.current_value is not None else "等待数据")
            )
            items = [
                QStandardItem(rule.channel), QStandardItem(rule.condition_text),
                QStandardItem(current), QStandardItem(state),
                QStandardItem("是" if rule.enabled else "否"),
            ]
            items[0].setData(rule.rule_id, Qt.ItemDataRole.UserRole)
            color = QColor("#e34d59" if rule.active else ("#209567" if rule.enabled else "#8a96a6"))
            for item in items:
                item.setForeground(color)
            self.alarm_rule_model.appendRow(items)
            self._alarm_rule_rows[rule.rule_id] = row
        self._refresh_alarm_summary()

    def _flush_alarm_rule_values(self) -> None:
        self._alarm_view_dirty = False
        for rule in self.alarm_rules:
            row = self._alarm_rule_rows.get(rule.rule_id)
            if row is None:
                continue
            current = "—" if rule.current_value is None else f"{rule.current_value:.10g}"
            state = "已停用" if not rule.enabled else (
                "报警" if rule.active else ("正常" if rule.current_value is not None else "等待数据")
            )
            color = QColor("#e34d59" if rule.active else ("#209567" if rule.enabled else "#8a96a6"))
            for column, text in ((2, current), (3, state), (4, "是" if rule.enabled else "否")):
                item = self.alarm_rule_model.item(row, column)
                if item and item.text() != text:
                    item.setText(text)
            for column in range(self.alarm_rule_model.columnCount()):
                item = self.alarm_rule_model.item(row, column)
                if item:
                    item.setForeground(color)

    def _evaluate_alarms(self, channel: str, value: float | int, frame: RtuFrame) -> None:
        raw_frame = hex_text(frame.raw)
        for rule in self.alarm_rules:
            if rule.channel != channel:
                continue
            event = rule.evaluate(float(value), frame.timestamp, raw_frame)
            self._alarm_view_dirty = True
            if event is not None:
                self._record_alarm_event(event)

    def _record_alarm_event(self, event: AlarmEvent) -> None:
        self.alarm_events.append(event)
        state = "报警" if event.state == "active" else "恢复"
        items = [
            QStandardItem(event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]),
            QStandardItem(state), QStandardItem(event.channel),
            QStandardItem(f"{event.value:.10g}"), QStandardItem(event.condition),
        ]
        items[0].setData(event, Qt.ItemDataRole.UserRole)
        color = QColor("#e34d59" if event.state == "active" else "#209567")
        for item in items:
            item.setForeground(color)
        self.alarm_event_model.insertRow(0, items)
        while self.alarm_event_model.rowCount() > 10_000:
            self.alarm_event_model.removeRow(self.alarm_event_model.rowCount() - 1)
        self._refresh_alarm_summary()

    def _show_alarm_event_detail(self) -> None:
        rows = self.alarm_event_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.alarm_event_model.item(rows[0].row(), 0)
        event = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(event, AlarmEvent):
            return
        state = "进入报警" if event.state == "active" else "恢复正常"
        duration = (
            f"\n持续时间  {event.duration_ms:.1f} ms"
            if event.duration_ms is not None else ""
        )
        self.alarm_event_detail.setPlainText(
            f"发生时间  {event.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n"
            f"事件状态  {state}\n检测通道  {event.channel}\n"
            f"检测值    {event.value:.12g}\n报警条件  {event.condition}"
            f"{duration}\n原始帧    {event.raw_frame}"
        )

    def _refresh_alarm_summary(self) -> None:
        if not hasattr(self, "alarm_summary"):
            return
        active = sum(1 for rule in self.alarm_rules if rule.enabled and rule.active)
        self.alarm_summary.setText(
            f"{len(self.alarm_rules)} 条规则 · {active} 条活动报警 · {len(self.alarm_events)} 条事件"
        )

    def _clear_alarm_events(self) -> None:
        self.alarm_events.clear()
        self.alarm_event_model.removeRows(0, self.alarm_event_model.rowCount())
        self.alarm_event_detail.clear()
        self._refresh_alarm_summary()
        self.status_label.setText("报警事件记录已清空，检测规则保持不变")

    def _export_alarm_events(self) -> None:
        if not self.alarm_events:
            QMessageBox.information(self, "没有报警事件", "当前没有可导出的报警事件。")
            return
        default_name = datetime.now().strftime("modbus_alarm_%Y%m%d_%H%M%S.csv")
        target, _ = QFileDialog.getSaveFileName(self, "导出报警事件", default_name, "CSV 文件 (*.csv)")
        if not target:
            return
        if not target.lower().endswith(".csv"):
            target += ".csv"
        try:
            with Path(target).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["timestamp", "state", "channel", "value", "condition", "duration_ms", "raw_frame"])
                for event in self.alarm_events:
                    writer.writerow([
                        event.timestamp.isoformat(sep=" ", timespec="milliseconds"),
                        event.state, event.channel, event.value, event.condition,
                        "" if event.duration_ms is None else f"{event.duration_ms:.3f}",
                        event.raw_frame,
                    ])
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.status_label.setText(f"报警事件已导出：{target}")

    def _build_right_tabs(self) -> QWidget:
        self.right_tabs = QTabWidget()
        # The tab stack is vertical on the right, but labels are custom-painted horizontally.
        self.right_tabs.setTabBar(HorizontalTextSideTabBar())
        self.right_tabs.setTabPosition(QTabWidget.TabPosition.East)
        self.right_tabs.setMinimumWidth(500)
        self.right_tabs.setMaximumWidth(650)
        self.poll_page = self._build_poll_tab()
        self.right_tabs.addTab(self.poll_page, "轮询")
        self.parse_page = self._build_parse_tab()
        self.right_tabs.addTab(self.parse_page, "解析")
        self.write_page = self._build_write_tab()
        self.right_tabs.addTab(self.write_page, "写入")
        self.right_tabs.addTab(self._build_send_tab(), "构帧")
        self.right_tabs.addTab(self._build_settings_tab(), "设置")
        return self.right_tabs

    def _tab_page(self) -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setMinimumWidth(300)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        scroll.setWidget(page)
        return scroll, layout

    @staticmethod
    def _field_with_unit(field: QWidget, unit: str) -> QWidget:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(field, 1)
        unit_label = QLabel(unit, objectName="muted")
        unit_label.setMinimumWidth(26)
        row.addWidget(unit_label)
        return wrapper

    def _build_poll_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("连续读取轮询", objectName="sectionTitle"))
        layout.addWidget(QLabel("多个轮询项按“请求 → 等待响应 → 下一项”自动调度", objectName="muted"))
        self.poll_task_tree = QTreeWidget()
        self.poll_task_tree.setColumnCount(3)
        self.poll_task_tree.setHeaderLabels(["ID", "名称", "范围"])
        task_font = QFont(self.poll_task_tree.font())
        task_font.setPointSize(max(14, task_font.pointSize() + 1))
        self.poll_task_tree.setFont(task_font)
        task_header_font = QFont(self.poll_task_tree.header().font())
        task_header_font.setPointSize(max(13, task_header_font.pointSize() + 1))
        self.poll_task_tree.header().setFont(task_header_font)
        self.poll_task_tree.setStyleSheet("QTreeWidget::item { padding: 6px 4px; }")
        self.poll_task_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.poll_task_tree.header().resizeSection(0, 55)
        self.poll_task_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.poll_task_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.poll_task_tree.header().resizeSection(2, 120)
        self.poll_task_tree.setRootIsDecorated(False)
        self.poll_task_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.poll_task_tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.poll_task_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.poll_task_tree.setMinimumHeight(112)
        self.poll_task_tree.setMaximumHeight(175)
        self.poll_task_tree.currentItemChanged.connect(self._poll_task_selected)
        layout.addWidget(self.poll_task_tree)
        task_actions = QHBoxLayout()
        self.poll_add_button = QPushButton("新增")
        self.poll_add_button.clicked.connect(self._add_poll_task)
        self.poll_copy_button = QPushButton("复制")
        self.poll_copy_button.clicked.connect(self._copy_poll_task)
        self.poll_delete_button = QPushButton("删除", objectName="danger")
        self.poll_delete_button.clicked.connect(self._delete_poll_task)
        self.poll_once_button = QPushButton("立即一次")
        self.poll_once_button.clicked.connect(self._run_selected_poll_once)
        for button in (self.poll_add_button, self.poll_copy_button, self.poll_delete_button, self.poll_once_button):
            task_actions.addWidget(button)
        layout.addLayout(task_actions)
        form = QFormLayout()
        self.poll_name = QLineEdit()
        self.poll_name.setPlaceholderText("例如：温度区")
        self.poll_enabled = QCheckBox("启用此轮询项")
        self.poll_enabled.setChecked(True)
        self.poll_address = IntegerEdit()
        self.poll_address.setRange(1, 247)
        self.poll_address.setValue(1)
        self.poll_function = QComboBox()
        for text, code in FUNCTION_OPTIONS[:4]:
            self.poll_function.addItem(text, code)
        self.poll_function.setCurrentIndex(2)
        self.poll_function.currentIndexChanged.connect(self._poll_function_changed)
        self.poll_start = IntegerEdit()
        self.poll_start.setRange(0, 65535)
        self.poll_start.setValue(0)
        self.poll_quantity = IntegerEdit()
        self.poll_quantity.setRange(1, 125)
        self.poll_quantity.setValue(10)
        self.poll_interval = IntegerEdit()
        self.poll_interval.setRange(0, 60000)
        self.poll_interval.setValue(50)
        self.poll_interval.setToolTip("收到完整响应后，等待此时间再发送下一请求")
        self.poll_timeout = IntegerEdit()
        self.poll_timeout.setRange(50, 10000)
        self.poll_timeout.setValue(500)
        self.poll_range_mode = QComboBox()
        self.poll_range_mode.addItem("自动扩展", "auto")
        self.poll_range_mode.addItem("手动固定", "manual")
        form.addRow("任务名称", self.poll_name)
        form.addRow("状态", self.poll_enabled)
        form.addRow("从站地址", self.poll_address)
        form.addRow("读取功能", self.poll_function)
        form.addRow("范围模式", self.poll_range_mode)
        form.addRow("起始寄存器", self.poll_start)
        form.addRow("寄存器数量", self.poll_quantity)
        form.addRow("响应后间隔", self._field_with_unit(self.poll_interval, "ms"))
        form.addRow("响应超时", self._field_with_unit(self.poll_timeout, "ms"))
        layout.addLayout(form)

        self.poll_button = QPushButton("开始安全轮询", objectName="primary")
        self.poll_button.clicked.connect(self._toggle_polling)
        layout.addWidget(self.poll_button)

        poll_status_card = QFrame(objectName="pollStatusCard")
        status_grid = QGridLayout(poll_status_card)
        status_grid.setContentsMargins(12, 9, 12, 9)
        status_grid.setHorizontalSpacing(8)
        status_grid.setVerticalSpacing(6)
        status_grid.addWidget(QLabel("状态", objectName="muted"), 0, 0)
        self.poll_state_value = QLabel("尚未开始", objectName="pollValue")
        self.poll_state_value.setFixedWidth(72)
        status_grid.addWidget(self.poll_state_value, 0, 1)
        status_grid.addWidget(QLabel("成功", objectName="muted"), 0, 2)
        self.poll_success_value = QLabel("0", objectName="pollValue")
        self.poll_success_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.poll_success_value.setFixedWidth(64)
        status_grid.addWidget(self.poll_success_value, 0, 3)
        status_grid.addWidget(QLabel("异常", objectName="muted"), 0, 4)
        self.poll_failure_value = QLabel("0", objectName="pollValue")
        self.poll_failure_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.poll_failure_value.setFixedWidth(64)
        status_grid.addWidget(self.poll_failure_value, 0, 5)
        status_grid.addWidget(QLabel("范围", objectName="muted"), 1, 0)
        self.poll_range_value = QLabel("—", objectName="pollValue")
        self.poll_range_value.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        status_grid.addWidget(self.poll_range_value, 1, 1, 1, 3)
        status_grid.addWidget(QLabel("响应", objectName="muted"), 1, 4)
        self.poll_response_value = QLabel("—", objectName="pollValue")
        self.poll_response_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.poll_response_value.setFixedWidth(96)
        status_grid.addWidget(self.poll_response_value, 1, 5)
        for column, width in enumerate((36, 80, 36, 64, 84, 96)):
            status_grid.setColumnMinimumWidth(column, width)
        status_grid.setColumnStretch(1, 1)
        layout.addStretch()
        layout.addWidget(poll_status_card)

        self.poll_next_timer = QTimer(self)
        self.poll_next_timer.setSingleShot(True)
        self.poll_next_timer.timeout.connect(self._send_poll_request)
        self.poll_timeout_timer = QTimer(self)
        self.poll_timeout_timer.setSingleShot(True)
        self.poll_timeout_timer.timeout.connect(self._poll_timed_out)
        for signal in (
            self.poll_name.textChanged,
            self.poll_enabled.toggled,
            self.poll_address.valueChanged,
            self.poll_function.currentIndexChanged,
            self.poll_range_mode.currentIndexChanged,
            self.poll_start.valueChanged,
            self.poll_quantity.valueChanged,
            self.poll_interval.valueChanged,
            self.poll_timeout.valueChanged,
        ):
            signal.connect(self._poll_form_changed)
        self._create_poll_task(select=True)
        return page

    def _build_parse_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("数据解析通道", objectName="sectionTitle"))
        form = QFormLayout()
        self.parser_form = form
        self.parser_name = QLineEdit()
        self.parser_name.setPlaceholderText("例：温度")
        self.parser_poll_task = QComboBox()
        self._refresh_parser_poll_sources()
        self.parser_type = QComboBox()
        self.parser_type.addItems(
            ["uint8", "int8", "uint16", "int16", "uint32", "int32", "float32", "float64"]
        )
        self.parser_order = QComboBox()
        self.parser_order.addItem("大端 / ABCD", "big")
        self.parser_order.addItem("小端 / DCBA", "little")
        self.parser_order.addItem("字交换 / CDAB", "word_swap")
        self.parser_order.addItem("字节交换 / BADC", "byte_swap")
        self.parser_offset = IntegerEdit()
        self.parser_offset.setRange(0, 65535)
        self.parser_offset.setValue(self.poll_start.value())
        self.parser_offset.setReadOnly(True)
        self.parser_byte_lane = QComboBox()
        self.parser_byte_lane.addItem("高字节", 0)
        self.parser_byte_lane.addItem("低字节", 1)
        self.parser_byte_lane.setEnabled(False)
        self.parser_count = IntegerEdit()
        self.parser_count.setRange(1, 128)
        self.parser_scale = QLineEdit("1.0")
        self.parser_scale.setPlaceholderText("倍率，例如 0.1")
        form.addRow("通道名称", self.parser_name)
        form.addRow("数据块 ID", self.parser_poll_task)
        form.addRow("数据类型", self.parser_type)
        form.addRow("解析数量", self.parser_count)
        form.addRow("字节序", self.parser_order)
        form.addRow("8位字节位置", self.parser_byte_lane)
        form.addRow("缩放倍率", self.parser_scale)
        layout.addLayout(form)
        self.parser_offset_hint = QLabel()
        self.parser_offset_hint.setWordWrap(True)
        self.parser_offset_hint.setStyleSheet("background:#e7f4f8;color:#187195;padding:8px;border-radius:6px")
        layout.addWidget(self.parser_offset_hint)
        self.parser_type.currentTextChanged.connect(self._update_parser_offset_hint)
        self.parser_poll_task.currentIndexChanged.connect(self._parser_poll_source_changed)
        self.parser_offset.valueChanged.connect(self._update_parser_offset_hint)
        self.parser_count.valueChanged.connect(self._update_parser_offset_hint)
        self.parser_byte_lane.currentIndexChanged.connect(self._update_parser_offset_hint)
        self._last_poll_start = self.poll_start.value()
        self._update_parser_offset_hint()
        add_parser = QPushButton("添加解析结构", objectName="primary")
        add_parser.clicked.connect(self._add_parser)
        layout.addWidget(add_parser)

        self.parser_tree = QTreeWidget()
        self.parser_tree.setColumnCount(3)
        self.parser_tree.setHeaderLabels(["变量 / 结构", "地址 · 类型", "当前值"])
        self.parser_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.parser_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.parser_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.parser_tree.customContextMenuRequested.connect(self._show_parser_context_menu)
        self.parser_tree.itemDoubleClicked.connect(self._rename_parser_from_double_click)
        self.parser_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.parser_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.parser_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.parser_tree.header().resizeSection(0, 108)
        self.parser_tree.header().resizeSection(1, 165)
        self.parser_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.parser_tree.setMinimumHeight(220)
        self.parser_tree.setAlternatingRowColors(True)
        self.parser_tree.setStyleSheet("QTreeWidget { font-size: 16px; } QTreeWidget::item { min-height: 32px; }")
        layout.addWidget(self.parser_tree, 1)
        remove_parser = QPushButton("删除所选变量 / 结构 / 成员", objectName="danger")
        remove_parser.clicked.connect(self._remove_parser)
        parser_actions = QHBoxLayout()
        parser_actions.addWidget(remove_parser, 1)
        clear_parsers = QPushButton("清空所有通道", objectName="danger")
        clear_parsers.setToolTip("删除全部解析变量、波形绑定和报警规则")
        clear_parsers.clicked.connect(self._clear_all_parsers)
        parser_actions.addWidget(clear_parsers)
        layout.addLayout(parser_actions)
        return page

    def _build_write_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("类型化数据写入", objectName="sectionTitle"))
        note = QLabel("直接填写数值；轮询运行时会在响应间隙安全写入，完成后自动恢复")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

        form = QFormLayout()
        self.write_form = form
        self.write_address = IntegerEdit()
        self.write_address.setRange(1, 247)
        self.write_address.setValue(1)
        self.write_target = QComboBox()
        self.write_target.addItem("保持寄存器", "register")
        self.write_target.addItem("线圈", "coil")
        self.write_start = IntegerEdit()
        self.write_start.setRange(0, 65535)
        self.write_start_label = QLabel("起始寄存器")
        self.write_type = QComboBox()
        self.write_type.addItems(["uint16", "int16", "uint32", "int32", "float32", "float64"])
        self.write_order = QComboBox()
        self.write_order.addItem("大端 / ABCD", "big")
        self.write_order.addItem("小端 / DCBA", "little")
        self.write_order.addItem("字交换 / CDAB", "word_swap")
        self.write_order.addItem("字节交换 / BADC", "byte_swap")
        self.write_values = QPlainTextEdit()
        self.write_values.setMaximumHeight(86)
        self.write_values.setPlaceholderText("例：12.5, -3.2, 100（支持逗号、空格或换行）")
        form.addRow("从站地址", self.write_address)
        form.addRow("写入目标", self.write_target)
        form.addRow(self.write_start_label, self.write_start)
        form.addRow("数据类型", self.write_type)
        form.addRow("字节序", self.write_order)
        form.addRow("写入数据", self.write_values)
        layout.addLayout(form)

        preview_card = QFrame(objectName="composerCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.addWidget(QLabel("自动编码预览", objectName="muted"))
        self.write_function_label = QLabel("等待数据")
        self.write_function_label.setStyleSheet("color:#0879be;font-weight:700")
        preview_layout.addWidget(self.write_function_label)
        self.write_register_preview = QLabel("寄存器：—")
        self.write_register_preview.setWordWrap(True)
        self.write_register_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.write_register_preview)
        self.write_frame_preview = QPlainTextEdit()
        self.write_frame_preview.setReadOnly(True)
        self.write_frame_preview.setMaximumHeight(82)
        self.write_frame_preview.setFont(QFont("Cascadia Mono", 10))
        preview_layout.addWidget(self.write_frame_preview)
        self.write_hint = QLabel("请输入写入数据")
        self.write_hint.setWordWrap(True)
        preview_layout.addWidget(self.write_hint)
        layout.addWidget(preview_card)

        self.write_confirm = QCheckBox("发送前再次确认")
        self.write_confirm.setChecked(True)
        layout.addWidget(self.write_confirm)
        layout.addStretch()
        self.write_button = QPushButton("发送写入命令", objectName="primary")
        self.write_button.clicked.connect(self._send_typed_write)
        self.write_button.setEnabled(False)
        layout.addWidget(self.write_button)
        self.write_timeout_timer = QTimer(self)
        self.write_timeout_timer.setSingleShot(True)
        self.write_timeout_timer.timeout.connect(self._write_timed_out)

        self.write_target.currentIndexChanged.connect(self._write_target_changed)
        self.write_address.valueChanged.connect(self._update_write_preview)
        self.write_start.valueChanged.connect(self._update_write_preview)
        self.write_type.currentTextChanged.connect(self._update_write_preview)
        self.write_order.currentIndexChanged.connect(self._update_write_preview)
        self.write_values.textChanged.connect(self._update_write_preview)
        self._write_target_changed()
        return page

    @staticmethod
    def _split_write_tokens(text: str) -> list[str]:
        normalized = text.replace("，", ",").replace("；", ",").replace(";", ",")
        return [token for token in normalized.replace("\n", ",").replace("\t", ",").replace(" ", ",").split(",") if token]

    def _write_target_changed(self, *_args) -> None:
        register_mode = self.write_target.currentData() == "register"
        self.write_start_label.setText("起始寄存器" if register_mode else "起始线圈")
        self.write_form.setRowVisible(self.write_type, register_mode)
        self.write_form.setRowVisible(self.write_order, register_mode)
        self.write_values.setPlaceholderText(
            "例：12.5, -3.2, 100（支持逗号、空格或换行）"
            if register_mode else
            "例：1, 0, true, false, on, off"
        )
        self._update_write_preview()

    def _parse_coil_values(self, tokens: list[str]) -> list[int]:
        mapping = {"1": 1, "true": 1, "on": 1, "yes": 1, "开": 1,
                   "0": 0, "false": 0, "off": 0, "no": 0, "关": 0}
        values = []
        for token in tokens:
            key = token.strip().lower()
            if key not in mapping:
                raise ValueError(f"无法识别线圈值“{token}”，请使用0/1或true/false")
            values.append(mapping[key])
        return values

    def _update_write_preview(self, *_args) -> None:
        self._last_write_frame = b""
        tokens = self._split_write_tokens(self.write_values.toPlainText())
        if not tokens:
            self.write_function_label.setText("等待数据")
            self.write_register_preview.setText("寄存器：—")
            self.write_frame_preview.clear()
            self.write_hint.setText("请输入写入数据")
            self.write_button.setEnabled(False)
            return
        try:
            if self.write_target.currentData() == "coil":
                values = self._parse_coil_values(tokens)
                function = 5 if len(values) == 1 else 15
                frame = build_request(
                    self.write_address.value(), function, self.write_start.value(), values=values
                )
                self.write_register_preview.setText("线圈：" + ", ".join(map(str, values)))
                detail = f"{len(values)} 个线圈"
            else:
                data_type = self.write_type.currentText()
                integer_type = data_type.startswith(("uint", "int"))
                values = [int(token, 0) if integer_type else float(token) for token in tokens]
                if not integer_type and any(not math.isfinite(value) for value in values):
                    raise ValueError("浮点写入值不能是 NaN 或 Infinity")
                encoded = encode_typed_values(values, data_type, self.write_order.currentData())
                if len(encoded) % 2:
                    encoded += b"\x00"
                registers = [int.from_bytes(encoded[pos : pos + 2], "big") for pos in range(0, len(encoded), 2)]
                function = 6 if len(registers) == 1 else 16
                frame = build_request(
                    self.write_address.value(), function, self.write_start.value(), values=registers
                )
                register_text = ", ".join(f"0x{value:04X}" for value in registers)
                self.write_register_preview.setText(f"寄存器：{register_text}")
                detail = f"{len(values)} 个 {data_type} → {len(registers)} 个寄存器"
            function_name = FUNCTION_NAMES[function]
            self.write_function_label.setText(f"自动选择 0x{function:02X} · {function_name}")
            self.write_frame_preview.setPlainText(hex_text(frame))
            self.write_hint.setText(f"{detail} · 完整帧 {len(frame)} bytes")
            self.write_hint.setStyleSheet("color:#209567")
            self._last_write_frame = frame
            self.write_button.setEnabled(
                not self.write_in_progress and self._queued_write_frame is None
            )
        except (ValueError, OverflowError) as exc:
            self.write_function_label.setText("数据无效")
            self.write_register_preview.setText("寄存器：—")
            self.write_frame_preview.clear()
            self.write_hint.setText(str(exc))
            self.write_hint.setStyleSheet("color:#df5262")
            self.write_button.setEnabled(False)

    def _send_typed_write(self) -> None:
        if not self._last_write_frame:
            return
        if not self.worker or not self.worker.isRunning():
            QMessageBox.information(self, "尚未连接", "请先连接串口，再发送写入命令。")
            return
        if self.write_in_progress or self._queued_write_frame is not None:
            self.status_label.setText("已有写入命令正在等待或执行")
            return
        if self.write_confirm.isChecked():
            answer = QMessageBox.question(
                self,
                "确认写入设备",
                f"即将写入从站 {self.write_address.value()}，起始地址 {self.write_start.value()}。\n"
                "写入会修改设备数据，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        frame = bytes(self._last_write_frame)
        if self.poll_active:
            self._queued_write_frame = frame
            self.poll_next_timer.stop()
            self.write_button.setEnabled(False)
            self.write_button.setText("等待轮询间隙…")
            self._set_label_text(self.poll_state_value, "等待写入")
            self.status_label.setText("写入已排队 · 当前轮询响应结束后自动发送")
            if not self.poll_waiting:
                self._send_queued_write()
            return
        try:
            self.worker.send(frame)
        except serial.SerialException as exc:
            self._on_serial_error(str(exc))
            return
        self.tx_bytes += len(frame)
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self._on_frame(frame, "TX")
        self.status_label.setText("写入命令已发送，等待从站响应")

    def _send_queued_write(self) -> None:
        if self._queued_write_frame is None or self.write_in_progress:
            return
        if not self.worker or not self.worker.isRunning():
            self._finish_interleaved_write("写入取消：串口已断开")
            return
        frame = self._queued_write_frame
        self._queued_write_frame = None
        self._active_write_frame = frame
        self.write_in_progress = True
        self.write_button.setText("写入中…")
        self._set_label_text(self.poll_state_value, "写入中")
        try:
            self.worker.send(frame)
        except serial.SerialException as exc:
            self._finish_interleaved_write(f"写入发送失败：{exc}")
            return
        self.tx_bytes += len(frame)
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self._on_frame(frame, "TX")
        write_timeout = max((task["timeout_ms"] for task in self.poll_tasks), default=500)
        self.write_timeout_timer.start(write_timeout)
        self.status_label.setText("轮询已暂缓 · 写入命令已发送，等待从站响应")

    def _handle_write_response(self, frame: RtuFrame) -> bool:
        if not self.write_in_progress:
            return False
        result = write_response_status(self._active_write_frame, frame.raw)
        if result is None:
            return False
        if result == "success":
            self._finish_interleaved_write("写入成功 · 已自动恢复安全轮询")
        else:
            self._finish_interleaved_write(
                f"写入被从站拒绝（{frame.summary}）· 已自动恢复安全轮询"
            )
        return True

    def _write_timed_out(self) -> None:
        if self.write_in_progress:
            self._finish_interleaved_write("写入响应超时 · 已自动恢复安全轮询")

    def _finish_interleaved_write(self, message: str) -> None:
        self._reset_write_transaction()
        self.status_label.setText(message)
        if self.poll_active and self.worker and self.worker.isRunning():
            self._set_label_text(self.poll_state_value, "运行中")
            self._schedule_next_poll_task()
        else:
            self._set_label_text(self.poll_state_value, "已停止")

    def _reset_write_transaction(self) -> None:
        if hasattr(self, "write_timeout_timer"):
            self.write_timeout_timer.stop()
        self.write_in_progress = False
        self._active_write_frame = b""
        self._queued_write_frame = None
        if hasattr(self, "write_button"):
            self.write_button.setText("发送写入命令")
            self.write_button.setEnabled(bool(self._last_write_frame))

    def _build_send_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("智能构帧", objectName="sectionTitle"))
        layout.addWidget(QLabel("勾选自动项，只填写真正关心的数据", objectName="muted"))

        self.auto_address = QCheckBox("自动加入从站地址")
        self.auto_address.setChecked(True)
        self.auto_function = QCheckBox("自动加入功能码与长度")
        self.auto_function.setChecked(True)
        self.auto_crc = QCheckBox("自动计算 CRC16")
        self.auto_crc.setChecked(True)
        for check in (self.auto_address, self.auto_function, self.auto_crc):
            check.toggled.connect(self._builder_mode_changed)
            layout.addWidget(check)

        form = QFormLayout()
        self.builder_address = IntegerEdit()
        self.builder_address.setRange(1, 247)
        self.builder_address.setValue(1)
        self.builder_function = QComboBox()
        for text, code in FUNCTION_OPTIONS:
            self.builder_function.addItem(text, code)
        self.builder_start = IntegerEdit()
        self.builder_start.setRange(0, 65535)
        self.builder_start.setDisplayIntegerBase(10)
        self.builder_quantity = IntegerEdit()
        self.builder_quantity.setRange(1, 2000)
        self.builder_quantity.setValue(1)
        self.builder_values = QLineEdit()
        self.builder_values.setPlaceholderText("例：100, 200, 300  或  1,0,1")
        form.addRow("从站地址", self.builder_address)
        form.addRow("功能", self.builder_function)
        form.addRow("起始地址", self.builder_start)
        form.addRow("读取数量", self.builder_quantity)
        form.addRow("写入数据", self.builder_values)
        layout.addLayout(form)
        for widget in (
            self.builder_address,
            self.builder_function,
            self.builder_start,
            self.builder_quantity,
        ):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._update_builder)
            else:
                widget.valueChanged.connect(self._update_builder)
        self.builder_values.textChanged.connect(self._update_builder)

        self.manual_hex = QLineEdit()
        self.manual_hex.setPlaceholderText("手动模式：输入 HEX 负载或完整帧")
        self.manual_hex.textChanged.connect(self._update_builder)
        self.manual_hex.hide()
        layout.addWidget(self.manual_hex)

        preview_card = QFrame(objectName="composerCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.addWidget(QLabel("发送预览", objectName="muted"))
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(78)
        self.preview_text.setFont(QFont("Cascadia Mono", 10))
        preview_layout.addWidget(self.preview_text)
        self.preview_hint = QLabel("")
        self.preview_hint.setWordWrap(True)
        preview_layout.addWidget(self.preview_hint)
        layout.addWidget(preview_card)

        repeat_row = QHBoxLayout()
        self.repeat_send = QCheckBox("循环发送")
        self.repeat_interval = IntegerEdit()
        self.repeat_interval.setRange(20, 600000)
        self.repeat_interval.setValue(1000)
        repeat_row.addWidget(self.repeat_send)
        repeat_row.addWidget(self.repeat_interval, 1)
        repeat_row.addWidget(QLabel("ms", objectName="muted"))
        layout.addLayout(repeat_row)
        layout.addStretch()
        self.send_button = QPushButton("发送帧", objectName="primary")
        self.send_button.clicked.connect(self.send_frame)
        layout.addWidget(self.send_button)
        self.send_timer = QTimer(self)
        self.send_timer.timeout.connect(self.send_frame)
        self.repeat_send.toggled.connect(self._toggle_repeat)
        self.repeat_interval.valueChanged.connect(
            lambda value: self.send_timer.setInterval(value) if self.send_timer.isActive() else None
        )
        return page

    def _add_serial_settings(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("串口参数", objectName="sectionTitle"))
        layout.addWidget(QLabel("RTU 暂不包含 TCP 设置", objectName="muted"))
        form = QFormLayout()
        self.data_bits = QComboBox()
        self.data_bits.addItems(["8", "7", "6", "5"])
        self.parity = QComboBox()
        self.parity.addItem("无校验 (None)", "N")
        self.parity.addItem("偶校验 (Even)", "E")
        self.parity.addItem("奇校验 (Odd)", "O")
        self.parity.addItem("标记 (Mark)", "M")
        self.parity.addItem("空格 (Space)", "S")
        self.stop_bits = QComboBox()
        self.stop_bits.addItem("1", 1)
        self.stop_bits.addItem("1.5", 1.5)
        self.stop_bits.addItem("2", 2)
        form.addRow("数据位", self.data_bits)
        form.addRow("校验位", self.parity)
        form.addRow("停止位", self.stop_bits)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(QLabel("推荐组合", objectName="sectionTitle"))
        for text, baud, parity in (
            ("9600 · 8N1", 9600, "N"),
            ("19200 · 8E1", 19200, "E"),
            ("115200 · 8N1", 115200, "N"),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, b=baud, p=parity: self._apply_preset(b, p))
            layout.addWidget(button)
        layout.addStretch()
        note = QLabel("串口参数在连接时锁定；修改后重新连接即可生效。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

    def _build_settings_tab(self) -> QWidget:
        tabs = QTabWidget(objectName="settingsTabs")
        tabs.setTabPosition(QTabWidget.TabPosition.North)
        serial_page, serial_layout = self._tab_page()
        self._add_serial_settings(serial_layout)
        tabs.addTab(serial_page, "串口参数")

        page, layout = self._tab_page()
        layout.addWidget(QLabel("外观设置", objectName="sectionTitle"))
        layout.addWidget(QLabel("默认使用接近 VOFA+ 的清爽明亮配色", objectName="muted"))
        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("界面语言"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.addItem("English（英文显示）", "en_US")
        language_index = self.language_combo.findData(self.settings.value("ui/language", "zh_CN"))
        self.language_combo.setCurrentIndex(max(0, language_index))
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        language_row.addWidget(self.language_combo, 1)
        layout.addLayout(language_row)
        language_hint = QLabel("切换后重启软件生效，帧列表和帧详情也会同步切换。", objectName="muted")
        language_hint.setWordWrap(True)
        layout.addWidget(language_hint)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("界面主题"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("VOFA+ 明亮（默认）", "light")
        self.theme_combo.addItem("深色模式", "dark")
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        theme_row.addWidget(self.theme_combo, 1)
        layout.addLayout(theme_row)
        layout.addSpacing(12)
        description = QLabel(
            "主题切换立即生效并自动保存。下次启动时会恢复上次选择。"
        )
        description.setWordWrap(True)
        description.setObjectName("muted")
        layout.addWidget(description)
        layout.addSpacing(18)
        layout.addWidget(QLabel("数据显示", objectName="sectionTitle"))
        display_hint = QLabel("只改变界面呈现和内存缓存，不影响串口接收与解析。", objectName="muted")
        display_hint.setWordWrap(True)
        layout.addWidget(display_hint)
        self.show_timestamp = QCheckBox("帧列表显示毫秒时间戳")
        self.show_timestamp.setChecked(True)
        self.uppercase_hex = QCheckBox("HEX 使用大写字母")
        self.uppercase_hex.setChecked(True)
        layout.addWidget(self.show_timestamp)
        layout.addWidget(self.uppercase_hex)
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("帧缓存上限"))
        self.keep_limit = IntegerEdit()
        self.keep_limit.setRange(100, 100000)
        self.keep_limit.setValue(5000)
        limit_row.addWidget(self.keep_limit, 1)
        limit_row.addWidget(QLabel("帧", objectName="muted"))
        layout.addLayout(limit_row)
        layout.addSpacing(18)
        layout.addWidget(QLabel("工程 JSON", objectName="sectionTitle"))
        project_hint = QLabel(
            "导出当前串口、轮询、解析、波形绑定、报警规则和界面配置。JSON 可直接交给 AI 修改，再一键导入还原。"
        )
        project_hint.setWordWrap(True)
        project_hint.setObjectName("muted")
        layout.addWidget(project_hint)
        project_actions = QHBoxLayout()
        export_project = QPushButton("导出配置 JSON", objectName="primary")
        export_project.clicked.connect(self._export_project_json)
        project_actions.addWidget(export_project)
        layout.addLayout(project_actions)
        safety_hint = QLabel("导入只恢复配置，不会自动连接串口、开始轮询或发送数据。")
        safety_hint.setWordWrap(True)
        safety_hint.setStyleSheet("color:#209567")
        layout.addWidget(safety_hint)
        layout.addStretch()
        tabs.addTab(page, "软件设置")
        return tabs

    def _language_changed(self) -> None:
        language = self.language_combo.currentData() or "zh_CN"
        self.settings.set("ui/language", language)
        if language == "en_US":
            title = "Language"
            message = "Language saved. Restart Modbus Studio to use the English interface."
        else:
            title = "界面语言"
            message = "语言设置已保存，重新启动 Modbus Studio 后切换为简体中文。"
        QMessageBox.information(self, title, message)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value, field: str) -> None:
        index = combo.findData(value)
        if index < 0:
            raise ValueError(f"{field} 的值不受当前软件支持：{value!r}")
        combo.setCurrentIndex(index)

    def _project_document(self) -> dict:
        """Capture configuration only; live samples and running state are excluded."""
        view_names = ("frames", "raw", "overview", "waveform", "alarms")
        tab_names = ("poll", "parse", "write", "builder", "settings")
        parser_documents = []
        for config in self.parsers:
            parser_documents.append({
                "name": config["name"],
                "type": config["type"],
                "order": config["order"],
                "source": config["source"],
                "poll_id": config.get("poll_id", self.poll_tasks[0]["id"]),
                "address": config["address"],
                "function": config["function"],
                "register": config["register"],
                "offset": config["offset"],
                "byte_lane": config.get("byte_lane", 0),
                "count": config["count"],
                "scale": config["scale"],
                "color": config["color"],
                "members": list(config["members"]),
                "enabled": sorted(config["enabled"]),
                "plots": sorted(config["plots"]),
                "plot_views": [sorted(view) for view in config["plot_views"]],
            })
        return {
            "schema": PROJECT_SCHEMA,
            "version": PROJECT_VERSION,
            "application": "Modbus Studio RTU",
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ai_help": {
                "purpose": "可由人或 AI 编辑的 Modbus Studio 工程配置",
                "safety": "导入不会自动连接串口、启动轮询、循环发送或写入设备",
                "poll_blocks": "polls 是独立轮询数据块；parser.poll_id 指定解析来源的数据块 ID",
                "parser_addressing": "source=data 时 register 是绝对寄存器地址；count 是数值个数，不是寄存器个数",
                "byte_orders": {"big": "ABCD", "little": "DCBA", "word_swap": "CDAB", "byte_swap": "BADC"},
            },
            "serial": self._serial_config(),
            "poll": {
                "address": self.poll_tasks[0]["address"],
                "function": self.poll_tasks[0]["function"],
                "start": self.poll_tasks[0]["start"],
                "quantity": self.poll_tasks[0]["quantity"],
                "interval_ms": self.poll_tasks[0]["interval_ms"],
                "timeout_ms": self.poll_tasks[0]["timeout_ms"],
            },
            "polls": [{
                key: task[key] for key in (
                    "id", "name", "enabled", "address", "function", "range_mode",
                    "start", "quantity", "interval_ms", "timeout_ms",
                )
            } for task in self.poll_tasks],
            "parsers": parser_documents,
            "alarms": [rule.to_document() for rule in self.alarm_rules],
            "parser_sequence": self._parser_sequence,
            "receive": {
                # Retained in the file format so older project JSON remains compatible.
                "address_filter": 0,
                "function_filter": -1,
                "crc_filter": 0,
                "keyword": "",
                "show_timestamp": self.show_timestamp.isChecked(),
                "uppercase_hex": self.uppercase_hex.isChecked(),
                # Retained for compatibility with older project files.
                "auto_scroll": False,
                "keep_limit": self.keep_limit.value(),
            },
            "write": {
                "address": self.write_address.value(),
                "target": self.write_target.currentData(),
                "start": self.write_start.value(),
                "type": self.write_type.currentText(),
                "order": self.write_order.currentData(),
                "values": self.write_values.toPlainText(),
                "confirm": self.write_confirm.isChecked(),
            },
            "builder": {
                "auto_address": self.auto_address.isChecked(),
                "auto_function": self.auto_function.isChecked(),
                "auto_crc": self.auto_crc.isChecked(),
                "address": self.builder_address.value(),
                "function": self.builder_function.currentData(),
                "start": self.builder_start.value(),
                "quantity": self.builder_quantity.value(),
                "values": self.builder_values.text(),
                "manual_hex": self.manual_hex.text(),
                "repeat_interval_ms": self.repeat_interval.value(),
            },
            "ui": {
                "theme": self.theme_combo.currentData() or "light",
                "main_view": view_names[self.views.currentIndex()],
                "right_tab": tab_names[self.right_tabs.currentIndex()],
                "window_size": {"width": self.width(), "height": self.height()},
                "splitter_sizes": self.body_splitter.sizes(),
                "wave_view": self.live_plot.view_state(),
                "wave_views": [plot.view_state() for plot in self.live_plots],
                "wave_layout": self.wave_tabs.currentIndex(),
                "active_wave_view": self.active_wave_plot_index,
            },
        }

    def _export_project_json(self) -> None:
        default_name = datetime.now().strftime("modbus_project_%Y%m%d_%H%M%S.json")
        target, _selected_filter = QFileDialog.getSaveFileName(
            self, "导出工程配置", default_name, "JSON 配置 (*.json)"
        )
        if not target:
            return
        if not target.lower().endswith(".json"):
            target += ".json"
        try:
            document = validate_project(self._project_document())
            destination = Path(target)
            temporary = destination.with_name(destination.name + ".tmp")
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "JSON 导出失败", str(exc))
            return
        self.status_label.setText(f"工程配置已导出：{target}")

    def _import_project_json(self) -> None:
        source, _selected_filter = QFileDialog.getOpenFileName(
            self, "导入工程配置", "", "JSON 配置 (*.json);;所有文件 (*)"
        )
        if not source:
            return
        try:
            path = Path(source)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("配置文件超过 2 MiB，已拒绝导入")
            document = validate_project(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            QMessageBox.warning(
                self,
                "JSON 配置有问题",
                f"配置尚未应用，当前界面保持不变。\n\n{exc}\n\n可把此 JSON 和错误信息发给 AI 修复。",
            )
            return

        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "导入配置",
                "导入前需要断开当前串口，但不会自动重新连接。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.disconnect_serial()
            if self.worker and self.worker.isRunning():
                QMessageBox.warning(self, "无法导入", "串口线程尚未退出，请稍后重试。")
                return
        try:
            self._apply_project_document(document)
        except (ValueError, TypeError, KeyError) as exc:
            # Validation is intentionally completed first, so this only catches a
            # newer UI mismatch. It should never occur for files exported here.
            QMessageBox.warning(self, "配置应用失败", f"当前版本无法应用此配置：{exc}")
            return
        self.status_label.setText(f"已还原工程配置：{source} · 串口未连接、轮询未启动")

    def _apply_project_document(self, document: dict) -> None:
        self._stop_polling(silent=True)
        self.repeat_send.setChecked(False)
        serial_config = document["serial"]
        port = serial_config["port"]
        port_index = self.port_combo.findData(port)
        if port and port_index < 0:
            self.port_combo.addItem(f"{port}  ·  配置中的设备（当前未发现）", port)
            port_index = self.port_combo.count() - 1
        if port_index >= 0:
            self.port_combo.setCurrentIndex(port_index)
        self.baud_combo.setCurrentText(str(serial_config["baudrate"]))
        self.data_bits.setCurrentText(str(serial_config["bytesize"]))
        self._set_combo_data(self.parity, serial_config["parity"], "serial.parity")
        self._set_combo_data(self.stop_bits, serial_config["stopbits"], "serial.stopbits")
        self.settings.save_serial_config(serial_config)

        for row in range(self.frame_model.rowCount() - 1, -1, -1):
            item = self.frame_model.item(row, 2)
            slot = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
            if isinstance(slot, str) and ":" in slot:
                self.frame_model.removeRow(row)
        saved_polls = document.get("polls")
        if not saved_polls:
            legacy = document["poll"]
            saved_polls = [{
                "id": "P1", "name": "轮询 1", "enabled": True, "range_mode": "auto", **legacy,
            }]
        self.poll_tasks = []
        self.poll_task_sequence = 0
        for saved in saved_polls:
            task = dict(saved)
            task.update(success=0, failures=0, last_response="—", next_due=0.0)
            self.poll_tasks.append(task)
            if task["id"].startswith("P") and task["id"][1:].isdigit():
                self.poll_task_sequence = max(self.poll_task_sequence, int(task["id"][1:]))
        self._refresh_poll_task_tree(self.poll_tasks[0]["id"])
        self._refresh_parser_poll_sources()
        self._ensure_poll_rows()

        receive = document["receive"]
        self.show_timestamp.setChecked(receive["show_timestamp"])
        self.uppercase_hex.setChecked(receive["uppercase_hex"])
        self.keep_limit.setValue(receive["keep_limit"])

        self._clear_all_parsers()
        self._parser_sequence = document.get("parser_sequence", len(document["parsers"]))
        for saved in document["parsers"]:
            config = dict(saved)
            config["poll_id"] = saved.get("poll_id", self.poll_tasks[0]["id"])
            config["members"] = list(saved["members"])
            config["enabled"] = set(saved["enabled"])
            config["plots"] = set(saved["plots"])
            config["plot_views"] = [
                set(view) for view in saved.get("plot_views", [saved["plots"], []])
            ]
            self._insert_parser(config)
            for index in set(range(config["count"])) - config["enabled"]:
                item = config["member_items"].pop(index, None)
                if item is not None and item is not config["item"]:
                    config["item"].removeChild(item)
            for view, members in enumerate(config["plot_views"]):
                for index in members:
                    self._register_parser_member(config, index, view)
            self._refresh_parser_binding_text(config)

        self.alarm_rules = [
            AlarmRule.from_document(saved) for saved in document.get("alarms", [])
        ]
        self._rebuild_alarm_rule_table()

        write = document["write"]
        self.write_address.setValue(write["address"])
        self._set_combo_data(self.write_target, write["target"], "write.target")
        self.write_start.setValue(write["start"])
        self.write_type.setCurrentText(write["type"])
        self._set_combo_data(self.write_order, write["order"], "write.order")
        self.write_values.setPlainText(write["values"])
        self.write_confirm.setChecked(write["confirm"])

        builder = document["builder"]
        self.auto_address.setChecked(builder["auto_address"])
        self.auto_function.setChecked(builder["auto_function"])
        self.auto_crc.setChecked(builder["auto_crc"])
        self.builder_address.setValue(builder["address"])
        self._set_combo_data(self.builder_function, builder["function"], "builder.function")
        self.builder_start.setValue(builder["start"])
        self.builder_quantity.setValue(builder["quantity"])
        self.builder_values.setText(builder["values"])
        self.manual_hex.setText(builder["manual_hex"])
        self.repeat_interval.setValue(builder["repeat_interval_ms"])

        ui = document["ui"]
        self._set_combo_data(self.theme_combo, ui["theme"], "ui.theme")
        main_views = {"frames": 0, "raw": 1, "overview": 2, "waveform": 3, "alarms": 4}
        right_tabs = {"receive": 0, "poll": 0, "parse": 1, "write": 2,
                      "builder": 3, "serial": 4, "settings": 4}
        self._switch_view(main_views[ui["main_view"]])
        self.right_tabs.setCurrentIndex(right_tabs[ui["right_tab"]])
        self.resize(ui["window_size"]["width"], ui["window_size"]["height"])
        self.body_splitter.setSizes(ui["splitter_sizes"])
        wave_views = ui.get("wave_views", [ui["wave_view"], ui["wave_view"]])
        for plot, state in zip(self.live_plots, wave_views):
            plot.restore_view_state(state)
        self.wave_tabs.setCurrentIndex(ui.get("wave_layout", 0))
        if self.wave_tabs.currentIndex() == 1:
            self._activate_wave_plot(ui.get("active_wave_view", 0))
        self._apply_filters()
        self._update_builder()
        self._update_write_preview()

    def _switch_view(self, index: int) -> None:
        self.views.setCurrentIndex(index)
        if self.view_tabs.currentIndex() != index:
            self.view_tabs.setCurrentIndex(index)
        # One-shot contextual navigation: users can freely change the right tab afterwards.
        if not hasattr(self, "right_tabs"):
            return
        target_page = self.poll_page if index in (0, 1) else self.parse_page
        self.right_tabs.setCurrentWidget(target_page)

    def refresh_ports(self, manual: bool = False) -> None:
        ports = sorted(list_ports.comports(), key=lambda item: item.device)
        signature = tuple((item.device, item.description or "", item.hwid or "") for item in ports)
        if not manual and signature == self._known_ports:
            return

        previous_signature = self._known_ports
        self._known_ports = signature
        connected_port = self.worker.config["port"] if self.worker and self.worker.isRunning() else ""
        selected = (
            self.port_combo.currentData()
            or self.settings.serial_config()["port"]
            or self.port_combo.currentText()
        )
        self.port_combo.clear()
        for port in ports:
            label = f"{port.device}  ·  {port.description}" if port.description else port.device
            self.port_combo.addItem(label, port.device)
        if not ports:
            self.port_combo.addItem("未发现串口", "")
        if selected:
            index = self.port_combo.findData(selected)
            if index < 0:
                self.port_combo.addItem(f"{selected}  ·  已保存（当前未发现）", selected)
                index = self.port_combo.count() - 1
            self.port_combo.setCurrentIndex(index)

        devices = {item.device for item in ports}
        if connected_port and connected_port not in devices:
            self.disconnect_serial()
            self.status_label.setText(f"设备 {connected_port} 已拔出，正在自动搜索新串口…")
        elif not previous_signature and signature and not manual:
            self.status_label.setText(f"检测到新串口 {ports[0].device}，已自动加入列表")
        elif previous_signature != signature and not manual:
            self.status_label.setText(f"串口列表已自动更新 · 当前 {len(ports)} 个设备")
        elif manual:
            self.status_label.setText(f"发现 {len(ports)} 个串口")

    def _theme_changed(self) -> None:
        theme = self.theme_combo.currentData() or "light"
        app = QApplication.instance()
        if app:
            app.setStyleSheet(DARK_STYLE if theme == "dark" else LIGHT_STYLE)
        self.settings.set("appearance/theme", theme)

    def _serial_config(self) -> dict:
        return {
            "port": self.port_combo.currentData() or "",
            "baudrate": int(self.baud_combo.currentText()),
            "bytesize": int(self.data_bits.currentText()),
            "parity": self.parity.currentData(),
            "stopbits": self.stop_bits.currentData(),
        }

    def toggle_connection(self) -> None:
        if self.worker and self.worker.isRunning():
            self.disconnect_serial()
            return
        config = self._serial_config()
        if not config["port"]:
            QMessageBox.information(self, "没有可用串口", "请连接串口设备后点击刷新。")
            return
        self.settings.save_serial_config(config)
        self.worker = SerialWorker(config, self)
        self.worker.data_received.connect(self._on_bytes)
        self.worker.frame_received.connect(self._on_frame)
        self.worker.status_changed.connect(self._on_serial_status)
        self.worker.error_occurred.connect(self._on_serial_error)
        self.connect_button.setEnabled(False)
        self.connection_dot.setText("●  正在连接")
        self.connection_dot.setStyleSheet("color:#ffd166;padding:0 6px")
        self.worker.start()

    def disconnect_serial(self) -> None:
        self.send_timer.stop()
        self.repeat_send.setChecked(False)
        self._stop_polling(silent=True)
        self._reset_write_transaction()
        if self.worker:
            if not self.worker.stop():
                self.status_label.setText("串口线程仍在退出，请稍后重试")
                return
            self.worker = None
        self._on_serial_status(False, "已断开")

    def _on_serial_status(self, connected: bool, detail: str) -> None:
        if not connected and (self.write_in_progress or self._queued_write_frame is not None):
            self._reset_write_transaction()
        self.connect_button.setEnabled(True)
        self.connect_button.setText("断开连接" if connected else "连接串口")
        self.connect_button.setObjectName("danger" if connected else "primary")
        self.connect_button.style().unpolish(self.connect_button)
        self.connect_button.style().polish(self.connect_button)
        self.connection_dot.setText(f"●  {detail}")
        self.connection_dot.setStyleSheet(
            f"color:{'#36d59c' if connected else '#738096'};padding:0 6px"
        )
        self.port_combo.setEnabled(not connected)
        self.baud_combo.setEnabled(not connected)
        self.data_bits.setEnabled(not connected)
        self.parity.setEnabled(not connected)
        self.stop_bits.setEnabled(not connected)
        self.status_label.setText("串口已连接，正在监听 RTU 数据" if connected else detail)

    def _on_serial_error(self, message: str) -> None:
        self.status_label.setText(f"串口错误：{message}")
        QMessageBox.warning(self, "串口错误", message)

    def _on_bytes(self, byte_count: int) -> None:
        self.rx_bytes += byte_count
        if not self.pause_button.isChecked():
            self.rx_metric.setText(f"{self.rx_bytes:,}")

    def _toggle_display_pause(self, paused: bool) -> None:
        self.pause_button.setText("继续" if paused else "暂停")
        for plot in self.live_plots:
            plot.set_inspection_enabled(paused)
        if paused:
            self.status_label.setText("显示已暂停 · 可在波形上悬停查看各通道采样值")
            return
        self.rx_metric.setText(f"{self.rx_bytes:,}")
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self.good_metric.setText(f"{self.good_frames:,}")
        self.bad_metric.setText(f"{self.bad_frames:,}")
        self.status_label.setText("显示已继续 · 等待下一帧数据")

    def _on_frame(
        self, raw: bytes, direction: str = "RX", poll_slot: str | None = None,
        poll_id: str | None = None,
    ) -> None:
        frame = RtuFrame(raw, datetime.now(), direction)
        if direction == "RX" and self.poll_active and self.poll_current_task is not None:
            task = self.poll_current_task
            expected_function = task["function"]
            if (
                frame.address == task["address"]
                and frame.function in (expected_function, expected_function | 0x80)
            ):
                poll_id = task["id"]
                poll_slot = f"{poll_id}:rx"
        if self.pause_button.isChecked():
            if frame.crc_ok:
                self.good_frames += 1
            else:
                self.bad_frames += 1
            if direction == "RX":
                if frame.crc_ok:
                    self._parse_frame_channels(frame, update_display=False, poll_id=poll_id)
                if not self._handle_write_response(frame):
                    self._handle_poll_response(frame)
            return
        self.frames.append(frame)
        while len(self.frames) > self.keep_limit.value():
            self.frames.popleft()
        if frame.crc_ok:
            self.good_frames += 1
        else:
            self.bad_frames += 1
        self.good_metric.setText(f"{self.good_frames:,}")
        self.bad_metric.setText(f"{self.bad_frames:,}")

        time_format = "%H:%M:%S.%f" if self.show_timestamp.isChecked() else "%H:%M:%S"
        stamp = frame.timestamp.strftime(time_format)[:-3] if "%f" in time_format else frame.timestamp.strftime(time_format)
        address = "—" if frame.address is None else str(frame.address)
        english = self._english_display()
        function = frame.localized_function_name(english)
        crc_label = ("OK" if frame.crc_ok else "Error") if english else ("正确" if frame.crc_ok else "异常")
        raw_hex = hex_text(raw)
        if not self.uppercase_hex.isChecked():
            raw_hex = raw_hex.lower()
        row = [
            QStandardItem(poll_id or "—"),
            QStandardItem(stamp),
            QStandardItem(direction),
            QStandardItem(address),
            QStandardItem(function),
            QStandardItem(frame.localized_summary(english)),
            QStandardItem(crc_label),
            QStandardItem(raw_hex),
        ]
        row[3].setData(frame.address, Qt.ItemDataRole.UserRole)
        row[4].setData(frame.function, Qt.ItemDataRole.UserRole)
        row[6].setData(frame.crc_ok, Qt.ItemDataRole.UserRole)
        row[1].setData(frame, Qt.ItemDataRole.UserRole)
        if poll_slot:
            row[2].setData(poll_slot, Qt.ItemDataRole.UserRole + 1)
        color = QColor("#48d9c2" if direction == "RX" else "#68a7ff")
        row[2].setForeground(color)
        row[6].setForeground(QColor("#68df95" if frame.crc_ok else "#ff7185"))
        stable_row = self._find_poll_row(poll_slot) if poll_slot else -1
        if stable_row >= 0:
            # Keep the model items alive and update them in place. Replacing all seven
            # items on every poll response made the row and selection visibly flash.
            self.frame_table.setUpdatesEnabled(False)
            try:
                for column, new_item in enumerate(row):
                    current = self.frame_model.item(stable_row, column)
                    if current is None:
                        self.frame_model.setItem(stable_row, column, new_item)
                        continue
                    if current.text() != new_item.text():
                        current.setText(new_item.text())
                    current.setData(new_item.data(Qt.ItemDataRole.UserRole), Qt.ItemDataRole.UserRole)
                    current.setData(
                        new_item.data(Qt.ItemDataRole.UserRole + 1),
                        Qt.ItemDataRole.UserRole + 1,
                    )
                    current.setForeground(new_item.foreground())
            finally:
                self.frame_table.setUpdatesEnabled(True)
                self.frame_table.viewport().update()
        else:
            self.frame_model.appendRow(row)
            self._trim_frame_rows()

        self._raw_pending.append(f"{stamp}  {direction:<2}  {raw_hex}")
        if direction == "RX" and frame.crc_ok:
            self._parse_frame_channels(frame, poll_id=poll_id)
        if direction == "RX":
            if not self._handle_write_response(frame):
                self._handle_poll_response(frame)
        self._overview_dirty = True

    @staticmethod
    def _set_label_text(label: QLabel, text: str) -> None:
        """Avoid needless label relayout/repaint when a fixed field did not change."""
        if label.text() != text:
            label.setText(text)

    def _flush_raw_output(self) -> None:
        if not self._raw_pending:
            return
        pending = list(self._raw_pending)
        self._raw_pending.clear()
        self.raw_text.appendPlainText("\n".join(pending))

    def _flush_overview(self) -> None:
        if self._wave_count_dirty:
            self._wave_count_dirty = False
            self.wave_record_count.setText(f"已记录 {len(self.wave_recorder):,} 行")
        if self._alarm_view_dirty:
            self._flush_alarm_rule_values()
            self._refresh_alarm_summary()
        if not self._overview_dirty:
            return
        self._overview_dirty = False
        self._update_byte_overview()

    def _find_poll_row(self, slot: str | None) -> int:
        if not slot:
            return -1
        for row in range(self.frame_model.rowCount()):
            item = self.frame_model.item(row, 2)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == slot:
                return row
        return -1

    def _ensure_poll_rows(self) -> None:
        if not hasattr(self, "frame_model"):
            return
        english = self._english_display()
        for task in self.poll_tasks:
            for suffix, direction, summary in (
                ("tx", "TX", "Waiting to send" if english else "等待发送请求"),
                ("rx", "RX", "Waiting for first response" if english else "等待首个响应"),
            ):
                slot = f"{task['id']}:{suffix}"
                if self._find_poll_row(slot) >= 0:
                    continue
                items = [
                    QStandardItem(task["id"]), QStandardItem("—"), QStandardItem(direction),
                    QStandardItem(str(task["address"])), QStandardItem("Polling" if english else "轮询"),
                    QStandardItem(summary), QStandardItem("—"), QStandardItem("—"),
                ]
                items[2].setData(slot, Qt.ItemDataRole.UserRole + 1)
                items[2].setForeground(QColor("#48d9c2" if direction == "RX" else "#68a7ff"))
                self.frame_model.appendRow(items)

    def _trim_frame_rows(self) -> None:
        while self.frame_model.rowCount() > self.keep_limit.value():
            removable = -1
            for row in range(self.frame_model.rowCount()):
                item = self.frame_model.item(row, 2)
                slot = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
                if not isinstance(slot, str) or ":" not in slot:
                    removable = row
                    break
            if removable < 0:
                break
            self.frame_model.removeRow(removable)

    def _show_selected_frame(self) -> None:
        indexes = self.frame_table.selectionModel().selectedRows()
        if not indexes:
            return
        source_index = self.frame_filter.mapToSource(indexes[0])
        frame = self.frame_model.item(source_index.row(), 1).data(Qt.ItemDataRole.UserRole)
        poll_id = self.frame_model.item(source_index.row(), 0).text()
        english = self._english_display()
        if not isinstance(frame, RtuFrame):
            direction = self.frame_model.item(source_index.row(), 2).text()
            summary = self.frame_model.item(source_index.row(), 5).text()
            if english:
                self.detail_text.setPlainText(
                    f"Poll ID    {poll_id}\nDirection  {direction}\nStatus     {summary}\n\nNo complete frame is available for this row yet."
                )
            else:
                self.detail_text.setPlainText(
                    f"轮询项   {poll_id}\n方向     {direction}\n状态     {summary}\n\n当前行尚无完整帧。"
                )
            return
        crc_received = int.from_bytes(frame.raw[-2:], "little") if len(frame.raw) >= 2 else 0
        if english:
            lines = [
                f"Poll ID    {poll_id}",
                f"Time       {frame.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}",
                f"Direction  {frame.direction}",
                f"Slave      {frame.address if frame.address is not None else '—'}",
                f"Function   {frame.localized_function_name(True)}",
                f"Decoded    {frame.localized_summary(True)}",
                f"CRC        {'Passed' if frame.crc_ok else 'Failed'} · Frame 0x{crc_received:04X}",
                f"HEX        {hex_text(frame.raw)}",
            ]
        else:
            lines = [
                f"轮询项   {poll_id}",
                f"时间     {frame.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}",
                f"方向     {frame.direction}",
                f"从站     {frame.address if frame.address is not None else '—'}",
                f"功能     {frame.function_name}",
                f"解析     {frame.summary}",
                f"CRC      {'通过' if frame.crc_ok else '未通过'} · 帧内 0x{crc_received:04X}",
                f"HEX      {hex_text(frame.raw)}",
            ]
        self.detail_text.setPlainText("\n".join(lines))

    def _english_display(self) -> bool:
        if hasattr(self, "language_combo"):
            return self.language_combo.currentData() == "en_US"
        return self.settings.value("ui/language", "zh_CN") == "en_US"

    def _update_byte_overview(self) -> None:
        sample = b"".join(frame.raw for frame in list(self.frames)[-200:])
        if not sample:
            self.byte_overview.clear()
            return
        counts = sorted(((sample.count(value), value) for value in set(sample)), reverse=True)[:16]
        maximum = counts[0][0]
        lines = []
        for count, value in counts:
            bar = "█" * max(1, round(count / maximum * 28))
            lines.append(f"0x{value:02X}  {bar:<28}  {count:>5}  {count / len(sample):>6.1%}")
        self.byte_overview.setPlainText("\n".join(lines))

    def _apply_filters(self) -> None:
        # The app targets one device at a time, so the frame list always shows
        # the complete traffic stream. Legacy project filters are intentionally ignored.
        self.frame_filter.address = -1
        self.frame_filter.function = -1
        self.frame_filter.crc_mode = 0
        self.frame_filter.keyword = ""
        self.frame_filter.invalidateFilter()

    def _task_by_id(self, task_id: str | None) -> dict | None:
        return next((task for task in self.poll_tasks if task["id"] == task_id), None)

    def _selected_poll_task(self) -> dict | None:
        item = self.poll_task_tree.currentItem() if hasattr(self, "poll_task_tree") else None
        return self._task_by_id(item.data(0, Qt.ItemDataRole.UserRole) if item else None)

    def _create_poll_task(self, source: dict | None = None, select: bool = True) -> dict:
        used_numbers = {
            int(task["id"][1:]) for task in self.poll_tasks
            if task["id"].startswith("P") and task["id"][1:].isdigit()
        }
        task_number = 1
        while task_number in used_numbers:
            task_number += 1
        self.poll_task_sequence = max(self.poll_task_sequence, task_number)
        task_id = f"P{task_number}"
        if source is None:
            task = {
                "id": task_id, "name": f"轮询 {task_number}", "enabled": True,
                "address": self.poll_address.value(), "function": self.poll_function.currentData(),
                "range_mode": "auto", "start": self.poll_start.value(),
                "quantity": self.poll_quantity.value(), "interval_ms": self.poll_interval.value(),
                "timeout_ms": self.poll_timeout.value(), "success": 0, "failures": 0,
                "last_response": "—", "next_due": 0.0,
            }
        else:
            task = dict(source)
            task.pop("_pending_update", None)
            task.pop("_delete_after_cycle", None)
            task.update({
                "id": task_id, "name": f"{source['name']} 副本", "success": 0,
                "failures": 0, "last_response": "—", "next_due": 0.0,
            })
        self.poll_tasks.append(task)
        self._refresh_poll_task_tree(task_id if select else None)
        self._refresh_parser_poll_sources()
        self._ensure_poll_rows()
        return task

    def _refresh_poll_task_tree(self, select_id: str | None = None) -> None:
        if not hasattr(self, "poll_task_tree"):
            return
        if select_id is None:
            selected = self._selected_poll_task()
            select_id = selected["id"] if selected else None
        self.poll_task_tree.blockSignals(True)
        self.poll_task_tree.clear()
        selected_item = None
        for task in self.poll_tasks:
            values = {**task, **task.get("_pending_update", {})}
            end = values["start"] + values["quantity"] - 1
            item = QTreeWidgetItem([task["id"], values["name"], f"R{values['start']}～R{end}"])
            item.setData(0, Qt.ItemDataRole.UserRole, task["id"])
            if not values["enabled"]:
                for column in range(3):
                    item.setForeground(column, QColor("#8793a5"))
            self.poll_task_tree.addTopLevelItem(item)
            if task["id"] == select_id:
                selected_item = item
        self.poll_task_tree.blockSignals(False)
        if selected_item is None and self.poll_task_tree.topLevelItemCount():
            selected_item = self.poll_task_tree.topLevelItem(0)
        if selected_item is not None:
            self.poll_task_tree.setCurrentItem(selected_item)
            self._load_poll_task(self._task_by_id(selected_item.data(0, Qt.ItemDataRole.UserRole)))

    def _load_poll_task(self, task: dict | None) -> None:
        if task is None:
            return
        values = {**task, **task.get("_pending_update", {})}
        self._poll_form_loading = True
        try:
            self.poll_name.setText(values["name"])
            self.poll_enabled.setChecked(values["enabled"])
            self.poll_address.setValue(values["address"])
            self._set_combo_data(self.poll_function, values["function"], "poll.function")
            self._set_combo_data(self.poll_range_mode, values["range_mode"], "poll.range_mode")
            self.poll_start.setValue(values["start"])
            self.poll_quantity.setValue(values["quantity"])
            self.poll_interval.setValue(values["interval_ms"])
            self.poll_timeout.setValue(values["timeout_ms"])
            self._set_label_text(self.poll_success_value, str(task["success"]))
            self._set_label_text(self.poll_failure_value, str(task["failures"]))
            self._set_label_text(self.poll_response_value, task["last_response"])
            self._update_poll_range_display()
        finally:
            self._poll_form_loading = False

    def _poll_task_selected(self, current: QTreeWidgetItem | None, _previous=None) -> None:
        task_id = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self._load_poll_task(self._task_by_id(task_id))

    def _poll_form_changed(self, *_args) -> None:
        if self._poll_form_loading:
            return
        task = self._selected_poll_task()
        if task is None:
            return
        changes = {
            "name": self.poll_name.text().strip() or task["id"],
            "enabled": self.poll_enabled.isChecked(),
            "address": self.poll_address.value(), "function": self.poll_function.currentData(),
            "range_mode": self.poll_range_mode.currentData(), "start": self.poll_start.value(),
            "quantity": self.poll_quantity.value(), "interval_ms": self.poll_interval.value(),
            "timeout_ms": self.poll_timeout.value(),
        }
        if self.poll_waiting and self.poll_current_task is task:
            task["_pending_update"] = changes
            self._update_poll_task_item(task, changes)
            self._set_label_text(self.poll_range_value, f"R{changes['start']}～R{changes['start'] + changes['quantity'] - 1}")
            self.status_label.setText(f"{task['id']} 的修改将在当前响应结束后生效")
            return
        self._apply_poll_task_update(task, changes)

    def _apply_poll_task_update(self, task: dict, changes: dict) -> None:
        task.update(changes)
        self._sync_parsers_with_poll_task(task)
        self._update_poll_task_item(task)
        self._refresh_parser_poll_sources()
        if self._selected_poll_task() is task:
            self._load_poll_task(task)

    def _sync_parsers_with_poll_task(self, task: dict) -> None:
        required_quantity = 1
        for config in self.parsers:
            if config.get("source") != "data" or config.get("poll_id") != task["id"]:
                continue
            config["register"] = task["start"]
            config["offset"] = task["start"]
            config["address"] = task["address"]
            config["function"] = task["function"]
            size = type_size(config["type"])
            lane = config.get("byte_lane", 0) if size == 1 else 0
            required_quantity = max(required_quantity, (lane + size * config["count"] + 1) // 2)
            if config["count"] == 1:
                config["item"].setText(1, self._parser_member_location(config, 0))
                config["item"].setText(2, "等待数据")
            else:
                for index, item in config["member_items"].items():
                    item.setText(1, self._parser_member_location(config, index))
                    item.setText(2, "等待数据")
                config["item"].setText(2, "等待数据")
        if task["range_mode"] == "auto" and task["function"] in (3, 4):
            task["quantity"] = max(task["quantity"], required_quantity)

    def _update_poll_task_item(self, task: dict, preview: dict | None = None) -> None:
        values = {**task, **(preview or {})}
        for index in range(self.poll_task_tree.topLevelItemCount()):
            item = self.poll_task_tree.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) != task["id"]:
                continue
            end = values["start"] + values["quantity"] - 1
            item.setText(1, values["name"])
            item.setText(2, f"R{values['start']}～R{end}")
            color = QColor() if values["enabled"] else QColor("#8793a5")
            for column in range(3):
                item.setForeground(column, color)
            break

    def _add_poll_task(self) -> None:
        self._create_poll_task()

    def _copy_poll_task(self) -> None:
        task = self._selected_poll_task()
        if task is not None:
            self._create_poll_task(task)

    def _delete_poll_task(self) -> None:
        task = self._selected_poll_task()
        if task is None:
            return
        if len(self.poll_tasks) == 1:
            QMessageBox.information(self, "保留轮询项", "至少需要保留一个轮询项。")
            return
        if any(config.get("poll_id") == task["id"] for config in self.parsers):
            QMessageBox.information(self, "轮询项正在使用", "请先删除或改绑使用此数据块的解析通道。")
            return
        if self.poll_waiting and self.poll_current_task is task:
            task["_delete_after_cycle"] = True
            task["enabled"] = False
            self._update_poll_task_item(task)
            self.status_label.setText(f"{task['id']} 将在当前响应结束后删除")
            return
        self._delete_poll_task_now(task)

    def _delete_poll_task_now(self, task: dict) -> None:
        if task not in self.poll_tasks:
            return
        self.poll_tasks.remove(task)
        for row in range(self.frame_model.rowCount() - 1, -1, -1):
            item = self.frame_model.item(row, 0)
            if item and item.text() == task["id"]:
                self.frame_model.removeRow(row)
        self._refresh_poll_task_tree()
        self._refresh_parser_poll_sources()

    def _refresh_parser_poll_sources(self) -> None:
        if not hasattr(self, "parser_poll_task"):
            return
        current = self.parser_poll_task.currentData()
        self.parser_poll_task.blockSignals(True)
        self.parser_poll_task.clear()
        for task in self.poll_tasks:
            end = task["start"] + task["quantity"] - 1
            self.parser_poll_task.addItem(
                f"{task['id']} · {task['name']} · 从站{task['address']} · R{task['start']}～R{end}",
                task["id"],
            )
        index = self.parser_poll_task.findData(current)
        self.parser_poll_task.setCurrentIndex(index if index >= 0 else 0)
        self.parser_poll_task.blockSignals(False)
        if hasattr(self, "parser_offset"):
            self._parser_poll_source_changed()

    def _parser_poll_source_changed(self, *_args) -> None:
        task = self._task_by_id(self.parser_poll_task.currentData())
        if task is not None:
            self.parser_offset.setValue(task["start"])
        self._update_parser_offset_hint()

    def _run_selected_poll_once(self) -> None:
        task = self._selected_poll_task()
        if task is None or self.poll_waiting or self.write_in_progress:
            return
        if not self.worker or not self.worker.isRunning():
            QMessageBox.information(self, "尚未连接", "请先连接串口，再执行轮询项。")
            return
        task["next_due"] = 0.0
        if self.poll_active:
            self.poll_next_timer.stop()
            self._send_poll_request(preferred_task=task)
            return
        self.poll_manual_once = True
        self.poll_active = True
        self._send_poll_request(preferred_task=task)

    def _toggle_polling(self) -> None:
        if self.poll_active:
            self._stop_polling()
            return
        if not self.worker or not self.worker.isRunning():
            QMessageBox.information(self, "尚未连接", "请先连接串口，再启动寄存器轮询。")
            return
        self._poll_form_changed()
        enabled = [task for task in self.poll_tasks if task["enabled"]]
        if not enabled:
            QMessageBox.information(self, "没有启用项", "请至少启用一个轮询项。")
            return
        try:
            for task in enabled:
                build_request(task["address"], task["function"], task["start"], task["quantity"])
        except ValueError as exc:
            QMessageBox.information(self, "轮询参数无效", str(exc))
            return
        self.poll_active = True
        self.poll_manual_once = False
        self.poll_waiting = False
        self.poll_current_task = None
        self.poll_success = 0
        self.poll_failures = 0
        now = time.monotonic()
        for task in self.poll_tasks:
            task.update(success=0, failures=0, last_response="等待", next_due=now)
        self._set_label_text(self.poll_state_value, "运行中")
        self._set_label_text(self.poll_success_value, "0")
        self._set_label_text(self.poll_failure_value, "0")
        self._set_label_text(self.poll_response_value, "等待")
        self._update_poll_range_display()
        self.repeat_send.setChecked(False)
        self._ensure_poll_rows()
        self.poll_button.setText("停止轮询")
        self.poll_button.setObjectName("danger")
        self.poll_button.style().unpolish(self.poll_button)
        self.poll_button.style().polish(self.poll_button)
        self._send_poll_request()

    def _stop_polling(self, silent: bool = False) -> None:
        was_active = self.poll_active
        self.poll_active = False
        self.poll_manual_once = False
        self.poll_waiting = False
        current_task = self.poll_current_task
        self.poll_current_task = None
        if current_task is not None:
            pending = current_task.pop("_pending_update", None)
            delete_after_cycle = current_task.pop("_delete_after_cycle", False)
            if delete_after_cycle:
                self._delete_poll_task_now(current_task)
            elif pending:
                self._apply_poll_task_update(current_task, pending)
        if hasattr(self, "poll_next_timer"):
            self.poll_next_timer.stop()
            self.poll_timeout_timer.stop()
        if hasattr(self, "poll_button"):
            self.poll_button.setText("开始安全轮询")
            self.poll_button.setObjectName("primary")
            self.poll_button.style().unpolish(self.poll_button)
            self.poll_button.style().polish(self.poll_button)
            for widget in self._poll_editor_widgets():
                widget.setEnabled(True)
            for button in (self.poll_add_button, self.poll_copy_button, self.poll_delete_button, self.poll_once_button):
                button.setEnabled(True)
        cancelled_write = self._queued_write_frame is not None and not self.write_in_progress
        if cancelled_write:
            self._reset_write_transaction()
        if was_active and not silent:
            self.status_label.setText(
                "轮询已停止，排队中的写入已取消"
                if cancelled_write else "寄存器轮询已停止"
            )
        if was_active and not self.write_in_progress:
            self._set_label_text(self.poll_state_value, "已停止")

    def _update_poll_range_display(self) -> None:
        task = self._selected_poll_task()
        values = {**task, **task.get("_pending_update", {})} if task else None
        start = values["start"] if values else self.poll_start.value()
        quantity = values["quantity"] if values else self.poll_quantity.value()
        end = start + quantity - 1
        self._set_label_text(self.poll_range_value, f"R{start}～R{end}")

    def _poll_editor_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.poll_name, self.poll_enabled, self.poll_address, self.poll_function,
            self.poll_range_mode, self.poll_start, self.poll_quantity,
            self.poll_interval, self.poll_timeout,
        )

    def _schedule_next_poll_task(self) -> None:
        if not self.poll_active or self.poll_waiting or self.write_in_progress:
            return
        enabled = [task for task in self.poll_tasks if task["enabled"]]
        if not enabled:
            self._stop_polling()
            return
        task = min(enabled, key=lambda item: (item["next_due"], self.poll_tasks.index(item)))
        delay_ms = max(0, round((task["next_due"] - time.monotonic()) * 1000))
        self.poll_next_timer.start(delay_ms)

    def _send_poll_request(self, preferred_task: dict | None = None) -> None:
        if (
            not self.poll_active
            or self.poll_waiting
            or self.write_in_progress
            or self._queued_write_frame is not None
        ):
            return
        if not self.worker or not self.worker.isRunning():
            self._stop_polling(silent=True)
            return
        enabled = [task for task in self.poll_tasks if task["enabled"]]
        if preferred_task is not None:
            task = preferred_task
        elif enabled:
            task = min(enabled, key=lambda item: (item["next_due"], self.poll_tasks.index(item)))
            if task["next_due"] > time.monotonic():
                self._schedule_next_poll_task()
                return
        else:
            self._stop_polling()
            return
        try:
            self.poll_request = build_request(
                task["address"], task["function"], task["start"], task["quantity"],
            )
            self.worker.send(self.poll_request)
        except ValueError as exc:
            task["enabled"] = False
            task["failures"] += 1
            task["last_response"] = "参数异常"
            self._update_poll_task_item(task)
            self.status_label.setText(f"{task['id']} 参数无效，已暂停：{exc}")
            self._schedule_next_poll_task()
            return
        except serial.SerialException as exc:
            self._on_serial_error(str(exc))
            self._stop_polling(silent=True)
            return
        self.tx_bytes += len(self.poll_request)
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self.poll_current_task = task
        self._on_frame(
            self.poll_request, "TX", poll_slot=f"{task['id']}:tx", poll_id=task["id"]
        )
        self.poll_waiting = True
        self.poll_sent_quantity = task["quantity"]
        self.poll_sent_at = time.perf_counter()
        self.poll_timeout_timer.start(task["timeout_ms"])
        task["last_response"] = "等待响应"

    def _set_poll_rx_waiting(self, task: dict, text: str = "等待响应") -> None:
        row = self._find_poll_row(f"{task['id']}:rx")
        if row < 0:
            self._ensure_poll_rows()
            row = self._find_poll_row(f"{task['id']}:rx")
        if row < 0:
            return
        if self._english_display():
            text = {"等待响应": "Waiting for response", "超时": "Timeout"}.get(text, text)
            function_text = "Polling"
        else:
            function_text = "轮询"
        self.frame_table.setUpdatesEnabled(False)
        try:
            for column, value in ((1, "—"), (3, str(task["address"])), (4, function_text),
                                  (5, text), (6, "—"), (7, "—")):
                item = self.frame_model.item(row, column)
                if item is not None:
                    item.setText(value)
            time_item = self.frame_model.item(row, 1)
            if time_item is not None:
                time_item.setData(None, Qt.ItemDataRole.UserRole)
        finally:
            self.frame_table.setUpdatesEnabled(True)
            self.frame_table.viewport().update()

    def _handle_poll_response(self, frame: RtuFrame) -> None:
        task = self.poll_current_task
        if not self.poll_active or not self.poll_waiting or task is None:
            return
        expected_address = task["address"]
        expected_function = task["function"]
        if frame.address != expected_address or frame.function not in (expected_function, expected_function | 0x80):
            return
        self.poll_timeout_timer.stop()
        self.poll_waiting = False
        elapsed_ms = (time.perf_counter() - self.poll_sent_at) * 1000
        data_bytes = (
            (self.poll_sent_quantity + 7) // 8
            if expected_function in (1, 2)
            else self.poll_sent_quantity * 2
        )
        expected_length = data_bytes + 5
        if frame.crc_ok and frame.function == expected_function and len(frame.raw) == expected_length:
            self.poll_success += 1
            task["success"] += 1
            response = f"{elapsed_ms:.1f} ms"
        else:
            self.poll_failures += 1
            task["failures"] += 1
            response = "异常"
        task["last_response"] = response
        task["next_due"] = time.monotonic() + task["interval_ms"] / 1000
        if self._selected_poll_task() is task:
            self._set_label_text(self.poll_success_value, str(task["success"]))
            self._set_label_text(self.poll_failure_value, str(task["failures"]))
            self._set_label_text(self.poll_response_value, response)
        self._continue_after_poll_cycle()

    def _poll_timed_out(self) -> None:
        task = self.poll_current_task
        if not self.poll_active or not self.poll_waiting or task is None:
            return
        self.poll_waiting = False
        self.poll_failures += 1
        task["failures"] += 1
        task["last_response"] = "超时"
        task["next_due"] = time.monotonic() + task["interval_ms"] / 1000
        self._set_poll_rx_waiting(task, "超时")
        if self._selected_poll_task() is task:
            self._set_label_text(self.poll_success_value, str(task["success"]))
            self._set_label_text(self.poll_failure_value, str(task["failures"]))
            self._set_label_text(self.poll_response_value, "超时")
        self._continue_after_poll_cycle()

    def _continue_after_poll_cycle(self) -> None:
        finished_task = self.poll_current_task
        self.poll_current_task = None
        if finished_task is not None:
            pending = finished_task.pop("_pending_update", None)
            delete_after_cycle = finished_task.pop("_delete_after_cycle", False)
            if delete_after_cycle:
                self._delete_poll_task_now(finished_task)
            elif pending:
                self._apply_poll_task_update(finished_task, pending)
        if self.poll_manual_once:
            self.poll_manual_once = False
            self.poll_active = False
            self._set_label_text(self.poll_state_value, "单次完成")
            return
        if self._queued_write_frame is not None:
            self._send_queued_write()
        elif self.poll_active:
            self._schedule_next_poll_task()

    def _poll_function_changed(self) -> None:
        function = self.poll_function.currentData()
        self.poll_quantity.setMaximum(2000 if function in (1, 2) else 125)

    def _update_parser_offset_hint(self, *_args) -> None:
        if not hasattr(self, "parser_offset_hint"):
            return
        size = type_size(self.parser_type.currentText())
        is_8bit = size == 1
        self.parser_byte_lane.setEnabled(is_8bit)
        self.parser_form.setRowVisible(self.parser_byte_lane, is_8bit)
        task = self._task_by_id(self.parser_poll_task.currentData())
        poll_start = task["start"] if task else self.poll_start.value()
        poll_end = poll_start + (task["quantity"] if task else self.poll_quantity.value()) - 1
        self.parser_offset.setValue(poll_start)
        register_step = max(1, (size + 1) // 2)
        first_register = poll_start
        lane = self.parser_byte_lane.currentData() if is_8bit else 0
        total_bytes = size * self.parser_count.value()
        last_register = first_register + (lane + total_bytes - 1) // 2
        location = f"R{first_register}" if last_register == first_register else f"R{first_register}～R{last_register}"
        notice = "注意：解析数量填写数值个数，不是寄存器数量。"
        if last_register > poll_end:
            task_id = task["id"] if task else "当前数据块"
            notice = f"注意：范围超过 {task_id}，添加后会自动扩展数据块。"
        self.parser_offset_hint.setText(
            f"解析 {location} · {self.parser_type.currentText()} 每个占 {register_step} 个寄存器\n{notice}"
        )

    def _sync_parser_register_with_poll(self, new_start: int) -> None:
        if hasattr(self, "parser_offset") and self.parser_offset.value() == self._last_poll_start:
            self.parser_offset.setValue(new_start)
        self._last_poll_start = new_start
        self._update_parser_offset_hint()

    def _add_parser(self) -> None:
        try:
            scale = float(self.parser_scale.text().strip() or "1")
        except ValueError:
            QMessageBox.information(self, "缩放倍率无效", "缩放倍率必须是数字，例如 1、0.1 或 10。")
            return
        self._parser_sequence += 1
        base_name = self.parser_name.text().strip() or f"通道 {self._parser_sequence}"
        existing = {config["name"] for config in self.parsers}
        name = base_name
        suffix = 2
        while name in existing:
            name = f"{base_name} {suffix}"
            suffix += 1
        task = self._task_by_id(self.parser_poll_task.currentData())
        config = {
            "name": name,
            "type": self.parser_type.currentText(),
            "order": self.parser_order.currentData(),
            "source": "data",
            "poll_id": self.parser_poll_task.currentData(),
            "address": task["address"] if task else 0,
            "function": task["function"] if task else -1,
            "register": self.parser_offset.value(),
            "offset": self.parser_offset.value(),
            "byte_lane": self.parser_byte_lane.currentData(),
            "count": self.parser_count.value(),
            "scale": scale,
            "color": self.parser_colors[len(self.parsers) % len(self.parser_colors)],
        }
        config["members"] = [name] if config["count"] == 1 else [
            f"成员 {index}" for index in range(config["count"])
        ]
        config["plots"] = set()
        config["plot_views"] = [set(), set()]
        config["enabled"] = set(range(config["count"]))
        range_message = self._expand_poll_range_for_parser(config)
        self._insert_parser(config)
        self.parser_name.clear()
        self.status_label.setText(f"已添加解析通道“{name}”{range_message}")

    def _expand_poll_range_for_parser(self, config: dict) -> str:
        """Ensure a register poll includes every byte needed by a new parser."""
        task = self._task_by_id(config.get("poll_id"))
        if config["source"] != "data" or task is None or task["function"] not in (3, 4):
            return ""
        size = type_size(config["type"])
        lane = config.get("byte_lane", 0) if size == 1 else 0
        parser_start = config["register"]
        poll_start = task["start"]
        required_end = parser_start + (lane + size * config["count"] + 1) // 2
        poll_end = poll_start + task["quantity"]
        if poll_start <= parser_start and required_end <= poll_end:
            return ""
        if task["range_mode"] == "manual":
            return f"；地址超出 {task['id']} 的手动范围"
        new_start = min(poll_start, parser_start)
        new_end = max(poll_end, required_end)
        required_quantity = new_end - new_start
        if required_quantity > 125:
            return f"；需要读取 {required_quantity} 个寄存器，超过单帧上限125"
        task["start"] = new_start
        task["quantity"] = required_quantity
        if self._selected_poll_task() is task:
            self._load_poll_task(task)
        self._refresh_poll_task_tree(task["id"])
        self._refresh_parser_poll_sources()
        suffix = "，下一轮生效" if self.poll_active else ""
        return f"；{task['id']} 已自动调整为 R{new_start}～R{new_end - 1}{suffix}"

    def _insert_parser(self, config: dict) -> None:
        saved_views = config.get("plot_views")
        if saved_views is None:
            config["plot_views"] = [set(config.get("plots", set())), set()]
        else:
            config["plot_views"] = [set(view) for view in saved_views[:2]]
            while len(config["plot_views"]) < 2:
                config["plot_views"].append(set())
        config["plots"] = set().union(*config["plot_views"])
        self.parsers.append(config)
        uid = id(config)
        is_single = config["count"] == 1
        root = QTreeWidgetItem([
            config["name"],
            self._parser_member_location(config, 0) if is_single else f"{config['type']} × {config['count']}",
            "等待数据",
        ])
        root.setData(0, Qt.ItemDataRole.UserRole, uid)
        root.setData(0, Qt.ItemDataRole.UserRole + 1, 0 if is_single else -1)
        self.parser_tree.addTopLevelItem(root)
        config["item"] = root
        config["member_items"] = {}
        if is_single:
            config["member_items"][0] = root
            return
        for index, member_name in enumerate(config["members"]):
            item = QTreeWidgetItem([member_name, self._parser_member_location(config, index), "等待数据"])
            item.setData(0, Qt.ItemDataRole.UserRole, uid)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, index)
            root.addChild(item)
            config["member_items"][index] = item
        root.setExpanded(True)

    def _parser_member_location(self, config: dict, index: int) -> str:
        size = type_size(config["type"])
        if config["source"] == "frame":
            return f"Byte {config['offset'] + index * size} · {config['type']}"
        byte_index = config.get("byte_lane", 0) + index * size
        register = config["register"] + byte_index // 2
        lane = ".H" if byte_index % 2 == 0 else ".L"
        suffix = lane if size == 1 else ""
        poll_prefix = f"{config.get('poll_id', '—')} · "
        return f"{poll_prefix}R{register}{suffix} · {config['type']}"

    def _parser_for_item(self, item: QTreeWidgetItem | None) -> tuple[dict | None, int]:
        if item is None:
            return None, -1
        uid = item.data(0, Qt.ItemDataRole.UserRole)
        member_data = item.data(0, Qt.ItemDataRole.UserRole + 1)
        member = int(member_data) if member_data is not None else -1
        return next((config for config in self.parsers if id(config) == uid), None), member

    def _series_name(self, config: dict, index: int) -> str:
        if config["count"] == 1:
            return config["name"]
        return f"{config['name']}.{config['members'][index]}"

    def _register_parser_member(self, config: dict, index: int, view: int) -> None:
        color_index = (self.parsers.index(config) + index) % len(self.parser_colors)
        color = self.parser_colors[color_index]
        self.live_plots[view].register_series(self._series_name(config, index), color)

    def _set_wave_binding(self, config: dict, index: int, view: int, enabled: bool) -> None:
        if enabled:
            config["plot_views"][view].add(index)
            self._register_parser_member(config, index, view)
        else:
            config["plot_views"][view].discard(index)
            self.live_plots[view].remove_series(self._series_name(config, index))
        config["plots"] = set().union(*config["plot_views"])
        self._refresh_parser_binding_text(config)
        state = "已绑定到" if enabled else "已移出"
        self.status_label.setText(f"“{self._series_name(config, index)}”{state}波形 {'AB'[view]}")

    def _refresh_parser_binding_text(self, config: dict) -> None:
        plots = config["plots"]
        if config["count"] == 1:
            config["item"].setForeground(0, QColor(config["color"]) if plots else QColor())
            views = "".join("AB"[view] for view in range(2) if 0 in config["plot_views"][view])
            config["item"].setToolTip(0, f"已绑定波形 {views}" if views else "")
            return
        config["item"].setForeground(0, QColor(config["color"]) if plots else QColor())
        config["item"].setToolTip(0, f"{len(plots)} 个成员已绑定波形" if plots else "")
        for index, item in config["member_items"].items():
            item.setForeground(0, QColor(config["color"]) if index in plots else QColor())
            views = "".join(
                "AB"[view] for view in range(2) if index in config["plot_views"][view]
            )
            item.setToolTip(0, f"已绑定波形 {views}" if views else "")

    def _show_parser_context_menu(self, position) -> None:
        item = self.parser_tree.itemAt(position)
        config, member = self._parser_for_item(item)
        if config is None:
            return
        self.parser_tree.setCurrentItem(item)
        menu = GlassMenu(self)
        is_structure = member < 0
        active_view = self.active_wave_plot_index
        view_name = "AB"[active_view]
        if is_structure:
            bind_action = menu.addAction(f"全部添加到波形 {view_name}")
            unbind_action = menu.addAction("全部移出两个波形")
            rename_action = menu.addAction("重命名结构")
        else:
            bind_action = menu.addAction(f"添加到波形 {view_name}")
            unbind_action = menu.addAction("移出两个波形")
            rename_action = menu.addAction("重命名变量" if config["count"] == 1 else "重命名成员")
            bind_action.setEnabled(member not in config["plot_views"][active_view])
            unbind_action.setEnabled(member in config["plots"])
        menu.addSeparator()
        delete_action = menu.addAction("删除结构" if is_structure else ("删除变量" if config["count"] == 1 else "删除此成员"))
        chosen = menu.exec(self.parser_tree.viewport().mapToGlobal(position))
        if chosen == bind_action:
            indices = config["enabled"] if member < 0 else {member}
            for index in indices:
                self._set_wave_binding(config, index, active_view, True)
            self.status_label.setText(f"“{config['name']}”已添加到波形 {view_name}")
        elif chosen == unbind_action:
            indices = set(config["plots"]) if member < 0 else {member}
            for index in indices:
                for view in range(2):
                    self._set_wave_binding(config, index, view, False)
            self.status_label.setText(f"已更新“{config['name']}”的波形绑定")
        elif chosen == rename_action:
            self._rename_parser_item(config, member)
        elif chosen == delete_action:
            self._remove_parser_item(config, member)

    def _remove_parser(self) -> None:
        config, member = self._parser_for_item(self.parser_tree.currentItem())
        if config is None:
            return
        self._remove_parser_item(config, member)

    def _clear_all_parsers(self) -> None:
        """Remove every parser and waveform binding in one operation."""
        self.parsers.clear()
        self.parser_tree.clear()
        for plot in self.live_plots:
            plot.clear_all_series()
        self.alarm_rules.clear()
        self._rebuild_alarm_rule_table()
        self.wave_recorder.clear()
        self._wave_count_dirty = False
        self.wave_record_count.setText("已记录 0 行")
        self.status_label.setText("已清空全部解析通道、波形绑定和报警规则")

    def _remove_parser_item(self, config: dict, member: int) -> None:
        if member < 0 or config["count"] == 1:
            removed_channels = {
                self._series_name(config, index) for index in range(config["count"])
            }
            self.parsers.remove(config)
            self.parser_tree.takeTopLevelItem(self.parser_tree.indexOfTopLevelItem(config["item"]))
            for plot in self.live_plots:
                plot.remove_series(config["name"])
            self._remove_alarm_rules_for_channels(removed_channels)
            self.status_label.setText(f"已删除解析结构“{config['name']}”")
            return
        removed_channel = self._series_name(config, member)
        config["enabled"].discard(member)
        config["plots"].discard(member)
        for view, plot in enumerate(self.live_plots):
            config["plot_views"][view].discard(member)
            plot.remove_series(self._series_name(config, member))
        item = config["member_items"].pop(member)
        config["item"].removeChild(item)
        self._remove_alarm_rules_for_channels({removed_channel})
        self._refresh_parser_binding_text(config)
        self.status_label.setText(f"已删除成员“{config['members'][member]}”")

    def _rename_parser_from_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        config, member = self._parser_for_item(item)
        if config is not None:
            self._rename_parser_item(config, member)

    def _rename_parser_item(self, config: dict, member: int) -> None:
        is_single = config["count"] == 1
        old_name = config["name"] if member < 0 or is_single else config["members"][member]
        new_name, accepted = QInputDialog.getText(self, "重命名", "名称", text=old_name)
        new_name = new_name.strip()
        if not accepted or not new_name or new_name == old_name:
            return
        old_series = {index: self._series_name(config, index) for index in range(config["count"])}
        if member < 0 or is_single:
            config["name"] = new_name
            if is_single:
                config["members"][0] = new_name
            config["item"].setText(0, new_name)
        else:
            config["members"][member] = new_name
            config["member_items"][member].setText(0, new_name)
        for index, previous_name in old_series.items():
            current_name = self._series_name(config, index)
            for plot in self.live_plots:
                plot.rename_series(previous_name, current_name)
            for rule in self.alarm_rules:
                if rule.channel == previous_name:
                    rule.channel = current_name
        self._rebuild_alarm_rule_table()
        self.status_label.setText(f"已重命名为“{new_name}”")

    def _remove_alarm_rules_for_channels(self, channels: set[str]) -> None:
        if not channels:
            return
        self.alarm_rules = [rule for rule in self.alarm_rules if rule.channel not in channels]
        self._rebuild_alarm_rule_table()

    def _parse_frame_channels(
        self, frame: RtuFrame, update_display: bool = True, poll_id: str | None = None,
    ) -> None:
        waveform_values: dict[str, float | int] = {}
        for config in self.parsers:
            if config["source"] == "data":
                if config.get("poll_id") and config["poll_id"] != poll_id:
                    continue
                task = self._task_by_id(config.get("poll_id"))
                if task is None:
                    continue
                source = modbus_data_area(frame.raw)
                lane = config.get("byte_lane", 0) if type_size(config["type"]) == 1 else 0
                offset = (config["register"] - task["start"]) * 2 + lane
            else:
                # Backward compatibility for projects created before parsers were
                # bound exclusively through a data-block ID.
                if config["address"] and frame.address != config["address"]:
                    continue
                if config["function"] >= 0 and frame.function != config["function"]:
                    continue
                source = frame.raw
                offset = config["offset"]
            try:
                values = parse_typed_values(
                    source,
                    config["type"],
                    offset,
                    config["count"],
                    config["order"],
                    config["scale"],
                )
            except ValueError as exc:
                if not update_display:
                    continue
                message = f"等待数据 · {exc}"
                self._set_tree_text(config["item"], 2, "等待数据")
                if config["item"].toolTip(2) != message:
                    config["item"].setToolTip(2, message)
                for item in config["member_items"].values():
                    self._set_tree_text(item, 2, "等待数据")
                    if item.toolTip(2) != message:
                        item.setToolTip(2, message)
                continue
            if update_display:
                display = ", ".join(f"{value:.6g}" if isinstance(value, float) else str(value) for value in values)
                parent_display = display if config["count"] == 1 else "已更新"
                self._set_tree_text(config["item"], 2, parent_display)
                if config["item"].toolTip(2) != display:
                    config["item"].setToolTip(2, display)
            for index, value in enumerate(values):
                if index not in config["enabled"]:
                    continue
                item = config["member_items"].get(index)
                if update_display and item:
                    self._set_tree_text(item, 2, f"{value:.8g}" if isinstance(value, float) else str(value))
                    if item.toolTip(2):
                        item.setToolTip(2, "")
                series_name = self._series_name(config, index)
                self._evaluate_alarms(series_name, value, frame)
                if update_display and index in config["plots"]:
                    color_index = (self.parsers.index(config) + index) % len(self.parser_colors)
                    for view, members in enumerate(config["plot_views"]):
                        if index in members:
                            self.live_plots[view].add_sample(
                                series_name, float(value), self.parser_colors[color_index]
                            )
                    waveform_values[series_name] = value
        if update_display and waveform_values:
            self.wave_recorder.append(frame.timestamp, waveform_values)
            self._wave_count_dirty = True

    @staticmethod
    def _set_tree_text(item: QTreeWidgetItem, column: int, text: str) -> None:
        if item.text(column) != text:
            item.setText(column, text)

    def _builder_mode_changed(self) -> None:
        automatic = self.auto_address.isChecked() and self.auto_function.isChecked()
        self.builder_address.setVisible(self.auto_address.isChecked())
        self.builder_function.setVisible(self.auto_function.isChecked())
        self.builder_start.setVisible(automatic)
        self.builder_quantity.setVisible(automatic)
        self.builder_values.setVisible(automatic)
        self.manual_hex.setVisible(not automatic)
        self._update_builder()

    def _parse_values(self) -> list[int]:
        text = self.builder_values.text().strip()
        if not text:
            return []
        values = []
        for token in text.replace(";", ",").replace(" ", ",").split(","):
            if token.strip():
                values.append(int(token.strip(), 0))
        return values

    def _update_builder(self) -> None:
        function = self.builder_function.currentData()
        is_read = function in (1, 2, 3, 4)
        self.builder_quantity.setEnabled(is_read)
        self.builder_values.setEnabled(not is_read)
        try:
            if self.auto_address.isChecked() and self.auto_function.isChecked():
                data = build_request(
                    self.builder_address.value(),
                    function,
                    self.builder_start.value(),
                    self.builder_quantity.value(),
                    self._parse_values(),
                )
                if not self.auto_crc.isChecked():
                    data = data[:-2]
            else:
                data = parse_hex(self.manual_hex.text())
                if self.auto_address.isChecked():
                    data = bytes((self.builder_address.value(),)) + data
                if self.auto_crc.isChecked() and data:
                    data = append_crc(data)
            self._last_preview = data
            self.preview_text.setPlainText(hex_text(data))
            self.preview_hint.setText(f"{len(data)} bytes · 帧字段已自动补全")
            self.preview_hint.setStyleSheet("color:#59d8c5")
            self.send_button.setEnabled(bool(data))
        except (ValueError, OverflowError) as exc:
            self._last_preview = b""
            self.preview_text.setPlainText("等待有效输入…")
            self.preview_hint.setText(str(exc))
            self.preview_hint.setStyleSheet("color:#ff7587")
            self.send_button.setEnabled(False)

    def send_frame(self) -> None:
        if not self._last_preview:
            return
        if self.poll_active:
            self.status_label.setText("安全轮询运行中，请停止轮询后再手动发送")
            return
        if not self.worker or not self.worker.isRunning():
            self.status_label.setText("发送前请先连接串口")
            if not self.send_timer.isActive():
                QMessageBox.information(self, "尚未连接", "请先选择并连接串口，再发送 RTU 帧。")
            self.repeat_send.setChecked(False)
            return
        try:
            self.worker.send(self._last_preview)
        except serial.SerialException as exc:
            self._on_serial_error(str(exc))
            return
        self.tx_bytes += len(self._last_preview)
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self._on_frame(self._last_preview, "TX")

    def _toggle_repeat(self, enabled: bool) -> None:
        if enabled:
            self.send_timer.start(self.repeat_interval.value())
        else:
            self.send_timer.stop()

    def _apply_preset(self, baudrate: int, parity: str) -> None:
        self.baud_combo.setCurrentText(str(baudrate))
        self.data_bits.setCurrentText("8")
        self.parity.setCurrentIndex(self.parity.findData(parity))
        self.stop_bits.setCurrentIndex(self.stop_bits.findData(1))
        self.status_label.setText(f"已应用预设：{baudrate} · 8{parity}1")

    def clear_data(self) -> None:
        self.frames.clear()
        self._raw_pending.clear()
        self._overview_dirty = False
        self.frame_model.removeRows(0, self.frame_model.rowCount())
        if self.poll_active:
            self._ensure_poll_rows()
        self.raw_text.clear()
        self.detail_text.clear()
        self.byte_overview.clear()
        for plot in self.live_plots:
            plot.clear()
        self.wave_recorder.clear()
        self._wave_count_dirty = False
        self.wave_record_count.setText("已记录 0 行")
        for config in self.parsers:
            config["item"].setText(2, "等待数据")
            for item in config["member_items"].values():
                item.setText(2, "等待数据")
        self.rx_bytes = self.tx_bytes = self.good_frames = self.bad_frames = 0
        for label in (self.rx_metric, self.tx_metric, self.good_metric, self.bad_metric):
            label.setText("0")
        self.status_label.setText("数据已清空")

    def _restore_settings(self) -> None:
        config = self.settings.serial_config()
        self.baud_combo.setCurrentText(str(config["baudrate"]))
        self.data_bits.setCurrentText(str(config["bytesize"]))
        index = self.parity.findData(config["parity"])
        if index >= 0:
            self.parity.setCurrentIndex(index)
        index = self.stop_bits.findData(config["stopbits"])
        if index >= 0:
            self.stop_bits.setCurrentIndex(index)
        theme = self.settings.value("appearance/theme", "light")
        theme_index = self.theme_combo.findData(theme)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(0, theme_index))
        self.theme_combo.blockSignals(False)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(DARK_STYLE if theme == "dark" else LIGHT_STYLE)
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.settings.set("window/geometry", self.saveGeometry())
        self._stop_polling(silent=True)
        if self.worker:
            if not self.worker.stop():
                self.status_label.setText("串口线程仍在退出，请稍后再关闭")
                event.ignore()
                return
        if self.export_worker and self.export_worker.isRunning():
            if not self.export_worker.wait(3000):
                self.status_label.setText("CSV仍在导出，请稍后再关闭")
                event.ignore()
                return
        for timer_name in ("raw_flush_timer", "overview_refresh_timer", "port_scan_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        event.accept()
