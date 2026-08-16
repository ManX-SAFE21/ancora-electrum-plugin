#!/usr/bin/env python3
"""Tests for the headless auto-rebuild flow (``bal_will_autorebuild``).

The CLI equivalent of the GUI AUTO_REBUILD feature:
``BalController.auto_rebuild`` runs the wizard's close-time flow in a single
call.  Everything is exercised offline against a fake signing wallet (the same
fixtures the GUI tests use), so no wallet, network or Qt is needed.

Covers:

  * no-op when the will is still valid (``valid``);
  * rebuild + sign + push when a new UTXO invalidates the will (``rebuilt``,
    no on-chain invalidation: the rebuilt tx is anticipated to mine before
    the old one);
  * ``needs_signing`` when the wallet is encrypted;
  * on-chain invalidation when the will is already expired (``expired``);
  * on-chain invalidation when the anticipated locktime crosses the check-alive
    threshold (``anticipation_crossed``) - and no sign/push in that case.

The ``no_heirs`` and ``threshold_passed`` paths live in
``test_cli_controller_offline.py``.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_cli_autorebuild.py
"""

import os
import shutil
import sys
import tempfile
import time
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum import bitcoin, crypto
from electrum.descriptor import parse_descriptor
from electrum.simple_config import SimpleConfig
from electrum.transaction import PartialTxInput, PartialTxOutput, TxOutpoint
from electrum.util import bfh

from bal.cli.controller import BalController
from bal.core.will import Will
from bal.core.willexecutors import Willexecutors

PRIVKEY = bytes(range(32))
PUBKEY = crypto.privkey_to_pubkey(PRIVKEY)
ADDRESS = bitcoin.public_key_to_p2wpkh(PUBKEY)
SCRIPT = bitcoin.address_to_script(ADDRESS)
FUNDING_SATOSHIS = 500000


def make_funding_input(prevout_hex="11" * 32):
    """Return a fake wallet UTXO spendable by the will."""
    utxo = PartialTxInput(prevout=TxOutpoint(bfh(prevout_hex), 0))
    utxo.witness_utxo = PartialTxOutput.from_address_and_value(
        ADDRESS, FUNDING_SATOSHIS
    )
    utxo._trusted_value_sats = FUNDING_SATOSHIS
    utxo._TxInput__scriptpubkey = SCRIPT
    utxo._TxInput__address = ADDRESS
    return utxo


class FakeDB:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def put(self, key, value):
        self._data[key] = value

    def get_dict(self, key):
        return self._data.setdefault(key, {})

    def get_transaction(self, txid):
        return None

    def add_transaction(self, tx, *args, **kwargs):
        pass


class FakeWallet:
    def __init__(self, utxos, encrypted=False):
        self.db = FakeDB()
        self.adb = None
        self.network = None
        self._utxos = list(utxos)
        self._dust = 546
        self._change_addresses = [ADDRESS]
        self._encrypted = encrypted
        self.labels = {}

    def save_db(self):
        pass

    def dust_threshold(self):
        return self._dust

    def has_keystore_encryption(self):
        return self._encrypted

    def set_label(self, txid, label):
        self.labels[txid] = label

    def get_utxos(self):
        return list(self._utxos)

    def get_change_addresses_for_new_transaction(self, *args, **kwargs):
        return self._change_addresses

    def add_input_info(self, txin, only_der_suffix=False):
        pass

    def add_output_info(self, txout, only_der_suffix=False):
        pass

    def get_tx_info(self, tx):
        class _TxInfo:
            def __init__(self):
                class _MinedStatus:
                    def height(self):
                        return 0

                self.tx_mined_status = _MinedStatus()

        return _TxInfo()

    def get_transaction(self, txid):
        return None

    def sign_transaction(self, tx, password=None, ignore_warnings=True):
        descriptor = parse_descriptor(f"wpkh({PUBKEY.hex()})")
        for txin in tx.inputs():
            if txin.script_descriptor is None:
                txin.script_descriptor = descriptor
            if txin.value_sats() is None:
                txin._trusted_value_sats = FUNDING_SATOSHIS
        tx.sign({PUBKEY: PRIVKEY})


class _Plugin:
    """Real ``bal.cli.plugin.Plugin`` with an isolated config directory."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bal_cli_autorebuild_")
        from bal.cli.plugin import Plugin as RealPlugin

        self.config = SimpleConfig(
            {"electrum_path": self.tmpdir},
            read_user_config_function=lambda path: {},
        )
        self.plugin = RealPlugin(None, self.config, "bal")

    def __enter__(self):
        return self.plugin

    def __exit__(self, *exc):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _no_willexecutors():
    """Force an empty will-executor list (offline tests)."""
    return mock.patch.object(
        Willexecutors,
        "get_willexecutors",
        return_value={},
    )


def _make_controller(plugin, wallet):
    c = BalController(plugin, wallet)
    c.will_settings["locktime"] = "1y"
    c.heirs_add("alice", ADDRESS, "100000")
    c.heirs_add("bob", ADDRESS, "100%")
    return c


def _single(controller):
    """Return (txid, WillItem) for the controller's single will item."""
    assert len(controller.willitems) == 1, controller.willitems
    return next(iter(controller.willitems.items()))


