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

The delivery settings (CHANGELOG #123) live in the wallet, under the wallet
key ``will_settings``: :class:`Session` seeds that dict (``settings_version``
1) unless it is asked to start from the old shared value
(``legacy_global=`` / ``legacy_wallet=``, or a period as ``locktime``), and
the window loads it with ``bal.engine.context.load_will_settings``, as the
real window does. Electrum's global ``bal_will_settings`` must never change:
the :class:`Recorder` checks it at every step (rule I1).

Not a test module (no ``test_`` prefix): pytest does not collect it.
"""

import hashlib
import itertools
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

from bal.core import delivery
from bal.core.heirs import Heirs
from bal.core.plugin_base import BalPlugin
from bal.core.will import WillItem
from bal.core.willexecutors import Willexecutors
from bal.engine import build as engine_build
from bal.engine import context as engine_context
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

    ``time`` is replaced only inside ``window``, ``dialogs``,
    ``bal.engine.context``, ``bal.engine.build`` and the command line's
    ``bal.cli.controller`` (their ``time.time`` / ``time.sleep`` /
    ``time.strftime``), never globally: Electrum's own threads keep the real
    clock. ``sleep`` returns at once (the 10 s pause after an invalidation),
    ``strftime`` gives a fixed value (the log row times). The engine's
    ``refresh_invalidated_items`` is the window's former
    ``_refresh_invalidated_items`` (CHANGELOG #118) and ``build_will`` its
    former ``build_will`` with the creation time of the items (CHANGELOG
    #119), so they read the same clock. (The command line's builds now get
    the scenario clock too; before #119 they read the real one.) The
    delivery settings (#123): ``bal.engine.context`` reads "now" for the
    migration, the controller for ``bal_delivery_set``;
    ``bal.core.delivery`` never reads the clock.
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
    monkeypatch.setattr(engine_context, "time", fake_time)
    monkeypatch.setattr(engine_build, "time", fake_time)
    controller = importlib.import_module("bal.cli.controller")
    if hasattr(controller, "time"):
        monkeypatch.setattr(controller, "time", fake_time)


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

    def __init__(self, world, wallet, kind="invalidation(dialog)"):
        self.world = world
        self.wallet = wallet
        self.kind = kind
        # No fee estimate received yet: the invalidation pays the will's
        # fee rate (Will.invalidation_fee_rate falls back to it).
        self.fee_estimates = SimpleNamespace(eta_target_to_fee=lambda blocks: None)

    def blockchain(self):
        """A stale chain tip: transactions get locktime 0 (Util.get_current_height)."""
        return SimpleNamespace(is_tip_stale=lambda: True, height=lambda: 0)

    def get_server_height(self):
        """No server height (not used with a stale tip)."""
        return 0

    def broadcast_transaction(self, tx, timeout=None):
        """Return a marker; the broadcast happens in run_from_another_thread."""
        return _Broadcast(self, tx)

    def run_from_another_thread(self, coro, timeout=None):
        """Broadcast a marker; close any other coroutine without running it.

        The other coroutine is ``tx.add_info_from_network``, which would
        ask a server for the input transactions: offline there is none.
        """
        if isinstance(coro, _Broadcast):
            self.world.record_broadcast(self.wallet, coro.tx, self.kind)
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
    is answered "Cancel" / "Not now" / "No". Everything else is accepted.
    ``password`` is what the owner types in Electrum's password window
    (only a wallet with a password opens it, as in Electrum).
    """

    def __init__(self, password=None):
        """``password``: what the owner types in the password window."""
        self.cancel_on = set()
        self.asked = []
        self.password = password
        # The answer to the "which date?" question (#123): "keep" (the
        # default button) or "later". Cancel through ``cancel_on``.
        self.delivery_answer = "keep"

    def answer(self, kind, text):
        """Record the question; True to accept, False to cancel."""
        self.asked.append([kind, strip(text)])
        low = (kind + " " + str(text)).lower()
        return not any(word.lower() in low for word in self.cancel_on)

    def password_dialog(self, msg=None, parent=None):
        """Electrum's password window (``Plugin.password_dialog``).

        Returns the typed password, or None for Cancel, like the real one.
        """
        if self.answer("password", msg or ""):
            return self.password
        return None


class FakeWaitingDialog:
    """``BalWaitingDialog`` without Qt (the "Please wait" window).

    Same contract as the real one (dialogs.py): the task runs on the task
    thread, then ``accept`` closes the window (which sets ``_stopping``,
    through hideEvent) BEFORE ``on_success`` / ``on_error`` run, as
    Electrum's ``TaskThread`` does; ``exe()`` returns only when the window
    is closed (the real one runs a modal ``exec()``). The title and every
    ``update`` text are recorded in the window's events.
    """

    def __init__(self, bal_window, message, task, on_success=None,
                 on_error=None, on_cancel=None, exe=True):
        self.bal_window = bal_window
        self.task = task
        self.on_success = on_success
        self.on_error = on_error
        self.on_cancel = on_cancel
        self._stopping = False
        bal_window.events.append("waiting window: " + strip(message))
        if exe:
            self.exe()

    def exe(self):
        """Run the task to its end (modal window)."""
        OrderThread(self).add(self.task, self.on_success, self.accept, self.on_error)
        run_until_idle()

    def accept(self):
        """The window closes: hideEvent sets ``_stopping``."""
        self._stopping = True

    def update(self, msg):
        """A new text in the window."""
        self.bal_window.events.append("waiting window text: " + strip(msg))


class _ListWidget:
    """The Heirs / Will list of the window: refreshes do nothing.

    No ``will_settings_widget``: the toolbar fields are not drawn, so
    ``update_widget_value`` fails on it and is caught, as on a list without
    a toolbar.
    """

    def update(self, *a):
        """Redraw the list."""

    def update_will(self, willitems):
        """New will items for the list."""


# ---------------------------------------------------------------- window

class FlowWindow(BalWindow):
    """The real ``BalWindow`` on a real wallet; only its Qt edges replaced.

    ``update_all`` is NOT replaced: its status half (``Will.add_willtree``
    + ``Will.check_invalidated``) is plugin logic and runs; its widget half
    fails on the missing widgets and is caught by the method itself, as it
    would be for a window without tabs.

    The delivery settings are loaded as ``BalWindow.__init__`` loads them
    (CHANGELOG #123): the heirs first, then
    ``bal.engine.context.load_will_settings`` with the one-second flow lock
    (the one-time migration and the anchor check run here).
    """

    def __init__(self, plugin, wallet, user, world):
        """Set what ``BalWindow.__init__`` sets, without the Qt tabs."""
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
        self.will_settings = None
        self.settings_unavailable = False
        self._settings_ready = engine_context.load_will_settings(
            self, lock_timeout=1.0
        )
        self.ok = True
        self.disable_plugin = False
        self.no_willexecutor = False
        self._menubar_initialized = True
        self._auto_rebuild_running = False
        self._auto_rebuild_cooldown_until = 0.0
        # The Heirs and Will lists exist in a real window: the flows refresh
        # them (and would stop with an error on None).
        self.heir_list_widget = _ListWidget()
        self.will_list_widget = _ListWidget()
        self.popups = []
        self.events = []
        # "Check your will" dialogs opened by the window itself (renewal
        # from Prepare, the check at wallet close): see Session.
        self.opened_dialogs = []

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

    def ask_delivery_choice(self, parent=None):
        """The "which date?" question (#123): the scripted owner answers.

        The real texts are recorded (``BalWindow.delivery_choice_texts``);
        the answer is ``user.delivery_answer`` ("keep" or "later"), or
        Cancel (None) when the question matches ``user.cancel_on``. Without
        both dates the real method decides (no box is shown then).
        """
        dates = delivery.choice_dates(self.will_settings)
        if dates is None or None in dates:
            return BalWindow.ask_delivery_choice(self, parent)
        text, keep_text, later_text = self.delivery_choice_texts(*dates)
        if not self.user.answer(
                "delivery date", " | ".join((text, keep_text, later_text))):
            return None
        return self.user.delivery_answer

    def show_transaction(self, tx=None, txid=None, parent=None):
        """Electrum's transaction window (the classic invalidation).

        The owner signs and broadcasts there; the scripted owner does it,
        or closes the window when "transaction window" is cancelled. The
        rest of ``invalidate_will`` (build, label, message) is the real one.
        """
        if txid is not None and txid in self.willitems:
            tx = self.willitems[txid].tx
        self.popups.append(["transaction window", "opened"])
        if not self.user.answer("transaction window", "sign and broadcast"):
            return None
        self.wallet.sign_transaction(tx, self.user.password)
        self.world.record_broadcast(self.wallet, tx, "invalidation(transaction window)")
        return None


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
        # #135: the invalidation step (box, "Invalidate now").
        self._pending_invalidation = None
        self._invalidation_layout = None
        self._invalidation_tx = None
        self._renames_seen = None

    def build_will_task(self):
        """Queue phase 1 as the real one does, then run it to the end.

        The real one then calls ``show_modal``: the caller (wallet close,
        renewal, Check button) waits until the dialog is closed.
        """
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )
        run_until_idle()

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

    def _add_invalidation_buttons(self):
        """The "Cancel" / "Invalidate now" row (dialogs.py, #135): the
        scripted owner presses "Invalidate now", or Cancel when a
        ``cancel_on`` word matches (e.g. "invalidat"); the rest of each
        path is the real one."""
        self.events.append("invalidation-buttons")
        if self.bal_window.user.answer("button", "Invalidate now"):
            self._invalidate_now()
        else:
            self._invalidation_cancelled()

    def _remove_invalidation_buttons(self):
        """The row goes away: nothing to remove."""
        self._invalidation_layout = None

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


