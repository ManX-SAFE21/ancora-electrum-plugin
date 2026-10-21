"""
The verification inside the Building Will dialog (CHANGELOG #84b-2).

* ``loop_verify``: after the sending, every sent and not yet verified item is
  looked up on its will-executor (all at once, 20 s limit); the answers are
  recorded as before (CHECKED / CHECK_FAIL) and shown live: the step
  counter, who is still awaited, each executor's state;
* nothing to verify: no request at all;
* ``task_phase2`` verifies after the sending, before "All done";
* the Check button and the wizard no longer open the separate "Check
  Transaction" window;
* the sending keeps its "waiting for" sentence while it runs;
* at wallet close the dialog says what it is doing and why.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_verify_in_dialog.py
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication, QLabel, QWidget

# The helpers of the #84b-1 tests: a dialog without Qt set-up and will items.
from test_build_report import _dialog, _item, _steps

from bal.core.willexecutors import Willexecutors
from bal.gui.qt import dialogs as dialogs_mod
from bal.gui.qt import lists as lists_mod
from bal.gui.qt.dialogs import BalBuildWillDialog, BalWizardDialog

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

WE0 = "https://we0.test"
WE1 = "https://we1.test"


def _fake_check(answers, snapshots=None, dialog=None):
    """A check_transactions_parallel that answers from ``answers``.

    ``answers``: ``{wid: "same" | "other" | "timeout"}``. Before answering it
    ticks once and, with ``dialog``, records the report as seen live.
    """
    calls = []

    def fake(items, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        items = list(items)
        calls.append(items)
        on_tick()
        if snapshots is not None:
            snapshots.append(dialog._report().snapshot())
        for wid, url in items:
            answer = answers[wid]
            if answer == "timeout":
                continue
            tx = str(dialog.bal_window.willitems[wid].tx) if answer == "same" else "x"
            on_each(wid, url, {"tx": tx}, None)
        for wid, url in items:
            if answers[wid] == "timeout":
                on_timeout(wid, url)

    fake.calls = calls
    return fake


def _sent_dialog():
    """Two items sent to two executors, not verified yet."""
    return _dialog({
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", url=WE1),
    })


# ------------------------------------------------------------- loop_verify

def test_verify_shows_progress_then_records_the_answers():
    d = _sent_dialog()
    live = []
    fake = _fake_check({"a": "same", "b": "timeout"}, live, d)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    assert sorted(fake.calls[0]) == [("a", WE0), ("b", WE1)]
    # While waiting: the counter, who is awaited, both executors "verifying".
    steps = {s: (st, note) for s, st, note in live[0]["steps"]}
    assert steps["verify"] == ("run", "0/2 · 0 s / 20 s")
    assert live[0]["result"] == (
        "run",
        "Verifying your will with the will-executors… "
        "waiting for: we0.test, we1.test"
        # #172: the copies and the seconds, always, on their own line.
        "<br><b>Copies checked: 0 of 2 · 0 s (at most 20 s)</b>",
    )
    assert dict(live[0]["executors"]) == {
        WE0: ("verifying", None), WE1: ("verifying", None)}
    # The answers are recorded exactly as the Check button recorded them.
    a, b = d.bal_window.willitems["a"], d.bal_window.willitems["b"]
    assert a.get_status("CHECKED") and a.get_status("PUSHED")
    # #147: no answer in time is not "not received": still sent.
    assert b.get_status("PUSHED") and not b.get_status("CHECK_FAIL")
    assert d._report().executors() == {
        WE0: ("verified", None), WE1: ("no_check_answer", 20)}
    log = " ".join(d.labels)
    assert "checked {} - a".format(WE0) in log and "Timeout - no answer" in log
    d._finish_report()
    assert _steps(d)["verify"] == ("warn", "1/2")


def test_verify_a_different_tx_is_not_received():
    d = _dialog({"a": _item("COMPLETE", "PUSHED", url=WE0)})
    fake = _fake_check({"a": "other"}, dialog=d)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    assert d.bal_window.willitems["a"].get_status("CHECK_FAIL")
    assert d._report().executors() == {WE0: ("failed", None)}
    d._finish_report()
    assert _steps(d)["verify"] == ("err", "0/1")


def test_verify_all_confirmed():
    d = _sent_dialog()
    fake = _fake_check({"a": "same", "b": "same"}, dialog=d)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    d._finish_report()
    assert _steps(d)["verify"] == ("ok", "2/2")
    snap = d._report().snapshot()
    assert dict(snap["executors"]) == {WE0: ("verified", None), WE1: ("verified", None)}
    assert snap["result"][0] == "ok"            # the will summary: in force


def test_nothing_to_verify_asks_nobody():
    d = _dialog({
        "a": _item("COMPLETE", "PUSHED", "CHECKED", url=WE0),   # verified before
        "b": _item("COMPLETE", url=WE1),                        # never sent
    })
    fake = MagicMock()
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    assert not fake.called and d.labels == []


def test_phase2_verifies_after_sending_and_before_all_done():
    d = _dialog({})
    d.have_to_sign = False
    order = []
    d.loop_verify = lambda: order.append(("verify", len(d.labels)))
    d._show_still_active_warning = MagicMock()
    d._show_next_steps_hint = MagicMock()
    d.task_phase2(None)
    assert order and "Broadcasting your will to executors" in d.labels[order[0][1] - 1]
    assert "All done" in d.labels[-1]


def test_sending_count_shown_before_the_verification():
    d = _sent_dialog()
    d.msg_set_pushing(d.msg_ok())
    assert _steps(d)["send"] == ("ok", "ok")
    d._merge_executors_and_send_note()
    assert _steps(d)["send"] == ("ok", "2/2")
    assert d._report().executors() == {
        WE0: ("to_verify", None), WE1: ("to_verify", None)}


def test_cancel_at_signing_leaves_sending_to_do():
    """Owner's screenshot: "Sending: not needed" after a Cancel at signing."""
    d = _dialog({
        "a": _item("COMPLETE", "PUSHED", "CHECKED", url=WE0),
        "b": _item(url=WE1),                                    # still to sign
    })
    d.msg_set_signing("Aborted")
    d.msg_set_pushing("Nothing to do")
    d._finish_report()
    steps = _steps(d)
    assert steps["sign"] == ("warn", "interrupted")
    assert steps["send"] == ("todo", "")
    assert steps["verify"] == ("warn", "1/2")
    assert d._report().snapshot()["result"][0] == "warn"


