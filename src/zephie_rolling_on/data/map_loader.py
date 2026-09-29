from __future__ import annotations

import json
from pathlib import Path

from zephie_rolling_on.models.cell import Cell
from zephie_rolling_on.models.map_board import MapBoard

# Column indices in each row tuple (0-based), matching A=0 … W=22
IDX_ID = 0  # A
IDX_DESC = 1  # B
IDX_FINAL = 3  # D
IDX_JUMP_END = 4  # E
IDX_TYPE = 7  # H
IDX_DICE_STOP = 8  # I
IDX_LUCKY_START = 9  # J
IDX_LUCKY_END = 15  # P
IDX_SHORTCUT_START = 16  # Q
IDX_SHORTCUT_END = 22  # W

DATA_START_ROW = 2

# Parse xlsx once, then reuse a pure-JSON cache.
_CACHE_VERSION = 1


def _to_float(value) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text or text == "/":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _prob_tuple_from_row(row: tuple, start: int, end: int) -> tuple[float, float, float, float, float, float, float]:
    vals = tuple(_to_float(row[i]) for i in range(start, end + 1))
    if len(vals) != 7:
        msg = f"Expected 7 probability values, got {len(vals)}"
        raise ValueError(msg)
    return vals


def _parse_row_tuple(row: tuple, row_num: int) -> Cell | None:
    if len(row) <= IDX_SHORTCUT_END:
        return None

    raw_id = row[IDX_ID]
    if raw_id is None or raw_id == "":
        return None

    cell_id = int(raw_id)
    desc = row[IDX_DESC]
    description = str(desc).strip() if desc is not None else ""

    final_position = int(row[IDX_FINAL])
    jump_end = _to_int(row[IDX_JUMP_END])

    raw_type = row[IDX_TYPE]
    cell_type = str(raw_type).strip() if raw_type is not None else ""

    dice_stop = int(row[IDX_DICE_STOP] or 0)

    lucky = _prob_tuple_from_row(row, IDX_LUCKY_START, IDX_LUCKY_END)
    shortcut = _prob_tuple_from_row(row, IDX_SHORTCUT_START, IDX_SHORTCUT_END)

    return Cell(
        cell_id=cell_id,
        description=description,
        final_position=final_position,
        jump_end=jump_end,
        cell_type=cell_type,
        normal_dice_stop=dice_stop,
        lucky_prob=lucky,
        shortcut_prob=shortcut,
    )


def _cells_from_xlsx(path: Path) -> dict[int, Cell]:
    """用 openpyxl 解析 xlsx（仅首次 / 缓存失效时调用）。"""
    import openpyxl  # 延迟导入：避免无谓拉入 numpy/PIL（PyPy 走缓存时根本不需要）

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        cells: dict[int, Cell] = {}
        for row_num, row in enumerate(
            ws.iter_rows(min_row=DATA_START_ROW, values_only=True),
            start=DATA_START_ROW,
        ):
            cell = _parse_row_tuple(row, row_num)
            if cell is None:
                continue
            if cell.cell_id in cells:
                msg = f"Duplicate cell_id {cell.cell_id} at row {row_num}"
                raise ValueError(msg)
            cells[cell.cell_id] = cell
    finally:
        wb.close()

    if not cells:
        msg = f"No cells loaded from {path}"
        raise ValueError(msg)
    return cells


def _cache_path_for(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".cache.json")


def _cell_to_record(c: Cell) -> list:
    return [
        c.cell_id,
        c.description,
        c.final_position,
        c.jump_end,
        c.cell_type,
        c.normal_dice_stop,
        list(c.lucky_prob),
        list(c.shortcut_prob),
    ]


def _cell_from_record(r: list) -> Cell:
    return Cell(
        cell_id=r[0],
        description=r[1],
        final_position=r[2],
        jump_end=r[3],
        cell_type=r[4],
        normal_dice_stop=r[5],
        lucky_prob=tuple(r[6]),
        shortcut_prob=tuple(r[7]),
    )


def _write_cache(path: Path, cells: dict[int, Cell]) -> None:
    data = {
        "version": _CACHE_VERSION,
        "cells": [_cell_to_record(c) for c in cells.values()],
    }
    _cache_path_for(path).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _cells_from_cache(path: Path) -> dict[int, Cell] | None:
    """读 JSON 缓存；缺失 / 版本不符 / 比 xlsx 旧（已过期）→ 返回 None。"""
    cache = _cache_path_for(path)
    if not cache.exists():
        return None
    try:
        # 若源 xlsx 存在且比缓存新，视为过期（PyPy 下 xlsx 通常不存在或不更新）。
        if path.exists() and path.stat().st_mtime > cache.stat().st_mtime:
            return None
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            return None
        cells: dict[int, Cell] = {}
        for r in data["cells"]:
            c = _cell_from_record(r)
            cells[c.cell_id] = c
        return cells or None
    except Exception:
        return None


def load_map_from_xlsx(path: Path | str) -> MapBoard:
    """加载地图（缓存优先）。

    优先读取同名 ``<map>.cache.json``；缺失或过期时用 openpyxl 解析 xlsx 并写缓存。
    这样 PyPy / 无 openpyxl 环境只要有缓存即可运行，无 numpy/PIL 依赖。
    """
    path = Path(path)
    cells = _cells_from_cache(path)
    if cells is None:
        cells = _cells_from_xlsx(path)
        try:
            _write_cache(path, cells)
        except Exception:
            pass  # 缓存写失败不影响主流程
    return MapBoard(cells)
