"""The BASIC wizard: When, Heirs, Will-executors, Summary (CHANGELOG #134).

The REAL ``BalWindow`` with its two tabs (``real_window`` of
``test_gui_window_init_migration``) on a ``tests/flowkit.py`` wallet: three
fake will-executors, each with its own fee address, the test password, a
fixed clock (10 October 2026 12:00 UTC). The time zone of the date boxes is
fixed to Europe/Rome (``wizard_date.local_zone``), the welist download and
the ping are faked (nothing reaches the network): a will-executor in
``world.down`` does not answer.

Covered: BASIC shows the four pages in order and ADVANCED the old ones;
the quick choices and the boxes (summer time, day change, the month
length), the past refused; "Leave as it is" with a will, the later-date
note, the period form; a heir added on page 2 gets the new date; only the
will-executors that answer are selected, nobody answers -> Retry, one ->
the warning; the summary; "Create the will" builds, signs, sends and
verifies; Cancel, the close button and Esc restore heirs, date and
selection; the mode link; the look of the shared will-executor widget.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_basic_wizard.py
"""

import copy
import os
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication
from test_gui_window_init_migration import real_window

from bal.core import delivery, wizard_date
from bal.core.heirs import Heirs, percent_total
from bal.core.util import copy_structure
from bal.core.willexecutors import Willexecutors
from bal.gui.qt import basic_wizard
from bal.gui.qt import dialogs as dialogs_mod
from bal.gui.qt.basic_wizard import (
    ExecutorsPage,
    HeirsPage,
    SummaryPage,
    WhenPage,
)
from bal.gui.qt.dialogs import BalWizardDialog, BalWizardHeirsWidget

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
ROME = ZoneInfo("Europe/Rome")
WE = ["https://we0.test", "https://we1.test", "https://we2.test"]


@pytest.fixture
def make(monkeypatch):
    """A session with three will-executors and its REAL window.

    Returns ``(session, window)``; the window's welist download and the
    ping are faked from the session's world, its message boxes recorded in
    ``window.popups``.
    """
    fk.ensure_event_loop()
    made = []
    monkeypatch.setattr(wizard_date, "local_zone", lambda: ROME)

    def _make(check=False, **kw):
        """One session (``fk.Session`` arguments); ``check``: the will is
        built, signed and sent first (a will in force)."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        if check:
            s.check()
        monkeypatch.setattr(basic_wizard, "time_now", s.clock.time)
        bw = real_window(s, monkeypatch)
        bw.init_will()
        install_network(s, bw, monkeypatch)
        return s, bw

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def install_network(s, bw, monkeypatch):
    """The welist download and the ping, from ``s.world`` (no network).

    ``s.world.offline`` True: the download fails. A will-executor in
    ``s.world.down`` does not answer the ping.
    """
    s.world.offline = False
    bw.events = []          # the fake "Please wait" window records here
    bw.popups = []

    def fetch(old):
        """``fetch_will_executors_list``: the world's list (copies)."""
        if s.world.offline:
            raise ConnectionError("offline (test)")
        return {url: dict(copy.deepcopy(we), selected=False)
                for url, we in s.world.executors.items()}

    def ping(wes, *, on_each=None, on_tick=None, **kw):
        """``Willexecutors.ping_servers_parallel``: who is not down answers."""
        for url, we in wes.items():
            ok = url not in s.world.down and not s.world.offline
            we["status"] = 200 if ok else "KO"
            if on_each:
                on_each(url, we, ok)
        return wes

    monkeypatch.setattr(bw, "fetch_will_executors_list", fetch)
    monkeypatch.setattr(Willexecutors, "ping_servers_parallel", staticmethod(ping))
    for name in ("show_warning", "show_message", "show_error"):
        monkeypatch.setattr(
            bw, name, lambda text, *a, _n=name, **k: bw.popups.append((_n, text)))


def settings_of(s):
    """The wallet's own delivery settings (the live dict)."""
    return s.wallet.db.get_dict("will_settings")


def selected_urls(s):
    """The selected will-executors of the world."""
    return sorted(u for u, we in s.world.executors.items() if we.get("selected"))


