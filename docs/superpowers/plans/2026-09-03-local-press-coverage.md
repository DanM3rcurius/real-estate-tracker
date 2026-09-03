# Local Press Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the coverage hole around Westham — fill the three empty config lists the codebase already ships adapters for, make a coverage gap visible instead of silent, teach the lifecycle the difference between an expired ad and a sold property, and only then add the OVB portal.

**Architecture:** The premortem's central finding is that Hofradar's constraint is coverage, not ranking, and that its highest-yield intervention needs no new code at all — `gemeindeblatt_pdf`, `generic_rss` and `generic_sitemap` are built, tested and shipping with empty option lists. So Task 2 is data entry, not engineering, and it comes before the adapter. Task 1 first teaches `mark_missing` about listings that expire on a billing cycle, because without it every OVB listing is marked REMOVED on day 15. Task 3 makes a dark municipality visible. Task 4 adds the ovbimmo.de adapter, gated on its terms.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0, httpx, selectolax, pypdf (the `[pdf]` extra), pytest + respx, ruff.

**Spec:** `docs/premortem/premortem-transcript-20260903.md` — findings B3 and B8, and the synthesis section "The hidden assumption". Read it before Task 1.

**Depends on:** `docs/superpowers/plans/2026-09-03-denkmalboerse-source.md`, Tasks 1–4. Task 1 below modifies `mark_missing`, which Plan A Task 3 rewrites; Task 4 relies on Plan A Task 2's `terms_checked_at`. **Do not start this plan until Plan A Task 4 is committed.**

## Global Constraints

Identical to Plan A. Repeated here because a task may be read in isolation:

- Python ≥3.11, `from __future__ import annotations`, full type hints, line length 100, `ruff check src tests` clean.
- Module docstrings explain **why**. Comments and identifiers English; UI copy German.
- No magic numbers in function bodies — module constants or config.
- **Invariant 1:** `hofradar.lifecycle.ingest` is the only writer of `Property` rows.
- **Invariant 2:** `ChangeKind.FIRST_SEEN` only for a property that did not exist; reappearance is `REACTIVATED`.
- **Invariant 3:** `distance_air_km` and `distance_driving_km` never substitute for each other.
- **Invariant 4:** Source role gates authority — a `discovery` source may not verify, set freshness, or have its silence mark a listing removed.
- **Invariant 5:** Scores never live on `Property`.
- **Invariant 6:** The LLM may not write a number.
- **Invariant 7:** No bot-defence evasion, ever.
- **Tests never hit the network** — `respx` fixtures, asserting on the request that *would* have been made.
- **Do NOT run the suite with `HOFRADAR_OFFLINE=1`.**
- Test command: `python -m pytest -q`.
- After editing `config/`, run `python scripts/sync_config_defaults.py`.
- Public-surface additions go into `docs/MODULE_API.md` in the same task.

---

## File Structure

**New files**
- `src/hofradar/sources/adapters/ovbimmo.py` — the OVB portal adapter.
- `tests/sources/adapters/test_ovbimmo.py`
- `tests/sources/fixtures/ovbimmo_search_feldkirchen.html`, `tests/sources/fixtures/ovbimmo_detail.html`
- `tests/lifecycle/test_listing_ttl.py`
- `tests/report/test_coverage_map.py`
- `docs/coverage.md` — the municipality-by-publisher map.

**Modified files**
- `src/hofradar/db/enums.py` — `ListingStatus.EXPIRED`.
- `src/hofradar/lifecycle/absence.py` — TTL-aware absence handling.
- `src/hofradar/scoring/engine.py`, `src/hofradar/scoring/signals.py` — EXPIRED is not "gone".
- `src/hofradar/report/yield_stats.py` — `coverage_by_municipality` (module created in Plan A Task 7).
- `src/hofradar/report/render.py` — the dark-municipality section.
- `config/sources.yaml` + `src/hofradar/_config_defaults/sources.yaml` — bulletins, feeds, sites, and the ovbimmo entry.
- `docs/SOURCES.md`, `docs/MODULE_API.md`.

---

## Task 1: A listing that expired is not a listing that sold (closes B3)

**The verified fact this rests on:** OVB sells ad packages as *"2 Wochen online auf ovbimmo.de plus Veröffentlichung Samstag und Mittwoch im Print."* So listings vanish after ~14 days because the package ran out.

Premortem B3: as a `local` source, OVB's silence is allowed to mark listings REMOVED — and it would do so on a two-week timer for every listing, whether or not the farmstead sold. `REJECT_LISTING_GONE` then pulls them from the ranking and `confidence_score` zeroes their availability. Renewals produce a REMOVED→REACTIVATED metronome that fills the ten-entry digest with churn.

Note this also resolves the risk Plan A Task 3 flagged: OVB legitimately loses a large fraction of its inventory at once, which would otherwise trip `IMPLAUSIBLE_ABSENCE_FRACTION`.

