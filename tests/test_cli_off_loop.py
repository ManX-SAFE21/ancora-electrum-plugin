"""The ``bal_*`` commands do their work off Electrum's network thread (#116).

Electrum runs every command body on its network (asyncio) thread. The
plugin talks to the will-executors through ``Network.send_http_on_proxy``,
which Electrum forbids on that thread and which, called from another thread,
waits for that same thread. Before CHANGELOG #116, on a daemon with a
network: the welist download failed at once ("must not be called from
asyncio thread"), the send and the check ended in "no answer in time" after
20 seconds (the request left only after the command had given up) and the
ping blocked the daemon for good. Offline daemons and the unit tests never
showed it.

Here the commands run through Electrum's own command wrappers, on an event
loop, and a fake ``Network`` behaves like Electrum 4.8.2's: it refuses the
event-loop thread and runs each request as a coroutine ON that loop. Three
will-executors are used (owner rule, 2026-10-10), one of them slow. Also
checked: one command at a time per wallet, the "busy" message, and that
different wallets and the settings do not wait.

Run:
    PYTHONPATH=electrum-src python3 -m pytest tests/test_cli_off_loop.py -q
"""

import asyncio
import gc
import os
import sys
import threading
import time
import unittest.mock as mock
import weakref
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum import util as electrum_util
from electrum.commands import Commands
from electrum.util import UserFacingException
from test_cli_command_layer import FakeDaemon
from test_cli_controller_offline import VALID_ADDRESS, FakeWallet, Plugin

import bal  # noqa: F401  (importing the package registers the bal_* commands)
from bal.cli import commands as cli_commands
from bal.cli.controller import BalController
from bal.core import willexecutors as we_mod
from bal.core.plugin_base import BalPlugin
from bal.engine.locks import flow_lock_for

# Three local will-executors; the second one answers late.
WE = ["https://we-a.test", "https://we-b.test", "https://we-c.test"]
SLOW = {"https://we-b.test": 0.4}
# The delivery date of the fake will items (2030-03-17 17:46 UTC).
DELIVERY = 1_900_000_000
# Stops a regression from hanging the suite: Electrum itself waits forever.
GUARD = 8


class Daemon(FakeDaemon):
    """A fake daemon with several wallets, found by the end of their path."""

    def __init__(self, plugin, wallets):
        super().__init__(plugin)
        self._wallets = wallets

    def get_wallet(self, path):
        """The wallet whose name ends ``path``, or ``None``."""
        for name, wallet in self._wallets.items():
            if path and path.endswith(name):
                return wallet
        return None


class FakeNetwork:
    """``electrum.network.Network`` as the plugin uses it, with Electrum
    4.8.2's thread rules: ``send_http_on_proxy`` asserts it is not on the
    event-loop thread, then runs the request as a coroutine ON that loop and
    waits for it (so a blocked loop means no answer)."""

    loop = None             # the loop the commands run on
    answers = {}            # (method, url suffix) -> answer, per test
    received = []           # (time, method, url) in arrival order
    already_present = set()  # executors answering "already present"

    @classmethod
    def get_instance(cls):
        """A network exists (the daemon is online)."""
        return cls

    @classmethod
    async def _answer(cls, method, url, kwargs):
        """The fake server: logs the request, waits if slow, answers."""
        base = next((w for w in WE if url.startswith(w)), url)
        cls.received.append((time.monotonic(), method, url))
        await asyncio.sleep(SLOW.get(base, 0))
        if url.endswith("/info"):
            return {"address": VALID_ADDRESS, "base_fee": 1000, "info": "fake"}
        if url.endswith("/pushtxs"):
            return "already present" if base in cls.already_present else "thx"
        if url.endswith("/searchtx"):
            return {"tx": "pushed"}
        if "/data/" in url:
            return {w: {"url": w, "address": VALID_ADDRESS, "base_fee": 1000,
                        "info": "fake"} for w in WE}
        return None

    @classmethod
    def send_http_on_proxy(cls, method, url, **kwargs):
        """Electrum 4.8.2's rule and mechanism (network.py)."""
        assert electrum_util.get_running_loop() != cls.loop, (
            "must not be called from asyncio thread"
        )
        fut = asyncio.run_coroutine_threadsafe(
            cls._answer(method, url, kwargs), cls.loop
        )
        return fut.result(GUARD)


@pytest.fixture
def plugin():
    """A real ``bal.cli.plugin.Plugin`` with the three will-executors selected."""
    with Plugin() as p:
        executors = {
            url: {"url": url, "selected": True, "base_fee": 1000,
                  "address": VALID_ADDRESS, "status": 200, "info": "fake"}
            for url in WE
        }
        p.WILLEXECUTORS.set({BalPlugin.chainname: executors})
        yield p


