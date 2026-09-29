from __future__ import annotations

from zephie_rolling_on.paths import project_root

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml

from zephie_rolling_on.vision.capture import capture_screen, capture_window_client, crop_region
from zephie_rolling_on.vision.lucky_match import (
    LuckyCardMatch,
    match_lucky_slot,
    save_lucky_slot_debug,
    should_save_lucky_slot_debug,
)
from zephie_rolling_on.vision.ocr_debug import (
    format_patch_stats,
    save_cell_ocr_debug,
    save_dice_ocr_debug,
)
from zephie_rolling_on.vision.roll_kind import RollKind, find_roll_kind

from zephie_rolling_on.vision.ocr_runtime import get_ocr_reader, ocr_readtext, preload_ocr_reader

# 游戏内显示形如「123格」
_CELL_ID_PATTERN = re.compile(r"(\d+)\s*格")
# Optional "used/total" form (e.g. 45/100); plain digits also accepted.
_DICE_USAGE_PATTERN = re.compile(r"(\d+)\s*[/／]\s*(\d+)")

# OCR confusables → digits, then drop other Latin letters.
_CONFUSABLE_TO_DIGIT = str.maketrans(
    {
        "g": "9",
        "G": "9",
        "q": "9",
        "o": "0",
        "O": "0",
        "l": "1",
        "I": "1",
        "z": "2",
        "Z": "2",
        "s": "5",
        "S": "5",
    }
)
_LATIN_LETTERS = re.compile(r"[A-Za-z]")

_CELL_OCR_ALLOWED = frozenset("0123456789格")
_DICE_OCR_ALLOWED = frozenset("0123456789/／")

OcrFilterMode = Literal["cell", "dice", "none"]


def _strip_after_confusable_map(text: str, *, allowed: frozenset[str]) -> str:
    mapped = text.translate(_CONFUSABLE_TO_DIGIT)
    no_latin = _LATIN_LETTERS.sub("", mapped)
    return "".join(ch for ch in no_latin if ch in allowed)


def normalize_cell_ocr_text(raw: str) -> str:
    """格子 OCR：仅保留数字与「格」；其余字母过滤（g/G→9 后保留）。"""
    if not raw:
        return ""
    return _strip_after_confusable_map(raw, allowed=_CELL_OCR_ALLOWED)


def normalize_dice_ocr_text(raw: str) -> str:
    """Dice OCR: keep digits (and slash); drop Latin letters."""
    if not raw:
        return ""
    return _strip_after_confusable_map(raw, allowed=_DICE_OCR_ALLOWED)


@dataclass
class RecognitionResult:
    cell_id: int | None
    lucky_card_count: int
    lucky_card_tier: int
    raw_cell_text: str
    raw_lucky_text: str = ""
    lucky_card_ids: list[str] = field(default_factory=list)
    lucky_matches: list[LuckyCardMatch] = field(default_factory=list)
    dice_used: int | None = None
    dice_remaining: int | None = None
    raw_dice_text: str = ""
    # 左下投骰按钮：free=DOUBLE / paid=橙骰；未命中为 None
    roll_kind: RollKind | None = None
    roll_kind_score: float | None = None
    notes: str = ""
    timing_ms: dict[str, float] = field(default_factory=dict)


def load_regions(config_path: Path | None = None) -> dict[str, Any]:
    from zephie_rolling_on.data.adventure_frame import resolve_regions

    root = project_root()
    path = config_path or (root / "config" / "regions.yaml")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return resolve_regions(raw)


def _get_ocr_reader() -> Any | None:
    return get_ocr_reader()


def _prepare_small_patch_for_ocr(patch: np.ndarray, *, min_width: int = 160, min_height: int = 48) -> np.ndarray:
    """小区域放大后再 OCR。"""
    if patch is None or patch.size == 0:
        return patch
    import cv2

    h, w = patch.shape[:2]
    scale = 1
    if w < min_width or h < min_height:
        scale = max(3, int(min_width / max(w, 1)))
    if scale > 1:
        patch = cv2.resize(
            patch,
            (w * scale, h * scale),
            interpolation=cv2.INTER_CUBIC,
        )
    return patch


def _cell_display_thresholds(config: dict[str, Any]) -> dict[str, float | int]:
    bright_luma = int(config.get("cell_display_bright_luma", 200))
    return {
        "bright_luma": bright_luma,
        "min_ratio": float(config.get("cell_display_min_bright_ratio", 0.01)),
        "min_max_luma": int(config.get("cell_display_min_max_luma", bright_luma)),
        "strong_peak": int(config.get("cell_display_strong_peak_luma", 235)),
        "min_bright_pixels": int(config.get("cell_display_min_bright_pixels", 8)),
        "min_contrast": float(config.get("cell_display_min_contrast", 40)),
        "min_std": float(config.get("cell_display_min_std", 18)),
    }


