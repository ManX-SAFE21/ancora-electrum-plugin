"""
Group E - karen7 wallet: build the inheritance then generate the
cancellation (invalidation) transaction.

This test exercises the full pipeline with REAL Electrum transaction
building (no mocking of from_io, from_address_and_value, or is_address):

  1. Load the karen7 regtest wallet (heirs + UTXOs).
  2. Set Electrum to regtest mode so bcrt1q addresses validate.
  3. Build the inheritance transactions via ``Heirs.buildTransactions``
     using real ``PartialTransaction.from_io`` and real
     ``PartialTxOutput.from_address_and_value``.
  4. Wrap each built transaction into a ``WillItem`` with VALID status.
  5. Populate ``_trusted_value_sats`` on each input (what
     ``add_info_from_wallet`` does in the real flow).
  6. Call ``Will.invalidate_will()`` to generate the cancellation tx.
  7. Assert that the cancellation tx is well-formed.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 -m pytest tests/test_group_e_karen7_invalidate.py -q
"""

import copy
import json
import os
import sys

import pytest  # pyright: ignore[reportMissingImports]

# ------------------------------------------------------------------ #
# Electrum regtest mode (replaces mocking bitcoin.is_address)
# ------------------------------------------------------------------ #
from electrum import constants

constants.net = constants.BitcoinRegtest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum import bitcoin
from electrum.transaction import (
    PartialTransaction,
    PartialTxInput,
    TxOutpoint,
)
from electrum.util import bfh

from bal.core.heirs import Heirs
from bal.core.will import Will, WillItem

# ------------------------------------------------------------------ #
# Load karen7 wallet data
# ------------------------------------------------------------------ #

_WALLET_PATH = os.path.join(os.path.dirname(__file__), "karen7")
with open(_WALLET_PATH) as _f:
    _KAREN7_DATA = json.load(_f)


# ------------------------------------------------------------------ #
# Minimal real implementations (no MagicMock)
# ------------------------------------------------------------------ #

class _Karen7Wallet:
    """Minimal wallet implementation for tests.

    Provides only the methods that ``buildTransactions`` and
    ``invalidate_will`` call.  ``network`` is ``None`` so
    ``Util.get_current_height`` returns 0 without network access.
    """

    _CHANGE_ADDR = "bcrt1q0567jspgutk84axs4l7sm04u86yjkzg27dv6fk"

    def __init__(self, utxos):
        self._utxos = utxos
        self.network = None

    def dust_threshold(self):
        return 546

    def get_change_addresses_for_new_transaction(self):
        return [self._CHANGE_ADDR]

    def get_utxos(self):
        return self._utxos


class _Karen7BalPlugin:
    """Minimal bal_plugin config for tests.

    Provides only the config accessors that ``buildTransactions`` reads.
    No will-executors (``NO_WILLEXECUTOR = True``).
    """

    class _NoWillexecutor:
        def get(self, *a, **kw):
            return True

    class _MaxFee:
        def get(self, *a, **kw):
            return 500000

    class _EmptyWelist:
        default = {}
        def get(self, *a, **kw):
            return {"regtest": {}}

    NO_WILLEXECUTOR = _NoWillexecutor()
    MAX_WILLEXECUTOR_FEE = _MaxFee()
    WILLEXECUTORS = _EmptyWelist()

    def get_decimal_point(self):
        return 8


# ------------------------------------------------------------------ #
# UTXO builder from karen7 data (real PartialTxInput objects)
# ------------------------------------------------------------------ #

def _build_real_utxos(data):
    """Build real ``PartialTxInput`` objects from the karen7 wallet JSON.

    Each UTXO gets a proper ``scriptpubkey`` so that ``is_segwit()``
    returns ``True`` and the resulting ``PartialTransaction`` can
    compute a real ``txid()``.
    """
    utxos = []
    txo = data.get("txo", {})
    for txid, outputs in txo.items():
        if not isinstance(outputs, dict):
            continue
        for addr, out_map in outputs.items():
            if not isinstance(out_map, dict):
                continue
            for idx, info in out_map.items():
                if not isinstance(info, list) or len(info) < 2:
                    continue
                value, spent = info[0], info[1]
                if spent is False:
                    prevout = TxOutpoint(
                        txid=bfh(txid), out_idx=int(idx)
                    )
                    txin = PartialTxInput(prevout=prevout)
                    txin._trusted_value_sats = value
                    txin._TxInput__address = addr
                    txin._TxInput__scriptpubkey = bitcoin.address_to_script(
                        addr
                    )
                    txin.is_mine = True
                    utxos.append(txin)
    return utxos


