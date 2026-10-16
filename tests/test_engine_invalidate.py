"""
Tests for #152: one shared build of the invalidation transaction.

Covers:
  - ``bal.engine.invalidate.build_invalidation`` passes the same values as
    the six old copies: the will's fee rate (default 1), the History label
    setting and the earliest locktime of the will with ``date_to_check`` as
    the fallback (``None`` when the host has none).
  - ``will=None`` means the host's own will; a given will is used instead.
  - The Will tab's "Invalidate will", the automatic invalidation and the
    command line build through it, and they label the transaction with
    ``INVALIDATION_LABEL``.
  - No module outside ``bal/engine/invalidate.py`` calls
    ``Will.invalidate_will`` or writes the label text by hand any more, so a
    new copy cannot appear unnoticed.

No wallet, no network, no will-executor is contacted.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_engine_invalidate.py
"""

import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

import bal.cli.controller as controller_mod
import bal.gui.qt.window as window_mod
from bal.cli.controller import BalController
from bal.core.will import Will
from bal.core.will_versions import INVALIDATION_LABEL
from bal.engine import invalidate
from bal.engine.invalidate import build_invalidation
from bal.gui.qt.window import BalWindow

ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "bal")


class _Setting:
    """A plugin setting with a fixed value."""

    def __init__(self, value):
        self.value = value

    def get(self):
        """The fixed value."""
        return self.value


def _host(**extra):
    """A host with its own will, a fee rate and a History label setting."""
    host = types.SimpleNamespace(
        wallet=object(),
        willitems={"own": object()},
        will_settings={"baltx_fees": 7},
        bal_plugin=types.SimpleNamespace(HISTORY_LABEL=_Setting(True)),
        date_to_check=1_700_000_000,
    )
    for key, value in extra.items():
        setattr(host, key, value)
    return host


def _build(host, will=None):
    """Run the shared build with the core calls recorded, not run."""
    with patch.object(Will, "invalidate_will", return_value="TX") as inv, \
         patch.object(Will, "get_min_locktime", return_value=123) as minlt:
        result = build_invalidation(host, will)
    return result, inv, minlt


def test_same_values_as_the_old_copies():
    """Fee rate, label setting, wallet and earliest locktime are passed."""
    host = _host()
    result, inv, minlt = _build(host)
    assert result == "TX"
    minlt.assert_called_once_with(
        host.willitems, default_value=1_700_000_000
    )
    inv.assert_called_once_with(
        host.willitems, host.wallet, 7, history_label=True, will_locktime=123
    )


def test_default_fee_rate_and_no_reference_date():
    """No stored fee rate: 1 sat/byte; no date_to_check yet: None."""
    host = _host(will_settings={})
    del host.date_to_check
    _, inv, minlt = _build(host)
    assert inv.call_args.args[2] == 1
    assert minlt.call_args.kwargs == {"default_value": None}


def test_given_will_is_used():
    """An imported will (Will Details window) replaces the host's own."""
    host = _host()
    other = {"imported": object()}
    _, inv, minlt = _build(host, other)
    assert inv.call_args.args[0] is other
    assert minlt.call_args.args[0] is other


def test_nothing_to_invalidate_and_errors_propagate():
    """None comes back as it is; an error of the core is not swallowed."""
    host = _host()
    with patch.object(Will, "invalidate_will", return_value=None), \
         patch.object(Will, "get_min_locktime", return_value=1):
        assert build_invalidation(host) is None
    with patch.object(Will, "invalidate_will", side_effect=ValueError("x")), \
         patch.object(Will, "get_min_locktime", return_value=1):
        try:
            build_invalidation(host)
        except ValueError:
            pass
        else:
            raise AssertionError("the error was swallowed")


def _tx(txid="ab" * 32):
    """An unsigned transaction stand-in with a txid."""
    tx = MagicMock()
    tx.txid.return_value = txid
    return tx


