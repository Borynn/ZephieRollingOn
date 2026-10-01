from __future__ import annotations

from zephie_rolling_on.paths import project_root

import threading
import time
from collections.abc import Callable
from pathlib import Path

from zephie_rolling_on.app.runtime_state import RuntimeState, use_runtime
from zephie_rolling_on.app.tick import build_state, run_tick
from zephie_rolling_on.models.lucky_card import format_owned_lucky_cards
from zephie_rolling_on.models.lucky_deck import (
    clear_deck_pool_for_round_end,
    is_new_round_dice_ready,
    is_round_end_by_start,
    load_planner_calibration,
    sync_deck_from_new_hand_cards,
)
from zephie_rolling_on.data.map_loader import load_map_from_xlsx
from zephie_rolling_on.data.auto_click_config import (
    SKIP_ALL,
    SKIP_EXCEPT_HAMMER,
    SKIP_NONE,
    normalize_skip_exclamation_mode,
    load_same_cell_retry_after,
    load_skip_animation_via_f12,
)
from zephie_rolling_on.executor import actions as executor_actions
from zephie_rolling_on.models.map_board import MapBoard
from zephie_rolling_on.app.model_decide import suggest_action_via_model

from zephie_rolling_on.vision.capture import (
    capture_screen,
    capture_window_client,
    crop_region,
)
from zephie_rolling_on.vision.adventure_complete_banner import find_adventure_complete_banner
from zephie_rolling_on.vision.confirm_button import find_confirm_button
from zephie_rolling_on.vision.execute_task_button import find_execute_task_button
from zephie_rolling_on.vision.lucky_match import slot_center_in_client
from zephie_rolling_on.vision.recognize import (
    RecognitionResult,
    format_recognition_timing,
    is_cell_display_active,
    load_regions,
    recognize_cell_and_dice,
    recognize_cell_id_on_frame,
    recognize_frame,
    should_log_recognition_timing,
)
from zephie_rolling_on.vision.roll_kind import find_roll_kind

PROJECT_ROOT = project_root()
DEFAULT_MAP = PROJECT_ROOT / "map.xlsx"


