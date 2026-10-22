"""The Will tab lists the will by versions (CHANGELOG #129): ``PreviewList``.

The list was one flat row per transaction. Now it is a two-level tree:
one row per version (the draft, the will in force, the history; from
``bal.core.will_versions``) and under it one row per transaction, in
columns (locktime, full txid, will-executor with its fee address, the
will-executor's fee, status). Here, on the real list with realistic wills
made by ``tests/flowkit.py`` (three fake will-executors, each with its own
fee address; throw-away test wallets with the test password; fixed clock):

* the version rows, their order and texts, the paid invalidation row;
* the columns of the transaction rows (full txid, executor address, fee,
  the backup transaction);
* the last five versions and "Show all history";
* the rows opened / closed by the owner kept across ``update()``;
* double click and context menu on the transaction rows (under a version)
  and the version row's menu; delete refused for a sent transaction;
* the search box on versions and transactions; multiverse;
* the Settings dialog without the two "Hide" options;
* after the owner's ZIP test: the header of the version level ("N.",
  "Version", "Status") and the sub-header row of the transactions, the
  discarded drafts (unnumbered, only with "Show all history");
* the owner's four fixes after the first preview: a copy in force reads in
  force (green) even after a later draft marked it REPLACED, "not sent" for
  a version nobody received, a four-digit year, two-line cells tall enough.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_will_versions_list.py
"""

import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.simple_config import SimpleConfig
from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

import bal.gui.qt.lists as lists_mod
import bal.gui.qt.plugin as plugin_mod
from bal.gui.qt.lists import PreviewList
from bal.gui.qt.plugin import Plugin
from bal.gui.qt.theme import will_status_text

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D = fk.ts(2027, 2, 27, 22)
SIGN = ("sign your will",)
URLS = ["https://we0.test", "https://we1.test", "https://we2.test"]


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions with three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session (``fk.Session`` arguments)."""
        kw.setdefault("password", TEST_PASSWORD)
        kw.setdefault("fee", 21)
        kw.setdefault("locktime", D)
        kw.setdefault("threshold", "-30d")
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _list(s):
    """The real Will tab list on the session's window (no toolbar)."""
    # flowkit's window has no Electrum main window: what the list reads.
    s.bw.window.config = s.config
    s.host = QWidget()
    lst = PreviewList(s.bw, s.host, None)
    return lst


def _top(lst):
    """``[(kind, key, search text)]`` of the top-level rows."""
    m = lst.model()
    out = []
    for r in range(m.rowCount()):
        idx = m.index(r, 0)
        out.append((idx.data(lst.ROLE_KIND), idx.data(lst.ROLE_KEY),
                    idx.data(lst.ROLE_SEARCH)))
    return out


def _version_index(lst, number):
    """The first-cell index of version ``#number``."""
    m = lst.model()
    for r in range(m.rowCount()):
        idx = m.index(r, 0)
        if idx.data(lst.ROLE_KIND) == "version":
            v = lst._versions[idx.data(lst.ROLE_KEY)]
            if v.number == number:
                return idx
    raise AssertionError("no version #{}".format(number))


def _cells(lst, idx):
    """The FULL values of the row of ``idx``, column by column: what
    "Copy" copies (#136: some cells show them shortened), else the text."""
    out = []
    for c in range(len(lst.Columns)):
        cell = idx.sibling(idx.row(), c)
        full = cell.data(lst.ROLE_CLIPBOARD_DATA)
        out.append(full if full is not None else cell.data())
    return out


def _shown(lst, idx):
    """The texts the row of ``idx`` shows, column by column."""
    return [idx.sibling(idx.row(), c).data() for c in range(len(lst.Columns))]


def _history_with_invalidation(make):
    """#1 sent; #2 a new coin; #3 earlier; then a later date: paid
    invalidation and #4 in force."""
    s = make()
    s.check()
    s.clock.travel(1)
    s.add_coin(250_000)
    s.check()
    s.clock.travel(1)
    s.set_date(D - 10 * fk.DAY)
    s.check()
    s.clock.travel(1)
    s.set_date(D + 30 * fk.DAY)
    s.check()
    return s


