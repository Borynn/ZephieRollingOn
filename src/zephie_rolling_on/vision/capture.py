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


def _build_from_rtlgetversion() -> int | None:
    """``RtlGetVersion``：最权威的一路，不受应用清单虚拟化影响。

    进程若未在清单里声明支持的 Windows 版本，系统会向后兼容地谎报
    ``6.2.9200``（Win8）。``RtlGetVersion`` 绕过该机制，直接给出真实版本。
    顺带也不依赖注册表与字符串解析。
    """
    try:
        import ctypes

        class _OsVersionInfoW(ctypes.Structure):
            _fields_ = [
                ("dwOSVersionInfoSize", ctypes.c_ulong),
                ("dwMajorVersion", ctypes.c_ulong),
                ("dwMinorVersion", ctypes.c_ulong),
                ("dwBuildNumber", ctypes.c_ulong),
                ("dwPlatformId", ctypes.c_ulong),
                ("szCSDVersion", ctypes.c_wchar * 128),
            ]

        info = _OsVersionInfoW()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        if ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info)) != 0:
            return None
        return int(info.dwBuildNumber) or None
    except Exception:
        return None


def _build_from_getwindowsversion() -> int | None:
    """``sys.getwindowsversion().build``（CPython 内部同样走 RtlGetVersion）。

    注意用 ``.build`` 而非 ``.platform_version``：后者受兼容性影响，实测在
    带清单的打包版上会返回 22621，而 ``.build`` 返回真实的 22631。
    """
    try:
        return int(sys.getwindowsversion().build) or None
    except Exception:
        return None


def _build_from_registry() -> int | None:
    """注册表 ``CurrentBuild``。

    该值是**干净的内部版本号**（如 ``22631``），更新修订号 UBR（如 7582）是另一个
    独立的键，不会被拼进来，所以无需再做截断。
    """
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "CurrentBuild")
        finally:
            winreg.CloseKey(key)
        return int(str(value)) or None
    except Exception:
        return None


def _build_from_platform() -> int | None:
    """``platform.version()`` 的**第三段**。

    取第三段而不是最后一段：Python 3.12+ 的 ``platform._win32_ver`` 优先返回 WMI
    的 ``Version`` 且不做三段截断，会给出四段如 ``10.0.19045.3803``；最后一段是
    更新修订号 UBR 而非内部版本号，取它会把 19045 读成 3803，从而误判为
    「不支持 WGC」并静默退回桌面裁切。
    """
    try:
        parts = platform.version().split(".")
        if len(parts) < 3:
            return None
        return int(parts[2]) or None
    except Exception:
        return None


# 按可靠性排序：先取不受清单/字符串解析影响的来源，最后才解析版本字符串。
_BUILD_READERS: tuple[tuple[str, Callable[[], int | None]], ...] = (
    ("RtlGetVersion", _build_from_rtlgetversion),
    ("getwindowsversion", _build_from_getwindowsversion),
    ("registry", _build_from_registry),
    ("platform", _build_from_platform),
)

_build_cache: int | None = None
_build_source_cache: str = ""


def windows_build_source() -> str:
    """上一处成功读取内部版本号的来源名；供自检输出，便于排查。"""
    windows_build()
    return _build_source_cache


def windows_build() -> int:
    """当前 Windows 内部版本号；全部来源都失败时返回 0。

    逐层尝试见 ``_BUILD_READERS``。结果只算一次并缓存——该值在进程生命周期内
    不变，而 ``wgc_supported`` 每次截图都会问到它。
    """
    global _build_cache, _build_source_cache
    if _build_cache is not None:
        return _build_cache
    for source, reader in _BUILD_READERS:
        build = reader()
        if build:
            _build_cache = build
            _build_source_cache = source
            return build
    _build_cache = 0
    _build_source_cache = ""
    return 0


_wgc_state: Optional[bool] = None
# WGC 不可用的具体原因；空串表示可用或尚未判定。
_wgc_unavailable_reason = ""


