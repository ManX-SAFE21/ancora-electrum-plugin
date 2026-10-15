"""The heir window: "% Percentage" or "Fixed amount" buttons (#139).

Owner's request: the amount field of the New / Edit heir window (also used
by the BASIC wizard and the Heirs list) said only "Fixed or Percentage
amount if end with %". Now:

* two exclusive buttons next to the field, "% Percentage" and "Fixed
  amount"; a new heir starts on Percentage, an edit preselects the stored
  mode ("30%": Percentage, else Fixed amount);
* the field shows its unit at the right ("%" or the wallet's base unit) and
  reserves its room (the text never runs under it); the "%" is never typed:
  typed anyway in Fixed mode it switches to Percentage and is dropped;
  switching the mode keeps the number;
* a grey line under the field says what the mode means;
* OP_RETURN mode hides the buttons, the unit and the grey line;
* the stored format ("30%" or satoshis), the computation and the #135 edit
  hint are unchanged.

Owner's ZIP test of the OP_RETURN mode (A-D):

* A: OK with an empty name stored the key "" in memory and failed in
  Electrum's ``StoredDict`` with a raw error; now "Write a name for the
  heir.", the window stays open, nothing is stored (the core and the
  command line refuse it too);
* B: the amount's "?" is hidden with the amount;
* C: #138's grey address line is hidden;
* D: no empty band between the address and the message rows.

The REAL ``BalWindow`` dialog is used (only its ``exec`` is scripted) on
flowkit wallets with three fake will-executors, each with its own fee
address; after a new heir with a fixed amount, a Check then a second Check
clean.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_amount_mode.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QToolButton

from bal.core.heirs import Heirs
from bal.gui.qt.widgets import MessageEdit, PercAmountEdit

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
PERCENTAGE = "% Percentage"
FIXED = "Fixed amount"
PERCENTAGE_LINE = ("A share of the wallet balance (e.g. 30). The heirs' percentages "
                   "share, in proportion, what is left after the fixed amounts.")
FIXED_LINE = ("An exact amount in {}, paid first. If no heir has a percentage, what "
              "is left over is shared among the heirs in proportion: the wallet is "
              "always emptied completely.")
SHARE_CHANGES = "The share changes: the will must be signed again."
ONLY_NAME = "Only the name changes: no need to sign."
WRITE_A_NAME = "Write a name for the heir."
ADDRESS_LINE = ("One single address of the heir (e.g. bc1q…), taken from Receive in the "
                "heir's wallet. Not an xpub/zpub.")
AMOUNT_HELP_START = "Percentage: a share of the wallet balance"
OP_RETURN = "OP_RETURN:68656c6c6f"


@pytest.fixture
def make(monkeypatch):
    """A will in force at three will-executors, and the REAL window."""
    from test_gui_window_init_migration import real_window
    fk.ensure_event_loop()
    made = []

    def _make():
        """``(session, real window)``; ``rbw.errors``: the messages shown."""
        s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        assert len({w["address"] for w in s.world.executors.values()}) == 3
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.check()
        monkeypatch.setattr(s.plugin, "get_window_title", lambda t: t, raising=False)
        rbw = real_window(s, monkeypatch)
        rbw.init_will()
        errors = []
        monkeypatch.setattr(rbw, "show_error", lambda text, *a, **k: errors.append(text))
        rbw.errors = errors
        return s, rbw

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


class Parts:
    """The amount parts of the open dialog ``d``, found by type and text."""

    def __init__(self, d):
        """Find the field, the two buttons, the grey line and the hint."""
        self.d = d
        self.edits = d.findChildren(QLineEdit)
        self.field = d.findChildren(PercAmountEdit)[0]
        buttons = {b.text(): b for b in d.findChildren(QPushButton)}
        self.percentage = buttons[PERCENTAGE]
        self.fixed = buttons[FIXED]

    def label(self, *texts):
        """The first label of the dialog showing one of ``texts``, or None."""
        for w in self.d.findChildren(QLabel):
            if w.text() in texts:
                return w
        return None

    def mode_line(self):
        """The grey amount line (either mode's text), or None."""
        for w in self.d.findChildren(QLabel):
            if w.text() == PERCENTAGE_LINE or w.text().startswith("An exact amount in "):
                return w
        return None

    def labels(self):
        """Every label text of the dialog."""
        return [w.text() for w in self.d.findChildren(QLabel)]


def script(monkeypatch, steps, accept=True):
    """Script the dialog's ``exec``: run ``steps(Parts)``, then OK (or
    Cancel when ``accept`` is False) once."""
    from bal.gui.qt import window as window_mod
    calls = {"n": 0}

    def fake_exec(self):
        """Run the steps on the real dialog the first time only."""
        if calls["n"]:
            return False
        calls["n"] += 1
        steps(Parts(self))
        return accept

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)


