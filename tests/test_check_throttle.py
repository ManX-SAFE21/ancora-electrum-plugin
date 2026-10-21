"""
Tests for #172: the Check asks the will-executors a few at a time.

Seen in the owner's test (2026-10-20, a will in 3 deliveries, 4
will-executors, 84 transactions): the Check sent every ``searchtx`` at once,
the servers answered 429 Too Many Requests, and the copies were marked
"failed" and sent again; only the 2nd or 3rd Check verified them all.

Covers, with three fake will-executors (never the real ones):
  - ``check_transactions_parallel``: never more than ``CHECK_PER_SERVER``
    (3) requests at a time to one will-executor, the will-executors in
    parallel; every copy answered; at the deadline, the requests running
    AND the ones not sent yet are reported as timed out; ``on_busy`` runs
    on the calling thread.
  - ``check_transaction``: a 429 gives a pause (2 s, 4 s, 8 s, or the
    server's Retry-After, at most 10 s) and a new try; still busy after 3
    new tries: ``NO_ANSWER``; another error is raised as before.
  - ``verify_will`` with the real parallel check: a busy will-executor
    leaves its copies sent (no CHECK_FAIL), the others are verified.
  - ``check_deadline``: 20 s for a small will, 35 s for 21 copies on one
    will-executor, never more than 120 s (owner: 2 minutes).
  - The windows: the Check's "Verification" step and the right click
    "check" show the copies checked and the seconds, why it is longer and
    who is busy, each on its own line; a small will shows the count only;
    the right click window wraps its lines.

No wallet, no network, no will-executor is contacted.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_check_throttle.py
"""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

from test_build_report import _dialog, _item

from bal.core import willexecutors as we_mod
from bal.core.willexecutors import Willexecutors
from bal.engine.verify import NO_ANSWER, VERIFIED, verify_will
from bal.gui.qt import window as window_mod
from bal.gui.qt.dialogs import check_wait_lines

WE0 = "https://we0.test"
WE1 = "https://we1.test"
WE2 = "https://we2.test"
EXECUTORS = (WE0, WE1, WE2)


class _BusyError(Exception):
    """Like aiohttp's ``ClientResponseError`` for a 429 (status, headers)."""

    def __init__(self, retry_after=None):
        super().__init__("429, message='Too Many Requests'")
        self.status = 429
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


def _targets(per_executor):
    """``per_executor`` copies on each of the three will-executors."""
    return [("{}-{}".format(url[8:11], i), url)
            for url in EXECUTORS for i in range(per_executor)]


# ------------------------------------------------- a few at a time per server

def test_never_more_than_three_at_a_time_per_will_executor(monkeypatch):
    lock = threading.Lock()
    now = {url: 0 for url in EXECUTORS}
    most = {url: 0 for url in EXECUTORS}
    most_all = [0]

    def slow_check(txid, url, **kw):
        with lock:
            now[url] += 1
            most[url] = max(most[url], now[url])
            most_all[0] = max(most_all[0], sum(now.values()))
        time.sleep(0.05)
        with lock:
            now[url] -= 1
        return {"tx": txid}

    monkeypatch.setattr(Willexecutors, "check_transaction",
                        staticmethod(slow_check))
    answered = []
    results = Willexecutors.check_transactions_parallel(
        _targets(9), on_each=lambda wid, url, res, exc: answered.append(wid),
        deadline=30,
    )
    assert len(results) == len(answered) == 27
    assert most == {url: 3 for url in EXECUTORS}
    # The will-executors were asked at the same time, not one after the other.
    assert most_all[0] > 3


