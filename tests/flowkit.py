"""An offline world for the Phase 5e characterization tests (CHANGELOG #112).

Phase 5e moves the flows of the "Check your will" dialog into one engine
shared by the GUI and the command line. Before any code moves, the tests
built on this module record what the REAL ``BalBuildWillDialog`` does today
(rows, report, buttons, popups, password prompts, will items, settings,
labels, broadcasts, pushes, verifications) and compare it with saved
"golden" files, so each later step can prove that nothing changed.

Taken from the scenario harness (``context/bal-test-harness/harness.py``,
outside the repository), reduced to what pytest needs:

* a real Electrum testnet wallet in a temporary folder, made from a fixed
  test key (``_TEST_KEY_LABEL``): no seed or key is stored anywhere, it is
  derived from a public label at every run, and it never holds real coins.
  The same key gives the same addresses and transaction ids at every run;
* fake will-executors and a fake chain (:class:`World`), installed with
  pytest's ``monkeypatch`` so nothing leaks into other tests;
* a fixed clock (:class:`Clock`), moved forward only by a scenario;
* :class:`OrderThread`, a stand-in for Electrum's ``TaskThread`` that runs
  the tasks one at a time, in order, with Electrum's callback order
  (``cb_done`` before ``cb_success`` / ``cb_error``, see
  ``electrum/gui/common_qt/util.py`` ``TaskThread.on_done``) and drops a
  task added after ``stop()``. The harness's ``SyncThread`` runs the
  callbacks the other way round;
* :class:`FlowWindow` / :class:`FlowDialog`: the real ``BalWindow`` and
  ``BalBuildWillDialog`` with only their Qt and network edges replaced.
  Everything else (phase 1, phase 2, the push and verify loops, the
  invalidation, the status half of ``update_all``) is the plugin's code.

Not a test module (no ``test_`` prefix): pytest does not collect it.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace

from electrum import constants
from electrum import util as electrum_util
from electrum.bip32 import BIP32Node
from electrum.segwit_addr import encode_segwit_address
from electrum.simple_config import SimpleConfig
from electrum.transaction import Transaction, tx_from_any
from electrum.wallet import restore_wallet_from_text

from bal.core.heirs import Heirs
from bal.core.plugin_base import BalPlugin
from bal.core.willexecutors import Willexecutors
from bal.gui.qt import dialogs as dialogs_mod
from bal.gui.qt import window as window_mod
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.window import BalWindow

DAY = 86400

# Folder of the golden files; BAL_GOLDEN_UPDATE=1 rewrites them.
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden", "5e")

# The public label the test wallet's key is derived from (see the module
# docstring): a throw-away testnet key, the same at every run.
_TEST_KEY_LABEL = "safe21-heartbeat flowkit test wallet (testnet, no funds)"


def ts(y, m, d, hour=0):
    """UTC timestamp of a calendar day."""
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def fmt(t):
    """``YYYY-MM-DD`` of a timestamp, "-" for None."""
    if t is None:
        return "-"
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d")


def address(n):
    """A valid testnet P2WPKH address number ``n``; nobody has its key."""
    return encode_segwit_address("tb", 0, bytes([n]) * 20)


WE_ADDR = [address(1), address(2), address(3)]
HEIR_ADDR = {"alice": address(10), "bob": address(11), "carol": address(12)}

# "Today" for every scenario, so dates and texts never change between runs.
FIXED_NOW = ts(2026, 10, 10, 12)
D0 = ts(2032, 9, 23)
LATER = ts(2033, 9, 23)
EARLIER = ts(2031, 9, 23)
PAST = ts(2020, 1, 1)


# ---------------------------------------------------------------- clock

class Clock:
    """The plugin's clock during a scenario: fixed, moved only by travel()."""

    def __init__(self):
        self.now = FIXED_NOW

    def travel(self, days):
        """Move the clock ``days`` forward."""
        self.now += int(days * DAY)

    def time(self):
        """Replacement for ``time.time``."""
        return float(self.now)


