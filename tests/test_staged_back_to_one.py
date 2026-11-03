"""Back to one delivery needs the invalidation on the blockchain (CHANGELOG #188).

Owner decision (2026-10-24, `context/STUDY-2026-10-24-staged-will-safety.md`):
going back from several deliveries to one is a change of the TYPE of will.
The copies in several deliveries held by the will-executors are
invalidated on the blockchain first (a network fee; the coins spent
together), then the one-date will is prepared: the road of a postpone
(#134). Before, a one-date will dated one day earlier was built over them
(pre-empted by one day only; in the R-2 case not even that). The owner is
warned before the first table of a wallet is saved; a cancelled
invalidation leaves the copies in force and the window of the deliveries
offers them back.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address; the fixtures of ``test_gui_deliveries.py`` (ADVANCED,
luca and john, the owner's table 10/10, 10/10, 30/30 %).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_staged_back_to_one.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
import test_gui_deliveries as tgd
from PyQt6.QtWidgets import QApplication

from bal.core import delivery, futures
from bal.core import will_changes as wc
from bal.core.will import StagedWillRemovedException, Will
from bal.engine import context, instalments
from bal.gui.qt import deliveries

_APP = QApplication.instance() or QApplication(sys.argv)

make = tgd.make
answers = tgd.answers
D1, D2, D3 = tgd.D1, tgd.D2, tgd.D3
SIGN = ("sign your will",)
INVALIDATE = ("invalidat",)
ROW = "Back to one delivery: invalidating the will in several deliveries"


def valid(s):
    """The VALID copies."""
    return {w: i for w, i in s.bw.willitems.items() if i.get_status("VALID")}


def step(s):
    """The record of the last Check."""
    return s.recorder.steps[-1]


def rows(s):
    """The rows the last Check showed (tags removed)."""
    return [fk.strip(r) for r in step(s).get("rows", [])]


def result(s):
    """The final sentence of the last Check."""
    return (step(s).get("report") or {}).get("result", ["", ""])[1]


def sent_staged(make):
    """The owner's table, signed, sent and verified (39 copies)."""
    s = make()
    tgd.owner_table(s.bw)
    s.check()
    assert len(valid(s)) == 39 and not s.world.broadcast_log
    return s


# ------------------------------------------------------------- the core

def test_staged_in_force_and_the_rule(make):
    """``Will.staged_in_force``: the copies of deliveries 2-3 in force (36
    of 39); none with one date; none for drafts. ``check_staged_will_removed``
    raises only with a one-delivery plan."""
    s = sent_staged(make)
    staged = Will.staged_in_force(s.bw.willitems, s.wallet)
    assert len(staged) == 36
    plan_one = {"luca": [tgd.ADDR["luca"], "50%", D1], "john": [tgd.ADDR["john"], "50%", D1]}
    with pytest.raises(StagedWillRemovedException) as e:
        Will.check_staged_will_removed(s.bw.willitems, plan_one, s.wallet)
    assert e.value.deliveries == 3
    # The table still there: the plan has three deliveries, nothing raised.
    Will.check_staged_will_removed(
        s.bw.willitems, instalments.effective_heirs(s.bw), s.wallet)
    t = make()
    t.check()
    assert Will.staged_in_force(t.bw.willitems, t.wallet) == {}
    d = make()
    tgd.owner_table(d.bw)
    d.check(cancel_on=SIGN)                     # drafts only: never in force
    assert Will.staged_in_force(d.bw.willitems, d.wallet) == {}


# ------------------------------------------------------- the Check flow

def test_back_to_one_asks_the_invalidation_first(make):
    """The table removed, Check: "Back to one delivery: invalidating...",
    the Invalidate step; cancelled there: nothing built, nothing sent, the
    39 copies in force, the date untouched, the sentence says so."""
    s = sent_staged(make)
    context.clear_instalments(s.bw)
    s.check(cancel_on=INVALIDATE)
    assert ROW in " ".join(rows(s)), rows(s)
    assert step(s)["pushed"] == [] and step(s)["broadcasts"] == []
    assert len(valid(s)) == 39
    assert s.bw.will_settings["locktime"] == D1
    assert "was not invalidated and stays in force" in result(s)
    assert "Heir" not in " ".join(r for r in rows(s) if "removed" in r)


def test_cancelled_it_asks_again_and_builds_nothing(make):
    """A second Check after the Cancel: the same question, still nothing
    built (no one-date will over the copies in force)."""
    s = sent_staged(make)
    context.clear_instalments(s.bw)
    s.check(cancel_on=INVALIDATE)
    s.check(cancel_on=INVALIDATE)
    assert ROW in " ".join(rows(s))
    assert step(s)["pushed"] == [] and not s.world.broadcast_log
    assert len(valid(s)) == 39


