"""The Dossier - one property, and why we believe every word of it.

Every fact on this page is printed next to its evidence (source, quote, link).
A fact with no evidence is shown *and marked* rather than hidden: "we do not
know where this claim came from" is itself information the buyer needs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from hofradar.costmodel import (
    EVIDENCE_INFERRED,
    EVIDENCE_MANUAL,
    EVIDENCE_OBSERVED,
    MANUAL_TIERS,
    automatic_renovation_tier,
    infer_renovation_tier,
    manual_tier,
    renovation_evidence,
)
from hofradar.db.models import Property
from hofradar.web import history
from hofradar.web.charts import sparkline
from hofradar.web.deps import get_db, profile_from_query, render
from hofradar.web.filters import de_eur, de_km, de_number, de_sqm, de_tier, one_line
from hofradar.web.query import (
    best_url,
    change_chips,
    document_href,
    document_missing,
    load_property,
    no_link_reason,
    open_link,
    pick_document,
    row_to_dict,
)

router = APIRouter(tags=["dossier"])

#: ``user_state`` values the triage control accepts. "none" clears the flag.
#: "rejected" is a verdict about the farm and keeps it on the radar; only the
#: states in :data:`~hofradar.db.enums.HIDDEN_USER_STATES` take it off the
#: screen, and the labels have to say which is which - one German word for both
#: is what GitHub issue #9 reported. See docs/DECISIONS.md entry 20.
#: There is no "shortlist" entry any more: the Merkliste (``Property.
#: shortlisted_at``, the ``/merken`` route) replaced it. It was orthogonal to
#: triage - a Kontaktiert farm can stay on the list - and collided with the
#: same one-word-two-facts lesson issue #9 already taught this file.
USER_STATES: dict[str, str] = {
    "watch": "👀 Beobachten",
    "contacted": "📞 Kontaktiert",
    "rejected": "🚫 Abgelehnt (bleibt sichtbar)",
    "archived": "📦 Archiviert – nicht mehr anzeigen",
    "none": "– kein Status",
}

#: The reader's own name for a property fits the column it is stored in - a
#: longer one is refused and said so, never cut short without a word.
USER_TITLE_MAX = Property.__table__.c.user_title.type.length

#: A crawl holds SQLite's write lock for its whole run, so a rename made
#: meanwhile fails. htmx swaps nothing on an error status, so without this the
#: click would simply do nothing.
TITLE_LOCKED = (
    "Die Datenbank ist gerade gesperrt – vermutlich läuft ein Crawl. "
    "Bitte nach dem Crawl noch einmal speichern."
)

#: A form rendered before the Merkliste existed still posts this. Honoured as
#: "put it on the Merkliste" rather than silently dropped - see decision 20 /
#: docs/superpowers/specs/2026-09-04-ui-refinements-design.md section 2.
LEGACY_SHORTLIST_STATE = "shortlist"

#: Cost breakdown keys -> German labels. The table already prints purchase
#: price, acquisition costs and immediate capex from ``CostEstimate`` columns
#: directly, so those three keys are in :data:`SKIP_COST_KEYS` instead.
COST_LABELS: dict[str, str] = {
    "purchase": "Kaufpreis",
    "acquisition": "Erwerbsnebenkosten",
    "house": "Haus (Mitte)",
    "house_low": "Haus (niedrig)",
    "house_high": "Haus (hoch)",
    "roof": "Dach",
    "outbuildings": "Nebengebäude",
    "utilities": "Haustechnik",
    "contingency": "Puffer (Mitte)",
    "contingency_low": "Puffer (niedrig)",
    "contingency_high": "Puffer (hoch)",
    "immediate_capex": "Sofortmaßnahmen",
    "living_sqm_used": "Wohnfläche angesetzt",
    "roof_sqm_used": "Dachfläche angesetzt",
    "outbuilding_sqm_used": "Nebengebäudefläche angesetzt",
    "rate_per_sqm_low": "Satz €/m² (niedrig)",
    "rate_per_sqm_mid": "Satz €/m² (Mitte)",
    "rate_per_sqm_high": "Satz €/m² (hoch)",
}
#: Per-tag outbuilding area keys (``outbuilding_sqm_<tag>``) are not enumerable
#: up front - the tag list comes from the listing - so they get a generic label.
OUTBUILDING_SQM_PREFIX = "outbuilding_sqm_"
SQM_KEYS = frozenset({"living_sqm_used", "roof_sqm_used", "outbuilding_sqm_used"})
RATE_KEYS = frozenset({"rate_per_sqm_low", "rate_per_sqm_mid", "rate_per_sqm_high"})
#: Already printed as their own table row from the ``CostEstimate`` columns.
SKIP_COST_KEYS = frozenset({"purchase", "acquisition", "immediate_capex"})

#: Score breakdown path segments -> German labels. A ``_score`` suffix is
#: dropped before lookup and a ``_max`` suffix becomes " (max.)" - see
#: :func:`score_label`. A segment missing here passes through unchanged: a
#: label map must never hide a key the scorer actually wrote.
SCORE_LABELS: dict[str, str] = {
    "fit": "Passung",
    "deal": "Preis-Leistung",
    "hidden": "Verborgenheit",
    "freshness": "Frische",
    "confidence": "Belastbarkeit",
    "geography": "Lage",
    "price": "Preis",
    "land": "Grund",
    "substance": "Substanz",
    "seclusion": "Alleinlage",
    "development": "Entwicklung",
    "outbuildings": "Nebengebäude",
}

CAPITAL_RISK_LABELS: dict[str, str] = {
    "low": "gering",
    "moderate": "mäßig",
    "high": "hoch",
    "extreme": "extrem",
}

#: Whether the renovation tier came from the listing or was guessed from the
#: year of construction - see :func:`hofradar.costmodel.renovation_evidence`.
EVIDENCE_LABELS: dict[str, str] = {
    EVIDENCE_MANUAL: "manuell gesetzt",
    EVIDENCE_OBSERVED: "laut Inserat",
    EVIDENCE_INFERRED: "aus Baujahr geschätzt",
}

#: What the Sanierungsstufe form posts to hand the tier back to the inference
#: rules. An empty value means the same, for a hand-built request.
TIER_AUTO = "auto"
TIER_AUTO_LABEL = "Automatisch (aus Inserat/Baujahr)"

#: ``?sanierung=`` on the redirect back to the dossier: the page says what
#: happened, because a plain POST has no other way to report it.
TIER_SAVED = "gespeichert"
#: Saved, but the cost model could not be recomputed (a crawl holds the lock).
TIER_SAVED_NOT_RECOMPUTED = "nicht-neu-berechnet"
TIER_STATUS_PARAM = "sanierung"

#: The anchor of the Kostenmodell section, where the form sits.
COST_ANCHOR = "kostenmodell"

TIER_LOCKED = (
    "Die Datenbank ist gerade gesperrt – vermutlich läuft ein Crawl. "
    "Die Sanierungsstufe wurde nicht gespeichert; bitte nach dem Crawl noch einmal."
)


def tier_choices() -> list[tuple[str, str]]:
    """The <select>'s options: automatic first, then every tier a reader may
    set, worded by :func:`de_tier` so the dossier and the form never disagree."""
    choices = [(TIER_AUTO, TIER_AUTO_LABEL)]
    for tier in MANUAL_TIERS:
        word = de_tier(tier.value)
        choices.append((tier.value, word[:1].upper() + word[1:]))
    return choices

#: (attribute, German label, formatter). Order is the reading order of the page.
FACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("canonical_title", "Titel", "text"),
    ("property_type", "Objektart", "text"),
    ("price", "Preis", "eur"),
    ("price_type", "Preisart", "text"),
    ("price_first", "Erstpreis", "eur"),
    ("land_sqm", "Grundstück", "sqm"),
    ("living_sqm", "Wohnfläche", "sqm"),
    ("usable_sqm", "Nutzfläche", "sqm"),
    ("rooms", "Zimmer", "number"),
    ("year_built", "Baujahr", "year"),
    ("condition", "Zustand", "text"),
    ("street", "Straße", "text"),
    ("postcode", "PLZ", "text"),
    ("town", "Ort", "text"),
    ("district", "Ortsteil", "text"),
    ("distance_air_km", "Luftlinie", "km"),
    ("distance_driving_km", "Fahrstrecke", "driving_km"),
    ("geo_precision", "Standortgenauigkeit", "text"),
    ("building_features", "Gebäudemerkmale", "list"),
    ("outbuildings", "Nebengebäude", "list"),
    ("special_features", "Besonderheiten", "list"),
    ("exclusion_flags", "Ausschlussmerkmale", "list"),
)


def _format(value: Any, how: str) -> str:
    if how == "eur":
        return de_eur(value)
    if how == "sqm":
        return de_sqm(value)
    if how == "km":
        return de_km(value)
    if how == "driving_km":
        # Never fall back to the air distance. Unknown means unknown.
        return "nicht geprüft" if value is None else de_km(value)
    if how == "number":
        return de_number(value, 0)
    if how == "year":
        return "k. A." if value is None else str(int(value))
    if how == "list":
        items = list(value or [])
        return ", ".join(str(i) for i in items) if items else "k. A."
    return "k. A." if value in (None, "") else str(value)


def fact_rows(prop: Property) -> list[dict[str, Any]]:
    evidence = prop.evidence or {}
    rows: list[dict[str, Any]] = []
    for attribute, label, how in FACT_FIELDS:
        raw = getattr(prop, attribute, None)
        rows.append(
            {
                "key": attribute,
                "label": label,
                "value": _format(raw, how),
                "known": raw not in (None, "", [], {}),
                "evidence": evidence.get(attribute),
            }
        )
    return rows


def flatten_breakdown(breakdown: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Turn ``Score.breakdown`` into flat, printable ``(Pfad, Wert)`` rows."""
    rows: list[dict[str, Any]] = []
    if isinstance(breakdown, dict):
        for key, value in breakdown.items():
            path = f"{prefix} › {key}" if prefix else str(key)
            rows.extend(flatten_breakdown(value, path))
    elif isinstance(breakdown, list | tuple):
        if all(not isinstance(v, dict | list | tuple) for v in breakdown):
            rows.append({"path": prefix, "value": ", ".join(str(v) for v in breakdown)})
        else:
            for index, value in enumerate(breakdown, start=1):
                rows.extend(flatten_breakdown(value, f"{prefix} [{index}]"))
    else:
        rows.append({"path": prefix or "Wert", "value": breakdown})
    return rows


