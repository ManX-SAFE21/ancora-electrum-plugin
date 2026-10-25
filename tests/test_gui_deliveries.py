"""The window "Inheritance in several deliveries" (#170, M4a).

Owner, 2026-10-20 (the M4 plan, three drafts of the window, answers 1-6):

* the table is horizontal: the deliveries from left to right, the heirs in
  rows, the shares big, the check sums small; with 3 deliveries everything
  fits, with more (one day up to 12) only the deliveries scroll sideways,
  the names and the totals stay in place;
* the same rules as the command line, one problem at a time, translated;
  Save only without a problem;
* owner decision (a): anything that makes the heirs receive part of the
  estate earlier (a date, shares moved earlier, the table removed) is asked
  first; a date later than the will in force is said;
* opened from the wizard ("Several deliveries", then "Edit the
  deliveries…") and from Tools in the Will tab, ADVANCED only (D5);
* with a table the date fields are read-only, "10/03/2030 (+2 deliveries)"
  (answer 3); an heir renamed keeps its shares (answer 4); an heir added
  or removed asks to open the table (owner decision 3 of M3);
* the Check and the Will Details never show the row names "luca§2".

The REAL ``BalWindow`` on a ``tests/flowkit.py`` wallet: three fake
will-executors with their own fee addresses, the test password, a fixed
clock (2026-10-10). The Yes / No questions are answered by the test
(``deliveries.ask``).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_deliveries.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget
from test_gui_window_init_migration import real_window, toolbars

from bal.core import delivery
from bal.core.heirs import Heirs
from bal.engine import context, instalments
from bal.gui.qt import deliveries
from bal.gui.qt import window as window_mod
from bal.gui.qt.dialogs import person_renames
from bal.gui.qt.widgets import WillSettingsWidget, WillWidget

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D1 = fk.ts(2030, 3, 10)
D2 = fk.ts(2032, 3, 10)
D3 = fk.ts(2035, 3, 10)
ADDR = {"luca": fk.address(20), "john": fk.address(21),
        "maria": fk.address(22), "marco": fk.address(20)}
OWNER_ROWS = [
    {"locktime": D1, "shares": {"luca": "10%", "john": "10%"}},
    {"locktime": D2, "shares": {"luca": "10%", "john": "10%"}},
    {"locktime": D3, "shares": {"luca": "30%", "john": "30%"}},
]


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions: ADVANCED, three will-executors, synchronized, the
    heirs luca and john (their amounts are not used with a table)."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session; ``fk.Session`` arguments."""
        kw.setdefault("mode", "advanced")
        kw.setdefault("locktime", D1)
        s = fk.Session(monkeypatch, password=TEST_PASSWORD, fee=2, **kw)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        people(s.bw, "luca", "john")
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


@pytest.fixture
def answers(monkeypatch):
    """The owner's answers to the Yes / No questions: ``answers.queue`` (True
    = Yes; empty = No); every question asked is kept in ``answers.asked``."""

    class Answers:
        queue = []
        asked = []

    def ask(parent, text):
        """The question, answered from the queue."""
        Answers.asked.append(text)
        return Answers.queue.pop(0) if Answers.queue else False

    monkeypatch.setattr(deliveries, "ask", ask)
    return Answers


def people(bw, *names):
    """Replace the heirs by one row per person (saved in the wallet)."""
    for name in list(bw.heirs.keys()):
        del bw.heirs[name]
    bw.heirs.save()
    for name in names:
        bw.heirs[name] = [ADDR[name], "50%", D1]
    bw.heirs.save()


def window(s, monkeypatch):
    """The real window; its messages are kept in ``bw.shown``."""
    monkeypatch.setattr(s.plugin, "get_window_title", lambda t: t, raising=False)
    bw = real_window(s, monkeypatch)
    bw.shown = []
    monkeypatch.setattr(bw, "show_message", lambda text, *a, **k: bw.shown.append(text))
    monkeypatch.setattr(bw, "show_error", lambda text, *a, **k: bw.shown.append(text))
    return bw


def owner_table(bw, rows=OWNER_ROWS):
    """Store the owner's table through the engine's one write."""
    context.set_instalments(bw, [dict(r, shares=dict(r["shares"])) for r in rows])
    bw.refresh_setting_fields()


