from __future__ import annotations

from zephie_rolling_on.models.cell import Cell


class MapBoard:
    def __init__(self, cells: dict[int, Cell]) -> None:
        self._by_id = cells
        self._by_final: dict[int, Cell] = {}
        for c in cells.values():
            self._by_final[c.final_position] = c
        self._max_cell_id = max(self._by_id) if self._by_id else 1
        # 热路径：cell_id 是连续正整数，用 list 按下标取代 dict.get（省哈希）。
        # 下标 0 占位为 None；缺口格（若有）也保持 None，与 dict.get 行为一致。
        arr: list[Cell | None] = [None] * (self._max_cell_id + 1)
        for cid, c in cells.items():
            if 0 <= cid <= self._max_cell_id:
                arr[cid] = c
        self._arr = arr
        # fp 链终局缓存（地图静态量），作为实例属性避免热路径 getattr 兜底。
        self._end_cache: dict[int, tuple[int, str]] = {}

    @property
    def size(self) -> int:
        return len(self._by_id)

    @property
    def max_cell_id(self) -> int:
        """最大 cell_id（构造时缓存，供 clamp 热路径使用）。"""
        return self._max_cell_id

    def get(self, cell_id: int) -> Cell | None:
        # 数组按下标取（比 dict.get 快）；越界返回 None，等价于原 dict 行为。
        if cell_id < 0 or cell_id > self._max_cell_id:
            return None
        return self._arr[cell_id]

    def require(self, cell_id: int) -> Cell:
        cell = self.get(cell_id)
        if cell is None:
            msg = f"Unknown cell_id: {cell_id}"
            raise KeyError(msg)
        return cell

    def by_final_position(self, final_position: int) -> Cell | None:
        return self._by_final.get(final_position)

    def all_cells(self) -> list[Cell]:
        return list(self._by_id.values())
