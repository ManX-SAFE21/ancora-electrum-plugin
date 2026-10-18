"""The window "How BAL PROTOCOL works": the animated explainer (CHANGELOG #162).

The same 17-step animation the owner approved as a web page, drawn here
with ``QPainter`` so it works offline (also on the computer with the keys)
and in Electrum's language: the scene of :func:`bal.core.explainer.scene`
(where every element is at a given second of a step) is painted about 30
times a second.

* :func:`paint_scene`: draws one frame with any ``QPainter`` (a widget, a
  pixmap in the tests), in logical units scaled to the rectangle.
* :class:`ExplainerCanvas`: the drawing; :class:`ExplainerDialog`: the
  window with the drawing, the controls (back, next, play/pause, play from
  the start, the step numbers) and the text of the step under them.
* :func:`show_explainer`: opens the window (one per Electrum window).

The window starts playing by itself. Display only: nothing here builds,
signs, sends or changes a date.
"""

import math
import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import explainer as story
from ...i18n import _
from .common import _logger

# Colours of the web page (light; explicit, so Electrum's dark theme does
# not change them).
BG = "#F3F6F6"
STAGE = "#F2F7F6"
PANEL = "#FFFFFF"
INK = "#0A0F1C"
MUTED = "#5B6670"
LINE = "#D6DEE1"
TEAL = "#0D9488"
TEAL_2 = "#14B8A6"
CHAIN = "#0D9488"
CHAIN_LINE = "#9ED8CF"
HEIR = "#0B6B62"
DANGER = "#B42318"
ORANGE = "#D97706"
SRV_LINE = "#0B6B62"
LED_ON = "#14B8A6"
LED_OFF = "#EF4444"
SCREEN = "#19B8A6"
PAPER = "#FFF4DC"
RED = "#C62828"
COIN_TEXT = "#6B4500"

#: Milliseconds between two frames while something moves.
FRAME_MS = 33


def _color(c, alpha=1.0):
    """A ``QColor`` from a "#rrggbb" string with an opacity 0..1."""
    q = QColor(c)
    q.setAlphaF(max(0.0, min(1.0, alpha)))
    return q


def _font(size, bold=False, mono=False, italic=False, weight=None):
    """A font of ``size`` logical pixels (the painter is scaled)."""
    f = QFont()
    if mono:
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setFamily("monospace")
    f.setPixelSize(max(1, int(round(size))))
    if weight is not None:
        f.setWeight(weight)
    elif bold:
        f.setWeight(QFont.Weight.Bold)
    f.setItalic(italic)
    return f


def _text(p, x, y, text, size, color, alpha=1.0, anchor="start", **kw):
    """Draw ``text`` with its baseline at ``y``; ``anchor`` start/middle/end
    (like SVG). Returns the text width."""
    if alpha <= 0 or not text:
        return 0.0
    f = _font(size, **kw)
    p.setFont(f)
    w = QFontMetricsF(f).horizontalAdvance(text)
    if anchor == "middle":
        x -= w / 2
    elif anchor == "end":
        x -= w
    p.setPen(QPen(_color(color, alpha)))
    p.drawText(QPointF(x, y), text)
    return w


def _wrap(text, max_chars):
    """Break ``text`` into lines of at most ``max_chars`` characters (words
    are never cut)."""
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def _rect(p, x, y, w, h, r=0.0, fill=None, stroke=None, width=1.0, alpha=1.0,
          dash=None):
    """A (rounded) rectangle with an optional fill and outline."""
    p.setBrush(QBrush(_color(fill, alpha)) if fill else Qt.BrushStyle.NoBrush)
    if stroke:
        pen = QPen(_color(stroke, alpha), width)
        if dash:
            pen.setDashPattern([d / width for d in dash])
        p.setPen(pen)
    else:
        p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _line(p, x1, y1, x2, y2, color, width, alpha=1.0, cap=Qt.PenCapStyle.RoundCap):
    pen = QPen(_color(color, alpha), width)
    pen.setCapStyle(cap)
    p.setPen(pen)
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _circle(p, x, y, r, fill=None, stroke=None, width=1.0, alpha=1.0):
    p.setBrush(QBrush(_color(fill, alpha)) if fill else Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_color(stroke, alpha), width) if stroke else Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(x, y), r, r)


