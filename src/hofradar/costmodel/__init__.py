"""Total cost of ownership for a farmstead: purchase + side costs + renovation.

Public surface, per ``docs/MODULE_API.md``::

    estimate_costs(prop, profile) -> CostResult
    acquisition_costs(price, profile) -> float
    infer_renovation_tier(prop) -> RenovationTier
"""

from __future__ import annotations

from hofradar.costmodel.estimator import acquisition_costs, estimate_costs
from hofradar.costmodel.renovation import infer_renovation_tier

__all__ = ["acquisition_costs", "estimate_costs", "infer_renovation_tier"]
