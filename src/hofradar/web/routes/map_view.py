"""The map.

Leaflet is vendored, so the page works on a train. Tiles come from the network
and may not; the template therefore always renders the same properties as a
plain list underneath, and the map is an enhancement on top of it.

Geo precision is drawn honestly: an ``exact`` geocode gets a filled pin, a
``town``/``postcode``/``none`` geocode gets a hollow one. Showing a hollow pin
at the village centre is a claim about the village; a filled pin there would be
a claim about the farm, and we do not have that.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from hofradar.web.deps import get_db, has_control_params, redirect_to_saved, remember_query, render
from hofradar.web.query import row_to_dict
from hofradar.web.routes.radar import resolve, result_context

router = APIRouter(tags=["map"])

PRECISE = ("exact", "street")


@router.get("/map")
def map_page(request: Request, session: Session = Depends(get_db)):
    redirect = redirect_to_saved(request)
    if redirect is not None:
        return redirect
    results = resolve(request, session)
    points = []
    without_coordinates = []
    for row in results.rows:
        payload = row_to_dict(row)
        if payload["lat"] is None or payload["lon"] is None:
            without_coordinates.append(row)
            continue
        payload["precise"] = payload["geo_precision"] in PRECISE
        points.append(payload)

    context = result_context(request, results)
    context.update(
        {
            "points": points,
            "without_coordinates": without_coordinates,
            "center": results.profile.center,
            "radius_km": results.profile.radius.air_km_max,
        }
    )
    response = render(request, "pages/map.html", context)
    if has_control_params(request.query_params):
        remember_query(response, results)
    return response
