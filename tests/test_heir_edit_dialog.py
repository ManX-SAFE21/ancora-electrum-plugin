"""ONE way to edit an heir: the "Edit heir" dialog (#135, owner's ZIP test).

Owner: he renamed "erede test" to "erede test modificanome" (the Check saw
a rename), then renamed it back with the Edit dialog: the wallet then had
BOTH heirs at the same address, the next will would have given that
address a second share, and the summary box said "renamed".

Root cause: ``BalWindow.new_heir_dialog`` saved the heir under the new name
and never removed the old one (``set_heir`` only adds); the List's inline
cell editing renamed by delete + add. Now: no inline editing; a double
click and right click > Edit open the same dialog; a rename removes the
old name AFTER the new one is saved; a name used by another heir is
refused; the dialog says what an edit means ("Only the name changes: no
need to sign" / "You are changing who receives: …").

The real ``BalWindow`` and its real dialog are used (only the dialog's
``exec`` is scripted: it types the new name and presses OK) on a flowkit
wallet: three fake will-executors with their own fee addresses, the test
password, a fixed clock; the wallet counts as synchronized.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_heir_edit_dialog.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit

from bal.core.heirs import Heirs

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"


@pytest.fixture
def make(monkeypatch):
    """A will in force at three will-executors, and the REAL window."""
    from test_gui_window_init_migration import real_window
    fk.ensure_event_loop()
    made = []

    def _make():
        """``(session, real window)``."""
        s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.check()
        monkeypatch.setattr(s.plugin, "get_window_title", lambda t: t, raising=False)
        rbw = real_window(s, monkeypatch)
        rbw.init_will()
        return s, rbw

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def dialog(monkeypatch, type_name=None, seen=None):
    """Script the dialog's ``exec``: type ``type_name`` in the Name field,
    keep its hint in ``seen``, press OK once (then Cancel)."""
    from bal.gui.qt import window as window_mod
    calls = {"n": 0}

    def fake_exec(self):
        if calls["n"]:
            return False
        calls["n"] += 1
        edits = self.findChildren(QLineEdit)
        if type_name is not None:
            edits[0].setText(type_name)
        if seen is not None:
            seen.extend(w.text() for w in self.findChildren(QLabel) if w.text())
        return True

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)


def rename(rbw, monkeypatch, old, new, seen=None):
    """Double click on ``old`` in the List, type ``new``, OK."""
    dialog(monkeypatch, new, seen)
    rbw.new_heir_dialog(old)


def tab(s):
    """The Heirs tab as saved: ``{name: (address, amount)}``."""
    return {n: (h[0], h[1]) for n, h in Heirs(s.wallet).items()}


def check(s):
    """Press Check (the flowkit window reads the saved heirs)."""
    s.bw.heirs = Heirs(s.wallet)
    s.user.asked.clear()
    return s.check(cancel_on=("sign your will",))._report().snapshot()


@pytest.mark.parametrize("names", [("bob renamed", "bob"), ("bob renamed", "bob 3")])
def test_owner_sequence_one_heir_renamed_once(make, monkeypatch, names):
    """A -> B -> A and A -> B -> C with the real dialog: one heir at bob's
    address every time, each Check says "renamed" once and asks nothing,
    the real shares do not change, the next Check is clean."""
    s, rbw = make()
    address, amount = tab(s)["bob"]
    previous = "bob"
    for name in names:
        hints = []
        rename(rbw, monkeypatch, previous, name, hints)
        assert "Only the name changes: no need to sign." in hints
        assert tab(s) == {"alice": tab(s)["alice"], name: (address, amount)}
        snap = check(s)
        assert snap["boxes"]["changes"] == [
            "Heir renamed: {} → {}: only the name changes, the will stays the "
            "same.".format(previous, name)]
        rows = [list(r) for r in snap["changes"]]
        assert [r[0] for r in rows] == ["Heirs"] and "Real shares" not in str(rows)
        assert s.user.asked == []
        previous = name
    snap = check(s)
    assert snap["boxes"]["changes"] == [] and "changes" not in snap
    assert s.user.asked == []


def test_a_name_used_by_another_heir_is_refused(make, monkeypatch):
    """A rename to "alice", and a new heir called "alice": refused, the
    heirs stay as they were."""
    s, rbw = make()
    before = tab(s)
    errors = []
    monkeypatch.setattr(rbw, "show_error", lambda text, *a, **k: errors.append(text))
    rename(rbw, monkeypatch, "bob", "alice")
    dialog(monkeypatch, "alice")
    rbw.new_heir_dialog()
    assert errors == ["An heir named alice already exists."] * 2
    assert tab(s) == before


def test_address_change_hint(make, monkeypatch):
    """A new address: the dialog says the will must be signed again."""
    from bal.gui.qt.window import edit_hint
    assert edit_hint(("bob", "a1", "40%"), ("bob", "a2", "40%")) == (
        "You are changing who receives: the will must be signed again.")
    assert edit_hint(("bob", "a1", "40%"), ("bob", "a1", "50%")) == (
        "The share changes: the will must be signed again.")
    assert edit_hint(("bob", "a1", "40%"), ("bob 2", "a1", "40%")) == (
        "Only the name changes: no need to sign.")
    assert edit_hint(("bob", "a1", "40%"), ("bob", "a1", "40%")) == ""


def test_no_inline_editing_and_one_dialog(make, monkeypatch):
    """The List's cells are not editable; a double click and right click >
    Edit open the same dialog for the same heir."""
    from PyQt6.QtWidgets import QMenu

    s, rbw = make()
    lst = rbw.heir_list_widget
    assert list(lst.editable_columns) == []
    model = lst.model()
    for row in range(model.rowCount()):
        for col in range(model.columnCount()):
            assert not model.item(row, col).isEditable()
    opened = []
    monkeypatch.setattr(rbw, "new_heir_dialog", lambda key=None: opened.append(key))
    row = next(r for r in range(model.rowCount())
               if model.item(r, lst.Columns.NAME).text() == "bob")
    idx = model.index(row, lst.Columns.NAME)
    lst.on_double_click(idx)
    lst.setCurrentIndex(idx)
    lst.selectionModel().select(idx, lst.selectionModel().SelectionFlag.ClearAndSelect)
    actions = {}

    def fake_menu_exec(menu, *a, **k):
        # Press "Edit" while the menu exists (Qt deletes it afterwards).
        actions.update({a.text(): a for a in menu.actions()})
        actions["Edit"].trigger()

    monkeypatch.setattr(QMenu, "exec", fake_menu_exec)
    lst.create_menu(lst.visualRect(idx).center())
    names = list(actions)
    assert "Edit" in names and "Delete" in names
    assert not any(t.startswith("Edit ") for t in names)
    assert opened == ["bob", "bob"]


def test_an_address_used_by_another_heir_is_refused(make, monkeypatch):
    """bob's address typed for a new heir, and for alice: refused, the
    heirs stay as they were; bob may keep his own address (an edit)."""
    from bal.gui.qt import window as window_mod
    s, rbw = make()
    before = tab(s)
    bob_address = before["bob"][0]
    errors = []
    monkeypatch.setattr(rbw, "show_error", lambda text, *a, **k: errors.append(text))

    def type_in(name, address):
        calls = {"n": 0}

        def fake_exec(self):
            if calls["n"]:
                return False
            calls["n"] += 1
            edits = self.findChildren(QLineEdit)
            edits[0].setText(name)
            edits[1].setText(address)
            return True

        monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)

    type_in("carol", bob_address)
    rbw.new_heir_dialog()
    type_in("alice", bob_address)
    rbw.new_heir_dialog("alice")
    assert errors == ["This address is already used by the heir bob."] * 2
    assert tab(s) == before
    type_in("bob", bob_address)
    rbw.new_heir_dialog("bob")
    assert errors == ["This address is already used by the heir bob."] * 2


def test_an_old_wallet_with_two_heirs_on_one_address(make, monkeypatch):
    """Two heirs already on one address: the Heirs tab and the wizard's
    heirs page say so; nothing is changed and the Check is not blocked."""
    from bal.gui.qt.lists import same_address_text
    s, rbw = make()
    stored = Heirs(s.wallet)
    stored["bob 2"] = list(stored["bob"])
    before = tab(s)
    rbw.heirs = Heirs(s.wallet)
    lst = rbw.heir_list_widget
    lst.create_toolbar(s.config)
    lst.update()
    assert lst.same_address_label.text() == (
        "bob and bob 2 have the same address: keep only one.")
    assert same_address_text(rbw.heirs) == lst.same_address_label.text()
    snap = check(s)
    assert snap["steps"][0][1] == "ok"
    assert tab(s) == before


def test_an_invalid_address_in_plain_words(make, monkeypatch):
    """An invalid address in the real dialog: the translated two lines (the
    same helper as the command line), not "not an address,…"."""
    from bal.gui.qt import window as window_mod
    s, rbw = make()
    errors = []
    monkeypatch.setattr(rbw, "show_error", lambda text, *a, **k: errors.append(text))
    calls = {"n": 0}

    def fake_exec(self):
        if calls["n"]:
            return False
        calls["n"] += 1
        edits = self.findChildren(QLineEdit)
        edits[0].setText("carol")
        edits[1].setText("bc1qq74nedxy")
        return True

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)
    before = tab(s)
    rbw.new_heir_dialog()
    assert errors == [
        "Not a valid address for this network: bc1qq74nedxy\n"
        "Check that you copied all of it, with no extra or missing characters."]
    assert tab(s) == before


def test_owner_sequence_with_a_history_rename_back(make, monkeypatch):
    """Owner's ZIP test (A -> B -> A on a wallet with history): older
    versions keep the original name. Rename, Check, Check, rename back,
    Check: "renamed" once, no password, no delivery change; the next
    Check is clean; a share change still rebuilds."""
    s, rbw = make()
    s.bw.heirs = Heirs(s.wallet)
    s.set_heirs(alice="60%", bob="50%")
    s.check()                                   # version 2 (bob 50%)
    s.set_fee(14)
    s.check()                                   # version 3
    rbw.heirs = Heirs(s.wallet)
    rename(rbw, monkeypatch, "bob", "bob2")
    snap = check(s)
    assert snap["boxes"]["changes"][0].startswith("Heir renamed: bob → bob2")
    assert s.user.asked == []
    snap = check(s)
    assert snap["boxes"]["changes"] == [] and s.user.asked == []
    rename(rbw, monkeypatch, "bob2", "bob")
    snap = check(s)
    assert snap["boxes"]["changes"] == [
        "Heir renamed: bob2 → bob: only the name changes, the will stays the same."]
    assert [r[0] for r in snap["changes"]] == ["Heirs"]       # no delivery row
    assert s.user.asked == []
    snap = check(s)
    assert snap["boxes"]["changes"] == [] and "changes" not in snap
    stored = Heirs(s.wallet)
    stored["bob"] = [stored["bob"][0], "40%", stored["bob"][2]]
    check(s)
    assert ["password", "Sign your will"] in s.user.asked


def test_one_unmatched_item_does_not_block_the_others():
    """A rename is decided item by item: an item holding an old name of
    another heir (not in the Heirs tab, no heir at its address) does not
    stop the rename of the others; amounts compare by value."""
    from types import SimpleNamespace

    from bal.core.will import Will

    def item(**heirs):
        return SimpleNamespace(heirs=dict(heirs), get_status=lambda f: f == "VALID")

    a, b, c = "bc1qalice", "bc1qbob", "bc1qcarol"
    will = {"x": item(alice=[a, "60%", 1], bob2=[b, "20%", 1]),
            "y": item(alice=[a, "60%", 1], bob2=[b, "20.0%", 1], carol=[c, "10%", 1])}
    tab = {"alice": [a, "60%", 1], "bob": [b.upper().replace("BC1Q", "bc1q"), "20%", 1]}
    assert Will.sync_heir_names(will, tab) == [("bob2", "bob")]
    assert "bob" in will["x"].heirs and "bob" in will["y"].heirs
    assert "carol" in will["y"].heirs


def test_anticipation_leftover_is_dropped(make, monkeypatch):
    """Owner's ZIP test: rename A -> B, Check; a rebuild cancelled at the
    password left a draft dated ONE day earlier and that day as the stored
    date (here: a share change, Check, Cancel); the change is undone: the
    Check asks nothing, says the will is in force, the stored date is the
    will in force's again, and the next Check is clean. Nothing signed or
    sent is touched."""
    from bal.engine import context as engine_context
    s, rbw = make()
    rename(rbw, monkeypatch, "bob", "bob2")
    check(s)
    in_force = engine_context.in_force_delivery(s.bw)
    signed = {w: dict((k, v[1]) for k, v in i.STATUS.items())
              for w, i in s.bw.willitems.items() if i.get_status("COMPLETE")}
    stored = Heirs(s.wallet)
    stored["bob2"] = [stored["bob2"][0], "30%", stored["bob2"][2]]
    snap = check(s)                                   # rebuild, Cancel at Sign
    assert ["password", "Sign your will"] in s.user.asked
    assert s.wallet.db.get_dict("will_settings")["locktime"] == in_force - 86400
    stored = Heirs(s.wallet)
    stored["bob2"] = [stored["bob2"][0], "40%", stored["bob2"][2]]
    snap = check(s)
    assert s.user.asked == []
    assert snap["result"][0] == "ok"
    assert s.wallet.db.get_dict("will_settings")["locktime"] == in_force
    assert engine_context.in_force_delivery(s.bw) == in_force
    for wid, before in signed.items():
        assert dict((k, v[1]) for k, v in s.bw.willitems[wid].STATUS.items()) == before
    snap = check(s)
    assert s.user.asked == [] and snap["boxes"]["changes"] == [] and "changes" not in snap


def test_a_date_chosen_earlier_by_the_owner_still_asks(make, monkeypatch):
    """A delivery date the owner moved earlier himself (a week): it is not
    a leftover, the Check still prepares and asks to sign it, also after a
    Cancel."""
    s, rbw = make()
    in_force = s.wallet.db.get_dict("will_settings")["locktime"]
    s.set_date(in_force - 7 * 86400)
    check(s)
    assert ["password", "Sign your will"] in s.user.asked
    check(s)
    assert ["password", "Sign your will"] in s.user.asked
