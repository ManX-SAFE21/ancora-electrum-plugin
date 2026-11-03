"""The delivery settings on the command line (CHANGELOG #123).

Each wallet keeps its own delivery date and fee rate (wallet key
``will_settings``). The command line shows them with ``bal_delivery_show``
and changes them with ``bal_delivery_set``:

* ``--date YYYY-MM-DD`` (00:00 UTC) or a UNIX timestamp; a period, a block
  height or a date not in the future is refused; a date EARLIER than the
  will in force needs ``--confirm_earlier true``;
* ``--fee N``, a fee rate from 1 to 10000 (the GUI fee box);
* ``--keep true`` answers the "which date?" question by keeping the date of
  the prepared will.

``bal_settings_get/set/reset`` refuse Electrum's global ``bal_will_settings``
with a pointer to the two commands: Safe21 never writes it again (rule I1).
The controller reads the scenario clock (``tests/flowkit.py`` patches
``bal.cli.controller.time``).

Wallets, coins and will-executors come from ``tests/flowkit.py``: three fake
will-executors (each with its own fee address), throw-away test wallets with
the test password, a fixed clock. Nothing reaches a real will-executor.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_cli_delivery_commands.py
"""

import os
import sys
import threading
import time
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.util import UserFacingException
from PyQt6.QtWidgets import QApplication
from test_cli_command_layer import run
from test_cli_controller_offline import Plugin as CliPlugin

from bal.cli import commands as cli_commands
from bal.cli.controller import BalController
from bal.core import delivery
from bal.engine import context as engine_context

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"

# The owner's will in force: 2029-08-29 00:00 UTC.
OWNER_DATE = fk.ts(2029, 8, 29)
# The old shared value of the owner's Electrum (Safe21 before #123).
OLD_GLOBAL = {"locktime": "1054d", "baltx_fees": 13, "threshold": "-180d"}
# The prefix of the will-executor pseudo-heirs inside a will item.
_EXECUTOR_HEIR = 'w!ll3x3c"'
# The text that points from the old global to the new commands.
_MOVED = ("bal_will_settings is not used any more: each wallet keeps its own "
          "delivery date and fee rate. See them with bal_delivery_show and "
          "change them with bal_delivery_set (--date YYYY-MM-DD, --fee N).")


@pytest.fixture
def make(monkeypatch):
    """Start ``tests/flowkit.py`` sessions with three will-executors.

    Electrum's asyncio loop runs during the test and is stopped after it
    (else pytest never exits); every session's folder is removed.
    """
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


# ------------------------------------------------------------------ helpers

def wallet_dict(s):
    """The wallet's own delivery settings (the live dict of the wallet file)."""
    return s.wallet.db.get_dict("will_settings")


def returned(result):
    """What the command returned (``Session.cli`` wraps it)."""
    assert "returned" in result, result
    return result["returned"]


def raised(result):
    """The message of the error the command raised, as the terminal shows it."""
    assert "raised" in result, result
    kind, _sep, text = result["raised"].partition(": ")
    assert kind == "UserFacingException", result
    return text


def dated(ts):
    """``{"locktime": ts, "date": "YYYY-MM-DD"}``, as the commands return it."""
    return {"locktime": ts, "date": fk.fmt(ts)}


def back_before_123(s, old_global, heirs_recipe=None, item_recipe=None):
    """The wallet as the code before #123 left it; no window opens it again.

    The wallet's own dict is emptied (Safe21 before #123 never wrote it),
    ``old_global`` is written into Electrum's global (as the older plugin
    left it) and taken as the rule-I1 reference; ``heirs_recipe`` and
    ``item_recipe`` (if given) become the heirs' date and the recipe stored
    in the will items. The next command migrates.
    """
    d = wallet_dict(s)
    for key in list(d):
        del d[key]
    if heirs_recipe is not None:
        for name in list(s.bw.heirs):
            heir = list(s.bw.heirs[name])
            heir[2] = heirs_recipe
            s.bw.heirs[name] = heir
    if item_recipe is not None:
        will = s.wallet.db.get_dict("will")
        for wid in list(will):
            heirs = will[wid]["heirs"]
            for name in list(heirs):
                if not name.startswith(_EXECUTOR_HEIR):
                    heir = list(heirs[name])
                    heir[2] = item_recipe
                    heirs[name] = heir
    s.config.set_key("bal_will_settings", dict(old_global))
    s.snapshot_global()
    return d


