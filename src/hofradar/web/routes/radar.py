"""The Radar - the screen the product is.

``GET /`` renders the control panel and the first page of results server-side;
``GET /api/results`` re-renders only the list. Both go through the same
:func:`hofradar.web.query.build_results`, so what HTMX swaps in after a slider
move is byte-for-byte what a full reload would have produced.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from hofradar.web.deps import (
    filters_from_query,
    get_db,
    profile_from_query,
    render,
)
from hofradar.web.query import ResultSet, build_results, row_to_dict

router = APIRouter(tags=["radar"])


def resolve(request: Request, session: Session) -> ResultSet:
    """Query string -> profile + filters -> ranked rows."""
    profile = profile_from_query(request.query_params, session=session)
    filters = filters_from_query(request.query_params)
    return build_results(session, profile, filters)


def result_context(request: Request, results: ResultSet) -> dict[str, Any]:
    return {
        "results": results,
        "profile": results.profile,
        "filters": results.filters,
        "rows": results.rows,
        "query_string": results.filters.query_string(results.profile),
    }


@router.get("/")
def radar(request: Request, session: Session = Depends(get_db)):
    results = resolve(request, session)
    return render(request, "pages/radar.html", result_context(request, results))


@router.get("/api/results")
def api_results(request: Request, session: Session = Depends(get_db)):
    """The HTMX target. Returns the result-list partial, not a full page."""
    results = resolve(request, session)
    return render(request, "partials/results.html", result_context(request, results))


@router.get("/api/properties.json")
def api_properties(request: Request, session: Session = Depends(get_db)) -> JSONResponse:
    results = resolve(request, session)
    return JSONResponse(
        {
            "profile_hash": results.profile_hash,
            "profile": {
                "name": results.profile.name,
                "air_km_max": results.profile.radius.air_km_max,
                "driving_soft_km": results.profile.radius.effective_driving_soft,
                "driving_hard_km": results.profile.radius.effective_driving_hard,
                "total_budget_max": results.profile.budget.total_budget_max,
                "purchase_target_max": results.profile.budget.effective_purchase_target_max,
                "purchase_hard_max": results.profile.budget.effective_purchase_hard_max,
                "center": results.profile.center.model_dump(),
            },
            "rescored": results.rescored,
            "total_matched": results.total_matched,
            "total_in_db": results.total_in_db,
            "degraded": [d.message for d in results.degraded],
            "properties": [row_to_dict(row) for row in results.rows],
        }
    )


CSV_COLUMNS = (
    ("rank", "Rang"),
    ("public_id", "ID"),
    ("title", "Titel"),
    ("town", "Ort"),
    ("postcode", "PLZ"),
    ("distance_air_km", "Luftlinie km"),
    ("distance_driving_km", "Fahrstrecke km"),
    ("price", "Preis EUR"),
    ("price_type", "Preisart"),
    ("land_sqm", "Grund m2"),
    ("living_sqm", "Wohnflaeche m2"),
    ("property_type", "Objektart"),
    ("listing_status", "Status"),
    ("verification_status", "Verifikation"),
    ("user_state", "Triage"),
    ("url", "Quelle"),
)


@router.get("/api/export.csv")
def api_export(request: Request, session: Session = Depends(get_db)) -> Response:
    """CSV of exactly what the current filters show - nothing wider."""
    results = resolve(request, session)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([label for _, label in CSV_COLUMNS] + ["Final", "FIT", "DEAL", "HIDDEN", "FRESH", "CONF"])
    for row in results.rows:
        payload = row_to_dict(row)
        scores = payload.get("scores") or {}
        # A missing road route stays empty in the export; it never inherits the air value.
        writer.writerow(
            [payload.get(key) if payload.get(key) is not None else "" for key, _ in CSV_COLUMNS]
            + [
                scores.get("final", ""),
                scores.get("fit", ""),
                scores.get("deal", ""),
                scores.get("hidden", ""),
                scores.get("freshness", ""),
                scores.get("confidence", ""),
            ]
        )
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="hofradar-{results.profile_hash}.csv"'
        },
    )
