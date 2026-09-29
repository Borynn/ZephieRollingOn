"""Click helpers: SendInput, Interception, or Post/SendMessage."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

import ctypes
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any, Literal

import yaml

from zephie_rolling_on.executor.mouse_shield import click_shield_guard

PROJECT_ROOT = project_root()
CLICK_TARGETS_PATH = PROJECT_ROOT / "config" / "click_targets.yaml"

ClickDelivery = Literal["human", "interception", "postmessage", "sendmessage", "screen"]

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
MK_LBUTTON = 0x0001
CWP_SKIPINVISIBLE = 0x0001

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# 按下与抬起之间的固定间隔（秒）
_CLICK_HOLD_SEC = 0.015  # PostMessage down→up：宜 10~20ms；>50ms 易被物理 MOVE 拧成拖拽
_KEY_HOLD_SEC = 0.05

VK_F12 = 0x7B


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _require_win32() -> Any:
    if sys.platform != "win32":
        msg = "窗口点击仅支持 Windows"
        raise OSError(msg)
    import win32gui  # noqa: PLC0415

    return win32gui


def load_click_delivery() -> ClickDelivery:
    if not CLICK_TARGETS_PATH.is_file():
        return "postmessage"
    with CLICK_TARGETS_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = str(data.get("click_delivery", "postmessage")).strip().lower()
    if raw in ("human", "interception", "postmessage", "sendmessage", "screen"):
        return raw  # type: ignore[return-value]
    return "postmessage"


def _make_lparam(client_x: int, client_y: int) -> int:
    return ((int(client_y) & 0xFFFF) << 16) | (int(client_x) & 0xFFFF)


def client_to_screen(capture_hwnd: int, client_x: int, client_y: int) -> tuple[int, int]:
    win32gui = _require_win32()
    return win32gui.ClientToScreen(int(capture_hwnd), (int(client_x), int(client_y)))


def _screen_to_absolute(sx: int, sy: int) -> tuple[int, int]:
    user32 = ctypes.windll.user32
    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    ax = int((int(sx) - left) * 65535 / max(1, width - 1))
    ay = int((int(sy) - top) * 65535 / max(1, height - 1))
    return ax, ay


def _send_mouse(flags: int, *, ax: int = 0, ay: int = 0) -> None:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(ax, ay, 0, flags, 0, None)
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
    if sent != 1:
        msg = "SendInput 失败"
        raise OSError(msg)


def press_virtual_key(vk: int, *, hold_sec: float = _KEY_HOLD_SEC) -> None:
    """SendInput 模拟按键（应用层；部分游戏会过滤）。"""
    _require_win32()
    down = _INPUT()
    down.type = INPUT_KEYBOARD
    down.ki = _KEYBDINPUT(int(vk) & 0xFFFF, 0, 0, 0, None)
    up = _INPUT()
    up.type = INPUT_KEYBOARD
    up.ki = _KEYBDINPUT(int(vk) & 0xFFFF, 0, KEYEVENTF_KEYUP, 0, None)
    user32 = ctypes.windll.user32
    if user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT)) != 1:
        raise OSError("SendInput 按键按下失败")
    time.sleep(hold_sec)
    if user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT)) != 1:
        raise OSError("SendInput 按键抬起失败")


def press_f12() -> None:
    press_virtual_key(VK_F12)


def _key_lparam(*, scan: int, key_up: bool) -> int:
    """构造较完整的 WM_KEY* lParam（含扫描码；许多游戏会校验）。"""
    repeat = 1
    lp = repeat | ((int(scan) & 0xFF) << 16)
    if key_up:
        # bit30 previous down, bit31 transition up
        lp |= 1 << 30
        lp |= 1 << 31
    return lp


def _gui_focus_hwnd(hwnd: int) -> int | None:
    """取目标窗口所在线程当前焦点子窗（后台时也可能仍有内部 focus）。"""
    win32gui = _require_win32()
    user32 = ctypes.windll.user32

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("flags", ctypes.c_ulong),
            ("hwndActive", ctypes.c_void_p),
            ("hwndFocus", ctypes.c_void_p),
            ("hwndCapture", ctypes.c_void_p),
            ("hwndMenuOwner", ctypes.c_void_p),
            ("hwndMoveSize", ctypes.c_void_p),
            ("hwndCaret", ctypes.c_void_p),
            ("rcCaret", ctypes.c_long * 4),
        ]

    # pywin32 的 GetWindowThreadProcessId 在 win32process，不在 win32gui
    pid = wintypes.DWORD(0)
    tid = int(user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid)) or 0)
    if not tid:
        return None
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(int(tid), ctypes.byref(info)):
        return None
    focus = int(info.hwndFocus or 0)
    if focus and win32gui.IsWindow(focus):
        return focus
    active = int(info.hwndActive or 0)
    if active and win32gui.IsWindow(active):
        return active
    return None


def _key_targets(capture_hwnd: int) -> list[tuple[str, int]]:
    """去重后的候选 hwnd：焦点子窗 → capture → 根窗。"""
    win32gui = _require_win32()
    import win32con  # noqa: PLC0415

    out: list[tuple[str, int]] = []
    seen: set[int] = set()

    def add(label: str, h: int | None) -> None:
        if not h:
            return
        hi = int(h)
        if hi in seen or not win32gui.IsWindow(hi):
            return
        seen.add(hi)
        out.append((label, hi))

    add("focus", _gui_focus_hwnd(capture_hwnd))
    add("capture", int(capture_hwnd))
    try:
        root = int(win32gui.GetAncestor(int(capture_hwnd), win32con.GA_ROOT) or 0)
    except Exception:
        root = 0
    add("root", root)
    return out


def press_virtual_key_postmessage(
    hwnd: int,
    vk: int,
    *,
    hold_sec: float = _KEY_HOLD_SEC,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """向游戏相关 hwnd 投递按键（Post/Send + 扫描码）。

    说明：很多 DirectX/自研输入只读 Raw Input / GetAsyncKeyState，
    此时即使用正确 WM_KEY* 也不会生效；鼠标 PostMessage 有效不代表键盘也有效。
    """
    log = on_log or (lambda _m: None)
    win32gui = _require_win32()
    import win32api  # noqa: PLC0415

    capture = int(hwnd)
    if not capture or not win32gui.IsWindow(capture):
        log("[按键] 窗口无效")
        return False

    user32 = ctypes.windll.user32
    MAPVK_VK_TO_VSC = 0
    scan = int(user32.MapVirtualKeyW(int(vk) & 0xFFFF, MAPVK_VK_TO_VSC)) & 0xFF
    if scan == 0:
        # F12 常见扫描码回退
        scan = 0x58 if int(vk) == VK_F12 else 0
    lp_down = _key_lparam(scan=scan, key_up=False)
    lp_up = _key_lparam(scan=scan, key_up=True)
    vk_w = int(vk) & 0xFFFF

    targets = _key_targets(capture)
    if not targets:
        log("[按键] 无可用目标 hwnd")
        return False

    log(
        f"[按键] 尝试 vk=0x{vk_w:02X} scan=0x{scan:02X} "
        f"候选={[(n, h) for n, h in targets]}"
    )

    any_ok = False
    for label, target in targets:
        title = win32gui.GetWindowText(target) or "(无标题)"
        class_name = win32gui.GetClassName(target) or "?"
        for delivery_name, sender in (
            ("postmessage", win32api.PostMessage),
            ("sendmessage", win32api.SendMessage),
        ):
            try:
                sender(target, WM_KEYDOWN, vk_w, lp_down)
                time.sleep(hold_sec)
                sender(target, WM_KEYUP, vk_w, lp_up)
                any_ok = True
                log(
                    f"[按键] 已投递 {delivery_name} → {label} hwnd={target} "
                    f"class={class_name!r} title={title!r}"
                )
            except Exception as exc:
                log(f"[按键] {delivery_name}/{label} 失败: {exc}")

    if any_ok:
        log(
            "[按键] 消息已发出。若游戏仍无反应：多半不读 WM_KEY*，"
            "后台 F12 需改走 Interception/SendInput（通常还要前台焦点）。"
        )
    return any_ok


def press_f12_postmessage(
    hwnd: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    return press_virtual_key_postmessage(hwnd, VK_F12, on_log=on_log)


def press_f12_postmessage_probe(
    hwnd: int,
    *,
    hold_sec: float = 0.1,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Gemini 探测：仅 PostMessage（不用 SendMessage）+ 扫描码 lParam，默认按住 0.1s 再 KEYUP。

    用于验证游戏是否接受后台 WM_KEY*；若仍无反应，多半只读 Raw Input / 异步键状态。
    """
    log = on_log or (lambda _m: None)
    win32gui = _require_win32()
    import win32api  # noqa: PLC0415

    capture = int(hwnd)
    if not capture or not win32gui.IsWindow(capture):
        log("[F12探测] 窗口无效，请先绑定游戏窗口")
        return False

    user32 = ctypes.windll.user32
    MAPVK_VK_TO_VSC = 0
    scan = int(user32.MapVirtualKeyW(VK_F12, MAPVK_VK_TO_VSC)) & 0xFF
    if scan == 0:
        scan = 0x58
    # Gemini：down = 1|(scan<<16)；up 再置 bit30/bit31
    lp_down = 1 | (scan << 16)
    lp_up = 1 | (scan << 16) | (1 << 30) | (1 << 31)

    targets = _key_targets(capture)
    if not targets:
        log("[F12探测] 无可用目标 hwnd")
        return False

    log(
        f"[F12探测] PostMessage only | vk=0x{VK_F12:02X} scan=0x{scan:02X} "
        f"hold={hold_sec * 1000:.0f}ms | "
        f"lp_down=0x{lp_down:08X} lp_up=0x{lp_up:08X}"
    )
    any_ok = False
    for label, target in targets:
        title = win32gui.GetWindowText(target) or "(无标题)"
        class_name = win32gui.GetClassName(target) or "?"
        try:
            win32api.PostMessage(target, WM_KEYDOWN, VK_F12, lp_down)
            time.sleep(hold_sec)
            win32api.PostMessage(target, WM_KEYUP, VK_F12, lp_up)
            any_ok = True
            log(
                f"[F12探测] 已 PostMessage → {label} hwnd={target} "
                f"class={class_name!r} title={title!r}"
            )
        except Exception as exc:
            log(f"[F12探测] {label} 失败: {exc}")

    if any_ok:
        log(
            "[F12探测] 已发出。若游戏无开关界面反应，则多半不读 WM_KEY*，"
            "需 Interception/SendInput 或继续用点击 toggle。"
        )
    return any_ok