def _datetime_class(clock):
    """A ``datetime`` subclass whose ``now()`` reads ``clock``."""

    class ClockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime.fromtimestamp(clock.now, tz=timezone.utc)
            if tz is None:
                # A naive "now" in UTC: the tests do not depend on the
                # machine's time zone.
                return moment.replace(tzinfo=None)
            return moment.astimezone(tz)

    return ClockDatetime


# The modules whose module-level ``datetime`` name is replaced.
_CLOCK_MODULES = (
    "bal.core.checkalive",
    "bal.core.messages",
    "bal.core.plugin_base",
    "bal.core.report",
    "bal.core.reminders",
    "bal.core.summary",
    "bal.core.util",
    "bal.core.will",
    "bal.core.willexecutors",
)


def install_clock(monkeypatch, clock):
    """Point every plugin clock read used by the flows at ``clock``.

    ``time`` is replaced only inside ``window`` and ``dialogs`` (their
    ``time.time`` / ``time.sleep`` / ``time.strftime``), never globally:
    Electrum's own threads keep the real clock. ``sleep`` returns at once
    (the 10 s pause after an invalidation), ``strftime`` gives a fixed
    value (the log row times).
    """
    import importlib

    fake_dt = _datetime_class(clock)
    for name in _CLOCK_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "datetime", fake_dt)
    fake_time = SimpleNamespace(
        time=clock.time,
        sleep=lambda secs: None,
        strftime=lambda fmt_, *a: "00:00:00",
        monotonic=clock.time,
    )
    monkeypatch.setattr(window_mod, "time", fake_time)
    monkeypatch.setattr(dialogs_mod, "time", fake_time)


# ---------------------------------------------------------------- world

class World:
    """Everything outside the plugin: the chain and the will-executors."""

    def __init__(self):
        self.pushed = []          # (url, txid, raw)
        self.broadcast = {}       # txid -> raw (funding + invalidations)
        self.broadcast_log = []   # (kind, txid)
        self.down = set()         # executors that do not answer (#93)
        self.already = set()      # executors that answer "already present"
        # True: a broadcast reaches the network but the wallet does not see
        # it yet (Electrum has not registered it).
        self.wallet_lags = False
        self.verified = []        # (url, txid) asked by the verification
        self.executors = {
            "https://we{}.test".format(i): {
                "url": "https://we{}.test".format(i),
                "selected": True,
                "base_fee": 1000,
                "address": WE_ADDR[i],
                "status": "200",
                "info": "fake",
            }
            for i in range(2)
        }

    def add_executor(self):
        """A third will-executor, selected."""
        self.executors["https://we2.test"] = {
            "url": "https://we2.test", "selected": True, "base_fee": 1000,
            "address": WE_ADDR[2], "status": "200", "info": "fake"}

    def record_broadcast(self, wallet, tx, kind):
        """A transaction reaches the network: mempool, seen by the wallet."""
        raw = str(tx)
        self.broadcast[tx.txid()] = raw
        self.broadcast_log.append((kind, tx.txid()))
        if self.wallet_lags:
            return
        wallet.adb.add_transaction(tx_from_any(raw))
        # In the mempool (height 0), NOT wallet-local: Electrum's
        # adb.get_spender ignores local spenders.
        wallet.adb.add_unverified_or_unconfirmed_tx(tx.txid(), 0)

    # Fakes of the Willexecutors network calls --------------------------

    def get_willexecutors(self, bal_plugin, update=False, bal_window=None,
                          force=False, task=True):
        """``Willexecutors.get_willexecutors``: the world's list."""
        return self.executors

    def push_transactions_parallel(self, willexecutors, *, on_each=None,
                                   on_timeout=None, on_tick=None, **kw):
        """``Willexecutors.push_transactions_parallel``: record the push."""
        for url, we in willexecutors.items():
            if url in self.down:
                if on_timeout:
                    on_timeout(url, we)
                continue
            # Not strict: the plugin ends "txs" with a newline, so the split
            # gives one empty line more than there are transaction ids.
            for line, wid in zip(we["txs"].split("\n"), we["txsids"], strict=False):
                self.pushed.append((url, wid, line.strip()))
            if on_each:
                if url in self.already:
                    on_each(url, we, False, Willexecutors.AlreadyPresentException())
                else:
                    on_each(url, we, True, None)

    def check_transaction(self, txid, url, **kw):
        """``Willexecutors.check_transaction``: what the executor holds."""
        self.verified.append((url, txid))
        if url in self.down:
            raise ConnectionError("executor down (flowkit)")
        for u, wid, raw in reversed(self.pushed):
            if u == url and wid == txid:
                return {"tx": raw}
        return None

    def check_transactions_parallel(self, items, *, on_each=None,
                                    on_timeout=None, on_tick=None, **kw):
        """``Willexecutors.check_transactions_parallel``, in order, no threads.

        The real one asks every executor from a thread pool; the answers
        are the same, but their order would change from run to run.
        """
        for wid, url in items:
            if url in self.down:
                if on_timeout:
                    on_timeout(wid, url)
                continue
            try:
                res = self.check_transaction(wid, url)
            except Exception as exc:  # noqa: BLE001 - recorded like the real one
                if on_each:
                    on_each(wid, url, None, exc)
                continue
            if on_each:
                on_each(wid, url, res, None)