**Files:**
- Modify: `src/hofradar/db/enums.py` (`ListingStatus`)
- Modify: `src/hofradar/config.py` (`SourceConfig.listing_ttl_days`)
- Modify: `src/hofradar/lifecycle/absence.py`
- Modify: `src/hofradar/scoring/engine.py`, `src/hofradar/scoring/signals.py`
- Create: `tests/lifecycle/test_listing_ttl.py`
- Modify: `docs/MODULE_API.md`, `docs/DECISIONS.md`

**Interfaces:**
- Consumes: `mark_missing(session, seen_property_ids, *, source, run_id=None)`, `ImplausibleAbsence` (both from Plan A Task 3).
- Produces: `ListingStatus.EXPIRED`, `SourceConfig.listing_ttl_days: int | None`, `Source.listing_ttl_days` on the ORM model. `EXPIRED` is **not** in `GONE_STATUSES`.

- [ ] **Step 1: Write the failing test**

Create `tests/lifecycle/test_listing_ttl.py`:

```python
"""A newspaper ad that ran its two weeks has expired, not sold.

The distinction is the whole point. REMOVED means the property left the
market and is a real event worth reporting; EXPIRED means a billing cycle
ended and says nothing about the farmstead at all. Conflating them fills the
change feed with a fortnightly metronome and drops live listings out of the
ranking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.enums import ListingStatus
from hofradar.lifecycle import mark_missing
from tests.lifecycle.conftest import make_property, make_source  # existing helpers


def test_a_listing_older_than_the_ttl_expires_rather_than_being_removed(session) -> None:
    source = make_source(session, key="ovbimmo", role="local", listing_ttl_days=14)
    old = make_property(session, source=source, first_seen=datetime.now(UTC) - timedelta(days=20))
    session.flush()

    changes = mark_missing(session, set(), source=source)

    assert old.listing_status == ListingStatus.EXPIRED
    assert all(c.kind != "removed" for c in changes)


def test_a_listing_younger_than_the_ttl_is_still_a_real_removal(session) -> None:
    source = make_source(session, key="ovbimmo", role="local", listing_ttl_days=14)
    fresh = make_property(session, source=source, first_seen=datetime.now(UTC) - timedelta(days=3))
    session.flush()

    mark_missing(session, set(), source=source)

    # Gone after three days is not a billing cycle - that is the seller acting.
    assert fresh.listing_status == ListingStatus.REMOVED


def test_a_source_without_a_ttl_is_unaffected(session) -> None:
    source = make_source(session, key="denkmalboerse", role="primary", listing_ttl_days=None)
    prop = make_property(session, source=source, first_seen=datetime.now(UTC) - timedelta(days=400))
    session.flush()

    mark_missing(session, set(), source=source)

    assert prop.listing_status == ListingStatus.REMOVED


def test_expired_listings_do_not_trip_the_implausible_absence_guard(session) -> None:
    source = make_source(session, key="ovbimmo", role="local", listing_ttl_days=14)
    for i in range(10):
        make_property(
            session,
            source=source,
            external_id=f"G{i:05d}",
            first_seen=datetime.now(UTC) - timedelta(days=20),
        )
    session.flush()

    # All ten aged out together: normal for a fortnightly ad cycle, and must not
    # be mistaken for the parser failure the guard exists to catch.
    mark_missing(session, set(), source=source)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/lifecycle/test_listing_ttl.py -v`
Expected: FAIL — `TypeError: make_source() got an unexpected keyword argument 'listing_ttl_days'`.

- [ ] **Step 3: Write minimal implementation**

In `src/hofradar/db/enums.py`, add to `ListingStatus` after `REMOVED`:

```python
    EXPIRED = "expired"  # the advert's paid run ended; says nothing about the property
```

In `src/hofradar/config.py`, add to `SourceConfig` after `respect_robots`:

```python
    #: How many days a listing stays up before the advert simply expires. Set
    #: for sources that sell a fixed ad window (a newspaper's two weeks), so
    #: their silence after that window is read as EXPIRED, not REMOVED.
    listing_ttl_days: int | None = None
```

Add the matching column to `Source` in `src/hofradar/db/models.py`:

```python
    listing_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
```

Generate the migration (Plan A Task 1 wired this up):

```bash
python scripts/backup_db.py
alembic revision --autogenerate -m "source listing_ttl_days"
alembic upgrade head
```

In `src/hofradar/lifecycle/absence.py`, add the constant and rework the absence branch:

```python
#: Statuses that mean "this advert is no longer on display", of which only
#: REMOVED is evidence about the property itself.
def _absence_status(source: Source, prop: Property, now: datetime) -> ListingStatus:
    """Did the advert expire on a timer, or did the seller actually withdraw it?

    A source with a ``listing_ttl_days`` sells a fixed advertising window. Once
    a listing has been up for that long, its disappearance is the billing cycle
    ending and carries no information about the farmstead. Before that window,
    a disappearance is the seller acting, which is real news.
    """
    ttl = getattr(source, "listing_ttl_days", None)
    if not ttl:
        return ListingStatus.REMOVED
    first_seen = prop.first_seen
    if first_seen is None:
        return ListingStatus.REMOVED
    age_days = (now - _as_utc(first_seen)).days
    return ListingStatus.EXPIRED if age_days >= ttl else ListingStatus.REMOVED
```

