"""
bal.gui.qt.widgets
==================

Reusable, self-contained Qt widgets used to build the BAL tabs and dialogs.

These are "leaf" widgets: they receive the :class:`BalWindow` controller (and
any data they need) as constructor arguments at runtime, so this module does
not import ``window``/``dialogs`` and therefore introduces no import cycles.

Contents:
    * ClickableLabel, BalLineEdit, BalTextEdit, BalCheckBox - thin Qt wrappers
    * BalTxFeesWidget                                       - fee-rate editor
    * _LockTimeEditor + BalTimeEditWidget + raw/date editors - locktime editing
    * ThresholdTimeWidget / LockTimeWidget                  - threshold & locktime
    * WillSettingsWidget                                    - the settings panel
    * PercAmountEdit                                        - amount-or-percentage editor
    * WillWidget                                            - single will-tx box
"""

from typing import TYPE_CHECKING

from ...core.heirs import get_op_return_hex, is_op_return_address
from ...core.input_rules import (
    LockTimeEditor,
    normalize_locktime_raw_text,
    normalize_perc_amount_text,
    parse_perc_amount,
)
from ...core.reminders import build_ics_reminders, write_temp_ics
from .calendar import BalCalendar, BalCalendarButton
from .common import (
    DECIMAL_POINT,
    NLOCKTIME_BLOCKHEIGHT_MAX,
    NLOCKTIME_MAX,
    Any,
    BalTimestamp,
    BTCAmountEdit,
    ColorScheme,
    Decimal,
    HelpButton,
    Optional,
    QAbstractSpinBox,
    QCheckBox,
    QColor,
    QComboBox,
    QDateTime,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPainter,
    QPalette,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionFrame,
    Qt,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    Union,
    Util,
    Will,
    _,
    _logger,
    char_width_in_lineedit,
    datetime,
    getSaveFileName,
    log_error,
    os,
    partial,
    pyqtSignal,
    read_QIcon_from_bytes,
    signature_suffix,
    status_color,
)

if TYPE_CHECKING:
    from .window import BalWindow


class ClickableLabel(QLabel):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)



class BalTxFeesWidget(QWidget):
    valueChanged = pyqtSignal()
    current_value = None

    def __init__(self, bal_window, parent, value=None):
        super().__init__(parent)
        self.bal_window = bal_window
        layout = QHBoxLayout(self)
        self.txfee_widget = QSpinBox(self)
        self.txfee_widget.setMinimum(1)
        self.txfee_widget.setMaximum(10000)
        # Group C / C5: hovering the fee field explains what the number means.
        self.txfee_widget.setToolTip(
            _("Mining fee rate in sat/vByte used for the will transactions")
        )
        value = (
            value
            if value
            else self.bal_window.bal_plugin.WILL_SETTINGS.get()["baltx_fees"]
        )
        self.set_value(value)
        self.default_value = self.bal_window.bal_plugin.default_will_settings()[
            "baltx_fees"
        ]
        self.txfee_widget.valueChanged.connect(self.on_heir_tx_fees)
        #label = ClickableLabel("＄")
        #label.doubleClicked.connect(self.doubleclick)
        #layout.addWidget(label)
        button = HelpButton(_("mining fees expressed in sats/vbyte to be used in the Bitcoin transaction.\nHigher value ensure your transaction will be confirmed"))
        button.setText("丰")
        # Hover tooltip for the small "丰" help icon (the long explanation still
        # appears on click via the HelpButton); makes the icon self-explanatory.
        button.setToolTip(_("Miner fee, click for more information"))
        # Enlarge only the "丰" glyph on the button itself; without scoping the
        # rule to QPushButton it also enlarged the tooltip font (making it bigger
        # than the other tooltips, e.g. the calendar one). Scoping it keeps the
        # tooltip at the default size like everywhere else.
        button.setStyleSheet("QPushButton{font-size: 16px;}")
        layout.addWidget(button)
        layout.addWidget(self.txfee_widget)
        # Expose the leading icon (prefix) and the editable field so the parent
        # WillSettingsWidget can align them on a grid (see its vertical layout).
        self.prefix_widget = button
        self.field_widget = self.txfee_widget

    def doubleclick(self, event=None):
        pass

    def set_read_only(self, read_only=True):
        # Show the fee but make it non-editable (no spin arrows, no keyboard),
        # so it can only be changed from the "Build your will" wizard.
        self.txfee_widget.setReadOnly(read_only)
        self.txfee_widget.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
            if read_only
            else QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        # Light-grey background when locked, so the read-only state is visible
        # (same look as the date fields); empty stylesheet restores the
        # editable appearance used inside the wizard.
        self.txfee_widget.setStyleSheet(
            "QSpinBox{background-color:#f0f0f0;}" if read_only else ""
        )

    def get_value(self):
        return self.txfee_widget.value()

    def set_value(self, value, emit=True):
        value = int(value) if value is not None else 20
        if getattr(self, "_updating", False):
            return

        self._updating = True
        try:
            self.current_value = value
            spin = self.txfee_widget
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

        finally:
            self._updating = False

        if emit:
            spin.valueChanged.emit(value)

    def on_heir_tx_fees(self, value=None, update_all=True):
        if value != self.current_value:
            try:
                self.set_value(value)
                if update_all:
                    self.bal_window.update_setting_widgets(
                        self.get_value(), "baltx_fees", True
                    )
            except Exception as e:
                _logger.error(f"error while trying to update txfees{e}")
                log_error(e)
        else:
            pass



class _LockTimeEditor(LockTimeEditor):
    """Qt-side locktime editor base.

    The pure acceptance bounds and helpers live in ``bal.core.input_rules``;
    this is just the mixin that lets Qt widgets reuse them.
    """