def test_the_deadline_reports_the_running_and_the_not_sent(monkeypatch):
    release = threading.Event()

    def hanging_check(txid, url, **kw):
        if url == WE0:
            return {"tx": txid}
        release.wait(5)
        return {"tx": txid}

    monkeypatch.setattr(Willexecutors, "check_transaction",
                        staticmethod(hanging_check))
    timed_out = []
    start = time.time()
    try:
        results = Willexecutors.check_transactions_parallel(
            _targets(5), deadline=1.0, tick_interval=0.2,
            on_timeout=lambda wid, url: timed_out.append(wid),
        )
    finally:
        release.set()
    assert time.time() - start < 3
    assert sorted(results) == ["we0-{}".format(i) for i in range(5)]
    # 3 running + 2 never sent, on each of the two hanging will-executors.
    assert sorted(timed_out) == sorted(
        "{}-{}".format(n, i) for n in ("we1", "we2") for i in range(5))


def test_on_busy_runs_on_the_calling_thread(monkeypatch):
    def busy_once(txid, url, on_busy=None, **kw):
        if on_busy is not None:
            on_busy(url, 4)
        return {"tx": txid}

    monkeypatch.setattr(Willexecutors, "check_transaction",
                        staticmethod(busy_once))
    seen = []
    Willexecutors.check_transactions_parallel(
        _targets(1), deadline=10,
        on_busy=lambda url, s: seen.append((url, s, threading.current_thread())),
    )
    assert sorted(u for u, _s, _t in seen) == list(EXECUTORS)
    assert {t for _u, _s, t in seen} == {threading.current_thread()}
    assert {s for _u, s, _t in seen} == {4}


# ------------------------------------------------------- 429: pause, new try

def _send(answers, calls):
    """A ``send_request`` giving ``answers`` one after the other."""
    def fake(method, url, data=None, **kw):
        calls.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fake


def test_a_busy_will_executor_is_asked_again(monkeypatch):
    calls, pauses, busy = [], [], []
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
        _send([_BusyError(), _BusyError(), {"tx": "the tx"}], calls)))
    monkeypatch.setattr(we_mod.time, "sleep", pauses.append)
    res = Willexecutors.check_transaction(
        "abc", WE1, on_busy=lambda url, s: busy.append((url, s)))
    assert res == {"tx": "the tx"}
    assert calls == [WE1 + "/searchtx"] * 3
    assert pauses == [2, 4]
    assert busy == [(WE1, 2), (WE1, 4)]


def test_still_busy_is_no_answer_not_a_failure(monkeypatch):
    calls, pauses = [], []
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
        _send([_BusyError() for _ in range(10)], calls)))
    monkeypatch.setattr(we_mod.time, "sleep", pauses.append)
    res = Willexecutors.check_transaction("abc", WE2)
    assert res is Willexecutors.NO_ANSWER
    assert len(calls) == 1 + Willexecutors.CHECK_BUSY_RETRIES == 4
    assert pauses == [2, 4, 8]


def test_the_server_pause_is_kept_but_never_too_long():
    assert Willexecutors.busy_pause(_BusyError("3"), 0) == 3
    assert Willexecutors.busy_pause(_BusyError("60"), 0) == 10
    assert Willexecutors.busy_pause(_BusyError("Wed, 21 Oct 2026 07:28:00 GMT"), 1) == 4
    assert Willexecutors.busy_pause(_BusyError(), 2) == 8
    assert Willexecutors.busy_pause(_BusyError(), 5) == 10
    assert Willexecutors.busy_pause(ConnectionError("down"), 0) is None


def test_another_error_is_raised_as_before(monkeypatch):
    calls = []
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
        _send([ConnectionError("down")], calls)))
    try:
        Willexecutors.check_transaction("abc", WE0)
    except ConnectionError:
        pass
    else:
        raise AssertionError("the error was not raised")
    assert len(calls) == 1