Inside `mark_missing`, compute the missing list first, then split it before the plausibility guard so expiries never trip it:

```python
    missing = [ps for ps in rows if ps.property_id not in seen]

    # Expiring adverts are separated out before the guard: a fortnightly ad
    # cycle legitimately clears most of a newspaper's inventory at once, which
    # is exactly the shape the guard exists to reject for other sources.
    genuinely_missing = []
    for ps in missing:
        prop = session.get(Property, ps.property_id)
        if prop is None or prop.merged_into_id is not None:
            continue
        if _absence_status(source, prop, now) is ListingStatus.EXPIRED:
            ps.last_listing_visible = False
            if prop.listing_status != ListingStatus.EXPIRED:
                _transition(session, prop, ListingStatus.EXPIRED, ChangeKind.STATUS_CHANGE,
                            detail=f"advert window of {source.listing_ttl_days} days elapsed "
                                   f"({source.key})",
                            run_id=run_id, now=now)
            continue
        genuinely_missing.append(ps)

    if rows and not seen and genuinely_missing:
        raise ImplausibleAbsence(...)   # unchanged text
    if rows and genuinely_missing:
        fraction = len(genuinely_missing) / len(rows)
        ...                              # unchanged threshold check
```

The existing REMOVED loop then iterates `genuinely_missing` instead of `missing`.

In `src/hofradar/scoring/engine.py` and `src/hofradar/scoring/signals.py`, leave `GONE_STATUSES` as `{REMOVED, SOLD}` — **do not add EXPIRED**. That is the point: an expired advert must not fire `REJECT_LISTING_GONE` or zero the availability term.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/lifecycle/test_listing_ttl.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass.

- [ ] **Step 6: Document it**

Add `ListingStatus.EXPIRED` and `SourceConfig.listing_ttl_days` to `docs/MODULE_API.md`, and append to `docs/DECISIONS.md`:

```markdown
---

## 15. An expired advert and a removed listing are different facts

**Decision.** `ListingStatus.EXPIRED` is a distinct status, set when a source
that sells a fixed advertising window (`listing_ttl_days`) stops carrying a
listing that has been up for at least that long. `EXPIRED` is not in
`GONE_STATUSES`.

**Why.** A regional newspaper's ad package runs two weeks. Reading its silence
as REMOVED marks every listing gone on a fortnightly timer, drops live
farmsteads out of the ranking through `REJECT_LISTING_GONE`, and turns the
change feed into a REMOVED/REACTIVATED metronome that fills a ten-entry digest
with churn. Decision 4 says a source's *role* decides what it may prove; this
adds that a source's *retention policy* decides what its silence means.

**Alternative rejected.** Classifying such sources as `discovery` so their
silence proves nothing. That would also forfeit their ability to verify a
listing is live and to set freshness — both of which a newspaper legitimately
can do. The retention policy is the narrower and truer fix.
```

- [ ] **Step 7: Commit**

```bash
git add src/hofradar/db src/hofradar/config.py src/hofradar/lifecycle migrations/versions tests/lifecycle/test_listing_ttl.py docs/
git commit -m "feat(lifecycle): distinguish an expired advert window from a removed listing"
```

---

## Task 2: Fill the three empty config lists (the hidden assumption)

This is the highest-yield task in either plan and it contains no new code. `gemeindeblatt_pdf` ships complete with `options.bulletins: []`; `generic_rss` and `generic_sitemap` ship **enabled** with `options.feeds: []` and `options.sites: []`. Four of nine premortem investigators independently observed that these stay empty because filling them is data entry rather than engineering.

**Files:**
- Modify: `config/sources.yaml` + `src/hofradar/_config_defaults/sources.yaml`
- Create: `docs/coverage.md`

**Interfaces:**
- Consumes: the existing `pdf_bulletin`, `generic_rss` and `generic_sitemap` adapters — unchanged.
- Produces: populated `options` lists, and `docs/coverage.md` as the record of which municipality is served by which source. Task 3 checks reality against this document.

- [ ] **Step 1: Write down the municipalities inside the radius**

Create `docs/coverage.md`. The origin is Westham, Feldkirchen-Westerham, **Landkreis Rosenheim** — and it sits on the Miesbach boundary, which is why coverage cannot be assumed from one publisher:

