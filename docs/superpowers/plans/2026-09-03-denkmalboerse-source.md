# Denkmalbörse Source + Pipeline Safety Rails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the BLfD Denkmalbörse as an enabled `primary` source, behind four pipeline safety rails that the premortem showed must exist before *any* new source ships.

**Architecture:** Tasks 1–4 are cross-cutting rails (schema migrations, source terms provenance, absence-detection guards, an inferred-cost gate fix). Tasks 5–7 add the source itself: a gazetteer pre-filter promoted to the `geo` public surface, a static-tree adapter over predictable object URLs, and per-source in-radius yield instrumentation that answers whether this source was worth building. Parsing stays in `hofradar.normalize`; the adapter only fetches and hands over `RawListing`.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0, Alembic (added here), httpx, selectolax, pytest + respx, ruff.

**Spec:** `docs/premortem/premortem-transcript-20260903.md` (findings B1, B2, B4, B7, B9 and the synthesis) plus the reconnaissance recorded in this repo's conversation history. Read the premortem transcript before Task 1 — every rail in this plan exists to close a specific numbered finding, and the task headers cite them.

## Global Constraints

- Python ≥3.11, `from __future__ import annotations` at the top of every module, full type hints.
- Line length 100 (`ruff` config); `ruff check src tests` must pass.
- Module docstrings explain **why**, not what. Comments and identifiers in English; UI copy in German.
- No magic numbers in function bodies — module constants or config.
- **Invariant 1:** `hofradar.lifecycle.ingest` is the only writer of `Property` rows.
- **Invariant 2:** `ChangeKind.FIRST_SEEN` only for a property that did not exist; reappearance is `REACTIVATED`.
- **Invariant 3:** `distance_air_km` and `distance_driving_km` never substitute for each other.
- **Invariant 4:** Source role gates authority. A `discovery` source may not set `verification_status=verified`, `last_verified` or `source_date`, and its silence may not mark a listing removed.
- **Invariant 5:** Scores never live on `Property` — they go in `scores`, keyed by `profile_hash`.
- **Invariant 6:** The LLM may not write a number. Advisory fields only.
- **Invariant 7:** No bot-defence evasion, ever. If a source blocks us, we record it and stop.
- **Tests never hit the network.** Mock every outbound call with `respx` and assert on the request that *would* have been made.
- **Do NOT run the suite with `HOFRADAR_OFFLINE=1` in the environment.** That variable short-circuits geo code paths and fails 12 unrelated tests. Only tests that want the gazetteer set it themselves.
- Test command: `python -m pytest -q` (pyproject already sets `pythonpath = ["src"]`).
- After editing anything in `config/`, run `python scripts/sync_config_defaults.py`. CI fails on drift.
- Anything added to a package's public surface must also be added to `docs/MODULE_API.md` in the same task.

---

## File Structure

**New files**
- `alembic.ini` — Alembic config, pointed at `HOFRADAR_DATABASE_URL`.
- `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/` — migration environment.
- `src/hofradar/sources/adapters/denkmalboerse.py` — the adapter. Fetch and hand over `RawListing`; no parsing logic.
- `tests/sources/adapters/test_denkmalboerse.py` — respx-backed adapter tests.
- `tests/sources/fixtures/denkmalboerse_object_005816.html` — captured detail page.
- `tests/lifecycle/test_absence_guards.py` — guards for `mark_missing`.
- `tests/costmodel/test_renovation_evidence.py` — observed-vs-inferred classification.
- `tests/scoring/test_inferred_cost_gate.py` — the B9 gate change.
- `tests/geo/test_in_radius_prefilter.py` — gazetteer pre-filter.

**Modified files**
- `pyproject.toml` — add `alembic>=1.14` to `dependencies`.
- `src/hofradar/config.py` — `SourceConfig` gains `terms_checked_at`, `terms_excerpt`; add registry validation.
- `src/hofradar/lifecycle/absence.py` — the two `mark_missing` guards.
- `src/hofradar/lifecycle/__init__.py` — export the new exception.
- `src/hofradar/costmodel/renovation.py` — `renovation_evidence()`.
- `src/hofradar/costmodel/estimator.py` — set `CostResult.renovation_evidence`.
- `src/hofradar/costmodel/__init__.py` — export `renovation_evidence`.
- `src/hofradar/contracts.py` — `CostResult.renovation_evidence` field.
- `src/hofradar/scoring/engine.py` — inferred-cost gate downgrade.
- `src/hofradar/geo/prefilter.py` (new) + `src/hofradar/geo/__init__.py` — `town_in_radius`.
- `src/hofradar/sources/adapters/__init__.py` — register the adapter.
- `config/sources.yaml` + `src/hofradar/_config_defaults/sources.yaml` — the registry entry.
- `docs/DECISIONS.md` — entries 13 and 14.
- `docs/MODULE_API.md` — new public names.
- `docs/SOURCES.md` — the new source, and the "do not add" note.

---

## Task 1: Wire up Alembic before any schema change (closes B4)

DECISIONS #10 says to add Alembic "the moment the schema changes against real data". This plan is that moment. `create_all()` is a no-op on existing tables, so a new column yields `no such column` on every query — and `rm hofradar.db` is the path of least resistance mid-build, which destroys the Observation history DECISIONS #2 calls the product.

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/.gitkeep`
- Create: `scripts/backup_db.py`
- Modify: `docs/DECISIONS.md` (append entry 13)

**Interfaces:**
- Consumes: `hofradar.db.models.Base`, `hofradar.db.session` (existing).
- Produces: `alembic upgrade head` as the schema command for every later task. No later task may call `create_all()` to add a column.

- [ ] **Step 1: Back up the live database before touching anything**

```bash
mkdir -p backups
python - <<'PY'
import os, shutil, sqlite3, datetime, pathlib
url = os.environ.get("HOFRADAR_DATABASE_URL", "")
if url and not url.startswith("sqlite"):
    raise SystemExit("Non-SQLite DSN: take a server-side dump instead, then re-run.")
src = pathlib.Path(url.replace("sqlite:///", "")) if url else pathlib.Path("data/hofradar.db")
if not src.exists():
    raise SystemExit(f"No database at {src} - nothing to back up, continue.")
stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
dst = pathlib.Path("backups") / f"hofradar-{stamp}.db"
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)          # consistent copy even with WAL active
print("backed up to", dst)
PY
```

Expected: prints a path under `backups/`, or says there is no database yet.

- [ ] **Step 2: Add the dependency**

In `pyproject.toml`, add to the `dependencies` list, after `"sqlalchemy>=2.0.36",`:

```toml
    "alembic>=1.14",
```

Then: `pip install -e ".[dev,pdf,images]"`

- [ ] **Step 3: Write the failing test**

Create `tests/db/test_migrations.py`:

```python
"""The migration environment must see every model table.

A migration environment whose metadata is wired to the wrong Base autogenerates
an empty revision and silently stops protecting the schema.
"""

