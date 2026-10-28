"""The "New heir" / "Edit heir" window: notes, the address, the QR (#138).

Owner's requests:

* private notes on each heir, kept only in this wallet (their own wallet
  key ``heir_notes``; the heirs entries stay [address, amount, locktime]);
  they follow the heir (rename, delete, import, the wizard's Cancel);
  never sent anywhere;
* the address field explains what it must be, and an extended key pasted
  instead of an address is refused with its own message (a PRIVATE one
  with a strong warning, and never repeated anywhere);
* a camera button reads a QR code: a plain address or a "bitcoin:" URI
  (only the address is kept).

The REAL ``BalWindow`` dialog is used (only its ``exec`` is scripted) on
flowkit wallets with three fake will-executors, each with its own fee
address; after a change, a Check then a second Check clean.

Test keys are the public BIP32 / BIP84 test vectors (never real keys).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_heir_notes_and_address.py
"""

import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

from bal.core import heir_notes
from bal.core.heirs import Heirs, extended_key_kind

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
# BIP32 test vector 1 (master) and BIP84 (account 0): public test values.
XPUB = ("xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu"
        "1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8")
XPRV = ("xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJx"
        "WUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi")
ZPUB = ("zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdV"
        "CToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs")
XPUB_MESSAGE = ("This is an extended public key (xpub/zpub), not an address: in the "
                "heir's wallet open Receive and copy one address.")
XPRV_MESSAGE = "This is a PRIVATE key: never paste it anywhere. Use one address from Receive."
# #139: the amount's "?" text (it replaced "Fixed or Percentage amount if
# end with %").
AMOUNT_HELP = (
    "Percentage: a share of the wallet balance (e.g. 30). The heirs' percentages "
    "are weights: together they share, in proportion, what is left after the fees "
    "and the fixed amounts (e.g. two heirs at 30 and no others: half each). "
    "Fixed amount: an exact amount in the wallet's unit, paid first. "
    "If no heir has a percentage, what is left over is shared among the heirs "
    "with a fixed amount, in proportion: the wallet is always emptied completely.")


@pytest.fixture
def make(monkeypatch):
    """A will in force at three will-executors, and the REAL window."""
    from test_gui_window_init_migration import real_window
    fk.ensure_event_loop()
    made = []

    def _make(check=True):
        s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        if check:
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


def dialog(monkeypatch, name=None, address=None, note=None, seen=None, amount=None):
    """Script the dialog's ``exec``: type the given fields, OK once."""
    from bal.gui.qt import window as window_mod
    calls = {"n": 0}

    def fake_exec(self):
        if calls["n"]:
            return False
        calls["n"] += 1
        edits = self.findChildren(QLineEdit)
        if name is not None:
            edits[0].setText(name)
        if address is not None:
            edits[1].setText(address)
        if amount is not None:
            edits[2].setText(amount)
        if note is not None:
            notes_box(self).setPlainText(note)
        if seen is not None:
            seen["texts"] = [w.text() for w in self.findChildren(QLabel)]
            seen["note"] = notes_box(self).toPlainText()
        return True

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)


def notes_box(d):
    """The Notes box of the heir dialog ``d`` (#140: the message field is
    a multi-line box too, so the type alone is not enough)."""
    from bal.gui.qt.widgets import MessageEdit
    return [w for w in d.findChildren(QPlainTextEdit) if not isinstance(w, MessageEdit)][0]


def stored_notes(s):
    return dict(s.wallet.db.get_dict("heir_notes"))


def check_twice_clean(s):
    """A Check, then a second Check with nothing to say or ask."""
    s.bw.heirs = Heirs(s.wallet)
    s.check()
    s.user.asked.clear()
    snap = s.check()._report().snapshot()
    assert s.user.asked == []
    assert snap["boxes"]["changes"] == [] and "changes" not in snap


# ------------------------------------------------------------------ notes

def test_note_saved_renamed_deleted(make, monkeypatch):
    """A note is saved with the heir, shown in the list's tooltip, moves
    with a rename and goes with a delete; the heirs entries stay 3 items."""
    s, rbw = make()
    dialog(monkeypatch, note="  Sister, phone in the red book  ")
    rbw.new_heir_dialog("bob")
    assert stored_notes(s) == {"bob": "Sister, phone in the red book"}
    assert len(Heirs(s.wallet)["bob"]) == 3
    lst = rbw.heir_list_widget
    lst.update()
    model = lst.model()
    row = next(r for r in range(model.rowCount()) if model.item(r, 0).text() == "bob")
    assert model.item(row, 0).toolTip() == "Sister, phone in the red book"
    seen = {}
    dialog(monkeypatch, name="bob renamed", seen=seen)
    rbw.new_heir_dialog("bob")
    assert seen["note"] == "Sister, phone in the red book"         # shown again
    assert stored_notes(s) == {"bob renamed": "Sister, phone in the red book"}
    rbw.delete_heirs(["bob renamed"])
    assert stored_notes(s) == {}