def test_only_the_version_in_force_has_in_force_rows(make):
    """CHANGELOG #130, owner's ZIP test: an older version still held by the
    will-executors (#1, replaced by the earlier #2) showed green "In force,
    verified" rows under "Replaced (still held by the will-executors)".
    Only the rows of THE version in force read "In force"."""
    s = make()
    s.check()
    s.clock.travel(1)
    s.set_date(D - fk.DAY)
    s.check()
    lst = _list(s)
    older = _version_index(lst, 1)
    current = _version_index(lst, 2)
    assert lst._versions[older.data(lst.ROLE_KEY)].state == "replaced_held"
    m = lst.model()
    for r in range(3):
        old_cells = _cells(lst, m.index(r + 1, 0, older))
        assert old_cells[5] == will_status_text(s.bw.willitems[old_cells[1]])
        assert not old_cells[5].startswith("In force")
        assert _cells(lst, m.index(r + 1, 0, current))[5] == "In force, verified"


def test_versions_and_the_paid_invalidation(make):
    s = _history_with_invalidation(make)
    lst = _list(s)
    top = _top(lst)
    assert [k for k, _key, _t in top] == [
        "version", "invalidation", "version", "version", "version"]
    texts = [t for _k, _key, t in top]
    assert texts[0].startswith("#4 · delivery ")
    assert "alice 60%, bob 40%" in texts[0]
    assert "miner fee 21 sat/vb" in texts[0]
    assert "sent and verified 3 of 3" in texts[0]
    assert texts[0].endswith("· in force")
    assert texts[1].startswith("invalidation transaction · miner fee ")
    assert top[1][1] in texts[1]                       # the full txid
    assert texts[1].endswith("· mempool")
    assert [t.split(" ·")[0] for t in texts[2:]] == ["#3", "#2", "#1"]
    assert all(t.endswith("· invalidated") for t in texts[2:])
    # Version rows span every column; every version starts closed (#131,
    # owner: the transaction rows only when the user opens a version).
    m = lst.model()
    for r in range(m.rowCount()):
        assert lst.isFirstColumnSpanned(r, QModelIndex())
    assert not any(lst.isExpanded(m.index(r, 0)) for r in range(m.rowCount()))
    # The custom painter draws the three columns of the version level:
    # the number, the delivery and summary, the state in its colour.
    paint = m.index(0, 0).data(lst.ROLE_CUSTOM_PAINT)
    assert (paint.state, paint.colour) == ("In force", "#2e7d32")
    assert paint.number == "#4" and paint.head.startswith("Delivery ")


def test_header_names_the_version_level_and_stays_full_width(make):
    """Owner, after the ZIP test: the header is "N." / "Version" /
    "Status" over the whole width, the same when every version is closed
    or open; the transaction columns are named by a grey sub-header row,
    seen only under an open version."""
    s = _seven_versions(make)
    lst = _list(s)
    s.host.resize(1400, 600)
    QVBoxLayout(s.host).addWidget(lst)
    s.host.show()
    _APP.processEvents()
    header = lst.header()
    width = lst.viewport().width()
    lst.collapseAll()
    _APP.processEvents()
    closed = header.segments()
    assert [label for _x, _w, label in closed] == ["N.", "Version", "Status"]
    x, w, _label = closed[-1]
    assert x + w == width                    # up to the right edge
    assert closed[1][1] > width // 2         # "Version" takes the room
    lst.expandAll()
    _APP.processEvents()
    assert header.segments()[0] == closed[0]
    assert [label for _x, _w, label in header.segments()] == ["N.", "Version", "Status"]
    lst.collapseAll()
    _APP.processEvents()
    assert header.segments() == closed
    # The sub-header row: under every version, visible only when open.
    version = _version_index(lst, 7)
    sub = lst.model().index(0, 0, version)
    assert lst._row_info(sub) == ("subheader", None)
    assert lst.visualRect(sub).isEmpty()
    lst.expand(version)
    _APP.processEvents()
    assert not lst.visualRect(sub).isEmpty()
    assert lst.build_menu(sub) is None       # no menu, not selectable
    assert not lst.model().itemFromIndex(sub).isSelectable()
    s.host.close()


