#!/usr/bin/env python3
"""Tests for the "Rebuild automatically on new transactions" (AUTO_REBUILD)
feature.

Covers:

  * the persisted ``bal_auto_rebuild`` configuration key exists and defaults
    to OFF (False), and can be enabled and read back;
  * the event wiring: ``Plugin._wallet_activity`` schedules the rebuild only
    for the matching wallet and only when the setting is enabled;
  * ``BalWindow.schedule_auto_rebuild`` debounces through ``QTimer`` and the
    re-entrancy / cooldown guards;
  * ``BalWindow.maybe_auto_rebuild`` reproduces the wizard's close-time flow:
      - no-op when the will is still valid;
      - rebuild + sign + push when a new UTXO invalidates the will (no on-chain
        invalidation, the rebuilt tx is anticipated to mine before the old);
      - on-chain invalidation when the check-alive threshold is already in the
        past (CheckAliveError);
      - on-chain invalidation when the will is already expired;
      - on-chain invalidation when the anticipated locktime would fall before
        the check-alive threshold (and no sign/push in that case).

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_auto_rebuild_on_new_tx.py
"""

import os
import sys
import tempfile
import time
import unittest.mock as mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from electrum import bitcoin, crypto  # noqa: E402
from electrum.descriptor import parse_descriptor  # noqa: E402
from electrum.transaction import (  # noqa: E402
    PartialTxInput,
    PartialTxOutput,
    TxOutpoint,
)
from electrum.util import bfh  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bal.gui.qt.window as window_mod  # noqa: E402
from bal.core.heirs import Heirs  # noqa: E402
from bal.core.plugin_base import BalConfig, BalPlugin  # noqa: E402
from bal.core.util import Util  # noqa: E402
from bal.core.will import Will  # noqa: E402
from bal.core.willexecutors import Willexecutors  # noqa: E402
from bal.gui.qt.plugin import Plugin  # noqa: E402
from bal.gui.qt.window import BalWindow  # noqa: E402

CONFIG_KEY = "bal_auto_rebuild"

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

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

    def sign_transaction(self, tx, password=None, ignore_warnings=True):
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
    plugin.get_decimal_point = window.get_decimal_point
    plugin.NO_WILLEXECUTOR.set(True)
    plugin.AUTO_REBUILD.set(True)

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
    ctl.ok = True
    ctl.update_all = lambda: None
    ctl._schedule_history_refresh = lambda: None
    ctl._auto_rebuild_running = False
    ctl._auto_rebuild_cooldown_until = 0.0
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
# Config key
# --------------------------------------------------------------------------- #

def test_auto_rebuild_config_defaults_off():
    """bal_auto_rebuild must default to OFF (False) when not yet stored."""
    cfg = FakeConfig()
    rebuild = BalConfig(cfg, CONFIG_KEY, False)
    assert rebuild.get() is False


def test_auto_rebuild_config_can_be_enabled():
    """Once enabled and persisted, bal_auto_rebuild reads back True."""
    cfg = FakeConfig()
    rebuild = BalConfig(cfg, CONFIG_KEY, False)
    rebuild.set(True)
    assert rebuild.get() is True
    assert BalConfig(cfg, CONFIG_KEY, False).get() is True


# --------------------------------------------------------------------------- #
# Event wiring (Plugin._wallet_activity)
# --------------------------------------------------------------------------- #

def test_wallet_activity_schedules_only_matching_wallet():
    plugin = Plugin.__new__(Plugin)
    plugin.AUTO_REBUILD = BalConfig(FakeConfig(), CONFIG_KEY, True)
    wallet_a = FakeWallet([make_funding_input()])
    wallet_b = FakeWallet([make_funding_input()])

    scheduled = []

    class _Win:
        wallet = wallet_a
        ok = True
        disable_plugin = False

        def schedule_auto_rebuild(self):
            scheduled.append(self)

    win = _Win()
    plugin.bal_windows = {"a": win}

    plugin._wallet_activity(wallet_b)
    assert scheduled == [], "a different wallet must not schedule a rebuild"

    plugin._wallet_activity(wallet_a)
    assert scheduled == [win], "the matching wallet must schedule a rebuild"