def _path(p, path, color, width, alpha=1.0, fill=None):
    pen = QPen(_color(color, alpha), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(_color(fill, alpha)) if fill else Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _check(p, x, y, color, width, alpha=1.0):
    """A tick whose left end is at (x, y)."""
    path = QPainterPath(QPointF(x, y))
    path.lineTo(x + 3.5, y + 3.5)
    path.lineTo(x + 9.5, y - 3.5)
    _path(p, path, color, width, alpha)


def _cross(p, x, y, half, color, width, alpha=1.0):
    _line(p, x - half, y - half, x + half, y + half, color, width, alpha)
    _line(p, x + half, y - half, x - half, y + half, color, width, alpha)


def _bitcoin_coin(p, x, y, r, alpha=1.0, size=None):
    """A coin with the ₿ sign, centred on (x, y)."""
    _circle(p, x, y, r, fill=PAPER, stroke=ORANGE, width=2 if r > 12 else 1.6,
            alpha=alpha)
    # the bitcoin sign drawn as a "B" with two short bars (the ₿ character
    # is missing from many fonts and would show as an empty box)
    size = size or r
    _text(p, x, y + size * 0.36, "B", size, COIN_TEXT, alpha, "middle", bold=True)
    for dx in (-size * 0.12, size * 0.12):
        _line(p, x + dx, y - size * 0.52, x + dx, y - size * 0.36, COIN_TEXT,
              max(1.0, size * 0.1), alpha, cap=Qt.PenCapStyle.FlatCap)
        _line(p, x + dx, y + size * 0.36, x + dx, y + size * 0.52, COIN_TEXT,
              max(1.0, size * 0.1), alpha, cap=Qt.PenCapStyle.FlatCap)


def _person(p, x, y, w, h, color, alpha=1.0):
    """The heir symbol (head + shoulders) in the box x, y, w, h."""
    sx, sy = w / 40.0, h / 60.0
    _circle(p, x + 20 * sx, y + 13 * sy, 10 * min(sx, sy) * 1.0, fill=color,
            alpha=alpha)
    body = QPainterPath(QPointF(x + 3 * sx, y + 58 * sy))
    body.cubicTo(x + 3 * sx, y + 38 * sy, x + 12 * sx, y + 30 * sy,
                 x + 20 * sx, y + 30 * sy)
    body.cubicTo(x + 28 * sx, y + 30 * sy, x + 37 * sx, y + 38 * sy,
                 x + 37 * sx, y + 58 * sy)
    body.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(color, alpha)))
    p.drawPath(body)


def _envelope(p, x, y, alpha, tip, bad):
    """A copy of the will (48 x 34) with its fee coin and its red mark."""
    if alpha <= 0:
        return
    _rect(p, x + 1, y + 1, 46, 32, 3, fill=PAPER, stroke=ORANGE, width=1.8,
          alpha=alpha)
    flap = QPainterPath(QPointF(x + 2, y + 2))
    flap.lineTo(x + 24, y + 18)
    flap.lineTo(x + 46, y + 2)
    _path(p, flap, ORANGE, 1.8, alpha)
    _circle(p, x + 24, y + 21, 3.2, fill=ORANGE, alpha=alpha)
    if tip > 0:
        _bitcoin_coin(p, x + 43, y + 5, 8, alpha * tip, size=10)
    if bad > 0:
        a = alpha * bad
        _rect(p, x + 1, y + 1, 46, 32, 3, fill="#E5484D", alpha=a * 0.3)
        _rect(p, x + 1, y + 1, 46, 32, 3, stroke=RED, width=2, alpha=a)
        _circle(p, x + 6, y + 6, 7, fill=RED, alpha=a)
        _cross(p, x + 6, y + 6, 3, "#FFFFFF", 2, a)


def _tear(p, x, y, t, start):
    """A refused copy: its two halves (and a red cross) fall apart."""
    if t < start:
        return
    e = story.ease((t - start - 0.7) / story.MOVE)
    alpha = 1 - e
    if alpha <= 0:
        return
    for side, pts in ((-1, [(1, 1), (24, 1), (20, 9), (27, 17), (20, 25), (24, 33), (1, 33)]),
                      (1, [(24, 1), (47, 1), (47, 33), (24, 33), (20, 25), (27, 17), (20, 9)])):
        p.save()
        cx, cy = x + (12 if side < 0 else 34), y + 17
        p.translate(cx + side * 22 * e, cy + 34 * e)
        p.rotate(side * 38 * e)
        p.translate(-cx, -cy)
        poly = QPolygonF([QPointF(x + a, y + b) for a, b in pts])
        p.setBrush(QBrush(_color(PAPER, alpha)))
        p.setPen(QPen(_color(ORANGE, alpha), 1.6))
        p.drawPolygon(poly)
        p.restore()
    _circle(p, x + 24, y - 12, 11, fill=DANGER, alpha=alpha)
    _cross(p, x + 24, y - 12, 5, "#FFFFFF", 3, alpha)


