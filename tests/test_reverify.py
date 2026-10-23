"""The Check asks again the copies already verified (CHANGELOG #182).

Owner's ZIP test of #181 (2026-10-22): every Check, even with the Wi-Fi
off, read "received and verified" 5/5: a copy verified once was never asked
again, so a will-executor that lost it, or closed, would read "verified"
for years. Owner's decisions:

* a will with one delivery (basic or advanced mode): asked again at every
  Check; a will in several deliveries: once its verification is a day old;
* a copy verified before that gets no answer now: "no answer to the check"
  (no longer verified), never sent again;
* no connection at all is "no answer" too (like the 503 of #180);
* the time of the last verification (and the next one of a will in several
  deliveries) is shown in the Check window and in the Will tab.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address; the will with three delivery dates of
``test_multiverse_chain_rebuild.py`` and a will with one date.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_reverify.py
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from aiohttp import ServerDisconnectedError
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
from test_build_report import _item
from test_check_unavailable import REAL_CHECK, _HttpError

from bal.core import datetext
from bal.core.build_report import BuildReport
from bal.core.summary import verified_text, verified_times
from bal.core.will import REVERIFY_SECONDS_MULTI, Will, WillItem
from bal.core.willexecutors import Willexecutors
from bal.engine.verify import verify_targets
from bal.gui.qt.widgets import BuildReportView

# The sessions of the chain tests (three will-executors, synchronized).
make = chain.make

WE0 = "https://we0.test"
WE1 = "https://we1.test"
WE2 = "https://we2.test"
NOW = 1_800_000_000
HOUR = 60 * 60


def _verified(url=WE0, locktime=None, when=None):
    """A VALID copy, sent and verified at ``when`` (None: before #182)."""
    w = _item("COMPLETE", "PUSHED", "CHECKED", url=url)
    if locktime is not None:
        w.tx.locktime = locktime
    w.checked_time = when
    return w


# ------------------------------------------------------- the copy itself

def test_the_time_of_the_verification_is_kept():
    w = _item("COMPLETE", "PUSHED", url=WE0)
    assert w.set_check_willexecutor({"tx": str(w.tx)}, now=NOW)
    assert w.get_status("CHECKED") and w.checked_time == NOW
    data = w.to_dict()
    assert data["checked_time"] == NOW
    assert WillItem(data).checked_time == NOW
    assert w.copy().checked_time == NOW


def test_a_copy_verified_before_keeps_its_saved_form():
    w = _item("COMPLETE", "PUSHED", "CHECKED", url=WE0)
    assert w.checked_time is None
    assert "checked_time" not in w.to_dict()


def test_no_answer_on_a_verified_copy_is_no_longer_verified():
    w = _verified(when=NOW)
    assert not w.set_check_willexecutor(Willexecutors.NO_ANSWER)
    assert not w.get_status("CHECKED")
    assert w.get_status("PUSHED") and not w.get_status("CHECK_FAIL")
    sent = _item("COMPLETE", "PUSHED", url=WE0)
    sent.set_check_willexecutor(Willexecutors.NO_ANSWER)
    assert sent.get_status("PUSHED") and not sent.get_status("CHECK_FAIL")


# ---------------------------------------------------- which copies are asked

def test_one_delivery_is_asked_again_at_every_check():
    will = {"a": _verified(WE0, when=NOW), "b": _verified(WE1, when=NOW),
            "c": _verified(WE2, when=NOW)}
    assert Will.reverify_period(will) == 0
    targets, _skipped = verify_targets(will, now=NOW)
    assert sorted(targets) == [("a", WE0), ("b", WE1), ("c", WE2)]


def test_several_deliveries_are_asked_again_after_a_day():
    will = {"a": _verified(WE0, 1_900_000_000, NOW),
            "b": _verified(WE1, 1_900_000_000, NOW),
            "c": _verified(WE2, 2_000_000_000, NOW - REVERIFY_SECONDS_MULTI)}
    assert Will.reverify_period(will) == REVERIFY_SECONDS_MULTI
    targets, _skipped = verify_targets(will, now=NOW + HOUR)
    assert targets == [("c", WE2)]
    targets, _skipped = verify_targets(will, now=NOW + REVERIFY_SECONDS_MULTI)
    assert len(targets) == 3


def test_a_copy_verified_before_182_is_asked_and_excluded_never():
    will = {"a": _verified(WE0, 1_900_000_000, None),
            "b": _verified(WE1, 2_000_000_000, NOW)}
    targets, _skipped = verify_targets(will, now=NOW + HOUR)
    assert targets == [("a", WE0)]
    targets, _skipped = verify_targets(will, now=NOW + HOUR, selected={WE1})
    assert targets == []


def test_never_a_draft_nor_a_replaced_copy():
    draft = _item("COMPLETE", url=WE0)
    old = _verified(WE1, when=NOW)
    old.set_status("REPLACED")
    targets, _skipped = verify_targets({"d": draft, "o": old}, now=NOW)
    assert targets == []


# ------------------------------------------------------- no connection

def test_no_connection_is_no_answer(monkeypatch):
    for error in (ConnectionError("You are offline."),
                  OSError("name not resolved"), ServerDisconnectedError()):
        monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
            MagicMock(side_effect=error)))
        assert REAL_CHECK("abc", WE0) is Willexecutors.NO_ANSWER