def install_world(monkeypatch, world):
    """Replace the will-executors' network calls by ``world``'s fakes."""
    monkeypatch.setattr(Willexecutors, "get_willexecutors",
                        staticmethod(world.get_willexecutors))
    monkeypatch.setattr(Willexecutors, "push_transactions_parallel",
                        staticmethod(world.push_transactions_parallel))
    monkeypatch.setattr(Willexecutors, "check_transaction",
                        staticmethod(world.check_transaction))
    monkeypatch.setattr(Willexecutors, "check_transactions_parallel",
                        staticmethod(world.check_transactions_parallel))


class _Broadcast:
    """What ``FakeNetwork.broadcast_transaction`` returns instead of a coroutine."""

    def __init__(self, network, tx):
        self.network = network
        self.tx = tx


class FakeNetwork:
    """The dialog's ``self.network`` (only what the invalidation uses)."""

    def __init__(self, world, wallet):
        self.world = world
        self.wallet = wallet

    def broadcast_transaction(self, tx, timeout=None):
        """Return a marker; the broadcast happens in run_from_another_thread."""
        return _Broadcast(self, tx)

    def run_from_another_thread(self, coro, timeout=None):
        """Broadcast a marker; close any other coroutine without running it.

        The other coroutine is ``tx.add_info_from_network``, which would
        ask a server for the input transactions: offline there is none.
        """
        if isinstance(coro, _Broadcast):
            self.world.record_broadcast(self.wallet, coro.tx, "invalidation(dialog)")
            return None
        coro.close()
        return None


# ---------------------------------------------------------------- threads

class OrderThread:
    """Electrum's ``TaskThread`` without a thread (see the module docstring).

    ``add`` only queues the task; :func:`run_until_idle` runs the queue.
    Like the real one, a callback that adds a task returns before that task
    runs.
    """

    queue = deque()

    def __init__(self, parent=None):
        self._stopping = False
        self.finished = SimpleNamespace(connect=lambda fn: None)

    def add(self, task, on_success=None, on_done=None, on_error=None, *, cancel=None):
        """Queue ``task``; ignored after stop(), as in Electrum."""
        if self._stopping:
            return
        OrderThread.queue.append((task, on_success, on_done, on_error))

    def stop(self):
        """Later add() calls are dropped."""
        self._stopping = True


def run_until_idle(limit=50):
    """Run the queued tasks, each followed by its callbacks (Electrum order).

    When the queue is empty, Qt's pending events run too: the dialog opens
    the classic invalidation window with ``QTimer.singleShot(0, ...)`` on the
    GUI thread, which this test thread is.
    """
    from PyQt6.QtCore import QCoreApplication

    for _ in range(limit):
        _run_queue(limit)
        QCoreApplication.processEvents()
        if not OrderThread.queue:
            return
    raise RuntimeError("flowkit: the dialog keeps adding tasks, a loop?")


