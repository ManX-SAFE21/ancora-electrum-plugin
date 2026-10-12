"""The "Future" view of the Will tab: the will on a timeline (CHANGELOG #131).

Above the will list (#129) a small selector "List | Future" switches
between the list by versions and this view. The view draws, with
``QPainter``, what :func:`bal.core.future.build_future` computed: today,
the Check Alive (when the will has one), the delivery to the heirs in the
#124 colour with the time left, the will-executors as small squares, and
the sentence "If the will is no longer checked or renewed, on <date> the
bitcoin will go to: <heirs>".

Three layers, so the PDF report (#132) can draw the same picture:

* :func:`future_scene`: the core data turned into plain texts and colours
  (dates in local time, as the version rows of #129); no widget.
* :func:`paint_future`: draws a scene with any ``QPainter`` in a
  rectangle (a widget, a ``QPicture``, a printer page) and returns where
  it stopped and the tooltip areas.
* :class:`FutureView`: the widget of the Will tab; :class:`WillViewSwitch`:
  the selector.

Display only: nothing here builds, signs, sends or changes a date.
"""

from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
    QPicture,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QWidget,
)

from ...core import future as core_future
from ...core.messages import months_text
from ...core.report import _EXECUTOR_TEXTS
from ...core.summary import busy_summary
from ...i18n import _, number_separators
from .common import _logger
from .widgets import DELIVERY_LEVEL_COLOURS

# Config values of the selector (``BalPlugin.WILL_VIEW``).
VIEW_LIST = "list"
VIEW_FUTURE = "future"

# Colours: explicit on text and background (like the will summary), so the
# view stays readable with Electrum's dark theme too.
_BG = "#fbfbfb"
_BORDER = "#d0d0d0"
_TEXT = "#111111"
_GREY = "#555555"
_AXIS = "#9e9e9e"
_TODAY = "#1565c0"
_DRAFT = "#8a8a8a"
_TITLE = {"ok": "#2e7d32", "bad": "#c62828", "info": "#555555"}
# Will-executor squares (owner's plan: green = received and verified, grey
# = not there yet; red when the summary says it did not receive it).
_EXECUTOR_COLOURS = {"verified": "#2e7d32", "failed": "#c62828"}
_EXECUTOR_OTHER = "#b0b0b0"

_MARGIN = 16.0
_SQUARE = 14.0
_GAP = 5.0
_FLAG = 12.0


# ------------------------------------------------------------------ scene

def utc_date_text(ts):
    """``"27/08/2027 22:00 UTC"``: the UTC day and time of ``ts`` (#131).

    Owner's choice after the first preview: the Future view shows the UTC
    day, like the will summary above it (the local time could read one day
    later than the summary for a delivery at 22:00 UTC). Day/month/year with
    four digits, as ``widgets._utc_dmy``; plain arithmetic from 1970, not
    ``datetime.fromtimestamp``, which fails after 2038 where ``time_t`` is
    32-bit. "UTC" is not translated (a standard abbreviation).
    """
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(ts))
    return moment.strftime("%d/%m/%Y %H:%M") + " UTC"


def _year_start(year):
    """1 January ``year`` 00:00 UTC as a UNIX time."""
    return int((datetime(year, 1, 1, tzinfo=timezone.utc)
                - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds())


def _year_of(ts):
    """The UTC year of ``ts`` (2038-safe, see :func:`utc_date_text`)."""
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(ts))).year


#: Steps between two labelled years, tried in this order (owner: every 2,
#: 5 or 10 years when the span is long).
YEAR_STEPS = (1, 2, 5, 10, 20, 50, 100)


