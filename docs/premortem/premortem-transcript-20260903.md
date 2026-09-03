# Premortem — Hofradar source expansion (Denkmalbörse + local press)

Date: 2026-09-03
Method: Klein premortem. Frame: it is six months from now, the plan has failed,
Dan checks two websites by hand on Saturdays instead. Nine failure modes were
generated, then one investigator was sent into each independently, in parallel.

## Context

**What.** A six-workstream source expansion for Hofradar:
1. BLfD Denkmalbörse adapter (`role=primary`, static detail pages).
2. A derived `market_tempo` class (SLOW / NORMAL / FAST) selecting freshness
   band tables and STALE thresholds.
3. A Denkmal branch in the cost model (rate multiplier + approval additive;
   tax relief only as a non-ranking eligible-cost basis).
4. Harvest of the ovbimmo.de `/anbieter` broker directory into the existing,
   empty `generic_rss` / `generic_sitemap` config.
5. An ovbimmo.de local-press adapter (`role=local`) or an email-ingest adapter
   fed by OVB's official Suchabo alert emails.
6. Authoritative `is_monument` enrichment from the Bayerischer Denkmal-Atlas.

**Who.** One private buyer looking for a Hofstelle/Sacherl near Westham,
Feldkirchen-Westerham.

**Success.** Dan gets to viewings on real, in-radius, buyable Hofstellen he
would not otherwise have found. Not elegant code.

**Known-unverified at time of writing.** robots.txt and AGB for both
blfd.bayern.de and ovbimmo.de — the reconnaissance container's egress proxy
blocks both hosts.

---

## Failure mode 1 — Yield/effort mismatch

**Story.** The Denkmalbörse adapter shipped clean in three weeks: static detail
pages, `role=primary`, tests green. It found four objects Bavaria-wide — two in
Franken, one in Schwaben, one in Landshut. Zero inside the radius. Nobody
flagged it, because the adapter was "working": the suite proved parsing, not
yield.

`market_tempo` and the Denkmal cost branch then consumed five weeks of scoring
machinery for a corpus of four unrankable properties. The Denkmal-Atlas
enrichment was the worst trade — ~200 Gemeinde PDFs, three weeks, to set a
boolean that was already obvious from the listing text.

The ovbimmo harvest broke faith. `/anbieter` yielded ~60 brokers; their feeds
produced ~380 listings a month; after radius and Hofstelle filters, six. Five of
the six carried an IS24 or Immowelt object ID in the exposé — the same listings
the disabled adapters would have shown, arriving late via a broker's own site.
The shortlist settled at three, all of which Dan already recognised from
Saturday browsing.

**Underlying assumption.** That the bottleneck was source coverage rather than
the number of Hofstellen that actually change hands inside the radius per month
— a number small enough that no amount of source engineering beats a human
looking twice a week.

**Early warning signs.**
- In-radius yield per adapter, measured *before* building the next one. Fewer
  than ~5 in-radius objects on the Denkmalbörse's first run means workstreams
  2, 3 and 6 have no corpus to justify them.
- Novelty rate: fraction of shortlisted properties Dan had not already seen.
  Under ~50% for two consecutive months means the pipeline duplicates Saturday
  rather than extending it.

---

## Failure mode 2 — Silent fixture rot

**Story.** The BLfD adapter shipped in March against fixtures captured on a
Tuesday afternoon. In May the Denkmalbörse changed its detail-page layout and
the selectors stopped matching. `discover()` returned `[]` without raising — an
empty result set is a legal result set — and all 565 tests stayed green, because
they were green against March's HTML. The frozen fixture had become the only
page the adapter was ever asked to parse.

Nothing alerted, because the pipeline had no concept of an implausible zero. The
Denkmalbörse legitimately posts two or three Hofstellen a month in all of
Oberbayern; a zero-yield run is the normal case. The digest kept arriving,
correctly formatted, honestly empty — and "max 10 entries, everything else
counted" means a digest of zero and a digest of ten look like the same healthy
artifact.

