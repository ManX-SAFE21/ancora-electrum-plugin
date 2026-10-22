"""
The will report as a clear PDF (CHANGELOG #92, #132).

#132 replaced the 78-column text report of #92 with A4 pages drawn with
QPainter, and removed "Save as file…" (the ``.txt``): the menu keeps "Save
as PDF…" and "Print…", which draw the same pages. The tests of the text
layout (60 lines x 78 columns, CRLF file, the .txt name) went with it.

* ``bal.core.report.will_report`` (the data, no Qt), on realistic wills
  made with ``tests/flowkit.py`` (three fake will-executors, each with its
  own fee address; throw-away test wallets with the test password; fixed
  clock): the heirs with the REAL amounts and their full addresses, the
  total, one transaction per will-executor with its fee and full id, the
  instalments (multiverse on: two instalments), the wills sent before;
* ``bal.gui.qt.report_pages`` (layout and painting): page 1 (title, state,
  delivery in UTC, time line, heirs with full addresses and total,
  will-executors line and explanation, how it works), page 2 (technical:
  every txid in full), the footer "PRIVATE — keep it in a safe place" and
  "Page x/y" on every page, no "Heartbeat" in the header, the logo (image,
  or the vector logo when the image cannot be read), everything inside
  the page; 30 and 40 will-executors (synthetic): listed two per row with
  their small square, flowing to the next page without splitting a row;
  multiverse (synthetic, 9 will-executors x 4 instalments): 3 pages, "How
  it works" never alone, the instalment table, the grid page, technical
  txids for instalment 1 only and counts for the others;
* ``bal.gui.qt.report_actions``: the menu has only "Save as PDF…" and
  "Print…", saving writes one PDF page per report page, printing draws the
  same pages (printer dialog mocked), the owner's rule (only for a will in
  force and verified), the "Will report" button of the Building Will dialog.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_inheritance_report.py
"""

import hashlib
import os
import re
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

# The helpers of the #84b-1 tests: will items and a dialog without Qt set-up.
from test_build_report import _dialog, _item

from bal.core import brand, datetext
from bal.core import future as cf
from bal.core import report as core_report
from bal.gui.qt import report_actions
from bal.gui.qt import report_pages as rp
from bal.gui.qt.dialogs import BalBuildWillDialog

_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D = fk.ts(2027, 8, 27, 22)
URLS = ["https://we0.test", "https://we1.test", "https://we2.test"]
CREATED = datetime(2026, 10, 13, 15, 42)
FOOTER = "PRIVATE — keep it in a safe place"


def _sat(sats):
    """Amounts as plain satoshis (the tests do not depend on the unit)."""
    return "{} sat".format(sats)


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions with three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(mode="advanced", **kw):
        """One session (``fk.Session`` arguments)."""
        kw.setdefault("password", TEST_PASSWORD)
        kw.setdefault("fee", 21)
        kw.setdefault("locktime", D)
        kw.setdefault("threshold", "-90d")
        s = fk.Session(monkeypatch, mode=mode, **kw)
        s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.bw.window.config = SimpleNamespace(format_amount_and_units=_sat)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _report(s, still_active=None):
    """The report data of the session's will, at the scenario's clock."""
    return core_report.will_report(
        s.bw.willitems, s.bw.heirs, s.bw.will_settings, s.plugin.is_basic_mode(),
        wallet=s.wallet, wallet_name="wallet_test", plugin_version="0.7.0",
        electrum_version="4.8.2", now=s.clock.now, created=CREATED,
        still_active=still_active if still_active is not None
        else s.bw._still_active_for_summary(),
    )


def _executor_item(s, url):
    """The will item of the will-executor ``url``."""
    return next(w for w in s.bw.willitems.values() if (w.we or {}).get("url") == url)


