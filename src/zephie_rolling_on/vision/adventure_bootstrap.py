"""Adventure UI bootstrap: toggle locate → frame calib (one retry)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from zephie_rolling_on.data.adventure_frame import load_anchor, save_anchor
from zephie_rolling_on.data.auto_click_config import (
    BLOCKED_SKIP_ANIMATION_SIZE,
    BLOCKED_SKIP_ANIMATION_SIZE_TOLERANCE,
    click_interval_range_text,
    jittered_click_interval_sec,
    load_click_interval_sec,
    set_skip_animation_resolution_block,
)
from zephie_rolling_on.vision.adventure_calib import detect_adventure_frame_anchor
from zephie_rolling_on.vision.capture import capture_window_client, last_capture_failure_note
from zephie_rolling_on.vision.toggle_adventure_btn import detect_and_update_toggle_button

# 第一次标定失败后：点一次开关按钮，再等**点击间隔**后重标。
# 这里不写死常量：这个等待与脚本里其它两次点击之间的间隔性质相同，用户能在界面上
# 调（auto_click.yaml 的 click_interval_ms）。写死成 50ms 会让该设置在此处失效——
# 用户把间隔调大到 300ms 后，这里仍只等 50ms，界面可能还没收起就重标了。
# 取值见下方 _retry_gap_sec。

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
    # 结构化坐标，供调用方直接展示——不去解析上面的 *_msg 字符串。
    # 两者都是 **100% 标定空间的客户区坐标**（帧来自归一化后的截图）。
    toggle_center: tuple[int, int] | None = None  # 检测到的大冒险开关按钮中心
    anchor: tuple[int, int] | None = None  # 标定得到的大冒险界面框左上角


def resolution_skip_animation_notice(width: int, height: int) -> str:
    """该分辨率下需要自动屏蔽跳动画时返回提示语，否则返回空字符串。

    判定允许 ``BLOCKED_SKIP_ANIMATION_SIZE_TOLERANCE`` 个像素的误差：归一化会先按
    系统缩放做一次重采样，再取整，结果很难**刚好**落到 1366×768（例如 1366.7 会被
    取成 1367）。要求严格相等会让这条判定在真机上随机失效，所以长宽各自容差比对。
    """
    want_w, want_h = BLOCKED_SKIP_ANIMATION_SIZE
    tol = BLOCKED_SKIP_ANIMATION_SIZE_TOLERANCE
    if abs(int(width) - want_w) <= tol and abs(int(height) - want_h) <= tol:
        return (
            f"检测到游戏解析度为{want_w}*{want_h}"
            f"（实测 {int(width)}*{int(height)}）"
            "，自动屏蔽投骰/用卡后跳过动画"
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
    from zephie_rolling_on.vision.scale_norm import baseline_client_size

    log = on_log or (lambda _m: None)
    try:
        _x, _y, w, h = _client_size_screen_pixels(int(hwnd))
    except Exception:  # noqa: BLE001
        return ""
    # BLOCKED_SKIP_ANIMATION_SIZE 是 100% 空间的逻辑分辨率，而上面拿到的是物理像素；
    # 缩放 >100% 时不换算就永远匹配不上（125% 下 1366×768 会报成 1708×960）。
    w, h = baseline_client_size(w, h)
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

    见 :func:`_run_adventure_bootstrap_impl` 的完整说明。

    这里只做一件事：在**唯一出口**把过程中算出的结构化坐标（开关按钮中心、大冒险
    框左上角）塞进结果。这两处是在实现里多个分支中算出的，逐个 return 去补字段
    容易漏（实现里共 6 个 return），所以用闭包收集 + 出口统一 ``replace``。
    """
    coords: dict[str, tuple[int, int] | None] = {"toggle": None, "anchor": None}
    result = _run_adventure_bootstrap_impl(
        hwnd,
        on_log=on_log,
        skip_toggle_when_blocked=skip_toggle_when_blocked,
        coords=coords,
    )
    return replace(
        result,
        toggle_center=coords["toggle"],
        anchor=coords["anchor"],
    )


def _run_adventure_bootstrap_impl(
    hwnd: int,
    *,
    on_log: Callable[[str], None] | None = None,
    skip_toggle_when_blocked: bool = False,
    coords: dict[str, tuple[int, int] | None] | None = None,
) -> AdventureBootstrapResult:
    """标定大冒险框；失败则点击一次开关按钮并重标一次。

    ``skip_toggle_when_blocked=True`` 时，**仅在**分辨率触发跳动画屏蔽（见
    ``BLOCKED_SKIP_ANIMATION_SIZE``）的情况下跳过开关按钮的模板检测（该步骤要跑
    多尺度整屏匹配，较慢，且会写回 click_targets.yaml）。其余分辨率照常检测——
    跳动画依赖该坐标，不检测就失去了自动修正的机会。
    """
    log = on_log or (lambda _m: None)
    hwnd = int(hwnd)
    if coords is None:
        coords = {"toggle": None, "anchor": None}

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
        if toggle_ok and toggle.hit is not None:
            # 坐标在内层的 ToggleBtnHit 上，不在 ToggleBtnDetectResult 上——
            # 直接取 toggle.center_x 会 AttributeError（门禁与 py_compile 都查不到
            # 属性错误，这属于运行时才暴露的一类）。
            coords["toggle"] = (
                int(toggle.hit.center_x),
                int(toggle.hit.center_y),
            )
        log(f"[标定流程·开关按钮] {'成功' if toggle_ok else '失败'}：{toggle_msg}")
    else:
        log(f"[标定流程] 分辨率 {BLOCKED_SKIP_ANIMATION_SIZE[0]}×"
            f"{BLOCKED_SKIP_ANIMATION_SIZE[1]}，跳过开关按钮检测")

    calib_ok, calib_msg = _calib_from_frame(frame)
    if calib_ok:
        coords["anchor"] = load_anchor()
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

    # 等待点击间隔再重标：load_click_interval_sec 优先读 RuntimeState，所以拿到的是
    # 界面上的当前值（不是文件旧值）；jittered 与其它点击间隔保持同样的随机扰动。
    retry_gap_sec = load_click_interval_sec()
    time.sleep(jittered_click_interval_sec(retry_gap_sec))
    frame2 = capture_window_client(hwnd)
    if frame2 is None:
        note = last_capture_failure_note()
        summary = (
            f"已点击开关按钮并等待 {click_interval_range_text(retry_gap_sec)}，"
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
        coords["anchor"] = load_anchor()
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