def watch_patches(s, monkeypatch):
    """Record every change written to the wallet file from now on."""
    patches = []
    real = s.wallet.db.add_patch

    def add_patch(patch):
        """Record one change, then make it."""
        patches.append(patch)
        return real(patch)

    monkeypatch.setattr(s.wallet.db, "add_patch", add_patch)
    return patches


def pending_question(make):
    """A wallet whose "which date?" question waits (see the flow tests).

    A will at 07-06 signed and sent; the heirs change and "Sign your will"
    is cancelled (VALID at 07-05, not signed). Both wills were built from
    the shared "1000d"; one day later the shared value is "1010d" (a later
    date, 07-15, never carried out). The command line migrates it.
    """
    s = make(locktime=fk.ts(2029, 7, 6))
    s.check()
    s.set_heirs(alice="50%", bob="30%")
    s.check(cancel_on={"Sign your will"})
    s.clock.travel(1)
    back_before_123(s, {"locktime": "1010d", "baltx_fees": 13, "threshold": "-180d"},
                    heirs_recipe="1010d", item_recipe="1000d")
    return s


# --------------------------------------------------------- bal_delivery_show

def test_show_migrates_and_reports_the_owner_wallet(make):
    """The first command after the update migrates the owner's wallet to
    the date of his will (the command line opens it before any window):
    ``bal_delivery_show`` reports it, with the old value and the note the
    window will show (the command line does not consume it). The migration
    time is the scenario clock's: the command line's clock is patched."""
    s = make(locktime=OWNER_DATE)
    s.check()
    back_before_123(s, OLD_GLOBAL, heirs_recipe="1054d")
    s.clock.travel(2)
    out = returned(s.cli("delivery_show"))
    assert out == {
        "date": "2029-08-29",
        "locktime": OWNER_DATE,
        "baltx_fees": 13,
        "threshold": "-180d",
        "settings_version": 2,
        "migrated_from": "will",
        "migrated_at": fk.FIXED_NOW + 2 * fk.DAY,
        "legacy_locktime": "1054d",
        "note": {"kind": "converted", "old": None, "new": dated(OWNER_DATE)},
        "choice_pending": False,
        "will_in_force": dated(OWNER_DATE),
        "settings_unavailable": False,
        # #127: the delivery form; the will is signed and sent, so locked.
        "form": "fixed",
        "period_months": None,
        "period": None,
        "period_start": None,
        "locked": True,
        # #169: no table of the deliveries (one delivery).
        "staged": False,
        "deliveries": [],
    }
    assert wallet_dict(s)["note_kind"] == "converted"
    # The same through Electrum's own command wrappers (``-w wallet``).
    assert run(s.plugin, "delivery_show", wallet=s.wallet) == out
    assert returned(s.cli("will_check")) == {"valid": True}
    s.assert_global_unchanged()


# ---------------------------------------------------------- bal_delivery_set

