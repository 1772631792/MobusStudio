import pytest

from modbus_tool.core.data_parser import encode_typed_values, modbus_data_area, parse_typed_values
from modbus_tool.core.modbus import (
    append_crc,
    build_request,
    check_crc,
    crc16,
    parse_hex,
    write_response_status,
)


def test_known_crc_vector():
    payload = bytes.fromhex("01 03 00 00 00 0A")
    assert crc16(payload) == 0xCDC5
    assert append_crc(payload).hex(" ").upper() == "01 03 00 00 00 0A C5 CD"
    assert check_crc(append_crc(payload))


def test_build_read_holding_registers():
    assert build_request(1, 3, 0, 10) == bytes.fromhex("01 03 00 00 00 0A C5 CD")


def test_build_write_multiple_registers():
    frame = build_request(1, 16, 0x10, values=[100, 200])
    assert frame[:9] == bytes.fromhex("01 10 00 10 00 02 04 00 64")
    assert check_crc(frame)


def test_write_response_status_accepts_standard_echoes():
    single = build_request(1, 6, 0x10, values=[100])
    assert write_response_status(single, single) == "success"

    multiple = build_request(1, 16, 0x10, values=[100, 200])
    response = append_crc(multiple[:6])
    assert write_response_status(multiple, response) == "success"


def test_write_response_status_rejects_unrelated_and_detects_exception():
    request = build_request(1, 16, 0x10, values=[100, 200])
    unrelated = append_crc(bytes.fromhex("02 10 00 10 00 02"))
    exception = append_crc(bytes.fromhex("01 90 02"))
    assert write_response_status(request, unrelated) is None
    assert write_response_status(request, exception) == "exception"


def test_build_multiple_coils_packs_lsb_first():
    frame = build_request(7, 15, 0x20, values=[1, 0, 1, 1, 0, 0, 0, 1, 1])
    assert frame[6] == 2
    assert frame[7:9] == bytes((0x8D, 0x01))
    assert check_crc(frame)


def test_parse_hex_accepts_common_separators():
    assert parse_hex("0x01, 03-00:0A") == bytes.fromhex("01 03 00 0A")


def test_validation():
    with pytest.raises(ValueError):
        build_request(0, 3, 0, 1)
    with pytest.raises(ValueError):
        build_request(1, 5, 0, values=[2])
    with pytest.raises(ValueError):
        build_request(1, 16, 65535, values=[1, 2])


def test_parse_typed_register_values():
    data = bytes.fromhex("00 64 FF 9C 41 48 00 00")
    assert parse_typed_values(data, "uint16", count=2) == [100, 65436]
    assert parse_typed_values(data, "int16", offset=2) == [-100]
    assert parse_typed_values(data, "float32", offset=4) == pytest.approx([12.5])


def test_parse_little_endian_and_scale():
    assert parse_typed_values(bytes.fromhex("64 00"), "uint16", byte_order="little", scale=0.1) == [10.0]
    assert parse_typed_values(bytes.fromhex("00 00 41 48"), "float32", byte_order="word_swap") == pytest.approx([12.5])
    assert parse_typed_values(bytes.fromhex("48 41 00 00"), "float32", byte_order="byte_swap") == pytest.approx([12.5])


def test_extract_modbus_data_area():
    frame = append_crc(bytes.fromhex("01 03 04 00 64 00 C8"))
    assert modbus_data_area(frame) == bytes.fromhex("00 64 00 C8")


@pytest.mark.parametrize("order", ["big", "little", "word_swap", "byte_swap"])
def test_typed_encode_round_trip(order):
    encoded = encode_typed_values([12.5, -3.25], "float32", order)
    assert parse_typed_values(encoded, "float32", count=2, byte_order=order) == pytest.approx([12.5, -3.25])


def test_typed_encode_signed_register():
    assert encode_typed_values([-1], "int16") == bytes.fromhex("FF FF")
