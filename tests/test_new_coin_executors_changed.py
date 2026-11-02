"""A new coin with other will-executors: the new copies go first (#215).

The audit of 2026-10-31 found the old copies and the new ones valid on the same
date. An old will-executor that broadcasts first kills the new copies, and the
new coin reaches no heir.

``tests/flowkit.py`` wallets with three fake will-executors, each with its own
fee address, with and without a password, a fixed clock. Nothing reaches the
network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_new_coin_executors_changed.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk

from bal.core.util import Util
from bal.core.will import Will


@pytest.fixture(params=["flowkit-test", None], ids=["password", "no_password"])
def sent(request, monkeypatch):
    """A will signed and sent to three will-executors."""
    fk.ensure_event_loop()
    s = fk.Session(monkeypatch, mode="advanced", password=request.param)
    s.world.add_executor()
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
    s.prepare()
    s.menu_sign()
    s.menu_broadcast()
    yield s
    s.close()
    fk.stop_event_loop()


def _swap_executors(s, how_many=3):
    """Deselect ``how_many`` will-executors and add as many new ones."""
    for index, record in enumerate(list(s.world.executors.values())[:how_many]):
        record["selected"] = False
        url = "https://new{}.test".format(index)
        s.world.executors[url] = {
            "url": url, "selected": True, "base_fee": 1000,
            "address": fk.address(50 + index), "status": "200", "info": "fake"}


def _copies(s):
    """``{url: locktime}`` per will-executor, and the coins of each, VALID only."""
    dates, coins = {}, {}
    for w in s.bw.willitems.values():
        if w.get_status("VALID") and w.we:
            dates[w.we["url"]] = int(w.tx.locktime)
            coins[w.we["url"]] = {i.prevout.to_str() for i in w.tx.inputs()}
    return dates, coins


def _assert_new_copies_go_first(s, old_date):
    """Every copy with more coins is dated one day before those with fewer."""
    earlier = int(Util.anticipate_locktime(old_date, days=1))
    dates, coins = _copies(s)
    most = max(len(c) for c in coins.values())
    assert most == 2
    for url, date in dates.items():
        assert date == (earlier if len(coins[url]) == most else old_date), url
    assert Will.find_same_date_copies(s.bw.willitems) is None


def _date(s):
    """The one date of the will sent."""
    dates = set(_copies(s)[0].values())
    assert len(dates) == 1
    return dates.pop()


def test_all_executors_changed_and_a_new_coin(sent):
    """The audit's case: the new copies are one day earlier."""
    old_date = _date(sent)
    _swap_executors(sent)
    sent.add_coin(500_000)
    sent.check()
    _assert_new_copies_go_first(sent, old_date)


def test_some_executors_changed_and_a_new_coin(sent):
    """One will-executor changed: all the new copies move together."""
    old_date = _date(sent)
    _swap_executors(sent, how_many=1)
    sent.add_coin(500_000)
    sent.check()
    _assert_new_copies_go_first(sent, old_date)


def test_executors_changed_with_no_new_coin_keep_the_date(sent):
    """No new coin: a copy with the same coins is no competitor."""
    old_date = _date(sent)
    _swap_executors(sent)
    sent.check()
    assert set(_copies(sent)[0].values()) == {old_date}


def test_a_second_check_changes_nothing(sent):
    """#136: after the rebuild and the send, a Check asks nothing."""
    _swap_executors(sent)
    sent.add_coin(500_000)
    sent.check()
    sent.menu_sign()
    sent.menu_broadcast()
    before = set(sent.bw.willitems)
    asked = len(sent.user.asked)
    sent.check()
    assert set(sent.bw.willitems) == before and len(sent.user.asked) == asked
