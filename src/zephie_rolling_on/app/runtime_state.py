"""Per-instance runtime state (in-memory; avoids multi-instance yaml races)."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Iterator

from zephie_rolling_on.models.lucky_deck import (
    PLANNER_STATE_PATH,
    PlannerCalibration,
    _load_planner_calibration_disk,
)


@dataclass
class RuntimeState:
    """In-process mutable state for one UI instance."""

    calibration: PlannerCalibration = field(default_factory=PlannerCalibration)
    # 大冒险框客户区锚点（多开时各界面独立，不抢写 adventure_frame.yaml）
    adventure_anchor: tuple[int, int] = (462, 152)
    # 绑定窗口（多开时各界面独立，不抢写 game_window.yaml）
    game_window: dict = field(
        default_factory=lambda: {
            "hwnd": None,
            "capture_hwnd": None,
            "root_hwnd": None,
            "title": "",
        }
    )
    # 自动点击开关（多开时各界面独立，不抢写 auto_click.yaml）
    # 感叹号格奖励处理模式：skip_all / skip_none / skip_except_hammer，见 auto_click_config
    skip_exclamation_mode: str = "skip_all"
    skip_animation_via_f12: bool = True
    auto_replenish_dice: bool = True
    # 统一连点间隔（ms）；界面改动会写回 auto_click.yaml
    click_interval_ms: int = 100
    # 连点间隔的随机扰动幅度（ms）；只在 yaml 里改，无界面
    click_interval_jitter_ms: int = 20
    # 快捷键：action -> "Ctrl+Alt+F9" 或 ""（禁用）；不抢写 hotkeys.yaml
    hotkeys: dict[str, str] = field(default_factory=dict)

    @classmethod
    def bootstrap_from_disk(cls) -> RuntimeState:
        """启动时读一次磁盘作初值，之后默认只改内存。"""
        from zephie_rolling_on.data.adventure_frame import (
            ADVENTURE_FRAME_PATH,
            BASELINE_ANCHOR,
            _load_anchor_disk,
        )
        from zephie_rolling_on.data.auto_click_config import (
            load_auto_replenish_dice as _load_auto_replenish_disk,
            load_click_interval_jitter_ms as _load_click_jitter_disk,
            load_click_interval_ms as _load_click_interval_disk,
            load_skip_animation_via_f12 as _load_skip_anim_disk,
            load_skip_exclamation_mode as _load_skip_ex_disk,
        )
        from zephie_rolling_on.ui.hotkey_config import (
            ALL_ACTIONS,
            load_hotkey_bindings as _load_hotkeys_disk,
        )

        cal_src = _load_planner_calibration_disk(PLANNER_STATE_PATH)
        cal = PlannerCalibration(
            dice_remaining=int(cal_src.dice_remaining),
            drawn_counts=dict(cal_src.drawn_counts),
        )
        try:
            anchor = _load_anchor_disk(ADVENTURE_FRAME_PATH)
        except Exception:
            anchor = BASELINE_ANCHOR
        gw: dict = {"hwnd": None, "capture_hwnd": None, "root_hwnd": None, "title": ""}
        try:
            import yaml

            gw_path = PLANNER_STATE_PATH.parent / "game_window.yaml"
            if gw_path.is_file():
                with gw_path.open(encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                if isinstance(raw, dict):
                    gw = {
                        "hwnd": raw.get("hwnd"),
                        "capture_hwnd": raw.get("capture_hwnd") or raw.get("hwnd"),
                        "root_hwnd": raw.get("root_hwnd"),
                        "title": str(raw.get("title") or ""),
                    }
        except Exception:
            pass

        # 磁盘 load_* 在无 runtime 时读 yaml；此处故意无 runtime
        skip_ex = bool(_load_skip_ex_disk())
        skip_anim = bool(_load_skip_anim_disk())
        auto_replenish = bool(_load_auto_replenish_disk())
        try:
            click_interval = int(round(float(_load_click_interval_disk())))
        except (TypeError, ValueError):
            click_interval = 50
        try:
            click_jitter = int(round(float(_load_click_jitter_disk())))
        except (TypeError, ValueError):
            click_jitter = 20
        hotkeys: dict[str, str] = {}
        try:
            bindings = _load_hotkeys_disk()
            for action in ALL_ACTIONS:
                b = bindings.get(action)
                if b is not None and b.enabled and b.key:
                    hotkeys[action] = b.label
                else:
                    hotkeys[action] = ""
        except Exception:
            hotkeys = {}

        return cls(
            calibration=cal,
            adventure_anchor=(int(anchor[0]), int(anchor[1])),
            game_window=gw,
            skip_exclamation_mode=skip_ex,
            skip_animation_via_f12=skip_anim,
            auto_replenish_dice=auto_replenish,
            click_interval_ms=click_interval,
            click_interval_jitter_ms=click_jitter,
            hotkeys=hotkeys,
        )

    def clone(self) -> RuntimeState:
        """测试用：独立副本。"""
        return RuntimeState(
            calibration=PlannerCalibration(
                dice_remaining=int(self.calibration.dice_remaining),
                drawn_counts=dict(self.calibration.drawn_counts),
            ),
            adventure_anchor=(int(self.adventure_anchor[0]), int(self.adventure_anchor[1])),
            game_window=dict(self.game_window),
            skip_exclamation_mode=str(self.skip_exclamation_mode),
            skip_animation_via_f12=bool(self.skip_animation_via_f12),
            auto_replenish_dice=bool(self.auto_replenish_dice),
            click_interval_ms=int(self.click_interval_ms),
            click_interval_jitter_ms=int(self.click_interval_jitter_ms),
            hotkeys=dict(self.hotkeys),
        )


_RUNTIME: ContextVar[RuntimeState | None] = ContextVar(
    "zephie_rolling_on_runtime", default=None
)


def get_runtime() -> RuntimeState | None:
    return _RUNTIME.get()


@contextmanager
def use_runtime(rt: RuntimeState) -> Iterator[RuntimeState]:
    token: Token = _RUNTIME.set(rt)
    try:
        yield rt
    finally:
        _RUNTIME.reset(token)
