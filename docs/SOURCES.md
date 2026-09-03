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
```

and got back **`HTTP/1.1 404 Not Found` (Server: CERN httpd)** - no
`robots.txt` exists on the host, so there are no crawl directives to honour or
violate.

The Impressum at `/blfd/impressum/index.html` carries a "Nutzungsbedingungen"
section. Its operative sentence:

> "Als Privatperson dürfen Sie urheberrechtlich geschütztes Material zum
> privaten und sonstigen eigenen Gebrauch im Rahmen des § 53
> Urheberrechtsgesetz (UrhG) verwenden. Eine Vervielfältigung oder Verwendung
> dieser Seiten oder Teilen davon in anderen elektronischen oder gedruckten
> Publikationen und deren Veröffentlichung ist nur mit unserer Einwilligung
> gestattet."

Private and own use is explicitly permitted under § 53 UrhG. Republication -
reproducing these pages or parts of them in other electronic or printed
publications - requires BLfD's consent. **No clause anywhere restricts
automated retrieval, crawling or machine access.** A separate
"Haftungsausschluss" section disclaims any warranty of the accuracy,
completeness or currency of the published information (consistent with this
source's `reliability: 0.8`, below `zvg_bayern`'s 0.95).

Hofradar is a private, password-gated research tool for one household and
does not publish - its weekly digest and web UI stay inside the household, so
this sits within the permitted use. That boundary (permitted: private use;
not permitted: republication) is recorded in the registry entry's `notes:`
as well, so it stays visible next to the config that could violate it.

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
Capturing one detail page says nothing about `robots.txt` or the terms of use,
and the search CGI's response shape (pagination / form-vs-results) is still
unverified, which is why `discover()` still calls `mark_enumeration_incomplete`.

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
