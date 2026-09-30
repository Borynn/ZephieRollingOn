"""Cell-id OCR via bright-glyph connected components."""
from __future__ import annotations

from zephie_rolling_on.paths import project_root
from zephie_rolling_on.vision.image_io import imread_unicode

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from zephie_rolling_on.vision.ocr_runtime import get_ocr_reader, ocr_readtext

_ASSETS = project_root() / "assets" / "cell_ocr"
_TAIL_COLS = 15


def _cell_ocr_params(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "upscale": int(config.get("cell_ocr_upscale", 10)),
        "bright_thresh": int(config.get("cell_ocr_bright_thresh", 120)),
        "glyph_pad": int(config.get("cell_ocr_glyph_pad", 4)),
        "min_glyph_area": int(config.get("cell_ocr_min_glyph_area", 30)),
        "min_glyph_height": int(config.get("cell_ocr_min_glyph_height", 5)),
        "morph_close_iter": int(config.get("cell_ocr_morph_close_iter", 2)),
        "fallback_min_width": int(config.get("cell_ocr_fallback_min_width", 320)),
        "fallback_min_height": int(config.get("cell_ocr_fallback_min_height", 80)),
        "max_glyphs": int(config.get("cell_ocr_max_glyphs", 8)),
        "low_glyph_conf": float(config.get("cell_ocr_low_glyph_conf", 0.75)),
        "lock_glyph_conf": float(config.get("cell_ocr_lock_glyph_conf", 0.90)),
        "fallback_min_conf": float(config.get("cell_ocr_fallback_min_conf", 0.80)),
        # 10× 连通域 crop 亮像素：真 3 中位 ~1931，真 7 ~1383，歧义 blob ~1393
        "area_seven_max": int(config.get("cell_ocr_area_seven_max", 1500)),
        "area_three_min": int(config.get("cell_ocr_area_three_min", 1750)),
    }


def _upscale_patch(patch: np.ndarray, *, scale: int) -> np.ndarray:
    import cv2

    if patch is None or patch.size == 0:
        return patch
    h, w = patch.shape[:2]
    factor = max(1, int(scale))
    if factor <= 1:
        return patch
    return cv2.resize(
        patch,
        (w * factor, h * factor),
        interpolation=cv2.INTER_CUBIC,
    )


def _glyph_ocr_variants(patch: np.ndarray) -> list[np.ndarray]:
    """单字小块：原图 + OTSU 正反二值化（与骰子 ROI 相同套路）。"""
    import cv2

    if patch is None or patch.size == 0:
        return []
    out: list[np.ndarray] = [patch]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if len(patch.shape) == 3 else patch
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


def _collect_single_digit_reads(patch: np.ndarray) -> list[tuple[str, float]]:
    """单字块多尺度 + 多预处理 OCR，收集 1 位数字读数。"""
    import cv2

    reads: list[tuple[str, float]] = []
    sources = [patch]
    h, w = patch.shape[:2]
    if max(h, w) < 120:
        sources.append(
            cv2.resize(patch, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC),
        )
    for src in sources:
        for variant in _glyph_ocr_variants(src):
            for use_allow in (True, False):
                results = ocr_readtext(
                    variant,
                    allowlist="0123456789" if use_allow else None,
                )
                for _bbox, text, conf in results:
                    digits = "".join(ch for ch in text if ch.isdigit())
                    if len(digits) != 1:
                        continue
                    reads.append((digits, float(conf)))
    return reads


def _vote_single_glyph_reads(
    reads: list[tuple[str, float]],
) -> tuple[str, float, bool, list[str]]:
    """同字多路结果投票：出现次数优先，其次最高置信度。"""
    if not reads:
        return "", 0.0, False, []
    buckets: dict[str, list[float]] = {}
    for digit, conf in reads:
        buckets.setdefault(digit, []).append(conf)
    alternates = sorted(buckets.keys())
    ambiguous = len(alternates) >= 2
    best_digit = max(
        alternates,
        key=lambda digit: (len(buckets[digit]), max(buckets[digit])),
    )
    return best_digit, max(buckets[best_digit]), ambiguous, alternates


def _ocr_single_glyph(patch: np.ndarray) -> tuple[str, float, bool, list[str]]:
    """对单个连通域裁剪块 OCR，只接受 1 位数字。"""
    reads = _collect_single_digit_reads(patch)
    return _vote_single_glyph_reads(reads)