class BalTimeEditWidget(QWidget, _LockTimeEditor):
    valueEdited = pyqtSignal()
    _setting_locktime = False
    current_value = None
    current_index = None
    default_value = None

    help_text = (
        "if you choose Raw, you can insert various options based on suffix:\n"
        + " - d: number of days after current day(ex: 1d means tomorrow)\n"
        + " - y: number of years after currrent day(ex: 1y means one year from today)\n"
    )
    label_text = None
    tooltip_text = None
    base_field = None

    def __init__(self, bal_window, parent, default_locktime=None):
        super().__init__(parent)
        self.bal_window = bal_window

        hbox = QHBoxLayout()
        self.setLayout(hbox)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        self.setMinimumWidth(40 * char_width_in_lineedit())
        self.locktime_raw_e = TimeRawEditWidget(self, time_edit=self)
        self.locktime_date_e = LockTimeDateEdit(self, time_edit=self)
        self.editors = [self.locktime_raw_e, self.locktime_date_e]
        self.combo = QComboBox()
        options = [_("Raw"), _("Date")]
        self.option_index_to_editor_map = {
            0: self.locktime_raw_e,
            1: self.locktime_date_e,
        }
        self.combo.addItems(options)
        # SIMPLE / ADVANCED (task: hide the Raw/Date selector in BASIC mode).
        #
        # In BASIC mode the user must not see or use the Raw/Date selector:
        # every date field is forced to the calendar ("Date") editor and the
        # combo is hidden. We keep a flag so the rest of __init__ can force the
        # Date editor regardless of the stored value's format.
        self._basic_mode = self.bal_window.bal_plugin.is_basic_mode()
        # Tracks whether the user PICKED an editor by hand from the Raw/Date
        # selector (set only on the combo's `activated` signal, i.e. real user
        # interaction). It lets the runtime BASIC<->ADVANCED switch respect a
        # manual "Date" choice in ADVANCED instead of forcing RAW back. It stays
        # False for programmatic changes (defaults, switches).
        self._user_picked_editor = False
        default_index = 0
        if not default_locktime:
            default_locktime = self.bal_window.bal_plugin.WILL_SETTINGS.get()[self.base_field]
        # Default editor per mode (owner request):
        #   * BASIC   -> Date editor (index 1); the Raw/Date selector is hidden,
        #               so the user always picks a date and never sees RAW.
        #   * ADVANCED -> RAW editor (index 0) by default; the selector stays
        #               visible so the user can switch to Date manually.
        if self._basic_mode:
            default_index = 1
        else:
            default_index = 0
            # In ADVANCED we default to RAW. If the stored value is an absolute
            # timestamp (a bare number), showing it in RAW would display that
            # raw number; substitute the relative default ("1y"/"30d") so the
            # RAW editor opens with a human-readable duration instead.
            try:
                int(default_locktime)
                is_absolute = True
            except Exception:
                is_absolute = False
            if is_absolute:
                default_locktime = (
                    self.bal_window.bal_plugin.default_will_settings_relative()[
                        self.base_field
                    ]
                )
        #hbox.addWidget(QLabel(self.label_text))
        help_button=HelpButton(self.help_text)
        help_button.setText(self.label_text)
        # Show a short label (e.g. "Delivery time" / "Check Alive") when the
        # user hovers the icon, so the emoji button is self-explanatory.
        if self.tooltip_text:
            help_button.setToolTip(_(self.tooltip_text))
        #help_button.setStyleSheet("font-size: 155555);
        hbox.addWidget(help_button)
        # Expose the leading icon (prefix) so the parent WillSettingsWidget can
        # align all rows on a common left edge (see its vertical layout).
        self.prefix_widget = help_button
        self.combo.currentIndexChanged.connect(self.on_current_index_changed)
        # `activated` fires ONLY on real user interaction with the selector (not
        # on programmatic setCurrentIndex), so we use it to remember that the
        # user picked the editor by hand. This is what lets the runtime
        # BASIC<->ADVANCED switch respect a manual "Date" choice in ADVANCED.
        self.combo.activated.connect(self._on_user_picked_editor)

        for w in self.editors:
            w.setVisible(False)
            w.setEnabled(False)

        self.editor = self.option_index_to_editor_map[default_index]
        self.editor.setVisible(True)
        self.editor.setEnabled(True)
        self.set_index(default_index)
        #self.on_current_index_changed(default_index)
        self.set_value(default_locktime)
        self.current_value=default_locktime
        hbox.addWidget(self.combo)
        # In BASIC mode hide the Raw/Date selector entirely: the field stays
        # locked on the calendar editor chosen above, so the user only ever
        # picks a Date and cannot switch to RAW.
        if self._basic_mode:
            self.combo.setVisible(False)
        for w in self.editors:
            hbox.addWidget(w)

        hbox.addStretch(1)
        # spssscer_widget = QWidget()
        # spacer_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # hbox.addWidget(spacer_widget)
        self.valueEdited.connect(lambda: self.update_will_settings(True))
        self.locktime_raw_e.editingFinished.connect(self.valueEdited.emit)
        self.locktime_date_e.dateTimeChanged.connect(self.valueEdited.emit)
        #self.combo.currentIndexChanged.connect(self.valueEdited.emit)

    def update_will_settings(
        self,
        update_all=False,
        update_will_dialog=False,
        update_heirs_dialog=False,
    ):
        self.bal_window.update_setting_widgets(
            self.get_value(),
            self.base_field,
            update_all,
            update_will_dialog,
            update_heirs_dialog,
        )

    def _on_user_picked_editor(self, i):
        """Record that the user chose the Raw/Date editor by hand.

        Connected to the combo's ``activated`` signal, which only fires on real
        user interaction (not on programmatic ``setCurrentIndex``). Used by the
        runtime BASIC<->ADVANCED switch to respect a manual "Date" choice in
        ADVANCED instead of forcing RAW back.
        """
        self._user_picked_editor = True

    def on_current_index_changed(self, i):
        self.current_index = i
        for w in self.editors:
            w.setVisible(False)
            w.setEnabled(False)
        # prev_locktime = self.editor.get_value()
        self.editor = self.option_index_to_editor_map[i]
        if i==0:
            self.editor.set_value(self.bal_window.bal_plugin.default_will_settings_relative()[self.base_field])
        else:
            self.editor.set_value(self.bal_window.bal_plugin.default_will_settings_absolute()[self.base_field])
        self.valueEdited.emit()
        # if self.editor.is_acceptable_locktime(prev_locktime):
        #    self.editor.set_value(prev_locktime, force=False)
        self.editor.setVisible(True)
        self.editor.setEnabled(True)
        self.bal_window.update_combo_setting_widgets(i, self.base_field,True)

    def get_value(self) -> Optional[str]:
        val = self.editor.get_value()
        #return self.current_value
        return val

    def set_index(self, index):
        if self.current_index != index:
            self.combo.setCurrentIndex(index)
            #self.on_current_index_changed(index, force)

    def apply_user_type_visibility(self):
        """Re-apply the BASIC/ADVANCED visibility of the Raw/Date selector.

        WHY this is needed (bug reported by the owner, task #04): the Raw/Date
        combo is hidden in BASIC mode and shown in ADVANCED mode. That visibility
        used to be decided ONLY in ``__init__`` (see the ``self._basic_mode``
        block there). The WILL and HEIR tab toolbars are created once and then
        REUSED for the whole session (they are not rebuilt when the user switches
        USER TYPE), so after switching from BASIC to ADVANCED the combo stayed
        hidden there and the user could only use the "Date" editor. The same
        happened when a RAW value was pushed from the wizard: the raw number
        showed (the active editor was updated) but the selector box stayed hidden
        because nothing ever re-showed it. The wizard worked only because it is
        recreated every time it is opened.

        This method re-reads ``is_basic_mode()`` and shows/hides ONLY the combo.
        It deliberately does NOT change the current value or the active editor
        (owner request: keep the current value to avoid confusing the delivery
        date with the inheritance). It is called from
        ``WillSettingsWidget.apply_user_type_visibility()`` (triggered by
        ``BalWindow.update_all()``, which also runs on every CHECK/refresh), so
        it must be side-effect free on the editor - otherwise pressing CHECK
        would reset a manual Date/RAW choice back to the default. The actual
        per-mode editor default lives in ``apply_user_type_editor_default()``,
        which is called ONLY on a real USER TYPE change.

        It is safe to call repeatedly and on either layout (horizontal toolbar or
        vertical wizard): it only flips the visibility of the Raw/Date combo.
        """
        try:
            basic = self.bal_window.bal_plugin.is_basic_mode()
        except Exception:
            # If the mode cannot be read, show the combo (the safe, most
            # capable default for an existing widget).
            basic = False
        self.combo.setVisible(not basic)

    def apply_user_type_editor_default(self):
        """Set the ACTIVE Raw/Date editor to the per-mode default.

        Split out from ``apply_user_type_visibility`` (Opzione 2) so it runs
        ONLY on a real BASIC<->ADVANCED USER TYPE change, not on every
        ``update_all()``/CHECK refresh. That is what stopped pressing CHECK from
        resetting a manual Date choice back to RAW.

        Behaviour:
          * BASIC    -> always the Date editor (index 1); the selector is hidden,
                       so the field must not stay on RAW.
          * ADVANCED -> RAW by default (index 0), UNLESS the user previously
                       picked an editor by hand (``_user_picked_editor``), in
                       which case their choice is left untouched.
        """
        try:
            basic = self.bal_window.bal_plugin.is_basic_mode()
        except Exception:
            basic = False
        try:
            if basic:
                self.set_index(1)
            else:
                if not self._user_picked_editor:
                    self.set_index(0)
        except Exception:
            # Never let a cosmetic editor switch break mode toggling.
            pass

    def set_value(
        self,
        x: Any,
        force=None,
        update_all=False,
        update_will_dialog=False,
        update_heirs_dialog=False,
    ) -> None:
        if not x:
            if self.current_index == 0:
                x = self.bal_window.bal_plugin.default_will_settings_relative()[self.base_field]
            elif self.current_index == 1:
                x = self.bal_window.bal_plugin.default_will_settings_absolute()[self.base_field]
        if x != self.get_value():
            self.editor.set_value(x)
            self.current_value = x
            self.bal_window.update_setting_widgets(x, self.base_field)

    def set_read_only(self, read_only=True):
        """Show the value but make it non-editable.

        Used everywhere except the "Build your will" wizard, where the date is
        the only place the user is allowed to change it. The Raw/Date combo is
        disabled and both editors become read-only with no spin buttons.
        """
        self.combo.setEnabled(not read_only)
        for w in self.editors:
            w.set_read_only(read_only)