def test_an_answer_with_an_error_is_still_a_failure(monkeypatch):
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
        MagicMock(side_effect=_HttpError(500))))
    try:
        REAL_CHECK("abc", WE0)
    except _HttpError:
        pass
    else:
        raise AssertionError("the error was not raised")


# ------------------------------------------------------------ the texts

def test_the_text_of_the_last_and_next_check():
    """The short date (owner's ZIP test: the long one did not fit)."""
    assert verified_text(None) == ""
    assert verified_text(NOW) == "last check " + datetext.local_short(NOW)
    assert verified_text(NOW, NOW + REVERIFY_SECONDS_MULTI) == (
        "last check {} · next check from {}".format(
            datetext.local_short(NOW),
            datetext.local_short(NOW + REVERIFY_SECONDS_MULTI)))
    assert "Oct" in datetext.local_short(1_792_000_000)


def test_the_times_of_a_will():
    one = {"a": _verified(WE0, when=NOW), "b": _verified(WE1, when=NOW - HOUR)}
    assert verified_times(one) == (NOW - HOUR, None)
    assert verified_times(one, url=WE0) == (NOW, None)
    assert verified_times(one, selected={WE0}) == (NOW, None)
    many = {"a": _verified(WE0, 1_900_000_000, NOW),
            "b": _verified(WE1, 2_000_000_000, NOW)}
    assert verified_times(many) == (NOW, NOW + REVERIFY_SECONDS_MULTI)
    unknown = {"a": _verified(WE0, when=None), "b": _verified(WE1, when=NOW)}
    assert verified_times(unknown) == (None, None)


def test_the_check_window_row_shows_the_date():
    r = BuildReport()
    r.set_executor(WE0, "verified")
    r.set_executor(WE1, "verified")
    r.set_verified({WE0: (NOW, NOW + REVERIFY_SECONDS_MULTI), WE1: (None, None)})
    snap = r.snapshot()
    assert snap["verified"] == {WE0: [NOW, NOW + REVERIFY_SECONDS_MULTI]}
    view = BuildReportView()
    view.render(snap)
    shown = [lbl.text() for lbl in view.findChildren(QLabel)]
    assert "✔ received and verified on " + datetext.local_short(NOW) in shown
    assert "✔ received and verified" in shown                     # WE1: no time
    assert "Next check from " + datetext.local_short(
        NOW + REVERIFY_SECONDS_MULTI) in shown
    assert BuildReport().snapshot().get("verified") is None


def test_one_delivery_has_no_next_check_line():
    r = BuildReport()
    r.set_executor(WE0, "verified")
    r.set_verified({WE0: (NOW, None)})
    view = BuildReportView()
    view.render(r.snapshot())
    assert not [lbl for lbl in view.findChildren(QLabel)
                if lbl.text().startswith("Next check from")]


def test_the_exclude_button_stays_in_a_narrow_window():
    """Owner's ZIP test (2026-10-23): the long rows pushed "Exclude" and
    "Retry" out of the window. Now the texts wrap: at 600 px both buttons
    are inside."""
    r = BuildReport()
    for url in (WE0, WE2, "https://www.bitcoin-afterlife.example"):
        r.set_executor(url, "verified")
    r.set_executor(WE1, "no_check_answer", 15)
    r.set_verified({u: (NOW, NOW + REVERIFY_SECONDS_MULTI)
                    for u in (WE0, WE2, "https://www.bitcoin-afterlife.example")})
    view = BuildReportView()
    view.on_exclude = MagicMock()
    view.on_retry = MagicMock()
    view.render(r.snapshot())
    view.resize(600, 600)
    view.show()
    QApplication.processEvents()
    try:
        buttons = [b for b in view.findChildren(QPushButton)
                   if b.text() in ("Exclude", "Retry")]
        assert len(buttons) == 2
        for b in buttons:
            right = b.mapTo(view, b.rect().topRight()).x()
            assert b.isVisible() and 0 < right <= view.width()
        # A longer text (a translation, a long host) wraps instead of
        # widening the row.
        rows = [lbl for lbl in view.findChildren(QLabel)
                if lbl.text().startswith(("✔", "⚠"))]
        assert len(rows) == 4 and all(lbl.wordWrap() for lbl in rows)
    finally:
        view.hide()


# ------------------------------------------------------------ the flows

def _asked(s):
    """The (url, txid) asked by the verification of the last Check
    (``Session.check`` clears the world's records first)."""
    return list(s.world.verified)


