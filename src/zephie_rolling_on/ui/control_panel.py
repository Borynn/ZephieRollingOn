from __future__ import annotations

from zephie_rolling_on.paths import project_root

import datetime
import queue
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import scrolledtext

import yaml

from zephie_rolling_on.app.runtime_state import RuntimeState, use_runtime
from zephie_rolling_on.app.session import ScriptSession
from zephie_rolling_on.app.tick import run_tick
from zephie_rolling_on.app.deck_pool_scan import scan_and_apply_deck_pool
from zephie_rolling_on.data.map_loader import load_map_from_xlsx
from zephie_rolling_on.data.adventure_frame import load_anchor, save_anchor
from zephie_rolling_on.data.auto_click_config import (
    load_skip_animation_via_f12,
    load_skip_exclamation_reward,
    save_auto_replenish_dice,
    save_click_interval_ms,
    save_skip_animation_via_f12,
    save_skip_exclamation_reward,
)
from zephie_rolling_on.executor.mouse_shield import get_mouse_shield
from zephie_rolling_on.ui.frame_locator import AdventureFrameLocator, FrameAnchor
from zephie_rolling_on.ui.hotkey_config import (
    ACTION_AUTO_START,
    ACTION_AUTO_STOP,
    ACTION_BIND_WINDOW,
    HotkeyBinding,
    load_hotkey_bindings,
    save_hotkey_bindings,
)
from zephie_rolling_on.ui.hotkey_listener import GlobalHotkeyManager
from zephie_rolling_on.ui.hotkey_settings import open_hotkey_settings
from zephie_rolling_on.ui.model_picker import open_model_picker
from zephie_rolling_on.ui.notice_dialog import guard_system_scale, show_blocking_notice
from zephie_rolling_on.ui.ui_geometry import apply_saved_geometry, remember_window_geometry
from zephie_rolling_on.ui.zehpie_theme import (
    RoundedButton,
    ZephieMinimalCheck,
    apply_zehpie_window_icon,
    draw_dashed_divider,
    make_rounded_entry,
    make_ui,
)
from zephie_rolling_on.ui.window_picker import DraggableWindowPicker
from zephie_rolling_on.vision.cursor_coords import get_cursor_client_pos
from zephie_rolling_on.vision.ocr_preload import (
    preload_recognition_engines,
    recognition_preload_log_lines,
    recognition_preload_status,
)
from zephie_rolling_on.models.lucky_card import format_owned_lucky_cards
from zephie_rolling_on.models.lucky_deck import (
    DECK_TOTAL,
    apply_ocr_dice_to_calibration,
    load_planner_calibration,
    sync_deck_from_new_hand_cards,
)
from zephie_rolling_on.ui.planner_calibration import open_planner_calibration
from zephie_rolling_on.vision.capture import capture_window_client
from zephie_rolling_on.vision.roll_kind import save_roll_kind_debug
from zephie_rolling_on.vision.adventure_bootstrap import run_adventure_bootstrap
from zephie_rolling_on.vision.inventory_dice import (
    recognize_charge_dice,
    recognize_inventory_dice,
    save_charge_dice_debug,
    save_inventory_dice_debug,
)
from zephie_rolling_on.executor.dice_replenish import run_dice_detect_and_replenish
from zephie_rolling_on.decision_models import (
    DecisionModelImportResult,
    DiscoveredModel,
)
from zephie_rolling_on.decision_models.runtime import (
    active_model_ready,
    set_active_decision_model,
)
from zephie_rolling_on.vision.recognize import (
    RecognitionResult,
    load_regions,
    recognize_advice_poll,
    recognize_all_on_frame,
    recognize_dice_only,
    recognize_game,
    recognize_lucky_slot_only,
)
from zephie_rolling_on.vision.win32_window import (
    WindowInfo,
    get_window_info,
    is_valid_game_capture_size,
    pick_window_at_point,
    register_tool_hwnd,
    resolve_hwnd,
)

PROJECT_ROOT = project_root()
GAME_WINDOW_CONFIG = PROJECT_ROOT / "config" / "game_window.yaml"
DEV_CONFIG = PROJECT_ROOT / "config" / "dev.yaml"
LOG_TEXT_MAX_LINES = 3000
# Fonts filled by make_ui() after Tk() exists.
_UI: dict = {}
def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_game_window_config() -> dict:
    return _load_yaml(GAME_WINDOW_CONFIG) or {
        "hwnd": None,
        "capture_hwnd": None,
        "root_hwnd": None,
        "title": "",
    }


def _save_game_window_config(info: WindowInfo) -> None:
    """仅单实例/无 Runtime 时写盘；多开时由 ControlPanel 写入 RuntimeState。"""
    GAME_WINDOW_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with GAME_WINDOW_CONFIG.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "capture_hwnd": int(info.capture_hwnd),
                "root_hwnd": int(info.root_hwnd),
                "hwnd": int(info.capture_hwnd),
                "title": info.title,
            },
            f,
            allow_unicode=True,
        )


def _dev_show_cursor_coords() -> bool:
    return bool(_load_yaml(DEV_CONFIG).get("show_cursor_coords", False))


def _dev_preload_ocr_on_startup() -> bool:
    return bool(_load_yaml(DEV_CONFIG).get("preload_ocr_on_startup", True))


def _dev_preload_engine_flags() -> tuple[bool, bool]:
    """返回 (preload_rapidocr, preload_easyocr)，默认 Rapid 开、Easy 关。"""
    cfg = _load_yaml(DEV_CONFIG)
    rapid = bool(cfg.get("preload_rapidocr_on_startup", True))
    easy = bool(cfg.get("preload_easyocr_on_startup", False))
    return rapid, easy


def _dev_one_click_test_save_debug() -> bool:
    return bool(_load_yaml(DEV_CONFIG).get("one_click_test_save_debug", False))


def _format_recognition_detail(
    dice_rec: RecognitionResult,
    cell_rec: RecognitionResult,
    lucky_rec: RecognitionResult,
) -> list[str]:
    """识别结果的可读明细（一键测试用）。"""
    lines = [
        f"  格子：{cell_rec.cell_id}"
        + (f"（原文 {cell_rec.raw_cell_text!r}）" if cell_rec.raw_cell_text else ""),
        f"  骰子：剩余 {dice_rec.dice_remaining}"
        + (f"（原文 {dice_rec.raw_dice_text!r}）" if dice_rec.raw_dice_text else ""),
        f"  幸运卡：{len(lucky_rec.lucky_card_ids)} 张"
        + (f" {lucky_rec.lucky_card_ids}" if lucky_rec.lucky_card_ids else ""),
    ]
    if lucky_rec.raw_lucky_text:
        lines.append(f"  幸运卡原文：{lucky_rec.raw_lucky_text!r}")
    for label, rec in (("格子", cell_rec), ("骰子", dice_rec), ("幸运卡", lucky_rec)):
        if rec.notes:
            lines.append(f"  {label}备注：{rec.notes}")
    return lines