class TimeRawEditWidget(QWidget):
    editingFinished = pyqtSignal()

    def is_acceptable_locktime(self, value):
        return True

    def __init__(self, parent, time_edit=None):
        super().__init__(parent)
        self.editor = LockTimeRawEdit(parent, time_edit)
        self.label = QLabel("")
        # Group C / C3: this trailing label shows the ABSOLUTE date computed
        # from the RAW value (e.g. "30d" -> "2027-06-23"), so it needs room for a
        # full "YYYY-MM-DD" string (~10 characters). Only the input box itself
        # (LockTimeRawEdit) is narrowed; shrinking this label was a mistake that
        # truncated the computed date.
        self.label.setFixedWidth(10 * char_width_in_lineedit())
        self.layout = QHBoxLayout(self)
        self.layout.addWidget(self.editor)
        self.layout.addWidget(self.label)
        self.editor.editingFinished.connect(self.editingFinished.emit)
        self.get_value = self.editor.get_value
        self.set_value = self.editor.set_value

    def set_read_only(self, read_only=True):
        self.editor.setReadOnly(read_only)
        # Match the Date editor: grey background when locked so the read-only
        # state is visible; empty stylesheet restores the editable look.
        self.editor.setStyleSheet(
            "QLineEdit{background-color:#f0f0f0;}" if read_only else ""
        )



class LockTimeRawEdit(QLineEdit, _LockTimeEditor):
    def __init__(self, parent=None, time_edit=None):
        QLineEdit.__init__(self, parent)
        # Group C / C3: narrow the RAW input to roughly a third of its former
        # width. The accepted values are short relative durations such as "30d"
        # or "1y", so a handful of characters is plenty while still leaving room
        # for larger day counts (e.g. "3650d").
        self.setFixedWidth(6 * char_width_in_lineedit())
        self.textChanged.connect(self.numbify)
        self.isdays = False
        self.isyears = False
        self.time_edit = time_edit

    def numbify(self):
        # Only digits plus the day ("d") and year ("y") suffixes are accepted.
        # The block-height suffix ("b") was removed (A1): locktimes are always
        # UNIX timestamps now, so block-relative input is no longer allowed.
        # The sanitisation itself lives in bal.core.input_rules.
        text = self.text().strip()
        pos = self.cursorPosition()
        s, isdays, isyears, pos = normalize_locktime_raw_text(text, pos)
        self.isdays = isdays
        self.isyears = isyears
        self.blockSignals(True)
        self.setText(s)
        self.blockSignals(False)
        # self.set_value(s, force=False)
        self.current_value = s
        # setText sets Modified to False.  Instead we want to remember
        # if updates were because of user modification.
        self.setModified(self.hasFocus())
        self.setCursorPosition(pos)

    def get_value(self) -> Optional[str]:
        try:
            return str(self.text())
        except Exception:
            return None

    def set_value(self, x: Any, force=True) -> None:
        if x != self.get_value():
            self.blockSignals(True)
            self.setText(str(x))
            self.blockSignals(False)
            self.numbify()



