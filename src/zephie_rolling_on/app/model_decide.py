"""Decide via the imported decision model package."""

from __future__ import annotations

from zephie_rolling_on.decision_models.runtime import (
    active_model_ready,
    get_active_decision_model,
)
from zephie_rolling_on.models.decision_action import ActionScore, RankResult
from zephie_rolling_on.models.game_state import GameState
from zephie_rolling_on.models.lucky_card import LuckyCardType, lucky_card_by_id
from zephie_rolling_on.models.lucky_deck import (
    DEFAULT_DICE_REMAINING,
    PlannerCalibration,
    load_planner_calibration,
)
from zephie_rolling_on.models.map_board import MapBoard


def action_label(action_id: str) -> str:
    if action_id == "roll_normal":
        return "普通投骰"
    if action_id.startswith("use_"):
        card_id = action_id[len("use_") :]
        d = lucky_card_by_id().get(card_id)
        if d is None:
            return f"使用 {card_id}"
        if d.type == LuckyCardType.MULTIPLY:
            return f"使用 ×{d.value}"
        if d.type == LuckyCardType.STEP_FORWARD:
            return f"使用 前进{d.value}"
        if d.type == LuckyCardType.STEP_BACK:
            return f"使用 后退{d.value}"
        if d.type == LuckyCardType.NEXT:
            return f"使用 跳关{d.value}"
        return f"使用 {card_id}"
    return action_id


def action_consumes_dice(action_id: str) -> bool:
    if action_id == "roll_normal":
        return True
    if action_id.startswith("use_"):
        card_id = action_id[len("use_") :]
        d = lucky_card_by_id().get(card_id)
        if d is None:
            return True
        return bool(d.consumes_dice)
    return True


def rank_from_model_result(result, *, model_name: str) -> RankResult:
    """Map a model ``action_name`` to the executor ``action_id``."""
    action = str(getattr(result, "action", "") or "").strip()
    if action == "roll":
        action_id = "roll_normal"
    elif action and action != "error":
        action_id = f"use_{action}"
    else:
        return RankResult(actions=[], held_below_threshold=[])
    v = float(getattr(result, "v_cells", 0.0) or 0.0)
    detail = f"模型={model_name}；V≈{v:.1f}"
    return RankResult(
        actions=[
            ActionScore(
                action_id=action_id,
                label=action_label(action_id),
                score=v,
                detail=detail,
                consumes_dice=action_consumes_dice(action_id),
                raw_value=v,
            )
        ],
        held_below_threshold=[],
    )


def format_model_advice(
    board: MapBoard,
    state: GameState,
    cal: PlannerCalibration,
    rank: RankResult,
    *,
    dice_clamped: bool,
) -> str:
    cell_id = state.current_cell_id
    cell = board.get(cell_id) if cell_id is not None else None
    cell_s = f"格子 {cell_id}"
    if cell is not None:
        cell_s = f"格子 {cell_id}（{cell.cell_type}）"
    rem = cal.deck_remaining_total()
    dice = int(state.dice_remaining if state.dice_remaining is not None else cal.dice_remaining)
    clamp_note = "；骰子已按上限截断" if dice_clamped else ""
    if not rank.actions:
        return f"{cell_s}｜剩骰 {dice}｜卡池剩余 {rem}｜无可用决策动作{clamp_note}"
    best = rank.actions[0]
    return (
        f"{cell_s}｜剩骰 {dice}｜卡池剩余 {rem}｜"
        f"建议 {best.label}（{best.detail}）{clamp_note}"
    )


def suggest_action_via_model(
    board: MapBoard,
    state: GameState,
) -> tuple[str, RankResult | None]:
    """Decide via the imported model; return a status message if unavailable."""
    if state.current_cell_id is None:
        return (
            "等待识别当前格子编号（请配置 config/regions.yaml 并确保游戏画面可见）。",
            None,
        )
    if board.get(state.current_cell_id) is None:
        return (
            f"地图中不存在格子 id={state.current_cell_id}。",
            None,
        )
    if not active_model_ready():
        return (
            "尚未导入决策模型。请在界面选择并导入模型后再启动。",
            None,
        )
    model = get_active_decision_model()
    if model is None:
        return ("决策模型未就绪。", None)

    cal = load_planner_calibration()
    plan_dice = int(
        state.dice_remaining
        if state.dice_remaining is not None
        else cal.dice_remaining
    )
    dice_clamped = False
    if plan_dice > DEFAULT_DICE_REMAINING:
        plan_dice = DEFAULT_DICE_REMAINING
        dice_clamped = True
    if plan_dice < 0:
        plan_dice = 0

    name = "model"
    try:
        info = model.display_info()
        name = str(info.name or name)
    except Exception:
        pass

    next_free = bool(state.extra.get("next_roll_free", False))
    hand = list(state.lucky_card_ids or [])
    drawn = dict(cal.normalized_drawn())
    try:
        out = model.decide(
            cell_id=int(state.current_cell_id),
            dice_remaining=int(plan_dice),
            next_roll_free=next_free,
            hand=hand,
            drawn=drawn,
        )
    except Exception as exc:
        return f"决策模型调用失败: {exc}", None

    rank = rank_from_model_result(out, model_name=name)
    if not rank.actions:
        return "决策模型未返回可执行动作。", None
    text = format_model_advice(
        board, state, cal, rank, dice_clamped=dice_clamped
    )
    return text, rank


def suggest_action_text(board: MapBoard, state: GameState) -> str:
    text, _rank = suggest_action_via_model(board, state)
    return text


def format_advice(advice: str) -> str:
    return f"[建议] {advice}"
