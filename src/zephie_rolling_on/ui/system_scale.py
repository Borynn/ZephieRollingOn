"""读取指定窗口所在显示器的系统缩放。"""

from __future__ import annotations

import ctypes
import sys

SUPPORTED_SCALE_PERCENT = 100
MONITOR_DEFAULTTONEAREST = 2


def _monitor_for_window(hwnd: int) -> int | None:
    try:
        user32 = ctypes.windll.user32
        monitor_from_window = user32.MonitorFromWindow
        monitor_from_window.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        monitor_from_window.restype = ctypes.c_void_p
        monitor = monitor_from_window(ctypes.c_void_p(int(hwnd)), MONITOR_DEFAULTTONEAREST)
        return int(monitor) if monitor else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _scale_from_monitor(monitor: int) -> int | None:
    """优先取显示器缩放百分比，再用同一显示器的有效 DPI 兜底。"""
    try:
        shcore = ctypes.windll.shcore
    except (AttributeError, OSError):
        return None

    # Windows 8.1+：直接返回 DEVICE_SCALE_FACTOR 百分比。
    try:
        get_scale = shcore.GetScaleFactorForMonitor
        get_scale.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
        get_scale.restype = ctypes.c_long
        percent = ctypes.c_int()
        if get_scale(ctypes.c_void_p(monitor), ctypes.byref(percent)) == 0:
            if percent.value > 0:
                return int(percent.value)
    except (AttributeError, OSError, TypeError, ValueError):
        pass

    # 旧版系统或 API 不可用时，从同一显示器的有效 DPI 换算。
    try:
        get_dpi = shcore.GetDpiForMonitor
        get_dpi.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        )
        get_dpi.restype = ctypes.c_long
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        if get_dpi(
            ctypes.c_void_p(monitor), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
        ) == 0 and dpi_x.value > 0:
            return int(round(dpi_x.value / 96 * 100))
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return None


def system_scale_percent(hwnd: int | None = None) -> int | None:
    """返回 ``hwnd`` 所在显示器的缩放百分比；无法读取时返回 ``None``。"""
    if sys.platform != "win32":
        return SUPPORTED_SCALE_PERCENT
    if not hwnd:
        return None
    monitor = _monitor_for_window(int(hwnd))
    return _scale_from_monitor(monitor) if monitor else None
