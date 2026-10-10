"""The Check dialog helpers moved to the core (CHANGELOG #115).

Phase 5e, step 3: the sentences and checks of the "Check your will" dialog
that need no widget now live in ``bal/core`` (``bal.core.messages`` and
``Will``), so a GUI-free engine can use them. They take explicit arguments
instead of reading the dialog. The dialog keeps its old method names as
bridges; the older tests (#75, #76, #77, #82) still run through them.

Here the core functions are called directly, case by case, and the bridges
are checked to pass the dialog's state and to never raise, as before.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_core_messages_moved.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.messages import (
    amounts_message,
    build_failure_message,
    past_date_message,
    rebuild_reason_message,
)
from bal.core.will import (
    HeirNotFoundException,
    NotCompleteWillException,
    Will,
    WillExecutorNotPresent,
)
from bal.gui.qt.dialogs import BalBuildWillDialog

PAST = 1577836800         # 2020-01-01
FUTURE = 2011219200       # 2033-09-25
WE = "https://we.example"


class _Item:
    """A will item with only the statuses and the will-executor."""

    def __init__(self, statuses, url=None):
        self._statuses = set(statuses)
        self.we = {"url": url} if url else None

    def get_status(self, status):
        """True when ``status`` is one of the item's statuses."""
        return status in self._statuses


def _utxo(value):
    """A coin whose ``value_sats()`` is ``value``."""
    u = MagicMock()
    u.value_sats.return_value = value
    return u


# ------------------------------------------------------- rebuild reason (#77)


def test_rebuild_reason_after_this_check_invalidated():
    """Right after this Check invalidated: the old will is gone, the new one comes."""
    plain = NotCompleteWillException()
    assert rebuild_reason_message(plain, True, False, None) == (
        "Old will invalidated: preparing the new will with the time period you set."
    )
    assert rebuild_reason_message(plain, True, True, None) == (
        "Old will invalidated: preparing the new will with the new date."
    )


def test_rebuild_reason_after_a_cancel_at_signing():
    """After a Cancel at signing: say the will was not signed."""
    signed_again = "The will prepared earlier was not signed: preparing it again."
    plain = NotCompleteWillException()
    assert rebuild_reason_message(plain, False, False, {None}) == signed_again
    missing = WillExecutorNotPresent(WE)
    assert rebuild_reason_message(missing, False, False, {WE}) == signed_again
    # An executor that was not in the unsigned will keeps its own message.
    assert rebuild_reason_message(missing, False, False, {"https://other"}) is None


def test_rebuild_reason_other_causes_keep_their_message():
    """Any other cause keeps its own sentence (None here)."""
    heir = HeirNotFoundException("bob", HeirNotFoundException.HEIR_REMOVED)
    assert rebuild_reason_message(heir, True, False, {None}) is None
    covered = NotCompleteWillException("coin not covered")
    assert rebuild_reason_message(covered, True, False, None) is None
    assert rebuild_reason_message(NotCompleteWillException(), False, False, None) is None


# ------------------------------------------------------------ amounts (#76/#82)


def test_amounts_message_each_kind():
    """One sentence per kind returned by Will.describe_amounts."""
    assert amounts_message(None) is None
    assert amounts_message({
        "kind": "fixed_exceeds", "fixed": 2_000_000, "balance": 1_000_000,
        "percent_heirs": ["bob"],
    }) == (
        "The fixed amounts (2,000,000 sat) exceed the balance available for the "
        "heirs (1,000,000 sat): the heirs with a percentage (bob) would receive "
        "nothing. Reduce the fixed amounts."
    )
    assert amounts_message({
        "kind": "fixed_exceeds", "fixed": 2_000_000, "balance": 1_000_000,
        "percent_heirs": [],
    }) == (
        "The fixed amounts (2,000,000 sat) exceed the balance available for the "
        "heirs (1,000,000 sat). Reduce the fixed amounts."
    )
    assert amounts_message({
        "kind": "fixed_scaled", "shares": [("alice", 60.0), ("bob", 40.0)],
    }).endswith("increased in proportion (alice 60%, bob 40%).")
    shares = [("alice", 200 / 3), ("bob", 100 / 3)]
    assert amounts_message({
        "kind": "percent", "total": 90.0, "shares": shares, "with_fixed": True,
    }) == (
        "The heirs' percentages add up to 90%: the will always empties the whole "
        "wallet, so the shares of what remains after the fixed amounts are "
        "recalculated in proportion (alice 66.7%, bob 33.3%)."
    )
    assert amounts_message({
        "kind": "percent", "total": 90.0, "shares": shares, "with_fixed": False,
    }) == (
        "The heirs' percentages add up to 90%: the will always empties the whole "
        "wallet, so the shares are recalculated in proportion "
        "(alice 66.7%, bob 33.3%)."
    )


# ------------------------------------------------------------ past date (#75)


def test_past_date_message():
    """Only a delivery date already passed gets the sentence."""
    assert past_date_message({"locktime": PAST}) == (
        "The delivery date (2020-01-01) has already passed. Choose a future "
        "date, then press Check again."
    )
    assert past_date_message({"locktime": FUTURE}) is None
    assert past_date_message({"locktime": "1y"}) is None
    assert past_date_message(None) is None
    assert past_date_message({"locktime": "garbage"}) is None


# ---------------------------------------------------------- build failure


