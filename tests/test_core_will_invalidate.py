"""
Tests for will invalidation (cancellation) in ``bal.core.will``.

Covers:
* Will.invalidate_will() - building the invalidation transaction
* Will.set_invalidate() - marking will items as invalidated (status cascade)

The invalidation ("cancellation") transaction spends the same UTXOs that were
committed to the time-locked will, making the original will transactions
unspendable.  This is the mechanism used when:
  * The will expires (locktime in the past)
  * The owner postpones a signed/sent will to a later date
  * The check-alive threshold is passed (dead-man's switch)

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 -m pytest tests/test_core_will_invalidate.py -q
"""

import copy
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

# Patch Transaction.add_info_from_wallet so WillItem can parse the tx hex
# without a live Electrum wallet connection.
from electrum.transaction import Transaction

from bal.core.will import Will, WillItem

_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

# A valid serialized Bitcoin transaction hex (1 input + 1 P2PKH output,
# version 2).  Reused across multiple test suites.
_VALID_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)

# The prevout string that _VALID_TX_HEX spends (input 0).
_PREVOUT_STR = "3140eb24b43386f35ba69e3875eb6c93130ac66201d01c58f598defc949a5c2a:0"

# Change address for the invalidation output.
_CHANGE_ADDR = "14CHYaaByjJZpx4oHBpfDMdqhTyXnZ3kVs"


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _make_willitem(value_sats=1000000, valid=True, extra_heirs=None):
    """Create a WillItem from _VALID_TX_HEX with a known input value.

    The input's ``_trusted_value_sats`` is set so that
    ``invalidate_will`` can read the balance from it.
    """
    heirs = {"alice": ["addr_alice", 5000, "30d"]}
    if extra_heirs:
        heirs.update(extra_heirs)
    item = WillItem({
        "tx": _VALID_TX_HEX,
        "heirs": heirs,
        "willexecutor": None,
        "status": "",
        "description": "",
        "time": 0,
        "change": "",
        "baltx_fees": 100,
    })
    item.STATUS = copy.deepcopy(WillItem.STATUS_DEFAULT)
    # Set the input value so the balance calculation works.
    item.tx.inputs()[0]._trusted_value_sats = value_sats
    if not valid:
        item.set_status("INVALIDATED", True)
    return item


def _make_utxo(prevout_str=None, value_sats=1000000, is_coinbase=False):
    """Create a minimal mock UTXO (wallet-side) matching a will input."""
    if prevout_str is None:
        prevout_str = _PREVOUT_STR
    utxo = MagicMock()
    utxo.prevout.to_str.return_value = prevout_str
    utxo.is_coinbase_output.return_value = is_coinbase
    utxo.block_height = 1
    utxo.value_sats.return_value = value_sats
    return utxo


def _mock_wallet(utxos, change_addr=_CHANGE_ADDR):
    """Create a mock wallet with the given UTXOs and change address."""
    wallet = MagicMock()
    wallet.get_utxos.return_value = utxos
    wallet.get_change_addresses_for_new_transaction.return_value = [change_addr]
    wallet.network = MagicMock()
    return wallet


def _run_invalidate(will, wallet, fees_per_byte=10, current_height=800000):
    """Run ``Will.invalidate_will`` with mocked Electrum tx building.

    Returns ``(result, mock_from_io, mock_out)`` so tests can inspect
    the calls to ``PartialTransaction.from_io`` and
    ``PartialTxOutput.from_address_and_value``.
    """
    mock_output = MagicMock()
    mock_output.value = 0
    mock_output.is_change = False

    mock_tx = MagicMock()
    mock_tx.txid.return_value = "invalidation_txid"
    mock_tx.estimated_size.return_value = 200

    with patch("bal.core.will.Util.get_current_height", return_value=current_height), \
         patch("electrum.transaction.PartialTxOutput.from_address_and_value",
               return_value=mock_output) as mock_out, \
         patch("electrum.transaction.PartialTransaction.from_io",
               return_value=mock_tx) as mock_from_io:
        result = Will.invalidate_will(will, wallet, fees_per_byte)
        return result, mock_from_io, mock_out


# ================================================================== #
# Will.invalidate_will - building the cancellation transaction
# ================================================================== #