```markdown
# Coverage map

Origin: Westham, Feldkirchen-Westerham (Lkr. Rosenheim).

Coverage is a property of the *publisher*, not of the radius. OVB-Heimatzeitungen
covers Stadt and Lkr. Rosenheim, Lkr. Mühldorf and western Lkr. Traunstein.
Lkr. Miesbach (Miesbacher Merkur) and Lkr. Ebersberg (Ebersberger Zeitung)
belong to the Ippen/Münchner Merkur group and are **not** covered by OVB.

| Gemeinde | Landkreis | Zeitung / Verlag | Amtsblatt | Status |
|---|---|---|---|---|
| Feldkirchen-Westerham | RO | OVB (Mangfall-Bote) | yes | covered |
| Bruckmühl | RO | OVB (Mangfall-Bote) | yes | covered |
| Bad Aibling | RO | OVB (Mangfall-Bote) | yes | covered |
| Kolbermoor | RO | OVB | yes | covered |
| Rosenheim | RO | OVB | yes | covered |
| Wasserburg a. Inn | RO | OVB (Wasserburger Ztg.) | yes | covered |
| Irschenberg | MB | Miesbacher Merkur (Ippen) | yes | **dark — Amtsblatt only** |
| Weyarn | MB | Miesbacher Merkur (Ippen) | yes | **dark — Amtsblatt only** |
| Valley | MB | Miesbacher Merkur (Ippen) | yes | **dark — Amtsblatt only** |
| Holzkirchen | MB | Miesbacher Merkur (Ippen) | yes | **dark — Amtsblatt only** |
| Otterfing | MB | Miesbacher Merkur (Ippen) | yes | **dark — Amtsblatt only** |
| Aying | M | Merkur (Ippen) | yes | **dark — Amtsblatt only** |
| Glonn | EBE | Ebersberger Ztg. (Ippen) | yes | **dark — Amtsblatt only** |

The dark rows are why the Gemeindeblatt adapter matters more than any portal:
in Miesbach and Ebersberg it is the *only* configured route into the radius
until an Ippen/Merkur source exists.
```

Verify each Landkreis assignment against the municipality's own website before relying on it — this table is the plan's best reconstruction, not a fetched dataset.

- [ ] **Step 2: Find each municipality's bulletin index URL**

For every row above, open the Gemeinde website and find the Amtsblatt / Mitteilungsblatt archive page (usually `/rathaus/amtsblatt` or `/buergerservice/mitteilungsblatt`). Record the **index** page, not an individual PDF — the adapter walks the index.

- [ ] **Step 3: Populate `gemeindeblatt_pdf`**

In **both** `config/sources.yaml` and `src/hofradar/_config_defaults/sources.yaml`, replace the `gemeindeblatt_pdf` entry's options and enable it:

```yaml
  - key: gemeindeblatt_pdf
    name: "Gemeinde-/Amtsblätter (PDF)"
    role: local
    adapter: pdf_bulletin
    region: "Landkreis Rosenheim / Miesbach / Ebersberg / Mühldorf"
    reliability: 0.85
    enabled: true
    rate_limit_seconds: 4.0
    terms_checked_at: 2026-09-03
    terms_excerpt: >
      Municipal official gazettes, published for public notice by statute.
      Each index checked for robots.txt; no clause restricting retrieval.
    notes: >
      Chiffre ads live here, and in Lkr. Miesbach and Ebersberg this is the
      only configured route into the radius. Requires the [pdf] extra.
    options:
      bulletins:
        - "https://www.feldkirchen-westerham.de/rathaus/amtsblatt/"
        # ... one entry per row of docs/coverage.md, dark rows first
```

Dark rows first, deliberately: they are the ones nothing else covers.

- [ ] **Step 4: Seed the broker feeds**

Open `https://ovbimmo.de/anbieter` — OVB's directory of the regional brokers it aggregates. For each broker inside the radius, visit their own site and check for `/feed`, `/rss`, or `/sitemap.xml`. Add what you find:

```yaml
  - key: generic_rss
    # ... existing fields unchanged ...
    options:
      feeds:
        - "https://<broker>.de/feed"
```

```yaml
  - key: generic_sitemap
    # ... existing fields unchanged ...
    options:
      sites:
        - url: "https://<broker>.de/sitemap.xml"
          include_patterns: ["/objekt/", "/immobilie/"]
          max_pages: 150
```

This is the point of the directory: it converts OVB's aggregation work into config for adapters that already exist and are already tested. It is worth doing even if Task 4 never ships.

- [ ] **Step 5: Sync and verify**

```bash
python scripts/sync_config_defaults.py
python -m pytest -q && ruff check src tests
```

Expected: all pass, config-drift check green.

- [ ] **Step 6: Run one real crawl and read the yield table**

```bash
hofradar run --sources gemeindeblatt_pdf,generic_rss,generic_sitemap
```

Then open the weekly report and read the "Quellen-Ausbeute" table added in Plan A Task 7. **This number is the point of the task.** Record it in `docs/coverage.md`.

- [ ] **Step 7: Commit**

```bash
git add config/sources.yaml src/hofradar/_config_defaults/sources.yaml docs/coverage.md
git commit -m "feat(config): populate the bulletin, feed and sitemap lists the adapters were waiting on"
```

