from __future__ import annotations

import ctypes
import queue
import sys
import threading
import traceback
from collections.abc import Callable
from ctypes import wintypes

from zephie_rolling_on.ui.hotkey_config import (
    ACTION_BIND_WINDOW,
    DEFAULT_HOTKEYS,
    HotkeyBinding,
    load_hotkey_bindings,
)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
_HOTKEY_ID_BASE = 1

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32


class GlobalHotkeyManager:
    """全局快捷键：专用线程 RegisterHotKey + GetMessage，游戏有焦点时由系统投递 WM_HOTKEY。

    设置快捷键时可 ``set_suspended(True)`` 暂停响应，避免捕获按键时误触发。
    """

    def __init__(self, handlers: dict[str, Callable[[], None]] | None = None) -> None:
        if sys.platform != "win32":
            raise OSError("全局快捷键仅支持 Windows")
        self._handlers = dict(handlers or {})
        self._bindings: dict[str, HotkeyBinding] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._registered: dict[int, str] = {}
        self._errors: list[str] = []
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._suspended = False
        self._suspend_lock = threading.Lock()

    def set_handlers(self, handlers: dict[str, Callable[[], None]]) -> None:
        self._handlers = dict(handlers)

    def bindings(self) -> dict[str, HotkeyBinding]:
        return dict(self._bindings)

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and bool(self._registered)

    def set_suspended(self, suspended: bool) -> None:
        with self._suspend_lock:
            self._suspended = suspended

    def is_suspended(self) -> bool:
        with self._suspend_lock:
            return self._suspended

    def bind_window_label(self) -> str:
        b = self._bindings.get(ACTION_BIND_WINDOW)
        if b and b.enabled and b.key:
            return b.label
        return DEFAULT_HOTKEYS[ACTION_BIND_WINDOW]

    def _hotkey_thread_main(self) -> None:
        try:
            self._thread_id = int(_kernel32.GetCurrentThreadId())
            hotkey_id = _HOTKEY_ID_BASE
            for action, binding in self._bindings.items():
                spec = binding.mod_vk()
                if spec is None:
                    continue
                mod, vk = spec
                ok = bool(_user32.RegisterHotKey(None, hotkey_id, mod, vk))
                if ok:
                    self._registered[hotkey_id] = action
                    hotkey_id += 1
                else:
                    err = _kernel32.GetLastError()
                    self._errors.append(
                        f"{binding.label}（{action}）注册失败(err={err})，可能已被占用"
                    )

            self._ready.set()
            if not self._registered:
                return

            msg = wintypes.MSG()
            while not self._stop.is_set():
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                if msg.message == WM_HOTKEY:
                    action = self._registered.get(int(msg.wParam))
                    if action and not self.is_suspended():
                        self._queue.put(action)
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._errors.append(f"快捷键线程异常: {exc}")
            traceback.print_exc()
        finally:
            for hid in list(self._registered):
                _user32.UnregisterHotKey(None, hid)
            self._registered.clear()
            self._thread_id = None
            if not self._ready.is_set():
                self._ready.set()

    def start(self, bindings: dict[str, HotkeyBinding] | None = None) -> list[str]:
        self.stop()
        self._bindings = bindings or load_hotkey_bindings()
        self._errors = []
        self._ready.clear()
        self._stop.clear()

        if not any(b.enabled and b.key and b.mod_vk() for b in self._bindings.values()):
            return []

        self._thread = threading.Thread(target=self._hotkey_thread_main, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

        if not self._errors and not self.is_active():
            self._errors.append("快捷键未启动（注册失败或监听线程已退出）")
        return list(self._errors)

    def reload(self, bindings: dict[str, HotkeyBinding] | None = None) -> list[str]:
        was_suspended = self.is_suspended()
        errors = self.start(bindings)
        if was_suspended:
            self.set_suspended(True)
        return errors

    def poll(self) -> None:
        try:
            while True:
                action = self._queue.get_nowait()
                handler = self._handlers.get(action)
                if handler:
                    handler()
        except queue.Empty:
            pass

    def stop(self) -> None:
        self._stop.set()
        tid = self._thread_id
        if tid:
            _user32.PostThreadMessageW(int(tid), WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None


HOTKEY_LABEL = DEFAULT_HOTKEYS[ACTION_BIND_WINDOW]
GlobalHotkeyListener = GlobalHotkeyManager
