"""The REAL ``BalWindow.__init__`` migrates before it builds the toolbars (#123).

``tests/flowkit.py`` replaces ``BalWindow.__init__`` (its ``FlowWindow`` has
no Qt tabs), so the flow tests cannot see the order inside the real one.
Here the real ``__init__`` runs on a stub of Electrum's main window (only
what ``__init__`` and the two list tabs use of it): the wallet's delivery
settings must be migrated BEFORE the Heirs and Will toolbars
(``WillSettingsWidget``) are built, so the toolbar shows the migrated date
(29/08/2029 for the owner) and not the old shared period.

Also here, because they need the real widgets:

* a settings write from a worker thread touches no widget (rule I5), the
  fields are refreshed on the GUI thread afterwards;
* the one-time note is scheduled by ``load_wallet`` after the window (2 s,
  before the Check Alive question at 3 s), shown once, and kept when
  showing it fails;
* a busy flow lock at opening: no migration, display values, then
  ``init_will`` prepares the settings;
* a mouse-wheel tick over the ADVANCED (editable) toolbar dates stores
  nothing.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three fake
will-executors with their own fee addresses, throw-away test wallets with
the test password, fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_window_init_migration.py
"""

import logging
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from bal.core import delivery
from bal.engine import context as engine_context
from bal.engine.locks import flow_lock_for
from bal.gui.qt import lists as lists_mod
from bal.gui.qt import plugin as qt_plugin
from bal.gui.qt.window import BalWindow

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"

# The owner's will in force: 2029-08-29 00:00 UTC.
OWNER_DATE = fk.ts(2029, 8, 29)
# The old shared value of the owner's Electrum (Safe21 before #123).
OLD_GLOBAL = {"locktime": "1054d", "baltx_fees": 13, "threshold": "-180d"}


@pytest.fixture
def make(monkeypatch):
    """Start ``tests/flowkit.py`` sessions with three will-executors.

    Electrum's asyncio loop runs during the test and is stopped after it
    (else pytest never exits); every session's folder is removed.
    """
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session (``fk.Session`` arguments), three will-executors."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


class StubElectrumWindow(QWidget):
    """What ``BalWindow.__init__``, its two list tabs and ``init_will`` use
    of Electrum's ``ElectrumWindow``: ``wallet``, ``config``,
    ``get_decimal_point`` and ``create_list_tab``. Nothing else is read
    (checked with a stub that logged every other attribute asked for). A
    real QWidget: the lists are built as its children."""

    def __init__(self, wallet, config):
        """Keep the wallet and the config; no Electrum window is built."""
        super().__init__()
        self.wallet = wallet
        self.config = config

    def get_decimal_point(self):
        """Bitcoin units (8 decimals)."""
        return 8

    def create_list_tab(self, lst):
        """Electrum's ``create_list_tab`` (``main_window.py``): a QWidget
        with a QVBoxLayout, the list's toolbar, then the list."""
        w = QWidget()
        w.searchable_list = lst
        vbox = QVBoxLayout()
        w.setLayout(vbox)
        toolbar = lst.create_toolbar(self.config)
        if toolbar:
            vbox.addLayout(toolbar)
        vbox.addWidget(lst)
        if toolbar:
            lst.show_toolbar()
        return w


def wallet_dict(s):
    """The wallet's own delivery settings (the live dict of the wallet file)."""
    return s.wallet.db.get_dict("will_settings")


def owner_before_update(make, mode="basic"):
    """The owner's wallet as the code before #123 left it.

    A will at 2029-08-29, signed and sent to three will-executors; then the
    wallet's own dict is emptied (Safe21 before #123 never wrote it), the
    heirs get the shared "1054d" and Electrum's global holds it (BAL's key,
    as the older plugin left it). No window has migrated it yet: the REAL
    window built next does.
    """
    s = make(mode=mode, locktime=OWNER_DATE)
    s.check()
    d = wallet_dict(s)
    for key in list(d):
        del d[key]
    for name in list(s.bw.heirs):
        heir = list(s.bw.heirs[name])
        heir[2] = "1054d"
        s.bw.heirs[name] = heir
    s.config.set_key("bal_will_settings", dict(OLD_GLOBAL))
    s.snapshot_global()
    return s


def real_window(s, monkeypatch, seen=None):
    """Build the REAL ``BalWindow`` on the session's wallet.

    ``seen`` (a list) receives a copy of the window's settings each time a
    toolbar (``WillSettingsWidget``) is built. The plugin's icons are not
    read: the test plugin has no Electrum ``Plugins`` parent, which
    ``read_file`` asks for the file.
    """
    if seen is not None:
        real_widget = lists_mod.WillSettingsWidget

        def spy(bal_window, parent, *args, **kwargs):
            """Record the settings the toolbar is built from."""
            seen.append(dict(bal_window.will_settings or {}))
            return real_widget(bal_window, parent, *args, **kwargs)

        monkeypatch.setattr(lists_mod, "WillSettingsWidget", spy)
    monkeypatch.setattr(s.plugin, "read_file", lambda path: b"", raising=False)
    return BalWindow(s.plugin, StubElectrumWindow(s.wallet, s.config))


