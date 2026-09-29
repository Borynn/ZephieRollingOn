"""窗口客户区截图。

后端按可用性自动选择，只保留两条路径：

1. **Windows Graphics Capture**（Win10 1803+）——不闪屏、不进入游戏进程、
   窗口被遮挡也能取到画面。系统低于 1803 时**不导入**该扩展（它链接 WinRT，
   在旧系统上导入即失败）。
2. **GDI 桌面裁切**——从合成后的桌面读取客户区。Win7 起即可用，不闪屏，且
   用 ``ClientToScreen`` + ``GetClientRect`` 定位，因此有边框时自动排除边框、
   无边框时自动等于整窗，无需判断用户的界面样式。代价是窗口必须未被遮挡。

已移除 PrintWindow：它会让窗口重绘而持续闪烁，对反外挂也更显眼，且存在
"整窗渲染 + 客户区 DC"导致的坐标偏移问题。桌面裁切能覆盖它原本的用途。
"""

from __future__ import annotations

import platform
import sys
from typing import Callable, Optional

import numpy as np

# Windows 10 1803 引入 Windows.Graphics.Capture。
WGC_MIN_BUILD = 17134


def windows_build() -> int:
    """当前 Windows 内部版本号；无法解析时返回 0。"""
    try:
        return int(platform.version().split(".")[-1])
    except Exception:
        return 0


_wgc_state: Optional[bool] = None


def wgc_supported() -> bool:
    """当前系统能否使用 WGC。

    低于 1803 直接返回 False，**不尝试导入** windows-capture——该扩展链接
    WinRT，在旧系统上导入会抛错，先判版本比事后捕获异常更可靠。
    """
    global _wgc_state
    if _wgc_state is None:
        if sys.platform != "win32" or windows_build() < WGC_MIN_BUILD:
            _wgc_state = False
        else:
            try:
                import windows_capture  # noqa: F401

                _wgc_state = True
            except Exception:
                _wgc_state = False
    return _wgc_state


def capture_screen(monitor_index: int = 1) -> np.ndarray:
    """截取主显示器，返回 BGR numpy 数组（OpenCV 格式）。"""
    import cv2
    import mss

    with mss.mss() as mss_ctx:
        mon = mss_ctx.monitors[monitor_index]
        shot = mss_ctx.grab(mon)
        img = np.array(shot)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def _capture_image_usable(image: np.ndarray) -> bool:
    """Reject the blank frames a failed capture produces.

    Observed failures are near-black (nothing rendered) and near-white (an empty
    surface). Rejecting only black lets a uniformly white frame through, which
    then silently yields zero matches.
    """
    if image is None or image.size == 0:
        return False
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    mean = float(gray.mean())
    std = float(gray.std())
    if mean < 4.0 and std < 4.0:
        return False
    if std < 8.0 and mean < 24.0:
        return False
    if mean > 250.0 and std < 12.0:
        return False
    return True


