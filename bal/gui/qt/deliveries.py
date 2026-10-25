"""The window of the inheritance in several deliveries (CHANGELOG #170, M4a).

Owner, 2026-10-20 (the M4 plan and three drafts of the window):

* the table is HORIZONTAL: the deliveries go from left to right, as time in
  the Future view and in the chain of the blockchain; the heirs are the
  rows. Each column has a dot on a line, its title "Delivery n", its date
  and the time since the delivery before ("+2 years");
* the shares of the heirs are the important numbers (a bigger, bold
  font); the check sums (the total of each heir on the right, of each
  delivery at the bottom, and the 100 % in green) are small and grey;
* today at most 3 deliveries (``delivery.INSTALMENTS_MAX``), and they all
  fit in the window. Later there may be up to 12 (owner): only the columns
  of the deliveries scroll then, with a bar at the bottom, while the names
  on the left and the totals on the right stay in place;
* one problem at a time under the table, in plain words, from the SAME
  rules as the command line (:func:`bal.core.delivery.validate_instalments`,
  translated through :class:`bal.core.delivery.TableRuleError`); Save works
  only without a problem;
* owner decision (a): every change by which the heirs would receive part of
  the estate EARLIER is asked first (the command line asks for
  ``--confirm_earlier true``); a delivery later than the will in force says
  that the next Check asks for the paid invalidation;
* "Back to one delivery" removes the table (asked first: the rest of the
  estate would arrive at delivery 1).

ADVANCED only (owner decision D5). Opened from the wizard's "Several
deliveries" choice and from Tools in the Will tab
(``BalWindow.open_deliveries_editor``). The table is written only by
:func:`bal.engine.context.set_instalments` and removed by
:func:`bal.engine.context.clear_instalments`.
"""

import calendar
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from electrum.gui.qt.util import ColorScheme
from electrum.logging import get_logger
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import delivery, will_changes
from ...core.will import Will
from ...core.willexecutors import Willexecutors
from ...engine import context, instalments
from ...i18n import _, number_separators
from .dialogs import BalDialog

_logger = get_logger(__name__)

#: The grey of the check sums and of the notes (the heir dialog's hint).
_MUTED = "#6A737D"
#: How many delivery columns the window shows without scrolling: all of
#: today's (owner: with 3 everything fits; with more, the bar appears).
_VISIBLE_COLUMNS = 3


def _day_text(ts):
    """``DD/MM/YYYY``: the UTC day of timestamp ``ts`` (the day the
    transaction uses). Plain arithmetic from 1970: ``fromtimestamp`` fails
    after 2038 where ``time_t`` is 32-bit."""
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(ts))
    return moment.strftime("%d/%m/%Y")


def _qdate_of(ts):
    """The :class:`QDate` of the UTC day of ``ts``."""
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(ts))
    return QDate(moment.year, moment.month, moment.day)


def _ts_of(qdate):
    """00:00 UTC of the day ``qdate``: the form of a day typed on the command
    line (:func:`bal.core.delivery.parse_cli_date`) and of the example
    table's later dates (:func:`bal.core.delivery.add_months`)."""
    return calendar.timegm((qdate.year(), qdate.month(), qdate.day(), 0, 0, 0))


def _number_text(value):
    """A share without the "%", with the decimal separator of the language
    ("11,33" in Italian): :func:`bal.core.delivery.share_text`."""
    text = delivery.share_text(value)[:-1]
    return text.replace(".", number_separators()[0])


def _pct(value):
    """A share with its "%" ("11,33%" in Italian)."""
    return _number_text(value) + "%"


def shown_percent(text):
    """A stored percentage as the owner reads it (#185, owner's ZIP test):
    "16.5%" is "16,5%" in Italian, as in the "What changes" box; the
    stored text never changes. A text that is not a percentage is given
    back as it is."""
    text = str(text).strip()
    if not text.endswith("%"):
        return text
    share = delivery.as_share(text)
    if share is not None:
        return _pct(share)
    return text.replace(".", number_separators()[0])


def _read_cell(text):
    """The text of a cell as the rules read it: empty is 0; "11,33" (the
    decimal comma) is 11.33. Anything else is passed on as it is, so the
    rules say what is wrong with it."""
    text = (text or "").strip().replace("%", "").strip()
    if not text:
        return "0"
    return text.replace(",", ".")