def toolbars(bw):
    """The toolbars of the Heirs and Will tabs."""
    return [bw.heir_list_widget.will_settings_widget,
            bw.will_list_widget.will_settings_widget]


def shown_day(toolbar):
    """The UTC day the toolbar's delivery field shows, ``DD/MM/YYYY``."""
    editor = toolbar.widgets["locktime"].locktime_date_e
    return editor.dateTime().toUTC().toString("dd/MM/yyyy")


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_real_window_migrates_before_the_toolbars(make, monkeypatch, caplog, mode):
    """The real ``__init__`` reads the heirs, migrates the wallet's own
    settings under the flow lock, and only then builds both toolbars: they
    show 29/08/2029 in the Date editor (the Raw/Date selector hidden), fee
    13. The window's settings are the wallet's live dict (I3); the global is
    unchanged (I1); one log line says where the date came from."""
    s = owner_before_update(make, mode)
    lock = flow_lock_for(s.wallet)
    held = []
    real_migration = delivery.migration_values

    def migration_values(*args, **kwargs):
        """Record whether the flow lock is held, then migrate."""
        held.append(lock.locked())
        return real_migration(*args, **kwargs)

    monkeypatch.setattr(delivery, "migration_values", migration_values)
    seen = []
    caplog.set_level(logging.INFO)
    bw = real_window(s, monkeypatch, seen)

    d = wallet_dict(s)
    assert bw.will_settings is d                                         # I3
    assert bw.settings_unavailable is False and bw._settings_ready is True
    assert held == [True]
    assert (d["locktime"], d["baltx_fees"], d["migrated_from"]) == (
        OWNER_DATE, 13, "will")
    assert (d["legacy_locktime"], d["note_kind"]) == ("1054d", "converted")
    assert len(seen) == 2, seen                   # Heirs and Will toolbars
    for settings in seen:
        assert settings["settings_version"] == 2     # #127: schema 2
        assert settings["locktime"] == OWNER_DATE
    for toolbar in toolbars(bw):
        field = toolbar.widgets["locktime"]
        assert field.get_value() == OWNER_DATE
        assert shown_day(toolbar) == "29/08/2029"
        assert field.current_index == 1                         # Date editor
        assert field.combo.isHidden()                           # Raw hidden
        assert toolbar.widgets["baltx_fees"].get_value() == 13
    s.assert_global_unchanged()                                          # I1
    messages = [r.getMessage() for r in caplog.records]
    assert any("will settings migrated: from will, delivery 2029-08-29, fee 13"
               in m for m in messages), messages


def test_load_wallet_shows_the_note_once_after_the_window(make, monkeypatch):
    """``load_wallet`` schedules the note 2 s after the wallet opens, before
    the Check Alive question (3 s); nothing is shown while the window is
    built. The note is then shown once, and its keys go only after the
    message box returned."""
    s = owner_before_update(make)
    bw = real_window(s, monkeypatch)
    d = wallet_dict(s)
    shown = []
    monkeypatch.setattr(bw, "show_message", shown.append)
    timers = []

    class Timer:
        """``QTimer`` of the Qt plugin: the calls are recorded, not run."""

        @staticmethod
        def singleShot(ms, fn):  # noqa: N802 - Qt name
            """Record one deferred call."""
            timers.append((ms, fn))

    monkeypatch.setattr(qt_plugin, "QTimer", Timer)
    plugin_self = type("QtPlugin", (), {})()
    plugin_self.get_window = lambda window: bw
    plugin_self._close_plugins_manager_dialog = lambda: None
    qt_plugin.Plugin.load_wallet(plugin_self, s.wallet, bw.window)

    delays = {getattr(fn, "__name__", None): ms for ms, fn in timers}
    assert delays["show_delivery_note"] == 2000
    assert delays["check_alive_on_open"] == 3000
    assert shown == [] and d["note_kind"] == "converted"
    note = next(fn for ms, fn in timers if ms == 2000)
    note()
    assert shown == ["Your delivery date is now fixed: 29/08/2029."]
    assert "note_kind" not in d and "note_new" not in d
    note()
    assert len(shown) == 1


def test_note_kept_when_showing_it_fails(make, monkeypatch):
    """When the message box cannot be shown, the note stays for the next
    opening; it is then shown once."""
    s = owner_before_update(make)
    bw = real_window(s, monkeypatch)
    bw.disable_plugin = False
    d = wallet_dict(s)

    def broken(text):
        """A message box that cannot be shown."""
        raise RuntimeError("injected: no screen")

    monkeypatch.setattr(bw, "show_message", broken)
    bw.show_delivery_note()
    assert d["note_kind"] == "converted"
    shown = []
    monkeypatch.setattr(bw, "show_message", shown.append)
    bw.show_delivery_note()
    bw.show_delivery_note()
    assert shown == ["Your delivery date is now fixed: 29/08/2029."]
    assert "note_kind" not in d