def test_verify_a_busy_will_executor_keeps_its_copies_sent(monkeypatch):
    """The real parallel check: we2 always busy, we1 busy once."""
    will = {}
    for url in EXECUTORS:
        for i in range(4):
            will["{}-{}".format(url[8:11], i)] = _item("COMPLETE", "PUSHED", url=url)
    first_try = set()
    lock = threading.Lock()

    def send(method, url, data=None, **kw):
        wid = data.decode("ascii")
        with lock:
            again = wid in first_try
            first_try.add(wid)
        if url.startswith(WE2) or (url.startswith(WE1) and not again):
            raise _BusyError()
        return {"tx": str(will[wid].tx)}

    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(send))
    # No real pause in the test.
    monkeypatch.setattr(we_mod, "CHECK_BUSY_MAX_SLEEP", 0)
    targets = [(wid, w.we["url"]) for wid, w in will.items()]
    outcomes = verify_will(will, targets)
    for wid, w in will.items():
        if wid.startswith("we2"):
            assert outcomes[wid] == NO_ANSWER
            assert w.get_status("PUSHED") and not w.get_status("CHECK_FAIL")
        else:
            assert outcomes[wid] == VERIFIED
            assert w.get_status("CHECKED")


# ------------------------------------------------------------- the deadline

def test_the_deadline_grows_with_the_transactions():
    assert Willexecutors.check_deadline([]) == 20
    assert Willexecutors.check_deadline(_targets(3)) == 20
    # The owner's will: 84 transactions, 21 on each of 4 will-executors.
    owner = [("t{}-{}".format(u, i), "https://we{}.test".format(u))
             for u in range(4) for i in range(21)]
    assert Willexecutors.check_deadline(owner) == 35
    # Owner's question: 10 will-executors with 30 transactions each. By the
    # busiest one: 10 rounds of 3; but 300 requests 16 at a time: 19 rounds.
    many = [("t{}-{}".format(u, i), "https://we{}.test".format(u))
            for u in range(10) for i in range(30)]
    assert Willexecutors.check_deadline(many) == 95
    # 30 will-executors with one transaction each: 2 rounds, still 20 s.
    single = [("t{}".format(u), "https://we{}.test".format(u)) for u in range(30)]
    assert Willexecutors.check_deadline(single) == 20
    # Up to 5000 transactions later: never more than 2 minutes.
    assert Willexecutors.check_deadline(_targets(1700)) == 120


def test_verify_will_gives_the_grown_deadline_to_the_check():
    will = {wid: _item("COMPLETE", "PUSHED", url=url)
            for wid, url in _targets(21)}
    fake = MagicMock()
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        verify_will(will, [(wid, w.we["url"]) for wid, w in will.items()])
    assert fake.call_args.kwargs["deadline"] == 35


# ----------------------------------------------------------- the windows

def _busy_check(snapshots=None, dialog=None):
    """A parallel check: we1 busy (new try in 4 s), then every copy ours."""
    seen = {}

    def fake(items, *, on_each=None, on_timeout=None, on_tick=None,
             on_busy=None, deadline=None, **kw):
        seen["deadline"] = deadline
        on_busy(WE1, 4)
        on_tick()
        if snapshots is not None:
            snapshots.append(dialog._report().snapshot())
        for wid, url in items:
            on_each(wid, url, {"tx": str(dialog_items(wid).tx)}, None)

    def dialog_items(wid):
        return fake.items[wid]

    fake.seen = seen
    return fake


def test_the_check_window_explains_the_longer_wait():
    d = _dialog({wid: _item("COMPLETE", "PUSHED", url=url)
                 for wid, url in _targets(21)})
    live = []
    fake = _busy_check(live, d)
    fake.items = d.bal_window.willitems
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    assert fake.seen["deadline"] == 35
    steps = {s: (st, note) for s, st, note in live[0]["steps"]}
    assert steps["verify"] == ("run", "0/3 · 0 s / 35 s")
    kind, text = live[0]["result"]
    assert kind == "run"
    # Each on its own line: the count in bold, why in grey, busy in orange.
    assert text.endswith(
        "<br><b>Copies checked: 0 of 63 · 0 s (at most 35 s)</b>"
        '<br><span style="color:#666666;">Many transactions: the '
        "will-executors are asked a few at a time, so as not to overload "
        "them.</span>"
        '<br><span style="color:#b26a00;">we1.test is busy: new try in 4 '
        "s.</span>")
    assert all(w.get_status("CHECKED") for w in d.bal_window.willitems.values())


