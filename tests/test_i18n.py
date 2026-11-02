from modbus_tool.ui.i18n import translate_text


def test_fixed_ui_translation() -> None:
    assert translate_text("解析") == "Parsing"
    assert translate_text("03 · 读保持寄存器") == "03 · Read Holding Registers"


def test_dynamic_ui_translation_does_not_modify_user_names() -> None:
    assert translate_text("已记录 12,345 行") == "Recorded 12,345 rows"
    assert translate_text("发现 3 个串口") == "Found 3 serial ports"
    assert translate_text("温度通道") == "温度通道"
