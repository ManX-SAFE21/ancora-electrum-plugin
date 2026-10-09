"""
A local copy of the will never makes it "Invalidated" (CHANGELOG #86).

Owner's wallet: fee changed -> Cancel at signing (the unsigned will is saved
in the wallet's history as a LOCAL transaction) -> later delivery date ->
Cancel at the invalidation. The next Check marked the unsigned will
"Invalidated" although nothing was ever broadcast, and the will already sent
(marked "Replaced" by it) looked dead while it was still executable.

Two causes, both fixed:

* ``Util.get_available_utxos`` gave back a coin hidden by a local will copy
  only when the copy was dated strictly LATER than the will being checked:
  the copy of the will itself (same date) kept hiding its own coin;
* ``Will.search_rai`` marked "Invalidated" an item whose coin was hidden only
  by such a local transaction (INVALIDATED means: spent by a BROADCAST tx).

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 tests/test_local_copy_not_invalidated.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.address_synchronizer import TX_HEIGHT_LOCAL
from electrum.transaction import Transaction

from bal.core.util import Util
from bal.core.will import Will, WillItem

# WillItem parses a raw tx and calls add_info_from_wallet; no live wallet here.
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)
LOCKTIME = 1979510400            # 2032-09-22
LABEL = "Safe21 Heartbeat inheritance transaction - {willexecutor}"
LOCAL_COPY = "aa" * 32


def _height(h):
    return SimpleNamespace(height=lambda: h)


def _wallet(raw_spender=LOCAL_COPY, spender_height=TX_HEIGHT_LOCAL,
            copy_locktime=LOCKTIME, label="Safe21 Heartbeat inheritance "
            "transaction - https://we0.test"):
    """A wallet whose only record of the coin is the spender ``raw_spender``."""
    w = MagicMock()
    w.db.get_spent_outpoint.return_value = raw_spender
    w.db.get_transaction.return_value = None          # the item itself: not stored
    w.adb.get_spender.return_value = None             # Electrum hides local spenders
    w.adb.get_tx_height.side_effect = lambda txid: _height(spender_height)
    w.adb.db.get_transaction.return_value = SimpleNamespace(locktime=copy_locktime)
    w.get_label_for_txid.return_value = label
    return w


def _item(*statuses, locktime=LOCKTIME):
    item = WillItem({
        "tx": _TX_HEX,
        "heirs": {"alice": ["tb1qalice", "100%", locktime]},
        "willexecutor": {"url": "https://we0.test", "base_fee": 1000},
        "baltx_fees": 10,
    })
    item.tx.locktime = locktime
    for s in statuses:
        item.set_status(s, True)
    return item


def _search_rai(item, wallet):
    will = {item._id: item}
    Will.search_rai(Will.get_all_inputs(will, only_valid=True), [], will, wallet)
    return item


# ------------------------------------------------- Util.get_available_utxos

def test_copy_of_the_same_will_does_not_hide_its_coin():
    """Same date: the local copy IS the will being checked (#86)."""
    assert Util._is_ignorable_local_spender(
        _wallet(), LOCAL_COPY, LABEL, LOCKTIME)


def test_copy_of_a_later_will_still_does_not_hide_the_coin():
    assert Util._is_ignorable_local_spender(
        _wallet(copy_locktime=LOCKTIME + 86400), LOCAL_COPY, LABEL, LOCKTIME)


def test_copy_of_an_earlier_will_still_hides_the_coin():
    """Unchanged: an earlier local will is a different, newer will."""
    assert not Util._is_ignorable_local_spender(
        _wallet(copy_locktime=LOCKTIME - 86400), LOCAL_COPY, LABEL, LOCKTIME)


def test_broadcast_or_foreign_spenders_are_never_ignored():
    assert not Util._is_ignorable_local_spender(
        _wallet(spender_height=0), LOCAL_COPY, LABEL, LOCKTIME)        # mempool
    assert not Util._is_ignorable_local_spender(
        _wallet(label="my payment"), LOCAL_COPY, LABEL, LOCKTIME)      # not BAL


# ---------------------------------------------- Will._funding_spent_only_locally

def test_spent_only_locally():
    assert Will._funding_spent_only_locally(_wallet(), "bb" * 32 + ":0")
    assert not Will._funding_spent_only_locally(_wallet(spender_height=0), "bb" * 32 + ":0")
    assert not Will._funding_spent_only_locally(_wallet(spender_height=800_000), "bb" * 32 + ":0")
    assert not Will._funding_spent_only_locally(_wallet(raw_spender=None), "bb" * 32 + ":0")
    assert not Will._funding_spent_only_locally(None, "bb" * 32 + ":0")
    broken = MagicMock()
    broken.db.get_spent_outpoint.side_effect = RuntimeError("db")
    assert not Will._funding_spent_only_locally(broken, "bb" * 32 + ":0")


# ------------------------------------------------------------- Will.search_rai

def test_unsigned_will_hidden_by_its_local_copy_stays_valid():
    """The owner's case: nothing was broadcast, nothing is invalidated."""
    item = _search_rai(_item(), _wallet())
    assert item.get_status("VALID")
    assert not item.get_status("INVALIDATED")


def test_coin_spent_by_a_broadcast_tx_still_invalidates():
    wallet = _wallet(spender_height=0)
    wallet.adb.get_spender.return_value = LOCAL_COPY            # a real spender
    item = _search_rai(_item(), wallet)
    assert item.get_status("MEMPOOL")                           # unchanged rule
    assert not item.get_status("VALID")


def test_coin_with_no_spender_at_all_keeps_the_old_rule():
    """No record at all of the coin: unchanged (INVALIDATED)."""
    item = _search_rai(_item(), _wallet(raw_spender=None))
    assert item.get_status("INVALIDATED")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
