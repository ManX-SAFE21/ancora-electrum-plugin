#!/usr/bin/env python3
"""End-to-end GUI-level tests for the BAL will flows.

Exercises the real ``BalWindow`` controller (built via ``__new__`` to skip
the heavyweight tab construction) with a fake wallet/window, through the full
will lifecycle:

* prepare/build + persist (``prepare_will``)
* sign -> COMPLETE (``sign_transactions``)
* export -> merge roundtrip (``export_json_file`` / ``_load_will_file`` /
  ``merge_will``)
* insufficient-funds warning path

Run offline under the runtime venv:

    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_gui_will_flows.py
"""

import json
import os
import sys
import tempfile
import unittest.mock as mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from electrum import bitcoin, crypto  # noqa: E402
from electrum.descriptor import parse_descriptor  # noqa: E402
from electrum.transaction import (  # noqa: E402
    PartialTxInput,
    PartialTxOutput,
    TxOutpoint,
    tx_from_any,
)
from electrum.util import bfh  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bal.core.heirs import Heirs  # noqa: E402
from bal.core.plugin_base import BalPlugin  # noqa: E402
from bal.core.util import Util  # noqa: E402
from bal.core.will import Will  # noqa: E402
from bal.core.willexecutors import Willexecutors  # noqa: E402
from bal.gui.qt.window import BalWindow  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

PRIVKEY = bytes(range(32))
PUBKEY = crypto.privkey_to_pubkey(PRIVKEY)
ADDRESS = bitcoin.public_key_to_p2wpkh(PUBKEY)
SCRIPT = bitcoin.address_to_script(ADDRESS)
FUNDING_SATOSHIS = 500000


def make_funding_input():
    """Return a fake wallet UTXO spent by the will.

    The input has no descriptor attached: the controller only needs
    prevout + value + script for coin selection, and the wallet's
    ``sign_transaction`` re-attaches the descriptor after the PSBT
    roundtrip drops it.
    """
    utxo = PartialTxInput(prevout=TxOutpoint(bfh("11" * 32), 0))
    # A real wallet input carries a witness UTXO, which is the ONLY field that
    # survives the PSBT roundtrip the controller does (WillItem re-parses the tx
    # via get_tx_from_any). Without it the re-parsed tx loses the input value
    # and has no txid.
    utxo.witness_utxo = PartialTxOutput.from_address_and_value(ADDRESS, FUNDING_SATOSHIS)
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

    def get_transaction(self, txid):
        return None

    def commit(self):
        pass


class FakeWallet:
    def __init__(self, utxos):
        self.db = FakeDB()
        self.adb = None
        self.network = None
        self._utxos = list(utxos)
        self._dust = 546
        self._change_addresses = [ADDRESS]
        self.labels = {}
        self.save_db_calls = 0

    # -- wallet API used by the BAL core ---------------------------------- #
    def save_db(self):
        self.save_db_calls += 1

    def dust_threshold(self):
        return self._dust

    def has_keystore_encryption(self):
        return False

    def set_label(self, txid, label):
        self.labels[txid] = label

    def get_all_labels(self):
        return dict(self.labels)

    def get_label_for_txid(self, txid):
        return self.labels.get(txid, "")

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

    # -- signing ----------------------------------------------------------- #
    def sign_transaction(self, tx, password=None, ignore_warnings=True):
        # The controller passes a re-parsed transaction whose inputs lost the
        # descriptor and the trusted value through the PSBT roundtrip.
        descriptor = parse_descriptor(f"wpkh({PUBKEY.hex()})")
        for txin in tx.inputs():
            if txin.script_descriptor is None:
                txin.script_descriptor = descriptor
            if txin.value_sats() is None:
                txin._trusted_value_sats = FUNDING_SATOSHIS
        tx.sign({PUBKEY: PRIVKEY})


class FakeConfig:
    def __init__(self):
        self._data = {}
        self._tmpdir = tempfile.mkdtemp(prefix="bal-test-")

    def electrum_path(self):
        return self._tmpdir

    def user_dir(self):
        return self._tmpdir

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set_key(self, key, value, save=True):
        self._data[key] = value