def _check_pages(pages):
    """Footer on every page, everything inside the page, nothing over the footer."""
    total = len(pages)
    for page in pages:
        texts = rp.page_texts(page)
        assert FOOTER in texts
        assert "Page {}/{}".format(page.number, total) in texts
        for item in page.items:
            r = item.rect
            assert r.left() >= -0.01 and r.right() <= rp.PAGE_W + 0.01, (item.kind, item.text)
            assert r.top() >= -0.01 and r.bottom() <= rp.PAGE_H + 0.01, (item.kind, item.text)
            footer = item.text in (FOOTER, "Page {}/{}".format(page.number, total))
            if not footer and item.kind != "line":
                assert r.bottom() <= rp.PAGE_H - rp.FOOTER_H + 0.01, (item.kind, item.text)


# ------------------------------------------------------------- core data

def test_report_data_of_the_will_in_force(make):
    """Three will-executors, alice 60 % and bob 40 %: the amounts the
    transaction pays, the full addresses, one transaction per executor with
    its own fee and full id, one instalment of three transactions."""
    s = make()
    s.check()
    r = _report(s)
    assert r.summary["state"] == "ok" and r.summary["title"] == "Your will is in force"
    assert r.delivery == D and not r.multiverse and r.miner_fee == "21 sat/vB"
    paid = {o.address: o.value for o in _executor_item(s, URLS[0]).tx.outputs()}
    assert [(h.name, h.address, h.sats) for h in r.heirs] == [
        ("alice", fk.HEIR_ADDR["alice"], paid[fk.HEIR_ADDR["alice"]]),
        ("bob", fk.HEIR_ADDR["bob"], paid[fk.HEIR_ADDR["bob"]]),
    ]
    assert [round(h.percent) for h in r.heirs] == [60, 40]
    assert r.total_sats == paid[fk.HEIR_ADDR["alice"]] + paid[fk.HEIR_ADDR["bob"]]
    assert [t.url for t in r.first_txs] == URLS
    for tx in r.first_txs:
        item = _executor_item(s, tx.url)
        assert tx.txid == item._id and len(tx.txid) == 64
        assert tx.fee == 1000 and tx.locktime == D
    assert len(r.instalments) == 1
    inst = r.instalments[0]
    assert inst.tx_count == 3 and inst.verified == 3 and set(inst.states) == set(URLS)
    assert r.older == []


def test_report_data_multiverse_two_instalments(make):
    """A table of deliveries (#168; BAL's hidden option before): alice at
    D, bob one year later. Two instalments; the first has one transaction
    per will-executor, each later one spends the change of every
    transaction before it. The heirs under their own names, never the
    row "bob§2" of delivery 2."""
    s = make()
    s.set_instalments((D, {"alice": "60%", "bob": "0%"}),
                      (D + 365 * fk.DAY, {"alice": "0%", "bob": "40%"}))
    s.check()
    r = _report(s)
    assert r.multiverse
    assert [i.locktime for i in r.instalments] == [D, D + 365 * fk.DAY]
    assert [h.name for h in r.instalments[0].heirs] == ["alice"]
    assert [h.name for h in r.instalments[1].heirs] == ["bob"]
    assert r.instalments[0].tx_count == 3
    assert r.instalments[1].tx_count == 9
    assert [t.url for t in r.first_txs] == URLS
    assert all(t.locktime == D for t in r.first_txs)
    assert {h.name: h.address for h in r.heirs} == {
        "alice": fk.HEIR_ADDR["alice"], "bob": fk.HEIR_ADDR["bob"]}


def test_report_lists_the_wills_sent_before(make):
    """A will sent before and still executable (the will summary's list) is
    in the technical details; the copies of the will in force are not."""
    s = make()
    s.check()
    current = _executor_item(s, URLS[1])
    old = SimpleNamespace(_id="ab" * 32, tx=SimpleNamespace(locktime=D + 86400),
                          we={"url": URLS[1], "base_fee": 500})
    r = _report(s, still_active=[(old, D + 86400, URLS[1]),
                                 (current, D, URLS[1])])
    assert [(t.txid, t.host, t.locktime) for t in r.older] == [
        ("ab" * 32, "we1.test", D + 86400)]


def test_executor_fee_is_the_fee_output():
    """The fee shown (Will tab and report) is the output to the executor's
    fee address, else its base fee."""
    item = _item("COMPLETE")
    assert core_report.executor_fee(item) == 1000
    assert core_report.executor_fee(SimpleNamespace(we=None)) is None


