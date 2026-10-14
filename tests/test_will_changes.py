"""What changes compared with the will in force (CHANGELOG #135).

The core (``bal.core.will_changes``): each kind of change alone, several
together (the owner's case), identical -> "No change", no will in force ->
no box, the real shares. Then the Check window (the box before the
signature; Cancel at the password changes nothing; a first will has no box)
and the BASIC wizard's page 4 (the box and the real shares of the heirs).

``tests/flowkit.py`` wallets: three fake will-executors with their own fee
addresses, the test password, a fixed clock.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_will_changes.py
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core import will_changes as wc

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
A, B, C, D = (fk.address(n) for n in (20, 21, 22, 23))
WE = ["https://we{}.test".format(i) for i in range(5)]
OLD_DATE = fk.ts(2030, 12, 31, 1)
NEW_DATE = fk.ts(2028, 10, 13)


class Item:
    """A will item as the core reads it (flags, heirs, fee, executor)."""

    def __init__(self, heirs, locktime=OLD_DATE, url=WE[0], fee=21,
                 flags=("VALID", "COMPLETE", "PUSHED", "CHECKED")):
        """``heirs``: ``{name: [address, written, date, real sats]}``."""
        self.heirs = heirs
        self.tx = SimpleNamespace(locktime=locktime)
        self.we = {"url": url} if url else None
        self.tx_fees = fee
        self.flags = set(flags)

    def get_status(self, flag):
        """The status flag ``flag``."""
        return flag in self.flags


def side(heirs, urls=(WE[0], WE[1], WE[2]), **kw):
    """The side of a will with one copy per will-executor of ``urls``."""
    return wc.side_from_items(
        {str(i): Item(heirs, url=u, **kw) for i, u in enumerate(urls)})


def heirs(**shares):
    """``{name: [address, "N%", date, real sats]}`` (real sats = N * 1000)."""
    address = {"alice": A, "bob": B, "carol": C, "dave": D}
    return {n: [address[n], "{}%".format(p), OLD_DATE, p * 1000]
            for n, p in shares.items()}


def rows(old, new):
    """The rows' ``(label, value, note)``."""
    return wc.change_rows(wc.will_changes(old, new))


BASE = heirs(alice=60, bob=40)


def test_identical_and_first_will():
    """Identical -> one row "No change"; no will in force -> no box."""
    assert rows(side(BASE), side(BASE)) == [
        ("No change", "the will stays the same as the one in force", None)]
    assert wc.will_changes(None, side(BASE)) is None
    assert wc.change_rows(None) is None
    assert wc.side_from_items({}) is None


def test_date_earlier_and_later():
    """Earlier: "— free"; later: the invalidation transaction (never
    "paid": the bitcoin stay the owner's, #135 follow-up)."""
    (row,) = rows(side(BASE), side(BASE, locktime=NEW_DATE))
    assert row == ("Delivery", "31 December 2030, 01:00 UTC → <b>13 October 2028, "
                   "00:00 UTC</b>", "moved earlier by 2 years and 2 months — free")
    (row,) = rows(side(BASE, locktime=NEW_DATE), side(BASE))
    assert row[2] == ("moved later by 2 years and 2 months — needs an "
                      "invalidation transaction on the blockchain: your bitcoin "
                      "stay yours, you only pay the normal network fee")


def test_heir_added_removed_renamed_by_address():
    """Matched by address: a new name on the same address is "renamed"."""
    new = heirs(alice=60, bob=40)
    new["carol"] = [C, "60%", OLD_DATE, 60000]
    out = rows(side(BASE), side(new))
    assert out[0] == ("Heirs", "added <b>carol</b>", None)
    (row,) = rows(side(heirs(alice=60, bob=40, carol=50)), side(heirs(alice=60, bob=40)))[:1]
    assert row == ("Heirs", "removed <b>carol</b>", None)
    renamed = {"alice rossi": [A, "60%", OLD_DATE, 60000], "bob": [B, "40%", OLD_DATE, 40000]}
    assert rows(side(BASE), side(renamed)) == [
        ("Heirs", "renamed: alice → <b>alice rossi</b>", None)]