def test_cli_builds_through_the_engine_and_labels():
    """``bal_will_invalidate`` uses the shared build and the label."""
    ctl = object.__new__(BalController)
    ctl._refuse_when_held = MagicMock()
    ctl.wallet = MagicMock()
    will = {"x": object()}
    tx = _tx()
    with patch.object(controller_mod, "build_invalidation", return_value=tx) as b:
        assert BalController.invalidate_will(ctl, will) is tx
    b.assert_called_once_with(ctl, will)
    ctl._refuse_when_held.assert_called_once_with()
    ctl.wallet.set_label.assert_called_once_with(tx.txid(), INVALIDATION_LABEL)


def test_will_tab_builds_through_the_engine():
    """The Will tab's task is the shared build, run on the worker thread."""
    win = object.__new__(BalWindow)
    win.date_to_check = 1
    captured = {}

    class _Waiting:
        """Records the task instead of running it in a dialog."""

        def __init__(self, parent, msg, task, on_success, on_failure, exe):
            captured["task"] = task
            captured["on_success"] = on_success

        def exe(self):
            """Nothing: the test runs the task itself."""

    will = {"x": object()}
    tx = _tx()
    with patch.object(window_mod, "_stopped_by_hold", return_value=False), \
         patch.object(window_mod, "BalWaitingDialog", _Waiting), \
         patch.object(window_mod, "build_invalidation", return_value=tx) as b:
        BalWindow.invalidate_will(win, will)
        assert captured["task"]() is tx
    b.assert_called_once_with(win, will)
    win.wallet = MagicMock()
    win.show_message = MagicMock()
    win.show_transaction = MagicMock()
    captured["on_success"](tx)
    win.wallet.set_label.assert_called_once_with(tx.txid(), INVALIDATION_LABEL)


def test_automatic_invalidation_builds_through_the_engine():
    """The automatic invalidation builds the same way; an encrypted wallet
    stops before signing."""
    win = object.__new__(BalWindow)
    win.wallet = MagicMock()
    win.wallet.has_keystore_encryption.return_value = True
    with patch.object(window_mod, "build_invalidation", return_value=_tx()) as b:
        assert BalWindow._auto_invalidate_will(win) is None
    b.assert_called_once_with(win, None)
    win.wallet.sign_transaction.assert_not_called()


def test_automatic_invalidation_labels_before_broadcast():
    """A passwordless wallet: signed, labelled with the constant, sent."""
    win = object.__new__(BalWindow)
    tx = _tx()
    tx.is_complete.return_value = True
    order = []
    win.wallet = MagicMock()
    win.wallet.has_keystore_encryption.return_value = False
    win.wallet.sign_transaction.return_value = tx
    win.wallet.set_label.side_effect = lambda *a: order.append(("label", a))
    network = MagicMock()
    network.run_from_another_thread.side_effect = (
        lambda *a, **k: order.append(("net", None))
    )
    network.broadcast_transaction.return_value = None
    win.wallet.network = network
    with patch.object(window_mod, "build_invalidation", return_value=tx):
        assert BalWindow._auto_invalidate_will(win) is tx
    assert ("label", (tx.txid(), INVALIDATION_LABEL)) in order
    assert order.index(("label", (tx.txid(), INVALIDATION_LABEL))) < len(order) - 1


def _python_files():
    """Every .py file of the plugin package."""
    for folder, _dirs, files in os.walk(ROOT):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(folder, name)


def _code_lines(path):
    """The lines of *path* without comments (docstrings may name the call)."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.split("#", 1)[0]
            if stripped.strip():
                yield stripped


def test_no_other_copy_of_the_build():
    """Only the engine brick calls Will.invalidate_will."""
    call = re.compile(r"\bWill\.invalidate_will\s*[(,]")
    found = []
    for path in _python_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        for line in _code_lines(path):
            if call.search(line):
                found.append(rel)
    assert sorted(set(found)) == ["engine/invalidate.py"], found


def test_label_text_written_once():
    """The label text appears in code only where the constant is defined."""
    found = []
    for path in _python_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        for line in _code_lines(path):
            if '"Safe21 Heartbeat invalidation"' in line:
                found.append(rel)
    assert found == ["core/will_versions.py"], found
    assert invalidate.INVALIDATION_LABEL == "Safe21 Heartbeat invalidation"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
