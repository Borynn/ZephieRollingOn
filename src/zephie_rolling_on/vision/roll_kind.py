"""Detect free / paid / START roll buttons via templates."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.vision.image_io import imread_unicode, imwrite_unicode

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

PROJECT_ROOT = project_root()
_DEFAULT_THRESHOLD = 0.70
_SCALES: tuple[float, ...] = (1.0, 0.9, 1.1, 0.8, 1.2)

RollKind = Literal["free", "paid", "start"]


@dataclass(frozen=True)
class RollKindHit:
    """匹配到的投骰种类（客户区坐标）。"""

    kind: RollKind
    center_x: int
    center_y: int
    score: float
    free_score: float
    paid_score: float
    start_score: float = 0.0


@dataclass(frozen=True)
class RollKindProbe:
    """探测结果（含未达阈值的分数，供调试）。"""

    hit: RollKindHit | None
    free_score: float
    paid_score: float
    start_score: float
    threshold: float
    search: dict[str, int]
    free_template_path: Path
    paid_template_path: Path
    start_template_path: Path


def _resolve_template_path(raw: str | None, default_rel: str) -> Path:
    if not raw:
        return PROJECT_ROOT / default_rel
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache(maxsize=12)
def _load_template_gray(path_str: str) -> np.ndarray | None:
    img = imread_unicode(path_str)
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _search_region(config: dict[str, Any]) -> dict[str, int] | None:
    cfg = (config or {}).get("roll_kind", {})
    search = cfg.get("search")
    if not isinstance(search, dict) or not search.get("width"):
        return None
    return {
        "left": int(search.get("left", 0)),
        "top": int(search.get("top", 0)),
        "width": int(search.get("width", 0)),
        "height": int(search.get("height", 0)),
    }


def _best_match_score(
    search_gray: np.ndarray,
    template: np.ndarray,
    *,
    origin_left: int,
    origin_top: int,
) -> tuple[float, int, int]:
    """返回 (best_score, center_x, center_y)；无匹配时 score=-1。"""
    sh, sw = search_gray.shape[:2]
    th0, tw0 = template.shape[:2]
    best_score = -1.0
    best_cx = origin_left
    best_cy = origin_top
    for scale in _SCALES:
        tw = max(8, int(round(tw0 * scale)))
        th = max(8, int(round(th0 * scale)))
        if tw > sw or th > sh:
            continue
        tmpl = template if scale == 1.0 else cv2.resize(
            template, (tw, th), interpolation=cv2.INTER_AREA
        )
        res = cv2.matchTemplate(search_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
        if max_v > best_score:
            best_score = float(max_v)
            best_cx = origin_left + int(max_l[0]) + tw // 2
            best_cy = origin_top + int(max_l[1]) + th // 2
    return best_score, best_cx, best_cy


def probe_roll_kind(
    frame: np.ndarray,
    config: dict[str, Any],
) -> RollKindProbe | None:
    """在 roll_kind.search 内匹配 free / paid / start，过阈值取最高分；start 优先。"""
    if frame is None or frame.size == 0:
        return None
    region = _search_region(config)
    if region is None:
        return None

    cfg = (config or {}).get("roll_kind", {})
    free_path = _resolve_template_path(
        cfg.get("free_template"),
        "assets/ui/free_dice_double.png",
    )
    paid_path = _resolve_template_path(
        cfg.get("paid_template"),
        "assets/ui/paid_dice.png",
    )
    start_path = _resolve_template_path(
        cfg.get("start_template"),
        "assets/ui/start_roll_btn.png",
    )
    free_tpl = _load_template_gray(str(free_path))
    paid_tpl = _load_template_gray(str(paid_path))
    start_tpl = _load_template_gray(str(start_path))
    if free_tpl is None or paid_tpl is None:
        return None

    fh, fw = frame.shape[:2]
    left = max(0, region["left"])
    top = max(0, region["top"])
    right = min(fw, left + region["width"])
    bottom = min(fh, top + region["height"])
    if right <= left or bottom <= top:
        return None

    search_gray = _to_gray(frame[top:bottom, left:right])
    threshold = float(cfg.get("match_threshold", _DEFAULT_THRESHOLD))

    free_score, free_cx, free_cy = _best_match_score(
        search_gray, free_tpl, origin_left=left, origin_top=top
    )
    paid_score, paid_cx, paid_cy = _best_match_score(
        search_gray, paid_tpl, origin_left=left, origin_top=top
    )
    start_score = -1.0
    start_cx, start_cy = left, top
    if start_tpl is not None:
        start_score, start_cx, start_cy = _best_match_score(
            search_gray, start_tpl, origin_left=left, origin_top=top
        )

    hit: RollKindHit | None = None
    # START 表示本局结束按钮，优先于 free/paid
    if start_tpl is not None and start_score >= threshold:
        hit = RollKindHit(
            kind="start",
            center_x=start_cx,
            center_y=start_cy,
            score=start_score,
            free_score=free_score,
            paid_score=paid_score,
            start_score=start_score,
        )
    elif free_score >= threshold or paid_score >= threshold:
        if free_score >= paid_score and free_score >= threshold:
            hit = RollKindHit(
                kind="free",
                center_x=free_cx,
                center_y=free_cy,
                score=free_score,
                free_score=free_score,
                paid_score=paid_score,
                start_score=start_score,
            )
        elif paid_score >= threshold:
            hit = RollKindHit(
                kind="paid",
                center_x=paid_cx,
                center_y=paid_cy,
                score=paid_score,
                free_score=free_score,
                paid_score=paid_score,
                start_score=start_score,
            )

    return RollKindProbe(
        hit=hit,
        free_score=free_score,
        paid_score=paid_score,
        start_score=start_score,
        threshold=threshold,
        search=region,
        free_template_path=free_path,
        paid_template_path=paid_path,
        start_template_path=start_path,
    )


def find_roll_kind(
    frame: np.ndarray,
    config: dict[str, Any],
) -> RollKindHit | None:
    """检测投骰按钮：free / paid / start；未命中返回 None。"""
    probe = probe_roll_kind(frame, config)
    return probe.hit if probe is not None else None


def save_roll_kind_debug(
    frame: np.ndarray,
    config: dict[str, Any],
) -> tuple[dict[str, Path], RollKindProbe | None]:
    """保存投骰种类检测调试图（search 裁剪 + 整窗 overlay）。"""
    from zephie_rolling_on.vision.capture import crop_region
    from zephie_rolling_on.vision.debug_paths import debug_image_path

    paths: dict[str, Path] = {}
    probe = probe_roll_kind(frame, config)
    if probe is None:
        return paths, None

    region = probe.search
    patch = crop_region(frame, region)
    search_path = debug_image_path("roll_kind_search.png")
    overlay_path = debug_image_path("roll_kind_overlay.png")
    if patch is not None and patch.size > 0:
        imwrite_unicode(str(search_path), patch)
        paths["search"] = search_path

    left = int(region["left"])
    top = int(region["top"])
    width = int(region["width"])
    height = int(region["height"])
    overlay = frame.copy()
    x2 = min(left + width, overlay.shape[1])
    y2 = min(top + height, overlay.shape[0])
    cv2.rectangle(overlay, (left, top), (x2, y2), (0, 200, 255), 2)
    if probe.hit is not None:
        status = f"HIT {probe.hit.kind}"
        color = {
            "free": (0, 255, 0),
            "paid": (0, 165, 255),
            "start": (255, 0, 255),
        }.get(probe.hit.kind, (0, 200, 255))
        cv2.circle(overlay, (probe.hit.center_x, probe.hit.center_y), 8, color, 2)
    else:
        status = "MISS"
    label = (
        f"roll_kind {status} free={probe.free_score:.3f} "
        f"paid={probe.paid_score:.3f} start={probe.start_score:.3f} "
        f"thr={probe.threshold:.2f}"
    )
    cv2.putText(
        overlay,
        label,
        (left, max(top - 8, 14)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (0, 200, 255),
        1,
        cv2.LINE_AA,
    )
    imwrite_unicode(str(overlay_path), overlay)
    paths["overlay"] = overlay_path
    return paths, probe
