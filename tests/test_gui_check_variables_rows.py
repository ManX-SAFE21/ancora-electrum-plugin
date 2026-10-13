"""The rows of the Check's first part: variables, Check Alive, amounts (#120).

"Check your will" starts with the "Checking variables" row: the heirs, the
Check Alive gate and the heirs' amounts (``BalBuildWillDialog.task_phase1``
up to "Checking your will"). CHANGELOG #120 moves that part into the engine
(``bal.engine.check.check_variables``). This file was written and run on
the code BEFORE the move and is unchanged after it: it photographs what
the dialog wrote, returned and kept in every branch, also the branches the
golden files of #112/#114 do not reach (Check Alive after the delivery,
an invalidation sent but not seen yet, a Check Alive with no information,
a renewal with no transaction, a will-executor fee too high, other errors,
a dialog already closing).

The real dialog class runs on a dialog built without Qt set-up (like the
scenario harness) whose window is a stub; ``Util`` and ``Will`` are patched
on their classes, where the old and the new code both reach them. The last
test runs the real flow of ``tests/flowkit.py``.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_check_variables_rows.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum import constants
from electrum.segwit_addr import encode_segwit_address
from PyQt6.QtWidgets import QApplication
from test_cli_controller_offline import FakeWallet

import bal.i18n
from bal.core import datetext
from bal.core.checkalive import (
    CheckAliveAfterDeliveryError,
    CheckAliveError,
    check_alive_renewal,
)
from bal.core.heirs import NoHeirsException as HeirsNoHeirsException
from bal.core.heirs import WillExecutorFeeTooHighException
from bal.core.messages import amounts_message, check_alive_message
from bal.core.util import Util
from bal.core.will import (
    FixedAmountException,
    NoHeirsException,
    PercAmountException,
    Will,
)
from bal.gui.qt.dialogs import BalBuildWillDialog

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

WE = ["https://we0.test", "https://we1.test", "https://we2.test"]
MAX_FEE = 5000
DAY = 86400
DATE = 1_900_000_000          # date_to_check (2030)
PAST = 1_577_836_800          # 2020-01-01
FUTURE = 2_011_219_200        # 2033-09-25
TX = object()                 # the renewal invalidation (opaque here)
GOES_ON = "goes on"           # task_phase1 reached "Checking your will"

WAIT = "Checking variables:\t<b>Wait</b>"
OK = "Checking variables:\t<font color=#05ad05><b>Ok</b></font>"
STOPPED = "Checking variables:\t<font color=#ff0000><b>Stopped</b></font>"
EXPIRED = "Checking variables:\t<font color=#ff0000><b>Expired</b></font>"
PASSED = "Checking variables:\t<font color=#cfa808><b>Check Alive passed</b></font>"
NO_HEIRS = (
    "Checking variables:\t<b><font color='#cfa808'>&#9888;</font> "
    "<b>No Heirs</b></b>"
)
AFTER_DELIVERY_TEXT = "Check Alive must be after today and before the delivery date"
AFTER_DELIVERY = (
    "Checking variables:\t<font color=#ff0000><b>{}</b></font>".format(
        AFTER_DELIVERY_TEXT)
)
ADJUST = (
    "In the inheritance process, the entire wallet will always be fully "
    "emptied. \nYour settings require an adjustment of the amounts"
)


def _error(text):
    """A row of its own in red, as ``msg_error`` writes it."""
    return "<font color='#ff0000'><b>{}</b></font>".format(text)


def _alert(text):
    """A row of its own after the amber sign, as ``msg_alert`` writes it."""
    return "<font color='#cfa808'>&#9888;</font> <b>{}</b>".format(text)


def _addr(n):
    """A valid P2WPKH address of the CURRENT network, number ``n``.

    Built at call time: ``tests/flowkit.py`` switches ``constants.net`` to
    testnet for the rest of the run.
    """
    return encode_segwit_address(constants.net.SEGWIT_HRP, 0, bytes([n]) * 20)


def _executors():
    """Three selected will-executors, each with its own fee address (owner
    rule): we0 1000 sat and we2 2000 sat under the maximum fee, we1 50000
    sat above it."""
    fees = {WE[0]: 1000, WE[1]: 50_000, WE[2]: 2000}
    return {url: {"url": url, "selected": True, "base_fee": fee,
                  "address": _addr(i + 1), "status": 200, "info": "fake"}
            for i, (url, fee) in enumerate(fees.items())}


def _utxo(value):
    """A coin with ``value_sats()``."""
    u = MagicMock()
    u.value_sats.return_value = value
    return u


RENEW_NOW = 1_800_000_000 - 1_800_000_000 % DAY   # 2027-01-15
BUILT = RENEW_NOW + 20 * DAY                       # delivery of the will in force


def _settings_of(locktime):
    """The stored settings of :func:`_renewal`: ``"period"`` is the period
    form of 7 calendar months (CHANGELOG #127: only the period form renews;
    before it was the relative "200d"), any other value a fixed date."""
    if locktime == "period":
        return {"locktime": BUILT, "threshold": "-180d",
                "form": "period", "period_months": 7}
    return {"locktime": locktime, "threshold": "-180d"}


def _renewal(locktime):
    """A passed Check Alive as ``check_alive_renewal`` describes it: a will
    in force delivered in 20 days (``BUILT``), its Check Alive 180 days
    before. ``locktime`` is the stored delivery: "period" (the period form
    of :func:`_settings_of`, which
    can be renewed) or the fixed date ``BUILT`` (which cannot)."""
    return check_alive_renewal(
        False, _settings_of(locktime),
        built_locktime=BUILT, creation_date=RENEW_NOW - 300 * DAY,
        now=RENEW_NOW,
    )


class _SliceEndError(Exception):
    """Raised by the stubbed ``msg_set_checking``: the first part is over."""


class _Dialog(BalBuildWillDialog):
    """The real dialog, recording every row ever written (rows are
    overwritten in the dialog), like ``tests/flowkit.py``'s FlowDialog."""

    def msg_edit_row(self, line, row=None):
        """Record the row, then write it as the real dialog does."""
        self.history.append(line)
        return super().msg_edit_row(line, row)


