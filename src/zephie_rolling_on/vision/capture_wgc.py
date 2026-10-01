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

# 最近一次 WGC 取帧失败的原因。此前失败是**完全静默**的，于是用户报
# 「走了桌面裁切/被遮挡」时无法判断 WGC 究竟卡在哪一步（会话创建、取帧超时、
# 还是画面被判空），只能靠猜。
_WGC_FAILURE_REASON = ""


def wgc_failure_reason() -> str:
    """最近一次 WGC 取帧失败的原因；成功或尚未调用时为空串。"""
    return _WGC_FAILURE_REASON


def _set_failure_reason(reason: str) -> None:
    global _WGC_FAILURE_REASON
    _WGC_FAILURE_REASON = reason


# ``draw_border=False`` 会走 ``GraphicsCaptureSession.SetIsBorderRequired``。该属性
# 由 **Windows 10 版本 2104（内部版本 20348）** 引入（微软文档原文；常被简化成
# 「Win11 才有」，不准确）。上游 windows-capture 的判定是**属性是否存在**：
#
#     if draw_border_settings != DrawBorderSettings::Default {
#         if Self::is_border_settings_supported()? { ...SetIsBorderRequired(...)  }
#         else { return Err(Error::BorderConfigUnsupported); }
#     }
#
# 关键是那个 ``!= Default`` 判断：传 ``True``（WithBorder）**同样**会走进这段、
# 在缺该属性的系统上返回 ``BorderConfigUnsupported``。所以**唯一安全的选择是
# 不传该参数**（即 Default），而不是改传 True。
#
# 后果：不传参数时上述整段被跳过，Win10 上照常工作（该版本本就无法关闭边框，
# 是系统限制而非缺陷）。
WGC_BORDER_MIN_BUILD = 20348

# 本进程内已确认「边框参数不被支持」。版本判断若不准、或上游改了行为，靠它自愈，
# 避免每次截图都重撞一次同样的异常。
_border_param_unsupported = False

# 同一 hwnd 的**会话创建连续失败**次数上限。会话创建失败通常是机器级或窗口级条件
# （缺 API、窗口类型不被支持），而运行时每次 poll 都会重新尝试创建——最坏情况
# 约 30 次/分钟，纯属空转。达到上限后不再尝试，直接走桌面裁切回退。
# 成功创建一次即清零（说明条件不再成立）；重新绑定窗口也清零，见
# ``reset_session_create_attempts``。
_MAX_SESSION_CREATE_ATTEMPTS = 3

# hwnd -> 连续创建失败次数
_CREATE_FAILS: dict[int, int] = {}


def _note_create_failure(key: int) -> None:
    _CREATE_FAILS[key] = _CREATE_FAILS.get(key, 0) + 1


def reset_session_create_attempts(hwnd: int | None = None) -> None:
    """清零会话创建的重试计数；``None`` 表示全部。

    由界面在**重新绑定游戏窗口**时调用。即便绑回的是同一个窗口也应当清零：用户
    重新绑定往往正是因为上一次不可用（换了窗口形态、改了系统版本、装了更新），
    此时旧计数已不代表现状。
    """
    with _WGC_CAPTURE_LOCK:
        if hwnd is None:
            _CREATE_FAILS.clear()
        else:
            _CREATE_FAILS.pop(int(hwnd), None)


def session_create_blocked(hwnd: int) -> bool:
    """该 hwnd 是否已因连续失败而停止重试（运行时会被降级到桌面裁切）。"""
    return int(_CREATE_FAILS.get(int(hwnd), 0)) >= _MAX_SESSION_CREATE_ATTEMPTS


def _suppress_border_param() -> None:
    """标记本机不支持边框参数；此后构造会话都不再传它。"""
    global _border_param_unsupported
    _border_param_unsupported = True


