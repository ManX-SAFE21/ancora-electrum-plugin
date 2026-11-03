"""The animated explainer "How BAL PROTOCOL works" (CHANGELOG #162, #226).

Since #226 the window shows the SIMPLE story of the green web page
(``safe21-ancora/explainer/come-funziona-bal-protocol-semplice.html``):
8 steps, with its texts.

Covered:

* the story (``bal/core/explainer.py``, no Qt): the 8 titles are the web
  page's, the years follow the clock (this year, + 3), the block height is
  an estimate from the stored tip (never lower), the strip slides only in
  the steps where time passes, the copies of every step (where, when they
  appear, the one sent first goes into the block, the others wait under
  it and are torn at the end), the fees, the medals 1-2-3, the accepted
  block, "today" reaching the delivery date, the coins, the fee to
  will-executor 2 and the joy marks, the reading time of a step;
* the drawing (``bal/gui/qt/explainer.py``): every step paints at several
  seconds without errors and draws something;
* the window: it starts playing by itself, the arrows and the step numbers
  stop it, play goes on from the step shown, "from the start" goes to step
  1, playing goes to the next step by itself and stops at the last one,
  closing stops every timer; Italian from the catalog (the web page's
  words);
* where it opens: the Will tab's Tools menu (``test_check_button``), the
  button "How will-executors work" of page 3 of the BASIC wizard and of the
  will-executor window (REAL
  ``BalWindow`` of ``test_gui_basic_wizard``: three fake will-executors,
  each with its own fee address, nothing reaches the network).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_explainer.py
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication
from test_gui_basic_wizard import make, open_wizard, press_next  # noqa: F401

from bal import i18n
from bal.core import explainer as story
from bal.gui.qt import explainer as ex
from bal.gui.qt.basic_wizard import ExecutorsPage

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)


# ------------------------------------------------------------- the story

def test_the_eight_steps_of_the_simple_page():
    """The titles of the web page's simple story, in its order."""
    assert story.STEP_COUNT == 8
    assert [title for title, _text in story.STEPS] == [
        "Write your will: choose the heirs and the date",
        "One copy for each will-executor, valid from the date you chose",
        "Each will-executor keeps a copy, with its own fee",
        "While you are here, nothing is sent: the bitcoin stay yours",
        "The delivery date arrives",
        "On the chosen date, the copies are sent",
        "The block accepts one copy only: the one sent first",
        "Bitcoin to the heirs, fee to the will-executor; then the others lapse",
    ]


def test_years_follow_the_clock():
    """This year and the delivery 3 years later; next year everything
    moves by one year."""
    assert story.years(datetime(2026, 10, 18)) == (2026, 2029)
    assert story.years(datetime(2027, 1, 1)) == (2027, 2030)


def test_block_height_is_an_estimate_from_the_stored_tip():
    """144 blocks a day since the stored moment; never lower than the
    stored tip (a computer clock in the past)."""
    t0 = datetime.fromtimestamp(story.T0, timezone.utc)
    assert story.tip_estimate(t0) == story.TIP0
    later = datetime.fromtimestamp(story.T0 + 10 * 86400, timezone.utc)
    assert story.tip_estimate(later) == story.TIP0 + 1440
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert story.tip_estimate(past) == story.TIP0


def test_the_strip_slides_only_when_time_passes():
    """As on the web page; from step 7 the accepted block keeps one number."""
    slides = [story.slide(n) for n in range(story.STEP_COUNT)]
    assert slides == [0, 0, 0, 1, 5, 5, 6, 6]
    assert {story.scene(n, 9)["next_block_index"] for n in (6, 7)} == {story.K + 6}


def _envs(n, t=9.0):
    return story.scene(n, t)["envelopes"]


