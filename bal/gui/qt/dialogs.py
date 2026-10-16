"""
bal.gui.qt.dialogs
==================

All modal/non-modal dialogs of the plugin.

    * BalDialog                  - common base dialog (icon, close handling).
    * BalWizard* (Dialog/Widget) - the step-by-step "create your will" wizard.
    * BalWaitingDialog /
      BalBlockingWaitingDialog   - progress dialogs for background tasks.
    * BalBuildWillDialog         - the central build/sign/push/broadcast flow.
    * WillDetailDialog           - shows the full will tree for one wallet.
    * WillExecutorDialog         - manage the list of will-executor servers.
    * WillExportDialog           - unified export window (File / QR / Audio);
      embeds a BalQrExportWidget for the QR transport.
    * BalQrExportWidget          - render+autoplay the will as QR frames.
    * WillImportDialog           - unified import window (File / QR / Audio);
      embeds a BalQrImportWidget for the QR transport.
    * BalQrImportWidget          - capture/assemble a will from QR frames via
      a continuous, hands-free camera loop (change/detection debounce via
      :func:`qr_import_accept_frame`) and send it to the review+sign wizard.
    * WillTxReviewSignDialog     - per-transaction review/sign wizard for the
      imported will (external copy, never touches the live will).

To keep the dialogs verbatim while avoiding import cycles with the list views,
the few list classes they reference are imported lazily inside the methods that
use them (see ``lists`` imports below).
"""

import copy
import html
import io
import json
import re
import zlib
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from electrum.util import MyEncoder
from PyQt6.QtCore import QEvent

from ...core import datetext, invalidation_preview, will_changes
from ...core.animated_qr import (
    AnimatedQrError,
    AnimatedQrSession,
    SessionLimitError,
    TransferConflictError,
    bbqr_frames,
    format_name,
    parse_for_detection,
)
from ...core.build_report import BuildReport, heir_shares
from ...core.checkalive import (
    check_alive_basic,
    resolve_date_to_check,
    resolve_relative_check_alive_date,
    will_executable_now,
)
from ...core.messages import (
    amounts_message,
    build_failure_message,
    check_alive_message,
    no_valid_transaction,
    rebuild_reason_message,
    will_change_message,
)
from ...core.qrtransfer import (
    CHUNK_PRESETS,
    decode_transfer,
    encode_transfer,
    preset_index_for_chunk_size,
)
from ...core.reminders import build_ics_reminders
from ...core.summary import executor_states, will_summary
from ...engine import build, check
from ...engine import context as engine_context
from ...engine.invalidate import broadcast_invalidation, build_invalidation
from ...engine.push import (
    ALREADY_PRESENT,
    EXCLUDED_FEE,
    RECEIVED,
    TIMEOUT,
    push_will,
    select_push,
)
from ...engine.sign import to_sign
from ...engine.verify import FAILED, NO_ANSWER, verify_targets, verify_will
from .calendar import BalCalendarButton
from .common import (
    HEIR_DUST_AMOUNT,
    HEIR_REAL_AMOUNT,
    N_,
    Any,
    BalanceTooLowException,
    BalTimestamp,
    BestEffortRequestFailed,
    Buttons,
    Callable,
    CancelButton,
    HeirAmountIsDustException,
    MessageBoxMixin,
    Network,
    NoHeirsException,
    NotCompleteWillException,
    NoWillExecutorNotPresent,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    TaskThread,
    TxBroadcastError,
    Util,
    WaitingDialog,
    Will,
    WillExecutorNotPresent,
    Willexecutors,
    WillExpiredException,
    WillItem,
    WillPostponedException,
    _,
    _logger,
    bring_to_front,
    decimal_point_to_base_unit_name,
    draw_qr,
    export_meta_gui,
    import_meta_gui,
    log_error,
    partial,
    pyqtSignal,
    read_json_file,
    read_QIcon_from_bytes,
    show_modal,
    show_on_top,
    stop_thread,
    time,
    top_level_of,
    tx_from_any,
    write_json_file,
)
from .flow_adapter import DialogFlowUI, DialogRun
from .report_actions import report_menu
from .widgets import (
    BuildReportView,
    WillSettingsWidget,
    WillWidget,
    delivery_level_colour,
)

if TYPE_CHECKING:
    from .window import BalWindow

# NOTE: list views (HeirListWidget, PreviewList, WillExecutorWidget) are
# imported lazily where needed to avoid a dialogs<->lists import cycle.


class BalDialog(QDialog,MessageBoxMixin):
    _stopping = False
    def __init__(self, parent, bal_plugin, title=None, icon="icons/bal16x16.png"):
        from PyQt6.QtCore import QMetaObject, Qt
        def handler(signum, frame):
            QMetaObject.invokeMethod(self, "close", Qt.ConnectionType.QueuedConnection)

        #signal.signal(signal.SIGINT, handler)
        # NOTE: do NOT store this as ``self.parent`` - that would shadow
        # QWidget.parent() and can make the dialog disappear behind Electrum.
        self._bal_parent = parent
        self.thread = None
        # Anchor the dialog to the *top-level* Electrum window so it always
        # stays in front of it (instead of falling behind).
        super().__init__(top_level_of(parent))
        if title:
            self.setWindowTitle(title)
        # WindowModalDialog.__init__(self,parent)
        self.setWindowIcon(read_QIcon_from_bytes(bal_plugin.read_file(icon)))

    def closeEvent(self, event):
        self._stopping = True
        # NOTE: we deliberately do NOT stop ``self.thread`` here.
        #
        # Electrum's ``TaskThread`` delivers results via ``on_done`` which calls
        # ``cb_done`` (often ``self.accept`` -> closes this dialog) *before*
        # ``cb_result`` (``on_success`` -> e.g. updating the will-executor
        # list).  If we stop/join the thread inside ``closeEvent`` the close
        # triggered by ``accept`` tears the thread down *before* ``on_success``
        # runs, so the downloaded data is silently dropped.  The original plugin
        # left this commented out for exactly this reason; subclasses that own a
        # genuinely long-lived thread stop it explicitly in their own close
        # handler.
        super().closeEvent(event)

    def hideEvent(self, event):
        self._stopping = True
        self._set_summary_busy(False)
        super().hideEvent(event)

    # CHANGELOG #84a: the "working" dialogs (build/check, sign, send, wait)
    # set ``_summary_window`` to their BalWindow. While one of them is on
    # screen the will summary of the Will tab only says "operation in
    # progress": otherwise it showed intermediate states ("to sign") and a
    # second button behind the dialog that was already asking the same
    # thing (owner's remark).
    _summary_window = None
    _summary_busy_on = False

    def showEvent(self, event):
        self._set_summary_busy(True)
        super().showEvent(event)

    def _set_summary_busy(self, busy):
        """Tell the BalWindow (if any) that this working dialog is shown/hidden.

        The per-dialog flag keeps the window's counter balanced when Qt
        sends several show/hide events. Never raises.
        """
        window = self._summary_window
        if window is None or self._summary_busy_on == busy:
            return
        self._summary_busy_on = busy
        try:
            window.set_will_summary_busy(busy)
        except Exception as e:
            _logger.debug(f"summary busy update failed: {e!r}")


class BalWizardDialog(BalDialog):
    # CHANGELOG #134: the BASIC flow (``basic_wizard.BasicWizardFlow``) of
    # this dialog, None in ADVANCED; True once "Create the will" was pressed
    # (the changes are kept, nothing is restored at close).
    basic_flow = None
    _keep_changes = False

    def __init__(self, bal_window: "BalWindow"):
        """The wizard "Build / Edit Your Will".

        CHANGELOG #134: in BASIC mode the four pages of
        ``bal.gui.qt.basic_wizard`` (When, Heirs, Will-executors, Summary)
        with a snapshot that Cancel and the close button restore; in
        ADVANCED the old pages, unchanged (Heirs first).
        """
        assert bal_window
        BalDialog.__init__(
            self, bal_window.window, bal_window.bal_plugin,
            _("Safe21 Heartbeat Wizard Setup"),
        )
        self.setMinimumSize(800, 400)
        self.bal_window = bal_window
        self._bal_parent = bal_window.window
        self.layout = QVBoxLayout(self)
        self._keep_changes = False
        self.basic_flow = None
        if bal_window.bal_plugin.is_basic_mode():
            # Lazy import: basic_wizard imports this module.
            from .basic_wizard import BasicWizardFlow

            self.basic_flow = BasicWizardFlow(self)
            self.widget = self.basic_flow.first_page()
        else:
            self.widget = BalWizardHeirsWidget(
                bal_window, self, self.on_next_heir, None, self.on_cancel_heir
            )
        self.layout.addWidget(self.widget)

    def switch_to_advanced(self):
        """ADVANCED was confirmed on the BASIC page 1 (#134): go on with the
        old pages, from the Heirs page.

        The BASIC snapshot is dropped: the old pages save every change at
        once and their Cancel undoes nothing, exactly as today, and what the
        BASIC pages had already saved stays (a date chosen on page 1 is
        saved only by its Next).
        """
        self.basic_flow = None
        self.next_widget(
            BalWizardHeirsWidget(
                self.bal_window, self, self.on_next_heir, None, self.on_cancel_heir
            )
        )

    def show_executors_step(self):
        """Open the wizard at its will-executor step (#134).

        BASIC: page 3 of the new wizard (download, ping, select); ADVANCED:
        the old download-method page, as before. Used by the build window's
        "no will-executor" buttons.
        """
        if self.basic_flow is not None:
            self.basic_flow.show_executors()
            return
        self.on_next_heir()
        self.on_next_locktimeandfee()

    def next_widget(self, widget):
        """Show wizard page ``widget`` in place of the current one.

        CHANGELOG #127: the dialog keeps its size when the page changes, so
        a taller page (the settings page with the mode selector) was squeezed
        under its minimum: the rows overlapped and the "BASIC" box was
        unreadable until the owner moved the window. The layout is now
        activated at once and the dialog grows to the new page's size hint
        (it never shrinks). The page is shown first: a widget added to a
        visible dialog is shown only later by Qt, and a hidden one does not
        count in the size hint.
        """
        self.layout.removeWidget(self.widget)
        self.widget.close()
        self.widget = widget
        self.layout.addWidget(self.widget)
        self.widget.show()
        self.layout.activate()
        self._grow_to_fit()

    def _grow_to_fit(self):
        """Grow the dialog to its size hint, never shrink it (#127).

        The minimum size alone gives every row only its minimum height
        (the "BASIC" box 19 pixels instead of 22); the size hint gives
        each row its normal height.
        """
        self.resize(self.size().expandedTo(self.sizeHint()))

    def event(self, event):
        """After every new layout of the page, grow to fit it (#127).

        Qt sends ``LayoutRequest`` to the dialog whenever a widget of its
        page is shown, hidden or resized, e.g. the rows that ADVANCED adds
        when it is chosen in the page's own mode selector.
        """
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            self._grow_to_fit()
        return result

    def on_next_heir(self):
        self.next_widget(
            BalWizardLocktimeAndFeeWidget(
                self.bal_window,
                self,
                self.on_next_locktimeandfee,
                self.on_previous_heir,
                self.on_cancel_heir,
            )
        )

    def on_previous_heir(self):
        self.next_widget(
            BalWizardHeirsWidget(
                self.bal_window, self, self.on_next_heir, None, self.on_cancel_heir
            )
        )

    def on_cancel_heir(self):
        pass

    def on_next_wedonwload(self):
        self.next_widget(
            BalWizardWEWidget(
                self.bal_window,
                self,
                self.on_next_we,
                self.on_next_locktimeandfee,
                self.on_cancel_heir,
            )
        )

    def on_next_we(self):
        """Last wizard page: build, sign, send and verify the will, then close.

        The will-executors are verified by the Building Will dialog itself,
        as its last step (CHANGELOG #84b-2). Before, the wizard ran the same
        check as the Check button (allegato15, case B) in a separate "Check
        Transaction" window opened after that dialog had closed.
        """
        # #134: the BASIC wizard keeps its changes (no restore at close).
        self._keep_changes = True
        close_window = BalBuildWillDialog(self.bal_window)
        close_window.build_will_task()
        self.close()
        # self.next_widget(BalWizardLocktimeAndFeeWidget(self.bal_window,self,self.on_next_locktimeandfee,self.on_next_wedonwload,self.on_next_wedonwload.on_cancel_heir))

    def on_next_locktimeandfee(self):
        self.next_widget(
            BalWizardWEDownloadWidget(
                self.bal_window,
                self,
                self.on_next_wedonwload,
                self.on_next_heir,
                self.on_cancel_heir,
            )
        )

    def on_accept(self):
        self.bal_window.update_all()
        pass

    def on_reject(self):
        pass

    def on_close(self):
        self.bal_window.update_all()
        pass

    def closeEvent(self, event):
        """Cancel and the close button end here (#134: the BASIC wizard
        puts back what it changed, unless "Create the will" was pressed)."""
        self._stopping = True
        self._restore_if_cancelled()
        # self.bal_window.heir_list_widget.will_settings_widget.update_will_settings()

    def reject(self):
        """Esc closes a QDialog through ``reject`` without a close event:
        restore there too (#134), then close as Qt does."""
        self._restore_if_cancelled()
        super().reject()

    def _restore_if_cancelled(self):
        """The BASIC snapshot back (once), unless the changes are kept."""
        flow = self.basic_flow
        if flow is None or self._keep_changes:
            return
        try:
            flow.restore()
        except Exception as e:
            _logger.error(f"wizard cancel: restore failed: {e!r}")



class BalWizardWidget(QWidget):
    title = None
    message = None

    def __init__(
        self, bal_window: "BalWindow", parent, on_next, on_previous, on_cancel
    ):
        QWidget.__init__(self, parent)
        self.vbox = QVBoxLayout(self)
        self.bal_window = bal_window
        self._bal_parent = parent
        self.on_next = on_next
        self.on_cancel = on_cancel
        # title/message are class attributes marked with N_(): translate them
        # here, when shown (a class body runs before the catalog is loaded).
        self.titleLabel = QLabel(_(self.title) if self.title else "")
        self.vbox.addWidget(self.titleLabel)
        self.messageLabel = QLabel(_(self.message))
        self.vbox.addWidget(self.messageLabel)

        self.content = self.get_content()
        self.content_container = QWidget()
        self.containrelayout = QVBoxLayout(self.content_container)
        self.containrelayout.addWidget(self.content)

        self.vbox.addWidget(self.content_container)

        spacer_widget = QWidget()
        spacer_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.vbox.addWidget(spacer_widget)

        self.buttons = []
        if on_previous:
            self.on_previous = on_previous
            self.previous_button = QPushButton(_("Previous"))
            self.previous_button.clicked.connect(self._on_previous)
            self.buttons.append(self.previous_button)

        self.next_button = QPushButton(_("Next"))
        self.next_button.clicked.connect(self._on_next)
        self.buttons.append(self.next_button)

        self.abort_button = QPushButton(_("Cancel"))
        self.abort_button.clicked.connect(self._on_cancel)
        self.buttons.append(self.abort_button)

        self.vbox.addLayout(Buttons(*self.buttons))

    def _on_cancel(self):
        self.on_cancel()
        self._bal_parent.close()

    def _on_next(self):
        if self.validate():
            self.on_next()

    def _on_previous(self):
        self.on_previous()

    def get_content(self):
        pass

    def validate(self):
        return True



class BalWizardHeirsWidget(BalWizardWidget):
    title = N_("Safe21 Heartbeat - Heirs")
    message = N_(
        "Please add your heirs\n remember that 100% of wallet balance will be spent"
    )

    def get_content(self):
        # Lazy import to avoid a dialogs<->lists import cycle (lists imports
        # BalBuildWillDialog from this module at load time).
        from .lists import HeirListWidget
        self.heir_list_widget = HeirListWidget(self.bal_window, self)
        button_add = QPushButton(_("Add"))
        button_add.clicked.connect(self.add_heir)
        button_import = QPushButton(_("Import"))
        button_import.clicked.connect(self.import_from_file)
        button_export = QPushButton(_("Export"))
        button_export.clicked.connect(self.export_to_file)
        widget = QWidget()
        vbox = QVBoxLayout(widget)
        vbox.addWidget(self.heir_list_widget)
        vbox.addLayout(Buttons(button_add, button_import, button_export))
        return widget

    def import_from_file(self):
        self.bal_window.import_heirs()
        self.heir_list_widget.update()

    def export_to_file(self):
        self.bal_window.export_heirs()

    def add_heir(self):
        self.bal_window.new_heir_dialog()
        self.heir_list_widget.update()

    def validate(self):
        return True



class BalWizardWEDownloadWidget(BalWizardWidget):
    title = N_("Safe21 Heartbeat - Will-Executors")
    message = N_("Choose willexecutors download method")

    def get_content(self):
        # question = QLabel()
        self.combo = QComboBox()
        self.combo.addItems(
            [
                _("Automatically download and select willexecutors"),
                _("Only download willexecutors list"),
                _("Import willexecutor list from file"),
                _("Manual"),
            ]
        )
        # heir_name.setFixedWidth(32 * char_width_in_lineedit())
        return self.combo

    def validate(self):
        return True

    def _on_next(self):
        """Run the chosen download method, then go to the next page.

        0: download, ping and select only who answers; 1: download and ping
        only; 2: import a file; 3: nothing (manual). 0 and 1 go through
        ``BalWindow.download_and_select_willexecutors`` (#134).
        """
        index = self.combo.currentIndex()
        _logger.debug(f"selected index:{index}")
        if index < 3:
            self.bal_window.willexecutors = Willexecutors.get_willexecutors(
                self.bal_window.bal_plugin
            )

            if index == 2:

                def do_nothing():
                    self.bal_window.willexecutors.update(self.willexecutors)
                    Willexecutors.save(
                        self.bal_window.bal_plugin, self.bal_window.willexecutors
                    )
                    pass

                import_meta_gui(
                    self.bal_window.window,
                    _("willexecutors"),
                    self.import_json_file,
                    do_nothing,
                )

            if index < 2:
                # Task #02 - "Automatically download and select" (index 0)
                # and "Only download" (index 1). Since CHANGELOG #134 the
                # download, ping and select-only-who-answers live in one
                # function shared with the BASIC wizard's will-executor page
                # (see its docstring for why the deselect matters).
                self.bal_window.download_and_select_willexecutors(
                    select_responding=index < 1
                )

        elif index == 3:
            # TODO DO NOTHING
            pass

        self.bal_window.will_list_widget.update()
        if self.validate():
            return self.on_next()

    def import_json_file(self, path):
        data = read_json_file(path)
        data = self._validate(data)
        self.willexecutors = data

    def _validate(self, data):
        return data



class BalWizardWEWidget(BalWizardWidget):
    title = N_("Safe21 Heartbeat - Will-Executors")
    message = N_("Configure and select your willexecutors")

    def get_content(self):
        # Lazy import to avoid a dialogs<->lists import cycle.
        from .lists import WillExecutorWidget
        widget = QWidget()
        vbox = QVBoxLayout(widget)
        vbox.addWidget(
            WillExecutorWidget(
                self,
                self.bal_window,
                Willexecutors.get_willexecutors(self.bal_window.bal_plugin),
            )
        )
        return widget



