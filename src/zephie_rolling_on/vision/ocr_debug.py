from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from zephie_rolling_on.vision.capture import crop_region
from zephie_rolling_on.vision.debug_paths import DEBUG_DIR, debug_capture_paths, ensure_debug_dir
from zephie_rolling_on.vision.image_io import imwrite_unicode

_last_cell_debug_key: str | None = None


def save_cell_ocr_debug(
    patch: np.ndarray,
    result: dict[str, Any],
    *,
    snapshot: bool = False,
) -> dict[str, Path]:
    """
    保存格子 OCR 调试图。
    - snapshot=True（一键测试）：固定写入 data/debug/cell_ocr_region.png + cell_ocr_result.json
    - snapshot=False（规划循环）：追加 data/debug/cell_ocr/<ts>_region.png + result.json
    """
    import cv2

    global _last_cell_debug_key

    paths: dict[str, Path] = {}
    if patch is None or patch.size == 0:
        return paths

    if snapshot:
        ensure_debug_dir(DEBUG_DIR)
        image_path = DEBUG_DIR / "cell_ocr_region.png"
        json_path = DEBUG_DIR / "cell_ocr_result.json"
    else:
        key = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        if key == _last_cell_debug_key:
            return paths
        _last_cell_debug_key = key
        image_path, json_path = debug_capture_paths("cell_ocr")
    imwrite_unicode(str(image_path), patch)
    paths["region"] = image_path

    payload = {
        "kind": "cell_ocr",
        "image": image_path.name,
        **result,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["result"] = json_path
    return paths


def save_dice_ocr_debug(
    frame: np.ndarray,
    region: dict[str, Any],
    ocr_input: np.ndarray | None = None,
) -> dict[str, Path]:
    """骰子 OCR 调试图（GUI 测试用，仍写单张至 data/debug/）。"""
    import cv2

    from zephie_rolling_on.vision.debug_paths import DEBUG_DIR, ensure_debug_dir

    paths: dict[str, Path] = {}
    left = int(region.get("left", 0))
    top = int(region.get("top", 0))
    width = int(region.get("width", 0))
    height = int(region.get("height", 0))

    if width <= 0 or height <= 0:
        return paths

    ensure_debug_dir(DEBUG_DIR)
    region_path = DEBUG_DIR / "dice_ocr_region.png"
    overlay_path = DEBUG_DIR / "dice_ocr_overlay.png"
    input_path = DEBUG_DIR / "dice_ocr_input.png"

    patch = crop_region(frame, region)
    if patch is None or patch.size == 0:
        empty = np.zeros((max(height, 1), max(width, 1), 3), dtype=np.uint8)
        imwrite_unicode(str(region_path), empty)
    else:
        imwrite_unicode(str(region_path), patch)
    paths["region"] = region_path

    if ocr_input is not None and ocr_input.size > 0:
        imwrite_unicode(str(input_path), ocr_input)
        paths["ocr_input"] = input_path

    if frame is not None and frame.size > 0:
        overlay = frame.copy()
        x2 = min(left + width, overlay.shape[1])
        y2 = min(top + height, overlay.shape[0])
        cv2.rectangle(overlay, (left, top), (x2, y2), (0, 140, 255), 2)
        cv2.putText(
            overlay,
            f"dice ROI {left},{top} {width}x{height}",
            (left, max(top - 8, 0)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 140, 255),
            1,
            cv2.LINE_AA,
        )
        imwrite_unicode(str(overlay_path), overlay)
        paths["overlay"] = overlay_path

    return paths


def format_patch_stats(patch: np.ndarray) -> str:
    if patch is None or patch.size == 0:
        return "裁剪图为空"
    h, w = patch.shape[:2]
    mean = float(patch.mean())
    return f"裁剪尺寸 {w}×{h}，平均像素值≈{mean:.1f}"