def _mouse_button_at(ax: int, ay: int, *, move_first: bool) -> None:
    if move_first:
        _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax=ax, ay=ay)
    _send_mouse(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE, ax=ax, ay=ay)
    time.sleep(_CLICK_HOLD_SEC)
    _send_mouse(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE, ax=ax, ay=ay)


def _mouse_click_screen(sx: int, sy: int) -> None:
    """光标移到屏幕坐标并左键点击一次（SendInput）。"""
    ax, ay = _screen_to_absolute(sx, sy)
    _mouse_button_at(ax, ay, move_first=True)


def resolve_message_target(
    capture_hwnd: int,
    client_x: int,
    client_y: int,
) -> tuple[int, int, int]:
    win32gui = _require_win32()
    hwnd = int(capture_hwnd)
    if not hwnd or not win32gui.IsWindow(hwnd):
        return 0, int(client_x), int(client_y)

    pt = (int(client_x), int(client_y))
    child = 0
    try:
        child = int(
            win32gui.ChildWindowFromPointEx(hwnd, pt, CWP_SKIPINVISIBLE) or 0,
        )
    except Exception:
        child = 0

    if child and child != hwnd and win32gui.IsWindow(child):
        sx, sy = win32gui.ClientToScreen(hwnd, pt)
        cx, cy = win32gui.ScreenToClient(child, (sx, sy))
        return child, int(cx), int(cy)
    return hwnd, int(client_x), int(client_y)


