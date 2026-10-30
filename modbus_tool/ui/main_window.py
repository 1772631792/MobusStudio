"""Main application window."""

from __future__ import annotations

from collections import deque
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
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modbus_tool.core.modbus import (
    FUNCTION_NAMES,
    RtuFrame,
    append_crc,
    build_request,
    hex_text,
    parse_hex,
)
from modbus_tool.core.data_parser import encode_typed_values, modbus_data_area, parse_typed_values, type_size
from modbus_tool.core.project_config import PROJECT_SCHEMA, PROJECT_VERSION, validate_project
from modbus_tool.core.serial_worker import SerialWorker
from modbus_tool.core.settings import Settings
from modbus_tool.core.wave_recorder import CsvExportWorker, WaveRecorder
from modbus_tool.ui.styles import DARK_STYLE, LIGHT_STYLE
from modbus_tool.ui.widgets import HorizontalTextSideTabBar, LivePlotWidget


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
        addr = model.index(source_row, 2, source_parent).data(Qt.ItemDataRole.UserRole)
        func = model.index(source_row, 3, source_parent).data(Qt.ItemDataRole.UserRole)
        crc_ok = model.index(source_row, 5, source_parent).data(Qt.ItemDataRole.UserRole)
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
        self.wave_recorder = WaveRecorder(max_rows=200_000)
        self.export_worker: CsvExportWorker | None = None
        self._raw_pending: deque[str] = deque(maxlen=5000)
        self._overview_dirty = False
        self._wave_count_dirty = False

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
        recent = QWidget()
        recent_layout = QHBoxLayout(recent)
        recent_layout.setContentsMargins(8, 0, 8, 0)
        recent_layout.setSpacing(5)
        recent_layout.addWidget(QLabel("最近帧  站号"))
        self.recent_address = QLabel("---")
        self.recent_address.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_address.setFixedWidth(42)
        recent_layout.addWidget(self.recent_address)
        recent_layout.addWidget(QLabel("·"))
        self.recent_function = QLabel("等待数据")
        self.recent_function.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_function.setFixedWidth(132)
        recent_layout.addWidget(self.recent_function)
        recent_layout.addWidget(QLabel("·"))
        self.recent_bytes = QLabel("0")
        self.recent_bytes.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.recent_bytes.setFixedWidth(52)
        recent_layout.addWidget(self.recent_bytes)
        recent_layout.addWidget(QLabel("bytes  ·  CRC"))
        self.recent_crc = QLabel("---")
        self.recent_crc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_crc.setFixedWidth(48)
        recent_layout.addWidget(self.recent_crc)
        status.addPermanentWidget(recent)
        status.addPermanentWidget(QLabel("RTU  |  v0.2.0"))
        self.setStatusBar(status)

    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="topBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 12, 10)

        logo = QLabel("M")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(34, 34)
        logo.setStyleSheet("background:#00bfa5;color:#06201c;border-radius:9px;font-size:18px;font-weight:900")
        layout.addWidget(logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(QLabel("MODBUS STUDIO", objectName="brand"))
        title_box.addWidget(QLabel("清爽、可靠的 RTU 调试台", objectName="muted"))
        layout.addLayout(title_box)
        layout.addStretch()

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
        self.view_buttons: list[QPushButton] = []
        for index, label in enumerate(("帧列表", "原始数据", "数据概览", "实时波形")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self._switch_view(i))
            self.view_buttons.append(button)
            tools.addWidget(button)
        self.view_buttons[0].setChecked(True)
        tools.addStretch()
        self.auto_scroll = QCheckBox("自动滚动")
        self.auto_scroll.setChecked(True)
        tools.addWidget(self.auto_scroll)
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
        layout.addWidget(self.views, 1)
        return panel

    def _build_frame_view(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.frame_model = QStandardItemModel(0, 7, self)
        self.frame_model.setHorizontalHeaderLabels(
            ["时间", "方向", "站号", "功能", "摘要", "CRC", "原始帧"]
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
        self.frame_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.frame_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.frame_table.setColumnWidth(0, 105)
        self.frame_table.setColumnWidth(1, 58)
        self.frame_table.setColumnWidth(2, 62)
        self.frame_table.setColumnWidth(3, 135)
        self.frame_table.setColumnWidth(5, 70)
        self.frame_table.selectionModel().selectionChanged.connect(self._show_selected_frame)
        splitter.addWidget(self.frame_table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(14, 8, 14, 10)
        detail_layout.addWidget(QLabel("帧详情", objectName="sectionTitle"))
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumBlockCount(300)
        self.detail_text.setPlaceholderText("选择一条帧，查看地址、功能、负载及 CRC 信息")
        detail_layout.addWidget(self.detail_text)
        splitter.addWidget(detail)
        splitter.setSizes([560, 150])
        return splitter

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
        title_box.addWidget(QLabel("进入本页会打开“解析”，右键通道即可绑定波形", objectName="muted"))
        header.addLayout(title_box)
        header.addStretch()
        self.wave_record_count = QLabel("已记录 0 行", objectName="muted")
        header.addWidget(self.wave_record_count)
        self.live_plot = LivePlotWidget()
        reset_plot = QPushButton("适应视图")
        reset_plot.setToolTip("恢复自动量程并回到最新数据；也可在绘图区双击")
        reset_plot.clicked.connect(self.live_plot.reset_view)
        self.export_csv_button = QPushButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._export_wave_csv)
        header.addWidget(self.export_csv_button)
        clear_plot = QPushButton("清空波形")
        clear_plot.clicked.connect(self._clear_wave_data)
        header.addWidget(clear_plot)
        header.insertWidget(header.count() - 2, reset_plot)
        layout.addLayout(header)
        layout.addWidget(self.live_plot, 1)
        interaction = QHBoxLayout()
        self.wave_view_state = QLabel("横向 1.00×  ·  纵向 1.00×  ·  最新", objectName="muted")
        interaction.addWidget(self.wave_view_state)
        interaction.addStretch()
        interaction.addWidget(QLabel("滚轮：时间宽度  ·  Ctrl+滚轮：纵向  ·  拖拽：回看  ·  双击：复位", objectName="muted"))
        self.live_plot.viewport_changed.connect(self.wave_view_state.setText)
        layout.addLayout(interaction)
        return widget

    def _clear_wave_data(self) -> None:
        self.live_plot.clear()
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

    def _build_right_tabs(self) -> QWidget:
        self.right_tabs = QTabWidget()
        # The tab stack is vertical on the right, but labels are custom-painted horizontally.
        self.right_tabs.setTabBar(HorizontalTextSideTabBar())
        self.right_tabs.setTabPosition(QTabWidget.TabPosition.East)
        self.right_tabs.setMinimumWidth(500)
        self.right_tabs.setMaximumWidth(650)
        self.receive_page = self._build_receive_tab()
        self.right_tabs.addTab(self.receive_page, "接收")
        self.right_tabs.addTab(self._build_poll_tab(), "轮询")
        self.parse_page = self._build_parse_tab()
        self.right_tabs.addTab(self.parse_page, "解析")
        self.write_page = self._build_write_tab()
        self.right_tabs.addTab(self.write_page, "写入")
        self.right_tabs.addTab(self._build_send_tab(), "构帧")
        self.right_tabs.addTab(self._build_serial_tab(), "串口")
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

    def _build_receive_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("接收过滤", objectName="sectionTitle"))
        layout.addWidget(QLabel("只影响显示，不丢弃底层数据", objectName="muted"))
        form = QFormLayout()
        self.filter_address = QSpinBox()
        self.filter_address.setRange(0, 247)
        self.filter_address.setSpecialValueText("全部")
        self.filter_address.valueChanged.connect(self._apply_filters)
        form.addRow("从站地址", self.filter_address)
        self.filter_function = QComboBox()
        self.filter_function.addItem("全部功能码", -1)
        for text, code in FUNCTION_OPTIONS:
            self.filter_function.addItem(text, code)
        self.filter_function.currentIndexChanged.connect(self._apply_filters)
        form.addRow("功能码", self.filter_function)
        self.filter_crc = QComboBox()
        self.filter_crc.addItems(["全部", "仅 CRC 正确", "仅 CRC 异常"])
        self.filter_crc.currentIndexChanged.connect(self._apply_filters)
        form.addRow("CRC 状态", self.filter_crc)
        self.filter_keyword = QLineEdit()
        self.filter_keyword.setPlaceholderText("搜索 HEX / 摘要")
        self.filter_keyword.textChanged.connect(self._apply_filters)
        form.addRow("关键字", self.filter_keyword)
        layout.addLayout(form)

        layout.addSpacing(8)
        layout.addWidget(QLabel("显示偏好", objectName="sectionTitle"))
        self.show_timestamp = QCheckBox("显示毫秒时间戳")
        self.show_timestamp.setChecked(True)
        self.uppercase_hex = QCheckBox("HEX 使用大写")
        self.uppercase_hex.setChecked(True)
        self.keep_limit = QSpinBox()
        self.keep_limit.setRange(100, 100000)
        self.keep_limit.setValue(5000)
        self.keep_limit.setSuffix(" 帧")
        layout.addWidget(self.show_timestamp)
        layout.addWidget(self.uppercase_hex)
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("缓存上限"))
        limit_row.addWidget(self.keep_limit)
        layout.addLayout(limit_row)
        layout.addStretch()
        tip = QLabel("提示  CRC 异常常由波特率、校验位或 RTU 分帧间隔不匹配引起。")
        tip.setWordWrap(True)
        tip.setStyleSheet("background:#12252a;color:#72cfc1;padding:10px;border-radius:7px")
        layout.addWidget(tip)
        return page

    def _build_poll_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("连续读取轮询", objectName="sectionTitle"))
        layout.addWidget(QLabel("严格按“请求 → 等待响应 → 再请求”运行", objectName="muted"))
        form = QFormLayout()
        self.poll_address = QSpinBox()
        self.poll_address.setRange(1, 247)
        self.poll_address.setValue(1)
        self.poll_function = QComboBox()
        for text, code in FUNCTION_OPTIONS[:4]:
            self.poll_function.addItem(text, code)
        self.poll_function.setCurrentIndex(2)
        self.poll_function.currentIndexChanged.connect(self._poll_function_changed)
        self.poll_start = QSpinBox()
        self.poll_start.setRange(0, 65535)
        self.poll_start.setValue(0)
        self.poll_quantity = QSpinBox()
        self.poll_quantity.setRange(1, 125)
        self.poll_quantity.setValue(10)
        self.poll_interval = QSpinBox()
        self.poll_interval.setRange(0, 60000)
        self.poll_interval.setValue(50)
        self.poll_interval.setSuffix(" ms")
        self.poll_interval.setToolTip("收到完整响应后，等待此时间再发送下一请求")
        self.poll_timeout = QSpinBox()
        self.poll_timeout.setRange(50, 10000)
        self.poll_timeout.setValue(500)
        self.poll_timeout.setSuffix(" ms")
        form.addRow("从站地址", self.poll_address)
        form.addRow("读取功能", self.poll_function)
        form.addRow("起始寄存器", self.poll_start)
        form.addRow("寄存器数量", self.poll_quantity)
        form.addRow("响应后间隔", self.poll_interval)
        form.addRow("响应超时", self.poll_timeout)
        layout.addLayout(form)

        poll_hint = QLabel("解析通道直接填写绝对寄存器地址；软件会根据本轮询起点自动定位数据。")
        poll_hint.setWordWrap(True)
        poll_hint.setObjectName("muted")
        layout.addWidget(poll_hint)

        self.poll_status = QLabel("尚未开始")
        self.poll_status.setWordWrap(True)
        self.poll_status.setStyleSheet("background:#e7f4f8;color:#187195;padding:10px;border-radius:7px")
        layout.addWidget(self.poll_status)
        layout.addStretch()
        self.poll_button = QPushButton("开始安全轮询", objectName="primary")
        self.poll_button.clicked.connect(self._toggle_polling)
        layout.addWidget(self.poll_button)

        self.poll_next_timer = QTimer(self)
        self.poll_next_timer.setSingleShot(True)
        self.poll_next_timer.timeout.connect(self._send_poll_request)
        self.poll_timeout_timer = QTimer(self)
        self.poll_timeout_timer.setSingleShot(True)
        self.poll_timeout_timer.timeout.connect(self._poll_timed_out)
        return page

    def _build_parse_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("数据解析通道", objectName="sectionTitle"))
        layout.addWidget(QLabel("从接收帧中直接提取数值，并可绑定实时波形", objectName="muted"))
        bind_hint = QLabel("解析一个时通道就是变量；解析多个时可展开成员，双击即可改名")
        bind_hint.setWordWrap(True)
        bind_hint.setStyleSheet("color:#0879be")
        layout.addWidget(bind_hint)
        form = QFormLayout()
        self.parser_form = form
        self.parser_name = QLineEdit()
        self.parser_name.setPlaceholderText("例：温度")
        self.parser_type = QComboBox()
        self.parser_type.addItems(
            ["uint8", "int8", "uint16", "int16", "uint32", "int32", "float32", "float64"]
        )
        self.parser_order = QComboBox()
        self.parser_order.addItem("大端 / ABCD", "big")
        self.parser_order.addItem("小端 / DCBA", "little")
        self.parser_order.addItem("字交换 / CDAB", "word_swap")
        self.parser_order.addItem("字节交换 / BADC", "byte_swap")
        self.parser_source = QComboBox()
        self.parser_source.addItem("Modbus 数据区", "data")
        self.parser_source.addItem("完整 RTU 帧", "frame")
        self.parser_address = QSpinBox()
        self.parser_address.setRange(0, 247)
        self.parser_address.setSpecialValueText("全部")
        self.parser_function = QComboBox()
        self.parser_function.addItem("全部功能码", -1)
        for text, code in FUNCTION_OPTIONS:
            self.parser_function.addItem(text, code)
        self.parser_offset = QSpinBox()
        self.parser_offset.setRange(0, 65535)
        self.parser_offset.setValue(self.poll_start.value())
        self.parser_offset.setToolTip("填写设备手册中的绝对寄存器地址，例如1103；float32下一项通常为1105")
        self.parser_offset_label = QLabel("起始寄存器")
        self.parser_byte_lane = QComboBox()
        self.parser_byte_lane.addItem("高字节", 0)
        self.parser_byte_lane.addItem("低字节", 1)
        self.parser_byte_lane.setEnabled(False)
        self.parser_count = QSpinBox()
        self.parser_count.setRange(1, 128)
        self.parser_scale = QLineEdit("1.0")
        self.parser_scale.setPlaceholderText("倍率，例如 0.1")
        form.addRow("通道名称", self.parser_name)
        form.addRow("数据类型", self.parser_type)
        form.addRow("字节序", self.parser_order)
        form.addRow("数据来源", self.parser_source)
        form.addRow("从站过滤", self.parser_address)
        form.addRow("功能过滤", self.parser_function)
        form.addRow(self.parser_offset_label, self.parser_offset)
        form.addRow("8位字节位置", self.parser_byte_lane)
        form.addRow("解析数量", self.parser_count)
        form.addRow("缩放倍率", self.parser_scale)
        layout.addLayout(form)
        self.parser_offset_hint = QLabel()
        self.parser_offset_hint.setWordWrap(True)
        self.parser_offset_hint.setStyleSheet("background:#e7f4f8;color:#187195;padding:8px;border-radius:6px")
        layout.addWidget(self.parser_offset_hint)
        self.parser_type.currentTextChanged.connect(self._update_parser_offset_hint)
        self.parser_source.currentIndexChanged.connect(self._update_parser_offset_hint)
        self.parser_offset.valueChanged.connect(self._update_parser_offset_hint)
        self.parser_count.valueChanged.connect(self._update_parser_offset_hint)
        self.parser_byte_lane.currentIndexChanged.connect(self._update_parser_offset_hint)
        self._last_poll_start = self.poll_start.value()
        self.poll_start.valueChanged.connect(self._sync_parser_register_with_poll)
        self._update_parser_offset_hint()
        add_parser = QPushButton("添加解析结构", objectName="primary")
        add_parser.clicked.connect(self._add_parser)
        layout.addWidget(add_parser)

        self.parser_tree = QTreeWidget()
        self.parser_tree.setColumnCount(4)
        self.parser_tree.setHeaderLabels(["变量 / 结构", "地址 · 类型", "当前值", "波形"])
        self.parser_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.parser_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.parser_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.parser_tree.customContextMenuRequested.connect(self._show_parser_context_menu)
        self.parser_tree.itemDoubleClicked.connect(self._rename_parser_from_double_click)
        self.parser_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.parser_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.parser_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.parser_tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.parser_tree.header().resizeSection(1, 110)
        self.parser_tree.header().resizeSection(2, 85)
        self.parser_tree.header().resizeSection(3, 85)
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
        clear_parsers.setToolTip("删除全部解析变量、结构和波形绑定")
        clear_parsers.clicked.connect(self._clear_all_parsers)
        parser_actions.addWidget(clear_parsers)
        layout.addLayout(parser_actions)
        return page

    def _build_write_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("类型化数据写入", objectName="sectionTitle"))
        note = QLabel("直接填写数值，软件自动编码数据、选择写功能码并补齐CRC")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

        form = QFormLayout()
        self.write_form = form
        self.write_address = QSpinBox()
        self.write_address.setRange(1, 247)
        self.write_address.setValue(1)
        self.write_target = QComboBox()
        self.write_target.addItem("保持寄存器", "register")
        self.write_target.addItem("线圈", "coil")
        self.write_start = QSpinBox()
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
            self.write_button.setEnabled(True)
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
        if self.poll_active:
            self.status_label.setText("安全轮询运行中，请先停止轮询再执行写入")
            return
        if not self.worker or not self.worker.isRunning():
            QMessageBox.information(self, "尚未连接", "请先连接串口，再发送写入命令。")
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
        try:
            self.worker.send(self._last_write_frame)
        except serial.SerialException as exc:
            self._on_serial_error(str(exc))
            return
        self.tx_bytes += len(self._last_write_frame)
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self._on_frame(self._last_write_frame, "TX")
        self.status_label.setText("写入命令已发送，等待从站响应")

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
        self.builder_address = QSpinBox()
        self.builder_address.setRange(1, 247)
        self.builder_address.setValue(1)
        self.builder_function = QComboBox()
        for text, code in FUNCTION_OPTIONS:
            self.builder_function.addItem(text, code)
        self.builder_start = QSpinBox()
        self.builder_start.setRange(0, 65535)
        self.builder_start.setDisplayIntegerBase(10)
        self.builder_quantity = QSpinBox()
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
        self.repeat_interval = QSpinBox()
        self.repeat_interval.setRange(20, 600000)
        self.repeat_interval.setValue(1000)
        self.repeat_interval.setSuffix(" ms")
        repeat_row.addWidget(self.repeat_send)
        repeat_row.addWidget(self.repeat_interval)
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

    def _build_serial_tab(self) -> QWidget:
        page, layout = self._tab_page()
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
        return page

    def _build_settings_tab(self) -> QWidget:
        page, layout = self._tab_page()
        layout.addWidget(QLabel("外观设置", objectName="sectionTitle"))
        layout.addWidget(QLabel("默认使用接近 VOFA+ 的清爽明亮配色", objectName="muted"))
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
        layout.addWidget(QLabel("工程 JSON", objectName="sectionTitle"))
        project_hint = QLabel(
            "导出当前串口、轮询、解析、波形绑定和界面配置。JSON 可直接交给 AI 修改，再一键导入还原。"
        )
        project_hint.setWordWrap(True)
        project_hint.setObjectName("muted")
        layout.addWidget(project_hint)
        project_actions = QHBoxLayout()
        export_project = QPushButton("导出配置 JSON", objectName="primary")
        export_project.clicked.connect(self._export_project_json)
        import_project = QPushButton("导入配置 JSON")
        import_project.clicked.connect(self._import_project_json)
        project_actions.addWidget(export_project)
        project_actions.addWidget(import_project)
        layout.addLayout(project_actions)
        safety_hint = QLabel("导入只恢复配置，不会自动连接串口、开始轮询或发送数据。")
        safety_hint.setWordWrap(True)
        safety_hint.setStyleSheet("color:#209567")
        layout.addWidget(safety_hint)
        layout.addStretch()
        return page

    @staticmethod
    def _set_combo_data(combo: QComboBox, value, field: str) -> None:
        index = combo.findData(value)
        if index < 0:
            raise ValueError(f"{field} 的值不受当前软件支持：{value!r}")
        combo.setCurrentIndex(index)

    def _project_document(self) -> dict:
        """Capture configuration only; live samples and running state are excluded."""
        view_names = ("frames", "raw", "overview", "waveform")
        tab_names = ("receive", "poll", "parse", "write", "builder", "serial", "settings")
        parser_documents = []
        for config in self.parsers:
            parser_documents.append({
                "name": config["name"],
                "type": config["type"],
                "order": config["order"],
                "source": config["source"],
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
            })
        return {
            "schema": PROJECT_SCHEMA,
            "version": PROJECT_VERSION,
            "application": "Modbus Studio RTU",
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ai_help": {
                "purpose": "可由人或 AI 编辑的 Modbus Studio 工程配置",
                "safety": "导入不会自动连接串口、启动轮询、循环发送或写入设备",
                "parser_addressing": "source=data 时 register 是绝对寄存器地址；count 是数值个数，不是寄存器个数",
                "byte_orders": {"big": "ABCD", "little": "DCBA", "word_swap": "CDAB", "byte_swap": "BADC"},
            },
            "serial": self._serial_config(),
            "poll": {
                "address": self.poll_address.value(),
                "function": self.poll_function.currentData(),
                "start": self.poll_start.value(),
                "quantity": self.poll_quantity.value(),
                "interval_ms": self.poll_interval.value(),
                "timeout_ms": self.poll_timeout.value(),
            },
            "parsers": parser_documents,
            "parser_sequence": self._parser_sequence,
            "receive": {
                "address_filter": self.filter_address.value(),
                "function_filter": self.filter_function.currentData(),
                "crc_filter": self.filter_crc.currentIndex(),
                "keyword": self.filter_keyword.text(),
                "show_timestamp": self.show_timestamp.isChecked(),
                "uppercase_hex": self.uppercase_hex.isChecked(),
                "auto_scroll": self.auto_scroll.isChecked(),
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

        poll = document["poll"]
        self.poll_address.setValue(poll["address"])
        self._set_combo_data(self.poll_function, poll["function"], "poll.function")
        self.poll_start.setValue(poll["start"])
        self.poll_quantity.setValue(poll["quantity"])
        self.poll_interval.setValue(poll["interval_ms"])
        self.poll_timeout.setValue(poll["timeout_ms"])

        receive = document["receive"]
        self.filter_address.setValue(receive["address_filter"])
        self._set_combo_data(self.filter_function, receive["function_filter"], "receive.function_filter")
        self.filter_crc.setCurrentIndex(receive["crc_filter"])
        self.filter_keyword.setText(receive["keyword"])
        self.show_timestamp.setChecked(receive["show_timestamp"])
        self.uppercase_hex.setChecked(receive["uppercase_hex"])
        self.auto_scroll.setChecked(receive["auto_scroll"])
        self.keep_limit.setValue(receive["keep_limit"])

        self._clear_all_parsers()
        self._parser_sequence = document.get("parser_sequence", len(document["parsers"]))
        for saved in document["parsers"]:
            config = dict(saved)
            config["members"] = list(saved["members"])
            config["enabled"] = set(saved["enabled"])
            config["plots"] = set(saved["plots"])
            self._insert_parser(config)
            for index in set(range(config["count"])) - config["enabled"]:
                item = config["member_items"].pop(index, None)
                if item is not None and item is not config["item"]:
                    config["item"].removeChild(item)
            for index in config["plots"]:
                self._register_parser_member(config, index)
            self._refresh_parser_binding_text(config)

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
        main_views = {"frames": 0, "raw": 1, "overview": 2, "waveform": 3}
        right_tabs = {"receive": 0, "poll": 1, "parse": 2, "write": 3,
                      "builder": 4, "serial": 5, "settings": 6}
        self._switch_view(main_views[ui["main_view"]])
        self.right_tabs.setCurrentIndex(right_tabs[ui["right_tab"]])
        self.resize(ui["window_size"]["width"], ui["window_size"]["height"])
        self.body_splitter.setSizes(ui["splitter_sizes"])
        self.live_plot.restore_view_state(ui["wave_view"])
        self._apply_filters()
        self._update_builder()
        self._update_write_preview()

    def _switch_view(self, index: int) -> None:
        self.views.setCurrentIndex(index)
        for i, button in enumerate(self.view_buttons):
            button.setChecked(i == index)
        # One-shot contextual navigation: users can freely change the right tab afterwards.
        if not hasattr(self, "right_tabs"):
            return
        target_page = self.receive_page if index in (0, 1) else self.parse_page
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
        if self.worker:
            if not self.worker.stop():
                self.status_label.setText("串口线程仍在退出，请稍后重试")
                return
            self.worker = None
        self._on_serial_status(False, "已断开")

    def _on_serial_status(self, connected: bool, detail: str) -> None:
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
        if paused:
            self.status_label.setText("显示已暂停 · 串口接收和安全轮询仍在后台运行")
            return
        self.rx_metric.setText(f"{self.rx_bytes:,}")
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self.good_metric.setText(f"{self.good_frames:,}")
        self.bad_metric.setText(f"{self.bad_frames:,}")
        self.status_label.setText("显示已继续 · 等待下一帧数据")

    def _on_frame(self, raw: bytes, direction: str = "RX", poll_slot: str | None = None) -> None:
        frame = RtuFrame(raw, datetime.now(), direction)
        if direction == "RX" and self.poll_active:
            expected_function = self.poll_function.currentData()
            if (
                frame.address == self.poll_address.value()
                and frame.function in (expected_function, expected_function | 0x80)
            ):
                poll_slot = "rx"
        if self.pause_button.isChecked():
            if frame.crc_ok:
                self.good_frames += 1
            else:
                self.bad_frames += 1
            if direction == "RX":
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
        function = frame.function_name
        crc_label = "正确" if frame.crc_ok else "异常"
        raw_hex = hex_text(raw)
        if not self.uppercase_hex.isChecked():
            raw_hex = raw_hex.lower()
        row = [
            QStandardItem(stamp),
            QStandardItem(direction),
            QStandardItem(address),
            QStandardItem(function),
            QStandardItem(frame.summary),
            QStandardItem(crc_label),
            QStandardItem(raw_hex),
        ]
        row[2].setData(frame.address, Qt.ItemDataRole.UserRole)
        row[3].setData(frame.function, Qt.ItemDataRole.UserRole)
        row[5].setData(frame.crc_ok, Qt.ItemDataRole.UserRole)
        row[0].setData(frame, Qt.ItemDataRole.UserRole)
        if poll_slot:
            row[1].setData(poll_slot, Qt.ItemDataRole.UserRole + 1)
        color = QColor("#48d9c2" if direction == "RX" else "#68a7ff")
        row[1].setForeground(color)
        row[5].setForeground(QColor("#68df95" if frame.crc_ok else "#ff7185"))
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
            self._parse_frame_channels(frame)
        if direction == "RX":
            self._handle_poll_response(frame)
        if self.auto_scroll.isChecked():
            if not poll_slot:
                self.frame_table.scrollToBottom()
        self._overview_dirty = True
        self._set_label_text(self.recent_address, address)
        self._set_label_text(self.recent_function, function)
        self._set_label_text(self.recent_bytes, str(len(raw)))
        self._set_label_text(self.recent_crc, crc_label)
        self.recent_crc.setStyleSheet(f"color:{'#259b65' if frame.crc_ok else '#df5262'}")

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
        if self.auto_scroll.isChecked():
            scrollbar = self.raw_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _flush_overview(self) -> None:
        if self._wave_count_dirty:
            self._wave_count_dirty = False
            self.wave_record_count.setText(f"已记录 {len(self.wave_recorder):,} 行")
        if not self._overview_dirty:
            return
        self._overview_dirty = False
        self._update_byte_overview()

    def _find_poll_row(self, slot: str | None) -> int:
        if not slot:
            return -1
        for row in range(self.frame_model.rowCount()):
            item = self.frame_model.item(row, 1)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == slot:
                return row
        return -1

    def _ensure_poll_rows(self) -> None:
        for slot, direction, summary in (
            ("rx", "RX", "等待首个响应"),
            ("tx", "TX", "等待发送请求"),
        ):
            if self._find_poll_row(slot) >= 0:
                continue
            items = [
                QStandardItem("—"), QStandardItem(direction), QStandardItem("—"),
                QStandardItem("轮询"), QStandardItem(summary), QStandardItem("—"),
                QStandardItem("—"),
            ]
            items[1].setData(slot, Qt.ItemDataRole.UserRole + 1)
            items[1].setForeground(QColor("#48d9c2" if direction == "RX" else "#68a7ff"))
            self.frame_model.appendRow(items)

    def _trim_frame_rows(self) -> None:
        while self.frame_model.rowCount() > self.keep_limit.value():
            removable = -1
            for row in range(self.frame_model.rowCount()):
                item = self.frame_model.item(row, 1)
                if not item or item.data(Qt.ItemDataRole.UserRole + 1) not in ("rx", "tx"):
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
        frame = self.frame_model.item(source_index.row(), 0).data(Qt.ItemDataRole.UserRole)
        if not isinstance(frame, RtuFrame):
            return
        crc_received = int.from_bytes(frame.raw[-2:], "little") if len(frame.raw) >= 2 else 0
        lines = [
            f"时间     {frame.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}",
            f"方向     {frame.direction}",
            f"从站     {frame.address if frame.address is not None else '—'}",
            f"功能     {frame.function_name}",
            f"解析     {frame.summary}",
            f"CRC      {'通过' if frame.crc_ok else '未通过'} · 帧内 0x{crc_received:04X}",
            f"HEX      {hex_text(frame.raw)}",
        ]
        self.detail_text.setPlainText("\n".join(lines))

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
        self.frame_filter.address = -1 if self.filter_address.value() == 0 else self.filter_address.value()
        self.frame_filter.function = self.filter_function.currentData()
        self.frame_filter.crc_mode = self.filter_crc.currentIndex()
        self.frame_filter.keyword = self.filter_keyword.text().strip().lower()
        self.frame_filter.invalidateFilter()

    def _toggle_polling(self) -> None:
        if self.poll_active:
            self._stop_polling()
            return
        if not self.worker or not self.worker.isRunning():
            QMessageBox.information(self, "尚未连接", "请先连接串口，再启动寄存器轮询。")
            return
        try:
            self.poll_request = build_request(
                self.poll_address.value(),
                self.poll_function.currentData(),
                self.poll_start.value(),
                self.poll_quantity.value(),
            )
        except ValueError as exc:
            QMessageBox.information(self, "轮询参数无效", str(exc))
            return
        self.poll_active = True
        self.poll_waiting = False
        self.poll_success = 0
        self.poll_failures = 0
        self.repeat_send.setChecked(False)
        self._ensure_poll_rows()
        for widget in (self.poll_address, self.poll_function, self.poll_start, self.poll_quantity):
            widget.setEnabled(False)
        self.poll_button.setText("停止轮询")
        self.poll_button.setObjectName("danger")
        self.poll_button.style().unpolish(self.poll_button)
        self.poll_button.style().polish(self.poll_button)
        self._send_poll_request()

    def _stop_polling(self, silent: bool = False) -> None:
        was_active = self.poll_active
        self.poll_active = False
        self.poll_waiting = False
        if hasattr(self, "poll_next_timer"):
            self.poll_next_timer.stop()
            self.poll_timeout_timer.stop()
        if hasattr(self, "poll_button"):
            self.poll_button.setText("开始安全轮询")
            self.poll_button.setObjectName("primary")
            self.poll_button.style().unpolish(self.poll_button)
            self.poll_button.style().polish(self.poll_button)
            for widget in (self.poll_address, self.poll_function, self.poll_start, self.poll_quantity):
                widget.setEnabled(True)
        if was_active and not silent:
            self.status_label.setText("寄存器轮询已停止")

    def _send_poll_request(self) -> None:
        if not self.poll_active or self.poll_waiting:
            return
        if not self.worker or not self.worker.isRunning():
            self._stop_polling(silent=True)
            return
        try:
            # Rebuild for every cycle so parser-created range expansions take effect
            # on the next request without interrupting an active polling session.
            self.poll_request = build_request(
                self.poll_address.value(),
                self.poll_function.currentData(),
                self.poll_start.value(),
                self.poll_quantity.value(),
            )
            self.worker.send(self.poll_request)
        except serial.SerialException as exc:
            self._on_serial_error(str(exc))
            self._stop_polling(silent=True)
            return
        self.tx_bytes += len(self.poll_request)
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self._on_frame(self.poll_request, "TX", poll_slot="tx")
        self.poll_waiting = True
        self.poll_sent_quantity = self.poll_quantity.value()
        self.poll_sent_at = time.perf_counter()
        self.poll_timeout_timer.start(self.poll_timeout.value())
        self.poll_status.setText(
            f"运行中 · 成功 {self.poll_success} · 超时/异常 {self.poll_failures}\n"
            f"R{self.poll_start.value()}～R{self.poll_start.value() + self.poll_quantity.value() - 1}"
        )

    def _handle_poll_response(self, frame: RtuFrame) -> None:
        if not self.poll_active or not self.poll_waiting:
            return
        expected_address = self.poll_address.value()
        expected_function = self.poll_function.currentData()
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
            result = f"最近响应 {elapsed_ms:.1f} ms"
        else:
            self.poll_failures += 1
            result = "最近响应异常"
        self.poll_status.setText(
            f"运行中 · 成功 {self.poll_success} · 超时/异常 {self.poll_failures}\n{result}"
        )
        self.poll_next_timer.start(self.poll_interval.value())

    def _poll_timed_out(self) -> None:
        if not self.poll_active or not self.poll_waiting:
            return
        self.poll_waiting = False
        self.poll_failures += 1
        self.poll_status.setText(
            f"运行中 · 成功 {self.poll_success} · 超时/异常 {self.poll_failures}\n最近请求超时"
        )
        self.poll_next_timer.start(self.poll_interval.value())

    def _poll_function_changed(self) -> None:
        function = self.poll_function.currentData()
        self.poll_quantity.setMaximum(2000 if function in (1, 2) else 125)

    def _update_parser_offset_hint(self, *_args) -> None:
        if not hasattr(self, "parser_offset_hint"):
            return
        size = type_size(self.parser_type.currentText())
        is_8bit = size == 1
        data_source = self.parser_source.currentData() == "data"
        self.parser_byte_lane.setEnabled(is_8bit and data_source)
        self.parser_form.setRowVisible(self.parser_byte_lane, is_8bit and data_source)
        if self.parser_source.currentData() == "data":
            register_step = max(1, (size + 1) // 2)
            self.parser_offset.setSingleStep(register_step)
            first_register = self.parser_offset.value()
            lane = self.parser_byte_lane.currentData() if is_8bit else 0
            total_bytes = size * self.parser_count.value()
            last_register = first_register + (lane + total_bytes - 1) // 2
            internal_offset = (first_register - self.poll_start.value()) * 2 + lane
            self.parser_offset_label.setText("起始寄存器")
            location = (
                f"R{first_register}" if last_register == first_register
                else f"R{first_register}～R{last_register}"
            )
            validity = "" if internal_offset >= 0 else "；注意：该地址小于当前轮询起点"
            self.parser_offset_hint.setText(
                f"将解析 {location}；解析数量表示数值个数。"
                f"{self.parser_type.currentText()} 每个占 {register_step} 个寄存器，"
                f"轮询范围会自动补足{validity}。"
            )
        else:
            self.parser_offset.setSingleStep(size)
            start = self.parser_offset.value()
            end = start + size * self.parser_count.value() - 1
            self.parser_offset_label.setText("完整帧字节偏移")
            self.parser_offset_hint.setText(
                f"解析完整RTU帧 Byte {start}～{end}。"
                "完整帧包含从站地址、功能码、长度/负载和CRC，通常建议改用“Modbus数据区”。"
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
        config = {
            "name": name,
            "type": self.parser_type.currentText(),
            "order": self.parser_order.currentData(),
            "source": self.parser_source.currentData(),
            "address": self.parser_address.value(),
            "function": self.parser_function.currentData(),
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
        config["enabled"] = set(range(config["count"]))
        range_message = self._expand_poll_range_for_parser(config)
        self._insert_parser(config)
        self.parser_name.clear()
        self.status_label.setText(f"已添加解析通道“{name}”{range_message}")

    def _expand_poll_range_for_parser(self, config: dict) -> str:
        """Ensure a register poll includes every byte needed by a new parser."""
        if config["source"] != "data" or self.poll_function.currentData() not in (3, 4):
            return ""
        size = type_size(config["type"])
        lane = config.get("byte_lane", 0) if size == 1 else 0
        parser_start = config["register"]
        poll_start = self.poll_start.value()
        if parser_start < poll_start:
            return "；地址小于当前轮询起点，请调整轮询范围"
        required_end = parser_start + (lane + size * config["count"] + 1) // 2
        required_quantity = required_end - poll_start
        if required_quantity <= self.poll_quantity.value():
            return ""
        if required_quantity > 125:
            return f"；需要读取 {required_quantity} 个寄存器，超过单帧上限125"
        self.poll_quantity.setValue(required_quantity)
        suffix = "，下一轮生效" if self.poll_active else ""
        return f"；轮询数量已自动扩大为 {required_quantity}{suffix}"

    def _insert_parser(self, config: dict) -> None:
        self.parsers.append(config)
        uid = id(config)
        is_single = config["count"] == 1
        root = QTreeWidgetItem([
            config["name"],
            self._parser_member_location(config, 0) if is_single else f"{config['type']} × {config['count']}",
            "等待数据",
            "未绑定" if is_single else "0 个已绑定",
        ])
        root.setData(0, Qt.ItemDataRole.UserRole, uid)
        root.setData(0, Qt.ItemDataRole.UserRole + 1, 0 if is_single else -1)
        root.setForeground(0, QColor(config["color"]))
        self.parser_tree.addTopLevelItem(root)
        config["item"] = root
        config["member_items"] = {}
        if is_single:
            config["member_items"][0] = root
            return
        for index, member_name in enumerate(config["members"]):
            item = QTreeWidgetItem([member_name, self._parser_member_location(config, index), "等待数据", "未绑定"])
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
        return f"R{register}{suffix} · {config['type']}"

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

    def _register_parser_member(self, config: dict, index: int) -> None:
        color_index = (self.parsers.index(config) + index) % len(self.parser_colors)
        color = self.parser_colors[color_index]
        self.live_plot.register_series(self._series_name(config, index), color)

    def _refresh_parser_binding_text(self, config: dict) -> None:
        plots = config["plots"]
        if config["count"] == 1:
            config["item"].setText(3, "● 波形调试" if plots else "未绑定")
            config["item"].setForeground(3, QColor(config["color"]) if plots else QColor())
            return
        config["item"].setText(3, f"{len(plots)} 个已绑定" if plots else "未绑定")
        for index, item in config["member_items"].items():
            item.setText(3, "● 波形调试" if index in plots else "未绑定")
            item.setForeground(3, QColor(config["color"]) if index in plots else QColor())

    def _show_parser_context_menu(self, position) -> None:
        item = self.parser_tree.itemAt(position)
        config, member = self._parser_for_item(item)
        if config is None:
            return
        self.parser_tree.setCurrentItem(item)
        menu = QMenu(self)
        is_structure = member < 0
        if is_structure:
            bind_action = menu.addAction("全部添加到波形调试")
            unbind_action = menu.addAction("全部移出波形调试")
            rename_action = menu.addAction("重命名结构")
        else:
            bind_action = menu.addAction("添加到波形调试")
            unbind_action = menu.addAction("移出波形调试")
            rename_action = menu.addAction("重命名变量" if config["count"] == 1 else "重命名成员")
            bind_action.setEnabled(member not in config["plots"])
            unbind_action.setEnabled(member in config["plots"])
        menu.addSeparator()
        delete_action = menu.addAction("删除结构" if is_structure else ("删除变量" if config["count"] == 1 else "删除此成员"))
        chosen = menu.exec(self.parser_tree.viewport().mapToGlobal(position))
        if chosen == bind_action:
            indices = config["enabled"] if member < 0 else {member}
            for index in indices:
                config["plots"].add(index)
                self._register_parser_member(config, index)
            self._refresh_parser_binding_text(config)
            self.status_label.setText(f"“{config['name']}”已添加到波形调试")
        elif chosen == unbind_action:
            indices = set(config["plots"]) if member < 0 else {member}
            for index in indices:
                config["plots"].discard(index)
                self.live_plot.remove_series(self._series_name(config, index))
            self._refresh_parser_binding_text(config)
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
        self.live_plot.clear_all_series()
        self.wave_recorder.clear()
        self._wave_count_dirty = False
        self.wave_record_count.setText("已记录 0 行")
        self.status_label.setText("已清空全部解析通道和波形绑定")

    def _remove_parser_item(self, config: dict, member: int) -> None:
        if member < 0 or config["count"] == 1:
            self.parsers.remove(config)
            self.parser_tree.takeTopLevelItem(self.parser_tree.indexOfTopLevelItem(config["item"]))
            self.live_plot.remove_series(config["name"])
            self.status_label.setText(f"已删除解析结构“{config['name']}”")
            return
        config["enabled"].discard(member)
        config["plots"].discard(member)
        self.live_plot.remove_series(self._series_name(config, member))
        item = config["member_items"].pop(member)
        config["item"].removeChild(item)
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
        old_series = {index: self._series_name(config, index) for index in config["plots"]}
        if member < 0 or is_single:
            config["name"] = new_name
            if is_single:
                config["members"][0] = new_name
            config["item"].setText(0, new_name)
        else:
            config["members"][member] = new_name
            config["member_items"][member].setText(0, new_name)
        for index, previous_name in old_series.items():
            self.live_plot.rename_series(previous_name, self._series_name(config, index))
        self.status_label.setText(f"已重命名为“{new_name}”")

    def _parse_frame_channels(self, frame: RtuFrame) -> None:
        waveform_values: dict[str, float | int] = {}
        for config in self.parsers:
            if config["address"] and frame.address != config["address"]:
                continue
            if config["function"] >= 0 and frame.function != config["function"]:
                continue
            source = frame.raw if config["source"] == "frame" else modbus_data_area(frame.raw)
            offset = config["offset"]
            if config["source"] == "data":
                lane = config.get("byte_lane", 0) if type_size(config["type"]) == 1 else 0
                offset = (config["register"] - self.poll_start.value()) * 2 + lane
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
                message = f"等待数据 · {exc}"
                self._set_tree_text(config["item"], 2, "等待数据")
                if config["item"].toolTip(2) != message:
                    config["item"].setToolTip(2, message)
                for item in config["member_items"].values():
                    self._set_tree_text(item, 2, "等待数据")
                    if item.toolTip(2) != message:
                        item.setToolTip(2, message)
                continue
            display = ", ".join(f"{value:.6g}" if isinstance(value, float) else str(value) for value in values)
            self._set_tree_text(config["item"], 2, display if len(display) < 48 else display[:47] + "…")
            if config["item"].toolTip(2) != display:
                config["item"].setToolTip(2, display)
            for index, value in enumerate(values):
                if index not in config["enabled"]:
                    continue
                item = config["member_items"].get(index)
                if item:
                    self._set_tree_text(item, 2, f"{value:.8g}" if isinstance(value, float) else str(value))
                    if item.toolTip(2):
                        item.setToolTip(2, "")
                if index in config["plots"]:
                    series_name = self._series_name(config, index)
                    color_index = (self.parsers.index(config) + index) % len(self.parser_colors)
                    self.live_plot.add_sample(series_name, float(value), self.parser_colors[color_index])
                    waveform_values[series_name] = value
        if waveform_values:
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
        self.live_plot.clear()
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
        self._set_label_text(self.recent_address, "---")
        self._set_label_text(self.recent_function, "等待数据")
        self._set_label_text(self.recent_bytes, "0")
        self._set_label_text(self.recent_crc, "---")
        self.recent_crc.setStyleSheet("")
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
        event.accept()
