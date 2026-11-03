"""The word under each step of the Check window (CHANGELOG #183).

Owner: "ok", "4/5", "0/5 · 27 s / 45 s" under the step names were small
(11 px) and grey, hard to read on the coloured cells. Preview approved
(proposal B): 13 px, in the step's own colour; a darker grey for the
steps not reached or not needed.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_step_words.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication, QLabel

from bal.core.build_report import BuildReport
from bal.gui.qt.widgets import BuildReportView

_APP = QApplication.instance() or QApplication(sys.argv)


def _words():
    """``{word: style}`` of the words under the steps of a report."""
    r = BuildReport()
    r.set_step("check", "ok")
    r.set_step("prepare", "skip")
    r.set_step("sign", "todo", "x")
    r.set_step("send", "warn", "4/5")
    r.set_step("verify", "run", "0/5 · 27 s / 45 s")
    view = BuildReportView()
    view.render(r.snapshot())
    notes = {note for _s, _st, note in r.snapshot()["steps"]}
    return {lbl.text(): lbl.styleSheet() for lbl in view.findChildren(QLabel)
            if lbl.text() in notes}


def test_the_words_are_bigger_and_in_the_step_colour():
    words = _words()
    colours = {state: fg for state, (_sym, fg, _bg) in BuildReportView._STEP_STYLE.items()}
    assert words["4/5"] == "color:{};font-size:13px;".format(colours["warn"])
    assert words["0/5 · 27 s / 45 s"] == "color:{};font-size:13px;".format(colours["run"])
    assert words["x"] == "color:#444444;font-size:13px;"
    assert all("font-size:13px;" in style for style in words.values())
