"""The texts of a will in several deliveries (CHANGELOG #185, step M4b).

Owner (2026-10-23, preview approved): the "What changes" box compares a
will in several deliveries delivery by delivery, with the shares of the
whole estate (before, delivery 1 alone, its shares recalculated inside it:
the misleading "Real shares 33% → 50%"); the version row of the Will tab
says "(+2 deliveries)" and names each person once ("luca 10% + 10% +
30%"); the Heirs tab's Amount column shows the table's shares, the dates in
its tooltip. A will with one date is unchanged.

The core with will items as the core reads them, then ``tests/flowkit.py``
wallets: three fake will-executors with their own fee addresses, the
owner's table of ``test_gui_deliveries.py`` (10/10, 10/10, 30/30 %).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_staged_texts.py
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
import test_gui_deliveries as tgd
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton
from test_gui_will_versions_list import _list, _top

from bal.core import datetext
from bal.core import will_changes as wc
from bal.core.heirs import Heirs
from bal.engine import context
from bal.gui.qt import deliveries
from bal.gui.qt.widgets import PercAmountEdit

_APP = QApplication.instance() or QApplication(sys.argv)

# The deliveries tests' sessions: ADVANCED, three will-executors, the heirs
# luca and john (50 % each, not used with a table), delivery 1 on D1.
make = tgd.make
answers = tgd.answers
D1, D2, D3 = tgd.D1, tgd.D2, tgd.D3
L, J = tgd.ADDR["luca"], tgd.ADDR["john"]
WE = ["https://we0.test", "https://we1.test", "https://we2.test"]
SIGN = ("sign your will",)
WHOLE = "The percentages are shares of the whole estate."


def day(ts):
    """The short UTC day the rows write."""
    return datetext.utc_day(ts, short=True)


# ------------------------------------------------------------- the core

class Item:
    """A will item as the core reads it: heirs, date, will-executor, fee."""

    def __init__(self, heirs, locktime, url):
        """``heirs``: ``{row name: [address, written, date, real sats]}``."""
        self.heirs = heirs
        self.tx = SimpleNamespace(locktime=locktime)
        self.we = {"url": url}
        self.tx_fees = 2
        self.flags = {"VALID", "COMPLETE", "PUSHED", "CHECKED"}

    def get_status(self, flag):
        """The status flag ``flag``."""
        return flag in self.flags


def one_date(luca=50, john=50, date=D1):
    """The side of a will with one date (one copy per will-executor)."""
    heirs = {"luca": [L, "{}%".format(luca), date, luca * 1000],
             "john": [J, "{}%".format(john), date, john * 1000]}
    return wc.side_from_items({str(i): Item(heirs, date, u) for i, u in enumerate(WE)})


def staged(*deliveries):
    """The side of a will in several deliveries: ``(date, luca %, john %)``
    per delivery; one copy per will-executor and delivery (the rows of
    delivery n named "luca§n", as the build names them)."""
    items = {}
    for n, (date, luca, john) in enumerate(deliveries, 1):
        heirs = {}
        for name, address, pct in (("luca", L, luca), ("john", J, john)):
            if pct:
                key = name if n == 1 else "{}§{}".format(name, n)
                heirs[key] = [address, "{}%".format(pct), date, pct * 1000]
        for i, u in enumerate(WE):
            items["{}-{}".format(n, i)] = Item(heirs, date, u)
    return wc.side_from_items(items)


OWNER = ((D1, 10, 10), (D2, 10, 10), (D3, 30, 30))


def rows(old, new, **kw):
    """The rows of the box, ``(label, value, note)``."""
    return wc.change_rows(wc.will_changes(old, new, **kw))


def test_one_date_to_three_deliveries():
    """The owner's case: 50/50 at one date, then 10/10, 10/10, 30/30 %.
    Before, the box compared delivery 1 alone (10 % + 10 % read 50 % + 50 %:
    no change, or "33% → 50%" with other heirs)."""
    assert rows(one_date(), staged(*OWNER)) == [
        ("Deliveries", "one delivery → <b>3 deliveries</b>", None),
        ("Delivery 1", "{}: luca 50% → <b>10%</b> · john 50% → <b>10%</b>".format(
            day(D1)), None),
        ("Delivery 2", "{} (new): luca <b>10%</b> · john <b>10%</b>".format(day(D2)), None),
        ("Delivery 3", "{} (new): luca <b>30%</b> · john <b>30%</b>".format(day(D3)), WHOLE),
    ]


def test_identical_will_in_deliveries_has_no_change():
    """Nothing changed: the one row "No change"."""
    assert rows(staged(*OWNER), staged(*OWNER)) == [
        ("No change", "the will stays the same as the one in force", None)]


def test_only_the_delivery_that_changed():
    """Delivery 3 changes alone: its row only, no "Deliveries" row."""
    assert rows(staged(*OWNER), staged((D1, 10, 10), (D2, 10, 10), (D3, 20, 40))) == [
        ("Delivery 3", "{}: luca 30% → <b>20%</b> · john 30% → <b>40%</b>".format(
            day(D3)), WHOLE)]


def test_dates_moved_and_a_delivery_removed():
    """Delivery 1 one day earlier (the note of the delivery row: free),
    delivery 2 later, delivery 3 removed (its shares said once)."""
    early, later = D1 - fk.DAY, D2 + 30 * fk.DAY
    out = rows(staged(*OWNER), staged((early, 10, 10), (later, 40, 40)))
    assert out[0] == ("Deliveries", "3 deliveries → <b>2 deliveries</b>", None)
    assert out[1] == ("Delivery 1", "{} → <b>{}</b>: luca 10% · john 10%".format(
        day(D1), day(early)), "moved earlier by 1 day — free")
    assert out[2] == ("Delivery 2", "{} → <b>{}</b>: luca 10% → <b>40%</b> · john 10% "
                      "→ <b>40%</b>".format(day(D2), day(later)), None)
    assert out[3] == ("Delivery 3", "removed (it was {}: luca 30% · john 30%)".format(
        day(D3)), WHOLE)
    assert len(out) == 4


def test_back_to_one_date():
    """Back to one delivery: delivery 1 takes everything, 2 and 3 removed."""
    out = rows(staged(*OWNER), one_date())
    assert [r[0] for r in out] == ["Deliveries", "Delivery 1", "Delivery 2", "Delivery 3"]
    assert out[0][1] == "3 deliveries → <b>one delivery</b>"
    assert out[1][1] == "{}: luca 10% → <b>50%</b> · john 10% → <b>50%</b>".format(day(D1))


def test_an_heir_added_and_one_removed():
    """An heir added is written with its share only; one removed is said in
    the Heirs row, not repeated as "→ 0%" in every delivery."""
    new = staged(*OWNER)
    old = one_date()
    old.heirs = {L: "luca", "tb1qold": "anna"}
    old.all_names = {L: ["luca"], "tb1qold": ["anna"]}
    old.shares = {L: 50.0, "tb1qold": 50.0}
    out = rows(old, new)
    assert out[1] == ("Delivery 1", "{}: luca 50% → <b>10%</b> · john <b>10%</b>".format(
        day(D1)), None)
    heirs = next(r for r in out if r[0] == "Heirs")
    assert heirs[1] == "added <b>john</b> · removed <b>anna</b>"


def test_staged_false_compares_delivery_one_only():
    """``engine.context.cancelled_postpone_date`` compares delivery 1 on
    purpose: with ``staged=False`` the rows are those of before #185."""
    kinds = [c.kind for c in wc.will_changes(
        staged(*OWNER), staged((D1 - fk.DAY, 10, 10), (D2, 10, 10), (D3, 30, 30)),
        staged=False)]
    assert kinds == [wc.DELIVERY]


