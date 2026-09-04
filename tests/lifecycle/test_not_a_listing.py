"""Ingest refuses a page that is not a listing, and refuses it writing nothing.

Issue #10: a portal's bookmark widget reached ``ingest`` and came out the
other side as a Property with a public_id, a geocode and a score. The refusal
has to happen *before* ``find_duplicate`` and before the observation, because
an Observation is the record of a source showing us a listing - and a search
page or a login form never was one. Invariant 1 is unaffected: ingest is
still the only writer of Property rows; it simply declines to be one here.
"""

from __future__ import annotations

import pytest

from hofradar.contracts import PAGE_KIND_INDEX, PAGE_KIND_UTILITY
from hofradar.db.models import Observation, Property, PropertySource, StatusHistory
from hofradar.lifecycle import NotAListing, ingest


@pytest.mark.parametrize("kind", [PAGE_KIND_INDEX, PAGE_KIND_UTILITY])
def test_a_non_listing_page_is_refused(db_session, make_source, make_listing, kind: str) -> None:
    source = make_source("ovbimmo")
    listing = make_listing(source_key=source.key, page_kind=kind)

    with pytest.raises(NotAListing) as excinfo:
        ingest(db_session, listing, source=source, run_id=1)

    assert excinfo.value.page_kind == kind
    assert excinfo.value.url == listing.url
    assert excinfo.value.reason


def test_a_refusal_leaves_nothing_behind(db_session, make_source, make_listing) -> None:
    """Not even the append-only observation: the observations table is the
    history of *listings*, and admitting a portal index page there would make
    every count and every yield statistic read from it a lie."""
    source = make_source("ovbimmo")
    listing = make_listing(source_key=source.key, page_kind=PAGE_KIND_INDEX)

    with pytest.raises(NotAListing):
        ingest(db_session, listing, source=source, run_id=1)

    assert db_session.query(Property).count() == 0
    assert db_session.query(Observation).count() == 0
    assert db_session.query(PropertySource).count() == 0
    assert db_session.query(StatusHistory).count() == 0


def test_a_refused_page_cannot_touch_the_property_it_was_pasted_over(
    db_session, make_source, make_listing
) -> None:
    """A second paste of the same URL, this time of the portal's search page,
    must not update the property that URL already produced - a known row is
    exactly what an over-eager fact merge would corrupt."""
    source = make_source("ovbimmo")
    good = make_listing(source_key=source.key, title="Vierseithof bei Wasserburg")
    prop, _ = ingest(db_session, good, source=source, run_id=1)
    observations_before = db_session.query(Observation).count()

    chimera = make_listing(
        source_key=source.key,
        url=good.url,
        title="Immobilien in Rosenheim (Kreis) kaufen",
        page_kind=PAGE_KIND_INDEX,
    )
    with pytest.raises(NotAListing):
        ingest(db_session, chimera, source=source, run_id=2)

    assert prop.canonical_title == "Vierseithof bei Wasserburg"
    assert db_session.query(Property).count() == 1
    assert db_session.query(Observation).count() == observations_before