def stored(bw):
    """The stored table (delivery 1 on the wallet's date)."""
    return delivery.table_rows(bw.will_settings)


def texts(editor):
    """The shown share of every cell: ``[{name: text}]``."""
    return [{n: c.text() for n, c in cells.items()} for cells in editor.cells]


def field_text(toolbar):
    """The read-only text of a date field, or None when the editor shows."""
    field = toolbar.widgets["locktime"]
    if field.period_label.isHidden():
        return None
    return field.period_label.text()


# ------------------------------------------------------------ the window

def test_without_a_table_the_window_starts_from_the_example(make, monkeypatch, answers):
    """33 / 33 / 34 % from the wallet's date, one year apart; Save stores
    it (nothing arrives earlier: no question); the date fields then show
    "10/03/2030 (+2 deliveries)", read-only."""
    s = make()
    bw = window(s, monkeypatch)
    ed = deliveries.DeliveriesEditor(bw)
    assert ed.names == ["luca", "john"]
    assert texts(ed) == [{"luca": "16.5", "john": "16.5"},
                         {"luca": "16.5", "john": "16.5"},
                         {"luca": "17", "john": "17"}]
    assert "example" in ed.top_note.text() and ed.one_delivery_button.isHidden()
    assert ed.save_button.isEnabled() and ed.problem.isHidden()
    # #188: the first table of a wallet asks to think it over (Yes).
    answers.queue.append(True)
    ed._save()
    assert [q for q in answers.asked if "Think it over" not in q] == []
    assert ed.result_kind == "saved"
    assert stored(bw) == delivery.default_instalments(["luca", "john"], D1)
    assert bw.will_settings["locktime"] == D1
    for toolbar in toolbars(bw):
        assert field_text(toolbar) == "10/03/2030 (+2 deliveries)"
        assert "Inheritance in several deliveries" in (
            toolbar.widgets["locktime"].period_label.toolTip())


def test_the_stored_table_is_shown_and_fits_without_scrolling(make, monkeypatch):
    """The owner's table: the cells, the totals of each delivery and heir,
    100 % in green; the three deliveries fit: no bar, every column shown."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    assert texts(ed) == [{"luca": "10", "john": "10"}, {"luca": "10", "john": "10"},
                         {"luca": "30", "john": "30"}]
    assert [label.text() for label in ed.delivery_totals] == ["20%", "20%", "60%"]
    assert {n: label.text() for n, label in ed.heir_totals.items()} == {
        "luca": "50%", "john": "50%"}
    assert ed.grand_total.text() == "100% ✓"
    assert [label.text() for label in ed.gap_labels] == ["", "+2 years", "+3 years"]
    assert ed.top_note.isHidden() and not ed.one_delivery_button.isHidden()
    ed.show()
    _APP.processEvents()
    assert ed.scroll.horizontalScrollBar().maximum() == 0
    assert ed.scroll.width() >= ed.middle_box.width()
    ed.close()


def test_more_deliveries_scroll_sideways_names_and_totals_stay(make, monkeypatch):
    """Owner (the future 12 deliveries): with more columns than fit, only
    the deliveries scroll; the names and the totals are outside the
    scrolling part. Simulated with a maximum of 6 (the rules stay at 3)."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    monkeypatch.setattr(delivery, "INSTALMENTS_MAX", 6)
    ed = deliveries.DeliveriesEditor(bw)
    for _i in range(3):
        ed._add_delivery()
    assert len(ed.cells) == 6 and not ed.add_button.isEnabled()
    ed.show()
    _APP.processEvents()
    assert ed.scroll.horizontalScrollBar().maximum() > 0
    names = [w for w in ed.findChildren(QLabel) if w.text() == "luca"]
    assert names and not any(ed.scroll.isAncestorOf(w) for w in names)
    assert not ed.scroll.isAncestorOf(ed.grand_total)
    assert ed.scroll.isAncestorOf(ed.cells[5]["luca"])
    ed.close()


