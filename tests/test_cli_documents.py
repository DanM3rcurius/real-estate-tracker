"""``hofradar documents [--check]`` - the loud word before a reader clicks a dead link.

A database that moves to another machine without its ``uploads/`` directory
(GitHub issue #26) leaves ``Document.local_path`` pointing nowhere this
machine can read. This is the CLI's early check for exactly that, mirroring
``migrate --check``'s shape: report, and exit 1 with ``--check`` if anything
is missing.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from hofradar.cli import main
from hofradar.db.models import Document


@pytest.fixture()
def cli_session(monkeypatch, db_session, make_property, tmp_path):
    """Point the CLI at the test database and an isolated uploads directory."""
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    (tmp_path / "uploads").mkdir()
    make_property(public_id="HF-0001")
    db_session.commit()

    @contextmanager
    def _scope():
        yield db_session

    monkeypatch.setattr("hofradar.cli.session_scope", _scope)
    monkeypatch.setattr("hofradar.cli.ensure_schema", lambda *a, **kw: None)
    return db_session


def _add_document(session, *, local_path: str, document_url: str = "upload:abc") -> Document:
    doc = Document(kind="upload", document_url=document_url, local_path=local_path)
    session.add(doc)
    session.commit()
    return doc


def test_no_documents_is_clean(cli_session, capsys):
    assert main(["documents"]) == 0
    assert main(["documents", "--check"]) == 0
    assert "0 document(s)" in capsys.readouterr().out


def test_a_present_file_is_not_reported_missing(cli_session, capsys, tmp_path):
    path = tmp_path / "uploads" / "abc.pdf"
    path.write_bytes(b"%PDF-1.4")
    _add_document(cli_session, local_path=str(path), document_url="upload:abc")

    assert main(["documents", "--check"]) == 0
    out = capsys.readouterr().out
    assert "1 document(s)" in out
    assert "0 missing" in out


def test_a_missing_file_is_reported_and_check_fails(cli_session, capsys, tmp_path):
    doc = _add_document(
        cli_session, local_path=str(tmp_path / "uploads" / "gone.pdf"), document_url="upload:gone"
    )

    without_check = main(["documents"])
    out = capsys.readouterr().out
    assert without_check == 0
    assert f"#{doc.id}" in out
    assert "uploads/" in out  # the README pointer

    assert main(["documents", "--check"]) == 1


def test_a_stale_absolute_path_from_another_machine_is_still_found_by_digest(
    cli_session, capsys, tmp_path
):
    """The check uses the same digest fallback as the web route (issue #26)."""
    real = tmp_path / "uploads" / "deadbeefdeadbeef.pdf"
    real.write_bytes(b"%PDF-1.4")
    _add_document(
        cli_session,
        local_path="/Users/someone/old-checkout/data/uploads/deadbeefdeadbeef.pdf",
        document_url="upload:deadbeefdeadbeef",
    )

    assert main(["documents", "--check"]) == 0
    assert "0 missing" in capsys.readouterr().out
