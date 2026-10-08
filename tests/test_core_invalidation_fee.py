"""
Tests for the fee of the on-chain INVALIDATION transaction.

The invalidation is broadcast TODAY (it spends the will's coins so the old
pre-signed inheritance transactions become unusable), therefore it must pay the
CURRENT network fee for a confirmation within
``INVALIDATION_FEE_ETA_BLOCKS`` (2) blocks. The will's ``baltx_fees`` (a
deliberately high rate meant for inheritance transactions broadcast years from
now) is only the FALLBACK used when no network estimate is available.

Covers:
* Will.get_invalidation_fee_rate() - rate selection and fallback rules
* Will.invalidate_will() - the chosen rate is really used for the fee

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_core_invalidation_fee.py
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 -m pytest tests/test_core_invalidation_fee.py -q
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.will import INVALIDATION_FEE_ETA_BLOCKS, Will, WillItem

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
# The outpoint spent by _VALID_TX_HEX (input 0).
_PREVOUT_STR = "3140eb24b43386f35ba69e3875eb6c93130ac66201d01c58f598defc949a5c2a:0"
_CHANGE_ADDR = "14CHYaaByjJZpx4oHBpfDMdqhTyXnZ3kVs"
_BALANCE = 1000000
# The will's baltx_fees used as the fallback rate in every test (sat/vbyte).
_BALTX_FEES = 20


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _wallet_with_estimate(fee_per_kb):
    """Wallet whose network returns ``fee_per_kb`` (sat/kvbyte) for any ETA.

    ``fee_per_kb`` may also be an Exception instance: the estimate call then
    raises it, to simulate a broken network object.
    """
    wallet = MagicMock()
    wallet.get_change_addresses_for_new_transaction.return_value = [_CHANGE_ADDR]
    network = MagicMock()
    if isinstance(fee_per_kb, Exception):
        network.fee_estimates.eta_target_to_fee.side_effect = fee_per_kb
    else:
        network.fee_estimates.eta_target_to_fee.return_value = fee_per_kb
    wallet.network = network
    return wallet


def _make_will():
    """One VALID will item spending _PREVOUT_STR with a known input value."""
    item = WillItem({
        "tx": _VALID_TX_HEX,
        "heirs": {"alice": ["addr_alice", 5000, "30d"]},
        "willexecutor": None,
        "status": "",
        "description": "",
        "time": 0,
        "change": "",
        "baltx_fees": _BALTX_FEES,
    })
    item.STATUS = WillItem.copy_status_table(WillItem.STATUS_DEFAULT)
    item.tx.inputs()[0]._TxInput__value_sats = _BALANCE
    return {"willtxid1": item}


def _make_utxo():
    """Wallet-side UTXO matching the will input."""
    utxo = MagicMock()
    utxo.prevout.to_str.return_value = _PREVOUT_STR
    utxo.is_coinbase_output.return_value = False
    utxo.block_height = 1
    utxo.value_sats.return_value = _BALANCE
    return utxo


def _invalidation_output_value(wallet, estimated_size=200):
    """Run Will.invalidate_will and return the value of the final output.

    Electrum's tx building is mocked (as in test_core_will_invalidate.py):
    ``estimated_size`` is the vsize the fee is computed from.
    """
    wallet.get_utxos.return_value = [_make_utxo()]
    mock_output = MagicMock()
    mock_tx = MagicMock()
    mock_tx.estimated_size.return_value = estimated_size
    with patch("bal.core.will.Util.get_current_height", return_value=800000), \
         patch("electrum.transaction.PartialTxOutput.from_address_and_value",
               return_value=mock_output) as mock_out, \
         patch("electrum.transaction.PartialTransaction.from_io",
               return_value=mock_tx):
        result = Will.invalidate_will(_make_will(), wallet, _BALTX_FEES)
    assert result is mock_tx
    # 1st call: full balance (size estimation); 2nd call: balance - fee.
    return mock_out.call_args_list[1][0][1]


# ------------------------------------------------------------------ #
# Will.get_invalidation_fee_rate
# ------------------------------------------------------------------ #

def test_target_is_two_blocks():
    """The owner's decision: confirm within 2 blocks."""
    assert INVALIDATION_FEE_ETA_BLOCKS == 2
    wallet = _wallet_with_estimate(3000)
    Will.get_invalidation_fee_rate(wallet, _BALTX_FEES)
    wallet.network.fee_estimates.eta_target_to_fee.assert_called_once_with(2)