def _run_queue(limit):
    """Run every queued task now (see run_until_idle)."""
    import sys

    count = 0
    while OrderThread.queue:
        count += 1
        if count > limit:
            raise RuntimeError("flowkit: more than {} tasks, a loop?".format(limit))
        task, on_success, on_done, on_error = OrderThread.queue.popleft()
        try:
            result = task()
        except BaseException:  # noqa: BLE001 - delivered like TaskThread
            exc_info = sys.exc_info()
            if on_done:
                on_done()
            if on_error:
                on_error(exc_info)
            continue
        if on_done:
            on_done()
        if on_success:
            on_success(result)


class _Signal:
    """A pyqtSignal stand-in: emit() does nothing (no widget to redraw)."""

    def emit(self, *a):
        pass

    def connect(self, *a):
        pass


class _Layout:
    """The dialog's ``vbox``: records the buttons the error handlers add."""

    def __init__(self, events):
        self.events = events

    def addWidget(self, widget):  # noqa: N802 - Qt name
        """Record the button's text."""
        text = widget.text() if hasattr(widget, "text") else type(widget).__name__
        self.events.append("button: " + text)

    def addLayout(self, layout):  # noqa: N802 - Qt name
        """A row of buttons."""
        self.events.append("layout")

    def removeItem(self, item):  # noqa: N802 - Qt name
        """A row removed (Retry)."""

    def sizeHint(self):  # noqa: N802 - Qt name
        """Any size: the window is not drawn."""
        from PyQt6.QtCore import QSize

        return QSize(0, 0)


# ---------------------------------------------------------------- user

class User:
    """The scripted owner: answers the questions, records them.

    ``cancel_on`` holds words: a question whose text contains one of them
    is answered "Cancel" / "Not now" / "No". Everything else is accepted
    (a wallet without a password: the password prompt answers None).
    """

    def __init__(self):
        self.cancel_on = set()
        self.asked = []

    def answer(self, kind, text):
        """Record the question; True to accept, False to cancel."""
        self.asked.append([kind, strip(text)])
        low = (kind + " " + str(text)).lower()
        return not any(word.lower() in low for word in self.cancel_on)


# ---------------------------------------------------------------- window

class FlowWindow(BalWindow):
    """The real ``BalWindow`` on a real wallet; only its Qt edges replaced.

    ``update_all`` is NOT replaced: its status half (``Will.add_willtree``
    + ``Will.check_invalidated``) is plugin logic and runs; its widget half
    fails on the missing widgets and is caught by the method itself, as it
    would be for a window without tabs.
    """

    def __init__(self, plugin, wallet, user, world):
        # BalWindow.__init__ builds the Qt tabs: set what it would set.
        self.bal_plugin = plugin
        self.window = SimpleNamespace(
            wallet=wallet,
            get_decimal_point=lambda: 8,
            format_amount_and_units=lambda sat: "{} sat".format(sat),
        )
        plugin.get_decimal_point = self.window.get_decimal_point
        self.wallet = wallet
        self.user = user
        self.world = world
        self.heirs = Heirs(wallet)
        self.will = wallet.db.get_dict("will")
        self.willitems = {}
        self.willexecutors = {}
        self.will_settings = plugin.WILL_SETTINGS.get()
        self.ok = True
        self.disable_plugin = False
        self.no_willexecutor = False
        self._menubar_initialized = True
        self._auto_rebuild_running = False
        self._auto_rebuild_cooldown_until = 0.0
        self.heir_list_widget = None
        self.will_list_widget = None
        self.popups = []
        self.events = []

    # Qt edges ----------------------------------------------------------

    def show_message(self, text):
        """A message box: recorded."""
        self.popups.append(["message", strip(text)])

    def show_warning(self, text, parent=None, title=None):
        """A warning box: recorded."""
        self.popups.append(["warning", strip(text)])

    def show_error(self, text):
        """An error box: recorded."""
        self.popups.append(["error", strip(text)])

    def show_critical(self, text):
        """A critical box: recorded."""
        self.popups.append(["critical", strip(text)])

    def _schedule_history_refresh(self):
        """Refreshes the History tab through a QTimer: recorded."""
        self.events.append("history-refresh")

    def set_will_summary_busy(self, busy):
        """The Will tab's summary box: no widget here."""

    def update_will_summary(self):
        """The Will tab's summary box: no widget here."""

    def ask_renew_will(self, text, parent=None):
        """The Check Alive "Renew / Not now" question (#79)."""
        return self.user.answer("renew", text)

    def get_wallet_password(self, message=None, parent=None):
        """The password window: None = accepted (no password), False = Cancel."""
        return None if self.user.answer("password", message or "") else False

    def invalidate_will(self, will=None):
        """Tools > Invalidate: Electrum's transaction window, signed and sent.

        The real method opens Electrum's transaction dialog, where the owner
        signs and broadcasts. Here the scripted owner does it (or closes the
        window when the question "classic invalidate" is cancelled).
        """
        from bal.core.will import Will

        self.popups.append(["classic-invalidate", "opened"])
        if not self.user.answer("classic invalidate", "transaction window"):
            return
        tx = Will.invalidate_will(
            self.willitems, self.wallet,
            self.will_settings.get("baltx_fees", 1),
            history_label=self.bal_plugin.HISTORY_LABEL.get(),
            will_locktime=Will.get_min_locktime(self.willitems),
        )
        if tx:
            self.wallet.sign_transaction(tx, None)
            self.wallet.set_label(tx.txid(), "Safe21 Heartbeat invalidation")
            self.world.record_broadcast(self.wallet, tx, "invalidation(classic)")