def _cost_value(key: str, value: Any) -> str:
    if key in SQM_KEYS:
        return de_sqm(value)
    if key in RATE_KEYS:
        return f"{de_number(value, 0)} €/m²"
    return de_eur(value)


def cost_rows(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    """Label and format ``CostEstimate.breakdown`` for the Kostenmodell table.

    The three keys the table already prints from their own ``CostEstimate``
    columns (purchase price, acquisition costs, immediate capex) are skipped -
    repeating them here would just be the same number twice.
    """
    rows: list[dict[str, Any]] = []
    for key, value in breakdown.items():
        if key in SKIP_COST_KEYS:
            continue
        if key.startswith(OUTBUILDING_SQM_PREFIX):
            tag = key[len(OUTBUILDING_SQM_PREFIX) :]
            rows.append({"path": f"Fläche {tag}", "value": de_sqm(value)})
            continue
        label = COST_LABELS.get(key, key)
        rows.append({"path": label, "value": _cost_value(key, value)})
    return rows


def score_label(segment: str) -> str:
    """One path segment of ``Score.breakdown`` -> German. Unknown -> itself."""
    if segment.endswith("_max"):
        stem = segment[: -len("_max")]
        return f"{SCORE_LABELS.get(stem, stem)} (max.)"
    if segment.endswith("_score"):
        stem = segment[: -len("_score")]
        return SCORE_LABELS.get(stem, stem)
    return SCORE_LABELS.get(segment, segment)


def score_rows(breakdown: Any) -> list[dict[str, Any]]:
    """``Score.breakdown`` flattened and labelled, path segments in German."""
    rows: list[dict[str, Any]] = []
    for row in flatten_breakdown(breakdown):
        path = row["path"]
        segments = path.split(" › ") if path else []
        labelled = " › ".join(score_label(segment) for segment in segments) if segments else path
        rows.append({"path": labelled, "value": row["value"]})
    return rows


def _score_for(prop: Property, profile_hash: str):
    scores = list(prop.scores or [])
    for score in scores:
        if score.profile_hash == profile_hash:
            return score
    return scores[0] if scores else None


def _context(request: Request, session: Session, prop: Property) -> dict[str, Any]:
    profile = profile_from_query(request.query_params, session=session)
    score = _score_for(prop, profile.profile_hash)
    cost = prop.cost_estimate
    manual = manual_tier(prop)
    live_tier = infer_renovation_tier(prop, profile.renovation)
    return {
        "prop": prop,
        "profile": profile,
        "score": score,
        "score_is_current": score is not None and score.profile_hash == profile.profile_hash,
        "cost": cost,
        "facts": fact_rows(prop),
        "breakdown_rows": score_rows((score.breakdown if score else {}) or {}),
        "cost_rows": cost_rows((cost.breakdown if cost else {}) or {}),
        # The cached figure when there is one; with no cost model yet, what
        # the rules say now, so the form never sits beside an empty cell.
        "renovation_tier_label": de_tier(cost.renovation_tier if cost else live_tier.value),
        "renovation_basis": EVIDENCE_LABELS.get(renovation_evidence(prop), ""),
        # Beside a manual tier, what the listing and the age rule would say:
        # an override must not hide the fact it overrides.
        "automatic_tier_label": (
            de_tier(automatic_renovation_tier(prop, profile.renovation).value)
            if manual is not None
            else None
        ),
        "tier_choices": tier_choices(),
        "tier_selected": manual.value if manual is not None else TIER_AUTO,
        "tier_status": request.query_params.get(TIER_STATUS_PARAM),
        "tier_saved": TIER_SAVED,
        "tier_saved_not_recomputed": TIER_SAVED_NOT_RECOMPUTED,
        "capital_risk_label": (
            CAPITAL_RISK_LABELS.get(score.capital_risk, score.capital_risk) if score else None
        ),
        "timeline": history.timeline(prop),
        "timeline_sentence": history.timeline_sentence(prop),
        "sparkline": sparkline(prop),
        "chips": change_chips(prop),
        "best_url": best_url(prop),
        # What the header button may actually point at. A hand-added listing
        # has no page to open, so the exposé it was read from opens instead -
        # and when nothing can, the page says so rather than dropping a button.
        "open_link": open_link(prop, document=pick_document(prop.documents)),
        "no_link_reason": no_link_reason(prop),
        "document_href": document_href,
        "document_missing": document_missing,
        "user_states": USER_STATES,
        "title_max": USER_TITLE_MAX,
        "sources": sorted(
            prop.property_sources or [], key=lambda s: (not s.is_best, not s.is_primary_source)
        ),
        "documents": list(prop.documents or []),
        "images": list(prop.images or []),
    }


@router.get("/property/{public_id}")
def dossier(public_id: str, request: Request, session: Session = Depends(get_db)):
    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    return render(request, "pages/dossier.html", _context(request, session, prop))


@router.post("/property/{public_id}/triage")
def triage(
    public_id: str,
    request: Request,
    session: Session = Depends(get_db),
    user_state: str = Form(default="none"),
    user_note: str = Form(default=""),
):
    """Human judgement. Survives every re-run and every profile change."""
    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    state = (user_state or "none").strip().lower()
    legacy_marked = False
    if state == LEGACY_SHORTLIST_STATE:
        # A form rendered before the Merkliste existed. Honour the intent.
        if prop.shortlisted_at is None:
            prop.shortlisted_at = history.now_utc()
        state = "none"
        legacy_marked = True
    if state not in USER_STATES:
        state = "none"
    prop.user_state = None if state == "none" else state
    prop.user_note = (user_note or "").strip() or None
    session.add(prop)
    session.commit()
    session.refresh(prop)
    return render(
        request,
        "partials/triage.html",
        {"prop": prop, "user_states": USER_STATES, "saved": True, "legacy_marked": legacy_marked},
    )


def _title_refused(
    request: Request, prop: Property, draft: str, message: str, status_code: int
) -> Response:
    """Not saved, and said so: the fold stays open with the reader's draft.

    HTMX gets the partial at 200 because htmx swaps nothing on an error
    status; a plain form post gets the error page with the real status.
    """
    if request.headers.get("HX-Request"):
        context = {"prop": prop, "title_max": USER_TITLE_MAX, "draft": draft, "error": message}
        return render(request, "partials/title.html", context)
    return render(
        request,
        "pages/error.html",
        {"code": status_code, "message": f"Nicht gespeichert: {message}"},
        status_code=status_code,
    )


@router.post("/property/{public_id}/title")
def rename(
    public_id: str,
    request: Request,
    session: Session = Depends(get_db),
    title: str = Form(default=""),
    reset: str = Form(default=""),
):
    """The reader's own name for the place (``Property.user_title``).

    ``canonical_title`` is the listing's words and ``ingest`` keeps it current,
    so an edit there would be undone by the next crawl. This writes the column
    ingest never touches. An empty title, the reset button, or the listing's
    own title typed back all clear it, so the heading follows the listing
    again. See docs/DECISIONS.md entry 29.
    """
    requested = load_property(session, public_id)
    if requested is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    # A merged-away row is never rendered in a list; the name belongs on the
    # survivor, exactly as a mark does (``/merken``).
    prop = _surviving(session, requested)
    wanted = "" if reset else one_line(title)
    if len(wanted) > USER_TITLE_MAX:
        message = f"Der Titel hat {len(wanted)} Zeichen, erlaubt sind {USER_TITLE_MAX}."
        return _title_refused(request, prop, wanted, message, 400)
    # Compared cleaned to cleaned: the field is pre-filled with the listing's
    # title collapsed, and saving it untouched must not freeze a copy of it.
    prop.user_title = None if wanted in ("", one_line(prop.canonical_title)) else wanted
    session.add(prop)
    try:
        session.commit()
    except OperationalError as exc:
        session.rollback()
        if "database is locked" not in str(exc).lower():
            raise
        return _title_refused(request, prop, wanted, TITLE_LOCKED, 503)
    session.refresh(prop)
    if request.headers.get("HX-Request"):
        if prop.id != requested.id:
            # Swapping the survivor's heading into the merged-away row's page
            # would look saved and then revert on reload. Go where it lives.
            return Response(
                status_code=204, headers={"HX-Redirect": f"/property/{prop.public_id}"}
            )
        return render(
            request,
            "partials/title.html",
            {"prop": prop, "title_max": USER_TITLE_MAX, "saved": True},
        )
    return RedirectResponse(f"/property/{prop.public_id}", status_code=303)


@router.post("/property/{public_id}/sanierung")
def set_renovation_tier(
    public_id: str,
    request: Request,
    session: Session = Depends(get_db),
    tier: str = Form(default=""),
):
    """The reader's own Sanierungsstufe (``Property.user_renovation_tier``).

    Somebody who has stood in the building knows more than a tag or the age
    rule, so the value wins over every inference (``costmodel.renovation``).
    ``auto`` or an empty value clears it. The tier moves every euro figure on
    the page, so this one property's cost model and score are recomputed at
    once - a saved tier beside the old figures would read as ignored.

    Two transactions on purpose: the tier is the reader's decision and is
    committed first; the recompute is derived and may fail on a crawl's lock
    without costing the decision. ``updated_at`` moved with the tier, so the
    radar's next ``rescore_all`` picks the property up either way.
    """
    requested = load_property(session, public_id)
    if requested is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    prop = _surviving(session, requested)
    wanted = (tier or "").strip().lower()
    allowed = {t.value for t in MANUAL_TIERS}
    if wanted not in allowed and wanted not in ("", TIER_AUTO):
        words = ", ".join(label for _value, label in tier_choices())
        return render(
            request,
            "pages/error.html",
            {
                "code": 400,
                "message": (
                    f"Nicht gespeichert: „{tier}“ ist keine Sanierungsstufe. "
                    f"Erlaubt sind: {words}."
                ),
            },
            status_code=400,
        )
    prop.user_renovation_tier = wanted if wanted in allowed else None
    session.add(prop)
    try:
        session.commit()
    except OperationalError as exc:
        session.rollback()
        if "database is locked" not in str(exc).lower():
            raise
        return render(
            request,
            "pages/error.html",
            {"code": 503, "message": TIER_LOCKED},
            status_code=503,
        )

    from hofradar.scoring import rescore_property

    status = TIER_SAVED
    profile = profile_from_query(request.query_params, session=session)
    try:
        rescore_property(session, prop, profile)
        session.commit()
    except OperationalError as exc:
        session.rollback()
        if "database is locked" not in str(exc).lower():
            raise
        status = TIER_SAVED_NOT_RECOMPUTED

    target = f"/property/{prop.public_id}?{TIER_STATUS_PARAM}={status}#{COST_ANCHOR}"
    if request.headers.get("HX-Request"):
        # The tier moves figures all over the page; reload it rather than
        # patching one table and leaving the band and the score stale.
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


def _surviving(session: Session, prop: Property) -> Property:
    """The row a merge left standing, following the chain like ``ingest`` does.

    A property merged into another is never rendered anywhere - ``build_results``
    skips it - so marking one would write a bookmark the Merkliste can never
    show. The mark belongs on the survivor, which is also where ``dedupe.merge``
    carries an older mark to.
    """
    seen: set[int] = set()
    while prop.merged_into_id is not None and prop.id not in seen:
        seen.add(prop.id)
        keeper = session.get(Property, prop.merged_into_id)
        if keeper is None:
            break
        prop = keeper
    return prop


@router.post("/property/{public_id}/merken")
def merken(public_id: str, request: Request, session: Session = Depends(get_db)):
    """The Merkliste toggle. The only route that writes ``Property.
    shortlisted_at`` on a reader's action - the legacy-triage branch above and
    ``dedupe.merge`` also set it (docs/DECISIONS.md entry 21)."""
    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    prop = _surviving(session, prop)
    prop.shortlisted_at = None if prop.shortlisted_at else history.now_utc()
    session.add(prop)
    session.commit()
    session.refresh(prop)
    if request.headers.get("HX-Request"):
        return render(request, "partials/merken_button.html", {"prop": prop})
    return RedirectResponse(f"/property/{prop.public_id}", status_code=303)


@router.post("/property/{public_id}/delete")
def delete(
    public_id: str,
    request: Request,
    session: Session = Depends(get_db),
    confirm_public_id: str = Form(default=""),
):
    """The narrow escape hatch: a mis-crawl or a duplicate, gone for good.

    Triage's 📦 *Archiviert* is the answer to "get this off my radar"; this is
    the answer to "this was never a property". It takes the ``public_id`` typed
    back by hand because it destroys append-only history that no source can
    re-supply, and it refuses outright while another row was merged into this
    one - deleting the survivor of a merge would resurrect the duplicate.
    """
    from hofradar.db.backup import BackupUnavailable
    from hofradar.lifecycle import ResurrectsMergedDuplicates, delete_property

    prop = load_property(session, public_id)
    if prop is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."},
            status_code=404,
        )
    if (confirm_public_id or "").strip() != prop.public_id:
        return render(
            request,
            "pages/error.html",
            {
                "code": 400,
                "message": (
                    f"Zum Löschen muss die ID {prop.public_id} genau eingetippt werden. "
                    "Es wurde nichts gelöscht."
                ),
            },
            status_code=400,
        )
    try:
        delete_property(session, prop)
    except ResurrectsMergedDuplicates as exc:
        return render(
            request,
            "pages/error.html",
            {"code": 409, "message": f"Nicht gelöscht: {exc}"},
            status_code=409,
        )
    except BackupUnavailable as exc:
        return render(
            request,
            "pages/error.html",
            {"code": 409, "message": f"Nicht gelöscht, keine Sicherung möglich: {exc}"},
            status_code=409,
        )
    if request.headers.get("HX-Request"):
        # HTMX follows a 303 itself and would swap the radar into the dossier.
        return Response(status_code=204, headers={"HX-Redirect": "/"})
    return RedirectResponse("/", status_code=303)


