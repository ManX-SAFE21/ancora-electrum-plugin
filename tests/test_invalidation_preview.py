"""The invalidation step of the Check window (#135 follow-up).

Owner (ZIP test of #135): the user must see what is invalidated before
signing, and the box must be usable: shown while Electrum's modal password
window was already on top, its button could not be clicked. Now the Check
window first shows the box "Invalidation of the old will" (the amount
moved and that it goes to a new address of THIS wallet, the network fee
and its rate, the wills it makes useless, at most 3 lines) with "Show the
transaction" (Electrum's transaction window on a copy, read-only) and
"Invalidate now"; the password is asked only after "Invalidate now".
Cancel (at the step or at the password) still puts the date back (#134
option B) and keeps the box.

``tests/flowkit.py`` wallets: three fake will-executors with their own fee
addresses, the test password, a fixed clock (2026-10-10 12:00 UTC); the
wallet counts as synchronized.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_invalidation_preview.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel

from bal.core import invalidation_preview

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
INVALIDATE = "Invalidate your old will"


@pytest.fixture
def make(monkeypatch):
    """A wallet with a will sent and verified by three will-executors, then
    a later delivery date (a postpone)."""
    fk.ensure_event_loop()
    made = []

    def _make():
        """The session (see the fixture)."""
        s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.check()
        s.set_date(fk.LATER)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def at_step(s, monkeypatch, look):
    """Call ``look(dialog)`` when the invalidation step shows its buttons
    (before any password), then let the scripted owner answer."""
    orig = fk.FlowDialog._add_invalidation_buttons

    def spy(self):
        """Look at the window at the step, then press as scripted."""
        look(self)
        return orig(self)

    monkeypatch.setattr(fk.FlowDialog, "_add_invalidation_buttons", spy)


def texts_of(snapshot):
    """The texts of the report view drawing ``snapshot``."""
    from bal.gui.qt.widgets import BuildReportView
    view = BuildReportView()
    view.render(snapshot)
    return [w.text() for w in view.findChildren(QLabel)]


def test_box_and_buttons_before_any_password(make, monkeypatch):
    """The step shows the box (amount, own address, fee and rate, the will
    made useless) and waits for "Invalidate now": no password asked yet.
    Cancel there: not invalidated, the date goes back, the box stays and
    "Show the transaction" still opens the (never signed) transaction."""
    s = make()
    found = {}

    def look(dlg):
        """Read the window at the step."""
        found["inv"] = dlg._report().snapshot().get("invalidation")
        found["asked"] = [k for k, _t in s.user.asked]
        found["texts"] = texts_of(dlg._report().snapshot())

    at_step(s, monkeypatch, look)
    dlg = s.check(cancel_on=("invalidat",))
    inv = found["inv"]
    assert "password" not in found["asked"]
    assert inv["amount"] == 1_000_000                 # the whole coin
    assert inv["mine"] is True and s.wallet.is_mine(inv["address"])
    assert inv["fee"] > 0 and inv["received"] == inv["amount"] - inv["fee"]
    assert inv["rate"] > 0
    assert inv["wills"] == [[1, fk.D0, 3]]            # #1, its date, 3 copies
    texts = found["texts"]
    assert "<b>Invalidation of the old will</b>" in texts
    short = invalidation_preview.short_address(inv["address"])
    assert "a new address of your wallet: <b>{}</b>".format(short) in "".join(texts)
    assert any("{} sat".format(inv["fee"]) in t for t in texts)
    assert any("3 copies at the will-executors" in t for t in texts)
    # Cancelled at the step: nothing broadcast, the later date went back,
    # the box is still there and its button still works.
    assert not [k for k, _t in s.world.broadcast_log if "invalidation" in k]
    assert s.wallet.db.get_dict("will_settings")["locktime"] == fk.D0
    assert dlg._report().snapshot().get("invalidation") == inv
    shown = []
    monkeypatch.setattr(s.bw, "show_transaction",
                        lambda tx=None, txid=None, parent=None: shown.append(tx))
    dlg._show_invalidation_tx()
    assert shown and shown[0].txid() and not shown[0].is_complete()


def test_show_the_transaction_is_the_signed_one(make, monkeypatch):
    """"Show the transaction" at the step opens Electrum's window on a COPY
    of the very transaction then signed and broadcast after "Invalidate
    now" (same txid), never on the real object, and hides the window's
    Sign / Broadcast buttons."""
    s = make()
    shown = []

    class Button:
        """A Sign / Broadcast button of Electrum's window."""
        visible = True

        def setVisible(self, value):  # noqa: N802 - Qt name
            self.visible = value

    class TxWindow:
        """What Electrum's transaction window returns."""
        sign_button = Button()
        broadcast_button = Button()

    def show_transaction(tx=None, txid=None, parent=None):
        """Record the transaction instead of opening the window.

        Owner's ZIP test (#135): the Check window was passed as ``parent``
        and Electrum's TxDialog crashed ("no attribute 'config'"): its
        parent must be the Electrum main window, i.e. the default (None).
        """
        assert parent is None, parent
        shown.append(tx)
        return TxWindow

    monkeypatch.setattr(s.bw, "show_transaction", show_transaction)
    real = {}

    def look(dlg):
        """Press "Show the transaction" at the step."""
        real["tx"] = dlg._pending_invalidation
        assert dlg._show_invalidation_tx() is TxWindow

    at_step(s, monkeypatch, look)
    s.check()
    (copy,) = shown
    assert copy is not real["tx"]
    invalidations = [t for kind, t in s.world.broadcast_log if "invalidation" in kind]
    assert invalidations and copy.txid() == invalidations[0] == real["tx"].txid()
    assert not TxWindow.sign_button.visible and not TxWindow.broadcast_button.visible
    assert ["button", "Invalidate now"] in s.user.asked
    assert ["password", "Invalidate your old will"] in s.user.asked


def test_cancel_at_the_password_still_reverts(make, monkeypatch):
    """"Invalidate now", then Cancel at "Invalidate your old will": nothing
    is broadcast, the later date goes back (option B, #134), the box stays."""
    s = make()
    dlg = s.check(cancel_on=("invalidate your old will",))
    assert ["button", "Invalidate now"] in s.user.asked
    assert not [k for k, _t in s.world.broadcast_log if "invalidation" in k]
    assert s.wallet.db.get_dict("will_settings")["locktime"] == fk.D0
    assert dlg._report().snapshot().get("invalidation")


def test_long_list_shows_three_then_the_rest():
    """Seven wills made useless: the 3 earliest, then "and 4 older versions"."""
    from bal.gui.qt.widgets import BuildReportView
    wills = [[n, fk.D0 + n * 86400, 2] for n in range(7, 0, -1)]
    wills.sort(key=lambda w: w[1])
    rows = BuildReportView().invalidation_rows(
        {"amount": 1000, "address": "tb1qxyz", "mine": True, "fee": 10,
         "rate": 1.0, "wills": wills})
    values = [v for _label, v, _note in rows]
    assert sum(1 for v in values if "copies at the will-executors" in v) == 3
    assert values[-1] == "and 4 older versions"
    assert "<b>#1</b>" in values[3]
