"""Deselected will-executors are not "to send" (CHANGELOG #134).

Owner's real test: a will built for 5 will-executors, 3 signed, sent and
verified, 2 signed but not sent; then 2 deselected. The sending rightly
skips a deselected will-executor (``Willexecutors.get_willexecutor_
transactions``), but the summary said "3 of 5 verified it · 2 to be sent"
with a Send button, and the Check window "to send", "3/5": Send was pressed
forever and nothing happened. Now one rule (``summary.selected_urls`` +
``executor_states(..., selected)``) for the summary, the Check window's
rows, the report and the Future view: a deselected will-executor that never
received the will is "deselected: not sent" and is not counted; a line
explains it with an "Open Will-Executors" button; selected again, it is
"to send" and Send pushes it.

``tests/flowkit.py`` wallets with 5 fake will-executors (each its own fee
address), the test password, a fixed clock.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from bal.core.summary import selected_urls, will_summary

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
WE = ["https://we{}.test".format(i) for i in range(5)]
OFF = WE[3:]


@pytest.fixture
def make(monkeypatch):
    """The owner's state: a will for 5 will-executors, 3 verified, 2 signed
    but not sent (``world.down`` at the sending, then the failure flag
    cleared: the state of a right-click Sign), the 2 then deselected."""
    fk.ensure_event_loop()
    made = []

    def _make():
        """The session (see the fixture)."""
        s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
        s.world.add_executor()
        for i in (3, 4):
            s.world.executors[WE[i]] = {
                "url": WE[i], "selected": True, "base_fee": 1000,
                "address": fk.address(4 + i), "status": "200", "info": "fake"}
        made.append(s)
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.world.down = set(OFF)
        s.check()
        s.world.down = set()
        for w in s.bw.willitems.values():
            if (w.we or {}).get("url") in OFF:
                w.set_status("PUSH_FAIL", False)
        s.bw.save_willitems()
        for url in OFF:
            s.world.executors[url]["selected"] = False
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def summary(s):
    """The Will tab summary with the selection rule."""
    return will_summary(s.bw.willitems, 2, s.bw.will_settings, True, now=fk.FIXED_NOW,
                        still_active=s.bw._still_active_for_summary(),
                        long_dates=True, selected=s.bw.selected_willexecutors())


def row(smr, label):
    """One line of the summary."""
    return next(r for r in smr["rows"] if r["label"] == label)


def test_deselected_are_not_to_send(make):
    """3 verified, 2 deselected: green "in force", 3 of 3, no Send; the
    line about the 2 deselected. Without the rule it read "2 to be sent"."""
    s = make()
    assert s.bw.selected_willexecutors() == set(WE[:3])
    smr = summary(s)
    assert (smr["state"], smr["kind"], smr["action"]) == ("ok", "ok", None)
    assert row(smr, "Will-Executor")["text"] == "3 of 3 received and verified it"
    assert smr["deselected"] == 2
    assert smr["deselected_text"] == (
        "2 will-executors are deselected, so they did not receive the will. To "
        "send it to them too, select them again.")
    old = will_summary(s.bw.willitems, 2, s.bw.will_settings, True, now=fk.FIXED_NOW)
    assert old["state"] == "complete_send"


def test_check_pushes_nothing_new_and_rows_say_deselected(make):
    """The Check (Send): nothing is pushed to the deselected ones; their
    rows say "deselected: not sent", the counts are over the 3 selected."""
    s = make()
    pushed = len(s.world.pushed)
    dlg = s.check()
    assert not [u for u, _w, _r in s.world.pushed[pushed:] if u in OFF]
    snap = dlg._report().snapshot()
    rows = dict(snap["executors"])
    assert all(rows[u][0] == "deselected" for u in OFF)
    assert all(rows[u][0] == "verified" for u in WE[:3])
    assert snap["result"][1].startswith("<b>Your will is in force</b>")
    from bal.gui.qt.widgets import BuildReportView
    assert BuildReportView.executor_text("deselected") == "deselected: not sent"


def test_selected_again_is_to_send_and_send_pushes_it(make):
    """we3 selected again: "Will in force: complete the sending", Send; the
    Check pushes the will to we3 (we4 stays deselected)."""
    s = make()
    s.world.executors[WE[3]]["selected"] = True
    smr = summary(s)
    assert (smr["state"], smr["action"]) == ("complete_send", "send")
    assert smr["note"] == ("3 of 4 will-executors received and verified it. "
                           "Send it to the other one too, for more safety.")
    assert smr["deselected"] == 1
    assert smr["deselected_text"].startswith("1 will-executor is deselected")
    pushed = len(s.world.pushed)
    s.check()
    urls = {u for u, _w, _r in s.world.pushed[pushed:]}
    assert WE[3] in urls and WE[4] not in urls


def test_selected_urls():
    """The shared rule's input: the selected URLs of the plugin's list."""
    assert selected_urls({"a": {"selected": True}, "b": {"selected": False},
                          "c": "broken"}) == {"a"}
    assert selected_urls(None) == set()


def test_summary_box_line_and_button(make, monkeypatch):
    """The Will tab box: the line and the "Open Will-Executors" button; the
    button opens the will-executor window, and its closing refreshes the
    box (reselected ones are "to send")."""
    from test_gui_window_init_migration import real_window

    s = make()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    opened = []
    monkeypatch.setattr(bw, "show_willexecutor_dialog",
                        lambda on_close=None: opened.append(on_close))
    bw.update_will_summary()
    box = bw.will_summary_widget
    assert not box.deselected_row.isHidden()
    assert box.deselected_label.text().startswith("2 will-executors are deselected")
    assert box.deselected_button.text() == "Open Will-Executors"
    box.deselected_button.click()
    assert opened == [bw._after_executors_window]
    s.world.executors[WE[3]]["selected"] = True
    opened[0]()                                   # the window closes
    assert box.button.text() == "Send" and not box.button.isHidden()
    assert box.deselected_label.text().startswith("1 will-executor is deselected")


def test_will_executor_window_runs_close_callbacks(make, monkeypatch):
    """The Tools window runs its close callbacks (the refresh above)."""
    from test_gui_window_init_migration import real_window

    from bal.gui.qt.dialogs import WillExecutorDialog

    s = make()
    bw = real_window(s, monkeypatch)
    calls = []
    d = WillExecutorDialog(bw)
    d.on_close_callbacks.append(lambda: calls.append(1))
    d.show()
    d.close()
    assert calls == [1]


def test_check_window_line_and_button():
    """The Check window's will-executors box: "deselected: not sent", the
    line, and "Open Will-Executors" calling the dialog's action."""
    from bal.core.build_report import BuildReport
    from bal.gui.qt.widgets import BuildReportView

    report = BuildReport()
    report.merge_executors({WE[0]: "verified", WE[3]: "deselected", WE[4]: "deselected"})
    view = BuildReportView()
    calls = []
    view.on_open_executors = lambda: calls.append(1)
    view.render(report.snapshot())
    texts = [w.text() for w in view.findChildren(QLabel)]
    assert texts.count("– deselected: not sent") == 2
    assert any(t.startswith("2 will-executors are deselected") for t in texts)
    button = [b for b in view.findChildren(QPushButton) if b.text() == "Open Will-Executors"]
    assert len(button) == 1
    button[0].click()
    assert calls == [1]


def test_list_row_counts_only_selected(make, monkeypatch):
    """The List's row of the will in force follows the same rule: "sent and
    verified 3 of 3 · 2 deselected"; selected again: 3 of 4 · 1 deselected;
    the refresh at the window's close updates it."""
    from test_gui_window_init_migration import real_window

    s = make()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    lst = bw.will_list_widget

    def row_text():
        """The second line of the version in force."""
        from bal.core import will_versions
        history = will_versions.build_history(dict(bw.willitems), bw.wallet)
        version = history.by_vid()[history.current]
        return lst._version_texts(version)[2]

    assert row_text().endswith("sent and verified 3 of 3 · 2 deselected")
    s.world.executors[WE[3]]["selected"] = True
    assert row_text().endswith("sent and verified 3 of 4 · 1 deselected")
    for url in OFF:
        s.world.executors[url]["selected"] = True
    assert row_text().endswith("sent and verified 3 of 5")
    updated = []
    monkeypatch.setattr(lst, "update", lambda *a: updated.append(1))
    bw._after_executors_window()
    assert updated
