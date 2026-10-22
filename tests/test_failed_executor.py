"""
A will-executor that did not get the will: "Exclude" and "Retry" (CHANGELOG #93).

* ``BalWindow.can_exclude_willexecutor`` / ``exclude_willexecutor``: never
  the last selected executor; deselect and save like the Will-Executor tab;
  drop the copies never sent (kept VALID they kept the summary orange and
  the report locked), keep every copy sent at least once (#178: it may be
  on the will-executor's server, even after a failed check);
* ``BuildReportView``: "Exclude" next to each executor that did not get the
  will and "Retry" under the box, only once the dialog has finished;
* ``BalBuildWillDialog``: the buttons are wired when the run ends; Exclude
  asks first; Retry runs the check again in the same dialog.

The whole flow (an executor not answering, Exclude, Check: "Your will is in
force", report allowed) is checked with the scenario harness (A20).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_failed_executor.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

# The helpers of the #84b-1 tests: will items and a dialog without Qt set-up.
from test_build_report import _dialog, _item

from bal.core.build_report import BuildReport
from bal.core.willexecutors import Willexecutors
from bal.gui.qt import dialogs as dialogs_mod
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.widgets import BuildReportView
from bal.gui.qt.window import BalWindow

_APP = QApplication.instance() or QApplication(sys.argv)

WE0 = "https://we0.test"
WE1 = "https://we1.test"


def _window(selected=(WE0, WE1), willitems=None):
    bw = SimpleNamespace(
        willexecutors={u: {"url": u, "selected": u in selected, "base_fee": 1000}
                       for u in (WE0, WE1)},
        willitems=willitems or {},
        bal_plugin=MagicMock(),
        save_willitems=MagicMock(),
    )
    bw.can_exclude_willexecutor = lambda url: BalWindow.can_exclude_willexecutor(bw, url)
    return bw


# --------------------------------------------------------- the window

def test_never_the_last_selected_executor():
    bw = _window(selected=(WE1,))
    with patch.object(Willexecutors, "save") as save:
        assert not BalWindow.can_exclude_willexecutor(bw, WE1)
        assert not BalWindow.exclude_willexecutor(bw, WE1)
    assert bw.willexecutors[WE1]["selected"] and not save.called
    assert not BalWindow.can_exclude_willexecutor(bw, WE0)      # not selected


def test_exclude_deselects_saves_and_drops_only_the_copies_never_sent():
    """#178 (owner's ZIP test): only the copies never sent are dropped. A
    copy sent at least once stays, even when its sending got no answer
    (PUSH_FAIL) or its check failed (CHECK_FAIL: we.divinaly.support's
    503 after "pushed: thx"): the will-executor may hold it."""
    sent_before = _item("COMPLETE", "PUSHED", "CHECKED", "REPLACED", url=WE1)
    push_failed = _item("COMPLETE", "PUSH_FAIL", url=WE1)
    check_failed = _item("COMPLETE", "CHECK_FAIL", url=WE1)
    never_sent = _item("COMPLETE", url=WE1)
    other = _item("COMPLETE", "PUSHED", "CHECKED", url=WE0)
    items = {"old": sent_before, "push": push_failed, "check": check_failed,
             "never": never_sent, "we0": other}
    bw = _window(willitems=items)
    with patch.object(Willexecutors, "save") as save:
        assert BalWindow.exclude_willexecutor(bw, WE1)
    assert bw.willexecutors[WE1]["selected"] is False
    save.assert_called_once_with(bw.bal_plugin, bw.willexecutors)
    assert set(bw.willitems) == {"old", "push", "check", "we0"}
    bw.save_willitems.assert_called_once_with()


# ------------------------------------------------------------ the view

def _snapshot():
    r = BuildReport()
    r.set_executor(WE0, "verified")
    r.set_executor(WE1, "timeout", 20)
    r.set_executor("https://we2.test", "failed")
    return r.snapshot()


def _buttons(widget, text):
    return [b for b in widget.findChildren(QPushButton) if b.text() == text]


def test_view_offers_exclude_and_retry_only_when_asked():
    view = BuildReportView()
    view.render(_snapshot())
    assert not _buttons(view, "Exclude") and not _buttons(view, "Retry")
    excluded, retried = [], []
    view.on_exclude = excluded.append
    view.on_retry = lambda: retried.append(True)
    view.render(_snapshot())
    exclude = _buttons(view, "Exclude")
    assert len(exclude) == 2 and len(_buttons(view, "Retry")) == 1
    exclude[0].click()
    _buttons(view, "Retry")[0].click()
    assert excluded == [WE1] and retried == [True]      # rows sorted by url


# ---------------------------------------------------------- the dialog

def _real_dialog():
    bw = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")}).bal_window
    bw.bal_plugin.read_file.return_value = b""
    bw.window.format_amount_and_units.side_effect = lambda sats: "{} sat".format(sats)
    return BalBuildWillDialog(bw, parent=QWidget())


def test_dialog_wires_the_buttons_when_the_run_ends():
    d = _real_dialog()
    try:
        assert d.report_view.on_exclude is None and d.report_view.on_retry is None
        d._add_close_button()
        assert d.report_view.on_exclude == d._exclude_executor
        assert d.report_view.on_retry == d._retry_check
    finally:
        d.thread.stop()
        d.deleteLater()


def test_exclude_asks_first_and_never_the_last():
    d = _real_dialog()
    try:
        d.show_warning = MagicMock()
        d.question = MagicMock(return_value=False)
        d._retry_check = MagicMock()
        d.bal_window.can_exclude_willexecutor.return_value = False
        d._exclude_executor(WE1)
        assert d.show_warning.called and not d.question.called
        d.bal_window.can_exclude_willexecutor.return_value = True
        d._report().set_executor(WE0, "verified")
        d._exclude_executor(WE1)                                    # "No"
        assert "1 other will-executor has it." in d.question.call_args.args[0]
        assert not d.bal_window.exclude_willexecutor.called
        d.question.return_value = True
        d._exclude_executor(WE1)                                    # "Yes"
        d.bal_window.exclude_willexecutor.assert_called_once_with(WE1)
        d._retry_check.assert_called_once_with()
    finally:
        d.thread.stop()
        d.deleteLater()


def test_retry_runs_the_check_again_in_the_same_dialog():
    d = _real_dialog()
    try:
        d._add_close_button()
        d.msg_set_pushing(d.msg_ok())
        old_thread = d.thread
        with patch.object(dialogs_mod, "TaskThread") as thread_cls:
            d._retry_check()
        old_thread.stop()
        assert d._close_button is None and d._close_layout is None
        assert d.labels == [] and d._report().step("send")[0] == "todo"
        assert d.report_view.on_exclude is None
        add = thread_cls.return_value.add
        add.assert_called_once()
        assert add.call_args.args[0] == d.task_phase1
    finally:
        d.deleteLater()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
