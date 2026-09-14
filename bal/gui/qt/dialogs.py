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

import io
import json
import re
import zlib
from typing import TYPE_CHECKING

from electrum.util import MyEncoder

from ...core.animated_qr import (
    AnimatedQrError,
    AnimatedQrSession,
    SessionLimitError,
    TransferConflictError,
    bbqr_frames,
    format_name,
    parse_for_detection,
    ur1_frames,
    ur2_frames,
)
from ...core.checkalive import CheckAliveError, resolve_date_to_check
from ...core.qrtransfer import (
    CHUNK_PRESETS,
    MissingFramesError,
    QrTransferError,
    decode_transfer,
    encode_transfer,
    encode_transfer_best,
    preset_index_for_chunk_size,
    split_frames,
)
from ...core.reminders import build_ics_reminders
from .calendar import BalCalendarButton
from .common import (
    HEIR_DUST_AMOUNT,
    HEIR_REAL_AMOUNT,
    AmountException,
    Any,
    BalTimestamp,
    BalanceTooLowException,
    BestEffortRequestFailed,
    Buttons,
    Callable,
    CancelButton,
    HeirAmountIsDustException,
    HeirChangeException,
    HeirNotFoundException,
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
    TxFeesChangedException,
    Util,
    WaitingDialog,
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
from .widgets import (
    WillSettingsWidget,
    WillWidget,
)

if TYPE_CHECKING:
    from .window import BalWindow

# NOTE: list views (HeirListWidget, PreviewList, WillExecutorWidget) are
# imported lazily where needed to avoid a dialogs<->lists import cycle.


# Animated-QR formats beyond the legacy BAL QR. Combined with the format
# selector in :class:`BalQrExportWidget` they give the export page
# interoperable BC-UR v1/v2 and BBQR output while keeping BAL QR as the
# default (and the only format understood by older plugin versions).
ANIMATED_QR_FORMATS = ("ur1", "ur2", "bbqr")


def encode_animated_frames(transfer, fmt, budget_chars):
    """Encode the transfer text into the given animated-QR format.

    ``transfer`` is the BAL QR transfer text (str). ``budget_chars`` is the
    maximum length of one frame (the exporter's "QR code size" preset).
    """
    payload = transfer.encode("utf-8")
    if fmt == "ur1":
        return ur1_frames(payload, budget_chars)
    if fmt == "ur2":
        return ur2_frames(payload, budget_chars)
    if fmt == "bbqr":
        return bbqr_frames(payload, budget_chars, encoding="Z")
    raise QrTransferError("unknown animated QR format: {}".format(fmt))


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
        super().hideEvent(event)


class BalWizardDialog(BalDialog):
    def __init__(self, bal_window: "BalWindow"):
        assert bal_window
        BalDialog.__init__(
            self, bal_window.window, bal_window.bal_plugin, _("Bal Wizard Setup")
        )
        self.setMinimumSize(800, 400)
        self.bal_window = bal_window
        self._bal_parent = bal_window.window
        self.layout = QVBoxLayout(self)
        self.widget = BalWizardHeirsWidget(
            bal_window, self, self.on_next_heir, None, self.on_cancel_heir
        )
        self.layout.addWidget(self.widget)

    def next_widget(self, widget):
        self.layout.removeWidget(self.widget)
        self.widget.close()
        self.widget = widget
        self.layout.addWidget(self.widget)
        # self.update()
        # self.repaint()

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
        close_window = BalBuildWillDialog(self.bal_window)
        close_window.build_will_task()

        # Run the SAME final server check as the "Check" button (allegato15,
        # case B): previously the wizard only ran build_will_task() and skipped
        # the will-executor verification, so the user always had to press
        # "Check" manually after finishing the wizard. We now replicate exactly
        # the lists.py check() logic: after building, query every will that
        # needs a server check (Will.needs_server_check) and run
        # check_transactions(), which shows the "Checking transactions" dialog.
        will = {}
        for wid, w in self.bal_window.willitems.items():
            if Will.needs_server_check(w):
                will[wid] = w
        if will:
            self.bal_window.check_transactions(will)

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
        self._stopping = True
        # self.bal_window.heir_list_widget.will_settings_widget.update_will_settings()
        pass



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
        self.titleLabel = QLabel(self.title)
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
    title = "Bitcoin After Life Heirs"
    message = (
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
    title = _("Bitcoin After Life Will-Executors")
    message = _("Choose willexecutors download method")

    def get_content(self):
        # question = QLabel()
        self.combo = QComboBox()
        self.combo.addItems(
            [
                "Automatically download and select willexecutors",
                "Only download willexecutors list",
                "Import willexecutor list from file",
                "Manual",
            ]
        )
        # heir_name.setFixedWidth(32 * char_width_in_lineedit())
        return self.combo

    def validate(self):
        return True

    def _on_next(self):

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

                def on_success(willexecutors):
                    def ping_on_success(result):
                        ping_on_done()

                    def ping_on_failure(exec_info):
                        ping_on_done()

                    def ping_on_done():
                        # Task #02 - "Automatically download and select"
                        # (index 0): the green SELECTED tick must follow the
                        # green ping dot, i.e. select ONLY servers that actually
                        # answered the ping (status == 200) and DESELECT every
                        # server that did not (timeout / error / never pinged).
                        #
                        # Why the explicit deselect matters: previously a server
                        # that had been selected on an earlier download but is
                        # now unreachable stayed selected, so the plugin kept
                        # broadcasting to a dead server and got stuck. Forcing
                        # selected=False for non-200 servers discards them at the
                        # source. Re-running this (each "Automatically download"
                        # action) re-evaluates every server: one that failed
                        # before but now answers is selected again.
                        #
                        # We compare the status as a string ("200") to stay
                        # consistent with the will-executor list view
                        # (lists.py uses str(status) == "200"), and use .get()
                        # so a missing "status" key never raises.
                        if index < 1:
                            for we in self.bal_window.willexecutors:
                                wedict = self.bal_window.willexecutors[we]
                                responded = str(wedict.get("status", "")) == "200"
                                wedict["selected"] = responded
                        Willexecutors.save(
                            self.bal_window.bal_plugin, self.bal_window.willexecutors
                        )

                    self.bal_window.ping_willexecutors(
                        self.bal_window.willexecutors, ping_on_success, ping_on_failure
                    )

                self.bal_window.download_list(self.bal_window.willexecutors, on_success)

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
    title = "Bitcoin After Life Will-Executors"
    message = _("Configure and select your willexecutors")

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
    title = "Bitcoin After Life Will Settings"
    message = _("")

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

    def __init__(self, bal_window, parent=None):
        if not parent:
            parent = bal_window.window
        BalDialog.__init__(self, parent, bal_window.bal_plugin, _("Building Will"))
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

        # SCROLLABLE message area (allegato14): with many will-executors the
        # report can reach dozens of lines. Previously the dialog kept resizing
        # itself taller for every new line (see msg_update's resize), so with
        # e.g. 50 will-executors the window grew past the screen and the bottom
        # buttons (Close) became unreachable. We now put the message label in a
        # QScrollArea with a capped maximum height: once the text exceeds that
        # height a vertical scrollbar appears and the buttons stay visible.
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.message_label)
        # Open the report area ~450px tall (owner request: 500px left too much
        # empty space below the short report; 450px lines up with the desired
        # window height). The dialog may still grow up to 700px to fit a few more
        # lines; beyond that the vertical scrollbar takes over and the bottom
        # buttons stay reachable.
        self.scroll_area.setMinimumHeight(450)
        self.scroll_area.setMaximumHeight(700)
        self.vbox.addWidget(self.scroll_area, 1)

        # Kept for backward compatibility (referenced by old/commented code);
        # no longer used to lay out the messages.
        self.qwidget = QWidget(self)
        self.labelsbox = QVBoxLayout(self.qwidget)
        self.setMinimumWidth(600)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.labels = []
        self.check_row = None
        self.inval_row = None
        self.build_row = None
        self.sign_row = None
        self.push_row = None
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
        self.network = Network.get_instance()
        self._stopping = False
        self.thread = TaskThread(self)
        self.thread.finished.connect(self.task_finished)  # see #3956

    def task_finished(self):
        pass

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
        if self._stopping:
            return
        txs = None
        _logger.debug("close plugin phase 1 started")
        varrow = self.msg_set_status("Checking variables")
        try:
            self.bal_window.init_class_variables()
        except CheckAliveError as cae:
            fee_per_byte = self.bal_window.will_settings.get("baltx_fees", 1)
            tx = Will.invalidate_will(
                self.bal_window.willitems, self.bal_window.wallet, fee_per_byte,
                history_label=self.bal_window.bal_plugin.HISTORY_LABEL.get(),
                will_locktime=Will.get_min_locktime(
                    self.bal_window.willitems,
                    default_value=self.bal_window.date_to_check,
                ),
            )
            if tx:
                _logger.debug(
                    "during phase1 CAE: {}, Continue to invalidate".format(cae)
                )
                self.msg_set_status(
                    "Checking variables", varrow,
                    "Check Alive Threshold Passed: you have to Invalidate "
                    "your old Will",
                    self.COLOR_ERROR,
                )
            else:
                raise cae
            return None, tx
        except NoHeirsException:
            self.msg_set_status(
                "Checking variables", varrow, self.msg_alert("No Heirs")
            )
            return "no_heirs", None
        except Exception as e:
            raise e
        try:
            _logger.debug("checking variables")
            Will.check_amounts(
                self.bal_window.heirs,
                self.bal_window.willexecutors,
                Util.get_available_utxos(
                    self.bal_window.window.wallet,
                    self.bal_window.bal_plugin.HISTORY_LABEL.get(),
                    Will.get_min_locktime(
                        self.bal_window.willitems,
                        default_value=self.bal_window.date_to_check,
                    ),
                ),
                self.bal_window.date_to_check,
                self.bal_window.window.wallet.dust_threshold(),
                max_fee=self.bal_window.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
            )
            _logger.debug("variables ok")
            self.msg_set_status("Checking variables", varrow, "Ok", self.COLOR_OK)
        except AmountException:
            self.msg_set_checking(
                self.msg_warning(
                    "In the inheritance process, "
                    + "the entire wallet will always be fully emptied. \n"
                    + "Your settings require an adjustment of the amounts"
                )
            )
        except WillExecutorFeeTooHighException as e:
            self.msg_set_checking(
                self.msg_warning(f"Will-executor fee too high: {e}")
            )

        self.msg_set_checking()
        have_to_build = False
        try:
            self.bal_window.check_will()
            self.msg_set_checking(self.msg_ok())
        except WillExpiredException:
            # UNIFY INVALIDATE PROCEDURE (+ task #03):
            #
            # The will is already expired (e.g. the CHECK button is pressed on an
            # expired will). Previously this returned (None, invalidate_tx),
            # which routed to the automatic invalidate path (password prompt +
            # auto-broadcast) that did NOT set the "BAL Invalidate transaction"
            # history label.
            #
            # We now return the SAME "invalidate_classic" signal used elsewhere,
            # so on_success_phase1 shows the warning popup and auto-opens
            # Electrum's classic transaction window (which sets the label). This
            # makes the CHECK button and the WIZARD behave identically and fixes
            # the missing-label bug (#03).
            _logger.debug("expired")
            self.msg_set_checking("Expired")
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
            self.msg_set_checking(_("Postponed: invalidating old will"))
            fee_per_byte = self.bal_window.will_settings.get("baltx_fees", 1)
            return None, Will.invalidate_will(
                self.bal_window.willitems, self.bal_window.wallet, fee_per_byte,
                history_label=self.bal_window.bal_plugin.HISTORY_LABEL.get(),
                will_locktime=Will.get_min_locktime(
                    self.bal_window.willitems,
                    default_value=self.bal_window.date_to_check,
                ),
            )
        except NoHeirsException:
            _logger.debug("no heirs")
            self.msg_set_checking("No Heirs")
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
            message = self._check_failure_message(e)
            _logger.debug(f"message: {message}")
            self.msg_set_checking(message)

        if have_to_build:
            self.msg_set_building()
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
                # the "BAL Invalidate transaction" history label.
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
                f"{_('Heir')} {hid}",
                None,
                f"{dust_amount} is DUST - excluded (amount below dust limit)",
                self.COLOR_WARNING,
            )

        have_to_sign = False
        for wid in Will.only_valid(self.bal_window.willitems):
            if not self.bal_window.willitems[wid].get_status("COMPLETE"):
                have_to_sign = True
                break
        return have_to_sign, txs

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
        for wid in Will.only_valid(self.bal_window.willitems):
            # Label shown in Electrum's History tab for inheritance txs.
            self.bal_window.wallet.set_label(wid, "BAL Inheritance transaction")
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

    def _sync_locktime_to_built_txs(self):
        """Align the plugin's stored delivery date with the built transactions.

        WHY this is needed (bug reported by the owner):
        When the will is rebuilt while it still spends the same coins as a
        previous one (e.g. after deleting an heir WITHOUT changing the date),
        the core engine AUTOMATICALLY anticipates the transaction locktime by
        one day (see Will.check_anticipate / Util.anticipate_locktime). This is
        correct and required so the new transaction can be mined BEFORE the old
        one it replaces.

        However the plugin's own stored delivery date
        (WILL_SETTINGS["locktime"]) was NOT updated and stayed at the original
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

        RELATIVE recipes ("30d"/"1y") are PRESERVED: they are resolved against
        the built will's frozen locktime on every check (via
        ``Util.resolve_locktime_against_tx`` for the postpone detection and
        ``resolve_date_to_check(..., built_locktime=...)`` for the reference
        timestamp), so they no longer drift away from the built transactions
        and never trigger the daily invalidate prompt.  Freezing them to an
        absolute timestamp here would silently erase the user's relative
        choice from WILL_SETTINGS.

        We route the update through BalWindow.update_setting_widgets, which is
        the single place that (1) stores the value in WILL_SETTINGS, (2)
        persists it to Electrum's database and (3) refreshes the date widgets in
        every panel/wizard, so the visible date and the .ics export stay
        consistent.
        """
        # Minimum locktime across the valid (just built) inheritance txs.
        # Will.get_min_locktime returns None when there is no valid tx.
        min_locktime = Will.get_min_locktime(self.bal_window.willitems, None)
        if min_locktime is None:
            return
        min_locktime = int(min_locktime)
        stored_locktime = self.bal_window.will_settings["locktime"]
        # A RELATIVE stored value ("30d"/"1y") is PRESERVED: it is resolved
        # against the built transactions on every check (the post-build
        # `resolve_date_to_check` anchoring and `resolve_locktime_against_tx`
        # in the postpone detection), so it no longer drifts and must not be
        # frozen to an absolute timestamp here.  Only an ABSOLUTE stored value
        # is compared with the built transactions (see below).
        is_relative_locktime = (
            isinstance(stored_locktime, str)
            and stored_locktime[-1:].lower() in ("d", "y")
        )
        if not is_relative_locktime:
            # Current stored delivery date, as a comparable UNIX timestamp.
            try:
                current = int(Util.parse_locktime_string(stored_locktime))
            except Exception:
                # If the stored value cannot be parsed, fall back to syncing.
                current = None
            # A genuine user-chosen POSTPONE (a later absolute date) is never
            # overwritten; a genuine automatic ANTICIPATION (built earlier
            # than stored) is synced to the built transactions.
            was_anticipation = current is not None and min_locktime < current
            if was_anticipation:
                _logger.debug(
                    f"sync delivery date to built tx locktime: "
                    f"{current} -> {min_locktime}"
                )
                # Remember that we anticipated the date, so the later sign
                # prompt can explain WHY signing is needed
                # (see on_success_phase1).
                self._date_was_anticipated = True
                # update_setting_widgets stores the value, persists it and
                # refreshes the date widgets in all panels/wizard (the .ics
                # calendar too).
                self.bal_window.update_setting_widgets(
                    min_locktime, "locktime", update_all=True
                )
        # A relative "Check Alive" threshold ("N days BEFORE the delivery") is
        # also PRESERVED: it is anchored on every check by
        # ``resolve_date_to_check`` / ``resolve_guard_threshold``, so it does
        # not need to be frozen to an absolute date here.

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
            wait_row = self.msg_edit_row(_(f"Please wait {i}secs"), wait_row)
            time.sleep(1)
        self.msg_del_row(wait_row)

    def loop_broadcast_invalidating(self, tx):
        if self._stopping:
            return
        self.msg_set_invalidating("Broadcasting")
        try:
            tx.add_info_from_wallet(self.bal_window.wallet)
            self.network.run_from_another_thread(tx.add_info_from_network(self.network))

            # IMPORTANT (task #21 fix): get the txid from the transaction
            # object, NOT from broadcast_transaction()'s return value.
            # Network.broadcast_transaction is declared "-> None" and ALWAYS
            # returns None (it only raises on failure). The previous code stored
            # that None into `txid` and put set_label() in the `else: # txid`
            # branch, which was therefore NEVER reached - that is why the
            # "BAL Invalidate transaction" history label kept missing on this
            # automatic ("postpone") path. The transaction is already signed and
            # complete here, so tx.txid() is the correct, stable id - exactly
            # what the working Tools -> Invalidate path uses
            # (BalWalletWindow.invalidate_will -> result.txid()).
            txid = tx.txid()

            # Set the history label BEFORE broadcasting. set_label only writes to
            # the local wallet metadata (no network needed), so doing it first
            # guarantees the label exists the moment the transaction shows up in
            # the History tab, regardless of how fast the broadcast/notification
            # arrives.
            if txid:
                self.bal_window.wallet.set_label(txid, "BAL Invalidate transaction")
            else:
                _logger.debug(f"invalidate tx has no txid: {tx}")

            self.network.run_from_another_thread(
                self.network.broadcast_transaction(tx, timeout=120), timeout=120
            )
            self.msg_set_invalidating(self.msg_ok())

        except TxBroadcastError as e:
            _logger.error(f"fail to broadcast transaction:{e}")
            msg = e.get_message_for_gui()
            self.msg_set_invalidating(self.msg_error(msg))
        except BestEffortRequestFailed as e:
            self.msg_set_invalidating(self.msg_error(e))

    def loop_push(self):
        # Broadcast is "one-shot" (Group B / B2 follow-up): each selected
        # will-executor is contacted ONCE. Transactions that are broadcast
        # successfully become PUSHED; transactions whose server fails or times
        # out are left as PUSH_FAIL and simply skipped - they are NOT retried
        # automatically. A dead will-executor could otherwise never answer and
        # make the plugin retry forever. The user can broadcast a failed
        # transaction manually later with the "Broadcast" button. Note that
        # get_willexecutor_transactions already excludes PUSHED transactions, so
        # the successful ones are never re-sent on a subsequent run.
        if self._stopping:
            return
        self.msg_set_pushing(_("Broadcasting"))
        try:

            willexecutors = Willexecutors.get_willexecutor_transactions(
                self.bal_window.willitems
            )

            # Only push to the will-executors the user actually selected.  We
            # filter the mapping up-front so push_transactions_parallel only
            # talks to the relevant servers.
            selected = {
                url: we
                for url, we in willexecutors.items()
                if Willexecutors.is_selected(
                    self.bal_window.willexecutors.get(url),
                ) and Willexecutors.is_valid(
                    self.bal_window.willexecutors.get(url),
                    max_fee=self.bal_window.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
                    dust=self.bal_window.window.wallet.dust_threshold(),
                )
            }

            # Servers that report "already present" need their stored tx
            # verified afterwards (network I/O); collect them here and process
            # them sequentially after the parallel push, keeping the original
            # check logic untouched.
            already_present = []
            total = len(selected)
            done = {"count": 0}

            deadline = Willexecutors.PUSH_GLOBAL_DEADLINE

            def _status_line():
                # e.g. "Broadcasting your will to executors: 2/3 (5s / 30s)".
                # The "/ 30s" makes the maximum wait explicit, so the user knows
                # the wizard will proceed by then (the global deadline) instead
                # of wondering how long the counter will keep climbing.
                return "{} {}/{} ({}s / {}s)".format(
                    _("Broadcasting"), done["count"], total,
                    min(int(time.time() - push_start), deadline), deadline,
                )

            def on_each(url, willexecutor, ok, exc):
                # Runs from a worker thread.  Do only thread-safe book-keeping
                # plus a signal-based UI update (msg_edit_row emits a pyqtSignal,
                # which is marshalled to the GUI thread).
                if isinstance(exc, Willexecutors.AlreadyPresentException):
                    already_present.append(url)
                elif ok:
                    for wid in willexecutor["txsids"]:
                        self.bal_window.willitems[wid].set_status("PUSHED", True)
                else:
                    # One-shot: mark the failed transactions and move on. They
                    # are left as PUSH_FAIL (no automatic retry).
                    for wid in willexecutor["txsids"]:
                        self.bal_window.willitems[wid].set_status("PUSH_FAIL", True)
                done["count"] += 1
                # Show the per-server result (Ok/Ko) in bold + color so the
                # outcome stands out, keeping the server URL in normal weight.
                result = self.msg_ok("Ok") if ok else self.msg_error("Ko")
                self.msg_edit_row("{} : {}".format(url, result))
                self.msg_set_pushing(_status_line())

            def on_timeout(url, willexecutor):
                # The global deadline elapsed before this server answered. Mark
                # its txs as failed and move on (one-shot: no automatic retry).
                # The user can broadcast them manually later if desired.
                for wid in willexecutor.get("txsids", []):
                    self.bal_window.willitems[wid].set_status("PUSH_FAIL", True)
                self.msg_edit_row(
                    "{} : {}".format(url, self.msg_error(_("Timeout - no answer")))
                )

            if self._stopping:
                return
            # Push to all selected will-executors in parallel: a slow/dead
            # server no longer blocks the others, so the wizard's "Broadcasting"
            # step is no longer sequential.  Each server keeps a short retry
            # behaviour, and a global deadline guarantees the wizard always
            # proceeds even if a server never answers.
            push_start = time.time()
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

            Willexecutors.push_transactions_parallel(
                selected, on_each=on_each, on_timeout=on_timeout, on_tick=on_tick
            )

            # Final summary line with the total elapsed time.
            self.msg_set_pushing(
                "{}/{} ({}s)".format(done["count"], total,
                                     int(time.time() - push_start))
            )

            # Verify the "already present" servers (sequential, original logic).
            self.bal_plugin = self.bal_window.bal_plugin
            for url in already_present:
                for wid in willexecutors[url]["txsids"]:
                    if self._stopping:
                        return
                    row = self.msg_edit_row(
                        "checking {} - {} : <b>{}</b>".format(
                            self.bal_window.willitems[wid].we["url"], wid, "Waiting"
                        )
                    )
                    w = self.bal_window.willitems[wid]
                    w.set_check_willexecutor(
                        Willexecutors.check_transaction(wid, w.we["url"])
                    )
                    # Show the CHECKED result in bold + color (green True /
                    # red False) so the outcome stands out, keeping the server
                    # URL and tx id in normal weight.
                    checked = self.bal_window.willitems[wid].get_status("CHECKED")
                    result = self.msg_ok(checked) if checked else self.msg_error(checked)
                    row = self.msg_edit_row(
                        "checked {} - {} : {}".format(
                            self.bal_window.willitems[wid].we["url"],
                            wid,
                            result,
                        ),
                        row,
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

    def invalidate_task(self, password, bal_window, tx):
        if self._stopping:
            return
        _logger.debug(f"invalidate tx: {tx}")
        # fee_per_byte = bal_window.will_settings.get("baltx_fees", 1)
        tx = self.bal_window.wallet.sign_transaction(tx, password)
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
            self.msg_set_invalidating(f"Error: {e}")
            raise Exception("Impossible to sign") from e

    def on_success_invalidate(self, success):
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
        # invalidation manually, with the "BAL Invalidate transaction" label.
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
            #      "BAL Invalidate transaction" history label - which fixes
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
                return

            self.msg_set_invalidating()
            # need to sign invalidate and restart phase 1

            password = self.bal_window.get_wallet_password(
                _("Invalidate your old will"), parent=self
            )
            if password is False:
                # The user cancelled the password prompt for the invalidation.
                # We must NOT call self.wait(3) here: on_success_phase1 runs in
                # the GUI thread, so wait()'s time.sleep() would freeze the UI
                # for several seconds. While frozen the dialog cannot repaint
                # the area it just resized, leaving a black, undrawn rectangle
                # at the bottom of the "Building Will" window (the reported bug).
                #
                # Instead, mark the invalidation as "Aborted" and offer a
                # non-blocking "Close" button (the same helper used by the
                # normal finish path), so the user can read the outcome and
                # dismiss the dialog when they want, without blocking the GUI.
                self.msg_set_invalidating(_("Aborted"))
                self._add_close_button()
                return
            self.thread.add(
                partial(self.invalidate_task, password, self.bal_window, tx),
                on_success=self.on_success_invalidate,
                on_done=self.on_accept,
                on_error=self.on_error,
            )

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

        calendar_button = BalCalendarButton(self.bal_window, self._ics_provider)
        calendar_button.setText(_("Calendar"))

        self._close_button = QPushButton(_("Close"))
        self._close_button.clicked.connect(self._on_close_clicked)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(calendar_button)
        button_row.addWidget(self._close_button)
        self.vbox.addLayout(button_row)
        self._close_button.setFocus()

    # ------------------------------------------------------------------ #
    # No-willexecutor error handling
    # ------------------------------------------------------------------ #

    def _add_no_willexecutor_buttons(self):
        """Add "Will-Executor" and "Close" buttons when no executor is
        selected and ``no_willexecutor`` is ``False``."""
        if getattr(self, "_no_we_buttons_added", False):
            return
        self._no_we_buttons_added = True
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

        self._no_we_layout = btn_row
        self.vbox.addLayout(btn_row)
        self.resize(self.vbox.sizeHint())

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
        wizard.on_next_heir()
        wizard.on_next_locktimeandfee()
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

        self._no_heirs_layout = btn_row
        self.vbox.addLayout(btn_row)
        self.resize(self.vbox.sizeHint())

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
                raw_description = self.bal_window.bal_plugin.EVENT_DESCRIPTION.default
                raw_summary = self.bal_window.bal_plugin.EVENT_SUMMARY.default
                threshold = None
                num_reminders = 3
            else:
                threshold_ts = BalTimestamp(
                    self.bal_window.will_settings["threshold"]
                ).to_timestamp()
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
        if self._next_steps_hint:
            self.bal_window.show_message(self._next_steps_hint)

    def closeEvent(self, event):
        self._stopping = True
        # Stop AND join the thread, then propagate the close event (previously
        # it neither waited nor called super().closeEvent()).
        stop_thread(getattr(self, "thread", None))
        super().closeEvent(event)

    def task_phase2(self, password):
        if self._stopping:
            return
        if self.have_to_sign:
            try:
                if txs := self.bal_window.sign_transactions(password):
                    for txid, tx in txs.items():
                        # Re-parse instead of deepcopy (the signed tx can carry
                        # wallet-derived input info holding a threading.RLock,
                        # which copy.deepcopy cannot pickle).
                        self.bal_window.willitems[txid].tx = Will.get_tx_from_any(
                            str(tx)
                        )
                    self.bal_window.save_willitems()
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
        # Blank separator row: visually detach the final "All done" summary
        # from the per-step result rows above it, so the closing line stands
        # out as the overall outcome rather than just another step.
        self.msg_edit_row("")
        # Final summary row: the whole "Building Will" sequence above (check /
        # sign / broadcast) finished without errors.  Give it an explicit
        # left-side label ("All done") so this closing Ok is not an orphan
        # result like the other rows have.
        self.msg_edit_row("{}:\t{}".format(_("All done"), self.msg_ok()))

        # Guide the user through any remaining MANUAL steps.  After the will is
        # (re)built -- e.g. because an heir was removed/added from the Wizard --
        # the new transactions may still need to be SIGNED and/or BROADCAST by
        # the user.  This dialog only signs/pushes automatically when it already
        # has the password and the will is in the right state; in every other
        # case the user is otherwise left without any indication of what to do
        # next.  We inspect the real status of the valid wills and tell the user
        # exactly which buttons to press.
        self._show_next_steps_hint()

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
                    "then 'Broadcast' to send it to the will-executors."
                )
            elif need_sign:
                hint = _(
                    "Next step (manual): press 'Sign' to sign your will."
                )
            elif need_push:
                hint = _(
                    "Next step (manual): press 'Broadcast' to send your will "
                    "to the will-executors."
                )
            else:
                # Nothing left to do (already signed and, if needed, sent).
                return

            self._next_steps_hint = hint
            self.msg_edit_row("<b>{}</b>".format(hint))
        except Exception as hint_err:
            _logger.debug(f"next-steps hint error: {hint_err}")

    def on_error(self, error):
        _logger.error(error)
        pass

    def on_error_phase1(self, error):
        self.bal_window.update_all()
        a, b, c = error
        self.msg_edit_row(self.msg_error(f"Error: {b}"))
        import traceback
        _logger.error(f"error phase1: {b}\n{''.join(traceback.format_exception(a, b, c))}")
        button=QPushButton(_("Close"))
        button.clicked.connect(self.close)
        self.vbox.addWidget(button)
        self.resize(self.vbox.sizeHint()+button.sizeHint()*2)
        self.repaint()
    def on_error_phase2(self, error):
        self.bal_window.upade_all()
        a, b, c = error
        self.msg_edit_row(self.msg_error(f"Error: {b}"))
        _logger.error(f"error phase2: {b}")

    def _executed_inheritance_status(self):
        """Return the on-chain status of an already-executed inheritance.

        Task #7a (owner request). After an inheritance is executed the wallet is
        fully emptied (the plugin always empties the wallet). Pressing CHECK on
        such an empty wallet makes ``check_will`` raise NotCompleteWillException
        (the heirs no longer match the empty wallet), which previously showed the
        alarming "Found CHANGES ... a NEW WILL must be prepared" message even
        though the inheritance was, in fact, correctly executed.

        To reassure the user we look at the will items, whose status is already
        set to CONFIRMED / MEMPOOL by ``Will.check_will`` (see core/will.py), and
        return a short code describing the real situation:

        Returns:
            "CONFIRMED" if at least one will transaction is confirmed on-chain
                (the inheritance is already executed);
            "MEMPOOL" if none is confirmed but at least one is in the mempool
                (waiting for confirmation);
            None if neither (the change really has to be rebuilt).

        CONFIRMED takes precedence over MEMPOOL, since a confirmed transaction is
        the strongest evidence that the inheritance has gone through.
        """
        has_mempool = False
        try:
            for _wid, witem in self.bal_window.willitems.items():
                if witem.get_status("CONFIRMED"):
                    return "CONFIRMED"
                if witem.get_status("MEMPOOL"):
                    has_mempool = True
        except Exception as _err:
            # Never let this purely informational check break the flow.
            _logger.debug(f"_executed_inheritance_status error: {_err}")
        return "MEMPOOL" if has_mempool else None

    def _check_failure_message(self, e):
        """Return a precise explanation of why the will is no longer coherent.

        ``Will.is_will_valid`` raises NotCompleteWillException (or one of its
        subclasses) when the stored will stops matching the wallet, the heirs
        or the will-executors.  Those exceptions ALREADY carry the useful
        detail - the heir name, the will-executor URL, the old and new fee
        rate - but this dialog used to discard all of it and print the same
        sentence, "Found CHANGES to the DATE or the HEIRS", for every case.

        That was not merely vague, it was WRONG for the most common situation:
        when the wallet simply receives new funds, neither the date nor the
        heirs changed, yet the user was sent hunting for edits never made.

        NOTE: no new exception classes were added for this (owner request).
        Every case below is told apart using only what the core already
        raises today.
        """
        # Subclasses first - they are all NotCompleteWillException.
        if isinstance(e, TxFeesChangedException):
            # The core raises TxFeesChangedException(f"{tx_fees}:  {w.tx_fees}"),
            # i.e. "current:  stored".  Show both rates when they can be read,
            # and fall back to a plain sentence if that format ever changes.
            try:
                now_fee, old_fee = [p.strip() for p in str(e).split(":", 1)]
                return _(
                    "Miner fee rate changed (the will was built with {} "
                    "sat/byte, now it is {}): a new will must be prepared."
                ).format(old_fee, now_fee)
            except Exception:
                return _(
                    "The miner fee rate changed: a new will must be prepared."
                )
        if isinstance(e, WillExecutorNotPresent):
            return _(
                'Will-executor "{}" is not covered by the current will: '
                "a new will must be prepared."
            ).format(str(e))
        if isinstance(e, NoWillExecutorNotPresent):
            return _(
                "Backup mode is enabled but the will has no backup "
                "transaction: a new will must be prepared."
            )
        if isinstance(e, HeirNotFoundException):
            # Raised when an heir was added, removed, or had its delivery date
            # changed.  We deliberately do NOT try to tell those three apart
            # (owner request: too fine-grained); naming the heir is what makes
            # the message actionable.
            return _(
                'Heir "{}" is not covered by the current will (it was added '
                "or removed, or its delivery date changed): a new will must "
                "be prepared."
            ).format(str(e))
        # Kept for completeness: nothing in the plugin raises these two today,
        # but they ARE NotCompleteWillException subclasses, so should future
        # code raise them they get a sensible message rather than the fallback.
        if isinstance(e, HeirChangeException):
            return _("The heirs changed: a new will must be prepared.")
        if isinstance(e, WillexecutorChangeException):
            return _("A will-executor changed: a new will must be prepared.")

        # A plain NotCompleteWillException.  The core raises it in exactly two
        # places, told apart STRUCTURALLY (not by matching message text, which
        # would be fragile): with no argument when the will holds no valid
        # transaction, and with one argument when a wallet utxo is not
        # included in the will.
        if type(e) is NotCompleteWillException:
            if e.args:
                return _(
                    "The wallet contains funds that the current will does not "
                    "cover yet: a new will must be prepared."
                )
            return _(
                "The will contains no valid transaction: a new will must be "
                "prepared."
            )

        # An unrecognised subclass: say so honestly instead of guessing.
        return _(
            "The will is no longer coherent and must be rebuilt; the exact "
            "reason could not be determined."
        )

    def _build_failure_message(self):
        """Return a plain-language explanation of why the will was not built.

        ``Heirs.buildTransactions`` records a reason code in
        ``last_build_error`` every time it gives up (the codes are listed in
        that method).  Here we turn that code into ONE specific sentence
        telling the user what to fix.

        WHY: this dialog used to print the same three "possible reasons"
        (low balance / dust shares / check-alive after the delivery date)
        whenever the build returned nothing.  The owner hit a real case where
        all three were false - the actual cause was that no will-executor was
        usable, which the list did not even mention - so the message actively
        misled.  When the reason is unknown we now SAY that it is unknown and
        list what to check, instead of asserting three guesses as if they were
        the only possibilities.
        """
        reason = None
        try:
            reason = getattr(self.bal_window.heirs, "last_build_error", None)
        except Exception as _err:
            # A diagnostic must never break the report it is explaining.
            _logger.debug(f"_build_failure_message: {_err}")

        messages = {
            "NO_HEIRS": _(
                "No heirs: add at least one heir before building the will."
            ),
            "NO_UTXO": _(
                "The wallet has no spendable funds, so no inheritance "
                "transaction can be created."
            ),
            "NO_WILLEXECUTOR_USABLE": _(
                "No usable will-executor: none of the servers in the list is "
                "both selected and valid. Open the will-executor settings, "
                "select at least one server and check that it is reachable."
            ),
            "NO_FUTURE_DATE": _(
                "No delivery date left to build: every heir's date is already "
                "covered by the existing will. Choose a later delivery date, "
                "or change an heir's date."
            ),
            "WILLEXECUTOR_FEE": _(
                "The amount to send must cover the miner fees plus this "
                "will-executor's fee, and the wallet balance is not enough: "
                "select cheaper will-executors, or add funds to the wallet."
            ),
            "WILLEXECUTOR_FEE_TOO_HIGH": _(
                "A will-executor asks for more than the maximum fee you "
                "allowed: raise the maximum fee in the settings, or select a "
                "cheaper will-executor."
            ),
            "TX_BUILD_FAILED": _(
                "The inheritance transactions could not be assembled from the "
                "available funds (the balance may not cover the miner fees)."
            ),
            "WILLEXECUTOR_TX_ERROR": _(
                "An unexpected error stopped the transactions being prepared "
                "for a will-executor, so it was skipped. Try again, or select "
                "a different will-executor."
            ),
        }

        if reason in messages:
            return messages[reason] + "\n\n" + _("Skipped")

        return (
            _(
                "Could not build the will, and the exact cause could not be "
                "determined. Please check that:\n"
                "- the wallet balance covers the miner and will-executor fees,\n"
                "- each heir's share is above the minimum (dust limit),\n"
                "- the Check Alive date is EARLIER than the delivery date,\n"
                "- at least one will-executor is selected and reachable."
            )
            + "\n\n"
            + _("Skipped")
        )

    def msg_set_checking(self, status="Waiting", row=None):
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
            "Building your will", self.build_row, status, color
        )

    def msg_set_signing(self, status=None, row=None):
        row = self.sign_row if row is None else row
        self.sign_row = self.msg_set_status("Signing your will", self.sign_row, status)

    def msg_set_pushing(self, status=None, row=None):
        row = self.push_row if row is None else row
        self.push_row = self.msg_set_status(
            "Broadcasting your will to executors", self.push_row, status
        )

    def msg_set_waiting(self, status=None, row=None):
        row = self.wait_row if row is None else row
        self.wait_row = self.msg_edit_row(f"Please wait {status}secs", self.wait_row)

    def msg_error(self, e):
        # Results are shown in bold so the outcome stands out from the
        # left-side state label (which stays in normal weight).
        return "<font color='{}'><b>{}</b></font>".format(self.COLOR_ERROR, e)

    def msg_ok(self, e="Ok"):
        # Results are shown in bold (see msg_error).
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
        status = "Wait" if status is None else status
        if color is None:
            line = "{}:\t<b>{}</b>".format(_(msg), status)
        else:
            line = "{}:\t<font color={}><b>{}</b></font>".format(
                _(msg), color, status
            )
        return self.msg_edit_row(line, row)

    def ask_password(self, msg=None):
        self.password = self.bal_window.get_wallet_password(msg, parent=self)

    def msg_edit_row(self, line, row=None):
        try:
            self.labels[row] = line
        except Exception:
            self.labels.append(line)
            row = len(self.labels) - 1

        self.updatemessage.emit()

        return row

    def msg_del_row(self, row):
        try:
            del self.labels[row]
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
        full_text = "<br><br>".join(self.labels).replace("\n", "<br>")
        self.message_label.setText(full_text)
        self.message_label.adjustSize()
        # Auto-scroll the report to the BOTTOM so the newest line is always
        # visible (allegato14). The QScrollArea caps the height, so instead of
        # resizing the whole dialog taller we move its vertical scrollbar to the
        # maximum. ensureWidgetVisible is deferred via the scrollbar range so it
        # reflects the just-added text.
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        # Let the dialog grow only up to the scroll area's capped height; beyond
        # that the scrollbar handles overflow and the buttons stay reachable.
        self.resize(self.sizeHint())
        # Re-assert the bottom position after the resize/relayout settled.
        scrollbar.setValue(scrollbar.maximum())

    def get_text(self):
        return self.message_label.text()

    pass