def test_set_a_later_date_and_a_fee(make):
    """A later date is stored at 00:00 UTC; with a will in force the answer
    says that the next prepare asks to invalidate it (miner fee), and the
    GUI Check does. A fee rate given as text (JSON-RPC) is stored as a
    number."""
    s = make(locktime=fk.D0)
    s.check()
    out = returned(s.cli("delivery_set", date="2033-01-01"))
    assert (out["date"], out["locktime"]) == ("2033-01-01", fk.ts(2033, 1, 1))
    assert out["message"] == (
        "Stored. The new date is later than the will in force (2032-09-23). "
        "If that will was already sent to the will-executors, the next "
        "bal_will_prepare asks to invalidate it first (on-chain, miner fee). "
        "Run bal_will_prepare to prepare the will with the new settings.")
    assert s.bw.will_settings["locktime"] == fk.ts(2033, 1, 1)   # one dict
    out = returned(s.cli("delivery_set", fee="15"))
    assert out["baltx_fees"] == 15 and wallet_dict(s)["baltx_fees"] == 15
    s.check(cancel_on={"Invalidate your old will"})
    questions = [q[1] for q in s.recorder.steps[-1]["questions"]]
    assert "Invalidate your old will" in questions
    assert s.recorder.steps[-1]["broadcasts"] == []
    s.assert_global_unchanged()


def test_an_earlier_date_needs_confirm_earlier(make, monkeypatch):
    """A date EARLIER than the will in force would let the heirs receive
    earlier: refused (nothing written) unless ``--confirm_earlier true``.
    With no will in force the stored date is the reference."""
    s = make(locktime=fk.D0)
    s.check()
    patches = watch_patches(s, monkeypatch)
    text = raised(s.cli("delivery_set", date="2031-09-23"))
    assert text == (
        "2031-09-23 is earlier than the will in force (2032-09-23): the heirs "
        "could receive the bitcoin earlier. If this is what you want, run the "
        "command again with --confirm_earlier true.")
    assert patches == [] and wallet_dict(s)["locktime"] == fk.D0
    out = returned(s.cli("delivery_set", date="2031-09-23", confirm_earlier=True))
    assert out["locktime"] == fk.EARLIER

    other = make(locktime=fk.D0)                     # no will yet
    text = raised(other.cli("delivery_set", date="2031-09-23"))
    assert text.startswith(
        "2031-09-23 is earlier than the current delivery date (2032-09-23).")
    assert wallet_dict(other)["locktime"] == fk.D0


def test_a_sent_will_with_no_valid_item_is_the_will_in_force(make):
    """No VALID item, the sent will still pending at the will-executors
    (the migration source "sent"): ``bal_delivery_show`` reports it as the
    will in force, and a later ``--date`` warns that the next prepare asks
    to invalidate it (miner fee). The ``--confirm_earlier`` reference stays
    the stored date."""
    s = make(locktime=OWNER_DATE)
    s.check()
    will = s.wallet.db.get_dict("will")
    for wid in list(will):              # as another program can leave them
        assert will[wid]["PUSHED"] is True
        will[wid]["VALID"] = False
        will[wid]["REPLACED"] = True
    show = returned(s.cli("delivery_show"))
    assert show["will_in_force"] == dated(OWNER_DATE)
    assert show["locktime"] == OWNER_DATE
    text = raised(s.cli("delivery_set", date="2029-08-15"))
    assert text.startswith(
        "2029-08-15 is earlier than the current delivery date (2029-08-29).")
    assert wallet_dict(s)["locktime"] == OWNER_DATE
    out = returned(s.cli("delivery_set", date="2029-09-30"))
    assert out["message"] == (
        "Stored. The new date is later than the will in force (2029-08-29). "
        "If that will was already sent to the will-executors, the next "
        "bal_will_prepare asks to invalidate it first (on-chain, miner fee). "
        "Run bal_will_prepare to prepare the will with the new settings.")
    assert out["will_in_force"] == dated(OWNER_DATE)
    assert wallet_dict(s)["locktime"] == fk.ts(2029, 9, 30)
    assert s.world.broadcast_log == []
    s.assert_global_unchanged()


