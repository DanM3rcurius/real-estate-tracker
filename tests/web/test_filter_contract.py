"""The control panel and the ranking engine must use the same filter words.

``ResultFilters.as_scoring_filters()`` emitted three keys ``SUPPORTED_FILTERS``
had never heard of, so ``_apply_filters`` raised ``ValueError``, ``lazy.call_or``
turned that into "Modul Bewertung nicht verfügbar", and the radar dropped to its
unscored fallback - silently, every time one of those three controls was used.
A subset assertion is the cheapest thing that would have caught it.
"""

from __future__ import annotations

from hofradar.scoring import rescore_all
from hofradar.scoring.engine import SUPPORTED_FILTERS, ranked_properties
from hofradar.web.deps import ResultFilters

#: Every control the panel can send at once, so no key escapes the check.
EVERY_CONTROL = ResultFilters(
    min_land_sqm=1_000.0,
    status="alive",
    verified_only=True,
    outbuildings_only=True,
    town="Bad Feilnbach",
    user_state="shortlist",
    include_rejected=True,
    include_hidden=True,
)


def test_every_key_the_control_panel_emits_is_supported():
    assert set(EVERY_CONTROL.as_scoring_filters()) <= SUPPORTED_FILTERS


def test_ranked_properties_accepts_the_whole_control_panel(db, seeded, default_profile):
    rescore_all(db, default_profile, only_dirty=False)
    ranked_properties(db, default_profile, filters=EVERY_CONTROL.as_scoring_filters())


def test_the_three_new_filters_actually_filter(db, seeded, default_profile):
    rescore_all(db, default_profile, only_dirty=False)

    def ids(**payload):
        return {
            prop.public_id
            for prop, _score in ranked_properties(
                db, default_profile, include_rejected=True, filters=payload
            )
        }

    assert ids(min_land_sqm=8_000.0) == {"HF-0001", "HF-0002"}
    assert "HF-0003" not in ids(verified_only=True)
    assert "HF-0004" not in ids(has_outbuildings=True)


def test_using_those_controls_does_not_degrade_the_radar(client, seeded):
    response = client.get(
        "/api/properties.json?verified_only=1&outbuildings_only=1&min_land_sqm=1000"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["degraded"] == []
    # The fallback path cannot score, so a scored row proves the engine answered.
    assert payload["properties"]
    assert payload["properties"][0]["scores"] is not None