class BalWizardLocktimeAndFeeWidget(BalWizardWidget):
    title = N_("Safe21 Heartbeat - Will Settings")
    message = ""

    def get_content(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # The wizard ("Build your will") is the ONLY place the delivery time,
        # check alive and fee can be edited, so it is the only read_only=False.
        layout.addWidget(WillSettingsWidget(self.bal_window, self, "v",
                                            read_only=False))
        spacer_widget = QWidget()
        spacer_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(spacer_widget)
        return widget



class BalWaitingDialog(BalDialog):
    updatemessage = pyqtSignal([str], arguments=["message"])

    def __init__(
        self,
        bal_window: "BalWindow",
        message: str,
        task,
        on_success=None,
        on_error=None,
        on_cancel=None,
        exe=True,
    ):
        assert bal_window
        BalDialog.__init__(
            self, bal_window.window, bal_window.bal_plugin, _("Please wait")
        )
        self._summary_window = bal_window
        self.message_label = QLabel(message)
        vbox = QVBoxLayout(self)
        vbox.addWidget(self.message_label)
        self.updatemessage.connect(self.update_message)
        if on_cancel:
            self.cancel_button = CancelButton(self)
            self.cancel_button.clicked.connect(on_cancel)
            vbox.addLayout(Buttons(self.cancel_button))
        self.accepted.connect(self.on_accepted)
        self.task = task
        self.on_success = on_success
        self.on_error = on_error
        self.on_cancel = on_cancel
        if exe:
            self.exe()

    def exe(self):
        self.thread = TaskThread(self)
        self.thread.finished.connect(self.deleteLater)  # see #3956
        self.thread.add(self.task, self.on_success, self.accept, self.on_error)
        # IMPORTANT: keep the *application-modal* exec() of the original code.
        # This dialog is driven by a TaskThread whose result (on_success, e.g.
        # populating the will-executor list) is delivered via a queued signal
        # while exec() spins the modal event loop.  Switching to window-modal
        # changed how the modal loop interacts with that delivery and could
        # cause the downloaded list to never be applied.  We only add the
        # raise/activate so the dialog stays visible, without altering modality.
        bring_to_front(self)
        self.exec()

    def hello(self):
        pass


    def on_accepted(self):
        pass

    def update_message(self, msg):
        self.message_label.setText(msg)

    def update(self, msg):
        self.updatemessage.emit(msg)

    def getText(self):
        return self.message_label.text()




class BalBlockingWaitingDialog(BalDialog):
    def __init__(self, bal_window: "BalWindow", message: str, task: Callable[[], Any]):
        BalDialog.__init__(self, bal_window, bal_window.bal_plugin, _("Please wait"))
        self.message_label = QLabel(message)
        vbox = QVBoxLayout(self)
        vbox.addWidget(self.message_label)
        self.finished.connect(self.deleteLater)  # see #3956
        # show popup (window-modal + on top so it is actually visible)
        show_on_top(self)
        # Refresh the GUI so the popup is painted (and message_label drawn)
        # BEFORE we block the GUI thread running the task; otherwise the popup
        # appears empty/frozen.
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        QApplication.processEvents()
        try:
            # block and run given task
            task()
        finally:
            # close popup
            self.accept()


class BalBuildWillDialog(BalDialog):
    updatemessage = pyqtSignal()
    COLOR_WARNING = "#cfa808"
    COLOR_ERROR = "#ff0000"
    COLOR_OK = "#05ad05"
    # CHANGELOG #84b: class-level defaults, so the report also works on a
    # dialog whose __init__ did not run (the scenario harness): reading a
    # missing attribute of such a Qt object raises RuntimeError, not
    # AttributeError, and getattr's default would not help.
    _auto_box_off = False
    _build_report = None
    _times = None
    _log_auto_opened = False
    # CHANGELOG #84b-2 (same reason as above).
    _closing = False
    verify_row = None
    # CHANGELOG #93 (same reason as above).
    _close_layout = None
    report_view = None
    # CHANGELOG #134 (same reason as above): see _revert_cancelled_postpone.
    _postpone_reverted = None
    _later_by_question = False

    def __init__(self, bal_window, parent=None, renew=False, closing=False):
        """``renew=True``: the owner already chose "Renew" for a passed Check
        Alive (wallet open / Prepare, CHANGELOG #79), so it is not asked
        again here.

        ``closing=True``: opened because the wallet is closing (the "Rebuild
        will on wallet close" setting). The dialog then says so, and why, in
        its title and in a box above the steps (CHANGELOG #84b-2, owner: a
        window asking for the password while Electrum closes must explain
        itself).
        """
        if not parent:
            parent = bal_window.window
        title = (
            _("Closing the wallet: checking your will")
            if closing
            else _("Building Will")
        )
        BalDialog.__init__(self, parent, bal_window.bal_plugin, title)
        self._closing = closing
        self._summary_window = bal_window
        self._renew_approved = renew
        self._check_alive_info = None
        # (parent already stored as self._bal_parent by BalDialog.__init__)
        self.updatemessage.connect(self.msg_update)
        self.bal_window = bal_window
        self.bal_plugin = bal_window.bal_plugin
        self.message_label = QLabel(_("Building Will:"))
        # Allow the long report text to wrap instead of forcing the dialog ever
        # wider, and let it grow downward inside the scroll area below.
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.vbox = QVBoxLayout(self)
        if closing:
            # Above the scroll area, so it stays in view while the steps run.
            self.vbox.addWidget(self._closing_intro())

        # SCROLLABLE message area (allegato14): with many will-executors the
        # report can reach dozens of lines. Previously the dialog kept resizing
        # itself taller for every new line (see msg_update's resize), so with
        # e.g. 50 will-executors the window grew past the screen and the bottom
        # buttons (Close) became unreachable. We now put the message label in a
        # QScrollArea with a capped maximum height: once the text exceeds that
        # height a vertical scrollbar appears and the buttons stay visible.
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        # CHANGELOG #84b: the steps bar, the result sentence and the boxes go
        # above the old rows, which become a collapsible log (closed by
        # default, opened by itself on an error: owner's decisions).
        self.report_view = BuildReportView(self._format_amount)
        self.log_toggle = QPushButton()
        self.log_toggle.setCheckable(True)
        self.log_toggle.setFlat(True)
        self.log_toggle.setStyleSheet(
            "QPushButton{color:#1565c0;text-align:left;border:none;padding:0;}"
        )
        self.log_toggle.toggled.connect(self._on_log_toggled)
        self._set_log_toggle_text()
        self._log_auto_opened = False
        content = QWidget()
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(4, 4, 4, 4)
        content_box.setSpacing(8)
        content_box.addWidget(self.report_view)
        content_box.addWidget(self.log_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        content_box.addWidget(self.message_label)
        content_box.addStretch(1)
        self.message_label.setVisible(False)
        self.scroll_area.setWidget(content)
        # The dialog's height follows its content up to a share of the
        # screen (see _fit_height); beyond that the scrollbar takes over and
        # the bottom buttons stay reachable (allegato14). The small minimum
        # only keeps the layout from squeezing the area under the buttons.
        self.scroll_area.setMinimumHeight(240)
        self.vbox.addWidget(self.scroll_area, 1)

        # Kept for backward compatibility (referenced by old/commented code);
        # no longer used to lay out the messages.
        self.qwidget = QWidget(self)
        self.labelsbox = QVBoxLayout(self.qwidget)
        self.setMinimumWidth(720)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.labels = []
        self.check_row = None
        self.inval_row = None
        self.build_row = None
        self.sign_row = None
        self.push_row = None
        self.verify_row = None
        # Manual next-steps hint (Sign / Broadcast) shown to the user after the
        # dialog finishes; None when nothing is left to do.
        self._next_steps_hint = None
        # Set to True by _sync_locktime_to_built_txs when the delivery date was
        # automatically anticipated during a rebuild. Used to explain to the
        # user WHY signing is being requested (otherwise the sign prompt appears
        # without any reason, as the owner reported).
        self._date_was_anticipated = False
        # Set to True right after we broadcast an automatic invalidation
        # transaction (the "postpone" path). On the very next phase-1 re-check
        # Electrum may not have seen the invalidation tx yet, so it would still
        # report a postpone and the wizard would re-prompt to invalidate over
        # and over (the reported loop). When this flag is set and a postpone is
        # STILL detected, we STOP with a clear message instead of re-prompting.
        self._invalidation_broadcast = False
        # CHANGELOG #77: why the old will was invalidated (a later date, or
        # the Check Alive threshold) and the will-executors of a will prepared
        # earlier but never signed. Only used to word the "Checking your will"
        # row; see _rebuild_reason_message.
        self._postpone_invalidation = False
        # #134: (chosen, restored) when a postpone was cancelled before its
        # invalidation reached the network and the stored date went back.
        self._postpone_reverted = None
        self._unsigned_executors = None
        self.network = Network.get_instance()
        self._stopping = False
        self.thread = TaskThread(self)
        self.thread.finished.connect(self.task_finished)  # see #3956

    def task_finished(self):
        pass

    def _closing_intro(self):
        """The light-blue box that says why this dialog opened at closing.

        Text approved by the owner (CHANGELOG #84b-2). On purpose it does
        NOT say how to turn the check off: the owner finds that dangerous
        (new funds would stay out of the will).
        """
        label = QLabel(_(
            "You are closing the wallet. Before it closes, the plugin checks "
            "that your will covers all your bitcoin, including those received "
            "after the last check. If something changed, it prepares a new "
            "will and asks for your password to sign it. Then it sends it to "
            "the will-executors and verifies that they received it."
        ))
        label.setWordWrap(True)
        label.setObjectName("balClosingIntro")
        label.setStyleSheet(
            "#balClosingIntro{background:#e3f2fd;color:#0d47a1;"
            "border:1px solid #90caf9;border-radius:6px;padding:8px;}"
        )
        return label

    def _add_closing_note(self, row):
        """At closing, tell the owner that Close lets the wallet close.

        ``row`` is a button row; the note goes on its left. Nothing when the
        dialog was not opened by the wallet closing.
        """
        if not self._closing:
            return
        note = QLabel(_("When you press Close, the wallet finishes closing."))
        row.insertWidget(0, note)

    def build_will_task(self):
        _logger.debug("build will task to be started")
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )
        # exec() already shows the dialog modally; route through the helper so
        # it is window-modal and brought to the front (no separate show()).
        show_modal(self)

    def task_phase1(self):
        """Phase 1 of the Check, on the dialog's worker thread.

        The variables, the Check Alive and the amounts run in the engine
        (:func:`bal.engine.check.check_variables`, CHANGELOG #120; its rows
        are written by :class:`DialogFlowUI` as before), then the will in
        force and the rebuild below. Returns the 2-tuple
        ``_on_success_phase1_body`` unpacks as ``have_to_sign, tx`` (a stop
        code and its transaction, or the sign flag and the built
        transactions), or ``None`` when the dialog is closing.
        """
        if self._stopping:
            return
        txs = None
        _logger.debug("close plugin phase 1 started")
        # A new DialogFlowUI / DialogRun at every pass: each pass writes its
        # own "Checking variables" row, as the old local ``varrow`` did.
        stop = check.check_variables(
            self.bal_window, DialogRun(self), DialogFlowUI(self)
        )
        if stop is not None:
            return stop.legacy()

        self.msg_set_checking()
        have_to_build = False
        # Snapshot BEFORE check_will: it discards the unsigned items, after
        # which they can no longer be told apart (CHANGELOG #77).
        self._unsigned_executors = self._unsigned_will_executors()
        try:
            try:
                # #135: the first check of the Check may also undo an
                # anticipation nobody needs any more (never after a build).
                self.bal_window.check_will(undo_anticipation=True)
            finally:
                # #135: an heir only renamed, said once, whatever the check
                # finds next (it may still need a new will for another
                # reason).
                self._report_renames()
            self.msg_set_checking(self.msg_ok())
        except WillExpiredException:
            # UNIFY INVALIDATE PROCEDURE (+ task #03):
            #
            # The will is already expired (e.g. the CHECK button is pressed on an
            # expired will). Previously this returned (None, invalidate_tx),
            # which routed to the automatic invalidate path (password prompt +
            # auto-broadcast) that did NOT set the "Safe21 Heartbeat invalidation"
            # history label.
            #
            # We now return the SAME "invalidate_classic" signal used elsewhere,
            # so on_success_phase1 shows the warning popup and auto-opens
            # Electrum's classic transaction window (which sets the label). This
            # makes the CHECK button and the WIZARD behave identically and fixes
            # the missing-label bug (#03).
            # #127: a period-form will keeps its Check Alive in BASIC too.
            if not check_alive_basic(
                self.bal_window.bal_plugin.is_basic_mode(),
                self.bal_window.will_settings,
            ) and not will_executable_now(
                Will.get_min_locktime(self.bal_window.willitems)
            ):
                # CHANGELOG #80: the will cannot be executed yet; it is only
                # "expired" against a Check Alive that moved later with the
                # delivery ("N days before"): the delivery was postponed
                # beyond the Check Alive margin. Same as a postpone: invalidate
                # and prepare the new will in this Check, instead of the false
                # "Your will has expired" and a second Check.
                _logger.debug("expired against the Check Alive only: postpone")
                self._postpone_invalidation = True
                self.msg_set_checking(_("Postponed: invalidating old will"))
                return None, build_invalidation(self.bal_window)
            _logger.debug("expired")
            self.msg_set_checking(_("Expired"))
            return "invalidate_classic", None
        except WillPostponedException as e:
            # An already signed/sent will is being postponed.  Like an expired
            # will, the previously committed coins must be invalidated on-chain
            # FIRST (otherwise a will-executor could broadcast the old,
            # earlier-locktime tx and execute the inheritance too early).  We
            # return (None, tx) so phase 2 asks the user to sign and broadcast
            # the invalidation; afterwards the user presses Prepare again to
            # rebuild the new (postponed) inheritance.
            _logger.debug(f"postponed {e}")
            self._postpone_invalidation = True
            self.msg_set_checking(_("Postponed: invalidating old will"))
            # The shared build (#152), as the Will tab's "Invalidate will".
            return None, build_invalidation(self.bal_window)
        except NoHeirsException:
            _logger.debug("no heirs")
            self.msg_set_checking(_("No Heirs"))
        except NotCompleteWillException as e:
            _logger.debug(f"not complete {e} true")
            message = False
            have_to_build = True
            # Task #7a: if the will was already executed, the wallet is empty and
            # this exception is expected. Before showing the alarming "Found
            # CHANGES ... a NEW WILL must be prepared" message, add a clear,
            # reassuring note on the "Checking your will" row telling the user
            # the inheritance is already on its way (mempool) or done (on-chain).
            # The original message is still shown afterwards (owner request: the
            # red/"changes" message stays, this is only an extra, more precise
            # informative line).
            # NOTE: we add the informative note as its OWN extra row (not via
            # msg_set_checking, which reuses self.check_row and would be
            # overwritten by the "Found CHANGES" line set below). Passing row=None
            # to msg_set_status appends a new line, so the executed/mempool note
            # and the original message are BOTH visible.
            executed_status = self._executed_inheritance_status()
            if executed_status == "CONFIRMED":
                # Green: the inheritance transaction is confirmed on the
                # blockchain, so it has been executed correctly.
                self.msg_set_status(
                    _("Checking your will"),
                    None,
                    _("An inheritance of this wallet is already executed (on blockchain)"),
                    self.COLOR_OK,
                )
            elif executed_status == "MEMPOOL":
                # Orange (warning colour): the transaction is in the mempool,
                # waiting to be confirmed. Not an error, just "in progress".
                self.msg_set_status(
                    _("Checking your will"),
                    None,
                    _("Inheritance in mempool (waiting confirmation)"),
                    self.COLOR_WARNING,
                )
            # All of these situations used to collapse into the SAME sentence
            # ("Found CHANGES to the DATE or the HEIRS, a NEW WILL must be
            # prepared"), shown for five genuinely different causes - and
            # plainly WRONG for the most common one, receiving funds, where
            # neither the date nor the heirs changed.  _check_failure_message
            # names the real cause using the detail each exception already
            # carries (heir name, will-executor URL, old/new fee rate).
            # A delivery date in the past is the real cause of whatever the
            # core reports here (BUG-4, CHANGELOG #75): say that instead.
            # CHANGELOG #77: right after an invalidation, or after a Cancel at
            # signing, the core's sentence is true but misleading: say why.
            message = (
                self._past_date_message()
                or self._rebuild_reason_message(e)
                or self._check_failure_message(e)
            )
            _logger.debug(f"message: {message}")
            self.msg_set_checking(message)

        if have_to_build:
            self.msg_set_building()
            # CHANGELOG #128: which items were REPLACED before the build, to
            # tell when the build only gave back the will in force.
            replaced_before = Will.replaced_ids(self.bal_window.willitems)
            try:
                txs = self.bal_window.build_will()
                if not txs:
                    # The message now names the ACTUAL reason the build gave
                    # up (recorded by Heirs.buildTransactions) instead of
                    # listing three fixed guesses that were frequently all
                    # wrong.  msg_alert keeps the warning sign coloured and the
                    # text in the default colour so it stays readable.
                    self.msg_set_building(
                        self.msg_alert(self._build_failure_message())
                    )
                    return False, None

                self.bal_window.check_will()
                if Will.only_brought_back(replaced_before, self.bal_window.willitems):
                    self._in_force_kept_report()
                else:
                    self._build_success_report()
            except WillExecutorNotPresent:
                self.msg_set_status(
                    _("Will-Executor excluded"), None, _("Skipped"), self.COLOR_ERROR
                )

            except NoWillExecutorNotPresent:
                _logger.debug("no will-executor selected, build interrupted")
                self.msg_set_status(
                    _("Will-Executor"), None,
                    _("Not present - select one or enable backup mode"),
                    self.COLOR_ERROR,
                )
                return "no_willexecutor", None

            except WillExpiredException as e:
                # An expired will is an EXPECTED situation (the locktime has
                # passed). After adding/changing an heir the will is rebuilt
                # above (build_will), and the freshly rebuilt transactions can
                # themselves already be expired.
                #
                # We must NOT trigger the wizard's automatic invalidation loop
                # here (return None, invalidate_tx). That loop re-runs
                # task_phase1 right after broadcasting the invalidation, but the
                # invalidation tx is not yet visible in the mempool, so the will
                # is still detected as expired and the user is asked to
                # invalidate again and again (infinite loop). It also never sets
                # the "Safe21 Heartbeat invalidation" history label.
                #
                # Instead we reproduce EXACTLY what the "Tools -> invalidate"
                # menu does (BalWalletWindow.invalidate_will): open Electrum's
                # classic transaction dialog so the user can sign and broadcast
                # the invalidation manually, set the proper history label, and
                # stop. This is robust regardless of mempool confirmation state.
                #
                # The actual call to invalidate_will() (which opens GUI windows)
                # must run in the GUI thread, so we only RETURN a signal here
                # ("invalidate_classic"); on_success_phase1 performs the call.
                # We still show the expired notice as a WARNING (orange).
                self.msg_set_building(self.msg_warning(e))
                return "invalidate_classic", None

            except NotCompleteWillException as e:
                # IMPORTANT (bugs E/F/K): build_will() above has just REBUILT the
                # whole will because the heirs (or the date) changed. The
                # post-build re-validation (check_will) then legitimately reports
                # that the new will differs from the previous one - e.g. it
                # raises HeirNotFoundException for a heir that is not yet covered
                # by an already-signed transaction. That is NOT an error: it is
                # exactly the signal that the freshly built transactions still
                # need to be SIGNED (and then broadcast).
                #
                # Previously this fell through to the generic "except Exception"
                # below, which (1) printed the heir name in RED and (2) returned
                # have_to_sign=False, so the rebuilt will was never signed
                # ("Nothing to do"). We now treat it as a successful rebuild:
                # show the green "Ok" + the heir list and FALL THROUGH to the
                # have_to_sign detection, so the new (status "New", not COMPLETE)
                # transactions are correctly detected and signed/broadcast.
                _logger.debug(f"will rebuilt, needs signing: {e}")
                self._build_success_report()

            except HeirAmountIsDustException:
                # ALL-DUST CASE (owner request): every heir's share is below the
                # Bitcoin dust limit, so the inheritance would pay nobody. We do
                # NOT build, sign or check anything - we show a clear message in
                # red and stop, so no "empty" will ends up in the list. This is
                # raised by Heirs.prepare_lists only when ALL real heirs are
                # dust; a mix of dust + valid heirs never reaches here.
                self.msg_set_building(
                    self.msg_error(
                        _(
                            "All heirs' shares are below the dust limit: "
                            "the inheritance cannot be created. "
                            "Increase the amounts or reduce the number of heirs."
                        )
                    )
                )
                return False, None

            except BalanceTooLowException as e:
                # The core DOES detect this precisely and carries the numbers,
                # but the exception was never caught here: it fell through to
                # the generic handler below, which printed the raw technical
                # string in red and re-raised.  Show the real figures instead.
                self.msg_set_building(
                    self.msg_alert(
                        _(
                            "Wallet balance is too low: {} satoshi available, "
                            "but the miner and will-executor fees need {} "
                            "satoshi (the minimum usable amount is {} "
                            "satoshi). Add funds, or select fewer "
                            "will-executors."
                        ).format(
                            int(e.balance), int(e.fees), int(e.dust_threshold)
                        )
                        + "\n\n"
                        + _("Skipped")
                    )
                )
                return False, None

            except Exception as e:
                self.msg_set_building(self.msg_error(e))
                raise e
                return False, None

        # DUST report (one line PER HEIR, not per will-executor).
        #
        # WHY: the wallet balance can be so small that each heir's share falls
        # below Bitcoin's dust limit, so the inheritance is not feasible. The
        # "is DUST" condition depends ONLY on the heir's amount, NOT on which
        # will-executor transaction we are looking at. The previous code looped
        # over every valid will (one per will-executor) AND every heir, so with
        # e.g. 20 will-executors and 10 heirs it printed 20x10 = 200 identical
        # "is DUST ... Excluded from will <wid>" rows (owner report, allegato13).
        #
        # We now collect the dust heirs in a de-duplicated dict (heir id ->
        # dust amount) across all valid wills and print ONE row per heir,
        # without the will-executor reference. So N heirs => at most N rows,
        # regardless of how many will-executors exist.
        dust_heirs = {}
        for wid in Will.only_valid(self.bal_window.willitems):
            heirs = self.bal_window.willitems[wid].heirs
            for hid, heir in heirs.items():
                if "DUST" in str(heir[HEIR_REAL_AMOUNT]):
                    # Keep the first dust amount seen for this heir; it is the
                    # same share in every will-executor copy of the will.
                    dust_heirs.setdefault(hid, heir[HEIR_DUST_AMOUNT])
        for hid, dust_amount in dust_heirs.items():
            self.msg_set_status(
                _("Heir {}").format(hid),
                None,
                _("{} is DUST - excluded (amount below dust limit)").format(
                    dust_amount
                ),
                self.COLOR_WARNING,
            )

        have_to_sign = bool(self._ids_to_sign())
        return have_to_sign, txs

    def _ids_to_sign(self):
        """The VALID items to sign: not COMPLETE, without the leftovers of
        deselected will-executors (#135, owner's ZIP test). Since #157 the
        rule is the engine's (:func:`bal.engine.sign.to_sign`), used by every
        "sign all".

        Owner: a will-executor selected, Check, Cancel at "Sign your will"
        (its copy stays an unsigned draft), then deselected again: the
        Check said "No change" but still asked the password and signed that
        copy, which is never sent. The #134 rule: a copy for a will-executor
        not selected now that never received the will (not PUSHED) is
        "deselected", not "to sign". It is left as it is (ignored, not
        dropped): unsigned, it exists only in this wallet and no
        will-executor holds it, so it cannot execute; a later rebuild drops
        it as a draft (#128), and if that will-executor is selected again it
        is signed and sent then. A signed or sent item is never touched.
        """
        return to_sign(self.bal_window.willitems,
                       selected=self._selected_willexecutors())

    def _build_success_report(self):
        """Mark the build step as done and list every heir in green.

        Called after a successful (re)build of the will. It:
          1. labels each inheritance transaction in Electrum's history;
          2. shows the green "Ok" result on the "Building your will" row;
          3. lists EVERY heir of the freshly built will, one per line, in green
             (the "Ok" colour), so the user can confirm at a glance who will
             inherit.

        Previously a heir name could appear in RED (it was the text of a rebuild
        exception) and the heir list was only shown on the "all clean" path,
        which is why after changing/deleting an heir the user saw a single red
        heir name and no green list. This helper is now used both on the clean
        path and on the "will was rebuilt and needs signing" path, so the green
        heir list is always shown.

        Internal will-executor pseudo-heirs (reserved ``w!ll3x3c"`` prefix) are
        skipped, exactly as the core coherence check does. A set keeps the list
        unique even when an heir appears in several transactions.
        """
        # Label shown in Electrum's History tab for inheritance txs: the
        # VALID items (CHANGELOG #119: bal.engine.build).
        build.label_rebuilt(self.bal_window, scope="valid")
        # Keep the plugin's stored delivery date in sync with the (possibly
        # auto-anticipated) transactions, otherwise the next Check would wrongly
        # ask to invalidate. See _sync_locktime_to_built_txs for the full why.
        self._sync_locktime_to_built_txs()
        self.msg_set_building(self.msg_ok())
        # List EACH heir on its OWN line, green + bold (owner request: revert the
        # one-line "Heirs: a, b, c" form of v0.4.6). Heir names can be long, and
        # now that the report area scrolls (allegato1) there is no need to cram
        # them onto a single line. We de-duplicate (an heir can appear in several
        # will-executor transactions) and skip the internal will-executor
        # pseudo-heirs (reserved ``w!ll3x3c"`` prefix), exactly as before.
        shown_heirs = set()
        for wid in Will.only_valid(self.bal_window.willitems):
            for hname in self.bal_window.willitems[wid].heirs:
                if str(hname)[:9] == 'w!ll3x3c"':
                    continue
                if hname in shown_heirs:
                    continue
                shown_heirs.add(hname)
                self.msg_set_status(_("Heir"), None, str(hname), self.COLOR_OK)

    def _in_force_kept_report(self):
        """The rows when the build only gave back the will in force (#128).

        The check found that the will prepared earlier (an unsigned draft)
        differs from the settings and said so ("The delivery date changed:
        preparing a new will."); the build then found that the will in
        force, already signed and sent, is exactly that will
        (``Will.only_brought_back``): nothing to sign, nothing to send. The
        "Checking your will" row is rewritten to "Ok" and the "Building your
        will" row says there was nothing to do, so the dialog does not end
        on a sentence about a new will that does not exist. These are the
        rows of a coherent will (existing msgids): the report's result
        already says "Your will is in force" with the delivery date, and a
        text on the check row would also be listed among the changes. The
        labels and the stored date are kept in step as after any build
        (:meth:`_build_success_report`).
        """
        build.label_rebuilt(self.bal_window, scope="valid")
        self._sync_locktime_to_built_txs()
        self.msg_set_checking(self.msg_ok())
        self.msg_set_building(_("Nothing to do"))

    def _sync_locktime_to_built_txs(self):
        """Align the plugin's stored delivery date with the built transactions.

        WHY this is needed (bug reported by the owner):
        When the will is rebuilt while it still spends the same coins as a
        previous one (e.g. after deleting an heir WITHOUT changing the date),
        the core engine AUTOMATICALLY anticipates the transaction locktime by
        one day (see Will.check_anticipate / Util.anticipate_locktime). This is
        correct and required so the new transaction can be mined BEFORE the old
        one it replaces.

        However the plugin's own stored delivery date (the ``locktime`` of
        the will settings) was NOT updated and stayed at the original
        date. On the next Check the plugin compared the stored date (original)
        with the transaction locktime (original minus one day) and, since
        stored > tx, mistook the automatic anticipation for a user POSTPONE,
        wrongly asking to invalidate the will.

        Fix: after a (re)build, set the stored delivery date to the MINIMUM
        locktime among the valid built transactions. We only ever move the date
        EARLIER (anticipation): if the minimum is not strictly below the current
        stored date we leave it untouched, so a genuine user-chosen postpone is
        never silently overwritten. The owner confirmed that, when several
        transactions carry different locktimes, taking the minimum is the
        desired behaviour, and that the date shown in the panel/wizard must
        reflect this anticipated date (so the calendar .ics also uses it).

        Since CHANGELOG #123 the stored date is always an absolute date of
        THIS wallet (the period form "30d"/"1y" is refused, the Raw editor
        is hidden), kept in the wallet's own settings
        (``wallet.db.get_dict("will_settings")``); Electrum's global
        ``bal_will_settings`` is never written.

        We route the update through BalWindow.update_setting_widgets, which is
        the single place that (1) stores the value through the one validated
        write (``bal.engine.context.set_will_setting``) and (2) refreshes the
        date fields of both tabs, so the visible date and the .ics export stay
        consistent. This runs on the dialog's worker thread: there only the
        value is written, and the fields are refreshed on the GUI thread
        (rule I5 of #123). The wallet file is saved by Electrum.
        """
        # The rule itself lives in bal.core (CHANGELOG #110) and the steps in
        # bal.engine.build (CHANGELOG #119), shared with the automatic
        # rebuild and the command line; this dialog stores the date its way.

        def store(field, value):
            _logger.debug(f"sync delivery date to built tx locktime: -> {value}")
            # Remember that we anticipated the date, so the later sign
            # prompt can explain WHY signing is needed
            # (see on_success_phase1).
            self._date_was_anticipated = True
            # update_setting_widgets stores the value, persists it and
            # refreshes the date widgets in all panels/wizard (the .ics
            # calendar too).
            self.bal_window.update_setting_widgets(value, field, update_all=True)

        build.sync_delivery_date(self.bal_window, store)
        # A relative "Check Alive" threshold is also PRESERVED: it is anchored
        # on every check by ``resolve_date_to_check`` / ``resolve_guard_threshold``
        # (negative counts back from the delivery, positive forward from the
        # inheritance creation date), so it never needs freezing to an absolute
        # date here.

    def on_accept(self):
        try:
            self.bal_window.update_all()
        except Exception as e:
            import traceback
            _logger.error(f"NoneType_catch on_accept: {e}\n{traceback.format_exc()}")
        pass

    def on_accept_phase2(self):
        try:
            self.bal_window.update_all()
        except Exception as e:
            import traceback
            _logger.error(f"NoneType_catch on_accept_phase2: {e}\n{traceback.format_exc()}")
        pass

    def on_error_push(self):
        pass

    def wait(self, secs):
        wait_row = None
        for i in range(secs, 0, -1):
            if self._stopping:
                return
            wait_row = self.msg_edit_row(_("Please wait {}secs").format(i), wait_row)
            time.sleep(1)
        self.msg_del_row(wait_row)

    def loop_broadcast_invalidating(self, tx):
        """Send the signed invalidation *tx* and show the result in its row.

        The steps (wallet and network information, the History label set
        BEFORE broadcasting, the 120 s broadcast; task #21: the txid comes
        from *tx*, never from the broadcast's return value) are shared with
        the automatic invalidation since #153:
        :func:`bal.engine.invalidate.broadcast_invalidation`. A broadcast
        error is shown in the "Invalidating old will" row, as before.
        """
        if self._stopping:
            return
        self.msg_set_invalidating(_("Broadcasting"))
        try:
            txid = broadcast_invalidation(self.bal_window.wallet, self.network, tx)
            if not txid:
                _logger.debug(f"invalidate tx has no txid: {tx}")
            self.msg_set_invalidating(self.msg_ok())

        except TxBroadcastError as e:
            _logger.error(f"fail to broadcast transaction:{e}")
            msg = e.get_message_for_gui()
            self.msg_set_invalidating(self.msg_error(msg))
        except BestEffortRequestFailed as e:
            self.msg_set_invalidating(self.msg_error(e))

    def loop_push(self):
        """The Check's "Sending" step: send the will to its will-executors.

        Broadcast is "one-shot" (Group B / B2 follow-up): each selected
        will-executor is contacted ONCE. Transactions that are broadcast
        successfully become PUSHED; transactions whose server fails or times
        out are left as PUSH_FAIL and simply skipped - they are NOT retried
        automatically. A dead will-executor could otherwise never answer and
        make the plugin retry forever. The user can broadcast a failed
        transaction manually later with the "Broadcast" button. PUSHED
        transactions are never re-sent on a subsequent run.

        The sending is the menu's and the command line's too since #155
        (:mod:`bal.engine.push`); this method keeps the rows, the counter
        and the report. A will-executor excluded before sending (current
        record over the maximum fee or not valid, D2) now gets a warning
        row "Will-Executor excluded" with the reason; before it was
        skipped silently.
        """
        if self._stopping:
            return
        self.msg_set_pushing(_("Broadcasting"))
        try:
            # Only the will-executors the user actually selected, with a
            # still valid record (address, fee within the maximum).
            selected, excluded = select_push(
                self.bal_window.willitems,
                self.bal_window.willexecutors,
                max_fee=self.bal_window.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
                dust=self.bal_window.window.wallet.dust_threshold(),
            )
            for url, reason in excluded.items():
                self.msg_set_status(
                    _("Will-Executor excluded"),
                    None,
                    "{}: {}".format(
                        urlparse(url).hostname or url,
                        _("fee above the maximum")
                        if reason == EXCLUDED_FEE
                        else _("not a valid will-executor"),
                    ),
                    self.COLOR_WARNING,
                )

            total = len(selected)
            done = {"count": 0}
            # CHANGELOG #85: the will-executors that have not answered yet,
            # named on the status line (owner: the dialog waited up to 20s
            # and looked broken without saying who it was waiting for).
            pending = set(selected)

            deadline = Willexecutors.PUSH_GLOBAL_DEADLINE

            def _status_line():
                # e.g. "Broadcasting 2/3 (5s / 20s) — waiting for: we.x.org".
                # The "/ 20s" makes the maximum wait explicit, so the user knows
                # the wizard will proceed by then (the global deadline) instead
                # of wondering how long the counter will keep climbing.
                elapsed = min(int(time.time() - push_start), deadline)
                # #84b: the same counter under the "Sending" step, and who is
                # still awaited in the result sentence.
                report = self._report()
                report.set_step(
                    "send", "run",
                    "{}/{} · {} s / {} s".format(
                        done["count"], total, elapsed, deadline
                    ),
                )
                waiting = sorted(urlparse(u).hostname or u for u in pending)
                sentence = html.escape(
                    _("Sending your will to the will-executors…")
                )
                if waiting:
                    sentence += " " + html.escape(
                        _("waiting for: {}").format(", ".join(waiting[:3]))
                    )
                report.set_result("run", sentence)
                return self.push_status_text(
                    done["count"], total, elapsed, deadline, pending,
                )

            def on_result(url, record, outcome, exc):
                # Runs from a worker thread.  Do only thread-safe book-keeping
                # plus a signal-based UI update (msg_edit_row emits a pyqtSignal,
                # which is marshalled to the GUI thread).
                pending.discard(url)
                if outcome == TIMEOUT:
                    # The global deadline elapsed before this server answered
                    # (one-shot: no automatic retry; PUSH_FAIL already set).
                    self._report().set_executor(url, "timeout", deadline)
                    self._log_only(
                        "{} : {}".format(url, self.msg_error(_("Timeout - no answer")))
                    )
                    return
                done["count"] += 1
                # #84b: the executor's own line in the will-executors box.
                self._report().set_executor(
                    url,
                    "received" if outcome in (RECEIVED, ALREADY_PRESENT) else "failed",
                )
                # Show the per-server result (Ok/Ko) in bold + color so the
                # outcome stands out, keeping the server URL in normal weight.
                result = (
                    self.msg_ok(_("Ok")) if outcome == RECEIVED else self.msg_error(_("Ko"))
                )
                self._log_only("{} : {}".format(url, result))
                self.msg_set_pushing(_status_line())

            check_rows = {}

            def on_check_start(url, wid):
                """An "already present" copy is being checked: its row."""
                check_rows[wid] = self._log_only(
                    "{} : <b>{}</b>".format(
                        _("checking {} - {}").format(
                            self.bal_window.willitems[wid].we["url"], wid
                        ),
                        _("Waiting"),
                    )
                )

            def on_check_done(url, wid, res):
                """Its answer: the report line and the row, in colour."""
                checked = self.bal_window.willitems[wid].get_status("CHECKED")
                if res is Willexecutors.NO_ANSWER:
                    # #147: it has the will, it did not answer the check.
                    self._report().set_executor(
                        url, "no_check_answer", Willexecutors.CHECK_TIMEOUT
                    )
                else:
                    self._report().set_executor(
                        url, "verified" if checked else "failed"
                    )
                result = self.msg_ok(checked) if checked else self.msg_error(checked)
                self._log_only(
                    "{} : {}".format(
                        _("checked {} - {}").format(
                            self.bal_window.willitems[wid].we["url"], wid
                        ),
                        result,
                    ),
                    check_rows.get(wid),
                )

            if self._stopping:
                return
            # Push to all selected will-executors in parallel: a slow/dead
            # server no longer blocks the others, and a global deadline
            # guarantees the wizard always proceeds even if a server never
            # answers.
            push_start = time.time()
            for url in selected:
                self._report().set_executor(url, "waiting")
            self.msg_set_pushing(_status_line())

            # Refresh the elapsed-seconds counter while the (blocking) parallel
            # push runs, so the user sees time advancing instead of a frozen
            # "Trasmissione".  The tick is driven from THIS (Task) thread by
            # push_transactions_parallel, the same thread that drives on_each, so
            # the pyqtSignal repaint is reliable (a separate heartbeat thread's
            # signal emissions were not being marshalled and never repainted).
            def on_tick():
                if self._stopping:
                    return
                self.msg_set_pushing(_status_line())

            # The final summary line comes before the "already present"
            # checks, as before: they are done after the parallel push.
            def on_check_start_first(url, wid):
                if not summary_done["done"]:
                    summary_done["done"] = True
                    self.msg_set_pushing(
                        "{}/{} ({}s)".format(done["count"], total,
                                             int(time.time() - push_start))
                    )
                on_check_start(url, wid)

            summary_done = {"done": False}
            self.bal_plugin = self.bal_window.bal_plugin
            push_will(
                self.bal_window.willitems,
                selected,
                on_result=on_result,
                on_tick=on_tick,
                should_stop=lambda: self._stopping,
                on_check_start=on_check_start_first,
                on_check_done=on_check_done,
            )
            if not summary_done["done"]:
                # Final summary line with the total elapsed time.
                self.msg_set_pushing(
                    "{}/{} ({}s)".format(done["count"], total,
                                         int(time.time() - push_start))
                )

            # One-shot broadcast: we deliberately do NOT raise/retry when some
            # will-executors failed. Their transactions stay PUSH_FAIL and are
            # left for the user to broadcast manually. This prevents an endless
            # retry loop against a will-executor that may never answer.

        except Exception as e:
            # Only genuine, unexpected errors reach here now (not the old
            # "retry" signal). Report the error; do not loop.
            self.msg_set_pushing(self.msg_error(e))
            self.wait(10)
            if not self._stopping:
                pass

    def loop_verify(self):
        """The Verification step: do the will-executors hold the will? (#84b-2)

        Every VALID item that was sent and is not verified yet
        (``Will.needs_server_check``, the items the Check button verified)
        is looked up on its will-executor. All the executors are asked at
        once, with the same 20 s limit as the sending, and each answer is
        recorded (the executor must hold exactly the tx that was sent,
        otherwise the item is marked failed and will be sent again). #156:
        the choice and the asking are the engine's
        (:func:`bal.engine.verify.verify_targets` /
        :func:`~bal.engine.verify.verify_will`), shared with the right
        click "check" and the command line; this step keeps its display.

        WHY here: the verification used to run after this dialog had closed,
        in a separate "Check Transaction" window, and only for the Check
        button and the wizard; at wallet close and after a renewal it did not
        run at all. Now it is the last step of every run of phase 2, and the
        owner sees it executor by executor: the step counter, who is still
        awaited, then "received and verified" or "not received". It only
        reads from the executors; nothing is sent to them.
        """
        if self._stopping:
            return
        targets, _skipped = verify_targets(self.bal_window.willitems)
        items = {wid: self.bal_window.willitems[wid] for wid, _url in targets}
        if not items:
            # Nothing sent that is not verified yet: the step then shows what
            # the items already say (see _finish_report).
            return
        left = {}       # url -> the ids it has not answered for yet
        for wid, w in items.items():
            left.setdefault(w.we["url"], set()).add(wid)
        failed = set()  # urls that did not confirm every one of their items
        # #147: urls that did not answer in time for an item (still sent).
        unanswered = set()
        total = len(left)
        deadline = Willexecutors.CHECK_GLOBAL_DEADLINE
        report = self._report()
        start = time.time()

        def _progress():
            # The log row first, then the bar and the sentence: the row's own
            # report update must not overwrite them (see _report_status).
            elapsed = min(int(time.time() - start), deadline)
            done = sum(1 for wids in left.values() if not wids)
            pending = [url for url, wids in left.items() if wids]
            self.msg_set_verifying(
                self.verify_status_text(done, total, elapsed, deadline, pending)
            )
            report.set_step(
                "verify", "run",
                "{}/{} · {} s / {} s".format(done, total, elapsed, deadline),
            )
            sentence = html.escape(
                _("Verifying your will with the will-executors…")
            )
            hosts = sorted(urlparse(u).hostname or u for u in pending)
            if hosts:
                sentence += " " + html.escape(
                    _("waiting for: {}").format(", ".join(hosts[:3]))
                )
            report.set_result("run", sentence)
            self.updatemessage.emit()

        def _answered(wid, url, res, outcome, timed_out):
            """Show one answer, already recorded by the engine, live.

            #147: no answer in time (``NO_ANSWER``, after the 15 s of the
            request or the 20 s of the whole step) leaves the item sent, and
            the executor reads "received, no answer to the check", not "not
            received".
            """
            # Called from this (task) thread by verify_will.
            no_answer = outcome == NO_ANSWER
            ok = not no_answer and outcome != FAILED
            if no_answer:
                unanswered.add(url)
            elif not ok:
                failed.add(url)
            left[url].discard(wid)
            if timed_out or not left[url]:
                if url in failed:
                    report.set_executor(url, "failed")
                elif url in unanswered:
                    report.set_executor(
                        url, "no_check_answer",
                        deadline if timed_out else Willexecutors.CHECK_TIMEOUT,
                    )
                else:
                    report.set_executor(url, "verified")
            if ok:
                result = self.msg_ok(_("Ok"))
            elif no_answer:
                result = self.msg_error(_("Timeout - no answer"))
            else:
                result = self.msg_error(_("Ko"))
            self._log_only(
                "{} : {}".format(_("checked {} - {}").format(url, wid), result)
            )
            _progress()

        def on_tick():
            if not self._stopping:
                _progress()

        self.msg_set_verifying()
        for url in left:
            report.set_executor(url, "verifying")
        _progress()
        verify_will(
            self.bal_window.willitems, targets,
            on_answer=_answered, on_tick=on_tick,
        )
        # The final word under the step is counted over every will-executor
        # of the will in _finish_report.
        if failed:
            self.msg_set_verifying(self.msg_error(_("Ko")))
        elif unanswered:
            self.msg_set_verifying(self.msg_warning(_("Timeout - no answer")))
        else:
            self.msg_set_verifying(self.msg_ok())

    def invalidate_task(self, password, bal_window, tx):
        if self._stopping:
            return
        _logger.debug(f"invalidate tx: {tx}")
        # fee_per_byte = bal_window.will_settings.get("baltx_fees", 1)
        tx = self.bal_window.wallet.sign_transaction(tx, password)
        if not tx or not tx.is_complete():
            # #134: not signed, so certainly not broadcast: a postpone is
            # cancelled as at the password's Cancel (the date goes back).
            self._revert_cancelled_postpone()
        try:
            if tx:
                if tx.is_complete():
                    self.loop_broadcast_invalidating(tx)
                    # Wait 10 seconds (was 5) AFTER broadcasting the
                    # invalidation so Electrum's wallet/network has time to see
                    # the new transaction before we re-run phase 1. Without this
                    # pause the immediate re-check still detected the old
                    # (not-yet-invalidated) will and re-prompted to invalidate,
                    # producing the reported loop. This runs in the worker
                    # thread, so the GUI is not frozen by the sleep.
                    self.wait(10)
                    # Remember that we just broadcast an invalidation. If the
                    # next phase-1 re-check STILL reports a postpone (because
                    # Electrum has not registered the tx yet), we stop with a
                    # clear message instead of looping (see on_success_phase1).
                    self._invalidation_broadcast = True
                else:
                    raise Exception("tx not complete")
            else:
                raise Exception("not tx")
        except Exception as e:
            (f"exception:{e}")
            self.msg_set_invalidating(_("Error: {}").format(e))
            raise Exception("Impossible to sign") from e

    @staticmethod
    def push_status_text(done, total, elapsed, deadline, pending=()):
        """The "Broadcasting" status line, naming who is still awaited (#85).

        "Broadcasting 0/1 (3s / 20s) — waiting for: we.example.org". Only the
        host name is shown, at most three of them ("(+N more)" for the
        rest), so the line stays short with many will-executors.
        """
        return BalBuildWillDialog._progress_text(
            _("Broadcasting"), done, total, elapsed, deadline, pending
        )

    @staticmethod
    def verify_status_text(done, total, elapsed, deadline, pending=()):
        """The "Verifying" status line (#84b-2), shaped like the sending one.

        "Verifying 1/2 (4s / 20s) — waiting for: we.example.org".
        """
        return BalBuildWillDialog._progress_text(
            _("Verifying"), done, total, elapsed, deadline, pending
        )

    @staticmethod
    def _progress_text(verb, done, total, elapsed, deadline, pending=()):
        """``verb done/total (elapsed / deadline)`` and who is still awaited."""
        text = "{} {}/{} ({}s / {}s)".format(verb, done, total, elapsed, deadline)
        hosts = sorted(urlparse(u).hostname or u for u in pending)
        if hosts:
            names = ", ".join(hosts[:3])
            if len(hosts) > 3:
                names += " " + _("(+{} more)").format(len(hosts) - 3)
            text += " — " + _("waiting for: {}").format(names)
        return text

    def _show_what_changes(self, when):
        """Fill and draw the "What changes" box (#135). Never raises.

        ``"sign"``: the will in force against the will as it will be once
        signed (the prepared drafts plus the copies in force they leave in
        place: ``engine.context.prepared_items``);
        ``"invalidate"`` (a postpone, nothing built yet): against what the
        settings will give (heirs, date, will-executors selected now, fee).
        The will in force is the #128 notion
        (``engine.context.in_force_items``); with none (a first will) there
        is no box. Informative only: the flow does not change.
        """
        try:
            bw = self.bal_window
            selected = self._selected_willexecutors()
            in_force = engine_context.in_force_items(bw)
            old = will_changes.side_from_items(in_force, selected)
            if when == "sign":
                new = will_changes.side_from_items(
                    engine_context.prepared_items(bw, in_force), selected)
            else:
                settings = bw.will_settings or {}
                new = will_changes.side_from_settings(
                    bw.heirs or {}, settings.get("locktime"), selected or [],
                    settings.get("baltx_fees"))
            changes = will_changes.with_renames(
                will_changes.will_changes(old, new), self._heir_renames())
            # #135: a rename is already saved; the sentence about Cancel
            # must not say it is undone.
            self._report().set_changes(will_changes.change_rows(changes), when,
                                       renamed=will_changes.has_renames(changes))
            self.msg_update()
        except Exception as e:
            _logger.error(f"what changes not shown: {e!r}")

    def _amounts_note(self, text, info):
        """The shares recalculated in proportion (#135, owner).

        Before, an "Attention" box at every Check and window (e.g. "the
        heirs' percentages add up to 130%…"), alarming when nothing
        changed. Now: the explanation stays in "Details (log)"; the report
        shows it as a box (calm title) only when the shares change compared
        with the will in force (:meth:`_shares_change`) or there is no will
        in force yet; otherwise one quiet grey line by the heirs
        ("Shares recalculated in proportion: alice 38.5% · …"). Never
        raises.
        """
        self._log_only(self.msg_alert(text))
        try:
            prominent = self._shares_change()
            if prominent and info and info.get("kind") == "percent" and not info.get("with_fixed"):
                # A calmer explanation than the log's (owner).
                note = _("The percentages you wrote add up to {total}. The "
                         "will always spends the whole balance, so each heir "
                         "receives a share in proportion: {shares}.").format(
                    total=self._shares_line({"shares": [("", info["total"])]}).strip(),
                    shares=self._shares_line(info))
            elif prominent:
                note = text
            else:
                note = _("Shares recalculated in proportion: {}").format(
                    self._shares_line(info))
            self._report().set_amounts_note(note, prominent)
        except Exception as e:
            _logger.error(f"amounts note not shown: {e!r}")

    def _quiet_shares_line(self, report):
        """The grey "Shares recalculated in proportion: …" when the written
        percentages do not add up to 100 and nothing said so yet (#135).

        Owner's ZIP test: a Check with only a rename, heirs at 130%, showed
        no line at all (the amounts check did not explain it on that pass).
        Read from the Heirs tab (``will_changes.real_shares_text``: the
        written percentages in proportion; nothing with fixed amounts).
        """
        try:
            if report.snapshot().get("amounts_note"):
                return
            shares = will_changes.real_shares_text(self.bal_window.heirs or {})
            if shares and shares[1]:
                report.set_amounts_note(
                    _("Shares recalculated in proportion: {}").format(shares[0]), False)
        except Exception as e:
            _logger.error(f"shares line not shown: {e!r}")

    def _shares_change(self):
        """True when the heirs' shares as written ({address: amount}) differ
        from the will in force's, or no will is in force (#135).

        The names do not count (a rename is not a change of shares), nor
        the will-executors or the date.
        """
        in_force = engine_context.in_force_items(self.bal_window)
        # THE will in force: the copies with the earliest delivery (#128);
        # an older will still held (a later date) is not the reference.
        dated = [w for w in in_force.values() if w.heirs]
        if not dated:
            return True
        earliest = min(int(w.tx.locktime) for w in dated)
        first = next(w for w in dated if int(w.tx.locktime) == earliest)

        def written(heirs):
            out = {}
            for name, heir in (heirs or {}).items():
                if str(name).startswith('w!ll3x3c"'):
                    continue
                out[str(heir[0])] = str(heir[1])
            return out

        return written(first.heirs) != written(self.bal_window.heirs)

    @staticmethod
    def _shares_line(info):
        """``alice 38.5% · bob 23.1%`` of ``Will.describe_amounts`` (one
        decimal, none when whole, the language's decimal comma)."""
        from ...i18n import number_separators
        decimal, _thousands = number_separators()

        def pct(x):
            text = "{:.1f}".format(x)
            if text.endswith(".0"):
                text = text[:-2]
            return text.replace(".", decimal) + "%"

        return " · ".join("{} {}".format(name, pct(share))
                          for name, share in (info or {}).get("shares") or [])

    def _heir_renames(self):
        """``[(old, new)]``: the heirs only renamed by this Check (#135).

        Kept by :meth:`_report_renames` from the FIRST check of the run:
        ``engine.context.check_will`` sets ``heir_renames`` at every call,
        and the check after the rebuild finds nothing more to rename.
        """
        renames = getattr(self, "_renames_seen", None)
        return list(renames) if isinstance(renames, list) else []

    def _report_renames(self):
        """The heirs only renamed by this Check (#135).

        A log line each (the technical "Heir renamed: … only the name
        changes" stays in "Details (log)"), and the "What changes" box with
        its Heirs row "renamed: luigi → luigi new (only the name changes)"
        when a will is in force: the one box of all the changes (owner).
        A signature or a postpone later in this Check redraws the box with
        every change (:meth:`_show_what_changes`). Never raises; no redraw
        here (worker thread): the next status row redraws.
        """
        try:
            renames = getattr(self.bal_window, "heir_renames", None)
            renames = list(renames) if isinstance(renames, list) else []
            if renames:
                self._renames_seen = renames
            for old, new in renames:
                self._report().put("changes", ("renamed", old), _(
                    "Heir renamed: {old} → {new}: only the name changes, the "
                    "will stays the same.").format(old=old, new=new))
            if renames and engine_context.in_force_items(self.bal_window):
                rows = will_changes.change_rows(
                    will_changes.with_renames([], renames))
                self._report().set_changes(rows, "check")
        except Exception as e:
            _logger.error(f"renames not reported: {e!r}")

    def _add_invalidation_buttons(self):
        """The row "Cancel" / "Invalidate now" of the invalidation step (#135).

        The run waits here for the owner (wallet close included: the owner
        is there). "Invalidate now" asks the password and invalidates
        (:meth:`_invalidate_now`); "Cancel", or closing the window, does not
        invalidate (:meth:`_invalidation_cancelled`, the #134 option B).
        """
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(lambda _c=False: self._invalidation_cancelled())
        # Lazy import to avoid a dialogs<->lists import cycle. The look of
        # the wizard's primary button (#134).
        from .lists import FRAMED_BUTTON_STYLE

        go = QPushButton(_("Invalidate now"))
        go.setDefault(True)
        go.setStyleSheet(FRAMED_BUTTON_STYLE)
        go.clicked.connect(lambda _c=False: self._invalidate_now())
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(go)
        self._invalidation_layout = row
        self.vbox.addLayout(row)
        go.setFocus()
        self._fit_height(shrink=False)

    def _remove_invalidation_buttons(self):
        """Take the "Cancel" / "Invalidate now" row away (answered)."""
        layout = getattr(self, "_invalidation_layout", None)
        if layout is None:
            return
        self._invalidation_layout = None
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.vbox.removeItem(layout)

    def _invalidate_now(self):
        """"Invalidate now": the password, then the invalidation (#135).

        The steps that followed the box before (the password "Invalidate
        your old will", then ``invalidate_task`` on the worker thread),
        unchanged; Cancel at the password is a cancelled invalidation.
        """
        tx = getattr(self, "_pending_invalidation", None)
        self._pending_invalidation = None
        self._remove_invalidation_buttons()
        if self._stopping:
            return
        password = self.bal_window.get_wallet_password(
            _("Invalidate your old will"), parent=self
        )
        if password is False:
            self._invalidation_cancelled(tx)
            return
        # Going on: the box is about a transaction no longer waiting.
        self._clear_invalidation_preview()
        self.thread.add(
            partial(self.invalidate_task, password, self.bal_window, tx),
            on_success=self.on_success_invalidate,
            on_done=self.on_accept,
            on_error=self.on_error,
        )

    def _invalidation_cancelled(self, tx=None):
        """The invalidation was NOT done (Cancel, at the step or at the
        password, or the window closed): as the Cancel at the password
        before (#134 option B, the later date goes back).

        We must NOT call self.wait(3) here: this runs in the GUI thread, and
        wait()'s time.sleep() would freeze the window (the black, undrawn
        rectangle of an old bug). "Aborted" and a non-blocking "Close"
        instead. The box stays, with "Show the transaction" still working:
        the transaction was never signed (#135, owner).
        """
        if tx is None:
            tx = getattr(self, "_pending_invalidation", None)
        self._pending_invalidation = None
        self._remove_invalidation_buttons()
        self._invalidation_tx = tx
        self.msg_set_invalidating(_("Aborted"))
        self._revert_cancelled_postpone()
        if not self._stopping:
            self._add_close_button()

    def _show_invalidation_preview(self, tx):
        """Fill and draw the box "Invalidation of the old will" (#135 follow-up).

        Owner (ZIP test): the Check asked the password "Invalidate your old
        will" without showing what is invalidated ("Tools > Invalidate"
        shows Electrum's transaction window first). Now the box says, from
        the built (unsigned) ``tx`` itself: the amount moved and that it
        goes to a new address of THIS wallet, the network fee and its rate,
        and the wills it makes useless
        (``bal.core.invalidation_preview``); its "Show the transaction"
        button opens Electrum's transaction window on a copy of it
        (:meth:`_show_invalidation_tx`). Nothing is stored; what is signed
        and broadcast does not change. Never raises.
        """
        try:
            bw = self.bal_window
            facts = invalidation_preview.invalidation_facts(
                tx, bw.willitems, getattr(bw, "wallet", None))
            self._invalidation_tx = tx if facts else None
            view = getattr(self, "report_view", None)
            if view is not None:
                view.format_fiat = self._format_fiat
                view.on_show_invalidation = self._show_invalidation_tx if facts else None
            self._report().set_invalidation(facts)
            self.msg_update()
        except Exception as e:
            _logger.error(f"invalidation preview not shown: {e!r}")

    def _clear_invalidation_preview(self):
        """Remove the invalidation box once its password was answered."""
        try:
            self._invalidation_tx = None
            view = getattr(self, "report_view", None)
            if view is not None:
                view.on_show_invalidation = None
            self._report().set_invalidation(None)
            self.msg_update()
        except Exception as e:
            _logger.error(f"invalidation preview not cleared: {e!r}")

    def _show_invalidation_tx(self):
        """Open Electrum's transaction window on the invalidation (read-only).

        A COPY of the transaction about to be signed, so nothing done in
        that window can change it; its Sign and Broadcast buttons are
        hidden: the invalidation is signed only through the password step
        of this window (#135 follow-up, owner: a read-only preview).
        """
        tx = getattr(self, "_invalidation_tx", None)
        if tx is None:
            return None
        try:
            shown = copy.deepcopy(tx)
        except Exception:
            # A transaction holding wallet data (a lock) cannot be deep
            # copied: re-parse it (as task_phase2 does). Never the real one:
            # it could be signed there.
            try:
                shown = Will.get_tx_from_any(str(tx))
            except Exception as e:
                _logger.error(f"invalidation copy failed: {e!r}")
                return None
        # Electrum's TxDialog needs the Electrum main window as ``parent``
        # (it reads ``parent.config`` and ``parent.wallet``): this Check
        # window has neither, and passing it crashed (owner's ZIP test).
        # The TxDialog is a top-level window of its own (no Qt parent), so
        # this window-modal Check window does not block it.
        dialog = self.bal_window.show_transaction(tx=shown)
        if dialog is not None:
            # The TxDialog has no Qt parent, and this Check window is
            # window-modal: Qt then BLOCKS every other top-level window, so
            # the transaction window opened behind Electrum and could not
            # be moved nor closed (owner's ZIP test). A child window of
            # this Check window is never blocked by it: re-parent it here
            # (Electrum keeps its main window in ``main_window``, which is
            # what it uses for the wallet and the config), then show it
            # again in front.
            from PyQt6.QtCore import Qt
            try:
                dialog.setParent(self, Qt.WindowType.Dialog)
                dialog.setWindowModality(Qt.WindowModality.NonModal)
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
            except Exception as e:
                _logger.error(f"invalidation preview re-parent failed: {e!r}")
        for name in ("sign_button", "broadcast_button"):
            button = getattr(dialog, name, None)
            if button is not None:
                button.setVisible(False)
        return dialog

    def _format_fiat(self, satoshi):
        """``satoshi`` in fiat with Electrum's formatter ("12.34 EUR"), or
        "" without a rate or a formatter."""
        try:
            fiat = self.bal_window.window.format_fiat_and_units(satoshi)
        except Exception:
            return ""
        return fiat if isinstance(fiat, str) else ""

    def _revert_cancelled_postpone(self):
        """A postpone whose invalidation was NOT done: the date goes back (#134).

        Owner's ZIP test (option B): after "Invalidate your old will" ->
        Cancel the stored date stayed on the later one: no will item had it,
        the summary never showed it, and every Check and wallet close asked
        for the invalidation again. Now the stored date goes back to the
        date the will items really use (the prepared draft's, else the will
        in force's: ``engine.context.revert_cancelled_postpone``) and the
        result says the later date was not applied.

        Called only before any broadcast: Cancel at the invalidation's
        password, or its signing failed. Never after a broadcast, even a
        failed or unconfirmed one (the invalidation may be on its way, so
        the postpone stays; ``loop_broadcast_invalidating``). Only for a
        postpone (``_postpone_invalidation``): an expired will or a renewal
        keeps today's behaviour. Not after "Use <later date>" of the #123
        "which date?" question in this Check (``_later_by_question``: the
        owner's explicit answer stays), nor when the stored date is the date
        of a VALID will already signed or sent (``cancelled_postpone_date``:
        going back would sign that newer will at an earlier date).
        """
        if (not self._postpone_invalidation or self._postpone_reverted
                or self._later_by_question):
            return
        self._postpone_reverted = engine_context.revert_cancelled_postpone(
            self.bal_window
        )

    def on_success_invalidate(self, success):
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )

    def _stop_invalidation_pending(self):
        """Stop: the invalidation was broadcast but is not yet seen.

        The task #21 loop guard, shared with the renewal path (#79): never
        ask to invalidate twice; tell the user to press Check again once the
        invalidation transaction is confirmed.
        """
        self.thread.stop()
        self.msg_set_invalidating(self.msg_ok())
        self.bal_window.show_message(
            _(
                "Your old will has been invalidated and the "
                "transaction was broadcast.\n"
                "Electrum may need a little time to register it.\n"
                "Please wait until the invalidation transaction is "
                "confirmed, then press the Check button again to "
                "finish updating your will."
            )
        )
        self._add_close_button()

    def _ask_delivery_choice(self):
        """Ask the "which date?" question, then run phase 1 again (#123).

        Phase 1 stopped on ``Phase1Result("delivery_choice")``: an older
        setting asked for a later date than the will prepared and not yet
        signed (plan 3.3). This runs on the GUI thread (``on_success``).
        "Keep" (the default) keeps the date of the prepared will (no fee);
        "Use" stores the later date, and the next pass reads the postpone and
        asks for the invalidation of the old will (miner fee). Cancel stops:
        nothing was changed. Never at wallet close (rule I6: no question
        while the wallet closes; the window skips the check then anyway).
        """
        if self._closing:
            _logger.info("wallet close: the delivery date question is pending")
            self._add_close_button()
            return
        before = (self.bal_window.will_settings or {}).get("locktime")
        if not self.bal_window.choose_delivery_date(parent=self):
            self.msg_edit_row(
                self.msg_alert(_("The check stopped: nothing was changed."))
            )
            self._add_close_button()
            return
        # #134: "Use <later date>" (its button says the old will must be
        # invalidated, miner fee) is the owner's explicit answer of #123: a
        # Cancel at that invalidation keeps it, as before #134 (the test
        # "question use later asks for the invalidation"); only a later date
        # chosen elsewhere goes back (_revert_cancelled_postpone).
        after = (self.bal_window.will_settings or {}).get("locktime")
        try:
            self._later_by_question = int(after) > int(before)
        except (TypeError, ValueError):
            self._later_by_question = False
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )

    def on_success_phase1(self, result):
        try:
            self._on_success_phase1_body(result)
        except Exception as e:
            import traceback
            _logger.error(f"NoneType_catch on_success_phase1: {e}\n{traceback.format_exc()}")

    def _on_success_phase1_body(self, result):
        """Act on the end of phase 1 (GUI thread): ``result`` is the
        ``(have_to_sign, tx)`` pair of :meth:`task_phase1`.

        A stop code ends the dialog (or asks a question: the Check Alive
        renewal, and since #123 the "which date?" question of
        :meth:`_ask_delivery_choice`); otherwise the invalidation or phase 2
        (sign, send, verify) follows.
        """
        if self._stopping:
            return
        self.have_to_sign, tx = list(result)
        # if not tx:
        #    self.msg_edit_row(self.msg_error("Error, no tx was built"))
        #    return

        # Special signal raised by task_phase1 when the freshly rebuilt will is
        # already expired (e.g. an heir was added to an expired will). Instead of
        # running the wizard's automatic invalidation loop (which would re-check
        # before the invalidation tx reaches the mempool and loop forever), we
        # behave exactly like the "Tools -> invalidate" menu: open Electrum's
        # classic transaction dialog so the user signs and broadcasts the
        # invalidation manually, with the "Safe21 Heartbeat invalidation" label.
        # This runs in the GUI thread (on_success callback), so opening windows
        # is safe. We then stop and close the wizard.
        if self.have_to_sign == "invalidate_classic":
            self.thread.stop()
            # UNIFY INVALIDATE PROCEDURE (+ task #03):
            #
            # When an heir is added to an already-expired will, the rebuilt will
            # is itself expired and the old will must be invalidated on-chain
            # before the new one can be used.
            #
            # We make the CHECK button and the WIZARD behave IDENTICALLY:
            #   1. show a WARNING popup (no "Tools -> Invalidate" wording);
            #   2. AUTOMATICALLY open Electrum's classic transaction dialog via
            #      BalWalletWindow.invalidate_will() (the same code used by the
            #      Tools -> Invalidate menu). That path already sets the
            #      "Safe21 Heartbeat invalidation" history label - which fixes
            #      task #03 (the label was previously missing on the automatic
            #      path).
            #
            # Window-stacking note (history): opening the transaction window
            # straight from within the closing wizard used to leave it BEHIND
            # the main window on some window managers. The robust fix is to
            # close the wizard FIRST and defer the call with QTimer.singleShot
            # so it runs on the next event-loop iteration, when the wizard is
            # already gone and the transaction window becomes the front-most,
            # focused window.
            self.close()
            self.bal_window.show_message(
                _(
                    "Your will has expired and must be invalidated before it "
                    "can be rebuilt.\n"
                    "A transaction window will now open:\n"
                    "please SIGN and then BROADCAST it to invalidate your old "
                    "will.\n"
                    "After the invalidation is confirmed, press the Check "
                    "button to finish the will."
                )
            )
            # Deferred so the wizard is fully closed before the classic
            # invalidate window opens (keeps it in front, fixes the old
            # "window behind" problem).
            QTimer.singleShot(0, self.bal_window.invalidate_will)
            return

        if self.have_to_sign == "no_willexecutor":
            self._add_no_willexecutor_buttons()
            return

        if self.have_to_sign == "no_heirs":
            self._add_no_heirs_buttons()
            return

        if self.have_to_sign == "fixed_amounts_exceed":
            # Fixed amounts larger than the balance (#76): the reason is
            # already on its own red row; stop here, so the dialog does not
            # go on to "Nothing to do" / "All done: Ok".
            self._add_close_button()
            return

        if self.have_to_sign == "check_alive_before_delivery":
            # The Check Alive date is on/after the delivery (a configuration
            # error, not an expired will): do NOT invalidate, just tell the
            # user and let them close the wizard.
            self.bal_window.show_error(
                _(
                    "Check Alive must be after today and before the delivery date, please update it"
                )
            )
            self._add_close_button()
            return

        if self.have_to_sign == "check_alive_stop":
            # Passed Check Alive that cannot be renewed with one click (#79):
            # the reason is on its own red row; nothing was invalidated.
            self._add_close_button()
            return

        if self.have_to_sign == "delivery_settings_unavailable":
            # #123, fail closed: the error is on its own red row; nothing was
            # built, signed, sent or invalidated.
            self._add_close_button()
            return

        if self.have_to_sign == "delivery_choice":
            self._ask_delivery_choice()
            return

        if self.have_to_sign == "invalidation_pending":
            self._stop_invalidation_pending()
            return

        if self.have_to_sign == "check_alive_renew":
            # Passed Check Alive (#79): ask "Renew" / "Not now" unless the
            # owner already chose "Renew" (wallet open / Prepare). On Renew
            # this falls through to the normal invalidation below; the next
            # phase-1 pass then prepares the new will (the old items are
            # marked INVALIDATED first, see BalWindow.init_class_variables).
            if not getattr(self, "_renew_approved", False):
                question = check_alive_message(
                    self._check_alive_info, datetext.utc_text
                )
                if not self.bal_window.ask_renew_will(question, parent=self):
                    self.msg_edit_row(
                        self.msg_alert(
                            _("Not now: nothing was changed, your current "
                              "will stays valid.")
                        )
                    )
                    self._add_close_button()
                    return
            self._renew_approved = True
            self.have_to_sign = None

        _logger.debug("have to sign {}".format(self.have_to_sign))
        password = None
        if self.have_to_sign is None:
            _logger.debug("have to invalidate")

            # LOOP GUARD (task #21): if we already broadcast an invalidation on
            # the previous pass and phase 1 STILL reports a postpone, Electrum
            # has simply not seen the invalidation transaction yet. Re-prompting
            # to invalidate here is exactly what produced the reported endless
            # loop ("Invalidate your old will" reappearing right after signing).
            # Instead of re-prompting, STOP cleanly with a clear message and
            # tell the user to retry the Check once the invalidation confirms.
            if self._invalidation_broadcast:
                self._stop_invalidation_pending()
                return

            self.msg_set_invalidating()
            if self._postpone_invalidation:
                # #135: a postpone: nothing is built yet, so the box compares
                # the will in force with what the settings will give.
                self._show_what_changes("invalidate")
            # #135 follow-up (owner's ZIP test): what the invalidation does,
            # in the window, with "Show the transaction" and "Invalidate
            # now". The password is asked ONLY after "Invalidate now": while
            # Electrum's modal password window was on top the box could not
            # be used. Informative only: the same tx is then signed.
            self._show_invalidation_preview(tx)
            self._pending_invalidation = tx
            self._add_invalidation_buttons()
            return

        elif self.have_to_sign:
            auto_sign = (
                self.bal_window.bal_plugin.is_basic_mode()
                or self.bal_window.bal_plugin.AUTO_SIGN.get()
            )
            if not auto_sign:
                self.msg_set_signing(
                    _("Auto-sign disabled — sign manually")
                )
                self.have_to_sign = False
            else:
                # If the will was rebuilt with an automatically anticipated
                # delivery date, explain WHY we are now asking to sign:
                # otherwise the sign prompt appears with no reason (owner
                # feedback). The note is shown on the "Building your will"
                # row (orange warning) before the modal password prompt
                # opens, so the user can read it.
                if self._date_was_anticipated:
                    self.msg_set_building(
                        "<b>{}</b>".format(
                            _(
                                "The delivery date was automatically moved "
                                "one day earlier so the updated will can "
                                "correctly replace the previous one.\n"
                                "Please sign (and broadcast) to confirm "
                                "the change."
                            )
                        )
                    )
                # #135: what the new will changes, drawn before the modal
                # password window opens.
                self._show_what_changes("sign")
                # #84b: the Signature step is running while Electrum's
                # password window is open (it stayed grey before).
                self.msg_set_signing()
                password = self.bal_window.get_wallet_password(
                    _("Sign your will"), parent=self
                )
                if password is False:
                    self.msg_set_signing(_("Aborted"))
        else:
            self.msg_set_signing(_("Nothing to do"))
        self.thread.add(
            partial(self.task_phase2, password),
            on_success=self.on_success_phase2,
            on_done=self.on_accept_phase2,
            on_error=self.on_error_phase2,
        )
        return

    def on_success_phase2(self, arg=False):
        self.thread.stop()
        self.bal_window.save_willitems()
        # After the whole check/sign/broadcast cycle, keep the wallet's local
        # history in sync with the current will state (save the still "New"
        # incomplete txs, remove the now-complete ones).
        try:
            self.bal_window._save_will_to_history()
        except Exception as e:
            _logger.error(f"save_will_to_history after phase2 failed: {e}")
        self.msg_edit_row(_("Finished"))
        # Instead of auto-closing after a countdown, let the user decide when to
        # dismiss the dialog: they can read the full "Building Will" report at
        # their own pace and then press "Close".  This runs in the GUI thread
        # (on_success callback) so building the button here is safe.
        self._add_close_button()

    def _add_close_button(self):
        """Add a right-aligned "Close" button and a "Calendar" dropdown button.

        Replaces the old automatic countdown (self.wait(5) + self.close()).
        Guarded so it is only built once even if called again.
        """
        if getattr(self, "_close_button", None) is not None:
            return
        # CHANGELOG #93: the run is over, "Exclude" / "Retry" may be offered.
        self._set_report_actions(True)
        self._finish_report()

        calendar_button = BalCalendarButton(self.bal_window, self._ics_provider)
        calendar_button.setText(_("Calendar"))
        # CHANGELOG #92: the inheritance plan report, as it is after this run.
        # "Will report", not "Report": Electrum's catalog wins (bal.i18n) and
        # a bare "Report" can mean "report a bug" there.
        report_button = QPushButton(_("Will report"))
        report_button.setMenu(report_menu(self.bal_window, self))

        self._close_button = QPushButton(_("Close"))
        self._close_button.clicked.connect(self._on_close_clicked)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(report_button)
        button_row.addWidget(calendar_button)
        button_row.addWidget(self._close_button)
        self._add_closing_note(button_row)
        self._close_layout = button_row
        self.vbox.addLayout(button_row)
        self._close_button.setFocus()
        # CHANGELOG #96: the run is over, the dialog fits its content (it
        # may have grown while the run showed more, e.g. the log). Qt shows
        # the new widgets of a visible dialog only later (queued), so the
        # fit would not count this row: show them now, unless hidden on
        # purpose.
        for i in range(button_row.count()):
            widget = button_row.itemAt(i).widget()
            if widget is not None and not widget.testAttribute(
                Qt.WidgetAttribute.WA_WState_ExplicitShowHide
            ):
                widget.show()
        self._fit_height(shrink=True)
        # Safety net: once Qt has processed the queued events (widgets shown,
        # layout done) measure again, so nothing is left hidden.
        QTimer.singleShot(0, lambda: self._fit_height(shrink=True))

    # ------------------------------------------------------------------ #
    # A will-executor that did not get the will (CHANGELOG #93)
    # ------------------------------------------------------------------ #

    def _set_report_actions(self, enabled):
        """Offer (or stop offering) "Exclude", "Retry" and (#134) "Open
        Will-Executors" in the report."""
        view = getattr(self, "report_view", None)
        if view is None:
            return
        view.on_exclude = self._exclude_executor if enabled else None
        view.on_retry = self._retry_check if enabled else None
        view.on_open_executors = self._open_executors_window if enabled else None

    def _selected_willexecutors(self):
        """The URLs of the will-executors selected now (#134), or None.

        ``BalWindow.selected_willexecutors``; anything that is not a set
        (a window built another way, a test double) means "no rule".
        """
        try:
            selected = self.bal_window.selected_willexecutors()
        except Exception:
            return None
        return selected if isinstance(selected, (set, frozenset)) else None

    def _open_executors_window(self):
        """"Open Will-Executors" (#134): the Tools window; when it closes,
        the rows and the Will tab summary follow the selection (a
        will-executor selected again is "to send", Send pushes it)."""
        self.bal_window.show_willexecutor_dialog(on_close=self._after_executors_window)

    def _after_executors_window(self):
        """The will-executor window closed: refresh the rows and the summary."""
        try:
            self._merge_executors_and_send_note()
            self.updatemessage.emit()
        except Exception as e:
            _logger.debug(f"rows not refreshed after the will-executor window: {e!r}")
        try:
            # The Will tab too: its summary box and the List's rows (#134).
            self.bal_window._after_executors_window()
        except Exception as e:
            _logger.debug(f"summary not refreshed: {e!r}")

    def _retry_check(self):
        """"Retry": run the whole check again, in this dialog.

        The copies a will-executor did not get are still "to send", so the
        new run sends them again and verifies them; it is what pressing
        Check again did, without having to know it (owner, #93). The run
        that just ended stopped its worker thread: a new one is made.
        """
        self._set_report_actions(False)
        layout = getattr(self, "_close_layout", None)
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.setParent(None)
                    widget.deleteLater()
            self.vbox.removeItem(layout)
            self._close_layout = None
        self._close_button = None
        self.labels = []
        self._row_times().clear()
        self._report().reset()
        self.check_row = self.inval_row = self.build_row = None
        self.sign_row = self.push_row = self.verify_row = None
        self._next_steps_hint = None
        self._log_auto_opened = False
        self._stopping = False
        self.thread = TaskThread(self)
        self.thread.finished.connect(self.task_finished)
        self.msg_update()
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )

    def _exclude_executor(self, url):
        """"Exclude": deselect a will-executor that did not get the will.

        Owner (#93): deselecting it in the Will-Executor tab and pressing
        Check again was the way out, but hard to find. Asks first, never
        for the last selected executor, then checks again without it.
        """
        host = urlparse(url).hostname or url
        if not self.bal_window.can_exclude_willexecutor(url):
            self.show_warning(_(
                '"{}" is the only will-executor selected: it cannot be '
                "excluded. Choose another one in the Will-Executor tab."
            ).format(host))
            return
        others = [
            u for u, (state, _sec) in self._report().executors().items()
            if u != url and state in ("verified", "to_verify", "received",
                                      "no_check_answer")
        ]
        text = _('"{}" did not receive your will.').format(host)
        if others:
            text += " " + (
                _("1 other will-executor has it.")
                if len(others) == 1
                else _("The other {} will-executors have it.").format(len(others))
            )
        text += "\n\n" + _(
            "If you exclude it, your will is no longer sent to it and the check "
            "runs again without it. You can select it again in the "
            "Will-Executor tab."
        )
        text += "\n\n" + _("Exclude it?")
        if not self.question(text):
            return
        if self.bal_window.exclude_willexecutor(url):
            self._retry_check()

    # ------------------------------------------------------------------ #
    # No-willexecutor error handling
    # ------------------------------------------------------------------ #

    def _add_no_willexecutor_buttons(self):
        """Add "Will-Executor" and "Close" buttons when no executor is
        selected and ``no_willexecutor`` is ``False``."""
        if getattr(self, "_no_we_buttons_added", False):
            return
        self._no_we_buttons_added = True
        self._finish_report()
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        we_btn = QPushButton(_("Will-Executor"))
        we_btn.clicked.connect(self._open_willexecutor_dialog)
        btn_row.addWidget(we_btn)

        download_btn = QPushButton(_("\U0001f52e Wizard"))
        download_btn.clicked.connect(self._open_willexecutor_download_widget)
        btn_row.addWidget(download_btn)

        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        self._add_closing_note(btn_row)

        self._no_we_layout = btn_row
        self.vbox.addLayout(btn_row)
        self._fit_height()

    def _open_willexecutor_dialog(self):
        """Open the will-executor management dialog, then auto-retry
        the build when it closes."""
        d = WillExecutorDialog(self.bal_window, parent=self)
        d.exec()
        self._retry_build_after_willexecutor()

    def _open_willexecutor_download_widget(self):
        """Close the build-will dialog and re-open the wizard at the
        will-executor download step so the user can add one."""
        self.close()
        wizard = BalWizardDialog(self.bal_window)
        # #134: BASIC opens the new wizard's will-executor page.
        wizard.show_executors_step()
        wizard.exec()

    def _retry_build_after_willexecutor(self):
        """Remove the no-willexecutor buttons, reset the message panel,
        and re-run ``task_phase1`` on the same thread."""
        self._no_we_buttons_added = False
        if self._no_we_layout:
            while self._no_we_layout.count():
                item = self._no_we_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
            self.vbox.removeItem(self._no_we_layout)
            self._no_we_layout = None
        self.labels = []
        self._row_times().clear()
        self._report().reset()
        self.msg_update()
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )

    # ------------------------------------------------------------------ #
    # No-heirs error handling (mirrors the no-willexecutor pattern above)
    # ------------------------------------------------------------------ #

    def _add_no_heirs_buttons(self):
        """Add "Heirs", "Wizard" and "Close" buttons when no heirs are
        configured."""
        if getattr(self, "_no_heirs_buttons_added", False):
            return
        self._no_heirs_buttons_added = True
        self._finish_report()
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        heirs_btn = QPushButton(_("Heirs"))
        heirs_btn.clicked.connect(self._open_heir_dialog)
        btn_row.addWidget(heirs_btn)

        wizard_btn = QPushButton(_("\U0001f52e Wizard"))
        wizard_btn.clicked.connect(self._open_heirs_wizard)
        btn_row.addWidget(wizard_btn)

        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        self._add_closing_note(btn_row)

        self._no_heirs_layout = btn_row
        self.vbox.addLayout(btn_row)
        self._fit_height()

    def _open_heir_dialog(self):
        """Open the heirs management dialog, then retry the build."""
        d = HeirsDialog(self.bal_window, parent=self)
        d.exec()
        self._retry_build_after_heirs()

    def _open_heirs_wizard(self):
        """Close the build-will dialog and open the wizard at the heirs
        step so the user can add heirs."""
        self.close()
        wizard = BalWizardDialog(self.bal_window)
        wizard.exec()

    def _retry_build_after_heirs(self):
        """Remove the no-heirs buttons, reset the message panel,
        and re-run ``task_phase1`` on the same thread."""
        self._no_heirs_buttons_added = False
        if self._no_heirs_layout:
            while self._no_heirs_layout.count():
                item = self._no_heirs_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
            self.vbox.removeItem(self._no_heirs_layout)
            self._no_heirs_layout = None
        self.labels = []
        self._row_times().clear()
        self._report().reset()
        self.msg_update()
        self.thread.add(
            self.task_phase1,
            on_success=self.on_success_phase1,
            on_done=self.on_accept,
            on_error=self.on_error_phase1,
        )

    def _ics_provider(self):
        """Return the .ics content for the current will data."""
        from datetime import datetime

        try:
            locktime_ts = Util.parse_locktime_string(
                self.bal_window.will_settings["locktime"]
            )
            locktime = datetime.fromtimestamp(locktime_ts)

            basic_mode = self.bal_window.bal_plugin.is_basic_mode()
            if basic_mode:
                # Factory defaults, in the GUI language (see BalConfig).
                raw_description = (
                    self.bal_window.bal_plugin.EVENT_DESCRIPTION.localized_default()
                )
                raw_summary = self.bal_window.bal_plugin.EVENT_SUMMARY.localized_default()
                threshold = None
                num_reminders = 3
            else:
                # The Check Alive reference shares the same sign rule as the
                # core: negative counts back from the delivery, positive counts
                # forward from the inheritance creation date.
                threshold_ts = resolve_relative_check_alive_date(
                    self.bal_window.will_settings["threshold"],
                    delivery_date=locktime_ts,
                    creation_date=Will.get_creation_time(self.bal_window.willitems),
                )
                threshold = datetime.fromtimestamp(threshold_ts)
                raw_description = self.bal_window.bal_plugin.EVENT_DESCRIPTION.get()
                raw_summary = self.bal_window.bal_plugin.EVENT_SUMMARY.get()
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

            # ToDo #2: when no reminder falls in the future (the delivery date is
            # too close or already passed), build_ics_reminders returns None so
            # the caller shows a clear warning instead of producing an empty,
            # seemingly-broken .ics file.
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

    def _on_close_clicked(self):
        # Close the dialog first, then show the persistent popup guiding the
        # user through any remaining MANUAL steps (Sign / Broadcast).  Showing
        # the (modal) hint after close() mirrors the previous behaviour where
        # the hint appeared once the auto-closing dialog was gone.
        self.close()
        # The still-active warning (#69) is the one message the owner must
        # not miss: it is repeated in a popup. The next-steps hint is only in
        # the dialog's "Attention" box now (owner's decision, #84b).
        if getattr(self, "_still_active_warning", None):
            self.bal_window.show_warning(self._still_active_warning)

    def closeEvent(self, event):
        self._stopping = True
        if getattr(self, "_pending_invalidation", None) is not None:
            # #135: closed at the invalidation step: not invalidated, the
            # later date goes back as after a Cancel (option B).
            self._invalidation_cancelled()
        # Stop AND join the thread, then propagate the close event (previously
        # it neither waited nor called super().closeEvent()).
        stop_thread(getattr(self, "thread", None))
        super().closeEvent(event)

    def task_phase2(self, password):
        if self._stopping:
            return
        # password False: "Sign your will" was cancelled ("Aborted" is shown);
        # nothing to try (#157: before, the signing ran and failed silently).
        if self.have_to_sign and password is not False:
            try:
                # #135: only the items to sign (no deselected leftovers).
                # #157: the engine stores each signed copy at once; an error
                # keeps the ones signed before and is shown (before, it was
                # swallowed and the row said nothing).
                result = self.bal_window.sign_transactions(
                    password, txids=self._ids_to_sign())
                if result.txs:
                    self.bal_window.save_willitems()
                if result.error is not None:
                    self.msg_set_signing(self.msg_error(result.error))
                elif result.txs:
                    self.msg_set_signing(self.msg_ok())
            except Exception as e:
                self.msg_set_signing(self.msg_error(e))

        self.msg_set_pushing()
        have_to_push = False
        for wid in Will.only_valid(self.bal_window.willitems):
            w = self.bal_window.willitems[wid]
            if w.we and w.get_status("COMPLETE") and not w.get_status("PUSHED"):
                have_to_push = True
        if not have_to_push:
            self.msg_set_pushing(_("Nothing to do"))
        else:
            try:
                self.loop_push()
                self.msg_set_pushing(self.msg_ok())

            except Exception as e:
                # td = traceback.format_exc()
                self.msg_set_pushing(self.msg_error(e))
        # CHANGELOG #84b-2: verify here what the will-executors hold (it
        # used to run after this dialog, in a separate window, and not at
        # all at wallet close). Never stops the dialog.
        try:
            if not self._stopping:
                self._merge_executors_and_send_note()
            self.loop_verify()
        except Exception as e:
            _logger.error(f"verification failed: {e!r}")
            self.msg_set_verifying(self.msg_error(e))
        # Blank separator row: visually detach the final "All done" summary
        # from the per-step result rows above it, so the closing line stands
        # out as the overall outcome rather than just another step.
        self.msg_edit_row("")
        # Final summary row: the whole "Building Will" sequence above (check /
        # sign / broadcast) finished without errors.  Give it an explicit
        # left-side label ("All done") so this closing Ok is not an orphan
        # result like the other rows have.
        self.msg_edit_row("{}:\t{}".format(_("All done"), self.msg_ok()))

        # Safety net (CHANGELOG #69): warn when an executor still holds a
        # transaction of an OLDER will that could be executed before the
        # current one. Read-only; shown in basic and advanced mode, also with
        # auto-sign.
        self._show_still_active_warning()

        # Guide the user through any remaining MANUAL steps.  After the will is
        # (re)built -- e.g. because an heir was removed/added from the Wizard --
        # the new transactions may still need to be SIGNED and/or BROADCAST by
        # the user.  This dialog only signs/pushes automatically when it already
        # has the password and the will is in the right state; in every other
        # case the user is otherwise left without any indication of what to do
        # next.  We inspect the real status of the valid wills and tell the user
        # exactly which buttons to press.
        self._show_next_steps_hint()

    def _show_still_active_warning(self):
        """Add a red warning row when an older will is still executable.

        Uses :meth:`Will.find_still_active_older_txs` (read-only). The plain
        text is also kept in ``self._still_active_warning`` so
        :meth:`_on_close_clicked` repeats it in a popup once the dialog is
        closed: a row at the bottom of a long report is easy to miss, and this
        is the one message the user must not miss. Never raises.
        """
        self._still_active_warning = None
        try:
            bw = self.bal_window
            fallback = (bw.will_settings or {}).get("locktime")
            found = Will.find_still_active_older_txs(bw.willitems, bw.wallet, fallback)
            if not found:
                return
            current = Will.get_current_will_locktime(bw.willitems, fallback)

            def day(locktime):
                """The one long date style (#134): "13 October 2028, 00:00 UTC"."""
                return datetext.utc_text(int(locktime))

            # One line per executor and date: an executor may hold several
            # old transactions with the same date.
            lines = []
            for _item, locktime, url in found:
                line = _(
                    "The will-executor {url} still holds a transaction from an "
                    "older will with delivery date {old_date}, earlier than "
                    "your current will ({current_date})."
                ).format(
                    url=url or "?", old_date=day(locktime), current_date=day(current)
                )
                if line not in lines:
                    lines.append(line)
            title = _("Warning: an older will is still active.")
            remedy = _(
                "It could be used first. To cancel it, send your whole balance "
                "to one of your own addresses (Electrum Send tab, Max), then "
                "press Check again."
            )
            self._still_active_warning = "\n\n".join([title] + lines + [remedy])
            row = self._log_only(
                self.msg_error(
                    "<br>".join(html.escape(t) for t in [title] + lines + [remedy])
                )
            )
            self._report().put("warnings", ("row", row), self._still_active_warning)
        except Exception as e:
            _logger.error(f"still-active warning failed: {e!r}")

    def _show_next_steps_hint(self):
        """Append a clear "what to do next" line to the Building Will dialog.

        Pure UX guidance (no logic change): looks at the valid wills and, if any
        still needs signing or broadcasting, tells the user to press 'Sign'
        and/or 'Broadcast' manually.  The computed hint is also stored in
        ``self._next_steps_hint`` so a persistent popup can be shown after the
        dialog closes (this dialog auto-closes after a few seconds, which is too
        short to be sure the user noticed the in-dialog line).
        """
        self._next_steps_hint = None
        # Group B / B2: when AUTO_SIGN is ON the dialog has already signed and
        # broadcast the will automatically, so the manual "press Sign/Broadcast"
        # hints (and the follow-up popup) would be wrong/confusing. Suppress
        # them in that case. When AUTO_SIGN is OFF, keep the previous behaviour
        # and guide the user through the remaining manual steps.
        try:
            if (
                self.bal_window.bal_plugin.is_basic_mode()
                or self.bal_window.bal_plugin.AUTO_SIGN.get()
            ):
                return
        except Exception:
            # If the setting cannot be read for any reason, fall back to the
            # original behaviour (show the manual hints).
            pass
        try:
            need_sign = False
            need_push = False
            for wid in Will.only_valid(self.bal_window.willitems):
                w = self.bal_window.willitems[wid]
                if not w.get_status("COMPLETE"):
                    # Not signed yet.
                    need_sign = True
                elif w.we and not w.get_status("PUSHED"):
                    # Signed but not yet sent to its will-executor.
                    need_push = True

            if need_sign and need_push:
                hint = _(
                    "Next steps (manual): press 'Sign' to sign your will, "
                    "then 'Broadcast will' to send it to the will-executors."
                )
            elif need_sign:
                hint = _(
                    "Next step (manual): press 'Sign' to sign your will."
                )
            elif need_push:
                hint = _(
                    "Next step (manual): press 'Broadcast will' to send your "
                    "will to the will-executors."
                )
            else:
                # Nothing left to do (already signed and, if needed, sent).
                return

            self._next_steps_hint = hint
            row = self.msg_edit_row("<b>{}</b>".format(hint))
            self._report().put("warnings", ("row", row), hint)
        except Exception as hint_err:
            _logger.debug(f"next-steps hint error: {hint_err}")

    def on_error(self, error):
        _logger.error(error)
        pass

    def on_error_phase1(self, error):
        self.bal_window.update_all()
        a, b, c = error
        self.msg_edit_row(self.msg_error(_("Error: {}").format(b)))
        self._finish_report()
        import traceback
        _logger.error(f"error phase1: {b}\n{''.join(traceback.format_exception(a, b, c))}")
        button=QPushButton(_("Close"))
        button.clicked.connect(self.close)
        self.vbox.addWidget(button)
        self.resize(self.vbox.sizeHint()+button.sizeHint()*2)
        self.repaint()
    def on_error_phase2(self, error):
        """Show an error raised while signing/sending the will (phase 2).

        Refreshes the wallet window and adds the error row. ``update_all``
        was misspelled ``upade_all`` (CHANGELOG #74), which raised
        AttributeError inside this handler and hid the original error.
        """
        self.bal_window.update_all()
        a, b, c = error
        self.msg_edit_row(self.msg_error(_("Error: {}").format(b)))
        self._finish_report()
        _logger.error(f"error phase2: {b}")

    # The bridges below keep the method names of the helpers moved to the
    # core (CHANGELOG #115): tests borrow them one by one, and they keep the
    # old promise of never raising, even when the window state cannot be read.

    def _executed_inheritance_status(self):
        """See :meth:`Will.executed_inheritance_status` (moved in CHANGELOG #115)."""
        try:
            willitems = self.bal_window.willitems
        except Exception as _err:
            _logger.debug(f"_executed_inheritance_status error: {_err}")
            return None
        return Will.executed_inheritance_status(willitems)

    def _unsigned_will_executors(self):
        """See :meth:`Will.unsigned_will_executors` (moved in CHANGELOG #115)."""
        try:
            willitems = self.bal_window.willitems
        except Exception as e:
            _logger.debug(f"unsigned will check failed: {e!r}")
            return None
        return Will.unsigned_will_executors(willitems)

    @staticmethod
    def _no_valid_transaction(e):
        """See :func:`bal.core.messages.no_valid_transaction` (CHANGELOG #77)."""
        return no_valid_transaction(e)

    def _rebuild_reason_message(self, e):
        """See :func:`bal.core.messages.rebuild_reason_message` (CHANGELOG #115).

        Passes this dialog's state: whether it broadcast an invalidation,
        whether that was a postpone, and the executors of the unsigned will
        seen before ``check_will``.
        """
        return rebuild_reason_message(
            e,
            self._invalidation_broadcast,
            self._postpone_invalidation,
            self._unsigned_executors,
        )

    def _check_failure_message(self, e):
        """Return a precise explanation of why the will is no longer coherent.

        The dialog used to print the same sentence, "Found CHANGES to the
        DATE or the HEIRS", for every case - wrong for the most common one,
        new funds received. The sentences now live in
        :func:`bal.core.messages.will_change_message`, shared with the main
        window and the command line (CHANGELOG #78).
        """
        return will_change_message(e)

    @staticmethod
    def _amounts_message(info):
        """See :func:`bal.core.messages.amounts_message` (moved in CHANGELOG #115)."""
        return amounts_message(info)

    def _amount_warning_is_false_alarm(self, available_utxos):
        """See :func:`bal.engine.check.amount_warning_is_false_alarm`
        (CHANGELOG #115; moved to the engine in #120).

        Reads this dialog's window. A window or will settings that cannot
        be read count as "date not passed" (the coin test still decides), as
        before; only an error in the date test itself means "show the
        warning" (returns False). Tests borrow this method on their own
        classes, so it uses nothing else of the dialog.
        """
        try:
            host = self.bal_window
        except Exception as _err:
            # As before #120: a window that cannot be read means "no date
            # message" (the engine helper treats None the same way).
            _logger.debug(f"_amount_warning_is_false_alarm: {_err}")
            host = None
        return check.amount_warning_is_false_alarm(host, available_utxos)

    def _past_date_message(self):
        """See :func:`bal.engine.check.past_date_message_of` (CHANGELOG
        #115; moved to the engine in #120).

        Reads the window's will settings; never raises, as before. Tests
        borrow this method on their own classes, so it uses nothing else of
        the dialog.
        """
        try:
            host = self.bal_window
        except Exception as _err:
            _logger.debug(f"_past_date_message: {_err}")
            return None
        return check.past_date_message_of(host)

    def _build_failure_message(self):
        """See :func:`bal.core.messages.build_failure_message` (CHANGELOG #115).

        Reads the reason code left by ``Heirs.buildTransactions`` in
        ``last_build_error``. The past-date test runs only for
        ``NO_FUTURE_DATE``, as before.
        """
        reason = None
        try:
            reason = getattr(self.bal_window.heirs, "last_build_error", None)
        except Exception as _err:
            # A diagnostic must never break the report it is explaining.
            _logger.debug(f"_build_failure_message: {_err}")
        date_passed = reason == "NO_FUTURE_DATE" and bool(self._past_date_message())
        return build_failure_message(reason, date_passed)

    def msg_set_checking(self, status=None, row=None):
        # The default is resolved here, not in the signature: a default
        # argument is evaluated at import time, before the catalog is loaded.
        status = _("Waiting") if status is None else status
        row = self.check_row if row is None else row
        self.check_row = self.msg_set_status(_("Checking your will"), row, status)

    def msg_set_invalidating(self, status=None, row=None):
        row = self.inval_row if row is None else row
        self.inval_row = self.msg_set_status(
            _("Invalidating old will"), self.inval_row, status
        )

    def msg_set_building(self, status=None, row=None,color=None):
        row = self.build_row if row is None else row
        self.build_row = self.msg_set_status(
            _("Building your will"), self.build_row, status, color
        )

    def msg_set_signing(self, status=None, row=None):
        row = self.sign_row if row is None else row
        self.sign_row = self.msg_set_status(
            _("Signing your will"), self.sign_row, status
        )

    def msg_set_pushing(self, status=None, row=None):
        row = self.push_row if row is None else row
        self.push_row = self.msg_set_status(
            _("Broadcasting your will to executors"), self.push_row, status
        )

    def msg_set_verifying(self, status=None, row=None):
        """The log row of the Verification step (#84b-2)."""
        row = self.verify_row if row is None else row
        self.verify_row = self.msg_set_status(
            _("Verifying your will with the executors"), row, status
        )

    def msg_set_waiting(self, status=None, row=None):
        row = self.wait_row if row is None else row
        self.wait_row = self.msg_edit_row(
            _("Please wait {}secs").format(status), self.wait_row
        )

    def msg_error(self, e):
        # Results are shown in bold so the outcome stands out from the
        # left-side state label (which stays in normal weight).
        return "<font color='{}'><b>{}</b></font>".format(self.COLOR_ERROR, e)

    def msg_ok(self, e=None):
        # Results are shown in bold (see msg_error). The default "Ok" is
        # translated here, not in the signature (evaluated at import time).
        e = _("Ok") if e is None else e
        return "<font color='{}'><b>{}</b></font>".format(self.COLOR_OK, e)

    def msg_warning(self, e):
        # Results are shown in bold (see msg_error).
        return "<font color='{}'><b>{}</b></font>".format(self.COLOR_WARNING, e)

    def msg_alert(self, e):
        """Amber warning sign followed by text in the theme's default colour.

        WHY: long warnings printed entirely in amber (COLOR_WARNING) are hard
        to read - the owner reported the multi-line "could not build the will"
        block as barely legible.  Colour is only needed to ATTRACT attention,
        not to be read, so we keep it on the "warning sign" character alone and
        let the message body inherit Electrum's normal text colour.  That also
        keeps it readable under the dark theme, where a hard-coded black would
        disappear.  U+26A0 is written as a numeric HTML entity so the source
        file stays pure ASCII; QLabel renders it as rich text.
        """
        return "<font color='{}'>&#9888;</font> <b>{}</b>".format(
            self.COLOR_WARNING, e
        )

    def msg_set_status(self, msg, row=None, status=None, color=None):
        # The left "state" label keeps its normal weight; only the right-side
        # result (``status``) is rendered in bold so it is easy to read at a
        # glance.  ``status`` may already contain rich-text emitted by
        # msg_ok/msg_error/msg_warning (which add their own <b>...</b>); wrapping
        # it again in <b> is harmless for those cases.
        # ``msg`` must already be translated by the caller: a variable
        # passed to _() cannot be extracted into the catalog.
        status = _("Wait") if status is None else status
        if color is None:
            line = "{}:\t<b>{}</b>".format(msg, status)
        else:
            line = "{}:\t<font color={}><b>{}</b></font>".format(
                msg, color, status
            )
        # The step row goes to the log as it is; the report (#84b) reads the
        # step and the state from the label and the status instead.
        self._auto_box_off = True
        try:
            row = self.msg_edit_row(line, row)
        finally:
            self._auto_box_off = False
        self._report_status(msg, status, color, row)
        # Redraw again: msg_edit_row redrew before the report knew the step.
        self.updatemessage.emit()
        return row

    def ask_password(self, msg=None):
        self.password = self.bal_window.get_wallet_password(msg, parent=self)

    def msg_edit_row(self, line, row=None):
        times = self._row_times()
        try:
            self.labels[row] = line
            times[row] = time.strftime("%H:%M:%S")
        except Exception:
            self.labels.append(line)
            row = len(self.labels) - 1
            times.append(time.strftime("%H:%M:%S"))
        # A row written on its own (not a step row): an error or a warning
        # also goes to the report's boxes (#84b).
        if not getattr(self, "_auto_box_off", False):
            self._report_row(line, row)

        self.updatemessage.emit()

        return row

    def msg_del_row(self, row):
        try:
            del self.labels[row]
            del self._row_times()[row]
        except Exception:
            pass
        self.updatemessage.emit()

    # def clear_layout(self,layout):
    #    while layout.count():
    #        item = layout.takeAt(0)
    #        w = item.widget()
    #        if w:
    #            w.setParent(None)
    #            w.deleteLater()

    # def msg_update(self):
    #    self.clear_layout(self.labelsbox)
    #    for label in self.labels:
    #        label=label.replace("\n","<br>")
    #        qlabel=QLabel(label)
    #        qlabel.setWordWrap(True)
    #        self.labelsbox.addWidget(qlabel)

    #    self.labelsbox.activate()
    #    self.qwidget.setMinimumSize(self.labelsbox.sizeHint())
    #    self.qwidget.adjustSize()
    #    from PyQt6.QtWidgets import QApplication
    #    QApplication.processEvents()
    #
    #    self.adjustSize()
    def msg_update(self):
        """Redraw the report (#84b) and the log (GUI thread)."""
        snapshot = self._report().snapshot()
        self.report_view.render(snapshot)
        result = snapshot.get("result")
        if result and result[0] == "err" and not self._log_auto_opened:
            # Owner's decision: the log opens by itself on an error (once,
            # so a later "close" by the owner is respected).
            self._log_auto_opened = True
            self.log_toggle.setChecked(True)
        self._set_log_toggle_text()
        times = self._row_times()
        lines = []
        for i, line in enumerate(self.labels):
            stamp = times[i] if i < len(times) else ""
            lines.append(
                "<span style='color:#777777;'>{}</span>&nbsp;&nbsp;{}".format(
                    stamp, line
                )
            )
        self.message_label.setText("<br>".join(lines).replace("\n", "<br>"))
        self.message_label.adjustSize()
        self._fit_height()
        # The newest log line is at the bottom: follow it only while the log
        # is open; otherwise keep the steps bar in view (top).
        scrollbar = self.scroll_area.verticalScrollBar()
        if self.log_toggle.isChecked():
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(0)

    def _set_log_toggle_text(self):
        """"▸ Details (log)" when closed, "▾ ..." when open."""
        arrow = "\u25be" if self.log_toggle.isChecked() else "\u25b8"
        self.log_toggle.setText("{} {}".format(arrow, _("Details (log)")))

    def _on_log_toggled(self, checked):
        """Show or hide the log under the report.

        Closing it gives the room back (CHANGELOG #96): the dialog fits its
        content again.
        """
        self.message_label.setVisible(checked)
        self._set_log_toggle_text()
        self._fit_height(shrink=not checked)

    #: Share of the screen's available height the dialog may take, and the
    #: height it opens with (both in logical pixels, see _fit_height).
    #: CHANGELOG #96 (owner: too tall, then too short): 75% of the screen
    #: at most (was 85%), 560 px at least: the height of the owner's
    #: screenshot with a "what changed" box, the heirs, 4 will-executors and
    #: the log link, all visible. On a 1366x768 notebook the cap (546 px)
    #: wins.
    MAX_SCREEN_SHARE = 0.75
    OPEN_HEIGHT = 560

    def _content_height(self):
        """Height the report needs inside the scroll area, never too small.

        CHANGELOG #96, owner's screenshots: the dialog ended too short and
        hid the will-executors box. Qt shows a widget added to a visible
        dialog only later (a queued event), and a widget not shown yet counts
        as empty, so a fit right after a redraw missed whole boxes. The
        children not hidden on purpose are shown first. The measure is also
        the larger of the layout's hint and its height for the width the
        text really has: a wrapped sentence needs more height when narrow.
        """
        area = self.scroll_area
        content = area.widget()
        for child in content.findChildren(QWidget):
            if not child.isWindow() and not child.testAttribute(
                Qt.WidgetAttribute.WA_WState_ExplicitShowHide
            ):
                child.show()
        layout = content.layout()
        layout.invalidate()
        height = content.sizeHint().height()
        if layout.hasHeightForWidth():
            margins = self.vbox.contentsMargins()
            width = (
                self.width() - margins.left() - margins.right()
                - 2 * area.frameWidth()
                - area.verticalScrollBar().sizeHint().width()
            )
            height = max(height, layout.totalHeightForWidth(max(width, 100)))
        return height + 2 * area.frameWidth()

    def _fit_height(self, shrink=False):
        """Fit the dialog's height to its content, between 560 px and 75%.

        Owner (CHANGELOG #84b): the dialog was too short and its buttons
        covered the will-executors box. It opens at least ``OPEN_HEIGHT``
        tall and grows with its content up to ``MAX_SCREEN_SHARE`` of the
        screen's available height (without the taskbar); beyond that the
        content scrolls and the buttons stay visible. While the run goes on
        it only grows, so it does not jump.

        Owner (CHANGELOG #96): it was much taller than its content and its
        buttons often ended below the screen; the first fix then made it too
        short. Now:

        * the room around the report is counted right. It was ~200 px too
          big: ``QScrollArea.sizeHint()`` keeps the size its content had
          when the dialog was created (~30 px), while the layout reserves at
          least the scroll area's minimum (240 px) for it;
        * the content is measured right (``_content_height``) and is never
          cut: no scroll bar while it fits under the cap;
        * never shorter than ``OPEN_HEIGHT``, so a short report does not
          leave a dialog that hides the will-executors and the log link;
        * ``shrink=True`` (the run is over, the log was closed): the dialog
          fits its content, down to ``OPEN_HEIGHT``;
        * after every change of height it is kept inside the screen
          (``_keep_on_screen``).

        Never raises.
        """
        try:
            screen = self.screen()
            available = screen.availableGeometry().height() if screen else 768
            limit = max(int(available * self.MAX_SCREEN_SHARE), 300)
            content = self._content_height()
            # Everything that is not the scroll area: margins, spacing and the
            # rows of buttons under it. The scroll area's share is what the
            # layout reserves for it (its item), not its own stale sizeHint().
            index = self.vbox.indexOf(self.scroll_area)
            reserved = self.vbox.itemAt(index).sizeHint().height()
            others = self.vbox.sizeHint().height() - reserved
            needed = max(content + max(others, 0),
                         self.vbox.totalMinimumSize().height())
            target = min(max(needed, self.OPEN_HEIGHT), limit)
            if target > self.height() or (shrink and target < self.height()):
                self.resize(max(self.width(), self.minimumWidth()), target)
                self._keep_on_screen()
        except Exception as e:
            _logger.debug(f"build dialog sizing failed: {e!r}")

    def _keep_on_screen(self):
        """Move the dialog up when its bottom edge is below the screen.

        CHANGELOG #96: Qt places a dialog inside the screen when it opens.
        When it grows afterwards its top edge stays where it was, so the
        bottom edge, with the buttons, could end below the screen. Only for
        a visible dialog (before it opens, Qt places it). Never raises.
        """
        try:
            if not self.isVisible():
                return
            screen = self.screen()
            if screen is None:
                return
            area = screen.availableGeometry()
            overflow = self.frameGeometry().bottom() - area.bottom()
            if overflow > 0:
                self.move(self.x(), max(area.top(), self.y() - overflow))
        except Exception as e:
            _logger.debug(f"build dialog position failed: {e!r}")

    # ---- the structured report (CHANGELOG #84b) ------------------------ #
    def _report(self):
        """The dialog's :class:`BuildReport` (created on first use)."""
        report = getattr(self, "_build_report", None)
        if report is None:
            report = self._build_report = BuildReport()
        return report

    def _row_times(self):
        """``HH:MM:SS`` of every log row (parallel to ``self.labels``)."""
        times = getattr(self, "_times", None)
        if times is None:
            times = self._times = []
        return times

    def _format_amount(self, satoshi):
        """An amount in the wallet's unit, as Electrum shows it."""
        return self.bal_window.window.format_amount_and_units(satoshi)

    def _step_of(self, label):
        """The bar step a status row belongs to, or None."""
        return {
            _("Checking variables"): "check",
            _("Checking your will"): "check",
            _("Invalidating old will"): "prepare",
            _("Building your will"): "prepare",
            _("Signing your will"): "sign",
            _("Broadcasting your will to executors"): "send",
            _("Verifying your will with the executors"): "verify",
        }.get(label)

    def _running_text(self, label):
        """The result sentence while the step of ``label`` runs."""
        return {
            _("Checking variables"): _("Checking your will\u2026"),
            _("Checking your will"): _("Checking your will\u2026"),
            _("Invalidating old will"): _(
                "Invalidating the old will on the blockchain\u2026"
            ),
            _("Building your will"): _("Preparing your will\u2026"),
            _("Signing your will"): _("Signing your will\u2026"),
            _("Broadcasting your will to executors"): _(
                "Sending your will to the will-executors\u2026"
            ),
            _("Verifying your will with the executors"): _(
                "Verifying your will with the will-executors\u2026"
            ),
        }.get(label, "")

    def _status_class(self, status, color=None):
        """``(kind, plain text)`` of a status written by this dialog.

        Every coloured status comes from msg_ok / msg_error / msg_warning /
        msg_alert (or the ``color`` argument), so the colour tells the kind:
        "ok", "err", "warn". Plain words tell "run" (Wait, Waiting,
        Broadcasting, the push counters and the "x/y (Ns / 20s)" progress
        lines of the sending and the verification), "skip" (Nothing to do,
        Skipped) and "abort" (Aborted); any other plain text is "text".
        """
        raw = str(status)
        text = re.sub(r"<[^>]+>", "", raw).replace("&#9888;", "").strip()
        text = html.unescape(text)
        marks = raw + " " + str(color or "")
        if self.COLOR_ERROR in marks:
            return "err", text
        if self.COLOR_WARNING in marks or "&#9888;" in raw:
            return "warn", text
        if self.COLOR_OK in marks:
            return "ok", text
        if text in (_("Wait"), _("Waiting")) or text.startswith(_("Broadcasting")):
            return "run", text
        if re.match(r"^\d+/\d+ \(\d+s\)$", text):
            return "run", text
        if re.search(r"\d+/\d+ \(\d+s / \d+s\)", text):
            # A progress line ("Verifying 1/2 (4s / 20s) ..."): matched by its
            # shape, so the translated verb does not matter.
            return "run", text
        if text in (_("Nothing to do"), _("Skipped")):
            return "skip", text
        if text == _("Aborted"):
            return "abort", text
        return "text", text

    def _report_status(self, label, status, color, row):
        """Turn a status row into the report: the step and, if any, a box line.

        Never raises: the report must never break the will flow.
        """
        try:
            report = self._report()
            kind, text = self._status_class(status, color)
            step = self._step_of(label)
            key = ("row", row)
            for box in ("changes", "warnings", "problems"):
                report.put(box, key, None)
            if step is None:
                # Rows of their own (an heir below the dust limit, an
                # excluded will-executor...): warnings and errors only.
                line = "{}: {}".format(label, text)
                if label == _("Will-Executor excluded") or kind == "warn":
                    report.put("warnings", key, line)
                elif kind == "err":
                    report.put("problems", key, line)
                return
            if kind == "run":
                # The sending and the verification set their own counter and
                # "waiting for" sentence once running (#84b-2: rewriting the
                # sentence here at every progress row erased "waiting for").
                if step not in ("send", "verify") or report.step(step)[0] != "run":
                    report.set_step(step, "run", "")
                    report.set_result("run", self._running_text(label))
            elif kind == "skip":
                report.set_step(step, "skip")
            elif kind == "abort":
                report.interrupt(step)
            elif kind == "ok":
                report.set_step(step, "ok")
                if text and text != _("Ok"):
                    report.put("changes", key, text)
            elif kind == "err":
                report.set_step(step, "err")
                if text and text not in (_("Stopped"), _("Ko")):
                    report.put("problems", key, text)
            elif kind == "warn":
                report.set_step(step, "warn")
                if text:
                    report.put("warnings", key, text)
            elif step in ("check", "prepare"):
                # Plain text on these steps tells what changed (a new fee, a
                # postpone, a date moved one day earlier...).
                if report.step(step)[0] in ("todo", "run"):
                    report.set_step(step, "ok")
                report.put("changes", key, text)
            else:
                report.set_step(step, "warn")
                report.put("warnings", key, text)
        except Exception as e:
            _logger.error(f"build report (status) failed: {e!r}")

    def _report_row(self, line, row):
        """A row written on its own: errors and warnings go to the boxes."""
        try:
            raw = str(line)
            if self.COLOR_ERROR in raw:
                box = "problems"
            elif self.COLOR_WARNING in raw or "&#9888;" in raw:
                box = "warnings"
            else:
                return
            text = re.sub(r"<[^>]+>", "", raw.replace("<br>", "\n"))
            text = html.unescape(text.replace("&#9888;", "")).strip()
            self._report().put(box, ("row", row), text)
        except Exception as e:
            _logger.error(f"build report (row) failed: {e!r}")

    def _log_only(self, line, row=None):
        """Write a log row that must NOT go to the boxes (own report call)."""
        self._auto_box_off = True
        try:
            return self.msg_edit_row(line, row)
        finally:
            self._auto_box_off = False

    def _finish_report(self):
        """Fill the report when the dialog stops (any ending). Never raises.

        The will-executors and the heirs come from the will items; the result
        sentence names the failed or cancelled step, otherwise it is the
        will summary of the Will tab (#84a), so the two always agree.
        """
        try:
            report = self._report()
            bw = self.bal_window
            self._quiet_shares_line(report)
            executors = self._merge_executors_and_send_note()
            total = len(executors)
            if report.step("send")[0] == "skip" and any(
                st == "to_sign" for st, _sec in executors.values()
            ):
                # Owner's test (#84b-2): after a Cancel at the password the bar
                # said "Sending: not needed". It was needed; it could not
                # happen because a copy is still to sign. Grey, no word.
                report.set_step("send", "todo")
            verified = sum(1 for st, _sec in executors.values() if st == "verified")
            # Counted over every will-executor of the will, like the sending
            # (#84b-2): a verification that ran and got no confirmation is an
            # error; one that never ran (nothing sent yet) is still "to do".
            verify_state = report.step("verify")[0]
            if total and verify_state != "skip":
                if verified == total:
                    report.set_step("verify", "ok", "{}/{}".format(verified, total))
                elif verified or any(
                    st == "no_check_answer" for st, _sec in executors.values()
                ):
                    # #147: no answer is not "not received": a warning.
                    report.set_step("verify", "warn", "{}/{}".format(verified, total))
                elif verify_state == "todo":
                    report.set_step("verify", "todo", _("to do"))
                else:
                    report.set_step("verify", "err", "{}/{}".format(verified, total))
            if report.step("prepare")[0] == "ok":
                report.set_heirs(heir_shares(bw.willitems, bw.heirs))
            summary = will_summary(
                bw.willitems,
                len(bw.heirs or {}),
                bw.will_settings,
                bw.bal_plugin.is_basic_mode(),
                still_active=bw._still_active_for_summary(),
                warn_months=getattr(bw.bal_plugin, "warning_months", lambda: None)(),
                # #134: the one date style and the in-force comparison.
                long_dates=True,
                # #134: deselected will-executors are not "to send".
                selected=self._selected_willexecutors(),
                # #135: THE will in force, the one rule of the List, the
                # Future view and the wizard (not only the REPLACED copies).
                in_force=engine_context.in_force_delivery(bw),
            )
            failed_step = next(
                (s for s in ("check", "prepare", "sign", "send")
                 if report.step(s)[0] == "err"),
                None,
            )
            interrupted = report.interrupted()
            if not failed_step and not interrupted:
                # A normal ending: the steps never reached were not needed
                # (nothing to prepare, sign or send).
                for step in ("prepare", "sign", "send"):
                    if report.step(step)[0] == "todo":
                        report.set_step(step, "skip")
            if failed_step:
                report.set_result("err", "<b>{}</b>".format(html.escape({
                    "check": _("The check stopped: nothing was changed."),
                    "prepare": _("The new will was not prepared."),
                    "sign": _("The new will was not signed."),
                    "send": _("The will was not sent to the will-executors."),
                }[failed_step])))
                report.set_will_now(self._will_now_text(summary))
            elif "prepare" in interrupted and self._postpone_reverted:
                # #134: the later date was put back (see
                # _revert_cancelled_postpone): "nothing was changed" would
                # not be true.
                report.set_result("warn", "<b>{}</b>".format(html.escape(_(
                    "Interrupted: the old will was not invalidated. Your later "
                    "date ({chosen}) was not applied: the will in force needs "
                    "an invalidation transaction on the blockchain first (your "
                    "bitcoin stay yours, you only pay the normal network fee). "
                    "When you are ready, choose it again."
                ).format(chosen=datetext.utc_text(self._postpone_reverted[0])))))
                report.set_will_now(self._will_now_text(summary))
            elif "prepare" in interrupted:
                report.set_result("warn", "<b>{}</b>".format(html.escape(_(
                    "Interrupted: the old will was not invalidated, nothing "
                    "was changed."
                ))))
                report.set_will_now(self._will_now_text(summary))
            elif "sign" in interrupted:
                report.set_result("warn", "<b>{}</b>".format(html.escape(
                    _("Interrupted: the new will was not signed.")
                )))
                report.set_will_now(self._will_now_text(summary))
            elif report.step("prepare")[0] == "warn":
                report.set_result("warn", "<b>{}</b>".format(html.escape(
                    _("The new will was not prepared.")
                )))
                report.set_will_now(self._will_now_text(summary))
            elif summary:
                report.set_result(
                    {"bad": "err", "busy": "info"}.get(summary["kind"], summary["kind"]),
                    self._summary_sentence(summary),
                )
            self.updatemessage.emit()
        except Exception as e:
            _logger.error(f"build report (finish) failed: {e!r}")

    def _merge_executors_and_send_note(self):
        """Every will-executor of the will in the box, and the sending's count.

        The box gets the state of each executor from the will items (a live
        "no answer" is kept); the Sending step gets "received/total". Used
        when the dialog ends and, since #84b-2, as soon as the sending ends,
        so the bar shows "2/2" under Sending while the verification runs
        (it showed "ok" until the end). Returns ``{url: (state, seconds)}``.
        """
        report = self._report()
        valid = Will.only_valid_list(self.bal_window.willitems)
        # #134: a deselected will-executor never received the will and the
        # sending skips it: "deselected: not sent", not counted in "n/m".
        selected = self._selected_willexecutors()
        report.merge_executors(executor_states(valid, selected))
        executors = report.executors()
        # A will-executor selected again since gets its real state ("to
        # send") from the merge above. The deselected ones stay in the box
        # but are left out of the counts.
        executors = {u: v for u, v in executors.items() if v[0] != "deselected"}
        total = len(executors)
        received = sum(
            1 for st, _sec in executors.values()
            if st in ("received", "verifying", "verified", "to_verify",
                      "no_check_answer")
        )
        failed = sum(
            1 for st, _sec in executors.values() if st in ("failed", "timeout")
        )
        if report.step("send")[0] in ("ok", "run") and total:
            report.set_step(
                "send", "warn" if failed else "ok", "{}/{}".format(received, total)
            )
        return executors

    @staticmethod
    def _summary_sentence(summary):
        """The will summary as the result sentence: title, then delivery.

        The delivery date and its text take the remaining-time colour of
        the will summary (#124).
        """
        text = "<b>{}</b>".format(html.escape(summary["title"]))
        for row in summary.get("rows") or []:
            if row.get("strong"):
                value = "<b>{}</b> {}".format(
                    html.escape(row["strong"]), html.escape(row.get("text") or ""),
                )
                colour = delivery_level_colour(row.get("level"))
                if colour:
                    value = '<span style="color:{};">{}</span>'.format(colour, value)
                text += "<br>{}: {}".format(html.escape(row["label"]), value)
                break
        return text

    @staticmethod
    def _will_now_text(summary):
        """The will in force in one line, for the "Your will now" box."""
        if not summary:
            return None
        text = summary["title"]
        for row in summary.get("rows") or []:
            if row.get("strong"):
                text += " \u2014 {}: {}".format(row["label"], row["strong"])
                break
        if summary.get("note"):
            text += "\n" + summary["note"]
        return text

    def get_text(self):
        return self.message_label.text()

    pass