class ScriptSession:
    """后台识别与建议/操作循环。"""

    #: 最近一次循环内停止的原因类型；``"occluded"`` 表示画面被遮挡。
    _stop_reason_kind: str = ""

    @property
    def stop_reason_kind(self) -> str:
        return self._stop_reason_kind

    def take_stop_reason_kind(self) -> str:
        """读取并清空停止原因，避免上一次的原因被后续停止重复消费。"""
        kind = self._stop_reason_kind
        self._stop_reason_kind = ""
        return kind

    def __init__(
        self,
        *,
        map_path: Path = DEFAULT_MAP,
        runtime: RuntimeState | None = None,
        on_log: Callable[[str], None] | None = None,
        on_lucky_cards: Callable[[str], None] | None = None,
        on_cell: Callable[[int | None], None] | None = None,
        on_calibration_changed: Callable[[], None] | None = None,
        on_round_progress: Callable[[int, int], None] | None = None,
        on_auto_stopped: Callable[[], None] | None = None,
    ) -> None:
        self.map_path = map_path
        self.runtime = runtime or RuntimeState.bootstrap_from_disk()
        self.on_log = on_log or (lambda _msg: None)
        self.on_lucky_cards = on_lucky_cards or (lambda _text: None)
        self.on_cell = on_cell or (lambda _cell_id: None)
        self.on_calibration_changed = on_calibration_changed or (lambda: None)
        self.on_round_progress = on_round_progress or (lambda _c, _t: None)
        self.on_auto_stopped = on_auto_stopped or (lambda: None)
        self._stop = threading.Event()  # 终止后台循环（停止/关闭程序）
        self._stop_requested = threading.Event()  # 停止请求（立即生效）
        self._thread: threading.Thread | None = None
        self.board: MapBoard | None = None
        self.hwnd: int | None = None
        self.auto_click = False
        self.regions: dict = {}
        # 本地维护的「已有幸运卡」清单（用卡即减、获卡即增）；None 表示尚未基线
        self._local_hand: list[str] | None = None
        # 上一次执行操作所在格子；用于「识别到格子变化」检查点
        self._last_acted_cell: int | None = None
        # 操作后连续识别到同一格的次数（满阈值则重放上次点击）
        self._same_cell_streak: int = 0
        # 最近一次已执行的决策动作（同格重试时只重放，不重算）
        self._last_action: dict | None = None
        # 已检测到冒险完成横幅，待点确认后再清卡池/计轮次
        self._adventure_complete_seen = False
        # 本局结束后的卡池清空 / 轮次计数是否已处理
        self._round_end_finalized = False
        # 多轮自动点击：目标轮数、已完成轮数、等待新局骰子重置
        self._rounds_target = 1
        self._rounds_completed = 0
        self._waiting_new_round_dice = False
        self.skip_exclamation_mode = SKIP_ALL
        self.auto_replenish_dice = True
        self._exclamation_skip_handled = False
        self._adventure_complete_debug_saved = False

    def start(
        self,
        hwnd: int,
        auto_click: bool,
        *,
        rounds_target: int = 1,
        skip_exclamation_mode: str = SKIP_ALL,
        auto_replenish_dice: bool = True,
    ) -> None:
        if self._thread and self._thread.is_alive():
            self.on_log("脚本已在运行。")
            return
        if not hwnd:
            self.on_log("请先绑定游戏窗口。")
            return

        self.hwnd = hwnd
        self.auto_click = auto_click
        self.board = load_map_from_xlsx(self.map_path)
        with use_runtime(self.runtime):
            # 必须在 runtime 下解析，否则大冒险锚点会读成共享 yaml
            self.regions = load_regions()
        self._local_hand = None
        self._last_acted_cell = None
        self._same_cell_streak = 0
        self._last_action = None
        self._adventure_complete_seen = False
        self._round_end_finalized = False
        self._rounds_target = max(1, int(rounds_target))
        self._rounds_completed = 0
        self._waiting_new_round_dice = False
        self.skip_exclamation_mode = normalize_skip_exclamation_mode(
            skip_exclamation_mode
        )
        self.auto_replenish_dice = bool(auto_replenish_dice)
        self._exclamation_skip_handled = False
        self._adventure_complete_debug_saved = False
        self._stop.clear()
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        mode = "自动点击" if auto_click else "仅建议"
        self.on_log(f"脚本已启动（{mode}）")
        if auto_click:
            self.on_round_progress(0, self._rounds_target)

    def stop(self) -> None:
        """立即停止后台循环。"""
        if not self.is_running():
            self.on_log("脚本未在运行。")
            return
        self._stop_requested.set()
        self._stop.set()
        self.on_log("[停止] 已立即停止。")
        if self._thread:
            self._thread.join(timeout=5.0)
        self._thread = None
        self._stop_requested.clear()

    def force_stop(self) -> None:
        """硬停止：立即终止线程（关闭程序时用）。"""
        self._stop_requested.set()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        self._thread = None
        self._stop_requested.clear()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_stopping(self) -> bool:
        return self.is_running() and self._stop_requested.is_set()

    def _run_loop(self) -> None:
        assert self.board is not None
        interval = float(self.regions.get("poll_interval_seconds", 2.0))

        with use_runtime(self.runtime):
            while not self._stop.is_set():
                try:
                    if self._stop_requested.is_set():
                        break
                    if self.auto_click:
                        if not self._auto_click_step():
                            break
                    else:
                        result = run_tick(self.board, self.regions, hwnd=self.hwnd)
                        rec = result.recognition
                        if rec is not None:
                            self.on_lucky_cards(
                                format_owned_lucky_cards(rec.lucky_card_ids),
                            )
                        for line in result.log_lines:
                            self.on_log(line)
                except Exception as exc:
                    self.on_log(f"[错误] {exc}")

                if self._stop.wait(interval):
                    break

    def _capture_frame(self):
        if self.hwnd:
            return capture_window_client(self.hwnd)
        return capture_screen()

    def _log_recognition_timing(
        self,
        tag: str,
        rec: RecognitionResult,
        *,
        capture_ms: float | None = None,
    ) -> None:
        if not should_log_recognition_timing():
            return
        parts: list[str] = []
        if capture_ms is not None:
            parts.append(f"截屏={capture_ms:.0f}ms")
        timing_line = format_recognition_timing(rec.timing_ms)
        if timing_line:
            parts.append(timing_line)
        if parts:
            self.on_log(f"[耗时·{tag}] {' '.join(parts)}")

    def _log_timing_parts(self, tag: str, parts: list[str]) -> None:
        if not should_log_recognition_timing() or not parts:
            return
        self.on_log(f"[耗时·{tag}] {' '.join(parts)}")

    def _is_dim(self, frame) -> bool:
        cell_region = self.regions.get("cell_id_region", {})
        cell_patch = (
            crop_region(frame, cell_region) if cell_region.get("width") else None
        )
        return (
            cell_patch is None
            or cell_patch.size == 0
            or not is_cell_display_active(cell_patch, self.regions)
        )

    def _auto_click_step(self) -> bool:
        """单步：变灰→确认/执行任务；正常→格子变化后规划投骰/用卡。

        返回 False 表示收到停止请求，循环应退出。
        """
        if self._stop.is_set() or self._stop_requested.is_set():
            return False

        timing_on = should_log_recognition_timing()
        capture_ms: float | None = None
        if timing_on:
            t_cap = time.perf_counter()
        frame = self._capture_frame()
        if timing_on:
            capture_ms = (time.perf_counter() - t_cap) * 1000.0
        if frame is None:
            from zephie_rolling_on.vision.capture import (
                capture_block_reason,
                last_capture_failure_note,
            )

            if capture_block_reason() == "occluded":
                self._request_stop_from_loop(
                    last_capture_failure_note() or "游戏画面被遮挡",
                    kind="occluded",
                )
                return False
            self.on_log("[截屏] 无法截取游戏窗口")
            return True

        if self._is_dim(frame):
            # 变灰：优先确认并点击；H 格执行任务则自动跳过或停止脚本。
            self._handle_dim_overlay(frame, capture_ms=capture_ms)
            return True

        self._exclamation_skip_handled = False
        self._adventure_complete_seen = False
        self._adventure_complete_debug_saved = False

        return self._handle_planner_action(frame, capture_ms=capture_ms)

    def _handle_dim_overlay(
        self, frame, *, capture_ms: float | None = None
    ) -> None:
        """画面变灰：冒险完成横幅→点确认→清卡池/计轮次；其余灰屏仍点确认；H 格执行任务。"""
        timing_on = should_log_recognition_timing()
        parts: list[str] = []
        if capture_ms is not None:
            parts.append(f"截屏={capture_ms:.0f}ms")

        # 刷新结束横幅调试图（与 execute_task 同搜索区）；每段灰屏只写一次
        if not self._adventure_complete_debug_saved:
            try:
                from zephie_rolling_on.vision.recognize import _load_dev_config_cached
                from zephie_rolling_on.vision.popup_button_debug import (
                    save_adventure_complete_banner_debug,
                )

                if bool(_load_dev_config_cached().get("one_click_test_save_debug", False)):
                    save_adventure_complete_banner_debug(frame, self.regions)
            except Exception:
                pass
            self._adventure_complete_debug_saved = True

        t0 = time.perf_counter() if timing_on else 0.0
        complete = find_adventure_complete_banner(frame, self.regions)
        if timing_on:
            parts.append(f"冒险完成横幅={(time.perf_counter() - t0) * 1000.0:.0f}ms")

        if complete is not None and not self._adventure_complete_seen:
            self._adventure_complete_seen = True
            self.on_log(
                f"[自动] 检测到冒险完成横幅(score={complete.score:.2f})，"
                f"仅点确认（本局结束改由亮屏 START 判定）"
                f" ({complete.center_x}, {complete.center_y})",
            )

        t0 = time.perf_counter() if timing_on else 0.0
        confirm = find_confirm_button(frame, self.regions)
        if timing_on:
            parts.append(f"确认按钮={(time.perf_counter() - t0) * 1000.0:.0f}ms")

        if confirm is not None:
            self._log_timing_parts("灰屏模板", parts)
            self.on_log(
                f"[自动] 检测到确认按钮(score={confirm.score:.2f})，"
                f"点击 ({confirm.center_x}, {confirm.center_y})",
            )
            executor_actions.click_confirm_button(
                self.hwnd,
                confirm.center_x,
                confirm.center_y,
                on_log=self.on_log,
            )
            # 本局结束仅由亮屏 START 判定；灰屏确认不再 finalize
            return

        if self._adventure_complete_seen and not self._round_end_finalized:
            self._log_timing_parts("灰屏模板", parts)
            return

        t0 = time.perf_counter() if timing_on else 0.0
        task = find_execute_task_button(frame, self.regions)
        if timing_on:
            parts.append(f"执行任务按钮={(time.perf_counter() - t0) * 1000.0:.0f}ms")
        self._log_timing_parts("灰屏模板", parts)

        if task is not None:
            mode = self.skip_exclamation_mode

            if mode == SKIP_NONE:
                # 不跳过任何奖励：脚本无法处理这个弹窗，停下来交给使用者。
                self._request_stop_from_loop("不跳过感叹号格奖励")

            elif mode == SKIP_ALL:
                # 跳过所有奖励：无需再检测任何东西。
                if not self._exclamation_skip_handled:
                    self._exclamation_skip_handled = True
                    self.on_log("[自动] 跳过感叹号格奖励")
                    executor_actions.skip_exclamation_reward(
                        self.hwnd,
                        on_log=self.on_log,
                    )

            elif mode == SKIP_EXCEPT_HAMMER:
                # 不跳过白金锤子：只有真的检测到锤子才停，否则照常跳过。
                from zephie_rolling_on.vision.platinum_hammer import (
                    find_platinum_hammer,
                )

                if find_platinum_hammer(frame, self.regions) is not None:
                    self._request_stop_from_loop("检测到白金锤子")
                elif not self._exclamation_skip_handled:
                    self._exclamation_skip_handled = True
                    self.on_log("[自动] 未检测到白金锤子，跳过感叹号格奖励")
                    executor_actions.skip_exclamation_reward(
                        self.hwnd,
                        on_log=self.on_log,
                    )
            return

    def _handle_planner_action(
        self, frame, *, capture_ms: float | None = None
    ) -> bool:
        """画面正常：识别 → 格子变化后同步卡池、规划并操作。

        返回 False 表示收到停止请求，循环应退出。
        """
        if self._stop_requested.is_set():
            return False

        dice_total = int(self.regions.get("dice_total", 100))
        timing_logged = False

        def _log_rec(tag: str, rec: RecognitionResult) -> None:
            nonlocal timing_logged
            cap = capture_ms if not timing_logged else None
            self._log_recognition_timing(tag, rec, capture_ms=cap)
            if cap is not None:
                timing_logged = True

        if self._waiting_new_round_dice:
            rec = recognize_cell_and_dice(
                frame,
                self.regions,
                hand_ids=list(self._local_hand or []),
                anchor_cell_id=self._last_acted_cell,
            )
            _log_rec("新局骰子", rec)
            if is_new_round_dice_ready(
                dice_used=rec.dice_used,
                dice_remaining=rec.dice_remaining,
                dice_total=dice_total,
            ):
                self._waiting_new_round_dice = False
                self._round_end_finalized = False
                self._adventure_complete_seen = False
                if self.auto_replenish_dice:
                    from zephie_rolling_on.executor.dice_replenish import (
                        run_dice_detect_and_replenish,
                    )

                    result = run_dice_detect_and_replenish(
                        int(self.hwnd) if self.hwnd else 0,
                        self.regions,
                        enabled=True,
                        frame=frame,
                        on_log=None,
                    )
                    if not result.ok:
                        self._request_stop_from_loop(result.message)
                        return False
                if rec.cell_id is not None:
                    self.on_cell(rec.cell_id)
                # 补充点击后画面可能已变，下一 poll 再完整识别规划
                return True
            if rec.cell_id is not None:
                self.on_cell(rec.cell_id)
            return True

        # 亮屏 + START 按钮 → 本局结束，立刻开新局（与骰子数无关）
        hit = find_roll_kind(frame, self.regions)
        if hit is not None and hit.kind == "start":
            return self._end_round_on_start_and_begin_new(
                score=hit.score,
            )
        # 复用同一次检测的按钮状态：决策前的零骰检查要区分免费/付费骰
        # （免费骰表示「还能再投一次」，此时不该停机）
        roll_kind = hit.kind if hit is not None else None

        if self._last_acted_cell is not None:
            quick = recognize_cell_id_on_frame(
                frame,
                self.regions,
                anchor_cell_id=self._last_acted_cell,
            )
            _log_rec("同格快检", quick)
            if quick.cell_id == self._last_acted_cell:
                if quick.cell_id is not None:
                    self.on_cell(quick.cell_id)
                if self._should_retry_after_same_cell(quick.cell_id):
                    return self._replay_last_action()
                return True
            self._same_cell_streak = 0
            # 亮屏且格子已变化：决策前先查可用骰子，再跑骰子/卡牌识别
            if not self._gate_inventory_dice_before_decision(
                frame, roll_kind=roll_kind
            ):
                return False

        else:
            # 首步：先确认格子，再查可用骰子，最后完整识别骰子/卡牌
            quick = recognize_cell_id_on_frame(
                frame,
                self.regions,
                anchor_cell_id=None,
            )
            _log_rec("首步格子", quick)
            if quick.cell_id is None:
                return self._recalib_frame_on_cell_miss()
            self.on_cell(quick.cell_id)
            if not self._gate_inventory_dice_before_decision(
                frame, roll_kind=roll_kind
            ):
                return False

        rec = recognize_frame(
            frame,
            self.regions,
            save_debug=False,
            anchor_cell_id=self._last_acted_cell,
        )
        _log_rec("完整识别", rec)
        if self._stop_requested.is_set():
            return False

        cell_id = rec.cell_id
        if cell_id is not None:
            self.on_cell(cell_id)
        if cell_id is None:
            # 亮屏但格子号未读出：跑一套大冒险框标定；两次都失败则停止脚本
            return self._recalib_frame_on_cell_miss()
        if cell_id == self._last_acted_cell:
            # 上一次操作结果尚未导致格子变化（同一格）
            if self._should_retry_after_same_cell(cell_id):
                return self._replay_last_action()
            return True
        self._same_cell_streak = 0

        ocr_hand = list(rec.lucky_card_ids)
        self.on_lucky_cards(format_owned_lucky_cards(ocr_hand))
        self._reconcile_deck(ocr_hand)

        if is_round_end_by_start(roll_kind=rec.roll_kind):
            return self._end_round_on_start_and_begin_new(
                score=rec.roll_kind_score,
            )

        return self._plan_and_act(rec, cell_id=cell_id)

    def _end_round_on_start_and_begin_new(
        self, *, score: float | None = None
    ) -> bool:
        """检测到 START：结算本局，等局末动画后点两次开新局，再等新局骰子。

        局末结算动画无法跳过，因此**必须先等**（``new_round_pre_click_wait_ms``）
        再点；早先这里直接点击，导致两次点击落在动画上而开不出新局。
        """
        _ = score
        if not self._finalize_round_on_adventure_complete():
            return False
        self.on_log("[轮次] 本局已结束，准备开启新局…")
        executor_actions.start_new_round(self.hwnd, on_log=self.on_log)
        self._waiting_new_round_dice = True
        return True

    def _is_round_in_progress(self) -> bool:
        """本轮仍在进行（未结算完、未在等新局骰子重置）。"""
        return not self._waiting_new_round_dice and not self._round_end_finalized

    def _gate_inventory_dice_before_decision(
        self, frame, *, roll_kind: str | None = None
    ) -> bool:
        """决策前检测可用骰子（与是否开启自动补充无关）。

        只有**本轮进行中、可用骰子为 0、且投骰按钮为付费骰**三者同时成立
        才停机。

        免费骰（``roll_kind == "free"``）说明最后一次投骰触发了免费投掷，
        即使可用骰子为 0 也还能再投一次 —— 此时停机是错的，这是本检查唯一
        要放行的情形。手牌不参与判断：靠不消耗骰子的卡牌续投不是好决策，
        留给使用者补充骰子后继续。

        ``roll_kind`` 为 ``None``（按钮未识别）时按付费处理，维持保守停机。
        返回 False 表示退出循环。OCR 失败不据此停脚本。
        """
        if not self._is_round_in_progress():
            return True
        from zephie_rolling_on.vision.inventory_dice import recognize_inventory_dice

        inv = recognize_inventory_dice(frame, self.regions)
        self.on_log(
            f"[可用骰子·决策前] A={inv.part_a} B={inv.part_b} 合计={inv.total}"
            f"（{inv.notes}）"
        )
        if inv.total is None:
            self.on_log("[可用骰子·决策前] 识别失败，跳过零骰停机检查")
            return True
        if int(inv.total) > 0:
            return True
        if roll_kind == "free":
            self.on_log(
                "[可用骰子·决策前] 可用骰子为 0，但投骰按钮为免费骰，"
                "仍可再投一次，继续"
            )
            return True
        label = {"paid": "付费骰", "start": "START"}.get(
            roll_kind or "", "未识别（按付费处理）"
        )
        self._request_stop_from_loop(
            f"本轮未结束、可用骰子数为 0，投骰按钮={label}，已停止脚本"
        )
        return False

    def _should_retry_after_same_cell(self, cell_id: int | None) -> bool:
        """操作后连续同格：未达阈值则等待；达阈值则应重放上次动作。"""
        limit = load_same_cell_retry_after()
        self._same_cell_streak += 1
        if self._same_cell_streak < limit:
            return False
        self.on_log(
            f"[重试] 操作后连续 {self._same_cell_streak} 次仍在格子 "
            f"{cell_id}，判定未点上，重放上次操作（不重算）…",
        )
        self._same_cell_streak = 0
        return True

    def _replay_last_action(self) -> bool:
        """重放上一次决策的点击；状态未变，不调用规划。"""
        action = self._last_action
        if not action:
            self.on_log("[重试] 无上次操作可重放，跳过")
            return True
        label = str(action.get("label") or action.get("action_id") or "动作")
        self.on_log(f"[重试] 重放：{label}")
        acted = self._execute_action_payload(action, adjust_hand=False)
        if acted and self._last_acted_cell is not None:
            # 仍停在同格检查点，等下一轮看是否挪动
            self._same_cell_streak = 0
        return True

    def _recalib_frame_on_cell_miss(self) -> bool:
        """格子号失败：跑一套大冒险框标定；成功则等下一轮，两次失败则停止脚本。

        「一套标定」= 检测开关按钮 → 框标定；失败则点一次开关、等 50ms 再标一次。
        返回 False 表示应退出循环。
        """
        if not self.hwnd:
            self._request_stop_from_loop("格子识别失败且无窗口")
            return False
        self.on_log("[自动] 格子识别失败，执行一次大冒险框标定…")
        from zephie_rolling_on.vision.adventure_bootstrap import run_adventure_bootstrap

        boot = run_adventure_bootstrap(int(self.hwnd), on_log=self.on_log)
        if boot.ok:
            self.regions = load_regions()
            self.on_log(
                f"[自动·标定] 成功（第{boot.calib_attempt}次）：{boot.message}；"
                "等待下一轮识别"
            )
            return True
        _ = boot.message
        self._request_stop_from_loop("大冒险框标定失败")
        return False

    def _notify_calibration_changed(self) -> None:
        self.on_calibration_changed()

    def _plan_and_act(self, rec: RecognitionResult, *, cell_id: int) -> bool:
        """根据识别结果规划并执行投骰/用卡。"""
        state = build_state(rec)
        if rec.dice_remaining is not None:
            self._notify_calibration_changed()
        advice_text, rank = suggest_action_via_model(self.board, state)
        rk = rec.roll_kind or "?"
        self.on_log(
            f"[识别] 格子={cell_id}"
            f"｜投骰={rk}"
            f"｜手牌={format_owned_lucky_cards(self._local_hand or [])}",
        )
        if rank is None or not rank.actions:
            self.on_log(f"[建议] {advice_text}")
            return True

        # 建议串已含策略标签；智能决策时再点明当前臂
        self.on_log(f"[建议] {advice_text}")
        best = rank.actions[0]

        payload = self._build_action_payload(best.action_id, best.label, rec)
        if payload is None:
            self.on_log(f"[操作] 未识别的动作 {best.action_id}，跳过")
            return True
        payload["score"] = float(best.score)
        payload["detail"] = str(best.detail or "")

        acted = self._execute_action_payload(payload, adjust_hand=True)
        if acted:
            self._last_action = payload
            self._last_acted_cell = cell_id
            self._same_cell_streak = 0
        return True

    def _build_action_payload(
        self,
        action_id: str,
        label: str,
        rec: RecognitionResult,
    ) -> dict | None:
        """Build a replayable click action dict."""
        if action_id == "roll_normal":
            return {
                "action_id": "roll_normal",
                "label": label or "普通投骰",
            }
        if action_id.startswith("use_"):
            card_id = action_id[len("use_") :]
            center = self._lucky_slot_center(rec, card_id)
            if center is None:
                self.on_log(f"[操作] 未在卡槽定位到 {card_id}，改投普通骰子")
                return {
                    "action_id": "roll_normal",
                    "label": "普通投骰（卡槽未定位）",
                    "fallback_from": card_id,
                }
            return {
                "action_id": action_id,
                "label": label or f"使用 {card_id}",
                "card_id": card_id,
                "slot_x": int(center[0]),
                "slot_y": int(center[1]),
            }
        return None

    def _execute_action_payload(
        self,
        payload: dict,
        *,
        adjust_hand: bool,
    ) -> bool:
        """执行/重放一次点击。重放时 adjust_hand=False，避免手牌重复扣减。"""
        action_id = str(payload.get("action_id") or "")
        label = str(payload.get("label") or "").strip()
        score = float(payload.get("score") or 0.0)
        detail = str(payload.get("detail") or "")

        def _log_op(text: str) -> None:
            if detail:
                self.on_log(f"[操作] {text}｜价值 {score:.1f}｜{detail}")
            else:
                self.on_log(f"[操作] {text}｜价值 {score:.1f}")

        if action_id == "roll_normal":
            ok = executor_actions.throw_normal_dice(self.hwnd, on_log=self.on_log)
            if ok:
                _log_op(label or "普通投骰")
            return ok
        if action_id.startswith("use_"):
            card_id = str(payload.get("card_id") or action_id[len("use_") :])
            sx = payload.get("slot_x")
            sy = payload.get("slot_y")
            if sx is None or sy is None:
                self.on_log(f"[操作] 缺少卡槽坐标，无法使用 {card_id}")
                return False
            ok = executor_actions.use_lucky_card(
                self.hwnd, int(sx), int(sy), on_log=self.on_log
            )
            if ok:
                _log_op(label or f"使用 {card_id}")
                if adjust_hand and self._local_hand and card_id in self._local_hand:
                    self._local_hand.remove(card_id)
            return ok
        self.on_log(f"[操作] 未识别的动作 {action_id}，跳过")
        return False

    def _finalize_round_on_adventure_complete(self) -> bool:
        """冒险完成横幅：清空卡池、计轮次。返回是否继续下一局（未达轮数上限）。"""
        if self._round_end_finalized:
            return self._rounds_completed < self._rounds_target
        cal = clear_deck_pool_for_round_end()
        self._round_end_finalized = True
        self._local_hand = []
        self.on_lucky_cards(format_owned_lucky_cards([]))
        _ = cal
        self.on_log("[本局结束] 已清空幸运卡池与手牌")
        self._notify_calibration_changed()
        self._rounds_completed += 1
        self.on_round_progress(self._rounds_completed, self._rounds_target)
        self.on_log(
            f"[轮次] 已完成 {self._rounds_completed}/{self._rounds_target} 局",
        )
        if self._rounds_completed >= self._rounds_target:
            self._request_stop_from_loop("已达设定轮数")
            return False
        self._reset_session_for_new_round()
        return True

    def _reset_session_for_new_round(self) -> None:
        """开新局前重置局内状态（卡池已在结束时清空）。"""
        self._local_hand = None
        self._last_acted_cell = None
        self._same_cell_streak = 0
        self._last_action = None

    def _request_stop_from_loop(self, reason: str, *, kind: str = "") -> None:
        """后台循环内请求停止（不 join，避免死锁）。

        ``kind`` 供界面区分是否需要额外提示（例如 ``"occluded"``）。
        """
        if self._stop_requested.is_set():
            return
        self._stop_reason_kind = kind
        self._stop_requested.set()
        self._waiting_new_round_dice = False
        self._stop.set()
        self.on_log(f"[停止] {reason}，自动点击已停止。")
        self.on_auto_stopped()

    def _reconcile_deck(self, ocr_hand: list[str]) -> None:
        """用「本地手牌（含上次用卡后状态）」对比 OCR 手牌，识别新获得的卡并记入卡池。"""
        if self._local_hand is None:
            self._local_hand = list(ocr_hand)
            self.on_log(
                f"[手牌] 基线：{format_owned_lucky_cards(self._local_hand)}",
            )
            return
        gained = sync_deck_from_new_hand_cards(self._local_hand, ocr_hand)
        self._local_hand = list(ocr_hand)
        if gained:
            cal = load_planner_calibration()
            self.on_log(
                f"[卡池] 新获得 {format_owned_lucky_cards(gained)}，已记入卡池；"
                f"{cal.summary_line()}",
            )
            self._notify_calibration_changed()

    def _lucky_slot_center(
        self,
        rec: RecognitionResult,
        card_id: str,
    ) -> tuple[int, int] | None:
        lucky_region = self.regions.get("lucky_cards_region", {})
        if not lucky_region.get("width"):
            return None
        match = next(
            (m for m in rec.lucky_matches if m.card_id == card_id), None
        )
        if match is None:
            return None
        return slot_center_in_client(match.slot_index, lucky_region, self.regions)
