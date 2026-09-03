"""The migration environment must see every model table.

A migration environment whose metadata is wired to the wrong Base autogenerates
an empty revision and silently stops protecting the schema.
"""

from __future__ import annotations

from alembic.config import Config

from hofradar.db.models import Base


def test_alembic_env_target_metadata_covers_models() -> None:
    config = Config("alembic.ini")
    assert config.get_main_option("script_location") == "migrations"

    from migrations.env import target_metadata

    assert target_metadata is Base.metadata
    assert "properties" in target_metadata.tables
    assert "observations" in target_metadata.tables
