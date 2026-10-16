"""
Tests for #155: one shared sending of the will to its will-executors.

Covers, with three fake will-executors (never the real ones):
  - ``select_push`` (D2): the selected will-executors with a valid current
    record receive the will; one over the maximum fee or with no valid
    address is excluded, with the reason, and its items are not touched.
  - ``push_will``: received -> PUSHED; refused -> PUSH_FAIL with the reason;
    no answer before the deadline, or no answer at all (D1) -> PUSH_FAIL
    and TIMEOUT; "already present" -> the copy is checked; an error while
    checking is logged, stored as "not verified" and the next check goes on
    (D3); ``should_stop`` stops before the next check; the callbacks see
    every outcome.
  - The callers: the menu reports a failure when one is only excluded; the
    command line names the reason of an exclusion; the Check writes a
    "Will-Executor excluded" warning row with the reason.
  - No module outside ``bal/engine/push.py`` sends the will by itself.

No wallet, no network, no will-executor is contacted.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_engine_push.py
"""

import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

import bal.gui.qt.dialogs as dialogs_mod
import bal.gui.qt.window as window_mod
from bal.cli.controller import BalController
from bal.core.willexecutors import Willexecutors
from bal.engine import push as push_mod
from bal.engine.push import (
    ALREADY_PRESENT,
    EXCLUDED_FEE,
    EXCLUDED_INVALID,
    FAILED,
    RECEIVED,
    TIMEOUT,
    push_will,
    select_push,
)
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.window import BalWindow

ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "bal")

WE0 = "https://we0.test"
WE1 = "https://we1.test"
WE2 = "https://we2.test"
# Valid mainnet addresses, one per will-executor (never used to send).
ADDRESSES = {
    WE0: "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
    WE1: "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
    WE2: "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
}


class _Item:
    """A will item that records its statuses and its check answer."""

    def __init__(self, url):
        self.we = {"url": url}
        self.status = {}
        self.check_answers = []

    def set_status(self, name, value=True):
        """Store status *name*."""
        self.status[name] = value

    def get_status(self, name):
        """The stored status *name*."""
        return self.status.get(name, False)

    def set_check_willexecutor(self, answer):
        """Record the check answer."""
        self.check_answers.append(answer)


def _current(fees=None, addresses=None):
    """The current records of the three will-executors."""
    fees = fees or {}
    addresses = addresses or {}
    return {
        url: {
            "url": url,
            "selected": True,
            "address": addresses.get(url, ADDRESSES[url]),
            "base_fee": fees.get(url, 15000),
        }
        for url in (WE0, WE1, WE2)
    }


def _grouped(urls):
    """``get_willexecutor_transactions``: one transaction per executor."""
    return {
        url: {"url": url, "txs": "raw\n", "txsids": ["T" + url[-6]]}
        for url in urls
    }


def test_select_keeps_the_valid_ones():
    """Three valid executors: all selected, none excluded."""
    with patch.object(Willexecutors, "get_willexecutor_transactions",
                      return_value=_grouped([WE0, WE1, WE2])) as g:
        selected, excluded = select_push(
            {}, _current(), force=True, max_fee=500000, dust=546
        )
    assert sorted(selected) == [WE0, WE1, WE2] and excluded == {}
    assert g.call_args.kwargs["force"] is True


def test_select_excludes_fee_and_invalid():
    """D2: over the maximum fee, or a bad address: excluded with the reason."""
    current = _current(fees={WE1: 600000}, addresses={WE2: "not-an-address"})
    with patch.object(Willexecutors, "get_willexecutor_transactions",
                      return_value=_grouped([WE0, WE1, WE2])):
        selected, excluded = select_push({}, current, max_fee=500000, dust=546)
    assert list(selected) == [WE0]
    assert excluded == {WE1: EXCLUDED_FEE, WE2: EXCLUDED_INVALID}


def test_select_missing_current_record_is_invalid():
    """An executor not in the current list at all cannot receive the will."""
    current = _current()
    del current[WE2]
    with patch.object(Willexecutors, "get_willexecutor_transactions",
                      return_value=_grouped([WE0, WE2])):
        selected, excluded = select_push({}, current, max_fee=500000, dust=546)
    assert list(selected) == [WE0] and excluded == {WE2: EXCLUDED_INVALID}


