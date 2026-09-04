# UI Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the six reader-facing fixes and issue #14 from the spec: filter memory, a Merkliste, a German dossier with the renovation basis, a collapsed score breakdown, a de-duplicated Verlauf, and a search box that finds postcodes.

**Architecture:** Server-rendered FastAPI + Jinja + HTMX; no new JS. Filter memory is one cookie and a `303`. The Merkliste is one nullable timestamp column on `Property` plus one toggle route. German copy is written at the source (cost-model assumptions) or mapped at render time (breakdown keys, statuses). #14 moves the search-box matcher into Python inside the ranking engine, shared with the degraded path.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, Jinja2, HTMX, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-04-ui-refinements-design.md` — read it first; every task below argues from it.

## Global Constraints

- `from __future__ import annotations`, full type hints, 100 columns, ruff clean (`ruff check src tests`).
- UI copy German; identifiers, comments, docstrings English. Emoji only in UI copy.
- No magic numbers in function bodies: module constants.
- Tests never touch the network or `data/hofradar.sqlite3`; use the `tests/web/conftest.py` fixtures (`client`, `db`, `seeded`, `make_property`, `default_profile`).
- Do **not** run the suite with `HOFRADAR_OFFLINE=1`.
- Run the suite as `PYTHONPATH=src python -m pytest -q`.
- Invariant 1: only `hofradar.lifecycle.ingest` writes `Property` *facts*. `shortlisted_at` is triage-class data (like `user_state`) and may be written by its web route.
- Commit after every green step with a conventional message (`feat(web): …`, `fix(scoring): …`, `docs: …`).
- Waves (from the spec's premortem): Tasks 1-4 are wave 1 and touch disjoint files; Task 5 is wave 2 and starts only once wave 1 is merged; Task 6 is docs.

Seed data every web test can rely on (`tests/web/conftest.py`, fixture `seeded`):

| public_id | canonical_title | town | postcode |
|---|---|---|---|
| HF-0001 | Hofstelle mit Stadel | Bad Feilnbach | 83075 |
| HF-0002 | Vierseithof im Chiemgau | Traunstein | 83278 |
| HF-0003 | Sacherl mit Obstgarten | Irschenberg | 83737 |
| HF-0004 | Saniertes Sacherl | Bruckmühl | 83052 |

---

### Task 1 (wave 1, A): Filter memory cookie

**Files:**
- Modify: `src/hofradar/web/deps.py` (append new helpers after `filters_from_query`)
- Modify: `src/hofradar/web/routes/radar.py` (`radar`, `api_results`)
- Modify: `src/hofradar/web/routes/map_view.py` (`map_page`)
- Modify: `src/hofradar/web/templates/partials/controls.html` (`.controls__links`)
- Test: `tests/web/test_filter_memory.py` (new)

**Interfaces:**
- Consumes: `ResultFilters.query_string(profile)` (deps.py), `render()` (deps.py).
- Produces, in `deps.py`:
  - `FILTER_COOKIE = "hofradar_radar"`, `FILTER_COOKIE_MAX_AGE = 365 * 24 * 3600`, `FILTER_COOKIE_MAX_LEN = 1000`
  - `REMEMBERED_KEYS: frozenset[str]` = `{"air_km_max","total_budget_max","min_land_sqm","status","verified_only","outbuildings_only","q","sort","include_rejected","include_hidden"}`
  - `PROFILE_KEYS: frozenset[str]` = `{"air_km_max","total_budget_max"}`
  - `has_control_params(params) -> bool` — any `REMEMBERED_KEYS` key present.
  - `saved_query(request) -> str | None` — the cookie's query string if valid, else `None`.
  - `saved_profile_params(request) -> dict[str, str]` — only `PROFILE_KEYS` from the cookie (Task 5 uses this).
  - `remember_query(response, results) -> None` — sets the cookie to `results.filters.query_string(results.profile)`.
  - `forget_query(response) -> None` — deletes the cookie.
  - `redirect_to_saved(request) -> RedirectResponse | None` — the `303` when the request has no control params, no `reset`, and a valid cookie.

- [ ] **Step 1: Write the failing tests**

```python
"""The radar remembers its sliders across navigation - in a cookie, via a 303.

The URL stays the truth: a bare ``/`` with a remembered state redirects to the
full query string, so permalinks, ``app.js``'s address-bar sync and the map
link all keep working unchanged.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hofradar.web.deps import FILTER_COOKIE


def test_a_filtered_request_sets_the_cookie(client, seeded):
    response = client.get("/?air_km_max=50&total_budget_max=700000&q=Traun")
    assert response.status_code == 200
    assert FILTER_COOKIE in response.cookies
    assert "air_km_max=50" in response.cookies[FILTER_COOKIE]
    assert "q=Traun" in response.cookies[FILTER_COOKIE]


def test_htmx_results_set_the_cookie_too(client, seeded):
    response = client.get("/api/results?air_km_max=45&total_budget_max=650000")
    assert FILTER_COOKIE in response.cookies


def test_bare_radar_redirects_to_the_remembered_state(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000&q=Traun")
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/?")
    assert "air_km_max=50" in location and "q=Traun" in location


def test_bare_map_redirects_as_well(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000")
    response = client.get("/map", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/map?")


def test_reset_clears_the_cookie_and_renders_defaults(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000")
    response = client.get("/?reset=1", follow_redirects=False)
    assert response.status_code == 200
    # An expired cookie is a Set-Cookie with max-age=0 / empty value.
    assert client.cookies.get(FILTER_COOKIE) in (None, "")
    assert client.get("/", follow_redirects=False).status_code == 200


def test_a_garbage_cookie_is_ignored_not_looped(app, seeded):
    with TestClient(app) as fresh:
        fresh.cookies.set(FILTER_COOKIE, "<script>alert(1)</script>")
        response = fresh.get("/", follow_redirects=False)
        assert response.status_code == 200
    with TestClient(app) as fresh:
        fresh.cookies.set(FILTER_COOKIE, "x" * 5000)
        assert fresh.get("/", follow_redirects=False).status_code == 200


def test_explicit_parameters_beat_the_cookie(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000")
    response = client.get("/?air_km_max=120", follow_redirects=False)
    assert response.status_code == 200
    assert 'value="120.0"' in response.text or 'value="120"' in response.text


def test_controls_offer_a_reset_link(client, seeded):
    assert 'href="/?reset=1"' in client.get("/").text
```

- [ ] **Step 2: Run them, expect ImportError on `FILTER_COOKIE`**

`PYTHONPATH=src python -m pytest -q tests/web/test_filter_memory.py`

- [ ] **Step 3: Implement the helpers in `deps.py`**

Append after `filters_from_query`:

```python
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


def has_control_params(params: Any) -> bool:
    return any(key in params for key in REMEMBERED_KEYS)


def _parse_saved(raw: str | None) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl

    if not raw or len(raw) > FILTER_COOKIE_MAX_LEN:
        return []
    pairs = [(k, v) for k, v in parse_qsl(raw, keep_blank_values=False) if k in REMEMBERED_KEYS]
    return pairs


def saved_query(request: Request) -> str | None:
    from urllib.parse import urlencode

    pairs = _parse_saved(request.cookies.get(FILTER_COOKIE))
    return urlencode(pairs) if pairs else None


def saved_profile_params(request: Request) -> dict[str, str]:
    return {k: v for k, v in _parse_saved(request.cookies.get(FILTER_COOKIE)) if k in PROFILE_KEYS}


def remember_query(response: Any, results: Any) -> None:
    value = results.filters.query_string(results.profile)
    response.set_cookie(
        FILTER_COOKIE, value, max_age=FILTER_COOKIE_MAX_AGE, path="/",
        samesite="lax", httponly=True,
    )


def forget_query(response: Any) -> None:
    response.delete_cookie(FILTER_COOKIE, path="/")


def redirect_to_saved(request: Request):
    """The 303 that makes a bare ``/`` show the remembered sliders, or None."""
    from fastapi.responses import RedirectResponse

    if "reset" in request.query_params or has_control_params(request.query_params):
        return None
    saved = saved_query(request)
    if not saved:
        return None
    return RedirectResponse(f"{request.url.path}?{saved}", status_code=303)
```

Note `saved_query` re-encodes only known keys, so a hostile cookie can never be reflected: the redirect target is built from the allowlisted pairs, not the raw string.

- [ ] **Step 4: Wire the routes**

`radar.py`:

```python
@router.get("/")
def radar(request: Request, session: Session = Depends(get_db)):
    redirect = redirect_to_saved(request)
    if redirect is not None:
        return redirect
    results = resolve(request, session)
    response = render(request, "pages/radar.html", result_context(request, results))
    if "reset" in request.query_params:
        forget_query(response)
    elif has_control_params(request.query_params):
        remember_query(response, results)
    return response


@router.get("/api/results")
def api_results(request: Request, session: Session = Depends(get_db)):
    results = resolve(request, session)
    response = render(request, "partials/results.html", result_context(request, results))
    remember_query(response, results)
    return response
```

`map_view.py`: add the same `redirect_to_saved` guard at the top of `map_page` and `remember_query` when `has_control_params`.

`controls.html`, inside `.controls__links`, after the JSON link:

```html
<a href="/?reset=1">↺ Filter zurücksetzen</a>
```

- [ ] **Step 5: Run the new tests and the whole web suite**

`PYTHONPATH=src python -m pytest -q tests/web` — all green. `ruff check src tests`.

- [ ] **Step 6: Commit**

`git commit -am "feat(web): remember the radar's filters in a cookie and redirect to them"`

---

### Task 2 (wave 1, C): German dossier, renovation basis, collapsed breakdown

**Files:**
- Modify: `src/hofradar/costmodel/estimator.py` (every `assumptions.append(...)`)
- Modify: `src/hofradar/web/filters.py` (`STATUS_LABELS`, `TIER_LABELS`, `de_status`, `de_tier`, register in `JINJA_FILTERS`)
- Modify: `src/hofradar/web/deps.py` — **only** replace the literal German strings in `STATUS_OPTIONS` with a build from `filters.STATUS_LABELS` (keep `""` and `"alive"` entries)
- Modify: `src/hofradar/web/routes/dossier.py` (`COST_LABELS`, `SCORE_LABELS`, `CAPITAL_RISK_LABELS`, `EVIDENCE_LABELS`, `cost_rows()`, `score_rows()`, `_context`)
- Modify: `src/hofradar/web/templates/pages/dossier.html` (Bewertung, Kostenmodell sections)
- Modify: `src/hofradar/web/templates/partials/macros.html` (`status_chip`, `cost_band`)
- Modify: `src/hofradar/web/static/app.css` (`.fold`)
- Modify: `tests/scoring/test_costmodel.py:141` (needle)
- Test: `tests/web/test_dossier_copy.py` (new)

**Interfaces:**
- Produces in `web/filters.py`:
  - `STATUS_LABELS: dict[str, str]` keyed by `ListingStatus` value: discovered→Entdeckt, verified→Verifiziert, active→Aktiv, price_changed→Preis geändert, stale→Veraltet, foreclosure→Zwangsversteigerung, off_market_signal→Off-Market-Signal, removed→Entfernt, expired→Anzeige abgelaufen, sold→Verkauft (copy the words from `deps.STATUS_OPTIONS`; check `ListingStatus` in `db/enums.py` for any member missing here and add it).
  - `TIER_LABELS = {"light": "leicht", "medium": "mittel", "heavy": "schwer", "complete": "Kernsanierung", "unknown": "unbekannt"}`
  - `de_status(value) -> str` (unknown → value unchanged, None → "unbekannt"); `de_tier(value) -> str` likewise.
- Task 3 imports `de_status` from `hofradar.web.filters`.

- [ ] **Step 1: Write the failing tests**

```python
"""The dossier speaks German, names the renovation basis, and folds the noise."""

from __future__ import annotations

from hofradar.web.filters import de_status, de_tier


def test_status_and_tier_words():
    assert de_status("discovered") == "Entdeckt"
    assert de_status("unheard_of") == "unheard_of"
    assert de_tier("heavy") == "schwer"
    assert de_tier(None) == "unbekannt"


def test_dossier_cost_section_is_german(client, seeded):
    html = client.get("/property/HF-0001").text
    assert "Sanierungsstufe" in html
    assert "laut Inserat" in html or "aus Baujahr geschätzt" in html
    assert "Renovation tier" not in html
    assert "house_low" not in html and "rate_per_sqm_low" not in html
    assert "Haus (niedrig)" in html


def test_breakdown_is_folded_and_german(client, seeded):
    html = client.get("/property/HF-0001").text
    assert "<details class=\"fold\"" in html
    assert "Punkteaufschlüsselung" in html
    assert "geography_score" not in html
    assert "Lage" in html


def test_status_chip_is_german(client, seeded):
    html = client.get("/").text
    assert "chip--status-active" in html
    assert ">Aktiv<" in html


def test_assumptions_are_written_in_german(db, seeded, default_profile):
    from hofradar.costmodel import estimate_costs

    cost = estimate_costs(seeded["near"], default_profile)
    joined = " ".join(cost.assumptions)
    assert "Sanierungsstufe" in joined
    assert "Renovation" not in joined and "assumed" not in joined
```

Check what key `seeded` uses for HF-0001 (`seeded["near"]` per `conftest.py:299`) and the fixture's `listing_status`; adjust `chip--status-active` to the seeded value if it differs.

- [ ] **Step 2: Run, expect failures on the missing filters**

- [ ] **Step 3: Translate the assumptions in `estimator.py`**

Replace each string; keep the numbers and formatting:

- `Renovation tier {tier} at {lo}-{hi} EUR/m2 of living area.` → `Sanierungsstufe {TIER_WORD} mit {lo:.0f}–{hi:.0f} €/m² Wohnfläche.` where `TIER_WORD` comes from a module-level `TIER_WORDS` dict in `estimator.py` (light→leicht, medium→mittel, heavy→schwer, complete→Kernsanierung, unknown→unbekannt; the estimator must not import the web package).
- `Living area not stated; assumed …` → `Wohnfläche nicht angegeben; angenommen {derived:.0f} m², also {frac:.0%} der genannten Nutzfläche von {usable:.0f} m².`
- `Neither living nor usable area stated; …` → `Weder Wohn- noch Nutzfläche angegeben; angenommen wird das typische bayerische Hofhaus mit {n:.0f} m².`
- `Outbuilding areas assumed from typical Bavarian sizes: …` → `Nebengebäudeflächen aus typischen bayerischen Größen angenommen: {detail}.`
- `No outbuildings tagged; …` → `Keine Nebengebäude erfasst; keine Sanierung von Nebengebäuden eingeplant.`
- `Roof: …` → `Dach: {living:.0f} m² Wohnfläche über {storeys} Geschosse ergeben {footprint:.0f} m² Grundfläche; der durchgehende Hoffirst deckt das {factor}-Fache, also {roof:.0f} m² zu {rate:.0f} €/m².`
- `Outbuildings: …` → `Nebengebäude: {sqm:.0f} m² zu {rate:.0f} €/m², um sie wetterfest und nutzbar zu machen.`
- `Utilities …` → `Haustechnik (Heizung, Elektrik, Wasser, Abwasser) pauschal {n:,.0f} €, unabhängig von der Größe.`
- `Contingency …` → `Puffer von {pct:.0%} auf jede Sanierungsposition.`
- `Immediate capex …` → `Sofortmaßnahmen von {n:,.0f} €, bevor das Gebäude überhaupt nutzbar ist (Zufahrt, Sicherung, Notreparaturen).`
- `No asking price known; …` → `Kein Angebotspreis bekannt; die Summen sind nur Sanierung und Sofortmaßnahmen und eine Untergrenze, keine Schätzung.`
- `Acquisition side costs …` → `Erwerbsnebenkosten von {pct:.2%} des Kaufpreises (Grunderwerbsteuer, Notar, Grundbuch, Makler) = {n:,.0f} €.`

Then fix `tests/scoring/test_costmodel.py:141` to assert `"typische bayerische Hofhaus" in a` and grep `tests/` for any other English assumption needle (`grep -rn "assumed\|Renovation tier" tests`).

- [ ] **Step 4: Filters and labels**

`web/filters.py`: add `STATUS_LABELS`, `TIER_LABELS`, `de_status`, `de_tier`; register both in `JINJA_FILTERS`. `deps.py`: `STATUS_OPTIONS = {"": "Alle Status", "alive": "Nur aktive", **STATUS_LABELS}` (import from `hofradar.web.filters`; check for import cycles - `filters.py` must not import `deps.py`).

`dossier.py`:

```python
COST_LABELS: dict[str, str] = {
    "purchase": "Kaufpreis", "acquisition": "Erwerbsnebenkosten",
    "house": "Haus (Mitte)", "house_low": "Haus (niedrig)", "house_high": "Haus (hoch)",
    "roof": "Dach", "outbuildings": "Nebengebäude", "utilities": "Haustechnik",
    "contingency": "Puffer (Mitte)", "contingency_low": "Puffer (niedrig)",
    "contingency_high": "Puffer (hoch)", "immediate_capex": "Sofortmaßnahmen",
    "living_sqm_used": "Wohnfläche angesetzt", "roof_sqm_used": "Dachfläche angesetzt",
    "outbuilding_sqm_used": "Nebengebäudefläche angesetzt",
    "rate_per_sqm_low": "Satz €/m² (niedrig)", "rate_per_sqm_mid": "Satz €/m² (Mitte)",
    "rate_per_sqm_high": "Satz €/m² (hoch)",
}
OUTBUILDING_SQM_PREFIX = "outbuilding_sqm_"
SQM_KEYS = frozenset({"living_sqm_used", "roof_sqm_used", "outbuilding_sqm_used"})
RATE_KEYS = frozenset({"rate_per_sqm_low", "rate_per_sqm_mid", "rate_per_sqm_high"})

SCORE_LABELS: dict[str, str] = {
    "fit": "Passung", "deal": "Preis-Leistung", "hidden": "Verborgenheit",
    "freshness": "Frische", "confidence": "Belastbarkeit", "geography": "Lage",
    "price": "Preis", "land": "Grund", "substance": "Substanz", "seclusion": "Alleinlage",
    "development": "Entwicklung", "outbuildings": "Nebengebäude",
}
CAPITAL_RISK_LABELS = {"low": "gering", "moderate": "mäßig", "high": "hoch", "extreme": "extrem"}
EVIDENCE_LABELS = {"observed": "laut Inserat", "inferred": "aus Baujahr geschätzt"}
```

`cost_rows(breakdown) -> list[dict]`: label via `COST_LABELS`, `Fläche {tag}` for the prefix keys, value formatted with `de_sqm` for `SQM_KEYS`, `f"{de_number(v, 0)} €/m²"` for `RATE_KEYS`, `de_eur` otherwise. Keys in `SKIP_COST_KEYS = {"purchase","acquisition","immediate_capex"}` are skipped because the table already prints them.

`score_label(path_segment)`: strip `_score` → lookup; `_max` → lookup of the stem + ` (max.)`; unknown → segment unchanged. Apply per segment in `flatten_breakdown` output (`" › ".join`). Also open the spec's `SCORE_LABELS` list to double-check against the actual keys in `scoring/fit.py:340-354`, `deal.py`, `hidden.py`; add any obvious ones you see, leave the rest passing through.

`_context` adds: `"renovation_tier_label": de_tier(cost.renovation_tier) if cost else None`, `"renovation_basis": EVIDENCE_LABELS.get(renovation_evidence(prop), "")` (import `renovation_evidence` from `hofradar.costmodel`), `"capital_risk_label"`, and replaces `cost_rows`/`breakdown_rows` with the labelled versions.

- [ ] **Step 5: Templates and CSS**

`dossier.html` Bewertung: `Kapitalrisiko: <b>{{ capital_risk_label or 'unbekannt' }}</b>`; wrap the breakdown table:

```html
<details class="fold">
  <summary>Punkteaufschlüsselung ({{ breakdown_rows | length }} Positionen)</summary>
  <div class="tablewrap">…existing table…</div>
</details>
```

Kostenmodell: first table row

```html
<tr><th scope="row">Sanierungsstufe</th>
    <td><b>{{ renovation_tier_label }}</b> <small>· {{ renovation_basis }}</small></td></tr>
```

and under the table `<p class="hint">Stufe: das schlechteste Signal aus Zustand, Merkmalen und Baujahr gewinnt. Ohne Angabe zum Zustand gilt ein Baujahr vor 1960 als „schwer“.</p>`.

`macros.html`: `status_chip` prints `{{ prop.listing_status | de_status }}`; `cost_band` prints `Sanierung {{ cost.renovation_tier | de_tier }}`.

`app.css`: `.fold > summary { cursor: pointer; font-weight: 600; margin: 8px 0; } .fold[open] > summary { margin-bottom: 4px; }`.

- [ ] **Step 6: Run everything**

`PYTHONPATH=src python -m pytest -q` and `ruff check src tests`. Grep the templates once for leftover English in reader-facing copy in the sections you touched.

- [ ] **Step 7: Commit**

`git commit -am "feat(web,costmodel): a German dossier - assumptions, labels, renovation basis, folded breakdown"`

---

### Task 3 (wave 1, D): Verlauf without repetition

**Files:**
- Modify: `src/hofradar/web/history.py` (`timeline`)
- Test: `tests/web/test_timeline.py` (new)

**Interfaces:**
- Consumes: `hofradar.web.filters.de_status` (Task 2). If Task 2 has not merged yet, add a local `STATUS_WORDS` fallback import guarded by `try/except ImportError` — no: simply import `de_status`; if it does not exist in your worktree, define `STATUS_LABELS` + `de_status` in `web/filters.py` exactly as Task 2 specifies (identical code merges cleanly).
- Produces: `timeline()` entries with `kind == "observations"` for folded runs.

- [ ] **Step 1: Write the failing tests**

```python
"""The Verlauf tells the story once: no duplicate first-seen row, German status
names, and one line per run of observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.enums import ChangeKind, ListingStatus
from hofradar.db.models import Observation, StatusHistory
from hofradar.web import history

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _observe(db, prop, source, at, visible=True):
    db.add(Observation(property_id=prop.id, source_id=source.id, scraped_at=at,
                       listing_visible=visible, url=f"https://x.test/{at.timestamp()}",
                       raw_hash=str(at.timestamp())))
```

Open `tests/web/conftest.py` `seeded` to see which `Observation` fields are required and mirror them in `_observe`.

```python
def test_first_seen_status_row_is_not_repeated(db, seeded, source):
    prop = seeded["near"]
    db.add(StatusHistory(property_id=prop.id, observed_at=T0, old_status=None,
                         new_status=ListingStatus.DISCOVERED, change_kind=ChangeKind.FIRST_SEEN))
    db.commit(); db.refresh(prop)
    titles = [e["title"] for e in history.timeline(prop)]
    assert titles.count("Erstmals erfasst") == 1
    assert "Statuswechsel" not in titles or all(
        "unbekannt" not in e["text"] for e in history.timeline(prop) if e["title"] == "Statuswechsel"
    )


def test_status_change_uses_german_words(db, seeded):
    prop = seeded["near"]
    db.add(StatusHistory(property_id=prop.id, observed_at=T0, old_status=ListingStatus.DISCOVERED,
                         new_status=ListingStatus.ACTIVE, change_kind=ChangeKind.STATUS_CHANGE))
    db.add(StatusHistory(property_id=prop.id, observed_at=T0 + timedelta(days=1),
                         old_status=ListingStatus.ACTIVE, new_status=ListingStatus.ACTIVE,
                         change_kind=ChangeKind.STATUS_CHANGE))
    db.commit(); db.refresh(prop)
    changes = [e for e in history.timeline(prop) if e["title"] == "Statuswechsel"]
    assert len(changes) == 1
    assert "„Entdeckt“ → „Aktiv“" in changes[0]["text"]
    assert "discovered" not in changes[0]["text"]


def test_consecutive_observations_fold_into_one_line(db, seeded, source):
    prop = seeded["near"]
    for day in range(5):
        _observe(db, prop, source, T0 + timedelta(days=day))
    db.commit(); db.refresh(prop)
    folded = [e for e in history.timeline(prop) if e["kind"] == "observations"]
    assert len(folded) == 1
    assert "5 Abrufe" in folded[0]["text"]
    assert folded[0]["at"] == T0 + timedelta(days=4)


def test_a_price_change_between_observations_starts_a_new_group(db, seeded, source):
    from hofradar.db.models import PriceHistory

    prop = seeded["near"]
    _observe(db, prop, source, T0)
    _observe(db, prop, source, T0 + timedelta(days=1))
    db.add(PriceHistory(property_id=prop.id, observed_at=T0 + timedelta(days=2),
                        old_price=500_000, new_price=450_000, delta_pct=-10.0))
    _observe(db, prop, source, T0 + timedelta(days=3))
    db.commit(); db.refresh(prop)
    kinds = [e["kind"] for e in history.timeline(prop) if e["kind"] in ("observations", "observation", "price_change")]
    assert kinds == ["observations", "price_change", "observation"]


def test_last_unreachable_observation_says_so(db, seeded, source):
    prop = seeded["near"]
    _observe(db, prop, source, T0)
    _observe(db, prop, source, T0 + timedelta(days=1), visible=False)
    db.commit(); db.refresh(prop)
    folded = [e for e in history.timeline(prop) if e["kind"] == "observations"]
    assert "nicht mehr erreichbar" in folded[0]["text"]
```

The seeded property may already carry observations/price rows; read the fixture and either use a fresh `make_property(db, public_id="HF-0100")` with no history (preferred) or adjust counts.

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Implement in `history.timeline`**

Status rows: skip `change_kind == ChangeKind.FIRST_SEEN` (compare as string too - the column is a `str`), skip `old == new`; text `f"„{de_status(old)}“ → „{de_status(new)}“."` when `old` else `f"Jetzt „{de_status(new)}“."`, then `detail`.

Observations: build the per-row entries as today with `kind="observation"`, then after the final sort run one folding pass:

```python
OBSERVATION_KINDS = ("observation",)

def _fold_observations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from hofradar.web.filters import de_date

    out: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []

    def flush() -> None:
        if not group:
            return
        if len(group) == 1:
            out.append(group[0])
        else:
            first, last = group[0], group[-1]
            text = f"{len(group)} Abrufe seit {de_date(first['at'])}, zuletzt {de_date(last['at'])}"
            if not last.get("visible", True):
                text += " – Inserat nicht mehr erreichbar"
            out.append({"at": last["at"], "kind": "observations", "icon": "👁",
                        "title": "Beobachtungen", "text": text})
        group.clear()

    for event in events:
        if event["kind"] in OBSERVATION_KINDS:
            group.append(event)
        else:
            flush()
            out.append(event)
    flush()
    return out
```

Add `"visible": visible` to each observation entry so the fold can read it; strip that key is unnecessary (the JSON endpoint only copies `at/kind/text`).

- [ ] **Step 4: Run the tests, the whole suite, ruff**

`tests/integration/test_first_price_is_not_a_change.py` must stay green.

- [ ] **Step 5: Commit**

`git commit -am "fix(web): a Verlauf that tells the story once - German statuses, folded observations"`

---

### Task 4 (wave 1, E): Issue #14 - the search box finds what was typed

**Files:**
- Modify: `src/hofradar/scoring/engine.py` (`SUPPORTED_FILTERS`, new `matches_search`, `ranked_properties` post-filter)
- Modify: `src/hofradar/web/query.py` (`passes_filters` uses `matches_search`)
- Modify: `src/hofradar/web/deps.py` — only the `if self.town:` line in `as_scoring_filters` (`payload["q"] = self.town`)
- Modify: `src/hofradar/web/templates/partials/results.html` (statusline + empty state)
- Test: `tests/web/test_search_box.py` (new), `tests/scoring/test_slider_recompute.py` (add `q` cases to the `test_filters` table)

**Interfaces:**
- Produces: `hofradar.scoring.engine.matches_search(prop, needle: str) -> bool`; filter key `q` in `SUPPORTED_FILTERS`.

- [ ] **Step 1: Write the failing tests**

```python
"""Issue #14: the search box means "find what I typed", not "exactly this town"."""

from __future__ import annotations

import pytest

from hofradar.scoring.engine import matches_search


@pytest.mark.parametrize("needle", ["83278", "Traun", "traunstein", "Vierseithof", "Chiemgau"])
def test_search_box_finds_the_property(client, seeded, needle):
    payload = client.get(f"/api/properties.json?q={needle}").json()
    ids = [p["public_id"] for p in payload["properties"]]
    assert ids == ["HF-0002"], (needle, ids)


def test_search_box_miss_says_zero_hits(client, seeded):
    html = client.get("/api/results?q=Nirgendwo").text
    assert "0 Treffer für „Nirgendwo“" in html
    assert "Kein Objekt passt zu diesen Reglern" not in html


def test_statusline_names_the_active_search(client, seeded):
    assert "Suche „Traun“" in client.get("/api/results?q=Traun").text


def test_umlauts_match_case_insensitively(db, seeded):
    from tests.web.conftest import make_property

    prop = make_property(db, public_id="HF-0200", town="Ödhof", canonical_title="Hof")
    assert matches_search(prop, "ödhof")
    assert matches_search(prop, "ÖD")
    assert not matches_search(prop, "Miesbach")
```

`tests/scoring/test_slider_recompute.py` `test_filters` table: add `({"q": "Feilnbach"}, 3)` and `({"q": "Nowhere"}, 0)` (the `estate` fixture there seeds Bad Feilnbach rows; confirm by reading the fixture).

- [ ] **Step 2: Run, expect failures (`matches_search` missing; `q` unsupported)**

- [ ] **Step 3: Engine**

```python
SEARCH_FIELDS: tuple[str, ...] = ("town", "postcode", "district", "canonical_title")


def matches_search(prop: Property, needle: str) -> bool:
    """Casefolded substring over the fields a reader would type.

    Done in Python, not SQL: SQLite's ``lower()`` folds ASCII only, so a
    ``LIKE`` would miss every umlaut village (GitHub issue #14).
    """
    wanted = needle.casefold().strip()
    if not wanted:
        return True
    haystack = " ".join(str(getattr(prop, f, None) or "") for f in SEARCH_FIELDS).casefold()
    return wanted in haystack
```

Add `"q"` to `SUPPORTED_FILTERS`; in `ranked_properties` pop it like `flags` (`wanted_search = filters.pop("q", None)`) and after the SQL: `if wanted_search: rows = [row for row in rows if matches_search(row[0], wanted_search)]`. `_apply_filters` must not see `q`.

`query.py` `passes_filters`: replace the inline haystack block with `if filters.town and not matches_search(prop, filters.town): return False` (import from `hofradar.scoring.engine`; `query.py` already tolerates a missing scoring package via `lazy` - check whether a direct import is acceptable there; if `query.py` must stay importable without `hofradar.scoring`, put `matches_search` in `hofradar/web/search.py` instead and have the engine import it from there. Pick the one that keeps the existing `lazy` contract; write a one-line comment saying why).

- [ ] **Step 4: Template**

`results.html` statusline, after the "im Filter" span:

```html
{% if filters.town %}<span>·</span><span>Suche „{{ filters.town }}“</span>{% endif %}
```

Empty state: add a branch before the "Kein Objekt passt" text:

```html
{% elif filters.town %}
  0 Treffer für „{{ filters.town }}“. Ort, PLZ, Ortsteil oder ein Wort aus dem Titel werden durchsucht.
```

- [ ] **Step 5: Full suite + ruff, including `tests/web/test_filter_contract.py`**

- [ ] **Step 6: Commit**

`git commit -am "fix(scoring,web): the search box matches postcode, district and partial names (#14)"`

---

### Task 5 (wave 2, B): Merkliste

Start only from the merged wave-1 tree.

**Files:**
- Modify: `src/hofradar/db/models.py:199` (add `shortlisted_at`)
- Create: `src/hofradar/migrations/versions/20260904_<rev>_property_shortlisted_at.py`
- Modify: `src/hofradar/dedupe/merge.py` (`_SCALAR_FIELDS` or a dedicated rule)
- Modify: `src/hofradar/scoring/engine.py` (`SUPPORTED_FILTERS` + `_apply_filters`: `shortlisted`)
- Modify: `src/hofradar/web/deps.py` (`ResultFilters.shortlisted_only`, `as_scoring_filters`, `query_string`, `filters_from_query`)
- Modify: `src/hofradar/web/query.py` (`passes_filters`, `row_to_dict`)
- Modify: `src/hofradar/web/routes/dossier.py` (`USER_STATES`, triage legacy value, `merken` route)
- Create: `src/hofradar/web/routes/merkliste.py`; register it in `web/app.py` next to the other routers; add nav item
- Create: `src/hofradar/web/templates/partials/merken_button.html`, `pages/merkliste.html`
- Modify: `pages/dossier.html` (header), `partials/result_card.html` (footer), `partials/triage.html` (nothing unless the saved-notice text needs the legacy word)
- Modify: `src/hofradar/web/routes/radar.py` (`CSV_COLUMNS` + a `Merkliste` value), `app.css` (`.btn--on`)
- Modify tests: `tests/web/test_filter_contract.py` (`user_state="watch"`, `shortlisted_only=True`), `tests/web/test_triage_and_settings.py` (first test → legacy behaviour), `tests/scoring/test_slider_recompute.py` (`user_state: "shortlist"` → `"watch"`)
- Test: `tests/web/test_merkliste.py`, `tests/dedupe/test_merge_keeps_the_mark.py`, `tests/db/test_shortlist_migration_data.py`

**Interfaces:**
- Consumes: `saved_profile_params(request)` (Task 1), `resolve()`/`result_context()` (radar.py), `render()`.
- Produces: `Property.shortlisted_at: datetime | None`; route `POST /property/{public_id}/merken`; page `GET /merkliste`; query key `merkliste=1`; engine filter `shortlisted`; JSON field `shortlisted_at`; CSV column `Merkliste`.

- [ ] **Step 1: Write the failing web tests**

```python
"""The Merkliste: one click on a dossier or a card, one page that lists it."""

from __future__ import annotations

from sqlalchemy import select

from hofradar.db.models import Property


def _prop(db, public_id):
    db.expire_all()
    return db.scalar(select(Property).where(Property.public_id == public_id))


def test_toggle_marks_and_unmarks(client, db, seeded):
    response = client.post("/property/HF-0001/merken", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Gemerkt" in response.text
    assert _prop(db, "HF-0001").shortlisted_at is not None

    response = client.post("/property/HF-0001/merken", headers={"HX-Request": "true"})
    assert "Merken" in response.text and "Gemerkt" not in response.text
    assert _prop(db, "HF-0001").shortlisted_at is None


def test_plain_post_redirects_back_to_the_dossier(client, seeded):
    response = client.post("/property/HF-0001/merken", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/property/HF-0001"


def test_unknown_property_is_404(client, seeded):
    assert client.post("/property/HF-9999/merken").status_code == 404


def test_button_appears_on_dossier_and_cards(client, seeded):
    assert 'hx-post="/property/HF-0001/merken"' in client.get("/property/HF-0001").text
    assert 'hx-post="/property/HF-0001/merken"' in client.get("/api/results").text


def test_merkliste_page_lists_only_marked_rows(client, db, seeded):
    assert "Noch nichts gemerkt" in client.get("/merkliste").text
    client.post("/property/HF-0002/merken")
    html = client.get("/merkliste").text
    assert "HF-0002" in html and "HF-0001" not in html
    assert "1 Objekt" in html or "1 gemerkt" in html


def test_merkliste_ignores_the_saved_search_but_keeps_the_sliders(client, db, seeded):
    client.post("/property/HF-0002/merken")
    client.get("/?air_km_max=55&total_budget_max=700000&q=Nirgendwo")
    response = client.get("/merkliste", follow_redirects=False)
    assert response.status_code == 200
    assert "HF-0002" in response.text
    assert "0 Treffer" not in response.text


def test_merkliste_shows_a_gate_rejected_row_but_not_an_archived_one(client, db, seeded):
    client.post("/property/HF-0002/merken")
    client.post("/property/HF-0002/triage", data={"user_state": "archived"})
    assert "HF-0002" not in client.get("/merkliste").text
    assert "1 archivierte ausgeblendet" in client.get("/merkliste").text
```

Add a rejected-row case using the same technique `tests/web/test_archived_is_hidden.py` uses to obtain a `Score.rejected` row (read it; reuse its helper).

```python
def test_legacy_shortlist_triage_value_lands_on_the_merkliste(client, db, seeded):
    response = client.post("/property/HF-0001/triage", data={"user_state": "shortlist"})
    assert response.status_code == 200
    assert "gemerkt" in response.text
    prop = _prop(db, "HF-0001")
    assert prop.shortlisted_at is not None and prop.user_state is None


def test_shortlist_is_no_longer_a_triage_state(client, seeded):
    html = client.get("/property/HF-0001").text
    assert 'value="shortlist"' not in html


def test_exports_carry_the_mark(client, db, seeded):
    client.post("/property/HF-0001/merken")
    row = next(p for p in client.get("/api/properties.json").json()["properties"] if p["public_id"] == "HF-0001")
    assert row["shortlisted_at"]
    csv = client.get("/api/export.csv").text
    assert "Merkliste" in csv.splitlines()[0]
```

`tests/dedupe/test_merge_keeps_the_mark.py`: read `tests/dedupe/` for how a merge is invoked (function name, fixture), then: mark the *loser* with `shortlisted_at=T0`, merge, assert the survivor has `shortlisted_at == T0`; second case both marked, survivor keeps the earlier timestamp.

`tests/db/test_shortlist_migration_data.py`: read `tests/db/test_migrations.py` for how it builds a database from the migrations alone. Upgrade to the revision *before* yours, insert a `properties` row with `user_state='shortlist'` (raw SQL, fill the NOT NULL columns the baseline defines), upgrade to head, assert `shortlisted_at IS NOT NULL AND user_state IS NULL`.

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Model + migration**

`models.py` after `user_note`:

```python
    #: On the reader's Merkliste since. Triage-class data like ``user_state``;
    #: null means not on the list. Written only by the ``/merken`` route.
    shortlisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
```

Generate: `alembic -c alembic.ini revision --autogenerate -m "property shortlisted_at"` (needs a database: run `hofradar init-db` against a scratch `HOFRADAR_DB` path first if the CLI supports it - read `alembic.ini`/`migrations/env.py` for how the URL is resolved; the existing migration files show the expected shape). Then hand-add to `upgrade()` after the column:

```python
    op.execute(
        "UPDATE properties SET shortlisted_at = updated_at, user_state = NULL "
        "WHERE user_state = 'shortlist'"
    )
```

`downgrade()` drops the column only. Run `PYTHONPATH=src python -m pytest -q tests/db` - the migrations-vs-models comparison must pass.

- [ ] **Step 4: Merge rule**

In `dedupe/merge.py`, where scalars are copied, add a dedicated rule (not the generic replace): `survivor.shortlisted_at = min(filter(None, (survivor.shortlisted_at, loser.shortlisted_at)), default=None)`. Read how the module names the two rows first.

- [ ] **Step 5: Filter plumbing**

`deps.py`: `ResultFilters.shortlisted_only: bool = False`; `as_scoring_filters`: `if self.shortlisted_only: payload["shortlisted"] = True`; `query_string`: `"merkliste": int(self.shortlisted_only)`; `filters_from_query`: `shortlisted_only=to_bool(get("merkliste"))`. Add `"merkliste"` to `REMEMBERED_KEYS`? **No** - the Merkliste page forces it; a remembered `merkliste=1` on the radar would be surprising.

`engine.py`: `"shortlisted"` in `SUPPORTED_FILTERS`; in `_apply_filters`: `if filters.get("shortlisted"): stmt = stmt.where(Property.shortlisted_at.is_not(None))`.

`query.py` `passes_filters`: `if filters.shortlisted_only and prop.shortlisted_at is None: return False`. `row_to_dict`: `"shortlisted_at": prop.shortlisted_at.isoformat() if prop.shortlisted_at else None`.

`radar.py` `CSV_COLUMNS`: append `("shortlisted_at", "Merkliste")`; in the writer, map that key to `"1"` when set, `""` otherwise.

- [ ] **Step 6: Routes**

`dossier.py`: remove `"shortlist"` from `USER_STATES`; add `LEGACY_SHORTLIST_STATE = "shortlist"`. In `triage()`, before the `if state not in USER_STATES` check:

```python
    if state == LEGACY_SHORTLIST_STATE:
        # A form rendered before the Merkliste existed. Honour the intent.
        if prop.shortlisted_at is None:
            prop.shortlisted_at = history.now_utc()
        state = "none"
        legacy_marked = True
```

and pass `legacy_marked` into the template so the notice reads `Gespeichert: ⭐ gemerkt`. Add the toggle:

```python
@router.post("/property/{public_id}/merken")
def merken(public_id: str, request: Request, session: Session = Depends(get_db)):
    """The Merkliste toggle. The only writer of ``Property.shortlisted_at``."""
    prop = load_property(session, public_id)
    if prop is None:
        return render(request, "pages/error.html",
                      {"code": 404, "message": f"Kein Objekt mit der ID {public_id}."}, status_code=404)
    prop.shortlisted_at = None if prop.shortlisted_at else history.now_utc()
    session.add(prop)
    session.commit()
    session.refresh(prop)
    if request.headers.get("HX-Request"):
        return render(request, "partials/merken_button.html", {"prop": prop})
    return RedirectResponse(f"/property/{prop.public_id}", status_code=303)
```

`routes/merkliste.py`:

```python
"""The Merkliste - what the reader marked, under the reader's own sliders.

It is the human's list, so the score gate does not apply (``include_rejected``
is forced on); archiving still hides, and still counts, exactly as on the
radar. The saved *view* filters are deliberately not applied: a remembered
search for one village must not empty a list of farms in another.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from hofradar.web.deps import (
    filters_from_query, get_db, profile_from_query, render, saved_profile_params,
)
from hofradar.web.query import build_results
from hofradar.web.routes.radar import result_context

router = APIRouter(tags=["merkliste"])


@router.get("/merkliste")
def merkliste(request: Request, session: Session = Depends(get_db)):
    params = dict(request.query_params) or saved_profile_params(request)
    profile = profile_from_query(params, session=session)
    filters = filters_from_query({})
    filters.shortlisted_only = True
    filters.include_rejected = True
    results = build_results(session, profile, filters)
    return render(request, "pages/merkliste.html", result_context(request, results))
```

`filters_from_query` takes an object with `.get` - a dict works; check it does not call anything dict lacks (`dict(params)` at the end is fine).

`app.py`: include the router; `NAV_ITEMS` gets `("/merkliste", "Merkliste", "⭐")` after Karte.

- [ ] **Step 7: Templates**

`partials/merken_button.html`:

```html
{# The Merkliste toggle. Self-contained: it swaps itself, so it works inside
   a card that arrived through an HTMX results swap. #}
<form class="merken" method="post" action="/property/{{ prop.public_id }}/merken"
      hx-post="/property/{{ prop.public_id }}/merken" hx-swap="outerHTML">
  <button class="btn btn--small{% if prop.shortlisted_at %} btn--on{% endif %}" type="submit"
          aria-pressed="{{ 'true' if prop.shortlisted_at else 'false' }}"
          title="{% if prop.shortlisted_at %}Von der Merkliste nehmen{% else %}Auf die Merkliste{% endif %}">
    {% if prop.shortlisted_at %}★ Gemerkt{% else %}☆ Merken{% endif %}
  </button>
</form>
```

Dossier header: after the `Inserat öffnen` paragraph, `{% include "partials/merken_button.html" %}`. Card footer: the same include, before the user-state chip. `pages/merkliste.html`:

```html
{% extends "base.html" %}
{% block title %}Merkliste – {{ app_title }}{% endblock %}
{% block content %}
<section class="page">
  <h1>⭐ Merkliste</h1>
  <p class="hint">{{ results.total_matched | de_number }} gemerkt · bewertet mit
     {{ profile.radius.air_km_max | de_km(0) }} und {{ profile.budget.total_budget_max | de_eur }}.
     <a href="/">Regler ändern</a></p>
  {% if not results.rows and not results.hidden_archived %}
  <p class="empty">Noch nichts gemerkt. Auf einem Dossier oder einer Karte ☆ Merken drücken.</p>
  {% else %}
  <div id="results">{% include "partials/results.html" %}</div>
  {% endif %}
</section>
{% endblock %}
```

Check `results.html`'s own empty-state text does not fire on top of yours (it shows "Kein Objekt passt…" when `rows` is empty and `total_in_db > 0`); guard with `{% if not results.rows %}` as above so only one message shows. `app.css`: `.btn--on { background: var(--ok-soft); color: var(--ok); border-color: var(--ok); }` (reuse the existing tokens; look at how `.btn` is styled).

- [ ] **Step 8: Fix the three existing tests named in Files, run the full suite + ruff**

`test_triage_post_persists_user_state` becomes the legacy test above or switches to `"watch"`. Add `"/merkliste"` to `EMPTY_DB_PAGES` and the data list in `tests/web/test_pages.py`.

- [ ] **Step 9: Commit**

`git commit -am "feat(web,db): the Merkliste - one flag, one toggle, one page"`

---

### Task 6 (wave 3): Docs

**Files:**
- Modify: `docs/DECISIONS.md` (append entry 21)
- Modify: `docs/MODULE_API.md` (scoring filters `q`, `shortlisted`; web: `/merkliste`, `/property/{id}/merken`, the cookie)
- Modify: `CLAUDE.md` ("Known ground": `USER_STATES` has no `shortlist`; the Merkliste lives in `Property.shortlisted_at`; filter memory is the `hofradar_radar` cookie and a `303`)

- [ ] **Step 1: Read entries 19 and 20 of `DECISIONS.md` for the house style, then write entry 21**

Title: `## 21. The Merkliste is a flag, not a triage state; the radar remembers by redirecting`. Cover: why a separate column (orthogonal to triage, the #9 one-word-one-fact lesson, the digest's "shortlist" is the scorer's pick and keeps its name), the legacy-value and merge rules, why a cookie plus `303` instead of localStorage (URL stays the truth, no JS, crumb and nav need nothing), why the Merkliste applies only the profile half of the cookie, and why `q` is matched in Python (SQLite `lower()` is ASCII-only).

- [ ] **Step 2: Update `MODULE_API.md` and `CLAUDE.md` in the same style as the surrounding text**

- [ ] **Step 3: Commit**

`git commit -am "docs: decision 21, and the API surface of the Merkliste and filter memory"`
