"""
Tests for the "adjust the amounts" warning (CHANGELOG #76, BUG-5).

The warning flashed in 12 Checks of the scenario matrix without any amount
change: with a delivery date in the past (heirs left out, 0 % instead of
100 %) and right after a Cancel (no spendable coin visible for a moment).
It is now shown only for a real amount problem.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_msg_amounts_false_alarm.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.gui.qt.dialogs import BalBuildWillDialog

PAST = 1577836800         # 2020-01-01
FUTURE = 2011219200       # 2033-09-25


def _utxo(value):
    u = MagicMock()
    u.value_sats.return_value = value
    return u


class _Dialog:
    _past_date_message = BalBuildWillDialog._past_date_message
    _amount_warning_is_false_alarm = BalBuildWillDialog._amount_warning_is_false_alarm

    def __init__(self, locktime):
        self.bal_window = SimpleNamespace(will_settings={"locktime": locktime})


def test_real_amount_problem_still_warns():
    """Future date and coins available: the warning is genuine."""
    assert _Dialog(FUTURE)._amount_warning_is_false_alarm([_utxo(1_000_000)]) is False


def test_past_date_is_not_an_amount_problem():
    assert _Dialog(PAST)._amount_warning_is_false_alarm([_utxo(1_000_000)]) is True


def test_no_visible_coin_is_not_an_amount_problem():
    assert _Dialog(FUTURE)._amount_warning_is_false_alarm([]) is True
    assert _Dialog(FUTURE)._amount_warning_is_false_alarm(None) is True


def test_errors_fall_back_to_showing_the_warning():
    broken = MagicMock()
    broken.value_sats.side_effect = RuntimeError("boom")
    assert _Dialog(FUTURE)._amount_warning_is_false_alarm([broken]) is False


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All amounts false-alarm tests passed")
