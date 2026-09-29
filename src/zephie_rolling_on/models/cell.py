from __future__ import annotations

from dataclasses import dataclass

# J–P / Q–W：正常、2、3、5、7、8、10 倍卡对应下标 0–6
PROB_LABELS = ("normal", "x2", "x3", "x5", "x7", "x8", "x10")


@dataclass(frozen=True, slots=True)
class Cell:
    cell_id: int
    description: str
    final_position: int
    jump_end: int | None
    cell_type: str
    normal_dice_stop: int
    lucky_prob: tuple[float, float, float, float, float, float, float]
    shortcut_prob: tuple[float, float, float, float, float, float, float]

    def lucky_prob_at(self, tier_index: int) -> float:
        return self.lucky_prob[tier_index]

    def shortcut_prob_at(self, tier_index: int) -> float:
        return self.shortcut_prob[tier_index]
