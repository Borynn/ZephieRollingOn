"""Zehpie UI theme: colors, fonts, rounded buttons, window icon."""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageTk

_PROJECT_ROOT = project_root()
WINDOW_ICON_PATH = _PROJECT_ROOT / "assets" / "ui" / "zehpie_window_icon.png"

COLORS = {
    "bg": "#FFF7FB",
    "card": "#FFFFFF",
    "border": "#E8D5F0",
    "text": "#6B4E7A",
    "muted": "#A88BB8",
    "faint": "#C4B0D0",
    "accent": "#C9A0DC",
    "accent_dark": "#B07CC8",
    "accent_deep": "#B07CC8",
    "accent_soft": "#F3E8F8",
    "accent_hover": "#EBDDF8",
    "primary": "#9A68D9",
    "primary_dark": "#7C44C4",
    "primary_light": "#F3EBFC",
    "primary_hover": "#8B54D1",
    "danger": "#E889A8",
    "danger_soft": "#FCE8F0",
    "danger_bg": "#FFF0F3",
    "danger_text": "#FF6B8B",
    "danger_border": "#FFD0DA",
    "badge_mint_bg": "#E6FAF8",
    "badge_mint_fg": "#2EC4B6",
    "badge_pink_bg": "#FFE8EE",
    "badge_pink_fg": "#DE537E",
    "accent_pink": "#FF6584",
    "entry_bg": "#FAF8FD",
    "border_sel": "#B07CC8",
    "radio_empty": "#E0D0EC",
    "warn": "#D4A88A",
    "mint": "#8ED9C5",
    "pink": "#F5A3C0",
    "sky": "#9CCFEF",
    "ring_track": "#F0E4F6",
}