def test_a_verification_error_never_stops_the_dialog():
    d = _dialog({})
    d.have_to_sign = False
    d.loop_verify = MagicMock(side_effect=RuntimeError("network down"))
    d._show_still_active_warning = MagicMock()
    d._show_next_steps_hint = MagicMock()
    d.task_phase2(None)
    assert "All done" in d.labels[-1]
    assert d._report().snapshot()["boxes"]["problems"] == ["network down"]


# ------------------------------------------- the sending keeps "waiting for"

def test_sending_keeps_waiting_for_while_it_runs():
    d = _dialog({"a": _item("COMPLETE", url=WE0), "b": _item("COMPLETE", url=WE1)})
    d.msg_set_pushing()                                   # task_phase2's "Wait"
    seen = {}

    def fake_push(selected, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        on_each(WE0, selected[WE0], True, None)
        on_tick()
        seen["snap"] = d._report().snapshot()
        on_each(WE1, selected[WE1], True, None)

    records = {WE0: {"txsids": ["a"]}, WE1: {"txsids": ["b"]}}
    with patch.object(Willexecutors, "get_willexecutor_transactions",
                      return_value=records), \
         patch.object(Willexecutors, "is_selected", return_value=True), \
         patch.object(Willexecutors, "is_valid", return_value=True), \
         patch.object(Willexecutors, "push_transactions_parallel", fake_push):
        d.loop_push()
    steps = {s: (st, note) for s, st, note in seen["snap"]["steps"]}
    assert steps["send"] == ("run", "1/2 · 0 s / 20 s")
    assert seen["snap"]["result"] == (
        "run", "Sending your will to the will-executors… waiting for: we1.test")


# ------------------------------------------------- texts and status class

def test_progress_texts_and_status_class():
    text = BalBuildWillDialog.verify_status_text(1, 2, 4, 20, {WE1})
    assert text == "Verifying 1/2 (4s / 20s) — waiting for: we1.test"
    assert BalBuildWillDialog.push_status_text(0, 1, 3, 20, {WE0}) == (
        "Broadcasting 0/1 (3s / 20s) — waiting for: we0.test")
    d = _dialog()
    assert d._status_class(text)[0] == "run"
    assert d._status_class("Verifica 1/2 (4s / 20s)")[0] == "run"   # translated
    d.msg_set_verifying()
    assert _steps(d)["verify"] == ("run", "")
    assert d._report().snapshot()["result"] == (
        "run", "Verifying your will with the will-executors…")


# ------------------------------- no separate "Check Transaction" window

def test_check_button_and_wizard_open_no_separate_window():
    for module, call in (
        (lists_mod, lambda fake: lists_mod.PreviewList.check(fake)),
        (dialogs_mod, lambda fake: BalWizardDialog.on_next_we(fake)),
    ):
        fake = MagicMock()
        fake.bal_window.willitems = {"a": _item("COMPLETE", "PUSHED", url=WE0)}
        with patch.object(module, "BalBuildWillDialog") as dialog_cls:
            call(fake)
        dialog_cls.return_value.build_will_task.assert_called_once_with()
        assert not fake.bal_window.check_transactions.called


# ----------------------------------------------------- at wallet close

def _real_dialog(**kwargs):
    bw = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED", url=WE0)}).bal_window
    bw.bal_plugin.read_file.return_value = b""
    bw.window.format_amount_and_units.side_effect = lambda sats: "{} sat".format(sats)
    return BalBuildWillDialog(bw, parent=QWidget(), **kwargs)


def _visible_texts(d):
    return [w.text() for w in d.findChildren(QLabel) if not w.isHidden()]


def test_at_wallet_close_the_dialog_says_what_and_why():
    d = _real_dialog(closing=True)
    try:
        d.show()
        assert d.windowTitle() == "Closing the wallet: checking your will"
        intro = [t for t in _visible_texts(d) if t.startswith("You are closing")]
        assert len(intro) == 1
        assert "verifies that they received it" in intro[0]
        assert "setting" not in intro[0].lower()      # owner: never suggest it
        d._add_close_button()
        _APP.processEvents()            # Qt shows new widgets of a shown window
        assert "When you press Close, the wallet finishes closing." in _visible_texts(d)
    finally:
        d.thread.stop()
        d.deleteLater()


def test_otherwise_the_dialog_is_unchanged():
    d = _real_dialog()
    try:
        d.show()
        assert d.windowTitle() == "Building Will"
        d._add_close_button()
        _APP.processEvents()
        texts = _visible_texts(d)
        assert not [t for t in texts if t.startswith("You are closing")]
        assert "When you press Close, the wallet finishes closing." not in texts
    finally:
        d.thread.stop()
        d.deleteLater()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
