"""The "Graph" view of the Will tab (CHANGELOG #184).

Owner (2026-10-23, previews in ``safe21-ancora/previews/185-*``): a third
view next to "List" and "Future". Time from left to right, one column per
delivery with its date and the shares of the heirs; one box per
transaction with the will-executor's name inside and the colour of its
state (the colours of the Check window's steps); curved links from each
copy to its children; [+] / [−] open and close a branch (at first only
delivery 1 is shown); the mouse over a box shows the path from the wallet
in green, outlines the copies of the same will-executor and gives the
details; a click opens Electrum's transaction window, as the List does.
Owner's ZIP test: the first branch open when the view appears, the bar in
the middle with bigger buttons, an adjustable zoom (also Ctrl + wheel) and
"Fit" for the whole open graph. Then (owner, preview approved): every row
is a possible future, on bands light and darker in turn, with "Future n"
(or "Futures 10–18" for a closed branch) at its right, and a sentence over
the graph that says so.
The copies that reached the blockchain keep their path green.

The tree comes from :func:`bal.core.graph.build_graph`. Explicit light
colours, like the rest of the Will tab, so it stays readable with the dark
theme. Display only: nothing here builds, signs, sends or changes a date.
"""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ...core import datetext
from ...core import graph as core_graph
from ...core.report import _EXECUTOR_TEXTS
from ...core.summary import selected_urls
from ...core.willexecutors import Willexecutors
from ...i18n import _
from .common import _logger

# state -> (text and border colour, background, symbol): the Check window's
# step colours (widgets.BuildReportView._STEP_STYLE).
STATE_STYLE = {
    core_graph.OK: ("#2e7d32", "#e8f5e9", "✔"),
    core_graph.WARN: ("#b26a00", "#fff3e0", "⚠"),
    core_graph.ERR: ("#c62828", "#fdecec", "✖"),
    core_graph.DRAFT: ("#616161", "#f5f5f5", "✎"),
    core_graph.OFF: ("#9e9e9e", "#f1f1f1", "–"),
}

_BG = "#ffffff"
_BORDER = "#d6d6d6"
_LINK = "#cfd4da"
_LINK_FADED = "#eef0f2"
_PATH = "#2e9d3a"
_SAME = "#1565c0"
_TEXT = "#333333"
_SHARES = "#2e7d32"
# #184 (owner): one band per possible future, light and darker in turn.
_BANDS = ("#f4f6f8", "#e6eaee")
_FUTURE = "#555555"

BOX_W = 180
BOX_H = 28
ROW_H = 40
COL_W = 380
LEFT = 100
TOP = 76
LEGEND_H = 44
TOGGLE = 18
# #184 (owner's ZIP test): an adjustable zoom, so a whole open will fits;
# the boxes, the links and the texts all get smaller or bigger.
ZOOM_MIN = 0.3
ZOOM_MAX = 1.5
ZOOM_STEP = 0.1


def _legend():
    """``[(state, label)]`` of the legend under the graph."""
    return [
        (core_graph.OK, _("verified")),
        (core_graph.WARN, _("no answer / to be verified")),
        (core_graph.ERR, _("not received")),
        (core_graph.DRAFT, _("draft not signed")),
        (core_graph.OFF, _("disabled or excluded")),
    ]


def detail_text(node):
    """The precise state of ``node`` for its tooltip (translated)."""
    if node.detail == "delivered":
        return _("delivered: in the blockchain")
    if node.detail == "replaced":
        return _("replaced version")
    # The key is looked up first: inside _() the extraction would take
    # the key itself for a text to translate.
    text = _EXECUTOR_TEXTS.get(node.detail)
    return _(text) if text else node.detail


