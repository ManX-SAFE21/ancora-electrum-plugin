"""The engine's state bricks and their hooks (CHANGELOG #117).

``bal.engine.context`` holds the load / save / check steps that were in the
command line's ``BalController`` (copied without rewriting; the controller
now calls them). They work on a "host" (``bal.engine.host.WalletHost``) and
call its three hooks where the GUI and the command line differ: store a
will setting, write the wallet file, redraw the History.

Here the functions run on a small fake host with the real plugin settings
and three will-executors (owner rule, 2026-10-10), and each hook is checked
to be called exactly when the old code did the same thing. The full flows
(prepare, sign, send...) keep running in ``test_cli_*.py`` and in the
golden files of #114.

Run:
    PYTHONPATH=electrum-src python3 -m pytest tests/test_engine_context.py -q
"""

import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from test_cli_controller_offline import VALID_ADDRESS, FakeWallet, Plugin

from bal.cli.controller import BalController
from bal.core.plugin_base import BalPlugin
from bal.core.util import Util
from bal.core.will import NoHeirsException, Will
from bal.engine import context
from bal.engine.host import WalletHost

WE = ["https://we-a.test", "https://we-b.test", "https://we-c.test"]
OLD_DATE = 1700000000


class Host:
    """A fake host: the state the engine reads and hooks that record calls."""

    def __init__(self, plugin, wallet=None):
        self.bal_plugin = plugin
        self.wallet = wallet or FakeWallet()
        self.will = {}
        self.willitems = {}
        self.heirs = {}
        self.will_settings = dict(
            plugin.WILL_SETTINGS.get() or plugin.default_will_settings()
        )
        self.willexecutors = {}
        self.date_to_check = None
        self.no_willexecutor = False
        self.calls = []

    def store_will_setting(self, field, value):
        """Hook: record it and do what both hosts do with the value."""
        self.calls.append(("store_will_setting", field, value))
        self.will_settings[field] = value

    def flush_wallet_db(self):
        """Hook: record it."""
        self.calls.append(("flush_wallet_db",))

    def after_history_saved(self):
        """Hook: record it."""
        self.calls.append(("after_history_saved",))


@pytest.fixture
def plugin():
    """The real command-line plugin, three will-executors selected."""
    with Plugin() as p:
        p.WILLEXECUTORS.set({BalPlugin.chainname: {
            url: {"url": url, "selected": True, "base_fee": 1000,
                  "address": VALID_ADDRESS, "status": 200, "info": "fake"}
            for url in WE
        }})
        yield p


class _Item:
    """A will item with what ``save_willitems`` reads."""

    def __init__(self, tx="raw-tx", extra=None):
        self.tx = tx
        self.extra = extra

    def to_dict(self):
        """The stored form (the tx object is serialized by the engine)."""
        d = {"tx": self.tx, "status": "New"}
        if self.extra is not None:
            d["extra"] = self.extra
        return d


# ------------------------------------------------------------ load / save


def test_load_willitems_wraps_every_stored_entry(plugin):
    """One WillItem per stored entry, built with the host's wallet."""
    host = Host(plugin)
    host.will = {"t1": {"tx": "a"}, "t2": {"tx": "b"}}
    with mock.patch.object(context, "WillItem",
                           side_effect=lambda w, wallet: (w["tx"], wallet)):
        context.load_willitems(host)
    assert host.willitems == {"t1": ("a", host.wallet), "t2": ("b", host.wallet)}
    assert host.calls == []


def test_save_willitems_replaces_the_stored_will_then_flushes(plugin):
    """Old entries go, the items are stored with the tx as text, and the
    wallet file is written once, at the end."""
    host = Host(plugin)
    host.will = {"old": {"tx": "x"}}
    host.willitems = {"t1": _Item(tx=123)}
    context.save_willitems(host)
    assert host.will == {"t1": {"tx": "123", "status": "New"}}
    assert host.calls == [("flush_wallet_db",)]


def test_save_willitems_refuses_what_cannot_be_stored(plugin):
    """A value JSON cannot hold stops the save before the file is written."""
    host = Host(plugin)
    host.willitems = {"t1": _Item(extra=object())}
    with pytest.raises(TypeError):
        context.save_willitems(host)
    assert host.calls == []


# --------------------------------------------------------------- history


def test_history_off_saves_nothing(plugin):
    """SAVE_HISTORY off: nothing saved, no redraw."""
    plugin.SAVE_HISTORY.set(False)
    host = Host(plugin)
    with mock.patch.object(Will, "save_valid_transactions_to_history") as save:
        context.save_to_history(host)
    save.assert_not_called()
    assert host.calls == []


def test_history_on_saves_then_redraws(plugin):
    """SAVE_HISTORY on: saved with the label, then the redraw hook."""
    plugin.SAVE_HISTORY.set(True)
    host = Host(plugin)
    with mock.patch.object(Will, "save_valid_transactions_to_history") as save:
        context.save_to_history(host)
    save.assert_called_once_with(
        host.willitems, host.wallet, plugin.HISTORY_LABEL.get()
    )
    assert host.calls == [("after_history_saved",)]


def test_history_failure_never_raises(plugin):
    """A failed save is logged; the redraw still happens (GUI behaviour)."""
    plugin.SAVE_HISTORY.set(True)
    host = Host(plugin)
    with mock.patch.object(Will, "save_valid_transactions_to_history",
                           side_effect=RuntimeError("boom")):
        context.save_to_history(host)
    assert host.calls == [("after_history_saved",)]


