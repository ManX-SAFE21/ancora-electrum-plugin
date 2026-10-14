"""Unsigned drafts no longer move the delivery date (CHANGELOG #128, known issue "P2").

The owner's case, 2026-10-12 (BASIC, fixed date): a will signed and sent
to four will-executors for 2027-02-27 22:00 UTC, fee 21. The fee became
20: the wallet-close Check prepared a new will one day earlier (02-26) and
the owner closed the password window. The fee went back to 21: the next
Check prepared another draft one day earlier again (02-25), because the
UNSIGNED draft of 02-26 was the will it had to beat. The date set back to
27/02 and a Check: still 02-25, and the password was asked again, while
the will-executors held the will of 02-27 all along.

Since #128 every build first drops the drafts (VALID, never signed nor
sent: they exist only in this wallet), then dates the new will only
against the will in force (the items signed and/or sent, still
executable), and brings back the will in force when the rebuilt
transactions are exactly its transactions (same txids), on a synchronized
wallet, when no other signed or sent item on the same coins is dated
earlier. Nothing new is stored in the wallet: the state is derived from
the items (``bal.core.will_state``).

Wallets, coins and three fake will-executors (each with its own fee
address) come from ``tests/flowkit.py`` (throw-away testnet wallet with a
password, fixed clock 2026-10-10 12:00 UTC). A real wallet online is
synchronized: the tests that expect the bring-back say so with
:func:`_synced` (flowkit's offline wallet is not).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_drafts_do_not_move_date.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core import delivery
from bal.engine import context

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets (flowkit).
TEST_PASSWORD = "flowkit-test"

# The owner's delivery: 2027-02-27 22:00 UTC.
D = fk.ts(2027, 2, 27, 22)
HOUR = 3600

# The password window of "Sign your will": a word of its question.
SIGN = ("sign your will",)

# The state of a will that is signed, sent and verified.
SENT = ("VALID", "COMPLETE", "PUSHED", "CHECKED")


@pytest.fixture
def make(monkeypatch):
    """``tests/flowkit.py`` sessions with three will-executors."""
    fk.ensure_event_loop()
    made = []

    def _make(mode="basic", **kw):
        """One session (fee 21, delivery D, Check Alive 30 days before)."""
        kw.setdefault("password", TEST_PASSWORD)
        kw.setdefault("fee", 21)
        kw.setdefault("locktime", D)
        kw.setdefault("threshold", "-30d")
        s = fk.Session(monkeypatch, mode=mode, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _synced(monkeypatch, s, value=True):
    """The wallet says it is synchronized (a real wallet online)."""
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: value)


def _valid(s):
    """The VALID items by txid."""
    return {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}


def _drafts(s):
    """The VALID items never signed nor sent."""
    return {wid: w for wid, w in _valid(s).items()
            if not w.get_status("COMPLETE") and not w.get_status("PUSHED")}


def _sent_will(make, monkeypatch, mode="basic", synced=True):
    """The owner's will: signed, sent and verified at D, fee 21.

    Returns the session and the ids of the three items in force.
    """
    s = make(mode)
    if synced:
        _synced(monkeypatch, s)
    s.check()
    valid = _valid(s)
    assert len(valid) == 3
    assert all(w.get_status(f) for w in valid.values() for f in SENT)
    assert {int(w.tx.locktime) for w in valid.values()} == {D}
    return s, set(valid)


def _last(s):
    """The record of the last action."""
    return s.recorder.steps[-1]


def _rows(s):
    """The rows the last Check dialog shows at its end (tags removed)."""
    return [fk.strip(r) for r in _last(s).get("rows", [])]


def _assert_in_force_kept(s, first):
    """The will in force is the owner's will again; nothing was asked,
    signed, sent or broadcast; the drafts are gone; D is stored."""
    step = _last(s)
    assert not [q for q in step["questions"] if q[0] == "password"], step["questions"]
    assert step["pushed"] == [] and step["broadcasts"] == []
    assert set(s.bw.willitems) == first, "the drafts must be gone"
    for wid in first:
        w = s.bw.willitems[wid]
        assert all(w.get_status(f) for f in SENT), w.status
        assert not w.get_status("REPLACED")
    assert s.bw.will_settings["locktime"] == D
    stored = s.wallet.db.get_dict("will")
    assert set(stored) == first
    assert all(stored[wid]["VALID"] and not stored[wid]["REPLACED"] for wid in first)


# ------------------------------------------------------------ the owner's case

@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_owner_case(make, monkeypatch, mode):
    """Fee 20 and 21 at wallet close (password cancelled both times), then
    the date set back to 27/02 and a Check: the will in force is kept."""
    s, first = _sent_will(make, monkeypatch, mode)
    s.set_fee(20)
    s.check(closing=True, cancel_on=SIGN)
    assert {int(w.tx.locktime) for w in _drafts(s).values()} == {D - fk.DAY}
    assert s.bw.will_settings["locktime"] == D - fk.DAY
    s.set_fee(21)
    s.check(closing=True, cancel_on=SIGN)
    # The draft of 02-26 is gone and never beaten by a 02-25 one (#128).
    # #135 (owner's ZIP test): with the fee put back, the settings equal
    # the will in force except the day the anticipation stored: that
    # leftover is undone (the drafts dropped, the date back to 02-27) and
    # the will in force is kept, nothing to sign. (Before #135 the draft
    # stayed at 02-26 and the password was asked: the "fee put back only"
    # case, out of scope in #128.)
    assert _drafts(s) == {}
    assert s.bw.will_settings["locktime"] == D
    _assert_in_force_kept(s, first)
    s.set_date(D)
    s.check(cancel_on=SIGN)
    _assert_in_force_kept(s, first)
    rows = _rows(s)
    assert "Checking your will: Ok" in rows, rows
    # #135: the will in force was already brought back by the Check
    # before (the anticipation leftover undone): nothing to build here.
    assert not any(r.startswith("Building your will") for r in rows), rows
    assert not any("preparing a new will" in r for r in rows), rows
    assert _last(s)["report"]["result"][0] == "ok"
    # The next Check finds nothing to do.
    s.check()
    _assert_in_force_kept(s, first)
    assert "Checking your will: Ok" in _rows(s)


def test_owner_case_golden(make, monkeypatch):
    """The owner's case (BASIC), photographed with the Phase 5e recorder:
    every row, popup, question, item and setting of the five Checks, in
    ``tests/golden/5e/check/owner_drafts_128.json``. A change of what the
    owner sees in this case shows up as a diff of that file."""
    s, first = _sent_will(make, monkeypatch)
    s.recorder.note("fee 20; wallet close, password cancelled")
    s.set_fee(20)
    s.check(closing=True, cancel_on=SIGN)
    s.recorder.note("fee 21; wallet close, password cancelled")
    s.set_fee(21)
    s.check(closing=True, cancel_on=SIGN)
    s.recorder.note("date 2027-02-27 22:00 UTC; Check")
    s.set_date(D)
    s.check(cancel_on=SIGN)
    s.recorder.note("Check again")
    s.check()
    _assert_in_force_kept(s, first)
    fk.check_golden("check/owner_drafts_128", s.recorder.steps)


def test_owner_case_without_cancel_at_the_end(make, monkeypatch):
    """The same, with nothing cancelled at the last Check: nothing to sign,
    so the password is never asked."""
    s, first = _sent_will(make, monkeypatch)
    s.set_fee(20)
    s.check(closing=True, cancel_on=SIGN)
    s.set_fee(21)
    s.check(closing=True, cancel_on=SIGN)
    s.set_date(D)
    s.check()
    _assert_in_force_kept(s, first)


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_fee_changed_then_back_with_the_date(make, monkeypatch, mode):
    """Signed at D; draft D-1 by a fee change; fee back and date D."""
    s, first = _sent_will(make, monkeypatch, mode)
    s.set_fee(20)
    s.check(cancel_on=SIGN)
    s.set_fee(21)
    s.set_date(D)
    s.check(cancel_on=SIGN)
    _assert_in_force_kept(s, first)


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_earlier_date_cancelled_then_back(make, monkeypatch, mode):
    """Signed at D; date D-5 (free) with the password cancelled; then D."""
    s, first = _sent_will(make, monkeypatch, mode)
    s.set_date(D - 5 * fk.DAY)
    s.check(cancel_on=SIGN)
    assert {int(w.tx.locktime) for w in _drafts(s).values()} == {D - 5 * fk.DAY}
    s.set_date(D)
    s.check(cancel_on=SIGN)
    _assert_in_force_kept(s, first)


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_never_signed_draft_then_a_later_date(make, monkeypatch, mode):
    """No will in force: a first will at D never signed, then D+10. The
    new will is built at D+10 (before #128 the draft pulled it back to D)."""
    s = make(mode)
    _synced(monkeypatch, s)
    s.check(cancel_on=SIGN)
    old = set(_drafts(s))
    assert len(old) == 3
    s.set_date(D + 10 * fk.DAY)
    s.check(cancel_on=SIGN)
    drafts = _drafts(s)
    assert len(drafts) == 3 and not old & set(s.bw.willitems)
    assert {int(w.tx.locktime) for w in drafts.values()} == {D + 10 * fk.DAY}
    assert s.bw.will_settings["locktime"] == D + 10 * fk.DAY


def test_not_synchronized_wallet_is_not_brought_back(make, monkeypatch):
    """The wallet does not know yet whether a coin was spent: the will in
    force is not brought back. The rebuilt transactions are the same (at
    D, never earlier because of the drafts): they keep the signatures of
    the will in force, so no password is asked, and they are sent again."""
    s, first = _sent_will(make, monkeypatch, synced=False)
    s.set_fee(20)
    s.check(cancel_on=SIGN)
    s.set_fee(21)
    s.set_date(D)
    pushed_before = len(s.world.pushed)
    s.check(cancel_on=SIGN)
    step = _last(s)
    assert not [q for q in step["questions"] if q[0] == "password"], step["questions"]
    assert step["broadcasts"] == []
    assert {wid for _url, wid, _raw in s.world.pushed[pushed_before:]} == first
    assert set(s.bw.willitems) == first, "the drafts must be gone"
    for wid in first:
        w = s.bw.willitems[wid]
        assert all(w.get_status(f) for f in ("VALID", "COMPLETE", "PUSHED"))
    assert s.bw.will_settings["locktime"] == D


# ------------------------------------------------------------ automatic rebuild

def test_automatic_rebuild_with_a_pending_draft(make, monkeypatch):
    """Draft D-1 left unsigned; fee and date back, a new coin, the
    automatic rebuild: the new will spends both coins at D-1, one day
    before the will in force (before #128: D-2, one day before the
    draft). The old drafts are gone."""
    s, first = _sent_will(make, monkeypatch)
    s.set_fee(20)
    s.check(cancel_on=SIGN)
    old_drafts = set(_drafts(s))
    s.set_fee(21)
    s.set_date(D)
    s.add_coin()
    s.auto()
    valid = _valid(s)
    assert valid and not old_drafts & set(s.bw.willitems)
    assert {int(w.tx.locktime) for w in valid.values()} == {D - fk.DAY}
    assert all(len(w.tx.inputs()) == 2 for w in valid.values())
    assert s.bw.will_settings["locktime"] == D - fk.DAY
    for wid in first:
        assert s.bw.willitems[wid].get_status("COMPLETE"), "never dropped"


# ------------------------------------------------------------ command line

def test_cli_with_a_draft(make, monkeypatch):
    """``bal_will_check`` only reports; ``bal_will_prepare`` drops the
    drafts and finds the will in force; ``bal_will_autorebuild`` then has
    nothing to do."""
    s, first = _sent_will(make, monkeypatch)
    s.set_fee(20)
    s.check(cancel_on=SIGN)
    drafts = set(_drafts(s))
    s.set_fee(21)
    s.set_date(D)
    check = s.cli("will_check")
    assert "fee" in check.get("raised", ""), check
    assert drafts <= set(s.wallet.db.get_dict("will")), "a check builds nothing"
    prepare = s.cli("prepare_will")["returned"]
    assert prepare["result"] == "coherent", prepare
    s.reload_window()
    _assert_in_force_kept(s, first)
    auto = s.cli("auto_rebuild")["returned"]
    assert auto["result"] == "valid", auto
    assert _last(s)["pushed"] == [] and _last(s)["questions"] == []


def test_cli_auto_rebuild_with_a_draft(make, monkeypatch):
    """``bal_will_autorebuild`` straight after the draft: the will in force
    comes back, nothing is signed or sent."""
    s, first = _sent_will(make, monkeypatch)
    s.set_fee(20)
    s.check(cancel_on=SIGN)
    s.set_fee(21)
    s.set_date(D)
    auto = s.cli("auto_rebuild")["returned"]
    assert auto["result"] == "valid", auto
    s.reload_window()
    _assert_in_force_kept(s, first)


# ------------------------------------------------------------ never dropped

def test_a_signed_earlier_will_is_kept_and_a_later_date_needs_the_invalidation(
        make, monkeypatch):
    """Fee 20, signed and sent (D-1): the will in force is now D-1. Fee and
    date back to D is a postpone of a sent will: the paid invalidation, as
    before. The signed D-1 items are never dropped."""
    s, first = _sent_will(make, monkeypatch)
    s.set_fee(20)
    s.check()
    second = {wid for wid, w in _valid(s).items() if w.get_status("PUSHED")}
    assert len(second) == 3
    assert {int(s.bw.willitems[w].tx.locktime) for w in second} == {D - fk.DAY}
    s.set_fee(21)
    s.set_date(D)
    s.check(cancel_on=("invalidat",) + SIGN)
    assert any("Postponed" in r for r in _last(s)["history"]), _last(s)["history"]
    for wid in second | first:
        assert s.bw.willitems[wid].get_status("COMPLETE")


# ------------------------------------------------------------ the hour of the day

def test_an_earlier_hour_is_a_new_will_to_sign(make, monkeypatch):
    """Same day, 21:00 instead of 22:00: a new will to sign (free), no
    invalidation, no bring-back."""
    s, first = _sent_will(make, monkeypatch)
    s.set_date(D - HOUR)
    s.check(cancel_on=SIGN)
    step = _last(s)
    assert step["broadcasts"] == []
    assert ["password", "Sign your will"] in [q[:2] for q in step["questions"]] or any(
        q[0] == "password" for q in step["questions"])
    drafts = _drafts(s)
    assert len(drafts) == 3 and not first & set(drafts)
    assert {int(w.tx.locktime) for w in drafts.values()} == {D - HOUR}
    assert all(s.bw.willitems[wid].get_status("REPLACED") for wid in first)


@pytest.mark.parametrize("with_draft", [False, True])
def test_a_later_hour_is_a_postpone(make, monkeypatch, with_draft):
    """Same day, 23:00 instead of 22:00: a postpone of a sent will, so the
    paid invalidation (``WillPostponedException``), with or without a draft
    left by a cancelled fee change."""
    s, first = _sent_will(make, monkeypatch)
    if with_draft:
        s.set_fee(20)
        s.check(cancel_on=SIGN)
        s.set_fee(21)
    s.set_date(D + HOUR)
    s.check(cancel_on=("invalidat",) + SIGN)
    assert any("Postponed" in r for r in _last(s)["history"]), _last(s)["history"]
    for wid in first:
        assert s.bw.willitems[wid].get_status("COMPLETE")


# ------------------------------------------------------------ other settings

def test_multiverse_with_a_draft(make, monkeypatch):
    """The hidden multiverse option (each heir its own date) with a draft:
    the will in force comes back as well."""
    s = make()
    _synced(monkeypatch, s)
    s.plugin.ENABLE_MULTIVERSE.set(True)
    s.bw.heirs["bob"] = [fk.HEIR_ADDR["bob"], "40%", D + 30 * fk.DAY]
    s.check()
    first = set(_valid(s))
    assert first and all(w.get_status("PUSHED") for w in _valid(s).values())
    s.set_fee(20)
    s.check(cancel_on=SIGN)
    assert _drafts(s)
    s.set_fee(21)
    s.check(cancel_on=SIGN)
    step = _last(s)
    assert step["pushed"] == [] and step["broadcasts"] == []
    assert not [q for q in step["questions"] if q[0] == "password"]
    assert not _drafts(s)
    assert set(_valid(s)) == first


def test_period_form_with_a_draft(make, monkeypatch):
    """#127 period form (ADVANCED): a first will left unsigned is not a will
    in force, so it neither locks the form nor holds the period date: the
    next day the date counts from that day and the draft is rebuilt."""
    s = make("advanced", period_months=24)
    _synced(monkeypatch, s)
    s.check(cancel_on=SIGN)
    old = set(_drafts(s))
    assert len(old) == 3
    assert not context.form_locked(s.bw)
    assert not context.will_in_force(s.bw)
    s.clock.travel(1)
    s.check(cancel_on=SIGN)
    expected = delivery.add_months(delivery.utc_midnight(s.clock.now), 24)
    assert s.bw.will_settings["locktime"] == expected
    drafts = _drafts(s)
    assert len(drafts) == 3 and not old & set(s.bw.willitems)
    assert {int(w.tx.locktime) for w in drafts.values()} == {expected}


def test_period_form_with_a_will_in_force_and_a_draft(make, monkeypatch):
    """Period form, will signed and sent, then a draft by a fee change: the
    will in force holds the period date; fee back: it comes back."""
    s = make("advanced", period_months=24)
    _synced(monkeypatch, s)
    s.check()
    first = set(_valid(s))
    stored = s.bw.will_settings["locktime"]
    s.set_fee(20)
    s.check(cancel_on=SIGN)
    assert context.will_in_force(s.bw)
    s.set_fee(21)
    s.set_date(stored)
    s.check(cancel_on=SIGN)
    assert set(_valid(s)) == first and not _drafts(s)
    assert s.bw.will_settings["locktime"] == stored


def test_a_draft_left_by_another_program(make, monkeypatch):
    """Another program (BAL, an older ZIP) prepared a draft one day earlier
    and its "Sign your will" was cancelled: the stored date stays (#123,
    ``ANCHOR_UNSIGNED``) and the next Check brings back the will in force
    instead of signing that draft."""
    s, first = _sent_will(make, monkeypatch)
    # The other program: a will at fee 20, saved without Safe21's anchor and
    # without touching Safe21's settings.
    s.set_fee(20)
    s.bw.init_class_variables()
    from bal.engine import build
    build.build_will(s.bw, with_creation_date=True)
    s.set_fee(21)
    s.set_date(D)
    will = s.wallet.db.get_dict("will")
    for k in list(will):
        del will[k]
    for wid, w in s.bw.willitems.items():
        d = w.to_dict()
        d["tx"] = str(d["tx"])
        will[wid] = d
    assert any(not w["COMPLETE"] and w["VALID"] for w in will.values())
    s.reload_window()
    assert s.bw.will_settings["locktime"] == D, "an unsigned draft is not followed"
    s.check(cancel_on=SIGN)
    _assert_in_force_kept(s, first)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