def test_discarded_draft_in_its_place(make):
    """A draft a plugin before #128 marked REPLACED: no number, shown in
    its place by creation (owner, ZIP test: "Show all history" must only
    add older rows at the bottom, so a discarded draft newer than the
    shown versions is never hidden)."""
    from bal.core import will_state
    s = make()
    s.check()
    s.clock.travel(1)
    s.set_fee(20)
    s.check(closing=True, cancel_on=SIGN)
    for item in s.bw.willitems.values():
        if will_state.is_draft(item):
            item.set_status("REPLACED", True)
            item.set_status("VALID", False)
    lst = _list(s)
    top = _top(lst)
    assert [k for k, _key, _t in top] == ["version", "version"]
    assert top[0][2].startswith("#1") and lst.hidden_versions == 0
    assert lst.history_button is None
    assert top[1][2].startswith("delivery ") and top[1][2].endswith("· discarded draft")
    paint = lst.model().index(1, 0).data(lst.ROLE_CUSTOM_PAINT)
    assert paint.number == "" and paint.state == "Discarded draft"


def test_columns_of_a_transaction_row(make):
    s = make()
    s.check()
    lst = _list(s)
    assert [lst.model().headerData(c, lst.header().orientation())
            for c in range(len(lst.Columns))] == [
        "Locktime", "Txid", "Will-Executor", "Fee address", "Executor fee", "Status"]
    version = lst.model().index(0, 0)
    # The sub-header row first, then one row per will-executor.
    assert lst.model().rowCount(version) == 4
    assert _cells(lst, lst.model().index(0, 0, version)) == [
        "Locktime", "Txid", "Will-Executor", "Fee address", "Executor fee", "Status"]
    for r, url in enumerate(URLS):
        child = lst.model().index(r + 1, 0, version)
        locktime, txid, executor, address, fee, status = _cells(lst, child)
        item = s.bw.willitems[txid]
        assert len(txid) == 64                          # Copy: the full txid
        assert locktime.startswith("2027-02-27T22:00")  # Copy: the exact time
        assert executor == url                          # Copy: the full URL
        assert address == fk.WE_ADDR[r]                 # Copy: the full address
        # #136: the wallet's unit only (the config's formatter, no fiat);
        # the window's formatter (with fiat in Electrum) is the tooltip.
        assert fee == "0.01 mBTC"
        assert child.sibling(child.row(), 4).data(Qt.ItemDataRole.ToolTipRole) == "1000 sat"
        assert status == will_status_text(item) == "In force, verified"
        # #136 (owner): what the row SHOWS is compact and on ONE line; the
        # full values are in the tooltips; the date is not bold; no fiat.
        shown = _shown(lst, child)
        assert not any("\n" in str(text) for text in shown)
        assert shown[0] == "27 Feb 2027, 22:00 UTC"
        assert shown[1] == txid[:8] + "…" + txid[-8:]
        assert shown[2] == url.split("://")[1]          # the host only
        assert shown[3] == fk.WE_ADDR[r][:8] + "…" + fk.WE_ADDR[r][-8:]
        assert shown[4] == "0.01 mBTC" and "USD" not in shown[4]
        for c, full in ((1, txid), (2, url), (3, fk.WE_ADDR[r])):
            assert child.sibling(child.row(), c).data(Qt.ItemDataRole.ToolTipRole) == full
        tip = child.sibling(child.row(), 0).data(Qt.ItemDataRole.ToolTipRole)
        assert tip.startswith("27 February 2027, 22:00 UTC") and locktime in tip
        font = child.sibling(child.row(), 0).data(Qt.ItemDataRole.FontRole)
        assert font is None or not font.bold()


