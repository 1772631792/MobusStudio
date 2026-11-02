import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QTabWidget

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
    return QApplication.instance() or QApplication([])


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
    p2_frame = append_crc(bytes.fromhex("02 03 02 00 22"))
    window._parse_frame_channels(RtuFrame(p2_frame, datetime.now(), "RX"), poll_id="P2")
    assert config["item"].text(2) == "34"
    window.close()