def year_ticks(start, end, x0, x1, label_width):
    """The year axis: ``(step, [(year, x, labelled)])``.

    One tick on 1 January (UTC) of every year inside ``[start, end]``, at
    its true place on the same scale as the markers (``x0`` = ``start``,
    ``x1`` = ``end``). Only the years that are a multiple of ``step`` get
    a label; ``step`` is the first of :data:`YEAR_STEPS` whose labels are
    at least ``label_width`` apart, so they never overlap at any width.
    """
    if end <= start or x1 <= x0:
        return 1, []
    per_year = (x1 - x0) * (365.2425 * 86400) / float(end - start)
    step = YEAR_STEPS[-1]
    for candidate in YEAR_STEPS:
        if per_year * candidate >= label_width:
            step = candidate
            break
    ticks = []
    for year in range(_year_of(start), _year_of(end) + 1):
        t = _year_start(year)
        if t < start or t > end:
            continue
        x = x0 + (x1 - x0) * (t - start) / float(end - start)
        ticks.append((year, x, year % step == 0))
    return step, ticks


def place_labels(labels, left, right, gap=10.0):
    """Put labels on rows so that none overlaps another (#131).

    ``labels``: ``[(x, width, align)]`` with ``align`` ``"center"`` (on
    ``x``) or ``"left"`` (starting at ``x``). Each is kept inside
    ``[left, right]`` and goes on the first row (0 = nearest the axis)
    where it does not touch a label already placed. Returns
    ``[(row, left_x)]`` in the same order.
    """
    rows = []
    out = []
    for x, width, align in labels:
        lx = x if align == "left" else x - width / 2.0
        lx = max(left, min(lx, right - width))
        rx = lx + width
        row = 0
        while row < len(rows) and any(
            not (rx + gap <= a or lx >= b + gap) for a, b in rows[row]
        ):
            row += 1
        if row == len(rows):
            rows.append([])
        rows[row].append((lx, rx))
        out.append((row, lx))
    return out


def _percent_text(pct):
    """``"60%"`` / ``"45,5%"``: one decimal at most, the language's separator.

    Real shares are rarely round (the will-executor's fee is paid first);
    one decimal is enough to read them, and "60.0" is shown as "60".
    """
    text = "{:.1f}".format(pct).rstrip("0").rstrip(".")
    return text.replace(".", number_separators()[0]) + "%"


def _heirs_text(heirs):
    """``"alice 60%, bob 40%"`` (heirs below the dust limit left out)."""
    parts = [
        "{} {}".format(h.name, _percent_text(h.percent))
        for h in heirs if h.percent is not None
    ]
    return ", ".join(parts)


def _heirs_tip(deliveries, format_amount):
    """One line per heir with the amount the transaction pays."""
    lines = []
    for inst in deliveries:
        for h in inst.heirs:
            if h.sats is not None:
                lines.append("{}: {}".format(h.name, format_amount(h.sats)))
    return "\n".join(lines)


def executor_state_text(state):
    """Where a will-executor stands, in words (the report's texts).

    The msgid is looked up through a variable on purpose: written as
    ``_(table["verified"])`` babel would extract the key "verified" as a
    msgid of its own.
    """
    text = _EXECUTOR_TEXTS.get(state, state)
    return _(text)


def _executor_legend(executors):
    """``[(colour, text)]``: one entry per square colour present in the will.

    Green "received and verified", red "not received", grey the other
    states present ("received, to be verified", "to send", "to sign"),
    joined: the texts of the report and the Building Will dialog.
    """
    present = []
    for mark in executors:
        if mark.state not in present:
            present.append(mark.state)
    out = []
    if "verified" in present:
        out.append((_EXECUTOR_COLOURS["verified"], executor_state_text("verified")))
    grey = [
        executor_state_text(state)
        for state in ("to_verify", "to_send", "to_sign") if state in present
    ]
    if grey:
        out.append((_EXECUTOR_OTHER, " / ".join(grey)))
    if "failed" in present:
        out.append((_EXECUTOR_COLOURS["failed"], executor_state_text("failed")))
    return out


def _sats(sats):
    """The plain amount when no wallet formatter is given."""
    return "{} sat".format(sats)


