"""Preload digit OCR engines per regions.yaml."""
from __future__ import annotations

import threading
from typing import Any

from zephie_rolling_on.vision.cell_id_reader import (
    digit_reader_preload_error,
    digit_reader_preload_status,
    preload_digit_reader,
)
from zephie_rolling_on.vision.ocr_runtime import (
    PreloadStatus,
    ocr_preload_error,
    ocr_preload_status,
    preload_ocr_reader,
)
from zephie_rolling_on.vision.recognize import load_regions

_PRELOAD_THREAD: threading.Thread | None = None
_WANTS_RAPID: bool = False
_WANTS_EASY: bool = False


def _digit_engine_name(config: dict[str, Any]) -> str:
    raw = config.get("digit_ocr_engine", config.get("cell_id_engine", "fast"))
    return str(raw).strip().lower()


def _aggregate_status(*statuses: PreloadStatus) -> PreloadStatus:
    if not statuses:
        return "idle"
    if any(s == "loading" for s in statuses):
        return "loading"
    if any(s == "error" for s in statuses):
        return "error"
    if any(s == "missing" for s in statuses):
        return "missing"
    if all(s == "ready" for s in statuses):
        return "ready"
    return "idle"


def recognition_preload_status() -> PreloadStatus:
    statuses: list[PreloadStatus] = []
    if _WANTS_RAPID:
        statuses.append(digit_reader_preload_status())
    if _WANTS_EASY:
        statuses.append(ocr_preload_status())
    if not statuses:
        return "idle"
    return _aggregate_status(*statuses)


def recognition_preload_log_lines(*, config: dict[str, Any] | None = None) -> list[str]:
    _ = config
    lines: list[str] = []

    if _WANTS_RAPID:
        rapid_status = digit_reader_preload_status()
        if rapid_status == "ready":
            lines.append("RapidOCR（格子+骰子）已就绪。")
        elif rapid_status == "missing":
            lines.append(
                "未安装 rapidocr（格子/骰子 fast 需要）：pip install rapidocr onnxruntime"
            )
        elif rapid_status == "error":
            lines.append(f"RapidOCR 预加载失败：{digit_reader_preload_error()}")

    if _WANTS_EASY:
        easy_status = ocr_preload_status()
        if easy_status == "ready":
            lines.append("EasyOCR（格子+骰子 legacy）已就绪。")
        elif easy_status == "missing":
            lines.append("未安装 easyocr（legacy 需要）：pip install easyocr")
        elif easy_status == "error":
            lines.append(f"EasyOCR 预加载失败：{ocr_preload_error()}")

    if not lines and recognition_preload_status() == "ready":
        lines.append("识别引擎已就绪。")
    return lines


def resolve_preload_targets(
    *,
    config: dict[str, Any] | None = None,
    preload_rapid: bool | None = None,
    preload_easy: bool | None = None,
) -> tuple[bool, bool]:
    """Resolve which OCR engines to preload."""
    cfg = config or load_regions()
    engine = _digit_engine_name(cfg)
    want_rapid = (
        bool(preload_rapid) if preload_rapid is not None else engine == "fast"
    )
    want_easy = (
        bool(preload_easy) if preload_easy is not None else engine != "fast"
    )
    return want_rapid, want_easy


def preload_recognition_engines(
    *,
    block: bool = False,
    config: dict[str, Any] | None = None,
    preload_rapid: bool | None = None,
    preload_easy: bool | None = None,
) -> None:
    """Preload digit OCR engines (Rapid and/or Easy)."""
    global _PRELOAD_THREAD, _WANTS_RAPID, _WANTS_EASY

    _WANTS_RAPID, _WANTS_EASY = resolve_preload_targets(
        config=config,
        preload_rapid=preload_rapid,
        preload_easy=preload_easy,
    )
    if not _WANTS_RAPID and not _WANTS_EASY:
        return

    if recognition_preload_status() == "ready":
        return
    if recognition_preload_status() == "loading" and not block:
        return

    def _task() -> None:
        if _WANTS_RAPID:
            preload_digit_reader(block=True)
        if _WANTS_EASY:
            preload_ocr_reader(block=True)

    if block:
        _task()
        return

    if _PRELOAD_THREAD is not None and _PRELOAD_THREAD.is_alive():
        return

    _PRELOAD_THREAD = threading.Thread(
        target=_task,
        name="recognition-preload",
        daemon=True,
    )
    _PRELOAD_THREAD.start()
