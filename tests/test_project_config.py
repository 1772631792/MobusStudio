from copy import deepcopy

import pytest

from modbus_tool.core.project_config import PROJECT_SCHEMA, PROJECT_VERSION, validate_project


def sample_project() -> dict:
    return {
        "schema": PROJECT_SCHEMA,
        "version": PROJECT_VERSION,
        "serial": {"port": "COM14", "baudrate": 115200, "bytesize": 8, "parity": "N", "stopbits": 1},
        "poll": {"address": 1, "function": 3, "start": 1103, "quantity": 12,
                 "interval_ms": 50, "timeout_ms": 500},
        "parsers": [{
            "name": "电压",
            "type": "float32",
            "order": "big",
            "source": "data",
            "address": 1,
            "function": 3,
            "register": 1103,
            "offset": 1103,
            "byte_lane": 0,
            "count": 2,
            "scale": 1.0,
            "color": "#00bfa5",
            "members": ["Ua", "Ub"],
            "enabled": [0, 1],
            "plots": [0],
        }],
        "parser_sequence": 1,
        "receive": {"address_filter": 0, "function_filter": -1, "crc_filter": 0,
                    "keyword": "", "show_timestamp": True, "uppercase_hex": True,
                    "auto_scroll": True, "keep_limit": 5000},
        "write": {"address": 1, "target": "register", "start": 0, "type": "uint16",
                  "order": "big", "values": "", "confirm": True},
        "builder": {"auto_address": True, "auto_function": True, "auto_crc": True,
                    "address": 1, "function": 3, "start": 0, "quantity": 1,
                    "values": "", "manual_hex": "", "repeat_interval_ms": 1000},
        "ui": {"theme": "light", "main_view": "waveform", "right_tab": "parse",
               "window_size": {"width": 1360, "height": 840},
               "splitter_sizes": [820, 520], "wave_view": {"x_zoom": 1.0, "y_zoom": 1.0}},
    }


def test_project_validation_returns_detached_document():
    original = sample_project()
    result = validate_project(original)
    result["serial"]["port"] = "COM1"
    assert original["serial"]["port"] == "COM14"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda doc: doc["poll"].update(quantity=126), "poll.quantity"),
        (lambda doc: doc["parsers"][0].update(type="float24"), "type"),
        (lambda doc: doc["parsers"][0].update(plots=[2]), "plots"),
        (lambda doc: doc["ui"].update(theme="pink"), "ui.theme"),
        (lambda doc: doc.update(version=999), "配置版本"),
    ],
)
def test_project_validation_rejects_bad_ai_edits(mutate, message):
    document = deepcopy(sample_project())
    mutate(document)
    with pytest.raises(ValueError, match=message):
        validate_project(document)


def test_project_validation_rejects_plot_for_deleted_member():
    document = sample_project()
    document["parsers"][0]["enabled"] = [1]
    with pytest.raises(ValueError, match="plots"):
        validate_project(document)


def test_project_validation_accepts_two_wave_views():
    document = sample_project()
    document["parsers"][0]["plot_views"] = [[0], [1]]
    document["parsers"][0]["plots"] = [0, 1]
    document["ui"]["wave_views"] = [
        {"x_zoom": 1.0, "y_zoom": 1.0},
        {"x_zoom": 2.0, "y_zoom": 0.5},
    ]
    document["ui"]["active_wave_view"] = 1
    document["ui"]["wave_layout"] = 1
    result = validate_project(document)
    assert result["parsers"][0]["plot_views"] == [[0], [1]]
    assert result["ui"]["active_wave_view"] == 1
    assert result["ui"]["wave_layout"] == 1


def test_project_validation_accepts_multiple_poll_blocks_and_parser_binding():
    document = sample_project()
    document["polls"] = [
        {"id": "P1", "name": "电压区", "enabled": True, "address": 1,
         "function": 3, "range_mode": "auto", "start": 1103, "quantity": 12,
         "interval_ms": 50, "timeout_ms": 500},
        {"id": "P2", "name": "温度区", "enabled": True, "address": 2,
         "function": 4, "range_mode": "manual", "start": 200, "quantity": 8,
         "interval_ms": 200, "timeout_ms": 600},
    ]
    document["parsers"][0]["poll_id"] = "P1"
    assert validate_project(document)["parsers"][0]["poll_id"] == "P1"
    document["parsers"][0]["poll_id"] = "P9"
    with pytest.raises(ValueError, match="poll_id"):
        validate_project(document)


def test_project_validation_rejects_inconsistent_wave_view_bindings():
    document = sample_project()
    document["parsers"][0]["plot_views"] = [[], []]
    with pytest.raises(ValueError, match="并集"):
        validate_project(document)


def test_project_validation_accepts_alarm_rules_and_rejects_missing_channel():
    document = sample_project()
    document["alarms"] = [{
        "id": "high-ua", "channel": "电压.Ua", "mode": "single",
        "operator": "ge", "threshold1": 250.0, "threshold2": 0.0,
        "enabled": True,
    }]
    assert validate_project(document)["alarms"][0]["channel"] == "电压.Ua"
    document["alarms"][0]["channel"] = "不存在"
    with pytest.raises(ValueError, match="channel"):
        validate_project(document)
