"""Public compatibility view of the supported RenderPlan interchange contracts."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .collection import RENDERPLAN_SET_FORMAT, RENDERPLAN_SET_VERSION
from .model import (
    MANIFEST_FORMAT,
    PLAN_FORMAT,
    PREFLIGHT_FORMAT,
    PROFILE_FORMAT,
    SCHEMA_VERSION,
)

CONTRACTS: Mapping[str, int] = MappingProxyType(
    {
        PROFILE_FORMAT: SCHEMA_VERSION,
        PLAN_FORMAT: SCHEMA_VERSION,
        PREFLIGHT_FORMAT: SCHEMA_VERSION,
        MANIFEST_FORMAT: SCHEMA_VERSION,
        RENDERPLAN_SET_FORMAT: RENDERPLAN_SET_VERSION,
    }
)


def supported_contracts() -> Mapping[str, int]:
    """Return the immutable mapping of supported document formats to versions."""
    return CONTRACTS


__all__ = ["CONTRACTS", "supported_contracts"]