def is_cell_display_active(
    patch: np.ndarray,
    config: dict[str, Any] | None = None,
) -> bool:
    """
    格子 ROI 是否为「正常显示」状态（可 OCR、可触发规划）。

    变灰过场时数字与背景一起被压暗（无亮白笔画）；正常时数字为亮白高对比。
    灰屏压暗后峰值常落在 150～180，旧阈值 150 会误判为亮屏，故提高到 ~200，
    并辅以对比度 / 亮度标准差，避免均匀灰雾或噪声碎块误触发。
    """
    if patch is None or patch.size == 0:
        return False
    cfg = config or {}
    params = _cell_display_thresholds(cfg)
    bright_luma = int(params["bright_luma"])
    min_ratio = float(params["min_ratio"])
    min_max_luma = int(params["min_max_luma"])
    strong_peak = int(params["strong_peak"])
    min_bright_pixels = int(params["min_bright_pixels"])
    min_contrast = float(params["min_contrast"])
    min_std = float(params["min_std"])
    if patch.ndim == 3:
        luma = np.max(patch[:, :, :3], axis=2)
    else:
        luma = patch
    max_luma = float(luma.max())
    if max_luma < min_max_luma:
        return False
    if float(luma.std()) < min_std:
        return False
    contrast = max_luma - float(np.percentile(luma, 25))
    if contrast < min_contrast:
        return False
    bright_mask = luma >= bright_luma
    bright_count = int(bright_mask.sum())
    bright_ratio = float(bright_count) / float(luma.size)
    if max_luma >= min_max_luma and bright_ratio >= min_ratio:
        return True
    # 宽 ROI 仅 1～2 位亮白数字时占比可能略低于 min_ratio（如 54px 裁切「1」）
    if max_luma >= strong_peak and bright_count >= min_bright_pixels:
        return True
    return False


def _dice_ocr_variants(patch: np.ndarray) -> list[np.ndarray]:
    """骰子 ROI 多种预处理，提高单数字「0」等小字识别率。"""
    import cv2

    base = _prepare_small_patch_for_ocr(patch, min_width=280, min_height=80)
    if base is None or base.size == 0:
        return []
    out: list[np.ndarray] = [base]
    if len(base.shape) == 3:
        gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    else:
        gray = base
    # 对比度增强后再二值化（浅色字 / 深色字各试一次）
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        enhanced = clahe.apply(gray)
    except Exception:
        enhanced = gray
    _, bw = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, bw_inv = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    for g in (bw, bw_inv):
        out.append(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))
    return out


def _ocr_dice_from_patch(patch: np.ndarray) -> str:
    """对骰子 ROI 多图多策略 OCR，合并为一条文本。"""
    if _get_ocr_reader() is None:
        return ""
    allow = "0123456789/／"
    best = ""
    for variant in _dice_ocr_variants(patch):
        for use_allow in (True, False):
            results = ocr_readtext(
                variant,
                allowlist=allow if use_allow else None,
            )
            parts: list[str] = []
            for _bbox, t, _conf in results:
                chunk = normalize_dice_ocr_text(t)
                if chunk:
                    parts.append(chunk)
            merged = "".join(parts).strip()
            if merged and (not best or len(merged) <= len(best) + 2):
                best = merged
            used, _rem = parse_dice_usage_from_text(merged, default_total=100)
            if used is not None:
                return merged
    return best


def _dice_failure_note(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    raw: str,
    dice_remaining: int | None,
) -> str:
    region = config.get("dice_usage_region", {})
    if not region.get("width"):
        return "请在 config/regions.yaml 配置 dice_usage_region"
    patch = crop_region(frame, region)
    if patch is None or patch.size == 0:
        return (
            "骰子 ROI 裁剪为空（坐标超出游戏客户区，或窗口被缩放；"
            "请用鼠标坐标核对 regions.yaml 里 dice_usage_region）"
        )
    if dice_remaining is None and not raw:
        return (
            "未能识别投掷次数（OCR 无数字；请用「测试骰子检测」确认 ROI 框住已用次数，"
            "开局显示 0 时也要把「0」完整框进去）"
        )
    if dice_remaining is None and raw:
        return f"未能识别投掷次数（OCR: {raw!r}）"
    return ""


def _digit_ocr_engine_name(config: dict[str, Any]) -> str:
    """digit_ocr_engine, falling back to cell_id_engine."""
    raw = config.get("digit_ocr_engine", config.get("cell_id_engine", "fast"))
    return str(raw).strip().lower()


def _easyocr_installed() -> bool:
    from importlib.util import find_spec

    return find_spec("easyocr") is not None


def _recognize_dice_usage(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    timing: dict[str, float] | None = None,
) -> tuple[int | None, int | None, str]:
    """投掷次数 OCR；失败时返回空，不影响格子识别。"""
    region = config.get("dice_usage_region", {})
    if not region.get("width"):
        return None, None, ""
    try:
        patch = crop_region(frame, region)
        if patch is None or patch.size == 0:
            return None, None, ""
        total = int(config.get("dice_total", 100))
        engine = _digit_ocr_engine_name(config)
        t_ocr = time.perf_counter()

        if engine == "fast":
            from zephie_rolling_on.vision.cell_id_reader import read_int_from_patch

            used = read_int_from_patch(patch, kind="dice", config=config)
            if timing is not None:
                timing["dice_ocr"] = (time.perf_counter() - t_ocr) * 1000.0
            if used is not None:
                return used, max(0, total - used), str(used)
            if not bool(config.get("dice_ocr_fallback_legacy", True)):
                return None, None, ""
            # RapidOCR 失败 → 回退 EasyOCR（懒加载，不预加载）

        # legacy：EasyOCR 多变体（见 _ocr_dice_from_patch）
        if _get_ocr_reader() is None:
            return None, None, ""
        t_legacy = time.perf_counter()
        raw = _ocr_dice_from_patch(patch).strip()
        if timing is not None:
            timing["dice_ocr"] = (time.perf_counter() - t_legacy) * 1000.0
        used, remaining = parse_dice_usage_from_text(raw, default_total=total)
        return used, remaining, raw
    except Exception:
        return None, None, ""


