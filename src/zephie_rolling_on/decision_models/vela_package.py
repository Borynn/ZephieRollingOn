"""Open-source model packages (``*.vpk``) — loaded in-process.

Package formats are selected by file extension:

    zephie_*.zm  interpreted by model_host.exe (separate process)
    *.vpk        loaded natively in this process

Both implement ``DecisionModelPackage``, so callers do not care which they hold.
Public models need no indirection, so they are read directly.

Container layout:

    u32 header_len | UTF-8 JSON header | busy.png | done.png | model bytes
"""

from __future__ import annotations

import json
import struct
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from zephie_rolling_on.decision_models.types import (
    CARD_TO_NATIVE,
    MAX_HAND,
    NATIVE_CARD_IDS,
    DecisionModelDisplayInfo,
    DecisionModelImportResult,
    DecisionResult,
)
from zephie_rolling_on.paths import project_root

PKG_SUFFIX = ".vpk"

# Fixed bundle size declared by the upstream VELA v4 model.
VELA_MODEL_BYTES = 1096193164

# Unique instance ids 1..30 from the upstream card data, grouped by card id.
# Public game data; kept local so this module depends on nothing private.
TYPE_TO_UNIQUE_IDS: dict[str, tuple[int, ...]] = {
    "forward_1": (1,), "forward_2": (2,), "forward_3": (3,), "forward_4": (4, 5),
    "forward_5": (6, 7), "forward_6": (8, 9), "forward_7": (10, 11),
    "forward_8": (12, 13), "forward_9": (14, 15), "forward_10": (16, 17),
    "forward_11": (18,), "forward_12": (19,), "back_1": (20,), "back_2": (21,),
    "back_3": (22,), "multiply_2": (23,), "multiply_3": (24,), "multiply_5": (25,),
    "multiply_7": (26,), "multiply_8": (27,), "multiply_10": (28,), "next_1": (29, 30),
}
_CARD_CAP = {cid: len(u) for cid, u in TYPE_TO_UNIQUE_IDS.items()}
_FULL_DECK_MASK = (1 << 30) - 1

# Action index vocabulary: 1 = roll, 2..23 = card, matching DecisionResult.
_CARD_VOCAB: tuple[str, ...] = (
    "forward_1", "forward_2", "forward_3", "forward_11", "forward_12", "back_1",
    "back_2", "back_3", "multiply_2", "multiply_3", "multiply_5", "multiply_7",
    "multiply_8", "multiply_10", "forward_4", "forward_5", "forward_6", "forward_7",
    "forward_8", "forward_9", "forward_10", "next_1",
)
_VOCAB_INDEX = {c: i for i, c in enumerate(_CARD_VOCAB)}


class VelaPackageError(RuntimeError):
    """Container malformed, or the native engine is unavailable."""


def _engine_state(module: Any) -> str:
    """原生引擎自报状态，用于把加载失败的原因说清楚。"""
    parts: list[str] = []
    for name in ("ready", "device_ready"):
        fn = getattr(module, name, None)
        if callable(fn):
            try:
                parts.append(f"{name}={bool(fn())}")
            except Exception:  # noqa: BLE001
                pass
    fn = getattr(module, "hip_ready", None)
    if callable(fn):
        try:
            hip = bool(fn())
            parts.append(f"hip_ready={hip}")
            if not hip:
                err = getattr(module, "hip_last_error", None)
                if callable(err):
                    try:
                        msg = str(err()).strip()
                        if msg:
                            parts.append(f"hip_last_error={msg}")
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
    return ("（引擎状态：" + "，".join(parts) + "）") if parts else ""


