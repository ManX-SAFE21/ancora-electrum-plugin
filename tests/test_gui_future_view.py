"""The Future view of the Will tab and its "List | Future" selector (CHANGELOG #131).

Here, on the real widgets (``bal/gui/qt/future_view.py``) with realistic
wills made by ``tests/flowkit.py`` (three fake will-executors, each with its
own fee address; throw-away test wallets with the test password; fixed
clock 2026-10-10 12:00 UTC):

* the selector switches between the list and the Future view and the
  choice is remembered (Electrum config ``bal_will_view``);
* the Future view paints without errors (grabbed to a pixmap) and exposes
  the texts it draws: the delivery, the sentence with the real shares, the
  will-executors, the explanation line; BASIC with a fixed date has no
  Check Alive, ADVANCED has one; a draft, no will;
* an error in the data never reaches the GUI (logged, today only);
* 30 will-executors (synthetic data): the squares wrap into rows inside the
  width, the texts below start under the last row, the count is right;
* the time line of #132 (owner's mockup): nothing overlaps (cards, labels,
  pill, legend, the Check Alive pole and flag, the leader lines) for dense
  and sparse dates at 300-1600 px; numbered circles, the next delivery
  highlighted, one card per delivery, the legend; no "instalment" word;
* Tools -> Display: the note about the Future view for the live will, not
  for an imported one (and shown after a Merge).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_future_view.py
"""

import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import bal.core.future as core_future
from bal.gui.qt import future_view as fv
from bal.gui.qt.lists import PreviewList

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D = fk.ts(2027, 2, 27, 22)
SIGN = ("sign your will",)
SENTENCE = "If the will is no longer checked or renewed, on "
EXPLAIN = ("Will-executors keep a copy of your will and broadcast it on "
           "the delivery date.")


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions with three will-executors, synchronized; the
    Future view reads the scenario's clock."""
    fk.ensure_event_loop()
    made = []

    def _make(mode="basic", **kw):
        """One session (``fk.Session`` arguments)."""
        kw.setdefault("password", TEST_PASSWORD)
        kw.setdefault("fee", 21)
        kw.setdefault("locktime", D)
        kw.setdefault("threshold", "-30d")
        s = fk.Session(monkeypatch, mode=mode, **kw)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        monkeypatch.setattr(fv.FutureView, "_now", lambda self: s.clock.now)
        s.bw.window.config = s.config
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _view(s, width=900):
    """A Future view of the session's window, shown and laid out."""
    view = fv.FutureView(s.bw)
    view.resize(width, 500)
    view.show()
    view.refresh()
    _APP.processEvents()
    return view


def _grab(view):
    """Paint the view into a pixmap; it must not be empty."""
    pixmap = view.grab()
    assert not pixmap.isNull() and pixmap.width() > 0
    return pixmap


def _colour_pixels(image, colour):
    """How many pixels of ``image`` have exactly ``colour``."""
    target = QColor(colour).rgb()
    return sum(1 for y in range(image.height()) for x in range(image.width())
               if image.pixel(x, y) == target)


def _date(ts):
    """A date as the view writes it (UTC day and time, #131)."""
    return fv.utc_date_text(ts)


def test_selector_switches_and_is_remembered(make):
    """List | Future: one view at a time, the choice stored in Electrum's
    config (never in the wallet) and restored by a new selector."""
    s = make()
    s.check()
    host = QWidget()
    box = QVBoxLayout(host)
    lst = PreviewList(s.bw, host, None)
    view = fv.FutureView(s.bw)
    area = fv.make_future_area(view)
    switch = fv.WillViewSwitch(s.plugin.WILL_VIEW, lst, area, view)
    for w in (switch, lst, area):
        box.addWidget(w)
    host.resize(1000, 700)
    host.show()
    _APP.processEvents()
    assert [b.text() for b in switch.buttons.values()] == ["List", "Future"]
    assert switch.buttons["list"].isChecked()
    assert not lst.isHidden() and area.isHidden()

    switch.buttons["future"].click()
    _APP.processEvents()
    assert switch.buttons["future"].isChecked()
    assert lst.isHidden() and not area.isHidden()
    assert s.config.get("bal_will_view") == "future"
    assert "bal_will_view" not in s.wallet.db.get_dict("will_settings")
    assert any(t.startswith(SENTENCE) for t in view.texts())

    again = fv.WillViewSwitch(s.plugin.WILL_VIEW, lst, area, view)
    assert again.buttons["future"].isChecked() and lst.isHidden()
    again.buttons["list"].click()
    assert s.config.get("bal_will_view") == "list"
    assert not lst.isHidden() and area.isHidden()