The compounding damage came from OVB: an empty seen-set flowed into
`lifecycle.mark_missing`, which did exactly what it was written to do and
transitioned every unseen listing to REMOVED. Eleven live Hofstellen were marked
gone in a single run, with clean Observation rows recording the disappearance.
The database's memory — the actual product — had faithfully remembered a fiction.

**Underlying assumption.** That a passing test suite says something about the
live site, when frozen fixtures only ever prove the adapter still parses the past.

**Early warning signs.**
- Consecutive-zero-run counter per source. Four straight zero runs is a fact
  worth alerting on, independent of whether anything was found.
- REMOVED-per-run ratio. Any run where a verifying source marks more than ~30%
  of its live inventory missing is a parser failure until proven otherwise —
  and `mark_missing` should refuse an empty seen-set outright.

---

## Failure mode 3 — The 14-day ad expiry poisons listing_status

**Story.** The OVB adapter shipped with `role=local`, and nobody asked what
"local" licensed it to do. Role gates authority: local sources may verify, set
`last_verified`, and — the fatal clause — have their *silence* mark a listing
REMOVED. On day 15, `mark_missing` marked eleven listings REMOVED in one pass.
Every one was still for sale; the ad package had simply run its two-week course.
The system was structurally incapable of telling "the seller stopped paying"
from "the farmstead sold", because the adapter only reported presence, and
absence was interpreted by role rather than by evidence.

The damage compounded as designed. `REJECT_LISTING_GONE` pulled all eleven out
of the ranking and `confidence_score`'s availability term zeroed them. A
Hofstelle in Vagen at 6 km — one of maybe four genuine in-radius candidates all
quarter — vanished from the ranked view while its sign still stood at the gate.

Then the renewals started. A seller who bought a second fortnight reappeared on
day 17; ingest correctly refused FIRST_SEEN (invariant 2 held) and wrote
REACTIVATED. The invariant was honoured and the outcome was still garbage: by
month two the change feed was a metronome, REMOVED and REACTIVATED on a 14-day
beat for the same dozen properties. The digest caps at 10 and sorts by recency
of change, so every Sunday it was ten rows of OVB churn with "and 3 more" hiding
the actual discoveries beneath.

**Underlying assumption.** That a source's *role* determines whether its silence
is evidence, when what actually determines it is the source's **retention
policy** — whether listings leave because the property left the market or
because a clock ran out.

**Early warning signs.**
- Bimodal removal age. Histogram `last_observed − first_observed` for every OVB
  property marked REMOVED. A hard spike at 13–15 days is a billing cycle, not a
  market; real removals scatter across weeks and months.
- REACTIVATED-to-FIRST_SEEN ratio per source per week. Any single property
  accumulating more than one REMOVED→REACTIVATED transition means the change
  feed is reporting ad terms, not the market. Alert on the second cycle, not
  the tenth.

---

## Failure mode 4 — The schema change destroys the memory

**Story.** Week three. The Denkmal branch needed `is_monument_provenance` on
`Property`, `market_tempo` cached alongside `renovation_tier`, and two new cost
columns. `create_all()` is a no-op on existing tables, so the new fields simply
weren't there and every query raised `no such column`. The fix was five minutes
of hand-written ALTER TABLE, or fifteen seconds of `rm /data/hofradar.db &&
hofradar init-db`. Mid-build, with ingest tests red, the second option won. The
database was four months old and *felt* like scratch data.

It wasn't. That file was the only copy of the crawl. The next run wrote 340
Observations and `lifecycle.ingest` did exactly what invariant 2 requires: no
prior Property row existed, so every one became FIRST_SEEN. Not a bug — the
correct behaviour on an empty database. The digest went out with ten entries,
all "neu entdeckt", including the Hofstelle in Bruckmühl that had been dropping
from 690k to 595k since spring. Its `first_seen` now read that Tuesday.

