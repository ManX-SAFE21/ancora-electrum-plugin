"""The date logic of the BASIC wizard's first page, "When" (CHANGELOG #134).

The page offers quick choices ("In 1 year", "In 2 years", "In 5 years"),
"Leave as it is" for a wallet that already has a will, and "Choose the
date…": four boxes Year | Month | Day | Hour in the COMPUTER'S LOCAL TIME.
The will itself stores a UTC timestamp (the nLockTime the will-executors
and the Bitcoin network read), so this module converts between the two:

* the quick choices are 00:00 UTC of today + N calendar months, the same
  month arithmetic as every other delivery date (:func:`quick_date`, the
  rule of ``delivery.default_delivery``);
* the boxes are read in the local time zone of the CHOSEN date, not of
  today: a date in summer gets the summer offset even when the owner
  chooses it in winter (:func:`local_to_timestamp`);
* the texts under the boxes show the same moment both ways, and say when
  the UTC day is not the local day (:func:`day_shift`).

Pure: standard library only, no Qt, the clock is never read here (every
"now" comes from the caller). The time zone is a parameter ``tz``: None is
the computer's own zone (Python's ``astimezone()`` reads it from the
operating system), a ``zoneinfo.ZoneInfo`` gives a fixed zone (the tests
use "Europe/Rome"). :func:`local_zone` is what the page uses; the tests
replace it.

Dates after 2038: the moments are built as "1970-01-01 UTC + seconds",
never with ``datetime.fromtimestamp`` (it fails after 2038 where
``time_t`` is 32-bit, see ``delivery.utc_midnight``).
"""

import calendar
import math
from datetime import datetime, timedelta, timezone

from . import delivery

#: The quick choices of the page, in calendar months (1, 2 and 5 years).
QUICK_MONTHS = (12, 24, 60)

#: The default quick choice when the wallet has no will yet: the same
#: default as a new wallet's delivery date (``delivery.DEFAULT_MONTHS``).
DEFAULT_QUICK_MONTHS = delivery.DEFAULT_MONTHS

#: The last year the Year box offers. The nLockTime is an unsigned 32-bit
#: number of seconds (``delivery.LOCKTIME_MAX``, 7 February 2106 06:28
#: UTC): every hour of 2105 fits, 2106 would not.
MAX_YEAR = 2105

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def local_zone():
    """The time zone of the page's boxes: None, the computer's own zone.

    A function so the tests can give a fixed zone (``ZoneInfo``) instead of
    depending on the machine that runs them.
    """
    return None


def quick_date(now, months):
    """00:00 UTC of today (UTC day of ``now``) + ``months`` calendar months.

    The rule of the delivery date of a new wallet
    (``delivery.default_delivery``), so "In 2 years" gives exactly the date
    a new wallet starts with.
    """
    return delivery.add_months(delivery.utc_midnight(now), months)


def utc_moment(ts):
    """The aware UTC ``datetime`` of timestamp ``ts`` (2038-safe)."""
    return _EPOCH + timedelta(seconds=int(math.floor(ts)))


def local_moment(ts, tz=None):
    """The aware ``datetime`` of ``ts`` in zone ``tz`` (None: the computer's).

    Its ``utcoffset()`` is the offset of THAT moment (summer or winter
    time), which is what the page shows next to the local time.
    """
    return utc_moment(ts).astimezone(tz)


def days_in_month(year, month):
    """The number of days of ``month`` (1..12) in ``year`` (28..31)."""
    return calendar.monthrange(year, month)[1]


def clamp_day(year, month, day):
    """``day`` kept inside ``month``: 31 becomes 30 in April, 28 or 29 in
    February (the Day box follows the month length)."""
    return max(1, min(int(day), days_in_month(year, month)))


def local_to_timestamp(year, month, day, hour, tz=None):
    """The UTC timestamp of ``year-month-day hour:00`` in zone ``tz``.

    The offset is the one of the chosen date (its own summer or winter
    time), never today's. ``tz`` None reads the date as the computer's local
    time (Python's naive ``astimezone()``, which asks the operating system
    for the offset of that date). The day is clamped to the month first
    (:func:`clamp_day`).

    An hour that does not exist locally (the hour skipped when the clocks
    go forward) or that exists twice (when they go back) is read with
    Python's default ``fold=0``: the offset in force before the change. The
    page then shows the UTC time it gives, so the owner sees what is saved.
    """
    day = clamp_day(year, month, day)
    if tz is None:
        moment = datetime(year, month, day, hour).astimezone()
    else:
        moment = datetime(year, month, day, hour, tzinfo=tz)
    return int((moment - _EPOCH).total_seconds())


def local_parts(ts, tz=None):
    """``(year, month, day, hour)`` of ``ts`` in zone ``tz``: what the four
    boxes show. Minutes are dropped: the boxes hold whole hours."""
    m = local_moment(ts, tz)
    return m.year, m.month, m.day, m.hour


def offset_text(ts, tz=None):
    """The UTC offset of ``ts`` in zone ``tz``: "UTC", "UTC+2", "UTC-3",
    "UTC+5:30" (not translated: a standard notation)."""
    offset = local_moment(ts, tz).utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    if rest:
        return "UTC{}{}:{:02d}".format(sign, hours, rest)
    return "UTC{}{}".format(sign, hours)


def day_shift(ts, tz=None):
    """The UTC day of ``ts`` compared with its local day, in days.

    -1: in UTC it is still the day before (a zone ahead of UTC, e.g. 01:00
    in Rome is 23:00 UTC of the day before); +1: in UTC it is already the
    day after (a zone behind UTC); 0: the same day.
    """
    local = local_moment(ts, tz).date()
    utc = utc_moment(ts).date()
    return (utc - local).days


def is_valid_choice(ts, now):
    """True when ``ts`` can be the delivery date: after ``now`` and a
    date nLockTime (``delivery.LOCKTIME_MIN`` .. ``LOCKTIME_MAX``).

    A date or hour that is not after now is refused by the page (Next
    disabled): a will delivered at once is not what the owner wants, and a
    past date would make the build stop anyway.
    """
    if ts is None:
        return False
    if not delivery.LOCKTIME_MIN <= ts <= delivery.LOCKTIME_MAX:
        return False
    return ts > now


def keep_offered(stored, has_will, now):
    """True when the page offers (and pre-selects) "Leave as it is".

    Only for a wallet that already has a will (``has_will``: will items are
    stored) and a stored delivery date that is a date after ``now``: that is
    the date in force or the date already chosen for it. A wallet without a
    will starts from "In 2 years", like today's default; a date already
    passed cannot be kept (the page would refuse it).
    """
    locktime = delivery.as_delivery(stored)
    return bool(has_will) and locktime is not None and locktime > now


def is_later_than_in_force(ts, in_force_date):
    """True when ``ts`` moves the will in force LATER (``in_force_date``:
    its delivery date, None when no will is in force).

    Moving it later needs the paid invalidation of that will; moving it
    earlier is free (the plugin pre-empts it with a will dated earlier).
    """
    return in_force_date is not None and ts is not None and ts > in_force_date
