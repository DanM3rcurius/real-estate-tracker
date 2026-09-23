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

**Not enough on its own** — see decision 17. Writing the migration was half the
work; nothing ran it, and the deployment carried on calling `create_all()`.

---

## 14. A source's yield is reported, and a source's terms gate its enablement

**Decision.** The weekly report carries per-source *in-radius* yield, and
`SourceConfig` refuses `enabled: true` without a recorded `terms_checked_at`
and `terms_excerpt`.

**Why.** Two failures that a green test suite cannot detect. A source can parse
perfectly and produce nothing inside the radius, which is invisible unless the
number is printed; and a source's terms can forbid the whole approach, which is
invisible until somebody reads them — after the abstractions built on it exist.
Both are cheap to prevent and expensive to discover late.

**Consequence.** New sources carry a yield expectation before they are built.
The Denkmalbörse's is 5 in-radius objects across its first four runs; below
that, the dependent scoring and cost work does not start. The Denkmalbörse
shipped `enabled: false` until its terms were actually read; it was enabled on
2026-09-03 once `robots.txt` (absent, HTTP 404) and the Impressum's
Nutzungsbedingungen had been fetched and recorded verbatim in `terms_excerpt`
(see `docs/SOURCES.md`). The gate did its job: the source stayed off until a
finding replaced the placeholder.

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

**Consequence (added by the whole-branch review).** Two seams follow from this
decision, and both are decided here rather than left to the reader:

*The retention policy explains a disappearance, never an empty run.*
`mark_missing`'s empty-seen-set guard therefore runs **before** the EXPIRED
split, not after it. Gating it on what survives the split left a source with
`listing_ttl_days` with no absence guard at all: every missing row was pulled
out as an expiry first, both guards were handed an empty list, and a template
change wrote one false EXPIRED row per listing and returned `[]` — a run
indistinguishable from a quiet one. The *fraction* guard stays on the far side
of the split, which is what lets a genuine fortnightly mass-expiry through: it
has a non-empty seen-set, because the ads that aged out are missing while the
rest of the inventory is still listed.

*A renewed advert is a reappearance in the history and not news in the digest.*
`EXPIRED` is in `_rules.DORMANT_STATUSES`, so an advert that ran out and came
back writes a `ChangeKind.REACTIVATED` row — invariant 2 says a reappearance
*is* a reactivation, and the append-only history is the product. Keeping the
fortnightly renewal out of the ten-entry digest is then a reporting judgement
made in `hofradar.report.data._was_reactivated`, which ignores a reactivation
whose `old_status` is EXPIRED. Nothing newsworthy is lost by that: an advert
down long enough for its return to mean something about the farmstead has
already been moved on to STALE by `apply_stale_rules` (EXPIRED is in
`STALE_ELIGIBLE_STATUSES`), and a reactivation out of STALE does reach the
digest. Suppressing the *event* instead would have bought the same quiet
digest by lying to the history, which is the one thing this project may not do.

---

## 16. An identical canonical listing URL is proof of identity across sources

**Decision.** Two listings that resolve to the same canonical URL are the same
listing, whichever sources found them. `hofradar.dedupe.compare` treats that as
a short-circuit proof alongside `(source_key, external_id)` equality and a
shared image hash, and `find_duplicate` blocks on the stored URL across the
source boundary so the candidate is retrievable in the first place.
`hofradar.dedupe._util.canonical_url` defines "the same URL": it removes a
default port, a `www.` prefix, userinfo, a trailing slash, the http/https
distinction and the `utm_*`/click-id tracking parameters — and nothing else.
Query parameters it does not recognise are kept and sorted, path case is kept,
and **the fragment is kept**.

**Why.** One portal is routinely reached twice: a dedicated adapter and a
syndicated feed of the same site produce byte-identical URLs (verified on the
two committed ovbimmo captures). Dedupe could not join those: both blocking
passes filtered on `Source.key`, `external_id` is one source's private
numbering, and the image-hash escape hatch is closed because nothing populates
`image_hashes` today. The measured verdict for one such pair was
`is_duplicate=False, confidence 0.22`, so one advert became two properties —
two shortlist entries, an inflated `tracked_total`, and a per-source yield
table in which two rows count one physical inventory, corrupting the very
number decision 14 exists to provide.

**Why so narrowly.** The costs are asymmetric in the opposite direction from
the absence guards. A missed join leaves a duplicate that the ordinary
evidence model may still catch and a human can merge; a false join fuses two
farmsteads into one property and destroys both their histories irreversibly.
So the normaliser removes only differences that provably cannot select
different content. `ref`, `source`, `id` and similar are *not* treated as
tracking parameters, because plenty of sites use them to choose what to show.

**The fragment, specifically.** A browser never sends it to a server, so
stripping it looks free — and the first version of this rule did strip it. It
is not free. `hofradar.sources.adapters.pdf_bulletin` gives every hit it finds
inside an Amtsblatt PDF the URL `<pdf_url>#page=<n>` and sets no `external_id`,
so there the fragment is the *only* thing telling two listings apart. Stripping
it merged two farmsteads found on two pages of one bulletin into a single
property at confidence 1.0 — reproduced through the real `ingest` path:
`properties: 1, kind: source_change` where two properties and a `first_seen`
belong. Any source whose listings share one document URL (a CSV with a repeated
`url` column, for instance) has the same shape. Keeping the fragment also costs
nothing for the case this rule exists to serve: the two routes into ovbimmo.de
produce byte-identical URLs with no fragment on either side. That is the test a
candidate normalisation has to pass — provably cosmetic *and* actually in the
way — and the fragment failed both halves of it.

**Alternative rejected.** Widening `compare`'s soft evidence model so that a
title plus a town could carry a cross-source match. That is precisely the
Vogtareuth trap the corroboration rule exists to avoid — three farms in one
village share a town and a plausible title. A URL is not a similarity signal
at all; it is an identifier, which is why it can be proof without loosening
anything else.

---

