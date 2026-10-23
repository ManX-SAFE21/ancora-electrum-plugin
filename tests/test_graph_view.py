"""The "Graph" view of the Will tab (CHANGELOG #184).

Owner (2026-10-23, previews approved): one column per delivery with its
date and shares, one box per transaction with the will-executor's name and
the colour of its state, curved links to the children, branches that open
and close (delivery 1 only at first), the mouse shows the path and the
copies of the same will-executor, a click opens Electrum's transaction
window; a third button "Graph" next to "List" and "Future".

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address, and the will with three delivery dates of
``test_multiverse_chain_rebuild.py`` (3 + 9 + 27 transactions).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_graph_view.py
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication, QWidget
from test_build_report import _item

from bal.core import graph as core_graph
from bal.gui.qt import future_view as fv
from bal.gui.qt.graph_view import GraphPanel, GraphView

# The sessions of the chain tests (three will-executors, synchronized).
make = chain.make

SIGN = ("sign your will",)
WE = ["https://we0.test", "https://we1.test", "https://we2.test"]


def _view(s):
    """The Graph view of the session's window, computed and laid out."""
    view = GraphView(s.bw)
    view.compute()
    view._stale = False
    return view


# ------------------------------------------------------------- the tree

def test_the_tree_of_a_will_in_three_deliveries(make):
    s = make()
    chain.three_dates(s)
    s.check()
    roots, nodes = core_graph.build_graph(s.bw.willitems, order=WE)
    assert len(nodes) == 39 and len(roots) == 3
    assert [r.url for r in roots] == WE                 # the tab's order
    for root in roots:
        assert root.depth == 0 and len(root.children) == 3
        assert [c.url for c in root.children] == WE
        for child in root.children:
            assert child.depth == 1 and len(child.children) == 3
            assert all(g.depth == 2 and not g.children for g in child.children)
    assert {n.state for n in nodes.values()} == {core_graph.OK}
    dates = core_graph.column_dates(nodes)
    assert dates == sorted(dates) and len(dates) == 3
    shares = core_graph.column_shares(s.bw.will_settings, s.bw.heirs)
    assert shares == [[("luca", "10%"), ("john", "10%")],
                      [("luca", "10%"), ("john", "10%")],
                      [("luca", "30%"), ("john", "30%")]]


def test_the_states_of_the_boxes():
    selected = {WE[0], WE[1]}
    cases = [
        (_item("COMPLETE", "PUSHED", "CHECKED", url=WE[0]), core_graph.OK, "verified"),
        (_item("COMPLETE", "PUSHED", url=WE[0]), core_graph.WARN, "to_verify"),
        (_item("COMPLETE", url=WE[0]), core_graph.WARN, "to_send"),
        (_item("COMPLETE", "PUSHED", "CHECK_FAIL", url=WE[0]), core_graph.ERR, "failed"),
        (_item(url=WE[0]), core_graph.DRAFT, "to_sign"),
        (_item("COMPLETE", "PUSHED", "CHECKED", url=WE[2]), core_graph.OFF, "excluded"),
    ]
    for w, state, detail in cases:
        assert core_graph.node_state(w, selected) == (state, detail), detail
    replaced = _item("COMPLETE", "PUSHED", url=WE[0])
    replaced.set_status("REPLACED")
    assert core_graph.node_state(replaced, selected) == (core_graph.OFF, "replaced")
    assert not core_graph.shown(replaced) and core_graph.shown(replaced, True)
    mined = _item("COMPLETE", "PUSHED", url=WE[0])
    mined.set_status("CONFIRMED")
    assert core_graph.node_state(mined, selected) == (core_graph.OK, "delivered")
    assert core_graph.shown(mined)


def test_a_draft_reads_as_a_draft(make):
    """The password window closed: the boxes are drafts (not signed)."""
    s = make()
    chain.three_dates(s)
    s.check(cancel_on=SIGN)
    _roots, nodes = core_graph.build_graph(s.bw.willitems, order=WE)
    assert len(nodes) == 39
    assert {n.state for n in nodes.values()} == {core_graph.DRAFT}


