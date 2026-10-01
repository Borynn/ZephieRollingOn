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

import random

import yaml

PROJECT_ROOT = project_root()
AUTO_CLICK_CONFIG_PATH = PROJECT_ROOT / "config" / "auto_click.yaml"
AUTO_CLICK_DEFAULT_PATH = PROJECT_ROOT / "config" / "auto_click.default.yaml"

# 感叹号格奖励的处理模式，存在 skip_exclamation_reward 键下。
#
# 键名沿用历史命名（旧值是 bool），**值改为三态字符串**：一个键、不产生两份配置，
# 旧配置读得动（见 normalize_skip_exclamation_mode）。
SKIP_ALL = "skip_all"  # 默认：三连点跳过所有奖励（= 旧 true）
SKIP_NONE = "skip_none"  # 不跳过任何奖励：检测到执行任务按钮即停（= 旧 false）
SKIP_EXCEPT_HAMMER = "skip_except_hammer"  # 不跳过白金锤子：检测到锤子才停，否则跳过

SKIP_MODES: tuple[str, ...] = (SKIP_ALL, SKIP_NONE, SKIP_EXCEPT_HAMMER)

# Used only when neither file exists (e.g. a stripped deployment).
_FALLBACK: dict = {
    "skip_exclamation_reward": SKIP_ALL,
    "skip_animation_via_f12": True,
    "auto_replenish_dice": True,
    "block_mouse_in_game": False,
    "click_interval_ms": 100,
    "click_interval_jitter_ms": 20,
    "same_cell_retry_after": 3,
    "new_round_pre_click_wait_ms": 2000,
    "new_round_animation_wait_ms": 3000,
}

# 统一连点间隔（毫秒）；所有场景共用，界面与 yaml 最低 50。
_DEFAULT_CLICK_INTERVAL_MS = 100
_MIN_CLICK_INTERVAL_MS = 50
_DEFAULT_SAME_CELL_RETRY_AFTER = 3

# 检测到 START（本局结束）后，等待多久再点两次开新局（毫秒）。
#
# 局末结算动画**无法跳过**，必须等它放完；否则两次点击会落在动画上而失效，
# 表现就是「检测到 START 却开不出新局」。慢机器/低配机上动画更久，所以这是
# 可调项（改 config/auto_click.yaml，**未做界面**）。
_DEFAULT_NEW_ROUND_PRE_CLICK_WAIT_MS = 2000

# 两次开新局点击完成后，等待开局动画（毫秒），之后才开始识别新局骰子。
_DEFAULT_NEW_ROUND_ANIMATION_WAIT_MS = 3000

# 两个等待的上限：避免误填一个极大值把脚本卡死（记为秒差）
_MAX_NEW_ROUND_WAIT_MS = 120_000

# 点击间隔的随机扰动幅度（毫秒）。真人不会每次都用同一个间隔，固定间隔本身
# 就是一种可识别的规律。
# 可改 config/auto_click.yaml 的 click_interval_jitter_ms（**未做界面**，改完重启生效）；
# 置 0 即完全关闭扰动。
_DEFAULT_CLICK_INTERVAL_JITTER_MS = 20
_MAX_CLICK_INTERVAL_JITTER_MS = 1000

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


def _effective_base_ms(base_ms: float) -> float:
    """归一化基准：非正数表示「不等待」，正数则不低于下限 50。

    必须在函数内部夹紧，而不是依赖调用方先夹：否则某个调用点传入未夹紧的值
    （如 30）会算出 10~30ms，**击穿 50ms 下限**。
    """
    base = float(base_ms)
    if base <= 0:
        return 0.0
    return max(float(_MIN_CLICK_INTERVAL_MS), base)


def _clamp_click_interval_jitter_ms(ms: float) -> float:
    """扰动幅度夹紧到 ``[0, 1000]``；0 表示关闭扰动。"""
    return min(max(0.0, float(ms)), float(_MAX_CLICK_INTERVAL_JITTER_MS))


