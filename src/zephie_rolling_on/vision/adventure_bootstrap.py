"""Adventure UI bootstrap: toggle locate → frame calib (one retry)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from zephie_rolling_on.data.adventure_frame import load_anchor, save_anchor
from zephie_rolling_on.vision.adventure_calib import detect_adventure_frame_anchor
from zephie_rolling_on.vision.capture import capture_window_client, last_capture_failure_note
from zephie_rolling_on.vision.toggle_adventure_btn import detect_and_update_toggle_button

# 第一次标定失败后：点一次开关按钮，再等这么久后重标
_RETRY_CLICK_WAIT_SEC = 0.05


@dataclass(frozen=True)
class AdventureBootstrapResult:
    ok: bool
    toggle_ok: bool
    toggle_msg: str
    calib_ok: bool
    calib_msg: str
    calib_attempt: int  # 成功或最终失败时的尝试次数（1 或 2）
    message: str


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
) -> AdventureBootstrapResult:
    """检测开关按钮 → 标定；失败则点击开关一次并重标一次。"""
    log = on_log or (lambda _m: None)
    hwnd = int(hwnd)

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
        )

    toggle = detect_and_update_toggle_button(hwnd, frame=frame, on_log=on_log)
    log(f"[标定流程·开关按钮] {'成功' if toggle.ok else '失败'}：{toggle.message}")

    calib_ok, calib_msg = _calib_from_frame(frame)
    if calib_ok:
        summary = (
            f"开关按钮→标定成功（第1次）。开关：{toggle.message}；标定：{calib_msg}"
        )
        log(f"[标定流程] {summary}")
        return AdventureBootstrapResult(
            ok=True,
            toggle_ok=bool(toggle.ok),
            toggle_msg=toggle.message,
            calib_ok=True,
            calib_msg=calib_msg,
            calib_attempt=1,
            message=summary,
        )

    log(f"[标定流程] 第1次标定失败：{calib_msg}；将点击一次开关按钮后重试…")
    clicked = _click_toggle_once(hwnd, on_log=on_log)
    if not clicked:
        summary = (
            f"第1次标定失败，且点击开关按钮失败，不再重试。"
            f"开关检测：{toggle.message}；标定：{calib_msg}"
        )
        log(f"[标定流程] {summary}")
        return AdventureBootstrapResult(
            ok=False,
            toggle_ok=bool(toggle.ok),
            toggle_msg=toggle.message,
            calib_ok=False,
            calib_msg=calib_msg,
            calib_attempt=1,
            message=summary,
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
            toggle_ok=bool(toggle.ok),
            toggle_msg=toggle.message,
            calib_ok=False,
            calib_msg=summary,
            calib_attempt=2,
            message=summary,
        )

    calib_ok2, calib_msg2 = _calib_from_frame(frame2)
    if calib_ok2:
        summary = (
            f"点击开关后第2次标定成功。"
            f"开关：{toggle.message}；标定：{calib_msg2}"
        )
        log(f"[标定流程] {summary}")
        return AdventureBootstrapResult(
            ok=True,
            toggle_ok=bool(toggle.ok),
            toggle_msg=toggle.message,
            calib_ok=True,
            calib_msg=calib_msg2,
            calib_attempt=2,
            message=summary,
        )

    summary = (
        f"两次标定均失败，停止。"
        f"第1次：{calib_msg}；第2次：{calib_msg2}；"
        f"开关检测：{toggle.message}"
    )
    log(f"[标定流程] {summary}")
    return AdventureBootstrapResult(
        ok=False,
        toggle_ok=bool(toggle.ok),
        toggle_msg=toggle.message,
        calib_ok=False,
        calib_msg=calib_msg2,
        calib_attempt=2,
        message=summary,
    )
