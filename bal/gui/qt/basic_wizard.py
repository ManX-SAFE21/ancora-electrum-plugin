"""
bal.gui.qt.basic_wizard
=======================

The BASIC mode wizard "Build / Edit Your Will" (CHANGELOG #134).

Four pages, each opening with a question and a step bar on top:

1. **When** - when the heirs receive the bitcoin: quick choices ("In 1
   year", "In 2 years", "In 5 years"), "Leave as it is" for a wallet that
   already has a will, or "Choose the date…" with four boxes in the
   computer's local time; the will stores the UTC moment and the page shows
   both. A wallet in the renewable period form (#127) only sees its renewal.
2. **Heirs** - the real heirs list with Add / Import / Export.
3. **Will-executors** - the list is downloaded and pinged at once and only
   the will-executors that answer are selected (the rule of the old
   "Automatically download and select" choice, shared through
   ``BalWindow.download_and_select_willexecutors``); the full
   ``WillExecutorWidget`` is shown below.
4. **Summary** - what will be created; "Create the will" runs the same
   build / sign / send / verify window as the old wizard
   (``BalWizardDialog.on_next_we``).

ADVANCED keeps the old pages (``bal.gui.qt.dialogs``); the "Mode: Basic ·
Advanced" link of page 1 switches the plugin mode through the same "at My
Risk" gate as the settings page (``common.switch_user_type``) and the
wizard then continues with the old pages.

**Cancel is linear (owner decision).** Today the wizard saves every change
at once (the heirs at each change, the date at each edit, the will-executor
selection at each click) and Cancel undoes nothing. The BASIC wizard takes
a :class:`WizardSnapshot` when it opens; Cancel and the window's close
button put the heirs, the delivery date (and the keys its write clears)
and the will-executors' ``selected`` flags back exactly, through the same
validated writers. "Create the will" keeps the changes.

Why the date is written when page 1 is left, not only at the end: a heir
added on page 2 gets the will's delivery date from the settings
(``BalWindow.set_heir``) and pages 2-4 show it; writing it at once and
restoring it on Cancel is the same rule as the heirs and the selection, and
the restore uses the same validated write (``update_setting_widgets``).

This module is imported lazily by ``dialogs`` (it imports ``dialogs`` and
``lists``, which import ``dialogs`` at load time).
"""

import html
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QValidator

from ... import i18n
from ...core import (
    datetext,
    delivery,
    heir_notes,
    will_changes,
    will_state,
    wizard_date,
)
from ...core.future import delivery_form_text, time_left_text
from ...core.heirs import (
    is_op_return_address,
    message_names,
    message_text,
    percent_total,
)
from ...core.summary import selected_urls
from ...core.util import Util, copy_structure
from ...engine import context as engine_context
from .common import (
    N_,
    HelpButton,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    Willexecutors,
    _,
    _logger,
    switch_user_type,
)
from .dialogs import BalWizardWidget
from .lists import (
    FRAMED_BUTTON_STYLE,
    HeirListWidget,
    WillExecutorWidget,
    extra_messages_text,
    same_address_text,
)
from .widgets import LockTimeWidget, what_changes_box

if TYPE_CHECKING:
    from .window import BalWindow

# ------------------------------------------------------------------ look

#: The four steps of the step bar (the number is joined outside ``_()``).
STEPS = (N_("When"), N_("Heirs"), N_("Will-executors"), N_("Summary"))

#: The quick choice buttons: white, light blue when chosen (the look of the
#: #133 main button, so the chosen one reads as "on").
CHIP_STYLE = (
    "QPushButton{font-weight:bold; color:#1F2328; background-color:#FFFFFF;"
    " border:1px solid #9EC3EE; border-radius:6px; padding:8px 16px;}"
    "QPushButton:hover{background-color:#F3F8FE;}"
    "QPushButton:checked{background-color:#EAF3FD; border:2px solid #2F6FD0;"
    " color:#174A8C;}"
)
#: A yellow note: something to know before going on.
NOTE_STYLE = ("QLabel{background:#FFF7E6; border:1px solid #F0D9A8;"
              " border-radius:6px; padding:8px; color:#6B4A00;}")
#: A light blue information box.
INFO_STYLE = ("QLabel{background:#EEF4FB; border:1px solid #C9DBF0;"
              " border-radius:6px; padding:8px; color:#24374D;}")
#: The light green box of the summary.
SUMMARY_STYLE = ("QFrame#summaryBox{background:#F7FBF8; border:1px solid #CFE7D6;"
                 " border-radius:6px;}"
                 "QFrame#summaryBox QLabel{background:transparent; color:#1F2328;}")
MUTED_STYLE = "QLabel{color:#6A737D;}"
ERROR_STYLE = "QLabel{color:#C62828;}"
OK_STYLE = "QLabel{color:#1A8F3C; font-weight:bold;}"
SHIFT_STYLE = "QLabel{color:#8A5A00;}"

# ------------------------------------------------------------------ texts

#: The "?" next to the date boxes (#134). The old text (``LockTimeWidget``,
#: still used by ADVANCED) mentions the Raw editor and says the time is
#: local; this page saves UTC and has no Raw editor.
NEW_DELIVERY_HELP = N_(
    "<b>DELIVERY DATE</b><br><br>"
    "The day and hour from which the will-executors send the will to the "
    "network, if you have not renewed it before.<br>"
    "Whenever needed, the transaction is anticipated by 1 day.<br><br>"
    "<b>Hour:</b> choose your computer's time; the will saves it in "
    "<b>UTC</b> (Greenwich time). Under the boxes you see both.<br><br>"
    "<b>Note:</b> the Bitcoin network accepts the transaction about an hour "
    "after the chosen time, sometimes more."
)

#: The box "Why two times?" of page 1.
WHY_TWO_TIMES = N_(
    "<b>Why two times?</b> Bitcoin counts time in <b>UTC</b>, Greenwich "
    "time, the same all over the world. Your computer's time zone can be "
    "ahead of it or behind it (Italy is 1 hour ahead in winter and 2 in "
    "summer). The will is saved in UTC: that is why the summary and the "
    "report show the UTC time.<br>"
    "<b>If you are testing:</b> the Bitcoin network accepts the transaction "
    "only when the block clock has passed that time, usually about an hour "
    "later, sometimes more. Wait a few hours before thinking it did not work."
)