def test_a_small_will_shows_the_count_only():
    assert check_wait_lines(3, 8, 4.6, 20, {}, 100.0) == [
        ("count", "Copies checked: 3 of 8 · 4 s (at most 20 s)")]
    # A busy will-executor whose new try already started is not shown.
    assert check_wait_lines(30, 84, 12.2, 35, {WE1: 99.0}, 100.0) == [
        ("count", "Copies checked: 30 of 84 · 12 s (at most 35 s)"),
        ("note", "Many transactions: the will-executors are asked a few at "
                 "a time, so as not to overload them.")]
    assert check_wait_lines(0, 8, 0, 20, {WE2: 102.5}, 100.0)[1:] == [
        ("busy", "we2.test is busy: new try in 3 s.")]
    # The seconds never pass the limit shown.
    assert check_wait_lines(8, 8, 21.0, 20, {}, 100.0)[0][1].endswith(
        "20 s (at most 20 s)")


def test_the_right_click_check_explains_the_longer_wait(monkeypatch):
    will = {wid: _item("COMPLETE", "PUSHED", url=url)
            for wid, url in _targets(21)}
    host = MagicMock()
    host.willitems = will
    host.waiting_dialog._stopping = False
    monkeypatch.setattr(window_mod.time, "sleep", lambda s: None)
    fake = _busy_check()
    fake.items = will
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        window_mod.BalWindow.check_transactions_task(host, will)
    assert fake.seen["deadline"] == 35
    shown = [c.args[0] for c in host.waiting_dialog.update.call_args_list]
    assert shown[0] == (
        "Checking transactions\n"
        "Copies checked: 0 of 63 · 0 s (at most 35 s)\n"
        "Many transactions: the will-executors are asked a few at a time, "
        "so as not to overload them.")
    assert shown[-1].endswith("\nwe1.test is busy: new try in 4 s.")


def test_the_right_click_window_wraps_its_lines():
    will = {"s0": _item("COMPLETE", "PUSHED", url=WE0)}
    host = MagicMock()
    with patch.object(window_mod, "BalWaitingDialog") as wd:
        window_mod.BalWindow.check_transactions(host, will)
    label = wd.return_value.message_label
    label.setWordWrap.assert_called_once_with(True)
    label.setMinimumWidth.assert_called_once_with(480)
    wd.return_value.exe.assert_called_once_with()


# ------------------------------- the "already present" copies of the sending

