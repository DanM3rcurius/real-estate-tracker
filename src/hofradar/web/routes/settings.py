"""Saved search profiles.

The sliders on the Radar are a *temporary* profile living in the query string.
This page is where one gets a name and is written to ``SearchProfileRecord`` so
next Sunday starts from the same place. The profile hash is shown everywhere,
because that hash is what the score cache is keyed on and the user should be
able to see when they have changed the question they are asking.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.config import SearchProfile
from hofradar.db.models import SearchProfileRecord
from hofradar.web.deps import base_profile, get_db, render, to_bool, to_float, to_int

router = APIRouter(tags=["settings"])

#: Editable scalar fields, grouped for the form. (path, label, kind, step)
FORM_SECTIONS: tuple[tuple[str, str, tuple[tuple[str, str, str, str], ...]], ...] = (
    (
        "center",
        "Suchzentrum",
        (
            ("center.name", "Bezeichnung", "text", ""),
            ("center.lat", "Breitengrad", "number", "0.0001"),
            ("center.lon", "Längengrad", "number", "0.0001"),
        ),
    ),
    (
        "radius",
        "Entfernung",
        (
            ("radius.air_km_max", "Luftlinie max. (km)", "number", "1"),
            ("radius.driving_km_soft_max", "Fahrstrecke weich (km, leer = abgeleitet)", "number", "1"),
            ("radius.driving_km_hard_max", "Fahrstrecke hart (km, leer = abgeleitet)", "number", "1"),
            ("radius.driving_factor_soft", "Faktor weich", "number", "0.01"),
            ("radius.driving_factor_hard", "Faktor hart", "number", "0.01"),
        ),
    ),
    (
        "budget",
        "Budget",
        (
            ("budget.total_budget_max", "Gesamtbudget (€)", "number", "25000"),
            ("budget.total_budget_exceptional_max", "Ausnahmegrenze (€, leer = abgeleitet)", "number", "25000"),
            ("budget.total_budget_hard_max", "Hartes Limit (€, leer = abgeleitet)", "number", "25000"),
            ("budget.purchase_target_max", "Kaufpreisziel (€, leer = abgeleitet)", "number", "10000"),
            ("budget.purchase_negotiation_max", "Verhandlungsgrenze (€, leer = abgeleitet)", "number", "10000"),
            ("budget.purchase_hard_max", "Kaufpreis hart (€, leer = abgeleitet)", "number", "10000"),
            ("budget.purchase_share_of_total", "Kaufpreisanteil am Budget", "number", "0.005"),
            ("budget.grunderwerbsteuer_pct", "Grunderwerbsteuer", "number", "0.001"),
            ("budget.notar_pct", "Notar", "number", "0.001"),
            ("budget.grundbuch_pct", "Grundbuch", "number", "0.001"),
            ("budget.makler_pct", "Makler", "number", "0.001"),
        ),
    ),
    (
        "land",
        "Grundstück",
        (
            ("land.preferred_min_sqm", "Mindestfläche (m²)", "number", "100"),
            ("land.strong_min_sqm", "Starke Fläche ab (m²)", "number", "100"),
        ),
    ),
    (
        "weights",
        "Gewichte (werden normalisiert)",
        (
            ("weights.fit", "FIT", "number", "0.01"),
            ("weights.deal", "DEAL", "number", "0.01"),
            ("weights.hidden", "HIDDEN", "number", "0.01"),
            ("weights.freshness", "FRESH", "number", "0.01"),
            ("weights.confidence", "CONF", "number", "0.01"),
        ),
    ),
    (
        "gates",
        "Filter und Grenzen",
        (
            ("gates.min_confidence_for_shortlist", "Konfidenz für Shortlist", "number", "1"),
            ("gates.min_confidence_to_keep", "Konfidenz zum Behalten", "number", "1"),
            ("gates.renovation_to_price_risk_ratio", "Sanierung/Preis-Risikofaktor", "number", "0.1"),
            ("gates.exceptional_development_min", "Entwicklungspotenzial min.", "number", "0.5"),
            ("gates.shortlist_size", "Shortlist-Größe", "number", "1"),
            ("gates.llm_review_size", "LLM-Prüfumfang", "number", "1"),
        ),
    ),
)

BOOLEAN_FIELDS = (
    ("radius.require_driving_check", "Fahrstrecke muss geprüft sein"),
    ("gates.reject_removed", "Verschwundene Inserate ausschließen"),
    ("gates.reject_unrouted", "Objekte ohne Route ausschließen"),
)

LIST_FIELDS = (
    ("property_types", "Objektarten (eine pro Zeile)"),
    ("preferred_features", "Bevorzugte Merkmale"),
    ("exclude", "Ausschlüsse"),
)

INT_PATHS = {"gates.shortlist_size", "gates.llm_review_size"}


def _get_path(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Drop pydantic computed fields so the dict round-trips into SearchProfile."""
    for section, keys in (
        ("radius", ("effective_driving_soft", "effective_driving_hard")),
        (
            "budget",
            (
                "effective_purchase_target_max",
                "effective_purchase_negotiation_max",
                "effective_purchase_hard_max",
                "effective_total_hard_max",
                "effective_total_exceptional_max",
            ),
        ),
    ):
        node = data.get(section)
        if isinstance(node, dict):
            for key in keys:
                node.pop(key, None)
    return data