def _live_threshold(bal_window):
    """The Check Alive date of the live will, as the toolbar shows it.

    ``bal_window.real_threshold``: the display cache the toolbar sets
    (``WillSettingsWidget.on_locktime_change``); before #123 it was a key of
    the shared settings. When the toolbar did not set it yet, the delivery
    date of the wallet's settings is shown instead.
    """
    threshold = getattr(bal_window, "real_threshold", None)
    if threshold is None:
        threshold = (getattr(bal_window, "will_settings", None) or {}).get("locktime")
    return threshold


class WillDetailDialog(BalDialog):
    def __init__(self, bal_window, will=None, threshold=None):
        """The Will Details window of the live will, or of an imported one.

        ``will``/``threshold`` are passed when showing an IMPORTED will; the
        live will shows the Check Alive date of the toolbar
        (:func:`_live_threshold`, #123). For the live will a note at the top
        points to the Future view of the Will tab (#131).
        """
        # ``will``/``threshold`` are passed when showing an IMPORTED (read-only)
        # will. In that case every action button below operates on the imported
        # will, never on the live wallet state.
        self._external_will = will is not None
        self.will = will if self._external_will else bal_window.willitems
        if threshold is not None:
            self.threshold = threshold
        elif self._external_will:
            self.threshold = max(wi.tx.locktime for wi in self.will.values())
        else:
            self.threshold = _live_threshold(bal_window)

        self.bal_window = bal_window
        Will.add_willtree(self.will)
        super().__init__(bal_window.window, bal_window.bal_plugin)
        self.config = bal_window.window.config
        self.wallet = bal_window.wallet
        self.format_amount = bal_window.window.format_amount
        self.base_unit = bal_window.window.base_unit
        self.format_fiat_and_units = bal_window.window.format_fiat_and_units
        self.fx = bal_window.window.fx
        self.format_fee_rate = bal_window.window.format_fee_rate
        self.decimal_point = bal_window.window.get_decimal_point()
        self.base_unit_name = decimal_point_to_base_unit_name(self.decimal_point)
        self.setWindowTitle(_("Will Details"))
        self.setMinimumSize(670, 700)
        self.vlayout = QVBoxLayout()
        # CHANGELOG #131: the graphic view is now "Future" in the Will tab;
        # this window stays for the technical details. Only for the LIVE
        # will: an imported will is not the one the Future view draws
        # (shown again after a Merge, when the dialog shows the live will).
        self.view_note = QLabel(_(
            "Will plan view (technical details). The new graphic view is "
            "'Future' in the Will tab."
        ))
        self.view_note.setWordWrap(True)
        self.view_note.setStyleSheet("color:#555555;font-style:italic;")
        self.view_note.setVisible(not self._external_will)
        self.vlayout.addWidget(self.view_note)
        w = QWidget()
        hlayout = QHBoxLayout(w)

        b = QPushButton(_("Sign"))
        b.clicked.connect(self.ask_password_and_sign_transactions)
        hlayout.addWidget(b)

        # CHANGELOG #94: the same command names as the Will tab's menu.
        b = QPushButton(_("Broadcast will"))
        b.clicked.connect(self.broadcast_transactions)
        hlayout.addWidget(b)

        b = QPushButton(_("Export"))
        b.clicked.connect(self.export_will)
        hlayout.addWidget(b)
        b = QPushButton(_("Invalidate will"))
        b.clicked.connect(self.invalidate_will)
        hlayout.addWidget(b)
        self.merge_button = None
        if self._external_will:
            b = QPushButton(_("Merge"))
            b.clicked.connect(self.merge_will)
            hlayout.addWidget(b)
            self.merge_button = b
        self.vlayout.addWidget(w)

        self.paint_scroll_area()
        self.vlayout.addWidget(
            QLabel(_("Expiration date: {}").format(BalTimestamp(self.threshold)))
        )
        self.vlayout.addWidget(self.scrollbox)
        w = QWidget()
        hlayout = QHBoxLayout(w)
        hlayout.addWidget(
            QLabel(_("Valid Txs:{}").format(len(Will.only_valid_list(self.will))))
        )
        hlayout.addWidget(QLabel(_("Total Txs:{}").format(len(self.will))))
        self.vlayout.addWidget(w)
        self.setLayout(self.vlayout)

    def paint_scroll_area(self):
        self.scrollbox = QScrollArea()
        viewport = QWidget(self.scrollbox)
        self.willlayout = QVBoxLayout(viewport)
        self.detailsWidget = WillWidget(parent=self, will=self.will)
        self.willlayout.addWidget(self.detailsWidget)

        self.scrollbox.setWidget(viewport)
        viewport.setLayout(self.willlayout)

    def ask_password_and_sign_transactions(self):
        self.bal_window.ask_password_and_sign_transactions(
            callback=self.update, will=self.will if self._external_will else None
        )
        self.update()

    def broadcast_transactions(self):
        self.bal_window.broadcast_transactions(
            will=self.will if self._external_will else None
        )
        self.update()

    def export_will(self):
        self.bal_window.export_will(will=self.will if self._external_will else None)

    def invalidate_will(self):
        self.bal_window.invalidate_will(
            will=self.will if self._external_will else None
        )

    def merge_will(self):
        """Merge the imported will into the live will and switch to it.

        The merge is performed by :meth:`BalWindow.merge_will` (the same
        common method used by the tools-menu "Merge" action). Afterwards the
        dialog stops showing the read-only imported will and operates on the
        live wallet willitems directly, so any further Sign/Broadcast/Export/
        Invalidate action targets the saved will items, and the note about
        the Future view (#131) appears.
        """
        self.bal_window.merge_will(self.will)
        self._external_will = False
        self.will = self.bal_window.willitems
        self.threshold = _live_threshold(self.bal_window)
        if self.merge_button:
            self.merge_button.hide()
        self.view_note.setVisible(True)
        self.update()

    def update(self):
        if not self._external_will:
            self.will = self.bal_window.willitems
        pos = self.vlayout.indexOf(self.scrollbox)
        self.vlayout.removeWidget(self.scrollbox)
        self.paint_scroll_area()
        self.vlayout.insertWidget(pos, self.scrollbox)
        super().update()