## 17. The schema is migrated on boot, and the migrations ship in the package

**Decision.** Every process that opens the persistent database calls
`hofradar.db.migrate.ensure_schema()` before serving a request, and the
migrations live in `src/hofradar/migrations/` rather than beside `alembic.ini`.
`init_db()` keeps `create_all()` and is now explicitly the throwaway-database
path that tests use.

**Why.** Decision 13 declared `alembic upgrade head` the schema command and
stopped there. No code path ever ran it: the container's `hofradar init-db &&
hofradar serve` called `create_all()`, which is a no-op on an existing table,
exited 0, and then failed every query touching the changed table with
`no such column: sources.listing_ttl_days` (GitHub issue #7). The image did not
COPY `migrations/` or `alembic.ini` either, so running the upgrade by hand
inside the container was not possible. A schema command nobody invokes and
cannot reach is not a migration framework; it is a file.

**The three states.** A live volume can be at head, one revision behind, or
older than Alembic itself and carrying no `alembic_version` at all. The third
is the common one here, because the database predates decision 13. It is
adopted by stamping the revision whose schema it *actually* has — read from the
schema, not assumed — and then upgraded normally.

**Failing loudly.** `ensure_schema` re-compares the database against the models
afterwards and refuses to return if they still differ. A half-migrated database
that boots is worse than one that does not: the mismatch surfaces later, deep
inside an unrelated page, and `web/lazy.py` reported it as a *missing module*,
which is not where anyone would look. That message now names the database.

**One at a time.** The web app and the scheduler are separate containers on one
database and `docker compose up` starts them together, so both migrate at once.
Reading the revision, deciding and acting happen on different connections, so
without a lock they interleave and the loser dies — "table alembic_version
already exists", or "duplicate column name" once both get as far as the upgrade.
Measured with six concurrent boots, not theorised. SQLite takes an OS file lock
beside the database (`<db>.migrate-lock`, on the same volume the processes
already share); Postgres takes a session advisory lock; the loser then finds the
work done and reports `current`.

**Consequence.** `hofradar migrate` (and `hofradar migrate --check`, which
changes nothing and exits 1 when work is pending) exist for operators. Because
the migrations are inside the package, `alembic.ini` at the repository root
points at `src/hofradar/migrations` — there is one copy, not a synced pair. A
`hofradar.sqlite3.migrate-lock` file appears next to the database; it holds no
data and is safe to delete when nothing is running.

---

## 18. An unlabelled address is parsed, and an unplaceable listing says so

**Decision.** When a source supplies no `location_raw`, `postcode` or `town`,
`normalize_listing` recovers the address from the description, requiring a
postcode *and* a town-shaped word. A listing that still has no town produces a
warning, and `/add` shows it.

**Why.** The paste box parsed `Label: value` lines only, so the shape a human
actually copies out of an exposé — a bare `83569 Vogtareuth, Landkreis
Rosenheim` — was dropped. The parser was never the problem: `parse_location`
reads that string correctly and was simply never given it. One unparsed line
then ended the property's life. No town, so nothing to geocode; no geocode, so
neither distance; `geo_precision='none'` collapses the location-certainty term
and the unrouted cap holds confidence at 65, below the shortlist gate of 70. The
listing saved, got a `public_id`, and never appeared on the Radar — `appears on
the radar: 0 of 1`, with nothing raised anywhere (GitHub issue #3).

**In the normaliser, not the adapter.** `hofradar.normalize` owns parsing, and
the RSS and sitemap detail pages have the same problem: an address in prose
rather than in a field. Fixing it in one adapter would have fixed it once.

**Strictly.** A wrong town is far worse than no town — it geocodes to a real
place somewhere else, and no later stage can tell that it is wrong. So the
shape is postcode plus town, never any five-digit run: `595.000` has
separators, `95000 EUR` has no town after it. A recovered location carries
lower evidence confidence than a stated one, because the field was inferred.

**Not by lowering the gate.** The gate was behaving correctly; the input was
impoverished. A listing that genuinely cannot be placed is still held off the
shortlist — it just no longer does so silently.

**Consequence.** Silence is the failure mode this codebase keeps producing:
entry 17 is the same shape one layer down, and issue #2 was a third. Anything
that quietly drops a load-bearing fact gets a `warnings` entry and a place in
the UI.

---

## 19. A fetched page must prove it is a listing before it becomes a property

**Decision.** Every full-page lift classifies what it fetched —
`_htmlutil.page_kind()` returns `listing`, `index` or `utility` — the kind
rides along on `RawListing`/`NormalizedListing` as `page_kind`, and
`hofradar.lifecycle.ingest` raises `NotAListing` for anything that is not a
`listing`, **before** `find_duplicate` and before the observation. A refused
page leaves no row of any kind.

**Why.** A property titled *„Merkliste"* — a portal's bookmark widget, every
fact `k. A.` — reached the radar with a `public_id`, a geocode and a score
(GitHub issue #10). Two independent gaps produced it: the title chain
preferred `og:title`, which on a portal is the page's marketing name rather
than the property's; and nothing anywhere asked whether the fetched page was
an advert at all. A crawler reaches `/merkliste`, `/suche` and a login form on
exactly the same code path as a real exposé.

**Page shape, not fact count.** The obvious gate — "no price, no area, no
year, no location: drop it" — does not work, and it is worth saying why in
writing so nobody re-proposes it. `extract_labeled_fields` scans the whole
page, so the real OVB search capture in `tests/fixtures/html` yields a
complete, plausible set of facts assembled from *different result cards*:
`property_type bauernhaus`, `land_sqm 461`, `living_sqm 434`, `town
Stephanskirchen 83071` — four adverts wearing one coat. A fact-count gate
passes exactly the page it has to reject. A page that lists twenty properties
is not a poorly-described property; it is a different kind of page.

**The signals, weakest last.** The URL naming a portal function
(`UTILITY_PATH_RE`, whole path segments only, so `/suchergebnisse` is not
`/suche`); the page's own headline being one („Merkliste" — the signal that
survives a paste with no URL, which is how the reported one arrived);
schema.org `@type`; and finally many sibling links of one URL shape (20 under
`/immobilien/*` on the search capture, against 5 in the related-ads rail of a
detail page). An index declaration outranks a listing one, because a portal
makes both at once: that same capture declares `SearchResultsPage` *and* a
`Product` named after the page whose `offers` is an `AggregateOffer` over all
186 results. Anything unclassified is a listing — a small broker's detail page
states nothing about itself, and refusing those would be worse than the bug.

**No observation for a refused page.** `Observation` is append-only and is the
history of what a source said about a *listing*. A search page never was one,
so admitting it there would make every count, every yield statistic and the
whole change feed read from a table with portal chrome in it. Invariant 1 is
untouched: `ingest` is still the only writer of `Property` rows; a refusal
simply writes nothing.

**Refusals are counted, never silent** — the lesson of entries 17 and 18
applied to a rejection rather than a fact. `normalize_listing` adds a German
`warnings` line so `/add` can explain it, the paste box turns the refusal into
a sentence instead of a saved property, and `pipeline.runner` counts every
discarded row by reason and logs one `NORMALIZE` entry (`rejected=N` plus the
breakdown) that `/runs` renders. That entry is written even when nothing was
rejected: "nothing was thrown away" and "nobody counted" must stay
distinguishable.

**Defence in depth, and where it is *not*.** The generic sitemap and RSS
adapters also skip `UTILITY_PATH_RE` URLs, so the fetch is never made — but
only *after* `record_enumerated_url`, because invariant 4b is about what the
site still offers and it plainly still offers that URL. Not fetching it is a
routing decision, exactly like the `options.pattern` filter beside it.

---

## 20. Hiding is a triage state; deleting is a guarded, cascading, backed-up act

**Decision.** "Get this off my radar" and "this was never a property" are two
different requests and get two different mechanisms. The first is a triage
state, `user_state="archived"` (the set is `hofradar.db.enums.HIDDEN_USER_STATES`):
the property is left out of every *reader-facing* view — radar, JSON, CSV, map,
weekly digest, LLM feed — while it keeps being crawled, rescored and observed.
The second is `hofradar.lifecycle.delete_property`, reachable from the dossier's
danger zone and from `hofradar delete-property`, which removes the row and every
child table with it after taking a snapshot.

**Why hiding is the default.** The database remembering what it has seen is the
product (entry 2). A farm the user is done with is still evidence: its price
history is a data point about the market, and a property deleted today is a
property re-announced as `FIRST_SEEN` the next time a source carries it, which
invariant 2 forbids. Hiding costs one indexed column read and loses nothing.

**Why "rejected" is not a hide.** The dossier already offered 🚫 *Abgelehnt* and
the radar already offered "auch abgelehnte zeigen", and they were unrelated:
the first is `Property.user_state`, a human verdict that survives every re-run;
the second is `Score.rejected`, the machine's gate, recomputed for every
`profile_hash`. One German word for two facts is why a stored triage verdict
looked ignored (GitHub issue #9). The verdict stays visible — a rejected farm is
one you decided about, not one you want to stop seeing — and the UI now says
*„Abgelehnt (bleibt sichtbar)"* against *„Archiviert – nicht mehr anzeigen"*,
with the gate's own switch renamed to *„vom Bewertungs-Gate aussortierte zeigen"*.
Should the owner later rule that a rejected farm should vanish too, that is one
word added to `HIDDEN_USER_STATES` and nothing else.

**Hiding is never silent.** `ResultSet.hidden_archived` counts what triage took
off the screen and the status line prints it, for the same reason entry 18 ends
the way it does: a row that disappears with no number beside it is the failure
this codebase keeps producing. The counter is its own query, so it is right on
the degraded path too.

**Deleting is guarded three ways.** It is typed out (the `public_id` has to be
entered back, in the form and on the command line the command is a dry run
without `--apply`); it takes a backup first (entry 13's snapshot, moved into
`hofradar.db.backup` so a request handler and an installed wheel can both reach
it); and it refuses outright while another row's `merged_into_id` points at the
property. That last one is not theoretical: the column is `ON DELETE SET NULL`,
so deleting the survivor of a merge would turn every duplicate merged into it
back into a visible property — the opposite of what was asked for. The caller
deletes the duplicates first or not at all.

**On foreign keys.** Eight of the nine child tables are cascaded by the ORM's
`delete-orphan`; `verification_events` has no ORM relationship and depends on
`ON DELETE CASCADE` being *enforced*, which SQLite only does under
`PRAGMA foreign_keys=ON`. `hofradar.db.session` sets it on every connection, but
every test fixture in this repository builds its engine with a bare
`create_engine` and therefore does not — a delete test written on the usual
fixture would have passed while leaving an audit trail behind for a property
that no longer exists. `tests/lifecycle/test_delete.py` builds its engine
through `get_engine` and asserts the pragma before it asserts anything else.

**Alternatives rejected.** A `deleted_at` tombstone column: it is `archived`
with a migration attached, and it would have to be honoured by every query in
the codebase rather than by the one filter pass that already exists. Deleting
from the radar list: the dossier is where the evidence for the decision is, and
a destructive control belongs next to it.

**Consequence.** On a Postgres deployment the web delete refuses, because the
snapshot is the operator's job there; `hofradar delete-property --apply
--no-backup` is the documented escape hatch for someone who has taken their own
dump. And `hofradar.db.enums.HIDDEN_USER_STATES` — not `hofradar.scoring` — owns
the vocabulary, because the web layer applies it in its own filter pass, which
must keep working when the scoring package cannot be imported (`web/lazy.py`).

---

## 21. The Merkliste is a flag, not a triage state; the radar remembers by redirecting

**Decision.** The reader's bookmarked properties (the *Merkliste*) are stored in
`Property.shortlisted_at: DateTime(timezone=True) | None`. `POST /property/
{public_id}/merken` (which toggles it null ↔ now) is the only route that writes
it on a reader's action; the legacy-triage branch and `dedupe.merge` also set it
(see "Legacy silence" below). It is read by `ResultFilters.shortlisted_only: bool`
(query key `merkliste=1`), which the radar and the Merkliste both honour, but the
Merkliste never applies the profile's slider gate to a mark - the sliders only
score and label it, they do not filter it - and its counts (`total_in_db`,
`hidden_archived`) are taken over the marked set, not the whole database. Filter
memory —
every slider, search box term and sort preference — lives in a single cookie
`hofradar_radar`, the canonical query string of `ResultFilters.query_string()`.
A bare `GET /` (or `GET /map`) with no query parameters and a non-empty cookie
answers `303` to the same path with the cookie's query string appended, so the
address bar always shows the real state. The Merkliste route `GET /merkliste`
applies only the *profile* half of the cookie (the two sliders `air_km_max` and
`total_budget_max`), never the *view filters* (search box, status, sort, switches):
a saved `q=Miesbach` must not empty the reader's own bookmarked list.

**Why a separate column.** The list is orthogonal to triage. A farm marked
`Kontaktiert` stays on the Merkliste, and deleting `user_state` does not clear
`shortlisted_at`. The second reason is the #9 lesson: one German word, one fact.
The digest's "shortlist" is the scorer's top ten (see `report/data.py`) and keeps
its name; the reader's list is *Merkliste* everywhere in UI copy. The radio choice
`⭐ Shortlist` left `USER_STATES`, so `Property.user_state` holds only triage:
`watch`, `contacted`, `rejected` and `archived` (where `archived` is "off my
radar" and `rejected` is "I decided about this and it stays visible").

**Legacy silence.** Two places would otherwise silently drop the mark, the shape
of bug this codebase keeps producing (entries 17, 18, 19). A form rendered
before the Merkliste existed still posts `user_state=shortlist`: the triage route
treats it as "put it on the Merkliste", setting `shortlisted_at` if null, clearing
`user_state`, and answering `Gespeichert: ⭐ gemerkt`. The migration adds the
column and converts `user_state='shortlist'` to `shortlisted_at=updated_at` (the
best available timestamp) in one SQL statement, honoring the reader's saved list.
When two properties merge, `dedupe.merge` picks the earlier `shortlisted_at` from
either side (`min(filter(None, ...))`), so absorbing a duplicate never un-marks a
farm.

**Why a cookie plus 303, not localStorage.** The URL is the truth. `ResultFilters.
query_string()` already emits the canonical query string for permalinks (CSV,
JSON, map exports); it feeds the cookie directly, and a bare `/` redirects to show
it. This costs no Javascript, no server-side session table, no stale-tab
inconsistencies. The existing `syncUrl` in `app.js` keeps working and the dossier's
← Radar crumb and the top-nav links need no change — they are built from `request.
url.path` and already do the right thing when the query string is in the URL. Guard
rails: the cookie is only honoured if it is ≤ 1,000 characters and parses to at
least one known key; the `reset`, `profile`, `limit` and unknown keys are never
stored; overwriting on the next request is silent.

**Why Python, not SQL.** `ResultFilters.as_scoring_filters()` emits `q` (not
`town`) for the search box. The scoring engine applies it in Python after the SQL
query, exactly like `flags` and `has_outbuildings` already are, not as a SQL
`LIKE`. SQLite's `lower()` is ASCII-only, so `lower('Ödhof')` never matches a
casefolded `öd` — and the radar would be wrong for every umlaut village. One
function `hofradar.search.matches_search(prop, needle)` (casefolded substring
over `town`, `postcode`, `district`, `canonical_title`) lives in the neutral
module `hofradar/search.py`, and both `scoring/engine.py` and `web/query.py`
import it from there: the web layer must keep working when the scoring package
is missing (`web/lazy.py`), and the engine must never import the web package.
So the ranked path and the degraded path cannot drift.

**Consequence.** A test must not assert defaults on a bare `/` after it has
requested `/` with parameters in the same `TestClient` — cookies are kept across
requests in the test harness, so a following bare `/` may redirect and the response
will carry the saved filters in its redirect target.

---

## 22. A rental is a price type, not an exclusion keyword, and substance cannot override it

**Decision.** `PriceType.RENT` (`"rent"`) is a value of `price_type`.
`parse_price` returns it for any monthly marker in the price string
("Kaltmiete", "Warmmiete", "/Monat", "mtl.", "zu vermieten"), and
`extract_features` sets `is_rental` for phrasings in the prose that describe
the *offer* ("zu vermieten", "zur Miete", "Kaltmiete", "Kaution", ...).
`normalize_listing` folds both into one fact: `price_type == "rent"`, the
`mietobjekt` tag in `exclusion_flags`, and a German `warnings` line. The
scoring engine rejects on `price_type` alone (`REJECT_RENTAL`,
`RENTAL_NOT_FOR_SALE`) and the crawl loop drops an *unknown* rental before
geocoding, counted as `rental` in the NORMALIZE entry. A rental the database
already holds is not dropped but ingested, so the row learns the fact and the
gate retires it. `_htmlutil.extract_labeled_fields` keeps a rent label in the
lifted value (`"Kaltmiete: 1.250 €"`), because the label is the fact.

**Why.** "Bauernhaus, 1.800 € Kaltmiete" parsed as an asking price of 1,800 €
and reached the top ten as the cheapest farm in Bavaria - the deal score
divides price by area, and nothing anywhere asked whether the figure was
monthly. The negative keyword list had "Wohnung zur Miete" and nothing else
about rent, and even a match there is overridable by farm substance
(`FLAG_EXCLUSION_OVERRIDDEN`, entry on the exclusion gate), which is exactly
wrong for a rental: a Vierseithof "zu vermieten" has all the substance in the
world and is still not for sale. So rent is modelled on the axis it belongs
to - what the number *means* - rather than as one more word in a list whose
matches a Stadel can cancel.

**Why the value is kept.** `price` stays 1,800 with `price_type = rent`
rather than being nulled. The source said it; dropping a fact is the silence
this codebase keeps producing (entries 17-19). The UI renders the type beside
the figure, and a rejected row is off the radar anyway.

**Why "vermietet" does not fire.** "Teilweise vermietet" is a hidden-market
phrase in `config/keywords.yaml` (a farm with a tenant in the Austragshaus is
a farm being sold), and "Mieteinnahmen" is a selling point. Only the offer
counts. The price-field pattern may match a bare "Monat" because it only
ever sees the price field; the prose pattern may not.

**Flats.** The same crawl yielded "3-Zimmer-Wohnung" by the dozen from broker
sitemaps. Those are a *type*, so they went where types go: the `negative`
vocabulary gained the flat words the list never had (Etagenwohnung,
Dachgeschosswohnung, Maisonette, Penthouse, Apartment, "Zimmer-Wohnung" /
"Zi-Whg", matched punctuation-insensitively so "2-Zimmer-Wohnung" and
"2 Zimmer Wohnung" are one term). A farm advertising "zwei Wohnungen" keeps
its substance override; that is the existing gate working as designed.

---

## 23. A System One model answers the typed questions a regex cannot, as evidence read by one rule

**Decision.** `hofradar.triage` asks TypeSafe's Jev (a System One model:
typed questions in, a probability distribution over the allowed labels out,
one fast call) three things about every listing the deterministic filters
could not reject: *is this for sale or for rent?* (`angebotsart`: kauf /
miete / unklar), *what is it?* (`objektart`: hofstelle / haus / wohnung /
grundstueck / gewerbe / sonstiges) and *does the text show real farm
substance?* (`hofsubstanz`, a 0-1 noul). The whole answer - model version,
every probability - is stored as `evidence["triage"]`. One deterministic
function, `triage.decide(verdict, gates, has_substance=...)`, turns it into
a rejection (`miete` or `wohnung` at or above
`gates.triage_reject_min_probability`, default 0.85), a flag
(`TRIAGE_DOUBTS_FARMSTEAD` when the likeliest answer is a rental or a flat
but under the threshold), or nothing. The crawl loop applies it before
geocoding (counted as `triage:miete` / `triage:wohnung`, same known-row rule
as entry 22) and `scoring.engine` applies it again on every rescore
(`TRIAGE_SAYS_RENTAL` / `TRIAGE_SAYS_FLAT`), so a property remembered before
the gate existed meets it the next time it is scored. Without
`TYPESAFE_API_KEY` the stage is absent and the NORMALIZE entry says
`triage: {enabled: false}`; with it, `asked` and `failed` are logged per run.

**Why a System One model and not the LLM review.** The review (entry 9)
runs last on ≤100 survivors because a frontier model call per crawled page
is the cost the ordering exists to avoid. This question is the opposite
shape: every page, three fixed labels, no prose wanted back. Jev is priced
and built for that (its answers are constrained to the labels we chose, so
it cannot invent a fourth kind of dwelling or write a number), which is why
it can sit *before* geocoding, where a Nominatim call per rental is the
expensive thing.

**Why it is not the scoring engine.** The question came up whether scoring
itself should move to the model. No: scores are arithmetic over facts and
two sliders, recomputed per `profile_hash` when a slider moves (entry 1),
and a model verdict per slider position is neither recomputable nor
explainable. The model decides *classification* questions; the numbers stay
deterministic. Invariant 6 is unchanged.

**Why the rule is thresholded and lives in one place.** A verdict is
evidence, and evidence is read through a rule the user can see and tune
(`triage_reject_min_probability` is a gate, so it is part of
`profile_hash`; set it to 1.0 and the reject is off, the flag stays). The
rule for a flat verdict has the same escape hatch as the keyword gate:
deterministic farm substance (outbuildings the normaliser found) turns a
reject into a flag, because "Wohnung im Austragshaus" of a farm sold whole is
still the farm. The rental verdict has no escape hatch, per entry 22. Putting
`decide` in `triage.rules` with no network import lets the scoring engine
call it without pulling in the client.

**Why our own POST and not `typesafe-sdk`.** The SDK is built on `httpx2`,
which `respx` cannot mock, and this suite's rule is that every outbound call
is mocked with `respx` and asserted on the request that would have been made.
The documented call is one endpoint, one bearer header and one JSON body;
owning it keeps the question texts - the part that actually decides what is
rejected - in `triage/jev.py` under version control. `TYPESAFE_BASE_URL`
and `HOFRADAR_JEV_MODEL` (default `jev-latest`) are honoured.

**What it may not do.** It never verifies availability (invariant 4: its
silence proves nothing and it is not a source), never writes a number, and a
failed call is counted and the listing proceeds unasked - a triage outage
must not become an empty radar.

**One entry point, and the paste box is one of them.** `triage.annotate` is
the only way a listing gets its verdict: classify, write the evidence, append
the warnings, return the decision. The crawl loop calls it and drops an
unknown row on a rejection; the paste box calls it and drops nothing - a
human chose to paste it - so the verdict shows on the confirmation page and
the scoring gate retires the row. A configured-but-failing triage is said on
that page (`TRIAGE_FAILED_NOTICE`); an unconfigured one is silent there,
exactly as in the crawl, because the run log already carries that fact.

**The threshold is measured, not believed.** `scripts/backtest_triage.py`
asks the model about every property already judged by a human and prints,
per verdict group and per threshold, how many would be rejected or flagged.
The groups are deliberately not summed into one accuracy number: "archived"
covers too-far and too-dear as well as flat-and-rented, so the table has to
be read, not scored. The Merkliste column is the one that must show zero.
With `--store` the same run backfills `evidence["triage"]` on rows that have
none - the pasted and CSV rows the crawl never re-asks about - which is the
one write the script makes, on the precedent of the LLM review writing its
summary; it creates nothing (invariant 1).

---

## 24. A PDF exposé is the listing's own words, read behind the link and accepted at the door

**Decision.** A PDF is lifted the same way an HTML page is: text out,
labelled lines picked up, typed parsing left to `hofradar.normalize`. The
lift lives once, in `hofradar.sources.adapters._pdfutil`, and is used in
three places. `DenkmalboerseAdapter.fetch_detail` follows the "zum Exposé"
link on every detail page, downloads the PDF through the polite client and
merges it into the listing - the page's own Kurzinfo keeps precedence, the
PDF fills the holes and its full text is appended to the description.
`/add` accepts an uploaded PDF next to the URL and the text box, and a pasted
URL that answers with a PDF is read as one. Every document a listing's facts
were read from rides along as a `DocumentRef` (`RawListing.documents` ->
`NormalizedListing.documents`) and `lifecycle.ingest` remembers it as a
`Document` row, one per (property, url), so the dossier's "Dokumente" list
links to the exposé and an uploaded file is kept under
`$HOFRADAR_DATA_DIR/uploads/` by content hash. `pypdf` is a core dependency
now; the `[pdf]` extra remains as an empty alias so existing install lines
keep working.

**Why.** On a live sample of 30 in-scope Denkmalbörse objects (2026-09-20),
29 linked an exposé PDF. The HTML alone left the room count empty on all 30,
the usable area on 24 and the living area on 9 - the "k. A." cells the reader
sees on the dossier - while the same facts sat one click away in a document
the adapter never opened. The prose is the bigger loss: `Gewölbekeller`,
`stark sanierungsbedürftig`, `Scheune`, `Alleinlage` are what
`extract_features` and the cost model key off, and they live in the exposé,
not in the Kurzinfo box. And the reader's own case is the same shape: a
broker sends an exposé as a PDF, and until now the only way in was to copy
its text into the box by hand.

**Why the label reader changed with it.** Exposés set facts in layouts an
HTML detail page does not: two on one line
(`Wohnfläche: ca. 1.050 m²          Grundstücksfläche: ca. 7.112 m²`, BLfD's
own template), a label above its value (`Wohnfläche` / `~118 m²`, every
broker's "Eckdaten" table), and a bare count (`28 Zimmer`).
`extract_labeled_fields` now reads all three, under guards that keep it a
string matcher and not a guesser: a run of two spaces or a tab separates
facts on a line, a single space never does; a label-above-value pair is only
taken for the numeric fields and only when the value line is short and
carries a digit or a price marker - never for `Lage`/`Ort`, whose next line
is prose on every exposé and would block the address recovery of entry 18;
a bare room count is only read off a line short enough to be a fact-box
entry, so "die 3-Zimmer-Wohnung im DG" never becomes the house. The same
work found that a recovered town could span a line ("Vogtareuth\nKaufpreis"
was one town); it cannot any more.

**What is refused loudly.** A scanned PDF with no text layer yields nothing,
and that is a `warnings` line on the listing (shown on `/add` and stored on
the observation), not an empty description that looks like a thin advert. A
PDF over `PDF_MAX_BYTES` (40 MB) is not read at all. A failed exposé fetch on
the Denkmalbörse - HTTP error, not a PDF, unreadable - is a warning on the
listing and never a reason to `mark_enumeration_incomplete`: the listing
exists, only the enrichment failed, and invariant 4b is about absence, not
about thin facts. The adapter option `expose_pdf: false` switches the
2-14 MB-per-object download off for an operator on a metered line, and says
so in `config/sources.yaml`.

**What it may not do.** The lift parses nothing - "ca. 1.050 m²" is still
`hofradar.normalize`'s to type, and a PDF's page kind is `listing` because a
reader or a detail page handed it over as one advert. It is not OCR: a scan
is reported, not guessed at.


## 25. An identity is not an address, and a mark is not a score

**The rule.** A template may only put a URL in an `href` when a browser can
follow it: `http://` or `https://` and nothing else, which is what
`web/query.is_web_url` answers and the Jinja test `{% if url is web_url %}`
enforces at the point of use. Everything else the system uses to *name* a
listing - `upload:<digest>` for a PDF the reader handed over, `manual:<iso>`
for a text paste with no page of its own - is printed as what it is, never
offered as somewhere to go. The stored exposé is reachable instead, through
`GET /document/{id}`, which serves files from `web/uploads.uploads_dir()` and
refuses a `local_path` that resolves anywhere else.

And: the Merkliste's candidate set comes from the `properties` table, not from
the ranking. A marked property with no `Score` row for the live `profile_hash`
is still the reader's, and is rendered unscored.

**What went wrong.** Both halves were the same failure in two places, and both
were reported from use in one sentence each: "the uploaded PDFs show nothing
when clicking *Inserat öffnen*", and "*Merkliste* is not working any more".

The first: `/add` writes the PDF to disk *before* anything parses it, because
the file is the evidence (entry 24), and names the listing by the file's own
digest so a re-upload lands on the same property (entry 16). That digest then
went straight into the dossier's `href` - and into the fact table's *Quelle*
link, the *Quellen* list and the *Dokument* link. A browser has no `upload:`
scheme, so all four did nothing at all when clicked, without an error, a
console message or a cursor that changed. Meanwhile the file itself sat on
disk with **no route in the application serving it**: the one artefact that
*was* the listing was the one thing unreachable.

The second: `scoring.ranked_properties` joins `Score` on the live
`profile_hash`, so a property nothing has scored under that hash is not in the
ranking at all. A hand-added property is exactly that - `/add` stores, it does
not score, and the first score is written by the next page load's rescore. Any
rescore that cannot write leaves it unscored for longer: a crawl holding
SQLite's write lock is the everyday case, and the fix that stopped the radar
500ing on that (rolling back and rendering the last stored scores) turned a
visible failure into a silent one for rows that had no stored scores yet. The
marked property then vanished from the Merkliste under
*"Alle gemerkten Objekte sind archiviert."* - a sentence stating a cause the
page had never checked. A row merged into another was lost the same way:
`/merken` happily wrote `shortlisted_at` on a row `build_results` skips.

**Why the fix is shaped this way.** Neither half is a rendering detail.

`best_url` keeps returning the identity, because that is what the JSON payload,
the CSV export and the digest quote, and truncating it there would lose a fact.
The decision about what is *clickable* belongs at the one place that builds a
link - `open_link`, which prefers the listing's own page, falls back to the
exposé we hold, and returns `None` when there is neither. `None` is then a
sentence on the page (`NO_LINK_MANUAL`, `NO_LINK_NONE`), never a missing
button: "there is no online listing, only the exposé" is information the reader
needs, and entry 18's rule about dropped facts applies to links as well.
`document_href` prefers our own stored copy over the remote URL, because the
copy still opens after the broker takes the exposé down - which is the whole
reason the file is written at all.

For the Merkliste: `include_rejected` was already forced on and the slider gate
already skipped (entry 21), on the reasoning that a mark is the human's and
outranks the machine. A *missing score row* is not even a machine verdict - it
is bookkeeping - so letting it hide a mark was strictly worse than the gate
this entry's predecessor had already ruled out. `_marked_pairs` loads the
marked, unmerged set directly and merges it into whatever the ranking returned;
`_sort_key` already handles a `None` score and the card already says
"noch nicht bewertet". `/merken` follows `merged_into_id` the way `ingest`
does, so a click always lands on a row that can be shown, and `total_in_db`
stops counting merged rows so the page's own numbers agree with it.

**What it may not do.** Serving a stored file is not serving a path: anything
outside the uploads directory is refused, and a `local_path` whose file is gone
is a 410 naming the path, not a bare 404. The Merkliste still honours
archiving, and still counts what it hides. And the empty page no longer names
archiving as the cause unless the archived count actually accounts for every
mark - the failure here was a true-sounding sentence, so a sentence that can
only be true is part of the fix.

## 26. A label is paired with a value by layout only when the value has that field's shape

**Decision.** `extract_labeled_fields` reads a fact wherever the page puts
its value, not only after a colon on the same line: a label on its own line
(with or without a trailing colon) takes the next *non-empty* line, up to
four blank lines down; a colon-less label and value in one table cell or row
("Wohnfläche ca. 180 m²", "Kaufpreis<TAB>450.000 €", pypdf's rendering of a
two-column fact table) are read as a pair; qualified labels ("Wohnfläche
ca.", "Anzahl Zimmer", "Grundstücksfläche (m²)") resolve to their base field;
a soft hyphen inside a label ("Kauf&shy;preis") is ignored; and
`m<sup>2</sup>` keeps its unit. Every pairing inferred from layout rather
than stated by a colon must pass a per-field *shape* check first: a price has
a currency, four digits or a price marker; a room count is one or two digits;
a year is four; an area starts with a number. `hofradar.normalize` reads
space-grouped numbers ("450 000 €", U+00A0, U+202F) and skips a percentage
ahead of a price. A listing page with no labelled price, living area or plot
area gets one `Eckdaten:` warning naming what is missing, and a price stated
as "auf Anfrage" no longer produces a false "could not parse" warning.

**Why.** Issue #27: the radar and the dossier said "k. A." for price and
areas that the exposé or web page stated plainly. The biggest cause was one
enabled source: every OVBimmo property had `price`, `living_sqm`, `land_sqm`,
`rooms` and `year_built` all NULL. The OVB detail page's "Objektdaten" table is
`<div class="col-label">Wohnfläche</div><div class="col-value">165
m<sup>2</sup></div>`, which flattens to "Wohnfläche", a blank line, "165 m",
"2"; the reader looked only at the *immediately* next line, and the adapter's
own documentation had misfiled those divs as sidebar widgets, so the gap was
recorded as a limitation and a test asserted it. Verified live on
2026-09-23: before, a current OVB detail page yielded no fact at all; after,
it yields price 559.000 €, 140 m², 1.147 m², 5 rooms, 1995. The same shape -
a label, markup, the value - is what `<strong>Kaufpreis:</strong> 450.000 €`,
a `<dl>` and a browser's copy of a fact table look like once they are text,
so it also hit pasted listings and broker exposés on `/add`. Space-grouped
numbers were worse than missing: "450 000 €" was a EUR 450 farm and
"1 050 m²" a 1 m² house.

**Why the shape check.** Without a colon the pairing is a guess from layout,
and layouts put the wrong things next to each other. OVB's headline block
sets the value *above* its label - "690.000,00 €", "Kaufpreis", "7",
"Zimmer", "165", "m²" - so a label-then-next-line reader pairs "Kaufpreis"
with the room count and "Zimmer" with the living area. The shape check
refuses both, and because the first value per field wins only once it has
passed, the Objektdaten table further down supplies the real figures. The
check is still shape, not parsing: the raw string goes to
`hofradar.normalize` unchanged. A combined "Wohn-/Nutzfläche" is read as a
usable area, not a living one: taking it as living area would inflate every
per-m² figure the cost model derives from it.

**What it may not do.** It is still a string matcher. Location labels still
never take a next-line value (entry 18's reason stands); the Denkmalbörse
Kurzinfo and exposé values it read before are unchanged, re-checked on eight
live objects. The dataLayer's cent-denominated figures on OVB stay unread
(converting is the normaliser's job, not an adapter's). A fact that genuinely
is not stated stays `None`, and now says so on `/add` and in the
observation's stored warnings instead of reaching the card silently.

**Existing rows.** Crawled sources repair themselves on their next run:
`ingest` fills a NULL fact from any source, and the report does not count a
first-known price (`old_price` NULL) as a price change. Hand-added listings
are not re-crawled, so `scripts/repair_pastes.py` re-reads their stored text
(an uploaded PDF's text included) and now also corrects a stored value the
re-parse disagrees with, and keeps an upload's cover-page title.

## 27. A document's identity, not its stored path, is what survives a move to another machine

**The rule.** `Document.local_path` is written as an absolute path on
whichever machine ran `/add`, and nothing rewrites it when the database
travels - so `hofradar.web.uploads.resolve_upload_path` never trusts it alone.
It tries the stored path first, and falls back to this machine's own
`uploads_dir()/<digest>.pdf` for an `upload:<digest>` document, because the
digest is the file's identity and identities do not change when the database
does. `GET /document/{id}`, `document_href`, `document_missing` and `hofradar
documents --check` all go through this one function rather than each growing
their own idea of where the file might be.

**What went wrong.** GitHub issue #26: PDFs stopped opening after a dev
laptop's database moved to the Pi. The mechanism was exactly what entry 25
already guards against, one level up - `stored_upload_path` correctly refuses
a `local_path` that does not resolve under this machine's `uploads_dir()`, but
it had no way to distinguish "this is an attack" from "this is a legitimate
file that just has not been copied here yet, or was copied under a path this
machine never had." Both looked identical: a plain string column pointing
outside the directory. `deploy/raspberrypi/README.md`'s own migration guide
made this worse, not just silent - *"Bringing an existing database with
you"* listed exactly three files and said only the database travels, so
following the documented steps to the letter left `uploads/` behind on the
laptop. The result read as success: the property, its facts, and the
`Document` row were all there; only the PDF was reachable nowhere, and the
only visible sign was a 410 a reader found by clicking.

**Why the fix is shaped this way.** The digest already is the file's name
(entry 16, decision 25) and already lives in `document_url`, which a database
move never touches - only `local_path`, a plain column naming a path on a
machine that may no longer be this one, can go stale. Reconstructing the
filename from the digest is therefore not a weaker check than
`stored_upload_path`'s directory guard, it is the same guard applied to a
name this function derives itself rather than reads from the row: the
candidate path is always `uploads_dir() / f"{digest}.pdf"`, built from a
digest matched against its own fixed shape before it ever touches the
filesystem, so a document's `local_path` can be anything at all - attacker,
stale, or merely missing - without it ever producing a path outside the
directory. `document_missing` and the dossier's own "Datei fehlt auf diesem
Server" notice exist because `document_href` returning `/document/{id}` for a
file that will 410 on click is entry 18's rule about dropped facts, applied to
links: a clickable-looking button that fails silently on click is the same
shape of lie as a missing warning.

**What it may not do.** The fallback only ever answers for an
`upload:<digest>` document; a remote exposé or a text paste never had a file
of its own to recover, and `document_missing` says `False` for either rather
than inventing one. `deploy/raspberrypi/README.md` now lists `uploads/`
alongside the database as something that travels, with the `scp`/`rsync`
commands and the ownership step, and `hofradar documents --check` is the
verification step before anyone calls the migration done.


## 28. Text is read as it was written: ligatures spelled out, a place kept apart from prose about it

**Decision.** Three string-level repairs, each where the text is first lifted.
`extract_pdf_text` spells out f-ligatures before anything reads a page:
Unicode's presentation forms (U+FB00-U+FB06, "Wohnﬂäche") silently, and a
glyph the font's `ToUnicode` map leaves out by inference, with a warning
naming each reading. The manual adapter's plain-text path does the same
through `recover_ligatures`, because plain text is often text that left a PDF.
`extract_labeled_fields` takes a `location_raw` value only when it reads like a
place (`reads_like_a_place`: capitalised words, numbers and a few joining words
up to the first comma). And `pdf_title` skips the line under a bare contact
label ("Ihr Gesprächspartner:") and any e-mail or web address.
`scripts/repair_pastes.py` re-reads stored uploads through all three, replaces
a stored town only when it is a sentence, and re-titles an upload with
`pdf_title` over its stored text instead of freezing the old title.

**Why.** Two exposés a reader uploaded stated their facts plainly and still
reached the radar half empty. The first is set in a subset Barlow whose
`ToUnicode` map has no entry for the fi, fl and ffi glyphs, so pypdf emits the
glyph number as a character: "WohnŦäche" (0x166), "beťndet" (0x165),
"EnergieeŨzienzklasse" (0x168). The embedded font has no glyph names or cmap
either (`glyph00358`), so nothing in the file says which ligature a glyph is.
The label reader never matched "WohnŦäche", and the living area was "k. A.".
The second has a "Lage:" paragraph ("Sauerlach zählt zu den beliebtesten
Wohnorten im südlichen ..."). Being on the label's own line, it passed the
next-line exclusion of entry 26 and became the town, which geocodes nowhere.
Because it was set, the normaliser never searched the text for the "82054
Sauerlach" printed on the cover (entry 18's recovery runs only when nothing
was labelled). Its title was the broker's name, the first line on the cover
with enough letters.

**How a missing glyph is read.** Every word holding a letter above Latin-1 is
a candidate (German and its typography are Latin-1, or are not letters). Each
f-ligature is tried in its place, and the one that turns the most of that
character's words into a known exposé stem wins ("fläch", "pflicht", "find",
"effizien", ...). The decision is made once per document, because a glyph is
the same ligature on every page and the cover alone may give nothing away. A
stem only counts when it spans the whole replaced ligature, or "beffindet"
would score for ffi on the strength of "find". With no hit, or a tie, the
character is left alone: "Łukasz Dvořák" stays as it is. Correct text is
inferred text all the same, so it is said in the listing's warnings.

**What it may not do.** It is still a string matcher. A glyph number that lands
inside Latin-1 (below 0x100) looks like an ordinary letter and cannot be told
apart. The stem list is short and German, so a document whose only ligatures
sit in words it does not know keeps its stray characters, and the `Eckdaten:`
warning names the fact that went missing. A place check is shape, not a
gazetteer: "Lage: Zentral" still passes. A euro sign the PDF itself maps to
"e" (a TeX `feymr10` font's `ToUnicode` says so) is left to `parse_price`,
which already reads "570.000,00 e".