# ------------------------------------------------------------------ #
# Build karen7 UTXO value lookup (for populating tx inputs)
# ------------------------------------------------------------------ #

def _build_utxo_value_map(data):
    """Return ``{prevout_str: value_sats}`` from karen7 wallet data."""
    m = {}
    txo = data.get("txo", {})
    for txid, outputs in txo.items():
        if not isinstance(outputs, dict):
            continue
        for _, out_map in outputs.items():
            if not isinstance(out_map, dict):
                continue
            for idx, info in out_map.items():
                if not isinstance(info, list) or len(info) < 2:
                    continue
                value, spent = info[0], info[1]
                if spent is False:
                    m[f"{txid}:{idx}"] = value
    return m


# ------------------------------------------------------------------ #
# Populate _trusted_value_sats on WillItem tx inputs
# ------------------------------------------------------------------ #

def _populate_input_values(will, utxo_value_map):
    """Set ``_trusted_value_sats`` on every input of every will tx.

    This is the equivalent of what ``add_info_from_wallet`` does in the
    real flow: looking up the UTXO value and attaching it to the input.
    """
    for _, wi in will.items():
        for txin in wi.tx.inputs():
            prevout_str = txin.prevout.to_str()
            if txin._trusted_value_sats is None and prevout_str in utxo_value_map:
                txin._trusted_value_sats = utxo_value_map[prevout_str]


# ------------------------------------------------------------------ #
# Inheritance builder (real Electrum, no mocking)
# ------------------------------------------------------------------ #

def _build_inheritance(utxos):
    """Build the inheritance transactions from karen7's heirs and UTXOs.

    Returns ``(txs, heirs_model)`` where ``txs`` is a dict of real
    ``PartialTransaction`` objects produced by ``Heirs.buildTransactions``.
    """
    heirs_data = _KAREN7_DATA["heirs"]
    h = Heirs.__new__(Heirs)
    h.update(heirs_data)

    wallet = _Karen7Wallet(utxos)
    bal_plugin = _Karen7BalPlugin()

    txs = h.buildTransactions(bal_plugin, wallet, tx_fees=1, utxos=utxos)
    return txs or {}, h


def _txs_to_will(txs, heirs_data):
    """Convert built transactions into a ``{txid: WillItem}`` will dict
    with VALID status, using karen7's heir data."""
    will = {}
    for txid, tx in txs.items():
        item_dict = {
            "tx": tx,
            "heirs": copy.deepcopy(heirs_data),
            "willexecutor": None,
            "status": "",
            "description": "",
            "time": 0,
            "change": "",
            "baltx_fees": 1,
        }
        wi = WillItem(item_dict, _id=txid)
        will[txid] = wi
    return will


# ================================================================== #
# Build and invalidate tests
# ================================================================== #

