from __future__ import annotations

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.vision.image_io import imread_unicode, imwrite_unicode

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from zephie_rolling_on.models.lucky_card import LuckyCardDef, LuckyCardType, load_lucky_card_defs

PROJECT_ROOT = project_root()
_last_lucky_debug_key: str | None = None

# 与 game_state 注释一致：0=正常, 1=2倍 … 6=10倍
_MULTIPLY_TO_TIER = {2: 1, 3: 2, 5: 3, 7: 4, 8: 5, 10: 6}

_DEFAULT_THRESHOLD = 0.72

# 倍数卡共享骰子图案，全卡匹配易把 ×3 认成 ×2；用右下数字区域匹配
_MULTIPLY_DIGIT_TOP_FRAC = 0.1
_MULTIPLY_DIGIT_LEFT_FRAC = 0.38

# 前进/后退：左上角 +/- 区分类型；右下数字区区分步数（仅同类型内比）
_STEP_SIGN_TOP_FRAC = 0.0
_STEP_SIGN_LEFT_FRAC = 0.0
_STEP_SIGN_BOTTOM_FRAC = 0.52
_STEP_SIGN_RIGHT_FRAC = 0.52
_STEP_BACK_DIGIT_TOP_FRAC = 0.46
_STEP_BACK_DIGIT_LEFT_FRAC = 0.24
_STEP_FORWARD_DIGIT_TOP_FRAC = 0.375
_STEP_FORWARD_DIGIT_LEFT_FRAC = 0.05
# 前进/后退数字「1」区域相近；共用 ROI 避免后退模板在前进卡右下角误高分
_STEP_SHARED_DIGIT_TOP_FRAC = 0.35
_STEP_SHARED_DIGIT_LEFT_FRAC = 0.05
_STEP_INNER_MARGIN_FRAC = 0.14
_STEP_INNER_RB_FORWARD = 12.0
_STEP_INNER_RB_BACK = -12.0

# 相对 lucky_cards_region 左上角；槽1~3 标定，槽4~5 由槽3+右缘反推
_DEFAULT_SLOT_RECTS: tuple[tuple[int, int, int, int], ...] = (
    (4, 0, 38, 40),
    (46, 0, 38, 40),
    (88, 0, 38, 40),
    (134, 0, 38, 40),
    (178, 0, 38, 40),
)


@dataclass(frozen=True)
class LuckyCardMatch:
    card_id: str
    card_type: LuckyCardType
    value: int
    score: float
    x: int
    y: int
    width: int
    height: int
    slot_index: int = 0


@dataclass(frozen=True)
class SlotRect:
    """单格卡槽，坐标相对 lucky_cards_region 裁剪图。"""

    slot_index: int
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class LuckySlotResult:
    matches: list[LuckyCardMatch] = field(default_factory=list)
    count: int = 0
    tier: int = 0
    summary: str = ""

    @property
    def card_ids(self) -> list[str]:
        return [m.card_id for m in self.matches]


@dataclass(frozen=True)
class _TemplateEntry:
    definition: LuckyCardDef
    image: np.ndarray
    gray: np.ndarray


def _load_regions_config() -> dict[str, Any]:
    from zephie_rolling_on.data.adventure_frame import resolve_regions

    path = PROJECT_ROOT / "config" / "regions.yaml"
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return resolve_regions(raw)


def load_lucky_slot_rects(config: dict[str, Any] | None = None) -> list[SlotRect]:
    """
    读取 5 格区域。优先传入的 config 或 config/regions.yaml → lucky_card_slots。
    坐标相对 lucky_cards_region 左上角（非全屏客户区）。
    仅当 yaml 未配置 lucky_card_slots 时才用 _DEFAULT_SLOT_RECTS。
    """
    cfg = _load_regions_config() if config is None else config
    raw_slots = cfg.get("lucky_card_slots")
    if not raw_slots:
        return [
            SlotRect(slot_index=i, left=l, top=t, width=w, height=h)
            for i, (l, t, w, h) in enumerate(_DEFAULT_SLOT_RECTS)
        ]

    rects: list[SlotRect] = []
    for i, item in enumerate(raw_slots):
        if not isinstance(item, dict):
            continue
        rects.append(
            SlotRect(
                slot_index=int(item.get("slot", i + 1)) - 1,
                left=int(item["left"]),
                top=int(item.get("top", 0)),
                width=int(item["width"]),
                height=int(item["height"]),
            )
        )
    return sorted(rects, key=lambda r: r.slot_index)


