"""
Tests for the history-save hook in ``BalWindow``.

Verifies that every successful prepare (including the "Prepare" menu action)
persists the freshly prepared transactions into the wallet's local history,
while abort paths (which return ``None``) skip the save. Also verifies that
after any local-history change (saving or removing will transactions) the
wallet tabs are re-rendered through ``update_tabs`` (all tabs) plus
``update_status`` (status-bar balance), and that the rebuild path uses the same
full refresh.

Run:
    source electrum/env/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/test_gui_prepare_will_history.py
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from unittest.mock import Mock, patch

import bal.gui.qt.window as win_mod
from bal.core.util import Util
from bal.core.will import NotCompleteWillException, Will
from bal.gui.qt.window import BalWindow

# ------------------------------------------------------------------ #
# prepare_will -> _save_will_to_history
# ------------------------------------------------------------------ #

def test_prepare_will_saves_to_history_on_success():
    win = object.__new__(BalWindow)
    will = {"wid": object()}
    with (
        patch.object(BalWindow, "build_inheritance_transaction", return_value=will)
        as build_mock,
        patch.object(BalWindow, "_save_will_to_history") as save_mock,
    ):
        result = BalWindow.prepare_will(win)
    assert result is will
    build_mock.assert_called_once_with(ignore_duplicate=False, keep_original=False)
    save_mock.assert_called_once_with()


def test_prepare_will_skips_save_when_build_aborted():
    win = object.__new__(BalWindow)
    with (
        patch.object(BalWindow, "build_inheritance_transaction", return_value=None)
        as build_mock,
        patch.object(BalWindow, "_save_will_to_history") as save_mock,
    ):
        result = BalWindow.prepare_will(win)
    assert result is None
    build_mock.assert_called_once_with(ignore_duplicate=False, keep_original=False)
    save_mock.assert_not_called()


# ------------------------------------------------------------------ #
# history refresh: _save_will_to_history -> update_tabs + update_status
# ------------------------------------------------------------------ #

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


class _FakeWindow:
    def __init__(self, *, with_update_tabs=True):
        self.show_message = Mock()
        self.update_status = Mock()
        self.history_list = Mock()
        self.history_list.update = Mock()
        if with_update_tabs:
            self.update_tabs = Mock()


class _FakeQTimer:
    calls = []

    @classmethod
    def singleShot(cls, delay, callable_):
        cls.calls.append((delay, callable_))


def _make_save_window(save_enabled):
    win = object.__new__(BalWindow)
    win.bal_plugin = _CfgBag(
        SAVE_HISTORY=_Cfg(save_enabled), HISTORY_LABEL=_Cfg("LBL")
    )
    win.willitems = {"wid": object()}
    win.wallet = object()
    return win


def test_save_will_to_history_schedules_refresh_when_enabled():
    win = _make_save_window(save_enabled=True)
    with (
        patch.object(Will, "save_valid_transactions_to_history") as save_mock,
        patch.object(BalWindow, "_schedule_history_refresh") as schedule_mock,
    ):
        BalWindow._save_will_to_history(win)
    save_mock.assert_called_once_with(win.willitems, win.wallet, "LBL")
    schedule_mock.assert_called_once_with()


def test_save_will_to_history_skips_save_and_refresh_when_disabled():
    win = _make_save_window(save_enabled=False)
    with (
        patch.object(Will, "save_valid_transactions_to_history") as save_mock,
        patch.object(BalWindow, "_schedule_history_refresh") as schedule_mock,
    ):
        BalWindow._save_will_to_history(win)
    save_mock.assert_not_called()
    schedule_mock.assert_not_called()


def test_save_will_to_history_schedules_refresh_even_on_error():
    win = _make_save_window(save_enabled=True)
    with (
        patch.object(
            Will,
            "save_valid_transactions_to_history",
            side_effect=RuntimeError("boom"),
        ),
        patch.object(BalWindow, "_schedule_history_refresh") as schedule_mock,
    ):
        BalWindow._save_will_to_history(win)
    schedule_mock.assert_called_once_with()


def test_schedule_history_refresh_marshals_to_gui_thread():
    win = object.__new__(BalWindow)
    with patch.object(win_mod, "QTimer", _FakeQTimer):
        _FakeQTimer.calls.clear()
        BalWindow._schedule_history_refresh(win)
    assert _FakeQTimer.calls == [(0, win._refresh_after_history_save)]


def test_refresh_after_history_save_calls_update_tabs_and_status():
    win = object.__new__(BalWindow)
    win.window = _FakeWindow(with_update_tabs=True)
    BalWindow._refresh_after_history_save(win)
    win.window.update_tabs.assert_called_once_with()
    win.window.update_status.assert_called_once_with()
    win.window.history_list.update.assert_not_called()


def test_refresh_after_history_save_falls_back_to_history_list():
    win = object.__new__(BalWindow)
    win.window = _FakeWindow(with_update_tabs=False)
    BalWindow._refresh_after_history_save(win)
    win.window.history_list.update.assert_called_once_with()
    win.window.update_status.assert_called_once_with()


def test_rebuild_path_schedules_full_refresh():
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
        MAX_WILLEXECUTOR_FEE=_Cfg(1),
        SAVE_HISTORY=_Cfg(True),
        HISTORY_LABEL=_Cfg("LBL"),
    )
    win.window = _FakeWindow()
    win.window.wallet = _Wallet()
    with (
        patch.object(Util, "get_available_utxos", return_value=[]),
        patch.object(Util, "parse_locktime_string", return_value=1_800_000_001),
        patch.object(Will, "get_min_locktime", return_value=0),
        patch.object(Will, "check_amounts"),
        patch.object(BalWindow, "init_class_variables"),
        patch.object(BalWindow, "build_will"),
        patch.object(
            BalWindow,
            "check_will",
            side_effect=[NotCompleteWillException(), None],
        ),
        patch.object(BalWindow, "update_all"),
        patch.object(BalWindow, "_schedule_history_refresh") as schedule_mock,
    ):
        BalWindow.build_inheritance_transaction(win)
    schedule_mock.assert_called_once_with()


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All prepare-will history tests passed")
