"""窗口几何记忆：主界面 / 快捷键 / 详细卡池 位置与尺寸。"""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

import re
from pathlib import Path

import yaml

_PROJECT_ROOT = project_root()
GEOMETRY_PATH = _PROJECT_ROOT / "config" / "ui_geometry.yaml"

_GEO_RE = re.compile(
    r"^(?P<w>\d+)x(?P<h>\d+)(?:\+(?P<x>-?\d+)\+(?P<y>-?\d+))?$"
)


def ensure_master_visible(master) -> bool:
    """弹出以 ``master`` 居中的模态窗**之前**调用：主窗最小化就先还原。

    返回 ``True`` 表示**确实做过还原**。调用方据此决定要不要设 ``transient``：
    对一个刚由最小化还原的主窗，设置 ``transient`` 会让窗口管理器把子窗
    ``withdraw`` —— 实测必现（见 ``should_set_transient``）。

    为什么必须还原：窗口最小化时 Windows 让 ``winfo_rootx`` / ``winfo_rooty``
    返回哨兵值 ``-32000``。任何按 master 坐标居中的子窗都会被放到屏幕外，而它
    紧接着会 ``grab_set()`` 抢走输入 —— 于是**弹窗看不见、主界面又点不动**，整个
    程序表现得像卡死。触发场景很常见：开着自动点击把面板最小化去玩游戏，脚本因
    画面遮挡停止，那个提示就落到了屏幕外。

    ``deiconify()`` 会**同步**更新坐标（实测立刻可读到正确值，无需等事件循环），
    所以还原后紧接着居中即可，不必插 ``after`` 或 pump。

    只处理 ``iconic``：``withdrawn`` 是刻意隐藏的（例如启动门禁用的隐藏 root），
    把它显示出来会凭空多出一个空窗口。
    """
    import tkinter as tk

    try:
        if not master.winfo_exists():
            return False
        state = getattr(master, "state", None)
        if callable(state) and state() == "iconic":
            master.deiconify()
            master.update_idletasks()
            return True
    except tk.TclError:
        return False
    return False


def center_on_screen(dialog, *, y_divisor: int = 3) -> None:
    """把 ``dialog`` 放到**屏幕**中央偏上。

    为什么用屏幕坐标而非 ``master`` 坐标：

    1. 主窗最小化时 Windows 让 ``winfo_rootx/rooty`` 返回 ``-32000``，按 master
       居中的弹窗会落到屏幕外；而它随后 ``grab_set()`` 抢走输入，用户既看不到
       弹窗、又点不动界面。
    2. 与 ``transient``、窗口级联、多显示器都无关，结果是确定的。

    ⚠ **必须在 `-topmost`/``lift``/``focus_force`` **之后**再调用一次**：实测
    「先 geometry 再置顶」会让窗口管理器丢掉先前请求的位置，窗口落到 (0,0) 或
    Windows 的级联位置（本机连续多次运行依次得到 +8+31 / +86+109 / +112+135）。
    典型用法：

        center_on_screen(dialog)
        dialog.grab_set()
        dialog.attributes("-topmost", True); dialog.lift(); ...
        center_on_screen(dialog)   # 置顶会吞掉位置，这里补回来
    """
    import tkinter as tk

    try:
        dialog.update_idletasks()
        w = max(dialog.winfo_width(), dialog.winfo_reqwidth(), 320)
        h = max(dialog.winfo_height(), dialog.winfo_reqheight(), 160)
        sw = int(dialog.winfo_screenwidth())
        sh = int(dialog.winfo_screenheight())
    except tk.TclError:
        return
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // max(1, y_divisor))
    try:
        dialog.geometry(f"{w}x{h}+{x}+{y}")
    except tk.TclError:
        pass


def should_set_transient(master, *, restored: bool) -> bool:
    """是否该对 ``master`` 设 ``transient``。

    ``transient`` 的作用是让弹窗跟随主窗（始终在其之上、随其最小化）。但实测：
    对一个**刚刚从最小化还原**的主窗设置 ``transient``，窗口管理器会把该子窗
    ``withdraw`` —— 弹窗坐标正确却完全不可见，而它已 ``grab_set()``，于是界面
    既无弹窗也无响应。函数名与实测对照：

    ==========================================  ============
    做法                                        结果
    ==========================================  ============
    master 正常 + transient                      normal（正常）
    master 刚还原 + transient                    **withdrawn**
    master 刚还原 + 不设 transient               normal
    ==========================================  ============

    所以只在「未做过还原」时设 transient，其余情况放弃这一绑定：弹窗仍有
    ``grab_set()`` 与短暂置顶，可见且可交互，只是不再跟随主窗——这是可接受的
    取舍，远比弹窗消失好。
    """
    import tkinter as tk

    if restored:
        return False
    try:
        return bool(master.winfo_viewable())
    except tk.TclError:
        return False


def _load_all() -> dict:
    if not GEOMETRY_PATH.is_file():
        return {}
    try:
        with GEOMETRY_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _save_all(data: dict) -> None:
    try:
        GEOMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with GEOMETRY_PATH.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)
    except OSError:
        pass


def load_geometry(key: str) -> str | None:
    raw = _load_all().get(key)
    if not isinstance(raw, str) or not _GEO_RE.match(raw.strip()):
        return None
    return raw.strip()


def save_geometry(key: str, geometry: str) -> None:
    geo = (geometry or "").strip()
    if not _GEO_RE.match(geo):
        return
    data = _load_all()
    if data.get(key) == geo:
        return
    data[key] = geo
    _save_all(data)


def apply_saved_geometry(
    win,
    key: str,
    *,
    default: str,
) -> None:
    """应用已保存几何；无效则回退 default。尽量保证窗口落在可见屏内。"""
    geo = load_geometry(key) or default
    try:
        win.geometry(geo)
        win.update_idletasks()
    except Exception:
        try:
            win.geometry(default)
        except Exception:
            return
        return

    try:
        import tkinter as tk

        x = int(win.winfo_x())
        y = int(win.winfo_y())
        w = int(win.winfo_width())
        h = int(win.winfo_height())
        sw = int(win.winfo_screenwidth())
        sh = int(win.winfo_screenheight())
        # 至少露出标题栏一截
        margin = 40
        if x + w < margin:
            x = margin - min(w, sw // 2)
        if y + h < margin:
            y = 0
        if x > sw - margin:
            x = max(0, sw - margin)
        if y > sh - margin:
            y = max(0, sh - margin)
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


def remember_window_geometry(win, key: str) -> None:
    """读取当前 geometry 并写入配置。"""
    try:
        win.update_idletasks()
        geo = win.winfo_geometry()
    except Exception:
        return
    # winfo_geometry → "WxH+X+Y"
    save_geometry(key, geo)
