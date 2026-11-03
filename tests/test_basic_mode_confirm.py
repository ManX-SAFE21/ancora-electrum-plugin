"""Going back to BASIC asks first; the wheel does not change the mode (#174).

Owner's report (2026-10-21): with the plugin Settings window open in
ADVANCED, scrolling with the pointer over "User type" or a wrong click put
the mode back to BASIC without a word, and the advanced tools disappeared.
Owner decisions: the mouse wheel does not change the user type; leaving
ADVANCED asks "Go back to BASIC mode? The advanced tools will be hidden. The
will does not change." Yes / No, No by default; on No nothing changes. One
shared function (``common.switch_user_type``) for the Settings dialog, its
reset buttons and the wizard.

Here: the real Settings dialog of a real ``Plugin`` on an isolated config
(only the modal loop and the GUI-only calls replaced, as in
``test_settings_basic_rows``), the real question box with its ``exec``
replaced, and the real wizard on a ``tests/flowkit.py`` wallet (three fake
will-executors, the test password).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_basic_mode_confirm.py
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
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QMessageBox, QPushButton
from test_gui_window_init_migration import real_window

import bal.gui.qt.plugin as plugin_mod
from bal.gui.qt import common as common_mod
from bal.gui.qt.plugin import Plugin
from bal.gui.qt.widgets import WillSettingsWidget

# Kept at module level: Qt widgets need an application object.
_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
WARNING = "Warning: change these settings only if you know what you are doing."
QUESTION = ("Go back to BASIC mode? The advanced tools will be hidden. "
            "The will does not change.")


@pytest.fixture
def answer(monkeypatch):
    """The owner's answer to "Go back to BASIC mode?" (True: Yes).

    The real box is built; only its ``exec`` is replaced: it records the
    text, the default button and the buttons, then clicks the answer (or
    nothing, ``None``, as Escape would leave it). Every question is in
    ``asked``.
    """
    state = {"yes": True, "asked": []}

    def fake_exec(box):
        """Record the box and click the chosen button."""
        buttons = {b.text(): b for b in box.buttons()}
        state["asked"].append({
            "title": box.windowTitle(), "text": box.text(),
            "default": box.defaultButton().text(),
            "escape": box.escapeButton().text(),
            "buttons": sorted(buttons)})
        if state["yes"] is not None:
            buttons["Yes" if state["yes"] else "No"].click()
        return 0

    monkeypatch.setattr(common_mod.QMessageBox, "exec", fake_exec)
    return state


def _plugin(user_type):
    """A real Plugin on an isolated config, with the GUI-only calls stubbed."""
    cfg = SimpleConfig({"electrum_path": tempfile.mkdtemp(prefix="bal_test_")})
    plugin = Plugin(None, cfg, "bal")
    plugin.get_window_title = lambda s: s
    plugin.read_file = lambda *a: b""
    plugin.broadcast_transactions = lambda *a, **k: None
    plugin.update_all = lambda *a, **k: None
    plugin.USER_TYPE.set(user_type)
    return plugin


def _dialog(plugin):
    """The Settings dialog of *plugin*, built without its modal loop."""
    captured = []
    with patch.object(plugin_mod, "show_modal",
                      side_effect=lambda dlg: captured.append(dlg) or True):
        plugin.settings_dialog(None, None)
    return captured[0]


def _combo(dialog):
    """The "User Type" combo of the dialog."""
    (combo,) = [c for c in dialog.findChildren(QComboBox)
                if c.count() == 2 and c.itemText(1) == "ADVANCED"]
    return combo


def _warning(dialog):
    """The red warning, shown in ADVANCED only."""
    (label,) = [w for w in dialog.findChildren(QLabel) if w.text() == WARNING]
    return label


def _wheel(widget, up):
    """Send one notch of the mouse wheel to *widget*; True when accepted."""
    delta = 120 if up else -120
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0),
                        QPoint(0, delta), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event.isAccepted()


def test_the_question_box(answer):
    """The text, Yes / No, No by default and on Escape; no click is No."""
    plugin = _plugin("advanced")
    answer["yes"] = None
    assert common_mod.switch_user_type(plugin, False) is False
    (asked,) = answer["asked"]
    assert asked == {"title": "BASIC mode", "text": QUESTION, "default": "No",
                     "escape": "No", "buttons": ["No", "Yes"]}
    assert plugin.USER_TYPE.get() == "advanced"


def test_switch_no_keeps_advanced_and_touches_nothing(answer):
    """No: ADVANCED stays, the windows are not refreshed, False."""
    plugin = _plugin("advanced")
    refreshed = []
    plugin.update_all = lambda *a, **k: refreshed.append(1)
    answer["yes"] = False
    assert common_mod.switch_user_type(plugin, False) is False
    assert plugin.USER_TYPE.get() == "advanced" and refreshed == []
    answer["yes"] = True
    assert common_mod.switch_user_type(plugin, False) is True
    assert plugin.USER_TYPE.get() == "basic" and refreshed == [1]
    assert len(answer["asked"]) == 2


def test_basic_to_basic_asks_nothing(answer):
    """Already BASIC: nothing is asked."""
    plugin = _plugin("basic")
    assert common_mod.switch_user_type(plugin, False) is True
    assert answer["asked"] == [] and plugin.USER_TYPE.get() == "basic"


def test_wheel_does_not_change_the_user_type(answer):
    """The wheel over "User Type" is left to the dialog, in both modes."""
    for user_type, index in (("advanced", 1), ("basic", 0)):
        plugin = _plugin(user_type)
        dialog = _dialog(plugin)
        combo = _combo(dialog)
        assert isinstance(combo, common_mod.NoWheelComboBox)
        for up in (True, False, True, False):
            assert _wheel(combo, up) is False
        assert combo.currentIndex() == index
        assert plugin.USER_TYPE.get() == user_type
        dialog.close()
    assert answer["asked"] == []


def test_a_plain_combo_did_change_with_the_wheel():
    """Control: a plain QComboBox takes the wheel (what the owner hit)."""
    combo = QComboBox()
    combo.addItems(["BASIC", "ADVANCED"])
    combo.setCurrentIndex(1)
    _wheel(combo, True)
    assert combo.currentIndex() == 0


def test_settings_combo_no_then_yes(answer):
    """Choosing BASIC in the Settings: No keeps ADVANCED and its rows;
    Yes switches and hides them."""
    plugin = _plugin("advanced")
    dialog = _dialog(plugin)
    combo = _combo(dialog)
    answer["yes"] = False
    combo.setCurrentIndex(0)
    assert plugin.USER_TYPE.get() == "advanced"
    assert combo.currentIndex() == 1 and not _warning(dialog).isHidden()
    answer["yes"] = True
    combo.setCurrentIndex(0)
    assert plugin.USER_TYPE.get() == "basic"
    assert combo.currentIndex() == 0 and _warning(dialog).isHidden()
    assert len(answer["asked"]) == 2
    dialog.close()


def test_reset_button_of_the_user_type_asks(answer):
    """The ↺ of "User Type" in ADVANCED asks too."""
    plugin = _plugin("advanced")
    dialog = _dialog(plugin)
    combo = _combo(dialog)
    (reset,) = [b for b in dialog.findChildren(QPushButton)
                if b.text() == "↺" and b.parentWidget() is not None
                and _same_row(dialog, b, combo)]
    answer["yes"] = False
    reset.click()
    assert plugin.USER_TYPE.get() == "advanced" and combo.currentIndex() == 1
    answer["yes"] = True
    reset.click()
    assert plugin.USER_TYPE.get() == "basic" and combo.currentIndex() == 0
    assert len(answer["asked"]) == 2
    dialog.close()


def _same_row(dialog, a, b):
    """True when widgets *a* and *b* sit on the same row of the grid."""
    from PyQt6.QtWidgets import QGridLayout

    (grid,) = dialog.findChildren(QGridLayout)
    rows = {}
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if w in (a, b):
            rows[w] = grid.getItemPosition(i)[0]
    return len(rows) == 2 and rows[a] == rows[b]


def test_reset_all_no_keeps_advanced_but_resets_the_rest(answer):
    """"Reset to Default Setting" in ADVANCED: No keeps ADVANCED, the other
    settings still go back to their defaults; Yes gives BASIC."""
    plugin = _plugin("advanced")
    plugin.NUM_REMINDERS.set(7)
    dialog = _dialog(plugin)
    (button,) = [b for b in dialog.findChildren(QPushButton)
                 if b.text() == "Reset to Default Setting"]
    answer["yes"] = False
    button.click()
    assert plugin.USER_TYPE.get() == "advanced"
    assert _combo(dialog).currentIndex() == 1
    assert plugin.NUM_REMINDERS.get() == plugin.NUM_REMINDERS.default
    answer["yes"] = True
    button.click()
    assert plugin.USER_TYPE.get() == "basic" and _combo(dialog).currentIndex() == 0
    assert len(answer["asked"]) == 2
    dialog.close()


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


def test_wizard_no_keeps_advanced_and_the_form(make, monkeypatch, answer):
    """The wizard's mode selector: the wheel is ignored; No keeps ADVANCED
    and the period form untouched; Yes gives BASIC and the fixed form."""
    from bal.core import delivery

    s = make(mode="advanced", period_months=36)
    bw = real_window(s, monkeypatch)
    box = WillSettingsWidget(bw, None, "v", read_only=False).form_box
    settings = s.wallet.db.get_dict("will_settings")
    assert isinstance(box.mode_combo, common_mod.NoWheelComboBox)
    assert _wheel(box.mode_combo, True) is False
    assert box.mode_combo.currentIndex() == 1
    assert box.period_radio.isChecked()
    answer["yes"] = False
    box.mode_combo.setCurrentIndex(0)
    assert s.plugin.USER_TYPE.get() == "advanced"
    assert box.mode_combo.currentIndex() == 1
    assert delivery.form_of(settings) == "period"
    assert box.period_radio.isChecked() and not box.period_radio.isHidden()
    answer["yes"] = True
    box.mode_combo.setCurrentIndex(0)
    assert s.plugin.USER_TYPE.get() == "basic"
    assert delivery.form_of(settings) == "fixed"
    assert len(answer["asked"]) == 2


def test_wizard_no_keeps_a_fixed_date_chosen_in_advanced(make, monkeypatch, answer):
    """ADVANCED with "Fixed date" chosen, then BASIC and No: the fixed date
    stays (the form does not follow the mode as on a switch)."""
    from bal.core import delivery

    s = make(mode="advanced", period_months=36)
    bw = real_window(s, monkeypatch)
    box = WillSettingsWidget(bw, None, "v", read_only=False).form_box
    settings = s.wallet.db.get_dict("will_settings")
    box.fixed_radio.click()
    assert delivery.form_of(settings) == "fixed"
    answer["yes"] = False
    box.mode_combo.setCurrentIndex(0)
    assert s.plugin.USER_TYPE.get() == "advanced"
    assert box.mode_combo.currentIndex() == 1
    assert delivery.form_of(settings) == "fixed" and box.fixed_radio.isChecked()
    assert len(answer["asked"]) == 1


def test_message_box_is_the_qt_one():
    """The shared module's box is Qt's (what the fixture patches)."""
    assert common_mod.QMessageBox is QMessageBox


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