def _items():
    """One item per will-executor, keyed as in ``_grouped``."""
    return {"T" + url[-6]: _Item(url) for url in (WE0, WE1, WE2)}


def test_push_outcomes_and_statuses():
    """Received, refused and silent: PUSHED, PUSH_FAIL, PUSH_FAIL (D1)."""
    items = _items()
    selected = _grouped([WE0, WE1, WE2])
    seen = []

    def fake_parallel(willexecutors, *, on_each, on_timeout, on_tick=None, **kw):
        """we0 receives, we1 refuses, we2 is still silent at the deadline."""
        on_each(WE0, willexecutors[WE0], True, None)
        willexecutors[WE1]["broadcast_error"] = "refused"
        on_each(WE1, willexecutors[WE1], False, None)
        on_timeout(WE2, willexecutors[WE2])

    with patch.object(Willexecutors, "push_transactions_parallel",
                      side_effect=fake_parallel):
        result = push_will(
            items, selected,
            on_result=lambda url, rec, outcome, exc: seen.append((url, outcome)),
        )
    assert result.outcomes == {WE0: RECEIVED, WE1: FAILED, WE2: TIMEOUT}
    assert result.errors == {WE1: "refused"}
    assert result.any_failed is True
    assert items["T" + WE0[-6]].status == {"PUSHED": True}
    assert items["T" + WE1[-6]].status == {"PUSH_FAIL": True}
    assert items["T" + WE2[-6]].status == {"PUSH_FAIL": True}
    assert seen == [(WE0, RECEIVED), (WE1, FAILED), (WE2, TIMEOUT)]
    assert "txs" not in selected[WE0]


def test_push_no_callback_at_all_is_a_timeout():
    """A server that reached neither callback did not get the will (D1)."""
    items = _items()
    with patch.object(Willexecutors, "push_transactions_parallel",
                      side_effect=lambda w, **kw: None):
        result = push_will(items, _grouped([WE0]))
    assert result.outcomes == {WE0: TIMEOUT}
    assert items["T" + WE0[-6]].status == {"PUSH_FAIL": True}


def test_push_error_text_from_the_exception():
    """No server message: the exception text is the reason."""
    items = _items()

    def fake_parallel(willexecutors, *, on_each, **kw):
        on_each(WE0, willexecutors[WE0], False, ConnectionError("offline"))

    with patch.object(Willexecutors, "push_transactions_parallel",
                      side_effect=fake_parallel):
        result = push_will(items, _grouped([WE0]))
    assert result.errors == {WE0: "offline"}


def _already(urls):
    """A parallel push where every executor answers "already present"."""
    def fake_parallel(willexecutors, *, on_each, **kw):
        for url in urls:
            on_each(url, willexecutors[url], False,
                    Willexecutors.AlreadyPresentException())
    return fake_parallel


def test_already_present_check_error_goes_on():
    """D3: the first check fails, it is stored as None, the others go on."""
    items = _items()
    order = []

    def check(wid, url):
        """we0's check raises; the others answer."""
        order.append(url)
        if url == WE0:
            raise ConnectionError("down")
        return {"tx": "raw"}

    with patch.object(Willexecutors, "push_transactions_parallel",
                      side_effect=_already([WE0, WE1, WE2])), \
         patch.object(Willexecutors, "check_transaction", side_effect=check):
        result = push_will(
            items, _grouped([WE0, WE1, WE2]),
            on_check_start=lambda url, wid: order.append(("start", url)),
            on_check_done=lambda url, wid, a: order.append(("done", url, a)),
        )
    assert set(result.outcomes.values()) == {ALREADY_PRESENT}
    assert result.any_failed is False
    assert items["T" + WE0[-6]].check_answers == [None]
    assert items["T" + WE1[-6]].check_answers == [{"tx": "raw"}]
    assert items["T" + WE2[-6]].check_answers == [{"tx": "raw"}]
    assert order[:3] == [("start", WE0), WE0, ("done", WE0, None)]
    assert len(result.checks) == 3


def test_should_stop_before_the_next_check():
    """A closing window stops the checks; nothing more is asked."""
    items = _items()
    with patch.object(Willexecutors, "push_transactions_parallel",
                      side_effect=_already([WE0, WE1])), \
         patch.object(Willexecutors, "check_transaction") as check:
        result = push_will(items, _grouped([WE0, WE1]), should_stop=lambda: True)
    assert result.stopped is True
    check.assert_not_called()


