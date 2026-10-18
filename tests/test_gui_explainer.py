"""The animated explainer "How BAL PROTOCOL works" (CHANGELOG #162).

Covered:

* the story (``bal/core/explainer.py``, no Qt): the years follow the
  clock (this year, + 3, + 7), the block height is an estimate from the
  stored tip (never lower), the strip slides only in the steps where time
  passes, the copies of every step (where, how many, red, torn), the
  accepted block, the delivery date that moves, heir 2's new address, the
  coins and the fee, the reading time of a step;
* the drawing (``bal/gui/qt/explainer.py``): every step paints at several
  seconds without errors and draws something;
* the window: it starts playing by itself, the arrows and the step numbers
  stop it, play goes on from the step shown, "from the start" goes to step
  1, playing goes to the next step by itself and stops at the last one,
  the text of the step (the new year in steps 9 and 10, the note of step
  17), closing stops every timer; Italian from the catalog;
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

def test_years_follow_the_clock():
    """This year, the delivery 3 years later, the postponed one 7 years
    later; next year everything moves by one year."""
    assert story.years(datetime(2026, 10, 18)) == (2026, 2029, 2033)
    assert story.years(datetime(2027, 1, 1)) == (2027, 2030, 2034)


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
    """Steps 5, 6, 7, 9, 10 and 12 slide one block; from step 12 the
    accepted block keeps one number."""
    slides = [story.slide(n) for n in range(story.STEP_COUNT)]
    assert slides == [0, 0, 0, 0, 1, 2, 3, 3, 4, 5, 5, 6, 6, 6, 6, 6, 6]
    assert {story.scene(n, 9)["next_block_index"] for n in range(11, 17)} == {
        story.K + 6}


def _envs(n, t=9.0):
    return story.scene(n, t)["envelopes"]


def test_the_copies_of_every_step():
    """Where the copies are, how many, which are red."""
    assert _envs(0) == {}
    # step 2: three copies next to the computer, step 3: at the will-executors
    assert all(e[:2] == story.home(i) for (k, i), e in _envs(1).items())
    assert {(k, i): e[:2] for (k, i), e in _envs(2).items()} == {
        (0, i): story.place(i, 0) for i in range(3)}
    # steps 6 and 7: one more copy on the right each time, none red
    assert sorted(_envs(5)) == [(k, i) for k in range(2) for i in range(3)]
    assert sorted(_envs(6)) == [(k, i) for k in range(3) for i in range(3)]
    assert _envs(6)[(2, 1)][:2] == story.place(1, 2)
    assert not any(e[3] for e in _envs(6).values())
    # step 8: the old copies turn red at 0.9 s and stay in their place
    assert not any(e[3] for e in _envs(7, 0.5).values())
    assert all(e[3] for e in _envs(7, 1.0).values())
    # step 9: the new copies on the right, not red
    e = _envs(8)
    assert e[(3, 0)][:2] == story.place(0, 3) and e[(3, 0)][3] == 0
    # step 11: the newest copies fly to the block and next to it
    e = _envs(10)
    assert [e[(3, i)][:2] for i in range(3)] == [story.BLOCK, *story.NEAR]
    # step 13: the two others are torn
    assert [pos for pos, _start in story.scene(12, 2)["tears"]] == list(story.NEAR)
    # step 14: after the delivery the red copies go away
    assert all(e[2] == 1 for e in _envs(13, 1.0).values())
    assert all(e[2] == 0 for e in _envs(13, 9.0).values())
    # step 17: two will-executors off, only the third sends
    s = story.scene(16, 1.0)
    assert s["off"] == (0, 1)
    assert [round(s["envelopes"][(3, i)][2], 2) for i in range(2)] == [0.25, 0.25]


def test_dates_block_heir_and_money():
    """The delivery marker, the accepted block, heir 2, coins and fee."""
    d0 = story.DUE_X[0]
    assert story.scene(4, 9)["due_x"] == d0
    assert story.scene(5, 9)["due_x"] == d0 - story.DAY          # one day earlier
    assert story.scene(6, 9)["due_x"] == d0 - 2 * story.DAY
    assert story.scene(7, 9)["due_cross"] == 1.0                  # red cross on it
    assert story.scene(8, 0.5)["due_year"] == 1
    assert story.scene(8, 2.0)["due_year"] == 2                   # postponed
    assert story.scene(9, 9)["today_x"] == story.DUE_X[1]         # the date arrives
    assert story.scene(10, 9)["new_block"] == 0
    assert story.scene(11, 9)["new_block"] == 1                    # accepted
    assert story.scene(12, 9)["refused"] == 1
    assert not story.scene(5, 9)["heir2_new"]
    assert story.scene(6, 1.0)["heir2_new"] is False
    assert story.scene(6, 2.0)["heir2_new"] and story.scene(6, 2.0)["heir2_flash"]
    assert story.scene(13, 9)["coins"] == list(story.COIN_HEIRS)
    s = story.scene(14, 9)
    assert s["fee"][0] == (story.SX + 75, story.row_y(0) + 13)
    assert story.scene(16, 9)["fee_tag"][0] == 2                   # the third one
    assert story.scene(15, 2)["question"] == 1


def test_reading_time_of_a_step():
    """4.4 words a second plus 2 s, at least 9 s (6 s for the question)."""
    assert story.step_seconds("a", "b", 0) == 9.0
    assert story.step_seconds("a", " ".join(["w"] * 87), 5) == pytest.approx(22.0)
    assert story.step_seconds("a", "b", story.QUESTION_STEP) == 6.0


# ------------------------------------------------------------- the drawing

def test_every_step_paints():
    """Every step at several seconds, without errors; something drawn."""
    texts = ex.translated_texts()
    for n in range(story.STEP_COUNT):
        for t in (0.0, 0.8, 1.9, 3.3, 7.0):
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
        assert d.canvas.step == 0 and d.title.text() == "Write your will"
        d.next_button.click()                       # an arrow stops playing
        assert d.canvas.step == 1 and not d.playing
        assert d.play_button.text() == "▶"
        d.dots[8].click()                           # a step number too
        assert d.canvas.step == 8 and not d.playing
        y2 = story.years()[2]
        assert str(y2) in d.text.text()             # the new year in step 9
        d.play_button.click()                       # play from the step shown
        assert d.playing and d.canvas.step == 8
        d.restart_button.click()                    # from the start
        assert d.playing and d.canvas.step == 0
        d.back_button.click()                       # back from 1 goes to 17
        assert d.canvas.step == 16 and not d.playing
        assert d.note.isVisible() and "Advanced Mode" in d.note.text()
        d.dots[15].click()
        assert not d.note.isVisible()
    finally:
        d.close()
    assert not d.play_timer.isActive() and not d.canvas.timer.isActive()


def test_playing_goes_on_and_stops_at_the_last_step():
    d, clock = _dialog()
    try:
        d._next_by_itself()
        assert d.canvas.step == 1 and d.playing
        d.go(16, stop=False)
        d._next_by_itself()
        assert d.canvas.step == 16 and not d.playing
    finally:
        d.close()


def test_strip_glides_from_where_it_was():
    """A jump to any step starts the moving parts from what was shown."""
    d, clock = _dialog(autoplay=False)
    try:
        d.go(12)
        clock.now += 0.01
        s = d.canvas.current_scene()
        assert s["chain_offset"] < story.BLK * story.slide(12)
        clock.now += 5
        s = d.canvas.current_scene()
        assert s["chain_offset"] == pytest.approx(story.BLK * story.slide(12))
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
            assert d.title.text() == "Scrivi il testamento"
            d.dots[5].click()
            assert d.title.text() == "L'importo cambia"
            assert d.eyebrow.text() == "Passo 6 di 17"
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