@pytest.mark.parametrize("change, problem", [
    (lambda ed: ed.date_editors[1].setDate(QDate(2030, 3, 20)),
     "Delivery 2: less than 28 days after delivery 1"),
    (lambda ed: ed.cells[0]["luca"].setText("abc"),
     "Delivery 1: luca: not a share (0 to 100, two decimals at most): 'abc'"),
    (lambda ed: ed.cells[2]["john"].setText("25"),
     "The shares add up to 95%, not 100%"),
    (lambda ed: ed.cells[0]["luca"].setText("10.001"),
     "Delivery 1: luca: not a share (0 to 100, two decimals at most): '10.001'"),
])
def test_one_problem_at_a_time_from_the_rules(make, monkeypatch, change, problem):
    """The first broken rule, in words, from ``validate_instalments`` (the
    command line's rules); Save is off until it is fixed."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    change(ed)
    assert ed.problem.text() == problem
    assert not ed.save_button.isEnabled()


def test_a_decimal_comma_and_an_empty_cell_are_read(make, monkeypatch, answers):
    """"10,5" is 10.5 (the Italian comma); an empty cell is 0 %."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    ed.cells[1]["luca"].setText("10,5")
    ed.cells[1]["john"].setText("9,5")
    ed.cells[2]["luca"].setText("")
    ed.cells[2]["john"].setText("60")
    assert ed.problem.isHidden() and ed.save_button.isEnabled()
    ed._save()
    assert [row["shares"] for row in stored(bw)] == [
        {"luca": "10%", "john": "10%"}, {"luca": "10.5%", "john": "9.5%"},
        {"luca": "0%", "john": "60%"}]


def test_add_and_remove_a_delivery(make, monkeypatch):
    """At least 2 deliveries, at most 3 today; a new one comes 12 months
    after the last, with 0 % for everyone."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    assert not ed.add_button.isEnabled() and ed.remove_last_button.isEnabled()
    ed._remove_last()
    assert len(ed.cells) == 2 and not ed.remove_last_button.isEnabled()
    assert ed.add_button.isEnabled()
    ed._add_delivery()
    assert ed.dates[2] == delivery.add_months(D2, 12)
    assert texts(ed)[2] == {"luca": "0", "john": "0"}


# ------------------------------------------------- earlier: owner decision (a)

def test_an_earlier_date_is_asked_first(make, monkeypatch, answers):
    """Delivery 2 a year earlier: the question names it; No stores
    nothing, Yes stores it (00:00 UTC of the day chosen)."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    ed.date_editors[1].setDate(QDate(2031, 3, 10))
    ed._save()
    assert len(answers.asked) == 1
    assert "Delivery 2: 10/03/2031 instead of 10/03/2032." in answers.asked[0]
    assert "Save the table?" in answers.asked[0]
    assert stored(bw)[1]["locktime"] == D2 and ed.result_kind is None
    answers.queue.append(True)
    ed._save()
    assert stored(bw)[1]["locktime"] == fk.ts(2031, 3, 10)
    assert ed.result_kind == "saved"


def test_shares_moved_earlier_are_asked_first(make, monkeypatch, answers):
    """30 % each at delivery 1 instead of 10 %: by 10/03/2030 the heirs
    would receive 60 % instead of 20 %."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    for name in ("luca", "john"):
        ed.cells[0][name].setText("30")
        ed.cells[2][name].setText("10")
    ed._save()
    assert len(answers.asked) == 1
    assert ("By 10/03/2030 the heirs would receive 60% of the estate instead "
            "of 20%.") in answers.asked[0]
    assert stored(bw)[0]["shares"] == {"luca": "10%", "john": "10%"}


def test_a_later_date_is_not_asked(make, monkeypatch, answers):
    """Delivery 3 a year later: nothing arrives earlier, no question."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    ed.date_editors[2].setDate(QDate(2036, 3, 10))
    ed._save()
    assert answers.asked == []
    assert stored(bw)[2]["locktime"] == fk.ts(2036, 3, 10)