def _tail_roi(patch: np.ndarray) -> np.ndarray:
    if patch is None or patch.size == 0:
        return patch
    w = patch.shape[1]
    cols = min(_TAIL_COLS, w)
    return patch[:, w - cols :]


@lru_cache(maxsize=2)
def _load_tail_reference(name: str) -> np.ndarray | None:
    import cv2

    path = _ASSETS / f"tail_ref_{name}.png"
    if not path.is_file():
        return None
    return imread_unicode(path)


def glyph_bright_pixel_area(
    crop: np.ndarray,
    config: dict[str, Any] | None = None,
) -> int:
    """放大后单字连通域内的亮像素数量（与分割阈值一致）。"""
    if crop is None or crop.size == 0:
        return 0
    import cv2

    thresh = int(_cell_ocr_params(config or {})["bright_thresh"])
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return int((gray >= thresh).sum())


def area_digit_hint(area: int, params: dict[str, Any]) -> str | None:
    """
    按亮像素量粗分 3 形 / 7 形（基于 debug 统计中位数 1931 vs 1383）。

    返回 None 表示落在重叠区，不单凭面积决断。
    """
    if area <= int(params["area_seven_max"]):
        return "7"
    if area >= int(params["area_three_min"]):
        return "3"
    return None


def _alternates_are_three_seven(alternates: list[str]) -> bool:
    return "3" in alternates and "7" in alternates


def classify_tail_cluster(patch: np.ndarray) -> str:
    """
    末位 ROI 像素簇：「3 形」与「7 形」在低分辨率下连通域几乎相同，
    需用原始 ROI 右侧像素与标定模板比对（见 assets/cell_ocr/tail_ref_*.png）。
    """
    tail = _tail_roi(patch)
    if tail is None or tail.size == 0:
        return "3"
    ref3 = _load_tail_reference("3")
    ref7 = _load_tail_reference("7")
    if ref3 is None or ref7 is None:
        return "3"
    cur = tail.astype(np.float32)
    d3 = float(np.abs(cur - ref3.astype(np.float32)).mean())
    d7 = float(np.abs(cur - ref7.astype(np.float32)).mean())
    return "3" if d3 <= d7 else "7"


def _keep_glyph_for_103_exception(
    chars: list[str],
    patch: np.ndarray,
    idx: int,
) -> bool:
    """仅 10x 三位数（典型 103）：整幅常误 107，末位保留连通域的 3。"""
    if idx != len(chars) - 1:
        return False
    if len(chars) != 3:
        return False
    if chars[0] != "1" or chars[1] != "0":
        return False
    if classify_tail_cluster(patch) != "3":
        return False
    return True


def _apply_area_three_seven_hint(
    chars: list[str],
    idx: int,
    crop: np.ndarray,
    patch: np.ndarray,
    alternates: list[str],
    params: dict[str, Any],
    notes: list[str],
) -> bool:
    """3/7 歧义时：亮像素更接近 7 形则偏向 7（103 前缀例外在外层拦截）。"""
    if not _alternates_are_three_seven(alternates):
        return False
    if _keep_glyph_for_103_exception(chars, patch, idx):
        return False
    area = glyph_bright_pixel_area(crop, params)
    hint = area_digit_hint(area, params)
    if hint is None or hint == chars[idx]:
        return False
    notes.append(f"[位{idx}] 亮像素{area}→{hint}形")
    chars[idx] = hint
    return True


