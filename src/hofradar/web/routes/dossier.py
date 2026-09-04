"""The Dossier - one property, and why we believe every word of it.

Every fact on this page is printed next to its evidence (source, quote, link).
A fact with no evidence is shown *and marked* rather than hidden: "we do not
know where this claim came from" is itself information the buyer needs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from hofradar.costmodel import renovation_evidence
from hofradar.db.models import Property
from hofradar.web import history
from hofradar.web.charts import sparkline
from hofradar.web.deps import get_db, profile_from_query, render
from hofradar.web.filters import de_eur, de_km, de_number, de_sqm, de_tier
from hofradar.web.query import best_url, change_chips, load_property, row_to_dict

router = APIRouter(tags=["dossier"])

#: ``user_state`` values the triage control accepts. "none" clears the flag.
#: "rejected" is a verdict about the farm and keeps it on the radar; only the
#: states in :data:`~hofradar.db.enums.HIDDEN_USER_STATES` take it off the
#: screen, and the labels have to say which is which - one German word for both
#: is what GitHub issue #9 reported. See docs/DECISIONS.md entry 20.
#: There is no "shortlist" entry any more: the Merkliste (``Property.
#: shortlisted_at``, the ``/merken`` route) replaced it. It was orthogonal to
#: triage - a Kontaktiert farm can stay on the list - and collided with the
#: same one-word-two-facts lesson issue #9 already taught this file.
USER_STATES: dict[str, str] = {
    "watch": "👀 Beobachten",
    "contacted": "📞 Kontaktiert",
    "rejected": "🚫 Abgelehnt (bleibt sichtbar)",
    "archived": "📦 Archiviert – nicht mehr anzeigen",
    "none": "– kein Status",
}

#: A form rendered before the Merkliste existed still posts this. Honoured as
#: "put it on the Merkliste" rather than silently dropped - see decision 20 /
#: docs/superpowers/specs/2026-09-04-ui-refinements-design.md section 2.
LEGACY_SHORTLIST_STATE = "shortlist"

#: Cost breakdown keys -> German labels. The table already prints purchase
#: price, acquisition costs and immediate capex from ``CostEstimate`` columns
#: directly, so those three keys are in :data:`SKIP_COST_KEYS` instead.
COST_LABELS: dict[str, str] = {
    "purchase": "Kaufpreis",
    "acquisition": "Erwerbsnebenkosten",
    "house": "Haus (Mitte)",
    "house_low": "Haus (niedrig)",
    "house_high": "Haus (hoch)",
    "roof": "Dach",
    "outbuildings": "Nebengebäude",
    "utilities": "Haustechnik",
    "contingency": "Puffer (Mitte)",
    "contingency_low": "Puffer (niedrig)",
    "contingency_high": "Puffer (hoch)",
    "immediate_capex": "Sofortmaßnahmen",
    "living_sqm_used": "Wohnfläche angesetzt",
    "roof_sqm_used": "Dachfläche angesetzt",
    "outbuilding_sqm_used": "Nebengebäudefläche angesetzt",
    "rate_per_sqm_low": "Satz €/m² (niedrig)",
    "rate_per_sqm_mid": "Satz €/m² (Mitte)",
    "rate_per_sqm_high": "Satz €/m² (hoch)",
}
#: Per-tag outbuilding area keys (``outbuilding_sqm_<tag>``) are not enumerable
#: up front - the tag list comes from the listing - so they get a generic label.
OUTBUILDING_SQM_PREFIX = "outbuilding_sqm_"
SQM_KEYS = frozenset({"living_sqm_used", "roof_sqm_used", "outbuilding_sqm_used"})
RATE_KEYS = frozenset({"rate_per_sqm_low", "rate_per_sqm_mid", "rate_per_sqm_high"})
#: Already printed as their own table row from the ``CostEstimate`` columns.
SKIP_COST_KEYS = frozenset({"purchase", "acquisition", "immediate_capex"})

#: Score breakdown path segments -> German labels. A ``_score`` suffix is
#: dropped before lookup and a ``_max`` suffix becomes " (max.)" - see
#: :func:`score_label`. A segment missing here passes through unchanged: a
#: label map must never hide a key the scorer actually wrote.
SCORE_LABELS: dict[str, str] = {
    "fit": "Passung",
    "deal": "Preis-Leistung",
    "hidden": "Verborgenheit",
    "freshness": "Frische",
    "confidence": "Belastbarkeit",
    "geography": "Lage",
    "price": "Preis",
    "land": "Grund",
    "substance": "Substanz",
    "seclusion": "Alleinlage",
    "development": "Entwicklung",
    "outbuildings": "Nebengebäude",
}

CAPITAL_RISK_LABELS: dict[str, str] = {
    "low": "gering",
    "moderate": "mäßig",
    "high": "hoch",
    "extreme": "extrem",
}

#: Whether the renovation tier came from the listing or was guessed from the
#: year of construction - see :func:`hofradar.costmodel.renovation_evidence`.
EVIDENCE_LABELS: dict[str, str] = {
    "observed": "laut Inserat",
    "inferred": "aus Baujahr geschätzt",
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


def _cost_value(key: str, value: Any) -> str:
    if key in SQM_KEYS:
        return de_sqm(value)
    if key in RATE_KEYS:
        return f"{de_number(value, 0)} €/m²"
    return de_eur(value)


def cost_rows(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    """Label and format ``CostEstimate.breakdown`` for the Kostenmodell table.

    The three keys the table already prints from their own ``CostEstimate``
    columns (purchase price, acquisition costs, immediate capex) are skipped -
    repeating them here would just be the same number twice.
    """
    rows: list[dict[str, Any]] = []
    for key, value in breakdown.items():
        if key in SKIP_COST_KEYS:
            continue
        if key.startswith(OUTBUILDING_SQM_PREFIX):
            tag = key[len(OUTBUILDING_SQM_PREFIX) :]
            rows.append({"path": f"Fläche {tag}", "value": de_sqm(value)})
            continue
        label = COST_LABELS.get(key, key)
        rows.append({"path": label, "value": _cost_value(key, value)})
    return rows


def score_label(segment: str) -> str:
    """One path segment of ``Score.breakdown`` -> German. Unknown -> itself."""
    if segment.endswith("_max"):
        stem = segment[: -len("_max")]
        return f"{SCORE_LABELS.get(stem, stem)} (max.)"
    if segment.endswith("_score"):
        stem = segment[: -len("_score")]
        return SCORE_LABELS.get(stem, stem)
    return SCORE_LABELS.get(segment, segment)


def score_rows(breakdown: Any) -> list[dict[str, Any]]:
    """``Score.breakdown`` flattened and labelled, path segments in German."""
    rows: list[dict[str, Any]] = []
    for row in flatten_breakdown(breakdown):
        path = row["path"]
        segments = path.split(" › ") if path else []
        labelled = " › ".join(score_label(segment) for segment in segments) if segments else path
        rows.append({"path": labelled, "value": row["value"]})
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
    cost = prop.cost_estimate
    return {
        "prop": prop,
        "profile": profile,
        "score": score,
        "score_is_current": score is not None and score.profile_hash == profile.profile_hash,
        "cost": cost,
        "facts": fact_rows(prop),
        "breakdown_rows": score_rows((score.breakdown if score else {}) or {}),
        "cost_rows": cost_rows((cost.breakdown if cost else {}) or {}),
        "renovation_tier_label": de_tier(cost.renovation_tier) if cost else None,
        "renovation_basis": EVIDENCE_LABELS.get(renovation_evidence(prop), ""),
        "capital_risk_label": (
            CAPITAL_RISK_LABELS.get(score.capital_risk, score.capital_risk) if score else None
        ),
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
    legacy_marked = False
    if state == LEGACY_SHORTLIST_STATE:
        # A form rendered before the Merkliste existed. Honour the intent.
        if prop.shortlisted_at is None:
            prop.shortlisted_at = history.now_utc()
        state = "none"
        legacy_marked = True
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
        {"prop": prop, "user_states": USER_STATES, "saved": True, "legacy_marked": legacy_marked},
    )


@router.post("/property/{public_id}/merken")
def merken(public_id: str, request: Request, session: Session = Depends(get_db)):
    """The Merkliste toggle. The only route that writes ``Property.
    shortlisted_at`` on a reader's action - the legacy-triage branch above and
    ``dedupe.merge`` also set it (docs/DECISIONS.md entry 21)."""
    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    prop.shortlisted_at = None if prop.shortlisted_at else history.now_utc()
    session.add(prop)
    session.commit()
    session.refresh(prop)
    if request.headers.get("HX-Request"):
        return render(request, "partials/merken_button.html", {"prop": prop})
    return RedirectResponse(f"/property/{prop.public_id}", status_code=303)


@router.post("/property/{public_id}/delete")
def delete(
    public_id: str,
    request: Request,
    session: Session = Depends(get_db),
    confirm_public_id: str = Form(default=""),
):
    """The narrow escape hatch: a mis-crawl or a duplicate, gone for good.

    Triage's 📦 *Archiviert* is the answer to "get this off my radar"; this is
    the answer to "this was never a property". It takes the ``public_id`` typed
    back by hand because it destroys append-only history that no source can
    re-supply, and it refuses outright while another row was merged into this
    one - deleting the survivor of a merge would resurrect the duplicate.
    """
    from hofradar.db.backup import BackupUnavailable
    from hofradar.lifecycle import ResurrectsMergedDuplicates, delete_property

    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    if (confirm_public_id or "").strip() != prop.public_id:
        return render(
            request,
            "pages/error.html",
            {
                "code": 400,
                "message": (
                    f"Zum Löschen muss die ID {prop.public_id} genau eingetippt werden. "
                    "Es wurde nichts gelöscht."
                ),
            },
            status_code=400,
        )
    try:
        delete_property(session, prop)
    except ResurrectsMergedDuplicates as exc:
        return render(
            request,
            "pages/error.html",
            {"code": 409, "message": f"Nicht gelöscht: {exc}"},
            status_code=409,
        )
    except BackupUnavailable as exc:
        return render(
            request,
            "pages/error.html",
            {"code": 409, "message": f"Nicht gelöscht, keine Sicherung möglich: {exc}"},
            status_code=409,
        )
    if request.headers.get("HX-Request"):
        # HTMX follows a 303 itself and would swap the radar into the dossier.
        return Response(status_code=204, headers={"HX-Redirect": "/"})
    return RedirectResponse("/", status_code=303)


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
