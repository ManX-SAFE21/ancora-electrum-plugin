"""A postpone whose invalidation is not done puts the date back (#134).

Owner's ZIP test (option B): a will sent and verified for 2030-12-31, a
prepared draft for 2028-10-13; the BASIC wizard's "In 5 years"
(2031-10-10, later than the will in force: a postpone), "Create the will",
Cancel at "Invalidate your old will". Before, the later date stayed in the
settings with no will item carrying it: the summary never showed it and
every Check and wallet close asked for the invalidation again. Now the
stored date goes back to the date the will items really use (the draft's,
else the will in force's) and the result says the later date was not
applied. The date stays once the invalidation is broadcast.

``tests/flowkit.py`` wallets: three fake will-executors with their own fee
addresses, the test password, a fixed clock (2026-10-10 12:00 UTC); the
wallet counts as synchronized (the will in force is read from the chain).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_cancelled_postpone.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core.heirs import Heirs
from bal.engine import context as engine_context

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
IN_FORCE = fk.ts(2030, 12, 31, 1)
DRAFT = fk.ts(2028, 10, 13)
FIVE_YEARS = fk.ts(2031, 10, 10)
INVALIDATE = "Invalidate your old will"
# #135: the invalidation step's button, before the password; the tests
# cancel there ("invalidat"), which is the same "not invalidated".
STEP = "Invalidate now"
NOT_APPLIED = (
    "<b>Interrupted: the old will was not invalidated. Your later date (10 "
    "October 2031, 00:00 UTC) was not applied: the will in force needs an "
    "invalidation transaction on the blockchain first (your bitcoin stay "
    "yours, you only pay the normal network fee). When you are ready, choose "
    "it again.</b>"
)


@pytest.fixture
def make(monkeypatch):
    """A wallet with a will sent and verified for :data:`IN_FORCE` by three
    will-executors; ``draft``: then a draft for :data:`DRAFT` (a Check
    cancelled at "Sign your will")."""
    fk.ensure_event_loop()
    made = []

    def _make(draft=True):
        """The session (see the fixture)."""
        s = fk.Session(monkeypatch, mode="basic", locktime=IN_FORCE,
                       password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.check()
        if draft:
            s.set_date(DRAFT)
            s.check(cancel_on=("sign your will",))
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def stored(s):
    """The stored delivery date."""
    return s.wallet.db.get_dict("will_settings")["locktime"]


def asked(s):
    """The questions of the last action (texts)."""
    return [text for _kind, text in s.user.asked]


def result(dlg):
    """The result sentence of a Check window."""
    return list(dlg._report().snapshot()["result"])


def test_the_date_to_go_back_to():
    """The engine's rule: the draft's date when it is not later than the
    will in force, else the will in force's date; None when the stored date
    is not later (not a postpone) or nothing is known."""
    class Host:
        """The fields the rule reads."""
        willitems = {}
        will_settings = {"locktime": FIVE_YEARS}

    host = Host()
    rule = engine_context.cancelled_postpone_date
    orig = engine_context.in_force_delivery
    try:
        engine_context.in_force_delivery = lambda h: IN_FORCE
        assert rule(host) == IN_FORCE
        host.will_settings = {"locktime": IN_FORCE}
        assert rule(host) is None
        engine_context.in_force_delivery = lambda h: None
        host.will_settings = {"locktime": FIVE_YEARS}
        assert rule(host) is None
    finally:
        engine_context.in_force_delivery = orig


def test_owner_case_through_the_wizard(make, monkeypatch):
    """The owner's ZIP test: wizard "In 5 years", "Create the will", Cancel
    at the invalidation: the date goes back to the draft's 2028-10-13, the
    result says the later date was not applied, the summary is the one of
    the draft and the will in force, and the next Check does NOT ask for
    the invalidation (it signs and sends the 2028 will)."""
    from zoneinfo import ZoneInfo

    from test_gui_basic_wizard import install_network
    from test_gui_window_init_migration import real_window

    from bal.core import wizard_date
    from bal.core.summary import will_summary
    from bal.gui.qt import basic_wizard
    from bal.gui.qt.dialogs import BalWizardDialog

    s = make()
    monkeypatch.setattr(wizard_date, "local_zone", lambda: ZoneInfo("Europe/Rome"))
    monkeypatch.setattr(basic_wizard, "time_now", s.clock.time)
    bw = real_window(s, monkeypatch)
    bw.init_will()
    install_network(s, bw, monkeypatch)
    dialog = BalWizardDialog(bw)
    dialog.widget.chips[60].click()
    dialog.widget.next_button.click()
    assert stored(s) == FIVE_YEARS                 # page 1 stored the choice
    dialog._keep_changes = True                    # what "Create the will" sets
    dialog.close()
    # "Create the will" = BalBuildWillDialog(bw).build_will_task() on this
    # wallet (flowkit's Qt-free dialog); Cancel at the invalidation.
    s.bw.heirs = Heirs(s.wallet)
    dlg = s.check(cancel_on=("invalidat",))
    assert STEP in asked(s)
    assert stored(s) == DRAFT
    assert result(dlg) == ["warn", NOT_APPLIED]
    smr = will_summary(s.bw.willitems, 2, s.bw.will_settings, True, now=fk.FIXED_NOW,
                       still_active=s.bw._still_active_for_summary(), long_dates=True)
    assert smr["state"] == "to_sign"
    assert smr["rows"][0]["strong"] == "13 October 2028, 00:00 UTC"
    dlg = s.check(cancel_on=("invalidat",))
    assert STEP not in asked(s)
    assert stored(s) == DRAFT
    assert result(dlg)[0] == "ok"


def test_no_draft_goes_back_to_the_will_in_force(make):
    """No draft: Check button with a later date, Cancel at the invalidation:
    back to the will in force's date (2030-12-31 01:00 UTC)."""
    s = make(draft=False)
    s.set_date(FIVE_YEARS)
    dlg = s.check(cancel_on=("invalidat",))
    assert STEP in asked(s)
    assert stored(s) == IN_FORCE
    assert result(dlg) == ["warn", NOT_APPLIED]
    s.check(cancel_on=("invalidat",))
    assert STEP not in asked(s)