class LockTimeDateEdit(QDateTimeEdit, _LockTimeEditor):
    # GUARD (kept on purpose, A1): NLOCKTIME_BLOCKHEIGHT_MAX is the highest value
    # Bitcoin interprets as a *block height*. By forcing the minimum to one above
    # it, every locktime entered here is guaranteed to be a UNIX *timestamp*,
    # never a block height. This is NOT block-height ordering; it is the
    # "bouncer" that prevents block-height values from ever being used again.
    min_allowed_value = NLOCKTIME_BLOCKHEIGHT_MAX + 1
    max_allowed_value = _LockTimeEditor.get_max_allowed_timestamp()

    def __init__(self, parent=None, time_edit=None):
        QDateTimeEdit.__init__(self, parent)
        self.setMinimumDateTime(datetime.fromtimestamp(self.min_allowed_value))
        self.setMaximumDateTime(datetime.fromtimestamp(self.max_allowed_value))
        #self.setDateTime(QDateTime.currentDateTime())
        self.time_edit = time_edit

    def set_read_only(self, read_only=True):
        # Read-only display: keyboard editing disabled and the up/down spin
        # arrows removed, so the date can only be changed from the wizard.
        self.setReadOnly(read_only)
        self.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
            if read_only
            else QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        # A read-only QDateTimeEdit keeps a white background by default, which
        # does not visually signal that it is locked.  Paint it light grey (like
        # the disabled combo/fee fields next to it) so the user sees at a glance
        # that the date is not editable here; an empty stylesheet restores the
        # default look when the field is made editable again (in the wizard).
        self.setStyleSheet(
            "QDateTimeEdit{background-color:#f0f0f0;}" if read_only else ""
        )

    def get_value(self) -> Optional[int]:
        #dt = self.dateTime().toPyDateTime()
        #locktime = int(time.mktime(dt.timetuple()))
        #p#
        #dt = dt_edit.dateTime()
        ## QDateTimets = dt.toSecsSinceEpoch()

        dt = self.dateTime()
        _ts = dt.toSecsSinceEpoch()

        return _ts


    def set_value(self, x: Any, force=False) -> None:
        if not self.is_acceptable_locktime(x):
            self.setDateTime(QDateTime.currentDateTime())
            return
        try:
            x = int(x)
        except Exception:
            x = QDateTime.currentDateTime().timestamp()
        finally:
            # Use the overflow-safe converter: on Windows datetime.fromtimestamp
            # raises OverflowError for timestamps past 2038 (e.g. NLOCKTIME_MAX).
            _dt = BalTimestamp._safe_fromtimestamp(x)
            self.alarm = _dt
            # Store the LOCAL wall-clock time, not the aware-UTC datetime:
            # QDateTimeEdit keeps the given wall time with a LocalTime spec, so
            # an aware-UTC datetime would make get_value() read back a timezone-
            # shifted epoch. That broke the set_value -> get_value roundtrip and
            # kept the valueEdited -> update_setting_widgets -> set_value cycle
            # firing forever (infinite RecursionError on wizard "Next").
            self.setDateTime(_dt.astimezone().replace(tzinfo=None))



class ThresholdTimeWidget(BalTimeEditWidget):
    # rich_text=True is used by the HelpButton, so HTML tags (<b>, <br>) render.
    help_text = (
        "<b>CHECK ALIVE</b><br><br>"
        "In DATA mode:<br>"
        "set the date for the \u201ccheck alive\u201d parameter.<br>"
        "When you open the wallet, if the \u201ccheck alive\u201d date has passed, "
        "the plugin will ask if you want to postpone the inheritance, since it "
        "assumes you still have control of the wallet and that you are still "
        "alive.<br><br>"
        "In RAW mode:<br>"
        "When less than this time is missing, ask to invalidate.<br>"
        "If you fail to invalidate during this time, your transactions will be delivered to your heirs.<br><br>"
        "if you choose Raw, you can insert various options based on suffix:<br>"
        " - d: number of days after current day(ex: 1d means tomorrow)<br>"
        " - y: number of years after current day(ex: 1y means one year from today)<br>"
        "<br><b>Note:</b> the date/time is expressed in your computer's "
        "<b>Local time</b> (not UTC time).<br>"
    )
    label_text = "🚨"
    #label_text = "Check Alive"
    tooltip_text = "Check Alive"
    base_field = "threshold"

    def __init__(self, bal_window, parent, init_value=None):
        if init_value is None:
            init_value = bal_window.bal_plugin.WILL_SETTINGS.get()["threshold"]
        super().__init__(bal_window, parent, init_value)
        self.default_value = self.bal_window.bal_plugin.default_will_settings()[
            "threshold"
        ]



class LockTimeWidget(BalTimeEditWidget):
    # rich_text=True is used by the HelpButton, so HTML tags (<b>, <br>) render.
    help_text = (
        "<b>DELIVERY TIME</b><br><br>"
        "Set Locktime for transactions.<br>"
        "Any time is needed transaction will be anticipated by 1day<br><br>"
        # The Raw locktime syntax below is only available in ADVANCED mode
        # (in BASIC mode the Raw/Date selector is hidden and only the Date
        # picker is shown), so we say so explicitly to avoid confusing users.
        "(ONLY IN ADVANCED MODE)<br>"
        "if you choose Raw, you can insert various options based on suffix:<br>"
        " - d: number of days after current day(ex: 1d means tomorrow)<br>"
        " - y: number of years after currrent day(ex: 1y means one year from today)<br>"
        "<br><b>Note:</b> the date/time is expressed in your computer's "
        "<b>Local time</b> (not UTC time).<br>"
    )
    label_text = "🚛"
    #label_text = "Locktime"
    # Hover tooltip for the delivery-time icon; mirrors the style of the fee
    # icon tooltip ("..., click for more information") so the two are consistent.
    tooltip_text = "Delivery Time, click for more information"
    base_field = "locktime"

    def __init__(self, bal_window, parent, init_value=None):
        if init_value is None:
            init_value = bal_window.bal_plugin.WILL_SETTINGS.get()["locktime"]
        super().__init__(bal_window, parent, init_value)
        self.default_value = self.bal_window.bal_plugin.default_will_settings()[
            "locktime"
        ]