def test_a_date_later_than_the_will_in_force_is_said(make, monkeypatch):
    """With a will in force (its dates per delivery), a later delivery 2
    says that the next Check asks for the paid invalidation (D6)."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    in_force = {1: D1, 2: D2, 3: D3}
    real = instalments.dates_in_force

    def dates_in_force(host, now=None):
        """The stored table's dates, as if its will were in force."""
        _in_force, reference, old = real(host, now)
        return in_force, reference, old

    monkeypatch.setattr(deliveries.instalments, "dates_in_force", dates_in_force)
    ed = deliveries.DeliveriesEditor(bw)
    assert ed.later_note.isHidden()
    ed.date_editors[1].setDate(QDate(2033, 3, 10))
    assert ed.later_note.text() == (
        "Delivery 2 is later than the will in force (10/03/2032): at the next "
        "Check the old will must be invalidated first (on-chain, miner fee).")


def test_back_to_one_delivery_is_asked_and_removes_the_table(make, monkeypatch, answers):
    """The rest of the estate would arrive at delivery 1: asked first (by
    how much, and that the heirs' amounts count again); No keeps the
    table, Yes removes it and the date editor is back."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    ed._back_to_one()
    assert len(answers.asked) == 1
    question = answers.asked[0]
    assert "receive the whole estate on 10/03/2030, the date of delivery 1. Today they receive 20%" in question
    assert "the amounts of the heirs count again" in question
    assert stored(bw) and ed.result_kind is None
    answers.queue.append(True)
    ed._back_to_one()
    assert stored(bw) == [] and ed.result_kind == "removed"
    assert bw.will_settings["locktime"] == D1
    for toolbar in toolbars(bw):
        assert field_text(toolbar) is None


def test_heirs_changed_since_the_table(make, monkeypatch):
    """An heir added: a column of 0 % and the note to check the shares (the
    rules then say what to fix); an heir removed: its shares are gone."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    bw.heirs["maria"] = [ADDR["maria"], "10%", D1]
    ed = deliveries.DeliveriesEditor(bw)
    assert ed.names == ["luca", "john", "maria"]
    assert [cells["maria"].text() for cells in ed.cells] == ["0", "0", "0"]
    assert "The heirs changed since the table was saved" in ed.top_note.text()
    assert ed.problem.text() == "maria: gets nothing at any delivery (remove the heir)"
    del bw.heirs["maria"]
    del bw.heirs["john"]
    ed = deliveries.DeliveriesEditor(bw)
    assert ed.names == ["luca"] and ed.grand_total.text() == "50%"
    assert ed.problem.text() == "The shares add up to 50%, not 100%"


# ------------------------------------------------------------ where it opens

def wizard_form(bw):
    """The wizard's delivery form (the settings page, editable)."""
    return WillSettingsWidget(bw, None, "v", read_only=False).form_box


def test_the_wizard_offers_several_deliveries_in_advanced_only(make, monkeypatch):
    """ADVANCED: "Several deliveries" next to "Fixed date" / "Period",
    enabled with a fixed date, not with the period form; BASIC: hidden."""
    s = make()
    bw = window(s, monkeypatch)
    box = wizard_form(bw)
    assert not box.staged_radio.isHidden() and box.staged_radio.isEnabled()
    assert box.fixed_radio.isChecked() and box.staged_button.isHidden()
    context.set_delivery(bw, form=delivery.FORM_PERIOD, months=24)
    box = wizard_form(bw)
    assert not box.staged_radio.isEnabled()
    context.set_delivery(bw, form=delivery.FORM_FIXED)
    s.plugin.USER_TYPE.set("basic")
    box = wizard_form(bw)
    assert box.staged_radio.isHidden() and box.staged_button.isHidden()


def test_the_wizard_choice_opens_the_window_and_follows_it(make, monkeypatch, answers):
    """"Several deliveries": the window (Cancel keeps "Fixed date"; Save
    selects it and shows "Edit the deliveries…", the period off); "Fixed
    date" with a table asks to go back to one delivery."""
    s = make()
    bw = window(s, monkeypatch)
    opened = []

    def opener(parent=None, save=False):
        """The window: closed (Cancel), or the owner's table saved."""
        opened.append(parent)
        if save:
            owner_table(bw)

    monkeypatch.setattr(bw, "open_deliveries_editor", opener)
    box = wizard_form(bw)
    box.staged_radio.click()
    assert len(opened) == 1 and box.fixed_radio.isChecked()
    monkeypatch.setattr(bw, "open_deliveries_editor",
                        lambda parent=None: opener(parent, save=True))
    box.staged_radio.click()
    assert box.staged_radio.isChecked() and not box.staged_button.isHidden()
    assert not box.period_radio.isEnabled()
    box.fixed_radio.click()
    assert len(answers.asked) == 1 and box.staged_radio.isChecked()
    answers.queue.append(True)
    box.fixed_radio.click()
    assert stored(bw) == [] and box.fixed_radio.isChecked()
    assert box.staged_button.isHidden()


