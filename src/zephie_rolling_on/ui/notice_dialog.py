"""阻断性提示弹窗，风格与主界面一致。

用于「必须停下来让用户处理」的情况，例如桌面裁切检测到游戏画面被遮挡。
调用方在弹窗返回后再决定停止测试或停止脚本。
"""

from __future__ import annotations

import tkinter as tk

from zephie_rolling_on.ui.zehpie_theme import (
    COLORS as _UI,
    apply_zehpie_window_icon,
    cute_font,
)


def show_blocking_notice(master: tk.Misc, title: str, message: str) -> None:
    """模态提示框；用户点「知道了」后返回。

    仅使用主界面已有的配色与字体，不引入新的视觉元素。
    """
    dialog = tk.Toplevel(master)
    dialog.title(title)
    dialog.configure(bg=_UI["bg"])
    dialog.resizable(False, False)
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

    # 保存引用，防止 PhotoImage 被回收
    if photo is not None:
        dialog._icon_ref = photo  # type: ignore[attr-defined]

    master.wait_window(dialog)


def _center(dialog: tk.Toplevel, master: tk.Misc) -> None:
    dialog.update_idletasks()
    w, h = max(dialog.winfo_width(), 420), max(dialog.winfo_height(), 180)
    try:
        x = master.winfo_rootx() + max(0, (master.winfo_width() - w) // 2)
        y = master.winfo_rooty() + max(0, (master.winfo_height() - h) // 3)
    except tk.TclError:
        x, y = 160, 160
    dialog.geometry(f"{w}x{h}+{x}+{y}")
