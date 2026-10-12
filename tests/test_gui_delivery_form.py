"""The delivery form in the GUI: wizard and toolbars (CHANGELOG #127).

Owner decisions 5, 7 and 8 (2026-10-12):

* the wizard's settings page starts with "Plugin mode (applies to every
  wallet)": BASIC / ADVANCED, the SAME ``USER_TYPE`` setting as the plugin
  Settings dialog, with the same "at My Risk" gate (one shared function);
  a wrong phrase puts BASIC back;
* below, "Fixed date" / "Period (renewable)": the period only in ADVANCED,
  both enabled only while the form is not locked (a will in force); a month
  box 1..240 without keyboard tracking, stored when editing finishes, with
  its text "N months (...) -> DD/MM/YYYY"; while locked, the lock sentence;
  switching the wizard back to BASIC sets the form back to fixed only when
  it is not locked;
* the Will and Heirs toolbars: a fixed date as before; the period form shows
  "36 months (3 years) -> 10/10/2029" instead of the date editor, and both
  follow every change of form, period or date, in both directions.

The REAL ``BalWindow`` with its two tabs (``test_gui_window_init_migration``
builds it on a ``tests/flowkit.py`` wallet: three fake will-executors with
their own fee addresses, the test password, a fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_delivery_form.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QComboBox
from test_gui_window_init_migration import real_window, toolbars

from bal.core import delivery
from bal.engine import context as engine_context
from bal.gui.qt import common as common_mod
from bal.gui.qt import widgets as widgets_mod
from bal.gui.qt.widgets import FORM_LOCKED_TEXT, WillSettingsWidget

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"


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


@pytest.fixture
def phrase(monkeypatch):
    """What the owner types in the "at My Risk" box (``None``: Cancel).

    ``QInputDialog.getText`` of the shared gate is replaced; every call is
    recorded in ``asked``.
    """
    state = {"text": "at My Risk", "asked": 0}

    def get_text(parent, title, label, *a, **kw):
        """The box: the phrase typed, or Cancel."""
        state["asked"] += 1
        if state["text"] is None:
            return "", False
        return state["text"], True

    monkeypatch.setattr(common_mod.QInputDialog, "getText", staticmethod(get_text))
    return state


def wizard_page(bw):
    """The wizard's settings page (vertical, editable) on the window."""
    return WillSettingsWidget(bw, None, "v", read_only=False)


def settings_of(s):
    """The wallet's own delivery settings (the live dict)."""
    return s.wallet.db.get_dict("will_settings")


def period_shown(toolbar):
    """The period text the toolbar's delivery field shows, or None when the
    date editor is shown."""
    field = toolbar.widgets["locktime"]
    if field.period_label.isHidden():
        assert not field.editor.isHidden()
        return None
    assert field.editor.isHidden()
    return field.period_label.text()


# ------------------------------------------------------------------ wizard

def test_period_is_offered_only_in_advanced_mode(make, monkeypatch):
    """BASIC: no choice shown (a fixed date). ADVANCED: both, enabled (no
    will yet)."""
    s = make(mode="basic")
    bw = real_window(s, monkeypatch)
    box = wizard_page(bw).form_box
    assert box.mode_combo.currentIndex() == 0
    assert box.period_radio.isHidden() and box.fixed_radio.isHidden()
    assert box.fixed_radio.isChecked() and box.lock_label.isHidden()
    s.plugin.USER_TYPE.set("advanced")
    box = wizard_page(bw).form_box
    assert box.mode_combo.currentIndex() == 1
    assert not box.period_radio.isHidden() and box.period_radio.isEnabled()
    assert box.fixed_radio.isEnabled()
    assert box.months_box.isHidden()                # the fixed form


def test_mode_selector_is_the_shared_setting_with_its_gate(make, monkeypatch, phrase):
    """The wizard's selector writes ``USER_TYPE`` (every wallet) through the
    same gate as the Settings dialog: the right phrase gives ADVANCED, a
    wrong one or Cancel puts BASIC back; BASIC needs no phrase."""
    s = make(mode="basic")
    bw = real_window(s, monkeypatch)
    box = wizard_page(bw).form_box
    phrase["text"] = "at my risk!"
    box.mode_combo.setCurrentIndex(1)
    assert phrase["asked"] == 1 and s.plugin.USER_TYPE.get() == "basic"
    assert box.mode_combo.currentIndex() == 0
    phrase["text"] = None
    box.mode_combo.setCurrentIndex(1)
    assert phrase["asked"] == 2 and s.plugin.USER_TYPE.get() == "basic"
    phrase["text"] = "  AT MY RISK "
    box.mode_combo.setCurrentIndex(1)
    assert phrase["asked"] == 3 and s.plugin.USER_TYPE.get() == "advanced"
    assert not box.period_radio.isHidden()
    box.mode_combo.setCurrentIndex(0)
    assert phrase["asked"] == 3 and s.plugin.USER_TYPE.get() == "basic"


