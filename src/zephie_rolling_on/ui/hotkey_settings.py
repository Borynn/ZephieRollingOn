"""Hotkey settings panel."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

from zephie_rolling_on.ui.hotkey_config import (
    ACTION_LABELS,
    ALL_ACTIONS,
    DEFAULT_HOTKEYS,
    HotkeyBinding,
    binding_from_string,
    binding_from_tk_event,
    load_hotkey_bindings,
    save_hotkey_bindings,
    validate_bindings,
)
from zephie_rolling_on.ui.ui_geometry import apply_saved_geometry, remember_window_geometry
from zephie_rolling_on.ui.zehpie_theme import RoundedButton, apply_zehpie_window_icon, make_ui

_GEO_KEY = "hotkey"
_GEO_DEFAULT = "460x380"


class HotkeySettingsDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        *,
        bindings: dict[str, HotkeyBinding] | None = None,
        on_saved: Callable[[dict[str, HotkeyBinding]], list[str]] | None = None,
        on_open: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._ui = make_ui()
        self.title("快捷键设置")
        apply_saved_geometry(self, _GEO_KEY, default=_GEO_DEFAULT)
        self.transient(master)
        self.resizable(False, False)
        self.configure(bg=self._ui["bg"])
        self._icon = apply_zehpie_window_icon(self)
        self._on_saved = on_saved
        self._on_open = on_open
        self._on_close = on_close
        self._bindings = dict(bindings) if bindings is not None else load_hotkey_bindings()
        self._label_vars: dict[str, tk.StringVar] = {}
        self._capture_action: str | None = None
        if self._on_open:
            self._on_open()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<KeyPress>", self._on_key_press, add="+")

    def _card(self, parent: tk.Misc, *, padx: int = 14, pady: int = 10) -> tk.Frame:
        c = self._ui
        wrap = tk.Frame(
            parent,
            bg=c["card"],
            highlightbackground=c["border"],
            highlightthickness=1,
            padx=padx,
            pady=pady,
        )
        return wrap

    def _build(self) -> None:
        c = self._ui
        tip_card = self._card(self)
        tip_card.pack(fill="x", padx=14, pady=(14, 8))
        tip_msg = (
            "点击「设置」后按下组合键；Esc 取消。\n"
            "可只按小键盘数字（显示为 Num7 等）。打开本窗口期间全局快捷键将自动暂停。\n"
            "多开时请为各界面配置不同快捷键（Windows 全局热键不能重复注册）。"
        )
        tk.Label(
            tip_card,
            text=tip_msg,
            bg=c["card"],
            fg=c["muted"],
            font=c["font_small"],
            justify="left",
            wraplength=410,
        ).pack(anchor="w")

        settings_card = self._card(self, pady=12)
        settings_card.pack(fill="both", expand=True, padx=14, pady=4)

        for action in ALL_ACTIONS:
            row = tk.Frame(settings_card, bg=c["card"])
            row.pack(fill="x", pady=6)
            tk.Label(
                row,
                text=ACTION_LABELS[action],
                bg=c["card"],
                fg=c["text"],
                font=c["font_section"],
                width=18,
                anchor="w",
            ).pack(side="left")
            var = tk.StringVar(value=self._binding_label(action))
            self._label_vars[action] = var
            tk.Label(
                row,
                textvariable=var,
                bg=c.get("primary_light", c["accent_soft"]),
                fg=c.get("primary", c["accent_dark"]),
                font=c["font_section"],
                padx=8,
                pady=3,
                width=12,
                anchor="center",
            ).pack(side="left", padx=8)
            RoundedButton(
                row,
                "设置",
                command=lambda a=action: self._begin_capture(a),
                bg_color="#FFFFFF",
                fg_color=c.get("primary", c["accent_dark"]),
                hover_bg=c.get("primary_light", c["accent_soft"]),
                border_color=c["border"],
                radius=8,
                font=c["font_small"],
                width=60,
                height=26,
            ).pack(side="left", padx=4)
            RoundedButton(
                row,
                "清除",
                command=lambda a=action: self._clear_binding(a),
                bg_color="#FFFFFF",
                fg_color=c["muted"],
                hover_bg="#F5F5F5",
                border_color=c["border"],
                radius=8,
                font=c["font_small"],
                width=50,
                height=26,
            ).pack(side="left")

        self._lbl_status = tk.Label(
            self,
            text="",
            anchor="w",
            bg=c["bg"],
            fg=c["danger"],
            font=c["font_small"],
            wraplength=420,
        )
        self._lbl_status.pack(fill="x", padx=14, pady=(4, 0))

        btn_bar = tk.Frame(self, bg=c["bg"], pady=12, padx=14)
        btn_bar.pack(fill="x", side="bottom")
        RoundedButton(
            btn_bar,
            "保存并应用",
            command=self._save,
            bg_color=c.get("primary", c["accent"]),
            fg_color="#FFFFFF",
            hover_bg=c.get("primary_hover", c["accent_dark"]),
            border_color=c.get("primary", c["accent"]),
            radius=10,
            font=c["font_btn"],
            width=95,
            height=30,
        ).pack(side="left")
        RoundedButton(
            btn_bar,
            "恢复默认",
            command=self._restore_defaults,
            bg_color=c.get("primary_light", c["accent_soft"]),
            fg_color=c.get("primary", c["accent_dark"]),
            hover_bg=c.get("accent_hover", "#EBDDF8"),
            border_color=c["border"],
            radius=10,
            font=c["font_btn"],
            width=85,
            height=30,
        ).pack(side="left", padx=8)
        RoundedButton(
            btn_bar,
            "关闭",
            command=self._close,
            bg_color="#FFFFFF",
            fg_color=c["muted"],
            hover_bg="#F5F5F5",
            border_color=c["border"],
            radius=10,
            font=c["font_btn"],
            width=70,
            height=30,
        ).pack(side="right")

    def _binding_label(self, action: str) -> str:
        b = self._bindings.get(action)
        if b is None:
            return "（未设置）"
        return b.label

    def _begin_capture(self, action: str) -> None:
        c = self._ui
        self._capture_action = action
        self._lbl_status.config(
            text=f"请为「{ACTION_LABELS[action]}」按下快捷键…（Esc 取消）",
            fg=c.get("primary", c["accent_dark"]),
        )
        self.focus_force()
        self.grab_set()

    def _cancel_capture(self) -> None:
        self._capture_action = None
        self._lbl_status.config(text="", fg=self._ui["danger"])
        try:
            self.grab_release()
        except tk.TclError:
            pass

    def _on_key_press(self, event: Any) -> str | None:
        if self._capture_action is None:
            return "break"
        if event.keysym == "Escape":
            self._cancel_capture()
            return "break"
        binding = binding_from_tk_event(self._capture_action, event)
        if binding is None:
            return "break"
        action = self._capture_action
        self._bindings[action] = binding
        self._label_vars[action].set(binding.label)
        self._cancel_capture()
        self._lbl_status.config(
            text=f"已设置 {ACTION_LABELS[action]} → {binding.label}",
            fg=self._ui["mint"],
        )
        return "break"

    def _clear_binding(self, action: str) -> None:
        self._bindings[action] = HotkeyBinding(
            action=action, enabled=False, modifiers=(), key=""
        )
        self._label_vars[action].set("（未设置）")
        self._lbl_status.config(
            text=f"已清除 {ACTION_LABELS[action]} 的快捷键", fg=self._ui["muted"]
        )

    def _restore_defaults(self) -> None:
        for action in ALL_ACTIONS:
            text = DEFAULT_HOTKEYS.get(action, "")
            self._bindings[action] = binding_from_string(action, text)
            self._label_vars[action].set(self._bindings[action].label)
        self._lbl_status.config(
            text="已恢复默认快捷键（尚未保存）", fg=self._ui["muted"]
        )

    def _save(self) -> None:
        errors = validate_bindings(self._bindings)
        if errors:
            self._lbl_status.config(text="；".join(errors), fg=self._ui["danger"])
            return
        apply_errors: list[str] = []
        if self._on_saved:
            # 由调用方写入 RuntimeState（多开不抢写共享 yaml）
            apply_errors = self._on_saved(self._bindings) or []
        else:
            save_hotkey_bindings(self._bindings)
        if apply_errors:
            self._lbl_status.config(
                text="已保存，但部分快捷键注册失败：" + "；".join(apply_errors),
                fg=self._ui["danger"],
            )
        else:
            self._lbl_status.config(
                text="快捷键已保存并应用（仅本界面）。", fg=self._ui["mint"]
            )

    def _close(self) -> None:
        remember_window_geometry(self, _GEO_KEY)
        self._cancel_capture()
        if self._on_close:
            self._on_close()
        self.destroy()


def open_hotkey_settings(
    master: tk.Misc,
    *,
    bindings: dict[str, HotkeyBinding] | None = None,
    on_saved: Callable[[dict[str, HotkeyBinding]], list[str]] | None = None,
    on_open: Callable[[], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> HotkeySettingsDialog:
    return HotkeySettingsDialog(
        master,
        bindings=bindings,
        on_saved=on_saved,
        on_open=on_open,
        on_close=on_close,
    )