def problem_text(error):
    """A broken rule of the table as the window says it (#170).

    The rule's text in the owner's language
    (:func:`bal.core.delivery.rule_text`). The English of the rules is a
    log line ("delivery 2: ..."): here the sentence starts with a capital
    letter, unless it starts with an heir's name, which stays as written.
    """
    text = delivery.rule_text(error, _)
    template = getattr(error, "template", "")
    if text and not str(template).startswith("{"):
        text = text[:1].upper() + text[1:]
    return text


def _gap_text(previous, current):
    """The time from one delivery to the next, "+2 years", "+18 months",
    "+30 days": the existing texts of the period (``months_text``)."""
    months = delivery.months_between(previous, current)
    if months <= 0:
        days = int((current - previous) // delivery.DAY)
        return "+" + (_("1 day") if days == 1 else _("{} days").format(days))
    years, rest = divmod(months, 12)
    months_part = _("1 month") if rest == 1 else _("{} months").format(rest)
    if years == 0:
        return "+" + months_part
    years_part = _("1 year") if years == 1 else _("{} years").format(years)
    if rest == 0:
        return "+" + years_part
    return "+" + _("{years} and {months}").format(years=years_part, months=months_part)


class _TimelineDot(QWidget):
    """One dot of the line above the delivery columns (owner: as the Future
    view and the chain, time from left to right). The line joins the dots:
    it starts at the first one and ends at the last one."""

    def __init__(self, first, last, parent=None):
        """``first`` / ``last``: no line on that side of the dot."""
        super().__init__(parent)
        self.first, self.last = first, last
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):  # noqa: N802 - Qt's name
        """Draw the line and the dot (the green of a good total)."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() // 2
        middle = self.width() // 2
        pen = QPen(self.palette().mid().color())
        pen.setWidth(2)
        painter.setPen(pen)
        if not self.first:
            painter.drawLine(0, y, middle, y)
        if not self.last:
            painter.drawLine(middle, y, self.width(), y)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ColorScheme.GREEN.as_color())
        painter.drawEllipse(middle - 6, y - 6, 12, 12)
        painter.end()


class DeliveriesEditor(BalDialog):
    """The window "Inheritance in several deliveries" (#170).

    ``bal_window``: the wallet's ``BalWindow`` (the engine host). The
    table shown at the start: the stored one, else the example
    (:func:`bal.core.delivery.default_instalments`: 33 / 33 / 34 % from the
    wallet's date, one year apart). The columns follow the heirs of now
    (the paid ones, not the message): an heir added since the table was
    saved gets 0 %, one removed is left out, and a note says to check the
    shares (owner decision 3 of M3: the Check stops until the table fits).
    ``result_kind`` after the window closes: "saved", "removed" or None.
    """

    def __init__(self, bal_window, parent=None):
        """Build the window on the stored table (or the example)."""
        super().__init__(parent or bal_window.window, bal_window.bal_plugin,
                         _("Inheritance in several deliveries"))
        self.bal_window = bal_window
        self.result_kind = None
        self.names = delivery.payee_names(bal_window.heirs)
        settings = bal_window.will_settings
        try:
            stored = delivery.table_rows(settings)
        except ValueError:
            stored = []
        self.had_table = instalments.has_table(bal_window)
        self.heirs_changed = False
        self.from_in_force = False
        in_force_rows, in_force_complete = ([], True) if stored else in_force_table(bal_window)
        if stored:
            rows = stored
            kept = set()
            for row in stored:
                shares = row.get("shares") if isinstance(row, dict) else None
                kept.update(shares or {})
            self.heirs_changed = kept != set(self.names)
            self.from_example = False
        elif in_force_rows:
            # #188: no table, but a will in several deliveries in force
            # (its removal not invalidated yet): its dates and shares, so
            # "I changed my mind" is one Save, nothing to sign.
            rows = in_force_rows
            self.from_example = False
            self.from_in_force = True
            # An heir added or removed since (Opus' review): the note says
            # to check the shares, not "nothing to sign".
            kept = set()
            for row in rows:
                kept.update(row.get("shares") or {})
            self.heirs_changed = (not in_force_complete) or kept != set(self.names)
        else:
            rows = delivery.default_instalments(self.names, self._first_date())
            self.from_example = True
        self.dates = []
        self.cells = []        # one list per delivery: {name: QLineEdit}
        self._build_window()
        self._set_rows(rows)

    # ------------------------------------------------------------ layout

    def _build_window(self):
        """The fixed parts: title, the three areas, buttons and notes."""
        vbox = QVBoxLayout(self)
        title = QLabel(_("Inheritance in several deliveries"))
        font = title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 2)
        title.setFont(font)
        vbox.addWidget(title)
        intro = QLabel(_(
            "The same dates for every heir. The shares are of the whole "
            "estate: they add up to 100%."))
        intro.setWordWrap(True)
        intro.setStyleSheet("color:{};".format(_MUTED))
        vbox.addWidget(intro)
        self.top_note = QLabel("")
        self.top_note.setWordWrap(True)
        vbox.addWidget(self.top_note)

        # Three areas side by side (owner): the names, the deliveries (the
        # only part that scrolls, sideways, when there are more columns
        # than fit) and the totals of the heirs. Their rows have the same
        # heights (:meth:`_place`).
        body = QHBoxLayout()
        body.setSpacing(12)
        self.left = QGridLayout()
        self.right = QGridLayout()
        self.middle = QGridLayout()
        for grid in (self.left, self.middle, self.right):
            grid.setVerticalSpacing(0)
            grid.setHorizontalSpacing(0)
            grid.setContentsMargins(0, 0, 0, 0)
        left_box, right_box = QWidget(), QWidget()
        left_box.setLayout(self.left)
        right_box.setLayout(self.right)
        self.middle_box = QWidget()
        self.middle_box.setLayout(self.middle)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.middle_box)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body.addWidget(left_box, 0, Qt.AlignmentFlag.AlignTop)
        body.addWidget(self.scroll, 0, Qt.AlignmentFlag.AlignTop)
        body.addWidget(right_box, 0, Qt.AlignmentFlag.AlignTop)
        body.addStretch(1)
        vbox.addLayout(body)

        tools = QHBoxLayout()
        self.add_button = QPushButton(_("Add a delivery"))
        self.add_button.clicked.connect(self._add_delivery)
        self.remove_last_button = QPushButton(_("Remove the last delivery"))
        self.remove_last_button.clicked.connect(self._remove_last)
        self.example_button = QPushButton(_("Example table"))
        self.example_button.clicked.connect(self._use_example)
        for button in (self.add_button, self.remove_last_button, self.example_button):
            tools.addWidget(button)
        tools.addStretch(1)
        vbox.addLayout(tools)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:{};".format(_MUTED))
        vbox.addWidget(self.info)
        self.later_note = QLabel("")
        self.later_note.setWordWrap(True)
        self.later_note.setStyleSheet(ColorScheme.YELLOW.as_stylesheet())
        vbox.addWidget(self.later_note)
        self.problem = QLabel("")
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(ColorScheme.RED.as_stylesheet())
        vbox.addWidget(self.problem)

        bottom = QHBoxLayout()
        self.one_delivery_button = QPushButton(_("Back to one delivery"))
        self.one_delivery_button.clicked.connect(self._back_to_one)
        self.one_delivery_button.setVisible(self.had_table)
        bottom.addWidget(self.one_delivery_button)
        bottom.addStretch(1)
        self.cancel_button = QPushButton(_("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton(_("Save"))
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.save_button)
        vbox.addLayout(bottom)

    @staticmethod
    def _clear(grid):
        """Remove every widget of ``grid``."""
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _small(self, text, color=_MUTED, align=Qt.AlignmentFlag.AlignCenter):
        """A small grey label (the check sums, the gaps)."""
        label = QLabel(text)
        font = label.font()
        font.setPointSizeF(max(font.pointSizeF() - 1, 7))
        label.setFont(font)
        label.setStyleSheet("color:{};".format(color))
        label.setAlignment(align)
        return label

    def _place(self, grid, widget, row, col, width=None, expand=False):
        """Put ``widget`` in a holder of the row's fixed height (and of the
        column's ``width``): the three areas then line up exactly, whatever
        their widgets. ``expand``: the widget takes the whole width (the
        line of the dots). Returns ``widget``."""
        holder = QWidget()
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        if expand:
            line.addWidget(widget)
        else:
            line.addWidget(widget, 0, Qt.AlignmentFlag.AlignCenter)
        holder.setFixedHeight(self.row_heights[row])
        if width is not None:
            holder.setFixedWidth(width)
        grid.addWidget(holder, row, col)
        return widget

    def _measure(self, bold, big):
        """The fixed sizes: the height of each kind of row and the width of
        a delivery column, from the widgets the window uses (so the fonts
        of the system are respected)."""
        date = QDateEdit()
        date.setDisplayFormat("dd/MM/yyyy")
        cell = QLineEdit()
        cell.setFont(big)
        title = QLabel(_("Delivery {}").format(12))
        title.setFont(bold)
        # The longest gap text ("+12 years and 11 months", translated).
        small = self._small("+" + _("{years} and {months}").format(
            years=_("{} years").format(12), months=_("{} months").format(11)))
        self.cell_width = cell.fontMetrics().horizontalAdvance("100,00") + 24
        percent = QLabel("%").sizeHint().width()
        self.column_width = max(
            date.sizeHint().width(), self.cell_width + percent + 8,
            title.sizeHint().width(), small.sizeHint().width()) + 24
        heir = max(cell.sizeHint().height(), QLabel("X").sizeHint().height()) + 12
        self.kind_heights = {
            "dots": 16,
            "title": title.sizeHint().height() + 6,
            "date": date.sizeHint().height() + 4,
            "gap": small.sizeHint().height() + 2,
            "heir": heir,
            "total": small.sizeHint().height() + 10,
        }
        for widget in (date, cell, title, small):
            widget.deleteLater()

    def _set_rows(self, rows):
        """Show ``rows`` (``[{"locktime", "shares"}]``): rebuild the grids.

        Rows: 0 the dots, 1 the titles, 2 the dates, 3 the gaps, then one
        per heir, then the totals of the deliveries. Every row has a fixed
        height and every delivery column a fixed width (:meth:`_measure`).
        """
        for grid in (self.left, self.middle, self.right):
            self._clear(grid)
        self.dates = [delivery.as_delivery(row.get("locktime")) for row in rows]
        self.cells = []
        self.date_editors = []
        self.gap_labels = []
        self.delivery_totals = []
        self.heir_totals = {}
        count = len(rows)
        first_heir = 4
        totals_row = first_heir + len(self.names)
        bold = QFont(self.font())
        bold.setBold(True)
        big = QFont(bold)
        big.setPointSizeF(bold.pointSizeF() + 1)
        if not hasattr(self, "column_width"):
            self._measure(bold, big)
        kinds = ["dots", "title", "date", "gap"] + ["heir"] * len(self.names) + ["total"]
        self.row_heights = [self.kind_heights[k] for k in kinds]
        width = self.column_width
        for col, row in enumerate(rows):
            n = col + 1
            self._place(self.middle, _TimelineDot(col == 0, col == count - 1),
                        0, col, width, expand=True)
            title = QLabel(_("Delivery {}").format(n))
            title.setFont(bold)
            self._place(self.middle, title, 1, col, width)
            editor = QDateEdit()
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd/MM/yyyy")
            if self.dates[col] is not None:
                editor.setDate(_qdate_of(self.dates[col]))
            editor.dateChanged.connect(
                lambda qdate, i=col: self._on_date(i, qdate))
            self._place(self.middle, editor, 2, col, width)
            self.date_editors.append(editor)
            self.gap_labels.append(self._place(self.middle, self._small(""), 3, col, width))
            cells = {}
            shares = row.get("shares") if isinstance(row.get("shares"), dict) else {}
            for r, name in enumerate(self.names):
                cell = QLineEdit()
                cell.setFont(big)
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setMaxLength(6)
                cell.setFixedWidth(self.cell_width)
                value = delivery.as_share(shares.get(name, 0))
                cell.setText(_number_text(value) if value is not None
                             else str(shares.get(name)))
                cell.textChanged.connect(self._refresh)
                box = QWidget()
                line = QHBoxLayout(box)
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(4)
                line.addWidget(cell)
                line.addWidget(QLabel("%"))
                self._place(self.middle, box, first_heir + r, col, width)
                cells[name] = cell
            self.cells.append(cells)
            self.delivery_totals.append(
                self._place(self.middle, self._small(""), totals_row, col, width))
        for r in range(first_heir):
            self._place(self.left, QLabel(""), r, 0)
        for r, name in enumerate(self.names):
            label = QLabel(name)
            label.setFont(bold)
            self._place(self.left, label, first_heir + r, 0)
            label.parentWidget().layout().setAlignment(
                label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.heir_totals[name] = self._place(
                self.right, self._small(""), first_heir + r, 0)
        total_label = self._small(_("total"), align=Qt.AlignmentFlag.AlignLeft)
        self._place(self.left, total_label, totals_row, 0)
        total_label.parentWidget().layout().setAlignment(
            total_label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for r in (0, 1, 2):
            self._place(self.right, QLabel(""), r, 0)
        self._place(self.right, self._small(_("total")), 3, 0)
        self.grand_total = self._place(self.right, self._small(""), totals_row, 0)
        self._fit_columns(count)
        self._refresh()

    def _fit_columns(self, count):
        """Show every column up to :data:`_VISIBLE_COLUMNS` (owner: with 3
        deliveries everything fits, no bar); more of them scroll sideways in
        the middle area only, the names and the totals stay in place."""
        height = sum(self.row_heights)
        self.middle_box.setFixedSize(self.column_width * count, height)
        shown = min(count, _VISIBLE_COLUMNS)
        bar = self.scroll.horizontalScrollBar().sizeHint().height()
        self.scroll.setFixedWidth(self.column_width * shown)
        self.scroll.setFixedHeight(height + (bar + 4 if count > _VISIBLE_COLUMNS else 0))

    # ------------------------------------------------------------ data

    def rows(self):
        """The table as the window shows it, in the form of the rules:
        ``[{"locktime": ts, "shares": {name: text}}]``."""
        out = []
        for col, cells in enumerate(self.cells):
            out.append({
                "locktime": self.dates[col],
                "shares": {name: _read_cell(cells[name].text()) for name in self.names},
            })
        return out

    def check(self):
        """The table checked by the rules of the command line: ``(rows, None)``
        or ``(None, the problem in words)``."""
        try:
            return delivery.validate_instalments(self.rows(), self.names, self._now()), None
        except ValueError as e:
            return None, problem_text(e)

    @staticmethod
    def _now():
        """The clock of the rules (the wizard's ``time_now``, which the tests
        replace)."""
        from .widgets import time_now

        return time_now()

    def _on_date(self, col, qdate):
        """A date changed: 00:00 UTC of that day (a date not touched keeps
        its exact time: delivery 1 is the wallet's date)."""
        self.dates[col] = _ts_of(qdate)
        self._refresh()

    def _refresh(self):
        """The totals, the gaps, the notes, the problem and Save."""
        names = self.names
        heir_totals = dict.fromkeys(names, Decimal(0))
        grand = Decimal(0)
        for col, cells in enumerate(self.cells):
            paid = Decimal(0)
            for name in names:
                value = delivery.as_share(_read_cell(cells[name].text()))
                if value is not None:
                    paid += value
                    heir_totals[name] += value
            grand += paid
            self.delivery_totals[col].setText(_pct(paid))
            previous = self.dates[col - 1] if col else None
            current = self.dates[col]
            self.gap_labels[col].setText(
                _gap_text(previous, current)
                if previous is not None and current is not None
                and current > previous else "")
        for name in names:
            self.heir_totals[name].setText(_pct(heir_totals[name]))
        total = self.grand_total
        total.setText(_pct(grand) + (" ✓" if grand == 100 else ""))
        font = total.font()
        font.setBold(grand == 100)
        total.setFont(font)
        total.setStyleSheet("color:{};".format(
            ColorScheme.GREEN.as_color().name() if grand == 100 else _MUTED))

        count = len(self.cells)
        self.add_button.setEnabled(count < delivery.INSTALMENTS_MAX)
        self.remove_last_button.setEnabled(count > delivery.INSTALMENTS_MIN)
        rows, problem = self.check()
        self.problem.setText(problem or "")
        self.problem.setVisible(bool(problem))
        self.save_button.setEnabled(problem is None)
        self.top_note.setText(self._top_text())
        self.top_note.setVisible(bool(self.top_note.text()))
        self.top_note.setStyleSheet(
            ColorScheme.YELLOW.as_stylesheet() if self.heirs_changed
            else "color:{};".format(_MUTED))
        self.info.setText(self._info_text(count))
        later = self._later_text(rows) if rows is not None else ""
        self.later_note.setText(later)
        self.later_note.setVisible(bool(later))

    def _top_text(self):
        """The note above the table: the heirs changed, the will in force
        (#188), or the example with the warning of #188."""
        if self.heirs_changed:
            return _("The heirs changed since the table was saved: check the "
                     "shares of every heir.")
        if self.from_in_force:
            return _("The dates and shares of your will in several deliveries, "
                     "still in force: save them to keep it (nothing to sign).")
        if self.from_example:
            return (_("An example to change: 33%, 33% and 34% of the estate, "
                      "one year apart.") + " " + back_to_one_warning())
        return ""

    def _branches(self):
        """``(will-executors, backup)``: the will-executors the build uses
        and whether the copy without a will-executor is made. The same list
        and filter as ``Heirs.buildTransactions`` (selected, and valid for
        the fee limit and the dust), which makes a copy of every delivery
        for each."""
        bw = self.bal_window
        plugin = bw.bal_plugin
        try:
            listed = Willexecutors.get_willexecutors(plugin) or {}
            max_fee = plugin.MAX_WILLEXECUTOR_FEE.get()
            dust = bw.wallet.dust_threshold()
            executors = sum(
                1 for we in listed.values()
                if isinstance(we, dict) and Willexecutors.is_selected(we)
                and Willexecutors.is_valid(we, max_fee=max_fee, dust=dust))
        except Exception as e:
            _logger.debug(f"will-executors not counted: {e!r}")
            executors = 0
        try:
            backup = bool(plugin.NO_WILLEXECUTOR.get())
        except Exception:
            backup = False
        return executors, backup

    def _info_text(self, count):
        """The grey lines: how many transactions, the fees, the message."""
        lines = []
        executors, backup = self._branches()
        levels = delivery.transactions_count(executors + int(backup), count)
        if executors and levels:
            parts = " + ".join(str(x) for x in levels)
            if backup:
                lines.append(_(
                    "With {} will-executors and the copy without a "
                    "will-executor: {} transactions to sign ({})."
                ).format(executors, sum(levels), parts))
            else:
                lines.append(_("With {} will-executors: {} transactions to sign ({}).")
                             .format(executors, sum(levels), parts))
        lines.append(_("Each will-executor takes its fee at every delivery."))
        if any(delivery.is_message_row(v) for v in self.bal_window.heirs.values()):
            lines.append(_("The message in the will is in delivery 1."))
        return "\n".join(lines)

    def _later_text(self, rows):
        """The deliveries later than the will in force, in words (D6)."""
        in_force, _reference, _old = instalments.dates_in_force(
            self.bal_window, self._now())
        return "\n".join(
            _("Delivery {} is later than the will in force ({}): at the next "
              "Check the old will must be invalidated first (on-chain, miner "
              "fee).").format(n, _day_text(date))
            for n, date in delivery.later_than_in_force(rows, in_force))

    # ------------------------------------------------------------ buttons

    def _add_delivery(self):
        """One more delivery, 12 months after the last, 0 % to everyone."""
        rows = self.rows()
        if len(rows) >= delivery.INSTALMENTS_MAX:
            return
        last = rows[-1]["locktime"] if rows else None
        locktime = delivery.add_months(last, delivery.DEFAULT_INSTALMENT_STEP_MONTHS) \
            if last is not None else None
        rows.append({"locktime": locktime, "shares": dict.fromkeys(self.names, "0")})
        self._set_rows(rows)

    def _remove_last(self):
        """The last delivery out (its shares go nowhere: the total says
        what is missing)."""
        rows = self.rows()
        if len(rows) > delivery.INSTALMENTS_MIN:
            self._set_rows(rows[:-1])

    def _first_date(self):
        """The first date of the example: the wallet's delivery date, else
        one year from now (a wallet always has one; never a crash)."""
        first = delivery.as_delivery(self.bal_window.will_settings.get("locktime"))
        if first is None:
            first = delivery.add_months(int(self._now()), 12)
        return first

    def _use_example(self):
        """The example table again (33 / 33 / 34 %, one year apart)."""
        self.from_example = True
        self.heirs_changed = False
        self._set_rows(delivery.default_instalments(self.names, self._first_date()))

    def earlier_text(self, rows):
        """What would arrive earlier with ``rows``, in words, or None (owner
        decision (a), the rules of :mod:`bal.core.delivery`)."""
        _in_force, reference, old = instalments.dates_in_force(
            self.bal_window, self._now())
        hit = delivery.first_earlier_date(rows, reference)
        if hit is not None:
            n, date, ref, _from_will = hit
            return _("Delivery {}: {} instead of {}.").format(
                n, _day_text(date), _day_text(ref))
        earlier = delivery.first_earlier(
            delivery.schedule_of(rows), delivery.schedule_now(reference, old))
        if earlier is not None:
            _n, date, more, before = earlier
            return _("By {} the heirs would receive {} of the estate instead of {}.").format(
                _day_text(date), _pct(more), _pct(before))
        return None

    def _save(self):
        """Save the table: the rules, the "earlier" question, then
        ``context.set_instalments`` (delivery 1 becomes the wallet's date)."""
        rows, problem = self.check()
        if rows is None:
            self.show_error(problem)
            return
        earlier = self.earlier_text(rows)
        if earlier is not None and not ask(
                self,
                _("With this table the heirs would receive part of the estate "
                  "earlier:") + "\n\n" + earlier + "\n\n" + _earlier_cost_text()
                + "\n\n" + _("Save the table?")):
            return
        if not self.had_table and not self.from_in_force and not ask(
                self, back_to_one_warning() + "\n\n" + _(
                    "Changing the dates or the shares later stays free: it is "
                    "going back to one delivery that costs.") + "\n\n"
                + _("Save the table?")):
            # #188 (owner): said once, at the first table of the wallet.
            return
        try:
            context.set_instalments(self.bal_window, rows, now=self._now())
        except delivery.DeliverySettingsUnavailable:
            from .flow_adapter import SETTINGS_UNAVAILABLE

            self.show_error(_(SETTINGS_UNAVAILABLE))
            return
        except ValueError as e:
            self.show_error(problem_text(e))
            return
        self.result_kind = "saved"
        _after_change(self.bal_window)
        self.accept()

    def _back_to_one(self):
        """"Back to one delivery": :func:`confirm_back_to_one`, then close."""
        if confirm_back_to_one(self.bal_window, self):
            self.result_kind = "removed"
            self.accept()


def ask(parent, text):
    """A Yes / No question of the deliveries; True for Yes (No is the
    default button: nothing changes by mistake). A function, so the tests
    can answer it."""
    yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
    if not isinstance(parent, QWidget):
        parent = None
    box = QMessageBox(QMessageBox.Icon.Question,
                      _("Inheritance in several deliveries"), text, yes | no, parent)
    box.setDefaultButton(no)
    return box.exec() == yes


def _earlier_cost_text():
    """Why an earlier date is asked (owner decision (a)), in words."""
    return _("It is free: a new will is prepared with the earlier dates. Once "
             "it is sent to the will-executors, only an invalidation "
             "transaction (miner fee) can undo it.")


def _after_change(bal_window):
    """The toolbars, the wizard's fields and the lists show the new state."""
    for name in ("refresh_setting_fields", "update_all"):
        refresh = getattr(bal_window, name, None)
        if callable(refresh):
            try:
                refresh()
            except Exception as e:
                _logger.debug(f"{name} after the table changed: {e!r}")


def confirm_back_to_one(bal_window, parent):
    """Remove the table after the owner's yes (#170, owner decision (a)).

    With one delivery only the rest of the estate arrives at delivery 1, earlier:
    the question says by how much, and that the amounts of the heirs count
    again. Returns True when the table was removed.
    """
    settings = bal_window.will_settings
    first = delivery.as_delivery(settings.get("locktime"))
    text = ""
    staged = Will.staged_in_force(bal_window.willitems or {}, bal_window.wallet)
    if staged:
        # #188 (owner decision; owner's ZIP test: said FIRST, it is what
        # matters most, and in place of "it is free"): a change of the type
        # of will, the copies in several deliveries held by the
        # will-executors are invalidated on the blockchain first.
        text = _(
            "Your will in several deliveries is held by the will-executors. "
            "To go back to one delivery it must be invalidated on the "
            "blockchain first: a network fee, and the will's coins are spent "
            "together in one transaction visible to everybody. The next Check "
            "will ask you to sign the invalidation, then prepare the new will."
        ) + "\n\n"
    if first is not None:
        _in_force, reference, old = instalments.dates_in_force(
            bal_window, DeliveriesEditor._now())
        earlier = delivery.first_earlier(
            [(first, Decimal(100))], delivery.schedule_now(reference, old))
        if earlier is not None:
            _n, date, _more, before = earlier
            text += _(
                # Owner's ZIP test (#188): "without the table" was not
                # understood; "with one delivery only" says what it means.
                "With one delivery only, the heirs receive the whole estate "
                "on {}, the date of delivery 1. Today they receive {} by that "
                "date: the rest would arrive earlier than planned."
            ).format(_day_text(date), _pct(before)) + "\n\n"
            if not staged:
                # "It is free...": true only when nothing was sent (drafts).
                text += _earlier_cost_text() + "\n\n"
    amounts = heir_amounts_text(bal_window)
    if amounts:
        # #185 (owner, B grey): the heir window shows these amounts grey and
        # locked while there is a table; the question names them all.
        text += _("Then the amounts of the heirs count again ({}): check them "
                  "before the next Check.").format(amounts)
    else:
        text += _("Then the amounts of the heirs count again: check them before "
                  "the next Check.")
    text += "\n\n" + _("Go back to one delivery?")
    if not ask(parent, text):
        return False
    try:
        removed = context.clear_instalments(bal_window)
    except delivery.DeliverySettingsUnavailable:
        from .flow_adapter import SETTINGS_UNAVAILABLE

        bal_window.show_error(_(SETTINGS_UNAVAILABLE))
        return False
    _after_change(bal_window)
    return bool(removed)


def heir_amounts_text(bal_window):
    """"Erede fortunato 70%, prova erede x 9%": each heir's own amount, the
    one that counts without a table (#185); a fixed amount in the wallet's
    unit; the message rows left out. "" when there is none."""
    parts = []
    for name, value in (getattr(bal_window, "heirs", None) or {}).items():
        if delivery.is_message_row(value):
            continue
        try:
            amount = str(value[1])
        except (IndexError, TypeError, KeyError):
            continue
        if amount.strip().endswith("%"):
            shown = shown_percent(amount)
        else:
            try:
                shown = bal_window.window.format_amount_and_units(int(amount))
            except Exception:
                shown = amount
        parts.append("{} {}".format(name, shown))
    return ", ".join(parts)


def back_to_one_warning():
    """The warning before a will in several deliveries is made (#188,
    owner decision of 2026-10-24): going back is not free."""
    return _(
        "Think it over: going back from several deliveries to one delivery "
        "will need an invalidation on the blockchain, to keep the will safe "
        "(a network fee; the will's coins are spent together in one "
        "transaction visible to everybody).")


def in_force_table(bal_window):
    """``(rows, complete)``: the table of the will in several deliveries in
    force (#188), its dates and the share of each heir at each delivery,
    read along the chain of copies (``will_changes.side_from_items``);
    ``([], True)`` without one. ``complete`` is False when an heir of the
    will is no longer in the Heirs tab (its share is left out: the window
    says the shares are to be checked)."""
    try:
        in_force = context.in_force_items(bal_window)
        if not Will.staged_in_force(bal_window.willitems or {}, bal_window.wallet):
            return [], True
        side = will_changes.side_from_items(in_force, bal_window.selected_willexecutors())
    except Exception as e:
        _logger.debug(f"table of the will in force not read: {e!r}")
        return [], True
    if side is None or not side.deliveries:
        return [], True
    complete = True
    by_address = {}
    for name, value in (bal_window.heirs or {}).items():
        if not delivery.is_message_row(value):
            by_address[str(value[0])] = name
    rows = []
    for date, shares in side.deliveries:
        if date is None or shares is None:
            return [], True
        cells = {}
        for address, pct in shares.items():
            name = by_address.get(address)
            share = delivery.as_share(round(pct, 2))
            if name is None:
                complete = False
            elif share is not None and share:
                cells[name] = delivery.share_text(share)
        rows.append({"locktime": int(date), "shares": cells})
    return rows, complete


def staged_field_text(settings):
    """The text the date fields show with a table (#170, owner answer 3):
    "10/03/2030 (+2 deliveries)", delivery 1 and how many follow; the dates
    are changed only in the window of the deliveries. None without a
    table."""
    try:
        rows = delivery.table_rows(settings)
    except ValueError:
        rows = None
    if rows == []:
        return None
    locktime = delivery.as_delivery((settings or {}).get("locktime"))
    day = _day_text(locktime) if locktime is not None else "?"
    if rows is None:
        return _("{date} (several deliveries)").format(date=day)
    more = len(rows) - 1
    if more == 1:
        return _("{date} (+1 delivery)").format(date=day)
    return _("{date} (+{n} deliveries)").format(date=day, n=more)



def person_shares(settings, name):
    """``(text, tooltip)`` of heir ``name`` in the Heirs tab's Amount column
    with a table (#185, owner's preview): ``"10% + 10% + 30%"``, the
    person's cell at each delivery ("0%" where it is empty, or where the
    person is not in the table yet), and one line per delivery, "Delivery 1
    (10/03/2030): 10%". The decimal separator of the language ("16,5%").
    None without a table, or when it cannot be read (the column then shows
    the amount as before). Also the heir window's "Shares" (#185, B grey).
    """
    try:
        rows = delivery.table_rows(settings)
    except ValueError:
        return None
    if len(rows) < 2:
        return None
    cells, lines = [], []
    for n, row in enumerate(rows, 1):
        row = row if isinstance(row, dict) else {}
        raw = (row.get("shares") or {}).get(name, 0)
        share = delivery.as_share(raw)
        # The language's decimal separator ("16,5%"), as the box (#185).
        cell = _pct(share) if share is not None else str(raw)
        cells.append(cell)
        locktime = delivery.as_delivery(row.get("locktime"))
        day = _day_text(locktime) if locktime is not None else "?"
        lines.append(_("Delivery {n} ({date}): {share}").format(
            n=n, date=day, share=cell))
    return " + ".join(cells), "\n".join(lines)


def staged_field_tip():
    """Where the dates of the deliveries are changed (the date fields'
    tooltip with a table)."""
    return _("The dates of the deliveries are changed in the window "
             "\"Inheritance in several deliveries\": Tools in the Will tab, "
             "or Build / Edit Your Will.")
