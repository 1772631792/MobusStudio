"""Typed value extraction for Modbus payloads."""

from __future__ import annotations

import struct


TYPE_FORMATS = {
    "uint8": "B",
    "int8": "b",
    "uint16": "H",
    "int16": "h",
    "uint32": "I",
    "int32": "i",
    "float32": "f",
    "float64": "d",
}


def type_size(data_type: str) -> int:
    if data_type not in TYPE_FORMATS:
        raise ValueError(f"不支持的数据类型：{data_type}")
    return struct.calcsize(TYPE_FORMATS[data_type])


def parse_typed_values(
    data: bytes,
    data_type: str,
    offset: int = 0,
    count: int = 1,
    byte_order: str = "big",
    scale: float = 1.0,
) -> list[float | int]:
    """Extract typed values from bytes using the requested byte order and scaling."""
    if offset < 0:
        raise ValueError("偏移量不能小于 0")
    if count < 1:
        raise ValueError("解析数量至少为 1")
    if byte_order not in ("big", "little", "word_swap", "byte_swap"):
        raise ValueError("不支持的字节顺序")
    code = TYPE_FORMATS.get(data_type)
    if code is None:
        raise ValueError(f"不支持的数据类型：{data_type}")
    size = struct.calcsize(code)
    end = offset + size * count
    if end > len(data):
        raise ValueError(f"需要 {end} 字节，当前数据区只有 {len(data)} 字节")
    values = []
    for index in range(count):
        chunk = data[offset + index * size : offset + (index + 1) * size]
        if byte_order == "little":
            value = struct.unpack(f"<{code}", chunk)[0]
        else:
            if byte_order == "word_swap" and size >= 4:
                words = [chunk[pos : pos + 2] for pos in range(0, size, 2)]
                chunk = b"".join(reversed(words))
            elif byte_order == "byte_swap" and size >= 2:
                chunk = b"".join(chunk[pos : pos + 2][::-1] for pos in range(0, size, 2))
            value = struct.unpack(f">{code}", chunk)[0]
        values.append(value)
    if scale != 1.0:
        return [value * scale for value in values]
    return values


def encode_typed_values(
    values: list[float | int],
    data_type: str,
    byte_order: str = "big",
) -> bytes:
    """Encode typed values using the same byte-order rules as the parser."""
    if not values:
        raise ValueError("至少需要一个写入值")
    if byte_order not in ("big", "little", "word_swap", "byte_swap"):
        raise ValueError("不支持的字节顺序")
    code = TYPE_FORMATS.get(data_type)
    if code is None:
        raise ValueError(f"不支持的数据类型：{data_type}")
    size = struct.calcsize(code)
    encoded = bytearray()
    for value in values:
        try:
            if byte_order == "little":
                chunk = struct.pack(f"<{code}", value)
            else:
                chunk = struct.pack(f">{code}", value)
                if byte_order == "word_swap" and size >= 4:
                    words = [chunk[pos : pos + 2] for pos in range(0, size, 2)]
                    chunk = b"".join(reversed(words))
                elif byte_order == "byte_swap" and size >= 2:
                    chunk = b"".join(chunk[pos : pos + 2][::-1] for pos in range(0, size, 2))
        except (struct.error, OverflowError) as exc:
            raise ValueError(f"数值 {value} 超出 {data_type} 的范围") from exc
        encoded.extend(chunk)
    return bytes(encoded)


def modbus_data_area(frame: bytes) -> bytes:
    """Return the business-data area from a common Modbus RTU response."""
    if len(frame) < 4:
        return b""
    function = frame[1]
    if function in (1, 2, 3, 4) and len(frame) >= 5:
        byte_count = frame[2]
        return frame[3 : min(3 + byte_count, len(frame) - 2)]
    return frame[2:-2]
