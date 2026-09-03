"""A source may not be enabled until somebody has read its terms.

The failure this prevents: an adapter written, a scoring abstraction designed
around it, and only then the discovery that the site's terms forbid automated
access - at which point the abstraction has no producer.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from hofradar.config import SourceConfig


def test_enabled_source_without_terms_check_is_rejected() -> None:
    with pytest.raises(ValidationError, match="terms_checked_at"):
        SourceConfig(key="x", name="X", enabled=True, adapter="manual")


def test_enabled_source_with_terms_check_is_accepted() -> None:
    source = SourceConfig(
        key="x",
        name="X",
        enabled=True,
        adapter="manual",
        terms_checked_at=date(2026, 9, 3),
        terms_excerpt="robots.txt: no Disallow for /objekte/. No AGB clause on automated access.",
    )
    assert source.terms_checked_at == date(2026, 9, 3)


def test_disabled_source_needs_no_terms_check() -> None:
    source = SourceConfig(key="x", name="X", enabled=False, adapter="manual")
    assert source.terms_checked_at is None
