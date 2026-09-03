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
| `ovbimmo` | Regional newspaper portal for Lkr. Rosenheim/Mühldorf/West-Traunstein — brokers plus the papers' own classified ads, including private Chiffre sellers. See below. |

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
The search CGI's response shape (pagination / form-vs-results) is still
unverified, which is why `discover()` still calls `mark_enumeration_incomplete`.

## OVBimmo (OVB Heimatzeitungen): terms check complete, source enabled

`hofradar.sources.adapters.ovbimmo.OvbimmoAdapter` is written and tested
against a real search-results capture. `ovbimmo` is enabled in
`config/sources.yaml` (`enabled: true`, `role: local`, `terms_checked_at:
2026-09-03`) - the registry entry's `terms_excerpt` carries the same finding
recorded here.

Terms check status: **DONE (2026-09-03)**, by a human on a networked
machine - not from this container, which cannot reach `ovbimmo.de` at all
(see Ruling 1 in the plan's shared context). Two pages were read:

- `https://ovbimmo.de/robots.txt`: `User-agent: *` carries **no blanket
  `Disallow`**. Only `*/2823228/`, `/_widget/*` and `/search-widget/*` are
  excluded - none of which this adapter touches. `/immobilien/*` is
  explicitly `Allow`ed, and a sitemap is advertised. `/kaufen/...` (the
  faceted search this adapter uses) is not disallowed.
- `https://www.rosenheim24.de/ueber-uns/agb/` (linked by ovbimmo's own footer
  as its AGB; publisher OVB24 GmbH, Stand April 2024) has three parts: (I)
  AGB für Online-Werbung, which defers to `ovb24.de/agb/`; (II) AGB für
  Shop-Produkte, covering purchase, delivery, payment and Widerruf for
  **purchased** products (E-Books, paid article access) - its §6
  "Nutzungsrecht bei digitalen Produkten" governs those purchased products,
  not the public listing pages this adapter reads; (III)
  Teilnahmebedingungen für Gewinnspiele. **No clause anywhere in it restricts
  automated retrieval, crawling or scraping.**

Both findings are scoped to the two pages actually read; neither is a claim
about the whole site. Search-result rendering was also confirmed
server-side: the real capture at
`tests/fixtures/html/ovbimmo_search_rosenheim.html` has the result cards in
the HTML, not injected by JavaScript.

**Pagination.** A single search page carries a small fraction of the total
result count - the real capture shows 20 of 186 results for one facet, with
`<link rel="next" href="...?page=2" />` in `<head>`. `discover()` follows
that trail per municipality/property-type facet, capped at
`options.max_pages_per_search` (module default
`ovbimmo.DEFAULT_MAX_PAGES_PER_SEARCH = 5`). Per invariant 4b, any run that
stops before the trail runs out - the cap, a bad page, a failed fetch, or no
municipalities configured at all - calls `mark_enumeration_incomplete` with
the reason, so this source's silence is never misread as "removed" for a
run that saw only page one of ten.

**The detail page, against a real capture.**
`tests/fixtures/html/ovbimmo_detail.html` is a real page captured from
`ovbimmo.de` on 2026-09-03 (see the provenance comment at the top of the
fixture and `tests/sources/adapters/test_ovbimmo.py`). It confirms the
structure expected going in: a `dataLayer` JSON blob carrying `listing_id`
(the same 6-char code as the URL), `property_price` **in cents**, `rooms`,
`area`, `postal_code`, `locality`, `geo_hierarchy_*`; and an Objektdaten
table built from `col-label`/`col-value` divs.

None of that structured data is parsed by this adapter. `fetch_detail` uses
only `_htmlutil`'s generic, markup-agnostic HTML fallback - and against the
real page, that gets:

- **title** and **image_urls** cleanly, via `og:title` / `og:image`;
- **description**, the full visible body text - which means the price,
  room count and phrases like "Provision für Käufer" all reach it as plain
  text, so the `hidden_score` keyword vocabulary still fires on them;
- **nothing** for `price_raw`/`rooms_raw`/`living_raw`/`land_raw`/etc.
  `extract_labeled_fields` wants a single "Label: value" line, and this
  page's Objektdaten table renders the value *before* its label in two
  separate divs, one per line ("690.000,00 €" then "Kaufpreis", never
  "Kaufpreis: 690.000,00 €") - so it genuinely extracts nothing here. This
  is a real limitation, not a bug in this task's scope: reading the
  dataLayer JSON or the col-label/col-value table would need a
  purpose-built extension to this adapter (or to `_htmlutil`) that does not
  exist yet.
- `external_id` always comes from the **URL**, never the page - the same
  6-char code the dataLayer also carries, but reading it from the URL needs
  no parser at all and survives a broker rewriting the title.

`contact_kind` is deliberately left `None` rather than hardcoded. The one
real detail page captured so far is a **broker** listing ("Provision für
Käufer", Robert Schlamp Immobilien e. K.) - not representative of the
private/Chiffre inventory this source exists to reach. One capture is one
page; guessing either way would corrupt the `hidden_score` signal.

**Coverage.** `ovbimmo` covers Lkr. Rosenheim, Mühldorf and western
Traunstein. It does **not** cover Lkr. Miesbach or Ebersberg — those are
Ippen/Münchner Merkur titles with a different portal. Until an Ippen source
exists, those municipalities are served only by `gemeindeblatt_pdf`. See
`docs/coverage.md`.

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