def test_invalidation_done_then_the_one_date_will(make):
    """The owner accepts: the invalidation is broadcast, the copies in
    several deliveries are dead, the one-date will is prepared and sent
    (3 copies); the next Check is clean."""
    s = sent_staged(make)
    context.clear_instalments(s.bw)
    s.check()
    assert [k for k, _t in s.world.broadcast_log] == ["invalidation(dialog)"]
    assert len(step(s)["pushed"]) == 3
    now = valid(s)
    assert len(now) == 3
    assert all(wc.delivery_number(i) == 1 for i in now.values())
    assert Will.staged_in_force(s.bw.willitems, s.wallet) == {}
    s.check()
    assert [q[0] for q in step(s)["questions"]] == []
    assert len(valid(s)) == 3


def test_drafts_only_table_removed_is_free(make):
    """The table never signed nor sent (drafts only): removed, the
    one-date will is simply the one to sign; no invalidation."""
    s = make()
    tgd.owner_table(s.bw)
    s.check(cancel_on=SIGN)
    context.clear_instalments(s.bw)
    s.check(cancel_on=SIGN)
    assert ROW not in " ".join(rows(s))
    assert [q[0] for q in step(s)["questions"]] == ["password"]
    assert not s.world.broadcast_log


def test_r2_amounts_equal_to_the_first_shares(make):
    """R-2: the heirs' own amounts equal delivery 1's cells (10 % each):
    before, the one-date will kept delivery 1's date and both wills were
    valid the same day. Now the invalidation first."""
    s = make()
    for name in list(s.bw.heirs):
        s.bw.heirs[name] = [s.bw.heirs[name][0], "10%", D1]
    s.bw.heirs.save()
    tgd.owner_table(s.bw)
    s.check()
    context.clear_instalments(s.bw)
    s.check(cancel_on=INVALIDATE)
    assert ROW in " ".join(rows(s))
    assert len(valid(s)) == 39


def test_three_to_two_deliveries_is_a_normal_change(make):
    """Two deliveries instead of three: still several deliveries: the
    whole chain one day earlier (#187), no invalidation."""
    s = sent_staged(make)
    rows_ = delivery.table_rows(s.bw.will_settings)[:2]
    rows_[1]["shares"] = {"luca": "40%", "john": "40%"}
    context.set_instalments(s.bw, rows_)
    s.check()
    assert not s.world.broadcast_log
    assert len(valid(s)) == 12
    assert futures.futures_off_plan(valid(s), futures.plan_of_rows(
        instalments.effective_heirs(s.bw))) == []


# ------------------------------------------- the window of the deliveries

def test_back_to_one_question_says_the_invalidation(make, monkeypatch, answers):
    """With a will in several deliveries in force, "Back to one delivery"
    says the invalidation comes first; No keeps the table; Yes removes it."""
    s = sent_staged(make)
    bw = tgd.window(s, monkeypatch)
    bw.init_will()                              # the real window reads the will
    ed = deliveries.DeliveriesEditor(bw)
    ed._back_to_one()
    (question,) = answers.asked
    assert "must be invalidated on the blockchain first" in question
    assert "visible to everybody" in question
    # Owner's ZIP test: the invalidation said FIRST, and never "it is free".
    assert question.startswith("Your will in several deliveries is held")
    assert "It is free" not in question
    assert question.index("invalidated on the blockchain") < question.index(
        "With one delivery only")
    assert tgd.stored(bw)
    answers.queue.append(True)
    ed._back_to_one()
    assert tgd.stored(bw) == [] and ed.result_kind == "removed"


def test_back_to_one_question_without_a_will_in_force(make, monkeypatch, answers):
    """Drafts only: the question of before, no invalidation sentence."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    tgd.owner_table(bw)
    deliveries.DeliveriesEditor(bw)._back_to_one()
    (question,) = answers.asked
    assert "invalidated on the blockchain first" not in question
    assert "It is free" in question             # drafts only: really free


def test_the_first_table_asks_to_think_it_over(make, monkeypatch, answers):
    """The first Save of a wallet: the warning (No: nothing stored; Yes:
    stored); a later Save of the table does not repeat it; the window shows
    the warning under the example."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    ed = deliveries.DeliveriesEditor(bw)
    assert "Think it over" in ed.top_note.text()
    ed._save()
    assert any("Think it over" in q and "stays free" in q for q in answers.asked)
    assert tgd.stored(bw) == [] and ed.result_kind is None
    answers.queue.append(True)
    ed._save()
    assert tgd.stored(bw) and ed.result_kind == "saved"
    asked = len(answers.asked)
    ed2 = deliveries.DeliveriesEditor(bw)
    assert "Think it over" not in ed2.top_note.text()
    ed2._save()
    assert len(answers.asked) == asked and ed2.result_kind == "saved"


