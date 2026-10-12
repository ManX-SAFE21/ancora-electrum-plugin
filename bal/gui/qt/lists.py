"""
bal.gui.qt.lists
================

Tree/list views (subclasses of Electrum's ``MyTreeView``) and their toolbars.

    * HeirListWidget        - editable list of heirs (address / amount / locktime).
    * PreviewList           - preview of the will transactions before signing.
    * WillExecutorListWidget- list of will-executor servers.
    * WillExecutorWidget    - container combining the list with add/import buttons.

These views call back into the :class:`BalWindow` controller (passed at
construction) for all business actions, so the heavy logic stays in ``window``
and ``dialogs``.
"""

import re
from typing import TYPE_CHECKING

from PyQt6.QtCore import QItemSelectionModel, QLocale, QRect
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QHeaderView,
    QMessageBox,
    QStyledItemDelegate,
    QStyleOptionHeader,
)
from PyQt6.QtWidgets import QLineEdit as _QLineEdit

from ...core import will_state, will_versions
from ...core.messages import protected_executor_message
from .calendar import BalCalendarButton
from .common import (
    N_,
    OP_RETURN_PREFIX,
    BalTimestamp,
    Buttons,
    CancelButton,
    HelpButton,
    MessageBoxMixin,
    MyTreeView,
    OkButton,
    QAbstractItemView,
    QApplication,
    QColor,
    QDateTime,
    QDateTimeEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QModelIndex,
    QPainter,
    QPersistentModelIndex,
    QPushButton,
    QSize,
    QSizePolicy,
    QSpinBox,
    QStandardItem,
    QStandardItemModel,
    QStyle,
    Qt,
    QToolButton,
    QVBoxLayout,
    QWidget,
    TaskThread,
    Util,
    Willexecutors,
    WillItem,
    _,
    _logger,
    char_width_in_lineedit,
    datetime,
    enum,
    export_meta_gui,
    getOpenFileName,
    import_meta_gui,
    is_op_return_address,
    partial,
    read_json_file,
    read_QIcon_from_bytes,
    status_color,
    translated_headers,
    tx_from_any,
    will_status_text,
    will_status_tooltip,
    write_json_file,
)
from .dialogs import BalBuildWillDialog, BalDialog
from .report_actions import report_menu
from .theme import version_state_colour, version_state_text
from .widgets import BalCheckBox, WillSettingsWidget

if TYPE_CHECKING:
    from .window import BalWindow


def _can_sign(will_item):
    """True if a will transaction may still be signed (not fully signed)."""
    return bool(will_item) and not will_item.get_status("COMPLETE")


def _can_broadcast(will_item):
    """True if a will transaction is ready to broadcast (fully signed)."""
    return bool(will_item) and will_item.get_status("COMPLETE")


def _can_delete(will_item):
    """True if a will transaction may be deleted.

    Not deletable (CHANGELOG #129):
        * a transaction SENT to its will-executor (PUSHED or CHECKED),
          whatever its other flags: replaced, invalidated or not. WHY:
          deleting it only on this PC does not remove it from the executor's
          server, so the owner would wrongly believe that inheritance was
          cancelled; and a replaced one that is still executable is part of
          the will in force (#128). Before #129 a REPLACED or INVALIDATED
          sent item could be deleted;
        * a transaction in force (``will_state``: signed) that no
          will-executor can hold: the backup transaction (no executor), as
          before.

    Deletable: the drafts, the partially signed ones, and the transactions
    never sent (a signed copy whose push failed or was never made; an old
    one never sent).
    """
    if not will_item:
        return False
    if will_item.get_status("PUSHED") or will_item.get_status("CHECKED"):
        return False
    if will_state.classify(will_item) == will_state.IN_FORCE:
        return bool(getattr(will_item, "we", None))
    return True


def _apply_select_all(willexecutors, select, valid=None):
    """Apply a bulk selection across a ``{url: we_dict}`` mapping.

    ``select`` is the target selection value. When ``valid`` is given (a
    ``{url: bool}`` validity map), the selection is restricted by validity:
    selecting sets valid ones to True and invalid ones to False, while
    deselecting only clears the invalid ones (valid ones keep their state).
    Without ``valid`` every entry is simply set to ``select``. The mapping is
    mutated in place and returned.
    """
    for url, we in willexecutors.items():
        if valid is not None:
            if select:
                we["selected"] = valid.get(url, False)
            elif not valid.get(url, False):
                we["selected"] = False
        else:
            we["selected"] = select
    return willexecutors


class HeirListWidget(MyTreeView, MessageBoxMixin):
    class Columns(MyTreeView.BaseColumnsEnum):
        NAME = enum.auto()
        ADDRESS = enum.auto()
        AMOUNT = enum.auto()

    headers = {
        Columns.NAME: N_("Name"),
        Columns.ADDRESS: N_("Address"),
        Columns.AMOUNT: N_("Amount"),
    }
    filter_columns = [Columns.NAME, Columns.ADDRESS]

    ROLE_SORT_ORDER = Qt.ItemDataRole.UserRole + 1000

    ROLE_HEIR_KEY = Qt.ItemDataRole.UserRole + 4000
    key_role = ROLE_HEIR_KEY

    def createEditor(self, parent, option, index):
        return QLineEdit(parent)

    def setEditorData(self, editor, index):
        editor.setText(index.data())

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text())

    def __init__(self, bal_window: "BalWindow", parent):
        super().__init__(
            parent=parent,
            main_window=bal_window.window,
            stretch_column=self.Columns.NAME,
            editable_columns=[
                self.Columns.NAME,
                self.Columns.ADDRESS,
                self.Columns.AMOUNT,
            ],
        )
        self.decimal_point = bal_window.window.get_decimal_point()
        self.bal_window = bal_window

        try:
            self.setModel(QStandardItemModel(self))
            self.sortByColumn(self.Columns.NAME, Qt.SortOrder.AscendingOrder)
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        except Exception:
            pass

        self.setSortingEnabled(True)
        self.std_model = self.model()

        self.update()

    def on_activated(self, idx):
        self.on_double_click(idx)

    def on_double_click(self, idx):
        edit_key = self.get_edit_key_from_coordinate(idx.row(), idx.column())
        self.bal_window.heirs.get(edit_key)
        self.bal_window.new_heir_dialog(edit_key)

    def on_edited(self, idx, edit_key, *, text):
        prior_name = self.bal_window.heirs.get(edit_key)
        if not prior_name:
            return
        col = idx.column()
        try:
            if col == 2:
                text = Util.encode_amount(text, self.decimal_point)
            elif col == 0:
                self.bal_window.delete_heirs([edit_key])
                edit_key = text
            prior_name[col - 1] = text
            prior_name.insert(0, edit_key)
            prior_name = tuple(prior_name)
        except Exception:
            prior_name = (
                (edit_key,) + prior_name[: col - 1] + (text,) + prior_name[col:]
            )

        try:
            self.bal_window.set_heir(prior_name)
        except Exception:
            self.update()

    def delete_heirs(self, selected_keys):
        self.bal_window.delete_heirs(selected_keys)
        self.update()

    def create_menu(self, position):
        menu = QMenu()
        idx = self.indexAt(position)
        column = idx.column() or self.Columns.NAME
        selected_keys = []
        for s_idx in self.selected_in_column(self.Columns.NAME):
            sel_key = self.model().itemFromIndex(s_idx).data(0)
            selected_keys.append(sel_key)
        if selected_keys and idx.isValid():
            column_title = self.model().horizontalHeaderItem(column).text()
            # ok
            column_data = "\n".join(
                self.model().itemFromIndex(s_idx).text()
                for s_idx in self.selected_in_column(column)
            )
            menu.addAction(
                _("Copy {}").format(column_title),
                lambda: self.place_text_on_clipboard(column_data, title=column_title),
            )
            if column in self.editable_columns:
                item = self.model().itemFromIndex(idx)
                if item.isEditable():
                    persistent = QPersistentModelIndex(idx)
                    menu.addAction(
                        _("Edit {}").format(column_title),
                        lambda p=persistent: self.edit(QModelIndex(p)),
                    )
            menu.addAction(_("Delete"), lambda: self.delete_heirs(selected_keys))
        menu.exec(self.viewport().mapToGlobal(position))

    def update(self):
        current_key = self.get_role_data_for_current_item(
            col=self.Columns.NAME, role=self.ROLE_HEIR_KEY
        )
        self.model().clear()
        self.update_headers(translated_headers(self.__class__.headers))
        set_current = None
        for key in sorted(self.bal_window.heirs.keys()):
            heir = self.bal_window.heirs[key]
            labels = [""] * len(self.Columns)
            labels[self.Columns.NAME] = key
            if is_op_return_address(heir[0]):
                data_hex = heir[0][len(OP_RETURN_PREFIX):]
                try:
                    decoded = bytes.fromhex(data_hex).decode("utf-8", errors="replace")
                    if len(decoded) > 40:
                        decoded = decoded[:40] + "\u2026"
                    labels[self.Columns.ADDRESS] = decoded
                except Exception:
                    labels[self.Columns.ADDRESS] = "OP_RETURN"
            else:
                labels[self.Columns.ADDRESS] = heir[0]
            labels[self.Columns.AMOUNT] = Util.decode_amount(
                heir[1], self.decimal_point
            )

            items = [QStandardItem(x) for x in labels]
            items[self.Columns.NAME].setEditable(True)
            items[self.Columns.ADDRESS].setEditable(True)
            items[self.Columns.AMOUNT].setEditable(True)
            items[self.Columns.NAME].setData(
                key, self.ROLE_HEIR_KEY + self.Columns.NAME
            )
            items[self.Columns.ADDRESS].setData(
                key, self.ROLE_HEIR_KEY + self.Columns.ADDRESS
            )
            items[self.Columns.AMOUNT].setData(
                key, self.ROLE_HEIR_KEY + self.Columns.AMOUNT
            )

            row_count = self.model().rowCount()
            self.model().insertRow(row_count, items)

            if key == current_key:
                idx = self.model().index(row_count, self.Columns.NAME)
                set_current = QPersistentModelIndex(idx)
        try:
            self.will_settings_widget.on_locktime_change()
        except Exception:
            pass
        self.set_current_idx(set_current)
        # FIXME refresh loses sort order; so set "default" here:
        self.filter()

    def refresh_row(self, key, row):
        # nothing to update here
        pass

    def get_edit_key_from_coordinate(self, row, col):
        a = self.get_role_data_from_coordinate(row, col, role=self.ROLE_HEIR_KEY + col)
        return a

    def create_toolbar(self, config):
        toolbar, menu = self.create_toolbar_with_menu("")
        menu.addAction(_("&New Heir"), self.bal_window.new_heir_dialog)
        menu.addAction(_("Import"), self.bal_window.import_heirs)
        menu.addAction(_("Export"), lambda: self.bal_window.export_heirs())

        new_heir_button = QPushButton(_("New Heir"))
        new_heir_button.clicked.connect(self.bal_window.new_heir_dialog)

        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        self.will_settings_widget = WillSettingsWidget(self.bal_window, self)

        layout.addWidget(self.will_settings_widget)
        layout.addWidget(new_heir_button)

        toolbar.insertWidget(2, widget)

        return toolbar

    def build_transactions(self):
        # will = self.bal_window.prepare_will()
        self.bal_window.prepare_will()



