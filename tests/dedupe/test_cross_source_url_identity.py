"""One portal reached twice must not become two properties.

``generic_rss``'s four Atom feeds and the dedicated ``ovbimmo`` adapter both
resolve to the same host, and the two committed captures contain the *same*
canonical URL for the same advert. Before this rule, dedupe could not join
them: both candidate-retrieval passes filtered on ``Source.key``, and
``compare_facts``' only cross-source proofs were ``(source_key, external_id)``
equality - one source's private numbering, never shared - and a perceptual
image hash that no adapter or normalizer populates. The measured verdict was
``is_duplicate=False, confidence 0.22, corroborating_dimensions: 0``, so the
same advert became two properties: two shortlist entries, an inflated
``tracked_total``, and a yield table in which two rows count one physical
inventory.

The rule added is narrow: an identical *canonical listing URL* is proof of
identity across sources, because a URL names one page on one host. What is
normalised away before comparing is only what provably cannot select different
content - see ``hofradar.dedupe._util.canonical_url``. This file pins both
directions, because the dangerous half is the other one: over-normalising
would fuse two different farmsteads into one property and destroy both their
histories.
"""

from __future__ import annotations

import pytest

from hofradar.db.enums import SourceRole
from hofradar.db.models import Property, PropertySource
from hofradar.dedupe import compare
from hofradar.dedupe._util import canonical_url
from hofradar.lifecycle import ingest

#: The listing that genuinely appears in both committed captures - the Atom
#: feed's <link> and the search page's href resolve to the same string.
SHARED_URL = (
    "https://ovbimmo.de/immobilien/"
    "zweifamilienhaus-grosskarolinenfeld-grosser-sonniger-garten-H3N33B"
)
#: A different advert on the same portal: different slug, different 6-char id.
OTHER_URL = (
    "https://ovbimmo.de/immobilien/"
    "interessante-wertanlage-bad-endorf-charmantes-apartment-mit-serioesem-mieter-GZJFXJ"
)


def test_the_same_listing_from_two_sources_is_proof(make_listing):
    """The measured failure, inverted. Deliberately thin on both sides - no
    price, no areas, no coordinates - because that is the shape a feed entry
    actually has, and it is exactly why the similarity model could not reach
    a verdict on its own."""
    from_feed = make_listing(
        source_key="generic_rss",
        url=SHARED_URL,
        title="Zweifamilienhaus! Großkarolinenfeld! Großer sonniger Garten!",
        external_id=SHARED_URL,
    )
    from_adapter = make_listing(
        source_key="ovbimmo",
        url=SHARED_URL,
        title="Zweifamilienhaus Großkarolinenfeld",
        external_id="H3N33B",
    )

    verdict = compare(from_feed, from_adapter)

    assert verdict.is_duplicate is True
    assert verdict.confidence == 1.0
    assert any("same_canonical_url" in reason for reason in verdict.reasons)


def test_two_different_listings_on_one_portal_never_collide(make_listing):
    """The direction that must not be sacrificed for the one above."""
    a = make_listing(source_key="ovbimmo", url=SHARED_URL, title="Haus in Großkarolinenfeld")
    b = make_listing(source_key="generic_rss", url=OTHER_URL, title="Haus in Bad Endorf")

    verdict = compare(a, b)

    assert verdict.is_duplicate is False
    assert not any("same_canonical_url" in reason for reason in verdict.reasons)


@pytest.mark.parametrize(
    "variant",
    [
        SHARED_URL + "/",
        SHARED_URL + "#exposé",
        SHARED_URL + "?utm_source=newsletter&utm_medium=email",
        SHARED_URL + "?gclid=abc123",
        SHARED_URL.replace("https://", "http://"),
        SHARED_URL.replace("https://ovbimmo.de", "https://www.ovbimmo.de"),
        SHARED_URL.replace("https://ovbimmo.de", "https://ovbimmo.de:443"),
        SHARED_URL.replace("https://ovbimmo.de", "https://OVBimmo.DE"),
    ],
)
def test_cosmetic_url_differences_still_name_one_listing(variant):
    assert canonical_url(variant) == canonical_url(SHARED_URL)


@pytest.mark.parametrize(
    "other",
    [
        OTHER_URL,
        # A different host is a different site, whatever the path says.
        SHARED_URL.replace("ovbimmo.de", "immowelt.de"),
        # An unrecognised query parameter may well be what selects the
        # listing, so it is kept rather than guessed away.
        SHARED_URL + "?id=1",
        # ...and two values of one are two listings.
        SHARED_URL + "?ref=partner-a",
    ],
)
def test_meaningful_url_differences_are_kept(other):
    assert canonical_url(other) != canonical_url(SHARED_URL)


def test_a_url_without_a_host_is_never_an_identity():
    """A bare path or a pasted scrap of text must not match another one."""
    assert canonical_url("/immobilien/some-slug-H3N33B") is None
    assert canonical_url("   ") is None
    assert canonical_url(None) is None


def test_two_routes_into_one_portal_ingest_as_one_property(
    db_session, make_source, make_listing
):
    """End to end through the real ingest path, at the level the defect bites:
    two Source rows, one Property, two PropertySource rows - not two properties
    double-counting one advert in ``tracked_total`` and the yield table."""
    feed = make_source(key="generic_rss", role=SourceRole.LOCAL, reliability=0.75)
    portal = make_source(key="ovbimmo", role=SourceRole.LOCAL, reliability=0.75)

    first, _ = ingest(
        db_session,
        make_listing(
            source_key="generic_rss",
            url=SHARED_URL,
            title="Zweifamilienhaus! Großkarolinenfeld! Großer sonniger Garten!",
            external_id=SHARED_URL,
            town="Großkarolinenfeld",
            postcode="83109",
        ),
        source=feed,
        run_id=1,
    )
    second, change = ingest(
        db_session,
        make_listing(
            source_key="ovbimmo",
            url=SHARED_URL,
            title="Zweifamilienhaus Großkarolinenfeld",
            external_id="H3N33B",
            town="Großkarolinenfeld",
            postcode="83109",
        ),
        source=portal,
        run_id=1,
    )

    assert second.id == first.id
    assert db_session.query(Property).count() == 1
    assert db_session.query(PropertySource).filter_by(property_id=first.id).count() == 2
    assert change.kind != "first_seen"