def _item_spending(controller, *prevout_hexes):
    """Return the will item whose tx spends exactly the given prevouts."""
    wanted = sorted(h for h in prevout_hexes)
    items = [
        item
        for item in controller.willitems.values()
        if sorted(i.prevout.txid.hex() for i in item.tx.inputs()) == wanted
    ]
    assert len(items) == 1, controller.willitems
    return items[0]


# --------------------------------------------------------------------------- #
# auto_rebuild behaviour
# --------------------------------------------------------------------------- #

def test_auto_rebuild_noop_when_will_valid():
    with _no_willexecutors():
        with _Plugin() as plugin:
            plugin.NO_WILLEXECUTOR.set(True)
            wallet = FakeWallet([make_funding_input()])
            c = _make_controller(plugin, wallet)
            c.prepare_will()
            txid_before, _ = _single(c)

            result = c.auto_rebuild()

    assert result["result"] == "valid", result
    assert next(iter(c.willitems)) == txid_before, (
        "a valid will must not be rebuilt"
    )


def test_auto_rebuild_rebuilds_and_pushes_on_new_utxo():
    with _no_willexecutors():
        with _Plugin() as plugin:
            plugin.NO_WILLEXECUTOR.set(True)
            wallet = FakeWallet([make_funding_input()])
            c = _make_controller(plugin, wallet)
            c.prepare_will()
            old_txid, old_item = _single(c)
            old_locktime = int(old_item.tx.locktime)

            # An incoming payment adds a second UTXO -> the will no longer
            # covers the whole wallet (NotCompleteWillException).
            wallet._utxos.append(make_funding_input("22" * 32))

            result = c.auto_rebuild()

    assert result["result"] == "rebuilt", result
    assert result["push"] == {}

    new_item = _item_spending(c, "11" * 32, "22" * 32)
    assert new_item.tx.txid() != old_txid, "the rebuilt will must replace the old tx"
    assert int(new_item.tx.locktime) <= old_locktime, (
        "the rebuilt tx must be anticipatable before the old will"
    )
    assert new_item.get_status("COMPLETE"), "passwordless rebuild must sign"
    assert new_item.get_status("VALID")

    # The rebuilt will is still valid now: no further work.
    assert c.check_will() is True


def test_auto_rebuild_encrypted_wallet_requires_manual_signing():
    with _no_willexecutors():
        with _Plugin() as plugin:
            plugin.NO_WILLEXECUTOR.set(True)
            wallet = FakeWallet([make_funding_input()], encrypted=True)
            c = _make_controller(plugin, wallet)
            c.prepare_will()
            wallet._utxos.append(make_funding_input("22" * 32))

            result = c.auto_rebuild()

    assert result["result"] == "needs_signing", result
    assert result["will"]["count"] == 2
    assert not any(w.get_status("COMPLETE") for w in c.willitems.values()), (
        "an encrypted wallet must never be signed without the password"
    )


def test_auto_rebuild_invalidates_when_locktime_expired():
    with _no_willexecutors():
        with _Plugin() as plugin:
            plugin.NO_WILLEXECUTOR.set(True)
            wallet = FakeWallet([make_funding_input()])
            c = _make_controller(plugin, wallet)
            c.prepare_will()
            _, item = _single(c)
            # Move the frozen delivery date into the past: "too late to
            # anticipate" -> the old will must be invalidated on-chain.
            item.tx.locktime = int(time.time()) - 2 * 86400

            result = c.auto_rebuild()

    assert result["result"] == "invalidated", result
    assert result["reason"] == "expired"
    assert result["invalidation_tx"]["txid"] is not None
    assert result["invalidation_tx"]["tx"]
    assert not any(w.get_status("COMPLETE") for w in c.willitems.values())


def test_auto_rebuild_invalidates_when_anticipation_crosses_threshold():
    with _no_willexecutors():
        with _Plugin() as plugin:
            now = time.time()
            delivery = int(now + 3 * 86400)
            # ADVANCED mode: the check-alive threshold sits 12h before delivery,
            # so an anticipated (delivery - 1 day) locktime falls BEFORE it.
            plugin.USER_TYPE.set("advanced")
            wallet = FakeWallet([make_funding_input()])
            plugin.NO_WILLEXECUTOR.set(True)
            c = _make_controller(plugin, wallet)
            c.will_settings["locktime"] = delivery
            c.will_settings["threshold"] = delivery - 12 * 3600
            c.prepare_will()
            old_txid, _ = _single(c)
            wallet._utxos.append(make_funding_input("22" * 32))

            # Force the anticipating branch (see the GUI test for the rationale:
            # with a new UTXO the real amounts change, so the natural rebuild
            # keeps the old locktime).
            with mock.patch.object(
                Will, "check_anticipate", return_value=delivery - 86400
            ):
                result = c.auto_rebuild()

    assert result["result"] == "invalidated", result
    assert result["reason"] == "anticipation_crossed"
    assert not any(w.get_status("COMPLETE") for w in c.willitems.values()), (
        "after an invalidation the rebuilt will must NOT be signed/pushed "
        "(the wizard stops and waits for the invalidation to confirm)"
    )
    new_item = _item_spending(c, "11" * 32, "22" * 32)
    assert new_item.tx.txid() != old_txid, "the rebuilt will must replace the old tx"
    assert int(new_item.tx.locktime) == delivery - 86400, (
        "the rebuilt locktime must be anticipated by one day"
    )


def _run_all():
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__} ... ", end="", flush=True)
        fn()
        print("OK")
    print(f"\n{len(tests)} tests passed")


if __name__ == "__main__":
    _run_all()