def future_scene(model, format_date=utc_date_text, format_amount=_sats):
    """Turn a :class:`bal.core.future.Future` into the texts and colours to draw.

    ``format_date(ts)``: a date as the Will tab shows it (local time,
    four-digit year, #129); ``format_amount(sats)``: an amount in the
    wallet's unit (tooltip only). Returns a plain dict (no Qt object):
    ``title`` / ``title_kind``, ``today`` (date and "(today)"),
    ``marker`` (the delivery or the draft on the axis, or None),
    ``check_alive`` (or None), ``executors`` (squares: colour, tooltip),
    ``lines`` (the legend under the axis: ``(colour or None, text,
    tooltip, kind)``; the squares follow the line of kind
    ``"executors"``), ``executor_legend`` (``(colour, text)``),
    ``executor_explain``, ``sentence`` / ``sentence_tip``, ``more`` (later
    instalments), ``note``.
    """
    scene = {
        "title": model.title,
        "title_kind": "info",
        "today": {"date": format_date(model.now), "label": _("(today)")},
        "marker": None,
        "check_alive": None,
        "executors": [],
        "lines": [],
        "executor_legend": [],
        "executor_explain": "",
        "sentence": "",
        "sentence_tip": "",
        "more": [],
        "note": model.note or "",
        "instalments": [],
        "start": model.now,
        "end": model.now,
    }
    if model.state == core_future.STATE_IN_FORCE:
        colour = DELIVERY_LEVEL_COLOURS.get(model.level, _TEXT)
        scene["title_kind"] = "bad" if model.level == "passed" else "ok"
        first = model.delivery
        scene["marker"] = {
            "at": first, "date": format_date(first), "left": model.time_left,
            "colour": colour, "draft": False, "tip": model.level_tip,
        }
        scene["end"] = max(first, model.now)
        scene["lines"].append((
            colour, "{}: {} {}".format(
                _("Delivery to the heirs"), format_date(first), model.time_left),
            model.level_tip, "",
        ))
        if model.check_alive is not None:
            check_colour = "#c62828" if model.check_alive_passed else _TEXT
            scene["check_alive"] = {
                "at": model.check_alive, "date": format_date(model.check_alive),
                "colour": check_colour,
            }
            text = "{}: {}".format(_("Check Alive"), format_date(model.check_alive))
            if not model.check_alive_passed:
                text += " — " + _("from that day the wallet asks you to renew it")
            scene["lines"].append((check_colour, text, "", ""))
            scene["end"] = max(scene["end"], model.check_alive)
        if model.period_months:
            scene["lines"].append((
                None, "{}: {}".format(
                    _("Period (renewable)"), months_text(model.period_months)), "", ""))
        for mark in model.executors:
            state_text = executor_state_text(mark.state)
            scene["executors"].append({
                "colour": _EXECUTOR_COLOURS.get(mark.state, _EXECUTOR_OTHER),
                "tip": "{}\n{}".format(mark.url, state_text),
            })
        # The count ("3 of 3 received and verified it", the summary's own
        # texts), then the squares, the colour legend and one line saying
        # what a will-executor is (owner, #131).
        scene["lines"].append((None, "{}: {}".format(
            _("Will-Executor"), model.executors_text), "", "executors"))
        scene["executor_legend"] = _executor_legend(model.executors)
        if model.executors:
            scene["executor_explain"] = _(
                "Will-executors keep a copy of your will and broadcast it on "
                "the delivery date."
            )
        heirs = _heirs_text(model.deliveries[0].heirs)
        if heirs:
            scene["sentence"] = _(
                "If the will is no longer checked or renewed, on {} the "
                "bitcoin will go to: {}"
            ).format(format_date(first), heirs)
            scene["sentence_tip"] = _heirs_tip(model.deliveries, format_amount)
        # Multiverse (hidden option): the later instalments as smaller
        # markers on the same time scale, and as lines (date: heirs).
        for inst in model.deliveries[1:]:
            scene["instalments"].append(
                {"at": inst.locktime, "date": format_date(inst.locktime)})
            scene["end"] = max(scene["end"], inst.locktime)
            text = _heirs_text(inst.heirs)
            if text:
                scene["more"].append("{}: {}".format(format_date(inst.locktime), text))
    elif model.state == core_future.STATE_DRAFT_ONLY:
        scene["title_kind"] = "bad"
        if model.draft_locktime:
            scene["marker"] = {
                "at": model.draft_locktime, "date": format_date(model.draft_locktime),
                "left": _("Draft"), "colour": _DRAFT, "draft": True, "tip": "",
            }
            scene["end"] = max(model.draft_locktime, model.now)
    elif model.state == core_future.STATE_NONE:
        scene["title_kind"] = "bad"
    return scene


