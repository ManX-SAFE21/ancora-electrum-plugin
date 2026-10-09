"""
Tests for the "delivery date already passed" message (CHANGELOG #75, BUG-4).

With a delivery date in the past, the Check said that an heir was "not
covered by the current will" and then "no delivery date left to build". Now
it names the real cause.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_msg_date_in_past.py
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.will import Will
from bal.gui.qt.dialogs import BalBuildWillDialog

NOW = 1791000000          # 2026-10-03 (UTC)
PAST = 1577836800         # 2020-01-01
FUTURE = 2011219200       # 2033-09-25


def test_past_absolute_date_is_reported():
    assert Will.past_delivery_date({"locktime": PAST}, now=NOW) == PAST


def test_future_or_relative_date_is_not_reported():
    assert Will.past_delivery_date({"locktime": FUTURE}, now=NOW) is None
    assert Will.past_delivery_date({"locktime": "1y"}) is None
    assert Will.past_delivery_date({"locktime": "30d"}) is None


def test_missing_or_broken_settings_never_raise():
    assert Will.past_delivery_date(None) is None
    assert Will.past_delivery_date({}) is None
    assert Will.past_delivery_date({"locktime": "garbage"}) is None


class _Dialog:
    """Just what the two methods under test use (no Qt widgets)."""

    _past_date_message = BalBuildWillDialog._past_date_message
    _build_failure_message = BalBuildWillDialog._build_failure_message

    def __init__(self, locktime, reason):
        self.bal_window = SimpleNamespace(
            will_settings={"locktime": locktime},
            heirs=SimpleNamespace(last_build_error=reason),
        )


def test_dialog_message_names_the_past_date():
    msg = _Dialog(PAST, "NO_FUTURE_DATE")._past_date_message()
    assert "2020-01-01" in msg and "already passed" in msg


def test_dialog_message_is_none_for_a_future_date():
    assert _Dialog(FUTURE, "NO_FUTURE_DATE")._past_date_message() is None


def test_build_row_no_longer_repeats_the_misleading_reason():
    """NO_FUTURE_DATE with a past date: just "Skipped" (cause shown above)."""
    assert _Dialog(PAST, "NO_FUTURE_DATE")._build_failure_message() == "Skipped"


def test_build_row_unchanged_for_a_future_date():
    msg = _Dialog(FUTURE, "NO_FUTURE_DATE")._build_failure_message()
    assert "No delivery date left to build" in msg


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All date-in-past message tests passed")
