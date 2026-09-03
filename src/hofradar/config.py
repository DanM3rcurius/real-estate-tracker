"""Configuration = the search DNA.

Nothing about *what* we are looking for is hard-coded. The two parameters the
user changes most - **how far** and **how much money** - are first-class fields
on :class:`SearchProfile`, are exposed as sliders in the web UI, and are inputs
to every score. Changing one changes ``profile_hash``, which invalidates the
score cache; the underlying facts and history are untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, computed_field, field_validator

#: Bundled copies of the YAML files, installed with the package.
PACKAGED_CONFIG_DIR = Path(__file__).parent / "_config_defaults"


def find_config_dir() -> Path:
    """Locate the search DNA.

    ``hofradar serve`` from the wrong directory used to silently fall back to
    hard-coded defaults - the app came up, said "registered 0 sources", and
    quietly ignored the user's radius, budget and keyword vocabulary. Config is
    now looked for in a defined order and the resolution is logged, so a wrong
    answer is visible instead of silent:

    1. ``HOFRADAR_CONFIG_DIR`` if set - always wins, never guessed past
    2. ``./config`` in the current directory
    3. a ``config/`` directory in any parent (running from ``src/`` or ``tests/``)
    4. the copies bundled inside the installed package
    """
    from_env = os.environ.get("HOFRADAR_CONFIG_DIR")
    if from_env:
        return Path(from_env)

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        directory = candidate / "config"
        if (directory / "search.yaml").is_file():
            return directory

    return PACKAGED_CONFIG_DIR


def __getattr__(name: str) -> Any:
    """``CONFIG_DIR`` stays importable, but is resolved when it is read."""
    if name == "CONFIG_DIR":
        return find_config_dir()
    raise AttributeError(name)


# --------------------------------------------------------------------------- #
# The adjustable parameters
# --------------------------------------------------------------------------- #


class Center(BaseModel):
    name: str = "Westham, Feldkirchen-Westerham"
    lat: float = 47.907
    lon: float = 11.840


class RadiusConfig(BaseModel):
    """Geography. Air and driving distance are separate facts and stay separate.

    ``air_km_max`` is the primary slider. The driving limits derive from it by
    ``driving_factor`` unless the user overrides them explicitly - roads in the
    Alpine foreland are typically 1.25-1.45x the straight line.
    """

    air_km_max: float = Field(80, ge=1, le=400)
    driving_km_soft_max: float | None = Field(None, ge=1, le=600)
    driving_km_hard_max: float | None = Field(None, ge=1, le=600)
    driving_factor_soft: float = Field(1.25, ge=1.0, le=3.0)
    driving_factor_hard: float = Field(1.45, ge=1.0, le=3.0)
    #: If we could not measure a road route, do NOT silently pass the property.
    require_driving_check: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_driving_soft(self) -> float:
        if self.driving_km_soft_max is not None:
            return self.driving_km_soft_max
        return round(self.air_km_max * self.driving_factor_soft, 1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_driving_hard(self) -> float:
        if self.driving_km_hard_max is not None:
            return self.driving_km_hard_max
        return round(self.air_km_max * self.driving_factor_hard, 1)


class BudgetConfig(BaseModel):
    """Money. The headline slider is ``total_budget_max`` - all-in capital.

    A purchase price is meaningless on a farmstead without the renovation that
    follows it, so the hard gate is on total cost, not on the asking price.
    The purchase bands below are derived from the total budget unless overridden.
    """

    #: All-in capital the user can actually deploy (purchase + fees + renovation).
    total_budget_max: float = Field(1_200_000, ge=10_000)
    #: Absolute ceiling; above this a property is excluded from the shortlist.
    total_budget_hard_max: float | None = Field(None, ge=10_000)
    #: Above this, only exceptional development potential keeps it in.
    total_budget_exceptional_max: float | None = Field(None, ge=10_000)

    #: Purchase price bands. None -> derived from total budget (see below).
    purchase_target_max: float | None = Field(None, ge=0)
    purchase_negotiation_max: float | None = Field(None, ge=0)
    purchase_hard_max: float | None = Field(None, ge=0)

    #: Share of the total budget that may go into the purchase itself.
    purchase_share_of_total: float = Field(0.625, gt=0, le=1.0)
    negotiation_uplift: float = Field(1.133, ge=1.0, le=2.0)
    exceptional_uplift: float = Field(1.20, ge=1.0, le=2.0)

    # Bavarian acquisition costs, as a fraction of the purchase price.
    grunderwerbsteuer_pct: float = Field(0.035, ge=0, le=0.20)
    notar_pct: float = Field(0.015, ge=0, le=0.10)
    grundbuch_pct: float = Field(0.005, ge=0, le=0.10)
    makler_pct: float = Field(0.0357, ge=0, le=0.10)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_purchase_target_max(self) -> float:
        if self.purchase_target_max is not None:
            return self.purchase_target_max
        return round(self.total_budget_max * self.purchase_share_of_total, -3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_purchase_negotiation_max(self) -> float:
        if self.purchase_negotiation_max is not None:
            return self.purchase_negotiation_max
        return round(self.effective_purchase_target_max * self.negotiation_uplift, -3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_purchase_hard_max(self) -> float:
        if self.purchase_hard_max is not None:
            return self.purchase_hard_max
        return round(self.effective_purchase_target_max * self.exceptional_uplift, -3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_total_hard_max(self) -> float:
        if self.total_budget_hard_max is not None:
            return self.total_budget_hard_max
        return round(self.total_budget_max * 1.25, -3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_total_exceptional_max(self) -> float:
        if self.total_budget_exceptional_max is not None:
            return self.total_budget_exceptional_max
        return round(self.total_budget_max * 1.125, -3)

    @property
    def acquisition_cost_pct(self) -> float:
        return (
            self.grunderwerbsteuer_pct + self.notar_pct + self.grundbuch_pct + self.makler_pct
        )


class LandConfig(BaseModel):
    preferred_min_sqm: float = 2000
    strong_min_sqm: float = 5000


class ScoreWeights(BaseModel):
    """Final ranking weights. Must sum to ~1.0; normalised on load if not."""

    fit: float = 0.35
    deal: float = 0.25
    hidden: float = 0.15
    freshness: float = 0.15
    confidence: float = 0.10

    def normalised(self) -> ScoreWeights:
        total = self.fit + self.deal + self.hidden + self.freshness + self.confidence
        if total <= 0:
            return ScoreWeights()
        return ScoreWeights(
            fit=self.fit / total,
            deal=self.deal / total,
            hidden=self.hidden / total,
            freshness=self.freshness / total,
            confidence=self.confidence / total,
        )


class GateConfig(BaseModel):
    """Hard rejections, applied before ranking."""

    min_confidence_for_shortlist: float = 70
    min_confidence_to_keep: float = 60
    #: Renovation above this multiple of the purchase price raises SANIERUNGSRISIKO.
    renovation_to_price_risk_ratio: float = 1.5
    #: Development score (0-10) required to survive the exceptional budget band.
    exceptional_development_min: float = 8
    #: A listing proven gone is never shortlisted.
    reject_removed: bool = True
    #: A self-reporting source kept talking and stopped naming this listing.
    stale_after_days: int = 45
    #: Nothing re-reports this listing (paste box, CSV, bulletin): it ages out
    #: on a longer clock, because there was never a stream to fall silent.
    unverified_stale_after_days: int = 180
    #: A listing matching the profile's `exclude` vocabulary is rejected unless
    #: it carries genuine farmstead substance that contradicts the match.
    reject_excluded: bool = True
    #: A property with no road route measured yet is held back, not silently passed.
    reject_unrouted: bool = False
    shortlist_size: int = 10
    llm_review_size: int = 100


class RenovationRates(BaseModel):
    """EUR per square metre bands, by tier."""

    light_min: float = 300
    light_max: float = 600
    medium_min: float = 700
    medium_max: float = 1200
    heavy_min: float = 1300
    heavy_max: float = 2000
    complete_min: float = 2000
    complete_max: float = 2800

    #: Farmsteads are not "living area x EUR/sqm". These are lump-sum components.
    roof_per_sqm_footprint: float = 350
    outbuilding_per_sqm: float = 450
    utilities_base: float = 80_000
    contingency_pct: float = 0.20
    immediate_capex_base: float = 25_000


class SearchProfile(BaseModel):
    """Everything the user can tune. This is what the sliders write to."""

    name: str = "default"
    center: Center = Field(default_factory=Center)
    radius: RadiusConfig = Field(default_factory=RadiusConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    land: LandConfig = Field(default_factory=LandConfig)
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    gates: GateConfig = Field(default_factory=GateConfig)
    renovation: RenovationRates = Field(default_factory=RenovationRates)

    property_types: list[str] = Field(default_factory=list)
    preferred_features: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("weights")
    @classmethod
    def _normalise_weights(cls, v: ScoreWeights) -> ScoreWeights:
        return v.normalised()

    def scoring_payload(self) -> dict[str, Any]:
        """The subset that actually changes a score. Drives ``profile_hash``."""
        return {
            "center": self.center.model_dump(),
            "radius": self.radius.model_dump(),
            "budget": self.budget.model_dump(),
            "land": self.land.model_dump(),
            "weights": self.weights.model_dump(),
            "gates": self.gates.model_dump(),
            "renovation": self.renovation.model_dump(),
            "property_types": sorted(self.property_types),
            "preferred_features": sorted(self.preferred_features),
            "exclude": sorted(self.exclude),
        }

    @property
    def profile_hash(self) -> str:
        blob = json.dumps(self.scoring_payload(), sort_keys=True, default=str)
        return hashlib.blake2s(blob.encode(), digest_size=12).hexdigest()


# --------------------------------------------------------------------------- #
# Static (non-slider) configuration
# --------------------------------------------------------------------------- #


class KeywordConfig(BaseModel):
    core: list[str] = Field(default_factory=list)
    buildings: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    hidden_phrases: list[str] = Field(default_factory=list)
    regional: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)

    @property
    def all_terms(self) -> list[str]:
        seen: dict[str, None] = {}
        for group in (
            self.core,
            self.buildings,
            self.features,
            self.hidden_phrases,
            self.regional,
        ):
            for term in group:
                seen.setdefault(term, None)
        return list(seen)


class SourceConfig(BaseModel):
    key: str
    name: str
    role: str = "discovery"
    adapter: str = "generic_rss"
    base_url: str | None = None
    region: str | None = None
    reliability: float = 0.5
    enabled: bool = False
    rate_limit_seconds: float = 2.0
    respect_robots: bool = True
    notes: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    profile: SearchProfile
    keywords: KeywordConfig
    sources: list[SourceConfig]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_profile(config_dir: Path | None = None) -> SearchProfile:
    d = config_dir or find_config_dir()
    search = _read_yaml(d / "search.yaml").get("search", {})
    scoring = _read_yaml(d / "scoring.yaml").get("scoring", {})
    merged = {**search, **scoring}
    return SearchProfile(**merged)


def load_keywords(config_dir: Path | None = None) -> KeywordConfig:
    d = config_dir or find_config_dir()
    return KeywordConfig(**_read_yaml(d / "keywords.yaml").get("keywords", {}))


def load_sources(config_dir: Path | None = None) -> list[SourceConfig]:
    d = config_dir or find_config_dir()
    raw = _read_yaml(d / "sources.yaml").get("sources", [])
    return [SourceConfig(**item) for item in raw]


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    return AppConfig(
        profile=load_profile(),
        keywords=load_keywords(),
        sources=load_sources(),
    )


def reload_config() -> AppConfig:
    load_config.cache_clear()
    return load_config()
