"""Keyword-driven feature extraction and property-type classification.

Everything here is matched against ``config/keywords.yaml`` (via
:class:`hofradar.config.KeywordConfig`), umlaut-insensitively and
case-insensitively, on whole words/phrases only. Whole-word matching alone
is not always enough - "Stall" must not fire inside "Installation", which a
plain ``\\b`` boundary already prevents (there is no boundary between the
"n" and the "s" in "installation"). But some short, real German words are
also common vocabulary in an unrelated sense (e.g. "Gut" the noun, meaning
"estate", vs. "gut" the adjective, meaning "good", as in "gut erhalten" -
both are genuine whole words, so ``\\b`` alone cannot tell them apart). For
any single-word keyword three characters or shorter, we additionally require
either a capitalised occurrence in the *original* (pre-casefold) text - the
proper-noun-like usage - or that it only ever appears fused into a compound
(which never reaches this matcher as a standalone word to begin with, since
a compound has no internal word boundary).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hofradar.config import KeywordConfig
from hofradar.normalize.text import normalize_text, slugify

#: Canonical slugs for phrases whose generic slugify() output would not match
#: the names this module's contract mandates (e.g. several distinct phrasings
#: of "sold by a private party" all collapse to one tag).
_HIDDEN_SIGNAL_OVERRIDES: dict[str, str] = {
    "von privat": "privatverkauf",
    "privat zu verkaufen": "privatverkauf",
    "verkauf aus altersgruenden": "aus_altersgruenden",
    "entwicklungspotential": "entwicklungspotenzial",
}

#: Hidden signals this module must surface even when the exact phrase is not
#: (or is only partially) present in config/keywords.yaml - foreclosure is
#: the clearest example, since ZVG auctions are rarely advertised with the
#: word "Zwangsversteigerung" bare in the hidden_phrases list.
_EXTRA_HIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "zwangsversteigerung": re.compile(
        r"zwangsversteigerung|zwangsvollstreckung|\bzvg\b|teilungsversteigerung"
    ),
}

_MONUMENT_RE = re.compile(r"denkmal")
_FORECLOSURE_RE = re.compile(
    r"zwangsversteigerung|zwangsvollstreckung|\bzvg\b|teilungsversteigerung|mindestgebot"
)
_PRIVATE_SELLER_RE = re.compile(
    r"von privat|privat zu verkaufen|privatverkauf|kein makler|ohne makler"
)
_OFF_MARKET_RE = re.compile(
    r"\bchiffre\b|off.?market|stille vermarktung|nicht oeffentlich (?:inseriert|vermarktet)"
)

#: Single-word keywords at or below this length require extra evidence
#: (capitalisation, i.e. proper-noun-like usage) before they count as a
#: match - see the module docstring.
_SHORT_TERM_MAX_LEN = 3


@dataclass(slots=True)
class FeatureExtraction:
    """Canonical tags and flags pulled out of a listing's free text."""

    building_features: list[str] = field(default_factory=list)
    outbuildings: list[str] = field(default_factory=list)
    special_features: list[str] = field(default_factory=list)
    exclusion_flags: list[str] = field(default_factory=list)
    hidden_signals: list[str] = field(default_factory=list)
    is_foreclosure: bool = False
    is_monument: bool = False
    is_private_seller: bool = False
    is_off_market_signal: bool = False


def _capitalised_occurrence_exists(orig_text: str, norm_word: str) -> bool:
    """True if ``norm_word`` occurs in ``orig_text`` as a word starting upper-case."""
    for token in re.findall(r"\b\w+\b", orig_text, flags=re.UNICODE):
        if token[:1].isupper() and normalize_text(token) == norm_word:
            return True
    return False


def _matched_terms(norm_text: str, orig_text: str, terms: list[str]) -> list[str]:
    """Return the subset of ``terms`` that occur as whole words/phrases in the text."""
    matched: list[str] = []
    for term in terms:
        norm_term = normalize_text(term)
        if not norm_term:
            continue
        if not re.search(r"\b" + re.escape(norm_term) + r"\b", norm_text):
            continue
        if " " not in norm_term and len(norm_term) <= _SHORT_TERM_MAX_LEN:
            if not _capitalised_occurrence_exists(orig_text, norm_term):
                continue
        matched.append(term)
    return matched


def _slug_for_hidden_phrase(term: str) -> str:
    norm = normalize_text(term)
    return _HIDDEN_SIGNAL_OVERRIDES.get(norm, slugify(term))


