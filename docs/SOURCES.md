# Source strategy

## The honest situation

The listings you most want — a Sacherl sold privately, an Austragshaus in a
Gemeindeblatt, a Hofstelle going through Zwangsversteigerung — are mostly *not*
on the big portals. The big portals are where the expensive, well-marketed,
already-discovered objects are.

That is convenient, because the big portals are also the ones that do not want
to be crawled.

## Three classes

| Role | May discover | May confirm a listing is live | May set freshness | Silence means "gone" |
|---|---|---|---|---|
| `primary` | ✅ | ✅ | ✅ | ✅ |
| `local` | ✅ | ✅ | ✅ | ✅ |
| `discovery` | ✅ | ❌ | ❌ | ❌ |

A `discovery` source can put a property into the database, where it sits as
`verification_status = unverified` until a primary source is found for it. It
can never make something look current.

## What ships enabled

| Source | Why |
|---|---|
| `manual` | Paste a URL or a whole exposé into the web UI. Never blocked. The fastest path to value on day one. |
| `csv_import` | Bulk-load anything you already have in a spreadsheet. |
| `zvg_bayern` | The official public foreclosure register. High signal, public by design, and exactly the kind of listing a normal search misses. |
| `generic_rss` | Regional brokers who publish a feed. Add their URLs to `options.feeds`. |
| `generic_sitemap` | Small broker sites with a `sitemap.xml`. Polite, capped, robots-respecting. |

`gemeindeblatt_pdf` ships enabled-capable but empty: it needs a list of your
municipalities' bulletin index pages in `options.bulletins`. This is where
Chiffre ads live, and every hit is stored with page-level evidence
("Gemeindeblatt Feldkirchen-Westerham, KW 34, Seite 17"). Requires `pip install
-e ".[pdf]"`.

## What ships disabled, and what that means

`kleinanzeigen`, `immobilienscout24`, `immowelt`.

The adapters are implemented and tested against fixture HTML. They are off
because:

- their terms of service restrict automated access;
- they run active bot defences, so an unattended crawl from a server IP will be
  blocked quickly and permanently;
- **no evasion is implemented and none will be**: no CAPTCHA solving, no proxy
  rotation, no browser-fingerprint spoofing. If a request is blocked, the
  adapter records the failure and stops cleanly.

If you decide to enable one for personal, low-rate use, do it from your own
machine and IP, leave the conservative `rate_limit_seconds` alone, and read the
site's terms first. That is your call to make, not the software's.

The better long-term answer for portal coverage is their official partner/API
access, or simply pasting the handful of listings you actually care about into
the paste box — which is one click and produces a fully-tracked property with
full history.

## Denkmalbörse (BLfD): terms check complete, source enabled

`hofradar.sources.adapters.denkmalboerse.DenkmalboerseAdapter` is written and
tested against a fixture (see the note on that fixture below). `denkmalboerse`
is enabled in `config/sources.yaml` (`enabled: true`, `terms_checked_at:
2026-09-03`) - the registry entry's `terms_excerpt` carries the same finding
recorded here.

Terms check status: **DONE (2026-09-03)**. A human on a networked machine ran:

```bash
curl -s https://www.blfd.bayern.de/robots.txt
curl -s https://www.blfd.bayern.de/blfd/impressum/index.html
```

The first got back **`HTTP/1.1 404 Not Found` (Server: CERN httpd)** - no
`robots.txt` exists on the host, so there are no crawl directives to honour or
violate. The second returned the Impressum, which carries a
"Nutzungsbedingungen" section. Its operative sentence:

> "Als Privatperson dürfen Sie urheberrechtlich geschütztes Material zum
> privaten und sonstigen eigenen Gebrauch im Rahmen des § 53
> Urheberrechtsgesetz (UrhG) verwenden. Eine Vervielfältigung oder Verwendung
> dieser Seiten oder Teilen davon in anderen elektronischen oder gedruckten
> Publikationen und deren Veröffentlichung ist nur mit unserer Einwilligung
> gestattet."

Private and own use is explicitly permitted under § 53 UrhG. Republication -
reproducing these pages or parts of them in other electronic or printed
publications - requires BLfD's consent. **Neither the robots.txt (which does
not exist) nor the Impressum's Nutzungsbedingungen restricts automated
retrieval, crawling or machine access** - that is a claim about the two pages
actually read, not about the whole site; a separate Datenschutz or
Nutzungsordnung page was not fetched and could carry something different. A
separate "Haftungsausschluss" section disclaims any warranty of the accuracy,
completeness or currency of the published information.