---

## Task 3: A dark municipality must be visible (closes B8)

Premortem B8: the config carries a source *registry*, not a coverage *map*, so nothing ever showed that the circle around Westham spills over the Miesbach line. An empty options list produces no errors, no failed runs and no red CI — just a source that dutifully reports zero forever. The Sacherl in Weyarn ran in the Miesbacher Merkur and the Gemeindeblatt, sold in four weeks, and the database has no row for it.

**Files:**
- Modify: `src/hofradar/report/yield_stats.py` (created in Plan A Task 7)
- Modify: `src/hofradar/report/render.py`
- Create: `tests/report/test_coverage_map.py`
- Modify: `docs/MODULE_API.md`

**Interfaces:**
- Consumes: `Observation`, `Property.town`, and the municipality list from `docs/coverage.md`.
- Produces: `coverage_by_municipality(session, *, since, expected: list[str]) -> list[MunicipalityCoverage]` with fields `town: str`, `observed: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/report/test_coverage_map.py`:

```python
"""Which municipalities inside the radius produced nothing at all?

Zero observations from a town is ambiguous on its own - a quiet market and an
uncovered one look identical. Naming the expected municipalities up front is
what turns the silence into a finding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.report.yield_stats import coverage_by_municipality
from tests.report.conftest import make_observation, make_property, make_source  # existing helpers

EXPECTED = ["Feldkirchen-Westerham", "Bruckmühl", "Weyarn", "Holzkirchen"]


def test_a_municipality_with_no_observations_reports_zero(session) -> None:
    source = make_source(session, key="gemeindeblatt_pdf")
    prop = make_property(session, source=source, town="Bruckmühl")
    make_observation(session, property=prop, source=source)
    session.flush()

    rows = coverage_by_municipality(
        session, since=datetime.now(UTC) - timedelta(days=28), expected=EXPECTED
    )

    by_town = {row.town: row.observed for row in rows}
    assert by_town["Bruckmühl"] == 1
    assert by_town["Weyarn"] == 0
    assert by_town["Holzkirchen"] == 0


def test_every_expected_municipality_appears_even_with_no_data(session) -> None:
    rows = coverage_by_municipality(
        session, since=datetime.now(UTC) - timedelta(days=28), expected=EXPECTED
    )

    assert [row.town for row in rows] == EXPECTED
    assert all(row.observed == 0 for row in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/report/test_coverage_map.py -v`
Expected: FAIL — `ImportError: cannot import name 'coverage_by_municipality'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/hofradar/report/yield_stats.py`:

```python
@dataclass(slots=True)
class MunicipalityCoverage:
    town: str
    observed: int


def coverage_by_municipality(
    session: Session, *, since: datetime, expected: list[str]
) -> list[MunicipalityCoverage]:
    """Observations per expected municipality, including the ones with none.

    The zeros are the entire point, which is why ``expected`` is a required
    argument rather than something derived from the data: a municipality that
    produced nothing cannot appear in a query over what was produced. Naming
    the towns we believe are in range is what makes their silence legible.
    """
    counts = dict(
        session.execute(
            select(Property.town, func.count(func.distinct(Property.id)))
            .join(Observation, Observation.property_id == Property.id)
            .where(Observation.scraped_at >= since, Property.town.in_(expected))
            .group_by(Property.town)
        ).all()
    )
    return [MunicipalityCoverage(town=town, observed=counts.get(town, 0)) for town in expected]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/report/test_coverage_map.py -v`
Expected: PASS

- [ ] **Step 5: Put the dark municipalities in the weekly report**

In `src/hofradar/report/render.py`, add a section that lists only the towns with zero observations over the window:

```markdown
## Dunkle Gemeinden (letzte 4 Wochen ohne einen einzigen Treffer)

Weyarn · Valley · Otterfing

Eine Gemeinde ohne Treffer ist keine ruhige Gemeinde — sie ist eine ungedeckte.
```

Read the expected list from `config/search.yaml` under a new `coverage.municipalities` key, populated from `docs/coverage.md`, so the report and the map cannot drift apart. Run `python scripts/sync_config_defaults.py` after adding it.

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass.

- [ ] **Step 7: Document it**

Add `coverage_by_municipality` to the `hofradar.report` block in `docs/MODULE_API.md`.

- [ ] **Step 8: Commit**

```bash
git add src/hofradar/report tests/report/test_coverage_map.py config/search.yaml src/hofradar/_config_defaults/search.yaml docs/MODULE_API.md
git commit -m "feat(report): name the municipalities inside the radius that produced nothing"
```

---

## Task 4: The ovbimmo.de adapter

