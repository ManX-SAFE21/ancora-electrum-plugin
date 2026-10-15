"""The will message (OP_RETURN): a button, 80 bytes, one per will (#140).

Owner's plan (2026-10-15):

* a "✉ A message in the will" button on page 2 of the BASIC wizard and in
  the Heirs tab (Advanced): a window that asks only the text; the plugin
  names a new message "OP_RETURN message" (a plain stored name, "... 2"
  when a payee already has it); an existing message keeps its name and the
  button opens it again;
* an 80-byte counter: bytes, not letters ("è" 2, an emoji 4); a longer
  text is cut, never inside a character; also in the Advanced OP_RETURN
  mode of the "New heir" window;
* a fixed warning (public forever, the will-executors read it before the
  delivery) and a "?" of its own;
* ONE message per will: a second one is refused in the windows, the
  command line and the imports (two OP_RETURN outputs in one transaction
  are not relayed by nodes before Bitcoin Core 30); an old wallet with two
  is only told, and its messages can still be edited;
* the message is not an heir: with only a message the wizard's Next stays
  off; page 4 shows it in a row of its own; the "What changes" box shows
  it escaped; the PDF has a "MESSAGE IN THE WILL" section;
* the Heirs list shows "✉ name" (display only); its menu uses the stored
  name.

The REAL ``BalWindow`` dialogs are used (only ``exec`` is scripted) on
flowkit wallets with three fake will-executors, each with its own fee
address; after a new message, a Check then a second Check clean, and every
will transaction carries exactly one OP_RETURN output with the text.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_will_message.py
"""

import json
import os
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton

from bal.core import heirs as heirs_mod
from bal.core import wizard_date
from bal.core.heirs import (
    MESSAGE_MAX_BYTES,
    Heirs,
    SecondMessageNotAllowed,
    automatic_message_name,
    check_one_message,
    create_op_return_script,
    fit_message,
    keep_one_message,
    message_address,
    message_names,
    message_size,
    message_text,
)
from bal.core.messages import heir_error_message
from bal.gui.qt.widgets import MessageEdit

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
ROME = ZoneInfo("Europe/Rome")
NAME = "OP_RETURN message"
BIRTHDAY = "Buon compleanno, amore mio. Niente anello: parole eterne e bitcoin. Tuo M."
FAMILY = "A Marco e Giulia: tutto quello che ho è vostro. Con amore, papà"
ALREADY = "There is already a message in the will: edit that one."
WARNING = ("The message will be public forever on the blockchain, and the "
           "will-executors can read it before the delivery. Do not write private data.")
HELP_START = "A short text written in the will transaction (OP_RETURN), at most 80 bytes."
MESSAGE_CHANGES = "The message changes: the will must be signed again."
WRITE_THE_MESSAGE = "Write the message."


@pytest.fixture
def make(monkeypatch):
    """A will in force at three will-executors, and the REAL window."""
    from test_gui_window_init_migration import real_window
    fk.ensure_event_loop()
    made = []
    monkeypatch.setattr(wizard_date, "local_zone", lambda: ROME)

    def _make(mode="basic"):
        """``(session, real window)``; ``rbw.errors`` / ``rbw.messages``:
        the error and information boxes shown."""
        s = fk.Session(monkeypatch, mode=mode, password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        assert len({w["address"] for w in s.world.executors.values()}) == 3
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.check()
        monkeypatch.setattr(s.plugin, "get_window_title", lambda t: t, raising=False)
        rbw = real_window(s, monkeypatch)
        rbw.init_will()
        rbw.errors, rbw.messages = [], []
        monkeypatch.setattr(rbw, "show_error", lambda text, *a, **k: rbw.errors.append(text))
        monkeypatch.setattr(rbw, "show_message",
                            lambda text, *a, **k: rbw.messages.append(text))
        return s, rbw

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def script(monkeypatch, steps, accept=True):
    """Script the dialog's ``exec``: run ``steps(dialog)`` and OK (or
    Cancel) the first time; Cancel after that (the window stayed open)."""
    from bal.gui.qt import window as window_mod
    calls = {"n": 0}

    def fake_exec(self):
        """The steps on the real dialog the first time only."""
        calls["n"] += 1
        if calls["n"] > 1:
            return False
        steps(self)
        return accept

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)
    return calls