def _dialog(info=None, init_error=None):
    """A dialog without Qt set-up on a stub window.

    The window's wallet is ONE object reached as ``wallet`` and as
    ``window.wallet``, as in every real window. ``init_class_variables``
    raises ``init_error`` (when set) and ``check_alive_info`` returns
    ``info``. ``msg_set_checking`` (the first row after this part) stops
    the run with ``_SliceEndError``.
    """
    d = _Dialog.__new__(_Dialog)
    d.history = []
    d.labels = []
    d.updatemessage = MagicMock()
    d.check_row = d.inval_row = d.build_row = d.sign_row = d.push_row = None
    d._invalidation_broadcast = False
    d._check_alive_info = None
    d.msg_set_checking = MagicMock(side_effect=_SliceEndError)
    bw = MagicMock()
    bw.wallet = bw.window.wallet = FakeWallet()
    bw.willitems = {}
    bw.heirs = MagicMock()
    bw.willexecutors = _executors()
    bw.date_to_check = DATE
    bw.will_settings = {"locktime": FUTURE, "threshold": "-180d",
                        "baltx_fees": 3}
    bw.bal_plugin.HISTORY_LABEL.get.return_value = "label"
    bw.bal_plugin.MAX_WILLEXECUTOR_FEE.get.return_value = MAX_FEE
    bw.bal_plugin.is_basic_mode.return_value = False
    bw._still_active_for_summary.return_value = []
    bw.init_class_variables.side_effect = init_error
    bw.check_alive_info.return_value = info
    d.bal_window = bw
    return d