def test_report_only_for_a_verified_will_in_force():
    """Owner's rule: not to sign, to send, to verify, failed, Check Alive..."""
    assert core_report.report_block_reason({"state": "ok"}) is None
    for state in ("to_sign", "to_send", "not_received", "none_received",
                  "check_alive", "expired", "still_active", "none", None):
        assert core_report.report_block_reason({"state": state} if state else None)
    # Sent but not verified yet: the menu entries are grey, the reason shown.
    # #178: its will-executor is selected (one not selected that has the
    # will is "excluded" and no longer verified: it does not lock it).
    item = _item("COMPLETE", "PUSHED")
    bw = _fake_window({item._id: item})
    url = item.we["url"]
    with patch.object(report_actions.Willexecutors, "get_willexecutors",
                      lambda *a, **k: {url: {"url": url, "selected": True}}):
        menu = report_actions.report_menu(bw, QWidget())
        actions = menu.actions()
        assert actions[0].isVisible() and "verified by every will-executor" in actions[0].text()
        assert not any(a.isEnabled() for a in actions)
        # And the actions refuse too (a defence: the menu is checked on opening).
        with patch.object(report_actions, "getSaveFileName") as ask:
            report_actions.save_report_pdf(bw)
            report_actions.print_report(bw)
        assert not ask.called and bw.show_warning.call_count == 2


def _fake_window(willitems):
    """A window stand-in for the menu and the rule (no wallet)."""
    bw = MagicMock()
    bw.willitems = willitems
    bw.heirs = {"Erede 1": ["tb1qalice", "75%", "4y"]}
    bw.will_settings = {"locktime": "4y", "threshold": "-180d", "baltx_fees": 10}
    bw.bal_plugin.is_basic_mode.return_value = True
    bw._still_active_for_summary.return_value = []
    return bw


# ------------------------------------------------------------- the pages

def test_pdf_pages_of_a_realistic_will(make):
    """Page 1 for everyone, page 2 technical; footer and "Page x/y" on both."""
    s = make()
    s.check()
    r = _report(s)
    pages = rp.layout_report(r, _sat)
    assert len(pages) == 2
    _check_pages(pages)
    first = rp.page_texts(pages[0])
    joined = "\n".join(first)
    for expected in ("Will plan", "Created on: 13 October 2026, 15:42", "Wallet: wallet_test",
                     "Plugin: 0.7.0 · Electrum 4.8.2", "Your will is in force",
                     "Delivery to the heirs:", "Will-Executor:",
                     "3 of 3 received and verified it", "21 sat/vB", "HEIRS",
                     "alice", rp.address_groups(fk.HEIR_ADDR["alice"]),
                     "bob", rp.address_groups(fk.HEIR_ADDR["bob"]),
                     "60 %", "40 %", "Total to the heirs", _sat(r.total_sats),
                     "WILL-EXECUTORS", "we0.test", "we1.test", "we2.test",
                     "received and verified",
                     "Will-executors keep a copy of your will and broadcast it on "
                     "the delivery date."):
        assert expected in first, expected
    # #145: the taller heir rows (the QR code) leave no room for "How it
    # works" on page 1: it opens page 2, never alone on a page.
    assert "HOW IT WORKS" in rp.page_texts(pages[1])
    # The delivery: the UTC day and time with the time left; the time line
    # shows the same date.
    assert "27 August 2027, 22:00 UTC (in 10 months and 17 days)" in first
    assert ("If the will is no longer checked or renewed, on 27 August 2027, 22:00 UTC"
            in "\n".join(rp.page_texts(pages[1])))
    for heir in r.heirs:
        assert _sat(heir.sats) in first
    # Nothing technical on page 1: the ids are on page 2, in full.
    second = rp.page_texts(pages[1])
    assert "TECHNICAL DETAILS" in second
    for tx in r.first_txs:
        assert tx.txid not in joined and tx.txid in second
    assert "Executor fee: 1000 sat" in second
    assert "Locktime: {} · 27 August 2027, 22:00 UTC".format(D) in second
    assert "Wills sent before and still held by the will-executors" in second
    assert any(t.startswith("None: the will-executors hold no other will") for t in second)
    assert "Will plan · TECHNICAL DETAILS" in second


