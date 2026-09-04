"""``hofradar delete-property`` - a dry run unless somebody says --apply.

The same shape as ``repair-removals``: printing what would go is the default,
because the thing on the other side of this command is an append-only history
that no source can re-supply.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import select

from hofradar.cli import main
from hofradar.db.models import Property


@pytest.fixture()
def cli_session(monkeypatch, db_session, make_property):
    """Point the CLI at the test database and keep it away from the schema."""
    make_property(public_id="HF-0001")
    db_session.commit()

    @contextmanager
    def _scope():
        yield db_session

    monkeypatch.setattr("hofradar.cli.session_scope", _scope)
    monkeypatch.setattr("hofradar.cli.ensure_schema", lambda *a, **kw: None)
    return db_session


def _exists(session, public_id: str = "HF-0001") -> bool:
    return session.scalar(select(Property).where(Property.public_id == public_id)) is not None


def test_dry_run_deletes_nothing(cli_session, capsys):
    assert main(["delete-property", "HF-0001"]) == 0
    assert _exists(cli_session)
    output = capsys.readouterr().out
    assert "HF-0001" in output
    assert "--apply" in output


def test_apply_deletes(cli_session, capsys):
    assert main(["delete-property", "HF-0001", "--apply", "--no-backup"]) == 0
    assert not _exists(cli_session)


def test_an_unknown_public_id_is_an_error(cli_session, capsys):
    assert main(["delete-property", "HF-NOPE", "--apply", "--no-backup"]) == 1
    assert _exists(cli_session)