def _run(d):
    """``task_phase1``'s return value, or ``GOES_ON`` when it reached the
    next part ("Checking your will")."""
    try:
        return d.task_phase1()
    except _SliceEndError:
        return GOES_ON


def _check_step(d):
    """The report's Check step: ``(state, note)``."""
    steps = {s: (state, note) for s, state, note in d._report().snapshot()["steps"]}
    return steps["check"]


def _boxes(d):
    """The report's boxes that are not empty."""
    return {k: v for k, v in d._report().snapshot()["boxes"].items() if v}


@pytest.fixture
def core():
    """``Util`` and ``Will`` patched on their classes: one visible coin of
    1,000,000 sat, the amounts fine, no explanation, no renewal tx."""
    with patch.object(Util, "get_available_utxos",
                      return_value=[_utxo(1_000_000)]) as coins, \
         patch.object(Will, "check_amounts") as amounts, \
         patch.object(Will, "describe_amounts", return_value=None) as describe, \
         patch.object(Will, "invalidate_will", return_value=None) as invalidate:
        yield SimpleNamespace(coins=coins, amounts=amounts, describe=describe,
                              invalidate=invalidate)


# ------------------------------------------------------------ the gate


def test_ok_row(core):
    """Nothing to say: "Ok" on the row, and the check goes on."""
    d = _dialog()
    assert _run(d) == GOES_ON
    assert d.labels == [OK]
    assert d.history == [WAIT, OK]
    assert _check_step(d) == ("ok", "ok")
    assert _boxes(d) == {}
    bw = d.bal_window
    core.coins.assert_called_once_with(bw.wallet, "label", DATE)
    core.amounts.assert_called_once_with(
        bw.heirs, bw.willexecutors, core.coins.return_value, DATE, 500,
        max_fee=MAX_FEE,
    )
    core.describe.assert_not_called()
    core.invalidate.assert_not_called()
    bw.check_alive_info.assert_not_called()


def test_stopping_returns_none(core):
    """A dialog already closing writes nothing and returns ``None``."""
    d = _dialog()
    d._stopping = True
    assert _run(d) is None
    assert d.labels == [] and d.history == []
    d.bal_window.init_class_variables.assert_not_called()


def test_no_heirs_row(core):
    """No heirs: the amber alert is the row's status; the check stops."""
    d = _dialog(init_error=NoHeirsException("Heirs are not defined"))
    assert _run(d) == ("no_heirs", None)
    assert d.labels == [NO_HEIRS]
    assert d.history == [WAIT, NO_HEIRS]
    assert _check_step(d)[0] == "warn"
    assert _boxes(d) == {"warnings": ["No Heirs"]}
    core.amounts.assert_not_called()


def test_the_heirs_module_no_heirs_is_not_caught(core):
    """Only ``bal.core.will.NoHeirsException`` means "no heirs": the class
    of the same name in ``bal.core.heirs`` goes to ``on_error_phase1``."""
    d = _dialog(init_error=HeirsNoHeirsException("x"))
    with pytest.raises(HeirsNoHeirsException):
        _run(d)
    assert d.history == [WAIT]


def test_second_pass_appends_a_new_row(core):
    """Each pass (after an invalidation, after Retry) writes its own row."""
    d = _dialog(init_error=NoHeirsException("Heirs are not defined"))
    _run(d)
    _run(d)
    assert d.labels == [NO_HEIRS, NO_HEIRS]
    assert d.history == [WAIT, NO_HEIRS, WAIT, NO_HEIRS]


