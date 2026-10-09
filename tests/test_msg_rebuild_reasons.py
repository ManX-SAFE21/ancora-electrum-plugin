"""
Tests for the clearer "Checking your will" row (CHANGELOG #77).

* ``HeirNotFoundException`` now says what changed for the heir (removed,
  added, address, amount, delivery date); an added heir is named before an
  heir whose share changed. ``str(e)`` is still the heir name.
* The Check dialog words one sentence per kind of change, and names the real
  reason right after an invalidation or after a Cancel at signing.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_msg_rebuild_reasons.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.will import (
    HeirNotFoundException,
    NotCompleteWillException,
    TxFeesChangedException,
    Will,
    WillExecutorNotPresent,
    WillItem,
)
from bal.gui.qt.dialogs import BalBuildWillDialog

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

OLD = 1979683200      # 2032-09-25 (UTC)
EARLIER = 1948147200  # 2031-09-25
ALICE, BOB, CAROL = "tb1qalice", "tb1qbob", "tb1qcarol"


def _will(*statuses):
    """One VALID will tx dated OLD (alice 60 %, bob 40 %), fee 13."""
    item = WillItem({
        "tx": _TX_HEX[:-8] + OLD.to_bytes(4, "little").hex(),
        "heirs": {"alice": [ALICE, "60%", OLD], "bob": [BOB, "40%", OLD]},
        "willexecutor": None,
        "baltx_fees": 13,
    })
    for s in statuses:
        item.set_status(s, True)
    return {item._id: item}


def _heir_error(heirs):
    try:
        Will.check_willexecutors_and_heirs(_will("COMPLETE", "PUSHED"), heirs, {}, False, 0, 13)
    except HeirNotFoundException as e:
        return e
    raise AssertionError("HeirNotFoundException expected")


# ------------------------------------------------------------------ core

def test_heir_removed():
    e = _heir_error({"alice": [ALICE, "100%", OLD]})
    assert (str(e), e.change) == ("bob", HeirNotFoundException.HEIR_REMOVED)


def test_heir_added_is_named_before_the_changed_shares():
    """A10: alice and bob go to 40 %, carol is added: name carol."""
    e = _heir_error({"alice": [ALICE, "40%", OLD], "bob": [BOB, "40%", OLD],
                     "carol": [CAROL, "20%", OLD]})
    assert (str(e), e.change) == ("carol", HeirNotFoundException.HEIR_ADDED)


def test_heir_amount_changed():
    """A12: used to say "its delivery date changed"."""
    e = _heir_error({"alice": [ALICE, "70%", OLD], "bob": [BOB, "30%", OLD]})
    assert (str(e), e.change) == ("alice", HeirNotFoundException.HEIR_AMOUNT)


def test_heir_date_changed():
    e = _heir_error({"alice": [ALICE, "60%", EARLIER], "bob": [BOB, "40%", EARLIER]})
    assert (str(e), e.change) == ("alice", HeirNotFoundException.HEIR_DATE)


def test_heir_address_changed():
    e = _heir_error({"alice": ["tb1qnew", "60%", OLD], "bob": [BOB, "40%", OLD]})
    assert (str(e), e.change) == ("alice", HeirNotFoundException.HEIR_ADDRESS)


def test_exception_without_change_keeps_the_old_shape():
    e = HeirNotFoundException("alice")
    assert str(e) == "alice" and e.change is None


# ---------------------------------------------------------------- dialog

class _Dialog:
    """Just what the methods under test use (no Qt widgets)."""

    _check_failure_message = BalBuildWillDialog._check_failure_message
    _rebuild_reason_message = BalBuildWillDialog._rebuild_reason_message
    _unsigned_will_executors = BalBuildWillDialog._unsigned_will_executors
    _no_valid_transaction = staticmethod(BalBuildWillDialog._no_valid_transaction)

    def __init__(self, broadcast=False, postpone=False, unsigned=None, will=None):
        self._invalidation_broadcast = broadcast
        self._postpone_invalidation = postpone
        self._unsigned_executors = unsigned
        self.bal_window = SimpleNamespace(willitems=will or {})


def _msg(e, **kw):
    d = _Dialog(**kw)
    return d._rebuild_reason_message(e) or d._check_failure_message(e)


def test_one_sentence_per_heir_change():
    hnf = HeirNotFoundException
    assert _msg(hnf("bob", hnf.HEIR_REMOVED)) == 'Heir "bob" was removed: preparing a new will.'
    assert _msg(hnf("carol", hnf.HEIR_ADDED)) == 'Heir "carol" was added: preparing a new will.'
    assert "delivery date of heir \"alice\" changed" in _msg(hnf("alice", hnf.HEIR_DATE))
    assert "amount of heir \"alice\" changed" in _msg(hnf("alice", hnf.HEIR_AMOUNT))
    assert "address of heir \"alice\" changed" in _msg(hnf("alice", hnf.HEIR_ADDRESS))
    assert "is not covered by the current will" in _msg(hnf("alice"))


def test_after_a_postpone_invalidation():
    msg = _msg(NotCompleteWillException(), broadcast=True, postpone=True)
    assert msg == "Old will invalidated: preparing the new will with the new date."


def test_after_a_check_alive_invalidation():
    msg = _msg(NotCompleteWillException(), broadcast=True)
    assert msg == ("Old will invalidated: preparing the new will with the "
                   "time period you set.")


def test_after_a_cancel_at_signing():
    unsigned = {"https://we0.test", "https://we1.test"}
    want = "The will prepared earlier was not signed: preparing it again."
    assert _msg(NotCompleteWillException(), unsigned=unsigned) == want          # B1
    assert _msg(WillExecutorNotPresent("https://we0.test"), unsigned=unsigned) == want  # B4


def test_real_changes_keep_their_own_message_after_a_cancel():
    unsigned = {"https://we0.test"}
    assert "Miner fee rate changed" in _msg(TxFeesChangedException("14:  13"), unsigned=unsigned)
    assert "was added" in _msg(HeirNotFoundException("carol", "added"), unsigned=unsigned)
    # A will-executor that the unsigned will did not have was really added.
    assert "is not covered" in _msg(WillExecutorNotPresent("https://we2.test"), unsigned=unsigned)
    # Funds received: not about signing.
    assert "does not cover yet" in _msg(NotCompleteWillException("utxo"), unsigned=unsigned)


def test_no_special_reason_without_cancel_or_invalidation():
    assert "no valid transaction" in _msg(NotCompleteWillException())


def test_funds_received_names_the_funds():
    """Before #77 the e.args test never matched (WillException drops its
    args), so new funds were reported as "no valid transaction"."""
    e = NotCompleteWillException("Some utxo in the wallet is not included")
    assert "does not cover yet" in _msg(e)


def test_unsigned_executors_snapshot():
    will = _will()
    for w in will.values():
        w.set_status("VALID", True)
    assert _Dialog(will=will)._unsigned_will_executors() == {None}
    assert _Dialog(will=_will("COMPLETE", "PUSHED"))._unsigned_will_executors() is None
    assert _Dialog(will={})._unsigned_will_executors() is None


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All rebuild-reason message tests passed")
