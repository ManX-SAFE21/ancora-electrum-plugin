"""An heir paid at an address of the wallet itself: a question (#197).

Owner's test of #194/#195 (2026-10-27): an heir address was copied from
the wallet's own Receive list. On the delivery date that heir's bitcoin
would stay in this wallet; and, while the will was unsigned, its local
copy paid the wallet and the Check saw "new funds" (#195). The owner's
idea: warn when an heir address belongs to the wallet itself, with a
question, never a block (he tests with his own addresses).

Now:

* the "New heir" / "Edit heir" window asks "Use it anyway?" (No by
  default) when the address entered or changed belongs to this wallet;
  Yes saves, No leaves the window with nothing saved; an edit that keeps
  the stored address asks nothing (owner: only when an address is entered
  or changed);
* the GUI import asks ONE question with the names; Yes imports the whole
  file, No imports nothing and says so;
* the command line asks nothing and saves as before; its answer has a
  "warning" line (owner's choice c1);
* the check itself: ``bal.core.heirs.is_own_address`` and
  ``own_address_heirs`` (receive and change addresses, a bech32 address in
  capitals, never an OP_RETURN message, never raises).

GUI: the REAL ``BalWindow`` dialog on flowkit wallets with three fake
will-executors, each with its own fee address (the fixtures of
``tests/test_heir_notes_and_address.py``). Command line: the real
controller on the offline fake wallet, three will-executors selected.
Throw-away keys only.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_own_address_heir.py
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum import bitcoin, crypto
from electrum.util import FileImportFailed
from test_cli_controller_offline import VALID_ADDRESS, FakeWallet, Plugin
from test_heir_notes_and_address import check_twice_clean, dialog, make  # noqa: F401

from bal.cli.controller import BalController
from bal.core.heirs import (
    Heirs,
    is_own_address,
    message_address,
    own_address_heirs,
)
from bal.core.plugin_base import BalPlugin

QUESTION = ("This address belongs to this same wallet: on the delivery date this "
            "heir's bitcoin would stay in this wallet instead of going to the "
            "heir. It is usually a mistake (an address copied from Receive). "
            "Use it anyway?")
IMPORT_QUESTION = ("These heirs of the file are paid at an address of this same "
                   "wallet: {}. On the delivery date their bitcoin would stay in "
                   "this wallet instead of going to them. It is usually a "
                   "mistake. Import the file anyway?")
TITLE = "Heir address"
WE = ["https://we-a.test", "https://we-b.test", "https://we-c.test"]


def foreign(seed):
    """An address of a throw-away key, never of the test wallet."""
    return bitcoin.public_key_to_p2wpkh(
        crypto.privkey_to_pubkey(bytes(range(seed, seed + 32))))


def own(s, index=5):
    """An unused receiving address of the test wallet itself."""
    address = s.wallet.get_receiving_addresses()[index]
    assert s.wallet.is_mine(address)
    return address


def answer(monkeypatch, *replies):
    """Script the #197 questions: each one is recorded, then answered."""
    from bal.gui.qt import window as window_mod
    asked = []

    def fake(parent, text, title=""):
        asked.append((text, title))
        return replies[len(asked) - 1]

    monkeypatch.setattr(window_mod, "ask_yes_no", fake)
    return asked


def gui_import(monkeypatch, rbw, path):
    """``import_heirs`` with Electrum's ``import_meta_gui`` as it works
    (only FileImportFailed is caught), without the file window. Returns the
    messages shown."""
    from bal.gui.qt import window as window_mod
    shown = []

    def fake_import(parent, title, importer, on_success):
        try:
            importer(str(path))
        except FileImportFailed as e:
            shown.append("critical: " + str(e))
        else:
            shown.append("Your heirs were successfully imported")
            on_success()

    monkeypatch.setattr(window_mod, "import_meta_gui", fake_import)
    monkeypatch.setattr(rbw, "show_message", lambda text, *a, **k: shown.append(text))
    rbw.import_heirs()
    return shown


# ------------------------------------------------------------- the check