def _records(session: Session) -> list[SearchProfileRecord]:
    return list(session.scalars(select(SearchProfileRecord).order_by(SearchProfileRecord.name)))


def _context(request: Request, session: Session, profile: SearchProfile, **extra: Any):
    data = _clean(profile.model_dump())
    return {
        "profile": profile,
        "profile_data": data,
        "records": _records(session),
        "form_sections": FORM_SECTIONS,
        "boolean_fields": BOOLEAN_FIELDS,
        "list_fields": LIST_FIELDS,
        "get_path": lambda path: _get_path(data, path),
        "saved": extra.pop("saved", False),
        "message": extra.pop("message", None),
        **extra,
    }


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_db)):
    name = request.query_params.get("edit")
    profile = base_profile(session, name=name) if name else base_profile(session)
    return render(request, "pages/settings.html", _context(request, session, profile))


@router.post("/settings")
async def settings_save(request: Request, session: Session = Depends(get_db)):
    """Persist the whole SearchProfile under a name."""
    form = await request.form()
    base = base_profile(session, name=(form.get("base") or None))
    data = _clean(base.model_dump())

    for _, _, fields in FORM_SECTIONS:
        for path, _label, kind, _step in fields:
            if path not in form:
                continue
            raw = (form.get(path) or "").strip()
            if kind == "text":
                _set_path(data, path, raw)
                continue
            if raw == "":
                _set_path(data, path, None)
                continue
            value = to_int(raw, None) if path in INT_PATHS else to_float(raw, None)
            _set_path(data, path, value)

    for path, _label in BOOLEAN_FIELDS:
        _set_path(data, path, to_bool(form.get(path)))

    for path, _label in LIST_FIELDS:
        if path in form:
            raw = form.get(path) or ""
            _set_path(data, path, [line.strip() for line in raw.splitlines() if line.strip()])

    name = (form.get("name") or "").strip() or base.name or "default"
    data["name"] = name

    try:
        profile = SearchProfile(**data)
    except Exception as exc:  # noqa: BLE001 - show the validation error, keep the form
        return render(
            request,
            "pages/settings.html",
            _context(request, session, base, message=f"Nicht gespeichert: {exc}"),
            status_code=400,
        )

    record = session.scalar(select(SearchProfileRecord).where(SearchProfileRecord.name == name))
    if record is None:
        record = SearchProfileRecord(name=name)
        session.add(record)
    record.data = _clean(profile.model_dump(mode="json"))
    record.profile_hash = profile.profile_hash
    if to_bool(form.get("is_default")):
        for other in _records(session):
            other.is_default = other.name == name
        record.is_default = True
    session.commit()

    return render(
        request,
        "pages/settings.html",
        _context(
            request,
            session,
            profile,
            saved=True,
            message=f"Profil „{name}“ gespeichert (Hash {profile.profile_hash}).",
        ),
    )


@router.post("/settings/{record_id}/default")
def settings_default(record_id: int, session: Session = Depends(get_db)) -> RedirectResponse:
    record = session.get(SearchProfileRecord, record_id)
    if record is not None:
        for other in _records(session):
            other.is_default = other.id == record.id
        session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/{record_id}/duplicate")
def settings_duplicate(record_id: int, session: Session = Depends(get_db)) -> RedirectResponse:
    record = session.get(SearchProfileRecord, record_id)
    if record is not None:
        name = f"{record.name} (Kopie)"
        index = 2
        while session.scalar(select(SearchProfileRecord).where(SearchProfileRecord.name == name)):
            name = f"{record.name} (Kopie {index})"
            index += 1
        data = dict(record.data or {})
        data["name"] = name
        session.add(
            SearchProfileRecord(
                name=name, data=data, profile_hash=record.profile_hash, is_default=False
            )
        )
        session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/{record_id}/delete")
def settings_delete(record_id: int, session: Session = Depends(get_db)) -> RedirectResponse:
    record = session.get(SearchProfileRecord, record_id)
    if record is not None:
        session.delete(record)
        session.commit()
    return RedirectResponse("/settings", status_code=303)
