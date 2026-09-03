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

## Pending a terms check: Denkmalbörse (BLfD)

`hofradar.sources.adapters.denkmalboerse.DenkmalboerseAdapter` is written and
tested against a fixture (see the note on that fixture below). `denkmalboerse`
**is** now in `config/sources.yaml`, with `enabled: false` and no
`terms_checked_at` / `terms_excerpt` keys at all - `SourceConfig` refuses
`enabled: true` without both, and neither can honestly be written yet, because
invariant 7's terms/robots check has not been run: the build environment's
egress proxy denies the `www.blfd.bayern.de` host outright (an organisation
policy decision, not a transient failure), so it cannot be checked from here.

Terms check status: **OUTSTANDING**. Before this source may be enabled,
someone with real network access must run these four commands and record
what came back:

```bash
curl -s https://www.blfd.bayern.de/robots.txt
curl -sI https://www.blfd.bayern.de/information-service/denkmalboerse/objekte/005816/index.html
curl -s https://www.blfd.bayern.de/information-service/denkmalboerse/ | grep -i -A5 'nutzungsbedingung\|impressum\|haftung'
curl -s 'https://www.blfd.bayern.de/cgi-bin/fts_search_verkauf.pl' | head -100
```

If `robots.txt` disallows `/information-service/`, or the terms restrict
automated retrieval, the adapter stays written but permanently disabled -
same treatment as the three portal adapters above. Only once the result is
recorded here (or in the registry entry's `terms_excerpt`) may
`config/sources.yaml` set `enabled: true` for `denkmalboerse`.

Partial evidence *has* since arrived from a networked machine, though it does
**not** close the terms check above - `robots.txt` and the terms text remain
unread:

- `https://www.blfd.bayern.de/information-service/denkmalboerse/objekte/005816/index.html`
  genuinely returns HTTP 200, served by a `CERN httpd` static server.
- The real page carries `<div class="immo-inhalt">`, and an "Eigentümer des
  Anwesens" block with a direct `mailto:` link - confirming
  `contact_kind="private"` is the right reading for this source.
- An Exposé PDF is published under
  `/mam/information_und_service/denkmal_boerse/oberbayern/`.

Also outstanding: `tests/fixtures/html/denkmalboerse_object_005816.html` is a
hand-written *synthetic* fixture, not a real captured page - the same egress
denial that blocks the terms check also blocks capturing it. It must be
replaced with a real page (see Step 2 of the plan this note originated from)
before this source ships enabled.

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