def neutral_scene(now, format_date=utc_date_text, busy=False):
    """Today only, with no claim about the will.

    Shown when the data could not be read (the error is logged) and, with
    ``busy``, while a working dialog is on screen (the summary's sentence:
    half-way states would only confuse, #84a).
    """
    model = core_future.Future(state="neutral", now=now)
    if busy:
        model.title = busy_summary()["title"]
    return future_scene(model, format_date)


def scene_texts(scene):
    """Every text the scene draws, in drawing order (for tests and #132)."""
    out = [scene["title"], scene["today"]["date"], scene["today"]["label"]]
    if scene["check_alive"]:
        out.append(scene["check_alive"]["date"])
    if scene["marker"]:
        out += [scene["marker"]["date"], scene["marker"]["left"]]
    out += [inst["date"] for inst in scene["instalments"]]
    for _colour, text, _tip, kind in scene["lines"]:
        out.append(text)
        if kind == "executors":
            out += [t for _c, t in scene["executor_legend"]]
            out.append(scene["executor_explain"])
    out += [scene["sentence"]] + scene["more"] + [scene["note"]]
    return [t for t in out if t]


# ---------------------------------------------------------------- drawing

def _font(base, bold=False, delta=0.0, italic=False):
    """A copy of ``base`` with weight, size and style changed."""
    font = QFont(base)
    font.setBold(bold)
    font.setItalic(italic)
    if delta:
        size = font.pointSizeF()
        if size > 0:
            font.setPointSizeF(size + delta)
    return font


def _text(painter, x, y, w, text, colour, font, align=Qt.AlignmentFlag.AlignLeft):
    """Draw ``text`` wrapped in a box of width ``w`` at ``(x, y)``; return its height."""
    painter.setFont(font)
    painter.setPen(QColor(colour))
    flags = align | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
    box = QFontMetricsF(font).boundingRect(QRectF(x, y, w, 10000), int(flags), text)
    painter.drawText(QRectF(x, y, w, box.height()), int(flags), text)
    return box.height()


def _x_of(t, scene, x0, x1):
    """The x of time ``t`` on the axis from today (x0) to the end (x1)."""
    span = float(scene["end"] - scene["start"])
    if span <= 0:
        return x1
    ratio = (float(t) - float(scene["start"])) / span
    return x0 + (x1 - x0) * min(max(ratio, 0.0), 1.0)


