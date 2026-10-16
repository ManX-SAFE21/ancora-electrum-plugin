"""
Tests for #154: one shared signing of the will transactions.

Covers:
  - ``bal.engine.sign.sign_targets``: every valid transaction, or the valid
    ones among the txids asked for, in their order.
  - ``bal.engine.sign.sign_will_item``: a COMPLETE item is not signed; an
    input that spends another transaction of the same will gets that
    output's value, descriptor, address and script; the wallet signs with
    the password (warnings ignored); COMPLETE and the signature count are
    set; the stored tx is not replaced; a wallet error propagates.
  - The GUI (``BalWindow._prepare_and_sign_tx`` and ``sign_transactions``)
    and the command line (``BalController.sign_transactions``) sign through
    it; the command line still stores the signed tx, checks the signatures
    and saves.
  - No module outside ``bal/engine/sign.py`` prepares the inputs of a will
    transaction, and the only other wallet signings are the two
    invalidation ones.

No wallet, no network, no will-executor is contacted.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_engine_sign.py
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
from bal.engine.sign import SignResult, sign_targets, sign_will_item
from bal.gui.qt.window import BalWindow

ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "bal")


class _Item:
    """A will item with statuses and a stored tx (its serialized text)."""

    def __init__(self, txt, valid=True, complete=False, outputs=()):
        self.tx = types.SimpleNamespace(text=txt, outputs=lambda: list(outputs))
        self.status = {"VALID": valid, "COMPLETE": complete}

    def get_status(self, name):
        """The stored status *name*."""
        return self.status.get(name, False)

    def set_status(self, name, value):
        """Store status *name*."""
        self.status[name] = value


def _input(parent_txid, index):
    """A transaction input spending ``parent_txid:index``."""
    txin = types.SimpleNamespace()
    txin.prevout = types.SimpleNamespace(to_json=lambda: [parent_txid, index])
    return txin


def _tx(inputs, complete=True, counts=(1, 1)):
    """A parsed transaction with *inputs*."""
    tx = MagicMock()
    tx.inputs.return_value = inputs
    tx.is_complete.return_value = complete
    if counts is None:
        tx.signature_count.side_effect = ValueError("no count")
    else:
        tx.signature_count.return_value = counts
    return tx


def _output(value):
    """A change output of a will transaction."""
    return types.SimpleNamespace(
        value=value,
        script_descriptor="desc",
        address="bc1qchange",
        scriptpubkey=b"\x00\x14spk",
    )


def test_targets_all_valid():
    """No txids: Will.only_valid decides."""
    items = {"a": _Item("a"), "b": _Item("b", valid=False)}
    with patch.object(Will, "only_valid", return_value=["a"]) as only:
        assert sign_targets(items) == ["a"]
    only.assert_called_once_with(items)


def test_targets_asked_txids_valid_only_in_order():
    """Unknown and invalid txids are skipped; the order is kept."""
    items = {"a": _Item("a"), "b": _Item("b", valid=False), "c": _Item("c")}
    assert sign_targets(items, ["c", "x", "b", "a"]) == ["c", "a"]
    assert sign_targets(items, []) == []


def test_complete_item_is_not_signed():
    """A COMPLETE item: a fresh copy, nothing signed, nothing changed."""
    items = {"a": _Item("a", complete=True)}
    wallet = MagicMock()
    parsed = _tx([])
    with patch.object(Will, "get_tx_from_any", return_value=parsed):
        tx, newly = sign_will_item(wallet, items, "a", "pw")
    assert (tx, newly) == (parsed, False)
    wallet.sign_transaction.assert_not_called()


def test_inputs_from_the_will_are_prepared_and_signed():
    """The change input gets the parent output's data; the wallet signs."""
    parent = _Item("p", outputs=[_output(1000), _output(2500)])
    child = _Item("c")
    items = {"p": parent, "c": child}
    own = _input("p", 1)
    outside = _input("f" * 64, 0)
    parsed = _tx([own, outside], complete=True, counts=(2, 3))
    wallet = MagicMock()
    with patch.object(Will, "get_tx_from_any", return_value=parsed):
        tx, newly = sign_will_item(wallet, items, "c", "pw")
    assert (tx, newly) == (parsed, True)
    assert own._trusted_value_sats == 2500
    assert own._TxInput__value_sats == 2500
    assert own.script_descriptor == "desc"
    assert own._TxInput__address == "bc1qchange"
    assert own._TxInput__scriptpubkey == b"\x00\x14spk"
    assert own.is_mine is True
    assert not hasattr(outside, "_trusted_value_sats")
    wallet.sign_transaction.assert_called_once_with(
        parsed, "pw", ignore_warnings=True
    )
    assert child.get_status("COMPLETE") is True
    assert (child.sigs_have, child.sigs_required) == (2, 3)
    # #157: the signed copy (re-parsed) is stored in the item at once.
    assert child.tx is parsed


def test_incomplete_and_no_count():
    """Not complete: no COMPLETE status; a failing count is only logged."""
    items = {"a": _Item("a")}
    with patch.object(Will, "get_tx_from_any",
                      return_value=_tx([], complete=False, counts=None)):
        _, newly = sign_will_item(MagicMock(), items, "a", None)
    assert newly is True
    assert items["a"].get_status("COMPLETE") is False
    assert not hasattr(items["a"], "sigs_have")