def test_unknown_stored_view_reads_as_the_list(make):
    """A broken config value never hides the list."""
    s = make()
    s.plugin.WILL_VIEW.set("something")
    lst, area = QWidget(), QWidget()
    switch = fv.WillViewSwitch(s.plugin.WILL_VIEW, lst, area, fv.FutureView(s.bw))
    assert switch.current() == "list" and area.isHidden()


def test_will_in_force_basic_fixed_date(make):
    """BASIC, fixed date: the delivery, the sentence with the real shares,
    the will-executors with the legend and the explanation; no Check
    Alive."""
    s = make()
    s.check()
    view = _view(s)
    _grab(view)
    texts = view.texts()
    assert texts[0] == "Your will is in force"
    assert _date(fk.FIXED_NOW) in texts and "Today" in texts
    assert "Delivery to the heirs: {} (in 4 months and 17 days)".format(_date(D)) in texts
    assert SENTENCE + "{} the bitcoin will go to: alice 60%, bob 40%".format(_date(D)) in texts
    assert "Will-Executor: 3 of 3 received and verified it" in texts
    assert "received and verified" in texts and EXPLAIN in texts
    assert not any(t.startswith("Check Alive") for t in texts)
    # #132: the delivery form under the delivery; no flag at all.
    assert "Delivery form: Fixed date" in texts
    assert view.scene["check_alive"] is None
    assert _colour_pixels(_grab(view).toImage(), fv.CHECK_ALIVE_COLOUR) == 0
    assert view.scene["marker"]["colour"] == "#c62828"      # red: < 6 months
    assert [sq["colour"] for sq in view.scene["executors"]] == ["#2e7d32"] * 3


def test_advanced_shows_the_check_alive_and_tooltips(make):
    """ADVANCED: the Check Alive on the axis and in the legend; each
    executor square tells its URL and state on mouse over."""
    s = make(mode="advanced")
    s.check()
    view = _view(s)
    _grab(view)
    texts = view.texts()
    check = fk.ts(2027, 1, 28)
    assert ("Check Alive: {} — from that day the wallet asks you to renew it"
            .format(_date(check))) in texts
    assert view.scene["check_alive"]["date"] == _date(check)
    # #132 (owner): the flag, its pole and its date in orange-red.
    assert view.scene["check_alive"]["colour"] == fv.CHECK_ALIVE_COLOUR == "#D85A30"
    assert _colour_pixels(_grab(view).toImage(), fv.CHECK_ALIVE_COLOUR) > 40
    assert "Delivery form: Fixed date" in texts
    squares = [(box, tip) for box, tip in view._hits if tip.startswith("https://")]
    assert [tip for _b, tip in squares] == [
        "https://we{}.test\nreceived and verified".format(i) for i in range(3)]
    box = squares[1][0]
    assert view.tooltip_at(box.center()) == squares[1][1]


def test_period_form_and_draft_and_no_will(make):
    """The period text (#127); only a draft; nothing at all."""
    s = make(mode="advanced", period_months=24)
    s.check()
    texts = _view(s).texts()
    # #132: the delivery form right under the delivery line.
    form = "Delivery form: Period (renewable) — 24 months (2 years)"
    assert form in texts
    assert texts.index(form) == 1 + next(
        i for i, t in enumerate(texts) if t.startswith("Delivery to the heirs:"))

    d = make()
    d.check(cancel_on=SIGN)
    view = _view(d)
    _grab(view)
    texts = view.texts()
    assert texts[0] == "No will in force"
    assert "Will prepared: it must be signed and sent" in texts
    assert view.scene["marker"]["draft"] is True and "Draft" in texts
    assert not any(t.startswith(SENTENCE) for t in texts)

    n = make()
    view = _view(n)
    _grab(view)
    assert view.texts()[0] == "No will in force"
    assert view.scene["marker"] is None


def test_errors_never_reach_the_gui(make, monkeypatch):
    """The data raises: logged, the view shows today only."""
    s = make()
    s.check()

    def boom(*a, **k):
        raise RuntimeError("broken will data")

    monkeypatch.setattr(core_future, "build_future", boom)
    view = _view(s)
    _grab(view)
    assert view.texts() == ["Today", _date(fk.FIXED_NOW)]


