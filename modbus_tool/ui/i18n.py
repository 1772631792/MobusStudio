"""Runtime translation for the standalone UI chrome."""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QTabBar,
    QTableView,
    QTreeWidget,
    QWidget,
)


ENGLISH = {
    "清爽、可靠的 RTU 调试台": "A clean, reliable RTU debugging tool",
    "导入 JSON": "Import JSON", "导入工程配置，不会自动连接串口或启动轮询": "Import a project without connecting or starting polling",
    "可用串口": "Available serial ports", "刷新串口": "Refresh ports", "连接串口": "Connect", "断开连接": "Disconnect",
    "●  未连接": "●  Disconnected", "帧列表": "Frames", "原始数据": "Raw Data", "数据概览": "Overview",
    "实时波形": "Live Plot", "参数报警": "Alarms", "暂停": "Pause", "继续": "Resume", "清空": "Clear",
    "轮询": "Polling", "解析": "Parsing", "写入": "Write", "构帧": "Frame Builder", "设置": "Settings",
    "时间": "Time", "方向": "Direction", "站号": "Slave", "功能": "Function", "摘要": "Summary", "原始帧": "Raw Frame",
    "帧详情": "Frame Details", "选择一条帧，查看地址、功能、负载及 CRC 信息": "Select a frame to inspect its address, function, payload and CRC",
    "时间戳 + 方向 + HEX · 接收数据按 RTU 静默间隔自动分帧": "Timestamp + direction + HEX · data is framed by the RTU silent interval",
    "连接串口后，接收的 RTU 数据将显示在这里…": "Incoming RTU data will appear here after connecting…",
    "接收字节": "RX Bytes", "有效帧": "Valid Frames", "CRC 异常": "CRC Errors", "发送字节": "TX Bytes",
    "字节分布": "Byte Distribution", "实时数据波形": "Live Data Plot", "单窗口": "Single View", "上下分屏": "Split View",
    "适应视图": "Fit View", "导出 CSV": "Export CSV", "清空波形": "Clear Plot",
    "参数报警": "Parameter Alarms", "添加报警": "Add Alarm", "编辑规则": "Edit Rule", "删除规则": "Delete Rule",
    "检测规则": "Rules", "检测通道": "Channel", "报警条件": "Condition", "当前值": "Current Value", "状态": "Status",
    "启用": "Enabled", "报警事件": "Alarm Events", "导出事件": "Export Events", "清空事件": "Clear Events",
    "发生时间": "Time", "检测值": "Value", "连续读取轮询": "Continuous Read Polling",
    "严格按“请求 → 等待响应 → 再请求”运行": "Runs strictly as request → response → next request",
    "从站地址": "Slave Address", "读取功能": "Read Function", "起始寄存器": "Start Register", "寄存器数量": "Register Count",
    "响应后间隔": "Response Interval", "响应超时": "Response Timeout", "尚未开始": "Not Started", "成功": "Success",
    "超时/异常": "Timeout / Error", "范围": "Range", "响应": "Response", "开始安全轮询": "Start Safe Polling", "停止轮询": "Stop Polling",
    "多个轮询项按“请求 → 等待响应 → 下一项”自动调度": "Multiple polling jobs are scheduled as request → response → next job",
    "名称": "Name", "新增": "Add", "复制": "Duplicate", "删除": "Delete", "立即一次": "Run Once",
    "任务名称": "Job Name", "启用此轮询项": "Enable this job", "范围模式": "Range Mode",
    "自动扩展": "Auto-expand", "手动固定": "Manual", "例如：温度区": "e.g. Temperature Block",
    "数据解析通道": "Parsed Data Channels", "通道名称": "Channel Name", "数据类型": "Data Type", "解析数量": "Value Count",
    "数据块 ID": "Data Block ID",
    "字节序": "Byte Order", "8位字节位置": "8-bit Byte Lane", "缩放倍率": "Scale", "数据来源": "Data Source",
    "从站过滤": "Slave Filter", "功能过滤": "Function Filter", "例：温度": "e.g. Temperature", "倍率，例如 0.1": "Scale, e.g. 0.1",
    "大端 / ABCD": "Big-endian / ABCD", "小端 / DCBA": "Little-endian / DCBA", "字交换 / CDAB": "Word swap / CDAB",
    "字节交换 / BADC": "Byte swap / BADC", "Modbus 数据区": "Modbus Data", "完整 RTU 帧": "Complete RTU Frame",
    "全部": "All", "全部功能码": "All Functions", "高字节": "High Byte", "低字节": "Low Byte",
    "添加解析结构": "Add Parser", "变量 / 结构": "Value / Structure", "地址 · 类型": "Address · Type",
    "删除所选变量 / 结构 / 成员": "Delete Selected Value / Structure / Member", "清空所有通道": "Clear All Channels",
    "类型化数据写入": "Typed Data Write", "保持寄存器": "Holding Registers", "线圈": "Coils", "写入目标": "Write Target",
    "数值类型": "Value Type", "写入数值": "Values", "自动编码预览": "Encoded Preview", "发送前再次确认": "Confirm before sending",
    "发送写入命令": "Send Write Command", "智能构帧": "Smart Frame Builder", "自动加入从站地址": "Auto-fill slave address",
    "自动加入功能码与长度": "Auto-fill function and length", "自动计算 CRC16": "Calculate CRC16", "起始地址": "Start Address",
    "读取数量": "Read Count", "写入数据": "Write Data", "发送预览": "Send Preview", "循环发送": "Repeat Send", "发送帧": "Send Frame",
    "串口参数": "Serial Parameters", "软件设置": "Application", "数据位": "Data Bits", "校验位": "Parity", "停止位": "Stop Bits", "推荐组合": "Recommended Presets",
    "无校验 (None)": "None", "偶校验 (Even)": "Even", "奇校验 (Odd)": "Odd", "标记 (Mark)": "Mark", "空格 (Space)": "Space",
    "外观设置": "Appearance", "界面主题": "Theme", "VOFA+ 明亮（默认）": "VOFA+ Light (Default)", "深色模式": "Dark Mode",
    "界面语言": "Language", "简体中文": "Simplified Chinese", "数据显示": "Data Display",
    "帧列表显示毫秒时间戳": "Show milliseconds", "HEX 使用大写字母": "Use uppercase HEX", "帧缓存上限": "Frame Cache Limit",
    "工程 JSON": "Project JSON", "导出配置 JSON": "Export Project JSON",
    "绑定解析成员": "Bind Parsed Value", "暂无可绑定成员": "No values available", "自动适应 / 复位视图": "Auto-fit / Reset View",
    "回到最新数据": "Return to Latest", "显示全部通道": "Show All Channels", "添加检测通道": "Add Channel", "已更新": "Updated",
    "参数报警规则": "Alarm Rule", "单阈值": "Single Threshold", "双阈值区间": "Range Threshold", "启用此规则": "Enable this rule",
    "阈值模式": "Threshold Mode", "阈值": "Threshold", "上限": "Upper Limit", "下限": "Lower Limit",
    "区间外报警": "Alarm Outside Range", "区间内报警": "Alarm Inside Range", "确定": "OK", "取消": "Cancel",
    "01 · 读线圈": "01 · Read Coils", "02 · 读离散输入": "02 · Read Discrete Inputs",
    "03 · 读保持寄存器": "03 · Read Holding Registers", "04 · 读输入寄存器": "04 · Read Input Registers",
    "05 · 写单个线圈": "05 · Write Single Coil", "06 · 写单个寄存器": "06 · Write Single Register",
    "0F · 写多个线圈": "0F · Write Multiple Coils", "10 · 写多个寄存器": "10 · Write Multiple Registers",
}