def test_transaction_rows_are_one_line(make):
    """#136 (owner): every transaction row is ONE text line (the fee
    address has its own column), and at 1150 px every column, Status
    included, is in view."""
    s = make()
    s.check()
    lst = _list(s)
    s.host.resize(1150, 500)
    lay = QVBoxLayout(s.host)
    lay.addWidget(lst)
    s.host.show()
    version = lst.model().index(0, 0)
    lst.expand(version)                    # closed at first since #131
    _APP.processEvents()
    line = QFontMetrics(QApplication.font()).height()
    for r in range(1, 4):
        child = lst.model().index(r, 0, version)
        assert lst.visualRect(child).height() < 2 * line
    status = lst.model().index(1, len(lst.Columns) - 1, version)
    rect = lst.visualRect(status)
    assert rect.left() < lst.viewport().width()
    # Follow-up (owner): some space after each column's widest text.
    metrics = QFontMetrics(lst.font())
    for column in range(1, len(lst.Columns) - 1):
        widest = max(metrics.horizontalAdvance(
            lst.model().index(r, column, version).data() or "") for r in range(1, 4))
        assert lst.header().sectionSize(column) >= widest + 14
    s.host.close()


def test_delivery_date_is_the_short_utc_form():
    """#134 (owner): the version header shows the short form of the one
    date style, in UTC and saying so ("27 Feb 2027, 22:00 UTC"); before it
    was the LOCAL time in the locale's format, without saying it. The
    month follows the plugin's language (Italian "feb", "ore")."""
    from bal import i18n

    assert lists_mod._delivery_text(D) == "27 Feb 2027, 22:00 UTC"

    class _Catalogs:
        """The repo's catalogs, compiled as the ZIP has them."""

        def read_file(self, path):
            from build_zip import compile_catalog
            root = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
            return compile_catalog(os.path.join(root, path).replace(".mo", ".po"))

    try:
        i18n.set_language(_Catalogs(), "it_IT")
        assert lists_mod._delivery_text(D) == "27 feb 2027, ore 22:00 UTC"
    finally:
        i18n.set_language(None, None)


def test_the_backup_transaction_says_so(make):
    s = make()
    s.plugin.NO_WILLEXECUTOR.set(True)
    s.bw.no_willexecutor = True
    s.check()
    lst = _list(s)
    version = lst.model().index(0, 0)
    assert lst.model().rowCount(version) == 5
    backup = lst.model().index(4, 0, version)
    cells = _cells(lst, backup)
    assert cells[2] == "No will-executor (backup transaction)"
    assert cells[3] == "" and cells[4] == ""
    assert "None" not in cells


def _seven_versions(make):
    """Seven versions: each Check one day earlier than the one before."""
    s = make()
    s.check()
    for day in range(1, 7):
        s.clock.travel(1)
        s.set_date(D - day * fk.DAY)
        s.check()
    return s


def test_last_five_versions_then_show_all(make):
    s = _seven_versions(make)
    lst = _list(s)
    top = _top(lst)
    assert [t.split(" ·")[0] for _k, _key, t in top[:-1]] == [
        "#7", "#6", "#5", "#4", "#3"]
    assert top[-1][0] == "show_all"
    # Owner's ZIP test: a real push button, with an arrow, on the last row.
    button = lst.history_button
    assert button.text() == "▼  Show all history (2 older versions)"
    assert lst.indexWidget(lst.model().index(len(top) - 1, 0)) is not None
    assert lst.hidden_versions == 2
    # One click on the button shows everything, and it stays so.
    button.click()
    shown = _top(lst)
    assert [t.split(" ·")[0] for _k, _key, t in shown[:-1]] == [
        "#7", "#6", "#5", "#4", "#3", "#2", "#1"]
    lst.update()
    assert len(_top(lst)) == 8 and lst.hidden_versions == 0
    # The whole history can be hidden again.
    assert _top(lst)[-1][0] == "hide_all"
    assert lst.history_button.text() == "▲  Hide history"
    lst.history_button.click()
    assert _top(lst)[-1][0] == "show_all" and lst.hidden_versions == 2
    assert len(_top(lst)) == 6


