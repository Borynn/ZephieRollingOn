"""Decision-model import progress dialog."""

from __future__ import annotations

import io
import math
import queue
import threading
import tkinter as tk
from collections.abc import Callable

from PIL import Image, ImageTk

from zephie_rolling_on.decision_models.types import (
    DecisionModelImportResult,
    DiscoveredModel,
)
from zephie_rolling_on.ui.zehpie_theme import (
    COLORS as _UI,
    apply_zehpie_window_icon,
    cute_font,
)

class ModelImportProgressDialog(tk.Toplevel):
    """后台调用 ``package.import_model(on_progress=...)``，完成后等用户点「完成」。"""

    def __init__(
        self,
        master: tk.Misc,
        model: DiscoveredModel,
        *,
        on_finished: Callable[[DiscoveredModel, DecisionModelImportResult], None]
        | None = None,
        on_failed: Callable[[DiscoveredModel, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        preview_pct: int | None = None,
    ) -> None:
        super().__init__(master)
        self.title("模型导入")
        self.configure(bg=_UI["bg"])
        # 先还原最小化的主窗，否则本窗口按 master 坐标居中会落到屏幕外
        # （最小化时 rootx/rooty 为 -32000），而它随后 grab_set 会让界面不可点。
        # 主窗刚由最小化还原时不设 transient：否则窗口会被 withdraw（不可见）。
        from zephie_rolling_on.ui.ui_geometry import (
            ensure_master_visible,
            should_set_transient,
        )

        restored = ensure_master_visible(master)
        if should_set_transient(master, restored=restored):
            self.transient(master)
        self.resizable(False, False)
        self._model = model
        self._on_finished = on_finished
        self._on_failed = on_failed
        self._on_log = on_log or (lambda _m: None)
        self._master = master
        # 两个布局各自需要的窗口宽度，_build() 里量出来；见 _refit()
        self._win_width = 0
        # Import stickers come from the package itself when it carries them.
        self._art_busy_bytes = self._read_embedded_art("busy")
        self._art_done_bytes = self._read_embedded_art("done")
        self._queue: queue.Queue = queue.Queue()
        self._result: DecisionModelImportResult | None = None
        self._done_ok = False
        self._closed = False
        self._pct = int(preview_pct) if preview_pct is not None else 0
        self._ring_angle = 0.0
        self._photos: list[ImageTk.PhotoImage] = []
        self._anim_job: str | None = None
        self._preview_mode = preview_pct is not None

        self._font_title = cute_font(18)
        self._font_pct = cute_font(28)
        self._font_sub = cute_font(11)
        self._font_btn = cute_font(10)

        icon = apply_zehpie_window_icon(self)
        if icon is not None:
            self._photos.append(icon)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_user_close)
        self.after(50, self._poll_queue)
        self.after(40, self._tick_ring)
        if self._preview_mode:
            self._pct_label.config(text=f"{self._pct}%")
            self._draw_ring()
        self.update_idletasks()
        self._refit(master)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        # 再定位一次：focus/grab 类调用可能让窗口管理器丢掉先前请求的位置。
        self._refit(master)

        if not self._preview_mode:
            threading.Thread(target=self._worker, name="dm-import", daemon=True).start()

    def _refit(self, master: tk.Misc | None = None) -> None:
        """按当前布局重新定尺寸并居中。

        **必须显式设 geometry**：顶层窗口一旦映射，就不会再因为换了内容而自动变大
        （``resizable(False, False)`` 下尤其明显）。原来「完成」布局比「导入中」宽时
        文字会被裁掉——实测 ``zephie_m1_points`` 需要 550px 而窗口停在 460px，
        标签只分到 188px 却需要 272px。

        宽度统一取 ``_win_width``（两个布局的较大者），这样切换状态时窗口不左右跳。
        """
        self.update_idletasks()
        width = max(int(self._win_width), int(self.winfo_reqwidth()))
        height = int(self.winfo_reqheight())
        self.geometry(f"{width}x{height}")
        self._center(master if master is not None else self._master)

    def _center(self, master: tk.Misc) -> None:
        """屏幕居中偏上。见 ``ui_geometry.center_on_screen``。

        用屏幕坐标而非 ``master`` 坐标：主窗最小化时 ``rootx/rooty`` 为 -32000，
        按 master 居中的弹窗会落到屏幕外，而它已 ``grab_set()``，用户既看不到
        弹窗又点不动界面。``master`` 参数保留仅为签名兼容。
        """
        from zephie_rolling_on.ui.ui_geometry import center_on_screen

        center_on_screen(self)

    def _read_embedded_art(self, which: str) -> bytes | None:
        pkg = self._model.package
        getter = getattr(pkg, "import_art_png", None)
        if callable(getter):
            return getter(which)
        return None

    def _load_art_bytes(
        self, data: bytes | None, height: int = 210
    ) -> ImageTk.PhotoImage | None:
        """Load sticker from PNG bytes; flatten alpha onto card color for Tk."""
        if not data:
            return None
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        if im.height != height:
            w = max(1, int(round(im.width * (height / im.height))))
            im = im.resize((w, height), Image.Resampling.LANCZOS)
        hex_bg = _UI["card"].lstrip("#")
        rgb = tuple(int(hex_bg[i : i + 2], 16) for i in (0, 2, 4))
        base = Image.new("RGBA", im.size, rgb + (255,))
        base.alpha_composite(im)
        photo = ImageTk.PhotoImage(base.convert("RGB"), master=self)
        self._photos.append(photo)
        return photo

    def _build(self) -> None:
        shell = tk.Frame(self, bg=_UI["bg"], padx=16, pady=14)
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

        body = tk.Frame(card, bg=_UI["card"])
        body.pack(fill="both", expand=True)

        # —— 导入中 ——
        self._busy = tk.Frame(body, bg=_UI["card"])
        self._busy.pack(fill="both", expand=True)

        left_b = tk.Frame(self._busy, bg=_UI["card"])
        left_b.pack(side="left", fill="y", padx=(4, 8))
        photo_b = self._load_art_bytes(self._art_busy_bytes)
        if photo_b is not None:
            tk.Label(left_b, image=photo_b, bg=_UI["card"], bd=0).pack()
        else:
            tk.Label(
                left_b, text="Zehpie", bg=_UI["card"], fg=_UI["accent"], font=self._font_title
            ).pack()

        right_b = tk.Frame(self._busy, bg=_UI["card"])
        right_b.pack(side="left", fill="both", expand=True, padx=(8, 4))

        ring_box = tk.Frame(right_b, bg=_UI["card"])
        ring_box.pack(expand=True)
        self._ring_size = 148
        self._ring = tk.Canvas(
            ring_box,
            width=self._ring_size,
            height=self._ring_size,
            bg=_UI["card"],
            highlightthickness=0,
            bd=0,
        )
        self._ring.pack()
        self._pct_label = tk.Label(
            ring_box,
            text="0%",
            bg=_UI["card"],
            fg=_UI["accent_deep"],
            font=self._font_pct,
        )
        self._pct_label.place(
            in_=self._ring, relx=0.5, rely=0.5, anchor="center"
        )

        self._busy_caption = tk.Label(
            right_b,
            text="Zehpie正在努力导入模型",
            bg=_UI["card"],
            fg=_UI["text"],
            font=self._font_sub,
        )
        self._busy_caption.pack(pady=(4, 8))

        # —— 完成 ——
        self._done = tk.Frame(body, bg=_UI["card"])
        # not packed until success

        left_d = tk.Frame(self._done, bg=_UI["card"])
        left_d.pack(side="left", fill="y", padx=(4, 8))
        photo_d = self._load_art_bytes(self._art_done_bytes)
        if photo_d is not None:
            tk.Label(left_d, image=photo_d, bg=_UI["card"], bd=0).pack()

        right_d = tk.Frame(self._done, bg=_UI["card"])
        right_d.pack(side="left", fill="both", expand=True, padx=(8, 4))

        name = self._model.display.name
        self._done_label = tk.Label(
            right_d,
            text=f"{name}导入完成！",
            bg=_UI["card"],
            fg=_UI["accent_deep"],
            font=self._font_title,
            # 放宽到 460：280 时稍长的模型名（如 zephie_m1_points）会提前折行，
            # 而折行后的可用宽度又不够，反而更容易被裁。
            wraplength=460,
            justify="left",
        )
        self._done_label.pack(expand=True, anchor="w", pady=(40, 12))

        self._btn_done = tk.Button(
            right_d,
            text="完成",
            command=self._on_done_click,
            relief="flat",
            bd=0,
            padx=26,
            pady=8,
            bg=_UI["accent"],
            fg="#FFFFFF",
            activebackground=_UI["accent_deep"],
            activeforeground="#FFFFFF",
            font=self._font_btn,
            cursor="hand2",
            state="disabled",
        )
        self._btn_done.pack(anchor="e", pady=(8, 4))

        # 失败时仍用 busy 布局，按钮挂在 card 底
        self._fail_reason = tk.Label(
            card,
            text="",
            anchor="w",
            justify="left",
            bg=_UI["card"],
            fg="#C9786A",
            font=self._font_sub,
            wraplength=320,
        )
        self._btn_fail = tk.Button(
            card,
            text="关闭",
            command=self._on_done_click,
            relief="flat",
            bd=0,
            padx=22,
            pady=6,
            bg=_UI["pink"],
            fg="#FFFFFF",
            activebackground="#E889A8",
            activeforeground="#FFFFFF",
            font=self._font_btn,
            cursor="hand2",
        )
        # 默认隐藏失败按钮
        self._draw_ring()

        # 量出两个布局各自需要的宽度，取较大者作为窗口宽度。
        # 顶层窗口映射后不会再自动变大，所以必须**提前**知道最宽的那个需求，
        # 否则切到「完成」时右侧文字会被裁掉。
        self.update_idletasks()
        w_busy = int(self.winfo_reqwidth())
        self._done.pack(fill="both", expand=True)
        self.update_idletasks()
        w_done = int(self.winfo_reqwidth())
        self._done.pack_forget()
        self._busy.pack(fill="both", expand=True)
        self.update_idletasks()
        self._win_width = max(w_busy, w_done)

    def _draw_ring(self) -> None:
        c = self._ring
        c.delete("all")
        s = self._ring_size
        pad = 10
        x0, y0, x1, y1 = pad, pad, s - pad, s - pad
        # 底轨
        c.create_oval(x0, y0, x1, y1, outline=_UI["ring_track"], width=10)
        pct = max(0, min(100, int(self._pct)))
        extent = -max(4.0, pct * 3.6)  # clockwise-ish from top
        # 动态高光弧：随角度旋转的渐变色段
        colors = (_UI["mint"], _UI["sky"], _UI["accent"], _UI["pink"])
        if pct <= 0:
            # 空闲时转小段弧
            span = 56
            start = 90 - self._ring_angle
            c.create_arc(
                x0,
                y0,
                x1,
                y1,
                start=start,
                extent=-span,
                style="arc",
                outline=colors[int(self._ring_angle / 40) % len(colors)],
                width=10,
            )
        else:
            # 主进度弧
            c.create_arc(
                x0,
                y0,
                x1,
                y1,
                start=90,
                extent=extent,
                style="arc",
                outline=_UI["accent"],
                width=10,
            )
            # 旋转装饰弧（前端亮色）
            tip = 90 + extent
            c.create_arc(
                x0,
                y0,
                x1,
                y1,
                start=tip - self._ring_angle * 0.15,
                extent=-28,
                style="arc",
                outline=colors[int(self._ring_angle / 30) % len(colors)],
                width=12,
            )
        # 外圈淡点缀
        for i in range(8):
            ang = math.radians(self._ring_angle + i * 45)
            cx = s / 2 + math.cos(ang) * (s / 2 - 4)
            cy = s / 2 + math.sin(ang) * (s / 2 - 4)
            r = 2.2
            c.create_oval(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                fill=colors[i % len(colors)],
                outline="",
            )

    def _tick_ring(self) -> None:
        if self._closed or self._result is not None:
            return
        self._ring_angle = (self._ring_angle + 8) % 360
        self._draw_ring()
        self._anim_job = self.after(40, self._tick_ring)

    def _show_done_ui(self, *, ok: bool, reason: str = "") -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except tk.TclError:
                pass
            self._anim_job = None
        self._busy.pack_forget()
        if ok:
            # 撤下失败态的残留控件：否则它们的尺寸会算进窗口高度（实测切回后
            # 窗口仍停在失败态的 359px，而不是完成态的 266px）。
            self._fail_reason.pack_forget()
            self._btn_fail.pack_forget()
            self._done.pack(fill="both", expand=True)
            self._btn_done.config(state="normal")
        else:
            self._busy.pack(fill="both", expand=True)
            self._busy_caption.config(text="导入失败了…", fg="#C9786A")
            # 顺序要紧：``before=self._btn_fail`` 要求该按钮**已经 pack**，
            # 先 pack 原因再 pack 按钮会抛 "isn't packed"，失败原因根本显示不出来。
            self._btn_fail.pack(anchor="e", pady=(6, 0))
            if reason:
                # 把失败原因写进界面，否则用户只能看到"导入失败"而无法排查
                self._fail_reason.config(text=reason, wraplength=420)
                self._fail_reason.pack(anchor="w", pady=(4, 0), before=self._btn_fail)
        # 换了布局就重新定尺寸：窗口不会自己变大，不重算就会被裁。
        self._refit()

    def _worker(self) -> None:
        def on_progress(pct: int, msg: str) -> None:
            self._queue.put(("progress", int(pct), msg))

        try:
            result = self._model.package.import_model(on_progress=on_progress)
        except Exception as exc:
            result = DecisionModelImportResult(ok=False, message=str(exc))
        self._queue.put(("result", result))

    def _poll_queue(self) -> None:
        if self._closed:
            return
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, pct, _msg = item
                    self._pct = max(0, min(100, pct))
                    self._pct_label.config(text=f"{self._pct}%")
                    self._draw_ring()
                elif kind == "result":
                    self._on_import_result(item[1])
        except queue.Empty:
            pass
        if not self._closed:
            self.after(50, self._poll_queue)

    def _on_import_result(self, result: DecisionModelImportResult) -> None:
        self._result = result
        name = self._model.display.name
        if not result.ok:
            self._pct = 0
            reason = (result.message or "").strip() or "未知原因"
            self._show_done_ui(ok=False, reason=reason)
            self._done_ok = False
            self._on_log(f"[决策模型] {name}导入失败：{reason}")
            return
        self._pct = 100
        self._pct_label.config(text="100%")
        self._draw_ring()
        self._done_label.config(text=f"{name}导入完成！")
        self._show_done_ui(ok=True)
        self._done_ok = True
        # 成功日志由控制面板 on_imported 统一输出「[决策模型] ***已就绪」

    def _on_done_click(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except tk.TclError:
                pass
            self._anim_job = None
        result = self._result or DecisionModelImportResult(ok=False, message="无结果")
        if self._done_ok and result.ok:
            if self._on_finished is not None:
                try:
                    self._on_finished(self._model, result)
                except Exception as exc:
                    self._on_log(f"[决策模型] 完成回调异常：{exc}")
        else:
            if self._on_failed is not None:
                self._on_failed(self._model, result.message or "导入失败")
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _on_user_close(self) -> None:
        # 导入中禁止叉掉；完成后等同「完成/关闭」
        if self._result is None:
            return
        self._on_done_click()
