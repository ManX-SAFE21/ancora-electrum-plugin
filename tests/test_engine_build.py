"""The engine's build brick: build, label, move the date (CHANGELOG #119).

``bal.engine.build`` holds the steps that were copied in several places:
building the will items (``BalWindow.build_will`` for the Check dialog, the
Prepare menu and the automatic rebuild; ``BalController.build_will`` for the
command line), labelling the rebuilt transactions in the History, and moving
the stored delivery date to the rebuilt will. Each caller keeps today's
behaviour through parameters; here each parameter and each caller's choice
is pinned. Three will-executors where executors matter (owner rule,
2026-10-10).

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 -m pytest tests/test_engine_build.py -q
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from test_cli_controller_offline import VALID_ADDRESS, FakeWallet, Plugin
from test_engine_context import Host

from bal.cli.controller import BalController
from bal.core.will import NoWillExecutorNotPresent, Will
from bal.core.willexecutors import Willexecutors
from bal.engine import build
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.window import BalWindow

WE = ["https://we0.test", "https://we1.test", "https://we2.test"]
MAX_FEE = 5000
DELIVERY = 1900000000


def _executors(we2_selected=True):
    """we0 not selected, we1 selected but too expensive, we2 selected and
    valid (unless ``we2_selected`` is False)."""
    def rec(url, selected, fee):
        return {"url": url, "selected": selected, "base_fee": fee,
                "address": VALID_ADDRESS, "status": 200, "info": "fake"}
    return {
        WE[0]: rec(WE[0], False, 1000),
        WE[1]: rec(WE[1], True, MAX_FEE * 10),
        WE[2]: rec(WE[2], we2_selected, 1000),
    }


@pytest.fixture
def plugin():
    """The real command-line plugin with a known maximum executor fee."""
    with Plugin() as p:
        p.MAX_WILLEXECUTOR_FEE.set(MAX_FEE)
        yield p


def _host(plugin, heirs=None):
    """A fake host with heirs dated ``DELIVERY``."""
    host = Host(plugin)
    host.heirs = MagicMock()
    host.heirs.values.return_value = [
        [VALID_ADDRESS, "100%", DELIVERY]] if heirs is None else heirs
    host.date_to_check = 1700000000
    return host


class _Tx:
    """What ``build_will`` reads from a built transaction."""

    def __init__(self, url):
        self.my_locktime = DELIVERY
        self.heirsvalue = 1
        self.description = "d"
        self.willexecutor = {"url": url}
        self.tx_fees = 20
        self.heirs = {"alice": [VALID_ADDRESS, "100%", DELIVERY]}


@pytest.fixture
def core():
    """The core calls of the build, replaced: no wallet, no real txs."""
    with patch.object(Will, "remove_stale_wallet_history") as stale, \
         patch.object(Willexecutors, "get_willexecutors",
                      return_value=_executors()) as executors, \
         patch.object(build, "WillItem",
                      side_effect=lambda rec, _id, wallet: ("item", _id, rec)) as item, \
         patch.object(Will, "update_will") as update, \
         patch.object(Will, "normalize_will") as normalize, \
         patch.object(build.context, "available_utxos", return_value=["u1"]), \
         patch.object(build, "time", SimpleNamespace(time=lambda: 1234.0)):
        yield SimpleNamespace(stale=stale, executors=executors, item=item,
                              update=update, normalize=normalize)


# ------------------------------------------------------------------ build


def test_build_with_three_executors(plugin, core):
    """we2 is selected and valid: the build goes on; get_transactions gets
    its five positional arguments, the last being ``date_to_check``."""
    host = _host(plugin)
    host.heirs.get_transactions.return_value = {"t1": _Tx(WE[2]), "t2": _Tx(WE[2])}
    out = build.build_will(host, with_creation_date=True)
    args = host.heirs.get_transactions.call_args.args
    assert len(args) == 5
    assert args[:4] == (plugin, host.wallet, host.will_settings["baltx_fees"], ["u1"])
    assert args[4] == host.date_to_check
    assert out is host.willitems
    assert host.willexecutors == _executors()
    core.stale.assert_called_once_with(host.wallet, plugin.HISTORY_LABEL.get())


def test_built_records(plugin, core):
    """Every record: status "New" (never translated), one creation time,
    copies of the executor and the heirs; then update_will and
    normalize_will on the live will."""
    host = _host(plugin)
    tx = _Tx(WE[2])
    host.heirs.get_transactions.return_value = {"t1": tx}
    build.build_will(host, with_creation_date=False)
    _kind, txid, rec = host.willitems["t1"]
    assert txid == "t1"
    assert rec["status"] == "New" and rec["time"] == 1234.0
    assert rec["willexecutor"] == tx.willexecutor
    assert rec["willexecutor"] is not tx.willexecutor
    assert rec["heirs"] == tx.heirs and rec["heirs"] is not tx.heirs
    assert rec["txchildren"] == [] and rec["baltx_fees"] == 20
    core.update.assert_called_once()
    core.normalize.assert_called_once_with(host.willitems, host.wallet)


def test_no_usable_executor_stops_before_building(plugin, core):
    """we2 deselected: we0 is not selected and we1 is too expensive, so
    NoWillExecutorNotPresent; the stale placeholders were already dropped
    and nothing was built."""
    core.executors.return_value = _executors(we2_selected=False)
    host = _host(plugin)
    with pytest.raises(NoWillExecutorNotPresent):
        build.build_will(host, with_creation_date=True)
    host.heirs.get_transactions.assert_not_called()
    core.stale.assert_called_once()


def test_own_executor_skips_the_check(plugin, core):
    """The user is their own executor: no executor needed."""
    core.executors.return_value = _executors(we2_selected=False)
    host = _host(plugin)
    host.no_willexecutor = True
    host.heirs.get_transactions.return_value = {"t1": _Tx(WE[2])}
    assert build.build_will(host, with_creation_date=True) is host.willitems


@pytest.mark.parametrize("built", [{}, None])
def test_empty_build_returns_nothing(plugin, core, built):
    """Nothing built: ``{}``, and the will is left as it was."""
    host = _host(plugin)
    old = SimpleNamespace(get_status=lambda name: False, time=None)
    host.willitems = {"old": old}
    host.heirs.get_transactions.return_value = built
    assert build.build_will(host, with_creation_date=True) == {}
    assert host.willitems == {"old": old}
    core.update.assert_not_called()


@pytest.mark.parametrize("with_creation_date", [True, False])
def test_date_to_check_re_anchored(plugin, core, with_creation_date):
    """``date_to_check`` follows the will being built (its earliest heir
    date); the old will's creation date only with ``with_creation_date``."""
    host = _host(plugin, heirs=[[VALID_ADDRESS, "50%", DELIVERY + 99],
                                [VALID_ADDRESS, "50%", DELIVERY]])
    host.heirs.get_transactions.return_value = {}
    with patch.object(Will, "get_creation_time", return_value=777), \
         patch.object(build, "resolve_date_to_check", return_value=4242) as resolve:
        build.build_will(host, with_creation_date=with_creation_date)
    resolve.assert_called_once_with(
        plugin.is_basic_mode(), host.will_settings, built_locktime=DELIVERY,
        creation_date=777 if with_creation_date else None,
    )
    assert host.date_to_check == 4242