class FakeWindow:
    def __init__(self, wallet):
        self.wallet = wallet
        self.messages = []
        self.warnings = []
        self.errors = []

    def get_decimal_point(self):
        return 0

    def show_message(self, text):
        self.messages.append(str(text))

    def show_warning(self, text, parent=None, title=None):
        self.warnings.append(str(text))

    def show_error(self, text):
        self.errors.append(str(text))

    def show_critical(self, text):
        self.errors.append(str(text))

    def update_status(self):
        pass


def make_controller(utxos=None):
    """Build a fully-wired BalWindow without constructing the Qt tabs."""
    utxos = [make_funding_input()] if utxos is None else utxos
    config = FakeConfig()
    wallet = FakeWallet(utxos)
    window = FakeWindow(wallet)

    plugin = BalPlugin(None, config, "bal")
    plugin.get_window_title = lambda title: str(title)
    # BalWindow.__init__ wires these onto the plugin; replicate since we skip it.
    plugin.get_decimal_point = window.get_decimal_point
    # Self-will-executor mode; init_class_variables() reads this from config and
    # would otherwise overwrite ctl.no_willexecutor with the (False) default.
    plugin.NO_WILLEXECUTOR.set(True)

    ctl = BalWindow.__new__(BalWindow)
    ctl.bal_plugin = plugin
    ctl.window = window
    ctl.wallet = wallet
    ctl.will = {}
    ctl.willitems = {}
    ctl.willexecutors = {}
    ctl.will_settings = plugin.WILL_SETTINGS.get()
    Util.fix_will_settings_tx_fees(ctl.will_settings)
    ctl.heirs = Heirs(wallet)
    ctl.heirs["alice"] = [ADDRESS, "100000", "1y"]
    ctl.heirs["bob"] = [ADDRESS, "100%", "1y"]
    ctl.no_willexecutor = True
    ctl.disable_plugin = False
    ctl.update_all = lambda: None
    ctl._schedule_history_refresh = lambda: None
    return ctl


def _no_willexecutors():
    """Force an empty will-executor list (offline tests)."""
    return mock.patch.object(
        Willexecutors,
        "get_willexecutors",
        return_value={},
    )


def _single(controller):
    """Return (txid, WillItem) for the controller's single will item."""
    assert len(controller.willitems) == 1, controller.willitems
    return next(iter(controller.willitems.items()))


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_prepare_will_builds_and_persists():
    with _no_willexecutors():
        ctl = make_controller()
        will = ctl.prepare_will()

    assert will, "prepare_will must return the built will"
    txid, item = _single(ctl)

    assert item.get_status("VALID"), "fresh items default to VALID"
    assert txid == item.tx.txid()
    assert isinstance(txid, str) and len(txid) == 64 and all(
        c in "0123456789abcdef" for c in txid
    ), "raw tx id expected (64-char hex, not a label/short id)"
    assert not item.tx.is_complete(), "unsigned will must not be complete"
    assert isinstance(item.tx.locktime, int) and item.tx.locktime > 0

    # Will item metadata mirrors the funded heir.
    heirs = item.heirs
    assert len(heirs) == 2
    assert heirs["alice"][0] == ADDRESS and int(heirs["alice"][1]) == 100000
    assert heirs["bob"][0] == ADDRESS
    assert int(heirs["bob"][3]) > 0, "percent heir got a real amount"
    assert int(heirs["alice"][3]) > 0

    # The full wallet balance is swept into the will; the only leftover is the
    # transaction fee.
    total = sum(int(h[3]) for h in heirs.values())
    assert FUNDING_SATOSHIS - total == item.tx.get_fee()

    # Unsigned partial transaction serialises to PSBT base64.
    serialized = str(item.tx)
    assert not serialized.startswith("02"), "partial must not be raw hex"

    # Nothing was pushed to the live history (no adb).
    assert "history" not in ctl.wallet.db._data
    assert ctl.wallet.save_db_calls == 0

    # The rebuilt, ready-to-sign will is labelled for Electrum's History tab and
    # the user is guided to the next manual step (sign).
    assert ctl.wallet.labels[txid] == "BAL Inheritance transaction"
    assert any("sign" in m.lower() for m in ctl.window.messages)
    assert not ctl.window.warnings, "a clean build must not warn"


