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

## Status of this document

**The Landkreis assignments above are the plan's best reconstruction, not a
verified survey.** They were compiled from general knowledge of Bavarian
newspaper-group territories (OVB vs. Ippen/Münchner Merkur) and of which
Landkreis each Gemeinde administratively belongs to. Nobody has checked this
table against any of the thirteen municipalities' own websites, their
`Amtsblatt`/`Mitteilungsblatt` pages, or the newspapers' own coverage-area
statements. Treat every row as a hypothesis the report is built on top of, not
a fact.

`config/search.yaml`'s `coverage.municipalities` list (which
`hofradar.report.yield_stats.coverage_by_municipality` reads) mirrors the
"Gemeinde" column above, with one deliberate exception: this table keeps the
plan's original abbreviated spelling **"Wasserburg a. Inn"** verbatim, but
`config/search.yaml` spells it **"Wasserburg am Inn"** — the canonical form
`hofradar.geo.gazetteer` and `hofradar.normalize.location.parse_location`
actually produce and store on `Property.town`/`Observation.town`. Because
`coverage_by_municipality`'s `Property.town.in_(expected)` match is exact, the
abbreviated form as written in this table would never match a single real
observation and Wasserburg would read as falsely dark on day one, in the one
Lkr.-Rosenheim town that is definitely covered. If this table is ever
"corrected" back to match `search.yaml` verbatim, use the *canonical* spelling
("Wasserburg am Inn"), not the abbreviated one — the abbreviated form here is
a leftover from the plan text, not a second valid spelling to preserve.

For every other row, mismatch risk is still real. If a source ever stores a
town name spelled differently than the config list (a different umlaut
folding, a missing hyphen, a spelled-out form the gazetteer does not use),
that municipality will read as a false "dark" — see `docs/MODULE_API.md` and
the module docstring for the exact-match caveat this creates. Because the
match is exact rather than fuzzy, this can only ever produce a false *dark*,
never a false *covered*: a spelling mismatch makes a covered town look
uncovered, it cannot make an uncovered one look covered. That asymmetry is why
declining to fuzzy-match was the right call — the failure mode this task
exists to prevent (a truly dark municipality reading as fine) stays
structurally unreachable.

A second, larger source of the same false-dark risk: nothing here maps an
**Ortsteil** (a village/hamlet that is part of a larger Gemeinde, not its own
municipality) to the Gemeinde it belongs to. A listing whose location string
resolves to the Ortsteil rather than the Gemeinde will never match this list,
however faithfully the Gemeinde spelling above is kept. The project's own
search centre makes this vivid: it is `"Westham, Feldkirchen-Westerham"` —
**Westham** is an Ortsteil of Feldkirchen-Westerham, not a Gemeinde in its own
right, and a listing that stores just "Westham" (as the origin naturally
would) will not match `Feldkirchen-Westerham` in `expected`. The same applies
to Vagen and Höhenrain (also Feldkirchen-Westerham) and to Götting (an
Ortsteil of Bruckmühl). No Ortsteil→Gemeinde mapping exists in this codebase
today; a reader chasing a "dark" Feldkirchen-Westerham or Bruckmühl should
check for this before concluding the publisher gap is real. This errs on the
loud side — a phantom gap gets investigated, not a real one missed — so it is
noted here rather than fixed.

Left deliberately unfilled, for the same reason: `options.bulletins` index
URLs for the Gemeindeblatt/Amtsblatt adapter, broker RSS/JSON feeds, and
sitemap URLs for each of the covered and dark municipalities. This container's
egress proxy returns a 403 CONNECT denial for Bavarian municipality sites and
for `ovbimmo.de` (see `shared-context.md`, Ruling 1) — that is an
organization-level policy decision, not a transient failure, so no URL was
guessed or fabricated to fill the gap.

### What a networked human still needs to do

1. For each Gemeinde in the table, open its own website and confirm the
   Landkreis, the publisher named for its official notices, and whether it
   runs an `Amtsblatt`/`Mitteilungsblatt` with a stable bulletin index.
2. Record the actual bulletin index URL (and, where one exists, an RSS/Atom
   feed or downloadable-PDF index) for `options.bulletins` on the
   `gemeindeblatt_pdf` source.
3. Confirm directly with OVB-Heimatzeitungen and with the Ippen/Münchner
   Merkur group (Miesbacher Merkur, Ebersberger Zeitung) which of these
   thirteen Gemeinden their online portals actually list classifieds for, and
   note any paywall or robots.txt restriction found along the way.
4. Check for a broker (Makler) operating in the Miesbach/Ebersberg dark rows
   who publishes a machine-readable feed, since no portal covers them today.
5. Once confirmed, correct this table (and `config/search.yaml`'s
   `coverage.municipalities`, then re-run `python scripts/sync_config_defaults.py`)
   rather than treating the current version as settled.