# ---------------------------------------------------------------------------
def paint_scene(p, rect, s, texts, now=None):
    """Draw the scene ``s`` (from :func:`bal.core.explainer.scene`) in
    ``rect``, keeping its proportions; ``texts``: the translated words
    (label key -> text) and the live values (``years``, ``tip``)."""
    scale = min(rect.width() / story.WIDTH, rect.height() / story.HEIGHT)
    ox = rect.x() + (rect.width() - story.WIDTH * scale) / 2
    oy = rect.y() + (rect.height() - story.HEIGHT * scale) / 2
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.translate(ox, oy)
    p.scale(scale, scale)
    _rect(p, 0.5, 0.5, story.WIDTH - 1, story.HEIGHT - 1, 18, fill=STAGE,
          stroke=LINE, width=1)
    p.setClipRect(QRectF(1, 1, story.WIDTH - 2, story.HEIGHT - 2))
    T = texts.get  # noqa: N806 - short for "text of"
    t = s["t"]
    y0, y1, y2 = texts["years"]

    # --- the blockchain: mined blocks | divider | next block + future ones
    nb = s["next_block_index"]
    base = texts["tip"] - (story.K - 1)       # the first block shown
    off = s["chain_offset"]
    for i in range(story.K + 6 + 4):
        x = story.X0 + i * story.BLK - off
        if x > story.WIDTH or x + 90 < 0:
            continue
        if i < nb:
            _rect(p, x, 20, 60, 60, fill=None, stroke=CHAIN, width=2)
            _text(p, x + 30, 52, str(base + i), 12.5, CHAIN, 1, "middle",
                  mono=True, bold=True)
            if i < nb - 1:
                _rect(p, x + 52, 64, 18, 10, 5, stroke=CHAIN, width=2)
                _rect(p, x + 64, 66, 18, 10, 5, stroke=CHAIN, width=2)
        elif i <= nb + 3:
            _rect(p, x, 20, 60, 60, stroke=CHAIN, width=2,
                  alpha=1.0 if i == nb else 0.45, dash=(6, 5))
    _line(p, story.DIVIDER_X, 8, story.DIVIDER_X, 92, CHAIN, 2,
          cap=Qt.PenCapStyle.FlatCap)
    a = s["new_block"]
    if a > 0:
        x = story.NEXT_BLOCK_X
        _rect(p, x, 20, 60, 60, fill=PANEL, stroke=CHAIN, width=4, alpha=a)
        _text(p, x + 30, 45, str(base + nb), 12.5, CHAIN, a, "middle",
              mono=True, bold=True)
        _circle(p, x + 30, 62, 7, fill=ORANGE, alpha=a)
        _circle(p, x + 58, 22, 13, fill=TEAL, alpha=a)
        _check(p, x + 51, 22, "#FFFFFF", 3, a)
        _text(p, x + 30, 106, T("accepted"), 16, TEAL, a, "middle", bold=True)
    _text(p, 20, 110, T("chain"), 17, CHAIN, 1, bold=True)

    # --- separators between you | will-executors | heirs
    for x in story.SEPARATORS:
        _line(p, x, 104, x, 400, CHAIN, 2, cap=Qt.PenCapStyle.FlatCap)

    # --- the three will-executors
    for i in range(3):
        y = story.row_y(i)
        x = story.SX
        off_ = i in s["off"]
        a = 0.35 if off_ else 1.0
        _rect(p, x, y, 150, 27, 3, fill=PANEL, stroke=SRV_LINE, width=2, alpha=a)
        for k in range(5):
            _line(p, x + 16 + k * 9, y + 7, x + 16 + k * 9, y + 20, SRV_LINE,
                  2.2, 0.55 * a)
        _text(p, x + 88, y + 18.5, "Umbrel" if i == 2 else "Server", 14,
              SRV_LINE, a, "middle", bold=True)
        _circle(p, x + 128, y + 13.5, 4, fill=LED_OFF if off_ else LED_ON,
                alpha=a)
        _rect(p, x - 4, y + 33, 158, 28, 6, fill=PANEL, alpha=a)
        _text(p, x + 75, y + 53, "Will-executor {}".format(i + 1), 16.5, INK,
              a, "middle", weight=QFont.Weight.DemiBold)
        if off_:
            _text(p, 396, y + 19, T("off"), 16, DANGER, 1, "end", bold=True)

    # --- the time line (years of today .. today + 7)
    _line(p, 60, 466, 990, 466, LINE, 2)
    for k in range(8):
        x = story.TODAY_X + 120 * k
        _line(p, x, 460, x, 472, MUTED, 1.5, cap=Qt.PenCapStyle.FlatCap)
        _text(p, x, 494, str(y0 + k), 14, MUTED, 1, "middle",
              weight=QFont.Weight.DemiBold)
    tx = s["today_x"]
    _circle(p, tx, 466, 10, fill=TEAL)
    _circle(p, tx, 466, 16, stroke=TEAL, width=2, alpha=0.35)
    _text(p, tx - 16, 454, T("today"), 16, MUTED, s["today_label"], "end")
    dx = s["due_x"]
    _rect(p, dx - 24, 416, 48, 42, 4, fill=PANEL, stroke=LINE, width=1)
    _rect(p, dx - 24, 416, 48, 13, 4, fill=ORANGE)
    _rect(p, dx - 24, 423, 48, 6, 0, fill=ORANGE)
    _text(p, dx, 451, str(y2 if s["due_year"] == 2 else y1), 17, INK, 1,
          "middle", bold=True)
    _text(p, dx - 32, 448, T("delivery"), 16, MUTED, 1, "end")
    if s["due_cross"] > 0:
        _cross(p, dx, 437, 21, RED, 5, s["due_cross"])

    # --- the transaction to the heirs (dashed curve with an arrow)
    a = s["transaction"]
    if a > 0:
        curve = QPainterPath(QPointF(226, 372))
        curve.cubicTo(430, 416, 590, 416, 796, 351)
        pen = QPen(_color(CHAIN, a), 3)
        pen.setDashPattern([8 / 3, 7 / 3])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(curve)
        ang = math.atan2(351 - 416, 796 - 590)
        tip_x, tip_y = 796, 351
        pts = [(tip_x, tip_y),
               (tip_x - 13 * math.cos(ang) + 7 * math.sin(ang),
                tip_y - 13 * math.sin(ang) - 7 * math.cos(ang)),
               (tip_x - 13 * math.cos(ang) - 7 * math.sin(ang),
                tip_y - 13 * math.sin(ang) + 7 * math.cos(ang))]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_color(CHAIN, a)))
        p.drawPolygon(QPolygonF([QPointF(*q) for q in pts]))

    # --- you: the computer and its screen, your address
    _rect(p, 31, 151, 170, 110, 5, fill=PANEL, stroke=SRV_LINE, width=2.5)
    _rect(p, 40, 160, 152, 92, 2, fill=SCREEN)
    _line(p, 116, 262, 116, 280, SRV_LINE, 2.5, cap=Qt.PenCapStyle.FlatCap)
    _line(p, 80, 281, 152, 281, SRV_LINE, 2.5)
    _paint_screen(p, s, texts)
    _text(p, 116, 312, T("you"), 19, INK, 1, "middle", weight=QFont.Weight.DemiBold)
    _rect(p, 20, 336, 206, 68, 6, fill=PANEL, stroke=TEAL, width=1.5)
    _text(p, 36, 362, T("your_address"), 16, MUTED)
    _text(p, 36, 388, story.ADDRESS_YOU, 15, INK, mono=True)

    # --- the heirs and their two addresses
    _person(p, 854, 190, 44, 66, HEIR)
    _person(p, 908, 200, 36, 56, HEIR, 0.8)
    _text(p, 899, 276, T("heirs"), 19, INK, 1, "middle",
          weight=QFont.Weight.DemiBold)
    _rect(p, 796, 296, 206, 52, 6, fill=PANEL, stroke=HEIR, width=1.5)
    _text(p, 812, 316, T("heir_address").format(1), 16, MUTED)
    _text(p, 812, 338, story.ADDRESS_HEIR_1, 15, INK, mono=True)
    flash = s["heir2_flash"]
    _rect(p, 796, 354, 206, 52, 6, fill=PANEL, stroke=ORANGE if flash else HEIR,
          width=3 if flash else 1.5)
    _text(p, 812, 374, T("heir_address").format(2), 16, MUTED)
    _text(p, 812, 396, story.ADDRESS_HEIR_2_NEW if s["heir2_new"]
          else story.ADDRESS_HEIR_2_OLD, 15, INK, mono=True)

    # --- notes near the blockchain and the will-executors
    a = s["refused"]
    if a > 0:
        _text(p, 1020, 152, T("refused"), 18, DANGER, a, "end", bold=True)
        _text(p, 1020, 172, T("spent"), 16, MUTED, a, "end")
    _text(p, 660, 206, T("invalidated"), 18, DANGER, s["invalidated"], "middle",
          bold=True)

    # --- the copies (oldest layer first: the newest is on top) and the torn ones
    for pos, start in s["tears"]:
        _tear(p, pos[0], pos[1], t, start)
    for k in range(4):
        for i in range(3):
            e = s["envelopes"].get((k, i))
            if e:
                _envelope(p, e[0], e[1], e[2], s["tips"], e[3])

    # --- the funds and the fee
    for cx, cy in s["coins"]:
        _bitcoin_coin(p, cx, cy, 17, 1.0, size=17)
    if s["fee"]:
        (fx, fy), fa = s["fee"]
        _bitcoin_coin(p, fx, fy, 13, fa, size=13)
    row, fa = s["fee_tag"]
    if row is not None and fa > 0:
        _text(p, story.SX - 14, story.row_y(row) + 18, T("fee"), 17, TEAL, fa,
              "end", bold=True)

    # --- short notes of the steps
    if s["tip_note"] > 0:
        a = s["tip_note"]
        for j, line in enumerate(_wrap(T("tip_note_1"), 14)):
            _text(p, 648, 172 + 16 * j, line, 14, TEAL, a, bold=True)
        for j, line in enumerate(_wrap(T("tip_note_2"), 15)):
            _text(p, 648, 232 + 16 * j, line, 13, MUTED, a)
    key, a = s["change_tag"]
    if key and a > 0:
        _text(p, 116, 138, T(key), 16, TEAL, a, "middle", bold=True)
    key, a = s["day_tag"]
    if key and a > 0:
        _text(p, 451, 406, T(key), 15, TEAL, a, "middle", bold=True)

    # --- the question card covers everything
    a = s["question"]
    if a > 0:
        g = QLinearGradient(QPointF(0, 0), QPointF(story.WIDTH, story.HEIGHT))
        g.setColorAt(0.0, _color("#0B6B62", a))
        g.setColorAt(0.55, _color("#14B8A6", a))
        g.setColorAt(1.0, _color("#7DE3D3", a))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(0, 0, story.WIDTH, story.HEIGHT), 18, 18)
        lines = _wrap(T("question"), 34)
        top = story.HEIGHT / 2 - (len(lines) - 1) * 26
        for j, line in enumerate(lines):
            _text(p, story.WIDTH / 2, top + 52 * j + 14, line, 40, "#FFFFFF", a,
                  "middle", weight=QFont.Weight.DemiBold)
    p.restore()


