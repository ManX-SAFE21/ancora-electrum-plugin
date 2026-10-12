"""The Check's first part in the engine: variables, Check Alive, amounts (#120).

``bal.engine.check.check_variables`` is the start of the Check's phase 1,
ported line by line from ``BalBuildWillDialog.task_phase1``. It reports
through a FlowUI (``bal.engine.ui``) with codes and data, never texts; the
dialog's rows are checked in ``tests/test_gui_flow_adapter.py`` and
``tests/test_gui_check_variables_rows.py``.

Here it runs on a small fake host with the real plugin settings and a
``RecordingUI``: each branch is pinned by the events it sends, the result
it returns, the run state it writes and what it reads or calls on the host
and the core. Three will-executors with their own fee addresses (owner
rule, 2026-10-10); two tests use the real ``Heirs`` and amount checks.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_engine_check_variables.py -q
"""

import dataclasses
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum import constants
from electrum.segwit_addr import encode_segwit_address
from test_cli_controller_offline import Plugin
from test_engine_context import Host

from bal.core.checkalive import (
    CheckAliveAfterDeliveryError,
    CheckAliveError,
    check_alive_renewal,
)
from bal.core.heirs import Heirs, WillExecutorFeeTooHighException
from bal.core.heirs import NoHeirsException as HeirsNoHeirsException
from bal.core.messages import amounts_message
from bal.core.will import (
    FixedAmountException,
    NoHeirsException,
    PercAmountException,
    Will,
)
from bal.engine import check
from bal.engine.ui import CheckRun, NullUI, RecordingUI, Step

WE = ["https://we0.test", "https://we1.test", "https://we2.test"]
MAX_FEE = 5000
DAY = 86400
DATE = 1_900_000_000          # date_to_check (2030)
PAST = 1_577_836_800          # 2020-01-01
FUTURE = 2_011_219_200        # 2033-09-25
TX = object()                 # the renewal invalidation (opaque here)
V = Step.VARIABLES
RUN = ("step", V, "run", {})
OK = ("step", V, "ok", {})
STOPPED = ("step", V, "stopped", {})

# Two heirs' addresses for the real Heirs (any network: never validated).
A1 = "bcrt1q087zm5m3jrhfg78zflqefhcr9heh4c98kzmvhp"
A2 = "bcrt1q08z5t4x74u2883sx2qwsmzk2hj8e5n7z83e4vy"