class WillExecutorDialog(BalDialog, MessageBoxMixin):
    def __init__(self, bal_window, parent=None):
        if not parent:
            parent = bal_window.window
        BalDialog.__init__(self, parent, bal_window.bal_plugin)
        self.bal_plugin = bal_window.bal_plugin
        self.config = self.bal_plugin.config
        self.bal_window = bal_window
        self.willexecutors_list = Willexecutors.get_willexecutors(self.bal_plugin)
        # #134: called when the window closes (the summary box and the Check
        # window show the will-executors selected again as "to send").
        self.on_close_callbacks = []

        self.setWindowTitle(_("Will-Executor Service List"))
        self.setMinimumSize(1000, 200)

        # Lazy import to avoid a dialogs<->lists import cycle.
        from .lists import WillExecutorWidget
        vbox = QVBoxLayout(self)
        self.will_executor_list_widget = WillExecutorWidget(
            self, self.bal_window, self.willexecutors_list
        )
        vbox.addWidget(self.will_executor_list_widget)

    def is_hidden(self):
        return self.isMinimized() or self.isHidden()

    def show_or_hide(self):
        if self.is_hidden():
            self.bring_to_top()
        else:
            self.hide()

    def bring_to_top(self):
        self.show()
        # raise_() alone does not grab focus on some window managers (Windows);
        # activateWindow() ensures the dialog actually comes to the front.
        bring_to_front(self)

    def closeEvent(self, event):
        """Close, then run the ``on_close_callbacks`` (#134; never raise)."""
        event.accept()
        for callback in list(self.on_close_callbacks):
            try:
                callback()
            except Exception as e:
                _logger.error(f"will-executor window close callback: {e!r}")


