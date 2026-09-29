"""Executable decision action structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionScore:
    action_id: str
    label: str
    score: float
    detail: str
    consumes_dice: bool
    raw_value: float | None = None
    # combo 规划：有序列时 action_id 为第一步；执行层可跑满 combo_steps
    combo_steps: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RankResult:
    """排序后的可执行动作；低于阈值的用卡建议保留在手牌。"""

    actions: list[ActionScore]
    held_below_threshold: list[str]