def visible(widget):
    """Not hidden on purpose (the dialog itself may not be on screen)."""
    return not widget.isHidden()


def open_wizard(bw):
    """The wizard dialog, shown."""
    dialog = BalWizardDialog(bw)
    dialog.show()
    _APP.processEvents()
    return dialog


def press_next(dialog):
    """Press the page's Next (or "Create the will")."""
    dialog.widget.next_button.click()
    _APP.processEvents()
    return dialog.widget


# ------------------------------------------------------------ pages


def test_basic_shows_the_four_pages_in_order(make):
    """BASIC: When -> Heirs -> Will-executors -> Summary, each with the
    step bar and its question; Previous goes back; no Previous on page 1."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        assert isinstance(page, WhenPage)
        assert not hasattr(page, "previous_button")
        assert not hasattr(page, "mode_link")       # #162: no mode link
        bar = page.findChild(basic_wizard.StepBar)
        assert [lab.text() for lab in bar.labels] == [
            "1  When", "2  Heirs", "3  Will-executors", "4  Summary"]
        assert isinstance(press_next(dialog), HeirsPage)
        assert isinstance(press_next(dialog), ExecutorsPage)
        page = press_next(dialog)
        assert isinstance(page, SummaryPage)
        assert page.next_button.text() == "Create the will"
        page.previous_button.click()
        assert isinstance(dialog.widget, ExecutorsPage)
    finally:
        dialog.close()


def test_advanced_keeps_the_old_pages(make):
    """ADVANCED: the old Heirs page first, no BASIC flow, no snapshot."""
    s, bw = make(mode="advanced")
    dialog = open_wizard(bw)
    try:
        assert isinstance(dialog.widget, BalWizardHeirsWidget)
        assert dialog.basic_flow is None
    finally:
        dialog.close()


def test_quick_choices_and_next_saves_the_date(make):
    """No will: "In 2 years" is chosen (no "Leave as it is"); "In 1 year"
    then Next stores 00:00 UTC of today + 12 months."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        assert "keep" not in page.chips
        assert page.chips[24].isChecked()
        assert page.chosen() == fk.ts(2028, 10, 10)
        assert page.big_date.text() == "10 October 2028"
        assert page.time_left.text() == "(in 2 years)"
        # 00:00 UTC is 02:00 in Rome (summer time).
        assert "10 October 2028, 02:00" in page.local_value.text()
        assert "UTC+2" in page.local_value.text()
        assert page.utc_value.text() == "10 October 2028, 00:00 UTC"
        page.chips[12].click()
        assert page.chosen() == fk.ts(2027, 10, 10)
        assert page.boxes.isHidden()
        press_next(dialog)
        assert settings_of(s)["locktime"] == fk.ts(2027, 10, 10)
    finally:
        dialog.close()


def test_choose_the_date_in_local_time(make):
    """"Choose the date…": the boxes start at the quick choice; 11 October
    2028 01:00 in Rome is saved as 10 October 23:00 UTC, with the
    day-change line; a winter date uses UTC+1."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        page.chips["choose"].click()
        assert visible(page.boxes)
        assert (page.year_box.value(), page.month_box.value(),
                page.day_box.value(), page.hour_box.value()) == (2028, 10, 10, 2)
        assert page.month_box.text() == "October"
        assert page.hour_box.text() == "02:00"
        page.day_box.setValue(11)
        page.hour_box.setValue(1)
        assert page.chosen() == fk.ts(2028, 10, 10, 23)
        assert page.utc_value.text() == "10 October 2028, 23:00 UTC"
        assert "11 October 2028, 01:00" in page.local_value.text()
        assert visible(page.shift_label)
        assert "the day before" in page.shift_label.text()
        page.month_box.setValue(1)
        page.year_box.setValue(2029)
        assert page.chosen() == fk.ts(2029, 1, 11, 0)
        assert "UTC+1" in page.local_value.text()
        assert page.shift_label.isHidden()
        page.hour_box.setValue(1)
        press_next(dialog)
        assert settings_of(s)["locktime"] == fk.ts(2029, 1, 11, 0)
    finally:
        dialog.close()


def test_day_follows_the_month_length(make):
    """31 in a 30-day month becomes 30; 29 February only in a leap year."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        page.chips["choose"].click()
        page.month_box.setValue(3)
        page.day_box.setValue(31)
        page.month_box.setValue(4)
        assert page.day_box.value() == 30 and page.day_box.maximum() == 30
        page.month_box.setValue(2)
        page.year_box.setValue(2028)
        assert page.day_box.maximum() == 29
        page.day_box.setValue(29)
        page.year_box.setValue(2029)
        assert page.day_box.value() == 28
    finally:
        dialog.close()