def _ocr_text(
    image: np.ndarray,
    *,
    mode: OcrFilterMode = "none",
    detail: bool = False,
) -> str | tuple[str, list]:
    if image is None or image.size == 0:
        return ("", []) if detail else ""
    if _get_ocr_reader() is None:
        return ("", []) if detail else ""
    results = ocr_readtext(image)
    parts: list[str] = []
    lines: list[str] = []
    for _bbox, t, conf in results:
        chunk = t
        if mode == "cell":
            chunk = normalize_cell_ocr_text(t)
        elif mode == "dice":
            chunk = normalize_dice_ocr_text(t)
        if chunk:
            parts.append(chunk)
        if detail:
            lines.append(f"{t!r}->{chunk!r} conf={conf:.2f}")
    text = "".join(parts)
    if detail:
        return text, lines
    return text


def _ocr_text_simple(image: np.ndarray, *, mode: OcrFilterMode = "none") -> str:
    out = _ocr_text(image, mode=mode, detail=False)
    return out if isinstance(out, str) else out[0]


def parse_dice_usage_from_text(
    text: str,
    *,
    default_total: int = 100,
) -> tuple[int | None, int | None]:
    """Parse dice-used count; returns (used, remaining) or (None, None)."""
    if not text or default_total <= 0:
        return None, None
    normalized = normalize_dice_ocr_text(text)
    total = default_total
    used: int | None = None

    match = _DICE_USAGE_PATTERN.search(normalized)
    if match:
        used = int(match.group(1))
        total = int(match.group(2))
    else:
        parts = [p for p in re.split(r"[/／]", normalized) if p.isdigit()]
        if len(parts) >= 2:
            used = int(parts[0])
            total = int(parts[1])
        else:
            digits = "".join(ch for ch in normalized if ch.isdigit())
            if not digits.isdigit():
                return None, None
            n = int(digits)
            suffix = str(default_total)
            if n > default_total and digits.endswith(suffix) and len(digits) > len(suffix):
                # 旧 ROI 斜杠丢失：9/100 → 9100
                used = int(digits[: -len(suffix)])
            elif n <= default_total:
                used = n
            else:
                return None, None

    if total <= 0:
        total = default_total
    used = max(0, min(used, total))
    return used, max(0, total - used)


def _cell_digit_width_frac(config: dict[str, Any]) -> float:
    frac = float(config.get("cell_digit_width_frac", 0.72))
    # ROI 已仅框数字时可配 1.0；旧 ROI 含「格」字时仍限制在 0.92 以内
    upper = 1.0 if frac >= 1.0 else 0.92
    return min(upper, max(0.45, frac))