def square_rects(count, x, y, width):
    """The boxes of ``count`` executor squares, in rows inside ``width``.

    As many squares per row as fit (at least one), then the next row: 30
    will-executors (owner, #131) never run past the edge nor over the texts
    below, which start under the last row.
    """
    step = _SQUARE + _GAP
    per_row = max(int((width + _GAP) // step), 1)
    return [
        QRectF(x + (i % per_row) * step, y + (i // per_row) * step, _SQUARE, _SQUARE)
        for i in range(count)
    ]


def _paint_executors(painter, x, y, w, scene, font, hits):
    """The executor squares (wrapped rows), their colour legend and the
    one-line explanation; returns the y under them."""
    squares = scene["executors"]
    boxes = square_rects(len(squares), x, y, w)
    for sq, box in zip(squares, boxes, strict=True):
        painter.setPen(QPen(QColor("#666666"), 1))
        painter.setBrush(QBrush(QColor(sq["colour"])))
        painter.drawRect(box)
        hits.append((box, sq["tip"]))
    if boxes:
        y = max(b.bottom() for b in boxes) + _GAP + 2
    # The legend: a small square and its text, wrapped like words.
    metrics = QFontMetricsF(font)
    line = metrics.height()
    side = _SQUARE * 0.8
    lx = x
    for colour, text in scene["executor_legend"]:
        width = side + 6 + metrics.horizontalAdvance(text) + 14
        if lx > x and lx + width > x + w:
            lx, y = x, y + line + 2
        painter.setPen(QPen(QColor("#666666"), 1))
        painter.setBrush(QBrush(QColor(colour)))
        painter.drawRect(QRectF(lx, y + (line - side) / 2, side, side))
        _text(painter, lx + side + 6, y, w - (lx + side + 6 - x), text, _GREY, font)
        lx += width
    if scene["executor_legend"]:
        y += line + 2
    if scene["executor_explain"]:
        y += _text(painter, x, y, w, scene["executor_explain"], _GREY,
                   _font(font, italic=True)) + 2
    return y + 2


def paint_future(painter, rect, scene, base_font=None):
    """Draw ``scene`` (:func:`future_scene`) in ``rect`` with ``painter``.

    Any ``QPainter`` works (a widget, a ``QPicture``, a ``QPrinter`` page:
    #132 can call it for the PDF report). Returns ``(bottom, hits)``: the
    y where the drawing ends and ``[(QRectF, tooltip)]`` of the parts that
    explain themselves on mouse over (executor squares, the delivery, the
    sentence).
    """
    base = QFont(base_font) if base_font is not None else QFont(QApplication.font())
    normal, small = base, _font(base, delta=-1)
    bold = _font(base, bold=True)
    hits = []
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    x, y, w = rect.x() + _MARGIN, rect.y() + _MARGIN, rect.width() - 2 * _MARGIN
    line = QFontMetricsF(normal).height()

    title_colour = _TITLE.get(scene["title_kind"], _GREY)
    if scene["title"]:
        y += _text(painter, x, y, w, scene["title"], title_colour,
                   _font(base, bold=True, delta=2)) + line * 0.6

    # The time line (#131, owner's second preview): a ruler of the YEARS on
    # top, then the dates above the axis, the axis with its markers, and
    # today / the time left under it. Every x comes from the same scale
    # (_x_of): today on the left, the last date on the right.
    x0, x1 = x + 8.0, x + w - 8.0
    small_m, bold_m = QFontMetricsF(small), QFontMetricsF(bold)
    small_line = small_m.height()
    marker = scene["marker"]
    check = scene["check_alive"]

    # The labels above the axis (dates), placed on rows so none overlaps.
    above = []          # (x, width, align, text, colour, font, kind)
    if marker:
        mx = _x_of(marker["at"], scene, x0, x1)
        above.append((mx, bold_m.horizontalAdvance(marker["date"]) + 2, "center",
                      marker["date"], marker["colour"], bold, "marker"))
    for inst in scene["instalments"]:
        ix = _x_of(inst["at"], scene, x0, x1)
        above.append((ix, small_m.horizontalAdvance(inst["date"]) + 2, "center",
                      inst["date"], _GREY, small, "instalment"))
    if check:
        cx = _x_of(check["at"], scene, x0, x1)
        # The flag points right: its date starts after the flag.
        above.append((cx + _FLAG + 3, small_m.horizontalAdvance(check["date"]) + 2, "left",
                      check["date"], check["colour"], small, "check"))
    placed = place_labels([(lx, lw, al) for lx, lw, al, *_r in above], x, x + w)
    rows_above = max([row for row, _lx in placed], default=-1) + 1
    row_h = bold_m.height() + 4

    # The year ruler.
    label_w = small_m.horizontalAdvance("2088") + 14
    ruler_y = y + small_line + 2
    _step, ticks = year_ticks(scene["start"], scene["end"], x0, x1, label_w)
    axis_y = ruler_y + 10 + rows_above * row_h + 18
    if ticks:
        painter.setPen(QPen(QColor(_AXIS), 1))
        painter.drawLine(QPointF(x0, ruler_y), QPointF(x1, ruler_y))
        for year, tx, labelled in ticks:
            painter.setPen(QPen(QColor(_AXIS), 1))
            painter.drawLine(QPointF(tx, ruler_y), QPointF(tx, ruler_y + (6 if labelled else 3)))
            if labelled:
                # A faint line down to the time line: the year where it is.
                grid = QPen(QColor("#e0e0e0"), 1)
                grid.setStyle(Qt.PenStyle.DotLine)
                painter.setPen(grid)
                painter.drawLine(QPointF(tx, ruler_y + 6), QPointF(tx, axis_y))
                text = str(year)
                tw = small_m.horizontalAdvance(text)
                lx = max(x, min(tx - tw / 2.0, x + w - tw))
                _text(painter, lx, y, tw + 2, text, _GREY, small)

    painter.setPen(QPen(QColor(_AXIS), 2))
    painter.drawLine(QPointF(x0, axis_y), QPointF(x1, axis_y))

    # The dates above the axis, each on its row (row 0 nearest the axis).
    for (lx0, lw, _al, text, colour, font, kind), (row, lx) in zip(above, placed, strict=True):
        ty = axis_y - 14 - (row + 1) * row_h + 4
        if kind == "check":
            # The flag pole reaches up to its date.
            pole = lx0 - _FLAG - 3
            painter.setPen(QPen(QColor(colour), 2))
            painter.drawLine(QPointF(pole, axis_y), QPointF(pole, ty + 2))
            painter.setBrush(QBrush(QColor(colour)))
            painter.drawPolygon(QPolygonF([
                QPointF(pole, ty + 2), QPointF(pole + _FLAG, ty + 7),
                QPointF(pole, ty + 12)]))
        _text(painter, lx, ty, lw, text, colour, font)
        if kind == "marker" and marker["tip"]:
            hits.append((QRectF(lx, ty, lw, axis_y - ty), marker["tip"]))

    # Later instalments: smaller dots.
    for inst in scene["instalments"]:
        ix = _x_of(inst["at"], scene, x0, x1)
        painter.setPen(QPen(QColor(_GREY), 2))
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawEllipse(QPointF(ix, axis_y), 5, 5)

    # Today.
    painter.setPen(QPen(QColor(_TODAY), 2))
    painter.setBrush(QBrush(QColor(_TODAY)))
    painter.drawEllipse(QPointF(x0, axis_y), 5, 5)

    # The delivery (or the draft).
    below_labels = [(x0 - 6, small_m.horizontalAdvance(scene["today"]["date"]) + 2, "left"),
                    (x0 - 6, small_m.horizontalAdvance(scene["today"]["label"]) + 2, "left")]
    if marker:
        mx = _x_of(marker["at"], scene, x0, x1)
        colour = QColor(marker["colour"])
        pen = QPen(colour, 2)
        if marker["draft"]:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(_BG) if marker["draft"] else colour))
        painter.drawEllipse(QPointF(mx, axis_y), 8, 8)
        below_labels.append((mx, small_m.horizontalAdvance(marker["left"]) + 2, "center"))
    under = place_labels(below_labels, x, x + w)
    texts_below = [(scene["today"]["date"], _TODAY), (scene["today"]["label"], _TODAY)]
    if marker:
        texts_below.append((marker["left"], marker["colour"]))
    below = axis_y + 9
    for (text, colour), (row, lx), (_bx, bw, _al) in zip(texts_below, under, below_labels,
                                                          strict=True):
        ty = axis_y + 9 + row * (small_line + 1)
        _text(painter, lx, ty, bw, text, colour, small)
        below = max(below, ty + small_line)

    # The legend under the axis.
    y = below + line * 0.8
    for colour, text, tip, kind in scene["lines"]:
        tx = x
        if colour:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(colour)))
            painter.drawEllipse(QPointF(x + 5, y + line / 2), 4, 4)
            tx = x + 16
        height = _text(painter, tx, y, w - (tx - x), text, colour or _TEXT, normal)
        if tip:
            hits.append((QRectF(tx, y, w - (tx - x), height), tip))
        y += height + 3
        if kind == "executors":
            y = _paint_executors(painter, x + 16, y + 2, w - 16, scene, small, hits)
    if scene["sentence"]:
        y += line * 0.5
        height = _text(painter, x, y, w, scene["sentence"], _TEXT, bold)
        if scene["sentence_tip"]:
            hits.append((QRectF(x, y, w, height), scene["sentence_tip"]))
        y += height + 3
    for text in scene["more"]:
        y += _text(painter, x + 16, y, w - 16, text, _TEXT, normal) + 3
    if scene["note"]:
        y += line * 0.5
        y += _text(painter, x, y, w, scene["note"], "#333333",
                   _font(base, italic=True)) + 3
    painter.restore()
    return y + _MARGIN, hits