def test_the_tools_entry_is_advanced_only(make, monkeypatch):
    """Tools in the Will tab: "Inheritance in several deliveries…",
    shown in ADVANCED, hidden in BASIC; it opens the window."""
    s = make()
    bw = window(s, monkeypatch)
    lst = bw.will_list_widget
    action = lst.deliveries_action
    assert action.text() == "Inheritance in several deliveries…"
    assert action in lst.menu.actions() and action.isVisible()
    opened = []
    monkeypatch.setattr(bw, "open_deliveries_editor", lambda: opened.append(1))
    action.trigger()
    assert opened == [1]
    s.plugin.USER_TYPE.set("basic")
    lst._show_deliveries_action()
    assert not action.isVisible()


@pytest.mark.parametrize("setup, refusal", [
    (lambda s, bw: s.plugin.USER_TYPE.set("basic"), "ADVANCED mode only"),
    (lambda s, bw: context.set_delivery(bw, form=delivery.FORM_PERIOD, months=24),
     "renewable period"),
    (lambda s, bw: people(bw), "There are no heirs"),
])
def test_the_window_is_refused_with_a_reason(make, monkeypatch, setup, refusal):
    """BASIC, the period form or no heirs: a message, no window."""
    s = make()
    bw = window(s, monkeypatch)
    setup(s, bw)
    monkeypatch.setattr(deliveries, "DeliveriesEditor",
                        lambda *a, **k: pytest.fail("window opened"))
    assert bw.open_deliveries_editor() is None
    assert len(bw.shown) == 1 and refusal in bw.shown[0]


def test_open_deliveries_editor_returns_what_the_window_did(make, monkeypatch, answers):
    """The window opens on the wallet's table; its result is returned."""
    s = make()
    bw = window(s, monkeypatch)
    answers.queue.append(True)                  # #188: the first table's warning
    monkeypatch.setattr(deliveries.DeliveriesEditor, "exec",
                        lambda self: self._save())
    assert bw.open_deliveries_editor() == "saved"
    assert len(stored(bw)) == 3


def test_a_two_delivery_table_reads_plus_one(make, monkeypatch):
    """"10/03/2030 (+1 delivery)" with two deliveries."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw, [OWNER_ROWS[0], dict(OWNER_ROWS[1], shares={
        "luca": "40%", "john": "40%"})])
    assert [field_text(t) for t in toolbars(bw)] == ["10/03/2030 (+1 delivery)"] * 2


# ------------------------------------------------------------ the heirs

def scripted_heir_dialog(monkeypatch, name=None):
    """The heir window's ``exec``: type ``name``, OK once; the labels shown
    (not hidden) are kept in the returned list."""
    calls = {"n": 0}
    seen = []

    def fake_exec(self):
        """OK once (then Cancel)."""
        if calls["n"]:
            return False
        calls["n"] += 1
        if name is not None:
            self.findChildren(QLineEdit)[0].setText(name)
        seen.extend(w.text() for w in self.findChildren(QLabel)
                    if w.text() and not w.isHidden())
        return True

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)
    return seen


def test_a_rename_keeps_the_shares_in_the_table(make, monkeypatch, answers):
    """Owner answer 4: luca renamed marco in the heir window; the table
    follows, still fits the heirs, nothing is asked; the window said that
    the amount is not used with a table."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    seen = scripted_heir_dialog(monkeypatch, "marco")
    bw.new_heir_dialog("luca")
    assert set(Heirs(s.wallet)) == {"marco", "john"}
    assert [row["shares"] for row in stored(bw)] == [
        {"marco": "10%", "john": "10%"}, {"marco": "10%", "john": "10%"},
        {"marco": "30%", "john": "30%"}]
    assert instalments.check_table(bw) and answers.asked == []
    assert any("this amount is not used" in text for text in seen)


