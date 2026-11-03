"""Postponing on a watch-only or multisig wallet: the invalidation is signed elsewhere (#227).

The owner's setup: the online computer holds a watch-only wallet (or one
cosigner of a multisig), the keys are on a second computer. A will signed
there and sent; then the delivery date moved later: a postpone, so the old
will must be invalidated on the blockchain first. Before #227 the Check
asked "Invalidate now" and then stopped with "Invalidating old will: Error:
not tx" (watch-only) or "Error: tx not complete" (multisig), said nothing
about signing on the other computer, and put the date back. Probed on test
wallets while preparing the test of audit item 33 (2026-11-03).

Now the Check opens the invalidation in Electrum's transaction window (with
its Export, as Tools > Invalidate), labelled as every invalidation; the row
and the result say where to sign it; the later date stays. Once it is signed
there and on the chain, the next Check prepares the will with the new date.
A wallet that signs alone is unchanged (it signs and broadcasts by itself;
a signing that fails still puts the date back, ``test_cancelled_postpone``).

Test wallets: throw-away keys made at every run (the wallets of
``tests/test_engine_merge.py``): watch-only signed offline, 2-of-3 multisig
watch-only and with one cosigner's keys (with password); three fake
will-executors (``tests/flowkit.py``), each with its own fee address.
Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_postpone_sign_elsewhere.py
"""

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import tx_from_any
from electrum.util import TxMinedInfo
from test_engine_merge import (  # noqa: F401 - world is the fixture
    PW,
    _export,
    _merge,
    _sign_offline,
    world,
)

from bal.engine.invalidate import INVALIDATION_LABEL

D = fk.ts(2030, 3, 1)
LATER = D + 10 * fk.DAY
INVALIDATING = "Invalidating old will"
RESULT = ("Postponing: the old will stays in force until its invalidation is "
          "signed on the other computers and broadcast. Then press Check again: "
          "the will with the new date is prepared then.")


def _rows(s):
    """The last Check's log, as plain text (no colours)."""
    return [re.sub("<[^>]+>", "", str(line)) for line in s.last.history]


def _last(rows, label):
    """The last value written on the row *label* ("label:\\tvalue")."""
    values = [r.split("\t", 1)[1] for r in rows if r.startswith(label + ":\t")]
    return values[-1] if values else None


def _in_force(world, kind):  # noqa: F811
    """A will for :data:`D` signed on the other computers and sent, on a
    wallet of *kind* (``watch``, ``msWatch``, ``msA``)."""
    folder = tempfile.mkdtemp(prefix="bal_227_")
    s = world(kind)
    s.set_heirs(alice="50%", bob="50%")
    s.set_date(D)
    s.check()
    path = _export(s, folder, "will.json")
    if kind == "watch":
        signed = _sign_offline(world("keys"), path, folder, "signed.json")
    elif kind == "msWatch":
        a = _sign_offline(world("msA"), path, folder, "will_A.json")
        signed = _sign_offline(world("msB"), a, folder, "will_AB.json")
    else:
        signed = _sign_offline(world("msB"), path, folder, "will_AB.json")
    _merge(s, signed)
    s.check()
    old = {wid for wid, w in s.bw.willitems.items()
           if w.get_status("VALID") and w.get_status("PUSHED")}
    assert len(old) == 3
    return s, old


def _shown(s, monkeypatch):
    """Electrum's transaction window: records the transactions it shows."""
    shown = []

    def show_transaction(tx=None, txid=None, parent=None):
        """The window with Export; the owner takes the file away."""
        shown.append(tx)

    monkeypatch.setattr(s.bw, "show_transaction", show_transaction)
    return shown


def _signatures(tx):
    """How many signatures the (first) input of *tx* holds."""
    txin = tx.inputs()[0]
    return len(txin.sigs_ecdsa)


@pytest.mark.parametrize("kind,row", [
    ("watch", "To sign on the offline computer"),
    ("msWatch", "To sign on the cosigners' computers"),
    ("msA", "To sign on the cosigners' computers"),
])
def test_the_check_hands_the_invalidation_to_the_other_computers(
        world, monkeypatch, kind, row):  # noqa: F811
    """No error: the transaction window, the row, the result; the date stays."""
    s, old = _in_force(world, kind)
    shown = _shown(s, monkeypatch)
    s.set_date(LATER)
    s.check()
    rows = _rows(s)
    assert _last(rows, INVALIDATING) == row
    assert not any("Error" in r for r in rows), rows
    assert s.last._report().snapshot()["result"] == ("warn", "<b>{}</b>".format(RESULT))
    # what the window shows: the invalidation of the coin of the will in force
    assert len(shown) == 1
    tx = shown[0]
    spent = {i.prevout.to_str() for w in old for i in s.bw.willitems[w].tx.inputs()}
    assert {i.prevout.to_str() for i in tx.inputs()} == spent
    assert all(s.wallet.is_mine(o.address) for o in tx.outputs())
    assert s.wallet.get_label_for_txid(tx.txid()) == INVALIDATION_LABEL
    # the message says where to sign it
    message = [text for kind_, text in s.bw.popups if kind_ == "message"][-1]
    assert ("offline computer" if kind == "watch" else "cosigners' computers") in message
    # nothing broadcast, the later date kept, the will in force untouched
    assert not s.world.broadcast_log
    assert s.wallet.db.get_dict("will_settings")["locktime"] == LATER
    for wid in old:
        assert s.bw.willitems[wid].get_status("VALID")