def _paint_screen(p, s, texts):
    """The picture on the computer's screen, in line with each step."""
    T = texts.get  # noqa: N806 - short for "text of"
    name, a = s["screen"]
    t = s["t"]
    white = "#FFFFFF"
    if a <= 0:
        return
    if name == "write":
        # the will is written once with a quill (6 s), line after line
        _rect(p, 62, 163, 88, 88, 3, fill="#FFFBF0", stroke="#E6D8B8", width=1,
              alpha=a)
        _text(p, 106, 180, T("sheet"), 11, "#1F2A44", a, "middle", italic=True,
              weight=QFont.Weight.DemiBold)
        lines = [((70, 193), (140, 193)), ((70, 205), (132, 205)),
                 ((70, 217), (138, 217)), ((70, 229), (118, 229))]
        windows = [(0.0, 0.96), (1.08, 2.04), (2.16, 3.12), (3.24, 4.2)]
        quill = None
        for (p0, p1), (s0, s1) in zip(lines, windows, strict=True):
            u = min(1.0, max(0.0, (t - s0) / (s1 - s0)))
            if u > 0:
                xe = p0[0] + (p1[0] - p0[0]) * u
                _line(p, p0[0], p0[1], xe, p0[1], "#1F2A44", 2, a)
                if u < 1:
                    quill = (xe, p0[1])
        u = min(1.0, max(0.0, (t - 4.32) / 0.96))
        if u > 0:   # the signature
            sig = QPainterPath(QPointF(104, 243))
            sig.cubicTo(110, 234, 114, 250, 120, 240)
            sig.cubicTo(125, 232, 130, 248, 136, 239)
            sig.lineTo(142, 240)
            pts = [sig.pointAtPercent(min(1.0, j / 40 * u)) for j in range(41)]
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            _path(p, path, "#1F2A44", 2, a)
            quill = (pts[-1].x(), pts[-1].y()) if u < 1 else (142, 240)
        if quill is None:
            quill = (70, 193) if t < 0.01 else (142, 240)
        qx, qy = quill
        _line(p, qx, qy, qx + 26, qy - 36, "#5B4636", 1.6, a)
        feather = QPainterPath(QPointF(qx + 6, qy - 8))
        feather.cubicTo(qx + 10, qy - 26, qx + 24, qy - 44, qx + 40, qy - 54)
        feather.cubicTo(qx + 36, qy - 38, qx + 26, qy - 20, qx + 10, qy - 6)
        feather.closeSubpath()
        _path(p, feather, "#8A7A66", 1, a, fill="#FFFFFF")
    elif name in ("copies", "tips", "invalid"):
        for j in range(3):
            y = 172 + 27 * j
            _rect(p, 56, y, 18, 22, 2, fill=white, alpha=a)
            _text(p, 82, y + 16, T("copy").format(j + 1), 13, white, a, bold=True)
            if name == "copies":
                _circle(p, 164, y + 11, 9, fill="#0B6B62", stroke=white, width=1.6,
                        alpha=a)
                _check(p, 159, y + 11, white, 2.2, a)
            elif name == "tips":
                _bitcoin_coin(p, 164, y + 11, 10, a, size=11)
            else:
                _circle(p, 164, y + 11, 9, fill=RED, stroke=white, width=1.6,
                        alpha=a)
                _cross(p, 164, y + 11, 4, white, 2.2, a)
    elif name == "send":
        # the sending signal, pulsing
        for delay, kind in ((0.0, "dot"), (0.25, "small"), (0.5, "big")):
            u = ((t - delay) % 1.4) / 1.4
            pulse = 0.3 + 0.7 * (0.5 - 0.5 * math.cos(2 * math.pi * u))
            if kind == "dot":
                _circle(p, 116, 226, 6, fill=white, alpha=a * pulse)
            else:
                arc = QPainterPath()
                if kind == "small":
                    arc.moveTo(100, 214)
                    arc.quadTo(116, 200, 132, 214)
                else:
                    arc.moveTo(89, 202)
                    arc.quadTo(116, 176, 143, 202)
                _path(p, arc, white, 7, a * pulse)
    elif name == "beat":
        pts = [(50, 206), (90, 206), (100, 186), (112, 228), (124, 194),
               (132, 206), (182, 206)]
        u = (t % 1.6) / 1.6
        total = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        left = total * u
        path = QPainterPath(QPointF(*pts[0]))
        for i in range(len(pts) - 1):
            d = math.dist(pts[i], pts[i + 1])
            if left >= d:
                path.lineTo(*pts[i + 1])
                left -= d
            else:
                f = left / d
                path.lineTo(pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f,
                            pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f)
                break
        _path(p, path, white, 3.5, a)
        _text(p, 116, 244, T("alive"), 13, white, a, "middle", bold=True)
    elif name == "amount":
        _text(p, 116, 187, T("amount"), 14, white, a, "middle", bold=True)
        _rect(p, 58, 200, 116, 14, 7, fill=white, alpha=a * 0.3)
        _rect(p, 58, 200, 116 * s["amount_bar"], 14, 7, fill=white, alpha=a)
        b = a * s["screen_new"]
        _rect(p, 66, 226, 14, 18, 2, fill=white, alpha=b)
        _text(p, 88, 240, T("new_copy"), 13, white, b, bold=True)
    elif name == "heirs":
        _text(p, 116, 187, T("heirs_small"), 14, white, a, "middle", bold=True)
        _person(p, 82, 194, 26, 38, white, a)
        b = a * s["screen_new"]
        _person(p, 118, 198, 22, 32, white, b * 0.85)
        _text(p, 150, 220, "+", 20, white, b, bold=True)
        _rect(p, 66, 234, 14, 16, 2, fill=white, alpha=b)
        _text(p, 88, 247, T("new_copy"), 13, white, b, bold=True)
    elif name == "calendar":
        y1, y2 = texts["years"][1], texts["years"][2]
        _rect(p, 86, 170, 60, 62, 5, fill=white, alpha=a)
        _rect(p, 86, 170, 60, 16, 5, fill=ORANGE, alpha=a)
        _rect(p, 86, 179, 60, 7, 0, fill=ORANGE, alpha=a)
        _text(p, 116, 214, str(y2 if s["calendar_year"] == 2 else y1), 20, INK,
              a, "middle", bold=True)
        _text(p, 116, 248, T("due_date"), 12, white, a, "middle", bold=True)
    elif name == "flat":
        _line(p, 52, 206, 180, 206, white, 3.5, a)
        _text(p, 116, 238, T("no_beat"), 13, white, a, "middle", bold=True)