def _colour_pixels(image, colour):
    """How many pixels of ``image`` have exactly ``colour``."""
    target = QColor(colour).rgb()
    return sum(1 for y in range(image.height()) for x in range(image.width())
               if image.pixel(x, y) == target)


def _status_rows(page):
    """``[(label, value)]`` of the state box: the label texts end with ":"."""
    texts = rp.page_texts(page)
    return [(t, texts[i + 1]) for i, t in enumerate(texts) if t.endswith(":")
            and i + 1 < len(texts)]


@pytest.mark.parametrize("mode, kw, form, flag", [
    ("advanced", {}, "Fixed date", True),
    ("advanced", {"period_months": 24}, "Period (renewable) — 24 months (2 years)", True),
    ("basic", {"threshold": "-30d"}, "Fixed date", False),
])
def test_delivery_form_and_check_alive_flag(make, mode, kw, form, flag):
    """#132 (owner): the state box says the delivery form right under the
    delivery; the time line's Check Alive flag is orange-red, and there is
    no flag (nor Check Alive row) in BASIC with a fixed date."""
    from bal.gui.qt import future_view as fv

    s = make(mode=mode, **kw)
    s.check()
    pages = rp.layout_report(_report(s), _sat)
    rows = _status_rows(pages[0])
    labels = [label for label, _value in rows]
    assert labels.index("Delivery form:") == labels.index("Delivery to the heirs:") + 1
    assert dict(rows)["Delivery form:"] == form
    assert ("Check Alive:" in labels) is flag
    timeline = next(i for i in pages[0].items if i.kind == "timeline")
    assert (timeline.scene["check_alive"] is not None) is flag
    if flag:
        assert timeline.scene["check_alive"]["colour"] == fv.CHECK_ALIVE_COLOUR
    orange = _colour_pixels(rp.page_image(pages[0], None, dpi=110), fv.CHECK_ALIVE_COLOUR)
    assert (orange > 20) is flag and (orange == 0) is not flag


def test_header_logo_and_no_heartbeat(make):
    """Owner: the logo only, without "Heartbeat"; the logo is painted from
    the image, or as a vector logo when the image cannot be read."""
    s = make()
    s.check()
    pages = rp.layout_report(_report(s), _sat)
    header = [i for i in pages[0].items if i.rect.top() < 60]
    assert any(i.kind == "logo" for i in header)
    assert not any("Heartbeat" in i.text for i in header)
    assert all(any(i.kind == "logo" for i in p.items) for p in pages)
    # The image shipped with the plugin (read as the plugin reads it).
    path = os.path.join(os.path.dirname(__file__), os.pardir, "bal", rp.LOGO_FILE)
    logo = rp.load_logo(lambda name: open(os.path.join(
        os.path.dirname(path), os.path.basename(name)), "rb").read())
    assert logo is not None and logo.width() == 1200
    assert rp.load_logo(lambda name: b"") is None              # -> vector logo
    # #163: the àncòra logo, in its own colours, with the image and with
    # the vector fallback. At 150 dpi (2.08 px per point, 15 mm margin =
    # 88.6 px) the logo is 30 points high: the anchor is 140 units high, so
    # one unit = 30/140 point, and the logo starts 2 points down.
    def at(image, x, y):
        """The colour at (x, y) units of the anchor."""
        k, s = 30.0 / 140.0, 150 / 72.0
        return image.pixelColor(round(88.6 + x * k * s), round(88.6 + (2 + y * k) * s))

    def near(colour, hex_colour):
        """True when ``colour`` is close to ``hex_colour``."""
        c = QColor(hex_colour)
        return all(abs(a - b) < 60 for a, b in (
            (colour.red(), c.red()), (colour.green(), c.green()), (colour.blue(), c.blue())))

    for image in (rp.page_image(pages[0], logo), rp.page_image(pages[0], None)):
        assert near(at(image, 70, 18), brand.WATER)   # the ring, right side
        assert near(at(image, 40, 56), brand.INK)     # the stock
        assert near(at(image, 60, 130), brand.INK)    # the bottom of the arm