def stored(s, name):
    """The stored amount of heir ``name``, as a string."""
    return str(Heirs(s.wallet)[name][1])


def check_twice_clean(s):
    """A Check, then a second Check with nothing to say or ask."""
    s.bw.heirs = Heirs(s.wallet)
    s.check()
    s.user.asked.clear()
    snap = s.check()._report().snapshot()
    assert s.user.asked == []
    assert snap["boxes"]["changes"] == [] and "changes" not in snap


# ---------------------------------------------------------------- buttons

def test_two_buttons_and_a_new_heir_on_percentage(make, monkeypatch):
    """Two exclusive checkable buttons; a new heir starts on Percentage,
    with the "%" unit, its room reserved, and the Percentage grey line."""
    s, rbw = make()
    seen = {}

    def steps(p):
        seen["checkable"] = (p.percentage.isCheckable(), p.fixed.isCheckable())
        seen["checked"] = (p.percentage.isChecked(), p.fixed.isChecked())
        seen["exclusive"] = p.percentage.group() is p.fixed.group() and \
            p.percentage.group().exclusive()
        seen["mode"] = p.field.percentage
        seen["unit"] = p.field.unit_text()
        seen["margin"] = p.field.textMargins().right()
        seen["unit_width"] = p.field.fontMetrics().horizontalAdvance("%")
        seen["line"] = p.mode_line().text()
        seen["order"] = p.edits.index(p.field)

    script(monkeypatch, steps, accept=False)
    rbw.new_heir_dialog()
    assert seen["checkable"] == (True, True) and seen["exclusive"]
    assert seen["checked"] == (True, False)
    assert seen["mode"] is True and seen["unit"] == "%"
    assert seen["margin"] >= seen["unit_width"] + PercAmountEdit.UNIT_RIGHT_PAD
    assert seen["line"] == PERCENTAGE_LINE
    assert seen["order"] == 2          # name, address, amount (as before)


def test_edit_preselects_the_stored_mode(make, monkeypatch):
    """Editing "40%": Percentage, the field shows "40"; editing a fixed
    amount (satoshis): Fixed amount, the amount in BTC, the BTC unit and
    the Fixed grey line."""
    s, rbw = make()
    seen = {}

    def steps(p):
        seen["checked"] = (p.percentage.isChecked(), p.fixed.isChecked())
        seen["text"] = p.field.text()
        seen["unit"] = p.field.unit_text()
        seen["line"] = p.mode_line().text()

    script(monkeypatch, steps, accept=False)
    rbw.new_heir_dialog("bob")
    assert stored(s, "bob") == "40%"
    assert seen == {"checked": (True, False), "text": "40", "unit": "%",
                    "line": PERCENTAGE_LINE}
    bob = list(rbw.heirs["bob"])
    rbw.heirs["bob"] = [bob[0], "100000", bob[2]]
    script(monkeypatch, steps, accept=False)
    rbw.new_heir_dialog("bob")
    assert seen == {"checked": (False, True), "text": "0.00100000", "unit": "BTC",
                    "line": FIXED_LINE.format("BTC")}


