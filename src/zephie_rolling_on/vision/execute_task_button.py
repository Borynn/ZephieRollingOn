"""Detect the execute-task popup button."""

from __future__ import annotations

from typing import Any

import numpy as np

from zephie_rolling_on.vision.confirm_button import ConfirmHit, find_popup_button


def find_execute_task_button(
    frame: np.ndarray,
    config: dict[str, Any],
) -> ConfirmHit | None:
    """画面变灰时若出现执行任务按钮，表示踩到 H 类格子，需玩家自行处理。"""
    return find_popup_button(
        frame,
        config,
        "execute_task_button",
        default_template="assets/ui/execute_task_button.png",
    )