def _addr(n):
    """A valid P2WPKH address of the CURRENT network, number ``n``.

    Built at call time: ``tests/flowkit.py`` switches ``constants.net`` to
    testnet for the rest of the run, and ``Willexecutors.is_valid`` checks
    the address against the current network.
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


def _settings_of(locktime):
    """The stored settings of :func:`_renewal`: ``"period"`` is the period
    form of 7 calendar months (CHANGELOG #127: only the period form renews;
    before it was the relative "200d"), any other value a fixed date."""
    if locktime == "period":
        now = 1_800_000_000 - 1_800_000_000 % DAY
        return {"locktime": now + 20 * DAY, "threshold": "-180d",
                "form": "period", "period_months": 7}
    return {"locktime": locktime, "threshold": "-180d"}


def _renewal(locktime):
    """A passed Check Alive as ``check_alive_renewal`` describes it: a will
    in force delivered in 20 days, its Check Alive 180 days before.
    ``locktime`` "period" (the period form, :func:`_settings_of`) can be
    renewed; the fixed date of the will in force cannot."""
    now = 1_800_000_000 - 1_800_000_000 % DAY
    return check_alive_renewal(
        False, _settings_of(locktime),
        built_locktime=now + 20 * DAY, creation_date=now - 300 * DAY, now=now,
    )


def _real_heirs(host, alice, bob):
    """The real ``Heirs`` with two heirs (no wallet: only the amounts) on
    ``host``, delivered in 30 days; the host's ``date_to_check`` becomes
    now, so both heirs count (an heir dated before it is left out)."""
    h = Heirs.__new__(Heirs)
    h.update({"alice": [A1, alice, "30d"], "bob": [A2, bob, "30d"]})
    host.heirs = h
    host.date_to_check = int(time.time())


class CheckHost(Host):
    """The fake host of ``test_engine_context`` plus the two methods
    ``check_variables`` calls on it (``BalWindow`` and ``BalController``
    both have them)."""

    def __init__(self, plugin, *, init_error=None, info=None):
        """Three will-executors, ``date_to_check`` = ``DATE``, delivery in
        the future, fee 3 sat/vB; ``init_class_variables`` raises
        ``init_error`` (when set) and ``check_alive_info`` returns
        ``info``."""
        super().__init__(plugin)           # FakeWallet: dust 500
        self.init_error = init_error
        self.info = info
        self.heirs = MagicMock()
        self.willexecutors = _executors()
        self.date_to_check = DATE
        self.will_settings["locktime"] = FUTURE
        self.will_settings["baltx_fees"] = 3

    def init_class_variables(self):
        """Record the call; raise ``init_error`` when set."""
        self.calls.append(("init_class_variables",))
        if self.init_error is not None:
            raise self.init_error

    def check_alive_info(self):
        """Record the call; return ``info``."""
        self.calls.append(("check_alive_info",))
        return self.info


@pytest.fixture
def plugin():
    """The real command-line plugin with a known maximum executor fee."""
    with Plugin() as p:
        p.MAX_WILLEXECUTOR_FEE.set(MAX_FEE)
        yield p


@pytest.fixture
def core():
    """The coins and the core amount / invalidation calls, replaced: one
    coin of 1,000,000 sat, the amounts fine, no explanation, no tx."""
    with patch.object(check.context, "available_utxos",
                      return_value=[_utxo(1_000_000)]) as coins, \
         patch.object(Will, "check_amounts") as amounts, \
         patch.object(Will, "describe_amounts", return_value=None) as describe, \
         patch.object(Will, "invalidate_will", return_value=None) as invalidate:
        yield SimpleNamespace(coins=coins, amounts=amounts, describe=describe,
                              invalidate=invalidate)


def _run(host, run=None, ui=None):
    """Run ``check_variables``; return ``(result, events)``.

    Every event must be one the engine declares (``VARIABLES_STATES`` /
    ``VARIABLES_NOTES``), so the dialog's adapter knows each of them.
    """
    run = CheckRun() if run is None else run
    ui = RecordingUI() if ui is None else ui
    result = check.check_variables(host, run, ui)
    for event in ui.events:
        if event[0] == "step":
            assert event[1] == V and event[2] in check.VARIABLES_STATES
        else:
            assert event[1] in check.VARIABLES_NOTES
    return result, ui.events


# ------------------------------------------------------------ the gate


def test_ok_reads_the_host_and_goes_on(plugin, core):
    """Nothing to say: "ok" and ``None`` (phase 1 goes on). The coins and
    the amounts are read from the host as the dialog did."""
    host = CheckHost(plugin)
    result, events = _run(host)
    assert result is None
    assert events == [RUN, OK]
    core.coins.assert_called_once_with(host)
    core.amounts.assert_called_once_with(
        host.heirs, host.willexecutors, core.coins.return_value, DATE, 500,
        max_fee=MAX_FEE,
    )
    core.describe.assert_not_called()
    core.invalidate.assert_not_called()
    assert host.calls == [("init_class_variables",)]


def test_the_row_starts_before_init(plugin, core):
    """The "Checking variables" row is written before the variables are
    prepared (so it shows "Wait" while they are)."""
    host = CheckHost(plugin)
    ui = RecordingUI()
    seen = []
    original = host.init_class_variables

    def init():
        """Note how many events came first, then prepare the variables."""
        seen.append(len(ui.events))
        original()

    host.init_class_variables = init
    _run(host, ui=ui)
    assert seen == [1]


def test_no_heirs_stops(plugin, core):
    """No heirs: "no_heirs" and the stop."""
    host = CheckHost(plugin, init_error=NoHeirsException("x"))
    result, events = _run(host)
    assert result == check.Phase1Result("no_heirs")
    assert result.legacy() == ("no_heirs", None)
    assert events == [RUN, ("step", V, "no_heirs", {})]
    core.amounts.assert_not_called()


def test_the_heirs_module_no_heirs_is_not_caught(plugin, core):
    """Only ``bal.core.will.NoHeirsException`` (raised by
    ``init_class_variables``) means "no heirs": the class with the same name
    in ``bal.core.heirs`` propagates, as before."""
    host = CheckHost(plugin, init_error=HeirsNoHeirsException("x"))
    ui = RecordingUI()
    with pytest.raises(HeirsNoHeirsException):
        _run(host, ui=ui)
    assert ui.events == [RUN]


def test_other_errors_propagate(plugin, core):
    """Any other error propagates; the row stays on "run"."""
    host = CheckHost(plugin, init_error=RuntimeError("boom"))
    ui = RecordingUI()
    with pytest.raises(RuntimeError, match="boom"):
        _run(host, ui=ui)
    assert ui.events == [RUN]
    core.amounts.side_effect = KeyError("amounts")
    ui = RecordingUI()
    with pytest.raises(KeyError):
        _run(CheckHost(plugin), ui=ui)
    assert ui.events == [RUN]


# ------------------------------------------------------------ Check Alive


def test_check_alive_after_delivery_stops(plugin, core):
    """A Check Alive on or after the delivery: stop, no information read."""
    host = CheckHost(plugin, init_error=CheckAliveAfterDeliveryError(DATE, DATE + DAY))
    run = CheckRun(check_alive_info="old")
    result, events = _run(host, run)
    assert result.legacy() == ("check_alive_before_delivery", None)
    assert events == [RUN, ("step", V, "check_alive_before_delivery", {})]
    assert ("check_alive_info",) not in host.calls
    assert run.check_alive_info == "old"


def test_invalidation_pending_writes_no_outcome(plugin, core):
    """The renewal invalidation was sent on the previous pass: never
    invalidate twice. No outcome event, nothing read or written."""
    host = CheckHost(plugin, init_error=CheckAliveError(DATE))
    run = CheckRun(invalidation_broadcast=True, check_alive_info="old")
    result, events = _run(host, run)
    assert result.legacy() == ("invalidation_pending", None)
    assert events == [RUN]
    assert ("check_alive_info",) not in host.calls
    core.invalidate.assert_not_called()
    assert run.check_alive_info == "old"


def test_expired_will_goes_to_the_classic_invalidation(plugin, core):
    """The will in force is past its delivery date: "expired", the classic
    invalidation; the information is kept."""
    info = {"reason": "expired", "renewable": False, "has_will": True}
    host = CheckHost(plugin, init_error=CheckAliveError(DATE), info=info)
    run = CheckRun()
    result, events = _run(host, run)
    assert result.legacy() == ("invalidate_classic", None)
    assert events == [RUN, ("step", V, "expired", {})]
    assert run.check_alive_info is info
    core.invalidate.assert_not_called()


class _Item:
    """A will item with what ``Will.get_min_locktime`` reads."""

    def __init__(self, locktime, valid=True):
        """A transaction dated ``locktime``; VALID unless ``valid`` is False."""
        self.tx = SimpleNamespace(locktime=locktime)
        self.valid = valid

    def get_status(self, status):
        """Only the VALID status is asked."""
        return self.valid if status == "VALID" else False


def test_renewal_builds_the_invalidation(plugin, core):
    """Renewable: the unsigned invalidation is built from the host's will,
    wallet, fee and History label and dated by the earliest date of the
    VALID will items (not by ``date_to_check``)."""
    info = _renewal("period")
    assert info["renewable"] and info["has_will"]
    core.invalidate.return_value = TX
    host = CheckHost(plugin, init_error=CheckAliveError(DATE), info=info)
    host.willitems = {"t1": _Item(DATE - 5 * DAY), "t2": _Item(DATE - 2 * DAY),
                      "t3": _Item(DATE - 9 * DAY, valid=False)}
    run = CheckRun()
    result, events = _run(host, run)
    assert result == check.Phase1Result("check_alive_renew", tx=TX)
    assert result.legacy()[1] is TX
    assert events == [RUN, ("step", V, "check_alive_passed", {})]
    assert run.check_alive_info is info
    core.invalidate.assert_called_once_with(
        host.willitems, host.wallet, 3,
        history_label=plugin.HISTORY_LABEL.get(),
        will_locktime=DATE - 5 * DAY,
    )


def test_renewal_without_items_is_dated_by_date_to_check(plugin, core):
    """No VALID will item: the invalidation is dated by ``date_to_check``."""
    core.invalidate.return_value = TX
    host = CheckHost(plugin, init_error=CheckAliveError(DATE),
                     info=_renewal("period"))
    _run(host)
    assert core.invalidate.call_args.kwargs["will_locktime"] == DATE


def test_renewable_without_a_will_builds_nothing(plugin, core):
    """Renewable but no will in force (``has_will`` False): no invalidation
    is built; "stopped" and the reason."""
    info = dict(_renewal("period"), has_will=False)
    host = CheckHost(plugin, init_error=CheckAliveError(DATE), info=info)
    result, events = _run(host)
    assert result.legacy() == ("check_alive_stop", None)
    assert events == [RUN, STOPPED, ("note", "check_alive_reason", {"info": info})]
    core.invalidate.assert_not_called()


def test_renewal_fee_defaults_to_one(plugin, core):
    """No transaction fee stored: the renewal invalidation pays 1 sat/vB."""
    core.invalidate.return_value = TX
    host = CheckHost(plugin, init_error=CheckAliveError(DATE),
                     info=_renewal("period"))
    del host.will_settings["baltx_fees"]
    _run(host)
    assert core.invalidate.call_args.args[2] == 1


def test_renewal_without_a_transaction_stops_with_the_reason(plugin, core):
    """Renewable but no transaction: "stopped" and the information."""
    info = _renewal("period")
    host = CheckHost(plugin, init_error=CheckAliveError(DATE), info=info)
    result, events = _run(host)
    assert result.legacy() == ("check_alive_stop", None)
    assert events == [RUN, STOPPED, ("note", "check_alive_reason", {"info": info})]


def test_not_renewable_stops_with_the_reason(plugin, core):
    """A fixed delivery date: no invalidation is built; the reason."""
    now = 1_800_000_000 - 1_800_000_000 % DAY
    info = _renewal(now + 20 * DAY)
    assert not info["renewable"] and info["reason"] == "fixed_date"
    host = CheckHost(plugin, init_error=CheckAliveError(DATE), info=info)
    result, events = _run(host)
    assert result.legacy() == ("check_alive_stop", None)
    assert events == [RUN, STOPPED, ("note", "check_alive_reason", {"info": info})]
    core.invalidate.assert_not_called()


def test_check_alive_without_info_shows_the_error(plugin, core):
    """No information: the error's own text; the old information goes."""
    error = CheckAliveError(DATE)
    host = CheckHost(plugin, init_error=error, info=None)
    run = CheckRun(check_alive_info="old")
    result, events = _run(host, run)
    assert result.legacy() == ("check_alive_stop", None)
    assert events == [RUN, STOPPED,
                      ("note", "check_alive_error", {"error": str(error)})]
    assert run.check_alive_info is None


