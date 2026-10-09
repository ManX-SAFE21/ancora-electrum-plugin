"""
Regression test for F5 (CHANGELOG #74): the Check dialog's phase-2 error
handler (signing / sending the will).

``BalBuildWillDialog.on_error_phase2`` called ``self.bal_window.upade_all()``
(typo). ``BalWindow`` has no such method, so an error while signing or sending
the will raised AttributeError inside the error handler itself: the error row
was never shown and the window was not refreshed.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_gui_phase2_error.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.gui.qt.dialogs import BalBuildWillDialog


class _Dialog:
    """Just what on_error_phase2 uses (no Qt widgets)."""

    COLOR_ERROR = BalBuildWillDialog.COLOR_ERROR
    msg_error = BalBuildWillDialog.msg_error
    on_error_phase2 = BalBuildWillDialog.on_error_phase2

    def __init__(self):
        self.labels = []
        # Only the methods BalWindow really has (a MagicMock would accept the
        # misspelled name too and hide the bug).
        self.bal_window = SimpleNamespace(update_all=MagicMock())

    def msg_edit_row(self, line, row=None):
        self.labels.append(line)

    def _finish_report(self):
        # CHANGELOG #84b: the dialog's report is filled on every ending;
        # tested in tests/test_build_report.py.
        self.report_finished = True


def test_phase2_error_refreshes_and_shows_the_error():
    dlg = _Dialog()
    err = RuntimeError("signing failed")
    dlg.on_error_phase2((RuntimeError, err, None))
    dlg.bal_window.update_all.assert_called_once_with()
    assert len(dlg.labels) == 1
    assert "signing failed" in dlg.labels[0]
    assert BalBuildWillDialog.COLOR_ERROR in dlg.labels[0]
    assert dlg.report_finished


if __name__ == "__main__":
    test_phase2_error_refreshes_and_shows_the_error()
    print("[OK] phase-2 error handler test passed")