def test_the_settings_side_reads_the_table():
    """With a table the heirs' amounts (50 %) are not used: the settings
    side has the table's shares, the same as the items built from it."""
    heirs = {"luca": [L, "50%", D1], "john": [J, "50%", D1]}
    table = [{"locktime": D1, "shares": {"luca": "10%", "john": "10%"}},
             {"locktime": D2, "shares": {"luca": "10%", "john": "10%"}},
             {"locktime": D3, "shares": {"luca": "30%", "john": "30%"}}]
    side = wc.side_from_settings(heirs, D1, WE, 2, table=table)
    assert side.deliveries == staged(*OWNER).deliveries
    assert side.shares is None
    assert wc.side_from_settings(heirs, D1, WE, 2).deliveries is None
    assert wc.settings_table({"locktime": D1}) is None
    assert wc.settings_table({"locktime": D1, "instalments": "garbage"}) is None


def test_a_will_with_one_date_is_unchanged():
    """No table on either side: the rows of before (date, real shares)."""
    out = rows(one_date(), one_date(luca=60, john=40, date=D1 - fk.DAY))
    assert [r[0] for r in out] == ["Delivery", "Real shares"]


# ----------------------------------------------------- the Check window

def test_check_window_box_delivery_by_delivery(make):
    """A will with one date in force (luca 50 %, john 50 %), then the
    owner's table: the box before the signature has one row per delivery,
    no "Real shares"."""
    s = make()
    s.check()
    tgd.owner_table(s.bw)
    snap = s.check(cancel_on=SIGN)._report().snapshot()
    assert snap["changes_for"] == "sign"
    labels = [r[0] for r in snap["changes"]]
    assert labels == ["Deliveries", "Delivery 1", "Delivery 2", "Delivery 3"]
    assert snap["changes"][0][1] == "one delivery → <b>3 deliveries</b>"
    assert snap["changes"][1][1].endswith(": luca 50% → <b>10%</b> · john 50% → <b>10%</b>")
    assert snap["changes"][2][1] == "{} (new): luca <b>10%</b> · john <b>10%</b>".format(
        day(D2))
    assert snap["changes"][3][2] == WHOLE