class WillSettingsWidget(QWidget):

    def __init__(self, bal_window: "BalWindow", parent, layout_type="h",
                 read_only=True):
        self.widgets = {}
        QWidget.__init__(self, parent)
        self.bal_window = bal_window
        # When read_only=True (toolbars, Heirs tab) the delivery time, check
        # alive and fee fields are display-only; they can only be edited from
        # the "Build your will" wizard, which passes read_only=False.
        self.read_only = read_only
        # Remember which layout this widget was built with, so the per-update
        # hooks (apply_editable_dates / apply_user_type_visibility) can tell the
        # main-window toolbar ("h") apart from the wizard (vertical) - needed
        # for the BASIC-mode Check-Alive "visible but read-only in the main
        # window only" behaviour.
        self.layout_type = layout_type
        box = QHBoxLayout(self) if layout_type == "h" else QVBoxLayout(self)

        self.calendar_button = BalCalendarButton(self.bal_window, self._ics_provider)
        self.calendar_button.setIcon(
            read_QIcon_from_bytes(
                self.bal_window.bal_plugin.read_file("icons/calendar.png")
            )
        )
        self.calendar_button.setToolTip(
            _("Export reminder dates to your calendar (.ics)")
        )
        self.widgets["locktime"] = LockTimeWidget(bal_window, self)
        self.widgets["threshold"] = ThresholdTimeWidget(bal_window, self)
        self.widgets["locktime"].valueEdited.connect(self.on_locktime_change)
        self.widgets["threshold"].valueEdited.connect(self.on_locktime_change)
        # SIMPLE / ADVANCED: in BASIC mode the "Check Alive" (threshold) row is
        # hidden EVERYWHERE (both the main-window toolbar and the wizard). The
        # user must never see or touch the Check Alive in BASIC. The widget is
        # still created and kept in self.widgets so the rest of the code and the
        # saved settings keep working; it is only hidden from view. (This
        # reverts the v0.5.2 experiment that had shown it read-only in the main
        # window - owner asked to hide it again.)
        if bal_window.bal_plugin.is_basic_mode():
            self.widgets["threshold"].setVisible(False)
        # self.widgets['baltx_fees'].valueChange.connect(self.bal_window.update_setting_widgets)
        self.on_locktime_change()
        self.widgets["baltx_fees"] = BalTxFeesWidget(bal_window, self)
        if not hasattr(bal_window, "txfee_widgets"):
            bal_window.txfee_widgets = []

        w = self.widgets["baltx_fees"]
        if w not in bal_window.txfee_widgets:
            bal_window.txfee_widgets.append(w)
        if layout_type == "h":
            box.addWidget(self.widgets["locktime"])
            box.addWidget(self.widgets["threshold"])
            box.addWidget(self.calendar_button)
            box.addWidget(self.widgets["baltx_fees"])
        else:
            # ----------------------------------------------------------------- #
            # Vertical layout (the "Build your will" wizard) - Layout H.         #
            #                                                                     #
            # User requirements (allegato4):                                      #
            #   * the leading ICONS (delivery time, calendar, fee and - in       #
            #     ADVANCED mode - check-alive) must be aligned one under the      #
            #     other on the left;                                             #
            #   * the editable FIELDS must all start from the SAME x position,    #
            #     just to the right of their icon;                               #
            #   * the fee field must be WIDER (its up/down arrows were covering   #
            #     the digits) - not a tiny box.                                  #
            #                                                                     #
            # DESIGN NOTE - why we do NOT pull the icon/field out of each        #
            # composite into a shared grid: the LockTime / Check-Alive composites #
            # hold TWO editors (Raw and Date) plus a Raw/Date combo that the user #
            # can switch at runtime in ADVANCED mode; ``self.editor`` is swapped  #
            # live (see on_current_index_changed). Reparenting only the currently #
            # active editor would orphan the other editor and the combo and break #
            # ADVANCED mode. So we keep every composite INTACT and instead align  #
            # them by:                                                            #
            #   1. forcing every leading icon (prefix_widget) to the SAME fixed   #
            #      width, so each composite's field starts at the same x; and     #
            #   2. stacking the whole composites left-aligned in the VBox.        #
            # This is robust to the Raw/Date switching and keeps all internal     #
            # logic working untouched.                                            #
            # ----------------------------------------------------------------- #
            locktime_w = self.widgets["locktime"]
            threshold_w = self.widgets["threshold"]
            fees_w = self.widgets["baltx_fees"]

            # Common icon width = the widest leading icon (incl. the calendar
            # button). Forcing every icon to this width makes the icons line up
            # one under the other and, because each field sits immediately to the
            # right of its icon, makes every field start at the same x too.
            icon_w = max(
                locktime_w.prefix_widget.sizeHint().width(),
                threshold_w.prefix_widget.sizeHint().width(),
                fees_w.prefix_widget.sizeHint().width(),
                self.calendar_button.sizeHint().width(),
            )
            for icon in (
                locktime_w.prefix_widget,
                threshold_w.prefix_widget,
                fees_w.prefix_widget,
                self.calendar_button,
            ):
                icon.setFixedWidth(icon_w)

            # WIDEN the fee field (user feedback: the spin-box up/down arrows
            # were covering the digits). ~8 chars leaves room for the value and
            # the arrows.
            fees_w.field_widget.setFixedWidth(8 * char_width_in_lineedit())

            # WHY THE TEXT WAS TRUNCATED (allegato16, fixed here):
            # A QLabel added to a box layout WITH an alignment flag (the old
            # ``alignment=Qt.AlignmentFlag.AlignLeft``) is NOT stretched to the
            # layout width - Qt gives it only its sizeHint. For a word-wrapped
            # label that sizeHint width is ambiguous/narrow, so the text wrapped
            # against an almost-minimum width and the reserved height was too
            # small, cutting the sentence in half. setMinimumWidth alone did not
            # help because the alignment flag still prevented horizontal stretch.
            #
            # TARGETED FIX:
            #   1. give the whole vertical WillSettingsWidget a sensible minimum
            #      width, so the box (and thus the labels) has real width to work
            #      with even before the parent dialog stretches it;
            #   2. add the two explanatory labels WITHOUT an alignment flag, so
            #      they expand to the full box width and word-wrap correctly;
            #   3. set an Expanding/Minimum size policy so the label takes the
            #      available width and computes its height from that width
            #      (heightForWidth), guaranteeing the full text is shown.
            hint_min_width = 44 * char_width_in_lineedit()
            self.setMinimumWidth(hint_min_width)

            # Explanatory hint ABOVE the date field (wizard only): tell the user
            # what the delivery date means. Wrapped so it fits the dialog width.
            # The explicit "\n" forces the line break exactly where the owner
            # asked (allegato2): after "(or backup)". With setWordWrap(True) the
            # QLabel honours the newline, so the sentence always shows on two
            # tidy lines instead of wrapping at an arbitrary point.
            date_hint = QLabel(
                _(
                    "Enter the date on which you want the inheritance (or "
                    "backup)\nof your Electrum wallet to take effect."
                )
            )
            date_hint.setWordWrap(True)
            date_hint.setMinimumWidth(hint_min_width)
            date_hint.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )
            # NOTE: added WITHOUT an alignment flag on purpose (see above), so
            # the label is stretched to the full width and wraps correctly.
            box.addWidget(date_hint)

            # Stack the composites one under the other, all left-aligned so the
            # fixed-width icons share a common left edge.
            box.addWidget(locktime_w, alignment=Qt.AlignmentFlag.AlignLeft)
            box.addWidget(threshold_w, alignment=Qt.AlignmentFlag.AlignLeft)
            # In BASIC mode the Check-Alive (threshold) row is hidden entirely
            # (it was already set invisible above); in ADVANCED mode it shows
            # with its icon aligned under the delivery-time icon.
            box.addWidget(
                self.calendar_button, alignment=Qt.AlignmentFlag.AlignLeft
            )
            box.addWidget(fees_w, alignment=Qt.AlignmentFlag.AlignLeft)

            # Cautionary note BELOW the miner-fee field (wizard only): warn the
            # user not to lower the miner fee unless they know what they do.
            # Explicit "\n" break after "miner fees" (allegato2), same rationale
            # as date_hint above.
            fee_note = QLabel(
                _(
                    "Please note: Do not reduce the miner fees\nunless you "
                    "know what you\u2019re doing"
                )
            )
            fee_note.setWordWrap(True)
            fee_note.setMinimumWidth(hint_min_width)
            fee_note.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )
            # Added WITHOUT an alignment flag (see date_hint above) so it
            # stretches to full width and wraps correctly instead of truncating.
            box.addWidget(fee_note)

            self.apply_editable_dates()
            return

        # Group C / C2: apply the current "Editable dates" setting to the date
        # fields. Done once at creation here, and re-applied later by
        # apply_editable_dates() whenever the setting changes (called from
        # BalWindow.update_all()), so toggling the checkbox takes effect
        # immediately, exactly like the "Hide Invalidated" filter does.
        self.apply_editable_dates()

    def apply_editable_dates(self):
        """Re-read the EDITABLE_DATES setting and lock/unlock the editable fields.

        Outside the "Build your will" wizard (``read_only=True``) the
        delivery-time, check-alive dates AND the mining fee are display-only by
        default. When the user ticks "Editable dates" in the settings, all three
        fields (locktime, threshold and fee) become editable here too; when it
        is unticked they all go back to read-only. The fee follows exactly the
        same rule as the dates (it used to stay always read-only, but the user
        asked for the fee to be editable together with the dates).

        This is safe to call repeatedly: it only adjusts the read-only state of
        the already-created sub-widgets, it does not rebuild anything. It is the
        per-update hook that lets the settings checkbox take effect without
        having to recreate the toolbar / re-open the window.
        """
        # Inside the wizard the dates are always editable; nothing to do.
        if not self.read_only:
            return

        basic = self.bal_window.bal_plugin.is_basic_mode()
        if not basic:
            # Advanced mode: fields are always editable.
            editable_dates = True
        else:
            try:
                editable_dates = self.bal_window.bal_plugin.EDITABLE_DATES.get()
            except Exception:
                # If the setting cannot be read, fall back to the safe default
                # (dates remain read-only outside the wizard).
                editable_dates = False

        self.widgets["locktime"].set_read_only(not editable_dates)
        self.widgets["threshold"].set_read_only(not editable_dates)
        # The fee now follows the very same "Editable dates" rule as the dates:
        # editable outside the wizard only when the setting is ticked.
        self.widgets["baltx_fees"].set_read_only(not editable_dates)

    def apply_user_type_visibility(self):
        """Re-apply the BASIC/ADVANCED visibility of the Check-Alive field.

        WHY this is needed (bug reported by the owner): the Check-Alive
        (threshold) field is hidden in BASIC mode and shown in ADVANCED mode.
        That visibility used to be decided ONLY in __init__. The toolbar
        settings widgets of the WILL and HEIR tabs are created once and then
        REUSED across the session (they are not rebuilt when the user switches
        USER TYPE), so after switching from BASIC to ADVANCED the Check-Alive
        field stayed hidden there. The wizard worked only because it is recreated
        every time it is opened.

        This method re-reads is_basic_mode() and shows/hides the Check-Alive
        field accordingly. It is called from BalWindow.update_all() (which the
        USER TYPE combo triggers when changed), exactly like apply_editable_dates
        is, so toggling BASIC/ADVANCED takes effect immediately on the already
        existing WILL/HEIR toolbars without restarting Electrum.

        It is safe to call repeatedly and on either layout (horizontal toolbar
        or vertical wizard): it only flips the visibility of the threshold
        widget.
        """
        try:
            basic = self.bal_window.bal_plugin.is_basic_mode()
        except Exception:
            # If the mode cannot be read, keep the field visible (the safe,
            # information-preserving default).
            basic = False
        threshold = self.widgets.get("threshold")
        if threshold is not None:
            # Hidden in BASIC, visible in ADVANCED - in BOTH layouts (main
            # window toolbar and wizard). Reverts the v0.5.2 experiment that
            # kept it visible in the main window in BASIC.
            threshold.setVisible(not basic)

        # Task #04: also re-apply the Raw/Date selector visibility on BOTH the
        # delivery-time (locktime) and the check-alive (threshold) boxes. The
        # combo was hidden once at construction (BASIC) and the reused WILL/HEIR
        # toolbars never re-showed it after switching to ADVANCED. Each composite
        # decides its own combo visibility from is_basic_mode() WITHOUT touching
        # its current value or editor.
        for field in ("locktime", "threshold"):
            widget = self.widgets.get(field)
            apply_combo = getattr(widget, "apply_user_type_visibility", None)
            if callable(apply_combo):
                apply_combo()

    def apply_user_type_editor_default(self):
        """Apply the per-mode Raw/Date editor default to both time fields.

        Called ONLY on a real BASIC<->ADVANCED USER TYPE change (not on every
        refresh/CHECK), so a manual Date/RAW choice is preserved when the user
        just presses CHECK. See BalTimeEditWidget.apply_user_type_editor_default.
        """
        for field in ("locktime", "threshold"):
            widget = self.widgets.get(field)
            apply_default = getattr(widget, "apply_user_type_editor_default", None)
            if callable(apply_default):
                apply_default()

    def open_or_save_calendar(self):
        """Build and save an .ics calendar file with SEPARATE reminder events.

        Group D / D1 (revised). Instead of a single calendar event holding
        internal VALARM reminders (which most calendars show as just one entry),
        this exports N *separate* VEVENTs, one per reminder date, so the user
        sees several distinct appointments in their calendar.

        The number of events is read from the NUM_REMINDERS setting (default 3,
        capped at 5 by the settings dialog). Their dates are computed by
        ``bal.core.reminders.build_ics_reminders`` (offsets spread uniformly
        across the check-alive period; the LAST event always falls one day
        before the delivery deadline / locktime). If the period is shorter than
        the requested number of reminders, at most one event per day is
        produced.

        Each event:
            * is placed on ``locktime - offset`` days (its own visible date);
            * carries a unique UID (``bal-<wallet>-<offset>d``) so calendars do
              not merge the events into one;
            * has its summary suffixed with " (reminder N/total)" to tell the
              events apart at a glance;
            * reuses the configured EVENT_DESCRIPTION (with the usual
              ``$wallet_name`` / ``$heirs_complete`` substitutions).

        The resulting file is written to a temp location and then copied to the
        path the user picks in the save dialog (default name "BAL_will_event.ics"
        on the Desktop).
        """
        # The .ics content (offsets, escaping, folding, VEVENT layout) is built
        # by the pure ``build_ics_reminders`` in bal.core.reminders. When no
        # reminder falls in the future it returns None (ToDo #2) and the user is
        # warned instead of getting an empty-looking file.
        ics_content = self._ics_provider()
        if not ics_content:
            self.bal_window.show_warning(
                _(
                    "No reminders were saved: the delivery date is too "
                    "close (or already passed)"
                )
            )
            return

        # Keep the generated .ics in a temp file; it is copied to the path the
        # user picks below.
        self.temp_path = write_temp_ics(ics_content)

        # Group D / D1b: always ask the user WHERE to save the .ics file (the
        # plugin no longer tries to open it with a calendar app). The save
        # dialog opens on the user's Desktop by default, with an .ics filter and
        # a sensible default filename.
        desktop = BalCalendar.desktop_dir()
        default_path = os.path.join(desktop, "BAL_will_event.ics")
        target = getSaveFileName(
            parent=self.bal_window.window,
            title=_("Save calendar reminder (.ics)"),
            # An absolute filename makes getSaveFileName ignore its remembered
            # IO_DIRECTORY and open on the Desktop instead.
            filename=default_path,
            filter="iCalendar (*.ics);;All files (*)",
            default_extension="ics",
            config=self.bal_window.window.config,
        )
        if not target:
            # User cancelled the save dialog: nothing to do.
            return
        try:
            self.save_ics_to(target)
        except Exception as save_err:
            _logger.error(f"saving .ics failed: {save_err}")
            self.bal_window.show_warning(
                _("Could not save the calendar file: {}").format(save_err)
            )
            return
        self.bal_window.show_message(
            _("Calendar file saved to:\n{}").format(target)
        )

    def save_ics_to(self, target):
        """Copy the generated .ics from the temp file to ``target`` (Group D / D1b).

        Overwrites the destination if it already exists. Used by
        open_or_save_calendar after the user picks a save location.

        Args:
            target: absolute destination path chosen by the user.

        Returns:
            The destination path.
        """
        _logger.debug(f"save_ics_to {self.temp_path} -> {target}")
        with open(self.temp_path, "rb") as src, open(target, "wb") as dst:
            dst.write(src.read())
        return target

    def _ics_provider(self):
        """Return the .ics content for the current locktime/threshold values.

        Used by :class:`BalCalendarButton` as its content provider. The whole
        document (reminder offsets, escaping, folding, VEVENT layout) is built
        by the pure :func:`bal.core.reminders.build_ics_reminders`.
        """
        try:
            locktime = self.widgets["locktime"].alarm

            basic_mode = self.bal_window.bal_plugin.is_basic_mode()
            if basic_mode:
                # BASIC mode: use factory defaults (the hidden settings are
                # ignored) and the fixed 30/10/1 offsets.
                raw_description = self.bal_window.bal_plugin.EVENT_DESCRIPTION.default
                raw_summary = self.bal_window.bal_plugin.EVENT_SUMMARY.default
                threshold = None
                num_reminders = 3
            else:
                raw_description = self.bal_window.bal_plugin.EVENT_DESCRIPTION.get()
                raw_summary = self.bal_window.bal_plugin.EVENT_SUMMARY.get()
                threshold = self.widgets["threshold"].alarm
                try:
                    num_reminders = int(
                        self.bal_window.bal_plugin.NUM_REMINDERS.get()
                    )
                except Exception:
                    num_reminders = 3

            heirs_details = "\r\n".join(
                f" {heir} - {self.bal_window.heirs[heir][0]}, "
                f"{self.bal_window.heirs[heir][1]}"
                for heir in self.bal_window.heirs
            )

            return build_ics_reminders(
                locktime=locktime,
                basic_mode=basic_mode,
                description=raw_description,
                summary=raw_summary,
                wallet_name=str(self.bal_window.wallet),
                heirs_details=heirs_details,
                version=self.bal_window.bal_plugin.version,
                num_reminders=num_reminders,
                threshold=threshold,
            )
        except Exception as e:
            _logger.error(f"failed to generate .ics: {e}")
            return None

    def on_locktime_change(self):
        locktime = self.widgets["locktime"].get_value()
        threshold = self.widgets["threshold"].get_value()
        locktime = BalTimestamp(locktime)
        threshold = BalTimestamp(threshold)

        min_locktime = min(
                Will.get_min_locktime(self.bal_window.willitems, NLOCKTIME_MAX),
                locktime.to_timestamp(),
        )
        # Resolved Check Alive date. For relative values this counts BACKWARDS
        # from the delivery time (reverse=True): "90d" means "90 days BEFORE the
        # delivery", not "90 days from now".
        td = threshold.to_date(min_locktime, True)
        self.widgets["threshold"].alarm=td
        self.bal_window.will_settings["real_threshold"]=td.timestamp()
        try:
            self.widgets["threshold"].editor.label.setText(td.strftime("%Y-%m-%d"))
        except Exception as _e:
            pass

        td = locktime.to_date()
        alarm = BalTimestamp(min_locktime).to_date()
        self.widgets["locktime"].alarm=alarm
        self.bal_window.will_settings["real_locktime"]=td.timestamp()
        try:
            self.widgets["locktime"].editor.label.setText(td.strftime("%Y-%m-%d"))
        except Exception as _e:
            pass