from __future__ import annotations

from alembic.config import Config

from hofradar.db.models import Base


def test_alembic_env_target_metadata_covers_models() -> None:
    config = Config("alembic.ini")
    assert config.get_main_option("script_location") == "migrations"

    from migrations.env import target_metadata

    assert target_metadata is Base.metadata
    assert "properties" in target_metadata.tables
    assert "observations" in target_metadata.tables
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/db/test_migrations.py -v`
Expected: FAIL — `alembic.util.exc.CommandError` or `FileNotFoundError` on `alembic.ini`.

- [ ] **Step 5: Create the Alembic scaffold**

`alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = src
file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`migrations/env.py`:

```python
"""Alembic environment.

The DSN is read from the same place the application reads it, so a migration
can never be applied to a different database than the one the app opens.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from hofradar.db.models import Base

config = context.config
target_metadata = Base.metadata

DEFAULT_URL = "sqlite:///data/hofradar.db"
config.set_main_option("sqlalchemy.url", os.environ.get("HOFRADAR_DATABASE_URL", DEFAULT_URL))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # render_as_batch: SQLite cannot ALTER a column in place, so Alembic
        # rebuilds the table. Without it every later ALTER fails on SQLite.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Then: `touch migrations/versions/.gitkeep`

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/db/test_migrations.py -v`
Expected: PASS

- [ ] **Step 7: Stamp the existing database as the baseline**

```bash
alembic revision --autogenerate -m "baseline from current models"
alembic stamp head
alembic current
```

Expected: `alembic current` prints one revision id. The generated revision under `migrations/versions/` describes the *current* schema — open it and confirm `upgrade()` creates `properties` and `observations`. An existing database is stamped, not upgraded, so no data is touched.

- [ ] **Step 8: Verify the whole suite is still green**

Run: `python -m pytest -q && ruff check src tests`
Expected: all tests pass, ruff clean.

- [ ] **Step 9: Record the decision**

Append to `docs/DECISIONS.md`:

```markdown
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
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml alembic.ini migrations scripts/backup_db.py tests/db/test_migrations.py docs/DECISIONS.md
git commit -m "build: wire up Alembic before the first schema change against real data"
```

---

## Task 2: A source must record its terms before it may be enabled (closes B7)

Premortem B7: the OVB adapter was written, a tempo axis was designed around it, and eleven weeks later the AGB turned out to forbid automated retrieval. The classification was an argument, not a finding, and nothing in the registry could tell the difference. This task makes an unchecked source structurally unable to be enabled.

**Files:**
- Modify: `src/hofradar/config.py:312-324` (`SourceConfig`)
- Create: `tests/config/test_source_terms.py`
- Modify: `docs/MODULE_API.md`

**Interfaces:**
- Consumes: `SourceConfig` (existing pydantic model).
- Produces: `SourceConfig.terms_checked_at: date | None`, `SourceConfig.terms_excerpt: str | None`, and a model validator rejecting `enabled=True` without both. Task 7 and Plan B both rely on this.

- [ ] **Step 1: Write the failing test**

Create `tests/config/test_source_terms.py`:

```python
"""A source may not be enabled until somebody has read its terms.

The failure this prevents: an adapter written, a scoring abstraction designed
around it, and only then the discovery that the site's terms forbid automated
access - at which point the abstraction has no producer.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from hofradar.config import SourceConfig


def test_enabled_source_without_terms_check_is_rejected() -> None:
    with pytest.raises(ValidationError, match="terms_checked_at"):
        SourceConfig(key="x", name="X", enabled=True, adapter="manual")


def test_enabled_source_with_terms_check_is_accepted() -> None:
    source = SourceConfig(
        key="x",
        name="X",
        enabled=True,
        adapter="manual",
        terms_checked_at=date(2026, 9, 3),
        terms_excerpt="robots.txt: no Disallow for /objekte/. No AGB clause on automated access.",
    )
    assert source.terms_checked_at == date(2026, 9, 3)


def test_disabled_source_needs_no_terms_check() -> None:
    source = SourceConfig(key="x", name="X", enabled=False, adapter="manual")
    assert source.terms_checked_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/config/test_source_terms.py -v`
Expected: FAIL — `test_enabled_source_without_terms_check_is_rejected` fails because no `ValidationError` is raised; the other two error on an unexpected keyword.

- [ ] **Step 3: Write minimal implementation**

In `src/hofradar/config.py`, add `from datetime import date` to the imports and `model_validator` to the pydantic import, then extend `SourceConfig`:

```python
class SourceConfig(BaseModel):
    key: str
    name: str
    role: str = "discovery"
    adapter: str = "generic_rss"
    base_url: str | None = None
    region: str | None = None
    reliability: float = 0.5
    enabled: bool = False
    rate_limit_seconds: float = 2.0
    respect_robots: bool = True
    notes: str | None = None
    #: The day somebody actually read this source's robots.txt and terms, and
    #: what they found. A source nobody has checked may be written and tested,
    #: but it may not be switched on - see docs/DECISIONS.md entry 14.
    terms_checked_at: date | None = None
    terms_excerpt: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enabled_requires_terms_check(self) -> SourceConfig:
        if self.enabled and not (self.terms_checked_at and self.terms_excerpt):
            raise ValueError(
                f"source {self.key!r} is enabled but has no recorded terms check: "
                "set terms_checked_at and terms_excerpt, or leave it disabled"
            )
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/config/test_source_terms.py -v`
Expected: PASS

- [ ] **Step 5: Backfill the sources that already ship enabled**

The existing enabled sources (`manual`, `csv_import`, `zvg_bayern`, `generic_rss`, `generic_sitemap`) now fail validation. `manual` and `csv_import` take no network at all; `zvg_bayern` is a public register. Add to each enabled entry in **both** `config/sources.yaml` and `src/hofradar/_config_defaults/sources.yaml`:

```yaml
    terms_checked_at: 2026-09-03
    terms_excerpt: >
      Local ingest only, no outbound requests - no site terms apply.
```

For `zvg_bayern`:

```yaml
    terms_checked_at: 2026-09-03
    terms_excerpt: >
      Official public foreclosure register, published for public inspection.
      robots.txt permits /index.php; no clause restricting automated retrieval.
```

For `generic_rss` and `generic_sitemap` (which carry no feeds yet):

```yaml
    terms_checked_at: 2026-09-03
    terms_excerpt: >
      Terms are a property of each configured feed, not of this adapter. Every
      entry added to options.feeds/options.sites carries its own check.
```

Then: `python scripts/sync_config_defaults.py`

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass. If a test constructs an enabled `SourceConfig` inline, add the two fields there too.

- [ ] **Step 7: Document the new public fields**

