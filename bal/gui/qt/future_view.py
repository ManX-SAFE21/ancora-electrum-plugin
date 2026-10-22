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

from ...core import datetext
from ...core import future as core_future
from ...core.report import _EXECUTOR_TEXTS
from ...core.summary import busy_summary, selected_urls
from ...core.willexecutors import Willexecutors
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
# The Check Alive flag, its pole and its date (owner, #132): orange-red, so
# it stands out from the black texts; red once the date has passed.
CHECK_ALIVE_COLOUR = "#D85A30"
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
        for state in ("to_verify", "excluded", "to_send", "to_sign", "deselected")
        if state in present
    ]
    if grey:
        out.append((_EXECUTOR_OTHER, " / ".join(grey)))
    if "failed" in present:
        out.append((_EXECUTOR_COLOURS["failed"], executor_state_text("failed")))
    return out


def _sats(sats):
    """The plain amount when no wallet formatter is given."""
    return "{} sat".format(sats)


def future_scene(model, format_date=utc_date_text, format_amount=_sats,
                 format_short=None, split_date=None):
    """Turn a :class:`bal.core.future.Future` into the texts and colours to draw.

    ``format_date(ts)``: a date in the texts under the time line (the
    delivery line, the Check Alive line, the sentence); ``format_short``:
    a date on the time line itself (today, the Check Alive card, a draft's
    marker, the later instalments; default ``format_date``);
    ``split_date(ts)``: ``(day, time)`` of a delivery card (default: the
    ``format_date`` text cut at its first space, "27/02/2027" and "22:00
    UTC"). The Will tab passes the one date style of #134
    (:func:`view_scene`) and, since #145, the PDF report too.
    ``format_amount(sats)``: an amount in the wallet's unit (tooltip only). Returns a plain dict (no Qt object):
    ``title`` / ``title_kind``, ``today`` (date and "(today)"),
    ``marker`` (the delivery or the draft on the axis, or None),
    ``check_alive`` (or None), ``executors`` (squares: colour, tooltip),
    ``lines`` (the legend under the axis: ``(colour or None, text,
    tooltip, kind)``; the squares follow the line of kind
    ``"executors"``), ``executor_legend`` (``(colour, text)``),
    ``executor_explain``, ``sentence`` / ``sentence_tip``, ``more`` (later
    instalments), ``note``.

    For the time line of #132 (owner's mockup): ``deliveries`` (one card
    per delivery: ``at``, ``number`` (None for a single delivery),
    ``title``, ``time``, ``heirs``, ``next``), ``pill`` (the time left
    without brackets), ``level`` / ``level_colour`` (#124), ``legend``
    (``(kind, text)`` of the small legend), the Check Alive's ``title``,
    ``hint`` and ``passed``, and today's ``name`` ("Today").
    """
    short = format_short or format_date
    scene = {
        "title": model.title,
        "title_kind": "info",
        "today": {"date": short(model.now), "label": _("(today)"),
                  "name": _capital(_bare(_("(today)")))},
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
        "deliveries": [],
        "pill": "",
        "level": None,
        "level_colour": _TEXT,
        "legend": [],
    }
    if model.state == core_future.STATE_IN_FORCE:
        colour = DELIVERY_LEVEL_COLOURS.get(model.level, _TEXT)
        scene["title_kind"] = "bad" if model.level == "passed" else "ok"
        first = model.delivery
        scene["marker"] = {
            "at": first, "date": short(first), "left": model.time_left,
            "colour": colour, "draft": False, "tip": model.level_tip,
        }
        scene["end"] = max(first, model.now)
        scene["lines"].append((
            colour, "{}: {} {}".format(
                _("Delivery to the heirs"), format_date(first), model.time_left),
            model.level_tip, "",
        ))
        # The delivery form under the delivery (owner, #132): "Fixed date"
        # or "Period (renewable) — 24 months (2 years)".
        scene["lines"].append((None, "{}: {}".format(
            _("Delivery form"),
            core_future.delivery_form_text(model.form, model.period_months)), "", ""))
        if model.check_alive is not None:
            check_colour = "#c62828" if model.check_alive_passed else CHECK_ALIVE_COLOUR
            scene["check_alive"] = {
                "at": model.check_alive, "date": short(model.check_alive),
                "colour": check_colour, "title": _("Check Alive"),
                "passed": model.check_alive_passed,
                "hint": "" if model.check_alive_passed else _(
                    "from that day the wallet asks you to renew it"),
            }
            text = "{}: {}".format(_("Check Alive"), format_date(model.check_alive))
            if not model.check_alive_passed:
                text += " — " + _("from that day the wallet asks you to renew it")
            scene["lines"].append((check_colour, text, "", ""))
            scene["end"] = max(scene["end"], model.check_alive)
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
                {"at": inst.locktime, "date": short(inst.locktime)})
            scene["end"] = max(scene["end"], inst.locktime)
            text = _heirs_text(inst.heirs)
            if text:
                scene["more"].append("{}: {}".format(format_date(inst.locktime), text))
        _delivery_cards(scene, model, colour,
                        split_date or (lambda ts: _split_date(format_date(ts))))
    elif model.state == core_future.STATE_DRAFT_ONLY:
        scene["title_kind"] = "bad"
        if model.draft_locktime:
            scene["marker"] = {
                "at": model.draft_locktime, "date": short(model.draft_locktime),
                "left": _("Draft"), "colour": _DRAFT, "draft": True, "tip": "",
            }
            scene["end"] = max(model.draft_locktime, model.now)
    elif model.state == core_future.STATE_NONE:
        scene["title_kind"] = "bad"
    return scene


