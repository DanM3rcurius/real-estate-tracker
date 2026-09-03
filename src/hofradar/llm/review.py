"""The language model comes last, and it is not allowed to invent facts.

Everything deterministic has already happened by the time this module runs:
parsing, deduplication, the geographic filter, availability verification and the
cost model. The model is handed a small, structured dossier of the survivors and
asked to do the one thing rules cannot: read the prose like a sceptical buyer.

Two hard constraints, enforced here rather than hoped for in the prompt:

1. It may not change a number. Its output is advisory - a summary, a risk list,
   a seclusion reading, a development reading. Prices, areas and distances stay
   exactly as the deterministic stages left them.
2. It runs on at most ``gates.llm_review_size`` properties per run, so cost is
   bounded and predictable no matter how many pages the crawlers touched.

Without an API key the whole stage is skipped and the system still works.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from hofradar.config import SearchProfile
from hofradar.db.models import Property

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("HOFRADAR_LLM_MODEL", "claude-sonnet-5")
MAX_DESCRIPTION_CHARS = 6000
MAX_CONCURRENCY = 4


class LLMUnavailable(RuntimeError):
    """Raised when no API key or SDK is present. The pipeline treats it as 'skip'."""


@dataclass(slots=True)
class ReviewVerdict:
    """Advisory output. Never overwrites a parsed fact."""

    summary: str = ""
    risks: list[str] = field(default_factory=list)
    #: 0-10, feeds the development term of the fit score as *evidence*, not as a claim.
    development_score: float | None = None
    seclusion: str | None = None  # alleinlage | ortsrand | locker | dicht | unklar
    condition_reading: str | None = None
    is_farmstead: bool | None = None
    contradictions: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


SYSTEM_PROMPT = """\
Du pruefst Immobilien-Exposes fuer einen Kaeufer, der eine Hofstelle oder ein Sacherl
in Oberbayern sucht. Du bist skeptisch und praezise.

Regeln:
- Du aenderst KEINE Zahlen. Preise, Flaechen und Entfernungen sind bereits geprueft.
- Du bewertest nur, was im Text tatsaechlich belegt ist. Marketingsprache ist kein Beleg.
- "Entwicklungspotenzial" vom Makler allein ist KEIN Nachweis der Teilbarkeit.
  Nachweis heisst: Flurstuecke benannt, Bebauungsplan zitiert, Bauvoranfrage erwaehnt,
  Teilungsgenehmigung erwaehnt.
