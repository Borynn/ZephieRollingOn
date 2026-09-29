"""拖到游戏窗口松手以绑定 hwnd（状态栏内嵌图标）。"""

from __future__ import annotations

from zephie_rolling_on.paths import project_root

from collections.abc import Callable
from pathlib import Path

import tkinter as tk

from zephie_rolling_on.vision.win32_window import WindowInfo, pick_window_at_point, register_tool_hwnd

PROJECT_ROOT = project_root()
_ICON_PATH = PROJECT_ROOT / "assets" / "ui" / "bind_window_icon.png"
# 拖影窗用近黑键色做透明；图标本身勿含该色
_TRANSPARENT_KEY = "#010101"


class DraggableWindowPicker:
    """状态栏可拖动图标：按住拖到游戏画面松开即可绑定。"""

    def __init__(
        self,
        on_picked: Callable[[WindowInfo], None],
        on_pick_failed: Callable[[str], None] | None = None,
        *,
        exclude_hwnds: list[int] | None = None,
    ) -> None:
        self._on_picked = on_picked
        self._on_pick_failed = on_pick_failed or (lambda _m: None)
        self._exclude = set(exclude_hwnds or [])
        self._photo: tk.PhotoImage | None = None
        self._btn: tk.Label | None = None
        self._ghost: tk.Toplevel | None = None
        self._dragging = False
        self._press_root: tk.Misc | None = None

    def add_exclude_hwnd(self, hwnd: int) -> None:
        self._exclude.add(int(hwnd))

    def mount(self, parent: tk.Misc, *, bg: str) -> tk.Label:
        """在 ``parent`` 右侧放绑定图标（保持 PhotoImage 引用防 GC）。"""
        if not _ICON_PATH.is_file():
            raise FileNotFoundError(f"缺少绑定图标: {_ICON_PATH}")
        self._photo = tk.PhotoImage(file=str(_ICON_PATH))
        self._press_root = parent.winfo_toplevel()
        btn = tk.Label(
            parent,
            image=self._photo,
            bg=bg,
            cursor="hand2",
            bd=0,
            highlightthickness=0,
            takefocus=0,
        )
        btn.pack(side="right", padx=(6, 0))
        btn.bind("<ButtonPress-1>", self._on_press)
        btn.bind("<B1-Motion>", self._on_motion)
        btn.bind("<ButtonRelease-1>", self._on_release)
        self._btn = btn
        return btn

    def _on_press(self, event: tk.Event) -> None:
        self._dragging = True
        self._ensure_ghost(event.x_root, event.y_root)
        if self._ghost is not None:
            try:
                self._ghost.grab_set()
            except tk.TclError:
                pass

    def _on_motion(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        self._ensure_ghost(event.x_root, event.y_root)
        self._move_ghost(event.x_root, event.y_root)

    def _on_release(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        x_root, y_root = int(event.x_root), int(event.y_root)
        ghost = self._ghost
        if ghost is not None:
            try:
                ghost.grab_release()
            except tk.TclError:
                pass
            try:
                self._exclude.add(int(ghost.winfo_id()))
            except tk.TclError:
                pass
            try:
                ghost.withdraw()
                ghost.update_idletasks()
            except tk.TclError:
                pass
        # 稍延迟再拾取，避免仍命中拖影窗
        host = self._press_root
        if host is not None:
            host.after(120, lambda: self._finish_pick(x_root, y_root))
        else:
            self._finish_pick(x_root, y_root)

    def _ensure_ghost(self, x_root: int, y_root: int) -> None:
        if self._ghost is not None:
            try:
                if self._ghost.winfo_exists():
                    self._ghost.deiconify()
                    self._move_ghost(x_root, y_root)
                    return
            except tk.TclError:
                self._ghost = None
        if self._photo is None:
            return
        ghost = tk.Toplevel()
        register_tool_hwnd(int(ghost.winfo_id()))
        ghost.overrideredirect(True)
        ghost.attributes("-topmost", True)
        ghost.configure(bg=_TRANSPARENT_KEY)
        try:
            ghost.attributes("-transparentcolor", _TRANSPARENT_KEY)
        except tk.TclError:
            pass
        lbl = tk.Label(
            ghost,
            image=self._photo,
            bg=_TRANSPARENT_KEY,
            bd=0,
            highlightthickness=0,
        )
        lbl.pack()
        # 拖影也跟着鼠标动
        lbl.bind("<B1-Motion>", self._on_motion)
        lbl.bind("<ButtonRelease-1>", self._on_release)
        self._ghost = ghost
        self._exclude.add(int(ghost.winfo_id()))
        self._move_ghost(x_root, y_root)

    def _move_ghost(self, x_root: int, y_root: int) -> None:
        if self._ghost is None or self._photo is None:
            return
        try:
            w = int(self._photo.width())
            h = int(self._photo.height())
            self._ghost.geometry(f"+{int(x_root) - w // 2}+{int(y_root) - h // 2}")
        except tk.TclError:
            pass

    def _finish_pick(self, x_root: int, y_root: int) -> None:
        info = pick_window_at_point(x_root, y_root, exclude_hwnds=self._exclude)
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except tk.TclError:
                pass
            self._ghost = None
        if info is not None:
            self._on_picked(info)
            return
        self._on_pick_failed(
            "绑定失败：未识别到足够大的游戏窗口。"
            "请把图标拖到游戏画面内再松开。"
        )

    def destroy(self) -> None:
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except tk.TclError:
                pass
            self._ghost = None
        self._btn = None
        self._photo = None