def test_settings_dialog_uses_the_same_gate(monkeypatch, phrase):
    """The plugin Settings dialog's "User Type" goes through the same shared
    function: a wrong phrase puts BASIC back there too."""
    from test_gui_delivery_colours import _settings_dialog

    plugin, dialog = _settings_dialog("basic", monkeypatch)
    calls = []
    real = common_mod.switch_user_type
    import bal.gui.qt.plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "switch_user_type",
                        lambda *a, **k: (calls.append(a[1]), real(*a, **k))[1])
    (combo,) = [c for c in dialog.findChildren(QComboBox)
                if c.count() == 2 and c.itemText(1) == "ADVANCED"]
    phrase["text"] = "nope"
    combo.setCurrentIndex(1)
    assert calls == [True] and plugin.USER_TYPE.get() == "basic"
    assert combo.currentIndex() == 0
    phrase["text"] = "at My Risk"
    combo.setCurrentIndex(1)
    assert plugin.USER_TYPE.get() == "advanced"
    dialog.close()


def test_choosing_the_period_in_the_wizard(make, monkeypatch):
    """Period, 36 months, no will in force: the date becomes today + 36
    months; the box shows the text; typing a number is stored once, when
    editing finishes."""
    s = make(mode="advanced")
    bw = real_window(s, monkeypatch)
    page = wizard_page(bw)
    box = page.form_box
    assert not box.months_box.keyboardTracking()
    box.months_box.setValue(36)
    box.period_radio.click()
    d = settings_of(s)
    assert (d["form"], d["period_months"]) == ("period", 36)
    assert d["locktime"] == fk.ts(2029, 10, 10)
    assert box.months_label.text() == "36 months (3 years) -> 10/10/2029"
    assert period_shown(page) == "36 months (3 years) -> 10/10/2029"
    stored = []
    real = engine_context.set_delivery
    monkeypatch.setattr(engine_context, "set_delivery",
                        lambda host, **kw: (stored.append(kw), real(host, **kw))[1])
    box.months_box.lineEdit().setText("24")
    box.months_box.interpretText()
    assert stored == []                           # nothing while typing
    box.months_box.editingFinished.emit()
    assert stored == [{"months": 24}]
    assert d["locktime"] == fk.ts(2028, 10, 10)
    assert box.months_label.text() == "24 months (2 years) -> 10/10/2028"
    box.fixed_radio.click()
    assert delivery.form_of(d) == "fixed" and d["locktime"] == fk.ts(2028, 10, 10)
    assert period_shown(page) is None


