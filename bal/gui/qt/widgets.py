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
    * WillSummaryWidget                                     - will summary box (#84a)
    * BuildReportView                                       - Building Will report (#84b)
"""

import html
from datetime import timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ...core.checkalive import (
    CHECK_ALIVE_DEFAULT_DAYS,
    CHECK_ALIVE_MAX_DAYS,
    check_alive_days,
    normalize_check_alive,
    resolve_relative_check_alive_date,
)
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
    N_,
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
    QFrame,
    QGridLayout,
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
    format_status_history,
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
    """The delivery date field: a Raw period ("1y") or a Date (CHANGELOG #81).

    Owner rule (#81): the saved delivery date changes ONLY when the user
    edits it - types a period, picks a date, or explicitly switches Raw/Date.
    Building the widget, switching BASIC/ADVANCED, refreshing or syncing
    another panel never change it. Before #81 each of those replaced it with
    a default ("1y" in ADVANCED, today + 1 year in BASIC): switching mode
    silently moved the owner's delivery date.

    * The editor follows the FORM of the saved value: a period opens the Raw
      editor, a fixed date the Date editor (BASIC always shows the Date
      editor; a period is then shown as its real date, nothing is saved).
    * An explicit Raw/Date switch keeps the same delivery written the other
      way: Raw -> Date shows the date the will really uses
      (``Will.planned_delivery``), Date -> Raw the days from today to it.
    * ``save_to_settings=False`` (the heir dialog) never writes the will's
      delivery setting; the dialog reads ``get_value()`` itself.
    """

    valueEdited = pyqtSignal()
    _setting_locktime = False
    current_value = None
    current_index = None
    default_value = None

    help_text = N_(
        "if you choose Raw, you can insert various options based on suffix:\n"
        " - d: number of days after current day(ex: 1d means tomorrow)\n"
        " - y: number of years after currrent day(ex: 1y means one year from today)\n"
    )
    label_text = None
    tooltip_text = None
    base_field = None
    # The Raw editor never accepts a sign for the delivery (the Check Alive,
    # which used one, has its own spin box since CHANGELOG #80).
    allow_negative_raw = False

    def __init__(self, bal_window, parent, default_locktime=None, save_to_settings=True):
        super().__init__(parent)
        self.bal_window = bal_window
        self.save_to_settings = save_to_settings
        # Nothing is saved while the widget is being built (#81), and a
        # programmatic editor switch only changes how the value is shown.
        self._quiet = True
        self._programmatic = False

        hbox = QHBoxLayout()
        self.setLayout(hbox)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        self.setMinimumWidth(40 * char_width_in_lineedit())
        self.locktime_raw_e = TimeRawEditWidget(
            self, time_edit=self, allow_negative=self.allow_negative_raw
        )
        self.locktime_date_e = LockTimeDateEdit(self, time_edit=self)
        self.editors = [self.locktime_raw_e, self.locktime_date_e]
        self.combo = QComboBox()
        options = [_("Raw"), _("Date")]
        self.option_index_to_editor_map = {
            0: self.locktime_raw_e,
            1: self.locktime_date_e,
        }
        self.combo.addItems(options)
        # BASIC mode: the Raw/Date selector is hidden and the Date editor is
        # always used (owner request).
        self._basic_mode = self.bal_window.bal_plugin.is_basic_mode()
        # True once the user picked an editor by hand (combo ``activated``):
        # a later BASIC<->ADVANCED switch then keeps that choice.
        self._user_picked_editor = False
        if not default_locktime:
            default_locktime = self.bal_window.bal_plugin.WILL_SETTINGS.get()[self.base_field]
        # #81: the editor follows the form of the saved value; the value is
        # never replaced by a default (that is what moved the delivery date).
        default_index = self._index_for(default_locktime)
        # help_text and tooltip_text are class attributes marked with N_():
        # translate them here, when shown.
        help_button=HelpButton(_(self.help_text))
        help_button.setText(self.label_text)
        # Show a short label (e.g. "Delivery time") when the user hovers the
        # icon, so the emoji button is self-explanatory.
        if self.tooltip_text:
            help_button.setToolTip(_(self.tooltip_text))
        hbox.addWidget(help_button)
        # Expose the leading icon (prefix) so the parent WillSettingsWidget can
        # align all rows on a common left edge (see its vertical layout).
        self.prefix_widget = help_button
        self.combo.currentIndexChanged.connect(self.on_current_index_changed)
        # `activated` fires ONLY on real user interaction with the selector.
        self.combo.activated.connect(self._on_user_picked_editor)

        for w in self.editors:
            w.setVisible(False)
            w.setEnabled(False)

        self.current_value = default_locktime
        self.editor = self.option_index_to_editor_map[default_index]
        self.editor.setVisible(True)
        self.editor.setEnabled(True)
        self.set_index(default_index)
        self.current_index = default_index
        self._show(default_locktime)
        hbox.addWidget(self.combo)
        if self._basic_mode:
            self.combo.setVisible(False)
        for w in self.editors:
            hbox.addWidget(w)

        hbox.addStretch(1)
        self.valueEdited.connect(lambda: self.update_will_settings(True))
        self.locktime_raw_e.editingFinished.connect(self.valueEdited.emit)
        self.locktime_date_e.dateTimeChanged.connect(self.valueEdited.emit)
        self._quiet = False

    # ---------------------------------------------------------------- helpers

    def _index_for(self, value):
        """Editor for ``value``: Date (1) in BASIC or for a fixed date, else Raw (0)."""
        if self.bal_window.bal_plugin.is_basic_mode():
            return 1
        return 0 if Util._relative_days(value) is not None else 1

    def _planned_timestamp(self, value):
        """The delivery date ``value`` really gives (see Will.planned_delivery)."""
        try:
            return int(Will.planned_delivery(
                getattr(self.bal_window, "willitems", None) or {}, value
            )[0])
        except Exception:
            try:
                return int(BalTimestamp(value).to_timestamp())
            except Exception:
                return None

    def _value_for_editor(self, index, value):
        """The same delivery written for editor ``index`` (0 Raw, 1 Date)."""
        relative = Util._relative_days(value) is not None
        if index == 1:
            return value if not relative else self._planned_timestamp(value)
        if relative:
            return value
        ts = self._planned_timestamp(value)
        if ts is None:
            return value
        today = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        return "{}d".format(max(1, round((ts - today) / 86400)))

    def _show(self, value):
        """Show ``value`` in the active editor without emitting an edit."""
        shown = self._value_for_editor(self.current_index, value)
        if shown is None:
            return
        self.editor.blockSignals(True)
        try:
            self.editor.set_value(shown)
        finally:
            self.editor.blockSignals(False)

    # ------------------------------------------------------------------ edits

    def update_will_settings(
        self,
        update_all=False,
        update_will_dialog=False,
        update_heirs_dialog=False,
    ):
        """A user edit: remember it and (unless disabled) save it."""
        if self._quiet:
            return
        self.current_value = self.get_value()
        if not self.save_to_settings:
            return
        self.bal_window.update_setting_widgets(
            self.current_value,
            self.base_field,
            update_all,
            update_will_dialog,
            update_heirs_dialog,
        )

    def _on_user_picked_editor(self, i):
        """Record that the user chose the Raw/Date editor by hand.

        Connected to the combo's ``activated`` signal, which only fires on real
        user interaction (not on programmatic ``setCurrentIndex``). Used by the
        runtime BASIC<->ADVANCED switch to respect a manual choice.
        """
        self._user_picked_editor = True

    def on_current_index_changed(self, i):
        """Switch editor, keeping the same delivery (#81).

        A programmatic switch (``set_index``: mode change, sync) only shows
        the saved value in the other editor. A switch made by the user saves
        the same delivery written the other way; before #81 both put a
        default value in the field and saved it.
        """
        self.current_index = i
        for w in self.editors:
            w.setVisible(False)
            w.setEnabled(False)
        self.editor = self.option_index_to_editor_map[i]
        self._show(self.current_value)
        self.editor.setVisible(True)
        self.editor.setEnabled(True)
        if self._programmatic or self._quiet:
            return
        self.valueEdited.emit()
        self.bal_window.update_combo_setting_widgets(i, self.base_field, True)

    def get_value(self) -> Optional[str]:
        val = self.editor.get_value()
        return val

    def set_index(self, index):
        """Programmatic editor switch: changes the display only (#81)."""
        if self.current_index != index:
            self._programmatic = True
            try:
                self.combo.setCurrentIndex(index)
            finally:
                self._programmatic = False

    def apply_user_type_visibility(self):
        """Re-apply the BASIC/ADVANCED visibility of the Raw/Date selector.

        WHY this is needed (bug reported by the owner, task #04): the WILL and
        HEIR tab toolbars are created once and then REUSED for the whole
        session, so after switching from BASIC to ADVANCED the combo stayed
        hidden there. This method re-reads ``is_basic_mode()`` and shows/hides
        ONLY the combo; it never changes the value or the active editor (it
        runs on every ``update_all()``/CHECK refresh). The per-mode editor
        choice lives in ``apply_user_type_editor_default()``, which runs ONLY
        on a real USER TYPE change.
        """
        try:
            basic = self.bal_window.bal_plugin.is_basic_mode()
        except Exception:
            # If the mode cannot be read, show the combo (the safe, most
            # capable default for an existing widget).
            basic = False
        self.combo.setVisible(not basic)

    def apply_user_type_editor_default(self):
        """Pick the editor for the new USER TYPE, never changing the value.

        Runs ONLY on a real BASIC<->ADVANCED change:
          * BASIC    -> the Date editor (the selector is hidden);
          * ADVANCED -> the editor that matches the saved value (Raw for a
                       period, Date for a fixed date), unless the user picked
                       one by hand (``_user_picked_editor``).
        The switch is programmatic: only the display changes (#81; it used to
        save "1y" or today + 1 year and so move the delivery date).
        """
        try:
            basic = self.bal_window.bal_plugin.is_basic_mode()
        except Exception:
            basic = False
        try:
            if basic:
                self.set_index(1)
            elif not self._user_picked_editor:
                self.set_index(self._index_for(self.current_value))
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
        """Show a value saved elsewhere (sync between panels); never saves.

        Before #81 this saved the value again, which made building a
        widget (e.g. the heir dialog with that heir's own date) overwrite the
        will's delivery setting.
        """
        if not x:
            return
        self.current_value = x
        if self._value_for_editor(self.current_index, x) != self.get_value():
            self._show(x)

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

    def __init__(self, parent, time_edit=None, allow_negative=False):
        super().__init__(parent)
        self.editor = LockTimeRawEdit(parent, time_edit, allow_negative=allow_negative)
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
    def __init__(self, parent=None, time_edit=None, allow_negative=False):
        QLineEdit.__init__(self, parent)
        # Group C / C3: narrow the RAW input to roughly a third of its former
        # width. The accepted values are short relative durations such as "30d"
        # or "1y", so a handful of characters is plenty while still leaving room
        # for larger day counts (e.g. "3650d").
        self.setFixedWidth(6 * char_width_in_lineedit())
        self.textChanged.connect(self.numbify)
        self.isdays = False
        self.isyears = False
        # The Check Alive field accepts a leading "-" (the sign selects the
        # anchor date); the delivery field does not.
        self.allow_negative = allow_negative
        self.time_edit = time_edit

    def numbify(self):
        # Only digits plus the day ("d") and year ("y") suffixes are accepted
        # (and, for the Check Alive field, a single leading "-"). The
        # block-height suffix ("b") was removed (A1): locktimes are always UNIX
        # timestamps now, so block-relative input is no longer allowed.
        # The sanitisation itself lives in bal.core.input_rules.
        text = self.text().strip()
        pos = self.cursorPosition()
        s, isdays, isyears, pos = normalize_locktime_raw_text(
            text, pos, allow_negative=self.allow_negative
        )
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



class ThresholdTimeWidget(QWidget):
    """The Check Alive field: "N days before the delivery" (CHANGELOG #80).

    Owner decisions (D14): a single spin box in days (arrows step 30 days,
    1..3650, default 180), the word "days" instead of "d", the minus sign
    added by the plugin, an approximate number of months and the resulting
    date next to it (red when already past). The Raw/Date selector and the
    fixed-date / "after creation" forms are gone: they did not follow the
    delivery when it moved (scenario C3adv).

    The value exchanged with the rest of the plugin is unchanged in form:
    ``get_value()`` returns ``"-Nd"`` (what ``bal.core.checkalive`` and BAL
    understand), and the interface of the old composite is kept
    (``valueEdited``, ``prefix_widget``, ``editor.label``, ``alarm``,
    ``set_value``, ``set_read_only``, ``set_index``...), so the toolbars,
    the wizard and the calendar keep working untouched.
    """

    valueEdited = pyqtSignal()
    # rich_text=True is used by the HelpButton, so HTML tags (<b>, <br>) render.
    help_text = N_(
        "<b>CHECK ALIVE</b><br><br>"
        "How many days before the delivery date the plugin asks if you are "
        "still here.<br><br>"
        "From that day on, when you open the wallet or press Check, the "
        "plugin asks you to <b>renew</b> the will: the old will is "
        "invalidated (one miner fee) and a new one is prepared with a later "
        "delivery date.<br>"
        "If you do not renew, nothing changes: at the delivery date the "
        "transactions are delivered to your heirs.<br><br>"
        "Default: 180 days (about six months). The date next to the field is "
        "the resulting Check Alive date; it turns red when it is already "
        "past.<br>"
    )
    label_text = "🚨"
    tooltip_text = N_("Check Alive: days before the delivery date")
    base_field = "threshold"

    def __init__(self, bal_window, parent, init_value=None):
        """Build the spin box; ``init_value`` is any saved Check Alive value."""
        super().__init__(parent)
        self.bal_window = bal_window
        self.alarm = None
        self.current_value = None
        hbox = QHBoxLayout()
        self.setLayout(hbox)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        help_button = HelpButton(_(self.help_text))
        help_button.setText(self.label_text)
        help_button.setToolTip(_(self.tooltip_text))
        hbox.addWidget(help_button)
        self.prefix_widget = help_button
        self.spin = QSpinBox()
        self.spin.setRange(1, CHECK_ALIVE_MAX_DAYS)
        self.spin.setSingleStep(30)
        self.spin.setSuffix(" " + _("days"))
        # Only an arrow click, Enter or leaving the field changes the value:
        # typing "180" must not save 1, 18 and 180 in turn.
        self.spin.setKeyboardTracking(False)
        self.spin.setToolTip(_(self.tooltip_text))
        hbox.addWidget(self.spin)
        self.hint = QLabel("")
        hbox.addWidget(self.hint)
        # Kept under the old name: on_locktime_change used ``editor.label``.
        self.label = QLabel("")
        self.editor = self
        hbox.addWidget(self.label)
        hbox.addStretch(1)
        if init_value is None:
            init_value = bal_window.bal_plugin.WILL_SETTINGS.get().get("threshold")
        self.spin.setValue(self._days_for_display(init_value))
        self.current_value = self.get_value()
        self._update_hint()
        self.spin.valueChanged.connect(self._on_spin_changed)
        self.valueEdited.connect(self.update_will_settings)

    def _days_for_display(self, value):
        """Days to show for any saved value (old forms converted, #80).

        Display only: the conversion is SAVED by
        ``BalWindow._normalize_check_alive`` once the will is loaded, against
        the delivery the will really uses.
        """
        days = check_alive_days(value)
        if days is not None:
            return days
        try:
            settings = dict(getattr(self.bal_window, "will_settings", None) or {})
            settings["threshold"] = value
            normalized = normalize_check_alive(
                settings,
                built_locktime=Will.get_min_locktime(
                    getattr(self.bal_window, "willitems", None) or {}
                ),
            )
            return check_alive_days(normalized) or CHECK_ALIVE_DEFAULT_DAYS
        except Exception:
            return CHECK_ALIVE_DEFAULT_DAYS

    def _update_hint(self):
        """Show "before the delivery (≈ 6 months) →" next to the spin box."""
        days = self.spin.value()
        months = round(days / 30.44)
        if months >= 2:
            extra = " " + _("(≈ {} months)").format(months)
        elif months == 1:
            extra = " " + _("(≈ 1 month)")
        else:
            extra = ""
        self.hint.setText(_("before the delivery") + extra + " →")

    def _on_spin_changed(self, _days):
        self._update_hint()
        self.valueEdited.emit()

    def update_will_settings(self):
        """Save the value and sync the other settings widgets."""
        self.current_value = self.get_value()
        self.bal_window.update_setting_widgets(
            self.current_value, self.base_field, True
        )

    def get_value(self) -> str:
        """The Check Alive as saved: ``"-Nd"``."""
        return "-{}d".format(self.spin.value())

    def set_value(self, x: Any, force=None, update_all=False,
                  update_will_dialog=False, update_heirs_dialog=False) -> None:
        """Show ``x`` (any saved form); save it only when it is "-Nd"."""
        days = self._days_for_display(x)
        if days == self.spin.value():
            return
        self.spin.blockSignals(True)
        self.spin.setValue(days)
        self.spin.blockSignals(False)
        self._update_hint()
        self.current_value = self.get_value()
        if check_alive_days(x) is not None:
            self.bal_window.update_setting_widgets(self.current_value, self.base_field)

    def show_resolved_date(self, td, now=None):
        """Show the resulting Check Alive date, red when already past."""
        self.alarm = td
        self.label.setText(td.strftime("%Y-%m-%d"))
        current = now if now is not None else datetime.now(tz=td.tzinfo).timestamp()
        past = td.timestamp() < current
        self.label.setStyleSheet("color:#ff0000;" if past else "")
        self.label.setToolTip(
            _("This Check Alive date has already passed") if past else ""
        )

    def set_read_only(self, read_only=True):
        """Show the value but make it non-editable (outside the wizard)."""
        self.spin.setReadOnly(read_only)
        self.spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
            if read_only
            else QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        self.spin.setStyleSheet(
            "QSpinBox{background-color:#f0f0f0;}" if read_only else ""
        )

    def set_index(self, index):
        """No Raw/Date selector any more (kept for the shared callers)."""

    def apply_user_type_visibility(self):
        """No Raw/Date selector any more (kept for the shared callers)."""

    def apply_user_type_editor_default(self):
        """No Raw/Date selector any more (kept for the shared callers)."""



class LockTimeWidget(BalTimeEditWidget):
    # rich_text=True is used by the HelpButton, so HTML tags (<b>, <br>) render.
    help_text = N_(
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
    tooltip_text = N_("Delivery Time, click for more information")
    base_field = "locktime"

    def __init__(self, bal_window, parent, init_value=None, save_to_settings=True):
        """``save_to_settings=False``: an heir's own date (heir dialog), never
        written to the will's delivery setting (#81)."""
        if init_value is None:
            init_value = bal_window.bal_plugin.WILL_SETTINGS.get()["locktime"]
        super().__init__(bal_window, parent, init_value, save_to_settings)
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
                # ignored) and the fixed 30/10/1 offsets. The defaults are in
                # the GUI language (see BalConfig).
                raw_description = (
                    self.bal_window.bal_plugin.EVENT_DESCRIPTION.localized_default()
                )
                raw_summary = self.bal_window.bal_plugin.EVENT_SUMMARY.localized_default()
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
                reminder_suffix=_("(reminder {idx}/{total})"),
            )
        except Exception as e:
            _logger.error(f"failed to generate .ics: {e}")
            return None

    def on_locktime_change(self):
        locktime = self.widgets["locktime"].get_value()
        threshold_raw = self.widgets["threshold"].get_value()
        # CHANGELOG #79: the REAL delivery date. With a will in force a
        # relative setting ("4y") stays anchored to the date frozen in the
        # signed tx; "today + 4y", shown before, moved every day while the
        # will kept its own date.
        try:
            planned, in_force = Will.planned_delivery(
                self.bal_window.willitems, locktime
            )
        except Exception:
            planned, in_force = BalTimestamp(locktime).to_timestamp(), False
        locktime = BalTimestamp(locktime)

        min_locktime = min(
                Will.get_min_locktime(self.bal_window.willitems, NLOCKTIME_MAX),
                planned,
        )
        # Resolved Check Alive date. The SIGN of the RAW value selects the
        # anchor: negative counts backwards from the delivery, positive counts
        # forward from the inheritance creation date. The rule lives in
        # ``bal.core.checkalive`` so this display matches every validity check.
        resolved = resolve_relative_check_alive_date(
            threshold_raw,
            delivery_date=min_locktime,
            creation_date=Will.get_creation_time(self.bal_window.willitems),
        )
        td = BalTimestamp._safe_fromtimestamp(resolved)
        self.widgets["threshold"].alarm=td
        self.bal_window.will_settings["real_threshold"]=td.timestamp()
        try:
            # Red when already past (CHANGELOG #80).
            self.widgets["threshold"].show_resolved_date(td)
        except Exception as _e:
            pass

        td = BalTimestamp._safe_fromtimestamp(planned)
        alarm = BalTimestamp(min_locktime).to_date()
        self.widgets["locktime"].alarm=alarm
        self.bal_window.will_settings["real_locktime"]=td.timestamp()
        try:
            label = self.widgets["locktime"].editor.label
            label.setText(td.strftime("%Y-%m-%d"))
            label.setToolTip(
                _("Delivery date of the will in force")
                if in_force
                else _("Delivery date of the will that will be prepared")
            )
        except Exception as _e:
            pass
        # CHANGELOG #84a: a new Check Alive value, or the heirs list redrawn
        # (it calls this too), changes the will summary of the Will tab.
        try:
            self.bal_window.update_will_summary()
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
                    _("{} or perc value").format(self.base_unit()),
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
                    # ``title`` is shown as it is: callers pass a translated
                    # label, or data (an heir name, a will-executor URL).
                    return QLabel("<b>{}:</b>\t{}".format(title, value))

                detaillayout.addWidget(qlabel(_("Locktime"), locktime))
                detaillayout.addWidget(qlabel(_("Creation Time"), creation))
                try:
                    total_fees = (
                        self.will[w].tx.input_value() - self.will[w].tx.output_value()
                    )
                except Exception:
                    total_fees = -1
                decoded_fees = total_fees
                fee_per_byte = round(total_fees / self.will[w].tx.estimated_size(), 3)
                fees_str = str(decoded_fees) + " (" + str(fee_per_byte) + " sats/vbyte)"
                detaillayout.addWidget(qlabel(_("Transaction fees"), fees_str))
                # The saved status history is English: translate it for display.
                detaillayout.addWidget(
                    qlabel(
                        _("Status"),
                        format_status_history(self.will[w].status)
                        + signature_suffix(self.will[w]),
                    )
                )
                detaillayout.addWidget(QLabel(""))
                detaillayout.addWidget(QLabel("<b>{}</b>".format(_("Heirs:"))))
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
                    detaillayout.addWidget(
                        QLabel("<b>{}</b>".format(_("Willexecutor:")))
                    )
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


class WillSummaryWidget(QFrame):
    """The will summary box at the top of the Will tab (CHANGELOG #84a).

    Draws what :func:`bal.core.summary.will_summary` computed: an icon and a
    title in the colour of the state (green: in force; orange: something to
    do; red: no will in force or delivery date passed; grey: executed, or
    an operation in progress), the
    label/value lines, an optional note and at most ONE button. The button
    only calls ``on_action(action)``: the window maps it to the existing
    commands (Check, Renew, the wizard), so the box adds no new logic.
    Hidden when there is nothing to show.
    """

    # kind -> (text and border colour, background, icon). Explicit colours on
    # both text and background, so the box stays readable with Electrum's
    # dark theme too.
    _STYLES = {
        "ok": ("#2e7d32", "#eef7ee", "✔"),
        "warn": ("#b26a00", "#fff5e5", "⚠"),
        "bad": ("#c62828", "#fdecec", "✖"),
        "info": ("#555555", "#f2f2f2", "ℹ"),
        "busy": ("#555555", "#f2f2f2", "⏳"),
    }
    _RED = "#c62828"

    def __init__(self, on_action, parent=None):
        super().__init__(parent)
        self.on_action = on_action
        self.action = None
        # The style sheet targets this object name only, so the colours do
        # not leak into the child labels and button.
        self.setObjectName("balWillSummary")
        box = QHBoxLayout(self)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(14)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        box.addWidget(self.icon)
        column = QVBoxLayout()
        column.setSpacing(6)
        self.title = QLabel()
        self.title.setWordWrap(True)
        column.addWidget(self.title)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(3)
        self.grid.setColumnStretch(1, 1)
        column.addLayout(self.grid)
        self.note = QLabel()
        self.note.setWordWrap(True)
        column.addWidget(self.note)
        box.addLayout(column, 1)
        self.button = QPushButton()
        self.button.setMinimumHeight(32)
        self.button.setMinimumWidth(170)
        self.button.clicked.connect(self._on_button)
        box.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.setVisible(False)

    def _on_button(self):
        """Run the action of the state shown (see ``BalWindow.on_will_summary_action``)."""
        if self.action and self.on_action:
            self.on_action(self.action)

    def _clear_rows(self):
        """Remove the label/value lines of the previous summary.

        Each line is hidden and detached at once, not only scheduled for
        deletion: ``deleteLater`` waits for the event loop that called it,
        and a working dialog's own loop (exec) runs meanwhile, so the old
        line stayed painted in the grey "operation in progress" box
        (owner's screenshots, CHANGELOG #87).
        """
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _value_html(self, row):
        """The value of one line as rich text (bold date, red alarm).

        Every piece is escaped: the texts come from the catalogs and the
        dates from the will, never from the user, but escaping keeps a
        stray "<" from breaking the line.
        """
        parts = []
        if row.get("strong"):
            parts.append("<b>{}</b>".format(html.escape(row["strong"])))
        if row.get("text"):
            parts.append(html.escape(row["text"]))
        value = "&nbsp;".join(parts)
        if row.get("alarm"):
            alarm = '<span style="color:{};">{}</span>'.format(
                self._RED, html.escape(row["alarm"])
            )
            value = value + " &middot; " + alarm if value else alarm
        if row.get("red"):
            value = '<span style="color:{};">{}</span>'.format(self._RED, value)
        return value

    def show_summary(self, summary):
        """Show ``summary`` (a :func:`will_summary` dict), or hide the box for None."""
        if not summary:
            self.action = None
            self.setVisible(False)
            return
        fg, bg, icon = self._STYLES.get(summary.get("kind"), self._STYLES["info"])
        self.setStyleSheet(
            "#balWillSummary{{background:{bg};border:1px solid {fg};"
            "border-left:6px solid {fg};border-radius:6px;}}"
            "#balWillSummary QLabel{{background:transparent;color:#111111;}}".format(
                bg=bg, fg=fg
            )
        )
        self.icon.setText(icon)
        self.icon.setStyleSheet("color:{};font-size:26px;".format(fg))
        self.title.setText(summary.get("title") or "")
        self.title.setStyleSheet(
            "color:{};font-size:15px;font-weight:bold;".format(fg)
        )
        self._clear_rows()
        for i, row in enumerate(summary.get("rows") or []):
            label = QLabel(row.get("label", "") + ":")
            label.setStyleSheet("color:#555555;")
            value = QLabel(self._value_html(row))
            value.setTextFormat(Qt.TextFormat.RichText)
            value.setWordWrap(True)
            self.grid.addWidget(label, i, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(value, i, 1)
        note = summary.get("note")
        self.note.setText(note or "")
        self.note.setStyleSheet("color:#333333;font-style:italic;")
        self.note.setVisible(bool(note))
        self.action = summary.get("action")
        if self.action:
            self.button.setText(summary.get("action_text") or "")
            self.button.setStyleSheet(
                "QPushButton{{font-weight:bold;background:{};color:white;"
                "border-radius:4px;padding:4px 12px;}}".format(fg)
            )
        self.button.setVisible(bool(self.action))
        self.setVisible(True)


class BuildReportView(QWidget):
    """The top part of the Building Will dialog (CHANGELOG #84b).

    Draws a :meth:`bal.core.build_report.BuildReport.snapshot`: the bar of
    the five steps, the result sentence and the boxes (what changed,
    warnings, problems, heirs, will-executors, the will in force). Mockup
    approved by the owner. Explicit colours on text and background, like
    the will summary of the Will tab, so it stays readable with the dark
    theme. ``format_amount(satoshi)`` formats the heirs' amounts in the
    wallet's unit.
    """

    # state -> (symbol, text and border colour, background)
    _STEP_STYLE = {
        "todo": ("–", "#8a8a8a", "#f1f1f1"),
        "skip": ("–", "#8a8a8a", "#f1f1f1"),
        "run": ("●", "#1565c0", "#e3f2fd"),
        "ok": ("✔", "#2e7d32", "#e8f5e9"),
        "warn": ("⚠", "#b26a00", "#fff3e0"),
        "err": ("✖", "#c62828", "#fdecec"),
    }
    # result kind -> (colour, background, icon)
    _RESULT_STYLE = {
        "ok": ("#2e7d32", "#eef7ee", "✔"),
        "warn": ("#b26a00", "#fff5e5", "⚠"),
        "err": ("#c62828", "#fdecec", "✖"),
        "run": ("#1565c0", "#eef5fd", "↻"),
        "info": ("#555555", "#f2f2f2", "ℹ"),
    }
    # will-executor state -> (symbol, colour)
    _EXECUTOR_STYLE = {
        "waiting": ("↻", "#b26a00"),
        "received": ("✔", "#2e7d32"),
        "verifying": ("↻", "#1565c0"),
        "verified": ("✔", "#2e7d32"),
        "to_verify": ("✔", "#2e7d32"),
        "failed": ("✖", "#c62828"),
        "timeout": ("✖", "#c62828"),
        "to_sign": ("–", "#8a8a8a"),
        "to_send": ("–", "#8a8a8a"),
    }

    #: States of a will-executor that did not get the will (#93).
    FAILED_STATES = ("failed", "timeout")

    def __init__(self, format_amount=None, parent=None):
        super().__init__(parent)
        self.format_amount = format_amount
        # CHANGELOG #93: set by the dialog once it has finished; then the
        # will-executors box offers "Exclude" next to each executor that did
        # not get the will, and "Retry" under it. None while it works.
        self.on_exclude = None
        self.on_retry = None
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(10)

    @staticmethod
    def executor_text(state, seconds=None):
        """What the will-executors box says for ``state``."""
        if state == "timeout":
            if seconds:
                return _("no answer ({} s)").format(seconds)
            return _("no answer")
        return {
            "waiting": _("waiting…"),
            "received": _("received"),
            "verifying": _("received, verifying…"),
            "verified": _("received and verified"),
            "to_verify": _("received, to be verified"),
            "failed": _("not received"),
            "to_sign": _("to sign"),
            "to_send": _("to send"),
        }.get(state, "")

    def _clear(self):
        """Remove what the previous snapshot drew (at once, see #87)."""
        while self.box.count():
            item = self.box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _steps_bar(self, steps):
        """The five steps with their state and word, joined by arrows."""
        from ...core.build_report import step_label

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for i, (step, state, note) in enumerate(steps):
            symbol, fg, bg = self._STEP_STYLE.get(state, self._STEP_STYLE["todo"])
            cell = QFrame()
            cell.setObjectName("balStep")
            cell.setStyleSheet(
                "#balStep{{background:{};border:1px solid {};border-radius:6px;}}"
                "#balStep QLabel{{background:transparent;}}".format(bg, fg)
            )
            column = QVBoxLayout(cell)
            column.setContentsMargins(6, 4, 6, 4)
            column.setSpacing(0)
            top = QLabel("{}  {}".format(symbol, step_label(step)))
            top.setAlignment(Qt.AlignmentFlag.AlignCenter)
            top.setStyleSheet("color:{};font-weight:bold;".format(fg))
            column.addWidget(top)
            sub = QLabel(note or " ")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sub.setStyleSheet("color:#555555;font-size:11px;")
            column.addWidget(sub)
            row.addWidget(cell, 1)
            if i < len(steps) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet("color:#999999;")
                row.addWidget(arrow)
        return bar

    def _result_line(self, kind, text):
        """The result sentence (rich text), coloured like the will summary."""
        fg, bg, icon = self._RESULT_STYLE.get(kind, self._RESULT_STYLE["info"])
        frame = QFrame()
        frame.setObjectName("balResult")
        frame.setStyleSheet(
            "#balResult{{background:{};border-left:6px solid {};border-radius:4px;}}"
            "#balResult QLabel{{background:transparent;}}".format(bg, fg)
        )
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 8, 12, 8)
        sign = QLabel(icon)
        sign.setStyleSheet("color:{};font-size:20px;".format(fg))
        sign.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(sign)
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setStyleSheet("color:#111111;font-size:13px;")
        row.addWidget(label, 1)
        return frame

    def _group(self, title, rows, accent="#777777", actions=None, footer=None):
        """A titled white box; ``rows``: ``[(left, right or None, colour)]``.

        The texts are plain and escaped here. ``actions``: ``{row: widget}``
        put at the end of that row; ``footer``: a widget under the rows
        (#93: the "Exclude" and "Retry" buttons).
        """
        frame = QFrame()
        frame.setObjectName("balGroup")
        frame.setStyleSheet(
            "#balGroup{background:#ffffff;border:1px solid #d6d6d6;border-radius:6px;}"
            "#balGroup QLabel{background:transparent;color:#222222;}"
        )
        column = QVBoxLayout(frame)
        column.setContentsMargins(12, 8, 12, 10)
        column.setSpacing(4)
        head = QLabel(title)
        head.setStyleSheet("color:{};font-weight:bold;".format(accent))
        column.addWidget(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(0, 1)
        for r, (left, right, colour) in enumerate(rows):
            text = QLabel(html.escape(left).replace("\n", "<br>"))
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setWordWrap(True)
            grid.addWidget(text, r, 0)
            if right is not None:
                value = QLabel(html.escape(right))
                if colour:
                    value.setStyleSheet("color:{};".format(colour))
                grid.addWidget(value, r, 1, Qt.AlignmentFlag.AlignRight)
            if actions and r in actions:
                grid.addWidget(actions[r], r, 2, Qt.AlignmentFlag.AlignRight)
        column.addLayout(grid)
        if footer is not None:
            column.addWidget(footer, 0, Qt.AlignmentFlag.AlignRight)
        return frame

    @staticmethod
    def _small_button(text, slot):
        """A small button for a box row (#93)."""
        button = QPushButton(text)
        button.setStyleSheet("QPushButton{padding:1px 10px;}")
        button.clicked.connect(slot)
        return button

    def _amount(self, satoshi):
        """``satoshi`` in the wallet's unit, or in sat without a formatter."""
        if self.format_amount:
            try:
                return self.format_amount(satoshi)
            except Exception as e:
                _logger.debug(f"amount format failed: {e!r}")
        return "{} sat".format(satoshi)

    @staticmethod
    def _percent(pct):
        """``50%`` / ``33.3%`` with the decimal separator of the language."""
        from ...i18n import number_separators

        text = "{:.1f}".format(pct)
        if text.endswith(".0"):
            text = text[:-2]
        return text.replace(".", number_separators()[0]) + "%"

    def render(self, snapshot):
        """Draw ``snapshot`` (see ``BuildReport.snapshot``)."""
        self._clear()
        self.box.addWidget(self._steps_bar(snapshot["steps"]))
        if snapshot.get("result"):
            self.box.addWidget(self._result_line(*snapshot["result"]))
        boxes = snapshot.get("boxes") or {}
        titles = (
            ("changes", _("What changed"), "#777777"),
            ("warnings", _("Attention"), "#b26a00"),
            ("problems", _("What went wrong"), "#c62828"),
        )
        for key, title, accent in titles:
            lines = boxes.get(key) or []
            if lines:
                self.box.addWidget(
                    self._group(title, [(t, None, None) for t in lines], accent)
                )
        heirs = snapshot.get("heirs")
        if heirs:
            rows = [
                (name, "{}  ({})".format(self._amount(sats), self._percent(pct)), None)
                for name, sats, pct in heirs
            ]
            self.box.addWidget(self._group(_("Heirs"), rows))
        executors = snapshot.get("executors")
        if executors:
            rows = []
            actions = {}
            failed = False
            for url, (state, seconds) in executors:
                symbol, colour = self._EXECUTOR_STYLE.get(state, ("", None))
                host = urlparse(url).hostname or url
                rows.append(
                    (host, "{} {}".format(symbol, self.executor_text(state, seconds)),
                     colour)
                )
                if state in self.FAILED_STATES:
                    failed = True
                    if self.on_exclude:
                        actions[len(rows) - 1] = self._small_button(
                            _("Exclude"), lambda _c=False, u=url: self.on_exclude(u)
                        )
            footer = None
            if failed and self.on_retry:
                footer = self._small_button(_("Retry"), lambda _c=False: self.on_retry())
            self.box.addWidget(
                self._group(_("Will-Executor"), rows, actions=actions, footer=footer)
            )
        if snapshot.get("will_now"):
            self.box.addWidget(
                self._group(_("Your will now"), [(snapshot["will_now"], None, None)])
            )
