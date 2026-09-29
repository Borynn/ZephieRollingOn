"""Resolve project root for source runs and frozen portable builds."""

from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: exe beside config/ / assets/ / decision_models/
        return Path(sys.executable).resolve().parent
    # src/zephie_rolling_on/paths.py → repo root
    return Path(__file__).resolve().parents[2]