# ---------------------------------------------------------------- dialog

class FlowDialog(BalBuildWillDialog):
    """The real ``BalBuildWillDialog``, built without Qt.

    ``__init__`` is skipped (it creates widgets); the attributes it sets
    are set here with the same values. Only the edges that create widgets
    or open windows are replaced, and each one is recorded in ``events``.
    """

    def __init__(self, bal_window, renew=False, closing=False):
        # The attributes of BalBuildWillDialog.__init__ (dialogs.py),
        # without the widgets.
        self._closing = closing
        self._summary_window = bal_window
        self._renew_approved = renew
        self._check_alive_info = None
        self.bal_window = bal_window
        self.bal_plugin = bal_window.bal_plugin
        self.labels = []
        self.check_row = None
        self.inval_row = None
        self.build_row = None
        self.sign_row = None
        self.push_row = None
        self.verify_row = None
        self.wait_row = None
        self._next_steps_hint = None
        self._date_was_anticipated = False
        self._invalidation_broadcast = False
        self._postpone_invalidation = False
        self._unsigned_executors = None
        self.network = FakeNetwork(bal_window.world, bal_window.wallet)
        self._stopping = False
        self.thread = OrderThread(self)
        self.updatemessage = _Signal()
        self.events = []
        self.history = []
        # The error handlers add a real "Close" QPushButton to the layout.
        self.vbox = _Layout(self.events)
        # Read with getattr(self, name, default) by the dialog. On a real
        # dialog a missing attribute gives the default; on this one (Qt
        # __init__ not run) it would raise RuntimeError, so the defaults are
        # set here.
        self._close_button = None
        self._no_we_buttons_added = False
        self._no_heirs_buttons_added = False
        self._still_active_warning = None

    def build_will_task(self):
        """Queue phase 1 as the real one does; show_modal is not called."""
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )

    # Every row ever written (rows are overwritten in the dialog).
    def msg_edit_row(self, line, row=None):
        """Record the row, then write it as the real dialog does."""
        self.history.append(line)
        return super().msg_edit_row(line, row)

    # Qt edges ----------------------------------------------------------

    def msg_update(self):
        """Redraws the widgets: nothing to draw."""

    # The three button rows: the logic before the widgets (guard, report
    # actions, final report) is the real one, copied line by line; only the
    # widgets are replaced by an event.

    def _add_close_button(self):
        """The Close / Calendar / Will report row (dialogs.py)."""
        if getattr(self, "_close_button", None) is not None:
            return
        self._set_report_actions(True)
        self._finish_report()
        self._close_button = "close-button"
        self.events.append("close-button")

    def _add_no_willexecutor_buttons(self):
        """The Will-Executor / Wizard / Close row (dialogs.py)."""
        if getattr(self, "_no_we_buttons_added", False):
            return
        self._no_we_buttons_added = True
        self._finish_report()
        self.events.append("no-willexecutor-buttons")

    def _add_no_heirs_buttons(self):
        """The Heirs / Wizard / Close row (dialogs.py)."""
        if getattr(self, "_no_heirs_buttons_added", False):
            return
        self._no_heirs_buttons_added = True
        self._finish_report()
        self.events.append("no-heirs-buttons")

    def resize(self, *a):
        """Window size: nothing to resize."""

    def repaint(self):
        """Window paint: nothing to paint."""

    def close(self):
        """Closes the window."""
        self.events.append("dialog-closed")
        self._stopping = True
        return True

    def show_warning(self, text, *a, **kw):
        """A warning box of the dialog itself."""
        self.events.append("warning: " + strip(text))

    def show_message(self, text, *a, **kw):
        """A message box of the dialog itself."""
        self.events.append("message: " + strip(text))

    def question(self, text, *a, **kw):
        """A Yes/No box of the dialog (Exclude)."""
        return self.bal_window.user.answer("question", text)


