import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSpinBox

from modbus_tool.ui.alarm_dialog import AlarmRuleDialog
from modbus_tool.ui.main_window import MainWindow
from modbus_tool.ui.widgets import IntegerEdit


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_numeric_inputs_are_plain_fields_without_spin_buttons() -> None:
    _app()
    window = MainWindow()
    alarm = AlarmRuleDialog("P1.T")
    assert not window.findChildren(QSpinBox)
    assert not window.findChildren(QDoubleSpinBox)
    assert not alarm.findChildren(QDoubleSpinBox)
    assert window.poll_interval.text() == "50"
    assert window.poll_timeout.text() == "500"
    assert window.repeat_interval.text() == "1000"
    assert window.keep_limit.text() == "5000"
    alarm.close()
    window.close()


def test_integer_edit_ignores_wheel_and_keeps_range_validation() -> None:
    app = _app()
    field = IntegerEdit(50)
    field.setRange(0, 100)
    wheel = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    app.sendEvent(field, wheel)
    assert field.value() == 50
    field.setValue(200)
    assert field.value() == 100