def test_real_shares_change():
    """The REAL shares (what the transactions pay), only those that move;
    the note when the written total is not 100."""
    out = rows(side(BASE), side(heirs(alice=50, bob=50)))
    assert out == [("Real shares", "alice 60% → <b>50%</b> · bob 40% → <b>50%</b>", None)]
    out = rows(side(BASE), side(heirs(alice=50, bob=30, carol=50)))
    share = dict((r[0], r) for r in out)["Real shares"]
    assert share[1] == "alice 60% → <b>38.5%</b> · bob 40% → <b>23.1%</b>"
    # #135: the total in the row's note (the shares said once).
    assert share[2] == ("The percentages you wrote add up to 130%: the will always "
                        "spends the whole balance, so the shares are recalculated "
                        "in proportion.")


def test_executors_added_removed_and_deselected():
    """Will-executors: the count and which; a deselected one that never
    received the will counts as removed (the #134 rule)."""
    out = rows(side(BASE), side(BASE, urls=WE))
    assert out == [("Will-executors", "from 3 to <b>5</b>: added <b>we3.test</b>, "
                    "<b>we4.test</b>", None)]
    old = wc.side_from_items({
        "0": Item(BASE, url=WE[0]), "1": Item(BASE, url=WE[1]),
        "2": Item(BASE, url=WE[2], flags=("VALID", "COMPLETE"))},
        selected={WE[0], WE[1]})
    assert old.executors == [WE[0], WE[1]]
    out = rows(side(BASE), side(BASE, urls=WE[:2]))
    assert out == [("Will-executors", "from 3 to <b>2</b>: removed <b>we2.test</b>", None)]


def test_fee_and_message():
    """The miner fee; an OP_RETURN message changed."""
    assert rows(side(BASE), side(BASE, fee=25)) == [
        ("Miner fee", "21 → <b>25 sat/vB</b>", None)]
    old = dict(BASE, msg=["OP_RETURN:" + "ciao".encode().hex(), "0", OLD_DATE, 0])
    new = dict(BASE, msg=["OP_RETURN:" + "addio".encode().hex(), "0", OLD_DATE, 0])
    assert rows(side(old), side(new)) == [
        ("Message", "changed: “ciao” → <b>“addio”</b>", None)]


def test_owner_case_several_changes():
    """The owner's case: in force 31 Dec 2030, 4 heirs 50/30/50/40 and 3
    will-executors; new 13 Oct 2028, one heir removed, 5 will-executors,
    fee 21 -> 25."""
    old = side(heirs(alice=50, bob=30, carol=50, dave=40))
    new = side(heirs(alice=50, bob=30, carol=50), urls=WE, locktime=NEW_DATE, fee=25)
    labels = [r[0] for r in rows(old, new)]
    assert labels == ["Delivery", "Heirs", "Real shares", "Will-executors", "Miner fee"]
    share = rows(old, new)[2]
    assert share[1] == ("alice 29.4% → <b>38.5%</b> · bob 17.6% → <b>23.1%</b> · "
                        "carol 29.4% → <b>38.5%</b>")


def test_settings_side_and_fixed_amounts():
    """The wizard's side: the written percentages in proportion; with a
    fixed amount no real shares (never invented)."""
    new = wc.side_from_settings(
        {"alice": [A, "50%", 0], "bob": [B, "30%", 0], "carol": [C, "50%", 0]},
        NEW_DATE, WE[:3], 21)
    assert round(new.shares[A], 1) == 38.5 and new.written_total == 130
    fixed = wc.side_from_settings({"alice": [A, "50%", 0], "bob": [B, "100000", 0]},
                                  NEW_DATE, WE[:3], 21)
    assert fixed.shares is None
    assert wc.real_shares_text({"alice": [A, "50%", 0], "bob": [B, "30%", 0],
                                "carol": [C, "50%", 0]}) == (
        "alice 38.5% · bob 23.1% · carol 38.5%", True)
    assert wc.real_shares_text({"alice": [A, "60%", 0], "bob": [B, "40%", 0]}) == (
        "alice 60% · bob 40%", False)
    assert wc.real_shares_text({"alice": [A, "50%", 0], "bob": [B, "1000", 0]}) is None


