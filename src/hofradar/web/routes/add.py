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
