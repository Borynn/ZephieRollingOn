"""Low-level mouse hook: optionally filter game-area input during script actions.

The hook is only engaged by ``GameMouseShield.start()``. Nothing in the current
application calls it, so ``click_shield_guard()`` currently only balances its
own counter and no input is filtered.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from contextlib import contextmanager
from ctypes import wintypes
from typing import Callable, Iterator


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
WM_QUIT = 0x0012

LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002

_BLOCK_MSGS = frozenset(
    {
        WM_MOUSEMOVE,
        WM_LBUTTONDOWN,
        WM_LBUTTONUP,
        WM_RBUTTONDOWN,
        WM_RBUTTONUP,
        WM_MBUTTONDOWN,
        WM_MBUTTONUP,
        WM_MOUSEWHEEL,
        WM_XBUTTONDOWN,
        WM_XBUTTONUP,
        WM_MOUSEHWHEEL,
    }
)

HC_ACTION = 0
GA_ROOT = 2


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


def _root_hwnd(hwnd: int) -> int:
    if not hwnd or not user32.IsWindow(int(hwnd)):
        return 0
    root = int(user32.GetAncestor(int(hwnd), GA_ROOT) or 0)
    return root or int(hwnd)


def _hwnd_is_under(target: int, hwnd: int) -> bool:
    if not target or not hwnd:
        return False
    cur = int(hwnd)
    target = int(target)
    seen: set[int] = set()
    while cur and cur not in seen:
        if cur == target:
            return True
        seen.add(cur)
        cur = int(user32.GetParent(cur) or 0)
    root = int(user32.GetAncestor(int(hwnd), GA_ROOT) or 0)
    return bool(root and root == target)


def _point_over_hwnd(target: int, x: int, y: int) -> bool:
    if not target or not user32.IsWindow(int(target)):
        return False
    hwnd = int(user32.WindowFromPoint(POINT(int(x), int(y))) or 0)
    return _hwnd_is_under(int(target), hwnd)


class GameMouseShield:
    """WH_MOUSE_LL 钩子；用 depth 标志位控制是否过滤游戏区物理输入。

    钩子仅在 ``start()`` 被调用时安装；当前应用不调用它，因此本类处于惰性状态。
    """

    def __init__(self) -> None:
        self._target_hwnd = 0
        self._hook_installed = False
        self._hook = None
        self._proc = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._tid = 0
        self._lock = threading.Lock()
        self._filter_depth = 0
        self._on_log: Callable[[str], None] | None = None

    @property
    def active(self) -> bool:
        return self._hook_installed and self._hook is not None

    @property
    def filtering(self) -> bool:
        return self._filter_depth > 0

    def set_logger(self, on_log: Callable[[str], None] | None) -> None:
        self._on_log = on_log

    def set_target_hwnd(self, hwnd: int | None) -> None:
        with self._lock:
            self._target_hwnd = _root_hwnd(int(hwnd or 0))

    def begin_filter(self) -> None:
        """脚本开始对游戏操作前调用（可嵌套）。"""
        with self._lock:
            self._filter_depth += 1

    def end_filter(self) -> None:
        """脚本操作结束后调用。"""
        with self._lock:
            self._filter_depth = max(0, self._filter_depth - 1)

    def start(self, hwnd: int | None = None) -> bool:
        """安装常驻钩子（标志位默认关闭，不挡用户）。"""
        if sys.platform != "win32":
            return False
        if hwnd is not None:
            self.set_target_hwnd(hwnd)
        with self._lock:
            if self._hook_installed:
                return True
            if not self._target_hwnd:
                return False
            self._hook_installed = True
            self._filter_depth = 0
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="game-mouse-shield",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=3.0):
            self.stop()
            return False
        ok = self._hook is not None
        log = self._on_log
        if log:
            if ok:
                log(
                    f"[鼠标屏蔽] 钩子已常驻（hwnd={self._target_hwnd}）；"
                    "仅脚本点击/拖拽瞬间过滤游戏区输入"
                )
            else:
                log("[鼠标屏蔽] 钩子安装失败")
        return ok

    def stop(self) -> None:
        with self._lock:
            was = self._hook_installed
            self._hook_installed = False
            self._filter_depth = 0
            tid = self._tid
            thread = self._thread
        if tid:
            try:
                user32.PostThreadMessageW(int(tid), WM_QUIT, 0, 0)
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            self._hook = None
            self._proc = None
            self._thread = None
            self._tid = 0
        if was:
            log = self._on_log
            if log:
                log("[鼠标屏蔽] 钩子已卸载")

    def _thread_main(self) -> None:
        self._tid = int(kernel32.GetCurrentThreadId())
        self._proc = LowLevelMouseProc(self._callback)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._proc,
            None,
            0,
        )
        self._ready.set()
        if not self._hook:
            self._hook_installed = False
            return
        msg = wintypes.MSG()
        while self._hook_installed:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            # 快路径：未在脚本操作窗口 → 原样放行
            if self._filter_depth <= 0 or not self._hook_installed:
                return int(user32.CallNextHookEx(None, n_code, w_param, l_param))
            if n_code == HC_ACTION and int(w_param) in _BLOCK_MSGS:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                flags = int(info.flags)
                if flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED):
                    return int(user32.CallNextHookEx(None, n_code, w_param, l_param))
                with self._lock:
                    target = self._target_hwnd
                if target and _point_over_hwnd(target, info.pt.x, info.pt.y):
                    return 1
        except Exception:
            pass
        return int(user32.CallNextHookEx(None, n_code, w_param, l_param))


_shield = GameMouseShield()


def get_mouse_shield() -> GameMouseShield:
    return _shield


@contextmanager
def click_shield_guard() -> Iterator[None]:
    """RAII：成对增减过滤计数器，结束（含异常）自动恢复。

    实际过滤只在该计数器 > 0 **且** ``start()`` 已安装钩子时发生；当前应用不调用
    ``start()``，因此这里不产生过滤效果。
    """
    shield = get_mouse_shield()
    shield.begin_filter()
    try:
        yield
    finally:
        shield.end_filter()
