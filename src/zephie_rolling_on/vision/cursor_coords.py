from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientCursor:
    x: int
    y: int
    screen_x: int
    screen_y: int
    frame_x: int
    frame_y: int


def client_to_adventure_frame_pos(
    client_x: int,
    client_y: int,
    *,
    anchor: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """游戏客户区坐标 → 相对大冒险界面框锚点的偏移（与 click_targets.yaml 一致）。"""
    from zephie_rolling_on.data.adventure_frame import load_anchor

    ax, ay = anchor if anchor is not None else load_anchor()
    return int(client_x) - ax, int(client_y) - ay


def get_cursor_client_pos(hwnd: int) -> ClientCursor | None:
    """
    若鼠标在 hwnd 客户区内，返回客户区坐标 (x,y) 及屏幕坐标。
    否则返回 None。
    """
    if sys.platform != "win32":
        return None

    import win32gui

    from zephie_rolling_on.vision.win32_window import _client_size_screen_pixels

    if not hwnd or not win32gui.IsWindow(hwnd):
        return None

    sx, sy = win32gui.GetCursorPos()
    try:
        cx, cy = win32gui.ScreenToClient(hwnd, (sx, sy))
    except win32gui.error:
        return None

    _x0, _y0, width, height = _client_size_screen_pixels(hwnd)
    if not (0 <= cx < width and 0 <= cy < height):
        return None

    frame_x, frame_y = client_to_adventure_frame_pos(int(cx), int(cy))
    return ClientCursor(
        x=int(cx),
        y=int(cy),
        screen_x=int(sx),
        screen_y=int(sy),
        frame_x=frame_x,
        frame_y=frame_y,
    )