class GraphView(QWidget):
    """The drawing of the graph (in a scroll area, see :class:`GraphPanel`).

    :meth:`refresh` follows the will summary's refreshes; it recomputes
    only while the view is shown. Never raises into the GUI: an error is
    logged and the view shows nothing.
    """

    def __init__(self, bal_window, parent=None):
        """``bal_window``: the ``BalWindow`` whose will is drawn."""
        super().__init__(parent)
        self.bal_window = bal_window
        self.show_replaced = False
        self.expanded = set()
        self.roots = []
        self.nodes = {}
        self.dates = []
        self.shares = []
        self.futures = {}     # wid -> (first, last) possible future
        self.boxes = {}       # wid -> QRectF of its box
        self.toggles = {}     # wid -> QRectF of its [+] / [-]
        self.wallet_point = None
        self.hover = None
        self.zoom = 1.0
        # The first compute (and each time the view appears) opens the first
        # branch of delivery 1 (owner).
        self._fresh = True
        self._logical_width = LEFT + COL_W
        self._logical_height = TOP + ROW_H + LEGEND_H
        self._stale = True
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    # ------------------------------------------------------------- data

    def compute(self):
        """Build the tree from the window's will, now (never raises)."""
        bw = self.bal_window
        try:
            plugin = bw.bal_plugin
            executors = Willexecutors.get_willexecutors(plugin) or {}
            self.roots, self.nodes = core_graph.build_graph(
                bw.willitems, selected=selected_urls(executors),
                show_replaced=self.show_replaced, order=list(executors))
            self.dates = core_graph.column_dates(self.nodes)
            self.shares = core_graph.column_shares(
                getattr(bw, "will_settings", None), getattr(bw, "heirs", None))
            self.futures = core_graph.future_ranges(self.roots)
        except Exception as e:
            _logger.warning(f"graph view failed: {e!r}")
            self.roots, self.nodes, self.dates, self.shares = [], {}, [], []
            self.futures = {}
        # A branch opened before stays open while its copy exists.
        self.expanded &= set(self.nodes)
        if self._fresh and self.roots:
            self._fresh = False
            self.open_first_branch()
        if self.hover not in self.nodes:
            self.hover = None
        self._layout()

    def refresh(self):
        """The will or its settings may have changed: redraw when visible."""
        self._stale = True
        if self.isVisible():
            self._redraw()

    def _redraw(self):
        """Recompute and repaint."""
        self.compute()
        self._stale = False
        self.update()

    def showEvent(self, event):  # noqa: N802 - Qt's name
        """Shown: redraw if stale."""
        super().showEvent(event)
        if self._stale:
            self._redraw()

    # ----------------------------------------------------------- layout

    def visible_nodes(self):
        """The nodes drawn now: delivery 1, and the children of the open
        branches."""
        out = []

        def walk(node):
            out.append(node)
            if node.wid in self.expanded:
                for child in node.children:
                    walk(child)

        for root in self.roots:
            walk(root)
        return out

    def _layout(self):
        """Place the boxes: one row per leaf, a parent in the middle of its
        open children, one column per delivery."""
        self.boxes, self.toggles = {}, {}
        row = [0]

        def place(node):
            """The centre y of ``node`` after placing its open subtree."""
            if node.wid in self.expanded and node.children:
                ys = [place(child) for child in node.children]
                y = (ys[0] + ys[-1]) / 2
            else:
                y = TOP + row[0] * ROW_H + ROW_H / 2
                row[0] += 1
            x = LEFT + node.depth * COL_W
            self.boxes[node.wid] = QRectF(x, y - BOX_H / 2, BOX_W, BOX_H)
            if node.children:
                self.toggles[node.wid] = QRectF(
                    x + BOX_W + 6, y - TOGGLE / 2, TOGGLE, TOGGLE)
            return y

        ys = [place(root) for root in self.roots]
        self.wallet_point = QPointF(30, (ys[0] + ys[-1]) / 2) if ys else None
        columns = max(len(self.dates), 1)
        self._logical_width = LEFT + columns * COL_W + 20
        self._logical_height = TOP + max(row[0], 1) * ROW_H + LEGEND_H + 10
        self.setMinimumSize(int(self._logical_width * self.zoom) + 1,
                            int(self._logical_height * self.zoom) + 1)

    def rows(self):
        """The rows drawn now, top to bottom: each is one possible future
        (an open leaf) or a group of them (a closed branch)."""
        return [n for n in self.visible_nodes()
                if not n.children or n.wid not in self.expanded]

    def future_label(self, node):
        """"Future 6" or, for a closed branch, "Futures 10–18"."""
        first, last = self.futures.get(node.wid, (0, 0))
        if first == last:
            return _("Future {n}").format(n=first)
        return _("Futures {first}–{last}").format(first=first, last=last)

    def explanation(self):
        """The sentence over the graph: what a row means."""
        if len(self.dates) > 1:
            return _(
                "Each row is a possible future. At each delivery only one "
                "will-executor publishes its copy, the first to arrive; the "
                "copies of the next delivery depend on it. Whichever way it "
                "goes, the heirs receive their shares.")
        return _(
            "Each row is a possible future: only one will-executor publishes "
            "its copy, the first to arrive. Whichever it is, the heirs receive "
            "their shares.")

    # ------------------------------------------------------------- zoom

    def set_zoom(self, zoom):
        """Draw at ``zoom`` (kept between :data:`ZOOM_MIN` and
        :data:`ZOOM_MAX`); returns the zoom applied."""
        self.zoom = round(min(max(float(zoom), ZOOM_MIN), ZOOM_MAX), 2)
        self._layout()
        self.update()
        return self.zoom

    def fit(self, width, height):
        """The zoom that shows the whole graph as it is open now in
        ``width`` x ``height`` pixels (the visible part of the scroll area);
        returns it."""
        if not self.boxes:
            return self.set_zoom(1.0)
        # Rounded down: a zoom rounded up could leave the last row out.
        zoom = min(width / self._logical_width, height / self._logical_height, ZOOM_MAX)
        return self.set_zoom(int(zoom * 100) / 100)

    def _logical(self, point):
        """A point of the widget in the drawing's own units (the zoom)."""
        return QPointF(point.x() / self.zoom, point.y() / self.zoom)

    def wheelEvent(self, event):  # noqa: N802 - Qt's name
        """Ctrl + wheel: zoom; the wheel alone scrolls, as usual."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = ZOOM_STEP if event.angleDelta().y() > 0 else -ZOOM_STEP
            self.set_zoom(self.zoom + step)
            on_zoom = getattr(self, "on_zoom", None)
            if callable(on_zoom):
                on_zoom(self.zoom)
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------ hover

    def _path(self):
        """The ids from the hovered node up to delivery 1."""
        out = set()
        node = self.nodes.get(self.hover)
        while node is not None:
            out.add(node.wid)
            node = node.parent
        return out

    def node_at(self, point):
        """The id of the box at ``point`` (a ``QPointF``), or None."""
        for wid, box in self.boxes.items():
            if box.contains(point):
                return wid
        return None

    def toggle_at(self, point):
        """The id whose [+] / [-] is at ``point``, or None."""
        for wid, box in self.toggles.items():
            if box.contains(point):
                return wid
        return None

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt's name
        """Remember the box under the mouse and repaint."""
        wid = self.node_at(self._logical(event.position()))
        if wid != self.hover:
            self.hover = wid
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt's name
        """The mouse left: nothing highlighted."""
        if self.hover is not None:
            self.hover = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 - Qt's name
        """[+] / [-]: open or close the branch; a box: its transaction."""
        if event.button() == Qt.MouseButton.LeftButton:
            point = self._logical(event.position())
            wid = self.toggle_at(point)
            if wid is not None:
                self.toggle(wid)
                return
            wid = self.node_at(point)
            if wid is not None:
                self.open_transaction(wid)
                return
        super().mousePressEvent(event)

    def toggle(self, wid):
        """Open the branch of ``wid``, or close it."""
        if wid in self.expanded:
            self.expanded.discard(wid)
        else:
            self.expanded.add(wid)
        self._layout()
        self.update()

    def expand_all(self):
        """Open every branch."""
        self.expanded = {wid for wid, n in self.nodes.items() if n.children}
        self._layout()
        self.update()

    def open_first_branch(self):
        """Delivery 1 shown, and the first copy open with all its children
        (owner: when the Graph view appears)."""
        self.expanded = set()
        stack = [self.roots[0]] if self.roots else []
        while stack:
            node = stack.pop()
            if node.children:
                self.expanded.add(node.wid)
                stack.extend(node.children)
        self._layout()
        self.update()

    def collapse_all(self):
        """Close every branch: delivery 1 only."""
        self.expanded = set()
        self._layout()
        self.update()

    def open_transaction(self, wid):
        """Electrum's transaction window for ``wid``, as the List opens it."""
        node = self.nodes.get(wid)
        if node is None:
            return
        try:
            self.bal_window.show_transaction(node.item.tx)
        except Exception as e:
            _logger.warning(f"graph view: transaction window failed: {e!r}")

    def tooltip(self, wid):
        """The details of ``wid``: delivery, txid, will-executor, state."""
        node = self.nodes.get(wid)
        if node is None:
            return None
        w = node.item
        lines = [_("Delivery {n}").format(n=node.depth + 1) + " · "
                 + datetext.utc_text(w.tx.locktime)]
        txid = node.wid
        lines.append("txid {}…{}".format(txid[:6], txid[-8:]))
        if node.url:
            line = node.url.split("://", 1)[-1]
            fee = (w.we or {}).get("base_fee")
            if fee:
                try:
                    amount = self.bal_window.window.format_amount_and_units(int(fee))
                except Exception:
                    amount = "{} sat".format(fee)
                line += " · " + _("fee {}").format(amount)
            lines.append(line)
        state = STATE_STYLE[node.state][2] + " " + detail_text(node)
        checked = getattr(w, "checked_time", None)
        if node.detail == "verified" and checked:
            state += " · " + datetext.local_short(checked)
        lines.append(state)
        return "\n".join(lines)

    def event(self, event):
        """The tooltip of the box under the mouse."""
        if event.type() == event.Type.ToolTip:
            wid = self.node_at(self._logical(QPointF(event.pos())))
            if wid is not None:
                QToolTip.showText(event.globalPos(), self.tooltip(wid), self)
                return True
            QToolTip.hideText()
            event.ignore()
            return True
        return super().event(event)

    # ------------------------------------------------------------ paint

    def paintEvent(self, event):  # noqa: N802 - Qt's name
        """Draw the graph on a light panel (never raises)."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
            painter.setPen(QPen(QColor(_BORDER), 1))
            painter.setBrush(QColor(_BG))
            painter.drawRoundedRect(rect, 6, 6)
            painter.scale(self.zoom, self.zoom)
            self.paint(painter)
        except Exception as e:
            _logger.warning(f"graph view paint failed: {e!r}")
        finally:
            painter.end()

    def paint(self, p):
        """The headers, the links, the boxes, the toggles, the legend."""
        base = QFont(self.font())
        if not self.nodes:
            p.setPen(QColor(_TEXT))
            p.setFont(base)
            p.drawText(QRectF(20, 20, max(self.width() - 40, 100), 30),
                       Qt.AlignmentFlag.AlignLeft, _("No will to draw yet."))
            return
        path = self._path()
        hovered = self.nodes.get(self.hover)
        rows = self.rows()
        self._paint_bands(p, rows)
        self._paint_headers(p, base)
        visible = self.visible_nodes()
        for node in visible:
            if node.wid in self.boxes:
                self._paint_link(p, node, path, hovered)
        if self.wallet_point is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#555555"))
            p.drawEllipse(self.wallet_point, 8, 8)
            small = QFont(base)
            small.setPointSizeF(max(base.pointSizeF() - 1.5, 7))
            p.setFont(small)
            p.setPen(QColor("#555555"))
            p.drawText(QRectF(8, self.wallet_point.y() + 12, 90, 34),
                       Qt.AlignmentFlag.AlignLeft, _("Wallet funds"))
        for node in visible:
            self._paint_box(p, node, base, path, hovered)
        self._paint_futures(p, base, rows, path)
        self._paint_legend(p, base)

    def _paint_bands(self, p, rows):
        """One band per row, light and darker in turn (owner)."""
        p.setPen(Qt.PenStyle.NoPen)
        for i, node in enumerate(rows):
            box = self.boxes.get(node.wid)
            if box is None:
                continue
            p.setBrush(QColor(_BANDS[i % 2]))
            p.drawRect(QRectF(8, box.center().y() - ROW_H / 2,
                              self._logical_width - 16, ROW_H))

    def _paint_futures(self, p, base, rows, path):
        """"Future n" at the right of each row; green on the path under
        the mouse."""
        bold = QFont(base)
        bold.setBold(True)
        p.setFont(bold)
        right = LEFT + max(len(self.dates), 1) * COL_W - 10
        for node in rows:
            box = self.boxes.get(node.wid)
            if box is None:
                continue
            p.setPen(QColor(_PATH if node.wid in path else _FUTURE))
            p.drawText(QRectF(right - 150, box.center().y() - 12, 150, 24),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       self.future_label(node))

    def _paint_headers(self, p, base):
        """"Delivery n", its date and the shares, over each column."""
        bold = QFont(base)
        bold.setBold(True)
        for depth, locktime in enumerate(self.dates):
            x = LEFT + depth * COL_W + BOX_W / 2
            area = QRectF(x - COL_W / 2 + 10, 10, COL_W - 20, 20)
            p.setFont(bold)
            p.setPen(QColor(_TEXT))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "{} · {}".format(
                _("Delivery {n}").format(n=depth + 1), datetext.utc_day(locktime)))
            if depth < len(self.shares) and self.shares[depth]:
                p.setFont(base)
                p.setPen(QColor(_SHARES))
                text = " · ".join("{} {}".format(n, s) for n, s in self.shares[depth])
                metrics = QFontMetricsF(base)
                text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, area.width())
                p.drawText(area.translated(0, 20), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_link(self, p, node, path, hovered):
        """The curve from the parent (or the wallet) to ``node``."""
        box = self.boxes[node.wid]
        if node.parent is None:
            if self.wallet_point is None:
                return
            start = QPointF(self.wallet_point.x() + 8, self.wallet_point.y())
        else:
            parent = self.boxes.get(node.parent.wid)
            if parent is None:
                return
            start = QPointF(parent.right() + 6 + TOGGLE, parent.center().y())
        end = QPointF(box.left(), box.center().y())
        green = node.wid in path or (
            node.delivered and (node.parent is None or node.parent.delivered))
        colour, width = (_PATH, 3) if green else (_LINK, 1.3)
        if not green and hovered is not None and node.url != hovered.url:
            colour = _LINK_FADED
        curve = QPainterPath(start)
        middle = (start.x() + end.x()) / 2
        curve.cubicTo(QPointF(middle, start.y()), QPointF(middle, end.y()), end)
        p.setPen(QPen(QColor(colour), width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(curve)

    def _paint_box(self, p, node, base, path, hovered):
        """The box of ``node`` and its [+] / [-]."""
        box = self.boxes.get(node.wid)
        if box is None:
            return
        fg, bg, symbol = STATE_STYLE[node.state]
        faded = (hovered is not None and node.url != hovered.url
                 and node.wid not in path)
        same = hovered is not None and node.url == hovered.url
        pen = QPen(QColor(_SAME), 3) if same else QPen(QColor(fg), 1.5)
        if node.state == core_graph.DRAFT and not same:
            pen.setStyle(Qt.PenStyle.DashLine)
        fill = QColor(bg)
        text_colour = QColor(fg)
        if faded:
            fill.setAlpha(110)
            text_colour.setAlpha(110)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(box, 5, 5)
        font = QFont(base)
        font.setBold(node.state != core_graph.OFF)
        p.setFont(font)
        p.setPen(text_colour)
        name = node.name or _("backup")
        inner = box.adjusted(8, 0, -8, 0)
        text = QFontMetricsF(font).elidedText(
            "{}  {}".format(symbol, name), Qt.TextElideMode.ElideRight, inner.width())
        p.drawText(inner, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        toggle = self.toggles.get(node.wid)
        if toggle is None:
            return
        opened = node.wid in self.expanded
        p.setPen(QPen(QColor("#9e9e9e")))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(toggle, 3, 3)
        bold = QFont(base)
        bold.setBold(True)
        p.setFont(bold)
        p.setPen(QColor(_TEXT))
        # A closed branch: its futures are written at the right of its row.
        p.drawText(toggle, Qt.AlignmentFlag.AlignCenter, "−" if opened else "+")

    def _paint_legend(self, p, base):
        """The states, under the graph."""
        y = max(self.height() / self.zoom, self._logical_height) - LEGEND_H + 8
        x = 16.0
        metrics = QFontMetricsF(base)
        p.setFont(base)
        for state, label in _legend():
            fg, bg, symbol = STATE_STYLE[state]
            mark = QRectF(x, y, 22, 20)
            pen = QPen(QColor(fg), 1.5)
            if state == core_graph.DRAFT:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(mark, 4, 4)
            p.setPen(QColor(fg))
            p.drawText(mark, Qt.AlignmentFlag.AlignCenter, symbol)
            p.setPen(QColor(_TEXT))
            p.drawText(QPointF(x + 28, y + 15), label)
            x += 28 + metrics.horizontalAdvance(label) + 24

    def texts(self):
        """The box texts drawn now, in order (tests)."""
        if self._stale:
            self.compute()
            self._stale = False
        return ["{} {}".format(STATE_STYLE[n.state][2], n.name or _("backup"))
                for n in self.visible_nodes()]


_BAR_BUTTON = (
    "QPushButton{font-weight:bold;padding:5px 14px;color:#0d47a1;"
    "background:#e3f2fd;border:1px solid #1565c0;border-radius:4px;}"
    "QPushButton:hover{background:#bbdefb;}"
)


class GraphPanel(QWidget):
    """The Graph view with its bar, in the middle above the drawing (owner):
    "Expand all", "Collapse all", "Show the replaced versions" and the zoom
    ([−] 100% [+] and "Fit"); the drawing in a scroll area."""

    def __init__(self, bal_window, parent=None):
        """``bal_window``: the ``BalWindow`` whose will is drawn."""
        super().__init__(parent)
        self.view = GraphView(bal_window)
        self.view.on_zoom = self._show_zoom
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch(1)
        self.expand_button = self._button(_("Expand all"), self.view.expand_all)
        self.collapse_button = self._button(_("Collapse all"), self.view.collapse_all)
        self.replaced_box = QCheckBox(_("Show the replaced versions"))
        self.replaced_box.setStyleSheet("QCheckBox{font-weight:bold;color:#0d47a1;}")
        self.replaced_box.toggled.connect(self._show_replaced)
        self.zoom_out = self._button("−", lambda: self._zoom_by(-ZOOM_STEP))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setStyleSheet("font-weight:bold;color:#333333;")
        self.zoom_in = self._button("+", lambda: self._zoom_by(ZOOM_STEP))
        self.fit_button = self._button(_("Fit"), self.fit)
        for widget in (self.expand_button, self.collapse_button, self.replaced_box):
            bar.addWidget(widget)
        bar.addSpacing(24)
        for widget in (self.zoom_out, self.zoom_label, self.zoom_in, self.fit_button):
            bar.addWidget(widget)
        bar.addStretch(1)
        column.addLayout(bar)
        # #184 (owner): what a row means, over the graph.
        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        self.explanation.setStyleSheet("color:#555555;padding:2px 8px;")
        column.addWidget(self.explanation)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.area.setWidget(self.view)
        column.addWidget(self.area, 1)

    @staticmethod
    def _button(text, slot):
        """A button of the bar, bigger and coloured (owner: more visible)."""
        button = QPushButton(text)
        button.setStyleSheet(_BAR_BUTTON)
        button.clicked.connect(lambda _c=False: slot())
        return button

    def _zoom_by(self, step):
        """[−] / [+]: one step of zoom."""
        self._show_zoom(self.view.set_zoom(self.view.zoom + step))

    def fit(self):
        """"Fit": the whole open graph in the visible part of the area."""
        viewport = self.area.viewport().size()
        self._show_zoom(self.view.fit(max(viewport.width() - 8, 100),
                                      max(viewport.height() - 8, 100)))

    def _show_zoom(self, zoom):
        """Write the zoom in the bar ("80%")."""
        self.zoom_label.setText("{}%".format(int(round(zoom * 100))))

    def showEvent(self, event):  # noqa: N802 - Qt's name
        """The Graph view appears: the first branch open again (owner)."""
        super().showEvent(event)
        self.view._fresh = True
        self.refresh()

    def _show_replaced(self, on):
        """The check box: draw the replaced versions (grey) or not."""
        self.view.show_replaced = bool(on)
        self.view._redraw()
        self._explain()

    def refresh(self):
        """See :meth:`GraphView.refresh`."""
        self.view.refresh()
        self._explain()

    def _explain(self):
        """The sentence over the graph (one delivery or several)."""
        self.explanation.setText(self.view.explanation() if self.view.nodes else "")
