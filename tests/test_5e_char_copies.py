"""Photographs of the other copies of the will flows (CHANGELOG #114, Phase 5e).

Step 2 of Phase 5e. The "Check your will" dialog is photographed by
``test_5e_char_check.py``; the same flows also exist in other places, and
each copy behaves a little differently today:

* the Will tab menu "Prepare" (``BalWindow.prepare_will``);
* the automatic rebuild on new transactions
  (``BalWindow.maybe_auto_rebuild``, the entry the option would run);
* the Will tab menus "Sign", "Broadcast will", "Invalidate will" and the
  row command "check";
* the check at wallet close (``BalWindow.on_close``);
* the command line (``bal_will_*`` through ``bal.cli.commands._call``,
  so an error is the message the terminal prints).

Each scenario runs the REAL code on the offline world of
``tests/flowkit.py`` and is compared with a golden file in
``tests/golden/5e/copies/``. Besides what the owner sees, every record
holds the will as stored in the wallet file ("stored") and the heirs as
stored, because several copies differ in what they save.

Nothing here is a judgement: a golden file records today's behaviour,
defects included. ``test_5e_divergences.py`` names each difference between
the copies; the owner decides, step by step, which behaviour the single
engine keeps.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_5e_char_copies.py
Update the golden files (only with a CHANGELOG line):
    BAL_GOLDEN_UPDATE=1 ... python3 -m pytest tests/test_5e_char_copies.py
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

# Test value: the password of the throw-away test wallets that have one.
TEST_PASSWORD = "flowkit-test"


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


def sent_will(make, **kw):
    """A will built, signed, sent and verified by the Check button."""
    s = make(**kw)
    s.check()
    return s


# ------------------------------------------------------------ GUI: Prepare

def prepare_first_will(make):
    """No will yet: Prepare builds it (not signed, not sent)."""
    s = make()
    s.prepare()
    return s


def prepare_coherent(make):
    """A will in force, nothing changed: Prepare."""
    s = sent_will(make)
    s.prepare()
    return s


def prepare_fee_changed(make):
    """A sent will, the fee changes: Prepare."""
    s = sent_will(make)
    s.recorder.note("fee 14")
    s.set_fee(14)
    s.prepare()
    return s


def prepare_new_coin(make):
    """A sent will, a second coin arrives: Prepare (#107 / #110 area)."""
    s = sent_will(make)
    s.recorder.note("new coin 250000")
    s.add_coin(250_000)
    s.prepare()
    return s


def prepare_date_later(make):
    """A sent will, delivery later: Prepare (postpone)."""
    s = sent_will(make)
    s.recorder.note("date later 2033-09-23")
    s.set_date(fk.LATER)
    s.prepare()
    return s


def prepare_expired(make):
    """The delivery date passes: Prepare."""
    s = sent_will(make, locktime=SOON)
    s.recorder.note("70 days pass")
    s.clock.travel(70)
    s.prepare()
    return s


def prepare_no_heirs(make):
    """No heir: Prepare."""
    s = make()
    s.set_heirs()
    s.prepare()
    return s


def prepare_no_willexecutor(make):
    """No will-executor selected: Prepare."""
    s = make()
    for we in s.world.executors.values():
        we["selected"] = False
    s.prepare()
    return s


def prepare_fixed_amount_too_big(make):
    """A fixed amount over the balance: Prepare."""
    s = make(mode="advanced")
    s.set_heirs(alice="2000000", bob="40%")
    s.prepare()
    return s


def prepare_percentages_80(make):
    """Advanced, percentages add up to 80 %: Prepare."""
    s = make(mode="advanced")
    s.set_heirs(alice="50%", bob="30%")
    s.prepare()
    return s


def prepare_fee_too_high(make):
    """A fee the balance cannot pay: Prepare."""
    s = make(fee=10000)
    s.prepare()
    return s


def prepare_check_alive_renew(make):
    """Advanced, relative delivery, Check Alive passed: Prepare, then Renew.

    Since #123 the old shared "60d" becomes a fixed date when the window
    opens: Prepare says the date is fixed (the renewal comes back with
    #126)."""
    s = sent_will(make, mode="advanced", locktime="60d", threshold="-30d")
    s.recorder.note("40 days pass")
    s.clock.travel(40)
    s.prepare()
    return s


def prepare_check_alive_fixed_date(make):
    """Advanced, fixed delivery, Check Alive passed: Prepare."""
    s = sent_will(make, mode="advanced", locktime=SOON, threshold="-30d")
    s.recorder.note("40 days pass")
    s.clock.travel(40)
    s.prepare()
    return s


# ------------------------------------------- GUI: Sign, Broadcast, check

def menus_prepare_sign_broadcast_check(make):
    """Prepare, then the menus Sign and Broadcast will, then row check."""
    s = make()
    s.prepare()
    s.menu_sign()
    s.menu_broadcast()
    s.row_check()
    return s


def menu_sign_cancel(make):
    """Wallet with a password: Prepare, then Sign and Cancel the password."""
    s = make(password=TEST_PASSWORD)
    s.prepare()
    s.menu_sign(cancel_on=("password",))
    return s


def menu_sign_with_password(make):
    """Wallet with a password: Prepare, then Sign with the password."""
    s = make(password=TEST_PASSWORD)
    s.prepare()
    s.menu_sign()
    return s


def menu_broadcast_executor_down(make):
    """Prepare and Sign, we1 does not answer, then Broadcast will."""
    s = make()
    s.prepare()
    s.menu_sign()
    s.recorder.note("we1 does not answer")
    s.world.down.add(WE1)
    s.menu_broadcast()
    return s


def row_check_never_sent(make):
    """Prepare only (nothing sent), then the row command check."""
    s = make()
    s.prepare()
    s.row_check()
    return s


def menu_invalidate_sent(make):
    """A sent will: the menu Invalidate will."""
    s = sent_will(make)
    s.menu_invalidate()
    return s


def menu_invalidate_nothing(make):
    """No will at all: the menu Invalidate will."""
    s = make()
    s.menu_invalidate()
    return s


def wallet_close_fee_changed(make):
    """A sent will, the fee changes, the wallet closes."""
    s = sent_will(make)
    s.recorder.note("fee 14")
    s.set_fee(14)
    s.close_wallet()
    return s


# ------------------------------------------- GUI: automatic rebuild

def auto_no_will(make):
    """No will yet: the automatic rebuild."""
    s = make()
    s.auto()
    return s


def auto_coherent(make):
    """A will in force, nothing changed: the automatic rebuild."""
    s = sent_will(make)
    s.auto()
    return s


def auto_new_coin(make):
    """A sent will, a second coin arrives: the automatic rebuild."""
    s = sent_will(make)
    s.recorder.note("new coin 250000")
    s.add_coin(250_000)
    s.auto()
    return s


def auto_new_coin_after_menu_window(make):
    """As auto_new_coin, after a "Please wait" window was used and closed."""
    s = sent_will(make)
    s.recorder.note("menu Invalidate will, window closed without signing")
    s.menu_invalidate(cancel_on=("transaction window",))
    s.recorder.note("new coin 250000")
    s.add_coin(250_000)
    s.auto()
    return s


def auto_fee_changed(make):
    """A sent will, the fee changes: the automatic rebuild."""
    s = sent_will(make)
    s.recorder.note("fee 14")
    s.set_fee(14)
    s.auto()
    return s


def auto_date_later_offline(make):
    """A sent will, delivery later, no network: the automatic rebuild."""
    s = sent_will(make)
    s.recorder.note("date later 2033-09-23")
    s.set_date(fk.LATER)
    s.auto()
    return s


def auto_date_later_online(make):
    """A sent will, delivery later, with a network: the automatic rebuild."""
    s = sent_will(make)
    s.recorder.note("date later 2033-09-23; the wallet has a network")
    s.set_date(fk.LATER)
    s.auto(network=True)
    return s


def auto_expired_online(make):
    """The delivery date passes, with a network: the automatic rebuild."""
    s = sent_will(make, locktime=SOON)
    s.recorder.note("70 days pass; the wallet has a network")
    s.clock.travel(70)
    s.auto(network=True)
    return s


def auto_check_alive_passed(make):
    """Advanced, relative delivery, Check Alive passed: automatic rebuild.

    Since #123 a fixed date (see :func:`prepare_check_alive_renew`)."""
    s = sent_will(make, mode="advanced", locktime="60d", threshold="-30d")
    s.recorder.note("40 days pass")
    s.clock.travel(40)
    s.auto()
    return s


def auto_new_coin_password(make):
    """Wallet with a password, a second coin arrives: automatic rebuild."""
    s = sent_will(make, password=TEST_PASSWORD)
    s.recorder.note("new coin 250000")
    s.add_coin(250_000)
    s.auto()
    return s


def auto_auto_sign_off(make):
    """Advanced with Auto-sign off, a second coin arrives: automatic rebuild."""
    s = sent_will(make, mode="advanced")
    s.recorder.note("Auto-sign off; new coin 250000")
    s.plugin.AUTO_SIGN.set(False)
    s.add_coin(250_000)
    s.auto()
    return s


# ------------------------------------------- command line

def cli_first_will(make):
    """No will: status, prepare, sign, broadcast, check_executor, status."""
    s = make()
    s.cli("will_status")
    s.cli("prepare_will")
    s.cli("sign_transactions", None)
    s.cli("broadcast_command")
    s.cli("check_transactions")
    s.cli("will_status")
    return s


def cli_coherent(make):
    """A will sent by the Check button: will_check, prepare_will, autorebuild."""
    s = sent_will(make)
    s.cli("will_check")
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_fee_changed(make):
    """A sent will, the fee changes: prepare_will, then autorebuild."""
    s = sent_will(make)
    s.recorder.note("fee 14")
    s.set_fee(14)
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_new_coin(make):
    """A sent will, a second coin arrives: autorebuild."""
    s = sent_will(make)
    s.recorder.note("new coin 250000")
    s.add_coin(250_000)
    s.cli("auto_rebuild")
    return s


def cli_date_later(make):
    """A sent will, delivery later: prepare_will, then autorebuild."""
    s = sent_will(make)
    s.recorder.note("date later 2033-09-23")
    s.set_date(fk.LATER)
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_expired(make):
    """The delivery date passes: prepare_will, then autorebuild."""
    s = sent_will(make, locktime=SOON)
    s.recorder.note("70 days pass")
    s.clock.travel(70)
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_one_heir_50(make):
    """Only alice at 50 % (the README example): prepare_will."""
    s = make()
    s.set_heirs(alice="50%")
    s.cli("prepare_will")
    return s


def cli_fixed_amounts_only(make):
    """Advanced, only fixed amounts: prepare_will."""
    s = make(mode="advanced")
    s.set_heirs(alice="300000", bob="200000")
    s.cli("prepare_will")
    return s


def cli_percentages_80(make):
    """Advanced, percentages add up to 80 %: prepare_will, autorebuild."""
    s = make(mode="advanced")
    s.set_heirs(alice="50%", bob="30%")
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_no_heirs(make):
    """No heir: prepare_will, autorebuild."""
    s = make()
    s.set_heirs()
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_no_willexecutor(make):
    """No will-executor selected: prepare_will, autorebuild."""
    s = make()
    for we in s.world.executors.values():
        we["selected"] = False
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_executor_down(make):
    """Prepare and sign, we1 does not answer: broadcast."""
    s = make()
    s.cli("prepare_will")
    s.cli("sign_transactions", None)
    s.recorder.note("we1 does not answer")
    s.world.down.add(WE1)
    s.cli("broadcast_command")
    return s


def cli_check_alive_fixed_date(make):
    """Advanced, fixed delivery, Check Alive passed: check, prepare, autorebuild."""
    s = sent_will(make, mode="advanced", locktime=SOON, threshold="-30d")
    s.recorder.note("40 days pass")
    s.clock.travel(40)
    s.cli("will_check")
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_invalidate_nothing(make):
    """No will at all: invalidate."""
    s = make()
    s.cli("invalidate_will_command")
    return s


def cli_password_wallet(make):
    """Wallet with a password, a second coin arrives: autorebuild, then sign."""
    s = sent_will(make, password=TEST_PASSWORD)
    s.recorder.note("new coin 250000")
    s.add_coin(250_000)
    s.cli("auto_rebuild")
    s.cli("sign_transactions", TEST_PASSWORD)
    return s


def cli_invalid_heir_in_wallet(make):
    """An heir with an address of another network is in the wallet.

    The Check button keeps it in the wallet; a command-line read deletes it.
    """
    s = make()
    s.recorder.note("heir 'mallory' with a mainnet address added")
    s.bw.heirs["mallory"] = ["bc1qusymuetsz2psaqzqxv8qmzcy64d9meckj3lxxf", "10%",
                             s.bw.will_settings["locktime"]]
    s.check()
    s.cli("heirs_list")
    return s


# ------------------------------------------- the same state, several copies

def all_executors_deselected_after_sent(make):
    """A sent will, then every will-executor deselected: Check, Prepare, CLI."""
    s = sent_will(make)
    s.recorder.note("every will-executor deselected")
    for we in s.world.executors.values():
        we["selected"] = False
    s.check()
    s.prepare()
    s.cli("prepare_will")
    return s


def unsigned_will_auto_then_cli(make):
    """Prepare only (not signed, not sent): automatic rebuild, then CLI."""
    s = make()
    s.prepare()
    s.auto()
    s.cli("auto_rebuild")
    return s


def unsigned_will_check(make):
    """Prepare only (not signed, not sent): then the Check button."""
    s = make()
    s.prepare()
    s.check()
    return s


def executor_fee_raised_after_sent(make):
    """A sent will; we1 raises its fee over the maximum: Check, CLI."""
    s = sent_will(make)
    s.recorder.note("we1 asks 10,000,000 sat (over the maximum fee)")
    s.world.executors[WE1]["base_fee"] = 10_000_000
    s.check()
    s.cli("broadcast_command", True)
    return s


def cli_date_in_past(make):
    """Delivery date in the past: prepare_will, autorebuild."""
    s = make()
    s.set_date(fk.PAST)
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def cli_fee_too_high(make):
    """A fee the balance cannot pay: prepare_will, autorebuild."""
    s = make(fee=10000)
    s.cli("prepare_will")
    s.cli("auto_rebuild")
    return s


def prepare_check_alive_expired_advanced(make):
    """Advanced, the delivery date itself passes: Prepare."""
    s = sent_will(make, mode="advanced", locktime=SOON, threshold="-30d")
    s.recorder.note("70 days pass")
    s.clock.travel(70)
    s.prepare()
    return s


def export_gui_vs_cli(make):
    """A sent will exported to a file by the GUI, then by the command line."""
    s = sent_will(make)
    path = os.path.join(s.dir, "will.json")
    s.act("gui export file", lambda: s.bw.export_json_file(path))
    s.cli("export_will", path)
    return s


SCENARIOS = [
    # GUI: Prepare
    prepare_first_will, prepare_coherent, prepare_fee_changed,
    prepare_new_coin, prepare_date_later, prepare_expired, prepare_no_heirs,
    prepare_no_willexecutor, prepare_fixed_amount_too_big,
    prepare_percentages_80, prepare_fee_too_high, prepare_check_alive_renew,
    prepare_check_alive_fixed_date,
    # GUI: menus, row check, wallet close
    menus_prepare_sign_broadcast_check, menu_sign_cancel,
    menu_sign_with_password, menu_broadcast_executor_down,
    row_check_never_sent, menu_invalidate_sent, menu_invalidate_nothing,
    wallet_close_fee_changed,
    # GUI: automatic rebuild
    auto_no_will, auto_coherent, auto_new_coin,
    auto_new_coin_after_menu_window, auto_fee_changed,
    auto_date_later_offline, auto_date_later_online, auto_expired_online,
    auto_check_alive_passed, auto_new_coin_password, auto_auto_sign_off,
    # command line
    cli_first_will, cli_coherent, cli_fee_changed, cli_new_coin,
    cli_date_later, cli_expired, cli_one_heir_50, cli_fixed_amounts_only,
    cli_percentages_80, cli_no_heirs, cli_no_willexecutor, cli_executor_down,
    cli_check_alive_fixed_date, cli_invalidate_nothing, cli_password_wallet,
    cli_invalid_heir_in_wallet, cli_date_in_past, cli_fee_too_high,
    # the same state through several copies
    all_executors_deselected_after_sent, unsigned_will_auto_then_cli,
    unsigned_will_check, executor_fee_raised_after_sent,
    prepare_check_alive_expired_advanced, export_gui_vs_cli,
]

SCENARIO_NAMES = {f.__name__ for f in SCENARIOS}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_copies_golden(session, scenario):
    """Today's copy, scenario by scenario, equals its golden file."""
    s = scenario(session)
    fk.check_golden("copies/" + scenario.__name__, s.recorder.steps)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
