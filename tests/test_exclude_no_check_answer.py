""""Exclude" next to "received, no answer to the check" (CHANGELOG #181).

Owner's ZIP tests of #178-#180 (2026-10-22): we.divinaly.support received
the will and then did not answer the check. The Check window showed
"received, no answer to the check" with no button: only the Will-Executor
tab could drop it. Now that row offers "Exclude" and "Retry" too, with its
own question (it HAS the will; "did not receive your will" would be false;
"You can also press Retry later"). The #93 question for a will-executor
that did not get the will stays as it was.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address, and the will with three delivery dates of
``test_multiverse_chain_rebuild.py``.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_exclude_no_check_answer.py
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from PyQt6.QtWidgets import QPushButton
from test_exclude_received import SILENT, _copies, _silent_check
from test_failed_executor import _real_dialog

from bal.core.build_report import BuildReport
from bal.gui.qt.widgets import BuildReportView

# The sessions of the chain tests (three will-executors, synchronized).
make = chain.make

WE0 = "https://we0.test"
WE1 = "https://we1.test"
WE2 = "https://we2.test"

NEW_QUESTION = '"we1.test" received your will, but did not answer the check.'
RETRY_LATER = "You can also press Retry later."
OLD_QUESTION = '"we1.test" did not receive your will.'


def _buttons(widget, text):
    """The buttons of ``widget`` showing ``text``."""
    return [b for b in widget.findChildren(QPushButton) if b.text() == text]


def _snapshot(state):
    """we0 and we2 verified, we1 in ``state``."""
    r = BuildReport()
    r.set_executor(WE0, "verified")
    r.set_executor(WE1, state, 15)
    r.set_executor(WE2, "verified")
    return r.snapshot()


def test_the_box_offers_exclude_and_retry_for_no_answer_to_the_check():
    """Only next to the "no answer to the check" row, only once asked."""
    view = BuildReportView()
    view.render(_snapshot("no_check_answer"))
    assert not _buttons(view, "Exclude") and not _buttons(view, "Retry")
    excluded, retried = [], []
    view.on_exclude = excluded.append
    view.on_retry = lambda: retried.append(True)
    view.render(_snapshot("no_check_answer"))
    assert len(_buttons(view, "Exclude")) == 1
    assert len(_buttons(view, "Retry")) == 1
    _buttons(view, "Exclude")[0].click()
    _buttons(view, "Retry")[0].click()
    assert excluded == [WE1] and retried == [True]


def test_all_verified_offers_nothing():
    view = BuildReportView()
    view.on_exclude = MagicMock()
    view.on_retry = MagicMock()
    view.render(_snapshot("verified"))
    assert not _buttons(view, "Exclude") and not _buttons(view, "Retry")


def _question(state):
    """The question "Exclude" asks for we1 in ``state`` (answered "No")."""
    d = _real_dialog()
    try:
        d.question = MagicMock(return_value=False)
        d._retry_check = MagicMock()
        d.bal_window.can_exclude_willexecutor.return_value = True
        d._report().set_executor(WE0, "verified")
        d._report().set_executor(WE1, state)
        d._exclude_executor(WE1)
        assert not d.bal_window.exclude_willexecutor.called
        return d.question.call_args.args[0]
    finally:
        d.thread.stop()
        d.deleteLater()


def test_its_own_question_for_no_answer_to_the_check():
    text = _question("no_check_answer")
    assert text.startswith(NEW_QUESTION)
    assert "The copies it already received stay with it." in text
    assert RETRY_LATER in text and text.endswith("Exclude it?")
    assert "did not receive" not in text


def test_the_question_for_a_will_not_received_is_unchanged():
    for state in ("failed", "timeout"):
        text = _question(state)
        assert text.startswith(OLD_QUESTION + " 1 other will-executor has it.")
        assert RETRY_LATER not in text and NEW_QUESTION not in text


def test_exclude_after_no_answer_to_the_check_keeps_its_copies(make, monkeypatch):
    """The whole way: ``SILENT`` never answers the check; its row offers
    "Exclude"; "Yes" deselects it, its 13 copies stay, the next Check does
    not ask it, sends it nothing and calls it "excluded"."""
    s = make()
    _silent_check(s, monkeypatch)
    chain.three_dates(s)
    d = s.check()
    snap = d._report().snapshot()
    assert dict(snap["executors"])[SILENT][0] == "no_check_answer"
    view = BuildReportView()
    view.on_exclude = MagicMock()
    view.on_retry = MagicMock()
    view.render(snap)
    assert len(_buttons(view, "Exclude")) == 1
    _buttons(view, "Exclude")[0].click()
    view.on_exclude.assert_called_once_with(SILENT)
    copies = _copies(s)
    assert len(copies) == 13
    d.question = MagicMock(return_value=True)
    d._retry_check = MagicMock()
    d._exclude_executor(SILENT)
    assert "You can also press Retry later." in d.question.call_args.args[0]
    d._retry_check.assert_called_once_with()
    assert SILENT not in s.bw.selected_willexecutors()
    assert sorted(_copies(s)) == sorted(copies)
    pushed = len(s.world.pushed)
    snap = s.check()._report().snapshot()
    assert dict(snap["executors"])[SILENT][0] == "excluded"
    assert not [u for u, _w, _r in s.world.pushed[pushed:] if u == SILENT]
    assert sorted(_copies(s)) == sorted(copies)
