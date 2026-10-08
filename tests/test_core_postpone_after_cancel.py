"""
Regression tests for BUG-2 (CHANGELOG #72): postponing after a Cancel.

Flow of the bug: a signed will is sent to the executors; a fee (or heir)
change rebuilds it and the user presses Cancel at "Sign your will" (the old
sent txs become REPLACED, the new ones stay unsigned); then the delivery date
is moved LATER and Check is pressed. The unsigned txs are dropped, the will
has no VALID tx left and is simply rebuilt at the new date, while the old
REPLACED txs, still held by the executors, keep their EARLIER date.

The postpone check only looked at VALID txs. Now a sent, still executable tx
is a postpone of a sent will too (WillPostponedException: invalidate first).

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 tests/test_core_postpone_after_cancel.py
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.will import (
    NotCompleteWillException,
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
_FUNDING = "3140eb24b43386f35ba69e3875eb6c93130ac66201d01c58f598defc949a5c2a:0"

OLD = 1979683200      # 2032-09-25 (UTC)
LATER = 2011219200    # 2033-09-25
EARLIER = 1948147200  # 2031-09-25
ALICE = "tb1qalice"
BOB = "tb1qbob"


def _hex_with_locktime(locktime):
    return _TX_HEX[:-8] + int(locktime).to_bytes(4, "little").hex()


def _sent_old_tx(locktime=OLD, *extra):
    """A signed tx sent to an executor, then REPLACED (not VALID anymore)."""
    item = WillItem({
        "tx": _hex_with_locktime(locktime),
        "heirs": {"alice": [ALICE, "60%", locktime], "bob": [BOB, "40%", locktime]},
        "willexecutor": {"url": "https://we.example", "selected": True},
    })
    for s in ("COMPLETE", "PUSHED", "REPLACED") + extra:
        item.set_status(s, True)
    return {item._id: item}


def _heirs(locktime):
    """The CURRENT heirs settings, with the requested delivery date."""
    return {"alice": [ALICE, "60%", locktime], "bob": [BOB, "40%", locktime]}


def _wallet(spent=()):
    wallet = MagicMock()
    wallet.is_up_to_date.return_value = True
    wallet.adb.get_spender.side_effect = (
        lambda outpoint: "ab" * 32 if outpoint in spent else None
    )
    return wallet


def _is_will_valid(will, heirs, wallet):
    """Will.is_will_valid with the wallet-state part (check_will) stubbed."""
    with patch.object(Will, "check_will"):
        return Will.is_will_valid(
            will, 0, 13, [], heirs=heirs, willexecutors={}, wallet=wallet
        )


def test_bug2_state_is_a_postpone():
    """The exact BUG-2 state: before #72 it was a plain rebuild."""
    try:
        _is_will_valid(_sent_old_tx(), _heirs(LATER), _wallet())
    except WillPostponedException:
        return
    except NotCompleteWillException as e:
        raise AssertionError(f"plain rebuild instead of a postpone: {e!r}") from e
    raise AssertionError("no exception")


def test_same_date_is_not_a_postpone():
    """Fee/heir change only: the replaced tx keeps the requested date."""
    Will.check_sent_txs_not_postponed(_sent_old_tx(), _heirs(OLD), _wallet())


def test_earlier_date_is_not_a_postpone():
    """Moving the date EARLIER never needs an on-chain invalidation."""
    Will.check_sent_txs_not_postponed(_sent_old_tx(), _heirs(EARLIER), _wallet())


def test_old_tx_with_spent_coin_is_ignored():
    """Already killed on-chain (e.g. invalidated): nothing to do."""
    Will.check_sent_txs_not_postponed(
        _sent_old_tx(), _heirs(LATER), _wallet(spent={_FUNDING})
    )


def test_status_invalidated_but_coin_unspent_still_counts():
    """A status is bookkeeping: only a real spend kills the tx."""
    try:
        Will.check_sent_txs_not_postponed(
            _sent_old_tx(OLD, "INVALIDATED"), _heirs(LATER), _wallet()
        )
    except WillPostponedException:
        return
    raise AssertionError("an executable sent tx was ignored")


def test_never_sent_tx_is_ignored():
    will = _sent_old_tx()
    for item in will.values():
        item.STATUS["PUSHED"][1] = False
    Will.check_sent_txs_not_postponed(will, _heirs(LATER), _wallet())


def test_confirmed_tx_is_ignored():
    Will.check_sent_txs_not_postponed(
        _sent_old_tx(OLD, "CONFIRMED"), _heirs(LATER), _wallet()
    )


def test_valid_txs_are_left_to_the_existing_check():
    will = _sent_old_tx()
    for item in will.values():
        item.STATUS["REPLACED"][1] = False
        item.STATUS["VALID"][1] = True
    Will.check_sent_txs_not_postponed(will, _heirs(LATER), _wallet())


def test_heir_added_and_date_later_is_a_postpone():
    """B6: a new heir plus a later date; the common heirs decide."""
    heirs = _heirs(LATER)
    heirs["carol"] = ["tb1qcarol", "20%", LATER]
    try:
        Will.check_sent_txs_not_postponed(_sent_old_tx(), heirs, _wallet())
    except WillPostponedException:
        return
    raise AssertionError("postpone not detected")


def test_without_wallet_nothing_is_decided():
    """No wallet: unspent coins cannot be told apart, so no decision here."""
    Will.check_sent_txs_not_postponed(_sent_old_tx(), _heirs(LATER), None)


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All postpone-after-cancel tests passed")
