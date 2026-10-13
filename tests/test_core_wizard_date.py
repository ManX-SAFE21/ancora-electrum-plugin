"""The date logic of the BASIC wizard's page "When" (CHANGELOG #134).

``bal.core.wizard_date``: the quick choices, the local boxes read in the
time zone of the CHOSEN date (summer / winter time), the day change between
local time and UTC, the month length, the past refused, "Leave as it is"
and the later-than-the-will-in-force check. Fixed zones (``zoneinfo``), so
the result does not depend on the machine.

Run:
    PYTHONPATH=electrum-src python3 -m pytest tests/test_core_wizard_date.py -q
"""

import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core import delivery, wizard_date

ROME = ZoneInfo("Europe/Rome")
NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")


def ts(y, m, d, hour=0, minute=0):
    """UTC timestamp of a calendar moment."""
    return int(datetime(y, m, d, hour, minute, tzinfo=timezone.utc).timestamp())


NOW = ts(2026, 10, 13, 12)


def test_quick_choices_are_midnight_utc_plus_months():
    """1, 2 and 5 years: 00:00 UTC of today + 12/24/60 months; 2 years is
    the default date of a new wallet."""
    assert wizard_date.quick_date(NOW, 12) == ts(2027, 10, 13)
    assert wizard_date.quick_date(NOW, 24) == ts(2028, 10, 13)
    assert wizard_date.quick_date(NOW, 60) == ts(2031, 10, 13)
    assert wizard_date.quick_date(NOW, 24) == delivery.default_delivery(NOW)
    assert wizard_date.QUICK_MONTHS == (12, 24, 60)


def test_rome_summer_uses_the_offset_of_the_chosen_date():
    """11 October 2028 01:00 in Rome (summer time, UTC+2) is 10 October
    23:00 UTC: the day before in UTC."""
    t = wizard_date.local_to_timestamp(2028, 10, 11, 1, ROME)
    assert t == ts(2028, 10, 10, 23)
    assert wizard_date.offset_text(t, ROME) == "UTC+2"
    assert wizard_date.day_shift(t, ROME) == -1
    assert wizard_date.local_parts(t, ROME) == (2028, 10, 11, 1)


def test_rome_winter_is_one_hour_ahead():
    """15 January 2029 10:00 in Rome (winter, UTC+1) is 09:00 UTC, the
    same day, even when chosen in summer."""
    t = wizard_date.local_to_timestamp(2029, 1, 15, 10, ROME)
    assert t == ts(2029, 1, 15, 9)
    assert wizard_date.offset_text(t, ROME) == "UTC+1"
    assert wizard_date.day_shift(t, ROME) == 0


def test_zone_behind_utc_gives_the_day_after():
    """New York 22:00 on 1 March 2028 (UTC-5) is 03:00 UTC on 2 March."""
    t = wizard_date.local_to_timestamp(2028, 3, 1, 22, NEW_YORK)
    assert t == ts(2028, 3, 2, 3)
    assert wizard_date.offset_text(t, NEW_YORK) == "UTC-5"
    assert wizard_date.day_shift(t, NEW_YORK) == 1


def test_half_hour_zone_and_utc():
    """The offset text with minutes, and plain "UTC" for UTC."""
    t = ts(2028, 10, 10)
    assert wizard_date.offset_text(t, KOLKATA) == "UTC+5:30"
    assert wizard_date.offset_text(t, timezone.utc) == "UTC"
    assert wizard_date.local_parts(t, KOLKATA) == (2028, 10, 10, 5)


def test_day_is_clamped_to_the_month():
    """31 in a 30-day month becomes 30; 29 or 31 February becomes 28 or 29."""
    assert wizard_date.clamp_day(2028, 4, 31) == 30
    assert wizard_date.clamp_day(2027, 2, 31) == 28
    assert wizard_date.clamp_day(2028, 2, 31) == 29
    assert wizard_date.days_in_month(2028, 2) == 29
    assert wizard_date.local_to_timestamp(2028, 4, 31, 12, timezone.utc) == ts(2028, 4, 30, 12)
    # 29 February exists in a leap year.
    assert wizard_date.local_to_timestamp(2028, 2, 29, 0, ROME) == ts(2028, 2, 28, 23)


def test_past_and_now_are_refused():
    """A moment not after now, a block height or beyond 2106 is refused."""
    assert not wizard_date.is_valid_choice(NOW, NOW)
    assert not wizard_date.is_valid_choice(NOW - 3600, NOW)
    assert wizard_date.is_valid_choice(NOW + 3600, NOW)
    assert not wizard_date.is_valid_choice(None, NOW)
    assert not wizard_date.is_valid_choice(800_000, NOW)
    assert not wizard_date.is_valid_choice(delivery.LOCKTIME_MAX + 1, NOW)
    last = wizard_date.local_to_timestamp(wizard_date.MAX_YEAR, 12, 31, 23, ROME)
    assert wizard_date.is_valid_choice(last, NOW)


def test_leave_as_it_is_only_with_a_will_and_a_future_date():
    """Offered for a wallet with a will and a stored future date."""
    future = ts(2032, 9, 23)
    assert wizard_date.keep_offered(future, True, NOW)
    assert not wizard_date.keep_offered(future, False, NOW)
    assert not wizard_date.keep_offered(ts(2020, 1, 1), True, NOW)
    assert not wizard_date.keep_offered("1054d", True, NOW)


def test_later_than_the_will_in_force():
    """Only a date after the will in force is "later"; no will, no note."""
    d0 = ts(2032, 9, 23)
    assert wizard_date.is_later_than_in_force(d0 + 1, d0)
    assert not wizard_date.is_later_than_in_force(d0, d0)
    assert not wizard_date.is_later_than_in_force(d0 - 86400, d0)
    assert not wizard_date.is_later_than_in_force(d0 + 1, None)


def test_dates_after_2038():
    """No 32-bit time_t limit: 2100 is converted both ways."""
    t = wizard_date.local_to_timestamp(2100, 7, 1, 12, ROME)
    assert t == ts(2100, 7, 1, 10)
    assert wizard_date.local_parts(t, ROME) == (2100, 7, 1, 12)


def test_system_zone_round_trip():
    """``tz=None`` (the computer's zone): the boxes give back the moment."""
    t = wizard_date.local_to_timestamp(2028, 10, 11, 1)
    assert wizard_date.local_parts(t) == (2028, 10, 11, 1)
    assert wizard_date.local_zone() is None