def wgc_unsupported_reason() -> str:
    """WGC 不可用的原因；可用时返回空串。

    这段原因以前被 ``except Exception`` 静默吞掉，导致用户报「走了桌面裁切」
    时完全无法判断是版本、缺扩展，还是扩展导入失败，只能靠猜。
    """
    wgc_supported()
    return _wgc_unavailable_reason


def wgc_supported() -> bool:
    """当前系统能否使用 WGC。

    低于 1803 直接返回 False，**不尝试导入** windows-capture——该扩展链接
    WinRT，在旧系统上导入会抛错，先判版本比事后捕获异常更可靠。
    """
    global _wgc_state, _wgc_unavailable_reason
    if _wgc_state is None:
        build = windows_build()
        if sys.platform != "win32":
            _wgc_state = False
            _wgc_unavailable_reason = f"非 Windows 平台（platform={sys.platform}）"
        elif build < WGC_MIN_BUILD:
            _wgc_state = False
            _wgc_unavailable_reason = (
                f"系统内部版本 {build} 低于 {WGC_MIN_BUILD}（Windows 10 1803）"
            )
        else:
            try:
                import windows_capture  # noqa: F401

                _wgc_state = True
                _wgc_unavailable_reason = ""
            except Exception as exc:  # noqa: BLE001
                _wgc_state = False
                _wgc_unavailable_reason = (
                    f"版本 {build} 满足要求，但导入 windows_capture 失败："
                    f"{type(exc).__name__}: {exc}"
                )
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


