"""Small, dependency-free Modbus RTU codec."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


FUNCTION_NAMES = {
    1: "读线圈",
    2: "读离散输入",
    3: "读保持寄存器",
    4: "读输入寄存器",
    5: "写单个线圈",
    6: "写单个寄存器",
    15: "写多个线圈",
    16: "写多个寄存器",
}


def crc16(data: bytes) -> int:
    """Return Modbus CRC16 as an integer (wire order is low byte first)."""
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def append_crc(data: bytes) -> bytes:
    value = crc16(data)
    return data + bytes((value & 0xFF, value >> 8))


def check_crc(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def hex_text(data: bytes) -> str:
    return " ".join(f"{value:02X}" for value in data)


def parse_hex(text: str) -> bytes:
    cleaned = text.replace("0x", "").replace("0X", "")
    for separator in (",", "-", ":", ";", "\n", "\t"):
        cleaned = cleaned.replace(separator, " ")
    compact = "".join(cleaned.split())
    if len(compact) % 2:
        raise ValueError("HEX 数据必须是偶数个字符")
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise ValueError("HEX 数据中包含无效字符") from exc


@dataclass(slots=True)
class RtuFrame:
    raw: bytes
    timestamp: datetime
    direction: str = "RX"

    @property
    def address(self) -> int | None:
        return self.raw[0] if self.raw else None

    @property
    def function(self) -> int | None:
        return self.raw[1] if len(self.raw) > 1 else None

    @property
    def crc_ok(self) -> bool:
        return check_crc(self.raw)

    @property
    def function_name(self) -> str:
        if self.function is None:
            return "数据不足"
        if self.function & 0x80:
            return "异常响应"
        return FUNCTION_NAMES.get(self.function, f"自定义 0x{self.function:02X}")

    @property
    def summary(self) -> str:
        if len(self.raw) < 2:
            return "不完整帧"
        function = self.function or 0
        payload = self.raw[2:-2] if len(self.raw) >= 4 else self.raw[2:]
        if function & 0x80 and payload:
            return f"异常码 0x{payload[0]:02X}"
        if function in (1, 2, 3, 4) and payload:
            byte_count = payload[0]
            if function in (3, 4) and byte_count % 2 == 0:
                registers = [
                    int.from_bytes(payload[i : i + 2], "big")
                    for i in range(1, min(1 + byte_count, len(payload)), 2)
                    if len(payload[i : i + 2]) == 2
                ]
                return f"{len(registers)} 个寄存器 · " + ", ".join(map(str, registers[:6]))
            return f"{byte_count} 字节数据"
        if function in (5, 6, 15, 16) and len(payload) >= 4:
            start = int.from_bytes(payload[:2], "big")
            value = int.from_bytes(payload[2:4], "big")
            label = "数量" if function in (15, 16) else "值"
            return f"地址 {start} · {label} {value}"
        return f"{len(payload)} 字节负载"


def build_request(
    address: int,
    function: int,
    start: int,
    quantity: int = 1,
    values: list[int] | None = None,
) -> bytes:
    """Build a standard RTU request and append CRC."""
    if not 1 <= address <= 247:
        raise ValueError("从站地址范围为 1–247")
    if not 0 <= start <= 0xFFFF:
        raise ValueError("起始地址范围为 0–65535")
    values = values or []
    pdu = bytes((address, function)) + start.to_bytes(2, "big")

    if function in (1, 2, 3, 4):
        limit = 2000 if function in (1, 2) else 125
        if not 1 <= quantity <= limit:
            raise ValueError(f"读取数量范围为 1–{limit}")
        if start + quantity > 0x10000:
            raise ValueError("读取范围超过寄存器/线圈地址上限 65535")
        pdu += quantity.to_bytes(2, "big")
    elif function == 5:
        if not values or values[0] not in (0, 1, 0x0000, 0xFF00):
            raise ValueError("线圈值只能是 0 或 1")
        pdu += (0xFF00 if values[0] in (1, 0xFF00) else 0).to_bytes(2, "big")
    elif function == 6:
        if not values or not 0 <= values[0] <= 0xFFFF:
            raise ValueError("寄存器值范围为 0–65535")
        pdu += values[0].to_bytes(2, "big")
    elif function == 15:
        if not values or len(values) > 1968:
            raise ValueError("请输入 1–1968 个线圈值")
        if start + len(values) > 0x10000:
            raise ValueError("写入范围超过线圈地址上限 65535")
        packed = bytearray((len(values) + 7) // 8)
        for index, value in enumerate(values):
            if value not in (0, 1):
                raise ValueError("线圈值只能是 0 或 1")
            if value:
                packed[index // 8] |= 1 << (index % 8)
        pdu += len(values).to_bytes(2, "big") + bytes((len(packed),)) + packed
    elif function == 16:
        if not values or len(values) > 123 or any(not 0 <= v <= 0xFFFF for v in values):
            raise ValueError("请输入 1–123 个 0–65535 的寄存器值")
        if start + len(values) > 0x10000:
            raise ValueError("写入范围超过寄存器地址上限 65535")
        body = b"".join(value.to_bytes(2, "big") for value in values)
        pdu += len(values).to_bytes(2, "big") + bytes((len(body),)) + body
    else:
        raise ValueError("暂不支持此功能码的自动构帧")
    return append_crc(pdu)