def _bitmap_to_bgr(bitmap: object, width: int, height: int) -> np.ndarray:
    """DDB bits -> BGR array.

    Measured behaviour: the buffer from ``GetBitmapBits`` on a
    ``CreateCompatibleBitmap`` selected into a screen/window DC is already
    TOP-DOWN (row 0 = top row), so no vertical flip is applied.
    """
    import cv2

    bmpstr = bitmap.GetBitmapBits(True)
    img = np.frombuffer(bmpstr, dtype=np.uint8)
    img.shape = (height, width, 4)
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def _is_client_occluded(hwnd: int) -> bool:
    """客户区是否被 Z 序更高的窗口覆盖。

    做法：从游戏顶层窗口沿 ``GW_HWNDPREV`` 往上遍历 Z 序，对每个可见、非最小化
    的窗口取其**可见外框**（DWM 扩展边框，不含阴影）与客户区矩形求交，一旦相交
    即判定被遮挡。

    用 DWM 边框而非 ``GetWindowRect``：后者在 Win10+ 含不可见阴影，相邻窗口的
    阴影会造成误判。

    保守起见：任何视觉上叠在客户区之上的窗口都算遮挡——桌面裁切会把它们一并
    截进去，从而污染识别。
    """
    if sys.platform != "win32":
        return False
    try:
        import win32gui

        from zephie_rolling_on.vision.win32_window import _client_size_screen_pixels

        x, y, cw, ch = _client_size_screen_pixels(hwnd)
        if cw <= 0 or ch <= 0:
            return False
        left, top, right, bottom = x, y, x + cw, y + ch

        root = int(win32gui.GetAncestor(int(hwnd), 2)) or int(hwnd)
        GW_HWNDPREV = 3
        win = int(win32gui.GetWindow(root, GW_HWNDPREV))
        guard = 0
        while win and guard < 512:
            guard += 1
            try:
                if win32gui.IsWindowVisible(win) and not win32gui.IsIconic(win):
                    wl, wt, wr, wb = _window_rect(win)
                    if wr > wl and wb > wt and not (
                        wr <= left or wl >= right or wb <= top or wt >= bottom
                    ):
                        return True
            except Exception:
                pass
            win = int(win32gui.GetWindow(win, GW_HWNDPREV))
    except Exception:
        return False
    return False


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """窗口可见外框（DWM 扩展边框，不含阴影）。"""
    import ctypes
    from ctypes import wintypes

    import win32gui

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    rect = RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            int(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if hr == 0 and rect.right > rect.left and rect.bottom > rect.top:
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        pass
    return win32gui.GetWindowRect(int(hwnd))


def screen_capture_client(hwnd: int) -> np.ndarray | None:
    """GDI 桌面裁切：从合成后的桌面读取客户区。

    不要求窗口重绘，所以不闪屏；坐标来自客户区，因此有边框时自动排除边框、
    无边框时自动等于整窗。窗口尺寸变化后无需重新标定。
    """
    if sys.platform != "win32":
        return None

    import win32gui
    import win32ui
    from ctypes import windll

    from zephie_rolling_on.vision.win32_window import _client_size_screen_pixels

    if not win32gui.IsWindow(hwnd):
        return None
    x, y, cw, ch = _client_size_screen_pixels(hwnd)
    if cw <= 0 or ch <= 0:
        return None

    screen_dc = win32gui.GetDC(0)
    if not screen_dc:
        return None
    mfc_dc = win32ui.CreateDCFromHandle(screen_dc)
    mem_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(mfc_dc, cw, ch)
        mem_dc.SelectObject(bitmap)
        SRCCOPY = 0x00CC0020
        if not windll.gdi32.BitBlt(
            mem_dc.GetSafeHdc(), 0, 0, cw, ch, screen_dc, x, y, SRCCOPY
        ):
            return None
        return _bitmap_to_bgr(bitmap, cw, ch)
    except Exception:
        return None
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        mem_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(0, screen_dc)


# ---------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------

# ""       正常
# "occluded" 客户区被其他窗口覆盖（调用方应提示用户并停止，而不是继续识别）
_CAPTURE_BLOCK_REASON = ""
_CAPTURE_FAILURE_NOTE = ""


def capture_block_reason() -> str:
    """最近一次截图失败的原因类型；见 ``_CAPTURE_BLOCK_REASON`` 的取值说明。"""
    return _CAPTURE_BLOCK_REASON


def last_capture_failure_note() -> str:
    return _CAPTURE_FAILURE_NOTE


def _report_failure(note: str, *, block_reason: str = "") -> None:
    global _CAPTURE_FAILURE_NOTE, _CAPTURE_BLOCK_REASON
    _CAPTURE_FAILURE_NOTE = note
    _CAPTURE_BLOCK_REASON = block_reason
    try:
        from zephie_rolling_on.vision.capture_wgc import _set_method

        _set_method("failed")
    except Exception:
        pass


def _clear_failure() -> None:
    global _CAPTURE_FAILURE_NOTE, _CAPTURE_BLOCK_REASON
    _CAPTURE_FAILURE_NOTE = ""
    _CAPTURE_BLOCK_REASON = ""


def last_capture_method() -> str:
    try:
        from zephie_rolling_on.vision.capture_wgc import (
            last_capture_method as _wgc_method,
        )

        return _wgc_method()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

OCCLUSION_HINT = (
    "游戏画面被其他窗口遮挡。请让游戏窗口保持在最前（不要被聊天窗口、"
    "浏览器或其他程序盖住），然后重试。"
)


def capture_window_client(hwnd: int) -> Optional[np.ndarray]:
    """截取游戏窗口客户区（BGR）。

    WGC 可用时优先用它；否则退回桌面裁切。窗口被遮挡时桌面裁切会截到上层
    窗口，因此先判定遮挡并直接返回 ``None``（``capture_block_reason()`` 会是
    ``"occluded"``），由调用方提示用户。
    """
    if sys.platform != "win32":
        return None

    from zephie_rolling_on.vision.win32_window import is_window_capturable

    if not is_window_capturable(hwnd):
        _report_failure("游戏窗口不可截取（可能已最小化或句柄失效）")
        return None

    if wgc_supported():
        from zephie_rolling_on.vision.capture_wgc import capture_window_wgc

        wgc_img = capture_window_wgc(hwnd)
        if wgc_img is not None:
            _clear_failure()
            return wgc_img

    if _is_client_occluded(hwnd):
        _report_failure(OCCLUSION_HINT, block_reason="occluded")
        return None

    shot = screen_capture_client(hwnd)
    if shot is not None and _capture_image_usable(shot):
        try:
            from zephie_rolling_on.vision.capture_wgc import _set_method

            _set_method("screen")
        except Exception:
            pass
        _clear_failure()
        return shot

    if not wgc_supported():
        _report_failure(
            "桌面裁切未取到可用画面。请确认游戏窗口可见、未最小化，"
            "且没有处于独占全屏；必要时改用窗口化模式。"
        )
    else:
        _report_failure(
            "窗口捕获失败。彩虹岛建议：窗口化/无边框窗口、关闭独占全屏；"
            "若仍黑屏，在兼容性中禁用全屏优化并以 Win8 兼容运行。"
        )
    return None


def crop_region(image: np.ndarray, region: dict) -> np.ndarray:
    """裁剪 ROI；越界时夹紧，无效区域返回空数组。"""
    if image is None or image.size == 0:
        return image
    img_h, img_w = image.shape[:2]
    left = max(0, int(region["left"]))
    top = max(0, int(region["top"]))
    right = min(img_w, left + int(region["width"]))
    bottom = min(img_h, top + int(region["height"]))
    if right <= left or bottom <= top:
        return image[0:0, 0:0]
    return image[top:bottom, left:right]
