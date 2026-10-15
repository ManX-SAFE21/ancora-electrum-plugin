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

import threading
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QStyle,
    QWidget,
)

from ...core import datetext, delivery, heir_notes
from ...core.checkalive import (
    CheckAliveAfterDeliveryError,
    CheckAliveError,
    check_alive_basic,
    resolve_date_to_check,
    resolve_guard_threshold,
)
from ...core.heirs import (
    AmountNotValid,
    SecondMessageNotAllowed,
    automatic_message_name,
    check_one_message,
    heir_with_address,
    message_address,
    message_names,
    message_text,
)
from ...core.messages import (
    check_alive_message,
    heir_error_message,
    will_change_message,
)
from ...core.summary import busy_summary, selected_urls, will_summary
from ...engine import build, context
from .common import (
    N_,
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
from .flow_adapter import CHOOSE_DATE_FIRST, SETTINGS_UNAVAILABLE
from .future_view import FutureView, WillViewSwitch, make_future_area
from .lists import HeirListWidget, PreviewList
from .widgets import (
    MESSAGE_WARNING_STYLE,
    LockTimeWidget,
    MessageEdit,
    PercAmountEdit,
    WillSummaryWidget,
    message_help_text,
    message_warning_text,
)

# The setting fields of the toolbars, refreshed together (CHANGELOG #123).
_SETTING_FIELDS = ("locktime", "threshold", "baltx_fees")


def _on_gui_thread():
    """True on the thread that runs Qt's GUI (Electrum's main thread).

    The Check's worker (``TaskThread``) and the automatic rebuild run on
    other threads: there a settings write must not touch a widget (rule I5
    of the #123 plan).
    """
    return threading.current_thread() is threading.main_thread()


def _hold_code(host):
    """The code of the delivery settings hold that applies, or None.

    See :func:`bal.engine.context.check_holds` (CHANGELOG #123, rule I6).
    A plain function, not a method: the window's methods that call it also
    run on test windows that are not ``BalWindow`` objects.
    """
    try:
        context.check_holds(host)
    except delivery.DeliveryHold as e:
        return e.code
    return None


def _stopped_by_hold(window):
    """True (and the owner is told) when a hold of the delivery settings
    stops a Sign / Broadcast / Invalidate command (rule I6).

    The settings could not be prepared: the error. The "which date?"
    question waits: press the Check button first.
    """
    code = _hold_code(window)
    if code is None:
        return False
    _logger.info(f"command refused: delivery hold ({code})")
    if code == delivery.DeliverySettingsUnavailable.code:
        window.show_error(_(SETTINGS_UNAVAILABLE))
    else:
        window.show_message(_(CHOOSE_DATE_FIRST))
    return True


def _address_field(window, width):
    """The heir's address field with Electrum's camera button (#138).

    A ``ButtonsLineEdit`` with "Read QR code with camera"; what is read
    goes through ``electrum.bip21.parse_bip21_URI`` and only the address is
    kept ("bitcoin:bc1q…?amount=…" → "bc1q…"). A QR with no bitcoin
    address shows a plain message (``window.show_error``); no camera or no
    zbar: Electrum's own message. A plain ``QLineEdit`` when Electrum's
    helpers are missing. ``field.qr_buttons``: the camera button(s), hidden
    in the OP_RETURN mode.
    """
    try:
        from electrum.gui.qt.util import ButtonsLineEdit
        from PyQt6.QtWidgets import QPushButton as _Button
    except Exception:
        field = QLineEdit()
        field.setFixedWidth(width)
        field.qr_buttons = []
        return field
    field = ButtonsLineEdit()
    field.setFixedWidth(width)
    field.qr_buttons = []
    try:
        field.add_qr_input_from_camera_button(
            config=window.window.config,
            show_error=window.show_error,
            setText=lambda data: field.setText(qr_address(data, window.show_error)
                                               or field.text()),
        )
        field.qr_buttons = field.findChildren(_Button)
    except Exception as e:
        _logger.error(f"camera button not added: {e!r}")
    reserve_button_space(field)
    return field


def reserve_button_space(field):
    """Keep the text of ``field`` out from under its camera button (#138).

    Owner's ZIP test (Windows, Italian): the camera icon sat ON the end of
    the address. Electrum's ``OverlayControlMixin`` only floats the buttons
    over the line edit and never reserves room for them (none of our style
    sheets touches this field), so the right text margin is set here: the
    width of the visible buttons plus the frame, or 0 when they are hidden
    (OP_RETURN mode). Called again whenever the buttons are shown or hidden.
    Returns the right margin set, in pixels.
    """
    buttons = [b for b in getattr(field, "qr_buttons", []) if not b.isHidden()]
    right = 0
    if buttons:
        overlay = getattr(field, "overlay_widget", None)
        if overlay is not None:
            overlay.adjustSize()
            width = overlay.sizeHint().width()
        else:
            width = sum(b.sizeHint().width() for b in buttons)
        # Electrum puts the overlay one frame width inside the right edge;
        # the extra 2 px keep the last character from touching the icon.
        frame = field.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
        right = width + frame + 2
    margins = field.textMargins()
    field.setTextMargins(margins.left(), margins.top(), right, margins.bottom())
    return right


def qr_address(data, show_error=None):
    """The bitcoin address in a scanned QR (#138), or None.

    A plain address or a BIP21 "bitcoin:" URI (``parse_bip21_URI``: the
    amount and the other fields are dropped). Anything else: None, and
    ``show_error`` (when given) says why in plain words.
    """
    try:
        from electrum.bip21 import parse_bip21_URI
        address = parse_bip21_URI(str(data or "").strip()).get("address")
    except Exception:
        address = None
    if not address and show_error is not None:
        show_error(_("This QR code does not contain a bitcoin address."))
    return address or None


def edit_hint(original, current):
    """The hint of the "Edit heir" dialog (#135, owner), or "".

    ``original`` / ``current``: ``(name, address, amount text)``. A new
    address: who receives changes, a new will must be signed; a new amount:
    the share changes, the same; only the name: nothing to sign (the Check
    saves a pure rename by itself, ``Will.sync_heir_names``). #140: a new
    text of a message (both addresses ``OP_RETURN:``): the message changes.
    """
    name0, address0, amount0 = (str(x).strip() for x in original)
    name1, address1, amount1 = (str(x).strip() for x in current)
    if (address1 != address0 and is_op_return_address(address0)
            and is_op_return_address(address1)):
        # #140: a new text of the same message, not a new payee.
        return _("The message changes: the will must be signed again.")
    if address1 != address0:
        return _("You are changing who receives: the will must be signed again.")
    if amount1 != amount0:
        return _("The share changes: the will must be signed again.")
    if name1 != name0:
        return _("Only the name changes: no need to sign.")
    return ""


def _utc_day(ts):
    """``DD/MM/YYYY`` of a delivery date, read as a UTC day (CHANGELOG #123).

    Safe21 stores 00:00 UTC; the UTC day is the day the transaction uses.
    Plain arithmetic from 1970, not ``datetime.fromtimestamp``, which fails
    after 2038 where ``time_t`` is 32-bit.
    """
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(ts))
    return moment.strftime("%d/%m/%Y")


class _SettingsRelay(QObject):
    """Brings a settings refresh from a worker thread to the GUI thread.

    CHANGELOG #123, rule I5: a delivery setting written off the GUI thread
    (the Check's worker, the automatic rebuild) only changes the stored
    value; ``changed`` is emitted there and the queued connection runs
    ``callback`` (which refreshes the fields) on the thread this object
    lives in, the GUI thread that built the window.
    """

    changed = pyqtSignal()

    def __init__(self, callback):
        """``callback``: called on the GUI thread after each ``changed``."""
        super().__init__()
        self._callback = callback
        self.changed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _deliver(self):
        """Run the callback (GUI thread); an error is only logged."""
        try:
            self._callback()
        except Exception as e:
            _logger.debug(f"settings refresh failed: {e!r}")