def test_open_and_closed_rows_survive_update(make):
    """The owner opens and closes the version in force and opens #5:
    update() and a new build keep both (by version identity, not by row
    number)."""
    s = _seven_versions(make)
    lst = _list(s)
    lst.expand(_version_index(lst, 7))
    lst.collapse(_version_index(lst, 7))
    lst.expand(_version_index(lst, 5))
    for _ in range(3):
        lst.update()
    assert not lst.isExpanded(_version_index(lst, 7))
    assert lst.isExpanded(_version_index(lst, 5))
    assert not lst.isExpanded(_version_index(lst, 6))
    # A new version arrives on top: the rows moved, the states did not.
    s.clock.travel(1)
    s.set_date(D - 7 * fk.DAY)
    s.check()
    lst.update()
    assert not lst.isExpanded(_version_index(lst, 8))       # new: closed (#131)
    assert not lst.isExpanded(_version_index(lst, 7))
    assert lst.isExpanded(_version_index(lst, 5))


def test_current_row_survives_update(make):
    s = make()
    s.check()
    lst = _list(s)
    child = lst.model().index(2, 0, lst.model().index(0, 0))
    txid = child.sibling(child.row(), 1).data(lst.ROLE_CLIPBOARD_DATA)
    lst.setCurrentIndex(child)
    lst.update()
    current = lst.selectionModel().currentIndex()
    assert lst._row_info(current) == ("variant", txid)


def test_double_click_and_menu_on_a_transaction_row(make):
    s = make()
    s.check()
    lst = _list(s)
    version = lst.model().index(0, 0)
    child = lst.model().index(3, 3, version)     # any column of the row
    txid = child.sibling(child.row(), 1).data(lst.ROLE_CLIPBOARD_DATA)
    opened = []
    with patch.object(s.bw, "show_transaction", lambda tx=None, **kw: opened.append(tx)):
        lst.on_double_click(child)
    assert [t.txid() for t in opened] == [txid]
    # show_transaction redraws the list: take the row again.
    version = lst.model().index(0, 0)
    child = lst.model().index(3, 3, version)
    assert child.sibling(child.row(), 1).data(lst.ROLE_CLIPBOARD_DATA) == txid
    menu = lst.build_menu(child)
    actions = {a.text(): a for a in menu.actions() if a.text()}
    # #136: "Copy" > each column (full values) before "copy id".
    assert list(actions) == ["details", "sign", "broadcast", "check ", "Copy",
                             "copy id", "copy", "merge from txn", "delete"]
    assert actions["details"].isEnabled()
    assert not actions["sign"].isEnabled()                    # already signed
    assert actions["broadcast"].isEnabled()
    # Sent to its will-executor: deleting it here would not cancel it.
    assert not actions["delete"].isEnabled()
    copied = []
    with patch.object(lst, "copy_txids", lambda keys: copied.append(keys)):
        actions["copy id"].trigger()
    assert copied == [[txid]]
    # A double click on a version row opens / closes it, and opens no
    # transaction window.
    version = lst.model().index(0, 0)
    lst.collapse(version)
    opened.clear()
    with patch.object(s.bw, "show_transaction", lambda tx=None, **kw: opened.append(tx)):
        lst.on_double_click(version)
        assert lst.isExpanded(version)
        lst.on_double_click(version)
        assert not lst.isExpanded(version)
    assert opened == []


def test_menu_of_a_version_row_acts_on_all_its_transactions(make):
    s = make()
    s.check()
    lst = _list(s)
    version = lst.model().index(0, 0)
    menu = lst.build_menu(version)
    actions = {a.text(): a for a in menu.actions()}
    # No "details" on a version row (owner: one window per will-executor).
    assert list(actions) == ["sign", "broadcast"]
    assert not actions["sign"].isEnabled()
    assert actions["broadcast"].isEnabled()
    sent = []
    with patch.object(s.bw, "broadcast_transactions",
                      lambda force=False, txids=None, **kw: sent.append((force, txids))):
        actions["broadcast"].trigger()
    (force, txids), = sent
    assert force is True and sorted(txids) == sorted(s.bw.willitems)


