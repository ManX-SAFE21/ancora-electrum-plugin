"""Remaining-time colours of the delivery date: the pure rules (CHANGELOG #124).

Owner decision 5 (2026-10-11): the delivery date is green with more than 12
months left, yellow below 12, red below 6 (or when the date has passed). The
two thresholds are plugin settings (ADVANCED only), NOT tied to the Check
Alive. Months are calendar months, the same arithmetic as the delivery date
(``bal.core.delivery.add_months``), so "in 2 years and 10 months" and the
colour never disagree.

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 -m pytest tests/test_core_delivery_colours.py
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core import delivery
from bal.core.delivery import (
    LEVEL_GREEN,
    LEVEL_PASSED,
    LEVEL_RED,
    LEVEL_YELLOW,
    add_months,
    delivery_level,
    months_between,
    warning_months,
)
from bal.core.summary import delivery_warning

DAY = 86400


def ts(y, m, d, hour=0):
    """UNIX time of ``y-m-d hour:00`` UTC."""
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


# ------------------------------------------------------------ months_between

@pytest.mark.parametrize("start, end, months", [
    (ts(2026, 10, 12), ts(2029, 8, 28), 34),       # the owner's will today
    (ts(2026, 1, 31), ts(2026, 2, 28), 1),         # clamped end of month
    (ts(2026, 1, 31), ts(2026, 2, 27), 0),
    (ts(2027, 1, 31), ts(2028, 2, 29), 13),        # leap year
    (ts(2026, 10, 12), ts(2026, 10, 12), 0),
    (ts(2026, 10, 12), ts(2026, 10, 11), 0),       # end before start
    (ts(2026, 10, 12, 18), ts(2027, 10, 12, 1), 12),  # UTC days, not hours
])
def test_months_between(start, end, months):
    """Whole calendar months, with the delivery date's own arithmetic."""
    assert months_between(start, end) == months


def test_months_between_matches_add_months():
    """``add_months(a, k) <= end < add_months(a, k + 1)`` for many dates."""
    a = ts(2026, 1, 1)
    for start_day in range(0, 400, 7):
        start = a + start_day * DAY
        for end_day in range(0, 900, 11):
            end = start + end_day * DAY
            k = months_between(start, end)
            assert add_months(start, k) <= end
            assert add_months(start, k + 1) > delivery.utc_midnight(end)


# ----------------------------------------------------------- warning_months

@pytest.mark.parametrize("yellow, red, expected", [
    (12, 6, (12, 6)),
    (36, 35, (36, 35)),
    (24, 3, (24, 3)),
    ("24", "3", (24, 3)),        # numeric text from a hand-edited config
    (6, 6, (12, 6)),             # red not below yellow
    (6, 12, (12, 6)),
    (0, 0, (12, 6)),
    (121, 6, (12, 6)),           # above ten years
    (12, 0, (12, 6)),
    (12.5, 6, (12, 6)),          # not whole months
    (True, 6, (12, 6)),
    (None, 6, (12, 6)),
    ("abc", 6, (12, 6)),
])
def test_warning_months(yellow, red, expected):
    """A broken pair never hides the warning: both defaults are used."""
    assert warning_months(yellow, red) == expected


# ----------------------------------------------------------- delivery_level

NOW = ts(2026, 10, 12, 4)     # the owner's test, 06:52 in Rome


def test_the_owner_will_is_green():
    """2029-08-28, about 34 months away: green with the defaults."""
    assert delivery_level(ts(2029, 8, 28), NOW) == LEVEL_GREEN


@pytest.mark.parametrize("yellow, red, level", [
    (36, 35, LEVEL_RED),          # the owner's ZIP-2 test: red 35
    (36, 6, LEVEL_YELLOW),        # yellow 36
    (35, 6, LEVEL_YELLOW),        # 34 months and 16 days left: below 35
    (34, 6, LEVEL_GREEN),         # not below 34
])
def test_the_owner_will_with_other_thresholds(yellow, red, level):
    """The owner's settings test of ZIP-2."""
    assert delivery_level(ts(2029, 8, 28), NOW, yellow, red) == level


def test_boundaries_are_calendar_months_from_today():
    """Exactly ``red`` months after today is yellow; the day before is red;
    exactly ``yellow`` months is green; the day before is yellow."""
    today = delivery.utc_midnight(NOW)
    six, twelve = add_months(today, 6), add_months(today, 12)
    assert delivery_level(six, NOW) == LEVEL_YELLOW
    assert delivery_level(six - DAY, NOW) == LEVEL_RED
    assert delivery_level(twelve, NOW) == LEVEL_GREEN
    assert delivery_level(twelve - DAY, NOW) == LEVEL_YELLOW


def test_end_of_month_boundary():
    """Today 2026-08-31: six months later is 2027-02-28 (clamped)."""
    now = ts(2026, 8, 31, 10)
    assert delivery_level(ts(2027, 2, 27), now) == LEVEL_RED
    assert delivery_level(ts(2027, 2, 28), now) == LEVEL_YELLOW


def test_passed_and_today():
    """A date not after now is passed; later today is still red."""
    assert delivery_level(NOW, NOW) == LEVEL_PASSED
    assert delivery_level(NOW - DAY, NOW) == LEVEL_PASSED
    assert delivery_level(NOW + 3600, NOW) == LEVEL_RED


# --------------------------------------------------- texts of the tooltips

def test_tooltips():
    """Green has none; yellow and red name their threshold; passed says the
    heirs can already receive the bitcoin (texts approved by the owner)."""
    assert delivery_warning(ts(2029, 8, 28), NOW) == (LEVEL_GREEN, "")
    level, tip = delivery_warning(ts(2027, 6, 1), NOW)
    assert (level, tip) == (LEVEL_YELLOW, "Less than 12 months to the delivery.")
    level, tip = delivery_warning(ts(2027, 1, 1), NOW, (24, 4))
    assert level == LEVEL_RED
    assert tip == ("Less than 4 months to the delivery: if you are still "
                   "here, choose a later date.")
    level, tip = delivery_warning(ts(2026, 10, 1), NOW)
    assert (level, tip) == (
        LEVEL_PASSED,
        "The delivery date has passed: the heirs can already receive the bitcoin.")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