**What the source is** (verified from the search index; the site itself was unreachable from the dev container): a regional portal that aggregates *"all offerings from regional brokers as well as exclusive ads from the daily newspapers"*, and that displays **private seller listings with Chiffre references**. Faceted URLs (`/kaufen/haus/feldkirchen-westerham`), detail pages at `/immobilien/{title-slug}-{6-char-uppercase-id}` (observed: `GZJFXJ`, `GFG5NV`, `G7V48N`, `GJ5KND`, `GDWZ56`), a provider directory at `/anbieter`, and the newspaper's own classified desk as a provider at `/anbieter/ovb-anzeigen-2187`.

The existing `hidden_score` vocabulary already fires natively on this inventory: `chiffre` (+15), `privatverkauf` (+15), `kein_makler` (+5), `preis_auf_anfrage` (+8).

**Files:**
- Create: `src/hofradar/sources/adapters/ovbimmo.py`
- Create: `tests/sources/adapters/test_ovbimmo.py`
- Create: `tests/sources/fixtures/ovbimmo_search_feldkirchen.html`, `tests/sources/fixtures/ovbimmo_detail.html`
- Modify: `src/hofradar/sources/adapters/__init__.py`
- Modify: `config/sources.yaml` + `src/hofradar/_config_defaults/sources.yaml`, `docs/SOURCES.md`

**Interfaces:**
- Consumes: the `SourceAdapter` base, which flattens the source's fields onto the adapter — use `self.base_url` and `self.options`, **not** `self.source`. All HTTP goes through `await self.client.get(url)`; the `PoliteClient` behind it handles per-host rate limiting, robots.txt and the User-Agent, so an adapter cannot skip them. Also `RawListing`, `raw_listing_from_html`, `SourceConfig.listing_ttl_days` (Task 1).
- Produces: `OvbimmoAdapter` with `key = "ovbimmo"`, registered in `ADAPTERS`.
- **Not overridden:** `verify()`. The base implements it via `_verify_impl`, including the `can_verify` role gate and the body scan for German gone-markers ("nicht mehr verfügbar", "wurde verkauft"). That body scan matters more here than on a static tree, because a portal often renders 200 for a withdrawn advert.

- [ ] **Step 1: Read the terms — a real gate**

From a networked machine:

```bash
curl -s https://ovbimmo.de/robots.txt
curl -s https://ovbimmo.de/agb | sed -n '1,200p'
curl -s https://ovbimmo.de/datenschutz | grep -i -B2 -A6 'automat\|scrap\|crawl\|robot'
curl -sI https://ovbimmo.de/kaufen/haus/feldkirchen-westerham
```

**Decision point, and it is binary.** If the AGB restricts automated retrieval in terms comparable to ImmobilienScout24's, invariant 7 admits no argument: finish the adapter, ship it `enabled: false` beside the three portal adapters, and **switch to the Suchabo route** — OVB runs an official search subscription that emails matching new listings daily. That is a sanctioned push channel with no robots question at all, and it would be a new `email_ingest` adapter (no email path exists in the codebase today). Record which branch you took in `docs/SOURCES.md` either way.

- [ ] **Step 2: Capture fixtures**

```bash
curl -s https://ovbimmo.de/kaufen/haus/feldkirchen-westerham \
  > tests/sources/fixtures/ovbimmo_search_feldkirchen.html
curl -s https://ovbimmo.de/immobilien/<a-real-slug>-GZJFXJ \
  > tests/sources/fixtures/ovbimmo_detail.html
```

Open both and confirm the listing cards are server-rendered. **If the results are injected by JavaScript, `selectolax` will see an empty shell** — stop and reconsider, because rendering a JS page is a different (and much heavier) adapter than this plan describes.

- [ ] **Step 3: Write the failing test**

Create `tests/sources/adapters/test_ovbimmo.py`:

```python
"""The OVB regional portal adapter.

The two behaviours worth pinning: the six-character detail id is the external
identifier (the title slug is cosmetic and changes when a broker rewrites the
headline), and discovery walks one faceted URL per in-radius municipality
rather than crawling the site.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from hofradar.sources import get_adapter
from tests.sources.conftest import make_source_config  # existing helper

FIXTURES = Path(__file__).parent.parent / "fixtures"
BASE = "https://ovbimmo.de"
DETAIL = f"{BASE}/immobilien/historischer-einfirsthof-feldkirchen-westerham-GZJFXJ"


@pytest.fixture
def adapter():
    return get_adapter(
        make_source_config(
            key="ovbimmo",
            adapter="ovbimmo",
            base_url=BASE,
            options={"municipalities": ["feldkirchen-westerham"]},
        )
    )


@respx.mock
async def test_external_id_is_the_six_character_code_not_the_slug(adapter) -> None:
    respx.get(DETAIL).mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "ovbimmo_detail.html").read_text(encoding="utf-8")
        )
    )

    listing = await adapter.fetch_detail(DETAIL)

    assert listing is not None
    assert listing.external_id == "GZJFXJ"
    assert listing.source_key == "ovbimmo"


@respx.mock
async def test_discover_requests_one_faceted_url_per_configured_municipality(
    adapter, profile, keywords
) -> None:
    search = respx.get(f"{BASE}/kaufen/haus/feldkirchen-westerham").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "ovbimmo_search_feldkirchen.html").read_text(encoding="utf-8")
        )
    )
    respx.get(url__regex=rf"{BASE}/immobilien/.+").mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "ovbimmo_detail.html").read_text(encoding="utf-8")
        )
    )

    listings = [item async for item in adapter.discover(profile, keywords)]

    assert search.called
    assert listings, "the search fixture must contain at least one detail link"
    assert all(listing.source_key == "ovbimmo" for listing in listings)


@respx.mock
async def test_a_gone_listing_is_reported_as_gone(adapter) -> None:
    respx.get(DETAIL).mock(return_value=httpx.Response(404))

    still_live, status = await adapter.verify(DETAIL)

    assert still_live is False
    assert status == 404
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/sources/adapters/test_ovbimmo.py -v`
Expected: FAIL — `KeyError: 'ovbimmo'` from `get_adapter`.