def test_the_copies_of_every_step():
    """Where the copies are, one per will-executor; the fees."""
    assert _envs(0) == {}
    # step 2: three copies next to the computer, one after another
    assert all(e[:2] == story.home(i) for i, e in _envs(1).items())
    a = [_envs(1, 0.9)[i][2] for i in range(3)]
    assert 0 < a[0] < 1 and a[1:] == [0.0, 0.0]
    # step 3: they fly to the will-executors, then the fees appear (2.5 s)
    assert {i: e[:2] for i, e in _envs(2).items()} == {
        i: story.place(i) for i in range(3)}
    assert story.scene(2, 2.0)["tips"] == 0 and story.scene(2, 4.0)["tips"] == 1
    assert story.scene(2, 9)["tip_note"] == 1
    # the screen: the sending signal, then the copies with their fee
    assert [n for n, _a in story.scene(2, 9)["screens"]] == ["send", "tips"]
    assert [round(a) for _n, a in story.scene(2, 9)["screens"]] == [0, 1]
    # steps 4 and 5: still with the will-executors, with the fee
    for n in (3, 4):
        assert {i: e[:2] for i, e in _envs(n).items()} == {
            i: story.place(i) for i in range(3)}
        assert story.scene(n, 0)["tips"] == 1


def test_sending_the_block_and_the_delivery():
    """Will-executor 2 sends first and wins; the others wait under the
    block and are torn only after the bitcoin went to the heirs."""
    e = _envs(5)
    assert [e[i][:2] for i in (1, 0, 2)] == [story.BLOCK, *story.NEAR]
    # the medals 1, 2, 3 when each copy arrives, and they stay on
    assert story.scene(5, 0.5)["medals"][1][1] == 0
    assert {i: m[0] for i, m in story.scene(5, 9)["medals"].items()} == {
        1: 0, 0: 1, 2: 2}
    assert all(m[1] == 1 for m in story.scene(7, 0)["medals"].values())
    # step 7: the block accepts it
    assert story.scene(5, 9)["new_block"] == 0
    assert story.scene(6, 0)["new_block"] == 0
    assert story.scene(6, 9)["new_block"] == 1
    # step 8: coins to the heirs, the fee to will-executor 2, the joy...
    s = story.scene(7, 4.0)
    assert s["coins"] == list(story.COIN_HEIRS)
    assert s["fee"][0] == (story.SX + 75, story.row_y(1) + 13)
    assert s["fee_tag"][0] == 1 and s["joy"] == 1
    # ...and only afterwards the other two copies are torn
    assert s["refused"] == 0 and sorted(s["envelopes"]) == [0, 1, 2]
    assert s["tears"] == [(story.NEAR[0], story.TEAR_AT),
                          (story.NEAR[1], story.TEAR_AT)]
    s = story.scene(7, 9)
    assert s["refused"] == 1 and list(s["envelopes"]) == [1]


def test_today_reaches_the_delivery_date():
    """At step 5 "today" moves to the delivery date and its label goes."""
    assert story.scene(3, 9)["today_x"] == story.TODAY_X
    assert story.scene(4, 9)["today_x"] == story.DUE_X
    assert story.scene(4, 9)["today_label"] == 0
    assert {story.scene(n, 9)["due_x"] for n in range(8)} == {story.DUE_X}


def test_reading_time_of_a_step():
    """The web page's pace: 3 words a second plus 2.5 s, at least 9 s, all
    halved."""
    assert story.step_seconds("a", "b") == 4.5
    assert story.step_seconds("a", " ".join(["w"] * 59)) == pytest.approx(11.25)


# ------------------------------------------------------------- the drawing

def test_every_step_paints():
    """Every step at several seconds, without errors; something drawn."""
    texts = ex.translated_texts()
    for n in range(story.STEP_COUNT):
        for t in (0.0, 0.8, 1.9, 3.3, 5.5, 7.0):
            img = QImage(515, 258, QImage.Format.Format_ARGB32)
            img.fill(QColor("#123456"))
            p = QPainter(img)
            ex.paint_scene(p, QRectF(0, 0, 515, 258), story.scene(n, t), texts)
            p.end()
            assert img.pixelColor(257, 129) != QColor("#123456")


# ------------------------------------------------------------- the window

class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _dialog(autoplay=True):
    clock = _Clock()
    d = ex.ExplainerDialog(clock=clock, autoplay=autoplay)
    d.show()
    _APP.processEvents()
    return d, clock


