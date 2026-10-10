"""The GUI window as an engine host (CHANGELOG #118).

Since #118 ``BalWindow`` runs the state steps of ``bal.engine.context`` (the
same ones the command line uses since #117) and keeps its own behaviour
through three hooks: a will setting goes through ``update_setting_widgets``
(fields refreshed, settings stored), the wallet file is never written by the
window (Electrum does it), and a History save is followed by the History
redraw. ``init_class_variables`` keeps its error logging: "no heirs" is
raised without a log, every later error is logged and raised again.

The windows here are real ``BalWindow`` objects without ``__init__`` (no Qt
tabs), with only what each step reads.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 -m pytest tests/test_gui_engine_host.py -q
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.checkalive import CheckAliveError
from bal.core.will import NoHeirsException
from bal.engine import context
from bal.engine.host import WalletHost
from bal.gui.qt import window as win_mod
from bal.gui.qt.window import BalWindow


def _window(**attrs):
    """A ``BalWindow`` without its Qt ``__init__``, with ``attrs`` set."""
    win = object.__new__(BalWindow)
    for name, value in attrs.items():
        setattr(win, name, value)
    return win


# ------------------------------------------------------------------ hooks


def test_the_window_has_the_three_hooks():
    """Every hook of ``WalletHost`` is a method of ``BalWindow``."""
    hooks = [n for n in vars(WalletHost) if not n.startswith("_")]
    assert sorted(hooks) == [
        "after_history_saved", "flush_wallet_db", "store_will_setting"]
    for name in hooks:
        assert callable(getattr(BalWindow, name)), name


def test_store_will_setting_refreshes_and_stores():
    """The same call ``_normalize_check_alive`` made before #118."""
    win = _window(update_setting_widgets=MagicMock())
    win.store_will_setting("threshold", "-31d")
    win.update_setting_widgets.assert_called_once_with(
        "-31d", "threshold", update_all=True
    )


def test_flush_wallet_db_never_writes_the_wallet():
    """The window leaves the wallet file to Electrum."""
    wallet = MagicMock()
    win = _window(wallet=wallet)
    win.flush_wallet_db()
    wallet.save_db.assert_not_called()


def test_after_history_saved_redraws_the_history():
    """The History redraw is scheduled on the GUI thread."""
    win = _window(_schedule_history_refresh=MagicMock())
    win.after_history_saved()
    win._schedule_history_refresh.assert_called_once_with()


def test_save_willitems_does_not_write_the_wallet():
    """``save_willitems`` stores the will in the wallet dict only."""
    wallet = MagicMock()
    item = MagicMock()
    item.to_dict.return_value = {"tx": "raw", "status": "New"}
    win = _window(wallet=wallet, will={"old": {}}, willitems={"t1": item})
    BalWindow.save_willitems(win)
    assert win.will == {"t1": {"tx": "raw", "status": "New"}}
    wallet.save_db.assert_not_called()


# ------------------------------------------------- the steps go to the engine


@pytest.mark.parametrize("method, function, args", [
    ("load_willitems", "load_willitems", ()),
    ("save_willitems", "save_willitems", ()),
    ("_save_will_to_history", "save_to_history", ()),
    ("_refresh_invalidated_items", "refresh_invalidated_items", ()),
    ("_normalize_check_alive", "normalize_check_alive", ()),
    ("check_alive_info", "check_alive_info", ()),
    ("check_will", "check_will", ()),
    ("init_heirs_to_locktime", "init_heirs_to_locktime", (True,)),
])
def test_each_step_calls_the_engine(method, function, args):
    """Each window step runs the engine function on the window itself."""
    win = _window(willitems={})
    with patch.object(context, function, return_value="engine") as fn:
        out = getattr(BalWindow, method)(win, *args)
    fn.assert_called_once_with(win, *args)
    if method in ("check_alive_info", "check_will"):
        assert out == "engine"


def test_load_willitems_shows_the_will():
    """After loading, a non-empty will is shown in the Will tab."""
    win = _window(will_list_widget=MagicMock(), will_tab=MagicMock())

    def load(host):
        host.willitems = {"t1": object()}

    with patch.object(context, "load_willitems", side_effect=load):
        BalWindow.load_willitems(win)
    win.will_list_widget.update_will.assert_called_once_with(win.willitems)
    assert win.will_list_widget.will is win.willitems
    win.will_tab.update.assert_called_once_with()


def test_load_willitems_empty_leaves_the_tab_alone():
    """No will items: the Will tab is not touched."""
    win = _window(will_list_widget=MagicMock(), will_tab=MagicMock())

    def load(host):
        host.willitems = {}

    with patch.object(context, "load_willitems", side_effect=load):
        BalWindow.load_willitems(win)
    win.will_list_widget.update_will.assert_not_called()
    win.will_tab.update.assert_not_called()


# ---------------------------------------------------------- error logging


def test_no_heirs_is_raised_without_a_log():
    """As before #118: the check came before the logged ``try``."""
    win = _window(heirs={})
    with patch.object(win_mod, "log_error") as log:
        with pytest.raises(NoHeirsException):
            BalWindow.init_class_variables(win)
    log.assert_not_called()


def test_later_errors_are_logged_and_raised():
    """A Check Alive error (or any later one) is logged, then raised."""
    win = _window()
    error = CheckAliveError(1700000000)
    with patch.object(context, "init_class_variables", side_effect=error), \
         patch.object(win_mod, "log_error") as log:
        with pytest.raises(CheckAliveError):
            BalWindow.init_class_variables(win)
    log.assert_called_once_with(error)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
