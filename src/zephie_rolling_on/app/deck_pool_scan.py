"""卡池扫描编排：开列表 → 两轮检测 → 写校准 → 关列表。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from zephie_rolling_on.executor.actions import _load_click_targets
from zephie_rolling_on.executor.win32_click import click_client_game, drag_client_game
from zephie_rolling_on.models.lucky_deck import apply_deck_pool_scan_counts
from zephie_rolling_on.vision.capture import capture_window_client
from zephie_rolling_on.vision.deck_pool import (
    ROUND1_CARDS,
    ROUND2_CARDS,
    DeckPoolScanResult,
    save_deck_pool_round_debug,
    scan_deck_pool_on_frames,
)
from zephie_rolling_on.vision.recognize import load_regions


def scan_and_apply_deck_pool(
    hwnd: int | None,
    regions: dict[str, Any] | None = None,
    *,
    on_log: Callable[[str], None] | None = None,
    open_wait_sec: float = 0.6,
    after_drag_wait_sec: float = 0.5,
    close_wait_sec: float = 0.3,
    save_debug: bool = False,
) -> DeckPoolScanResult | None:
    """打开卡池 → 两轮已抽勾检测 → 覆盖写入 drawn_counts → 关闭卡池。

    进度回调当前不输出：卡池扫描在正常流程里保持安静。
    """
    del on_log

    if not hwnd:
        return None

    cfg = regions if regions is not None else load_regions()
    targets = _load_click_targets()
    toggle = targets.get("deck_pool_toggle")
    drag_from = targets.get("deck_pool_drag_from")
    drag_to = targets.get("deck_pool_drag_to")
    if not isinstance(toggle, dict) or toggle.get("x") is None:
        return None
    if not isinstance(drag_from, dict) or not isinstance(drag_to, dict):
        return None

    root = cfg.get("deck_pool") if isinstance(cfg.get("deck_pool"), dict) else {}
    open_wait_sec = float(root.get("open_wait_sec", open_wait_sec))
    after_drag_wait_sec = float(root.get("after_drag_wait_sec", after_drag_wait_sec))
    close_wait_sec = float(root.get("close_wait_sec", close_wait_sec))

    tx, ty = int(toggle["x"]), int(toggle["y"])
    if not click_client_game(int(hwnd), tx, ty):
        return None
    time.sleep(open_wait_sec)

    frame1 = capture_window_client(int(hwnd))
    if frame1 is None or frame1.size == 0:
        click_client_game(int(hwnd), tx, ty)
        return None
    if save_debug:
        save_deck_pool_round_debug(
            frame1,
            cfg,
            round_key="round1",
            round_index=1,
            default_cards=ROUND1_CARDS,
            debug_prefix="deck_pool_r1",
        )

    fx, fy = int(drag_from["x"]), int(drag_from["y"])
    tox, toy = int(drag_to["x"]), int(drag_to["y"])
    # A failed drag still allows the second round to be attempted.
    drag_client_game(int(hwnd), fx, fy, tox, toy)
    time.sleep(after_drag_wait_sec)

    frame2 = capture_window_client(int(hwnd))
    if frame2 is None or frame2.size == 0:
        click_client_game(int(hwnd), tx, ty)
        return None
    if save_debug:
        save_deck_pool_round_debug(
            frame2,
            cfg,
            round_key="round2",
            round_index=2,
            default_cards=ROUND2_CARDS,
            debug_prefix="deck_pool_r2",
        )

    result = scan_deck_pool_on_frames(frame1, frame2, cfg)
    apply_deck_pool_scan_counts(result.drawn_counts)

    time.sleep(close_wait_sec)
    click_client_game(int(hwnd), tx, ty)
    return result
