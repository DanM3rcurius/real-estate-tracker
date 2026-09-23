"""Where a reader's uploaded exposé lives, and what it is called.

Two modules need the same answer and neither may guess: ``routes/add.py``
writes the file, ``routes/documents.py`` hands it back. The directory is read
from the environment per call rather than at import time so a test - and a
relocated deployment - can point it elsewhere.

The two pseudo-schemes here are identities, not addresses. A PDF-only
submission is named by its own digest so the same exposé sent twice updates
one property (decision 16), and a bare text paste is named by the moment it
arrived because nothing else about it is stable. Neither is a URL a browser
can open, which is what :func:`hofradar.web.query.is_web_url` exists to say.

``Document.local_path`` is written as an absolute path at upload time (issue
#26). Move the database to another machine - dev laptop to the Pi - without
also moving ``uploads/``, and every stored path still points at the old
machine's filesystem; even after the files are copied over, a path minted on
one box rarely resolves on another. :func:`resolve_upload_path` is what lets
the file be found anyway: for an ``upload:<digest>`` document the digest *is*
the filename, so it is reconstructed and looked up under this machine's own
:func:`uploads_dir` rather than trusted from the stored column.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

UPLOAD_DIR_NAME = "uploads"

#: Enough of the SHA-256 to name a file without collisions, short enough to
#: read in a URL. Both the stored file and the listing URL use it.
UPLOAD_DIGEST_LEN = 16

#: ``upload:<digest>`` - a PDF the reader handed over.
UPLOAD_URL_PREFIX = "upload:"

#: ``manual:<iso timestamp>`` - a paste with no URL of its own.
MANUAL_URL_PREFIX = "manual:"

#: What ``_store_upload`` names a file. A digest that does not match this
#: shape never reaches the filesystem - it is either not ours or corrupted,
#: and :func:`resolve_upload_path` must not turn it into a path.
_DIGEST_RE = re.compile(rf"^[0-9a-f]{{{UPLOAD_DIGEST_LEN}}}$")


def uploads_dir() -> Path:
    """The directory uploaded exposés are kept in - resolved now, not at import."""
    return Path(os.environ.get("HOFRADAR_DATA_DIR", "data")) / UPLOAD_DIR_NAME


def stored_upload_path(local_path: str | None) -> Path | None:
    """The readable file behind ``Document.local_path``, or ``None``.

    Refuses anything outside :func:`uploads_dir`. The column is only ever
    written from our own writes today, but it is a plain string in the
    database and this function is what turns it into a file the web layer
    is willing to serve.
    """
    if not local_path:
        return None
    try:
        path = Path(local_path).resolve()
        root = uploads_dir().resolve()
    except OSError:
        return None
    if not path.is_relative_to(root):
        return None
    return path if path.is_file() else None


def _path_by_digest(document_url: str | None) -> Path | None:
    """Reconstruct an upload's filename from its identity, ignoring ``local_path``.

    Only ``upload:<digest>`` documents can be recovered this way - a text
    paste (``manual:``) and a remote exposé (``expose``) never had a file of
    their own to lose.
    """
    if not document_url or not document_url.startswith(UPLOAD_URL_PREFIX):
        return None
    digest = document_url[len(UPLOAD_URL_PREFIX) :]
    if not _DIGEST_RE.match(digest):
        return None
    candidate = uploads_dir() / f"{digest}.pdf"
    return candidate if candidate.is_file() else None


def resolve_upload_path(local_path: str | None, document_url: str | None) -> Path | None:
    """The readable file for a ``Document``, surviving a stale ``local_path``.

    Tries the stored path first - the common case, and the one that still
    carries a path a reader typed for something outside ``uploads_dir()``.
    Falls back to the file this machine's own :func:`uploads_dir` would name
    from the document's digest, which is what makes an ``upload:`` document
    findable again after only the database, and not ``uploads/``, made the
    trip to another machine (issue #26) - and just as well after ``uploads/``
    arrived too, if the path column itself is what did not survive the move.
    """
    return stored_upload_path(local_path) or _path_by_digest(document_url)
