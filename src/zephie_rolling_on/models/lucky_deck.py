"""Lucky-card deck: drawn counts and remaining pool."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from zephie_rolling_on.models.lucky_card import format_owned_lucky_cards

PROJECT_ROOT = project_root()
PLANNER_STATE_PATH = PROJECT_ROOT / "config" / "planner_state.yaml"

HAND_SLOT_MAX = 5
DEFAULT_DICE_REMAINING = 100

# 一组 30 张：card_id -> 本组最多张数
DECK_GROUP_SIZE: dict[str, int] = {
    "forward_1": 1,
    "forward_2": 1,
    "forward_3": 1,
    "forward_11": 1,
    "forward_12": 1,
    "back_1": 1,
    "back_2": 1,
    "back_3": 1,
    "multiply_2": 1,
    "multiply_3": 1,
    "multiply_5": 1,
    "multiply_7": 1,
    "multiply_8": 1,
    "multiply_10": 1,
    "forward_4": 2,
    "forward_5": 2,
    "forward_6": 2,
    "forward_7": 2,
    "forward_8": 2,
    "forward_9": 2,
    "forward_10": 2,
    "next_1": 2,
}

DECK_TOTAL = sum(DECK_GROUP_SIZE.values())


def _card_numeric_suffix(card_id: str) -> int:
    return int(card_id.rsplit("_", 1)[-1])


def deck_card_labels_by_section() -> list[tuple[str, list[tuple[str, str, int]]]]:
    """按类型分组的 (分区标题, [(card_id, 中文短名, 本组上限), ...])。"""
    sections: list[tuple[str, list[tuple[str, str, int]]]] = []
    for title, prefix in (
        ("前进卡", "forward_"),
        ("后退卡", "back_"),
        ("倍数卡", "multiply_"),
        ("跳关卡", "next_"),
    ):
        rows: list[tuple[str, str, int]] = []
        for card_id, cap in DECK_GROUP_SIZE.items():
            if prefix == "next_":
                if card_id == "next_1":
                    rows.append((card_id, _short_label(card_id), cap))
            elif card_id.startswith(prefix):
                rows.append((card_id, _short_label(card_id), cap))
        rows.sort(key=lambda r: _card_numeric_suffix(r[0]))
        if rows:
            sections.append((title, rows))
    return sections


def deck_card_labels() -> list[tuple[str, str, int]]:
    """(card_id, 中文短名, 本组上限) 供校准 UI；按类型分组后展平。"""
    rows: list[tuple[str, str, int]] = []
    for _title, section_rows in deck_card_labels_by_section():
        rows.extend(section_rows)
    return rows


def _short_label(card_id: str) -> str:
    if card_id.startswith("forward_"):
        return f"前进{card_id.split('_', 1)[1]}"
    if card_id.startswith("back_"):
        return f"后退{card_id.split('_', 1)[1]}"
    if card_id.startswith("multiply_"):
        return f"×{card_id.split('_', 1)[1]}"
    if card_id == "next_1":
        return "跳关"
    return card_id


@dataclass
class PlannerCalibration:
    """局内规划校准：已抽清单（OCR 写入 dice_remaining）。"""

    dice_remaining: int = DEFAULT_DICE_REMAINING
    drawn_counts: dict[str, int] = field(default_factory=dict)

    def normalized_drawn(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for card_id, cap in DECK_GROUP_SIZE.items():
            n = int(self.drawn_counts.get(card_id, 0) or 0)
            out[card_id] = max(0, min(n, cap))
        return out

    def deck_remaining(self) -> dict[str, int]:
        """本组尚未被抽中的卡（multiset 计数）。

        热路径（规划抽卡 EV）会高频调用且其间无变更，按 drawn 版本缓存。
        返回值视为只读（所有调用方均只读遍历）。
        """
        ver = getattr(self, "_drawn_ver", 0)
        cache = getattr(self, "_dr_cache", None)
        if cache is not None and cache[0] == ver:
            return cache[1]
        drawn = self.normalized_drawn()
        rem: dict[str, int] = {}
        for card_id, cap in DECK_GROUP_SIZE.items():
            left = cap - drawn.get(card_id, 0)
            if left > 0:
                rem[card_id] = left
        self._dr_cache = (ver, rem)  # type: ignore[attr-defined]
        return rem

    def deck_remaining_total(self) -> int:
        return sum(self.deck_remaining().values())

    def is_pool_exhausted(self) -> bool:
        return self.deck_remaining_total() == 0

    def _bump_drawn(self) -> None:
        self._drawn_ver = getattr(self, "_drawn_ver", 0) + 1  # type: ignore[attr-defined]

    def reset_drawn_pool(self) -> None:
        """本组 30 张已全部出现过：重置池子，手牌不变。"""
        self.drawn_counts = {cid: 0 for cid in DECK_GROUP_SIZE}
        self._bump_drawn()

    def record_draw(self, card_id: str) -> None:
        drawn = self.normalized_drawn()
        cap = DECK_GROUP_SIZE[card_id]
        if drawn[card_id] >= cap:
            return
        self.drawn_counts[card_id] = drawn[card_id] + 1
        self._bump_drawn()
        if self.deck_remaining_total() == 0:
            self.reset_drawn_pool()

    def summary_line(self) -> str:
        rem = self.deck_remaining_total()
        return f"卡组剩余 {rem}/{DECK_TOTAL} 张 | 剩余骰子 {self.dice_remaining} (OCR)"

    def remaining_cards_display(self) -> str:
        rem = self.deck_remaining()
        if not rem:
            return "（本组已抽满，下次踩 B 将重置池）"
        parts = [_short_label(cid) for cid in sorted(rem.keys(), key=_sort_key)]
        return "、".join(parts)


def _sort_key(card_id: str) -> tuple[int, str]:
    if card_id.startswith("multiply_"):
        return (0, card_id)
    if card_id.startswith("forward_"):
        return (1, card_id)
    if card_id.startswith("back_"):
        return (2, card_id)
    return (3, card_id)


def can_draw_from_lucky_cell(*, hand_count: int) -> bool:
    """手牌已满 5 张时踩 B 等同无事发生（I 类）。"""
    return hand_count < HAND_SLOT_MAX


def load_planner_calibration(path: Path | None = None) -> PlannerCalibration:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            return rt.calibration
    return _load_planner_calibration_disk(path or PLANNER_STATE_PATH)


def _load_planner_calibration_disk(p: Path) -> PlannerCalibration:
    if not p.is_file():
        return PlannerCalibration()
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    dice = int(data.get("dice_remaining", DEFAULT_DICE_REMAINING))
    raw = data.get("drawn_counts") or {}
    drawn = {str(k): int(v) for k, v in raw.items() if k in DECK_GROUP_SIZE}
    cal = PlannerCalibration(
        dice_remaining=max(0, min(100, dice)),
        drawn_counts=drawn,
    )
    cal.drawn_counts = cal.normalized_drawn()
    if cal.is_pool_exhausted():
        cal.reset_drawn_pool()
    return cal


def sync_deck_from_new_hand_cards(
    previous: list[str],
    current: list[str],
) -> list[str]:
    """手牌 multiset 对比：每张新出现的卡记入已抽清单（更新幸运卡池）。"""
    prev = Counter(previous)
    curr = Counter(current)
    cal = load_planner_calibration()
    recorded: list[str] = []
    changed = False
    for card_id, count in curr.items():
        if card_id not in DECK_GROUP_SIZE:
            continue
        extra = count - prev.get(card_id, 0)
        for _ in range(max(0, extra)):
            cal.record_draw(card_id)
            recorded.append(card_id)
            changed = True
    if changed:
        save_planner_calibration(cal)
    return recorded


def is_dice_exhausted_ocr(
    *,
    dice_used: int | None,
    dice_remaining: int | None,
    dice_total: int = DEFAULT_DICE_REMAINING,
) -> bool:
    """OCR 判定本局投掷已用尽（已用=dice_total 或剩余=0）。"""
    if dice_remaining is not None and int(dice_remaining) <= 0:
        return True
    if dice_used is not None and int(dice_used) >= int(dice_total):
        return True
    return False


def is_round_end_by_start(*, roll_kind: str | None) -> bool:
    """亮屏检测到 START 按钮 → 本局结束（与骰子数无关）。"""
    return roll_kind == "start"


def is_round_end_by_paid_exhausted(
    *,
    dice_used: int | None,
    dice_remaining: int | None,
    roll_kind: str | None,
    dice_total: int = DEFAULT_DICE_REMAINING,
) -> bool:
    """Alias: round end is decided by START roll kind only."""
    return is_round_end_by_start(roll_kind=roll_kind)


def is_new_round_dice_ready(
    *,
    dice_used: int | None,
    dice_remaining: int | None,
    dice_total: int = DEFAULT_DICE_REMAINING,
) -> bool:
    """OCR 判定新局骰子计数已重置（开新局后恢复为 0/100 等）。"""
    if dice_remaining is not None and int(dice_remaining) >= int(dice_total):
        return True
    if dice_used is not None and int(dice_used) <= 0:
        return True
    return False


def maybe_clear_deck_pool_on_dice_exhausted(
    *,
    dice_used: int | None,
    dice_remaining: int | None,
    dice_total: int = DEFAULT_DICE_REMAINING,
) -> PlannerCalibration | None:
    """骰子用尽时重置幸运卡池（drawn_counts 清零）。未用尽或 OCR 无效时返回 None。"""
    if not is_dice_exhausted_ocr(
        dice_used=dice_used,
        dice_remaining=dice_remaining,
        dice_total=dice_total,
    ):
        return None
    return clear_deck_pool_for_round_end()


def clear_deck_pool_for_round_end() -> PlannerCalibration:
    """本局冒险结束时重置幸运卡池（不依赖骰子 OCR）。"""
    cal = load_planner_calibration()
    cal.reset_drawn_pool()
    save_planner_calibration(cal)
    return cal


def apply_deck_pool_scan_counts(drawn_counts: dict[str, int]) -> PlannerCalibration:
    """用卡池扫描结果整体覆盖已抽清单（未出现的 card_id 记 0）。"""
    cal = load_planner_calibration()
    merged: dict[str, int] = {cid: 0 for cid in DECK_GROUP_SIZE}
    for cid, n in (drawn_counts or {}).items():
        if cid not in DECK_GROUP_SIZE:
            continue
        merged[cid] = max(0, min(int(n), int(DECK_GROUP_SIZE[cid])))
    cal.drawn_counts = merged
    cal._bump_drawn()
    if cal.is_pool_exhausted():
        # 扫到「全抽满」时与 record_draw 一致：重置空池，避免规划立刻无卡
        cal.reset_drawn_pool()
    save_planner_calibration(cal)
    return cal


def apply_ocr_dice_to_calibration(dice_remaining: int | None) -> PlannerCalibration:
    """OCR 识别到剩余骰子后写入 planner_state（不改已抽清单）。"""
    cal = load_planner_calibration()
    if dice_remaining is None:
        return cal
    cal.dice_remaining = max(0, min(100, int(dice_remaining)))
    save_planner_calibration(cal)
    return cal


def save_planner_calibration(cal: PlannerCalibration, path: Path | None = None) -> Path:
    # 始终落成新对象，避免原地改 drawn_counts 后 _dr_cache 仍指向旧剩余张数
    fresh = PlannerCalibration(
        dice_remaining=int(cal.dice_remaining),
        drawn_counts=dict(cal.normalized_drawn()),
    )
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            rt.calibration = fresh
            return PLANNER_STATE_PATH
    p = path or PLANNER_STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "dice_remaining": int(fresh.dice_remaining),
        "drawn_counts": fresh.normalized_drawn(),
    }
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
    return p


def merge_calibration_into_state_dict(
    state_extra: dict[str, Any],
    cal: PlannerCalibration,
    *,
    hand_ids: list[str] | None = None,
) -> dict[str, Any]:
    hand = list(hand_ids or [])
    extra = dict(state_extra)
    extra["dice_remaining"] = cal.dice_remaining
    extra["drawn_counts"] = cal.normalized_drawn()
    extra["deck_remaining"] = cal.deck_remaining()
    extra["deck_remaining_total"] = cal.deck_remaining_total()
    extra["hand_full"] = len(hand) >= HAND_SLOT_MAX
    extra["hand_display"] = format_owned_lucky_cards(hand)
    return extra