def _bare(text):
    """``"(in 4 months)"`` -> ``"in 4 months"``: the time-left texts without
    their brackets (the pill of the time line, the legend's "today")."""
    return text.strip().strip("()").strip()


def _capital(text):
    """``text`` with its first letter in upper case ("today" -> "Today")."""
    return text[:1].upper() + text[1:]


def _split_date(text):
    """``"01/03/2028 22:00 UTC"`` -> ``("01/03/2028", "22:00 UTC")``."""
    day, _sep, rest = text.partition(" ")
    return day, rest


def _delivery_cards(scene, model, colour, split_date):
    """Fill the #132 time line data of a will in force (see :func:`future_scene`).

    ``split_date(ts)``: the ``(day, time)`` of a card: the day goes in the
    title ("Delivery to the heirs · 27/02/2027"), the time under it; an
    empty day keeps the title to the name and puts the whole date under it
    (the Will tab, #134: "31 Dec 2030, 01:00 UTC").

    The user reads "Delivery 1, 2, ..." where the code says "instalment"
    (owner, #132: an inheritance in stages delivers several times; "rata"
    sounded like a loan).
    """
    scene["level"] = model.level
    scene["level_colour"] = colour
    scene["pill"] = _bare(model.time_left)
    many = len(model.deliveries) > 1
    for number, inst in enumerate(model.deliveries, 1):
        day, time = split_date(inst.locktime)
        head = _("Delivery {}").format(number) if many else _("Delivery to the heirs")
        scene["deliveries"].append({
            "at": inst.locktime, "number": number if many else None,
            # No day (the Will tab's style, #134): the title is the name
            # alone and the date line carries the whole short date, so a
            # card of the narrowest width never cuts it.
            "title": "{} · {}".format(head, day) if day else head, "time": time,
            "heirs": " · ".join(
                "{} {}".format(h.name, _percent_text(h.percent))
                for h in inst.heirs if h.percent is not None),
            "next": number == 1,
        })
    legend = [("today", _bare(_("(today)")))]
    if scene["check_alive"]:
        legend.append(("check", _("Check Alive")))
    legend.append(("next", _("next delivery")))
    if many:
        legend.append(("later", _("later deliveries")))
    if scene["pill"]:
        legend.append(("pill", _("time left")))
    scene["legend"] = legend


#: The palette of the Future view while a prepared will is not sent yet
#: (#134): the will the executors hold, drawn desaturated.
_GHOST_GREY = "#9e9e9e"
_INFO_FILL = "#EEF4FB"
_INFO_BORDER = "#C9DBF0"
_INFO_TEXT = "#24374D"


def _grey_scene(scene, model, format_date, format_short):
    """Turn an in-force scene grey while a prepared will waits (#134).

    Owner (ZIP test): with a will prepared and not sent, the summary above
    says "Will prepared: it must be signed and sent" while the Future view
    looked like a normal green "in force" view. Now: the title "Current
    situation at the will-executors" (grey), an info box that names both
    dates, every colour of the will in force grey, and the prepared will's
    date as a dashed grey ghost on the time line, "prepared, not sent". The
    italic note says the same as the box, so it is left out.
    """
    pending = model.pending_locktime
    scene["title"] = _("Current situation at the will-executors")
    scene["title_kind"] = "info"
    scene["info"] = _(
        "This is what the will-executors hold today (delivery {held}). The "
        "prepared will (delivery {prepared}) is not signed and sent yet: when "
        "you sign and send it, this view is in colour again with the new date."
    ).format(held=format_date(model.delivery), prepared=format_date(pending))
    scene["note"] = ""
    _grey_colours(scene)
    scene["ghost"] = {"at": pending, "date": format_short(pending),
                      "left": _("prepared, not sent")}
    scene["end"] = max(scene["end"], pending)
    return scene


def _grey_colours(scene):
    """Every colour of the will shown turns grey (#134): the axis stretch,
    the next delivery, the pill, the card, the Check Alive, the executor
    squares and their legend, the coloured lines."""
    scene["level"] = "grey"
    scene["level_colour"] = _GHOST_GREY
    if scene["marker"]:
        scene["marker"]["colour"] = _GHOST_GREY
    if scene["check_alive"]:
        scene["check_alive"]["colour"] = _GHOST_GREY
    for square in scene["executors"]:
        square["colour"] = _GHOST_GREY
    scene["executor_legend"] = [(_GHOST_GREY, text)
                                for _colour, text in scene["executor_legend"]]
    scene["lines"] = [(_GHOST_GREY if colour else None, text, tip, kind)
                      for colour, text, tip, kind in scene["lines"]]


#: The opacity of a Future view whose will no will-executor received (#134).
_FADED_OPACITY = 0.48


def nothing_received(model):
    """True when no will-executor has received and verified the will the
    view shows (#134, owner): a draft only, or a will signed but not sent
    (e.g. signed offline). View-only: the #128 notion of "in force" is not
    touched.

    The executor marks of the version shown (``Future.executors``, from
    ``bal.core.summary.executor_states``, the source of the summary's "x of
    N received and verified"): not one is ``"verified"``. A will with no
    will-executor at all (the backup transaction kept by the owner) is not
    "not sent": it was never meant to be, so it keeps its colours.
    """
    if model.state == core_future.STATE_DRAFT_ONLY:
        return True
    marks = [m for m in model.executors if m.state != "deselected"]
    if model.state != core_future.STATE_IN_FORCE or not marks:
        return False
    return not any(mark.state == "verified" for mark in marks)