def test_wallet_close_cancel_goes_back(make):
    """The Check at wallet close, Cancel at the invalidation: the date goes
    back too (the next opening does not ask again)."""
    s = make()
    s.set_date(FIVE_YEARS)
    s.act("gui wallet close", s.bw.on_close, cancel_on=("invalidat",))
    assert STEP in asked(s)
    assert stored(s) == DRAFT


def test_signing_failure_goes_back(make, monkeypatch):
    """The invalidation could not be signed (so it was never broadcast):
    the same as Cancel."""
    s = make()
    s.set_date(FIVE_YEARS)
    monkeypatch.setattr(s.wallet, "sign_transaction", lambda tx, password, **kw: None)
    s.check()
    assert stored(s) == DRAFT
    assert not s.world.broadcast_log


def test_broadcast_invalidation_keeps_the_later_date(make):
    """The owner accepts: the invalidation is broadcast, the postpone is
    underway, the later date stays (never reverted after a broadcast)."""
    s = make()
    s.set_date(FIVE_YEARS)
    s.check()
    assert [kind for kind, _txid in s.world.broadcast_log][:1] == ["invalidation(dialog)"]
    assert stored(s) == FIVE_YEARS


def test_cli_has_no_decline_path(make):
    """``bal_will_check`` / prepare: the command returns the invalidation
    transaction for the owner to sign and broadcast; it has no Cancel, so
    the later date stays (the owner may broadcast it later)."""
    s = make()
    s.set_date(FIVE_YEARS)
    out = s.cli("prepare_will")
    assert out["returned"]["result"] == "postponed"
    assert stored(s) == FIVE_YEARS
