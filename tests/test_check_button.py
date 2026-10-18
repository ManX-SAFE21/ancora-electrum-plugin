"""
The Check button of the Will tab, as big as the Wizard (CHANGELOG #95).

Owner: Check is the command used most after the Wizard, but it was a small
icon at the far right, next to "Tools". It now has its name on it ("Check
your will"), the same look as the Wizard, and sits right after it:
    Wizard | Check | Calendar | Delivery time | Check Alive | fee

To make room for it, ADVANCED mode lays the toolbar fields on two rows
(delivery time | fee, then Check Alive: #146); BASIC keeps its one row. A
BASIC <-> ADVANCED switch rearranges the existing toolbars without a
restart. CHANGELOG #130 (owner): in BASIC the toolbars hide the delivery
date and the fee; the calendar is a big button of the Will tab (and a
Tools entry), never in the toolbar fields, so the Heirs tab has none.
The Wizard's own look: a light blue background (#133), then, after the
owner's ZIP test of #134, the look of the BASIC wizard's unchecked
quick-choice buttons (white, thin light blue border).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_check_button.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from bal.core.plugin_base import BalPlugin
from bal.gui.qt import lists as lists_mod
from bal.gui.qt import widgets as widgets_mod
from bal.gui.qt.widgets import WillSettingsWidget
from bal.i18n import _

_APP = QApplication.instance() or QApplication(sys.argv)

DELIVERY = 1979683200            # 2032-09-26, a fixed delivery date


class _Host(QWidget):
    """The Will tab list, reduced to what ``create_toolbar`` uses."""

    def __init__(self):
        super().__init__()
        self.bal_window = MagicMock()
        self.bal_window.bal_plugin.read_file.return_value = b""
        for name in ("build_transactions", "ask_password_and_sign_transactions",
                     "export_will", "import_will", "merge_will", "broadcast",
                     "check", "invalidate_will"):
            setattr(self, name, MagicMock())

    def create_toolbar_with_menu(self, title):
        """Electrum's toolbar: title, stretch, "Tools" menu button."""
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(title))
        toolbar.addStretch()
        toolbar.addWidget(QToolButton())
        return toolbar, QMenu()

    _big_button = lists_mod.PreviewList._big_button
    _big_calendar_button = lists_mod.PreviewList._big_calendar_button


def _row():
    host = _Host()
    settings = QWidget()
    settings._ics_provider = lambda: None
    with patch.object(lists_mod, "WillSettingsWidget", return_value=settings), \
            patch.object(lists_mod, "report_menu", return_value=QMenu()):
        toolbar = lists_mod.PreviewList.create_toolbar(host, None)
    host.toolbar_layout = toolbar
    row = toolbar.itemAt(2).widget().layout()
    host.row_layout = row
    # The widgets in order; the stretch between them is not a widget.
    return host, settings, [row.itemAt(i).widget() for i in range(row.count())
                            if row.itemAt(i).widget() is not None]


def test_wizard_on_the_left_check_before_the_calendar():
    """#161 (owner): the Wizard on the left, then the settings; on the
    right the Check ("Verifica/Aggiorna" in Italian) just before the
    calendar. No "Sign the will offline" button any more (it is in the
    Tools menus)."""
    host, settings, widgets = _row()
    wizard, check = widgets[0], widgets[2]
    assert wizard.text().strip() == _("Build / Edit Your Will")      # #127
    assert check.text().strip() == _("Check your will")
    assert not hasattr(host, "offline_button")
    assert widgets[1] is settings and len(widgets) == 4
    # #130 (owner): the calendar on the right, after a stretch.
    assert widgets[3] is host.calendar_button


def test_buttons_stay_on_the_left_calendar_on_the_right():
    """CHANGELOG #130, owner's ZIP test: with every field hidden in BASIC
    the row shrank and the toolbar's stretch pushed both big buttons to
    the right. The row now takes the free width (stretch 1) and has its
    own stretch before the Check and the calendar (#161)."""
    host, _settings, _widgets = _row()
    assert host.toolbar_layout.stretch(2) == 1
    row = host.row_layout
    kinds = ["stretch" if row.itemAt(i).widget() is None else "widget"
             for i in range(row.count())]
    assert kinds == ["widget", "widget", "stretch", "widget", "widget"]