def test_build_failure_message_each_code():
    """Each reason code of Heirs.buildTransactions has its own sentence."""
    codes = {
        "NO_HEIRS": "No heirs: add at least one heir",
        "NO_UTXO": "The wallet has no spendable funds",
        "NO_WILLEXECUTOR_USABLE": "No usable will-executor",
        "NO_FUTURE_DATE": "No delivery date left to build",
        "WILLEXECUTOR_FEE": "The amount to send must cover the miner fees",
        "WILLEXECUTOR_FEE_TOO_HIGH": "A will-executor asks for more",
        "TX_BUILD_FAILED": "The inheritance transactions could not be assembled",
        "FIXED_EXCEEDS_BALANCE": "The heirs' fixed amounts exceed",
        "WILLEXECUTOR_TX_ERROR": "An unexpected error stopped the transactions",
    }
    for code, start in codes.items():
        msg = build_failure_message(code, False)
        assert msg.startswith(start) and msg.endswith("\n\nSkipped"), code


def test_build_failure_message_past_date_and_unknown_reason():
    """Past date: just Skipped; unknown reason: say it is unknown."""
    # The past date is the cause, already shown on the row above (#75).
    assert build_failure_message("NO_FUTURE_DATE", True) == "Skipped"
    # Only "no future date" gives way to the past date.
    assert build_failure_message("NO_HEIRS", True).startswith("No heirs")
    for reason in (None, "SOMETHING_NEW"):
        msg = build_failure_message(reason, False)
        assert msg.startswith("Could not build the will, and the exact cause")
        assert msg.endswith("\n\nSkipped")


# ------------------------------------------------------------ will checks


def test_executed_inheritance_status():
    """CONFIRMED wins over MEMPOOL; nothing -> None; never raises."""
    status = Will.executed_inheritance_status
    assert status({"a": _Item({"MEMPOOL"}), "b": _Item({"CONFIRMED"})}) == "CONFIRMED"
    assert status({"a": _Item({"MEMPOOL"})}) == "MEMPOOL"
    assert status({"a": _Item({"VALID"})}) is None
    assert status({}) is None
    assert status(None) is None          # never raises


def test_unsigned_will_executors():
    """Executors of VALID items neither signed nor sent; never raises."""
    unsigned = Will.unsigned_will_executors
    will = {
        "a": _Item({"VALID"}, WE),
        "b": _Item({"VALID"}),
        "c": _Item({"VALID", "COMPLETE"}, "https://signed"),
        "d": _Item({"VALID", "PUSHED"}, "https://sent"),
        "e": _Item({"INVALIDATED"}, "https://old"),
    }
    assert unsigned(will) == {WE, None}
    assert unsigned({"c": will["c"], "d": will["d"]}) is None
    assert unsigned({}) is None
    assert unsigned(None) is None        # never raises


def test_amount_warning_is_false_alarm():
    """Past date or no visible coin: not a real amount problem."""
    false_alarm = Will.amount_warning_is_false_alarm
    assert false_alarm(True, [_utxo(1_000_000)]) is True
    assert false_alarm(False, [_utxo(1_000_000)]) is False
    assert false_alarm(False, []) is True
    assert false_alarm(False, None) is True
    broken = MagicMock()
    broken.value_sats.side_effect = RuntimeError("boom")
    assert false_alarm(False, [broken]) is False


# ------------------------------------------------------------------ bridges


class _Dialog:
    """The dialog's bridges on a plain object (no Qt widgets)."""

    _executed_inheritance_status = BalBuildWillDialog._executed_inheritance_status
    _unsigned_will_executors = BalBuildWillDialog._unsigned_will_executors
    _rebuild_reason_message = BalBuildWillDialog._rebuild_reason_message
    _amounts_message = staticmethod(BalBuildWillDialog._amounts_message)
    _amount_warning_is_false_alarm = BalBuildWillDialog._amount_warning_is_false_alarm
    _past_date_message = BalBuildWillDialog._past_date_message
    _build_failure_message = BalBuildWillDialog._build_failure_message

    def __init__(self, bal_window, broadcast=False, postpone=False, unsigned=None):
        self.bal_window = bal_window
        self._invalidation_broadcast = broadcast
        self._postpone_invalidation = postpone
        self._unsigned_executors = unsigned


def _window(locktime=FUTURE, reason=None, will=None):
    """A stand-in for BalWindow with only what the bridges read."""
    return SimpleNamespace(
        will_settings={"locktime": locktime},
        heirs=SimpleNamespace(last_build_error=reason),
        willitems=will or {},
    )


def test_bridges_pass_the_dialog_state():
    """The dialog bridges give the core answer for the dialog's state."""
    will = {"a": _Item({"VALID"}, WE), "b": _Item({"MEMPOOL"})}
    d = _Dialog(_window(PAST, "NO_FUTURE_DATE", will), broadcast=True, postpone=True)
    assert d._executed_inheritance_status() == "MEMPOOL"
    assert d._unsigned_will_executors() == {WE}
    assert d._rebuild_reason_message(NotCompleteWillException()) == (
        rebuild_reason_message(NotCompleteWillException(), True, True, None)
    )
    assert d._past_date_message() == past_date_message({"locktime": PAST})
    assert d._build_failure_message() == "Skipped"
    assert d._amount_warning_is_false_alarm([_utxo(1_000_000)]) is True
    info = {"kind": "fixed_scaled", "shares": [("alice", 100.0)]}
    assert d._amounts_message(info) == amounts_message(info)
    future = _Dialog(_window(FUTURE, "NO_FUTURE_DATE"))
    assert future._build_failure_message() == build_failure_message(
        "NO_FUTURE_DATE", False
    )


def test_bridges_never_raise_without_a_window():
    """As before the move: no window state -> the harmless answer."""
    d = _Dialog(None)
    assert d._executed_inheritance_status() is None
    assert d._unsigned_will_executors() is None
    assert d._past_date_message() is None
    assert d._build_failure_message() == build_failure_message(None, False)
    assert d._amount_warning_is_false_alarm([]) is True
    assert d._amount_warning_is_false_alarm([_utxo(5)]) is False


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