def test_a_moment_in_the_past_is_refused(make):
    """Today 10:00 in Rome is before now (12:00 UTC = 14:00): Next is
    disabled with the message; 15:00 is accepted."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        before = settings_of(s)["locktime"]
        page.chips["choose"].click()
        page.year_box.setValue(2026)
        page.month_box.setValue(10)
        page.day_box.setValue(10)
        page.hour_box.setValue(10)
        assert not page.next_button.isEnabled()
        assert visible(page.error_label)
        assert "already passed" in page.error_label.text()
        page.next_button.click()
        assert isinstance(dialog.widget, WhenPage)
        assert settings_of(s)["locktime"] == before
        page.hour_box.setValue(15)
        assert page.next_button.isEnabled() and page.error_label.isHidden()
    finally:
        dialog.close()


def test_leave_as_it_is_with_a_will_in_force(make):
    """A will in force: "Leave as it is" is offered and chosen, Next keeps
    the date (nothing written); a later date shows the note, an earlier
    one does not."""
    s, bw = make(mode="basic", check=True)
    d = settings_of(s)
    stored = d["locktime"]
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        assert page.chips["keep"].isChecked()
        assert page.chosen() == stored
        assert page.in_force == stored
        assert visible(page.keep_note)
        assert page.keep_note.text() == "The date of the will in force: no change."
        assert page.later_note.isHidden()
        page.chips["choose"].click()
        page.year_box.setValue(2033)
        assert visible(page.later_note)
        assert "later than the will in force" in page.later_note.text()
        page.chips[12].click()
        assert page.later_note.isHidden()
        page.chips["keep"].click()
        writes = []
        real = bw.update_setting_widgets
        bw.update_setting_widgets = lambda *a, **k: writes.append(a) or real(*a, **k)
        press_next(dialog)
        assert writes == [] and d["locktime"] == stored
    finally:
        dialog.close()


def test_period_form_shows_only_the_renewal(make):
    """A period-form wallet (#127): no choice, the renewal text, Next on."""
    s, bw = make(mode="basic", period_months=24)
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        assert page.period and not hasattr(page, "chips")
        texts = [lab.text() for lab in page.findChildren(basic_wizard.QLabel)]
        assert "Period (renewable) — 24 months (2 years)" in texts
        assert any(t.startswith("Next delivery: <b>10 October 2028, 00:00 UTC")
                   for t in texts)
        assert page.next_button.isEnabled()
        before = dict(settings_of(s))
        assert isinstance(press_next(dialog), HeirsPage)
        assert dict(settings_of(s)) == before
    finally:
        dialog.close()


def test_a_new_heir_gets_the_chosen_date(make):
    """Page 2 shows the chosen day; a heir added there gets that date."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        dialog.widget.chips[60].click()
        page = press_next(dialog)
        assert "Delivery to the heirs: <b>10 October 2031, 00:00 UTC</b>" in [
            lab.text() for lab in page.findChildren(basic_wizard.QLabel)]
        assert page.total_label.text() == "Total: 100% of the wallet balance."
        bw.set_heir(["carol", fk.HEIR_ADDR["carol"], "10%", "0"])
        page.heir_list_widget.update()
        assert bw.heirs["carol"][2] == fk.ts(2031, 10, 10)
        assert page.total_label.text().startswith("Total of the shares: 110%.")
    finally:
        dialog.close()


def test_no_heirs_disables_next(make):
    """Without heirs Next is off with "Add at least one heir."."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        page = press_next(dialog)
        assert page.next_button.isEnabled() and page.error_label.isHidden()
        bw.delete_heirs(list(bw.heirs))
        page.heir_list_widget.update()
        assert not page.next_button.isEnabled()
        assert visible(page.error_label)
    finally:
        dialog.close()


def test_percent_total():
    """The total of the percentages; None with a fixed amount; the
    OP_RETURN row is skipped."""
    a = fk.HEIR_ADDR["alice"]
    assert percent_total({}) == 0
    assert percent_total({"a": [a, "60%", 0], "b": [a, "40%", 0]}) == 100
    assert percent_total({"a": [a, "60%", 0], "m": ["OP_RETURN:6869", "0", 0]}) == 60
    assert percent_total({"a": [a, "60%", 0], "b": [a, "100000", 0]}) is None


# --------------------------------------------------------- will-executors


def to_executors(dialog):
    """From page 1 to page 3 (the download and ping run on entering)."""
    press_next(dialog)
    return press_next(dialog)


def test_only_the_executors_that_answer_are_selected(make):
    """we2 does not answer: we0 and we1 are selected, the green line says
    so; Next is on."""
    s, bw = make(mode="basic")
    s.world.down = {WE[2]}
    dialog = open_wizard(bw)
    try:
        page = to_executors(dialog)
        assert selected_urls(s) == WE[:2]
        assert visible(page.status_label)
        assert page.status_label.text() == (
            "✔  List updated: 2 will-executors answer, 2 will-executors selected.")
        assert page.nobody_row.isHidden() and page.one_label.isHidden()
        assert page.next_button.isEnabled()
    finally:
        dialog.close()


def test_nobody_answers_then_retry(make):
    """Offline: the download fails, the saved list is pinged, nobody
    answers: the note with Retry, Next off. Online again: Retry selects
    the three."""
    s, bw = make(mode="basic")
    s.world.offline = True
    dialog = open_wizard(bw)
    try:
        page = to_executors(dialog)
        assert selected_urls(s) == []
        assert visible(page.nobody_row) and page.status_label.isHidden()
        assert not page.next_button.isEnabled()
        assert bw.popups and bw.popups[0][0] == "show_warning"
        s.world.offline = False
        page.retry_button.click()
        assert selected_urls(s) == WE
        assert page.nobody_row.isHidden() and page.next_button.isEnabled()
    finally:
        dialog.close()


def test_one_executor_selected_shows_the_warning(make):
    """Only we0 answers: one selected, "At least 2 ..." shown, Next on."""
    s, bw = make(mode="basic")
    s.world.down = {WE[1], WE[2]}
    dialog = open_wizard(bw)
    try:
        page = to_executors(dialog)
        assert selected_urls(s) == WE[:1]
        assert visible(page.one_label)
        assert "1 will-executor answers, 1 will-executor selected" in page.status_label.text()
        assert page.next_button.isEnabled()
        # Deselected by hand: Next off, the message instead.
        page.we_widget.set_select_all(False)
        assert not page.next_button.isEnabled()
        assert visible(page.none_label)
    finally:
        dialog.close()


# --------------------------------------------------------- summary, create


def test_summary_texts(make):
    """The green box: delivery (UTC, time left, local), heirs, count, fee."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        dialog.widget.chips[12].click()
        to_executors(dialog)
        page = press_next(dialog)
        when = page.values["Delivery to the heirs"].text()
        assert "<b>10 October 2027, 00:00 UTC</b>" in when
        assert "(in 1 year)" in when
        assert "In your time zone: 10 October 2027, 02:00" in when
        assert page.values["Heirs"].text() == "alice 60% · bob 40%"
        assert page.values["Will-executors"].text() == "3 will-executors selected"
        # Owner (ZIP test): one fee only is paid (the copies spend the same
        # coins), never "each".
        assert page.values["Will-executors' fee"].text() == (
            "1000 sat<br><span style='color:#6A737D'>you will pay only one: the "
            "one of the will-executor that sends the will on the delivery "
            "day.</span>")
        # No will in force: no comparison under the delivery.
        assert "In force now" not in when
        s.world.executors[WE[2]]["base_fee"] = 2000
        assert basic_wizard.executor_fee_text(bw, s.world.executors) == (
            "from 1000 sat to 2000 sat<br><span style='color:#6A737D'>you will "
            "pay only one of these: the one of the will-executor that sends "
            "the will on the delivery day.</span>")
    finally:
        dialog.close()


def test_create_the_will_builds_signs_sends_and_keeps(make, monkeypatch):
    """"Create the will" runs today's build window: the will at the chosen
    date is signed, sent to the three will-executors and verified; the
    wizard closes and keeps its changes (nothing restored)."""
    s, bw = make(mode="basic")
    opened = []

    class BuildDialog(fk.FlowDialog):
        """The build window on the session's flow window (same wallet).

        A subclass, not a function: the dialog's code reads static helpers
        of ``dialogs.BalBuildWillDialog`` by that name.
        """

        def __init__(self, bal_window, *a, **k):
            """Built on ``s.bw`` with the heirs the wizard saved."""
            s.bw.heirs = Heirs(s.wallet)
            super().__init__(s.bw)
            opened.append(self)

    monkeypatch.setattr(dialogs_mod, "BalBuildWillDialog", BuildDialog)
    dialog = open_wizard(bw)
    dialog.widget.chips[12].click()
    to_executors(dialog)
    press_next(dialog)
    restored = []
    monkeypatch.setattr(dialog.basic_flow, "restore", lambda: restored.append(1))
    press_next(dialog)                       # "Create the will"
    fk.run_until_idle()
    assert len(opened) == 1 and restored == []
    assert dialog.isHidden()
    assert settings_of(s)["locktime"] == fk.ts(2027, 10, 10)
    items = s.bw.willitems
    assert items and {int(w.tx.locktime) for w in items.values()} == {fk.ts(2027, 10, 10)}
    assert all(w.get_status("COMPLETE") and w.get_status("PUSHED") for w in items.values())
    assert sorted({url for url, _wid, _raw in s.world.pushed}) == WE
    assert sorted({url for url, _wid in s.world.verified}) == WE


# ------------------------------------------------------------- cancel


def change_everything(s, bw, dialog):
    """Page 1: a new date; page 2: a new heir; page 3: we2 does not answer."""
    dialog.widget.chips[60].click()
    page = press_next(dialog)
    bw.set_heir(["carol", fk.HEIR_ADDR["carol"], "10%", "0"])
    page.heir_list_widget.update()
    s.world.down = {WE[2]}
    press_next(dialog)
    assert settings_of(s)["locktime"] == fk.ts(2031, 10, 10)
    assert "carol" in bw.heirs and selected_urls(s) == WE[:2]


@pytest.mark.parametrize("how", ["cancel", "close", "escape"])
def test_cancel_close_and_escape_restore_everything(make, how):
    """Cancel, the close button (X) and Esc put back the heirs (also in
    the wallet), the date and the selection exactly."""
    s, bw = make(mode="basic")
    heirs_before = copy_structure(dict(bw.heirs))
    date_before = settings_of(s)["locktime"]
    dialog = open_wizard(bw)
    change_everything(s, bw, dialog)
    if how == "cancel":
        dialog.widget.abort_button.click()
    elif how == "close":
        dialog.close()
    else:
        dialog.reject()
    _APP.processEvents()
    assert dict(bw.heirs) == heirs_before
    assert dict(Heirs(s.wallet)) == heirs_before
    assert settings_of(s)["locktime"] == date_before
    assert selected_urls(s) == WE


def test_restore_puts_back_the_keys_the_date_write_clears(make):
    """A pending "which date?" question and the later wish survive a
    cancelled wizard (the date write clears them; the restore writes them
    back)."""
    s, bw = make(mode="basic")
    d = settings_of(s)
    d[delivery.LATER_WISH_KEY] = fk.LATER
    dialog = open_wizard(bw)
    dialog.widget.chips[12].click()
    press_next(dialog)
    assert delivery.LATER_WISH_KEY not in d
    dialog.close()
    assert d[delivery.LATER_WISH_KEY] == fk.LATER
    assert d["locktime"] == fk.D0


def test_new_executors_stay_unselected_after_cancel(make):
    """A will-executor first seen in the wizard stays in the list, not
    selected, after Cancel."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    new = "https://we3.test"
    s.world.executors[new] = {"url": new, "selected": True, "base_fee": 1000,
                              "address": fk.address(4), "status": "200"}
    dialog.close()
    assert new in s.world.executors and not s.world.executors[new]["selected"]
    assert selected_urls(s) == WE


def test_why_two_times_starts_closed_and_opens_on_click(make):
    """#162: page 1 shows only the line "Why two times?"; a click opens the
    text, a second click closes it."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        box = dialog.widget.why_box
        assert box.button.text() == "▸ Why two times?"
        assert not visible(box.body)
        box.button.click()
        assert visible(box.body) and "UTC" in box.body.text()
        assert box.button.text() == "▾ Why two times?"
        box.button.click()
        assert not visible(box.body)
    finally:
        dialog.close()


# ------------------------------------------------- the will-executor widget


def test_will_executor_window_look_only(make):
    """The Tools menu window: the empty size line hidden, alternating rows,
    the "without will-executor" option after the buttons (grey); the
    option still switches the setting and the list still selects."""
    from bal.gui.qt.dialogs import WillExecutorDialog

    s, bw = make(mode="basic")
    d = WillExecutorDialog(bw)
    try:
        w = d.will_executor_list_widget
        assert w.size_label.isHidden()
        assert w.will_executor_list_widget.alternatingRowColors()
        layout = w.layout()
        assert layout.indexOf(w.no_executor_row) == layout.count() - 1
        box = [c for c in w.no_executor_row.findChildren(basic_wizard.QWidget)
               if c.metaObject().className() == "BalCheckBox"
               or type(c).__name__ == "BalCheckBox"][0]
        before = bool(s.plugin.NO_WILLEXECUTOR.get())
        box.click()
        assert bool(s.plugin.NO_WILLEXECUTOR.get()) is not before
        w.set_select_all(False)
        assert selected_urls(s) == []
        w.set_select_all(True)
        assert selected_urls(s) == WE
    finally:
        d.close()


# ------------------------------------------- after the owner's ZIP test


def draft_before_will_in_force(make, monkeypatch):
    """The owner's wallet of the ZIP test: a will signed, sent and verified
    for D0 (2032-09-23), then a will PREPARED (not signed, not sent) for an
    earlier date, 2031-09-23 (a Check cancelled at "Sign your will"),
    which is now the stored date. The wallet is
    synchronized (the will in force is read from the chain)."""
    s, _bw = make(mode="basic", check=True)
    s.set_date(fk.EARLIER)
    s.check(cancel_on=("sign your will",))
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
    from test_gui_window_init_migration import real_window
    bw = real_window(s, monkeypatch)
    bw.init_will()
    install_network(s, bw, monkeypatch)
    return s, bw


def test_draft_is_not_called_the_will_in_force(make, monkeypatch):
    """The owner's bug: "Leave as it is" showed the prepared draft's date
    as "the date of the will in force". The will in force is the one the
    executors hold (D0): the note says the date is the one chosen last
    time, the reference line compares with D0, and the later-date note
    compares with D0 too."""
    s, bw = draft_before_will_in_force(make, monkeypatch)
    assert settings_of(s)["locktime"] == fk.EARLIER
    assert basic_wizard.in_force_date(bw) == fk.D0
    assert basic_wizard.has_draft(bw)
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        assert page.in_force == fk.D0
        assert page.chips["keep"].isChecked() and page.chosen() == fk.EARLIER
        assert page.keep_note.text() == (
            "The date you chose last time (prepared will, not sent yet): no change.")
        # Two lines, so the reference is never cut (owner, ZIP test).
        assert page.compare_label.text() == (
            "Will in force: <b>23 September 2032, 00:00 UTC</b><br>Your choice: "
            "<b>23 September 2031, 00:00 UTC</b> (earlier — free)")
        assert page.later_note.isHidden()
        # Between the draft and the will in force: still earlier, no note.
        page.chips["choose"].click()
        page.year_box.setValue(2032)
        page.month_box.setValue(1)
        assert page.chosen() < fk.D0 and page.later_note.isHidden()
        # After the will in force: the note and "later" in the reference.
        page.year_box.setValue(2033)
        assert visible(page.later_note)
        assert ("(later — needs an invalidation transaction: you only pay the "
                "network fee)") in page.compare_label.text()
        # The same date as the will in force: no comparison at all.
        page.year_box.setValue(2032)
        page.month_box.setValue(9)
        page.day_box.setValue(23)
        page.hour_box.setValue(2)
        assert page.chosen() == fk.D0
        assert page.compare_label.isHidden() and page.later_note.isHidden()
    finally:
        dialog.close()


def test_summary_page_compares_with_the_will_in_force(make, monkeypatch):
    """Page 4 with a will in force: the comparison with the will in force.
    #134 wrote it as two lines under the delivery; since #135 (owner's
    mockup) it is the "What changes" box under the green box, with how much
    earlier (bal.core.future.span_text)."""
    s, bw = draft_before_will_in_force(make, monkeypatch)
    dialog = open_wizard(bw)
    try:
        to_executors(dialog)
        page = press_next(dialog)
        when = page.values["Delivery to the heirs"].text()
        assert "In force now" not in when
        texts = [lab.text() for lab in page.changes_box.findChildren(basic_wizard.QLabel)]
        assert "<b>What changes compared with the will in force</b>" in texts
        assert ("23 September 2032, 00:00 UTC → <b>23 September 2031, 00:00 UTC</b>"
                "<br><span style='color:#6A737D'>moved earlier by 1 year — free</span>"
                ) in texts
    finally:
        dialog.close()


def test_will_tab_summary_shows_in_force_and_after_signing(make, monkeypatch):
    """The Will tab summary of the same wallet: long UTC dates, and the two
    lines "In force now" / "After signing" with "moved earlier by 1 year";
    the italic note names the will sent before in the same style. The
    default (the PDF report, step #137) keeps YYYY-MM-DD and no extra
    lines."""
    from bal.core.summary import will_summary

    s, bw = draft_before_will_in_force(make, monkeypatch)
    args = (bw.willitems, len(bw.heirs), bw.will_settings, True)
    still = bw._still_active_for_summary()
    assert still
    summary = will_summary(*args, now=s.clock.now, still_active=still,
                           long_dates=True)
    assert summary["state"] == "to_sign"
    rows = {r["label"]: r for r in summary["rows"]}
    labels = [r["label"] for r in summary["rows"]]
    assert labels[:3] == ["Delivery to the heirs", "In force now", "After signing"]
    assert rows["Delivery to the heirs"]["strong"] == "23 September 2031, 00:00 UTC"
    assert rows["In force now"]["strong"] == "23 September 2032, 00:00 UTC"
    assert rows["After signing"]["text"] == "→ delivery moved earlier by 1 year"
    assert summary["note"] == (
        "Until you sign and send it, the will sent before stays in force: "
        "delivery on 23 September 2032, 00:00 UTC.")
    old = will_summary(*args, now=s.clock.now, still_active=still)
    assert [r["label"] for r in old["rows"]][0] == "Delivery to the heirs"
    assert "In force now" not in [r["label"] for r in old["rows"]]
    assert old["rows"][0]["strong"] == "2031-09-23"


def test_delivery_change_texts():
    """Earlier, later (with the invalidation transaction) and the same date; the
    span is calendar months (2 years and 4 months)."""
    from bal.core import datetext

    in_force = fk.ts(2031, 2, 26)
    assert datetext.delivery_change(in_force, fk.ts(2028, 10, 13)) == (
        "moved earlier by 2 years and 4 months")
    assert datetext.delivery_change(in_force, fk.ts(2031, 5, 26)) == (
        "moved later by 3 months — needs an invalidation transaction on the "
        "blockchain: your bitcoin stay yours, you only pay the normal network "
        "fee")
    assert datetext.delivery_change(in_force, in_force) == "same date"
    assert datetext.utc_text(fk.ts(2028, 10, 13)) == "13 October 2028, 00:00 UTC"
    assert datetext.utc_text(fk.ts(2028, 10, 13), short=True) == "13 Oct 2028, 00:00 UTC"
    assert datetext.local_line(fk.ts(2028, 10, 13), ROME) == (
        "In your time zone: 13 October 2028, 02:00")


def test_same_date_shows_no_comparison(make):
    """A will in force and a choice with the SAME date: page 1 and page 4
    show no comparison (owner, ZIP test); ``in_force_change_lines`` is
    empty, two lines when the dates differ."""
    from bal.core import datetext

    s, bw = make(mode="basic", check=True)
    dialog = open_wizard(bw)
    try:
        page = dialog.widget
        assert page.chips["keep"].isChecked() and page.chosen() == page.in_force
        assert page.compare_label.isHidden()
        to_executors(dialog)
        page = press_next(dialog)
        assert "In force now" not in page.values["Delivery to the heirs"].text()
    finally:
        dialog.close()
    assert datetext.in_force_change_lines(fk.D0, fk.D0) == []
    assert datetext.in_force_change_lines(None, fk.D0) == []
    assert datetext.in_force_change_lines(fk.D0, fk.LATER) == [
        "In force now: 23 September 2032, 00:00 UTC",
        "After signing: 23 September 2033, 00:00 UTC → delivery moved later by "
        "1 year — needs an invalidation transaction on the blockchain: your "
        "bitcoin stay yours, you only pay the normal network fee",
    ]


@pytest.mark.parametrize("mode", ["basic", "advanced"])
@pytest.mark.parametrize("stored", [False, True])
def test_no_executor_option_hidden_in_basic(make, mode, stored):
    """#134 (owner): "Add transactions without willexecutor" is hidden in
    BASIC (wizard page 3 and the Tools window) and shown in ADVANCED; the
    stored value is never changed."""
    from bal.gui.qt.dialogs import WillExecutorDialog

    s, bw = make(mode=mode)
    s.plugin.NO_WILLEXECUTOR.set(stored)
    window = WillExecutorDialog(bw)
    dialog = open_wizard(bw)
    try:
        rows = [window.will_executor_list_widget.no_executor_row]
        if mode == "basic":
            rows.append(to_executors(dialog).we_widget.no_executor_row)
        for row in rows:
            assert row.isHidden() is (mode == "basic")
        assert bool(s.plugin.NO_WILLEXECUTOR.get()) is stored
    finally:
        dialog.close()
        window.close()


@pytest.mark.parametrize("cosigners, words", [
    (1, "the keys are on the offline computer"),
    (3, "made on the cosigners' computers")])
def test_watch_only_summary_says_where_the_will_goes(make, monkeypatch,
                                                    cosigners, words):
    """#161 (owner): on a watch-only wallet the last page says, in a teal
    frame, that the will is signed elsewhere and that the window "Sign the
    will offline" opens after "Create the will"; then it opens it."""
    s, bw = make(mode="basic")
    monkeypatch.setattr(bw.wallet, "is_watching_only", lambda: True)
    monkeypatch.setattr(bw.wallet, "n", cosigners, raising=False)
    dialog = open_wizard(bw)
    try:
        to_executors(dialog)
        page = press_next(dialog)
        note = page.offline_note
        assert words in note.text()
        assert "«Sign the will offline»" in note.text()
        assert "#0D9488" in note.styleSheet()
        texts = [label.text() for label in page.findChildren(type(note))]
        assert not any("asks for the password" in t for t in texts)
        built, opened = [], []
        monkeypatch.setattr(dialogs_mod, "BalBuildWillDialog",
                            lambda bw: type("D", (), {
                                "build_will_task": lambda self: built.append(1)})())
        monkeypatch.setattr(bw, "after_wizard_create", lambda: opened.append(1))
        page.next_button.click()
        assert built == [1] and opened == [1]
    finally:
        dialog.close()