Nothing errored. `hidden_score` still computed; `long_online` and the stale
penalty just read a history of zero days and returned near-nothing for
everything, so the ranking flattened. Price history rendered as a single point.
The system could tell Dan 595k — which the website also told him — and nothing
else.

**Underlying assumption.** That the SQLite file at `/data` was a rebuildable
artifact of the code rather than the irreplaceable, un-refetchable product itself.

**Early warning signs.**
- Any run where FIRST_SEEN exceeds ~20% of processed listings, or where the
  count of distinct `first_seen` dates across all properties drops.
- Total Observation row count failing to increase monotonically between runs.
  Check before and after every deploy; refuse to start if it fell.

---

## Failure mode 5 — The monument regex becomes a staleness-laundering exploit

**Story.** The tempo slice shipped clean and needed no migration, because
`market_tempo` was derived rather than stored. Every fixture exercising SLOW was
hand-written with a genuine BLfD entry. Nobody wrote a fixture for a listing
whose exposé said "Denkmalschutz nicht vorhanden" — the case
`_MONUMENT_RE = re.compile(r"denkmal")` gets exactly backwards. The Denkmal-Atlas
provenance slice went into the backlog behind the cost model and stayed there.

The exploit ran through two multipliers at once. A stretched band table meant a
400-day-old listing scored freshness like a 60-day-old one, and the later STALE
threshold withheld the −10 that also feeds `hidden` — so laundered listings
gained twice: once on freshness, once on the component specifically meant to
reward overlooked objects. And the listings that mention Denkmal in passing are
disproportionately the ones that have been sitting: broker copy accretes
adjectives on relists, and "in Denkmalnähe" is what you write when the building
itself has nothing to sell. The real BLfD entries were a handful; the marketing
false positives were dozens.

Within three weekly cycles the top of the digest was structurally biased toward
the oldest text in the corpus. Dan saw entry #2 — a Hof he had dismissed in
April, unchanged, still 40k over — sitting above a genuinely new Kaufangebot he
had never seen. The digest caps at 10, so the new listing wasn't merely lower;
it was invisible.

**Underlying assumption.** That `is_monument` was accurate enough for a boolean
display flag, therefore accurate enough to be a scoring multiplier — ignoring
that a flag's tolerable error rate scales with what depends on it.

**Early warning signs.**
- Ratio check: `count(is_monument=True)` versus `count(source='denkmalboerse')`.
  If the flag fires on more properties than the authoritative source has
  entries, the regex is manufacturing monuments. Log it per run.
- Digest age distribution: median `days_since_first_seen` of the top 10 rising
  week over week while the corpus median stays flat.

---

## Failure mode 6 — The scoring system crosses the threshold of explicability

**Story.** Week three, a Tuesday evening. Dan opened the digest and saw a Denkmal
farmstead near Bruckmühl ranked second, above a cheaper, closer, structurally
sounder property he had already half-decided on. He clicked the breakdown. It was
honest, exactly as designed: thirteen hidden signals as separate line items, a
negative stale penalty not netted off, a capital_risk widened asymmetrically by
the Denkmal approval additive, and a freshness band he could not reconcile
because `market_tempo` had swapped both the band table *and* STALE_ONLINE_DAYS
underneath it. Every number was labelled. None of it added up to an answer. He
read `costmodel/` for twenty minutes to learn that the rate multiplier had pushed
total cost toward — but not over — the hard max, so no gate fired and no name
appeared to explain the near-miss.

The subtler failure: he changed a slider, the profile_hash changed, and the
ranking reordered in ways he could no longer predict *directionally*. He had lost
the ability to say "raising the distance weight should push this one down." The
system's explicability had always rested on naming rejections; it never needed to
explain *ordering* among survivors, because with five components and twelve
signals the ordering was still intuitable. The expansion pushed it past that
point — and the honest-breakdown value made it worse: more true line items, less
understanding.

