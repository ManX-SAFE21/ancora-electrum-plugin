"""Photographs of today's "Check your will" dialog (CHANGELOG #112, Phase 5e).

Each scenario drives the REAL ``BalBuildWillDialog`` and ``BalWindow`` code
on a real (offline, testnet, no funds) Electrum wallet through the world of
``tests/flowkit.py``, and compares everything the owner would see or the
plugin would store with a saved golden file in ``tests/golden/5e/check/``:
the dialog rows (also the ones overwritten later), the report, the buttons,
the popups, the questions and password prompts, the will items with their
statuses, dates, fees and History labels, the settings, the heirs, and what
was broadcast, sent to and verified on the will-executors.

Phase 5e moves this code into one engine for the GUI and the command line;
these files must stay identical through every move. A golden file changes
only when a step changes behaviour on purpose, with a CHANGELOG line.

Scenarios with two coins whose result depends on the random coin order
stay in the scenario harness (run 6 times); the ones here are
deterministic. The second coin in ``new_coin`` is checked twice in the
suite (``test_goldens_are_stable``).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_5e_char_check.py
Update the golden files (only with a CHANGELOG line):
    BAL_GOLDEN_UPDATE=1 ... python3 -m pytest tests/test_5e_char_check.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

WE0 = "https://we0.test"
WE1 = "https://we1.test"

# 60 days after the fixed "today": a delivery that a scenario can let pass.
SOON = fk.FIXED_NOW - fk.FIXED_NOW % fk.DAY + 60 * fk.DAY


@pytest.fixture(scope="module", autouse=True)
def _event_loop():
    """Electrum's asyncio loop for the wallets, stopped after the module."""
    fk.ensure_event_loop()
    yield
    fk.stop_event_loop()