def scene_height(scene, width, base_font=None):
    """The height :func:`paint_future` needs at ``width`` (drawn on a QPicture)."""
    picture = QPicture()
    painter = QPainter(picture)
    try:
        bottom, _hits = paint_future(painter, QRectF(0, 0, width, 10000), scene, base_font)
    finally:
        painter.end()
    return bottom


# ----------------------------------------------------------------- widgets

class FutureView(QWidget):
    """The Future view of the Will tab (see the module docstring).

    :meth:`refresh` is called with every refresh of the will summary
    (``BalWindow.update_will_summary``): it only marks the data stale while
    the view is hidden, and recomputes it when shown, so the List mode pays
    nothing. Never raises into the GUI: an error is logged and the view
    shows today only.
    """

    def __init__(self, bal_window, parent=None):
        """``bal_window``: the ``BalWindow`` whose will is drawn."""
        super().__init__(parent)
        self.bal_window = bal_window
        self.model = None
        self.scene = None
        self._hits = []
        self._stale = True
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def _format_amount(self, sats):
        """An amount in the wallet's unit, as Electrum shows it."""
        try:
            return self.bal_window.window.format_amount_and_units(sats)
        except Exception:
            return _sats(sats)

    def _now(self):
        """The current time (the core's clock, patched by the tests)."""
        return core_future._now(None)

    def compute(self):
        """Build the model and the scene from the window's will, now.

        Busy (a working dialog on screen): the summary's "operation in
        progress". Any error: logged, today only.
        """
        bw = self.bal_window
        now = self._now()
        if getattr(bw, "_will_summary_busy", 0) > 0:
            self.model = None
            self.scene = neutral_scene(now, busy=True)
            return
        try:
            plugin = bw.bal_plugin
            warn = getattr(plugin, "warning_months", lambda: None)()
            self.model = core_future.build_future(
                bw.willitems, bw.will_settings, plugin.is_basic_mode(), now=now,
                warn_months=warn, wallet=getattr(bw, "wallet", None),
            )
            self.scene = future_scene(self.model, format_amount=self._format_amount)
        except Exception as e:
            _logger.warning(f"future view failed: {e!r}")
            self.model = None
            self.scene = neutral_scene(now)

    def refresh(self):
        """The will or its settings may have changed: redraw when visible."""
        self._stale = True
        if self.isVisible():
            self._redraw()

    def _redraw(self):
        """Recompute, resize to the drawing and repaint."""
        self.compute()
        self._stale = False
        self._fit()
        self.update()

    def _fit(self):
        """Ask for the height the drawing needs at the current width."""
        if self.scene is None:
            return
        try:
            height = scene_height(self.scene, max(self.width(), 400), self.font())
        except Exception as e:
            _logger.warning(f"future view size failed: {e!r}")
            return
        self.setMinimumHeight(int(height) + 1)

    def texts(self):
        """The texts the view draws (tests; the same list #132 can print)."""
        if self.scene is None or self._stale:
            self.compute()
            self._stale = False
        return scene_texts(self.scene)

    def showEvent(self, event):  # noqa: N802 - Qt's name
        """Shown (List -> Future, or the tab opened): redraw if stale."""
        super().showEvent(event)
        if self._stale or self.scene is None:
            self._redraw()

    def resizeEvent(self, event):  # noqa: N802 - Qt's name
        """A new width can wrap the sentence on more lines."""
        super().resizeEvent(event)
        self._fit()

    def paintEvent(self, event):  # noqa: N802 - Qt's name
        """Draw the scene on a light panel (never raises)."""
        painter = QPainter(self)
        try:
            rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
            painter.setPen(QPen(QColor(_BORDER), 1))
            painter.setBrush(QBrush(QColor(_BG)))
            painter.drawRoundedRect(rect, 6, 6)
            if self.scene is None:
                return
            _bottom, self._hits = paint_future(painter, rect, self.scene, self.font())
        except Exception as e:
            _logger.warning(f"future view paint failed: {e!r}")
        finally:
            painter.end()

    def event(self, event):
        """Tooltips of the executor squares, the delivery and the heirs."""
        if event.type() == event.Type.ToolTip:
            pos = QPointF(event.pos())
            for box, tip in self._hits:
                if box.contains(pos):
                    QToolTip.showText(event.globalPos(), tip, self)
                    return True
            QToolTip.hideText()
            event.ignore()
            return True
        return super().event(event)

    def tooltip_at(self, point):
        """The tooltip at ``point`` (a ``QPointF``), or ``None`` (tests)."""
        for box, tip in self._hits:
            if box.contains(point):
                return tip
        return None


