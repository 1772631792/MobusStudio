"""Versioned, human-readable project configuration validation."""

from __future__ import annotations

from copy import deepcopy
import re


PROJECT_SCHEMA = "modbus-studio.project"
PROJECT_VERSION = 1
DATA_TYPES = {"uint8", "int8", "uint16", "int16", "uint32", "int32", "float32", "float64"}
BYTE_ORDERS = {"big", "little", "word_swap", "byte_swap"}
SOURCES = {"data", "frame"}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _mapping(value, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} 必须是 JSON 对象")
    return value


def _integer(value, path: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{path} 必须是 {low}～{high} 的整数")
    return value


def _number(value, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} 必须是数字")
    number = float(value)
    if not -1e100 < number < 1e100:
        raise ValueError(f"{path} 超出允许范围")
    return number


def _choice(value, path: str, choices: set):
    if value not in choices:
        raise ValueError(f"{path} 的值无效：{value!r}")
    return value


def validate_project(document: object) -> dict:
    """Validate an imported document and return a detached, normalized copy."""
    root = _mapping(document, "根节点")
    if root.get("schema") != PROJECT_SCHEMA:
        raise ValueError(f"schema 必须是 {PROJECT_SCHEMA!r}")
    if root.get("version") != PROJECT_VERSION:
        raise ValueError(f"暂不支持配置版本 {root.get('version')!r}，当前版本为 {PROJECT_VERSION}")

    serial = _mapping(root.get("serial"), "serial")
    if not isinstance(serial.get("port"), str) or len(serial["port"]) > 256:
        raise ValueError("serial.port 必须是串口名称字符串")
    _integer(serial.get("baudrate"), "serial.baudrate", 1, 10_000_000)
    _choice(serial.get("bytesize"), "serial.bytesize", {5, 6, 7, 8})
    _choice(serial.get("parity"), "serial.parity", {"N", "E", "O", "M", "S"})
    _choice(serial.get("stopbits"), "serial.stopbits", {1, 1.5, 2})

    poll = _mapping(root.get("poll"), "poll")
    _integer(poll.get("address"), "poll.address", 1, 247)
    function = _choice(poll.get("function"), "poll.function", {1, 2, 3, 4})
    _integer(poll.get("start"), "poll.start", 0, 65535)
    _integer(poll.get("quantity"), "poll.quantity", 1, 2000 if function in (1, 2) else 125)
    _integer(poll.get("interval_ms"), "poll.interval_ms", 0, 60000)
    _integer(poll.get("timeout_ms"), "poll.timeout_ms", 50, 10000)

    parsers = root.get("parsers")
    if not isinstance(parsers, list) or len(parsers) > 512:
        raise ValueError("parsers 必须是最多 512 项的数组")
    series_names: set[str] = set()
    for pos, parser in enumerate(parsers):
        path = f"parsers[{pos}]"
        parser = _mapping(parser, path)
        name = parser.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError(f"{path}.name 必须是 1～128 字符的名称")
        data_type = _choice(parser.get("type"), f"{path}.type", DATA_TYPES)
        _choice(parser.get("order"), f"{path}.order", BYTE_ORDERS)
        source = _choice(parser.get("source"), f"{path}.source", SOURCES)
        _integer(parser.get("address"), f"{path}.address", 0, 247)
        _choice(parser.get("function"), f"{path}.function", {-1, 1, 2, 3, 4, 5, 6, 15, 16})
        _integer(parser.get("register"), f"{path}.register", 0, 65535)
        _integer(parser.get("offset"), f"{path}.offset", 0, 65535)
        lane = _integer(parser.get("byte_lane", 0), f"{path}.byte_lane", 0, 1)
        if data_type not in {"uint8", "int8"} and lane:
            raise ValueError(f"{path}.byte_lane 仅适用于 8 位类型")
        if source == "frame" and lane:
            raise ValueError(f"{path}.byte_lane 在完整帧模式下必须为 0")
        count = _integer(parser.get("count"), f"{path}.count", 1, 128)
        _number(parser.get("scale"), f"{path}.scale")
        color = parser.get("color")
        if not isinstance(color, str) or not _COLOR.fullmatch(color):
            raise ValueError(f"{path}.color 必须是 #RRGGBB 颜色")
        members = parser.get("members")
        if not isinstance(members, list) or len(members) != count or any(
            not isinstance(item, str) or not item.strip() or len(item) > 128 for item in members
        ):
            raise ValueError(f"{path}.members 必须包含 {count} 个非空名称")
        for key in ("enabled", "plots"):
            values = parser.get(key)
            if not isinstance(values, list) or any(
                isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < count
                for item in values
            ):
                raise ValueError(f"{path}.{key} 必须是有效成员序号数组")
            if len(values) != len(set(values)):
                raise ValueError(f"{path}.{key} 不能包含重复序号")
        if not set(parser["plots"]).issubset(set(parser["enabled"])):
            raise ValueError(f"{path}.plots 只能绑定 enabled 中的成员")
        for index in parser["enabled"]:
            series = name if count == 1 else f"{name}.{members[index]}"
            if series in series_names:
                raise ValueError(f"波形变量名称重复：{series}")
            series_names.add(series)

    receive = _mapping(root.get("receive"), "receive")
    _integer(receive.get("address_filter"), "receive.address_filter", 0, 247)
    _choice(receive.get("function_filter"), "receive.function_filter", {-1, 1, 2, 3, 4, 5, 6, 15, 16})
    _integer(receive.get("crc_filter"), "receive.crc_filter", 0, 2)
    if not isinstance(receive.get("keyword"), str) or len(receive["keyword"]) > 512:
        raise ValueError("receive.keyword 必须是字符串")
    for key in ("show_timestamp", "uppercase_hex", "auto_scroll"):
        if not isinstance(receive.get(key), bool):
            raise ValueError(f"receive.{key} 必须是 true 或 false")
    _integer(receive.get("keep_limit"), "receive.keep_limit", 100, 100000)

    ui = _mapping(root.get("ui"), "ui")
    _choice(ui.get("theme"), "ui.theme", {"light", "dark"})
    _choice(ui.get("main_view"), "ui.main_view", {"frames", "raw", "overview", "waveform"})
    _choice(ui.get("right_tab"), "ui.right_tab", {"receive", "poll", "parse", "write", "builder", "serial", "settings"})
    size = _mapping(ui.get("window_size"), "ui.window_size")
    _integer(size.get("width"), "ui.window_size.width", 800, 10000)
    _integer(size.get("height"), "ui.window_size.height", 600, 10000)
    splitter = ui.get("splitter_sizes")
    if not isinstance(splitter, list) or len(splitter) != 2:
        raise ValueError("ui.splitter_sizes 必须包含左右两个宽度")
    for index, value in enumerate(splitter):
        _integer(value, f"ui.splitter_sizes[{index}]", 0, 10000)
    wave = _mapping(ui.get("wave_view"), "ui.wave_view")
    x_zoom = _number(wave.get("x_zoom"), "ui.wave_view.x_zoom")
    y_zoom = _number(wave.get("y_zoom"), "ui.wave_view.y_zoom")
    if not 0.2 <= x_zoom <= 25 or not 0.2 <= y_zoom <= 50:
        raise ValueError("ui.wave_view 的缩放值超出范围")

    write = _mapping(root.get("write"), "write")
    _integer(write.get("address"), "write.address", 1, 247)
    _choice(write.get("target"), "write.target", {"register", "coil"})
    _integer(write.get("start"), "write.start", 0, 65535)
    _choice(write.get("type"), "write.type", DATA_TYPES - {"uint8", "int8"})
    _choice(write.get("order"), "write.order", BYTE_ORDERS)
    if not isinstance(write.get("values"), str) or len(write["values"]) > 100_000:
        raise ValueError("write.values 必须是长度不超过 100000 的字符串")
    if not isinstance(write.get("confirm"), bool):
        raise ValueError("write.confirm 必须是 true 或 false")

    builder = _mapping(root.get("builder"), "builder")
    for key in ("auto_address", "auto_function", "auto_crc"):
        if not isinstance(builder.get(key), bool):
            raise ValueError(f"builder.{key} 必须是 true 或 false")
    _integer(builder.get("address"), "builder.address", 1, 247)
    _choice(builder.get("function"), "builder.function", {1, 2, 3, 4, 5, 6, 15, 16})
    _integer(builder.get("start"), "builder.start", 0, 65535)
    _integer(builder.get("quantity"), "builder.quantity", 1, 2000)
    for key in ("values", "manual_hex"):
        if not isinstance(builder.get(key), str) or len(builder[key]) > 100_000:
            raise ValueError(f"builder.{key} 必须是长度不超过 100000 的字符串")
    _integer(builder.get("repeat_interval_ms"), "builder.repeat_interval_ms", 20, 600000)
    if "parser_sequence" in root:
        _integer(root["parser_sequence"], "parser_sequence", 0, 1_000_000)
    return deepcopy(root)