def _border_kwargs() -> dict[str, bool]:
    """``WindowsCapture`` 的边框参数；不支持时返回空（即不传该参数，保持 Default）。

    传 ``True`` 不是安全的替代：上游只在 ``!= Default`` 时才做能力检查，明确要求
    画边框同样会因缺该属性而返回 ``BorderConfigUnsupported``。见 ``WGC_BORDER_MIN_BUILD``。
    """
    if _border_param_unsupported:
        return {}
    try:
        from zephie_rolling_on.vision.capture import windows_build

        build = windows_build()
    except Exception:
        return {}
    # 读不到版本号（0）时也不传：Win10 能正常工作，Win11 最多多一个黄框，
    # 远比「会话直接失败」好。
    return {"draw_border": False} if build >= WGC_BORDER_MIN_BUILD else {}


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
        # 帧回调里转换失败的原文。此前被 `except Exception` 静默吞掉，导致
        # 「帧到了但转换失败」与「一帧都没到（超时）」在下游完全无法区分，
        # 只能一律报成超时——这会把人引向错误的方向。
        self._callback_error = ""
        # 累计到达的帧数（无论是否被请求），用于区分「没帧」与「帧为空」。
        self._arrivals = 0

        capture = WindowsCapture(
            cursor_capture=False,
            window_hwnd=self.hwnd,
            **_border_kwargs(),
        )

        @capture.event
        def on_frame_arrived(frame: Frame, control: InternalCaptureControl) -> None:
            # 仅在有人请求时 copy，避免 60fps 全帧拷贝拖垮 CPU/内存带宽
            with self._frame_lock:
                self._arrivals += 1
                if not self._request:
                    return
                try:
                    bgr = frame.convert_to_bgr().frame_buffer
                    self._latest = np.ascontiguousarray(bgr).copy()
                    self._callback_error = ""
                except Exception as exc:  # noqa: BLE001
                    self._latest = None
                    self._callback_error = f"{type(exc).__name__}: {exc}"
                self._request = False
                self._ready.set()

        @capture.event
        def on_closed() -> None:
            self._closed = True
            self._ready.set()

        self._capture: WindowsCapture = capture
        self._control = capture.start_free_threaded()

    @property
    def callback_error(self) -> str:
        """最近一次帧转换失败的原文；正常时为 ``""``。"""
        return self._callback_error

    @property
    def arrivals(self) -> int:
        """累计到达的帧数。用于判断「没收到帧」还是「收到的帧被判空」。"""
        return self._arrivals

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


def _get_or_create_session(
    hwnd: int, *, force: bool = False
) -> _WgcHwndSession | None:
    key = int(hwnd)
    session = _SESSIONS.get(key)
    if session is not None and session.hwnd == key and session.alive:
        return session
    if session is not None:
        _SESSIONS.pop(key, None)
        session.stop()

    # 连续失败达上限：不再尝试创建。桌面裁切回退仍然可用，而每次 poll 都重撞一次
    # 注定失败的会话没有意义。重新绑定窗口会清零。
    # ``force=True`` 供**自检**使用：诊断要拿到真正的失败原因，而不是「已停止重试」
    # 这条结论本身——否则报告会把根因挡在后面。
    if not force and _CREATE_FAILS.get(key, 0) >= _MAX_SESSION_CREATE_ATTEMPTS:
        _set_failure_reason(
            f"创建 WGC 会话已连续失败 {_MAX_SESSION_CREATE_ATTEMPTS} 次，"
            "本次绑定内不再重试（重新绑定游戏窗口可重置）"
        )
        return None

    try:
        session = _WgcHwndSession(key)
    except Exception as exc:  # noqa: BLE001
        first = f"{type(exc).__name__}: {exc}"
        session = None
        # 自愈：版本判断失准（或上游改了行为）时，从错误原文认出「边框参数不支持」，
        # 去掉该参数重试一次，此后本进程不再传它。
        if not _border_param_unsupported and "border" in first.lower():
            _suppress_border_param()
            try:
                session = _WgcHwndSession(key)
            except Exception as exc2:  # noqa: BLE001
                _set_failure_reason(
                    "创建 WGC 会话失败（已去掉边框参数重试）："
                    f"{type(exc2).__name__}: {exc2}"
                )
                _note_create_failure(key)
                return None
            _set_method("wgc")
        if session is None:
            _set_failure_reason(f"创建 WGC 会话失败：{first}")
            _note_create_failure(key)
            return None
    _CREATE_FAILS.pop(key, None)
    _SESSIONS[key] = session
    return session


