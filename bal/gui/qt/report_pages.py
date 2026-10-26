"""The pages of the will report, laid out and painted with QPainter (CHANGELOG #132).

:func:`bal.core.report.will_report` gives the data (no Qt); here it becomes
A4 pages:

* page 1, for everyone: the header (the àncòra logo on the left; on the
  right "Will plan", when it was made, the wallet, the versions), the
  state box in the colours of the will summary, the #131 time line with its
  year ruler, the heirs with their full addresses (in groups of 4
  characters, with a QR code of the address, #145) and the total, the
  instalments (multiverse only), the will-executors (squares and list) and
  how it works;
* multiverse only: a page "Will-executors per instalment" (a grid of
  squares, will-executor x instalment) with how many transactions there
  are;
* then the technical details: each will-executor's fee and the full
  transaction id (instalment 1 only with multiverse; the later instalments
  as counts), the locktime and the miner fee, and the wills sent before
  that the will-executors still hold;
* on every page the footer "PRIVATE — keep it in a safe place" and
  "Page x/y"; after the first, a small header (logo + subject).

WHY a page space in points: :func:`layout_report` places everything in a
page of :data:`PAGE_W` x :data:`PAGE_H` units = points (A4 less 15 mm
margins), with fonts sized in those units (``QFont.setPixelSize``); the
painter is scaled to the device (PDF, printer, image), so the result is the
same on every printer. A row (an heir with its address, a transaction with
its id) is never split across pages; a section title always stays with its
first row.

Dates (#145, owner): the one date style of the plugin (CHANGELOG #134,
:mod:`bal.core.datetext`), "24 December 2032, 01:00 UTC", the short form
only where the long one does not fit; "Created on" is the local time of the
computer that made the report.

Two steps, so the tests can read what is drawn without a PDF parser:
:func:`layout_report` returns :class:`Page` objects (a list of
:class:`Item` with their texts and places) and :func:`page_texts` lists the
texts of a page; :func:`paint_page` / :func:`print_pages` /
:func:`page_image` draw them.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import QApplication

from ...core import datetext
from ...core import future as core_future
from ...core.brand import INK, WATER
from ...core.future import ExecutorMark
from ...core.report import _host, _percent
from ...i18n import _
from . import future_view as fv
from .widgets import DELIVERY_LEVEL_COLOURS, WillSummaryWidget, message_warning_text

#: The page space, in points: A4 (210 x 297 mm) less 15 mm on every side.
MARGIN_MM = 15
PAGE_W = (210 - 2 * MARGIN_MM) / 25.4 * 72.0
PAGE_H = (297 - 2 * MARGIN_MM) / 25.4 * 72.0
#: Room kept for the footer at the bottom and the small header on top of
#: the pages after the first.
FOOTER_H = 24.0
SMALL_HEADER_H = 28.0
#: The logo file in the plugin (read with the plugin's ``read_file``: the
#: plugin is loaded from a zip): ``ancora-lockup-1200.png`` of the brand kit,
#: unchanged (``assets/brand/png``, #163: anchor + "àncòra").
LOGO_FILE = "icons/ancora-lockup.png"
#: Width / height of the logo (the file's own proportions).
LOGO_RATIO = 1200.0 / 266.0
#: The grid page shows at most this many will-executors, then "(+N more)".
MAX_GRID_ROWS = 40

# Brand colours (brand/BRAND.md) and the report's own greys.
TEAL = "#0D9488"
NAVY = "#0A0F1C"
TEXT = "#111111"
GREY = "#555555"
RULE = "#d6d6d6"
#: The line between two heirs (#145, owner: light, but visible on paper).
HEIR_RULE = "#bdbdbd"
#: The side of an heir's address QR code: 15 mm (owner: smaller than the
#: first 20 mm). A 62-character address needs 33 modules: about 0.45 mm
#: each, still read by a phone on paper.
QR_SIDE = 15 / 25.4 * 72.0


@dataclass
class Item:
    """One thing drawn on a page.

    ``kind``: ``"text"`` (``text`` in ``font`` and ``colour``, wrapped in
    ``rect``, aligned with ``align``), ``"rect"`` (``fill`` and a border in
    ``colour``, corner ``radius``; no border when ``colour`` is empty),
    ``"line"`` (from the top-left to the top-right of ``rect``, ``width``
    thick), ``"logo"`` (the àncòra logo in ``rect``), ``"timeline"``
    (the #131 time line of ``scene``, drawn by ``future_view.paint_future``)
    or ``"qr"`` (the QR code of ``text`` filling the square ``rect``, #145).
    """

    kind: str
    rect: QRectF
    text: str = ""
    font: Optional[QFont] = None
    colour: str = TEXT
    fill: Optional[str] = None
    align: int = 0
    radius: float = 0.0
    width: float = 0.5
    scene: Optional[dict] = None
    base_font: Optional[QFont] = None


@dataclass
class Page:
    """One page: its items (in page points), its subject (the small header
    of the pages after the first) and its number (from 1)."""

    number: int
    subject: str = ""
    items: List[Item] = field(default_factory=list)


# ------------------------------------------------------------------ fonts

def report_font(size, bold=False, italic=False, mono=False):
    """A font of ``size`` page points (see the module docstring).

    The family is Electrum's (the plugin looks native, brand/BRAND.md); the
    ids and addresses use a monospaced font. No hinting: the text is
    measured once and must take the same room on the screen, in the PDF and
    on a 1200 dpi printer.
    """
    if mono:
        font = QFont("Courier New")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setFixedPitch(True)
    else:
        font = QFont(QApplication.font().family())
    font.setPixelSize(int(size))
    font.setBold(bold)
    font.setItalic(italic)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


_WRAP = int(Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)


def text_height(text, font, width, align=0):
    """The height of ``text`` wrapped at ``width`` (page points)."""
    flags = _WRAP | int(align or Qt.AlignmentFlag.AlignLeft)
    box = QFontMetricsF(font).boundingRect(QRectF(0, 0, width, 100000), flags, text)
    return box.height()


def text_width(text, font):
    """The width of ``text`` on one line (page points)."""
    return QFontMetricsF(font).horizontalAdvance(text)


def date_fit(ts, font, width):
    """A will date in the plugin's one style (#145): the long form
    "24 December 2032, 01:00 UTC", or the short "24 Dec 2032, 01:00 UTC"
    when the long one is wider than ``width`` in ``font``."""
    text = datetext.utc_text(ts)
    if text_width(text, font) > width:
        text = datetext.utc_short(ts)
    return text


def address_groups(address):
    """``address`` in groups of 4 characters ("bc1q g4w5 qq75 ...", #145).

    Owner: easier to read and to copy by hand from paper; a long address
    (up to 62 characters) can then wrap between two groups with any font.
    The page says to copy it without the spaces; the QR code next to it
    holds the address itself.
    """
    text = str(address or "")
    return " ".join(text[i:i + 4] for i in range(0, len(text), 4))


def qr_modules(text):
    """The QR code of ``text``: rows of booleans (True: a dark module), no
    border. The ``qrcode`` package ships with Electrum (the plugin's QR
    export uses it too); imported here so the report still loads without
    it. ``None`` when it is missing or ``text`` cannot be encoded."""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.ERROR_CORRECT_M, border=0)
        qr.add_data(str(text))
        qr.make(fit=True)
        return qr.get_matrix()
    except Exception:
        return None


# ----------------------------------------------------------------- blocks

class _Block:
    """Items laid out from y = 0; :class:`_Flow` places the block on a page."""

    def __init__(self):
        """An empty block."""
        self.items = []
        self.height = 0.0

    def text(self, x, y, w, text, font, colour=TEXT, align=0):
        """Add ``text`` wrapped in width ``w`` at ``(x, y)``; return its height."""
        height = text_height(text, font, w, align)
        self.items.append(Item("text", QRectF(x, y, w, height), text=text, font=font,
                               colour=colour, align=int(align)))
        self.height = max(self.height, y + height)
        return height

    def rect(self, x, y, w, h, fill, colour="", radius=0.0):
        """Add a filled rectangle (drawn under the items added after it)."""
        self.items.append(Item("rect", QRectF(x, y, w, h), fill=fill, colour=colour,
                               radius=radius))
        self.height = max(self.height, y + h)

    def line(self, x, y, w, colour=RULE, width=0.5):
        """Add a horizontal line."""
        self.items.append(Item("line", QRectF(x, y, w, 0), colour=colour, width=width))
        self.height = max(self.height, y + width)

    def gap(self, h):
        """Leave ``h`` points of empty room under the block."""
        self.height += h


class _Flow:
    """Puts blocks on pages, top to bottom, never splitting a block."""

    def __init__(self):
        """Start page 1 (no small header)."""
        self.pages = []
        self.subject = ""
        self.pending = None
        self._new_page()

    def _new_page(self):
        """Open the next page; the pages after the first start lower."""
        self.pages.append(Page(number=len(self.pages) + 1, subject=self.subject))
        self.top = 0.0 if len(self.pages) == 1 else SMALL_HEADER_H
        self.y = self.top

    def new_page(self, subject, keep=False):
        """Start a new page for ``subject`` (unless the current one is empty).

        ``keep``: stay on the current page when it already belongs to
        ``subject`` (it was opened for it and holds only what was moved
        to its top, e.g. "How it works").
        """
        if keep and self.pages[-1].number > 1 and self.pages[-1].subject == subject:
            self.subject = subject
            return
        self.subject = subject
        if self.y > self.top:
            self._new_page()
        else:
            self.pages[-1].subject = subject

    def fits(self, height):
        """True when ``height`` points still fit on the current page."""
        return self.y + height <= PAGE_H - FOOTER_H

    def heading(self, title, subject=None):
        """A section title: kept with the next block (never alone at a page end).

        ``subject``: the small header of a page this section opens
        (default: the title).
        """
        self.pending = _heading_block(title)
        self.subject = subject or title

    def add(self, block, space=4.0):
        """Place ``block`` (with the pending section title) on the page."""
        items = list(block.items)
        height = block.height
        if self.pending is not None:
            lead = self.pending.height
            items = self.pending.items + [_moved(i, lead) for i in items]
            height += lead
            self.pending = None
        bottom = PAGE_H - FOOTER_H
        if self.y + height > bottom and self.y > self.top:
            self._new_page()
        page = self.pages[-1]
        if not page.items and page.number > 1:
            page.subject = self.subject
        page.items += [_moved(i, self.y) for i in items]
        self.y += height + space


def _heading_block(title):
    """A section title in the brand colour with a rule under it."""
    block = _Block()
    block.text(0, 0, PAGE_W, title, report_font(11, bold=True), TEAL)
    block.line(0, block.height + 2, PAGE_W, TEAL, 0.8)
    block.gap(6)
    return block


def _moved(item, dy):
    """A copy of ``item`` moved down by ``dy``."""
    rect = QRectF(item.rect)
    rect.translate(0, dy)
    return Item(item.kind, rect, item.text, item.font, item.colour, item.fill,
                item.align, item.radius, item.width, item.scene, item.base_font)


# ----------------------------------------------------------------- layout

def _status_colours(kind):
    """``(text and border colour, background)`` of the summary ``kind``
    (the Will tab's box, ``WillSummaryWidget._STYLES``)."""
    fg, bg, _icon = WillSummaryWidget._STYLES.get(kind, WillSummaryWidget._STYLES["info"])
    return fg, bg


def _header_block(report):
    """Page 1 header: the logo on the left, the title and the facts on the right.

    Owner: the logo only (no "Heartbeat"); the versions say "Plugin"
    (existing msgid) for the same reason.
    """
    block = _Block()
    logo_h = 30.0
    block.items.append(Item("logo", QRectF(0, 2, logo_h * LOGO_RATIO, logo_h)))
    right = Qt.AlignmentFlag.AlignRight
    x, w = PAGE_W * 0.45, PAGE_W * 0.55
    y = block.text(x, 0, w, _("Will plan"), report_font(18, bold=True), NAVY, right)
    small = report_font(8)
    lines = [
        # #145: the one date style; the local time of this computer.
        "{}: {}".format(_("Created on"), datetext.moment_text(report.created)),
    ]
    if report.wallet_name:
        lines.append("{}: {}".format(_("Wallet"), report.wallet_name))
    lines.append("{}: {} · Electrum {}".format(
        _("Plugin"), report.plugin_version, report.electrum_version))
    y += 2
    for line in lines:
        y += block.text(x, y, w, line, small, GREY, right)
    block.line(0, max(block.height, logo_h + 4) + 6, PAGE_W, TEAL, 1.2)
    block.gap(6)
    block.text(0, block.height, PAGE_W, _(
        "PRIVATE - It contains no seed, no private keys and no passwords, but "
        "it says who will receive your bitcoin, how much and when. Keep it in "
        "a safe place."
    ), report_font(8, italic=True), GREY)
    return block


def _status_block(report):
    """The state box, coloured like the will summary of the Will tab."""
    future = report.future
    fg, bg = _status_colours(report.summary.get("kind"))
    rows = []      # (label, value, colour)
    if future.delivery:
        rows.append((_("Delivery to the heirs"), "{} {}".format(
            datetext.utc_text(future.delivery), future.time_left),
            DELIVERY_LEVEL_COLOURS.get(future.level, TEXT)))
        rows.append((_("Delivery form"),
                     core_future.delivery_form_text(future.form, future.period_months), TEXT))
    if future.check_alive is not None:
        value = datetext.utc_text(future.check_alive)
        if not future.check_alive_passed:
            value += " — " + _("from that day the wallet asks you to renew it")
        rows.append((_("Check Alive"), value, "#c62828" if future.check_alive_passed else TEXT))
    executors = future.executors_text or _("none (backup transaction only)")
    rows.append((_("Will-Executor"), executors, TEXT))
    if report.miner_fee:
        rows.append((_("Miner fee"), report.miner_fee, TEXT))
    label_font, value_font = report_font(9), report_font(9, bold=True)
    pad, bar = 10.0, 5.0
    x = bar + pad
    inner = PAGE_W - x - pad
    label_w = max(text_width(label + ":", label_font) for label, _v, _c in rows) + 12
    block = _Block()
    title = report.summary.get("title") or future.title
    y = pad
    y += block.text(x, y, inner, title, report_font(13, bold=True), fg) + 5
    for label, value, colour in rows:
        h = block.text(x, y, label_w, label + ":", label_font, GREY)
        h = max(h, block.text(x + label_w, y, inner - label_w, value, value_font, colour))
        y += h + 3
    note = report.summary.get("note") or future.note
    if note:
        y += block.text(x, y + 2, inner, note, report_font(9, italic=True), "#333333") + 2
    # #190: the excluded will-executors that still hold a copy, as in the
    # Will tab: the first sentence in bold, then one line per sentence.
    for i, line in enumerate(report.summary.get("held_text") or []):
        y += block.text(x, y + (4 if i == 0 else 1), inner, line,
                        report_font(9, bold=(i == 0)), "#555555") + 1
    height = y + pad - 3
    # The box goes under the texts: put it first.
    box = [Item("rect", QRectF(0, 0, PAGE_W, height), fill=bg, colour=fg, radius=5),
           Item("rect", QRectF(0, 0, bar, height), fill=fg, radius=0)]
    block.items = box + block.items
    block.height = height
    return block


def timeline_scene(scene):
    """The #131 scene with only its time line (ruler, axis, dates).

    The report says the rest (state, will-executors, the sentence) in its
    own sections, so the picture does not repeat it.
    """
    out = dict(scene)
    out.update(title="", lines=[], executors=[], executor_legend=[],
               executor_explain="", sentence="", sentence_tip="", more=[], note="")
    return out


def _timeline_block(scene):
    """The time line on a light panel, scaled to the page width."""
    base = report_font(9)
    tl = timeline_scene(scene)
    height = fv.scene_height(tl, PAGE_W, base)
    block = _Block()
    block.rect(0, 0, PAGE_W, height, "#fbfbfb", "#d0d0d0", 5)
    block.items.append(Item("timeline", QRectF(0, 0, PAGE_W, height), scene=tl,
                            base_font=base))
    return block


def _heir_blocks(report, format_amount):
    """The heirs table: name, real share, amount and the QR code of the
    address on the right; the full address under the name; then the total.

    #145 (owner, on paper): the address in a larger monospaced font, black
    (the thin grey one could fade), in groups of 4 characters (see
    :func:`address_groups`; one sentence above the table says to copy it
    without the spaces), wrapped before the share column; the QR code of the
    address right of the amount, from the top of the row (a lower row); a
    visible light line between two heirs.
    """
    qr_x = PAGE_W - QR_SIDE
    # Owner (#145 preview): narrow columns, so a usual address (42
    # characters, 11 groups) stays on one line. The share and amount
    # columns are as wide as their widest value (amounts in the wallet's
    # unit; a longer text, as "excluded (below the dust limit)", wraps), the
    # headers wrap when longer; the address gets the rest.
    value_font, total_font = report_font(10), report_font(10, bold=True)
    amounts = [format_amount(h.sats) for h in report.heirs if h.sats is not None]
    amount_w = min(130.0, 4 + max(
        [text_width(a, value_font) for a in amounts]
        + [text_width(format_amount(report.total_sats), total_font), 40.0]))
    share_w = 10 + max([text_width(_percent(h.percent), value_font)
                        for h in report.heirs if h.percent is not None] + [30.0])
    amount_x = qr_x - 8 - amount_w
    share_x = amount_x - share_w
    head = _Block()
    small = report_font(8, bold=True)
    right = Qt.AlignmentFlag.AlignRight
    head.text(0, 0, PAGE_W, _(
        "The addresses are written in groups of 4 characters to read them "
        "better: copy them without the spaces. The QR code next to each one "
        "holds the same address."), report_font(8, italic=True), GREY)
    head.gap(4)
    top = head.height
    # Owner (#145): the column holds the name and, under it, the address.
    head.text(0, top, share_x, _("Name and Bitcoin address"), small, GREY)
    head.text(share_x, top, share_w, _("Heir's share"), small, GREY, right)
    # Owner (#145 preview): over the start of the heirs' amounts (all of
    # the same width in BTC, right-aligned), not at the right edge.
    amounts_x = amount_x + amount_w - max(
        [text_width(a, value_font) for a in amounts] + [0.0])
    head.text(amounts_x, top, amount_x + amount_w - amounts_x, _("Amount"),
              small, GREY)
    head.line(0, head.height + 2, PAGE_W, HEIR_RULE, 0.8)
    blocks = [head]
    for heir in report.heirs:
        b = _Block()
        name_font = report_font(10, bold=True)
        h = b.text(0, 5, share_x - 10, heir.name, name_font)
        share = _percent(heir.percent) if heir.percent is not None else ""
        b.text(share_x, 5, share_w, share, report_font(10), TEXT, right)
        amount = (format_amount(heir.sats) if heir.sats is not None
                  else _("excluded (below the dust limit)"))
        b.text(amount_x, 5, amount_w, amount, report_font(10), TEXT, right)
        y = 5 + h + 3
        y += b.text(0, y, share_x - 4, address_groups(heir.address),
                    report_font(10, mono=True), TEXT)
        if heir.address:
            # A quiet zone of about 4 modules around the code (scanners
            # need it): the room left of it, the gaps above and below.
            b.items.append(Item("qr", QRectF(qr_x, 5, QR_SIDE, QR_SIDE),
                                text=str(heir.address)))
            y = max(y, 5 + QR_SIDE)
        b.line(0, y + 6, PAGE_W, HEIR_RULE, 0.8)
        b.gap(1)
        blocks.append(b)
    # #145: the total is drawn in the block of the last heir, so it never
    # opens a page alone.
    total = blocks[-1] if len(blocks) > 1 else _Block()
    y = total.height + 2
    total.text(0, y, amount_x, _("Total to the heirs"), report_font(10, bold=True))
    total.text(amount_x, y, amount_w, format_amount(report.total_sats),
               report_font(10, bold=True), TEXT, right)
    if total is not blocks[-1]:
        blocks.append(total)
    return blocks


def _message_block(report):
    """The will message (#140, owner): each text in quotes and, under it, the
    plugin's own warning of the message window (#145, owner: public forever,
    read by the will-executors before the delivery, no private data)."""
    b = _Block()
    y = 2
    for text in report.messages:
        y += b.text(0, y, PAGE_W, "“{}”".format(text), report_font(10, italic=True))
        y += 2
    b.text(0, y + 2, PAGE_W, message_warning_text(), report_font(8), GREY)
    return b


def _instalment_blocks(report):
    """Multiverse: one row per instalment (number, UTC date, the share of
    each heir on that date, how many will-executors verified it)."""
    cols = [(0, 26), (28, 112), (144, PAGE_W - 144 - 130), (PAGE_W - 128, 128)]
    head = _Block()
    small = report_font(8, bold=True)
    for (x, w), text in zip(cols, (_("N."), _("Date"), _("Heirs"), _("Will-Executor")),
                            strict=True):
        head.text(x, 0, w, text, small, GREY)
    head.line(0, head.height + 2, PAGE_W)
    blocks = [head]
    font = report_font(9)
    for inst in report.instalments:
        b = _Block()
        heirs = ", ".join(
            "{} {}".format(h.name, _percent(h.percent))
            for h in inst.heirs if h.percent is not None)
        # #145: the short form of the one date style (a narrow column; it
        # wraps under the day when it does not fit).
        texts = (str(inst.number), datetext.utc_short(inst.locktime), heirs,
                 _("sent and verified {} of {}").format(inst.verified, len(inst.states)))
        y = 0.0
        for (x, w), text in zip(cols, texts, strict=True):
            y = max(y, b.text(x, 3, w, text, font))
        b.line(0, y + 6, PAGE_W)
        blocks.append(b)
    return blocks


def _square(block, x, y, side, colour):
    """A will-executor square (#131 colours) with a grey border."""
    block.rect(x, y, side, side, colour, "#666666", 0)


def _executor_colour(state):
    """The #131 colour of a will-executor ``state``."""
    return fv._EXECUTOR_COLOURS.get(state, fv._EXECUTOR_OTHER)


def _executor_blocks(report):
    """The will-executors: one row per will-executor (its small square in
    the state's colour, host and state; two per row above four, so 30 or 40
    take 15 or 20 rows that flow to the next page, never split), then the
    one-line explanation."""
    marks = report.future.executors
    if not marks:
        b = _Block()
        y = b.text(0, 0, PAGE_W, _("none (backup transaction only)"), report_font(10, bold=True))
        b.text(0, y + 3, PAGE_W, _(
            "No will-executor holds your will: you keep the signed transaction "
            "yourself (backup transaction). Nobody will broadcast it for you: "
            "after the delivery date it must be broadcast by you or by someone "
            "you gave it to."
        ), report_font(9))
        return [b]
    # Owner (#132, after the first preview): no row of big squares; the
    # small square of each row already shows its state.
    blocks = []
    font, bold = report_font(9), report_font(9, bold=True)
    # More than four will-executors: two per row, so a usual will (up to
    # ten) still fits on page 1.
    per_row = 2 if len(marks) > 4 else 1
    col_w = PAGE_W / per_row
    host_w = 140.0 if per_row == 2 else 200.0
    for start in range(0, len(marks), per_row):
        b = _Block()
        for i, mark in enumerate(marks[start:start + per_row]):
            x = i * col_w
            _square(b, x, 2, 8, _executor_colour(mark.state))
            b.text(x + 14, 0, host_w - 18, _host(mark.url), bold)
            b.text(x + host_w, 0, col_w - host_w - 6, fv.executor_state_text(mark.state),
                   font, GREY)
        blocks.append(b)
    explain = _Block()
    explain.text(0, 2, PAGE_W, _(
        "Will-executors keep a copy of your will and broadcast it on the "
        "delivery date."
    ), report_font(9, italic=True), GREY)
    blocks.append(explain)
    return blocks


def _how_blocks(scene):
    """How it works: the #131 sentence (and the later instalments), then the
    #92 paragraph."""
    b = _Block()
    y = 0.0
    if scene.get("sentence"):
        y += b.text(0, y, PAGE_W, scene["sentence"], report_font(10, bold=True)) + 3
    for text in scene.get("more") or []:
        y += b.text(12, y, PAGE_W - 12, text, report_font(9)) + 2
    b.text(0, y + 3, PAGE_W, _(
        "As long as you renew the will before the delivery date (moving it "
        "later), your bitcoin stays yours. If you stop, from the delivery "
        "date the will-executors broadcast the transaction already signed "
        "and the heirs receive the amounts shown above."
    ), report_font(9))
    return [b]


def _instalment_label(number):
    """``"Delivery 2"`` (the label of one delivery of a will in stages)."""
    # The user reads "Delivery" where the code says "instalment" (owner,
    # #132: "rata" sounded like a loan).
    return _("Delivery {}").format(number)


def _grid_blocks(report):
    """Multiverse: will-executor x instalment squares, at most
    :data:`MAX_GRID_ROWS` rows then "(+N more)", the colour legend and how
    many transactions there are."""
    insts = report.instalments
    urls = sorted({url for inst in insts for url in inst.states})
    host_w = 190.0
    col_w = (PAGE_W - host_w) / max(len(insts), 1)
    center = Qt.AlignmentFlag.AlignHCenter
    head = _Block()
    for i, inst in enumerate(insts):
        x = host_w + i * col_w
        y = head.text(x, 0, col_w, _instalment_label(inst.number),
                      report_font(8, bold=True), GREY, center)
        # The UTC day only, short: four full dates side by side would touch.
        head.text(x, y, col_w, datetext.utc_day(inst.locktime, short=True),
                  report_font(7), GREY, center)
    head.line(0, head.height + 2, PAGE_W)
    blocks = [head]
    for url in urls[:MAX_GRID_ROWS]:
        b = _Block()
        b.text(0, 1, host_w - 6, _host(url), report_font(8, bold=True))
        for i, inst in enumerate(insts):
            state = inst.states.get(url)
            if state:
                _square(b, host_w + i * col_w + col_w / 2 - 5, 1, 10, _executor_colour(state))
        b.gap(1)
        blocks.append(b)
    if len(urls) > MAX_GRID_ROWS:
        more = _Block()
        more.text(0, 0, PAGE_W, _("(+{} more)").format(len(urls) - MAX_GRID_ROWS),
                  report_font(8, italic=True), GREY)
        blocks.append(more)
    legend = _Block()
    states = {}
    for inst in insts:
        for url, state in inst.states.items():
            states.setdefault(url, state)
    x = 0.0
    font = report_font(8)
    for colour, text in fv._executor_legend([ExecutorMark(u, s) for u, s in states.items()]):
        _square(legend, x, 4, 8, colour)
        legend.text(x + 12, 2, text_width(text, font) + 4, text, font, GREY)
        x += 12 + text_width(text, font) + 18
    blocks.append(legend)
    counts = ", ".join("{}: {}".format(_instalment_label(i.number), i.tx_count) for i in insts)
    sentence = _Block()
    sentence.text(0, 4, PAGE_W, _(
        "Each delivery spends the change of the one before at every "
        "will-executor, so the transactions multiply: {}. Only one "
        "transaction per delivery can ever be confirmed."
    ).format(counts), report_font(9))
    blocks.append(sentence)
    return blocks


def _tx_block(tx, format_amount, show_fee=True):
    """A transaction: holder (and the will-executor's fee), then the full id."""
    b = _Block()
    holder = tx.host or _("no will-executor (backup transaction)")
    h = b.text(0, 3, PAGE_W - 170, holder, report_font(9, bold=True))
    if show_fee and tx.fee is not None:
        b.text(PAGE_W - 170, 3, 170, "{}: {}".format(_("Executor fee"), format_amount(tx.fee)),
               report_font(9), TEXT, Qt.AlignmentFlag.AlignRight)
    if not show_fee:
        b.text(PAGE_W - 170, 3, 170, date_fit(tx.locktime, report_font(9), 170),
               report_font(9), TEXT, Qt.AlignmentFlag.AlignRight)
    y = 3 + h + 1
    label = _("Txid") + ": "
    lw = text_width(label, report_font(8))
    b.text(0, y, lw + 2, label, report_font(8), GREY)
    y += b.text(lw + 2, y, PAGE_W - lw - 2, tx.txid, report_font(8, mono=True))
    b.line(0, y + 3, PAGE_W)
    return b


def _subtitle(text):
    """A bold line opening a part of the technical details."""
    b = _Block()
    b.text(0, 4, PAGE_W, text, report_font(10, bold=True), NAVY)
    b.gap(2)
    return b


def _technical(flow, report, format_amount):
    """The technical details (a new page, see the module docstring)."""
    flow.new_page(_("TECHNICAL DETAILS"), keep=True)
    flow.heading(_("TECHNICAL DETAILS"))
    facts = _Block()
    y = 0.0
    font = report_font(9)
    if report.delivery:
        y += facts.text(0, y, PAGE_W, "{}: {} · {}".format(
            _("Locktime"), report.delivery, datetext.utc_text(report.delivery)), font) + 2
    if report.miner_fee:
        y += facts.text(0, y, PAGE_W, "{}: {}".format(_("Miner fee"), report.miner_fee),
                        font) + 2
    flow.add(facts)
    if report.multiverse:
        flow.add(_subtitle("{} · {}".format(
            _instalment_label(1), datetext.utc_text(report.instalments[0].locktime))))
    for tx in report.first_txs:
        flow.add(_tx_block(tx, format_amount), space=0)
    if report.multiverse:
        for inst in report.instalments[1:]:
            b = _Block()
            b.text(0, 3, PAGE_W, " · ".join((
                _instalment_label(inst.number), datetext.utc_text(inst.locktime),
                "{}: {}".format(_("Transactions"), inst.tx_count),
                _("sent and verified {} of {}").format(inst.verified, len(inst.states)),
            )), report_font(9))
            flow.add(b, space=0)
        note = _Block()
        note.text(0, 4, PAGE_W, _(
            "Only the transactions of delivery 1 are listed here. The full "
            "list is in the will file (Tools → Export)."
        ), report_font(9, italic=True), GREY)
        flow.add(note)
    flow.add(_subtitle(_("Wills sent before and still held by the will-executors")))
    if not report.older:
        none = _Block()
        none.text(0, 0, PAGE_W, _(
            "None: the will-executors hold no other will that can still be "
            "executed."
        ), report_font(9))
        flow.add(none)
    for tx in report.older:
        flow.add(_tx_block(tx, format_amount, show_fee=False), space=0)


def layout_report(report, format_amount):
    """Lay ``report`` (a :class:`bal.core.report.WillReport`) out on pages.

    ``format_amount(sats)``: an amount in the wallet's unit (owner: never
    a fiat value). Returns the :class:`Page` list, with the small headers
    and the footers ("Page x/y") already added.
    """
    # #145: the dates of the time line in the one style, as the Future view
    # of the Will tab (future_view.view_scene, without its greying: the
    # report describes the will in force only).
    scene = fv.future_scene(
        report.future, format_date=datetext.utc_text, format_amount=format_amount,
        format_short=datetext.utc_short,
        split_date=lambda ts: ("", datetext.utc_short(ts)),
    )
    flow = _Flow()
    flow.add(_header_block(report), space=10)
    flow.add(_status_block(report), space=8)
    flow.add(_timeline_block(scene), space=10)

    flow.heading(_("HEIRS"))
    for block in _heir_blocks(report, format_amount):
        flow.add(block, space=0)
    flow.y += 10
    if report.messages:
        # #145 (owner): where it is in the transaction, for a technical
        # reader ("OP_RETURN" is a Bitcoin term, never translated).
        flow.heading("{} (OP_RETURN)".format(_("MESSAGE IN THE WILL")))
        flow.add(_message_block(report))
        flow.y += 8
    if report.multiverse:
        flow.heading(_("DELIVERIES"))
        for block in _instalment_blocks(report):
            flow.add(block, space=0)
        flow.y += 10
    flow.heading(_("WILL-EXECUTORS"))
    for block in _executor_blocks(report):
        flow.add(block, space=2)
    flow.y += 8
    # "How it works" on page 1 when it fits; otherwise at the top of the
    # next page (the grid page, or the technical details), never alone on
    # a page of its own (owner, #132 preview).
    how = _how_blocks(scene)
    following = (_("WILL-EXECUTORS PER DELIVERY") if report.multiverse
                 else _("TECHNICAL DETAILS"))
    if not flow.fits(_heading_block(_("HOW IT WORKS")).height
                     + sum(b.height for b in how)):
        flow.new_page(following)
    flow.heading(_("HOW IT WORKS"), subject=flow.subject)
    for block in how:
        flow.add(block)
    flow.y += 8

    if report.multiverse:
        # The grid continues the page when it fits whole (owner: no page
        # half empty), otherwise it opens a page of its own.
        grid = _grid_blocks(report)
        title = _("WILL-EXECUTORS PER DELIVERY")
        if not flow.fits(_heading_block(title).height + sum(g.height + 1 for g in grid)):
            flow.new_page(title, keep=True)
        flow.heading(title)
        for block in grid:
            flow.add(block, space=1)
        flow.y += 8
    _technical(flow, report, format_amount)

    pages = flow.pages
    for page in pages:
        _decorate(page, len(pages))
    return pages


def _decorate(page, total):
    """Add the footer (every page) and the small header (pages after the first)."""
    small = report_font(8)
    y = PAGE_H - FOOTER_H + 8
    page.items.append(Item("line", QRectF(0, y, PAGE_W, 0), colour=RULE, width=0.5))
    page.items.append(Item("text", QRectF(0, y + 4, PAGE_W * 0.7, 12), font=small,
                           colour=GREY, text=_("PRIVATE — keep it in a safe place")))
    page.items.append(Item("text", QRectF(PAGE_W * 0.7, y + 4, PAGE_W * 0.3, 12),
                           font=small, colour=GREY, text=_("Page {}/{}").format(page.number, total),
                           align=int(Qt.AlignmentFlag.AlignRight)))
    if page.number > 1:
        logo_h = 12.0
        page.items.append(Item("logo", QRectF(0, 0, logo_h * LOGO_RATIO, logo_h)))
        subject = "{} · {}".format(_("Will plan"), page.subject) if page.subject else _("Will plan")
        page.items.append(Item("text", QRectF(PAGE_W * 0.3, 1, PAGE_W * 0.7, 12),
                               font=small, colour=GREY, text=subject,
                               align=int(Qt.AlignmentFlag.AlignRight)))
        page.items.append(Item("line", QRectF(0, logo_h + 6, PAGE_W, 0), colour=RULE,
                               width=0.5))


def page_texts(page):
    """Every text drawn on ``page``, in drawing order (tests; the time line's
    own texts included)."""
    out = []
    for item in page.items:
        if item.kind == "text" and item.text:
            out.append(item.text)
        elif item.kind == "timeline":
            out += fv.scene_texts(item.scene)
    return out


# ---------------------------------------------------------------- painting

def load_logo(read_file):
    """The logo image read with the plugin's ``read_file``, or ``None``.

    ``None`` (file missing, unreadable, not an image) makes the painter
    draw the vector logo instead (:func:`paint_vector_logo`): the report
    never fails because of the logo.
    """
    try:
        data = read_file(LOGO_FILE)
        image = QImage.fromData(bytes(data))
    except Exception:
        return None
    return None if image.isNull() else image


def paint_vector_logo(painter, rect):
    """The àncòra logo drawn with lines, from ``assets/brand/svg/ancora-lockup.svg``.

    The fallback when the PNG cannot be read (#163): the anchor with the
    geometry of the SVG (the mark is 120 x 140 units, drawn 130 units high
    as in the lockup) in the logo's own colours, then the word "àncòra" in
    Electrum's bold font, in ink (the brand font is not installed here).
    """
    h = rect.height()
    k = h / 140.0
    x0, y0 = rect.x(), rect.y()

    def pt(x, y):
        return QPointF(x0 + x * k, y0 + y * k)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor(INK), 9 * k)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(pt(60, 28), pt(60, 120))                 # shank
    painter.drawLine(pt(22, 56), pt(98, 56))                  # stock
    # The arm: the lower half of a circle of radius 46 around (60, 84).
    painter.drawArc(QRectF(x0 + 14 * k, y0 + 38 * k, 92 * k, 92 * k), 180 * 16, 180 * 16)
    pen.setColor(QColor(WATER))
    painter.setPen(pen)
    painter.drawEllipse(pt(60, 18), 10 * k, 10 * k)          # ring
    painter.drawLine(pt(14, 84), pt(26, 72))                  # the two tips
    painter.drawLine(pt(106, 84), pt(94, 72))
    font = report_font(max(1, round(0.8 * h)), bold=True)
    painter.setFont(font)
    painter.setPen(QColor(INK))
    painter.drawText(QPointF(x0 + 151.4 / 130.0 * h, y0 + 0.94 * h), "àncòra")
    painter.restore()


def paint_page(painter, page, logo=None):
    """Draw ``page`` with ``painter`` (already scaled to page points)."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for item in page.items:
        if item.kind == "text":
            painter.setFont(item.font)
            painter.setPen(QColor(item.colour))
            flags = _WRAP | int(item.align or Qt.AlignmentFlag.AlignLeft)
            painter.drawText(item.rect, flags, item.text)
        elif item.kind == "rect":
            painter.setPen(QPen(QColor(item.colour), 0.6) if item.colour else Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(item.fill)) if item.fill else Qt.BrushStyle.NoBrush)
            if item.radius:
                painter.drawRoundedRect(item.rect, item.radius, item.radius)
            else:
                painter.drawRect(item.rect)
        elif item.kind == "line":
            painter.setPen(QPen(QColor(item.colour), item.width))
            painter.drawLine(item.rect.topLeft(), item.rect.topRight())
        elif item.kind == "logo":
            if logo is not None:
                painter.drawImage(item.rect, logo)
            else:
                paint_vector_logo(painter, item.rect)
        elif item.kind == "timeline":
            fv.paint_future(painter, item.rect, item.scene, item.base_font)
        elif item.kind == "qr":
            paint_qr(painter, item.rect, item.text)


def paint_qr(painter, rect, text):
    """The QR code of ``text`` in the square ``rect`` (#145): one black
    square per dark module, drawn as vectors (sharp on any printer), with
    no antialiasing so two modules never leave a hairline between them.
    Nothing is drawn when the code cannot be made (see :func:`qr_modules`).
    """
    modules = qr_modules(text)
    if not modules:
        return
    size = rect.width() / len(modules)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#000000")))
    for r, row in enumerate(modules):
        for c, dark in enumerate(row):
            if dark:
                painter.drawRect(QRectF(rect.x() + c * size, rect.y() + r * size,
                                        size, size))
    painter.restore()


def print_pages(printer, pages, logo=None):
    """Draw every page on its own sheet of ``printer`` (PDF or paper).

    The page space is scaled to the printable area: exactly 1 point per
    point on A4 with 15 mm margins; smaller (never larger) on a smaller
    area (US Letter, wide hardware margins), so a page never breaks.
    """
    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("the printer could not be opened")
    try:
        area = printer.pageLayout().paintRectPixels(printer.resolution())
        scale = min(area.width() / PAGE_W, area.height() / PAGE_H)
        for index, page in enumerate(pages):
            if index:
                printer.newPage()
            painter.save()
            painter.scale(scale, scale)
            paint_page(painter, page, logo)
            painter.restore()
    finally:
        painter.end()


def page_image(page, logo=None, dpi=150):
    """``page`` as a white A4 image at ``dpi`` (previews and tests)."""
    px = dpi / 25.4
    image = QImage(int(round(210 * px)), int(round(297 * px)), QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    try:
        painter.translate(MARGIN_MM * px, MARGIN_MM * px)
        painter.scale(dpi / 72.0, dpi / 72.0)
        paint_page(painter, page, logo)
    finally:
        painter.end()
    return image
