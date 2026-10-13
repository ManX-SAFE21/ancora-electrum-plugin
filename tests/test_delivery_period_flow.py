"""A will delivered after a renewable period of months: the real flows (#127).

Owner decisions of 2026-10-12. The period form: the will is delivered N
calendar months after the day it is prepared; when its Check Alive passes,
the owner is asked to RENEW it (#79): the old will is invalidated on-chain
(miner fee) and the new one is prepared at once, delivered N months after
the day of the renewal. The stored delivery date is always an absolute date
(``locktime``); the engine moves it only when no will is in force
(``context.apply_delivery_policy``, right after the invalidated items are
refreshed). A will already in the period form keeps working in BASIC mode.

Each test runs the real "Check your will" dialog and the real window
(``tests/flowkit.py``: three fake will-executors with their own fee
addresses, a throw-away test wallet with the test password, a fixed clock
starting 2026-10-10 12:00 UTC):

* the first period will is delivered today + N months;
* the renewal is REALLY offered (phase 1 ends with "check_alive_renew" for
  the period form) and builds the new will at today + N months; the next
  day the Check is Ok;
* a manual Invalidate, then the policy step: the next Check prepares the new
  will at today + N months (and it needs the policy AFTER the refresh);
* a new N set while a will is in force is used at the next renewal;
* a new coin anticipates the will by one day and the stored date follows;
* a period will keeps renewing in BASIC mode;
* a fixed date is never renewed; a new wallet gets today + 24 months.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_delivery_period_flow.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.engine import check as engine_check
from bal.engine import context as engine_context

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
RENEW_QUESTION = "Are you still here?"


@pytest.fixture
def make(monkeypatch):
    """``tests/flowkit.py`` sessions with three will-executors."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session (``fk.Session`` arguments), three will-executors."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


@pytest.fixture
def phase1(monkeypatch):
    """Record what phase 1 of every Check returned (``check_variables``)."""
    seen = []
    real = engine_check.check_variables

    def spy(host, run, ui):
        """Run the real phase 1 and remember its result."""
        result = real(host, run, ui)
        seen.append((result, dict(host.will_settings)))
        return result

    monkeypatch.setattr(engine_check, "check_variables", spy)
    return seen


def last(s):
    """The last step the session recorded."""
    return s.recorder.steps[-1]


def rows(step):
    """The rows of a recorded step, without HTML."""
    return [fk.strip(r) for r in step.get("rows", [])]


def valid_dates(s):
    """The dates of the VALID will items, sorted."""
    return sorted({fk.fmt(w.tx.locktime) for w in s.bw.willitems.values()
                   if w.get_status("VALID")})


def assert_ok_next_day(s):
    """The next day the Check ends "All done: Ok" and does nothing."""
    s.clock.travel(1)
    pushed = len(s.world.pushed)
    s.check()
    step = last(s)
    assert "All done: Ok" in rows(step), rows(step)
    assert step["questions"] == [] and step["broadcasts"] == []
    assert len(s.world.pushed) == pushed


# ------------------------------------------------------------ the first will

def test_first_period_will_is_today_plus_n_months(make):
    """2 months from 2026-10-10: the will is delivered 2026-12-10 (00:00
    UTC); the stored date and ``period_start`` say so."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    assert valid_dates(s) == ["2026-12-10"]
    d = s.wallet.db.get_dict("will_settings")
    assert (d["form"], d["period_months"]) == ("period", 2)
    assert d["locktime"] == fk.ts(2026, 12, 10)
    assert d["period_start"] == fk.ts(2026, 10, 10)
    assert_ok_next_day(s)


def test_new_wallet_default_is_today_plus_24_months(make):
    """Owner decision 6: a wallet with nothing to migrate from gets a fixed
    date 24 calendar months after today, and the first Check builds it."""
    s = make(mode="basic")
    other = s.second_wallet(seed=False, plugin_data=False, password=TEST_PASSWORD)
    d = other.wallet.db.get_dict("will_settings")
    assert (d["migrated_from"], d["form"]) == ("default", "fixed")
    assert d["locktime"] == fk.ts(2028, 10, 10)
    other.activate()
    other.check()
    assert valid_dates(other) == ["2028-10-10"]


# ------------------------------------------------------------ the renewal

def renewal_session(make, phase1, mode="advanced"):
    """A period will of 2 months (Check Alive 30 days before), signed and
    sent; 40 days later its Check Alive (2026-11-10) has passed."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    if mode == "basic":
        s.plugin.USER_TYPE.set("basic")
    s.clock.travel(40)
    phase1.clear()
    return s