def test_invalidation_error_propagates_with_the_info_written(plugin, core):
    """An error while building the renewal propagates, with the information
    already in the run state (the dialog's error path sees it)."""
    info = _renewal("period")
    core.invalidate.side_effect = RuntimeError("no coins")
    host = CheckHost(plugin, init_error=CheckAliveError(DATE), info=info)
    run = CheckRun()
    ui = RecordingUI()
    with pytest.raises(RuntimeError, match="no coins"):
        _run(host, run, ui)
    assert run.check_alive_info is info
    assert ui.events == [RUN]


# ------------------------------------------------------------ amounts


FIXED_INFO = {"kind": "fixed_exceeds", "fixed": 2_000_000,
              "balance": 990_000, "percent_heirs": ["bob"]}
PERCENT_INFO = {"kind": "percent", "total": 80.0, "with_fixed": False,
                "shares": [("alice", 62.5), ("bob", 37.5)]}


def test_fixed_amounts_exceed_stops(plugin, core):
    """Fixed amounts above the balance: "stopped" and the explanation; the
    explanation asks for no maximum fee (it counts every selected
    will-executor)."""
    core.amounts.side_effect = FixedAmountException("x")
    core.describe.return_value = FIXED_INFO
    host = CheckHost(plugin)
    result, events = _run(host)
    assert result.legacy() == ("fixed_amounts_exceed", None)
    assert events == [RUN, STOPPED, ("note", "fixed_amounts_exceed", {
        "text": amounts_message(FIXED_INFO), "info": FIXED_INFO})]
    core.describe.assert_called_once_with(
        host.heirs, host.willexecutors, core.coins.return_value, DATE, 500)


