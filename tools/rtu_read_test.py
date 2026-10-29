"""Read-only Modbus RTU hardware diagnostic for functions 01/02/03/04.

Example:
    python tools/rtu_read_test.py COM14 --start 0 --quantity 10 --count 10
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modbus_tool.core.modbus import build_request, check_crc, hex_text  # noqa: E402


def read_frame(port: serial.Serial, request: bytes, timeout: float = 1.0) -> bytes:
    port.reset_input_buffer()
    port.write(request)
    port.flush()
    deadline = time.monotonic() + timeout
    last_byte_at: float | None = None
    response = bytearray()
    while time.monotonic() < deadline:
        chunk = port.read(port.in_waiting or 1)
        now = time.monotonic()
        if chunk:
            response.extend(chunk)
            last_byte_at = now
        elif response and last_byte_at is not None and now - last_byte_at >= 0.01:
            break
    return bytes(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="安全的 Modbus RTU 通用只读测试")
    parser.add_argument("port", help="串口，例如 COM14")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--address", type=int, default=1)
    parser.add_argument("--function", type=int, choices=(1, 2, 3, 4), default=3)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--quantity", type=int, default=10)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.25, help="请求间隔，单位秒")
    args = parser.parse_args()

    good = 0
    total_ms = 0.0
    request = build_request(args.address, args.function, args.start, args.quantity)
    print(f"TX: {hex_text(request)}")
    try:
        with serial.Serial(args.port, args.baud, bytesize=8, parity="N", stopbits=1, timeout=0.02) as port:
            for index in range(args.count):
                started = time.perf_counter()
                response = read_frame(port, request)
                elapsed_ms = (time.perf_counter() - started) * 1000
                total_ms += elapsed_ms
                valid = check_crc(response)
                expected_bytes = (
                    (args.quantity + 7) // 8 if args.function in (1, 2) else args.quantity * 2
                )
                shape_ok = (
                    len(response) == expected_bytes + 5
                    and response[:3] == bytes((args.address, args.function, expected_bytes))
                )
                if valid and shape_ok:
                    good += 1
                print(
                    f"[{index + 1:03d}] {elapsed_ms:7.2f} ms  "
                    f"len={len(response):3d}  CRC={'OK' if valid else 'FAIL'}  "
                    f"frame={'OK' if shape_ok else 'FAIL'}"
                )
                if response and (args.count == 1 or not valid):
                    print(f"RX: {hex_text(response)}")
                if index + 1 < args.count:
                    time.sleep(max(0.02, args.interval))
    except serial.SerialException as exc:
        print(f"串口错误：{exc}", file=sys.stderr)
        return 2
    average = total_ms / max(1, args.count)
    print(f"RESULT: {good}/{args.count} passed, average={average:.2f} ms")
    return 0 if good == args.count else 1


if __name__ == "__main__":
    raise SystemExit(main())
