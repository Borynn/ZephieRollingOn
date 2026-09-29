"""Planner calibration window (drawn-card checklist)."""

from __future__ import annotations

import tkinter as tk

from zephie_rolling_on.app.runtime_state import RuntimeState, use_runtime
from zephie_rolling_on.models.lucky_deck import (
    PlannerCalibration,
    deck_card_labels_by_section,
    load_planner_calibration,
    save_planner_calibration,
)
from zephie_rolling_on.ui.ui_geometry import apply_saved_geometry, remember_window_geometry
from zephie_rolling_on.ui.zehpie_theme import (
    apply_zehpie_window_icon,
    make_rounded_entry,
    make_ui,
)

_GEO_KEY = "calibration"
_GEO_DEFAULT = "420x480"


class PlannerCalibrationDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        *,
        on_saved=None,
        runtime: RuntimeState | None = None,
    ) -> None:
        super().__init__(master)
        self._ui = make_ui()
        self.title("详细卡池 — 已抽清单")
        apply_saved_geometry(self, _GEO_KEY, default=_GEO_DEFAULT)
        self.configure(bg=self._ui["bg"])
        self.transient(master)
        self._on_saved = on_saved
        self._runtime = runtime
        self._icon_photo = apply_zehpie_window_icon(self)
        with self._rt():
            self._cal = load_planner_calibration()
        self._spin_vars: dict[str, tk.StringVar] = {}
        self._caps: dict[str, int] = {}
        self._canvas: tk.Canvas | None = None
        self._build()
        self._bind_mousewheel()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _rt(self):
        if self._runtime is not None:
            return use_runtime(self._runtime)
        from contextlib import nullcontext

        return nullcontext()

    def _build(self) -> None:
        ui = self._ui
        top = tk.Frame(self, bg=ui["bg"], padx=12, pady=10)
        top.pack(fill="x")

        tk.Label(
            top,
            text="当前格子、剩余骰子由 OCR 自动识别（自动点击时更新），此处仅维护已抽清单。",
            wraplength=380,
            fg=ui["muted"],
            bg=ui["bg"],
            font=ui["font_body"],
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        self._lbl_pool = tk.Label(
            top,
            text="",
            anchor="w",
            fg=ui["accent_dark"],
            bg=ui["bg"],
            font=ui["font_section"],
        )
        self._lbl_pool.pack(fill="x")

        tk.Label(
            top,
            text="本组已抽走的张数（与游戏内卡池一致；抽满 30 张后游戏会重置池子）",
            wraplength=380,
            fg=ui["muted"],
            bg=ui["bg"],
            font=ui["font_body"],
            justify="left",
        ).pack(anchor="w", pady=(4, 4))

        canvas_frame = tk.Frame(self, bg=ui["bg"])
        canvas_frame.pack(fill="both", expand=True, padx=12, pady=4)
        canvas = tk.Canvas(
            canvas_frame,
            highlightthickness=0,
            bg=ui["card"],
            bd=0,
        )
        self._canvas = canvas
        # 与日志同款 tk 简约滚动条；颜色保持 Zehpie 原紫系
        scrollbar = tk.Scrollbar(
            canvas_frame,
            orient="vertical",
            command=canvas.yview,
            bg=ui["accent"],
            troughcolor=ui["accent_soft"],
            activebackground=ui["accent_dark"],
            highlightthickness=0,
            bd=0,
            width=12,
            relief="flat",
            elementborderwidth=0,
        )
        inner = tk.Frame(canvas, bg=ui["card"])
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        drawn = self._cal.normalized_drawn()
        for section_title, cards in deck_card_labels_by_section():
            tk.Label(
                inner,
                text=section_title,
                anchor="w",
                font=ui["font_section"],
                fg=ui["accent_dark"],
                bg=ui["card"],
            ).pack(fill="x", pady=(8, 2))
            for card_id, label, cap in cards:
                row = tk.Frame(inner, bg=ui["card"])
                row.pack(fill="x", pady=2)
                tk.Label(
                    row,
                    text=f"{label}（最多{cap}）",
                    width=16,
                    anchor="w",
                    bg=ui["card"],
                    fg=ui["text"],
                    font=ui["font_body"],
                ).pack(side="left")
                var = tk.StringVar(value=str(int(drawn.get(card_id, 0))))
                var.trace_add("write", lambda *_: self._refresh_pool_label())
                self._spin_vars[card_id] = var
                self._caps[card_id] = int(cap)
                wrap, ent = make_rounded_entry(
                    row,
                    textvariable=var,
                    width=4,
                    font=ui["font_body"],
                    bg=ui.get("entry_bg", "#FAF8FD"),
                    fg=ui["text"],
                    border=ui["border"],
                    height=28,
                    radius=12,
                )
                wrap.pack(side="left", padx=(4, 0))
                ent.bind(
                    "<FocusOut>",
                    lambda _e, cid=card_id: self._clamp_card(cid),
                )
                ent.bind(
                    "<Return>",
                    lambda _e, cid=card_id: self._clamp_card(cid),
                )

        btn_row = tk.Frame(self, bg=ui["bg"], padx=12, pady=10)
        btn_row.pack(fill="x")
        tk.Button(
            btn_row,
            text="保存",
            command=self._save,
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            bg=ui["accent"],
            fg="#FFFFFF",
            activebackground=ui["accent_dark"],
            activeforeground="#FFFFFF",
            font=ui["font_section"],
            cursor="hand2",
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            btn_row,
            text="本组清零（满池）",
            command=self._reset_pool,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            bg=ui["accent_soft"],
            fg=ui["text"],
            activebackground=ui["border"],
            font=ui["font_body"],
            cursor="hand2",
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            btn_row,
            text="关闭",
            command=self._close,
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            bg=ui["card"],
            fg=ui["text"],
            activebackground=ui["border"],
            font=ui["font_body"],
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=ui["border"],
            highlightcolor=ui["border"],
        ).pack(side="right")

        self._refresh_pool_label()

    def _clamp_card(self, card_id: str) -> None:
        var = self._spin_vars.get(card_id)
        if var is None:
            return
        cap = int(self._caps.get(card_id, 99))
        raw = (var.get() or "").strip()
        try:
            n = int(float(raw)) if raw else 0
        except (TypeError, ValueError):
            n = 0
        n = max(0, min(cap, n))
        if raw != str(n):
            var.set(str(n))

    def _bind_mousewheel(self) -> None:
        def on_mousewheel(event: tk.Event) -> None:
            canvas = self._canvas
            if canvas is None or not canvas.winfo_exists():
                return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.bind_all("<MouseWheel>", on_mousewheel)

    def _unbind_mousewheel(self) -> None:
        try:
            self.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass

    def _collect_drawn(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for cid, var in self._spin_vars.items():
            cap = int(self._caps.get(cid, 99))
            raw = (var.get() or "").strip()
            try:
                n = int(float(raw)) if raw else 0
            except (TypeError, ValueError):
                n = 0
            out[cid] = max(0, min(cap, n))
        return out

    def sync_from_file(self) -> None:
        """OCR 或自动点击更新校准后刷新剩骰/卡组摘要（不改动输入框）。"""
        with self._rt():
            self._cal = load_planner_calibration()
        self._refresh_pool_label()

    def _refresh_pool_label(self) -> None:
        try:
            with self._rt():
                cal = load_planner_calibration()
            preview = PlannerCalibration(
                dice_remaining=cal.dice_remaining,
                drawn_counts=self._collect_drawn(),
            )
            preview.drawn_counts = preview.normalized_drawn()
            self._lbl_pool.config(
                text=f"{preview.summary_line()}（骰子为 OCR 缓存）",
            )
        except (tk.TclError, TypeError, ValueError):
            pass

    def _reset_pool(self) -> None:
        for var in self._spin_vars.values():
            var.set("0")
        self._refresh_pool_label()

    def _save(self) -> None:
        drawn = self._collect_drawn()
        with self._rt():
            cal = load_planner_calibration()
            updated = PlannerCalibration(
                dice_remaining=int(cal.dice_remaining),
                drawn_counts=drawn,
            )
            updated.drawn_counts = updated.normalized_drawn()
            if updated.is_pool_exhausted():
                updated.reset_drawn_pool()
            self._cal = updated
            path = save_planner_calibration(updated)
            final_drawn = updated.normalized_drawn()
        for cid, var in self._spin_vars.items():
            try:
                var.set(str(int(final_drawn.get(cid, 0))))
            except tk.TclError:
                pass
        if self._on_saved:
            self._on_saved(self._cal, path)
        self._refresh_pool_label()

    def _close(self) -> None:
        remember_window_geometry(self, _GEO_KEY)
        self._unbind_mousewheel()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def open_planner_calibration(
    master: tk.Misc,
    *,
    on_saved=None,
    runtime: RuntimeState | None = None,
) -> PlannerCalibrationDialog:
    return PlannerCalibrationDialog(master, on_saved=on_saved, runtime=runtime)
