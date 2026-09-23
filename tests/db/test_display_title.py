"""``Property.display_title``: the reader's name when there is one, else the listing's.

Every reader-facing surface - the radar card, the dossier, the digest, the
change feed, the JSON - reads this one property, so its fallback is the rule
they all share. An empty string is "no name", not a name: a heading must never
render blank because a form posted nothing.
"""

from __future__ import annotations

import pytest

from hofradar.db.models import Property


@pytest.mark.parametrize("user_title", [None, ""])
def test_falls_back_to_the_listing_title(user_title: str | None) -> None:
    prop = Property(canonical_title="Hofstelle in Vogtareuth", user_title=user_title)

    assert prop.display_title == "Hofstelle in Vogtareuth"


def test_prefers_the_readers_name() -> None:
    prop = Property(canonical_title="Hofstelle in Vogtareuth", user_title="Moarhof")

    assert prop.display_title == "Moarhof"
    assert prop.canonical_title == "Hofstelle in Vogtareuth"


def test_survives_a_round_trip_through_the_database(db_session, make_property) -> None:
    prop = make_property(canonical_title="Hofstelle in Vogtareuth", user_title="Moarhof")
    db_session.expire_all()

    stored = db_session.get(Property, prop.id)

    assert stored is not None
    assert stored.user_title == "Moarhof"
    assert stored.display_title == "Moarhof"
