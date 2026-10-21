"""Remaining-time colours of the delivery date in the GUI (CHANGELOG #124).

Owner decision 5 (2026-10-11): green with more than 12 months left, yellow
below 12, red below 6 or when the date has passed; the two thresholds are
editable in ADVANCED Settings, right below "User Type", and are NOT tied to
the Check Alive. Here:

* the will summary's delivery line and the Check dialog's result sentence
  take the colour, with the owner's tooltip texts; the toolbar date field
  keeps its normal look (owner, ZIP-2 test);
* BASIC mode always uses 12 and 6, ADVANCED the stored values;
* the two Settings rows: hidden in BASIC, visible in ADVANCED, no keyboard
  tracking (typing "12" stores 12 once), red kept below yellow, reset;
* ``bal_settings_set`` refuses a threshold out of range or red >= yellow.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three fake
will-executors with their own fee addresses, throw-away test wallets with
the test password, fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_delivery_colours.py
"""

import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.simple_config import SimpleConfig
from electrum.util import UserFacingException
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QLabel,
    QPushButton,
)

import bal.gui.qt.plugin as plugin_mod
from bal.cli.controller import BalSettingsController
from bal.core.summary import will_summary
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.plugin import Plugin
from bal.gui.qt.widgets import (
    DELIVERY_LEVEL_COLOURS,
    BalSpinBox,
    WillSummaryWidget,
)

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"

GREEN, YELLOW, RED = (DELIVERY_LEVEL_COLOURS[k] for k in ("green", "yellow", "red"))


@pytest.fixture
def make(monkeypatch):
    """``tests/flowkit.py`` sessions with three will-executors."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session (``fk.Session`` arguments), three will-executors."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _sent_will(make, months_left):
    """A will signed, sent and verified, ``months_left`` months and ten
    days after the flowkit clock (2026-10-10)."""
    index = 10 + months_left - 1
    s = make(mode="basic", locktime=fk.ts(2026 + index // 12, index % 12 + 1, 20))
    s.check()
    return s


def _summary(s):
    """The will summary of the session's window, with its thresholds."""
    return will_summary(
        s.bw.willitems, len(s.bw.heirs or {}), s.bw.will_settings,
        s.plugin.is_basic_mode(), warn_months=s.plugin.warning_months(),
    )


def _delivery_row(summary):
    """The "Delivery to the heirs" line."""
    return next(r for r in summary["rows"] if r["label"] == "Delivery to the heirs")


# ------------------------------------------------------------ summary + dialog

@pytest.mark.parametrize("months, level, colour", [
    (34, "green", GREEN), (8, "yellow", YELLOW), (3, "red", RED),
])
def test_summary_and_dialog_take_the_colour(make, months, level, colour):
    """The delivery line of the summary box and the Check dialog's result
    sentence take the colour; the box shows the tooltip."""
    s = _sent_will(make, months)
    summary = _summary(s)
    assert summary["state"] == "ok"
    row = _delivery_row(summary)
    assert row["level"] == level
    box = WillSummaryWidget(lambda action: None)
    box.show_summary(summary)
    value = box.grid.itemAtPosition(0, 1).widget()
    assert 'color:{};'.format(colour) in value.text()
    assert value.toolTip() == row["tip"]
    assert (row["tip"] == "") == (level == "green")
    sentence = BalBuildWillDialog._summary_sentence(summary)
    assert 'color:{};'.format(colour) in sentence
    assert row["strong"] in sentence


def test_advanced_thresholds_change_the_colour_basic_ignores_them(make):
    """The owner's ZIP-2 test: 34 months left, yellow 36 -> yellow, red 35
    -> red; BASIC mode always uses 12 and 6 (green)."""
    s = _sent_will(make, 34)
    s.plugin.WARN_YELLOW_MONTHS.set(36)
    assert _delivery_row(_summary(s))["level"] == "green"      # BASIC: 12/6
    s.plugin.USER_TYPE.set("advanced")
    assert s.plugin.warning_months() == (36, 6)
    assert _delivery_row(_summary(s))["level"] == "yellow"
    s.plugin.WARN_RED_MONTHS.set(35)
    row = _delivery_row(_summary(s))
    assert row["level"] == "red"
    assert row["tip"].startswith("Less than 35 months to the delivery")
    s.plugin.WARN_RED_MONTHS.set(40)                  # broken pair: defaults
    assert s.plugin.warning_months() == (12, 6)
    assert _delivery_row(_summary(s))["level"] == "green"


# --------------------------------------------------------------------- toolbar

def test_toolbar_date_keeps_its_normal_look(make, monkeypatch):
    """Owner, ZIP-2 test (2026-10-12): the colour appears only on the
    "Delivery to the heirs" line. The date field of the Will and Heirs
    toolbars (and of the wizard) keeps its normal look, red or not."""
    from test_gui_window_init_migration import real_window, toolbars

    s = _sent_will(make, 3)                         # red in the summary
    bw = real_window(s, monkeypatch)
    assert _delivery_row(_summary(s))["level"] == "red"
    for toolbar in toolbars(bw):
        toolbar.on_locktime_change()
        editor = toolbar.widgets["locktime"].locktime_date_e
        style = editor.styleSheet()
        assert style in ("", "QDateTimeEdit{background-color:#f0f0f0;}"), style
        assert not any(c in style for c in DELIVERY_LEVEL_COLOURS.values())
        assert editor.toolTip() == ""


