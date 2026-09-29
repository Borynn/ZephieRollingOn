"""Model package discovery and the clients that drive them."""

from __future__ import annotations

from zephie_rolling_on.decision_models.discover import (
    DEFAULT_MODELS_DIR,
    discover_decision_models,
    ensure_models_dir,
    models_dir,
)
from zephie_rolling_on.decision_models.model_host_client import (
    PACKAGE_GLOB,
    PACKAGE_SUFFIX,
    HostedDecisionModel,
    ModelHostError,
    ModelHostProcess,
    host_executable,
    model_id_from_filename,
)
from zephie_rolling_on.decision_models.runtime import (
    active_model_ready,
    clear_active_decision_model,
    get_active_decision_model,
    set_active_decision_model,
)
from zephie_rolling_on.decision_models.types import (
    CARD_TO_NATIVE,
    MAX_HAND,
    N_CARDS,
    NATIVE_CARD_IDS,
    DecisionModelDisplayInfo,
    DecisionModelImportResult,
    DecisionModelPackage,
    DecisionResult,
    DiscoveredModel,
)
from zephie_rolling_on.decision_models.vela_package import (
    PKG_SUFFIX as OPEN_PKG_SUFFIX,
)
from zephie_rolling_on.decision_models.vela_package import (
    VelaPackageError,
    VelaPackageModel,
)

__all__ = [
    "CARD_TO_NATIVE",
    "DEFAULT_MODELS_DIR",
    "DecisionModelDisplayInfo",
    "DecisionModelImportResult",
    "DecisionModelPackage",
    "DecisionResult",
    "DiscoveredModel",
    "HostedDecisionModel",
    "MAX_HAND",
    "ModelHostError",
    "ModelHostProcess",
    "N_CARDS",
    "NATIVE_CARD_IDS",
    "OPEN_PKG_SUFFIX",
    "PACKAGE_GLOB",
    "PACKAGE_SUFFIX",
    "VelaPackageError",
    "VelaPackageModel",
    "active_model_ready",
    "clear_active_decision_model",
    "discover_decision_models",
    "ensure_models_dir",
    "get_active_decision_model",
    "host_executable",
    "model_id_from_filename",
    "models_dir",
    "set_active_decision_model",
]