@pytest.mark.parametrize("kwargs, words", [
    ({"date": "1054d"}, "A period such as 1054d is not accepted"),
    ({"date": "1y"}, "A period such as 1y is not accepted"),
    ({"date": "36m"}, "A period such as 36m is not accepted"),
    ({"date": "-180d"}, "A period such as -180d is not accepted"),
    ({"date": "800000"}, "block height"),
    ({"date": "2020-01-01"}, "is not in the future"),
    ({"date": "2026-10-10"}, "is not in the future"),          # today
    ({"date": "2029-02-30"}, "No such day"),
    ({"date": "29/08/2029"}, "Not a date"),
    ({"date": "4294967296"}, "is too late"),
    ({"fee": 0}, "not a fee rate"),
    ({"fee": "abc"}, "Not a fee rate"),
    ({"fee": True}, "Not a fee rate"),
    ({"fee": 10001}, "The fee rate 10001 is too high: at most 10000 sat/vByte."),
    ({"fee": "50000"}, "The fee rate 50000 is too high"),
    ({"fee": 1e6}, "is too high: at most 10000"),
    ({"fee": "10000.5"}, "is too high: at most 10000"),
    ({}, "Nothing to change"),
    ({"date": "2040-01-01", "keep": True}, "not both"),
    ({"keep": "maybe"}, "Expected true or false for keep"),
    ({"date": "2040-01-01", "fee": 0}, "not a fee rate"),
])
def test_refused_values_write_nothing(make, monkeypatch, kwargs, words):
    """Every refused call explains why and writes nothing at all (the date
    and the fee are checked before any write)."""
    s = make(locktime=fk.D0)
    before = dict(wallet_dict(s))
    patches = watch_patches(s, monkeypatch)
    text = raised(s.cli("delivery_set", **kwargs))
    assert words in text, text
    assert patches == []
    assert dict(wallet_dict(s)) == before


def test_fee_rates_up_to_the_gui_maximum_are_stored(make):
    """The fee rates the GUI fee box can hold are stored as given: 10000
    (its maximum) and a float of at least 1 (JSON-RPC); only a rate above
    10000 is refused (see ``test_refused_values_write_nothing``)."""
    s = make(locktime=fk.D0)
    for fee, stored in ((10000, 10000), ("10000", 10000), (14.5, 14.5),
                        ("1.5", 1.5), (1, 1)):
        out = returned(s.cli("delivery_set", fee=fee))
        assert out["baltx_fees"] == stored, (fee, out)
        assert wallet_dict(s)["baltx_fees"] == stored
    s.assert_global_unchanged()


# ------------------------------------------------- the "which date?" question

def test_keep_answers_the_question(make):
    """While the question waits, the checking commands return the code
    ``delivery_choice_pending`` with both dates and the commands that answer
    it; sign, broadcast and invalidate are refused. ``--keep true`` keeps
    the prepared will's date; then the will checks again and the GUI Check
    signs it without asking the question."""
    s = pending_question(make)
    message = (
        "The delivery date must be confirmed first, so nothing was built, "
        "signed or sent. The will prepared for 2029-07-05 is not signed and "
        "sent yet, and an older setting asked for 2029-07-15. To keep "
        "2029-07-05 (no fee): bal_delivery_set --keep true. To use a later "
        "date (if the old will was sent, it must be invalidated first, miner "
        "fee): bal_delivery_set --date YYYY-MM-DD. Details: bal_delivery_show.")
    assert returned(s.cli("will_check")) == {
        "valid": False, "result": "delivery_choice_pending", "message": message}
    for method in ("prepare_will", "auto_rebuild"):
        assert returned(s.cli(method)) == {
            "result": "delivery_choice_pending", "message": message}
    for method, args in (("sign_transactions", (None,)),
                         ("push_transactions_to_willexecutors", ()),
                         ("invalidate_will", ())):
        assert raised(s.cli(method, *args)) == message, method
    out = returned(s.cli("delivery_show"))
    assert out["choice_pending"] is True
    assert out["choice"] == {"keep": dated(fk.ts(2029, 7, 5)),
                             "later": dated(fk.ts(2029, 7, 15))}
    assert out["note"]["kind"] == "later_pending"
    assert s.world.pushed[3:] == [] and s.world.broadcast_log == []

    out = returned(s.cli("delivery_set", keep=True))
    assert out["message"] == "The date of the prepared will is kept: 2029-07-05."
    assert out["choice_pending"] is False and "choice" not in out
    d = wallet_dict(s)
    assert d["locktime"] == fk.ts(2029, 7, 5)
    assert delivery.CHOICE_KEY not in d and "note_kind" not in d
    assert returned(s.cli("will_check")) == {"valid": True}
    s.reload_window()
    s.check()
    step = s.recorder.steps[-1]
    assert [q[1] for q in step["questions"]] == ["Sign your will"]
    assert len(step["pushed"]) == 3 and step["broadcasts"] == []


