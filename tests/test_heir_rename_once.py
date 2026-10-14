"""An heir only renamed is said ONCE, then the will is coherent (#135).

Owner's ZIP test: after renaming an heir (luigi -> "luigi modifica nome":
same address, share and date) EVERY Check, and the wallet opening, said
"Heir renamed: luigi -> luigi modifica nome: preparing a new will", rebuilt
the very same transactions ("not needed" to sign or send) and changed
nothing; the heirs box and the "What changes" box showed the old name.

Root cause (reproduced here): ``Will.update_will`` writes the new heirs
only on the items the rebuild makes again (same txid, #122). The copy of a
will-executor the rebuild does not make (here a deselected one, never
dropped: it is still VALID and sent) kept the old name, so the check by
name saw "luigi" missing at every Check.

Fix: a pure rename (same address, same amount, one candidate)
is recognised before the check (``Will.sync_heir_names``): only the names
in the items' stored heirs change, the will is saved, nothing is rebuilt.
The transactions, signatures and statuses do not change.

``tests/flowkit.py`` wallets: three fake will-executors with their own fee
addresses, the test password, a fixed clock; the wallet counts as
synchronized.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_heir_rename_once.py
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core.will import Will

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
WE2 = "https://we2.test"
RENAMED = "Heir renamed: bob → bob renamed: only the name changes, the will stays the same."


@pytest.fixture
def make(monkeypatch):
    """A will sent to three will-executors; then we2 deselected (its copy
    stays VALID and sent) and bob renamed (same address, share, date)."""
    fk.ensure_event_loop()
    made = []

    def _make(deselect=True):
        """The session (see the fixture)."""
        s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
        s.world.add_executor()
        made.append(s)
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.check()
        if deselect:
            s.world.executors[WE2]["selected"] = False
            s.check()
        rename(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def rename(s):
    """bob -> "bob renamed" in the Heirs tab (same address, share, date)."""
    share = s.bw.heirs["bob"][1]
    del s.bw.heirs["bob"]
    s.bw.heirs.save()
    s.bw.heirs["bob renamed"] = [fk.HEIR_ADDR["bob"], share,
                                 s.bw.will_settings["locktime"]]


def names(will):
    """The heir names of every item (no will-executor fee entries)."""
    return {sorted(n for n in (w["heirs"] if isinstance(w, dict) else w.heirs)
                   if not n.startswith('w!ll3x3c"'))[1] for w in will.values()}


def view_texts(snapshot):
    """The texts the Check window's report view draws for ``snapshot``."""
    from PyQt6.QtWidgets import QLabel

    from bal.gui.qt.widgets import BuildReportView
    view = BuildReportView()
    view.render(snapshot)
    return [w.text() for w in view.findChildren(QLabel)]


def test_rename_said_once_then_clean(make):
    """First Check: one "renamed" line, nothing rebuilt, signed or sent;
    every item (the deselected copy too) and the saved will carry the new
    name. Second Check: no change line at all."""
    s = make()
    txids = set(s.bw.willitems)
    pushed = len(s.world.pushed)
    first = s.check()
    snap = first._report().snapshot()
    assert snap["boxes"]["changes"] == [RENAMED]          # the log's line
    # ONE box (owner): "What changes" with just the rename row, no signing.
    assert snap["changes_for"] == "check"
    assert [list(r) for r in snap["changes"]] == [[
        "Heirs", "renamed: bob → <b>bob renamed</b> (only the name changes, already saved: no need to sign)", None]]
    texts = view_texts(snap)
    assert "<b>What changes compared with the will in force</b>" in texts
    assert "What changed" not in texts                     # the old box is not drawn
    assert not any(t.startswith("Check the list: by signing") for t in texts)
    steps = {name: state for name, state, _note in snap["steps"]}
    assert steps["prepare"] == "skip" and steps["sign"] == "skip" and steps["send"] == "skip"
    assert set(s.bw.willitems) == txids and len(s.world.pushed) == pushed
    assert names(s.bw.willitems) == {"bob renamed"}
    assert names(s.wallet.db.get_dict("will")) == {"bob renamed"}
    second = s.check()
    snap = second._report().snapshot()
    assert snap["boxes"]["changes"] == [] and "changes" not in snap


def test_after_reopening_the_window(make):
    """The new names were saved: a window opened again on the wallet finds
    nothing to say."""
    s = make()
    s.check()
    s.reload_window()
    assert names(s.bw.willitems) == {"bob renamed"}
    snap = s.check()._report().snapshot()
    assert snap["boxes"]["changes"] == []


def test_postpone_after_a_rename_has_no_stale_row(make):
    """A later date after the rename: the "What changes" box has no
    "renamed: bob → …" row (the delivery, and we2: deselected, it holds the
    will in force but will not get the new one)."""
    s = make()
    s.check()                                   # the rename, said once
    s.set_date(fk.LATER)
    dlg = s.check(cancel_on=("invalidat",))
    snap = dlg._report().snapshot()
    assert snap["changes_for"] == "invalidate"
    assert [r[0] for r in snap["changes"]] == ["Delivery", "Will-executors"]
    assert not any("renamed" in r[1] for r in snap["changes"])


def test_only_a_pure_rename_is_synchronized():
    """Same address but another share, or two candidates: nothing changes
    (the usual check and rebuild handle it). Another date: the name is
    written, the usual check still finds the date."""
    def item(**heirs):
        return SimpleNamespace(heirs=dict(heirs), get_status=lambda f: f == "VALID")

    def tab(**heirs):
        return dict(heirs)

    a, b = "tb1qalice", "tb1qbob"
    will = {"t": item(alice=[a, "60%", "D"], bob=[b, "40%", "D"])}
    assert Will.sync_heir_names(will, tab(alice=[a, "60%", "D"], bobby=[b, "40%", "D"])) == [
        ("bob", "bobby")]
    assert will["t"].heirs == {"alice": [a, "60%", "D"], "bobby": [b, "40%", "D"]}
    will = {"t": item(alice=[a, "60%", "D"], bob=[b, "40%", "D"])}
    assert Will.sync_heir_names(will, tab(alice=[a, "60%", "D"], bobby=[b, "50%", "D"])) == []
    # Another date: renamed (names only); the usual check then sees the date.
    will2 = {"t": item(alice=[a, "60%", "D"], bob=[b, "40%", "D"])}
    assert Will.sync_heir_names(will2, tab(alice=[a, "60%", "D"], bobby=[b, "40%", "E"])) == [
        ("bob", "bobby")]
    assert Will.sync_heir_names(will, tab(alice=[a, "60%", "D"], x=[b, "40%", "D"],
                                          y=[b, "40%", "D"])) == []
    assert "bob" in will["t"].heirs