In `docs/MODULE_API.md`, under the `hofradar.config` description, note that `SourceConfig` now carries `terms_checked_at: date | None` and `terms_excerpt: str | None`, and that `enabled=True` requires both.

- [ ] **Step 8: Commit**

```bash
git add src/hofradar/config.py tests/config/test_source_terms.py config/sources.yaml src/hofradar/_config_defaults/sources.yaml docs/MODULE_API.md
git commit -m "feat(config): a source may not be enabled without a recorded terms check"
```

---

## Task 3: `mark_missing` refuses weak evidence (closes B2, and B3's first half)

Premortem B2: a template change made `discover()` return `[]`, the empty seen-set flowed into `mark_missing`, and eleven live listings were transitioned to REMOVED with clean Observation rows recording the fiction. An empty result set is a legal result set, so nothing raised.

**Files:**
- Modify: `src/hofradar/lifecycle/absence.py:34-77`
- Modify: `src/hofradar/lifecycle/__init__.py`
- Create: `tests/lifecycle/test_absence_guards.py`
- Modify: `docs/MODULE_API.md`

**Interfaces:**
- Consumes: `mark_missing(session, seen_property_ids, *, source, run_id=None) -> list[ChangeResult]` (existing signature; unchanged).
- Produces: `hofradar.lifecycle.ImplausibleAbsence(RuntimeError)`, raised instead of returning. Plan B Task 1 extends the same function with `listing_ttl_days`.

- [ ] **Step 1: Write the failing test**

Create `tests/lifecycle/test_absence_guards.py`:

```python
"""mark_missing must not turn a parser failure into a market event.

A source that suddenly reports nothing has either lost its whole inventory in
one week - which never happens - or stopped parsing. The second is
overwhelmingly more likely, and the damage is written into the append-only
observation history where it cannot be distinguished from the truth later.
"""

from __future__ import annotations

import pytest

from hofradar.db.enums import ListingStatus
from hofradar.lifecycle import ImplausibleAbsence, mark_missing
from tests.lifecycle.conftest import make_property, make_source  # existing helpers


def test_empty_seen_set_raises_instead_of_removing_everything(session) -> None:
    source = make_source(session, key="denkmalboerse", role="primary")
    for i in range(3):
        make_property(session, source=source, external_id=f"00{i}")
    session.flush()

    with pytest.raises(ImplausibleAbsence, match="saw nothing"):
        mark_missing(session, set(), source=source)


def test_removing_more_than_the_threshold_raises(session) -> None:
    source = make_source(session, key="denkmalboerse", role="primary")
    props = [make_property(session, source=source, external_id=f"00{i}") for i in range(10)]
    session.flush()

    # Saw 5 of 10 - half the inventory vanished in one run.
    seen = {p.id for p in props[:5]}
    with pytest.raises(ImplausibleAbsence, match="50"):
        mark_missing(session, seen, source=source)


def test_a_normal_single_removal_still_works(session) -> None:
    source = make_source(session, key="denkmalboerse", role="primary")
    props = [make_property(session, source=source, external_id=f"00{i}") for i in range(10)]
    session.flush()

    seen = {p.id for p in props[:9]}
    changes = mark_missing(session, seen, source=source)

    assert len(changes) == 1
    assert props[9].listing_status == ListingStatus.REMOVED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/lifecycle/test_absence_guards.py -v`
Expected: FAIL — `ImportError: cannot import name 'ImplausibleAbsence'`.

- [ ] **Step 3: Write minimal implementation**

In `src/hofradar/lifecycle/absence.py`, add near the other module constants:

```python
#: A run that saw this fraction or more of a source's visible inventory
#: disappear is treated as a parser failure, not as a market event. Half an
#: inventory never goes in one week; a changed HTML template does.
IMPLAUSIBLE_ABSENCE_FRACTION = 0.30


class ImplausibleAbsence(RuntimeError):
    """A run's absences are too broad to be believed, so nothing is written."""
```

Then replace the body of `mark_missing` between the `can_verify` check and the loop:

```python
    now = utcnow()
    seen = set(seen_property_ids or ())
    rows = session.execute(
        select(PropertySource).where(
            PropertySource.source_id == source.id,
            PropertySource.last_listing_visible.is_(True),
        )
    ).scalars().all()

    # Guard before writing anything: an empty or near-empty seen-set is far more
    # likely to be a broken selector than an emptied market, and the transitions
    # below are recorded in the append-only history where they cannot be undone.
    if rows and not seen:
        raise ImplausibleAbsence(
            f"source {source.key!r} saw nothing while {len(rows)} of its listings "
            "are still marked visible; refusing to mark them removed"
        )
    missing = [ps for ps in rows if ps.property_id not in seen]
    if rows:
        fraction = len(missing) / len(rows)
        if fraction >= IMPLAUSIBLE_ABSENCE_FRACTION:
            raise ImplausibleAbsence(
                f"source {source.key!r} would remove {len(missing)} of {len(rows)} "
                f"listings ({fraction:.0%}); refusing above "
                f"{IMPLAUSIBLE_ABSENCE_FRACTION:.0%}"
            )

    changes: list[ChangeResult] = []
    for ps in missing:
```

Delete the now-redundant `if ps.property_id in seen: continue` line from the loop body; every other line of the loop is unchanged.

In `src/hofradar/lifecycle/__init__.py`, add `ImplausibleAbsence` to the imports from `.absence` and to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/lifecycle/test_absence_guards.py -v`
Expected: PASS

- [ ] **Step 5: Handle the raise where the pipeline calls it**

In `src/hofradar/pipeline/`, find the `mark_missing` call site (`grep -rn "mark_missing" src/hofradar/pipeline/`). Wrap it so one bad source fails loudly without aborting the run:

```python
        try:
            changes.extend(mark_missing(session, seen_ids, source=source, run_id=run.id))
        except ImplausibleAbsence as exc:
            # Recorded as a source failure, not swallowed: the run continues for
            # other sources, and this source's absences are simply not believed.
            logger.error("absence detection refused for %s: %s", source.key, exc)
            run.errors.append(f"{source.key}: {exc}")
