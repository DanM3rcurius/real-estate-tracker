"""parse_german_date: absolute, spelled-out, ISO-week, relative, and ISO forms."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from hofradar.normalize import parse_german_date


def test_dotted_numeric_date():
    result = parse_german_date("12.03.2026")
    assert result == datetime(2026, 3, 12, tzinfo=UTC)


def test_dotted_numeric_date_two_digit_year():
    result = parse_german_date("12.03.26")
    assert result == datetime(2026, 3, 12, tzinfo=UTC)


def test_spelled_out_month():
    result = parse_german_date("12. März 2026")
    assert result == datetime(2026, 3, 12, tzinfo=UTC)


def test_spelled_out_month_ascii_umlaut_fold():
    result = parse_german_date("12. Maerz 2026")
    assert result == datetime(2026, 3, 12, tzinfo=UTC)


def test_iso_week():
    result = parse_german_date("KW 34/2026")
    expected = date.fromisocalendar(2026, 34, 1)
    assert result == datetime(expected.year, expected.month, expected.day, tzinfo=UTC)


def test_iso_week_no_space():
    result = parse_german_date("KW34/2026")
    expected = date.fromisocalendar(2026, 34, 1)
    assert result == datetime(expected.year, expected.month, expected.day, tzinfo=UTC)


def test_vor_n_tagen():
    result = parse_german_date("vor 3 Tagen")
    today = datetime.now(UTC).date()
    assert result.date() == today - timedelta(days=3)
    assert result.tzinfo is not None


def test_vor_n_wochen():
    result = parse_german_date("vor 2 Wochen")
    today = datetime.now(UTC).date()
    assert result.date() == today - timedelta(weeks=2)


def test_gestern():
    result = parse_german_date("gestern")
    today = datetime.now(UTC).date()
    assert result.date() == today - timedelta(days=1)


def test_heute():
    result = parse_german_date("heute")
    today = datetime.now(UTC).date()
    assert result.date() == today


def test_iso_string_date_only():
    result = parse_german_date("2026-03-12")
    assert result == datetime(2026, 3, 12, tzinfo=UTC)


def test_iso_string_with_z_suffix():
    result = parse_german_date("2026-03-12T10:30:00Z")
    assert result == datetime(2026, 3, 12, 10, 30, tzinfo=UTC)


def test_iso_string_with_offset_converts_to_utc():
    result = parse_german_date("2026-03-12T10:30:00+02:00")
    assert result == datetime(2026, 3, 12, 8, 30, tzinfo=UTC)


def test_result_is_always_tz_aware():
    for text in ("12.03.2026", "heute", "KW 34/2026", "2026-03-12"):
        result = parse_german_date(text)
        assert result.tzinfo is not None


def test_none_and_empty_and_garbage():
    assert parse_german_date(None) is None
    assert parse_german_date("") is None
    assert parse_german_date("nicht interpretierbar") is None


def test_invalid_calendar_date_returns_none():
    assert parse_german_date("31.02.2026") is None
