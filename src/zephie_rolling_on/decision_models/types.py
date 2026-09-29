"""Shared types for decision model packages.

Everything here is the *contract* between the UI and a model package; it says
nothing about how a package is built or stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Card vocabulary order used when talking to a model package.
# This is game data (card ids as used by the game), not model data.
NATIVE_CARD_IDS: tuple[str, ...] = (
    "back_1",
    "back_2",
    "back_3",
    "forward_1",
    "forward_10",
    "forward_11",
    "forward_12",
    "forward_2",
    "forward_3",
    "forward_4",
    "forward_5",
    "forward_6",
    "forward_7",
    "forward_8",
    "forward_9",
    "multiply_10",
    "multiply_2",
    "multiply_3",
    "multiply_5",
    "multiply_7",
    "multiply_8",
    "next_1",
)

CARD_TO_NATIVE: dict[str, int] = {cid: i for i, cid in enumerate(NATIVE_CARD_IDS)}

MAX_HAND = 5
N_CARDS = len(NATIVE_CARD_IDS)


@dataclass(frozen=True)
class DecisionModelDisplayInfo:
    """Model card shown in the picker dialog."""

    model_id: str
    name: str
    version: str = ""
    summary: str = ""
    param_lines: tuple[str, ...] = ()
    status_line: str = ""


@dataclass
class DecisionModelImportResult:
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class DecisionResult:
    """Return value of one decision."""

    action: str
    """``"roll"`` or a card id."""

    rl_action: int
    """1 = roll, 2..23 = card index in the action vocabulary; 0 = error."""

    native_code: int
    """0 = roll; 1..hand_len = hand slot (1-based)."""

    v_cells: float
    """Estimated value of the position; unit is defined by the model."""


@runtime_checkable
class DecisionModelPackage(Protocol):
    """What the UI needs from any model package, regardless of format."""

    def display_info(self) -> DecisionModelDisplayInfo: ...

    def import_model(self, *, on_progress=None) -> DecisionModelImportResult: ...

    def decide(
        self,
        *,
        cell_id: int,
        dice_remaining: int,
        next_roll_free: bool = False,
        hand=...,
        drawn=...,
    ) -> DecisionResult: ...

    def import_art_png(self, which: str) -> bytes | None: ...


@dataclass
class DiscoveredModel:
    package: DecisionModelPackage
    folder: str
    display: DecisionModelDisplayInfo = field(init=False)

    def __post_init__(self) -> None:
        self.display = self.package.display_info()