def test_renewal_is_offered_and_builds_today_plus_n_months(make, phase1):
    """Phase 1 ends with "check_alive_renew" (the unsigned invalidation) for
    the period form; Renew: the old will is invalidated and the new one is
    built at today + 2 months (2026-11-19 -> 2027-01-19), its Check Alive
    in the future; the next day the Check is Ok."""
    s = renewal_session(make, phase1)
    s.check()
    (first, settings), (second, _) = phase1[0], phase1[1]
    assert first.kind == "check_alive_renew" and first.tx is not None
    assert settings["form"] == "period" and settings["period_months"] == 2
    assert second is None                       # the new will is prepared
    step = last(s)
    assert [k for k, _t in step["questions"]][:1] == ["renew"]
    assert RENEW_QUESTION in step["questions"][0][1]
    # #134: the GUI writes the dates in the one long style (UTC).
    assert "19 January 2027, 00:00 UTC (today + 2 months)" in step["questions"][0][1]
    assert [k for k, _t in step["broadcasts"]] == ["invalidation(dialog)"]
    assert valid_dates(s) == ["2027-01-19"]
    d = s.wallet.db.get_dict("will_settings")
    assert d["locktime"] == fk.ts(2027, 1, 19)
    assert d["period_start"] == fk.ts(2026, 11, 19)
    assert "All done: Ok" in rows(step)
    assert_ok_next_day(s)


def test_not_now_changes_nothing(make, phase1):
    """Not now: nothing is invalidated, the stored date stays."""
    s = renewal_session(make, phase1)
    s.check(cancel_on=("renew",))
    assert phase1[0][0].kind == "check_alive_renew"
    assert last(s)["broadcasts"] == []
    assert s.wallet.db.get_dict("will_settings")["locktime"] == fk.ts(2026, 12, 10)
    assert valid_dates(s) == ["2026-12-10"]


def test_period_will_keeps_renewing_in_basic_mode(make, phase1):
    """Owner decision 5: the mode only hides the choice. Prepared in
    ADVANCED, then BASIC: the passed Check Alive still asks to renew and the
    new will is delivered today + 2 months. (Before #127 BASIC ignored the
    Check Alive: no question, the will simply ran out.)"""
    s = renewal_session(make, phase1, mode="basic")
    assert s.plugin.is_basic_mode()
    s.check()
    assert phase1[0][0].kind == "check_alive_renew"
    assert [k for k, _t in last(s)["questions"]][:1] == ["renew"]
    assert valid_dates(s) == ["2027-01-19"]
    assert_ok_next_day(s)


def test_fixed_date_is_not_renewed(make, phase1):
    """The same dates with a fixed delivery date: the Check stops on the
    reason ("fixed date"), nothing is asked or invalidated."""
    s = make(mode="advanced", locktime=fk.ts(2026, 12, 10), threshold="-30d")
    s.check()
    s.clock.travel(40)
    phase1.clear()
    s.check()
    assert phase1[0][0].kind == "check_alive_stop"
    step = last(s)
    assert step["questions"] == [] and step["broadcasts"] == []
    assert any("Your delivery is a fixed date" in r for r in rows(step))


def test_new_n_is_used_at_the_next_renewal(make, phase1, monkeypatch):
    """N = 3 set while the 2-month will is in force: nothing now; at the
    renewal the new will is delivered today + 3 months."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
    engine_context.set_delivery(s.bw, months=3)
    s.clock.travel(40)
    s.check()
    assert "19 February 2027, 00:00 UTC (today + 3 months)" in last(s)["questions"][0][1]
    assert valid_dates(s) == ["2027-02-19"]
    assert_ok_next_day(s)


# ------------------------------------------------------------ invalidate + policy

def test_manual_invalidate_then_the_policy_dates_the_new_will(make):
    """Will tab > Invalidate (the classic window, paid): the next Check
    finds no will in force, so the period starts again today: the new will
    is delivered today + 2 months, and the next day the Check is Ok."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    s.clock.travel(11)                                # 2026-10-21
    s.menu_invalidate()
    assert [k for k, _t in s.world.broadcast_log] == [
        "invalidation(transaction window)"]
    s.check()
    assert valid_dates(s) == ["2026-12-21"]
    d = s.wallet.db.get_dict("will_settings")
    assert (d["locktime"], d["period_start"]) == (
        fk.ts(2026, 12, 21), fk.ts(2026, 10, 21))
    assert_ok_next_day(s)


def test_policy_must_run_after_the_refresh(make, monkeypatch):
    """The order of ``init_class_variables`` is needed: with the policy
    BEFORE the refresh of the invalidated items, the old will still looks
    in force, the date is not moved and the new will would be built at the
    OLD date. Shown by calling the two steps in the wrong order."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    s.clock.travel(11)
    s.menu_invalidate()
    assert engine_context.apply_delivery_policy(s.bw) is None   # wrong order
    assert s.bw.will_settings["locktime"] == fk.ts(2026, 12, 10)
    engine_context.refresh_invalidated_items(s.bw)
    assert engine_context.apply_delivery_policy(s.bw) == fk.ts(2026, 12, 21)


# ------------------------------------------------------------ anticipation

def test_new_coin_anticipates_one_day_and_the_date_follows(make):
    """A new coin: the will is rebuilt one day EARLIER (no fee), the stored
    date follows it (the #107/#110 sync), ``period_start`` is not moved
    (a will is in force), and the next day the Check is Ok."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    start = s.wallet.db.get_dict("will_settings")["period_start"]
    s.add_coin(250_000)
    s.check()
    assert last(s)["broadcasts"] == []
    assert valid_dates(s) == ["2026-12-09"]
    d = s.wallet.db.get_dict("will_settings")
    assert d["locktime"] == fk.ts(2026, 12, 9)
    assert d["period_start"] == start
    assert_ok_next_day(s)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