```

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass. Any existing test that calls `mark_missing` with an empty seen-set on a populated source now expects the raise — update it to assert the raise, which is the new correct behaviour.

- [ ] **Step 7: Document the new public name**

Add `ImplausibleAbsence` to the `hofradar.lifecycle` block in `docs/MODULE_API.md`.

- [ ] **Step 8: Commit**

```bash
git add src/hofradar/lifecycle tests/lifecycle/test_absence_guards.py src/hofradar/pipeline docs/MODULE_API.md
git commit -m "fix(lifecycle): refuse absence detection when a run's absences are implausible"
```

---

## Task 4: An inferred renovation cost may not reject a property (closes B9)

Premortem B9: every genuine Hofstelle is pre-1960 with condition unstated, so `infer_renovation_tier` returns HEAVY by the age rule; the €/m² band then applies to 400+ m² and pushes `total_mid` past `effective_total_hard_max` on properties whose *asking price* sits well inside budget. `REJECT_TOTAL_COST` removes rather than ranks, so the target class disappears entirely.

The fix: distinguish a renovation figure backed by evidence from one produced by the age fallback, and let only the former reject.

**Files:**
- Modify: `src/hofradar/costmodel/renovation.py`
- Modify: `src/hofradar/costmodel/estimator.py:138+` (inside `estimate_costs`)
- Modify: `src/hofradar/costmodel/__init__.py`
- Modify: `src/hofradar/contracts.py:142-154` (`CostResult`)
- Modify: `src/hofradar/scoring/engine.py` (gate constants and `_apply_gates`)
- Create: `tests/costmodel/test_renovation_evidence.py`, `tests/scoring/test_inferred_cost_gate.py`
- Modify: `docs/MODULE_API.md`

**Interfaces:**
- Consumes: `infer_renovation_tier(prop) -> RenovationTier`, `estimate_costs(prop, profile) -> CostResult` (both unchanged).
- Produces: `renovation_evidence(prop) -> str` returning `"observed"` or `"inferred"`; `CostResult.renovation_evidence: str`; scoring flag `FLAG_COST_INFERRED = "TOTAL_COST_INFERRED"`.

- [ ] **Step 1: Write the failing test for the classifier**

Create `tests/costmodel/test_renovation_evidence.py`:

```python
"""Whether the renovation figure rests on evidence or on the age fallback.

The distinction is load-bearing: a cost derived from "pre-1960 and nobody said
anything" must not be allowed to hard-reject a property, because it is a
default, not a measurement.
"""

from __future__ import annotations

from hofradar.costmodel import renovation_evidence
from tests.costmodel.conftest import make_property  # existing helper


def test_stated_condition_counts_as_observed() -> None:
    prop = make_property(condition="sanierungsbeduerftig", year_built=1890)
    assert renovation_evidence(prop) == "observed"


def test_condition_tag_counts_as_observed() -> None:
    prop = make_property(building_features=["kernsanierung"], year_built=1890)
    assert renovation_evidence(prop) == "observed"


def test_age_only_fallback_counts_as_inferred() -> None:
    prop = make_property(year_built=1890)
    assert renovation_evidence(prop) == "inferred"


def test_nothing_at_all_counts_as_inferred() -> None:
    prop = make_property()
    assert renovation_evidence(prop) == "inferred"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/costmodel/test_renovation_evidence.py -v`
Expected: FAIL — `ImportError: cannot import name 'renovation_evidence'`.

- [ ] **Step 3: Write minimal implementation**

In `src/hofradar/costmodel/renovation.py`, append:

```python
#: What the renovation tier rests on. ``observed`` means the listing said
#: something about the condition; ``inferred`` means we fell through to the age
#: rule, which is a deliberately pessimistic default rather than a measurement.
EVIDENCE_OBSERVED = "observed"
EVIDENCE_INFERRED = "inferred"


def renovation_evidence(prop: Property) -> str:
    """Did anybody actually state this building's condition?

    Kept separate from :func:`infer_renovation_tier` so the tier stays a single
    value with one meaning. Callers that must not act on a guess ask this.
    """
    if _tier_from_condition(prop) is not RenovationTier.UNKNOWN:
        return EVIDENCE_OBSERVED
    if _tier_from_tags(property_tags(prop)) is not RenovationTier.UNKNOWN:
        return EVIDENCE_OBSERVED
    return EVIDENCE_INFERRED
```

In `src/hofradar/costmodel/__init__.py`, export `renovation_evidence` (and add it to `__all__`).

In `src/hofradar/contracts.py`, add to `CostResult` after `renovation_tier`:

```python
    #: "observed" or "inferred" - see hofradar.costmodel.renovation_evidence.
    renovation_evidence: str = "inferred"
```

In `src/hofradar/costmodel/estimator.py`, import `renovation_evidence` and set it where `CostResult` is constructed inside `estimate_costs`:

```python
        renovation_evidence=renovation_evidence(prop),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/costmodel/test_renovation_evidence.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for the gate**

Create `tests/scoring/test_inferred_cost_gate.py`:

```python
"""A modelled cost may flag a property, never remove it from the shortlist.

Every genuine Hofstelle is pre-1960 with the condition unstated, so the
pessimistic age rule applies to a large building and the modelled total clears
the hard maximum on properties whose asking price is comfortably inside budget.
Rejecting on that number makes the system blind to its own target class.
"""

from __future__ import annotations

from hofradar.scoring.engine import (
    FLAG_COST_INFERRED,
    REJECT_TOTAL_COST,
    score_property,
)
from tests.scoring.conftest import make_profile, make_property  # existing helpers


def test_inferred_cost_over_budget_flags_but_does_not_reject() -> None:
    profile = make_profile(total_hard_max=800_000)
    # Pre-1960, no stated condition: tier HEAVY by the age rule alone.
    prop = make_property(price=350_000, living_sqm=400, year_built=1890)

    result = score_property(prop, profile)

    assert REJECT_TOTAL_COST not in result.reject_reasons
    assert FLAG_COST_INFERRED in result.flags


def test_observed_cost_over_budget_still_rejects() -> None:
    profile = make_profile(total_hard_max=800_000)
    prop = make_property(
        price=350_000, living_sqm=400, year_built=1890, condition="sanierungsbeduerftig"
    )

    result = score_property(prop, profile)

    assert REJECT_TOTAL_COST in result.reject_reasons
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/scoring/test_inferred_cost_gate.py -v`
Expected: FAIL — `ImportError: cannot import name 'FLAG_COST_INFERRED'`.

- [ ] **Step 7: Write minimal implementation**

In `src/hofradar/scoring/engine.py`, add beside the other flag constants:

```python
#: The total-cost gate was crossed only by a renovation figure nobody stated.
#: Recorded as a flag rather than a rejection: the pessimistic age rule is a
#: default, and defaulting a property out of the shortlist hides exactly the
#: pre-1960 farmsteads this project exists to find.
FLAG_COST_INFERRED = "TOTAL_COST_INFERRED"
```

In `_apply_gates`, replace the total-cost block:

