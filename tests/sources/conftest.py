"""Fixtures for the source-layer tests: pytest-asyncio + respx, fully offline.

The root ``tests/conftest.py`` already provides ``db_session``/``session`` and
``make_source`` (a persisted ``Source`` ORM row) - those are reused as-is by
the registry tests. What lives here is specific to the source layer: a
``SourceConfig`` factory with sane test defaults (no rate limiting, robots
disabled unless a test opts in), a small keyword vocabulary, and a helper to
read the HTML/XML/CSV fixtures this package owns.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hofradar.config import KeywordConfig, SearchProfile, SourceConfig

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "html"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def read_fixture() -> Callable[[str], str]:
    def _read(name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")

    return _read


@pytest.fixture
def make_source_config() -> Callable[..., SourceConfig]:
    """Build a ``SourceConfig``. Defaults are test-friendly: instant rate limit,
    robots.txt enforcement off (individual tests turn it back on to test it).
    """

    def _make(**kwargs: Any) -> SourceConfig:
        defaults: dict[str, Any] = {
            "key": "test-source",
            "name": "Test Source",
            "role": "primary",
            "adapter": "manual",
            "base_url": "https://example.test",
            "reliability": 0.8,
            "enabled": True,
            "rate_limit_seconds": 0.0,
            "respect_robots": False,
            "options": {},
        }
        defaults.update(kwargs)
        return SourceConfig(**defaults)

    return _make


@pytest.fixture
def search_profile() -> SearchProfile:
    return SearchProfile()


@pytest.fixture
def sample_keywords() -> KeywordConfig:
    return KeywordConfig(
        core=["Hofstelle", "Bauernhof", "Resthof"],
        buildings=["Stadel", "Scheune", "Stall"],
        hidden_phrases=["nicht mehr landwirtschaftlich genutzt", "Chiffre", "Entwicklungspotenzial"],
        regional=["Sacherl"],
        negative=["Eigentumswohnung"],
    )
