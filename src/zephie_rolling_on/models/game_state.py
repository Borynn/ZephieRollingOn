from __future__ import annotations

from dataclasses import dataclass, field

# 0=正常, 1=2倍 … 6=10倍；与 Cell.lucky_prob 下标一致
DEFAULT_LUCKY_TIER = 0


@dataclass
class GameState:
    """运行时状态（识别模块填入，规划模块只读）。"""

    current_cell_id: int | None = None
    lucky_card_tier: int = DEFAULT_LUCKY_TIER
    lucky_card_count: int = 0
    lucky_card_ids: list[str] = field(default_factory=list)
    dice_remaining: int | None = None
    deck_remaining_total: int | None = None
    hand_full: bool = False
    # 后续可扩展：道具栏、回合阶段等
    extra: dict = field(default_factory=dict)
