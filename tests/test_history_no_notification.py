"""
The will's local copies enter the wallet history without a notification
(CHANGELOG #90).

The unsigned copies of the will (one per will-executor) are saved into the
wallet's local history. Electrum announced each of them as a new transaction:
its desktop notification said "4 new transactions: Total amount sent ..."
although nothing was sent (owner's report). ``Will._add_transaction_to_history``
now adds them with ``is_new=False``, as Electrum does when it re-adds a stored
tx, and clears the wallet's tx-parents cache itself (the one thing the event
did besides notifying).

Checked on a real Electrum wallet with the scenario harness (outside the
repository): 2 "new_transaction" events before, 0 after; the same copy kept
in the history, LOCAL, with its label; the same balance.

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 tests/test_history_no_notification.py
"""

import inspect
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.address_synchronizer import AddressSynchronizer
from electrum.transaction import PartialTransaction
from electrum.util import UnrelatedTransactionException

from bal.core.will import Will


class _Adb:
    """Records how add_transaction is called; can refuse the first call."""

    def __init__(self, unrelated_first=False):
        self.calls = []
        self.unrelated_first = unrelated_first

    def add_transaction(self, tx, *, allow_unrelated=False, is_new=True):
        self.calls.append((tx, allow_unrelated, is_new))
        if self.unrelated_first and len(self.calls) == 1:
            raise UnrelatedTransactionException()
        return True


def _wallet(adb, stored=None, with_cache=True):
    wallet = MagicMock()
    wallet.adb = adb
    wallet.db.get_transaction.return_value = stored
    if not with_cache:
        del wallet.clear_tx_parents_cache
    return wallet


def test_electrum_add_transaction_has_is_new():
    """The parameter exists in the Electrum we run on (4.7.2 and 4.8.x)."""
    assert "is_new" in inspect.signature(AddressSynchronizer.add_transaction).parameters


def test_new_copy_is_added_without_the_new_transaction_event():
    adb = _Adb()
    wallet = _wallet(adb)
    tx = MagicMock()
    Will._add_transaction_to_history(wallet, tx, "ab" * 32)
    assert adb.calls == [(tx, False, False)]
    wallet.clear_tx_parents_cache.assert_called_once_with()


def test_unrelated_fallback_is_also_silent():
    adb = _Adb(unrelated_first=True)
    tx = MagicMock()
    Will._add_transaction_to_history(_wallet(adb), tx, "ab" * 32)
    assert adb.calls == [(tx, False, False), (tx, True, False)]


def test_signatures_merged_into_the_stored_copy_silently():
    stored = MagicMock(spec=PartialTransaction)
    stored.is_complete.return_value = False
    tx = MagicMock(spec=PartialTransaction)
    adb = _Adb()
    Will._add_transaction_to_history(_wallet(adb, stored=stored), tx, "ab" * 32)
    stored.combine_with_other_psbt.assert_called_once_with(tx)
    assert adb.calls == [(stored, False, False)]


def test_a_wallet_without_the_cache_method_is_fine():
    adb = _Adb()
    Will._add_transaction_to_history(_wallet(adb, with_cache=False), MagicMock(), "ab" * 32)
    assert len(adb.calls) == 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