```python
    total_mid = float(cost.total_mid or 0.0)
    carve_out = development_score >= gates.exceptional_development_min
    cost_is_inferred = cost.renovation_evidence != "observed"

    def _cost_reject(reason: str) -> None:
        """Reject only on a figure somebody stood behind; otherwise flag."""
        if cost_is_inferred:
            if FLAG_COST_INFERRED not in result.flags:
                result.flags.append(FLAG_COST_INFERRED)
        else:
            result.reject_reasons.append(reason)

    if total_mid > budget.effective_total_hard_max:
        if carve_out:
            result.flags.append(FLAG_EXCEPTIONAL_CARVE_OUT)
        else:
            _cost_reject(REJECT_TOTAL_COST)
    elif total_mid > budget.effective_total_exceptional_max:
        if carve_out:
            result.flags.append(FLAG_EXCEPTIONAL_CARVE_OUT)
        else:
            _cost_reject(REJECT_EXCEPTIONAL_WITHOUT_DEVELOPMENT)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/scoring/test_inferred_cost_gate.py -v`
Expected: PASS

- [ ] **Step 9: Run the whole suite**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass. Existing scoring tests that assert `REJECT_TOTAL_COST` on a property with no stated condition now get a flag instead — update those tests to state a condition, which is what they meant to test.

- [ ] **Step 10: Document it**

Add `renovation_evidence(prop) -> str` to the `hofradar.costmodel` block in `docs/MODULE_API.md`, and note the new `CostResult.renovation_evidence` field in the shared-types line.

- [ ] **Step 11: Commit**

```bash
git add src/hofradar/costmodel src/hofradar/contracts.py src/hofradar/scoring/engine.py tests/costmodel/test_renovation_evidence.py tests/scoring/test_inferred_cost_gate.py docs/MODULE_API.md
git commit -m "fix(scoring): an inferred renovation cost flags a property instead of rejecting it"
```

---

## Task 5: Gazetteer pre-filter on the geo public surface

The Denkmalbörse is Bavaria-wide; the radius is Upper Bavaria. The town is in every object title (`"Kleinbauernhof in Altenstadt bei Schongau"`). A gazetteer hit is free and offline, and lets the adapter skip the *detail fetch* for a Franconian object — which conserves the crawl budget against a public authority, the genuinely scarce resource here.

**The rule this task encodes:** the pre-filter may only skip a fetch. It may never mark a property rejected, and an unknown town must fall through to the full path. Rejecting by heuristic is how you lose the one Sacherl you were looking for.

**Files:**
- Create: `src/hofradar/geo/prefilter.py`
- Modify: `src/hofradar/geo/__init__.py`
- Create: `tests/geo/test_in_radius_prefilter.py`
- Modify: `docs/MODULE_API.md`

**Interfaces:**
- Consumes: `hofradar.geo.gazetteer.lookup(query) -> GazetteerEntry | None`, `hofradar.geo.distance.haversine_km`, `SearchProfile.radius`, `SearchProfile.origin`.
- Produces: `town_in_radius(town: str | None, profile: SearchProfile) -> bool | None` — `True` known and inside, `False` known and outside, **`None` unknown**. Task 6 calls it and treats `None` as "fetch anyway".

- [ ] **Step 1: Write the failing test**

Create `tests/geo/test_in_radius_prefilter.py`:

```python
"""A cheap, offline "is this town even worth fetching" check.

Three-valued on purpose. A town the gazetteer does not know must not be
silently discarded - the unknown case is exactly where an obscure hamlet with a
farmstead lives, so it falls through to the full geocoding path.
"""

from __future__ import annotations

from hofradar.geo import town_in_radius
from tests.geo.conftest import make_profile  # existing helper


def test_town_inside_the_radius_is_true() -> None:
    profile = make_profile(air_km_max=60)
    assert town_in_radius("Bad Aibling", profile) is True


def test_town_far_outside_the_radius_is_false() -> None:
    profile = make_profile(air_km_max=60)
    assert town_in_radius("Nordhalben", profile) is False


def test_unknown_town_is_none_so_the_caller_fetches_anyway() -> None:
    profile = make_profile(air_km_max=60)
    assert town_in_radius("Hinterdupfing", profile) is None


def test_missing_town_is_none() -> None:
    profile = make_profile(air_km_max=60)
    assert town_in_radius(None, profile) is None
    assert town_in_radius("", profile) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/geo/test_in_radius_prefilter.py -v`
Expected: FAIL — `ImportError: cannot import name 'town_in_radius'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/hofradar/geo/prefilter.py`:

```python
"""A free, offline "is this worth a network round trip" question.

Bavaria-wide sources put most of their inventory outside an Upper-Bavarian
radius, and every detail page fetched for a Franconian object is crawl budget
spent on a public authority's server for nothing. The gazetteer already knows
where the towns are, so the question costs nothing.

The return type is deliberately three-valued. ``None`` means "the gazetteer has
never heard of this place", which is the single most likely description of a
hamlet with a farmstead in it - so it must reach the real geocoder, not the
bin. This function may only ever save a fetch; it may never reject a property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hofradar.geo.distance import haversine_km
from hofradar.geo.gazetteer import lookup

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import SearchProfile


def town_in_radius(town: str | None, profile: SearchProfile) -> bool | None:
    """Is this town inside the air radius? ``None`` when we cannot tell.

    Uses the air radius only, and only to decide whether to *fetch*. Air
    distance never stands in for road distance anywhere a property is judged.
    """
    if not town or not town.strip():
        return None
    entry = lookup(town)
    if entry is None:
        return None
    origin = (profile.origin.lat, profile.origin.lon)
    return haversine_km(origin, (entry.lat, entry.lon)) <= profile.radius.air_km_max
```

In `src/hofradar/geo/__init__.py`, add `from hofradar.geo.prefilter import town_in_radius` and append `"town_in_radius"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/geo/test_in_radius_prefilter.py -v`
Expected: PASS. If `make_profile` does not exist in `tests/geo/conftest.py`, add it there building a `SearchProfile` with the Westham origin and the given `air_km_max`.

- [ ] **Step 5: Document it**

Add to the `hofradar.geo` block in `docs/MODULE_API.md`:

```python
def town_in_radius(town: str | None, profile: SearchProfile) -> bool | None  # None = unknown
```

- [ ] **Step 6: Commit**

```bash
git add src/hofradar/geo tests/geo/test_in_radius_prefilter.py docs/MODULE_API.md
git commit -m "feat(geo): offline gazetteer pre-filter that may skip a fetch, never reject"
```

---

## Task 6: The Denkmalbörse adapter

**What the source actually is** (verified): a static file tree with a Perl CGI search in front. Detail pages live at `https://www.blfd.bayern.de/information-service/denkmalboerse/objekte/{6-digit zero-padded id}/index.html` — confirmed live for ids `004712`, `005759`, `005816`, `007138`, `007148`. Owners list free of charge; each object is an exposé with photo and descriptive text; contact details are **in the listing** (not Chiffre, not via the Amt); BLfD disclaims accuracy.

**Files:**
- Create: `src/hofradar/sources/adapters/denkmalboerse.py`
- Create: `tests/sources/adapters/test_denkmalboerse.py`
- Create: `tests/sources/fixtures/denkmalboerse_object_005816.html`
- Modify: `src/hofradar/sources/adapters/__init__.py`

