"""调试图统一目录（避免写入项目根目录）。"""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = project_root()
DEBUG_DIR = PROJECT_ROOT / "data" / "debug"
CELL_OCR_DEBUG_DIR = DEBUG_DIR / "cell_ocr"
LUCKY_SLOT_DEBUG_DIR = DEBUG_DIR / "lucky_slot"


def ensure_debug_dir(subdir: Path | None = None) -> Path:
    target = subdir or DEBUG_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def debug_image_path(filename: str) -> Path:
    """单张调试图路径（执行任务/骰子等 GUI 测试用）。"""
    ensure_debug_dir(DEBUG_DIR)
    return DEBUG_DIR / filename


def save_debug_frame(frame, filename: str) -> Path | None:
    """把一帧存成调试图；失败返回 ``None``，**不抛异常**。

    供「一键测试」每步**开始前**留证：以前某步失败就直接 return，一张图都写不出来
    （表现为「开关开了却找不到图片」）。失败时最需要看的就是该步当时看到的画面。

    注意：存下来的是**归一化之后**的画面（``capture_window_client`` 已在出口把
    非 100% 缩放压回 100% 标定空间），所以尺寸与 ``regions.yaml`` 一致，可直接和
    模板对照；若排查的是缩放问题本身，需要另行抓取原始物理像素。
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    from zephie_rolling_on.vision.image_io import imwrite_unicode

    path = debug_image_path(filename)
    try:
        return path if imwrite_unicode(str(path), frame) else None
    except Exception:  # noqa: BLE001
        return None


def new_debug_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def debug_capture_paths(prefix: str, *, stem: str | None = None) -> tuple[Path, Path]:
    """返回 (图像路径, 结果 json 路径)，每次调用使用新时间戳。"""
    ts = stem or new_debug_timestamp()
    if prefix == "cell_ocr":
        base = ensure_debug_dir(CELL_OCR_DEBUG_DIR)
        image_path = base / f"{ts}_region.png"
        result_path = base / f"{ts}_result.json"
    elif prefix == "lucky_slot":
        base = ensure_debug_dir(LUCKY_SLOT_DEBUG_DIR)
        image_path = base / f"{ts}_slot.png"
        result_path = base / f"{ts}_result.json"
    else:
        base = ensure_debug_dir()
        image_path = base / f"{ts}_{prefix}.png"
        result_path = base / f"{ts}_{prefix}_result.json"
    return image_path, result_path