def extract_features(text: str, keywords: KeywordConfig) -> FeatureExtraction:
    """Extract canonical feature tags and boolean signals from ``text``.

    Group mapping, all driven by ``config/keywords.yaml``:

    - ``keywords.buildings`` -> ``outbuildings`` (Scheune, Stadel, Stall, ...)
    - ``keywords.features`` -> ``building_features``: qualities of the place
      itself rather than structures on it (Alleinlage, Obstgarten, teilbar,
      Bebauungsplan). The seclusion and development terms of the fit score read
      this list, so it is deliberately separate from ``outbuildings``.
    - ``keywords.regional`` -> ``special_features`` (Sacherl, Hoamat, ...)
    - ``keywords.hidden_phrases`` -> ``hidden_signals``, slugified, with a
      handful of canonical-name overrides plus a foreclosure detector that
      fires independently of the configured vocabulary
    - ``keywords.negative`` -> ``exclusion_flags``

    The whole-farm type names in ``keywords.core`` are not a feature group;
    they feed :func:`classify_property_type` instead.
    """
    text = text or ""
    norm_text = normalize_text(text)

    outbuildings = sorted({slugify(t) for t in _matched_terms(norm_text, text, keywords.buildings)})
    building_features = sorted(
        {slugify(t) for t in _matched_terms(norm_text, text, keywords.features)}
    )
    special_features = sorted(
        {slugify(t) for t in _matched_terms(norm_text, text, keywords.regional)}
    )
    exclusion_flags = sorted(
        {slugify(t) for t in _matched_terms(norm_text, text, keywords.negative)}
    )

    hidden_matches = _matched_terms(norm_text, text, keywords.hidden_phrases)
    hidden_signals = {_slug_for_hidden_phrase(t) for t in hidden_matches}
    for slug, pattern in _EXTRA_HIDDEN_PATTERNS.items():
        if pattern.search(norm_text):
            hidden_signals.add(slug)

    return FeatureExtraction(
        building_features=building_features,
        outbuildings=outbuildings,
        special_features=special_features,
        exclusion_flags=exclusion_flags,
        hidden_signals=sorted(hidden_signals),
        is_foreclosure=bool(_FORECLOSURE_RE.search(norm_text)),
        is_monument=bool(_MONUMENT_RE.search(norm_text)),
        is_private_seller=bool(_PRIVATE_SELLER_RE.search(norm_text)),
        is_off_market_signal=bool(_OFF_MARKET_RE.search(norm_text)),
    )


#: Farmstead-type vocabulary, most specific first. "Anwesen" ("premises") on
#: its own is the most generic term in the list and only wins when nothing
#: more specific is present.
_PROPERTY_TYPE_SPECIFICITY: list[str] = [
    "Vierseithof",
    "Dreiseithof",
    "Zweiseithof",
    "Einödhof",
    "Resthof",
    "Austragshaus",
    "Hofanwesen",
    "Bauernanwesen",
    # A qualified form ("former farm") is more specific than the bare term
    # it contains, so it must rank ahead of it.
    "ehemaliger Bauernhof",
    "Bauernhof",
    "Bauernhaus",
    "Hof mit Nebengebäuden",
    "Hofstelle",
    "Landgut",
    "landwirtschaftliches Anwesen",
    "ehemalige Landwirtschaft",
    "Landwirtschaft",
    "Einöde",
    "Sacherl zu verkaufen",
    "Sacherl",
    "Anwesen",
]


def _most_specific_match(norm_text: str, orig_text: str, vocabulary: list[str]) -> str | None:
    present = set(_matched_terms(norm_text, orig_text, vocabulary))
    for term in _PROPERTY_TYPE_SPECIFICITY:
        if term in present:
            return term
    # Vocabulary entries not covered by the specificity ranking still count,
    # just after every ranked term.
    unranked = present - set(_PROPERTY_TYPE_SPECIFICITY)
    if unranked:
        return sorted(unranked, key=len, reverse=True)[0]
    return None


def classify_property_type(title: str, description: str, keywords: KeywordConfig) -> str | None:
    """Pick the single most specific property type mentioned, title first.

    Matches against ``keywords.core`` (the farmstead-type vocabulary) in the
    title; if any type is present there, the most specific one wins (e.g.
    "Vierseithof" beats "Anwesen" when a listing mentions both). Only when
    the title has no match at all do we fall back to the description. Returns
    the canonical slug (e.g. "vierseithof"), or ``None`` if nothing matched.
    """
    title = title or ""
    description = description or ""
    vocabulary = keywords.core

    title_match = _most_specific_match(normalize_text(title), title, vocabulary)
    if title_match is not None:
        return slugify(title_match)

    desc_match = _most_specific_match(normalize_text(description), description, vocabulary)
    if desc_match is not None:
        return slugify(desc_match)

    return None