def label(key):
    """The translated word ``key`` of :data:`bal.core.explainer.LABELS`.

    A function on purpose: ``_(LABELS["key"])`` would make ``pybabel``
    collect the key itself as a text to translate.
    """
    return _(story.LABELS[key])


def translated_texts(now=None):
    """The words of the scene in Electrum's language, plus the live values:
    ``years`` (this year, delivery, postponed) and ``tip`` (estimate)."""
    texts = {k: label(k) for k in story.LABELS}
    texts["years"] = story.years(now)
    texts["tip"] = story.tip_estimate()
    return texts


def step_texts(n, years=None):
    """(title, text, note) of step ``n`` in Electrum's language; the new
    delivery year is written into steps 9 and 10."""
    title, text, note = story.STEPS[n]
    years = years or story.years()
    text = _(text)
    if n in story.YEAR_STEPS:
        text = text.format(years[2])
    return _(title), text, _(note) if note else ""


# ---------------------------------------------------------------------------
class ExplainerCanvas(QWidget):
    """The drawing: paints the current step at the current second.

    ``clock``: a function returning seconds (``time.monotonic`` by
    default; the tests pass their own).
    """

    def __init__(self, parent=None, clock=None):
        super().__init__(parent)
        self.clock = clock or time.monotonic
        self.texts = translated_texts()
        self.step = 0
        self.started = self.clock()
        self.start_values = story.step_targets(0)
        self.setMinimumSize(515, 258)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self.update)

    def elapsed(self):
        """Seconds since the current step started."""
        return max(0.0, self.clock() - self.started)

    def current_scene(self):
        """The scene of the current step at this second."""
        return story.scene(self.step, self.elapsed(), self.start_values)

    def show_step(self, n):
        """Start step ``n``: the strip, the delivery date and "today" glide
        from where they are now (also after a jump to any step)."""
        try:
            s = self.current_scene()
            self.start_values = (s["chain_offset"], s["due_x"], s["today_x"])
        except Exception as e:   # never let a drawing problem stop the window
            _logger.error(f"explainer: {e!r}")
            self.start_values = story.step_targets(max(0, n - 1))
        self.step = n
        self.started = self.clock()
        self.update()

    def start(self):
        """Repaint about 30 times a second."""
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def heightForWidth(self, w):  # noqa: N802 - Qt's name
        return int(w * story.HEIGHT / story.WIDTH)

    def hasHeightForWidth(self):  # noqa: N802 - Qt's name
        return True

    def paintEvent(self, event):  # noqa: N802 - Qt's name
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(BG))
            paint_scene(p, QRectF(self.rect()), self.current_scene(), self.texts)
        except Exception as e:
            _logger.error(f"explainer paint: {e!r}")
        finally:
            p.end()


