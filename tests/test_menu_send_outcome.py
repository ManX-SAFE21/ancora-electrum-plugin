"""Tools > Send says what really happened (#213).

The audit of 2026-10-31 found "All transactions are broadcasted" with 0 copies
sent. An owner who reads it believes the heirs are protected while no
will-executor holds the will.

``tests/flowkit.py`` wallets with three fake will-executors, each with its own
fee address, the test password, a fixed clock. Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_menu_send_outcome.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk

from bal.engine import push as push_mod
from bal.gui.qt import window as window_mod

TEST_PASSWORD = "flowkit-test"
ALL_SENT = "All transactions are broadcasted to respective Will-Executors"
SOME_NOT = "Some transaction was not broadcasted"
UNSIGNED = "Nothing sent: the will is not signed yet."
NOTHING_LEFT = "Nothing to send: the will-executors have already received every copy."
NO_EXECUTOR = "Nothing sent: no selected will-executor can receive the will."
STOPPED = "Sending stopped: some copies may not have been sent. Press Check to verify."


@pytest.fixture
def make(monkeypatch):
    """A session with three will-executors."""
    fk.ensure_event_loop()
    made = []

    def _make():
        """The session (see the fixture)."""
        s = fk.Session(monkeypatch, mode="advanced", password=TEST_PASSWORD)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _signed(s):
    """A prepared and signed will, not sent."""
    s.prepare()
    s.menu_sign()
    s.bw.popups.clear()
    return s


def _send(s):
    """Tools > Send; returns its popups, and how many copies left."""
    s.bw.popups.clear()
    before = len(s.world.pushed)
    s.menu_broadcast()
    return list(s.bw.popups), len(s.world.pushed) - before


def test_a_real_send_says_all_sent(make):
    """Signed copies reach all three: the success message, as before."""
    s = _signed(make())
    popups, sent = _send(s)
    assert sent > 0
    assert popups == [["message", ALL_SENT]]
    assert len({url for url, _w, _r in s.world.pushed}) == 3


def test_an_unsigned_will_is_not_sent(make):
    """Prepared only: nothing leaves, and it says why."""
    s = make()
    s.prepare()
    popups, sent = _send(s)
    assert sent == 0
    assert popups == [["warning", UNSIGNED]]


def test_a_will_already_sent_says_nothing_to_send(make):
    """The second Send has nothing left."""
    s = _signed(make())
    _send(s)
    popups, sent = _send(s)
    assert sent == 0
    assert popups == [["warning", NOTHING_LEFT]]


def test_no_selected_executor_sends_nothing(make):
    """All three deselected after the signature."""
    s = _signed(make())
    for record in s.world.executors.values():
        record["selected"] = False
    popups, sent = _send(s)
    assert sent == 0
    assert popups == [["warning", NO_EXECUTOR]]


def test_already_present_with_our_copy_is_a_success(make):
    """"Already present" and the check finds exactly our transaction."""
    s = _signed(make())
    s.world.already = set(s.world.executors)
    popups, _sent = _send(s)
    assert popups == [["message", ALL_SENT]]


def test_already_present_with_another_copy_is_not_a_success(make, monkeypatch):
    """"Already present", but the will-executor holds something else."""
    s = _signed(make())
    s.world.already = set(s.world.executors)
    monkeypatch.setattr(s.world, "check_transaction",
                        lambda txid, url, **kw: {"tx": "something else"})
    popups, _sent = _send(s)
    assert popups == [["warning", SOME_NOT]]


def test_already_present_with_no_usable_answer_is_not_a_success(make, monkeypatch):
    """The check fails with an error: stored as not verified, so a warning."""
    s = _signed(make())
    s.world.already = set(s.world.executors)

    def broken(txid, url, **kw):
        """A check that breaks."""
        raise ValueError("broken answer")

    monkeypatch.setattr(s.world, "check_transaction", broken)
    popups, _sent = _send(s)
    assert popups == [["warning", SOME_NOT]]


def test_a_check_with_no_answer_in_time_stays_to_be_verified(make):
    """The will-executor said it holds the copy, then no answer: no warning."""
    s = _signed(make())
    s.world.already = set(s.world.executors)
    real = s.world.check_transactions_parallel

    def late(items, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        """Nobody answers the check in time."""
        for wid, url in items:
            on_timeout(wid, url)

    s.world.check_transactions_parallel = late
    try:
        popups, _sent = _send(s)
    finally:
        s.world.check_transactions_parallel = real
    assert popups == [["message", ALL_SENT]]
    assert all(w.get_status("PUSHED") for w in s.bw.willitems.values()
               if w.get_status("VALID"))


def test_an_executor_down_is_still_a_warning(make):
    """Unchanged: one of three does not answer."""
    s = _signed(make())
    s.world.down = {next(iter(s.world.executors))}
    popups, _sent = _send(s)
    assert popups == [["warning", SOME_NOT]]


def test_a_second_check_after_the_send_stays_clean(make):
    """#136: the messages change no state; the Check after them asks nothing."""
    s = _signed(make())
    _send(s)
    before = set(s.bw.willitems)
    s.check()
    assert set(s.bw.willitems) == before and not s.user.asked


def test_partly_signed_will_is_not_all_sent(make):
    """One copy is left unsigned: the others leave, and it is not "all"."""
    s = _signed(make())
    wid, item = next((k, w) for k, w in s.bw.willitems.items() if w.get_status("VALID"))
    item.set_status("COMPLETE", False)
    popups, sent = _send(s)
    assert sent > 0
    assert popups == [["warning", SOME_NOT]]
    assert wid not in {w for _u, w, _r in s.world.pushed}


def test_a_window_closed_before_sending_sends_nothing(make):
    """The window is closed already: the task stops and says so."""
    s = _signed(make())

    class Closed:
        """A waiting window already closed."""

        _stopping = True

        def update(self, text):
            """Nothing to show."""

    s.bw.waiting_dialog = Closed()
    before = len(s.world.pushed)
    assert s.bw.push_transactions_to_willexecutors() == window_mod.PUSH_STOPPED
    assert len(s.world.pushed) == before


def test_a_window_closed_during_the_checks_is_not_a_success(make, monkeypatch):
    """Closing the waiting window never reads as a success."""
    s = _signed(make())
    stopped = push_mod.PushResult(stopped=True)
    monkeypatch.setattr(window_mod, "push_will", lambda *a, **kw: stopped)
    popups, _sent = _send(s)
    assert popups == [["warning", STOPPED]]


def test_the_reasons_without_a_window():
    """The rule itself, on bare items."""

    class Item:
        """A copy with these statuses."""

        def __init__(self, *flags):
            self.flags = flags
            self.we = {"url": "https://we0.test"}

        def get_status(self, name):
            """The flag."""
            return name in self.flags

    reason = push_mod.nothing_sent_reason
    assert reason({}) == push_mod.NOTHING_NO_COPIES
    assert reason({"a": Item("COMPLETE")}) == push_mod.NOTHING_NO_COPIES
    assert reason({"a": Item("VALID")}) == push_mod.NOTHING_UNSIGNED
    assert reason({"a": Item("VALID", "COMPLETE", "PUSHED")}) == push_mod.NOTHING_ALL_SENT
    assert reason({"a": Item("VALID", "COMPLETE")}) == push_mod.NOTHING_NO_EXECUTOR
    forced = reason({"a": Item("VALID", "COMPLETE", "PUSHED")}, force=True)
    assert forced == push_mod.NOTHING_NO_EXECUTOR