# ---------------------------------------------------------------- session

_LOOP = {}


def ensure_event_loop():
    """Start Electrum's asyncio loop once (the wallet code expects one)."""
    if "loop" in _LOOP:
        return
    try:
        electrum_util.get_asyncio_loop()
        _LOOP["loop"] = None
        return
    except Exception:  # noqa: BLE001 - no loop yet
        pass
    loop, stopping_fut, loop_thread = electrum_util.create_and_start_event_loop()
    _LOOP.update(loop=loop, stopping_fut=stopping_fut, thread=loop_thread)


def stop_event_loop():
    """Stop the loop started by ensure_event_loop (its thread is not a daemon)."""
    loop = _LOOP.pop("loop", None)
    if loop is None:
        return
    loop.call_soon_threadsafe(_LOOP["stopping_fut"].set_result, 1)
    _LOOP.pop("thread").join(timeout=10)
    _LOOP.clear()


def _funding_tx(addr, value, tag):
    """A made-up mined transaction paying ``value`` to ``addr``."""
    from electrum.bitcoin import address_to_script

    spk = address_to_script(addr)
    raw = ("02000000" + "01" + tag * 32 + "00000000" + "00" + "ffffffff"
           + "01" + value.to_bytes(8, "little").hex()
           + bytes([len(spk)]).hex() + spk.hex() + "00000000")
    return Transaction(raw)


