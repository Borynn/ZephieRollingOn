from __future__ import annotations

import threading
from typing import Any, Literal

_OCR_READER: Any = None
_INIT_LOCK = threading.Lock()
_READ_LOCK = threading.Lock()
_PRELOAD_THREAD: threading.Thread | None = None

PreloadStatus = Literal["idle", "loading", "ready", "missing", "error"]
_status: PreloadStatus = "idle"
_error_message: str = ""


def ocr_preload_status() -> PreloadStatus:
    return _status


def ocr_preload_error() -> str:
    return _error_message


def is_ocr_ready() -> bool:
    return _OCR_READER is not None


def ocr_readtext(image: Any, *, allowlist: str | None = None) -> list[Any]:
    """串行调用 EasyOCR readtext，避免多线程同时识别导致结果错乱。"""
    reader = get_ocr_reader()
    if reader is None:
        return []
    with _READ_LOCK:
        if allowlist:
            return reader.readtext(image, allowlist=allowlist)
        return reader.readtext(image)


def get_ocr_reader() -> Any | None:
    """线程安全获取 EasyOCR Reader；必要时阻塞直到加载完成。"""
    global _OCR_READER
    if _OCR_READER is not None:
        return _OCR_READER
    with _INIT_LOCK:
        if _OCR_READER is not None:
            return _OCR_READER
        return _load_reader_locked()


def preload_ocr_reader(*, block: bool = False) -> None:
    """
    在后台预加载 EasyOCR 模型并做一次空跑，避免首次识别卡住 UI。
    block=True 时在当前线程同步加载（供 CLI / 测试）。
    """
    global _PRELOAD_THREAD, _status

    if _OCR_READER is not None:
        _status = "ready"
        return
    if _status == "loading":
        if block and _PRELOAD_THREAD is not None:
            _PRELOAD_THREAD.join()
        return

    def _task() -> None:
        global _status
        with _INIT_LOCK:
            if _OCR_READER is not None:
                _status = "ready"
                return
            _status = "loading"
            _load_reader_locked()

    if block:
        _task()
        return

    if _PRELOAD_THREAD is not None and _PRELOAD_THREAD.is_alive():
        return

    _status = "loading"
    _PRELOAD_THREAD = threading.Thread(target=_task, name="ocr-preload", daemon=True)
    _PRELOAD_THREAD.start()


def _load_reader_locked() -> Any | None:
    global _OCR_READER, _status, _error_message
    try:
        import easyocr  # noqa: PLC0415
    except ImportError:
        _status = "missing"
        _error_message = "未安装 easyocr"
        return None

    try:
        reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
        _warmup_reader(reader)
        _OCR_READER = reader
        _status = "ready"
        _error_message = ""
        return reader
    except Exception as exc:
        _status = "error"
        _error_message = str(exc)
        return None


def _warmup_reader(reader: Any) -> None:
    """首次 readtext 较慢，用小图在后台先跑一遍。"""
    import numpy as np

    dummy = np.zeros((48, 160, 3), dtype=np.uint8)
    try:
        reader.readtext(dummy)
    except Exception:
        pass