def test_percentages_are_explained_and_the_check_goes_on(plugin, core):
    """Percentages not adding up to 100: "ok", the explanation, go on."""
    core.amounts.side_effect = PercAmountException("x")
    core.describe.return_value = PERCENT_INFO
    result, events = _run(CheckHost(plugin))
    assert result is None
    text = amounts_message(PERCENT_INFO)
    assert "add up to 80%" in text
    assert events == [RUN, OK, ("note", "amounts_explained",
                                {"text": text, "info": PERCENT_INFO})]


@pytest.mark.parametrize("coins, locktime", [
    ([], FUTURE),                      # no coin visible right now
    ([_utxo(1_000_000)], PAST),        # the delivery date has passed
], ids=["no_coin", "date_passed"])
def test_false_alarm_is_silent(plugin, core, coins, locktime):
    """Not an amount problem (BUG-5): "ok" and nothing else."""
    core.coins.return_value = coins
    core.amounts.side_effect = PercAmountException("x")
    host = CheckHost(plugin)
    host.will_settings["locktime"] = locktime
    result, events = _run(host)
    assert result is None
    assert events == [RUN, OK]


def test_explanation_wins_over_a_false_alarm(plugin, core):
    """An explanation is shown even when the warning is a false alarm (no
    coin visible right now, CHANGELOG #82): it depends on the settings
    only."""
    core.coins.return_value = []
    core.amounts.side_effect = PercAmountException("x")
    core.describe.return_value = PERCENT_INFO
    result, events = _run(CheckHost(plugin))
    assert result is None
    assert core.describe.call_args.args[2] == []
    assert events == [RUN, OK, ("note", "amounts_explained", {
        "text": amounts_message(PERCENT_INFO), "info": PERCENT_INFO})]