class Session:
    """One owner, one wallet, one world: the start of a scenario.

    Basic or advanced mode, one mined coin of 1,000,000 sat, two heirs
    (alice 60 %, bob 40 %), two will-executors, fee ``fee`` sat/vB,
    delivery ``locktime``, Check Alive 180 days before the delivery
    (advanced only; ``threshold``). Nothing is built yet: the first Check builds the will.
    """

    def __init__(self, monkeypatch, mode="basic", fee=13, locktime=D0,
                 threshold="-180d"):
        constants.net = constants.BitcoinTestnet
        ensure_event_loop()
        self.monkeypatch = monkeypatch
        self.clock = Clock()
        install_clock(monkeypatch, self.clock)
        monkeypatch.setattr(dialogs_mod, "TaskThread", OrderThread)
        OrderThread.queue.clear()
        self.world = World()
        install_world(monkeypatch, self.world)
        self.dir = tempfile.mkdtemp(prefix="bal_flowkit_")
        self.config = SimpleConfig(
            {"electrum_path": self.dir, "testnet": True, "offline": True},
            read_user_config_function=lambda path: {},
        )
        self.plugin = BalPlugin(SimpleNamespace(), self.config, "bal")
        self.plugin.USER_TYPE.set(mode)
        seed = hashlib.sha256(_TEST_KEY_LABEL.encode()).digest()
        xprv = BIP32Node.from_rootseed(seed, xtype="p2wpkh").to_xprv(
            net=constants.BitcoinTestnet)
        self.wallet = restore_wallet_from_text(
            xprv, path=os.path.join(self.dir, "w"), config=self.config,
        )["wallet"]
        self._coin_tags = iter(["cd", "ef", "a1", "a2", "a3"])
        self._add_coin(1_000_000, "ab")
        ws = self.plugin.WILL_SETTINGS.get()
        ws["baltx_fees"] = fee
        ws["locktime"] = locktime
        ws["threshold"] = threshold
        self.plugin.WILL_SETTINGS.set(ws)
        self.user = User()
        self.bw = FlowWindow(self.plugin, self.wallet, self.user, self.world)
        self.bw.heirs["alice"] = [HEIR_ADDR["alice"], "60%", locktime]
        self.bw.heirs["bob"] = [HEIR_ADDR["bob"], "40%", locktime]
        self.recorder = Recorder(self)
        self.last = None

    def close(self):
        """Remove the temporary wallet folder."""
        shutil.rmtree(self.dir, ignore_errors=True)

    # The owner's actions ------------------------------------------------

    def _add_coin(self, value, tag):
        addr = self.wallet.get_unused_address() or self.wallet.get_receiving_addresses()[-1]
        fund = _funding_tx(addr, value, tag)
        self.world.broadcast[fund.txid()] = str(fund)
        self.wallet.adb.add_transaction(fund)
        # Mined (height 100), not SPV-verified offline.
        self.wallet.adb.add_unverified_or_unconfirmed_tx(fund.txid(), 100)

    def add_coin(self, value=250_000):
        """Receive one more mined coin."""
        self._add_coin(value, next(self._coin_tags))

    def set_date(self, t):
        """Change the delivery date in the Will tab."""
        self.bw.update_setting_widgets(t, "locktime")

    def set_fee(self, f):
        """Change the transaction fee in the Will tab."""
        self.bw.update_setting_widgets(f, "baltx_fees")

    def set_heirs(self, **shares):
        """Replace the heirs: ``alice="40%"`` (dates = the will's date)."""
        for name in list(self.bw.heirs.keys()):
            if name not in shares:
                del self.bw.heirs[name]
        for name, amount in shares.items():
            self.bw.heirs[name] = [HEIR_ADDR[name], amount,
                                   self.bw.will_settings["locktime"]]

    def check(self, cancel_on=(), renew=False, closing=False, until_idle=True):
        """Press "Check your will" and let the dialog run to its end."""
        pushed_before = self._begin(cancel_on)
        dlg = FlowDialog(self.bw, renew=renew, closing=closing)
        dlg.build_will_task()
        if until_idle:
            run_until_idle()
        self.last = dlg
        self.recorder.record(dlg, pushed_before)
        return dlg

    def redo(self, action, cancel_on=()):
        """A button of the last dialog (Retry, Exclude): run it and record.

        ``action(dialog)`` presses the button; the dialog's history and
        events start again from empty so the record shows only this run.
        """
        pushed_before = self._begin(cancel_on)
        dlg = self.last
        dlg.history = []
        dlg.events = []
        action(dlg)
        run_until_idle()
        self.recorder.record(dlg, pushed_before)
        return dlg

    def _begin(self, cancel_on):
        """Clear the per-Check records; return the number of pushes so far."""
        self.user.cancel_on = set(cancel_on)
        self.user.asked = []
        self.bw.popups = []
        self.bw.events = []
        self.world.broadcast_log = []
        self.world.verified = []
        return len(self.world.pushed)


# ---------------------------------------------------------------- recording

_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def strip(text):
    """Text without HTML tags, for popups and questions."""
    return re.sub(r"<[^>]+>", "", str(text)).replace("\t", " ").strip()