def test_errors_are_raised_again(plugin, core):
    """A build error reaches the caller unchanged."""
    host = _host(plugin)
    error = RuntimeError("boom")
    host.heirs.get_transactions.side_effect = error
    with pytest.raises(RuntimeError) as info:
        build.build_will(host, with_creation_date=True)
    assert info.value is error


def test_each_caller_keeps_its_creation_date_choice():
    """The window passes the creation date, the command line does not."""
    with patch.object(build, "build_will", return_value="built") as fn:
        win = object.__new__(BalWindow)
        assert BalWindow.build_will(win, False, False) == "built"
        fn.assert_called_once_with(win, with_creation_date=True)
        fn.reset_mock()
        ctl = object.__new__(BalController)
        assert BalController.build_will(ctl) == "built"
        fn.assert_called_once_with(ctl, with_creation_date=False)


# ------------------------------------------------------------------ labels


class _Item:
    """A will item with only its VALID flag."""

    def __init__(self, valid):
        self.valid = valid

    def get_status(self, name):
        """True for VALID when the item is valid."""
        return name == "VALID" and self.valid


def _label_host(fail_on=None):
    """A host with one VALID and two not valid items."""
    labels = []

    def set_label(wid, text):
        if wid == fail_on:
            raise RuntimeError("cannot label")
        labels.append((wid, text))

    host = SimpleNamespace(
        willitems={"v": _Item(True), "r": _Item(False), "i": _Item(False)},
        wallet=SimpleNamespace(set_label=set_label),
    )
    return host, labels


def test_label_scopes():
    """"valid" labels the VALID items, "all" every item; English text."""
    host, labels = _label_host()
    build.label_rebuilt(host, scope="valid")
    assert labels == [("v", "Safe21 Heartbeat backup transaction")]
    labels.clear()
    build.label_rebuilt(host, scope="all")
    assert [w for w, _t in labels] == ["v", "r", "i"]


def test_label_errors():
    """With ``on_error`` the others are still labelled; without, it raises."""
    host, labels = _label_host(fail_on="r")
    seen = []
    build.label_rebuilt(host, scope="all", on_error=seen.append)
    assert [w for w, _t in labels] == ["v", "i"] and len(seen) == 1
    with pytest.raises(RuntimeError):
        build.label_rebuilt(host, scope="all")