#: The keys of the wallet's delivery settings the BASIC wizard can change:
#: the date, and what its validated write clears (the "which date?"
#: question with its note, the later wish of the migration;
#: ``engine.context.set_will_setting``).
SNAPSHOT_SETTING_KEYS = (
    "locktime", delivery.CHOICE_KEY, *delivery.NOTE_KEYS, delivery.LATER_WISH_KEY,
)

_MISSING = object()


def time_now():
    """The current time (the page's "now"); a function so the tests can
    replace the clock."""
    return time.time()




def _label(text="", style=None, wrap=False, rich=True):
    """A QLabel with ``style``; rich text unless ``rich`` is False."""
    lab = QLabel(text)
    if rich:
        lab.setTextFormat(Qt.TextFormat.RichText)
    if style:
        lab.setStyleSheet(style)
    lab.setWordWrap(wrap)
    return lab


def _scaled(widget, factor, bold=None):
    """Scale the font of ``widget`` by ``factor`` (and set it bold)."""
    font = widget.font()
    font.setPointSizeF(font.pointSizeF() * factor)
    if bold is not None:
        font.setBold(bold)
    widget.setFont(font)
    return widget


def amount_text(bal_window, sats):
    """``sats`` in the wallet's unit (``"1,000 sat"``), as Electrum shows it.

    The same rule as the Will tab (``PreviewList``): Electrum's
    ``format_amount_and_units``, else ``"<n> sat"``.
    """
    try:
        return bal_window.window.format_amount_and_units(sats)
    except Exception:
        return "{} sat".format(sats)


def _count_text(count, one, many):
    """``one`` for 1, ``many.format(count)`` otherwise (both translated)."""
    return _(one) if count == 1 else _(many).format(count)


def answered_text(count):
    """``"3 will-executors answer"`` / ``"1 will-executor answers"``."""
    return _count_text(count, N_("1 will-executor answers"),
                       N_("{} will-executors answer"))


def selected_text(count):
    """``"3 will-executors selected"`` / ``"1 will-executor selected"``."""
    return _count_text(count, N_("1 will-executor selected"),
                       N_("{} will-executors selected"))


def _selected(willexecutors):
    """The selected will-executors of ``willexecutors`` (``{url: we}``)."""
    return {url: we for url, we in (willexecutors or {}).items()
            if isinstance(we, dict) and we.get("selected")}


def _answered(willexecutors):
    """How many will-executors answered the last ping (status 200).

    Compared as text, like the list view and the selection rule
    (``BalWindow.download_and_select_willexecutors``).
    """
    return sum(1 for we in (willexecutors or {}).values()
               if isinstance(we, dict) and str(we.get("status", "")) == "200")


def executor_fee_text(bal_window, willexecutors):
    """The will-executors' fee row of the summary, or None (none selected).

    Each selected will-executor holds its own copy of the will, with its
    own fee output (its ``base_fee``, ``Heirs.prepare_lists``); the copies
    spend the same coins, so only ONE of them can be confirmed and only one
    fee is paid: the fee of the will-executor that sends the will on the
    delivery day (owner, ZIP test of #134: "each" was wrong). The value is
    the fee, or "from <lowest> to <highest>" when they differ, in the
    wallet's unit (with the fiat value when Electrum shows it); under it,
    in grey, that only one is paid.
    """
    fees = []
    for we in _selected(willexecutors).values():
        try:
            fees.append(int(we.get("base_fee", 0)))
        except (TypeError, ValueError):
            continue
    if not fees:
        return None
    low, high = min(fees), max(fees)
    if low == high:
        value = amount_text(bal_window, low)
        note = _("you will pay only one: the one of the will-executor that "
                 "sends the will on the delivery day.")
    else:
        value = _("from {low} to {high}").format(
            low=amount_text(bal_window, low), high=amount_text(bal_window, high))
        note = _("you will pay only one of these: the one of the will-executor "
                 "that sends the will on the delivery day.")
    return "{}<br><span style='color:#6A737D'>{}</span>".format(value, note)


def _is_message(value):
    """True for a stored OP_RETURN message row (#140); False for anything
    else, an unreadable row included."""
    try:
        return is_op_return_address(str(value[0]))
    except (IndexError, KeyError, TypeError):
        return False


def heirs_text(bal_window, heirs):
    """``"alice 60% · bob 40%"``: each heir with its share.

    A percentage as stored, a fixed amount in the wallet's unit, an
    OP_RETURN message row as "(OP_RETURN)" (it receives no amount).
    """
    parts = []
    for name in sorted(heirs):
        heir = heirs[name]
        try:
            address, amount = heir[0], heir[1]
        except (TypeError, IndexError):
            parts.append(str(name))
            continue
        if is_op_return_address(str(address)):
            share = "(OP_RETURN)"
        elif Util.is_perc(str(amount)):
            share = str(amount)
        else:
            try:
                share = amount_text(bal_window, int(float(amount)))
            except (TypeError, ValueError):
                share = str(amount)
        parts.append("{} {}".format(name, share))
    return " · ".join(parts)


def in_force_date(bal_window, now=None):
    """The delivery date of the will REALLY in force, or None.

    ``context.in_force_delivery`` (``bal.core.will_state.is_in_force``:
    signed and/or sent, not invalidated, still executable). A prepared
    draft is not the will in force, even with the stored date (owner's ZIP
    test of #134: ``delivery.earliest_in_force`` reads the VALID items
    first, so a draft dated 13/10/2028 hid the will of 26/02/2031 the
    will-executors held). ``now`` is not needed (kept for the callers).
    """
    return engine_context.in_force_delivery(bal_window)


def has_draft(bal_window):
    """True when a will is prepared in this wallet but not signed nor sent
    (``bal.core.will_state.is_draft``)."""
    try:
        return bool(will_state.draft_ids(getattr(bal_window, "willitems", None) or {}))
    except Exception:
        return False


# -------------------------------------------------------------- snapshot


