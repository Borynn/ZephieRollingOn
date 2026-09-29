"""Scan ``decision_models/`` for model packages.

Two package formats, selected by file extension:

  ``zephie_*.zm``  interpreted by ``model_host.exe`` (separate process)
  ``*.vpk``        loaded in-process by a native engine

Both implement the same ``DecisionModelPackage`` protocol, so the rest of the
application does not care which one it is holding.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from zephie_rolling_on.decision_models.model_host_client import (
    PACKAGE_GLOB,
    HostedDecisionModel,
)
from zephie_rolling_on.decision_models.types import DiscoveredModel
from zephie_rolling_on.decision_models.vela_package import (
    PKG_SUFFIX as OPEN_PKG_SUFFIX,
)
from zephie_rolling_on.decision_models.vela_package import VelaPackageModel
from zephie_rolling_on.paths import project_root

PROJECT_ROOT = project_root()
DEFAULT_MODELS_DIR = PROJECT_ROOT / "decision_models"


def models_dir(path: Path | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_MODELS_DIR


def ensure_models_dir(path: Path | None = None) -> Path:
    root = models_dir(path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def discover_decision_models(
    path: Path | None = None,
    *,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> list[DiscoveredModel]:
    """Find every usable model package under ``path``."""
    root = ensure_models_dir(path)
    found: list[DiscoveredModel] = []

    def _add(p: Path, factory: Callable[[], object]) -> None:
        try:
            pkg = factory()
            pkg.display_info()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            if on_error is not None:
                on_error(p.name, exc)
            return
        found.append(DiscoveredModel(package=pkg, folder=str(p.parent)))  # type: ignore[arg-type]

    for p in sorted(root.glob(PACKAGE_GLOB), key=lambda x: x.name.lower()):
        _add(p, lambda p=p: HostedDecisionModel(p, models_dir=root))

    for p in sorted(root.glob(f"*{OPEN_PKG_SUFFIX}"), key=lambda x: x.name.lower()):
        _add(p, lambda p=p: VelaPackageModel(p))

    return found
