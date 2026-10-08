"""
Tests for the parent/child tree of the will transactions (``father`` links).

Bug reproduced from a real wallet (Electrum log, Check wizard):

    File "bal/core/will.py", in check_invalidated
        or willtree[w.father].get_status("CONFIRMED")
    KeyError: 'a65a682f…'

``father`` is persisted in the wallet (``"Father"``) and was never cleared, so
after the father tx was removed from the will the child pointed to a txid that
no longer exists. ``Will.add_willtree`` now rebuilds the links from scratch and
``Will.check_invalidated`` treats a missing father as "no father".

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_core_willtree_father.py
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

# A valid serialized transaction (1 input, 1 output) reused by the core tests.
_VALID_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)
# The input of _VALID_TX_HEX spends this outpoint (little-endian in the hex).
_FUNDING_ID = "3140eb24b43386f35ba69e3875eb6c93130ac66201d01c58f598defc949a5c2a"
# PARENT: _VALID_TX_HEX itself. CHILD: the same tx with its input re-pointed to
# the parent's txid, so the child spends the parent's output 0.
_PARENT_ID = Transaction(_VALID_TX_HEX).txid()
_CHILD_TX_HEX = _VALID_TX_HEX.replace(
    bytes.fromhex(_FUNDING_ID)[::-1].hex(), bytes.fromhex(_PARENT_ID)[::-1].hex()
)
# A txid that is not in the will (the "removed father" of the bug report).
_MISSING_ID = "a65a682f2975f0dffd9eccce9392833f227badf617fc67815674736d594d4115"


def _item(tx_hex=_VALID_TX_HEX, father=None):
    """WillItem built from ``tx_hex``, optionally with a stored father."""
    data = {"tx": tx_hex, "heirs": {}, "willexecutor": None}
    if father is not None:
        data["Father"] = father
    return WillItem(data)


def _wallet_without_adb():
    """Wallet whose check_invalidated path skips on-chain lookups."""
    wallet = MagicMock()
    wallet.adb = None
    return wallet


def test_stale_father_is_discarded_by_add_willtree():
    child = _item(father=_MISSING_ID)
    will = {child._id: child}
    Will.add_willtree(will)
    assert child.father is None


def test_check_invalidated_does_not_crash_on_stale_father():
    """The exact failure of the bug report, now without KeyError."""
    child = _item(father=_MISSING_ID)
    will = {child._id: child}
    Will.add_willtree(will)
    Will.check_invalidated(will, [], _wallet_without_adb())


def test_check_invalidated_tolerates_missing_father_without_add_willtree():
    """Defensive path: even if the tree was not rebuilt, no KeyError."""
    child = _item(father=_MISSING_ID)
    will = {child._id: child}
    Will.check_invalidated(will, [], _wallet_without_adb())
    assert child.father == _MISSING_ID


def test_real_parent_child_link_is_built():
    parent = _item()
    child = _item(_CHILD_TX_HEX)
    will = {_PARENT_ID: parent, child._id: child}
    Will.add_willtree(will)
    assert child.father == _PARENT_ID
    assert [c[0] for c in parent.children] == [child._id]
    assert parent.father is None


def test_stale_father_is_replaced_by_the_real_parent():
    parent = _item()
    child = _item(_CHILD_TX_HEX, father=_MISSING_ID)
    will = {_PARENT_ID: parent, child._id: child}
    Will.add_willtree(will)
    assert child.father == _PARENT_ID


def test_child_of_unconfirmed_parent_is_not_checked_against_utxos():
    """Existing rule kept: a child of a VALID (not mined) parent is skipped."""
    parent = _item()
    child = _item(_CHILD_TX_HEX)
    will = {_PARENT_ID: parent, child._id: child}
    Will.add_willtree(will)
    wallet = MagicMock()
    # Funding not spent on-chain: nothing gets invalidated, we only observe
    # which items had their inputs checked.
    with patch.object(Will, "check_tx_height", return_value=-2) as height, \
         patch.object(Will, "_funding_really_spent", return_value=False):
        Will.check_invalidated({child._id: child, _PARENT_ID: parent}, [], wallet)
    # Only the parent (a root) looked up its height; the child was skipped.
    checked = [call.args[0] for call in height.call_args_list]
    assert checked == [parent.tx]


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All will-tree father tests passed")