class WizardSnapshot:
    """What the BASIC wizard may change, taken when it opens (#134).

    * the heirs (a deep copy of ``{name: [address, amount, date]}``);
    * the delivery settings of :data:`SNAPSHOT_SETTING_KEYS`;
    * the ``selected`` flag of every will-executor (by URL);
    * #137: the will-executors the user deselected on purpose
      (``bal_willexecutors_user_deselected``, every network);
    * #138: the owner's private notes on the heirs (``heir_notes``).

    :meth:`restore` puts them back exactly (Cancel, the close button). New
    will-executors downloaded meanwhile stay in the list, not selected (they
    were not selected when the wizard opened).
    """

    def __init__(self, heirs, settings, selected, user_deselected=None, notes=None):
        """Keep the parts (see the class)."""
        self.heirs = heirs
        self.settings = settings
        self.selected = selected
        self.user_deselected = user_deselected
        self.notes = notes

    @classmethod
    def take(cls, bal_window):
        """The snapshot of ``bal_window`` now."""
        heirs = copy_structure(dict(getattr(bal_window, "heirs", None) or {}))
        d = getattr(bal_window, "will_settings", None) or {}
        settings = {k: copy_structure(d[k]) for k in SNAPSHOT_SETTING_KEYS if k in d}
        wes = Willexecutors.get_willexecutors(bal_window.bal_plugin)
        selected = {url: bool(we.get("selected")) for url, we in wes.items()
                    if isinstance(we, dict)}
        # #137: seeded first (the list as it is now), then copied whole.
        Willexecutors.user_deselected(bal_window.bal_plugin)
        setting = getattr(bal_window.bal_plugin, "WILLEXECUTORS_USER_DESELECTED", None)
        user = copy_structure(setting.get()) if setting is not None else None
        wallet = getattr(bal_window, "wallet", None)
        notes = heir_notes.all_notes(wallet) if wallet is not None else None
        return cls(heirs, settings, selected, user, notes)

    def restore(self, bal_window):
        """Put the heirs, the settings and the selection back (see class).

        Each part through its own writer, the heirs first: ``Heirs.save``
        (one write of the whole list), the date through the window's
        validated write (``update_setting_widgets``: ``context.
        set_will_setting``, the toolbars refreshed), the keys that write
        clears through ``delivery.validate_values`` + ``write_values`` (item
        by item, rule I3), the selection with ``Willexecutors.save``. A part
        that is already as it was is not written. A part that fails is
        logged and the others are still restored. Returns the names of the
        parts written.
        """
        written = []
        for name, step in (("heirs", self._restore_heirs),
                           ("settings", self._restore_settings),
                           ("willexecutors", self._restore_selection),
                           ("user deselections", self._restore_user_deselected),
                           ("heir notes", self._restore_notes)):
            try:
                if step(bal_window):
                    written.append(name)
            except Exception as e:
                _logger.error(f"wizard cancel: {name} not restored: {e!r}")
        for widget_name in ("heir_list_widget", "will_list_widget"):
            widget = getattr(bal_window, widget_name, None)
            try:
                if widget is not None:
                    widget.update()
            except Exception as e:
                _logger.debug(f"wizard cancel: {widget_name} not refreshed: {e!r}")
        return written

    def _restore_notes(self, bal_window):
        """The heirs' notes as they were (#138); True when written."""
        wallet = getattr(bal_window, "wallet", None)
        if wallet is None or self.notes is None:
            return False
        return heir_notes.restore(wallet, self.notes)

    def _restore_user_deselected(self, bal_window):
        """The user's deselections as they were (#137); True when written."""
        setting = getattr(bal_window.bal_plugin, "WILLEXECUTORS_USER_DESELECTED", None)
        if setting is None or self.user_deselected is None:
            return False
        if setting.get() == self.user_deselected:
            return False
        setting.set(copy_structure(self.user_deselected))
        return True

    def _restore_heirs(self, bal_window):
        """The heirs as they were; True when something was written."""
        heirs = bal_window.heirs
        if dict(heirs) == self.heirs:
            return False
        # dict methods, not Heirs.__setitem__ (which saves at every heir):
        # the list is rebuilt in memory and saved once.
        for name in list(heirs.keys()):
            dict.__delitem__(heirs, name)
        for name, value in copy_structure(self.heirs).items():
            dict.__setitem__(heirs, name, value)
        heirs.save()
        return True

    def _restore_settings(self, bal_window):
        """The delivery keys as they were; True when something was written."""
        d = bal_window.will_settings
        if d is None:
            return False
        changed = [k for k in SNAPSHOT_SETTING_KEYS
                   if d.get(k, _MISSING) != self.settings.get(k, _MISSING)]
        if not changed:
            return False
        if "locktime" in changed and "locktime" in self.settings:
            # First the date: its write clears the question and the wish,
            # put back just below.
            bal_window.update_setting_widgets(
                self.settings["locktime"], "locktime", update_all=True)
        back = {k: v for k, v in self.settings.items()
                if k != "locktime" and d.get(k, _MISSING) != v}
        if back:
            delivery.write_values(d, delivery.validate_values(back))
        for key in SNAPSHOT_SETTING_KEYS:
            if key not in self.settings and key in d:
                del d[key]
        refresh = getattr(bal_window, "refresh_setting_fields", None)
        if callable(refresh):
            refresh()
        return True

    def _restore_selection(self, bal_window):
        """The ``selected`` flags as they were; True when saved."""
        plugin = bal_window.bal_plugin
        wes = Willexecutors.get_willexecutors(plugin)
        changed = False
        for url, we in wes.items():
            if not isinstance(we, dict):
                continue
            wanted = self.selected.get(url, False)
            if bool(we.get("selected")) != wanted:
                we["selected"] = wanted
                changed = True
        # The window's own copy too (the old wizard pages read it).
        for url, we in (getattr(bal_window, "willexecutors", None) or {}).items():
            if isinstance(we, dict):
                we["selected"] = self.selected.get(url, False)
        if changed:
            Willexecutors.save(plugin, wes)
        return changed


# ------------------------------------------------------------- widgets