def test_busy_shows_the_operation_in_progress(make):
    """A working dialog on screen: the summary's sentence, no half state."""
    s = make()
    s.check()
    s.bw._will_summary_busy = 1
    view = _view(s)
    assert view.texts()[0] == "Operation in progress: follow the window in front"


def _thirty():
    """A will in force with 30 will-executors: 28 verified, 1 to be
    verified, 1 that did not receive it (synthetic data)."""
    now = fk.FIXED_NOW
    states = ["verified"] * 28 + ["to_verify", "failed"]
    return core_future.Future(
        state=core_future.STATE_IN_FORCE, now=now, title="Your will is in force",
        deliveries=[core_future.Instalment(locktime=D, heirs=[
            core_future.HeirShare("alice", 599_000, 60.0),
            core_future.HeirShare("bob", 399_333, 40.0)])],
        level="red", time_left=core_future.time_left_text(D, now),
        executors=[core_future.ExecutorMark("https://we{:02d}.test".format(i), st)
                   for i, st in enumerate(states)],
        executors_text="28 of 30 verified it · 1 to be verified · 1 did not receive it",
    )


def test_thirty_executors_wrap_inside_the_width():
    """Owner (#131): many will-executors. At 420 px the 30 squares take
    several rows, all inside the width; the legend, the explanation and the
    sentence start below the last row; every square has its tooltip."""
    model = _thirty()
    scene = fv.future_scene(model)
    width = 420
    rect = QRectF(0, 0, width, 2000)
    pixmap = QPixmap(width, 900)
    painter = QPainter(pixmap)
    try:
        bottom, hits = fv.paint_future(painter, rect, scene)
    finally:
        painter.end()
    squares = [box for box, tip in hits if tip.startswith("https://we")]
    assert len(squares) == 30
    rows = sorted({round(b.top()) for b in squares})
    assert len(rows) >= 2
    inner_left, inner_right = rect.left() + fv._MARGIN, rect.right() - fv._MARGIN
    assert all(inner_left <= b.left() and b.right() <= inner_right for b in squares)
    for i, a in enumerate(squares):
        for b in squares[i + 1:]:
            assert not a.intersects(b)
    sentence = [box for box, tip in hits if tip.startswith("alice: ")]
    assert sentence and sentence[0].top() > max(b.bottom() for b in squares)
    assert bottom > sentence[0].bottom()
    texts = fv.scene_texts(scene)
    assert ("Will-Executor: 28 of 30 verified it · 1 to be verified · "
            "1 did not receive it") in texts
    assert texts[texts.index(EXPLAIN) - 3:texts.index(EXPLAIN)] == [
        "received and verified", "received, to be verified", "not received"]
    colours = [sq["colour"] for sq in scene["executors"]]
    assert colours.count("#2e7d32") == 28 and colours.count("#c62828") == 1
    assert fv.scene_height(scene, width) == pytest.approx(bottom)


def test_square_rects_rows():
    """``square_rects``: as many per row as fit, never fewer than one."""
    boxes = fv.square_rects(30, 0, 0, 100)
    per_row = len({b.left() for b in boxes})
    assert per_row == 5 and len({b.top() for b in boxes}) == 6
    assert all(b.right() <= 100 for b in boxes)
    assert len({b.top() for b in fv.square_rects(3, 0, 0, 5)}) == 3


# ------------------------------------------- UTC dates and the year axis

def test_dates_are_the_utc_day(make):
    """Owner's choice: a delivery at 22:00 UTC reads its UTC day and time,
    like the summary box (the local time could be the next day)."""
    assert fv.utc_date_text(D) == "27/02/2027 22:00 UTC"
    assert fv.utc_date_text(fk.ts(2027, 8, 27, 22)) == "27/08/2027 22:00 UTC"
    # After 2038 too (no datetime.fromtimestamp).
    assert fv.utc_date_text(fk.ts(2045, 1, 1, 23)) == "01/01/2045 23:00 UTC"
    s = make()
    s.check()
    texts = _view(s).texts()
    # The card of the single delivery: the UTC day in its title, the time under it.
    assert "Delivery to the heirs · 27/02/2027" in texts and "22:00 UTC" in texts
    assert "Delivery to the heirs: 27/02/2027 22:00 UTC (in 4 months and 17 days)" in texts
    assert any(t.startswith(SENTENCE + "27/02/2027 22:00 UTC ") for t in texts)
    assert "10/10/2026 12:00 UTC" in texts


