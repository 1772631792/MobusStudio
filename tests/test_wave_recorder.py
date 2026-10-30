from datetime import datetime

from modbus_tool.core.wave_recorder import WaveRecorder
from modbus_tool.core.wave_recorder import CsvExportWorker


def test_wave_recorder_builds_wide_rows_and_is_bounded():
    recorder = WaveRecorder(max_rows=2)
    now = datetime(2026, 10, 30, 12, 0, 0)
    recorder.append(now, {"temperature": 25.0})
    recorder.append(now, {"temperature": 25.1, "pressure": 100.0})
    recorder.append(now, {"temperature": 25.2})
    channels, rows = recorder.snapshot()
    assert channels == ["temperature", "pressure"]
    assert len(rows) == 2
    assert rows[-1]["temperature"] == 25.2


def test_csv_export_uses_utf8_wide_format(tmp_path):
    recorder = WaveRecorder()
    recorder.append(datetime(2026, 10, 30, 12, 0, 0), {"温度": 25.5, "pressure": 100})
    target = tmp_path / "wave.csv"
    CsvExportWorker(str(target), recorder).run()
    text = target.read_text(encoding="utf-8-sig")
    assert "timestamp,温度,pressure" in text
    assert ",25.5,100" in text
