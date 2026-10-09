"""
Tests for the heirs' amounts (CHANGELOG #76).

* ``Heirs.prepare_lists`` now STOPS (FixedAmountsExceedBalanceException) when
  the fixed amounts do not fit in the balance left for the heirs, or leave
  nothing for the percentage heirs. Before, it scaled the fixed amounts down
  and silently left the percentage heirs out, or with fixed heirs only paid
  them about 71 % and returned the rest to the wallet as change.
* ``Will.describe_amounts`` explains what the build does with the amounts,
  and ``BalBuildWillDialog._amounts_message`` turns it into the sentence
  approved by the owner (numbers with the separators of the language).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_core_amounts_explained.py
"""

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal import i18n
from bal.core.heirs import FixedAmountsExceedBalanceException, Heirs
from bal.core.will import Will
from bal.gui.qt.dialogs import BalBuildWillDialog

A1 = "bcrt1q087zm5m3jrhfg78zflqefhcr9heh4c98kzmvhp"
A2 = "bcrt1q08z5t4x74u2883sx2qwsmzk2hj8e5n7z83e4vy"


def _wallet():
    w = MagicMock()
    w.dust_threshold.return_value = 500
    return w


def _heirs(alice, bob):
    h = Heirs.__new__(Heirs)
    h.update({"alice": [A1, alice, "30d"], "bob": [A2, bob, "30d"]})
    return h


def _utxos(total):
    u = MagicMock()
    u.value_sats.return_value = total
    return [u]


def _raises_fixed(heirs, balance):
    try:
        heirs.prepare_lists(balance, 100, _wallet())
    except FixedAmountsExceedBalanceException:
        return True
    return False


# --------------------------------------------------------------- prepare_lists

def test_fixed_larger_than_balance_with_a_percentage_heir_stops():
    assert _raises_fixed(_heirs(2_000_000, "50%"), 1_000_000)


def test_fixed_heirs_only_larger_than_balance_stops():
    """Before #76: ~71 % to the heirs, the rest back to the wallet as change."""
    assert _raises_fixed(_heirs(800_000, 600_000), 1_000_000)


def test_fixed_equal_to_the_rest_leaves_nothing_for_percentages_stops():
    assert _raises_fixed(_heirs(999_900, "50%"), 1_000_000)


def test_fixed_that_fits_still_builds():
    locktimes, _ = _heirs(100_000, "50%").prepare_lists(1_000_000, 100, _wallet())
    assert locktimes


def test_fixed_only_that_fits_exactly_still_builds():
    locktimes, _ = _heirs(500_000, 499_900).prepare_lists(1_000_000, 100, _wallet())
    assert locktimes


# ---------------------------------------------------------- describe_amounts

def _describe(alice, bob, balance=1_000_000):
    return Will.describe_amounts(_heirs(alice, bob), {}, _utxos(balance), time.time(), 500)


def test_percentages_below_100_are_rescaled():
    info = _describe("50%", "40%")
    assert info["kind"] == "percent" and info["total"] == 90
    assert [(n, round(s, 1)) for n, s in info["shares"]] == [("alice", 55.6), ("bob", 44.4)]
    assert info["with_fixed"] is False


def test_percentages_above_100_are_rescaled():
    info = _describe("60%", "60%")
    assert info["kind"] == "percent" and info["total"] == 120
    assert [round(s, 1) for _n, s in info["shares"]] == [50.0, 50.0]


def test_percentages_with_a_fixed_heir():
    info = _describe(100_000, "50%")
    assert info["kind"] == "percent" and info["with_fixed"] is True
    assert info["shares"] == [("bob", 100.0)]


def test_nothing_to_explain():
    assert _describe(100_000, "100%") is None
    assert _describe("60%", "40%") is None


def test_fixed_amounts_exceeding_the_balance():
    info = _describe(2_000_000, "50%")
    assert info == {"kind": "fixed_exceeds", "fixed": 2_000_000,
                    "balance": 1_000_000, "percent_heirs": ["bob"]}
    info = _describe(800_000, 600_000)
    assert info["kind"] == "fixed_exceeds" and info["percent_heirs"] == []


def test_describe_never_raises():
    assert Will.describe_amounts(None, {}, [], 0, 500) is None


# ------------------------------------------------------------ dialog message

def test_message_english_numbers():
    msg = BalBuildWillDialog._amounts_message(_describe("50%", "40%"))
    assert "add up to 90%" in msg and "alice 55.6%, bob 44.4%" in msg
    msg = BalBuildWillDialog._amounts_message(_describe(2_000_000, "50%"))
    assert "2,000,000 sat" in msg and "1,000,000 sat" in msg and "(bob)" in msg


def test_message_italian_numbers():
    """Italian writes 55,6% and 2.000.000 (the text itself is translated
    by the catalog; here only the number format is checked)."""
    no_catalog = SimpleNamespace(read_file=MagicMock(side_effect=KeyError))
    i18n.set_language(no_catalog, "it_IT")
    try:
        msg = BalBuildWillDialog._amounts_message(_describe("50%", "40%"))
        assert "alice 55,6%, bob 44,4%" in msg
        msg = BalBuildWillDialog._amounts_message(_describe(2_000_000, "50%"))
        assert "2.000.000 sat" in msg
    finally:
        i18n.set_language(no_catalog, None)


def test_no_info_no_message():
    assert BalBuildWillDialog._amounts_message(None) is None


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All amounts tests passed")
