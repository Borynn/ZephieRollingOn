"""Deck-pool drawn-mark detection (two-pass template match)."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.vision.image_io import imread_unicode, imwrite_unicode

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from zephie_rolling_on.models.lucky_deck import DECK_GROUP_SIZE

PROJECT_ROOT = project_root()
_DEFAULT_THRESHOLD = 0.70
_DEFAULT_TOLERANCE = 4
_DEFAULT_ROW_STEP = 30

# 第一轮（打开卡池后，未拖拽）：与游戏列表自上而下顺序一致
ROUND1_CARDS: tuple[str, ...] = (
    "forward_1",
    "forward_2",
    "forward_3",
    "forward_4",
    "forward_4",
    "forward_5",
    "forward_5",
    "forward_6",
    "forward_6",
    "forward_7",
    "forward_7",
    "forward_8",
    "forward_8",
    "forward_9",
    "forward_9",
    "forward_10",
    "forward_10",
    "forward_11",
)

# 第二轮（向下拖拽后）：剩余 12 槽
ROUND2_CARDS: tuple[str, ...] = (
    "forward_12",
    "back_1",
    "back_2",
    "back_3",
    "multiply_2",
    "multiply_3",
    "multiply_5",
    "multiply_7",
    "multiply_8",
    "multiply_10",
    "next_1",
    "next_1",
)


@dataclass(frozen=True)
class DeckSlotHit:
    """某一列表槽位是否已抽（客户区坐标）。"""

    round_index: int
    slot_index: int
    card_id: str
    expected_x: int
    expected_y: int
    hit_x: int | None
    hit_y: int | None
    score: float
    drawn: bool


@dataclass
class DeckPoolScanResult:
    """两轮卡池扫描汇总。"""

    drawn_counts: dict[str, int] = field(default_factory=dict)
    slot_hits: list[DeckSlotHit] = field(default_factory=list)
    round1_hit_count: int = 0
    round2_hit_count: int = 0
    notes: str = ""


def _resolve_template_path(raw: str | None) -> Path:
    if not raw:
        return PROJECT_ROOT / "assets" / "ui" / "deck_drawn_mark.png"
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache(maxsize=4)
def _load_template_gray(path_str: str) -> np.ndarray | None:
    img = imread_unicode(path_str)
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _find_all_template_centers(
    search_gray: np.ndarray,
    template: np.ndarray,
    *,
    threshold: float,
    origin_left: int,
    origin_top: int,
) -> list[tuple[int, int, float]]:
    """在 search 内找所有过阈值匹配，返回客户区 (cx, cy, score)。"""
    sh, sw = search_gray.shape[:2]
    th, tw = template.shape[:2]
    if tw > sw or th > sh:
        return []
    res = cv2.matchTemplate(search_gray, template, cv2.TM_CCOEFF_NORMED)
    hits: list[tuple[int, int, float]] = []
    # 拷贝以便抹掉邻域做 NMS
    work = res.copy()
    pad_x = max(1, tw // 2)
    pad_y = max(1, th // 2)
    while True:
        _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(work)
        if float(max_v) < threshold:
            break
        cx = origin_left + int(max_l[0]) + tw // 2
        cy = origin_top + int(max_l[1]) + th // 2
        hits.append((cx, cy, float(max_v)))
        x0 = max(0, int(max_l[0]) - pad_x)
        y0 = max(0, int(max_l[1]) - pad_y)
        x1 = min(work.shape[1], int(max_l[0]) + pad_x + 1)
        y1 = min(work.shape[0], int(max_l[1]) + pad_y + 1)
        work[y0:y1, x0:x1] = -1.0
        if len(hits) >= 40:
            break
    return hits


def _round_cards(round_cfg: dict[str, Any], default: tuple[str, ...]) -> list[str]:
    raw = round_cfg.get("cards")
    if isinstance(raw, list) and raw:
        return [str(c) for c in raw]
    return list(default)


def detect_deck_round(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    round_key: str,
    round_index: int,
    default_cards: tuple[str, ...],
) -> list[DeckSlotHit]:
    """单轮：在 search 内匹配已抽勾，按期望中心 ±tolerance 归到槽位。"""
    if frame is None or frame.size == 0:
        return []
    root = (config or {}).get("deck_pool", {})
    if not isinstance(root, dict):
        return []
    round_cfg = root.get(round_key, {})
    if not isinstance(round_cfg, dict):
        return []
    search = round_cfg.get("search")
    if not isinstance(search, dict) or not search.get("width"):
        return []

    left = int(search.get("left", 0))
    top = int(search.get("top", 0))
    width = int(search.get("width", 0))
    height = int(search.get("height", 0))
    fh, fw = frame.shape[:2]
    x0 = max(0, left)
    y0 = max(0, top)
    x1 = min(fw, left + width)
    y1 = min(fh, top + height)
    if x1 <= x0 or y1 <= y0:
        return []

    tpl_path = _resolve_template_path(root.get("drawn_template"))
    template = _load_template_gray(str(tpl_path))
    if template is None:
        return []

    threshold = float(root.get("match_threshold", _DEFAULT_THRESHOLD))
    tolerance = int(root.get("center_tolerance", _DEFAULT_TOLERANCE))
    row_step = int(root.get("row_step", _DEFAULT_ROW_STEP))
    fc = round_cfg.get("first_center") or {}
    first_x = int(fc.get("x", 250))
    first_y = int(fc.get("y", 146 if round_index == 1 else 326))
    cards = _round_cards(round_cfg, default_cards)

    search_gray = _to_gray(frame[y0:y1, x0:x1])
    centers = _find_all_template_centers(
        search_gray,
        template,
        threshold=threshold,
        origin_left=x0,
        origin_top=y0,
    )

    remaining = list(centers)
    hits: list[DeckSlotHit] = []
    for i, card_id in enumerate(cards):
        ex = first_x
        ey = first_y + i * row_step
        best_j = -1
        best_dist = 10**9
        best_score = -1.0
        for j, (cx, cy, score) in enumerate(remaining):
            if abs(cx - ex) <= tolerance and abs(cy - ey) <= tolerance:
                dist = abs(cx - ex) + abs(cy - ey)
                if dist < best_dist or (dist == best_dist and score > best_score):
                    best_dist = dist
                    best_score = score
                    best_j = j
        if best_j >= 0:
            cx, cy, score = remaining.pop(best_j)
            hits.append(
                DeckSlotHit(
                    round_index=round_index,
                    slot_index=i + 1,
                    card_id=card_id,
                    expected_x=ex,
                    expected_y=ey,
                    hit_x=cx,
                    hit_y=cy,
                    score=score,
                    drawn=True,
                )
            )
        else:
            hits.append(
                DeckSlotHit(
                    round_index=round_index,
                    slot_index=i + 1,
                    card_id=card_id,
                    expected_x=ex,
                    expected_y=ey,
                    hit_x=None,
                    hit_y=None,
                    score=0.0,
                    drawn=False,
                )
            )
    return hits


def aggregate_drawn_counts(slot_hits: list[DeckSlotHit]) -> dict[str, int]:
    """槽位命中 → card_id 已抽张数（不超过 DECK_GROUP_SIZE）。"""
    counts: Counter[str] = Counter()
    for hit in slot_hits:
        if hit.drawn and hit.card_id in DECK_GROUP_SIZE:
            counts[hit.card_id] += 1
    out: dict[str, int] = {cid: 0 for cid in DECK_GROUP_SIZE}
    for cid, n in counts.items():
        out[cid] = min(int(n), int(DECK_GROUP_SIZE[cid]))
    return out


def scan_deck_pool_on_frames(
    frame_round1: np.ndarray,
    frame_round2: np.ndarray,
    config: dict[str, Any],
) -> DeckPoolScanResult:
    """给定两轮截图，汇总已抽计数（不含点击/拖拽）。"""
    h1 = detect_deck_round(
        frame_round1,
        config,
        round_key="round1",
        round_index=1,
        default_cards=ROUND1_CARDS,
    )
    h2 = detect_deck_round(
        frame_round2,
        config,
        round_key="round2",
        round_index=2,
        default_cards=ROUND2_CARDS,
    )
    all_hits = h1 + h2
    counts = aggregate_drawn_counts(all_hits)
    drawn_n = sum(1 for h in all_hits if h.drawn)
    return DeckPoolScanResult(
        drawn_counts=counts,
        slot_hits=all_hits,
        round1_hit_count=sum(1 for h in h1 if h.drawn),
        round2_hit_count=sum(1 for h in h2 if h.drawn),
        notes=f"两轮合计命中 {drawn_n} 槽",
    )


def save_deck_pool_round_debug(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    round_key: str,
    round_index: int,
    default_cards: tuple[str, ...],
    debug_prefix: str,
) -> tuple[dict[str, Path], list[DeckSlotHit]]:
    """保存单轮卡池检测调试图。"""
    from zephie_rolling_on.vision.capture import crop_region
    from zephie_rolling_on.vision.debug_paths import debug_image_path

    paths: dict[str, Path] = {}
    hits = detect_deck_round(
        frame,
        config,
        round_key=round_key,
        round_index=round_index,
        default_cards=default_cards,
    )
    root = (config or {}).get("deck_pool", {})
    round_cfg = root.get(round_key, {}) if isinstance(root, dict) else {}
    search = round_cfg.get("search") if isinstance(round_cfg, dict) else None
    if not isinstance(search, dict):
        return paths, hits

    patch = crop_region(frame, search)
    search_path = debug_image_path(f"{debug_prefix}_search.png")
    overlay_path = debug_image_path(f"{debug_prefix}_overlay.png")
    if patch is not None and patch.size > 0:
        imwrite_unicode(str(search_path), patch)
        paths["search"] = search_path

    overlay = frame.copy()
    left = int(search["left"])
    top = int(search["top"])
    width = int(search["width"])
    height = int(search["height"])
    cv2.rectangle(
        overlay,
        (left, top),
        (min(left + width, overlay.shape[1]), min(top + height, overlay.shape[0])),
        (255, 0, 255),
        2,
    )
    for hit in hits:
        color = (0, 255, 0) if hit.drawn else (0, 165, 255)
        cv2.circle(overlay, (hit.expected_x, hit.expected_y), 4, color, 1)
        if hit.drawn and hit.hit_x is not None and hit.hit_y is not None:
            cv2.circle(overlay, (hit.hit_x, hit.hit_y), 6, (0, 255, 0), 2)
    imwrite_unicode(str(overlay_path), overlay)
    paths["overlay"] = overlay_path
    return paths, hits