def test_the_instructions_can_be_copied(world, monkeypatch):  # noqa: F811
    """The numbered steps in the message, and "Copy the instructions" puts
    them in the clipboard (owner: to paste and print them)."""
    from PyQt6.QtWidgets import QApplication, QPushButton

    s, _old = _in_force(world, "watch")
    _shown(s, monkeypatch)
    s.set_date(LATER)
    s.check()
    assert "instructions-message" in s.last.events
    message = s.last._elsewhere_message
    assert [line[:2] for line in message.splitlines() if line[:1].isdigit()] == [
        "1.", "2.", "3.", "4.", "5.", "6."]
    assert "Tools > Load transaction" in message
    button = QPushButton()
    s.last._copy_instructions(button)
    assert QApplication.clipboard().text() == message
    assert button.text() == "Instructions copied"


def test_the_message_has_its_own_copy_button():
    """The real message: "Copy the instructions" copies and leaves it open;
    "OK" closes it (owner: the button in the message too)."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

    from bal.gui.qt.dialogs import BalBuildWillDialog

    class Host(QWidget):
        """The Check window's parts the message uses."""

        _elsewhere_message = "1. one\n2. two"
        _copy_instructions = BalBuildWillDialog._copy_instructions

    host = Host()
    seen = {}

    def press():
        """Copy, look, then OK."""
        box = next(w for w in QApplication.topLevelWidgets()
                   if isinstance(w, QMessageBox) and w.isVisible())
        copy = next(b for b in box.buttons()
                    if b.text() == "Copy the instructions")
        copy.click()
        seen["open"] = box.isVisible()
        seen["text"] = box.text()
        seen["copied"] = QApplication.clipboard().text()
        seen["label"] = copy.text()
        box.button(QMessageBox.StandardButton.Ok).click()

    QTimer.singleShot(0, press)
    BalBuildWillDialog._show_instructions(host, host._elsewhere_message)
    # the first line in bold on screen, the copy in plain text
    assert seen == {"open": True, "text": "<b>1. one</b><br>2. two",
                    "copied": "1. one\n2. two", "label": "Instructions copied"}


def test_the_multisig_cosigner_signs_its_part(world, monkeypatch):  # noqa: F811
    """With one cosigner's keys: the password, then one signature of two in
    the transaction the window shows."""
    s, _old = _in_force(world, "msA")
    shown = _shown(s, monkeypatch)
    s.set_date(LATER)
    s.check()
    assert "Invalidate your old will" in [t for _k, t in s.user.asked]
    assert not shown[0].is_complete() and _signatures(shown[0]) == 1


def test_signed_elsewhere_then_the_new_will(world, monkeypatch):  # noqa: F811
    """The whole road: signed on the offline computer, on the chain; the
    next Check prepares the will with the later date, to sign offline, and
    asks no invalidation again."""
    s, old = _in_force(world, "watch")
    shown = _shown(s, monkeypatch)
    s.set_date(LATER)
    s.check()
    keys = world("keys")
    tx = tx_from_any(str(shown[0]))
    keys.wallet.sign_transaction(tx, PW)
    assert tx.is_complete()
    s.wallet.adb.add_transaction(tx)
    s.wallet.db.put("stored_height", 300)
    s.wallet.adb.add_verified_tx(tx.txid(), TxMinedInfo(
        _height=101, timestamp=int(s.clock.now), txpos=1, header_hash="00" * 32))
    s.clock.travel(1)
    s.check()
    assert "Invalidate now" not in [t for _k, t in s.user.asked]
    new = {wid: w for wid, w in s.bw.willitems.items()
           if w.get_status("VALID") and wid not in old}
    assert len(new) == 3
    assert {int(w.tx.locktime) for w in new.values()} == {LATER}
    assert not any(s.bw.willitems[wid].get_status("VALID") for wid in old)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
