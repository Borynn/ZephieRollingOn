"""Locate the adventure toggle button via expanding-box template match."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.vision.image_io import imread_unicode

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import yaml

PROJECT_ROOT = project_root()
CLICK_TARGETS_PATH = PROJECT_ROOT / "config" / "click_targets.yaml"

_DEFAULT_THRESHOLD = 0.78
_SCALES: tuple[float, ...] = (1.0, 0.95, 1.05, 0.9, 1.1)
_EXPAND_SIZES: tuple[int, ...] = (200, 400, 800, 1600)
_DEFAULT_TEMPLATE = "assets/ui/toggle_adventure_btn.png"
_DEFAULT_CENTER = (1625, 1045)


@dataclass(frozen=True)
class ToggleBtnHit:
    center_x: int
    center_y: int
    score: float
    template: str
    search_size: int | None  # None = full client
    search: dict[str, int]


@dataclass(frozen=True)
class ToggleBtnDetectResult:
    ok: bool
    hit: ToggleBtnHit | None
    message: str
    center_before: tuple[int, int]
    wrote: bool


def _resolve_template_path(raw: str) -> Path:
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


def load_toggle_btn_config(path: Path | None = None) -> dict[str, Any]:
    """从 click_targets.yaml 读取检测配置与上次中心。"""
    p = path or CLICK_TARGETS_PATH
    raw: dict[str, Any] = {}
    if p.is_file():
        with p.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            raw = loaded
    pt = raw.get("toggle_adventure_ui")
    cx, cy = _DEFAULT_CENTER
    if isinstance(pt, dict) and pt.get("x") is not None and pt.get("y") is not None:
        cx, cy = int(pt["x"]), int(pt["y"])

    block = raw.get("toggle_adventure_ui_detect")
    if not isinstance(block, dict):
        block = {}
    template = str(block.get("template") or _DEFAULT_TEMPLATE)
    sizes = block.get("expand_sizes")
    if isinstance(sizes, list) and sizes:
        expand = tuple(int(s) for s in sizes)
    else:
        expand = _EXPAND_SIZES
    return {
        "center_x": cx,
        "center_y": cy,
        "match_threshold": float(block.get("match_threshold", _DEFAULT_THRESHOLD)),
        "template": template,
        "expand_sizes": expand,
    }


def save_toggle_adventure_ui_center(
    x: int,
    y: int,
    path: Path | None = None,
) -> Path:
    """更新 click_targets.yaml 中 toggle_adventure_ui 的 x/y（不改动其它键）。"""
    import re

    p = path or CLICK_TARGETS_PATH
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    new_block = f"toggle_adventure_ui:\n  x: {int(x)}\n  y: {int(y)}\n"
    pat = re.compile(
        r"^toggle_adventure_ui:\s*\n(?:^[ \t]+(?:x|y):\s*.*\n)+",
        re.MULTILINE,
    )
    if pat.search(text):
        text = pat.sub(new_block, text, count=1)
    else:
        text = text.rstrip() + "\n\n# 开关大冒险界面（客户区绝对坐标；由按钮检测写回）\n" + new_block
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _clamp_search_box(
    cx: int,
    cy: int,
    size: int,
    fw: int,
    fh: int,
) -> tuple[int, int, int, int]:
    """以 (cx,cy) 为中心、边长 size 的框，裁到客户区 [0,fw)×[0,fh)。"""
    half = size // 2
    left = cx - half
    top = cy - half
    right = left + size
    bottom = top + size
    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > fw:
        shift = right - fw
        left = max(0, left - shift)
        right = fw
    if bottom > fh:
        shift = bottom - fh
        top = max(0, top - shift)
        bottom = fh
    return left, top, right, bottom


def _match_best_in_roi(
    search_gray: np.ndarray,
    *,
    left: int,
    top: int,
    template_path: str,
    template: np.ndarray,
    threshold: float,
) -> ToggleBtnHit | None:
    sh, sw = search_gray.shape[:2]
    best_score = -1.0
    best: ToggleBtnHit | None = None
    th0, tw0 = template.shape[:2]
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
        score = float(max_v)
        if score > best_score:
            best_score = score
            cx = left + int(max_l[0]) + tw // 2
            cy = top + int(max_l[1]) + th // 2
            best = ToggleBtnHit(
                center_x=cx,
                center_y=cy,
                score=score,
                template=template_path,
                search_size=None,
                search={"left": left, "top": top, "width": sw, "height": sh},
            )
    if best is None or best.score < threshold:
        return None
    return best


def detect_toggle_adventure_button(
    frame: np.ndarray,
    *,
    config: dict[str, Any] | None = None,
    on_log: Callable[[str], None] | None = None,
    write_click_targets: bool = True,
    click_targets_path: Path | None = None,
) -> ToggleBtnDetectResult:
    """在客户区截图中定位开关按钮；成功时写回 click_targets 中心坐标。"""
    log = on_log or (lambda _m: None)
    cfg = config or load_toggle_btn_config()
    cx0, cy0 = int(cfg["center_x"]), int(cfg["center_y"])
    threshold = float(cfg["match_threshold"])
    expand_sizes: tuple[int, ...] = tuple(cfg["expand_sizes"])

    if frame is None or frame.size == 0:
        return ToggleBtnDetectResult(
            ok=False,
            hit=None,
            message="截图为空",
            center_before=(cx0, cy0),
            wrote=False,
        )

    fh, fw = frame.shape[:2]
    tpl_path = _resolve_template_path(str(cfg["template"]))
    template = _load_template_gray(str(tpl_path))
    if template is None:
        return ToggleBtnDetectResult(
            ok=False,
            hit=None,
            message=f"模板读失败：{tpl_path}",
            center_before=(cx0, cy0),
            wrote=False,
        )
    try:
        tpl_label = str(tpl_path.relative_to(PROJECT_ROOT))
    except ValueError:
        tpl_label = str(tpl_path)

    # 扩框序列；若边长已盖住整屏则直接做整屏并结束
    stages: list[int | None] = list(expand_sizes)  # type: ignore[arg-type]
    stages.append(None)  # full client

    seen_boxes: set[tuple[int, int, int, int]] = set()
    last_best = 0.0
    for size in stages:
        if size is None:
            left, top, right, bottom = 0, 0, fw, fh
            size_label: int | None = None
        else:
            if size >= max(fw, fh) and min(fw, fh) <= size:
                # 已不小于客户区较长边：改走整屏，避免重复
                left, top, right, bottom = 0, 0, fw, fh
                size_label = None
            else:
                left, top, right, bottom = _clamp_search_box(cx0, cy0, int(size), fw, fh)
                size_label = int(size)

        box = (left, top, right, bottom)
        if box in seen_boxes:
            if size_label is None:
                break
            continue
        seen_boxes.add(box)

        if right - left < 8 or bottom - top < 8:
            continue

        search_gray = _to_gray(frame[top:bottom, left:right])
        hit = _match_best_in_roi(
            search_gray,
            left=left,
            top=top,
            template_path=tpl_label,
            template=template,
            threshold=threshold,
        )
        size_txt = "整屏" if size_label is None else f"{size_label}x{size_label}"
        if hit is None:
            # 取 soft best 供日志
            soft = _match_best_in_roi(
                search_gray,
                left=left,
                top=top,
                template_path=tpl_label,
                template=template,
                threshold=0.0,
            )
            soft_score = soft.score if soft else 0.0
            last_best = max(last_best, soft_score)
            log(
                f"[开关按钮] {size_txt} 未命中 "
                f"(搜索 {left},{top}-{right},{bottom} best={soft_score:.3f} < {threshold:.2f})"
            )
            if size_label is None:
                break
            continue

        hit = ToggleBtnHit(
            center_x=hit.center_x,
            center_y=hit.center_y,
            score=hit.score,
            template=hit.template,
            search_size=size_label,
            search=hit.search,
        )
        if write_click_targets:
            save_toggle_adventure_ui_center(
                hit.center_x,
                hit.center_y,
                path=click_targets_path,
            )
        size_txt = "整屏" if size_label is None else f"{size_label}x{size_label}"
        wrote = bool(write_click_targets)
        msg = (
            f"命中客户区 ({hit.center_x},{hit.center_y}) "
            f"score={hit.score:.3f} 框={size_txt} "
            f"模板={Path(hit.template).name}"
            + ("；已写回 toggle_adventure_ui" if wrote else "")
        )
        log(f"[开关按钮] {msg}")
        return ToggleBtnDetectResult(
            ok=True,
            hit=hit,
            message=msg,
            center_before=(cx0, cy0),
            wrote=wrote,
        )

    return ToggleBtnDetectResult(
        ok=False,
        hit=None,
        message=(
            f"未找到开关按钮（中心候选 {cx0},{cy0}，客户区 {fw}x{fh}，"
            f"best≈{last_best:.3f} < {threshold:.2f}）；保留原坐标"
        ),
        center_before=(cx0, cy0),
        wrote=False,
    )


def detect_and_update_toggle_button(
    hwnd: int,
    *,
    frame: np.ndarray | None = None,
    on_log: Callable[[str], None] | None = None,
) -> ToggleBtnDetectResult:
    """截屏（或复用帧）并检测；供启动 / 一键测试调用。"""
    from zephie_rolling_on.vision.capture import capture_window_client

    log = on_log or (lambda _m: None)
    img = frame
    if img is None:
        img = capture_window_client(int(hwnd))
    if img is None:
        return ToggleBtnDetectResult(
            ok=False,
            hit=None,
            message="无法截取游戏窗口",
            center_before=_DEFAULT_CENTER,
            wrote=False,
        )
    return detect_toggle_adventure_button(img, on_log=log)