def _faded_scene(scene):
    """Grey, faded, with the big "NOT SENT TO THE WILL-EXECUTORS YET" over
    the time line (#134, owner): nothing happens on the date until the will
    is sent. The texts of the scene stay (the draft-only view keeps its own
    title and note)."""
    _grey_colours(scene)
    scene["title_kind"] = "info"
    scene["faded"] = True
    scene["overlay"] = (_("NOT SENT TO THE WILL-EXECUTORS YET"),
                        _("Until you send it, nothing happens on the date."))
    return scene


def view_scene(model, format_amount=_sats):
    """:func:`future_scene` in the one date style of the Will tab (#134).

    Owner (ZIP test): the Future view sits under the will summary, so it
    writes the dates the same way: "31 December 2030, 01:00 UTC" in the
    texts, the short "31 Dec 2030, 01:00 UTC" on the time line, and the
    cards "Delivery to the heirs" over "31 Dec 2030, 01:00 UTC" (owner's
    ZIP test: "· 23 set 2032" in the title was cut on a narrow card).
    """
    scene = future_scene(
        model, format_date=datetext.utc_text, format_amount=format_amount,
        format_short=datetext.utc_short,
        split_date=lambda ts: ("", datetext.utc_short(ts)),
    )
    # #134: a prepared will that is not the will in force greys the view
    # (the PDF report never does: it describes the will in force only).
    # #134: no will-executor received it: faded, with the overlay (wins
    # over the case below: what the executors hold is nothing yet).
    if nothing_received(model):
        _faded_scene(scene)
        # Signed but not sent (owner): "Your will is in force" would be
        # wrong. Signed = every executor is still "to send" (or its sending
        # failed); a draft-only view keeps its own title.
        states = {mark.state for mark in model.executors} - {"deselected"}
        if (model.state == core_future.STATE_IN_FORCE and states
                and states <= {"to_send", "failed"}):
            scene["title"] = _("Will signed, not sent yet")
        return scene
    if (model.state == core_future.STATE_IN_FORCE
            and getattr(model, "pending_locktime", None)
            and model.pending_locktime != model.delivery):
        _grey_scene(scene, model, datetext.utc_text, datetext.utc_short)
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


def view_neutral_scene(now, busy=False):
    """:func:`neutral_scene` with today in the short date style of #134."""
    model = core_future.Future(state="neutral", now=now)
    if busy:
        model.title = busy_summary()["title"]
    return view_scene(model)


def scene_texts(scene):
    """Every text the scene draws, in drawing order (for tests and #132)."""
    out = [scene["title"], scene.get("info", ""), scene["today"]["name"],
           scene["today"]["date"]]
    if scene.get("overlay"):
        out += list(scene["overlay"])
    check = scene["check_alive"]
    if check:
        out += [check["title"], check["date"], check["hint"]]
    out.append(scene["pill"])
    for card in scene["deliveries"]:
        out += [card["title"], card["time"], card["heirs"]]
    if scene["marker"] and scene["marker"]["draft"]:
        out += [scene["marker"]["date"], scene["marker"]["left"]]
    if scene.get("ghost"):
        out += [scene["ghost"]["date"], scene["ghost"]["left"]]
    out += [text for _kind, text in scene["legend"]]
    for _colour, text, _tip, kind in scene["lines"]:
        out.append(text)
        if kind == "executors":
            out += [t for _c, t in scene["executor_legend"]]
            out.append(scene["executor_explain"])
    out += [scene["sentence"]] + scene["more"] + [scene["note"]]
    return [t for t in out if t]


# ---------------------------------------------------------------- drawing

def _font(base, bold=False, delta=0.0, italic=False):
    """A copy of ``base`` with weight, size and style changed.

    ``delta`` is added to the point size, or to the pixel size when
    ``base`` is sized in pixels: the PDF report (#132) draws in a page
    space of 1 unit = 1 point with pixel-sized fonts, so the drawing is the
    same size on every printer.
    """
    font = QFont(base)
    font.setBold(bold)
    font.setItalic(italic)
    if delta:
        size = font.pointSizeF()
        if size > 0:
            font.setPointSizeF(size + delta)
        elif font.pixelSize() > 0:
            font.setPixelSize(max(1, int(round(font.pixelSize() + delta))))
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


# --------------------------------------------------------------- time line
# The time line of #132 (owner's mockup, "make it clearer, more space"):
# year bands, a thick axis with the stretch to the next delivery in the
# #124 colour and a "pill" with the time left, today, the Check Alive flag
# with its card above the axis, numbered delivery circles with their cards
# below it, and a small legend. Shared by the Future view and the PDF
# report. :func:`timeline_layout` computes every place (tests check that
# nothing overlaps), :func:`_paint_timeline` draws them.

_BAND = "#f1f3f5"
_AXIS_LINE = "#d6d6d6"
_CARD_BORDER = "#d0d0d0"
_CHECK_FILL = {False: "#fdf0ea", True: "#fdecec"}
_LATER_FILL = "#c8e6c9"
_LATER_LINE = "#81c784"
_LATER_TEXT = "#1b5e20"
#: The light background of the next delivery's card, per #124 level.
_LEVEL_FILL = {"green": "#eef7ee", "yellow": "#fff5e5", "red": "#fdecec",
               "passed": "#fdecec"}