class StepBar(QWidget):
    """"1 When  2 Heirs  3 Will-executors  4 Summary": the current step in
    blue and bold, the steps done lighter, the next ones grey."""

    def __init__(self, current, parent=None):
        """Build the bar with step ``current`` (0..3) highlighted."""
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 6)
        self.labels = []
        for i, text in enumerate(STEPS):
            lab = QLabel("{}  {}".format(i + 1, _(text)))
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if i == current:
                css = "color:#2F6FD0; font-weight:bold; border-bottom:3px solid #2F6FD0;"
            elif i < current:
                css = "color:#555555; border-bottom:3px solid #8FB6EA;"
            else:
                css = "color:#8A9099; border-bottom:3px solid #DDE1E6;"
            lab.setStyleSheet("QLabel{padding:4px; %s}" % css)
            h.addWidget(lab, 1)
            self.labels.append(lab)


class _TextSpinBox(QSpinBox):
    """A spin box that shows its value as a text (a month name, an hour).

    The text cannot be typed (the line edit is read-only): the arrows, the
    mouse wheel and the keyboard arrows choose the value.
    """

    def __init__(self, texts, parent=None):
        """``texts(value)`` gives the text shown for a value."""
        super().__init__(parent)
        self._texts = texts
        self.lineEdit().setReadOnly(True)

    def textFromValue(self, value):  # noqa: N802 - Qt name
        """The text of ``value``."""
        return self._texts(value)

    def valueFromText(self, text):  # noqa: N802 - Qt name
        """The value whose text is ``text`` (else the current value)."""
        for value in range(self.minimum(), self.maximum() + 1):
            if self._texts(value) == text:
                return value
        return self.value()

    def validate(self, text, pos):
        """Only the texts of the range are acceptable."""
        for value in range(self.minimum(), self.maximum() + 1):
            if self._texts(value) == text:
                return QValidator.State.Acceptable, text, pos
        return QValidator.State.Invalid, text, pos


def _spin(low, high, value, width, texts=None):
    """A box with up/down arrows on its right, the value centred, the font a
    little larger (owner's design, p6a)."""
    box = _TextSpinBox(texts) if texts else QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    box.setKeyboardTracking(False)
    box.setFixedWidth(width)
    box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    _scaled(box, 1.15)
    return box


# ---------------------------------------------------------------- pages


class _StepPage(BalWizardWidget):
    """A page of the BASIC wizard: the step bar, the question, the content.

    Built on ``BalWizardWidget`` (the same Previous / Next / Cancel buttons
    and layout as the old pages); its title and message lines are hidden
    (the question replaces them) and Next has the light blue framed look of
    the #133 main button.
    """

    title = None
    message = ""
    step = 0
    question = ""

    def __init__(self, flow, on_next, on_previous):
        """Build the page for ``flow`` (:class:`BasicWizardFlow`)."""
        self.flow = flow
        super().__init__(flow.bal_window, flow.dialog, on_next, on_previous,
                         flow.dialog.on_cancel_heir)
        self.titleLabel.hide()
        self.messageLabel.hide()
        self.next_button.setStyleSheet(FRAMED_BUTTON_STYLE)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(StepBar(self.step))
        body.addSpacing(6)
        q = _scaled(QLabel(_(self.question)), 1.45, bold=True)
        q.setWordWrap(True)
        body.addWidget(q)
        self.build(body)
        self.refresh()

    def get_content(self):
        """An empty body, filled after ``BalWizardWidget.__init__`` (the
        page's own attributes are not set yet when it asks for it)."""
        self.body = QWidget()
        return self.body

    def build(self, layout):
        """Add the page's widgets to ``layout`` (subclasses)."""

    def refresh(self):
        """Update the texts and the Next button (subclasses)."""

    def reminder(self):
        """``"Delivery to the heirs: <b>10 October 2028, 00:00 UTC</b>"`` (pages
        2-3): the date chosen on page 1, in the one long date style (#134)."""
        locktime = delivery.as_delivery(
            (self.bal_window.will_settings or {}).get("locktime"))
        text = datetext.utc_text(locktime) if locktime is not None else "-"
        return _label(_("Delivery to the heirs: <b>{}</b>").format(text),
                      "QLabel{color:#4A5560;}")