def test_a_new_date_answers_the_question(make):
    """A date given with ``--date`` answers the question too (here the
    later date): the question is gone and the answer says that the next
    prepare asks for the invalidation of the sent will."""
    s = pending_question(make)
    out = returned(s.cli("delivery_set", date="2029-07-15"))
    assert out["locktime"] == fk.ts(2029, 7, 15)
    assert out["choice_pending"] is False
    assert "later than the will in force (2029-07-05)" in out["message"]
    assert delivery.CHOICE_KEY not in wallet_dict(s)


def test_keep_without_a_question(make):
    """``--keep true`` with no question pending changes nothing and says so."""
    s = make(locktime=fk.D0)
    before = dict(wallet_dict(s))
    out = returned(s.cli("delivery_set", keep=True))
    assert out["message"] == "No question was pending: nothing to keep."
    assert dict(wallet_dict(s)) == before


# --------------------------------------------------------------- fail closed

def test_settings_unavailable_on_the_command_line(make, monkeypatch):
    """When the settings cannot be prepared (an injected error while the
    will items are read) every command says so and nothing is written,
    built, signed, sent or broadcast."""
    s = make(locktime=fk.D0)
    s.check()
    back_before_123(s, OLD_GLOBAL)
    pushed = len(s.world.pushed)

    def broken(host, willitems=None):
        """The item reading fails."""
        raise RuntimeError("injected: the will items cannot be read")

    monkeypatch.setattr(engine_context, "will_views", broken)
    message = ("The delivery settings of this wallet could not be prepared: "
               "nothing was changed. Please restart Electrum and send the log.")
    out = returned(s.cli("delivery_show"))
    assert out["settings_unavailable"] is True and out["message"] == message
    assert out["settings_version"] is None
    assert returned(s.cli("will_check"))["result"] == "delivery_settings_unavailable"
    for method in ("prepare_will", "auto_rebuild"):
        assert returned(s.cli(method)) == {
            "result": "delivery_settings_unavailable", "message": message}
    assert raised(s.cli("delivery_set", fee=10)) == message
    assert raised(s.cli("sign_transactions", None)) == message
    assert dict(wallet_dict(s)) == {}
    assert len(s.world.pushed) == pushed and s.world.broadcast_log == []
    s.assert_global_unchanged()


# ------------------------------------------------- the old global, the clock

@pytest.mark.parametrize("command, args", [
    ("settings_get", ("bal_will_settings",)),
    ("settings_get", ("WILL_SETTINGS",)),
    ("settings_set", ("bal_will_settings", '{"locktime": "2y"}')),
    ("settings_reset", ("bal_will_settings",)),
    ("settings_reset", ("WILL_SETTINGS",)),
])
def test_settings_commands_refuse_the_global_will_settings(command, args):
    """``bal_settings_*`` on ``bal_will_settings`` give the pointer to
    ``bal_delivery_show`` / ``bal_delivery_set``; the global (here the old
    "1054d") is neither changed nor listed."""
    with CliPlugin() as plugin:
        plugin.config.set_key("bal_will_settings", dict(OLD_GLOBAL))
        with pytest.raises(UserFacingException) as info:
            run(plugin, command, *args)
        assert str(info.value) == _MOVED
        assert "WILL_SETTINGS" not in run(plugin, "settings_list")
        assert plugin.config.get("bal_will_settings") == OLD_GLOBAL


