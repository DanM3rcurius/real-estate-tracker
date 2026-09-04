"""Whether the renovation figure rests on evidence or on the age fallback.

The distinction is load-bearing: a cost derived from "pre-1960 and nobody said
anything" must not be allowed to hard-reject a property, because it is a
default, not a measurement.

Placed beside ``test_costmodel.py`` rather than under a new ``tests/costmodel/``
package: ``make_property`` here builds a bare, unpersisted ``Property`` (no
session needed), and this directory's ``factories`` module is already on
``sys.path`` for every test file that lives in it, exactly as ``test_costmodel.py``
already relies on.
"""

from __future__ import annotations

from factories import make_property

from hofradar.costmodel import renovation_evidence


def test_stated_condition_counts_as_observed() -> None:
    prop = make_property(condition="sanierungsbeduerftig", year_built=1890)
    assert renovation_evidence(prop) == "observed"


def test_condition_tag_counts_as_observed() -> None:
    prop = make_property(building_features=["kernsanierung"], year_built=1890)
    assert renovation_evidence(prop) == "observed"


def test_age_only_fallback_counts_as_inferred() -> None:
    prop = make_property(year_built=1890)
    assert renovation_evidence(prop) == "inferred"


def test_nothing_at_all_counts_as_inferred() -> None:
    prop = make_property()
    assert renovation_evidence(prop) == "inferred"