def test_wallet_activity_skips_when_disabled():
    plugin = Plugin.__new__(Plugin)
    plugin.AUTO_REBUILD = BalConfig(FakeConfig(), CONFIG_KEY, False)
    wallet_obj = FakeWallet([make_funding_input()])

    scheduled = []

    class _Win:
        wallet = wallet_obj
        ok = True
        disable_plugin = False

        def schedule_auto_rebuild(self):
            scheduled.append(self)

    plugin.bal_windows = {"a": _Win()}

    plugin._wallet_activity(wallet_obj)
    assert scheduled == [], "AUTO_REBUILD off must not schedule anything"


# --------------------------------------------------------------------------- #
# Scheduling / guards
# --------------------------------------------------------------------------- #

def test_schedule_auto_rebuild_debounces():
    ctl = make_controller()
    with mock.patch.object(window_mod.QTimer, "singleShot") as single_shot:
        ctl.schedule_auto_rebuild()
    single_shot.assert_called_once_with(
        ctl._AUTO_REBUILD_DEBOUNCE_MS, ctl._run_auto_rebuild
    )


def test_auto_rebuild_guards():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()
    assert ctl._auto_rebuild_allowed() is True
    # Re-entrancy guard.
    ctl._auto_rebuild_running = True
    assert ctl._auto_rebuild_allowed() is False
    ctl._auto_rebuild_running = False
    # Cooldown guard.
    ctl._auto_rebuild_cooldown_until = time.time() + 100
    assert ctl._auto_rebuild_allowed() is False
    ctl._auto_rebuild_cooldown_until = 0.0
    assert ctl._auto_rebuild_allowed() is True
    # Disabled / inactive guards.
    ctl.disable_plugin = True
    assert ctl._auto_rebuild_allowed() is False
    ctl.disable_plugin = False
    ctl.ok = False
    assert ctl._auto_rebuild_allowed() is False


def test_run_auto_rebuild_spawns_worker_when_allowed():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()

    started = []

    class FakeThread:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            started.append(self.target)

    with mock.patch.object(window_mod.threading, "Thread", FakeThread):
        ctl._run_auto_rebuild()
    assert len(started) == 1, "the worker thread must be spawned"


# --------------------------------------------------------------------------- #
# maybe_auto_rebuild behaviour
# --------------------------------------------------------------------------- #

def test_auto_rebuild_noop_when_disabled():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()
        ctl.bal_plugin.AUTO_REBUILD.set(False)
        txid_before, _ = _single(ctl)

        result = ctl.maybe_auto_rebuild()

    assert result is False
    txid_after, _ = _single(ctl)
    assert txid_after == txid_before, "disabled flow must not touch the will"


def test_auto_rebuild_noop_without_will():
    with _no_willexecutors():
        ctl = make_controller()
        assert not ctl.willitems
        result = ctl.maybe_auto_rebuild()
    assert result is False


def test_auto_rebuild_noop_when_will_valid():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()
        txid_before, _ = _single(ctl)

        with mock.patch.object(ctl, "_auto_invalidate_will") as inv, mock.patch.object(
            ctl, "_auto_sign_save_push"
        ) as sign:
            result = ctl.maybe_auto_rebuild()

    assert result is False
    txid_after, _ = _single(ctl)
    assert txid_after == txid_before, "a valid will must not be rebuilt"
    inv.assert_not_called()
    sign.assert_not_called()


