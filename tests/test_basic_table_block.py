"""BASIC with a will in several deliveries, the "Multiverse" (#193).

Owner decision (1), 2026-10-23: in BASIC the table of the deliveries is
read-only and its window exists only in ADVANCED. An heir added, deleted
or imported there would leave the table out of step with the heirs, and
the next Check would stop with no way to fix it in BASIC. So "New heir",
"Delete" and "Import" (owner, 2026-10-26: Import too) do not go on and say
to switch to ADVANCED. An edit (the shares follow the new name) and the
message (no share in the table) still work; ADVANCED and a will with one
delivery are unchanged. The message window and its "?" say that the
message travels in delivery 1.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address, the test password, a fixed clock; the REAL ``BalWindow``
(only the dialogs' ``exec`` and the file window are scripted).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_basic_table_block.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QPushButton, QToolButton
from test_gui_window_init_migration import real_window
from test_multiverse_chain_rebuild import D1, D2, D3

from bal.core.heirs import message_address, message_names
from bal.engine import context
from bal.gui.qt import window as window_mod
from bal.gui.qt.widgets import MessageEdit, message_help_text

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"

BLOCKED = ("This will is in several deliveries: to add or remove an heir, "
           "switch to ADVANCED, where you give them their shares in the table.")
DELIVERY_1 = "In a will in several deliveries the message travels in delivery 1."
ADDR = {"luca": fk.address(20), "john": fk.address(21), "carol": fk.address(22)}


@pytest.fixture
def make(monkeypatch):
    """A wallet with three will-executors and the REAL window."""
    fk.ensure_event_loop()
    made = []

    def _make(mode="basic", table=True):
        """``(session, real window)``: heirs luca and john; ``table``: the
        owner's three deliveries (10/10, 10/10, 30/30 %). ``rbw.messages``:
        the information boxes shown; ``rbw.opened``: the heir windows that
        opened (``_heir_dialog``, replaced: it would wait for the user)."""
        s = fk.Session(monkeypatch, mode=mode, password=TEST_PASSWORD,
                       fee=20, locktime=D1)
        s.world.add_executor()
        made.append(s)
        assert len({w["address"] for w in s.world.executors.values()}) == 3
        for name in list(s.bw.heirs.keys()):
            del s.bw.heirs[name]
        for name in ("luca", "john"):
            s.bw.heirs[name] = [ADDR[name], "50%", D1]
        s.bw.heirs.save()
        if table:
            context.set_instalments(s.bw, [
                {"locktime": D1, "shares": {"luca": "10%", "john": "10%"}},
                {"locktime": D2, "shares": {"luca": "10%", "john": "10%"}},
                {"locktime": D3, "shares": {"luca": "30%", "john": "30%"}}])
        monkeypatch.setattr(s.plugin, "get_window_title", lambda t: t, raising=False)
        rbw = real_window(s, monkeypatch)
        rbw.messages, rbw.opened = [], []
        monkeypatch.setattr(rbw, "show_message",
                            lambda text, *a, **k: rbw.messages.append(text))
        monkeypatch.setattr(rbw, "show_error", lambda text, *a, **k: None)
        monkeypatch.setattr(rbw, "_heir_dialog",
                            lambda key=None: rbw.opened.append(key))
        return s, rbw

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def fake_import(monkeypatch, rbw, heirs):
    """Electrum's file window replaced: the import reads ``heirs``.
    Returns the list of the imports that really ran."""
    ran = []

    def _import(window, title, importer, on_success):
        """``import_meta_gui`` without the file window."""
        ran.append(title)
        for name, value in heirs.items():
            rbw.heirs[name] = value
        on_success()

    monkeypatch.setattr(window_mod, "import_meta_gui", _import)
    return ran


def script(monkeypatch, steps):
    """The dialog's ``exec``: ``steps(dialog)`` and Cancel."""
    def fake_exec(self):
        """The steps on the real dialog, then Cancel."""
        steps(self)
        return False

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)


def heirs_tab_button(rbw):
    """The Heirs tab's REAL "👤 New Heir" button (the tab as Electrum's
    ``create_list_tab`` builds it: the list's toolbar, then the list)."""
    found = [b for b in rbw.heirs_tab.findChildren(QPushButton)
             if b.text() == "👤 New Heir"]
    assert len(found) == 1, found
    return found[0]


def heirs_tab_action(rbw, text):
    """The Heirs tab's REAL menu entry ``text`` (its "Tools" menu)."""
    # Electrum's ``create_toolbar_with_menu``: the menu hangs on a tool
    # button, it is not a child widget of the tab.
    menus = [b.menu() for b in rbw.heirs_tab.findChildren(QToolButton)
             if isinstance(b.menu(), QMenu)]
    found = [a for m in menus for a in m.actions() if a.text() == text]
    assert len(found) == 1, (text, found)
    return found[0]


# ------------------------------------------------------------ BASIC + table

def test_new_heir_is_blocked(make):
    """BASIC with a table: no heir window, the owner's sentence; the heirs
    and the table unchanged. The wizard's "+ Add heir" and the Heirs window
    use the same method."""
    s, rbw = make()
    assert rbw.new_heir_dialog() is None
    assert rbw.opened == []
    assert rbw.messages == [BLOCKED]
    assert sorted(rbw.heirs.keys()) == ["john", "luca"]