This private-use reading holds **only while the web UI's password gate is
configured** (`HOFRADAR_PASSWORD` / `HOFRADAR_PASSWORD_HASH` - see
`src/hofradar/web/app.py`, invariant 8). The gate is opt-in: with no password
set, the auth middleware is not installed at all and the UI is open to
anyone who can reach it. Running it that way, while Denkmalbörse listings
flow through it, serves BLfD material to the public with no household
boundary left standing - which is republication in substance, breaching the
very clause this reading depends on. Deploying without the password gate
configured is outside the permitted use BLfD grants; it is not this
document's or the registry's call to make on an operator's behalf, so it is
stated here as a hard condition, not a default assumption. That boundary
(permitted: private use behind the password gate; not permitted:
republication, or an unauthenticated deployment that amounts to the same
thing) is recorded in the registry entry's `notes:` as well, so it stays
visible next to the config that could violate it.

Evidence gathered from a networked machine on 2026-09-03, which does **not**
by itself close the terms check above:

- `https://www.blfd.bayern.de/information-service/denkmalboerse/objekte/005816/index.html`
  returns HTTP 200, served by a `CERN httpd` static server.
- The real page carries `<div class="immo-inhalt">` and an "Eigentümer des
  Anwesens" block with a direct `mailto:` link - confirming
  `contact_kind="private"` is the right reading for this source.
- An Exposé PDF is published under
  `/mam/information_und_service/denkmal_boerse/oberbayern/`.

`tests/fixtures/html/denkmalboerse_object_005816.html` is now a real page
captured from `www.blfd.bayern.de` on 2026-09-03 (see the provenance comment
at the top of the fixture and `tests/sources/adapters/test_denkmalboerse.py`).

**The search CGI's response shape is now verified.** A real capture of
`/cgi-bin/fts_search_verkauf.pl` on 2026-09-03
(`tests/fixtures/html/denkmalboerse_search_cgi.html`, 300 KB, HTTP 200) shows
one response holds the entire catalogue: **237 rows, 237 unique object ids,
no pagination** ("Weiter" appears once on the page, in unrelated site
navigation, never as a paginator). Each row is a `<tr>` with the object's
title/link, its `PLZ Ort` address, and a final `<td>` naming its
Regierungsbezirk. Distribution across the seven: Oberbayern 43, Unterfranken
45, Mittelfranken 38, Oberfranken 34, Schwaben 28, Niederbayern 27, Oberpfalz
22.

Because the shape is now known, `discover()` row-scans this table (rather than
scanning every anchor on the page) and, per invariant 4b, leaves
`enumeration_complete` true only when the walk actually succeeded: a non-200
response, a parse that yields zero object rows (indistinguishable from a
template change), or any detail fetch that failed all call
`mark_enumeration_incomplete` with a specific reason instead of the old
unconditional call. A healthy walk over the real fixture now genuinely leaves
`can_prove_absence` true.

**Regierungsbezirk is now the primary pre-filter, checked before the
gazetteer.** `discover()` reads the row's last `<td>` and skips the detail
fetch for any row whose Bezirk is recognised as one of Bavaria's seven and is
not in the configured in-scope set (`options.regierungsbezirke`) - an empty,
missing, or unrecognised Bezirk value still falls through and fetches, the
same rule the gazetteer pre-filter already follows for an unknown town.
Matching is case-insensitive (an operator typo like `"oberbayern"` must still
line up with the row value `"Oberbayern"`), and a configured value that
matches none of the seven at all - not a case variant, just wrong - falls
back to the default with a logged warning rather than silently fetching
nothing. An explicit empty list (`regierungsbezirke: []`) *is* honoured as
"nothing in scope"; only the key being absent entirely means "use the
default". The gazetteer stays in place as a second, narrower gate for the
in-Bezirk towns it does recognise.

The default in-scope set is **`Oberbayern`, `Niederbayern`, `Schwaben`** -
not Oberbayern alone. It is tied to the default profile's `air_km_max`
(80 km), not to "near the origin": by this project's own `haversine_km`,
Landshut (Niederbayern) is 73.8 km from the profile origin and Landsberg am
Lech (Schwaben) is 73.1 km, both inside that radius. An operator who raises
`air_km_max` well past 80 km should widen `options.regierungsbezirke` to
match - the constant does not derive itself from the profile at runtime. On
the real 2026-09-03 capture that default set covers 98 of the 237 rows
(Oberbayern 43, Niederbayern 27, Schwaben 28); the Bezirk column skips the
other **139** with certainty, before any gazetteer lookup runs. The gazetteer
pre-filter alone would have skipped zero of those 98 - of the towns it
recognises inside the default set, none are known-and-outside the radius.