**Underlying assumption.** That a rejection being *named* is the same thing as a
ranking being *predictable* — that explaining exclusion also explains order.

**Early warning signs.**
- Dan opens the score breakdown and then opens source code in the same session.
- Ranking churn on a slider nudge: a small weight change reorders the top ten by
  more than two positions. If he cannot call the direction beforehand, trust is
  already gone.

---

## Failure mode 7 — The deferred ToS check kills OVB after the model was reshaped around it

**Story.** Week 2: the OVB adapter got written first because it was the
interesting part. Reconnaissance had been done from a container whose proxy
blocks ovbimmo.de, so fixtures were hand-built from a memory of the site's shape.
Nobody flagged this as a blocker, because the source was classed `local` — and
`local` had been defined in *opposition to* the disabled portals rather than by
any positive evidence about OVB itself. The classification was an argument, not a
finding, but it entered the source registry as a fact.

Week 5: the tempo axis shipped with three classes. The FAST pole existed because
OVB's ads reportedly expire in 14 days — that single number justified a new
lifecycle path for ad expiry plus fixture tests asserting it. `ingest` grew a
branch that only OVB fixtures ever exercised. The `/anbieter` harvest was going
to seed the RSS/sitemap config, so that config stayed empty in the meantime,
waiting.

Week 11: Dan opened ovbimmo.de on his own laptop. The AGB restricted automated
retrieval in language close enough to ImmobilienScout24's that invariant 7
admitted no argument. The adapter was disabled the same day. What remained: a
tempo axis whose FAST pole was unreachable, so it stopped discriminating; a
lifecycle expiry path with no producer; an empty feed config with nothing to seed
it; and no evidence at all for the local-press thesis, because OVB *was* the
test. Dan's reaction was not about the wasted weeks. It was that this had
happened before, in the same shape, with three portals — and the project had
written the lesson down as an invariant instead of as a step.

**Underlying assumption.** That a source's legal status is a compliance detail to
confirm later, rather than the input that determines whether the abstractions
built on it are worth designing.

**Early warning signs.**
- A `local`-classed source in the registry with no stored robots.txt fetch
  timestamp or AGB excerpt. Greppable today.
- A tempo class with zero producing adapters, or a lifecycle branch whose only
  coverage is one source's fixtures. If FAST has one producer, the axis is one
  AGB away from being two-valued.

---

## Failure mode 8 — The coverage blind spot swallows the one real find

**Story.** The blind spot was baked in during week one, for a defensible-sounding
reason: OVB had a clean portal, a single publisher, four titles, and a verified
footprint — Rosenheim, Mühldorf, western Traunstein. It could be built in an
afternoon. Ippen/Merkur was a second integration against a different site, so it
went into "later" with no date, no ticket, no owner.

That decision was never revisited, because nothing in the system surfaced it: the
config carries a source *registry*, not a coverage *map*, so no screen anywhere
showed that the circle drawn around Westham spills straight over the Miesbach
line that runs past the village. The radius was expressed in kilometres; the
coverage was expressed in publishers; nobody laid one over the other.

The second failure compounded it. `pdf_bulletin` shipped complete, `role=local`,
with `options.bulletins: []` — the adapter that would have caught the Weyarn
Gemeindeblatt was installed, working, and pointed at nothing. Filling that list
is twenty minutes of finding PDF URLs for Weyarn, Valley, Holzkirchen,
Bruckmühl. It stayed empty for six months because an empty list produces no
errors, no failed runs, no red CI — just a source that dutifully reports zero
forever.

The digest kept arriving, capped at ten, looking healthy. It *was* healthy — for
Rosenheim and Mühldorf. Dan read a stream of Wasserburg and Bad Aibling listings
and slowly recalibrated his sense of the market, never noticing that Holzkirchen
and Weyarn had gone quiet in his feed while staying loud in his neighbours'
conversations. The Sacherl in Weyarn ran in the Miesbacher Merkur and the
Gemeindeblatt, sold in four weeks, and the database has no row for it. Not a
missed match. A blank.

