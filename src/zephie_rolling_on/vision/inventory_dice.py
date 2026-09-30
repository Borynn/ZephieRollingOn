"""Read available / charge dice counts from ROI via RapidOCR."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.vision.image_io import imread_unicode, imwrite_unicode

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from zephie_rolling_on.vision.capture import crop_region
from zephie_rolling_on.vision.cell_id_reader import read_int_from_patch

PROJECT_ROOT = project_root()
_DEFAULT_PLUS_TEMPLATE = PROJECT_ROOT / "assets" / "ui" / "inventory_dice_plus.png"
# 区域B 深红底（BGR），与 UI 样本中位数接近
_DEFAULT_PLUS_FILL_BGR = (55, 44, 150)
_PLUS_MATCH_THRESHOLD = 0.55
_INVENTORY_MAX = 99999


@dataclass(frozen=True)
class DigitRoiResult:
    value: int | None
    notes: str
    plus_erased: bool = False
    plus_score: float | None = None


@dataclass(frozen=True)
class InventoryDiceResult:
    part_a: int | None
    part_b: int | None
    total: int | None
    part_a_notes: str
    part_b_notes: str
    notes: str


@dataclass(frozen=True)
class ChargeDiceResult:
    value: int | None
    notes: str


def _box_from_corners(
    left: int, top: int, right: int, bottom: int
) -> dict[str, int]:
    return {
        "left": int(left),
        "top": int(top),
        "width": max(1, int(right) - int(left)),
        "height": max(1, int(bottom) - int(top)),
    }


def _region_or_default(
    config: dict[str, Any], key: str, default: dict[str, int]
) -> dict[str, int]:
    raw = config.get(key)
    if isinstance(raw, dict) and raw.get("width"):
        return {
            "left": int(raw.get("left", 0)),
            "top": int(raw.get("top", 0)),
            "width": int(raw["width"]),
            "height": int(raw.get("height", 1)),
        }
    return dict(default)


@lru_cache(maxsize=4)
def _load_plus_template_bgr(path_str: str) -> np.ndarray | None:
    return imread_unicode(path_str)


def _estimate_bg_bgr(patch: np.ndarray) -> np.ndarray:
    h, w = patch.shape[:2]
    samples = [
        patch[0, 0],
        patch[0, w - 1],
        patch[h - 1, 0],
        patch[h - 1, w - 1],
        patch[0, w // 2],
        patch[h - 1, w // 2],
    ]
    return np.median(np.asarray(samples, dtype=np.float32), axis=0)


def _plus_ink_mask(tmpl_bgr: np.ndarray) -> np.ndarray:
    bg = _estimate_bg_bgr(tmpl_bgr)
    dist = np.linalg.norm(tmpl_bgr.astype(np.float32) - bg, axis=2)
    return dist > 22.0


def _plus_arm_metrics(tmpl_bgr: np.ndarray) -> tuple[int, int, float, float]:
    """Measure plus arm length / stroke thickness / ink centroid from template.

    Template has ~6px horizontal and ~5px vertical arms (nearly equal).
    Erase uses one shared arm length for both directions.
    """
    ink = _plus_ink_mask(tmpl_bgr)
    ys, xs = np.where(ink)
    if ys.size == 0:
        return 5, 2, tmpl_bgr.shape[1] / 2.0, tmpl_bgr.shape[0] / 2.0
    cy = float(ys.mean())
    cx = float(xs.mean())
    row = int(np.clip(round(cy), 0, ink.shape[0] - 1))
    col = int(np.clip(round(cx), 0, ink.shape[1] - 1))
    hz = np.flatnonzero(ink[row])
    vt = np.flatnonzero(ink[:, col])
    h_len = int(hz[-1] - hz[0] + 1) if hz.size else 5
    v_len = int(vt[-1] - vt[0] + 1) if vt.size else 5
    arm = max(h_len, v_len)
    # Thickness: column width on vertical-stem rows (skip full horizontal bar row)
    thick_vals: list[int] = []
    for r in range(ink.shape[0]):
        if r == row:
            continue
        cols = np.flatnonzero(ink[r])
        if cols.size == 0:
            continue
        thick_vals.append(int(cols[-1] - cols[0] + 1))
    thick = int(round(float(np.median(thick_vals)))) if thick_vals else 2
    thick = max(1, min(thick, 3))
    return arm, thick, cx, cy


def erase_inventory_plus(
    patch: np.ndarray,
    *,
    template_path: Path | None = None,
    fill_bgr: tuple[int, int, int] = _DEFAULT_PLUS_FILL_BGR,
    threshold: float = _PLUS_MATCH_THRESHOLD,
) -> tuple[np.ndarray, bool, float]:
    """Template-match plus center, then erase an equal-arm cross (H len == V len).

    Arm length prefers the vertical ink span at the matched center (same as
    horizontal for a square plus, and does not reach into the digit on the right).
    Only ink pixels on the cross are painted.
    """
    if patch is None or patch.size == 0:
        return patch, False, 0.0
    path = template_path or _DEFAULT_PLUS_TEMPLATE
    tmpl = _load_plus_template_bgr(str(path))
    if tmpl is None:
        return patch.copy(), False, 0.0

    sh, sw = patch.shape[:2]
    th0, tw0 = tmpl.shape[:2]
    if th0 > sh or tw0 > sw:
        return patch.copy(), False, 0.0

    arm0, thick0, _tcx, _tcy = _plus_arm_metrics(tmpl)
    search_w = min(sw, max(tw0 + 2, int(round(sw * 0.55))))
    search = patch[:, :search_w]
    search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    tmpl_gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)

    best_score = -1.0
    best_loc = (0, 0)
    best_scale = 1.0
    best_size = (tw0, th0)
    for scale in (1.0, 0.9, 1.1, 0.85, 1.15, 0.8, 1.2):
        tw = max(4, int(round(tw0 * scale)))
        th = max(4, int(round(th0 * scale)))
        if tw > search.shape[1] or th > search.shape[0]:
            continue
        t2 = (
            tmpl_gray
            if scale == 1.0
            else cv2.resize(tmpl_gray, (tw, th), interpolation=cv2.INTER_AREA)
        )
        res = cv2.matchTemplate(search_gray, t2, cv2.TM_CCOEFF_NORMED)
        _mn, mx, _ml, loc = cv2.minMaxLoc(res)
        if float(mx) > best_score:
            best_score = float(mx)
            best_loc = (int(loc[0]), int(loc[1]))
            best_scale = float(scale)
            best_size = (tw, th)

    out = patch.copy()
    if best_score < threshold:
        return out, False, best_score

    tw, th = best_size
    cx = best_loc[0] + tw / 2.0
    cy = best_loc[1] + th / 2.0

    bg = _estimate_bg_bgr(patch)
    dist = np.linalg.norm(patch.astype(np.float32) - bg, axis=2)
    ink = dist > 18.0

    icx = int(np.clip(round(cx), 0, sw - 1))
    vt = np.flatnonzero(ink[:, icx])
    if vt.size:
        arm = int(vt[-1] - vt[0] + 1)
    else:
        arm = max(3, int(round(arm0 * best_scale)))
    thick = max(1, min(3, int(round(thick0 * best_scale))))
    # +0.75：盖住横臂两端抗锯齿，仍远短于伸到右侧数字
    half = arm / 2.0 + 0.75
    half_t = thick / 2.0 + 0.25

    yy, xx = np.ogrid[:sh, :sw]
    vertical = (np.abs(xx - cx) <= half_t) & (np.abs(yy - cy) <= half)
    horizontal = (np.abs(yy - cy) <= half_t) & (np.abs(xx - cx) <= half)
    cross = vertical | horizontal
    erase = cross & ink
    if not np.any(erase):
        erase = cross
    out[erase] = fill_bgr
    return out, True, best_score


def _ocr_digit_roi(
    patch: np.ndarray,
    *,
    config: dict[str, Any],
    erase_plus: bool = False,
) -> DigitRoiResult:
    if patch is None or patch.size == 0:
        return DigitRoiResult(None, "ROI 为空")
    plus_erased = False
    plus_score: float | None = None
    work = patch
    if erase_plus:
        fill = config.get("inventory_dice_plus_fill_bgr", list(_DEFAULT_PLUS_FILL_BGR))
        if isinstance(fill, (list, tuple)) and len(fill) >= 3:
            fill_bgr = (int(fill[0]), int(fill[1]), int(fill[2]))
        else:
            fill_bgr = _DEFAULT_PLUS_FILL_BGR
        thr = float(config.get("inventory_dice_plus_match_threshold", _PLUS_MATCH_THRESHOLD))
        tpl = config.get("inventory_dice_plus_template")
        tpl_path = Path(tpl) if tpl else None
        if tpl_path is not None and not tpl_path.is_absolute():
            tpl_path = PROJECT_ROOT / tpl_path
        work, plus_erased, plus_score = erase_inventory_plus(
            patch,
            template_path=tpl_path,
            fill_bgr=fill_bgr,
            threshold=thr,
        )
    value = read_int_from_patch(
        work,
        kind="cell",
        max_value=int(config.get("inventory_dice_max", _INVENTORY_MAX)),
        max_digits=int(config.get("inventory_dice_max_digits", 5)),
        config=config,
    )
    if value is None:
        note = "RapidOCR 未识别"
        if erase_plus:
            note += (
                f"（加号{'已擦' if plus_erased else '未擦'} "
                f"score={plus_score:.3f})"
                if plus_score is not None
                else "（加号处理失败）"
            )
        return DigitRoiResult(None, note, plus_erased, plus_score)
    note = f"识别={value}"
    if erase_plus:
        note += (
            f"；加号{'已擦' if plus_erased else '未擦'}"
            f" score={plus_score:.3f}"
            if plus_score is not None
            else ""
        )
    return DigitRoiResult(value, note, plus_erased, plus_score)


def recognize_inventory_dice(
    frame: np.ndarray,
    config: dict[str, Any],
) -> InventoryDiceResult:
    """两段 ROI 相加得到可用骰子数（道具数量，非决策剩骰）。"""
    # 默认：框内角点 → left/top/width/height（已由 resolve_regions 加锚点）
    a_box = _region_or_default(
        config,
        "inventory_dice_region_a",
        _box_from_corners(115, 700, 160, 715),
    )
    b_box = _region_or_default(
        config,
        "inventory_dice_region_b",
        _box_from_corners(165, 700, 200, 715),
    )
    patch_a = crop_region(frame, a_box)
    patch_b = crop_region(frame, b_box)
    ra = _ocr_digit_roi(patch_a, config=config, erase_plus=False)
    rb = _ocr_digit_roi(patch_b, config=config, erase_plus=True)
    total: int | None = None
    if ra.value is not None and rb.value is not None:
        total = int(ra.value) + int(rb.value)
    elif ra.value is not None:
        total = int(ra.value)
    elif rb.value is not None:
        total = int(rb.value)
    notes = f"A={ra.value} B={rb.value} 合计={total}"
    return InventoryDiceResult(
        part_a=ra.value,
        part_b=rb.value,
        total=total,
        part_a_notes=ra.notes,
        part_b_notes=rb.notes,
        notes=notes,
    )


def recognize_charge_dice(
    frame: np.ndarray,
    config: dict[str, Any],
) -> ChargeDiceResult:
    """充能骰子数：单 ROI RapidOCR。"""
    box = _region_or_default(
        config,
        "charge_dice_region",
        _box_from_corners(82, 544, 121, 562),
    )
    patch = crop_region(frame, box)
    hit = _ocr_digit_roi(patch, config=config, erase_plus=False)
    return ChargeDiceResult(value=hit.value, notes=hit.notes)


def _draw_roi_box(
    overlay: np.ndarray,
    box: dict[str, int],
    color: tuple[int, int, int],
    label: str,
) -> None:
    left = int(box.get("left", 0))
    top = int(box.get("top", 0))
    width = int(box.get("width", 0))
    height = int(box.get("height", 0))
    if width <= 0 or height <= 0:
        return
    x2 = min(left + width, overlay.shape[1])
    y2 = min(top + height, overlay.shape[0])
    cv2.rectangle(overlay, (left, top), (x2, y2), color, 2)
    cv2.putText(
        overlay,
        label,
        (left, max(top - 6, 0)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        color,
        1,
        cv2.LINE_AA,
    )


def save_inventory_dice_debug(
    frame: np.ndarray,
    config: dict[str, Any],
    result: InventoryDiceResult | None = None,
) -> dict[str, Path]:
    """一键测试：保存可用骰子 A/B 原图、B 去加号后图、全屏框叠加。"""
    from zephie_rolling_on.vision.debug_paths import DEBUG_DIR, ensure_debug_dir

    paths: dict[str, Path] = {}
    if frame is None or frame.size == 0:
        return paths
    ensure_debug_dir(DEBUG_DIR)

    a_box = _region_or_default(
        config,
        "inventory_dice_region_a",
        _box_from_corners(115, 700, 160, 715),
    )
    b_box = _region_or_default(
        config,
        "inventory_dice_region_b",
        _box_from_corners(165, 700, 200, 715),
    )
    patch_a = crop_region(frame, a_box)
    patch_b = crop_region(frame, b_box)

    path_a = DEBUG_DIR / "inventory_dice_a.png"
    path_b = DEBUG_DIR / "inventory_dice_b.png"
    path_b_erased = DEBUG_DIR / "inventory_dice_b_no_plus.png"
    path_overlay = DEBUG_DIR / "inventory_dice_overlay.png"
    path_json = DEBUG_DIR / "inventory_dice_result.json"

    if patch_a is not None and patch_a.size > 0:
        imwrite_unicode(str(path_a), patch_a)
        paths["a"] = path_a
    if patch_b is not None and patch_b.size > 0:
        imwrite_unicode(str(path_b), patch_b)
        paths["b"] = path_b
        fill = config.get("inventory_dice_plus_fill_bgr", list(_DEFAULT_PLUS_FILL_BGR))
        if isinstance(fill, (list, tuple)) and len(fill) >= 3:
            fill_bgr = (int(fill[0]), int(fill[1]), int(fill[2]))
        else:
            fill_bgr = _DEFAULT_PLUS_FILL_BGR
        thr = float(
            config.get("inventory_dice_plus_match_threshold", _PLUS_MATCH_THRESHOLD)
        )
        tpl = config.get("inventory_dice_plus_template")
        tpl_path = Path(tpl) if tpl else None
        if tpl_path is not None and not tpl_path.is_absolute():
            tpl_path = PROJECT_ROOT / tpl_path
        erased, plus_erased, plus_score = erase_inventory_plus(
            patch_b,
            template_path=tpl_path,
            fill_bgr=fill_bgr,
            threshold=thr,
        )
        imwrite_unicode(str(path_b_erased), erased)
        paths["b_no_plus"] = path_b_erased
    else:
        plus_erased = False
        plus_score = None

    overlay = frame.copy()
    _draw_roi_box(overlay, a_box, (0, 200, 80), "inv A")
    _draw_roi_box(overlay, b_box, (0, 160, 255), "inv B")
    imwrite_unicode(str(path_overlay), overlay)
    paths["overlay"] = path_overlay

    payload: dict[str, Any] = {
        "kind": "inventory_dice",
        "region_a": a_box,
        "region_b": b_box,
        "plus_erased": bool(plus_erased) if patch_b is not None else None,
        "plus_score": plus_score if patch_b is not None else None,
        "images": {k: p.name for k, p in paths.items()},
    }
    if result is not None:
        payload.update(
            {
                "part_a": result.part_a,
                "part_b": result.part_b,
                "total": result.total,
                "part_a_notes": result.part_a_notes,
                "part_b_notes": result.part_b_notes,
                "notes": result.notes,
            }
        )
    path_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["result"] = path_json
    return paths


def save_charge_dice_debug(
    frame: np.ndarray,
    config: dict[str, Any],
    result: ChargeDiceResult | None = None,
) -> dict[str, Path]:
    """一键测试：保存充能骰子 ROI 与全屏框叠加。"""
    from zephie_rolling_on.vision.debug_paths import DEBUG_DIR, ensure_debug_dir

    paths: dict[str, Path] = {}
    if frame is None or frame.size == 0:
        return paths
    ensure_debug_dir(DEBUG_DIR)

    box = _region_or_default(
        config,
        "charge_dice_region",
        _box_from_corners(82, 544, 121, 562),
    )
    patch = crop_region(frame, box)
    path_region = DEBUG_DIR / "charge_dice_region.png"
    path_overlay = DEBUG_DIR / "charge_dice_overlay.png"
    path_json = DEBUG_DIR / "charge_dice_result.json"

    if patch is not None and patch.size > 0:
        imwrite_unicode(str(path_region), patch)
        paths["region"] = path_region

    overlay = frame.copy()
    _draw_roi_box(overlay, box, (220, 80, 40), "charge")
    imwrite_unicode(str(path_overlay), overlay)
    paths["overlay"] = path_overlay

    payload: dict[str, Any] = {
        "kind": "charge_dice",
        "region": box,
        "images": {k: p.name for k, p in paths.items()},
    }
    if result is not None:
        payload["value"] = result.value
        payload["notes"] = result.notes
    path_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["result"] = path_json
    return paths