class HeirsDialog(BalDialog, MessageBoxMixin):
    def __init__(self, bal_window, parent=None):
        if not parent:
            parent = bal_window.window
        BalDialog.__init__(self, parent, bal_window.bal_plugin)
        self.bal_plugin = bal_window.bal_plugin
        self.bal_window = bal_window

        self.setWindowTitle(_("Heirs"))
        self.setMinimumSize(800, 300)

        from .lists import HeirListWidget
        vbox = QVBoxLayout(self)
        self.heir_list_widget = HeirListWidget(bal_window, self)
        vbox.addWidget(self.heir_list_widget)

        btn_row = QHBoxLayout()
        new_heir_btn = QPushButton(_("New Heir"))
        new_heir_btn.clicked.connect(self._add_heir)
        btn_row.addWidget(new_heir_btn)

        import_btn = QPushButton(_("Import"))
        import_btn.clicked.connect(self._import_heirs)
        btn_row.addWidget(import_btn)

        export_btn = QPushButton(_("Export"))
        export_btn.clicked.connect(self._export_heirs)
        btn_row.addWidget(export_btn)

        btn_row.addStretch(1)
        vbox.addLayout(btn_row)

    def _add_heir(self):
        self.bal_window.new_heir_dialog()
        self.heir_list_widget.update()

    def _import_heirs(self):
        self.bal_window.import_heirs()
        self.heir_list_widget.update()

    def _export_heirs(self):
        self.bal_window.export_heirs()

    def closeEvent(self, event):
        event.accept()