_CARD_PAD = 6.0
_CARD_MAX = 210.0
_CARD_GAP = 8.0
_ABOVE_MIN = 16.0       # clearance between the axis and the boxes above
_BELOW_MIN = 18.0       # ... and the cards below
_R_NEXT = 10.0
_R_LATER = 8.0
_R_TODAY = 6.0
_FLAG_H = 10.0


def _wrap_height(text, font, width):
    """The height of ``text`` wrapped at ``width``."""
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap)
    return QFontMetricsF(font).boundingRect(QRectF(0, 0, width, 10000), flags, text).height()


def _card(lines, max_width=_CARD_MAX):
    """Size a card of ``lines`` = ``[(text, font, colour, wrap)]``.

    ``wrap`` lines are wrapped at the card width; the others are elided
    with "…" when too long (the full text is in the report's table and
    in the view's lines). Returns ``(width, height, [(text, font, colour,
    height)])``.
    """
    inner_max = max_width - 2 * _CARD_PAD
    natural = max([QFontMetricsF(f).horizontalAdvance(t) + 2
                   for t, f, _c, wrap in lines if t and not wrap] or [60.0])
    inner = min(inner_max, natural)
    out, height = [], 2 * _CARD_PAD
    for text, font, colour, wrap in lines:
        if not text:
            continue
        if wrap:
            h = _wrap_height(text, font, inner)
        else:
            text = QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, inner)
            h = QFontMetricsF(font).height()
        out.append((text, font, colour, h))
        height += h
    return inner + 2 * _CARD_PAD, height, out


def _place_above(placed, left, right, height):
    """The lowest offset (from the axis, upwards) where a box of ``height``
    over ``[left, right]`` touches no box already placed above the axis."""
    bottom = _ABOVE_MIN
    for lo, hi, b, h in placed:
        if not (right + 4 <= lo or left >= hi + 4):
            bottom = max(bottom, b + h + 6)
    placed.append((left, right, bottom, height))
    return bottom


def _pack_cards(widths, centres, left, right):
    """Left edges of cards in date order on one row inside ``[left, right]``,
    each as near its centre as the others allow (``None`` if they do not fit).

    Order is kept, so the straight leader lines from the circles to the
    cards never cross each other.
    """
    if sum(widths) + _CARD_GAP * (len(widths) - 1) > right - left:
        return None
    lefts = []
    edge = left
    for w, c in zip(widths, centres, strict=True):
        lx = max(min(max(c - w / 2.0, left), right - w), edge)
        lefts.append(lx)
        edge = lx + w + _CARD_GAP
    edge = right
    for i in range(len(lefts) - 1, -1, -1):
        lefts[i] = min(lefts[i], edge - widths[i])
        edge = lefts[i] - _CARD_GAP
    return lefts


def _leader(cx, r, axis_y, rect):
    """The leader line from a delivery circle to the top of its card."""
    end_x = max(rect.left() + 8, min(cx, rect.right() - 8))
    return (QPointF(cx, axis_y + r), QPointF(end_x, rect.top()))


def _arrange_cards(widths, heights, centres, radii, x, w, axis_y):
    """Place the delivery cards below the axis: ``[(QRectF, leader or None)]``.

    1. One row, in date order (:func:`_pack_cards`).
    2. Two staggered rows: the even cards (1st, 3rd, ...) near the axis,
       the odd ones on a second row; each near card stays between the
       leader lines of its neighbours, so no line crosses a card.
    3. A column, without leader lines: the numbers on the circles and in
       the titles tell which card is which.
    Order is always kept, so leader lines never cross each other.
    """
    if not widths:
        return []
    top = axis_y + _BELOW_MIN
    lefts = _pack_cards(widths, centres, x, x + w)
    if lefts is not None:
        out = []
        for lx, cw, ch, cx, r in zip(lefts, widths, heights, centres, radii, strict=True):
            rect = QRectF(lx, top, cw, ch)
            out.append((rect, _leader(cx, r, axis_y, rect)))
        return out
    near = list(range(0, len(widths), 2))
    far = list(range(1, len(widths), 2))
    near_h = max(heights[i] for i in near)
    far_top = top + near_h + _CARD_GAP + 4
    far_lefts = _pack_cards([widths[i] for i in far], [centres[i] for i in far], x, x + w)
    if far_lefts is not None:
        rects = {}
        for i, lx in zip(far, far_lefts, strict=True):
            rects[i] = QRectF(lx, far_top, widths[i], heights[i])
        # The x span each far leader takes while crossing the near row.
        spans = {}
        for i in far:
            a, b = _leader(centres[i], radii[i], axis_y, rects[i])
            spans[i] = (min(a.x(), b.x()) - 4, max(a.x(), b.x()) + 4)
        fits = True
        for i in near:
            lo = spans[i - 1][1] if i - 1 in spans else x
            hi = spans[i + 1][0] if i + 1 in spans else x + w
            if hi - lo < widths[i]:
                fits = False
                break
            lx = max(lo, min(centres[i] - widths[i] / 2.0, hi - widths[i]))
            rects[i] = QRectF(lx, top, widths[i], heights[i])
        if fits:
            return [(rects[i], _leader(centres[i], radii[i], axis_y, rects[i]))
                    for i in range(len(widths))]
    width = min(w, max(widths))
    out = []
    for ch in heights:
        out.append((QRectF(x, top, width, ch), None))
        top += ch + 6
    return out


#: The legend's pill symbol (#134): 24 x 8 px, and the room it takes
#: before its text.
_LEGEND_PILL_W = 24.0
_LEGEND_PILL_SLOT = 30