class Recorder:
    """Turns what happened in a Check into JSON-friendly data.

    Transaction ids become ``T1``, ``T2``... in order of first appearance
    in the scenario: the ids are stable (fixed key), the short names keep
    the golden files readable.
    """

    def __init__(self, session):
        self.session = session
        self.names = {}
        self.steps = []

    def name(self, txid):
        """The short name of a transaction id."""
        if txid not in self.names:
            self.names[txid] = "T{}".format(len(self.names) + 1)
        return self.names[txid]

    def text(self, value):
        """Replace every transaction id inside ``value`` by its short name."""
        if isinstance(value, str):
            return _HEX64.sub(lambda m: self.name(m.group(0)), value)
        if isinstance(value, (list, tuple)):
            return [self.text(v) for v in value]
        if isinstance(value, dict):
            return {self.text(str(k)): self.text(v) for k, v in value.items()}
        return value

    def note(self, label):
        """An owner action between two Checks (kept in the golden file)."""
        self.steps.append({"action": label})

    def items(self):
        """Every will item: name, date, fee, executor, statuses set."""
        out = []
        for wid, w in self.session.bw.willitems.items():
            statuses = sorted(k for k, v in w.STATUS.items() if v[1]) \
                if hasattr(w, "STATUS") else []
            out.append({
                "tx": self.name(wid),
                "locktime": fmt(w.tx.locktime),
                "fee": w.tx_fees,
                "executor": (w.we or {}).get("url"),
                "statuses": statuses,
                "label": self.session.wallet.get_label_for_txid(wid) or "",
            })
        return out

    def record(self, dlg, pushed_before):
        """Add one Check to the scenario's steps."""
        s = self.session
        report = dlg._report().snapshot()
        ws = s.bw.will_settings
        step = {
            "action": "check",
            "rows": self.text(dlg.labels),
            "history": self.text(list(dlg.history)),
            "report": self.text(json.loads(json.dumps(report, default=list))),
            "dialog_events": self.text(dlg.events),
            "window_events": self.text(s.bw.events),
            "popups": self.text(s.bw.popups),
            "questions": self.text(s.user.asked),
            "broadcasts": [[k, self.name(t)] for k, t in s.world.broadcast_log],
            "pushed": [[u, self.name(t)] for u, t, _raw in s.world.pushed[pushed_before:]],
            "verified": [[u, self.name(t)] for u, t in s.world.verified],
            "items": self.items(),
            "settings": {
                "locktime": fmt(ws.get("locktime")) if isinstance(ws.get("locktime"), int)
                else ws.get("locktime"),
                "threshold": ws.get("threshold"),
                "baltx_fees": ws.get("baltx_fees"),
            },
            "heirs": {k: list(v[:2]) for k, v in s.bw.heirs.items()},
        }
        self.steps.append(step)


# ---------------------------------------------------------------- golden

def check_golden(name, data):
    """Compare ``data`` with ``golden/5e/<name>.json``.

    With ``BAL_GOLDEN_UPDATE=1`` the file is (re)written instead. A golden
    file changes only together with a CHANGELOG line that says why.
    """
    path = os.path.join(GOLDEN_DIR, name + ".json")
    text = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get("BAL_GOLDEN_UPDATE") == "1":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return
    assert os.path.exists(path), (
        "missing golden file {} (run once with BAL_GOLDEN_UPDATE=1)".format(path))
    with open(path, encoding="utf-8") as f:
        expected = f.read()
    if expected != text:
        # A readable diff in the failure message.
        import difflib

        diff = "".join(difflib.unified_diff(
            expected.splitlines(True), text.splitlines(True),
            "golden/" + name, "now", n=2))
        raise AssertionError("golden {} changed:\n{}".format(name, diff[:6000]))


__all__ = [
    "D0", "DAY", "EARLIER", "FIXED_NOW", "HEIR_ADDR", "LATER", "PAST",
    "Clock", "FakeNetwork", "FlowDialog", "FlowWindow", "OrderThread",
    "Recorder", "Session", "User", "World", "check_golden",
    "ensure_event_loop", "fmt", "run_until_idle", "stop_event_loop",
    "strip", "ts",
]