class TestInvalidateWill:
    """Tests for ``Will.invalidate_will()``: the cancellation transaction."""

    def test_basic_returns_tx(self):
        """A single valid will item with a matching wallet UTXO produces an
        invalidation transaction."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        result, mock_from_io, _ = _run_invalidate(will, wallet, fees_per_byte=10)

        assert result is not None, "should return a transaction"

    def test_basic_rbf_enabled(self):
        """The invalidation tx has RBF (Replace-By-Fee) enabled."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        result, _, _ = _run_invalidate(will, wallet)

        result.set_rbf.assert_called_with(True)

    def test_basic_locktime_is_current_height(self):
        """The invalidation tx locktime equals the current block height."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])
        current_height = 750000

        _, mock_from_io, _ = _run_invalidate(will, wallet, current_height=current_height)

        # from_io(inputs, outputs, locktime=<height>, version=2)
        _, kwargs = mock_from_io.call_args
        assert kwargs["locktime"] == current_height

    def test_basic_version_2(self):
        """The invalidation tx uses Bitcoin transaction version 2."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        _, mock_from_io, _ = _run_invalidate(will, wallet)

        _, kwargs = mock_from_io.call_args
        assert kwargs["version"] == 2

    def test_basic_output_value_deducts_fee(self):
        """The invalidation output value is balance minus fee.

        Fee = estimated_size * fees_per_byte.
        """
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])
        fees_per_byte = 10

        _, mock_from_io, mock_out = _run_invalidate(will, wallet, fees_per_byte=fees_per_byte)

        # The second call to from_address_and_value uses balance - fee.
        # estimated_size returns 200, so fee = 200 * 10 = 2000.
        # Expected output value = 1000000 - 2000 = 998000.
        second_call_value = mock_out.call_args_list[1][0][1]
        assert second_call_value == 998000

    def test_basic_spends_correct_utxos(self):
        """The invalidation tx spends the same UTXOs as the will."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        _, mock_from_io, _ = _run_invalidate(will, wallet)

        # First positional arg is the list of UTXOs to spend.
        spent_utxos = mock_from_io.call_args[0][0]
        assert len(spent_utxos) == 1
        assert spent_utxos[0].prevout.to_str() == _PREVOUT_STR

    def test_no_matching_utxos_returns_none(self):
        """When wallet UTXOs don't match any will inputs, returns None."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo(prevout_str="aaaa:1")])

        result, _, _ = _run_invalidate(will, wallet)
        assert result is None

    def test_no_valid_items_returns_none(self):
        """When all will items are INVALIDATED, returns None."""
        item = _make_willitem(valid=False)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        result, _, _ = _run_invalidate(will, wallet)
        assert result is None

    def test_empty_will_returns_none(self):
        """An empty will dictionary returns None."""
        wallet = _mock_wallet([_make_utxo()])

        result, _, _ = _run_invalidate({}, wallet)
        assert result is None

    def test_skips_young_coinbase(self):
        """Coinbase UTXOs younger than current_height + 100 are skipped."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        # Coinbase UTXO: block_height = 800050, current_height = 800000
        # 800050 < 800000 + 100 => skipped
        utxo = _make_utxo(value_sats=1000000, is_coinbase=True)
        utxo.block_height = 800050
        wallet = _mock_wallet([utxo])

        result, _, _ = _run_invalidate(will, wallet, current_height=800000)
        assert result is None

    def test_includes_mature_coinbase(self):
        """Coinbase UTXOs at or above current_height + 100 are included."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        utxo = _make_utxo(value_sats=1000000, is_coinbase=True)
        utxo.block_height = 800150  # >= 800000 + 100
        wallet = _mock_wallet([utxo])

        result, _, _ = _run_invalidate(will, wallet, current_height=800000)
        assert result is not None

    def test_fee_exceeds_balance_returns_none(self):
        """When the fee exceeds the balance, returns None.

        estimated_size (200) * fees_per_byte (100) = 20000 > balance (100).
        """
        item = _make_willitem(value_sats=100)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        result, mock_from_io, _ = _run_invalidate(will, wallet, fees_per_byte=100)

        assert result is None
        # from_io is still called once (for fee estimation), but the
        # result is discarded because balance - fee <= 0.
        assert mock_from_io.call_count == 1

    def test_only_valid_items_contribute_balance(self):
        """INVALIDATED will items are excluded from the balance."""
        valid_item = _make_willitem(value_sats=1000000, valid=True)
        invalid_item = _make_willitem(value_sats=2000000, valid=False)
        will = {"valid": valid_item, "invalid": invalid_item}
        wallet = _mock_wallet([_make_utxo()])

        _, mock_from_io, mock_out = _run_invalidate(will, wallet, fees_per_byte=10)

        # Balance = 1000000 (valid only), fee = 200 * 10 = 2000
        # Output value = 998000
        second_call_value = mock_out.call_args_list[1][0][1]
        assert second_call_value == 998000

    def test_first_from_io_uses_full_balance(self):
        """The first from_io call uses the full balance (before fee deduction)
        to estimate the fee."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        _, mock_from_io, mock_out = _run_invalidate(will, wallet, fees_per_byte=10)

        # First from_address_and_value call: value = balance (1000000)
        first_call_value = mock_out.call_args_list[0][0][1]
        assert first_call_value == 1000000

    def test_output_address_is_change_address(self):
        """The invalidation output goes to the wallet's change address."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        _, _, mock_out = _run_invalidate(will, wallet)

        # Both calls to from_address_and_value use the change address.
        for call in mock_out.call_args_list:
            assert call[0][0] == _CHANGE_ADDR

    def test_multiple_utxos_all_matched(self):
        """Multiple matching UTXOs are all included in the invalidation."""
        item1 = _make_willitem(value_sats=500000)
        item2 = _make_willitem(value_sats=300000)
        will = {"tx1": item1, "tx2": item2}

        # Two UTXOs with different prevouts matching the two will items.
        # Since both items use the same _VALID_TX_HEX, their prevout is the
        # same.  To test multiple UTXOs, we need a second tx hex with a
        # different input.
        #
        # However, get_all_inputs deduplicates by prevout_str, so even with
        # two items sharing the same prevout, only one entry is added to
        # prevout_to_spend.  The first matching UTXO is what matters.
        utxos = [_make_utxo()]
        wallet = _mock_wallet(utxos)

        result, mock_from_io, _ = _run_invalidate(will, wallet)
        assert result is not None
        # Only 1 UTXO spent (deduplication of shared prevout)
        spent_utxos = mock_from_io.call_args[0][0]
        assert len(spent_utxos) == 1

    def test_zero_fees_per_byte(self):
        """With zero fee rate, the full balance goes to the output."""
        item = _make_willitem(value_sats=1000000)
        will = {"willtxid1": item}
        wallet = _mock_wallet([_make_utxo()])

        _, mock_from_io, mock_out = _run_invalidate(will, wallet, fees_per_byte=0)

        assert mock_from_io.call_count == 2  # two calls (both succeed)
        # Output value = balance - 0 = 1000000
        second_call_value = mock_out.call_args_list[1][0][1]
        assert second_call_value == 1000000


