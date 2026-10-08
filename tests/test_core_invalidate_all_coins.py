"""
Regression tests for BUG-3 (CHANGELOG #71): the invalidation must spend EVERY
wallet coin used by ANY will transaction.

Before #71, ``Will.invalidate_will`` took only ONE input per VALID will
transaction, and only from VALID ones. With two coins, the coin left out
depended on the coin order, and an older transaction still held by an
executor could stay executable after "Tools > Invalidate".

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 tests/test_core_invalidate_all_coins.py
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

_CHANGE_ADDR = "14CHYaaByjJZpx4oHBpfDMdqhTyXnZ3kVs"
COIN_A = "aa" * 32
COIN_B = "bb" * 32
COIN_C = "cc" * 32      # a wallet coin never used by the will
VALUE = 500_000


def _raw_tx(prev_txids, locktime):
    """Unsigned legacy tx spending output 0 of each of ``prev_txids``."""
    raw = "02000000" + "%02x" % len(prev_txids)
    for txid in prev_txids:
        raw += bytes.fromhex(txid)[::-1].hex() + "00000000" + "00" + "fdffffff"
    raw += "01" + (900_000).to_bytes(8, "little").hex() + "16" + "0014" + "11" * 20
    return raw + int(locktime).to_bytes(4, "little").hex()


def _item(prev_txids, locktime, *statuses, valid=True):
    item = WillItem({"tx": _raw_tx(prev_txids, locktime), "heirs": {},
                     "willexecutor": {"url": "https://we.example", "selected": True}})
    for s in statuses:
        item.set_status(s, True)
    if not valid:
        item.STATUS["VALID"][1] = False
    return item


def _utxo(txid):
    utxo = MagicMock()
    utxo.prevout.to_str.return_value = f"{txid}:0"
    utxo.is_coinbase_output.return_value = False
    utxo.block_height = 1
    utxo.value_sats.return_value = VALUE
    return utxo


def _spent_outpoints(will, wallet_coins):
    """Run Will.invalidate_will and return the outpoints it spends."""
    wallet = MagicMock()
    wallet.network = None                     # fee: fallback rate
    wallet.get_change_addresses_for_new_transaction.return_value = [_CHANGE_ADDR]
    wallet.get_utxos.return_value = [_utxo(t) for t in wallet_coins]
    tx = MagicMock()
    tx.estimated_size.return_value = 200
    with patch("bal.core.will.Util.get_current_height", return_value=800000), \
         patch("electrum.transaction.PartialTxOutput.from_address_and_value"), \
         patch("electrum.transaction.PartialTransaction.from_io",
               return_value=tx) as from_io:
        result = Will.invalidate_will({i._id: i for i in will}, wallet, 2)
    if result is None:
        return set()
    return {u.prevout.to_str() for u in from_io.call_args_list[-1][0][0]}


def test_two_coin_tx_spends_both_coins():
    """BUG-3: one valid tx with 2 inputs -> both coins must be spent."""
    will = [_item([COIN_A, COIN_B], 2000000000, "COMPLETE", "PUSHED")]
    assert _spent_outpoints(will, [COIN_A, COIN_B]) == {f"{COIN_A}:0", f"{COIN_B}:0"}


def test_coin_of_an_older_sent_tx_is_spent():
    """The current will uses coin A; an older sent tx (REPLACED) uses B."""
    current = _item([COIN_A], 2000000000, "COMPLETE", "PUSHED")
    old = _item([COIN_B], 1990000000, "COMPLETE", "PUSHED", "REPLACED", valid=False)
    assert _spent_outpoints([current, old], [COIN_A, COIN_B]) == {
        f"{COIN_A}:0", f"{COIN_B}:0"}


def test_coin_of_an_item_marked_invalidated_but_unspent_is_spent():
    """A status is bookkeeping: if the coin is still in the wallet, spend it."""
    current = _item([COIN_A], 2000000000, "COMPLETE", "PUSHED")
    old = _item([COIN_B], 1990000000, "COMPLETE", "PUSHED", "INVALIDATED", valid=False)
    assert _spent_outpoints([current, old], [COIN_A, COIN_B]) == {
        f"{COIN_A}:0", f"{COIN_B}:0"}


def test_wallet_coin_not_used_by_the_will_is_not_touched():
    will = [_item([COIN_A], 2000000000, "COMPLETE", "PUSHED")]
    assert _spent_outpoints(will, [COIN_A, COIN_C]) == {f"{COIN_A}:0"}


def test_coins_already_spent_are_simply_absent():
    """Only coins still in the wallet can be spent; nothing left -> None."""
    will = [_item([COIN_A, COIN_B], 2000000000, "COMPLETE", "PUSHED")]
    assert _spent_outpoints(will, [COIN_B]) == {f"{COIN_B}:0"}
    assert _spent_outpoints(will, [COIN_C]) == set()


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All invalidate-all-coins tests passed")