# ----------------------------------------------------------------- GUI


@pytest.fixture
def make(monkeypatch):
    """A flowkit session with three will-executors (synchronized wallet)."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """The session."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def test_check_window_shows_the_box_before_the_password(make, monkeypatch):
    """A heir added: the Check window fills the box (with its sentence)
    BEFORE the "Sign your will" password; Cancel there changes nothing (the
    will in force stays, nothing is sent)."""
    s = make(mode="basic")
    s.check()
    s.set_heirs(alice="60%", bob="40%", carol="10%")
    seen = []
    real = s.user.password_dialog

    def password(msg=None, parent=None):
        """Record the box present when the password is asked."""
        if s.last_dialog is not None:
            seen.append((msg, s.last_dialog._report().snapshot().get("changes")))
        return real(msg, parent)

    import bal.gui.qt.dialogs as dialogs_mod
    orig = dialogs_mod.BalBuildWillDialog._show_what_changes

    def spy(self, when):
        """Remember the dialog."""
        s.last_dialog = self
        return orig(self, when)

    s.last_dialog = None
    monkeypatch.setattr(dialogs_mod.BalBuildWillDialog, "_show_what_changes", spy)
    s.plugin.password_dialog = password
    pushed = len(s.world.pushed)
    dlg = s.check(cancel_on=("sign your will",))
    snap = dlg._report().snapshot()
    assert snap["changes_for"] == "sign"
    # The rebuilt will is dated one day earlier so it replaces the one in
    # force (the plugin's anticipation): the box says so too.
    assert [r[0] for r in snap["changes"]] == ["Delivery", "Heirs", "Real shares"]
    assert snap["changes"][0][2] == "moved earlier by 1 day — free"
    assert snap["changes"][1][1] == "added <b>carol</b>"
    assert seen and seen[0][0] == "Sign your will" and seen[0][1] == snap["changes"]
    assert len(s.world.pushed) == pushed                     # nothing sent
    from bal.engine import context as engine_context
    assert len(engine_context.in_force_items(s.bw)) == 3     # still in force


def test_first_will_has_no_box(make):
    """Nothing in force: no box."""
    s = make(mode="basic")
    dlg = s.check()
    assert "changes" not in dlg._report().snapshot()


def test_check_window_renders_the_box():
    """The report view draws the box and the sentence."""
    from PyQt6.QtWidgets import QLabel

    from bal.core.build_report import BuildReport
    from bal.gui.qt.widgets import BuildReportView

    report = BuildReport()
    report.set_changes([("Miner fee", "21 → <b>25 sat/vB</b>", None)])
    view = BuildReportView()
    view.render(report.snapshot())
    texts = [w.text() for w in view.findChildren(QLabel)]
    assert "<b>What changes compared with the will in force</b>" in texts
    assert "• Miner fee" in texts and "21 → <b>25 sat/vB</b>" in texts
    assert any(t.startswith("Check the list: by signing") for t in texts)


def test_wizard_page_four_box_and_real_shares(make, monkeypatch):
    """Page 4: the heirs with their REAL shares (and the note when the
    total is not 100) and the box: one heir more, the real shares."""
    from zoneinfo import ZoneInfo

    from test_gui_basic_wizard import install_network
    from test_gui_window_init_migration import real_window

    from bal.core import wizard_date
    from bal.gui.qt import basic_wizard
    from bal.gui.qt.dialogs import BalWizardDialog

    s = make(mode="basic")
    s.check()
    s.set_heirs(alice="50%", bob="30%", carol="50%")
    monkeypatch.setattr(wizard_date, "local_zone", lambda: ZoneInfo("Europe/Rome"))
    monkeypatch.setattr(basic_wizard, "time_now", s.clock.time)
    bw = real_window(s, monkeypatch)
    bw.init_will()
    install_network(s, bw, monkeypatch)
    d = BalWizardDialog(bw)
    try:
        for _i in range(3):
            d.widget.next_button.click()
        page = d.widget
        assert page.values["Heirs"].text() == (
            "alice 38.5% · bob 23.1% · carol 38.5% <span style='color:#6A737D'>"
            "(recalculated in proportion)</span>")
        texts = [lab.text() for lab in page.changes_box.findChildren(QLabelType())]
        assert "added <b>carol</b>" in texts
        assert any(t.startswith("alice 60% → <b>38.5%</b>") for t in texts)
    finally:
        d.close()