def test_menu_reports_an_exclusion_as_not_sent():
    """The menu's task returns True when one executor is only excluded."""
    win = object.__new__(BalWindow)
    win.willitems = _items()
    win.bal_plugin = MagicMock()
    win.bal_plugin.MAX_WILLEXECUTOR_FEE.get.return_value = 500000
    win.wallet = MagicMock()
    win.wallet.dust_threshold.return_value = 546
    win.waiting_dialog = MagicMock()
    win.waiting_dialog._stopping = False
    empty = push_mod.PushResult()
    with patch.object(window_mod, "select_push",
                      return_value=({}, {WE1: EXCLUDED_FEE})), \
         patch.object(window_mod, "push_will", return_value=empty), \
         patch.object(Willexecutors, "get_willexecutors", return_value={}):
        assert BalWindow.push_transactions_to_willexecutors(win) is True
    texts = [c.args[0] for c in win.waiting_dialog.update.call_args_list]
    assert any("excluded: fee above the maximum" in t for t in texts)


def test_cli_names_the_exclusion():
    """The command line lists an excluded executor in ``_failed``."""
    ctl = object.__new__(BalController)
    ctl._refuse_when_held = MagicMock()
    ctl.willitems = _items()
    ctl.willexecutors = _current()
    ctl.plugin = MagicMock()
    ctl.plugin.MAX_WILLEXECUTOR_FEE.get.return_value = 500000
    ctl.wallet = MagicMock()
    ctl.wallet.dust_threshold.return_value = 546
    ctl.save_willitems = MagicMock()
    result = push_mod.PushResult(outcomes={WE0: RECEIVED})
    with patch("bal.cli.controller.select_push",
               return_value=({WE0: {}}, {WE1: EXCLUDED_FEE, WE2: EXCLUDED_INVALID})), \
         patch("bal.cli.controller.push_will", return_value=result):
        out = BalController.push_transactions_to_willexecutors(ctl)
    assert out[WE0] == "Success"
    assert out["_failed"] == {
        WE1: "excluded: fee above the maximum",
        WE2: "excluded: not a valid will-executor",
    }
    ctl.save_willitems.assert_called_once_with()


def test_check_writes_the_exclusion_row():
    """The Check: a "Will-Executor excluded" warning row with the reason."""
    rows = []
    dlg = types.SimpleNamespace(
        _stopping=False,
        COLOR_WARNING="#c80",
        bal_window=MagicMock(),
        msg_set_pushing=lambda *a, **k: None,
        msg_set_status=lambda label, row, status, color=None: rows.append(
            (label, status, color)),
        _report=lambda: MagicMock(),
        _log_only=lambda *a, **k: None,
        push_status_text=lambda *a, **k: "",
        msg_ok=lambda e=None: "ok",
        msg_error=lambda e: "err",
    )
    dlg.bal_window.bal_plugin.MAX_WILLEXECUTOR_FEE.get.return_value = 500000
    with patch.object(dialogs_mod, "select_push",
                      return_value=({}, {WE1: EXCLUDED_FEE, WE2: EXCLUDED_INVALID})), \
         patch.object(dialogs_mod, "push_will",
                      return_value=push_mod.PushResult()) as p:
        BalBuildWillDialog.loop_push(dlg)
    assert rows == [
        ("Will-Executor excluded", "we1.test: fee above the maximum", "#c80"),
        ("Will-Executor excluded", "we2.test: not a valid will-executor", "#c80"),
    ]
    p.assert_called_once()


def _code_lines(path):
    """The lines of *path* without comments."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.split("#", 1)[0]
            if stripped.strip():
                yield stripped


def test_no_other_copy_of_the_sending():
    """Only the engine sends the will (the parallel push).

    ``get_willexecutor_transactions`` is still called elsewhere, only to ask
    whether something is left to send (the messages after a rebuild).
    """
    pattern = re.compile(r"\bpush_transactions_parallel\s*\(")
    found = set()
    for folder, _dirs, files in os.walk(ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for line in _code_lines(path):
                if pattern.search(line) and "def " not in line:
                    found.add(rel)
    assert found == {"engine/push.py"}, found


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
