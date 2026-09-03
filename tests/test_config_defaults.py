"""The bundled config must match the repository's config.

The packaged copies exist so that `hofradar serve` from an unexpected working
directory still boots with the real search DNA. That only helps if they are the
same files, so drift is a test failure rather than a surprise months later.
"""

from __future__ import annotations

import filecmp
from pathlib import Path

import pytest

from hofradar.config import PACKAGED_CONFIG_DIR, find_config_dir, load_profile

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config"
YAML_NAMES = ("search.yaml", "scoring.yaml", "keywords.yaml", "sources.yaml")


@pytest.mark.parametrize("name", YAML_NAMES)
def test_the_packaged_copy_matches_the_repository(name: str) -> None:
    repo_file = REPO_CONFIG / name
    packaged = PACKAGED_CONFIG_DIR / name
    assert packaged.is_file(), f"{name} is missing; run scripts/sync_config_defaults.py"
    assert filecmp.cmp(repo_file, packaged, shallow=False), (
        f"{name} has drifted; run scripts/sync_config_defaults.py"
    )


def test_the_env_override_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOFRADAR_CONFIG_DIR", str(tmp_path))
    assert find_config_dir() == tmp_path


def test_config_is_found_from_a_subdirectory(monkeypatch, tmp_path) -> None:
    """Running from tests/ or src/ must still find the repository's config."""
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "config" / "search.yaml").write_text("search: {}\n", encoding="utf-8")
    deep = root / "src" / "hofradar"
    deep.mkdir(parents=True)

    monkeypatch.delenv("HOFRADAR_CONFIG_DIR", raising=False)
    monkeypatch.chdir(deep)

    assert find_config_dir() == root / "config"


def test_an_unrelated_directory_falls_back_to_the_packaged_copies(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("HOFRADAR_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    assert find_config_dir() == PACKAGED_CONFIG_DIR


def test_the_fallback_still_loads_the_real_search_dna(monkeypatch, tmp_path) -> None:
    """The bug this guards: a wrong cwd used to silently mean empty defaults."""
    monkeypatch.delenv("HOFRADAR_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    profile = load_profile()

    assert profile.center.name.startswith("Westham")
    assert profile.radius.air_km_max == 80.0
    assert profile.budget.total_budget_max == 1_200_000.0
    assert profile.property_types, "property types must not be empty"
