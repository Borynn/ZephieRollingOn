from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass

from zephie_rolling_on.vision.win32_window import register_tool_hwnd

# 大冒险小游戏框尺寸（物理像素，与客户区检测坐标同一坐标系）
FRAME_WIDTH = 1248
FRAME_HEIGHT = 766

_BAR_GAP = 6  # 确认栏与框之间的缝隙


@dataclass(frozen=True)
class FrameAnchor:
    """标定结果：框的物理屏幕位置 + 换算到游戏客户区的左上角锚点。"""

    screen_left: int
    screen_top: int
    width: int
    height: int
    client_left: int | None  # 相对游戏窗口客户区左上角（与 regions.yaml 同坐标系）
    client_top: int | None


class AdventureFrameLocator:
    """可拖动的固定尺寸覆盖框：拖到与大冒险界面重合后点确认，得到客户区锚点。

    用法与 DraggableWindowPicker 类似：构造时给 on_confirm 回调与 get_game_hwnd
    取数器；show() 显示框 + 确认栏，确认时把框的物理屏幕矩形换算为游戏客户区坐标。
    """

    def __init__(
        self,
        on_confirm: Callable[[FrameAnchor], None],
        get_game_hwnd: Callable[[], int | None],
        *,
        on_cancel: Callable[[], None] | None = None,
        width: int = FRAME_WIDTH,
        height: int = FRAME_HEIGHT,
    ) -> None:
        if sys.platform != "win32":
            raise OSError("界面标定仅支持 Windows（需要窗口句柄换算）")
        self._on_confirm = on_confirm
        self._get_game_hwnd = get_game_hwnd
        self._on_cancel = on_cancel or (lambda: None)
        self._w = int(width)
        self._h = int(height)
        self._frame: tk.Toplevel | None = None
        self._bar: tk.Toplevel | None = None
        self._coord_var: tk.StringVar | None = None
        self._drag_x = 0
        self._drag_y = 0
        self._title_hint: str | None = None

    # ---- 显示 / 关闭 ----
    def show(
        self,
        *,
        client_left: int | None = None,
        client_top: int | None = None,
        title_hint: str | None = None,
    ) -> None:
        """显示红框。若给出客户区坐标，则把框放到该锚点（自动标定预览）。"""
        self._title_hint = title_hint
        if self._frame is not None:
            if client_left is not None and client_top is not None:
                self._place_at_client(int(client_left), int(client_top))
            self._frame.deiconify()
            self._frame.lift()
            if self._bar is not None:
                self._bar.deiconify()
                self._bar.lift()
            self._refresh_coords()
            return

        if client_left is not None and client_top is not None:
            x0, y0 = self._client_to_screen_top_left(int(client_left), int(client_top))
        else:
            x0, y0 = self._initial_top_left()

        frame = tk.Toplevel()
        frame.overrideredirect(True)
        frame.attributes("-topmost", True)
        frame.attributes("-alpha", 0.35)
        frame.configure(bg="#000000")
        frame.geometry(f"{self._w}x{self._h}+{x0}+{y0}")
        register_tool_hwnd(int(frame.winfo_id()))

        canvas = tk.Canvas(
            frame, width=self._w, height=self._h, highlightthickness=0, bg="#103060", cursor="fleur"
        )
        canvas.pack(fill="both", expand=True)
        # 红色边框 + 中心十字，便于与大冒险界面对齐
        canvas.create_rectangle(2, 2, self._w - 2, self._h - 2, outline="#ff3030", width=4)
        canvas.create_line(self._w // 2, 0, self._w // 2, self._h, fill="#ff3030", width=1)
        canvas.create_line(0, self._h // 2, self._w, self._h // 2, fill="#ff3030", width=1)
        canvas.create_text(
            self._w // 2,
            self._h // 2 - 14,
            text=f"{self._w} x {self._h}",
            fill="#ffffff",
            font=("Consolas", 16, "bold"),
        )
        hint = title_hint or "拖动本框与大冒险界面对齐"
        canvas.create_text(
            self._w // 2,
            self._h // 2 + 14,
            text=hint,
            fill="#ffffff",
            font=("", 12),
        )
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_motion)

        self._frame = frame
        self._force_physical_rect(x0, y0)

        self._build_bar(auto_preview=bool(title_hint))
        self._reposition_bar()
        self._refresh_coords()

    def _client_to_screen_top_left(self, client_left: int, client_top: int) -> tuple[int, int]:
        hwnd = self._get_game_hwnd()
        if hwnd:
            try:
                import win32gui

                return win32gui.ClientToScreen(int(hwnd), (int(client_left), int(client_top)))
            except Exception:
                pass
        return self._initial_top_left()

    def _force_physical_rect(self, screen_left: int, screen_top: int) -> None:
        """用 win32 强制框为精确物理像素位置+尺寸（避免 Tk geometry 的 DPI/阴影偏差）。"""
        if self._frame is None:
            return
        self._frame.update_idletasks()
        try:
            import win32con
            import win32gui

            hwnd = int(self._frame.winfo_id())
            # Tk 有时 winfo_id 是子句柄，外框才是可 SetWindowPos 的顶层
            try:
                root = int(win32gui.GetAncestor(hwnd, 2))  # GA_ROOT
                if root:
                    hwnd = root
            except Exception:
                pass
            flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            win32gui.SetWindowPos(
                hwnd, 0, int(screen_left), int(screen_top), self._w, self._h, flags
            )
        except Exception:
            self._frame.geometry(
                f"{self._w}x{self._h}+{int(screen_left)}+{int(screen_top)}"
            )

    def _force_physical_size(self) -> None:
        """用 win32 强制框为精确物理像素尺寸，规避 DPI 缩放误差。"""
        if self._frame is None:
            return
        self._frame.update_idletasks()
        try:
            import win32con
            import win32gui

            hwnd = int(self._frame.winfo_id())
            try:
                root = int(win32gui.GetAncestor(hwnd, 2))
                if root:
                    hwnd = root
            except Exception:
                pass
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            win32gui.SetWindowPos(hwnd, 0, 0, 0, self._w, self._h, flags)
        except Exception:
            pass

    def _place_at_client(self, client_left: int, client_top: int) -> None:
        if self._frame is None:
            return
        x0, y0 = self._client_to_screen_top_left(client_left, client_top)
        self._force_physical_rect(x0, y0)
        self._reposition_bar()
        self._refresh_coords()

    def _build_bar(self, *, auto_preview: bool = False) -> None:
        bar = tk.Toplevel()
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        bar.configure(bg="#222222")
        register_tool_hwnd(int(bar.winfo_id()))

        wrap = tk.Frame(bar, bg="#222222", padx=8, pady=6)
        wrap.pack()
        prompt = (
            "自动标定预览：红框应与大冒险界面重合。可拖微调后点「确认」覆盖锚点，或点「关闭」。"
            if auto_preview
            else "把红框拖到与大冒险界面完全重合后点「确认」："
        )
        tk.Label(
            wrap,
            text=prompt,
            bg="#222222",
            fg="#ffffff",
        ).pack(side="left", padx=(0, 8))
        self._coord_var = tk.StringVar(value="")
        tk.Label(
            wrap,
            textvariable=self._coord_var,
            bg="#222222",
            fg="#7fd1ff",
            font=("Consolas", 9),
        ).pack(side="left", padx=(0, 8))
        tk.Button(wrap, text="确认", width=8, bg="#2e7d32", fg="white", command=self._confirm).pack(
            side="left", padx=(0, 6)
        )
        cancel_label = "关闭" if auto_preview else "取消"
        tk.Button(wrap, text=cancel_label, width=8, command=self._cancel).pack(side="left")

        # 也允许拖动这一栏来移动整组（抓住标签区域）
        for w in (bar, wrap):
            w.bind("<ButtonPress-1>", self._on_press_bar)
            w.bind("<B1-Motion>", self._on_motion_bar)
        self._bar = bar

    # ---- 拖动 ----
    def _on_press(self, event: tk.Event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_motion(self, event: tk.Event) -> None:
        if self._frame is None:
            return
        x = self._frame.winfo_x() + event.x - self._drag_x
        y = self._frame.winfo_y() + event.y - self._drag_y
        self._frame.geometry(f"+{x}+{y}")
        self._reposition_bar()
        self._refresh_coords()

    def _on_press_bar(self, event: tk.Event) -> None:
        self._drag_x = event.x_root
        self._drag_y = event.y_root

    def _on_motion_bar(self, event: tk.Event) -> None:
        if self._frame is None:
            return
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        self._drag_x = event.x_root
        self._drag_y = event.y_root
        x = self._frame.winfo_x() + dx
        y = self._frame.winfo_y() + dy
        self._frame.geometry(f"+{x}+{y}")
        self._reposition_bar()
        self._refresh_coords()

    def _reposition_bar(self) -> None:
        if self._frame is None or self._bar is None:
            return
        self._bar.update_idletasks()
        fx = self._frame.winfo_x()
        fy = self._frame.winfo_y()
        bh = self._bar.winfo_height() or 40
        by = fy - bh - _BAR_GAP
        if by < 0:  # 顶部空间不足则放到框下方
            by = fy + self._h + _BAR_GAP
        self._bar.geometry(f"+{max(0, fx)}+{by}")
        self._bar.lift()

    # ---- 尺寸/坐标换算 ----
    def _force_physical_size(self) -> None:
        """用 win32 强制框为精确物理像素尺寸，规避 DPI 缩放误差。"""
        if self._frame is None:
            return
        self._frame.update_idletasks()
        try:
            import win32con
            import win32gui

            hwnd = int(self._frame.winfo_id())
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            win32gui.SetWindowPos(hwnd, 0, 0, 0, self._w, self._h, flags)
        except Exception:
            pass

    def _frame_screen_rect(self) -> tuple[int, int, int, int]:
        """框的物理屏幕矩形 (left, top, width, height)。"""
        if self._frame is None:
            return 0, 0, self._w, self._h
        try:
            import win32gui

            l, t, r, b = win32gui.GetWindowRect(int(self._frame.winfo_id()))
            return l, t, r - l, b - t
        except Exception:
            self._frame.update_idletasks()
            return self._frame.winfo_x(), self._frame.winfo_y(), self._w, self._h

    def _current_anchor(self) -> FrameAnchor:
        sl, st, w, h = self._frame_screen_rect()
        cl: int | None = None
        ct: int | None = None
        hwnd = self._get_game_hwnd()
        if hwnd:
            try:
                import win32gui

                cl, ct = win32gui.ScreenToClient(int(hwnd), (sl, st))
            except Exception:
                cl, ct = None, None
        return FrameAnchor(
            screen_left=sl, screen_top=st, width=w, height=h, client_left=cl, client_top=ct
        )

    def _refresh_coords(self) -> None:
        if self._coord_var is None:
            return
        a = self._current_anchor()
        if a.client_left is not None:
            self._coord_var.set(
                f"客户区 ({a.client_left},{a.client_top}) | 屏幕 ({a.screen_left},{a.screen_top}) | {a.width}x{a.height}"
            )
        else:
            self._coord_var.set(
                f"屏幕 ({a.screen_left},{a.screen_top}) | {a.width}x{a.height} | 未绑定游戏窗口"
            )

    # ---- 确认 / 取消 ----
    def _confirm(self) -> None:
        anchor = self._current_anchor()
        self._on_confirm(anchor)
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()
        self._on_cancel()

    def destroy(self) -> None:
        for win in (self._bar, self._frame):
            if win is not None:
                try:
                    win.destroy()
                except tk.TclError:
                    pass
        self._frame = None
        self._bar = None
        self._coord_var = None

    # ---- 初始位置：尽量居中到游戏客户区 ----
    def _initial_top_left(self) -> tuple[int, int]:
        hwnd = self._get_game_hwnd()
        if hwnd:
            try:
                import win32gui

                from zephie_rolling_on.vision.win32_window import _client_size_screen_pixels

                x0, y0, cw, ch = _client_size_screen_pixels(int(hwnd))
                if cw > 0 and ch > 0:
                    return x0 + max(0, (cw - self._w) // 2), y0 + max(0, (ch - self._h) // 2)
            except Exception:
                pass
        # 回退：屏幕中心
        try:
            import win32api

            sw = win32api.GetSystemMetrics(0)
            sh = win32api.GetSystemMetrics(1)
            return max(0, (sw - self._w) // 2), max(0, (sh - self._h) // 2)
        except Exception:
            return 80, 80