def _merge_locked_prefix_fallback(
    glyph_raw: str,
    confs: list[float],
    ambiguous: list[bool],
    alternates_list: list[list[str]],
    glyph_crops: list[np.ndarray],
    patch: np.ndarray,
    buckets: dict[str, list[float]],
    params: dict[str, Any],
) -> tuple[str, float, list[str]]:
    """
    按位合并：高置信位锁定前缀，仅对低置信歧义位查阅「同前缀」备用读数。

    不采用整幅票数最高串（如 237 会压过 287），而在已锁定前缀下逐位对齐。
    """
    notes: list[str] = []
    if not glyph_raw or not buckets:
        return glyph_raw, _mean_conf(confs), notes

    lock_conf = float(params["lock_glyph_conf"])
    fb_min = float(params["fallback_min_conf"])
    chars = list(glyph_raw)
    n = len(chars)

    for i in range(n):
        if i >= len(ambiguous) or i >= len(confs):
            break
        if not ambiguous[i] or confs[i] >= lock_conf:
            continue

        prefix = "".join(chars[:i])
        matching = [
            text for text in buckets if len(text) == n and text.startswith(prefix)
        ]
        alts = alternates_list[i] if i < len(alternates_list) else []
        crop = glyph_crops[i] if i < len(glyph_crops) else None

        if _keep_glyph_for_103_exception(chars, patch, i):
            notes.append(f"[位{i}] 10x三位+尾簇3 保留 '{chars[i]}'")
            continue

        if not matching:
            if crop is not None and _apply_area_three_seven_hint(
                chars,
                i,
                crop,
                patch,
                alts,
                params,
                notes,
            ):
                continue
            notes.append(f"[位{i}] 无备用'{prefix}*'，保留 '{chars[i]}'")
            continue

        digits_at_i = [text[i] for text in matching]
        uniq = set(digits_at_i)
        if len(uniq) == 1:
            new_digit = digits_at_i[0]
            if new_digit != chars[i]:
                notes.append(f"[位{i}] 前缀{prefix!r} 备用一致→'{new_digit}'")
                chars[i] = new_digit
            continue

        if i == n - 1 and uniq <= {"3", "7"}:
            cluster = classify_tail_cluster(patch)
            sevens = [text for text in matching if text[i] == "7"]
            applied = False
            if sevens:
                best = max(sevens, key=lambda text: len(buckets[text]))
                if len(buckets[best]) >= 2 and max(buckets[best]) >= fb_min:
                    if cluster == "7" or not _keep_glyph_for_103_exception(
                        chars,
                        patch,
                        i,
                    ):
                        chars[i] = "7"
                        notes.append(f"[位{i}] 尾簇{cluster} 备用→'7'")
                        applied = True
            if (
                not applied
                and crop is not None
                and _apply_area_three_seven_hint(
                    chars,
                    i,
                    crop,
                    patch,
                    alts,
                    params,
                    notes,
                )
            ):
                continue

    out = "".join(chars)
    if out == glyph_raw:
        return glyph_raw, _mean_conf(confs), notes
    merged_conf = max(_mean_conf(confs), max(max(v) for v in buckets.values()))
    return out, merged_conf, notes


def _mean_conf(confs: list[float]) -> float:
    if not confs:
        return 0.0
    return sum(confs) / len(confs)