def slot_center_in_client(
    slot_index: int,
    lucky_region: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> tuple[int, int] | None:
    """给定槽位序号，返回该卡槽中心在「游戏客户区」中的坐标。

    SlotRect 坐标相对 lucky_cards_region；加上 region 左上角偏移得到客户区坐标。
    """
    rects = load_lucky_slot_rects(config)
    rect = next((r for r in rects if r.slot_index == slot_index), None)
    if rect is None:
        return None
    base_l = int(lucky_region.get("left", 0))
    base_t = int(lucky_region.get("top", 0))
    cx = base_l + rect.left + rect.width // 2
    cy = base_t + rect.top + rect.height // 2
    return cx, cy


def should_save_lucky_slot_debug(config: dict[str, Any] | None = None) -> bool:
    dev_path = PROJECT_ROOT / "config" / "dev.yaml"
    mtime = dev_path.stat().st_mtime if dev_path.is_file() else 0.0
    cached = getattr(should_save_lucky_slot_debug, "_cache", None)
    if cached is not None and cached[0] == mtime:
        dev = cached[1]
    else:
        dev = {}
        if dev_path.is_file():
            with dev_path.open(encoding="utf-8") as f:
                dev = yaml.safe_load(f) or {}
        should_save_lucky_slot_debug._cache = (mtime, dev)  # type: ignore[attr-defined]
    if "save_lucky_slot_debug" in dev:
        return bool(dev.get("save_lucky_slot_debug"))
    if config and "save_lucky_slot_debug" in config:
        return bool(config.get("save_lucky_slot_debug"))
    return True


def save_lucky_slot_debug(
    slot_bgr: np.ndarray,
    matches: list[LuckyCardMatch],
    *,
    summary: str = "",
    snapshot: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """保存幸运卡槽调试图（叠加 5 个检测框，便于核对对准）。
    - snapshot=True（一键测试）：data/debug/lucky_slot.png + lucky_slot_result.json
    - snapshot=False（规划循环）：追加 data/debug/lucky_slot/<ts>_*
    """
    import json

    from zephie_rolling_on.vision.debug_paths import DEBUG_DIR, debug_capture_paths, ensure_debug_dir

    global _last_lucky_debug_key

    paths: dict[str, Path] = {}
    if slot_bgr is None or slot_bgr.size == 0:
        return paths

    match_by_slot = {m.slot_index: m for m in matches}
    match_rows = [
        {
            "slot": m.slot_index + 1,
            "card_id": m.card_id,
            "card_type": m.card_type.value,
            "value": m.value,
            "score": round(m.score, 4),
        }
        for m in matches
    ]
    rects = load_lucky_slot_rects(config)
    result = {
        "summary": summary,
        "count": len(matches),
        "matches": match_rows,
        "slot_rects": [
            {
                "slot": r.slot_index + 1,
                "left": r.left,
                "top": r.top,
                "width": r.width,
                "height": r.height,
            }
            for r in rects
        ],
    }
    if snapshot:
        ensure_debug_dir(DEBUG_DIR)
        image_path = DEBUG_DIR / "lucky_slot.png"
        json_path = DEBUG_DIR / "lucky_slot_result.json"
    else:
        key = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        if key == _last_lucky_debug_key:
            return paths
        _last_lucky_debug_key = key
        image_path, json_path = debug_capture_paths("lucky_slot")

    overlay = slot_bgr.copy()
    for rect in rects:
        x0, y0 = int(rect.left), int(rect.top)
        x1 = x0 + int(rect.width)
        y1 = y0 + int(rect.height)
        hit = match_by_slot.get(rect.slot_index)
        color = (0, 220, 0) if hit is not None else (0, 200, 255)
        cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 2)
        label = f"S{rect.slot_index + 1}"
        if hit is not None:
            label = f"{label}:{hit.card_id}"
        cv2.putText(
            overlay,
            label,
            (x0, max(y0 - 4, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
            cv2.LINE_AA,
        )
    imwrite_unicode(str(image_path), overlay)
    paths["slot"] = image_path

    payload = {
        "kind": "lucky_slot",
        "image": image_path.name,
        **result,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["result"] = json_path
    return paths


@lru_cache(maxsize=1)
def _load_template_entries() -> tuple[_TemplateEntry, ...]:
    entries: list[_TemplateEntry] = []
    for definition in load_lucky_card_defs():
        path = definition.template_path
        if not path.is_file():
            continue
        image = imread_unicode(path)
        if image is None or image.size == 0:
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        entries.append(_TemplateEntry(definition=definition, image=image, gray=gray))
    return tuple(entries)


def _crop_frac_box(
    gray: np.ndarray,
    *,
    top: float,
    left: float,
    bottom: float,
    right: float,
) -> np.ndarray:
    """按相对坐标裁剪矩形子图（0~1）。"""
    h, w = gray.shape[:2]
    y0 = int(h * top)
    y1 = max(y0 + 1, int(h * bottom))
    x0 = int(w * left)
    x1 = max(x0 + 1, int(w * right))
    y1 = min(y1, h)
    x1 = min(x1, w)
    if y1 <= y0 or x1 <= x0:
        return gray
    return gray[y0:y1, x0:x1]


def _step_sign_roi(gray: np.ndarray) -> np.ndarray:
    return _crop_frac_box(
        gray,
        top=_STEP_SIGN_TOP_FRAC,
        left=_STEP_SIGN_LEFT_FRAC,
        bottom=_STEP_SIGN_BOTTOM_FRAC,
        right=_STEP_SIGN_RIGHT_FRAC,
    )


def _max_template_score(search_gray: np.ndarray, tmpl_gray: np.ndarray) -> float:
    """对两块图做缩放模板匹配，返回最高 TM_CCOEFF_NORMED。"""
    sch, scw = search_gray.shape[:2]
    th, tw = tmpl_gray.shape[:2]
    if sch < 4 or scw < 4 or th < 4 or tw < 4:
        return -1.0
    best = -1.0
    for scale in _scales_for_template(sch, th):
        nw = max(4, int(tw * scale))
        nh = max(4, int(th * scale))
        if nw > scw or nh > sch:
            continue
        tmpl = cv2.resize(tmpl_gray, (nw, nh), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(search_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(cv2.minMaxLoc(res)[1]))
    return best


_STEP_SIGN_SCORE_BONUS = 0.22
_STEP_TONE_SCORE_BONUS = 0.18  # 内圈 R-B（避开槽位蓝边）
_STEP_FULL_CARD_BONUS = 0.12  # 整卡粗匹配区分前进/后退底色
_STEP_WRONG_TYPE_PENALTY = 0.45  # 色调已判定方向时，压制反方向
_STEP_CLOSE_SCORE_GAP = 0.06  # 同类型步数卡分数接近时用紧 ROI 决胜
_STEP_FORWARD_TIGHT_DIGIT_BOX = (0.42, 0.20, 0.88, 0.78)
_STEP_BACK_TIGHT_DIGIT_BOX = (0.48, 0.30, 0.90, 0.82)


def _step_reference_grays() -> tuple[np.ndarray | None, np.ndarray | None]:
    fwd_gray: np.ndarray | None = None
    back_gray: np.ndarray | None = None
    for entry in _load_template_entries():
        if entry.definition.id == "forward_1":
            fwd_gray = entry.gray
        elif entry.definition.id == "back_1":
            back_gray = entry.gray
    return fwd_gray, back_gray


def _step_forward_plus_roi(gray: np.ndarray) -> np.ndarray:
    return _crop_frac_box(gray, top=0.0, left=0.0, bottom=0.38, right=0.48)


def _step_back_minus_roi(gray: np.ndarray) -> np.ndarray:
    """后退「−」在数字上方居中，不用后退模板左上角（易与前进卡误匹配）。"""
    return _crop_frac_box(gray, top=0.02, left=0.28, bottom=0.42, right=0.72)


def _step_sign_scores(cell_gray: np.ndarray) -> tuple[float, float]:
    """返回 (前进 + 区匹配分, 后退 − 区匹配分)。"""
    fwd_gray, back_gray = _step_reference_grays()
    if fwd_gray is None or back_gray is None:
        return -1.0, -1.0
    sf = _max_template_score(
        _step_forward_plus_roi(cell_gray),
        _step_forward_plus_roi(fwd_gray),
    )
    sb = _max_template_score(
        _step_back_minus_roi(cell_gray),
        _step_back_minus_roi(back_gray),
    )
    return sf, sb


def _step_inner_rb_margin(cell_bgr: np.ndarray | None) -> float:
    """
    内圈平均 (R-B)。避开槽位蓝边；前进模板约 +47，后退约 -50（标定图）。
    """
    if cell_bgr is None or cell_bgr.size == 0 or cell_bgr.ndim != 3:
        return 0.0
    h, w = cell_bgr.shape[:2]
    m = max(2, int(min(h, w) * _STEP_INNER_MARGIN_FRAC))
    if h <= 2 * m or w <= 2 * m:
        inner = cell_bgr
    else:
        inner = cell_bgr[m : h - m, m : w - m]
    if inner.size == 0:
        return 0.0
    return float(inner[:, :, 2].mean() - inner[:, :, 0].mean())


def _step_kind_hint(rb_margin: float) -> LuckyCardType | None:
    if rb_margin >= _STEP_INNER_RB_FORWARD:
        return LuckyCardType.STEP_FORWARD
    if rb_margin <= _STEP_INNER_RB_BACK:
        return LuckyCardType.STEP_BACK
    return None


def _step_tone_score_bonus(card_type: LuckyCardType, *, rb_margin: float) -> float:
    if card_type == LuckyCardType.STEP_FORWARD:
        return _STEP_TONE_SCORE_BONUS * max(
            0.0, (rb_margin - _STEP_INNER_RB_FORWARD) / 80.0
        )
    if card_type == LuckyCardType.STEP_BACK:
        return _STEP_TONE_SCORE_BONUS * max(
            0.0, (_STEP_INNER_RB_BACK - rb_margin) / 80.0
        )
    return 0.0


def _step_full_card_scores(cell_gray: np.ndarray) -> tuple[float, float]:
    fwd_gray, back_gray = _step_reference_grays()
    if fwd_gray is None or back_gray is None:
        return -1.0, -1.0
    return (
        _max_template_score(cell_gray, fwd_gray),
        _max_template_score(cell_gray, back_gray),
    )


def _step_full_card_bonus(
    card_type: LuckyCardType,
    *,
    full_fwd: float,
    full_back: float,
) -> float:
    if full_fwd < 0 or full_back < 0:
        return 0.0
    if card_type == LuckyCardType.STEP_FORWARD:
        return _STEP_FULL_CARD_BONUS * max(0.0, full_fwd - full_back)
    if card_type == LuckyCardType.STEP_BACK:
        return _STEP_FULL_CARD_BONUS * max(0.0, full_back - full_fwd)
    return 0.0


def _digit_roi(gray: np.ndarray, *, top_frac: float, left_frac: float) -> np.ndarray:
    """卡面右下数字区域，用于区分同系列不同步数/倍数。"""
    h, w = gray.shape[:2]
    y0 = int(h * top_frac)
    x0 = int(w * left_frac)
    if y0 >= h or x0 >= w:
        return gray
    return gray[y0:, x0:]


def _digit_offset(full_shape: tuple[int, int], *, top_frac: float, left_frac: float) -> tuple[int, int]:
    h, w = full_shape[:2]
    return int(w * left_frac), int(h * top_frac)


def _multiply_digit_roi(gray: np.ndarray) -> np.ndarray:
    return _digit_roi(
        gray,
        top_frac=_MULTIPLY_DIGIT_TOP_FRAC,
        left_frac=_MULTIPLY_DIGIT_LEFT_FRAC,
    )


def _multiply_digit_offset(full_shape: tuple[int, int]) -> tuple[int, int]:
    return _digit_offset(
        full_shape,
        top_frac=_MULTIPLY_DIGIT_TOP_FRAC,
        left_frac=_MULTIPLY_DIGIT_LEFT_FRAC,
    )


def _digit_roi_for_card_type(card_type: LuckyCardType) -> tuple[float, float] | None:
    if card_type == LuckyCardType.MULTIPLY:
        return _MULTIPLY_DIGIT_TOP_FRAC, _MULTIPLY_DIGIT_LEFT_FRAC
    if card_type == LuckyCardType.STEP_FORWARD:
        return _STEP_FORWARD_DIGIT_TOP_FRAC, _STEP_FORWARD_DIGIT_LEFT_FRAC
    if card_type == LuckyCardType.STEP_BACK:
        return _STEP_BACK_DIGIT_TOP_FRAC, _STEP_BACK_DIGIT_LEFT_FRAC
    return None


def _tight_step_digit_score(
    cell_gray: np.ndarray,
    tmpl_gray: np.ndarray,
    *,
    box: tuple[float, float, float, float],
) -> float:
    top, left, bottom, right = box
    cell_crop = _crop_frac_box(cell_gray, top=top, left=left, bottom=bottom, right=right)
    tmpl_crop = _crop_frac_box(tmpl_gray, top=top, left=left, bottom=bottom, right=right)
    return _max_template_score(cell_crop, tmpl_crop)


def _resolve_step_type_winner(
    matches: list[LuckyCardMatch],
    cell_gray: np.ndarray,
    *,
    card_type: LuckyCardType,
) -> LuckyCardMatch | None:
    if not matches:
        return None
    ranked = sorted(matches, key=lambda m: m.score, reverse=True)
    best = ranked[0]
    if len(ranked) < 2 or best.score - ranked[1].score >= _STEP_CLOSE_SCORE_GAP:
        return best

    close = [m for m in ranked if best.score - m.score < _STEP_CLOSE_SCORE_GAP]
    tmpl_by_id = {e.definition.id: e.gray for e in _load_template_entries()}
    box = (
        _STEP_FORWARD_TIGHT_DIGIT_BOX
        if card_type == LuckyCardType.STEP_FORWARD
        else _STEP_BACK_TIGHT_DIGIT_BOX
    )
    rescored: list[tuple[float, LuckyCardMatch]] = []
    for m in close:
        tmpl = tmpl_by_id.get(m.card_id)
        if tmpl is None:
            rescored.append((m.score, m))
            continue
        tight = _tight_step_digit_score(cell_gray, tmpl, box=box)
        rescored.append((0.65 * tight + 0.35 * m.score, m))
    return max(rescored, key=lambda item: item[0])[1]


def _scales_for_template(cell_h: int, tmpl_h: int) -> list[float]:
    if cell_h <= 0 or tmpl_h <= 0:
        return [1.0]
    base = cell_h / tmpl_h
    scales: list[float] = []
    for factor in (0.75, 0.85, 0.95, 1.0, 1.05, 1.15):
        s = base * factor
        if 0.45 <= s <= 1.35:
            scales.append(s)
    return scales or [min(1.0, base)]


def _tier_from_matches(matches: list[LuckyCardMatch]) -> int:
    tier = 0
    for m in matches:
        if m.card_type == LuckyCardType.MULTIPLY:
            tier = max(tier, _MULTIPLY_TO_TIER.get(m.value, 0))
    return tier


def _crop_cell(slot_img: np.ndarray, rect: SlotRect) -> np.ndarray | None:
    sh, sw = slot_img.shape[:2]
    x0 = max(0, rect.left)
    y0 = max(0, rect.top)
    x1 = min(sw, rect.right)
    y1 = min(sh, rect.bottom)
    if x1 <= x0 or y1 <= y0:
        return None
    return slot_img[y0:y1, x0:x1]


def _best_match_in_cell(
    cell_gray: np.ndarray,
    *,
    cell_bgr: np.ndarray | None = None,
    x_offset: int,
    slot_index: int,
    threshold: float,
) -> LuckyCardMatch | None:
    ch, cw = cell_gray.shape[:2]
    if ch < 4 or cw < 4:
        return None

    fwd_sign, back_sign = _step_sign_scores(cell_gray)
    rb_margin = _step_inner_rb_margin(cell_bgr)
    kind_hint = _step_kind_hint(rb_margin)
    full_fwd, full_back = _step_full_card_scores(cell_gray)

    best_by_id: dict[str, LuckyCardMatch] = {}
    for entry in _load_template_entries():
        card_type = entry.definition.type
        roi = _digit_roi_for_card_type(card_type)
        tmpl_gray = entry.gray
        search_gray = cell_gray
        off_x, off_y = 0, 0
        if roi is not None:
            top_frac, left_frac = roi
            tmpl_gray = _digit_roi(entry.gray, top_frac=top_frac, left_frac=left_frac)
            search_gray = _digit_roi(cell_gray, top_frac=top_frac, left_frac=left_frac)
            off_x, off_y = _digit_offset(
                cell_gray.shape,
                top_frac=top_frac,
                left_frac=left_frac,
            )
        th, tw = tmpl_gray.shape[:2]
        sch, scw = search_gray.shape[:2]
        if th < 4 or tw < 4 or sch < 4 or scw < 4:
            continue
        for scale in _scales_for_template(sch, th):
            nw = max(4, int(tw * scale))
            nh = max(4, int(th * scale))
            if nw > scw or nh > sch:
                continue
            tmpl = cv2.resize(tmpl_gray, (nw, nh), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(search_gray, tmpl, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
            score = float(max_val)
            if card_type == LuckyCardType.STEP_FORWARD and fwd_sign >= 0:
                score += _STEP_SIGN_SCORE_BONUS * max(0.0, fwd_sign - back_sign)
            elif card_type == LuckyCardType.STEP_BACK and back_sign >= 0:
                score += _STEP_SIGN_SCORE_BONUS * max(0.0, back_sign - fwd_sign)
            score += _step_tone_score_bonus(card_type, rb_margin=rb_margin)
            score += _step_full_card_bonus(
                card_type, full_fwd=full_fwd, full_back=full_back
            )
            if kind_hint is not None and card_type in (
                LuckyCardType.STEP_FORWARD,
                LuckyCardType.STEP_BACK,
            ):
                if card_type != kind_hint:
                    score -= _STEP_WRONG_TYPE_PENALTY
            if score < threshold:
                continue
            x, y = int(max_loc[0]) + off_x, int(max_loc[1]) + off_y
            candidate = LuckyCardMatch(
                card_id=entry.definition.id,
                card_type=entry.definition.type,
                value=entry.definition.value,
                score=score,
                x=x_offset + x,
                y=y,
                width=nw,
                height=nh,
                slot_index=slot_index,
            )
            prev = best_by_id.get(entry.definition.id)
            if prev is None or candidate.score > prev.score:
                best_by_id[entry.definition.id] = candidate
    if not best_by_id:
        return None

    best_by_type: dict[LuckyCardType, LuckyCardMatch] = {}
    for card_type in LuckyCardType:
        group = [m for m in best_by_id.values() if m.card_type == card_type]
        if not group:
            continue
        if card_type in (LuckyCardType.STEP_FORWARD, LuckyCardType.STEP_BACK):
            winner = _resolve_step_type_winner(group, cell_gray, card_type=card_type)
        else:
            winner = max(group, key=lambda m: m.score)
        if winner is not None:
            best_by_type[card_type] = winner
    if not best_by_type:
        return None
    return max(best_by_type.values(), key=lambda m: m.score)


def match_lucky_slot(
    slot_bgr: np.ndarray,
    *,
    threshold: float | None = None,
    max_cards: int = 5,
    slot_count: int = 5,
    config: dict[str, Any] | None = None,
) -> LuckySlotResult:
    """
    在写死的各卡槽矩形内分别模板匹配（坐标见 regions.yaml → lucky_card_slots）。
    """
    if slot_bgr is None or slot_bgr.size == 0:
        return LuckySlotResult(summary="幸运卡槽图像为空")

    thresh = threshold if threshold is not None else _DEFAULT_THRESHOLD
    slot_gray = cv2.cvtColor(slot_bgr, cv2.COLOR_BGR2GRAY)
    rects = load_lucky_slot_rects(config)[:max_cards]

    matches: list[LuckyCardMatch] = []
    for rect in rects:
        cell_bgr = _crop_cell(slot_bgr, rect)
        if cell_bgr is None:
            continue
        cell_gray = cv2.cvtColor(cell_bgr, cv2.COLOR_BGR2GRAY)
        hit = _best_match_in_cell(
            cell_gray,
            cell_bgr=cell_bgr,
            x_offset=rect.left,
            slot_index=rect.slot_index,
            threshold=thresh,
        )
        if hit is not None:
            matches.append(hit)

    if not matches:
        return LuckySlotResult(
            count=0,
            tier=0,
            summary="未匹配到幸运卡（可调 threshold 或检查 lucky_card_slots 坐标）",
        )

    tier = _tier_from_matches(matches)
    parts = [f"槽{m.slot_index + 1}:{m.card_id}({m.score:.2f})" for m in matches]
    summary = "、".join(parts)

    return LuckySlotResult(
        matches=matches,
        count=len(matches),
        tier=tier,
        summary=summary,
    )
