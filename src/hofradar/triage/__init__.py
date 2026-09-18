"""Fast, typed second opinions on the questions a regex cannot settle.

The crawl yields pages a keyword list cannot classify with confidence: a
"Bauernhaus" advertised at "1.400 EUR warm" with no rent vocabulary the
normaliser knows, a "Wohnung" that is in fact the Austragshaus of a farm
being sold whole, a flat whose title says "Anwesen". Every one of those used
to reach the radar, get geocoded, get scored, and sit in the top ten until a
reader archived it by hand.

A System One model (TypeSafe's Jev, https://docs.typesafe.ai) is built for
exactly this shape of question: hand it the listing as state, ask typed
questions (*is this for sale or for rent? what kind of dwelling is it?*), and
get back a distribution over the allowed answers in one fast, cheap call. It
cannot invent a fourth answer and it cannot write a number - the answers are
probabilities over labels this package chose - so it fits inside invariant 6
without a special case.

What it may and may not do here:

- It runs after the deterministic filters, on listings those could not reject,
  so a regex still does the bulk of the work for free.
- Its verdict is *evidence* (``Property.evidence["triage"]``), recorded with
  the model version and the full distribution, and the decision that reads
  it (:func:`decide`) is deterministic, thresholded by ``gates`` and therefore
  recomputable from the stored answer. Nothing it says is ever a price, an
  area or a distance (invariant 6), and it never verifies availability
  (invariant 4).
- Without ``TYPESAFE_API_KEY`` the stage is absent and the run log says so.
  A failed call is counted and the listing proceeds as if never asked; a
  triage outage must not turn into an empty radar.

The scoring engine reads the same evidence through the same :func:`decide`,
so a property ingested before the gate existed is still rejected the next
time it is scored - the rule lives in one place. See docs/DECISIONS.md 23.
"""

from __future__ import annotations

from hofradar.triage.jev import (
    JEV_MODEL_ENV,
    TYPESAFE_API_KEY_ENV,
    TYPESAFE_BASE_URL_ENV,
    JevTriage,
    TriageUnavailable,
    TriageVerdict,
)
from hofradar.triage.rules import (
    DWELLING_FLAT,
    OFFER_RENT,
    TRIAGE_EVIDENCE_KEY,
    TriageDecision,
    decide,
    verdict_from_evidence,
)

__all__ = [
    "DWELLING_FLAT",
    "JEV_MODEL_ENV",
    "OFFER_RENT",
    "TRIAGE_EVIDENCE_KEY",
    "TYPESAFE_API_KEY_ENV",
    "TYPESAFE_BASE_URL_ENV",
    "JevTriage",
    "TriageDecision",
    "TriageUnavailable",
    "TriageVerdict",
    "decide",
    "verdict_from_evidence",
]
