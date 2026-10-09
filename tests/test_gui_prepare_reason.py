"""
The main window's "Prepare" popup names the real reason (CHANGELOG #78).

It used to show a vague title ("Txfees are changed", "Found CHANGES to the
DATE or the HEIRS") followed by the raw exception text; it now shows the
sentence of the Check dialog (``bal.core.messages``), for the same cases.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_gui_prepare_reason.py
"""

import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.util import Util
from bal.core.will import (
    HeirNotFoundException,
    NotCompleteWillException,
    TxFeesChangedException,
    Will,
)
from bal.gui.qt.window import BalWindow


class _Cfg:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _CfgBag:
    def __init__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)


class _Wallet:
    def dust_threshold(self):
        return 546


def _popups(first_error):
    """Run the Prepare flow with check_will raising ``first_error`` once."""
    win = object.__new__(BalWindow)
    win.disable_plugin = False
    win.heirs = {"h": object()}
    win.willexecutors = {}
    win.no_willexecutor = True
    win.willitems = {}
    win.will = {}
    win.date_to_check = 1_800_000_000
    win.will_settings = {"baltx_fees": 1, "locktime": "1 month"}
    win.bal_plugin = _CfgBag(
        is_basic_mode=lambda: False,
        MAX_WILLEXECUTOR_FEE=_Cfg(1),
        SAVE_HISTORY=_Cfg(True),
        HISTORY_LABEL=_Cfg("LBL"),
    )
    win.window = Mock()
    win.window.wallet = _Wallet()
    with (
        patch.object(Util, "get_available_utxos", return_value=[]),
        patch.object(Util, "parse_locktime_string", return_value=1_800_000_001),
        patch.object(Will, "get_min_locktime", return_value=0),
        patch.object(Will, "check_amounts"),
        patch.object(Will, "remove_stale_wallet_history"),
        patch.object(BalWindow, "init_class_variables"),
        patch.object(BalWindow, "build_will"),
        patch.object(BalWindow, "check_will", side_effect=[first_error, None]),
        patch.object(BalWindow, "update_all"),
        patch.object(BalWindow, "_schedule_history_refresh"),
        patch.object(BalWindow, "show_message") as show,
    ):
        BalWindow.build_inheritance_transaction(win)
    return [c.args[0] for c in show.call_args_list]


def test_added_heir_is_named():
    msgs = _popups(HeirNotFoundException("carol", HeirNotFoundException.HEIR_ADDED))
    assert 'Heir "carol" was added: preparing a new will.' in msgs, msgs
    assert not any("Found CHANGES" in m for m in msgs), msgs


def test_fee_change_shows_both_rates():
    msgs = _popups(TxFeesChangedException("14:  13"))
    assert any("built with 13 sat/byte, now it is 14" in m for m in msgs), msgs


def test_no_new_popup_for_new_funds():
    """New funds never had a popup here; that is unchanged."""
    msgs = _popups(NotCompleteWillException("Some utxo in the wallet is not included"))
    assert not any("does not cover yet" in m for m in msgs), msgs


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All Prepare popup tests passed")
