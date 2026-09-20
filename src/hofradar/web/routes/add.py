"""The paste box.

Most of the good farmsteads in this search are found by a human reading a local
paper, not by a crawler. So there is one text area: drop a URL or an entire
exposé in, and it goes through the same normalise -> dedupe -> lifecycle path a
crawled listing would. Every one of those modules is imported lazily and each
failure is reported as a sentence, because this form must never eat a paste.

The third way in is a file: most brokers answer a request with a PDF and
nothing else, so the exposé itself can be uploaded here (or linked, when the
URL points straight at the PDF). The file is written to disk before it is
parsed, because it - not what we read out of it - is the evidence, and it is
named by its own digest so the same exposé uploaded twice updates one
property instead of creating a second.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.contracts import PAGE_KIND_INDEX, PAGE_KIND_UTILITY, RawListing
from hofradar.db.enums import SourceRole
from hofradar.db.models import Source
from hofradar.web import lazy
from hofradar.web.deps import get_db, profile_from_query, render

router = APIRouter(tags=["add"])

MANUAL_SOURCE_KEY = "manual"

#: What a human is told about a page the lifecycle refused. Split by kind
#: because the remedy differs: a result list needs the advert opened first, a
#: portal function needs a different paste altogether. Issue #10 was a
#: "Merkliste" that saved silently, so saying nothing is not an option, and
#: saying "Fehler" would be wrong - nothing failed, the page was refused.
INGEST_REFUSAL_NOTICES: dict[str, str] = {
    PAGE_KIND_INDEX: (
        "Die eingefügte Seite ist kein Inserat, sondern eine Trefferliste mit "
        "mehreren Objekten. Es wurde nichts gespeichert – bitte das einzelne "
        "Inserat öffnen und dieses einfügen."
    ),
    PAGE_KIND_UTILITY: (
        "Die eingefügte Seite ist kein Inserat, sondern eine Portalseite "
        "(z. B. Merkliste, Suchagent oder Login). Es wurde nichts gespeichert."
    ),
}

INGEST_REFUSAL_FALLBACK = "Die eingefügte Seite ist kein Inserat. Es wurde nichts gespeichert."

#: The PDF lift lives in `sources`, which the web layer never imports at
#: module level - a half-written sibling must not stop this page from
#: rendering. Resolved through `lazy` at call time like every other one.
PDFUTIL_MODULE = "hofradar.sources.adapters._pdfutil"

#: Uploaded exposés are kept next to the database, under the same data
#: directory, and the environment is read per request rather than at import
#: so a test (and a relocated deployment) can point it elsewhere.
UPLOAD_DIR_NAME = "uploads"

#: Enough of the SHA-256 to name a file without collisions, short enough to
#: read in a URL. Both the stored file and the listing URL use it, which is
#: what makes a re-upload land on the existing property (decision 16).
UPLOAD_DIGEST_LEN = 16
UPLOAD_URL_PREFIX = "upload:"

_BYTES_PER_MB = 1024 * 1024

#: Raw fields that, on their own, still make a listing worth remembering.
_CONTENT_FIELDS = (
    "price_raw",
    "land_raw",
    "living_raw",
    "usable_raw",
    "rooms_raw",
    "year_raw",
    "location_raw",
    "postcode",
    "town",
)

UPLOAD_TOO_LARGE_NOTICE = (
    "Die Datei „{name}“ ist größer als {limit} MB und wurde nicht gelesen. "
    "Ein Exposé ist kleiner – bitte die PDF-Datei selbst hochladen, nicht die "
    "Bildermappe."
)
UPLOAD_NOT_A_PDF_NOTICE = (
    "Die Datei „{name}“ ist keine PDF-Datei und wurde nicht gelesen."
)
UPLOAD_UNREADABLE_NOTICE = (
    "Die PDF-Datei „{name}“ konnte nicht gelesen werden ({error}). Es wurde "
    "nichts daraus übernommen."
)
UPLOAD_NOT_STORED_NOTICE = (
    "Die PDF-Datei konnte nicht abgelegt werden ({error}) – sie wird trotzdem "
    "gelesen, bleibt aber nicht als Dokument erhalten."
)
UPLOAD_EMPTY_NOTICE = (
    "Aus der Datei „{name}“ ließ sich nichts lesen. Es wurde nichts gespeichert "
    "– bitte den Exposé-Text zusätzlich einfügen."
)
NOTHING_SUBMITTED_NOTICE = (
    "Bitte eine Inserats-URL, einen Exposé-Text oder eine PDF-Datei angeben."
)


@dataclass(slots=True)
class _Upload:
    """A PDF the reader chose, validated and already on disk."""

    data: bytes
    filename: str
    url: str
    local_path: str | None


#: `get_adapter` reads the adapter name out of `Source.config`, which is where
#: `sync_sources_to_db` writes it. A row created here has to carry it too, or
#: the paste box cannot build its own adapter and silently falls back to
#: storing the text unparsed (GitHub issue #3).
MANUAL_ADAPTER_CONFIG = {"adapter": "manual", "options": {}}


def manual_source(session: Session) -> Source:
    """The pseudo-source every hand-entered listing is attributed to.

    Role LOCAL, not PRIMARY: a human paste is good evidence of existence but is
    not the seller's own page, and must not be able to mark a listing verified.
    """
    source = session.scalar(select(Source).where(Source.key == MANUAL_SOURCE_KEY))
    if source is None:
        source = Source(
            key=MANUAL_SOURCE_KEY,
            name="Manuelle Eingabe",
            role=SourceRole.LOCAL,
            reliability=0.8,
            enabled=True,
            notes="Von Hand eingefügte Inserate und Exposé-Texte.",
            config=dict(MANUAL_ADAPTER_CONFIG),
        )
        session.add(source)
        session.commit()
        session.refresh(source)
    elif not (source.config or {}).get("adapter"):
        # A row written by an earlier version of this function, or by hand.
        source.config = {**(source.config or {}), **MANUAL_ADAPTER_CONFIG}
        session.commit()
    return source


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _get_adapter(source: Source) -> Any:
    """Build the manual adapter for this source row.

    ``get_adapter`` takes a ``Source`` or a ``SourceConfig`` and reads the
    adapter name from it - never a bare key, which the previous fallback here
    passed and which failed with the same error it was catching. If the row is
    unusable, fall back to the registry's own manual entry rather than to
    nothing: the paste box is the one source that must always work.
    """
    get_adapter = lazy.load("hofradar.sources:get_adapter")
    try:
        return get_adapter(source)
    except Exception:  # noqa: BLE001 - fall back to the configured definition
        configs = lazy.call("hofradar.config:load_config").sources
        manual = next(cfg for cfg in configs if cfg.key == MANUAL_SOURCE_KEY)
        return get_adapter(manual)


def _uploads_dir() -> Path:
    """Where an uploaded exposé is kept - resolved now, not at import time."""
    return Path(os.environ.get("HOFRADAR_DATA_DIR", "data")) / UPLOAD_DIR_NAME


def _store_upload(data: bytes, digest: str) -> tuple[str | None, lazy.Degraded | None]:
    """Write the file to disk before a parser has looked at it.

    A full disk or a read-only volume costs the document reference, not the
    listing: the facts were in the bytes we already hold, so the paste is
    still processed and the reader is told what was lost.
    """
    path = _uploads_dir() / f"{digest}.pdf"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as exc:
        return None, lazy.Degraded(UPLOAD_NOT_STORED_NOTICE.format(error=type(exc).__name__))
    return str(path), None


async def _accept_upload(pdf: UploadFile | None) -> tuple[_Upload | None, list[lazy.Degraded]]:
    """Validate the chosen file and put it on disk, before anything parses it.

    An empty file input - the normal case, the reader pasted text instead -
    is not a failure and produces no notice. Everything else that cannot be
    used produces one: a file silently ignored is the bug this codebase
    keeps having.
    """
    if pdf is None:
        return None, []
    filename = (pdf.filename or "").strip()
    data = await pdf.read()
    if not filename or not data:
        return None, []

    try:
        pdfutil = lazy.load(PDFUTIL_MODULE)
    except lazy.ModuleUnavailable as exc:
        return None, [lazy.Degraded(exc.user_message, detail=repr(exc.original))]

    if len(data) > pdfutil.PDF_MAX_BYTES:
        limit = pdfutil.PDF_MAX_BYTES // _BYTES_PER_MB
        return None, [lazy.Degraded(UPLOAD_TOO_LARGE_NOTICE.format(name=filename, limit=limit))]
    if not pdfutil.looks_like_pdf(data):
        return None, [lazy.Degraded(UPLOAD_NOT_A_PDF_NOTICE.format(name=filename))]

    digest = hashlib.sha256(data).hexdigest()[:UPLOAD_DIGEST_LEN]
    local_path, note = _store_upload(data, digest)
    upload = _Upload(
        data=data,
        filename=filename,
        url=f"{UPLOAD_URL_PREFIX}{digest}",
        local_path=local_path,
    )
    return upload, [note] if note is not None else []


def _apply_upload(
    raw: RawListing, upload: _Upload, *, source: Source, page_wins: bool
) -> tuple[RawListing, list[lazy.Degraded]]:
    """Fold the uploaded PDF into what the reader otherwise handed over.

    ``page_wins`` is true when there is already a listing from the reader's
    own words or from a fetched page; then the PDF only fills the holes in
    it, because a human's summary beats a cover page. With neither, the PDF
    is the listing.
    """
    try:
        pdfutil = lazy.load(PDFUTIL_MODULE)
    except lazy.ModuleUnavailable as exc:
        return raw, [lazy.Degraded(exc.user_message, detail=repr(exc.original))]

    try:
        if page_wins:
            pdfutil.merge_pdf_into_listing(
                raw,
                pdfutil.extract_pdf_text(upload.data),
                document_url=upload.url,
                document_title=upload.filename,
                kind=pdfutil.DOCUMENT_KIND_UPLOAD,
            )
        else:
            parsed = _get_adapter(source).ingest_pdf(
                upload.url, upload.data, filename=upload.filename
            )
            # The listing's identity stays whatever the reader gave: a URL
            # they pasted wins, and only a PDF-only submission is identified
            # by the file's own digest.
            parsed.url = raw.url
            raw = parsed
    except pdfutil.PdfUnavailable:
        return raw, [lazy.Degraded(pdfutil.WARNING_PDF_UNAVAILABLE)]
    except pdfutil.PdfError as exc:
        return raw, [
            lazy.Degraded(
                UPLOAD_UNREADABLE_NOTICE.format(name=upload.filename, error=type(exc).__name__)
            )
        ]

    for ref in raw.documents:
        if ref.url == upload.url and upload.local_path:
            ref.local_path = upload.local_path
    return raw, []


def _has_content(raw: RawListing) -> bool:
    """Is there anything here to remember - a title, prose or one fact?"""
    return bool(
        raw.title
        or raw.description
        or any(getattr(raw, name, None) for name in _CONTENT_FIELDS)
    )


@router.get("/add")
def add_page(request: Request, session: Session = Depends(get_db)):
    profile = profile_from_query(request.query_params, session=session)
    return render(
        request,
        "pages/add.html",
        {
            "profile": profile,
            "degraded": [],
            "result": None,
            "url_value": "",
            "text_value": "",
        },
    )


@router.post("/add")
async def add_submit(
    request: Request,
    session: Session = Depends(get_db),
    url: str = Form(default=""),
    text: str = Form(default=""),
    pdf: UploadFile | None = File(default=None),
):
    profile = profile_from_query(request.query_params, session=session)
    url, text = url.strip(), text.strip()
    result: dict[str, Any] | None = None

    upload, degraded = await _accept_upload(pdf)

    if not url and not text and upload is None:
        degraded.append(lazy.Degraded(NOTHING_SUBMITTED_NOTICE))
        return render(
            request,
            "pages/add.html",
            {
                "profile": profile,
                "degraded": degraded,
                "result": None,
                "url_value": url,
                "text_value": text,
            },
            status_code=400,
        )

    source = manual_source(session)

    try:
        # A pasted URL identifies the listing; failing that, an uploaded file
        # does, by its digest, so the same exposé sent twice updates one row.
        # Only a bare text paste has nothing stable to be identified by.
        fallback_url = (
            upload.url if upload is not None
            else f"manual:{datetime.now(UTC).isoformat(timespec='seconds')}"
        )
        raw = RawListing(
            source_key=MANUAL_SOURCE_KEY,
            url=url or fallback_url,
            description=text or None,
            title=(text.splitlines()[0][:200] if text else None),
            fetched_at=datetime.now(UTC),
        )

        # The adapter is what knows how to read an exposé: "Kaufpreis: ...",
        # "Wohnfläche: ...", and the HTML case. Building the RawListing here by
        # hand meant a pasted text arrived with every one of those fields empty
        # - the paste box parsed nothing at all (GitHub issue #3). The
        # hand-built listing above stays as the fallback for when the adapter
        # cannot be loaded, because this form must never eat a paste.
        if text:
            try:
                parsed = _get_adapter(source).ingest_text(raw.url, text)
            except lazy.ModuleUnavailable as exc:
                degraded.append(lazy.Degraded(exc.user_message))
            except Exception as exc:  # noqa: BLE001 - fall back to the raw text
                degraded.append(
                    lazy.Degraded(
                        "Der Text konnte nicht strukturiert gelesen werden – er wird "
                        f"unverändert gespeichert. ({type(exc).__name__})"
                    )
                )
            else:
                if parsed is not None:
                    parsed.description = parsed.description or text
                    raw = parsed

        fetched_page = False
        if url:
            try:
                adapter = _get_adapter(source)
                fetched = await _maybe_await(adapter.fetch_detail(url))
                if fetched is not None:
                    raw = fetched
                    fetched_page = True
                    if text and not raw.description:
                        raw.description = text
            except lazy.ModuleUnavailable as exc:
                degraded.append(lazy.Degraded(exc.user_message))
            except Exception as exc:  # noqa: BLE001 - a dead URL is not our bug
                degraded.append(
                    lazy.Degraded(
                        "Die URL konnte nicht abgerufen werden – der eingefügte Text wird trotzdem "
                        f"verarbeitet. ({type(exc).__name__})"
                    )
                )

        if upload is not None:
            raw, upload_notes = _apply_upload(
                raw, upload, source=source, page_wins=bool(text) or fetched_page
            )
            degraded.extend(upload_notes)
            if not _has_content(raw):
                # A scanned exposé, and nothing else handed over. Storing an
                # empty property with a warning attached is precisely the
                # silence that looks like success: refuse it, name the file,
                # and repeat what the lift already said about it.
                session.rollback()
                degraded.append(lazy.Degraded(UPLOAD_EMPTY_NOTICE.format(name=upload.filename)))
                degraded.extend(lazy.Degraded(warning) for warning in raw.warnings)
                return render(
                    request,
                    "pages/add.html",
                    {
                        "profile": profile,
                        "degraded": degraded,
                        "result": None,
                        "url_value": url,
                        "text_value": text,
                    },
                    status_code=400,
                )

        keywords, kw_note = lazy.call_or("hofradar.config:load_keywords", None)
        if kw_note is not None:
            from hofradar.config import KeywordConfig

            keywords = KeywordConfig()

        listing = lazy.call("hofradar.normalize:normalize_listing", raw, keywords)

        # Geocode and route before ingesting. Without this the property has no
        # road distance, the scorer caps its confidence below the shortlist
        # threshold, and a hand-pasted listing could never reach the top ten -
        # which would defeat the point of the paste box.
        geo = None
        try:
            locate = lazy.load("hofradar.geo:locate")
            geo = await locate(session, listing, profile)
        except lazy.ModuleUnavailable as exc:
            degraded.append(lazy.Degraded(exc.user_message))
        except Exception as exc:  # noqa: BLE001 - a geocoder outage is not our bug
            degraded.append(
                lazy.Degraded(
                    "Standort konnte nicht bestimmt werden - das Objekt wird ohne "
                    f"Entfernung gespeichert. ({type(exc).__name__})"
                )
            )

        # Not `lazy.call`: it translates *every* exception into
        # ModuleUnavailable, which would report a deliberate refusal as a
        # missing package. Everything else is re-wrapped exactly as lazy.call
        # would, so a stale schema still gets its own notice (issue #7).
        ingest = lazy.load("hofradar.lifecycle:ingest")
        not_a_listing = lazy.load("hofradar.lifecycle:NotAListing")
        try:
            prop, change = ingest(session, listing, source=source, geo=geo)
        except not_a_listing as exc:
            session.rollback()
            degraded.append(
                lazy.Degraded(
                    INGEST_REFUSAL_NOTICES.get(exc.page_kind, INGEST_REFUSAL_FALLBACK)
                )
            )
            return render(
                request,
                "pages/add.html",
                {
                    "profile": profile,
                    "degraded": degraded,
                    "result": None,
                    "url_value": url,
                    "text_value": text,
                },
            )
        except lazy.ModuleUnavailable:
            raise
        except BaseException as exc:  # noqa: BLE001 - see hofradar.web.lazy
            raise lazy.ModuleUnavailable("hofradar.lifecycle:ingest", exc) from exc
        session.commit()
        result = {
            "public_id": getattr(prop, "public_id", None),
            "title": getattr(prop, "canonical_title", None),
            "town": getattr(prop, "town", None),
            "change_kind": getattr(change, "kind", None),
            "detail": getattr(change, "detail", None),
            # The complaint behind issue #3 was not "parsing is wrong", it was
            # "it looked like it worked". Anything the normaliser could not
            # make sense of belongs on the confirmation page.
            "warnings": list(getattr(listing, "warnings", []) or []),
        }
    except lazy.ModuleUnavailable as exc:
        session.rollback()
        degraded.append(lazy.Degraded(exc.user_message, detail=repr(exc.original)))
    except Exception as exc:  # noqa: BLE001 - never lose the paste to a traceback
        session.rollback()
        degraded.append(
            lazy.Degraded(f"Verarbeitung fehlgeschlagen: {type(exc).__name__}: {exc}")
        )

    return render(
        request,
        "pages/add.html",
        {
            "profile": profile,
            "degraded": degraded,
            "result": result,
            "url_value": "" if result else url,
            "text_value": "" if result else text,
        },
    )
