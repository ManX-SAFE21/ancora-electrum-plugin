"""
Regression tests for BUG-1 (CHANGELOG #73): a postpone made together with a
fee change or an heir change.

``Will.check_willexecutors_and_heirs`` stopped at the first difference it
met: a changed fee rate (``TxFeesChangedException``) or a changed heir
address/amount (``HeirNotFoundException``) was reported before the delivery
date was compared. The will was then rebuilt as a normal update (one day
before the old tx, overwriting the requested later date) and the old signed
tx stayed in force. The postpone of a signed/sent tx is now detected first.

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 tests/test_core_postpone_with_other_changes.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.will import (
    HeirNotFoundException,
    TxFeesChangedException,
    Will,
    WillItem,
    WillPostponedException,
)

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
LATER = 2011219200    # 2033-09-25
EARLIER = 1948147200  # 2031-09-25
ALICE = "tb1qalice"
BOB = "tb1qbob"


def _will(*statuses, fee=13):
    """One VALID will tx dated OLD (alice 60 %, bob 40 %) with ``statuses``."""
    item = WillItem({
        "tx": _TX_HEX[:-8] + OLD.to_bytes(4, "little").hex(),
        "heirs": {"alice": [ALICE, "60%", OLD], "bob": [BOB, "40%", OLD]},
        "willexecutor": None,
        "baltx_fees": fee,
    })
    for s in statuses:
        item.set_status(s, True)
    return {item._id: item}


def _heirs(locktime, alice="60%", bob="40%"):
    return {"alice": [ALICE, alice, locktime], "bob": [BOB, bob, locktime]}


def _check(will, heirs, fee=13):
    return Will.check_willexecutors_and_heirs(will, heirs, {}, False, 0, fee)


def _raises(exc, *args, **kwargs):
    try:
        _check(*args, **kwargs)
    except exc:
        return
    except Exception as e:
        raise AssertionError(f"expected {exc.__name__}, got {e!r}") from e
    raise AssertionError(f"expected {exc.__name__}, nothing raised")


def test_fee_change_and_later_date_is_a_postpone():
    """A7/A9: before #73 this was TxFeesChangedException (plain update)."""
    _raises(WillPostponedException, _will("COMPLETE", "PUSHED"), _heirs(LATER), fee=14)


def test_heir_amount_change_and_later_date_is_a_postpone():
    """A13: before #73 this was HeirNotFoundException (plain update)."""
    _raises(WillPostponedException, _will("COMPLETE", "PUSHED"),
            _heirs(LATER, alice="70%", bob="30%"))


def test_signed_but_not_sent_is_also_a_postpone():
    """Signed (COMPLETE) is enough: the tx may have been exported by hand."""
    _raises(WillPostponedException, _will("COMPLETE"), _heirs(LATER), fee=14)


def test_fee_change_alone_is_still_a_plain_update():
    _raises(TxFeesChangedException, _will("COMPLETE", "PUSHED"), _heirs(OLD), fee=14)


def test_heir_change_alone_is_still_a_plain_update():
    _raises(HeirNotFoundException, _will("COMPLETE", "PUSHED"),
            _heirs(OLD, alice="70%", bob="30%"))


def test_fee_change_and_earlier_date_is_not_a_postpone():
    """Moving the date EARLIER never needs an on-chain invalidation."""
    _raises(TxFeesChangedException, _will("COMPLETE", "PUSHED"), _heirs(EARLIER), fee=14)


def test_unsigned_will_with_later_date_is_not_a_postpone():
    """Nothing was signed or sent: a plain rebuild, no invalidation."""
    _raises(TxFeesChangedException, _will(), _heirs(LATER), fee=14)


def test_plain_postpone_still_detected():
    """A4 (date later only) keeps working."""
    _raises(WillPostponedException, _will("COMPLETE", "PUSHED"), _heirs(LATER))


def test_unchanged_will_is_coherent():
    assert _check(_will("COMPLETE", "PUSHED"), _heirs(OLD)) is True


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All postpone-with-other-changes tests passed")