class WhenPage(_StepPage):
    """Page 1: "When do the heirs receive the bitcoin?" (see module)."""

    step = 0
    question = N_("When do the heirs receive the bitcoin?")

    #: The quick choices, in order: (key, msgid). "keep" is added in front
    #: when the wallet has a will (``wizard_date.keep_offered``).
    CHOICES = (
        (12, N_("In 1 year")),
        (24, N_("In 2 years")),
        (60, N_("In 5 years")),
        ("choose", N_("Choose the date…")),
    )

    def __init__(self, flow):
        """Page 1; Next stores the date (:meth:`save`) and goes to Heirs."""
        super().__init__(flow, self._next, None)
        self._add_mode_link()

    # ------------------------------------------------------------ build

    def build(self, layout):
        """The chips, the boxes, the dates, the notes and the info box."""
        bw = self.bal_window
        self.now = time_now()
        self.zone = wizard_date.local_zone()
        settings = bw.will_settings or {}
        self.stored = delivery.as_delivery(settings.get("locktime"))
        self.period = delivery.is_period(settings)
        self.in_force = in_force_date(bw, self.now)
        self.choice = None
        layout.addSpacing(8)
        if self.period:
            self._build_period(layout, settings)
            return
        self.keep = wizard_date.keep_offered(
            self.stored, bool(getattr(bw, "willitems", None)), self.now)
        self._build_chips(layout)
        # Owner (ZIP test): a fixed reference under the choices while a will
        # is in force: its date next to the chosen one.
        self.compare_label = _label("", "QLabel{color:#4A5560;}", wrap=True)
        layout.addWidget(self.compare_label)
        layout.addSpacing(10)
        self._build_boxes(layout)
        self._build_dates(layout)
        self.error_label = _label("", ERROR_STYLE, wrap=True)
        layout.addWidget(self.error_label)
        self.later_note = _label(
            # #135 follow-up (owner): never "paid" (see datetext).
            _("<b>This date is later than the will in force.</b><br>Moving it "
              "later needs an invalidation transaction on the blockchain: your "
              "bitcoin stay yours, you only pay the normal network fee. "
              "Moving it earlier is free."), NOTE_STYLE, wrap=True)
        layout.addWidget(self.later_note)
        layout.addSpacing(6)
        layout.addWidget(_label(_(WHY_TWO_TIMES), INFO_STYLE, wrap=True))
        self._select("keep" if self.keep else wizard_date.DEFAULT_QUICK_MONTHS)

    def _build_period(self, layout, settings):
        """A period-form wallet (#127): only its renewal, no choice."""
        months = delivery.period_months(settings)
        layout.addWidget(_label(
            delivery_form_text(delivery.FORM_PERIOD, months), wrap=True))
        if self.stored is not None:
            layout.addWidget(_label(_("Next delivery: {}").format(
                "<b>{}</b>".format(datetext.utc_text(self.stored)))))
            self._add_big_date(layout)
        self.error_label = None
        self.later_note = None

    def _build_chips(self, layout):
        """The quick choice buttons (one checked at a time)."""
        row = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.chips = {}
        choices = list(self.CHOICES)
        if self.keep:
            choices.insert(0, ("keep", N_("Leave as it is")))
        for key, text in choices:
            button = QPushButton(_(text))
            button.setCheckable(True)
            button.setStyleSheet(CHIP_STYLE)
            button.clicked.connect(lambda _checked=False, k=key: self._select(k))
            self.group.addButton(button)
            self.chips[key] = button
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

    def _build_boxes(self, layout):
        """Year | Month | Day | Hour in the computer's time (owner, p6a)."""
        self.boxes = QWidget()
        g = QGridLayout(self.boxes)
        g.setContentsMargins(0, 4, 0, 8)
        g.setHorizontalSpacing(22)
        g.setVerticalSpacing(6)
        year = wizard_date.local_parts(self.now, self.zone)[0]
        self.year_box = _spin(year, wizard_date.MAX_YEAR, year, 80)
        self.month_box = _spin(
            1, 12, 1, 130,
            datetext.month_name)
        self.day_box = _spin(1, 31, 1, 64)
        self.hour_box = _spin(0, 23, 0, 84, lambda h: "{:02d}:00".format(h))
        for c, (text, box) in enumerate(((N_("Year"), self.year_box),
                                         (N_("Month"), self.month_box),
                                         (N_("Day"), self.day_box),
                                         (N_("Hour"), self.hour_box))):
            g.addWidget(_label("<b>{}</b>".format(_(text))), 0, c)
            g.addWidget(box, 1, c)
            box.valueChanged.connect(self._on_box)
        g.addWidget(_label(_("your computer's time"), MUTED_STYLE), 1, 4)
        help_button = HelpButton(_(NEW_DELIVERY_HELP))
        # The hover text of the old delivery field (same meaning).
        help_button.setToolTip(_(LockTimeWidget.tooltip_text))
        g.addWidget(help_button, 1, 5)
        g.setColumnStretch(6, 1)
        layout.addWidget(self.boxes)

    def _add_big_date(self, layout):
        """"10 October 2028 (in 2 years)": the will's day, big, with the
        time left on its right in brackets (owner, p1d)."""
        row = QHBoxLayout()
        self.big_date = _scaled(QLabel(""), 1.6, bold=True)
        self.time_left = _scaled(_label("", MUTED_STYLE), 1.25)
        row.addWidget(self.big_date)
        row.addWidget(self.time_left, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        layout.addLayout(row)
        if self.period and self.stored is not None:
            self.big_date.setText(datetext.utc_day(self.stored))
            self.time_left.setText(time_left_text(self.stored, self.now))

    def _build_dates(self, layout):
        """The big date, the two times and the day-change line."""
        self._add_big_date(layout)
        self.keep_note = _label("", MUTED_STYLE)
        layout.addWidget(self.keep_note)
        layout.addSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.local_value = _scaled(_label(""), 1.2)
        self.utc_value = _scaled(_label(""), 1.2)
        for r, (text, value) in enumerate(((N_("In your time zone:"), self.local_value),
                                           (N_("In the will:"), self.utc_value))):
            grid.addWidget(_label(_(text), "QLabel{color:#4A5560;}"), r, 0)
            grid.addWidget(value, r, 1)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        self.shift_label = _label("", SHIFT_STYLE, wrap=True)
        layout.addWidget(self.shift_label)
        layout.addSpacing(6)

    def _add_mode_link(self):
        """"Mode: Basic · Advanced" at the bottom left (page 1 only)."""
        link = _label(_('Mode: <b>Basic</b> · <a href="advanced">Advanced</a>'),
                      MUTED_STYLE)
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        link.setOpenExternalLinks(False)
        link.linkActivated.connect(self._on_mode_link)
        self.mode_link = link
        buttons = self.vbox.itemAt(self.vbox.count() - 1).layout()
        buttons.insertWidget(0, link)

    # ------------------------------------------------------------ state

    def _select(self, key):
        """Choose ``key`` ("keep", 12/24/60 months, "choose")."""
        self.choice = key
        button = self.chips.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        if key != "choose":
            self._fill_boxes(self.chosen())
        self.boxes.setVisible(key == "choose")
        self.refresh()

    def _fill_boxes(self, ts):
        """Show ``ts`` in the four boxes (local time), signals blocked: a
        quick choice also fills them, so "Choose the date…" starts there."""
        if ts is None:
            return
        y, m, d, h = wizard_date.local_parts(ts, self.zone)
        boxes = (self.year_box, self.month_box, self.day_box, self.hour_box)
        for box in boxes:
            box.blockSignals(True)
        try:
            self.year_box.setValue(max(min(y, self.year_box.maximum()),
                                       self.year_box.minimum()))
            self.month_box.setValue(m)
            self.day_box.setMaximum(wizard_date.days_in_month(
                self.year_box.value(), m))
            self.day_box.setValue(d)
            self.hour_box.setValue(h)
        finally:
            for box in boxes:
                box.blockSignals(False)

    def _on_box(self, _value=None):
        """A box changed: the day follows the month length (31 -> 30, 28
        or 29), then the texts are updated."""
        days = wizard_date.days_in_month(self.year_box.value(), self.month_box.value())
        if self.day_box.maximum() != days:
            self.day_box.blockSignals(True)
            self.day_box.setMaximum(days)   # Qt clamps the value
            self.day_box.blockSignals(False)
        if self.choice == "choose":
            self.refresh()

    def chosen(self):
        """The delivery date of the current choice (UTC timestamp), or None."""
        if self.period:
            return self.stored
        if self.choice == "keep":
            return self.stored
        if self.choice == "choose":
            return wizard_date.local_to_timestamp(
                self.year_box.value(), self.month_box.value(),
                self.day_box.value(), self.hour_box.value(), self.zone)
        if self.choice in wizard_date.QUICK_MONTHS:
            return wizard_date.quick_date(self.now, self.choice)
        return None

    def refresh(self):
        """Update every text and the Next button for the current choice."""
        if not hasattr(self, "next_button") or self.choice is None:
            if self.period and hasattr(self, "next_button"):
                self.next_button.setEnabled(True)
            return
        ts = self.chosen()
        ok = wizard_date.is_valid_choice(ts, time_now())
        self.next_button.setEnabled(ok)
        if ts is None:
            return
        self.big_date.setText(datetext.utc_day(ts))
        self.time_left.setText(time_left_text(ts, self.now) if ok else "")
        self.local_value.setText("<b>{}</b> <span style='color:#6A737D'>({})</span>".format(
            datetext.local_text(ts, self.zone), wizard_date.offset_text(ts, self.zone)))
        self.utc_value.setText(datetext.utc_text(ts))
        shift = wizard_date.day_shift(ts, self.zone)
        if shift < 0:
            self.shift_label.setText("⚠  " + _(
                "In UTC it is still <b>the day before</b>: this is normal, it is "
                "the same moment."))
        elif shift > 0:
            self.shift_label.setText("⚠  " + _(
                "In UTC it is already <b>the day after</b>: this is normal, it "
                "is the same moment."))
        self.shift_label.setVisible(shift != 0)
        self.error_label.setText("" if ok else _(
            "This moment has already passed: choose a later date or hour."))
        self.error_label.setVisible(not ok)
        later = ok and wizard_date.is_later_than_in_force(ts, self.in_force)
        self.later_note.setVisible(later)
        if self.choice == "keep":
            self.keep_note.setText(self.keep_text(ts))
        self.keep_note.setVisible(self.choice == "keep")
        self.compare_label.setText(self.compare_text(ts) if ok else "")
        self.compare_label.setVisible(bool(self.compare_label.text()))

    def keep_text(self, ts):
        """What the date of "Leave as it is" really is (owner's ZIP test).

        "The date of the will in force" only when it IS the date of the will
        the executors hold; a prepared will not sent yet says so; otherwise
        it is the date chosen before, not used yet.
        """
        if self.in_force is not None and ts == self.in_force:
            return _("The date of the will in force: no change.")
        if has_draft(self.bal_window):
            return _("The date you chose last time (prepared will, not sent "
                     "yet): no change.")
        return _("The date already chosen: no change.")

    def compare_text(self, ts):
        """"Will in force: <date>" and, below, "Your choice: <date>
        (earlier — free)" or "(later — needs an invalidation transaction: you
        only pay the network fee)" (#135 follow-up wording).

        Only while a will is really in force (:func:`in_force_date`) and the
        choice is another date; "" else (owner, ZIP test: the same date
        needs no comparison, and one long line was cut).
        """
        if self.in_force is None or ts is None or ts == self.in_force:
            return ""
        if ts < self.in_force:
            how = _("(earlier — free)")
        else:
            how = _("(later — needs an invalidation transaction: you only pay "
                    "the network fee)")
        return "{}<br>{}".format(
            _("Will in force: <b>{}</b>").format(datetext.utc_text(self.in_force)),
            _("Your choice: <b>{choice}</b> {how}").format(
                choice=datetext.utc_text(ts), how=how))

    # ---------------------------------------------------------- actions

    def save(self):
        """Store the chosen date (Next). True when the page may be left.

        Through the window's validated write (``update_setting_widgets``,
        ``context.set_will_setting``), only when it differs from the stored
        date ("Leave as it is" and a period form write nothing). A refused
        write (the settings on hold) keeps the page and says so.
        """
        if self.period:
            return True
        ts = self.chosen()
        if not wizard_date.is_valid_choice(ts, time_now()):
            self.refresh()
            return False
        if ts == self.stored:
            return True
        stored = self.bal_window.update_setting_widgets(ts, "locktime", update_all=True)
        if stored is None:
            self.error_label.setText(_("The delivery date could not be saved."))
            self.error_label.setVisible(True)
            return False
        self.stored = stored
        return True

    def _next(self):
        """Next: store the date, then the Heirs page."""
        if self.save():
            self.flow.show_heirs()

    def _on_mode_link(self, _href=None):
        """"Advanced" clicked: the shared "at My Risk" gate; when it is
        confirmed the wizard goes on with the ADVANCED pages."""
        if switch_user_type(self.bal_window.bal_plugin, True, parent=self):
            self.flow.switch_to_advanced()


class HeirsPage(_StepPage):
    """Page 2: "Who receives the bitcoin?" (see module)."""

    step = 1
    question = N_("Who receives the bitcoin?")

    def __init__(self, flow):
        """Page 2; Previous: When, Next: Will-executors."""
        super().__init__(flow, flow.show_executors, flow.show_when)

    def build(self, layout):
        """The reminder, the heirs list, its buttons, the total and notes."""
        layout.addWidget(self.reminder())
        self.heir_list_widget = HeirListWidget(self.bal_window, self)
        self.heir_list_widget.setMinimumHeight(150)
        layout.addWidget(self.heir_list_widget)
        # The list is cleared and filled again at every change (also a
        # delete from its own menu): follow it.
        model = self.heir_list_widget.model()
        model.modelReset.connect(self.refresh)
        model.rowsInserted.connect(self.refresh)
        model.rowsRemoved.connect(self.refresh)
        row = QHBoxLayout()
        for text, slot in ((N_("+ Add heir"), self.add_heir),
                           # #140 (owner): the will message, one per will.
                           (N_("✉ A message in the will"), self.add_message),
                           (N_("Import"), self.import_from_file),
                           (N_("Export"), self.export_to_file)):
            button = QPushButton(_(text))
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.total_label = _label("", MUTED_STYLE, wrap=True)
        layout.addWidget(self.total_label)
        # #135: heirs sharing an address (an old wallet): shown, not changed.
        self.same_address_label = _label("", NOTE_STYLE, wrap=True)
        layout.addWidget(self.same_address_label)
        self.error_label = _label(_("Add at least one heir."), ERROR_STYLE)
        layout.addWidget(self.error_label)
        layout.addWidget(_label(
            _("Remember: 100% of the wallet balance is spent."), INFO_STYLE,
            wrap=True))

    def refresh(self, *_args):
        """The total line and the Next button (no heirs: disabled).

        #140 (owner): the message is not an heir: with only a message, Next
        stays disabled and "Add at least one heir." stays shown.
        """
        if not hasattr(self, "next_button"):
            return
        heirs = self.bal_window.heirs or {}
        count = len(heirs) - len(message_names(heirs))
        self.next_button.setEnabled(count > 0)
        self.error_label.setVisible(count == 0)
        total = percent_total(heirs) if count else None
        if total is None:
            text = ""
        else:
            decimal, _thousands = i18n.number_separators()
            number = ("{:.2f}".format(total).rstrip("0").rstrip(".")).replace(".", decimal)
            if abs(total - 100) < 1e-9:
                text = _("Total: {}% of the wallet balance.").format(number)
            else:
                text = _("Total of the shares: {}%. They count in proportion to "
                         "this total: the whole balance is spent.").format(number)
        self.total_label.setText(text)
        self.total_label.setVisible(bool(text))
        # #140: an old wallet with more than one message is told so.
        same = "\n".join(t for t in (same_address_text(heirs),
                                      extra_messages_text(heirs)) if t)
        self.same_address_label.setText(same)
        self.same_address_label.setVisible(bool(same))

    def add_heir(self):
        """"+ Add heir": today's New heir window, then the list again."""
        self.bal_window.new_heir_dialog()
        self.heir_list_widget.update()
        self.refresh()

    def add_message(self):
        """"✉ A message in the will" (#140): the message window, then the
        list again."""
        self.bal_window.message_dialog()
        self.heir_list_widget.update()
        self.refresh()

    def import_from_file(self):
        """"Import": today's import of a heirs file."""
        self.bal_window.import_heirs()
        self.heir_list_widget.update()
        self.refresh()

    def export_to_file(self):
        """"Export": today's export of the heirs."""
        self.bal_window.export_heirs()


class ExecutorsPage(_StepPage):
    """Page 3: "Who keeps the will?" (see module)."""

    step = 2
    question = N_("Who keeps the will?")

    def __init__(self, flow):
        """Page 3; Previous: Heirs, Next: Summary. :meth:`download` runs
        when the page is shown (``BasicWizardFlow.show_executors``)."""
        self.answered = None
        super().__init__(flow, flow.show_summary, flow.show_heirs)

    def build(self, layout):
        """The reminder, the explanation, the state, the list."""
        layout.addWidget(self.reminder())
        layout.addWidget(_label(
            _("Will-executors are services that keep your signed will and "
              "send it to the network on the delivery date."), MUTED_STYLE,
            wrap=True))
        self.status_label = _label("", OK_STYLE, wrap=True)
        layout.addWidget(self.status_label)
        self.nobody_row = QWidget()
        nobody = QHBoxLayout(self.nobody_row)
        nobody.setContentsMargins(0, 0, 0, 0)
        self.nobody_note = _label(
            _("<b>No will-executor answers.</b><br>Check your internet "
              "connection and press «Retry»."), NOTE_STYLE, wrap=True)
        nobody.addWidget(self.nobody_note, 1)
        self.retry_button = QPushButton(_("Retry"))
        self.retry_button.clicked.connect(self.download)
        nobody.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.nobody_row)
        self.one_label = _label(_("At least 2 will-executors are recommended."),
                                NOTE_STYLE, wrap=True)
        layout.addWidget(self.one_label)
        self.none_label = _label(_("Select at least one will-executor."),
                                 ERROR_STYLE)
        layout.addWidget(self.none_label)
        self.we_widget = WillExecutorWidget(
            self, self.bal_window,
            Willexecutors.get_willexecutors(self.bal_window.bal_plugin))
        self.we_widget.will_executor_list_widget.setMinimumHeight(120)
        model = self.we_widget.will_executor_list_widget.model()
        model.modelReset.connect(self.refresh)
        model.rowsInserted.connect(self.refresh)
        layout.addWidget(self.we_widget)

    def download(self):
        """Download the list, ping it, select only who answers (#134).

        ``BalWindow.download_and_select_willexecutors`` (the rule of the old
        "Automatically download and select"); a failed download pings the
        saved list, so an offline computer shows "No will-executor
        answers" and Retry. Then the list shows the result.
        """
        bw = self.bal_window
        try:
            bw.download_and_select_willexecutors(
                select_responding=True, ping_without_list=True)
        except Exception as e:
            _logger.error(f"will-executors not downloaded: {e!r}")
        wes = Willexecutors.get_willexecutors(bw.bal_plugin)
        self.answered = _answered(wes)
        self.we_widget.willexecutors_list = wes
        self.we_widget.will_executor_list_widget.update()
        self.refresh()

    def refresh(self, *_args):
        """The state line, the notes and the Next button."""
        if not hasattr(self, "next_button"):
            return
        selected = len(_selected(self.we_widget.willexecutors_list))
        answered = self.answered
        nobody = answered == 0
        self.nobody_row.setVisible(nobody)
        if answered:
            self.status_label.setText("✔  " + _("List updated: {answered}, {selected}.").format(
                answered=answered_text(answered), selected=selected_text(selected)))
        self.status_label.setVisible(bool(answered))
        self.one_label.setVisible(not nobody and selected == 1)
        self.none_label.setVisible(not nobody and answered is not None and selected == 0)
        self.next_button.setEnabled(bool(answered) and selected >= 1)


