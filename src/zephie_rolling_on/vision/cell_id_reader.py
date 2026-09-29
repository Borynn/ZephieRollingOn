"""Digit ROI reader (RapidOCR + ONNX) for cell id and dice-used counts."""
from __future__ import annotations

import threading
from typing import Any, Literal

import numpy as np

from zephie_rolling_on.vision.ocr_runtime import PreloadStatus

DigitKind = Literal["cell", "dice"]

_READER: Any | None = None
_INIT_LOCK = threading.Lock()
_READ_LOCK = threading.Lock()
_PRELOAD_THREAD: threading.Thread | None = None
_PRELOAD_STATUS: PreloadStatus = "idle"
_PRELOAD_ERROR: str = ""


def _kind_defaults(kind: DigitKind, config: dict[str, Any]) -> tuple[int, int, int]:
    """返回 (min_width, min_height, max_value)。"""
    if kind == "dice":
        max_value = int(config.get("dice_total", config.get("dice_used_max", 100)))
        min_width = int(
            config.get(
                "dice_ocr_fast_min_width",
                config.get("cell_id_fast_min_width", 320),
            )
        )
        min_height = int(
            config.get(
                "dice_ocr_fast_min_height",
                config.get("cell_id_fast_min_height", 80),
            )
        )
        return min_width, min_height, max_value

    max_value = int(config.get("cell_id_max", 2900))
    min_width = int(config.get("cell_id_fast_min_width", 320))
    min_height = int(config.get("cell_id_fast_min_height", 80))
    return min_width, min_height, max_value


def _prepare_patch_for_ocr(
    patch: np.ndarray,
    *,
    min_width: int,
    min_height: int,
) -> np.ndarray:
    if patch is None or patch.size == 0:
        return patch
    import cv2

    h, w = patch.shape[:2]
    scale = max(3, int(min_width / max(w, 1)), int(min_height / max(h, 1)))
    if scale <= 1:
        return patch
    return cv2.resize(
        patch,
        (w * scale, h * scale),
        interpolation=cv2.INTER_CUBIC,
    )


def _patch_variants(
    patch: np.ndarray,
    *,
    min_width: int,
    min_height: int,
) -> list[np.ndarray]:
    """放大 ROI + OTSU 正反二值化；解析成功即早停（由调用方控制）。"""
    import cv2

    base = _prepare_patch_for_ocr(
        patch,
        min_width=min_width,
        min_height=min_height,
    )
    if base is None or base.size == 0:
        return []
    out: list[np.ndarray] = [base]
    gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY) if len(base.shape) == 3 else base
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        enhanced = clahe.apply(gray)
    except Exception:
        enhanced = gray
    _, bw = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, bw_inv = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    for g in (bw, bw_inv):
        out.append(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))
    return out


def _parse_digits(
    text: str,
    *,
    max_value: int,
    max_digits: int,
) -> int | None:
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits.isdigit() or len(digits) > max_digits:
        return None
    value = int(digits)
    if value < 0 or value > max_value:
        return None
    return value


def _get_rapidocr_reader() -> Any | None:
    global _READER
    with _INIT_LOCK:
        if _READER is not None:
            return _READER
        return _load_reader_locked()


def digit_reader_preload_status() -> PreloadStatus:
    return _PRELOAD_STATUS


def digit_reader_preload_error() -> str:
    return _PRELOAD_ERROR


cell_id_preload_status = digit_reader_preload_status
cell_id_preload_error = digit_reader_preload_error


def preload_digit_reader(*, block: bool = False) -> None:
    """后台预加载 RapidOCR 并空跑，避免首次数字识别卡顿。"""
    global _PRELOAD_THREAD, _PRELOAD_STATUS

    if _READER is not None:
        _PRELOAD_STATUS = "ready"
        return
    if _PRELOAD_STATUS == "loading":
        if block and _PRELOAD_THREAD is not None:
            _PRELOAD_THREAD.join()
        return

    def _task() -> None:
        global _PRELOAD_STATUS
        with _INIT_LOCK:
            if _READER is not None:
                _PRELOAD_STATUS = "ready"
                return
            _PRELOAD_STATUS = "loading"
            _load_reader_locked()

    if block:
        _task()
        return

    if _PRELOAD_THREAD is not None and _PRELOAD_THREAD.is_alive():
        return

    _PRELOAD_STATUS = "loading"
    _PRELOAD_THREAD = threading.Thread(
        target=_task,
        name="digit-ocr-preload",
        daemon=True,
    )
    _PRELOAD_THREAD.start()