def _many_executors(count):
    """A will with ``count`` will-executors (synthetic): all verified except
    two to be verified and one that did not receive it."""
    now = fk.FIXED_NOW
    states = ["verified"] * (count - 3) + ["to_verify"] * 2 + ["failed"]
    urls = ["https://we{:02d}.test".format(i) for i in range(count)]
    model = cf.Future(
        state=cf.STATE_IN_FORCE, now=now, title="Your will is in force",
        deliveries=[cf.Instalment(locktime=D, heirs=[
            cf.HeirShare("alice", 600_000, 60.0), cf.HeirShare("bob", 400_000, 40.0)])],
        level="green", time_left=cf.time_left_text(D, now),
        executors=[cf.ExecutorMark(u, st) for u, st in zip(urls, states, strict=True)],
        executors_text="{} of {} verified it".format(count - 3, count),
    )
    return _synthetic(model, urls, [D]), urls


@pytest.mark.parametrize("count", [30, 40])
def test_many_executors_listed_two_per_row(count):
    """30 and 40 will-executors (synthetic): no row of big squares (owner,
    #132 preview); each executor is listed once with its small square, two
    per row; the rows flow to the next page, a row (squares and texts) is
    never split and the section title stays with its first row."""
    r, urls = _many_executors(count)
    pages = rp.layout_report(r, _sat)
    _check_pages(pages)
    hosts = {url[8:] for url in urls}
    where = {}
    for page in pages:
        for item in page.items:
            # The list rows (x > 0, after the small square), not the
            # technical rows (x = 0).
            if item.kind == "text" and item.text in hosts and item.rect.left() > 1:
                assert item.text not in where
                where[item.text] = (page.number, item.rect.top())
    assert set(where) == hosts
    squares = [(p.number, i.rect) for p in pages for i in p.items
               if i.kind == "rect" and i.rect.width() == 8]
    assert len(squares) == count
    # Two per row: each row's two hosts share a page and a line.
    for left, right in zip(urls[0::2], urls[1::2], strict=True):
        assert where[left[8:]] == where[right[8:]]
    # The title is on the page of the first executor.
    title_page = next(p.number for p in pages if "WILL-EXECUTORS" in rp.page_texts(p))
    assert title_page == where[urls[0][8:]][0]
    assert not any(i.kind == "rect" and i.rect.width() == 14 for p in pages for i in p.items)
    assert "not received" in [t for p in pages for t in rp.page_texts(p)]


def _synthetic(model, urls, dates, states=None):
    """A :class:`WillReport` made by hand (multiverse is off in the plugin)."""
    states = states or {u: m.state for u, m in zip(urls, model.executors, strict=True)}
    insts = [core_report.ReportInstalment(
        number=n + 1, locktime=d, heirs=model.deliveries[n].heirs,
        tx_count=len(urls) ** (n + 1), states=dict(states)) for n, d in enumerate(dates)]
    txs = [core_report.ReportTx(url=u, locktime=dates[0], fee=1000 + i,
                                txid=hashlib.sha256(u.encode()).hexdigest())
           for i, u in enumerate(urls)]
    total = {}
    for inst in model.deliveries:
        for h in inst.heirs:
            total[h.name] = total.get(h.name, 0) + h.sats
    heirs = [core_report.ReportHeir(n, "tb1q" + n * 6, v, 100.0 * v / sum(total.values()))
             for n, v in sorted(total.items())]
    return core_report.WillReport(
        created=CREATED, wallet_name="wallet_test", plugin_version="0.7.0",
        electrum_version="4.8.2", summary={"state": "ok", "kind": "ok",
                                           "title": model.title},
        future=model, miner_fee="10 sat/vB", heirs=heirs, instalments=insts,
        first_txs=txs)


