"""
Offline tests for the headless ``bal.cli.controller.BalController``.

These run without a wallet, a network or Qt: the controller is exercised
against a ``FakeWallet`` plus a real ``bal.cli.plugin.Plugin`` backed by an
isolated in-memory ``SimpleConfig``.  Only the flows that never touch the
network (settings/heirs/willexecutors CRUD, status snapshots, error mapping)
are covered here; build/sign/push flows need a live wallet and network and are
exercised by the group tests instead.

Run:
    source electrum/env/bin/activate
    python3 tests/test_cli_controller_offline.py
"""

import os
import shutil
import sys
import tempfile
import time
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.simple_config import SimpleConfig
from electrum.util import UserFacingException

from bal.cli.controller import BalController
from bal.core.heirs import Heirs
from bal.core.util import Util

VALID_ADDRESS = "bc1qusymuetsz2psaqzqxv8qmzcy64d9meckj3lxxf"


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
    def __init__(self):
        self.db = FakeDB()
        self.network = None
        self.adb = None
        self._dust = 500

    def save_db(self):
        pass

    def dust_threshold(self):
        return self._dust

    def has_keystore_encryption(self):
        return False

    def set_label(self, txid, text):
        pass

    def get_utxos(self):
        return []

    def get_change_addresses_for_new_transaction(self, *args, **kwargs):
        return [VALID_ADDRESS]


class Plugin:
    """Real ``bal.cli.plugin.Plugin`` with an isolated config directory."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bal_cli_test_")
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


def _make_controller(plugin):
    return BalController(plugin, FakeWallet())


def test_controller_init_empty():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        assert c.willitems == {}
        assert c.will == {}
        assert c.heirs == {}
        assert isinstance(c.will_settings, dict)
        assert "baltx_fees" in c.will_settings
        # Fresh config: no stored will-executors.  On mainnet the default
        # WILLEXECUTORS table is keyed by "mainnet" while chainname is
        # "bitcoin", so nothing is injected either.
        assert c.willexecutors == {}
        assert c.no_willexecutor is False


def test_settings_roundtrip():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        listing = c.settings_list()
        assert "BAL_TX_FEES" in listing or "TX_FEES" in listing
        tx_key = "BAL_TX_FEES" if "BAL_TX_FEES" in listing else "TX_FEES"
        assert c.settings_get(tx_key)["value"] == 100

        c.settings_set("bal_tx_fees", "150")
        assert c.settings_get("bal_tx_fees")["value"] == 150
        assert c.settings_get("TX_FEES")["value"] == 150

        c.settings_set("bal_no_willexecutor", "true")
        assert c.settings_get("bal_no_willexecutor")["value"] is True

        c.settings_reset("bal_tx_fees")
        assert c.settings_get("bal_tx_fees")["value"] == 100


def test_settings_unknown_key():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        try:
            c.settings_get("bal_does_not_exist")
            raise AssertionError("expected UserFacingException")
        except UserFacingException as e:
            assert "Unknown BAL setting" in str(e)


def test_heirs_crud():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        c.heirs_add("alice", VALID_ADDRESS, "100000")
        assert c.heirs["alice"][0] == VALID_ADDRESS
        assert c.heirs["alice"][1] == "100000"

        c.heirs_update("alice", amount="200000")
        assert c.heirs["alice"][1] == "200000"
        assert c.heirs_show("alice")["value"][1] == "200000"

        assert "alice" in c.heirs_list()
        c.heirs_delete(["alice"])
        assert "alice" not in c.heirs_list()


def test_heirs_add_op_return():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        c.heirs_add("note", "OP_RETURN:6a0242414c", "100000")
        assert c.heirs["note"][1] == "0"


def test_willexecutors_crud():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        assert c.willexecutors == {}

        new_url = "https://executor.example.invalid"
        c.willexecutors_add(new_url, address="", base_fee=250)
        assert c.willexecutors_show(new_url)["willexecutor"]["base_fee"] == 250
        assert c.willexecutors_show(new_url)["willexecutor"]["selected"] is False

        c.willexecutors_update(new_url, base_fee="300", info="Example executor")
        assert c.willexecutors_show(new_url)["willexecutor"]["base_fee"] == 300

        c.willexecutors_select([new_url], select=True)
        assert c.willexecutors_show(new_url)["willexecutor"]["selected"] is True

        renamed = "https://executor2.example.invalid"
        c.willexecutors_update(new_url, rename_to=renamed)
        assert renamed in c.willexecutors
        assert new_url not in c.willexecutors

        assert c.willexecutors_delete([renamed]) == {"deleted": [renamed]}
        assert renamed not in c.willexecutors


def test_will_status_empty():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        status = c.will_status()
        assert status["count"] == 0
        assert status["items"] == []


def test_will_check_no_heirs_raises():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        try:
            c.will_check()
            raise AssertionError("expected UserFacingException")
        except UserFacingException as e:
            assert "heir" in str(e).lower()


def test_auto_rebuild_no_heirs():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        assert c.auto_rebuild() == {"result": "no_heirs"}


def test_auto_rebuild_threshold_passed_invalidates():
    with Plugin() as plugin:
        c = _make_controller(plugin)
        c.heirs_add("alice", VALID_ADDRESS, "100000")
        plugin.USER_TYPE.set("advanced")
        c.will_settings["threshold"] = int(time.time()) - 3600
        result = c.auto_rebuild()
        assert result["result"] == "invalidated"
        assert result["reason"] == "threshold_passed"
        assert result["invalidation_tx"] == {"txid": None, "tx": None}


def test_build_will_reanchors_date_to_check_to_new_locktime():
    """CLI mirror of the GUI regression: ``build_will`` must re-anchor
    ``date_to_check`` to the CURRENT heirs' earliest delivery before building,
    so an anticipated (shortened) rebuild is not blocked by the old built-will
    anchor (which would yield NO_FUTURE_DATE in ``get_transactions``).
    """
    with Plugin() as plugin:
        plugin.USER_TYPE.set("advanced")
        plugin.NO_WILLEXECUTOR.set(True)
        plugin.ENABLE_MULTIVERSE.set(True)
        plugin.WILL_SETTINGS.set({"threshold": "150d", "locktime": "2y", "baltx_fees": 20})
        c = _make_controller(plugin)
        c.no_willexecutor = True
        c.heirs["alice"] = [VALID_ADDRESS, "100%", "1y"]

        # Simulate an old built will frozen at 2y: reload keeps its (stale)
        # anchor, which would reject the anticipated "1y" delivery.
        c.init_class_variables()
        stale_anchor = Util.parse_locktime_string("2y") - 150 * 86400
        c.date_to_check = stale_anchor
        assert Util.parse_locktime_string("1y") < c.date_to_check

        with mock.patch.object(Heirs, "get_transactions", return_value={}) as gt:
            result = c.build_will()

        assert result == {}
        # build_will re-anchored date_to_check to the new 1y delivery...
        expected = Util.parse_locktime_string("1y") - 150 * 86400
        assert abs(c.date_to_check - expected) < 3600
        # ...and used THAT anchor as the build filter, not the stale 2y one.
        assert gt.call_args.args[-1] == c.date_to_check


# ------------------------------------------------------------------ #
# runner
# ------------------------------------------------------------------ #

def main():
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        print(f"  {name}")
        try:
            fn()
        except Exception as e:
            failures += 1
            print(f"  [FAIL] {name}: {e!r}")
    if failures:
        print(f"[FAIL] {failures} test(s) failed")
        sys.exit(1)
    print("[OK] All offline controller tests passed")


if __name__ == "__main__":
    main()
