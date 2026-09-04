"""Request-scoped plumbing: the DB session, and the two sliders.

The whole product is "distance" and "total budget" as controls, so turning a
query string into a :class:`~hofradar.config.SearchProfile` is the most
load-bearing function in the web package. It is deliberately forgiving:
a slider that arrives as ``?air_km_max=banana`` must clamp to a sane default,
never 500, because the value comes from a URL a human can edit.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.config import SearchProfile
from hofradar.db.enums import HIDDEN_USER_STATES
from hofradar.web.filters import STATUS_LABELS

if TYPE_CHECKING:
    # hofradar.web.query imports ResultFilters from this module, so importing
    # ResultSet at runtime would be circular; the annotation-only import is safe
    # because of ``from __future__ import annotations`` above.
    from hofradar.web.query import ResultSet

# --------------------------------------------------------------------------- #
# Slider ranges. These are the UI contract; the model's own validators are
# wider on purpose (a saved profile may legitimately sit outside the slider).
# --------------------------------------------------------------------------- #

AIR_KM_MIN = 10.0
AIR_KM_MAX = 200.0
AIR_KM_STEP = 5.0

BUDGET_MIN = 200_000.0
BUDGET_MAX = 3_000_000.0
BUDGET_STEP = 25_000.0

LAND_MIN = 0.0
LAND_MAX = 50_000.0

RESULT_LIMIT_DEFAULT = 60
RESULT_LIMIT_MAX = 500

SORT_OPTIONS: dict[str, str] = {
    "score": "Gesamtscore",
    "price": "Kaufpreis",
    "distance": "Entfernung (Luftlinie)",
    "newest": "Neueste zuerst",
    "drop": "Größter Preisrückgang",
}

STATUS_OPTIONS: dict[str, str] = {"": "Alle Status", "alive": "Nur aktive", **STATUS_LABELS}


# --------------------------------------------------------------------------- #
# Database session
# --------------------------------------------------------------------------- #


def get_db(request: Request) -> Iterator[Session]:
    """Yield a session from the factory the app factory installed.

    Tests hand :func:`hofradar.web.app.create_app` an in-memory factory; there is
    no global engine lookup here so nothing ever touches the real SQLite file.
    """
    factory = request.app.state.session_factory
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Coercion helpers - forgiving on purpose
# --------------------------------------------------------------------------- #


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def to_float(raw: Any, default: float | None) -> float | None:
    """Parse a query value, accepting German decimal commas. Never raises."""
    if raw is None:
        return default
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip().replace(" ", "").replace(" ", "")
    if not text:
        return default
    text = text.replace(".", "") if text.count(",") == 1 and "." in text else text
    text = text.replace(",", ".")
    try:
        parsed = float(text)
    except ValueError:
        return default
    if parsed != parsed or parsed in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return parsed


def to_int(raw: Any, default: int | None) -> int | None:
    value = to_float(raw, None)
    if value is None:
        return default
    return int(value)


def to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "on", "yes", "ja", "an"}


# --------------------------------------------------------------------------- #
# Profile from query parameters
# --------------------------------------------------------------------------- #


def base_profile(session: Session | None = None, *, name: str | None = None) -> SearchProfile:
    """The profile the sliders start from.

    Priority: an explicitly named saved profile, then the saved default, then
    ``config/search.yaml``, then the model defaults. Every step is guarded -
    a broken YAML file must not take the UI down.
    """
    from hofradar.db.models import SearchProfileRecord

    if session is not None:
        record = None
        try:
            if name:
                record = session.scalar(
                    select(SearchProfileRecord).where(SearchProfileRecord.name == name)
                )
            if record is None and name is None:
                record = session.scalar(
                    select(SearchProfileRecord).where(SearchProfileRecord.is_default.is_(True))
                )
        except Exception:  # noqa: BLE001 - table may not exist on a fresh DB
            record = None
        if record is not None and record.data:
            try:
                return SearchProfile(**record.data)
            except Exception:  # noqa: BLE001 - a saved profile may predate a schema change
                pass

    try:
        from hofradar.config import load_profile

        return load_profile()
    except Exception:  # noqa: BLE001 - missing/broken config dir
        return SearchProfile()


def profile_from_query(
    params: Any,
    *,
    session: Session | None = None,
) -> SearchProfile:
    """Build the live :class:`SearchProfile` from ``request.query_params``.

    Moving the budget slider must actually move the derived purchase bands, so
    any explicit ``purchase_*_max`` override inherited from the saved profile is
    cleared unless the request sets it too. Otherwise the headline slider would
    visibly do nothing, which is exactly the bug this UI exists to avoid.
    """
    get = params.get
    profile = base_profile(session, name=(get("profile") or None))
    data = profile.model_dump()

    radius = dict(data.get("radius") or {})
    air = to_float(get("air_km_max"), None)
    if air is not None:
        radius["air_km_max"] = clamp(air, AIR_KM_MIN, AIR_KM_MAX)
    for key, low, high in (
        ("driving_km_soft_max", 1.0, 600.0),
        ("driving_km_hard_max", 1.0, 600.0),
    ):
        raw = get(key)
        if raw is not None:
            value = to_float(raw, None)
            radius[key] = None if value is None else clamp(value, low, high)
    if get("require_driving_check") is not None:
        radius["require_driving_check"] = to_bool(get("require_driving_check"))
    radius.pop("effective_driving_soft", None)
    radius.pop("effective_driving_hard", None)
    data["radius"] = radius

    budget = dict(data.get("budget") or {})
    total = to_float(get("total_budget_max"), None)
    if total is not None:
        budget["total_budget_max"] = clamp(total, BUDGET_MIN, BUDGET_MAX)
        # The slider owns the purchase bands unless the caller overrides them.
        for band in ("purchase_target_max", "purchase_negotiation_max", "purchase_hard_max"):
            budget[band] = None
    for band in ("purchase_target_max", "purchase_negotiation_max", "purchase_hard_max"):
        raw = get(band)
        if raw is not None:
            value = to_float(raw, None)
            budget[band] = None if value is None else clamp(value, 0.0, BUDGET_MAX)
    for key in (
        "effective_purchase_target_max",
        "effective_purchase_negotiation_max",
        "effective_purchase_hard_max",
        "effective_total_hard_max",
        "effective_total_exceptional_max",
    ):
        budget.pop(key, None)
    data["budget"] = budget

    # ``min_land_sqm`` is deliberately NOT folded into the profile: it is a
    # display filter, and letting it move the profile hash would make the hash
    # jump between a plain page load and the first HTMX request (which always
    # submits the field, even at zero).

    try:
        return SearchProfile(**data)
    except Exception:  # noqa: BLE001 - clamping should make this unreachable
        return profile


# --------------------------------------------------------------------------- #
# Non-scoring display filters
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ResultFilters:
    """Everything in the control panel that is *not* part of the profile hash."""

    min_land_sqm: float | None = None
    status: str = ""
    verified_only: bool = False
    outbuildings_only: bool = False
    town: str = ""
    sort: str = "score"
    #: The machine's scoring gate (``Score.rejected``), recomputed per profile.
    include_rejected: bool = False
    #: The human's triage (``Property.user_state``), which survives every re-run.
    include_hidden: bool = False
    user_state: str = ""
    #: The Merkliste view: only rows the reader has marked. Set by
    #: ``routes/merkliste.py``, never remembered in the filter cookie.
    shortlisted_only: bool = False
    limit: int = RESULT_LIMIT_DEFAULT
    raw: dict[str, str] = field(default_factory=dict)

    def as_scoring_filters(self) -> dict[str, Any]:
        """The dict handed to ``hofradar.scoring.ranked_properties(filters=...)``.

        Every key here must be in ``hofradar.scoring.engine.SUPPORTED_FILTERS``;
        one that is not makes ``_apply_filters`` raise, which the lazy loader
        reports as a missing module and the radar answers with unscored rows.
        """
        payload: dict[str, Any] = {}
        if self.min_land_sqm:
            payload["min_land_sqm"] = self.min_land_sqm
        if self.status:
            payload["status"] = self.status
        if self.verified_only:
            payload["verified_only"] = True
        if self.outbuildings_only:
            payload["has_outbuildings"] = True
        if self.town:
            payload["q"] = self.town
        if self.user_state:
            payload["user_state"] = self.user_state
        if self.shortlisted_only:
            payload["shortlisted"] = True
        return payload

    def query_string(self, profile: SearchProfile, **overrides: Any) -> str:
        """Rebuild a canonical query string (used for export/permalinks)."""
        values: dict[str, Any] = {
            "air_km_max": profile.radius.air_km_max,
            "total_budget_max": profile.budget.total_budget_max,
            "min_land_sqm": self.min_land_sqm or 0,
            "status": self.status,
            "verified_only": int(self.verified_only),
            "outbuildings_only": int(self.outbuildings_only),
            "q": self.town,
            "sort": self.sort,
            "include_rejected": int(self.include_rejected),
            "include_hidden": int(self.include_hidden),
            "merkliste": int(self.shortlisted_only),
        }
        values.update(overrides)
        return urlencode({k: v for k, v in values.items() if v not in ("", None)})


def filters_from_query(params: Any) -> ResultFilters:
    get = params.get
    sort = (get("sort") or "score").strip().lower()
    if sort not in SORT_OPTIONS:
        sort = "score"
    status = (get("status") or "").strip().lower()
    if status not in STATUS_OPTIONS:
        status = ""
    land = to_float(get("min_land_sqm"), 0.0) or 0.0
    limit = to_int(get("limit"), RESULT_LIMIT_DEFAULT) or RESULT_LIMIT_DEFAULT
    user_state = (get("user_state") or "").strip().lower()[:24]
    # Naming a hidden state is an explicit ask to see it; letting the default
    # hide veto that would AND the two into an empty, unexplained result.
    include_hidden = to_bool(get("include_hidden")) or user_state in HIDDEN_USER_STATES
    return ResultFilters(
        min_land_sqm=clamp(land, LAND_MIN, LAND_MAX) or None,
        status=status,
        verified_only=to_bool(get("verified_only")),
        outbuildings_only=to_bool(get("outbuildings_only")),
        town=(get("q") or "").strip()[:120],
        sort=sort,
        include_rejected=to_bool(get("include_rejected")),
        include_hidden=include_hidden,
        user_state=user_state,
        shortlisted_only=to_bool(get("merkliste")),
        limit=int(clamp(limit, 1, RESULT_LIMIT_MAX)),
        raw={k: v for k, v in dict(params).items() if isinstance(v, str)},
    )


# --------------------------------------------------------------------------- #
# Template rendering
# --------------------------------------------------------------------------- #


def render(request: Request, template_name: str, context: dict[str, Any], **kwargs: Any):
    """Render through the app's Jinja environment with the shared base context."""
    templates = request.app.state.templates
    payload = {"request": request, **shared_context(request), **context}
    return templates.TemplateResponse(request, template_name, payload, **kwargs)