def _crop_cell_digit_band(patch: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    """裁掉右侧「格」字，只对数字带做 OCR（避免 167 被读成 16格）。"""
    if patch is None or patch.size == 0:
        return patch
    frac = _cell_digit_width_frac(config)
    if frac >= 1.0:
        return patch
    _h, w = patch.shape[:2]
    x1 = max(1, int(w * frac))
    return patch[:, :x1]


def _parse_cell_id_from_digits_only(text: str) -> int | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits.isdigit() or len(digits) > 4 or len(digits) < 1:
        return None
    return int(digits)


def _best_digit_ocr_chunk(parts: list[tuple[str, float]]) -> tuple[str, float]:
    """从数字带多框 OCR 中取最优单框，避免把 567+8 拼成 5678。"""
    if not parts:
        return "", 0.0
    multi = [(text, conf) for text, conf in parts if len(text) >= 2]
    if multi:
        text, conf = max(multi, key=lambda item: (item[1], len(item[0])))
        return text, conf
    text, conf = max(parts, key=lambda item: (len(item[0]), item[1]))
    return text, conf


def _normalize_digits_only_candidate(text: str) -> int | None:
    """全幅仅数字 OCR：8671（格边误识为 1）→ 867。"""
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits.isdigit() or not digits:
        return None
    if len(digits) == 4 and digits.endswith("1"):
        trimmed = digits[:-1]
        if trimmed.isdigit() and 1 <= len(trimmed) <= 3:
            return int(trimmed)
    if len(digits) > 4:
        return None
    return int(digits)


def _cell_step_plausible(cell_id: int, anchor: int, max_delta: int) -> bool:
    return abs(cell_id - anchor) <= max_delta


def _cell_step_limits(config: dict[str, Any]) -> tuple[int, int]:
    strict = int(config.get("cell_max_step_delta", 150))
    relaxed = int(config.get("cell_relaxed_step_delta", 500))
    return max(1, strict), max(strict, relaxed)


def _pick_cell_id_candidate(
    digit_id: int | None,
    digit_raw: str,
    full_id: int | None,
    full_raw: str,
    *,
    digit_conf: float = 0.0,
    full_conf: float = 0.0,
    anchor_cell_id: int | None = None,
    max_step_delta: int = 150,
    relaxed_step_delta: int = 500,
    extra_id: int | None = None,
    extra_raw: str = "",
    extra_conf: float = 0.0,
    suffix_id: int | None = None,
    suffix_raw: str = "",
    suffix_conf: float = 0.0,
) -> tuple[int | None, str]:
    """在「数字带」与「全幅」两路 OCR 间择优。

    - 有锚点格子时：先按步长过滤误读（如 238 vs 2333），再择优。
    - 无锚点时：位数不同取更多位（167→16）；位数相同取置信度更高。
    """
    candidates: list[tuple[int, str, float, bool]] = []
    if digit_id is not None:
        candidates.append((digit_id, digit_raw, digit_conf, False))
    if full_id is not None:
        candidates.append((full_id, full_raw, full_conf, True))
    if extra_id is not None:
        candidates.append((extra_id, extra_raw, extra_conf, False))
    if suffix_id is not None:
        candidates.append((suffix_id, suffix_raw, suffix_conf, False))

    if not candidates:
        return None, full_raw or digit_raw or extra_raw

    # 去重：同 id 保留更高置信
    deduped: dict[int, tuple[int, str, float, bool]] = {}
    for item in candidates:
        prev = deduped.get(item[0])
        if prev is None or item[2] > prev[2]:
            deduped[item[0]] = item
    candidates = list(deduped.values())

    if anchor_cell_id is not None:
        for limit in (max_step_delta, relaxed_step_delta):
            plausible = [
                c for c in candidates if _cell_step_plausible(c[0], anchor_cell_id, limit)
            ]
            if len(plausible) == 1:
                cid, raw, _conf, from_full = plausible[0]
                if cid == suffix_id and suffix_raw:
                    raw = suffix_raw
                elif not from_full and cid == digit_id and digit_raw:
                    raw = f"{digit_raw}格"
                elif "格" not in raw and cid is not None:
                    raw = f"{cid}格"
                return cid, raw
            if len(plausible) >= 2:
                cid, raw, _conf, from_full = min(
                    plausible,
                    key=lambda c: (abs(c[0] - anchor_cell_id), -c[2]),
                )
                if cid == suffix_id and suffix_raw:
                    raw = suffix_raw
                elif not from_full and cid == digit_id and digit_raw:
                    raw = f"{digit_raw}格"
                elif "格" not in raw and cid is not None:
                    raw = f"{cid}格"
                return cid, raw
        else:
            return None, full_raw or digit_raw

    if full_id is not None:
        for alt_id, alt_raw in ((digit_id, digit_raw), (extra_id, extra_raw)):
            if alt_id is None:
                continue
            if (
                len(str(alt_id)) == len(str(full_id)) + 1
                and str(alt_id).startswith(str(full_id))
                and (
                    anchor_cell_id is None
                    or _cell_step_plausible(alt_id, anchor_cell_id, relaxed_step_delta)
                )
            ):
                return alt_id, alt_raw if alt_raw else str(alt_id)

    if digit_id is not None and full_id is not None:
        dl, fl = len(str(digit_id)), len(str(full_id))
        if anchor_cell_id is None and dl != fl:
            chosen = (digit_id, digit_raw) if dl > fl else (full_id, full_raw)
            return chosen
        return (full_id, full_raw) if full_conf >= digit_conf else (digit_id, digit_raw)
    if digit_id is not None:
        return digit_id, digit_raw
    if full_id is not None:
        return full_id, full_raw
    return None, full_raw or digit_raw


def _cell_id_engine_name(config: dict[str, Any]) -> str:
    return _digit_ocr_engine_name(config)


def _ocr_cell_from_patch_legacy(
    digit_patch: np.ndarray,
    config: dict[str, Any],
    *,
    anchor_cell_id: int | None = None,
) -> tuple[str, float, list[str], list[dict[str, Any]], int | None]:
    """legacy：亮字连通域分割 + 逐字 EasyOCR（见 vision/cell_ocr.py）。"""
    from zephie_rolling_on.vision.cell_ocr import read_cell_digits

    digit_raw, digit_conf, detail, glyph_records = read_cell_digits(
        digit_patch, config
    )
    digit_id = _parse_cell_id_from_digits_only(digit_raw)
    return digit_raw, digit_conf, detail, glyph_records, digit_id


def _finalize_cell_ocr_result(
    *,
    digit_id: int | None,
    digit_raw: str,
    digit_conf: float,
    detail: list[str],
    glyph_records: list[dict[str, Any]],
    config: dict[str, Any],
    anchor_cell_id: int | None,
) -> tuple[int | None, str, list[str], dict[str, Any]]:
    strict_delta, relaxed_delta = _cell_step_limits(config)
    cell_id, raw = _pick_cell_id_candidate(
        digit_id,
        digit_raw,
        None,
        "",
        digit_conf=digit_conf,
        full_conf=0.0,
        anchor_cell_id=anchor_cell_id,
        max_step_delta=strict_delta,
        relaxed_step_delta=relaxed_delta,
    )
    if cell_id is not None and "格" not in raw and digit_raw:
        raw = f"{digit_raw}格"
    if cell_id is None and anchor_cell_id is not None and digit_id is not None:
        detail.append(
            f"[步长校验] 锚点={anchor_cell_id} 候选={digit_id} 超步长范围",
        )
    diagnostic = {
        "cell_id": cell_id,
        "raw_cell_text": raw,
        "digit_id": digit_id,
        "digit_raw": digit_raw,
        "digit_conf": round(digit_conf, 4),
        "anchor_cell_id": anchor_cell_id,
        "glyphs": glyph_records,
        "ocr_detail": detail,
    }
    return cell_id, raw, detail, diagnostic


def _ocr_cell_from_patch(
    patch: np.ndarray,
    config: dict[str, Any],
    *,
    anchor_cell_id: int | None = None,
) -> tuple[int | None, str, list[str], dict[str, Any]]:
    """格子 OCR：默认 RapidOCR 快路径；失败可回退 legacy 连通域管线。"""
    digit_patch = _crop_cell_digit_band(patch, config)
    engine = _cell_id_engine_name(config)

    if engine == "fast":
        from zephie_rolling_on.vision.cell_id_reader import read_int_from_patch

        fast_id = read_int_from_patch(digit_patch, kind="cell", config=config)
        if fast_id is not None:
            digit_raw = str(fast_id)
            return _finalize_cell_ocr_result(
                digit_id=fast_id,
                digit_raw=digit_raw,
                digit_conf=1.0,
                detail=[f"[RapidOCR] → {digit_raw}"],
                glyph_records=[],
                config=config,
                anchor_cell_id=anchor_cell_id,
            )
        if not bool(config.get("cell_id_fallback_legacy", True)):
            return _finalize_cell_ocr_result(
                digit_id=None,
                digit_raw="",
                digit_conf=0.0,
                detail=["[RapidOCR] 未识别且未开启 legacy 回退"],
                glyph_records=[],
                config=config,
                anchor_cell_id=anchor_cell_id,
            )
        if not _easyocr_installed():
            return _finalize_cell_ocr_result(
                digit_id=None,
                digit_raw="",
                digit_conf=0.0,
                detail=["[RapidOCR] 未识别（未安装 EasyOCR，跳过 legacy）"],
                glyph_records=[],
                config=config,
                anchor_cell_id=anchor_cell_id,
            )

    digit_raw, digit_conf, detail, glyph_records, digit_id = (
        _ocr_cell_from_patch_legacy(
            digit_patch,
            config,
            anchor_cell_id=anchor_cell_id,
        )
    )
    if engine == "fast":
        detail.insert(0, "[RapidOCR] 未识别，已回退 legacy")
    return _finalize_cell_ocr_result(
        digit_id=digit_id,
        digit_raw=digit_raw,
        digit_conf=digit_conf,
        detail=detail,
        glyph_records=glyph_records,
        config=config,
        anchor_cell_id=anchor_cell_id,
    )


def parse_cell_id_from_text(text: str) -> int | None:
    """解析 OCR 文本中的「整数+格」，例如 42格。"""
    if not text:
        return None
    normalized = normalize_cell_ocr_text(text)
    # 勿把「45/100」等投掷次数误当成格子号
    if _DICE_USAGE_PATTERN.search(normalized):
        return None
    match = _CELL_ID_PATTERN.search(normalized)
    if match:
        return int(match.group(1))
    if "格" not in normalized and "/" in normalized:
        return None
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if digits.isdigit() and len(digits) <= 4:
        return int(digits)
    return None


def _should_save_ocr_debug(config: dict[str, Any]) -> bool:
    dev = _load_dev_config_cached()
    if "save_ocr_debug" in dev:
        return bool(dev.get("save_ocr_debug"))
    return bool(config.get("save_ocr_debug", False))


def _load_dev_config_cached() -> dict[str, Any]:
    dev_path = project_root() / "config" / "dev.yaml"
    mtime = dev_path.stat().st_mtime if dev_path.is_file() else 0.0
    cached = getattr(_load_dev_config_cached, "_cache", None)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    dev: dict[str, Any] = {}
    if dev_path.is_file():
        with dev_path.open(encoding="utf-8") as f:
            dev = yaml.safe_load(f) or {}
    _load_dev_config_cached._cache = (mtime, dev)  # type: ignore[attr-defined]
    return dev


def should_log_recognition_timing() -> bool:
    return bool(_load_dev_config_cached().get("recognition_timing_debug", False))


_TIMING_LABELS: dict[str, str] = {
    "capture": "截屏",
    "cell_ocr": "格子OCR",
    "roll_kind": "免费/付费骰",
    "dice_ocr": "骰子OCR",
    "lucky_match": "幸运卡匹配",
    "total": "合计",
}

_TIMING_ORDER = ("capture", "cell_ocr", "roll_kind", "dice_ocr", "lucky_match", "total")


def format_recognition_timing(timing_ms: dict[str, float]) -> str:
    if not timing_ms:
        return ""
    parts: list[str] = []
    for key in _TIMING_ORDER:
        if key in timing_ms:
            parts.append(f"{_TIMING_LABELS[key]}={timing_ms[key]:.0f}ms")
    for key, ms in timing_ms.items():
        if key not in _TIMING_ORDER:
            parts.append(f"{key}={ms:.0f}ms")
    return " ".join(parts)


def _new_timing_dict() -> dict[str, float] | None:
    return {} if should_log_recognition_timing() else None


def _acquire_recognition_frame(
    hwnd: int | None,
    frame: np.ndarray | None,
    timing: dict[str, float] | None,
) -> np.ndarray | None:
    """已有帧则直接复用；否则截屏一次（耗时写入 timing['capture']）。"""
    if frame is not None:
        return frame
    t_cap = time.perf_counter()
    got = capture_window_client(hwnd) if hwnd else capture_screen()
    if timing is not None and got is not None:
        timing["capture"] = (time.perf_counter() - t_cap) * 1000.0
    return got


def recognize_cell_id_on_frame(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    anchor_cell_id: int | None = None,
) -> RecognitionResult:
    """已有帧时仅 OCR 格子（不跑骰子/幸运卡），供自动点击同格等待轮询。"""
    default_tier = int(config.get("default_lucky_tier", 0))
    timing = _new_timing_dict()
    t0 = time.perf_counter()
    cell_id, raw_cell, cell_notes = _recognize_cell_on_frame(
        frame,
        config,
        anchor_cell_id=anchor_cell_id,
        save_debug=False,
        timing=timing,
    )
    if timing is not None:
        timing["total"] = (time.perf_counter() - t0) * 1000.0
    return RecognitionResult(
        cell_id=cell_id,
        lucky_card_count=0,
        lucky_card_tier=default_tier,
        raw_cell_text=raw_cell,
        lucky_card_ids=[],
        lucky_matches=[],
        notes=cell_notes,
        timing_ms=timing or {},
    )


def recognize_frame(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    save_debug: bool | None = None,
    debug_snapshot: bool = False,
    anchor_cell_id: int | None = None,
) -> RecognitionResult:
    default_tier = int(config.get("default_lucky_tier", 0))
    region = config.get("cell_id_region", {})
    if not region.get("width"):
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=default_tier,
            raw_cell_text="",
            notes="请在 config/regions.yaml 配置 cell_id_region（相对游戏客户区左上角）",
        )

    patch = crop_region(frame, region)
    debug_note = ""
    do_save_cell = (
        save_debug if save_debug is not None else _should_save_ocr_debug(config)
    )

    timing = _new_timing_dict()
    t0 = time.perf_counter()
    cell_id, raw_cell, cell_notes = _recognize_cell_on_frame(
        frame,
        config,
        anchor_cell_id=anchor_cell_id,
        save_debug=do_save_cell,
        debug_snapshot=debug_snapshot,
        timing=timing,
    )
    if do_save_cell and patch is not None and patch.size > 0:
        lit = is_cell_display_active(patch, config)
        where = (
            "data/debug/cell_ocr_region.png"
            if debug_snapshot
            else "data/debug/cell_ocr/"
        )
        lit_label = "正常" if lit else (
            "变灰（已保存调试图，跳过 OCR）" if debug_snapshot else "变灰跳过"
        )
        debug_note = (
            f"已保存格子 OCR 调试图至 {where}。{format_patch_stats(patch)}。"
            f" 显示状态={lit_label}。"
        )

    lucky_count = 0
    raw_lucky = ""
    lucky_tier = default_tier
    lucky_ids: list[str] = []
    lucky_matches: list[LuckyCardMatch] = []
    lucky_notes = ""
    roll_kind: RollKind | None = None
    roll_kind_score: float | None = None

    t_rk = time.perf_counter()
    rk_hit = find_roll_kind(frame, config)
    if timing is not None:
        timing["roll_kind"] = (time.perf_counter() - t_rk) * 1000.0
    if rk_hit is not None:
        roll_kind = rk_hit.kind
        roll_kind_score = float(rk_hit.score)

    lucky_region = config.get("lucky_cards_region", {})
    if lucky_region.get("width"):
        lucky_patch = crop_region(frame, lucky_region)
        threshold = config.get("lucky_match_threshold")
        t_lucky = time.perf_counter()
        lucky_result = match_lucky_slot(
            lucky_patch,
            threshold=float(threshold) if threshold is not None else None,
            max_cards=int(config.get("lucky_match_max_cards", 5)),
            config=config,
        )
        if timing is not None:
            timing["lucky_match"] = (time.perf_counter() - t_lucky) * 1000.0
        lucky_count = lucky_result.count
        lucky_tier = lucky_result.tier if lucky_result.tier else default_tier
        lucky_ids = lucky_result.card_ids
        lucky_matches = list(lucky_result.matches)
        raw_lucky = lucky_result.summary
        lucky_notes = lucky_result.summary

        save_lucky = (
            save_debug if save_debug is not None else should_save_lucky_slot_debug(config)
        )
        if save_lucky:
            save_lucky_slot_debug(
                lucky_patch,
                lucky_result.matches,
                summary=lucky_result.summary,
                snapshot=debug_snapshot,
                config=config,
            )
            lucky_where = (
                "data/debug/lucky_slot.png"
                if debug_snapshot
                else "data/debug/lucky_slot/"
            )
            debug_note = (debug_note + f" 已保存幸运卡调试图至 {lucky_where}。").strip()

    dice_used, dice_remaining, raw_dice = _recognize_dice_usage(
        frame, config, timing=timing
    )

    notes = debug_note
    dice_note = _dice_failure_note(
        frame, config, raw=raw_dice, dice_remaining=dice_remaining
    )
    if cell_id is None:
        notes = cell_notes + (f" {debug_note}" if debug_note else "")
    if dice_note:
        notes = f"{notes} {dice_note}".strip() if notes else dice_note

    if timing is not None:
        timing["total"] = (time.perf_counter() - t0) * 1000.0

    return RecognitionResult(
        cell_id=cell_id,
        lucky_card_count=lucky_count,
        lucky_card_tier=lucky_tier,
        raw_cell_text=raw_cell,
        raw_lucky_text=raw_lucky,
        lucky_card_ids=lucky_ids,
        lucky_matches=lucky_matches,
        dice_used=dice_used,
        dice_remaining=dice_remaining,
        raw_dice_text=raw_dice,
        roll_kind=roll_kind,
        roll_kind_score=roll_kind_score,
        notes=notes,
        timing_ms=timing or {},
    )


