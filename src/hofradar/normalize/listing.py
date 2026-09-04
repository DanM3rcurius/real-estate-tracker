"""Top-level wiring: ``RawListing`` -> ``NormalizedListing``.

This is the one function every source adapter's output eventually passes
through. It is deliberately dumb about *how* each field is parsed - all of
that lives in the sibling modules - and focused only on: calling the right
parser for each raw field, attaching :class:`~hofradar.contracts.Evidence`
for every load-bearing fact, and recording a human-readable warning whenever
a raw value was present but could not be turned into something trustworthy.
"""

from __future__ import annotations

from hofradar.config import KeywordConfig
from hofradar.contracts import (
    PAGE_KIND_INDEX,
    PAGE_KIND_UTILITY,
    Evidence,
    NormalizedListing,
    RawListing,
)
from hofradar.normalize.dates import parse_german_date
from hofradar.normalize.features import classify_property_type, extract_features
from hofradar.normalize.location import find_location_in_text, parse_location
from hofradar.normalize.numbers import parse_area, parse_german_number, parse_price
from hofradar.normalize.text import text_hash

#: Confidence assigned to evidence for a value the normaliser successfully
#: parsed from a source-provided raw string. Not a statement about whether
#: the underlying fact is *true* - only about parse confidence.
_PARSE_CONFIDENCE = 0.8

#: A year_built outside this range is almost certainly a mis-parse (a
#: listing ID, a price fragment, ...) rather than a real construction year.
_PLAUSIBLE_YEAR_RANGE = range(1000, 2101)

#: A location read out of prose rather than from a labelled field. The parse
#: itself is strict, but the *field* was inferred, and evidence should say so.
_UNLABELLED_LOCATION_CONFIDENCE = 0.6

#: What a page that is not one listing gets told about itself. Every typed
#: value below it was scraped off a page that offers nothing - on a portal's
#: result list from several different adverts at once (issue #10) - so the
#: warning is about the whole listing, not about one field that failed.
#: German, because these lines are shown to the user on /add.
_PAGE_KIND_WARNINGS: dict[str, str] = {
    PAGE_KIND_INDEX: (
        "Seitentyp: Diese Seite ist eine Trefferliste, kein einzelnes Inserat - "
        "gefundene Angaben können aus mehreren Objekten stammen."
    ),
    PAGE_KIND_UTILITY: (
        "Seitentyp: Diese Seite ist eine Portalfunktion (z. B. Merkliste, "
        "Login, Impressum) und kein Inserat."
    ),
}


def _add_numeric_evidence(
    listing: NormalizedListing,
    field_name: str,
    value: float | int | None,
    raw: str | None,
    *,
    source_key: str,
    url: str,
) -> None:
    if value is not None:
        listing.add_evidence(
            field_name,
            Evidence(source=source_key, url=url, quote=raw, confidence=_PARSE_CONFIDENCE),
        )
    elif raw:
        listing.warnings.append(f"{field_name}: could not parse a value from {raw!r}")