def test_unit_and_margin_follow_the_mode_and_the_number_stays(make, monkeypatch):
    """Wallet unit mBTC: Fixed shows "mBTC" (and the Fixed line says it),
    Percentage "%"; the right margin always holds the unit; switching
    keeps the number typed."""
    s, rbw = make()
    monkeypatch.setattr(rbw.window, "get_decimal_point", lambda: 5, raising=False)
    seen = {}

    def steps(p):
        metrics = p.field.fontMetrics()
        p.field.setText("12.5")
        p.fixed.click()
        seen["fixed"] = (p.field.percentage, p.field.text(), p.field.unit_text(),
                         p.field.textMargins().right(), metrics.horizontalAdvance("mBTC"),
                         p.mode_line().text(), p.percentage.isChecked())
        p.percentage.click()
        seen["perc"] = (p.field.percentage, p.field.text(), p.field.unit_text(),
                        p.field.textMargins().right(), metrics.horizontalAdvance("%"),
                        p.mode_line().text(), p.fixed.isChecked())

    script(monkeypatch, steps, accept=False)
    rbw.new_heir_dialog()
    mode, text, unit, margin, width, line, other = seen["fixed"]
    assert (mode, text, unit, other) == (False, "12.5", "mBTC", False)
    assert margin >= width + PercAmountEdit.UNIT_RIGHT_PAD
    assert line == FIXED_LINE.format("mBTC")
    mode, text, unit, margin, width, line, other = seen["perc"]
    assert (mode, text, unit, other) == (True, "12.5", "%", False)
    assert margin >= width + PercAmountEdit.UNIT_RIGHT_PAD
    assert margin < seen["fixed"][3]
    assert line == PERCENTAGE_LINE


def test_percent_typed_in_fixed_switches_to_percentage(make, monkeypatch):
    """A "%" typed in Fixed mode: Percentage, the "%" dropped from the
    text, the buttons and the grey line follow; saved as "25%"."""
    s, rbw = make()
    seen = {}

    def steps(p):
        p.edits[0].setText("carol")
        p.edits[1].setText(fk.HEIR_ADDR["carol"])
        p.fixed.click()
        p.field.setText("25%")
        seen["after"] = (p.field.percentage, p.field.text(), p.percentage.isChecked(),
                         p.fixed.isChecked(), p.mode_line().text())

    script(monkeypatch, steps)
    rbw.new_heir_dialog()
    assert seen["after"] == (True, "25", True, False, PERCENTAGE_LINE)
    assert rbw.errors == []
    assert stored(s, "carol") == "25%"


# ---------------------------------------------------------- stored format

def test_stored_format_percentage_and_satoshis(make, monkeypatch):
    """Percentage "10" is stored "10%"; Fixed "0.001" (BTC) is stored as
    satoshis, as ``Util.encode_amount`` always did."""
    s, rbw = make()

    def percentage(p):
        p.edits[0].setText("carol")
        p.edits[1].setText(fk.HEIR_ADDR["carol"])
        p.field.setText("10")

    script(monkeypatch, percentage)
    rbw.new_heir_dialog()
    assert stored(s, "carol") == "10%"

    def fixed(p):
        p.fixed.click()
        p.field.setText("0.001")

    script(monkeypatch, fixed)
    rbw.new_heir_dialog("carol")
    assert rbw.errors == []
    assert stored(s, "carol") == "100000"


# ---------------------------------------------------------------- OP_RETURN

def test_op_return_hides_buttons_unit_and_line(make, monkeypatch):
    """OP_RETURN: the field, the buttons and the grey line are hidden (the
    unit is painted by the field); back to an address they come back."""
    s, rbw = make()
    seen = {}

    def shown(p):
        """Visibility of the field, the buttons and the grey line."""
        line = p.label(PERCENTAGE_LINE)
        return [w.isVisibleTo(p.d) for w in (p.field, p.percentage, p.fixed, line)]

    def steps(p):
        seen["address"] = shown(p)
        p.edits[1].setText("OP_RETURN:68656c6c6f")
        seen["op_return"] = shown(p)
        p.edits[1].setText(fk.HEIR_ADDR["carol"])
        seen["back"] = shown(p)

    script(monkeypatch, steps, accept=False)
    rbw.new_heir_dialog()
    assert seen["address"] == [True] * 4
    assert seen["op_return"] == [False] * 4
    assert seen["back"] == [True] * 4


