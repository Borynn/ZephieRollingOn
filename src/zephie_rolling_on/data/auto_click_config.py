"""Auto-click user config (control panel).

Two files, deliberately split:

  config/auto_click.yaml          user state; gitignored, survives an overwrite
                                  update (same treatment as the other per-user
                                  config files)
  config/auto_click.default.yaml  factory defaults; shipped, tracked

The user file wins when present. When it is absent the factory file provides the
initial values, so a fresh install starts from the shipped defaults rather than
from hardcoded constants.
"""
from __future__ import annotations

from zephie_rolling_on.paths import project_root

from pathlib import Path

import yaml

PROJECT_ROOT = project_root()
AUTO_CLICK_CONFIG_PATH = PROJECT_ROOT / "config" / "auto_click.yaml"
AUTO_CLICK_DEFAULT_PATH = PROJECT_ROOT / "config" / "auto_click.default.yaml"

# Used only when neither file exists (e.g. a stripped deployment).
_FALLBACK: dict = {
    "skip_exclamation_reward": True,
    "skip_animation_via_f12": True,
    "auto_replenish_dice": True,
    "block_mouse_in_game": False,
    "click_interval_ms": 100,
    "same_cell_retry_after": 3,
}

# 统一连点间隔（毫秒）；所有场景共用，界面与 yaml 最低 50。
_DEFAULT_CLICK_INTERVAL_MS = 100
_MIN_CLICK_INTERVAL_MS = 50
_DEFAULT_SAME_CELL_RETRY_AFTER = 3

# Fallback yaml keys if click_interval_ms is absent (read-only).
_LEGACY_INTERVAL_KEYS = (
    "click_interval_ms",
    "skip_animation_pre_delay_ms",
    "skip_animation_f12_gap_ms",
    "skip_exclamation_click_delay_ms",
    "lucky_confirm_delay_ms",
)


def _read_yaml_dict(p: Path) -> dict:
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw if isinstance(raw, dict) else {}


def load_auto_click_config(path: Path | None = None) -> dict:
    """User config merged over factory defaults."""
    if path is not None:
        return _read_yaml_dict(path) or dict(_FALLBACK)
    merged = dict(_FALLBACK)
    merged.update(_read_yaml_dict(AUTO_CLICK_DEFAULT_PATH))
    merged.update(_read_yaml_dict(AUTO_CLICK_CONFIG_PATH))
    return merged


def _clamp_click_interval_ms(ms: float) -> float:
    return max(float(_MIN_CLICK_INTERVAL_MS), float(ms))


def load_click_interval_ms(path: Path | None = None) -> float:
    """统一点击间隔（毫秒），最低 50。优先 RuntimeState，再读 yaml。"""
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None and getattr(rt, "click_interval_ms", None) is not None:
            try:
                return _clamp_click_interval_ms(float(rt.click_interval_ms))
            except (TypeError, ValueError):
                pass
    data = load_auto_click_config(path)
    raw = data.get("click_interval_ms")
    if raw is None:
        # 旧配置：取任一旧间隔键，否则默认 50
        for key in _LEGACY_INTERVAL_KEYS[1:]:
            if key in data:
                raw = data[key]
                break
    if raw is None:
        raw = _DEFAULT_CLICK_INTERVAL_MS
    try:
        ms = float(raw)
    except (TypeError, ValueError):
        ms = float(_DEFAULT_CLICK_INTERVAL_MS)
    return _clamp_click_interval_ms(ms)


def load_click_interval_sec(path: Path | None = None) -> float:
    return load_click_interval_ms(path) / 1000.0


def save_click_interval_ms(ms: float | int, path: Path | None = None) -> Path:
    """写入 yaml，并同步当前 RuntimeState（若有）。"""
    clamped = int(round(_clamp_click_interval_ms(float(ms))))
    p = path or AUTO_CLICK_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_auto_click_config(p)
    data["click_interval_ms"] = clamped
    # 清理已废弃的分场景间隔键，避免歧义
    for key in (
        "skip_animation_pre_delay_ms",
        "skip_animation_f12_gap_ms",
        "skip_exclamation_click_delay_ms",
        "lucky_confirm_delay_ms",
    ):
        data.pop(key, None)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            rt.click_interval_ms = clamped
    return p


# Aliases → unified click_interval.


def load_skip_animation_f12_gap_sec(path: Path | None = None) -> float:
    return load_click_interval_sec(path)


def load_skip_animation_pre_delay_sec(path: Path | None = None) -> float:
    return load_click_interval_sec(path)


def load_skip_exclamation_click_delay_sec(path: Path | None = None) -> float:
    return load_click_interval_sec(path)


def load_lucky_confirm_delay_sec(path: Path | None = None) -> float:
    return load_click_interval_sec(path)


def load_same_cell_retry_after(path: Path | None = None) -> int:
    """操作后连续同格几次则判定失败并重新决策。"""
    raw = load_auto_click_config(path).get(
        "same_cell_retry_after", _DEFAULT_SAME_CELL_RETRY_AFTER
    )
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = _DEFAULT_SAME_CELL_RETRY_AFTER
    return max(1, n)


def load_skip_exclamation_reward(path: Path | None = None) -> bool:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            return bool(rt.skip_exclamation_reward)
    return bool(load_auto_click_config(path).get("skip_exclamation_reward", False))


def load_skip_animation_via_f12(path: Path | None = None) -> bool:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            return bool(rt.skip_animation_via_f12)
    return bool(load_auto_click_config(path).get("skip_animation_via_f12", True))


def save_skip_exclamation_reward(enabled: bool, path: Path | None = None) -> Path:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            rt.skip_exclamation_reward = bool(enabled)
            return AUTO_CLICK_CONFIG_PATH
    p = path or AUTO_CLICK_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_auto_click_config(p)
    data["skip_exclamation_reward"] = bool(enabled)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return p


def save_skip_animation_via_f12(enabled: bool, path: Path | None = None) -> Path:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            rt.skip_animation_via_f12 = bool(enabled)
            return AUTO_CLICK_CONFIG_PATH
    p = path or AUTO_CLICK_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_auto_click_config(p)
    data["skip_animation_via_f12"] = bool(enabled)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return p


def load_auto_replenish_dice(path: Path | None = None) -> bool:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            return bool(rt.auto_replenish_dice)
    return bool(load_auto_click_config(path).get("auto_replenish_dice", True))


def save_auto_replenish_dice(enabled: bool, path: Path | None = None) -> Path:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            rt.auto_replenish_dice = bool(enabled)
            return AUTO_CLICK_CONFIG_PATH
    p = path or AUTO_CLICK_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_auto_click_config(p)
    data["auto_replenish_dice"] = bool(enabled)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return p


def load_block_mouse_in_game(path: Path | None = None) -> bool:
    return bool(load_auto_click_config(path).get("block_mouse_in_game", False))


def save_block_mouse_in_game(enabled: bool, path: Path | None = None) -> Path:
    p = path or AUTO_CLICK_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_auto_click_config(p)
    data["block_mouse_in_game"] = bool(enabled)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return p
