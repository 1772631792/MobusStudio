"""Alarm rule editor dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from modbus_tool.core.alarm import AlarmRule


class AlarmRuleDialog(QDialog):
    def __init__(self, channel: str, rule: AlarmRule | None = None, parent=None) -> None:
        super().__init__(parent)
        self.channel = channel
        self.original = rule
        self.result_rule: AlarmRule | None = None
        self.setWindowTitle("参数报警规则")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        description = QLabel(f"检测通道：{channel}")
        description.setWordWrap(True)
        description.setObjectName("sectionTitle")
        layout.addWidget(description)

        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("单阈值", "single")
        self.mode.addItem("双阈值区间", "double")
        self.operator = QComboBox()
        self.threshold1 = self._number_box()
        self.threshold2 = self._number_box()
        self.enabled = QCheckBox("启用此规则")
        self.enabled.setChecked(True)
        form.addRow("阈值模式", self.mode)
        form.addRow("报警条件", self.operator)
        self.threshold1_label = QLabel("阈值")
        form.addRow(self.threshold1_label, self.threshold1)
        self.threshold2_label = QLabel("上限")
        form.addRow(self.threshold2_label, self.threshold2)
        form.addRow("状态", self.enabled)
        self.form = form
        layout.addLayout(form)

        hint = QLabel("报警只在状态变化时记录一次：进入报警、恢复正常。不会为每个采样点重复记日志。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self._mode_changed)

        if rule is not None:
            self.mode.setCurrentIndex(self.mode.findData(rule.mode))
            self._mode_changed()
            self.operator.setCurrentIndex(self.operator.findData(rule.operator))
            self.threshold1.setValue(rule.threshold1)
            self.threshold2.setValue(rule.threshold2)
            self.enabled.setChecked(rule.enabled)
        else:
            self._mode_changed()

    @staticmethod
    def _number_box() -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(-1e100, 1e100)
        box.setDecimals(8)
        box.setSingleStep(1.0)
        return box

    def _mode_changed(self, *_args) -> None:
        mode = self.mode.currentData()
        previous = self.operator.currentData()
        self.operator.clear()
        if mode == "single":
            for label, value in (("大于 >", "gt"), ("大于等于 ≥", "ge"),
                                 ("小于 <", "lt"), ("小于等于 ≤", "le"),
                                 ("等于 =", "eq")):
                self.operator.addItem(label, value)
            self.threshold1_label.setText("阈值")
            self.form.setRowVisible(self.threshold2, False)
        else:
            self.operator.addItem("区间外报警", "outside")
            self.operator.addItem("区间内报警", "inside")
            self.threshold1_label.setText("下限")
            self.form.setRowVisible(self.threshold2, True)
        index = self.operator.findData(previous)
        if index >= 0:
            self.operator.setCurrentIndex(index)

    def accept(self) -> None:
        try:
            values = dict(
                channel=self.channel,
                mode=self.mode.currentData(),
                operator=self.operator.currentData(),
                threshold1=self.threshold1.value(),
                threshold2=self.threshold2.value(),
                enabled=self.enabled.isChecked(),
            )
            if self.original is not None:
                values["rule_id"] = self.original.rule_id
            self.result_rule = AlarmRule(**values)
        except ValueError as exc:
            QMessageBox.information(self, "报警规则无效", str(exc))
            return
        super().accept()
