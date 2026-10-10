"""
The Building Will dialog fits its content and stays on the screen (CHANGELOG #96).

Owner's screenshots: the dialog was much taller than its content and its
buttons often ended below the screen; the first fix then made it too short
and hid the will-executors box (the scroll bar showed it). The causes:

* the room around the report (margins, the row of buttons) was counted
  ~200 px too big: ``QScrollArea.sizeHint()`` keeps the size its content had
  when the dialog was created (~30 px), while the layout reserves at least
  240 px for it;
* the dialog grew while the run showed more (e.g. the log) and never shrank;
* growing moved its bottom edge down, past the screen (Qt places a dialog
  inside the screen only when it opens);
* a fit right after a redraw missed the boxes just drawn: Qt shows a widget
  added to a visible dialog only later, and a widget not shown yet counts
  as empty.

Now it fits its content when the run is over and when the log is closed,
never cuts it while it fits under the cap, is never shorter than 560 px,
takes at most 75% of the screen, and moves up when its bottom edge would be
below the screen.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_dialog_fits_screen.py
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication, QWidget

# The helpers of the #84b-1 tests: will items and a dialog without Qt set-up.
from test_build_report import _dialog, _item

from bal.gui.qt.dialogs import BalBuildWillDialog

_APP = QApplication.instance() or QApplication(sys.argv)

# Available screen areas (without the taskbar): a 1366x768 notebook, a
# 1366x940 screen and a full HD one.
NOTEBOOK = QRect(0, 0, 1366, 728)
MID = QRect(0, 0, 1366, 900)
TALL = QRect(0, 0, 1920, 1040)


def _real_dialog(screen_area=TALL):
    """A real dialog on a screen, built as the #84b height test does."""
    bw = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")}).bal_window
    bw.bal_plugin.read_file.return_value = b""
    bw.window.format_amount_and_units.side_effect = lambda sats: "{} sat".format(sats)
    d = BalBuildWillDialog(bw, parent=QWidget())
    screen = SimpleNamespace(availableGeometry=lambda: QRect(screen_area))
    d.screen = lambda: screen
    d._test_screen = screen_area
    return d


def _content(d):
    """The height of what the scroll area shows, measured as it is now."""
    d.scroll_area.widget().layout().invalidate()
    return d.scroll_area.widget().sizeHint().height() + 2 * d.scroll_area.frameWidth()


def _limit(d):
    """The tallest the dialog may be on its (test) screen."""
    return int(d._test_screen.height() * d.MAX_SCREEN_SHARE)


def test_owner_limits():
    assert BalBuildWillDialog.MAX_SCREEN_SHARE == 0.75
    assert BalBuildWillDialog.OPEN_HEIGHT == 560


def test_short_content_is_not_shorter_than_the_minimum():
    d = _real_dialog()
    try:
        d.msg_set_status("Checking variables", None, d.msg_ok())
        assert d.height() >= d.OPEN_HEIGHT               # while the run goes on
        d._add_close_button()
        assert d.height() == d.OPEN_HEIGHT               # short content
        d.msg_edit_row("a later log line")               # the log is closed
        assert d.height() == d.OPEN_HEIGHT
    finally:
        d.thread.stop()
        d.deleteLater()


def test_a_notebook_screen_caps_the_minimum():
    """On a 1366x768 notebook 75% of the screen is below 560 px: the cap
    wins and the buttons stay on the screen."""
    d = _real_dialog(NOTEBOOK)
    try:
        d.show()
        QApplication.processEvents()
        d._add_close_button()
        QApplication.processEvents()
        assert d.height() == _limit(d) < d.OPEN_HEIGHT
        assert d.frameGeometry().bottom() <= NOTEBOOK.bottom()
    finally:
        d.thread.stop()
        d.deleteLater()


def test_end_of_run_never_hides_the_boxes():
    """Owner's screenshots: the will-executors box was below the fold.

    The real end of a run: the dialog is visible, the last redraw adds the
    boxes and the row of buttons in the same turn of the event loop.
    """
    d = _real_dialog()
    try:
        d.show()
        QApplication.processEvents()
        d.msg_update()
        QApplication.processEvents()
        report = d._report()
        for n in range(4):
            report.set_executor("https://we{}.test".format(n), "verified")
        report.put("warnings", "pct", "The percentages of the heirs add up to "
                   "50%: the will always empties the whole wallet, so the "
                   "shares are recalculated in proportion. " * 2)
        report.put("changes", "fee", "The miners' fee changed: a new will "
                   "has to be prepared. " * 2)
        d._add_close_button()
        d.msg_update()
        QApplication.processEvents()
        QApplication.processEvents()
        bar = d.scroll_area.verticalScrollBar()
        assert bar.maximum() == 0, "{} px hidden below".format(bar.maximum())
        assert d.OPEN_HEIGHT <= d.height() <= _limit(d)
        assert d.frameGeometry().bottom() <= TALL.bottom()
    finally:
        d.thread.stop()
        d.deleteLater()


def test_room_around_the_report_is_counted_right():
    d = _real_dialog()
    try:
        d._add_close_button()
        for i in range(20):
            d.msg_edit_row("log line {}".format(i))
        d.show()
        QApplication.processEvents()
        d.log_toggle.setChecked(True)
        QApplication.processEvents()
        content = _content(d)
        # Before: content + ~258 px; now margins, spacing and the buttons.
        assert d.height() <= max(content + 120, d.OPEN_HEIGHT)
        assert d.scroll_area.verticalScrollBar().maximum() == 0   # all visible
    finally:
        d.thread.stop()
        d.deleteLater()


def test_fit_counts_the_buttons_of_a_visible_dialog():
    """Qt shows widgets added to a visible dialog only later: the row of
    buttons must count all the same (it was 28 px short)."""
    d = _real_dialog()
    try:
        d.show()
        QApplication.processEvents()
        d._add_close_button()
        fitted = d.height()
        QApplication.processEvents()
        assert fitted >= d.vbox.totalMinimumSize().height()
        assert d.height() == fitted
        assert d.scroll_area.verticalScrollBar().maximum() == 0   # all visible
    finally:
        d.thread.stop()
        d.deleteLater()


def test_closing_the_log_gives_the_room_back():
    d = _real_dialog()
    try:
        d._add_close_button()
        closed = d.height()
        for i in range(60):
            d.msg_edit_row("log line {}".format(i))
        d.log_toggle.setChecked(True)
        opened = d.height()
        assert closed < opened <= _limit(d)
        d.log_toggle.setChecked(False)
        assert d.height() == closed
    finally:
        d.thread.stop()
        d.deleteLater()


def test_moves_up_instead_of_ending_below_the_screen():
    d = _real_dialog(MID)
    try:
        d.show()
        QApplication.processEvents()
        # Opened just above the taskbar, as Qt may place it.
        d.move(100, MID.bottom() - d.frameGeometry().height() - 10)
        QApplication.processEvents()
        before = d.height()
        d.log_toggle.setChecked(True)
        for i in range(40):
            d.msg_edit_row("log line {}".format(i))       # the dialog grows
        QApplication.processEvents()
        assert d.height() > before
        assert d.frameGeometry().bottom() <= MID.bottom()
        assert d.y() >= MID.top()
    finally:
        d.thread.stop()
        d.deleteLater()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
