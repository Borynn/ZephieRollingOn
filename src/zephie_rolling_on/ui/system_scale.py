"""系统显示缩放检测。

为什么必须拦：识图依赖「逻辑坐标 == 物理像素」这一前提。进程是 DPI-unaware，
系统缩放一旦不是 100%，Windows 会把客户区坐标和 GetSystemMetrics 虚拟化，
桌面裁切就会按错误的位置 BitBlt，模板匹配随之整体偏移，识别必然失败。
所以启动时先检测，缩放不为 100% 就阻断启动并让用户改回。

多显示器提示：这里取的是**系统（主显示器）**缩放；若游戏放在缩放不同的
副屏上，同样会失效。
"""

from __future__ import annotations

import ctypes
import sys

# 识图标定基准。缩放不等于它就无法工作。
SUPPORTED_SCALE_PERCENT = 100
_BASE_DPI = 96


def _dpi_to_percent(dpi: int) -> int:
    return int(round(dpi / _BASE_DPI * 100))


def _scale_from_monitor() -> int | None:
    """GetDpiForMonitor(MDT_EFFECTIVE_DPI)。

    取 HMONITOR 的**实际** DPI，不受进程 DPI 感知级别影响，是最可靠的一路。
    """
    try:
        shcore = ctypes.windll.shcore
    except (AttributeError, OSError):
        return None
    try:
        MONITOR_DEFAULTTOPRIMARY = 1
        hmon = ctypes.windll.user32.MonitorFromWindow(None, MONITOR_DEFAULTTOPRIMARY)
        if not hmon:
            return None
        dx = ctypes.c_uint()
        dy = ctypes.c_uint()
        MDT_EFFECTIVE_DPI = 0
        if shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy)) != 0:
            return None
        if dx.value <= 0:
            return None
        return _dpi_to_percent(int(dx.value))
    except (AttributeError, OSError):
        return None


def _scale_from_device() -> int | None:
    """GetScaleFactorForDevice(0)（Win8.1+），直接返回百分比。"""
    try:
        shcore = ctypes.windll.shcore
    except (AttributeError, OSError):
        return None
    try:
        pct = int(shcore.GetScaleFactorForDevice(0))
    except (AttributeError, OSError):
        return None
    return pct if pct > 0 else None


def _scale_from_system_dpi() -> int | None:
    """GetDpiForSystem（Win10 1607+）。

    对 DPI-unaware 进程可能只报 96，所以只当作补充信号，不单独采信。
    """
    try:
        get_dpi = ctypes.windll.user32.GetDpiForSystem
    except (AttributeError, OSError):
        return None
    try:
        dpi = int(get_dpi())
    except (AttributeError, OSError):
        return None
    return _dpi_to_percent(dpi) if dpi > 0 else None


def _scale_from_resolution() -> int | None:
    """物理分辨率 ÷ 逻辑分辨率。

    DPI-unaware 时 GetSystemMetrics 返回缩放后的逻辑尺寸，比值即为缩放倍数；
    进程若已 DPI 感知则比值为 1，只会"少报"、不会误报，因此可安全并列。
    """
    class _DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", ctypes.c_wchar * 32),
            ("dmSpecVersion", ctypes.c_ushort),
            ("dmDriverVersion", ctypes.c_ushort),
            ("dmSize", ctypes.c_ushort),
            ("dmDriverExtra", ctypes.c_ushort),
            ("dmFields", ctypes.c_uint32),
            ("dmOrientation", ctypes.c_short),
            ("dmPaperSize", ctypes.c_short),
            ("dmPaperLength", ctypes.c_short),
            ("dmPaperWidth", ctypes.c_short),
            ("dmScale", ctypes.c_short),
            ("dmCopies", ctypes.c_short),
            ("dmDefaultSource", ctypes.c_short),
            ("dmPrintQuality", ctypes.c_short),
            ("dmColor", ctypes.c_short),
            ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short),
            ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short),
            ("dmFormName", ctypes.c_wchar * 32),
            ("dmLogPixels", ctypes.c_ushort),
            ("dmBitsPerPel", ctypes.c_uint32),
            ("dmPelsWidth", ctypes.c_uint32),
            ("dmPelsHeight", ctypes.c_uint32),
            ("dmDisplayFlags", ctypes.c_uint32),
            ("dmDisplayFrequency", ctypes.c_uint32),
        ]

    try:
        user32 = ctypes.windll.user32
        logical_w = int(user32.GetSystemMetrics(0))
        dev = _DEVMODEW()
        dev.dmSize = ctypes.sizeof(dev)
        ENUM_CURRENT_SETTINGS = 0xFFFFFFFF  # -1 as DWORD
        if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dev)):
            return None
        physical_w = int(dev.dmPelsWidth)
        if logical_w <= 0 or physical_w <= 0:
            return None
        return int(round(physical_w / logical_w * 100))
    except (AttributeError, OSError, ZeroDivisionError):
        return None


def _candidates() -> list[int]:
    values = (
        _scale_from_monitor(),
        _scale_from_device(),
        _scale_from_system_dpi(),
        _scale_from_resolution(),
    )
    return [v for v in values if v is not None and v > 0]


def system_scale_percent() -> int:
    """当前系统缩放百分比。

    取各来源的**最大值**：任一路径显示被放大就按被放大处理，避免因某一路径
    不可用而漏报（漏报会让用户在毫不知情的情况下识别失败）。
    全部探测失败时返回 100，即不误拦。
    """
    if sys.platform != "win32":
        return SUPPORTED_SCALE_PERCENT
    found = _candidates()
    if not found:
        return SUPPORTED_SCALE_PERCENT
    return max(found)


def is_scale_supported() -> bool:
    """缩放是否为识图可用的 100%。"""
    return system_scale_percent() == SUPPORTED_SCALE_PERCENT
