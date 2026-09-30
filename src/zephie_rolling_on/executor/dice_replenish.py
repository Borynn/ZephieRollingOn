"""Detect available dice and auto-replenish from inventory / charge."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from zephie_rolling_on.executor.win32_click import click_client_game
from zephie_rolling_on.vision.capture import capture_window_client
from zephie_rolling_on.vision.inventory_dice import (
    recognize_charge_dice,
    recognize_inventory_dice,
)

Status = Literal[
    "skipped_enough",
    "replenished",
    "insufficient",
    "ocr_failed",
    "click_failed",
    "disabled",
]


@dataclass(frozen=True)
class DiceReplenishResult:
    status: Status
    available: int | None
    charge: int | None
    need: int | None
    message: str
    remaining: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("skipped_enough", "replenished", "disabled")


def _load_targets() -> dict:
    from zephie_rolling_on.executor.actions import _load_click_targets

    return _load_click_targets()


def _click_named(
    hwnd: int,
    targets: dict,
    key: str,
    *,
    on_log: Callable[[str], None],
) -> bool:
    pt = targets.get(key)
    if not isinstance(pt, dict) or pt.get("x") is None or pt.get("y") is None:
        on_log(f"[补充骰子] 未配置 click_targets.{key}")
        return False
    cx, cy = int(pt["x"]), int(pt["y"])
    ok = click_client_game(int(hwnd), cx, cy, on_log=on_log)
    if ok:
        on_log(f"[补充骰子] 已点击 {key} 客户区 ({cx}, {cy})")
    return ok


def _breakdown_need(need: int) -> tuple[int, int, int]:
    """把差额拆成 100 / 10 / 1 的点击次数（贪心）。"""
    n = max(0, int(need))
    n100 = n // 100
    n %= 100
    n10 = n // 10
    n1 = n % 10
    return n100, n10, n1


def _ocr_remaining_dice(
    img,
    regions: dict[str, Any],
    *,
    on_log: Callable[[str], None],
) -> int | None:
    """OCR remaining in-round dice from the same frame."""
    from zephie_rolling_on.vision.recognize import recognize_dice_only

    dice_rec = recognize_dice_only(regions, frame=img)
    rem = dice_rec.dice_remaining
    on_log(
        f"[补充骰子·剩余骰子] OCR={rem}"
        f"（used={dice_rec.dice_used} raw={dice_rec.raw_dice_text!r}）"
    )
    if rem is None:
        return None
    return max(0, int(rem))


def execute_replenish_clicks(
    hwnd: int,
    need: int,
    *,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """打开补充界面 → 按 100/10/1 点够 → 确认。"""
    log = on_log or (lambda _m: None)
    from zephie_rolling_on.data.auto_click_config import (
        jittered_click_interval_sec,
        load_click_interval_sec,
    )

    targets = _load_targets()
    delay_sec = load_click_interval_sec()

    n100, n10, n1 = _breakdown_need(need)
    log(
        f"[补充骰子] 需补充 {need}：点100×{n100}、点10×{n10}、点1×{n1}"
    )

    if not _click_named(hwnd, targets, "replenish_dice_open", on_log=log):
        return False
    if delay_sec:
        time.sleep(jittered_click_interval_sec(delay_sec))

    for _ in range(n100):
        if not _click_named(hwnd, targets, "replenish_dice_btn_100", on_log=log):
            return False
        if delay_sec:
            time.sleep(jittered_click_interval_sec(delay_sec))
    for _ in range(n10):
        if not _click_named(hwnd, targets, "replenish_dice_btn_10", on_log=log):
            return False
        if delay_sec:
            time.sleep(jittered_click_interval_sec(delay_sec))
    for _ in range(n1):
        if not _click_named(hwnd, targets, "replenish_dice_btn_1", on_log=log):
            return False
        if delay_sec:
            time.sleep(jittered_click_interval_sec(delay_sec))

    if not _click_named(hwnd, targets, "replenish_dice_confirm", on_log=log):
        return False
    return True


def run_dice_detect_and_replenish(
    hwnd: int,
    regions: dict[str, Any],
    *,
    enabled: bool = True,
    frame=None,
    on_log: Callable[[str], None] | None = None,
) -> DiceReplenishResult:
    """检测可用/充能并按需补充。``enabled=False`` 时直接跳过。

    OCR 局内值为**剩余骰子**。规则：
    - 可用 ≥ 剩余 → 跳过补充
    - 否则查充能；可用+充能 < 剩余 → 不足停机
    - 否则补充差额 = 剩余 − 可用
    """
    log = on_log or (lambda _m: None)
    if not enabled:
        return DiceReplenishResult(
            status="disabled",
            available=None,
            charge=None,
            need=None,
            message="自动补充骰子未开启，跳过",
        )
    if not hwnd:
        return DiceReplenishResult(
            status="click_failed",
            available=None,
            charge=None,
            need=None,
            message="未检测到游戏窗口，无法补充骰子",
        )

    img = frame
    if img is None:
        img = capture_window_client(int(hwnd))
    if img is None:
        return DiceReplenishResult(
            status="ocr_failed",
            available=None,
            charge=None,
            need=None,
            message="无法检测可用骰子",
        )

    inv = recognize_inventory_dice(img, regions)
    available = inv.total
    log(f"[补充骰子·可用] A={inv.part_a} B={inv.part_b} 合计={available}（{inv.notes}）")
    if available is None:
        return DiceReplenishResult(
            status="ocr_failed",
            available=None,
            charge=None,
            need=None,
            message=f"可用骰子识别失败：{inv.notes}",
        )

    rem = _ocr_remaining_dice(img, regions, on_log=log)
    if rem is None:
        return DiceReplenishResult(
            status="ocr_failed",
            available=int(available),
            charge=None,
            need=None,
            message="局内剩余骰子识别失败，无法判断是否需要补充",
            remaining=None,
        )

    log(f"[补充骰子] 可用={available} 剩余骰子={rem}")
    if int(available) >= int(rem):
        msg = f"可用 {available}≥剩余骰子 {rem}，无需补充"
        log(f"[补充骰子] {msg}")
        return DiceReplenishResult(
            status="skipped_enough",
            available=int(available),
            charge=None,
            need=0,
            message=msg,
            remaining=int(rem),
        )

    charge_rec = recognize_charge_dice(img, regions)
    charge = charge_rec.value
    log(f"[补充骰子·充能] {charge}（{charge_rec.notes}）")
    if charge is None:
        return DiceReplenishResult(
            status="ocr_failed",
            available=int(available),
            charge=None,
            need=None,
            message="充能骰子识别失败",
            remaining=int(rem),
        )

    pooled = int(available) + int(charge)
    need = int(rem) - int(available)
    # 不够停机：可用+充能 不足以覆盖剩余骰子需求
    if pooled < int(rem):
        msg = (
            f"骰子不足：可用 {available}+充能 {charge}={pooled}"
            f" < 剩余骰子 {rem}，停止"
        )
        log(f"[补充骰子] {msg}")
        return DiceReplenishResult(
            status="insufficient",
            available=int(available),
            charge=int(charge),
            need=need,
            message="骰子不足",
            remaining=int(rem),
        )

    log(
        f"[补充骰子] 可用 {available}+充能 {charge}={pooled}≥剩余骰子 {rem}，"
        f"开始补充差额 {need}…"
    )
    if not execute_replenish_clicks(int(hwnd), need, on_log=log):
        return DiceReplenishResult(
            status="click_failed",
            available=int(available),
            charge=int(charge),
            need=need,
            message="补充骰子点击失败",
            remaining=int(rem),
        )
    msg = f"已补充差额 {need}（补充前可用={available} 剩余骰子={rem}）"
    log(f"[补充骰子] {msg}")
    return DiceReplenishResult(
        status="replenished",
        available=int(available),
        charge=int(charge),
        need=need,
        message=msg,
        remaining=int(rem),
    )