- Widersprueche zwischen Text und Fotos meldest du als Widerspruch, du entscheidest sie nicht.
- Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema.
"""

RESPONSE_SCHEMA = {
    "summary": "Zwei bis drei Saetze: warum interessant, was der Haken ist.",
    "risks": ["kurze Risiko-Stichpunkte"],
    "development_score": "0-10, nur belegte Teilbarkeit/Baurecht zaehlt hoch",
    "seclusion": "alleinlage | ortsrand | locker | dicht | unklar",
    "condition_reading": "light | medium | heavy | complete | unklar",
    "is_farmstead": "true/false: ist das wirklich eine Hofstelle/Sacherl?",
    "contradictions": ["Text sagt X, andere Evidenz sagt Y"],
}


def build_review_prompt(prop: Property, profile: SearchProfile) -> str:
    """The dossier handed to the model. Deliberately compact - tokens are the budget."""
    cost = prop.cost_estimate
    dossier = {
        "titel": prop.canonical_title,
        "ort": prop.town,
        "plz": prop.postcode,
        "typ": prop.property_type,
        "preis": prop.price,
        "preis_art": prop.price_type,
        "grundstueck_qm": prop.land_sqm,
        "wohnflaeche_qm": prop.living_sqm,
        "nutzflaeche_qm": prop.usable_sqm,
        "baujahr": prop.year_built,
        "zustand": prop.condition,
        "nebengebaeude": prop.outbuildings,
        "merkmale": prop.building_features,
        "besonderheiten": prop.special_features,
        "entfernung_luftlinie_km": prop.distance_air_km,
        "entfernung_fahrstrecke_km": prop.distance_driving_km,
        "fahrstrecke_geprueft": prop.distance_driving_km is not None,
        "zwangsversteigerung": prop.is_foreclosure,
        "denkmal": prop.is_monument,
        "privatverkauf": prop.is_private_seller,
        "erstmals_gesehen": prop.first_seen.isoformat() if prop.first_seen else None,
        "preisreduktionen": prop.price_reduction_count,
        "geschaetzte_gesamtkosten": cost.total_mid if cost else None,
        "budget_gesamt": profile.budget.total_budget_max,
        "beschreibung": (prop.description or "")[:MAX_DESCRIPTION_CHARS],
    }
    return (
        "Objektdossier:\n"
        + json.dumps(dossier, ensure_ascii=False, indent=2, default=str)
        + "\n\nAntworte mit JSON nach diesem Schema:\n"
        + json.dumps(RESPONSE_SCHEMA, ensure_ascii=False, indent=2)
    )


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise LLMUnavailable("the anthropic SDK is not installed") from exc
    return AsyncAnthropic(api_key=api_key)


def _parse_verdict(text: str) -> ReviewVerdict:
    """Tolerate the model wrapping its JSON in prose or a fenced block."""
    payload: dict = {}
    candidate = text.strip()
    if "```" in candidate:
        parts = candidate.split("```")
        for part in parts:
            part = part.removeprefix("json").strip()
            if part.startswith("{"):
                candidate = part
                break
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            log.warning("could not parse LLM response as JSON")
    dev = payload.get("development_score")
    try:
        dev_value = float(dev) if dev is not None else None
    except (TypeError, ValueError):
        dev_value = None
    return ReviewVerdict(
        summary=str(payload.get("summary") or "").strip(),
        risks=[str(r) for r in payload.get("risks") or []],
        development_score=dev_value,
        seclusion=payload.get("seclusion"),
        condition_reading=payload.get("condition_reading"),
        is_farmstead=payload.get("is_farmstead"),
        contradictions=[str(c) for c in payload.get("contradictions") or []],
        raw=payload,
    )


async def review_property(prop: Property, profile: SearchProfile) -> ReviewVerdict:
    client = _client()
    response = await client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_review_prompt(prop, profile)}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_verdict(text)


async def review_properties(
    session: Session, properties: list[Property], profile: SearchProfile
) -> int:
    """Review up to ``gates.llm_review_size`` properties. Returns how many succeeded."""
    import asyncio

    _client()  # fail fast with LLMUnavailable before doing any work
    batch = properties[: profile.gates.llm_review_size]
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _one(prop: Property) -> tuple[Property, ReviewVerdict | None]:
        async with semaphore:
            try:
                return prop, await review_property(prop, profile)
            except Exception:  # a single failure must not abort the run
                log.exception("LLM review failed for %s", prop.public_id)
                return prop, None

    results = await asyncio.gather(*(_one(p) for p in batch))
    succeeded = 0
    for prop, verdict in results:
        if verdict is None:
            continue
        prop.llm_summary = verdict.summary
        prop.llm_risks = verdict.risks
        prop.llm_reviewed_at = datetime.now(UTC)
        # Advisory only: recorded as evidence, never written over a parsed fact.
        evidence = dict(prop.evidence or {})
        evidence["llm_review"] = {
            "source": "llm",
            "url": None,
            "quote": verdict.summary[:500],
            "confidence": 0.5,
            "observed_at": datetime.now(UTC).isoformat(),
            "development_score": verdict.development_score,
            "seclusion": verdict.seclusion,
            "condition_reading": verdict.condition_reading,
            "contradictions": verdict.contradictions,
        }
        prop.evidence = evidence
        session.add(prop)
        succeeded += 1
    session.flush()
    return succeeded