# --------------------------------------------------------------------------- #
# QR / audio will transfer
# --------------------------------------------------------------------------- #

def export_filter_options():
    """The export filters shared by the File / QR / Audio export dialogs.

    Mirrors the historical All / Valid / Valid-NC choices of the "Export"
    file menu: All selects every will item, Valid only the valid ones and
    Valid NC the valid ones that are NOT yet fully signed (Complete).
    """
    return [
        (_("All"), lambda wi: True),
        (_("Valid"), lambda wi: wi.get_status("VALID")),
        (
            _("Valid NC"),
            lambda wi: wi.get_status("VALID") and not wi.get_status("COMPLETE"),
        ),
    ]


def filter_willitems(source, filters, filter_index):
    """The will items of ``source`` matched by ``filters[filter_index]``."""
    _label, fn = filters[filter_index]
    return {wid: wi for wid, wi in source.items() if fn(wi)}


def serialize_tx_list(willitems):
    """The serialized transaction strings of the given will items, sorted by txid."""
    items = sorted(willitems.values(), key=lambda wi: str(wi.tx.txid()))
    return [str(wi.tx) for wi in items]


def decode_will_payload(text) -> tuple[Any, Any]:
    """Autodetect: whole-will JSON or transaction list?

    Returns ``("will", dict_of_willitems_data)`` when ``text`` is a JSON
    object whose values are dicts containing a ``"tx"`` key (the whole-will
    format produced by :meth:`BalWindow.export_json_file` and friends).
    Otherwise returns ``("txs", [tx_strings])`` where the transaction
    strings were split on commas and/or newlines.
    """
    text = text.strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict) and data:
        if all(isinstance(v, dict) and "tx" in v for v in data.values()):
            return ("will", data)
    parts = [p for p in re.split(r"[,\r\n]+", text) if p.strip()]
    return ("txs", parts)