def test_draft_on_top_unnumbered_and_not_deletable(make):
    s = make()
    s.check()
    s.set_fee(22)
    s.check(closing=True, cancel_on=SIGN)
    lst = _list(s)
    top = _top(lst)
    assert top[0][2].startswith("delivery ")
    assert top[0][2].endswith("· draft")
    assert top[1][2].startswith("#1 · ")
    # Fix 2 (owner): a version no will-executor received says "not sent".
    assert "not sent" in top[0][2] and "sent and verified" not in top[0][2]
    assert "sent and verified 3 of 3" in top[1][2]
    m = lst.model()
    # Closed at first (#131); the user opens them.
    assert not lst.isExpanded(m.index(0, 0)) and not lst.isExpanded(m.index(1, 0))
    # Fix 1 (owner): the draft marked the sent will REPLACED (#128 keeps it
    # in force); its rows read in force, green, not "Replaced" in pink.
    in_force = m.index(1, 0)
    for r in range(3):
        cells = _cells(lst, m.index(r + 1, 0, in_force))
        item = s.bw.willitems[cells[1]]
        assert item.get_status("REPLACED")
        assert cells[5] == "In force, verified"
        colour = m.itemFromIndex(m.index(r + 1, 4, in_force)).background().color().name()
        assert colour == "#8afa6c"
        draft_cells = _cells(lst, m.index(r + 1, 0, m.index(0, 0)))
        assert draft_cells[5] == "Draft"
    draft_row = m.index(1, 0, m.index(0, 0))
    menu = lst.build_menu(draft_row)   # kept: its actions die with it
    actions = {a.text(): a for a in menu.actions() if a.text()}
    # #179 (owner, choice B): a draft is part of the will being prepared:
    # signed by the Check or dropped by it, never deleted by hand.
    assert not actions["delete"].isEnabled() and actions["sign"].isEnabled()
    sent_row = m.index(1, 0, m.index(1, 0))
    menu = lst.build_menu(sent_row)
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert not actions["delete"].isEnabled()


def test_search_looks_in_versions_and_transactions(make):
    s = make()
    s.check()
    s.clock.travel(1)
    s.add_coin(250_000)
    s.check()
    lst = _list(s)
    m = lst.model()
    lst.filter("we1.test")
    for r in range(m.rowCount()):
        version = m.index(r, 0)
        assert not lst.isRowHidden(r, QModelIndex())
        shown = [m.index(c, 2, version).data() for c in range(1, m.rowCount(version))
                 if not lst.isRowHidden(c, version)]
        # The sub-header stays above the transaction shown.
        assert not lst.isRowHidden(0, version)
        # #136: the cell shows the host only (the full URL is in Copy).
        assert shown == ["we1.test"]
    lst.filter("#2 ")
    assert [lst.isRowHidden(r, QModelIndex()) for r in range(m.rowCount())] == [False, True]
    # A matching version shows all its transactions.
    assert not any(lst.isRowHidden(c, m.index(0, 0)) for c in range(4))
    lst.filter("")
    assert not any(lst.isRowHidden(r, QModelIndex()) for r in range(m.rowCount()))


def test_multiverse_does_not_break_the_list(make):
    """A will in two deliveries (a table, #168; BAL's multiverse option
    before): one version, each first-delivery row holds its three
    instalments."""
    s = make()
    s.set_instalments((D, {"alice": "60%", "bob": "0%"}),
                      (D + 365 * fk.DAY, {"alice": "0%", "bob": "40%"}))
    s.check()
    lst = _list(s)
    m = lst.model()
    assert m.rowCount() == 1
    version = m.index(0, 0)
    assert m.rowCount(version) == 4
    for r in range(1, 4):
        root = m.index(r, 0, version)
        assert m.rowCount(root) == 3                  # its instalments
        assert not lst.isExpanded(root)
        child = m.index(0, 0, root)
        assert lst._row_info(child)[0] == "variant"
    lst.update()
    assert m.rowCount() == 1


