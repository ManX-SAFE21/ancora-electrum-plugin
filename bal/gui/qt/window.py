"""
bal.gui.qt.window
=================

The :class:`BalWindow` controller: one instance per Electrum wallet window.

This is the orchestration layer that ties together the heirs list, the will
preview, the will-executors and the various dialogs.  It owns the per-wallet
state (``heirs``, ``will``, ``willitems``, ``will_settings``) and exposes the
high-level actions (build / check / sign / broadcast / invalidate the will,
import/export, etc.) that the menus, tabs and dialogs invoke.

The actual Bitcoin logic lives in :mod:`bal.core`; this class only coordinates
it with the GUI.
"""

import json
import threading

from electrum.util import MyEncoder

from ...core.checkalive import (
    CheckAliveError,
    check_alive_expired,
    resolve_date_to_check,
    resolve_guard_threshold,
)
from .common import (
    OP_RETURN_PREFIX,
    AmountException,
    BalPlugin,
    Buttons,
    CancelButton,
    ElectrumWindow,
    FileImportFailed,
    HeirChangeException,
    HeirNotFoundException,
    Heirs,
    HelpButton,
    Mapping,
    Network,
    NoHeirsException,
    NotCompleteWillException,
    NoWillExecutorNotPresent,
    OkButton,
    Optional,
    PaymentIdentifier,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTimer,
    QVBoxLayout,
    SerializationError,
    Transaction,
    TxDialog,
    TxFeesChangedException,
    Util,
    Will,
    WillexecutorChangeException,
    WillExecutorFeeTooHighException,
    WillExecutorNotPresent,
    Willexecutors,
    WillExpiredException,
    WillItem,
    WillPostponedException,
    _,
    _logger,
    char_width_in_lineedit,
    copy_structure,
    export_meta_gui,
    import_meta_gui,
    is_onion_url,
    is_op_return_address,
    is_tor_active,
    log_error,
    partial,
    read_json_file,
    read_QIcon_from_bytes,
    show_on_top,
    shown_cv,
    time,
    tx_from_any,
    write_json_file,
)
from .dialogs import (
    BalBuildWillDialog,
    BalDialog,
    BalWaitingDialog,
    BalWizardDialog,
    WillDetailDialog,
    WillExecutorDialog,
    WillExportDialog,
    WillImportDialog,
    _complete_import,
    decode_will_payload,
)
from .lists import HeirListWidget, PreviewList
from .widgets import LockTimeWidget, PercAmountEdit