@router.get("/api/property/{public_id}.json")
def api_property(public_id: str, request: Request, session: Session = Depends(get_db)):
    prop = load_property(session, public_id)
    if prop is None:
        return JSONResponse({"error": "not_found", "public_id": public_id}, status_code=404)
    profile = profile_from_query(request.query_params, session=session)
    score = _score_for(prop, profile.profile_hash)

    from hofradar.web.query import ResultRow

    row = ResultRow(
        rank=0,
        prop=prop,
        score=score,
        cost=prop.cost_estimate,
        chips=change_chips(prop),
        best_url=best_url(prop),
        price_delta_pct=history.total_price_delta_pct(prop),
        open_link=open_link(prop, document=pick_document(prop.documents)),
    )
    payload = row_to_dict(row)
    payload["evidence"] = prop.evidence or {}
    payload["user_note"] = prop.user_note
    payload["user_title"] = prop.user_title
    payload["user_renovation_tier"] = prop.user_renovation_tier
    payload["timeline"] = [
        {
            "at": event["at"].isoformat() if event["at"] else None,
            "kind": event["kind"],
            "text": event["text"],
        }
        for event in history.timeline(prop)
    ]
    payload["sources"] = [
        {
            "url": s.url,
            "role": s.role,
            "is_primary_source": s.is_primary_source,
            "is_best": s.is_best,
            "contact_name": s.contact_name,
            "contact_kind": s.contact_kind,
        }
        for s in prop.property_sources or []
    ]
    payload["documents"] = [
        {
            "title": d.title,
            "kind": d.kind,
            "issue": d.issue,
            "page_number": d.page_number,
            "url": d.document_url,
            "href": document_href(d),
            "missing": document_missing(d),
            "matched_text": d.matched_text,
        }
        for d in prop.documents or []
    ]
    return JSONResponse(payload)