def test_is_own_address(make):  # noqa: F811
    """Receive and change addresses of the wallet, also a bech32 one in
    capitals; never an address of someone else, a message, an empty text;
    a wallet that cannot tell gives False."""
    s, _rbw = make()
    assert is_own_address(s.wallet, own(s))
    assert is_own_address(s.wallet, s.wallet.get_change_addresses()[0])
    assert is_own_address(s.wallet, "  " + own(s).upper() + " ")
    alice = Heirs(s.wallet)["alice"][0]
    assert not s.wallet.is_mine(alice)
    assert not is_own_address(s.wallet, alice)
    assert not is_own_address(s.wallet, fk.HEIR_ADDR["carol"])
    assert not is_own_address(s.wallet, message_address("hello"))
    assert not is_own_address(s.wallet, "")

    class Broken:
        def is_mine(self, address):
            raise RuntimeError("no keys")

    assert not is_own_address(Broken(), own(s))


def test_own_address_heirs(make):  # noqa: F811
    """The names in order; a name saved with the same address is left out,
    one saved with another address is not; a broken entry is skipped."""
    s, _rbw = make()
    data = {"luca": [own(s, 5), "50%"], "carol": [fk.HEIR_ADDR["carol"], "20%"],
            "anna": [own(s, 6), "30%"], "bad": []}
    assert own_address_heirs(s.wallet, data) == ["luca", "anna"]
    stored = {"luca": [own(s, 5).upper(), "10%"], "anna": [foreign(9), "5%"]}
    assert own_address_heirs(s.wallet, data, stored=stored) == ["anna"]


# ------------------------------------------------------------ the window

def test_new_heir_at_own_address_yes_saves(make, monkeypatch):  # noqa: F811
    """Yes: the heir is saved at that address; one question, the #197
    text and title."""
    s, rbw = make()
    asked = answer(monkeypatch, True)
    dialog(monkeypatch, name="luca", address=own(s), amount="10%")
    rbw.new_heir_dialog()
    assert asked == [(QUESTION, TITLE)]
    assert Heirs(s.wallet)["luca"][0] == own(s)
    assert rbw.errors == []


def test_new_heir_at_own_address_no_saves_nothing(make, monkeypatch):  # noqa: F811
    """No: nothing saved, no error box; the window would show again."""
    s, rbw = make()
    asked = answer(monkeypatch, False)
    dialog(monkeypatch, name="luca", address=own(s), amount="10%")
    rbw.new_heir_dialog()
    assert asked == [(QUESTION, TITLE)]
    assert "luca" not in Heirs(s.wallet)
    assert rbw.errors == []


def test_address_of_someone_else_asks_nothing(make, monkeypatch):  # noqa: F811
    """An address not of this wallet: saved, no question (unchanged)."""
    s, rbw = make()
    asked = answer(monkeypatch)
    dialog(monkeypatch, name="carol", address=fk.HEIR_ADDR["carol"], amount="10%")
    rbw.new_heir_dialog()
    assert asked == []
    assert Heirs(s.wallet)["carol"][0] == fk.HEIR_ADDR["carol"]


def test_edit_keeping_the_address_asks_nothing(make, monkeypatch):  # noqa: F811
    """An heir saved at an address of the wallet (as the owner's, before
    #197): an edit of the amount asks nothing; a change to another address
    of the wallet asks again."""
    s, rbw = make()
    rbw.heirs["luca"] = [own(s, 5), "10%", rbw.heirs["alice"][2]]
    asked = answer(monkeypatch, True)
    dialog(monkeypatch, amount="20%")
    rbw.new_heir_dialog("luca")
    assert asked == []
    assert Heirs(s.wallet)["luca"][1] == "20%"
    dialog(monkeypatch, address=own(s, 6))
    rbw.new_heir_dialog("luca")
    assert asked == [(QUESTION, TITLE)]
    assert Heirs(s.wallet)["luca"][0] == own(s, 6)


def test_yes_then_the_checks_are_clean(make, monkeypatch):  # noqa: F811
    """The owner's flow: an heir added at an address of the wallet with
    Yes; the Check prepares the new will, the second Check has nothing to
    say or ask (no "new funds", #195)."""
    s, rbw = make()
    answer(monkeypatch, True)
    dialog(monkeypatch, name="luca", address=own(s), amount="10%")
    rbw.new_heir_dialog()
    check_twice_clean(s)


# ------------------------------------------------------------ the import

