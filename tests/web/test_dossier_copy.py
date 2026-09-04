"""The dossier speaks German, names the renovation basis, and folds the noise."""

from __future__ import annotations

from hofradar.web.filters import de_status, de_tier
from tests.web.conftest import add_score


def test_status_and_tier_words():
    assert de_status("discovered") == "Entdeckt"
    assert de_status("unheard_of") == "unheard_of"
    assert de_tier("heavy") == "schwer"
    assert de_tier(None) == "unbekannt"


def test_dossier_cost_section_is_german(client, db, seeded):
    near = seeded["near"]
    cost = near.cost_estimate
    cost.renovation_tier = "heavy"
    cost.breakdown = {
        **cost.breakdown,
        "house_low": 180_000.0,
        "house_high": 240_000.0,
        "rate_per_sqm_low": 1_200.0,
        "rate_per_sqm_mid": 1_500.0,
        "rate_per_sqm_high": 1_800.0,
        "living_sqm_used": 150.0,
    }
    db.add(cost)
    db.commit()

    html = client.get("/property/HF-0001").text
    assert "Sanierungsstufe" in html
    assert "laut Inserat" in html or "aus Baujahr geschätzt" in html
    assert "Renovation tier" not in html
    assert "house_low" not in html and "rate_per_sqm_low" not in html
    assert "Haus (niedrig)" in html


def test_breakdown_is_folded_and_german(client, db, seeded, default_profile):
    near = seeded["near"]
    add_score(
        db,
        near,
        default_profile.profile_hash,
        breakdown={
            "fit": {
                "geography_score": 18,
                "geography_max": 20,
                "price_score": 15,
                "price_max": 20,
            },
            "deal": {"total_budget_ratio": 0.72},
        },
    )

    html = client.get("/property/HF-0001").text
    assert '<details class="fold"' in html
    assert "Punkteaufschlüsselung" in html
    assert "geography_score" not in html
    assert "Lage" in html


def test_status_chip_is_german(client, seeded):
    html = client.get("/").text
    assert "chip--status-active" in html
    assert ">Aktiv<" in html


def test_assumptions_are_written_in_german(db, seeded, default_profile):
    from hofradar.costmodel import estimate_costs

    cost = estimate_costs(seeded["near"], default_profile)
    joined = " ".join(cost.assumptions)
    assert "Sanierungsstufe" in joined
    assert "Renovation" not in joined and "assumed" not in joined