def test_the_rename_rule_of_the_engine(make, monkeypatch):
    """``instalments.rename_heir``: nothing without a table, for a name
    not in it, or onto a name already in it."""
    s = make()
    bw = window(s, monkeypatch)
    assert instalments.rename_heir(bw, "luca", "marco") is False
    owner_table(bw)
    assert instalments.rename_heir(bw, "nobody", "marco") is False
    assert instalments.rename_heir(bw, "luca", "john") is False
    assert instalments.rename_heir(bw, "luca", "luca") is False
    assert stored(bw) == delivery.table_rows({"locktime": D1, "instalments": [
        dict(r, shares=dict(r["shares"])) for r in OWNER_ROWS]})


def test_without_a_table_the_heir_window_has_no_note(make, monkeypatch):
    """The note of the table only with a table."""
    s = make()
    bw = window(s, monkeypatch)
    seen = scripted_heir_dialog(monkeypatch)
    bw.new_heir_dialog("luca")
    assert not any("this amount is not used" in text for text in seen)


def test_an_heir_added_asks_to_open_the_table(make, monkeypatch, answers):
    """Owner decision 3 of M3: the table no longer fits; asked once (Yes
    opens the window); nothing asked when the heirs did not change, in
    BASIC, or without a table."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    opened = []
    monkeypatch.setattr(bw, "open_deliveries_editor", lambda *a, **k: opened.append(1))
    before = bw._heirs_snapshot()
    assert bw.offer_table_update(before) is None and answers.asked == []
    bw.heirs["maria"] = [ADDR["maria"], "10%", D1]
    answers.queue.append(True)
    bw.offer_table_update(before)
    assert len(answers.asked) == 1 and opened == [1]
    assert "the table of the deliveries must be updated" in answers.asked[0]
    # The table no longer fits, but this change did not touch the heirs
    # (e.g. the heir window closed with Cancel): nothing asked.
    assert bw.offer_table_update(bw._heirs_snapshot()) is None
    assert len(answers.asked) == 1
    s.plugin.USER_TYPE.set("basic")
    bw.offer_table_update(before)
    assert len(answers.asked) == 1


def test_the_heir_window_closed_unchanged_asks_nothing(make, monkeypatch, answers):
    """A table that already does not fit (maria added before) and the heir
    window opened and closed without a change: no question."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    bw.heirs["maria"] = [ADDR["maria"], "10%", D1]
    monkeypatch.setattr(window_mod.BalDialog, "exec", lambda self: False)
    bw.new_heir_dialog("luca")
    assert answers.asked == []


def test_the_transactions_to_sign_are_counted(make, monkeypatch):
    """The grey line: 3 will-executors and 3 deliveries, 39 transactions;
    with the copy without a will-executor, 4 copies of each: 84."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    ed = deliveries.DeliveriesEditor(bw)
    assert "With 3 will-executors: 39 transactions to sign (3 + 9 + 27)." in (
        ed.info.text())
    assert "Each will-executor takes its fee at every delivery." in ed.info.text()
    ed._remove_last()
    assert "With 3 will-executors: 12 transactions to sign (3 + 9)." in ed.info.text()
    s.plugin.NO_WILLEXECUTOR.set(True)
    ed = deliveries.DeliveriesEditor(bw)
    assert ("With 3 will-executors and the copy without a will-executor: 84 "
            "transactions to sign (4 + 16 + 64).") in ed.info.text()


def test_deleting_an_heir_in_the_list_asks_to_open_the_table(make, monkeypatch, answers):
    """The Heirs list's Delete: john removed, the table no longer fits."""
    s = make()
    bw = window(s, monkeypatch)
    owner_table(bw)
    bw.heir_list_widget.delete_heirs(["john"])
    assert len(answers.asked) == 1