class RoundedButton(tk.Canvas):
    """Canvas-drawn rounded button; supports ``config(text=…, state=…)``."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], Any] | None = None,
        *,
        bg_color: str | None = None,
        fg_color: str | None = None,
        hover_bg: str | None = None,
        border_color: str | None = None,
        radius: int = 10,
        font: tuple | None = None,
        height: int = 32,
        width: int | None = None,
        **kwargs: Any,
    ) -> None:
        parent_bg = parent.cget("bg") if "bg" not in kwargs else kwargs.pop("bg")
        super().__init__(
            parent,
            height=height,
            bg=parent_bg,
            highlightthickness=0,
            **kwargs,
        )
        self.command = command
        self.text = text
        self.bg_color = bg_color or COLORS["primary_light"]
        self.hover_bg = hover_bg or self.bg_color
        self.fg_color = fg_color or COLORS["primary"]
        self.border_color = border_color or COLORS["border"]
        self.radius = radius
        self.font = font or ("Microsoft YaHei UI", 9, "bold")
        self.current_bg = self.bg_color
        self._enabled = True
        if width is not None:
            self.configure(width=width)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", self._draw)

    def _draw(self, event: tk.Event | None = None) -> None:
        self.delete("all")
        w = self.winfo_width() if event is None else int(event.width)
        h = self.winfo_height() if event is None else int(event.height)
        if w <= 1 or h <= 1:
            return
        r = self.radius
        points = [
            r, 1,
            w - r, 1,
            w - 1, 1,
            w - 1, r,
            w - 1, h - r,
            w - 1, h - 1,
            w - r, h - 1,
            r, h - 1,
            1, h - 1,
            1, h - r,
            1, r,
            1, 1,
        ]
        fill = self.current_bg if self._enabled else COLORS["entry_bg"]
        fg = self.fg_color if self._enabled else COLORS["faint"]
        self.create_polygon(
            points,
            fill=fill,
            outline=self.border_color,
            width=1.2,
            smooth=True,
        )
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)

    def _on_enter(self, _e: tk.Event) -> None:
        if not self._enabled:
            return
        self.current_bg = self.hover_bg
        self._draw()
        self.config(cursor="hand2")

    def _on_leave(self, _e: tk.Event) -> None:
        self.current_bg = self.bg_color
        self._draw()
        self.config(cursor="")

    def _on_click(self, _e: tk.Event) -> None:
        if self._enabled and self.command:
            self.command()

    def configure(self, cnf: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        if cnf and isinstance(cnf, dict):
            kw = {**cnf, **kw}
        elif cnf is not None and not isinstance(cnf, dict):
            return super().configure(cnf)
        text = kw.pop("text", None)
        state = kw.pop("state", None)
        if text is not None:
            self.text = str(text)
        if state is not None:
            self._enabled = str(state) in ("normal", "active", tk.NORMAL)
            if not self._enabled:
                self.current_bg = self.bg_color
        result = super().configure(**kw) if kw else None
        self._draw()
        return result

    config = configure  # type: ignore[assignment]


def draw_dashed_divider(canvas: tk.Canvas, color: str | None = None) -> None:
    """在 Canvas 上画一条水平虚线分割线。"""
    canvas.delete("all")
    w = canvas.winfo_width()
    if w <= 1:
        return
    canvas.create_line(
        0, 3, w, 3, fill=color or COLORS["border"], dash=(4, 3), width=1
    )


def make_rounded_entry(
    parent: tk.Misc,
    *,
    textvariable: tk.Variable | None = None,
    width: int = 6,
    font: tuple | None = None,
    bg: str | None = None,
    fg: str | None = None,
    border: str | None = None,
    height: int = 28,
    radius: int = 12,
) -> tuple[tk.Frame, tk.Entry]:
    """真圆角浅底输入框：Canvas 只画底，Entry 叠在上层（避免 delete 误删控件）。"""
    fill = bg or COLORS["entry_bg"]
    outline = border or COLORS["border"]
    text_fg = fg or COLORS["text"]
    parent_bg = parent.cget("bg")
    wrap = tk.Frame(parent, bg=parent_bg, highlightthickness=0, bd=0, width=1, height=height)
    px_w = max(48, int(width) * 9 + 28)
    wrap.configure(width=px_w, height=height)
    wrap.pack_propagate(False)

    canvas = tk.Canvas(
        wrap,
        width=px_w,
        height=height,
        bg=parent_bg,
        highlightthickness=0,
        bd=0,
    )
    canvas.place(x=0, y=0, relwidth=1, relheight=1)

    def _paint(_event: tk.Event | None = None) -> None:
        # 只清背景层，绝不 delete("all")，以免误删其它绑定
        canvas.delete("bg")
        w = max(int(canvas.winfo_width()), px_w)
        h = max(int(canvas.winfo_height()), height)
        r = min(radius, h // 2, w // 2)
        pts = [
            r, 1,
            w - r, 1,
            w - 1, 1,
            w - 1, r,
            w - 1, h - r,
            w - 1, h - 1,
            w - r, h - 1,
            r, h - 1,
            1, h - 1,
            1, h - r,
            1, r,
            1, 1,
        ]
        canvas.create_polygon(
            pts, fill=fill, outline=outline, width=1.2, smooth=True, tags=("bg",)
        )
        canvas.tag_lower("bg")

    canvas.bind("<Configure>", _paint)
    _paint()

    ent = tk.Entry(
        wrap,
        textvariable=textvariable,
        width=width,
        font=font,
        bg=fill,
        fg=text_fg,
        relief="flat",
        bd=0,
        highlightthickness=0,
        insertbackground=text_fg,
        justify="center",
        exportselection=False,
    )
    # Entry 叠在 Canvas 之上，可正常选中与编辑
    ent.place(relx=0.5, rely=0.5, anchor="center", width=px_w - 14, height=height - 8)
    wrap._rounded_canvas = canvas  # noqa: SLF001
    return wrap, ent


class ZephieMinimalCheck(tk.Frame):
    """对齐 ``zephie.py``：简约圆角方块 + 纯白对勾（PIL 超采样，Canvas 兜底）。"""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        *,
        variable: tk.BooleanVar | None = None,
        default: bool = True,
        command: Callable[..., Any] | None = None,
        bg: str | None = None,
        fg: str | None = None,
        font: tuple | None = None,
        **kwargs: Any,
    ) -> None:
        parent_bg = bg or parent.cget("bg")
        super().__init__(parent, bg=parent_bg, highlightthickness=0, **kwargs)
        self.var = variable if variable is not None else tk.BooleanVar(value=default)
        self._command = command
        self._enabled = True
        self._hover = False
        self._fg = fg or COLORS["text"]
        self._font = font or cute_font(9, bold=False)
        self._icons: dict[str, ImageTk.PhotoImage] = {}
        self._init_icons()

        self._cv = tk.Canvas(
            self,
            width=18,
            height=18,
            bg=parent_bg,
            highlightthickness=0,
            cursor="hand2",
        )
        self._cv.pack(side="left", padx=(0, 8), pady=2)
        self._lbl = tk.Label(
            self,
            text=text,
            bg=parent_bg,
            fg=self._fg,
            font=self._font,
            cursor="hand2",
            anchor="w",
        )
        self._lbl.pack(side="left", fill="x", expand=True)

        for widget in (self._cv, self._lbl):
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        try:
            self.var.trace_add("write", lambda *_a: self._draw())
        except tk.TclError:
            self.var.trace("w", lambda *_a: self._draw())
        self._draw()

    def _init_icons(self) -> None:
        size = 18
        scale = 4
        s = size * scale

        def create_box(
            bg_color: str, border_color: str, border_width: int, *, checked: bool
        ) -> ImageTk.PhotoImage:
            img = Image.new("RGBA", (s, s), (255, 255, 255, 0))
            draw = ImageDraw.Draw(img)
            pad = 4
            draw.rounded_rectangle(
                [pad, pad, s - pad, s - pad],
                radius=16,
                fill=bg_color,
                outline=border_color,
                width=border_width * scale,
            )
            if checked:
                pts = [(21, 36), (31, 48), (52, 24)]
                draw.line(
                    pts, fill="#FFFFFF", width=int(1.8 * scale), joint="curve"
                )
            return ImageTk.PhotoImage(img.resize((size, size), Image.Resampling.LANCZOS))

        try:
            self._icons["chk_normal"] = create_box(
                COLORS["primary"], COLORS["primary_hover"], 1, checked=True
            )
            self._icons["chk_hover"] = create_box(
                COLORS["primary_hover"], COLORS["primary_dark"], 1, checked=True
            )
            self._icons["unchk_normal"] = create_box(
                "#FFFFFF", "#D0BEE8", 1, checked=False
            )
            self._icons["unchk_hover"] = create_box(
                "#FAF7FD", COLORS["primary"], 1, checked=False
            )
            # 禁用态
            self._icons["chk_disabled"] = create_box(
                COLORS["faint"], COLORS["faint"], 1, checked=True
            )
            self._icons["unchk_disabled"] = create_box(
                COLORS["entry_bg"], COLORS["faint"], 1, checked=False
            )
        except Exception:
            self._icons.clear()

    def _checked(self) -> bool:
        try:
            return bool(self.var.get())
        except (tk.TclError, ValueError):
            return False

    def _draw(self) -> None:
        self._cv.delete("all")
        checked = self._checked()
        if self._icons:
            if not self._enabled:
                key = "chk_disabled" if checked else "unchk_disabled"
            else:
                key = ("chk_" if checked else "unchk_") + (
                    "hover" if self._hover else "normal"
                )
            self._cv.create_image(9, 9, image=self._icons[key])
            return
        # Canvas 矢量兜底（与 zephie.py 一致）
        if checked:
            bg_col = (
                COLORS["faint"]
                if not self._enabled
                else (COLORS["primary_hover"] if self._hover else COLORS["primary"])
            )
            self._cv.create_rectangle(1, 1, 17, 17, fill=bg_col, outline=bg_col, width=1)
            self._cv.create_line(
                4, 9, 8, 13, 14, 5,
                fill="#FFFFFF",
                width=2,
                capstyle="round",
                joinstyle="round",
            )
        else:
            border_col = (
                COLORS["faint"]
                if not self._enabled
                else (COLORS["primary"] if self._hover else "#D0BEE8")
            )
            self._cv.create_rectangle(
                1, 1, 17, 17, fill="#FFFFFF", outline=border_col, width=1
            )

    def _toggle(self, _e: tk.Event | None = None) -> None:
        if not self._enabled:
            return
        self.var.set(not self._checked())
        self._draw()
        if self._command:
            self._command()

    def _on_enter(self, _e: tk.Event) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._draw()

    def _on_leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._draw()

    def configure(self, cnf: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        if cnf and isinstance(cnf, dict):
            kw = {**cnf, **kw}
        elif cnf is not None and not isinstance(cnf, dict):
            return super().configure(cnf)
        state = kw.pop("state", None)
        text = kw.pop("text", None)
        if state is not None:
            self._enabled = str(state) in ("normal", "active", tk.NORMAL)
            cursor = "hand2" if self._enabled else ""
            self._cv.config(cursor=cursor)
            self._lbl.config(
                cursor=cursor,
                fg=self._fg if self._enabled else COLORS["faint"],
            )
            self._draw()
        if text is not None:
            self._lbl.config(text=str(text))
        return super().configure(**kw) if kw else None

    config = configure  # type: ignore[assignment]


AnimeCheckbutton = ZephieMinimalCheck


class ZephieSelect(tk.Frame):
    """简约圆角下拉框：左侧文案 + 右侧下拉（Canvas 绘制，与主题一致）。

    用法::

        var = tk.StringVar(value="skip_all")
        ZephieSelect(
            parent,
            "跳过感叹号格奖励:",
            values=[("跳过所有奖励", "skip_all"), ("不跳过任何奖励", "skip_none")],
            variable=var,
            command=on_change,
        )

    ``variable`` 存的是**值**（如 ``skip_all``），界面显示对应的**文案**——
    这样调用方不必再维护一份「文案 ↔ 值」映射。
    """

    _ROW_H = 26
    _RADIUS = 8

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        *,
        values: list[tuple[str, str]],
        variable: tk.StringVar | None = None,
        command: Callable[..., Any] | None = None,
        bg: str | None = None,
        fg: str | None = None,
        font: tuple | None = None,
        width: int = 168,
        height: int = 30,
        **kwargs: Any,
    ) -> None:
        parent_bg = bg or parent.cget("bg")
        super().__init__(parent, bg=parent_bg, highlightthickness=0, **kwargs)
        self._values = list(values)
        self.var = (
            variable
            if variable is not None
            else tk.StringVar(value=self._values[0][1] if self._values else "")
        )
        self._command = command
        self._enabled = True
        self._hover = False
        self._popup: tk.Toplevel | None = None
        self._fg = fg or COLORS["text"]
        self._font = font or cute_font(9, bold=False)
        self._width = int(width)
        self._height = int(height)

        self._lbl = tk.Label(
            self,
            text=text,
            bg=parent_bg,
            fg=self._fg,
            font=self._font,
            anchor="w",
            cursor="hand2",
        )
        self._lbl.pack(side="left")
        self._cv = tk.Canvas(
            self,
            width=self._width,
            height=self._height,
            bg=parent_bg,
            highlightthickness=0,
            cursor="hand2",
        )
        # 紧贴文本之后（不用 expand 撑开，否则会被推到最右侧）
        self._cv.pack(side="left", padx=(8, 0))
        # 文案与下拉框**都要**能点：只把下拉框做成热区时，用户很自然会去点文字，
        # 那时候毫无反应，看起来就像「点不动」。
        for widget in (self._lbl, self._cv):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        try:
            self.var.trace_add("write", lambda *_a: self._draw())
        except tk.TclError:
            self.var.trace("w", lambda *_a: self._draw())
        self._draw()

    # -- 取值 --

    def get(self) -> str:
        return str(self.var.get())

    def set(self, value: str) -> None:
        """选中指定值；值变化时触发 command（同值不重复触发）。"""
        value = str(value)
        changed = value != str(self.var.get())
        self.var.set(value)
        self._draw()
        if changed and self._command:
            self._command()

    def _selected_label(self) -> str:
        cur = str(self.var.get())
        for label, value in self._values:
            if value == cur:
                return label
        return self._values[0][0] if self._values else ""

    # -- 绘制 --

    def _draw(self) -> None:
        cv = self._cv
        cv.delete("all")
        w, h = self._width, self._height
        r = self._RADIUS
        points = [
            r, 1, w - r, 1, w - 1, 1, w - 1, r,
            w - 1, h - r, w - 1, h - 1, w - r, h - 1, r, h - 1,
            1, h - 1, 1, h - r, 1, r, 1, 1,
        ]
        if not self._enabled:
            fill = COLORS["entry_bg"]
        elif self._hover or self._popup is not None:
            fill = COLORS["primary_light"]
        else:
            fill = "#FFFFFF"
        cv.create_polygon(
            points,
            fill=fill,
            outline=COLORS["border"],
            width=1.2,
            smooth=True,
        )
        cv.create_text(
            10,
            h // 2,
            text=self._selected_label(),
            anchor="w",
            fill=self._fg if self._enabled else COLORS["faint"],
            font=self._font,
            width=max(20, w - 34),
        )
        cv.create_text(
            w - 13,
            h // 2,
            text="▾",
            anchor="center",
            fill=COLORS["muted"] if self._enabled else COLORS["faint"],
            font=self._font,
        )

    # -- 下拉 --

    def _popup_alive(self) -> bool:
        """弹层是否还活着。

        override-redirect 窗口不受 Tk 生命周期保护，可能被窗口管理器直接销毁，
        而 ``_popup`` 仍指向它。这种情况必须复位，否则每次点击都只走 ``_close()``，
        表现就是「下拉框点不开」。
        """
        if self._popup is None:
            return False
        try:
            if bool(self._popup.winfo_exists()):
                return True
        except tk.TclError:
            pass
        self._popup = None
        return False

    def _on_click(self, _e: tk.Event | None = None) -> None:
        if not self._enabled:
            return
        if self._popup_alive():
            self._close()
            return
        self._open()

    def _open(self) -> None:
        if not self._values or self._popup_alive():
            return
        top = tk.Toplevel(self)
        self._popup = top
        top.overrideredirect(True)
        top.configure(bg=COLORS["border"])
        try:
            top.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.update_idletasks()
        x = self._cv.winfo_rootx()
        y = self._cv.winfo_rooty() + self._cv.winfo_height()

        lb = tk.Listbox(
            top,
            bg="#FFFFFF",
            fg=self._fg,
            # 比正文略大一号：Listbox 行高由字体决定，9pt 时只有约 17px，太挤
            font=cute_font(10, bold=False),
            # 高度必须显式设为选项个数：Listbox 默认 10 行，不设就会留一大片空白
            height=max(1, len(self._values)),
            selectmode=tk.BROWSE,
            activestyle="none",
            highlightthickness=0,
            bd=0,
            relief="flat",
            # 选中不变色（只靠关闭状态的显示值来表达当前选择）；悬停才变色。
            # 因此把选中色设成与普通行一致，避免 Listbox 原生高亮干扰。
            selectbackground="#FFFFFF",
            selectforeground=self._fg,
        )
        cur = str(self.var.get())
        for i, (label, value) in enumerate(self._values):
            lb.insert(tk.END, label)
            if value == cur:
                lb.selection_set(i)
                lb.see(i)
        lb.pack(fill="both", expand=True, padx=2, pady=2)

        # 弹层高度必须按 Listbox 的**实测**高度算：它的行高由字体决定，用
        # 假定的常量算会多出空白（9pt 时每行只有 17px，而非 26px）。
        top.update_idletasks()
        box_h = lb.winfo_reqheight() + 4
        # 下方放不下就翻到上方，避免列表跑到屏幕外
        try:
            if y + box_h > self.winfo_screenheight():
                y = max(0, self._cv.winfo_rooty() - box_h)
        except tk.TclError:
            pass
        top.geometry(f"{self._width}x{box_h}+{x}+{y}")

        # 逐行悬停变色。Listbox 没有原生的行悬停，只能自己按鼠标位置刷该项底色；
        # 离开/移动时把上一行还原（选中行还原成选中色，否则失去选中反馈）。
        hover = {"idx": -1}

        def _on_motion(e: tk.Event) -> None:
            idx = int(lb.nearest(int(e.y)))
            if idx == hover["idx"]:
                return
            self._paint_row(lb, hover["idx"], hovered=False)
            hover["idx"] = idx
            self._paint_row(lb, idx, hovered=True)

        def _on_list_leave(_e: tk.Event) -> None:
            self._paint_row(lb, hover["idx"], hovered=False)
            hover["idx"] = -1

        lb.bind("<Motion>", _on_motion)
        lb.bind("<Leave>", _on_list_leave)

        def _pick(_e: tk.Event | None = None) -> None:
            sel = lb.curselection()
            if sel:
                self.set(self._values[int(sel[0])][1])
            self._close()

        lb.bind("<ButtonRelease-1>", _pick)
        lb.bind("<Return>", _pick)
        # grab 会把外部点击也送到本窗口，这里据此关闭（否则点别处关不掉）
        top.bind("<Button-1>", lambda e: self._close() if e.widget is top else None)
        top.bind("<Escape>", lambda _e: self._close())
        # 故意**不**绑 <FocusOut>：override-redirect 窗口上的焦点事件很不可靠，
        # lb.focus_set() 或窗口管理器的一次焦点抖动都会立刻触发关闭，表现就是
        # 「点一下就闪没了 / 点不动」。点外部关闭已由上面的 grab + Button-1 覆盖。
        try:
            top.grab_set()
        except tk.TclError:
            pass
        try:
            top.deiconify()
            top.lift()
        except tk.TclError:
            pass
        lb.focus_set()

    def _paint_row(self, lb: tk.Listbox, idx: int, *, hovered: bool) -> None:
        """给弹层某一行上色：悬停变色，其余一律白底。

        选中行**不变色**（当前选择由关闭状态显示的文字表达）。

        单独成方法而非内联闭包，是为了能直接测（合成鼠标事件在这里不可靠：
        Tk 把指针事件发给指针所在窗口，不一定是被调用的控件）。
        """
        if idx < 0:
            return
        if hovered:
            bg, fg = COLORS["primary_light"], COLORS["primary_dark"]
        else:
            bg, fg = "#FFFFFF", self._fg
        try:
            lb.itemconfig(idx, background=bg, foreground=fg)
        except tk.TclError:
            pass

    def _close(self) -> None:
        top = self._popup
        self._popup = None
        if top is None:
            self._draw()
            return
        try:
            top.grab_release()
        except tk.TclError:
            pass
        try:
            top.destroy()
        except tk.TclError:
            pass
        self._draw()

    # -- 悬停 --

    def _on_enter(self, _e: tk.Event) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._draw()

    def _on_leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._draw()

    # -- 兼容 RoundedButton / ZephieMinimalCheck 的 config 用法 --

    def configure(self, cnf: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        if cnf and isinstance(cnf, dict):
            kw = {**cnf, **kw}
        elif cnf is not None and not isinstance(cnf, dict):
            return super().configure(cnf)
        state = kw.pop("state", None)
        text = kw.pop("text", None)
        if state is not None:
            self._enabled = str(state) in ("normal", "active", tk.NORMAL)
            self._cv.config(cursor="hand2" if self._enabled else "")
            self._lbl.config(fg=self._fg if self._enabled else COLORS["faint"])
            if not self._enabled:
                self._close()
            self._draw()
        if text is not None:
            self._lbl.config(text=str(text))
        return super().configure(**kw) if kw else None

    config = configure  # type: ignore[assignment]



def cute_font(size: int, *, bold: bool = True) -> tuple:
    """优先圆体；缺省回退雅黑。须在已有 Tk 根窗口后调用以枚举字体。"""
    # 中文优先圆体/雅黑；Comic Sans 仅作西文回退（排在雅黑后，避免中文界面误选）
    preferred = (
        "YouYuan",
        "幼圆",
        "华文圆体",
        "STCaiyun",
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Comic Sans MS",
        "Segoe UI",
    )
    try:
        available = {n.lower() for n in tkfont.families()}
    except (tk.TclError, RuntimeError):
        available = set()
    name = "Microsoft YaHei UI"
    for cand in preferred:
        if cand.lower() in available:
            name = cand
            break
    return (name, size, "bold") if bold else (name, size)


def make_ui() -> dict:
    """颜色 + 字体字典（在 ``tk.Tk()`` 之后调用）。"""
    ui = dict(COLORS)
    ui["font_ui"] = cute_font(9, bold=False)
    ui["font_title"] = cute_font(13)
    ui["font_section"] = cute_font(9)
    ui["font_btn"] = cute_font(10)
    ui["font_sub"] = cute_font(9, bold=False)
    ui["font_name"] = cute_font(13)
    ui["font_body"] = cute_font(9, bold=False)
    ui["font_small"] = cute_font(8, bold=False)
    ui["font_mono"] = cute_font(10, bold=False)
    ui["font_done"] = cute_font(11)
    ui["font_pct"] = cute_font(28)
    return ui


def apply_zehpie_window_icon(win: tk.Misc) -> ImageTk.PhotoImage | None:
    """设置窗口图标；``iconphoto(True, …)`` 同时作为后续子窗默认图标。

    返回的 PhotoImage 必须由调用方保存引用，否则会被 GC 掉变回羽毛笔。
    """
    if not WINDOW_ICON_PATH.is_file():
        return None
    try:
        im = Image.open(WINDOW_ICON_PATH).convert("RGB")
        photo = ImageTk.PhotoImage(im, master=win)
        win.iconphoto(True, photo)
        return photo
    except Exception:
        return None