def test_one_delivery_shows_the_heirs_shares(make):
    s = make()
    chain._set_heirs(s, ["luca", "john"])
    s.check()
    roots, nodes = core_graph.build_graph(s.bw.willitems, order=WE)
    assert len(roots) == 3 and all(not r.children for r in roots)
    assert core_graph.column_shares(s.bw.will_settings, s.bw.heirs) == [
        [("luca", "50%"), ("john", "50%")]]


# ------------------------------------------------------------- the view

def test_the_first_branch_open_at_first_then_branches_open_and_close(make):
    """Owner's ZIP test: when the view appears, delivery 1 and the whole
    first branch (1 + 3 + 9 boxes, the other copies closed)."""
    s = make()
    chain.three_dates(s)
    s.check()
    view = _view(s)
    assert len(view.visible_nodes()) == 3 + 3 + 9
    first = view.roots[0].wid
    view.mousePressEvent(_press(view.toggles[first].center()))
    assert len(view.visible_nodes()) == 3                 # delivery 1 only
    view.mousePressEvent(_press(view.toggles[first].center()))
    assert len(view.visible_nodes()) == 3 + 3 + 9         # as it was left
    view.expand_all()
    assert len(view.visible_nodes()) == 39
    assert len(view.boxes) == 39
    view.collapse_all()
    assert len(view.visible_nodes()) == 3


def test_the_mouse_shows_the_path_and_the_details(make):
    s = make()
    chain.three_dates(s)
    s.check()
    view = _view(s)
    view.expand_all()
    leaf = view.roots[1].children[2].children[0]
    view.hover = leaf.wid
    assert view._path() == {leaf.wid, leaf.parent.wid, leaf.parent.parent.wid}
    tip = view.tooltip(leaf.wid)
    assert tip.startswith("Delivery 3 · ")
    assert "we0.test" in tip and "received and verified" in tip
    assert view.node_at(view.boxes[leaf.wid].center()) == leaf.wid


def test_a_click_opens_the_transaction_window(make):
    s = make()
    chain.three_dates(s)
    s.check()
    s.bw.show_transaction = MagicMock()
    view = _view(s)
    root = view.roots[0]
    view.mousePressEvent(_press(view.boxes[root.wid].center()))
    s.bw.show_transaction.assert_called_once_with(root.item.tx)


def test_the_view_paints_without_errors(make):
    """Painted off screen, every branch open, a box under the mouse."""
    s = make()
    chain.three_dates(s)
    s.check()
    view = _view(s)
    view.expand_all()
    view.hover = view.roots[0].children[0].wid
    view.resize(view.minimumSize())
    image = view.grab()
    assert not image.isNull() and image.width() >= 3 * 380
    assert view.texts()[:3] == ["✔ we0.test", "✔ we0.test", "✔ we0.test"]


def test_the_panel_shows_the_replaced_versions_on_demand(make):
    s = make()
    chain.three_dates(s)
    s.check()
    for w in list(s.bw.willitems.values())[:1]:
        w.set_status("REPLACED")
    panel = GraphPanel(s.bw)
    panel.view.compute()
    before = len(panel.view.nodes)
    panel.replaced_box.setChecked(True)
    assert len(panel.view.nodes) == before + 1
    replaced = [n for n in panel.view.nodes.values() if n.detail == "replaced"]
    assert len(replaced) == 1 and replaced[0].state == core_graph.OFF


def test_the_switch_has_a_graph_button(make):
    s = make()
    host = QWidget()
    lst, area, graph = QWidget(host), QWidget(host), MagicMock()
    switch = fv.WillViewSwitch(s.plugin.WILL_VIEW, lst, area, fv.FutureView(s.bw),
                               graph_widget=graph)
    assert set(switch.buttons) == {fv.VIEW_LIST, fv.VIEW_FUTURE, fv.VIEW_GRAPH}
    switch.choose(fv.VIEW_GRAPH)
    assert s.plugin.WILL_VIEW.get() == fv.VIEW_GRAPH
    graph.setVisible.assert_called_with(True)
    graph.refresh.assert_called()
    assert switch.current() == fv.VIEW_GRAPH
    old = fv.WillViewSwitch(s.plugin.WILL_VIEW, lst, area, fv.FutureView(s.bw))
    assert fv.VIEW_GRAPH not in old.buttons and old.current() == fv.VIEW_LIST


