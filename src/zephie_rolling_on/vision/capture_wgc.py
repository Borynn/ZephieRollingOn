"""Windows Graphics Capture: long-lived session per hwnd."""
from __future__ import annotations

import sys
import threading
import time
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from windows_capture import CaptureControl, WindowsCapture

_LAST_CAPTURE_METHOD = ""
_WGC_CAPTURE_LOCK = threading.Lock()
# hwnd -> 长会话；多开时各窗口独立
_SESSIONS: dict[int, "_WgcHwndSession"] = {}


def last_capture_method() -> str:
    return _LAST_CAPTURE_METHOD


def is_wgc_available() -> bool:
    """Whether WGC can be used on this machine.

    Delegates to :func:`zephie_rolling_on.vision.capture.wgc_supported`, which
    checks the Windows build before attempting the import — the extension links
    WinRT and cannot be imported at all below Windows 10 1803.
    """
    from zephie_rolling_on.vision.capture import wgc_supported

    return wgc_supported()


def _set_method(name: str) -> None:
    global _LAST_CAPTURE_METHOD
    _LAST_CAPTURE_METHOD = name


def _window_rect_for_capture(hwnd: int) -> tuple[int, int, int, int]:
    """可见窗口外框 (left, top, right, bottom)，优先去掉 DWM 阴影。

    GetWindowRect 在 Win10+ 常含不可见阴影，导致按外框比例裁客户区时
    **偏多裁掉左侧**，整图内容相对客户区坐标往左移一截（高度仍可能对得上）。
    """
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

    # DWMWA_EXTENDED_FRAME_BOUNDS = 9：可见外框，不含阴影
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


def _crop_frame_to_client(image: np.ndarray, hwnd: int) -> np.ndarray:
    """WGC 有时含窗口边框/阴影，按客户区裁切以对齐 regions.yaml。"""
    import cv2
    import win32gui

    from zephie_rolling_on.vision.win32_window import _client_size_screen_pixels

    if image is None or image.size == 0:
        return image

    _x0, _y0, cw, ch = _client_size_screen_pixels(hwnd)
    h, w = image.shape[:2]
    if cw <= 0 or ch <= 0:
        return image
    if w == cw and h == ch:
        return image

    try:
        wl, wt, wr, wb = _window_rect_for_capture(hwnd)
    except win32gui.error:
        out = image[:ch, :cw] if h >= ch and w >= cw else image
        if out.shape[0] != ch or out.shape[1] != cw:
            return cv2.resize(out, (cw, ch), interpolation=cv2.INTER_AREA)
        return out

    win_w = max(1, wr - wl)
    win_h = max(1, wb - wt)
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    off_x = cx - wl
    off_y = cy - wt

    sx = w / win_w
    sy = h / win_h
    x1 = max(0, int(round(off_x * sx)))
    y1 = max(0, int(round(off_y * sy)))
    x2 = min(w, x1 + max(1, int(round(cw * sx))))
    y2 = min(h, y1 + max(1, int(round(ch * sy))))
    cropped = image[y1:y2, x1:x2]
    if cropped.size == 0:
        return image
    # 像素坐标必须与客户区物理尺寸一致，否则模板匹配的 x/y 会整段平移
    if cropped.shape[0] != ch or cropped.shape[1] != cw:
        cropped = cv2.resize(cropped, (cw, ch), interpolation=cv2.INTER_AREA)
    return cropped


def _capture_image_usable(image: np.ndarray) -> bool:
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
    return True


class _WgcHwndSession:
    """单个 hwnd 的长寿命 WGC 会话：按需取帧，不在每帧 stop。"""

    def __init__(self, hwnd: int) -> None:
        from windows_capture import Frame, InternalCaptureControl, WindowsCapture

        self.hwnd = int(hwnd)
        self._frame_lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._request = False
        self._ready = threading.Event()
        self._closed = False
        self._control: CaptureControl | None = None

        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=self.hwnd,
        )

        @capture.event
        def on_frame_arrived(frame: Frame, control: InternalCaptureControl) -> None:
            # 仅在有人请求时 copy，避免 60fps 全帧拷贝拖垮 CPU/内存带宽
            with self._frame_lock:
                if not self._request:
                    return
                try:
                    bgr = frame.convert_to_bgr().frame_buffer
                    self._latest = np.ascontiguousarray(bgr).copy()
                except Exception:
                    self._latest = None
                self._request = False
                self._ready.set()

        @capture.event
        def on_closed() -> None:
            self._closed = True
            self._ready.set()

        self._capture: WindowsCapture = capture
        self._control = capture.start_free_threaded()

    @property
    def alive(self) -> bool:
        if self._closed:
            return False
        if self._control is not None and self._control.is_finished():
            return False
        return True

    def grab(self, *, timeout_sec: float) -> np.ndarray | None:
        if not self.alive:
            return None
        self._ready.clear()
        with self._frame_lock:
            self._request = True
        if not self._ready.wait(timeout_sec):
            with self._frame_lock:
                self._request = False
            return None
        if self._closed:
            return None
        with self._frame_lock:
            img = self._latest
            return None if img is None else img.copy()

    def stop(self) -> None:
        self._closed = True
        self._ready.set()
        control = self._control
        self._control = None
        if control is None:
            return
        try:
            if not control.is_finished():
                control.stop()
            control.wait()
        except Exception:
            pass


def release_wgc_session(hwnd: int | None = None) -> None:
    """Release one WGC session, or all when hwnd is None."""
    to_stop: list[_WgcHwndSession] = []
    with _WGC_CAPTURE_LOCK:
        if hwnd is None:
            to_stop = list(_SESSIONS.values())
            _SESSIONS.clear()
        else:
            session = _SESSIONS.pop(int(hwnd), None)
            if session is not None:
                to_stop.append(session)
    for session in to_stop:
        session.stop()


def _get_or_create_session(hwnd: int) -> _WgcHwndSession | None:
    key = int(hwnd)
    session = _SESSIONS.get(key)
    if session is not None and session.hwnd == key and session.alive:
        return session
    if session is not None:
        _SESSIONS.pop(key, None)
        session.stop()
    try:
        session = _WgcHwndSession(key)
    except Exception:
        return None
    _SESSIONS[key] = session
    return session


def capture_window_wgc(hwnd: int, *, timeout_sec: float = 8.0) -> np.ndarray | None:
    """Capture HWND via WGC; reuse the per-hwnd session when possible."""
    if not is_wgc_available():
        return None

    import win32gui

    if not hwnd or not win32gui.IsWindow(hwnd):
        if hwnd:
            release_wgc_session(int(hwnd))
        return None

    key = int(hwnd)
    for attempt in range(2):
        with _WGC_CAPTURE_LOCK:
            session = _get_or_create_session(key)
        if session is None:
            return None

        # 取帧在锁外等待，避免与 release_wgc_session 死锁
        img = session.grab(timeout_sec=timeout_sec)
        if img is not None and _capture_image_usable(img):
            _set_method("wgc")
            return _crop_frame_to_client(img, hwnd)

        with _WGC_CAPTURE_LOCK:
            if _SESSIONS.get(key) is session:
                _SESSIONS.pop(key, None)
        session.stop()
        if attempt == 0:
            time.sleep(0.05)
    return None