- [ ] **Step 5: Write minimal implementation**

Create `src/hofradar/sources/adapters/ovbimmo.py`:

```python
"""OVB's regional property portal for the Rosenheim / Chiemgau / Inn-Salzach area.

Why a newspaper portal rather than a national one: OVB aggregates the regional
brokers *and* the classified ads from its own daily papers, including private
sellers with Chiffre references. That second inventory is the reason this
source exists - it is the closest thing to the Gemeindeblatt small ads that
reaches a machine-readable page, and the existing hidden_score vocabulary
already recognises every signal it carries.

Discovery walks one faceted search URL per configured municipality rather than
crawling: the site exposes /kaufen/{typ}/{ort} directly, so there is no reason
to ask for anything we do not want. Detail pages carry a stable six-character
identifier that survives a broker rewriting the headline, which is what makes
deduplication reliable here - the title slug does not.

Adverts run for a fixed paid window, so the registry sets listing_ttl_days and
the lifecycle reads a disappearance after that window as EXPIRED rather than
REMOVED. See docs/DECISIONS.md entry 15.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from hofradar.contracts import RawListing
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.base import SourceAdapter

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import KeywordConfig, SearchProfile

logger = logging.getLogger(__name__)

#: Faceted search: /kaufen/{typ}/{ort}. "haus" excludes the Eigentumswohnungen
#: that dominate the portal and are never what we are looking for.
SEARCH_TEMPLATE = "/kaufen/{property_type}/{municipality}"
DEFAULT_PROPERTY_TYPES: tuple[str, ...] = ("haus", "grundstueck")
#: /immobilien/<cosmetic-slug>-GZJFXJ - the trailing code is the identity.
DETAIL_HREF_RE = re.compile(r"/immobilien/[a-z0-9-]+-([A-Z0-9]{6})(?:[/?#]|$)")


class OvbimmoAdapter(SourceAdapter):
    key = "ovbimmo"

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        base = self.base_url or "https://ovbimmo.de"
        options = self.options or {}
        municipalities: list[str] = options.get("municipalities") or []
        property_types: tuple[str, ...] = tuple(
            options.get("property_types") or DEFAULT_PROPERTY_TYPES
        )
        if not municipalities:
            logger.warning("ovbimmo: no municipalities configured; discovering nothing")
            return

        seen: set[str] = set()
        for municipality in municipalities:
            for property_type in property_types:
                path = SEARCH_TEMPLATE.format(
                    property_type=property_type, municipality=municipality
                )
                response = await self.client.get(urljoin(base, path))
                if response.status_code >= 400:
                    continue
                for node in HTMLParser(response.text).css("a"):
                    href = node.attributes.get("href") or ""
                    match = DETAIL_HREF_RE.search(href)
                    if match is None or match.group(1) in seen:
                        continue
                    seen.add(match.group(1))
                    listing = await self.fetch_detail(urljoin(base, href))
                    if listing is not None:
                        yield listing

    async def fetch_detail(self, url: str) -> RawListing | None:
        response = await self.client.get(url)
        if response.status_code >= 400:
            return None
        listing = raw_listing_from_html(self.key, url, response.text)
        match = DETAIL_HREF_RE.search(url)
        if match is not None:
            listing.external_id = match.group(1)
        listing.http_status = response.status_code
        return listing
```