**Interfaces:**
- Consumes: the `SourceAdapter` base, which flattens the source's fields onto the adapter itself — use `self.base_url`, `self.options`, `self.key`, `self.role`, **not** `self.source`. All HTTP goes through `await self.client.get(url)` (a `PoliteClient`: per-host rate limiting, robots.txt and the descriptive User-Agent are handled there, so an adapter cannot skip them). Also `RawListing`, `town_in_radius` from Task 5, `raw_listing_from_html` from `adapters/_htmlutil.py`.
- Produces: `DenkmalboerseAdapter` with `key = "denkmalboerse"`, registered in `ADAPTERS`. Yields `RawListing` with `external_id` = the 6-digit id, `contact_kind="private"`.
- **Not overridden:** `verify()`. The base already implements it via `_verify_impl` (GET, check status against `{404, 410}`, then scan the body for gone-markers), and gates it on `can_verify` so only a `primary`/`local` role may call it. Static object pages return a clean 404 when withdrawn, so the default is correct here.

**Blocked on:** Task 2's terms check. The registry entry in Task 7 cannot be `enabled: true` until you have run the four commands in Step 1 and pasted the result.

- [ ] **Step 1: Read the terms — this is a real gate, not a formality**

From a machine with network access (this cannot be done from the dev container; its egress proxy blocks the host):

```bash
curl -s https://www.blfd.bayern.de/robots.txt
curl -sI https://www.blfd.bayern.de/information-service/denkmalboerse/objekte/005816/index.html
curl -s https://www.blfd.bayern.de/information-service/denkmalboerse/ | grep -i -A5 'nutzungsbedingung\|impressum\|haftung'
curl -s 'https://www.blfd.bayern.de/cgi-bin/fts_search_verkauf.pl' | head -100
```

Record the outcome. **If robots.txt disallows `/information-service/` or the terms restrict automated retrieval, stop here** — invariant 7 admits no argument, and this plan ends with the adapter written but `enabled: false`, exactly as the three portal adapters did.

- [ ] **Step 2: Capture the fixture**

```bash
curl -s https://www.blfd.bayern.de/information-service/denkmalboerse/objekte/005816/index.html \
  > tests/sources/fixtures/denkmalboerse_object_005816.html
```

This is the "Kleinbauernhof in Altenstadt bei Schongau" object. Open it and note which element holds the title, the description, the price (or "auf Anfrage"), the areas, and the contact block — Step 4's selectors must match what is actually there, not what this plan guessed.

- [ ] **Step 3: Write the failing test**

Create `tests/sources/adapters/test_denkmalboerse.py`:

```python
"""The adapter fetches; it does not parse German into typed facts.

Everything asserted here is about *which request would have been made* and
which stringy fields came back. Turning "auf Anfrage" into a PriceType is
hofradar.normalize's job, and testing it here would duplicate that contract.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from hofradar.sources import get_adapter
from tests.sources.conftest import make_source_config  # existing helper

FIXTURE = Path(__file__).parent.parent / "fixtures" / "denkmalboerse_object_005816.html"
BASE = "https://www.blfd.bayern.de"
DETAIL = f"{BASE}/information-service/denkmalboerse/objekte/005816/index.html"


@pytest.fixture
def adapter():
    return get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )


@respx.mock
async def test_fetch_detail_requests_the_static_object_page(adapter) -> None:
    route = respx.get(DETAIL).mock(
        return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))
    )

    listing = await adapter.fetch_detail(DETAIL)

    assert route.called
    assert listing is not None
    assert listing.source_key == "denkmalboerse"
    assert listing.url == DETAIL
    assert listing.external_id == "005816"
    # Contact is published in the exposé itself - not Chiffre, not via the Amt.
    assert listing.contact_kind == "private"
    assert "Altenstadt" in (listing.title or "")


@respx.mock
async def test_verify_reports_a_removed_object_as_gone(adapter) -> None:
    # Regression coverage for inherited behaviour, not new code: SourceAdapter
    # already implements verify() via _verify_impl. It is here because the
    # role gate (only primary/local may verify) is the thing most likely to
    # break silently if this source is ever reclassified.
    respx.get(DETAIL).mock(return_value=httpx.Response(404))

    still_live, status = await adapter.verify(DETAIL)

    assert still_live is False
    assert status == 404


@respx.mock
async def test_discover_skips_the_detail_fetch_for_an_out_of_radius_town(adapter, profile, keywords) -> None:
    index = f"{BASE}/cgi-bin/fts_search_verkauf.pl"
    respx.get(index).mock(
        return_value=httpx.Response(
            200,
            text=(
                '<a href="/information-service/denkmalboerse/objekte/005759/index.html">'
                "Pfarrhaus in Nordhalben</a>"
            ),
        )
    )
    detail = respx.get(
        f"{BASE}/information-service/denkmalboerse/objekte/005759/index.html"
    ).mock(return_value=httpx.Response(200, text="<html></html>"))

    listings = [item async for item in adapter.discover(profile, keywords)]

    assert listings == []
    assert not detail.called, "Nordhalben is in Oberfranken; the page must not be fetched"


@respx.mock
async def test_discover_fetches_an_unknown_town_rather_than_discarding_it(adapter, profile, keywords) -> None:
    index = f"{BASE}/cgi-bin/fts_search_verkauf.pl"
    respx.get(index).mock(
        return_value=httpx.Response(
            200,
            text=(
                '<a href="/information-service/denkmalboerse/objekte/007148/index.html">'
                "Historische Hofstelle in Hinterdupfing</a>"
            ),
        )
    )
    detail = respx.get(
        f"{BASE}/information-service/denkmalboerse/objekte/007148/index.html"
    ).mock(return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8")))

    listings = [item async for item in adapter.discover(profile, keywords)]

    assert detail.called, "an unknown town must fall through to the full path"
    assert len(listings) == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/sources/adapters/test_denkmalboerse.py -v`
Expected: FAIL — `KeyError: 'denkmalboerse'` from `get_adapter`.

- [ ] **Step 5: Write minimal implementation**

Create `src/hofradar/sources/adapters/denkmalboerse.py`:

