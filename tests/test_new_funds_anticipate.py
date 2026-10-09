"""
Funds received: the new will is dated one day earlier (CHANGELOG #91).

When funds arrive, the Check (or the wallet close) builds a new will that
spends the old coins plus the new ones. With a will-executor, BAL kept the
old date: the executor then held two wills executable on the same day, and
if the old one were mined the new coins would never reach the heirs.

* ``Will.check_anticipate``: same executor, same heirs and fees, more coins
  -> one day earlier; identical coins, fewer coins, another executor -> no
  change (as before);
* ``Will.find_same_date_copies``: finds the state left by wills built before
  the fix (the Check then rebuilds, one day earlier);
* ``will_change_message`` says why.

The whole flow (build, sign, send, verify, the old wills "Replaced", the
first executable will spending every coin) is checked by the scenario
harness and on regtest, outside the repository.

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 tests/test_new_funds_anticipate.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.messages import will_change_message
from bal.core.util import Util
from bal.core.will import NotCompleteWillException, SameDateCopiesException, Will

DELIVERY = 1979510400                    # 2032-09-23 00:00 UTC
DAY = 86400
NO_CONSTRAINT = 4294967295 + 1
WE0 = "https://we0.test"
WE1 = "https://we1.test"
HEIRS = {
    "alice": ["tb1qalice", "60%", "4y", 600_000],
    "bob": ["tb1qbob", "40%", "4y", 400_000],
}


class _Input:
    def __init__(self, coin):
        self.prevout = self
        self._coin = coin

    def to_str(self):
        return self._coin


class _Tx:
    def __init__(self, coins, locktime):
        self._coins = list(coins)
        self.locktime = locktime

    def inputs(self):
        return [_Input(c) for c in self._coins]


class _Item:
    """The parts of a WillItem that the rule and the detector read."""

    def __init__(self, coins, locktime=DELIVERY, url=WE0, fee=13, base_fee=1000,
                 heirs=None, valid=True):
        self.tx = _Tx(coins, locktime)
        self.heirs = dict(heirs or HEIRS)
        self.we = {"url": url, "base_fee": base_fee} if url else None
        self.tx_fees = fee
        self._valid = valid

    def get_status(self, name):
        return self._valid if name == "VALID" else False


def _one_day_earlier(ts):
    return int(Util.anticipate_locktime(ts, days=1))


# ------------------------------------------------------------ the rule

def test_new_funds_same_executor_one_day_earlier():
    old = _Item(["c1:0"])
    new = _Item(["c1:0", "c2:0"])
    assert int(Will.check_anticipate(old, new)) == DELIVERY - DAY
    assert _one_day_earlier(DELIVERY) == DELIVERY - DAY


def test_identical_copy_keeps_its_date():
    old = _Item(["c1:0", "c2:0"])
    new = _Item(["c2:0", "c1:0"])                        # same coins, any order
    assert Will.check_anticipate(old, new) == NO_CONSTRAINT


def test_fewer_coins_and_other_executor_unchanged():
    # A coin was spent: the old will cannot be executed any more anyway.
    assert Will.check_anticipate(_Item(["c1:0", "c2:0"]), _Item(["c1:0"])) == NO_CONSTRAINT
    # Another executor: its copy is not a competitor (same coins, its own fee).
    assert Will.check_anticipate(
        _Item(["c1:0"], url=WE0), _Item(["c1:0", "c2:0"], url=WE1)
    ) == NO_CONSTRAINT


def test_fee_change_still_one_day_earlier():
    """The rules that were there before #91 are unchanged."""
    old = _Item(["c1:0"], fee=13)
    new = _Item(["c1:0"], fee=14)
    assert int(Will.check_anticipate(old, new)) == DELIVERY - DAY


def test_already_earlier_new_will_is_left_alone():
    old = _Item(["c1:0"])
    new = _Item(["c1:0", "c2:0"], locktime=DELIVERY - 2 * DAY)
    assert Will.check_anticipate(old, new) == NO_CONSTRAINT


# -------------------------------------------------------- the detector

def test_detector_finds_the_risky_state():
    will = {"old": _Item(["c1:0"]), "new": _Item(["c1:0", "c2:0"])}
    assert Will.find_same_date_copies(will) == WE0
    # The incomplete will dated EARLIER than the complete one: worse, found too.
    will = {"old": _Item(["c1:0"], locktime=DELIVERY - DAY),
            "new": _Item(["c1:0", "c2:0"])}
    assert Will.find_same_date_copies(will) == WE0


def test_detector_quiet_when_safe():
    # Fixed state: the complete will is one day earlier.
    assert Will.find_same_date_copies({
        "old": _Item(["c1:0"]),
        "new": _Item(["c1:0", "c2:0"], locktime=DELIVERY - DAY),
    }) is None
    # The old will is no longer VALID (Replaced / Invalidated).
    assert Will.find_same_date_copies({
        "old": _Item(["c1:0"], valid=False),
        "new": _Item(["c1:0", "c2:0"]),
    }) is None
    # One copy per executor, the normal will.
    assert Will.find_same_date_copies({
        "a": _Item(["c1:0", "c2:0"], url=WE0),
        "b": _Item(["c1:0", "c2:0"], url=WE1),
    }) is None
    # Without a will-executor BAL already handles it (amounts compared).
    assert Will.find_same_date_copies({
        "old": _Item(["c1:0"], url=None),
        "new": _Item(["c1:0", "c2:0"], url=None),
    }) is None


def test_message_names_the_executor():
    e = SameDateCopiesException(WE0)
    assert isinstance(e, NotCompleteWillException)          # the Check rebuilds
    text = will_change_message(e)
    assert WE0 in text and "one day earlier" in text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