@pytest.fixture
def network(monkeypatch):
    """Install the fake network; reset what it saw."""
    monkeypatch.setattr(we_mod, "Network", FakeNetwork)
    FakeNetwork.received = []
    FakeNetwork.already_present = set()
    yield FakeNetwork
    FakeNetwork.loop = None


def run_together(plugin, calls, wallets):
    """Run several ``(name, wallet_name, args, kwargs)`` commands at once on
    one event loop, as Electrum's daemon does; return results or errors."""
    async def main():
        FakeNetwork.loop = asyncio.get_running_loop()
        tasks = []
        for name, wallet_name, args, kwargs in calls:
            cmds = Commands(config=plugin.config, daemon=Daemon(plugin, wallets))
            if wallet_name is not None:
                kwargs = dict(kwargs, wallet_path=wallet_name)
            tasks.append(getattr(cmds, "bal_" + name)(*args, **kwargs))
        return await asyncio.gather(*tasks, return_exceptions=True)

    return asyncio.run(main())


def run_one(plugin, name, *args, wallet=None, **kwargs):
    """Run one command on a wallet named ``w`` and return its result."""
    wallets = {"w": wallet} if wallet is not None else {}
    out = run_together(plugin, [(name, "w" if wallet else None, args, kwargs)],
                       wallets)[0]
    if isinstance(out, BaseException):
        raise out
    return out


# --------------------------------------------------------------- the thread


def test_the_work_runs_off_the_network_thread(plugin):
    """The controller runs on a worker thread: no event loop there."""
    seen = {}

    def heirs_list(self):
        seen["loop"] = electrum_util.get_running_loop()
        seen["thread"] = threading.current_thread()
        return {}

    with mock.patch.object(BalController, "heirs_list", heirs_list):
        assert run_one(plugin, "heirs_list", wallet=FakeWallet()) == {}
    assert seen["loop"] is None
    assert seen["thread"] is not threading.main_thread()


def test_ping_three_executors(plugin, network):
    """The ping reaches the three will-executors (one slow) and returns; it
    used to block the daemon for good."""
    start = time.monotonic()
    out = run_one(plugin, "willexecutors_ping", urls=WE, wallet=FakeWallet())
    assert out == {url: {"status": 200, "ok": True} for url in WE}
    assert sorted(url for _t, _m, url in network.received) == [
        url + "/" + BalPlugin.chainname + "/info" for url in WE
    ]
    assert time.monotonic() - start < GUARD


def test_download_the_list(plugin, network):
    """The welist download (advanced mode) works; it failed with "must not
    be called from asyncio thread"."""
    plugin.USER_TYPE.set("advanced")
    plugin.WELIST_SERVER.set("https://welist.test/")
    out = run_one(plugin, "willexecutors_download", wallet=FakeWallet())
    assert out["downloaded"] == 3


class _Item:
    """A will item with just what the push and the check use.

    Since #123 the controller also reads every will item when it loads the
    wallet's delivery settings (``bal.core.delivery.views_from``): the
    transaction's locktime (a date), the fee rate, the status table and the
    heirs. Without them the settings fail closed and the send is refused
    (rule I6), as for a broken will item in a real wallet.
    """

    def __init__(self, url):
        """One signed item for the will-executor ``url``."""
        self.we = {"url": url}
        self.status = {"VALID": True, "COMPLETE": True}
        self.tx = SimpleNamespace(locktime=DELIVERY)
        self.tx_fees = 13
        self.heirs = {}

    @property
    def STATUS(self):  # noqa: N802 - the WillItem attribute name
        """The status flags, as ``WillItem.STATUS`` (``{flag: bool}`` form)."""
        return self.status

    def get_status(self, name):
        """True when the status flag ``name`` is set."""
        return bool(self.status.get(name))

    def set_status(self, name, value=True):
        """Set (or clear) the status flag ``name``."""
        self.status[name] = value

    def set_check_willexecutor(self, resp):
        """The real rule in short: an answer with a tx means CHECKED."""
        ok = bool(resp and "tx" in resp)
        self.set_status("CHECKED" if ok else "CHECK_FAIL")
        self.set_status("PUSHED", ok)
        return ok


@pytest.fixture
def three_item_will():
    """A will with one signed item per will-executor (``tx-a``, ``tx-b``,
    ``tx-c``); loading and saving the wallet are skipped."""
    items = {"tx-" + url[-6]: _Item(url) for url in WE}
    targets = {
        url: {"url": url, "txs": "00", "txsids": ["tx-" + url[-6]]} for url in WE
    }

    def init_will(self):
        self.will = {}
        self.willitems = items

    with mock.patch.object(BalController, "_init_will", init_will), \
         mock.patch.object(BalController, "save_willitems"), \
         mock.patch.object(we_mod.Willexecutors, "get_willexecutor_transactions",
                           side_effect=lambda *a, **k: {u: dict(t) for u, t in targets.items()}):
        yield items


