"""
The bal_* commands and an open plugin window (CHANGELOG #148).

Found in the analysis of 2026-10-15: the plugin window of an open wallet
keeps its own copies of the heirs and of the will and writes them back
later. An heir added from Electrum's Console vanished at the next heir edit
in the window; a will prepared from the Console was thrown away at wallet
close, where the close Check rebuilt, signed and sent a will from the OLD
heirs.

* while the wallet's window is open, the commands that change its heirs or
  will are refused with a clear message, and the wallet does not change;
* the will-executor list belongs to the plugin: its changes are refused
  while any plugin window is open;
* the commands that only read, and the delivery date and fee (shared with
  the window), keep working;
* once the wallet is closed, or without the GUI (daemon), every command
  runs as before;
* every command is classified: a new command must be added to one list.

Three fake will-executors (flowkit); nothing reaches a real server.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_cli_window_guard.py
"""

import json
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import flowkit as fk
import pytest
from electrum.util import UserFacingException

from bal.cli import commands as cli_commands

PW = "flowkit-test"

WALLET_REFUSAL = "This wallet is open in the Àncòra window"
EXECUTOR_REFUSAL = "A wallet is open in the Àncòra window"

# The controller methods that only read (or change the delivery date and
# fee, one dict shared with the window): allowed with the window open.
READS = {
    "delivery_show", "delivery_set", "delivery_form",
    "heirs_list", "heirs_show", "heirs_export",
    "willexecutors_list", "willexecutors_show", "willexecutors_export",
    "will_status", "will_check", "invalidate_will_command",
}


@pytest.fixture
def sess(monkeypatch):
    """A wallet with its plugin window open, as the Qt plugin registers it."""
    fk.ensure_event_loop()
    s = fk.Session(monkeypatch, password=PW)
    s.world.add_executor()   # three fake will-executors
    # The Qt plugin keeps its windows in ``bal_windows`` (Plugin.get_window).
    s.plugin.bal_windows = {id(s.bw): s.bw}
    yield s
    s.close()
    fk.stop_event_loop()


def _snapshot(s):
    """What the wallet and the plugin hold: heirs, will, will-executors."""
    db = s.wallet.db
    will = {k: dict(v) if isinstance(v, dict) else v
            for k, v in db.get_dict("will").items()}
    return json.dumps({
        "heirs": dict(db.get("heirs", {}) or {}),
        "will": will,
        "executors": s.plugin.WILLEXECUTORS.get(),
    }, sort_keys=True, default=str)


def _cli(s, method, *args):
    return cli_commands._call(s.plugin, s.wallet, method, *args)


def _command_methods():
    """Every controller method a bal_* command calls (``commands.py``)."""
    path = os.path.join(os.path.dirname(cli_commands.__file__), "commands.py")
    text = open(path, encoding="utf-8").read()
    return set(re.findall(r'_call_off_loop\(\s*plugin,\s*wallet,\s*"(\w+)"', text))


# ------------------------------------------------------------ the classes

def test_every_command_is_classified():
    methods = _command_methods()
    assert len(methods) >= 25
    writes = cli_commands._WALLET_WRITES | cli_commands._EXECUTOR_WRITES
    assert not (writes & READS)
    assert methods == writes | READS, sorted(methods ^ (writes | READS))


# ------------------------------------------------- the window is open

@pytest.mark.parametrize("method", sorted(cli_commands._WALLET_WRITES))
def test_a_wallet_change_is_refused_and_nothing_changes(sess, method):
    s = sess
    s.check()
    before = _snapshot(s)
    with pytest.raises(UserFacingException) as e:
        _cli(s, method, *([None] * 2))
    assert WALLET_REFUSAL in str(e.value)
    assert "close the wallet and use the command line" in str(e.value)
    assert _snapshot(s) == before


@pytest.mark.parametrize("method", sorted(cli_commands._EXECUTOR_WRITES))
def test_an_executor_change_is_refused(sess, method):
    s = sess
    before = _snapshot(s)
    with pytest.raises(UserFacingException) as e:
        _cli(s, method, *([None] * 2))
    assert EXECUTOR_REFUSAL in str(e.value)
    assert _snapshot(s) == before


def test_the_owners_case_an_heir_added_in_the_console(sess):
    s = sess
    with pytest.raises(UserFacingException):
        _cli(s, "heirs_add", "carol", fk.HEIR_ADDR["carol"], "10%")
    # Nothing to lose later: the window and the wallet agree.
    assert sorted(s.wallet.db.get("heirs")) == sorted(s.bw.heirs) == ["alice", "bob"]


def test_the_reads_keep_working_and_change_nothing(sess):
    s = sess
    s.check()
    before = _snapshot(s)
    url = sorted(_cli(s, "willexecutors_list"))[0]
    assert sorted(_cli(s, "heirs_list")) == ["alice", "bob"]
    assert _cli(s, "heirs_show", "alice")
    assert _cli(s, "willexecutors_show", url)
    assert _cli(s, "will_status")
    assert _cli(s, "delivery_show")
    _cli(s, "will_check")
    _cli(s, "heirs_export", os.path.join(s.dir, "heirs.json"))
    _cli(s, "willexecutors_export", os.path.join(s.dir, "executors.json"))
    try:
        _cli(s, "invalidate_will_command")
    except UserFacingException as e:
        # Its own answer (e.g. nothing to invalidate), never the #148 refusal.
        assert WALLET_REFUSAL not in str(e)
    assert _snapshot(s) == before


def test_the_delivery_fee_is_still_allowed(sess):
    s = sess
    _cli(s, "delivery_set", None, 21)
    assert s.bw.will_settings.get("baltx_fees") == 21


def test_an_executor_change_is_refused_for_another_wallets_window(sess):
    s = sess
    other = SimpleNamespace(wallet=object(), disable_plugin=False)
    s.plugin.bal_windows = {1: other}
    # This wallet's own change is allowed (its window is not open)...
    _cli(s, "heirs_add", "carol", fk.HEIR_ADDR["carol"], "10%")
    assert "carol" in s.wallet.db.get("heirs")
    # ...the plugin's will-executor list is not.
    with pytest.raises(UserFacingException) as e:
        _cli(s, "willexecutors_select", [sorted(_cli(s, "willexecutors_list"))[0]], False)
    assert EXECUTOR_REFUSAL in str(e.value)


# ------------------------------------------- closed wallet, or no GUI

def test_after_the_wallet_closes_every_command_runs(sess):
    s = sess
    # No close Check here: since #150 only ADVANCED mode can switch it off.
    s.plugin.USER_TYPE.set("advanced")
    s.plugin.REBUILD_ON_CLOSE.set(False)
    s.close_wallet()
    assert s.bw.disable_plugin            # still listed, no longer open
    _cli(s, "heirs_add", "carol", fk.HEIR_ADDR["carol"], "10%")
    assert "carol" in s.wallet.db.get("heirs")
    url = sorted(_cli(s, "willexecutors_list"))[0]
    _cli(s, "willexecutors_select", [url], False)


def test_without_the_gui_every_command_runs(sess):
    s = sess
    del s.plugin.bal_windows              # the daemon's plugin has none
    _cli(s, "heirs_update", "bob", None, "30%")
    _cli(s, "heirs_add", "carol", fk.HEIR_ADDR["carol"], "10%")
    assert "carol" in s.wallet.db.get("heirs")
    assert _cli(s, "prepare_will")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
