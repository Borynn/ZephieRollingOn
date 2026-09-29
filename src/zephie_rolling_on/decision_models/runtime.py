"""Active decision model (process singleton)."""

from __future__ import annotations

from typing import Any

_active: Any | None = None


def set_active_decision_model(model: Any | None) -> None:
    global _active
    _active = model


def get_active_decision_model() -> Any | None:
    return _active


def clear_active_decision_model() -> None:
    global _active
    _active = None


def active_model_ready() -> bool:
    m = _active
    return m is not None and bool(getattr(m, "is_imported", False))
