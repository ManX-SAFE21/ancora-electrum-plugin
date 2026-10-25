"""
bal.gui.qt.common
=================

Shared imports and tiny helper utilities for the Qt GUI layer.

Every other ``bal.gui.qt`` module does ``from .common import *`` so that the
long list of Electrum / PyQt6 imports lives in a single place.  This file also
hosts a few GUI helpers that do not deserve a module of their own:

    * :class:`shown_cv`       - trivial mutable "is this tab shown?" holder.
    * :func:`add_widget`      - add a labelled widget (plus optional help) to a grid.
    * :func:`log_error`       - format an exception traceback for a dialog.
    * :func:`export_meta_gui` - export plugin metadata to a JSON file.
      (:class:`CheckAliveError` now lives in ``bal.core.checkalive``.)
"""

import enum
import os
import subprocess
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
from typing import Any, Callable, Mapping, Optional, Union

from electrum.bitcoin import NLOCKTIME_BLOCKHEIGHT_MAX, NLOCKTIME_MAX, NLOCKTIME_MIN
from electrum.gui.common_qt.util import draw_qr
from electrum.gui.qt.amountedit import BTCAmountEdit
from electrum.gui.qt.main_window import ElectrumWindow, StatusBarButton
from electrum.gui.qt.my_treeview import MyTreeView
from electrum.gui.qt.password_dialog import PasswordDialog
from electrum.gui.qt.transaction_dialog import TxDialog
from electrum.gui.qt.util import (
    Buttons,
    CancelButton,
    ColorScheme,
    EnterButton,
    HelpButton,
    MessageBoxMixin,
    OkButton,
    TaskThread,
    WaitingDialog,
    WindowModalDialog,
    char_width_in_lineedit,
    getOpenFileName,
    getSaveFileName,
    import_meta_gui,
    read_QIcon_from_bytes,
    read_QPixmap_from_bytes,
    webopen,
)
from electrum.logging import get_logger
from electrum.network import BestEffortRequestFailed, Network, TxBroadcastError
from electrum.payment_identifier import PaymentIdentifier
from electrum.plugin import hook
from electrum.transaction import SerializationError, Transaction, tx_from_any
from electrum.util import (
    DECIMAL_POINT,
    FileExportFailed,
    FileImportFailed,
    UserCancelled,
    decimal_point_to_base_unit_name,
    read_json_file,
    write_json_file,
)
from PyQt6.QtCore import (
    QDateTime,
    QModelIndex,
    QPersistentModelIndex,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPalette, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyleOptionFrame,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.heirs import (
    HEIR_DUST_AMOUNT,
    HEIR_REAL_AMOUNT,
    OP_RETURN_PREFIX,
    BalanceTooLowException,
    HeirAmountIsDustException,
    Heirs,
    WillExecutorFeeTooHighException,
    get_op_return_hex,
    is_op_return_address,
    validate_op_return_hex,
)

# --- Core (GUI-free) logic layer ---
from ...core.plugin_base import BalPlugin, BalTimestamp
from ...core.util import Util, copy_structure
from ...core.will import (
    AmountException,
    HeirChangeException,
    HeirNotFoundException,
    NoHeirsException,
    NotCompleteWillException,
    NoWillExecutorNotPresent,
    StagedWillRemovedException,
    TxFeesChangedException,
    Will,
    WillChainError,
    WillexecutorChangeException,
    WillExecutorNotPresent,
    WillExpiredException,
    WillItem,
    WillPostponedException,
    format_status_history,
)
from ...core.willexecutors import (  # noqa: F401
    Willexecutors,
    is_onion_url,
    is_tor_active,
)

# BAL's translator: Electrum's catalog first, then BAL's (see bal/i18n.py).
from ...i18n import N_, _

# --- Presentation helpers ---
from .theme import (
    signature_suffix,
    status_color,
    will_status_text,
    will_status_tooltip,
)
from .window_utils import (
    bring_to_front,
    show_modal,
    show_on_top,
    stop_thread,
    top_level_of,
)

_logger = get_logger(__name__)

# Labels of bal.core.qrtransfer.CHUNK_PRESETS, marked for translation here
# because qrtransfer.py must stay free of Electrum imports (the Android reader
# ships a copy of it). The combos show them with _(label);
# tests/test_i18n.py checks that this list matches CHUNK_PRESETS.
QR_PRESET_LABELS = (
    N_("Small - ~150 bytes/QR (low-res cameras)"),
    N_("Medium - ~400 bytes/QR"),
    N_("Large - ~900 bytes/QR"),
    N_("XL - ~1800 bytes/QR (high-res cameras)"),
)


class shown_cv:  # noqa: N801  (intentionally lowercase: mirrors Qt signal naming)
    _type = bool

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value




def add_widget(grid, label, widget, row, help_):
    """Add a ``label | widget | help button`` row to ``grid``.

    ``label`` and ``help_`` must already be translated by the caller: a
    variable passed to _() cannot be extracted into the catalog.
    """
    grid.addWidget(QLabel(label), row, 0)
    grid.addWidget(widget, row, 1)
    grid.addWidget(HelpButton(help_), row, 2)




def confirm_advanced_mode(parent=None):
    """Ask for the "at My Risk" phrase before ADVANCED mode; True when typed.

    The safety gate of the plugin Settings dialog (owner request): ADVANCED
    exposes powerful, easy-to-misuse controls, so the phrase must be typed
    (any capitalisation). Cancel or a wrong phrase gives False. Shared by
    the Settings dialog and the wizard's mode selector (#127).
    """
    text, ok = QInputDialog.getText(
        parent,
        _("Enable ADVANCED mode"),
        _("Type 'at My Risk' to enable ADVANCED mode"),
    )
    return bool(ok) and text.strip().lower() == "at my risk"


def confirm_basic_mode(parent=None):
    """Ask "Go back to BASIC mode?" (Yes / No, No by default); True on Yes.

    #174 (owner, 2026-10-21): in the Settings dialog, a turn of the mouse
    wheel or a wrong click put ADVANCED back to BASIC without a word, and
    the advanced tools disappeared. Leaving ADVANCED now asks first. Shared
    by the Settings dialog, its reset buttons and the wizard's mode
    selector (through :func:`switch_user_type`).
    """
    return ask_yes_no(
        parent,
        _("Go back to BASIC mode? The advanced tools will be hidden. "
          "The will does not change."),
        _("BASIC mode"),
    )


def ask_yes_no(parent, text, title=""):
    """A Yes / No question with translated buttons; True on Yes.

    No is the default button and the Esc key, so nothing changes by mistake.

    #189 (owner): Qt's standard Yes / No buttons (Electrum's ``question``)
    stayed in English, because Electrum loads no Qt translation; buttons
    added with their own ``_()`` text are translated by the plugin's
    catalogs. Used by every Yes / No question of the plugin.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title or "")
    box.setText(text)
    yes = box.addButton(_("Yes"), QMessageBox.ButtonRole.YesRole)
    no = box.addButton(_("No"), QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


def is_advanced_mode(plugin):
    """True when the stored mode (``USER_TYPE``) is ADVANCED."""
    return str(plugin.USER_TYPE.get()).lower() == "advanced"


class NoWheelComboBox(QComboBox):
    """A combo box that the mouse wheel does not change (#174).

    Scrolling the Settings dialog with the pointer over the "User type" box
    changed the mode by mistake. The wheel event is ignored, so it goes to
    the parent (the dialog scrolls instead); a click or the keyboard still
    change the value.
    """

    def wheelEvent(self, event):  # noqa: N802 - Qt name
        """Leave the wheel to the parent widget."""
        event.ignore()


def switch_user_type(plugin, advanced, parent=None):
    """Set the plugin mode (``USER_TYPE``, every wallet); True when set as asked.

    ``advanced``: True for ADVANCED, which needs :func:`confirm_advanced_mode`
    first; when it is not confirmed the mode is set to BASIC and False is
    returned (the caller puts its selector back, on the mode in force:
    :func:`is_advanced_mode`). #174: leaving ADVANCED for BASIC asks
    :func:`confirm_basic_mode` first; on No nothing changes (ADVANCED stays,
    the windows are not touched) and False is returned. From BASIC to BASIC
    nothing is asked. Then the open windows follow at once: the per-mode editor
    default of the toolbars (``_apply_editor_default_on_toolbars``, only on
    a real change) and ``update_all``, when the plugin has them (the Qt
    ``Plugin``; a test plugin may not). Before them, the delivery form of
    every open wallet follows the mode (``context.follow_mode``, #146). One function for the Settings dialog
    and the wizard (#127), so both behave the same.
    """
    if not advanced and is_advanced_mode(plugin) and not confirm_basic_mode(parent):
        return False
    confirmed = (not advanced) or confirm_advanced_mode(parent)
    plugin.USER_TYPE.set("advanced" if (advanced and confirmed) else "basic")
    # #146 (owner): the delivery form of every open wallet follows the mode
    # (period in ADVANCED, fixed in BASIC; never with a will in force), from
    # the Settings dialog as from the wizard. Before ``update_all``, so the
    # toolbars show the new form at once.
    from ...engine import context as engine_context

    for window in list(getattr(plugin, "bal_windows", {}).values()):
        engine_context.follow_mode(window)
    for name in ("_apply_editor_default_on_toolbars", "update_all"):
        refresh = getattr(plugin, name, None)
        if callable(refresh):
            try:
                refresh()
            except Exception as e:
                _logger.debug(f"user type refresh ({name}) failed: {e!r}")
    return confirmed


def translated_headers(headers):
    """Return a translated copy of a list's ``headers`` class attribute.

    The column headers are class attributes marked with N_(): a class body
    runs at import time, before the catalog is loaded, so they are
    translated here, each time the headers are (re)built.
    """
    return {column: _(text) for column, text in headers.items()}




def log_error(exec_info, window=None):
    """Log an error and optionally show it.

    ``exec_info`` may be either a ``sys.exc_info()`` triple
    ``(type, value, traceback)`` or a single exception instance (callers use
    both forms), so we handle both and always try to log a full traceback.
    """
    _logger.error(f"LOG_ERROR: {exec_info}")
    exc = None
    if isinstance(exec_info, BaseException):
        exc = exec_info
    elif isinstance(exec_info, (tuple, list)) and len(exec_info) >= 2:
        # sys.exc_info() form: the exception instance is the 2nd element.
        exc = exec_info[1]
    try:
        if exc is not None:
            _logger.error(
                "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
            )
        else:
            _logger.error(traceback.format_exc())
    except Exception:
        _logger.error(traceback.format_exc())

    if window is not None:
        # show_error expects a human-readable message, not a triple.
        window.show_error(str(exc) if exc is not None else str(exec_info))




# The file choices of the plugin's own file windows (#158): JSON first,
# every file second, as Electrum's ``import_meta_gui``. Every export now
# saves ".json" (:func:`export_meta_gui`); a file exported before without
# it is found with "All files" (the plugin reads the content, never the
# name).
IMPORT_FILE_FILTER = "JSON (*.json);;All files (*)"


def export_meta_gui(electrum_window, title, exporter):
    """Choose where to save a file and export it.

    #158: a *title* ending in ".json" (the will, the heirs, the
    will-executors) also makes ".json" the ending added to a name typed
    without one, so the saved file is recognised as JSON.
    """
    filter_ = "All files (*)"
    default_extension = "json" if title.endswith(".json") else None
    filename = getSaveFileName(
        parent=electrum_window,
        title=_("Select file to save your {}").format(title),
        filename="BALplugin_{}_{}_{}".format(
            BalPlugin.chainname, str(electrum_window.wallet), title
        ),
        filter=filter_,
        default_extension=default_extension,
        config=electrum_window.config,
    )
    if not filename:
        return
    try:
        exporter(filename)
    except FileExportFailed as e:
        electrum_window.show_critical(str(e))
    else:
        electrum_window.show_message(
            _("Your {0} were exported to '{1}'").format(title, str(filename))
        )