def _labels_apart(ticks, label_w):
    """True when the labelled ticks are at least ``label_w`` apart."""
    xs = [x for _y, x, labelled in ticks if labelled]
    return all(b - a >= label_w - 1e-6 for a, b in zip(xs, xs[1:], strict=False))


def test_year_ticks_short_span():
    """2026-10-10 -> 2028-10-10 on 800 px: a tick on 1 January 2027 and
    2028 at their true places, every year labelled."""
    start, end = fk.ts(2026, 10, 10), fk.ts(2028, 10, 10)
    step, ticks = fv.year_ticks(start, end, 0.0, 800.0, 50.0)
    assert step == 1
    assert [(y, lab) for y, _x, lab in ticks] == [(2027, True), (2028, True)]
    for year, x, _lab in ticks:
        expected = 800.0 * (fk.ts(year, 1, 1) - start) / (end - start)
        assert x == pytest.approx(expected)
    assert _labels_apart(ticks, 50.0)


@pytest.mark.parametrize("width", [300.0, 520.0, 900.0, 1600.0])
def test_year_ticks_long_span_never_overlap(width):
    """2026 -> 2045: the step grows (2, 5, 10 years) with a narrow width so
    the labels never overlap; every year still has its tick."""
    start, end = fk.ts(2026, 10, 10), fk.ts(2045, 3, 1)
    step, ticks = fv.year_ticks(start, end, 0.0, width, 50.0)
    assert [y for y, _x, _l in ticks] == list(range(2027, 2046))
    assert step in fv.YEAR_STEPS
    assert _labels_apart(ticks, 50.0)
    assert all(lab == (y % step == 0) for y, _x, lab in ticks)
    if width <= 520.0:
        assert step >= 2


def test_nine_executors_four_instalments_paint():
    """Multiverse case of the owner: 9 will-executors, 4 instalments. The
    later instalments are on the axis (their dates drawn), the axis spans
    to the last one and the drawing fits in 520 px."""
    now = fk.FIXED_NOW
    dates = [fk.ts(2028, 3, 1, 22), fk.ts(2030, 3, 1, 22),
             fk.ts(2032, 3, 1, 22), fk.ts(2035, 3, 1, 22)]
    model = core_future.Future(
        state=core_future.STATE_IN_FORCE, now=now, title="Your will is in force",
        deliveries=[core_future.Instalment(locktime=d, heirs=[
            core_future.HeirShare("luca", 1000, 12.5),
            core_future.HeirShare("john", 1000, 12.5)]) for d in dates],
        level="green", time_left=core_future.time_left_text(dates[0], now),
        check_alive=fk.ts(2027, 9, 1, 22),
        executors=[core_future.ExecutorMark("https://we{}.test".format(i), "verified")
                   for i in range(9)],
        executors_text="9 of 9 received and verified it",
    )
    scene = fv.future_scene(model)
    assert scene["end"] == dates[-1]
    assert [i["date"] for i in scene["instalments"]] == [
        "01/03/2030 22:00 UTC", "01/03/2032 22:00 UTC", "01/03/2035 22:00 UTC"]
    for width in (520, 900):
        pixmap = QPixmap(width, 1200)
        painter = QPainter(pixmap)
        try:
            bottom, hits = fv.paint_future(painter, QRectF(0, 0, width, 1200), scene)
        finally:
            painter.end()
        squares = [b for b, tip in hits if tip.startswith("https://")]
        assert len(squares) == 9 and all(b.right() <= width for b in squares)
        assert bottom < 1200
    texts = fv.scene_texts(scene)
    assert "Delivery 1 · 01/03/2028" in texts and "01/09/2027 22:00 UTC" in texts


def _stage_model(dates, check=None, level="green", passed=False,
                 heirs=(("Luca", 10.0), ("John", 10.0))):
    """A will in force with ``dates`` deliveries (synthetic: multiverse is
    off in the plugin)."""
    now = fk.FIXED_NOW
    return core_future.Future(
        state=core_future.STATE_IN_FORCE, now=now, title="Your will is in force",
        deliveries=[core_future.Instalment(locktime=d, heirs=[
            core_future.HeirShare(name, 1000, pct) for name, pct in heirs]) for d in dates],
        level=level, time_left=core_future.time_left_text(dates[0], now),
        check_alive=check, check_alive_passed=passed,
    )