def test_multiverse_nine_executors_four_instalments():
    """Synthetic multiverse (owner: 9 will-executors x 4 instalments, Luca
    and John 10/10/15/15 %): the instalment table, the grid page with the
    transaction counts, the technical ids of instalment 1 only."""
    now = fk.FIXED_NOW
    dates = [fk.ts(2028, 3, 1, 22), fk.ts(2030, 3, 1, 22),
             fk.ts(2032, 3, 1, 22), fk.ts(2035, 3, 1, 22)]
    shares = [10, 10, 15, 15]
    deliveries = [cf.Instalment(locktime=d, heirs=[
        cf.HeirShare("John", 1_000_000 * p, p), cf.HeirShare("Luca", 1_000_000 * p, p)])
        for d, p in zip(dates, shares, strict=True)]
    urls = ["https://we{}.test".format(i) for i in range(9)]
    model = cf.Future(
        state=cf.STATE_IN_FORCE, now=now, title="Your will is in force",
        deliveries=deliveries, level="green", time_left=cf.time_left_text(dates[0], now),
        executors=[cf.ExecutorMark(u, "verified") for u in urls],
        executors_text="9 of 9 received and verified it",
    )
    r = _synthetic(model, urls, dates)
    assert r.multiverse
    pages = rp.layout_report(r, _sat)
    _check_pages(pages)
    # Owner (#132 preview): 3 pages; "How it works" never alone on a page.
    assert len(pages) == 3
    for page in pages:
        texts = rp.page_texts(page)
        if "HOW IT WORKS" in texts:
            assert page.number == 1 or "WILL-EXECUTORS PER DELIVERY" in texts
    # #145: the taller heir rows push the end of the table to page 2.
    first = rp.page_texts(pages[0]) + rp.page_texts(pages[1])
    assert "DELIVERIES" in rp.page_texts(pages[0])
    for n, (d, p) in enumerate(zip(dates, shares, strict=True), 1):
        assert str(n) in first and datetext.utc_short(d) in first
        assert "John {0} %, Luca {0} %".format(p) in first
    assert first.count("sent and verified 9 of 9") == 4
    # The grid continues page 2 when it fits whole.
    grid = next(p for p in pages if "WILL-EXECUTORS PER DELIVERY" in rp.page_texts(p))
    assert grid.number == 2
    grid_texts = rp.page_texts(grid)
    assert [t for t in grid_texts if t.startswith("Delivery ") and "·" not in t] == [
        "Delivery 1", "Delivery 2", "Delivery 3", "Delivery 4"]
    assert any("Delivery 1: 9, Delivery 2: 81, Delivery 3: 729, "
               "Delivery 4: 6561" in t for t in grid_texts)
    squares = [i for i in grid.items if i.kind == "rect" and i.rect.width() == 10]
    assert len(squares) == 9 * 4
    tech = [t for p in pages if p.number > grid.number for t in rp.page_texts(p)]
    for tx in r.first_txs:
        assert tx.txid in tech
    assert sum(len(t) == 64 and re.fullmatch("[0-9a-f]+", t) is not None for t in tech) == 9
    assert any(t.startswith("Delivery 2 · ") and "Transactions: 81" in t for t in tech)
    assert any(t.startswith("Delivery 4 · ") and "Transactions: 6561" in t for t in tech)
    assert any("(Tools → Export)" in t for t in tech)


def test_backup_only_will_is_explained():
    """No will-executor: the report says that nobody else holds the will."""
    now = fk.FIXED_NOW
    model = cf.Future(
        state=cf.STATE_IN_FORCE, now=now, title="Your will is in force",
        deliveries=[cf.Instalment(locktime=D, heirs=[cf.HeirShare("alice", 900, 100.0)])],
        level="green", time_left=cf.time_left_text(D, now))
    r = _synthetic(model, [], [D], states={})
    r.first_txs = [core_report.ReportTx(url=None, locktime=D, txid="cd" * 32)]
    pages = rp.layout_report(r, _sat)
    _check_pages(pages)
    texts = [t for p in pages for t in rp.page_texts(p)]
    assert "none (backup transaction only)" in texts
    assert any(t.startswith("No will-executor holds your will") for t in texts)
    assert "no will-executor (backup transaction)" in texts and "cd" * 32 in texts


