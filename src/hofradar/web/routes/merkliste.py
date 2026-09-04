"""The Merkliste - what the reader marked, under the reader's own sliders.

It is the human's list, so the score gate does not apply (``include_rejected``
is forced on); archiving still hides, and still counts, exactly as on the
radar. The saved *view* filters are deliberately not applied: a remembered
search for one village must not empty a list of farms in another.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from hofradar.web.deps import (
    filters_from_query,
    get_db,
    profile_from_query,
    render,
    saved_profile_params,
)
from hofradar.web.query import build_results
from hofradar.web.routes.radar import result_context

router = APIRouter(tags=["merkliste"])


@router.get("/merkliste")
def merkliste(request: Request, session: Session = Depends(get_db)):
    params = dict(request.query_params) or saved_profile_params(request)
    profile = profile_from_query(params, session=session)
    filters = filters_from_query({})
    filters.shortlisted_only = True
    filters.include_rejected = True
    results = build_results(session, profile, filters)
    return render(request, "pages/merkliste.html", result_context(request, results))
