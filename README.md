# 🌾 Hofradar

A research platform with **memory** for finding Hofstellen, Sacherl and Resthöfe
in Upper Bavaria — not a scraper.

**[Look at it →](https://danm3rcurius.github.io/real-estate-tracker/)** — a
static snapshot of the UI. The farmsteads in it are **invented**; the tool has
no public instance, because a static host cannot run its password gate. See
[docs/DEPLOY.md](docs/DEPLOY.md#the-public-snapshot-on-github-pages).

The difference matters. A scraper searches again every week and shows you the
same twelve farms it showed you last week. Hofradar remembers every property it
has ever seen, so it can tell you the only things that are actually new:

> *„Dieses Sacherl kennen wir seit Februar. Heute ist der Preis von 690.000 €
> auf 595.000 € gefallen."*

## The two knobs

Everything is tunable, but two parameters are the product, and they are live
sliders on the main screen:

| Slider | What it really controls |
|---|---|
| **Entfernung** | The air-line radius from your search centre. The driving-distance limits derive from it (×1.25 soft, ×1.45 hard) — and air distance is *never* allowed to stand in for road distance. |
| **Gesamtbudget** | All-in capital: purchase + Bavarian side costs + renovation. The purchase-price bands derive from it, so moving one number keeps the whole model coherent. |

Move a slider and every score is recomputed against the facts already in the
database. No re-crawl, no lost history: scores live in their own table keyed by
a hash of the search profile.

## Quickstart

Docker is optional — this is a plain Python project and the virtualenv route is
the simpler one for local use:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,pdf,images]"
hofradar init-db
hofradar serve                # → http://localhost:8000
```

or, if you would rather not manage the environment:

```bash
docker compose up -d          # → http://localhost:8000
```

Nothing else is required. No API keys, no database server, no npm.

Want the demo data rather than an empty database?

```bash
hofradar seed-demo            # twelve invented farmsteads, no network calls
```

> **`does not appear to be a Python project`?** You are on a branch that does
> not have the code yet. `git fetch origin && git checkout <branch>`, then
> re-run the install.

## Before you put it on a public URL

There is one password, and it is off by default so that localhost stays
frictionless:

```bash
hofradar hash-password        # prompts, prints a pbkdf2_sha256$… string
```

Put that in `HOFRADAR_PASSWORD_HASH` and restart. Signed `HttpOnly` session
cookie, constant-time comparison, lockout after 8 failed attempts, `/api/*`
answers `401` rather than an HTML page. With no password set the app logs a
warning at startup telling you it is open.

See [docs/DEPLOY.md](docs/DEPLOY.md) for hosting — including why Vercel is the
wrong shape for this (no persistent disk, no long-running crawl, no real
scheduler) and which options work from a browser alone.

## What it does, in order

```
DISCOVERY → VERIFICATION → SCORING → CHANGE DETECTION → WEEKLY SHORTLIST
```

Expensive work happens last, on the smallest set. Crawlers may touch thousands
of pages; the language model sees at most a hundred candidates that already
survived deduplication, the geographic filter and availability verification.

## The rules that make it trustworthy

These are enforced in code and covered by tests, because each one is a mistake
that a naive search makes every single week:

1. **Never NEW twice.** A property that has ever been in the database is
   reported as `REACTIVATED` / `PRICE_CHANGE` / known — never as new.
2. **Air ≠ road.** `distance_air_km` and `distance_driving_km` are separate
   columns. 79 km straight line and 134 km by road is *not* within 80 km.
3. **An aggregator may discover, never confirm.** A discovery source cannot set
   `verified`, cannot set freshness, and its silence cannot mark a listing gone.
4. **Total cost, not asking price.** 700k purchase + 1M renovation is
   `capital_risk = EXTREME`, not a top hit.
5. **Evidence first.** Every load-bearing fact carries `{source, url, quote,
   confidence}`. The dossier page can always answer *why does the system believe
   this?*
6. **Five listings, one farm.** Deduplication by fingerprint, geography, area,
   text similarity and perceptual image hashes — never by URL alone, and never
   on a title match alone.
7. **The LLM may not change a number.** It reads prose and flags risks. Prices,
   areas and distances come from the deterministic stages.

## Commands

```bash
hofradar init-db                        # create tables, register sources
hofradar serve                          # web UI
hofradar run                            # one full pipeline run
hofradar run --sources zvg_bayern       # just one source
hofradar rescore --air-km 40 --budget 800000   # what would the sliders do?
hofradar report --out reports/weekly/kw35.md
hofradar import listings.csv
hofradar config                         # show the resolved profile
hofradar hash-password                  # password hash for HOFRADAR_PASSWORD_HASH
```

## Configuration

| File | Contents |
|---|---|
| `config/search.yaml` | The search DNA: centre, radius, budget, land, property types, exclusions |
| `config/scoring.yaml` | Ranking weights, hard gates, the renovation cost model |
| `config/keywords.yaml` | The vocabulary — including the Bavarian regionalisms and the "hidden listing" phrases |
| `config/sources.yaml` | The source registry and which sources are enabled |

Everything in `search.yaml` and `scoring.yaml` is also editable in the web UI
under **Einstellungen** and saved as a named profile.

## Sources

Enabled by default: manual paste-in, CSV import, the official ZVG foreclosure
register, and generic RSS/sitemap adapters for regional brokers.

The big portals (Kleinanzeigen, ImmobilienScout24, Immowelt) ship **disabled**.
Their terms restrict automated access and they are actively bot-defended. See
[docs/SOURCES.md](docs/SOURCES.md) for what that means for you and how to add
your own regional feeds — which is where the hidden listings actually are.

## Docs

- [docs/DECISIONS.md](docs/DECISIONS.md) — the load-bearing architecture calls and why
- [docs/DEPLOY.md](docs/DEPLOY.md) — hosting, the password gate, backups
- [docs/SOURCES.md](docs/SOURCES.md) — source strategy, legality, adding your own
- [docs/MODULE_API.md](docs/MODULE_API.md) — the internal package contract

## Licence

MIT. See [LICENSE](LICENSE).