In `src/hofradar/sources/adapters/__init__.py`, import `OvbimmoAdapter` and add `"ovbimmo": OvbimmoAdapter` to `ADAPTERS`.

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/sources/adapters/test_ovbimmo.py -v`
Expected: PASS

- [ ] **Step 7: Add the registry entry**

In **both** `config/sources.yaml` and `src/hofradar/_config_defaults/sources.yaml`:

```yaml
  - key: ovbimmo
    name: "OVBimmo (OVB Heimatzeitungen)"
    role: local
    adapter: ovbimmo
    base_url: "https://ovbimmo.de"
    region: "Lkr. Rosenheim / Mühldorf / West-Traunstein"
    reliability: 0.75
    enabled: true      # false if Step 1 found the AGB restricts automated access
    rate_limit_seconds: 5.0
    listing_ttl_days: 14
    terms_checked_at: 2026-09-03   # replace with the day you ran Step 1
    terms_excerpt: >
      PASTE THE ACTUAL robots.txt AND AGB FINDING FROM STEP 1 HERE.
      If automated retrieval is restricted, set enabled: false and take the
      Suchabo email route instead.
    notes: >
      A regional newspaper portal, not a national one - it carries the brokers
      OVB aggregates plus the classified ads from its own papers, including
      private sellers with Chiffre. Covers Lkr. Rosenheim but NOT Lkr. Miesbach
      or Ebersberg (Ippen/Merkur territory) - see docs/coverage.md.
      Crawl Wednesdays and Saturdays; that is when print publishes.
      reliability 0.75 keeps it below the small_local_source threshold, which
      is correct: it is a regional paper, not a portal.
    options:
      municipalities:
        - feldkirchen-westerham
        - bruckmuehl
        - bad-aibling
        - kolbermoor
        - rosenheim
        - wasserburg-am-inn
      property_types: ["haus", "grundstueck"]
```

Then: `python scripts/sync_config_defaults.py`

- [ ] **Step 8: Run the whole suite**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass.

- [ ] **Step 9: Document it**

Add `ovbimmo` to the "What ships enabled" table in `docs/SOURCES.md`, and record the coverage limitation explicitly:

```markdown
`ovbimmo` covers Lkr. Rosenheim, Mühldorf and western Traunstein. It does
**not** cover Lkr. Miesbach or Ebersberg — those are Ippen/Münchner Merkur
titles with a different portal. Until an Ippen source exists, those
municipalities are served only by `gemeindeblatt_pdf`. See `docs/coverage.md`.
```

- [ ] **Step 10: Commit**

```bash
git add src/hofradar/sources tests/sources config/sources.yaml src/hofradar/_config_defaults/sources.yaml docs/SOURCES.md
git commit -m "feat(sources): add the OVB regional press portal adapter"
```

---

## Explicit non-goals

- **The Ippen/Merkur adapter** (Miesbacher Merkur, Ebersberger Zeitung). It is the other half of the radius and it is genuinely needed — but Task 2 covers those municipalities via their Amtsblätter first, which is cheaper and needs no new code. Task 3 keeps the gap visible so it cannot be forgotten, which is precisely what failed in premortem B8.
- **The `email_ingest` / Suchabo adapter.** Held as the documented fallback in Task 4 Step 1 rather than built speculatively. If the AGB closes the crawl route it becomes the next plan, and it generalises to every portal that offers alerts.
- **`market_tempo`, the Denkmal cost branch, the Denkmal-Atlas enrichment.** All gated on Plan A Task 7's yield number.
- **A `FAST` tempo class justified by OVB's 14-day window.** Premortem B7 is explicit: do not design an abstraction whose only producer is a source whose terms nobody has read. Task 1 handles the 14-day window in the lifecycle, where it is a fact about advert retention rather than a scoring abstraction.

---

## Self-Review

**Spec coverage.** B3 → Task 1 (`EXPIRED` + `listing_ttl_days`). B8 → Task 2 (fill the lists, dark rows first) and Task 3 (dark municipalities in the report). The hidden assumption ("coverage, not ranking") → Task 2's position ahead of Task 4, and the non-goals deferring every scoring change. B7 → Task 4 Step 1's binary gate with a named fallback. B1 is inherited from Plan A Task 7's yield table, which Task 2 Step 6 requires you to read.

**Placeholder scan.** Three intentional gaps, each marked and each impossible to fill from this container: the `terms_excerpt` values in Task 4 Step 7 (needs the Step 1 curls), the bulletin index URLs in Task 2 Step 3 (needs each Gemeinde's website), and the broker feed URLs in Task 2 Step 4 (needs `/anbieter`). Task 2 Steps 2 and 4 are the instructions for filling them. No code step contains a placeholder.

**Type consistency.** `listing_ttl_days` is `int | None` on both `SourceConfig` (Task 1 Step 3) and the `Source` ORM column, and `_absence_status` guards `if not ttl` so both `None` and `0` mean "no window". `MunicipalityCoverage` fields (`town`, `observed`) match between Task 3's test and implementation. `SourceYield` from Plan A Task 7 is untouched — Task 3 appends to that module rather than modifying it. `DETAIL_HREF_RE` group 1 is the six-character id, used identically in `discover` and `fetch_detail`.

**Cross-plan check.** Task 1's rework of `mark_missing` builds directly on Plan A Task 3's `missing`/guard structure and must be applied after it; the dependency is stated in the header. `EXPIRED` is deliberately absent from `GONE_STATUSES` in both `scoring/engine.py` and `scoring/signals.py` — a reviewer should check both files, since each defines its own copy of that frozenset.
