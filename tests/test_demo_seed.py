"""The synthetic dataset behind the public snapshot.

What is worth pinning here is not the contents of the YAML - that will change -
but the properties the published site depends on: that seeding writes through
``lifecycle.ingest`` rather than around it, that it is idempotent, that it makes
no network call, and that a listing whose road distance was never computed keeps
an unknown road distance all the way to the page.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.config import load_profile
from hofradar.db.enums import SourceRole
from hofradar.db.models import Observation, Property
from hofradar.demo import DEMO_SOURCE_KEY, DemoSeedError, seed_demo, seed_path

SEED_ENTRIES = 12


@pytest.fixture
def demo_source(make_source):
    """The source the seeder attributes every synthetic row to."""
    return make_source(DEMO_SOURCE_KEY, role=SourceRole.PRIMARY, enabled=False)


@pytest.fixture
def profile():
    return load_profile()


def test_seeding_ingests_every_listing(db_session, demo_source, profile):
    count = seed_demo(db_session, profile, rescore=False)

    assert count == SEED_ENTRIES
    assert db_session.query(Property).count() == SEED_ENTRIES
    # ingest writes the observation first, always - one per listing.
    assert db_session.query(Observation).count() == SEED_ENTRIES


def test_seeding_twice_updates_and_never_duplicates(db_session, demo_source, profile):
    """A second seed is a re-crawl of unchanged listings, not a second dataset."""
    seed_demo(db_session, profile, rescore=False)
    seed_demo(db_session, profile, rescore=False)

    assert db_session.query(Property).count() == SEED_ENTRIES
    assert db_session.query(Observation).count() == SEED_ENTRIES * 2


def test_seeding_makes_no_network_call(db_session, demo_source, profile):
    """Coordinates come from the file. Nothing here may geocode or route."""
    with respx.mock(assert_all_called=False) as mock:
        # Any outbound request at all fails the test rather than escaping.
        route = mock.route().mock(side_effect=httpx.ConnectError("no network in seeding"))
        seed_demo(db_session, profile, rescore=False)
        assert not route.called


def test_unknown_road_distance_stays_unknown(db_session, demo_source, profile):
    """Invariant 3: a missing route is None, never the air distance."""
    seed_demo(db_session, profile, rescore=False)

    without_route = [
        p for p in db_session.query(Property).all() if p.distance_driving_km is None
    ]
    assert without_route, "the dataset must keep at least one un-routed listing"
    for prop in without_route:
        # It knows where it is, it just never had a road route computed.
        assert prop.distance_air_km is not None
        assert prop.distance_driving_minutes is None


def test_every_listing_lands_inside_the_configured_radius(db_session, demo_source, profile):
    """A demo whose properties all fail the distance gate would show nothing."""
    seed_demo(db_session, profile, rescore=False)

    for prop in db_session.query(Property).all():
        assert prop.distance_air_km <= profile.radius.air_km_max


def test_facts_are_derived_by_the_normalizer_not_declared(db_session, demo_source, profile):
    """The point of authoring expose text is that the real code reads it."""
    seed_demo(db_session, profile, rescore=False)
    by_town = {p.town: p for p in db_session.query(Property).all()}

    # "Zwangsversteigerung" in the text, nowhere in the YAML as a boolean.
    assert by_town["Dorfen"].is_foreclosure
    # "denkmalgeschützt" / "Denkmalschutz".
    assert by_town["Bad Tölz"].is_monument
    # "Von privat, kein Makler."
    assert by_town["Miesbach"].is_private_seller
    # Classified from the title against the core vocabulary.
    assert by_town["Dorfen"].property_type == "vierseithof"
    # "Kaufpreis auf Anfrage" parses to no price rather than to zero.
    assert by_town["Bad Tölz"].price is None


def test_seeding_refuses_a_file_that_does_not_declare_itself_fictional(
    db_session, demo_source, profile, tmp_path
):
    """The banner and the whole premise rest on this flag. No flag, no seed."""
    sneaky = tmp_path / "listings.yaml"
    sneaky.write_text(
        "meta:\n  fictional: false\nlistings:\n  - url: https://example.invalid/x\n",
        encoding="utf-8",
    )

    with pytest.raises(DemoSeedError, match="fictional"):
        seed_demo(db_session, profile, path=sneaky, rescore=False)


def test_seeding_without_the_source_registered_says_so(db_session, profile):
    with pytest.raises(DemoSeedError, match="init-db"):
        seed_demo(db_session, profile, rescore=False)


def test_the_shipped_dataset_is_found_from_the_repository_root():
    assert seed_path().exists()