def _already_present(urls):
    """A push where every will-executor answers "already present"."""
    def fake(selected, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        for url in urls:
            on_each(url, selected[url], False,
                    Willexecutors.AlreadyPresentException())
    return fake


def test_the_already_present_copies_no_longer_wait_one_by_one(monkeypatch):
    """Owner's test 2026-10-21: we.divinaly.support said "already present"
    and then answered no check: 31 copies, 16 s each, 8 minutes of a still
    window. Now they are asked 3 at a time, until the deadline."""
    from bal.engine.push import push_will

    will, selected = {}, {}
    for url, count in ((WE0, 4), (WE1, 31), (WE2, 4)):
        wids = ["{}-{}".format(url[8:11], i) for i in range(count)]
        for wid in wids:
            will[wid] = _item("COMPLETE", "PUSHED", url=url)
        selected[url] = {"url": url, "txsids": wids}
    release = threading.Event()
    lock = threading.Lock()
    now, most = {WE1: 0}, [0]

    def check(wid, url, **kw):
        if url != WE1:
            return {"tx": str(will[wid].tx)}
        with lock:
            now[WE1] += 1
            most[0] = max(most[0], now[WE1])
        release.wait(5)
        with lock:
            now[WE1] -= 1
        return None

    begun, done, ticks = [], [], []
    monkeypatch.setattr(Willexecutors, "check_transaction", staticmethod(check))
    monkeypatch.setattr(Willexecutors, "check_deadline",
                        staticmethod(lambda targets, **kw: 1))
    start = time.time()
    try:
        with patch.object(Willexecutors, "push_transactions_parallel",
                          side_effect=_already_present(list(selected))):
            result = push_will(
                will, selected,
                on_checks_begin=lambda n, d: begun.append((n, d)),
                on_check_done=lambda url, wid, a: done.append((url, a)),
                on_check_tick=lambda: ticks.append(1),
            )
    finally:
        release.set()
    assert time.time() - start < 3
    assert begun == [(39, 1)]
    assert most[0] == 3
    assert len(done) == 39 and ticks
    for wid, w in will.items():
        if wid.startswith("we1"):
            # #147: no answer in time, the copy stays sent.
            assert result.checks[wid] is Willexecutors.NO_ANSWER
            assert w.get_status("PUSHED") and not w.get_status("CHECK_FAIL")
        else:
            assert w.get_status("CHECKED")


def test_closing_the_window_stops_the_already_present_checks(monkeypatch):
    from bal.engine.push import push_will

    will = {"a{}".format(i): _item("COMPLETE", "PUSHED", url=WE1)
            for i in range(10)}
    selected = {WE1: {"url": WE1, "txsids": list(will)}}
    release = threading.Event()
    monkeypatch.setattr(Willexecutors, "check_transaction", staticmethod(
        lambda wid, url, **kw: release.wait(5) and None))
    ticks = []
    start = time.time()
    try:
        with patch.object(Willexecutors, "push_transactions_parallel",
                          side_effect=_already_present([WE1])):
            result = push_will(
                will, selected, on_check_tick=lambda: ticks.append(1),
                should_stop=lambda: len(ticks) >= 1)
    finally:
        release.set()
    assert time.time() - start < 3
    assert result.stopped is True
    # Nothing recorded for the copies not answered: they stay as they were.
    assert all(w.get_status("PUSHED") and not w.get_status("CHECK_FAIL")
               for w in will.values())


def test_the_sending_window_shows_the_already_present_checks():
    d = _dialog({"a": _item("COMPLETE", url=WE0), "b": _item("COMPLETE", url=WE1)})
    d.msg_set_pushing()
    seen = {}

    def fake_check(items, *, on_each=None, on_tick=None, on_busy=None,
                   deadline=None, **kw):
        items = list(items)
        wid, url = items[0]
        on_each(wid, url, {"tx": str(d.bal_window.willitems[wid].tx)}, None)
        on_busy(WE1, 4)
        on_tick()
        seen["snap"] = d._report().snapshot()
        seen["deadline"] = deadline

    records = {WE0: {"txsids": ["a"]}, WE1: {"txsids": ["b"]}}
    with patch.object(Willexecutors, "get_willexecutor_transactions",
                      return_value=records), \
         patch.object(Willexecutors, "is_selected", return_value=True), \
         patch.object(Willexecutors, "is_valid", return_value=True), \
         patch.object(Willexecutors, "push_transactions_parallel",
                      _already_present([WE0, WE1])), \
         patch.object(Willexecutors, "check_transactions_parallel", fake_check):
        d.loop_push()
    assert seen["deadline"] == 20
    steps = {s: (st, note) for s, st, note in seen["snap"]["steps"]}
    assert steps["send"] == ("run", "1/2 · 0 s / 20 s")
    assert seen["snap"]["result"] == (
        "run",
        "Checking the copies the will-executors already had…"
        "<br><b>Copies checked: 1 of 2 · 0 s (at most 20 s)</b>"
        '<br><span style="color:#b26a00;">we1.test is busy: new try in 4 '
        "s.</span>")


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
