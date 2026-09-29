from __future__ import annotations

from zephie_rolling_on.paths import project_root

import argparse
import time
from pathlib import Path

from zephie_rolling_on.app.tick import run_tick
from zephie_rolling_on.data.map_loader import load_map_from_xlsx
from zephie_rolling_on.vision.recognize import load_regions

PROJECT_ROOT = project_root()
DEFAULT_MAP = PROJECT_ROOT / "map.xlsx"


def run_once(map_path: Path, dry_cell_id: int | None) -> None:
    board = load_map_from_xlsx(map_path)
    print(f"已加载地图: {board.size} 格")
    result = run_tick(board, load_regions(), dry_cell_id=dry_cell_id)
    for line in result.log_lines:
        print(line)


def run_loop(map_path: Path, interval: float, dry_cell_id: int | None) -> None:
    board = load_map_from_xlsx(map_path)
    regions = load_regions()
    interval = float(regions.get("poll_interval_seconds", interval))
    print(f"已加载地图: {board.size} 格，每 {interval}s 识别一次（Ctrl+C 退出）")

    while True:
        try:
            result = run_tick(board, regions, dry_cell_id=dry_cell_id)
            for line in result.log_lines:
                print(line)
        except KeyboardInterrupt:
            print("\n已退出。")
            break
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Zephie Rolling On — recognition + advice")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP, help="map.xlsx 路径")
    parser.add_argument("--loop", action="store_true", help="循环识别（默认只跑一次）")
    parser.add_argument("--interval", type=float, default=2.0, help="循环间隔秒")
    parser.add_argument(
        "--dry-cell-id",
        type=int,
        default=None,
        help="不截屏，用指定格子 id 测试建议逻辑",
    )
    args = parser.parse_args()

    if not args.map.is_file():
        raise SystemExit(f"找不到地图文件: {args.map}")

    if args.loop:
        run_loop(args.map, args.interval, args.dry_cell_id)
    else:
        run_once(args.map, args.dry_cell_id)


if __name__ == "__main__":
    main()
