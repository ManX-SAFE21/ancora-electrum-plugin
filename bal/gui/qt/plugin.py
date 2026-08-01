"""
bal.gui.qt.plugin
=================

The Qt entry point of the plugin.

:class:`Plugin` subclasses :class:`bal.core.plugin_base.BalPlugin` and adds the
Electrum ``@hook`` methods that wire the plugin into the Qt GUI (status-bar
button, Tools menu, wallet load/close, settings dialog).  Electrum instantiates
this class because the package ``manifest.json`` declares ``available_for:
["qt"]`` and the loader imports ``qt.py`` (a thin shim re-exporting this class).

One :class:`bal.gui.qt.window.BalWindow` is created per top-level wallet window
and cached in ``self.bal_windows``.
"""

from electrum.gui.qt.main_window import StatusBarButton
from PyQt6.QtWidgets import QLayout

from .common import *
from .common import (  # underscore names are not re-exported by "import *"
    _,
    _logger,
    read_QIcon_from_bytes,
)
from .dialogs import BalDialog
from .widgets import BalCheckBox, BalLineEdit, BalSpinBox, BalTextEdit
from .window import BalWindow


def _window_key(window):
    """Return a stable, hashable identity for an Electrum top-level window.

    The original code used ``window.winId`` (the *bound method*, not its
    result) as a dict key.  That happened to work because the same window
    object yields the same bound method, but it is semantically wrong and
    fragile across window re-creation / multiple wallets.  ``id(window)`` is a
    stable, correct identity for the lifetime of the window object.
    """
    return id(window)