def test_auto_rebuild_rebuilds_and_pushes_on_new_utxo():
    with _no_willexecutors():
        ctl = make_controller()
        # A relative delivery recipe keeps the will coherent after the rebuild
        # anticipates the locktime by one day (an absolute recipe would read the
        # anticipated tx as a postpone, see check_willexecutors_and_heirs).
        ctl.will_settings["locktime"] = "1y"
        ctl.prepare_will()
        old_txid, old_item = _single(ctl)
        old_locktime = int(old_item.tx.locktime)

        # An incoming payment adds a second UTXO -> the will no longer covers
        # the whole wallet (NotCompleteWillException).
        ctl.wallet._utxos.append(make_funding_input("22" * 32))

        with mock.patch.object(ctl, "_auto_invalidate_will") as inv, mock.patch.object(
            ctl, "push_transactions_to_willexecutors"
        ) as push, mock.patch.object(ctl, "_save_will_to_history") as history:
            result = ctl.maybe_auto_rebuild()

    assert result is True, "a stale will must be rebuilt"
    assert inv.call_count == 0, "a plain rebuild must not invalidate on-chain"
    push.assert_called_once()
    history.assert_called_once()

    # The rebuilt will now spends BOTH wallet UTXOs (BAL keeps the previous
    # single-input transaction alongside it in the will).
    new_item = _item_spending(ctl, "11" * 32, "22" * 32)
    assert new_item.tx.txid() != old_txid, "the rebuilt will must replace the old tx"
    # The new locktime must be at most the old one, so the new tx can be mined
    # before the previous will.
    assert int(new_item.tx.locktime) <= old_locktime
    assert new_item.get_status("COMPLETE"), "passwordless rebuild must sign"
    assert new_item.get_status("VALID")

    # The rebuilt will is still valid now: no further work.
    assert ctl.check_will() is True


def test_auto_rebuild_invalidates_when_threshold_passed():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()
        # ADVANCED mode with a check-alive threshold already in the past.
        ctl.bal_plugin.USER_TYPE.set("advanced")
        ctl.will_settings["threshold"] = int(time.time()) - 3600

        with mock.patch.object(ctl, "_auto_invalidate_will") as inv, mock.patch.object(
            ctl, "_auto_sign_save_push"
        ) as sign:
            result = ctl.maybe_auto_rebuild()

    assert result is True
    inv.assert_called_once()
    sign.assert_not_called()


def test_auto_rebuild_invalidates_when_locktime_expired():
    with _no_willexecutors():
        ctl = make_controller()
        ctl.prepare_will()
        txid, item = _single(ctl)
        # Move the frozen delivery date into the past: "too late to
        # anticipate" -> the old will must be invalidated on-chain.
        item.tx.locktime = int(time.time()) - 2 * 86400

        with mock.patch.object(ctl, "_auto_invalidate_will") as inv, mock.patch.object(
            ctl, "_auto_sign_save_push"
        ) as sign:
            result = ctl.maybe_auto_rebuild()

    assert result is True
    inv.assert_called_once()
    sign.assert_not_called()


def test_auto_rebuild_invalidates_when_anticipation_crosses_threshold():
    with _no_willexecutors():
        ctl = make_controller()
        now = time.time()
        delivery = int(now + 3 * 86400)
        ctl.will_settings["locktime"] = delivery
        # ADVANCED mode: the check-alive threshold sits 12h before delivery, so
        # an anticipated (delivery - 1 day) locktime falls BEFORE it.
        ctl.bal_plugin.USER_TYPE.set("advanced")
        ctl.will_settings["threshold"] = delivery - 12 * 3600

        ctl.prepare_will()
        old_txid, _ = _single(ctl)
        ctl.wallet._utxos.append(make_funding_input("22" * 32))

        # The rebuild itself anticipates the delivery date by one day ONLY when
        # the rebuilt transactions keep the same real amounts (Will.check_anticipate,
        # same coins + same heirs).  Real amounts are re-computed against the
        # wallet balance, so a new UTXO normally changes them and the rebuilt
        # will keeps the old locktime.  Force the anticipating branch here to
        # exercise the "anticipated locktime crosses the threshold" handling.
        with mock.patch.object(
            Will, "check_anticipate", return_value=delivery - 86400
        ):
            with mock.patch.object(
                ctl, "_auto_invalidate_will"
            ) as inv, mock.patch.object(ctl, "_auto_sign_save_push") as sign:
                result = ctl.maybe_auto_rebuild()

    assert result is True
    inv.assert_called_once(), (
        "an anticipated locktime below the threshold must invalidate on-chain"
    )
    sign.assert_not_called(), (
        "after an invalidation the rebuilt will must NOT be signed/pushed "
        "(the wizard stops and waits for the invalidation to confirm)"
    )
    new_item = _item_spending(ctl, "11" * 32, "22" * 32)
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
    app = QApplication.instance() or QApplication([])
    _run_all()