def test_import_no_imports_nothing(make, monkeypatch, tmp_path):  # noqa: F811
    """One question with the names of the file; No: no heir of the file
    imported, "Nothing was imported.", no "successfully imported"."""
    s, rbw = make()
    asked = answer(monkeypatch, False)
    path = tmp_path / "heirs.json"
    date = Heirs(s.wallet)["alice"][2]
    path.write_text(json.dumps({"luca": [own(s, 5), "10%", date],
                                "carol": [fk.HEIR_ADDR["carol"], "10%", date],
                                "anna": [own(s, 6), "10%", date]}))
    shown = gui_import(monkeypatch, rbw, path)
    assert asked == [(IMPORT_QUESTION.format("luca, anna"), TITLE)]
    assert shown == ["Nothing was imported."]
    assert sorted(Heirs(s.wallet)) == ["alice", "bob"]


def test_import_yes_imports_the_whole_file(make, monkeypatch, tmp_path):  # noqa: F811
    """Yes: the whole file is imported, as before #197."""
    s, rbw = make()
    asked = answer(monkeypatch, True)
    path = tmp_path / "heirs.json"
    date = Heirs(s.wallet)["alice"][2]
    path.write_text(json.dumps({"luca": [own(s, 5), "10%", date],
                                "carol": [fk.HEIR_ADDR["carol"], "10%", date]}))
    shown = gui_import(monkeypatch, rbw, path)
    assert asked == [(IMPORT_QUESTION.format("luca"), TITLE)]
    assert shown == ["Your heirs were successfully imported"]
    assert sorted(Heirs(s.wallet)) == ["alice", "bob", "carol", "luca"]


def test_import_of_heirs_already_saved_asks_nothing(make, monkeypatch, tmp_path):  # noqa: F811
    """The exported heirs imported again (luca saved at an address of the
    wallet, the same address in the file): no question."""
    s, rbw = make()
    rbw.heirs["luca"] = [own(s, 5), "10%", rbw.heirs["alice"][2]]
    asked = answer(monkeypatch)
    path = tmp_path / "heirs.json"
    path.write_text(json.dumps({k: list(v) for k, v in Heirs(s.wallet).items()}))
    shown = gui_import(monkeypatch, rbw, path)
    assert asked == []
    assert shown == ["Your heirs were successfully imported"]


# ------------------------------------------------------ the command line

class OwnWallet(FakeWallet):
    """The offline fake wallet, with the addresses it owns."""

    def __init__(self, owned):
        super().__init__()
        self.owned = set(owned)

    def is_mine(self, address):
        """True for the addresses given at creation."""
        return address in self.owned


@pytest.fixture
def cli():
    """The real command-line plugin, three will-executors selected."""
    with Plugin() as p:
        p.WILLEXECUTORS.set({BalPlugin.chainname: {
            url: {"url": url, "selected": True, "base_fee": 1000,
                  "address": VALID_ADDRESS, "status": 200, "info": "fake"}
            for url in WE
        }})
        yield p


def test_cli_warns_and_saves(cli, tmp_path):
    """No question and no new option (owner c1): the heir is saved and the
    answer has "warning" when the address belongs to the wallet; none for
    an address of someone else or an update that keeps the address."""
    c = BalController(cli, OwnWallet([foreign(1), foreign(2)]))
    out = c.heirs_add("luca", foreign(1), "50%")
    assert out["warning"] == ("The address of luca belongs to this wallet: on the "
                              "delivery date the bitcoin would stay in this wallet.")
    assert c.heirs["luca"][0] == foreign(1)
    assert "warning" not in c.heirs_add("carol", foreign(7), "50%")
    assert "warning" not in c.heirs_update("luca", amount="40%")
    out = c.heirs_update("carol", address=foreign(2))
    assert out["warning"].startswith("The address of carol belongs")
    assert c.heirs["carol"][0] == foreign(2)


def test_cli_import_warns_and_imports(cli, tmp_path):
    """The import takes the heirs as before; "warning" names those at an
    address of the wallet."""
    c = BalController(cli, OwnWallet([foreign(1), foreign(2)]))
    path = tmp_path / "heirs.json"
    path.write_text(json.dumps({"luca": [foreign(1), "10%", "1y"],
                                "carol": [foreign(7), "10%", "1y"],
                                "anna": [foreign(2), "10%", "1y"]}))
    out = c.heirs_import(str(path))
    assert out["imported"] == 3 and out["skipped"] == {}
    assert out["warning"] == ("The addresses of luca, anna belong to this wallet: "
                              "on the delivery date their bitcoin would stay in "
                              "this wallet.")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