def _press(point):
    """A left click at ``point`` (a ``QPointF``)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QMouseEvent
    return QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(point),
                       QPointF(point), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


_APP = QApplication.instance() or QApplication(sys.argv)


# ------------------------------------------------------------- the zoom

def test_the_zoom_scales_the_drawing_and_the_clicks(make):
    """Owner's ZIP test: an adjustable zoom (30 % to 150 %), the whole
    drawing smaller or bigger, the clicks still on their boxes."""
    s = make()
    chain.three_dates(s)
    s.check()
    s.bw.show_transaction = MagicMock()
    view = _view(s)
    full = view.minimumSize()
    assert view.set_zoom(0.5) == 0.5
    half = view.minimumSize()
    assert abs(half.width() - full.width() / 2) <= 2
    assert abs(half.height() - full.height() / 2) <= 2
    box = view.boxes[view.roots[1].wid]
    view.mousePressEvent(_press(box.center() * 0.5))
    s.bw.show_transaction.assert_called_once_with(view.roots[1].item.tx)
    assert view.set_zoom(0.01) == 0.3 and view.set_zoom(9) == 1.5


def test_fit_shows_the_whole_open_graph(make):
    s = make()
    chain.three_dates(s)
    s.check()
    view = _view(s)
    view.expand_all()
    zoom = view.fit(1000, 600)
    size = view.minimumSize()
    assert 0.3 <= zoom < 1 and size.width() <= 1001 and size.height() <= 601


def test_the_panel_bar_and_ctrl_wheel(make):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QWheelEvent
    s = make()
    chain.three_dates(s)
    s.check()
    panel = GraphPanel(s.bw)
    panel.view.compute()
    panel.zoom_out.click()
    assert panel.view.zoom == 0.9 and panel.zoom_label.text() == "90%"
    panel.zoom_in.click()
    panel.zoom_in.click()
    assert panel.zoom_label.text() == "110%"
    wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    panel.view.wheelEvent(wheel)
    assert panel.view.zoom == 1.0 and panel.zoom_label.text() == "100%"
    panel.resize(900, 500)
    panel.fit()
    assert panel.zoom_label.text() == "{}%".format(int(round(panel.view.zoom * 100)))


# --------------------------------------------------- the possible futures

def test_every_row_is_a_possible_future(make):
    """Owner (preview approved): every path to the last delivery is a
    possible future, numbered 1 to 27; a closed branch is a group of them;
    a sentence over the graph says what a row means."""
    s = make()
    chain.three_dates(s)
    s.check()
    view = _view(s)
    ranges = core_graph.future_ranges(view.roots)
    assert [ranges[r.wid] for r in view.roots] == [(1, 9), (10, 18), (19, 27)]
    labels = [view.future_label(n) for n in view.rows()]
    assert labels == ["Future {}".format(i) for i in range(1, 10)] + [
        "Futures 10–18", "Futures 19–27"]
    for child in view.roots[0].children:
        view.toggle(child.wid)
    assert [view.future_label(n) for n in view.rows()] == [
        "Futures 1–3", "Futures 4–6", "Futures 7–9", "Futures 10–18", "Futures 19–27"]
    view.collapse_all()
    assert [view.future_label(n) for n in view.rows()] == [
        "Futures 1–9", "Futures 10–18", "Futures 19–27"]
    view.expand_all()
    assert len(view.rows()) == 27
    assert view.explanation().startswith("Each row is a possible future. At each delivery")


def test_one_delivery_has_one_future_per_will_executor(make):
    s = make()
    chain._set_heirs(s, ["luca", "john"])
    s.check()
    panel = GraphPanel(s.bw)
    panel.view.compute()
    panel._explain()
    assert [panel.view.future_label(n) for n in panel.view.rows()] == [
        "Future 1", "Future 2", "Future 3"]
    assert panel.explanation.text().startswith(
        "Each row is a possible future: only one will-executor")