def test_op_return_heir_opens_with_the_amount_hidden(make, monkeypatch):
    """Editing an OP_RETURN heir: #140 (owner's ZIP test) opens the
    message window, which has no amount field at all."""
    from bal.gui.qt import window as window_mod
    from bal.gui.qt.widgets import MessageEdit
    s, rbw = make()
    rbw.heirs["note"] = ["OP_RETURN:68656c6c6f", "0", rbw.heirs["bob"][2]]
    seen = {}

    def fake_exec(self):
        """Look at the window, then Cancel."""
        seen["amount"] = self.findChildren(PercAmountEdit)
        seen["message"] = [w.text() for w in self.findChildren(MessageEdit)]
        return False

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)
    rbw.new_heir_dialog("note")
    assert seen == {"amount": [], "message": ["hello"]}


# ---------------------------------------------------------------- edit hint

@pytest.mark.parametrize("amount", ["40%", "100000"])
def test_edit_without_changes_keeps_the_name_only_hint(make, monkeypatch, amount):
    """Editing an heir without touching the amount: no hint, and a pure
    rename says "Only the name changes" and keeps the stored amount; a
    mode switch says the share changes."""
    s, rbw = make()
    bob = list(rbw.heirs["bob"])
    rbw.heirs["bob"] = [bob[0], amount, bob[2]]
    seen = {}

    def steps(p):
        seen["untouched"] = p.label(SHARE_CHANGES, ONLY_NAME) is None
        other = p.fixed if p.field.percentage else p.percentage
        same = p.percentage if p.field.percentage else p.fixed
        other.click()
        seen["switched"] = p.label(SHARE_CHANGES) is not None
        same.click()
        seen["back"] = p.label(SHARE_CHANGES, ONLY_NAME) is None
        p.edits[0].setText("bobby")
        seen["renamed"] = p.label(ONLY_NAME) is not None

    script(monkeypatch, steps)
    rbw.new_heir_dialog("bob")
    assert seen == {"untouched": True, "switched": True, "back": True, "renamed": True}
    assert "bob" not in Heirs(s.wallet)
    assert stored(s, "bobby") == amount


# ---------------------------------------------------------------- Check

def test_new_heir_with_a_fixed_amount_then_checks_clean(make, monkeypatch):
    """BASIC: a new heir with a fixed amount (0.001 BTC): stored as
    satoshis; the Check prepares the new will; the second Check is
    clean."""
    s, rbw = make()

    def steps(p):
        p.edits[0].setText("carol")
        p.edits[1].setText(fk.HEIR_ADDR["carol"])
        p.fixed.click()
        p.field.setText("0.001")

    script(monkeypatch, steps)
    rbw.new_heir_dialog()
    assert rbw.errors == []
    assert stored(s, "carol") == "100000"
    check_twice_clean(s)
    paid = [o.value for item in s.bw.willitems.values()
            for o in item.tx.outputs() if o.address == fk.HEIR_ADDR["carol"]]
    assert paid and all(v >= 100000 for v in paid)


# ------------------------------------------- owner's OP_RETURN test (A-D)

def payee_widgets(p):
    """The amount's "?" button and #138's grey address line of ``p``."""
    helps = [b for b in p.d.findChildren(QToolButton)
             if str(getattr(b, "help_text", "")).startswith(AMOUNT_HELP_START)]
    assert len(helps) == 1
    return helps[0], p.label(ADDRESS_LINE)


def test_op_return_hides_the_amount_help_and_the_address_line(make, monkeypatch):
    """B, C: in OP_RETURN mode the amount's "?" and the grey address line
    are hidden; back to an address they come back."""
    s, rbw = make()
    seen = {}

    def steps(p):
        widgets = payee_widgets(p)
        seen["address"] = [w.isVisibleTo(p.d) for w in widgets]
        p.edits[1].setText(OP_RETURN)
        seen["op_return"] = [w.isVisibleTo(p.d) for w in widgets]
        p.edits[1].setText(fk.HEIR_ADDR["carol"])
        seen["back"] = [w.isVisibleTo(p.d) for w in widgets]

    script(monkeypatch, steps, accept=False)
    rbw.new_heir_dialog()
    assert seen == {"address": [True, True], "op_return": [False, False],
                    "back": [True, True]}


