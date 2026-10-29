"""Main application window."""

from __future__ import annotations

from datetime import datetime
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
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
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
from modbus_tool.core.data_parser import modbus_data_area, parse_typed_values
from modbus_tool.core.serial_worker import SerialWorker
from modbus_tool.core.settings import Settings
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
        self.frames: list[RtuFrame] = []
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.good_frames = 0
        self.bad_frames = 0
        self._last_preview = b""
        self._known_ports: tuple[tuple[str, str, str], ...] = ()
        self.parsers: list[dict] = []
        self.parser_colors = ["#00bfa5", "#0879be", "#ef476f", "#f2a541", "#8b5cf6", "#35a853"]
        self.poll_active = False
        self.poll_waiting = False
        self.poll_success = 0
        self.poll_failures = 0
        self.poll_sent_at = 0.0
        self.poll_request = b""

        self.setWindowTitle("Modbus Studio · RTU")
        self.resize(1360, 840)
        self.setMinimumSize(1050, 680)
        self._build_ui()
        self._restore_settings()
        self.refresh_ports(manual=True)
        self._update_builder()
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

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.addWidget(self._build_workspace())
        body.addWidget(self._build_right_tabs())
        body.setStretchFactor(0, 1)
        body.setSizes([980, 350])
        root_layout.addWidget(body, 1)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.status_label = QLabel("就绪 · 等待连接串口")
        status.addWidget(self.status_label)
        status.addPermanentWidget(QLabel("RTU  |  v0.1.0"))
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
        pause.toggled.connect(lambda checked: pause.setText("继续" if checked else "暂停"))
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
        title_box.addWidget(QLabel("在右侧“解析”选项卡中勾选绑定波形", objectName="muted"))
        header.addLayout(title_box)
        header.addStretch()
        clear_plot = QPushButton("清空波形")
        clear_plot.clicked.connect(lambda: self.live_plot.clear())
        header.addWidget(clear_plot)
        layout.addLayout(header)
        self.live_plot = LivePlotWidget()
        layout.addWidget(self.live_plot, 1)
        return widget

    def _build_right_tabs(self) -> QWidget:
        self.right_tabs = QTabWidget()
        # The tab stack is vertical on the right, but labels are custom-painted horizontally.
        self.right_tabs.setTabBar(HorizontalTextSideTabBar())
        self.right_tabs.setTabPosition(QTabWidget.TabPosition.East)
        self.right_tabs.setMinimumWidth(400)
        self.right_tabs.setMaximumWidth(500)
        self.right_tabs.addTab(self._build_receive_tab(), "接收")
        self.right_tabs.addTab(self._build_poll_tab(), "轮询")
        self.right_tabs.addTab(self._build_parse_tab(), "解析")
        self.right_tabs.addTab(self._build_send_tab(), "构帧")
        self.right_tabs.addTab(self._build_serial_tab(), "串口")
        self.right_tabs.addTab(self._build_settings_tab(), "设置")
        return self.right_tabs

    def _tab_page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        return page, layout

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

        poll_hint = QLabel("解析通道的“字节偏移”从响应数据区第一个字节开始计算。寄存器每个占 2 字节。")
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
        form = QFormLayout()
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
        self.parser_offset.setSuffix(" byte")
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
        form.addRow("字节偏移", self.parser_offset)
        form.addRow("解析数量", self.parser_count)
        form.addRow("缩放倍率", self.parser_scale)
        layout.addLayout(form)
        self.parser_bind_plot = QCheckBox("绑定到实时波形")
        self.parser_bind_plot.setChecked(True)
        layout.addWidget(self.parser_bind_plot)
        add_parser = QPushButton("添加解析通道", objectName="primary")
        add_parser.clicked.connect(self._add_parser)
        layout.addWidget(add_parser)

        self.parser_table = QTableWidget(0, 4)
        self.parser_table.setHorizontalHeaderLabels(["通道", "类型", "当前值", "波形"])
        self.parser_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.parser_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.parser_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.parser_table.verticalHeader().hide()
        self.parser_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.parser_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.parser_table.setMinimumHeight(150)
        layout.addWidget(self.parser_table, 1)
        remove_parser = QPushButton("删除所选通道", objectName="danger")
        remove_parser.clicked.connect(self._remove_parser)
        layout.addWidget(remove_parser)
        return page

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
        layout.addStretch()
        return page

    def _switch_view(self, index: int) -> None:
        self.views.setCurrentIndex(index)
        for i, button in enumerate(self.view_buttons):
            button.setChecked(i == index)

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
        elif selected:
            index = self.port_combo.findData(selected)
            if index >= 0:
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
            self.worker.stop()
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

    def _on_bytes(self, chunk: bytes) -> None:
        self.rx_bytes += len(chunk)
        self.rx_metric.setText(f"{self.rx_bytes:,}")

    def _on_frame(self, raw: bytes, direction: str = "RX", poll_slot: str | None = None) -> None:
        frame = RtuFrame(raw, datetime.now(), direction)
        if direction == "RX" and self.poll_active:
            expected_function = self.poll_function.currentData()
            if (
                frame.address == self.poll_address.value()
                and frame.function in (expected_function, expected_function | 0x80)
            ):
                poll_slot = "rx"
        if self.pause_button.isChecked() and direction == "RX":
            if frame.crc_ok:
                self._parse_frame_channels(frame)
            self._handle_poll_response(frame)
            return
        self.frames.append(frame)
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
        row[0].setData(len(self.frames) - 1, Qt.ItemDataRole.UserRole)
        if poll_slot:
            row[1].setData(poll_slot, Qt.ItemDataRole.UserRole + 1)
        color = QColor("#48d9c2" if direction == "RX" else "#68a7ff")
        row[1].setForeground(color)
        row[5].setForeground(QColor("#68df95" if frame.crc_ok else "#ff7185"))
        stable_row = self._find_poll_row(poll_slot) if poll_slot else -1
        if stable_row >= 0:
            for column, item in enumerate(row):
                self.frame_model.setItem(stable_row, column, item)
        else:
            self.frame_model.appendRow(row)
            self._trim_frame_rows()

        self.raw_text.appendPlainText(f"{stamp}  {direction:<2}  {raw_hex}")
        if direction == "RX" and frame.crc_ok:
            self._parse_frame_channels(frame)
        if direction == "RX":
            self._handle_poll_response(frame)
        if self.auto_scroll.isChecked():
            if not poll_slot:
                self.frame_table.scrollToBottom()
            self.raw_text.verticalScrollBar().setValue(self.raw_text.verticalScrollBar().maximum())
        self._update_byte_overview()
        self.status_label.setText(
            f"最近帧：站号 {address} · {function} · {len(raw)} bytes · CRC {crc_label}"
        )

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
        frame_index = self.frame_model.item(source_index.row(), 0).data(Qt.ItemDataRole.UserRole)
        if frame_index is None or frame_index >= len(self.frames):
            return
        frame = self.frames[frame_index]
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
        sample = b"".join(frame.raw for frame in self.frames[-200:])
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
            self.worker.send(self.poll_request)
        except serial.SerialException as exc:
            self._on_serial_error(str(exc))
            self._stop_polling(silent=True)
            return
        self.tx_bytes += len(self.poll_request)
        self.tx_metric.setText(f"{self.tx_bytes:,}")
        self._on_frame(self.poll_request, "TX", poll_slot="tx")
        self.poll_waiting = True
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
            (self.poll_quantity.value() + 7) // 8
            if expected_function in (1, 2)
            else self.poll_quantity.value() * 2
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

    def _add_parser(self) -> None:
        try:
            scale = float(self.parser_scale.text().strip() or "1")
        except ValueError:
            QMessageBox.information(self, "缩放倍率无效", "缩放倍率必须是数字，例如 1、0.1 或 10。")
            return
        base_name = self.parser_name.text().strip() or f"通道 {len(self.parsers) + 1}"
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
            "offset": self.parser_offset.value(),
            "count": self.parser_count.value(),
            "scale": scale,
            "plot": self.parser_bind_plot.isChecked(),
            "color": self.parser_colors[len(self.parsers) % len(self.parser_colors)],
        }
        self._insert_parser(config)
        self.parser_name.clear()
        self.status_label.setText(f"已添加解析通道“{name}”")
        if config["plot"]:
            self._switch_view(3)

    def _insert_parser(self, config: dict) -> None:
        self.parsers.append(config)
        row = self.parser_table.rowCount()
        self.parser_table.insertRow(row)
        name_item = QTableWidgetItem(config["name"])
        name_item.setForeground(QColor(config["color"]))
        self.parser_table.setItem(row, 0, name_item)
        self.parser_table.setItem(row, 1, QTableWidgetItem(config["type"]))
        self.parser_table.setItem(row, 2, QTableWidgetItem("等待数据"))
        self.parser_table.setItem(row, 3, QTableWidgetItem("●" if config["plot"] else "—"))

    def _remove_parser(self) -> None:
        row = self.parser_table.currentRow()
        if row < 0 or row >= len(self.parsers):
            return
        config = self.parsers.pop(row)
        self.parser_table.removeRow(row)
        self.live_plot.remove_series(config["name"])
        self.status_label.setText(f"已删除解析通道“{config['name']}”")

    def _parse_frame_channels(self, frame: RtuFrame) -> None:
        for row, config in enumerate(self.parsers):
            if config["address"] and frame.address != config["address"]:
                continue
            if config["function"] >= 0 and frame.function != config["function"]:
                continue
            source = frame.raw if config["source"] == "frame" else modbus_data_area(frame.raw)
            try:
                values = parse_typed_values(
                    source,
                    config["type"],
                    config["offset"],
                    config["count"],
                    config["order"],
                    config["scale"],
                )
            except ValueError as exc:
                self.parser_table.setItem(row, 2, QTableWidgetItem(f"等待 · {exc}"))
                continue
            display = ", ".join(f"{value:.6g}" if isinstance(value, float) else str(value) for value in values)
            self.parser_table.setItem(row, 2, QTableWidgetItem(display))
            if config["plot"]:
                for index, value in enumerate(values):
                    series_name = config["name"] if len(values) == 1 else f"{config['name']}[{index}]"
                    self.live_plot.add_sample(series_name, float(value), config["color"])

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
        self.frame_model.removeRows(0, self.frame_model.rowCount())
        if self.poll_active:
            self._ensure_poll_rows()
        self.raw_text.clear()
        self.detail_text.clear()
        self.byte_overview.clear()
        self.live_plot.clear()
        for row in range(self.parser_table.rowCount()):
            self.parser_table.setItem(row, 2, QTableWidgetItem("等待数据"))
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
        if self.worker:
            self.worker.stop()
        event.accept()
