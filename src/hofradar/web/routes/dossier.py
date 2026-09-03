"""The Dossier - one property, and why we believe every word of it.

Every fact on this page is printed next to its evidence (source, quote, link).
A fact with no evidence is shown *and marked* rather than hidden: "we do not
know where this claim came from" is itself information the buyer needs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from hofradar.db.models import Property
from hofradar.web import history
from hofradar.web.charts import sparkline
from hofradar.web.deps import get_db, profile_from_query, render
from hofradar.web.filters import de_eur, de_km, de_number, de_sqm
from hofradar.web.query import best_url, change_chips, load_property, row_to_dict

router = APIRouter(tags=["dossier"])

#: ``user_state`` values the triage control accepts. "none" clears the flag.
USER_STATES: dict[str, str] = {
    "shortlist": "⭐ Shortlist",
    "watch": "👀 Beobachten",
    "contacted": "📞 Kontaktiert",
    "rejected": "🚫 Abgelehnt",
    "none": "– kein Status",
}

#: (attribute, German label, formatter). Order is the reading order of the page.
FACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("canonical_title", "Titel", "text"),
    ("property_type", "Objektart", "text"),
    ("price", "Preis", "eur"),
    ("price_type", "Preisart", "text"),
    ("price_first", "Erstpreis", "eur"),
    ("land_sqm", "Grundstück", "sqm"),
    ("living_sqm", "Wohnfläche", "sqm"),
    ("usable_sqm", "Nutzfläche", "sqm"),
    ("rooms", "Zimmer", "number"),
    ("year_built", "Baujahr", "year"),
    ("condition", "Zustand", "text"),
    ("street", "Straße", "text"),
    ("postcode", "PLZ", "text"),
    ("town", "Ort", "text"),
    ("district", "Ortsteil", "text"),
    ("distance_air_km", "Luftlinie", "km"),
    ("distance_driving_km", "Fahrstrecke", "driving_km"),
    ("geo_precision", "Standortgenauigkeit", "text"),
    ("building_features", "Gebäudemerkmale", "list"),
    ("outbuildings", "Nebengebäude", "list"),
    ("special_features", "Besonderheiten", "list"),
    ("exclusion_flags", "Ausschlussmerkmale", "list"),
)


def _format(value: Any, how: str) -> str:
    if how == "eur":
        return de_eur(value)
    if how == "sqm":
        return de_sqm(value)
    if how == "km":
        return de_km(value)
    if how == "driving_km":
        # Never fall back to the air distance. Unknown means unknown.
        return "nicht geprüft" if value is None else de_km(value)
    if how == "number":
        return de_number(value, 0)
    if how == "year":
        return "k. A." if value is None else str(int(value))
    if how == "list":
        items = list(value or [])
        return ", ".join(str(i) for i in items) if items else "k. A."
    return "k. A." if value in (None, "") else str(value)


def fact_rows(prop: Property) -> list[dict[str, Any]]:
    evidence = prop.evidence or {}
    rows: list[dict[str, Any]] = []
    for attribute, label, how in FACT_FIELDS:
        raw = getattr(prop, attribute, None)
        rows.append(
            {
                "key": attribute,
                "label": label,
                "value": _format(raw, how),
                "known": raw not in (None, "", [], {}),
                "evidence": evidence.get(attribute),
            }
        )
    return rows


def flatten_breakdown(breakdown: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Turn ``Score.breakdown`` into flat, printable ``(Pfad, Wert)`` rows."""
    rows: list[dict[str, Any]] = []
    if isinstance(breakdown, dict):
        for key, value in breakdown.items():
            path = f"{prefix} › {key}" if prefix else str(key)
            rows.extend(flatten_breakdown(value, path))
    elif isinstance(breakdown, list | tuple):
        if all(not isinstance(v, dict | list | tuple) for v in breakdown):
            rows.append({"path": prefix, "value": ", ".join(str(v) for v in breakdown)})
        else:
            for index, value in enumerate(breakdown, start=1):
                rows.extend(flatten_breakdown(value, f"{prefix} [{index}]"))
    else:
        rows.append({"path": prefix or "Wert", "value": breakdown})
    return rows


