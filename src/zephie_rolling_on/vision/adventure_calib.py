"""Adventure frame auto-calibration from button templates."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from zephie_rolling_on.data.adventure_frame import ADVENTURE_FRAME_PATH, PROJECT_ROOT
from zephie_rolling_on.vision.image_io import imread_unicode

# 与 ui.frame_locator.FRAME_* 保持一致
FRAME_WIDTH = 1248
FRAME_HEIGHT = 766

_DEFAULT_THRESHOLD = 0.72
_SCALES: tuple[float, ...] = (1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15)

_DEFAULT_MARKERS: tuple[dict[str, Any], ...] = (
    {
        "id": "daoju",
        "template": "assets/ui/adventure_calib_daoju.png",
        "frame_x": 1035,
        "frame_y": 723,
    },
    {
        "id": "paihang",
        "template": "assets/ui/adventure_calib_paihang.png",
        "frame_x": 1168,
        "frame_y": 723,
    },
)


@dataclass(frozen=True)
class CalibMarkerHit:
    marker_id: str
    center_x: int
    center_y: int
    score: float
    frame_x: int
    frame_y: int
    template: str

    @property
    def anchor_left(self) -> int:
        return int(self.center_x) - int(self.frame_x)

    @property
    def anchor_top(self) -> int:
        return int(self.center_y) - int(self.frame_y)


@dataclass(frozen=True)
class AdventureAutoCalibResult:
    ok: bool
    anchor_left: int | None
    anchor_top: int
    hits: tuple[CalibMarkerHit, ...]
    message: str
    search: dict[str, int]


def _resolve_template_path(raw: str | None) -> Path:
    if not raw:
        return PROJECT_ROOT / "assets" / "ui" / "adventure_calib_daoju.png"
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache(maxsize=8)
def _load_template_gray(path_str: str) -> np.ndarray | None:
    img = imread_unicode(path_str)
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def load_auto_calib_config(path: Path | None = None) -> dict[str, Any]:
    p = path or ADVENTURE_FRAME_PATH
    raw: dict[str, Any] = {}
    if p.is_file():
        with p.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            raw = loaded
    block = raw.get("auto_calib") if isinstance(raw.get("auto_calib"), dict) else {}
    markers = block.get("markers")
    if not isinstance(markers, list) or not markers:
        markers = list(_DEFAULT_MARKERS)
    return {
        "search_left": int(block.get("search_left", 800)),
        "search_top": int(block.get("search_top", 450)),
        "match_threshold": float(block.get("match_threshold", _DEFAULT_THRESHOLD)),
        "markers": markers,
    }


def _match_one(
    search_gray: np.ndarray,
    *,
    left: int,
    top: int,
    template: np.ndarray,
    threshold: float,
) -> tuple[float, int, int] | None:
    """Return (score, center_x, center_y) in full-frame client coords, or None."""
    sh, sw = search_gray.shape[:2]
    th0, tw0 = template.shape[:2]
    best_score = -1.0
    best_cx = 0
    best_cy = 0
    for scale in _SCALES:
        tw = max(8, int(round(tw0 * scale)))
        th = max(8, int(round(th0 * scale)))
        if tw > sw or th > sh:
            continue
        tmpl = (
            template
            if scale == 1.0
            else cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        )
        res = cv2.matchTemplate(search_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
        if float(max_v) > best_score:
            best_score = float(max_v)
            best_cx = left + int(max_l[0]) + tw // 2
            best_cy = top + int(max_l[1]) + th // 2
    if best_score < threshold:
        return None
    return best_score, best_cx, best_cy


def detect_adventure_frame_anchor(
    frame: np.ndarray,
    *,
    config: dict[str, Any] | None = None,
) -> AdventureAutoCalibResult:
    """在客户区截图中匹配标定按钮，反推大冒险框左上角锚点。"""
    cfg = config or load_auto_calib_config()
    fh, fw = frame.shape[:2]
    left = max(0, int(cfg["search_left"]))
    top = max(0, int(cfg["search_top"]))
    right = fw
    bottom = fh
    search = {"left": left, "top": top, "width": max(0, right - left), "height": max(0, bottom - top)}
    if right <= left or bottom <= top:
        return AdventureAutoCalibResult(
            ok=False,
            anchor_left=None,
            anchor_top=0,
            hits=(),
            message=f"搜索区无效：客户区 {fw}x{fh}，起点 ({left},{top})",
            search=search,
        )

    search_gray = _to_gray(frame[top:bottom, left:right])
    threshold = float(cfg["match_threshold"])
    hits: list[CalibMarkerHit] = []
    miss_notes: list[str] = []

    for raw in cfg["markers"]:
        if not isinstance(raw, dict):
            continue
        mid = str(raw.get("id") or "marker")
        tpl_path = _resolve_template_path(str(raw.get("template") or ""))
        template = _load_template_gray(str(tpl_path))
        if template is None:
            miss_notes.append(f"{mid}: 模板读失败 ({tpl_path})")
            continue
        matched = _match_one(
            search_gray,
            left=left,
            top=top,
            template=template,
            threshold=threshold,
        )
        if matched is None:
            # 再跑一遍拿 best_score 方便日志
            soft = _match_one(
                search_gray,
                left=left,
                top=top,
                template=template,
                threshold=0.0,
            )
            score = soft[0] if soft else 0.0
            miss_notes.append(f"{mid}: 未达阈值 (best={score:.3f} < {threshold:.2f})")
            continue
        score, cx, cy = matched
        hits.append(
            CalibMarkerHit(
                marker_id=mid,
                center_x=cx,
                center_y=cy,
                score=score,
                frame_x=int(raw.get("frame_x", 0)),
                frame_y=int(raw.get("frame_y", 0)),
                template=str(tpl_path),
            )
        )

    if not hits:
        detail = "；".join(miss_notes) if miss_notes else "无可用模板"
        return AdventureAutoCalibResult(
            ok=False,
            anchor_left=None,
            anchor_top=0,
            hits=(),
            message=f"未匹配到标定按钮（搜索 {left},{top}→右下）。{detail}",
            search=search,
        )

    # 多命中：对锚点取中位数，降低单一误匹配影响
    lefts = sorted(h.anchor_left for h in hits)
    tops = sorted(h.anchor_top for h in hits)
    mid = len(hits) // 2
    if len(hits) % 2:
        ax, ay = lefts[mid], tops[mid]
    else:
        ax = (lefts[mid - 1] + lefts[mid]) // 2
        ay = (tops[mid - 1] + tops[mid]) // 2

    warn = ""
    if len(hits) >= 2:
        spread = max(abs(h.anchor_left - ax) + abs(h.anchor_top - ay) for h in hits)
        if spread > 8:
            warn = f"；注意：两按钮推出的锚点偏差较大 (max_L1={spread})"

    # 锚点合理范围：框应落在客户区内（允许少量越界）
    if ax < -50 or ay < -50 or ax + FRAME_WIDTH < 100 or ay + FRAME_HEIGHT < 100:
        return AdventureAutoCalibResult(
            ok=False,
            anchor_left=ax,
            anchor_top=ay,
            hits=tuple(hits),
            message=(
                f"算出锚点 ({ax},{ay}) 不合理（框 {FRAME_WIDTH}x{FRAME_HEIGHT}，"
                f"客户区 {fw}x{fh}）。命中: "
                + ", ".join(f"{h.marker_id}@({h.center_x},{h.center_y})={h.score:.3f}" for h in hits)
            ),
            search=search,
        )

    hit_txt = ", ".join(
        f"{h.marker_id}@客户区({h.center_x},{h.center_y}) score={h.score:.3f} → 锚点候选({h.anchor_left},{h.anchor_top})"
        for h in hits
    )
    if miss_notes:
        hit_txt += "；" + "；".join(miss_notes)

    return AdventureAutoCalibResult(
        ok=True,
        anchor_left=ax,
        anchor_top=ay,
        hits=tuple(hits),
        message=f"自动标定锚点=({ax},{ay})；{hit_txt}{warn}",
        search=search,
    )