#: Timelines of the owner's cases (#132 mockup) and harder ones.
TIMELINES = {
    "sparse": ([fk.ts(2028, 3, 1, 22), fk.ts(2030, 3, 1, 22), fk.ts(2032, 3, 1, 22),
                fk.ts(2035, 3, 1, 22)], fk.ts(2027, 12, 1, 22)),
    "dense": ([fk.ts(2027, 3, 1, 22), fk.ts(2027, 5, 1, 22), fk.ts(2027, 7, 1, 22),
               fk.ts(2027, 9, 1, 22)], fk.ts(2027, 1, 15)),
    "single": ([fk.ts(2031, 2, 26, 22)], fk.ts(2030, 11, 28)),
    "near": ([fk.ts(2026, 11, 20, 22)], fk.ts(2026, 11, 1)),
    "no_check": ([fk.ts(2027, 2, 27, 22)], None),
    "long": ([fk.ts(2030, 1, 1), fk.ts(2045, 1, 1), fk.ts(2060, 1, 1), fk.ts(2075, 1, 1)],
             fk.ts(2029, 12, 1)),
}


def _layout(case, width):
    """The time line layout of ``case`` at ``width`` px."""
    dates, check = TIMELINES[case]
    scene = fv.future_scene(_stage_model(dates, check))
    return scene, fv.timeline_layout(scene, 16.0, 0.0, width - 32.0, QApplication.font())


@pytest.mark.parametrize("width", [300, 420, 520, 900, 1600])
@pytest.mark.parametrize("case", sorted(TIMELINES))
def test_timeline_nothing_overlaps(case, width):
    """#132 (owner's mockup): no card, label, pill or legend entry overlaps
    another; the Check Alive pole and flag cross none of them; the leader
    lines cross no other card; everything stays inside the width."""
    _scene, lay = _layout(case, width)
    boxes = fv.timeline_boxes(lay)
    for kind, rect in boxes:
        assert rect.left() >= 16.0 - 0.5 and rect.right() <= width - 16.0 + 0.5, (kind, rect)
    for i, (k1, r1) in enumerate(boxes):
        for k2, r2 in boxes[i + 1:]:
            assert not r1.adjusted(0.5, 0.5, -0.5, -0.5).intersects(r2), (k1, k2)
    axis = lay["axis_y"]
    if lay["pole"]:
        px, top = lay["pole"]
        flag = QRectF(px, top, fv._FLAG, fv._FLAG_H)
        for kind, rect in boxes:
            if kind == "check":
                continue
            crosses = rect.left() < px < rect.right() and rect.top() < axis and rect.bottom() > top
            assert not crosses, kind
            assert not rect.intersects(flag), kind
    for a, b in lay["leaders"]:
        for step in range(19):
            t = step / 20.0
            point = QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
            assert not any(rect.contains(point) for _k, rect in boxes), (case, width)
    if lay["pill"] and lay["pill"]["on_axis"]:
        pill = lay["pill"]["rect"]
        for c in lay["circles"]:
            circle = QRectF(c["x"] - c["r"], axis - c["r"], 2 * c["r"], 2 * c["r"])
            assert not pill.intersects(circle), c["kind"]