def test_worker_thread_write_touches_no_widget(make, monkeypatch):
    """Rule I5: a setting written from a worker thread (the Check's phase 1,
    the automatic rebuild) only changes the stored value; the fields are
    refreshed later on the GUI thread. A period is refused from any thread."""
    s = make(locktime=OWNER_DATE)
    bw = real_window(s, monkeypatch)
    calls = []
    real_update = bw.update_widget_value

    def spy(widget, field, value):
        """Record on which thread a field is touched."""
        calls.append(threading.current_thread() is threading.main_thread())
        return real_update(widget, field, value)

    monkeypatch.setattr(bw, "update_widget_value", spy)

    def on_worker(fn):
        """Run ``fn`` on a worker thread and wait for it."""
        thread = threading.Thread(target=fn)
        thread.start()
        thread.join(10)

    def process_events():
        """Let Qt deliver the queued refresh (GUI thread)."""
        for _ in range(5):
            QCoreApplication.processEvents()

    on_worker(lambda: bw.update_setting_widgets(17, "baltx_fees", True))
    assert wallet_dict(s)["baltx_fees"] == 17
    assert calls == []                       # no widget touched off the GUI thread
    process_events()
    assert calls and all(calls)
    for toolbar in toolbars(bw):
        assert toolbar.widgets["baltx_fees"].get_value() == 17

    calls.clear()
    on_worker(lambda: bw.store_will_setting("threshold", "-90d"))
    assert calls == [] and wallet_dict(s)["threshold"] == "-90d"
    process_events()
    assert calls and all(calls)

    on_worker(lambda: bw.update_setting_widgets("1054d", "locktime", True))
    process_events()
    assert bw.update_setting_widgets("1054d", "locktime", True) is None
    assert wallet_dict(s)["locktime"] == OWNER_DATE
    for toolbar in toolbars(bw):
        assert shown_day(toolbar) == "29/08/2029"
    s.assert_global_unchanged()


def test_busy_flow_lock_at_opening_then_init_will(make, monkeypatch):
    """A command holds the wallet's flow lock while the window opens: the
    window waits one second, migrates nothing and fails closed with display
    values (the date of the will in force, never the period); the toolbars
    are built all the same. ``init_will`` (at ``load_wallet``) then prepares
    the settings and the fields show them."""
    s = owner_before_update(make)
    lock = flow_lock_for(s.wallet)
    started, finish = threading.Event(), threading.Event()

    def command():
        """Another command working on this wallet (holds the lock)."""
        with lock:
            started.set()
            finish.wait(10)

    thread = threading.Thread(target=command)
    thread.start()
    assert started.wait(10)
    try:
        bw = real_window(s, monkeypatch)
    finally:
        finish.set()
        thread.join(10)
    d = wallet_dict(s)
    assert bw._settings_ready is False and bw.settings_unavailable is True
    assert dict(d) == {}                                    # nothing migrated
    assert bw.will_settings is not d
    assert bw.will_settings["locktime"] == OWNER_DATE      # display only
    assert not delivery.is_current(bw.will_settings)
    for toolbar in toolbars(bw):
        assert shown_day(toolbar) == "29/08/2029"

    bw.init_will()
    assert bw._settings_ready is True and bw.settings_unavailable is False
    assert bw.will_settings is d
    assert (d["settings_version"], d["locktime"], d["migrated_from"]) == (
        2, OWNER_DATE, "will")
    for toolbar in toolbars(bw):
        assert shown_day(toolbar) == "29/08/2029"
        assert toolbar.widgets["baltx_fees"].get_value() == 13
    s.assert_global_unchanged()


@pytest.mark.parametrize("delta", [-120, 120], ids=["wheel_down", "wheel_up"])
def test_wheel_on_the_advanced_toolbar_dates_stores_nothing(
        make, monkeypatch, delta):
    """One mouse-wheel tick over the ADVANCED Heirs and Will toolbar dates
    (editable there, no click, no focus) changes nothing: no settings write,
    the stored date and the shown day stay. Before the guard one tick down
    stored a date one day earlier (an earlier will), one tick up a later one
    (a paid invalidation)."""
    s = make(mode="advanced", locktime=OWNER_DATE)
    bw = real_window(s, monkeypatch)
    s.check()
    d = wallet_dict(s)
    assert d["locktime"] == OWNER_DATE
    before = dict(d)
    calls = []
    real_set = engine_context.set_will_setting

    def spy(host, field, value):
        """Record every validated settings write."""
        calls.append((field, value))
        return real_set(host, field, value)

    monkeypatch.setattr(engine_context, "set_will_setting", spy)
    accepted = []
    for toolbar in toolbars(bw):
        editor = toolbar.widgets["locktime"].locktime_date_e
        assert not editor.isReadOnly()           # ADVANCED: editable here
        assert editor.focusPolicy() == Qt.FocusPolicy.StrongFocus
        pos = QPointF(editor.width() / 2, editor.height() / 2)
        event = QWheelEvent(
            pos, editor.mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(editor, event)
        QCoreApplication.processEvents()
        accepted.append(event.isAccepted())
    assert calls == []
    assert dict(d) == before
    for toolbar in toolbars(bw):
        assert shown_day(toolbar) == "29/08/2029"
        assert toolbar.widgets["locktime"].get_value() == OWNER_DATE
    assert accepted == [False, False]            # left to the parent (scroll)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
