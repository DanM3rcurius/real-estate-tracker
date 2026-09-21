"""The TypeSafe System One call, kept to one request shape and no SDK.

``typesafe-sdk`` exists and would do this in four lines, but it is built on
``httpx2`` and this suite's rule is that every outbound call is mocked with
``respx``, which only knows ``httpx``. One documented POST is small enough to
own outright, and owning it keeps the question texts - the part that actually
decides what gets rejected - in this file, under version control, next to
the rule that reads the answers.

Wire shape (https://docs.typesafe.ai/api): ``POST {base}/v1/systemone`` with
``Authorization: Bearer <key>`` and a body of ``state`` (any JSON), ``model``
and ``questions`` (name -> ``{"type": "choice"|"noul"|"score", ...}``); the
answer carries ``answers`` keyed the same way, each with a ``choice`` plus
``probabilities`` and ``confidence``, or a bare ``noul`` probability.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

from hofradar.contracts import PAGE_KIND_LISTING, NormalizedListing
from hofradar.triage.rules import TRIAGE_EVIDENCE_KEY, TriageDecision, TriageVerdict, decide

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import GateConfig

log = logging.getLogger(__name__)

TYPESAFE_API_KEY_ENV = "TYPESAFE_API_KEY"
TYPESAFE_BASE_URL_ENV = "TYPESAFE_BASE_URL"
JEV_MODEL_ENV = "HOFRADAR_JEV_MODEL"

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
SYSTEM_ONE_PATH = "/v1/systemone"
REQUEST_TIMEOUT_SECONDS = 10.0
#: The same cap the Claude review applies - tokens are the budget, and the
#: offer type is decided in the first paragraph anyway.
MAX_DESCRIPTION_CHARS = 6000
USER_AGENT = "hofradar-triage/0.1"

#: Question names. They are also the keys in the response, so the parser and
#: the evidence use the same words.
Q_OFFER = "angebotsart"
Q_DWELLING = "objektart"
Q_SUBSTANCE = "hofsubstanz"

#: The questions. Criteria are in German because the listings are; the labels
#: are the strings :mod:`hofradar.triage.rules` keys on (``OFFER_RENT``,
#: ``DWELLING_FLAT``), so renaming one means renaming both.
QUESTIONS: dict[str, dict[str, Any]] = {
    Q_OFFER: {
        "type": "choice",
        "instructions": "Wird das Objekt zum Kauf oder zur Miete angeboten?",
        "criteria": {
            "kauf": (
                "Verkauf: Kaufpreis, Verkehrswert, Zwangsversteigerung, "
                "Erbbaurecht zum Kauf, 'zu verkaufen'"
            ),
            "miete": (
                "Vermietung oder Verpachtung: Kaltmiete, Warmmiete, Miete pro Monat, "
                "'zu vermieten', Kaution, Mietvertrag"
            ),
            "unklar": "Aus dem Text nicht erkennbar",
        },
    },
    Q_DWELLING: {
        "type": "choice",
        "instructions": "Was für ein Objekt wird angeboten?",
        "criteria": {
            "hofstelle": (
                "Hofstelle, Sacherl, Bauernhof, Resthof, Vierseithof, Anwesen mit "
                "Nebengebäuden wie Stadel, Stall, Scheune oder Tenne"
            ),
            "haus": (
                "Einfamilienhaus, Doppelhaushälfte, Reihenhaus oder Bauernhaus ohne "
                "Wirtschaftsgebäude"
            ),
            "wohnung": (
                "Wohnung, Apartment, Etagen-, Dachgeschoss- oder Eigentumswohnung, "
                "Penthouse, Maisonette, WG-Zimmer"
            ),
            "grundstueck": "Unbebautes Grundstück, Bauplatz, Acker, Wald oder Wiese ohne Gebäude",
            "gewerbe": "Gewerbe-, Büro-, Lager-, Gastronomieobjekt oder Halle",
            "sonstiges": "Garage, Stellplatz, Ferienobjekt oder etwas anderes",
        },
    },
    Q_SUBSTANCE: {
        "type": "noul",
        "instructions": (
            "Belegt der Text echte landwirtschaftliche Bausubstanz - Stadel, Stall, "
            "Scheune, Tenne, Wirtschaftsgebäude oder eine große Hoffläche?"
        ),
    },
}


class TriageUnavailable(RuntimeError):
    """No API key. The pipeline treats it as 'stage absent' and logs that."""


def build_state(listing: NormalizedListing) -> dict[str, Any]:
    """The listing as the model sees it: the text plus the raw figures.

    Deliberately not the normaliser's own guesses (``property_type``,
    ``exclusion_flags``): the point of a second opinion is that it is formed
    from the source, not from the first opinion.
    """
    return {
        "titel": listing.title or "",
        "beschreibung": (listing.description or "")[:MAX_DESCRIPTION_CHARS],
        "preis": listing.price_raw or "",
        "zimmer": listing.rooms,
        "wohnflaeche_qm": listing.living_sqm,
        "grundstueck_qm": listing.land_sqm,
        "ort": listing.town or "",
    }


def parse_verdict(payload: dict[str, Any]) -> TriageVerdict:
    """Read the documented answer shape. Raises ``ValueError`` on anything else,
    so a changed API surfaces as a counted failure, not a silent 'kauf'."""
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("response carries no 'answers' object")
    offer = answers.get(Q_OFFER) or {}
    dwelling = answers.get(Q_DWELLING) or {}
    if "choice" not in offer or "choice" not in dwelling:
        raise ValueError("response lacks a choice for angebotsart/objektart")
    substance = answers.get(Q_SUBSTANCE) or {}
    farm = substance.get("noul")
    return TriageVerdict(
        model=str(payload.get("model") or "unknown"),
        offer_kind=str(offer["choice"]),
        offer_probabilities=_probabilities(offer),
        dwelling_kind=str(dwelling["choice"]),
        dwelling_probabilities=_probabilities(dwelling),
        farm_substance=float(farm) if farm is not None else None,
    )


def _probabilities(answer: dict[str, Any]) -> dict[str, float]:
    raw = answer.get("probabilities")
    if not isinstance(raw, dict):
        return {}
    return {str(k): float(v) for k, v in raw.items()}


class JevTriage:
    """One client per pipeline run. ``asked``/``failed`` feed the run log."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.asked = 0
        self.failed = 0
        self._endpoint = base_url.rstrip("/") + SYSTEM_ONE_PATH
        self._client = client or httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        self._owns_client = client is None

    @classmethod
    def from_env(cls) -> JevTriage:
        """Raises :class:`TriageUnavailable` without a key - the caller decides
        whether that is 'skip' (the pipeline) or an error (a CLI)."""
        api_key = os.environ.get(TYPESAFE_API_KEY_ENV)
        if not api_key:
            raise TriageUnavailable(f"{TYPESAFE_API_KEY_ENV} is not set")
        return cls(
            api_key,
            base_url=os.environ.get(TYPESAFE_BASE_URL_ENV) or DEFAULT_BASE_URL,
            model=os.environ.get(JEV_MODEL_ENV) or DEFAULT_MODEL,
        )

    async def classify(self, listing: NormalizedListing) -> TriageVerdict | None:
        """Ask the three questions. ``None`` means 'no verdict this run' - the
        failure is logged and counted, never raised into the crawl loop."""
        self.asked += 1
        body = {"state": build_state(listing), "model": self.model, "questions": QUESTIONS}
        try:
            response = await self._client.post(self._endpoint, json=body)
            response.raise_for_status()
            return parse_verdict(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            self.failed += 1
            log.warning("triage failed for %s: %s: %s", listing.url, type(exc).__name__, exc)
            return None

    def stats(self) -> dict[str, Any]:
        return {"enabled": True, "model": self.model, "asked": self.asked, "failed": self.failed}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def annotate(
    listing: NormalizedListing, gates: GateConfig, triage: JevTriage
) -> TriageDecision | None:
    """Ask, record, decide - the one path every entry point takes.

    The crawl loop and the paste box both call this, so a pasted rental and a
    crawled one carry the same evidence and the same warning. The verdict goes
    into ``listing.evidence["triage"]`` (ingest merges it onto the row) and
    the decision's warnings onto ``listing.warnings``. ``None`` means the call
    failed or the page is not a listing; the caller decides what a rejection
    means for it - the crawl loop drops an unknown row, the paste box never
    drops anything a human chose to paste and leaves it to the scoring gate.
    """
    if listing.page_kind != PAGE_KIND_LISTING:
        return None
    verdict = await triage.classify(listing)
    if verdict is None:
        return None
    listing.evidence[TRIAGE_EVIDENCE_KEY] = verdict.to_evidence()
    decision = decide(verdict, gates, has_substance=bool(listing.outbuildings))
    listing.warnings.extend(decision.warnings)
    return decision
