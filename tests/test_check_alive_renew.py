"""
Tests for the Check Alive renewal (CHANGELOG #79).

A passed Check Alive used to invalidate the will on its own and then stop on
"Checking variables: Wait" with no will in force. Now the owner is asked to
RENEW (invalidate the old will, then prepare the new one at once); when a
one-click renewal is not possible only the reason is shown.

* ``check_alive_renewal`` (core): passed or not, renewable or why not;
* the texts (``bal.core.messages``);
* ``Will.planned_delivery``: the REAL delivery date shown next to the field;
* ``Will.refresh_invalidated``: never raises;
* ``BalWindow``: the offer on wallet open / Prepare, no invalidation in the
  automatic rebuild.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_check_alive_renew.py
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.checkalive import CheckAliveError, check_alive_renewal
from bal.core.messages import (
    check_alive_cannot_renew,
    check_alive_message,
    check_alive_passed_sentence,
    check_alive_renew_question,
)
from bal.core.will import Will, WillItem
from bal.gui.qt import window as win_mod
from bal.gui.qt.window import BalWindow

DAY = 86400
NOW = 1_800_000_000 - (1_800_000_000 % DAY)   # 2027-01-15, midnight UTC
FROZEN = NOW + 20 * DAY                       # delivery frozen in the will

# WillItem parses a raw tx and calls add_info_from_wallet; no live wallet here.
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)


def _renewal(locktime, threshold="-180d", built=FROZEN, basic=False):
    return check_alive_renewal(
        basic, {"locktime": locktime, "threshold": threshold},
        built_locktime=built, creation_date=NOW - 300 * DAY, now=NOW,
    )


# --------------------------------------------------------- check_alive_renewal

def test_not_passed_or_basic_mode_gives_none():
    assert _renewal("200d", "-10d") is None          # Check Alive in 10 days
    assert _renewal("200d", basic=True) is None


def test_relative_delivery_is_renewable_from_today():
    info = _renewal("200d")
    assert info["renewable"] and info["has_will"] and info["reason"] is None
    assert info["check_date"] == FROZEN - 180 * DAY  # 160 days ago
    assert info["new_delivery"] == NOW + 200 * DAY   # counted from today
    assert info["new_check"] == NOW + 20 * DAY
    assert info["period"] == (200, "d")


def test_fixed_delivery_date_is_not_renewable():
    info = _renewal(FROZEN)
    assert not info["renewable"] and info["reason"] == "fixed_date"


def test_renewal_whose_check_alive_is_still_past():
    """Delivery 100 days, Check Alive 180 days before: renewing is useless."""
    info = _renewal("100d")
    assert not info["renewable"] and info["reason"] == "still_past"


def test_will_past_its_delivery_is_the_expired_case():
    """C1adv/C3adv: not a Check Alive matter, it must be invalidated."""
    info = _renewal("200d", built=NOW - DAY)
    assert info["reason"] == "expired" and not info["renewable"]
    assert "can be executed now" in check_alive_message(info)


def test_no_will_in_force():
    """S1: first will with a delivery closer than the Check Alive."""
    info = _renewal("90d", built=None)
    assert info is not None and not info["has_will"]


# ------------------------------------------------------------------ messages

def test_question_names_both_dates_and_the_period():
    text = check_alive_renew_question(_renewal("200d"))
    assert "Are you still here?" in text
    assert "2026-08-08" in text            # passed Check Alive (FROZEN - 180)
    assert "2027-08-03" in text            # NOW + 200 days
    assert "today + 200 days" in text and "miner fee" in text
    assert check_alive_message(_renewal("200d")) == text


def test_years_and_singular_periods():
    assert "today + 4 years" in check_alive_renew_question(_renewal("4y"))
    assert "today + 1 year)" in check_alive_renew_question(_renewal("1y"))


def test_reasons_when_not_renewable():
    assert "fixed date" in check_alive_cannot_renew(_renewal(FROZEN))
    assert "renewed will would have its Check Alive in the past" in (
        check_alive_cannot_renew(_renewal("100d")))
    assert "choose a later delivery date or a shorter Check Alive" in (
        check_alive_cannot_renew(_renewal("90d", built=None)))
    assert check_alive_message(_renewal(FROZEN)) == check_alive_cannot_renew(
        _renewal(FROZEN))


def test_cli_sentence():
    assert "has passed: a renewed will would be delivered on 2027-08-03" in (
        check_alive_passed_sentence(_renewal("200d")))


# ------------------------------------------------------------ planned_delivery

def _will(recipe, tx_locktime):
    item = WillItem({
        "tx": _TX_HEX[:-8] + tx_locktime.to_bytes(4, "little").hex(),
        "heirs": {"alice": ["tb1qalice", "100%", recipe]},
        "willexecutor": None,
        "baltx_fees": 13,
    })
    return {item._id: item}


def test_planned_delivery_is_the_frozen_date_of_the_will():
    """"4y" next to a will built a year ago: its own date, not today + 4y."""
    will = _will("4y", FROZEN)
    assert Will.planned_delivery(will, "4y") == (FROZEN, True)


def test_planned_delivery_of_a_changed_setting():
    will = _will("4y", FROZEN)
    ts, in_force = Will.planned_delivery(will, "5y")
    assert not in_force and ts == FROZEN + 365 * DAY


def test_planned_delivery_without_a_will():
    ts, in_force = Will.planned_delivery({}, FROZEN)
    assert (ts, in_force) == (FROZEN, False)


def test_refresh_invalidated_never_raises():
    Will.refresh_invalidated({"x": object()}, [], None)


# ------------------------------------------------------------------ BalWindow

def _window(info):
    win = object.__new__(BalWindow)
    win.check_alive_info = MagicMock(return_value=info)
    win.ask_renew_will = MagicMock(return_value=True)
    win.show_warning = MagicMock()
    return win


def test_offer_renew_runs_the_wizard_with_renewal_approved():
    win = _window(_renewal("200d"))
    with patch.object(win_mod, "BalBuildWillDialog") as dlg:
        assert BalWindow.offer_check_alive_renewal(win) is True
    dlg.assert_called_once_with(win, renew=True)
    dlg.return_value.build_will_task.assert_called_once_with()


def test_not_now_does_nothing():
    win = _window(_renewal("200d"))
    win.ask_renew_will.return_value = False
    with patch.object(win_mod, "BalBuildWillDialog") as dlg:
        BalWindow.offer_check_alive_renewal(win)
    dlg.assert_not_called()


def test_not_renewable_only_shows_the_reason():
    win = _window(_renewal(FROZEN))
    with patch.object(win_mod, "BalBuildWillDialog") as dlg:
        BalWindow.offer_check_alive_renewal(win)
    dlg.assert_not_called()
    win.ask_renew_will.assert_not_called()
    assert "fixed date" in win.show_warning.call_args.args[0]


def test_nothing_to_offer():
    assert BalWindow.offer_check_alive_renewal(_window(None)) is False


def test_wallet_open_skips_basic_mode():
    win = object.__new__(BalWindow)
    win.disable_plugin = False
    win.bal_plugin = MagicMock()
    win.bal_plugin.is_basic_mode.return_value = True
    win.offer_check_alive_renewal = MagicMock()
    BalWindow.check_alive_on_open(win)
    win.offer_check_alive_renewal.assert_not_called()


def test_wallet_open_offers_when_passed():
    win = object.__new__(BalWindow)
    win.disable_plugin = False
    win.bal_plugin = MagicMock()
    win.bal_plugin.is_basic_mode.return_value = False
    win.willitems = _will("200d", FROZEN)
    win._refresh_invalidated_items = MagicMock()
    info = _renewal("200d")
    win.check_alive_info = MagicMock(return_value=info)
    win.offer_check_alive_renewal = MagicMock()
    BalWindow.check_alive_on_open(win)
    win._refresh_invalidated_items.assert_called_once_with()
    win.offer_check_alive_renewal.assert_called_once_with(info)


def test_automatic_rebuild_never_invalidates_for_a_passed_check_alive():
    win = object.__new__(BalWindow)
    win._AUTO_REBUILD_COOLDOWN = 0
    win.init_class_variables = MagicMock(side_effect=CheckAliveError(NOW - DAY))
    win._auto_invalidate_will = MagicMock()
    assert BalWindow._auto_rebuild_flow(win) is False
    win._auto_invalidate_will.assert_not_called()


def test_automatic_rebuild_still_invalidates_an_expired_will():
    win = object.__new__(BalWindow)
    win.init_class_variables = MagicMock(side_effect=CheckAliveError(NOW - DAY))
    win.check_alive_info = MagicMock(return_value=_renewal("200d", built=NOW - DAY))
    win._auto_invalidate_will = MagicMock()
    assert BalWindow._auto_rebuild_flow(win) is True
    win._auto_invalidate_will.assert_called_once_with()


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All Check Alive renewal tests passed")