class SummaryPage(_StepPage):
    """Page 4: "Check and create" (see module)."""

    step = 3
    question = N_("Check and create")

    def __init__(self, flow):
        """Page 4; Previous: Will-executors, "Create the will": the build."""
        super().__init__(flow, flow.create, flow.show_executors)
        self.next_button.setText(_("Create the will"))

    def build(self, layout):
        """The green box, the "What changes" box (#135) and the two notes."""
        bw = self.bal_window
        now = time_now()
        zone = wizard_date.local_zone()
        box = QFrame()
        box.setObjectName("summaryBox")
        box.setStyleSheet(SUMMARY_STYLE)
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(14)
        locktime = delivery.as_delivery((bw.will_settings or {}).get("locktime"))
        if locktime is not None:
            when = "<b>{}</b>  {}<br><span style='color:#4A5560'>{}</span>".format(
                datetext.utc_text(locktime), time_left_text(locktime, now),
                datetext.local_line(locktime, zone))
            # #135: the comparison with the will in force is now the "What
            # changes" box under this one (it replaces the two lines of
            # #134; owner's mockup).
        else:
            when = "-"
        wes = Willexecutors.get_willexecutors(bw.bal_plugin)
        count = len(_selected(wes))
        rows = [(_("Delivery to the heirs"), when),
                (_("Heirs"), self._heirs_value(bw))]
        message = self._message_value(bw)
        if message:
            # #140 (owner): the message has a row of its own.
            rows.append((_("Message"), message))
        rows.append((_("Will-executors"), selected_text(count)))
        fee = executor_fee_text(bw, wes)
        if fee:
            rows.append((_("Will-executors' fee"), fee))
        self.values = {}
        for r, (key, value) in enumerate(rows):
            k = _label(key, "QLabel{color:#6A737D;}")
            v = _label(value, wrap=True)
            # Wrap inside the dialog's width instead of widening the column
            # past it (owner, ZIP test: a long line was cut).
            v.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            v.setMinimumWidth(200)
            grid.addWidget(k, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(v, r, 1)
            self.values[key] = v
        grid.setColumnStretch(1, 1)
        layout.addWidget(box)
        self.changes_box = None
        changes = self._changes_rows(bw, wes)
        if changes:
            layout.addSpacing(6)
            self.changes_box = what_changes_box(changes)
            layout.addWidget(self.changes_box)
        layout.addSpacing(8)
        layout.addWidget(_label(
            _("«Create the will» prepares it, asks for the password to sign it "
              "and sends it to the will-executors."), MUTED_STYLE, wrap=True))
        layout.addSpacing(4)
        layout.addWidget(_label(
            _("After it is created you can put the dates in your calendar with "
              "the 📅 button of the Will tab."), MUTED_STYLE, wrap=True))


    @staticmethod
    def _heirs_value(bw):
        """The "Heirs" value: the REAL shares (#135, owner).

        Each written percentage in proportion to their total (what the build
        pays), e.g. "alice 38.5% · bob 23.1% · carol 38.5%", with the grey
        "(recalculated in proportion)" when the written total is not 100.
        With a fixed amount the real shares depend on the balance: today's
        text (``heirs_text``), never an invented share. #140: the messages
        are not heirs: they have their own row (:meth:`_message_value`).
        """
        heirs = {name: value for name, value in (bw.heirs or {}).items()
                 if not _is_message(value)}
        real = will_changes.real_shares_text(heirs)
        if real is None:
            return heirs_text(bw, heirs)
        text, rescaled = real
        if rescaled:
            text += " <span style='color:#6A737D'>{}</span>".format(
                _("(recalculated in proportion)"))
        return text

    @staticmethod
    def _message_value(bw):
        """The "Message" value (#140): the text in quotes, or "" with no
        message. Escaped: the label is rich text and the text is the
        owner's own. More than one (an old wallet): joined by " / "."""
        heirs = bw.heirs or {}
        texts = [message_text(heirs[name][0]) for name in message_names(heirs)]
        return " / ".join("“{}”".format(html.escape(t)) for t in texts)

    @staticmethod
    def _changes_rows(bw, wes):
        """The rows of "What changes compared with the will in force" (#135).

        The will in force (``engine.context.in_force_items``, the #128
        notion) against what the NEW SETTINGS will give (nothing is built
        yet): the date, the heirs, the will-executors selected now, the
        miner fee. None with no will in force (a first will: no box).
        """
        try:
            selected = selected_urls(wes)
            old = will_changes.side_from_items(engine_context.in_force_items(bw), selected)
            settings = bw.will_settings or {}
            new = will_changes.side_from_settings(
                bw.heirs or {}, settings.get("locktime"), selected,
                settings.get("baltx_fees"))
            # #137: the will-executors new on the network, selected by the
            # download of page 3, are said so.
            return will_changes.change_rows(
                will_changes.will_changes(old, new), wizard=True,
                new_in_network=getattr(bw, "new_willexecutors", None) or ())
        except Exception as e:
            _logger.error(f"what changes not shown: {e!r}")
            return None


# ----------------------------------------------------------------- flow


class BasicWizardFlow:
    """The order of the BASIC pages inside a ``BalWizardDialog`` (#134).

    Takes the :class:`WizardSnapshot` when the wizard opens; the dialog
    calls :meth:`restore` on Cancel or close (not after "Create the will").
    """

    def __init__(self, dialog):
        """Start the flow for ``dialog`` (a ``BalWizardDialog``)."""
        self.dialog = dialog
        self.bal_window: "BalWindow" = dialog.bal_window
        self.snapshot = WizardSnapshot.take(self.bal_window)
        self._restored = False

    def first_page(self):
        """Page 1, not shown yet (the dialog adds it)."""
        return WhenPage(self)

    def show_when(self):
        """Page 1."""
        self.dialog.next_widget(WhenPage(self))

    def show_heirs(self):
        """Page 2."""
        self.dialog.next_widget(HeirsPage(self))

    def show_executors(self):
        """Page 3: shown first, then the download and ping (their "Please
        wait" windows open over it)."""
        page = ExecutorsPage(self)
        self.dialog.next_widget(page)
        page.download()

    def show_summary(self):
        """Page 4."""
        self.dialog.next_widget(SummaryPage(self))

    def create(self):
        """"Create the will": today's build / sign / send / verify window
        (``BalWizardDialog.on_next_we``), the changes kept."""
        self.dialog.on_next_we()

    def restore(self):
        """Cancel or close: put everything back, once (see the snapshot)."""
        if self._restored:
            return
        self._restored = True
        self.snapshot.restore(self.bal_window)

    def switch_to_advanced(self):
        """ADVANCED was confirmed: the dialog goes on with the old pages."""
        self.dialog.switch_to_advanced()