def probe_wgc_session(hwnd: int) -> tuple[bool, str]:
    """实测能否为 ``hwnd`` 创建 WGC 会话；返回 ``(是否成功, 失败原因)``。

    供自检使用。``is_wgc_available()`` 只证明扩展**能导入**，不代表会话真能创建
    ——Win10 上 ``IsBorderRequired`` 缺失那次事故，自检就报着「WGC 可用」而实际
    每次截屏都失败。真正建一次会话，结论才诚实。

    **绕过「连续失败 3 次停止重试」的限制**：诊断要拿到真正的失败原因，而不是
    「已停止重试」这条结论本身。运行时是否已被降级，由
    ``session_create_blocked`` 另行报告。

    不在此处释放会话：复用同一会话更便宜，由调用方（自检结束）统一释放。
    """
    _set_failure_reason("")
    if not is_wgc_available():
        from zephie_rolling_on.vision.capture import wgc_unsupported_reason

        return False, wgc_unsupported_reason() or "WGC 不可用（版本不足或扩展导入失败）"
    with _WGC_CAPTURE_LOCK:
        session = _get_or_create_session(int(hwnd), force=True)
    if session is None:
        return False, wgc_failure_reason() or "会话创建失败（原因未记录）"
    return True, ""


def capture_window_wgc(
    hwnd: int, *, timeout_sec: float = 8.0, blank_retries: int = 2
) -> np.ndarray | None:
    """Capture HWND via WGC; reuse the per-hwnd session when possible.

    ``blank_retries``：画面被判空时在**同一会话**内额外重取几次。判空多半是转场/
    加载这类**瞬时**状态，而重建会话既要重做 ``GraphicsCaptureItem`` 转换、又更
    容易失败，所以先原地重试更稳。
    """
    _set_failure_reason("")
    if not is_wgc_available():
        _set_failure_reason("WGC 不可用（见 wgc_unsupported_reason）")
        return None

    import win32gui

    if not hwnd or not win32gui.IsWindow(hwnd):
        if hwnd:
            release_wgc_session(int(hwnd))
        _set_failure_reason("窗口句柄无效或已失效")
        return None

    key = int(hwnd)
    grabs_per_session = max(1, int(blank_retries) + 1)

    for session_attempt in range(2):
        with _WGC_CAPTURE_LOCK:
            session = _get_or_create_session(key)
        if session is None:
            return None

        frames_flowed = False
        last_blank_mean = 0.0
        for _ in range(grabs_per_session):
            # 取帧在锁外等待，避免与 release_wgc_session 死锁
            img = session.grab(timeout_sec=timeout_sec)
            if img is None:
                break
            frames_flowed = True
            if _capture_image_usable(img):
                _set_method("wgc")
                return _crop_frame_to_client(img, hwnd)
            last_blank_mean = float(img.mean())

        if frames_flowed:
            # 能稳定取到帧，只是内容为空：这是**窗口内容**的问题，会话本身没坏，
            # 重建没有意义，直接给出精确原因（不必再做第二次会话尝试）。
            _set_failure_reason(
                f"连续取到 {grabs_per_session} 帧但画面为空"
                f"（灰度均值 {last_blank_mean:.1f}）；"
                "可能是转场/加载画面、窗口最小化或独占全屏"
            )
            return None

        # 一帧都没拿到：会话可能真的坏了，销毁后重建一次
        err = session.callback_error
        _set_failure_reason(
            f"帧到达但转换失败：{err}"
            if err
            else f"等待帧超时（>{timeout_sec:g}s，累计到达 {session.arrivals} 帧）"
        )

        with _WGC_CAPTURE_LOCK:
            if _SESSIONS.get(key) is session:
                _SESSIONS.pop(key, None)
        session.stop()
        if session_attempt == 0:
            time.sleep(0.05)
    return None
