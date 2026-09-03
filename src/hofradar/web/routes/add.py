"""The paste box.

Most of the good farmsteads in this search are found by a human reading a local
paper, not by a crawler. So there is one text area: drop a URL or an entire
exposé in, and it goes through the same normalise -> dedupe -> lifecycle path a
crawled listing would. Every one of those modules is imported lazily and each
failure is reported as a sentence, because this form must never eat a paste.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.db.enums import SourceRole
from hofradar.db.models import Source
from hofradar.web import lazy
from hofradar.web.deps import get_db, profile_from_query, render

router = APIRouter(tags=["add"])

MANUAL_SOURCE_KEY = "manual"


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
        )
        session.add(source)
        session.commit()
        session.refresh(source)
    return source


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _get_adapter(source: Source) -> Any:
    """``get_adapter`` takes a Source per the contract; tolerate a key too."""
    get_adapter = lazy.load("hofradar.sources:get_adapter")
    try:
        return get_adapter(source)
    except Exception:  # noqa: BLE001 - a key-based build is equally plausible
        return get_adapter(MANUAL_SOURCE_KEY)


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
):
    profile = profile_from_query(request.query_params, session=session)
    url, text = url.strip(), text.strip()
    degraded: list[lazy.Degraded] = []
    result: dict[str, Any] | None = None

    if not url and not text:
        degraded.append(lazy.Degraded("Bitte eine Inserats-URL oder einen Exposé-Text angeben."))
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
        from hofradar.contracts import RawListing

        raw = RawListing(
            source_key=MANUAL_SOURCE_KEY,
            url=url or f"manual:{datetime.now(UTC).isoformat(timespec='seconds')}",
            description=text or None,
            title=(text.splitlines()[0][:200] if text else None),
            fetched_at=datetime.now(UTC),
        )

        if url:
            try:
                adapter = _get_adapter(source)
                fetched = await _maybe_await(adapter.fetch_detail(url))
                if fetched is not None:
                    raw = fetched
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

        prop, change = lazy.call(
            "hofradar.lifecycle:ingest", session, listing, source=source, geo=geo
        )
        session.commit()
        result = {
            "public_id": getattr(prop, "public_id", None),
            "title": getattr(prop, "canonical_title", None),
            "town": getattr(prop, "town", None),
            "change_kind": getattr(change, "kind", None),
            "detail": getattr(change, "detail", None),
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