preload_cell_id_reader = preload_digit_reader


def _load_reader_locked() -> Any | None:
    global _READER, _PRELOAD_STATUS, _PRELOAD_ERROR
    try:
        from rapidocr import RapidOCR
    except ImportError:
        _PRELOAD_STATUS = "missing"
        _PRELOAD_ERROR = "未安装 rapidocr / onnxruntime（pip install rapidocr onnxruntime）"
        return None

    try:
        reader = RapidOCR()
        _warmup_reader(reader)
        _READER = reader
        _PRELOAD_STATUS = "ready"
        _PRELOAD_ERROR = ""
        return reader
    except Exception as exc:
        _PRELOAD_STATUS = "error"
        _PRELOAD_ERROR = str(exc)
        return None


def _warmup_reader(reader: Any) -> None:
    dummy = np.zeros((21, 43, 3), dtype=np.uint8)
    try:
        reader(dummy, use_det=False, use_cls=False)
    except Exception:
        pass


def is_digit_reader_ready() -> bool:
    return _get_rapidocr_reader() is not None


is_cell_id_fast_reader_ready = is_digit_reader_ready


def read_int_from_patch(
    patch: np.ndarray,
    *,
    max_value: int | None = None,
    kind: DigitKind = "cell",
    min_width: int | None = None,
    min_height: int | None = None,
    max_digits: int | None = None,
    config: dict[str, Any] | None = None,
) -> int | None:
    """
    识别纯数字 ROI（BGR numpy）。

    Parameters
    ----------
    patch:
        已裁好的数字区域（格子号或已用骰子次数）。
    max_value:
        合法上限（格子默认 2900，骰子默认 dice_total/100）。越界 → None。
    kind:
        ``\"cell\"`` / ``\"dice\"``，决定 config 里默认放大尺寸与上限。
    min_width / min_height:
        可选覆盖放大目标；默认按 kind 从 config 读取。
    max_digits:
        最多接受几位数字；默认 ``len(str(max_value))``。
    config:
        regions.yaml 等配置字典。

    Returns
    -------
    0～max_value 的整数；无法识别、越界或引擎未安装时返回 None。
    """
    if patch is None or patch.size == 0:
        return None
    cfg = config or {}
    def_w, def_h, def_max = _kind_defaults(kind, cfg)
    width = int(min_width if min_width is not None else def_w)
    height = int(min_height if min_height is not None else def_h)
    limit = int(max_value if max_value is not None else def_max)
    digits_cap = int(max_digits if max_digits is not None else max(1, len(str(limit))))

    reader = _get_rapidocr_reader()
    if reader is None:
        return None

    for variant in _patch_variants(patch, min_width=width, min_height=height):
        with _READ_LOCK:
            result = reader(variant, use_det=False, use_cls=False)
        txts = getattr(result, "txts", None) or ()
        merged = "".join(txts).strip()
        value = _parse_digits(merged, max_value=limit, max_digits=digits_cap)
        if value is not None:
            return value
    return None


def read_cell_id_from_patch(
    patch: np.ndarray,
    *,
    config: dict[str, Any] | None = None,
) -> int | None:
    """格子号：0～cell_id_max（默认 2900）。"""
    return read_int_from_patch(patch, kind="cell", config=config)


def read_dice_used_from_patch(
    patch: np.ndarray,
    *,
    config: dict[str, Any] | None = None,
) -> int | None:
    """已用投掷次数：0～dice_total（默认 100）。"""
    return read_int_from_patch(patch, kind="dice", config=config)