class BalWindow:
    # Automatic rebuild-on-new-transaction flow (AUTO_REBUILD setting):
    # the debounce window collapses bursts of wallet events into one run, and
    # the cooldown prevents the flow from re-triggering right after a rebuild
    # (the freshly persisted txs can themselves fire wallet events).
    _AUTO_REBUILD_DEBOUNCE_MS = 5000
    _AUTO_REBUILD_COOLDOWN = 10.0

    def __init__(self, bal_plugin: "BalPlugin", window: "ElectrumWindow"):
        self.bal_plugin = bal_plugin
        self.window = window
        self.heirs = {}
        self.will = {}
        self.willitems = {}
        self.willexecutors = {}
        self.will_settings = None
        self.ok = False
        self.disable_plugin = True
        # Guard against wiring the menu/tabs more than once for the same window.
        # Electrum may invoke both the ``init_menubar`` hook and our hot-init
        # path (``init_qt`` -> ``_setup_window``) for the same window, e.g. when
        # Electrum restarts with the plugin already enabled.  Calling
        # ``init_menubar_tools`` twice would add the Heirs/Will tabs and the
        # menu actions twice, producing the garbled/condensed menu entry.
        self._menubar_initialized = False
        # Auto-rebuild flow state: re-entrancy guard and cooldown deadline.
        self._auto_rebuild_running = False
        self._auto_rebuild_cooldown_until = 0.0
        self.bal_plugin.get_decimal_point = self.window.get_decimal_point

        if self.window.wallet:
            self.wallet = self.window.wallet
            if not self.will_settings:
                self.will_settings = self.bal_plugin.WILL_SETTINGS.get()
                Util.fix_will_settings_tx_fees(self.will_settings)
            self.heirs = Heirs(self.wallet)

            self.heirs_tab = self.create_heirs_tab()
            self.will_tab = self.create_will_tab()
            self.heirs_tab.wallet = self.wallet
            self.will_tab.wallet = self.wallet

    def init_menubar_tools(self, tools_menu):
        # Idempotent: only wire the tabs + menu actions once per window.
        # A second call (e.g. init_menubar hook *and* the hot-init path both
        # firing) would otherwise duplicate the Heirs/Will tabs and the
        # Will-Executors / toggle actions, which Qt renders as a broken,
        # condensed menu entry under the Electrum logo.
        if self._menubar_initialized:
            _logger.info("init_menubar_tools: already initialised, skipping")
            return
        self._menubar_initialized = True
        self.tools_menu = tools_menu

        def add_optional_tab(tabs, tab, icon, description):
            tab.tab_icon = icon
            tab.tab_description = description
            tab.tab_pos = len(tabs)
            if tab.is_shown_cv.get():
                tabs.addTab(tab, icon, description.replace("&", ""))

        def add_toggle_action(tab):
            is_shown = tab.is_shown_cv.get()
            tab.menu_action = self.window.view_menu.addAction(
                tab.tab_description, lambda: self.window.toggle_tab(tab)
            )
            tab.menu_action.setCheckable(True)
            tab.menu_action.setChecked(is_shown)

        add_optional_tab(
            self.window.tabs,
            self.heirs_tab,
            read_QIcon_from_bytes(self.bal_plugin.read_file("icons/heir.png")),
            _("&Heirs"),
        )
        add_optional_tab(
            self.window.tabs,
            self.will_tab,
            read_QIcon_from_bytes(self.bal_plugin.read_file("icons/will.png")),
            _("&Will"),
        )
        tools_menu.addSeparator()
        self.tools_menu.willexecutors_action = tools_menu.addAction(
            _("&Will-Executors"), self.show_willexecutor_dialog
        )
        self.window.view_menu.addSeparator()
        add_toggle_action(self.heirs_tab)
        add_toggle_action(self.will_tab)

    def load_willitems(self):
        self.willitems = {}
        for wid, w in self.will.items():
            self.willitems[wid] = WillItem(w, wallet=self.wallet)
        if self.willitems:
            self.will_list_widget.will = self.willitems
            self.will_list_widget.update_will(self.willitems)
            self.will_tab.update()

    def save_willitems(self):
        keys = list(self.will.keys())
        for k in keys:
            del self.will[k]
        for wid, w in self.willitems.items():
            d = w.to_dict()
            # Store the tx in its serialized form: the wallet DB deep-copies
            # every value on save (JsonDB.put), and a live Transaction carrying
            # wallet-derived input info (utxo / script_descriptor, which hold a
            # threading.RLock) cannot be deep-copied.  Serializing to a string
            # matches how the will is re-read (get_will -> tx_from_any).
            d["tx"] = str(d["tx"])
            # Mirror the encoder the wallet DB uses: JsonDB.put returns False
            # (silently dropping the will) when a value cannot be serialized,
            # so prove it here and fail loudly instead.
            try:
                json.dumps(d, cls=MyEncoder)
            except Exception as e:
                _logger.error(f"save_willitems: will {wid} is not serializable: {e!r}")
                raise
            self.will[wid] = d

    def init_will(self):
        _logger.info("********************init_____will____________**********")
        if not self.willexecutors:
            self.willexecutors = Willexecutors.get_willexecutors(
                self.bal_plugin, update=False, bal_window=self
            )
        if not self.heirs:
            self.heirs = Heirs._validate(Heirs(self.wallet))
            self.heirs_tab.update()
        if not self.will:
            self.will = self.wallet.db.get_dict("will")
            Util.fix_will_tx_fees(self.will)
            if self.will:
                self.willitems = {}
                try:
                    self.load_willitems()
                except Exception:
                    self.disable_plugin = True
                    self.show_warning(
                        _("Please restart Electrum to activate the BAL plugin"),
                        title=_("Success"),
                    )
                    self.close_wallet()
                    return

        # if not self.will_settings:
        #    self.will_settings = self.wallet.db.get_dict("will_settings")
        #    Util.fix_will_settings_tx_fees(self.will_settings)

        #    _logger.info("will_settings: {}".format(self.will_settings))
        #    if not self.will_settings:
        #        Util.copy(self.will_settings, self.bal_plugin.default_will_settings())
        #        _logger.debug("not_will_settings {}".format(self.will_settings))
        #    self.bal_plugin.validate_will_settings(self.will_settings)
        #    self.heir_list_widget.update_will_settings()
        #    self.heir_list_widget.update()

    def init_wizard(self):
        wizard_dialog = BalWizardDialog(self)
        wizard_dialog.exec()

    def show_willexecutor_dialog(self):
        self.willexecutor_dialog = WillExecutorDialog(self)
        # Keep it in front of Electrum (window-modal) instead of letting it
        # fall behind the main window.
        show_on_top(self.willexecutor_dialog)

    def create_heirs_tab(self):
        if not self.heirs:
            self.heirs = Heirs(self.wallet)
        self.heir_list_widget = HeirListWidget(self, self.window)
        tab = self.window.create_list_tab(self.heir_list_widget)
        tab.is_shown_cv = shown_cv(False)
        return tab

    def create_will_tab(self):
        self.will_list_widget = PreviewList(self, self.window, None)
        tab = self.window.create_list_tab(self.will_list_widget)
        tab.is_shown_cv = shown_cv(True)
        return tab

    def new_heir_dialog(self, heir_key=None):
        heir = self.heirs.get(heir_key)
        title = "New heir"
        if heir:
            title = f"Edit: {heir_key}"

        d = BalDialog(
            self.window, self.bal_plugin, self.bal_plugin.get_window_title(_(title))
        )

        vbox = QVBoxLayout(d)
        grid = QGridLayout()

        heir_name = QLineEdit()
        heir_name.setFixedWidth(32 * char_width_in_lineedit())
        heir_address = QLineEdit()
        heir_address.setFixedWidth(32 * char_width_in_lineedit())
        heir_amount = PercAmountEdit(self.window.get_decimal_point)

        # OP_RETURN message field (hidden by default)
        op_return_message = QLineEdit()
        op_return_message.setFixedWidth(32 * char_width_in_lineedit())
        op_return_message.setVisible(False)
        op_return_amount_label = QLabel(_("Amount"))
        op_return_message_label = QLabel(_("OP_RETURN Message"))

        if heir:
            heir_name.setText(str(heir_key))
            addr = str(heir[0])
            heir_address.setText(addr)
            if not is_op_return_address(addr):
                heir_amount.setText(
                    str(Util.decode_amount(heir[1], self.window.get_decimal_point()))
                )
            self.heir_locktime = LockTimeWidget(self, self.window, heir[2])
        else:
            heir_address.setText("")
            self.heir_locktime = LockTimeWidget(self, self.window, self.will_settings["locktime"])

        def _update_op_return_from_message():
            msg = op_return_message.text()
            data_hex = msg.encode("utf-8").hex()
            heir_address.setText(OP_RETURN_PREFIX + data_hex)

        def _update_op_return_from_address():
            addr = heir_address.text()
            if is_op_return_address(addr):
                data_hex = addr[len(OP_RETURN_PREFIX):]
                try:
                    decoded = bytes.fromhex(data_hex).decode("utf-8", errors="replace")
                    op_return_message.setText(decoded)
                except Exception:
                    op_return_message.setText("")

        def _on_address_changed():
            addr = heir_address.text()
            if is_op_return_address(addr):
                if not op_return_message.isVisible():
                    op_return_message.setVisible(True)
                    heir_amount.setVisible(False)
                    op_return_amount_label.setVisible(False)
                    op_return_message_label.setVisible(True)
                op_return_message.blockSignals(True)
                _update_op_return_from_address()
                op_return_message.blockSignals(False)
            else:
                op_return_message.setVisible(False)
                heir_amount.setVisible(True)
                op_return_amount_label.setVisible(True)
                op_return_message_label.setVisible(False)

        _on_address_changed()
        heir_address.textChanged.connect(_on_address_changed)
        op_return_message.textChanged.connect(_update_op_return_from_message)

        new_heir_button = QPushButton(_("Add another heir"))
        self.add_another_heir = False

        def new_heir():
            self.add_another_heir = True
            d.accept()

        new_heir_button.clicked.connect(new_heir)

        grid.addWidget(QLabel(_("Name")), 1, 0)
        grid.addWidget(heir_name, 1, 1)
        grid.addWidget(HelpButton(_("Unique name or description about heir")), 1, 2)

        grid.addWidget(QLabel(_("Address")), 2, 0)
        grid.addWidget(heir_address, 2, 1)
        grid.addWidget(HelpButton(_("Bitcoin address or OP_RETURN: prefix + hex data")), 2, 2)

        grid.addWidget(op_return_amount_label, 3, 0)
        grid.addWidget(heir_amount, 3, 1)
        grid.addWidget(HelpButton(_("Fixed or Percentage amount if end with %")), 3, 2)

        grid.addWidget(op_return_message_label, 3, 0)
        grid.addWidget(op_return_message, 3, 1)

        locktime_label = QLabel(_("Locktime"))
        enable_multiverse = self.bal_plugin.ENABLE_MULTIVERSE.get()
        if enable_multiverse:
            grid.addWidget(locktime_label, 4, 0)
            grid.addWidget(self.heir_locktime, 4, 1)
            grid.addWidget(HelpButton(_("locktime")), 4, 2)

        vbox.addLayout(grid)
        buttons = [CancelButton(d), OkButton(d)]
        if not heir:
            buttons.append(new_heir_button)
        vbox.addLayout(Buttons(*buttons))
        while d.exec():
            raw_address = heir_address.text()
            if is_op_return_address(raw_address):
                amount = "0"
            else:
                amount = Util.encode_amount(heir_amount.text(), self.window.get_decimal_point())
            heir = [
                heir_name.text(),
                raw_address,
                amount,
                str(self.will_settings["locktime"]),
            ]
            try:
                self.set_heir(heir)
                if self.add_another_heir:
                    self.new_heir_dialog()
                break
            except Exception as e:
                self.show_error(str(e))

    def set_heir(self, heir):
        heir = list(heir)
        if is_op_return_address(heir[1]):
            heir[2] = "0"
        if not self.bal_plugin.ENABLE_MULTIVERSE.get():
            heir[3] = self.will_settings["locktime"]

        h = Heirs.validate_heir(heir[0], heir[1:])
        self.heirs[heir[0]] = h
        self.heir_list_widget.update()
        return True

    def delete_heirs(self, heirs):
        for heir in heirs:
            try:
                del self.heirs[heir]
            except Exception as e:
                _logger.debug(f"error deleting heir: {heir} {e}")
                pass
        self.heirs.save()
        self.heir_list_widget.update()
        return True

    def import_heirs(self):
        import_meta_gui(
            self.window,
            _("heirs"),
            self.heirs.import_file,
            self.heir_list_widget.update,
        )

    def export_heirs(self):
        export_meta_gui(self.window, "heirs.json", self.heirs.export_file)

    def prepare_will(self, ignore_duplicate=False, keep_original=False):
        will = self.build_inheritance_transaction(
            ignore_duplicate=ignore_duplicate, keep_original=keep_original
        )
        # Persist the freshly prepared transactions into the wallet's local
        # history (when SAVE_HISTORY is enabled). This runs on every successful
        # prepare -- including the "Prepare" menu action -- so the New txs show
        # up in History immediately. Abort paths return None and are skipped.
        if will:
            self._save_will_to_history()
        return will

    def delete_not_valid(self, txid, s_utxo):
        raise NotImplementedError()

    def update_will(self, will):
        Will.update_will(self.willitems, will)
        self.willitems.update(will)
        Will.normalize_will(self.willitems, self.wallet)

    def build_will(self, ignore_duplicate=True, keep_original=True):
        _logger.debug("building will...")
        # Drop stale wallet-LOCAL will placeholders saved by previous prepares
        # so their coins are available to this build (see remove_stale...).
        Will.remove_stale_wallet_history(
            self.window.wallet, self.bal_plugin.HISTORY_LABEL.get()
        )
        # A (re)build may have anticipated the delivery (shorter heir recipes)
        # while ``date_to_check`` is still anchored to the OLD built will. Using
        # that stale anchor as the build filter would block every future
        # delivery ("NO_FUTURE_DATE"). Recompute ``date_to_check`` for the will
        # that is being built: its locktime is the earliest future delivery
        # among the CURRENT heirs. The checks of the EXISTING will keep their
        # anchored ``date_to_check`` (set in init_class_variables).
        _new_locktime = min(
            (
                Util.parse_locktime_string(h[2])
                for h in self.heirs.values()
            ),
            default=None,
        )
        if _new_locktime:
            self.date_to_check = resolve_date_to_check(
                self.bal_plugin.is_basic_mode(),
                self.will_settings,
                built_locktime=_new_locktime,
            )
        will = {}
        # willtodelete = []
        # willtoappend = {}
        try:
            self.willexecutors = Willexecutors.get_willexecutors(
                self.bal_plugin, update=False, bal_window=self
            )
            if not self.no_willexecutor:

                f = False
                for _u, w in self.willexecutors.items():
                    if Willexecutors.is_selected(
                        w
                    ) and Willexecutors.is_valid(
                        w, max_fee=self.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
                        dust=self.window.wallet.dust_threshold()
                    ):
                        f = True
                if not f:
                    _logger.error("No Will-Executor or backup transaction selected")
                    raise NoWillExecutorNotPresent(
                        "No Will-Executor or backup transaction selected"
                    )
            # date_to_check already carries the correct reference timestamp for
            # the current mode (the Check Alive in ADVANCED, or "now" in BASIC -
            # see init_class_variables). So build the will directly against it;
            # no per-mode branch is needed here anymore. The available-UTXO view
            # restores coins that a newer, wallet-local will tx (stored in the
            # history with a later locktime) nominally spent.
            txs = self.heirs.get_transactions(
                self.bal_plugin,
                self.window.wallet,
                self.will_settings["baltx_fees"],
                Util.get_available_utxos(
                    self.window.wallet,
                    self.bal_plugin.HISTORY_LABEL.get(),
                    Will.get_min_locktime(
                        self.willitems, default_value=self.date_to_check
                    ),
                ),
                self.date_to_check,
            )

            _logger.info(f"txs built: {txs}")
            creation_time = time.time()
            if txs:
                for txid in txs:
                    # txtodelete = []
                    _break = False
                    tx = {}
                    tx["tx"] = txs[txid]
                    tx["my_locktime"] = txs[txid].my_locktime
                    tx["heirsvalue"] = txs[txid].heirsvalue
                    tx["description"] = txs[txid].description
                    tx["willexecutor"] = copy_structure(txs[txid].willexecutor)
                    tx["status"] = _("New")
                    tx["baltx_fees"] = txs[txid].tx_fees
                    tx["time"] = creation_time
                    tx["heirs"] = copy_structure(txs[txid].heirs)
                    tx["txchildren"] = []
                    will[txid] = WillItem(tx, _id=txid, wallet=self.wallet)
                self.update_will(will)
            else:
                _logger.info("No transactions was built")
                _logger.info(f"will-settings: {self.will_settings}")
                _logger.info(f"date_to_check:{self.date_to_check}")
                _logger.info(f"heirs: {self.heirs}")
                return {}
        except Exception as e:
            _logger.info(f"Exception build_will: {e}")
            raise e
            pass
        return self.willitems

    def check_will(self):
        result = Will.is_will_valid(
            self.willitems,
            self.date_to_check,
            self.will_settings["baltx_fees"],
            Util.get_available_utxos(
                self.window.wallet,
                self.bal_plugin.HISTORY_LABEL.get(),
                Will.get_min_locktime(
                    self.willitems, default_value=self.date_to_check
                ),
            ),
            heirs=self.heirs,
            willexecutors=self.willexecutors,
            self_willexecutor=self.no_willexecutor,
            wallet=self.wallet,
            callback_not_valid_tx=self.delete_not_valid,
        )
        return result

    def _save_will_to_history(self):
        """Persist the current will state into the wallet's LOCAL history.

        Runs after the will has been prepared/built/signed/checked (the
        "Prepare" action, the check dialog's phase 2 and the manual Sign
        action). When the SAVE_HISTORY setting is enabled,
        ``Will.save_valid_transactions_to_history`` stores the still "New" (not
        fully-signed) transactions under the configured label and removes
        entries for fully-signed ("Complete") and stale ones. The wallet tabs
        are then re-rendered through ``_refresh_after_history_save``.

        This must never raise: history persistence is a convenience on top of
        the will flows, so any failure is logged and ignored.
        """
        try:
            if not bool(self.bal_plugin.SAVE_HISTORY.get()):
                return
            Will.save_valid_transactions_to_history(
                self.willitems,
                self.wallet,
                self.bal_plugin.HISTORY_LABEL.get(),
            )
        except Exception as e:
            _logger.error(f"save_will_to_history failed: {e}")
        self._schedule_history_refresh()

    def _schedule_history_refresh(self):
        """Re-render the wallet tabs after the local history has changed.

        The actual refresh must run on the GUI thread (``HistoryModel.refresh``
        asserts that), so the call is marshalled through ``QTimer.singleShot``.
        Used after saving/removing will transactions in the local history and
        after a will rebuild, regardless of the calling thread.
        """
        QTimer.singleShot(0, self._refresh_after_history_save)

    def _refresh_after_history_save(self):
        """Re-render the wallet tabs after saving txs to the local history.

        ``update_tabs`` refreshes history plus the receive/send/address/coins
        lists; ``update_status`` refreshes the status-bar balance, which
        ``update_tabs`` does not touch. When ``update_tabs`` is not available we
        fall back to refreshing just the History tab.
        """
        if hasattr(self.window, "update_tabs"):
            self.window.update_tabs()
        elif hasattr(self.window, "history_list"):
            self.window.history_list.update()
        if hasattr(self.window, "update_status"):
            self.window.update_status()

    def show_message(self, text):
        self.window.show_message(text)

    def show_warning(self, text, parent=None, title=None):
        self.window.show_warning(text, parent=parent, title=title)

    def show_error(self, text):
        self.window.show_error(text)

    def show_critical(self, text):
        self.window.show_critical(text)

    def update_combo_setting_widgets(
        self,
        new_value,
        field,
        update_all=False,
        update_will_dialog=False,
        update_heirs_dialog=False,
    ):
        if (update_all or update_will_dialog) and hasattr(self,'will_list_widget'):
            self.update_widget_combo(self.will_list_widget,field,new_value)
        if update_all or update_heirs_dialog and hasattr(self,'heir_list_widget'):
            self.update_widget_combo(self.heir_list_widget,field,new_value)


    def update_widget_combo(self,widget,field,value):
        try:
            widget.will_settings_widget.widgets[field].set_index(value)
        except Exception as _e:
            pass
    def update_widget_value(self, widget, field, value):
        try:
            widget.will_settings_widget.widgets[field].set_value(value)
        except Exception as _e:
            pass

    def update_setting_widgets(
        self,
        new_value,
        field,
        update_all=False,
        update_will_dialog=False,
        update_heirs_dialog=False,
    ):
        if update_all or update_heirs_dialog:
            self.update_widget_value(self.heir_list_widget, field, new_value)
        if update_all or update_will_dialog:
            self.update_widget_value(self.will_list_widget, field, new_value)
        self.will_settings[field] = new_value
        self.bal_plugin.WILL_SETTINGS.set(self.will_settings)

    def init_heirs_to_locktime(self, multiverse=False):
        if multiverse:
            return
        # Coerce the locktime to a plain serializable scalar: will_settings is
        # read from Electrum's config and a non-primitive value here would end
        # up inside the heirs dict and break json_db persistence (this was one
        # path to the "cannot pickle '_thread.RLock' object" error).
        locktime = self.will_settings["locktime"]
        if not isinstance(locktime, (int, float, str)):
            locktime = str(locktime)
        # Iterate over a snapshot of the keys: assigning to self.heirs[...]
        # triggers Heirs.__setitem__ -> save(), which mutates the mapping while
        # we iterate it.  Building the new values first and applying them after
        # the loop avoids "dict changed size during iteration" and the repeated
        # save() on every heir.
        updates = {
            heir: [self.heirs[heir][0], self.heirs[heir][1], locktime]
            for heir in list(self.heirs)
        }
        for heir, value in updates.items():
            self.heirs[heir] = value

    def init_class_variables(self):
        if not self.heirs:
            raise NoHeirsException(_("Heirs are not defined"))
        try:
            # SIMPLE / ADVANCED root behaviour of the "Check Alive" (threshold).
            #
            # self.date_to_check is the single reference timestamp that EVERY
            # downstream validity check reads: the build filter
            # (get_locktimes/get_transactions), the heir-count in
            # check_willexecutors_and_heirs ("No Heirs"), check_will_expired,
            # check_amounts, and the "locktime is lower than threshold" guard.
            #
            # In BASIC mode the Check Alive is HIDDEN and NOT editable by the
            # user, so it must never govern any of those checks. Setting
            # date_to_check to the Check Alive there caused the will to be
            # wrongly blocked whenever the (fixed) Check Alive ended up later
            # than the delivery time (e.g. "No Heirs" even with heirs present,
            # or a will that refused to (re)build). We therefore set
            # date_to_check to "now" in BASIC: every check is then evaluated
            # against the current moment, i.e. the Check Alive effectively does
            # not exist, while the delivery time (locktime) is still fully
            # enforced. ADVANCED mode keeps the user-controlled Check Alive
            # exactly as before. The policy itself lives in
            # ``bal.core.checkalive.resolve_date_to_check``.
            self.date_to_check = resolve_date_to_check(
                self.bal_plugin.is_basic_mode(),
                self.will_settings,
                built_locktime=Will.get_min_locktime(self.willitems),
            )
            # found = False
            # NOTE: block-height tracking removed (A1) - locktimes are always
            # UNIX timestamps now, so we no longer read the current block height
            # or compute a block_to_check here. Validity is decided purely by
            # comparing locktimes against date_to_check (a timestamp).
            self.no_willexecutor = self.bal_plugin.NO_WILLEXECUTOR.get()
            self.willexecutors = Willexecutors.get_willexecutors(
                self.bal_plugin, update=True, bal_window=self, task=False
            )
            # SIMPLE / ADVANCED: in BASIC mode the "Check Alive" parameter must
            # behave AS IF IT DID NOT EXIST. The check-alive threshold drives
            # the "you are alive -> postpone the inheritance" prompt; raising
            # CheckAliveError here is what triggers that postpone/invalidate
            # flow. In BASIC we therefore SKIP this check entirely, so a passed
            # check-alive date never forces a postpone/rewrite of the will. The
            # delivery time (locktime) is unaffected and still fully enforced.
            # The BASIC/ADVANCED rule lives in
            # ``bal.core.checkalive.check_alive_expired``.
            if check_alive_expired(
                self.bal_plugin.is_basic_mode(), self.date_to_check
            ):
                raise CheckAliveError(self.date_to_check)

            self.init_heirs_to_locktime(self.bal_plugin.ENABLE_MULTIVERSE.get())

        except Exception as e:
            log_error(e )
            _logger.error(f"init_class_variables: {e}")

            raise e

    def is_locktime_below_threshold(self) -> bool:
        """True when the stored settings make the delivery earlier than the
        Check Alive threshold (the "locktime is lower than threshold" guard).

        Compares the delivery against the settings-derived threshold on the
        SAME reference frame (see ``resolve_guard_threshold``), never against
        the built-will-anchored ``date_to_check``: anchoring the guard to an
        old, longer built will would wrongly fire right after the delivery was
        shortened.  The anchored reference still governs the validity and
        expiry checks, which is where ``date_to_check`` belongs.
        In BASIC mode there is no threshold, so the locktime is checked against
        ``date_to_check`` (= now) exactly as before.
        """
        locktime = Util.parse_locktime_string(self.will_settings["locktime"])
        threshold_ts = resolve_guard_threshold(
            self.bal_plugin.is_basic_mode(), self.will_settings
        )
        if threshold_ts is not None:
            return locktime < threshold_ts
        return self.date_to_check is not None and locktime < self.date_to_check

    def build_inheritance_transaction(self, ignore_duplicate=True, keep_original=True):
        try:
            _logger.info(
                "BAL-plugin \u25b8 STEP 1/7: prepare inheritance "
                "(validate settings, amounts and locktime)"
            )
            if self.disable_plugin:
                _logger.info("plugin is disabled")
                return
            if not self.heirs:
                _logger.warning("not heirs {}".format(self.heirs))
                return
            # Free the coins locked by stale wallet-LOCAL will placeholders
            # BEFORE the amount/UTXO checks below (Step 1) see them.
            Will.remove_stale_wallet_history(
                self.window.wallet, self.bal_plugin.HISTORY_LABEL.get()
            )
            try:
                self.init_class_variables()
                Will.check_amounts(
                    self.heirs,
                    self.willexecutors,
                    Util.get_available_utxos(
                        self.window.wallet,
                        self.bal_plugin.HISTORY_LABEL.get(),
                        Will.get_min_locktime(
                            self.willitems, default_value=self.date_to_check
                        ),
                    ),
                    self.date_to_check,
                    self.window.wallet.dust_threshold(),
                    max_fee=self.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
                )
            except AmountException as e:
                self.show_warning(
                    _(
                        f"In the inheritance process, the entire wallet will always be fully emptied. Your settings require an adjustment of the amounts.{e}"
                    )
                )
            except WillExecutorFeeTooHighException as e:
                self.show_error(
                    _(f"Will-executor fee too high: {e}")
                )
                return
            except CheckAliveError:
                self.show_error(
                    _(
                        "CheckAlive is in the past please update it to a date in the future but less than locktime"
                    )
                )
                return
            if self.is_locktime_below_threshold():
                self.show_error(_("locktime is lower than threshold"))
                return
            if not self.no_willexecutor:
                f = False
                for _k, we in self.willexecutors.items():
                    if Willexecutors.is_selected(
                        we
                    ) and Willexecutors.is_valid(
                        we, max_fee=self.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
                        dust=self.window.wallet.dust_threshold()
                    ):
                        f = True
                if not f:
                    self.show_error(
                        _(" no backup transaction or willexecutor selected")
                    )
                    return

            try:
                _logger.info(
                    "BAL-plugin \u25b8 STEP 2/7: checking if the current will is "
                    "still coherent (heirs, will-executors, fees, locktime)"
                )
                self.check_will()
                _logger.info(
                    "BAL-plugin \u25b8 STEP 2/7 result: will is COHERENT, "
                    "nothing to rebuild"
                )
            except WillExpiredException:
                _logger.info(
                    "BAL-plugin \u25b8 STEP 2/7 result: will EXPIRED -> "
                    "invalidating on-chain (real fee)"
                )
                self.invalidate_will()
                return
            except NoHeirsException:
                _logger.info(
                    "BAL-plugin \u25b8 STEP 2/7 result: no valid heirs -> abort"
                )
                return
            except WillPostponedException as e:
                # The will was already signed/sent and is being postponed.
                # We do NOT rebuild automatically: the user must first sign and
                # broadcast the invalidation tx (so the old, earlier-locktime tx
                # can never be used by a will-executor), then press "Prepare"
                # again
                # to create the new postponed inheritance.
                _logger.info(
                    "BAL-plugin \u25b8 STEP 2/7 result: will POSTPONED on a "
                    "signed/sent tx -> must invalidate on-chain first (real fee)"
                )
                _logger.info(f"will postponed: {e}")
                self.show_message(
                    _(
                        "This inheritance was already signed/sent to "
                        "will-executors and you are postponing it.\n\n"
                        "The previously committed coins must be invalidated "
                        "on-chain FIRST, otherwise a will-executor could "
                        "broadcast the old (earlier) transaction and execute "
                        "the inheritance too early.\n\n"
                        "Please sign and broadcast the invalidation transaction "
                        "now, then press 'Prepare' again to create the new "
                        "(postponed) inheritance."
                    )
                )
                self.invalidate_will()
                return
            except NotCompleteWillException as e:
                _logger.info(
                    "BAL-plugin \u25b8 STEP 2/7 result: will NOT coherent -> "
                    "REBUILD needed (no on-chain fee)"
                )
                _logger.info("{}:{}".format(type(e), e))
                message = False
                if isinstance(e, HeirChangeException):
                    message = "Heirs changed:"
                elif isinstance(e, WillExecutorNotPresent):
                    message = "Will-Executor not present:"
                elif isinstance(e, WillexecutorChangeException):
                    message = "Will-Executor changed"
                elif isinstance(e, TxFeesChangedException):
                    message = "Txfees are changed"
                elif isinstance(e, HeirNotFoundException):
                    # Task #01b: replace the misleading "Heir not found" text.
                    # This branch is most often hit because the delivery date
                    # was anticipated, not because an heir is missing, so we use
                    # a clear message that covers both the DATE and the HEIRS
                    # cases (kept consistent with dialogs.py / the CHECK window).
                    message = (
                        "Found CHANGES to the DATE or the HEIRS,\n"
                        "a NEW WILL must be prepared."
                    )

                if message:
                    self.show_message(
                        f"{_(message)}:\n {e}\n{_('will have to be built')}"
                    )

                _logger.info("build will")
                self.build_will(ignore_duplicate, keep_original)

                # Track whether the rebuild produced a coherent, ready-to-sign
                # will, so we can guide the user through the remaining manual
                # steps (Sign + Broadcast) afterwards.
                rebuilt_ok = False
                try:
                    self.check_will()
                    for wid, _w in self.willitems.items():
                        # Label shown in Electrum's History tab for inheritance txs.
                        self.wallet.set_label(wid, "BAL Inheritance transaction")
                    rebuilt_ok = True
                except WillExpiredException as e:
                    self.invalidate_will()
                except NotCompleteWillException as e:
                    self.show_error(
                        "Error:{}\n {}".format(
                            str(e),
                            _("Please, check your heirs, locktime and threshold!"),
                        )
                    )

                self._schedule_history_refresh()

                # Guide the user: the inheritance was just (re)built and is now
                # in the "New" state, so it must be SIGNED and then BROADCAST
                # again -- two manual steps the user has to perform.  Without
                # this hint the user is left with a freshly rebuilt will and no
                # indication that it still needs to be signed and re-sent to the
                # will-executors.
                if rebuilt_ok:
                    if self.no_willexecutor:
                        next_steps = _(
                            "Your inheritance has been rebuilt and now needs "
                            "to be signed again.\n\n"
                            "Next step (manual):\n"
                            "  1. Press 'Sign' to sign the new transaction."
                        )
                    else:
                        next_steps = _(
                            "Your inheritance has been rebuilt and now needs "
                            "to be signed and re-sent to the will-executors.\n\n"
                            "Next steps (manual):\n"
                            "  1. Press 'Sign' to sign the new transaction.\n"
                            "  2. Press 'Broadcast' to send it to the "
                            "will-executors."
                        )
                    self.show_message(next_steps)
            self.update_all()
            return self.willitems
        except Exception as e:
            raise e

    def show_transaction_real(
        self,
        tx: Transaction,
        *,
        parent: "ElectrumWindow",
        prompt_if_unsaved: bool = False,
        external_keypairs: Mapping[bytes, bytes] = None,
        payment_identifier: "PaymentIdentifier" = None,
    ):
        try:
            d = TxDialog(
                tx,
                parent=parent,
                prompt_if_unsaved=prompt_if_unsaved,
                external_keypairs=external_keypairs,
                # payment_identifier=payment_identifier,
            )
            d.setWindowIcon(
                read_QIcon_from_bytes(self.bal_plugin.read_file("icons/bal16x16.png"))
            )
        except SerializationError as e:
            _logger.error("unable to deserialize the transaction")
            parent.show_critical(
                _("Electrum was unable to deserialize the transaction:") + "\n" + str(e)
            )
        else:
            # Electrum's own TxDialog: keep it in front of the main window.
            show_on_top(d, modal_to_window=False)
            return d

    def show_transaction(self, tx=None, txid=None, parent=None):
        if not parent:
            parent = self.window
        if txid is not None and txid in self.willitems:
            tx = self.willitems[txid].tx
        if not tx:
            raise Exception(_("no tx"))
        return self.show_transaction_real(tx, parent=parent)

    def invalidate_will(self, will=None):
        # The reference timestamp is normally set by init_class_variables();
        # fall back to "now" so a first-action invalidation always has it.
        if not hasattr(self, "date_to_check") or self.date_to_check is None:
            self.date_to_check = resolve_date_to_check(
                self.bal_plugin.is_basic_mode(), self.will_settings
            )

        def on_success(result):
            if result:
                self.show_message(
                    _(
                        "Please sign and broadcast this transaction to invalidate current will"
                    )
                )
                # Label shown in Electrum's History tab for invalidate txs.
                self.wallet.set_label(result.txid(), "BAL Invalidate transaction")
                self.show_transaction(result)
            else:
                self.show_message(_("No transactions to invalidate"))

        def on_failure(exec_info):
            log_error(exec_info, self)

        willitems = will if will is not None else self.willitems
        fee_per_byte = self.will_settings.get("baltx_fees", 1)
        task = partial(
            Will.invalidate_will,
            willitems,
            self.wallet,
            fee_per_byte,
            history_label=self.bal_plugin.HISTORY_LABEL.get(),
            will_locktime=Will.get_min_locktime(
                willitems, default_value=self.date_to_check
            ),
        )
        msg = _("Calculating Transactions")
        self.waiting_dialog = BalWaitingDialog(
            self, msg, task, on_success, on_failure, exe=False
        )
        self.waiting_dialog.exe()

    def sign_transactions(self, password, will=None, txids=None):
            try:
                willitems = will if will is not None else self.willitems
                txs = {}
                signed = None
                tosign = None

                def get_message():
                    msg = ""
                    if signed:
                        msg = _(f"signed: {signed}\n")
                    return msg + _(f"signing: {tosign}")

                if txids is not None:
                    targets = [
                        t for t in txids
                        if t in willitems and willitems[t].get_status("VALID")
                    ]
                else:
                    targets = Will.only_valid(willitems)
                for txid in targets:
                    wi = willitems[txid]
                    if wi.get_status("COMPLETE"):
                        # Already signed and complete: keep as-is (the single-tx
                        # helper short-circuits without touching the wallet).
                        tx, _ = self._prepare_and_sign_tx(willitems, txid, password)
                        txs[txid] = tx
                        continue
                    tosign = txid
                    try:
                        self.waiting_dialog.update(get_message())
                    except Exception:
                        pass
                    tx, _signed = self._prepare_and_sign_tx(willitems, txid, password)
                    signed = tosign
                    txs[txid] = tx
            except Exception:
                return None
            return txs

    def _prepare_and_sign_tx(self, willitems, txid, password):
        """Prepare one will transaction and sign it.

        Shared by the batch signer (:meth:`sign_transactions`) and the
        per-transaction review wizard of the QR import flow
        (:class:`WillTxReviewSignDialog`).

        Returns ``(tx, newly_signed)``: ``newly_signed`` is False when the
        transaction was already COMPLETE (nothing was signed).
        """
        wi = willitems[txid]
        # Do NOT deepcopy: the stored tx carries wallet-derived objects
        # (utxo / script_descriptor) that hold a threading.RLock, and
        # copy.deepcopy raises "cannot pickle '_thread.RLock'".  Re-parse
        # from the serialized form instead, which is exactly how the will
        # is persisted/loaded (WillItem.to_dict -> serialize -> tx_from_any).
        tx = Will.get_tx_from_any(str(wi.tx))
        if wi.get_status("COMPLETE"):
            return tx, False
        for txin in tx.inputs():
            prevout = txin.prevout.to_json()
            if prevout[0] in willitems:
                change = willitems[prevout[0]].tx.outputs()[prevout[1]]
                txin._trusted_value_sats = change.value
                try:
                    txin.script_descriptor = change.script_descriptor
                except Exception:
                    pass
                txin.is_mine = True
                txin._TxInput__address = change.address
                txin._TxInput__scriptpubkey = change.scriptpubkey
                txin._TxInput__value_sats = change.value
                txin._trusted_value_sats = change.value

        self.wallet.sign_transaction(tx, password, ignore_warnings=True)
        if tx.is_complete():
            wi.set_status("COMPLETE", True)
        # Refresh the per-item signature counts from the freshly signed
        # partial tx: at this point the signatures are still present
        # (before any finalization), so the will list can show the real
        # "added/required" count (e.g. "1/2" for a multisig).
        try:
            have, required = tx.signature_count()
            wi.sigs_have = int(have)
            wi.sigs_required = int(required)
        except Exception as e:
            _logger.debug(f"signature_count after signing failed: {e}")
        return tx, True

    def get_wallet_password(self, message=None, parent=None):
        parent = self.window if not parent else parent
        password = None
        if self.wallet.has_keystore_encryption():
            password = self.bal_plugin.password_dialog(parent=parent, msg=message)
            if password is None:
                return False
            try:
                self.wallet.check_password(password)
            except Exception as e:
                self.show_error(str(e))
                password = self.get_wallet_password(message)
        return password

    # ------------------------------------------------------------------ #
    # Automatic rebuild on new transactions (AUTO_REBUILD)
    #
    # When the AUTO_REBUILD setting is enabled, wallet activity (a new
    # transaction / a sync update) schedules the headless rebuild flow below,
    # which reproduces EXACTLY what the "Build your will" wizard does at wallet
    # close (task_phase1 / task_phase2):
    #
    #   * the delivery date of the rebuilt transactions is anticipated by one
    #     day (Will.search_anticipate -> check_anticipate) so the new will
    #     mines BEFORE the previous one and orphans it WITHOUT an on-chain
    #     invalidation transaction;
    #   * an on-chain invalidation transaction is built ONLY when the
    #     anticipated locktime would fall before the check-alive threshold
    #     (post-build check_will -> WillExpiredException), or when the
    #     threshold is already in the past (CheckAliveError) - the same two
    #     conditions that trigger invalidation in the wizard.
    # ------------------------------------------------------------------ #

    def schedule_auto_rebuild(self, delay_ms=None):
        """Debounced entry point for the auto-rebuild flow.

        Called by ``Plugin._wallet_activity`` (on the asyncio callback thread)
        whenever a transaction/update is seen for this wallet.  The actual
        rebuild is deferred through ``QTimer`` (thread-safe to schedule, runs
        on the GUI thread) so a burst of events collapses into a single run.
        """
        try:
            delay = delay_ms if delay_ms is not None else self._AUTO_REBUILD_DEBOUNCE_MS
            QTimer.singleShot(delay, self._run_auto_rebuild)
        except Exception as e:
            _logger.debug("schedule_auto_rebuild failed: {}".format(e))

    def _run_auto_rebuild(self):
        """GUI-thread guard before launching the auto-rebuild worker.

        Checks the cheap guards that must be evaluated on the GUI thread and,
        when allowed, runs the headless flow in a background thread so the
        interface is not frozen (signing/pushing can take a while).
        """
        if not self._auto_rebuild_allowed():
            return
        self._auto_rebuild_running = True
        threading.Thread(target=self._auto_rebuild_worker, daemon=True).start()

    def _auto_rebuild_worker(self):
        try:
            self._auto_rebuild_flow()
        except Exception as e:
            _logger.error("auto rebuild worker failed: {}".format(e))
        finally:
            self._auto_rebuild_running = False
            QTimer.singleShot(0, self._after_auto_rebuild)

    def _auto_rebuild_allowed(self):
        """Cheap guards evaluated before running the auto-rebuild flow."""
        if self.disable_plugin or not self.ok:
            return False
        if not self.bal_plugin.AUTO_REBUILD.get():
            return False
        if not self.willitems:
            return False
        if self._auto_rebuild_running:
            return False
        if time.time() < self._auto_rebuild_cooldown_until:
            return False
        return True

    def maybe_auto_rebuild(self):
        """Run the headless auto-rebuild flow synchronously on this thread.

        This is the testable entry point (and what the background worker
        runs): it reproduces the wizard's close-time flow and returns True when
        it rebuilt/invalidated the will, False when there was nothing to do.
        """
        if not self._auto_rebuild_allowed():
            return False
        self._auto_rebuild_running = True
        try:
            result = self._auto_rebuild_flow()
        finally:
            self._auto_rebuild_running = False
            QTimer.singleShot(0, self._after_auto_rebuild)
        return result

    def _after_auto_rebuild(self):
        """Refresh the will tabs after an auto-rebuild (GUI thread)."""
        try:
            self.update_all()
        except Exception as e:
            _logger.debug("_after_auto_rebuild update_all failed: {}".format(e))
        try:
            if hasattr(self, "will_list_widget"):
                self.will_list_widget.update()
        except Exception:
            pass

    def _auto_rebuild_flow(self):
        """Core headless rebuild flow (mirrors the wizard's close flow).

        Returns True when the will was rebuilt or invalidated, False when there
        was nothing to do.  Runs on the caller's thread.
        """
        try:
            self._auto_rebuild_cooldown_until = (
                time.time() + self._AUTO_REBUILD_COOLDOWN
            )
            _logger.info("auto rebuild: checking will after wallet activity")

            # 1) Recompute date_to_check / willexecutors exactly like
            #    init_class_variables does at the start of the wizard's phase 1.
            #    A Check Alive threshold already in the past (ADVANCED mode)
            #    means the old will must be invalidated on-chain.
            try:
                self.init_class_variables()
            except CheckAliveError:
                _logger.info("auto rebuild: check-alive threshold passed -> invalidate")
                self._auto_invalidate_will()
                return True
            except NoHeirsException:
                _logger.info("auto rebuild: no heirs, nothing to rebuild")
                return False

            # 2) Check the current will against the freshly computed reference
            #    date.  A still-valid will needs no rebuild.
            try:
                self.check_will()
                _logger.debug("auto rebuild: will is still valid, nothing to do")
                return False
            except (WillExpiredException, WillPostponedException) as e:
                # Expired ("too late to anticipate") or a postpone on a
                # signed/sent will: the old coins must be invalidated on-chain
                # first.
                _logger.info(
                    "auto rebuild: {} -> invalidate".format(type(e).__name__)
                )
                self._auto_invalidate_will()
                return True
            except NoHeirsException:
                return False
            except NotCompleteWillException:
                # The will no longer covers the wallet's current UTXOs / heirs
                # / date: rebuild it.  The rebuild automatically anticipates
                # the delivery date by one day when the same coins/heirs are
                # involved (Will.search_anticipate), so the new transactions
                # mine before the previous ones.
                pass

            # 3) Rebuild.
            try:
                txs = self.build_will()
            except Exception as e:
                _logger.error("auto rebuild: build_will failed: {}".format(e))
                return False
            if not txs:
                _logger.info("auto rebuild: nothing was built")
                return False

            # 4) Re-validate the freshly built will (mirrors task_phase1 after
            #    build_will).  If the anticipated locktime now falls before the
            #    check-alive threshold, the previous will must be invalidated
            #    on-chain before the new one is used - and we STOP, exactly like
            #    the wizard ("invalidate_classic"): signing/pushing the new will
            #    while the invalidation is not confirmed would race it for the
            #    same inputs.  The next wallet event / manual Check continues
            #    once the invalidation confirms.
            try:
                self.check_will()
            except (WillExpiredException, WillPostponedException) as e:
                _logger.info(
                    "auto rebuild: anticipated locktime crossed threshold "
                    "({}) -> invalidate old will".format(type(e).__name__)
                )
                self._auto_invalidate_will()
                return True
            except NoHeirsException:
                return False
            except NotCompleteWillException:
                # The freshly rebuilt transactions simply need signing.
                pass
            except Exception as e:
                _logger.error(
                    "auto rebuild: post-build check failed: {}".format(e)
                )
                return False

            # 5) Sign (passwordless wallets only, headlessly), persist and push
            #    the rebuilt transactions to their will-executors: pushing the
            #    earlier-locktime transactions is what makes them orphan the
            #    previous ones.
            self._auto_sign_save_push()
            return True
        finally:
            # Always apply the cooldown so a burst of events (or the wallet
            # events fired by our own persistence) cannot loop forever.
            self._auto_rebuild_cooldown_until = (
                time.time() + self._AUTO_REBUILD_COOLDOWN
            )

    def _auto_invalidate_will(self, will=None):
        """Build, sign and broadcast the on-chain invalidation tx, headlessly.

        Reuses the exact recipe of the wizard's ``loop_broadcast_invalidating``
        (label set before broadcast, tx info pulled from wallet/network,
        broadcast timeout 120s) without any dialog.  An encrypted wallet cannot
        sign headlessly, so we stop with a logged warning and leave the
        invalidation to the user's manual flow.
        """
        willitems = will if will is not None else self.willitems
        try:
            tx = Will.invalidate_will(
                willitems,
                self.wallet,
                self.will_settings.get("baltx_fees", 1),
                history_label=self.bal_plugin.HISTORY_LABEL.get(),
                will_locktime=Will.get_min_locktime(
                    willitems,
                    default_value=getattr(self, "date_to_check", None),
                ),
            )
        except Exception as e:
            _logger.error("auto invalidate: could not build tx: {}".format(e))
            return None
        if not tx:
            _logger.info("auto invalidate: no transactions to invalidate")
            return None
        try:
            if self.wallet.has_keystore_encryption():
                _logger.warning(
                    "auto invalidate: wallet is encrypted; signing the "
                    "invalidation requires the password -> invalidate manually"
                )
                return None
            network = getattr(self.wallet, "network", None)
            if network is None:
                _logger.error("auto invalidate: no network, cannot broadcast")
                return None
            tx = self.wallet.sign_transaction(tx, None, ignore_warnings=True)
            if not tx or not tx.is_complete():
                raise Exception("invalidation tx not complete")
            tx.add_info_from_wallet(self.wallet)
            network.run_from_another_thread(tx.add_info_from_network(network))
            txid = tx.txid()
            if txid:
                # Label BEFORE broadcasting so the History tab shows it the
                # moment the tx appears (matches the wizard behaviour).
                self.wallet.set_label(txid, "BAL Invalidate transaction")
            network.run_from_another_thread(
                network.broadcast_transaction(tx, timeout=120), timeout=120
            )
            _logger.info("auto invalidate: broadcast invalidation {}".format(txid))
            return tx
        except Exception as e:
            _logger.error("auto invalidate failed: {}".format(e))
            return None

    def _auto_sign_save_push(self):
        """Headless sign + persist + push of the rebuilt will.

        Mirrors the wizard's phase 2 (sign_transactions -> save_willitems ->
        push_transactions_to_willexecutors) without dialogs.  Encrypted
        wallets cannot be signed headlessly, so the rebuilt transactions are
        left unsigned ("New") for the user to sign manually.
        """
        try:
            if self.wallet.has_keystore_encryption():
                _logger.warning(
                    "auto rebuild: wallet is encrypted; rebuilt will left "
                    "unsigned (sign manually)"
                )
            else:
                txs = self.sign_transactions(None)
                if txs:
                    for txid, tx in txs.items():
                        # Store the signed tx back, like
                        # ask_password_and_sign_transactions.on_success does
                        # (re-parse instead of deepcopy: the signed tx may carry
                        # wallet-derived input info holding a threading.RLock).
                        self.willitems[txid].tx = Will.get_tx_from_any(str(tx))
        except Exception as e:
            _logger.error("auto rebuild: signing failed: {}".format(e))
        try:
            self.save_willitems()
        except Exception as e:
            _logger.error("auto rebuild: save_willitems failed: {}".format(e))
        self._save_will_to_history()
        try:
            self.push_transactions_to_willexecutors()
        except Exception as e:
            _logger.error("auto rebuild: push failed: {}".format(e))

    def on_close(self):
        # Wallet is closing: run the closing "build will" task and tear down
        # the plugin's tabs/menu.  Each step is isolated so that one failure
        # does not leave the GUI half-initialised (which previously forced the
        # user to restart Electrum).  Errors are logged instead of silently
        # swallowed.
        if self.disable_plugin:
            return

        # 1) Business logic: build/save the will on close (unchanged behaviour).
        # REBUILD_ON_CLOSE gates the "Build your will" wizard only: the will is
        # still persisted so a manual Build/Check from the session is not lost.
        try:
            if self.bal_plugin.REBUILD_ON_CLOSE.get():
                close_window = BalBuildWillDialog(self)
                close_window.build_will_task()
            self.save_willitems()
        except Exception as e:
            _logger.error(f"on_close: build/save will failed: {e}")

        # 2) GUI teardown - each action guarded independently.
        def _safe(desc, fn):
            try:
                fn()
            except Exception as e:
                _logger.error(f"on_close: {desc} failed: {e}")

        _safe("close heirs tab", lambda: self.heirs_tab.close())
        _safe("close will tab", lambda: self.will_tab.close())
        _safe(
            "remove willexecutors menu action",
            lambda: self.tools_menu.removeAction(
                self.tools_menu.willexecutors_action
            ),
        )
        _safe("toggle heirs tab off", lambda: self.window.toggle_tab(self.heirs_tab))
        _safe("toggle will tab off", lambda: self.window.toggle_tab(self.will_tab))
        _safe("refresh tabs", lambda: self.window.tabs.update())

        # 3) Reset in-memory state so re-enabling/re-opening starts clean.
        self.willitems = {}
        self.will = {}
        self.heirs = {}
        self.willexecutors = {}
        self.disable_plugin = True
        self.ok = False
        # The tabs/menu actions were removed above; allow init_menubar_tools to
        # re-wire them if this same window is reused for another wallet.
        self._menubar_initialized = False

    def ask_password_and_sign_transactions(self, callback=None, will=None, txids=None):
        external = will is not None
        willitems = will if external else self.willitems

        def on_success(txs):
            if txs:
                for txid, tx in txs.items():
                    # Re-parse instead of deepcopy: the signed tx may carry
                    # wallet-derived input info holding a threading.RLock, which
                    # copy.deepcopy cannot pickle (see sign_transactions above).
                    willitems[txid].tx = Will.get_tx_from_any(str(tx))
                    if not external:
                        self.will[txid] = willitems[txid].to_dict()
                try:
                    Will.check_signatures(willitems, self.wallet)
                except Exception as e:
                    _logger.error(f"check_signatures after signing failed: {e}")
                if not external:
                    try:
                        self.will_list_widget.update()
                    except Exception:
                        pass
                # After signing, keep the local history in sync (save the still
                # incomplete "New" txs, remove the now-complete ones).
                if not external:
                    self._save_will_to_history()
                if callback:
                    try:
                        callback()
                    except Exception as e:
                        raise e

        def on_failure(exec_info):
            log_error(exec_info, self)

        password = self.get_wallet_password()
        task = partial(self.sign_transactions, password, will=will, txids=txids)
        msg = _("Signing transactions...")
        self.waiting_dialog = BalWaitingDialog(
            self, msg, task, on_success, on_failure, exe=False
        )
        self.waiting_dialog.exe()

    def broadcast_transactions(self, force=False, will=None, txids=None):
        external = will is not None

        def on_success(sulcess):
            if not external:
                self.will_list_widget.update()
            if sulcess:
                _logger.info("error, some transaction was not sent")
                self.show_warning(_("Some transaction was not broadcasted"))
                return
            _logger.debug("OK, sulcess transaction was sent")
            self.show_message(
                _("All transactions are broadcasted to respective Will-Executors")
            )

        def on_failure(exec_info):
            log_error(exec_info, self)
            # a,b,c = err
            # _logger.error(f"fail to broadcast transactions:{err}")
            # _logger.error(f"error: {b}")
            # _logger.error("traceback ")
            # tb = c
            # while tb is not None:
            #    frame = tb.tb_frame
            #    _logger.error("file:", frame.f_code.co_filename)
            #    _logger.error("name:", frame.f_code.co_name)
            #    _logger.error("line:", tb.tb_lineno)
            #    _logger.error("lasti:", tb.tb_lasti)
            #    tb = tb.tb_next

        task = partial(self.push_transactions_to_willexecutors, force, will=will, txids=txids)
        msg = _("Selecting Will-Executors")
        self.waiting_dialog = BalWaitingDialog(
            self, msg, task, on_success, on_failure, exe=False
        )
        self.waiting_dialog.exe()

    def push_transactions_to_willexecutors(self, force=False, will=None, txids=None):
        willitems = will if will is not None else self.willitems
        if txids is not None:
            willitems = {
                t: willitems[t] for t in txids if t in willitems
            }
        willexecutors = Willexecutors.get_willexecutor_transactions(willitems, force=force)

        def getMsg(willexecutors):
            msg = "Broadcasting Transactions to Will-Executors:\n"
            for url in willexecutors:
                msg += f"{url}:\t{willexecutors[url]['broadcast_status']}\n"
            return msg

        # Initialise statuses + show the list immediately.
        for url in willexecutors:
            willexecutors[url].setdefault("broadcast_status", _("waiting..."))
        try:
            self.waiting_dialog.update(getMsg(willexecutors))
        except Exception:
            pass

        error = {"flag": False}
        already_present = []

        def on_each(url, willexecutor, ok, exc):
            # Runs from a worker thread.  We only do book-keeping + a thread-safe
            # signal-based UI update here; the heavier "already present" check
            # path (which itself does network I/O) is handled below in the main
            # task thread to keep the original sequential behaviour for it.
            if isinstance(exc, Willexecutors.AlreadyPresentException):
                already_present.append(url)
                willexecutor["broadcast_status"] = _("checking...")
            elif ok:
                for wid in willexecutor.get("txsids", []):
                    willitems[wid].set_status("PUSHED", True)
                willexecutor["broadcast_status"] = _("Success")
            else:
                for wid in willexecutor.get("txsids", []):
                    willitems[wid].set_status("PUSH_FAIL", True)
                error["flag"] = True
                willexecutor["broadcast_status"] = _("Failed")
            willexecutor.pop("txs", None)
            try:
                self.waiting_dialog.update(getMsg(willexecutors))
            except Exception:
                pass

        if self.waiting_dialog._stopping:
            return
        # Push to all servers in parallel (each server keeps its own retry
        # behaviour, but a slow/dead server no longer blocks the others).
        Willexecutors.push_transactions_parallel(willexecutors, on_each=on_each)

        # Handle the "already present" servers: verify each stored tx.  This
        # keeps the exact original check logic, just executed after the parallel
        # push has identified which servers need it.
        for url in already_present:
            willexecutor = willexecutors[url]
            for wid in willexecutor.get("txsids", []):
                if self.waiting_dialog._stopping:
                    return
                self.waiting_dialog.update(
                    "checking {} - {} : {}".format(
                        willitems[wid].we["url"], wid, "Waiting"
                    )
                )
                w = willitems[wid]
                w.set_check_willexecutor(
                    Willexecutors.check_transaction(wid, w.we["url"])
                )
                self.waiting_dialog.update(
                    "checked {} - {} : {}".format(
                        willitems[wid].we["url"],
                        wid,
                        willitems[wid].get_status("CHECKED"),
                    )
                )

        if error["flag"]:
            return True

    def export_json_file(self, path, will=None):
        if will is None:
            for wid in self.willitems:
                self.willitems[wid].set_status("EXPORTED", True)
                self.will[wid] = self.willitems[wid].to_dict()
            write_json_file(path, self.will)
        else:
            write_json_file(path, {wid: wi.to_dict() for wid, wi in will.items()})

    def export_tx_file(self, path, will=None):
        """Export only the serialized transactions of the given will items.

        Writes a plain text file with every transaction (or PSBT) serialized
        on a single line, separated by a comma (``tx1,tx2,tx3``). The raw hex
        and PSBT base64 alphabets never contain a comma, so the separator is
        unambiguous. When ``will`` is omitted the live will items are used.
        """
        willitems = will if will is not None else self.willitems
        serialized = ",".join(str(wi.tx) for wid, wi in willitems.items())
        with open(path, "w", encoding="utf-8") as f:
            f.write(serialized)

    def export_will(self, will=None):
        try:
            export_meta_gui(
                self.window, "will.json", partial(self.export_json_file, will=will)
            )
        except Exception as e:
            self.show_error(str(e))
            raise e

    def export_will_dialog(self, will=None, initial_mode: Optional[str] = None):
        """Open the unified export window (File / QR / Audio).

        The window lets the user pick an All / Valid / Valid NC filter in
        the top row and choose one of the three transports, each with its
        contextual settings (file format for File, QR-code size and autoplay
        for QR, KB/sec for Audio). ``will`` defaults to the live will items;
        ``initial_mode`` opens the window directly on the given transport.
        """
        try:
            willitems = will if will is not None else self.willitems
            d = WillExportDialog(
                self,
                will=willitems,
                bal_plugin=self.bal_plugin,
                initial_mode=initial_mode or "file",
            )
            show_on_top(d)
        except Exception as e:
            self.show_error(str(e))
            raise e

    def get_audio_modem_plugin(self):
        """Return Electrum's ``audio_modem`` plugin instance, or None.

        The plugin is only usable when Electrum exposes it (the ``Plugins``
        manager knows the name) and its optional runtime dependency
        ``amodem`` is installed (:meth:`is_available`). Every other case
        returns None so callers can simply hide the audio buttons.
        """
        try:
            p = self.window.gui_object.plugins.get("audio_modem")
        except Exception:
            return None
        if not p or not getattr(p, "is_available", lambda: False)():
            return None
        return p

    def _audio_send_payload(self, payload):
        """Send a transfer payload through the audio_modem plugin.

        Wraps the plugin's own ``_send`` with a proper parent widget. The
        audio channel zlib-compresses internally, so the payload is passed
        uncompressed (no BAL ``Z`` flag needed on that transport).
        """
        plugin = self.get_audio_modem_plugin()
        if plugin is None:
            self.show_error(_("Audio MODEM plugin is not available."))
            return
        plugin._send(parent=self.window, blob=payload)

    def set_audio_modem_bitrate(self, kbps):
        """Set the ``audio_modem`` plugin transfer speed to ``kbps`` KB/sec.

        Both the send and the receive paths read ``modem_config``, so the
        sender and the receiver must be configured with the same speed. Raises
        when the plugin (or its ``amodem`` dependency) is unavailable.
        """
        plugin = self.get_audio_modem_plugin()
        if plugin is None:
            raise Exception(_("Audio MODEM plugin is not available."))
        try:
            import amodem.config
        except Exception as e:
            raise Exception(str(e)) from e
        plugin.modem_config = amodem.config.bitrates[int(kbps)]

    def merge_will(self, imported):
        """Merge imported will items into the live will.

        Both the tools-menu "Merge" action and the details-dialog "Merge"
        button go through this single method.

        For a transaction that already exists in the live will the live
        WillItem is kept (never replaced): only the operational statuses
        (signed/pushed/checked/mempool/confirmed) that are True in the
        imported item are carried over. When the live transaction is not
        yet signed the imported transaction is merged into it (signatures
        are combined when both are the same unsigned tx, otherwise the
        transaction is substituted); an already-signed live transaction is
        left untouched. Transactions that are new are added wholesale.

        After the merge a local validity check recomputes the
        valid/invalidated/replaced statuses (no server contact, no expiry
        raise).
        """
        # The reference timestamp is normally set by init_class_variables(),
        # which the merge flow does not run (Merge -> file import can be the
        # very first action in a session). Fall back to "now" so the local
        # validity check and the trailing update_all() always have it.
        if not hasattr(self, "date_to_check") or self.date_to_check is None:
            self.date_to_check = resolve_date_to_check(
                self.bal_plugin.is_basic_mode(), self.will_settings
            )

        for wid, wi in imported.items():
            if wid in self.willitems:
                live = self.willitems[wid]
                was_complete = live.get_status("COMPLETE")
                for status in (
                    "COMPLETE",
                    "PUSHED",
                    "CHECKED",
                    "MEMPOOL",
                    "CONFIRMED",
                ):
                    if wi.get_status(status):
                        live.set_status(status, True)
                if not was_complete:
                    try:
                        if live.tx.txid() == wi.tx.txid():
                            live.tx.combine_with_other_psbt(wi.tx)
                        else:
                            live.tx = wi.tx
                    except Exception:
                        live.tx = wi.tx
                    if live.tx.is_complete():
                        live.set_status("COMPLETE", True)
            else:
                self.willitems[wid] = wi
        Will.normalize_will(self.willitems, self.wallet)
        self.save_willitems()
        # Local validity check: recompute valid/invalidated/replaced statuses.
        try:
            Will.add_willtree(self.willitems)
            bal_plugin = getattr(self, "bal_plugin", None)
            history_label = (
                bal_plugin.HISTORY_LABEL.get() if bal_plugin is not None else None
            )
            all_utxos = Util.get_available_utxos(
                self.wallet,
                history_label,
                Will.get_min_locktime(
                    self.willitems, default_value=self.date_to_check
                ),
            )
            Will.check_invalidated(
                self.willitems, Will.utxos_strs(all_utxos), self.wallet
            )
            Will.search_rai(
                Will.get_all_inputs(self.willitems, only_valid=True),
                all_utxos,
                self.willitems,
                self.wallet,
            )
            Will.check_signatures(self.willitems, self.wallet)
        except Exception as e:
            log_error(e, self)
        self.save_willitems()
        self.update_all()

    def merge_will_from_file(self, path):
        try:
            willitems = self._load_will_file(path)
        except Exception as e:
            raise FileImportFailed(_("Invalid will file: {}").format(e)) from None
        Will.normalize_will(willitems, self.wallet)
        self.merge_will(willitems)

    def merge_single_transaction(self, tx):
        """Merge a single raw transaction (e.g. from the clipboard or a file)
        into the live will.

        The transaction is wrapped in a fresh :class:`WillItem` and merged
        through :meth:`merge_will`, so existing items are combined/updated and
        new transactions are added wholesale, exactly like a will-file merge.
        """
        wi = WillItem({"tx": str(tx)}, _id=tx.txid(), wallet=self.wallet)
        self.merge_will({wi._id: wi})

    def merge_will_ui(self):
        def on_success():
            self.will_list_widget.update_will(self.willitems)

        import_meta_gui(self.window, _("will"), self.merge_will_from_file, on_success)

    def import_will_into_details(self):
        """Import a will file and show it in a WillDetails window.

        Unlike the "Merge" actions (which merge the file into the active
        will), this is a read-only preview: the parsed will is shown in a
        :class:`WillDetailDialog` and the live wallet state is never touched.
        The dialog's Sign/Broadcast/Export/Invalidate buttons operate on the
        imported will only, and its Merge button merges the imported will
        into the live one.
        """
        imported = {}

        def on_file(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception as e:
                self.show_error(_("Invalid will file: {}").format(e))
                return
            kind, data = decode_will_payload(text)
            try:
                if kind == "will":
                    willitems = self._load_will_payload(data)
                    # Attach wallet/input info so the imported txs can be
                    # signed and broadcast (mirrors merge_will_from_file).
                    Will.normalize_will(willitems, self.wallet)
                    for wi in willitems.values():
                        wi.set_status("IMPORTED", True)
                    imported.update(willitems)
                else:
                    # Serialized transactions: route through the shared import
                    # tail (validity pass + review/sign wizard).
                    _complete_import(
                        self,
                        self.bal_plugin,
                        text,
                        show_error=self.show_error,
                        show_warning=self.show_warning,
                        close=lambda: None,
                    )
            except Exception as e:
                self.show_error(_("Invalid will file: {}").format(e))

        def on_success():
            if not imported:
                return
            d = WillDetailDialog(self, will=imported)
            show_on_top(d)

        import_meta_gui(self.window, _("will"), on_file, on_success)

    def import_will_dialog(self):
        """Open the unified import window (File / QR / Audio).

        The window offers three transports: File opens the read-only
        :class:`WillDetailDialog` preview; QR and Audio capture the
        transfer and send it through the per-transaction review wizard
        (:class:`WillTxReviewSignDialog`). Every flow works on fresh
        :class:`WillItem` objects and never touches the live will.
        """
        d = WillImportDialog(self, bal_plugin=self.bal_plugin)
        show_on_top(d)

    def _load_will_file(self, path):
        data = read_json_file(path)
        willitems = {}
        for k, v in data.items():
            data[k]["tx"] = tx_from_any(v["tx"])
            willitems[k] = WillItem(data[k], _id=k)
        return willitems

    def _load_will_payload(self, data):
        """Build WillItems from decoded whole-will JSON data."""
        willitems = {}
        for k, v in data.items():
            d = dict(v)
            d["tx"] = tx_from_any(d["tx"])
            willitems[k] = WillItem(d, _id=k)
        return willitems

    def check_transactions_task(self, will):
        start = time.time()
        # Servers are now contacted in parallel (see
        # Willexecutors.check_transactions_parallel) with a fast-fail timeout and
        # a global deadline, so a single slow/dead will-executor no longer
        # freezes the "checking transaction" dialog for minutes.  The dialog
        # shows live progress plus an elapsed-time counter (Xs / DEADLINEs).
        targets = [(wid, w.we["url"]) for wid, w in will.items() if w.we]
        total = len(targets)
        deadline = Willexecutors.CHECK_GLOBAL_DEADLINE
        done = {"count": 0}

        def _status_line():
            return "{} {}/{} ({}s / {}s)".format(
                _("Checking transactions"), done["count"], total,
                min(int(time.time() - start), deadline), deadline,
            )

        def on_each(wid, url, res, exc):
            # Reuse the original per-item logic: set_check_willexecutor handles
            # both a real response and a None/failure (-> CHECK_FAIL).
            try:
                will[wid].set_check_willexecutor(res)
            except Exception as e:
                _logger.error(f"check on_each error for {wid}: {e}")
            done["count"] += 1
            self.waiting_dialog.update(_status_line())

        def on_timeout(wid, url):
            # The global deadline elapsed before this server answered: mark the
            # item as failed (None response) so the user can retry later.
            try:
                will[wid].set_check_willexecutor(None)
            except Exception as e:
                _logger.error(f"check on_timeout error for {wid}: {e}")

        def on_tick():
            if getattr(self.waiting_dialog, "_stopping", False):
                return
            self.waiting_dialog.update(_status_line())

        if total:
            self.waiting_dialog.update(_status_line())
            Willexecutors.check_transactions_parallel(
                targets, on_each=on_each, on_timeout=on_timeout, on_tick=on_tick
            )

        if time.time() - start < 3:
            time.sleep(3 - (time.time() - start))

    def check_transactions(self, will):
        def on_success(result):
            if hasattr(self,"waiting_dialog"):
                del self.waiting_dialog
            self.update_all()
            pass

        def on_failure(exec_info):
            log_error(exec_info, self)
            # _logger.error(f"error checking transactions {e}")
            # pass

        task = partial(self.check_transactions_task, will)
        msg = _("Check Transaction")
        self.waiting_dialog = BalWaitingDialog(
            self, msg, task, on_success, on_failure, exe=False
        )
        self.waiting_dialog.exe()

    def update_willexecutor_list_widget(self, parent, willexecutors):
        try:
            parent.willexecutors_list.update(willexecutors)
            parent.will_executor_list_widget.update()
        except Exception as e:
            _logger.error(f"impossible to update will_executor_list_widget {e}")
        self.will_executors.update()

    def fetch_will_executors_list(self, old_willexecutors):
        """Download the will-executor list (runs inside the TaskThread worker).

        In BASIC mode the factory-default welist server is used and the setting
        is hidden.  In ADVANCED mode the user-configured URL is used exclusively
        (no fallback), and any failure produces a descriptive error.

        Returns the downloaded dict (empty ``{}`` on failure).
        """
        chainname = BalPlugin.chainname
        basic = self.bal_plugin.is_basic_mode()

        if basic:
            base = self.bal_plugin.WELIST_SERVER.default
        else:
            base = self.bal_plugin.WELIST_SERVER.get()
        base = base if base.endswith("/") else base + "/"
        url = f"{base}data/{chainname}?page=0&limit=100"
        candidates = [url]

        result = {}
        any_server_reached = False
        last_error = None
        net = Network.get_instance()
        _logger.info(f"fetch_will_executors_list: network present = {net is not None}")
        for url in candidates:
            _logger.info(f"fetch_will_executors_list: trying {url}")
            try:
                # Fast-fail with a couple of short retries instead of the
                # default 10x/3s storm: if the user's connection is flaky we
                # want to fall back to the next URL (and then show the simple
                # error message) quickly, not freeze for minutes.
                resp = Willexecutors.send_request(
                    "get", url, timeout=10, max_retries=1, retry_sleep=1,
                )
                any_server_reached = True
                _logger.info(
                    f"fetch_will_executors_list: resp type={type(resp).__name__} "
                    f"len={len(resp) if hasattr(resp, '__len__') else 'n/a'}"
                )
                if resp:
                    # Robustness: the server is expected to return a dict of
                    # {url: info}. If it returns anything else (a raw string
                    # error page, a list, etc. - more likely over Tor when a
                    # server misbehaves), do NOT use it: that would later crash
                    # on `self.willexecutors.update(result)`. Treat it as an
                    # empty/failed response and try the next candidate.
                    if not isinstance(resp, dict):
                        _logger.warning(
                            f"fetch_will_executors_list: {url} -> unexpected "
                            f"response type {type(resp).__name__}, ignoring"
                        )
                        last_error = "invalid response format"
                        continue
                    result = resp
                    # Tor gating: if Electrum is NOT connected through Tor, drop
                    # the .onion servers entirely - they are unreachable without
                    # Tor and would only waste time. When on Tor, keep them all.
                    tor_on = is_tor_active()
                    for w in list(result.keys()):
                        if w in ("status", "url"):
                            continue
                        # Defensive: each entry must be a dict too; skip anything
                        # malformed rather than crashing downstream.
                        if not isinstance(result.get(w), dict):
                            del result[w]
                            continue
                        if not tor_on and is_onion_url(w):
                            del result[w]
                            continue
                        Willexecutors.initialize_willexecutor(
                            result[w], w, None,
                            old_willexecutors.get(w, None),
                        )
                    break
                _logger.warning(f"fetch_will_executors_list: {url} -> empty response")
            except Exception as e:
                last_error = str(e)
                _logger.error(
                    f"fetch_will_executors_list: {url} -> {type(e).__name__}: {e}"
                )
        if not result:
            if not basic:
                # Advanced mode: always raise with full details.
                raise Willexecutors.NoServersForChainError(
                    chainname,
                    url=url,
                    reason=last_error or "empty response",
                )
            if any_server_reached:
                raise Willexecutors.NoServersForChainError(chainname)
        return result

    # Simple, user-facing message shown when the download fails for any reason
    # (the technical cause is in the Electrum log).
    DOWNLOAD_FAILED_MESSAGE = (
        "Could not download the will-executors list.\n\n"
        "This is usually caused by your internet connection or a firewall, "
        "not by the plugin. Please check your connection (a VPN often helps) "
        "and try again."
    )

    # Shown when the download fails while Electrum is connected through Tor:
    # the most common cause is a slow Tor connection, so guide the user
    # accordingly instead of the generic message above.
    DOWNLOAD_FAILED_TOR_MESSAGE = (
        "Could not download the will-executors list over Tor.\n\n"
        "Electrum is connected through Tor and the connection is taking too "
        "long. Your Tor connection may be slow. Please try again, or use a VPN "
        "(or temporarily disable Tor) for a faster connection."
    )

    def download_list(self, willexecutors, fn_on_success, fn_on_failure=None):
        if fn_on_failure is None:
            fn_on_failure = log_error

        base_msg = _("Downloading will-executors list...")
        download_start = time.time()
        # Upper bound shown to the user.  Unified with every other network wait
        # via the single shared Willexecutors.NETWORK_DEADLINE constant (the
        # user asked for one consistent 20s value everywhere instead of the old
        # scattered 30s/45s numbers).  Showing "Xs / NETWORK_DEADLINEs" tells the
        # user how long they may have to wait instead of an open-ended counter.
        download_deadline = Willexecutors.NETWORK_DEADLINE

        def task():
            # Heartbeat: show an elapsed-seconds counter (with the max wait made
            # explicit) while the (blocking) download runs, so the user sees time
            # advancing instead of a seemingly frozen dialog on a slow link.
            stop_heartbeat = threading.Event()

            def _heartbeat():
                while not stop_heartbeat.wait(1.0):
                    if getattr(self.waiting_dialog, "_stopping", False):
                        return
                    try:
                        self.waiting_dialog.update(
                            "{} ({}s / {}s)".format(
                                base_msg,
                                min(int(time.time() - download_start),
                                    download_deadline),
                                download_deadline,
                            )
                        )
                    except Exception:
                        return

            hb = threading.Thread(target=_heartbeat, name="bal-dl-hb",
                                  daemon=True)
            hb.start()
            try:
                return self.fetch_will_executors_list(willexecutors)
            finally:
                stop_heartbeat.set()

        def on_success(result):
            # Defensive: only merge a proper dict (see fetch_will_executors_list).
            # A non-dict here would raise "dictionary update sequence element..."
            if isinstance(result, dict) and result:
                self.willexecutors.update(result)
                fn_on_success(result)
            else:
                # Tor-aware failure message: a download failure/timeout while
                # Electrum is connected through Tor is most often a slow Tor
                # connection, so point the user to that (try again / VPN /
                # disable Tor). Otherwise keep the generic connection message.
                if is_tor_active():
                    self.show_warning(_(self.DOWNLOAD_FAILED_TOR_MESSAGE))
                else:
                    self.show_warning(_(self.DOWNLOAD_FAILED_MESSAGE))

        def on_failure(exc_info):
            _logger.error(f"download_list failed: {exc_info}")
            if isinstance(exc_info[1], Willexecutors.NoServersForChainError):
                err = exc_info[1]
                if err.url and err.reason == "empty response":
                    # Server reached but returned no data for this chain.
                    self.show_warning(_(
                        f"No active will-executor servers found for the "
                        f"{err.chain} network.\n\n"
                        f"The welist server at {err.url} responded but "
                        f"returned no will-executors for this chain."
                    ))
                elif err.url:
                    # Advanced mode with a non-empty error (network issue).
                    self.show_warning(_(
                        f"Could not reach the configured welist server.\n\n"
                        f"Server: {err.url}\n"
                        f"Error: {err.reason}\n\n"
                        f"Please verify the welist server URL in the plugin "
                        f"settings."
                    ))
                else:
                    # Basic mode: the server responded but has no data for this
                    # chain.
                    self.show_warning(_(
                        f"No active will-executor found for the "
                        f"{err.chain} network."
                    ))
            else:
                # Tor-aware: a generic failure/timeout while on Tor is most
                # likely a slow Tor connection.
                if is_tor_active():
                    self.show_warning(_(self.DOWNLOAD_FAILED_TOR_MESSAGE))
                else:
                    self.show_warning(_(self.DOWNLOAD_FAILED_MESSAGE))

        self.waiting_dialog = BalWaitingDialog(
            self, base_msg, task, on_success, on_failure, exe=False
        )
        self.waiting_dialog.exe()

    def ping_willexecutors_task(self, wes):
        _logger.info("ping willexecutots task")
        # Track per-url state for the live status text.  Servers are contacted
        # in parallel (see Willexecutors.ping_servers_parallel), so a single
        # unreachable server no longer blocks all the others: the whole batch
        # now takes about as long as the slowest server instead of the sum of
        # every server's (possibly timing-out) request.
        pinged = set()
        failed = set()
        total = len(wes)
        ping_start = time.time()

        ping_deadline = Willexecutors.PUSH_GLOBAL_DEADLINE

        def get_title():
            # Header shows progress + an elapsed-seconds counter with the max
            # wait made explicit (e.g. "3s / 30s"), so the user sees time
            # advancing and knows how long it may take, instead of a seemingly
            # frozen dialog.
            answered = len(pinged) + len(failed)
            msg = _("Ping Will-Executors:")
            msg += "  {}/{} ({}s / {}s)".format(
                answered, total,
                min(int(time.time() - ping_start), ping_deadline),
                ping_deadline,
            )
            msg += "\n\n"
            for url in wes:
                urlstr = "{:<50}: ".format(url[:50])
                if url in pinged:
                    urlstr += _("Ok")
                elif url in failed:
                    urlstr += _("Ko")
                else:
                    urlstr += _("waiting...")
                urlstr += "\n"
                msg += urlstr
            return msg

        def on_each(url, we, ok):
            if ok:
                pinged.add(url)
            else:
                failed.add(url)
            try:
                self.waiting_dialog.update(get_title())
            except Exception:
                pass

        # Show the initial "waiting..." list immediately.
        try:
            self.waiting_dialog.update(get_title())
        except Exception:
            pass

        # Refresh the elapsed-seconds counter while the (blocking) parallel ping
        # runs.  The tick is driven from THIS thread by ping_servers_parallel,
        # the same thread that drives on_each, so the dialog repaint is reliable.
        def on_tick():
            if getattr(self.waiting_dialog, "_stopping", False):
                return
            try:
                self.waiting_dialog.update(get_title())
            except Exception:
                pass

        Willexecutors.ping_servers_parallel(wes, on_each=on_each, on_tick=on_tick)

    def ping_willexecutors(self, wes, fn_on_success, fn_on_failure=None):
        if not fn_on_failure:
            fn_on_failure = log_error

        def on_success(result):
            fn_on_success(result)

        def on_failure(exec_info):
            fn_on_failure(exec_info)

        _logger.info("ping willexecutors")
        task = partial(self.ping_willexecutors_task, wes)
        msg = _("Ping Will-Executors")
        self.waiting_dialog = BalWaitingDialog(
            self, msg, task, on_success, on_failure, exe=False
        )
        self.waiting_dialog.exe()

    def preview_modal_dialog(self):
        self.dw = WillDetailDialog(self)
        # This dialog is meant to be modal (per its name); show it on top so it
        # cannot disappear behind the Electrum window.
        show_on_top(self.dw)

    def update_all(self):
        try:
            # Re-sync the cached "hide invalidated/replaced" flags from the
            # persisted config before refreshing the list.  The Settings dialog
            # checkboxes write the config directly (without touching the cached
            # flags), so without this the list would keep filtering with the old
            # value and the invalidated/replaced rows would not appear/disappear
            # until Electrum was restarted.
            self.bal_plugin.sync_hide_filters()
            _logger.debug(f"NoneType_debug willitems type: {type(self.willitems).__name__} len={len(self.willitems)}")
            for _wid, _w in list(self.willitems.items())[:3]:
                _logger.debug(f"NoneType_debug willitems[{_wid}] type={type(_w).__name__}")
            Will.add_willtree(self.willitems)
            all_utxos = Util.get_available_utxos(
                self.wallet,
                self.bal_plugin.HISTORY_LABEL.get(),
                Will.get_min_locktime(
                    self.willitems, default_value=self.date_to_check
                ),
            )
            utxos_list = Will.utxos_strs(all_utxos)
            Will.check_invalidated(self.willitems, utxos_list, self.wallet)

            self.will_list_widget.update_will(self.willitems)
            self.heirs_tab.update()
            self.will_tab.update()
            self.will_list_widget.update()

            # Group C / C2: re-apply the "Editable dates" setting to the date
            # fields of the toolbars / Heirs tab. The settings checkbox calls
            # update_all() when toggled, so this makes the change take effect
            # immediately (same pattern as sync_hide_filters above for the
            # "Hide Invalidated/Replaced" checkboxes). Guarded so a missing
            # widget never breaks the rest of the refresh.
            for _list in (self.heir_list_widget, self.will_list_widget):
                _settings_widget = getattr(_list, "will_settings_widget", None)
                if _settings_widget is not None:
                    try:
                        _settings_widget.apply_editable_dates()
                    except Exception as _edit_err:
                        _logger.debug(f"apply_editable_dates error: {_edit_err}")
                    # Re-apply BASIC/ADVANCED visibility of the Check-Alive
                    # field on the existing WILL/HEIR toolbars, so switching
                    # USER TYPE shows/hides it immediately (bug fix: it used to
                    # reappear only in the wizard, not on these tabs).
                    try:
                        _settings_widget.apply_user_type_visibility()
                    except Exception as _vis_err:
                        _logger.debug(
                            f"apply_user_type_visibility error: {_vis_err}"
                        )
        except Exception as e:
            _logger.error(f"error while updating window: {e}")