def test_box_of_a_later_delivery_before_the_invalidation(make):
    """Delivery 3 moved later (a postpone: the paid invalidation first).
    Nothing is built yet: the box compares the will in force with the
    settings, the table's shares and dates (before, the heirs' 50 %)."""
    s = make()
    tgd.owner_table(s.bw)
    s.check()
    later = D3 + 365 * fk.DAY
    rows_ = tgd.stored(s.bw)
    rows_[2]["locktime"] = later
    context.set_instalments(s.bw, rows_)
    snap = s.check(cancel_on=("invalidat",))._report().snapshot()
    assert snap["changes_for"] == "invalidate"
    (row,) = snap["changes"]
    assert row[0] == "Delivery 3"
    assert row[1] == "{} → <b>{}</b>: luca 30% · john 30%".format(day(D3), day(later))


def test_wizard_summary_reads_the_table(make):
    """The wizard's summary box (``SummaryPage._changes_rows``) compares
    the table, not the heirs' amounts."""
    from bal.gui.qt.basic_wizard import SummaryPage

    s = make()
    s.check()
    tgd.owner_table(s.bw)
    wes = {u: {"selected": True} for u in WE}
    out = SummaryPage._changes_rows(s.bw, wes)
    assert [r[0] for r in out][:4] == ["Deliveries", "Delivery 1", "Delivery 2", "Delivery 3"]
    assert "Real shares" not in [r[0] for r in out]


# ------------------------------------------------- the Will tab version row

def test_version_row_of_a_will_in_deliveries(make):
    """"Delivery <date> (+2 deliveries)" and each person once, with the
    share of each delivery in order."""
    s = make()
    tgd.owner_table(s.bw)
    s.check()
    (top,) = _top(_list(s))
    text = top[2]
    assert "(+2 deliveries)" in text
    assert "luca 10% + 10% + 30% · john 10% + 10% + 30%" in text
    assert "luca 10%, john 10%" not in text


def test_version_row_with_an_empty_delivery_cell(make):
    """A person with nothing at a delivery reads "0%" there; two deliveries
    say "(+1 delivery)"."""
    s = make()
    tgd.owner_table(s.bw, [{"locktime": D1, "shares": {"luca": "60%", "john": "0%"}},
                           {"locktime": D2, "shares": {"luca": "0%", "john": "40%"}}])
    s.check()
    (top,) = _top(_list(s))
    assert "(+1 delivery)" in top[2]
    assert "luca 60% + 0% · john 0% + 40%" in top[2]


def test_version_row_of_a_will_with_one_date(make):
    """One date: as before, "luca 50%, john 50%", no "(+" text."""
    s = make()
    s.check()
    (top,) = _top(_list(s))
    assert "luca 50%, john 50%" in top[2] or "john 50%, luca 50%" in top[2]
    assert "(+" not in top[2]


# ---------------------------------------------------- the Heirs tab column

def _amounts(bw):
    """``{name: (Amount text, its tooltip)}`` of the Heirs list AS SHOWN:
    never rebuilt here (owner's ZIP test: the list kept "70%" after the
    table was saved; the test before rebuilt it by hand and saw nothing)."""
    lst = bw.heir_list_widget
    m = lst.model()
    col = lst.Columns.AMOUNT
    return {m.item(r, 0).text(): (m.item(r, col).text(), m.item(r, col).toolTip())
            for r in range(m.rowCount())}


