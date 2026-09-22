"""The deterministic half of triage: what a stored verdict means for a listing.

Kept apart from the HTTP client so that ``hofradar.scoring.engine`` can apply
the rule to evidence that is already in the database without importing
anything that talks to the network, and so that the crawl loop and the
scoring gate cannot drift: both call :func:`decide`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import GateConfig

#: Where the verdict lives in ``NormalizedListing.evidence`` / ``Property.evidence``.
TRIAGE_EVIDENCE_KEY = "triage"

#: The two answers that can reject a listing. They are the labels of the
#: ``angebotsart`` and ``objektart`` questions in :mod:`hofradar.triage.jev`;
#: :func:`decide` names them in its reason so the run log and the reject
#: vocabulary of the scoring engine can key on them.
OFFER_RENT = "miete"
DWELLING_FLAT = "wohnung"

#: Advisory flag when the model's most likely answer is a rental or a flat
#: but the probability stays under ``gates.triage_reject_min_probability``.
FLAG_TRIAGE_DOUBT = "TRIAGE_DOUBTS_FARMSTEAD"


@dataclass(slots=True)
class TriageVerdict:
    """What the model answered, with the whole distribution kept."""

    model: str
    offer_kind: str
    offer_probabilities: dict[str, float]
    dwelling_kind: str
    dwelling_probabilities: dict[str, float]
    #: 0-1: does the text show real farm building substance?
    farm_substance: float | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_evidence(self) -> dict[str, Any]:
        """The ``Evidence``-shaped dict every other fact uses, plus the answers.

        ``confidence`` is the probability of the deciding answer, so
        ``lifecycle._rules.merge_evidence`` keeps the more certain of two
        verdicts about the same listing.
        """
        deciding = max(
            self.offer_probabilities.get(OFFER_RENT, 0.0),
            self.dwelling_probabilities.get(DWELLING_FLAT, 0.0),
            self.offer_probabilities.get(self.offer_kind, 0.0),
        )
        return {
            "source": "jev",
            "url": None,
            "quote": None,
            "confidence": round(deciding, 4),
            "observed_at": self.observed_at.isoformat(),
            "model": self.model,
            "offer_kind": self.offer_kind,
            "offer_probabilities": self.offer_probabilities,
            "dwelling_kind": self.dwelling_kind,
            "dwelling_probabilities": self.dwelling_probabilities,
            "farm_substance": self.farm_substance,
        }


def verdict_from_evidence(entry: dict[str, Any] | None) -> TriageVerdict | None:
    """Rebuild a verdict from ``Property.evidence["triage"]``; ``None`` if absent
    or not something this module wrote."""
    if not isinstance(entry, dict) or entry.get("source") != "jev":
        return None
    try:
        observed = datetime.fromisoformat(str(entry.get("observed_at")))
    except (TypeError, ValueError):
        observed = datetime.now(UTC)
    farm = entry.get("farm_substance")
    return TriageVerdict(
        model=str(entry.get("model") or "unknown"),
        offer_kind=str(entry.get("offer_kind") or ""),
        offer_probabilities=_float_map(entry.get("offer_probabilities")),
        dwelling_kind=str(entry.get("dwelling_kind") or ""),
        dwelling_probabilities=_float_map(entry.get("dwelling_probabilities")),
        farm_substance=float(farm) if farm is not None else None,
        observed_at=observed,
    )


def _float_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, prob in value.items():
        try:
            out[str(key)] = float(prob)
        except (TypeError, ValueError):
            continue
    return out


@dataclass(slots=True)
class TriageDecision:
    """``reject_reason`` is ``OFFER_RENT``, ``DWELLING_FLAT`` or ``None``;
    ``flags`` are advisory and go on the score."""

    reject_reason: str | None = None
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def decide(verdict: TriageVerdict, gates: GateConfig, *, has_substance: bool) -> TriageDecision:
    """Turn a stored verdict into a rejection, a flag, or nothing.

    A rental rejects on probability alone: nothing about a farm contradicts
    "zu vermieten". A flat verdict is a statement about the *type*, and the
    same escape hatch as the keyword gate applies (``FLAG_EXCLUSION_OVERRIDDEN``
    in the scoring engine): deterministic farm substance - outbuildings the
    normaliser actually found - turns a reject into a flag, because a
    Vierseithof advertised as "Wohnung im Austragshaus" is still a
    Vierseithof.
    """
    threshold = gates.triage_reject_min_probability
    p_rent = verdict.offer_probabilities.get(OFFER_RENT, 0.0)
    p_flat = verdict.dwelling_probabilities.get(DWELLING_FLAT, 0.0)

    if p_rent >= threshold:
        return TriageDecision(
            reject_reason=OFFER_RENT,
            warnings=[f"Triage ({verdict.model}): Mietangebot mit {p_rent:.0%} Wahrscheinlichkeit"],
        )
    if p_flat >= threshold and not has_substance:
        return TriageDecision(
            reject_reason=DWELLING_FLAT,
            warnings=[
                f"Triage ({verdict.model}): Wohnung mit {p_flat:.0%} Wahrscheinlichkeit, "
                "keine Nebengebäude erkannt"
            ],
        )
    if verdict.offer_kind == OFFER_RENT or verdict.dwelling_kind == DWELLING_FLAT:
        what = "Mietangebot" if verdict.offer_kind == OFFER_RENT else "Wohnung"
        prob = p_rent if verdict.offer_kind == OFFER_RENT else p_flat
        return TriageDecision(
            flags=[FLAG_TRIAGE_DOUBT],
            warnings=[
                f"Triage ({verdict.model}): wahrscheinlich {what} ({prob:.0%}), "
                "unter der Ausschlussschwelle - bitte prüfen"
            ],
        )
    return TriageDecision()
