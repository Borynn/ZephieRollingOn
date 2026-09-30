"""Decision-model picker dialog (scan ``decision_models/``, import)."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

from zephie_rolling_on.decision_models import (
    DecisionModelDisplayInfo,
    DecisionModelImportResult,
    DiscoveredModel,
    discover_decision_models,
    ensure_models_dir,
    models_dir,
)
from zephie_rolling_on.ui.model_import_progress import ModelImportProgressDialog
from zephie_rolling_on.ui.zehpie_theme import apply_zehpie_window_icon, make_ui

# 同时可见约 2 张卡片的高度
_CARD_OUTER_H = 118
_LIST_VISIBLE_CARDS = 2
_LIST_HEIGHT = _CARD_OUTER_H * _LIST_VISIBLE_CARDS + 8


class ModelPickerDialog(tk.Toplevel):
    """模型选择窗口。确认后调用所选模型的 ``import_model`` 并关闭。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_imported: Callable[[DiscoveredModel, DecisionModelImportResult], None]
        | None = None,
        on_log: Callable[[str], None] | None = None,
        models_path: Any = None,
    ) -> None:
        super().__init__(master)
        self._ui = make_ui()
        self.title("选择决策模型")
        self.configure(bg=self._ui["bg"])
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
        self._on_imported = on_imported
        self._on_log = on_log or (lambda _m: None)
        self._models_path = models_path
        self._models: list[DiscoveredModel] = []
        self._selected_id: str | None = None
        self._card_frames: dict[str, tk.Frame] = {}
        self._radio_canvas: dict[str, tk.Canvas] = {}
        self._importing = False
        self._icon_photo = apply_zehpie_window_icon(self)

        self._build()
        self._reload_models()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())

        self.update_idletasks()
        self._center_on_master(master)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()
        # 再定位一次：focus/grab 类调用可能让窗口管理器丢掉先前请求的位置。
        self._center_on_master(master)

    def _center_on_master(self, master: tk.Misc) -> None:
        """屏幕居中。见 ``ui_geometry.center_on_screen``。

        方法名保留（调用点不变），但已改为屏幕居中：主窗最小化时 ``rootx/rooty``
        为 -32000，按 master 居中的弹窗会落到屏幕外，而它已 ``grab_set()``，用户
        既看不到弹窗又点不动界面。
        """
        from zephie_rolling_on.ui.ui_geometry import center_on_screen

        center_on_screen(self, y_divisor=2)

    def _build(self) -> None:
        ui = self._ui
        pad = tk.Frame(self, bg=ui["bg"], padx=20, pady=16)
        pad.pack(fill="both", expand=True)

        header = tk.Frame(pad, bg=ui["bg"])
        header.pack(fill="x")
        tk.Label(
            header,
            text="模型选择",
            anchor="w",
            bg=ui["bg"],
            fg=ui["text"],
            font=ui["font_title"],
        ).pack(side="left")
        close = tk.Label(
            header,
            text="✕",
            bg=ui["bg"],
            fg=ui["muted"],
            font=ui["font_body"],
            cursor="hand2",
            padx=6,
        )
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self._on_cancel())

        tk.Label(
            pad,
            text="请选择协助判断游戏下一步的决策模型。",
            anchor="w",
            bg=ui["bg"],
            fg=ui["muted"],
            font=ui["font_sub"],
        ).pack(fill="x", pady=(6, 12))

        list_wrap = tk.Frame(pad, bg=ui["bg"])
        list_wrap.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(
            list_wrap,
            height=_LIST_HEIGHT,
            width=420,
            bg=ui["bg"],
            highlightthickness=0,
            bd=0,
        )
        self._scrollbar = tk.Scrollbar(
            list_wrap, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar.pack(side="right", fill="y")

        self._list_inner = tk.Frame(self._canvas, bg=ui["bg"])
        self._list_window = self._canvas.create_window(
            (0, 0), window=self._list_inner, anchor="nw"
        )
        self._list_inner.bind("<Configure>", self._on_list_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<Enter>", lambda _e: self._canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self._canvas.bind("<Leave>", lambda _e: self._canvas.unbind_all("<MouseWheel>"))

        self._empty_label = tk.Label(
            self._list_inner,
            text="",
            justify="left",
            anchor="nw",
            bg=ui["bg"],
            fg=ui["muted"],
            font=ui["font_body"],
            wraplength=400,
        )

        tk.Label(
            pad,
            text="参考值因设备与局面而异；参数由各模型封装返回。",
            anchor="w",
            bg=ui["bg"],
            fg=ui["faint"],
            font=ui["font_small"],
        ).pack(fill="x", pady=(10, 8))

        foot = tk.Frame(pad, bg=ui["bg"])
        foot.pack(fill="x")
        self._path_hint = tk.Label(
            foot,
            text="",
            anchor="w",
            bg=ui["bg"],
            fg=ui["faint"],
            font=ui["font_small"],
        )
        self._path_hint.pack(side="left", fill="x", expand=True)

        btns = tk.Frame(foot, bg=ui["bg"])
        btns.pack(side="right")
        self._btn_cancel = tk.Button(
            btns,
            text="取消",
            command=self._on_cancel,
            relief="flat",
            bd=0,
            padx=16,
            pady=6,
            bg=ui["card"],
            fg=ui["text"],
            activebackground=ui["border"],
            font=ui["font_body"],
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=ui["border"],
            highlightcolor=ui["border"],
        )
        self._btn_cancel.pack(side="right", padx=(8, 0))
        self._btn_confirm = tk.Button(
            btns,
            text="确认并导入",
            command=self._on_confirm,
            relief="flat",
            bd=0,
            padx=16,
            pady=6,
            bg=ui["accent"],
            fg="#FFFFFF",
            activebackground=ui["accent_dark"],
            activeforeground="#FFFFFF",
            font=ui["font_section"],
            cursor="hand2",
        )
        self._btn_confirm.pack(side="right")

    def _on_list_configure(self, _event: tk.Event | None = None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._list_window, width=event.width)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if self._canvas.winfo_exists():
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _reload_models(self) -> None:
        ui = self._ui
        ensure_models_dir(self._models_path)
        self._models = discover_decision_models(self._models_path)
        for child in self._list_inner.winfo_children():
            child.destroy()
        self._card_frames.clear()
        self._radio_canvas.clear()
        self._selected_id = None
        root = models_dir(self._models_path)
        self._path_hint.config(text=str(root))
        if not self._models:
            self._empty_label = tk.Label(
                self._list_inner,
                text=(
                    f"未发现决策模型。\n请将模型包放到：\n{root}"
                ),
                justify="left",
                anchor="nw",
                bg=ui["bg"],
                fg=ui["muted"],
                font=ui["font_body"],
                wraplength=400,
            )
            self._empty_label.pack(fill="x", pady=8)
            self._btn_confirm.config(state="disabled")
            return
        self._btn_confirm.config(state="normal")
        for m in self._models:
            self._add_card(m)
        if self._models:
            self._select(self._models[0].display.model_id)

    def _add_card(self, model: DiscoveredModel) -> None:
        ui = self._ui
        info = model.display
        outer = tk.Frame(self._list_inner, bg=ui["bg"], pady=4)
        outer.pack(fill="x")
        card = tk.Frame(
            outer,
            bg=ui["card"],
            highlightthickness=2,
            highlightbackground=ui["border"],
            highlightcolor=ui["border"],
            padx=12,
            pady=10,
        )
        card.pack(fill="x")
        self._card_frames[info.model_id] = card

        row = tk.Frame(card, bg=ui["card"])
        row.pack(fill="x")
        left = tk.Frame(row, bg=ui["card"])
        left.pack(side="left", fill="both", expand=True)

        title_row = tk.Frame(left, bg=ui["card"])
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text=info.name,
            anchor="w",
            bg=ui["card"],
            fg=ui["text"],
            font=ui["font_name"],
        ).pack(side="left")
        if info.version:
            tk.Label(
                title_row,
                text=info.version,
                anchor="w",
                bg=ui["card"],
                fg=ui["muted"],
                font=ui["font_small"],
            ).pack(side="left", padx=(8, 0))

        if info.summary:
            tk.Label(
                left,
                text=info.summary,
                anchor="w",
                bg=ui["card"],
                fg=ui["text"],
                font=ui["font_body"],
                wraplength=320,
                justify="left",
            ).pack(fill="x", pady=(4, 0))
        for line in info.param_lines:
            tk.Label(
                left,
                text=line,
                anchor="w",
                bg=ui["card"],
                fg=ui["muted"],
                font=ui["font_small"],
            ).pack(fill="x")
        if info.status_line:
            tk.Label(
                left,
                text=info.status_line,
                anchor="w",
                bg=ui["card"],
                fg=ui["faint"],
                font=ui["font_small"],
            ).pack(fill="x", pady=(2, 0))

        radio = tk.Canvas(
            row,
            width=28,
            height=28,
            bg=ui["card"],
            highlightthickness=0,
            bd=0,
        )
        radio.pack(side="right", padx=(8, 0))
        self._radio_canvas[info.model_id] = radio

        def on_click(_e: tk.Event, mid: str = info.model_id) -> None:
            self._select(mid)

        # Tk delivers a click to the innermost widget only, so bind the whole
        # subtree — otherwise clicks on any text Label (most of the card) do nothing.
        def _bind_click(widget: tk.Misc) -> None:
            widget.bind("<Button-1>", on_click)
            try:
                widget.configure(cursor="hand2")
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                _bind_click(child)

        _bind_click(card)

    def _draw_radio(self, canvas: tk.Canvas, *, selected: bool) -> None:
        ui = self._ui
        canvas.delete("all")
        canvas.create_oval(
            6, 6, 22, 22,
            outline=ui["accent"] if selected else ui["radio_empty"],
            width=2,
        )
        if selected:
            canvas.create_oval(10, 10, 18, 18, fill=ui["accent"], outline=ui["accent"])

    def _select(self, model_id: str) -> None:
        ui = self._ui
        self._selected_id = model_id
        for mid, card in self._card_frames.items():
            selected = mid == model_id
            try:
                card.configure(
                    highlightbackground=ui["border_sel"] if selected else ui["border"],
                    highlightcolor=ui["border_sel"] if selected else ui["border"],
                )
                if selected:
                    card.configure(bg=ui["accent_soft"])
                    for child in card.winfo_children():
                        self._set_bg_recursive(child, ui["accent_soft"])
                else:
                    card.configure(bg=ui["card"])
                    for child in card.winfo_children():
                        self._set_bg_recursive(child, ui["card"])
                radio = self._radio_canvas.get(mid)
                if radio is not None:
                    radio.configure(bg=ui["accent_soft"] if selected else ui["card"])
                    self._draw_radio(radio, selected=selected)
            except tk.TclError:
                pass

    def _set_bg_recursive(self, widget: tk.Misc, color: str) -> None:
        try:
            if isinstance(widget, (tk.Frame, tk.Label, tk.Canvas)):
                widget.configure(bg=color)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_bg_recursive(child, color)

    def _selected_model(self) -> DiscoveredModel | None:
        if not self._selected_id:
            return None
        for m in self._models:
            if m.display.model_id == self._selected_id:
                return m
        return None

    def _on_confirm(self) -> None:
        if self._importing:
            return
        model = self._selected_model()
        if model is None:
            self._on_log("[决策模型] 请先选择一个模型")
            return
        self._importing = True
        self._btn_confirm.config(state="disabled")
        self._btn_cancel.config(state="disabled")

        def on_finished(m: DiscoveredModel, result: DecisionModelImportResult) -> None:
            self._importing = False
            if self._on_imported is not None:
                self._on_imported(m, result)
            self.destroy()

        def on_failed(_m: DiscoveredModel, msg: str) -> None:
            self._importing = False
            self._btn_confirm.config(state="normal")
            self._btn_cancel.config(state="normal")
            _ = msg
            self._on_log("[决策模型] 导入失败")

        ModelImportProgressDialog(
            self,
            model,
            on_finished=on_finished,
            on_failed=on_failed,
            on_log=self._on_log,
        )

    def _on_cancel(self) -> None:
        if self._importing:
            return
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def open_model_picker(
    master: tk.Misc,
    *,
    on_imported: Callable[[DiscoveredModel, DecisionModelImportResult], None]
    | None = None,
    on_log: Callable[[str], None] | None = None,
    models_path: Any = None,
) -> ModelPickerDialog:
    return ModelPickerDialog(
        master,
        on_imported=on_imported,
        on_log=on_log,
        models_path=models_path,
    )