class BalWindow:
    """The plugin's part of one Electrum wallet window.

    It builds the Heirs and Will tabs and the menu, holds the wallet's
    heirs, will and delivery settings (since CHANGELOG #123 the wallet's own
    ``will_settings``, see ``bal.engine.context.load_will_settings``) and is
    the GUI host of the engine (``bal.engine.host.WalletHost``).
    """

    # Automatic rebuild-on-new-transaction flow (AUTO_REBUILD setting):
    # the debounce window collapses bursts of wallet events into one run, and
    # the cooldown prevents the flow from re-triggering right after a rebuild
    # (the freshly persisted txs can themselves fire wallet events).
    _AUTO_REBUILD_DEBOUNCE_MS = 5000
    _AUTO_REBUILD_COOLDOWN = 10.0
    # CHANGELOG #123: True when the wallet's delivery settings could not be
    # prepared (``bal.engine.context.ensure_will_settings``): every flow
    # stops. Class-level defaults, so a window built without ``__init__``
    # (the tests, the scenario harness) reads them too.
    settings_unavailable = False
    # The dates the toolbar shows (Check Alive, delivery), kept for the Will
    # Details window. Display caches of the window, never stored in the
    # settings (before #123 they were keys of the shared settings).
    real_threshold = None
    real_locktime = None

    def __init__(self, bal_plugin: "BalPlugin", window: "ElectrumWindow"):
        """Build the window state, the delivery settings and the two tabs.

        CHANGELOG #123: the heirs are read first, then the wallet's own
        delivery settings are loaded (:func:`bal.engine.context.
        load_will_settings`: a one-time migration, the anchor check), and
        only then the tabs are built, so their fields show the migrated
        values. The load waits at most one second for the wallet's flow lock
        (a command migrating the same wallet); if it is busy, it is done
        again in :meth:`init_will`.
        """
        self.bal_plugin = bal_plugin
        self.window = window
        self.heirs = {}
        self.will = {}
        self.willitems = {}
        self.willexecutors = {}
        self.will_settings = None
        self.settings_unavailable = False
        # True once the delivery settings were prepared (see init_will).
        self._settings_ready = False
        # Refreshes the setting fields on the GUI thread (rule I5).
        self._settings_relay = _SettingsRelay(self.refresh_setting_fields)
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
            # The heirs first: the migration reads them ("plugin data", the
            # heirs' old periods). Electrum's global bal_will_settings is
            # never written again (#123).
            self.heirs = Heirs(self.wallet)
            self._settings_ready = context.load_will_settings(
                self, lock_timeout=1.0
            )

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

    # ------------------------------------------------------------------ #
    # Engine host (bal.engine.host.WalletHost, CHANGELOG #118)
    # ------------------------------------------------------------------ #
    def store_will_setting(self, field, value):
        """Engine hook: store a delivery setting and refresh the fields that
        show it (the call the window made before #118).

        Since #123 :meth:`update_setting_widgets` writes through the one
        validated write into the wallet's own settings, and on a worker
        thread (the Check's phase 1) it only writes: the fields are then
        refreshed on the GUI thread (rule I5).
        """
        self.update_setting_widgets(value, field, update_all=True)

    def flush_wallet_db(self):
        """Engine hook: nothing; the window leaves writing the wallet file to
        Electrum, as it did before #118."""

    def after_history_saved(self):
        """Engine hook: redraw the History tab on the GUI thread."""
        self._schedule_history_refresh()

    # The state steps below live in bal.engine.context since #117/#118; the
    # names stay (the window's flows, the tests and the harness use them).

    def load_willitems(self):
        """See :func:`bal.engine.context.load_willitems`; then show the will
        in the Will tab."""
        context.load_willitems(self)
        if self.willitems:
            self.will_list_widget.will = self.willitems
            self.will_list_widget.update_will(self.willitems)
            self.will_tab.update()

    def save_willitems(self):
        """See :func:`bal.engine.context.save_willitems` (the tx stored as
        text, every value proven JSON-serializable)."""
        context.save_willitems(self)

    def init_will(self):
        """Load the heirs, the will-executors and the will of the wallet.

        Called when the wallet opens (``load_wallet``, or the hot enable).
        CHANGELOG #123: when the delivery settings could not be prepared in
        ``__init__`` (the wallet was busy, or an error), they are prepared
        again here, with the will items loaded; a success clears the hold and
        the fields show the stored values.
        """
        _logger.info("********************init_____will____________**********")
        try:
            # #138: no note of an heir that is no longer there. The heirs
            # as STORED in the wallet (self.heirs may not be loaded yet: an
            # empty list here would wipe every note).
            stored = self.wallet.db.get("heirs", None)
            if isinstance(stored, dict):
                heir_notes.clean(self.wallet, list(stored.keys()))
        except Exception as e:
            _logger.error(f"heir notes not checked: {e!r}")
        try:
            # #137: seed the user's deselections once, from the list as it
            # is when the wallet opens (owner decision A).
            Willexecutors.user_deselected(self.bal_plugin)
        except Exception as e:
            _logger.error(f"user deselections not read: {e!r}")
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
                        _("Please restart Electrum to activate the Safe21 Heartbeat plugin"),
                        title=_("Success"),
                    )
                    self.close_wallet()
                    return
        # #123: prepare the delivery settings again when __init__ could not.
        if getattr(self, "settings_unavailable", False) or not getattr(
            self, "_settings_ready", True
        ):
            self._settings_ready = context.load_will_settings(
                self, self.willitems or None, lock_timeout=1.0
            )
            self.refresh_setting_fields()
        # The will items are known now: convert an old Check Alive value
        # against the delivery the will really uses (#80).
        self._normalize_check_alive()
        # ...and the will summary can describe them (#84a).
        self.update_will_summary()

    # ------------------------------------------------------------------ #
    # The wallet's delivery settings (CHANGELOG #123)
    # ------------------------------------------------------------------ #
    def refresh_setting_fields(self):
        """Show the stored delivery settings in the toolbar fields.

        GUI thread only: :meth:`update_setting_widgets` calls it there, and
        through ``_SettingsRelay`` after a write made on a worker thread.
        Then each toolbar shows the delivery form (#127: the period text or
        the date, ``WillSettingsWidget.apply_delivery_form``). Never raises.
        """
        settings = self.will_settings or {}
        lists = (
            getattr(self, "heir_list_widget", None),
            getattr(self, "will_list_widget", None),
        )
        for field in _SETTING_FIELDS:
            if field not in settings:
                continue
            for widget in lists:
                if widget is not None:
                    self.update_widget_value(widget, field, settings[field])
        self._show_delivery_form()
        try:
            self.update_will_summary()
        except Exception as e:
            _logger.debug(f"will summary refresh failed: {e!r}")

    def _show_delivery_form(self):
        """Both toolbars show the stored delivery form (#127): the period
        text "N months (...) -> DD/MM/YYYY" or the date editor
        (``WillSettingsWidget.apply_delivery_form``). GUI thread only;
        never raises."""
        for widget in (
            getattr(self, "heir_list_widget", None),
            getattr(self, "will_list_widget", None),
        ):
            toolbar = getattr(widget, "will_settings_widget", None)
            if toolbar is not None:
                try:
                    toolbar.apply_delivery_form()
                except Exception as e:
                    _logger.debug(f"delivery form refresh failed: {e!r}")

    def _request_fields_refresh(self):
        """Refresh the setting fields now on the GUI thread, else later there.

        Off the GUI thread the relay's queued signal does it (rule I5); a
        window built without ``__init__`` has no relay and is skipped.
        """
        if _on_gui_thread():
            self.refresh_setting_fields()
            return
        relay = getattr(self, "_settings_relay", None)
        if relay is not None:
            relay.changed.emit()

    def delivery_note_text(self, note):
        """The text of the one-time note ``(kind, old, new)`` (plan 3.3).

        Neutral wording: the old value was SHARED by every wallet of this
        Electrum, so it may have been changed from another wallet. ``None``
        for a note this version does not know.
        """
        kind, old, new = note
        new_day = _utc_day(new) if delivery.as_delivery(new) else None
        old_day = _utc_day(old) if delivery.as_delivery(old) else None
        if kind == delivery.NOTE_CONVERTED and new_day:
            return _("Your delivery date is now fixed: {}.").format(new_day)
        if kind in (delivery.NOTE_DATE_DIFFERS, delivery.NOTE_LATER_PENDING):
            if not (old_day and new_day):
                return None
            text = _(
                "The shared setting (possibly changed from another wallet) "
                "was {old}; this wallet keeps the date of its will, {new}."
            ).format(old=old_day, new=new_day)
            if self.will_settings.get(delivery.CHOICE_KEY):
                text += "\n\n" + _(CHOOSE_DATE_FIRST)
            return text
        if kind == delivery.NOTE_OUTSIDE_CHANGE and new_day:
            text = _(
                "Your will was changed by another program. The delivery date "
                "now follows the will in force: {new} (before: {old})."
            ).format(new=new_day, old=old_day or "-")
            if old_day and int(new) < int(old):
                # An anticipation was followed: choosing the later date again
                # is a postpone, which costs a fee.
                text += "\n\n" + _(
                    "To use the later date again, choose it and press "
                    "'Check your will': first the old will needs an "
                    "invalidation transaction (your bitcoin stay yours, you "
                    "only pay the network fee)."
                )
            return text
        return None

    def show_delivery_note(self):
        """Show the one-time note of the delivery settings (plan 3.3).

        Scheduled by ``load_wallet`` two seconds after the wallet opens, so
        it appears after the main window. The note is deleted only after the
        message box returns: when showing it fails, it stays and is shown at
        the next opening. While the "which date?" question waits, its two
        dates stay (the Check asks it). Never raises.
        """
        try:
            if self.disable_plugin or self.settings_unavailable:
                return
            settings = self.will_settings
            note = delivery.pending_note(settings)
            if note is None:
                return
            text = self.delivery_note_text(note)
            if text is None:
                _logger.info(f"delivery note {note[0]!r} not shown: unknown")
            else:
                self.show_message(text)
            delivery.mark_note_shown(settings)
        except Exception as e:
            _logger.error(f"delivery note not shown: {e!r}")

    def delivery_choice_texts(self, keep, later):
        """The "which date?" question: ``(text, keep button, later button)``.

        ``keep``: the date of the will already prepared (no fee); ``later``:
        the later date an older setting asked for (the old will must be
        invalidated first, miner fee). Plan 3.3.
        """
        text = _(
            "The will prepared for {keep} is not signed and sent yet. The "
            "shared setting (possibly changed from another wallet) asked for "
            "a later date: {later}. Which date do you want?"
        ).format(keep=_utc_day(keep), later=_utc_day(later))
        keep_text = _("Keep {} (the will already prepared, no fee)").format(
            _utc_day(keep)
        )
        later_text = _(
            # #135 follow-up (owner): the same wording as the other texts.
            "Use {} (later: needs an invalidation transaction, you only pay "
            "the network fee)"
        ).format(_utc_day(later))
        return text, keep_text, later_text

    def ask_delivery_choice(self, parent=None):
        """Ask the "which date?" question; ``"keep"``, ``"later"`` or None.

        Asked by the Check (and by Prepare) while ``choice_pending`` is set,
        never at wallet close. "Keep" is the default button; Esc and closing
        the box are Cancel (None: nothing changes). When the later date was
        lost, only Keep makes sense: it is chosen without asking (logged).
        """
        dates = delivery.choice_dates(self.will_settings)
        if dates is None or dates[0] is None:
            return None
        keep, later = dates
        if later is None:
            _logger.info("delivery choice: the later date is unknown, kept")
            return "keep"
        text, keep_text, later_text = self.delivery_choice_texts(keep, later)
        box = QMessageBox(parent or self.window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(_("Check your will"))
        box.setText(text)
        keep_button = box.addButton(keep_text, QMessageBox.ButtonRole.AcceptRole)
        later_button = box.addButton(later_text, QMessageBox.ButtonRole.ActionRole)
        cancel = box.addButton(_("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_button)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep_button:
            return "keep"
        if clicked is later_button:
            return "later"
        return None

    def answer_delivery_choice(self, use_later=False):
        """Store the answer to the "which date?" question, then show it.

        See :func:`bal.engine.context.answer_delivery_choice` (the question
        and its note are cleared). GUI thread. Returns the date stored.
        """
        stored = context.answer_delivery_choice(self, use_later=use_later)
        self.refresh_setting_fields()
        return stored

    def choose_delivery_date(self, parent=None):
        """Ask the "which date?" question and store the answer.

        True when a date was chosen (the caller may go on), False on Cancel
        or when the answer could not be stored (logged).
        """
        answer = self.ask_delivery_choice(parent=parent)
        if answer is None:
            return False
        try:
            self.answer_delivery_choice(use_later=answer == "later")
        except Exception as e:
            _logger.error(f"delivery choice not stored: {e!r}")
            return False
        return True

    def init_wizard(self):
        wizard_dialog = BalWizardDialog(self)
        wizard_dialog.exec()

    def show_willexecutor_dialog(self, on_close=None):
        """The Tools -> Will-Executors window.

        ``on_close`` (#134): called when it closes, e.g. to show the
        will-executors selected again as "to send" in the summary box and
        the Check window.
        """
        self.willexecutor_dialog = WillExecutorDialog(self)
        if on_close is not None:
            self.willexecutor_dialog.on_close_callbacks.append(on_close)
        # Keep it in front of Electrum (window-modal) instead of letting it
        # fall behind the main window.
        show_on_top(self.willexecutor_dialog)

    def _after_executors_window(self):
        """The will-executor window closed (#134): the summary box and the
        List's version rows follow the selection made there."""
        self.update_will_summary()
        try:
            self.will_list_widget.update()
        except Exception as e:
            _logger.debug(f"will list not refreshed: {e!r}")

    def selected_willexecutors(self):
        """The URLs of the will-executors selected now (#134), or None.

        ``bal.core.summary.selected_urls`` of the plugin's list: the one rule
        of the summary, the Check window, the report and the Future view.
        """
        try:
            return selected_urls(Willexecutors.get_willexecutors(self.bal_plugin))
        except Exception as e:
            _logger.debug(f"selected will-executors not read: {e!r}")
            return None

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
        # CHANGELOG #84a: the will summary goes between the toolbar and the
        # transaction list (Electrum's list tab is a plain QVBoxLayout).
        self.will_summary_widget = WillSummaryWidget(self.on_will_summary_action)
        layout = tab.layout()
        index = layout.indexOf(self.will_list_widget)
        layout.insertWidget(max(index, 0), self.will_summary_widget)
        # CHANGELOG #131: under the summary, the "List | Future" selector;
        # the Future view (in a scroll area) takes the list's place when
        # chosen. The selector restores the last choice.
        self.future_view = FutureView(self)
        self.future_area = make_future_area(self.future_view)
        index = layout.indexOf(self.will_list_widget)
        layout.insertWidget(index + 1, self.future_area, 1)
        self.will_view_switch = WillViewSwitch(
            self.bal_plugin.WILL_VIEW, self.will_list_widget,
            self.future_area, self.future_view,
        )
        layout.insertWidget(index, self.will_view_switch)
        self.update_will_summary()
        return tab

    def update_will_summary(self):
        """Refresh the will summary box of the Will tab (CHANGELOG #84a).

        Called whenever the transaction list or the heirs list is redrawn
        and when the delivery / Check Alive settings change, so the box
        always matches what the list shows. Since #131 it also refreshes
        the Future view of the Will tab. Read-only; never raises.
        """
        # CHANGELOG #131: the Future view follows the same refreshes (it
        # recomputes only while it is shown).
        future = getattr(self, "future_view", None)
        if future is not None:
            try:
                future.refresh()
            except Exception as e:
                _logger.warning(f"future view refresh failed: {e!r}")
        widget = getattr(self, "will_summary_widget", None)
        if widget is None:
            return
        if getattr(self, "_will_summary_busy", 0) > 0:
            widget.show_summary(busy_summary())
            return
        try:
            summary = will_summary(
                self.willitems,
                len(self.heirs or {}),
                self.will_settings,
                self.bal_plugin.is_basic_mode(),
                still_active=self._still_active_for_summary(),
                warn_months=getattr(self.bal_plugin, "warning_months", lambda: None)(),
                # #134: the one date style and the in-force comparison.
                long_dates=True,
                # #134: deselected will-executors are not "to send".
                selected=self.selected_willexecutors(),
                # #135: THE will in force, the one rule of the List, the
                # Future view and the wizard (not only the REPLACED copies).
                in_force=context.in_force_delivery(self),
            )
        except Exception as e:
            _logger.debug(f"will summary failed: {e!r}")
            summary = None
        widget.show_summary(summary)

    def _still_active_for_summary(self):
        """Sent txs, not VALID, still executable on-chain, any date (#84a/#84b).

        The plugin's records can be wrong (owner's wallet: after "later date
        -> Cancel" the sent txs were marked Replaced while their coin was
        never spent), and after a Cancel at signing the will sent before
        stays in force until the new one is signed. The on-chain check
        (``Will.sent_still_executable``, the same rules as the #69 warning)
        keeps the summary truthful. Never raises.
        """
        try:
            return Will.sent_still_executable(self.willitems, self.wallet)
        except Exception as e:
            _logger.debug(f"still-active check for the summary failed: {e!r}")
            return []

    def can_exclude_willexecutor(self, url):
        """True when ``url`` is selected and is not the last one selected (#93).

        Excluding the last will-executor would leave the will with nobody to
        broadcast it: the owner must choose another one, or the backup mode,
        in the Will-Executor tab.
        """
        selected = [
            u for u, we in (self.willexecutors or {}).items()
            if Willexecutors.is_selected(we)
        ]
        return url in selected and len(selected) > 1

    def exclude_willexecutor(self, url):
        """Deselect the will-executor ``url`` and save it, like its tab (#93).

        Owner: when an executor does not answer, deselecting it in the
        Will-Executor tab and pressing Check again is the way out, but hard
        to find; the Building Will dialog now offers it with one click. The
        executor stays in the list and can be selected again. Returns False
        (nothing done) when it is not allowed (:meth:`can_exclude_willexecutor`).

        The copies of the will meant for it that it never got (VALID, not
        sent) are dropped: they exist only here, and a deselected executor's
        copy stays VALID (BAL only ignores it), so the summary kept saying
        "1 will-executor did not receive it" and the report stayed locked
        (found with the scenario harness). Copies it did get are kept.
        """
        if not self.can_exclude_willexecutor(url):
            return False
        # #137: Exclude is the plugin's deselection of a will-executor that
        # did not answer (selected again when it answers): never a user
        # deselection. The user's list is seeded first, as it was.
        Willexecutors.user_deselected(self.bal_plugin)
        self.willexecutors[url]["selected"] = False
        Willexecutors.save(self.bal_plugin, self.willexecutors)
        unsent = [
            wid for wid, w in self.willitems.items()
            if w.get_status("VALID")
            and (w.we or {}).get("url") == url
            and not w.get_status("PUSHED")
        ]
        for wid in unsent:
            del self.willitems[wid]
        if unsent:
            self.save_willitems()
        return True

    def set_will_summary_busy(self, busy):
        """A working dialog was shown (True) or hidden (False), see BalDialog.

        While at least one is on screen the summary says "operation in
        progress"; when the last one closes it shows the result.
        """
        count = getattr(self, "_will_summary_busy", 0) + (1 if busy else -1)
        self._will_summary_busy = max(count, 0)
        self.update_will_summary()

    def on_will_summary_action(self, action):
        """Run the button of the will summary box with the EXISTING commands.

        "check" is the Check button of the toolbar; "renew" the Check Alive
        question of #79 (Renew / Not now, with its fee warning); "wizard"
        the "Build Your Will" wizard. "sign_send" / "send" use the Check too
        when it signs and sends by itself (BASIC mode or "auto sign"), else
        the Sign and Broadcast menu commands, as the owner chose to sign by
        hand.
        """
        auto = self.bal_plugin.is_basic_mode() or self.bal_plugin.AUTO_SIGN.get()
        if action == "open_executors":
            # #134: the deselected will-executors' line; the box follows
            # what the owner selects there when the window closes.
            self.show_willexecutor_dialog(on_close=self._after_executors_window)
            return
        if action == "wizard":
            self.init_wizard()
        elif action == "renew":
            self.offer_check_alive_renewal()
        elif action == "check" or (action in ("sign_send", "send") and auto):
            self.will_list_widget.check()
        elif action == "sign_send":
            self.ask_password_and_sign_transactions(
                callback=self.broadcast_transactions
            )
        elif action == "send":
            self.broadcast_transactions()
        self.update_will_summary()

    def new_heir_dialog(self, heir_key=None):
        """The "New heir" window, or "Edit: <name>" for ``heir_key``.

        Also used by the BASIC wizard and the Heirs list. #139 (owner): the
        amount has two buttons, "% Percentage" and "Fixed amount", its unit
        at the right and a grey line saying what the mode means; a new heir
        starts on Percentage, an edit preselects the stored mode ("30%":
        Percentage). The stored format ("30%" or satoshis) is unchanged.
        #140 (owner): in the OP_RETURN mode the message has an 80-byte
        counter, a fixed warning and its own "?"; a new message with no name
        gets "OP_RETURN message"; a second message is refused. An existing
        message opens :meth:`message_dialog` instead.
        """
        heir = self.heirs.get(heir_key)
        if heir is not None and is_op_return_address(str(heir[0])):
            # #140 (owner's ZIP test): a message (double click, Edit) opens
            # the same window as the "✉" button, not the heir's one with
            # the hexadecimal address.
            return self.message_dialog(heir_key)
        title = _("New heir")
        if heir:
            title = _("Edit: {}").format(heir_key)

        d = BalDialog(
            self.window, self.bal_plugin, self.bal_plugin.get_window_title(title)
        )

        vbox = QVBoxLayout(d)
        grid = QGridLayout()

        heir_name = QLineEdit()
        heir_name.setFixedWidth(32 * char_width_in_lineedit())
        # #138: a line with Electrum's "Read QR code with camera" button.
        heir_address = _address_field(self, 32 * char_width_in_lineedit())
        heir_amount = PercAmountEdit(self.window.get_decimal_point)
        # #139 (owner): the amount, then the two mode buttons, on one row;
        # a grey line under it says what the mode means. No parent yet, as
        # the other fields: the grid gives it the dialog, in the grid's
        # order (``findChildren`` keeps the name, address, amount order).
        amount_row = QWidget()
        amount_layout = QHBoxLayout(amount_row)
        amount_layout.setContentsMargins(0, 0, 0, 0)
        percentage_button = QPushButton(_("% Percentage"))
        fixed_button = QPushButton(_("Fixed amount"))
        mode_buttons = QButtonGroup(amount_row)
        mode_buttons.setExclusive(True)
        for button in (percentage_button, fixed_button):
            button.setCheckable(True)
            mode_buttons.addButton(button)
        amount_layout.addWidget(heir_amount)
        amount_layout.addWidget(percentage_button)
        amount_layout.addWidget(fixed_button)
        amount_layout.addStretch(1)
        amount_help = QLabel("")
        amount_help.setWordWrap(True)
        amount_help.setStyleSheet("color:#6A737D;")
        amount_help_button = HelpButton(_(
            "Percentage: a share of the wallet balance (e.g. 30). The heirs' "
            "percentages are weights: together they share, in proportion, what is "
            "left after the fees and the fixed amounts (e.g. two heirs at 30 and no "
            "others: half each). "
            "Fixed amount: an exact amount in the wallet's unit, paid first. "
            "If no heir has a percentage, what is left over is shared among the "
            "heirs with a fixed amount, in proportion: the wallet is always "
            "emptied completely."))
        # #138 (owner): what the address must be, in one grey line.
        address_help = QLabel(_(
            "One single address of the heir (e.g. bc1q…), taken from Receive in "
            "the heir's wallet. Not an xpub/zpub."))
        address_help.setWordWrap(True)
        address_help.setStyleSheet("color:#6A737D;")

        def _show_payee_rows(visible):
            """Show or hide what only an address (a payee) has (#139).

            The amount row, its grey line and its "?", and #138's grey
            address line: none of them means anything for an OP_RETURN
            message (owner's ZIP test). A widget not yet in the dialog (the
            first call, before the grid) is only hidden, never shown: shown
            without a parent it would open as a window of its own; the
            dialog shows it later.
            """
            for widget in (amount_row, amount_help, amount_help_button, address_help):
                if visible and widget.parentWidget() is None:
                    continue
                widget.setVisible(visible)

        def _show_amount_mode(percentage):
            """The buttons and the grey line follow the field's mode (#139)."""
            percentage_button.setChecked(percentage)
            fixed_button.setChecked(not percentage)
            if percentage:
                amount_help.setText(_(
                    "A share of the wallet balance (e.g. 30). The heirs' percentages "
                    "share, in proportion, what is left after the fixed amounts."))
            else:
                amount_help.setText(_(
                    "An exact amount in {}, paid first. If no heir has a percentage, "
                    "what is left over is shared among the heirs in proportion: the "
                    "wallet is always emptied completely.").format(heir_amount.unit_text()))

        heir_amount.mode_changed.connect(_show_amount_mode)
        percentage_button.clicked.connect(lambda: heir_amount.set_percentage(True))
        fixed_button.clicked.connect(lambda: heir_amount.set_percentage(False))

        # OP_RETURN message field (hidden by default). #140 (owner): at most
        # 80 bytes with a counter, a fixed warning and a "?" of its own.
        op_return_message = MessageEdit(lines=3)
        op_return_message.setFixedWidth(32 * char_width_in_lineedit())
        op_return_message.setVisible(False)
        op_return_amount_label = QLabel(_("Amount"))
        op_return_message_label = QLabel(_("OP_RETURN Message"))
        message_help_button = HelpButton(message_help_text())
        message_info = QWidget()
        message_info_layout = QVBoxLayout(message_info)
        message_info_layout.setContentsMargins(0, 0, 0, 0)
        message_info_layout.addWidget(op_return_message.counter)
        message_warning = QLabel(message_warning_text())
        message_warning.setWordWrap(True)
        message_warning.setStyleSheet(MESSAGE_WARNING_STYLE)
        message_info_layout.addWidget(message_warning)

        def _show_message_rows(visible):
            """Show or hide the "?", the counter and the warning of the
            message (#140); as ``_show_payee_rows``, a widget not yet in the
            dialog is never shown (it would open as a window of its own)."""
            for widget in (message_help_button, message_info):
                if visible and widget.parentWidget() is None:
                    continue
                widget.setVisible(visible)

        if heir:
            heir_name.setText(str(heir_key))
            addr = str(heir[0])
            heir_address.setText(addr)
            if not is_op_return_address(addr):
                # #139: "30%" opens on Percentage (the field shows "30"),
                # anything else on Fixed amount.
                heir_amount.set_amount_text(
                    str(Util.decode_amount(heir[1], self.window.get_decimal_point()))
                )
            else:
                heir_amount.set_percentage(True)
            # #81: the heir's own date; the widget never writes the will's
            # delivery setting (opening this dialog used to overwrite it).
            self.heir_locktime = LockTimeWidget(
                self, self.window, heir[2], save_to_settings=False
            )
        else:
            heir_address.setText("")
            heir_amount.set_percentage(True)  # #139: a new heir: Percentage
            self.heir_locktime = LockTimeWidget(
                self, self.window, self.will_settings["locktime"],
                save_to_settings=False,
            )

        _show_amount_mode(heir_amount.percentage)

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
            """The OP_RETURN rows (a message, no amount) or the payee rows;
            #138: the camera button only for an address; #139: the mode
            buttons, the unit and the grey amount line go with the amount."""
            addr = heir_address.text()
            for camera in getattr(heir_address, "qr_buttons", []):
                camera.setVisible(not is_op_return_address(addr))
            # The text margin follows the button (owner's ZIP test).
            reserve_button_space(heir_address)
            _show_message_rows(is_op_return_address(addr))
            if is_op_return_address(addr):
                if not op_return_message.isVisible():
                    op_return_message.setVisible(True)
                    heir_amount.setVisible(False)
                    _show_payee_rows(False)
                    op_return_amount_label.setVisible(False)
                    op_return_message_label.setVisible(True)
                op_return_message.blockSignals(True)
                _update_op_return_from_address()
                op_return_message.blockSignals(False)
            else:
                op_return_message.setVisible(False)
                heir_amount.setVisible(True)
                _show_payee_rows(True)
                op_return_amount_label.setVisible(True)
                op_return_message_label.setVisible(False)
            if d.isVisible():
                # #139 (owner's ZIP test): the window fits the rows now
                # shown, so no empty band is left where the hidden ones
                # were. Queued: the layout takes the new rows after this
                # event.
                QTimer.singleShot(0, d.adjustSize)

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

        # #140 (owner): "Bitcoin address", never mistaken for a home address.
        grid.addWidget(QLabel(_("Bitcoin address")), 2, 0)
        grid.addWidget(heir_address, 2, 1)
        grid.addWidget(HelpButton(_("Bitcoin address or OP_RETURN: prefix + hex data")), 2, 2)
        grid.addWidget(address_help, 3, 1)

        grid.addWidget(op_return_amount_label, 4, 0)
        grid.addWidget(amount_row, 4, 1)
        grid.addWidget(amount_help_button, 4, 2)
        grid.addWidget(amount_help, 5, 1)

        grid.addWidget(op_return_message_label, 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(op_return_message, 4, 1)
        # #140: in the cells of the amount's "?" and grey line, hidden in
        # the OP_RETURN mode (and these hidden in the payee mode).
        grid.addWidget(message_help_button, 4, 2, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(message_info, 5, 1)
        _show_message_rows(is_op_return_address(heir_address.text()))

        locktime_label = QLabel(_("Locktime"))
        enable_multiverse = self.bal_plugin.ENABLE_MULTIVERSE.get()
        if enable_multiverse:
            grid.addWidget(locktime_label, 6, 0)
            grid.addWidget(self.heir_locktime, 6, 1)
            grid.addWidget(HelpButton(_("locktime")), 6, 2)

        # #138 (owner): the owner's private notes on this heir.
        notes = QPlainTextEdit()
        notes.setFixedWidth(32 * char_width_in_lineedit())
        notes.setFixedHeight(4 * QFontMetrics(notes.font()).height() + 12)
        notes.setPlainText(heir_notes.get(self.wallet, heir_key) if heir_key else "")
        grid.addWidget(QLabel(_("Notes")), 7, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(notes, 7, 1)
        grid.addWidget(HelpButton(_(
            "Private notes for you, kept only in this wallet; never sent to the "
            "will-executors or written on the blockchain.")), 7, 2,
            Qt.AlignmentFlag.AlignTop)

        vbox.addLayout(grid)
        # #135 (owner): what an edit means for the will, live.
        hint = QLabel("")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6A737D;")
        vbox.addWidget(hint)
        original = None
        if heir:
            original = (str(heir_key), str(heir[0]),
                        str(Util.decode_amount(heir[1], self.window.get_decimal_point()))
                        if not is_op_return_address(str(heir[0])) else "")

            def _update_hint():
                """The #135 hint; the amount as it would be saved (#139:
                the number plus "%" in Percentage, as ``original`` has it)."""
                hint.setText(edit_hint(original, (
                    heir_name.text(), heir_address.text(),
                    "" if is_op_return_address(heir_address.text())
                    else heir_amount.amount_text())))

            for field in (heir_name, heir_address, heir_amount):
                field.textChanged.connect(_update_hint)
            heir_amount.mode_changed.connect(_update_hint)
            _update_hint()
        buttons = [CancelButton(d), OkButton(d)]
        if not heir:
            buttons.append(new_heir_button)
        vbox.addLayout(Buttons(*buttons))
        while d.exec():
            if (not heir_name.text().strip() and heir_key is None
                    and is_op_return_address(heir_address.text())):
                # #140 (owner): a new message gets its name by itself.
                heir_name.setText(automatic_message_name(
                    self.heirs, _("OP_RETURN message")))
            if not heir_name.text().strip():
                # #139 (owner's ZIP test): an empty name reached the wallet
                # file and failed there with a raw error; the window stays
                # open and nothing is stored. The name is not stripped when
                # saved: existing names keep matching.
                self.show_error(_("Write a name for the heir."))
                continue
            raw_address = heir_address.text()
            if is_op_return_address(raw_address):
                amount = "0"
            else:
                try:
                    # #139: "30%" in Percentage, as before the buttons.
                    amount = Util.encode_amount(heir_amount.amount_text(),
                                                self.window.get_decimal_point())
                except Exception:
                    # #135: a plain sentence, not a raw exception text.
                    self.show_error(heir_error_message(AmountNotValid("")))
                    continue
            # #81: the date of this dialog's field (multiverse); without
            # multiverse set_heir uses the will's delivery anyway.
            heir = [
                heir_name.text(),
                raw_address,
                amount,
                str(self.heir_locktime.get_value()),
            ]
            if heir_key is not None and original is not None and edit_hint(
                    original, (heir[0], heir_address.text(),
                               "" if is_op_return_address(raw_address)
                               else heir_amount.amount_text())) in (
                    "", _("Only the name changes: no need to sign.")):
                # #135 (owner's ZIP test, A -> B -> A): only the name was
                # touched: keep the stored amount and date exactly as they
                # were, so the will items still match them (no rebuild).
                old_value = self.heirs.get(heir_key)
                heir[2], heir[3] = old_value[1], old_value[2]
            new_name = heir[0]
            note = notes.toPlainText()
            if heir_notes.too_long(note):
                self.show_error(_("The notes are too long: at most {} characters.").format(
                    heir_notes.MAX_LENGTH))
                continue
            if new_name != heir_key and new_name in self.heirs:
                # #135: never overwrite another heir (a new one, or a
                # rename to a name already in the list).
                self.show_error(_("An heir named {} already exists.").format(new_name))
                continue
            try:
                # #140 (owner): one message per will; a message being edited
                # may change its text.
                check_one_message(self.heirs, heir_key, raw_address)
            except SecondMessageNotAllowed as e:
                self.show_error(heir_error_message(e))
                continue
            other = heir_with_address(self.heirs, raw_address, except_name=heir_key)
            if other is not None:
                # #135 (owner): no two heirs on one address (the second
                # would take a second share there). The heir's own address
                # (an edit) is allowed; compared as ``address_key`` does.
                self.show_error(
                    _("This address is already used by the heir {}.").format(other))
                continue
            try:
                self.set_heir(heir)
            except Exception as e:
                # #135 (owner): "not an address,bc1q…" in plain, translated
                # words (the same helper as the command line).
                self.show_error(heir_error_message(e))
                continue
            if heir_key is not None and new_name != heir_key:
                # #135: a rename. The old name goes AFTER the new one is
                # saved, so a refused entry never loses the heir; before,
                # it was never removed and a second heir took a share.
                self.delete_heirs([heir_key])
            # #138: the note, under the heir's (new) name; empty removes it.
            heir_notes.set_note(self.wallet, new_name, note)
            self.heir_list_widget.update()
            if self.add_another_heir:
                self.new_heir_dialog()
            break

    def message_dialog(self, key=None):
        """The "✉ A message in the will" window (#140, owner).

        One message per will: it opens the message already there (the
        first, by name, in an old wallet with more) or a new one. It asks
        only the text: the plugin names a new message "OP_RETURN message"
        (translated, a plain name; ``automatic_message_name`` when a payee
        has it) and stores ``OP_RETURN:<hex>`` with no amount, as the
        Advanced "New heir" window does. An existing message keeps its name.
        ``key``: the message to open (a double click in the Heirs list);
        default: the first one. Returns True when the message was saved.
        """
        if key is None or key not in self.heirs:
            names = message_names(self.heirs)
            key = names[0] if names else None
        heir = self.heirs.get(key) if key is not None else None
        d = BalDialog(self.window, self.bal_plugin,
                      self.bal_plugin.get_window_title(_("A message in the will")))
        vbox = QVBoxLayout(d)
        grid = QGridLayout()
        # Owner's ZIP test: the whole message (80 bytes) on two lines.
        field = MessageEdit(lines=2)
        field.setFixedWidth(48 * char_width_in_lineedit())
        if heir is not None:
            field.setText(message_text(heir[0]))
        grid.addWidget(QLabel(_("Message")), 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(field, 0, 1)
        grid.addWidget(HelpButton(message_help_text()), 0, 2, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(field.counter, 1, 1)
        vbox.addLayout(grid)
        warning = QLabel(message_warning_text())
        warning.setWordWrap(True)
        warning.setStyleSheet(MESSAGE_WARNING_STYLE)
        vbox.addWidget(warning)
        name = key if key is not None else automatic_message_name(
            self.heirs, _("OP_RETURN message"))
        # Owner: the window says what the name is for.
        name_line = QLabel(_(
            "In the heirs list it is called \"{}\": the name is only for you, "
            "it is not written in the transaction.").format(name))
        name_line.setWordWrap(True)
        name_line.setStyleSheet("color:#6A737D;")
        vbox.addWidget(name_line)
        hint = QLabel("")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6A737D;")
        vbox.addWidget(hint)
        if heir is not None:
            def _update_hint():
                """The #135 hint: a new text must be signed again."""
                hint.setText(edit_hint((key, heir[0], ""),
                                       (key, message_address(field.text()), "")))

            field.textChanged.connect(_update_hint)
            _update_hint()
        vbox.addLayout(Buttons(CancelButton(d), OkButton(d)))
        while d.exec():
            text = field.text()
            if not text.strip():
                self.show_error(_("Write the message."))
                continue
            address = message_address(text)
            if heir is not None and address == str(heir[0]):
                return False  # nothing changed: nothing to sign
            locktime = heir[2] if heir is not None else self.will_settings["locktime"]
            try:
                check_one_message(self.heirs, name, address)
                self.set_heir([name, address, "0", str(locktime)])
            except Exception as e:
                self.show_error(heir_error_message(e))
                continue
            self.heir_list_widget.update()
            return True
        return False

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
        """Delete the heirs ``heirs`` and their notes (#138)."""
        for heir in heirs:
            try:
                del self.heirs[heir]
            except Exception as e:
                _logger.debug(f"error deleting heir: {heir} {e}")
                pass
        self.heirs.save()
        heir_notes.delete(self.wallet, heirs)
        self.heir_list_widget.update()
        return True

    def import_heirs(self):
        """Import heirs from a file (the notes of the heirs still there are
        kept; the notes of heirs no longer there are dropped, #138). #140:
        a second message of the file is not imported, and said so."""
        skipped = []

        def importer(path):
            """``Heirs.import_file``, keeping the skipped messages."""
            skipped[:] = self.heirs.import_file(path) or []

        def after_import():
            """Clean the notes, then show the list (and the skipped ones)."""
            heir_notes.clean(self.wallet, list(self.heirs.keys()))
            self.heir_list_widget.update()
            if skipped:
                self.show_message(_(
                    "Not imported: {}. There is already a message in the will: "
                    "only one is allowed.").format(", ".join(skipped)))

        import_meta_gui(
            self.window,
            _("heirs"),
            importer,
            after_import,
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

    def build_will(self, ignore_duplicate=True, keep_original=True):
        """See :func:`bal.engine.build.build_will` (CHANGELOG #119).

        The window passes the old will's creation date when it re-anchors
        ``date_to_check`` (``with_creation_date=True``), as it did before.
        ``ignore_duplicate`` / ``keep_original`` are kept for the callers and
        were never used. The former helper ``update_will`` (only used here)
        is part of the engine function now.
        """
        return build.build_will(self, with_creation_date=True)

    def check_will(self, undo_anticipation=False):
        """See :func:`bal.engine.context.check_will` (``undo_anticipation``:
        the first check of a Check only, #135).

        CHANGELOG #118: ``callback_not_valid_tx=self.delete_not_valid`` is no
        longer passed: ``Will.is_will_valid`` never calls it (and
        ``delete_not_valid`` raises ``NotImplementedError``).
        """
        return context.check_will(self, undo_anticipation=undo_anticipation)

    def _save_will_to_history(self):
        """Persist the current will state into the wallet's LOCAL history.

        Runs after the will has been prepared/built/signed/checked (the
        "Prepare" action, the check dialog's phase 2 and the manual Sign
        action). When the SAVE_HISTORY setting is enabled,
        ``Will.save_valid_transactions_to_history`` stores the still "New" (not
        fully-signed) transactions under the configured label and removes
        entries for fully-signed ("Complete") and stale ones. The wallet tabs
        are then re-rendered through ``_refresh_after_history_save`` (the
        ``after_history_saved`` hook).

        This must never raise: history persistence is a convenience on top of
        the will flows, so any failure is logged and ignored. The steps live
        in :func:`bal.engine.context.save_to_history` (CHANGELOG #118).
        """
        context.save_to_history(self)

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
        """Store one delivery setting and show it in the toolbar fields.

        CHANGELOG #123: the value goes through the one validated write,
        :func:`bal.engine.context.set_will_setting`, into the wallet's own
        settings; Electrum's global ``bal_will_settings`` is never written.
        A value it refuses (a period, a fee below 1, the settings not
        available) is not stored: it is logged and the fields show the
        stored value again.

        On a worker thread (the Check's phase 1, the automatic rebuild) only
        the setting is written; the fields are refreshed on the GUI thread
        through a queued signal (rule I5). On the GUI thread the fields are
        updated first and the value stored after, as before #123, then the
        toolbars show the delivery form again (#127). Returns the value
        stored, or None when nothing was stored.
        """
        if _on_gui_thread():
            if update_all or update_heirs_dialog:
                self.update_widget_value(self.heir_list_widget, field, new_value)
            if update_all or update_will_dialog:
                self.update_widget_value(self.will_list_widget, field, new_value)
        try:
            stored = context.set_will_setting(self, field, new_value)
        except (ValueError, delivery.DeliveryHold) as e:
            _logger.warning(f"delivery setting {field} not stored: {e}")
            self._request_fields_refresh()
            return None
        if not _on_gui_thread():
            self._request_fields_refresh()
        else:
            # #127: a new date (a renewal, the sync to the built will) also
            # changes the period text of the toolbars.
            self._show_delivery_form()
        return stored

    def init_heirs_to_locktime(self, multiverse=False):
        """See :func:`bal.engine.context.init_heirs_to_locktime`: every heir
        gets the delivery date (the locktime coerced to a plain scalar, the
        new values built before any ``Heirs.__setitem__`` saves)."""
        context.init_heirs_to_locktime(self, multiverse)

    def _refresh_invalidated_items(self):
        """Mark the will items already invalidated on-chain (CHANGELOG #79).

        Runs before ``date_to_check`` and the Check Alive are computed, which
        read the VALID items: see ``Will.refresh_invalidated``. Never raises.
        See :func:`bal.engine.context.refresh_invalidated_items`.
        """
        context.refresh_invalidated_items(self)

    def _normalize_check_alive(self):
        """Convert an old Check Alive value to "-Nd" and save it (#80).

        Runs when the will is loaded and before every check: a no-op once
        the value is "-Nd". The rules (same date kept) live in
        ``bal.core.checkalive.normalize_check_alive``; the new value goes
        through :meth:`store_will_setting`. Never raises. See
        :func:`bal.engine.context.normalize_check_alive`.
        """
        context.normalize_check_alive(self)

    def check_alive_info(self):
        """``check_alive_renewal`` for this wallet (None: nothing to do).

        Read-only and offline (no will-executor ping), so it can run when the
        wallet opens. See :func:`bal.engine.context.check_alive_info`.
        """
        return context.check_alive_info(self)

    def ask_renew_will(self, text, parent=None):
        """Ask "Renew" / "Not now"; True when the owner chose to renew.

        Owner decision (CHANGELOG #79): a passed Check Alive is never acted
        on without asking. "Not now" is the default (Esc / closing the box):
        nothing is invalidated and the current will stays valid.
        """
        box = QMessageBox(parent or self.window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(_("Check Alive"))
        box.setText(text)
        renew = box.addButton(_("Renew"), QMessageBox.ButtonRole.AcceptRole)
        not_now = box.addButton(_("Not now"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(not_now)
        box.setEscapeButton(not_now)
        box.exec()
        return box.clickedButton() is renew

    def offer_check_alive_renewal(self, info=None, parent=None):
        """Ask to renew the will when the Check Alive has passed.

        Used when the wallet opens and by "Prepare". On "Renew" the Check
        wizard runs with the renewal already approved (invalidate the old
        will, then prepare the new one). When the will cannot be renewed with
        one click, only the reason is shown. Returns True when something was
        shown.
        """
        info = info or self.check_alive_info()
        if not info:
            return False
        text = check_alive_message(info, datetext.utc_text)
        if info.get("renewable") and info.get("has_will"):
            if self.ask_renew_will(text, parent=parent):
                BalBuildWillDialog(self, renew=True).build_will_task()
        else:
            self.show_warning(text)
        return True

    def check_alive_on_open(self):
        """When the wallet opens: offer the renewal if the Check Alive passed.

        Only in ADVANCED mode, or for a will in the period form also in BASIC
        (#127, ``check_alive_basic``), and with a will in force (the help
        text of the Check Alive always promised this question; nothing did
        it before CHANGELOG #79). Never raises: opening the wallet must not
        fail.
        """
        try:
            if self.disable_plugin or check_alive_basic(
                self.bal_plugin.is_basic_mode(),
                getattr(self, "will_settings", None),
            ):
                return
            if not Will.get_min_locktime(self.willitems):
                return
            # #123: no question on settings that are on hold (display-only
            # values, or the "which date?" question the Check asks first).
            try:
                context.check_holds(self)
            except delivery.DeliveryHold as e:
                _logger.info(f"check alive on open skipped: {e.code}")
                return
            # A will already invalidated on-chain is not "in force".
            self._refresh_invalidated_items()
            info = self.check_alive_info()
            if info and info.get("has_will"):
                self.offer_check_alive_renewal(info)
        except Exception as e:
            _logger.error(f"check alive on open failed: {e!r}")

    def init_class_variables(self):
        """Prepare the state every check reads.

        See :func:`bal.engine.context.init_class_variables` (CHANGELOG #118):
        refresh the invalidated items and the Check Alive value, then set
        ``date_to_check`` (BASIC: now, so the hidden Check Alive never
        governs a check; ADVANCED: the Check Alive, see
        ``bal.core.checkalive.resolve_date_to_check``), the will-executors,
        the Check Alive gate and the heirs' delivery date.

        Errors are logged as before #118: "no heirs" is raised without a log
        (it came before the old ``try``), every later error is logged with
        ``log_error`` and raised again. The two holds of the delivery
        settings (#123, ``bal.core.delivery.DeliveryHold``) come first and
        are expected states, not errors: one info line, then raised.
        """
        try:
            context.init_class_variables(self)
        except NoHeirsException:
            raise
        except delivery.DeliveryHold as e:
            _logger.info(f"init_class_variables: delivery hold ({e.code})")
            raise
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
            self.bal_plugin.is_basic_mode(),
            self.will_settings,
            creation_date=Will.get_creation_time(self.willitems),
        )
        if threshold_ts is not None:
            return locktime < threshold_ts
        return self.date_to_check is not None and locktime < self.date_to_check

    def build_inheritance_transaction(self, ignore_duplicate=True, keep_original=True):
        """The Will tab's "Prepare": check the will and rebuild it if needed.

        Returns the will items, or None when it stops. CHANGELOG #123: with
        the settings unavailable it stops with the error; with the "which
        date?" question pending it asks it (as the Check does) and, with an
        answer, starts again. CHANGELOG #128: the reason of a rebuild is
        shown after the build, and not when the build only gave back the
        will in force.
        """
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
            except delivery.DeliverySettingsUnavailable:
                # #123, fail closed: nothing is prepared on display-only values.
                self.show_error(_(SETTINGS_UNAVAILABLE))
                return
            except delivery.DeliveryChoicePending:
                # #123: the "which date?" question first, as in the Check;
                # with an answer, Prepare starts again (once).
                if self.choose_delivery_date():
                    return self.build_inheritance_transaction(
                        ignore_duplicate, keep_original
                    )
                return
            except AmountException as e:
                self.show_warning(
                    _(
                        "In the inheritance process, the entire wallet will always be fully emptied. Your settings require an adjustment of the amounts.{}"
                    ).format(e)
                )
            except WillExecutorFeeTooHighException as e:
                self.show_error(
                    _("Will-executor fee too high: {}").format(e)
                )
                return
            except CheckAliveAfterDeliveryError:
                self.show_error(
                    _(
                        "Check Alive must be after today and before the delivery date, please update it"
                    )
                )
                return
            except CheckAliveError:
                # CHANGELOG #79: offer the renewal (or say why it is not
                # possible) instead of a bare "CheckAlive is in the past".
                self.offer_check_alive_renewal()
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
                # CHANGELOG #78: the same sentence as the Check dialog and the
                # command line (bal.core.messages), instead of a vague title
                # followed by the raw exception text. The popup is still shown
                # only for the cases that showed one before.
                change_text = None
                if isinstance(
                    e,
                    (
                        HeirChangeException,
                        WillExecutorNotPresent,
                        WillexecutorChangeException,
                        TxFeesChangedException,
                        HeirNotFoundException,
                    ),
                ):
                    change_text = will_change_message(e)

                _logger.info("build will")
                # CHANGELOG #128: the sentence ("... preparing a new will") is
                # shown after the build, and not at all when the build only
                # dropped unsigned drafts and gave back the will in force
                # (the popup below then says "Your will is in force").
                replaced_before = Will.replaced_ids(self.willitems)
                try:
                    self.build_will(ignore_duplicate, keep_original)
                finally:
                    if change_text and not Will.only_brought_back(
                        replaced_before, self.willitems
                    ):
                        self.show_message(change_text)

                # Track whether the rebuild produced a coherent, ready-to-sign
                # will, so we can guide the user through the remaining manual
                # steps (Sign + Broadcast) afterwards.
                rebuilt_ok = False
                try:
                    self.check_will()
                    # Label shown in Electrum's History tab for inheritance
                    # txs: every item, as Prepare always did (CHANGELOG #119).
                    build.label_rebuilt(self, scope="all")
                    rebuilt_ok = True
                except WillExpiredException as e:
                    self.invalidate_will()
                except NotCompleteWillException as e:
                    self.show_error(
                        _("Error:{}\n {}").format(
                            str(e),
                            _("Please, check your heirs, locktime and threshold!"),
                        )
                    )

                self._schedule_history_refresh()

                # Guide the user: say what is left to do after the rebuild.
                # Usually the will is in the "New" state and must be SIGNED
                # and then BROADCAST again (two manual steps). CHANGELOG #122:
                # a rebuild can also give back the transactions already
                # signed and sent (same txids: Will.update_will keeps them).
                if rebuilt_ok and not Will.is_new(self.willitems):
                    if Willexecutors.get_willexecutor_transactions(
                        self.willitems, current_willexecutors=self.willexecutors
                    ):
                        self.show_message(
                            _(
                                "Next step (manual): press 'Broadcast will' to send your "
                                "will to the will-executors."
                            )
                        )
                    else:
                        self.show_message(_("Your will is in force"))
                elif rebuilt_ok:
                    if self.no_willexecutor:
                        # CHANGELOG #94: "will", as everywhere else.
                        next_steps = _(
                            "Your will has been rebuilt and now needs "
                            "to be signed again.\n\n"
                            "Next step (manual):\n"
                            "  1. Press 'Sign' to sign the new transaction."
                        )
                    else:
                        next_steps = _(
                            "Your will has been rebuilt and now needs "
                            "to be signed and re-sent to the will-executors.\n\n"
                            "Next steps (manual):\n"
                            "  1. Press 'Sign' to sign the new transaction.\n"
                            "  2. Press 'Broadcast will' to send it to the "
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
                "{}\n{}".format(
                    _("Electrum was unable to deserialize the transaction:"), e
                )
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
        """Build the invalidation and open it in Electrum's transaction window.

        The Will tab's "Invalidate will", the Will Details window and the
        Check (expired or postponed will). CHANGELOG #123: refused while a
        hold of the delivery settings applies (rule I6).
        """
        if _stopped_by_hold(self):
            return
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
                self.wallet.set_label(result.txid(), "Safe21 Heartbeat invalidation")
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
                        msg = _("signed: {}").format(signed) + "\n"
                    return msg + _("signing: {}").format(tosign)

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
    #     (post-build check_will -> WillExpiredException).  A threshold
    #     already in the past (CheckAliveError) no longer invalidates here:
    #     the owner is asked to renew instead (CHANGELOG #79).
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
        """Refresh the will tabs after an auto-rebuild (GUI thread).

        When the rebuild moved the delivery date (CHANGELOG #110), the date
        widgets are refreshed here, on the GUI thread.
        """
        synced = getattr(self, "_auto_rebuild_synced_locktime", None)
        if synced is not None:
            self._auto_rebuild_synced_locktime = None
            try:
                self.update_setting_widgets(synced, "locktime", update_all=True)
            except Exception as e:
                _logger.debug("_after_auto_rebuild date refresh failed: {}".format(e))
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
            except delivery.DeliveryHold as e:
                # CHANGELOG #123 (rule I6): the "which date?" question waits
                # for the Check, or the settings are not available. Never a
                # question here: skipped.
                _logger.info(f"auto rebuild: delivery hold ({e.code}), nothing to do")
                return False
            except CheckAliveAfterDeliveryError:
                _logger.info(
                    "auto rebuild: check-alive not before the delivery, "
                    "nothing to rebuild/invalidate"
                )
                return False
            except CheckAliveError:
                info = self.check_alive_info()
                if info and info.get("reason") == "expired":
                    # Past the delivery date: the expired case, invalidated
                    # as before (same as WillExpiredException below).
                    _logger.info("auto rebuild: will expired -> invalidate")
                    self._auto_invalidate_will()
                    return True
                # CHANGELOG #79: never invalidate on our own for a passed
                # Check Alive (it used to leave no will in force). The owner
                # is asked to renew when the wallet opens or on Check.
                _logger.info(
                    "auto rebuild: check-alive passed -> nothing done, the "
                    "owner is asked to renew"
                )
                return False
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
                # #134: a postpone that cannot be invalidated here (encrypted
                # wallet, no network) is NOT reverted like a Cancel in the
                # Check window (``engine.context.revert_cancelled_postpone``):
                # nobody declined it, the owner was never asked; the next Check
                # asks him.
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

            # 4b) Keep the stored delivery date in step with the rebuilt
            #     (anticipated) transactions, as the Check dialog does;
            #     otherwise the next Check reads the anticipation as a
            #     postponement and asks for an on-chain invalidation
            #     (CHANGELOG #110).
            self._sync_delivery_after_auto_rebuild()

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
                self.wallet.set_label(txid, "Safe21 Heartbeat invalidation")
            network.run_from_another_thread(
                network.broadcast_transaction(tx, timeout=120), timeout=120
            )
            _logger.info("auto invalidate: broadcast invalidation {}".format(txid))
            return tx
        except Exception as e:
            _logger.error("auto invalidate failed: {}".format(e))
            return None

    def _sync_delivery_after_auto_rebuild(self):
        """Store the rebuilt will's (anticipated) delivery date.

        Same rule as the Check dialog (``Will.anticipated_delivery_to_store``,
        CHANGELOG #110). This runs on the auto-rebuild worker thread, so only
        the setting is stored here, through the one validated write into the
        wallet's own settings (``context.set_will_setting``, #123; a
        ``StoredDict`` item may be written from a worker thread, only its
        ``save_db`` may not). The date widgets are refreshed on the GUI
        thread: by :meth:`_after_auto_rebuild` and by the queued signal of
        :meth:`_request_fields_refresh` (rule I5). The steps live in
        :func:`bal.engine.build.sync_delivery_date` (CHANGELOG #119); an
        error of the rule, or a value the write refuses, is logged and
        nothing is stored, as before.
        """

        def store(field, value):
            """Validated write only; the fields follow on the GUI thread."""
            _logger.info("auto rebuild: delivery date synced to {}".format(value))
            stored = context.set_will_setting(self, field, value)
            self._auto_rebuild_synced_locktime = stored
            self._request_fields_refresh()

        build.sync_delivery_date(
            self,
            store,
            on_error=lambda e: _logger.error(
                "auto rebuild: delivery date sync failed: {}".format(e)
            ),
        )

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
        """The wallet closes: check the will (setting "Rebuild will on wallet
        close"), save it and remove the plugin's tabs and menu.

        CHANGELOG #123 (rule I6): with the "which date?" question pending or
        the settings unavailable, the check is skipped with one log line:
        nothing is built, signed or sent, and no question is ever asked while
        the wallet closes.
        """
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
        # CHANGELOG #84b-2: ``closing=True`` makes the dialog say that the
        # wallet is closing and why it is working; it also verifies with the
        # will-executors what it sent, like the Check button.
        try:
            hold = _hold_code(self)
            if hold is not None:
                _logger.info(f"wallet close: delivery hold ({hold}), will not checked")
            if self.bal_plugin.REBUILD_ON_CLOSE.get() and hold is None:
                close_window = BalBuildWillDialog(self, closing=True)
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
        """Ask the password and sign the will (the Will tab's "Sign").

        Also the Will Details window and the will summary. CHANGELOG #123:
        refused while a hold of the delivery settings applies (rule I6); for
        the live will the WHOLE will is then saved with its current flags
        (:meth:`save_willitems`, which records the will anchor).
        """
        if _stopped_by_hold(self):
            return
        external = will is not None
        willitems = will if external else self.willitems

        def on_success(txs):
            """Put the signed transactions into the will items, then check
            the signatures and run ``callback``. For the live will also:
            save the whole will with the anchor (#123), redraw the Will tab
            and update the History."""
            if txs:
                for txid, tx in txs.items():
                    # Re-parse instead of deepcopy: the signed tx may carry
                    # wallet-derived input info holding a threading.RLock, which
                    # copy.deepcopy cannot pickle (see sign_transactions above).
                    willitems[txid].tx = Will.get_tx_from_any(str(tx))
                if not external:
                    # #123 (FS-1 of the review): save EVERY item, as the
                    # Check's signing does (dialogs.py, task_phase2). Writing
                    # only the signed ones left the stale VALID flag of a
                    # will already invalidated (Prepare after a postpone) in
                    # the wallet; the next load took it for the will in
                    # force and moved the delivery date back to it.
                    self.save_willitems()
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
        """Send the will to its will-executors (the Will tab's "Broadcast will").

        Also the Will Details window, the will summary and the settings'
        re-send. CHANGELOG #123: refused while a hold of the delivery
        settings applies (rule I6).
        """
        if _stopped_by_hold(self):
            return
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
        # Read the CURRENT executor list from the config, so an executor the
        # user deselected after building the will is not contacted anymore.
        willexecutors = Willexecutors.get_willexecutor_transactions(
            willitems,
            force=force,
            current_willexecutors=Willexecutors.get_willexecutors(
                self.bal_plugin, update=False, task=False
            ),
        )

        def getMsg(willexecutors):
            msg = _("Broadcasting Transactions to Will-Executors:") + "\n"
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
                    _("checking {} - {} : {}").format(
                        willitems[wid].we["url"], wid, _("Waiting")
                    )
                )
                w = willitems[wid]
                w.set_check_willexecutor(
                    Willexecutors.check_transaction(wid, w.we["url"])
                )
                self.waiting_dialog.update(
                    _("checked {} - {} : {}").format(
                        willitems[wid].we["url"],
                        wid,
                        willitems[wid].get_status("CHECKED"),
                    )
                )

        if error["flag"]:
            return True

    def export_json_file(self, path, will=None):
        """Write the will as a JSON file (``will``: an imported will instead).

        The live will's items are marked EXPORTED and the whole will is
        saved first; CHANGELOG #123: through :meth:`save_willitems`, so the
        stored will matches the items in memory and the will anchor
        describes exactly what is stored.
        """
        if will is None:
            for wid in self.willitems:
                self.willitems[wid].set_status("EXPORTED", True)
            self.save_willitems()
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
    DOWNLOAD_FAILED_MESSAGE = N_(
        "Could not download the will-executors list.\n\n"
        "This is usually caused by your internet connection or a firewall, "
        "not by the plugin. Please check your connection (a VPN often helps) "
        "and try again."
    )

    # Shown when the download fails while Electrum is connected through Tor:
    # the most common cause is a slow Tor connection, so guide the user
    # accordingly instead of the generic message above.
    DOWNLOAD_FAILED_TOR_MESSAGE = N_(
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
                        "No active will-executor servers found for the "
                        "{chain} network.\n\n"
                        "The welist server at {url} responded but "
                        "returned no will-executors for this chain."
                    ).format(chain=err.chain, url=err.url))
                elif err.url:
                    # Advanced mode with a non-empty error (network issue).
                    self.show_warning(_(
                        "Could not reach the configured welist server.\n\n"
                        "Server: {url}\n"
                        "Error: {reason}\n\n"
                        "Please verify the welist server URL in the plugin "
                        "settings."
                    ).format(url=err.url, reason=err.reason))
                else:
                    # Basic mode: the server responded but has no data for this
                    # chain.
                    self.show_warning(_(
                        "No active will-executor found for the "
                        "{chain} network."
                    ).format(chain=err.chain))
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

    def download_and_select_willexecutors(self, select_responding=True,
                                          ping_without_list=False):
        """Download the will-executor list, ping it, keep only who answers.

        The "Automatically download and select willexecutors" choice of the
        wizard (Task #02), shared since CHANGELOG #134 by the old wizard page
        (ADVANCED) and the BASIC wizard's "Will-executors" page, so the rule
        lives in one place:

        * ``self.willexecutors`` is reloaded from the saved list, then the
          welist is downloaded (``download_list``: the "Please wait" window,
          the warnings when it fails) and merged into it;
        * every will-executor is pinged (``ping_willexecutors``);
        * ``select_responding``: the green SELECTED tick must follow the
          green ping dot, i.e. select ONLY servers that actually answered
          the ping (status 200) and DESELECT every server that did not
          (timeout / error / never pinged). Why the explicit deselect
          matters: a server selected on an earlier download but now
          unreachable stayed selected, so the plugin kept broadcasting to a
          dead server and got stuck. Re-running this re-evaluates every
          server: one that failed before but now answers is selected again.
          The status is compared as a string ("200"), like the will-executor
          list view, and read with ``.get()`` so a missing key never raises.
          False ("Only download willexecutors list"): the selection stays;
        * the list is saved (``Willexecutors.save``) after the ping.

        ``ping_without_list`` (the BASIC page only): when the download fails
        the SAVED list is pinged and selected the same way, so an offline
        computer ends with nobody selected (the page then says nobody
        answers) instead of a stale selection. The old page keeps today's
        behaviour (False: a failed download changes nothing).

        Returns True when the list was downloaded. Both windows are modal:
        the whole run is over when this returns.
        """
        self.willexecutors = Willexecutors.get_willexecutors(self.bal_plugin)
        outcome = {"downloaded": False}
        # #137 (owner): read BEFORE anything is deselected here, so the
        # first-time seed sees the list as it was; and the URLs known
        # before this download (a URL that is not is new on the network).
        user_deselected = Willexecutors.user_deselected(self.bal_plugin)
        known = set(self.willexecutors)
        self.new_willexecutors = set()

        def ping_on_done(*_args):
            """After the ping (answer or failure): select and save.

            #137: who answers is selected, except the will-executors the
            user deselected on purpose (they stay deselected); one the
            plugin deselected before (no answer, Exclude) is selected
            again when it answers; the new ones that answer are selected
            and remembered for the summary ("new in the network").
            """
            if select_responding:
                self.new_willexecutors = Willexecutors.select_after_ping(
                    self.willexecutors, user_deselected, known)
            Willexecutors.save(self.bal_plugin, self.willexecutors)

        def ping():
            """Ping every will-executor of ``self.willexecutors``."""
            self.ping_willexecutors(self.willexecutors, ping_on_done, ping_on_done)

        def on_success(_willexecutors):
            """The list was downloaded (and merged by ``download_list``)."""
            outcome["downloaded"] = True
            ping()

        self.download_list(self.willexecutors, on_success)
        if ping_without_list and not outcome["downloaded"]:
            ping()
        return outcome["downloaded"]

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
        """Refresh the will statuses and every widget of the window.

        CHANGELOG #129: the "hide" filter flags are no longer re-read here
        (the options are gone; the Will tab lists the will by versions).
        """
        try:
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
            # immediately. Guarded so a missing widget never breaks the rest
            # of the refresh.
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
                    # #127: the period text or the date, as stored.
                    _settings_widget.apply_delivery_form()
        except Exception as e:
            _logger.error(f"error while updating window: {e}")


