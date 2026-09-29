from __future__ import annotations

from dataclasses import dataclass

from zephie_rolling_on.models.game_state import GameState
from zephie_rolling_on.models.lucky_card import format_owned_lucky_cards
from zephie_rolling_on.models.lucky_deck import (
    DECK_TOTAL,
    DEFAULT_DICE_REMAINING,
    apply_ocr_dice_to_calibration,
    can_draw_from_lucky_cell,
    load_planner_calibration,
    merge_calibration_into_state_dict,
)
from zephie_rolling_on.models.map_board import MapBoard
from zephie_rolling_on.app.model_decide import format_advice, suggest_action_text
from zephie_rolling_on.vision.recognize import RecognitionResult, recognize_game


@dataclass
class TickResult:
    state: GameState
    advice: str
    formatted_advice: str
    recognition: RecognitionResult | None
    log_lines: list[str]


def build_state(
    recognition: RecognitionResult | None,
    *,
    dry_cell_id: int | None = None,
) -> GameState:
    cal = load_planner_calibration()
    if dry_cell_id is not None:
        return GameState(
            current_cell_id=dry_cell_id,
            dice_remaining=cal.dice_remaining,
            deck_remaining_total=cal.deck_remaining_total(),
        )
    if recognition is None:
        return GameState(
            dice_remaining=cal.dice_remaining,
            deck_remaining_total=cal.deck_remaining_total(),
        )
    hand = list(recognition.lucky_card_ids)
    if recognition.dice_remaining is not None:
        cal = apply_ocr_dice_to_calibration(recognition.dice_remaining)
    dice_remaining = cal.dice_remaining

    cell_id = recognition.cell_id

    extra = merge_calibration_into_state_dict({}, cal, hand_ids=hand)
    extra["dice_remaining"] = dice_remaining
    if recognition.dice_used is not None:
        extra["dice_used_ocr"] = recognition.dice_used
        extra["dice_total"] = int(
            recognition.dice_used + (recognition.dice_remaining or 0)
        )
    # 视觉检测到的下次投骰种类 → 规划 RunState.next_roll_free（不再默认全是付费）
    if recognition.roll_kind is not None:
        extra["roll_kind"] = recognition.roll_kind
        extra["next_roll_free"] = recognition.roll_kind == "free"
        if recognition.roll_kind_score is not None:
            extra["roll_kind_score"] = float(recognition.roll_kind_score)
    else:
        extra["next_roll_free"] = False
    return GameState(
        current_cell_id=cell_id,
        lucky_card_count=recognition.lucky_card_count,
        lucky_card_tier=recognition.lucky_card_tier,
        lucky_card_ids=hand,
        dice_remaining=dice_remaining,
        deck_remaining_total=cal.deck_remaining_total(),
        hand_full=not can_draw_from_lucky_cell(hand_count=len(hand)),
        extra=extra,
    )


def run_tick(
    board: MapBoard,
    regions: dict,
    *,
    hwnd: int | None = None,
    dry_cell_id: int | None = None,
) -> TickResult:
    log_lines: list[str] = []
    recognition: RecognitionResult | None = None

    if dry_cell_id is not None:
        log_lines.append(f"(测试模式，假定格子 {dry_cell_id})")
        state = build_state(recognition, dry_cell_id=dry_cell_id)
    else:
        recognition = recognize_game(regions, hwnd=hwnd)
        state = build_state(recognition, dry_cell_id=dry_cell_id)
        cal = load_planner_calibration()

        if recognition.cell_id is not None:
            log_lines.append(
                f"当前格子 id={recognition.cell_id}（OCR: {recognition.raw_cell_text!r}）",
            )
        elif recognition.raw_cell_text:
            log_lines.append(f"格子 OCR 未解析: {recognition.raw_cell_text!r}")
        if recognition.notes:
            log_lines.append(recognition.notes)
        if recognition.roll_kind is not None:
            kind_cn = {
                "free": "免费",
                "paid": "付费",
                "start": "START",
            }.get(recognition.roll_kind, recognition.roll_kind)
            score = recognition.roll_kind_score
            score_s = f"{score:.2f}" if score is not None else "?"
            log_lines.append(f"投骰种类: {kind_cn}({recognition.roll_kind}) score={score_s}")
        log_lines.append(
            f"幸运卡: {format_owned_lucky_cards(recognition.lucky_card_ids)}",
        )
        if recognition.raw_dice_text or recognition.dice_remaining is not None:
            used = recognition.dice_used
            rem = recognition.dice_remaining
            log_lines.append(
                f"投掷 OCR: {recognition.raw_dice_text!r} "
                f"已用={used} 剩余={rem} (上限{DEFAULT_DICE_REMAINING})",
            )
        if recognition.dice_remaining is not None:
            log_lines.append(
                f"卡组剩余 {cal.deck_remaining_total()}/{DECK_TOTAL} 张 | "
                f"剩余骰子 {cal.dice_remaining} (已写入校准)",
            )
        else:
            log_lines.append(cal.summary_line())
        if len(recognition.lucky_card_ids) >= 5:
            log_lines.append("手牌已满：再踩幸运格不会获得新卡。")
    advice = suggest_action_text(board, state)
    formatted = format_advice(advice)
    log_lines.append(formatted)

    return TickResult(
        state=state,
        advice=advice,
        formatted_advice=formatted,
        recognition=recognition,
        log_lines=log_lines,
    )