def field_of(d):
    """The message field of the open dialog ``d``."""
    return d.findChildren(MessageEdit)[0]


def labels(d):
    """The label texts of ``d``."""
    return [w.text() for w in d.findChildren(QLabel)]


def help_texts(d):
    """The texts of the "?" buttons of ``d``."""
    return [str(getattr(b, "help_text", "")) for b in d.findChildren(QToolButton)]


def write_message(monkeypatch, rbw, text):
    """The "✉" window: write ``text``, OK."""
    script(monkeypatch, lambda d: field_of(d).setText(text))
    return rbw.message_dialog()


def check_twice_clean(s):
    """A Check, then a second Check with nothing to say or ask."""
    s.bw.heirs = Heirs(s.wallet)
    s.check()
    s.user.asked.clear()
    snap = s.check()._report().snapshot()
    assert s.user.asked == []
    assert snap["boxes"]["changes"] == [] and "changes" not in snap


def op_returns(tx):
    """The OP_RETURN scripts of ``tx``."""
    return [bytes(o.scriptpubkey) for o in tx.outputs() if bytes(o.scriptpubkey)[:1] == b"\x6a"]


# ------------------------------------------------------------------ core

def test_bytes_not_letters_and_the_cut():
    """The owner's examples: 74 and 65 bytes, kept whole; "è" is 2 bytes,
    an emoji 4; a longer text is cut to 80 bytes, never inside a
    character; the stored address round-trips."""
    assert message_size(BIRTHDAY) == 74 and fit_message(BIRTHDAY) == BIRTHDAY
    assert message_size(FAMILY) == 65 and fit_message(FAMILY) == FAMILY
    assert message_size("è") == 2 and message_size("\U0001F496") == 4
    assert fit_message("a" * 100) == "a" * MESSAGE_MAX_BYTES
    cut = fit_message("a" * 79 + "è")          # 81 bytes: the "è" does not fit
    assert cut == "a" * 79
    cut = fit_message("a" * 78 + "\U0001F496")  # 82 bytes
    assert cut == "a" * 78
    assert message_text(message_address(FAMILY)) == FAMILY
    assert message_address("hi") == "OP_RETURN:6869"
    heirs_mod.validate_op_return_hex(message_address(fit_message("è" * 60))[10:])


def test_automatic_name():
    """"OP_RETURN message"; "... 2" when a payee has it; "... 3" next."""
    a = [fk.HEIR_ADDR["alice"], "50%", 0]
    assert automatic_message_name({}, NAME) == NAME
    assert automatic_message_name({NAME: a}, NAME) == NAME + " 2"
    assert automatic_message_name({NAME: a, NAME + " 2": a}, NAME) == NAME + " 3"


def test_one_message_rule():
    """A second message is refused; a payee never; a message changes its
    text; in an old wallet with two, each may still be edited."""
    a = [fk.HEIR_ADDR["alice"], "50%", 0]
    m1 = [message_address("one"), "0", 0]
    m2 = [message_address("two"), "0", 0]
    heirs = {"alice": a, "m1": m1}
    with pytest.raises(SecondMessageNotAllowed) as info:
        check_one_message(heirs, "m2", m2[0])
    assert info.value.args[0] == "m1"
    assert heir_error_message(info.value) == ALREADY
    with pytest.raises(SecondMessageNotAllowed):
        check_one_message(heirs, "alice", m2[0])     # a payee made a message
    check_one_message(heirs, "bob", fk.HEIR_ADDR["bob"])
    check_one_message(heirs, "m1", m2[0])
    old = {"alice": a, "m1": m1, "m2": m2}
    check_one_message(old, "m2", message_address("new text"))
    assert message_names(old) == ["m1", "m2"]


def test_import_keeps_one_message():
    """An import: the first message of the file when the heirs have none,
    the next ones skipped; with a message already there, none; a file
    message under the same name replaces it."""
    a = [fk.HEIR_ADDR["alice"], "50%", 0]
    m = lambda t: [message_address(t), "0", 0]  # noqa: E731
    kept, skipped = keep_one_message({"alice": a}, {"x": m("1"), "bob": a, "y": m("2")})
    assert list(kept) == ["x", "bob"] and skipped == ["y"]
    kept, skipped = keep_one_message({"alice": a, "old": m("0")}, {"x": m("1")})
    assert kept == {} and skipped == ["x"]
    kept, skipped = keep_one_message({"old": m("0")}, {"old": m("new")})
    assert list(kept) == ["old"] and skipped == []


