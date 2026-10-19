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
from bal.engine import context as engine_context

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

        assert c.willexecutors_delete([renamed]) == {"deleted": [renamed], "not_found": []}
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


def test_auto_rebuild_threshold_passed_does_not_invalidate():
    """CHANGELOG #79: a passed Check Alive is reported, never invalidated on
    its own (it used to return an invalidation, ``threshold_passed``)."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        c.heirs_add("alice", VALID_ADDRESS, "100000")
        plugin.USER_TYPE.set("advanced")
        # Two days in the past, not one hour (CHANGELOG #113): #80 turns the
        # Check Alive into whole days before the delivery, rounded, and in
        # the afternoon (UTC) "one hour ago" was rounded to tomorrow, which
        # is not past any more.
        c.will_settings["threshold"] = int(time.time()) - 2 * 86400
        result = c.auto_rebuild()
        assert result["result"] == "check_alive_passed", result
        assert "invalidation_tx" not in result
        assert "Check Alive" in result["message"]


def test_build_will_reanchors_date_to_check_to_new_locktime():
    """CLI mirror of the GUI regression: ``build_will`` must re-anchor
    ``date_to_check`` to the CURRENT heirs' earliest delivery before building,
    so an anticipated (shortened) rebuild is not blocked by the old built-will
    anchor (which would yield NO_FUTURE_DATE in ``get_transactions``).

    CHANGELOG #123: the settings are the wallet's own, written through the
    controller's validated store, and the delivery is always a DATE (a
    period such as "2y" is refused). Before #123 they were seeded into
    Electrum's global ``bal_will_settings`` (never written now) with the
    delivery "2y". With a date the Check Alive is anchored to the stored
    date (``resolve_delivery_date``), so the shortened delivery is stored
    as the date the heirs' "1y" gives today: the old will is still frozen
    at 2y and its stale anchor must not be the build filter.
    """
    with Plugin() as plugin:
        plugin.USER_TYPE.set("advanced")
        plugin.NO_WILLEXECUTOR.set(True)
        c = _make_controller(plugin)
        c.store_will_setting("threshold", "-150d")
        # A new ADVANCED wallet starts in the period form (#146), which
        # dates the will today + 24 months at every flow; the fixed form
        # keeps the 1y date. Before #168 BAL's multiverse option kept the
        # heir's own "1y" and hid that (D7: every heir gets the will's date).
        engine_context.set_delivery(
            c, form="fixed", date=Util.parse_locktime_string("1y"))
        c.store_will_setting("baltx_fees", 20)
        assert c.will_settings is c.wallet.db.get_dict("will_settings")
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
        assert plugin.config.get("bal_will_settings") is None


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


def test_heirs_same_address_refused():
    """#135 (owner): no two heirs on one address. The command line refuses
    it on add and update (the heir's own address stays allowed; a bech32
    address in another case is the same address)."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        c.heirs_add("alice", VALID_ADDRESS, "100000")
        for call in (lambda: c.heirs_add("bob", VALID_ADDRESS.upper(), "100000"),
                     lambda: c.heirs_add("bob", " " + VALID_ADDRESS, "100000")):
            try:
                call()
                raise AssertionError("expected UserFacingException")
            except UserFacingException as e:
                assert str(e) == "This address is already used by the heir alice."
        c.heirs_update("alice", address=VALID_ADDRESS, amount="200000")
        assert list(c.heirs) == ["alice"]