def test_locked_form_shows_the_sentence_and_disables_the_choice(make, monkeypatch):
    """A will signed and sent: both radios disabled, the lock sentence
    shown; switching to BASIC keeps a locked period form."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    box = wizard_page(bw).form_box
    assert box.locked
    assert not box.fixed_radio.isEnabled() and not box.period_radio.isEnabled()
    assert not box.lock_label.isHidden()
    assert box.lock_label.text() == FORM_LOCKED_TEXT
    assert box.months_box.isEnabled()             # N may change: next renewal
    box.mode_combo.setCurrentIndex(0)             # BASIC: no phrase needed
    assert s.plugin.is_basic_mode()
    assert delivery.form_of(settings_of(s)) == "period"
    assert not box.period_radio.isHidden()        # its state stays visible
    assert box.lock_label.isHidden()              # BASIC: no lock sentence


def test_basic_in_the_wizard_sets_an_unlocked_period_back_to_fixed(make, monkeypatch):
    """No will in force: choosing BASIC in the wizard puts the fixed form
    back (the date stays)."""
    s = make(mode="advanced", period_months=36)
    bw = real_window(s, monkeypatch)
    box = wizard_page(bw).form_box
    assert box.period_radio.isChecked()
    box.mode_combo.setCurrentIndex(0)
    d = settings_of(s)
    assert delivery.form_of(d) == "fixed" and "period_months" not in d
    assert d["locktime"] == fk.ts(2029, 10, 10)
    assert box.fixed_radio.isChecked() and box.period_radio.isHidden()
    assert box.fixed_radio.isHidden()


def test_basic_with_a_will_in_force_shows_only_the_mode(make, monkeypatch):
    """The owner's ZIP test (2026-10-12): BASIC, a fixed date, a will in
    force. Only the mode selector is shown: no disabled "Fixed date", no
    lock sentence. ADVANCED shows both again."""
    s = make(mode="basic")
    s.check()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    box = wizard_page(bw).form_box
    assert box.locked
    assert not box.mode_combo.isHidden()
    assert box.fixed_radio.isHidden() and box.period_radio.isHidden()
    assert box.lock_label.isHidden()
    s.plugin.USER_TYPE.set("advanced")
    box = wizard_page(bw).form_box
    assert not box.fixed_radio.isHidden() and not box.lock_label.isHidden()


def test_wizard_grows_to_fit_the_settings_page(make, monkeypatch):
    """The owner's ZIP test (2026-10-12): after "Next" the settings page was
    squeezed into the old dialog size (the "BASIC" box 14 pixels high,
    rows overlapping) until the window was moved. Now the dialog grows at
    once: every row of the delivery box gets its full height."""
    from bal.gui.qt.dialogs import BalWizardDialog
    from bal.gui.qt.widgets import DeliveryFormWidget

    s = make(mode="advanced")
    s.check()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    dialog = BalWizardDialog(bw)
    try:
        dialog.show()
        _APP.processEvents()
        dialog.on_next_heir()
        _APP.processEvents()
        assert dialog.height() >= dialog.minimumSizeHint().height()
        (box,) = dialog.findChildren(DeliveryFormWidget)
        for w in (box.mode_combo, box.fixed_radio):
            assert w.height() >= w.sizeHint().height(), w
        # A wrapped label needs the height of its text at its own width.
        label = box.lock_label
        assert label.height() >= label.heightForWidth(label.width())
    finally:
        dialog.close()


def test_wizard_grows_when_advanced_is_chosen(make, monkeypatch, phrase):
    """The owner's second ZIP test (2026-10-12): BASIC page, then ADVANCED
    chosen in the selector ("at My Risk"): the page gets more rows (Check
    Alive, the two forms, the lock sentence) and the dialog kept its size
    and squeezed them (the "BASIC" box 7 pixels high). Now the dialog grows
    after every new layout of its page."""
    from bal.gui.qt.dialogs import BalWizardDialog
    from bal.gui.qt.widgets import DeliveryFormWidget

    s = make(mode="basic")
    s.check()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    dialog = BalWizardDialog(bw)
    try:
        dialog.show()
        _APP.processEvents()
        assert (dialog.width(), dialog.height()) == (800, 400)  # opens as before
        dialog.on_next_heir()
        _APP.processEvents()
        (box,) = dialog.findChildren(DeliveryFormWidget)
        box.mode_combo.setCurrentIndex(1)
        _APP.processEvents()
        assert s.plugin.USER_TYPE.get() == "advanced"
        assert dialog.height() >= dialog.minimumSizeHint().height()
        for w in (box.mode_combo, box.fixed_radio):
            assert w.height() >= w.sizeHint().height(), w
        label = box.lock_label
        assert not label.isHidden()
        assert label.height() >= label.heightForWidth(label.width())
    finally:
        dialog.close()


def test_no_period_with_multiverse(make, monkeypatch):
    """Owner decision 10: multiverse on, the period cannot be chosen."""
    s = make(mode="advanced")
    s.plugin.ENABLE_MULTIVERSE.set(True)
    bw = real_window(s, monkeypatch)
    assert not wizard_page(bw).form_box.period_radio.isEnabled()


# ------------------------------------------------------------------ toolbars

def test_toolbars_follow_the_form_both_ways(make, monkeypatch):
    """Both toolbars: the date editor for a fixed date; the period text
    instead for the period form; back to the date editor; a new date (the
    renewal, the one-day anticipation) changes the text."""
    s = make(mode="advanced")
    bw = real_window(s, monkeypatch)
    for toolbar in toolbars(bw):
        assert period_shown(toolbar) is None
    engine_context.set_delivery(bw, form="period", months=36)
    bw.refresh_setting_fields()
    for toolbar in toolbars(bw):
        assert period_shown(toolbar) == "36 months (3 years) -> 10/10/2029"
        assert toolbar.widgets["locktime"].get_value() == fk.ts(2029, 10, 10)
    bw.store_will_setting("locktime", fk.ts(2029, 10, 9))
    for toolbar in toolbars(bw):
        assert period_shown(toolbar) == "36 months (3 years) -> 09/10/2029"
    engine_context.set_delivery(bw, form="fixed")
    bw.refresh_setting_fields()
    for toolbar in toolbars(bw):
        assert period_shown(toolbar) is None
        assert toolbar.widgets["locktime"].get_value() == fk.ts(2029, 10, 9)


def test_toolbars_follow_the_wizard(make, monkeypatch):
    """A choice in the wizard reaches both toolbars at once."""
    s = make(mode="advanced")
    bw = real_window(s, monkeypatch)
    box = wizard_page(bw).form_box
    box.months_box.setValue(12)
    box.period_radio.click()
    for toolbar in toolbars(bw):
        assert period_shown(toolbar) == "12 months (1 year) -> 10/10/2027"
    box.fixed_radio.click()
    for toolbar in toolbars(bw):
        assert period_shown(toolbar) is None


def test_period_field_text():
    """The text of the field: months_text, the arrow and the UTC day joined
    outside ``_()``; None for a fixed date."""
    settings = {"form": "period", "period_months": 35,
                "locktime": fk.ts(2029, 9, 21)}
    assert widgets_mod.period_field_text(settings) == (
        "35 months (2 years and 11 months) -> 21/09/2029")
    assert widgets_mod.period_field_text({"locktime": fk.ts(2029, 9, 21)}) is None
    assert widgets_mod.period_field_text(settings, months=1) == (
        "1 month -> 21/09/2029")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