def _make_wallet(config, path, password, label):
    """A testnet wallet at ``path`` whose key is derived from ``label``.

    ``label`` is public (no seed or key is stored anywhere); a different
    label gives a different wallet. ``password``: a test wallet with a
    password (the only kind that opens Electrum's password window, so the
    only one where Cancel exists). Test value, not a real password.
    """
    seed = hashlib.sha256(label.encode()).digest()
    xprv = BIP32Node.from_rootseed(seed, xtype="p2wpkh").to_xprv(
        net=constants.BitcoinTestnet)
    return restore_wallet_from_text(
        xprv, path=path, config=config, password=password,
    )["wallet"]


# Numbers of the wallets made by Session.second_wallet (own file, own key).
_MORE_WALLETS = itertools.count(2)


def _is_period(value):
    """True for an old period recipe such as ``"60d"`` or ``"1y"``."""
    return isinstance(value, str) and delivery.as_delivery(value) is None


class Session:
    """One owner, one wallet, one world: the start of a scenario.

    Basic or advanced mode, one mined coin of 1,000,000 sat, two heirs
    (alice 60 %, bob 40 %), two will-executors, fee ``fee`` sat/vB,
    delivery ``locktime``, Check Alive 180 days before the delivery
    (advanced only; ``threshold``). Nothing is built yet: the first Check builds the will.

    The delivery settings (CHANGELOG #123) are the wallet's own. By
    default the wallet dict is seeded with ``settings_version`` 1, the date,
    the fee and the Check Alive: the window finds them current and migrates
    nothing. To start from the time before #123 instead (the window then
    migrates, as when an owner installs the new version):

    * ``legacy_global``: Electrum's global ``bal_will_settings`` as an older
      plugin left it (a dict, written raw);
    * ``legacy_wallet``: the wallet's own ``will_settings`` as found (a BAL
      0.2.x dict, no ``settings_version``);
    * a period as ``locktime`` (``"60d"``): it can only be an old shared
      value, so it becomes ``legacy_global`` (fee, period, Check Alive).

    ``period_months`` (CHANGELOG #127): the wallet is seeded in the period
    form of N calendar months, as the wizard leaves it before the first
    will (``period_start`` today, ``locktime`` today + N months; the
    ``locktime`` argument is not used then).

    On that path the heirs are in the wallet before the window opens (a
    wallet used before the update), so the old values may be migrated.
    The global is never written by the plugin: :class:`Recorder` checks it
    at every step (:meth:`snapshot_global`).
    """

    def __init__(self, monkeypatch, mode="basic", fee=13, locktime=D0,
                 threshold="-180d", password=None, legacy_global=None,
                 legacy_wallet=None, period_months=None):
        """Build the world, the wallet and its window (see the class)."""
        constants.net = constants.BitcoinTestnet
        ensure_event_loop()
        self.monkeypatch = monkeypatch
        self.clock = Clock()
        install_clock(monkeypatch, self.clock)
        monkeypatch.setattr(dialogs_mod, "TaskThread", OrderThread)
        monkeypatch.setattr(window_mod, "BalWaitingDialog", FakeWaitingDialog)
        monkeypatch.setattr(window_mod, "BalBuildWillDialog", self._open_dialog)
        OrderThread.queue.clear()
        self.world = World()
        install_world(monkeypatch, self.world)
        self.dir = tempfile.mkdtemp(prefix="bal_flowkit_")
        self._owns_dir = True
        self.config = SimpleConfig(
            {"electrum_path": self.dir, "testnet": True, "offline": True},
            read_user_config_function=lambda path: {},
        )
        self.plugin = BalPlugin(SimpleNamespace(), self.config, "bal")
        self.plugin.USER_TYPE.set(mode)
        self.wallet = _make_wallet(
            self.config, os.path.join(self.dir, "w"), password, _TEST_KEY_LABEL)
        self._coin_tags = iter(["cd", "ef", "a1", "a2", "a3"])
        self._add_coin(1_000_000, "ab")
        if legacy_global is None and legacy_wallet is None and _is_period(locktime):
            legacy_global = {
                "baltx_fees": fee, "locktime": locktime, "threshold": threshold}
        if legacy_global is not None:
            # BAL's key as an older plugin left it (Safe21 never writes it).
            self.config.set_key(self.plugin.WILL_SETTINGS.name, legacy_global)
        self.snapshot_global()
        self.user = User(password)
        # Electrum's password window lives on the Qt Plugin object.
        self.plugin.password_dialog = self.user.password_dialog
        legacy = legacy_global is not None or legacy_wallet is not None
        self._open_window(locktime, fee, threshold, seed=not legacy,
                          legacy_wallet=legacy_wallet, plugin_data=True,
                          period_months=period_months)
        self.recorder = Recorder(self)
        self.last = None

    def _open_window(self, locktime, fee, threshold, seed, legacy_wallet,
                     plugin_data, period_months=None):
        """Seed the wallet's delivery settings and open its window.

        ``seed``: the wallet dict gets ``settings_version`` 1 with
        ``locktime`` / ``fee`` / ``threshold`` (written item by item, as the
        plugin does). Otherwise ``legacy_wallet`` (if any) is written as
        found and the window migrates. ``plugin_data`` (only without
        ``seed``): the heirs are written before the window opens, so the
        wallet counts as one used before the update; without it they come
        after (a wallet created after the update). ``period_months`` (only
        with ``seed``): the period form of #127, see :class:`Session`.
        """
        if seed and period_months is not None:
            dates = delivery.period_values(self.clock.now, period_months)
            locktime = dates["locktime"]
        heirs = {
            "alice": [HEIR_ADDR["alice"], "60%", locktime],
            "bob": [HEIR_ADDR["bob"], "40%", locktime],
        }
        settings = self.wallet.db.get_dict(delivery.WALLET_KEY)
        if seed:
            values = {"settings_version": delivery.SCHEMA_VERSION,
                      "locktime": locktime, "baltx_fees": fee,
                      "threshold": threshold}
            if period_months is not None:
                values.update(form=delivery.FORM_PERIOD,
                              period_months=period_months,
                              period_start=dates["period_start"])
        else:
            values = dict(legacy_wallet or {})
        for key, value in values.items():
            settings[key] = value
        heirs_first = plugin_data and not seed
        if heirs_first:
            stored = Heirs(self.wallet)
            for name, heir in heirs.items():
                stored[name] = heir
        self.bw = FlowWindow(self.plugin, self.wallet, self.user, self.world)
        if not heirs_first:
            for name, heir in heirs.items():
                self.bw.heirs[name] = heir

    def close(self):
        """Remove the temporary wallet folder (a second wallet shares it)."""
        if getattr(self, "_owns_dir", True):
            shutil.rmtree(self.dir, ignore_errors=True)

    # Electrum's global settings (rule I1, CHANGELOG #123) -----------------

    def global_text(self):
        """Electrum's global ``bal_will_settings`` as JSON (None: missing)."""
        return json.dumps(self.config.get(self.plugin.WILL_SETTINGS.name))

    def snapshot_global(self):
        """Take the global as it is now as the reference of rule I1.

        Called at the start; a test that changes the global on purpose (as
        BAL or an older plugin would) calls it again afterwards.
        """
        self._global_before = self.global_text()

    def assert_global_unchanged(self):
        """Rule I1: the global ``bal_will_settings`` is byte-identical."""
        now = self.global_text()
        assert now == self._global_before, (
            "rule I1: Electrum's global bal_will_settings changed: "
            "{} -> {}".format(self._global_before, now))

    # More wallets in the same Electrum (CHANGELOG #123) -------------------

    def second_wallet(self, locktime=D0, fee=13, threshold="-180d",
                      password=None, seed=True, plugin_data=True,
                      legacy_wallet=None, period_months=None):
        """Another wallet in the same Electrum: same config (so the same
        global), plugin, clock and will-executors; its own key, coin, heirs,
        owner and window. Returns its :class:`Session`.

        ``seed`` / ``legacy_wallet`` / ``plugin_data`` / ``period_months``:
        see :meth:`_open_window`. ``seed=False, plugin_data=False`` is a wallet
        created after the update: it gets the default date, never the old
        shared value. Call :meth:`activate` before acting on a wallet when
        both have a password.
        """
        other = object.__new__(Session)
        other.monkeypatch = self.monkeypatch
        other.clock = self.clock
        other.world = self.world
        other.dir = self.dir
        other._owns_dir = False
        other.config = self.config
        other.plugin = self.plugin
        number = next(_MORE_WALLETS)
        other.wallet = _make_wallet(
            self.config, os.path.join(self.dir, "w{}".format(number)),
            password, "{} (wallet {})".format(_TEST_KEY_LABEL, number))
        other._coin_tags = iter(["c2", "e2", "b1", "b2", "b3"])
        other._add_coin(1_000_000, "ba")
        other._global_before = self._global_before
        other.user = User(password)
        other.activate()
        other._open_window(locktime, fee, threshold, seed=seed,
                           legacy_wallet=legacy_wallet, plugin_data=plugin_data,
                           period_months=period_months)
        other.recorder = Recorder(other)
        other.last = None
        return other

    def activate(self):
        """Point the shared plugin's password window at this wallet's owner."""
        self.plugin.password_dialog = self.user.password_dialog

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
        """Change the delivery date in the Will tab.

        Through the one validated write (``context.set_will_setting``,
        CHANGELOG #123), as the window's ``update_setting_widgets`` does; a
        value it refuses raises here (``ValueError``: a period such as
        ``"1054d"`` is no longer a delivery date), so a test cannot store
        it by mistake. A past date is a date and is stored.
        """
        engine_context.set_will_setting(self.bw, "locktime", t)

    def set_fee(self, f):
        """Change the transaction fee in the Will tab (validated, see
        :meth:`set_date`: a fee below 1 raises ``ValueError``)."""
        engine_context.set_will_setting(self.bw, "baltx_fees", f)

    def set_threshold(self, value):
        """Change the Check Alive (ADVANCED; ``"-Nd"``, validated, see
        :meth:`set_date`)."""
        engine_context.set_will_setting(self.bw, "threshold", value)

    def show_note(self):
        """The one-time note of the delivery settings, as ``load_wallet``
        shows it two seconds after the wallet opens (CHANGELOG #123)."""
        return self.act("gui delivery note", self.bw.show_delivery_note)

    def set_heirs(self, **shares):
        """Replace the heirs: ``alice="40%"`` (dates = the will's date)."""
        for name in list(self.bw.heirs.keys()):
            if name not in shares:
                del self.bw.heirs[name]
        # As BalWindow.delete_heirs: ``del`` alone does not reach the wallet.
        self.bw.heirs.save()
        for name, amount in shares.items():
            self.bw.heirs[name] = [HEIR_ADDR[name], amount,
                                   self.bw.will_settings["locktime"]]

    def set_instalments(self, *rows):
        """Store a table of deliveries (#168): ``(date, {name: share})``
        rows, through the engine's one validated write
        (``context.set_instalments``: 2-3 dates, 28 days apart, shares of the
        whole estate adding up to 100 %). Returns the table as stored."""
        return engine_context.set_instalments(
            self.bw, [{"locktime": d, "shares": dict(sh)} for d, sh in rows])

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
        self.bw.opened_dialogs = []
        self.world.broadcast_log = []
        self.world.verified = []
        return len(self.world.pushed)

    def _open_dialog(self, bal_window, parent=None, renew=False, closing=False):
        """``BalBuildWillDialog`` as the window itself opens it (Qt-free)."""
        dlg = FlowDialog(bal_window, renew=renew, closing=closing)
        bal_window.opened_dialogs.append(dlg)
        return dlg

    # Other entry points (CHANGELOG #114) ---------------------------------

    def act(self, label, action, cancel_on=()):
        """Run one owner action outside the Check dialog and record it.

        ``action()`` is called; what it returns (or the error it raises) is
        recorded with everything the window showed and stored. The task
        thread and Qt's pending events run to the end first.
        """
        pushed_before = self._begin(cancel_on)
        try:
            result = {"returned": action()}
        except Exception as e:  # noqa: BLE001 - the error is the result
            result = {"raised": "{}: {}".format(type(e).__name__, e)}
        run_until_idle()
        self.recorder.record_action(label, result, pushed_before)
        return result

    def prepare(self, cancel_on=()):
        """Will tab menu > Prepare (``BalWindow.prepare_will``)."""
        return self.act("gui prepare", lambda: _summary(self.bw.prepare_will()),
                        cancel_on)

    def auto(self, network=False):
        """The automatic rebuild, through ``BalWindow.maybe_auto_rebuild``.

        The option is switched on and the cooldown cleared first. With
        ``network=True`` the wallet has a (fake) network, so the headless
        invalidation can broadcast; without it, it stops there, as offline.
        """
        self.plugin.AUTO_REBUILD.set(True)
        self.bw._auto_rebuild_cooldown_until = 0.0
        if network:
            self.monkeypatch.setattr(
                self.wallet, "network",
                FakeNetwork(self.world, self.wallet, "invalidation(automatic)"),
                raising=False)
        return self.act("gui automatic rebuild", self.bw.maybe_auto_rebuild)

    def menu_sign(self, cancel_on=()):
        """Will tab menu > Sign (``ask_password_and_sign_transactions``)."""
        return self.act("gui menu sign",
                        self.bw.ask_password_and_sign_transactions, cancel_on)

    def menu_broadcast(self):
        """Will tab menu > Broadcast will (``broadcast_transactions``)."""
        return self.act("gui menu broadcast", self.bw.broadcast_transactions)

    def row_check(self):
        """Right click on the rows > check (``check_transactions``).

        The list passes the selected rows: here every row of the will.
        """
        return self.act("gui row check",
                        lambda: self.bw.check_transactions(dict(self.bw.willitems)))

    def menu_invalidate(self, cancel_on=()):
        """Will tab menu > Invalidate will (``BalWindow.invalidate_will``)."""
        return self.act("gui menu invalidate", self.bw.invalidate_will, cancel_on)

    def close_wallet(self):
        """The wallet closes (``BalWindow.on_close``)."""
        return self.act("gui wallet close", self.bw.on_close)

    def cli(self, method, *args, **kwargs):
        """One ``bal_*`` command: a fresh controller, errors as the daemon shows them.

        Goes through ``bal.cli.commands._call``, so a domain error becomes
        the same message the terminal prints (exit 1).
        """
        from bal.cli import commands as cli_commands

        label = "cli {}{}".format(method, "".join(" " + str(a) for a in args))
        return self.act(label, lambda: cli_commands._call(
            self.plugin, self.wallet, method, *args, **kwargs))

    def reload_window(self):
        """The window reads the will again from the wallet (as when it opens).

        After a command-line action the GUI copy is stale; a real window
        would only see it after reopening the wallet. The delivery settings
        are loaded again as ``BalWindow.__init__`` loads them (CHANGELOG
        #123: migration if needed, then the anchor check).
        """
        self.bw.will = self.wallet.db.get_dict("will")
        self.bw.willitems = {}
        for wid, w in self.bw.will.items():
            self.bw.willitems[wid] = WillItem(w, wallet=self.wallet)
        self.bw._settings_ready = engine_context.load_will_settings(
            self.bw, lock_timeout=1.0)