def test_delivery_commands_are_registered_and_parsed():
    """``bal_delivery_show`` / ``bal_delivery_set`` need a wallet (no
    password), every argument is described, and Electrum's own parser reads
    the options (``--keep true`` is a bool)."""
    from electrum.commands import get_parser, known_commands

    for name in ("bal_delivery_show", "bal_delivery_set"):
        cmd = known_commands[name]
        assert cmd.requires_wallet and not cmd.requires_password, name
        for arg in list(cmd.params) + list(cmd.options):
            if arg not in ("wallet", "wallet_path", "plugin", "password"):
                assert arg in cmd.arg_descriptions, (name, arg)
    assert known_commands["bal_delivery_set"].arg_types == {
        "date": "str", "fee": "int", "keep": "bool", "confirm_earlier": "bool",
        "months": "int"}
    args = get_parser().parse_args(
        ["bal_delivery_set", "--date", "2040-01-01", "--fee", "17", "--keep",
         "true", "--confirm_earlier", "false"])
    assert (args.date, args.fee, args.keep, args.confirm_earlier) == (
        "2040-01-01", 17, True, False)


def test_commands_read_the_scenario_clock(make):
    """The controller's "now" is the scenario clock (``flowkit`` patches
    ``bal.cli.controller.time``): a date still in the future for the
    machine's clock is refused once the scenario clock has passed it."""
    s = make(locktime=fk.D0)
    out = returned(s.cli("delivery_set", date="2035-01-01"))
    assert out["locktime"] == fk.ts(2035, 1, 1)
    s.clock.travel(3650)                                      # 2036-10-07
    assert time.time() < fk.ts(2035, 1, 1)                     # machine clock
    text = raised(s.cli("delivery_set", date="2035-01-01", confirm_earlier=True))
    assert text == "2035-01-01 00:00 UTC is not in the future."


def test_heir_date_points_to_bal_delivery_set(make):
    """With multiverse off every heir gets the will's date: a date for one
    heir is refused with the pointer to ``bal_delivery_set --date``."""
    s = make(locktime=fk.D0)
    text = raised(s.cli("heirs_add", "carol", fk.HEIR_ADDR["carol"], "10%",
                        locktime="2y"))
    assert text == (
        "Every heir gets the delivery date of the will, so --locktime cannot "
        "be set for one heir. To change the date for every heir, use "
        "bal_delivery_set --date YYYY-MM-DD (see the current date with "
        "bal_delivery_show).")


def test_through_electrum_commands_the_window_sees_the_change(make):
    """``bal_delivery_set`` through Electrum's command wrappers (as from a
    terminal, one command at a time under the wallet's flow lock): the
    window works on the same dict and sees the new values at once."""
    s = make(locktime=fk.D0)
    out = run(s.plugin, "delivery_set", date="2040-01-01", fee=17, wallet=s.wallet)
    assert (out["date"], out["locktime"], out["baltx_fees"]) == (
        "2040-01-01", 2208988800, 17)
    assert s.bw.will_settings["locktime"] == 2208988800
    assert s.bw.will_settings["baltx_fees"] == 17
    s.assert_global_unchanged()


def test_commands_on_one_wallet_run_one_at_a_time(make):
    """Two commands on the same wallet: the second one's controller (and
    so its settings load and migration) waits for the first one."""
    s = make(locktime=fk.D0)
    order = []
    real_init = BalController._init_settings

    def slow_init(self):
        """The settings load, slowed down."""
        order.append("start")
        time.sleep(0.2)
        real_init(self)
        order.append("end")

    with mock.patch.object(BalController, "_init_settings", slow_init):
        thread = threading.Thread(target=cli_commands._call_locked,
                                  args=(s.plugin, s.wallet, "delivery_show"))
        thread.start()
        deadline = time.monotonic() + 10
        while not order and time.monotonic() < deadline:
            time.sleep(0.01)
        assert order == ["start"]
        cli_commands._call_locked(s.plugin, s.wallet, "delivery_show")
        thread.join(10)
    assert order == ["start", "end", "start", "end"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