def shared_context(request: Request) -> dict[str, Any]:
    return {
        "sort_options": SORT_OPTIONS,
        "status_options": STATUS_OPTIONS,
        "air_km_min": AIR_KM_MIN,
        "air_km_max_slider": AIR_KM_MAX,
        "air_km_step": AIR_KM_STEP,
        "budget_min": BUDGET_MIN,
        "budget_max_slider": BUDGET_MAX,
        "budget_step": BUDGET_STEP,
        "nav_path": request.url.path,
        "auth_enabled": _auth_enabled(),
    }


def _auth_enabled() -> bool:
    """Templates only show the logout control when there is a session to end."""
    from hofradar.web import auth

    return auth.password_configured()


# --------------------------------------------------------------------------- #
# Filter memory - one cookie, and a 303 so the URL stays the truth
# --------------------------------------------------------------------------- #

FILTER_COOKIE = "hofradar_radar"
FILTER_COOKIE_MAX_AGE = 365 * 24 * 3600
FILTER_COOKIE_MAX_LEN = 1000

#: The keys ``ResultFilters.query_string`` emits - the only ones remembered.
REMEMBERED_KEYS: frozenset[str] = frozenset(
    {
        "air_km_max", "total_budget_max", "min_land_sqm", "status", "verified_only",
        "outbuildings_only", "q", "sort", "include_rejected", "include_hidden",
    }
)
#: The two sliders: what the scores depend on. The Merkliste applies only these.
PROFILE_KEYS: frozenset[str] = frozenset({"air_km_max", "total_budget_max"})


