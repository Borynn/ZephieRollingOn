from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

# 进程 DPI 感知，避免客户区尺寸与屏幕像素不一致
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


@dataclass
class WindowInfo:
    """capture_hwnd：实际用于截屏/坐标；root_hwnd：外层顶层窗口。"""

    capture_hwnd: int
    root_hwnd: int
    title: str
    client_width: int
    client_height: int

    @property
    def hwnd(self) -> int:
        """Same as capture_hwnd."""
        return self.capture_hwnd


# 本工具自己的窗口标题（绑定到这些会得到 220×72 等小区域）
_EXCLUDED_TITLE_KEYWORDS = (
    "绑定游戏窗口",
    "骰子大冒险辅助",
)

# 小于此客户区视为误绑工具窗/控件，非游戏主画面
MIN_CAPTURE_WIDTH = 640
MIN_CAPTURE_HEIGHT = 480

_REGISTERED_TOOL_HWNDS: set[int] = set()


def register_tool_hwnd(hwnd: int) -> None:
    if hwnd:
        _REGISTERED_TOOL_HWNDS.add(int(hwnd))


def enumerate_our_tool_hwnds() -> list[int]:
    """本工具相关窗口句柄（截屏回退 mss 前临时隐藏）。"""
    win32gui = _require_win32()
    found: set[int] = set(_REGISTERED_TOOL_HWNDS)

    def callback(hwnd, _extra) -> bool:
        if not win32gui.IsWindow(hwnd):
            return True
        if is_our_tool_window(hwnd):
            found.add(int(hwnd))
            root = int(win32gui.GetAncestor(hwnd, 2))
            if root:
                found.add(root)
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except win32gui.error:
        pass
    return [h for h in found if win32gui.IsWindow(h)]


def is_our_tool_window(hwnd: int) -> bool:
    win32gui = _require_win32()
    for h in (hwnd, int(win32gui.GetAncestor(hwnd, 2))):
        title = win32gui.GetWindowText(h) or ""
        if any(k in title for k in _EXCLUDED_TITLE_KEYWORDS):
            return True
    return False


def is_valid_game_capture_size(width: int, height: int) -> bool:
    return width >= MIN_CAPTURE_WIDTH and height >= MIN_CAPTURE_HEIGHT


def _require_win32() -> Any:
    if sys.platform != "win32":
        msg = "窗口句柄功能仅支持 Windows"
        raise OSError(msg)
    import win32gui  # noqa: PLC0415

    return win32gui


def _client_size_screen_pixels(hwnd: int) -> tuple[int, int, int, int]:
    """
    返回 (screen_left, screen_top, width, height)，均为屏幕物理像素。
    用 ClientToScreen 两点差计算宽高，兼容 DPI 缩放。
    """
    win32gui = _require_win32()
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    cw = right - left
    ch = bottom - top
    if cw <= 0 or ch <= 0:
        return 0, 0, 0, 0
    x0, y0 = win32gui.ClientToScreen(hwnd, (0, 0))
    x1, y1 = win32gui.ClientToScreen(hwnd, (cw, ch))
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _client_area_pixels(hwnd: int) -> int:
    _x, _y, w, h = _client_size_screen_pixels(hwnd)
    return w * h


def _enum_child_hwnds(parent: int) -> list[int]:
    win32gui = _require_win32()
    children: list[int] = []

    def callback(hwnd, _extra) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            children.append(int(hwnd))
        return True

    try:
        win32gui.EnumChildWindows(parent, callback, None)
    except win32gui.error:
        pass
    return children


def pick_window_at_point(
    x: int,
    y: int,
    *,
    exclude_hwnds: set[int] | None = None,
) -> WindowInfo | None:
    """
    根据屏幕坐标选择最佳截屏 hwnd：
    光标下窗口、其顶层窗口、以及顶层下所有可见子窗口中客户区最大者。
    排除本工具窗口及过小区域。
    """
    win32gui = _require_win32()
    skip = exclude_hwnds or set()

    hit = win32gui.WindowFromPoint((x, y))
    if not hit:
        return None
    hit = int(hit)
    root = int(win32gui.GetAncestor(hit, 2))

    candidates: set[int] = {hit, root}
    candidates.update(_enum_child_hwnds(root))

    def ok(hwnd: int) -> bool:
        if hwnd in skip or is_our_tool_window(hwnd):
            return False
        _x, _y, w, h = _client_size_screen_pixels(hwnd)
        return is_valid_game_capture_size(w, h)

    valid = [h for h in candidates if ok(h)]
    if not valid:
        return None

    best = max(valid, key=_client_area_pixels)
    _sx, _sy, w, h = _client_size_screen_pixels(best)
    root = int(win32gui.GetAncestor(best, 2))
    title = win32gui.GetWindowText(root) or win32gui.GetWindowText(best) or "(无标题)"
    return WindowInfo(
        capture_hwnd=best,
        root_hwnd=root,
        title=title,
        client_width=w,
        client_height=h,
    )


def hwnd_from_screen_point(x: int, y: int) -> int:
    """屏幕坐标 → 最佳截屏 hwnd（非仅顶层）。"""
    info = pick_window_at_point(x, y)
    return info.capture_hwnd if info else 0


def get_window_info(hwnd: int) -> WindowInfo | None:
    """根据已有 hwnd 刷新尺寸；若过小则尝试改用顶层下最大子窗口。"""
    win32gui = _require_win32()
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None

    hwnd = int(hwnd)
    root = int(win32gui.GetAncestor(hwnd, 2))
    candidates: set[int] = {hwnd, root}
    candidates.update(_enum_child_hwnds(root))
    best = max(candidates, key=_client_area_pixels)
    if _client_area_pixels(best) <= 0:
        return None

    _sx, _sy, w, h = _client_size_screen_pixels(best)
    title = win32gui.GetWindowText(root) or win32gui.GetWindowText(best) or "(无标题)"
    return WindowInfo(
        capture_hwnd=best,
        root_hwnd=root,
        title=title,
        client_width=w,
        client_height=h,
    )


def find_hwnd_by_title_substring(substring: str) -> int | None:
    win32gui = _require_win32()
    target = 0

    def callback(hwnd, _extra) -> bool:
        nonlocal target
        if not win32gui.IsWindowVisible(hwnd):
            return True
        text = win32gui.GetWindowText(hwnd)
        if substring and substring in text:
            target = int(hwnd)
            return False
        return True

    win32gui.EnumWindows(callback, None)
    return target or None


def resolve_hwnd(hwnd: int | None, title: str, *, capture_hwnd: int | None = None) -> int | None:
    """解析用于截屏的 hwnd。"""
    if capture_hwnd:
        info = get_window_info(capture_hwnd)
        if info is not None:
            return info.capture_hwnd
    if hwnd:
        info = get_window_info(hwnd)
        if info is not None:
            return info.capture_hwnd
    if title:
        found = find_hwnd_by_title_substring(title)
        if found:
            info = get_window_info(found)
            if info is not None:
                return info.capture_hwnd
    return None


def is_window_capturable(hwnd: int) -> bool:
    win32gui = _require_win32()
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    if win32gui.IsIconic(hwnd):
        return False
    _x, _y, w, h = _client_size_screen_pixels(hwnd)
    return w > 0 and h > 0
