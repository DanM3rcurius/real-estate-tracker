"""Handing back the exposé the reader uploaded.

``/add`` writes the file to disk before anything parses it, because the file -
not what we read out of it - is the evidence (decision 24). Until now nothing
served it back: the dossier linked to ``upload:<digest>``, which is the
listing's *identity* and not an address, so "Inserat öffnen" and "Dokument"
were buttons that did nothing when clicked. This is the route that makes the
stored file readable again.

It serves only files inside the uploads directory. ``Document.local_path`` is
written by our own code today, but it is a plain string column, and a path that
resolves outside that directory is refused rather than opened. It also survives
a ``local_path`` that names a directory this machine has never had - the one a
database migration leaves behind (issue #26) - by falling back to this
machine's own copy of an ``upload:`` document's digest; see
:func:`hofradar.web.uploads.resolve_upload_path`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from hofradar.db.models import Document
from hofradar.web.deps import get_db, render
from hofradar.web.query import is_web_url
from hofradar.web.uploads import resolve_upload_path

router = APIRouter(tags=["documents"])

#: Uploads are PDFs; ``/add`` refuses anything else before it writes a byte.
PDF_MEDIA_TYPE = "application/pdf"

NOT_FOUND = "Kein Dokument mit der Nummer {document_id}."
#: Issue #26: a database moved to another machine (dev -> Pi) without its
#: ``uploads/`` directory looks exactly like a deleted file from here. Say so,
#: rather than let "nicht mehr vorhanden" read as if the reader deleted it.
FILE_GONE = (
    "Die Datei „{title}“ fehlt auf diesem Server. Gespeichert war sie unter "
    "{path} – das ist meist eine Datenbank ohne ihr uploads/-Verzeichnis "
    "(siehe deploy/raspberrypi/README.md, „Bringing an existing database with "
    "you“): die Datei von dort auf dieses Gerät kopieren und den Dateinamen "
    "dabei unverändert lassen. Das Objekt und seine Fakten bleiben erhalten."
)
REMOTE_ONLY = (
    "Zu „{title}“ liegt keine eigene Kopie vor, nur der Link zur Quelle: {url}"
)
NO_FILE = "Zu „{title}“ ist weder eine Datei noch ein aufrufbarer Link hinterlegt."


def _error(request: Request, code: int, message: str):
    return render(request, "pages/error.html", {"code": code, "message": message}, status_code=code)


@router.get("/document/{document_id}")
def document(document_id: int, request: Request, session: Session = Depends(get_db)):
    """The stored exposé itself, shown inline so the browser's PDF viewer opens it."""
    row = session.get(Document, document_id)
    if row is None:
        return _error(request, 404, NOT_FOUND.format(document_id=document_id))

    title = row.title or row.kind
    path = resolve_upload_path(row.local_path, row.document_url)
    if path is not None:
        return FileResponse(
            path,
            media_type=PDF_MEDIA_TYPE,
            # inline, not an attachment: the reader wants to read it, not file it.
            headers={"Content-Disposition": f'inline; filename="{path.name}"'},
        )

    # No readable copy. Say which of the three reasons it is - a 404 with no
    # sentence is exactly the silence this route exists to end.
    if row.local_path:
        return _error(request, 410, FILE_GONE.format(title=title, path=row.local_path))
    if is_web_url(row.document_url):
        return _error(request, 404, REMOTE_ONLY.format(title=title, url=row.document_url))
    return _error(request, 404, NO_FILE.format(title=title))
