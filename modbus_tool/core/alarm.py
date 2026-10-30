"""Bounded, transition-based parameter alarm evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from uuid import uuid4


SINGLE_OPERATORS = {"gt", "ge", "lt", "le", "eq"}
DOUBLE_OPERATORS = {"outside", "inside"}
OPERATOR_LABELS = {
    "gt": ">",
    "ge": "≥",
    "lt": "<",
    "le": "≤",
    "eq": "=",
    "outside": "区间外",
    "inside": "区间内",
}


@dataclass(slots=True)
class AlarmEvent:
    timestamp: datetime
    rule_id: str
    channel: str
    state: str
    value: float
    condition: str
    raw_frame: str
    duration_ms: float | None = None


@dataclass(slots=True)
class AlarmRule:
    channel: str
    mode: str = "single"
    operator: str = "gt"
    threshold1: float = 0.0
    threshold2: float = 0.0
    enabled: bool = True
    rule_id: str = field(default_factory=lambda: uuid4().hex)
    active: bool = False
    current_value: float | None = None
    active_since: datetime | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"single", "double"}:
            raise ValueError("报警模式必须是 single 或 double")
        allowed = SINGLE_OPERATORS if self.mode == "single" else DOUBLE_OPERATORS
        if self.operator not in allowed:
            raise ValueError("报警比较方式与阈值模式不匹配")
        for value in (self.threshold1, self.threshold2):
            if not math.isfinite(float(value)):
                raise ValueError("报警阈值必须是有限数字")
        if self.mode == "double" and self.threshold1 >= self.threshold2:
            raise ValueError("双阈值下限必须小于上限")

    @property
    def condition_text(self) -> str:
        if self.mode == "single":
            return f"{OPERATOR_LABELS[self.operator]} {self.threshold1:g}"
        return (
            f"{OPERATOR_LABELS[self.operator]} "
            f"[{self.threshold1:g}, {self.threshold2:g}]"
        )

    def _alarm_condition(self, value: float) -> bool:
        if self.operator == "gt":
            return value > self.threshold1
        if self.operator == "ge":
            return value >= self.threshold1
        if self.operator == "lt":
            return value < self.threshold1
        if self.operator == "le":
            return value <= self.threshold1
        if self.operator == "eq":
            return math.isclose(value, self.threshold1, rel_tol=1e-9, abs_tol=1e-12)
        if self.operator == "outside":
            return value < self.threshold1 or value > self.threshold2
        return self.threshold1 <= value <= self.threshold2

    def evaluate(self, value: float, timestamp: datetime, raw_frame: str) -> AlarmEvent | None:
        number = float(value)
        if not self.enabled or not math.isfinite(number):
            return None
        self.current_value = number
        should_be_active = self._alarm_condition(number)
        if should_be_active == self.active:
            return None
        self.active = should_be_active
        if self.active:
            self.active_since = timestamp
            return AlarmEvent(
                timestamp, self.rule_id, self.channel, "active", number,
                self.condition_text, raw_frame,
            )
        duration_ms = None
        if self.active_since is not None:
            duration_ms = max(0.0, (timestamp - self.active_since).total_seconds() * 1000)
        self.active_since = None
        return AlarmEvent(
            timestamp, self.rule_id, self.channel, "cleared", number,
            self.condition_text, raw_frame, duration_ms,
        )

    def to_document(self) -> dict:
        return {
            "id": self.rule_id,
            "channel": self.channel,
            "mode": self.mode,
            "operator": self.operator,
            "threshold1": self.threshold1,
            "threshold2": self.threshold2,
            "enabled": self.enabled,
        }

    @classmethod
    def from_document(cls, document: dict) -> "AlarmRule":
        return cls(
            rule_id=document["id"],
            channel=document["channel"],
            mode=document["mode"],
            operator=document["operator"],
            threshold1=float(document["threshold1"]),
            threshold2=float(document["threshold2"]),
            enabled=bool(document.get("enabled", True)),
        )
