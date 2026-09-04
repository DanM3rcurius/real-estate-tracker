# Module API contract (binding for all packages)

Every package below MUST expose exactly these names from its `__init__.py`.
Other packages import ONLY through these. Nothing else is public.

Shared types live in `hofradar.contracts` (RawListing, NormalizedListing,
GeoResult, CostResult, ScoreResult, DuplicateVerdict, ChangeResult, Evidence).
`RawListing.page_kind` and `NormalizedListing.page_kind` are `PageKind`
(`PAGE_KIND_LISTING` / `PAGE_KIND_INDEX` / `PAGE_KIND_UTILITY`, all defined in
`hofradar.contracts`), defaulting to `"listing"` so a source that hands over
one advert it already knows to be one says nothing. Only a `listing` may
become a `Property`; see `docs/DECISIONS.md` entry 19.
`CostResult.renovation_evidence` is `"observed"` or `"inferred"` (see
`hofradar.costmodel.renovation_evidence`) - only an "observed" figure may
hard-reject a property on total cost; an "inferred" one only flags it.
Config types live in `hofradar.config` (SearchProfile, KeywordConfig, SourceConfig,
CoverageConfig). `SearchProfile.coverage.municipalities` is not a scoring slider - it
is excluded from `scoring_payload()` / `profile_hash` - but loads through the same
config machinery so the report can never drift from `config/search.yaml`.
`SourceConfig` carries `terms_checked_at: date | None` and `terms_excerpt: str | None` —
the record of somebody having actually read the source's robots.txt and terms.
A model validator rejects `enabled=True` unless both are set; see
`docs/DECISIONS.md` entry 14.
`SourceConfig` also carries `listing_ttl_days: int | None` (and the matching
`Source.listing_ttl_days` ORM column) — set for a source that sells a fixed
advertising window (a newspaper's fortnight), so `hofradar.lifecycle.mark_missing`
reads its silence past that window as `ListingStatus.EXPIRED`, not `REMOVED`;
see `docs/DECISIONS.md` entry 15.
ORM models live in `hofradar.db.models`. Enums in `hofradar.db.enums`, which also
carries `HIDDEN_USER_STATES: frozenset[str]` — the `Property.user_state` values
that take a property out of every reader-facing view (radar, exports, map,
digest, LLM feed) while it keeps being crawled, rescored and observed. It lives
there rather than in `hofradar.scoring` because the web layer applies it in its
own filter pass, which must keep working when scoring cannot be imported.
Deliberately not the triage verdict `"rejected"`, which is a judgement about the
farm and stays visible — see `docs/DECISIONS.md` entry 20.

```python
# hofradar.db.backup - the snapshot before anything destructive
def backup_database(url: str | None = None, *, into: Path | None = None) -> Path | None
    # The path written, or None when there was nothing to copy (in-memory
    # database, or no file yet). Raises BackupUnavailable for a database it
    # cannot snapshot (a Postgres DSN) rather than returning None: a caller
    # that reads "no backup" as "backed up" is the silent success this
    # codebase keeps producing. `scripts/backup_db.py` is a wrapper around it.

class BackupUnavailable(RuntimeError)
```

Schema handling is split deliberately (`docs/DECISIONS.md` entry 17):

```python
# hofradar.db.session - throwaway databases only; never alters an existing table
def init_db(engine: Engine | None = None) -> Engine: ...

# hofradar.db.migrate - what every process opening the persistent database calls
def ensure_schema(engine: Engine | None = None) -> SchemaState: ...   # raises SchemaError
def schema_drift(engine: Engine) -> list[str]                         # [] == matches models
def current_revision(engine: Engine) -> str | None
def head_revision() -> str | None
```

`SchemaState` is a frozen dataclass: `action` (`"created" | "adopted" |
"upgraded" | "current"`), `from_revision`, `to_revision`, `.changed`.

---

## `hofradar.normalize`

```python
def normalize_listing(raw: RawListing, keywords: KeywordConfig) -> NormalizedListing: ...
def parse_price(text: str | None) -> tuple[float | None, str]:   # (value, PriceType)
def parse_area(text: str | None) -> float | None                  # -> square metres
def parse_german_number(text: str | None) -> float | None
def parse_german_date(text: str | None) -> datetime | None
def normalize_text(text: str | None) -> str                       # casefold, umlaut-fold, squash ws
def text_hash(text: str | None) -> str
def extract_features(text: str, keywords: KeywordConfig) -> FeatureExtraction
def classify_property_type(title: str, description: str, keywords: KeywordConfig) -> str | None
def parse_location(text: str | None) -> LocationParts   # .postcode .town .street .district
def find_location_in_text(text: str | None) -> str | None  # unlabelled address out of prose
```

`normalize_listing` falls back to `find_location_in_text(raw.description)` when
a source gave no `location_raw`, `postcode` or `town` — an unlabelled address is
not an absent one, and a listing with no town is un-geocodable and therefore
invisible. The match must be a postcode *and* a town-shaped word, never a bare
five-digit run. A recovered location carries lower evidence confidence and says
so in `warnings`; a listing with no recoverable location warns too, rather than
leaving `town` silently `None`. See `docs/DECISIONS.md` entry 18.

`normalize_listing` carries `raw.page_kind` through unchanged and, for anything
other than `PAGE_KIND_LISTING`, prepends a German `warnings` line saying what
the page actually was — the fact every other field below it depends on. It does
not refuse: refusing is `hofradar.lifecycle.ingest`'s call (entry 19).

`FeatureExtraction` is a dataclass with: `building_features, outbuildings,
special_features, exclusion_flags, hidden_signals, is_foreclosure, is_monument,
is_private_seller, is_off_market_signal` (lists of canonical lowercase tags / bools).

## `hofradar.dedupe`

```python
def fingerprint(listing: NormalizedListing | Property) -> str
def find_duplicate(session, listing: NormalizedListing, *, lat=None, lon=None) -> DuplicateVerdict
def compare(a, b) -> DuplicateVerdict            # a/b: NormalizedListing | Property
    # Three short-circuit proofs, then the weighted evidence model. The proofs
    # are: the same (source_key, external_id); the same canonical listing URL
    # on ANY source (a URL names one page on one host, so this is the one
    # proof that crosses source boundaries - it is what joins a portal reached
    # by both a dedicated adapter and a syndicated feed of the same site); and
    # a shared image phash. Everything else needs >= 2 corroborating numeric
    # dimensions; text similarity alone never merges.
def merge_properties(session, keep: Property, drop: Property) -> Property
```

## `hofradar.lifecycle`

```python
def ingest(session, listing: NormalizedListing, *, run_id: int | None = None,
           source: Source, geo: GeoResult | None = None) -> tuple[Property, ChangeResult]
    # Raises NotAListing - writing nothing at all, not even the Observation -
    # when listing.page_kind is not PAGE_KIND_LISTING. Checked before
    # find_duplicate: docs/DECISIONS.md entry 19.
def mark_missing(session, seen_property_ids: set[int], *, source: Source,
                 run_id: int | None = None, enumeration_complete: bool) -> list[ChangeResult]
    # enumeration_complete has no default on purpose: absence is only evidence
    # when the source listed its whole inventory without error or truncation.
    # A listing missing past source.listing_ttl_days is set EXPIRED, not
    # REMOVED - see docs/DECISIONS.md entry 15. Raises ImplausibleAbsence -
    # and writes nothing - in two cases. First, unconditionally and BEFORE
    # that EXPIRED split: the seen-set is empty against a real inventory (even
    # a single visible listing). A listing_ttl_days explains why one advert
    # vanished, never why a run produced no rows at all, so it cannot excuse
    # that. Second, after the split: what remains would remove >= 2 listings
    # AND >= 30% of a source with at least 3 visible listings in one run - a
    # genuine fortnightly mass-expiry is pulled out before this one and passes.
def apply_stale_rules(session, *, stale_after_days: int = 45,
                      unverified_stale_after_days: int = 180,
                      non_reporting_source_ids: set[int] | None = None,
                      run_id: int | None = None) -> list[ChangeResult]
    # Two clocks: a source that re-reports every run vs one that never will.
def repair_phantom_removals(session, *, non_reporting_source_keys: set[str],
                            dry_run: bool = True) -> RepairReport
def changes_since(session, since: datetime, *, kinds: list[str] | None = None) -> list[dict]

class ImplausibleAbsence(RuntimeError)
    # Raised by mark_missing when a run's absences are too broad to be
    # believed (see above). The caller decides what to do - the pipeline logs
    # it as a source failure and continues with the other sources.

class NotAListing(ValueError)
    # Raised by ingest for a page that is not one advert, carrying .url,
    # .page_kind and a short English .reason. Not a source failure: the
    # pipeline counts it by reason and carries on, the paste box turns it
    # into a German sentence.

def delete_property(session, prop: Property, *, backup: bool = True) -> DeletionReport
    # The narrow escape hatch of docs/DECISIONS.md entry 20, for a mis-crawl or
    # a duplicate dedupe cannot see. Takes a hofradar.db.backup snapshot first,
    # then relies on the ORM cascade plus PRAGMA foreign_keys=ON for the one
    # child table with no ORM relationship (verification_events). Commits.
    # Writes nothing and raises when it will not proceed: ResurrectsMergedDuplicates
    # while another row's merged_into_id points here (the column is ON DELETE
    # SET NULL, so deleting a merge survivor would revive its duplicates), or
    # BackupUnavailable when a snapshot was asked for and cannot be taken.
    # "Get this off my radar" is user_state="archived", not this.

def dependent_rows(session, prop: Property) -> dict[str, int]
    # table name -> rows that would go with the property. Writes nothing; it is
    # what `hofradar delete-property` prints in its default dry run.

@dataclass(frozen=True)
class DeletionReport:
    public_id: str; title: str | None
    children: dict[str, int]; backup_path: Path | None; dry_run: bool
    .total -> int ; .summary() -> str

class ResurrectsMergedDuplicates(RuntimeError)
```

## `hofradar.geo`

```python
async def geocode(session, query: str, *, country="DE") -> GeoResult
async def route_distance(session, origin: tuple[float,float], dest: tuple[float,float]) -> tuple[float|None, float|None]
def haversine_km(a: tuple[float,float], b: tuple[float,float]) -> float
async def locate(session, listing: NormalizedListing, profile: SearchProfile) -> GeoResult
def within_air_radius(distance_air_km: float | None, profile: SearchProfile) -> bool
def within_driving_radius(distance_driving_km: float | None, profile: SearchProfile) -> bool | None  # None = unknown
def driving_band(km: float | None, profile: SearchProfile) -> str  # within_soft | within_hard | beyond | unknown
def town_in_radius(town: str | None, profile: SearchProfile) -> bool | None  # None = unknown
```

## `hofradar.costmodel`

```python
def estimate_costs(prop: Property, profile: SearchProfile) -> CostResult
def acquisition_costs(price: float, profile: SearchProfile) -> float
def infer_renovation_tier(prop: Property) -> str
def renovation_evidence(prop: Property) -> str   # "observed" | "inferred"
```

## `hofradar.scoring`

`GONE_STATUSES` (an internal constant, defined identically in
`hofradar.scoring.engine` and `hofradar.scoring.signals`) is
`{ListingStatus.REMOVED, ListingStatus.SOLD}`. `ListingStatus.EXPIRED` is
deliberately absent from both — an expired advert must not fire
`REJECT_LISTING_GONE` or zero a property's availability term; see
`docs/DECISIONS.md` entry 15.

```python
def score_property(prop: Property, profile: SearchProfile, *, cost: CostResult | None = None,
                   now: datetime | None = None) -> ScoreResult
def fit_score(prop, profile) -> tuple[float, dict]
def deal_score(prop, profile, cost) -> tuple[float, dict]
def hidden_score(prop, profile, now) -> tuple[float, dict]
def freshness_score(prop, now) -> tuple[float, dict]
def confidence_score(prop) -> tuple[float, dict]
def rescore_all(session, profile: SearchProfile, *, only_dirty: bool = True) -> int
def ranked_properties(session, profile: SearchProfile, *, limit: int | None = None,
                      include_rejected: bool = False, include_hidden: bool = False,
                      filters: dict | None = None) -> list[tuple[Property, Score]]
    # Two different facts wearing one German word. include_rejected is the
    # machine's scoring gate (Score.rejected, recomputed per profile_hash);
    # include_hidden is the human's triage (Property.user_state in
    # HIDDEN_USER_STATES, surviving every re-run). A NULL user_state always
    # passes. See docs/DECISIONS.md entry 20.

SUPPORTED_FILTERS: frozenset[str]
    # The `filters` keys ranked_properties accepts: town, min_land_sqm,
    # max_price, status, user_state, verified_only, has_outbuildings, flags.
    # These are exactly the names hofradar.web.deps.ResultFilters.as_scoring_filters()
    # emits, and tests/web/test_filter_contract.py keeps the two equal: an
    # unknown key raises, hofradar.web.lazy reports that as a missing module,
    # and the radar answers with unscored rows instead of an error.
    # `status="alive"` means "not REMOVED or SOLD"; no row carries that word.
```

`rescore_all` writes `Score` rows keyed by `profile.profile_hash`, and is the
function the web UI calls after a slider moves.

## `hofradar.sources`

```python
ADAPTERS: dict[str, type[SourceAdapter]]
def get_adapter(source: Source | SourceConfig) -> SourceAdapter
class SourceAdapter:
    key: str
    enumerates: bool                 # does discover() yield a COMPLETE inventory?
    enumeration_complete: bool       # did this run's enumeration finish intact?
    enumerated_urls: set[str]        # URLs seen this run, fetched or pre-filtered away
    can_prove_absence: bool          # enumerates and complete and can_verify
    def begin_enumeration(self) -> None: ...
    def mark_enumeration_incomplete(self, reason: str) -> None: ...
    def record_enumerated_url(self, url: str) -> None: ...
    async def discover(self, profile: SearchProfile, keywords: KeywordConfig) -> AsyncIterator[RawListing]: ...
    async def fetch_detail(self, url: str) -> RawListing | None: ...
    async def verify(self, url: str) -> tuple[bool, int | None]: ...   # (still_live, http_status)

# hofradar.sources.adapters.generic_rss
MAPPABLE_ENTRY_FIELDS: frozenset[str]
    # The raw RawListing fields a source's options.entry_field_map may fill
    # from a feed's own element names ({feedparser key: field}). This is how
    # the generic feed adapter learns a vendor dialect - e.g. classmarkets'
    # cm_postalcode/cm_locality - without learning the vendor. Raw string
    # fields only: identity (external_id, url) and authority (contact_kind,
    # listing_visible) fields are not mappable, and a non-string value is
    # refused rather than coerced.

# hofradar.sources.adapters._htmlutil - the shared full-page lift. Package
# internal (adapters only), but every HTML adapter depends on it, so its
# surface is pinned here:
def raw_listing_from_html(source_key, url, html, *, http_status=None,
                          extra=None) -> RawListing
def extract_labeled_fields(text: str) -> dict[str, str]
def listing_title(tree: HTMLParser, url: str) -> str | None
    # JSON-LD name -> <h1> -> og:title -> <title>, with a trailing site name
    # stripped only when it matches og:site_name or the URL's own host.
def page_kind(tree: HTMLParser, url: str) -> PageKind
    # listing | index | utility. Signals, in order: UTILITY_PATH_RE on the
    # path; the page's own heading; schema.org @type (an index declaration
    # outranks a listing one, because a portal makes both at once); many
    # sibling result-card links. Unclassified is "listing", so a plain broker
    # detail page that declares nothing still ingests. DECISIONS entry 19.
def is_utility_url(url: str) -> bool
UTILITY_PATH_RE: re.Pattern
    # Whole path segments only (/merkliste, /suchagent, /login, /impressum,
    # /suche, ...), so /suchergebnisse is not read as /suche. The generic
    # sitemap and RSS adapters skip these URLs - AFTER recording them as
    # enumerated, so invariant 4b / can_prove_absence are unaffected.
```

## `hofradar.report`

```python
def build_report(session, profile: SearchProfile, *, run_id: int | None = None,
                 since: datetime | None = None) -> ReportData
def render_markdown(data: ReportData) -> str
def render_html(data: ReportData) -> str

# hofradar.report.yield_stats - was a source worth building?
@dataclass
class SourceYield:
    source_key: str
    observed: int    # distinct properties observed since `since`
    in_radius: int    # of those, how many have distance_air_km <= radius_air_km
                       # (an unknown distance is never counted as in-radius)

def source_yield(session, *, since: datetime, radius_air_km: float | None = None) -> list[SourceYield]
    # radius_air_km overrides the "in radius" threshold; omit it and the module
    # falls back to YIELD_RADIUS_AIR_KM. build_report always passes the
    # configured profile.radius.air_km_max, so the table matches the radius
    # printed in the report header rather than an unrelated hardcoded value.

@dataclass
class MunicipalityCoverage:
    town: str
    observed: int    # distinct properties observed since `since`, 0 = dark municipality

def coverage_by_municipality(session, *, since: datetime, expected: list[str]) -> list[MunicipalityCoverage]
    # `expected` is required, never derived from the data: a town that produced
    # nothing cannot appear in a query over what was produced. Every name in
    # `expected` appears in the result, in that order, even at zero. The list
    # comes from config/search.yaml's `coverage.municipalities` (SearchProfile.coverage,
    # see hofradar.config.CoverageConfig) - see docs/coverage.md for how it was
    # built and its caveats.
```

`ReportData.source_yields` carries a `source_yield(..., since=now - 28 days)`
snapshot into both renderers - see docs/DECISIONS.md entry 14.
`ReportData.municipality_coverage` carries the matching `coverage_by_municipality(...)`
snapshot into both renderers, rendered as "Dunkle Gemeinden" (towns with zero
observations over the same window) - see docs/coverage.md.

## `hofradar.pipeline`

```python
async def run_pipeline(profile: SearchProfile, *, trigger: str = "manual",
                       source_keys: list[str] | None = None, dry_run: bool = False) -> SearchRun
```
