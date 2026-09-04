# Coverage map

Origin: Westham, Feldkirchen-Westerham (Lkr. Rosenheim).

Coverage is a property of the *publisher*, not of the radius. OVB-Heimatzeitungen
covers Stadt and Lkr. Rosenheim, Lkr. Mühldorf and western Lkr. Traunstein.
Lkr. Miesbach (Miesbacher Merkur) and Lkr. Ebersberg (Ebersberger Zeitung)
belong to the Ippen/Münchner Merkur group and are **not** covered by OVB.

**Update, 2026-09-03 — the newspaper and the portal are two different
channels, and this table used to conflate them.** "OVB" above is the
*newspaper* — the print titles and their Amtsblatt/classifieds arrangement.
**OVBimmo.de, the *portal*, is a different thing**: it aggregates listings
from brokers (Makler) directly, independent of which newspaper covers a
given Landkreis, because a broker's listing follows the broker, not a
publisher boundary. Verified live 2026-09-03 via `ovbimmo.de`'s per-Landkreis
Atom feed (`generic_rss`'s `options.feeds`, see `config/sources.yaml`):
`de.miesbach` returns **14** entries and `de.ebersberg` returns **14**
entries — nonzero, so the *portal* channel is not dark in Lkr. Miesbach or
Lkr. Ebersberg the way the newspaper channel is. Read this narrowly, though:

- 14 listings for an entire Landkreis is thin (`de.rosenheim-kreis` returns
  100 for comparison), and **none of the 14 has been read or verified to be
  a farmstead** — this only establishes that the channel returns something,
  not that it returns something useful.
- The feed is scoped to the whole Landkreis, not to an individual Gemeinde.
  Nothing here says any of those 14 listings is actually in Irschenberg,
  Weyarn, Valley, Holzkirchen or Otterfing specifically, as opposed to
  Miesbach town or another Gemeinde in the same Landkreis — that would need
  reading each entry's `cm:locality`, which has not been done.
  Amtsblatt-per-Gemeinde is still the only source with municipality-level
  precision for these five.
- **Aying is in Landkreis München, not Miesbach or Ebersberg.** Neither
  configured feed (`de.miesbach`, `de.ebersberg`) says anything about it;
  its portal status is simply unaddressed by this update, not confirmed
  either way.

So: the Amtsblatt/newspaper channel really is dark in Miesbach and Ebersberg
(and unaddressed for Aying) — that finding stands. What no longer stands is
any wording implying those Gemeinden are unreachable by *any* configured
source: the portal channel reaches into two of the three affected
Landkreise, thinly, at Landkreis rather than Gemeinde granularity, unverified
for content.

| Gemeinde | Landkreis | Zeitung / Verlag | Amtsblatt (newspaper) | OVBimmo-Portal (Atom, Lkr.-level, 2026-09-03) | Status |
|---|---|---|---|---|---|
| Feldkirchen-Westerham | RO | OVB (Mangfall-Bote) | yes | 100 (`de.rosenheim-kreis`) | covered |
| Bruckmühl | RO | OVB (Mangfall-Bote) | yes | 100 (`de.rosenheim-kreis`) | covered |
| Bad Aibling | RO | OVB (Mangfall-Bote) | yes | 100 (`de.rosenheim-kreis`) | covered |
| Kolbermoor | RO | OVB | yes | 100 (`de.rosenheim-kreis`) | covered |
| Rosenheim | RO | OVB | yes | 100 (`de.rosenheim-kreis`) | covered |
| Wasserburg am Inn | RO | OVB (Wasserburger Ztg.) | yes | 100 (`de.rosenheim-kreis`) | covered |
| Irschenberg | MB | Miesbacher Merkur (Ippen) | **dark** | 14 (`de.miesbach`, Lkr.-wide, not Gemeinde-attributed) | newspaper dark, portal thin |
| Weyarn | MB | Miesbacher Merkur (Ippen) | **dark** | 14 (`de.miesbach`, Lkr.-wide, not Gemeinde-attributed) | newspaper dark, portal thin |
| Valley | MB | Miesbacher Merkur (Ippen) | **dark** | 14 (`de.miesbach`, Lkr.-wide, not Gemeinde-attributed) | newspaper dark, portal thin |
| Holzkirchen | MB | Miesbacher Merkur (Ippen) | **dark** | 14 (`de.miesbach`, Lkr.-wide, not Gemeinde-attributed) | newspaper dark, portal thin |
| Otterfing | MB | Miesbacher Merkur (Ippen) | **dark** | 14 (`de.miesbach`, Lkr.-wide, not Gemeinde-attributed) | newspaper dark, portal thin |
| Aying | M | Merkur (Ippen) | **dark** | not queried (Lkr. München, no feed configured) | **dark — unaddressed** |
| Glonn | EBE | Ebersberger Ztg. (Ippen) | **dark** | 14 (`de.ebersberg`, Lkr.-wide, not Gemeinde-attributed) | newspaper dark, portal thin |

The Amtsblatt/Gemeindeblatt adapter still matters more than the portal for
every "newspaper dark, portal thin" row: it is the only source with
Gemeinde-level precision there, and the only one that would ever surface a
private Chiffre seller rather than a broker listing. Aying stays the clearest
case for why the Gemeindeblatt adapter is not optional: no portal feed
addresses it at all today.

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
"Gemeinde" column above exactly, including **"Wasserburg am Inn"**. That
spelling is not cosmetic: it is the canonical form `hofradar.geo.gazetteer`
and `hofradar.normalize.location.parse_location` produce and store on
`Property.town`/`Observation.town`, and `coverage_by_municipality`'s
`Property.town.in_(expected)` match is exact. An earlier version of this
table carried the plan's abbreviated **"Wasserburg a. Inn"** — which matches
no real observation, so Wasserburg would have read as falsely dark on day
one, in the one Lkr.-Rosenheim town that is definitely covered. Never
reintroduce the abbreviated form: it is a leftover from the plan text, not a
second valid spelling.

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

**Note on the development sandbox, dated 2026-09-03 — this is a statement
about this container's egress policy at this point in time, not a claim
about whether the municipalities themselves are reachable from a normal
network.** Ruling 1 (`shared-context.md`) said the sandbox's egress proxy
denied `ovbimmo.de`, `www.blfd.bayern.de` and Bavarian municipality sites
alike; that has partly changed. As of this date, the same proxy allows
`ovbimmo.de` and `www.blfd.bayern.de` — which is what made the Atom-feed
check above possible — **but still returns a 403 CONNECT denial for
Bavarian municipality domains**: re-tested this session against
`feldkirchen-westerham.de`, `bruckmuehl.de`, `weyarn.de` and `holzkirchen.de`,
all four denied by the proxy. So, from *this development sandbox, on this
date*, `options.bulletins` index URLs for the Gemeindeblatt/Amtsblatt adapter
could not be filled in, and none was guessed or fabricated to fill the gap —
see the `gemeindeblatt_pdf` entry's `notes:` in `config/sources.yaml` for the
precise per-Gemeinde checklist left for whoever next runs this from a machine
with ordinary internet access. Broker RSS/JSON feeds and sitemap URLs for
individual local Makler are also still unfilled, for the same reason: the
directory task (`https://ovbimmo.de/anbieter`) that would enumerate them was
not run this session — only the aggregate Atom feeds were. This paragraph
describes a sandbox limitation on one date, not a property of any
municipality's actual web presence, and should be read (and eventually
deleted) as such once someone runs the checklist from a normal connection.