def test_network_estimate_is_used():
    """A live estimate (sat/kvbyte) becomes the rate in sat/vbyte."""
    wallet = _wallet_with_estimate(3000)
    assert Will.get_invalidation_fee_rate(wallet, _BALTX_FEES) == (3.0, "network")


def test_network_estimate_higher_than_baltx_fees_is_used_as_is():
    """No cap: if the market is above baltx_fees, the market rate is used."""
    wallet = _wallet_with_estimate(45000)
    assert Will.get_invalidation_fee_rate(wallet, _BALTX_FEES) == (45.0, "network")


def test_fractional_estimate_is_kept():
    """Electrum estimates may be fractional (e.g. 1.5 sat/vbyte)."""
    wallet = _wallet_with_estimate(1500)
    assert Will.get_invalidation_fee_rate(wallet, _BALTX_FEES) == (1.5, "network")


def test_offline_wallet_falls_back_to_baltx_fees():
    """No network object (offline): previous behaviour, baltx_fees."""
    wallet = MagicMock()
    wallet.network = None
    assert Will.get_invalidation_fee_rate(wallet, _BALTX_FEES) == (
        _BALTX_FEES,
        "fallback",
    )


def test_missing_estimate_falls_back():
    """Estimates not received yet: Electrum returns None."""
    wallet = _wallet_with_estimate(None)
    assert Will.get_invalidation_fee_rate(wallet, _BALTX_FEES) == (
        _BALTX_FEES,
        "fallback",
    )


def test_estimate_error_falls_back_and_does_not_raise():
    """A broken network object must never break the invalidation."""
    wallet = _wallet_with_estimate(RuntimeError("server gone"))
    assert Will.get_invalidation_fee_rate(wallet, _BALTX_FEES) == (
        _BALTX_FEES,
        "fallback",
    )


def test_non_integer_or_invalid_estimates_fall_back():
    """Only a positive int (Electrum's sat/kvbyte type) is trusted."""
    for bad in (0, -1000, True, "3000", 3.5, MagicMock()):
        wallet = _wallet_with_estimate(bad)
        assert Will.get_invalidation_fee_rate(wallet, _BALTX_FEES) == (
            _BALTX_FEES,
            "fallback",
        ), bad


def test_no_wallet_falls_back():
    assert Will.get_invalidation_fee_rate(None, _BALTX_FEES) == (
        _BALTX_FEES,
        "fallback",
    )


# ------------------------------------------------------------------ #
# Will.invalidate_will uses the chosen rate
# ------------------------------------------------------------------ #

def test_invalidation_pays_network_fee_not_baltx_fees():
    """3 sat/vbyte on 200 vbytes = 600 sat (not 200 * 20 = 4000 sat)."""
    wallet = _wallet_with_estimate(3000)
    assert _invalidation_output_value(wallet) == _BALANCE - 600


def test_invalidation_offline_pays_baltx_fees():
    """Offline: unchanged behaviour, 200 * 20 = 4000 sat."""
    wallet = _wallet_with_estimate(None)
    assert _invalidation_output_value(wallet) == _BALANCE - 4000


def test_fractional_fee_is_rounded_up_to_whole_satoshis():
    """1.5 sat/vbyte on 201 vbytes = 301.5 -> 302 sat (never below the rate)."""
    wallet = _wallet_with_estimate(1500)
    value = _invalidation_output_value(wallet, estimated_size=201)
    assert value == _BALANCE - 302
    assert isinstance(value, int)


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All invalidation fee tests passed")
