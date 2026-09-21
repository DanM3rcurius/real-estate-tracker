"""The backtest's pure parts: grouping, the listing it rebuilds, the sweep table.

The script is not a package, so it is loaded from its path. The network half
is the same ``JevTriage`` the crawl uses and is covered in ``test_jev.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from hofradar.db.enums import PriceType
from hofradar.db.models import Observation, Property
from hofradar.triage import TriageVerdict

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backtest_triage.py"
_spec = importlib.util.spec_from_file_location("backtest_triage", _SCRIPT)
backtest = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# A slots dataclass resolves its annotations through sys.modules at class
# creation, so a path-loaded module has to be registered before it executes.
sys.modules[_spec.name] = backtest
_spec.loader.exec_module(backtest)


def _prop(**overrides) -> Property:
    defaults = dict(
        public_id="hof-x",
        canonical_title="Hofstelle",
        description="Eigene Beschreibung.",
        town="Aying",
        rooms=6.0,
        living_sqm=150.0,
        land_sqm=4000.0,
        outbuildings=["stadel"],
        price_type=PriceType.ASKING,
        evidence={},
    )
    defaults.update(overrides)
    return Property(**defaults)


def test_every_row_lands_in_exactly_one_group_first_match_wins() -> None:
    now = datetime.now(UTC)
    assert backtest.label_group(_prop(shortlisted_at=now, user_state="archived")) == "merkliste"
    assert backtest.label_group(_prop(user_state="watch")) == "beobachtet"
    assert backtest.label_group(_prop(user_state="contacted")) == "beobachtet"
    assert backtest.label_group(_prop(user_state="rejected")) == "abgelehnt"
    assert backtest.label_group(_prop(user_state="archived")) == "archiviert"
    assert backtest.label_group(_prop(price_type=PriceType.RENT)) == "miete_regex"
    assert backtest.label_group(_prop()) == "unbewertet"


def test_the_rebuilt_listing_prefers_the_observation_text_and_raw_price() -> None:
    prop = _prop()
    observation = Observation(
        property_id=1,
        source_id=1,
        url="https://x.invalid/1",
        title="t",
        description="Der volle Pastetext mit Kaltmiete 1.200 €.",
        price_raw="Kaltmiete 1.200 €",
    )
    listing = backtest.listing_from_property(prop, observation)
    assert listing.description == observation.description
    assert listing.price_raw == "Kaltmiete 1.200 €"
    assert listing.url == "https://x.invalid/1"
    assert listing.outbuildings == ["stadel"]

    bare = backtest.listing_from_property(prop, None)
    assert bare.description == "Eigene Beschreibung."
    assert bare.price_raw is None
    assert bare.url == "hof-x"


def _verdict(p_rent: float, p_flat: float = 0.0) -> TriageVerdict:
    return TriageVerdict(
        model="jev-test",
        offer_kind="miete" if p_rent >= 0.5 else "kauf",
        offer_probabilities={"kauf": 1 - p_rent, "miete": p_rent},
        dwelling_kind="wohnung" if p_flat >= 0.5 else "hofstelle",
        dwelling_probabilities={"hofstelle": 1 - p_flat, "wohnung": p_flat},
    )


def test_sweep_counts_rejects_and_flags_per_group_and_threshold() -> None:
    rows = [
        backtest.Row("a", "merkliste", True, _verdict(0.1)),
        backtest.Row("b", "archiviert", False, _verdict(0.9)),
        backtest.Row("c", "archiviert", False, _verdict(0.7)),
        backtest.Row("d", "archiviert", True, _verdict(0.0, p_flat=0.95)),
    ]
    table = backtest.sweep(rows, (0.6, 0.85))

    at_60 = table[0.6]
    assert at_60["merkliste"] == Counter({"n": 1})
    assert at_60["archiviert"]["reject_miete"] == 2
    # Substance turns the flat reject into a flag at every threshold.
    assert at_60["archiviert"]["reject_wohnung"] == 0
    assert at_60["archiviert"]["flag"] == 1

    at_85 = table[0.85]
    assert at_85["archiviert"]["reject_miete"] == 1
    assert at_85["archiviert"]["flag"] == 2  # the 0.7 rental and the flat

    text = backtest.render_table(table, current=0.85)
    assert "merkliste" in text and "archiviert" in text
    assert "<- konfiguriert" in text.splitlines()[-1]