**Decision 14's yield gate is met, on a real snapshot, by a wide margin.**
Decision 14 requires 5 in-radius objects across the Denkmalbörse's first four
runs. Scoring the 43 Oberbayern rows from the 2026-09-03 capture through this
project's own gazetteer and `haversine_km`, against the real search profile
(origin 47.907, 11.84; `air_km_max` 80.0 km), yields **11 in-radius objects**:
Kirchseeon (20.6 km), Rosenheim (22.3 km), Vaterstetten (23.1 km), Rohrdorf
(27.8 km), Prien am Chiemsee (38.1 km), Kiefersfelden (42.2 km),
Fürstenfeldbruck (~52.9 km), Reit im Winkl (53.2 km), Mühldorf am Inn
(63.4 km, two distinct objects - 008637 and 007505), and Niedertaufkirchen -
Arbing (71.3 km, via its postcode 84494 resolving to the gazetteer's
Neumarkt-Sankt Veit entry - a real PLZ match, not a coincidence, unlike the
Fürstenfeldbruck entry below). That clears the gate more than twice over. It
is a **lower bound**, not the true figure: **32 of the 43** Oberbayern towns
are unknown to the bundled offline gazetteer (it only covers the Landkreise
immediately around the search origin), so a real geocoder would very likely
place more of them inside the radius. Method: Regierungsbezirk = Oberbayern
subset of the real capture, town read from each row's own `PLZ Ort` address
line (falling back to the title only for the few rows with no address
paragraph at all), looked up in `hofradar.geo.gazetteer`, distance via
`hofradar.geo.distance.haversine_km` against the profile center - the same
offline path `town_in_radius` uses, not a live Nominatim run.

Fürstenfeldbruck needs a caveat the other ten do not: `gazetteer.lookup()`
substring-matches it to the unrelated entry "Bruck" (Landkreis Ebersberg),
so its *listed* distance above (~52.9 km) is a manual correction using
Fürstenfeldbruck's real coordinates (48.1772, 11.2547), not what the code
path actually returns for it (which would read 18.3 km, the distance to
Bruck instead). The object's in-radius verdict does not change either way -
52.9 km and 18.3 km are both well inside 80 km - only the printed distance
is corrected here. The substring-matching bug itself lives in
`hofradar.geo.gazetteer.lookup()` and is being fixed on a separate branch;
this document does not depend on that fix landing, since the verdict for
this object is correct regardless.

## Adding a regional source

Most of the value is here. Regional brokers around Rosenheim, Miesbach,
Ebersberg, Mühldorf, Wasserburg and Traunstein are small, slow-moving, and
frequently list a Hofstelle for weeks before it reaches a portal — if it ever
does.

```yaml
- key: makler_musterhuber
  name: "Immobilien Musterhuber, Bad Aibling"
  role: primary
  adapter: generic_rss          # or generic_sitemap
  base_url: "https://musterhuber-immobilien.de"
  region: "Landkreis Rosenheim"
  reliability: 0.8
  enabled: true
  rate_limit_seconds: 3.0
  options:
    feeds:
      - "https://musterhuber-immobilien.de/feed"
```

For a site without a feed, use `generic_sitemap` and narrow it:

```yaml
  adapter: generic_sitemap
  options:
    sites:
      - url: "https://musterhuber-immobilien.de/sitemap.xml"
        include_patterns: ["/objekt/", "/immobilie/"]
        max_pages: 150
```

## Politeness is enforced, not requested

Every adapter goes through one HTTP client that:

- honours `robots.txt` when `respect_robots: true` (the default) and refuses
  disallowed paths outright;
- rate-limits per host to the source's `rate_limit_seconds`;
- sends a descriptive `User-Agent` identifying the project;
- backs off on 429/5xx and honours `Retry-After`;
- caps total pages per source per run.

Nominatim (geocoding) and OSRM (routing) are free public services used at
≤1 request/second with aggressive local caching. If you run this at any real
volume, point `HOFRADAR_NOMINATIM_URL` and `HOFRADAR_OSRM_URL` at your own
instances.