def _split_glyph_crops(
    patch: np.ndarray,
    config: dict[str, Any],
) -> list[tuple[int, np.ndarray]]:
    """按亮字连通域从左到右切分单字块。"""
    import cv2

    if patch is None or patch.size == 0:
        return []
    params = _cell_ocr_params(config)
    scaled = _upscale_patch(patch, scale=params["upscale"])
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    mask = (gray >= params["bright_thresh"]).astype(np.uint8) * 255
    if params["morph_close_iter"] > 0:
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=params["morph_close_iter"],
        )
    _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
    pad = params["glyph_pad"]
    h, w = scaled.shape[:2]
    glyphs: list[tuple[int, np.ndarray]] = []
    for idx in range(1, _count):
        x, y, gw, gh, area = stats[idx]
        if area < params["min_glyph_area"] or gh < params["min_glyph_height"]:
            continue
        x0 = max(0, int(x) - pad)
        y0 = max(0, int(y) - pad)
        x1 = min(w, int(x + gw) + pad)
        y1 = min(h, int(y + gh) + pad)
        crop = scaled[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        glyphs.append((int(x), crop))
    glyphs.sort(key=lambda item: item[0])
    return glyphs


def _prepare_fallback_patch(patch: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    import cv2

    params = _cell_ocr_params(config)
    min_w = params["fallback_min_width"]
    min_h = params["fallback_min_height"]
    h, w = patch.shape[:2]
    scale = max(3, int(min_w / max(w, 1)), int(min_h / max(h, 1)))
    if scale > 1:
        return cv2.resize(
            patch,
            (w * scale, h * scale),
            interpolation=cv2.INTER_CUBIC,
        )
    return patch


def _ocr_fallback_whole_patch(
    patch: np.ndarray,
    config: dict[str, Any],
) -> tuple[str, float, list[str], int, dict[str, list[float]]]:
    """连通域分割失败时：整幅放大 + 按检测框从左拼数字（备用）。"""
    detail: list[str] = []
    base = _prepare_fallback_patch(patch, config)
    if base is None or base.size == 0:
        return "", 0.0, detail, 0, {}

    buckets: dict[str, list[float]] = {}
    for variant in _glyph_ocr_variants(base):
        for use_allow in (True, False):
            parts: list[tuple[int, str, float]] = []
            for bbox, text, conf in ocr_readtext(
                variant,
                allowlist="0123456789" if use_allow else None,
            ):
                digits = "".join(ch for ch in text if ch.isdigit())
                if not digits:
                    continue
                xs = [int(p[0]) for p in bbox]
                parts.append((min(xs), digits, float(conf)))
            if not parts:
                continue
            parts.sort(key=lambda item: item[0])
            merged = "".join(p[1] for p in parts)
            conf = min(p[2] for p in parts)
            detail.append(f"[备用] {merged!r} conf={conf:.2f}")
            buckets.setdefault(merged, []).append(conf)
    if not buckets:
        return "", 0.0, detail, 0, {}
    best_text = max(
        buckets.keys(),
        key=lambda text: (len(buckets[text]), max(buckets[text]), len(text)),
    )
    best_conf = max(buckets[best_text])
    return best_text, best_conf, detail, len(buckets[best_text]), buckets


def read_cell_digits(
    patch: np.ndarray,
    config: dict[str, Any],
) -> tuple[str, float, list[str], list[dict[str, Any]]]:
    """
    识别格子 ROI 内纯数字串。

    返回 (digit_raw, mean_conf, detail_lines, glyph_records)。
    """
    if get_ocr_reader() is None:
        return "", 0.0, ["[格子 OCR] 引擎未就绪"], []

    if patch is None or patch.size == 0:
        return "", 0.0, ["[格子 OCR] ROI 为空"], []

    detail: list[str] = []
    glyphs = _split_glyph_crops(patch, config)
    max_glyphs = int(_cell_ocr_params(config)["max_glyphs"])
    if len(glyphs) > max_glyphs:
        detail.append(
            f"[格子 OCR] 连通域 {len(glyphs)} 块 > {max_glyphs}，疑似灰屏噪声，跳过",
        )
        return "", 0.0, detail, []

    glyph_records: list[dict[str, Any]] = []
    chars: list[str] = []
    confs: list[float] = []
    ambiguous_flags: list[bool] = []
    alternates_list: list[list[str]] = []
    glyph_crops: list[np.ndarray] = []

    for idx, (_x, crop) in enumerate(glyphs):
        digit, conf, ambiguous, alternates = _ocr_single_glyph(crop)
        bright_area = glyph_bright_pixel_area(crop, config)
        glyph_crops.append(crop)
        alternates_list.append(alternates)
        glyph_records.append(
            {
                "index": idx,
                "digit": digit,
                "conf": round(conf, 4),
                "ambiguous": ambiguous,
                "alternates": alternates,
                "bright_area": bright_area,
                "area_hint": area_digit_hint(
                    bright_area,
                    _cell_ocr_params(config),
                ),
            },
        )
        detail.append(
            f"[字{idx}] {digit!r} conf={conf:.2f}"
            + (f" 歧义{alternates}" if ambiguous else ""),
        )
        if digit:
            chars.append(digit)
            confs.append(conf)
            ambiguous_flags.append(ambiguous)

    if chars:
        glyph_raw = "".join(chars)
        mean_conf = _mean_conf(confs)
        min_conf = min(confs)
        detail.insert(0, f"[连通域] {len(glyphs)} 块 → {glyph_raw!r}")
        params = _cell_ocr_params(config)

        if len(glyph_raw) <= 4 and min_conf < params["low_glyph_conf"]:
            _fb_best, _fb_conf, fb_detail, _fb_n, fb_buckets = (
                _ocr_fallback_whole_patch(patch, config)
            )
            detail.extend(fb_detail)
            resolved, resolved_conf, resolve_notes = _merge_locked_prefix_fallback(
                glyph_raw,
                confs,
                ambiguous_flags,
                alternates_list,
                glyph_crops,
                patch,
                fb_buckets,
                params,
            )
            for note in resolve_notes:
                detail.insert(0, note)
            if resolved != glyph_raw:
                return resolved, resolved_conf, detail, glyph_records
        return glyph_raw, mean_conf, detail, glyph_records

    fallback, fb_conf, fb_detail, _fb_n, _fb_buckets = _ocr_fallback_whole_patch(
        patch,
        config,
    )
    detail.extend(fb_detail)
    if fallback:
        detail.insert(0, f"[备用整幅] → {fallback!r}")
        return fallback, fb_conf, detail, glyph_records

    detail.insert(0, "[格子 OCR] 未检出亮字连通域")
    return "", 0.0, detail, glyph_records