# ----------------------------------------------------------- the actions

def test_default_file_name():
    """A PDF on the Desktop, named after the wallet and the day."""
    assert report_actions.default_file_name("My wallet (1)", CREATED) == (
        "safe21-report-My_wallet_1-2026-10-13.pdf")
    assert report_actions.default_file_name("", CREATED) == (
        "safe21-report-wallet-2026-10-13.pdf")


def test_save_as_pdf_one_sheet_per_page(make):
    """Save as PDF: the file chosen, one PDF page per report page."""
    s = make()
    s.check()
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "report.pdf")
        with patch.object(report_actions, "getSaveFileName", return_value=target) as ask:
            report_actions.save_report_pdf(s.bw)
        assert ask.call_args.kwargs["filename"].endswith(".pdf")
        pdf = open(target, "rb").read()
    assert pdf.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf)) == 2
    assert not any(kind == "warning" for kind, *_rest in s.bw.popups)
    assert any(kind == "message" and target in text for kind, text in s.bw.popups)


def test_print_draws_the_same_pages(make):
    """Print: the printer dialog (mocked, accepted), then the same pages
    as the PDF, one per sheet."""
    from PyQt6.QtPrintSupport import QPrinter
    from PyQt6.QtWidgets import QDialog

    s = make()
    s.check()
    drawn = []
    real = rp.print_pages

    def spy(printer, pages, logo=None):
        """Record the pages, then draw them for real."""
        drawn.append(pages)
        real(printer, pages, logo)

    class Dialog:
        """The printer dialog: accepted at once."""

        def __init__(self, printer, parent=None):
            """Keep nothing: the printer is already set."""

        def setWindowTitle(self, title):  # noqa: N802 - Qt name
            """No window."""

        def exec(self):
            """The owner presses Print."""
            return QDialog.DialogCode.Accepted

    original = report_actions.new_printer
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "printed.pdf")

        def new_printer():
            """The report's printer, writing to a PDF instead of paper."""
            p = original()
            p.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            p.setOutputFileName(path)
            return p

        with patch("PyQt6.QtPrintSupport.QPrintDialog", Dialog), \
                patch.object(report_actions, "new_printer", new_printer), \
                patch.object(rp, "print_pages", spy):
            report_actions.print_report(s.bw)
        pdf = open(path, "rb").read()
    assert len(drawn) == 1
    expected, _logo = report_actions.build_pages(s.bw)
    assert [rp.page_texts(p) for p in drawn[0]] == [rp.page_texts(p) for p in expected]
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf)) == len(expected)


def test_menu_has_only_pdf_and_print():
    """#132: "Save as file…" (.txt) is gone; "Save as PDF…" and "Print…"."""
    item = _item("COMPLETE", "PUSHED", "CHECKED")
    menu = report_actions.report_menu(_fake_window({item._id: item}), QWidget())
    visible = [a.text() for a in menu.actions() if a.isVisible()]
    assert visible == ["Save as PDF…", "Print…"]
    assert not hasattr(report_actions, "save_report")
    assert not hasattr(core_report, "report_text")


def test_building_will_dialog_has_the_report_button():
    """The "Will report" button of the Building Will dialog opens the menu."""
    bw = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")}).bal_window
    bw.bal_plugin.read_file.return_value = b""
    bw.window.format_amount_and_units.side_effect = _sat
    d = BalBuildWillDialog(bw, parent=QWidget())
    try:
        d._add_close_button()
        buttons = [b for b in d.findChildren(QPushButton) if b.text() == "Will report"]
        assert len(buttons) == 1 and buttons[0].menu() is not None
        visible = [a for a in buttons[0].menu().actions() if a.isVisible()]
        assert [a.text() for a in visible] == ["Save as PDF…", "Print…"]
        assert all(a.isEnabled() for a in visible)              # verified will
    finally:
        d.thread.stop()
        d.deleteLater()