### What a networked human still needs to do

1. For each Gemeinde in the table, open its own website and confirm the
   Landkreis, the publisher named for its official notices, and whether it
   runs an `Amtsblatt`/`Mitteilungsblatt` with a stable bulletin index.
2. Record the actual bulletin index URL for `options.bulletins` on the
   `gemeindeblatt_pdf` source — see the checklist in that source's `notes:`
   in `config/sources.yaml`, dark rows first.
3. ~~Confirm which of these thirteen Gemeinden OVBimmo's portal actually
   lists.~~ Partly done 2026-09-03: `de.miesbach` and `de.ebersberg` return
   14 listings each (Landkreis-wide, not Gemeinde-attributed, none verified
   as a farmstead) — see the table above. Still open: reading `cm:locality`
   on those 28 entries to see which Gemeinde each actually falls in, and the
   same check for **Lkr. München** (Aying), which no configured feed covers.
   The Ippen/Münchner Merkur group's own portal (if one exists) was not
   checked at all.
4. Open `https://ovbimmo.de/anbieter` (OVB's broker directory) and check each
   broker inside the radius for their own `/feed`, `/rss` or `/sitemap.xml` —
   this seeds `generic_rss`'s and `generic_sitemap`'s `options` with
   individual Makler, on top of the aggregate feeds already configured. Not
   done this session.
5. Once confirmed, correct this table (and `config/search.yaml`'s
   `coverage.municipalities`, then re-run `python scripts/sync_config_defaults.py`)
   rather than treating the current version as settled.
