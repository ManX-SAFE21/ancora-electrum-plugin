"""An identical rebuild keeps the signed will: real flows (CHANGELOG #122).

The owner's wallet on 2026-10-11 (reproduced here): a will built from a
FIXED delivery date was signed, sent and verified; then the delivery
setting became the period giving the same day ("1054d", the Raw/Date
switch of advanced mode). At wallet close the check rebuilt identical
transactions and put fresh, unsigned copies in place of the signed ones;
the owner closed the password window and the will was saved unsigned.

Since #122 ``Will.update_will`` keeps the signed items when the rebuilt
transactions are the same: the check still says why it rebuilds, but
nothing is asked, signed, sent or broadcast, and the saved will is still
signed, sent and verified. The messages after a rebuild follow what is
really left to do (Prepare, command line).

Not covered here (CHANGELOG #123): with a period as delivery setting, an
old REPLACED will built from a fixed date is still compared with "today +
the period" and, two days later, taken for a postpone (paid invalidation).
#123 converts the periods to fixed dates once.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three
will-executors, throw-away test wallet with a password, fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_date_form_change_flow.py
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core.will import Will
from bal.engine import context

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets that have one.
TEST_PASSWORD = "flowkit-test"

# The state of a will that is signed, sent and verified.
SENT = ("VALID", "COMPLETE", "PUSHED", "CHECKED")


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


def _sent_will(make, mode, wills):
    """A will built from the fixed date ``fk.D0``, signed, sent, verified.

    ``wills=2``: then the heirs change and the Check sends a second will one
    day earlier; the first one is replaced but still held by the
    will-executors (the owner's wallet).
    """
    s = make(mode=mode, locktime=fk.D0, password=TEST_PASSWORD)
    s.check()
    if wills == 2:
        s.set_heirs(alice="50%", bob="30%")
        s.check()
    return s


def _valid(s):
    """The VALID will items by txid."""
    return {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}


def _period_giving(s, locktime):
    """The period ("Nd") that gives ``locktime`` today (the Date -> Raw
    switch of advanced mode, CHANGELOG #81)."""
    today = datetime.fromtimestamp(s.clock.now, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return "{}d".format((int(locktime) - int(today.timestamp())) // fk.DAY)


def _to_equal_period(s):
    """Write the delivery as the period giving the will's day today.

    Returns the period, the VALID txids and their signed transactions.
    """
    valid = _valid(s)
    assert valid and all(w.get_status(flag) for w in valid.values() for flag in SENT)
    period = _period_giving(s, Will.get_min_locktime(s.bw.willitems))
    s.set_date(period)
    return period, set(valid), {wid: str(w.tx) for wid, w in valid.items()}


def _reopen(s):
    """Electrum opens the wallet again: a new window reads the will saved in
    the wallet."""
    s.bw = fk.FlowWindow(s.plugin, s.wallet, s.user, s.world)
    context.load_willitems(s.bw)


def _rows(step):
    """Every row the step showed, in the Check dialogs it opened too."""
    rows = [fk.strip(r) for r in step.get("rows", [])]
    for d in step.get("dialogs_opened", []):
        rows += [fk.strip(r) for r in d["rows"]]
    return rows


def _assert_nothing_done(s, label):
    """The last step asked nothing, signed nothing, sent nothing and
    broadcast nothing (it may say that it rebuilt the will)."""
    step = s.recorder.steps[-1]
    assert step["questions"] == [], (label, step["questions"])
    assert step["pushed"] == [], (label, step["pushed"])
    assert step["broadcasts"] == [], (label, step["broadcasts"])
    rows = _rows(step)
    if any(r.startswith("Signing your will") for r in rows):
        assert "Signing your will: Nothing to do" in rows, (label, rows)
    return rows


def _assert_quiet(s, label):
    """Nothing done and the will found in order."""
    rows = _assert_nothing_done(s, label)
    assert "Checking your will: Ok" in rows, (label, rows)


def _assert_same_signed_will(s, txids, raw, period):
    """The same VALID transactions, still signed, sent and verified, with
    the period written in them."""
    valid = _valid(s)
    assert set(valid) == txids
    for wid, w in valid.items():
        assert all(w.get_status(flag) for flag in SENT), w.status
        assert str(w.tx) == raw[wid]
        recipes = {h[2] for n, h in w.heirs.items() if not n.startswith('w!ll3x3c"')}
        assert recipes == {period}


@pytest.mark.parametrize("mode", ["basic", "advanced"])
@pytest.mark.parametrize("wills", [1, 2])
def test_wallet_close_keeps_the_signed_will(flow_session, mode, wills):
    """The owner's sequence: the date written as the equal period, then the
    wallet closes and the owner would press Cancel at the password window.
    The closing check rebuilds identical transactions: nothing is asked,
    signed or sent; the saved will is still signed, sent and verified, with
    the period written in it."""
    s = _sent_will(flow_session, mode, wills)
    period, txids, raw = _to_equal_period(s)
    s.act("gui wallet close", s.bw.on_close, cancel_on=("sign your will",))
    rows = _assert_nothing_done(s, "wallet close")
    assert "Signing your will: Nothing to do" in rows, rows
    _reopen(s)
    _assert_same_signed_will(s, txids, raw, period)


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_one_will_stays_in_order_the_following_days(flow_session, mode):
    """One will (nothing replaced): after the close, the Checks of the
    following days find it in order and ask, send and pay nothing."""
    s = _sent_will(flow_session, mode, 1)
    period, txids, raw = _to_equal_period(s)
    s.act("gui wallet close", s.bw.on_close, cancel_on=("sign your will",))
    _reopen(s)
    date = Will.get_min_locktime(s.bw.willitems)
    for days in (1, 1, 5):
        s.clock.travel(days)
        s.check()
        _assert_quiet(s, "+{} day(s)".format(days))
        _assert_same_signed_will(s, txids, raw, period)
        assert Will.get_min_locktime(s.bw.willitems) == date


def test_check_keeps_the_signed_will(flow_session):
    """The same with the Check button instead of the wallet close."""
    s = _sent_will(flow_session, "basic", 2)
    period, txids, raw = _to_equal_period(s)
    s.check(cancel_on=("sign your will",))
    _assert_nothing_done(s, "Check")
    _assert_same_signed_will(s, txids, raw, period)


def test_prepare_says_the_will_is_in_force(flow_session):
    """Will menu > Prepare on the same case: the reason of the rebuild, then
    "Your will is in force" instead of "press Sign, then Broadcast"."""
    s = _sent_will(flow_session, "basic", 1)
    period, txids, raw = _to_equal_period(s)
    s.prepare()
    step = s.recorder.steps[-1]
    assert [p[1] for p in step["popups"]] == [
        'The delivery date of heir "alice" changed: preparing a new will.',
        "Your will is in force",
    ], step["popups"]
    _assert_same_signed_will(s, txids, raw, period)


def test_command_line_prepare_keeps_the_signed_will(flow_session):
    """``bal_prepare_will`` on the same case: rebuilt, but nothing to sign
    or send; the will saved in the wallet is still signed."""
    s = _sent_will(flow_session, "basic", 1)
    period, txids, raw = _to_equal_period(s)
    result = s.cli("prepare_will")["returned"]
    assert result["result"] == "rebuilt", result
    assert result["message"] == "The will is coherent", result
    s.reload_window()
    _assert_same_signed_will(s, txids, raw, period)


def test_command_line_auto_rebuild_on_an_encrypted_wallet(flow_session):
    """``bal_will_autorebuild`` on the same case (wallet with a password):
    nothing to sign, so not "needs_signing"."""
    s = _sent_will(flow_session, "basic", 1)
    period, txids, raw = _to_equal_period(s)
    result = s.cli("auto_rebuild")["returned"]
    assert result["result"] == "valid", result
    s.reload_window()
    _assert_same_signed_will(s, txids, raw, period)


def test_an_executor_that_moved_gets_the_signed_will(flow_session):
    """A will-executor changes URL (same fee address and fee): the Check
    sends it the signed transaction, without asking the password."""
    s = _sent_will(flow_session, "basic", 1)
    old = s.world.executors.pop("https://we0.test")
    s.world.executors["https://we0-new.test"] = dict(old, url="https://we0-new.test")
    s.check(cancel_on=("sign your will",))
    step = s.recorder.steps[-1]
    assert step["questions"] == [], step["questions"]
    assert [u for u, _t in step["pushed"]] == ["https://we0-new.test"], step["pushed"]
    moved = [w for w in _valid(s).values() if w.we["url"] == "https://we0-new.test"]
    assert len(moved) == 1 and all(moved[0].get_status(f) for f in SENT)


def test_a_fee_rate_giving_the_same_transactions_is_written_once(flow_session):
    """A fee rate that gives the same transactions (13.001 instead of 13,
    possible with a JSON setting): one rebuild, then the will is in order
    (before, every Check said "fee rate changed")."""
    s = _sent_will(flow_session, "basic", 1)
    ws = s.plugin.WILL_SETTINGS.get()
    ws["baltx_fees"] = 13.001
    s.plugin.WILL_SETTINGS.set(ws)
    s.bw.will_settings = s.plugin.WILL_SETTINGS.get()
    s.check()
    _assert_nothing_done(s, "first Check")
    s.check()
    _assert_quiet(s, "second Check")


def test_a_later_period_still_invalidates(flow_session):
    """A real postpone after the period was written: the Check still asks
    to invalidate the old will (paid, on-chain) and sends a later will."""
    s = _sent_will(flow_session, "basic", 2)
    period, txids, _raw = _to_equal_period(s)
    s.check()
    _assert_nothing_done(s, "Check")
    date = Will.get_min_locktime(s.bw.willitems)
    s.set_date("{}d".format(int(period[:-1]) + 30))
    s.check()
    step = s.recorder.steps[-1]
    assert [q[1] for q in step["questions"]][:1] == ["Invalidate your old will"]
    assert any(kind.startswith("invalidation") for kind, _txid in step["broadcasts"])
    assert Will.get_min_locktime(s.bw.willitems) > date


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