def QLabelType():  # noqa: N802 - a tiny alias
    """QLabel (imported late, the Qt app exists)."""
    from PyQt6.QtWidgets import QLabel

    return QLabel


def test_box_before_the_paid_invalidation_of_a_postpone(make):
    """A later date (a postpone): nothing is built yet, the box compares the
    will in force with the settings, before "Invalidate your old will"
    (no sentence: it is not the signature)."""
    s = make(mode="basic")
    s.check()
    s.set_date(fk.LATER)
    dlg = s.check(cancel_on=("invalidat",))
    snap = dlg._report().snapshot()
    assert snap["changes_for"] == "invalidate"
    (row,) = snap["changes"]
    assert row[0] == "Delivery" and row[2].startswith("moved later by 1 year")



def test_executor_added_keeps_the_copies_in_force(make):
    """A will-executor added: the check prepares a copy for it alone and the
    copies in force stay the will. The box says "added", it must NOT say the
    will-executors already in force were removed (the new side is the drafts
    plus the copies in force they leave in place). Two will-executors in
    force, the third one added (the flowkit "executor_added" case)."""
    s = make(mode="basic")
    del s.world.executors["https://we2.test"]
    s.check()
    s.world.add_executor()
    dlg = s.check()
    snap = dlg._report().snapshot()
    assert snap["changes_for"] == "sign"
    assert [list(r) for r in snap["changes"]] == [
        ["Will-executors", "from 2 to <b>3</b>: added <b>we2.test</b>", None]]


def test_renames_join_the_one_box():
    """#135 (owner): the heirs only renamed by the Check join the Heirs row
    (created after the delivery when missing); "No change" goes away; no
    will in force stays None."""
    renames = [("bob", "bobby")]
    assert wc.change_rows(wc.with_renames([wc.Change(wc.NONE)], renames)) == [
        ("Heirs", "renamed: bob → <b>bobby</b> (only the name changes, already saved: no need to sign)", None)]
    changes = [wc.Change(wc.DELIVERY, 2, 1), wc.Change(wc.FEE, 13, 14)]
    labels = [r[0] for r in wc.change_rows(wc.with_renames(changes, renames))]
    assert labels == ["Delivery", "Heirs", "Miner fee"]
    changes = [wc.Change(wc.HEIRS, added=["carol"])]
    (row,) = wc.change_rows(wc.with_renames(changes, renames))
    assert row[1] == ("added <b>carol</b> · renamed: bob → <b>bobby</b> "
                      "(only the name changes, already saved: no need to sign)")
    assert wc.with_renames(None, renames) is None


def test_two_names_on_one_address_are_never_a_rename():
    """#135 (owner's ZIP test): a second name at bob's address is a second
    heir paid there: "added X (same address as Y)", never "renamed"; one
    name each side is a rename (the wizard's wording: no need to sign)."""
    a, b = "tb1qalice", "tb1qbob"
    old = wc.side_from_settings({"alice": [a, "60%", 1], "bob": [b, "40%", 1]}, 1, [], None)
    dup = wc.side_from_settings({"alice": [a, "60%", 1], "bob": [b, "40%", 1],
                                 "bob 3": [b, "40%", 1]}, 1, [], None)
    rows = wc.change_rows(wc.will_changes(old, dup), wizard=True)
    assert rows[0] == ("Heirs", "added <b>bob 3</b> (same address as bob)", None)
    assert rows[1][0] == "Real shares"
    renamed = wc.side_from_settings({"alice": [a, "60%", 1], "bob 3": [b, "40%", 1]},
                                    1, [], None)
    (row,) = wc.change_rows(wc.will_changes(old, renamed), wizard=True)
    assert row == ("Heirs", "renamed: bob → <b>bob 3</b> (only the name changes: "
                   "no need to sign)", None)
    (row,) = wc.change_rows(wc.will_changes(old, renamed))
    assert row == ("Heirs", "renamed: bob → <b>bob 3</b>", None)