**Underlying assumption.** That source coverage and search radius are independent
settings, so sources could be sequenced by integration cost rather than by which
parts of the radius were still dark.

**Early warning signs.**
- Per-Gemeinde observation counts, grouped and compared against the
  municipalities inside the current radius. Weyarn, Valley, Holzkirchen,
  Otterfing at zero for four consecutive weeks is not a quiet market — it is an
  uncovered one. Queryable today.
- Any listing Dan learns about from a human that is inside the radius and absent
  from the database. One is a data point; two is a proven structural gap, and
  the fix is scheduling Ippen and populating `options.bulletins`, not tuning
  scores.

---

## Failure mode 9 — Cost pessimism compounds into a hard gate and hides the target class

**Story.** Week 3: the shortlist came back with four entries, all 1980s bungalows
with big gardens. Not one Hofstelle. Dan assumed the market was thin. It wasn't
— the pipeline had ingested eleven genuine farmsteads that month and gated every
one.

The mechanism was arithmetic, not judgement. Each listing hit the age rule —
pre-1960, condition unstated (as it always is; German listings say
"sanierungsbedürftig" and stop) — so tier inference took the pessimistic branch
and assigned HEAVY. The Denkmal branch then multiplied the €/m² band and added
the approval additive on top. Because Hofstellen are *large*, the multiplier
applied to 400+ m², not 120. `total_mid` landed above
`effective_total_hard_max` on properties whose asking price was well inside
budget — a rejection manufactured entirely by a modelled renovation figure with
no observed evidence behind it. The asymmetric band-widening ("the variance IS
the risk") then pushed `high` up, lifting capital_risk toward EXTREME, so even
the near-misses that survived the gate ranked below the bungalows.

The second failure was interface, not model. `REJECT_TOTAL_COST` removed the
property from the shortlist entirely rather than ranking it last, and the reason
lived in a breakdown panel Dan opened maybe twice. A digest reporting "10 shown,
47 counted" reads as a healthy funnel, not as a system that has quietly excluded
its own target class. He found the farmstead he eventually bid on through a
broker's newsletter — it was in the database, REACTIVATED twice, rejected every
time.

**Underlying assumption.** That pessimism is safe because it only lowers scores
— ignoring that the same pessimism was wired into a hard gate, where "cautious"
means "invisible".

**Early warning signs.**
- Rejection-reason composition by property class. If >30% of Denkmal/pre-1960
  listings exit via REJECT_TOTAL_COST while <5% of post-1980 listings do, the
  gate is discriminating on the target class, not on cost.
- Modelled-vs-asking spread: how often `total_mid − asking_price` exceeds the
  asking price itself. When the renovation estimate dominates purchase price on
  most rejections, the gate is firing on an inference, not a fact.

---

# SYNTHESIS

## 1. The most likely failure — #1, yield/effort mismatch

It is most likely because **nothing has to go wrong for it to happen.** Every
other failure mode requires a bug, a bad call, or an unlucky sequence. This one
is the default outcome when all six workstreams work exactly as designed: the
Denkmalbörse is Bavaria-wide and holds a handful of in-radius objects; ovbimmo
is largely brokered stock already visible on the portals. Three of the nine
investigators reached the same shortlist size without being told to.

## 2. The most dangerous failure — #4, the schema change destroys the memory

Every other failure on this list is recoverable. Re-tune the gate, fix the
regex, disable the source, widen the coverage. **Lost Observation history cannot
be refetched from anywhere.** Months of price drops, status transitions and
honest `first_seen` dates exist in exactly one SQLite file, and DECISIONS #2
names that history as the single feature that turns a scraper into a radar.
DECISIONS #10 (no Alembic) makes `rm` the path of least resistance during
exactly the mid-build moment when the pipeline is half-refactored. Failure #2
sharpens it further: fixture rot can write a *fiction* into that history, so the
memory can be corrupted as well as erased.

## 3. The hidden assumption

**Hofradar is being built as a ranking problem when its actual constraint is
coverage.**

The scoring system is already far more sophisticated than the corpus justifies:
five weighted components, twelve hidden signals, nine named gates, a confidence
model, per-profile score caching — over a database that, by the project's own
description, "finds almost nothing." The expansion then proposes a tempo axis, a
cost branch and a thirteenth signal: more *reasoning about* properties, almost
no new *properties*.

Meanwhile `gemeindeblatt_pdf` ships complete with `options.bulletins: []`, and
`generic_rss` / `generic_sitemap` ship enabled with `options.feeds: []` and
`options.sites: []`. Those three empty lists are the highest-yield, lowest-risk,
zero-code intervention available — and they stayed empty because filling them is
data entry, not engineering. Four of the nine investigators arrived at that
observation independently.

Several of the failure modes are worse than neutral on this axis: #3, #5 and #9
describe the reasoning machinery actively *hiding or destroying* intake that the
system already had.

## 4. The revised plan

1. **Config before code.** Before any new adapter: fill `options.bulletins`
   (Weyarn, Valley, Holzkirchen, Bruckmühl, Feldkirchen-Westerham, Bad Aibling,
   Irschenberg, Otterfing) and seed `options.feeds` / `options.sites` from the
   ovbimmo `/anbieter` directory. Two adapters already built and tested. Run
   four weeks. This is the coverage experiment that tests the entire thesis.
2. **Write the yield gate down now.** No `market_tempo`, no cost branch, no
   Atlas enrichment until a source has produced ≥5 in-radius objects. Decide the
   number before the data, not after.
3. **Terms before enablement.** Add `terms_checked_at` and `terms_excerpt` to
   `SourceConfig`; a source without both may not be `enabled: true`. This makes
   failure #7 structurally impossible rather than merely unlikely.
4. **Make `mark_missing` refuse weak evidence.** Three guards: refuse an empty
   seen-set outright; refuse any run marking >30% of a source's inventory
   missing; and add a source-level `listing_ttl_days` so disappearance-at-TTL
   produces an EXPIRED state that is *not* in `GONE_STATUSES`.
5. **Back up before any schema change, and assert on the way in.** A
   `scripts/backup_db.py`, plus a pre-run assertion that refuses to start if the
   Observation count fell or if FIRST_SEEN would exceed 20% of processed
   listings.
6. **Reorder: authoritative monument provenance BEFORE the tempo class.** Split
   `is_monument` into claimed vs confirmed; `market_tempo=SLOW` requires
   confirmed. This is a reordering of existing work, not extra work.
7. **Modelled cost may not drive a hard gate.** Where renovation is inferred
   rather than observed, `REJECT_TOTAL_COST` becomes a flag and a rank penalty,
   not an exclusion — or gate on `total_low` instead of `total_mid`.
8. **Build a coverage map, not just a source registry.** One query:
   municipalities inside the radius × observations in the last four weeks. The
   dark cells are the backlog, in priority order.

## 5. The pre-launch checklist

1. `curl` robots.txt and the AGB for blfd.bayern.de and ovbimmo.de; paste
   excerpts and a date into the source registry. *(prevents #7)*
2. Back up `/data/hofradar.db`; add the pre-run assertion that Observation count
   never decreases. *(prevents #4)*
3. Fill the three empty config lists; run four weeks; record in-radius yield per
   source before writing any new adapter. *(prevents #1, #8)*
4. Add the three `mark_missing` guards and `listing_ttl_days` before enabling any
   source whose listings expire on a billing cycle. *(prevents #2, #3)*
5. Write and actually look at the per-Gemeinde coverage query. *(prevents #8)*