```python
"""The Bayerisches Landesamt für Denkmalpflege's Denkmalbörse.

Why this source is worth having: a large share of Bavarian farmsteads are
Baudenkmäler, owners list here free of charge, and nobody browses a state
authority's search CGI - so the competition for anything found here is a
fraction of a portal's. It is also the origin of the advert rather than a copy
of one, which is what earns it the ``primary`` role: the exposé is withdrawn
when the owner withdraws it, so its silence is the seller's own signal.

Structurally it is a static file tree with a Perl CGI search in front. Detail
pages are plain HTML at a predictable path keyed by a stable six-digit id, so
``fetch_detail`` and ``verify`` are ordinary cheap GETs and the id is a
first-class external identifier for deduplication.

BLfD disclaims the accuracy of what owners submit, which is modelled as a
*reliability* below 1.0 in the registry - never as a lower role. Accuracy and
provenance are different questions.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from hofradar.contracts import RawListing
from hofradar.geo import town_in_radius
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.base import SourceAdapter

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import KeywordConfig, SearchProfile

logger = logging.getLogger(__name__)

#: The search CGI. It is the only index the site offers; there is no sitemap.
SEARCH_PATH = "/cgi-bin/fts_search_verkauf.pl"
#: Object detail pages: /information-service/denkmalboerse/objekte/007148/index.html
OBJECT_HREF_RE = re.compile(
    r"/information-service/denkmalboerse/objekte/(\d{6})/index\.html"
)
#: Titles read "Historische Hofstelle in Reichenschwand - Leuzenberg". Everything
#: after the last " in " is the place, up to a district suffix.
TITLE_TOWN_RE = re.compile(r"\bin\s+([^,\-–]+)", re.IGNORECASE)


def _town_from_title(title: str | None) -> str | None:
    if not title:
        return None
    match = TITLE_TOWN_RE.search(title)
    return match.group(1).strip() if match else None


class DenkmalboerseAdapter(SourceAdapter):
    key = "denkmalboerse"

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        base = self.base_url or "https://www.blfd.bayern.de"
        response = await self.client.get(urljoin(base, SEARCH_PATH))
        tree = HTMLParser(response.text)

        seen: set[str] = set()
        for node in tree.css("a"):
            href = node.attributes.get("href") or ""
            match = OBJECT_HREF_RE.search(href)
            if match is None:
                continue
            object_id = match.group(1)
            if object_id in seen:
                continue
            seen.add(object_id)

            title = node.text(strip=True)
            # The pre-filter may only save a fetch. False means the gazetteer is
            # sure this is outside the radius; None means it has never heard of
            # the place, which is precisely where a hamlet with a farmstead
            # lives - so None fetches.
            if town_in_radius(_town_from_title(title), profile) is False:
                logger.debug("denkmalboerse: skipping %s (%s) - outside radius", object_id, title)
                continue

            listing = await self.fetch_detail(urljoin(base, match.group(0)))
            if listing is not None:
                yield listing

    async def fetch_detail(self, url: str) -> RawListing | None:
        response = await self.client.get(url)
        if response.status_code >= 400:
            return None
        listing = raw_listing_from_html(self.key, url, response.text)
        match = OBJECT_HREF_RE.search(url)
        if match is not None:
            listing.external_id = match.group(1)
        # Owners publish their own contact details in the exposé; the Amt does
        # not broker the sale and there is no Chiffre intermediary.
        listing.contact_kind = "private"
        listing.http_status = response.status_code
        return listing
```

In `src/hofradar/sources/adapters/__init__.py`, import `DenkmalboerseAdapter` and add `"denkmalboerse": DenkmalboerseAdapter` to the `ADAPTERS` mapping.

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/sources/adapters/test_denkmalboerse.py -v`
Expected: PASS. If the fixture's markup does not yield a title through `raw_listing_from_html`, adjust the selectors in `_htmlutil.py`'s call — not by adding parsing to this adapter.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/hofradar/sources/adapters/denkmalboerse.py src/hofradar/sources/adapters/__init__.py tests/sources
git commit -m "feat(sources): add the BLfD Denkmalbörse adapter"
```

---

## Task 7: Registry entry and per-source in-radius yield (closes B1)

Premortem B1, the most likely failure: four objects Bavaria-wide, zero in radius, and nobody noticed because the adapter "worked" — the suite proved parsing, not yield. Five further weeks then went into scoring machinery for a corpus that could not be ranked. This task makes yield a number you see every run.

**The go/no-go, written down before the data:** if the Denkmalbörse produces **fewer than 5 in-radius objects** across its first four weekly runs, the follow-on work (market_tempo, the Denkmal cost branch, the Denkmal-Atlas enrichment) does not start. Decide it now, not after seeing a number you want to rationalise.

**Files:**
- Modify: `config/sources.yaml` + `src/hofradar/_config_defaults/sources.yaml`
- Create: `src/hofradar/report/yield_stats.py`
- Create: `tests/report/test_yield_stats.py`
- Modify: `docs/SOURCES.md`, `docs/DECISIONS.md`

**Interfaces:**
- Consumes: `Observation`, `PropertySource`, `Property.distance_air_km`, `Source.key`.
- Produces: `source_yield(session, *, since) -> list[SourceYield]` where `SourceYield` is a dataclass with `source_key: str`, `observed: int`, `in_radius: int`. Plan B Task 3 extends the same module with per-Gemeinde coverage.

- [ ] **Step 1: Write the failing test**

Create `tests/report/test_yield_stats.py`:

```python
"""How many in-radius properties did each source actually produce?

The number this answers is "was this source worth building", which no test of
parsing can answer. A source that parses perfectly and yields nothing inside
the radius is a source that should not have been built.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.report.yield_stats import source_yield
from tests.report.conftest import make_observation, make_property, make_source  # existing helpers


def test_counts_in_radius_separately_from_observed(session) -> None:
    source = make_source(session, key="denkmalboerse")
    near = make_property(session, source=source, distance_air_km=12.0)
    far = make_property(session, source=source, distance_air_km=210.0)
    unknown = make_property(session, source=source, distance_air_km=None)
    for prop in (near, far, unknown):
        make_observation(session, property=prop, source=source)
    session.flush()

    rows = source_yield(session, since=datetime.now(UTC) - timedelta(days=28))

    assert len(rows) == 1
    assert rows[0].source_key == "denkmalboerse"
    assert rows[0].observed == 3
    # Unknown distance is not counted as in-radius: we did not prove it.
    assert rows[0].in_radius == 1


def test_a_source_with_no_observations_reports_zero(session) -> None:
    make_source(session, key="denkmalboerse")
    session.flush()

    rows = source_yield(session, since=datetime.now(UTC) - timedelta(days=28))

    assert rows == [] or rows[0].observed == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/report/test_yield_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hofradar.report.yield_stats'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/hofradar/report/yield_stats.py`:

