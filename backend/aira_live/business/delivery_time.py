"""Spoken delivery times and order notes. OWNER: Kanish.

``parse_delivery_by("6 am tomorrow")`` keeps the owner's words and adds an IST datetime when the words are plain
enough to read deterministically (day word + clock time, "in 2 hours", a weekday). Anything else keeps the words
with ``at: None``: the vendor still sees what the owner said, and nothing is invented. ``clean_note`` caps and
sanitises the free text that goes into the vendor message.
"""
import re
from datetime import datetime, timedelta

from aira_live.business.clock import IST
from aira_live.business.text import normalize_digits

NOTE_MAX = 140
_NONE_WORDS = {"no", "none", "nothing", "no time", "not needed", "no note", "nil", "nope", "skip",
               "இல்லை", "வேண்டாம்", "नहीं", "कुछ नहीं"}
# day words → days from today
_DAY_WORDS = [
    (("day after tomorrow", "நாளை மறுநாள்", "परसों"), 2),
    (("tomorrow", "tmrw", "tomorow", "நாளை", "நாளைக்கு", "कल"), 1),
    (("today", "tonight", "இன்று", "இன்னைக்கு", "இன்னிக்கு", "आज"), 0),
]
_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3,
             "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
# part-of-day words → (default hour, am/pm hint for a bare clock number)
_PART_WORDS = {
    "morning": (7, "am"), "காலை": (7, "am"), "காலையில்": (7, "am"), "सुबह": (7, "am"),
    "noon": (12, "pm"), "midday": (12, "pm"),
    "afternoon": (14, "pm"), "மதியம்": (14, "pm"), "दोपहर": (14, "pm"),
    "evening": (18, "pm"), "மாலை": (18, "pm"), "மாலையில்": (18, "pm"), "शाम": (18, "pm"),
    "night": (20, "pm"), "tonight": (20, "pm"), "இரவு": (20, "pm"), "रात": (20, "pm"),
}
_CLOCK = re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|o'?clock)?(?!\d)")
_RELATIVE = re.compile(r"in\s+(\d+)\s*(hour|hours|hr|hrs|minute|minutes|min|mins)\b")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _is_none(text: str) -> bool:
    return text.strip().strip(".!,").lower() in _NONE_WORDS


def parse_delivery_by(text: str | None, now: datetime | None = None) -> dict | None:
    """The owner's delivery time → {"text": words, "at": ISO datetime | ISO date | None}; None when he said no time."""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw or _is_none(raw):
        return None
    now = (now or datetime.now(IST)).astimezone(IST)
    s = normalize_digits(raw).lower()

    rel = _RELATIVE.search(s)
    if rel:
        n = int(rel.group(1))
        delta = timedelta(hours=n) if rel.group(2).startswith("h") else timedelta(minutes=n)
        return {"text": raw, "at": (now + delta).replace(second=0, microsecond=0).isoformat()}

    days: int | None = None
    for words, offset in _DAY_WORDS:
        if any(w in s for w in words):
            days = offset
            break
    if days is None:
        for word, wd in _WEEKDAYS.items():
            if re.search(rf"\b{word}\b", s):
                days = (wd - now.weekday()) % 7 or 7
                break

    hint: str | None = None
    part_hour: int | None = None
    for word, (hour, ampm) in _PART_WORDS.items():
        if word in s:
            part_hour, hint = hour, ampm
            break

    clock = _CLOCK.search(s)
    hour = minute = None
    if clock:
        h, m = int(clock.group(1)), int(clock.group(2) or 0)
        suffix = (clock.group(3) or "").replace(".", "")
        if 0 <= h <= 23 and 0 <= m <= 59:
            if suffix in ("am", "pm") or (h > 12 and not suffix) or clock.group(2):
                ampm = suffix if suffix in ("am", "pm") else None
            else:
                ampm = hint or ("am" if 7 <= h <= 11 else "pm")   # bare "6" at a dairy: 7–11 morning, else pm
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            hour, minute = h, m
    if hour is None and part_hour is not None:
        hour, minute = part_hour, 0

    if hour is None and days is None:
        return {"text": raw, "at": None}
    if hour is None:
        return {"text": raw, "at": (now + timedelta(days=days)).date().isoformat()}
    if days is None:                               # a time alone: today if still ahead, else tomorrow
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
    else:
        at = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return {"text": raw, "at": at.isoformat()}


def _at_words(at: str) -> str:
    """'2026-10-12T06:00:00+05:30' → 'Mon 12 Oct, 6:00 am'; '2026-10-12' → 'Mon 12 Oct'."""
    try:
        dt = datetime.fromisoformat(at)
    except ValueError:
        return ""
    day = f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}"
    if len(at) == 10:
        return day
    dt = dt.astimezone(IST)
    hour12 = dt.hour % 12 or 12
    return f"{day}, {hour12}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def delivery_by_words(delivery_by: dict | None) -> str:
    """The owner's words, plus the read date in brackets when there is one: '6 am tomorrow (Mon 12 Oct, 6:00 am)'."""
    if not delivery_by or not delivery_by.get("text"):
        return ""
    words = _at_words(delivery_by["at"]) if delivery_by.get("at") else ""
    return f"{delivery_by['text']} ({words})" if words else str(delivery_by["text"])


def clean_note(text: str | None) -> str | None:
    """Short free text for the vendor: whitespace collapsed, control characters dropped, capped at NOTE_MAX."""
    s = re.sub(r"\s+", " ", _CONTROL.sub(" ", str(text or ""))).strip()
    if not s or _is_none(s):
        return None
    return s[:NOTE_MAX].rstrip()