def normalize_listing(raw: RawListing, keywords: KeywordConfig) -> NormalizedListing:
    """Turn one source adapter's raw output into a typed, evidenced listing.

    Every load-bearing fact that was actually parsed from a raw string
    (price, land_sqm, living_sqm, year_built, town) gets an
    :class:`~hofradar.contracts.Evidence` entry keyed by field name, quoting
    the raw substring it came from. A raw field that was present but could
    not be parsed produces a warning instead of silently disappearing.
    """
    listing = NormalizedListing(
        source_key=raw.source_key,
        url=raw.url,
        title=raw.title,
        description=raw.description,
        price_raw=raw.price_raw,
        external_id=raw.external_id,
        image_urls=list(raw.image_urls),
        contact_name=raw.contact_name,
        contact_kind=raw.contact_kind,
        contact_detail=raw.contact_detail,
        listing_visible=raw.listing_visible,
        http_status=raw.http_status,
        fetched_at=raw.fetched_at,
        page_kind=raw.page_kind,
    )

    # Said first, because it is the fact every other one below depends on: a
    # page that is not a listing is not a listing with missing fields.
    page_kind_warning = _PAGE_KIND_WARNINGS.get(raw.page_kind)
    if page_kind_warning is not None:
        listing.warnings.append(page_kind_warning)

    combined_text = " ".join(t for t in (raw.title, raw.description) if t)
    listing.text_hash = text_hash(combined_text)

    price, price_type = parse_price(raw.price_raw)
    listing.price = price
    listing.price_type = price_type
    _add_numeric_evidence(
        listing, "price", price, raw.price_raw, source_key=raw.source_key, url=raw.url
    )

    land_sqm = parse_area(raw.land_raw)
    listing.land_sqm = land_sqm
    _add_numeric_evidence(
        listing, "land_sqm", land_sqm, raw.land_raw, source_key=raw.source_key, url=raw.url
    )

    living_sqm = parse_area(raw.living_raw)
    listing.living_sqm = living_sqm
    _add_numeric_evidence(
        listing, "living_sqm", living_sqm, raw.living_raw, source_key=raw.source_key, url=raw.url
    )

    usable_sqm = parse_area(raw.usable_raw)
    listing.usable_sqm = usable_sqm
    _add_numeric_evidence(
        listing, "usable_sqm", usable_sqm, raw.usable_raw, source_key=raw.source_key, url=raw.url
    )

    listing.rooms = parse_german_number(raw.rooms_raw)
    if listing.rooms is None and raw.rooms_raw:
        listing.warnings.append(f"rooms: could not parse a value from {raw.rooms_raw!r}")

    year_value = parse_german_number(raw.year_raw)
    year_built: int | None = None
    year_already_warned = False
    if year_value is not None:
        year_int = int(year_value)
        if year_int in _PLAUSIBLE_YEAR_RANGE:
            year_built = year_int
        else:
            listing.warnings.append(
                f"year_built: implausible value {year_int} from {raw.year_raw!r}"
            )
            year_already_warned = True
    listing.year_built = year_built
    _add_numeric_evidence(
        listing,
        "year_built",
        year_built,
        None if year_already_warned else raw.year_raw,
        source_key=raw.source_key,
        url=raw.url,
    )

    # A source that labels its address gives us location_raw; plenty do not,
    # and an unlabelled address is not an absent one. Recovering it from the
    # description is what keeps a pasted exposé - the shape a human actually
    # copies - from ending up un-geocodable and therefore invisible (issue #3).
    location_raw = raw.location_raw
    recovered = False
    if not location_raw and not raw.postcode and not raw.town:
        location_raw = find_location_in_text(raw.description)
        recovered = location_raw is not None

    location = parse_location(location_raw)
    listing.street = location.street
    listing.postcode = raw.postcode or location.postcode
    listing.town = raw.town or location.town
    listing.district = location.district
    if listing.town:
        listing.add_evidence(
            "town",
            Evidence(
                source=raw.source_key,
                url=raw.url,
                quote=location_raw or raw.town,
                confidence=_UNLABELLED_LOCATION_CONFIDENCE
                if recovered
                else (_PARSE_CONFIDENCE if location.town else 0.6),
            ),
        )
        if recovered:
            listing.warnings.append(
                f"town: no labelled location, read {location_raw!r} out of the description"
            )
    elif location_raw:
        listing.warnings.append(f"town: could not determine a town from {location_raw!r}")
    else:
        # The silence that used to be total. Without a town there is nothing to
        # geocode, so the property can never reach the map or the shortlist -
        # which must be said out loud rather than discovered weeks later.
        listing.warnings.append(
            "town: no location found - the listing cannot be placed on the map "
            "or reach the shortlist until one is added"
        )

    listing.source_date = parse_german_date(raw.source_date_raw)
    if listing.source_date is None and raw.source_date_raw:
        listing.warnings.append(
            f"source_date: could not parse a date from {raw.source_date_raw!r}"
        )

    features = extract_features(combined_text, keywords)
    listing.building_features = features.building_features
    listing.outbuildings = features.outbuildings
    listing.special_features = features.special_features
    listing.exclusion_flags = features.exclusion_flags
    listing.hidden_signals = features.hidden_signals
    listing.is_foreclosure = features.is_foreclosure
    listing.is_monument = features.is_monument
    listing.is_private_seller = features.is_private_seller
    listing.is_off_market_signal = features.is_off_market_signal
    if listing.exclusion_flags:
        listing.warnings.append(
            f"exclusion flags matched: {', '.join(listing.exclusion_flags)}"
        )

    listing.property_type = classify_property_type(raw.title or "", raw.description or "", keywords)

    return listing