```python
"""Per-source yield: the number that says whether a source was worth building.

A source can parse flawlessly and still be useless, because parsing is not
yield. This module answers the only question that matters about a new
adapter - how many properties inside the radius did it actually produce - and
puts it in the weekly report where it cannot be avoided.

An unknown air distance is deliberately not counted as in-radius. We did not
prove the property is near; treating unknown as near would be the same mistake
as letting air distance stand in for road distance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from hofradar.db.models import Observation, Property, Source

if TYPE_CHECKING:  # pragma: no cover
    from datetime import datetime

    from sqlalchemy.orm import Session

#: Air kilometres inside which a property counts towards a source's yield. Air
#: rather than road because the question is "did this source find things near
#: us", not "how far is the drive" - and most rows are never routed.
YIELD_RADIUS_AIR_KM = 60.0


@dataclass(slots=True)
class SourceYield:
    source_key: str
    observed: int
    in_radius: int


def source_yield(session: Session, *, since: datetime) -> list[SourceYield]:
    """Distinct properties each source observed since ``since``, and how many were near."""
    in_radius = func.count(
        func.distinct(
            func.nullif(
                func.coalesce(
                    Property.id * (Property.distance_air_km <= YIELD_RADIUS_AIR_KM), 0
                ),
                0,
            )
        )
    )
    rows = session.execute(
        select(
            Source.key,
            func.count(func.distinct(Property.id)),
            in_radius,
        )
        .join(Observation, Observation.source_id == Source.id)
        .join(Property, Property.id == Observation.property_id)
        .where(Observation.scraped_at >= since)
        .group_by(Source.key)
        .order_by(Source.key)
    ).all()
    return [SourceYield(source_key=key, observed=obs, in_radius=near) for key, obs, near in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/report/test_yield_stats.py -v`
Expected: PASS

- [ ] **Step 5: Put the number in the weekly report**

In `src/hofradar/report/render.py`, add a section to both the Markdown and HTML renderers, fed by `source_yield(session, since=report_window_start)`:

```markdown
## Quellen-Ausbeute (letzte 4 Wochen)

| Quelle | Objekte gesehen | davon im Radius |
|---|---|---|
| denkmalboerse | 41 | 3 |
```

Render `in_radius` as its own column, never folded into the total — a source's total is not its yield.

- [ ] **Step 6: Add the registry entry**

In **both** `config/sources.yaml` and `src/hofradar/_config_defaults/sources.yaml`, after `zvg_bayern`:

```yaml
  - key: denkmalboerse
    name: "BLfD Denkmalbörse"
    role: primary
    adapter: denkmalboerse
    base_url: "https://www.blfd.bayern.de"
    region: Bayern
    reliability: 0.8
    enabled: true
    rate_limit_seconds: 4.0
    terms_checked_at: 2026-09-03   # replace with the day you ran Task 6 Step 1
    terms_excerpt: >
      PASTE THE ACTUAL robots.txt AND TERMS FINDING FROM TASK 6 STEP 1 HERE.
      If either restricts automated retrieval, set enabled: false instead.
    notes: >
      A state authority's listing exchange for Baudenkmäler. role=primary
      because the exposé is the origin of the advert, not a copy - it is
      withdrawn when the owner withdraws it, so its silence is the seller's own
      signal. reliability 0.8 rather than 0.95 because BLfD publishes owner
      statements and explicitly disclaims their accuracy: that is a fact about
      the data, which reliability models, not about provenance, which role does.
```

Then: `python scripts/sync_config_defaults.py`

- [ ] **Step 7: Verify end to end against the fixture, not the network**

Run: `python -m pytest -q && ruff check src tests`
Expected: all pass, including the config-drift check.

- [ ] **Step 8: Document the source and the trap**

In `docs/SOURCES.md`, add `denkmalboerse` to the "What ships enabled" table, and add:

```markdown
## What looks relevant and is not

`Deutsche Denkmalbörse`, `Capital & Denkmal` and the rest of the Denkmal-AfA
industry sell converted Altbau *apartments* to capital investors — Leipzig,
Berlin, Potsdam — marketed hard and already discovered. Despite the name they
are the opposite of a hidden Hofstelle. Do not add them.

`Deutsche Stiftung Denkmalschutz` is a funder and publisher, not a marketplace.
```

Append to `docs/DECISIONS.md`:

```markdown
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
that, the dependent scoring and cost work does not start.
```

- [ ] **Step 9: Commit**

```bash
git add config/sources.yaml src/hofradar/_config_defaults/sources.yaml src/hofradar/report tests/report docs/SOURCES.md docs/DECISIONS.md
git commit -m "feat(report): per-source in-radius yield, and enable the Denkmalbörse"
```

---

## Explicit non-goals

Deliberately **not** in this plan, and why:

- **`market_tempo` / per-class freshness bands.** Gated on Task 7's yield number. Building band tables before you know the real `online_days` distribution of Denkmalbörse objects means guessing the numbers. Slice 2.
- **The Denkmal cost branch** (rate multiplier + approval additive). Same gate. Task 4 makes the *existing* gate safe for this property class, which is the part that cannot wait.
- **Anything tax.** No `tax_relief_basis`, no after-tax view. §7i (let) and §10f (owner-occupied) are different statutes and both need a `Bescheinigung` that does not exist at listing time; the value depends on a marginal rate that is a fact about the buyer, not the building.
- **The Denkmal-Atlas enrichment.** It is the prerequisite for `market_tempo=SLOW` (the loose `_MONUMENT_RE = r"denkmal"` cannot carry a scoring exemption), so it comes with slice 2, not before it.
- **The `authority_channel` hidden signal.** The existing vocabulary already fires ~23–33 points on these listings via `direct_owner_contact`, `kein_makler`, `preis_auf_anfrage` and `long_online`. Adding a signal before seeing real breakdowns is premature.

---

## Self-Review

**Spec coverage.** B1 → Task 7 (yield instrumentation + written go/no-go). B2 → Task 3 (absence guards). B4 → Task 1 (Alembic + backup). B7 → Task 2 (terms gate) and Task 6 Step 1. B9 → Task 4 (inferred-cost flag). B5, B6, B8 are out of scope here by design: B5 and B6 belong to the deferred `market_tempo` work, B8 to Plan B — both are named in the non-goals above rather than left silent.

**Placeholder scan.** One intentional placeholder remains: the `terms_excerpt` in Task 7 Step 6, which cannot be written until Task 6 Step 1 is run from a networked machine. It is marked in caps and its task step says explicitly what to do if the terms are unfavourable. Every other step carries real code.

**Type consistency.** `renovation_evidence` returns `str` in Task 4 Step 3 and is compared against the literal `"observed"` in Step 7 — consistent. `town_in_radius` returns `bool | None` in Task 5 and is tested with `is False` in Task 6, which is why `None` correctly falls through. `ImplausibleAbsence` is defined in Task 3 Step 3 and imported in Task 3 Step 1's test from `hofradar.lifecycle` — the export is added in the same step. `SourceYield` fields (`source_key`, `observed`, `in_radius`) match between Task 7's test and implementation.

**One known risk carried forward.** Task 3's `IMPLAUSIBLE_ABSENCE_FRACTION = 0.30` will fire on the OVB source in Plan B, where a 14-day ad cycle legitimately expires a large fraction at once. Plan B Task 1 resolves this with `listing_ttl_days`, and until it exists the two must not run together.