def has_control_params(params: Mapping[str, str]) -> bool:
    return any(key in params for key in REMEMBERED_KEYS)


def _parse_saved(raw: str | None) -> list[tuple[str, str]]:
    if not raw or len(raw) > FILTER_COOKIE_MAX_LEN:
        return []
    pairs = [(k, v) for k, v in parse_qsl(raw, keep_blank_values=False) if k in REMEMBERED_KEYS]
    return pairs


def saved_query(request: Request) -> str | None:
    pairs = _parse_saved(request.cookies.get(FILTER_COOKIE))
    return urlencode(pairs) if pairs else None


def saved_profile_params(request: Request) -> dict[str, str]:
    return {k: v for k, v in _parse_saved(request.cookies.get(FILTER_COOKIE)) if k in PROFILE_KEYS}


def remember_query(response: Response, results: ResultSet) -> None:
    value = results.filters.query_string(results.profile)
    response.set_cookie(
        FILTER_COOKIE, value, max_age=FILTER_COOKIE_MAX_AGE, path="/",
        samesite="lax", httponly=True,
    )


def forget_query(response: Response) -> None:
    response.delete_cookie(FILTER_COOKIE, path="/")


def redirect_to_saved(request: Request) -> RedirectResponse | None:
    """The 303 that makes a bare ``/`` show the remembered sliders, or None."""
    if "reset" in request.query_params or has_control_params(request.query_params):
        return None
    saved = saved_query(request)
    if not saved:
        return None
    return RedirectResponse(f"{request.url.path}?{saved}", status_code=303)