def recognize_cell_and_dice(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    hand_ids: list[str] | None = None,
    anchor_cell_id: int | None = None,
) -> RecognitionResult:
    """仅 OCR 格子号与剩余骰子；手牌由调用方传入（暂停恢复时不识别幸运卡槽）。"""
    default_tier = int(config.get("default_lucky_tier", 0))
    hand = list(hand_ids or [])
    timing = _new_timing_dict()
    t0 = time.perf_counter()
    cell_id, raw_cell, cell_notes = _recognize_cell_on_frame(
        frame, config, anchor_cell_id=anchor_cell_id, timing=timing
    )
    dice_used, dice_remaining, raw_dice = _recognize_dice_usage(
        frame, config, timing=timing
    )
    notes = cell_notes or ""
    dice_note = _dice_failure_note(
        frame, config, raw=raw_dice, dice_remaining=dice_remaining
    )
    if dice_note:
        notes = f"{notes} {dice_note}".strip() if notes else dice_note
    if timing is not None:
        timing["total"] = (time.perf_counter() - t0) * 1000.0
    return RecognitionResult(
        cell_id=cell_id,
        lucky_card_count=len(hand),
        lucky_card_tier=default_tier,
        raw_cell_text=raw_cell,
        lucky_card_ids=hand,
        lucky_matches=[],
        dice_used=dice_used,
        dice_remaining=dice_remaining,
        raw_dice_text=raw_dice,
        notes=notes,
        timing_ms=timing or {},
    )