def test_other_errors_reach_on_error_phase1(core):
    """Any other error leaves the row on "Wait" and propagates."""
    d = _dialog(init_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        _run(d)
    assert d.labels == [WAIT]
    assert d.history == [WAIT]


# ------------------------------------------------------------ Check Alive


def test_after_delivery_row(core):
    """A Check Alive on or after the delivery: plain (untranslated) row in
    red, the check stops, the Check Alive information is not read."""
    d = _dialog(init_error=CheckAliveAfterDeliveryError(DATE, DATE + DAY))
    d._check_alive_info = "old"
    assert _run(d) == ("check_alive_before_delivery", None)
    assert d.labels == [AFTER_DELIVERY]
    assert d.history == [WAIT, AFTER_DELIVERY]
    assert _check_step(d)[0] == "err"
    assert _boxes(d) == {"problems": [AFTER_DELIVERY_TEXT]}
    d.bal_window.check_alive_info.assert_not_called()
    assert d._check_alive_info == "old"


def test_after_delivery_row_stays_english():
    """With a translated GUI the after-delivery row keeps its English label
    and status (they were never passed to ``_()``), so the step bar does not
    recognise it: the Check step stays running and the whole row goes to
    the problems box. Kept as it is (an owner decision to change)."""
    with patch.object(bal.i18n, "_electrum_gettext", lambda m: "T:" + m):
        d = _dialog(init_error=CheckAliveAfterDeliveryError(DATE, DATE + DAY))
        with patch.object(Util, "get_available_utxos", return_value=[]):
            assert _run(d) == ("check_alive_before_delivery", None)
        assert d.history == ["T:Checking variables:\t<b>T:Wait</b>",
                             AFTER_DELIVERY]
        assert d.labels == [AFTER_DELIVERY]
        assert _check_step(d)[0] == "run"
        assert _boxes(d) == {"problems": [
            "Checking variables: " + AFTER_DELIVERY_TEXT]}


def test_texts_are_translated():
    """The other rows are translated when they are written."""
    with patch.object(bal.i18n, "_electrum_gettext", lambda m: "T:" + m):
        d = _dialog(init_error=NoHeirsException("Heirs are not defined"))
        assert _run(d) == ("no_heirs", None)
        assert d.history == [
            "T:Checking variables:\t<b>T:Wait</b>",
            "T:Checking variables:\t<b><font color='#cfa808'>&#9888;</font> "
            "<b>T:No Heirs</b></b>",
        ]


def test_invalidation_pending_keeps_wait(core):
    """The renewal invalidation was sent on the previous pass and the
    wallet has not seen it yet: never invalidate twice; the row stays on
    "Wait" and nothing else is read or written."""
    d = _dialog(init_error=CheckAliveError(DATE))
    d._invalidation_broadcast = True
    assert _run(d) == ("invalidation_pending", None)
    assert d.labels == [WAIT]
    assert d.history == [WAIT]
    assert _check_step(d)[0] == "run"
    d.bal_window.check_alive_info.assert_not_called()
    core.invalidate.assert_not_called()
    assert d._check_alive_info is None


def test_expired_row(core):
    """The will in force is past its delivery date: classic invalidation."""
    info = {"reason": "expired", "renewable": False, "has_will": True}
    d = _dialog(info=info, init_error=CheckAliveError(DATE))
    assert _run(d) == ("invalidate_classic", None)
    assert d.labels == [EXPIRED]
    assert d.history == [WAIT, EXPIRED]
    assert _check_step(d)[0] == "err"
    assert _boxes(d) == {"problems": ["Expired"]}
    assert d._check_alive_info is info
    core.invalidate.assert_not_called()


def test_renewal_returns_the_tx_and_keeps_the_info(core):
    """Renewable: the unsigned invalidation is built and returned, the
    information kept for the Renew question."""
    info = _renewal("period")
    assert info["renewable"] and info["has_will"]
    core.invalidate.return_value = TX
    d = _dialog(info=info, init_error=CheckAliveError(DATE))
    result = _run(d)
    assert result == ("check_alive_renew", TX)
    assert result[1] is TX
    assert d.labels == [PASSED]
    assert d.history == [WAIT, PASSED]
    assert _check_step(d)[0] == "warn"
    assert _boxes(d) == {"warnings": ["Check Alive passed"]}
    assert d._check_alive_info is info
    bw = d.bal_window
    core.invalidate.assert_called_once_with(
        bw.willitems, bw.wallet, 3, history_label="label", will_locktime=DATE,
    )


def test_renewal_fee_defaults_to_one(core):
    """No transaction fee stored: the renewal invalidation pays 1 sat/vB."""
    core.invalidate.return_value = TX
    d = _dialog(info=_renewal("period"), init_error=CheckAliveError(DATE))
    del d.bal_window.will_settings["baltx_fees"]
    _run(d)
    assert core.invalidate.call_args.args[2] == 1


def gui_message(info):
    """The Check Alive text as the GUI writes it: the dates in the one long
    style of #134 (``bal.core.datetext.utc_text``)."""
    return check_alive_message(info, datetext.utc_text)


def test_renewal_without_tx_shows_the_question_in_red(core):
    """Renewable but no transaction could be built: the check stops and the
    renewal question is shown as an error (today's text; the dates in the
    one long style since #134)."""
    info = _renewal("period")
    d = _dialog(info=info, init_error=CheckAliveError(DATE))
    assert _run(d) == ("check_alive_stop", None)
    row = _error(gui_message(info))
    assert d.labels == [STOPPED, row]
    assert d.history == [WAIT, STOPPED, row]
    assert _check_step(d)[0] == "err"
    assert _boxes(d) == {"problems": [gui_message(info)]}


def test_not_renewable_row(core):
    """A fixed delivery date cannot be renewed in one click: the reason."""
    info = _renewal(BUILT)
    assert not info["renewable"] and info["reason"] == "fixed_date"
    d = _dialog(info=info, init_error=CheckAliveError(DATE))
    assert _run(d) == ("check_alive_stop", None)
    assert d.labels == [STOPPED, _error(gui_message(info))]
    core.invalidate.assert_not_called()
    assert d._check_alive_info is info


def test_check_alive_without_info_row(core):
    """No Check Alive information: the error's own text, in red."""
    error = CheckAliveError(DATE)
    d = _dialog(info=None, init_error=error)
    d._check_alive_info = "old"
    assert _run(d) == ("check_alive_stop", None)
    assert d.labels == [STOPPED, _error(str(error))]
    assert d.history == [WAIT, STOPPED, _error(str(error))]
    assert d._check_alive_info is None


def test_invalidation_error_keeps_the_info(core):
    """An error while building the renewal invalidation propagates, with
    the information already kept and the row on "Wait"."""
    info = _renewal("period")
    core.invalidate.side_effect = RuntimeError("no coins")
    d = _dialog(info=info, init_error=CheckAliveError(DATE))
    with pytest.raises(RuntimeError, match="no coins"):
        _run(d)
    assert d.history == [WAIT]
    assert d._check_alive_info is info


# ------------------------------------------------------------ amounts


FIXED_INFO = {"kind": "fixed_exceeds", "fixed": 2_000_000,
              "balance": 990_000, "percent_heirs": ["bob"]}
PERCENT_INFO = {"kind": "percent", "total": 80.0, "with_fixed": False,
                "shares": [("alice", 62.5), ("bob", 37.5)]}


def test_fixed_amounts_exceed_stops(core):
    """Fixed amounts above the balance: "Stopped" and the explanation in
    red; the check stops. ``describe_amounts`` gets no maximum fee."""
    core.amounts.side_effect = FixedAmountException("x")
    core.describe.return_value = FIXED_INFO
    d = _dialog()
    assert _run(d) == ("fixed_amounts_exceed", None)
    text = amounts_message(FIXED_INFO)
    assert d.labels == [STOPPED, _error(text)]
    assert d.history == [WAIT, STOPPED, _error(text)]
    assert _check_step(d)[0] == "err"
    assert _boxes(d) == {"problems": [text]}
    bw = d.bal_window
    core.describe.assert_called_once_with(
        bw.heirs, bw.willexecutors, core.coins.return_value, DATE, 500)


def test_percentages_are_explained_and_the_check_goes_on(core):
    """Percentages not adding up to 100: "Ok" and the amber explanation."""
    core.amounts.side_effect = PercAmountException("x")
    core.describe.return_value = PERCENT_INFO
    d = _dialog()
    assert _run(d) == GOES_ON
    text = amounts_message(PERCENT_INFO)
    assert "add up to 80%" in text
    assert d.labels == [OK, _alert(text)]
    assert d.history == [WAIT, OK, _alert(text)]
    assert _check_step(d) == ("ok", "ok")
    assert _boxes(d) == {"warnings": [text]}


@pytest.mark.parametrize("coins, locktime", [
    ([], FUTURE),                      # no coin visible right now
    ([_utxo(1_000_000)], PAST),        # the delivery date has passed
], ids=["no_coin", "date_passed"])
def test_false_alarm_is_silent(core, coins, locktime):
    """Not an amount problem (BUG-5): "Ok" and nothing else."""
    core.coins.return_value = coins
    core.amounts.side_effect = PercAmountException("x")
    d = _dialog()
    d.bal_window.will_settings["locktime"] = locktime
    assert _run(d) == GOES_ON
    assert d.labels == [OK]
    assert d.history == [WAIT, OK]


def test_amount_problem_without_explanation(core):
    """A real amount problem with no explanation: the generic warning."""
    core.amounts.side_effect = PercAmountException("x")
    d = _dialog()
    assert _run(d) == GOES_ON
    assert d.labels == [OK, _alert(ADJUST)]
    assert d.history == [WAIT, OK, _alert(ADJUST)]
    assert _boxes(d) == {"warnings": [ADJUST]}


def test_coin_error_leaves_an_empty_list(core):
    """An amount error while reading the coins: the explanation is computed
    on no coin, which makes it a false alarm."""
    core.coins.side_effect = PercAmountException("x")
    d = _dialog()
    assert _run(d) == GOES_ON
    assert core.describe.call_args.args[2] == []
    core.amounts.assert_not_called()
    assert d.labels == [OK]


def test_fee_too_high_rows(core):
    """A will-executor fee above the maximum: "Ok" and an amber row with
    the exception's text; the check goes on."""
    executors = _executors()
    core.amounts.side_effect = WillExecutorFeeTooHighException(
        executors[WE[1]], MAX_FEE)
    d = _dialog()
    assert _run(d) == GOES_ON
    row = _alert("Will-executor fee too high: WillExecutorFeeTooHighException: "
                 "https://we1.test fee:50000 > max:5000")
    assert d.labels == [OK, row]
    assert d.history == [WAIT, OK, row]
    assert _check_step(d) == ("ok", "ok")


# ------------------------------------------------------------ real flow


@pytest.fixture
def flow_session(monkeypatch):
    """One ``tests/flowkit.py`` session with Electrum's asyncio loop."""
    fk.ensure_event_loop()
    made = []

    def make(**kw):
        """Start one flowkit session and remember it for closing."""
        s = fk.Session(monkeypatch, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()
    fk.stop_event_loop()


def test_passed_check_alive_after_the_update_invalidates_nothing(flow_session):
    """Real flow on the seed of the old renewal test (CHANGELOG #123).

    Before #123 this test (``test_renewal_sent_but_not_seen_yet``) renewed
    a will built from the shared period "60d": the renewal invalidation was
    broadcast, the wallet did not see it yet, and the second pass of phase
    1 stopped at once ("invalidation_pending"). Since #123 that old period
    becomes the wallet's fixed date when the window opens, so the one-click
    renewal is not offered until #126 (plan, risk R1-3) and that flow
    cannot be reached; ``test_invalidation_pending_keeps_wait`` keeps its
    rows covered.

    Now: the Check Alive passes and the Check stops on the reason (the date
    is fixed). Nothing is asked or invalidated, also by the next Check
    while the wallet still sees no new transaction.
    """
    s = flow_session(mode="advanced", locktime="60d", threshold="-30d")
    settings = s.bw.will_settings
    assert settings is s.wallet.db.get_dict("will_settings")
    assert settings["migrated_from"] == "global"
    assert settings["legacy_locktime"] == "60d"
    assert settings["locktime"] == fk.ts(2026, 12, 9)   # 60 days, now fixed
    s.check()
    s.clock.travel(40)
    s.world.wallet_lags = True
    reason = (
        "The Check Alive date (9 November 2026, 00:00 UTC) has passed. Your "
        "delivery is a "
        "fixed date: choose a later delivery date to renew your will. Until "
        "then your current will stays valid."
    )
    for label in ("first Check", "next Check"):
        s.check()
        step = s.recorder.steps[-1]
        assert step["rows"] == [STOPPED, _error(reason)], label
        assert step["history"] == [WAIT, STOPPED, _error(reason)], label
        report = step["report"]
        assert report["steps"][0] == ["check", "err", "error"], label
        assert report["boxes"] == {"changes": [], "warnings": [],
                                   "problems": [reason]}, label
        assert step["broadcasts"] == [], label
        assert step["questions"] == [], label
        assert step["popups"] == [], label
        assert step["dialog_events"] == ["close-button"], label
    assert settings["locktime"] == fk.ts(2026, 12, 9)


def test_renewal_sent_but_not_seen_yet(flow_session):
    """Real flow, back with the period form (CHANGELOG #127): the Check
    Alive passes, Renew; the renewal invalidation is broadcast but the
    wallet does not see it yet. The second pass of phase 1 stops at once
    ("invalidation_pending"): its "Checking variables" row stays on "Wait"
    and nothing is invalidated twice.

    The test of the same name before #123 (seed: the shared period "60d");
    from #123 to #126 no period was left and the renewal could not be
    reached. Now 2 calendar months, three will-executors (one invalidation
    covers the one coin of all three copies).
    """
    s = flow_session(mode="advanced", period_months=2, threshold="-30d")
    s.world.add_executor()
    assert s.bw.will_settings["form"] == "period"
    s.check()
    s.clock.travel(40)
    s.world.wallet_lags = True
    s.check()
    step = s.recorder.steps[-1]
    assert step["rows"] == [
        PASSED,
        "Invalidating old will:\t<b><font color='#05ad05'><b>Ok</b></font></b>",
        WAIT,
    ]
    assert step["history"] == [WAIT, PASSED,
                               "Invalidating old will:\t<b>Wait</b>",
                               "Invalidating old will:\t<b>Broadcasting</b>",
                               "Invalidating old will:\t<b><font color='#05ad05'>"
                               "<b>Ok</b></font></b>"] + [
        "Please wait {}secs".format(n) for n in range(10, 0, -1)] + [
        WAIT,
        "Invalidating old will:\t<b><font color='#05ad05'><b>Ok</b></font></b>",
    ]
    report = step["report"]
    assert report["steps"][0] == ["check", "run", ""]
    assert report["boxes"] == {"changes": [], "warnings": ["Check Alive passed"],
                               "problems": []}
    assert step["broadcasts"] == [["invalidation(dialog)", "T4"]]
    assert [kind for kind, _text in step["questions"]] == ["renew"]
    assert "today + 2 months" in step["questions"][0][1]
    assert step["popups"] == [["message", (
        "Your old will has been invalidated and the transaction was "
        "broadcast.\nElectrum may need a little time to register it.\n"
        "Please wait until the invalidation transaction is confirmed, then "
        "press the Check button again to finish updating your will.")]]
    assert step["dialog_events"] == ["close-button"]
    # The wallet has not seen the invalidation: the old will is still in
    # force for the period policy, so the date is not moved yet.
    assert step["settings"]["locktime"] == "2026-12-10"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