def audio_bitrates():
    """The transfer speeds (KB/sec) offered by the ``audio_modem`` plugin."""
    try:
        import amodem.config
    except Exception:
        return []
    return sorted(amodem.config.bitrates.keys())


def current_kbps(plugin):
    """The KB/sec the given plugin is configured with (1 when unknown)."""
    try:
        return int(round(plugin.modem_config.modem_bps / 1e3))
    except Exception:
        return 1


class BalQrImage(QWidget):
    """A widget that renders one QR code, scaled to its own size.

    Electrum's own ``QRCodeWidget`` hard-codes the LOW error-correction level,
    which is fine for a one-shot payload but risky for long multi-frame will
    transfers. This widget renders a fresh code on every paint with MEDIUM
    correction using Electrum's :func:`draw_qr` paint helper.
    """

    def __init__(self, text="", parent=None):
        QWidget.__init__(self, parent)
        self.text = text
        self.setMinimumSize(240, 240)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

    def set_text(self, text):
        self.text = text
        self.update()

    def paintEvent(self, event):
        # Imported lazily: qrcode is shipped with Electrum but it is not a Qt
        # widget, so keeping it out of the hub import keeps dialogs importable
        # even when qrcode itself is missing at import time.
        import qrcode

        qr = qrcode.QRCode(
            error_correction=qrcode.ERROR_CORRECT_M, border=2
        )
        qr.add_data(self.text)
        qr.make(fit=True)
        draw_qr(
            qr=qr, paint_device=self, is_enabled=True, min_boxsize=2
        )
        QWidget.paintEvent(self, event)


class BalQrExportWidget(QWidget):
    """Self-contained QR export page: view, navigation, autoplay, resolution.

    Renders the will transfer one QR code at a time. The user walks the
    frames with the Prev/Next arrows, can auto-advance at a chosen speed
    (with optional looping) and can change the "QR code size" preset live,
    which is the resolution: how many payload bytes each frame carries.
    The page (re)displays itself on :meth:`set_tx_strings`.
    """

    def __init__(self, chunk_size=CHUNK_PRESETS[0][1], parent=None, on_size_change=None):
        QWidget.__init__(self, parent)
        self.chunk_size = chunk_size
        self._on_size_change = on_size_change
        self.tx_strings = []
        self.transfer = ""
        self.frames = []
        self.index = 0
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_step)

        vbox = QVBoxLayout(self)

        self.intro_label = QLabel()
        vbox.addWidget(self.intro_label)

        self.qr_view = BalQrImage(parent=self)
        vbox.addWidget(self.qr_view)

        self.progress_label = QLabel()
        vbox.addWidget(self.progress_label)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton(_("Previous"))
        self.prev_btn.clicked.connect(self._prev)
        nav.addWidget(self.prev_btn)
        self.next_btn = QPushButton(_("Next"))
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)

        vbox.addLayout(nav)

        auto_row = QHBoxLayout()
        self.auto_btn = QPushButton(_("Auto"))
        self.auto_btn.setToolTip(
            _("Automatically advance through the QR codes.")
        )
        self.auto_btn.clicked.connect(self._toggle_auto)
        auto_row.addWidget(self.auto_btn)
        auto_row.addWidget(QLabel(_("QR codes per second:")))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 10)
        self.fps_spin.setValue(1)
        self.fps_spin.setSuffix(_(" /s"))
        auto_row.addWidget(self.fps_spin)
        self.loop_check = QCheckBox(_("Loop"))
        self.loop_check.setToolTip(
            _(
                "When the last QR code is reached, keep cycling from the "
                "first one instead of stopping."
            )
        )
        auto_row.addWidget(self.loop_check)
        auto_row.addStretch(1)
        vbox.addLayout(auto_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel(_("QR code size:")))
        self.size_combo = QComboBox()
        self.size_combo.addItems([_(label) for label, _budget in CHUNK_PRESETS])
        self.size_combo.setCurrentIndex(
            preset_index_for_chunk_size(self.chunk_size)
        )
        self.size_combo.currentIndexChanged.connect(self._on_chunk_change)
        size_row.addWidget(self.size_combo)
        size_row.addStretch(1)
        vbox.addLayout(size_row)

    def set_tx_strings(self, tx_strings):
        """Rebuild the transfer frames from the given serialized transactions."""
        self.tx_strings = list(tx_strings)
        self._stop_auto()
        self.transfer = encode_transfer(self.tx_strings, compress=False)
        self._refresh_frames()
        self._update_intro()
        self._render()

    @property
    def total_frames(self):
        return len(self.frames)

    def _refresh_frames(self):
        self.frames = bbqr_frames(
            self.transfer.encode("utf-8"), self.chunk_size, encoding="Z"
        )
        self.index = 0

    def _on_chunk_change(self, index):
        self._stop_auto()
        self.chunk_size = CHUNK_PRESETS[index][1]
        self._refresh_frames()
        self._render()
        if self._on_size_change is not None:
            self._on_size_change(self.chunk_size)

    def _toggle_auto(self):
        """Start/stop the automatic QR slideshow."""
        if self.auto_timer.isActive():
            self._stop_auto()
            return
        if len(self.frames) <= 1:
            return
        fps = self.fps_spin.value()
        if fps <= 0:
            return
        self.auto_btn.setText(_("Stop"))
        self.auto_timer.start(int(1000 / fps))

    def _stop_auto(self):
        if self.auto_timer.isActive():
            self.auto_timer.stop()
        self.auto_btn.setText(_("Auto"))

    def _auto_step(self):
        if self.index >= len(self.frames) - 1:
            # Reach the last code: loop back or stop the slideshow.
            if self.loop_check.isChecked():
                self.index = 0
                self._render()
                return
            self._stop_auto()
            return
        self._next()

    def _update_intro(self):
        self.intro_label.setText(
            _(
                "Scan the QR codes below with the will-opening device.\n"
                "{} codes carry the whole transfer (any order works, "
                "duplicates are ignored)."
            ).format(self.total_frames)
        )

    def _prev(self):
        if self.index > 0:
            self.index -= 1
            self._render()

    def _next(self):
        if self.index < len(self.frames) - 1:
            self.index += 1
            self._render()

    def _render(self):
        if not self.frames:
            return
        self.qr_view.set_text(self.frames[self.index])
        self.progress_label.setText(
            _("Frame {} of {}").format(self.index + 1, len(self.frames))
        )
        self.prev_btn.setEnabled(self.index > 0)
        self.next_btn.setEnabled(self.index < len(self.frames) - 1)


class WillExportDialog(BalDialog):
    """One window to export a will to a file, QR codes or audio.

    The export filter (All / Valid / Valid NC) sits at the top and applies to
    every option. Below it the user picks one of the three transports; each
    option shows its transport-specific settings: the file content mode
    (whole will item vs only the transactions), the QR resolution (frame
    size) and autoplay speed, and the audio KB/sec. If the ``audio_modem``
    plugin is missing only the audio option is disabled - file and QR stay
    usable.
    """

    MODE_FILE = 0
    MODE_QR = 1
    MODE_AUDIO = 2

    def __init__(self, bal_window, will=None, bal_plugin=None, initial_mode="file"):
        BalDialog.__init__(self, bal_window.window, bal_plugin, _("Export will"))
        self.bal_window = bal_window
        self.bal_plugin = bal_plugin
        self._source = will if will is not None else bal_window.willitems
        self._filters = export_filter_options()
        self._filter_index = 0
        try:
            chunk = int(bal_plugin.QR_CHUNK_SIZE.get())
        except Exception:
            chunk = CHUNK_PRESETS[0][1]
        if not self._selected_items():
            self.show_message(_("No will transaction to export."))
            self.close()
            return

        vbox = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(_("Export:")))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems([label for label, _fn in self._filters])
        self.filter_combo.currentIndexChanged.connect(self._on_filter_change)
        filter_row.addWidget(self.filter_combo)
        # Content scope: whole will items vs only the serialized transactions.
        # Applies uniformly to every transport (File / QR / Audio).
        self.content_check = QCheckBox(_("Whole will"))
        self.content_check.setChecked(True)
        self.content_check.setToolTip(
            _(
                "Export the full will items (all details/statuses) as JSON, "
                "or only the serialized transactions."
            )
        )
        self.content_check.toggled.connect(self._on_content_toggle)
        filter_row.addWidget(self.content_check)
        filter_row.addStretch(1)
        vbox.addLayout(filter_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(_("Send as:")))
        self.transport_group = QButtonGroup(self)
        self.transport_file = QRadioButton(_("File"))
        self.transport_qr = QRadioButton(_("QR Code"))
        self.transport_audio = QRadioButton(_("Audio"))
        self.transport_group.addButton(self.transport_file, self.MODE_FILE)
        self.transport_group.addButton(self.transport_qr, self.MODE_QR)
        self.transport_group.addButton(self.transport_audio, self.MODE_AUDIO)
        for rb in (self.transport_file, self.transport_qr, self.transport_audio):
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        vbox.addLayout(mode_row)
        self.transport_group.idClicked.connect(self._on_mode_clicked)

        self.stacked = QStackedWidget()
        self.file_page = self._build_file_page()
        self.stacked.addWidget(self.file_page)
        self.qr_page = BalQrExportWidget(
            chunk_size=chunk,
            on_size_change=self._remember_qr_size,
        )
        self.stacked.addWidget(self.qr_page)
        self.audio_page = self._build_audio_page()
        self.stacked.addWidget(self.audio_page)
        vbox.addWidget(self.stacked)

        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.close)
        vbox.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        # The audio transport is only offered when the audio_modem plugin is
        # available; otherwise its radio button is disabled (file and QR stay
        # usable).
        self.transport_audio.setEnabled(self._audio_available)

        mode_ids = {
            "file": self.MODE_FILE,
            "qr": self.MODE_QR,
            "audio": self.MODE_AUDIO,
        }
        mode = mode_ids.get(initial_mode, self.MODE_FILE)
        if mode == self.MODE_AUDIO and not self._audio_available:
            mode = self.MODE_FILE
        (self.transport_file, self.transport_qr, self.transport_audio)[
            mode
        ].setChecked(True)
        self.qr_page.set_tx_strings(self._payload_strings())
        self._on_mode_clicked(mode)
        self._update_info()

    def _build_file_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(
            QLabel(
                _(
                    "Export the will as a JSON file. Uncheck \"Whole will\" "
                    "above to export only the serialized transactions "
                    "(comma-separated)."
                )
            )
        )
        self.file_info_label = QLabel()
        v.addWidget(self.file_info_label)
        self.file_export_btn = QPushButton(_("Export…"))
        self.file_export_btn.clicked.connect(self._export_file)
        v.addWidget(self.file_export_btn, alignment=Qt.AlignmentFlag.AlignRight)
        return page

    def _build_audio_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.audio_warn_label = QLabel()
        self.audio_warn_label.setWordWrap(True)
        self._audio_plugin = self.bal_window.get_audio_modem_plugin()
        self.bitrates = audio_bitrates()
        available = self._audio_plugin is not None
        self._audio_available = available
        if available:
            self.audio_warn_label.hide()
        else:
            self.audio_warn_label.setText(
                _("Audio MODEM plugin is not available.")
            )
        v.addWidget(self.audio_warn_label)
        kbps_row = QHBoxLayout()
        kbps_row.addWidget(QLabel(_("Speed (KB/sec):")))
        self.kbps_combo = QComboBox()
        self.kbps_combo.addItems([str(x) for x in self.bitrates])
        current = current_kbps(self._audio_plugin)
        if self.bitrates and current in self.bitrates:
            self.kbps_combo.setCurrentIndex(self.bitrates.index(current))
        kbps_row.addWidget(self.kbps_combo)
        kbps_row.addStretch(1)
        v.addLayout(kbps_row)
        self.audio_info_label = QLabel()
        v.addWidget(self.audio_info_label)
        self.audio_send_btn = QPushButton(_("Send"))
        self.audio_send_btn.setEnabled(available)
        self.audio_send_btn.clicked.connect(self._send_audio)
        v.addWidget(self.audio_send_btn, alignment=Qt.AlignmentFlag.AlignRight)
        return page

    def _selected_items(self):
        return filter_willitems(self._source, self._filters, self._filter_index)

    def _remember_qr_size(self, size):
        """Persist the QR size chosen in the export page for the next export."""
        if self.bal_plugin is not None:
            try:
                self.bal_plugin.QR_CHUNK_SIZE.set(size)
            except Exception:
                pass

    def _payload_strings(self):
        """The data handed to the QR page, honouring the content scope.

        ``["<json>"]`` when "Whole will" is checked, otherwise the serialized
        transaction strings (the QR transfer wraps them independently).
        """
        items = self._selected_items()
        if self.content_check.isChecked():
            return [self._whole_will_json()]
        return serialize_tx_list(items)

    def _payload_text(self):
        """The raw audio payload, honouring the content scope."""
        if self.content_check.isChecked():
            return self._whole_will_json()
        return "\n".join(serialize_tx_list(self._selected_items()))

    def _whole_will_json(self):
        # Use Electrum's MyEncoder so the ``tx`` Transaction object (and any
        # datetime fields) are serialized the same way write_json_file does,
        # otherwise json.dumps raises "Object of type Transaction is not JSON
        # serializable".
        return json.dumps(
            {wid: wi.to_dict() for wid, wi in self._selected_items().items()},
            cls=MyEncoder,
        )

    def _on_filter_change(self, index):
        previous = self._filter_index
        self._filter_index = index
        if not self._selected_items():
            # The selection is empty under the new filter: revert and inform.
            self.filter_combo.blockSignals(True)
            self.filter_combo.setCurrentIndex(previous)
            self.filter_combo.blockSignals(False)
            self._filter_index = previous
            self.show_message(_("No will transaction matches the selected filter."))
            return
        if self.transport_qr.isChecked():
            self.qr_page.set_tx_strings(self._payload_strings())
        self._update_info()

    def _on_content_toggle(self, checked):
        if self.transport_qr.isChecked():
            self.qr_page.set_tx_strings(self._payload_strings())
        self._update_info()

    def _on_mode_clicked(self, mode):
        if mode == self.MODE_QR:
            self.qr_page.set_tx_strings(self._payload_strings())
            self.stacked.setCurrentWidget(self.qr_page)
        elif mode == self.MODE_AUDIO:
            self.stacked.setCurrentWidget(self.audio_page)
        else:
            self.stacked.setCurrentWidget(self.file_page)
        self._update_info()

    def _update_info(self):
        count = len(self._selected_items())
        noun = _("item(s)") if self.content_check.isChecked() else _("transaction(s)")
        self.file_info_label.setText(
            _("{} {} will be exported.").format(count, noun)
        )
        self.audio_info_label.setText(
            _("{} {} will be sent.").format(count, noun)
        )

    def _export_file(self):
        items = self._selected_items()
        if not items:
            self.show_message(_("No will transaction matches the selected filter."))
            return
        if self.content_check.isChecked():
            exporter = partial(self.bal_window.export_json_file, will=items)
            title = "will"
        else:
            exporter = partial(self.bal_window.export_tx_file, will=items)
            title = "will_tx"
        try:
            export_meta_gui(self.bal_window.window, title, exporter)
        except Exception as e:
            self.show_error(str(e))
            raise e

    def _send_audio(self):
        try:
            kbps = int(self.kbps_combo.currentText())
        except ValueError:
            self.show_error(_("Invalid audio speed."))
            return
        if not self.bitrates or kbps not in self.bitrates:
            self.show_error(_("Invalid audio speed."))
            return
        items = self._selected_items()
        if not items:
            self.show_message(_("No will transaction matches the selected filter."))
            return
        try:
            self.bal_window.set_audio_modem_bitrate(kbps)
        except Exception as e:
            self.show_error(str(e))
            return
        payload = self._payload_text()
        try:
            self.bal_window._audio_send_payload(payload)
        except Exception as e:
            log_error(e, self)
            self.show_error(str(e))
            return
        self.close()


class WillImportDialog(BalDialog):
    """One window to import a will from a file, QR codes or audio.

    Mirrors :class:`WillExportDialog`: the user picks one of the three
    transports. File imports are shown in a read-only
    :class:`WillDetailDialog`; QR and audio captures go through the shared
    review+sign wizard (:class:`WillTxReviewSignDialog`). The live will is
    never touched by any of the three flows.
    """

    MODE_FILE = 0
    MODE_QR = 1
    MODE_AUDIO = 2

    def __init__(self, bal_window, bal_plugin=None):
        BalDialog.__init__(self, bal_window.window, bal_plugin, _("Import will"))
        self.bal_window = bal_window
        self.bal_plugin = bal_plugin

        vbox = QVBoxLayout(self)
        intro = QLabel(
            _(
                "Choose how the will was exported: from a file, QR codes or "
                "audio.\nThe import never touches the live will."
            )
        )
        intro.setWordWrap(True)
        vbox.addWidget(intro)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(_("Import from:")))
        self.transport_group = QButtonGroup(self)
        self.transport_file = QRadioButton(_("File"))
        self.transport_qr = QRadioButton(_("QR Code"))
        self.transport_audio = QRadioButton(_("Audio"))
        self.transport_group.addButton(self.transport_file, self.MODE_FILE)
        self.transport_group.addButton(self.transport_qr, self.MODE_QR)
        self.transport_group.addButton(self.transport_audio, self.MODE_AUDIO)
        for rb in (self.transport_file, self.transport_qr, self.transport_audio):
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        vbox.addLayout(mode_row)
        self.transport_group.idClicked.connect(self._on_mode_clicked)

        self.stacked = QStackedWidget()
        self.file_page = self._build_file_page()
        self.stacked.addWidget(self.file_page)
        self.qr_page = BalQrImportWidget(
            bal_window, bal_plugin, close_cb=self.close
        )
        self.stacked.addWidget(self.qr_page)
        self.audio_page = self._build_audio_page()
        self.stacked.addWidget(self.audio_page)
        vbox.addWidget(self.stacked)

        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.close)
        vbox.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        # The audio transport is only offered when the audio_modem plugin is
        # available; otherwise its radio button is disabled.
        self.transport_audio.setEnabled(self._audio_available)

        self.transport_file.setChecked(True)
        self._on_mode_clicked(self.MODE_FILE)

    def _build_file_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        lbl = QLabel(
            _(
                "Import the JSON will file written by the Export ▶ File "
                "option. The will opens in a read-only preview window."
            )
        )
        lbl.setWordWrap(True)
        v.addWidget(lbl)
        btn = QPushButton(_("Choose will file…"))
        btn.clicked.connect(self._import_file)
        v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return page

    def _build_audio_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.audio_warn_label = QLabel()
        self.audio_warn_label.setWordWrap(True)
        self._audio_plugin = self.bal_window.get_audio_modem_plugin()
        self.bitrates = audio_bitrates()
        available = self._audio_plugin is not None
        self._audio_available = available
        if available:
            self.audio_warn_label.hide()
        else:
            self.audio_warn_label.setText(
                _("Audio MODEM plugin is not available.")
            )
        v.addWidget(self.audio_warn_label)
        kbps_row = QHBoxLayout()
        kbps_row.addWidget(QLabel(_("Speed (KB/sec):")))
        self.kbps_combo = QComboBox()
        self.kbps_combo.addItems([str(x) for x in self.bitrates])
        current = current_kbps(self._audio_plugin)
        if self.bitrates and current in self.bitrates:
            self.kbps_combo.setCurrentIndex(self.bitrates.index(current))
        kbps_row.addWidget(self.kbps_combo)
        kbps_row.addStretch(1)
        v.addLayout(kbps_row)
        self.status_label = QLabel(_("Waiting for the audio transfer…"))
        v.addWidget(self.status_label)
        btns = QHBoxLayout()
        self.receive_btn = QPushButton(_("Receive by audio…"))
        self.receive_btn.setEnabled(available)
        self.receive_btn.clicked.connect(self._audio_receive)
        btns.addWidget(self.receive_btn)
        btns.addStretch(1)
        v.addLayout(btns)
        return page

    def _on_mode_clicked(self, mode):
        if mode == self.MODE_QR:
            self.stacked.setCurrentWidget(self.qr_page)
        elif mode == self.MODE_AUDIO:
            self.stacked.setCurrentWidget(self.audio_page)
        else:
            self.stacked.setCurrentWidget(self.file_page)

    def _import_file(self):
        self.bal_window.import_will_into_details()

    def _audio_receive(self):
        """Start a receiver thread on the chosen speed and finish the import."""
        try:
            kbps = int(self.kbps_combo.currentText())
        except ValueError:
            self.show_error(_("Invalid audio speed."))
            return
        if not self.bitrates or kbps not in self.bitrates:
            self.show_error(_("Invalid audio speed."))
            return
        try:
            self.bal_window.set_audio_modem_bitrate(kbps)
        except Exception as e:
            self.show_error(str(e))
            return

        try:
            import amodem  # noqa: F401  # type: ignore  (guaranteed by is_available)
        except Exception as e:
            self.show_error(str(e))
            return
        plugin = self._audio_plugin
        self.status_label.setText(_("Receiving…"))
        self.receive_btn.setEnabled(False)

        def receiver_thread():
            with plugin._audio_interface() as interface:
                src = interface.recorder()
                dst = io.BytesIO()
                amodem.main.recv(config=plugin.modem_config, src=src, dst=dst)
                return dst.getvalue()

        def on_success(blob):
            self.receive_btn.setEnabled(True)
            if not blob:
                return
            try:
                text = zlib.decompress(blob).decode("ascii")
            except Exception as e:
                self.show_error(str(e))
                return
            if not text.strip():
                self.show_error(_("No transaction data received."))
                return
            self.close()
            _complete_import(
                self.bal_window,
                self.bal_plugin,
                text,
                show_error=self.show_error,
                show_warning=self.show_warning,
                close=lambda: None,
            )

        def on_error(exec_info):
            self.receive_btn.setEnabled(True)
            log_error(exec_info, self)

        WaitingDialog(
            self,
            _("Waiting for audio ({:.1f} kbps)…").format(
                plugin.modem_config.modem_bps / 1e3
            ),
            receiver_thread,
            on_success,
            on_error,
        )


