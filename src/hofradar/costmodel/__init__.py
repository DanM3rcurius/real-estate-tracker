"""Total cost of ownership for a farmstead: purchase + side costs + renovation.

Public surface, per ``docs/MODULE_API.md``::

    estimate_costs(prop, profile) -> CostResult
    acquisition_costs(price, profile) -> float
    infer_renovation_tier(prop) -> RenovationTier   # the reader's tier, else the listing's
    listing_renovation_tier(prop) -> RenovationTier # the listing's alone
    renovation_evidence(prop) -> str                # "reader" | "observed" | "inferred"
    listing_renovation_evidence(prop) -> str        # "observed" | "inferred"
    reader_renovation_tier(prop) -> RenovationTier | None
    READER_TIERS, STATED_EVIDENCE
"""

from __future__ import annotations

from hofradar.costmodel.estimator import acquisition_costs, estimate_costs
from hofradar.costmodel.renovation import (
    READER_TIERS,
    STATED_EVIDENCE,
    infer_renovation_tier,
    listing_renovation_evidence,
    listing_renovation_tier,
    reader_renovation_tier,
    renovation_evidence,
)

__all__ = [
    "READER_TIERS",
    "STATED_EVIDENCE",
    "acquisition_costs",
    "estimate_costs",
    "infer_renovation_tier",
    "listing_renovation_evidence",
    "listing_renovation_tier",
    "reader_renovation_tier",
    "renovation_evidence",
]
