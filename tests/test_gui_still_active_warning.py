"""
GUI side of the "an older will is still active" warning (CHANGELOG #69).

``BalBuildWillDialog._show_still_active_warning`` adds a red row to the Check
dialog and keeps the plain text for the popup shown when the dialog is
closed (``_on_close_clicked``). The core rule is tested in
tests/test_core_still_active_warning.py; here the will check is stubbed.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_gui_still_active_warning.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.will import Will
from bal.gui.qt.dialogs import BalBuildWillDialog

OLD = 1979683200      # 2032-09-25 (UTC)
CURRENT = 2011219200  # 2033-09-25


class _Dialog:
    """Just what the two methods under test use (no Qt widgets)."""

    COLOR_ERROR = BalBuildWillDialog.COLOR_ERROR
    msg_error = BalBuildWillDialog.msg_error
    _show_still_active_warning = BalBuildWillDialog._show_still_active_warning
    _on_close_clicked = BalBuildWillDialog._on_close_clicked
    # CHANGELOG #84b: the row goes to the log only and the text to the
    # dialog's report ("Attention" box), see tests/test_build_report.py.
    _log_only = BalBuildWillDialog._log_only
    _report = BalBuildWillDialog._report
    _auto_box_off = False
    _build_report = None

    def __init__(self):
        self.labels = []
        self._next_steps_hint = None
        self.closed = False
        self.bal_window = SimpleNamespace(
            willitems={}, wallet=MagicMock(), will_settings={"locktime": CURRENT},
            show_warning=MagicMock(), show_message=MagicMock(),
        )

    def msg_edit_row(self, line, row=None):
        self.labels.append(line)

    def close(self):
        self.closed = True


def _run(found, current=CURRENT):
    dlg = _Dialog()
    with patch.object(Will, "find_still_active_older_txs", return_value=found), \
         patch.object(Will, "get_current_will_locktime", return_value=current):
        dlg._show_still_active_warning()
    return dlg


def test_no_older_active_tx_no_row_no_popup():
    dlg = _run([])
    assert dlg.labels == []
    dlg._on_close_clicked()
    dlg.bal_window.show_warning.assert_not_called()


def test_warning_row_and_popup():
    item = object()
    dlg = _run([(item, OLD, "https://we.example"), (item, OLD, "https://we.example")])
    # One red row; the duplicate (same executor and date) is listed once.
    assert len(dlg.labels) == 1
    row = dlg.labels[0]
    assert BalBuildWillDialog.COLOR_ERROR in row
    assert row.count("https://we.example") == 1
    assert "2032-09-25" in row and "2033-09-25" in row
    text = dlg._still_active_warning
    assert text.startswith("Warning: an older will is still active.")
    assert "Max" in text
    # #84b: the same text in the "Attention" box of the dialog's report.
    assert dlg._report().snapshot()["boxes"]["warnings"] == [text]
    dlg._on_close_clicked()
    assert dlg.closed
    dlg.bal_window.show_warning.assert_called_once_with(text)


def test_url_is_escaped_in_the_rich_text_row():
    dlg = _run([(object(), OLD, "https://we.example/<b>")])
    assert "&lt;b&gt;" in dlg.labels[0]
    assert "https://we.example/<b>" in dlg._still_active_warning


def test_failure_is_swallowed():
    dlg = _Dialog()
    with patch.object(Will, "find_still_active_older_txs", side_effect=RuntimeError):
        dlg._show_still_active_warning()
    assert dlg.labels == [] and dlg._still_active_warning is None


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All still-active warning GUI tests passed")
