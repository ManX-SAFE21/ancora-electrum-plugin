"""The shares recalculated in proportion: a box only when they change (#135).

Owner: "Attention: the heirs' percentages add up to 130%…" was shown at
every Check and window, alarming when nothing changed. Then the Check
window showed a box with a calm title ("How the shares are divided") only
when the shares as written change compared with the will in force, or
there is no will in force yet; otherwise one quiet grey line by the heirs
("Shares recalculated in proportion: …"). The explanation always stays in
"Details (log)". #161 (owner): the box is no longer drawn (too much
information); the report still holds the explanation.

Also (#135, owner): the "renamed … already saved" row and the Cancel
sentence that does not say a rename is undone; the will-executors box's
note about the deselected ones gets the whole width (it was cut off).

``tests/flowkit.py`` wallets: three fake will-executors with their own fee
addresses, the test password, a fixed clock; the wallet counts as
synchronized.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_shares_note.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"


@pytest.fixture
def make(monkeypatch):
    """A wallet whose heirs add up to 110% (alice 60%, bob 50%)."""
    fk.ensure_event_loop()
    made = []

    def _make():
        """The session (see the fixture)."""
        s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.set_heirs(alice="60%", bob="50%")
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def texts(snapshot):
    """The texts the Check window's report view draws."""
    from bal.gui.qt.widgets import BuildReportView
    view = BuildReportView()
    view.render(snapshot)
    return [w.text() for w in view.findChildren(QLabel)]


def test_box_for_a_first_will_then_a_quiet_line(make):
    """First will: the explanation is in the report (and the log), but
    #161 (owner) the window no longer draws its box ("too much
    information"). Next Check (nothing changed): only the grey line, and
    never "Attention"."""
    s = make()
    snap = s.check()._report().snapshot()
    text, prominent = snap["amounts_note"]
    assert prominent is True
    assert text.startswith("The percentages you wrote add up to 110%.")
    assert snap["boxes"]["warnings"] == []
    drawn = texts(snap)
    assert "How the shares are divided" not in drawn and "Attention" not in drawn
    assert text not in drawn

    snap = s.check()._report().snapshot()
    text, prominent = snap["amounts_note"]
    assert prominent is False
    assert text == "Shares recalculated in proportion: alice 54.5% · bob 45.5%"
    assert snap["boxes"]["warnings"] == []
    drawn = texts(snap)
    assert "How the shares are divided" not in drawn and "Attention" not in drawn
    assert text in drawn


def test_after_a_change_of_shares_the_next_check_is_quiet(make):
    """A will in force (60/40), then 60/50: the Check that changes the
    shares has the box; the next one (the older will still held at a later
    date is not the reference) has only the grey line."""
    s = make()
    s.set_heirs(alice="60%", bob="40%")
    s.check()
    s.set_heirs(alice="60%", bob="50%")
    assert s.check()._report().snapshot()["amounts_note"][1] is True
    assert s.check()._report().snapshot()["amounts_note"][1] is False


def test_rename_only_is_quiet_a_new_share_is_not(make):
    """A rename (names do not count) keeps the quiet line; a new share
    brings the box back."""
    s = make()
    s.check()
    share = s.bw.heirs["bob"][1]
    del s.bw.heirs["bob"]
    s.bw.heirs.save()
    s.bw.heirs["bob renamed"] = [fk.HEIR_ADDR["bob"], share, s.bw.will_settings["locktime"]]
    snap = s.check()._report().snapshot()
    assert snap["amounts_note"][1] is False
    s.bw.heirs["bob renamed"] = [fk.HEIR_ADDR["bob"], "70%", s.bw.will_settings["locktime"]]
    snap = s.check(cancel_on=("sign your will",))._report().snapshot()
    assert snap["amounts_note"][1] is True


def test_cancel_sentence_with_a_saved_rename():
    """The box has a rename (already saved) and something to sign: the
    sentence says Cancel changes nothing ELSE."""
    from bal.core.build_report import BuildReport
    report = BuildReport()
    report.set_changes([("Heirs", "renamed: a → <b>b</b>", None),
                        ("Miner fee", "13 → <b>14 sat/vB</b>", None)], "sign", renamed=True)
    drawn = texts(report.snapshot())
    assert any(t.endswith("nothing else changes (the new names are already saved).")
               for t in drawn)
    report.set_changes([("Miner fee", "13 → <b>14 sat/vB</b>", None)], "sign")
    drawn = texts(report.snapshot())
    assert any(t.endswith("press Cancel at the password request: nothing changes.")
               for t in drawn)


def test_deselected_note_gets_the_whole_width():
    """The will-executors box's note about deselected ones is not
    right-aligned (it was cut off in the real window)."""
    from PyQt6.QtWidgets import QFrame

    from bal.core.build_report import BuildReport
    from bal.gui.qt.widgets import BuildReportView
    report = BuildReport()
    report.set_executor("https://we0.test", "verified")
    report.set_executor("https://we3.test", "deselected")
    view = BuildReportView()
    view.render(report.snapshot())
    note = next(w for w in view.findChildren(QLabel) if "is deselected, so" in w.text())
    assert note.wordWrap()
    frame = next(f for f in view.findChildren(QFrame) if f.objectName() == "balGroup"
                 and note in f.findChildren(QLabel))
    layout = frame.layout()
    footer = layout.itemAt(layout.count() - 1)
    assert int(footer.alignment().value) == 0


def test_real_shares_row_says_it_once(make):
    """A Check whose "What changes" box has the "Real shares" row (60/40 ->
    60/50): no separate "How the shares are divided" box; the row's note
    has the total."""
    s = make()
    s.set_heirs(alice="60%", bob="40%")
    s.check()
    s.set_heirs(alice="60%", bob="50%")
    snap = s.check(cancel_on=("sign your will",))._report().snapshot()
    shares = [r for r in snap["changes"] if r[0] == "Real shares"]
    assert shares and shares[0][2].startswith("The percentages you wrote add up to 110%:")
    drawn = texts(snap)
    assert "How the shares are divided" not in drawn
    assert not any(t.startswith("Shares recalculated in proportion") for t in drawn)


def test_quiet_line_without_an_explanation(make, monkeypatch):
    """Owner's ZIP test: a rename-only Check with heirs at 110% where the
    amounts check said nothing: the grey line is there anyway (the window
    has no Heirs section: it is drawn in the box area)."""
    from bal.core.will import Will
    s = make()
    s.check()
    monkeypatch.setattr(Will, "check_amounts", staticmethod(lambda *a, **k: None))
    share = s.bw.heirs["bob"][1]
    del s.bw.heirs["bob"]
    s.bw.heirs.save()
    s.bw.heirs["bob renamed"] = [fk.HEIR_ADDR["bob"], share, s.bw.will_settings["locktime"]]
    snap = s.check()._report().snapshot()
    assert snap["heirs"] is None and snap["changes_for"] == "check"
    assert snap["amounts_note"] == [
        "Shares recalculated in proportion: alice 54.5% · bob renamed 45.5%", False]
    assert snap["amounts_note"][0] in texts(snap)
