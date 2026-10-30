from datetime import datetime, timedelta

from modbus_tool.core.alarm import AlarmRule


def test_single_threshold_records_only_state_transitions():
    rule = AlarmRule("温度", operator="gt", threshold1=80)
    now = datetime(2026, 10, 30, 12, 0, 0)
    assert rule.evaluate(70, now, "01 03") is None
    active = rule.evaluate(81, now + timedelta(seconds=1), "01 03 AA")
    assert active and active.state == "active" and active.value == 81
    assert rule.evaluate(82, now + timedelta(seconds=2), "01 03 BB") is None
    cleared = rule.evaluate(79, now + timedelta(seconds=3), "01 03 CC")
    assert cleared and cleared.state == "cleared"
    assert cleared.duration_ms == 2000


def test_double_threshold_outside_range():
    rule = AlarmRule(
        "压力", mode="double", operator="outside",
        threshold1=10, threshold2=20,
    )
    now = datetime(2026, 10, 30, 12, 0, 0)
    assert rule.evaluate(15, now, "frame") is None
    assert rule.evaluate(21, now, "frame").state == "active"
    assert rule.evaluate(19.5, now, "frame").state == "cleared"
    assert rule.evaluate(18.9, now, "frame") is None


def test_alarm_rule_document_round_trip():
    original = AlarmRule(
        "电流", mode="single", operator="le", threshold1=2.5,
        enabled=False,
    )
    restored = AlarmRule.from_document(original.to_document())
    assert restored.to_document() == original.to_document()
