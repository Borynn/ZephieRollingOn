"""脚本运行期间强制屏幕常亮，停止时恢复。

为什么需要
----------
脚本靠**截屏**识别画面。人离开后系统若关掉显示器或进入睡眠，截到的是黑屏/锁屏
界面，识别会把整帧当成「变灰」或直接失败，脚本于是空跑一整晚。这里在脚本运行
期间把显示器与系统都钉在唤醒状态，停止后立刻恢复系统原有的节能行为。

必须在同一个线程申请与释放
--------------------------
``SetThreadExecutionState`` 的状态**按线程**记录：它只在调用它的那个线程存活期间
有效。若在后台线程申请、却在 UI 线程释放，那次释放对后台线程的状态不起作用，
屏幕会一直亮到进程退出。所以本模块**只允许 UI 线程调用**：控制面板在启动收尾
（``root.after`` 回调，即 UI 线程）申请，在所有停止路径（同为 UI 线程）释放。

标志位怎么取
------------
* 申请 ``ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED``：
  ``ES_CONTINUOUS`` 让状态**持续**生效（缺了它只是一次性重置计时器），
  ``ES_DISPLAY_REQUIRED`` 管显示器不关，``ES_SYSTEM_REQUIRED`` 管系统不睡眠。
* 释放 ``ES_CONTINUOUS`` 单独一个，即「恢复正常，由系统自行节能」。

覆盖不到的情况
--------------
``SetThreadExecutionState`` **不阻止屏保**（微软文档明确说明）：屏保一旦启动，仍会
盖住游戏画面。所以本模块另外用 ``SystemParametersInfo`` 临时停用屏保，
``keep_screen_awake`` 时停用、``allow_screen_sleep`` 时恢复。

屏保这条有两个必须知道的限制：

* **只作用于本次会话**。调用时不带 ``SPIF_UPDATEINIFILE``，因此**不写注册表**，
  注销/重启后自动回到用户的原始设置。这是刻意的：临时停用不该改用户配置。
* **恢复可能不生效**，且这在部分机器上是正常的。实测：注册表 ``ScreenSaveActive``
  为 0 且未配置 ``SCRNSAVE.EXE``（没选屏保）时，``SPI_SETSCREENSAVEACTIVE(TRUE)``
  返回成功但读回仍是 0 —— 没有屏保程序时系统不接受"启用"这个状态。因此：

  - 只有读回**原本确认为启用**时才去停用，避免制造无意义的状态改动；
  - 恢复失败时保留内部标志，交给调用方的兜底逻辑下次重试；
  - 最坏情况也只是「本次会话屏保保持停用」，注销即恢复。

本模块**不抛异常**：申请失败只返回 False。屏幕常亮属于「锦上添花」，不能因为它
让启动或停止流程崩掉。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000

# Screen saver: SetThreadExecutionState 管不到它，需要单独停用/恢复
SPI_GETSCREENSAVEACTIVE = 0x0010
SPI_SETSCREENSAVEACTIVE = 0x0011

_KEEP_AWAKE = ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
_RESTORE_DEFAULT = ES_CONTINUOUS

_kernel32: Any = None
_user32: Any = None
_bind_attempted = False
_held = False
# 是否由本模块临时停用了屏保 —— 只有真停用过才在恢复时去开回来
_screensaver_suppressed = False


def _bind() -> None:
    """绑定一次 Win32 签名（``ES_*`` 含 0x80000000，必须显式声明为无符号）。"""
    global _kernel32, _user32, _bind_attempted
    if _bind_attempted:
        return
    _bind_attempted = True
    if sys.platform != "win32":
        return
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetThreadExecutionState.argtypes = [wintypes.ULONG]
        k32.SetThreadExecutionState.restype = wintypes.ULONG
        _kernel32 = k32
    except Exception:
        _kernel32 = None
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        # uiAction/uiParam 都是 UINT；pvParam 是 BOOL*，fWinIni 是 UINT。
        # fWinIni 传 0（不带 SPIF_UPDATEINIFILE）＝ 只改本次会话，不写注册表。
        u32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        u32.SystemParametersInfoW.restype = wintypes.BOOL
        _user32 = u32
    except Exception:
        _user32 = None


def _get_screensaver_active() -> bool | None:
    """屏保当前是否启用；读不到返回 None（调用方据此不做任何改动）。"""
    if _user32 is None:
        return None
    val = ctypes.c_int(-1)
    try:
        if not _user32.SystemParametersInfoW(
            SPI_GETSCREENSAVEACTIVE, 0, ctypes.byref(val), 0
        ):
            return None
    except Exception:
        return None
    return bool(val.value)


def _set_screensaver_active(active: bool) -> bool:
    """启用/停用屏保（只影响本次会话）。注意见模块说明：无屏保程序时启用会被忽略。"""
    if _user32 is None:
        return False
    val = ctypes.c_int(1 if active else 0)
    try:
        return bool(
            _user32.SystemParametersInfoW(
                SPI_SETSCREENSAVEACTIVE, 0, ctypes.byref(val), 0
            )
        )
    except Exception:
        return False


def _apply(flags: int) -> bool:
    """下发一次执行状态；成功返回 True。任何异常都吞掉并返回 False。"""
    _bind()
    if _kernel32 is None:
        return False
    try:
        return _kernel32.SetThreadExecutionState(flags) != 0
    except Exception:
        return False


def is_screen_awake() -> bool:
    """是否仍在抑制（执行状态或屏保，任一未释放）。供界面兜底判断用。"""
    return _held or _screensaver_suppressed


def keep_screen_awake() -> bool:
    """申请强制屏幕常亮 + 临时停用屏保。

    返回 True 表示**本次调用真的申请了**（此前未申请）。``ES_CONTINUOUS`` 让执行
    状态持续生效，因此重复调用无副作用；重复调用返回 False，调用方据此避免重复
    打日志。
    """
    global _held, _screensaver_suppressed
    if _held:
        return False
    if not _apply(_KEEP_AWAKE):
        return False
    _held = True

    # 顺手停用屏保。只在「读回确认为启用」时才动：没配屏保的机器上启用/停用都无意义，
    # 读不到状态时更不能盲改。停用失败不影响上面的常亮申请。
    if _get_screensaver_active() is True and _set_screensaver_active(False):
        _screensaver_suppressed = True
    return True


def allow_screen_sleep() -> bool:
    """恢复系统原有节能行为与屏保；返回 True 表示本次至少恢复了一项。

    两项**各自独立判断**：某项恢复失败时另一项仍要恢复，且失败的那项保留内部
    标志，让调用方下一次再调（例如界面的轮询兜底）时自动重试——而不是留下「以为
    释放了其实没释放」的假象。

    调用点应把它当作 ``finally``：任何停止路径都该走一遍，忘记释放会让屏幕一直
    亮到进程退出。
    """
    global _held, _screensaver_suppressed
    restored = False
    if _held and _apply(_RESTORE_DEFAULT):
        _held = False
        restored = True
    if _screensaver_suppressed and _set_screensaver_active(True):
        _screensaver_suppressed = False
        restored = True
    return restored
