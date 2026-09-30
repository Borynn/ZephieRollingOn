from __future__ import annotations

from zephie_rolling_on.paths import project_root

import time
from collections.abc import Callable
from pathlib import Path

import yaml

from zephie_rolling_on.data.auto_click_config import (
    click_interval_range_text,
    jittered_click_interval_sec,
    load_click_interval_sec,
    load_new_round_animation_wait_ms,
    load_new_round_pre_click_wait_ms,
    load_skip_animation_via_f12,
    skip_animation_effective,
)
from zephie_rolling_on.executor.interception_click import press_f12_interception
from zephie_rolling_on.executor.win32_click import (
    click_client_game,
    drag_client_game,
    load_click_delivery,
    move_client_cursor,
)
from zephie_rolling_on.executor.mouse_shield import click_shield_guard

PROJECT_ROOT = project_root()
CLICK_TARGETS_PATH = PROJECT_ROOT / "config" / "click_targets.yaml"


def _load_click_targets() -> dict:
    from zephie_rolling_on.data.adventure_frame import resolve_click_targets

    if not CLICK_TARGETS_PATH.is_file():
        return {}
    with CLICK_TARGETS_PATH.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return resolve_click_targets(raw)


def _load_click_targets_raw() -> dict:
    """未做锚点换算的原始配置（用于客户区绝对坐标项）。"""
    if not CLICK_TARGETS_PATH.is_file():
        return {}
    with CLICK_TARGETS_PATH.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw if isinstance(raw, dict) else {}


def _toggle_ui_uses_f12() -> bool:
    """仅 interception 用 F12；其余（默认 postmessage）用点击开关界面。"""
    return load_click_delivery() == "interception"


