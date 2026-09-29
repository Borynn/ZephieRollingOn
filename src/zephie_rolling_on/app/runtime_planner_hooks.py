"""Local stubs for strategy / smart-switch hooks used by runtime state."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STRATEGY_DISTANCE = "distance"
PROJECT = project_root()
STRATEGY_PATH = PROJECT / "config" / "planner_strategy.yaml"
SMART_SWITCH_PATH = PROJECT / "config" / "planner_smart_switch.yaml"


@dataclass
class SmartSwitchState:
    paid_rolls: int = 0
    cards_drawn: int = 0
    cards_after_paid: list = field(default_factory=list)
    cards_at_25: Any = None
    cards_at_50: Any = None
    b_from_roll: int = 0
    last_dice_used: int = 0
    last_hand_ids: list | None = None
    pending_roll: bool = False


def _empty_smart_switch_state() -> SmartSwitchState:
    return SmartSwitchState()


def _load_smart_switch_state_disk(
    _path: Path, *, bind_global: bool = False
) -> SmartSwitchState:
    return _empty_smart_switch_state()


def _read_planner_strategy(_path: Path) -> str:
    return STRATEGY_DISTANCE