def test_heirs_tab_follows_the_table_saved_in_its_window(make, monkeypatch, answers):
    """The owner's steps: the wallet open, the window of the deliveries,
    "Example table" saved (16.5 / 16.5 / 17 %): the Amount column shows the
    shares at once, the dates in the tooltip; back to one delivery: the
    heir's own amount again."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    before = _amounts(bw)
    assert "+" not in before["luca"][0] and before["luca"][1] == ""
    deliveries.DeliveriesEditor(bw)._save()
    shown = _amounts(bw)
    assert shown["luca"][0] == "16.5% + 16.5% + 17%"
    assert shown["luca"][1].startswith("Delivery 1 (10/03/2030): 16.5%\n"
                                       "Delivery 2 (10/03/2031): 16.5%\n")
    assert shown["john"][0] == "16.5% + 16.5% + 17%"
    answers.queue.append(True)
    deliveries.DeliveriesEditor(bw)._back_to_one()
    assert _amounts(bw) == before


def test_heirs_tab_amount_column_with_an_empty_cell(make, monkeypatch):
    """A person with nothing at a delivery: "0%" in its place. The table
    stored, then the window's refresh (``update_all``, what its Save calls)."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    tgd.owner_table(bw, [{"locktime": D1, "shares": {"luca": "60%", "john": "0%"}},
                         {"locktime": D2, "shares": {"luca": "0%", "john": "40%"}}])
    bw.update_all()
    shown = _amounts(bw)
    assert shown["luca"][0] == "60% + 0%"
    assert shown["john"][0] == "0% + 40%"


# ------------------------------------------------------ the decimal comma

class _Catalogs:
    """The repo's catalogs, compiled as the ZIP has them."""

    def read_file(self, path):
        """The compiled ``.mo`` of ``path``."""
        from build_zip import compile_catalog

        root = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
        return compile_catalog(os.path.join(root, path).replace(".mo", ".po"))


def test_italian_shows_the_decimal_comma(make, monkeypatch):
    """Owner's ZIP test: "16.5%" in the version row and the Heirs tab,
    "16,5%" in the box. In Italian every place reads "16,5%"; the stored
    table keeps "16.5%"."""
    from bal import i18n

    s = make()
    bw = tgd.window(s, monkeypatch)
    try:
        i18n.set_language(_Catalogs(), "it_IT")
        deliveries.DeliveriesEditor(bw)._save()
        shown = _amounts(bw)
        assert shown["luca"][0] == "16,5% + 16,5% + 17%"
        assert shown["luca"][1].startswith("Consegna 1 (10/03/2030): 16,5%")
        s.check()
        (top,) = _top(_list(s))
        assert "luca 16,5% + 16,5% + 17% · john 16,5% + 16,5% + 17%" in top[2]
        assert "(+2 consegne)" in top[2]
    finally:
        i18n.set_language(None, None)
    assert tgd.stored(bw)[0]["shares"] == {"luca": "16.5%", "john": "16.5%"}


# ------------------------------------------- the heir window ("B grey")

def _scripted(monkeypatch, fill=None, click=None):
    """Script the heir window's ``exec``: ``click`` is the text of a button
    to press first, ``fill(dialog)`` types; OK once, then Cancel. Returns
    ``seen``: what the window showed at OK (``labels``: the shown texts,
    ``amount``: the amount field, ``buttons``: by text)."""
    from bal.gui.qt import window as window_mod

    seen = {}
    calls = {"n": 0}

    def fake_exec(self):
        """OK once (then Cancel)."""
        if calls["n"]:
            return False
        calls["n"] += 1
        buttons = {b.text(): b for b in self.findChildren(QPushButton)}
        if click is not None:
            buttons[click].click()
        if fill is not None:
            fill(self)
        seen["labels"] = [w.text() for w in self.findChildren(QLabel)
                          if w.text() and not w.isHidden()]
        seen["amount"] = self.findChildren(PercAmountEdit)[0]
        seen["buttons"] = buttons
        return True

    monkeypatch.setattr(window_mod.BalDialog, "exec", fake_exec)
    return seen


