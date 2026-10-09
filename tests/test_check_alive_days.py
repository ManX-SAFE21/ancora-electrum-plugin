"""
Tests for the Check Alive as "N days before the delivery" (CHANGELOG #80).

Owner decision D14: the Check Alive is only "N days before the delivery"
(saved as ``"-Nd"``). Old saved values are converted keeping the same date
(decision D2 = A); what cannot be expressed so becomes the default, 180 days.

* ``check_alive_days`` / ``normalize_check_alive`` (core);
* the defaults (``BalPlugin``);
* the spin box (``ThresholdTimeWidget``);
* ``BalWindow._normalize_check_alive`` saves the conversion once.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_check_alive_days.py
"""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication

from bal.core.checkalive import (
    check_alive_days,
    normalize_check_alive,
    resolve_date_to_check,
    will_executable_now,
)
from bal.core.plugin_base import BalPlugin
from bal.gui.qt.widgets import ThresholdTimeWidget
from bal.gui.qt.window import BalWindow

_app = QApplication.instance() or QApplication(sys.argv)

DAY = 86400
NOW = 1_800_000_000 - (1_800_000_000 % DAY)   # 2027-01-15, midnight UTC
DELIVERY = NOW + 400 * DAY                    # fixed delivery date


def _norm(threshold, locktime=DELIVERY, built=None, creation=None):
    return normalize_check_alive(
        {"threshold": threshold, "locktime": locktime},
        built_locktime=built, creation_date=creation, now=NOW,
    )


# ------------------------------------------------------------ check_alive_days

def test_days_of_the_new_form():
    assert check_alive_days("-180d") == 180
    assert check_alive_days("-1y") == 365
    assert check_alive_days(" -30d ") == 30


def test_days_of_other_forms_is_none():
    for value in ("30d", "1y", DELIVERY, "-0d", "-4000d", "-xd", "", None, "garbage"):
        assert check_alive_days(value) is None, value


# ------------------------------------------------------- normalize_check_alive

def test_new_form_stays_and_years_become_days():
    assert _norm("-30d") == "-30d"
    assert _norm("-1y") == "-365d"


def test_fixed_date_keeps_the_same_date():
    """Fixed Check Alive 31 days before a fixed delivery -> "-31d"."""
    assert _norm(DELIVERY - 31 * DAY) == "-31d"
    converted = _norm(DELIVERY - 31 * DAY)
    settings = {"threshold": converted, "locktime": DELIVERY}
    assert resolve_date_to_check(False, settings, now=NOW) == DELIVERY - 31 * DAY


def test_fixed_date_not_at_midnight_is_rounded_to_whole_days():
    assert _norm(DELIVERY - 31 * DAY + 3 * 3600) == "-31d"


def test_positive_after_creation_keeps_the_same_date():
    """"30d" after a creation 100 days ago: the date is NOW - 70 days."""
    creation = NOW - 100 * DAY
    assert _norm("30d", creation=creation) == "-{}d".format(400 + 70)


def test_relative_delivery_uses_the_date_frozen_in_the_will():
    """"1y" delivery of a will built earlier: its frozen date, not now + 1y."""
    frozen = NOW + 200 * DAY
    assert _norm(frozen - 50 * DAY, locktime="1y", built=frozen) == "-50d"


def test_a_passed_date_before_the_delivery_is_kept():
    """A Check Alive that has passed stays passed: the owner is asked to
    renew (#79) instead of the date silently moving."""
    assert _norm(NOW - 10 * DAY) == "-410d"


def test_values_that_cannot_be_expressed_become_the_default():
    assert _norm(DELIVERY + DAY) == "-180d"            # after the delivery
    assert _norm(DELIVERY) == "-180d"                  # on the delivery
    assert _norm(DELIVERY - 4000 * DAY) == "-180d"     # more than 3650 days
    assert _norm("-4000d") == "-180d"
    assert _norm("garbage") == "-180d"
    assert _norm(None) == "-180d"
    assert normalize_check_alive({"threshold": DELIVERY - DAY}, now=NOW) == "-180d"
    assert normalize_check_alive(None, now=NOW) == "-180d"