def load_click_interval_jitter_ms(path: Path | None = None) -> float:
    """点击间隔的随机扰动幅度（毫秒），0~1000，0 表示不加扰动。

    优先 RuntimeState（每次点击都会调用，走内存避免读 yaml），再读 yaml，
    最后回落到默认 20。**未做界面**：改 config/auto_click.yaml 后需重启生效，
    与 ``click_interval_ms`` 一致。
    """
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            val = getattr(rt, "click_interval_jitter_ms", None)
            if val is not None:
                try:
                    return _clamp_click_interval_jitter_ms(float(val))
                except (TypeError, ValueError):
                    pass
    raw = load_auto_click_config(path).get("click_interval_jitter_ms")
    if raw is None:
        raw = _DEFAULT_CLICK_INTERVAL_JITTER_MS
    try:
        return _clamp_click_interval_jitter_ms(float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_CLICK_INTERVAL_JITTER_MS)


def _jitter_offset_ms(
    base_ms: float, jitter_ms: float | None = None
) -> tuple[float, float]:
    """扰动的偏移量区间（毫秒），相对基准。入参需已过 :func:`_effective_base_ms`。

    下界取 ``-min(扰动, 基准 - 50)``，而不是「算完再截断到 50」：
    基准恰好为下限 50 时，``max(50, 50±20)`` 会把 30~50 全部压到 50，
    约一半点击都精确落在 50ms —— 那本身又成了新规律。压缩下半区间后，
    基准 50 的实际范围是 50~70 **且均匀**，上下界与截断写法完全一致。

    ``jitter_ms`` 不传则按当前配置取；显式传入便于单测固定幅度。
    """
    jitter = max(
        0.0,
        load_click_interval_jitter_ms() if jitter_ms is None else float(jitter_ms),
    )
    lower = -min(jitter, max(0.0, float(base_ms) - float(_MIN_CLICK_INTERVAL_MS)))
    return lower, jitter


def jittered_click_interval_sec(base_sec: float | None = None) -> float:
    """单次点击间隔（秒）：基准 ±20ms 随机，且不低于 50ms。

    例：基准 100 → 80~120；基准 50 → 50~70（不是 30~70）。基准为 0 时返回 0，
    保持旧的「0 = 不等待」语义。

    **每次睡眠前都要重新调用。** 调用方若在循环外算一次再复用，同一批点击会
    共用同一个扰动值，就失去意义了。``base_sec`` 用于复用已经读好的基准，
    省掉重复读配置；不传则按当前 ``click_interval_ms`` 现取。
    """
    base_ms = _effective_base_ms(
        load_click_interval_ms() if base_sec is None else float(base_sec) * 1000.0
    )
    if base_ms <= 0:
        return 0.0
    lower, upper = _jitter_offset_ms(base_ms)
    return max(0.0, base_ms + random.uniform(lower, upper)) / 1000.0


def click_interval_range_text(base_sec: float | None = None) -> str:
    """实际间隔范围的可读文本（如 ``"80~120ms"``），供日志直接拼接。

    入参与 :func:`jittered_click_interval_sec` **一致（秒，接同一个基准变量）**，
    秒→毫秒的换算只在这里做一次。此前拆成「秒版随机 + 毫秒版范围」两个单位，
    调用点极易把秒误传给毫秒参数，把范围显示错。
    """
    base_ms = _effective_base_ms(
        load_click_interval_ms() if base_sec is None else float(base_sec) * 1000.0
    )
    if base_ms <= 0:
        return "0ms"
    lower, upper = _jitter_offset_ms(base_ms)
    return f"{max(0.0, base_ms + lower):.0f}~{base_ms + upper:.0f}ms"


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