class TestKaren7BuildAndInvalidate:
    """Load the real karen7 regtest wallet, build the inheritance
    transactions with real Electrum, then generate the cancellation
    (invalidation) transaction."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Shared setup: build UTXOs, inheritance, and will once."""
        self.utxos = _build_real_utxos(_KAREN7_DATA)
        self.utxo_value_map = _build_utxo_value_map(_KAREN7_DATA)
        assert len(self.utxos) > 0, "no UTXOs in karen7 wallet"

        self.heirs_data = _KAREN7_DATA["heirs"]
        self.txs, self.heirs_model = _build_inheritance(self.utxos)

        self.wallet = _Karen7Wallet(self.utxos)
        self.will = _txs_to_will(self.txs, self.heirs_data)
        _populate_input_values(self.will, self.utxo_value_map)

    # ------------------------------------------------------------------ #
    # Build tests
    # ------------------------------------------------------------------ #

    def test_build_produces_real_partial_transactions(self):
        """Building the inheritance produces real PartialTransaction objects."""
        assert self.txs, "buildTransactions returned empty"
        for txid, tx in self.txs.items():
            assert isinstance(tx, PartialTransaction), (
                f"tx {txid} should be a real PartialTransaction, "
                f"got {type(tx).__name__}"
            )

    def test_built_txs_have_valid_txid(self):
        """Every built transaction has a computable txid (not None)."""
        assert self.txs, "no transactions built"
        for txid, tx in self.txs.items():
            computed = tx.txid()
            assert computed is not None, (
                f"tx {txid} has txid() == None"
            )
            assert computed == txid, (
                f"txid mismatch: key={txid}, computed={computed}"
            )

    def test_built_tx_has_karen7_heirs(self):
        """The built will contains karen7's four heirs."""
        assert len(self.heirs_model) == 4
        assert list(self.heirs_model.keys()) == [
            "aaaa", "lucia", "mario", "mario2"
        ]

    def test_will_items_are_valid(self):
        """Every WillItem in the will starts with VALID=True."""
        assert self.will, "will is empty"
        for wid, wi in self.will.items():
            assert wi.get_status("VALID") is True, (
                f"WillItem {wid} should be VALID"
            )

    def test_will_inputs_have_values(self):
        """After populating, every tx input has a non-None value_sats."""
        for wid, wi in self.will.items():
            for i, txin in enumerate(wi.tx.inputs()):
                assert txin.value_sats() is not None, (
                    f"WillItem {wid} input {i} "
                    f"({txin.prevout.to_str()}) has no value"
                )

    # ------------------------------------------------------------------ #
    # Invalidation tests
    # ------------------------------------------------------------------ #

    def test_invalidate_returns_real_tx(self):
        """Calling invalidate_will produces a real PartialTransaction."""
        result = Will.invalidate_will(self.will, self.wallet, 10)
        assert result is not None, "invalidate_will returned None"
        assert isinstance(result, PartialTransaction), (
            f"expected PartialTransaction, got {type(result).__name__}"
        )

    def test_invalidation_tx_has_rbf(self):
        """The cancellation tx has RBF enabled."""
        result = Will.invalidate_will(self.will, self.wallet, 10)
        assert result is not None
        assert result.is_rbf_enabled() is True

    def test_invalidation_tx_locktime(self):
        """The cancellation tx locktime equals the current height.

        With ``network=None`` the current height is 0.
        """
        result = Will.invalidate_will(self.will, self.wallet, 10)
        assert result is not None
        assert result.locktime == 0

    def test_invalidation_tx_version_2(self):
        """The cancellation tx uses Bitcoin transaction version 2."""
        result = Will.invalidate_will(self.will, self.wallet, 10)
        assert result is not None
        assert result.version == 2

    def test_invalidation_spends_correct_utxos(self):
        """The cancellation tx spends the same UTXOs as the will."""
        result = Will.invalidate_will(self.will, self.wallet, 10)
        assert result is not None

        will_prevouts = set()
        for wi in self.will.values():
            for txin in wi.tx.inputs():
                will_prevouts.add(txin.prevout.to_str())

        for txin in result.inputs():
            assert txin.prevout.to_str() in will_prevouts, (
                f"inval input {txin.prevout.to_str()} not in will UTXOs"
            )

    def test_invalidation_output_to_change_address(self):
        """The cancellation output goes to the wallet's change address."""
        result = Will.invalidate_will(self.will, self.wallet, 10)
        assert result is not None

        outputs = result.outputs()
        assert len(outputs) == 1
        assert outputs[0].address == _Karen7Wallet._CHANGE_ADDR

    def test_invalidation_output_value_deducts_fee(self):
        """The output value equals balance minus estimated fee.

        balance = sum of input values (from will inputs).
        fee = estimated_size * fees_per_byte.
        """
        fees_per_byte = 10
        result = Will.invalidate_will(
            self.will, self.wallet, fees_per_byte
        )
        assert result is not None

        balance = sum(txin.value_sats() for txin in result.inputs()
                       if txin.value_sats() is not None)
        fee = result.estimated_size() * fees_per_byte
        expected = balance - fee

        assert result.outputs()[0].value == expected, (
            f"output value {result.outputs()[0].value} != "
            f"expected {expected} (balance={balance}, fee={fee})"
        )

    def test_all_invalidated_returns_none(self):
        """When all will items are INVALIDATED, returns None."""
        for wid in self.will:
            self.will[wid].set_status("INVALIDATED", True)

        result = Will.invalidate_will(self.will, self.wallet, 10)
        assert result is None

    def test_empty_will_returns_none(self):
        """An empty will dictionary returns None."""
        result = Will.invalidate_will({}, self.wallet, 10)
        assert result is None
