"""
Tests for ``bal.gui.qt.calendar``.

Covers the GUI/OS glue that stayed in this module: BalCalendar.open_with_default_app.
The RFC-5545 helpers (format_time, ical_escape, fold_ical_line, write_temp_ics)
moved to ``bal.core.reminders`` and are tested in ``tests/test_core_reminders.py``.

Run:
    QT_QPA_PLATFORM=offscreen python3 tests/test_gui_calendar.py
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from bal.gui.qt.calendar import BalCalendar

# ------------------------------------------------------------------ #
# open_with_default_app
# ------------------------------------------------------------------ #


def test_open_with_default_app_not_found():
    result = BalCalendar.open_with_default_app(
        "/nonexistent/calendar_app", "/tmp/fake.ics"
    )
    assert result is False


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All calendar tests passed")
