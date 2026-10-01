"""Adventure frame anchor: store relative offsets, resolve to client coords."""
from __future__ import annotations

from zephie_rolling_on.paths import project_root

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = project_root()
ADVENTURE_FRAME_PATH = PROJECT_ROOT / "config" / "adventure_frame.yaml"

# 首次标定确认的基础位置；regions/click_targets 中的偏移量相对此锚点标定
BASELINE_ANCHOR = (462, 152)

_REGION_BOX_KEYS = (
    "cell_id_region",
    "dice_usage_region",
    "lucky_cards_region",
    "inventory_dice_region_a",
    "inventory_dice_region_b",
    "charge_dice_region",
)


def load_anchor(path: Path | None = None) -> tuple[int, int]:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            return (int(rt.adventure_anchor[0]), int(rt.adventure_anchor[1]))
    return _load_anchor_disk(path or ADVENTURE_FRAME_PATH)


def _load_anchor_disk(p: Path) -> tuple[int, int]:
    if not p.is_file():
        return BASELINE_ANCHOR
    with p.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    block = raw.get("anchor") if isinstance(raw.get("anchor"), dict) else raw
    left = int(block.get("left", BASELINE_ANCHOR[0]))
    top = int(block.get("top", BASELINE_ANCHOR[1]))
    return left, top


def save_anchor(left: int, top: int, path: Path | None = None) -> Path:
    if path is None:
        from zephie_rolling_on.app.runtime_state import get_runtime

        rt = get_runtime()
        if rt is not None:
            rt.adventure_anchor = (int(left), int(top))
            return ADVENTURE_FRAME_PATH
    p = path or ADVENTURE_FRAME_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    left, top = int(left), int(top)

    # Rewrite ONLY the two numbers inside `anchor:`.
    #
    # This used to regenerate the whole file from a fixed template, which silently
    # deleted every other key in it — including the `auto_calib` block
    # (search bounds, threshold, marker templates). `save_anchor` runs on every
    # automatic re-calibration, so the factory config was destroyed the first
    # time the anchor moved, and the loss was invisible because defaults exist.
    if p.is_file():
        try:
            updated = _replace_anchor_values(
                p.read_text(encoding="utf-8"), left, top
            )
        except (OSError, UnicodeDecodeError):
            updated = None
        if updated is not None:
            p.write_text(updated, encoding="utf-8")
            return p

    # No file, or no `anchor:` block to update: write a minimal one.
    lines = [
        "# 大冒险界面框在游戏客户区中的左上角（「定位大冒险界面」确认后更新）",
        "# regions.yaml / click_targets.yaml 中的 left/top（或 x/y）为相对本锚点的偏移",
        "",
        "anchor:",
        f"  left: {left}",
        f"  top: {top}",
        "",
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _replace_anchor_values(text: str, left: int, top: int) -> str | None:
    """Replace ``left``/``top`` under the top-level ``anchor:`` block only.

    Returns ``None`` when there is no ``anchor:`` block, so the caller can fall
    back to creating a fresh file. Everything outside that block — other keys,
    blank lines and comments — is preserved byte for byte.
    """
    lines = text.splitlines()
    start = None
    for i, raw in enumerate(lines):
        if raw.rstrip() == "anchor:":
            start = i
            break
    if start is None:
        return None

    seen_left = seen_top = False
    for i in range(start + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        # A non-indented, non-blank line ends the block.
        if stripped and not line[:1].isspace():
            break
        key = stripped.split(":", 1)[0] if ":" in stripped else ""
        if key == "left" and not seen_left:
            lines[i] = f"  left: {left}"
            seen_left = True
        elif key == "top" and not seen_top:
            lines[i] = f"  top: {top}"
            seen_top = True

    if not (seen_left and seen_top):
        return None

    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    return out


def _shift_box(box: dict[str, Any], dx: int, dy: int) -> dict[str, Any]:
    out = dict(box)
    if "left" in out:
        out["left"] = int(out["left"]) + dx
    if "top" in out:
        out["top"] = int(out["top"]) + dy
    return out


def _shift_point(point: dict[str, Any], dx: int, dy: int) -> dict[str, Any]:
    out = dict(point)
    if "x" in out:
        out["x"] = int(out["x"]) + dx
    if "y" in out:
        out["y"] = int(out["y"]) + dy
    return out


def resolve_regions(
    cfg: dict[str, Any],
    anchor: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """把配置里相对大冒险框的偏移换算为游戏客户区绝对坐标。"""
    ax, ay = anchor if anchor is not None else load_anchor()
    out = deepcopy(cfg)
    for key in _REGION_BOX_KEYS:
        box = out.get(key)
        if isinstance(box, dict):
            out[key] = _shift_box(box, ax, ay)
    for popup_key in (
        "confirm_button",
        "execute_task_button",
        "platinum_hammer",
        "adventure_complete_banner",
        "roll_kind",
    ):
        popup = out.get(popup_key)
        if isinstance(popup, dict):
            popup = dict(popup)
            search = popup.get("search")
            if isinstance(search, dict):
                popup["search"] = _shift_box(search, ax, ay)
            out[popup_key] = popup
    # 结束横幅必须与「执行任务」同搜索区（避免 yaml 漂移 / 旧 debug 误导）
    ex = out.get("execute_task_button")
    ac = out.get("adventure_complete_banner")
    if isinstance(ex, dict) and isinstance(ac, dict):
        ex_search = ex.get("search")
        if isinstance(ex_search, dict):
            ac = dict(ac)
            ac["search"] = dict(ex_search)
            out["adventure_complete_banner"] = ac
    deck = out.get("deck_pool")
    if isinstance(deck, dict):
        deck = dict(deck)
        for rk in ("round1", "round2"):
            round_cfg = deck.get(rk)
            if not isinstance(round_cfg, dict):
                continue
            round_cfg = dict(round_cfg)
            search = round_cfg.get("search")
            if isinstance(search, dict):
                round_cfg["search"] = _shift_box(search, ax, ay)
            fc = round_cfg.get("first_center")
            if isinstance(fc, dict):
                round_cfg["first_center"] = _shift_point(fc, ax, ay)
            deck[rk] = round_cfg
        out["deck_pool"] = deck
    return out


def resolve_click_targets(
    cfg: dict[str, Any],
    anchor: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """把 click_targets 里相对大冒险框的 x/y 换算为客户区绝对坐标。"""
    ax, ay = anchor if anchor is not None else load_anchor()
    out = deepcopy(cfg)
    for key in (
        "roll_dice",
        "lucky_confirm",
        "deck_pool_toggle",
        "deck_pool_drag_from",
        "deck_pool_drag_to",
        "skip_animation_park",
        "replenish_dice_open",
        "replenish_dice_btn_100",
        "replenish_dice_btn_10",
        "replenish_dice_btn_1",
        "replenish_dice_confirm",
    ):
        pt = out.get(key)
        if isinstance(pt, dict):
            out[key] = _shift_point(pt, ax, ay)
    for list_key in (
        "new_round_clicks",
        "skip_exclamation_clicks",
        "replenish_dice_clicks",
    ):
        clicks = out.get(list_key)
        if not isinstance(clicks, list):
            continue
        resolved_clicks: list[Any] = []
        for pt in clicks:
            if (
                isinstance(pt, dict)
                and pt.get("x") is not None
                and pt.get("y") is not None
            ):
                resolved_clicks.append(_shift_point(pt, ax, ay))
            else:
                resolved_clicks.append(pt)
        out[list_key] = resolved_clicks
    return out
