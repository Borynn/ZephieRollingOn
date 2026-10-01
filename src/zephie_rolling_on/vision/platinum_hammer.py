"""Detect the platinum-hammer reward icon in the dimmed task popup."""

from __future__ import annotations

from typing import Any

import numpy as np

from zephie_rolling_on.vision.confirm_button import ConfirmHit, find_popup_button


def find_platinum_hammer(
    frame: np.ndarray,
    config: dict[str, Any],
) -> ConfirmHit | None:
    """画面变灰且踩到 H 类格子时，检测奖励里是否出现白金锤子。

    命中返回客户区中心坐标与 score；未检测到（含模板缺失）返回 ``None``。
    只用「检测到 / 未检测到」两态，不做第三种判定——阈值以下即视为未检测到，
    与其它弹窗按钮检测保持一致。
    """
    return find_popup_button(
        frame,
        config,
        "platinum_hammer",
        default_template="assets/ui/platinum_hammer.png",
    )