def timeline_layout(scene, x, y, w, base):
    """Where every part of the time line goes, from ``y`` down, in width ``w``.

    Returns a dict: ``axis_y``, ``x0`` / ``x1`` (today / the last date),
    ``bands`` (``(QRectF, shaded, label)``), ``above`` (boxes above the
    axis: ``{kind, rect, ...}`` for the Check Alive card, today and a pill
    moved off the axis), ``pole`` (``(x, top_y)`` or None), ``pill``
    (``{rect, text}`` or None), ``circles``, ``cards`` (below the axis),
    ``leaders`` (``(QPointF, QPointF)``), ``legend`` and ``bottom``.

    WHY the Check Alive is placed first above the axis: its pole runs from
    the axis up to its card, so nothing may be placed under it; its room
    covers the pole and the flag, so later boxes go above it or beside it.
    """
    small = _font(base, delta=-1)
    small_bold = _font(base, bold=True, delta=-1)
    bold = _font(base, bold=True)
    sm, sbm = QFontMetricsF(small), QFontMetricsF(small_bold)
    x0, x1 = x + 10.0, x + w - 10.0
    out = {"x0": x0, "x1": x1, "bands": [], "above": [], "pole": None, "pill": None,
           "circles": [], "cards": [], "leaders": [], "legend": []}
    placed = []
    check = scene["check_alive"]
    level_colour = scene.get("level_colour") or _TEXT
    cards = scene.get("deliveries") or []
    next_x = _x_of(cards[0]["at"], scene, x0, x1) if cards else None
    marker = scene["marker"]

    # 1. The Check Alive: flag, pole and card (relative offsets for now).
    check_item = None
    if check:
        cx = _x_of(check["at"], scene, x0, x1)
        colour = check["colour"]
        cw, ch, lines = _card([
            (check["title"], small_bold, colour, False),
            (check["date"], small, _TEXT, False),
            (check["hint"], small, _GREY, True),
        ])
        if cx + _FLAG + 4 + cw <= x + w:
            mode, left = "right", cx + _FLAG + 4
            span = (cx - 1, left + cw)
            height = ch
        elif cx - 4 - cw >= x:
            mode, left = "left", cx - 4 - cw
            span = (left, cx + _FLAG + 1)
            height = ch
        else:
            mode = "top"
            left = max(x, min(cx - cw / 2.0, x + w - cw))
            span = (min(left, cx - 1), max(left + cw, cx + _FLAG + 1))
            height = ch + _FLAG_H + 4
        bottom = _place_above(placed, span[0], span[1], height)
        check_item = {"kind": "check", "cx": cx, "mode": mode, "left": left, "w": cw,
                      "h": ch, "lines": lines, "bottom": bottom, "height": height,
                      "span": span, "colour": colour, "passed": check.get("passed")}

    # 2. The pill with the time left: on the green stretch, away from the
    # circles and the pole; above the axis when it does not fit there.
    pill_item = None
    if scene.get("pill") and next_x is not None:
        text = scene["pill"]
        pw, ph = sbm.horizontalAdvance(text) + 16, sbm.height() + 4
        a, b = x0 + _R_TODAY + 6, next_x - _R_NEXT - 6
        # Keep the pill off the Check Alive pole and (#134) off the dashed
        # circle of a prepared will's ghost: split the stretch around them.
        cuts = []
        if check_item:
            cuts.append((check_item["cx"], 6))
        early = marker if marker and marker["draft"] else scene.get("ghost")
        if early:
            cuts.append((_x_of(early["at"], scene, x0, x1), _R_LATER + 6))
        pieces = [(a, b)]
        for cx, gap in sorted(cuts):
            out_pieces = []
            for lo, hi in pieces:
                if lo < cx < hi:
                    out_pieces += [(lo, cx - gap), (cx + gap, hi)]
                else:
                    out_pieces.append((lo, hi))
            pieces = out_pieces
        best = max(pieces, key=lambda p: p[1] - p[0])
        if best[1] - best[0] >= pw:
            mid = (best[0] + best[1]) / 2.0
            pill_item = {"on_axis": True, "left": mid - pw / 2.0, "w": pw, "h": ph,
                         "text": text}
        else:
            pw = min(pw, w)
            mid = (x0 + max(next_x, x0)) / 2.0
            left = max(x, min(mid - pw / 2.0, x + w - pw))
            pill_item = {"on_axis": False, "left": left, "w": pw, "h": ph, "text": text,
                         "bottom": _place_above(placed, left, left + pw, ph)}

    # 3. Today (and a draft's date), above the axis.
    name, date = scene["today"]["name"], scene["today"]["date"]
    tw = max(QFontMetricsF(bold).horizontalAdvance(name), sm.horizontalAdvance(date)) + 2
    th = QFontMetricsF(bold).height() + sm.height()
    t_left = max(x, min(x0 - _R_TODAY, x + w - tw))
    today_item = {"left": t_left, "w": tw, "h": th,
                  "bottom": _place_above(placed, t_left, t_left + tw, th)}
    draft_item = None
    # The draft of a wallet with no will in force, or (#134) the ghost of a
    # prepared will while another one is in force: the same dashed label.
    ghost = marker if marker and marker["draft"] else scene.get("ghost")
    if ghost:
        marker_at = ghost["at"]
        text = "{} · {}".format(ghost["date"], ghost["left"])
        dw, dh = sm.horizontalAdvance(text) + 2, sm.height()
        mx = _x_of(marker_at, scene, x0, x1)
        d_left = max(x, min(mx - dw / 2.0, x + w - dw))
        draft_item = {"x": mx, "left": d_left, "w": dw, "h": dh, "text": text,
                      "bottom": _place_above(placed, d_left, d_left + dw, dh)}

    # 4. The cards of the deliveries, below the axis.
    sized = []
    for card in cards:
        colour = level_colour if card["next"] else _TEXT
        sized.append(_card([
            (card["title"], small_bold, colour, False),
            (card["time"], small, _GREY, False),
            (card["heirs"], small, _TEXT, False),
        ]))
    centres = [_x_of(c["at"], scene, x0, x1) for c in cards]

    # Vertical positions: year labels, the boxes above, the axis.
    label_h = sm.height() + 4
    above_h = max([b + h for _l, _r, b, h in placed], default=_ABOVE_MIN) + 4
    axis_y = y + label_h + above_h
    out["axis_y"] = axis_y

    def box(left, bottom, width, height):
        """An above-axis box from its offset above the axis."""
        return QRectF(left, axis_y - bottom - height, width, height)

    if check_item:
        ci = check_item
        if ci["mode"] == "top":
            pole_top = axis_y - ci["bottom"] - _FLAG_H
            card = QRectF(ci["left"], pole_top - 4 - ci["h"], ci["w"], ci["h"])
        else:
            card = box(ci["left"], ci["bottom"], ci["w"], ci["h"])
            pole_top = card.top()
        out["pole"] = (ci["cx"], pole_top)
        out["above"].append({"kind": "check", "rect": card, "lines": ci["lines"],
                             "colour": ci["colour"], "passed": ci["passed"],
                             "span": QRectF(ci["span"][0], min(card.top(), pole_top),
                                            ci["span"][1] - ci["span"][0],
                                            axis_y - min(card.top(), pole_top))})
    out["above"].append({"kind": "today", "rect": box(today_item["left"], today_item["bottom"],
                                                      today_item["w"], today_item["h"]),
                         "name": name, "date": date, "bold": bold, "small": small})
    if draft_item:
        out["above"].append({"kind": "draft", "rect": box(
            draft_item["left"], draft_item["bottom"], draft_item["w"], draft_item["h"]),
            "text": draft_item["text"], "x": draft_item["x"], "font": small})
    if pill_item:
        if pill_item["on_axis"]:
            rect = QRectF(pill_item["left"], axis_y - pill_item["h"] / 2.0,
                          pill_item["w"], pill_item["h"])
        else:
            rect = box(pill_item["left"], pill_item["bottom"], pill_item["w"], pill_item["h"])
        out["pill"] = {"rect": rect, "text": pill_item["text"], "on_axis": pill_item["on_axis"],
                       "font": small_bold}

    # Circles on the axis.
    out["circles"].append({"kind": "today", "x": x0, "r": _R_TODAY})
    if ghost:
        out["circles"].append({"kind": "draft", "x": _x_of(ghost["at"], scene, x0, x1),
                               "r": _R_LATER})
    for card, cx in zip(cards, centres, strict=True):
        out["circles"].append({"kind": "next" if card["next"] else "later", "x": cx,
                               "r": _R_NEXT if card["next"] else _R_LATER,
                               "number": card["number"]})

    # Cards below the axis: one row, two staggered rows, or a column.
    bottom = axis_y + _R_NEXT + 6
    radii = [_R_NEXT if c["next"] else _R_LATER for c in cards]
    placed_cards = _arrange_cards([sz[0] for sz in sized], [sz[1] for sz in sized],
                                  centres, radii, x, w, axis_y)
    for card, (_cw, _ch, lines), (rect, leader) in zip(cards, sized, placed_cards, strict=True):
        if leader is not None:
            out["leaders"].append(leader)
        out["cards"].append({"rect": rect, "lines": lines, "next": card["next"]})
        bottom = max(bottom, rect.bottom())

    # Year bands, behind everything, from the labels to the cards.
    band_bottom = bottom + 6
    label_w = sm.horizontalAdvance("2088") + 10
    step, ticks = year_ticks(scene["start"], scene["end"], x0, x1, label_w)
    edges = [(x0, _year_of(scene["start"]), False)]
    edges += [(tx, year, True) for year, tx, labelled in ticks if labelled and tx > x0 + 0.5]
    edges.append((x1, None, False))
    for (lx, year, labelled), (rx, _yr, _lb) in zip(edges, edges[1:], strict=False):
        if rx - lx < 0.5 or year is None:
            continue
        group = year // step
        # A label only where its band is wide enough to hold it.
        label = str(year) if (labelled or step == 1) and rx - lx >= label_w else ""
        out["bands"].append((QRectF(lx, y, rx - lx, band_bottom - y), group % 2 == 1, label))
    out["label_font"] = small

    # The legend under the bands.
    ly = band_bottom + 8
    lx = x
    lh = sm.height()
    for kind, text in scene.get("legend") or []:
        # The pill's symbol is a long pill, like the one on the axis (owner,
        # #134: a short one read as a dot); the others need 18 px.
        sym = _LEGEND_PILL_SLOT if kind == "pill" else 18
        width = sym + sm.horizontalAdvance(text) + 16
        if lx > x and lx + width > x + w:
            lx, ly = x, ly + lh + 4
        out["legend"].append({"kind": kind, "text": text, "sym": sym,
                              "rect": QRectF(lx, ly, width - 16, lh)})
        lx += width
    out["legend_font"] = small
    out["bottom"] = (ly + lh + 4) if out["legend"] else band_bottom + 4
    out["level_colour"] = level_colour
    out["level"] = scene.get("level")
    out["bold"] = bold
    return out