def test_check_will_passes_after_build():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()
        assert ctl.check_will() is True


def test_sign_transitions_to_complete():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()
        txid, item = _single(ctl)

        txs = ctl.sign_transactions(None)

    assert txs and txid in txs
    signed = txs[txid]
    assert signed.is_complete(), "single-key p2wpkh must be fully signable"

    # Mirror BalWindow.ask_password_and_sign_transactions.on_success: store
    # the signed tx back into the will item and re-check signatures.
    item.tx = Will.get_tx_from_any(str(signed))
    Will.check_signatures(ctl.willitems, ctl.wallet)

    assert item.get_status("COMPLETE"), "signed item must become COMPLETE"
    assert item.sigs_have == 1 and item.sigs_required == 1
    assert item.tx.inputs()[0].is_segwit()

    # A complete tx serialises to raw hex.
    raw = str(item.tx)
    assert raw.startswith("02")
    # The raw serialization must roundtrip back into the same complete tx.
    reparsed = tx_from_any(raw)
    assert reparsed.is_complete()
    assert reparsed.txid() == signed.txid()


def test_export_and_merge_roundtrip():
    with _no_willexecutors():
        src = make_controller()
        src.prepare_will()
        txid, _ = _single(src)
        signed = src.sign_transactions(None)
        item = src.willitems[txid]
        item.tx = Will.get_tx_from_any(str(signed[txid]))
        Will.check_signatures(src.willitems, src.wallet)

        assert item.get_status("COMPLETE")

        tmpdir = tempfile.mkdtemp(prefix="bal-export-")
        path = os.path.join(tmpdir, "will.json")
        src.export_json_file(path, will=src.willitems)

        # The exported file is a JSON dict keyed by txid; the signed tx is
        # serialised as raw hex via MyEncoder.
        with open(path) as f:
            exported = json.load(f)
        assert list(exported) == [txid]
        assert exported[txid]["tx"] == str(item.tx)
        assert exported[txid]["COMPLETE"] is True
        assert exported[txid]["VALID"] is True

        # Fresh controller receives the exported will.
        dst = make_controller()
        imported = dst._load_will_file(path)
        dst.merge_will(imported)

    assert len(dst.willitems) == 1
    dst_txid, dst_item = _single(dst)
    assert dst_txid == txid, "merge must keep the same txid"
    assert dst_item.get_status("COMPLETE"), "signed status must survive merge"
    assert str(dst_item.tx) == str(item.tx), "signed raw tx must survive roundtrip"
    assert dst_item.tx.is_complete()


def test_insufficient_funds_warns():
    with _no_willexecutors():
        ctl = make_controller(utxos=[])

    will = ctl.prepare_will()

    assert not will, "no wallet funds must not build a will"
    assert ctl.window.warnings, "user must be warned about the balance"
    assert any("adjustment" in w.lower() for w in ctl.window.warnings)
    assert not ctl.willitems


def test_guard_not_blocked_by_old_built_will():
    """Regression: shortening the delivery in the STORED settings (relative
    "1y"/"30d") while an old, still-VALID built will is frozen at a longer
    locktime must NOT fire the "locktime is lower than threshold" guard.

    The old guard compared the fresh settings locktime against ``date_to_check``
    anchored to the built will (see ``resolve_date_to_check``), so a built-will
    delivery longer than the settings' one made it fire even though the settings
    are internally consistent (locktime is 30d AFTER the threshold).  The guard
    must instead compare the stored settings on a single reference frame
    (``BalWindow.is_locktime_below_threshold``); ``date_to_check`` keeps its
    built anchor for the expiry/validity checks.
    """
    with _no_willexecutors():
        ctl = make_controller()
        ctl.bal_plugin.USER_TYPE.set("advanced")  # ADVANCED Check-Alive mode
        ctl.prepare_will()
        txid, item = _single(ctl)

        # Freeze the built (VALID) will at a delivery one year longer than the
        # now-shortened settings: the pre-fix guard would reject the rebuild.
        item.tx.locktime = item.tx.locktime + 365 * 86400
        ctl.will_settings = {"locktime": "1y", "threshold": "30d"}
        Util.fix_will_settings_tx_fees(ctl.will_settings)

        ctl.init_class_variables()

        # date_to_check is anchored to the built will (long delivery)...
        assert ctl.date_to_check == item.tx.locktime - 30 * 86400
        # ...and the OLD guard would have fired here:
        old_locktime = Util.parse_locktime_string(ctl.will_settings["locktime"])
        assert old_locktime < ctl.date_to_check
        # but the settings themselves are consistent, so the guard must pass:
        assert ctl.is_locktime_below_threshold() is False
        assert not ctl.window.errors