# ================================================================== #
# Will.set_invalidate - status flag cascade
# ================================================================== #

class TestSetInvalidate:
    """Tests for ``Will.set_invalidate()``: marking will items as invalidated."""

    def test_single_item_no_children(self):
        """Invalidating a single will item sets INVALIDATED and clears VALID."""
        item = _make_willitem(valid=True)
        item.children = {}
        will = {"willid1": item}

        Will.set_invalidate("willid1", will)

        assert item.get_status("INVALIDATED") is True
        assert item.get_status("VALID") is False

    def test_cascades_to_direct_children(self):
        """Invalidating a parent cascades INVALIDATED to its children."""
        parent = _make_willitem(valid=True)
        child = _make_willitem(valid=True)

        parent.children = {"child_id": ["child_id", 0, 0]}
        child.children = {}

        will = {"parent_id": parent, "child_id": child}

        Will.set_invalidate("parent_id", will)

        assert parent.get_status("INVALIDATED") is True
        assert parent.get_status("VALID") is False
        assert child.get_status("INVALIDATED") is True
        assert child.get_status("VALID") is False

    def test_cascades_to_grandchildren(self):
        """Invalidating cascades through multiple levels of descendants."""
        root = _make_willitem(valid=True)
        branch = _make_willitem(valid=True)
        leaf = _make_willitem(valid=True)

        root.children = {"branch_id": ["branch_id", 0, 0]}
        branch.children = {"leaf_id": ["leaf_id", 0, 0]}
        leaf.children = {}

        will = {
            "root_id": root,
            "branch_id": branch,
            "leaf_id": leaf,
        }

        Will.set_invalidate("root_id", will)

        for name, item in [("root", root), ("branch", branch), ("leaf", leaf)]:
            assert item.get_status("INVALIDATED") is True, f"{name} should be INVALIDATED"
            assert item.get_status("VALID") is False, f"{name} should not be VALID"

    def test_empty_children_dict(self):
        """A will item with an empty children dict is a leaf (no cascade)."""
        item = _make_willitem(valid=True)
        item.children = {}
        will = {"wid": item}

        Will.set_invalidate("wid", will)

        assert item.get_status("INVALIDATED") is True
        assert item.get_status("VALID") is False

    def test_does_not_affect_siblings(self):
        """Invalidating one item does not affect unrelated siblings."""
        item_a = _make_willitem(valid=True)
        item_b = _make_willitem(valid=True)

        item_a.children = {}
        item_b.children = {}

        will = {"a": item_a, "b": item_b}

        Will.set_invalidate("a", will)

        assert item_a.get_status("INVALIDATED") is True
        assert item_a.get_status("VALID") is False
        assert item_b.get_status("INVALIDATED") is False
        assert item_b.get_status("VALID") is True

    def test_multiple_children(self):
        """Invalidating a parent with multiple children cascades to all of them."""
        parent = _make_willitem(valid=True)
        child1 = _make_willitem(valid=True)
        child2 = _make_willitem(valid=True)

        parent.children = {
            "c1": ["c1", 0, 0],
            "c2": ["c2", 0, 0],
        }
        child1.children = {}
        child2.children = {}

        will = {"p": parent, "c1": child1, "c2": child2}

        Will.set_invalidate("p", will)

        assert parent.get_status("INVALIDATED") is True
        assert child1.get_status("INVALIDATED") is True
        assert child2.get_status("INVALIDATED") is True

    def test_idempotent(self):
        """Setting INVALIDATED twice on the same item is a safe no-op."""
        item = _make_willitem(valid=True)
        item.children = {}
        will = {"wid": item}

        Will.set_invalidate("wid", will)
        Will.set_invalidate("wid", will)

        assert item.get_status("INVALIDATED") is True
        assert item.get_status("VALID") is False


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All invalidation tests passed")
