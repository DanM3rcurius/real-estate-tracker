"""Rendering the digest.

Markdown is the canonical form - it pastes into a mail, a note or a chat, which
is how this report actually gets used. The HTML form is a *fragment*
(``<article class="hof-report">``) on purpose: the web UI drops it straight into
the site layout, and :class:`~hofradar.db.models.ReportRecord` stores the same
string, so what the user copies and what the archive holds cannot drift apart.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hofradar.report.data import ReportData, ReportEntry
from hofradar.web.filters import JINJA_FILTERS, de_eur, de_km, de_number, de_score, de_sqm

TEMPLATE_DIR = Path(__file__).parent / "templates"

#: Label -> attribute, in the order the blueprint prints them.
SCORE_COLUMNS = (
    ("FIT", "fit_score"),
    ("DEAL", "deal_score"),
    ("HIDDEN", "hidden_score"),
    ("FRESH", "freshness_score"),
    ("CONF", "confidence_score"),
)

COUNT_ROWS = (
    ("Neu verifiziert", "newly_verified"),
    ("Echte Neuzugänge", "new_candidates"),
    ("Reaktiviert", "reactivated"),
    ("Preisänderungen", "price_changes"),
    ("Entfernt / verkauft", "removed"),
    ("Zwangsversteigerungen", "foreclosures"),
    ("Off-Market-Signale", "off_market_signals"),
    ("Bekannte mit Update", "known_updated"),
    ("Aktiv insgesamt", "active_total"),
    ("Erfasst insgesamt", "tracked_total"),
)


@lru_cache(maxsize=1)
def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(JINJA_FILTERS)
    env.globals["SCORE_COLUMNS"] = SCORE_COLUMNS
    env.globals["COUNT_ROWS"] = COUNT_ROWS
    return env


def _scores_line(entry: ReportEntry) -> str:
    parts = [f"{label} {de_score(getattr(entry, attr))}" for label, attr in SCORE_COLUMNS]
    return " · ".join(parts)


def _cost_band(entry: ReportEntry) -> str:
    if entry.total_low is None and entry.total_high is None:
        return "noch nicht berechnet"
    band = f"{de_eur(entry.total_low)} – {de_eur(entry.total_high)}"
    if entry.total_mid is not None:
        band += f" (Mitte {de_eur(entry.total_mid)})"
    return band


def render_markdown(data: ReportData) -> str:
    """The copy-paste form. Deliberately short."""
    lines: list[str] = []
    add = lines.append

    add(f"# Hofradar – {data.week_label}")
    add("")
    add(f"**Suchprofil:** {data.profile_name} (`{data.profile_hash}`)")
    add(f"**Zentrum:** {data.center_name}")
    add(
        f"**Entfernung:** {de_km(data.radius_air_km, 0)} Luftlinie "
        f"(Fahrstrecke weich {de_km(data.radius_driving_soft_km)}, "
        f"hart {de_km(data.radius_driving_hard_km)})"
    )
    add(
        f"**Gesamtbudget:** {de_eur(data.budget_total_max)} "
        f"(Kaufpreisziel {de_eur(data.budget_purchase_target)}, "
        f"hart {de_eur(data.budget_purchase_hard)})"
    )
    add(f"**Zeitraum:** seit {data.period_start.strftime('%d.%m.%Y')}")
    add("")

    add("## Lage der Woche")
    add("")
    add("| Kennzahl | Anzahl |")
    add("| --- | ---: |")
    for label, attribute in COUNT_ROWS:
        add(f"| {label} | {de_number(getattr(data.counts, attribute))} |")
    add("")

    add(f"## Quellen-Ausbeute (letzte {data.yield_window_weeks} Wochen)")
    add("")
    if not data.source_yields:
        add("_Keine Beobachtungen im Zeitraum._")
    else:
        add("| Quelle | Objekte gesehen | davon im Radius |")
        add("| --- | ---: | ---: |")
        for row in data.source_yields:
            add(f"| {row.source_key} | {de_number(row.observed)} | {de_number(row.in_radius)} |")
    add("")

    add(f"## Shortlist ({len(data.entries)} von {data.counts.tracked_total} erfassten Objekten)")
    add("")
    if not data.entries:
        add("_Diese Woche nichts, das einen Anruf rechtfertigt._")
        add("")
    for entry in data.entries:
        place = entry.town or "Ort unbekannt"
        add(f"### {entry.rank}. {entry.action} — {entry.title}, {place} `{entry.public_id}`")
        source_label = "Primärquelle" if entry.is_primary_source else "nur Discovery-Quelle"
        add(f"- **Status:** {entry.status_label} · {entry.category_label} · {source_label}")
        add(
            f"- **Preis:** {de_eur(entry.price)} ({entry.price_type}) · "
            f"**Grund:** {de_sqm(entry.land_sqm)} · **Wohnfläche:** {de_sqm(entry.living_sqm)}"
        )
        add(
            f"- **Entfernung:** {de_km(entry.distance_air_km)} Luftlinie · "
            f"**Fahrstrecke:** {entry.driving_display}"
        )
        add(f"- **Scores:** {_scores_line(entry)} → **Final {de_score(entry.final_score)}**")
        add(f"- **Gesamtinvestition:** {_cost_band(entry)}")
        add(f"- **Warum interessant:** {'; '.join(entry.why) if entry.why else '–'}")
        add(f"- **Risiko:** {'; '.join(entry.risks) if entry.risks else '–'}")
        if entry.url:
            add(f"- **Quelle:** {entry.url}")
        add("")

    add("## Nicht einzeln gelistet")
    add("")
    add(
        f"{de_number(data.counts.not_listed)} weitere Objekte sind erfasst und bewertet, "
        "erscheinen hier aber nur als Zahl. Details im Radar."
    )
    if data.notes:
        add("")
        add("## Hinweise")
        add("")
        for note in data.notes:
            add(f"- {note}")
    add("")
    return "\n".join(lines)


def render_html(data: ReportData) -> str:
    """An embeddable ``<article>`` fragment - not a full document."""
    template = _environment().get_template("report.html")
    return template.render(data=data, counts=data.counts, cost_band=_cost_band)