def _click_toggle_adventure_ui(
    hwnd: int | None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """点击 toggle_adventure_ui（客户区绝对坐标）一次。"""
    log = on_log or (lambda _m: None)
    if not hwnd:
        log("[开关界面] 无 hwnd，无法点击")
        return False
    raw = _load_click_targets_raw()
    pt = raw.get("toggle_adventure_ui")
    if not isinstance(pt, dict) or pt.get("x") is None or pt.get("y") is None:
        log("[开关界面] 未配置 click_targets.toggle_adventure_ui")
        return False
    cx, cy = int(pt["x"]), int(pt["y"])
    ok = click_client_game(int(hwnd), cx, cy, on_log=log)
    if ok:
        log(f"[开关界面] 已点击 toggle_adventure_ui 客户区 ({cx}, {cy})")
    return ok


def press_f12_once(
    hwnd: int | None = None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """开关大冒险界面一次：interception→F12；否则→点击 toggle_adventure_ui。"""
    log = on_log or (lambda _m: None)
    if _toggle_ui_uses_f12():
        ok, err = press_f12_interception(on_log=log)
        if not ok:
            log(f"[F12] 失败：{err}")
            return False
        log("[F12] 已按一次（格子识别失败，等待下一轮）")
        return True
    ok = _click_toggle_adventure_ui(hwnd, on_log=log)
    if ok:
        log("[开关界面] 已点击一次（格子识别失败，等待下一轮）")
    return ok


def _park_cursor_after_skip(
    hwnd: int | None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> None:
    """跳过动画后：PostMessage 把游戏内指针移到停靠点（默认框内 300,10），真实鼠标不动。"""
    log = on_log or (lambda _m: None)
    if not hwnd:
        return
    targets = _load_click_targets()
    pt = targets.get("skip_animation_park")
    if not isinstance(pt, dict) or pt.get("x") is None or pt.get("y") is None:
        from zephie_rolling_on.data.adventure_frame import load_anchor

        ax, ay = load_anchor()
        cx, cy = ax + 300, ay + 10
    else:
        cx, cy = int(pt["x"]), int(pt["y"])
    if move_client_cursor(int(hwnd), cx, cy, on_log=log, use_guard=False):
        log(f"[跳过动画] 游戏内指针已停靠客户区 ({cx}, {cy})")


def toggle_ui_to_skip_animation(
    hwnd: int | None = None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """开关界面跳过动画：连做两次（F12 或点击），再用 PostMessage 停靠游戏内指针。

    两次动作之间的间隔读 ``click_interval_ms``；停靠只发 PostMessage，不动真实鼠标。
    """
    log = on_log or (lambda _m: None)
    with click_shield_guard():
        ok = _toggle_ui_to_skip_animation_body(hwnd, on_log=log)
        if ok:
            _park_cursor_after_skip(hwnd, on_log=log)
        return ok


def _toggle_ui_to_skip_animation_body(
    hwnd: int | None = None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    log = on_log or (lambda _m: None)
    gap_sec = load_click_interval_sec()
    if _toggle_ui_uses_f12():
        ok, err = press_f12_interception(on_log=log)
        if not ok:
            log(f"[跳过动画] F12 失败：{err}")
            return False
        time.sleep(jittered_click_interval_sec(gap_sec))
        ok, err = press_f12_interception(on_log=log)
        if not ok:
            log(f"[跳过动画] 第二次 F12 失败：{err}")
            return False
        log(f"[跳过动画] 已 F12×2 开关界面（间隔 {click_interval_range_text(gap_sec)}）")
        return True

    if not _click_toggle_adventure_ui(hwnd, on_log=log):
        log("[跳过动画] 第一次点击开关界面失败")
        return False
    time.sleep(jittered_click_interval_sec(gap_sec))
    if not _click_toggle_adventure_ui(hwnd, on_log=log):
        log("[跳过动画] 第二次点击开关界面失败")
        return False
    log(f"[跳过动画] 已点击开关界面×2（间隔 {click_interval_range_text(gap_sec)}）")
    return True


def _maybe_skip_animation_after_action(
    hwnd: int | None = None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> None:
    # skip_animation_effective：用户开关 且 未被分辨率屏蔽（见 config 层）
    if not skip_animation_effective():
        return
    log = on_log or (lambda _m: None)
    interval = load_click_interval_sec()
    if interval > 0:
        log(f"[跳过动画] 前置等待 {click_interval_range_text(interval)}")
        time.sleep(jittered_click_interval_sec(interval))
    toggle_ui_to_skip_animation(hwnd, on_log=log)


def click_client(
    hwnd: int,
    client_x: int,
    client_y: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> None:
    """游戏客户区点击（由 click_targets.click_delivery 决定：默认 postmessage）。"""
    click_client_game(hwnd, client_x, client_y, on_log=on_log)


def throw_normal_dice(
    hwnd: int | None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """投掷普通骰子：点一下 click_targets.roll_dice。"""
    log = on_log or (lambda _m: None)
    targets = _load_click_targets()
    # 底层点击不写日志；高层「普通投骰」由 session 输出
    ok = _click_target(hwnd, targets.get("roll_dice"), "roll_dice", log, quiet=True)
    if ok:
        _maybe_skip_animation_after_action(hwnd, on_log=log)
    return ok


def use_lucky_card(
    hwnd: int | None,
    slot_x: int,
    slot_y: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """使用幸运卡：点卡槽中心 → 间隔 → 点 lucky_confirm 确认。

    间隔读 ``config/auto_click.yaml`` 的 ``click_interval_ms``。
    """
    log = on_log or (lambda _m: None)
    if not hwnd:
        return False
    targets = _load_click_targets()
    # 底层点击细节不写日志；高层「用了哪张卡」由 session 输出
    if not click_client_game(int(hwnd), int(slot_x), int(slot_y), on_log=None):
        return False
    delay_sec = load_click_interval_sec()
    if delay_sec > 0:
        time.sleep(jittered_click_interval_sec(delay_sec))
    ok = _click_target(
        hwnd, targets.get("lucky_confirm"), "lucky_confirm", log, quiet=True
    )
    if ok:
        _maybe_skip_animation_after_action(hwnd, on_log=log)
    return ok


def _click_target(
    hwnd: int | None,
    target: dict | None,
    name: str,
    on_log: Callable[[str], None],
    *,
    quiet: bool = False,
) -> bool:
    if not hwnd or not target or not isinstance(target, dict):
        return False
    x = target.get("x")
    y = target.get("y")
    if x is None or y is None:
        if not quiet:
            on_log(f"[操作] {name} 坐标未配置完整，跳过")
        return False
    ok = click_client_game(int(hwnd), int(x), int(y), on_log=None)
    if ok and not quiet:
        on_log(f"[操作] 已点击 {name}")
    return ok


def _click_configured_sequence(
    hwnd: int | None,
    *,
    clicks_key: str,
    min_clicks: int,
    log_start: str,
    name_prefix: str,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    log = on_log or (lambda _m: None)
    if not hwnd:
        return False
    targets = _load_click_targets()
    clicks = targets.get(clicks_key)
    if not isinstance(clicks, list) or len(clicks) < min_clicks:
        log(f"[操作] click_targets.yaml 中 {clicks_key} 至少需要 {min_clicks} 个点")
        return False
    delay_sec = load_click_interval_sec()
    log(log_start)
    any_ok = False
    seq = clicks[:min_clicks]
    for idx, pt in enumerate(seq, start=1):
        if _click_target(
            hwnd,
            pt if isinstance(pt, dict) else None,
            f"{name_prefix}_{idx}",
            log,
            quiet=True,
        ):
            any_ok = True
        if idx < len(seq):
            # 每次点击单独取扰动，同一批点击的间隔彼此不同
            time.sleep(jittered_click_interval_sec(delay_sec))
    return any_ok


def start_new_round(
    hwnd: int | None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """开启新一局：等局末动画 → 按 new_round_clicks 点 2 次 → 等开局动画。

    时序由 ``config/auto_click.yaml`` 控制（均无界面，改后重启生效）：

    ``new_round_pre_click_wait_ms``
        检测到 START 后的等待。局末结算动画**无法跳过**，必须等它放完；否则
        两次点击会落在动画上而失效，表现就是「检测到 START 却开不出新局」。
    ``new_round_animation_wait_ms``
        两次点击后等开局动画，之后才开始识别新局骰子。

    两次点击之间的间隔用 ``click_interval_ms``。
    """
    log = on_log or (lambda _m: None)
    anim_sec = load_new_round_animation_wait_ms() / 1000.0
    pre_sec = load_new_round_pre_click_wait_ms() / 1000.0

    if pre_sec > 0:
        log(f"[新局] 等待局末结算动画 {pre_sec:g}s（该动画无法跳过）…")
        time.sleep(pre_sec)

    delay_sec = load_click_interval_sec()
    any_ok = _click_configured_sequence(
        hwnd,
        clicks_key="new_round_clicks",
        min_clicks=2,
        log_start=(
            "[新局] 开始依次点击以开启新一轮（2 次，间隔 "
            f"{click_interval_range_text(delay_sec)}）…"
        ),
        name_prefix="new_round",
        on_log=log,
    )
    if not any_ok:
        log("[新局] 两次点击均未执行（坐标未标定）；请在 click_targets.yaml 填写 new_round_clicks")
        return False
    if anim_sec > 0:
        log(f"[新局] 第二次点击完成，等待开局动画 {anim_sec:g}s…")
        time.sleep(anim_sec)
    return True


def skip_exclamation_reward(
    hwnd: int | None,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """跳过感叹号格奖励：按 skip_exclamation_clicks 顺序点击 3 次。

    连点间隔读 ``config/auto_click.yaml`` 的 ``click_interval_ms``。
    """
    log = on_log or (lambda _m: None)
    delay_sec = load_click_interval_sec()
    any_ok = _click_configured_sequence(
        hwnd,
        clicks_key="skip_exclamation_clicks",
        min_clicks=3,
        log_start=(
            "[感叹号格] 开始依次点击以跳过奖励（3 次，间隔 "
            f"{click_interval_range_text(delay_sec)}）…"
        ),
        name_prefix="skip_exclamation",
        on_log=log,
    )
    if not any_ok:
        log(
            "[感叹号格] 三次点击均未执行（坐标未标定）；"
            "请在 click_targets.yaml 填写 skip_exclamation_clicks",
        )
    return any_ok


def replenish_dice(
    hwnd: int | None,
    *,
    on_log: Callable[[str], None] | None = None,
    regions: dict | None = None,
    enabled: bool = True,
) -> bool:
    """检测可用/充能骰子并按需补充（替代旧三连点）。

    需传入已 resolve 的 ``regions``；未传则现场 ``load_regions()``。
    """
    log = on_log or (lambda _m: None)
    if not hwnd:
        log("[补充骰子] 无 hwnd")
        return False
    from zephie_rolling_on.executor.dice_replenish import run_dice_detect_and_replenish
    from zephie_rolling_on.vision.recognize import load_regions

    cfg = regions if isinstance(regions, dict) else load_regions()
    result = run_dice_detect_and_replenish(
        int(hwnd),
        cfg,
        enabled=enabled,
        on_log=log,
    )
    return bool(result.ok)


def click_confirm_button(

    hwnd: int | None,

    center_x: int,

    center_y: int,

    *,

    on_log: Callable[[str], None] | None = None,

) -> bool:

    """点击奖励确认按钮中心（client 坐标）。"""

    log = on_log or (lambda _m: None)

    if not hwnd:

        return False

    ok = click_client_game(int(hwnd), int(center_x), int(center_y), on_log=None)
    _ = log
    return ok




def perform_advice(

    advice: str,

    *,

    hwnd: int | None,

    auto_click: bool,

    on_log: Callable[[str], None] | None = None,

) -> None:

    log = on_log or (lambda _m: None)

    if not auto_click:

        return



    if throw_normal_dice(hwnd, on_log=log):

        return



    log("[操作] 自动点击已开启；未配置 config/click_targets.yaml 中的 roll_dice 坐标。")

    log(f"[操作] 策略摘要: {advice[:160]}{'…' if len(advice) > 160 else ''}")