def _recognize_cell_on_frame(
    frame: np.ndarray,
    config: dict[str, Any],
    *,
    anchor_cell_id: int | None = None,
    save_debug: bool = False,
    debug_snapshot: bool = False,
    timing: dict[str, float] | None = None,
) -> tuple[int | None, str, str]:
    """仅 OCR 格子区域，返回 (cell_id, raw_cell_text, notes)。"""
    region = config.get("cell_id_region", {})
    if not region.get("width"):
        return (
            None,
            "",
            "请在 config/regions.yaml 配置 cell_id_region（相对游戏客户区左上角）",
        )
    patch = crop_region(frame, region)
    lit = (
        patch is not None
        and patch.size > 0
        and is_cell_display_active(patch, config)
    )

    if save_debug and debug_snapshot and patch is not None and patch.size > 0 and not lit:
        save_cell_ocr_debug(
            patch,
            {
                "cell_id": None,
                "raw_cell_text": "",
                "display_lit": False,
                "skipped_reason": "格子区域变暗（数字非亮白），跳过 OCR",
            },
            snapshot=True,
        )
        return None, "", "格子区域变暗（数字非亮白），已保存调试图"

    if not lit:
        return None, "", "格子区域变暗（数字非亮白），暂不识别"
    engine = _cell_id_engine_name(config)
    if engine != "fast" and _get_ocr_reader() is None:
        if save_debug and debug_snapshot:
            save_cell_ocr_debug(
                patch,
                {
                    "cell_id": None,
                    "raw_cell_text": "",
                    "display_lit": True,
                    "skipped_reason": "未安装 easyocr",
                },
                snapshot=True,
            )
        return None, "", "未安装 easyocr，请执行: pip install easyocr"
    t_ocr = time.perf_counter()
    cell_id, raw_cell, ocr_lines, ocr_diag = _ocr_cell_from_patch(
        patch, config, anchor_cell_id=anchor_cell_id
    )
    if timing is not None:
        timing["cell_ocr"] = (time.perf_counter() - t_ocr) * 1000.0
    if save_debug:
        ocr_diag["display_lit"] = lit
        save_cell_ocr_debug(patch, ocr_diag, snapshot=debug_snapshot)
    if cell_id is None:
        detail = "; ".join(ocr_lines) if ocr_lines else "无 OCR 候选"
        anchor_note = (
            f"（相对锚点 {anchor_cell_id} 步长不合理）" if anchor_cell_id is not None else ""
        )
        notes = f"未能识别「数字+格」{anchor_note}（OCR: {raw_cell!r}；明细: {detail}）。"
        return None, raw_cell, notes
    return cell_id, raw_cell, ""


