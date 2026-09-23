"""The reader's name for a farm is a label, not evidence - it never moves a score.

Scoring reads the advert's prose (``text_blob``) because a Baurecht reference
or an "Alleinlage" is quoted there. ``user_title`` is whatever the reader typed
into the heading: call a plain house "Vierseithof in Alleinlage mit Stadel" and
nothing about the house has changed. If the name leaked into the blob, renaming
a property would buy it substance, seclusion and development points.
"""

from __future__ import annotations

from typing import Any

import pytest
from factories import make_property

from hofradar.config import SearchProfile
from hofradar.scoring import fit_score
from hofradar.scoring._util import text_blob

#: Every word in it scores somewhere if it reaches the blob.
_LOADED_NAME = "Vierseithof in Alleinlage mit Stadel, Stall und Baurecht"


@pytest.fixture()
def profile() -> SearchProfile:
    return SearchProfile()


def plain(**kwargs: Any):
    """A property whose listing says nothing a score would reward."""
    defaults: dict[str, Any] = {
        "canonical_title": "Anwesen",
        "description": "Objekt in Oberbayern.",
        "property_type": None,
        "building_features": [],
        "outbuildings": [],
        "special_features": [],
        "year_built": 1980,
        "is_monument": False,
    }
    defaults.update(kwargs)
    return make_property(**defaults)


def test_text_blob_reads_the_listing_title_not_the_readers_name() -> None:
    prop = plain(canonical_title="Anwesen am Ortsrand", user_title="Moarhof")

    blob = text_blob(prop)

    assert "anwesen am ortsrand" in blob
    assert "moarhof" not in blob


def test_a_loaded_name_leaves_the_fit_score_unchanged(profile: SearchProfile) -> None:
    unnamed_score, unnamed = fit_score(plain(user_title=None), profile)
    named_score, named = fit_score(plain(user_title=_LOADED_NAME), profile)

    assert named_score == unnamed_score
    assert named["substance_matches"] == unnamed["substance_matches"] == []
    assert named["seclusion_class"] == unnamed["seclusion_class"]
    assert named["development_basis"] == unnamed["development_basis"] == "unclear"
    assert named["outbuilding_types"] == unnamed["outbuilding_types"] == []