def test_check_looks_like_the_wizard():
    _host, _settings, (wizard, _rest, check, _cal) = _row()
    assert isinstance(check, QPushButton)
    assert check.iconSize() == wizard.iconSize()
    assert check.minimumHeight() == wizard.minimumHeight() == 40
    assert "bold" in check.styleSheet() and "bold" in wizard.styleSheet()
    # #133 gave the Wizard a light blue background; since the owner's ZIP
    # test of #134 it looks like the unchecked quick-choice buttons of the
    # BASIC wizard: white, a thin light blue border, light blue only on
    # hover. The Check keeps the plain look; both keep their size.
    sheet = wizard.styleSheet()
    assert "QPushButton{font-weight:bold; color:#1F2328; background-color:#FFFFFF;" in sheet
    assert "border:1px solid #9EC3EE" in sheet
    assert "QPushButton:hover{background-color:#EAF3FD;}" in sheet
    assert "2px solid" not in wizard.styleSheet()
    assert "border" not in check.styleSheet()
    assert "background" not in check.styleSheet()


def test_check_runs_the_check_and_explains_itself():
    host, _settings, (wizard, _rest, check, _cal) = _row()
    # assert_called_once, not ..._with(): a mock takes Qt's "checked"
    # argument, the real methods take none and Qt leaves it out.
    check.click()
    host.check.assert_called_once()
    wizard.click()
    host.bal_window.init_wizard.assert_called_once()
    # The tooltip names the auto-sign option exactly as it is translated.
    assert "'{}'".format(_("Auto-sign on Check")) in check.toolTip()


# ------------------------------------------------- room for it (the toolbar)

class _Field(QWidget):
    """Stand-in for the Check Alive and fee fields (not what is measured)."""

    valueEdited = pyqtSignal()  # noqa: N815 (the real fields' signal name)

    def __init__(self, bal_window, parent):
        super().__init__(parent)
        self.prefix_widget = QWidget()

    def get_value(self):
        return None

    def set_read_only(self, read_only):
        pass


class _CalendarButton(QToolButton):
    """Stand-in for the calendar export button."""

    def __init__(self, bal_window, provider):
        super().__init__()


def _bal_window(mode):
    """Just what the real delivery field uses from BalWindow.

    ``mode`` is a one-item list ("basic" / "advanced") the test can change.
    """
    settings = {"locktime": DELIVERY, "threshold": "-180d"}
    return SimpleNamespace(
        will_settings=dict(settings),
        willitems={},
        update_setting_widgets=MagicMock(),
        update_combo_setting_widgets=MagicMock(),
        bal_plugin=SimpleNamespace(
            is_basic_mode=lambda: mode[0] == "basic",
            read_file=lambda path: b"",
            EDITABLE_DATES=SimpleNamespace(get=lambda: False),
            WILL_SETTINGS=SimpleNamespace(get=lambda: dict(settings)),
            default_will_settings=BalPlugin.default_will_settings,
            default_will_settings_relative=BalPlugin.default_will_settings_relative,
            default_will_settings_absolute=BalPlugin.default_will_settings_absolute,
        ),
    )


def _settings_row(mode):
    """The real WillSettingsWidget of a toolbar, other fields stood in."""
    with patch.object(widgets_mod, "ThresholdTimeWidget", _Field),             patch.object(widgets_mod, "BalTxFeesWidget", _Field),             patch.object(widgets_mod, "BalCalendarButton", _CalendarButton),             patch.object(WillSettingsWidget, "on_locktime_change", lambda self: None):
        return WillSettingsWidget(_bal_window(mode), None, "h")


def _lines(row):
    """The widgets of the two toolbar rows, in order."""
    return [[r.itemAt(i).widget() for i in range(r.count()) if r.itemAt(i).widget()]
            for r in row._rows]


