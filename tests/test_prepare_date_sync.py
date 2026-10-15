"""Prepare moves the stored delivery date with the rebuilt will (CHANGELOG #142).

Found in the owner's ZIP test of #141 (2026-10-15, his wallet): he changed
an heir's percentage, pressed Prepare and Sign in the WILL tab, then Check.

* The rebuild spends the coins of the will in force with other amounts, so
  the new will is dated one day EARLIER (the anticipation rule).
* The Check, the command line and the automatic rebuild move the stored
  date with it (``build.sync_delivery_date``); Prepare did not (divergence
  D14).
* So Prepare's own check right after the build found every heir at the old
  date: "Error: <heir> / Please, check your heirs, locktime and threshold!".
* The next Check read the old stored date as a POSTPONE of a signed will
  and offered a paid on-chain invalidation (the owner cancelled it and set
  the date by hand).

Now Prepare moves the stored date (only EARLIER, only a fixed date, as the
others) and reads the delivery state again before its check.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three
fake will-executors, each with its own fee address; throw-away test wallet
with a password; fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_prepare_date_sync.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core.messages import will_change_message
from bal.core.will import HeirNotFoundException

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets that have one.
TEST_PASSWORD = "flowkit-test"


@pytest.fixture
def flow_session(monkeypatch):
    """``tests/flowkit.py`` sessions with Electrum's asyncio loop."""
    fk.ensure_event_loop()
    made = []

    def make(**kw):
        """Start one session with three will-executors; close it later."""
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _sent(make, mode):
    """A will signed, sent and verified at ``fk.D0``."""
    s = make(mode=mode, locktime=fk.D0, password=TEST_PASSWORD)
    s.check()
    assert int(s.bw.will_settings["locktime"]) == fk.D0
    return s


def _valid_dates(s):
    """The delivery dates of the VALID items."""
    return {int(w.tx.locktime) for w in s.bw.willitems.values()
            if w.get_status("VALID")}


def _errors(s):
    """The error popups of the window."""
    return [text for kind, text in s.bw.popups if kind == "error"]


def _prepare_sign_check(s):
    """Prepare and Sign in the WILL tab, then Check (the owner's steps)."""
    s.bw.popups = []
    s.prepare()
    prepared = list(s.bw.popups)
    s.menu_sign()
    dlg = s.check()
    return prepared, dlg


@pytest.mark.parametrize("mode", ["advanced", "basic"])
def test_new_shares_prepare_moves_the_date(flow_session, mode):
    """The owner's case: new percentages, Prepare, Sign, Check. No error
    popup, the stored date moves one day earlier with the will, no
    invalidation, the will is sent; the next Check is clean."""
    s = _sent(flow_session, mode)
    s.set_heirs(alice="70%", bob="30%")
    prepared, dlg = _prepare_sign_check(s)
    assert not [t for k, t in prepared if k == "error"], prepared
    assert any("rebuilt" in t for k, t in prepared if k == "message"), prepared
    earlier = fk.D0 - fk.DAY
    assert int(s.bw.will_settings["locktime"]) == earlier
    assert _valid_dates(s) == {earlier}
    assert s.world.broadcast_log == []
    assert not any("Postponed" in str(line) for line in dlg.history)
    pushed = len(s.world.pushed)
    s.check()
    assert s.world.broadcast_log == [] and len(s.world.pushed) == pushed
    assert int(s.bw.will_settings["locktime"]) == earlier


def test_new_coin_prepare_moves_the_date(flow_session):
    """New funds (the D14 scenario): Prepare moves the date as the Check."""
    s = _sent(flow_session, "advanced")
    s.add_coin()
    prepared, _dlg = _prepare_sign_check(s)
    assert not [t for k, t in prepared if k == "error"], prepared
    assert int(s.bw.will_settings["locktime"]) == fk.D0 - fk.DAY
    assert s.world.broadcast_log == []


def test_earlier_date_chosen_is_kept(flow_session):
    """A date the owner moved earlier is the will's date: Prepare keeps it
    (the rule moves the stored date only to an anticipated will)."""
    s = _sent(flow_session, "advanced")
    chosen = fk.D0 - 10 * fk.DAY
    s.set_date(chosen)
    prepared, _dlg = _prepare_sign_check(s)
    assert not [t for k, t in prepared if k == "error"], prepared
    assert int(s.bw.will_settings["locktime"]) == chosen
    assert _valid_dates(s) == {chosen}
    assert s.world.broadcast_log == []


def test_error_popup_names_the_cause(flow_session, monkeypatch):
    """When the will built by Prepare is still not coherent, the popup gives
    the cause in the Check's words, not "Error:<heir name>"."""
    s = _sent(flow_session, "advanced")
    s.set_heirs(alice="70%", bob="30%")
    real = s.bw.check_will
    calls = []
    error = HeirNotFoundException("alice", HeirNotFoundException.HEIR_AMOUNT)

    def check_will(*a, **kw):
        """The first check is the real one; the one after the build fails."""
        calls.append(1)
        if len(calls) == 1:
            return real(*a, **kw)
        raise error

    monkeypatch.setattr(s.bw, "check_will", check_will)
    s.bw.popups = []
    s.prepare()
    errors = _errors(s)
    assert errors == [will_change_message(error) + "\n"
                      + "Please, check your heirs, locktime and threshold!"]
    assert not errors[0].startswith("Error:")
