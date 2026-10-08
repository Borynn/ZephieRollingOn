"""Windows 任务栏身份（AppUserModelID）与窗口图标槽位。

两件互不相干的事，都只在 Windows 上生效：

1. **任务栏独立实体** —— 进程不显式声明 AppUserModelID 时，Windows 用 exe
   路径推导隐式 ID。用 ``python.exe`` 跑源码时，所有 Python 程序会挤进同一个
   任务栏按钮；打包成 exe 后虽自带 ID，但 ID 跟着文件名走，改名/换目录就换
   实体。``set_app_user_model_id()`` 把 ID 固定下来，任务栏于是给出**独立**
   实体：能单独钉住、单独预览、不与其它 Python 程序合并。

2. **图标发虚** —— Tk 的 ``wm iconbitmap`` 只 ``LoadImage`` 出**一个**句柄
   （``LR_DEFAULTSIZE`` → ``SM_CXICON``，100% 缩放下是 32x32），再把这个句柄
   同时写进 ``ICON_BIG`` / ``ICON_SMALL`` / ``ICON_SMALL2``。但标题栏要的是
   ``SM_CXSMICON``（100% 下 16x16），只能拿 32x32 硬缩 —— 这就是"图标糊"的
   根因。``apply_window_icon()`` 改成按窗口所在显示器的 DPI **分别**
   ``LoadImage`` 出正确尺寸，再分槽写入；源 .ico 里的帧尺寸与这些档位一一
   对应（见 ``scripts/build_window_icon.py``），因此不需要任何缩放插值。

AppUserModelID 必须在**创建第一个窗口之前**设置，否则已经建好的窗口会带着
旧 ID 进任务栏。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

APP_USER_MODEL_ID = "ZephieRollingOn.ControlPanel"

WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010

SM_CXICON = 11
SM_CXSMICON = 49

GA_ROOT = 2

GCLP_HICON = -14
GCLP_HICONSM = -34

# SetWindowPos
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

FALLBACK_DPI = 96
FALLBACK_BIG = 32
FALLBACK_SMALL = 16

_user32: Any = None
_shell32: Any = None
_set_class: Any = None
_bind_attempted = False
# LoadImage 出来的 HICON 由窗口持有，进程内不能再销毁；按 (路径, 尺寸) 复用，
# 避免每弹一个对话框就多加载两份。
_hicon_cache: dict[tuple[str, int, int], tuple[int, int]] = {}


def _bind() -> None:
    """绑定一次 Win32 函数签名。

    必须显式写 ``argtypes`` / ``restype``：64 位下 HICON 是指针，而 ctypes 默认
    按 ``c_int`` 取返回值，会把句柄截断成 32 位。
    """
    global _user32, _shell32, _set_class, _bind_attempted
    if _bind_attempted:
        return
    _bind_attempted = True
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        # SetClassLongPtrW 只在 64 位 user32 里存在，32 位叫 SetClassLongW
        set_class = getattr(user32, "SetClassLongPtrW", None) or getattr(
            user32, "SetClassLongW"
        )
        set_class.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        set_class.restype = ctypes.c_void_p
        _set_class = set_class
        _user32 = user32
    except Exception:
        _user32 = None
        return
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        _shell32 = shell32
    except Exception:
        _shell32 = None


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """声明进程的 AppUserModelID，让任务栏给出独立实体。须在建窗口前调用。"""
    _bind()
    if _shell32 is None:
        return False
    try:
        return _shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0
    except Exception:
        return False


def _icon_sizes(user32: Any, hwnd: int) -> tuple[int, int]:
    """窗口所在显示器的 ICON_BIG / ICON_SMALL 像素尺寸。

    进程是 per-monitor DPI aware，``GetSystemMetrics`` 只反映系统 DPI；混合
    DPI 多屏下要按窗口所在显示器的 DPI 取，才和 Windows 实际请求的尺寸对上。
    """
    dpi = FALLBACK_DPI
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    if get_dpi is not None:
        try:
            get_dpi.argtypes = [ctypes.c_void_p]
            get_dpi.restype = wintypes.UINT
            dpi = int(get_dpi(ctypes.c_void_p(hwnd))) or FALLBACK_DPI
        except Exception:
            dpi = FALLBACK_DPI

    get_for_dpi = getattr(user32, "GetSystemMetricsForDpi", None)
    if get_for_dpi is not None:
        try:
            get_for_dpi.argtypes = [ctypes.c_int, wintypes.UINT]
            get_for_dpi.restype = ctypes.c_int
            big = int(get_for_dpi(SM_CXICON, dpi))
            small = int(get_for_dpi(SM_CXSMICON, dpi))
            if big > 0 and small > 0:
                return big, small
        except Exception:
            pass

    try:
        return (
            int(user32.GetSystemMetrics(SM_CXICON)) or FALLBACK_BIG,
            int(user32.GetSystemMetrics(SM_CXSMICON)) or FALLBACK_SMALL,
        )
    except Exception:
        return FALLBACK_BIG, FALLBACK_SMALL


def _load_icons(user32: Any, ico_path: Path, big: int, small: int) -> tuple[int, int]:
    key = (str(ico_path), big, small)
    cached = _hicon_cache.get(key)
    if cached is not None:
        return cached
    h_big = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, big, big, LR_LOADFROMFILE)
    h_small = user32.LoadImageW(
        None, str(ico_path), IMAGE_ICON, small, small, LR_LOADFROMFILE
    )
    pair = (int(h_big or 0), int(h_small or 0))
    if pair != (0, 0):
        _hicon_cache[key] = pair
    return pair


def apply_window_icon(win: Any, ico_path: str | Path) -> bool:
    """把 ``.ico`` 按正确尺寸分槽写给窗口（标题栏 + 任务栏）。成功返回 True。

    ``win`` 是任意 Tk 窗口（``winfo_id()`` 拿到的是 TkChild）；标题栏和任务栏
    按钮属于它的顶层 wrapper，所以先 ``GetAncestor(.., GA_ROOT)`` 再写。
    """
    _bind()
    user32 = _user32
    if user32 is None:
        return False
    path = Path(ico_path)
    if not path.is_file():
        return False
    try:
        hwnd = int(win.winfo_id())
    except Exception:
        return False
    if not hwnd:
        return False
    try:
        root = int(user32.GetAncestor(ctypes.c_void_p(hwnd), GA_ROOT) or hwnd)
        big, small = _icon_sizes(user32, root)
        h_big, h_small = _load_icons(user32, path, big, small)
        if not h_big and not h_small:
            return False
        send = user32.SendMessageW
        if h_big:
            send(ctypes.c_void_p(root), WM_SETICON, ICON_BIG, h_big)
        if h_small:
            send(ctypes.c_void_p(root), WM_SETICON, ICON_SMALL, h_small)
        # 类图标：之后新建的 Toplevel / 子窗口默认继承它
        _set_class(ctypes.c_void_p(root), GCLP_HICON, h_big or h_small)
        _set_class(ctypes.c_void_p(root), GCLP_HICONSM, h_small or h_big)
        # 窗口已经显示出来时，标题栏不会自动重画 —— 强制刷一次非客户区，
        # 否则弹窗会短暂留着 Tk 的羽毛笔，直到下一次重绘
        if user32.IsWindowVisible(ctypes.c_void_p(root)):
            user32.SetWindowPos(
                ctypes.c_void_p(root),
                None,
                0,
                0,
                0,
                0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
    except Exception:
        return False
    return True
