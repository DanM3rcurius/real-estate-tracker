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
"""

from __future__ import annotations

import os
from pathlib import Path

UPLOAD_DIR_NAME = "uploads"

#: Enough of the SHA-256 to name a file without collisions, short enough to
#: read in a URL. Both the stored file and the listing URL use it.
UPLOAD_DIGEST_LEN = 16

#: ``upload:<digest>`` - a PDF the reader handed over.
UPLOAD_URL_PREFIX = "upload:"

#: ``manual:<iso timestamp>`` - a paste with no URL of its own.
MANUAL_URL_PREFIX = "manual:"


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