class PercAmountEdit(BTCAmountEdit):
    def __init__(self, decimal_point, is_int=False, parent=None, *, max_amount=None):
        super().__init__(decimal_point, is_int, parent, max_amount=max_amount)

    def numbify(self):
        # The text sanitisation lives in bal.core.input_rules.
        text = self.text().strip()
        if text == "!":
            self.shortcut.emit()
            return
        pos = self.cursorPosition()
        s, self.is_perc = normalize_perc_amount_text(text, DECIMAL_POINT)

        self.setText(s)
        self.setModified(self.hasFocus())
        self.setCursorPosition(pos)

    def _get_amount_from_text(self, text: str) -> Union[None, Decimal, int]:
        return parse_perc_amount(text, DECIMAL_POINT)

    def _get_text_from_amount(self, amount):
        out = super()._get_text_from_amount(amount)
        if self.is_perc:
            out += "%"
        return out

    def paintEvent(self, event):
        QLineEdit.paintEvent(self, event)
        if self.base_unit:
            panel = QStyleOptionFrame()
            self.initStyleOption(panel)
            text_rect = self.style().subElementRect(
                QStyle.SubElement.SE_LineEditContents, panel, self
            )
            text_rect.adjust(2, 0, -10, 0)
            painter = QPainter(self)
            painter.setPen(ColorScheme.GRAY.as_color())
            if len(self.text()) == 0:
                painter.drawText(
                    text_rect,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    self.base_unit() + " or perc value",
                )



