# Module API contract (binding for all packages)

Every package below MUST expose exactly these names from its `__init__.py`.
Other packages import ONLY through these. Nothing else is public.

Shared types live in `hofradar.contracts` (RawListing, NormalizedListing,
GeoResult, CostResult, ScoreResult, DuplicateVerdict, ChangeResult, Evidence).
Config types live in `hofradar.config` (SearchProfile, KeywordConfig, SourceConfig).
ORM models live in `hofradar.db.models`. Enums in `hofradar.db.enums`.

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
```

`FeatureExtraction` is a dataclass with: `building_features, outbuildings,
special_features, exclusion_flags, hidden_signals, is_foreclosure, is_monument,
is_private_seller, is_off_market_signal` (lists of canonical lowercase tags / bools).

## `hofradar.dedupe`

```python
def fingerprint(listing: NormalizedListing | Property) -> str
def find_duplicate(session, listing: NormalizedListing, *, lat=None, lon=None) -> DuplicateVerdict
def compare(a, b) -> DuplicateVerdict            # a/b: NormalizedListing | Property
def merge_properties(session, keep: Property, drop: Property) -> Property
```

## `hofradar.lifecycle`

```python
def ingest(session, listing: NormalizedListing, *, run_id: int | None = None,
           source: Source, geo: GeoResult | None = None) -> tuple[Property, ChangeResult]
def mark_missing(session, seen_property_ids: set[int], *, source: Source,
                 run_id: int | None = None, enumeration_complete: bool) -> list[ChangeResult]
    # enumeration_complete has no default on purpose: absence is only evidence
    # when the source listed its whole inventory without error or truncation.
def apply_stale_rules(session, *, stale_after_days: int = 45,
                      unverified_stale_after_days: int = 180,
                      non_reporting_source_ids: set[int] | None = None,
                      run_id: int | None = None) -> list[ChangeResult]
    # Two clocks: a source that re-reports every run vs one that never will.
def repair_phantom_removals(session, *, non_reporting_source_keys: set[str],
                            dry_run: bool = True) -> RepairReport
def changes_since(session, since: datetime, *, kinds: list[str] | None = None) -> list[dict]
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
```

## `hofradar.scoring`

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
                      include_rejected: bool = False, filters: dict | None = None) -> list[tuple[Property, Score]]
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
    can_prove_absence: bool          # enumerates and complete and can_verify
    def begin_enumeration(self) -> None: ...
    def mark_enumeration_incomplete(self, reason: str) -> None: ...
    async def discover(self, profile: SearchProfile, keywords: KeywordConfig) -> AsyncIterator[RawListing]: ...
    async def fetch_detail(self, url: str) -> RawListing | None: ...
    async def verify(self, url: str) -> tuple[bool, int | None]: ...   # (still_live, http_status)
```

## `hofradar.report`

```python
def build_report(session, profile: SearchProfile, *, run_id: int | None = None,
                 since: datetime | None = None) -> ReportData
def render_markdown(data: ReportData) -> str
def render_html(data: ReportData) -> str
```

## `hofradar.pipeline`

```python
async def run_pipeline(profile: SearchProfile, *, trigger: str = "manual",
                       source_keys: list[str] | None = None, dry_run: bool = False) -> SearchRun
```