def test_prepare_with_a_table_that_no_longer_fits(make, monkeypatch, answers):
    """ADVANCED: the message and "Open the table of the deliveries now?";
    BASIC: the message only."""
    s = make()
    bw = window(s, monkeypatch)
    opened = []
    monkeypatch.setattr(bw, "open_deliveries_editor", lambda *a, **k: opened.append(1))
    answers.queue.append(True)
    bw.table_mismatch("The table of the deliveries no longer matches the heirs.")
    assert answers.asked == ["The table of the deliveries no longer matches the "
                             "heirs.\n\nOpen the table of the deliveries now?"]
    assert opened == [1]
    s.plugin.USER_TYPE.set("basic")
    bw.table_mismatch("no longer matches")
    assert bw.shown == ["no longer matches"] and len(answers.asked) == 1


# ------------------------------------------------- never the row names "§"

def staged_will(s):
    """The owner's table on the flowkit window, then a Check (3 + 9 + 27)."""
    people(s.bw, "luca", "john")
    s.set_instalments((D1, {"luca": "10%", "john": "10%"}),
                      (D2, {"luca": "10%", "john": "10%"}),
                      (D3, {"luca": "30%", "john": "30%"}))
    return s.check()


def history(dialog):
    """Every row the Check wrote, as plain text."""
    return [fk.strip(str(r)) for r in dialog.history]


def test_prepare_asks_to_open_the_table(make):
    """The Will tab's Prepare (the flows' window): the table no longer fits
    (maria added), nothing is prepared, the question carries the message
    and "Open the table of the deliveries now?"; the Check stopped by it
    offers the button."""
    s = make()
    staged_will(s)
    s.bw.heirs["maria"] = [ADDR["maria"], "10%", D1]
    s.prepare()
    questions = [text for kind, text in s.bw.popups if kind == "question"]
    assert len(questions) == 1
    assert "no longer matches the heirs" in questions[0]
    assert questions[0].endswith("Open the table of the deliveries now?")
    dlg = s.check()
    assert "button: Open the table of the deliveries" in dlg.events


def test_the_check_names_the_persons(make):
    """The Check's "Heir" rows: each person once, never "luca§2"."""
    s = make()
    rows = history(staged_will(s))
    assert not any(delivery.INSTALMENT_SEP in row for row in rows)
    heirs = [row for row in rows if row.startswith("Heir")]
    assert sum("luca" in row for row in heirs) == 1
    assert sum("john" in row for row in heirs) == 1


def test_a_rename_is_said_once_with_the_persons_names(make):
    """luca -> marco in a will in deliveries: the Check says it once,
    "luca → marco", never per delivery with the row names."""
    s = make()
    staged_will(s)
    del s.bw.heirs["luca"]
    s.bw.heirs["marco"] = [ADDR["marco"], "50%", D1]
    s.bw.heirs.save()
    assert instalments.rename_heir(s.bw, "luca", "marco")
    dlg = s.check()
    assert sorted(s.bw.heir_renames) == [
        ("luca", "marco"), ("luca§2", "marco§2"), ("luca§3", "marco§3")]
    changes = dlg._report().snapshot()["boxes"]["changes"]
    assert changes == ["Heir renamed: luca → marco: only the name changes, the "
                       "will stays the same."]
    assert not any(delivery.INSTALMENT_SEP in row for row in history(dlg))


def test_person_renames():
    """The rows of one person are one rename; order kept; no table: as is."""
    assert person_renames([("luca", "marco"), ("luca§2", "marco§2"),
                           ("john§3", "jon§3")]) == [("luca", "marco"), ("john", "jon")]
    assert person_renames([("a", "b")]) == [("a", "b")]


def test_the_will_details_name_the_delivery(make, monkeypatch):
    """Display > the will items: "luca (delivery 2)", never "luca§2"."""
    s = make()
    staged_will(s)
    bw = window(s, monkeypatch)
    holder = QWidget()
    holder.bal_window = bw
    holder.decimal_point = 8
    holder.base_unit_name = "BTC"
    widget = WillWidget(None, holder, s.bw.willitems)
    shown = [w.text() for w in widget.findChildren(QLabel)]
    assert any("luca (delivery 2)" in t for t in shown)
    assert any("luca (delivery 1)" in t for t in shown)
    assert not any(delivery.INSTALMENT_SEP in t for t in shown)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