class WillDetailDialog(BalDialog):
    def __init__(self, bal_window, will=None, threshold=None):
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
            self.threshold = bal_window.will_settings["real_threshold"]

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
        w = QWidget()
        hlayout = QHBoxLayout(w)

        b = QPushButton(_("Sign"))
        b.clicked.connect(self.ask_password_and_sign_transactions)
        hlayout.addWidget(b)

        b = QPushButton(_("Broadcast"))
        b.clicked.connect(self.broadcast_transactions)
        hlayout.addWidget(b)

        b = QPushButton(_("Export"))
        b.clicked.connect(self.export_will)
        hlayout.addWidget(b)
        b = QPushButton(_("Invalidate"))
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
            QLabel(_("Expiration date: ") + str(BalTimestamp(self.threshold)))
        )
        self.vlayout.addWidget(self.scrollbox)
        w = QWidget()
        hlayout = QHBoxLayout(w)
        hlayout.addWidget(
            QLabel(_("Valid Txs:") + str(len(Will.only_valid_list(self.will))))
        )
        hlayout.addWidget(QLabel(_("Total Txs:") + str(len(self.will))))
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
        Invalidate action targets the saved will items.
        """
        self.bal_window.merge_will(self.will)
        self._external_will = False
        self.will = self.bal_window.willitems
        self.threshold = self.bal_window.will_settings["real_threshold"]
        if self.merge_button:
            self.merge_button.hide()
        self.update()

    def toggle_replaced(self):
        self.bal_window.bal_plugin.hide_replaced()
        toggle = _("Hide")
        if self.bal_window.bal_plugin._hide_replaced:
            toggle = _("Unhide")
        self.toggle_replace_button.setText(f"{toggle} {_('replaced')}")
        self.update()

    def toggle_invalidated(self):
        self.bal_window.bal_plugin.hide_invalidated()
        toggle = _("Hide")
        if self.bal_window.bal_plugin._hide_invalidated:
            toggle = _("Unhide")
        self.toggle_invalidate_button.setText(_(f"{toggle} {_('invalidated')}"))
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
        event.accept()


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

    def __init__(self, chunk_size=CHUNK_PRESETS[0][1], parent=None):
        QWidget.__init__(self, parent)
        self.chunk_size = chunk_size
        self.format = "balqr"
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
        self.size_combo.addItems([label for label, _budget in CHUNK_PRESETS])
        self.size_combo.setCurrentIndex(
            preset_index_for_chunk_size(self.chunk_size)
        )
        self.size_combo.currentIndexChanged.connect(self._on_chunk_change)
        size_row.addWidget(self.size_combo)
        size_row.addStretch(1)
        vbox.addLayout(size_row)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel(_("Format:")))
        self.format_combo = QComboBox()
        self._format_options = ["balqr"] + list(ANIMATED_QR_FORMATS)
        self.format_combo.addItems(
            [
                format_name(fmt) + ((" (default)") if fmt == "balqr" else "")
                for fmt in self._format_options
            ]
        )
        self.format_combo.setCurrentIndex(0)
        self.format_combo.currentIndexChanged.connect(self._on_format_change)
        format_row.addWidget(self.format_combo)
        self.format_hint = QLabel()
        self.format_hint.setWordWrap(True)
        format_row.addWidget(self.format_hint, 1)
        vbox.addLayout(format_row)

    def set_tx_strings(self, tx_strings):
        """Rebuild the transfer frames from the given serialized transactions."""
        self.tx_strings = list(tx_strings)
        self._stop_auto()
        self.transfer = encode_transfer(self.tx_strings, compress=False)
        # BAL QR now ships compact (best-of) compressed by default: smaller
        # frames, and the importer reverses it via the per-frame flag. The
        # other formats keep the raw transfer (they compress internally).
        self._balqr_transfer, self._balqr_compressed = encode_transfer_best(
            self.tx_strings
        )
        self._refresh_frames()
        self._update_intro()
        self._render()

    @property
    def total_frames(self):
        return len(self.frames)

    def _refresh_frames(self):
        if self.format == "balqr":
            self.frames = split_frames(
                self._balqr_transfer,
                self.chunk_size,
                compressed=self._balqr_compressed,
            )
        else:
            self.frames = encode_animated_frames(
                self.transfer, self.format, self.chunk_size
            )
        self.index = 0

    def _on_format_change(self, index):
        self._stop_auto()
        self.format = self._format_options[index]
        self._refresh_frames()
        self._render()
        self._update_intro()

    def _on_chunk_change(self, index):
        self._stop_auto()
        self.chunk_size = CHUNK_PRESETS[index][1]
        self._refresh_frames()
        self._render()

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
        self.format_hint.setText(
            {
                "balqr": _(
                    "Proprietary format; every other BAL wallet understands it."
                ),
                "ur1": _("Legacy BC-UR v1 (ur:bytes), compatible with older "
                         "Blockchain Commons tools."),
                "ur2": _("Standard BC-UR v2 fountain codes (ur:bytes)."),
                "bbqr": _("Coinkite BBQR (B$ frames) for BitKit & friends."),
            }[self.format]
        )
        if self.format == "balqr":
            self.intro_label.setText(
                _(
                    "Scan the QR codes below, in order, with the will-opening "
                    "device.\nFrame 1 of {} carries the total number of codes."
                ).format(self.total_frames)
            )
        else:
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
        self.qr_page = BalQrExportWidget(chunk_size=chunk)
        self.stacked.addWidget(self.qr_page)
        self.audio_page = self._build_audio_page()
        self.stacked.addWidget(self.audio_page)
        vbox.addWidget(self.stacked)

        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.close)
        vbox.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        mode_ids = {
            "file": self.MODE_FILE,
            "qr": self.MODE_QR,
            "audio": self.MODE_AUDIO,
        }
        mode = mode_ids.get(initial_mode, self.MODE_FILE)
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
    * a frame whose ``session_key`` (transfer identity, e.g. ``"balqr:3"`` or
      ``"ur2:2-31-3804692811"``) contradicts the transfer already being built
      is a ``reset`` (a different transfer was presented).

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
            # Slot grid is always 1-based even for 0-based wire formats.
            slot = index + 1 if fmt == "bbqr" else index
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
        except (MissingFramesError, QrTransferError, AnimatedQrError) as e:
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
        txid = self.txids[self.i]
        try:
            tx, newly = self.bal_window._prepare_and_sign_tx(
                self.items, txid, self.password
            )
        except Exception as e:
            log_error(e, self)
            self.show_error(_("Could not sign the transaction: {}").format(e))
            return
        if newly and tx.is_complete():
            self.items[txid].set_status("COMPLETE", True)
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

