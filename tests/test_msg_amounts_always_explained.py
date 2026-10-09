"""
The heirs' amounts are always explained (CHANGELOG #82).

* With no coin visible at the moment of the Check (e.g. right after a
  Cancel) the "percentages add up to 40%" row disappeared: the check failed
  on its first, balance-based test and was taken for a false alarm. The
  explanation depends only on the settings, so ``describe_amounts`` now gives
  it with a balance of 0 too, and never reports "fixed amounts exceed" then.
* Only fixed amounts: they are paid in proportion (the wallet is emptied);
  new ``fixed_scaled`` explanation and message (text approved by the owner).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_msg_amounts_always_explained.py
"""

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal import i18n
from bal.core.heirs import Heirs
from bal.core.will import Will
from bal.gui.qt.dialogs import BalBuildWillDialog

A1 = "bcrt1q087zm5m3jrhfg78zflqefhcr9heh4c98kzmvhp"
A2 = "bcrt1q08z5t4x74u2883sx2qwsmzk2hj8e5n7z83e4vy"
A3 = "bcrt1qdyx9x5mlh3s9nqpl8y8zqtz4p6ngsk0kh3whzd"


def _heirs(**amounts):
    h = Heirs.__new__(Heirs)
    addresses = iter((A1, A2, A3))
    h.update({name: [next(addresses), amount, "30d"] for name, amount in amounts.items()})
    return h


def _utxos(total):
    if not total:
        return []
    u = MagicMock()
    u.value_sats.return_value = total
    return [u]


def _describe(heirs, balance=1_000_000):
    return Will.describe_amounts(heirs, {}, _utxos(balance), time.time(), 500)


# ------------------------------------------------------------ describe_amounts

def test_percentages_are_explained_with_no_coin_visible():
    """Before #82 the row disappeared (owner's wallet, 20% + 20%)."""
    info = _describe(_heirs(**{"1": "20%", "pocket manuel": "20%"}), balance=0)
    assert info["kind"] == "percent" and info["total"] == 40
    assert [round(s) for _n, s in info["shares"]] == [50, 50]


def test_no_fixed_exceeds_with_no_coin_visible():
    info = _describe(_heirs(alice=800_000, bob="50%"), balance=0)
    assert info["kind"] == "percent" and info["with_fixed"] is True


def test_fixed_amounts_only_are_paid_in_proportion():
    info = _describe(_heirs(alice=300_000, bob=200_000))
    assert info["kind"] == "fixed_scaled"
    assert [(n, round(s)) for n, s in info["shares"]] == [("alice", 60), ("bob", 40)]


def test_fixed_amounts_only_explained_with_no_coin_visible():
    assert _describe(_heirs(alice=300_000, bob=200_000), balance=0)["kind"] == "fixed_scaled"


def test_fixed_amounts_that_exceed_still_stop():
    """#76 unchanged: with coins visible, too large fixed amounts stop."""
    assert _describe(_heirs(alice=800_000, bob=600_000))["kind"] == "fixed_exceeds"


def test_dust_and_op_return_are_left_out_of_the_shares():
    h = _heirs(alice=300_000, bob=100, carol=100_000)
    h["carol"][0] = "OP_RETURN:48656c6c6f"
    info = _describe(h)
    assert info["kind"] == "fixed_scaled" and info["shares"] == [("alice", 100.0)]


def test_nothing_to_explain_still_none():
    assert _describe(_heirs(alice="60%", bob="40%")) is None
    assert _describe(_heirs(alice=100_000, bob="100%")) is None


# ------------------------------------------------------------------ message

def test_fixed_scaled_message():
    msg = BalBuildWillDialog._amounts_message(_describe(_heirs(alice=300_000, bob=200_000)))
    assert msg == ("All the heirs have a fixed amount: the will always empties the "
                   "whole wallet, so the amounts are increased in proportion "
                   "(alice 60%, bob 40%).")


def test_fixed_scaled_message_italian_numbers():
    no_catalog = SimpleNamespace(read_file=MagicMock(side_effect=KeyError))
    i18n.set_language(no_catalog, "it_IT")
    try:
        msg = BalBuildWillDialog._amounts_message(
            _describe(_heirs(alice=200_000, bob=100_000)))
        assert "alice 66,7%, bob 33,3%" in msg
    finally:
        i18n.set_language(no_catalog, None)


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All amounts-explained tests passed")
