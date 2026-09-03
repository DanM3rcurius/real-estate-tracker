# Architecture decisions

Each entry is a call that would be expensive to reverse later, the alternatives
that were weighed, and the reason.

---

## 1. Scores are computed at query time, never stored on the property

**Decision.** `Property` holds facts. Scores live in a separate `scores` table
keyed by `(property_id, profile_hash)`, where `profile_hash` is a stable hash of
the user's `SearchProfile`.

**Why.** The requirement is that distance and budget are adjustable. If a score
were a column on the property, every slider move would either be a lie (stale
numbers) or a destructive rewrite (history lost). With a profile-keyed cache,
moving a slider produces a *new* set of scores alongside the old ones, the facts
and the observation history are untouched, and two profiles can be compared
side by side.

**Alternatives considered.**
- Score columns on `properties`, recomputed in place. Cheapest, but destroys the
  ability to compare profiles and makes any concurrent run racy.
- Compute on every request with no cache. Correct but O(n) Python per keystroke;
  unusable past a few thousand properties.
- Materialised view per profile. Postgres-only; we want SQLite to work.

---

## 2. `properties` vs append-only `observations`

**Decision.** Every crawl writes an immutable `Observation` row. The `Property`
row is a derived, conservatively-updated summary. Price history, status history
and change detection all read from observations.

**Why.** This is the single feature that turns a scraper into a radar. Without
it you cannot say "price fell from 690k to 595k" — you can only say "595k".

**Cost.** Storage grows linearly with runs × listings. At one user's scale
(thousands of properties, weekly runs) this is megabytes per year. Accepted.

---

## 3. Air distance and driving distance are separate facts, and neither implies the other

**Decision.** Two columns, `distance_air_km` and `distance_driving_km`, plus a
`routed` boolean on the geo result. `within_driving_radius()` returns **`None`**
when the road distance is unknown, forcing callers to decide consciously. The UI
renders an unrouted property as *„Fahrstrecke: nicht geprüft"*, never as its air
distance.

**Why.** 79 km straight line can be 134 km of road through the Alpine foreland.
Conflating them is the exact failure this system exists to prevent, and a
`None` that must be handled is much harder to ignore than a plausible-looking
number.

---

## 4. Source *role* decides what a source is allowed to prove

**Decision.** `primary` / `local` / `discovery`. Only the first two may set
`verification_status = verified`, `last_verified`, or `source_date`. Only they
count towards freshness. Only their *silence* can transition a listing to
`REMOVED`.

**Why.** An aggregator's cached copy of a six-month-old advert is not evidence
that the advert is live, and a search engine's crawl date is not the seller's
publication date. Encoding this as a property of the source, checked in the
lifecycle writer, means no future adapter can accidentally launder a stale copy
into a fresh-looking hit.

---

## 5. httpx + selectolax adapters instead of Scrapy

**Decision.** A small async `SourceAdapter` base class over `httpx`, parsed with
`selectolax`, rather than the Scrapy framework the blueprint suggests.

**Why.** Scrapy's Twisted reactor does not coexist comfortably with an asyncio
FastAPI process, and running it out-of-process would mean a job queue and a
second deployment unit for what is currently one user's weekly job. We keep the
part of Scrapy's design that actually matters — the strict separation of
discovery, parsing, normalisation, deduplication and storage — as package
boundaries instead of framework classes.

**Reversible?** Yes, cheaply: adapters yield `RawListing` and nothing downstream
knows how it was fetched. A Scrapy spider producing `RawListing` would drop in.

---

## 6. SQLite by default, Postgres by environment variable

**Decision.** `HOFRADAR_DATABASE_URL` unset → a WAL-mode SQLite file. Set → that
DSN. The models use no dialect-specific types (JSON columns, string enums).

**Why.** "I can access it via web" should not require provisioning a database.
One user, a few thousand properties and a weekly write burst is comfortably
inside SQLite's envelope. The escape hatch costs one environment variable.

**Consequence.** The blueprint's recommendation of Postgres is honoured as the
production path, not as the barrier to entry.

---

## 7. Server-rendered Jinja + HTMX, no build step

**Decision.** No npm, no bundler, no SPA. HTMX and Leaflet are vendored into the
repository rather than loaded from a CDN.

**Why.** The UI is sliders, lists and a map. A build toolchain would be the
largest source of future breakage in a project whose owner wants to change a
scoring weight, not maintain a frontend. Vendoring means the app works on a
machine with no outbound internet.

---

## 8. The big portals ship disabled

**Decision.** Kleinanzeigen, ImmobilienScout24 and Immowelt adapters exist,
are tested against fixtures, and are `enabled: false`. No bot-defence evasion,
no CAPTCHA solving, no proxy rotation, no fingerprint spoofing is implemented.
When a request is blocked the adapter records the failure and stops.

**Why.** Those sites' terms restrict automated access, and unattended crawling
from a server IP is both a legal and an operational dead end. The honest design
is: the adapters are available for personal, low-rate use from the user's own
machine, and the defaults are the sources that welcome being read — the official
ZVG register, broker feeds, municipal bulletins — which is also where the
genuinely *hidden* listings are.

**Consequence.** Day-one coverage comes from ZVG + your own regional feeds +
the paste box. See `docs/SOURCES.md`.

---

## 9. The LLM runs last, on ≤100 candidates, and may not change a number

**Decision.** `gates.llm_review_size` caps the batch. Its output lands in
`llm_summary`, `llm_risks` and an `evidence["llm_review"]` entry — never in
`price`, `land_sqm` or any distance.

**Why.** Cost is bounded and predictable regardless of crawl size, and a
hallucinated square-metre figure can never enter the ranking. Without an API key
the stage is skipped and the deterministic pipeline is unaffected.

---

## 10. No migration framework in v0.1

**Decision.** `init_db()` runs `create_all()`. Alembic is not wired up.

**Why.** With a single user and no production data yet, a migration framework is
ceremony. The moment the schema changes against real data, add Alembic and
autogenerate an initial revision from the current models — the models are
already written to be autogenerate-friendly.

**Risk accepted.** A schema change before that point means recreating the
database or hand-writing an `ALTER TABLE`.