class ExplainerDialog(QDialog):
    """The window: the drawing, the controls, the text of the step.

    It starts playing by itself (as if "play" had been pressed); closing
    it stops every timer.
    """

    def __init__(self, parent=None, clock=None, autoplay=True):
        super().__init__(parent)
        self.setWindowTitle(label("title"))
        self.setStyleSheet(f"QDialog {{ background: {BG}; }}")
        self.resize(1080, 820)
        self.playing = False
        self.play_timer = QTimer(self)
        self.play_timer.setSingleShot(True)
        self.play_timer.timeout.connect(self._next_by_itself)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.canvas = ExplainerCanvas(self, clock=clock)
        layout.addWidget(self.canvas, 1)

        # Controls under the drawing (their place never changes): symbols,
        # with their names as tooltips.
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.back_button = self._button("←", "back", self.previous)
        self.next_button = self._button("→", "next", self.next)
        self.play_button = self._button("▶", "play", self.toggle_play, primary=True)
        self.restart_button = self._button("↺", "restart", self.restart)
        for b in (self.back_button, self.next_button, self.play_button,
                  self.restart_button):
            bar.addWidget(b)
        bar.addSpacing(12)
        self.dots = []
        for i in range(story.STEP_COUNT):
            d = QPushButton(str(i + 1))
            d.setFixedSize(28, 28)
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            d.setToolTip(step_texts(i)[0])
            d.clicked.connect(lambda _c=False, i=i: self.go(i))
            self.dots.append(d)
            bar.addWidget(d)
        bar.addStretch(1)
        layout.addLayout(bar)

        # The text of the step.
        card = QWidget()
        card.setObjectName("card")
        card.setStyleSheet(
            f"#card {{ background: {PANEL}; border: 1px solid {LINE}; "
            "border-radius: 14px; }")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 16, 20, 18)
        cl.setSpacing(6)
        self.eyebrow = QLabel()
        self.eyebrow.setStyleSheet(f"color: {TEAL}; font-weight: 600; "
                                   "letter-spacing: 1px;")
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color: {INK}; font-size: 22px; font-weight: 600;")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setStyleSheet(f"color: {MUTED}; font-size: 16px;")
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        for w in (self.eyebrow, self.title, self.text, self.note):
            cl.addWidget(w)
        card.setMinimumHeight(150)
        layout.addWidget(card)

        self.go(0, stop=False)
        if autoplay:
            self.play()

    def _button(self, symbol, key, slot, primary=False):
        """A square control with a symbol; its name is the tooltip."""
        b = QPushButton(symbol)
        b.setFixedSize(40, 40)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        name = label(key)
        b.setToolTip(name)
        b.setAccessibleName(name)
        if primary:
            b.setStyleSheet(f"QPushButton {{ background: {TEAL}; color: white; "
                            f"border: 1px solid {TEAL}; border-radius: 8px; "
                            "font-size: 16px; }")
        else:
            b.setStyleSheet("QPushButton { font-size: 18px; }")
        b.clicked.connect(lambda _c=False: slot())
        return b

    # -- steps --------------------------------------------------------------
    def go(self, n, stop=True):
        """Show step ``n`` (a step number or an arrow stops the playing)."""
        if stop:
            self.pause()
        self.canvas.show_step(n)
        title, text, note = step_texts(n)
        self.eyebrow.setText(label("step_of").format(n + 1,
                                                               story.STEP_COUNT))
        self.title.setText(title)
        self.text.setText(text)
        self.note.setText(note)
        self.note.setVisible(bool(note))
        for i, d in enumerate(self.dots):
            current = i == n
            done = i < n
            d.setStyleSheet(
                "QPushButton { border-radius: 14px; font-size: 12px; "
                + (f"background: {TEAL}; color: white; border: 1px solid {TEAL};"
                   if current else
                   f"background: white; color: {TEAL if done else MUTED}; "
                   f"border: 1px solid {TEAL if done else LINE};")
                + " }")

    def previous(self):
        self.go((self.canvas.step - 1) % story.STEP_COUNT)

    def next(self):
        self.go((self.canvas.step + 1) % story.STEP_COUNT)

    # -- playing ------------------------------------------------------------
    def _set_play_button(self, playing):
        self.play_button.setText("❚❚" if playing else "▶")
        name = label("pause" if playing else "play")
        self.play_button.setToolTip(name)
        self.play_button.setAccessibleName(name)

    def _schedule(self):
        """Wait the reading time of the current step, then the next one."""
        n = self.canvas.step
        title, text, _note = step_texts(n)
        self.play_timer.start(int(story.step_seconds(title, text, n) * 1000))

    def play(self):
        """Play from the step shown."""
        self.playing = True
        self._set_play_button(True)
        self.canvas.show_step(self.canvas.step)
        self.canvas.start()
        self._schedule()

    def restart(self):
        """Play from step 1."""
        self.go(0)
        self.play()

    def pause(self):
        self.playing = False
        self.play_timer.stop()
        self._set_play_button(False)

    def toggle_play(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def _next_by_itself(self):
        if not self.playing:
            return
        n = self.canvas.step
        if n >= story.STEP_COUNT - 1:
            self.pause()
            return
        self.go(n + 1, stop=False)
        self._schedule()

    def showEvent(self, event):  # noqa: N802 - Qt's name
        self.canvas.start()
        super().showEvent(event)

    def closeEvent(self, event):  # noqa: N802 - Qt's name
        self.pause()
        self.canvas.stop()
        super().closeEvent(event)

    def hideEvent(self, event):  # noqa: N802 - Qt's name
        self.pause()
        self.canvas.stop()
        super().hideEvent(event)


def _question_icon(size=16):
    """A teal round badge with a white "?", drawn (no image file, no font
    glyph that may be missing): the "help" sign of the button below."""
    ratio = 3
    pix = QPixmap(size * ratio, size * ratio)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0D9488"))
    p.drawEllipse(QRectF(0, 0, size * ratio, size * ratio))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * ratio * 0.72))
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(0, 0, size * ratio, size * ratio).adjusted(0, -ratio * 0.5, 0, 0),
               int(Qt.AlignmentFlag.AlignCenter), "?")
    p.end()
    pix.setDevicePixelRatio(ratio)
    return QIcon(pix)