def test_amount_problem_without_explanation(plugin, core):
    """A real amount problem with no explanation: the generic note."""
    core.amounts.side_effect = PercAmountException("x")
    result, events = _run(CheckHost(plugin))
    assert result is None
    assert events == [RUN, OK, ("note", "amounts_adjust", {})]


def test_text_and_false_alarm_come_before_the_outcome_row(plugin, core):
    """The sentence and the false-alarm test are computed before the row
    is rewritten, as before (both see only the "run" event)."""
    core.amounts.side_effect = FixedAmountException("x")
    core.describe.return_value = FIXED_INFO
    ui = RecordingUI()
    seen = []
    with patch.object(check, "amounts_message",
                      side_effect=lambda info: seen.append(("text", len(ui.events))) or "T"), \
         patch.object(check, "amount_warning_is_false_alarm",
                      side_effect=lambda h, u: seen.append(("alarm", len(ui.events))) or False):
        _run(CheckHost(plugin), ui=ui)
    assert seen == [("text", 1), ("alarm", 1)]


def test_coin_error_leaves_an_empty_list(plugin, core):
    """An amount error while reading the coins: the explanation is computed
    on no coin, which makes it a false alarm."""
    core.coins.side_effect = PercAmountException("x")
    result, events = _run(CheckHost(plugin))
    assert result is None
    assert core.describe.call_args.args[2] == []
    core.amounts.assert_not_called()
    assert events == [RUN, OK]


def test_executor_fee_too_high(plugin, core):
    """A will-executor fee above the maximum: "ok" and a note with the
    exception's text; phase 1 goes on."""
    core.amounts.side_effect = WillExecutorFeeTooHighException(
        _executors()[WE[1]], MAX_FEE)
    result, events = _run(CheckHost(plugin))
    assert result is None
    assert events == [RUN, OK, ("note", "executor_fee_too_high", {
        "error": "WillExecutorFeeTooHighException: https://we1.test "
                 "fee:50000 > max:5000"})]


