"""
Tests for #153: one shared broadcast of the signed invalidation.

Covers:
  - ``bal.engine.invalidate.broadcast_invalidation`` keeps the steps of the
    two old copies, in their order: wallet information, network
    information, the History label (``INVALIDATION_LABEL``) BEFORE the
    broadcast, the broadcast with a 120 s timeout; it returns the txid.
  - A transaction without a txid is broadcast unlabelled; a broadcast
    error propagates (the label is already set).
  - The Check's "Invalidate now" (``loop_broadcast_invalidating``) sends
    through it and shows "Ok", or the broadcast error, in its row.
  - The automatic invalidation sends through it.
  - No module outside ``bal/engine/invalidate.py`` broadcasts a
    transaction or pulls its network information by itself any more.

No wallet, no network, no will-executor is contacted.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_engine_invalidate_broadcast.py
"""

import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from electrum.network import BestEffortRequestFailed

import bal.gui.qt.dialogs as dialogs_mod
import bal.gui.qt.window as window_mod
from bal.core.will_versions import INVALIDATION_LABEL
from bal.engine.invalidate import BROADCAST_TIMEOUT, broadcast_invalidation
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.window import BalWindow

ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
TXID = "cd" * 32


def _parts(txid=TXID):
    """A wallet, a network and a tx that record every call in one list."""
    calls = []
    tx = MagicMock()
    tx.txid.return_value = txid
    tx.add_info_from_wallet.side_effect = lambda w: calls.append("wallet_info")
    tx.add_info_from_network.side_effect = (
        lambda n: calls.append("network_info") or "network_coro"
    )
    wallet = MagicMock()
    wallet.set_label.side_effect = lambda *a: calls.append(("label",) + a)
    network = MagicMock()
    network.broadcast_transaction.side_effect = (
        lambda t, timeout: calls.append(("broadcast", timeout)) or "send_coro"
    )
    network.run_from_another_thread.side_effect = (
        lambda coro, **kw: calls.append(("run", coro, kw.get("timeout")))
    )
    return calls, wallet, network, tx


def test_steps_in_the_old_order():
    """Information, label, then the broadcast with a 120 s timeout."""
    calls, wallet, network, tx = _parts()
    assert broadcast_invalidation(wallet, network, tx) == TXID
    assert BROADCAST_TIMEOUT == 120
    assert calls == [
        "wallet_info",
        "network_info",
        ("run", "network_coro", None),
        ("label", TXID, INVALIDATION_LABEL),
        ("broadcast", 120),
        ("run", "send_coro", 120),
    ]
    tx.add_info_from_wallet.assert_called_once_with(wallet)
    tx.add_info_from_network.assert_called_once_with(network)


def test_no_txid_no_label():
    """A tx without a txid is still broadcast, without a label."""
    calls, wallet, network, tx = _parts(txid=None)
    assert broadcast_invalidation(wallet, network, tx) is None
    wallet.set_label.assert_not_called()
    assert ("broadcast", 120) in calls


def test_broadcast_error_propagates_after_the_label():
    """The caller sees the error; the label was written before."""
    calls, wallet, network, tx = _parts()
    network.broadcast_transaction.side_effect = None

    def run(coro, **kw):
        """The send fails; the information step succeeds."""
        if kw.get("timeout") == 120:
            raise BestEffortRequestFailed("down")

    network.run_from_another_thread.side_effect = run
    try:
        broadcast_invalidation(wallet, network, tx)
    except BestEffortRequestFailed:
        pass
    else:
        raise AssertionError("the error was swallowed")
    wallet.set_label.assert_called_once_with(TXID, INVALIDATION_LABEL)


def _dialog():
    """A Check dialog with only what loop_broadcast_invalidating reads."""
    # A QDialog cannot be created empty: a plain object with the dialog's
    # own message helpers stands in for it.
    dlg = types.SimpleNamespace(
        COLOR_OK=BalBuildWillDialog.COLOR_OK,
        COLOR_ERROR=BalBuildWillDialog.COLOR_ERROR,
    )
    dlg.msg_ok = types.MethodType(BalBuildWillDialog.msg_ok, dlg)
    dlg.msg_error = types.MethodType(BalBuildWillDialog.msg_error, dlg)
    dlg._stopping = False
    dlg.bal_window = MagicMock()
    dlg.network = MagicMock()
    dlg.rows = []
    dlg.msg_set_invalidating = lambda status=None, row=None: dlg.rows.append(status)
    return dlg


def test_check_sends_through_the_engine():
    """"Invalidate now": the shared broadcast, then "Ok" in the row."""
    dlg = _dialog()
    tx = MagicMock()
    with patch.object(dialogs_mod, "broadcast_invalidation", return_value=TXID) as b:
        BalBuildWillDialog.loop_broadcast_invalidating(dlg, tx)
    b.assert_called_once_with(dlg.bal_window.wallet, dlg.network, tx)
    assert dlg.rows[0] == "Broadcasting"
    assert dlg.rows[-1] == dlg.msg_ok()


def test_check_shows_the_broadcast_error():
    """A failed broadcast is shown in the row, as before."""
    dlg = _dialog()
    with patch.object(
        dialogs_mod,
        "broadcast_invalidation",
        side_effect=BestEffortRequestFailed("down"),
    ):
        BalBuildWillDialog.loop_broadcast_invalidating(dlg, MagicMock())
    assert "down" in dlg.rows[-1]
    assert dlg.rows[-1] != dlg.msg_ok()


def test_check_closing_sends_nothing():
    """A closing dialog does not broadcast."""
    dlg = _dialog()
    dlg._stopping = True
    with patch.object(dialogs_mod, "broadcast_invalidation") as b:
        BalBuildWillDialog.loop_broadcast_invalidating(dlg, MagicMock())
    b.assert_not_called()
    assert dlg.rows == []


def test_automatic_invalidation_sends_through_the_engine():
    """The automatic invalidation signs, then uses the shared broadcast."""
    win = object.__new__(BalWindow)
    tx = MagicMock()
    tx.is_complete.return_value = True
    win.wallet = MagicMock()
    win.wallet.has_keystore_encryption.return_value = False
    win.wallet.sign_transaction.return_value = tx
    with patch.object(window_mod, "build_invalidation", return_value=tx), \
         patch.object(window_mod, "broadcast_invalidation", return_value=TXID) as b:
        assert BalWindow._auto_invalidate_will(win) is tx
    b.assert_called_once_with(win.wallet, win.wallet.network, tx)


def _code_lines(path):
    """The lines of *path* without comments."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.split("#", 1)[0]
            if stripped.strip():
                yield stripped


def test_no_other_copy_of_the_broadcast():
    """Only the engine brick broadcasts or pulls network information."""
    call = re.compile(r"\.(broadcast_transaction|add_info_from_network)\s*\(")
    found = set()
    for folder, _dirs, files in os.walk(ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            for line in _code_lines(path):
                if call.search(line):
                    found.add(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    assert found == {"engine/invalidate.py"}, found


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