def test_label_reads_the_wallet_only_when_needed():
    """No items: the wallet is not read (the GUI tests' windows have none)."""
    build.label_rebuilt(SimpleNamespace(willitems={}), scope="all")


def test_label_unknown_scope():
    """A wrong scope is an error, before anything is read."""
    with pytest.raises(ValueError):
        build.label_rebuilt(SimpleNamespace(), scope="some")


# ---------------------------------------------------------- delivery date


def _date_host():
    """A host with a stored absolute date."""
    return SimpleNamespace(willitems={}, will_settings={"locktime": DELIVERY})


def test_date_moves_through_the_callers_store():
    """An earlier rebuilt date goes through ``store("locktime", new)``."""
    host, stored = _date_host(), []
    with patch.object(Will, "anticipated_delivery_to_store",
                      return_value=DELIVERY - 86400) as rule:
        new = build.sync_delivery_date(host, lambda f, v: stored.append((f, v)))
    rule.assert_called_once_with(host.willitems, DELIVERY)
    assert new == DELIVERY - 86400 and stored == [("locktime", DELIVERY - 86400)]


def test_date_left_alone_when_the_rule_says_so():
    """No earlier date (relative, equal or later): nothing stored."""
    host, stored = _date_host(), []
    with patch.object(Will, "anticipated_delivery_to_store", return_value=None):
        assert build.sync_delivery_date(host, lambda f, v: stored.append(v)) is None
    assert stored == []


def test_date_rule_errors():
    """With ``on_error``: logged by the caller, nothing stored; without, raised."""
    host, stored, seen = _date_host(), [], []
    with patch.object(Will, "anticipated_delivery_to_store",
                      side_effect=RuntimeError("rule")):
        assert build.sync_delivery_date(
            host, lambda f, v: stored.append(v), on_error=seen.append) is None
        with pytest.raises(RuntimeError):
            build.sync_delivery_date(host, lambda f, v: stored.append(v))
    assert stored == [] and len(seen) == 1


# ------------------------------------------------------------ the callers


def test_dialog_store_sets_the_flag_first():
    """The dialog remembers the anticipated date before it refreshes the
    fields (the sign prompt explains it), as before #119."""
    order = []
    window = SimpleNamespace(
        willitems={}, will_settings={"locktime": DELIVERY},
        update_setting_widgets=lambda v, f, update_all: order.append(
            ("widgets", v, f, update_all)),
    )
    dialog = SimpleNamespace(bal_window=window, _date_was_anticipated=False)
    with patch.object(Will, "anticipated_delivery_to_store",
                      return_value=DELIVERY - 86400):
        BalBuildWillDialog._sync_locktime_to_built_txs(dialog)
    assert dialog._date_was_anticipated is True
    assert order == [("widgets", DELIVERY - 86400, "locktime", True)]


def test_auto_rebuild_store_marks_the_date_after_saving():
    """The automatic rebuild stores the setting (no widgets: worker
    thread) and marks the date for the GUI-thread refresh."""
    saved = []
    win = object.__new__(BalWindow)
    win.willitems = {}
    win.will_settings = {"locktime": DELIVERY}
    win.bal_plugin = SimpleNamespace(
        WILL_SETTINGS=SimpleNamespace(set=lambda s: saved.append(dict(s))))
    win.update_setting_widgets = MagicMock()
    with patch.object(Will, "anticipated_delivery_to_store",
                      return_value=DELIVERY - 86400):
        BalWindow._sync_delivery_after_auto_rebuild(win)
    assert saved == [{"locktime": DELIVERY - 86400}]
    assert win._auto_rebuild_synced_locktime == DELIVERY - 86400
    win.update_setting_widgets.assert_not_called()


def test_auto_rebuild_rule_error_is_only_logged():
    """A rule error in the automatic rebuild stores nothing and is not
    raised (it runs on a worker thread)."""
    win = object.__new__(BalWindow)
    win.willitems = {}
    win.will_settings = {"locktime": DELIVERY}
    with patch.object(Will, "anticipated_delivery_to_store",
                      side_effect=RuntimeError("rule")):
        BalWindow._sync_delivery_after_auto_rebuild(win)
    assert not hasattr(win, "_auto_rebuild_synced_locktime")


def test_command_line_labels_then_syncs_even_after_a_label_error(plugin):
    """The command line logs a label it cannot write and still moves the
    date, through its own store hook."""
    wallet = FakeWallet()
    c = BalController(plugin, wallet)
    c.willitems = {"v": _Item(True)}
    wallet.set_label = MagicMock(side_effect=RuntimeError("cannot label"))
    with patch.object(Will, "anticipated_delivery_to_store",
                      return_value=DELIVERY - 86400):
        c._after_rebuild()
    wallet.set_label.assert_called_once_with("v", build.BACKUP_LABEL)
    assert c.will_settings["locktime"] == DELIVERY - 86400
    assert plugin.WILL_SETTINGS.get()["locktime"] == DELIVERY - 86400


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
