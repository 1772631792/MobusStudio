import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLabel, QTabWidget

from modbus_tool.core.modbus import RtuFrame, append_crc
from modbus_tool.ui.main_window import MainWindow


class _Worker:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def isRunning(self) -> bool:  # noqa: N802
        return True

    def send(self, frame: bytes) -> None:
        self.sent.append(bytes(frame))


def _app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_multiple_poll_tasks_have_stable_tx_rx_rows_and_round_robin() -> None:
    app = _app()
    window = MainWindow()
    window._add_poll_task()
    window.poll_tasks[1]["address"] = 2
    window._refresh_poll_task_tree("P1")
    worker = _Worker()
    window.worker = worker

    window._toggle_polling()
    assert window.poll_current_task["id"] == "P1"
    window._on_frame(append_crc(bytes([1, 3, 20]) + bytes(range(20))), "RX")
    app.processEvents()
    assert window.poll_current_task["id"] == "P2"
    window._on_frame(append_crc(bytes([2, 3, 20]) + bytes(range(20))), "RX")

    assert [(window.frame_model.item(row, 0).text(), window.frame_model.item(row, 2).text())
            for row in range(4)] == [("P1", "TX"), ("P1", "RX"), ("P2", "TX"), ("P2", "RX")]
    assert [task["success"] for task in window.poll_tasks] == [1, 1]
    window._stop_polling(silent=True)
    window.worker = None
    window.close()


def test_deleted_poll_id_is_reused_and_status_column_is_absent() -> None:
    _app()
    window = MainWindow()
    window._add_poll_task()
    assert window.poll_task_tree.columnCount() == 3
    window._refresh_poll_task_tree("P2")
    window._delete_poll_task()
    window._add_poll_task()
    assert [task["id"] for task in window.poll_tasks] == ["P1", "P2"]
    window.close()


def test_poll_button_precedes_status_card_and_settings_are_split() -> None:
    _app()
    window = MainWindow()
    poll_layout = window.poll_button.parentWidget().layout()
    status_card = window.findChild(type(window.poll_button.parentWidget()), "pollStatusCard")
    assert status_card is not None
    assert poll_layout.indexOf(window.poll_button) < poll_layout.indexOf(status_card)
    assert not any("绝对寄存器地址" in label.text() for label in window.findChildren(QLabel))
    settings_tabs = window.findChild(QTabWidget, "settingsTabs")
    assert settings_tabs is not None
    assert [settings_tabs.tabText(index) for index in range(settings_tabs.count())] == ["串口参数", "软件设置"]
    assert window.poll_task_tree.font().pointSize() >= 14
    assert window.poll_task_tree.header().font().pointSize() >= 13
    assert window.poll_task_tree.header().sectionSize(2) >= 120
    assert window.poll_task_tree.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert window.poll_task_tree.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    assert window.poll_task_tree.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    status_labels = {label.text() for label in status_card.findChildren(QLabel)}
    assert "异常" in status_labels
    assert "超时/异常" not in status_labels
    window.close()


def test_new_tx_keeps_previous_rx_visible_until_replacement_arrives() -> None:
    _app()
    window = MainWindow()
    previous = append_crc(bytes.fromhex("01 03 14 00 01 00 02 00 03 00 04 00 05 00 06 00 07 00 08 00 09 00 0A"))
    window._on_frame(previous, "RX", poll_slot="P1:rx", poll_id="P1")
    rx_row = window._find_poll_row("P1:rx")
    previous_hex = window.frame_model.item(rx_row, 7).text()
    worker = _Worker()
    window.worker = worker
    window.poll_active = True
    window._send_poll_request(preferred_task=window.poll_tasks[0])
    assert window.frame_model.item(rx_row, 7).text() == previous_hex
    assert window.frame_model.item(rx_row, 5).text() != "等待响应"
    window._stop_polling(silent=True)
    window.worker = None
    window.close()


def test_clicked_frame_detail_remains_a_static_snapshot() -> None:
    _app()
    window = MainWindow()
    first = append_crc(bytes.fromhex("01 03 02 00 01"))
    second = append_crc(bytes.fromhex("01 03 02 00 02"))
    window._on_frame(first, "RX", poll_slot="P1:rx", poll_id="P1")
    source_row = window._find_poll_row("P1:rx")
    proxy_index = window.frame_filter.mapFromSource(window.frame_model.index(source_row, 0))
    window.frame_table.selectRow(proxy_index.row())
    window._show_selected_frame()
    snapshot = window.detail_text.toPlainText()

    window._on_frame(second, "RX", poll_slot="P1:rx", poll_id="P1")
    assert window.detail_text.toPlainText() == snapshot
    assert "01 03 02 00 01" in snapshot
    window.close()


def test_frame_view_is_roomier_and_supports_english_display() -> None:
    _app()
    window = MainWindow()
    assert window.detail_text.minimumHeight() >= 210
    assert window.detail_text.font().pointSize() >= 14
    assert window.frame_table.font().pointSize() >= 14
    assert window.frame_table.verticalHeader().defaultSectionSize() >= 38
    english_index = window.language_combo.findData("en_US")
    assert english_index >= 0
    assert "English" in window.language_combo.itemText(english_index)
    window.language_combo.blockSignals(True)
    window.language_combo.setCurrentIndex(english_index)
    window.language_combo.blockSignals(False)

    frame = append_crc(bytes.fromhex("01 03 04 00 64 00 C8"))
    window._on_frame(frame, "RX", poll_slot="P1:rx", poll_id="P1")
    source_row = window._find_poll_row("P1:rx")
    assert window.frame_model.item(source_row, 4).text() == "Read Holding Registers"
    assert window.frame_model.item(source_row, 5).text() == "2 registers · 100, 200"
    proxy_index = window.frame_filter.mapFromSource(window.frame_model.index(source_row, 0))
    window.frame_table.selectRow(proxy_index.row())
    window._show_selected_frame()
    assert "Function   Read Holding Registers" in window.detail_text.toPlainText()
    assert "Decoded    2 registers · 100, 200" in window.detail_text.toPlainText()
    window.close()


