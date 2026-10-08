"""
Tests for the read-only "an older will is still active" check (CHANGELOG #69).

``Will.find_still_active_older_txs`` reports the will txs that were sent to a
will-executor, are no longer part of the current will, are dated EARLIER than
it, and can still be executed (no input spent by a broadcast transaction).

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 tests/test_core_still_active_warning.py
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.will import Will, WillItem

# WillItem parses a raw tx and calls add_info_from_wallet; no live wallet here.
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

# A valid serialized transaction (1 input, 1 output, nLockTime in the last 4
# bytes); the same one used by tests/test_core_willtree_father.py.
_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)
# The outpoint spent by the input of _TX_HEX.
_FUNDING = "3140eb24b43386f35ba69e3875eb6c93130ac66201d01c58f598defc949a5c2a:0"

OLD = 1979683200      # 2032-09-25 (UTC)
CURRENT = 2011219200  # 2033-09-25
LATER = 2042755200    # 2034-09-25
WE = "https://we.example"


def _hex_with_locktime(locktime, tx_hex=_TX_HEX):
    """``tx_hex`` with its nLockTime replaced (a different txid each time)."""
    return tx_hex[:-8] + int(locktime).to_bytes(4, "little").hex()


def _item(locktime, *statuses, tx_hex=_TX_HEX, valid=False):
    """WillItem dated ``locktime`` with the given status flags set.

    ``valid=False`` (the default) clears VALID: it is set by default on a
    new WillItem, while most items here are old, replaced ones.
    """
    item = WillItem(
        {
            "tx": _hex_with_locktime(locktime, tx_hex),
            "heirs": {},
            "willexecutor": {"url": WE, "selected": True},
        }
    )
    for s in statuses:
        item.set_status(s, True)
    if not valid:
        item.STATUS["VALID"][1] = False
    return item


def _will(*items):
    return {i._id: i for i in items}


def _wallet(spent=(), up_to_date=True):
    """Wallet mock: ``spent`` outpoints have a broadcast spender."""
    wallet = MagicMock()
    wallet.is_up_to_date.return_value = up_to_date
    wallet.adb.get_spender.side_effect = (
        lambda outpoint: "ab" * 32 if outpoint in spent else None
    )
    return wallet


def _current():
    """The current will: valid, signed and sent, dated CURRENT."""
    return _item(CURRENT, "COMPLETE", "PUSHED", valid=True)


def test_bug2_state_is_reported():
    """Old sent tx, only REPLACED (never invalidated), coins unspent."""
    old = _item(OLD, "COMPLETE", "PUSHED", "REPLACED")
    found = Will.find_still_active_older_txs(_will(old, _current()), _wallet())
    assert [(i._id, lt, url) for i, lt, url in found] == [(old._id, OLD, WE)]


def test_checked_counts_as_sent():
    old = _item(OLD, "COMPLETE", "CHECKED", "REPLACED")
    assert Will.find_still_active_older_txs(_will(old, _current()), _wallet())


def test_correct_postpone_invalidated_old_tx_is_not_reported():
    old = _item(OLD, "COMPLETE", "PUSHED", "REPLACED", "INVALIDATED")
    assert Will.find_still_active_older_txs(_will(old, _current()), _wallet()) == []


def test_fee_change_replaced_tx_with_later_date_is_not_reported():
    """Normal fee/heir change: the replaced tx is dated LATER (anticipate)."""
    old = _item(LATER, "COMPLETE", "PUSHED", "REPLACED")
    assert Will.find_still_active_older_txs(_will(old, _current()), _wallet()) == []


def test_old_tx_with_spent_coin_is_not_reported():
    old = _item(OLD, "COMPLETE", "PUSHED", "REPLACED")
    wallet = _wallet(spent={_FUNDING})
    assert Will.find_still_active_older_txs(_will(old, _current()), wallet) == []


def test_never_sent_old_tx_is_not_reported():
    """Decision W2: a tx whose push failed is not reported."""
    old = _item(OLD, "COMPLETE", "PUSH_FAIL", "REPLACED")
    assert Will.find_still_active_older_txs(_will(old, _current()), _wallet()) == []


def test_confirmed_or_mempool_old_tx_is_not_reported():
    for status in ("CONFIRMED", "MEMPOOL"):
        old = _item(OLD, "COMPLETE", "PUSHED", status)
        found = Will.find_still_active_older_txs(_will(old, _current()), _wallet())
        assert found == [], status


def test_current_valid_tx_is_never_reported():
    current = _current()
    assert Will.find_still_active_older_txs(_will(current), _wallet()) == []


def test_without_valid_items_the_settings_date_is_the_reference():
    old = _item(OLD, "COMPLETE", "PUSHED", "REPLACED")
    assert Will.find_still_active_older_txs(_will(old), _wallet(), CURRENT)
    assert Will.find_still_active_older_txs(_will(old), _wallet(), OLD - 1) == []
    assert Will.find_still_active_older_txs(_will(old), _wallet(), None) == []


def test_child_follows_its_parent():
    """A child spends the parent's output: it lives only while the parent can."""
    parent = _item(OLD, "COMPLETE", "PUSHED", "REPLACED")
    child_hex = _TX_HEX.replace(
        bytes.fromhex(_FUNDING.split(":")[0])[::-1].hex(),
        bytes.fromhex(parent._id)[::-1].hex(),
    )
    child = _item(OLD, "COMPLETE", "PUSHED", "REPLACED", tx_hex=child_hex)
    will = _will(parent, child, _current())

    found = {i._id for i, _lt, _u in Will.find_still_active_older_txs(will, _wallet())}
    assert found == {parent._id, child._id}

    # Funding of the parent spent on-chain: neither can be executed anymore.
    spent = _wallet(spent={_FUNDING})
    assert Will.find_still_active_older_txs(will, spent) == []


def test_not_synchronized_wallet_is_silent():
    old = _item(OLD, "COMPLETE", "PUSHED", "REPLACED")
    wallet = _wallet(up_to_date=False)
    assert Will.find_still_active_older_txs(_will(old, _current()), wallet) == []


def test_never_raises():
    old = _item(OLD, "COMPLETE", "PUSHED", "REPLACED")
    wallet = _wallet()
    wallet.is_up_to_date.side_effect = RuntimeError("boom")
    assert Will.find_still_active_older_txs(_will(old, _current()), wallet) == []
    assert Will.find_still_active_older_txs({}, wallet) == []
    assert Will.find_still_active_older_txs(_will(old), None, CURRENT) == []


def test_results_are_sorted_by_date():
    older = _item(OLD - 86400, "COMPLETE", "PUSHED", "REPLACED")
    old = _item(OLD, "COMPLETE", "PUSHED", "REPLACED")
    found = Will.find_still_active_older_txs(_will(old, older, _current()), _wallet())
    assert [lt for _i, lt, _u in found] == [OLD - 86400, OLD]


def test_get_current_will_locktime():
    later_valid = _item(LATER, valid=True)
    assert Will.get_current_will_locktime(_will(_current(), later_valid)) == CURRENT
    assert Will.get_current_will_locktime(_will(_item(OLD, "REPLACED")), 123) == 123
    assert Will.get_current_will_locktime({}, None) is None


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All still-active warning tests passed")