def _fonts():
    """The normal and bold fonts of the application."""
    normal = QFont(QApplication.font())
    bold = QFont(normal)
    bold.setBold(True)
    return normal, bold


# Inner left padding of the texts of the version level (header and rows).
_PAD = 6


def _number_width():
    """Width of the "N." column of the version level (#129, owner)."""
    _normal, bold = _fonts()
    return QFontMetrics(bold).horizontalAdvance("#9999") + 2 * _PAD


def _status_width():
    """Width of the "Status" column of the version level.

    Wide enough for the longest state text in the current language (the
    states, the invalidation's "On the blockchain" / "Mempool" and the
    header label), so the header and every row put it at the same place.
    """
    _normal, bold = _fonts()
    metrics = QFontMetrics(bold)
    texts = [version_state_text(state) for state in (
        "draft", "in_force", "replaced_held", "replaced", "invalidated",
        "partially_signed", "executed", "not_valid", "discarded_draft")]
    texts += [_("On the blockchain"), _("Mempool"), _("Status")]
    return max(metrics.horizontalAdvance(t) for t in texts if t) + 3 * _PAD


class _RowPaint:
    """Draws a version-level row of the Will tab (CHANGELOG #129).

    Electrum's item delegate (``ElectrumItemDelegate``) calls ``paint`` /
    ``sizeHint`` / ``show_tooltip`` of the object stored in the item's
    ``MyTreeView.ROLE_CUSTOM_PAINT`` role. The row spans every column
    (``setFirstColumnSpanned``) and is drawn in the three columns of the
    version level, the same as :class:`_VersionHeader` (owner, after the
    ZIP test): "N." (the number), "Version" (the headline in bold and a
    second line) and "Status" (in the colour of the state). The
    transaction columns underneath keep their own widths.
    """

    def __init__(self, head, sub, state="", colour="#555555", number=""):
        """``head`` (bold), ``sub`` (second line), ``state`` and its colour,
        ``number`` ("#16", empty for drafts and the other rows)."""
        self.head = head
        self.sub = sub
        self.state = state
        self.colour = colour
        self.number = number

    def paint(self, painter, rect):
        """Draw the three columns inside ``rect`` (the whole row)."""
        normal, bold = _fonts()
        painter.save()
        try:
            bold_metrics = QFontMetrics(bold)
            line = bold_metrics.height()
            top = rect.top() + max(0, (rect.height() - 2 * line) // 2)
            number_w = _number_width()
            status_x = rect.right() - _status_width() + _PAD
            text_x = rect.left() + number_w + _PAD
            width = max(0, status_x - text_x - 2 * _PAD)
            # Dark text on the light background of the row (set on the
            # item), readable with Electrum's dark theme too, like the
            # summary box above the list.
            painter.setFont(bold)
            painter.setPen(QColor("#111111"))
            if self.number:
                painter.drawText(
                    QRect(rect.left() + _PAD, top, number_w - _PAD, line),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    self.number,
                )
            painter.drawText(
                QRect(text_x, top, width, line),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                bold_metrics.elidedText(self.head, Qt.TextElideMode.ElideRight, width),
            )
            if self.state:
                painter.setPen(QColor(self.colour))
                painter.drawText(
                    QRect(status_x, rect.top(), rect.right() - status_x, rect.height()),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    self.state,
                )
            if self.sub:
                painter.setFont(normal)
                painter.setPen(QColor("#333333"))
                painter.drawText(
                    QRect(text_x, top + line, width, line),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    QFontMetrics(normal).elidedText(
                        self.sub, Qt.TextElideMode.ElideRight, width
                    ),
                )
        finally:
            painter.restore()

    def sizeHint(self, default_size):  # noqa: N802 - Electrum's name
        """Two lines high; the width stays the default (the row spans)."""
        _normal, bold = _fonts()
        return QSize(default_size.width(), 2 * QFontMetrics(bold).height() + 8)

    def show_tooltip(self, evt):
        """No own tooltip: the item's tooltip (the whole text) is shown."""
        return False


class _VersionHeader(QHeaderView):
    """The header of the Will tab: the columns of the VERSION level.

    Owner, after the ZIP test (#129): the tree's header showed the
    transaction columns, squeezed to the left when every version was
    closed. It now shows "N." / "Version" / "Status" across the whole
    width, at the same places as :class:`_RowPaint`, whatever is open; the
    transaction columns are named by a sub-header row under each open
    version. The sections themselves still size the transaction columns.
    """

    def __init__(self, parent):
        """A horizontal header whose sections are not clickable."""
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setSectionsClickable(False)

    def segments(self):
        """``[(x, width, label)]`` of the three columns, in viewport pixels.

        The left edge follows the tree's indentation (the rows start after
        the expand arrow); the right edge is the end of the last section,
        so the header scrolls with the rows.
        """
        tree = self.parent()
        indent = tree.indentation() if tree.rootIsDecorated() else 0
        right = self.length() - self.offset()
        number_w = indent + _number_width()
        status_w = _status_width()
        return [
            (0, number_w, _("N.")),
            (number_w, max(0, right - status_w - number_w), _("Version")),
            (right - status_w, status_w, _("Status")),
        ]

    def paintEvent(self, event):  # noqa: N802 - Qt's name
        """Draw the three version columns (the sections are not drawn)."""
        painter = QPainter(self.viewport())
        try:
            tree = self.parent()
            indent = tree.indentation() if tree.rootIsDecorated() else 0
            height = self.viewport().height()
            for i, (x, width, label) in enumerate(self.segments()):
                opt = QStyleOptionHeader()
                self.initStyleOption(opt)
                opt.rect = QRect(x, 0, width, height)
                opt.section = i
                self.style().drawControl(
                    QStyle.ControlElement.CE_HeaderSection, opt, painter, self)
                # The texts start where the rows' texts start.
                text_x = x + _PAD + (indent if i == 0 else 0)
                painter.drawText(
                    QRect(text_x, 0, max(0, width - _PAD), height),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    label,
                )
        finally:
            painter.end()


# Background of the version and invalidation rows (light, like the summary
# box above the list), so their dark text stays readable whatever Electrum's
# theme is.
_VERSION_ROW_BG = "#f2f2f2"

# The display format of the toolbar's date field, read once (see
# _delivery_text).
_DATE_FORMAT = None


def long_year_format(fmt):
    """``fmt`` (a Qt date format) with a four-digit year (#129, owner).

    The locale's short format often has a two-digit year ("dd/MM/yy HH:mm"
    in Italian): the owner's mockup reads "27/02/2027 23:00". Only a lone
    ``yy`` is widened; ``yyyy`` stays as it is.
    """
    return re.sub(r"(?<!y)yy(?!y)", "yyyy", fmt)


def _delivery_text(locktime):
    """The delivery date of a version row (#129).

    The toolbar's date field (``LockTimeDateEdit``, a ``QDateTimeEdit``)
    shows the local time in the locale's short format, with minutes; the
    same format is used here (day/month order of the locale), with a
    four-digit year (:func:`long_year_format`).
    """
    global _DATE_FORMAT
    if _DATE_FORMAT is None:
        _DATE_FORMAT = long_year_format(QDateTimeEdit().displayFormat())
    try:
        moment = QDateTime.fromSecsSinceEpoch(int(locktime))
        return QLocale().toString(moment, _DATE_FORMAT)
    except Exception:
        return str(BalTimestamp(locktime))


class _TallCell:
    """Gives a two-line cell the height of its two lines (#129, owner).

    The Will-Executor cell holds the URL and, below it, the fee address;
    Qt's default row height left the two lines touching. Electrum's item
    delegate asks the object in ``MyTreeView.ROLE_CUSTOM_PAINT`` for the
    size; the text itself is still drawn by the delegate.
    """

    def paint(self, painter, rect):
        """Nothing to add: the delegate draws the text."""

    def sizeHint(self, default_size):  # noqa: N802 - Electrum's name
        """At least two text lines plus a margin."""
        line = QFontMetrics(QApplication.font()).height()
        return QSize(default_size.width(), max(default_size.height(), 2 * line + 10))

    def show_tooltip(self, evt):
        """No own tooltip."""
        return False


def _executor_fee(item):
    """The will-executor's own fee paid by ``item`` (sat), or ``None``.

    The value of the output to the executor's fee address; when the
    transaction has none (it cannot be read), the executor's base fee.
    """
    we = getattr(item, "we", None)
    if not we:
        return None
    address = we.get("address")
    try:
        for out in item.tx.outputs():
            if address and out.address == address:
                return int(out.value)
    except Exception:
        pass
    try:
        return int(we.get("base_fee"))
    except (TypeError, ValueError):
        return None


#: The look of the two main commands of the Will tab: bold text.
BIG_BUTTON_STYLE = "QPushButton{font-weight:bold;}"

#: "Build / Edit Your Will" only (CHANGELOG #131, owner: option C of the
#: mockup, then "only on the first button"): bold text and a 2 px blue
#: frame so the main entry point stands out. Setting a border drops Qt's
#: native button look, so the padding, the rounded corners and the hover /
#: pressed backgrounds are given here too (palette colours: they follow
#: the theme).
FRAMED_BUTTON_STYLE = (
    "QPushButton{font-weight:bold; border:2px solid #2F6FD6;"
    " border-radius:6px; padding:4px 12px;"
    " background-color:palette(button);}"
    "QPushButton:hover{background-color:palette(light);}"
    "QPushButton:pressed{background-color:palette(midlight);}"
)


class PreviewList(MyTreeView, MessageBoxMixin):
    """The Will tab list: the will by versions (CHANGELOG #129).

    Two levels: one row per VERSION of the will (one build: the draft, the
    will in force, the history; ``bal.core.will_versions``) and under it
    one row per transaction (a will-executor's copy, or the backup), with
    the multiverse instalments under their parent transaction. A paid
    invalidation has its own row between the versions it ended and the
    next one. Only the last versions are shown until "Show all history" is
    pressed. The class name, the toolbar and the menu actions are the same
    as before (other code and tests use them).
    """

    class Columns(MyTreeView.BaseColumnsEnum):
        LOCKTIME = enum.auto()
        TXID = enum.auto()
        WILLEXECUTOR = enum.auto()
        FEE = enum.auto()
        STATUS = enum.auto()

    # CHANGELOG #83: no separate "Server" column any more; the Status column
    # says where this row's will-executor stands (see will_status_text).
    # CHANGELOG #129: the will-executor's own fee of each copy ("Compenso").
    headers = {
        Columns.LOCKTIME: N_("Locktime"),
        Columns.TXID: N_("Txid"),
        Columns.WILLEXECUTOR: N_("Will-Executor"),
        Columns.FEE: N_("Executor fee"),
        Columns.STATUS: N_("Status"),
    }

    ROLE_HEIR_KEY = Qt.ItemDataRole.UserRole + 2000
    key_role = ROLE_HEIR_KEY
    # What a row is ("version", "variant", "invalidation", "show_all",
    # "hide_all") and
    # its key (version id or txid), on the first cell of every row.
    ROLE_KIND = Qt.ItemDataRole.UserRole + 2001
    ROLE_KEY = Qt.ItemDataRole.UserRole + 2002
    # The text the search box looks in (lower case), on the first cell.
    ROLE_SEARCH = Qt.ItemDataRole.UserRole + 2003
    filter_columns = [Columns.LOCKTIME]

    def createEditor(self, parent, option, index):
        return QLineEdit(parent)

    def setEditorData(self, editor, index):
        editor.setText(index.data())

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text())

    def __init__(self, bal_window: "BalWindow", parent, will):
        """Build the list on ``will`` (the window's will items by default).

        #129: a tree (expand arrows shown), rows of different heights
        (two-line rows), no header sorting (the order is the versions'
        own), and the full txid never cut: the Status column takes the
        remaining width instead of the Txid column.
        """
        super().__init__(
            parent=parent,
            main_window=bal_window.window,
            stretch_column=self.Columns.STATUS,
        )
        # self._bal_parent = parent
        self.bal_window = bal_window
        self.decimal_point = bal_window.window.get_decimal_point

        if will is not None:
            self.will = will
        else:
            self.will = bal_window.willitems

        # State kept while the window lives (#129): "Show all history"
        # pressed, and the rows the owner opened or closed, by identity
        # (version id / txid), never by row number: update() runs often.
        self._show_all = False
        self._expanded = {}
        self._rebuilding = False
        # The "Show all history" / "Hide history" push button, when shown.
        self.history_button = None
        self._versions = {}
        self.history = None
        self.hidden_versions = 0

        self.setModel(QStandardItemModel(self))
        # The header names the version level (owner, after the ZIP test).
        self.setHeader(_VersionHeader(self))
        self.header().setResizeContentsPrecision(0)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(False)
        self.setSortingEnabled(False)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.expanded.connect(lambda idx: self._remember_expanded(idx, True))
        self.collapsed.connect(lambda idx: self._remember_expanded(idx, False))
        self.clicked.connect(self._on_clicked)
        self.std_model = self.model()

        self.update()

    # -------------------------------------------------------- the rows

    def _row_info(self, idx):
        """``(kind, key)`` of the row of ``idx`` (``(None, None)`` if none).

        Read from the row's first cell with ``idx.sibling``, so it works on
        the transaction rows under a version too (``model().index(row,
        col)`` only reaches the top-level rows).
        """
        if idx is None or not idx.isValid():
            return None, None
        item = self.model().itemFromIndex(idx.sibling(idx.row(), 0))
        if item is None:
            return None, None
        return item.data(self.ROLE_KIND), item.data(self.ROLE_KEY)

    def _row_key(self, idx):
        """The identity of a row for its expanded state (``v:`` / ``t:``)."""
        kind, key = self._row_info(idx)
        if kind == "version":
            return "v:" + str(key)
        if kind == "variant":
            return "t:" + str(key)
        return None

    def _remember_expanded(self, idx, expanded):
        """Keep what the owner opened or closed (not the list's own changes)."""
        if self._rebuilding:
            return
        key = self._row_key(idx)
        if key:
            self._expanded[key] = expanded

    def _on_clicked(self, idx):
        """One click on "Show all history" shows the whole history; one
        click on "Hide history" goes back to the last versions."""
        kind, _key = self._row_info(idx)
        if kind == "show_all":
            self.show_all_history()
        elif kind == "hide_all":
            self.hide_history()

    def show_all_history(self):
        """Show every version (kept while the window lives)."""
        self._show_all = True
        self.update()

    def hide_history(self):
        """Back to the last versions only (owner, ZIP test of #129: once
        shown, the whole history could not be hidden again)."""
        self._show_all = False
        self.update()

    def on_activated(self, idx):
        self.on_double_click(idx)

    def on_double_click(self, idx):
        """Open the transaction of a transaction row (also under a version).

        A version row opens or closes; the paid invalidation opens its
        transaction from the wallet; "Show all history" shows it all and
        "Hide history" hides it again.
        """
        kind, key = self._row_info(idx)
        if kind == "variant":
            self.show_transaction([key])
        elif kind == "version":
            first = idx.sibling(idx.row(), 0)
            self.setExpanded(first, not self.isExpanded(first))
        elif kind == "invalidation":
            self.show_invalidation(key)
        elif kind == "show_all":
            self.show_all_history()
        elif kind == "hide_all":
            self.hide_history()

    def show_invalidation(self, txid):
        """Open the paid invalidation ``txid`` in Electrum's transaction window."""
        try:
            tx = self.bal_window.wallet.adb.get_transaction(txid)
        except Exception:
            tx = None
        if tx is not None:
            self.bal_window.show_transaction(tx)

    def _selected_variant_keys(self, idx):
        """The txids of the selected transaction rows.

        The row under the mouse decides when it is not among the selected
        ones (a right click does not always select). Version rows are left
        out.
        """
        keys = []
        for s_idx in self.selectionModel().selectedIndexes():
            if s_idx.column() != 0:
                continue
            kind, key = self._row_info(s_idx)
            if kind == "variant" and key not in keys:
                keys.append(key)
        kind, key = self._row_info(idx)
        if kind == "variant" and key not in keys:
            keys = [key]
        return keys

    def build_menu(self, idx):
        """The context menu of the row of ``idx`` (``None``: no menu).

        A transaction row: the actions of before, on the selected
        transaction rows. A version row (#129): sign and broadcast (every
        transaction of the version, instalments included), through the
        same functions; no "details" there (owner: it opened one window
        per will-executor), "details" stays on each transaction row. The
        paid invalidation: details and copy id.
        """
        kind, key = self._row_info(idx)
        menu = QMenu()
        if kind == "version":
            version = self._versions.get(key)
            if version is None:
                return None
            keys = list(version.items)
            menu.addAction(
                _("sign"), lambda: self.sign_transactions(keys)
            ).setEnabled(any(_can_sign(self.will.get(k)) for k in keys))
            menu.addAction(
                _("broadcast"), lambda: self.broadcast_transactions(keys)
            ).setEnabled(any(_can_broadcast(self.will.get(k)) for k in keys))
            return menu
        if kind == "invalidation":
            menu.addAction(_("details"), lambda: self.show_invalidation(key))
            menu.addAction(_("copy id"), lambda: self.copy_txids([key]))
            return menu
        if kind != "variant":
            return None
        selected_keys = self._selected_variant_keys(idx)
        if not selected_keys:
            return None
        menu.addAction(
            _("details"),
            lambda: self.show_transaction(selected_keys),
        ).setEnabled(len(selected_keys) == 1)
        menu.addAction(
            _("sign"),
            lambda: self.sign_transactions(selected_keys),
        ).setEnabled(
            any(_can_sign(self.will.get(k)) for k in selected_keys)
        )
        menu.addAction(
            _("broadcast"),
            lambda: self.broadcast_transactions(selected_keys),
        ).setEnabled(
            any(_can_broadcast(self.will.get(k)) for k in selected_keys)
        )
        menu.addAction(
            _("check "),
            lambda: self.check_transactions(selected_keys),
        )

        menu.addSeparator()
        menu.addAction(
            _("copy id"),
            lambda: self.copy_txids(selected_keys),
        )
        menu.addAction(
            _("copy"),
            lambda: self.copy_tx_hexes(selected_keys),
        )
        menu.addAction(
            _("merge from txn"),
            lambda: self.merge_from_txn(),
        )

        menu.addSeparator()
        menu.addAction(
            _("delete"), lambda: self.delete(selected_keys)
        ).setEnabled(
            any(_can_delete(self.will.get(k)) for k in selected_keys)
        )
        return menu

    def create_menu(self, position):
        """Show the context menu of the row under ``position`` (see build_menu)."""
        menu = self.build_menu(self.indexAt(position))
        if menu is not None:
            menu.exec(self.viewport().mapToGlobal(position))

    def is_deletable(self, key):
        """True if the will transaction ``key`` may be deleted (``_can_delete``)."""
        return _can_delete(self.will.get(key))

    def delete(self, selected_keys):
        keys = [k for k in selected_keys if self.is_deletable(k)]
        for key in keys:
            del self.will[key]
            try:
                del self.bal_window.willitems[key]
            except Exception:
                pass
            try:
                del self.bal_window.will[key]
            except Exception:
                pass
        self.update()

    def sign_transactions(self, selected_keys):
        """Sign all selected transactions that are not fully signed yet."""
        keys = [
            k for k in selected_keys
            if _can_sign(self.will.get(k))
        ]
        if keys:
            self.bal_window.ask_password_and_sign_transactions(
                callback=self.update, txids=keys
            )

    def broadcast_transactions(self, selected_keys):
        """Force-broadcast all selected fully-signed transactions.

        ``force=True`` makes the will-executors re-accept transactions that
        were already pushed before.
        """
        keys = [
            k for k in selected_keys
            if _can_broadcast(self.will.get(k))
        ]
        if keys:
            self.bal_window.broadcast_transactions(force=True, txids=keys)
            self.update()

    def copy_txids(self, selected_keys):
        """Copy the selected transaction IDs to the clipboard (one per line)."""
        self.place_text_on_clipboard(
            "\n".join(selected_keys), title=_("Transaction IDs")
        )

    def copy_tx_hexes(self, selected_keys):
        """Copy the selected transactions (serialised hex) to the clipboard."""
        hexes = "\n".join(str(self.will[k].tx) for k in selected_keys)
        self.place_text_on_clipboard(hexes, title=_("Transactions"))

    def merge_from_txn(self):
        """Merge a transaction read from the clipboard, or from a file if the
        clipboard does not contain a valid transaction.
        """
        tx = None
        try:
            tx = tx_from_any(QApplication.clipboard().text())
        except Exception:
            tx = None
        if tx is None:
            filename = getOpenFileName(
                parent=self.bal_window.window,
                title=_("Open transaction file"),
                filter="All files (*)",
                config=self.bal_window.window.config,
            )
            if not filename:
                return
            try:
                with open(filename, "r") as f:
                    data = f.read()
                tx = tx_from_any(data)
            except Exception as e:
                self.bal_window.show_error(
                    _("Invalid transaction file: {}").format(e)
                )
                return
        try:
            self.bal_window.merge_single_transaction(tx)
        except Exception as e:
            self.bal_window.show_error(str(e))
        self.update()

    def check_transactions(self, selected_keys):
        wout = {}
        for k in selected_keys:
            wout[k] = self.will[k]
        if wout:
            self.bal_window.check_transactions(wout)
        self.update()

    def show_transaction(self, selected_keys):
        for key in selected_keys:
            self.bal_window.show_transaction(self.will[key].tx)

        self.update()

    def select(self, selected_keys):
        self.selected += selected_keys
        self.update()

    def deselect(self, selected_keys):
        for key in selected_keys:
            self.selected.remove(key)
        self.update()

    def update_will(self, will):
        self.will.update(will)
        self.update()

    # ------------------------------------------------ building the tree

    def _format_sats(self, sats):
        """``sats`` with the wallet's unit (``"1,000 sat"``), as Electrum shows it."""
        try:
            return self.bal_window.window.format_amount_and_units(sats)
        except Exception:
            return "{} sat".format(sats)

    def _heirs_text(self, version):
        """``"alice 60%, bob 40%"``: each heir with the amount as set."""
        parts = []
        for name, spec, _sats in version.heirs:
            spec = str(spec).strip()
            if not spec.endswith("%"):
                try:
                    spec = self._format_sats(int(spec))
                except (TypeError, ValueError):
                    pass
            parts.append("{} {}".format(name, spec))
        return ", ".join(parts)

    def _version_texts(self, version):
        """``(number, headline, second line)`` of a version row.

        "#11" (empty for drafts, discarded drafts and the other group),
        "Delivery 27/02/2027 23:00" and "alice 60%, bob 40% · Miner fee 21
        sat/vB · sent and verified 3 of 3".
        """
        number = "#{}".format(version.number) if version.number else ""
        if version.state == will_versions.VERSION_OTHER:
            head = _("Other transactions")
        elif version.locktime:
            head = "{} {}".format(_("Delivery"), _delivery_text(version.locktime))
        else:
            head = version_state_text(version.state)
        parts = []
        heirs = self._heirs_text(version)
        if heirs:
            parts.append(heirs)
        if version.fee_rate:
            parts.append("{} {} sat/vB".format(_("Miner fee"), version.fee_rate))
        if version.executors_total and not version.executors_sent:
            # A draft, or a version no will-executor ever received (#129).
            parts.append(_("not sent"))
        elif version.executors_total:
            parts.append(_("sent and verified {} of {}").format(
                version.executors_verified, version.executors_total))
        return number, head, " · ".join(parts)

    def _spanned_row(self, kind, key, head, sub, state="", colour="#555555",
                     background=_VERSION_ROW_BG, number=""):
        """A row that spans every column, drawn by :class:`_RowPaint`.

        The first cell carries the kind, the key, the search text and the
        tooltip; its own text stays empty (the painter draws it), so the
        Locktime column is not widened by it.
        """
        items = [QStandardItem("") for _c in self.Columns]
        first = items[0]
        first.setData(kind, self.ROLE_KIND)
        first.setData(key, self.ROLE_KEY)
        first.setData(_RowPaint(head, sub, state, colour, number),
                      MyTreeView.ROLE_CUSTOM_PAINT)
        whole = " · ".join(t for t in (number, head, sub, state) if t)
        first.setData(whole.lower(), self.ROLE_SEARCH)
        first.setToolTip(whole)
        for item in items:
            item.setEditable(False)
            if background:
                item.setBackground(QColor(background))
        return items

    def _variant_row(self, txid, item, in_force=False):
        """The row of one transaction (a will-executor's copy or the backup).

        In columns, as the owner asked: the locktime as before (bold), the
        FULL txid, the will-executor URL with its fee address below it (or
        "no will-executor" for the backup), its fee, and the status of
        #83, the whole row in the colour of its status. ``in_force``: the
        item is in force (``will_state``), so a REPLACED flag left by a
        later draft does not make it read "Replaced" (owner, #129).
        """
        we = getattr(item, "we", None)
        if we:
            executor = we.get("url") or ""
            address = we.get("address") or ""
            if address:
                executor = executor + "\n" + address
        else:
            # A backup transaction (the option "No will-executor"); it used
            # to show the string "None".
            text = _("no will-executor (backup transaction)")
            executor = text[:1].upper() + text[1:]
        fee = _executor_fee(item)
        labels = [""] * len(self.Columns)
        labels[self.Columns.LOCKTIME] = str(BalTimestamp(item.tx.locktime))
        labels[self.Columns.TXID] = txid
        labels[self.Columns.WILLEXECUTOR] = executor
        labels[self.Columns.FEE] = self._format_sats(fee) if fee is not None else ""
        # CHANGELOG #83: what this row is for the will today, coherent with
        # its colour (where its will-executor stands); the executor and the
        # whole history are in the tooltip.
        labels[self.Columns.STATUS] = will_status_text(item, in_force=in_force)
        items = [QStandardItem(str(text)) for text in labels]
        colour = QColor(status_color(item, in_force=in_force))
        for cell in items:
            cell.setEditable(False)
            cell.setBackground(colour)
        # Group C / C6: the Locktime column in bold, so the delivery time
        # stands out at a glance.
        bold_font = items[self.Columns.LOCKTIME].font()
        bold_font.setBold(True)
        items[self.Columns.LOCKTIME].setFont(bold_font)
        try:
            items[self.Columns.STATUS].setToolTip(will_status_tooltip(item))
        except Exception as tip_err:
            _logger.debug(f"status tooltip error: {tip_err}")
        if "\n" in executor:
            items[self.Columns.WILLEXECUTOR].setData(
                _TallCell(), MyTreeView.ROLE_CUSTOM_PAINT)
        first = items[0]
        first.setData("variant", self.ROLE_KIND)
        first.setData(txid, self.ROLE_KEY)
        first.setData(" ".join(labels).lower(), self.ROLE_SEARCH)
        return items

    def _subheader_row(self):
        """The grey row naming the transaction columns under a version.

        Owner, after the ZIP test (#129): the tree's header names the
        version level; under an open version this row names the columns of
        its transactions (the same texts as before, ``headers``). It is
        not selectable and has no menu.
        """
        labels = translated_headers(self.__class__.headers)
        items = []
        for column in self.Columns:
            cell = QStandardItem(labels[column])
            cell.setEditable(False)
            cell.setSelectable(False)
            cell.setBackground(QColor("#e4e4e4"))
            cell.setForeground(QColor("#444444"))
            font = cell.font()
            font.setBold(True)
            cell.setFont(font)
            items.append(cell)
        items[0].setData("subheader", self.ROLE_KIND)
        return items

    def _add_variants(self, parent_item, wid, version, will):
        """Add the row of ``wid`` under ``parent_item``, then its instalments.

        Only the rows of THE version in force read "In force" (green), even
        when a later draft flagged them REPLACED (#129). The rows of an
        older version still held by the will-executors keep their own
        status ("Replaced", in its colour), like the version's state:
        CHANGELOG #130, owner's ZIP test (#15 showed green "In force" rows
        under "Replaced (still held by the will-executors)").
        """
        states = self.history.states if self.history is not None else {}
        current = self.history.current if self.history is not None else None
        row = self._variant_row(
            wid, will[wid],
            in_force=(version.vid == current
                      and states.get(wid) == will_state.IN_FORCE),
        )
        parent_item.appendRow(row)
        for child in version.children.get(wid, []):
            self._add_variants(row[0], child, version, will)

    def _invalidation_items(self, inv):
        """The row of a paid invalidation (between the versions it ended)."""
        state = _("On the blockchain") if inv.confirmed else _("Mempool")
        sub = []
        if inv.fee is not None:
            sub.append("{} {}".format(_("Miner fee"), self._format_sats(inv.fee)))
        sub.append(inv.txid)
        return self._spanned_row(
            "invalidation", inv.txid, _("Paid invalidation"), " · ".join(sub),
            state, version_state_colour("invalidated"),
        )

    def _will_items(self):
        """``{txid: WillItem}`` of the list's will (dicts become items)."""
        out = {}
        for wid, item in self.will.items():
            if not isinstance(item, WillItem):
                item = WillItem(item)
            out[wid] = item
        return out

    def _default_expanded(self, version):
        """Every version starts closed (CHANGELOG #131, owner).

        #129 opened the draft and the version in force; the owner then
        asked that the transaction rows (txids, addresses) appear only when
        the user opens a version (double click or the arrow): too much data
        at first sight frightens a user. What the user opens stays open
        while the window lives (``self._expanded``).
        """
        return False

    def _restore_expanded(self):
        """Open or close each row as the owner left it, else the default."""
        model = self.model()
        stack = [(model.index(r, 0), None) for r in range(model.rowCount())]
        while stack:
            idx, version = stack.pop()
            kind, key = self._row_info(idx)
            if kind == "version":
                version = self._versions.get(key)
                default = bool(version) and self._default_expanded(version)
            elif kind == "variant":
                default = False
            else:
                continue
            row_key = self._row_key(idx)
            self.setExpanded(idx, self._expanded.get(row_key, default))
            for r in range(model.rowCount(idx)):
                child = model.index(r, 0, idx)
                if model.hasChildren(child):
                    stack.append((child, version))

    def _put_history_button(self, item, kind, text):
        """Put the "Show all history" / "Hide history" button on ``item``'s row.

        Owner, ZIP test of #129: as a plain text row it was confused with
        the versions; it is now a real push button, centred under the list,
        with an arrow. ``kind`` is "show_all" or "hide_all"; the button is
        reachable as ``self.history_button`` (tests).
        """
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 4, 0, 4)
        button = QPushButton(text)
        button.setObjectName("history_button")
        button.clicked.connect(
            self.show_all_history if kind == "show_all" else self.hide_history
        )
        layout.addStretch(1)
        layout.addWidget(button)
        layout.addStretch(1)
        self.setIndexWidget(self.model().indexFromItem(item), box)
        self.history_button = button

    def _hideable_versions(self):
        """How many versions "Hide history" would hide (0: no such row).

        The same count as "Show all history ({} older versions)" when the
        list shows only the last versions; never raises.
        """
        if self.history is None:
            return 0
        try:
            return will_versions.display_rows(self.history, show_all=False)[1]
        except Exception:
            return 0

    def _find_row(self, kind, key):
        """The first-cell index of the row ``(kind, key)``, or ``None``."""
        model = self.model()
        stack = [model.index(r, 0) for r in range(model.rowCount())]
        while stack:
            idx = stack.pop()
            if self._row_info(idx) == (kind, key):
                return idx
            stack.extend(model.index(r, 0, idx) for r in range(model.rowCount(idx)))
        return None

    def rebuild(self):
        """Redraw the tree from the will (see the class docstring).

        Keeps, across the frequent refreshes: the rows opened or closed,
        the current row and the scroll position. Display only: nothing is
        written to the will.
        """
        current = self._row_info(self.selectionModel().currentIndex())
        scroll = self.verticalScrollBar().value()
        will = self._will_items()
        wallet = getattr(self.bal_window, "wallet", None)
        try:
            self.history = will_versions.build_history(will, wallet)
            rows, self.hidden_versions = will_versions.display_rows(
                self.history, show_all=self._show_all)
        except Exception as e:
            # The list must always show the transactions: on an unexpected
            # error, every item as one "other transactions" group.
            _logger.error(f"will versions failed: {e}")
            other = will_versions._make_version("other", sorted(will), will, {})
            other.state = will_versions.VERSION_OTHER
            self.history = None
            rows, self.hidden_versions = [("version", other)], 0
        self._rebuilding = True
        try:
            model = self.model()
            model.clear()
            self.update_headers(translated_headers(self.__class__.headers))
            self._versions = {}
            for kind, obj in rows:
                if kind == "invalidation":
                    model.appendRow(self._invalidation_items(obj))
                    continue
                self._versions[obj.vid] = obj
                number, head, sub = self._version_texts(obj)
                row = self._spanned_row(
                    "version", obj.vid, head, sub,
                    version_state_text(obj.state), version_state_colour(obj.state),
                    number=number,
                )
                if obj.roots:
                    row[0].appendRow(self._subheader_row())
                for wid in obj.roots:
                    self._add_variants(row[0], wid, obj, will)
                model.appendRow(row)
            history_button = None
            self.history_button = None
            if self.hidden_versions:
                history_button = (
                    "show_all",
                    "▼  " + _("Show all history ({} older versions)").format(
                        self.hidden_versions),
                )
            elif self._show_all and self._hideable_versions():
                history_button = ("hide_all", "▲  " + _("Hide history"))
            if history_button is not None:
                kind, text = history_button
                # The row only holds the place: a real button is put on it
                # below (owner: the text row looked like the list's rows).
                button_row = self._spanned_row(kind, kind, "", "", background=None)
                model.appendRow(button_row)
            for r in range(model.rowCount()):
                self.setFirstColumnSpanned(r, QModelIndex(), True)
            if history_button is not None:
                self._put_history_button(button_row[0], kind, text)
            self._restore_expanded()
            if current != (None, None):
                idx = self._find_row(*current)
                if idx is not None:
                    self.selectionModel().setCurrentIndex(
                        idx,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
        finally:
            self._rebuilding = False
        self.hide_rows()
        self.verticalScrollBar().setValue(scroll)

    def hide_rows(self):
        """Apply the search box to the versions AND to their transactions.

        Electrum's ``MyTreeView.hide_rows`` only walks the top-level rows
        (and needs ``filter_columns`` cells with text). Here a version stays
        visible when its own text or one of its transactions matches; under
        a version that does not match, only the matching transactions stay.
        """
        text = (self.current_filter or "").lower()
        model = self.model()
        root = QModelIndex()
        for r in range(model.rowCount()):
            idx = model.index(r, 0)
            if not text:
                self.setRowHidden(r, root, False)
                for c in range(model.rowCount(idx)):
                    self._show_subtree(model.index(c, 0, idx), idx, "")
                continue
            own = text in str(idx.data(self.ROLE_SEARCH) or "")
            any_child = False
            subheader = None
            for c in range(model.rowCount(idx)):
                child = model.index(c, 0, idx)
                if child.data(self.ROLE_KIND) == "subheader":
                    subheader = c
                    continue
                if self._show_subtree(child, idx, "" if own else text):
                    any_child = True
            if subheader is not None:
                # The column names stay while a transaction is shown.
                self.setRowHidden(subheader, idx, not (own or any_child))
            self.setRowHidden(r, root, not (own or any_child))

    def _show_subtree(self, idx, parent, text):
        """Hide the transaction rows that do not match ``text`` (``""``: show all).

        Returns True when ``idx`` or one of its instalments matches.
        """
        model = self.model()
        own = not text or text in str(idx.data(self.ROLE_SEARCH) or "")
        any_child = False
        for c in range(model.rowCount(idx)):
            if self._show_subtree(model.index(c, 0, idx), idx, "" if own else text):
                any_child = True
        visible = own or any_child
        self.setRowHidden(idx.row(), parent, not visible)
        return visible

    def update(self):
        """Redraw the list, the toolbar fields and the will summary."""
        try:
            self.menu.removeAction(self.importaction)
        except Exception:
            pass

        if self.will is None:
            return

        self.rebuild()
        try:
            self.will_settings_widget.on_locktime_change()
        except Exception as _e:
            pass
        # CHANGELOG #84a: the summary box above the list says the same thing
        # in words; redraw it with the list.
        try:
            self.bal_window.update_will_summary()
        except Exception as _e:
            pass

    def create_toolbar(self, config):
        toolbar, menu = self.create_toolbar_with_menu("")
        menu.addAction(_("Prepare"), self.build_transactions)
        menu.addAction(_("Display"), self.bal_window.preview_modal_dialog)
        menu.addAction(_("Sign"), self.ask_password_and_sign_transactions)
        # Export/Import open a single window that offers all transports
        # (file / QR / audio). The Choose Filter / transport settings live
        # inside that window.
        menu.addAction(_("Export"), self.export_will)
        menu.addAction(_("Import"), self.import_will)
        menu.addAction(_("Merge"), self.merge_will)
        # CHANGELOG #94 (owner): "will" in every command, the same word as
        # the rest of the plugin ("Broadcast" alone read "Trasmissione" and
        # "Invalidate" read "Invalida il piano" in Italian).
        menu.addAction(_("Broadcast will"), self.broadcast)
        menu.addAction(_("Check"), self.check)
        menu.addAction(_("Invalidate will"), self.invalidate_will)
        # CHANGELOG #92: the inheritance plan report (save as file / print).
        menu.addSeparator()
        menu.addMenu(report_menu(self.bal_window, self, title=_("Will report")))

        # The Wizard is the main entry point to create an inheritance, so make
        # it stand out: show a bold label next to a slightly larger icon (the
        # plain icon-only button was too easy to overlook).
        # #127 (owner): the same wizard also changes a will already made, so
        # the button says both. The will summary, shown only when there is
        # no will yet, keeps "Build Your Will".
        wizard = self._big_button(
            _("Build / Edit Your Will"), "icons/wizard.png",
            _("Wizard - Build your will"), self.bal_window.init_wizard,
            framed=True,
        )
        # display = QPushButton(_("Display"))
        # display.clicked.connect(self.bal_window.preview_modal_dialog)

        # CHANGELOG #95 (owner): Check is the command used most after the
        # Wizard (new funds, a change, a will-executor that did not get the
        # will), but as a small icon at the far right it was easy to miss.
        # Same look as the Wizard, right after it, with its name on it. The
        # tooltip names the auto-sign option as it is translated, because
        # with that option off the Check only shows how to sign and send.
        refresh = self._big_button(
            _("Check your will"), "icons/reload.png",
            _(
                "Rebuild the will if something has changed, send it to the "
                "will-executors and check that they have it.\n"
                "With '{}' off, it shows how to sign and send it."
            ).format(_("Auto-sign on Check")),
            self.check,
        )

        widget = QWidget(self)
        hlayout = QHBoxLayout(widget)
        hlayout.setContentsMargins(0, 0, 0, 0)
        self.will_settings_widget = WillSettingsWidget(self.bal_window, self)
        # CHANGELOG #130 (owner): the calendar reminders are a command of
        # the Will tab only, in BASIC and ADVANCED alike: a big button next
        # to the two main commands, and an entry in Tools.
        self.calendar_button = self._big_calendar_button()
        calendar_menu = self.calendar_button.menu()
        calendar_menu.setTitle(_("Calendar reminders"))
        menu.addMenu(calendar_menu)
        # Toolbar order (left -> right):
        #   Wizard | Check | Delivery time | Check Alive | fee ... Calendar
        # The two main commands go first, on the LEFT (#95: Check used to
        # be last); the settings widget shows its fields in ADVANCED only
        # (#130). The row takes the toolbar's free width (stretch 1), so
        # the buttons stay on the left even when BASIC hides every field,
        # and the calendar sits on the right, next to Tools (owner).
        hlayout.addWidget(wizard)
        hlayout.addWidget(refresh)
        hlayout.addWidget(self.will_settings_widget)
        hlayout.addStretch(1)
        hlayout.addWidget(self.calendar_button)
        toolbar.insertWidget(2, widget, 1)

        self.menu = menu
        self.toolbar = toolbar
        return toolbar

    def _big_button(self, text, icon_path, tooltip, slot, framed=False):
        """A main toolbar command: bold name next to a larger icon.

        Used for the Wizard and the Check (CHANGELOG #95), so the two main
        commands look the same; ``framed`` adds the blue frame of
        :data:`FRAMED_BUTTON_STYLE` (#131: the Wizard only).
        """
        button = QPushButton("  {}".format(text))
        button.setIcon(
            read_QIcon_from_bytes(self.bal_window.bal_plugin.read_file(icon_path))
        )
        button.setIconSize(QSize(28, 28))
        button.setMinimumHeight(40)
        button.setStyleSheet(FRAMED_BUTTON_STYLE if framed else BIG_BUTTON_STYLE)
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

    def _big_calendar_button(self):
        """The Will tab's calendar reminders button (CHANGELOG #130).

        Owner: the small calendar icon of the toolbar was hard to see next
        to the two big commands; the button is now as tall as they are,
        with a doubled icon. Same menu (Open / Open with... / Save) and the
        same reminder dates as before: the .ics comes from the Will tab's
        settings widget (``WillSettingsWidget._ics_provider``).
        """
        button = BalCalendarButton(
            self.bal_window, self.will_settings_widget._ics_provider, self
        )
        button.setIcon(
            read_QIcon_from_bytes(
                self.bal_window.bal_plugin.read_file("icons/calendar.png")
            )
        )
        button.setIconSize(QSize(28, 28))
        button.setMinimumSize(QSize(48, 40))
        button.setToolTip(_("Export reminder dates to your calendar (.ics)"))
        return button

    def build_transactions(self):
        will = self.bal_window.prepare_will()
        if will:
            self.update_will(will)

    def export_will(self):
        self.bal_window.export_will_dialog()

    def import_will(self):
        self.bal_window.import_will_dialog()

    def import_will_into_details(self):
        self.bal_window.import_will_into_details()

    def merge_will(self):
        self.bal_window.merge_will_ui()

    def ask_password_and_sign_transactions(self):
        self.bal_window.ask_password_and_sign_transactions(callback=self.update)

    def broadcast(self):
        self.bal_window.broadcast_transactions()
        self.update()

    def check(self):
        """The Check button: check, prepare, sign, send and verify the will.

        The will-executors are verified by the Building Will dialog itself,
        as its last step (CHANGELOG #84b-2), for every sent item not yet
        verified (``Will.needs_server_check``). Before, this method ran that
        check after the dialog had closed, in a separate "Check Transaction"
        window.
        """
        close_window = BalBuildWillDialog(self.bal_window)
        close_window.build_will_task()
        self.update()
        # NOTE (Group B / B2): signing + broadcasting is already performed
        # automatically by BalBuildWillDialog.build_will_task() above (called at
        # the start of check()). We must NOT trigger a second sign/broadcast
        # cycle here, otherwise the will would be broadcast twice. Whether the
        # automatic sign/broadcast runs silently or shows the manual "next step"
        # hints is controlled by the AUTO_SIGN setting inside that dialog.

    def invalidate_will(self):
        self.bal_window.invalidate_will()
        self.update()


# class PreviewDialog(BalDialog, MessageBoxMixin):
#    def __init__(self, bal_window, will):
#        self._bal_parent = bal_window.window
#        BalDialog.__init__(
#            self, bal_window=bal_window, bal_plugin=bal_window.bal_plugin
#        )
#        self.bal_plugin = bal_window.bal_plugin
#        self.gui_object = self.bal_plugin.gui_object
#        self.config = self.bal_plugin.config
#        self.bal_window = bal_window
#        self.wallet = bal_window.window.wallet
#        self.format_amount = bal_window.window.format_amount
#        self.base_unit = bal_window.window.base_unit
#        self.format_fiat_and_units = bal_window.window.format_fiat_and_units
#        self.fx = bal_window.window.fx
#        self.format_fee_rate = bal_window.window.format_fee_rate
#        self.show_address = bal_window.window.show_address
#        if not will:
#            self.will = bal_window.willitems
#        else:
#            self.will = will
#        self.setWindowTitle(_("Transactions Preview"))
#        self.setMinimumSize(1000, 200)
#        self.size_label = QLabel()
#        self.transactions_list = PreviewList(self.bal_window,self, self.will)
#
#        try:
#            self.bal_window.init_class_variables()
#        except Exception as e:
#            _logger.error(f"PreviewDialog Exception: {e}")
#        self.check_will()
#
#        vbox = QVBoxLayout(self)
#        vbox.addWidget(self.size_label)
#        vbox.addWidget(self.transactions_list)
#        buttonbox = QHBoxLayout()
#
#        b = QPushButton(_("Sign"))
#        b.clicked.connect(self.transactions_list.ask_password_and_sign_transactions)
#        buttonbox.addWidget(b)
#
#        b = QPushButton(_("Export Will"))
#        b.clicked.connect(self.transactions_list.export_will)
#        buttonbox.addWidget(b)
#
#        b = QPushButton(_("Broadcast"))
#        b.clicked.connect(self.transactions_list.broadcast)
#        buttonbox.addWidget(b)
#
#        b = QPushButton(_("Invalidate will"))
#        b.clicked.connect(self.transactions_list.invalidate_will)
#        buttonbox.addWidget(b)
#
#        vbox.addLayout(buttonbox)
#
#        self.update()
#
#    def update_will(self, will):
#        self.will.update(will)
#        self.transactions_list.update_will(will)
#        self.update()
#
#    def update(self):
#        self.transactions_list.update()
#
#    def is_hidden(self):
#        return self.isMinimized() or self.isHidden()
#
#    def show_or_hide(self):
#        if self.is_hidden():
#            self.bring_to_top()
#        else:
#            self.hide()
#
#    def bring_to_top(self):
#        self.show()
#        self.raise_()
#
#    def closeEvent(self, event):
#        event.accept()



class _FullUrlEditDelegate(QStyledItemDelegate):
    """Item delegate for the will-executor URL column.

    The URL column may display a SHORTENED form of long .onion addresses. This
    delegate ensures editing operates on the FULL url: the editor is preloaded
    from the real key role (which always holds the complete address) rather than
    from the visible (possibly shortened) cell text.
    """

    def __init__(self, url_role, parent=None):
        super().__init__(parent)
        self._url_role = url_role

    def createEditor(self, parent, option, index):
        return _QLineEdit(parent)

    def setEditorData(self, editor, index):
        # Prefer the full URL stored in the key role; fall back to the visible
        # text if for some reason the role is missing.
        full = index.data(self._url_role)
        if not full:
            full = index.data()
        editor.setText(full or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text())


class WillExecutorListWidget(MyTreeView):
    class Columns(MyTreeView.BaseColumnsEnum):
        SELECTED = enum.auto()
        URL = enum.auto()
        STATUS = enum.auto()
        BASE_FEE = enum.auto()
        INFO = enum.auto()
        ADDRESS = enum.auto()

    headers = {
        Columns.SELECTED: "",
        Columns.URL: N_("Url"),
        Columns.STATUS: N_("S"),
        Columns.BASE_FEE: N_("Base fee"),
        Columns.INFO: N_("Info"),
        Columns.ADDRESS: N_("Default Address"),
    }

    filter_columns = [Columns.URL]

    ROLE_SORT_ORDER = Qt.ItemDataRole.UserRole + 3000
    ROLE_HEIR_KEY = Qt.ItemDataRole.UserRole + 3001
    key_role = ROLE_HEIR_KEY

    def __init__(self, parent: "WillExecutorWidget"):
        super().__init__(
            parent=parent,
            stretch_column=self.Columns.ADDRESS,
            editable_columns=[
                self.Columns.URL,
                self.Columns.BASE_FEE,
                self.Columns.ADDRESS,
                self.Columns.INFO,
            ],
        )
        self._bal_parent = parent
        try:
            self.setModel(QStandardItemModel(self))
            self.sortByColumn(self.Columns.SELECTED, Qt.SortOrder.AscendingOrder)
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        except Exception:
            pass
        self.setSortingEnabled(True)
        self.std_model = self.model()
        self.config = parent.bal_plugin.config
        self.get_decimal_point = parent.bal_plugin.get_decimal_point

        # The URL column may DISPLAY a shortened form of long .onion addresses
        # (see update()).  Without this, double-clicking to edit would load the
        # shortened text (with the ellipsis) into the editor and saving it would
        # corrupt the will-executor key.  This delegate makes the editor load the
        # FULL url from the real key role instead of the visible (shortened)
        # text, so edits always operate on the complete address.
        try:
            self.setItemDelegateForColumn(
                self.Columns.URL,
                _FullUrlEditDelegate(self.ROLE_HEIR_KEY + self.Columns.URL, self),
            )
        except Exception:
            pass

        self.update()

    def create_menu(self, position):
        menu = QMenu()
        idx = self.indexAt(position)
        column = idx.column() or self.Columns.URL
        selected_keys = []
        sel_key = None
        for s_idx in self.selected_in_column(self.Columns.URL):
            item = self.model().itemFromIndex(s_idx)
            # Use the FULL url stored in the key role, NOT item.data(0): the
            # latter is the (possibly shortened) DISPLAY text, and using it as a
            # dict key breaks delete/select/deselect/ping for long .onion URLs
            # (KeyError). Fall back to the display text only if the role is
            # unexpectedly missing.
            sel_key = item.data(self.ROLE_HEIR_KEY + self.Columns.URL) or item.data(0)
            selected_keys.append(sel_key)
        if selected_keys and idx.isValid():
            column_title = self.model().horizontalHeaderItem(column).text()
            # column_data = "\n".join(
            #    self.model().itemFromIndex(s_idx).text()
            #    for s_idx in self.selected_in_column(column)
            # )
            # When exactly ONE cell is selected, offer "Copy" to copy the value
            # of that cell to the clipboard (e.g. a single url / address / fee).
            # This list has no ``main_window``, so use QApplication directly.
            if len(self.selectionModel().selectedIndexes()) == 1:
                cell_value = self.model().itemFromIndex(idx).text()
                menu.addAction(
                    _("Copy"),
                    lambda: QApplication.clipboard().setText(cell_value),
                )
            if Willexecutors.is_selected(self._bal_parent.willexecutors_list[sel_key]):
                menu.addAction(
                    _("deselect").format(column_title),
                    lambda: self.deselect(selected_keys),
                )
            else:
                menu.addAction(
                    _("select").format(column_title), lambda: self.select(selected_keys)
                )
            if column in self.editable_columns:
                item = self.model().itemFromIndex(idx)
                if item.isEditable():
                    persistent = QPersistentModelIndex(idx)
                    menu.addAction(
                        _("Edit {}").format(column_title),
                        lambda p=persistent: self.edit(QModelIndex(p)),
                    )

            menu.addAction(
                _("Ping").format(column_title),
                lambda: self.ping_willexecutors(selected_keys),
            )
            menu.addSeparator()
            menu.addAction(
                _("delete").format(column_title), lambda: self.delete(selected_keys)
            )

        menu.exec(self.viewport().mapToGlobal(position))

    def ping_willexecutors(self, selected_keys):
        wout = {}
        for k in selected_keys:
            wout[k] = self._bal_parent.willexecutors_list[k]
        self._bal_parent.update_willexecutors(wout)

        self._bal_parent.save_willexecutors()
        self.update()

    def on_activated(self, idx):
        self.on_double_click(idx)

    def on_double_click(self, idx):
        edit_key = self.get_edit_key_from_coordinate(
            idx.row(), self.Columns.URL
        )
        if edit_key and edit_key in self._bal_parent.willexecutors_list:
            self._bal_parent.add(edit_key)

    def get_edit_key_from_coordinate(self, row, col):
        role = self.ROLE_HEIR_KEY + col
        a = self.get_role_data_from_coordinate(row, col, role=role)
        return a

    def delete(self, selected_keys):
        """Delete the selected will-executors, except the Bitcoin After Life
        one: it is kept and a message says to deselect it (CHANGELOG #108;
        before, it came back selected at the next start on testnet)."""
        plugin = self._bal_parent.bal_plugin
        kept = [k for k in selected_keys if Willexecutors.is_protected(plugin, k)]
        for key in selected_keys:
            if key not in kept:
                del self._bal_parent.willexecutors_list[key]

        self._bal_parent.save_willexecutors()
        self.update()
        if kept:
            self._bal_parent.show_message(
                "\n".join(protected_executor_message(k) for k in kept)
            )

    def select(self, selected_keys):
        for wid, w in self._bal_parent.willexecutors_list.items():
            if wid in selected_keys:
                w["selected"] = True
        self._bal_parent.save_willexecutors()
        self.update()

    def deselect(self, selected_keys):
        for wid, w in self._bal_parent.willexecutors_list.items():
            if wid in selected_keys:
                w["selected"] = False
        self._bal_parent.save_willexecutors()
        self.update()

    def on_edited(self, idx, edit_key, *, text):
        # prior_name = self._bal_parent.willexecutors_list[edit_key]
        col = idx.column()
        try:
            if col == self.Columns.URL:
                if text != edit_key and Willexecutors.is_protected(
                    self._bal_parent.bal_plugin, edit_key
                ):
                    # Renaming is deleting the old URL (CHANGELOG #108): put
                    # the URL back and say why.
                    self.update()
                    self._bal_parent.show_message(protected_executor_message(edit_key))
                    return
                self._bal_parent.willexecutors_list[text] = self._bal_parent.willexecutors_list[
                    edit_key
                ]
                del self._bal_parent.willexecutors_list[edit_key]
            if col == self.Columns.BASE_FEE:
                self._bal_parent.willexecutors_list[edit_key]["base_fee"] = (
                    Util.encode_amount(text, self.get_decimal_point())
                )
            if col == self.Columns.ADDRESS:
                self._bal_parent.willexecutors_list[edit_key]["address"] = text
            if col == self.Columns.INFO:
                self._bal_parent.willexecutors_list[edit_key]["info"] = text
            self._bal_parent.save_willexecutors()
            self.update()
        except Exception:
            pass

    def update(self):
        if self._bal_parent.willexecutors_list is None:
            return
        try:
            current_key = self.get_role_data_for_current_item(
                col=self.Columns.URL, role=self.ROLE_HEIR_KEY
            )
            self.model().clear()
            self.update_headers(translated_headers(self.__class__.headers))

            set_current = None

            for url, value in self._bal_parent.willexecutors_list.items():
                labels = [""] * len(self.Columns)
                # Long Tor (.onion) URLs overflow the column; show a shortened
                # form (first 37 chars + ellipsis, matching the welist site)
                # while keeping the FULL url as the real key data (setData below)
                # and in the tooltip, so nothing downstream breaks. Short URLs
                # are shown unchanged.
                display_url = url if len(url) <= 40 else url[:37] + "\u2026"
                labels[self.Columns.URL] = display_url
                if Willexecutors.is_selected(value):

                    labels[self.Columns.SELECTED] = [
                        read_QIcon_from_bytes(
                            self._bal_parent.bal_plugin.read_file("icons/confirmed.png")
                        ),
                        "",
                    ]
                else:
                    labels[self.Columns.SELECTED] = ""
                labels[self.Columns.BASE_FEE] = Util.decode_amount(
                    value.get("base_fee", 0), self.get_decimal_point()
                )
                if str(value.get("status", 0)) == "200":
                    labels[self.Columns.STATUS] = [
                        read_QIcon_from_bytes(
                            self._bal_parent.bal_plugin.read_file(
                                "icons/status_connected.png"
                            )
                        ),
                        "",
                    ]
                else:
                    labels[self.Columns.STATUS] = [
                        read_QIcon_from_bytes(
                            self._bal_parent.bal_plugin.read_file("icons/unconfirmed.png")
                        ),
                        "",
                    ]
                labels[self.Columns.ADDRESS] = str(value.get("address", ""))
                labels[self.Columns.INFO] = str(value.get("info", ""))

                items = []
                for e in labels:
                    if isinstance(e, list):
                        try:
                            items.append(QStandardItem(*e))
                        except Exception as e:
                            pass
                    else:
                        items.append(QStandardItem(e))

                max_fee = self._bal_parent.bal_plugin.MAX_WILLEXECUTOR_FEE.get()
                dust = self._bal_parent.bal_window.window.wallet.dust_threshold()
                if not Willexecutors.is_valid(value, max_fee=max_fee, dust=dust):
                    grey = QColor("#808080")
                    for item in items:
                        font = item.font()
                        font.setItalic(True)
                        item.setFont(font)
                        item.setForeground(grey)

                items[self.Columns.SELECTED].setEditable(False)
                items[self.Columns.URL].setEditable(True)
                items[self.Columns.ADDRESS].setEditable(True)
                items[self.Columns.INFO].setEditable(True)
                items[self.Columns.BASE_FEE].setEditable(True)
                items[self.Columns.STATUS].setEditable(False)

                items[self.Columns.URL].setData(
                    url, self.ROLE_HEIR_KEY + self.Columns.URL
                )
                # Full URL on hover (the visible text may be shortened above).
                items[self.Columns.URL].setToolTip(url)
                items[self.Columns.BASE_FEE].setData(
                    url, self.ROLE_HEIR_KEY + self.Columns.BASE_FEE
                )
                items[self.Columns.INFO].setData(
                    url, self.ROLE_HEIR_KEY + self.Columns.INFO
                )
                items[self.Columns.ADDRESS].setData(
                    url, self.ROLE_HEIR_KEY + self.Columns.ADDRESS
                )
                row_count = self.model().rowCount()
                self.model().insertRow(row_count, items)
                if url == current_key:
                    idx = self.model().index(row_count, self.Columns.URL)
                    set_current = QPersistentModelIndex(idx)
                self.set_current_idx(set_current)
            self.filter()
        except Exception as e:
            _logger.error(f"error updating willexcutor {e}")
            raise e



class WillExecutorWidget(QWidget, MessageBoxMixin):
    def __init__(self, parent, bal_window, willexecutors=None):
        self.bal_window = bal_window
        self.bal_plugin = bal_window.bal_plugin
        self._bal_parent = parent
        MessageBoxMixin.__init__(self)
        QWidget.__init__(self, parent)
        if willexecutors:
            self.willexecutors_list = willexecutors
        else:
            self.willexecutors_list = Willexecutors.get_willexecutors(self.bal_plugin)

        self.size_label = QLabel()
        self.will_executor_list_widget = WillExecutorListWidget(self)

        vbox = QVBoxLayout(self)
        vbox.addWidget(self.size_label)

        widget = QWidget()
        hbox = QHBoxLayout(widget)
        hbox.addWidget(QLabel(_("Add transactions without willexecutor")))
        heir_no_willexecutor = BalCheckBox(self.bal_plugin.NO_WILLEXECUTOR)
        hbox.addWidget(heir_no_willexecutor)
        spacer_widget = QWidget()
        spacer_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        hbox.addWidget(spacer_widget)
        vbox.addWidget(widget)

        vbox.addWidget(self.will_executor_list_widget)
        buttonbox = QHBoxLayout()

        b = QPushButton(_("Add"))
        b.clicked.connect(self.add)
        buttonbox.addWidget(b)

        b = QPushButton(_("Download List"))
        b.clicked.connect(self.download_list)
        buttonbox.addWidget(b)

        b = QPushButton(_("Import"))
        b.clicked.connect(self.import_file)
        buttonbox.addWidget(b)

        def _menu_button(label):
            # ``label`` must already be translated by the caller.
            btn = QToolButton()
            btn.setText(label)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            buttonbox.addWidget(btn)
            return btn

        export_btn = _menu_button(_("Export"))
        export_menu = QMenu(export_btn)
        export_menu.addAction(_("Export all"), lambda: self.export_file())
        export_menu.addAction(
            _("Export selected"), lambda: self.export_file(subset="selected")
        )
        export_menu.addAction(
            _("Export only valid"), lambda: self.export_file(subset="valid")
        )
        export_btn.setMenu(export_menu)

        ping_btn = _menu_button(_("Ping All"))
        ping_menu = QMenu(ping_btn)
        ping_menu.addAction(_("Ping all"), lambda: self.update_willexecutors())
        ping_menu.addAction(
            _("Ping selected"), lambda: self.ping_selected_willexecutors()
        )
        ping_btn.setMenu(ping_menu)

        select_btn = _menu_button(_("Select All"))
        select_menu = QMenu(select_btn)
        select_menu.addAction(_("Select all"), lambda: self.set_select_all(True))
        select_menu.addAction(
            _("Select only valid"), lambda: self.set_select_all(True, only_valid=True)
        )
        select_menu.addAction(_("Deselect all"), lambda: self.set_select_all(False))
        select_menu.addAction(
            _("Deselect only invalid"),
            lambda: self.set_select_all(False, only_valid=True),
        )
        select_btn.setMenu(select_menu)

        vbox.addLayout(buttonbox)
        # self.will_executor_list_widget.update()

    def add(self, edit_key=None):
        executor = None
        if edit_key:
            executor = self.willexecutors_list.get(edit_key)
            title = _("Edit: {}").format(edit_key)
        else:
            title = _("New Will Executor")
        d = BalDialog(
            self.bal_window.window,
            self.bal_plugin,
            self.bal_plugin.get_window_title(title),
        )

        vbox = QVBoxLayout(d)
        grid = QGridLayout()

        url_edit = QLineEdit()
        url_edit.setFixedWidth(32 * char_width_in_lineedit())
        info_edit = QLineEdit(_("New Will Executor"))
        info_edit.setFixedWidth(32 * char_width_in_lineedit())
        base_fee_spin = QSpinBox()
        base_fee_spin.setRange(0, 1000000)
        base_fee_spin.setValue(0)
        address_edit = QLineEdit()
        address_edit.setFixedWidth(32 * char_width_in_lineedit())
        sync_btn = QPushButton("\u21BB")
        sync_btn.setFixedWidth(32)
        loading_label = QLabel()
        d._sync_active = False

        if executor:
            url_edit.setText(edit_key)
            info_edit.setText(str(executor.get("info", _("New Will Executor"))))
            base_fee_spin.setValue(int(executor.get("base_fee", 0)))
            address_edit.setText(str(executor.get("address", "")))

        def on_sync():
            url = url_edit.text().strip()
            if not url:
                self.show_error(_("URL is required"))
                return
            tmp = {}
            sync_btn.setEnabled(False)
            loading_label.setText("\u27F3")
            d._sync_active = True

            def task():
                return Willexecutors.get_info_task(url, tmp)

            def on_success(result):
                if not getattr(d, "_sync_active", False):
                    return
                if result.get("status") == 200:
                    info_edit.setText(str(result.get("info", "")))
                    base_fee_spin.setValue(int(result.get("base_fee", 0)))
                    address_edit.setText(str(result.get("address", "")))
                    if edit_key and edit_key in self.willexecutors_list:
                        self.willexecutors_list[edit_key].update({
                            "status": 200,
                            "info": result.get("info", ""),
                            "base_fee": result.get("base_fee", 0),
                            "address": result.get("address", ""),
                            "last_update": result.get(
                                "last_update", datetime.now().timestamp()
                            ),
                        })
                        self.will_executor_list_widget.update()
                else:
                    QMessageBox.warning(
                        d,
                        _("Error"),
                        _("Could not reach server at {}").format(url),
                    )
                    url_edit.setFocus()
                    url_edit.selectAll()
                sync_btn.setEnabled(True)
                loading_label.clear()
                d._sync_active = False

            def on_error(exc_info):
                if not getattr(d, "_sync_active", False):
                    return
                QMessageBox.warning(
                    d,
                    _("Error"),
                    _("Error contacting server: {}").format(
                        str(exc_info[1])
                    ),
                )
                url_edit.setFocus()
                url_edit.selectAll()
                sync_btn.setEnabled(True)
                loading_label.clear()
                d._sync_active = False

            def on_done():
                pass

            sync_thread = TaskThread(d)
            sync_thread.add(
                task, on_success=on_success, on_done=on_done, on_error=on_error
            )

        sync_btn.clicked.connect(on_sync)

        if not edit_key:
            add_another_btn = QPushButton(_("Add another"))
            self._add_another = False

            def add_another():
                self._add_another = True
                d.accept()

            add_another_btn.clicked.connect(add_another)
        else:
            self._add_another = False
            add_another_btn = None

        row = 0
        grid.addWidget(QLabel(_("URL")), row, 0)
        grid.addWidget(url_edit, row, 1)
        grid.addWidget(sync_btn, row, 2)
        grid.addWidget(loading_label, row, 3)
        grid.addWidget(
            HelpButton(_("Will executor server URL (e.g. http://192.168.1.100:8080)")),
            row,
            4,
        )

        row += 1
        grid.addWidget(QLabel(_("Info")), row, 0)
        grid.addWidget(info_edit, row, 1)
        grid.addWidget(
            HelpButton(_("A short description or name for this executor")),
            row,
            2,
        )

        row += 1
        grid.addWidget(QLabel(_("Base Fee (sats)")), row, 0)
        grid.addWidget(base_fee_spin, row, 1)
        grid.addWidget(
            HelpButton(_("Base fee in satoshis")),
            row,
            2,
        )

        row += 1
        grid.addWidget(QLabel(_("Address")), row, 0)
        grid.addWidget(address_edit, row, 1)
        grid.addWidget(
            HelpButton(_("Bitcoin address for fee payments (optional)")),
            row,
            2,
        )

        vbox.addLayout(grid)
        buttons = [CancelButton(d), OkButton(d)]
        if not edit_key:
            buttons.append(add_another_btn)
        vbox.addLayout(Buttons(*buttons))

        while d.exec():
            url = url_edit.text().strip()
            if not url:
                self.show_error(_("URL is required"))
                continue
            if edit_key:
                old_url = edit_key
                if url != old_url and Willexecutors.is_protected(
                    self.bal_plugin, old_url
                ):
                    # Renaming is deleting the old URL (CHANGELOG #108).
                    self.show_error(protected_executor_message(old_url))
                    continue
                ex = self.willexecutors_list[old_url]
                ex.update({
                    "info": info_edit.text().strip() or "New Will Executor",
                    "base_fee": base_fee_spin.value(),
                    "address": address_edit.text().strip(),
                })
                if url != old_url:
                    self.willexecutors_list[url] = ex
                    del self.willexecutors_list[old_url]
            else:
                self.willexecutors_list[url] = {
                    "info": info_edit.text().strip() or "New Will Executor",
                    "base_fee": base_fee_spin.value(),
                    "address": address_edit.text().strip(),
                    "selected": False,
                    "status": "-1",
                }
            self.will_executor_list_widget.update()
            Willexecutors.save(self.bal_window.bal_plugin, self.willexecutors_list)
            if not self._add_another:
                break
            self._add_another = False
            url_edit.clear()
            info_edit.setText(_("New Will Executor"))
            base_fee_spin.setValue(0)
            address_edit.clear()

    def download_list(self, wes=None):
        # Both this button and the wizard go through the same code path on
        # BalWindow, which shows a "Downloading..." dialog (non-blocking GUI),
        # tries the configured + fallback servers, logs the technical details
        # and shows a simple message on failure.
        def on_success(result):
            self.willexecutors_list.update(result)
            self.will_executor_list_widget.update()
            Willexecutors.save(self.bal_window.bal_plugin, self.willexecutors_list)
            self.update()

        self.bal_window.download_list(self.bal_window.willexecutors, on_success)

    def export_file(self, subset=None):
        data = self.export_data(subset)
        if subset and not data:
            self.show_message(_("No will-executor matches the selected filter"))
            return
        export_meta_gui(
            self.bal_window.window,
            "willexecutors.json",
            partial(self.export_json_file, subset=subset),
        )

    def export_data(self, subset=None):
        data = self.willexecutors_list
        if subset == "selected":
            data = {
                url: we
                for url, we in data.items()
                if Willexecutors.is_selected(we)
            }
        elif subset == "valid":
            valid = self._validity()
            data = {
                url: we for url, we in data.items() if valid.get(url, False)
            }
        return data

    def export_json_file(self, path, subset=None):
        write_json_file(path, self.export_data(subset))

    def _validity(self):
        max_fee = self.bal_plugin.MAX_WILLEXECUTOR_FEE.get()
        dust = self.bal_window.window.wallet.dust_threshold()
        return {
            url: Willexecutors.is_valid(we, max_fee=max_fee, dust=dust)
            for url, we in self.willexecutors_list.items()
        }

    def _selected_willexecutors(self):
        return {
            url: we
            for url, we in self.willexecutors_list.items()
            if Willexecutors.is_selected(we)
        }

    def set_select_all(self, select, only_valid=False):
        """Apply a bulk selection across all will-executors.

        ``select=True`` selects all (or only the valid ones when
        ``only_valid=True``, deselecting the invalid ones); ``select=False``
        deselects all (or only the invalid ones when ``only_valid=True``,
        leaving the valid ones selected).
        """
        valid = self._validity() if only_valid else None
        _apply_select_all(self.willexecutors_list, select, valid)
        self.save_willexecutors()

    def ping_selected_willexecutors(self):
        wes = self._selected_willexecutors()
        if not wes:
            self.show_message(_("No will-executor is selected"))
            return
        self.update_willexecutors(wes)

    def import_file(self):
        import_meta_gui(
            self.bal_window.window,
            _("willexecutors"),
            self.import_json_file,
            self.willexecutors_list.update,
        )

    def update_willexecutors(self, wes=None):
        if not wes:
            wes = self.willexecutors_list
        self.bal_window.ping_willexecutors(wes, self.save_willexecutors)

    def import_json_file(self, path):
        data = read_json_file(path)
        data = self._validate(data)
        self.willexecutors_list.update(data)
        self.will_executor_list_widget.update()

    # TODO validate willexecutor json import file
    def _validate(self, data):
        return data

    def save_willexecutors(self, wes=None):
        if not wes:
            wes = self.willexecutors_list
        self.willexecutors_list.update(wes)
        self.will_executor_list_widget.update()
        Willexecutors.save(self.bal_window.bal_plugin, self.willexecutors_list)