def test_note_help_and_length_cap(make, monkeypatch):
    """The "?" texts (the four of before and the notes' one; #139: the
    amount's is new) and the grey address line are there; a note over the
    cap is refused."""
    s, rbw = make()
    seen = {}
    dialog(monkeypatch, note="x" * (heir_notes.MAX_LENGTH + 1), seen=seen)
    rbw.new_heir_dialog("bob")
    assert rbw.errors == ["The notes are too long: at most 1000 characters."]
    assert stored_notes(s) == {}
    assert ("One single address of the heir (e.g. bc1q…), taken from Receive in the "
            "heir's wallet. Not an xpub/zpub.") in seen["texts"]
    from bal.gui.qt import window as window_mod
    helps = []
    # #198: every "?" is made by common.help_button (Electrum 4.8.3).
    real = window_mod.help_button

    def spy(text, *a, **k):
        helps.append(text)
        return real(text, *a, **k)

    monkeypatch.setattr(window_mod, "help_button", spy)
    dialog(monkeypatch, note="ok")
    rbw.new_heir_dialog("bob")
    # #139: the amount's "?" is made before the others (the OP_RETURN
    # toggle hides it); #140: the message's "?" too. The first four are
    # compared as a set.
    from bal.gui.qt.widgets import message_help_text
    assert sorted(helps[:4]) == sorted(["Unique name or description about heir",
                                        "Bitcoin address or OP_RETURN: prefix + hex data",
                                        AMOUNT_HELP, message_help_text()])
    assert helps[-1] == ("Private notes for you, kept only in this wallet; never sent "
                         "to the will-executors or written on the blockchain.")


def test_import_keeps_notes_of_heirs_still_there(make, monkeypatch, tmp_path):
    """GUI import: the notes of heirs with the same name stay; a note whose
    heir is no longer there is dropped (cleaned)."""
    s, rbw = make()
    heir_notes.set_note(s.wallet, "bob", "keep")
    heir_notes.set_note(s.wallet, "ghost", "orphan")
    path = tmp_path / "heirs.json"
    path.write_text(json.dumps({"bob": list(Heirs(s.wallet)["bob"])}))
    from bal.gui.qt import window as window_mod

    def fake_import(parent, title, importer, on_success):
        importer(str(path))
        on_success()

    monkeypatch.setattr(window_mod, "import_meta_gui", fake_import)
    rbw.import_heirs()
    assert stored_notes(s) == {"bob": "keep"}


def test_wizard_cancel_restores_the_notes(make, monkeypatch):
    """The BASIC wizard's Cancel puts the notes back with the heirs."""
    from bal.gui.qt.dialogs import BalWizardDialog
    s, rbw = make()
    heir_notes.set_note(s.wallet, "bob", "before")
    wizard = BalWizardDialog(rbw)
    dialog(monkeypatch, name="bob 2", note="after")
    rbw.new_heir_dialog("bob")
    assert stored_notes(s) == {"bob 2": "after"}
    wizard.close()
    assert stored_notes(s) == {"bob": "before"}
    assert "bob" in Heirs(s.wallet)


def test_old_wallet_without_notes_opens(make):
    """A wallet with no "heir_notes" key (every wallet before #138): the
    window opens, no note, the heirs as they were."""
    s, rbw = make()
    assert "heir_notes" not in s.wallet.db.data or not stored_notes(s)
    assert heir_notes.get(s.wallet, "bob") == ""
    assert sorted(Heirs(s.wallet)) == ["alice", "bob"]


def test_cli_delete_and_import_clean_the_notes(make, tmp_path):
    """The command line's delete removes the note; its import cleans."""
    from bal.cli.controller import BalController
    s, rbw = make()
    heir_notes.set_note(s.wallet, "alice", "a")
    heir_notes.set_note(s.wallet, "bob", "b")
    c = BalController.__new__(BalController)
    c.wallet = s.wallet
    c.heirs = Heirs(s.wallet)
    c.plugin = s.plugin
    c.heirs_delete(["bob"])
    assert stored_notes(s) == {"alice": "a"}
    heir_notes.set_note(s.wallet, "ghost", "x")
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"alice": list(Heirs(s.wallet)["alice"])}))
    c.heirs_import(str(path))
    assert stored_notes(s) == {"alice": "a"}


