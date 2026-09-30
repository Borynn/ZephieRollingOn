"""Save debug images for dimmed popup-button detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from zephie_rolling_on.vision.capture import crop_region
from zephie_rolling_on.vision.confirm_button import PopupButtonProbe, probe_popup_button

from zephie_rolling_on.vision.debug_paths import debug_image_path
from zephie_rolling_on.vision.image_io import imwrite_unicode

DEBUG_EXECUTE_TASK_SEARCH_PATH = debug_image_path("execute_task_search.png")
DEBUG_EXECUTE_TASK_OVERLAY_PATH = debug_image_path("execute_task_overlay.png")


DEBUG_ADVENTURE_COMPLETE_SEARCH_PATH = debug_image_path("adventure_complete_search.png")
DEBUG_ADVENTURE_COMPLETE_OVERLAY_PATH = debug_image_path("adventure_complete_overlay.png")


def save_popup_button_debug(
    frame: np.ndarray,
    config: dict[str, Any],
    section: str,
    *,
    default_template: str | None = None,
    debug_prefix: str,
) -> tuple[dict[str, Path], PopupButtonProbe | None]:
    """保存弹窗按钮检测调试图（search 裁剪 + 整窗 overlay）。"""
    import cv2

    paths: dict[str, Path] = {}
    probe = probe_popup_button(
        frame,
        config,
        section,
        default_template=default_template,
    )
    if probe is None:
        return paths, None

    region = probe.search
    patch = crop_region(frame, region)
    search_path = debug_image_path(f"{debug_prefix}_search.png")
    overlay_path = debug_image_path(f"{debug_prefix}_overlay.png")
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
    cv2.rectangle(overlay, (left, top), (x2, y2), (255, 0, 255), 2)
    status = "HIT" if probe.hit is not None else "MISS"
    label = (
        f"{section} {status} best={probe.best_score:.3f} "
        f"thr={probe.threshold:.2f} {left},{top} {width}x{height}"
    )
    cv2.putText(
        overlay,
        label,
        (left, max(top - 8, 14)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 0, 255),
        1,
        cv2.LINE_AA,
    )
    if probe.hit is not None:
        cv2.circle(overlay, (probe.hit.center_x, probe.hit.center_y), 8, (0, 255, 0), 2)
        cv2.putText(
            overlay,
            f"center {probe.hit.center_x},{probe.hit.center_y}",
            (probe.hit.center_x + 10, probe.hit.center_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    imwrite_unicode(str(overlay_path), overlay)
    paths["overlay"] = overlay_path
    return paths, probe


def save_execute_task_button_debug(
    frame: np.ndarray,
    config: dict[str, Any],
) -> tuple[dict[str, Path], PopupButtonProbe | None]:
    """Save execute-task button debug crops."""
    return save_popup_button_debug(
        frame,
        config,
        "execute_task_button",
        default_template="assets/ui/execute_task_button.png",
        debug_prefix="execute_task",
    )


def save_adventure_complete_banner_debug(
    frame: np.ndarray,
    config: dict[str, Any],
) -> tuple[dict[str, Path], PopupButtonProbe | None]:
    """保存冒险完成横幅检测调试图。"""
    return save_popup_button_debug(
        frame,
        config,
        "adventure_complete_banner",
        default_template="assets/ui/adventure_complete_banner.png",
        debug_prefix="adventure_complete",
    )