def recognize_cell_id_only(
    config: dict[str, Any] | None = None,
    *,
    hwnd: int | None = None,
) -> RecognitionResult:
    """仅截屏 + 格子 OCR（规划循环轻量检测）。"""
    cfg = config or load_regions()
    default_tier = int(cfg.get("default_lucky_tier", 0))
    if hwnd:
        frame = capture_window_client(hwnd)
    else:
        frame = capture_screen()
    if frame is None:
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=default_tier,
            raw_cell_text="",
            notes="无法截取游戏窗口",
        )
    cell_id, raw_cell, notes = _recognize_cell_on_frame(frame, cfg)
    return RecognitionResult(
        cell_id=cell_id,
        lucky_card_count=0,
        lucky_card_tier=default_tier,
        raw_cell_text=raw_cell,
        notes=notes,
    )


def recognize_dice_only(
    config: dict[str, Any] | None = None,
    *,
    hwnd: int | None = None,
    frame: np.ndarray | None = None,
    save_debug: bool = False,
) -> RecognitionResult:
    """仅截屏 + 投掷次数 OCR（规划循环 / GUI 测试骰子）。可传入 frame 避免重复截屏。"""
    cfg = config or load_regions()
    default_tier = int(cfg.get("default_lucky_tier", 0))
    region = cfg.get("dice_usage_region", {})
    if not region.get("width"):
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=default_tier,
            raw_cell_text="",
            notes="请在 config/regions.yaml 配置 dice_usage_region",
        )
    timing = _new_timing_dict()
    t_all = time.perf_counter()
    frame = _acquire_recognition_frame(hwnd, frame, timing)
    if frame is None:
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=default_tier,
            raw_cell_text="",
            notes="无法截取游戏窗口",
        )
    dice_used, dice_remaining, raw_dice = _recognize_dice_usage(frame, cfg, timing=timing)
    notes = _dice_failure_note(
        frame, cfg, raw=raw_dice, dice_remaining=dice_remaining
    )
    if save_debug:
        patch = crop_region(frame, region)
        ocr_input = None
        if patch is not None and patch.size > 0:
            variants = _dice_ocr_variants(patch)
            ocr_input = variants[0] if variants else None
        saved = save_dice_ocr_debug(frame, region, ocr_input)
        if saved:
            names = ", ".join(p.name for p in saved.values())
            debug_line = f"已保存骰子调试图: {names}"
            notes = f"{notes} {debug_line}".strip() if notes else debug_line
    if timing is not None:
        timing["total"] = (time.perf_counter() - t_all) * 1000.0
    return RecognitionResult(
        cell_id=None,
        lucky_card_count=0,
        lucky_card_tier=default_tier,
        raw_cell_text="",
        dice_used=dice_used,
        dice_remaining=dice_remaining,
        raw_dice_text=raw_dice,
        notes=notes,
        timing_ms=timing or {},
    )