def test_will_executable_now():
    """Truly expired (invalidate at once) vs a postpone beyond the margin."""
    assert will_executable_now(NOW - DAY, now=NOW)
    assert will_executable_now(NOW, now=NOW)
    assert not will_executable_now(NOW + DAY, now=NOW)
    assert not will_executable_now(None, now=NOW)


# -------------------------------------------------------------------- defaults

def test_defaults_are_180_days_before_the_delivery():
    assert BalPlugin.default_will_settings()["threshold"] == "-180d"
    assert BalPlugin.default_will_settings_relative()["threshold"] == "-180d"


# ---------------------------------------------------------------- the spin box

def _bal_window(threshold="-180d"):
    return SimpleNamespace(
        bal_plugin=SimpleNamespace(
            WILL_SETTINGS=SimpleNamespace(get=lambda: {"threshold": threshold})
        ),
        will_settings={"threshold": threshold, "locktime": DELIVERY},
        willitems={},
        update_setting_widgets=MagicMock(),
    )


def test_spin_box_range_step_and_value():
    w = ThresholdTimeWidget(_bal_window(), None)
    assert (w.spin.minimum(), w.spin.maximum(), w.spin.singleStep()) == (1, 3650, 30)
    assert w.spin.value() == 180 and w.get_value() == "-180d"
    assert "days" in w.spin.suffix()
    assert "before the delivery" in w.hint.text() and "6 months" in w.hint.text()


def test_spin_box_shows_an_old_value_converted_without_saving_it():
    bw = _bal_window(DELIVERY - 31 * DAY)
    w = ThresholdTimeWidget(bw, None)
    assert w.get_value() == "-31d"
    bw.update_setting_widgets.assert_not_called()


def test_set_value_of_the_new_form_saves_it():
    bw = _bal_window()
    w = ThresholdTimeWidget(bw, None)
    w.set_value("-30d")
    assert w.spin.value() == 30
    bw.update_setting_widgets.assert_called_once_with("-30d", "threshold")
    assert "1 month" in w.hint.text()


def test_user_change_saves_and_syncs():
    bw = _bal_window()
    w = ThresholdTimeWidget(bw, None)
    w.spin.setValue(90)
    bw.update_setting_widgets.assert_called_with("-90d", "threshold", True)
    assert "3 months" in w.hint.text()


def test_resolved_date_turns_red_when_past():
    w = ThresholdTimeWidget(_bal_window(), None)
    past = datetime.fromtimestamp(NOW - DAY, tz=timezone.utc)
    w.show_resolved_date(past, now=NOW)
    assert w.label.text() == past.strftime("%Y-%m-%d")
    assert "ff0000" in w.label.styleSheet() and w.alarm is past
    future = datetime.fromtimestamp(NOW + DAY, tz=timezone.utc)
    w.show_resolved_date(future, now=NOW)
    assert w.label.styleSheet() == ""


def test_read_only_and_legacy_calls():
    w = ThresholdTimeWidget(_bal_window(), None)
    w.set_read_only(True)
    assert w.spin.isReadOnly()
    w.set_read_only(False)
    assert not w.spin.isReadOnly()
    w.set_index(1)                     # no Raw/Date selector any more
    w.apply_user_type_visibility()
    w.apply_user_type_editor_default()
    assert w.editor.label is w.label


# ------------------------------------------------------------------- BalWindow

def _window(threshold):
    win = object.__new__(BalWindow)
    win.will_settings = {"threshold": threshold, "locktime": DELIVERY}
    win.willitems = {}
    win.update_setting_widgets = MagicMock()
    return win


def test_window_saves_the_conversion_once():
    win = _window(DELIVERY - 31 * DAY)
    BalWindow._normalize_check_alive(win)
    win.update_setting_widgets.assert_called_once_with("-31d", "threshold", update_all=True)


def test_window_leaves_the_new_form_alone():
    win = _window("-180d")
    BalWindow._normalize_check_alive(win)
    win.update_setting_widgets.assert_not_called()


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All Check Alive days tests passed")
