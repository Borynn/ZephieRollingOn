"""Adventure UI bootstrap: toggle locate → frame calib (one retry)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from zephie_rolling_on.data.adventure_frame import load_anchor, save_anchor
from zephie_rolling_on.data.auto_click_config import (
    BLOCKED_SKIP_ANIMATION_SIZE,
    set_skip_animation_resolution_block,
)
from zephie_rolling_on.vision.adventure_calib import detect_adventure_frame_anchor
from zephie_rolling_on.vision.capture import capture_window_client, last_capture_failure_note
from zephie_rolling_on.vision.toggle_adventure_btn import detect_and_update_toggle_button

# 第一次标定失败后：点一次开关按钮，再等这么久后重标
_RETRY_CLICK_WAIT_SEC = 0.05

# 开关按钮检测被跳过时的占位说明
_TOGGLE_SKIPPED_MSG = "未检测（分辨率屏蔽时跳过）"


@dataclass(frozen=True)
class AdventureBootstrapResult:
    ok: bool
    toggle_ok: bool
    toggle_msg: str
    calib_ok: bool
    calib_msg: str
    calib_attempt: int  # 成功或最终失败时的尝试次数（1 或 2）
    message: str
    toggle_checked: bool = True  # False 表示本次未做开关按钮检测
    resolution_notice: str = ""  # 分辨率触发的自动屏蔽说明（若有）


def resolution_skip_animation_notice(width: int, height: int) -> str:
    """该分辨率下需要自动屏蔽跳动画时返回提示语，否则返回空字符串。"""
    if (int(width), int(height)) == BLOCKED_SKIP_ANIMATION_SIZE:
        return (
            f"检测到游戏解析度为{BLOCKED_SKIP_ANIMATION_SIZE[0]}*"
            f"{BLOCKED_SKIP_ANIMATION_SIZE[1]}，自动屏蔽投骰/用卡后跳过动画"
        )
    return ""


def apply_resolution_skip_animation_block(
    hwnd: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> str:
    """按当前游戏客户区分辨率决定是否屏蔽跳动画；返回提示语（可能为空）。

    只影响本次运行，不改动用户的勾选状态。
    """
    from zephie_rolling_on.vision.win32_window import _client_size_screen_pixels

    log = on_log or (lambda _m: None)
    try:
        _x, _y, w, h = _client_size_screen_pixels(int(hwnd))
    except Exception:  # noqa: BLE001
        return ""
    notice = resolution_skip_animation_notice(w, h)
    set_skip_animation_resolution_block(bool(notice))
    if notice:
        log(f"[跳过动画] {notice}")
    return notice


def _calib_from_frame(frame) -> tuple[bool, str]:
    result = detect_adventure_frame_anchor(frame)
    if not result.ok or result.anchor_left is None:
        return False, result.message

    new_left, new_top = int(result.anchor_left), int(result.anchor_top)
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


def _click_toggle_once(
    hwnd: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    from zephie_rolling_on.executor.actions import _click_toggle_adventure_ui

    return bool(_click_toggle_adventure_ui(int(hwnd), on_log=on_log))


def run_adventure_bootstrap(
    hwnd: int,
    *,
    on_log: Callable[[str], None] | None = None,
    skip_toggle_when_blocked: bool = False,
) -> AdventureBootstrapResult:
    """标定大冒险框；失败则点击一次开关按钮并重标一次。

    ``skip_toggle_when_blocked=True`` 时，**仅在**分辨率触发跳动画屏蔽（见
    ``BLOCKED_SKIP_ANIMATION_SIZE``）的情况下跳过开关按钮的模板检测（该步骤要跑
    多尺度整屏匹配，较慢，且会写回 click_targets.yaml）。其余分辨率照常检测——
    跳动画依赖该坐标，不检测就失去了自动修正的机会。
    """
    log = on_log or (lambda _m: None)
    hwnd = int(hwnd)

    resolution_notice = apply_resolution_skip_animation_block(hwnd, on_log=on_log)
    blocked_by_resolution = bool(resolution_notice)
    detect_toggle = not (blocked_by_resolution and skip_toggle_when_blocked)

    frame = capture_window_client(hwnd)
    if frame is None:
        note = last_capture_failure_note()
        msg = f"截屏失败，无法标定。{note}"
        log(f"[标定流程] {msg}")
        return AdventureBootstrapResult(
            ok=False,
            toggle_ok=False,
            toggle_msg="未检测（截屏失败）",
            calib_ok=False,
            calib_msg=msg,
            calib_attempt=1,
            message=msg,
            toggle_checked=detect_toggle,
            resolution_notice=resolution_notice,
        )

    toggle_ok = False
    toggle_msg = _TOGGLE_SKIPPED_MSG
    if detect_toggle:
        toggle = detect_and_update_toggle_button(hwnd, frame=frame, on_log=on_log)
        toggle_ok = bool(toggle.ok)
        toggle_msg = toggle.message
        log(f"[标定流程·开关按钮] {'成功' if toggle_ok else '失败'}：{toggle_msg}")
    else:
        log(f"[标定流程] 分辨率 {BLOCKED_SKIP_ANIMATION_SIZE[0]}×"
            f"{BLOCKED_SKIP_ANIMATION_SIZE[1]}，跳过开关按钮检测")

    calib_ok, calib_msg = _calib_from_frame(frame)
    if calib_ok:
        summary = (
            f"标定成功（第1次）。开关：{toggle_msg}；标定：{calib_msg}"
        )
        log(f"[标定流程] {summary}")
        return AdventureBootstrapResult(
            ok=True,
            toggle_ok=toggle_ok,
            toggle_msg=toggle_msg,
            calib_ok=True,
            calib_msg=calib_msg,
            calib_attempt=1,
            message=summary,
            toggle_checked=detect_toggle,
            resolution_notice=resolution_notice,
        )

    log(f"[标定流程] 第1次标定失败：{calib_msg}；将点击一次开关按钮后重试…")
    clicked = _click_toggle_once(hwnd, on_log=on_log)
    if not clicked:
        summary = (
            f"第1次标定失败，且点击开关按钮失败，不再重试。"
            f"开关检测：{toggle_msg}；标定：{calib_msg}"
        )
        log(f"[标定流程] {summary}")
        return AdventureBootstrapResult(
            ok=False,
            toggle_ok=toggle_ok,
            toggle_msg=toggle_msg,
            calib_ok=False,
            calib_msg=calib_msg,
            calib_attempt=1,
            message=summary,
            toggle_checked=detect_toggle,
            resolution_notice=resolution_notice,
        )

    time.sleep(_RETRY_CLICK_WAIT_SEC)
    frame2 = capture_window_client(hwnd)
    if frame2 is None:
        note = last_capture_failure_note()
        summary = (
            f"已点击开关按钮并等待 {_RETRY_CLICK_WAIT_SEC * 1000:.0f}ms，"
            f"但第2次截屏失败，停止标定。{note}；第1次：{calib_msg}"
        )
        log(f"[标定流程] {summary}")
        return AdventureBootstrapResult(
            ok=False,
            toggle_ok=toggle_ok,
            toggle_msg=toggle_msg,
            calib_ok=False,
            calib_msg=summary,
            calib_attempt=2,
            message=summary,
            toggle_checked=detect_toggle,
            resolution_notice=resolution_notice,
        )

    calib_ok2, calib_msg2 = _calib_from_frame(frame2)
    if calib_ok2:
        summary = (
            f"点击开关后第2次标定成功。"
            f"开关：{toggle_msg}；标定：{calib_msg2}"
        )
        log(f"[标定流程] {summary}")
        return AdventureBootstrapResult(
            ok=True,
            toggle_ok=toggle_ok,
            toggle_msg=toggle_msg,
            calib_ok=True,
            calib_msg=calib_msg2,
            calib_attempt=2,
            message=summary,
            toggle_checked=detect_toggle,
            resolution_notice=resolution_notice,
        )

    summary = (
        f"两次标定均失败，停止。"
        f"第1次：{calib_msg}；第2次：{calib_msg2}；"
        f"开关检测：{toggle_msg}"
    )
    log(f"[标定流程] {summary}")
    return AdventureBootstrapResult(
        ok=False,
        toggle_ok=toggle_ok,
        toggle_msg=toggle_msg,
        calib_ok=False,
        calib_msg=calib_msg2,
        calib_attempt=2,
        message=summary,
        toggle_checked=detect_toggle,
        resolution_notice=resolution_notice,
    )