def test_message_edit_counts_and_cuts():
    """The field: "0 / 80 bytes", then the bytes of the text; a pasted
    text over 80 bytes is cut, the counter says 80."""
    field = MessageEdit()
    assert field.counter.text() == "0 / 80 bytes"
    field.setText(BIRTHDAY)
    assert field.counter.text() == "74 / 80 bytes"
    field.setText("è" * 50)
    assert field.text() == "è" * 40 and field.counter.text() == "80 / 80 bytes"


def test_message_box_two_lines_no_line_breaks():
    """Owner's ZIP test: the field is a box that wraps; 80 characters fit
    in its two lines, with no scroll bar; a line break (typed text or a
    paste) becomes a space; Enter is left to the dialog."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QPlainTextEdit

    from bal.gui.qt.common import char_width_in_lineedit
    field = MessageEdit(lines=2)
    assert isinstance(field, QPlainTextEdit)
    field.setFixedWidth(48 * char_width_in_lineedit())
    field.setText("one\ntwo\r\nthree")
    assert field.text() == "one two three"     # "\r\n" is one line break
    field.setText("a" * 39 + " " + "b" * 40)
    field.show()
    _APP.processEvents()
    layout = field.document().documentLayout()
    lines = sum(field.document().findBlockByNumber(i).layout().lineCount()
                for i in range(field.document().blockCount()))
    assert lines <= 2
    assert layout.documentSize().height() <= field.viewport().height() + 1
    assert field.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    field.keyPressEvent(enter)
    assert not enter.isAccepted() and "\n" not in field.text()
    field.hide()


def test_help_text_one_sentence_per_line():
    """Owner's ZIP test: the "?" text has one sentence per line and the
    two examples on lines of their own."""
    from bal.gui.qt.widgets import message_help_text
    lines = message_help_text().split("\n")
    assert lines[0] == HELP_START
    assert lines[-3] == "Examples:"
    assert lines[-2].startswith("\u201cHappy birthday") and lines[-1].endswith("dad\u201d")


# ----------------------------------------------------------- the ✉ window

def test_new_message_window_and_two_checks_clean(make, monkeypatch):
    """BASIC: the "✉" window asks only the text, with the counter, the
    warning, its "?" and what the name is for; saved as "OP_RETURN
    message" with no amount; the Check prepares the new will with ONE
    OP_RETURN output carrying the text; the second Check is clean."""
    s, rbw = make()
    seen = {}

    def steps(d):
        field = field_of(d)
        seen["edits"] = len(d.findChildren(QLineEdit))
        seen["boxes"] = len(d.findChildren(MessageEdit))
        field.setText(BIRTHDAY)
        seen["labels"] = labels(d)
        seen["helps"] = help_texts(d)

    script(monkeypatch, steps)
    assert rbw.message_dialog() is True
    assert rbw.errors == []
    assert seen["edits"] == 0 and seen["boxes"] == 1  # no name, no address
    assert "74 / 80 bytes" in seen["labels"] and WARNING in seen["labels"]
    assert ('In the heirs list it is called "OP_RETURN message": the name is only '
            'for you, it is not written in the transaction.') in seen["labels"]
    assert any(h.startswith(HELP_START) for h in seen["helps"])
    stored = Heirs(s.wallet)
    assert message_names(stored) == [NAME]
    assert stored[NAME][0] == message_address(BIRTHDAY) and str(stored[NAME][1]) == "0"
    check_twice_clean(s)
    script_hex = create_op_return_script(BIRTHDAY.encode("utf-8").hex())
    # The new will: the items that pay the message (the old will, replaced,
    # has none); one per will-executor, each with exactly ONE OP_RETURN.
    new = [item for item in s.bw.willitems.values() if NAME in (item.heirs or {})]
    assert len({(item.we or {}).get("url") for item in new}) == 3
    for item in new:
        assert op_returns(item.tx) == [script_hex]


def test_the_button_opens_the_same_message(make, monkeypatch):
    """A second press opens the message already there: its text, the
    "message changes" hint on a new text; saved under the same name, no
    second row. The same text: nothing saved."""
    s, rbw = make()
    write_message(monkeypatch, rbw, FAMILY)
    seen = {}

    def steps(d):
        field = field_of(d)
        seen["text"] = field.text()
        seen["hint_before"] = MESSAGE_CHANGES in labels(d)
        field.setText(BIRTHDAY)
        seen["hint_after"] = MESSAGE_CHANGES in labels(d)

    script(monkeypatch, steps)
    assert rbw.message_dialog() is True
    assert seen == {"text": FAMILY, "hint_before": False, "hint_after": True}
    assert message_names(rbw.heirs) == [NAME]
    assert message_text(rbw.heirs[NAME][0]) == BIRTHDAY
    assert write_message(monkeypatch, rbw, BIRTHDAY) is False
    assert rbw.errors == []


def test_empty_message_refused_and_name_taken(make, monkeypatch):
    """An empty text: "Write the message.", nothing stored. A payee named
    "OP_RETURN message": the message is "OP_RETURN message 2"."""
    s, rbw = make()
    calls = script(monkeypatch, lambda d: field_of(d).setText("   "))
    assert rbw.message_dialog() is False
    assert calls["n"] == 2 and rbw.errors == [WRITE_THE_MESSAGE]
    assert message_names(rbw.heirs) == []
    rbw.errors.clear()
    rbw.set_heir([NAME, fk.HEIR_ADDR["carol"], "10%", "0"])
    write_message(monkeypatch, rbw, "hi")
    assert message_names(rbw.heirs) == [NAME + " 2"]
    assert rbw.heirs[NAME][0] == fk.HEIR_ADDR["carol"]


def test_old_wallet_with_two_messages(make, monkeypatch):
    """Two messages (an old wallet): the button opens the first by name
    and edits it; the Heirs list says to delete the others."""
    s, rbw = make()
    rbw.heirs["m1"] = [message_address("one"), "0", rbw.heirs["bob"][2]]
    rbw.heirs["m2"] = [message_address("two"), "0", rbw.heirs["bob"][2]]
    rbw.heir_list_widget.update()
    note = rbw.heir_list_widget.same_address_label
    assert not note.isHidden()
    assert note.text() == ("There are 2 messages in the will: only one is allowed. "
                           "Delete the others (right click, Delete).")
    write_message(monkeypatch, rbw, "uno")
    assert rbw.errors == []
    assert message_text(rbw.heirs["m1"][0]) == "uno"
    assert message_text(rbw.heirs["m2"][0]) == "two"


def test_double_click_on_a_message_opens_the_envelope_window(make, monkeypatch):
    """Owner's ZIP test: a double click (or Edit) on a message opens the
    "✉" window of THAT message, not the heir window with the hexadecimal
    address; in an old wallet with two, the second one is edited."""
    s, rbw = make()
    rbw.heirs["m1"] = [message_address("one"), "0", rbw.heirs["bob"][2]]
    rbw.heirs["m2"] = [message_address("two"), "0", rbw.heirs["bob"][2]]
    seen = {}

    def steps(d):
        seen["text"] = field_of(d).text()
        seen["address_fields"] = len(d.findChildren(QLineEdit))
        field_of(d).setText("due")

    script(monkeypatch, steps)
    w = rbw.heir_list_widget
    w.update()
    model = w.model()
    role = w.ROLE_HEIR_KEY + w.Columns.NAME
    row = next(r for r in range(model.rowCount())
               if model.item(r, w.Columns.NAME).data(role) == "m2")
    w.on_double_click(model.index(row, w.Columns.ADDRESS))
    assert seen == {"text": "two", "address_fields": 0}
    assert message_text(rbw.heirs["m2"][0]) == "due"
    assert message_text(rbw.heirs["m1"][0]) == "one"


def test_heirs_list_tooltip_and_bitcoin_address(make, monkeypatch):
    """Owner's ZIP test: the list shows 40 characters of a message and
    the whole text as the tooltip; the column and the heir window say
    "Bitcoin address"."""
    s, rbw = make()
    write_message(monkeypatch, rbw, BIRTHDAY)
    w = rbw.heir_list_widget
    w.update()
    model = w.model()
    role = w.ROLE_HEIR_KEY + w.Columns.NAME
    row = next(r for r in range(model.rowCount())
               if model.item(r, w.Columns.NAME).data(role) == NAME)
    cell = model.item(row, w.Columns.ADDRESS)
    assert cell.text() == BIRTHDAY[:40] + "\u2026"
    assert cell.toolTip() == BIRTHDAY
    assert model.horizontalHeaderItem(w.Columns.ADDRESS).text() == "Bitcoin address"
    # Owner: one person on "New Heir", as the "✉" on the message button.
    from PyQt6.QtWidgets import QPushButton
    w.create_toolbar(rbw.window.config)
    texts = [b.text() for b in w.findChildren(QPushButton)]
    assert "\U0001F464 New Heir" in texts and "✉ A message in the will" in texts
    seen = {}
    script(monkeypatch, lambda d: seen.update(labels=labels(d)), accept=False)
    rbw.new_heir_dialog()
    assert "Bitcoin address" in seen["labels"] and "Address" not in seen["labels"]


# --------------------------------------------- Advanced "New heir" window

def test_advanced_op_return_mode_counter_warning_and_help(make, monkeypatch):
    """The OP_RETURN mode of the "New heir" window: the counter, the
    warning and the message "?" shown; hidden for an address; an empty
    name gets "OP_RETURN message"; the message field is cut at 80 bytes."""
    s, rbw = make(mode="advanced")
    seen = {}

    def message_widgets(d):
        """Visibility of the counter, the warning and the message "?"."""
        field = field_of(d)
        warning = next(w for w in d.findChildren(QLabel) if w.text() == WARNING)
        helps = [b for b in d.findChildren(QToolButton)
                 if str(getattr(b, "help_text", "")).startswith(HELP_START)]
        assert len(helps) == 1
        return [w.isVisibleTo(d) for w in (field.counter, warning, helps[0])]

    def steps(d):
        edits = d.findChildren(QLineEdit)
        seen["address"] = message_widgets(d)
        edits[1].setText("OP_RETURN:")
        seen["op_return"] = message_widgets(d)
        field_of(d).setText("x" * 90)
        seen["text"] = field_of(d).text()
        seen["counter"] = field_of(d).counter.text()
        edits[1].setText(fk.HEIR_ADDR["carol"])
        seen["back"] = message_widgets(d)
        edits[1].setText(message_address("ciao"))

    script(monkeypatch, steps)
    rbw.new_heir_dialog()
    assert seen["address"] == [False] * 3
    assert seen["op_return"] == [True] * 3
    assert seen["text"] == "x" * 80 and seen["counter"] == "80 / 80 bytes"
    assert seen["back"] == [False] * 3
    assert rbw.errors == []
    assert message_names(rbw.heirs) == [NAME]
    assert message_text(rbw.heirs[NAME][0]) == "ciao"


def test_advanced_second_message_refused(make, monkeypatch):
    """A second message in the "New heir" window: refused with the plain
    sentence, the window stays open, nothing stored; editing the message
    there changes its text."""
    s, rbw = make(mode="advanced")
    write_message(monkeypatch, rbw, "one")
    before = dict(rbw.heirs)

    def steps(d):
        edits = d.findChildren(QLineEdit)
        edits[0].setText("another")
        edits[1].setText(message_address("two"))

    calls = script(monkeypatch, steps)
    rbw.new_heir_dialog()
    assert calls["n"] == 2 and rbw.errors == [ALREADY]
    assert dict(rbw.heirs) == before
    rbw.errors.clear()
    seen = {}

    def edit(d):
        field_of(d).setText("three")
        seen["hint"] = MESSAGE_CHANGES in labels(d)

    script(monkeypatch, edit)
    rbw.new_heir_dialog(NAME)
    assert rbw.errors == [] and seen["hint"]
    assert message_text(rbw.heirs[NAME][0]) == "three"


# ---------------------------------------------------------------- lists

def test_heirs_list_shows_the_envelope_and_deletes_by_name(make, monkeypatch):
    """The Heirs list: "✉ OP_RETURN message" on screen, the plain name in
    the key role, no amount (it receives nothing); its menu "Delete"
    removes the stored name."""
    from bal.gui.qt import lists as lists_mod
    s, rbw = make(mode="advanced")
    write_message(monkeypatch, rbw, "hi")
    w = rbw.heir_list_widget
    w.update()
    model = w.model()
    role = w.ROLE_HEIR_KEY + w.Columns.NAME
    rows = {model.item(r, w.Columns.NAME).data(role): model.item(r, w.Columns.NAME).text()
            for r in range(model.rowCount())}
    assert rows[NAME] == "✉ " + NAME
    assert rows["alice"] == "alice"
    row = next(r for r in range(model.rowCount())
               if model.item(r, w.Columns.NAME).data(role) == NAME)
    alice = next(r for r in range(model.rowCount())
                 if model.item(r, w.Columns.NAME).data(role) == "alice")
    assert model.item(row, w.Columns.AMOUNT).text() == ""
    assert model.item(alice, w.Columns.AMOUNT).text() == "60%"
    w.resize(600, 300)
    w.show()
    _APP.processEvents()
    index = model.index(row, w.Columns.NAME)
    w.selectionModel().select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect
                              | QItemSelectionModel.SelectionFlag.Rows)
    menus = []
    monkeypatch.setattr(lists_mod.QMenu, "exec", lambda self, *a: menus.append(self))
    w.create_menu(w.visualRect(index).center())
    w.hide()
    delete = next(a for a in menus[0].actions() if a.text() == "Delete")
    delete.trigger()
    assert NAME not in Heirs(s.wallet) and "alice" in Heirs(s.wallet)


# ---------------------------------------------------------------- wizard

def wizard(rbw, monkeypatch, s):
    """The BASIC wizard on page 2 (Heirs), network faked."""
    from test_gui_basic_wizard import install_network, open_wizard, press_next

    from bal.gui.qt import basic_wizard
    monkeypatch.setattr(basic_wizard, "time_now", s.clock.time)
    install_network(s, rbw, monkeypatch)
    dialog = open_wizard(rbw)
    return dialog, press_next(dialog)


def test_wizard_message_button_and_not_an_heir(make, monkeypatch):
    """Page 2 has the "✉" button; with only a message Next stays off and
    "Add at least one heir." stays shown; with an heir Next is on."""
    from PyQt6.QtWidgets import QPushButton
    s, rbw = make()
    dialog, page = wizard(rbw, monkeypatch, s)
    try:
        buttons = [b.text() for b in page.findChildren(QPushButton)]
        assert "✉ A message in the will" in buttons
        script(monkeypatch, lambda d: field_of(d).setText("hi"))
        page.add_message()
        assert message_names(rbw.heirs) == [NAME]
        assert page.next_button.isEnabled()
        rbw.delete_heirs(["alice", "bob"])
        page.heir_list_widget.update()
        assert list(rbw.heirs) == [NAME]
        assert not page.next_button.isEnabled() and not page.error_label.isHidden()
        rbw.set_heir(["carol", fk.HEIR_ADDR["carol"], "100%", "0"])
        page.heir_list_widget.update()
        assert page.next_button.isEnabled() and page.error_label.isHidden()
    finally:
        dialog.close()


def test_summary_page_message_row_and_changes_box(make, monkeypatch):
    """Page 4: the message out of "Heirs", in a row of its own (escaped);
    the "What changes" box: "added" with the escaped text."""
    from bal.gui.qt import basic_wizard
    from bal.gui.qt.basic_wizard import SummaryPage
    s, rbw = make()
    text = "a<b>&c"
    write_message(monkeypatch, rbw, text)
    assert SummaryPage._heirs_value(rbw) == "alice 60% · bob 40%"
    assert SummaryPage._message_value(rbw) == "“a&lt;b&gt;&amp;c”"
    wes = basic_wizard.Willexecutors.get_willexecutors(rbw.bal_plugin)
    rows = SummaryPage._changes_rows(rbw, wes)
    assert ("Message", "added: <b>“a&lt;b&gt;&amp;c”</b>", None) in rows
    dialog, page = wizard(rbw, monkeypatch, s)
    try:
        from test_gui_basic_wizard import press_next
        press_next(dialog)
        summary = press_next(dialog)
        assert isinstance(summary, SummaryPage)
        assert summary.values["Message"].text() == "“a&lt;b&gt;&amp;c”"
        assert summary.values["Heirs"].text() == "alice 60% · bob 40%"
    finally:
        dialog.close()


# ------------------------------------------------------- command line, import

def controller(s):
    """A ``BalController`` on the session's wallet."""
    from bal.cli.controller import BalController
    c = BalController.__new__(BalController)
    c.wallet = s.wallet
    c.heirs = Heirs(s.wallet)
    c.plugin = s.plugin
    c.will_settings = s.bw.will_settings
    return c