# ------------------------------------------------------------ Check Alive


def test_old_check_alive_is_converted_through_the_hook(plugin):
    """"-1y" becomes "-365d" and is stored through the hook (#80)."""
    host = Host(plugin)
    host.will_settings["threshold"] = "-1y"
    context.normalize_check_alive(host)
    assert host.calls == [("store_will_setting", "threshold", "-365d")]
    assert host.will_settings["threshold"] == "-365d"


def test_converted_check_alive_is_left_alone(plugin):
    """Already "-Nd": nothing stored."""
    host = Host(plugin)
    host.will_settings["threshold"] = "-30d"
    context.normalize_check_alive(host)
    assert host.calls == []


def test_check_alive_info_never_raises(plugin):
    """Broken settings: ``None``, no exception."""
    host = Host(plugin)
    host.will_settings = None
    assert context.check_alive_info(host) is None


def test_refresh_invalidated_never_raises(plugin):
    """A failure while reading the coins is only logged."""
    host = Host(plugin)
    with mock.patch.object(Util, "get_available_utxos",
                           side_effect=RuntimeError("boom")):
        context.refresh_invalidated_items(host)
    assert host.calls == []


# ------------------------------------------------------- the check's state


def test_heirs_get_the_delivery_date_then_flush(plugin):
    """Every heir gets the settings' date; the wallet file is written once."""
    host = Host(plugin)
    host.heirs = {"alice": [VALID_ADDRESS, "50%", OLD_DATE],
                  "bob": [VALID_ADDRESS, "50%", OLD_DATE]}
    context.init_heirs_to_locktime(host)
    date = host.will_settings["locktime"]
    assert host.heirs == {"alice": [VALID_ADDRESS, "50%", date],
                          "bob": [VALID_ADDRESS, "50%", date]}
    assert host.calls == [("flush_wallet_db",)]


def test_multiverse_keeps_each_heirs_date(plugin):
    """Each heir has its own date: nothing changes, nothing is written."""
    host = Host(plugin)
    host.heirs = {"alice": [VALID_ADDRESS, "50%", OLD_DATE]}
    context.init_heirs_to_locktime(host, multiverse=True)
    assert host.heirs["alice"][2] == OLD_DATE
    assert host.calls == []


def test_init_class_variables_without_heirs(plugin):
    """No heirs: NoHeirsException, before anything is changed."""
    host = Host(plugin)
    with pytest.raises(NoHeirsException, match="Heirs are not defined"):
        context.init_class_variables(host)
    assert host.calls == []


def test_init_class_variables_prepares_the_state(plugin):
    """The reference date, the three will-executors and the heirs' date."""
    host = Host(plugin)
    host.heirs = {"alice": [VALID_ADDRESS, "100%", OLD_DATE]}
    context.init_class_variables(host)
    assert host.date_to_check is not None
    assert host.no_willexecutor is False
    assert set(WE) <= set(host.willexecutors)
    assert host.heirs["alice"][2] == host.will_settings["locktime"]
    assert host.calls == [("flush_wallet_db",)]


def test_check_will_passes_the_hosts_state(plugin):
    """``Will.is_will_valid`` gets the host's will, settings, heirs and
    will-executors, and the coins the will can use."""
    host = Host(plugin)
    host.heirs = {"alice": [VALID_ADDRESS, "100%", OLD_DATE]}
    host.willexecutors = {url: {"url": url} for url in WE}
    host.date_to_check = 1800000000
    with mock.patch.object(Util, "get_available_utxos", return_value=["u1"]) as utxos, \
         mock.patch.object(Will, "is_will_valid", return_value=True) as valid:
        assert context.check_will(host) is True
    utxos.assert_called_once_with(
        host.wallet, plugin.HISTORY_LABEL.get(), 1800000000
    )
    valid.assert_called_once_with(
        host.willitems, 1800000000, host.will_settings["baltx_fees"], ["u1"],
        heirs=host.heirs, willexecutors=host.willexecutors,
        self_willexecutor=False, wallet=host.wallet,
    )


# ------------------------------------------------------------ the hosts


def test_the_controller_is_a_host(plugin):
    """``BalController`` has the state and the three hooks, and its hooks do
    what its own code did before #117.

    Since #123 the store hook writes the wallet's own settings through the
    one validated write and saves the wallet file; Electrum's global
    ``bal_will_settings`` is never written, and a value the write refuses
    (a period as delivery date) changes nothing."""
    wallet = FakeWallet()
    c = BalController(plugin, wallet)
    for name in WalletHost.__annotations__:
        assert hasattr(c, name), name
    assert c.bal_plugin is plugin
    with mock.patch.object(wallet, "save_db") as save_db:
        c.flush_wallet_db()
    save_db.assert_called_once_with()
    with mock.patch.object(wallet, "save_db") as save_db:
        c.store_will_setting("threshold", "-90d")
    save_db.assert_called_once_with()
    assert c.will_settings is wallet.db.get_dict("will_settings")
    assert c.will_settings["threshold"] == "-90d"
    before = dict(c.will_settings)
    with pytest.raises(ValueError):
        c.store_will_setting("locktime", "1054d")
    assert dict(c.will_settings) == before
    assert plugin.config.get("bal_will_settings") is None
    assert c.after_history_saved() is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