def _is_window_cloaked(hwnd: int) -> bool:
    """窗口是否为「隐形窗口」（DWM cloaked）。

    Windows 会长期保留一批 ``IsWindowVisible`` 为真、却**什么都不渲染**的顶层
    窗口，例如「Windows 输入体验」、已挂起的 UWP 应用（``ApplicationFrameWindow``）。
    它们常常占满整屏并停在 Z 序高处，只看 ``IsWindowVisible`` 会把它们当成遮挡者，
    从而把正常画面误判为「被遮挡」并停止脚本。

    `DwmGetWindowAttribute(DWMWA_CLOAKED)` 才是它们的真实可见性：
    0 = 正常显示，1 = DWM  cloak，2 = 父窗口 cloak，3 = 两者皆然。
    查询失败时返回 ``False``（宁可少过滤，也不漏判真实遮挡）。
    """
    try:
        import ctypes

        DWMWA_CLOAKED = 14
        value = ctypes.c_int(0)
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            int(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
        if hr != 0:
            return False
        return bool(value.value)
    except Exception:
        return False


def _is_client_occluded(hwnd: int) -> bool:
    """客户区是否被 Z 序更高的窗口覆盖。

    做法：从游戏顶层窗口沿 ``GW_HWNDPREV`` 往上遍历 Z 序，对每个可见、非最小化、
    **非隐形（cloaked）**的窗口取其**可见外框**（DWM 扩展边框，不含阴影）与客户区
    矩形求交，一旦相交即判定被遮挡。

    用 DWM 边框而非 ``GetWindowRect``：后者在 Win10+ 含不可见阴影，相邻窗口的
    阴影会造成误判。

    必须排除 cloaked 窗口：它们 ``IsWindowVisible`` 为真但实际不渲染，且常占满
    整屏，是「明明没被遮挡却报遮挡」最常见的成因（见 :func:`_is_window_cloaked`）。

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
                if (
                    win32gui.IsWindowVisible(win)
                    and not win32gui.IsIconic(win)
                    and not _is_window_cloaked(win)
                ):
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


def occluding_windows(hwnd: int, *, limit: int = 8) -> list[tuple[int, str, str]]:
    """列出当前会把客户区判为遮挡的窗口，供日志/自检定位问题。

    返回 ``[(hwnd, 类名, 标题), …]``。空列表表示未被遮挡。
    """
    out: list[tuple[int, str, str]] = []
    if sys.platform != "win32":
        return out
    try:
        import win32gui

        from zephie_rolling_on.vision.win32_window import _client_size_screen_pixels

        x, y, cw, ch = _client_size_screen_pixels(hwnd)
        if cw <= 0 or ch <= 0:
            return out
        left, top, right, bottom = x, y, x + cw, y + ch

        root = int(win32gui.GetAncestor(int(hwnd), 2)) or int(hwnd)
        GW_HWNDPREV = 3
        win = int(win32gui.GetWindow(root, GW_HWNDPREV))
        guard = 0
        while win and guard < 512 and len(out) < limit:
            guard += 1
            try:
                if (
                    win32gui.IsWindowVisible(win)
                    and not win32gui.IsIconic(win)
                    and not _is_window_cloaked(win)
                ):
                    wl, wt, wr, wb = _window_rect(win)
                    if wr > wl and wb > wt and not (
                        wr <= left or wl >= right or wb <= top or wt >= bottom
                    ):
                        try:
                            cls = win32gui.GetClassName(win)
                        except Exception:
                            cls = "?"
                        try:
                            title = win32gui.GetWindowText(win)
                        except Exception:
                            title = ""
                        out.append((int(win), cls, title))
            except Exception:
                pass
            win = int(win32gui.GetWindow(win, GW_HWNDPREV))
    except Exception:
        return out
    return out


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


def _wgc_failure_detail() -> str:
    """WGC 取帧失败的细节；取不到时为空串。"""
    try:
        from zephie_rolling_on.vision.capture_wgc import wgc_failure_reason

        return wgc_failure_reason()
    except Exception:
        return ""


def _occlusion_note_with_detail(hwnd: int, *, wgc_ok: bool = False) -> str:
    """遮挡提示 + 判定依据 + WGC 失败原因（若有）。

    弹窗文案仍用 ``OCCLUSION_HINT`` 常量，这里只把「判定依据」附进日志/停止原因。
    必须带上 WGC 失败原因：遮挡判定**只在 WGC 取帧失败后才会执行**，所以「被遮挡」
    往往是次要症状，真正卡住的是 WGC——只报遮挡会把排查引向错误方向。
    """
    parts: list[str] = []
    if wgc_ok:
        reason = _wgc_failure_detail()
        parts.append(f"WGC 取帧失败：{reason}" if reason else "WGC 取帧失败（原因未记录）")
    try:
        who = occluding_windows(hwnd, limit=3)
    except Exception:
        who = []
    if who:
        names = []
        for _h, cls, title in who:
            label = title.strip() or cls
            names.append(f"{cls}「{label[:24]}」")
        parts.append(f"判定遮挡依据：{'、'.join(names)}")
    if not parts:
        return OCCLUSION_HINT
    return f"{OCCLUSION_HINT}（{'；'.join(parts)}）"


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

    wgc_ok = wgc_supported()
    if wgc_ok:
        from zephie_rolling_on.vision.capture_wgc import capture_window_wgc

        wgc_img = capture_window_wgc(hwnd)
        if wgc_img is not None:
            _clear_failure()
            return wgc_img

    if _is_client_occluded(hwnd):
        _report_failure(
            _occlusion_note_with_detail(hwnd, wgc_ok=wgc_ok),
            block_reason="occluded",
        )
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

    if not wgc_ok:
        reason = wgc_unsupported_reason()
        _report_failure(
            "桌面裁切未取到可用画面。请确认游戏窗口可见、未最小化，"
            f"且没有处于独占全屏；必要时改用窗口化模式。"
            + (f"（WGC 不可用：{reason}）" if reason else "")
        )
    else:
        detail = _wgc_failure_detail()
        _report_failure(
            "窗口捕获失败（WGC 取帧失败，桌面裁切也未取到可用画面）。"
            + (f"WGC 原因：{detail}。" if detail else "")
            + "彩虹岛建议：窗口化/无边框窗口、关闭独占全屏；"
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
