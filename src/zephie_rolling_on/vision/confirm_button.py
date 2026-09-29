"""Find the reward confirm button (template match in search ROI)."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.vision.image_io import imread_unicode

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

PROJECT_ROOT = project_root()
_DEFAULT_THRESHOLD = 0.72
# 模板与截屏分辨率可能略有差异，多尺度搜索更稳
_SCALES: tuple[float, ...] = (1.0, 0.9, 1.1, 0.8, 1.2)


@dataclass(frozen=True)
class ConfirmHit:
    """匹配到的确认按钮（客户区坐标）。"""

    center_x: int
    center_y: int
    score: float


def _resolve_template_path(raw: str | None) -> Path:
    if not raw:
        return PROJECT_ROOT / "assets" / "ui" / "confirm_button.png"
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


def _search_region_for_section(config: dict[str, Any], section: str) -> dict[str, int] | None:
    cfg = (config or {}).get(section, {})
    search = cfg.get("search")
    if not isinstance(search, dict) or not search.get("width"):
        return None
    return {
        "left": int(search.get("left", 0)),
        "top": int(search.get("top", 0)),
        "width": int(search.get("width", 0)),
        "height": int(search.get("height", 0)),
    }


@dataclass(frozen=True)
class PopupButtonProbe:
    """弹窗按钮模板匹配探测结果（含未达阈值的 best_score，供调试）。"""

    hit: ConfirmHit | None
    best_score: float
    threshold: float
    search: dict[str, int]
    template_path: Path


def _match_popup_button_in_frame(
    frame: np.ndarray,
    config: dict[str, Any],
    section: str,
    *,
    default_template: str | None = None,
) -> PopupButtonProbe | None:
    if frame is None or frame.size == 0:
        return None
    cfg = (config or {}).get(section, {})
    region = _search_region_for_section(config, section)
    if region is None:
        return None

    raw_tpl = cfg.get("template") or default_template
    tpl_path = _resolve_template_path(raw_tpl)
    template = _load_template_gray(str(tpl_path))
    if template is None:
        return None

    fh, fw = frame.shape[:2]
    left = max(0, region["left"])
    top = max(0, region["top"])
    right = min(fw, left + region["width"])
    bottom = min(fh, top + region["height"])
    if right <= left or bottom <= top:
        return None

    search_gray = _to_gray(frame[top:bottom, left:right])
    sh, sw = search_gray.shape[:2]
    threshold = float(cfg.get("match_threshold", _DEFAULT_THRESHOLD))

    best_score = -1.0
    best: ConfirmHit | None = None
    th0, tw0 = template.shape[:2]
    for scale in _SCALES:
        tw = max(8, int(round(tw0 * scale)))
        th = max(8, int(round(th0 * scale)))
        if tw > sw or th > sh:
            continue
        tmpl = template if scale == 1.0 else cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(search_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
        if max_v > best_score:
            best_score = float(max_v)
            cx = left + int(max_l[0]) + tw // 2
            cy = top + int(max_l[1]) + th // 2
            best = ConfirmHit(center_x=cx, center_y=cy, score=float(max_v))

    hit = best if best is not None and best.score >= threshold else None
    return PopupButtonProbe(
        hit=hit,
        best_score=best_score,
        threshold=threshold,
        search=region,
        template_path=tpl_path,
    )


def find_popup_button(
    frame: np.ndarray,
    config: dict[str, Any],
    section: str,
    *,
    default_template: str | None = None,
) -> ConfirmHit | None:
    """在配置的搜索区内模板匹配弹窗按钮（确认 / 执行任务等）。"""
    probe = _match_popup_button_in_frame(
        frame, config, section, default_template=default_template
    )
    return probe.hit if probe is not None else None


def probe_popup_button(
    frame: np.ndarray,
    config: dict[str, Any],
    section: str,
    *,
    default_template: str | None = None,
) -> PopupButtonProbe | None:
    """同 find_popup_button，但始终返回 best_score（未达阈值时 hit 为 None）。"""
    return _match_popup_button_in_frame(
        frame, config, section, default_template=default_template
    )


def find_confirm_button(
    frame: np.ndarray,
    config: dict[str, Any],
) -> ConfirmHit | None:
    """在搜索区内模板匹配确认按钮，命中返回客户区中心坐标，否则 None。"""
    return find_popup_button(
        frame,
        config,
        "confirm_button",
        default_template="assets/ui/confirm_button.png",
    )