def test_new_heir_with_notes_then_checks_clean(make, monkeypatch):
    """BASIC: an heir added with notes: the Check prepares the new will;
    the second Check is clean; the notes are not in any will item."""
    s, rbw = make()
    dialog(monkeypatch, name="carol", address=fk.HEIR_ADDR["carol"], note="Cousin",
           amount="10%")
    rbw.new_heir_dialog()
    assert stored_notes(s) == {"carol": "Cousin"}
    check_twice_clean(s)
    for item in s.bw.willitems.values():
        assert "Cousin" not in json.dumps(item.to_dict(), default=str)


# -------------------------------------------------------------- address

def test_extended_keys_are_named(make, monkeypatch, caplog):
    """An xpub / zpub: its own message; an xprv: the strong warning, and
    the private key appears in no message and no log line."""
    s, rbw = make()
    caplog.set_level(logging.DEBUG)
    assert extended_key_kind(XPUB) == "public"
    assert extended_key_kind(ZPUB) == "public"
    assert extended_key_kind(XPRV) == "private"
    assert extended_key_kind(fk.HEIR_ADDR["bob"]) is None
    for key, message in ((XPUB, XPUB_MESSAGE), (ZPUB, XPUB_MESSAGE), (XPRV, XPRV_MESSAGE)):
        rbw.errors.clear()
        dialog(monkeypatch, name="carol", address=key)
        rbw.new_heir_dialog()
        assert rbw.errors == [message]
    assert "carol" not in Heirs(s.wallet)
    assert XPRV not in caplog.text and XPRV[:20] not in caplog.text


def test_cli_refuses_an_xprv_without_echo(make):
    """The command line says the same, never with the key."""
    from electrum.util import UserFacingException

    from bal.cli.controller import BalController, _user_facing
    s, rbw = make()
    c = BalController.__new__(BalController)
    c.wallet = s.wallet
    c.heirs = Heirs(s.wallet)
    c.plugin = s.plugin
    c.will_settings = s.bw.will_settings
    with pytest.raises(Exception) as info:
        c.heirs_add("carol", XPRV, "10%")
    assert XPRV not in str(info.value)
    # What the command layer prints (``_user_facing``, as every CLI error).
    shown = _user_facing(info.value)
    assert isinstance(shown, UserFacingException)
    assert str(shown) == XPRV_MESSAGE and XPRV not in str(shown)


# -------------------------------------------------------------- QR

def test_qr_plain_address_uri_and_invalid():
    """A plain address and a "bitcoin:" URI with an amount give the
    address; anything else: None and a plain message."""
    from bal.gui.qt.window import qr_address
    # The BIP173 example address (mainnet: the network of this test).
    address = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    errors = []
    assert qr_address(address, errors.append) == address
    assert qr_address("bitcoin:{}?amount=0.01&label=x".format(address),
                      errors.append) == address
    assert errors == []
    assert qr_address("bitcoin:notanaddress", errors.append) is None
    assert qr_address("hello", errors.append) is None
    assert errors == ["This QR code does not contain a Bitcoin address."] * 2


def test_camera_button_on_the_address_field(make, monkeypatch):
    """The address field has Electrum's camera button; it is hidden in the
    OP_RETURN mode; what it reads is set through qr_address."""
    s, rbw = make()
    seen = {}
    from bal.gui.qt import window as window_mod

    def fake_exec(self):
        field = self.findChildren(QLineEdit)[1]
        seen["buttons"] = list(field.qr_buttons)
        seen["visible"] = [not b.isHidden() for b in field.qr_buttons]
        field.setText("OP_RETURN:68656c6c6f")
        seen["hidden_in_op_return"] = [b.isHidden() for b in field.qr_buttons]
        return False

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)
    rbw.new_heir_dialog()
    assert len(seen["buttons"]) == 1 and seen["visible"] == [True]
    assert seen["hidden_in_op_return"] == [True]


def test_address_text_never_under_the_camera_button(make, monkeypatch):
    """Owner's ZIP test (#138): the camera icon sat on the end of the
    address. With the button visible the right text margin is at least the
    button's width; in OP_RETURN mode it is 0 again; back to an address,
    the room comes back."""
    s, rbw = make()
    seen = {}
    from bal.gui.qt import window as window_mod

    def fake_exec(self):
        self.show()  # real sizes, as on screen
        field = self.findChildren(QLineEdit)[1]
        button = field.qr_buttons[0]
        seen["button_width"] = button.sizeHint().width()
        seen["margin_address"] = field.textMargins().right()
        field.setText("OP_RETURN:68656c6c6f")
        seen["margin_op_return"] = field.textMargins().right()
        field.setText("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
        seen["margin_back"] = field.textMargins().right()
        self.hide()
        return False

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)
    rbw.new_heir_dialog()
    assert seen["button_width"] > 0
    assert seen["margin_address"] >= seen["button_width"]
    assert seen["margin_op_return"] == 0
    assert seen["margin_back"] == seen["margin_address"]
