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

---

## 11. One shared password, not a user model

**Decision.** A single password in the environment, verified in constant time
against a PBKDF2 hash, plus an HMAC-signed session cookie that carries nothing
but an expiry. No users table, no registration, no reset flow. The gate is not
installed at all when no password is configured.

**Why.** There is exactly one user, and every part of an account system that
was built anyway would be a part that could be got wrong. The things that
actually matter on a public URL are covered: constant-time comparison so the
password cannot be discovered a byte at a time, an unforgeable and unreplayable
cookie, a persisted signing key so restarts do not log you out, a login
throttle so the URL is not a free brute-force oracle, `401` rather than an HTML
login page for API and HTMX callers, a `/healthz` that tells an anonymous
caller nothing, and a validated `next` parameter so the form cannot be turned
into an open redirect.

**Alternatives considered.**
- HTTP Basic auth. Two lines of code, but no logout, an ugly browser prompt,
  and the password on every single request.
- A real identity provider (OAuth, Authelia in front). Correct if this is ever
  shared with a second person; today it is more moving parts than the app.
- No auth, bind to localhost only. What Option C in `docs/DEPLOY.md`
  recommends, but it cannot be the only answer once there is a public URL.

**When to revisit.** The moment a second person needs access. That is an
identity provider in front of the app, not a bigger `auth.py`.

---

## 12. Hosting needs a disk and a long-running process, which rules out Vercel

**Decision.** Target Fly.io, Railway or Render — a container with a persistent
volume mounted at `/data` and an always-on process. Not Vercel, Netlify or
Cloudflare Pages.

**Why.** Serverless platforms give an ephemeral filesystem, a request-duration
cap and a cron that is just a timed HTTP call. This app's product *is* the
database that remembers across months; a crawl is a multi-minute background job
across a dozen sources; and the weekly run needs a process that stays alive.
Each of those is a straight mismatch. The UI alone could run on Vercel against
an external Postgres, but the crawler and scheduler would still need a real
host, so it trades one deployment unit for three.

**Consequence.** The Dockerfile and `docker-compose.yml` are the deployment
contract, and `fly.toml` is the worked example. A volume at `/data` is not
optional anywhere.

---

## 13. Alembic, from the first schema change against real data

**Decision.** `alembic upgrade head` is now the schema command. `init_db()`
keeps `create_all()` for a brand-new database, but no column may be added by
`create_all()` against an existing one.

**Why.** Decision 10 deferred this until "the schema changes against real
data", which the Denkmalbörse work reached. `create_all()` is a no-op on an
existing table, so the alternative to a migration is recreating the database —
and the append-only `observations` history that decision 2 calls the product
cannot be refetched from any source. Losing it is the one failure in this area
that no later work can repair.

**Supersedes.** Decision 10, which remains as the record of why it was deferred.

**Consequence.** `scripts/backup_db.py` runs before any migration, and
`render_as_batch=True` is set because SQLite cannot ALTER a column in place.

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
