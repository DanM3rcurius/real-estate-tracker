"""Total cost of ownership for a farmstead: purchase + side costs + renovation.

Public surface, per ``docs/MODULE_API.md``::

    estimate_costs(prop, profile) -> CostResult
    acquisition_costs(price, profile) -> float
    infer_renovation_tier(prop, rates=None) -> RenovationTier
    automatic_renovation_tier(prop, rates=None) -> RenovationTier  # manual ignored
    renovation_evidence(prop) -> str   # "manual" | "observed" | "inferred"
    manual_tier(prop) -> RenovationTier | None
    MANUAL_TIERS
"""

from __future__ import annotations

from hofradar.costmodel.estimator import acquisition_costs, estimate_costs
from hofradar.costmodel.renovation import (
    EVIDENCE_INFERRED,
    EVIDENCE_MANUAL,
    EVIDENCE_OBSERVED,
    MANUAL_TIERS,
    automatic_renovation_tier,
    infer_renovation_tier,
    manual_tier,
    renovation_evidence,
)

__all__ = [
    "EVIDENCE_INFERRED",
    "EVIDENCE_MANUAL",
    "EVIDENCE_OBSERVED",
    "MANUAL_TIERS",
    "acquisition_costs",
    "automatic_renovation_tier",
    "estimate_costs",
    "infer_renovation_tier",
    "manual_tier",
    "renovation_evidence",
]
