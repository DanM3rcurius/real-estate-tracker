"""Offline fallback gazetteer: Upper Bavaria - the Landkreise this project's
search actually covers (Rosenheim, Miesbach, Ebersberg, Muhldorf,
Traunstein), plus the Wasserburg am Inn area straddling Rosenheim/Muhldorf.

Used only when ``HOFRADAR_OFFLINE=1``. It keeps tests and air-gapped runs
working without ever hitting Nominatim - never used when the network is
available. Coordinates are town-centre approximations (roughly "town"
precision, a few hundred metres at worst), not survey-grade positions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_POSTCODE_RE = re.compile(r"\b\d{5}\b")


@dataclass(frozen=True, slots=True)
class GazetteerEntry:
    name: str
    postcode: str
    lat: float
    lon: float


_TOWNS: tuple[GazetteerEntry, ...] = (
    # -- Landkreis Rosenheim ------------------------------------------------ #
    GazetteerEntry("Rosenheim", "83022", 47.8561, 12.1289),
    GazetteerEntry("Bad Aibling", "83043", 47.8656, 12.0116),
    GazetteerEntry("Bruckmuehl", "83052", 47.8425, 11.9536),
    GazetteerEntry("Kolbermoor", "83059", 47.8508, 12.0631),
    GazetteerEntry("Raubling", "83064", 47.7761, 12.1194),
    GazetteerEntry("Stephanskirchen", "83071", 47.8386, 12.1519),
    GazetteerEntry("Prien am Chiemsee", "83209", 47.8586, 12.3453),
    GazetteerEntry("Bernau am Chiemsee", "83233", 47.7906, 12.3831),
    GazetteerEntry("Rimsting", "83253", 47.9017, 12.3067),
    GazetteerEntry("Vogtareuth", "83569", 47.9508, 12.1917),
    GazetteerEntry("Amerang", "83123", 47.9481, 12.2919),
    GazetteerEntry("Feldkirchen-Westerham", "83620", 47.9061, 11.8442),
    GazetteerEntry("Rott am Inn", "83543", 47.9169, 11.9836),
    GazetteerEntry("Riedering", "83134", 47.8814, 12.2372),
    GazetteerEntry("Frasdorf", "83112", 47.7719, 12.3467),
    GazetteerEntry("Neubeuern", "83115", 47.7717, 12.1811),
    GazetteerEntry("Nussdorf am Inn", "83131", 47.7275, 12.1381),
    GazetteerEntry("Brannenburg", "83098", 47.6989, 12.1122),
    GazetteerEntry("Flintsbach am Inn", "83126", 47.6797, 12.0819),
    GazetteerEntry("Oberaudorf", "83080", 47.6394, 12.1739),
    GazetteerEntry("Kiefersfelden", "83088", 47.6072, 12.1867),
    GazetteerEntry("Samerberg", "83122", 47.7583, 12.2119),
    GazetteerEntry("Halfing", "83128", 47.9603, 12.2528),
    GazetteerEntry("Rohrdorf", "83101", 47.7625, 12.1433),
    GazetteerEntry("Soechtenau", "83139", 47.9236, 12.1181),
    # -- Landkreis Miesbach --------------------------------------------------#
    GazetteerEntry("Miesbach", "83714", 47.7897, 11.8317),
    GazetteerEntry("Holzkirchen", "83607", 47.8747, 11.7108),
    GazetteerEntry("Hausham", "83734", 47.7375, 11.8283),
    GazetteerEntry("Schliersee", "83727", 47.7392, 11.8578),
    GazetteerEntry("Bad Wiessee", "83707", 47.7028, 11.7161),
    GazetteerEntry("Tegernsee", "83684", 47.7139, 11.7592),
    GazetteerEntry("Gmund am Tegernsee", "83703", 47.7539, 11.7522),
    GazetteerEntry("Otterfing", "83624", 47.9058, 11.7178),
    GazetteerEntry("Waakirchen", "83666", 47.7861, 11.7378),
    GazetteerEntry("Warngau", "83627", 47.8514, 11.7514),
    GazetteerEntry("Valley", "83626", 47.8975, 11.7997),
    GazetteerEntry("Weyarn", "83629", 47.8656, 11.8069),
    GazetteerEntry("Irschenberg", "83737", 47.8067, 11.9019),
    GazetteerEntry("Fischbachau", "83730", 47.7014, 11.9539),
    GazetteerEntry("Bayrischzell", "83735", 47.6606, 12.0206),
    GazetteerEntry("Hundham", "83734", 47.7217, 11.9186),
    # -- Landkreis Ebersberg --------------------------------------------------#
    GazetteerEntry("Ebersberg", "85560", 48.0797, 11.9683),
    GazetteerEntry("Grafing bei Muenchen", "85567", 48.0464, 11.9611),
    GazetteerEntry("Kirchseeon", "85614", 48.0842, 11.9214),
    GazetteerEntry("Glonn", "85652", 47.9975, 11.8681),
    GazetteerEntry("Assling", "85617", 47.9942, 11.9744),
    GazetteerEntry("Vaterstetten", "85591", 48.1058, 11.7511),
    GazetteerEntry("Zorneding", "85604", 48.1136, 11.8517),
    GazetteerEntry("Baldham", "85598", 48.1097, 11.8175),
    GazetteerEntry("Steinhoering", "85643", 48.0011, 12.0356),
    GazetteerEntry("Frauenneuharting", "83565", 48.0311, 12.0819),
    GazetteerEntry("Bruck", "85567", 48.0261, 12.0106),
    # -- Landkreis Muhldorf ----------------------------------------------------#
    GazetteerEntry("Muehldorf am Inn", "84453", 48.2489, 12.5225),
    GazetteerEntry("Waldkraiburg", "84478", 48.2081, 12.3986),
    GazetteerEntry("Altoetting", "84503", 48.2278, 12.6767),
    GazetteerEntry("Neumarkt-Sankt Veit", "84494", 48.3711, 12.5028),
    GazetteerEntry("Ampfing", "84539", 48.2461, 12.4083),
    GazetteerEntry("Kraiburg am Inn", "84410", 48.1994, 12.4058),
    GazetteerEntry("Gars am Inn", "83536", 48.1428, 12.2708),
    GazetteerEntry("Toeging am Inn", "84513", 48.2492, 12.5814),
    GazetteerEntry("Polling", "84503", 48.2211, 12.5350),
    GazetteerEntry("Mettenheim", "84562", 48.2417, 12.4419),
    # -- Landkreis Traunstein --------------------------------------------------#
    GazetteerEntry("Traunstein", "83278", 47.8697, 12.6417),
    GazetteerEntry("Trostberg", "83308", 48.0272, 12.5606),
    GazetteerEntry("Chieming", "83339", 47.8925, 12.5361),
    GazetteerEntry("Grabenstaett", "83355", 47.8636, 12.5658),
    GazetteerEntry("Uebersee", "83236", 47.8300, 12.4931),
    GazetteerEntry("Grassau", "83224", 47.7717, 12.4633),
    GazetteerEntry("Marquartstein", "83250", 47.7411, 12.4592),
    GazetteerEntry("Reit im Winkl", "83242", 47.6706, 12.4589),
    GazetteerEntry("Ruhpolding", "83324", 47.7642, 12.6425),
    GazetteerEntry("Siegsdorf", "83313", 47.8286, 12.6250),
    GazetteerEntry("Waging am See", "83329", 47.9436, 12.7275),
    GazetteerEntry("Palling", "83349", 47.9736, 12.5967),
    GazetteerEntry("Tacherting", "83342", 47.9714, 12.5361),
    GazetteerEntry("Vachendorf", "83377", 47.8286, 12.5325),
    # -- Wasserburg am Inn area (Rosenheim/Muhldorf border) --------------------#
    GazetteerEntry("Wasserburg am Inn", "83512", 48.0561, 12.2331),
    GazetteerEntry("Edling", "83533", 48.0006, 12.1728),
    GazetteerEntry("Griesstaett", "83556", 47.9756, 12.2144),
    GazetteerEntry("Soyen", "83564", 48.0219, 12.1381),
    GazetteerEntry("Schonstett", "83125", 48.0186, 12.2917),
    GazetteerEntry("Babensham", "83547", 48.0119, 12.2939),
    GazetteerEntry("Rechtmehring", "83562", 48.1069, 12.1592),
)

#: Lower-cased town name -> entry. Umlauts are stored transliterated
#: (ue/oe/ae/ss) so plain-ASCII and umlaut spellings both normalise to a hit
#: via ``_fold`` below.
BY_NAME: dict[str, GazetteerEntry] = {entry.name.lower(): entry for entry in _TOWNS}
#: Postcode -> entry. A few postcodes are shared by neighbouring villages in
#: reality; this fallback keeps only the last one registered for that code.
BY_POSTCODE: dict[str, GazetteerEntry] = {entry.postcode: entry for entry in _TOWNS}

_UMLAUT_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _fold(text: str) -> str:
    return text.casefold().translate(_UMLAUT_FOLD)


_BY_NAME_FOLDED: dict[str, GazetteerEntry] = {_fold(entry.name): entry for entry in _TOWNS}


def _boundary_pattern(name: str) -> re.Pattern[str]:
    """A ``\\w``-boundary-anchored matcher for one folded entry name.

    Farmstead addresses paste a town name into a longer string ("83512
    Wasserburg am Inn", "Titting - Altdorf"), so the match has to look
    inside the query - but German compounds glue words together with no
    separator at all ("Fuerstenfeldbruck" contains "Bruck" as bare
    characters, not as the town "Bruck"). ``\\w`` boundaries reject a hit
    inside a longer word while still crossing spaces, hyphens and
    punctuation, which is exactly where a real town name legitimately ends
    inside a longer string.
    """
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


#: One boundary matcher per entry, paired with its folded name (for the
#: leftmost-match tie-break below - comparing lengths on the *folded* name,
#: not ``entry.name``, keeps this correct once a non-ASCII entry name is
#: added; today the two lengths happen to coincide because every bundled
#: name is already ASCII-transliterated).
_NAME_PATTERNS: tuple[tuple[re.Pattern[str], str, GazetteerEntry], ...] = tuple(
    (_boundary_pattern(name), name, entry) for name, entry in _BY_NAME_FOLDED.items()
)


def lookup(query: str) -> GazetteerEntry | None:
    """Best-effort match of a free-text address against the gazetteer.

    Tries an exact town-name match first (umlaut-folded, so "Muehldorf" and
    "Mühldorf" both hit), then a word-boundary substring match (so
    "Hauptstrasse 5, 83043 Bad Aibling" resolves via "bad aibling", but
    "Fuerstenfeldbruck" does *not* resolve via "bruck" - the town name has to
    appear as whole words in the query, not as a bare run of characters
    inside a longer word), then any 5-digit postcode found in the text.

    When two *disjoint* entries both match ("Chieming bei Waging am See"
    contains both "Chieming" and "Waging am See"), the one that starts
    earliest in the query wins, not the one with the longer name. The
    subject of an "X bei Y" title is X, and X is written first - preferring
    the longer name instead would systematically favour whichever entry's
    name happens to be wordier, with no relation to which one the query is
    actually about. A tie in start position (one entry's match is a prefix
    of another's, e.g. "Tegernsee" at the start of "Tegernsee ...") keeps
    the longer, more specific match.
    """
    normalized = " ".join((query or "").strip().split())
    if not normalized:
        return None
    folded = _fold(normalized)

    if folded in _BY_NAME_FOLDED:
        return _BY_NAME_FOLDED[folded]

    best_start: int | None = None
    best_name_len = -1
    best_entry: GazetteerEntry | None = None
    for pattern, name, entry in _NAME_PATTERNS:
        match = pattern.search(folded)
        if match is None:
            continue
        start = match.start()
        if best_start is None or start < best_start or (
            start == best_start and len(name) > best_name_len
        ):
            best_start, best_name_len, best_entry = start, len(name), entry
    if best_entry is not None:
        return best_entry

    for match in _POSTCODE_RE.findall(normalized):
        if match in BY_POSTCODE:
            return BY_POSTCODE[match]

    return None