def test_op_return_message_right_under_the_address(make, monkeypatch):
    """D: shown on screen, the OP_RETURN message row sits right under the
    address row (only the grid spacing between them) and the window fits
    its rows in both modes (no empty band). #140 adds the counter and the
    warning under the message, so the OP_RETURN window is no longer
    always shorter: it is compared with the height its rows need."""
    s, rbw = make()
    seen = {}

    def fits(d):
        """The window is as tall as its rows need (no empty band)."""
        return abs(d.height() - d.sizeHint().height()) <= 2

    def steps(p):
        p.d.show()
        _APP.processEvents()
        p.edits[1].setText(OP_RETURN)
        for _ in range(3):
            _APP.processEvents()
        # #140: the message field is a two/three-line box (MessageEdit).
        message = p.d.findChildren(MessageEdit)[0]
        assert message.isVisible()
        seen["gap"] = message.geometry().top() - p.edits[1].geometry().bottom()
        seen["fits_message"] = fits(p.d)
        p.edits[1].setText(fk.HEIR_ADDR["carol"])
        for _ in range(3):
            _APP.processEvents()
        seen["fits_address"] = fits(p.d)
        p.d.hide()

    script(monkeypatch, steps, accept=False)
    rbw.new_heir_dialog()
    assert 0 < seen["gap"] <= 12
    assert seen["fits_message"] and seen["fits_address"]


def script_until_cancel(monkeypatch, names, seen):
    """Script ``exec``: OK with each name of ``names`` (address and amount
    filled), then Cancel; ``seen["calls"]`` counts the calls."""
    from bal.gui.qt import window as window_mod
    seen["calls"] = 0

    def fake_exec(self):
        """One OK per name, then Cancel (the window stayed open)."""
        seen["calls"] += 1
        if seen["calls"] > len(names):
            return False
        p = Parts(self)
        p.edits[0].setText(names[seen["calls"] - 1])
        p.edits[1].setText(fk.HEIR_ADDR["carol"])
        p.field.setText("10")
        return True

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)


def test_empty_name_is_refused_and_the_window_stays_open(make, monkeypatch):
    """A (owner's bug): an empty or blank name: "Write a name for the
    heir.", the window stays open, the heirs are unchanged (no "" key);
    a normal heir after it saves fine."""
    s, rbw = make()
    before = {k: list(v) for k, v in Heirs(s.wallet).items()}
    seen = {}
    script_until_cancel(monkeypatch, ["", "   "], seen)
    rbw.new_heir_dialog()
    assert seen["calls"] == 3                       # OK, OK, then Cancel
    assert rbw.errors == [WRITE_A_NAME, WRITE_A_NAME]
    assert "" not in rbw.heirs and "   " not in rbw.heirs
    assert {k: list(v) for k, v in Heirs(s.wallet).items()} == before
    rbw.errors.clear()
    script_until_cancel(monkeypatch, ["carol"], seen)
    rbw.new_heir_dialog()
    assert rbw.errors == []
    assert stored(s, "carol") == "10%"
    assert "" not in Heirs(s.wallet)


def test_empty_name_refused_by_the_core_and_the_command_line(make):
    """A: ``Heirs.validate_heir`` refuses a name empty after strip before
    anything is stored; a name with spaces around is kept as it is; the
    command line gives the same plain sentence and stores nothing."""
    from bal.cli.controller import BalController, _user_facing
    from bal.core.heirs import HeirNameNotValid
    from bal.core.messages import heir_error_message
    s, rbw = make()
    value = [fk.HEIR_ADDR["carol"], "10%", rbw.heirs["bob"][2]]
    for name in ("", "  ", None):
        with pytest.raises(HeirNameNotValid) as info:
            Heirs.validate_heir(name, value)
        assert heir_error_message(info.value) == WRITE_A_NAME
    assert Heirs.validate_heir(" carol ", value)[0] == fk.HEIR_ADDR["carol"]
    c = BalController.__new__(BalController)
    c.wallet = s.wallet
    c.heirs = Heirs(s.wallet)
    c.plugin = s.plugin
    c.will_settings = s.bw.will_settings
    with pytest.raises(Exception) as info:
        c.heirs_add(" ", fk.HEIR_ADDR["carol"], "10%")
    assert str(_user_facing(info.value)) == WRITE_A_NAME
    assert " " not in Heirs(s.wallet) and "" not in Heirs(s.wallet)
    assert sorted(Heirs(s.wallet)) == ["alice", "bob"]