def test_real_core_percentages_80(plugin):
    """Real heirs at 50% + 30%, real amount checks: the explanation the
    owner sees (golden ``percentages_not_100``), and phase 1 goes on."""
    host = CheckHost(plugin)
    _real_heirs(host, "50%", "30%")
    with patch.object(check.context, "available_utxos",
                      return_value=[_utxo(1_000_000)]):
        result, events = _run(host)
    assert result is None
    assert events[:2] == [RUN, OK]
    assert events[2][:2] == ("note", "amounts_explained")
    assert events[2][2]["text"] == (
        "The heirs' percentages add up to 80%: the will always empties the "
        "whole wallet, so the shares are recalculated in proportion "
        "(alice 62.5%, bob 37.5%).")


def test_real_core_fixed_exceeds_counts_every_selected_executor(plugin):
    """Real heirs: alice 999,500 sat fixed, bob 100%; one coin of 1,000,000
    sat. The check fails on we0 (fee 1000); the explanation leaves out the
    highest fee of the selected will-executors, we1's 50,000 sat, although
    it is above the maximum fee."""
    host = CheckHost(plugin)
    _real_heirs(host, 999_500, "100%")
    with patch.object(check.context, "available_utxos",
                      return_value=[_utxo(1_000_000)]):
        result, events = _run(host)
    assert result.legacy() == ("fixed_amounts_exceed", None)
    assert events[:2] == [RUN, STOPPED]
    note = events[2]
    assert note[:2] == ("note", "fixed_amounts_exceed")
    assert note[2]["info"] == {"kind": "fixed_exceeds", "fixed": 999_500,
                               "balance": 950_000, "percent_heirs": ["bob"]}
    assert note[2]["text"] == amounts_message(note[2]["info"])


@pytest.mark.parametrize("make", [
    lambda p: CheckHost(p, init_error=NoHeirsException("x")),
    lambda p: CheckHost(p, init_error=CheckAliveError(DATE), info=_renewal("period")),
], ids=["no_heirs", "renew"])
def test_null_ui_gives_the_same_result(plugin, core, make):
    """The flow never depends on the UI: a UI that drops every event gives
    the same result."""
    core.invalidate.return_value = TX
    recorded, _events = _run(make(plugin))
    assert check.check_variables(make(plugin), CheckRun(), NullUI()) == recorded


# ------------------------------------------------------------ helpers


def test_past_date_message_of(plugin):
    """The date message of the host's settings; ``None`` when they cannot
    be read."""
    host = CheckHost(plugin)
    host.will_settings["locktime"] = PAST
    assert "2020-01-01" in check.past_date_message_of(host)
    host.will_settings["locktime"] = FUTURE
    assert check.past_date_message_of(host) is None
    assert check.past_date_message_of(None) is None


def test_amount_warning_is_false_alarm(plugin):
    """No coin or a passed date: a false alarm; otherwise not. An error in
    the date test means "not a false alarm"."""
    assert check.amount_warning_is_false_alarm(None, []) is True
    assert check.amount_warning_is_false_alarm(None, [_utxo(5)]) is False
    host = CheckHost(plugin)
    host.will_settings["locktime"] = PAST
    assert check.amount_warning_is_false_alarm(host, [_utxo(5)]) is True
    host.will_settings["locktime"] = FUTURE
    assert check.amount_warning_is_false_alarm(host, [_utxo(5)]) is False
    with patch.object(check, "past_date_message_of", side_effect=RuntimeError):
        assert check.amount_warning_is_false_alarm(host, []) is False


def test_phase1_result():
    """Frozen; ``legacy()`` is today's ``(kind, tx)`` tuple."""
    result = check.Phase1Result("no_heirs")
    assert result.legacy() == ("no_heirs", None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.kind = "x"


def test_check_run_and_the_uis():
    """The run state starts empty; ``RecordingUI`` keeps the events in
    order, ``NullUI`` drops them."""
    assert CheckRun() == CheckRun(invalidation_broadcast=False,
                                  check_alive_info=None)
    ui = RecordingUI()
    ui.step(V, "run")
    ui.note("amounts_adjust", text="t")
    assert ui.events == [RUN, ("note", "amounts_adjust", {"text": "t"})]
    assert NullUI().step(V, "run") is None
    assert NullUI().note("x") is None
    assert Step.VARIABLES == "variables"