class Plugin(BalPlugin):
    def __init__(self, parent, config, name):
        _logger.info("INIT BALPLUGIN")
        BalPlugin.__init__(self, parent, config, name)
        self.bal_windows = {}
        # Status-bar buttons, keyed by id(sb.window()).  Tracking them lets us
        # remove a stale button before creating a fresh one when a wallet is
        # switched / Electrum is restarted, so the icon is never duplicated.
        self._statusbar_buttons = {}

    @hook
    def init_qt(self, gui_object):
        # Called when the plugin is enabled, including *hot* (while a wallet is
        # already open).  The original code gave up here and asked the user to
        # restart Electrum; instead we fully initialise the already-open
        # window(s) so the plugin works immediately.
        _logger.info("HOOK bal init qt")
        try:
            self.gui_object = gui_object
            for window in gui_object.windows:
                self._setup_window(window, load_open_wallet=True)
        except Exception as e:
            _logger.error("Error loading plugin {}".format(e))
            raise e

    @staticmethod
    def _close_plugins_manager_dialog():
        """Close Electrum's "Electrum Plugins" manager dialog if it is open.

        This is the native Electrum ``PluginsDialog`` (a ``WindowModalDialog``);
        it is not owned by this plugin, so we locate it among the application's
        top-level widgets and close it.  Failures are non-fatal: leaving the
        dialog open is harmless, so we never propagate exceptions from here.
        """
        Plugin._handle_plugins_manager_dialog(attempt=0)

    @staticmethod
    def _find_plugins_manager_dialogs():
        """Return the open Electrum "Electrum Plugins" manager dialog(s).

        The match is intentionally permissive: when our plugin is loaded from a
        zip (``electrum_external_plugins``), ``isinstance`` against the imported
        ``PluginsDialog`` class can fail due to differing module identities, so
        we also match by class name and by window title (including the localized
        title, since the user runs Electrum under a non-English locale).
        """
        try:
            from PyQt6.QtWidgets import QApplication
        except Exception:
            return []
        try:
            from electrum.gui.qt.plugins_dialog import (
                PluginsDialog as plugins_dialog,  # noqa: N813
            )
        except Exception:
            plugins_dialog = None
        app = QApplication.instance()
        if app is None:
            return []
        # Accept both the English title and the translated one.  We cannot rely
        # only on _() because the dialog object may have been built with a
        # different gettext binding than ours when loaded from a zip.
        titles = {"Electrum Plugins"}
        try:
            titles.add(_("Electrum Plugins"))
        except Exception:
            pass
        found = []
        for w in app.topLevelWidgets():
            try:
                is_match = False
                if plugins_dialog is not None and isinstance(w, plugins_dialog):
                    is_match = True
                elif type(w).__name__ == "PluginsDialog":
                    is_match = True
                elif w.windowTitle() in titles:
                    is_match = True
                if not is_match:
                    continue
                # Only count it as "open" if it is actually visible: after a
                # successful close()/reject() the QDialog object still lives in
                # topLevelWidgets() but becomes invisible, so filtering by
                # isVisible() is what tells "still open" from "already closed".
                visible = w.isVisible()
                _logger.info(
                    "plugins manager dialog match: cls={} title={!r} "
                    "visible={}".format(
                        type(w).__name__, w.windowTitle(), visible
                    )
                )
                if visible:
                    found.append(w)
            except Exception as e:
                _logger.debug("inspecting top-level widget failed: {}".format(e))
        return found

    @staticmethod
    def _try_dismiss_dialog(d):
        """Attempt to dismiss a (possibly modal) dialog as robustly as we can.

        A ``PluginsDialog`` is opened with ``exec()`` (a nested, *application-
        modal* event loop).  Inside such a loop a plain ``close()`` is not
        always honoured, so we also try ``reject()`` / ``done()`` which end the
        modal loop directly.  Any of these may fail depending on Qt state, so
        each is guarded independently.
        """
        try:
            from PyQt6.QtWidgets import QDialog as qdialog  # noqa: N813
        except Exception:
            qdialog = None
        # 1) reject() / done(): the reliable way to end an exec() modal loop.
        if qdialog is not None and isinstance(d, qdialog):
            try:
                d.reject()
            except Exception as e:
                _logger.debug("reject() failed: {}".format(e))
            try:
                d.done(qdialog.DialogCode.Rejected)
            except Exception as e:
                _logger.debug("done() failed: {}".format(e))
        # 2) close(): covers non-QDialog top-levels and is a harmless extra.
        try:
            d.close()
        except Exception as e:
            _logger.debug("could not close plugins dialog: {}".format(e))

    @staticmethod
    def _handle_plugins_manager_dialog(attempt=0):
        """Try to auto-close the manager dialog; retry a few times.

        Enabling the plugin happens while Electrum's ``PluginsDialog`` may still
        be running its own modal event loop, so a single ``close()`` can be
        ignored. We retry on a short schedule and, if it is still open after the
        last attempt, fall back to bringing it to the front so the user notices
        it and closes it themselves (it must not linger in the background).
        """
        try:
            from PyQt6.QtCore import QTimer as qtimer  # noqa: N813
        except Exception:
            qtimer = None
        # Schedule of retry delays (ms) measured from each call.
        retry_delays = [400, 800, 1500]
        dialogs = Plugin._find_plugins_manager_dialogs()
        _logger.info(
            "auto-close plugins dialog: attempt={} found={}".format(
                attempt, len(dialogs)
            )
        )
        for d in dialogs:
            Plugin._try_dismiss_dialog(d)
        # Re-check: anything still visible?
        still_open = Plugin._find_plugins_manager_dialogs()
        if not still_open:
            _logger.info("plugins dialog closed successfully")
            return
        if attempt < len(retry_delays) and qtimer is not None:
            qtimer.singleShot(
                retry_delays[attempt],
                lambda: Plugin._handle_plugins_manager_dialog(attempt + 1),
            )
            return
        # Final fallback: we could not close it -> at least raise it to the
        # front so it does not stay hidden in the background.
        _logger.info(
            "could not close plugins dialog after {} attempts; "
            "bringing it to front".format(attempt + 1)
        )
        for d in still_open:
            try:
                d.showNormal()
                d.raise_()
                d.activateWindow()
            except Exception as e:
                _logger.debug("could not raise plugins dialog: {}".format(e))

    def _setup_window(self, window, *, load_open_wallet):
        """Create the BalWindow for *window* and wire its menu (and, when
        enabling hot, the already-open wallet).

        This mirrors what the ``init_menubar`` + ``load_wallet`` hooks do at
        normal startup, so enabling the plugin while a wallet is open no longer
        requires restarting Electrum.
        """
        w = self.get_window(window)
        # Use Electrum's official tools_menu instead of searching the menubar
        # for a menu whose *translated* title equals "&Tools" (which breaks
        # under non-English locales).
        tools_menu = getattr(window, "tools_menu", None)
        if tools_menu is not None:
            try:
                w.init_menubar_tools(tools_menu)
            except Exception as e:
                _logger.error("init_qt: failed wiring tools menu: {}".format(e))
        if load_open_wallet and getattr(window, "wallet", None):
            # Replicate load_wallet() for the wallet that is already open.
            try:
                w.wallet = window.wallet
                w.init_will()
                w.willexecutors = Willexecutors.get_willexecutors(
                    self, update=False, bal_window=w
                )
                w.disable_plugin = False
                w.ok = True
            except Exception as e:
                _logger.error("init_qt: failed initialising open wallet: {}".format(e))
        return w

    @hook
    def create_status_bar(self, sb):
        # Show the BAL icon in the status bar (bottom-right): it signals that
        # the Bitcoin After Life plugin is installed and, when clicked, quickly
        # opens the plugin settings (settings_dialog).
        #
        # NOTE: this was NOT the "condensed menu/tabs" bug under the Electrum
        # logo -- that one was a Windows OverflowError (year 2038), fixed
        # separately.  The icon must therefore be kept.
        #
        # To avoid a duplicated icon on restart / wallet switch, we track the
        # button by id(sb.window()) and remove the stale one before creating a
        # fresh one.
        _logger.info("HOOK create status bar")
        key = id(sb.window())
        old = self._statusbar_buttons.pop(key, None)
        if old is not None:
            try:
                old.setParent(None)
                old.deleteLater()
            except Exception:
                pass
        b = StatusBarButton(
            read_QIcon_from_bytes(self.read_file("icons/bal32x32.png")),
            "Bal " + _("Bitcoin After Life"),
            lambda: self.settings_dialog(sb.window()),
            sb.height(),
        )
        sb.addPermanentWidget(b)
        self._statusbar_buttons[key] = b

        # When the plugin is enabled "hot" from Tools -> Plugins, Electrum keeps
        # its "Electrum Plugins" manager dialog open and even calls
        # bring_to_front on it. Enabling triggers reload_windows(), which
        # recreates the window and therefore fires this create_status_bar hook;
        # that makes this the right place to auto-close the leftover manager
        # dialog (Electrum 4.7.x no longer calls the old init_qt hook).
        #
        # We use a QTimer so this runs *after* Electrum's own bring_to_front
        # (QTimer.singleShot(100, ...)); a slightly larger delay makes our close
        # win. On a normal startup no PluginsDialog is open, so the helper is a
        # harmless no-op.
        QTimer.singleShot(250, self._close_plugins_manager_dialog)

    @hook
    def init_menubar(self, window):
        _logger.info("HOOK init_menubar")
        w = self.get_window(window)
        w.init_menubar_tools(window.tools_menu)
        # Also try here: init_menubar is one of the hooks fired when Electrum
        # recreates the window during a hot enable (reload_windows()), so it is
        # another reliable trigger to auto-close the leftover manager dialog.
        QTimer.singleShot(300, self._close_plugins_manager_dialog)

    @hook
    def load_wallet(self, wallet, main_window):
        _logger.debug("HOOK load wallet")
        w = self.get_window(main_window)
        # havetoupdate = Util.fix_will_settings_tx_fees(wallet.db)
        w.wallet = wallet
        w.init_will()
        w.willexecutors = Willexecutors.get_willexecutors(
            self, update=False, bal_window=w
        )
        w.disable_plugin = False
        w.ok = True
        # load_wallet is fired on the recreated window during a hot enable too;
        # use it as an extra trigger to auto-close the leftover manager dialog.
        QTimer.singleShot(350, self._close_plugins_manager_dialog)

    @hook
    def close_wallet(self, wallet):
        _logger.debug("HOOK close wallet")
        # Iterate over a snapshot: on_close() may mutate the GUI/state.
        for win in list(self.bal_windows.values()):
            if getattr(win, "wallet", None) == wallet:
                try:
                    win.on_close()
                except Exception as e:
                    _logger.error("close_wallet: on_close failed: {}".format(e))

    @hook
    def init_keystore(self):
        _logger.debug("init keystore")

    @hook
    def daemon_wallet_loaded(self, boh, wallet):
        _logger.debug("daemon wallet loaded")

    def get_window(self, window):
        window = window.top_level_window()
        key = _window_key(window)
        w = self.bal_windows.get(key, None)
        if w is None:
            w = BalWindow(self, window)
            self.bal_windows[key] = w
        return w

    def requires_settings(self):
        return True

    def settings_widget(self, window):

        w = self.get_window(window.window)
        widget = QWidget()
        enterbutton = EnterButton(_("Settings"), partial(w.settings_dialog, window))

        widget.setLayout(Buttons(enterbutton, widget))
        return widget

    def password_dialog(self, msg=None, parent=None):
        parent = parent or self
        d = PasswordDialog(parent, msg)
        return d.run()

    def get_seed(self):
        password = None
        if self.wallet.has_keystore_encryption():
            password = self.password_dialog(parent=self.d.parent())
            if not password:
                raise UserCancelled()

        keystore = self.wallet.get_keystore()
        if not keystore or not keystore.has_seed():
            return
        self.extension = bool(keystore.get_passphrase(password))
        return keystore.get_seed(password)

    def settings_dialog(self, window=None, wallet=None):

        d = BalDialog(window, self, self.get_window_title("Settings"))
        d.setMinimumSize(100, 200)
        qicon = read_QPixmap_from_bytes(self.read_file("icons/bal16x16.png"))
        lbl_logo = QLabel()
        lbl_logo.setPixmap(qicon)

        # WINDOWS FLICKER FIX (root cause found by bisection, tested on Windows
        # by the user): the ADVANCED-only rows below used to be added to the
        # grid *visible* and then hidden all at once with setVisible(False)
        # AFTER they were already part of the layout. On Windows that
        # visible->hidden transition, inside an already-populated layout while
        # the dialog's native window is being created, forced a live re-layout
        # that flashed the dialog on screen (the "ghost window" flicker). It
        # only showed in ADVANCED mode because that is the larger dialog. The
        # fix, validated step by step on Windows, is to set each ADVANCED-only
        # widget's visibility BEFORE it is ever added to the layout, so it
        # never transitions visible->hidden inside a live layout.
        #
        # ``basic_init`` is the current mode; ``_hide_if_basic(w)`` hides a
        # widget immediately (at creation/add time) when in BASIC mode and
        # returns it, so it can be wrapped inline around each ADVANCED-only
        # widget at its grid.addWidget(...) call.
        basic_init = str(self.USER_TYPE.get()).lower() != "advanced"

        def _hide_if_basic(w):
            """Hide *w* now (before it is added to any layout) if in BASIC
            mode, and return it. Avoids the Windows relayout flicker that a
            later setVisible(False) inside the populated grid caused."""
            if basic_init:
                w.setVisible(False)
            return w

        # heir_ping_willexecutors = BalCheckBox(self.PING_WILLEXECUTORS)
        # heir_ask_ping_willexecutors = BalCheckBox(self.ASK_PING_WILLEXECUTORS)
        # heir_no_willexecutor = BalCheckBox(self.NO_WILLEXECUTOR)

        def on_multiverse_change():
            self.update_all()

        # heir_enable_multiverse = BalCheckBox(self.ENABLE_MULTIVERSE,on_multiverse_change)

        heir_hide_replaced = BalCheckBox(self.HIDE_REPLACED, on_multiverse_change)

        heir_hide_invalidated = BalCheckBox(self.HIDE_INVALIDATED, on_multiverse_change)

        # Auto-sign checkbox (Group B / B2). When ticked, the "Check" action
        # automatically signs and broadcasts the will after querying the
        # will-executor servers. Bound to the persisted AUTO_SIGN config; the
        # default is ON (see plugin_base.py).
        heir_auto_sign = BalCheckBox(self.AUTO_SIGN)

        # Editable dates checkbox (Group C / C2). When ticked, the delivery-time
        # and check-alive date fields become editable everywhere (toolbar /
        # Heirs tab), not only inside the "Build your will" wizard. Bound to the
        # persisted EDITABLE_DATES config; the default is OFF (see
        # plugin_base.py). Changing it refreshes the open windows so the date
        # fields immediately become editable/read-only.
        heir_editable_dates = BalCheckBox(self.EDITABLE_DATES, on_multiverse_change)

        # Number of reminders spin box (Group D / D1). Sets how many reminder
        # alarms the exported .ics calendar event contains. Bound to the
        # persisted NUM_REMINDERS config (default 3), with a range of 1..5.
        heir_num_reminders = BalSpinBox(self.NUM_REMINDERS, minimum=1, maximum=5)

        # Max willexecutor fee spin box. Maximum fee (in satoshi) allowed for
        # a single will-executor. If a will-executor charges more, the will
        # will not be built. Default 500,000 satoshi (0.005 BTC).
        heir_max_willexecutor_fee = BalSpinBox(
            self.MAX_WILLEXECUTOR_FEE, minimum=0, maximum=10000000
        )

        # "No will-executor TX" checkbox. Bound to the persisted NO_WILLEXECUTOR
        # config (default ON, see plugin_base.py), the SAME config used by the
        # checkbox inside the "Build your will" wizard's will-executor download
        # window, so the two stay in sync automatically. When enabled the plugin
        # also builds a will that does not require a will-executor (e.g. it can
        # be saved on a USB stick and a copy given to the heirs).
        heir_no_willexecutor = BalCheckBox(self.NO_WILLEXECUTOR)

        # USER TYPE selector (SIMPLE / ADVANCED, global). A two-choice combo
        # (not a free-text field) bound to the USER_TYPE config:
        #   index 0 -> "BASIC"    -> stored value "basic"    (DEFAULT)
        #   index 1 -> "ADVANCED" -> stored value "advanced"
        #
        # BASIC hides the advanced controls (Raw/Date selector and the
        # "Check Alive" field) and disables the check-alive postpone behaviour.
        # Changing it refreshes the open windows so the controls appear/disappear
        # immediately. It is kept in a named variable so the "Reset" button can
        # restore its displayed value after a reset.
        user_type_combo = QComboBox()
        user_type_combo.addItems([_("BASIC"), _("ADVANCED")])
        # Map the stored string to the combo index (anything but "advanced"
        # falls back to BASIC, matching BalPlugin.is_basic_mode()).
        user_type_combo.setCurrentIndex(
            0 if str(self.USER_TYPE.get()).lower() != "advanced" else 1
        )

        def on_user_type_change(idx):
            # Persist "basic"/"advanced" and refresh the open windows so the
            # advanced controls appear/disappear right away.
            #
            # SAFETY GATE (owner request): enabling ADVANCED exposes powerful,
            # easy-to-misuse controls. Before switching to ADVANCED the user must
            # type a confirmation phrase ("at My Risk", case-insensitive). If the
            # phrase is wrong or the dialog is cancelled, we revert the combo to
            # BASIC and do NOT enable ADVANCED. Switching back to BASIC needs no
            # confirmation.
            if idx == 1:
                text, ok = QInputDialog.getText(
                    d,
                    _("Enable ADVANCED mode"),
                    _("Type 'at My Risk' to enable ADVANCED mode"),
                )
                # Accept any capitalisation (e.g. "at my risk", "AT MY RISK").
                if not ok or text.strip().lower() != "at my risk":
                    # Revert to BASIC. Block the signal while we reset the combo
                    # index so this handler is not called again recursively.
                    user_type_combo.blockSignals(True)
                    user_type_combo.setCurrentIndex(0)
                    user_type_combo.blockSignals(False)
                    self.USER_TYPE.set("basic")
                    self._apply_editor_default_on_toolbars()
                    self.update_all()
                    return
            self.USER_TYPE.set("advanced" if idx == 1 else "basic")
            # Show/hide the advanced-only rows.
            basic = idx != 1
            for w in (lbl_welist_server, edit_welist_server, help_welist_server,
                      lbl_num_reminders, heir_num_reminders, help_num_reminders,
                      lbl_event_summary, edit_event_summary, help_event_summary,
                      lbl_event_description, edit_event_description, help_event_description,
                      lbl_calendar_app, edit_calendar_app, help_calendar_app,
                      lbl_auto_sign, heir_auto_sign, help_auto_sign,
                      lbl_save_history, heir_save_history, help_save_history,
                      lbl_history_label, edit_history_label, help_history_label,
                      reset_btn_6, reset_btn_7, reset_btn_8, reset_btn_9, reset_btn_10,
                      reset_btn_11, reset_btn_12, reset_btn_auto_sign):
                w.setVisible(not basic)
            # Opzione 2: apply the per-mode Raw/Date editor default ONLY here, on
            # a real USER TYPE change (not inside update_all/CHECK), so pressing
            # CHECK never resets a manual Date/RAW choice. update_all() below
            # still refreshes the transaction list and the combo visibility.
            self._apply_editor_default_on_toolbars()
            self.update_all()

        user_type_combo.currentIndexChanged.connect(on_user_type_change)

        # Editable line/text widgets are created once and kept in named
        # variables so the "Reset" button (Group C / C4b) can refresh the
        # displayed values after resetting the underlying config.
        edit_event_summary = BalLineEdit(self.EVENT_SUMMARY)
        edit_event_summary.setMinimumWidth(360)
        edit_event_description = BalTextEdit(self.EVENT_DESCRIPTION)
        edit_event_description.setMinimumHeight(
            edit_event_description.fontMetrics().lineSpacing() * 3 + 10
        )
        edit_welist_server = BalLineEdit(self.WELIST_SERVER)
        edit_welist_server.setMinimumWidth(360)

        edit_calendar_app = BalLineEdit(self.CALENDAR_APP)
        edit_calendar_app.setMinimumWidth(360)

        # "Save inheritance transactions in wallet history" checkbox + label
        # field (History persistence). When the checkbox is ON, the valid will
        # transactions are saved into the wallet's LOCAL history (the History
        # tab) after each check, each tagged with the label below. The label
        # field is disabled while the checkbox is off, so the user cannot set a
        # label for a feature that is not active.
        def on_save_history_change():
            edit_history_label.setEnabled(bool(self.SAVE_HISTORY.get()))

        heir_save_history = BalCheckBox(self.SAVE_HISTORY, on_click=on_save_history_change)
        edit_history_label = BalLineEdit(self.HISTORY_LABEL)
        edit_history_label.setMinimumWidth(360)
        edit_history_label.setEnabled(bool(self.SAVE_HISTORY.get()))

        def _make_reset_btn(cfg, widget, kind):
            """Return a small ``↺`` button that resets a single setting."""
            btn = QPushButton("\u21ba")
            btn.setFixedWidth(24)
            btn.setToolTip(_("Reset to default"))
            def reset():
                cfg.set(cfg.default)
                if kind == "check":
                    widget.setChecked(bool(cfg.default))
                elif kind == "spin":
                    widget.setValue(int(cfg.default))
                elif kind == "line":
                    widget.setText(cfg.default)
                elif kind == "text":
                    widget.setPlainText(cfg.default)
                elif kind == "user_type":
                    widget.setCurrentIndex(
                        1 if str(cfg.default).lower() == "advanced" else 0
                    )
            btn.clicked.connect(reset)
            return btn

        heir_repush = QPushButton("Rebroadcast transactions")
        heir_repush.clicked.connect(partial(self.broadcast_transactions, True))
        bal_mode = QComboBox()
        options = ["Easy", "Advanced", "Experimental"]
        bal_mode.addItems(options)

        # Group C / C4a: warning shown at the very top of the dialog, in red and
        # bold, so the (non-technical) user is reminded not to touch these
        # settings unless they understand them. Placed above the grid via the
        # outer vertical layout below.
        lbl_warning = QLabel(
            _("Warning: change these settings only if you know what you are doing.")
        )
        lbl_warning.setStyleSheet("color: red; font-weight: bold;")
        lbl_warning.setWordWrap(True)

        # The grid is created WITHOUT a parent so it can be embedded inside an
        # outer QVBoxLayout together with the warning label (top) and the
        # Reset/support button row (bottom). Assigning ``QGridLayout(d)`` would
        # have made the grid the dialog's only layout, leaving no room for them.
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        # NOTE: the "User Type" row is placed at row 5, right before the
        # advanced-only settings, so the user picks basic/advanced first.
        add_widget(
            grid,
            "Hide Replaced",
            heir_hide_replaced,
            0,
            "Hide replaced transactions from will detail and list",
        )
        grid.addWidget(_make_reset_btn(self.HIDE_REPLACED, heir_hide_replaced, "check"), 0, 3)
        add_widget(
            grid,
            "Hide Invalidated",
            heir_hide_invalidated,
            1,
            "Hide invalidated transactions from will detail and list",
        )
        grid.addWidget(_make_reset_btn(self.HIDE_INVALIDATED, heir_hide_invalidated, "check"), 1, 3)
        lbl_auto_sign = QLabel(_("Auto-sign on Check"))
        help_auto_sign = HelpButton(
            "When checking, automatically sign and broadcast the will "
            "transactions to their will-executors.\n"
            "The wallet password is requested only if the wallet is "
            "encrypted."
        )
        grid.addWidget(_hide_if_basic(lbl_auto_sign), 2, 0)
        grid.addWidget(_hide_if_basic(heir_auto_sign), 2, 1)
        grid.addWidget(_hide_if_basic(help_auto_sign), 2, 2)
        reset_btn_auto_sign = _make_reset_btn(
            self.AUTO_SIGN, heir_auto_sign, "check"
        )
        grid.addWidget(_hide_if_basic(reset_btn_auto_sign), 2, 3)
        add_widget(
            grid,
            "Panel editable Date and Fee",
            heir_editable_dates,
            3,
            (
                "When enabled, the delivery-time and check-alive date fields "
                "can be edited everywhere (toolbar / Heirs tab), not only in "
                "the will-building wizard.\n"
                "When disabled, those dates are display-only outside the wizard."
            ),
        )
        grid.addWidget(_make_reset_btn(self.EDITABLE_DATES, heir_editable_dates, "check"), 3, 3)
        # "Add transaction without will-executor" setting (formerly labelled
        # "No will-executor TX"). When ON the plugin ALSO builds the backup
        # inheritance transaction that does NOT require a will-executor (the
        # "celeste"/light-blue one shown in the will list): it can be saved on a
        # USB stick and a copy handed to the heirs. When OFF only the
        # transactions destined to the selected will-executors are built.
        #
        # Placed here (row 5, right below "Panel editable Date and Fee" and above
        # "Number of reminders") at the user's request so related options sit
        # together. The remaining grid rows below were renumbered accordingly.
        add_widget(
            grid,
            "Add transaction without willexecutor",
            heir_no_willexecutor,
            4,
            (
                "Create a will that does not require a Will-executor; it can be "
                "saved, for example, on a USB stick, and a copy can be given to "
                "the heirs."
            ),
        )
        grid.addWidget(_make_reset_btn(self.NO_WILLEXECUTOR, heir_no_willexecutor, "check"), 4, 3)
        # Max willexecutor fee: maximum fee (in satoshi) allowed for a single
        # will-executor. Visible to all users (BASIC and ADVANCED).
        add_widget(
            grid,
            "Max Will-Executor Fee (satoshi)",
            heir_max_willexecutor_fee,
            5,
            (
                "Maximum fee (in satoshi) allowed to be paid to a single "
                "will-executor. If a will-executor charges more than this, "
                "the will will not be built.\n"
                "Default: 500,000 satoshi (0.005 BTC)."
            ),
        )
        grid.addWidget(_make_reset_btn(self.MAX_WILLEXECUTOR_FEE, heir_max_willexecutor_fee, "spin"), 5, 3)
        # User Type selector placed BEFORE the advanced-only settings so the
        # user chooses basic/advanced first, then sees the relevant options.
        add_widget(
            grid,
            "User Type",
            user_type_combo,
            6,
            (
                "Choose how much detail the plugin shows.\n\n"
                "BASIC: simplified interface, safe configuration for most "
                "users.\n\n"
                "ADVANCED: shows every control, including the 'Check Alive' "
                "field and the Raw/Date selector. Allows configuring the "
                "welist server URL and keeps all date/fee fields always "
                "editable."
            ),
        )
        grid.addWidget(_make_reset_btn(self.USER_TYPE, user_type_combo, "user_type"), 6, 3)
        # Number of reminders, event summary and event description are visible
        # only in ADVANCED mode.  In BASIC mode the factory defaults are always
        # used and these settings are hidden.
        lbl_num_reminders = QLabel(_("Number of reminders"))
        help_num_reminders = HelpButton(
            "How many reminder alarms the exported calendar (.ics) event "
            "contains.  Range: 1 to 5 (default 3).  Only used in ADVANCED mode."
        )
        grid.addWidget(_hide_if_basic(lbl_num_reminders), 7, 0)
        grid.addWidget(_hide_if_basic(heir_num_reminders), 7, 1)
        grid.addWidget(_hide_if_basic(help_num_reminders), 7, 2)
        reset_btn_6 = _make_reset_btn(self.NUM_REMINDERS, heir_num_reminders, "spin")
        grid.addWidget(_hide_if_basic(reset_btn_6), 7, 3)

        lbl_event_summary = QLabel(_("Event summary"))
        help_event_summary = HelpButton(
            "Default message to be used in event summary\n"
            "Variables:\n"
            "  $wallet_name: name of wallet\n"
            "  $heirs_complete: list of heirs name,address,amount\n"
            "Only used in ADVANCED mode."
        )
        grid.addWidget(_hide_if_basic(lbl_event_summary), 8, 0)
        grid.addWidget(_hide_if_basic(edit_event_summary), 8, 1)
        grid.addWidget(_hide_if_basic(help_event_summary), 8, 2)
        reset_btn_7 = _make_reset_btn(self.EVENT_SUMMARY, edit_event_summary, "line")
        grid.addWidget(_hide_if_basic(reset_btn_7), 8, 3)

        lbl_event_description = QLabel(_("Event description"))
        help_event_description = HelpButton(
            "Default message to be used in event description\n"
            "Variables:\n"
            "  $wallet_name: name of wallet\n"
            "  $heirs_complete: list of heirs name,address,amount\n"
            "Only used in ADVANCED mode."
        )
        grid.addWidget(_hide_if_basic(lbl_event_description), 9, 0)
        grid.addWidget(_hide_if_basic(edit_event_description), 9, 1)
        grid.addWidget(_hide_if_basic(help_event_description), 9, 2)
        reset_btn_8 = _make_reset_btn(self.EVENT_DESCRIPTION, edit_event_description, "text")
        grid.addWidget(_hide_if_basic(reset_btn_8), 9, 3)
        # Welist server URL: shown only in ADVANCED mode.  In BASIC mode the
        # factory default is always used and the setting is hidden.
        lbl_welist_server = QLabel(_("Welist Server URL"))
        help_welist_server = HelpButton(
            "URL of the server that provides the will-executor list.  "
            "Only available in ADVANCED mode."
        )
        grid.addWidget(_hide_if_basic(lbl_welist_server), 10, 0)
        grid.addWidget(_hide_if_basic(edit_welist_server), 10, 1)
        grid.addWidget(_hide_if_basic(help_welist_server), 10, 2)
        reset_btn_9 = _make_reset_btn(self.WELIST_SERVER, edit_welist_server, "line")
        grid.addWidget(_hide_if_basic(reset_btn_9), 10, 3)

        lbl_calendar_app = QLabel(_("Calendar app command"))
        help_calendar_app = HelpButton(
            "Command used to open .ics calendar files.\n"
            "Leave empty to use the system default (xdg-open/open/start).\n"
            "Only used in ADVANCED mode."
        )
        grid.addWidget(_hide_if_basic(lbl_calendar_app), 11, 0)
        grid.addWidget(_hide_if_basic(edit_calendar_app), 11, 1)
        grid.addWidget(_hide_if_basic(help_calendar_app), 11, 2)
        reset_btn_10 = _make_reset_btn(self.CALENDAR_APP, edit_calendar_app, "line")
        grid.addWidget(_hide_if_basic(reset_btn_10), 11, 3)

        # Save-in-history toggle and history label: advanced-only rows. The
        # label field is disabled while the checkbox is off (see
        # on_save_history_change above).
        lbl_save_history = QLabel(_("Save inheritance transactions in history"))
        help_save_history = HelpButton(
            "After each check, save the valid will transactions into the "
            "wallet's local history (the History tab), each with a label.\n"
            "The label may contain the variable:\n"
            "  {willexecutor}: replaced with the will-executor URL of the item\n"
            "Only used in ADVANCED mode."
        )
        grid.addWidget(_hide_if_basic(lbl_save_history), 12, 0)
        grid.addWidget(_hide_if_basic(heir_save_history), 12, 1)
        grid.addWidget(_hide_if_basic(help_save_history), 12, 2)
        reset_btn_11 = _make_reset_btn(self.SAVE_HISTORY, heir_save_history, "check")
        grid.addWidget(_hide_if_basic(reset_btn_11), 12, 3)

        lbl_history_label = QLabel(_("History label"))
        help_history_label = HelpButton(
            "Label applied to the will transactions saved into the wallet's "
            "local history.\n"
            "Variables:\n"
            "  {willexecutor}: replaced with the will-executor URL of the item\n"
            "Only used in ADVANCED mode."
        )
        grid.addWidget(_hide_if_basic(lbl_history_label), 13, 0)
        grid.addWidget(_hide_if_basic(edit_history_label), 13, 1)
        grid.addWidget(_hide_if_basic(help_history_label), 13, 2)
        reset_btn_12 = _make_reset_btn(self.HISTORY_LABEL, edit_history_label, "line")
        grid.addWidget(_hide_if_basic(reset_btn_12), 13, 3)

        # NOTE: the ADVANCED-only widgets above have ALREADY been given their
        # correct initial visibility inline (via _hide_if_basic) BEFORE being
        # added to the grid. The old code did the opposite - it added them
        # visible and then hid them here, all at once, which is what caused
        # the Windows relayout flicker. Do NOT reintroduce a post-hoc
        # setVisible() loop here.

        grid.addWidget(heir_repush, 14, 0)
        grid.addWidget(
            HelpButton(
                "Broadcast all transactions to willexecutors including those already pushed"
            ),
            14,
            2,
        )

        # ----------------------------------------------------------------- #
        # Group C / C4b: "Reset" button that restores the dialog settings to  #
        # their factory defaults. It only resets the settings exposed by THIS #
        # dialog (the ones below) and refreshes the corresponding widgets so   #
        # change is visible immediately. It deliberately does NOT touch the   #
        # wills, will-executors or any other configuration.                   #
        # ----------------------------------------------------------------- #
        def on_reset_defaults():
            """Reset the dialog settings to their defaults and refresh widgets.

            The default value of each setting is taken from ``BalConfig.default``
            (the third argument used when the config was created in
            ``plugin_base.py``), so there is a single source of truth and no
            hard-coded duplicates here.
            """
            # Map each config object to the widget that displays it, so we can
            # both reset the stored value and update what the user sees.
            resets = [
                (self.USER_TYPE, user_type_combo, "user_type"),
                (self.HIDE_REPLACED, heir_hide_replaced, "check"),
                (self.HIDE_INVALIDATED, heir_hide_invalidated, "check"),
                (self.AUTO_SIGN, heir_auto_sign, "check"),
                (self.EDITABLE_DATES, heir_editable_dates, "check"),
                (self.NUM_REMINDERS, heir_num_reminders, "spin"),
                (self.NO_WILLEXECUTOR, heir_no_willexecutor, "check"),
                (self.MAX_WILLEXECUTOR_FEE, heir_max_willexecutor_fee, "spin"),
                (self.EVENT_SUMMARY, edit_event_summary, "line"),
                (self.EVENT_DESCRIPTION, edit_event_description, "text"),
                (self.WELIST_SERVER, edit_welist_server, "line"),
                (self.CALENDAR_APP, edit_calendar_app, "line"),
                (self.SAVE_HISTORY, heir_save_history, "check"),
                (self.HISTORY_LABEL, edit_history_label, "line"),
            ]
            for cfg, widget, kind in resets:
                # Persist the default value back into the Electrum config.
                cfg.set(cfg.default)
                # Refresh the widget so the reset is immediately visible. The
                # widgets' own signal handlers will re-persist the same default
                # value, which is harmless.
                if kind == "check":
                    widget.setChecked(bool(cfg.default))
                elif kind == "spin":
                    widget.setValue(int(cfg.default))
                elif kind == "line":
                    widget.setText(cfg.default)
                elif kind == "text":
                    widget.setPlainText(cfg.default)
                elif kind == "user_type":
                    # Default is "basic" -> combo index 0; "advanced" -> index 1.
                    widget.setCurrentIndex(
                        1 if str(cfg.default).lower() == "advanced" else 0
                    )
            # Re-sync the history-label field's enabled state after a reset: the
            # reset restores SAVE_HISTORY to its default, so the field must
            # follow the (default) checkbox state again.
            edit_history_label.setEnabled(bool(self.SAVE_HISTORY.get()))
            # Refresh the open BAL windows so any dependent view (e.g. the
            # editable-dates state is not in this list, but hide filters are)
            # reflects the reset values.
            self.update_all()

        btn_reset = QPushButton(_("Reset to Default Setting"))
        btn_reset.setToolTip(_("Reset these settings to their default values"))
        btn_reset.clicked.connect(on_reset_defaults)

        # Group C / C4c: clickable support link to the project website.
        lbl_support = QLabel(
            '<a href="https://bitcoin-after.life"><b>bitcoin-after.life</b></a>'
        )
        lbl_support.setToolTip(_("Open the Bitcoin After Life support website"))
        # Open the link in the user's browser via Electrum's helper instead of
        # letting Qt open it directly, so it goes through Electrum's policy.
        lbl_support.setOpenExternalLinks(False)
        lbl_support.linkActivated.connect(
            lambda _url: webopen("https://bitcoin-after.life")
        )

        # Bottom row: Reset on the left, support link on the right.
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(btn_reset)
        bottom_row.addStretch(1)
        bottom_row.addWidget(QLabel("<b>" + _("Support:") + "</b>"))
        bottom_row.addWidget(lbl_support)

        # Outer layout: warning (top) -> settings grid -> bottom button row.
        outer = QVBoxLayout(d)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        outer.addWidget(lbl_warning)
        # Blank vertical gap below the red warning so it is not glued to the
        # first setting row ("User Type"); requested by the user for readability.
        outer.addSpacing(12)
        outer.addLayout(grid)
        outer.addLayout(bottom_row)

        if ret := bool(show_modal(d)):
            try:
                self.update_all()
                return ret
            except Exception:
                pass
        return False

    def broadcast_transactions(self, force):
        for _k, w in self.bal_windows.items():
            w.broadcast_transactions(force)

    def update_all(self):
        for _k, w in self.bal_windows.items():
            w.update_all()

    def _apply_editor_default_on_toolbars(self):
        """Apply the per-mode Raw/Date editor default to the WILL/HEIR toolbars.

        Called ONLY on a real USER TYPE (BASIC<->ADVANCED) change - never from
        update_all()/CHECK - so pressing CHECK does not reset a manual Date/RAW
        choice (Opzione 2). Guarded so a missing widget never breaks the switch.
        """
        for _k, w in self.bal_windows.items():
            for _list in (getattr(w, "heir_list_widget", None),
                          getattr(w, "will_list_widget", None)):
                settings_widget = getattr(_list, "will_settings_widget", None)
                apply_default = getattr(
                    settings_widget, "apply_user_type_editor_default", None
                )
                if callable(apply_default):
                    try:
                        apply_default()
                    except Exception as _e:
                        _logger.debug(
                            f"apply_user_type_editor_default error: {_e}"
                        )

    def get_window_title(self, title):
        return _("BAL - ") + _(title)