def _summary(value):
    """What a GUI flow returned, short: the will items become their count."""
    if isinstance(value, dict):
        return {"will items": len(value)}
    return value


# ---------------------------------------------------------------- recording

_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
_RAW_TX = re.compile(r"^(?:[0-9a-f]+|cHNidP[A-Za-z0-9+/=]+)$")


# The remaining-time colour of the delivery date (#124), dropped from the
# recorded result sentence (see ``Recorder.dialog``).
_COLOUR_SPAN = re.compile(r'<span style="color:#[0-9a-fA-F]{6};">(.*?)</span>', re.S)


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
        """Replace every transaction id inside ``value`` by its short name.

        A whole serialized transaction (raw hex or PSBT, as the command line
        returns them) becomes ``<tx Tn>``.
        """
        if isinstance(value, str):
            if len(value) > 120 and _RAW_TX.match(value):
                try:
                    return "<tx {}>".format(self.name(tx_from_any(value).txid()))
                except Exception:  # noqa: BLE001 - not a transaction after all
                    return "<{} characters>".format(len(value))
            # The temporary folder changes at every run.
            value = value.replace(self.session.dir, "<dir>")
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

    def dialog(self, dlg):
        """What a "Check your will" dialog showed.

        The remaining-time colour of the delivery date in the result
        sentence (CHANGELOG #124) is a style, not a text: it is left out, so
        the golden files keep recording what the dialog says.
        """
        report = dlg._report().snapshot()
        if report.get("result"):
            kind, text = report["result"]
            report["result"] = (kind, _COLOUR_SPAN.sub(r"\1", text or ""))
        return {
            "rows": self.text(dlg.labels),
            "history": self.text(list(dlg.history)),
            "report": self.text(json.loads(json.dumps(report, default=list))),
            "dialog_events": self.text(dlg.events),
        }

    def record_action(self, label, result, pushed_before):
        """Add one action outside the Check dialog (CHANGELOG #114)."""
        step = {"action": self.text(label),
                "result": self.text(json.loads(json.dumps(result, default=self._plain)))}
        dialogs = [self.dialog(d) for d in self.session.bw.opened_dialogs]
        if dialogs:
            step["dialogs_opened"] = dialogs
        step.update(self.common(pushed_before))
        self.steps.append(step)

    def _plain(self, value):
        """JSON for what json cannot write: a transaction becomes ``<tx Tn>``.

        Anything else becomes its type name (a repr would carry a memory
        address that changes at every run).
        """
        txid = getattr(value, "txid", None)
        if callable(txid):
            return "<tx {}>".format(self.name(txid()))
        return "<{}>".format(type(value).__name__)

    def common(self, pushed_before):
        """The part of a record shared by every action.

        First rule I1 of CHANGELOG #123: Electrum's global
        ``bal_will_settings`` must be byte-identical to the reference
        (:meth:`Session.assert_global_unchanged`), else the step fails.
        The delivery form (CHANGELOG #127) is recorded only for the period
        form (``form``, ``period_months``), so the golden files of a fixed
        date do not change.
        """
        s = self.session
        s.assert_global_unchanged()
        ws = s.bw.will_settings
        stored = s.wallet.db.get_dict("will")
        return {
            "window_events": self.text(s.bw.events),
            "popups": self.text(s.bw.popups),
            "questions": self.text(s.user.asked),
            "broadcasts": [[k, self.name(t)] for k, t in s.world.broadcast_log],
            "pushed": [[u, self.name(t)] for u, t, _raw in s.world.pushed[pushed_before:]],
            "verified": [[u, self.name(t)] for u, t in s.world.verified],
            "items": self.items(),
            # The will as stored in the wallet file (what survives a crash
            # or a restart): name -> statuses set.
            "stored": {self.name(wid): sorted(
                k for k in WillItem.STATUS_DEFAULT if w.get(k) is True)
                for wid, w in stored.items()},
            # Electrum's History label of every stored will transaction.
            "labels": {self.name(wid): s.wallet.get_label_for_txid(wid) or ""
                       for wid in stored},
            "settings": self.settings(ws),
            # The heirs as stored in the wallet (the command line may delete
            # some when it loads them).
            "heirs": {k: list(v[:2]) for k, v in s.wallet.db.get("heirs", {}).items()},
        }

    @staticmethod
    def settings(ws):
        """The delivery settings of a record: date, Check Alive, fee, and
        for the period form (#127) ``form`` and ``period_months``."""
        out = {
            "locktime": fmt(ws.get("locktime")) if isinstance(ws.get("locktime"), int)
            else ws.get("locktime"),
            "threshold": ws.get("threshold"),
            "baltx_fees": ws.get("baltx_fees"),
        }
        if delivery.is_period(ws):
            out["form"] = ws.get("form")
            out["period_months"] = ws.get("period_months")
        return out

    def record(self, dlg, pushed_before):
        """Add one Check to the scenario's steps."""
        step = {"action": "check"}
        step.update(self.dialog(dlg))
        step.update(self.common(pushed_before))
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