def test_anticipated_rebuild_reanchors_date_to_check():
    """Regression (karen7): rebuilding a SIGNED will whose delivery was
    anticipated (per-heir recipes shortened from 2y to 1y, ADVANCED mode) must
    succeed.

    ``date_to_check`` stays anchored to the OLD built delivery for the validity
    checks, but ``build_will`` must re-anchor it to the NEW (earliest current)
    delivery as its build filter: before the fix the stale 2028 anchor rejected
    every "1y" heir (cmp <= 0 in ``fixed_percent_lists_amount``) and the build
    reported ``NO_FUTURE_DATE``.  The old signed item is then superseded by
    ``search_rai`` (REPLACED -> no on-chain invalidation) and the rebuilt will
    is coherent again.
    """
    with _no_willexecutors():
        ctl = make_controller()
        ctl.bal_plugin.USER_TYPE.set("advanced")
        # Per-heir deliveries require multiverse mode (the only way heirs can
        # carry a different recipe than the settings locktime).
        ctl.bal_plugin.ENABLE_MULTIVERSE.set(True)
        ctl.will_settings = {"locktime": "2y", "threshold": "150d", "baltx_fees": 20}
        Util.fix_will_settings_tx_fees(ctl.will_settings)
        ctl.heirs["alice"][2] = "2y"
        ctl.heirs["bob"][2] = "2y"

        # Build and sign a 2y will (the old, committed delivery).
        ctl.prepare_will()
        old_txid, _old_item = _single(ctl)
        old_locktime = _old_item.tx.locktime
        signed = ctl.sign_transactions(None)
        _old_item.tx = Will.get_tx_from_any(str(signed[old_txid]))
        Will.check_signatures(ctl.willitems, ctl.wallet)
        assert _old_item.get_status("COMPLETE")

        # Anticipate: shorten every heir to 1y.
        ctl.heirs["alice"][2] = "1y"
        ctl.heirs["bob"][2] = "1y"

        ctl.init_class_variables()
        # date_to_check stays anchored to the OLD built delivery...
        assert ctl.date_to_check == old_locktime - 150 * 86400
        # ...and that stale anchor would reject the anticipated "1y" dates.
        assert Util.parse_locktime_string("1y") < ctl.date_to_check

        # The rebuild must succeed (re-anchored to the new delivery).
        willitems = ctl.build_inheritance_transaction()

    assert ctl.heirs.last_build_error is None, "NO_FUTURE_DATE must not fire"
    new_valid = [
        it for tid, it in willitems.items()
        if tid != old_txid and it.get_status("VALID")
    ]
    assert new_valid, "the anticipated (1y) will must build and stay VALID"
    new_item = new_valid[0]
    assert new_item.tx.locktime < old_locktime, "delivery must be anticipated"
    # date_to_check was re-anchored to the rebuilt delivery (1y minus 150d).
    assert abs(ctl.date_to_check - (new_item.tx.locktime - 150 * 86400)) < 3600

    # The old signed item is kept but superseded (REPLACED -> not VALID).
    assert _old_item.get_status("REPLACED") is True
    assert _old_item.get_status("VALID") is False

    # The rebuilt will is coherent (plain rebuild, no on-chain invalidation).
    assert ctl.check_will() is True
    assert not any("delivery date" in m for m in ctl.window.messages)
    assert not ctl.window.errors


def _run_all():
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__} ... ", end="", flush=True)
        fn()
        print("OK")
    print(f"\n{len(tests)} tests passed")


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    _run_all()