def test_each_delivery_steps_right_in_every_column(make):
    """#170 (owner's ZIP test): in a will in deliveries the rows of delivery
    2 and 3 step right by two figure spaces per level in every column but
    the first (the tree indents that one); "Copy" keeps the plain values; a
    will with one date is unchanged."""
    step = "  "
    s = make()
    s.check(cancel_on=SIGN)
    lst = _list(s)
    m = lst.model()
    root = m.index(1, 0, m.index(0, 0))
    assert not any(str(t).startswith(" ") for t in _shown(lst, root))
    s = make()
    s.set_instalments((D, {"alice": "30%", "bob": "30%"}),
                      (D + 365 * fk.DAY, {"alice": "20%", "bob": "0%"}),
                      (D + 730 * fk.DAY, {"alice": "0%", "bob": "20%"}))
    s.check()
    lst = _list(s)
    m = lst.model()
    root = m.index(1, 0, m.index(0, 0))
    child = m.index(0, 0, root)
    grandchild = m.index(0, 0, child)
    for level, idx in enumerate((root, child, grandchild)):
        shown = _shown(lst, idx)
        assert not str(shown[0]).startswith(" ")
        for text in shown[1:]:
            assert text.startswith(step * level), (level, text)
            assert not text.startswith(step * (level + 1)), (level, text)
        assert not any(str(v).startswith(" ") for v in _cells(lst, idx))


# --------------------------------------------------------------- settings

def _settings_dialog(user_type):
    """The plugin's Settings dialog, built without showing it."""
    cfg = SimpleConfig({"electrum_path": tempfile.mkdtemp(prefix="bal_test_")})
    plugin = Plugin(None, cfg, "bal")
    plugin.get_window_title = lambda title: title
    plugin.read_file = lambda *a: b""
    plugin.broadcast_transactions = lambda *a, **k: None
    plugin.update_all = lambda *a, **k: None
    plugin.USER_TYPE.set(user_type)
    captured = []
    with patch.object(plugin_mod, "show_modal", side_effect=lambda d: captured.append(d)):
        plugin.settings_dialog(None, None)
    return plugin, captured[0]


@pytest.mark.usefixtures("yes_to_basic")
@pytest.mark.parametrize("user_type", ["basic", "advanced"])
def test_settings_without_the_hide_options(user_type):
    """The list's "Show all history" replaces them; the config keys stay
    readable and Reset leaves them alone."""
    plugin, dialog = _settings_dialog(user_type)
    texts = [lb.text() for lb in dialog.findChildren(QLabel)]
    assert "Hide Replaced" not in texts and "Hide Invalidated" not in texts
    plugin.HIDE_REPLACED.set(False)
    (reset_all,) = [b for b in dialog.findChildren(QPushButton)
                    if b.text() == "Reset to Default Setting"]
    reset_all.click()
    assert plugin.HIDE_REPLACED.get() is False
    assert plugin.HIDE_INVALIDATED.get() is True
    dialog.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_copy_gives_the_full_values(make):
    """#136 (owner): right click > Copy > a column copies the FULL value,
    never the shortened text the cell shows."""
    from unittest.mock import patch as _patch

    s = make()
    s.check()
    lst = _list(s)
    version = lst.model().index(0, 0)
    child = lst.model().index(1, 0, version)
    txid, executor, address = _cells(lst, child)[1:4]
    menu = lst.build_menu(child)
    copy = next(a for a in menu.actions() if a.text() == "Copy").menu()
    columns = {a.text(): a for a in copy.actions()}
    copied = []
    with _patch.object(lst, "place_text_on_clipboard",
                       lambda text, title=None: copied.append(text)):
        columns["Txid"].trigger()
        columns["Will-Executor"].trigger()
        columns["Fee address"].trigger()
        columns["Locktime"].trigger()
    assert copied[0] == txid and len(txid) == 64
    assert copied[1] == executor and executor.startswith("https://")
    assert copied[2] == address and "…" not in address
    assert copied[3].startswith("2027-02-27T22:00")


def test_invalidation_row_txid_is_short_and_whole_in_the_tooltip(make):
    """#136: the invalidation row shows its txid shortened, the tooltip
    has it whole (and the search finds it)."""
    s = _history_with_invalidation(make)
    lst = _list(s)
    first = lst.model().index(1, 0)
    txid = first.data(lst.ROLE_KEY)
    paint = first.data(lst.ROLE_CUSTOM_PAINT)
    assert txid[:8] + "…" + txid[-8:] in paint.sub and txid not in paint.sub
    assert txid in first.data(Qt.ItemDataRole.ToolTipRole)
    assert txid in first.data(lst.ROLE_SEARCH)