def test_heir_window_shows_the_shares_and_locks_the_amount(make, monkeypatch, answers):
    """An heir of the table: "Shares" with the table's cells and dates over
    the amount; the amount grey and locked (the mode buttons off), with the
    sentence; OK after a new address keeps the stored amount exactly."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    tgd.owner_table(bw)
    new_address = fk.address(23)

    def fill(dialog):
        """A new address for luca, nothing else."""
        dialog.findChildren(QLineEdit)[1].setText(new_address)

    seen = _scripted(monkeypatch, fill=fill)
    bw.new_heir_dialog("luca")
    labels = seen["labels"]
    assert "Shares" in labels and "10% + 10% + 30%" in labels
    assert ("Delivery 1 (10/03/2030): 10%\nDelivery 2 (10/03/2032): 10%\n"
            "Delivery 3 (10/03/2035): 30%") in labels
    assert any(t.startswith("With the inheritance in several deliveries this amount "
                            "is not used: it counts only if you go back") for t in labels)
    assert seen["amount"].isReadOnly()
    assert not seen["buttons"]["% Percentage"].isEnabled()
    assert not seen["buttons"]["Fixed amount"].isEnabled()
    assert "Edit the deliveries…" in seen["buttons"]
    stored = Heirs(s.wallet)["luca"]
    assert stored[0] == new_address and stored[1] == "50%"
    assert answers.asked == []


def test_heir_window_button_opens_the_deliveries(make, monkeypatch):
    """"Edit the deliveries…" opens the window of the deliveries over the
    heir window; the shares shown follow what was saved there."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    tgd.owner_table(bw)
    opened = []

    def editor(parent=None):
        """The window of the deliveries: luca's last share changed."""
        opened.append(parent)
        rows = tgd.stored(bw)
        rows[2]["shares"] = {"luca": "20%", "john": "40%"}
        context.set_instalments(bw, rows)
        return "saved"

    monkeypatch.setattr(bw, "open_deliveries_editor", editor)
    seen = _scripted(monkeypatch, click="Edit the deliveries…")
    bw.new_heir_dialog("luca")
    assert len(opened) == 1 and opened[0] is not None      # over the heir window
    assert "10% + 10% + 20%" in seen["labels"]


def test_heir_window_of_a_new_heir_with_a_table(make, monkeypatch, answers):
    """A new heir: no share yet ("0% + 0% + 0%"), the amount written (it
    must be above 0), the sentence; after OK the table is offered."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    tgd.owner_table(bw)

    def fill(dialog):
        """maria, her address, 20 %."""
        edits = dialog.findChildren(QLineEdit)
        edits[0].setText("maria")
        edits[1].setText(tgd.ADDR["maria"])
        dialog.findChildren(PercAmountEdit)[0].setText("20")

    seen = _scripted(monkeypatch, fill=fill)
    bw.new_heir_dialog()
    labels = seen["labels"]
    assert "0% + 0% + 0%" in labels
    assert any("After OK you will be asked to open the table" in t for t in labels)
    assert any(t.startswith("With the inheritance in several deliveries this amount "
                            "counts only if you go back to one delivery.") for t in labels)
    assert not seen["amount"].isReadOnly()
    assert Heirs(s.wallet)["maria"][1] == "20%"
    assert len(answers.asked) == 1                 # open the table now?


def test_heir_window_in_basic_has_no_button(make, monkeypatch):
    """BASIC: the shares are shown, the window of the deliveries is not
    offered (it is ADVANCED only)."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    tgd.owner_table(bw)
    monkeypatch.setattr(bw.bal_plugin, "is_basic_mode", lambda: True)
    seen = _scripted(monkeypatch)
    bw.new_heir_dialog("luca")
    assert "10% + 10% + 30%" in seen["labels"]
    assert "Edit the deliveries…" not in seen["buttons"]


def test_heir_window_without_a_table_is_unchanged(make, monkeypatch):
    """No table: no "Shares", the amount editable, no sentence."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    seen = _scripted(monkeypatch)
    bw.new_heir_dialog("luca")
    assert "Shares" not in seen["labels"]
    assert not seen["amount"].isReadOnly()
    assert not any("several deliveries" in t for t in seen["labels"])


def test_back_to_one_delivery_names_the_amounts(make, monkeypatch, answers):
    """The question names the amounts that count again (the heir window
    shows them grey while there is a table)."""
    s = make()
    bw = tgd.window(s, monkeypatch)
    tgd.owner_table(bw)
    deliveries.DeliveriesEditor(bw)._back_to_one()
    (question,) = answers.asked
    assert ("Then the amounts of the heirs count again (luca 50%, john 50%): "
            "check them before the next Check.") in question