def _load_new_round_wait_ms(
    key: str, default_ms: int, path: Path | None = None
) -> float:
    """读取「开新局」相关的等待时长（毫秒），夹紧到 ``[0, 上限]``。

    负值当 0（不等），超上限则截断——误填一个极大值会把脚本长时间卡住，
    而这类值来自手改 yaml，必须容错。
    """
    raw = load_auto_click_config(path).get(key, default_ms)
    try:
        ms = float(raw)
    except (TypeError, ValueError):
        ms = float(default_ms)
    return min(max(0.0, ms), float(_MAX_NEW_ROUND_WAIT_MS))


def load_new_round_pre_click_wait_ms(path: Path | None = None) -> float:
    """检测到 START 后、点两次开新局之前的等待（毫秒）。

    用于等局末结算动画放完（该动画无法跳过）。**未做界面**：改
    config/auto_click.yaml 后重启生效。
    """
    return _load_new_round_wait_ms(
        "new_round_pre_click_wait_ms",
        _DEFAULT_NEW_ROUND_PRE_CLICK_WAIT_MS,
        path,
    )


def load_new_round_animation_wait_ms(path: Path | None = None) -> float:
    """两次开新局点击之后、开始识别新局骰子之前的等待（毫秒）。

    用于等开局动画。**未做界面**：改 config/auto_click.yaml 后重启生效。
    """
    return _load_new_round_wait_ms(
        "new_round_animation_wait_ms",
        _DEFAULT_NEW_ROUND_ANIMATION_WAIT_MS,
        path,
    )


def normalize_skip_exclamation_mode(raw: object) -> str:
    """把配置值归一化成三态字符串。

    旧格式是 bool（``true``=跳过、``false``=不跳过），映射成 ``SKIP_ALL`` /
    ``SKIP_NONE``；未知值兜底为 ``SKIP_ALL``，与历史默认行为一致。
    """
    if isinstance(raw, bool):
        return SKIP_ALL if raw else SKIP_NONE
    if raw in SKIP_MODES:
        return str(raw)
    return SKIP_ALL


def load_skip_exclamation_mode(path: Path | None = None) -> str:
    """读取感叹号格奖励的处理模式（三态字符串）。"""
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            return normalize_skip_exclamation_mode(rt.skip_exclamation_mode)
    return normalize_skip_exclamation_mode(
        load_auto_click_config(path).get("skip_exclamation_reward", SKIP_ALL)
    )


def load_skip_animation_via_f12(path: Path | None = None) -> bool:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            return bool(rt.skip_animation_via_f12)
    return bool(load_auto_click_config(path).get("skip_animation_via_f12", True))


# ---------------------------------------------------------------------------
# 分辨率屏蔽
# ---------------------------------------------------------------------------
# 在该分辨率下「投骰/用卡后跳过动画」会出问题，运行时自动屏蔽。
# 只作用于本次运行，不写盘，也不改动用户勾选状态：分辨率变回其它值即自动恢复。
BLOCKED_SKIP_ANIMATION_SIZE = (1366, 768)

_skip_animation_resolution_block = False


def set_skip_animation_resolution_block(blocked: bool) -> None:
    """由启动/一键测试根据当前游戏分辨率设置。"""
    global _skip_animation_resolution_block
    _skip_animation_resolution_block = bool(blocked)


def skip_animation_blocked_by_resolution() -> bool:
    return _skip_animation_resolution_block


def skip_animation_effective(path: Path | None = None) -> bool:
    """实际是否执行跳过动画：用户开关 且 未被分辨率屏蔽。"""
    return bool(load_skip_animation_via_f12(path)) and not _skip_animation_resolution_block


def save_skip_exclamation_mode(mode: str, path: Path | None = None) -> Path:
    """保存感叹号格奖励的处理模式（三态字符串）。"""
    mode = normalize_skip_exclamation_mode(mode)
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            rt.skip_exclamation_mode = mode
            return AUTO_CLICK_CONFIG_PATH
    p = path or AUTO_CLICK_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_auto_click_config(p)
    data["skip_exclamation_reward"] = mode
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