def timeline_boxes(layout):
    """Every box of the time line that must not overlap another (tests):
    ``[(kind, QRectF)]`` (Check Alive card, today, draft, pill, cards,
    legend entries)."""
    boxes = [(item["kind"], item["rect"]) for item in layout["above"]]
    if layout["pill"]:
        boxes.append(("pill", layout["pill"]["rect"]))
    boxes += [("card", c["rect"]) for c in layout["cards"]]
    boxes += [("legend", e["rect"]) for e in layout["legend"]]
    return boxes


def _flag(painter, px, top, colour, size=_FLAG, height=_FLAG_H):
    """A small flag pointing right, its mast at ``px`` from ``top``."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(colour)))
    painter.drawPolygon(QPolygonF([
        QPointF(px, top), QPointF(px + size, top + height / 2.0), QPointF(px, top + height)]))


def _card_paint(painter, rect, lines, fill, border, width=1.0):
    """A rounded card with its lines of text."""
    painter.setPen(QPen(QColor(border), width))
    painter.setBrush(QBrush(QColor(fill)))
    painter.drawRoundedRect(rect, 5, 5)
    ty = rect.top() + _CARD_PAD
    for text, font, colour, h in lines:
        _text(painter, rect.left() + _CARD_PAD, ty, rect.width() - 2 * _CARD_PAD + 1,
              text, colour, font)
        ty += h


def _paint_timeline(painter, lay, scene, hits):
    """Draw a :func:`timeline_layout` (see there)."""
    axis_y, x0, x1 = lay["axis_y"], lay["x0"], lay["x1"]
    level_colour = lay["level_colour"]
    # Year bands and their labels.
    font = lay["label_font"]
    for rect, shaded, label in lay["bands"]:
        if shaded:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(_BAND)))
            painter.drawRect(rect)
        if label:
            _text(painter, rect.left(), rect.top() + 1, rect.width(), label, _GREY, font,
                  Qt.AlignmentFlag.AlignHCenter)
    # The axis, and the stretch from today to the next delivery.
    pen = QPen(QColor(_AXIS_LINE), 6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(QPointF(x0, axis_y), QPointF(x1, axis_y))
    nxt = next((c for c in lay["circles"] if c["kind"] == "next"), None)
    if nxt is not None and nxt["x"] > x0:
        pen = QPen(QColor(level_colour), 6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(x0, axis_y), QPointF(nxt["x"], axis_y))
    # Leader lines (under the circles and the cards).
    for a, b in lay["leaders"]:
        painter.setPen(QPen(QColor("#9e9e9e"), 1))
        painter.drawLine(a, b)
    # The Check Alive: pole, flag, card.
    for item in lay["above"]:
        if item["kind"] != "check":
            continue
        px, top = lay["pole"]
        painter.setPen(QPen(QColor(item["colour"]), 2))
        painter.drawLine(QPointF(px, axis_y), QPointF(px, top))
        _flag(painter, px, top, item["colour"])
        _card_paint(painter, item["rect"], item["lines"],
                    _CHECK_FILL[bool(item["passed"])], item["colour"])
    # Circles.
    small_bold = _font(lay["bold"], delta=-2)
    for c in lay["circles"]:
        cx, r = c["x"], c["r"]
        if c["kind"] == "today":
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QBrush(QColor(_TODAY)))
        elif c["kind"] == "draft":
            pen = QPen(QColor(_DRAFT), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(_BG)))
        elif c["kind"] == "next":
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QBrush(QColor(level_colour)))
        else:
            painter.setPen(QPen(QColor(_LATER_LINE), 1.5))
            painter.setBrush(QBrush(QColor(_LATER_FILL)))
        painter.drawEllipse(QPointF(cx, axis_y), r, r)
        if c.get("number"):
            colour = "#ffffff" if c["kind"] == "next" else _LATER_TEXT
            painter.setFont(small_bold)
            painter.setPen(QColor(colour))
            painter.drawText(QRectF(cx - r, axis_y - r, 2 * r, 2 * r),
                             int(Qt.AlignmentFlag.AlignCenter), str(c["number"]))
    # The pill with the time left.
    if lay["pill"]:
        pill = lay["pill"]
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(QBrush(QColor(level_colour)))
        rect = pill["rect"]
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.setFont(pill["font"])
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), pill["text"])
        if scene["marker"] and scene["marker"].get("tip"):
            hits.append((QRectF(rect), scene["marker"]["tip"]))
    # Today and a draft's date.
    for item in lay["above"]:
        rect = item["rect"]
        if item["kind"] == "today":
            h = _text(painter, rect.left(), rect.top(), rect.width() + 2, item["name"],
                      _TODAY, item["bold"])
            _text(painter, rect.left(), rect.top() + h, rect.width() + 2, item["date"],
                  _TODAY, item["small"])
        elif item["kind"] == "draft":
            _text(painter, rect.left(), rect.top(), rect.width() + 2, item["text"], _DRAFT,
                  item["font"])
    # The delivery cards.
    for card in lay["cards"]:
        if card["next"]:
            fill = _LEVEL_FILL.get(lay["level"], "#ffffff")
            _card_paint(painter, card["rect"], card["lines"], fill, level_colour, 1.5)
            if scene["marker"] and scene["marker"].get("tip"):
                hits.append((QRectF(card["rect"]), scene["marker"]["tip"]))
        else:
            _card_paint(painter, card["rect"], card["lines"], "#ffffff", _CARD_BORDER)
    # The legend.
    font = lay["legend_font"]
    for entry in lay["legend"]:
        rect, kind = entry["rect"], entry["kind"]
        mid = rect.center().y()
        sx = rect.left() + 6
        if kind == "today":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(_TODAY)))
            painter.drawEllipse(QPointF(sx, mid), 4, 4)
        elif kind == "check":
            painter.setPen(QPen(QColor(CHECK_ALIVE_COLOUR), 1.5))
            painter.drawLine(QPointF(sx - 3, mid + 5), QPointF(sx - 3, mid - 5))
            _flag(painter, sx - 3, mid - 5, CHECK_ALIVE_COLOUR, 9, 7)
        elif kind == "next":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(level_colour)))
            painter.drawEllipse(QPointF(sx, mid), 5, 5)
        elif kind == "later":
            painter.setPen(QPen(QColor(_LATER_LINE), 1))
            painter.setBrush(QBrush(QColor(_LATER_FILL)))
            painter.drawEllipse(QPointF(sx, mid), 4, 4)
        elif kind == "pill":
            # 3x as wide as high: the elongated pill of the axis (#134).
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(level_colour)))
            painter.drawRoundedRect(
                QRectF(rect.left(), mid - 4, _LEGEND_PILL_W, 8), 4, 4)
        sym = entry.get("sym", 18)
        _text(painter, rect.left() + sym, rect.top(), rect.width() - sym + 2, entry["text"],
              _GREY, font)
    return lay["bottom"]


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
    if scene.get("info"):
        # #134: the light box that says which will this is (a prepared
        # will is not sent yet).
        pad = 8.0
        text_h = QFontMetricsF(normal).boundingRect(
            QRectF(0, 0, w - 2 * pad, 10000),
            int(Qt.TextFlag.TextWordWrap), scene["info"]).height()
        box = QRectF(x, y, w, text_h + 2 * pad)
        painter.setPen(QPen(QColor(_INFO_BORDER), 1))
        painter.setBrush(QBrush(QColor(_INFO_FILL)))
        painter.drawRoundedRect(box, 6, 6)
        _text(painter, x + pad, y + pad, w - 2 * pad, scene["info"], _INFO_TEXT, normal)
        y += box.height() + line * 0.6

    # #134: a will no will-executor received is drawn faded under the title.
    if scene.get("faded"):
        painter.setOpacity(_FADED_OPACITY)
    # The time line (#132, owner's mockup): see timeline_layout.
    lay = timeline_layout(scene, x, y, w, base)
    timeline_top = y
    y = _paint_timeline(painter, lay, scene, hits) + line * 0.4

    # The legend under the axis.
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
    if scene.get("overlay"):
        painter.setOpacity(1.0)
        ov = overlay_layout(scene, x, w, timeline_top, lay["axis_y"], base)
        painter.setPen(QPen(QColor(_INFO_BORDER), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255, 225)))
        painter.drawRoundedRect(ov["rect"], 8, 8)
        _text(painter, ov["text_x"], ov["rect"].top() + _OVERLAY_PAD, ov["text_w"],
              ov["title"], "#555555", ov["title_font"], Qt.AlignmentFlag.AlignHCenter)
        _text(painter, ov["text_x"], ov["rect"].top() + _OVERLAY_PAD + ov["title_h"] + 2,
              ov["text_w"], ov["sub"], "#555555", ov["sub_font"],
              Qt.AlignmentFlag.AlignHCenter)
    painter.restore()
    return y + _MARGIN, hits


_OVERLAY_PAD = 10.0


def overlay_layout(scene, x, w, top, axis_y, base):
    """Where the "NOT SENT…" overlay goes (#134): centred on the axis,
    inside ``[x, x + w]`` (the texts wrap), never above ``top`` (under the
    title). Returns ``rect``, ``text_x`` / ``text_w``, the two texts, their
    fonts and the title's height."""
    title, sub = scene["overlay"]
    title_font = _font(base, bold=True, delta=4)
    sub_font = QFont(base)
    pad = _OVERLAY_PAD
    flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap)
    tm, sm = QFontMetricsF(title_font), QFontMetricsF(sub_font)
    width = min(w, max(tm.horizontalAdvance(title), sm.horizontalAdvance(sub)) + 2 * pad + 4)
    text_w = width - 2 * pad
    title_h = tm.boundingRect(QRectF(0, 0, text_w, 10000), flags, title).height()
    sub_h = sm.boundingRect(QRectF(0, 0, text_w, 10000), flags, sub).height()
    height = title_h + 2 + sub_h + 2 * pad
    left = x + (w - width) / 2.0
    rect = QRectF(left, max(top, axis_y - height / 2.0), width, height)
    return {"rect": rect, "text_x": left + pad, "text_w": text_w, "title": title,
            "sub": sub, "title_font": title_font, "sub_font": sub_font,
            "title_h": title_h}


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
            self.scene = view_neutral_scene(now, busy=True)
            return
        try:
            plugin = bw.bal_plugin
            warn = getattr(plugin, "warning_months", lambda: None)()
            self.model = core_future.build_future(
                bw.willitems, bw.will_settings, plugin.is_basic_mode(), now=now,
                warn_months=warn, wallet=getattr(bw, "wallet", None),
                day=datetext.utc_text,
                # #134: deselected will-executors are not "to send".
                selected=selected_urls(Willexecutors.get_willexecutors(plugin)),
            )
            # #134: the one date style of the Will tab.
            self.scene = view_scene(self.model, format_amount=self._format_amount)
        except Exception as e:
            _logger.warning(f"future view failed: {e!r}")
            self.model = None
            self.scene = view_neutral_scene(now)

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