def test_command_line_one_message(make, tmp_path):
    """``heirs_add``: a second message refused with the plain sentence
    (nothing stored); the message itself may change its text. Import: the
    second message of the file skipped, with the reason."""
    s, rbw = make()
    c = controller(s)
    c.heirs_add("note", message_address("one"), "0")
    with pytest.raises(Exception) as info:
        c.heirs_add("other", message_address("two"), "0")
    assert str(info.value) == ALREADY
    assert "other" not in Heirs(s.wallet)
    c.heirs_update("note", address=message_address("uno"))
    assert message_text(Heirs(s.wallet)["note"][0]) == "uno"
    c.heirs_delete(["note"])
    path = tmp_path / "heirs.json"
    path.write_text(json.dumps({
        "m1": [message_address("first"), "0", "0"],
        "carol": [fk.HEIR_ADDR["carol"], "10%", "0"],
        "m2": [message_address("second"), "0", "0"],
    }), encoding="utf-8")
    out = c.heirs_import(str(path))
    assert out["names"] == ["m1", "carol"]
    assert out["skipped"] == {"m2": ALREADY}
    assert message_names(Heirs(s.wallet)) == ["m1"]


def test_gui_import_skips_a_second_message(make, monkeypatch, tmp_path):
    """The Heirs tab import: the file's message when the will already has
    one is not imported, and a box says so."""
    from bal.gui.qt import window as window_mod
    s, rbw = make()
    write_message(monkeypatch, rbw, "one")
    path = tmp_path / "heirs.json"
    path.write_text(json.dumps({
        "carol": [fk.HEIR_ADDR["carol"], "10%", "0"],
        "imported note": [message_address("two"), "0", "0"],
    }), encoding="utf-8")

    def fake_import(window, title, importer, on_success):
        """Electrum's ``import_meta_gui`` without the file window."""
        importer(str(path))
        on_success()

    monkeypatch.setattr(window_mod, "import_meta_gui", fake_import)
    rbw.import_heirs()
    stored = Heirs(s.wallet)
    assert "carol" in stored and "imported note" not in stored
    assert message_names(stored) == [NAME]
    assert rbw.messages == ["Not imported: imported note. There is already a "
                            "message in the will: only one is allowed."]