class ControlPanel:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("控制面板仅支持 Windows（需要窗口句柄）")

        self.root = tk.Tk()
        global _UI
        _UI = make_ui()
        self.root.title("Zephie Rolling On!")
        apply_saved_geometry(self.root, "main", default="400x860")
        self.root.minsize(360, 720)
        self.root.configure(bg=_UI["bg"])
        # 启动即设图标；须保持 PhotoImage 引用，否则 GC 后变回羽毛笔
        self._window_icon = apply_zehpie_window_icon(self.root)
        self._geo_save_after: str | None = None
        self.root.bind("<Configure>", self._on_main_configure, add="+")

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._bound: WindowInfo | None = None
        self._picker = DraggableWindowPicker(
            self._on_window_picked,
            self._on_pick_failed,
            exclude_hwnds=[],
        )
        self._frame_locator = AdventureFrameLocator(
            on_confirm=self._on_adventure_frame_confirmed,
            get_game_hwnd=self._resolved_hwnd,
        )
        # 本界面独占的局内状态（多开互不抢写全局 yaml）
        self._pending_cell_text: str | None = None
        self._pending_lucky_text: str | None = None
        self._status_flush_after_id: str | None = None
        self._runtime = RuntimeState.bootstrap_from_disk()
        self._session = ScriptSession(
            runtime=self._runtime,
            on_log=self._enqueue_log,
            on_lucky_cards=self._update_owned_lucky_cards,
            on_cell=self._update_current_cell,
            on_calibration_changed=self._on_calibration_changed,
            on_round_progress=self._on_round_progress,
            on_auto_stopped=self._on_auto_stopped,
        )
        self._show_cursor = _dev_show_cursor_coords()
        self._hotkey = GlobalHotkeyManager()
        self._hotkey.set_handlers(
            {
                ACTION_BIND_WINDOW: self._on_bind_hotkey,
                ACTION_AUTO_START: self._on_auto_start_hotkey,
                ACTION_AUTO_STOP: self._on_auto_stop_hotkey,
            }
        )
        self._recognition_test_busy = False
        self._exporting_logs = False
        self._auto_start_preparing = False
        self._advice_busy = False
        self._advice_loop_active = False
        self._advice_poll_busy = False
        self._advice_loop_after_id: str | None = None
        self._last_cell_id: int | None = None
        self._last_hand_ids: list[str] = []
        self._calibration_dialog = None
        self._hotkey_settings_dialog = None
        self._map_path = PROJECT_ROOT / "map.xlsx"
        self._active_decision_model = None
        self._selected_model_label = "未选择"

        self._build_ui()
        self._refresh_calibration_labels()
        register_tool_hwnd(int(self.root.winfo_id()))
        self._picker.add_exclude_hwnd(int(self.root.winfo_id()))
        self._restore_bound_window()
        self._start_hotkey_listener()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_log_queue()
        self._poll_hotkeys()
        if _dev_preload_ocr_on_startup():
            rapid, easy = _dev_preload_engine_flags()
            if rapid or easy:
                self._start_ocr_preload(preload_rapid=rapid, preload_easy=easy)
        if self._show_cursor:
            self._poll_cursor_coords()

    def _start_hotkey_listener(self) -> None:
        with use_runtime(self._runtime):
            bindings = load_hotkey_bindings()
        errors = self._hotkey.start(bindings)
        self._refresh_hotkey_labels()
        if errors:
            self._append_log("快捷键监听失败：" + "；".join(errors))
            return
        if not self._hotkey.is_active():
            self._append_log("快捷键监听未启动（钩子线程未运行）。")
            return
        self._append_log("已注册全局热键")

    def _build_ui(self) -> None:
        bg = _UI["bg"]
        card = _UI["card"]
        text_c = _UI["text"]
        muted = _UI["muted"]
        accent = _UI["accent"]
        font_ui = _UI["font_ui"]
        font_mono = _UI["font_mono"]

        shell = tk.Frame(self.root, bg=bg, padx=14, pady=12)
        shell.pack(fill="both", expand=True)

        def html_card(*, padx: int = 12, pady: int = 10) -> tk.Frame:
            wrap = tk.Frame(
                shell,
                bg=card,
                highlightbackground=_UI["border"],
                highlightcolor=_UI["border"],
                highlightthickness=1,
                takefocus=0,
                padx=padx,
                pady=pady,
            )
            wrap.pack(fill="x", pady=(0, 8))
            return wrap

        def section(title: str) -> tk.Frame:
            wrap = html_card()
            tk.Label(
                wrap,
                text=title,
                anchor="w",
                bg=card,
                fg=_UI.get("primary", _UI["accent_dark"]),
                font=_UI["font_section"],
            ).pack(anchor="w", pady=(0, 6))
            return wrap

        def pack_round_btn(
            parent,
            label,
            command,
            *,
            primary=False,
            danger=False,
            pady=2,
            side=None,
            padx=0,
            fill="x",
            expand=False,
            width=None,
            height=32,
            font=None,
            radius=10,
        ):
            if primary:
                b = RoundedButton(
                    parent,
                    label,
                    command,
                    bg_color=_UI.get("primary", accent),
                    fg_color="#FFFFFF",
                    hover_bg=_UI.get("primary_hover", _UI["accent_dark"]),
                    border_color=_UI.get("primary", accent),
                    radius=radius,
                    font=font or _UI["font_btn"],
                    height=height,
                    width=width,
                )
            elif danger:
                b = RoundedButton(
                    parent,
                    label,
                    command,
                    bg_color=_UI.get("danger_bg", _UI["danger_soft"]),
                    fg_color=_UI.get("danger_text", _UI["danger"]),
                    hover_bg="#FFE4EA",
                    border_color=_UI.get("danger_border", _UI["danger"]),
                    radius=radius,
                    font=font or font_ui,
                    height=height,
                    width=width,
                )
            else:
                b = RoundedButton(
                    parent,
                    label,
                    command,
                    bg_color=_UI.get("primary_light", _UI["accent_soft"]),
                    fg_color=_UI.get("primary", text_c),
                    hover_bg=_UI.get("accent_hover", "#EBDDF8"),
                    border_color=_UI["border"],
                    radius=radius,
                    font=font or font_ui,
                    height=height,
                    width=width,
                )
            if side is None:
                pack_kw = {"pady": pady, "expand": expand}
                if fill:
                    pack_kw["fill"] = fill
                b.pack(**pack_kw)
            else:
                pack_kw = {
                    "side": side,
                    "padx": padx,
                    "pady": pady,
                    "expand": expand,
                }
                if fill:
                    pack_kw["fill"] = fill
                b.pack(**pack_kw)
            return b

        # ---------- 状态看板（图1 布局）----------
        st = html_card()
        top_hero = tk.Frame(st, bg=card)
        top_hero.pack(fill="x")

        hero_left = tk.Frame(top_hero, bg=card)
        hero_left.pack(side="left", fill="both", expand=True)

        self._pill_status = tk.Frame(
            hero_left, bg=_UI["badge_pink_bg"], padx=8, pady=3
        )
        self._pill_status.pack(anchor="w", pady=(0, 4))
        self._lbl_pill_dot = tk.Label(
            self._pill_status,
            text="●",
            bg=_UI["badge_pink_bg"],
            fg=_UI["badge_pink_fg"],
            font=_UI["font_small"],
        )
        self._lbl_pill_dot.pack(side="left", padx=(0, 4))
        self._lbl_pill_text = tk.Label(
            self._pill_status,
            text="未绑定窗口",
            bg=_UI["badge_pink_bg"],
            fg=_UI["badge_pink_fg"],
            font=_UI["font_section"],
        )
        self._lbl_pill_text.pack(side="left")
        self._lbl_window = self._lbl_pill_text

        tk.Label(
            hero_left,
            text="把Zephie拖到游戏里，她会帮助你～",
            anchor="w",
            bg=card,
            fg=muted,
            font=_UI["font_small"],
        ).pack(anchor="w", pady=(2, 4))

        hero_right = tk.Frame(top_hero, bg=card)
        hero_right.pack(side="right", padx=(6, 0))
        # 原 Zephie 绑定图标：拖到游戏窗松手
        self._picker.mount(hero_right, bg=card)

        divider = tk.Canvas(st, height=6, bg=card, highlightthickness=0)
        divider.pack(fill="x", pady=(4, 6))
        divider.bind("<Configure>", lambda e: draw_dashed_divider(e.widget, _UI["border"]))

        font_num = _UI["font_btn"]
        stats = tk.Frame(st, bg=card)
        stats.pack(fill="x")
        left_col = tk.Frame(stats, bg=card)
        left_col.pack(side="left", fill="both", expand=True)
        right_col = tk.Frame(stats, bg=card)
        right_col.pack(side="right", fill="y", padx=(10, 0))

        def _stat_row(parent: tk.Frame, label: str) -> tk.Frame:
            row = tk.Frame(parent, bg=card)
            row.pack(fill="x", pady=2, anchor="w")
            tk.Label(row, text=label, bg=card, fg=muted, font=font_ui).pack(
                side="left"
            )
            return row

        row_cell = _stat_row(left_col, "当前格子")
        self._lbl_cell = tk.Label(
            row_cell, text="—", bg=card, fg=_UI.get("primary", accent), font=font_num
        )
        self._lbl_cell.pack(side="left", padx=(12, 0))

        row_dice = _stat_row(left_col, "剩余骰子")
        self._lbl_dice = tk.Label(
            row_dice, text="—", bg=card, fg=_UI.get("primary", accent), font=font_num
        )
        self._lbl_dice.pack(side="left", padx=(12, 0))

        row_deck = _stat_row(right_col, "卡池剩余")
        self._lbl_deck = tk.Label(
            row_deck,
            text=f"—/{DECK_TOTAL}",
            bg=card,
            fg=_UI.get("primary", accent),
            font=font_num,
        )
        self._lbl_deck.pack(side="left", padx=(8, 0))

        pack_round_btn(
            right_col,
            "✦ 详细卡池",
            self._open_calibration,
            side=None,
            fill="",
            pady=(6, 0),
            height=26,
            width=96,
            radius=8,
            font=("Microsoft YaHei UI", 8),
        )

        # 幸运卡单独占一行（卡池信息与按钮下方），长文本自动换行，不挤占上方内容
        row_lucky = _stat_row(st, "幸运卡")
        self._lbl_lucky = tk.Label(
            row_lucky,
            text="—",
            bg=card,
            fg=_UI.get("accent_pink", _UI["warn"]),
            font=font_num,
            anchor="w",
            justify="left",
            wraplength=260,
        )
        self._lbl_lucky.pack(side="left", padx=(12, 0), fill="x", expand=True)

        # ---------- 工具（图2：仅圆角「一键测试」）----------
        tools = html_card()
        self._btn_test_all = pack_round_btn(
            tools, "一键测试", self._test_all_recognition, height=36, pady=0
        )

        if self._show_cursor:
            cur = section("坐标（开发）")
            self._lbl_cursor_client = tk.Label(
                cur,
                text="移到游戏窗口内显示坐标",
                anchor="w",
                bg=card,
                fg=accent,
                font=font_mono,
            )
            self._lbl_cursor_client.pack(fill="x")
            self._lbl_cursor_frame = tk.Label(
                cur,
                text="—",
                anchor="w",
                bg=card,
                fg=_UI["accent_dark"],
                font=("Consolas", 10, "bold"),
            )
            self._lbl_cursor_frame.pack(fill="x")

        # ---------- 决策模块（图2：标题左 / 模型名右 / 中间圆角按钮）----------
        strat = html_card()
        ai_header = tk.Frame(strat, bg=card)
        ai_header.pack(fill="x", pady=(0, 8))
        tk.Label(
            ai_header,
            text="决策模块",
            bg=card,
            fg=_UI.get("primary", _UI["accent_dark"]),
            font=_UI["font_section"],
        ).pack(side="left")
        model_header = (
            "模型未选择"
            if self._selected_model_label in ("未选择", "", "模型未选择")
            else self._selected_model_label
        )
        self._lbl_selected_model = tk.Label(
            ai_header,
            text=model_header,
            bg=card,
            fg=muted,
            font=_UI["font_small"],
        )
        self._lbl_selected_model.pack(side="right")
        self._btn_select_model = pack_round_btn(
            strat,
            "选择决策模型",
            self._open_decision_model_picker,
            height=36,
            pady=0,
        )

        # ---------- 自动点击（布局自有；按钮改圆角）----------
        auto = section("自动点击")
        row_rounds = tk.Frame(auto, bg=card)
        row_rounds.pack(fill="x", pady=(0, 6))
        tk.Label(row_rounds, text="轮数", bg=card, fg=muted, font=font_ui).pack(
            side="left"
        )
        self._rounds_var = tk.StringVar(value="1")
        rounds_wrap, self._ent_rounds = make_rounded_entry(
            row_rounds,
            textvariable=self._rounds_var,
            width=4,
            font=font_ui,
            bg=_UI.get("entry_bg", "#FAF8FD"),
            fg=text_c,
            border=_UI["border"],
            height=28,
            radius=12,
        )
        rounds_wrap.pack(side="left", padx=(8, 8))
        self._spn_rounds = self._ent_rounds
        self._lbl_rounds_progress = tk.Label(
            row_rounds,
            text="0/1",
            anchor="w",
            bg=card,
            fg=_UI["accent_dark"],
            font=font_mono,
        )
        self._lbl_rounds_progress.pack(side="left")

        self._btn_auto_stop = pack_round_btn(
            row_rounds,
            "停止",
            self._auto_click_stop,
            danger=True,
            side="right",
            padx=(4, 0),
            fill="",
            width=64,
            height=32,
            pady=0,
        )
        self._btn_auto_start = pack_round_btn(
            row_rounds,
            "启动",
            self._auto_click_start,
            primary=True,
            side="right",
            padx=(8, 0),
            fill="",
            width=110,
            height=32,
            pady=0,
        )
        # 无独立暂停键；H 格暂停时用「启动」当继续。占位避免旧代码引用崩掉。
        self._btn_auto_pause = self._btn_auto_stop

        pack_round_btn(
            auto,
            "快捷键设置",
            self._open_hotkey_settings,
            height=28,
            radius=8,
            font=_UI["font_small"],
            pady=4,
        )

        self._skip_exclamation_var = tk.BooleanVar(
            value=bool(self._runtime.skip_exclamation_reward)
        )
        self._chk_skip_exclamation = ZephieMinimalCheck(
            auto,
            "跳过感叹号格奖励",
            variable=self._skip_exclamation_var,
            command=self._on_skip_exclamation_change,
            bg=card,
            fg=text_c,
            font=font_ui,
        )
        self._chk_skip_exclamation.pack(fill="x", pady=(8, 3))

        self._skip_animation_var = tk.BooleanVar(
            value=bool(self._runtime.skip_animation_via_f12)
        )
        self._chk_skip_animation = ZephieMinimalCheck(
            auto,
            "投骰/用卡后跳过动画",
            variable=self._skip_animation_var,
            command=self._on_skip_animation_change,
            bg=card,
            fg=text_c,
            font=font_ui,
        )
        self._chk_skip_animation.pack(fill="x", pady=3)

        self._auto_replenish_var = tk.BooleanVar(
            value=bool(self._runtime.auto_replenish_dice)
        )
        self._chk_auto_replenish = ZephieMinimalCheck(
            auto,
            "自动补充骰子",
            variable=self._auto_replenish_var,
            command=self._on_auto_replenish_change,
            bg=card,
            fg=text_c,
            font=font_ui,
        )
        self._chk_auto_replenish.pack(fill="x", pady=3)

        interval_row = tk.Frame(auto, bg=card)
        interval_row.pack(fill="x", pady=(6, 0))
        tk.Label(
            interval_row,
            text="点击间隔基准(ms):",
            anchor="w",
            bg=card,
            fg=muted,
            font=_UI["font_small"],
        ).pack(side="left")
        self._click_interval_var = tk.StringVar(
            value=str(int(self._runtime.click_interval_ms))
        )
        interval_wrap, self._ent_click_interval = make_rounded_entry(
            interval_row,
            textvariable=self._click_interval_var,
            width=6,
            font=font_ui,
            bg=_UI.get("entry_bg", "#FAF8FD"),
            fg=text_c,
            border=_UI["border"],
            height=28,
            radius=12,
        )
        interval_wrap.pack(side="left", padx=(6, 0))
        self._ent_click_interval.bind("<Return>", self._on_click_interval_commit)
        self._ent_click_interval.bind("<FocusOut>", self._on_click_interval_commit)

        self._lbl_auto_hotkeys = tk.Label(
            auto,
            text="",
            anchor="w",
            bg=card,
            fg=muted,
            font=_UI["font_small"],
            wraplength=340,
            justify="left",
        )
        self._lbl_auto_hotkeys.pack(fill="x")
        self._sync_auto_click_buttons()

        log_wrap = tk.Frame(
            shell,
            bg=card,
            highlightbackground=_UI["border"],
            highlightcolor=_UI["border"],
            highlightthickness=1,
            takefocus=0,
            padx=8,
            pady=6,
        )
        log_wrap.pack(fill="both", expand=True)
        log_head = tk.Frame(log_wrap, bg=card)
        log_head.pack(fill="x")
        tk.Label(
            log_head,
            text="日志",
            anchor="w",
            bg=card,
            fg=_UI["accent_dark"],
            font=_UI["font_section"],
        ).pack(side="left")
        # 导出日志：只在脚本停止时可用（自检要真的截一张图，运行时导出会互相干扰）
        self._btn_export_log = pack_round_btn(
            log_head,
            "导出日志",
            self._export_logs,
            side="right",
            fill=None,
            width=92,
            height=24,
            radius=8,
            font=_UI["font_small"],
            pady=0,
        )
        self._text = scrolledtext.ScrolledText(
            log_wrap,
            height=12,
            wrap="word",
            state="disabled",
            bg="#FFFCFB",
            fg=text_c,
            font=font_ui,
            relief="flat",
            highlightthickness=0,
        )
        self._text.pack(fill="both", expand=True, pady=(4, 0))
        self._text.tag_config("recommend", foreground=_UI["pink"])
        self._text.tag_config("log_action", foreground=_UI["accent_dark"], font=_UI["font_section"])
        self._text.tag_config("log_recognize", foreground=_UI["mint"])
        self._text.tag_config("log_auto", foreground=_UI["sky"])
        self._text.tag_config("log_pause", foreground=_UI["warn"])
        self._text.tag_config("log_stop", foreground=_UI["danger"])
        self._text.tag_config("log_hand", foreground=_UI["accent"])
        self._text.tag_config("log_error", foreground=_UI["danger"], font=_UI["font_section"])
        self._text.tag_config("log_suggest", foreground=_UI["pink"])
        self._text.tag_config(
            "log_cell", foreground=_UI["accent_dark"], font=_UI["font_mono"]
        )
        # 点空白/标签时把焦点从轮数、间隔输入框挪开，避免卡片看起来一直“选中”
        self._install_background_defocus(shell)

    def _on_background_click(self, event: tk.Event) -> None:
        try:
            cls = event.widget.winfo_class()
        except tk.TclError:
            return
        if cls in ("Entry", "Spinbox", "Text"):
            return
        self.root.focus_set()

    def _install_background_defocus(self, widget: tk.Misc) -> None:
        skip = {"Entry", "Spinbox", "Text", "Scrollbar", "TScrollbar"}
        for child in widget.winfo_children():
            try:
                cls = child.winfo_class()
            except tk.TclError:
                continue
            if cls not in skip:
                child.bind("<Button-1>", self._on_background_click, add="+")
                self._install_background_defocus(child)

    def _poll_hotkeys(self) -> None:
        self._hotkey.poll()
        self.root.after(50, self._poll_hotkeys)

    def _on_bind_hotkey(self) -> None:
        self._bind_window_under_cursor()

    def _on_auto_start_hotkey(self) -> None:
        self._auto_click_start()

    def _on_auto_stop_hotkey(self) -> None:
        self._auto_click_stop()

    def _refresh_hotkey_labels(self) -> None:
        with use_runtime(self._runtime):
            bindings = self._hotkey.bindings() or load_hotkey_bindings()
        if hasattr(self, "_lbl_auto_hotkeys"):
            parts: list[str] = []
            for action, name in (
                (ACTION_AUTO_START, "启动"),
                (ACTION_AUTO_STOP, "停止"),
            ):
                b = bindings.get(action)
                if b and b.enabled and b.key:
                    parts.append(f"{name} {b.label}")
            self._lbl_auto_hotkeys.config(
                text=("自动点击快捷键：" + " | ".join(parts)) if parts else "自动点击快捷键：未设置"
            )

    def _open_hotkey_settings(self) -> None:
        if self._hotkey_settings_dialog is not None:
            try:
                if self._hotkey_settings_dialog.winfo_exists():
                    self._hotkey_settings_dialog.lift()
                    return
            except tk.TclError:
                pass
        with use_runtime(self._runtime):
            initial = load_hotkey_bindings()
        self._hotkey_settings_dialog = open_hotkey_settings(
            self.root,
            bindings=initial,
            on_saved=self._on_hotkeys_saved,
            on_open=lambda: self._hotkey.set_suspended(True),
            on_close=lambda: self._hotkey.set_suspended(False),
        )

    def _on_hotkeys_saved(self, bindings: dict[str, HotkeyBinding]) -> list[str]:
        with use_runtime(self._runtime):
            save_hotkey_bindings(bindings)
        errors = self._hotkey.reload(bindings)
        self._refresh_hotkey_labels()
        if errors:
            self._append_log("快捷键已保存（仅本界面），但部分注册失败：" + "；".join(errors))
            self._append_log(
                "多开时同一快捷键只能注册到一个界面；请改用不同组合键。"
            )
        else:
            self._append_log("快捷键已保存并应用（仅本界面；未写入共享 yaml）。")
        return errors

    def _resolved_hwnd(self) -> int | None:
        """只用本界面绑定的窗口，不读共享 game_window.yaml（避免多开串台）。"""
        if not self._bound:
            return None
        return resolve_hwnd(
            self._bound.capture_hwnd,
            self._bound.title,
            capture_hwnd=self._bound.capture_hwnd,
        )

    def _load_regions(self) -> dict:
        """Resolve recognition regions from this panel's adventure anchor."""
        with use_runtime(self._runtime):
            return load_regions()

    def _restore_bound_window(self) -> None:
        """启动时用本实例 Runtime 里的初值恢复（bootstrap 自磁盘拷贝一次）。"""
        cfg = dict(self._runtime.game_window or {})
        hwnd = resolve_hwnd(
            cfg.get("hwnd"),
            cfg.get("title", ""),
            capture_hwnd=cfg.get("capture_hwnd"),
        )
        if hwnd:
            info = get_window_info(hwnd)
            if info:
                self._set_bound(info, log_success=False, persist_disk=False)

    def _set_bound(
        self,
        info: WindowInfo,
        *,
        log_success: bool = True,
        persist_disk: bool = False,
    ) -> None:
        if not is_valid_game_capture_size(info.client_width, info.client_height):
            self._append_log(
                f"拒绝绑定：客户区仅 {info.client_width}×{info.client_height}，"
                f"过小。请把鼠标放在游戏画面内再按 {self._hotkey.bind_window_label()}。"
            )
            return
        prev_hwnd = self._bound.capture_hwnd if self._bound else None
        self._bound = info
        if prev_hwnd is not None and int(prev_hwnd) != int(info.capture_hwnd):
            from zephie_rolling_on.vision.capture_wgc import release_wgc_session

            # Release only this panel's prior hwnd; keep other instances' WGC sessions
            release_wgc_session(int(prev_hwnd))
        # 本界面内存；默认不写共享 yaml，避免多开互相覆盖
        self._runtime.game_window = {
            "capture_hwnd": int(info.capture_hwnd),
            "root_hwnd": int(info.root_hwnd),
            "hwnd": int(info.capture_hwnd),
            "title": info.title,
        }
        if persist_disk:
            _save_game_window_config(info)
        title_short = (info.title or "已绑定").strip()
        if len(title_short) > 14:
            title_short = title_short[:13] + "…"
        self._set_bind_pill(bound=True, text=title_short or "已绑定")
        if log_success:
            self._append_log(
                f"已绑定：{info.title} | {info.client_width}×{info.client_height} "
                f"| hwnd={info.capture_hwnd}"
            )

    def _on_pick_failed(self, msg: str) -> None:
        self.root.after(0, lambda: self._append_log(msg))

    def _bind_window_under_cursor(self) -> None:
        if sys.platform != "win32":
            return
        import win32gui

        x, y = win32gui.GetCursorPos()
        exclude = {int(self.root.winfo_id())}
        self._picker.add_exclude_hwnd(int(self.root.winfo_id()))
        info = pick_window_at_point(x, y, exclude_hwnds=exclude)
        if info is None:
            self._append_log(
                f"绑定失败：鼠标下没有足够大的游戏窗口。"
                f"请把鼠标移到游戏画面内再按 {self._hotkey.bind_window_label()}。"
            )
            return
        self._set_bound(info)

    def _on_window_picked(self, info: WindowInfo) -> None:
        self.root.after(0, lambda: self._set_bound(info))

    def _on_adventure_frame_confirmed(self, anchor: FrameAnchor) -> None:
        """红框标定完成：保存锚点到本界面内存，检测/点击区域按新锚点换算。"""
        self._append_log(
            f"[大冒险界面定位] 框尺寸 {anchor.width}x{anchor.height} | "
            f"屏幕左上 ({anchor.screen_left},{anchor.screen_top})"
        )
        if anchor.client_left is None or anchor.client_top is None:
            self._append_log(
                "  ⚠ 未绑定游戏窗口，无法换算客户区坐标。请先绑定窗口再标定。"
            )
            return
        with use_runtime(self._runtime):
            old_left, old_top = load_anchor()
            new_left, new_top = int(anchor.client_left), int(anchor.client_top)
            save_anchor(new_left, new_top)
        self._append_log(
            f"  → 大冒险界面客户区锚点 = ({new_left},{new_top})（仅本界面）"
        )
        if (new_left, new_top) == (old_left, old_top):
            self._append_log("  检测/点击区域已按当前锚点换算（与上次标定一致，无需平移）。")
        else:
            dx, dy = new_left - old_left, new_top - old_top
            self._append_log(
                f"  锚点 {old_left},{old_top} → {new_left},{new_top}；"
                f"检测/点击区域整体平移 ({dx},{dy})。"
            )
        self._append_log(
            "  下次识别与自动点击将使用更新后的客户区坐标。"
        )
        if self._session.is_running():
            with use_runtime(self._runtime):
                self._session.regions = load_regions()
            self._append_log("  已刷新运行中会话的识别区域。")

    def _apply_auto_calib_silent(
        self,
        hwnd: int,
        *,
        frame=None,
    ) -> tuple[bool, str]:
        """模板匹配自动标定：直接写锚点，不弹红框、不需确认。返回 (ok, 摘要)。

        若传入 ``frame`` 则复用该客户区截图，不再二次截屏。
        """
        if frame is None:
            frame = capture_window_client(int(hwnd))
        if frame is None:
            from zephie_rolling_on.vision.capture import last_capture_failure_note

            note = last_capture_failure_note()
            return False, f"截屏失败。{note}"

        from zephie_rolling_on.vision.adventure_calib import detect_adventure_frame_anchor

        result = detect_adventure_frame_anchor(frame)
        if not result.ok or result.anchor_left is None:
            return False, result.message

        new_left, new_top = int(result.anchor_left), int(result.anchor_top)
        with use_runtime(self._runtime):
            old_left, old_top = load_anchor()
            save_anchor(new_left, new_top)
        if (new_left, new_top) == (old_left, old_top):
            detail = f"锚点 ({new_left},{new_top})（与上次相同）"
        else:
            detail = (
                f"锚点 {old_left},{old_top} → {new_left},{new_top} "
                f"（平移 {new_left - old_left},{new_top - old_top}）"
            )
        return True, f"{result.message}；已写入 {detail}"

    def _auto_calib_adventure_frame(self) -> None:
        """模板匹配「道具兑换 / 排行」按钮，自动写入大冒险锚点并弹出红框预览。"""
        hwnd = self._resolved_hwnd()
        if not hwnd:
            self._append_log("[自动标定] 请先绑定游戏窗口。")
            return
        self._append_log("[自动标定] 截取客户区并搜索标定按钮…")
        ok, summary = self._apply_auto_calib_silent(int(hwnd))
        self._append_log(f"[自动标定] {summary}")
        if not ok:
            return
        if self._session.is_running():
            with use_runtime(self._runtime):
                self._session.regions = load_regions()
            self._append_log("[自动标定] 已刷新运行中会话的识别区域。")

        with use_runtime(self._runtime):
            left, top = load_anchor()
        # 手动点「自动标定」仍弹红框便于核对；启动流程里的静默标定不弹
        self._frame_locator.show(
            client_left=left,
            client_top=top,
            title_hint="自动标定结果（应与大冒险界面重合）",
        )

    def _apply_dice_ocr(self, rec: RecognitionResult) -> str:
        """把 OCR 剩骰写入本界面内存校准，返回用于校准区展示的提示。"""
        if rec.dice_remaining is not None:
            with use_runtime(self._runtime):
                apply_ocr_dice_to_calibration(rec.dice_remaining)
            return f"[OCR 已写入] 剩骰 {rec.dice_remaining}"
        if rec.raw_dice_text:
            return f"[骰子 OCR 失败] {rec.raw_dice_text!r}"
        if rec.notes:
            return f"[骰子 OCR 失败] {rec.notes}"
        return ""

    def _set_recognition_test_busy(self, busy: bool) -> None:
        self._recognition_test_busy = busy
        self._sync_recognition_test_buttons()

    def _sync_recognition_test_buttons(self) -> None:
        """识别测试进行中，或自动点击运行中：禁用一键测试与导出日志。"""
        running = self._session.is_running()
        busy = self._recognition_test_busy or self._auto_start_preparing or running
        state = "disabled" if busy else "normal"
        for name in ("_btn_test_all",):
            if hasattr(self, name):
                getattr(self, name).config(state=state)
        # 导出日志会现场跑自检（含实际截屏），运行时导出会与脚本抢游戏窗口
        export_busy = busy or self._session.is_stopping() or self._exporting_logs
        if hasattr(self, "_btn_export_log"):
            self._btn_export_log.config(
                state="disabled" if export_busy else "normal"
            )

    def _export_logs(self) -> None:
        """把日志、环境自检报告、环境信息和配置快照打成一个 zip 放到程序目录。

        只在脚本停止后可用：自检会真的截一张图并绑定窗口，运行中导出会和脚本
        抢同一个游戏窗口，两边的识别都会受影响。
        """
        if self._exporting_logs:
            return
        if (
            self._session.is_running()
            or self._session.is_stopping()
            or self._auto_start_preparing
            or self._recognition_test_busy
        ):
            self._append_log("[导出日志] 请先停止脚本（等按钮恢复为「启动」），再导出日志。")
            return

        log_text = self._text.get("1.0", "end").rstrip("\n") + "\n"
        self._exporting_logs = True
        self._sync_recognition_test_buttons()
        self._append_log("[导出日志] 开始整理日志并运行环境自检…")

        from zephie_rolling_on.ui.log_export import LogExportDialog

        LogExportDialog(
            self.root,
            log_text,
            on_log=self._append_log,
            on_closed=self._on_log_export_closed,
        )

    def _on_log_export_closed(self) -> None:
        self._exporting_logs = False
        self._sync_recognition_test_buttons()

    def _test_all_recognition(self) -> None:
        """整条识别流程：标定 → 卡池 → 骰子/格子/幸运卡。

        失败时输出尽量详细的排查信息（各阶段的具体原因），因为一键测试是
        用户反馈问题的主要入口。
        """
        if self._recognition_test_busy:
            self._append_log("识别测试进行中，请稍候…")
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            self._append_log("请先绑定游戏窗口。")
            return
        tag = "一键测试"
        save_debug = _dev_one_click_test_save_debug()
        self._set_recognition_test_busy(True)
        self._append_log(f"[{tag}] 开始：标定 → 卡池 → 骰子/格子/幸运卡")

        def work() -> None:
            def log(msg: str) -> None:
                try:
                    self.root.after(0, lambda m=msg: self._append_log(m))
                except Exception:  # noqa: BLE001
                    pass

            try:
                with use_runtime(self._runtime):
                    self._recognition_test_body(hwnd, save_debug, tag, log)
            except Exception as exc:  # noqa: BLE001
                import traceback

                detail = traceback.format_exc().strip().splitlines()[-1]
                self.root.after(
                    0,
                    lambda e=exc, d=detail: self._finish_recognition_test_error(
                        f"{tag}内部异常：{type(e).__name__}: {e}｜{d}"
                    ),
                )

        threading.Thread(target=work, name="recognition-test-all", daemon=True).start()

    def _recognition_test_body(
        self,
        hwnd: int,
        save_debug: bool,
        tag: str,
        log: "Callable[[str], None]",
    ) -> None:
        """在工作线程内执行；失败原因通过 ``_finish_recognition_test_error`` 上报。"""
        # ---- 1) 分辨率检查 + 标定（1366×768 时跳过开关检测，其余分辨率照常）----
        boot = run_adventure_bootstrap(
            int(hwnd), on_log=None, skip_toggle_when_blocked=True
        )
        if boot.resolution_notice:
            log(f"[跳过动画] {boot.resolution_notice}")

        if not boot.ok:
            lines = [
                f"{tag}：标定失败",
                f"  开关按钮：{boot.toggle_msg}",
                f"  第1次标定：{boot.calib_msg}",
            ]
            if boot.calib_attempt >= 2:
                lines.append(f"  尝试次数：{boot.calib_attempt}（含点击开关后重标）")
            self.root.after(
                0,
                lambda t=tag, ls=lines: self._finish_recognition_test_error(
                    "\n".join(ls)
                ),
            )
            return

        # 标定成功，但开关按钮没找到：标定按钮（道具兑换/排行）在画面上，开关按钮不在
        # （常见于被其他界面盖住）。此时标定仍能成功，但跳过动画依赖该坐标，用旧坐标
        # 点击会落在错误位置且不报错，所以明确告警——但不停下后续检测，让其余阶段
        # 的诊断信息也能拿到。
        if boot.toggle_checked and not boot.toggle_ok:
            log(f"[{tag}] 警告：开关按钮检测失败（跳过动画将无法正常工作）")
            log(f"  原因：{boot.toggle_msg}")
            log("  影响：投骰/用卡后的跳过动画会点击旧坐标，可能落在错误位置且不报错")
            log(
                "  处理：确认游戏画面完整可见（开关按钮未被其他界面遮挡）后重试；"
                "必要时在 config/click_targets.yaml 手动修正 toggle_adventure_ui"
            )
            log(f"[{tag}] 继续执行后续检测…")

        # ---- 2) 卡池 ----
        regions = load_regions()
        pool_lines: list[str] = []
        pool_result = scan_and_apply_deck_pool(
            int(hwnd),
            regions,
            on_log=lambda m: pool_lines.append(str(m)),
            save_debug=save_debug,
        )
        if pool_result is None:
            detail = "\n".join(f"  {x}" for x in pool_lines) or "  （未产生诊断信息）"
            self.root.after(
                0,
                lambda t=tag, d=detail: self._finish_recognition_test_error(
                    f"{t}：卡池扫描失败\n{d}"
                ),
            )
            return

        # ---- 3) 截屏 + 识别 ----
        frame = capture_window_client(hwnd)
        if frame is None:
            from zephie_rolling_on.vision.capture import (
                capture_block_reason,
                last_capture_failure_note,
            )

            if capture_block_reason() == "occluded":
                self.root.after(
                    0,
                    lambda t=tag: (
                        self._set_recognition_test_busy(False),
                        self._show_occlusion_notice(f"{t}已停止"),
                    ),
                )
                return
            from zephie_rolling_on.vision.capture import windows_build, wgc_supported

            note = last_capture_failure_note() or "未返回图像"
            win = int(hwnd)
            lines = [
                f"{tag}：截屏失败",
                f"  原因：{note}",
                f"  截屏后端：{'WGC' if wgc_supported() else '桌面裁切'}"
                f"（系统内部版本 {windows_build()}）",
                f"  窗口句柄：{win}",
            ]
            self.root.after(
                0,
                lambda t=tag, ls=lines: self._finish_recognition_test_error("\n".join(ls)),
            )
            return

        inv_rec = recognize_inventory_dice(frame, regions)
        charge_rec = recognize_charge_dice(frame, regions)
        if save_debug:
            save_inventory_dice_debug(frame, regions, inv_rec)
            save_charge_dice_debug(frame, regions, charge_rec)
        dice_rec, cell_rec, lucky_rec = recognize_all_on_frame(
            frame,
            regions,
            save_debug=save_debug,
            debug_snapshot=save_debug,
        )
        self.root.after(
            0,
            lambda d=dice_rec, c=cell_rec, l=lucky_rec, t=tag: (
                self._finish_all_recognition(d, c, l, t)
            ),
        )

    def _recognition_detail_lines(
        self,
        dice_rec: RecognitionResult,
        cell_rec: RecognitionResult,
        lucky_rec: RecognitionResult,
    ) -> list[str]:
        """识别结果的可读明细，用于成功/失败两种输出。"""
        return _format_recognition_detail(dice_rec, cell_rec, lucky_rec)

    def _finish_all_recognition(
        self,
        dice_rec: RecognitionResult,
        cell_rec: RecognitionResult,
        lucky_rec: RecognitionResult,
        tag: str = "一键测试",
    ) -> None:
        self._set_recognition_test_busy(False)
        # 更新状态栏；不打 OCR 写入类日志
        if dice_rec.dice_remaining is not None:
            self._apply_dice_ocr(dice_rec)
            self._refresh_calibration_labels()
            if self._calibration_dialog and self._calibration_dialog.winfo_exists():
                self._calibration_dialog.sync_from_file()
        else:
            self._refresh_calibration_labels()

        if cell_rec.cell_id is not None:
            self._update_current_cell(cell_rec.cell_id)

        lucky = format_owned_lucky_cards(lucky_rec.lucky_card_ids)
        self._update_owned_lucky_cards(lucky)

        detail = "\n".join(_format_recognition_detail(dice_rec, cell_rec, lucky_rec))

        missing: list[str] = []
        if dice_rec.dice_remaining is None:
            missing.append("骰子")
        if cell_rec.cell_id is None:
            missing.append("格子")
        if missing:
            self._append_log(
                f"[检测] {tag}：识别未通过（未识别出 {'、'.join(missing)}）\n{detail}"
            )
            return

        self._append_log(f"[{tag}] 完成\n{detail}")

    def _show_occlusion_notice(self, headline: str) -> None:
        """画面被遮挡：弹窗提示并停止当前动作。"""
        from zephie_rolling_on.vision.capture import (
            OCCLUSION_HINT,
            last_capture_failure_note,
        )

        self._append_log(f"[遮挡] {headline}：{last_capture_failure_note() or OCCLUSION_HINT}")
        show_blocking_notice(self.root, "游戏画面被遮挡", OCCLUSION_HINT)

    def _finish_recognition_test_error(self, message: str) -> None:
        self._set_recognition_test_busy(False)
        lines = str(message).splitlines() or [""]
        self._append_log(f"[检测] {lines[0]}")
        for extra in lines[1:]:
            self._append_log(extra)
        self._refresh_calibration_labels()

    def _test_charge_dice(self) -> None:
        if self._recognition_test_busy:
            self._append_log("检测测试进行中，请稍候…")
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            self._append_log("请先绑定游戏窗口。")
            return
        self._set_recognition_test_busy(True)
        self._append_log("[充能骰子] 开始：截屏 → RapidOCR…")

        def work() -> None:
            try:
                with use_runtime(self._runtime):
                    regions = load_regions()
                    frame = capture_window_client(int(hwnd))
                    if frame is None:
                        raise RuntimeError("无法截取游戏窗口")
                    result = recognize_charge_dice(frame, regions)
                self.root.after(0, lambda r=result: self._finish_charge_dice(r))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._finish_charge_dice_error(e))

        threading.Thread(target=work, name="charge-dice-test", daemon=True).start()

    def _finish_charge_dice(self, result) -> None:
        self._set_recognition_test_busy(False)
        self._append_log(f"[充能骰子] {result.value}（{result.notes}）")
        self._set_status_note(f"充能骰子={result.value}")

    def _finish_charge_dice_error(self, exc: BaseException) -> None:
        self._set_recognition_test_busy(False)
        self._append_log(f"[充能骰子] 失败：{exc}")
        self._set_status_note(f"充能骰子检测失败：{exc}")

    def _test_roll_kind_detection(self) -> None:
        if self._recognition_test_busy:
            self._append_log("检测测试进行中，请稍候…")
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            self._append_log("请先绑定游戏窗口。")
            return
        regions = self._load_regions()
        self._set_recognition_test_busy(True)
        self._append_log(
            "[免费/付费骰] 开始截图（请确保亮屏且左下投骰按钮可见）…"
        )

        def work() -> None:
            try:
                frame = capture_window_client(hwnd)
                if frame is None or frame.size == 0:
                    raise RuntimeError("截屏失败或客户区为空")
                paths, probe = save_roll_kind_debug(frame, regions)
                self.root.after(
                    0,
                    lambda p=paths, pr=probe: self._finish_roll_kind_detection(p, pr),
                )
            except Exception as exc:
                self.root.after(
                    0, lambda e=exc: self._finish_roll_kind_detection_error(e)
                )

        threading.Thread(
            target=work, name="roll-kind-detection-test", daemon=True
        ).start()

    def _finish_roll_kind_detection(self, paths: dict[str, Path], probe) -> None:
        self._set_recognition_test_busy(False)
        if not paths:
            self._append_log(
                "[免费/付费骰] 失败：无法保存截图（search 区无效或模板缺失）"
            )
            return
        for key, path in paths.items():
            self._append_log(f"[免费/付费骰] 已保存 {key}: {path}")
        if probe is not None:
            if probe.hit is not None:
                kind_cn = "免费" if probe.hit.kind == "free" else "付费"
                self._append_log(
                    f"[免费/付费骰] 命中 {kind_cn}({probe.hit.kind}) "
                    f"score={probe.hit.score:.3f} "
                    f"free={probe.free_score:.3f} paid={probe.paid_score:.3f} "
                    f"阈值={probe.threshold:.2f}"
                )
                self._append_log(
                    f"[免费/付费骰] 中心=({probe.hit.center_x}, {probe.hit.center_y}) "
                    f"search={probe.search['left']},{probe.search['top']} "
                    f"{probe.search['width']}x{probe.search['height']}"
                )
            else:
                self._append_log(
                    f"[免费/付费骰] 未命中 "
                    f"free={probe.free_score:.3f} paid={probe.paid_score:.3f} "
                    f"阈值={probe.threshold:.2f} "
                    f"search={probe.search['left']},{probe.search['top']} "
                    f"{probe.search['width']}x{probe.search['height']}"
                )
        self._append_log("[免费/付费骰] 完成")

    def _finish_roll_kind_detection_error(self, exc: BaseException) -> None:
        self._set_recognition_test_busy(False)
        self._append_log(f"[免费/付费骰] 失败：{exc}")

    def _test_deck_pool_detection(self) -> None:
        if self._recognition_test_busy:
            self._append_log("检测测试进行中，请稍候…")
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            self._append_log("请先绑定游戏窗口。")
            return
        regions = self._load_regions()
        save_debug = _dev_one_click_test_save_debug()
        self._set_recognition_test_busy(True)
        self._append_log(
            "[卡池检测] 开始：开列表 → 第1轮 → 拖拽 → 第2轮 → 写校准 → 关列表…"
        )

        def work() -> None:
            try:
                with use_runtime(self._runtime):
                    result = scan_and_apply_deck_pool(
                        hwnd,
                        regions,
                        on_log=lambda m: self.root.after(0, lambda msg=m: self._append_log(msg)),
                        save_debug=save_debug,
                    )
                self.root.after(
                    0,
                    lambda r=result: self._finish_deck_pool_detection(r),
                )
            except Exception as exc:
                self.root.after(
                    0, lambda e=exc: self._finish_deck_pool_detection_error(e)
                )

        threading.Thread(target=work, name="deck-pool-detection-test", daemon=True).start()

    def _finish_deck_pool_detection(self, result) -> None:
        self._set_recognition_test_busy(False)
        if result is None:
            self._append_log("[卡池检测] 未完成（见上方日志）")
        else:
            self._append_log(
                f"[卡池检测] 完成 r1={result.round1_hit_count} "
                f"r2={result.round2_hit_count}｜{result.notes}"
            )
        self._sync_calibration_display()
        if self._calibration_dialog and self._calibration_dialog.winfo_exists():
            self._calibration_dialog.sync_from_file()

    def _finish_deck_pool_detection_error(self, exc: BaseException) -> None:
        self._set_recognition_test_busy(False)
        self._append_log(f"[卡池检测] 失败：{exc}")
        self._sync_calibration_display()

    def _start_ocr_preload(
        self,
        *,
        preload_rapid: bool = True,
        preload_easy: bool = False,
    ) -> None:
        regions = self._load_regions()
        preload_recognition_engines(
            block=False,
            config=regions,
            preload_rapid=preload_rapid,
            preload_easy=preload_easy,
        )
        self._poll_ocr_preload()

    def _poll_ocr_preload(self) -> None:
        status = recognition_preload_status()
        if status == "loading":
            self.root.after(400, self._poll_ocr_preload)
            return
        for line in recognition_preload_log_lines():
            self._append_log(line)

    def _set_status_note(self, text: str) -> None:
        """状态栏已去掉备注行；保留空实现以免旧调用报错。"""
        return

    def _set_bind_pill(self, *, bound: bool, text: str) -> None:
        """状态胶囊：未绑定樱粉 / 已绑定薄荷。"""
        if not hasattr(self, "_pill_status"):
            return
        if bound:
            bg, fg = _UI["badge_mint_bg"], _UI["badge_mint_fg"]
        else:
            bg, fg = _UI["badge_pink_bg"], _UI["badge_pink_fg"]
        self._pill_status.configure(bg=bg)
        self._lbl_pill_dot.configure(bg=bg, fg=fg)
        self._lbl_pill_text.configure(bg=bg, fg=fg, text=text)

    def _refresh_calibration_labels(self, *, dice_hint: str = "") -> None:
        with use_runtime(self._runtime):
            cal = load_planner_calibration()
        if hasattr(self, "_lbl_dice"):
            self._lbl_dice.config(text=str(cal.dice_remaining))
        if hasattr(self, "_lbl_deck"):
            rem = cal.deck_remaining_total()
            self._lbl_deck.config(text=f"{rem}/{DECK_TOTAL}")
        if dice_hint:
            self._append_log(dice_hint)

    def _on_calibration_changed(self) -> None:
        self.root.after(0, self._sync_calibration_display)

    def _sync_calibration_display(self) -> None:
        self._refresh_calibration_labels()
        if self._calibration_dialog and self._calibration_dialog.winfo_exists():
            self._calibration_dialog.sync_from_file()

    def _open_calibration(self) -> None:
        if self._calibration_dialog and self._calibration_dialog.winfo_exists():
            self._calibration_dialog.lift()
            return

        def on_saved(_cal, path) -> None:
            self._refresh_calibration_labels()
            self._append_log(f"详细卡池已更新（本界面内存）：{_cal.summary_line()}")

        self._calibration_dialog = open_planner_calibration(
            self.root,
            on_saved=on_saved,
            runtime=self._runtime,
        )

    def _update_owned_lucky_cards(self, text: str) -> None:
        # 会话线程回调：只投递到 UI 线程，避免直接碰 Tk
        self.root.after(0, lambda t=text: self._queue_lucky_text(t))

    def _update_current_cell(self, cell_id: int | None) -> None:
        text = str(cell_id) if cell_id is not None else "—"
        self.root.after(0, lambda t=text: self._queue_cell_text(t))

    def _queue_lucky_text(self, text: str) -> None:
        self._pending_lucky_text = text
        self._schedule_status_flush()

    def _queue_cell_text(self, text: str) -> None:
        self._pending_cell_text = text
        self._schedule_status_flush()

    def _schedule_status_flush(self) -> None:
        if self._status_flush_after_id is not None:
            return
        self._status_flush_after_id = self.root.after(80, self._flush_status_labels)

    def _flush_status_labels(self) -> None:
        self._status_flush_after_id = None
        if self._pending_cell_text is not None and hasattr(self, "_lbl_cell"):
            self._lbl_cell.config(text=self._pending_cell_text)
            self._pending_cell_text = None
        if self._pending_lucky_text is not None and hasattr(self, "_lbl_lucky"):
            self._lbl_lucky.config(text=self._pending_lucky_text)
            self._pending_lucky_text = None

    def _open_decision_model_picker(self) -> None:
        if self._session.is_running() or self._auto_start_preparing:
            self._append_log("自动点击运行中，请先停止再切换决策模型。")
            return
        open_model_picker(
            self.root,
            on_log=self._append_log,
            on_imported=self._on_decision_model_imported,
        )

    def _on_decision_model_imported(
        self,
        model: DiscoveredModel,
        result: DecisionModelImportResult,
    ) -> None:
        _ = result
        info = model.display
        label = f"{info.name} {info.version}".strip()
        self._selected_model_label = label
        self._active_decision_model = model.package
        set_active_decision_model(model.package)
        if hasattr(self, "_lbl_selected_model"):
            self._lbl_selected_model.config(text=label or "模型未选择")
        name = (info.name or label or "模型").strip()
        self._append_log(f"[决策模型] {name}已就绪")
        self._set_status_note(f"已选择决策模型：{label}")

    def _toggle_advice_loop(self) -> None:
        if self._advice_loop_active:
            self._stop_advice_loop()
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            self._append_log("请先绑定游戏窗口。")
            return
        if not self._map_path.is_file():
            self._append_log(f"找不到地图: {self._map_path}")
            return
        self._advice_loop_active = True
        self._last_cell_id = None
        self._last_hand_ids = []
        if hasattr(self, "_btn_advice"):
            self._btn_advice.config(text="停止规划建议")
        interval = float(self._load_regions().get("advice_poll_interval_seconds", 1.0))
        self._append_log(
            f"已启动规划建议循环（每 {interval:g}s 仅检测格子；"
            f"变亮且变化后完整识别手牌/剩骰并输出建议）",
        )
        self._schedule_advice_poll()

    def _stop_advice_loop(self) -> None:
        self._advice_loop_active = False
        if self._advice_loop_after_id is not None:
            try:
                self.root.after_cancel(self._advice_loop_after_id)
            except tk.TclError:
                pass
            self._advice_loop_after_id = None
        if hasattr(self, "_btn_advice"):
            self._btn_advice.config(text="启动规划建议")
        self._append_log("已停止规划建议循环。")

    def _schedule_advice_poll(self) -> None:
        if not self._advice_loop_active:
            return
        self._run_advice_poll_tick()
        interval_ms = int(
            float(self._load_regions().get("advice_poll_interval_seconds", 1.0)) * 1000,
        )
        self._advice_loop_after_id = self.root.after(
            interval_ms,
            self._schedule_advice_poll,
        )

    def _run_advice_poll_tick(self) -> None:
        if not self._advice_loop_active or self._advice_poll_busy:
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            return
        regions = self._load_regions()
        self._advice_poll_busy = True

        def work() -> None:
            try:
                rec = recognize_advice_poll(regions, hwnd=hwnd)
                self.root.after(0, lambda r=rec: self._on_advice_poll(r))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._on_advice_poll_error(e))

        threading.Thread(target=work, name="advice-poll", daemon=True).start()

    def _on_advice_poll_error(self, exc: BaseException) -> None:
        self._advice_poll_busy = False
        self._append_log(f"[监控] 检测失败：{exc}")

    def _on_advice_poll(self, rec: RecognitionResult) -> None:
        self._advice_poll_busy = False
        if not self._advice_loop_active:
            return

        cell_id = rec.cell_id

        if cell_id is not None:
            if self._last_cell_id is None:
                if self._advice_busy:
                    return
                self._append_log(f"[监控] 首次识别格子 {cell_id}，完整识别手牌并输出建议…")
                self._run_full_planner_advice()
                return
            if cell_id != self._last_cell_id:
                self._last_cell_id = cell_id
                self._append_log(f"[监控] 格子变化 → {cell_id}，完整识别手牌…")
                self._run_full_planner_advice()
                return

    def _run_full_planner_advice(self) -> None:
        if self._advice_busy:
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            return
        regions = self._load_regions()
        self._advice_busy = True

        def work() -> None:
            try:
                board = load_map_from_xlsx(self._map_path)
                with use_runtime(self._runtime):
                    result = run_tick(board, regions, hwnd=hwnd)
                self.root.after(0, lambda r=result: self._finish_advice(r))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._finish_advice_error(e))

        threading.Thread(target=work, name="planner-advice", daemon=True).start()

    def _finish_advice(self, result) -> None:
        self._advice_busy = False
        rec = result.recognition
        if rec and rec.lucky_card_ids:
            hand = list(rec.lucky_card_ids)
            with use_runtime(self._runtime):
                recorded = sync_deck_from_new_hand_cards(self._last_hand_ids, hand)
            if recorded:
                self._append_log(f"[卡组] 手牌新增 {recorded}，已记入已抽清单")
            self._last_hand_ids = hand
            self._update_owned_lucky_cards(format_owned_lucky_cards(hand))
        if rec and rec.cell_id is not None:
            self._last_cell_id = rec.cell_id
            self._update_current_cell(rec.cell_id)

        dice_hint = self._apply_dice_ocr(rec) if rec else ""

        for line in result.log_lines:
            if line.startswith("[建议]"):
                self._append_log(line)
            elif any(
                line.startswith(prefix)
                for prefix in (
                    "幸运卡:",
                    "卡组",
                    "手牌已满",
                    "当前格子",
                    "格子 OCR",
                    "投掷 OCR",
                    "未能识别",
                )
            ):
                self._append_log(line)

        self._refresh_calibration_labels(dice_hint=dice_hint)
        if self._calibration_dialog and self._calibration_dialog.winfo_exists():
            self._calibration_dialog.sync_from_file()

    def _finish_advice_error(self, exc: BaseException) -> None:
        self._advice_busy = False
        self._append_log(f"规划建议失败：{exc}")

    def _poll_cursor_coords(self) -> None:
        hwnd = self._resolved_hwnd()
        if hwnd and hasattr(self, "_lbl_cursor_client"):
            # 必须走本界面 runtime 锚点：标定只写内存，不抢写 adventure_frame.yaml
            with use_runtime(self._runtime):
                pos = get_cursor_client_pos(hwnd)
            if pos:
                self._lbl_cursor_client.config(
                    text=f"x={pos.x}, y={pos.y}  （屏幕 {pos.screen_x},{pos.screen_y}）",
                    fg="#0066aa",
                )
                self._lbl_cursor_frame.config(
                    text=f"x={pos.frame_x}, y={pos.frame_y}  ← 写入 click_targets",
                    fg="#2e7d32",
                )
            else:
                self._lbl_cursor_client.config(
                    text="移到游戏窗口客户区内",
                    fg="#999",
                )
                self._lbl_cursor_frame.config(text="—", fg="#999")
        self.root.after(80, self._poll_cursor_coords)

    def _enqueue_log(self, msg: str) -> None:
        # 自动点击运行中：操作 + 精简的本局结束/轮次 + 启停
        if self._session.is_running() and bool(getattr(self._session, "auto_click", False)):
            if not (
                msg.startswith("[操作]")
                or msg.startswith("[本局结束] 已清空幸运卡池与手牌")
                or msg.startswith("[轮次] 已完成 ")
                or msg.startswith("[轮次] 本局已结束，立即点击开启新局")
                or msg.startswith("脚本已启动")
                or msg.startswith("[停止]")
                or msg.startswith("[暂停]")
                or msg.startswith("[继续]")
            ):
                return
        self._log_queue.put(msg)

    def _poll_log_queue(self) -> None:
        # 决策在后台线程；此处只做 UI 刷新。勿在空闲时反复 config 按钮，否则拖动/缩放会卡。
        batch: list[str] = []
        while len(batch) < 80:
            try:
                batch.append(self._log_queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._append_log_batch(batch)
        # 停止中/启动准备中需要刷新按钮文案；其余由 start/stop 等显式 sync
        if self._session.is_stopping() or self._auto_start_preparing:
            self._sync_auto_click_buttons()
        self.root.after(200, self._poll_log_queue)

    @staticmethod
    def _log_tag_for_message(msg: str) -> str | None:
        if msg.startswith("[操作]"):
            return "log_action"
        if msg.startswith("[识别]") or msg.startswith("[继续] 识别"):
            return "log_recognize"
        if msg.startswith("[暂停]") or msg.startswith("[继续]"):
            return "log_pause"
        if msg.startswith("[停止]"):
            return "log_stop"
        if msg.startswith("[自动]"):
            return "log_auto"
        if msg.startswith("[手牌]") or msg.startswith("[卡池]") or msg.startswith("幸运卡:"):
            return "log_hand"
        if msg.startswith("[错误]"):
            return "log_error"
        if msg.startswith("[建议]"):
            return "log_suggest"
        if msg.startswith("当前格子"):
            return "log_cell"
        return None

    def _append_log(self, msg: str) -> None:
        self._append_log_batch([msg])

    def _append_log_batch(self, messages: list[str]) -> None:
        if not messages:
            return
        self._text.config(state="normal")
        for msg in messages:
            start_index = self._text.index("end-1c")
            # 时间戳：排查时要和系统事件（如显卡驱动超时恢复）对时间，
            # 没有时间戳就对不上，也无法判断"卡了一会儿"到底是多久。
            line = f"{datetime.datetime.now().strftime('%H:%M:%S')} {msg}"
            self._text.insert("end", line + "\n")
            line_tag = self._log_tag_for_message(msg)
            if line_tag:
                self._text.tag_add(line_tag, start_index, f"{start_index} lineend")
            # 偏移量必须在**插入后的整行**上算（已含时间戳前缀），在 msg 上算会错位
            pos = line.find("推荐")
            if pos != -1:
                seg_end = line.find(" | ", pos)
                if seg_end == -1:
                    seg_end = len(line)
                self._text.tag_add(
                    "recommend",
                    f"{start_index}+{pos}c",
                    f"{start_index}+{seg_end}c",
                )
        self._text.see("end")
        try:
            end_line = int(str(self._text.index("end-1c")).split(".")[0])
            if end_line > LOG_TEXT_MAX_LINES:
                self._text.delete("1.0", f"{end_line - LOG_TEXT_MAX_LINES + 1}.0")
        except (ValueError, tk.TclError):
            pass
        self._text.config(state="disabled")

    def _on_round_progress(self, completed: int, target: int) -> None:
        text = f"{completed}/{target}"
        self.root.after(
            0,
            lambda t=text: self._lbl_rounds_progress.config(text=t),
        )

    def _on_auto_stopped(self) -> None:
        self.root.after(0, self._on_auto_stopped_ui)

    def _on_auto_stopped_ui(self, _retry: int = 0) -> None:
        self._disable_mouse_shield()
        self._sync_auto_click_buttons()
        if self._session.take_stop_reason_kind() == "occluded":
            self._show_occlusion_notice("自动点击已停止")
        # 循环内停止（如可用骰子为 0）会在工作线程里就通知界面，此时线程尚未退出，
        # is_stopping() 仍为真，按钮会显示「停止中…」。线程真正结束后没有任何回调，
        # 界面就永久卡在该状态（按停止热键可恢复，因为那条路径会 join 并重新同步）。
        # 这里在仍处于停止中时短暂轮询，等线程退出后自动恢复按钮状态。
        if self._session.is_stopping() and _retry < 25:
            self.root.after(200, lambda: self._on_auto_stopped_ui(_retry + 1))

    def _read_rounds_target(self) -> int:
        try:
            return max(1, min(99, int(self._rounds_var.get())))
        except (tk.TclError, ValueError):
            return 1

    def _on_skip_exclamation_change(self) -> None:
        enabled = bool(self._skip_exclamation_var.get())
        with use_runtime(self._runtime):
            save_skip_exclamation_reward(enabled)
        state = "开启" if enabled else "关闭"
        self._append_log(f"跳过感叹号格奖励已{state}")

    def _on_skip_animation_change(self) -> None:
        enabled = bool(self._skip_animation_var.get())
        with use_runtime(self._runtime):
            save_skip_animation_via_f12(enabled)
        state = "开启" if enabled else "关闭"
        self._append_log(f"投骰/用卡后跳过动画已{state}")

    def _on_auto_replenish_change(self) -> None:
        enabled = bool(self._auto_replenish_var.get())
        with use_runtime(self._runtime):
            save_auto_replenish_dice(enabled)
        state = "开启" if enabled else "关闭"
        self._append_log(f"自动补充骰子已{state}")

    def _on_click_interval_commit(self, _event: object | None = None) -> None:
        raw = self._click_interval_var.get().strip()
        try:
            ms = int(float(raw))
        except (TypeError, ValueError):
            ms = int(self._runtime.click_interval_ms)
        ms = max(50, ms)
        self._click_interval_var.set(str(ms))
        if ms == int(self._runtime.click_interval_ms):
            return
        with use_runtime(self._runtime):
            save_click_interval_ms(ms)
        self._append_log(
            f"点击间隔基准已设为 {ms}ms（实际 {ms}±20ms，最低 50；"
            "已写入 auto_click.yaml，下次启动后生效）"
        )

    def _disable_mouse_shield(self) -> None:
        """停止时卸钩（默认不启用屏蔽；仅清理残留）。"""
        get_mouse_shield().stop()

    def _sync_auto_click_buttons(self) -> None:
        running = self._session.is_running()
        paused = self._session.is_paused()
        stopping = self._session.is_stopping()
        preparing = self._auto_start_preparing
        spin_state = "disabled" if (running or preparing) else "normal"
        self._spn_rounds.config(state=spin_state)
        if hasattr(self, "_chk_skip_exclamation"):
            self._chk_skip_exclamation.config(state=spin_state)
        if hasattr(self, "_chk_skip_animation"):
            self._chk_skip_animation.config(state=spin_state)
        if hasattr(self, "_chk_auto_replenish"):
            self._chk_auto_replenish.config(state=spin_state)
        if hasattr(self, "_ent_click_interval"):
            self._ent_click_interval.config(state=spin_state)
        if preparing:
            self._btn_auto_start.config(state="disabled", text="准备中…")
            self._btn_auto_stop.config(state="disabled", text="停止")
        elif stopping:
            self._btn_auto_start.config(state="disabled", text="启动")
            self._btn_auto_stop.config(state="disabled", text="停止中…")
        elif running and not paused:
            self._btn_auto_start.config(state="disabled", text="启动")
            self._btn_auto_stop.config(state="normal", text="停止")
        elif running and paused:
            self._btn_auto_start.config(state="normal", text="启动 / 继续")
            self._btn_auto_stop.config(state="normal", text="停止")
        else:
            self._btn_auto_start.config(state="normal", text="启动")
            self._btn_auto_stop.config(state="disabled", text="停止")
        self._sync_recognition_test_buttons()

    def _auto_click_start(self) -> None:
        if self._session.is_paused():
            self._session.resume()
            self._sync_auto_click_buttons()
            return
        if self._auto_start_preparing:
            self._append_log("启动准备中（标定/补充骰子/卡池），请稍候…")
            return
        if self._session.is_running():
            self._append_log("自动点击已在运行。")
            return
        hwnd = self._resolved_hwnd()
        if not hwnd:
            self._append_log("请先绑定游戏窗口。")
            return
        if not self._map_path.is_file():
            self._append_log(f"找不到地图: {self._map_path}")
            return
        if not active_model_ready():
            self._append_log("[启动] 请先点「选择决策模型」并完成导入")
            return

        rounds = self._read_rounds_target()
        skip_ex = bool(self._skip_exclamation_var.get())
        auto_replenish = bool(self._auto_replenish_var.get())
        self._update_current_cell(None)
        self._auto_start_preparing = True
        self._sync_auto_click_buttons()

        def work() -> None:
            calib_ok = False
            calib_msg = ""
            pool_ok = False
            toggle_ok = False
            toggle_msg = ""
            toggle_checked = False
            replenish_ok = True
            replenish_msg = ""
            try:
                with use_runtime(self._runtime):
                    boot = run_adventure_bootstrap(
                        int(hwnd), on_log=None, skip_toggle_when_blocked=True
                    )
                    calib_ok = bool(boot.ok)
                    calib_msg = boot.message
                    toggle_ok = bool(boot.toggle_ok)
                    toggle_msg = boot.toggle_msg
                    toggle_checked = bool(boot.toggle_checked)
                    if boot.resolution_notice:
                        self.root.after(
                            0,
                            lambda n=boot.resolution_notice: self._append_log(
                                f"[跳过动画] {n}"
                            ),
                        )
                    if not calib_ok:
                        self.root.after(
                            0,
                            lambda: self._finish_auto_start_prep(
                                hwnd=int(hwnd),
                                rounds=rounds,
                                skip_ex=skip_ex,
                                auto_replenish=auto_replenish,
                                calib_ok=False,
                                calib_msg=calib_msg,
                                pool_ok=False,
                                toggle_ok=toggle_ok,
                                toggle_msg=toggle_msg,
                                toggle_checked=toggle_checked,
                                replenish_ok=True,
                                replenish_msg="",
                            ),
                        )
                        return
                    regions = load_regions()
                    if auto_replenish:
                        rep = run_dice_detect_and_replenish(
                            int(hwnd),
                            regions,
                            enabled=True,
                            on_log=None,
                        )
                        replenish_ok = bool(rep.ok)
                        replenish_msg = rep.message
                        if not replenish_ok:
                            self.root.after(
                                0,
                                lambda: self._finish_auto_start_prep(
                                    hwnd=int(hwnd),
                                    rounds=rounds,
                                    skip_ex=skip_ex,
                                    auto_replenish=auto_replenish,
                                    calib_ok=True,
                                    calib_msg=calib_msg,
                                    pool_ok=False,
                                    toggle_ok=toggle_ok,
                                    toggle_msg=toggle_msg,
                                    toggle_checked=toggle_checked,
                                    replenish_ok=False,
                                    replenish_msg=replenish_msg,
                                ),
                            )
                            return
                        # 实际点过补充后，等界面收起再扫卡池
                        if rep.status == "replenished":
                            time.sleep(0.05)
                    pool_result = scan_and_apply_deck_pool(
                        int(hwnd),
                        regions,
                        on_log=None,
                        save_debug=False,
                    )
                    pool_ok = pool_result is not None
            except Exception as exc:
                self.root.after(
                    0,
                    lambda e=exc: self._finish_auto_start_prep_error(e),
                )
                return
            self.root.after(
                0,
                lambda: self._finish_auto_start_prep(
                    hwnd=int(hwnd),
                    rounds=rounds,
                    skip_ex=skip_ex,
                    auto_replenish=auto_replenish,
                    calib_ok=calib_ok,
                    calib_msg=calib_msg,
                    pool_ok=pool_ok,
                    toggle_ok=toggle_ok,
                    toggle_msg=toggle_msg,
                    toggle_checked=toggle_checked,
                    replenish_ok=replenish_ok,
                    replenish_msg=replenish_msg,
                ),
            )

        threading.Thread(target=work, name="auto-start-prep", daemon=True).start()

    def _finish_auto_start_prep(
        self,
        *,
        hwnd: int,
        rounds: int,
        skip_ex: bool,
        auto_replenish: bool = True,
        calib_ok: bool,
        calib_msg: str,
        pool_ok: bool,
        toggle_ok: bool = False,
        toggle_msg: str = "",
        toggle_checked: bool = False,
        replenish_ok: bool = True,
        replenish_msg: str = "",
    ) -> None:
        self._auto_start_preparing = False
        _ = (calib_msg, pool_ok, toggle_msg, replenish_msg)
        self._sync_calibration_display()
        if self._calibration_dialog and self._calibration_dialog.winfo_exists():
            self._calibration_dialog.sync_from_file()

        if not calib_ok:
            self._append_log("[启动] 标定出错")
            self._sync_auto_click_buttons()
            return

        # 跳动画（postmessage 模式）靠点击开关按钮坐标实现。检测失败时坐标仍是旧的，
        # 点击会落在错误位置且不报错，因此启动阶段直接停止，避免带着坏坐标空跑。
        if toggle_checked and not toggle_ok:
            self._append_log("[启动] 开关按钮检测失败，已停止启动")
            self._append_log(f"  原因：{toggle_msg}")
            self._append_log(
                "  影响：投骰/用卡后的跳过动画会点击旧坐标，可能落在错误位置且不报错"
            )
            self._append_log(
                "  处理：确认游戏画面完整可见（开关按钮未被其他界面遮挡）后重试；"
                "必要时在 config/click_targets.yaml 手动修正 toggle_adventure_ui"
            )
            self._sync_auto_click_buttons()
            return

        if auto_replenish and not replenish_ok:
            self._append_log("[启动] 补充骰子出错")
            self._sync_auto_click_buttons()
            return

        if self._session.is_running():
            self._append_log("自动点击已在运行。")
            self._sync_auto_click_buttons()
            return

        if not active_model_ready():
            self._append_log("[启动] 决策模型未就绪")
            self._sync_auto_click_buttons()
            return

        self._session.start(
            hwnd,
            auto_click=True,
            rounds_target=rounds,
            skip_exclamation_reward=skip_ex,
            auto_replenish_dice=auto_replenish,
        )
        self._sync_auto_click_buttons()

    def _finish_auto_start_prep_error(self, exc: BaseException) -> None:
        self._auto_start_preparing = False
        _ = exc
        self._append_log("[启动] 准备出错")
        self._sync_auto_click_buttons()

    def _auto_click_pause(self) -> None:
        # 热键仍可暂停；界面已无暂停按钮
        self._session.pause()
        self._disable_mouse_shield()
        self._sync_auto_click_buttons()

    def _auto_click_stop(self) -> None:
        self._disable_mouse_shield()
        self._session.stop()
        self._update_current_cell(None)
        target = self._read_rounds_target()
        self._lbl_rounds_progress.config(text=f"0/{target}")
        self._sync_auto_click_buttons()

    def _on_main_configure(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        if self._geo_save_after is not None:
            try:
                self.root.after_cancel(self._geo_save_after)
            except (tk.TclError, ValueError):
                pass
        self._geo_save_after = self.root.after(
            400, lambda: remember_window_geometry(self.root, "main")
        )

    def _on_close(self) -> None:
        remember_window_geometry(self.root, "main")
        self._disable_mouse_shield()
        if self._advice_loop_active:
            self._stop_advice_loop()
        if self._session.is_running():
            self._session.force_stop()
        from zephie_rolling_on.vision.capture_wgc import release_wgc_session

        hwnd = self._bound.capture_hwnd if self._bound else None
        if hwnd:
            release_wgc_session(int(hwnd))
        self._hotkey.stop()
        self._picker.destroy()
        self._frame_locator.destroy()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_app() -> None:
    # 识图依赖逻辑坐标 == 物理像素：系统缩放不是 100% 时先拦下并提示，
    # 用户点「确认」后直接关闭软件，不进入主界面。
    if not guard_system_scale():
        return
    ControlPanel().run()