def test_the_real_button_and_menu_are_blocked(make, monkeypatch):
    """Owner's ZIP test: the "👤 New Heir" button opened the heir window
    (Qt's ``clicked`` passes False, taken as a heir name). Now the real
    button and the real menu entries ("&New Heir", "Import") stop with the
    sentence, as the methods do."""
    s, rbw = make()
    ran = fake_import(monkeypatch, rbw, {"carol": [ADDR["carol"], "10%", D1]})
    heirs_tab_button(rbw).click()
    heirs_tab_action(rbw, "&New Heir").trigger()
    heirs_tab_action(rbw, "Import").trigger()
    assert rbw.opened == []
    assert ran == []
    assert rbw.messages == [BLOCKED, BLOCKED, BLOCKED]
    assert sorted(rbw.heirs.keys()) == ["john", "luca"]


def test_the_button_opens_a_new_heir_window_in_advanced(make, monkeypatch):
    """ADVANCED with a table: the real button and menu entry open the
    window of a NEW heir (no key), never an "edit of False": no phantom
    delete of "False" after OK, the automatic name of a new message."""
    s, rbw = make("advanced")
    monkeypatch.setattr(rbw, "ask_deliveries", lambda text: False)
    heirs_tab_button(rbw).click()
    heirs_tab_action(rbw, "&New Heir").trigger()
    assert rbw.opened == [None, None]
    assert rbw.messages == []


def test_edit_is_not_blocked(make):
    """BASIC with a table: "Edit" of an heir opens its window (a rename
    carries the shares)."""
    s, rbw = make()
    rbw.new_heir_dialog("luca")
    assert rbw.opened == ["luca"]
    assert rbw.messages == []


def test_delete_is_blocked(make):
    """BASIC with a table: "Delete" of an heir, or of an heir and the
    message together, does not delete; the sentence is shown."""
    s, rbw = make()
    rbw.heirs["OP_RETURN message"] = [message_address("ciao"), "0", D1]
    rbw.heir_list_widget.delete_heirs(["luca"])
    rbw.heir_list_widget.delete_heirs(["john", "OP_RETURN message"])
    assert rbw.messages == [BLOCKED, BLOCKED]
    assert sorted(rbw.heirs.keys()) == ["OP_RETURN message", "john", "luca"]


def test_the_message_alone_can_be_deleted(make):
    """BASIC with a table: the message has no share in the table, so
    deleting it alone goes on."""
    s, rbw = make()
    rbw.heirs["OP_RETURN message"] = [message_address("ciao"), "0", D1]
    rbw.heir_list_widget.delete_heirs(["OP_RETURN message"])
    assert rbw.messages == []
    assert message_names(rbw.heirs) == []


def test_import_is_blocked(make, monkeypatch):
    """BASIC with a table: "Import" opens no file window and imports
    nothing; the sentence is shown."""
    s, rbw = make()
    ran = fake_import(monkeypatch, rbw, {"carol": [ADDR["carol"], "10%", D1]})
    rbw.import_heirs()
    assert ran == []
    assert rbw.messages == [BLOCKED]
    assert "carol" not in rbw.heirs


# ------------------------------------------------------------ unchanged

@pytest.mark.parametrize("mode,table", [("advanced", True), ("basic", False)])
def test_advanced_or_one_delivery_unchanged(make, monkeypatch, mode, table):
    """ADVANCED with a table, or BASIC with one delivery: New heir, Delete
    and Import go on as before, no sentence."""
    s, rbw = make(mode, table)
    # ADVANCED with a table asks to open the table after a change: "No".
    monkeypatch.setattr(rbw, "ask_deliveries", lambda text: False)
    rbw.new_heir_dialog()
    assert rbw.opened == [None]
    rbw.heir_list_widget.delete_heirs(["john"])
    assert "john" not in rbw.heirs
    ran = fake_import(monkeypatch, rbw, {"carol": [ADDR["carol"], "10%", D1]})
    rbw.import_heirs()
    assert ran and "carol" in rbw.heirs
    assert BLOCKED not in rbw.messages


# ------------------------------------------------------------ the message

def test_message_help_says_delivery_1():
    """The "?" of the message names delivery 1."""
    assert DELIVERY_1 in message_help_text()


@pytest.mark.parametrize("table", [True, False])
def test_message_window_says_delivery_1(make, monkeypatch, table):
    """The message window (BASIC: never blocked) has the grey line only in
    a will in several deliveries; its "?" has the sentence always."""
    s, rbw = make("basic", table)
    seen = {}

    def steps(d):
        """Read the open window."""
        seen["labels"] = [w.text() for w in d.findChildren(QLabel)]
        seen["help"] = [str(getattr(b, "help_text", ""))
                        for b in d.findChildren(QToolButton)]
        seen["field"] = bool(d.findChildren(MessageEdit))

    script(monkeypatch, steps)
    assert rbw.message_dialog() is False
    assert seen["field"]
    assert (DELIVERY_1 in seen["labels"]) is table
    assert any(DELIVERY_1 in h for h in seen["help"])
    assert rbw.messages == []