def recognize_advice_poll(
    config: dict[str, Any] | None = None,
    *,
    hwnd: int | None = None,
) -> RecognitionResult:
    """规划建议轻量轮询：仅 OCR 格子。手牌/剩骰在格子变化后由完整 recognize_game 识别。"""
    cfg = config or load_regions()
    default_tier = int(cfg.get("default_lucky_tier", 0))
    if hwnd:
        frame = capture_window_client(hwnd)
    else:
        frame = capture_screen()
    if frame is None:
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=default_tier,
            raw_cell_text="",
            notes="无法截取游戏窗口",
        )

    cell_id, raw_cell, cell_notes = _recognize_cell_on_frame(frame, cfg)

    return RecognitionResult(
        cell_id=cell_id,
        lucky_card_count=0,
        lucky_card_tier=default_tier,
        raw_cell_text=raw_cell,
        raw_lucky_text="",
        lucky_card_ids=[],
        notes=cell_notes,
    )


def recognize_lucky_slot_only(
    config: dict[str, Any] | None = None,
    *,
    hwnd: int | None = None,
    frame: np.ndarray | None = None,
    save_debug: bool = True,
    debug_snapshot: bool = False,
) -> RecognitionResult:
    """仅截屏 + 幸运卡槽模板匹配（用于 GUI 测试）。可传入 frame 避免重复截屏。"""
    cfg = config or load_regions()
    default_tier = int(cfg.get("default_lucky_tier", 0))
    lucky_region = cfg.get("lucky_cards_region", {})
    if not lucky_region.get("width"):
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=default_tier,
            raw_cell_text="",
            notes="请在 config/regions.yaml 配置 lucky_cards_region",
        )
    timing = _new_timing_dict()
    t_all = time.perf_counter()
    frame = _acquire_recognition_frame(hwnd, frame, timing)
    if frame is None:
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=default_tier,
            raw_cell_text="",
            notes="无法截取游戏窗口",
        )
    lucky_patch = crop_region(frame, lucky_region)
    th = cfg.get("lucky_match_threshold")
    t_lucky = time.perf_counter()
    lucky_result = match_lucky_slot(
        lucky_patch,
        threshold=float(th) if th is not None else None,
        config=cfg,
    )
    if timing is not None:
        timing["lucky_match"] = (time.perf_counter() - t_lucky) * 1000.0
    if save_debug:
        save_lucky_slot_debug(
            lucky_patch,
            lucky_result.matches,
            summary=lucky_result.summary,
            snapshot=debug_snapshot,
            config=cfg,
        )
    if timing is not None:
        timing["total"] = (time.perf_counter() - t_all) * 1000.0
    return RecognitionResult(
        cell_id=None,
        lucky_card_count=lucky_result.count,
        lucky_card_tier=lucky_result.tier or default_tier,
        raw_cell_text="",
        raw_lucky_text=lucky_result.summary,
        lucky_card_ids=lucky_result.card_ids,
        notes="",
        timing_ms=timing or {},
    )


def recognize_game(
    config: dict[str, Any] | None = None,
    *,
    hwnd: int | None = None,
    frame: np.ndarray | None = None,
    save_debug: bool | None = None,
    debug_snapshot: bool = False,
) -> RecognitionResult:
    cfg = config or load_regions()
    timing = _new_timing_dict()
    t_all = time.perf_counter()
    frame = _acquire_recognition_frame(hwnd, frame, timing)
    if frame is None:
        return RecognitionResult(
            cell_id=None,
            lucky_card_count=0,
            lucky_card_tier=int(cfg.get("default_lucky_tier", 0)),
            raw_cell_text="",
            notes="无法截取游戏窗口，请重新绑定窗口",
        )
    rec = recognize_frame(
        frame, cfg, save_debug=save_debug, debug_snapshot=debug_snapshot
    )
    if timing is not None:
        merged = {**timing, **rec.timing_ms}
        merged["total"] = (time.perf_counter() - t_all) * 1000.0
        rec.timing_ms = merged
    return rec


def recognize_all_on_frame(
    frame: np.ndarray,
    config: dict[str, Any] | None = None,
    *,
    save_debug: bool = False,
    debug_snapshot: bool = False,
) -> tuple[RecognitionResult, RecognitionResult, RecognitionResult]:
    """单次截图上识别骰子、格子、幸运卡（供一键测试等）。"""
    cfg = config or load_regions()
    dice_rec = recognize_dice_only(cfg, frame=frame, save_debug=save_debug)
    cell_rec = recognize_frame(
        frame,
        cfg,
        save_debug=save_debug,
        debug_snapshot=debug_snapshot,
    )
    lucky_rec = recognize_lucky_slot_only(
        cfg,
        frame=frame,
        save_debug=save_debug,
        debug_snapshot=debug_snapshot,
    )
    return dice_rec, cell_rec, lucky_rec