def test_window_plays_by_itself_and_the_controls():
    d, clock = _dialog()
    try:
        assert d.playing and d.play_timer.isActive()
        assert d.play_button.text() == "❚❚"
        assert d.canvas.step == 0
        assert d.title.text() == "Write your will: choose the heirs and the date"
        assert len(d.dots) == 8
        d.next_button.click()                       # an arrow stops playing
        assert d.canvas.step == 1 and not d.playing
        assert d.play_button.text() == "▶"
        d.dots[5].click()                           # a step number too
        assert d.canvas.step == 5 and not d.playing
        assert d.text.text().startswith("Each will-executor takes its signed copy")
        d.play_button.click()                       # play from the step shown
        assert d.playing and d.canvas.step == 5
        d.restart_button.click()                    # from the start
        assert d.playing and d.canvas.step == 0
        d.back_button.click()                       # back from 1 goes to 8
        assert d.canvas.step == 7 and not d.playing
    finally:
        d.close()
    assert not d.play_timer.isActive() and not d.canvas.timer.isActive()


def test_playing_goes_on_and_stops_at_the_last_step():
    d, clock = _dialog()
    try:
        d._next_by_itself()
        assert d.canvas.step == 1 and d.playing
        d.go(7, stop=False)
        d._next_by_itself()
        assert d.canvas.step == 7 and not d.playing
    finally:
        d.close()


def test_strip_glides_from_where_it_was():
    """A jump to any step starts the moving parts from what was shown."""
    d, clock = _dialog(autoplay=False)
    try:
        d.go(6)
        clock.now += 0.01
        s = d.canvas.current_scene()
        assert s["chain_offset"] < story.BLK * story.slide(6)
        clock.now += 5
        s = d.canvas.current_scene()
        assert s["chain_offset"] == pytest.approx(story.BLK * story.slide(6))
    finally:
        d.close()


def test_italian_texts():
    class _Catalogs:
        """The repo's catalogs, compiled as the ZIP has them."""

        def read_file(self, path):
            from build_zip import compile_catalog
            root = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
            return compile_catalog(os.path.join(root, path).replace(".mo", ".po"))

    try:
        i18n.set_language(_Catalogs(), "it_IT")
        d, clock = _dialog(autoplay=False)
        try:
            assert d.windowTitle() == "Come funziona BAL PROTOCOL"
            assert d.title.text() == "Scrivi il testamento: scegli gli eredi e la data"
            d.dots[7].click()
            assert d.title.text() == ("Bitcoin agli eredi, mancia al "
                                      "will-executor; poi le altre invalidate")
            assert d.text.text().startswith("Appena la copia è accettata")
            assert d.eyebrow.text() == "Passo 8 di 8"
            texts = ex.translated_texts()
            assert texts["fee"] == "+ mancia"
            assert texts["chain"] == "Blockchain di Bitcoin"
        finally:
            d.close()
    finally:
        i18n.set_language(None, None)


# ------------------------------------------------------------- where it opens

def test_wizard_page_3_and_executor_window_open_it(make):  # noqa: F811
    """The button next to the explanation of the will-executors (page 3 of the
    BASIC wizard) and in the will-executor window opens the window; a
    second press shows the same one again."""
    s, bw = make(mode="basic")
    dialog = open_wizard(bw)
    try:
        press_next(dialog)
        page = press_next(dialog)
        assert isinstance(page, ExecutorsPage)
        page.explainer_button.click()
        _APP.processEvents()
        first = bw.explainer_dialog
        assert first is not None and first.isVisible() and first.playing
        bw.show_willexecutor_dialog()
        _APP.processEvents()
        bw.willexecutor_dialog.explainer_button.click()
        _APP.processEvents()
        assert bw.explainer_dialog is first and first.canvas.step == 0
    finally:
        if getattr(bw, "explainer_dialog", None):
            bw.explainer_dialog.close()
        if getattr(bw, "willexecutor_dialog", None):
            bw.willexecutor_dialog.close()
        dialog.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