def _find_vela_module() -> Any | None:
    """Import the native engine, searching the shipped locations."""
    try:
        import vela_official  # type: ignore

        return vela_official
    except ImportError:
        pass

    root = project_root()
    candidates = [
        root / "native" / "vela",  # shipped location
        root,                      # portable: beside the exe
        root / "bin",
    ]
    for d in candidates:
        if not d.is_dir():
            continue
        if any(d.glob("vela_official*.pyd")) or any(d.glob("vela_official*.so")):
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
            try:
                import vela_official  # type: ignore

                return vela_official
            except ImportError:
                continue
    return None


def read_vpk_header(path: Path) -> tuple[dict[str, Any], int]:
    """Parse the JSON header; returns (header, absolute offset of the art)."""
    with Path(path).open("rb") as f:
        raw = f.read(4)
        if len(raw) != 4:
            raise VelaPackageError("truncated container")
        (header_len,) = struct.unpack("<I", raw)
        if not 0 < header_len <= 1 << 20:
            raise VelaPackageError(f"implausible header length {header_len}")
        blob = f.read(header_len)
    if len(blob) != header_len:
        raise VelaPackageError("truncated header")
    try:
        header = json.loads(blob.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise VelaPackageError(f"header is not JSON: {exc}") from exc
    if header.get("format") != "vela-package":
        raise VelaPackageError(f"unexpected format {header.get('format')!r}")
    return header, 4 + header_len


def _read_art(path: Path, offset: int, length: int, which: str) -> bytes | None:
    with Path(path).open("rb") as f:
        f.seek(offset)
        blob = f.read(length)
    return blob if blob[:4] == b"\x89PNG" else None


class VelaPackageModel:
    """A `.vpk` open-source model, driven natively in-process."""

    def __init__(self, package_path: Path) -> None:
        self.package_path = Path(package_path).resolve()
        self.model_id = self.package_path.stem
        self._header, self._after_header = read_vpk_header(self.package_path)
        self._info: DecisionModelDisplayInfo | None = None
        self._imported = False
        self._module: Any | None = None

    # -- interface 1 -----------------------------------------------------
    def display_info(self) -> DecisionModelDisplayInfo:
        if self._info is not None:
            return self._info
        h = self._header
        lines: list[str] = []
        if h.get("infer_time"):
            lines.append(f"推理参考时间 {h['infer_time']}")
        if h.get("performance"):
            lines.append(f"模型表现 {h['performance']}")
        self._info = DecisionModelDisplayInfo(
            model_id=str(h.get("model_id") or self.model_id),
            name=str(h.get("name") or self.model_id),
            version="",
            summary="",
            param_lines=tuple(lines),
            status_line=self.package_path.name,
        )
        return self._info

    # -- interface 2 -----------------------------------------------------
    def import_model(
        self,
        *,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> DecisionModelImportResult:
        def report(pct: int) -> None:
            if on_progress is not None:
                on_progress(int(pct), "")

        report(0)
        module = _find_vela_module()
        if module is None:
            return DecisionModelImportResult(
                ok=False,
                message="缺少原生推理引擎（native/vela），该模型无法导入",
            )
        self._module = module

        h = self._header
        busy_len = int(h.get("busy_png_bytes", 0))
        done_len = int(h.get("done_png_bytes", 0))
        payload_off = self._after_header + busy_len + done_len
        payload_len = int(h.get("payload_bytes", 0))
        if payload_len != VELA_MODEL_BYTES:
            return DecisionModelImportResult(
                ok=False, message=f"载荷长度异常: {payload_len}"
            )

        # 头部只声明了长度，实际文件可能因下载不完整而更短。不先查这一点的话，
        # 会一路走到原生层才以 "short read" 之类的信息失败，难以判断是文件问题
        # 还是内存问题。
        expected = payload_off + payload_len
        try:
            actual = self.package_path.stat().st_size
        except OSError as exc:
            return DecisionModelImportResult(ok=False, message=f"无法读取模型文件：{exc}")
        if actual < expected:
            return DecisionModelImportResult(
                ok=False,
                message="模型文件不完整，请重新下载",
            )

        report(10)
        try:
            # The native side reads only the model region of the container, so
            # the payload is never copied through Python.
            loaded = module.load_model_from_file(
                str(self.package_path), payload_off, payload_len
            )
        except MemoryError:
            self._imported = False
            return DecisionModelImportResult(
                ok=False,
                message=(
                    "内存不足：加载 VELA 需要约 2 GB 可用内存"
                    "（模型 1.02 GB，加载过程会再占一份临时副本）。"
                    "请关闭其他程序后重试。"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._imported = False
            return DecisionModelImportResult(ok=False, message=f"VELA 加载失败: {exc}")

        if not loaded:
            self._imported = False
            return DecisionModelImportResult(
                ok=False,
                message=(
                    "原生引擎拒绝加载该包（load_model_from_file 返回 False）。"
                    f"常见原因：可用内存不足（加载约需 2 GB）或文件损坏。{_engine_state(module)}"
                ),
            )

        report(90)
        if not module.ready():
            return DecisionModelImportResult(
                ok=False,
                message=f"VELA 未就绪。{_engine_state(module)}",
            )
        self._imported = True
        report(100)
        return DecisionModelImportResult(ok=True, message="模型导入完成")

    @property
    def is_imported(self) -> bool:
        return self._imported

    # -- interface 3 -----------------------------------------------------
    def decide(
        self,
        *,
        cell_id: int,
        dice_remaining: int,
        next_roll_free: bool = False,
        hand: Sequence[str] = (),
        drawn: Mapping[str, int] | None = None,
    ) -> DecisionResult:
        if not self._imported or self._module is None:
            raise RuntimeError("model not imported; call import_model first")

        hand_list = [str(c) for c in hand]
        counts = {cid: 0 for cid in NATIVE_CARD_IDS}
        if drawn is None:
            for c in hand_list:
                if c in counts:
                    counts[c] += 1
        else:
            for k, v in drawn.items():
                if k in counts:
                    counts[k] = int(v)

        position, dice_used, bonus, hand_uids, mask = _to_vela_input(
            cell_id=int(cell_id),
            dice_remaining=int(dice_remaining),
            next_roll_free=bool(next_roll_free),
            hand=hand_list,
            drawn=counts,
        )
        try:
            out = self._module.evaluate(position, dice_used, bonus, hand_uids, mask)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"VELA evaluate failed: {exc}") from exc

        best = int(out["best"])
        values = [float(v) for v in out.get("values") or []]
        slot = _pick_legal(hand_list, best, values, cell_id, dice_remaining,
                           next_roll_free)
        return _to_decision_result(hand_list, slot, values)

    # -- UI art (busy / done stickers packed with the model) -------------
    def import_art_png(self, which: str) -> bytes | None:
        kind = which.strip().lower()
        key, off = ("busy_png_bytes", self._after_header) if kind == "busy" else (
            "done_png_bytes",
            self._after_header + int(self._header.get("busy_png_bytes", 0)),
        )
        length = int(self._header.get(key, 0))
        if length <= 0:
            return None
        return _read_art(self.package_path, off, length, kind)


# ---------------------------------------------------------------------------
# Our input  ->  VELA snapshot
# ---------------------------------------------------------------------------
def _to_vela_input(
    *,
    cell_id: int,
    dice_remaining: int,
    next_roll_free: bool,
    hand: Sequence[str],
    drawn: Mapping[str, int],
) -> tuple[int, int, int, list[int], int]:
    """DLL-style state -> (position, diceUsed, bonusRoll, hand[], deckMask)."""
    remaining: dict[str, int] = {}
    for cid in NATIVE_CARD_IDS:
        cap = _CARD_CAP[cid]
        remaining[cid] = max(0, cap - int(drawn.get(cid, 0)))

    take: dict[str, int] = {cid: 0 for cid in NATIVE_CARD_IDS}
    hand_uids: list[int] = []
    for cid in hand:
        uids = TYPE_TO_UNIQUE_IDS.get(cid)
        if uids is None:
            raise ValueError(f"unknown card_id {cid!r}")
        idx = remaining[cid] + take[cid]
        if idx >= len(uids):
            idx = take[cid] % len(uids)  # reshuffle / over-assignment fallback
        hand_uids.append(uids[idx])
        take[cid] += 1

    mask = 0
    for cid in NATIVE_CARD_IDS:
        for i in range(min(remaining[cid], len(TYPE_TO_UNIQUE_IDS[cid]))):
            mask |= 1 << (TYPE_TO_UNIQUE_IDS[cid][i] - 1)
    if mask == 0:
        mask = _FULL_DECK_MASK  # upstream requires deckAvailable >= 1

    dice_used = max(0, min(100, 100 - int(dice_remaining)))
    return int(cell_id), dice_used, 1 if next_roll_free else 0, hand_uids, mask


def _is_legal(
    hand: Sequence[str], index: int, cell_id: int, dice_remaining: int, free: bool
) -> bool:
    can_roll = dice_remaining > 0 or free
    if index == 0:
        return can_roll
    slot = index - 1
    if slot < 0 or slot >= len(hand):
        return False
    cid = hand[slot]
    if cid.startswith("multiply") and not can_roll:
        return False
    if cid == "next_1":
        return _jump_available(cell_id)
    return True


_JUMP_CACHE: dict[int, bool] = {}


def _jump_available(cell_id: int) -> bool:
    """next_1 needs a jump target; read it from our map once."""
    if cell_id in _JUMP_CACHE:
        return _JUMP_CACHE[cell_id]
    ok = False
    try:
        from zephie_rolling_on.data.map_loader import load_map_from_xlsx

        board = _map_board()
        cell = board.get(int(cell_id)) if board is not None else None
        ok = cell is not None and cell.jump_end is not None
    except Exception:  # noqa: BLE001
        ok = True  # be permissive rather than block a legal action
    _JUMP_CACHE[cell_id] = ok
    return ok


_BOARD: Any | None = None


def _map_board() -> Any | None:
    global _BOARD
    if _BOARD is None:
        try:
            from zephie_rolling_on.data.map_loader import load_map_from_xlsx

            _BOARD = load_map_from_xlsx(project_root() / "map.xlsx")
        except Exception:  # noqa: BLE001
            _BOARD = False
    return _BOARD or None


def _pick_legal(
    hand: Sequence[str],
    best: int,
    values: Sequence[float],
    cell_id: int,
    dice_remaining: int,
    free: bool,
) -> int:
    """VELA's action, falling back to the best-scoring legal one."""
    if _is_legal(hand, best, cell_id, dice_remaining, free):
        return best
    n = len(hand) + 1
    order = sorted(range(n), key=lambda i: (-(values[i] if i < len(values) else 0.0), i))
    for i in order:
        if _is_legal(hand, i, cell_id, dice_remaining, free):
            return i
    return 0


def _to_decision_result(
    hand: Sequence[str], index: int, values: Sequence[float]
) -> DecisionResult:
    v = float(values[index]) if index < len(values) else 0.0
    if index <= 0:
        return DecisionResult(action="roll", rl_action=1, native_code=0, v_cells=v)
    slot = index - 1
    if slot >= len(hand):
        return DecisionResult(action="error", rl_action=0, native_code=0, v_cells=v)
    card = hand[slot]
    vocab = _VOCAB_INDEX.get(card)
    rl_action = 2 + vocab if vocab is not None else 0
    return DecisionResult(
        action=card, rl_action=rl_action, native_code=slot + 1, v_cells=v
    )


__all__ = [
    "PKG_SUFFIX",
    "TYPE_TO_UNIQUE_IDS",
    "VELA_MODEL_BYTES",
    "VelaPackageError",
    "VelaPackageModel",
    "read_vpk_header",
]