def test_broadcast_to_three_executors(plugin, network, three_item_will):
    """The send reaches the three will-executors, the slow one too, and
    every item is marked sent. It used to end in "no answer in time"."""
    out = run_one(plugin, "will_broadcast", wallet=FakeWallet())
    assert out == {url: "Success" for url in WE}
    assert all(i.get_status("PUSHED") for i in three_item_will.values())
    assert len(network.received) == 3


def test_broadcast_already_present_is_checked(plugin, network, three_item_will):
    """An executor that already holds the will is asked (searchtx) on the
    spot; that direct call failed on the network thread."""
    network.already_present = {WE[2]}
    run_one(plugin, "will_broadcast", wallet=FakeWallet())
    item = three_item_will["tx-" + WE[2][-6]]
    assert item.get_status("CHECKED") and item.get_status("PUSHED")
    assert any(url.endswith("/searchtx") for _t, _m, url in network.received)


def test_check_three_executors(plugin, network, three_item_will):
    """The check asks the three will-executors and marks every item CHECKED;
    it used to mark them CHECK_FAIL (and not sent) after 20 seconds."""
    for item in three_item_will.values():
        item.set_status("PUSHED")
    out = run_one(plugin, "will_check_executor", wallet=FakeWallet())
    assert all(i.get_status("CHECKED") for i in three_item_will.values())
    assert not any(i.get_status("CHECK_FAIL") for i in three_item_will.values())
    assert {v["checked"] for v in out.values()} == {True}


# ------------------------------------------------------- one at a time


def _timed_heirs_list(log, delay=0.3):
    """A ``heirs_list`` that records when it starts and ends."""
    def heirs_list(self):
        start = time.monotonic()
        time.sleep(delay)
        log.append((id(self.wallet), start, time.monotonic()))
        return {}
    return heirs_list


def test_same_wallet_one_command_at_a_time(plugin):
    """Two commands on one wallet started together run one after the other."""
    log, wallet = [], FakeWallet()
    with mock.patch.object(BalController, "heirs_list", _timed_heirs_list(log)):
        out = run_together(plugin, [("heirs_list", "w", (), {})] * 2, {"w": wallet})
    assert out == [{}, {}]
    (_, s1, e1), (_, s2, e2) = sorted(log, key=lambda r: r[1])
    assert s2 >= e1


def test_different_wallets_do_not_wait(plugin):
    """Commands on two wallets run at the same time."""
    log = []
    wallets = {"w1": FakeWallet(), "w2": FakeWallet()}
    with mock.patch.object(BalController, "heirs_list", _timed_heirs_list(log)):
        run_together(plugin, [("heirs_list", "w1", (), {}),
                              ("heirs_list", "w2", (), {})], wallets)
    (_, s1, e1), (_, s2, e2) = sorted(log, key=lambda r: r[1])
    assert s2 < e1


def test_busy_message(plugin, monkeypatch):
    """A command that cannot get the wallet in time says so."""
    monkeypatch.setattr(cli_commands, "BUSY_TIMEOUT", 0.2)
    wallet = FakeWallet()
    lock = flow_lock_for(wallet)
    lock.acquire()
    try:
        with pytest.raises(UserFacingException, match="Another command is still"):
            run_one(plugin, "heirs_list", wallet=wallet)
    finally:
        lock.release()
    assert run_one(plugin, "heirs_list", wallet=wallet) == {}


def test_settings_do_not_wait_for_a_wallet(plugin):
    """The settings belong to the plugin: they run while a wallet is busy."""
    wallet = FakeWallet()
    lock = flow_lock_for(wallet)
    lock.acquire()
    try:
        assert run_one(plugin, "settings_get", "bal_tx_fees")["value"] == 100
    finally:
        lock.release()


def test_lock_released_after_an_error(plugin):
    """A command that fails leaves the wallet free for the next one."""
    wallet = FakeWallet()
    with mock.patch.object(BalController, "heirs_list",
                           side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            run_one(plugin, "heirs_list", wallet=wallet)
    assert flow_lock_for(wallet).acquire(blocking=False)
    flow_lock_for(wallet).release()


def test_flow_lock_for():
    """Same wallet, same lock; another wallet, another lock; a forgotten
    wallet frees its lock."""
    a, b = FakeWallet(), FakeWallet()
    assert flow_lock_for(a) is flow_lock_for(a)
    assert flow_lock_for(a) is not flow_lock_for(b)
    ref = weakref.ref(a)
    del a
    gc.collect()
    assert ref() is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
