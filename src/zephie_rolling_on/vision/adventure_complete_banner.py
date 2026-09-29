"""Detect the adventure-complete banner via template match."""

from __future__ import annotations

from typing import Any

import numpy as np

from zephie_rolling_on.vision.confirm_button import ConfirmHit, find_popup_button


def find_adventure_complete_banner(
    frame: np.ndarray,
    config: dict[str, Any],
) -> ConfirmHit | None:
    """画面变灰且出现「已完成冒险。」横幅时，表示本局真正结束。"""
    return find_popup_button(
        frame,
        config,
        "adventure_complete_banner",
        default_template="assets/ui/adventure_complete_banner.png",
    )