@pytest.fixture
def session(monkeypatch):
    """A factory of sessions, all removed after the test."""
    made = []

    def make(**kw):
        s = fk.Session(monkeypatch, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


# ------------------------------------------------------------ scenarios
#
# Each scenario is a function (session factory) -> Session; the Checks it
# makes are recorded by the session's Recorder and its owner actions are
# noted with ``s.recorder.note``.

def first_will_basic(make):
    """The first will, basic mode."""
    s = make(mode="basic")
    s.check()
    return s


def first_will_advanced(make):
    """The first will, advanced mode."""
    s = make(mode="advanced")
    s.check()
    return s


def coherent_will(make):
    """A second Check with nothing changed."""
    s = first_will_basic(make)
    s.check()
    return s


def heir_added(make):
    """carol added: 40/40/20."""
    s = first_will_basic(make)
    s.recorder.note("heirs alice 40%, bob 40%, carol 20%")
    s.set_heirs(alice="40%", bob="40%", carol="20%")
    s.check()
    return s


def heir_removed(make):
    """bob removed: alice 100%."""
    s = first_will_basic(make)
    s.recorder.note("heirs alice 100%")
    s.set_heirs(alice="100%")
    s.check()
    return s


def fee_changed(make):
    """The fee goes from 13 to 14 sat/vB."""
    s = first_will_basic(make)
    s.recorder.note("fee 14")
    s.set_fee(14)
    s.check()
    return s


def new_coin(make):
    """A second coin arrives (#91: the new will is dated one day earlier)."""
    s = first_will_basic(make)
    s.recorder.note("new coin 250000")
    s.add_coin(250_000)
    s.check()
    return s


def date_later_sent(make):
    """Postpone a signed and sent will: invalidation, then the new will."""
    s = first_will_basic(make)
    s.recorder.note("date later 2033-09-23")
    s.set_date(fk.LATER)
    s.check()
    s.check()
    return s


def date_later_advanced(make):
    """Postpone in advanced mode (Check Alive follows the date)."""
    s = first_will_advanced(make)
    s.recorder.note("date later 2033-09-23")
    s.set_date(fk.LATER)
    s.check()
    return s


def date_earlier(make):
    """Anticipate: no invalidation, a plain rebuild."""
    s = first_will_basic(make)
    s.recorder.note("date earlier 2031-09-23")
    s.set_date(fk.EARLIER)
    s.check()
    return s


def date_in_past(make):
    """The delivery date set in the past."""
    s = first_will_basic(make)
    s.recorder.note("date 2020-01-01")
    s.set_date(fk.PAST)
    s.check()
    return s


def expired_basic(make):
    """The delivery date passes: classic invalidation window."""
    s = make(mode="basic", locktime=SOON)
    s.check()
    s.recorder.note("70 days pass")
    s.clock.travel(70)
    s.check()
    return s


def check_alive_fixed_date(make):
    """Advanced, fixed delivery date: the Check Alive passes, the check stops."""
    s = make(mode="advanced", locktime=SOON, threshold="-30d")
    s.check()
    s.recorder.note("40 days pass")
    s.clock.travel(40)
    s.check()
    return s


def check_alive_renew(make):
    """Advanced, delivery 2 months after creation: Check Alive passes, Renew.

    Since #127 the period form (2 calendar months; before #123 the shared
    "60d"): the renewal is offered again (#123-#126: a fixed date, no
    renewal)."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    s.recorder.note("40 days pass")
    s.clock.travel(40)
    s.check()
    return s


def check_alive_not_now(make):
    """Advanced, delivery 2 months after creation: Check Alive passes, Not now.

    The period form since #127 (see :func:`check_alive_renew`)."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    s.recorder.note("40 days pass")
    s.clock.travel(40)
    s.check(cancel_on=("renew",))
    return s


def check_alive_renew_approved(make):
    """Advanced, period delivery: Renew answered at wallet open (renew=True).

    The period form since #127 (see :func:`check_alive_renew`)."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    s.recorder.note("40 days pass; dialog opened after Renew")
    s.clock.travel(40)
    s.check(renew=True)
    return s


def check_alive_expired_advanced(make):
    """Advanced: the delivery date itself passes."""
    s = make(mode="advanced", locktime=SOON, threshold="-30d")
    s.check()
    s.recorder.note("70 days pass")
    s.clock.travel(70)
    s.check()
    return s


def check_alive_passed_no_will(make):
    """Advanced: Check Alive already passed before any will exists."""
    s = make(mode="advanced", locktime=SOON, threshold="-90d")
    s.check()
    return s


def no_heirs(make):
    """No heir at all."""
    s = make()
    s.set_heirs()
    s.check()
    return s


def no_willexecutor(make):
    """No will-executor selected."""
    s = make()
    for we in s.world.executors.values():
        we["selected"] = False
    s.check()
    return s


def fixed_amount_too_big(make):
    """A fixed amount bigger than the balance."""
    s = make(mode="advanced")
    s.set_heirs(alice="2000000", bob="40%")
    s.check()
    return s


def percentages_not_100(make):
    """Advanced: percentages add up to 80 %."""
    s = make(mode="advanced")
    s.set_heirs(alice="50%", bob="30%")
    s.check()
    return s


def fixed_amounts_tiny(make):
    """Two tiny fixed amounts (100 sat each)."""
    s = make(mode="advanced")
    s.set_heirs(alice="100", bob="100")
    s.check()
    return s


def fee_too_high(make):
    """A fee the balance cannot pay (10,000 sat/vB)."""
    s = make(fee=10000)
    s.check()
    return s


# Test value: the password of the throw-away test wallets that have one.
TEST_PASSWORD = "flowkit-test"


def cancel_at_sign(make):
    """Wallet with a password: fee changed, Cancel at signing, Check again.

    Only a wallet with a password opens the password window, so only there
    can the owner press Cancel.
    """
    s = make(mode="basic", password=TEST_PASSWORD)
    s.check()
    s.recorder.note("fee 14; Cancel at 'Sign your will'")
    s.set_fee(14)
    s.check(cancel_on=("sign your will",))
    s.check()
    return s


def cancel_at_invalidate(make):
    """Wallet with a password: date later, Cancel at the invalidation, again."""
    s = make(mode="basic", password=TEST_PASSWORD)
    s.check()
    s.recorder.note("date later; Cancel at 'Invalidate your old will'")
    s.set_date(fk.LATER)
    s.check(cancel_on=("invalidate",))
    s.check()
    return s


def executor_down(make):
    """Fee changed while we1 does not answer."""
    s = first_will_basic(make)
    s.recorder.note("fee 14; we1 does not answer")
    s.set_fee(14)
    s.world.down.add(WE1)
    s.check()
    return s


def executor_already_has_it(make):
    """we0 answers "already present" to the new will."""
    s = first_will_basic(make)
    s.recorder.note("fee 14; we0 answers already present")
    s.set_fee(14)
    s.world.already.add(WE0)
    s.check()
    return s


def executor_added(make):
    """A third will-executor is selected."""
    s = first_will_basic(make)
    s.recorder.note("we2 added")
    s.world.add_executor()
    s.check()
    return s


def executor_deselected(make):
    """we1 deselected after the will was sent."""
    s = first_will_basic(make)
    s.recorder.note("we1 deselected")
    s.world.executors[WE1]["selected"] = False
    s.check()
    return s


def auto_sign_off(make):
    """Advanced, Auto-sign off: the will is prepared, not signed."""
    s = first_will_advanced(make)
    s.recorder.note("Auto-sign off; fee 14")
    s.plugin.AUTO_SIGN.set(False)
    s.set_fee(14)
    s.check()
    return s


def phase2_error(make):
    """Signing fails with an error in phase 2."""
    s = first_will_basic(make)
    s.recorder.note("fee 14; signing raises")

    def broken(*a, **kw):
        raise RuntimeError("signing broke (flowkit)")

    s.monkeypatch.setattr(s.bw, "sign_transactions", broken)
    s.set_fee(14)
    s.check()
    return s


def inheritance_executed(make):
    """The will transaction itself reached the chain (it was executed)."""
    s = first_will_basic(make)
    s.recorder.note("the will transaction of we0 is broadcast")
    wid = next(iter(s.bw.willitems))
    s.world.record_broadcast(s.wallet, s.bw.willitems[wid].tx, "will executed")
    s.check()
    return s


def invalidation_not_seen_yet(make):
    """Postpone; the invalidation is broadcast but Electrum does not see it."""
    s = first_will_basic(make)
    s.recorder.note("date later; the wallet does not see broadcasts yet")
    s.set_date(fk.LATER)
    s.world.wallet_lags = True
    s.check()
    return s


def closing(make):
    """The check at wallet close, after a fee change."""
    s = first_will_basic(make)
    s.recorder.note("fee 14; wallet closed")
    s.set_fee(14)
    s.check(closing=True)
    return s


def retry_after_down(make):
    """we1 does not answer; it comes back; Retry in the same dialog."""
    s = executor_down(make)
    s.recorder.note("we1 answers again; Retry")
    s.world.down.discard(WE1)
    s.redo(lambda dlg: dlg._retry_check())
    return s


def exclude_down(make):
    """we1 does not answer; Exclude it in the same dialog."""
    s = executor_down(make)
    s.recorder.note("Exclude we1")
    s.redo(lambda dlg: dlg._exclude_executor(WE1))
    return s


SCENARIOS = [
    first_will_basic, first_will_advanced, coherent_will, heir_added,
    heir_removed, fee_changed, new_coin, date_later_sent,
    date_later_advanced, date_earlier, date_in_past, expired_basic,
    check_alive_fixed_date, check_alive_renew, check_alive_not_now,
    check_alive_renew_approved,
    check_alive_expired_advanced, check_alive_passed_no_will, no_heirs,
    no_willexecutor, fixed_amount_too_big, percentages_not_100,
    fixed_amounts_tiny,
    fee_too_high, cancel_at_sign, cancel_at_invalidate, executor_down,
    executor_already_has_it, executor_added, executor_deselected,
    auto_sign_off, phase2_error, inheritance_executed,
    invalidation_not_seen_yet, closing, retry_after_down, exclude_down,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_check_dialog_golden(session, scenario):
    """Today's dialog, scenario by scenario, equals its golden file."""
    s = scenario(session)
    fk.check_golden("check/" + scenario.__name__, s.recorder.steps)


def test_goldens_are_stable(session):
    """Two runs of the same scenario record the same thing (no hidden clock)."""
    first = new_coin(session).recorder.steps
    second = new_coin(session).recorder.steps
    assert first == second


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