def test_the_window_offers_the_will_in_force_back(make, monkeypatch, answers):
    """The table removed, the invalidation cancelled: the window opens on
    the dates and shares of the will in force; Save keeps it, no warning
    asked; the next Check finds the will in force, nothing to sign."""
    s = sent_staged(make)
    bw = tgd.window(s, monkeypatch)
    bw.init_will()                              # the real window reads the will
    context.clear_instalments(bw)
    s.check(cancel_on=INVALIDATE)
    ed = deliveries.DeliveriesEditor(bw)
    assert ed.from_in_force and not ed.from_example
    assert "still in force" in ed.top_note.text()
    assert tgd.texts(ed) == [{"luca": "10", "john": "10"}, {"luca": "10", "john": "10"},
                             {"luca": "30", "john": "30"}]
    ed._save()
    assert answers.asked == [] and ed.result_kind == "saved"
    assert [r["locktime"] for r in tgd.stored(bw)] == [D1, D2, D3]
    assert [r["shares"] for r in tgd.stored(bw)] == [
        {"luca": "10%", "john": "10%"}, {"luca": "10%", "john": "10%"},
        {"luca": "30%", "john": "30%"}]
    s.check()
    assert [q[0] for q in step(s)["questions"]] == []
    assert len(valid(s)) == 39 and not s.world.broadcast_log


def test_in_force_table_without_a_staged_will(make, monkeypatch):
    """No will in several deliveries in force: nothing offered (the example)."""
    s = make()
    s.check()
    bw = tgd.window(s, monkeypatch)
    assert deliveries.in_force_table(bw) == ([], True)
    ed = deliveries.DeliveriesEditor(bw)
    assert ed.from_example and not ed.from_in_force


# ------------------------------------------------------ the command line

def test_cli_clear_needs_the_confirmation(make):
    """``bal_instalments_clear`` with a will in several deliveries in force:
    refused without ``--confirm_invalidation true``, the table kept; with
    it, removed; the check then returns "postponed" with the sentence."""
    s = sent_staged(make)
    out = s.cli("instalments_clear", True)
    assert "raised" in out, out
    assert "must be invalidated on the blockchain first" in out["raised"]
    assert "--confirm_invalidation true" in out["raised"]
    assert tgd.stored(s.bw)
    out = s.cli("instalments_clear", True, True)
    assert out["returned"]["removed"] is True
    assert tgd.stored(s.bw) == []
    out = s.cli("prepare_will")
    assert out["returned"]["result"] == "postponed"
    assert "Back to one delivery" in out["returned"]["message"]
    assert len(valid(s)) == 39


def test_cli_clear_of_drafts_needs_no_confirmation(make):
    """Drafts only: the command of before (``--confirm_earlier`` alone)."""
    s = make()
    tgd.owner_table(s.bw)
    s.check(cancel_on=SIGN)
    out = s.cli("instalments_clear", True)
    assert out["returned"]["removed"] is True


def test_the_window_says_the_heirs_changed_when_one_is_new(make, monkeypatch, answers):
    """Opus' review: the will in force offered back while an heir was added
    since: the note says to check the shares, not "nothing to sign"."""
    s = sent_staged(make)
    bw = tgd.window(s, monkeypatch)
    bw.init_will()
    context.clear_instalments(bw)
    bw.heirs["maria"] = [tgd.ADDR["maria"], "50%", D1]
    bw.heirs.save()
    ed = deliveries.DeliveriesEditor(bw)
    assert ed.from_in_force and ed.heirs_changed
    assert "check the shares" in ed.top_note.text()
    assert "nothing to sign" not in ed.top_note.text()


def test_no_automatic_invalidation_for_a_back_to_one(make, monkeypatch):
    """Opus' review: the automatic rebuild (off by default) must never
    invalidate on its own for a back to one delivery: the Check asks."""
    s = sent_staged(make)
    context.clear_instalments(s.bw)
    calls = []
    monkeypatch.setattr(s.bw, "_auto_invalidate_will", lambda *a, **k: calls.append(1))
    assert s.bw._auto_rebuild_flow() is False    # the headless flow itself
    assert calls == []
    assert len(valid(s)) == 39 and not s.world.broadcast_log