# -------------------------------------------------------------------- settings

def _settings_dialog(user_type, monkeypatch):
    """The plugin's Settings dialog, built without showing it."""
    cfg = SimpleConfig({"electrum_path": tempfile.mkdtemp(prefix="bal_test_")})
    plugin = Plugin(None, cfg, "bal")
    plugin.get_window_title = lambda title: title
    plugin.read_file = lambda *a: b""
    plugin.broadcast_transactions = lambda *a, **k: None
    plugin.update_all = lambda *a, **k: None
    plugin.USER_TYPE.set(user_type)
    captured = []
    with patch.object(plugin_mod, "show_modal", side_effect=lambda d: captured.append(d)):
        plugin.settings_dialog(None, None)
    return plugin, captured[0]


def _warn_boxes(plugin, dialog):
    """The yellow and red spin boxes of the dialog."""
    yellow = [b for b in dialog.findChildren(BalSpinBox) if b.value() == 12
              and b.maximum() == 120]
    red = [b for b in dialog.findChildren(BalSpinBox) if b.value() == 6
           and b.maximum() == 11]
    assert len(yellow) == 1 and len(red) == 1, (yellow, red)
    return yellow[0], red[0]


def _labels(dialog, text):
    """The labels of the dialog with ``text``."""
    return [lb for lb in dialog.findChildren(QLabel) if lb.text() == text]


def test_settings_rows_hidden_in_basic_visible_in_advanced(monkeypatch):
    """Two rows right below "User Type", ADVANCED only."""
    for user_type, hidden in (("basic", True), ("advanced", False)):
        plugin, dialog = _settings_dialog(user_type, monkeypatch)
        yellow, red = _warn_boxes(plugin, dialog)
        for label in ("Yellow warning: inheritance date less than (months)",
                      "Red warning: inheritance date less than (months)"):
            (lbl,) = _labels(dialog, label)
            assert lbl.isHidden() is hidden
        assert yellow.isHidden() is hidden and red.isHidden() is hidden
        dialog.close()


@pytest.mark.usefixtures("yes_to_basic")
def test_settings_boxes_store_once_keep_red_below_yellow_and_reset(monkeypatch):
    """No keyboard tracking (two digits are stored once); the red box can
    never reach the yellow value; Reset gives 12 and 6 back."""
    plugin, dialog = _settings_dialog("advanced", monkeypatch)
    yellow, red = _warn_boxes(plugin, dialog)
    assert not yellow.keyboardTracking() and not red.keyboardTracking()
    stored = []
    real_set = plugin.WARN_YELLOW_MONTHS.set
    monkeypatch.setattr(plugin.WARN_YELLOW_MONTHS, "set",
                        lambda v: (stored.append(v), real_set(v)))
    yellow.lineEdit().setText("36")
    yellow.interpretText()
    yellow.editingFinished.emit()
    assert stored == [36] and plugin.WARN_YELLOW_MONTHS.get() == 36
    assert red.maximum() == 35
    red.setValue(35)
    assert plugin.WARN_RED_MONTHS.get() == 35 and yellow.minimum() == 36
    assert plugin.warning_months() == (36, 35)
    (reset_all,) = [b for b in dialog.findChildren(QPushButton)
                    if b.text() == "Reset to Default Setting"]
    reset_all.click()
    assert plugin.warning_months() == (12, 6)
    assert (yellow.value(), red.value()) == (12, 6)
    yellow.setValue(36)
    red.setValue(35)
    (grid,) = [g for g in dialog.findChildren(QGridLayout)
               if g.itemAtPosition(6, 1) is not None
               and g.itemAtPosition(6, 1).widget() is yellow]
    assert grid.itemAtPosition(7, 1).widget() is red   # right below yellow
    # User Type is the first row since #151 (it was row 5, above yellow).
    assert isinstance(grid.itemAtPosition(0, 1).widget(), QComboBox)  # User Type
    # Each threshold's own reset button gives BOTH defaults back (red first,
    # since each box bounds the other).
    for row in (6, 7):
        yellow.setValue(36)
        red.setValue(35)
        grid.itemAtPosition(row, 3).widget().click()
        assert (plugin.WARN_YELLOW_MONTHS.get(), plugin.WARN_RED_MONTHS.get()) == (12, 6)
        assert (yellow.value(), red.value()) == (12, 6)
    dialog.close()


# ------------------------------------------------------------- command line

def test_command_line_checks_the_thresholds(monkeypatch):
    """``bal_settings_set``: 1 to 120 months, red below yellow."""
    plugin, dialog = _settings_dialog("advanced", monkeypatch)
    dialog.close()
    ctl = BalSettingsController(plugin)
    assert ctl.settings_set("bal_warn_yellow_months", "24")["value"] == 24
    assert ctl.settings_set("WARN_RED_MONTHS", "3")["value"] == 3
    for key, value in (("bal_warn_yellow_months", "0"),
                       ("bal_warn_yellow_months", "121"),
                       ("bal_warn_red_months", "24"),
                       ("bal_warn_yellow_months", "3")):
        with pytest.raises(UserFacingException):
            ctl.settings_set(key, value)
    assert plugin.warning_months() == (24, 3)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