def test_parser_is_bound_to_selected_poll_block() -> None:
    _app()
    window = MainWindow()
    window._add_poll_task()
    p2 = window.poll_tasks[1]
    p2.update(address=2, start=100, quantity=10)
    window._update_poll_task_item(p2)
    window._refresh_parser_poll_sources()
    window.parser_poll_task.setCurrentIndex(window.parser_poll_task.findData("P2"))
    assert window.parser_offset.isReadOnly()
    assert window.parser_offset.value() == 100
    window.parser_name.setText("温度")
    window.parser_type.setCurrentText("uint16")
    window.parser_offset.setValue(100)
    window._add_parser()

    config = window.parsers[-1]
    assert config["poll_id"] == "P2"
    assert config["address"] == 2
    p1_frame = append_crc(bytes.fromhex("01 03 02 00 11"))
    window._parse_frame_channels(RtuFrame(p1_frame, datetime.now(), "RX"), poll_id="P1")
    assert config["item"].text(2) == "等待数据"
    # The data-block ID is authoritative; changing the block's slave must not
    # leave a stale parser-side address filter behind.
    p2["address"] = 3
    p2_frame = append_crc(bytes.fromhex("03 03 02 00 22"))
    window._parse_frame_channels(RtuFrame(p2_frame, datetime.now(), "RX"), poll_id="P2")
    assert config["item"].text(2) == "34"
    window.close()


def test_parser_address_follows_poll_block_start_changes() -> None:
    _app()
    window = MainWindow()
    window.parser_name.setText("温度")
    window.parser_type.setCurrentText("uint16")
    window._add_parser()
    config = window.parsers[-1]

    window.poll_start.setValue(100)
    assert window.poll_tasks[0]["start"] == 100
    assert config["register"] == 100
    assert "P1 · R100" in config["item"].text(1)

    response = append_crc(bytes.fromhex("01 03 02 00 22"))
    window._parse_frame_channels(RtuFrame(response, datetime.now(), "RX"), poll_id="P1")
    assert config["item"].text(2) == "34"
    window.close()


def test_poll_tasks_can_be_edited_added_and_deleted_while_running() -> None:
    _app()
    window = MainWindow()
    worker = _Worker()
    window.worker = worker
    window._toggle_polling()
    p1 = window.poll_tasks[0]
    assert window.poll_waiting
    assert window.poll_start.isEnabled()
    assert window.poll_add_button.isEnabled()
    assert window.poll_copy_button.isEnabled()
    assert window.poll_delete_button.isEnabled()

    window._add_poll_task()
    assert [task["id"] for task in window.poll_tasks] == ["P1", "P2"]
    window.poll_start.setValue(200)
    assert window.poll_tasks[1]["start"] == 200
    window._delete_poll_task()
    assert [task["id"] for task in window.poll_tasks] == ["P1"]

    window.poll_start.setValue(100)
    assert p1["start"] == 0
    assert p1["_pending_update"]["start"] == 100
    response = append_crc(bytes([1, 3, 20]) + bytes(range(20)))
    window._on_frame(response, "RX")
    assert p1["start"] == 100
    assert "_pending_update" not in p1
    window._stop_polling(silent=True)
    window.worker = None
    window.close()


def test_parser_form_uses_only_data_block_binding_and_compact_hint() -> None:
    _app()
    window = MainWindow()
    labels = {label.text() for label in window.findChildren(QLabel)}
    assert "从接收帧中直接提取数值，并可绑定实时波形" not in labels
    assert "解析一个时通道就是变量；解析多个时可展开成员，双击即可改名" not in labels
    assert "数据来源" not in labels
    assert "从站过滤" not in labels
    assert "功能过滤" not in labels
    assert "数据块起点" not in labels

    window.poll_tasks[0].update(start=1103, quantity=12)
    window._refresh_parser_poll_sources()
    window.parser_type.setCurrentText("float32")
    window.parser_count.setValue(12)
    hint = window.parser_offset_hint.text()
    assert hint.startswith("解析 R1103～R1126 · float32 每个占 2 个寄存器")
    assert "注意：" in hint
    assert "解析数量表示数值个数" not in hint
    assert "轮询范围会自动补足" not in hint
    window.close()


def test_multi_value_parser_keeps_parent_compact_and_values_on_children() -> None:
    _app()
    window = MainWindow()
    window.parser_name.setText("三相电压")
    window.parser_type.setCurrentText("uint16")
    window.parser_count.setValue(3)
    window._add_parser()
    config = window.parsers[-1]
    frame = append_crc(bytes.fromhex("01 03 06 00 01 00 02 00 03"))
    window._parse_frame_channels(RtuFrame(frame, datetime.now(), "RX"), poll_id="P1")

    assert config["item"].text(2) == "已更新"
    assert [config["member_items"][index].text(2) for index in range(3)] == ["1", "2", "3"]
    assert window.parser_tree.header().sectionSize(1) >= 160
    window.close()
