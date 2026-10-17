"""Spoken delivery times → an IST datetime (when parseable) plus the raw words; free-text order notes."""
from datetime import datetime

from aira_live.business.clock import IST
from aira_live.business.delivery_time import clean_note, delivery_by_words, parse_delivery_by

NOW = datetime(2026, 10, 11, 15, 30, tzinfo=IST)      # Sunday 11 Oct 2026, 3:30 pm IST


def test_tomorrow_6am():
    d = parse_delivery_by("deliver by 6 am tomorrow", now=NOW)
    assert d == {"text": "deliver by 6 am tomorrow", "at": "2026-10-12T06:00:00+05:30"}


def test_time_without_day_today_when_still_ahead_else_tomorrow():
    assert parse_delivery_by("5 pm", now=NOW)["at"] == "2026-10-11T17:00:00+05:30"
    assert parse_delivery_by("6:30 am", now=NOW)["at"] == "2026-10-12T06:30:00+05:30"


def test_day_words_and_24h_clock():
    assert parse_delivery_by("day after tomorrow 18:00", now=NOW)["at"] == "2026-10-13T18:00:00+05:30"
    assert parse_delivery_by("today evening", now=NOW)["at"] == "2026-10-11T18:00:00+05:30"
    assert parse_delivery_by("tomorrow morning", now=NOW)["at"] == "2026-10-12T07:00:00+05:30"
    assert parse_delivery_by("tuesday 7 am", now=NOW)["at"] == "2026-10-13T07:00:00+05:30"
    assert parse_delivery_by("in 2 hours", now=NOW)["at"] == "2026-10-11T17:30:00+05:30"


def test_tamil_and_hindi_day_words():
    assert parse_delivery_by("நாளை காலை 6 மணிக்கு", now=NOW)["at"] == "2026-10-12T06:00:00+05:30"
    assert parse_delivery_by("कल शाम 5 बजे", now=NOW)["at"] == "2026-10-12T17:00:00+05:30"


def test_day_only_keeps_the_date():
    assert parse_delivery_by("tomorrow", now=NOW) == {"text": "tomorrow", "at": "2026-10-12"}


def test_unparseable_keeps_raw_text_and_no_datetime():
    assert parse_delivery_by("before the festival rush", now=NOW) == {"text": "before the festival rush", "at": None}


def test_empty_or_no_means_none():
    for s in ("", "  ", None, "no", "none", "no time", "nothing", "not needed", "இல்லை", "नहीं"):
        assert parse_delivery_by(s, now=NOW) is None


def test_words_for_messages_and_read_back():
    d = parse_delivery_by("6 am tomorrow", now=NOW)
    assert delivery_by_words(d) == "6 am tomorrow (Mon 12 Oct, 6:00 am)"
    assert delivery_by_words({"text": "tomorrow", "at": "2026-10-12"}) == "tomorrow (Mon 12 Oct)"
    assert delivery_by_words({"text": "before the rush", "at": None}) == "before the rush"
    assert delivery_by_words(None) == ""


def test_note_is_trimmed_capped_and_sanitised():
    assert clean_note("  send   fresh\nstock \x00 ") == "send fresh stock"
    assert clean_note("x" * 200) == "x" * 140
    for s in ("", "   ", None, "no", "none", "nothing", "no note"):
        assert clean_note(s) is None


def test_afternoon_is_not_read_as_noon_and_midnight_is_not_night():
    """Bug sweep: "afternoon" contains "noon" and "midnight" contains "night"; both went to the vendor wrong."""
    assert parse_delivery_by("tomorrow afternoon", now=NOW)["at"] == "2026-10-12T14:00:00+05:30"
    assert parse_delivery_by("before midnight", now=NOW)["at"] is None    # left unread rather than guessed
    assert parse_delivery_by("tonight", now=NOW)["at"] == "2026-10-11T20:00:00+05:30"
    assert parse_delivery_by("tomorrow noon", now=NOW)["at"] == "2026-10-12T12:00:00+05:30"
