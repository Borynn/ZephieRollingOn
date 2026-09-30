"""阻断性提示弹窗，风格与主界面一致。

用于「必须停下来让用户处理」的情况，例如桌面裁切检测到游戏画面被遮挡。
调用方在弹窗返回后再决定停止测试或停止脚本。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageTk

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.ui.zehpie_theme import (
    COLORS as _UI,
    RoundedButton,
    apply_zehpie_window_icon,
    cute_font,
)


def show_blocking_notice(master: tk.Misc, title: str, message: str) -> None:
    """模态提示框；用户点「知道了」后返回。

    仅使用主界面已有的配色与字体，不引入新的视觉元素。

    先还原最小化的主窗：否则 ``master`` 的 rootx/rooty 是 -32000，本窗口会被
    放到屏幕外，而它紧接着 grab_set 抢走输入 —— 表现为「界面卡死、弹窗不见」。
    关掉后**不**把主窗再最小化：脚本此时已停止，用户本就需要看到主界面。
    """
    from zephie_rolling_on.ui.ui_geometry import (
        ensure_master_visible,
        should_set_transient,
    )

    restored = ensure_master_visible(master)

    dialog = tk.Toplevel(master)
    dialog.title(title)
    dialog.configure(bg=_UI["bg"])
    dialog.resizable(False, False)
    # 主窗刚由最小化还原时不能设 transient：窗口管理器会把本窗口 withdraw
    # （坐标正确但完全不可见），而它随后 grab_set 会让界面既无弹窗也无响应。
    if should_set_transient(master, restored=restored):
        dialog.transient(master)

    photo = apply_zehpie_window_icon(dialog)

    shell = tk.Frame(dialog, bg=_UI["bg"], padx=16, pady=14)
    shell.pack(fill="both", expand=True)

    card = tk.Frame(
        shell,
        bg=_UI["card"],
        highlightbackground=_UI["border"],
        highlightcolor=_UI["border"],
        highlightthickness=2,
        padx=14,
        pady=12,
    )
    card.pack(fill="both", expand=True)

    tk.Label(
        card,
        text=title,
        anchor="w",
        justify="left",
        bg=_UI["card"],
        fg=_UI.get("accent_deep", _UI["accent_dark"]),
        font=cute_font(13),
    ).pack(fill="x", pady=(0, 8))

    tk.Label(
        card,
        text=message,
        anchor="w",
        justify="left",
        wraplength=380,
        bg=_UI["card"],
        fg=_UI["text"],
        font=cute_font(9, bold=False),
    ).pack(fill="x", pady=(0, 12))

    btn_row = tk.Frame(card, bg=_UI["card"])
    btn_row.pack(fill="x")

    def _close() -> None:
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    tk.Button(
        btn_row,
        text="知道了",
        command=_close,
        relief="flat",
        bd=0,
        padx=26,
        pady=8,
        bg=_UI["accent"],
        fg="#FFFFFF",
        activebackground=_UI.get("accent_deep", _UI["accent_dark"]),
        activeforeground="#FFFFFF",
        font=cute_font(10),
        cursor="hand2",
    ).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", _close)
    dialog.update_idletasks()
    _center(dialog, master)
    try:
        dialog.grab_set()
    except tk.TclError:
        pass
    # 保持在最前，避免被游戏窗口盖住而看起来"没有反应"
    try:
        dialog.attributes("-topmost", True)
        dialog.lift()
        dialog.focus_force()
        dialog.after(200, lambda: dialog.attributes("-topmost", False))
    except tk.TclError:
        pass

    # 再次定位：实测「先 geometry 再 -topmost/lift/focus」会让窗口管理器丢掉
    # 先前请求的位置，窗口落到 (0,0) 或 Windows 的级联位置（本机多次运行依次
    # 得到 +8+31 / +86+109 / +112+135）。所以置顶之后必须重新应用一次几何。
    _center(dialog, master)

    # 保存引用，防止 PhotoImage 被回收
    if photo is not None:
        dialog._icon_ref = photo  # type: ignore[attr-defined]

    master.wait_window(dialog)


def _center(dialog: tk.Toplevel, master: tk.Misc | None = None) -> None:
    """屏幕居中偏上。见 :func:`ui_geometry.center_on_screen`。

    ``master`` 参数保留只为签名兼容，不参与计算：用屏幕坐标可避免主窗最小化时
    ``-32000`` 把弹窗推到屏幕外，也不受 ``transient``/级联/多显示器影响。
    """
    from zephie_rolling_on.ui.ui_geometry import center_on_screen

    center_on_screen(dialog)


# ---------------------------------------------------------------------------
# 系统缩放门禁（启动时）
# ---------------------------------------------------------------------------

# 与 config/regions.yaml 的标定基准绑定：缩放不是 100% 就无法识图。
SCALE_WARNING_TITLE = "Zephie发现你的系统缩放不是100%！"
SCALE_WARNING_HINT = "快去系统设置里改好，不然Zephie没法帮你 ~"

_SCALE_ART_PATH = project_root() / "assets" / "ui" / "scale_warning.png"
_SCALE_ART_HEIGHT = 150

# 字号与「模型导入」一致：主句 18、说明 11，且**同为加粗**。
# 说明句若不加粗，与主句字重不同，视觉上就像"另一种字体"。
_SCALE_TITLE_SIZE_MAX = 18
_SCALE_HINT_SIZE_MAX = 11
_SCALE_TITLE_SIZE_MIN = 9
_SCALE_HINT_SIZE_MIN = 8
# 外框 + 卡片内边距 + 图文间距 + 窗口边框余量（按屏幕宽度反推可用文字宽度）。
_SCALE_CHROME_W = 16 * 2 + 2 * 2 + 16 * 2 + 14 + 40


def _measure(font: tuple, text: str) -> int:
    return int(tkfont.Font(font=font).measure(text))


def _largest_fitting_size(
    text: str, *, max_size: int, min_size: int, available_px: int
) -> int:
    """在「单行放得下」的前提下取最大字号。"""
    for size in range(int(max_size), int(min_size) - 1, -1):
        if _measure(cute_font(size), text) <= available_px:
            return size
    return int(min_size)


def _fit_scale_text(dialog: tk.Toplevel, art_width: int) -> tuple[int, int, int]:
    """按可用宽度挑字号，保证两句话**各自单行**。

    返回 ``(主句字号, 附句字号, 兜底折行宽度)``。

    本弹窗只在缩放不为 100% 时出现，此时像素空间已被系统虚拟化；小屏高缩放
    下固定字号会把 "100%" 从中间拆成两行，所以这里主动缩字号而不是折行。

    兜底折行宽度平时为 0（Tk 中即"不折行"）。只有当可用宽度窄到连最小字号都
    放不下时才启用折行——那种情况下不折行会让整窗超出屏幕，连「确认」都点不到，
    折行是两害相权取其轻。
    """
    available = int(dialog.winfo_screenwidth()) - int(art_width) - _SCALE_CHROME_W
    title_size = _largest_fitting_size(
        SCALE_WARNING_TITLE,
        max_size=_SCALE_TITLE_SIZE_MAX,
        min_size=_SCALE_TITLE_SIZE_MIN,
        available_px=available,
    )
    hint_size = _largest_fitting_size(
        SCALE_WARNING_HINT,
        max_size=min(_SCALE_HINT_SIZE_MAX, title_size),
        min_size=_SCALE_HINT_SIZE_MIN,
        available_px=available,
    )
    widest = max(
        _measure(cute_font(title_size), SCALE_WARNING_TITLE),
        _measure(cute_font(hint_size), SCALE_WARNING_HINT),
    )
    wrap = available if widest > available > 0 else 0
    return title_size, hint_size, wrap


def _load_scale_art(parent: tk.Misc) -> ImageTk.PhotoImage | None:
    """左侧插画；白底与卡片同色，无需抠图即可无缝融入。"""
    if not _SCALE_ART_PATH.is_file():
        return None
    try:
        im = Image.open(_SCALE_ART_PATH).convert("RGB")
    except Exception:
        return None
    if im.height != _SCALE_ART_HEIGHT:
        width = max(1, int(round(im.width * (_SCALE_ART_HEIGHT / im.height))))
        im = im.resize((width, _SCALE_ART_HEIGHT), Image.Resampling.LANCZOS)
    try:
        return ImageTk.PhotoImage(im, master=parent)
    except Exception:
        return None


def _center_on_screen(dialog: tk.Toplevel) -> None:
    """屏幕居中偏上。启动阶段主窗口尚未显示，因此不依赖 master 的几何信息。"""
    dialog.update_idletasks()
    w = max(dialog.winfo_width(), dialog.winfo_reqwidth())
    h = max(dialog.winfo_height(), dialog.winfo_reqheight())
    x = max(0, (dialog.winfo_screenwidth() - w) // 2)
    y = max(0, (dialog.winfo_screenheight() - h) // 3)
    dialog.geometry(f"+{x}+{y}")


def show_scale_notice(master: tk.Misc) -> None:
    """系统缩放不是 100% 的阻断提示。

    版式沿用主界面：左侧插画、右侧两行文字、底部圆角「确认」按钮。
    字体与「模型导入」一致：同族（雅黑）且**同为加粗**，仅字号分级——
    附句若不加粗，字重差异看起来就像"另一种字体"。
    两句话各自单行：不靠折行，而是按屏幕可用宽度自动缩字号（见
    :func:`_fit_scale_text`），避免把 "100%" 从中间拆开。
    点「确认」后返回，由调用方关闭软件。
    """
    dialog = tk.Toplevel(master)
    dialog.title("Zephie Rolling On!")
    dialog.configure(bg=_UI["bg"])
    dialog.resizable(False, False)
    # 启动门禁用的是隐藏 root；Tk 会把 transient 子窗随隐藏的 master 一起隐藏，
    # 那样用户根本看不到提示，所以仅在 master 可见时才设 transient。
    try:
        if master.winfo_viewable():
            dialog.transient(master)
    except tk.TclError:
        pass

    # PhotoImage 必须保引用，否则被 GC 回收后控件会变空白
    photos: list[ImageTk.PhotoImage] = []
    icon = apply_zehpie_window_icon(dialog)
    if icon is not None:
        photos.append(icon)

    shell = tk.Frame(dialog, bg=_UI["bg"], padx=16, pady=14)
    shell.pack(fill="both", expand=True)

    card = tk.Frame(
        shell,
        bg=_UI["card"],
        highlightbackground=_UI["border"],
        highlightcolor=_UI["border"],
        highlightthickness=2,
        padx=16,
        pady=14,
    )
    card.pack(fill="both", expand=True)

    row = tk.Frame(card, bg=_UI["card"])
    row.pack(fill="both", expand=True)

    art = _load_scale_art(dialog)
    if art is not None:
        photos.append(art)
        tk.Label(row, image=art, bg=_UI["card"], bd=0).pack(
            side="left", anchor="n", padx=(0, 14)
        )

    text_col = tk.Frame(row, bg=_UI["card"])
    text_col.pack(side="left", fill="both", expand=True, anchor="n")

    art_width = art.width() if art is not None else 0
    title_size, hint_size, wrap = _fit_scale_text(dialog, art_width)

    # 正常情况 wrap=0，即不设折行：Tk 对中文会逐字折行，会把 "100%" 拆成
    # "1" / "00%"。这里用「按可用宽度缩字号」保证单行——两句话永远各占一行。
    tk.Label(
        text_col,
        text=SCALE_WARNING_TITLE,
        anchor="w",
        justify="left",
        wraplength=wrap,
        bg=_UI["card"],
        fg=_UI.get("accent_deep", _UI["accent_dark"]),
        font=cute_font(title_size),
    ).pack(anchor="w", pady=(20, 10))

    tk.Label(
        text_col,
        text=SCALE_WARNING_HINT,
        anchor="w",
        justify="left",
        wraplength=wrap,
        bg=_UI["card"],
        fg=_UI["text"],
        # 字重与主句一致（均加粗），否则看起来像另一种字体
        font=cute_font(hint_size),
    ).pack(anchor="w")

    btn_row = tk.Frame(card, bg=_UI["card"])
    btn_row.pack(fill="x", pady=(16, 2))

    def _close() -> None:
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    RoundedButton(
        btn_row,
        text="确认",
        command=_close,
        bg_color=_UI["accent"],
        fg_color="#FFFFFF",
        hover_bg=_UI["accent_deep"],
        border_color=_UI["accent"],
        radius=14,
        font=cute_font(10),
        height=36,
        width=128,
    ).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", _close)
    _center_on_screen(dialog)
    try:
        dialog.grab_set()
    except tk.TclError:
        pass
    # 保持在最前，避免启动瞬间被其它窗口盖住而看起来"没反应"
    try:
        dialog.attributes("-topmost", True)
        dialog.lift()
        dialog.focus_force()
        dialog.after(200, lambda: dialog.attributes("-topmost", False))
    except tk.TclError:
        pass

    dialog._photos_ref = photos  # type: ignore[attr-defined]

    master.wait_window(dialog)


def guard_system_scale() -> bool:
    """启动门禁：缩放不是 100% 时弹窗，用户确认后关闭软件。

    返回 ``True`` 表示可以继续启动；``False`` 表示调用方应立即退出。
    缩放正常时**不创建任何窗口**。
    """
    from zephie_rolling_on.ui.system_scale import is_scale_supported

    if is_scale_supported():
        return True

    root = tk.Tk()
    root.withdraw()
    try:
        show_scale_notice(root)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return False