class WillViewSwitch(QWidget):
    """The "List | Future" selector above the will list (CHANGELOG #131).

    Two checkable buttons, one pressed. The choice is stored in Electrum's
    config (``bal_will_view``, ``BalPlugin.WILL_VIEW``): plugin-wide like
    the other ``bal_*`` view settings, never in a wallet (the view is a
    preference of the person, not data of the will). ``list_widget`` and
    ``future_widget`` are shown and hidden; ``future_view.refresh`` is
    called when the Future view appears.
    """

    def __init__(self, config_var, list_widget, future_widget, future_view, parent=None):
        """``config_var``: a ``BalConfig``; the widgets to switch."""
        super().__init__(parent)
        self.config_var = config_var
        self.list_widget = list_widget
        self.future_widget = future_widget
        self.future_view = future_view
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for view, text in ((VIEW_LIST, _("List")), (VIEW_FUTURE, _("Future"))):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setMinimumWidth(90)
            self.group.addButton(button)
            box.addWidget(button)
            self.buttons[view] = button
            button.clicked.connect(lambda _checked=False, v=view: self.choose(v))
        box.addStretch(1)
        self.apply(self.current())

    def current(self):
        """The stored view; anything unknown reads as the list."""
        try:
            value = self.config_var.get()
        except Exception:
            value = None
        return VIEW_FUTURE if value == VIEW_FUTURE else VIEW_LIST

    def choose(self, view):
        """The owner pressed a button: remember it and switch."""
        try:
            self.config_var.set(view)
        except Exception as e:
            _logger.warning(f"could not store the will view: {e!r}")
        self.apply(view)

    def apply(self, view):
        """Show ``view`` (``"list"`` or ``"future"``) and press its button."""
        future = view == VIEW_FUTURE
        self.buttons[VIEW_FUTURE if future else VIEW_LIST].setChecked(True)
        self.list_widget.setVisible(not future)
        self.future_widget.setVisible(future)
        if future:
            self.future_view.refresh()


def make_future_area(future_view):
    """The Future view in a scroll area (a long sentence never forces the
    Electrum window to grow)."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(future_view)
    return area