# ---------------------------------------------------------------- PDF

def test_pdf_has_the_message(make, monkeypatch):
    """After the Check the will in force carries the message: the report
    data has it and the PDF shows "MESSAGE IN THE WILL", the text in
    quotes and when it becomes public. Without a message: no section."""
    from bal.core import report as core_report
    from bal.gui.qt import report_pages as rp
    s, rbw = make()

    def pages():
        """The PDF texts of the session's will."""
        s.bw.window.config = SimpleNamespace(format_amount_and_units=str)
        r = core_report.will_report(
            s.bw.willitems, s.bw.heirs, s.bw.will_settings, s.plugin.is_basic_mode(),
            wallet=s.wallet, now=s.clock.now,
            still_active=s.bw._still_active_for_summary())
        texts = [t for p in rp.layout_report(r, str) for t in rp.page_texts(p)]
        return r, texts

    r, texts = pages()
    assert r.messages == [] and "MESSAGE IN THE WILL" not in texts
    write_message(monkeypatch, rbw, FAMILY)
    check_twice_clean(s)
    r, texts = pages()
    assert r.messages == [FAMILY]
    assert "MESSAGE IN THE WILL" in texts
    assert "“{}”".format(FAMILY) in texts
    assert ("Written on the blockchain at the delivery, with the will transaction: "
            "from then on it is public. The will-executors can read it before.") in texts


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