def _local_validity_pass(bal_window, items):
    """Local, wallet-only validity check (no server, no expiry raise).

    Mirrors the check that :meth:`BalWindow.merge_will` runs after a merge so
    the QR / audio import and the file-merge paths behave identically.
    """
    date_to_check = getattr(bal_window, "date_to_check", None)
    if date_to_check is None:
        date_to_check = resolve_date_to_check(
            bal_window.bal_plugin.is_basic_mode(),
            bal_window.will_settings,
        )
    history_label = bal_window.bal_plugin.HISTORY_LABEL.get()
    try:
        Will.add_willtree(items)
        all_utxos = Util.get_available_utxos(
            bal_window.wallet,
            history_label,
            Will.get_min_locktime(items, default_value=date_to_check),
        )
        Will.check_invalidated(
            items, Will.utxos_strs(all_utxos), bal_window.wallet
        )
        Will.search_rai(
            Will.get_all_inputs(items, only_valid=True),
            all_utxos,
            items,
            bal_window.wallet,
        )
        Will.check_signatures(items, bal_window.wallet)
    except Exception as e:
        log_error(e, bal_window)


def _complete_import(bal_window, bal_plugin, payload, *, show_error, show_warning, close):
    """Shared tail of the QR / audio import flows.

    Autodetects the transferred ``payload`` with :func:`decode_will_payload`:
    a whole-will (JSON of willitems) is shown read-only in a
    :class:`WillDetailDialog`; a transaction list is parsed into local
    :class:`WillItem` objects, run through the local validity pass and handed
    to the review+sign wizard. The live will is never touched.
    ``show_error`` / ``show_warning`` / ``close`` are callbacks supplied by
    the per-transport dialog.
    """
    kind, data = decode_will_payload(payload)
    if kind == "will":
        return _complete_import_will(
            bal_window, bal_plugin, data, show_error=show_error, close=close
        )
    return _complete_import_txs(
        bal_window, bal_plugin, data, show_error=show_error, show_warning=show_warning, close=close
    )


def _complete_import_will(bal_window, bal_plugin, data, *, show_error, close):
    """Build local WillItems from whole-will JSON data and open WillDetailDialog."""
    items = {}
    for wid, d in data.items():
        try:
            d = dict(d)
            d["tx"] = tx_from_any(d["tx"])
            items[wid] = WillItem(d, _id=wid, wallet=bal_window.wallet)
        except Exception as e:
            show_error(_("Could not parse a transferred will item: {}").format(e))
            return
    Will.normalize_will(items, bal_window.wallet)
    for wi in items.values():
        wi.set_status("IMPORTED", True)
    close()
    from .dialogs import WillDetailDialog

    dlg = WillDetailDialog(bal_window, will=items)
    show_on_top(dlg)


def _complete_import_txs(bal_window, bal_plugin, tx_strings, *, show_error, show_warning, close):
    """Build local WillItems from serialized transactions and open the review wizard."""
    items = {}
    for s in tx_strings:
        try:
            wi = WillItem({"tx": s}, wallet=bal_window.wallet)
        except Exception as e:
            show_error(
                _("Could not parse a transferred transaction: {}").format(e)
            )
            return
        items[wi._id] = wi
    Will.normalize_will(items, bal_window.wallet)
    _local_validity_pass(bal_window, items)
    for wi in items.values():
        wi.set_status("IMPORTED", True)
    valid = [wid for wid in items if items[wid].get_status("VALID")]
    if not valid:
        show_error(
            _(
                "The imported will contains no valid transaction in this "
                "wallet."
            )
        )
        close()
        return
    close()
    skipped = len(items) - len(valid)
    if skipped:
        show_warning(
            _(
                "{} imported transaction(s) are not valid in this wallet "
                "and were skipped."
            ).format(skipped)
        )
    wizard = WillTxReviewSignDialog(
        bal_window, will=items, bal_plugin=bal_plugin
    )
    if wizard.aborted:
        return
    show_on_top(wizard)


def qr_import_accept_frame(
    state, fmt, session_key, frame_total, index, payload, stable_reads=2
):
    """Change/detection policy for hands-free sequential QR frame import.

    The continuous camera loop reads the same displayed code many times per
    second, so we must decide when a scanned frame is worth storing:

    * frames whose ``(index, payload)`` identity differs from the last
      accepted one only count as ``pending`` until the same identity has been
      seen ``stable_reads`` times in a row -- this mirrors the export-side
      slideshow pacing and swallows transition artifacts;
    * re-reading the currently accepted frame is ``ignore``d;
    * a frame whose ``session_key`` (transfer identity, e.g. ``"bbqr:Z:B:3"``)
      contradicts the transfer already being built is a ``reset`` (a
      different transfer was presented).

    ``state`` is a mutable mapping with keys ``last_index``, ``last_payload``,
    ``pending_index``, ``pending_payload``, ``pending_count`` and ``key``.
    Returns one of ``"reset"``, ``"accept"``, ``"pending"``, ``"ignore"``.
    """
    current_key = state.get("key")
    if current_key and session_key != current_key:
        return "reset"
    if index == state.get("last_index") and payload == state.get("last_payload"):
        return "ignore"
    if index == state.get("pending_index") and payload == state.get("pending_payload"):
        state["pending_count"] = state.get("pending_count", 0) + 1
    else:
        state["pending_index"] = index
        state["pending_payload"] = payload
        state["pending_count"] = 1
    if state["pending_count"] >= stable_reads:
        state["key"] = session_key
        state["last_index"] = index
        state["last_payload"] = payload
        state["pending_index"] = None
        state["pending_payload"] = None
        state["pending_count"] = 0
        return "accept"
    return "pending"


class BalQrImportWidget(QWidget):
    """Self-contained QR import page: camera or manual frame capture.

    Frames are captured one by one from the camera (or typed manually). The
    first frame fixes the total frame count and the transfer compression flag;
    the slot grid shows which frames are still missing. When every frame is
    present the "Review and Sign" button assembles the transfer, decodes it
    into transactions and hands them to :class:`WillTxReviewSignDialog`. All
    work happens on a local copy; the live will is never touched.
    """

    def __init__(self, bal_window, bal_plugin, parent=None, close_cb=None):
        QWidget.__init__(self, parent)
        self.bal_window = bal_window
        self.bal_plugin = bal_plugin
        self.close_cb = close_cb or (lambda: None)
        self._session = None
        self._fmt = None
        self._key = None
        self.frames = {}
        self.total = 0
        self.slot_widgets = {}
        self._scanning = False

        # Continuous camera session (started on demand, then hands-free).
        self._reader = None
        self._camera = None
        self._capture_session = None
        self._video_sink = None
        self._latest_image = None
        self._finish_pending = False
        self._debounce = {
            "last_index": None,
            "last_payload": None,
            "pending_index": None,
            "pending_payload": None,
            "pending_count": 0,
        }
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(200)  # ~5 frames analyzed per second
        self._scan_timer.timeout.connect(self._on_scan_tick)

        vbox = QVBoxLayout(self)
        intro = QLabel(
            _(
                "Show the will QR codes to the camera, one after another.\n"
                "After the first code, every new code is captured "
                "automatically.\nThe first frame sets the total number of "
                "codes; duplicates are ignored."
            )
        )
        intro.setWordWrap(True)
        vbox.addWidget(intro)

        self.status_label = QLabel(_("Waiting for the first frame…"))
        vbox.addWidget(self.status_label)

        # Slot grid inside a scroll area (a large will can need many frames).
        self.slot_widget = QWidget()
        self.slot_grid = QGridLayout(self.slot_widget)
        self.slot_grid.setSpacing(4)
        self.slot_area = QScrollArea()
        self.slot_area.setWidget(self.slot_widget)
        self.slot_area.setWidgetResizable(True)
        self.slot_area.setMaximumHeight(180)
        self.slot_area.setVisible(False)
        vbox.addWidget(self.slot_area)

        manual = QHBoxLayout()
        self.manual_edit = QLineEdit()
        self.manual_edit.setPlaceholderText(
            _("…or paste/type the frame text here")
        )
        self.manual_edit.returnPressed.connect(self._add_from_manual)
        manual.addWidget(self.manual_edit)
        manual_btn = QPushButton(_("Add frame"))
        manual_btn.clicked.connect(self._add_from_manual)
        manual.addWidget(manual_btn)
        vbox.addLayout(manual)

        buttons = QHBoxLayout()
        self.scan_btn = QPushButton(_("Scan with camera…"))
        self.scan_btn.setToolTip(
            _(
                "Start the live camera. While it runs, every new QR code "
                "shown to the camera is captured automatically."
            )
        )
        self.scan_btn.clicked.connect(self._toggle_scan)
        buttons.addWidget(self.scan_btn)
        self.reset_btn = QPushButton(_("Reset"))
        self.reset_btn.clicked.connect(self._reset_all)
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        vbox.addLayout(buttons)

        bottom = QHBoxLayout()
        self.review_btn = QPushButton(_("Review and Sign…"))
        self.review_btn.setEnabled(False)
        self.review_btn.clicked.connect(self._review_and_sign)
        bottom.addWidget(self.review_btn)
        bottom.addStretch(1)
        vbox.addLayout(bottom)

    # -- frame handling -------------------------------------------------------

    def _add_from_manual(self):
        text = self.manual_edit.text().strip()
        if text:
            self.manual_edit.clear()
            self._add_frame(text, manual=True)

    def _reset_transfer(self, fmt, frame_total):
        """Wipe the open transfer because an incompatible frame arrived."""
        self._reset_all()
        self.show_warning(
            _(
                "The scanned code belongs to a different transfer ({} "
                "frames, {}). The import was reset; scan the first code again."
            ).format(frame_total, format_name(fmt))
        )

    def _add_frame(self, frame_text, manual=False):
        """Feed one scanned/pasted frame string into the receive session.

        ``manual`` controls whether parse/malformed failures raise a visible
        error (the camera loop fails silently and just keeps scanning).
        """
        try:
            fmt, key, frame_total, index = parse_for_detection(frame_text)
        except AnimatedQrError as e:
            if manual:
                self.show_error(str(e))
            return "error"
        if self._key is not None and key != self._key:
            self._reset_transfer(fmt, frame_total)
            return "reset"
        session = self._session if self._session is not None else AnimatedQrSession()
        try:
            status = session.add_part(frame_text)
        except TransferConflictError:
            self._reset_transfer(fmt, frame_total)
            return "reset"
        except SessionLimitError as e:
            self.show_error(str(e))
            self._reset_all()
            return "error"
        except AnimatedQrError as e:
            if manual:
                self.show_error(str(e))
            return "error"
        self.total = session.total
        if self._session is None:
            self._session = session
            self._fmt = fmt
            self._key = key
            self._init_slots()
        if status == "ok":
            # Slot grid is always 1-based even for the 0-based BBQR indexes.
            slot = index + 1
            self.frames[slot] = frame_text
        self._update_slots()
        self._update_status()
        return status

    def show_message(self, msg):
        """Messagebox shim (this widget is not a dialog)."""
        MessageBoxMixin.show_message(self, msg)

    def show_warning(self, msg):
        """Warning shim (this widget is not a dialog)."""
        MessageBoxMixin.show_warning(self, msg)

    def show_error(self, msg):
        """Error shim (this widget is not a dialog)."""
        MessageBoxMixin.show_error(self, msg)

    def _init_slots(self):
        while self.slot_grid.count():
            item = self.slot_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            self.slot_grid.removeItem(item)
        self.slot_widgets = {}
        for index in range(1, self.total + 1):
            b = QPushButton(str(index))
            b.setEnabled(False)
            row = (index - 1) // 8
            col = (index - 1) % 8
            self.slot_grid.addWidget(b, row, col)
            self.slot_widgets[index] = b
        self.slot_area.setVisible(True)

    def _update_slots(self):
        for index, b in self.slot_widgets.items():
            present = index in self.frames
            b.setStyleSheet(
                "QPushButton{background-color:#90ee90;}" if present else ""
            )

    def _update_status(self):
        session = self._session
        have = len(self.frames)
        done = session is not None and session.done and have >= self.total
        if done:
            self.status_label.setText(
                _("All {} frames stored.").format(self.total)
            )
            self.review_btn.setEnabled(True)
            self._maybe_auto_finish()
        else:
            self.status_label.setText(
                _("Stored {} of {} frames.").format(have, self.total)
            )
            self.review_btn.setEnabled(False)

    def _reset_all(self):
        self._stop_scan()
        self._finish_pending = False
        self._session = None
        self._fmt = None
        self._key = None
        self.frames = {}
        self.total = 0
        self._reset_debounce()
        if self.slot_widgets:
            for b in self.slot_widgets.values():
                b.deleteLater()
        self.slot_widgets = {}
        self.slot_area.setVisible(False)
        self.review_btn.setEnabled(False)
        self.status_label.setText(_("Waiting for the first frame…"))

    # -- capture --------------------------------------------------------------

    def _reset_debounce(self):
        self._debounce.update(
            {
                "key": None,
                "last_index": None,
                "last_payload": None,
                "pending_index": None,
                "pending_payload": None,
                "pending_count": 0,
            }
        )

    def _toggle_scan(self):
        if self._scanning:
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self):
        """Open the camera and start the continuous, hands-free frame loop."""
        if self._scanning:
            return
        try:
            from electrum.qrreader import get_qr_reader
            from PyQt6.QtMultimedia import (
                QCamera,
                QMediaCaptureSession,
                QMediaDevices,
                QVideoSink,
            )
        except Exception as e:
            self.show_error(str(e))
            return
        try:
            self._reader = get_qr_reader()
        except Exception as e:
            self.show_error(str(e))
            return

        device = QMediaDevices.defaultVideoInput()
        if not device or device.isNull():
            self.show_error(
                _("Cannot start QR scanner, no usable camera found.")
            )
            return

        self._scanning = True
        self.scan_btn.setText(_("Stop scanning"))
        self._finish_pending = False
        self._reset_debounce()

        try:
            self._camera = QCamera(device)
            self._camera.errorOccurred.connect(self._on_camera_error)
            self._capture_session = QMediaCaptureSession()
            self._capture_session.setCamera(self._camera)
            self._video_sink = QVideoSink(self)
            # QVideoSink notifies new frames via videoFrameChanged (videoFrame
            # is the frame *getter*, not a signal).
            self._video_sink.videoFrameChanged.connect(self._on_video_frame)
            self._capture_session.setVideoSink(self._video_sink)
            self._camera.start()
            self._scan_timer.start()
        except Exception as e:
            self._stop_scan()
            self.show_error(str(e))

    def _stop_scan(self):
        """Release the camera and the continuous loop."""
        self._scanning = False
        self._scan_timer.stop()
        if self._camera is not None:
            try:
                self._camera.errorOccurred.disconnect(self._on_camera_error)
            except (RuntimeError, TypeError, AttributeError):
                pass
            self._camera.stop()
        if self._video_sink is not None:
            try:
                self._video_sink.videoFrameChanged.disconnect(self._on_video_frame)
            except (RuntimeError, TypeError, AttributeError):
                pass
        self._camera = None
        self._capture_session = None
        self._video_sink = None
        self._reader = None
        self._latest_image = None
        self._reset_debounce()
        self.scan_btn.setText(_("Scan with camera…"))

    def _on_camera_error(self, error, error_str):
        # A failed camera should not silently drop the hands-free session.
        if self._scanning:
            self._stop_scan()
            self.show_error(_("Camera error: {}").format(error_str or error))

    def _on_video_frame(self, video_frame):
        if self._scanning and video_frame.isValid():
            self._latest_image = video_frame.toImage()

    def _on_scan_tick(self):
        """Analyze the latest camera frame (~5 times per second)."""
        image = self._latest_image
        self._latest_image = None
        if image is None or self._reader is None or not self._scanning:
            return
        from PyQt6.QtGui import QImage

        try:
            gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
        except Exception:
            return
        try:
            results = self._reader.read_qr_code(
                gray.constBits().__int__(),
                gray.sizeInBytes(),
                gray.bytesPerLine(),
                gray.width(),
                gray.height(),
            )
        except Exception:
            return
        if results:
            self._handle_scanned_text(results[0].data)

    def _handle_scanned_text(self, text):
        """Route a decoded QR string through the change/detection policy."""
        try:
            fmt, key, frame_total, index = parse_for_detection(text)
        except AnimatedQrError:
            return
        decision = qr_import_accept_frame(
            self._debounce, fmt, key, frame_total, index, text
        )
        if decision == "pending":
            return
        if decision == "reset":
            self._reset_all()
            self._finish_pending = False
            self.show_warning(
                _(
                    "The scanned code belongs to a different transfer ({} "
                    "frames, {}). The import was reset; show the first code "
                    "again."
                ).format(frame_total, format_name(fmt))
            )
            return
        if decision == "accept":
            self._add_frame(text, manual=False)

    def _maybe_auto_finish(self):
        if self._finish_pending:
            return
        if not self._scanning:
            return
        session = self._session
        if not self.frames or not self.total:
            return
        if session is None or not session.done:
            return
        self._finish_pending = True
        self._stop_scan()
        # Defer so the widget repaints before the review dialog takes over.
        QTimer.singleShot(0, self._review_and_sign)

    def hideEvent(self, event):
        # Leaving the QR page (or closing the dialog) must release the camera.
        self._stop_scan()
        super().hideEvent(event)

    # -- finish ---------------------------------------------------------------

    def _review_and_sign(self):
        session = self._session
        if session is None or not session.done:
            return
        try:
            transfer, compressed = session.resolve()
            parts = decode_transfer(transfer, compressed)
        except AnimatedQrError as e:
            self.show_error(str(e))
            self._reset_all()
            return
        if not parts:
            self.show_error(_("The transferred will contains no transactions."))
            return
        # Join the frames back into an opaque payload; _complete_import
        # autodetects whether it is a whole will or a transaction list.
        self._finish_import("\n".join(parts))

    def _finish_import(self, payload):
        """Hand the transferred payload to the shared import tail."""
        _complete_import(
            self.bal_window,
            self.bal_plugin,
            payload,
            show_error=self.show_error,
            show_warning=self.show_warning,
            close=self.close_cb,
        )

class WillTxReviewSignDialog(BalDialog):
    """Per-transaction review + sign wizard for an imported will.

    Walks the (valid) imported transactions one at a time showing outputs,
    total outputs and fees, with Sign / Skip / Cancel per page. All signing
    runs on the local copy of the imported will; the live will and the wallet
    history are never touched. The final page offers to export the signed
    transactions as a file and/or as QR codes.
    """

    def __init__(self, bal_window, will=None, bal_plugin=None):
        BalDialog.__init__(
            self, bal_window.window, bal_plugin, _("Review and sign imported will")
        )
        self.bal_window = bal_window
        self.items = will if will is not None else bal_window.willitems
        self.txids = sorted(Will.only_valid(self.items))
        self.aborted = False
        self.i = 0
        if not self.txids:
            self.aborted = True
            self.close()
            return
        self.password = bal_window.get_wallet_password(
            message=_(
                "Enter your wallet password to sign the imported transactions."
            )
        )
        if self.password is False:
            self.aborted = True
            self.close()
            return

        vbox = QVBoxLayout(self)
        self.stack = QStackedWidget(self)
        self.summary_page = self._build_summary_page()
        # Index 0 = summary page; review pages start at index 1.
        self.stack.addWidget(self.summary_page)
        self.review_pages = []
        for _txid in self.txids:
            page = self._build_review_page()
            self.stack.addWidget(page)
            self.review_pages.append(page)
        vbox.addWidget(self.stack)
        self.stack.setCurrentIndex(1)
        self._render()

    # -- page builders ---------------------------------------------------------

    def _build_review_page(self):
        page = QWidget()
        vbox = QVBoxLayout(page)
        header = QLabel()
        vbox.addWidget(header)
        txid_label = QLabel()
        txid_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        vbox.addWidget(txid_label)
        outputs_label = QLabel()
        outputs_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        vbox.addWidget(outputs_label)
        totals_label = QLabel()
        vbox.addWidget(totals_label)

        row = QHBoxLayout()
        sign_btn = QPushButton(_("Sign"))
        sign_btn.clicked.connect(self._sign_current)
        row.addWidget(sign_btn)
        skip_btn = QPushButton(_("Skip"))
        skip_btn.clicked.connect(self._advance)
        row.addWidget(skip_btn)
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.close)
        row.addWidget(cancel_btn)
        row.addStretch(1)
        vbox.addLayout(row)
        return page

    def _build_summary_page(self):
        page = QWidget()
        vbox = QVBoxLayout(page)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        vbox.addWidget(self.summary_label)
        save_btn = QPushButton(_("Save signed file…"))
        save_btn.clicked.connect(self._save_signed)
        vbox.addWidget(save_btn)
        self.qr_btn = QPushButton(_("Show signed QR…"))
        self.qr_btn.clicked.connect(self._show_signed_qr)
        vbox.addWidget(self.qr_btn)
        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.close)
        vbox.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        return page

    # -- rendering -------------------------------------------------------------

    def _page_index(self):
        return 0 if self.i >= len(self.txids) else self.i + 1

    def _render(self):
        if self.i >= len(self.txids):
            self._enter_summary()
            return
        txid = self.txids[self.i]
        wi = self.items[txid]
        tx = wi.tx
        page = self.review_pages[self.i]

        headers = page.findChildren(QLabel)
        headers[0].setText(
            _("Transaction {} of {}").format(self.i + 1, len(self.txids))
        )
        headers[1].setText(_("TXID: {}").format(txid))
        lines = []
        for o in tx.outputs():
            value = o.value if o.value is not None else _("unknown")
            if isinstance(value, int):
                value_s = self.bal_window.window.format_amount_and_units(value)
            else:
                value_s = value
            lines.append("{}   {}".format(o.get_ui_address_str(), value_s))
        headers[2].setText("\n".join(lines) if lines else _("(no outputs)"))
        total_out = sum(
            (o.value or 0) for o in tx.outputs() if isinstance(o.value, int)
        )
        fee = None
        try:
            iv = tx.input_value()
            if isinstance(iv, int):
                fee = iv - total_out
        except Exception:
            fee = None
        if fee is not None:
            fee_s = self.bal_window.window.format_amount_and_units(fee)
        else:
            fee_s = _("unknown (partial transaction)")
        headers[3].setText(
            _("Total outputs: {}\nFee: {}").format(
                self.bal_window.window.format_amount_and_units(total_out), fee_s
            )
        )
        self.stack.setCurrentIndex(self._page_index())

    def _enter_summary(self):
        signed = sum(
            1 for txid in self.txids if self.items[txid].get_status("COMPLETE")
        )
        self.summary_label.setText(
            _(
                "Signed {} of {} transactions.\n\nSave a signed file to carry "
                "to the broadcast device, or show the signed transactions as "
                "QR codes."
            ).format(signed, len(self.txids))
        )
        self.qr_btn.setEnabled(signed > 0)
        self.stack.setCurrentIndex(0)

    # -- actions ---------------------------------------------------------------

    def _sign_current(self):
        """Sign the transaction on this page, then go to the next one.

        #157: the signed copy is stored in the imported will's item by the
        engine (:func:`bal.engine.sign.sign_will_item`). Before, it was
        dropped here and only COMPLETE was set, so "Save signed file…" wrote
        the unsigned transactions.
        """
        txid = self.txids[self.i]
        try:
            self.bal_window._prepare_and_sign_tx(self.items, txid, self.password)
        except Exception as e:
            log_error(e, self)
            self.show_error(_("Could not sign the transaction: {}").format(e))
            return
        self._advance()

    def _advance(self):
        self.i += 1
        self._render()

    def _save_signed(self):
        data = {wid: wi.to_dict() for wid, wi in self.items.items()}

        def _do_save(path):
            try:
                write_json_file(path, data)
            except Exception as e:
                self.show_error(str(e))
                return
            self.show_message(_("Signed will saved."))

        export_meta_gui(self.bal_window.window, "will_signed.json", _do_save)

    def _show_signed_qr(self):
        signed = {
            wid: wi
            for wid, wi in self.items.items()
            if wid in self.txids and wi.get_status("COMPLETE")
        }
        if not signed:
            self.show_message(_("No signed transaction to show."))
            return
        d = WillExportDialog(
            self.bal_window, will=signed, bal_plugin=self.bal_plugin, initial_mode="qr"
        )
        show_on_top(d)