class BalLineEdit(QLineEdit):
    def __init__(self,variable):
        QLineEdit.__init__(self)
        self.setText(variable.get())
        def on_edit():
            variable.set(self.text())
        self.editingFinished.connect(on_edit)


class BalTextEdit(QTextEdit):
    def __init__(self,variable):
        QTextEdit.__init__(self)
        self.setPlainText(variable.get())
        def on_edit():
            variable.set(self.toPlainText())
        self.textChanged.connect(on_edit)


class BalCheckBox(QCheckBox):
    def __init__(self, variable, on_click=None):
        QCheckBox.__init__(self)
        self.setChecked(variable.get())
        self.on_click = on_click

        def on_check(v):
            variable.set(v == 2)
            #variable.get()
            if self.on_click:
                self.on_click()

        self.stateChanged.connect(on_check)


class BalSpinBox(QSpinBox):
    """Integer spin box bound to a BalConfig value (Group D / D1).

    Mirrors BalCheckBox / BalLineEdit: it shows the persisted value on creation
    and writes the new value back to the config whenever the user changes it.

    Args:
        variable: the BalConfig accessor to read from / write to.
        minimum: smallest selectable value (default 1).
        maximum: largest selectable value (default 5, used by "Number of
            reminders").
        on_change: optional callback invoked after the value is persisted.
    """

    def __init__(self, variable, minimum=1, maximum=5, on_change=None):
        QSpinBox.__init__(self)
        self.setMinimum(minimum)
        self.setMaximum(maximum)
        # Coerce the stored value to int and clamp it into the allowed range,
        # so a stale/invalid config value can never push the spin box out of
        # bounds.
        try:
            current = int(variable.get())
        except Exception:
            current = minimum
        current = max(minimum, min(maximum, current))
        self.setValue(current)
        self.on_change = on_change

        def on_value_changed(v):
            variable.set(int(v))
            if self.on_change:
                self.on_change()

        self.valueChanged.connect(on_value_changed)