def translate_text(text: str) -> str:
    if not text:
        return text
    if text in ENGLISH:
        return ENGLISH[text]
    recorded = re.fullmatch(r"已记录\s*([\d,]+)\s*行", text)
    if recorded:
        return f"Recorded {recorded.group(1)} rows"
    ports = re.fullmatch(r"发现\s*(\d+)\s*个串口", text)
    if ports:
        return f"Found {ports.group(1)} serial ports"
    return text


def _property(obj: QObject, getter: str, setter: str) -> None:
    if hasattr(obj, getter) and hasattr(obj, setter):
        value = getattr(obj, getter)()
        if isinstance(value, str) and value:
            getattr(obj, setter)(translate_text(value))


def translate_ui(root: QObject) -> None:
    for obj in [root, *root.findChildren(QObject)]:
        for getter, setter in (("windowTitle", "setWindowTitle"), ("toolTip", "setToolTip"),
                               ("statusTip", "setStatusTip"), ("placeholderText", "setPlaceholderText")):
            _property(obj, getter, setter)
        if isinstance(obj, (QLabel, QAbstractButton, QGroupBox, QAction)):
            _property(obj, "text", "setText")
        if isinstance(obj, QComboBox):
            for index in range(obj.count()):
                obj.setItemText(index, translate_text(obj.itemText(index)))
        if isinstance(obj, QTabBar):
            for index in range(obj.count()):
                obj.setTabText(index, translate_text(obj.tabText(index)))
        if isinstance(obj, QTableView) and obj.model() is not None:
            model = obj.model()
            for column in range(model.columnCount()):
                value = model.headerData(column, Qt.Orientation.Horizontal)
                if isinstance(value, str):
                    model.setHeaderData(column, Qt.Orientation.Horizontal, translate_text(value))
        if isinstance(obj, QTreeWidget) and obj.headerItem() is not None:
            header = obj.headerItem()
            for column in range(header.columnCount()):
                header.setText(column, translate_text(header.text(column)))


class EnglishUiFilter(QObject):
    """Translate dialogs and menus created after startup."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            QTimer.singleShot(0, lambda target=watched: translate_ui(target))
        return False