def test_three_deliveries_asked_again_after_a_day(make):
    """The three-date will (39 copies, 13 per will-executor): the next Check
    does not ask them again within the day; a day later it asks all."""
    s = make()
    chain.three_dates(s)
    d = s.check()
    chain.audit(s, {1: 3, 2: 9, 3: 27})
    snap = d._report().snapshot()
    assert len(snap["verified"]) == 3
    assert all(nxt == last + REVERIFY_SECONDS_MULTI
               for last, nxt in snap["verified"].values())
    s.check()
    assert _asked(s) == []
    s.clock.travel(1.01)
    s.check()
    assert len(_asked(s)) == 39
    assert all(w.get_status("CHECKED") for w in s.bw.willitems.values()
               if w.get_status("VALID"))


def test_one_delivery_asked_again_at_every_check(make):
    s = make()
    chain._set_heirs(s, ["luca", "john"])
    s.check()
    for _n in range(2):
        snap = s.check()._report().snapshot()
        assert len(_asked(s)) == 3
        assert all(nxt is None for _last, nxt in snap["verified"].values())


def test_offline_a_verified_will_reads_no_answer_and_is_not_sent_again(make, monkeypatch):
    """Owner (2026-10-22): with the Wi-Fi off the Check said 5/5 verified.
    Now nothing is reached: every will-executor reads "no answer to the
    check", no copy is marked failed nor sent again; back online, the next
    Check verifies them all."""
    s = make()
    chain._set_heirs(s, ["luca", "john"])
    s.check()
    pushed = len(s.world.pushed)
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
        MagicMock(side_effect=ConnectionError("You are offline."))))
    s.world.check_transaction = lambda txid, url, **kw: REAL_CHECK(txid, url)
    snap = s.check()._report().snapshot()
    states = dict(snap["executors"])
    assert {st for st, _sec in states.values()} == {"no_check_answer"}
    valid = [w for w in s.bw.willitems.values() if w.get_status("VALID")]
    assert valid and all(w.get_status("PUSHED") and not w.get_status("CHECK_FAIL")
                         and not w.get_status("CHECKED") for w in valid)
    assert len(s.world.pushed) == pushed
    del s.world.check_transaction
    snap = s.check()._report().snapshot()
    assert {st for st, _sec in dict(snap["executors"]).values()} == {"verified"}
    assert len(s.world.pushed) == pushed


class _Catalogs:
    """The repo's catalogs, compiled as the ZIP has them."""

    def read_file(self, path):
        """The compiled catalog *path* (``.mo``) from its ``.po``."""
        from build_zip import compile_catalog
        root = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
        return compile_catalog(os.path.join(root, path).replace(".mo", ".po"))


def _longest_rows_view(width):
    """The Will-Executor box with its longest rows, shown at ``width``."""
    r = BuildReport()
    r.set_executor(WE0, "verified")
    r.set_executor(WE1, "no_check_answer", 15)
    r.set_executor(WE2, "excluded")
    r.set_executor("https://www.bitcoin-afterlife.example", "verified")
    r.set_verified({u: (NOW, NOW + REVERIFY_SECONDS_MULTI)
                    for u in (WE0, "https://www.bitcoin-afterlife.example")})
    view = BuildReportView()
    view.on_exclude = MagicMock()
    view.on_retry = MagicMock()
    view.render(r.snapshot())
    view.resize(width, 600)
    view.show()
    QApplication.processEvents()
    return view


def test_the_rows_are_whole_in_every_language():
    """Owner's ZIP test (2026-10-23): "verified on 23 Oct 2026, 13:52"
    wrapped with room to spare. In English, Italian and Spanish: at a
    normal width every row is whole on one line; at 600 px a row may wrap
    but "Exclude" and "Retry" stay inside and no row is wider than the
    window."""
    from bal import i18n
    try:
        for lang in (None, "it_IT", "es_ES"):
            i18n.set_language(_Catalogs(), lang)
            for width, one_line in ((1000, True), (600, False)):
                view = _longest_rows_view(width)
                try:
                    # The box rows: a symbol and ONE space (the step bar's
                    # words have two).
                    rows = [lbl for lbl in view.findChildren(QLabel)
                            if lbl.text()[:1] in ("✔", "⚠", "–")
                            and lbl.text()[1:3] != "  "]
                    assert len(rows) == 4, (lang, [r.text() for r in rows])
                    line = rows[0].fontMetrics().height()
                    for lbl in rows:
                        right = lbl.mapTo(view, lbl.rect().topRight()).x()
                        assert right <= view.width(), (lang, width, lbl.text())
                        if one_line:
                            assert lbl.height() < 2 * line, (lang, lbl.text())
                    buttons = [b for b in view.findChildren(QPushButton)
                               if b.isVisible()]
                    assert len(buttons) == 2, lang
                    for b in buttons:
                        right = b.mapTo(view, b.rect().topRight()).x()
                        assert 0 < right <= view.width(), (lang, width, b.text())
                finally:
                    view.hide()
    finally:
        i18n.set_language(None, None)
