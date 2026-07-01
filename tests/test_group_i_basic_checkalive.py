"""
Tests for the "BASIC mode dynamic Check Alive" fix (Group I).

Background (reported by the owner): with the plugin left in BASIC mode (the
default), the "Check Alive" (threshold) field is hidden from the user and
stays stuck at its old/default value (roughly "today + 11 months", i.e. one
year minus the default 30-day margin). If the user then anticipates the
delivery time (locktime) to something earlier than that stale threshold - e.g.
"in 1 month" - two different checks that compare locktime against
``date_to_check`` (the resolved threshold) would incorrectly treat the will as
"expired"/"invalid", even though the whole Check Alive concept is supposed to
be inert in BASIC mode.

The fix (``BalWindow.compute_date_to_check``) makes ``date_to_check`` track
the delivery time LIVE in BASIC mode, placed a fixed 2-hour margin before it,
so it is always < locktime by construction. In ADVANCED mode nothing changes:
the stored threshold is used as-is.

These tests call the real production method directly (no GUI/Electrum wallet
needed - it is a plain ``@staticmethod``), so they exercise the exact code
used at runtime rather than a re-implementation.

Run:
    PYTHONPATH=electrum-src python3 -m pytest tests/test_group_i_basic_checkalive.py -q
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.gui.qt.window import BalWindow  # noqa: E402  (path insert above)


OFFSET = BalWindow.BASIC_MODE_CHECK_ALIVE_OFFSET_SECONDS


def test_offset_is_two_hours():
    """Owner-approved margin: exactly 2 hours."""
    assert OFFSET == 2 * 60 * 60


def _relative_days_to_midnight_timestamp(days):
    """Reproduce Util.parse_locktime_string's own normalisation: "now + N
    days", truncated to midnight. Used to compute the expected value in
    tests without duplicating the parsing logic itself."""
    from datetime import datetime, timedelta

    now = datetime.now()
    return (
        (now + timedelta(days=days))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )


def test_basic_mode_relative_locktime_one_year():
    """BASIC + default "1y" delivery: date_to_check tracks it, 2h earlier."""
    result = BalWindow.compute_date_to_check(True, "1y", "30d")
    expected_locktime = _relative_days_to_midnight_timestamp(365)
    assert abs(result - (expected_locktime - OFFSET)) < 5


def test_basic_mode_anticipated_delivery_stays_consistent():
    """BASIC + delivery anticipated to 1 month: date_to_check follows it,
    NOT the old ~11-month threshold - this is the exact bug scenario
    reported by the owner."""
    stale_threshold = "30d"  # would resolve close to the OLD 1-year locktime
    anticipated_locktime = "30d"  # user moved delivery to ~1 month from now
    result = BalWindow.compute_date_to_check(
        True, anticipated_locktime, stale_threshold
    )
    locktime_ts = _relative_days_to_midnight_timestamp(30)
    # date_to_check must be (delivery - 2h), always strictly before delivery.
    assert result < locktime_ts
    assert abs((locktime_ts - OFFSET) - result) < 5


def test_basic_mode_date_to_check_always_before_locktime():
    """Regression guard for the reported bug: whatever the delivery date is
    (even very close to "now"), date_to_check must stay before it."""
    for relative_locktime in ("1d", "7d", "30d", "90d", "365d"):
        result = BalWindow.compute_date_to_check(True, relative_locktime, "30d")
        parsed_locktime = _relative_days_to_midnight_timestamp(
            int(relative_locktime[:-1])
        )
        assert result < parsed_locktime, (
            f"date_to_check ({result}) should be before locktime "
            f"({parsed_locktime}) for locktime={relative_locktime}"
        )


def test_advanced_mode_uses_stored_threshold_unchanged():
    """ADVANCED mode: behaviour must stay exactly as before this fix - the
    stored threshold is used as-is, regardless of the locktime value."""
    absolute_threshold = time.time() + 5 * 86400  # arbitrary user-chosen value
    result = BalWindow.compute_date_to_check(False, "30d", absolute_threshold)
    assert abs(result - absolute_threshold) < 1


def test_basic_mode_falls_back_on_unparsable_locktime():
    """If the locktime can't be parsed for any reason, BASIC mode must not
    crash: it falls back to the stored threshold, same as ADVANCED."""
    absolute_threshold = time.time() + 5 * 86400
    result = BalWindow.compute_date_to_check(
        True, {"not": "a valid locktime"}, absolute_threshold
    )
    assert abs(result - absolute_threshold) < 1


if __name__ == "__main__":
    test_offset_is_two_hours()
    test_basic_mode_relative_locktime_one_year()
    test_basic_mode_anticipated_delivery_stays_consistent()
    test_basic_mode_date_to_check_always_before_locktime()
    test_advanced_mode_uses_stored_threshold_unchanged()
    test_basic_mode_falls_back_on_unparsable_locktime()
    print("All test_group_i_basic_checkalive tests passed.")