def test_timeline_cards_highlight_and_legend():
    """Numbered circles, the next delivery bigger and highlighted, one card
    per delivery linked by a leader, the legend; a single delivery has one
    card titled "Delivery to the heirs · <day>" and no number."""
    scene, lay = _layout("sparse", 1000)
    circles = [c for c in lay["circles"] if c["kind"] in ("next", "later")]
    assert [c["number"] for c in circles] == [1, 2, 3, 4]
    assert [c["kind"] for c in circles] == ["next", "later", "later", "later"]
    assert circles[0]["r"] > circles[1]["r"]
    assert [c["next"] for c in lay["cards"]] == [True, False, False, False]
    assert len(lay["leaders"]) == 4
    assert [c["title"] for c in scene["deliveries"]] == [
        "Delivery 1 · 01/03/2028", "Delivery 2 · 01/03/2030",
        "Delivery 3 · 01/03/2032", "Delivery 4 · 01/03/2035"]
    assert scene["deliveries"][0]["heirs"] == "Luca 10% · John 10%"
    assert scene["deliveries"][0]["time"] == "22:00 UTC"
    assert [kind for kind, _t in scene["legend"]] == ["today", "check", "next", "later", "pill"]
    assert [e["text"] for e in lay["legend"]] == [
        "today", "Check Alive", "next delivery", "later deliveries", "time left"]
    assert lay["pill"]["text"] == "in 1 year and 4 months"
    check = next(i for i in lay["above"] if i["kind"] == "check")
    assert check["colour"] == fv.CHECK_ALIVE_COLOUR
    # Painted: the next card's light fill and the Check Alive colour are there.
    pixmap = QPixmap(1000, 700)
    pixmap.fill(QColor("#ffffff"))
    painter = QPainter(pixmap)
    try:
        fv.paint_future(painter, QRectF(0, 0, 1000, 700), scene)
    finally:
        painter.end()
    image = pixmap.toImage()
    assert _colour_pixels(image, "#eef7ee") > 200
    assert _colour_pixels(image, fv.CHECK_ALIVE_COLOUR) > 20

    scene, lay = _layout("single", 900)
    assert len(lay["cards"]) == 1 and lay["cards"][0]["next"]
    assert scene["deliveries"][0]["title"] == "Delivery to the heirs · 26/02/2031"
    assert [c.get("number") for c in lay["circles"] if c["kind"] == "next"] == [None]
    assert "later" not in [k for k, _t in scene["legend"]]


def test_timeline_grows_with_dense_cards():
    """Four deliveries within one year at 300 px do not fit on one row: the
    cards go in a column and the drawing gets taller (scene_height)."""
    _scene, wide = _layout("dense", 1600)
    _scene, narrow = _layout("dense", 300)
    assert len(wide["leaders"]) == 4 and not narrow["leaders"]
    tops = [c["rect"].top() for c in narrow["cards"]]
    assert tops == sorted(tops) and len(set(tops)) == 4
    scene = fv.future_scene(_stage_model(*TIMELINES["dense"]))
    assert fv.scene_height(scene, 300) > fv.scene_height(scene, 1600)


def test_no_instalment_word_for_the_user():
    """Owner (#132): "delivery", never "instalment" / "rata", in what the
    user reads (the catalogs' active entries)."""
    from babel.messages.pofile import read_po

    root = os.path.join(os.path.dirname(__file__), os.pardir, "bal", "locale")
    with open(os.path.join(root, "bal.pot"), "rb") as f:
        pot = read_po(f)
    assert not [m.id for m in pot if m.id and "nstalment" in str(m.id)]
    with open(os.path.join(root, "it_IT", "LC_MESSAGES", "bal.po"), "rb") as f:
        italian = read_po(f)
    assert not [m.string for m in italian
                if m.id and isinstance(m.string, str)
                and re.search(r"\brat[ae]\b", m.string, re.IGNORECASE)]


# ------------------------------------------------- Tools -> Display (#131)

def _detail_window():
    """The fake window of ``tests/test_import_will_details.py``."""
    from test_import_will_details import _make_fake_bal_window

    window_widget = QWidget()
    bw = _make_fake_bal_window(window_widget)
    bw.window.config = SimpleNamespace()
    bw.window.format_amount = lambda *a, **k: "1.0"
    bw.window.base_unit = "BTC"
    bw.window.format_fiat_and_units = lambda *a, **k: "1.0"
    bw.window.fx = None
    bw.window.format_fee_rate = lambda *a, **k: "1.0"
    bw.window.get_decimal_point = lambda: 8
    bw._keep = window_widget
    return bw


def test_display_note_only_for_the_live_will():
    """The note points to the Future view for the LIVE will only; an
    imported will gets it after a Merge (then the dialog shows the live
    will)."""
    from test_import_will_details import _make_willitems

    from bal.gui.qt.dialogs import WillDetailDialog

    note = ("Will plan view (technical details). The new graphic view is "
            "'Future' in the Will tab.")
    bw = _detail_window()
    live = WillDetailDialog(bw)
    assert live.view_note.text() == note and not live.view_note.isHidden()
    assert live.vlayout.itemAt(0).widget() is live.view_note

    bw.merge_will = lambda will: None
    imported = WillDetailDialog(bw, will=_make_willitems(2))
    assert imported.view_note.isHidden()
    imported.merge_will()
    assert not imported.view_note.isHidden()


def test_tooltip_point_outside_everything(make):
    """No tooltip where nothing is drawn."""
    s = make()
    s.check()
    view = _view(s)
    assert view.tooltip_at(QPointF(-50, -50)) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
