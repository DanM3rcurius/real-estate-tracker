# UI refinements: filter memory, Merkliste, a German dossier, and #14

Date: 2026-09-04. Branch: `claude/ui-refinements-features-s1tj9p`.

## Why

Six reader-facing rough edges, reported after real use, plus the one open
GitHub issue that is a UI defect (#14). None of them is a scraping or scoring
problem; all of them are about what the reader sees. They are landed as one
slice because they touch the same three files (`dossier.html`, `dossier.py`,
`controls.html`) and one shared vocabulary.

Occam's razor governs every choice below: the smallest mechanism that makes
the symptom go away, no new abstraction unless two callers need it.

## Scope

1. The radar's filters survive navigation.
2. A **Merkliste** (favourites): a one-click ⭐ on the dossier and on every
   result card, and a page that lists what was marked.
3. The dossier is German end to end: cost-model assumptions, breakdown labels,
   status names, capital-risk words.
4. The renovation tier is shown with its basis (stated in the listing, or
   guessed from the year built).
5. The `Bewertung` point breakdown is collapsed by default.
6. The `Verlauf` no longer repeats itself: no duplicate first-seen row, no
   `Status unbekannt → discovered`, one line per run of observations.
7. Issue #14: the search box finds postcodes, districts and partial town
   names, and says "0 Treffer für „…“" instead of showing nothing.

Out of scope: #13 (a ruling, not UI), the pipeline's commit visibility, any
change to scoring numbers.

## 1. Filter memory (cookie)

**Mechanism.** One cookie, `hofradar_radar`, whose value is the canonical
query string `ResultFilters.query_string(profile)` already used for the
CSV/JSON/map permalinks. No JS, no localStorage, no server-side table.

- `GET /` and `GET /api/results` set the cookie whenever the request carries
  at least one known control parameter (the keys `query_string` emits).
  `max_age` one year, `path=/`, `samesite=lax`, `httponly`.
- `GET /`, `GET /map` and `GET /merkliste` with **no** known control parameter
  and a non-empty cookie answer `303` to the same path with the cookie's query
  string appended. The address bar therefore always shows the real state, the
  existing `syncUrl` in `app.js` keeps working, and the dossier's `← Radar`
  crumb and the top-nav links need no change.
- `GET /?reset=1` deletes the cookie and renders the defaults. The controls
  panel gets a link `↺ Filter zurücksetzen` next to the map/CSV/JSON links.
- Guard rails: the cookie is only honoured if it is ≤ 1 000 characters and
  parses (`urllib.parse.parse_qsl`) to at least one known key. Anything else
  is ignored and overwritten on the next request. `reset`, `profile`, `limit`
  and unknown keys are never stored.

**Files.** `web/deps.py` (helper `saved_query(request) -> str | None`,
`remember_query(response, results)`), `web/routes/radar.py`,
`web/routes/map_view.py`, the new Merkliste route, `partials/controls.html`.

**Tests.** `/?air_km_max=50` sets the cookie; a following bare `/` is a `303`
to `/?air_km_max=50…`; `/?reset=1` is `200` and clears it; a garbage cookie
is ignored (`200`, no redirect loop); `/api/results` sets it too.

## 2. Merkliste

**Data.** `Property.shortlisted_at: DateTime(timezone=True) | None`, indexed.
Null means not on the list. One Alembic migration adds the column and
converts the old triage state in the same revision:

```sql
UPDATE properties SET shortlisted_at = updated_at, user_state = NULL
 WHERE user_state = 'shortlist';
```

The `⭐ Shortlist` radio leaves `USER_STATES`. Two reasons: the list is
orthogonal to triage (a `Kontaktiert` farm stays on the list), and the #9
lesson - one word, one fact. Note that `report/data.py` also says "shortlist"
for the digest's top ten; that is the scorer's pick, not the reader's, and it
keeps its name. The reader's list is **Merkliste** everywhere in UI copy.

**Writes.** `POST /property/{public_id}/merken` toggles: null → now, set →
null. Only route that writes `shortlisted_at`. It renders
`partials/merken_button.html` for HTMX (`hx-swap="outerHTML"`), and answers
`303` back to the dossier for a plain form post. Invariant 1 is untouched -
this is triage-class data like `user_state`, not a `Property` fact.

**Button.** `partials/merken_button.html` takes `prop`. Off: `☆ Merken`;
on: `★ Gemerkt` (class `btn--on`). Placed in the dossier header next to
`Inserat öffnen`, and in every result card footer. Because the card partial is
rendered inside `/api/results` swaps, the button carries its own `hx-*`
attributes and needs nothing from the page.

**Read.** `ResultFilters.shortlisted_only: bool` (query key `merkliste=1`),
emitted to the engine as `filters["shortlisted"] = True`.
`scoring.engine.SUPPORTED_FILTERS` gains `shortlisted` →
`Property.shortlisted_at.is_not(None)`; `web.query.passes_filters` mirrors
it for the degraded path.

**Page.** `GET /merkliste`: nav item `("/merkliste", "Merkliste", "⭐")` after
Karte. It calls the radar's `resolve()` with `shortlisted_only=True` and
`include_rejected=True` forced (the human's list is not subject to the score
gate; archived rows stay hidden and counted, exactly as on the radar). It
renders `pages/merkliste.html`: a heading with the count, the shared
`partials/results.html`, and - when empty - "Noch nichts gemerkt. Auf einem
Dossier oder einer Karte ☆ Merken drücken." No control panel; the profile
comes from the query string / cookie like everywhere else.

**Exports.** `row_to_dict` gains `shortlisted_at` (ISO or null). The CSV gets a
`Merkliste` column (`1`/empty).

**Tests.** Toggle on → row has a timestamp, HTMX response contains `Gemerkt`;
toggle again → null. `/merkliste` lists exactly the marked rows, including a
score-rejected one, excluding an archived one. Migration test: the
migrations-only schema equals the models (existing `test_migrations.py`), plus
a data test that a `user_state='shortlist'` row comes out with
`shortlisted_at` set and `user_state` null. `test_filter_contract.py` drops
`user_state="shortlist"` for `"watch"` and adds `shortlisted_only=True`.

## 3. A German dossier

**Assumptions at the source.** Every string appended to `assumptions` in
`costmodel/estimator.py` is rewritten in German (UI copy rule). The strings
are stored in `CostEstimate.assumptions` and are recomputed on every scoring
run (`_write_cost` overwrites), so existing rows turn German on the next run;
no data migration. `tests/scoring/test_costmodel.py:141` changes its needle.

**Labels in the dossier, not in the data.** `dossier.py` gets three small
dicts and uses them when building rows:

- `COST_LABELS`: `purchase → Kaufpreis`, `acquisition → Erwerbsnebenkosten`,
  `house / house_low / house_high → Haus (Mitte / niedrig / hoch)`,
  `roof → Dach`, `outbuildings → Nebengebäude`, `utilities → Haustechnik`,
  `contingency* → Puffer …`, `immediate_capex → Sofortmaßnahmen`,
  `living_sqm_used → Wohnfläche angesetzt`, `roof_sqm_used → Dachfläche
  angesetzt`, `outbuilding_sqm_used → Nebengebäudefläche angesetzt`,
  `rate_per_sqm_* → Satz €/m² (niedrig/Mitte/hoch)`; keys starting with
  `outbuilding_sqm_` → `Fläche <tag>`.
- `SCORE_LABELS` for the breakdown path segments: `fit → Passung`,
  `deal → Preis-Leistung`, `hidden → Verborgenheit`, `freshness → Frische`,
  `confidence → Belastbarkeit`, `geography → Lage`, `price → Preis`,
  `land → Grund`, `substance → Substanz`, `seclusion → Alleinlage`,
  `development → Entwicklung`, `outbuildings → Nebengebäude`; a `_score`
  suffix is dropped, `_max` becomes ` (max.)`. Unknown segments pass through
  unchanged - a label map must never hide a key.
- `CAPITAL_RISK_LABELS`: `low → gering`, `moderate → mäßig`, `high → hoch`,
  `extreme → extrem`.

Money values in the cost rows are rendered with `de_eur`, square metres with
`de_sqm`, rates as `€/m²`; today they print as raw floats.

**Status names.** `web/filters.py` gains `STATUS_LABELS` (the German half of
`deps.STATUS_OPTIONS`, moved so `history.py` can import it without a cycle)
and a Jinja filter `de_status`. `deps.STATUS_OPTIONS` is built from it. The
`status_chip` macro prints `{{ prop.listing_status | de_status }}` (the CSS
class keeps the enum value).

## 4. Renovation tier, with its basis

`renovation_evidence(prop)` already answers "stated or guessed?" but is not
stored. The dossier computes it live (no column, no migration) and shows one
row at the top of the Kostenmodell table:

> Sanierungsstufe · **schwer** · laut Inserat / aus Baujahr geschätzt

Tier words: `light → leicht`, `medium → mittel`, `heavy → schwer`,
`complete → Kernsanierung`, `unknown → unbekannt` (`TIER_LABELS` in
`web/filters.py`, filter `de_tier`, also used by the `cost_band` macro on the
cards). A `<p class="hint">` under the table explains the rule in two
sentences: worst signal wins; nothing stated and built before 1960 means
schwer.

## 5. Bewertung collapsed

The breakdown table moves into `<details class="fold">` with
`<summary>Punkteaufschlüsselung ({{ n }} Positionen)</summary>`, closed by
default. Badges, Kapitalrisiko and the reject reasons stay visible. Pure
template + one CSS rule.

## 6. Verlauf cleanup

In `history.timeline()`:

- a `status_history` row with `change_kind == first_seen` is skipped
  (`Erstmals erfasst` already says it);
- a row whose `old_status == new_status` is skipped;
- the text becomes `„entdeckt“ → „aktiv“` using `de_status`, then `detail`;
- observations are folded: consecutive observation rows collapse into one
  entry dated at the latest, `N Abrufe seit {de_date(first)}, zuletzt
  {de_date(last)}` plus ` – Inserat nicht mehr erreichbar` if the last one
  was not visible. A price or status event between two observations starts a
  new group, so the order of events is preserved.

`api_property` serialises the same list, so the JSON gets the folded shape
too; the `kind` of a folded entry is `observations`.

## 7. Issue #14: the search box

- `ResultFilters.as_scoring_filters()` emits `q` (not `town`) for the box.
- `scoring.engine.SUPPORTED_FILTERS` gains `q`: casefolded substring over
  `town`, `postcode`, `district`, `canonical_title` -
  `or_(*[func.lower(col).like(f"%{needle}%") for col in ...])`. `town` keeps
  its exact-list semantics for programmatic callers.
- `partials/results.html`: when `filters.town` is set and there are no rows,
  the empty state reads `0 Treffer für „{{ filters.town }}“.`; the statusline
  gains `· Suche „…“` while a search is active.
- Tests: postcode, district, title fragment and prefix each find the seeded
  property via `/` and `/api/properties.json`; exact `town` filter unchanged;
  the empty-state text appears for a miss.

## Docs

- `docs/DECISIONS.md` entry 21: the Merkliste is a flag, not a triage state;
  filter memory is a cookie that redirects, so the URL stays the truth.
- `docs/MODULE_API.md`: engine filters `q` and `shortlisted`; the new route
  and cookie in the web section.
- `CLAUDE.md`: known-ground note that `USER_STATES` no longer has
  `shortlist` and where the list lives.

## Testing strategy

Every change has a test in `tests/web/` or `tests/scoring/`, offline, using
the existing fixtures. The migration test suite stays untouched and must stay
green: it is the only thing that sees a forgotten `shortlisted_at` migration.
`ruff check src tests` and the full suite are the gate before push.