def explainer_button(slot):
    """The button "? How will-executors work" that opens the window "How BAL
    PROTOCOL works" (#162). Words with a "?" badge, not a bare icon (owner:
    the round "i" alone was nearly invisible), small (owner: about half the
    first size), in the plugin's teal; the same in every window."""
    b = QPushButton(" " + label("button"))   # a little air after the badge
    b.setIcon(_question_icon())
    b.setIconSize(QSize(16, 16))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(label("title"))
    b.setAccessibleName(label("button"))
    b.setStyleSheet(
        "QPushButton { color: #0B6B62; background: #FFFFFF; font-weight: 600; "
        "font-size: 11px; border: 1px solid #0D9488; border-radius: 12px; "
        "padding: 3px 10px 3px 5px; }"
        " QPushButton:hover { background: #E0F2EF; }"
        " QPushButton:pressed { background: #C8EBE5; }")
    b.clicked.connect(lambda _c=False: slot())
    return b


def explainer_intro(text, slot):
    """The light teal box that opens the will-executor parts: the sentence
    ``text`` (already translated) on the left and the button "? How
    will-executors work" on the right, centred on it (#162, owner: a tidier
    layout, the same in the wizard and in the will-executor window). The
    button is ``box.button``."""
    box = QFrame()
    box.setObjectName("explainerIntro")
    box.setStyleSheet(
        "#explainerIntro { background: #F1F9F7; border: 1px solid #CFE8E2; "
        "border-left: 3px solid #0D9488; border-radius: 6px; }")
    row = QHBoxLayout(box)
    row.setContentsMargins(12, 8, 10, 8)
    row.setSpacing(14)
    sentence = QLabel(text)
    sentence.setWordWrap(True)
    sentence.setStyleSheet("QLabel { color: #33413F; background: transparent; border: none; }")
    row.addWidget(sentence, 1)
    box.button = explainer_button(slot)
    row.addWidget(box.button, 0, Qt.AlignmentFlag.AlignVCenter)
    return box


def show_explainer(bal_window, parent=None):
    """Open the window "How BAL PROTOCOL works" (one per Electrum window;
    shown again, in front, if it is already open). Returns it."""
    dialog = getattr(bal_window, "explainer_dialog", None)
    if dialog is None:
        dialog = ExplainerDialog(parent or getattr(bal_window, "window", None))
        bal_window.explainer_dialog = dialog
        dialog.finished.connect(lambda *_a: setattr(bal_window,
                                                    "explainer_dialog", None))
    else:
        dialog.restart()
    from .window_utils import show_on_top
    show_on_top(dialog, modal_to_window=False)
    return dialog
