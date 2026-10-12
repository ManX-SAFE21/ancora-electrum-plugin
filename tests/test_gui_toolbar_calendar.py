"""The calendar button of the toolbar makes the .ics file in BASIC mode (CHANGELOG #126).

Found while designing #123: the BASIC toolbar passes the delivery date with
its time zone and no "now"; ``build_ics_reminders`` took a naive "now", so
"aware minus naive" raised TypeError, the button logged "failed to
generate .ics" and saved nothing. Here the toolbars of the REAL window (on
the stub Electrum window of ``tests/test_gui_window_init_migration.py``)
build the .ics content in BASIC and ADVANCED mode.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three fake
will-executors with their own fee addresses, throw-away test wallet with
the test password, fixed clock 2026-10-10).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_toolbar_calendar.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
import test_gui_window_init_migration as init_tests
from PyQt6.QtWidgets import QApplication

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# The sessions fixture (three will-executors) and the real-window helpers.
make = init_tests.make
real_window = init_tests.real_window
toolbars = init_tests.toolbars


def test_basic_toolbar_makes_the_calendar_file(make, monkeypatch):
    """BASIC: the will 4 months away (as the owner's now) gives the three
    fixed reminders, 30, 10 and 1 day before the delivery, on both
    toolbars."""
    s = make(mode="basic", locktime=fk.ts(2027, 2, 27))
    s.check()
    bw = real_window(s, monkeypatch)
    for toolbar in toolbars(bw):
        toolbar.on_locktime_change()
        content = toolbar._ics_provider()
        assert content is not None, "no .ics content (see the log)"
        assert content.count("BEGIN:VEVENT") == 3
        assert "DTSTART:20270128" in content          # 30 days before 27/02


@pytest.mark.parametrize("mode", ["advanced"])
def test_advanced_toolbar_still_makes_the_calendar_file(make, monkeypatch, mode):
    """ADVANCED is unchanged: reminders spread over the Check Alive period."""
    s = make(mode=mode, locktime=fk.ts(2027, 2, 27))
    s.check()
    bw = real_window(s, monkeypatch)
    for toolbar in toolbars(bw):
        toolbar.on_locktime_change()
        content = toolbar._ics_provider()
        assert content is not None and "BEGIN:VEVENT" in content


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