def _dispatch_button(
    target_hwnd: int,
    client_x: int,
    client_y: int,
    *,
    delivery: ClickDelivery,
) -> None:
    import win32api  # noqa: PLC0415
    import win32gui  # noqa: PLC0415

    if not target_hwnd or not win32gui.IsWindow(target_hwnd):
        msg = "目标窗口无效"
        raise OSError(msg)

    lparam = _make_lparam(client_x, client_y)
    if delivery == "postmessage":
        sender = win32api.PostMessage
    elif delivery == "sendmessage":
        sender = win32api.SendMessage
    else:
        msg = f"未知消息投递: {delivery}"
        raise ValueError(msg)

    sender(target_hwnd, WM_MOUSEMOVE, 0, lparam)
    sender(target_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(_CLICK_HOLD_SEC)
    sender(target_hwnd, WM_LBUTTONUP, 0, lparam)


def move_client_cursor(
    capture_hwnd: int,
    client_x: int,
    client_y: int,
    *,
    on_log: Callable[[str], None] | None = None,
    use_guard: bool = True,
) -> bool:
    """仅 PostMessage WM_MOUSEMOVE：移动游戏内指针，不移动真实光标。"""
    if use_guard:
        with click_shield_guard():
            return _move_client_cursor_impl(
                capture_hwnd, client_x, client_y, on_log=on_log
            )
    return _move_client_cursor_impl(capture_hwnd, client_x, client_y, on_log=on_log)


def _move_client_cursor_impl(
    capture_hwnd: int,
    client_x: int,
    client_y: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    log = on_log or (lambda _m: None)
    if sys.platform != "win32":
        log("[移光标] 仅支持 Windows")
        return False
    win32gui = _require_win32()
    if not capture_hwnd or not win32gui.IsWindow(int(capture_hwnd)):
        log("[移光标] 游戏窗口无效")
        return False
    try:
        import win32api  # noqa: PLC0415

        target, tx, ty = resolve_message_target(
            int(capture_hwnd), int(client_x), int(client_y)
        )
        if not target:
            log("[移光标] 无法解析消息目标 hwnd")
            return False
        win32api.PostMessage(target, WM_MOUSEMOVE, 0, _make_lparam(tx, ty))
    except Exception as exc:
        log(f"[移光标] 失败: {exc}")
        return False
    log(f"[移光标] PostMessage MOVE → 客户区 ({tx}, {ty})（真实鼠标不动）")
    return True


def click_client_game(
    capture_hwnd: int,
    client_x: int,
    client_y: int,
    *,
    delivery: ClickDelivery | None = None,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """在已绑定游戏 capture hwnd 的客户区点击一次。"""
    with click_shield_guard():
        return _click_client_game_impl(
            capture_hwnd,
            client_x,
            client_y,
            delivery=delivery,
            on_log=on_log,
        )


def _click_client_game_impl(
    capture_hwnd: int,
    client_x: int,
    client_y: int,
    *,
    delivery: ClickDelivery | None = None,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    log = on_log or (lambda _m: None)
    if sys.platform != "win32":
        log("[点击] 仅支持 Windows")
        return False

    mode = delivery or load_click_delivery()
    win32gui = _require_win32()
    if not capture_hwnd or not win32gui.IsWindow(int(capture_hwnd)):
        log("[点击] 游戏窗口无效，请先绑定")
        return False

    sx, sy = client_to_screen(capture_hwnd, client_x, client_y)

    if mode == "human":
        try:
            _mouse_click_screen(sx, sy)
        except Exception as exc:
            log(f"[点击] human 失败: {exc}")
            return False
        log(
            f"[点击] human SendInput 客户区 ({client_x}, {client_y}) "
            f"→ 屏幕 ({sx}, {sy})",
        )
        return True

    if mode == "interception":
        from zephie_rolling_on.executor.interception_click import click_screen_interception

        ok, err = click_screen_interception(sx, sy, on_log=log)
        if not ok:
            log(f"[点击] interception 未执行: {err}")
            return False
        log(
            f"[点击] interception 客户区 ({client_x}, {client_y}) "
            f"→ 屏幕 ({sx}, {sy})",
        )
        return True

    if mode == "screen":
        import pyautogui  # noqa: PLC0415

        pyautogui.click(sx, sy)
        log(f"[点击] screen ({sx}, {sy})")
        return True

    target, tx, ty = resolve_message_target(capture_hwnd, client_x, client_y)
    if not target:
        log("[点击] 无法解析消息目标 hwnd")
        return False

    title = win32gui.GetWindowText(target) or "(无标题)"
    class_name = win32gui.GetClassName(target) or "?"
    try:
        _dispatch_button(target, tx, ty, delivery=mode)
    except Exception as exc:
        log(f"[点击] {mode} 失败 hwnd={target} ({title!r}): {exc}")
        return False

    log(
        f"[点击] {mode} → hwnd={target} class={class_name!r} "
        f"客户区 ({tx}, {ty})",
    )
    return True


def drag_client_game(
    capture_hwnd: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    delivery: ClickDelivery | None = None,
    steps: int = 24,
    step_delay_sec: float = 0.02,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """客户区拖拽：按下 → 插值移动 → 抬起（用于卡池列表滚动）。"""
    with click_shield_guard():
        return _drag_client_game_impl(
            capture_hwnd,
            x0,
            y0,
            x1,
            y1,
            delivery=delivery,
            steps=steps,
            step_delay_sec=step_delay_sec,
            on_log=on_log,
        )


def _drag_client_game_impl(
    capture_hwnd: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    delivery: ClickDelivery | None = None,
    steps: int = 24,
    step_delay_sec: float = 0.02,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    log = on_log or (lambda _m: None)
    if sys.platform != "win32":
        log("[拖拽] 仅支持 Windows")
        return False

    mode = delivery or load_click_delivery()
    win32gui = _require_win32()
    if not capture_hwnd or not win32gui.IsWindow(int(capture_hwnd)):
        log("[拖拽] 游戏窗口无效，请先绑定")
        return False

    n = max(2, int(steps))
    if mode in ("human", "screen"):
        try:
            sx0, sy0 = client_to_screen(capture_hwnd, x0, y0)
            sx1, sy1 = client_to_screen(capture_hwnd, x1, y1)
            if mode == "screen":
                import pyautogui  # noqa: PLC0415

                pyautogui.moveTo(sx0, sy0)
                pyautogui.dragTo(sx1, sy1, duration=max(0.15, n * step_delay_sec), button="left")
            else:
                ax0, ay0 = _screen_to_absolute(sx0, sy0)
                _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax=ax0, ay=ay0)
                _send_mouse(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE, ax=ax0, ay=ay0)
                for i in range(1, n + 1):
                    t = i / n
                    sx = int(round(sx0 + (sx1 - sx0) * t))
                    sy = int(round(sy0 + (sy1 - sy0) * t))
                    ax, ay = _screen_to_absolute(sx, sy)
                    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax=ax, ay=ay)
                    time.sleep(step_delay_sec)
                ax1, ay1 = _screen_to_absolute(sx1, sy1)
                _send_mouse(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE, ax=ax1, ay=ay1)
        except Exception as exc:
            log(f"[拖拽] {mode} 失败: {exc}")
            return False
        log(f"[拖拽] {mode} ({x0},{y0}) → ({x1},{y1})")
        return True

    if mode == "interception":
        # 无专用拖拽时回退 human SendInput（已在外层 guard 内，勿再包一层）
        return _drag_client_game_impl(
            capture_hwnd,
            x0,
            y0,
            x1,
            y1,
            delivery="human",
            steps=steps,
            step_delay_sec=step_delay_sec,
            on_log=on_log,
        )

    import win32api  # noqa: PLC0415

    target, tx0, ty0 = resolve_message_target(capture_hwnd, x0, y0)
    if not target:
        log("[拖拽] 无法解析消息目标 hwnd")
        return False
    _t2, tx1, ty1 = resolve_message_target(capture_hwnd, x1, y1)
    sender = win32api.PostMessage if mode == "postmessage" else win32api.SendMessage
    try:
        sender(target, WM_MOUSEMOVE, 0, _make_lparam(tx0, ty0))
        sender(target, WM_LBUTTONDOWN, MK_LBUTTON, _make_lparam(tx0, ty0))
        for i in range(1, n + 1):
            t = i / n
            cx = int(round(tx0 + (tx1 - tx0) * t))
            cy = int(round(ty0 + (ty1 - ty0) * t))
            sender(target, WM_MOUSEMOVE, MK_LBUTTON, _make_lparam(cx, cy))
            time.sleep(step_delay_sec)
        sender(target, WM_LBUTTONUP, 0, _make_lparam(tx1, ty1))
    except Exception as exc:
        log(f"[拖拽] {mode} 失败: {exc}")
        return False
    log(f"[拖拽] {mode} ({x0},{y0}) → ({x1},{y1}) steps={n}")
    return True