class WillWidget(QWidget):
    def __init__(self, father=None, parent=None, will=None):
        super().__init__()
        vlayout = QVBoxLayout()
        self.setLayout(vlayout)
        self.will = will if will is not None else parent.bal_window.willitems
        self._bal_parent = parent
        for w in self.will:
            if (
                self.will[w].get_status("REPLACED")
                and self._bal_parent.bal_window.bal_plugin._hide_replaced
            ):
                continue
            if (
                self.will[w].get_status("INVALIDATED")
                and self._bal_parent.bal_window.bal_plugin._hide_invalidated
            ):
                continue
            f = self.will[w].father
            if father == f:
                qwidget = QWidget()
                # childWidget = QWidget()
                hlayout = QHBoxLayout(qwidget)
                qwidget.setLayout(hlayout)
                vlayout.addWidget(qwidget)
                detailw = QWidget()
                detaillayout = QVBoxLayout()
                detailw.setLayout(detaillayout)

                willpushbutton = QPushButton(w)

                willpushbutton.clicked.connect(
                    partial(
                        self._bal_parent.bal_window.show_transaction,
                        tx=self.will[w].tx,
                    )
                )
                detaillayout.addWidget(willpushbutton)
                locktime = str(BalTimestamp(self.will[w].tx.locktime))
                creation = str(BalTimestamp(self.will[w].time))

                def qlabel(title, value):
                    label = "<b>" + _(str(title)) + f":</b>\t{str(value)}"
                    return QLabel(label)

                detaillayout.addWidget(qlabel("Locktime", locktime))
                detaillayout.addWidget(qlabel("Creation Time", creation))
                try:
                    total_fees = (
                        self.will[w].tx.input_value() - self.will[w].tx.output_value()
                    )
                except Exception:
                    total_fees = -1
                decoded_fees = total_fees
                fee_per_byte = round(total_fees / self.will[w].tx.estimated_size(), 3)
                fees_str = str(decoded_fees) + " (" + str(fee_per_byte) + " sats/vbyte)"
                detaillayout.addWidget(qlabel("Transaction fees:", fees_str))
                detaillayout.addWidget(
                    qlabel("Status:", self.will[w].status + signature_suffix(self.will[w]))
                )
                detaillayout.addWidget(QLabel(""))
                detaillayout.addWidget(QLabel("<b>Heirs:</b>"))
                for heir_name in self.will[w].heirs:
                    if 'w!ll3x3c"' in heir_name:
                        continue
                    h = self.will[w].heirs[heir_name]
                    decoded_amount = Util.decode_amount(
                        h[3], self._bal_parent.decimal_point
                    )
                    if is_op_return_address(h[0]):
                        data_hex = get_op_return_hex(h[0]) or ""
                        try:
                            decoded = bytes.fromhex(data_hex).decode(
                                "utf-8", errors="replace"
                            )
                        except Exception:
                            decoded = h[0]
                        detaillayout.addWidget(qlabel(heir_name, "OP_RETURN: " + decoded))
                    else:
                        detaillayout.addWidget(
                            qlabel(
                                heir_name,
                                f"{decoded_amount} {self._bal_parent.base_unit_name} "
                                f"[{h[0]}]",
                            )
                        )
                if self.will[w].we:
                    detaillayout.addWidget(QLabel(""))
                    detaillayout.addWidget(QLabel(_("<b>Willexecutor:</b:")))
                    decoded_amount = Util.decode_amount(
                        self.will[w].we["base_fee"], self._bal_parent.decimal_point
                    )

                    detaillayout.addWidget(
                        qlabel(
                            self.will[w].we["url"],
                            f"{decoded_amount} {self._bal_parent.base_unit_name}",
                        )
                    )
                    if self.will[w].we.get("address"):
                        detaillayout.addWidget(
                            qlabel(_("Address"), self.will[w].we["address"])
                        )
                detaillayout.addStretch()
                pal = QPalette()
                pal.setColor(
                    QPalette.ColorRole.Window, QColor(status_color(self.will[w]))
                )
                detailw.setAutoFillBackground(True)
                detailw.setPalette(pal)

                hlayout.addWidget(detailw)
                hlayout.addWidget(WillWidget(w, parent=parent, will=self.will))


