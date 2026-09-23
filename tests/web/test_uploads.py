"""``resolve_upload_path`` - finding an upload after its ``local_path`` no longer applies.

Issue #26: a database moved to another machine without ``uploads/`` (or with
it, but the stored column still names the old machine's filesystem) leaves
``Document.local_path`` unresolvable here even once the file itself has been
copied over. The digest embedded in an ``upload:<digest>`` document's own
identity is what recovers it - see the module docstring.
"""

from __future__ import annotations

from hofradar.web.uploads import UPLOAD_URL_PREFIX, resolve_upload_path, stored_upload_path


def test_a_path_under_uploads_dir_resolves_normally(monkeypatch, tmp_path):
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    path = tmp_path / "uploads" / "abcdef0123456789.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.4")

    assert stored_upload_path(str(path)) == path
    assert resolve_upload_path(str(path), "upload:abcdef0123456789") == path


def test_a_stale_local_path_falls_back_to_the_digest(monkeypatch, tmp_path):
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    real = tmp_path / "uploads" / "abcdef0123456789.pdf"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"%PDF-1.4")
    stale = "/Users/someone/old-checkout/data/uploads/abcdef0123456789.pdf"

    assert stored_upload_path(stale) is None
    assert resolve_upload_path(stale, f"{UPLOAD_URL_PREFIX}abcdef0123456789") == real


def test_no_fallback_without_a_matching_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    (tmp_path / "uploads").mkdir()

    assert resolve_upload_path("/nowhere/on/this/box.pdf", "upload:0000000000000000") is None


def test_no_fallback_for_a_non_upload_document(monkeypatch, tmp_path):
    """A remote exposé or a text paste never had a file to recover."""
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    (tmp_path / "uploads").mkdir()

    assert resolve_upload_path("/nowhere.pdf", "manual:2026-09-01T00:00:00") is None
    assert resolve_upload_path("/nowhere.pdf", "https://example.invalid/expose.pdf") is None


def test_a_malformed_digest_is_never_turned_into_a_path(monkeypatch, tmp_path):
    """The digest is matched against its own shape before it touches the filesystem."""
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    (tmp_path / "uploads").mkdir()
    # A path-traversal attempt through the document_url column, not local_path.
    (tmp_path / "secret.pdf").write_bytes(b"not yours")

    assert resolve_upload_path(None, "upload:../../secret") is None
