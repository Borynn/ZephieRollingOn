from __future__ import annotations

from zephie_rolling_on.paths import project_root

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = project_root()
MANIFEST_PATH = PROJECT_ROOT / "assets" / "lucky_cards" / "manifest.yaml"


class LuckyCardType(str, Enum):
    """幸运卡种类（与 assets/lucky_cards/manifest.yaml 中 type 一致）。"""

    NEXT = "next"
    MULTIPLY = "multiply"
    STEP_FORWARD = "step_forward"
    STEP_BACK = "step_back"


@dataclass(frozen=True)
class LuckyCardDef:
    id: str
    type: LuckyCardType
    value: int
    consumes_dice: bool
    template: str
    description: str = ""

    @property
    def template_path(self) -> Path:
        return PROJECT_ROOT / "assets" / "lucky_cards" / self.template


def _manifest_cache_key(path: Path | None) -> str:
    return str(path or MANIFEST_PATH)


@lru_cache(maxsize=4)
def _load_lucky_card_manifest_cached(path_str: str) -> dict[str, Any]:
    with Path(path_str).open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_lucky_card_manifest(path: Path | None = None) -> dict[str, Any]:
    return _load_lucky_card_manifest_cached(_manifest_cache_key(path))


@lru_cache(maxsize=4)
def _load_lucky_card_defs_cached(path_str: str) -> tuple[LuckyCardDef, ...]:
    data = _load_lucky_card_manifest_cached(path_str)
    out: list[LuckyCardDef] = []
    for raw in data.get("cards", []):
        out.append(
            LuckyCardDef(
                id=str(raw["id"]),
                type=LuckyCardType(str(raw["type"])),
                value=int(raw["value"]),
                consumes_dice=bool(raw["consumes_dice"]),
                template=str(raw["template"]),
                description=str(raw.get("description", "")),
            )
        )
    return tuple(out)


def load_lucky_card_defs(path: Path | None = None) -> list[LuckyCardDef]:
    return list(_load_lucky_card_defs_cached(_manifest_cache_key(path)))


@lru_cache(maxsize=4)
def _lucky_card_by_id_cached(path_str: str) -> dict[str, LuckyCardDef]:
    return {d.id: d for d in _load_lucky_card_defs_cached(path_str)}


def lucky_card_by_id(path: Path | None = None) -> dict[str, LuckyCardDef]:
    """card_id → 定义（带缓存；规划评分热路径勿重复读 yaml）。"""
    return _lucky_card_by_id_cached(_manifest_cache_key(path))


def format_owned_lucky_cards(card_ids: list[str]) -> str:
    """将识别到的 card_id 列表格式化为简短中文（用于日志/UI）。"""
    if not card_ids:
        return "无"
    by_id = lucky_card_by_id()
    parts: list[str] = []
    for cid in card_ids:
        d = by_id.get(cid)
        if d is None:
            parts.append(cid)
            continue
        if d.type == LuckyCardType.MULTIPLY:
            parts.append(f"×{d.value}")
        elif d.type == LuckyCardType.STEP_FORWARD:
            parts.append(f"前进{d.value}")
        elif d.type == LuckyCardType.STEP_BACK:
            parts.append(f"后退{d.value}")
        elif d.type == LuckyCardType.NEXT:
            parts.append("下一关")
        else:
            parts.append(d.id)
    return "、".join(parts)