def test_basic_keeps_one_row_and_its_spacing():
    row = _settings_row(["basic"])
    w = row.widgets
    assert _lines(row) == [[w["locktime"], w["threshold"], w["baltx_fees"]], []]
    forty = 40 * widgets_mod.char_width_in_lineedit()
    assert w["locktime"].minimumWidth() == forty


def test_basic_toolbar_hides_date_fee_and_calendar():
    """CHANGELOG #130 (owner): in BASIC the Will and Heirs toolbars show
    neither the delivery date nor the fee; the calendar button is never in
    these toolbars (the Will tab has its own big one)."""
    row = _settings_row(["basic"])
    w = row.widgets
    assert w["locktime"].isHidden() and w["baltx_fees"].isHidden()
    assert row.calendar_button.isHidden()


def test_advanced_toolbar_keeps_date_and_fee_without_calendar():
    row = _settings_row(["advanced"])
    w = row.widgets
    assert not w["locktime"].isHidden() and not w["baltx_fees"].isHidden()
    assert row.calendar_button.isHidden()


def test_will_tab_big_calendar_button_and_tools_entry():
    """CHANGELOG #130: the calendar button of the Will tab is as tall as the
    two main commands, with a doubled icon, and Tools has "Calendar
    reminders" with the same Open / Open with... / Save."""
    host, _settings, (wizard, _rest, _check, calendar) = _row()
    assert calendar.iconSize() == wizard.iconSize()
    assert calendar.minimumHeight() == wizard.minimumHeight() == 40
    titles = [a.text() for a in host.menu.actions()]
    assert _("Calendar reminders") in titles
    sub = next(a.menu() for a in host.menu.actions()
               if a.text() == _("Calendar reminders"))
    assert sub is calendar.menu()
    assert [a.text() for a in sub.actions()] == [
        _("Open"), _("Open with..."), _("Save")]


def test_tools_menu_order_and_offline_entry():
    """#161 (owner): the Will tab's Tools menu in the owner's order: the
    commands of the will as they are used, then the others; under the line
    the offline route, the calendar reminders and the report; #162: "How
    BAL PROTOCOL works" last. The offline entry opens the window (on the
    step to make now, by itself); the last one opens the explainer."""
    host, _settings, _widgets = _row()
    texts = [a.text() if not a.isSeparator() else "--"
             for a in host.menu.actions()]
    assert texts[:-2] == [
        _("Prepare"), _("Sign"), _("Broadcast will"), _("Check"),
        _("Invalidate will"), _("Display"), _("Export"), _("Import"),
        _("Merge"), "--", _("Sign the will offline…"),
        _("Calendar reminders")]
    # Then the report's submenu (``report_menu``) and the explainer.
    assert host.menu.actions()[-2].menu() is not None
    assert texts[-1] == _("How BAL PROTOCOL works")
    entry = next(a for a in host.menu.actions()
                 if a.text() == _("Sign the will offline…"))
    entry.trigger()
    host.bal_window.show_offline_sign_dialog.assert_called_once_with()
    host.menu.actions()[-1].trigger()
    host.bal_window.show_explainer.assert_called_once_with()


def test_advanced_uses_two_rows_packed_to_the_left():
    row = _settings_row(["advanced"])
    w = row.widgets
    assert _lines(row) == [[w["locktime"], w["baltx_fees"]],
                           [w["threshold"]]]
    assert w["locktime"].minimumWidth() == 0


def test_mode_switch_rearranges_the_existing_toolbar():
    mode = ["basic"]
    row = _settings_row(mode)
    w = row.widgets
    mode[0] = "advanced"
    row.apply_user_type_visibility()
    assert _lines(row) == [[w["locktime"], w["baltx_fees"]],
                           [w["threshold"]]]
    assert not w["locktime"].isHidden()       # #130: shown again at once
    row.apply_user_type_visibility()          # every refresh: nothing moves
    assert len(_lines(row)[0]) == 2
    mode[0] = "basic"
    row.apply_user_type_visibility()
    assert _lines(row)[1] == [] and len(_lines(row)[0]) == 3
    assert w["locktime"].isHidden() and w["baltx_fees"].isHidden()
    assert w["locktime"].minimumWidth() == 40 * widgets_mod.char_width_in_lineedit()

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