def _score_for(prop: Property, profile_hash: str):
    scores = list(prop.scores or [])
    for score in scores:
        if score.profile_hash == profile_hash:
            return score
    return scores[0] if scores else None


def _context(request: Request, session: Session, prop: Property) -> dict[str, Any]:
    profile = profile_from_query(request.query_params, session=session)
    score = _score_for(prop, profile.profile_hash)
    return {
        "prop": prop,
        "profile": profile,
        "score": score,
        "score_is_current": score is not None and score.profile_hash == profile.profile_hash,
        "cost": prop.cost_estimate,
        "facts": fact_rows(prop),
        "breakdown_rows": flatten_breakdown((score.breakdown if score else {}) or {}),
        "cost_rows": flatten_breakdown((prop.cost_estimate.breakdown if prop.cost_estimate else {}) or {}),
        "timeline": history.timeline(prop),
        "timeline_sentence": history.timeline_sentence(prop),
        "sparkline": sparkline(prop),
        "chips": change_chips(prop),
        "best_url": best_url(prop),
        "user_states": USER_STATES,
        "sources": sorted(
            prop.property_sources or [], key=lambda s: (not s.is_best, not s.is_primary_source)
        ),
        "documents": list(prop.documents or []),
        "images": list(prop.images or []),
    }


@router.get("/property/{public_id}")
def dossier(public_id: str, request: Request, session: Session = Depends(get_db)):
    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    return render(request, "pages/dossier.html", _context(request, session, prop))


@router.post("/property/{public_id}/triage")
def triage(
    public_id: str,
    request: Request,
    session: Session = Depends(get_db),
    user_state: str = Form(default="none"),
    user_note: str = Form(default=""),
):
    """Human judgement. Survives every re-run and every profile change."""
    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    state = (user_state or "none").strip().lower()
    if state not in USER_STATES:
        state = "none"
    prop.user_state = None if state == "none" else state
    prop.user_note = (user_note or "").strip() or None
    session.add(prop)
    session.commit()
    session.refresh(prop)
    return render(
        request,
        "partials/triage.html",
        {"prop": prop, "user_states": USER_STATES, "saved": True},
    )


@router.get("/api/property/{public_id}.json")
def api_property(public_id: str, request: Request, session: Session = Depends(get_db)):
    prop = load_property(session, public_id)
    if prop is None:
        return JSONResponse({"error": "not_found", "public_id": public_id}, status_code=404)
    profile = profile_from_query(request.query_params, session=session)
    score = _score_for(prop, profile.profile_hash)

    from hofradar.web.query import ResultRow

    row = ResultRow(
        rank=0,
        prop=prop,
        score=score,
        cost=prop.cost_estimate,
        chips=change_chips(prop),
        best_url=best_url(prop),
        price_delta_pct=history.total_price_delta_pct(prop),
    )
    payload = row_to_dict(row)
    payload["evidence"] = prop.evidence or {}
    payload["user_note"] = prop.user_note
    payload["timeline"] = [
        {
            "at": event["at"].isoformat() if event["at"] else None,
            "kind": event["kind"],
            "text": event["text"],
        }
        for event in history.timeline(prop)
    ]
    payload["sources"] = [
        {
            "url": s.url,
            "role": s.role,
            "is_primary_source": s.is_primary_source,
            "is_best": s.is_best,
            "contact_name": s.contact_name,
            "contact_kind": s.contact_kind,
        }
        for s in prop.property_sources or []
    ]
    payload["documents"] = [
        {
            "title": d.title,
            "kind": d.kind,
            "issue": d.issue,
            "page_number": d.page_number,
            "url": d.document_url,
            "matched_text": d.matched_text,
        }
        for d in prop.documents or []
    ]
    return JSONResponse(payload)