def test_wallet_error_propagates():
    """A wrong password reaches the caller."""
    items = {"a": _Item("a")}
    wallet = MagicMock()
    wallet.sign_transaction.side_effect = ValueError("wrong password")
    with patch.object(Will, "get_tx_from_any", return_value=_tx([])):
        try:
            sign_will_item(wallet, items, "a", "bad")
        except ValueError:
            pass
        else:
            raise AssertionError("the error was swallowed")
    assert items["a"].tx.text == "a"  # #157: nothing stored on an error
    assert items["a"].get_status("COMPLETE") is False


def test_gui_signs_through_the_engine():
    """``_prepare_and_sign_tx`` is the engine's function with the wallet."""
    win = object.__new__(BalWindow)
    win.wallet = MagicMock()
    items = {"a": _Item("a")}
    with patch.object(window_mod, "sign_will_item",
                      return_value=("TX", True)) as s:
        assert BalWindow._prepare_and_sign_tx(win, items, "a", "pw") == ("TX", True)
    s.assert_called_once_with(win.wallet, items, "a", "pw")


def test_gui_batch_uses_the_shared_sign_will():
    """The Will tab's "Sign" is the engine's ``sign_will`` (#157): chosen
    txids without the deselected rule; "sign all" of the live will with the
    selected will-executors."""
    win = object.__new__(BalWindow)
    win.wallet = MagicMock()
    win.willitems = {"a": _Item("a"), "b": _Item("b", valid=False)}
    win.waiting_dialog = MagicMock()
    win.selected_willexecutors = MagicMock(return_value={"https://a"})
    with patch.object(window_mod, "sign_will", return_value="RESULT") as sw:
        assert BalWindow.sign_transactions(win, "pw", txids=["a", "b"]) == "RESULT"
        assert sw.call_args.args == (win.wallet, win.willitems, "pw", ["a", "b"])
        assert sw.call_args.kwargs["selected"] is None
        BalWindow.sign_transactions(win, "pw")
        assert sw.call_args.kwargs["selected"] == {"https://a"}
        other = {"x": _Item("x")}
        BalWindow.sign_transactions(win, "pw", will=other)
        assert sw.call_args.args[1] is other
        assert sw.call_args.kwargs["selected"] is None


def _controller(items):
    """A command-line controller with the will *items*."""
    ctl = object.__new__(BalController)
    ctl._refuse_when_held = MagicMock()
    ctl.wallet = MagicMock()
    ctl.wallet.has_keystore_encryption.return_value = False
    ctl.save_willitems = MagicMock()
    ctl._save_to_history = MagicMock()
    ctl.willitems = items
    return ctl


def _result(txs=None, error=None):
    """A ``SignResult`` with *txs* and *error*."""
    r = SignResult()
    r.txs = dict(txs or {})
    r.error = error
    return r


def test_cli_signs_through_the_engine_and_saves():
    """The command line is the engine's ``sign_will`` (#157); it saves the
    will and the History; "sign all" passes the selected will-executors."""
    items = {"new": _Item("new")}
    ctl = _controller(items)
    ctl.willexecutors = {"https://a": {"selected": True},
                         "https://b": {"selected": False}}
    with patch.object(controller_mod, "sign_will",
                      return_value=_result({"new": "SIGNED"})) as sw:
        out = BalController.sign_transactions(ctl, "pw", txids=None)
        assert sw.call_args.kwargs["selected"] == {"https://a"}
        BalController.sign_transactions(ctl, "pw", txids=["new"])
        assert sw.call_args.kwargs["selected"] is None
    assert out == {"new": "SIGNED"}
    assert sw.call_args.args == (ctl.wallet, items, "pw", ["new"])
    assert ctl.save_willitems.call_count == 2
    assert ctl._save_to_history.call_count == 2


def test_cli_error_saves_then_raises():
    """A signing that fails: the ones signed before are saved, then the
    error reaches the command."""
    ctl = _controller({"a": _Item("a")})
    ctl.willexecutors = {}
    with patch.object(controller_mod, "sign_will",
                      return_value=_result({"a": "T"}, ValueError("bad"))):
        try:
            BalController.sign_transactions(ctl, "pw")
        except ValueError:
            pass
        else:
            raise AssertionError("the error was swallowed")
    ctl.save_willitems.assert_called_once_with()
    ctl._save_to_history.assert_called_once_with()


def _code_lines(path):
    """The lines of *path* without comments."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.split("#", 1)[0]
            if stripped.strip():
                yield stripped


def _files_matching(pattern):
    """The plugin files (relative to ``bal/``) with a code line matching."""
    found = []
    for folder, _dirs, files in os.walk(ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for line in _code_lines(path):
                if re.search(pattern, line):
                    found.append(rel)
    return sorted(found)


def test_no_other_copy_of_the_signing():
    """No GUI or command-line module prepares the inputs of a will
    transaction any more; the other signings are the two invalidation ones
    (the Check's and the automatic one).

    ``bal/core`` still fills inputs the same way where the will is built
    and loaded (``heirs.py``, ``Will.add_info_from_will``,
    ``Will.new_input``): not a signing, left for a later step.
    """
    preparing = [
        f for f in _files_matching(r"_TxInput__address")
        if not f.startswith("core/")
    ]
    assert preparing == ["engine/sign.py"]
    assert _files_matching(r"\.sign_transaction\s*\(") == [
        "engine/sign.py",
        "gui/qt/dialogs.py",
        "gui/qt/window.py",
    ]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
