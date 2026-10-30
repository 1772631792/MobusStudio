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
